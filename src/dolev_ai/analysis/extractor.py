"""Async worker pool that pulls tweets off a queue and runs LLM extraction.

- Backpressure: bounded queue, drop-oldest on overflow, periodic backlog events.
- Batches up to `batch_size` tweets per LLM call.
- Publishes `post.extracting` / `post.extracted` / `extraction.backlog` events.
- Persists extractions to DB so the rebuilt aggregator can read them later.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from dolev_ai.db import save_extraction
from dolev_ai.events import EventBus
from dolev_ai.llm import Extraction, LLMProvider
from dolev_ai.models import RawTweet

logger = logging.getLogger(__name__)


class ExtractionWorker:
    def __init__(
        self,
        provider: LLMProvider,
        event_bus: EventBus,
        session_factory,
        max_queue: int = 1000,
        workers: int = 2,
        batch_size: int = 8,
        max_wait_s: float = 2.0,
        backlog_interval_s: float = 5.0,
    ) -> None:
        self._provider = provider
        self._event_bus = event_bus
        self._session_factory = session_factory
        self._max_queue = max_queue
        self._workers = workers
        self._batch_size = batch_size
        self._max_wait_s = max_wait_s
        self._backlog_interval_s = backlog_interval_s

        self._queue: asyncio.Queue[RawTweet] = asyncio.Queue(maxsize=max_queue)
        self._tasks: list[asyncio.Task] = []
        self._stopped = asyncio.Event()
        self._dropped_total = 0
        # LLM call telemetry
        self._calls_total = 0
        self._calls_finance = 0
        self._calls_errors = 0
        self._latency_sum_ms = 0
        self._last_call_at: float | None = None

    def call_stats(self) -> dict:
        avg = (self._latency_sum_ms / self._calls_total) if self._calls_total else 0
        return {
            "calls_total": self._calls_total,
            "calls_finance": self._calls_finance,
            "calls_errors": self._calls_errors,
            "avg_latency_ms": int(avg),
            "last_call_at": self._last_call_at,
            "dropped_total": self._dropped_total,
        }

    def backlog(self) -> int:
        return self._queue.qsize()

    def capacity(self) -> int:
        return self._max_queue

    async def submit(self, tweet: RawTweet) -> None:
        """Enqueue a tweet for extraction. Drops oldest on overflow."""
        try:
            self._queue.put_nowait(tweet)
        except asyncio.QueueFull:
            try:
                _ = self._queue.get_nowait()
                self._dropped_total += 1
            except asyncio.QueueEmpty:
                pass
            try:
                self._queue.put_nowait(tweet)
            except asyncio.QueueFull:
                self._dropped_total += 1

    async def start(self) -> None:
        for _ in range(self._workers):
            self._tasks.append(asyncio.create_task(self._worker_loop()))
        self._tasks.append(asyncio.create_task(self._backlog_loop()))
        logger.info(
            f"Extractor started: workers={self._workers} batch={self._batch_size} "
            f"queue_cap={self._max_queue}"
        )

    async def stop(self) -> None:
        self._stopped.set()
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()

    async def _collect_batch(self) -> list[RawTweet]:
        """Block on first item, then drain up to batch_size or max_wait_s."""
        first = await self._queue.get()
        batch = [first]
        deadline = asyncio.get_event_loop().time() + self._max_wait_s
        while len(batch) < self._batch_size:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=remaining)
                batch.append(item)
            except asyncio.TimeoutError:
                break
        return batch

    async def _worker_loop(self) -> None:
        while not self._stopped.is_set():
            try:
                batch = await self._collect_batch()
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.warning(f"extractor batch collect failed: {e}")
                continue

            # Notify UI that extraction is in progress for these posts
            for tw in batch:
                try:
                    await self._event_bus.publish({
                        "type": "post.extracting",
                        "tweet_id": tw.id,
                        "author": tw.author,
                    })
                except Exception:
                    pass

            try:
                extractions = await self._provider.extract_batch(batch)
            except Exception as e:
                logger.error(f"extractor LLM call failed: {e}", exc_info=True)
                self._calls_errors += len(batch)
                continue

            # Telemetry + per-call WS events
            import time as _time
            now_ts = _time.time()
            self._last_call_at = now_ts
            for ex in extractions:
                self._calls_total += 1
                if ex.is_finance:
                    self._calls_finance += 1
                self._latency_sum_ms += ex.latency_ms
                if ex.latency_ms == 0 and not ex.is_finance and not ex.tickers and not ex.themes:
                    self._calls_errors += 1
                try:
                    await self._event_bus.publish({
                        "type": "llm.call",
                        "tweet_id": ex.post_id,
                        "model": ex.model,
                        "latency_ms": ex.latency_ms,
                        "is_finance": ex.is_finance,
                        "tickers_n": len(ex.tickers),
                        "themes_n": len(ex.themes),
                        "ts": now_ts,
                    })
                except Exception:
                    pass

            # Persist + publish
            try:
                with self._session_factory() as session:
                    for ex in extractions:
                        try:
                            save_extraction(session, ex)
                        except Exception as e:
                            logger.warning(f"save_extraction failed for {ex.post_id}: {e}")
            except Exception as e:
                logger.warning(f"extractor DB session failed: {e}")

            tweet_by_id = {tw.id: tw for tw in batch}
            for ex in extractions:
                tw = tweet_by_id.get(ex.post_id)
                try:
                    await self._event_bus.publish({
                        "type": "post.extracted",
                        "tweet_id": ex.post_id,
                        "author": tw.author if tw else "",
                        "text": tw.text if tw else "",
                        "url": tw.url if tw else "",
                        "is_finance": ex.is_finance,
                        "overall_sentiment": ex.overall_sentiment,
                        "summary": ex.summary,
                        "tickers": [
                            {"ticker": t.ticker, "sentiment": t.sentiment,
                             "confidence": t.confidence, "explicit": t.explicit}
                            for t in ex.tickers
                        ],
                        "themes": [
                            {"theme": t.theme, "sentiment": t.sentiment,
                             "confidence": t.confidence}
                            for t in ex.themes
                        ],
                        "model": ex.model,
                        "latency_ms": ex.latency_ms,
                        "created_at": datetime.utcnow().isoformat(),
                    })
                except Exception:
                    pass

    async def _backlog_loop(self) -> None:
        while not self._stopped.is_set():
            try:
                await asyncio.sleep(self._backlog_interval_s)
                await self._event_bus.publish({
                    "type": "extraction.backlog",
                    "depth": self.backlog(),
                    "capacity": self.capacity(),
                    "dropped_total": self._dropped_total,
                })
            except asyncio.CancelledError:
                return
            except Exception:
                continue
