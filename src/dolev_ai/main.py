"""Dolev AI daemon — collects tweets, fires trade-signal alerts, serves monitoring UI."""
from __future__ import annotations

import argparse
import asyncio
import logging
import pathlib
import signal
import sys
from datetime import datetime, timedelta

import uvicorn
import yaml
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

load_dotenv()

from dolev_ai.alert.telegram import TelegramAlerter
from dolev_ai.analysis.aggregator import GraphEdge, aggregate
from dolev_ai.analysis.sentiment import score_batch
from dolev_ai.analysis.ticker import extract_tickers
from dolev_ai.db import (
    init_db,
    last_alert_time,
    load_tweets_since,
    log_alert,
    save_signal,
    save_ticker_score,
    save_tweets,
)
from dolev_ai.events import EventBus
from dolev_ai.models import Signal
from dolev_ai.sources.playwright_source import PlaywrightSource
from dolev_ai.strategies.trust_graph import TrustGraphStrategy
from dolev_ai.synth.synthesizer import Synthesizer
from dolev_ai.web.server import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

CONFIG_PATH = pathlib.Path(__file__).parent.parent.parent / "config" / "settings.yaml"
SEEDS_PATH = pathlib.Path(__file__).parent.parent.parent / "config" / "seeds.yaml"
PROFILE_DIR = pathlib.Path(__file__).parent.parent.parent / "browser_profile"


def _load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def _load_seed_handles() -> list[str]:
    with open(SEEDS_PATH) as f:
        data = yaml.safe_load(f)
    return [a["handle"] for a in data.get("accounts", [])]


class Agent:
    def __init__(self, cfg: dict, dry_run: bool) -> None:
        self._cfg = cfg
        self._dry_run = dry_run
        self._session_factory = init_db()
        self._event_bus = EventBus()
        self._source = PlaywrightSource(
            profile_dir=PROFILE_DIR,
            headless=cfg.get("playwright", {}).get("headless", False),
            delay_min=cfg.get("playwright", {}).get("request_delay_min_s", 3.0),
            delay_max=cfg.get("playwright", {}).get("request_delay_max_s", 6.0),
            scroll_count=cfg.get("playwright", {}).get("scroll_count", 3),
        )
        self._handles = _load_seed_handles()
        self._alerter = TelegramAlerter(dry_run=dry_run)
        self._threshold = cfg.get("trust_graph", {}).get("score_threshold", 5.0)
        self._synthesizer = Synthesizer(
            model=cfg.get("synth", {}).get("model", "claude-sonnet-4-6"),
            cache_system_prompt=cfg.get("synth", {}).get("cache_system_prompt", True),
        )
        tg_cfg = cfg.get("trust_graph", {})
        with self._session_factory() as session:
            self._strategy = TrustGraphStrategy(
                score_threshold=self._threshold,
                min_credible_voices=tg_cfg.get("min_credible_voices", 3),
                cooldown_hours=tg_cfg.get("cooldown_hours", 4.0),
                cooldown_override_multiplier=tg_cfg.get("cooldown_override_multiplier", 2.0),
                last_alert_time_fn=lambda t: last_alert_time(session, t),
            )
        # Track last threshold_progress per ticker to detect near-threshold crossings
        self._last_threshold_progress: dict[str, float] = {}

    async def collect(self) -> None:
        """Fetch recent tweets and persist. Publishes tweet.ingested events."""
        since = datetime.utcnow() - timedelta(
            minutes=self._cfg.get("poll_cadence_minutes", 5) * 2
        )
        logger.info(f"Collecting from {len(self._handles)} accounts since {since:%H:%M}…")
        try:
            tweets = await self._source.fetch_feed_engagements(self._handles, since)
            with self._session_factory() as session:
                added = save_tweets(session, tweets)

            # Publish tweet.ingested for each new tweet so the UI can show the live feed
            if added > 0:
                # We don't know which were "new" exactly, but publish the most-recent batch
                # (the UI deduplicates by tweet id)
                new_tweets = tweets[:added] if len(tweets) >= added else tweets
                texts = [t.text for t in new_tweets]
                if texts:
                    sentiments = score_batch(texts)
                    for tweet, (label, confidence) in zip(new_tweets, sentiments):
                        tickers = extract_tickers(tweet.text)
                        await self._event_bus.publish({
                            "type": "tweet.ingested",
                            "id": tweet.id,
                            "author": tweet.author,
                            "text": tweet.text,
                            "created_at": tweet.created_at.isoformat(),
                            "url": tweet.url,
                            "tickers": tickers,
                            "sentiment": label,
                            "sentiment_score": round(confidence, 3),
                        })

            logger.info(f"  Saved {added} new tweets (fetched {len(tweets)})")
        except Exception as e:
            logger.error(f"Collect failed: {e}", exc_info=True)

    async def evaluate(self) -> None:
        """Score tickers, apply strategy, synthesize and send alerts. Publishes score/signal events."""
        window = self._cfg.get("score_window_minutes", 60)
        since = datetime.utcnow() - timedelta(minutes=window)

        with self._session_factory() as session:
            tweets = load_tweets_since(session, since)

        if not tweets:
            logger.info("Evaluate: no tweets in window")
            return

        ticker_scores, graph_edges = aggregate(tweets, window_minutes=window, threshold=self._threshold)
        logger.info(f"Evaluate: {len(ticker_scores)} tickers scored from {len(tweets)} tweets")

        # Publish score updates for all tickers
        seen_edges: set[tuple[str, str]] = set()
        with self._session_factory() as session:
            for ts in ticker_scores.values():
                save_ticker_score(session, ts)
                await self._event_bus.publish({
                    "type": "ticker.score_updated",
                    "ticker": ts.ticker,
                    "score": ts.score,
                    "voices": ts.unique_credible_voices,
                    "tweet_count": ts.tweet_count,
                    "threshold": self._threshold,
                    "threshold_progress": ts.threshold_progress,
                })
                # Emit near_threshold when crossing 75% from below
                prev = self._last_threshold_progress.get(ts.ticker, 0.0)
                if ts.threshold_progress >= 0.75 > prev:
                    await self._event_bus.publish({
                        "type": "ticker.near_threshold",
                        "ticker": ts.ticker,
                        "score": ts.score,
                        "threshold": self._threshold,
                        "percent": round(ts.threshold_progress * 100, 1),
                    })
                self._last_threshold_progress[ts.ticker] = ts.threshold_progress

        # Publish new trust-graph edges
        for edge in graph_edges:
            key = (edge.author, edge.ticker)
            if key not in seen_edges:
                seen_edges.add(key)
                await self._event_bus.publish({
                    "type": "graph.edge_added",
                    "author": edge.author,
                    "ticker": edge.ticker,
                    "weight": edge.weight,
                    "sentiment": edge.sentiment,
                })

        signals = self._strategy.evaluate(ticker_scores)
        if not signals:
            logger.info("No signals above threshold")
            return

        logger.info(f"{len(signals)} signal(s) to synthesize and send")
        for sig in signals:
            ts = ticker_scores[sig.ticker]
            tweet_texts = [t.text for t in tweets if sig.ticker in t.text.upper()][:10]

            # Notify UI that we're about to call Claude
            await self._event_bus.publish({"type": "signal.synthesizing", "ticker": sig.ticker})

            enriched: Signal = self._synthesizer.synthesize(sig.ticker, ts, tweet_texts)
            with self._session_factory() as session:
                save_signal(session, enriched)
                ok = self._alerter.send(enriched)
                if ok:
                    log_alert(session, enriched)
                    logger.info(f"Alert sent: {enriched.side.upper()} ${enriched.ticker} "
                                f"(conviction={enriched.conviction:.2f})")

            await self._event_bus.publish({
                "type": "signal.fired",
                "ticker": enriched.ticker,
                "side": enriched.side,
                "conviction": enriched.conviction,
                "suggested_size_pct": enriched.suggested_size_pct,
                "rationale": enriched.rationale,
                "key_drivers": enriched.key_drivers,
                "generated_at": enriched.generated_at.isoformat(),
            })

    async def run(self) -> None:
        started_at = datetime.utcnow()
        await self._source.start()

        # Start FastAPI + uvicorn in the same event loop
        app = create_app(
            event_bus=self._event_bus,
            session_factory=self._session_factory,
            threshold=self._threshold,
            started_at=started_at,
        )
        web_config = uvicorn.Config(
            app, host="127.0.0.1", port=8000, log_level="warning", loop="none"
        )
        web_server = uvicorn.Server(web_config)
        asyncio.create_task(web_server.serve())
        logger.info("Monitoring dashboard: http://localhost:8000")

        scheduler = AsyncIOScheduler()
        scheduler.add_job(
            self.collect, "interval",
            minutes=self._cfg.get("poll_cadence_minutes", 5),
            next_run_time=datetime.utcnow(),
        )
        scheduler.add_job(
            self.evaluate, "interval",
            minutes=self._cfg.get("eval_cadence_minutes", 2),
            next_run_time=datetime.utcnow() + timedelta(seconds=30),
        )
        scheduler.start()
        logger.info("Dolev AI agent started. Press Ctrl+C to stop.")

        stop_event = asyncio.Event()

        def _shutdown(*_):
            stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                asyncio.get_event_loop().add_signal_handler(sig, _shutdown)
            except (NotImplementedError, RuntimeError):
                pass  # Windows doesn't support add_signal_handler for all signals

        try:
            await stop_event.wait()
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            web_server.should_exit = True
            scheduler.shutdown(wait=False)
            await self._source.stop()
            logger.info("Agent stopped.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Dolev AI stock trend agent")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print alerts to stdout instead of sending to Telegram")
    args = parser.parse_args()

    cfg = _load_config()
    agent = Agent(cfg, dry_run=args.dry_run)
    asyncio.run(agent.run())


if __name__ == "__main__":
    main()
