"""Dolev AI daemon — collects tweets and fires trade-signal alerts."""
from __future__ import annotations

import argparse
import asyncio
import logging
import pathlib
import signal
import sys
from datetime import datetime, timedelta

import yaml
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

load_dotenv()

from dolev_ai.alert.telegram import TelegramAlerter
from dolev_ai.analysis.aggregator import aggregate
from dolev_ai.db import (
    init_db,
    last_alert_time,
    load_tweets_since,
    log_alert,
    save_signal,
    save_ticker_score,
    save_tweets,
)
from dolev_ai.models import Signal
from dolev_ai.sources.playwright_source import PlaywrightSource
from dolev_ai.strategies.trust_graph import TrustGraphStrategy
from dolev_ai.synth.synthesizer import Synthesizer

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
        self._source = PlaywrightSource(
            profile_dir=PROFILE_DIR,
            headless=cfg.get("playwright", {}).get("headless", False),
            delay_min=cfg.get("playwright", {}).get("request_delay_min_s", 3.0),
            delay_max=cfg.get("playwright", {}).get("request_delay_max_s", 6.0),
            scroll_count=cfg.get("playwright", {}).get("scroll_count", 3),
        )
        self._handles = _load_seed_handles()
        self._alerter = TelegramAlerter(dry_run=dry_run)
        self._synthesizer = Synthesizer(
            model=cfg.get("synth", {}).get("model", "claude-sonnet-4-6"),
            cache_system_prompt=cfg.get("synth", {}).get("cache_system_prompt", True),
        )
        tg_cfg = cfg.get("trust_graph", {})
        with self._session_factory() as session:
            self._strategy = TrustGraphStrategy(
                score_threshold=tg_cfg.get("score_threshold", 5.0),
                min_credible_voices=tg_cfg.get("min_credible_voices", 3),
                cooldown_hours=tg_cfg.get("cooldown_hours", 4.0),
                cooldown_override_multiplier=tg_cfg.get("cooldown_override_multiplier", 2.0),
                last_alert_time_fn=lambda t: last_alert_time(session, t),
            )

    async def collect(self) -> None:
        """Fetch recent tweets from seed accounts and persist."""
        since = datetime.utcnow() - timedelta(
            minutes=self._cfg.get("poll_cadence_minutes", 5) * 2
        )
        logger.info(f"Collecting from {len(self._handles)} accounts since {since:%H:%M}…")
        try:
            tweets = await self._source.fetch_feed_engagements(self._handles, since)
            with self._session_factory() as session:
                added = save_tweets(session, tweets)
            logger.info(f"  Saved {added} new tweets (fetched {len(tweets)})")
        except Exception as e:
            logger.error(f"Collect failed: {e}", exc_info=True)

    async def evaluate(self) -> None:
        """Score tickers, apply strategy, synthesize and send alerts."""
        window = self._cfg.get("score_window_minutes", 60)
        since = datetime.utcnow() - timedelta(minutes=window)

        with self._session_factory() as session:
            tweets = load_tweets_since(session, since)

        if not tweets:
            logger.info("Evaluate: no tweets in window")
            return

        ticker_scores = aggregate(tweets, window_minutes=window)
        logger.info(f"Evaluate: {len(ticker_scores)} tickers scored from {len(tweets)} tweets")

        with self._session_factory() as session:
            for ts in ticker_scores.values():
                save_ticker_score(session, ts)

        signals = self._strategy.evaluate(ticker_scores)
        if not signals:
            logger.info("No signals above threshold")
            return

        logger.info(f"{len(signals)} signal(s) to synthesize and send")
        for sig in signals:
            ts = ticker_scores[sig.ticker]
            tweet_texts = [t.text for t in tweets if sig.ticker in t.text.upper()][:10]
            enriched: Signal = self._synthesizer.synthesize(sig.ticker, ts, tweet_texts)
            with self._session_factory() as session:
                save_signal(session, enriched)
                ok = self._alerter.send(enriched)
                if ok:
                    log_alert(session, enriched)
                    logger.info(f"Alert sent: {enriched.side.upper()} ${enriched.ticker} "
                                f"(conviction={enriched.conviction:.2f})")

    async def run(self) -> None:
        await self._source.start()
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
                pass  # Windows doesn't support add_signal_handler

        try:
            await stop_event.wait()
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
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
