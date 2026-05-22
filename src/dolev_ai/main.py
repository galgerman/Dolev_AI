"""Dolev AI daemon — collects tweets, extracts via LLM, fires trade-signal alerts, serves UI."""
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
from dolev_ai.alert.telegram_bot import TelegramBot
import dolev_ai.paper_trade as paper_trade
from dolev_ai.analysis.aggregator import ExtractionRecord, aggregate
from dolev_ai.analysis.extractor import ExtractionWorker
from dolev_ai.db import (
    PaperPositionRow,
    SignalApprovalRow,
    init_db,
    last_alert_time,
    load_extractions_since,
    log_alert,
    pending_approvals_older_than,
    prune_old_data,
    save_graph_edge,
    save_pending_approval,
    save_signal,
    save_theme_score,
    save_ticker_score,
    save_tweets,
    set_approval_message_id,
    update_approval_status,
)
from dolev_ai.eod import build_eod_summary
from dolev_ai.events import EventBus
from dolev_ai.live_events import (
    build_graph_edge_events,
    build_ticker_discovered_events,
    build_tweet_ingested_events,
)
from dolev_ai.llm.factory import build_provider, load_active_config
from dolev_ai.models import RawTweet, Signal
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
PROFILE_DIR = pathlib.Path(__file__).parent.parent.parent / "browser_profile_chrome"


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
        self._bot = TelegramBot(dry_run=dry_run, on_callback=self._on_tg_callback)
        self._threshold = cfg.get("trust_graph", {}).get("score_threshold", 5.0)
        pt_cfg = cfg.get("paper_trading", {})
        self._approval_timeout_minutes = pt_cfg.get("approval_timeout_minutes", 15)
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
        self._last_theme_progress: dict[str, float] = {}
        self._scheduler: AsyncIOScheduler | None = None
        self._worker_started_at: datetime | None = None
        self._worker_lock = asyncio.Lock()

        # LLM extraction pipeline
        self._llm_provider = None
        self._extractor: ExtractionWorker | None = None
        self._active_model_cfg: dict | None = None

    @property
    def event_bus(self) -> EventBus:
        return self._event_bus

    @property
    def session_factory(self):
        return self._session_factory

    @property
    def threshold(self) -> float:
        return self._threshold

    def status(self) -> dict:
        return {
            "running": self._scheduler is not None,
            "started_at": self._worker_started_at.isoformat() if self._worker_started_at else None,
            "mode": "dry-run" if self._dry_run else "live",
        }

    async def _on_tg_callback(self, approval_id: int, decision: str) -> None:
        """Called by TelegramBot when a user taps an inline button."""
        with self._session_factory() as session:
            ack = await paper_trade.handle_approval_callback(
                approval_id, decision, session, self._event_bus, self._synthesizer
            )
            approval = session.get(SignalApprovalRow, approval_id)
            if approval and approval.telegram_message_id:
                await self._bot.edit_message(approval.telegram_message_id, ack)
            logger.info(f"Approval {approval_id} → {decision}: {ack}")

    async def expire_approvals(self) -> None:
        """Mark pending approvals older than timeout as expired."""
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(minutes=self._approval_timeout_minutes)
        with self._session_factory() as session:
            stale = pending_approvals_older_than(session, cutoff)
            for approval in stale:
                ack = await paper_trade.handle_approval_callback(
                    approval.id, "expired", session, self._event_bus, self._synthesizer
                )
                if approval.telegram_message_id:
                    await self._bot.edit_message(approval.telegram_message_id, ack)
                logger.info(f"Expired approval {approval.id} for signal {approval.signal_id}")

    async def send_eod_summary(self) -> None:
        """Send end-of-day summary via Telegram."""
        try:
            with self._session_factory() as session:
                summary = build_eod_summary(session, datetime.utcnow())
            await self._bot.send_text(summary)
            logger.info("EOD summary sent.")
        except Exception as e:
            logger.error(f"EOD summary failed: {e}", exc_info=True)

    def llm_status(self) -> dict:
        cfg = self._active_model_cfg or {}
        base = {
            "provider": cfg.get("provider", "unknown"),
            "model": cfg.get("name", "unknown"),
            "endpoint": cfg.get("endpoint", ""),
            "running": self._extractor is not None,
            "backlog": self._extractor.backlog() if self._extractor else 0,
            "capacity": self._extractor.capacity() if self._extractor else 0,
        }
        if self._extractor:
            base.update(self._extractor.call_stats())
        return base

    async def _ensure_extractor(self) -> None:
        if self._extractor is not None:
            return
        try:
            cfg = load_active_config()
            self._active_model_cfg = cfg
            self._llm_provider = build_provider(cfg)
            healthy = await self._llm_provider.healthcheck()
            if not healthy:
                logger.warning(
                    f"LLM healthcheck failed for {cfg.get('name')} @ {cfg.get('endpoint')}. "
                    "Extractions will return empty until the endpoint is reachable."
                )
            self._extractor = ExtractionWorker(
                provider=self._llm_provider,
                event_bus=self._event_bus,
                session_factory=self._session_factory,
                max_queue=cfg.get("max_queue", 1000),
                workers=cfg.get("max_concurrent", 2),
                batch_size=cfg.get("batch_size", 8),
            )
            await self._extractor.start()
        except Exception as e:
            logger.error(f"Could not start LLM extractor: {e}", exc_info=True)

    async def start_worker(self) -> bool:
        async with self._worker_lock:
            if self._scheduler is not None:
                return False

            await self._source.start()
            await self._bot.start()
            await self._ensure_extractor()

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
            ret = self._cfg.get("retention", {}) or {}
            scheduler.add_job(
                self.prune, "interval",
                minutes=ret.get("prune_interval_minutes", 60),
                next_run_time=datetime.utcnow() + timedelta(minutes=5),
            )
            # Approval expiry — every minute
            scheduler.add_job(self.expire_approvals, "interval", minutes=1)
            # EOD summary — weekdays at 21:00 UTC (16:00 ET)
            eod_cfg = self._cfg.get("eod_summary", {}) or {}
            if eod_cfg.get("enabled", True):
                scheduler.add_job(
                    self.send_eod_summary, "cron",
                    day_of_week=eod_cfg.get("days", "mon-fri"),
                    hour=eod_cfg.get("cron_hour_utc", 21),
                    minute=eod_cfg.get("cron_minute", 0),
                )
            scheduler.start()
            self._scheduler = scheduler
            self._worker_started_at = datetime.utcnow()
            logger.info("Dolev AI scraper agent started.")
            return True

    async def stop_worker(self) -> bool:
        async with self._worker_lock:
            if self._scheduler is None:
                return False

            scheduler = self._scheduler
            self._scheduler = None
            self._worker_started_at = None
            scheduler.shutdown(wait=False)
            await self._bot.stop()
            await self._source.stop()
            if self._extractor is not None:
                await self._extractor.stop()
                self._extractor = None
            if self._llm_provider is not None:
                try:
                    await self._llm_provider.aclose()
                except Exception:
                    pass
                self._llm_provider = None
            await self._event_bus.publish({
                "type": "collection.finished",
                "total": 0,
                "completed": 0,
                "current_handle": None,
                "tweets_found": 0,
                "tickers_found": 0,
            })
            logger.info("Dolev AI scraper agent stopped.")
            return True

    async def prune(self) -> None:
        """Hourly DB pruner — trims time-series tables and old tweets/extractions."""
        ret = self._cfg.get("retention", {}) or {}
        try:
            with self._session_factory() as session:
                counts = prune_old_data(
                    session,
                    tweets_keep_days=ret.get("tweets_keep_days", 30),
                    scores_keep_hours=ret.get("scores_keep_hours", 24),
                    edges_keep_hours=ret.get("edges_keep_hours", 24),
                )
            total = sum(counts.values())
            if total > 0:
                logger.info(f"Prune: deleted {total} rows {counts}")
                await self._event_bus.publish({"type": "db.pruned", **counts, "total": total})
        except Exception as e:
            logger.error(f"Prune failed: {e}", exc_info=True)

    async def collect(self) -> None:
        """Fetch recent tweets, persist, and submit to LLM extractor."""
        since = datetime.utcnow() - timedelta(
            minutes=self._cfg.get("poll_cadence_minutes", 5) * 2
        )
        max_accounts = self._cfg.get("playwright", {}).get("max_accounts_per_poll", len(self._handles))
        handles = self._handles[:max_accounts]
        logger.info(f"Collecting from {len(handles)} accounts since {since:%H:%M}…")
        try:
            tweets: list[RawTweet] = []
            discovered_tickers: set[str] = set()
            await self._event_bus.publish({
                "type": "collection.started",
                "total": len(handles),
                "completed": 0,
                "current_handle": None,
                "tweets_found": 0,
                "tickers_found": 0,
            })
            for index, handle in enumerate(handles, start=1):
                await self._event_bus.publish({
                    "type": "collection.account_started",
                    "handle": handle,
                    "index": index,
                    "total": len(handles),
                    "completed": index - 1,
                    "tweets_found": len(tweets),
                    "tickers_found": len(discovered_tickers),
                })

                account_tweets = await self._source.fetch_feed_engagements([handle], since)
                tweets.extend(account_tweets)

                # Provisional UI events from regex (fast feedback before LLM runs)
                live_events = (
                    build_tweet_ingested_events(account_tweets)
                    + build_graph_edge_events(account_tweets)
                    + build_ticker_discovered_events(account_tweets)
                )
                for event in live_events:
                    if event["type"] == "ticker.discovered":
                        discovered_tickers.add(event["ticker"])
                    await self._event_bus.publish(event)

                await self._event_bus.publish({
                    "type": "collection.account_completed",
                    "handle": handle,
                    "index": index,
                    "total": len(handles),
                    "completed": index,
                    "account_tweets": len(account_tweets),
                    "tweets_found": len(tweets),
                    "tickers_found": len(discovered_tickers),
                })

            with self._session_factory() as session:
                added = save_tweets(session, tweets)

            # Submit every saved tweet to the LLM extractor
            if self._extractor is not None:
                for tw in tweets:
                    await self._extractor.submit(tw)

            logger.info(f"  Saved {added} new tweets (fetched {len(tweets)})")
            await self._event_bus.publish({
                "type": "collection.finished",
                "total": len(self._handles),
                "completed": len(self._handles),
                "current_handle": None,
                "tweets_found": len(tweets),
                "tickers_found": len(discovered_tickers),
            })
        except Exception as e:
            logger.error(f"Collect failed: {e}", exc_info=True)
            await self._event_bus.publish({
                "type": "collection.failed",
                "message": str(e),
            })

    def _load_records(self, since: datetime) -> list[ExtractionRecord]:
        with self._session_factory() as session:
            rows = load_extractions_since(session, since)
        records: list[ExtractionRecord] = []
        for row in rows:
            ex = row["extraction"]
            tw_row = row["tweet"]
            tw = RawTweet(
                id=tw_row.id, author=tw_row.author, text=tw_row.text,
                created_at=tw_row.created_at, like_count=tw_row.like_count,
                retweet_count=tw_row.retweet_count, reply_count=tw_row.reply_count,
                url=tw_row.url,
            )
            tickers = [
                (t.ticker, t.sentiment, t.confidence, t.explicit)
                for t in row["tickers"]
            ]
            themes = [(t.theme, t.sentiment, t.confidence) for t in row["themes"]]
            records.append(ExtractionRecord(
                tweet=tw, is_finance=ex.is_finance,
                overall_sentiment=ex.overall_sentiment,
                tickers=tickers, themes=themes,
            ))
        return records

    async def evaluate(self) -> None:
        """Score tickers + themes from extractions, apply strategy, alert."""
        window = self._cfg.get("score_window_minutes", 60)
        since = datetime.utcnow() - timedelta(minutes=window)

        records = self._load_records(since)
        if not records:
            logger.info("Evaluate: no extractions in window")
            return

        ticker_scores, theme_scores, edges = aggregate(
            records, window_minutes=window, threshold=self._threshold
        )
        logger.info(
            f"Evaluate: {len(ticker_scores)} tickers + {len(theme_scores)} themes "
            f"from {len(records)} extractions"
        )

        # Persist + publish ticker scores
        with self._session_factory() as session:
            for ts in ticker_scores.values():
                save_ticker_score(session, ts)
                await self._event_bus.publish({
                    "type": "ticker.score_updated",
                    "ticker": ts.ticker,
                    "score": ts.score,
                    "direct_score": ts.direct_score,
                    "cascade_score": ts.cascade_score,
                    "voices": ts.unique_credible_voices,
                    "tweet_count": ts.tweet_count,
                    "threshold": self._threshold,
                    "threshold_progress": ts.threshold_progress,
                })
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

        # Persist + publish theme scores
        with self._session_factory() as session:
            for theme, tscore in theme_scores.items():
                save_theme_score(
                    session, theme=tscore.theme, score=tscore.score,
                    voices=tscore.voices, tweet_count=tscore.tweet_count,
                    window_start=tscore.window_start, window_end=tscore.window_end,
                )
                await self._event_bus.publish({
                    "type": "theme.score_updated",
                    "theme": tscore.theme,
                    "score": tscore.score,
                    "voices": tscore.voices,
                    "tweet_count": tscore.tweet_count,
                })
                abs_prog = abs(tscore.score) / self._threshold if self._threshold else 0
                prev = self._last_theme_progress.get(tscore.theme, 0.0)
                if abs_prog >= 1.0 > prev:
                    await self._event_bus.publish({
                        "type": "theme.activated",
                        "theme": tscore.theme,
                        "score": tscore.score,
                        "voices": tscore.voices,
                    })
                self._last_theme_progress[tscore.theme] = abs_prog

        # Persist + publish edges (dedup by from/to/tweet_id within this cycle)
        seen_edges: set[tuple[str, str, str | None]] = set()
        with self._session_factory() as session:
            for edge in edges:
                key = (edge.from_id, edge.to_id, edge.tweet_id)
                if key in seen_edges:
                    continue
                seen_edges.add(key)
                save_graph_edge(
                    session, from_id=edge.from_id, to_id=edge.to_id,
                    edge_type=edge.edge_type, weight=edge.weight,
                    sentiment=edge.sentiment, tweet_id=edge.tweet_id,
                )
                await self._event_bus.publish({
                    "type": "graph.edge_added",
                    "from_id": edge.from_id,
                    "to_id": edge.to_id,
                    "edge_type": edge.edge_type,
                    "weight": edge.weight,
                    "sentiment": edge.sentiment,
                    "tweet_id": edge.tweet_id,
                    "tweet_url": edge.tweet_url,
                    # Back-compat with existing UI:
                    "author": edge.from_id.removeprefix("acct:") if edge.from_id.startswith("acct:") else None,
                    "ticker": edge.to_id.removeprefix("ticker:") if edge.to_id.startswith("ticker:") else None,
                })

        signals = self._strategy.evaluate(ticker_scores)
        if not signals:
            logger.info("No signals above threshold")
            return

        logger.info(f"{len(signals)} signal(s) to synthesize and send")
        # Themes most relevant to each ticker (from themes.yaml mapping × theme_score)
        from dolev_ai.analysis.aggregator import _themes_config
        themes_cfg = _themes_config()

        for sig in signals:
            ts = ticker_scores[sig.ticker]
            tweet_texts = [
                r.tweet.text for r in records
                if any(t[0] == sig.ticker for t in r.tickers)
            ][:10]

            # Active themes that map to this ticker
            relevant_themes: list[tuple[str, float]] = []
            for theme_name, ts_obj in theme_scores.items():
                ticker_weights = (themes_cfg.get(theme_name, {}) or {}).get("tickers", {}) or {}
                if sig.ticker in ticker_weights:
                    relevant_themes.append((theme_name, ts_obj.score))
            relevant_themes.sort(key=lambda x: abs(x[1]), reverse=True)
            relevant_themes = relevant_themes[:5]

            await self._event_bus.publish({"type": "signal.synthesizing", "ticker": sig.ticker})

            enriched: Signal = self._synthesizer.synthesize(
                sig.ticker, ts, tweet_texts, themes=relevant_themes
            )
            with self._session_factory() as session:
                signal_id = save_signal(session, enriched)
                log_alert(session, enriched)

                # Route through paper-trade approval flow
                action = paper_trade.handle_signal(enriched, session)
                if action.kind == paper_trade.ActionKind.PROPOSE_OPEN:
                    approval_id = save_pending_approval(session, "open", signal_id)
                    msg_id = await self._bot.send_open_prompt(enriched, approval_id)
                    if msg_id:
                        set_approval_message_id(session, approval_id, msg_id)
                    logger.info(f"Approval prompt sent for {enriched.side.upper()} ${enriched.ticker}")
                elif action.kind == paper_trade.ActionKind.PROPOSE_CLOSE:
                    approval_id = save_pending_approval(
                        session, "close", signal_id,
                        position_id=action.position.id if action.position else None
                    )
                    msg_id = await self._bot.send_close_prompt(
                        action.position, enriched, approval_id
                    )
                    if msg_id:
                        set_approval_message_id(session, approval_id, msg_id)
                    logger.info(f"Close-approval prompt sent for ${enriched.ticker}")
                else:
                    logger.info(f"Signal no-op for ${enriched.ticker} (position same-side)")

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

        app = create_app(
            event_bus=self._event_bus,
            session_factory=self._session_factory,
            threshold=self._threshold,
            started_at=started_at,
            agent_control=self,
        )
        web_config = uvicorn.Config(
            app, host="127.0.0.1", port=8000, log_level="warning", loop="none"
        )
        web_server = uvicorn.Server(web_config)
        asyncio.create_task(web_server.serve())
        logger.info("Monitoring dashboard: http://localhost:8000")

        await self.start_worker()
        logger.info("Dolev AI agent started. Press Ctrl+C to stop.")

        stop_event = asyncio.Event()

        def _shutdown(*_):
            stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                asyncio.get_event_loop().add_signal_handler(sig, _shutdown)
            except (NotImplementedError, RuntimeError):
                pass

        try:
            await stop_event.wait()
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            web_server.should_exit = True
            await self.stop_worker()
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
