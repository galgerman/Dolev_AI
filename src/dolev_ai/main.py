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
    load_latest_movements,
    log_alert,
    pending_approvals_older_than,
    prune_old_data,
    save_graph_edge,
    save_movements,
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
from dolev_ai.sources.tradingview import TradingViewSource
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
        tv_cfg = cfg.get("tradingview", {}) or {}
        self._tv_enabled = tv_cfg.get("enabled", True)
        self._tv_source = TradingViewSource(
            top_n_per_side=tv_cfg.get("top_n_per_side", 100),
            min_market_cap=float(tv_cfg.get("min_market_cap_m", 100)) * 1_000_000,
            min_volume=tv_cfg.get("min_volume", 100_000),
        )
        self._tv_confirmation_weight = tv_cfg.get("confirmation_weight", 0.5)
        self._tv_strong_move_pct = tv_cfg.get("strong_move_pct", 5.0)
        self._tv_discovery_weight = tv_cfg.get("discovery_weight", 0.5)
        inv_cfg = tv_cfg.get("investigate", {}) or {}
        self._inv_enabled = inv_cfg.get("enabled", True)
        self._inv_threshold = inv_cfg.get("pct_threshold", 5.0)
        self._inv_max_per_cycle = inv_cfg.get("max_per_cycle", 5)
        self._inv_dedupe_minutes = inv_cfg.get("dedupe_minutes", 30)
        self._inv_last: dict[str, datetime] = {}      # ticker → last investigated_at
        self._handles = _load_seed_handles()
        self._alerter = TelegramAlerter(dry_run=dry_run)
        self._bot = TelegramBot(dry_run=dry_run, on_callback=self._on_tg_callback)
        self._threshold = cfg.get("trust_graph", {}).get("score_threshold", 5.0)
        pt_cfg = cfg.get("paper_trading", {})
        self._approval_timeout_minutes = pt_cfg.get("approval_timeout_minutes", 15)
        self._synthesizer = Synthesizer()

        # Broker + risk management
        broker_cfg = cfg.get("broker", {}) or {}
        self._broker_enabled = broker_cfg.get("enabled", False)
        self._broker: "IBKRBroker | None" = None
        self._risk: "RiskManager | None" = None
        self._ibkr_market: "IBKRMarketSource | None" = None
        from dolev_ai.broker.risk import RiskManager
        risk_cfg = broker_cfg.get("risk", {}) or {}
        self._risk = RiskManager(
            max_position_pct=risk_cfg.get("max_position_pct", 2.0),
            stop_loss_pct=risk_cfg.get("stop_loss_pct", 2.0),
            max_open_positions=risk_cfg.get("max_open_positions", 5),
        )
        # Shared reference data cache (per-cycle gradients + sector ETFs + premarket H/L)
        from dolev_ai.strategies.reference_data import ReferenceCache
        self._ref_cache = ReferenceCache()

        if self._broker_enabled:
            from dolev_ai.broker.ibkr import IBKRBroker
            self._broker = IBKRBroker(
                host=broker_cfg.get("host", "127.0.0.1"),
                port=broker_cfg.get("port", 7497),
                client_id=broker_cfg.get("client_id", 1),
                timeout=broker_cfg.get("connect_timeout_s", 10.0),
            )
            md_cfg = broker_cfg.get("market_data", {}) or {}
            if md_cfg.get("enabled", False):
                from dolev_ai.broker.market_data import IBKRMarketSource
                self._ibkr_market = IBKRMarketSource(
                    broker=self._broker,
                    reference_cache=self._ref_cache,
                    top_n_per_side=md_cfg.get("top_n_per_side", 50),
                    gradient_top_n=md_cfg.get("gradient_top_n", 20),
                    gradient_bars=md_cfg.get("gradient_bars", 10),
                    feature_bars=md_cfg.get("feature_bars", 12),
                    min_market_cap_m=float(broker_cfg.get("min_market_cap_m",
                        cfg.get("tradingview", {}).get("min_market_cap_m", 100))),
                    min_volume=broker_cfg.get("min_volume",
                        cfg.get("tradingview", {}).get("min_volume", 100_000)),
                    snapshot_enabled=md_cfg.get("snapshot_enabled", True),
                )
                logger.info("IBKR market data source enabled — will replace TradingView")

        tg_cfg = cfg.get("trust_graph", {})
        with self._session_factory() as session:
            self._strategy = TrustGraphStrategy(
                score_threshold=self._threshold,
                min_credible_voices=tg_cfg.get("min_credible_voices", 3),
                cooldown_hours=tg_cfg.get("cooldown_hours", 4.0),
                cooldown_override_multiplier=tg_cfg.get("cooldown_override_multiplier", 2.0),
                last_alert_time_fn=lambda t: last_alert_time(session, t),
            )

        # Pipeline mode (observe | paper | live) — research-grade switch
        pipeline_cfg = cfg.get("pipeline", {}) or {}
        self._pipeline_mode = pipeline_cfg.get("mode", "paper")
        self._auto_execute_conf = pipeline_cfg.get("auto_execute_confidence_min", 70.0)
        self._approval_conf = pipeline_cfg.get("approval_confidence_min", 50.0)

        # Top-level Twitter kill switch
        twitter_cfg = cfg.get("twitter", {}) or {}
        self._twitter_enabled = twitter_cfg.get("enabled", False)

        # Observer + position tracker config
        observer_cfg = cfg.get("observer", {}) or {}
        self._observer_horizons = observer_cfg.get(
            "horizons_seconds", [30, 60, 180, 300, 900, 1800]
        )
        self._observer_tick_seconds = observer_cfg.get("tick_seconds", 5.0)
        tracker_cfg = cfg.get("position_tracker", {}) or {}
        self._tracker_poll_seconds = tracker_cfg.get("poll_seconds", 5.0)

        # Confidence scorer (weights come from config; defaults baked in)
        feat_cfg = cfg.get("features", {}) or {}
        weight_overrides = feat_cfg.get("confidence_weights")
        from dolev_ai.strategies.confidence import ConfidenceScorer
        self._scorer = ConfidenceScorer(weights=weight_overrides)

        # Forward-return observer + MFE/MAE tracker (lazy-started in start_worker)
        from dolev_ai.observer import ForwardReturnObserver
        from dolev_ai.position_tracker import PositionTracker
        from dolev_ai.prices import get_price_async
        self._observer: ForwardReturnObserver | None = ForwardReturnObserver(
            self._session_factory, get_price_async, tick_seconds=self._observer_tick_seconds,
        )
        self._tracker: PositionTracker | None = PositionTracker(
            self._session_factory, get_price_async, poll_seconds=self._tracker_poll_seconds,
        )

        # Momentum / gradient strategy
        mom_cfg = cfg.get("momentum", {}) or {}
        self._momentum_enabled = mom_cfg.get("enabled", False)
        # Twitter is gated at the top level now; momentum still honours its own legacy switch
        self._momentum_disable_twitter = (
            not self._twitter_enabled
            or mom_cfg.get("disable_timeline_scraping", True)
        )
        self._momentum_poll_seconds = mom_cfg.get("poll_cadence_seconds", 30)
        self._momentum_eval_seconds = mom_cfg.get("eval_cadence_seconds", 15)
        self._momentum_flatten_before_close = mom_cfg.get("flatten_minutes_before_close", 5)
        self._momentum_approval_timeout_sec = mom_cfg.get("approval_timeout_seconds", 60)
        self._gradient_strategy = None
        if self._momentum_enabled:
            from dolev_ai.strategies.gradient import MomentumStrategy
            self._gradient_strategy = MomentumStrategy(
                scorer=self._scorer,
                auto_execute_confidence=self._auto_execute_conf,
                approval_confidence=self._approval_conf,
                entry_threshold_pct_per_min=mom_cfg.get("entry_threshold_pct_per_min", 0.3),
                min_total_move_pct=mom_cfg.get("min_total_move_pct", 1.5),
                max_trades_per_day=mom_cfg.get("max_trades_per_day", 10),
                held_ticker_fn=self._held_side_for,
                trades_today_fn=self._trades_opened_today,
            )
            logger.info(
                f"Momentum mode ON [pipeline={self._pipeline_mode}] — "
                f"auto≥{self._auto_execute_conf}/100, approval≥{self._approval_conf}/100, "
                f"max {mom_cfg.get('max_trades_per_day', 10)} trades/day"
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
                approval_id, decision, session, self._event_bus, self._synthesizer,
                broker=self._broker, risk=self._risk,
            )
            approval = session.get(SignalApprovalRow, approval_id)
            if approval and approval.telegram_message_id:
                await self._bot.edit_message(approval.telegram_message_id, ack)
            logger.info(f"Approval {approval_id} → {decision}: {ack}")

    async def expire_approvals(self) -> None:
        """Mark pending approvals older than timeout as expired."""
        from datetime import timedelta
        # Momentum mode uses a short seconds-based timeout; trust-graph uses minutes.
        if self._momentum_enabled:
            cutoff = datetime.utcnow() - timedelta(seconds=self._momentum_approval_timeout_sec)
        else:
            cutoff = datetime.utcnow() - timedelta(minutes=self._approval_timeout_minutes)
        with self._session_factory() as session:
            stale = pending_approvals_older_than(session, cutoff)
            for approval in stale:
                ack = await paper_trade.handle_approval_callback(
                    approval.id, "expired", session, self._event_bus, self._synthesizer,
                    broker=self._broker, risk=self._risk,
                )
                if approval.telegram_message_id:
                    await self._bot.edit_message(approval.telegram_message_id, ack)
                logger.info(f"Expired approval {approval.id} for signal {approval.signal_id}")

    # ── Momentum helpers ────────────────────────────────────────────────────

    def _held_side_for(self, ticker: str) -> str | None:
        """Return 'buy'/'sell' if we have an open position in this ticker, else None."""
        from dolev_ai.db import position_for_ticker
        with self._session_factory() as session:
            pos = position_for_ticker(session, ticker)
            return pos.side if pos else None

    def _trades_opened_today(self) -> int:
        """Count positions opened since midnight UTC (proxy for trades today)."""
        from dolev_ai.db import PaperPositionRow
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        with self._session_factory() as session:
            return (
                session.query(PaperPositionRow)
                .filter(PaperPositionRow.opened_at >= today_start)
                .count()
            )

    async def evaluate_gradient(self) -> None:
        """Run MomentumStrategy against the latest movements.

        Pipeline mode:
          observe — record signal observation, NO orders, NO Telegram per-signal
          paper   — record observation + auto-execute through paper broker + rich Telegram journal
          live    — same as paper but broker.trading_mode='live' (config-gated)
        """
        if not self._momentum_enabled or self._gradient_strategy is None:
            return
        from dolev_ai.utils.market_hours import is_us_market_open
        if not is_us_market_open():
            return

        with self._session_factory() as session:
            movements = load_latest_movements(session, max_age_minutes=5)

        if not movements:
            return

        signals = self._gradient_strategy.evaluate(movements)
        if not signals:
            return

        logger.info(
            f"Momentum strategy [{self._pipeline_mode}]: {len(signals)} signal(s)"
        )

        from dolev_ai.db import save_signal_observation as _save_obs
        from dataclasses import asdict as _asdict

        for ms in signals:
            sig = ms.signal
            mv = movements.get(sig.ticker)
            entry_price = mv.last_price if mv is not None else 0.0
            sector_etf = mv.sector_etf if mv is not None else None

            # Always record a signal observation — fuels research reports
            try:
                with self._session_factory() as session:
                    _save_obs(
                        session,
                        ticker=sig.ticker,
                        side=sig.side,
                        entry_price=entry_price,
                        confidence=ms.confidence,
                        components=ms.components,
                        features=_asdict(ms.features),
                        horizons_seconds=self._observer_horizons,
                        sector_etf=sector_etf,
                        generated_at=sig.generated_at,
                    )
            except Exception as e:
                logger.warning(f"Failed to record signal observation: {e}")

            await self._event_bus.publish({
                "type": "signal.fired",
                "ticker": sig.ticker,
                "side": sig.side,
                "confidence": ms.confidence,
                "conviction": sig.conviction,
                "key_drivers": sig.key_drivers,
                "components": ms.components,
                "generated_at": sig.generated_at.isoformat(),
                "pipeline_mode": self._pipeline_mode,
                "auto_executed": ms.auto_execute and self._pipeline_mode != "observe",
            })

            # Observe mode: stop here. No orders, no Telegram blast.
            if self._pipeline_mode == "observe":
                continue

            # Paper / live: route through the broker
            with self._session_factory() as session:
                if ms.auto_execute:
                    pos, _ = await paper_trade.auto_execute(
                        sig, session, self._event_bus,
                        broker=self._broker, risk=self._risk,
                        features=ms.features, confidence=ms.confidence,
                        components=ms.components, tracker=self._tracker,
                    )
                    log_alert(session, sig)
                    if pos is not None and pos.status == "open":
                        msg_id = await self._bot.send_trade_open(
                            pos,
                            features=ms.features,
                            components=ms.components,
                            latency_ms=pos.fill_latency_ms,
                            slippage_bps=pos.slippage_bps,
                            confidence=ms.confidence,
                            mode=self._pipeline_mode,
                        )
                        if msg_id is not None:
                            pos.telegram_message_id = msg_id
                            session.commit()
                    elif pos is not None and pos.status == "closed":
                        # auto_execute closed an opposite-side position
                        hold_min = None
                        if pos.opened_at and pos.closed_at:
                            hold_min = (pos.closed_at - pos.opened_at).total_seconds() / 60.0
                        await self._bot.send_trade_close(
                            pos, mfe_pct=pos.mfe_pct, mae_pct=pos.mae_pct,
                            hold_minutes=hold_min, exit_reason=pos.exit_reason,
                            edit_message_id=pos.telegram_message_id,
                        )
                else:
                    signal_id = save_signal(session, sig)
                    log_alert(session, sig)
                    action = paper_trade.handle_signal(sig, session)
                    if action.kind == paper_trade.ActionKind.PROPOSE_OPEN:
                        approval_id = save_pending_approval(session, "open", signal_id)
                        msg_id = await self._bot.send_open_prompt(sig, approval_id)
                        if msg_id:
                            set_approval_message_id(session, approval_id, msg_id)
                    elif action.kind == paper_trade.ActionKind.PROPOSE_CLOSE:
                        approval_id = save_pending_approval(
                            session, "close", signal_id,
                            position_id=action.position.id if action.position else None,
                        )
                        msg_id = await self._bot.send_close_prompt(
                            action.position, sig, approval_id
                        )
                        if msg_id:
                            set_approval_message_id(session, approval_id, msg_id)

    async def eod_flatten(self) -> None:
        """Close all open positions a few minutes before market close."""
        if not self._momentum_enabled:
            return
        from dolev_ai.utils.market_hours import minutes_to_close
        mtc = minutes_to_close()
        if mtc is None:
            return  # market closed
        if mtc > self._momentum_flatten_before_close:
            return  # not yet — wait for the window

        from dolev_ai.db import open_positions as _open_positions
        with self._session_factory() as session:
            positions = _open_positions(session)
        if not positions:
            return

        logger.info(f"EOD flatten: closing {len(positions)} positions ({mtc} min to close)")
        closed_count = 0
        for pos in positions:
            with self._session_factory() as session:
                # Use a synthetic exit_signal_id = -1 to mark EOD-close
                result = await paper_trade.close_position(
                    pos.id, -1, session, self._event_bus, broker=self._broker,
                    exit_reason="eod_flatten", tracker=self._tracker,
                )
                if result:
                    closed_count += 1
                    # Send rich close journal — edit original entry message if we have it
                    hold_min = None
                    if result.opened_at and result.closed_at:
                        hold_min = (result.closed_at - result.opened_at).total_seconds() / 60.0
                    try:
                        await self._bot.send_trade_close(
                            result,
                            mfe_pct=result.mfe_pct, mae_pct=result.mae_pct,
                            hold_minutes=hold_min, exit_reason="eod_flatten",
                            edit_message_id=result.telegram_message_id,
                        )
                    except Exception as e:
                        logger.warning(f"send_trade_close failed for {result.ticker}: {e}")
        await self._bot.send_text(
            f"🌅 EOD flatten: closed {closed_count}/{len(positions)} positions "
            f"({mtc} min before close)"
        )

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

    async def _notify_market_open(self) -> None:
        broker_status = "IBKR connected" if (self._broker and self._broker.connected) else "broker offline"
        await self._bot.send_text(
            f"*Market open* — 9:30 AM ET\n"
            f"{broker_status}  |  pipeline: {self._pipeline_mode.upper()}\n"
            f"_Momentum scanner running._"
        )

    async def _notify_market_close(self) -> None:
        from dolev_ai.db import PaperPositionRow
        with self._session_factory() as session:
            open_count = (
                session.query(PaperPositionRow)
                .filter(PaperPositionRow.status == "open")
                .count()
            )
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        with self._session_factory() as session:
            trades_today = (
                session.query(PaperPositionRow)
                .filter(PaperPositionRow.opened_at >= today_start)
                .count()
            )
        await self._bot.send_text(
            f"*Market closed* — 4:00 PM ET\n"
            f"Trades today: {trades_today}  |  Open positions: {open_count}\n"
            f"_EOD flatten running..._"
        )

    async def start_worker(self) -> bool:
        async with self._worker_lock:
            if self._scheduler is not None:
                return False

            await self._source.start()
            await self._tv_source.start()
            await self._bot.start()

            if self._broker is not None:
                connected = await self._broker.connect()
                if connected:
                    logger.info("IBKR broker connected — orders will route to paper account")
                else:
                    logger.warning("IBKR broker failed to connect — falling back to sim mode")
            await self._ensure_extractor()

            scheduler = AsyncIOScheduler()

            # Twitter scrape — skipped in pure-momentum mode
            twitter_disabled = self._momentum_enabled and self._momentum_disable_twitter
            if not twitter_disabled:
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
            else:
                logger.info("Twitter timeline scraping disabled (momentum mode)")

            ret = self._cfg.get("retention", {}) or {}
            scheduler.add_job(
                self.prune, "interval",
                minutes=ret.get("prune_interval_minutes", 60),
                next_run_time=datetime.utcnow() + timedelta(minutes=5),
            )
            # Approval expiry — every minute
            scheduler.add_job(self.expire_approvals, "interval", minutes=1)

            # Market data poll — momentum mode uses fast cadence (seconds),
            # legacy mode uses minutes from tradingview config
            if self._momentum_enabled:
                scheduler.add_job(
                    self.fetch_movers, "interval",
                    seconds=self._momentum_poll_seconds,
                    next_run_time=datetime.utcnow() + timedelta(seconds=10),
                )
                scheduler.add_job(
                    self.evaluate_gradient, "interval",
                    seconds=self._momentum_eval_seconds,
                    next_run_time=datetime.utcnow() + timedelta(seconds=20),
                )
                # EOD flatten — check every minute near close
                scheduler.add_job(self.eod_flatten, "interval", minutes=1)
                logger.info(
                    f"Momentum scheduler: scanner every {self._momentum_poll_seconds}s, "
                    f"strategy every {self._momentum_eval_seconds}s"
                )
            elif self._tv_enabled:
                tv_cfg = self._cfg.get("tradingview", {}) or {}
                scheduler.add_job(
                    self.fetch_movers, "interval",
                    minutes=tv_cfg.get("poll_cadence_minutes", 5),
                    next_run_time=datetime.utcnow() + timedelta(seconds=15),
                )
            # EOD summary — weekdays at 21:00 UTC (16:00 ET)
            eod_cfg = self._cfg.get("eod_summary", {}) or {}
            if eod_cfg.get("enabled", True):
                scheduler.add_job(
                    self.send_eod_summary, "cron",
                    day_of_week=eod_cfg.get("days", "mon-fri"),
                    hour=eod_cfg.get("cron_hour_utc", 21),
                    minute=eod_cfg.get("cron_minute", 0),
                )
            # Market open / close notifications (ET timezone)
            if self._momentum_enabled:
                scheduler.add_job(
                    self._notify_market_open, "cron",
                    day_of_week="mon-fri", hour=9, minute=30,
                    timezone="America/New_York",
                )
                scheduler.add_job(
                    self._notify_market_close, "cron",
                    day_of_week="mon-fri", hour=16, minute=0,
                    timezone="America/New_York",
                )

            scheduler.start()
            self._scheduler = scheduler
            self._worker_started_at = datetime.utcnow()

            # Forward-return observer always runs in momentum mode — it powers
            # research reports even while paper-trading.
            if self._momentum_enabled and self._observer is not None:
                self._observer.start()

            logger.info(
                f"Dolev AI agent started [pipeline={self._pipeline_mode}, "
                f"twitter={'on' if self._twitter_enabled else 'off'}]."
            )
            broker_status = "IBKR connected" if (self._broker and self._broker.connected) else "broker offline"
            await self._bot.send_text(
                f"*Dolev AI started* — mode: {self._pipeline_mode.upper()}\n"
                f"{broker_status}  |  auto≥{self._auto_execute_conf:.0f}/100\n"
                f"_Watching for signals..._"
            )
            return True

    async def stop_worker(self) -> bool:
        async with self._worker_lock:
            if self._scheduler is None:
                return False

            scheduler = self._scheduler
            self._scheduler = None
            self._worker_started_at = None
            scheduler.shutdown(wait=False)
            if self._observer is not None:
                await self._observer.stop()
            if self._tracker is not None:
                await self._tracker.stop_all()
            await self._bot.send_text("*Dolev AI stopped.*")
            await self._bot.stop()
            await self._source.stop()
            await self._tv_source.stop()
            if self._broker is not None:
                await self._broker.disconnect()
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

    async def fetch_movers(self) -> None:
        """Poll market movers (IBKR with gradient if connected, else TradingView)."""
        # In momentum mode, skip when market is closed (no point computing gradient on stale prices)
        if self._momentum_enabled:
            from dolev_ai.utils.market_hours import is_us_market_open
            if not is_us_market_open():
                return
        try:
            if self._ibkr_market is not None and self._broker and self._broker.connected:
                movers = await self._ibkr_market.fetch_movers()
                source = "IBKR"
            else:
                movers = await self._tv_source.fetch_movers()
                source = "TradingView"
        except Exception as e:
            logger.error(f"fetch_movers failed: {e}", exc_info=True)
            return
        if not movers:
            return

        with self._session_factory() as session:
            save_movements(session, movers)

        gainers = [m for m in movers if m.side == "gainer"]
        losers = [m for m in movers if m.side == "loser"]
        top = gainers[0] if gainers else None
        grad_str = f"  gradient={top.gradient:+.3f}%/min" if top and getattr(top, "gradient", 0) else ""
        logger.info(
            f"{source}: {len(gainers)} gainers "
            + (f"(top: ${top.ticker} {top.pct_change:+.1f}%{grad_str})" if top else "")
            + f" · {len(losers)} losers"
        )

        await self._event_bus.publish({
            "type": "movers.updated",
            "gainers": [self._mover_to_dict(m) for m in gainers[:25]],
            "losers": [self._mover_to_dict(m) for m in losers[:25]],
            "captured_at": (movers[0].captured_at.isoformat() if movers else None),
        })

        # Queue investigations for big movers we don't already understand
        if self._inv_enabled:
            await self._queue_investigations(movers)

    @staticmethod
    def _mover_to_dict(m) -> dict:
        return {
            "ticker": m.ticker,
            "pct_change": round(m.pct_change, 2),
            "last_price": round(m.last_price, 2),
            "rel_volume": round(getattr(m, "rel_volume", 0.0), 2),
            "market_cap": getattr(m, "market_cap", 0.0),
            "rank": m.rank,
            "side": m.side,
            "gradient": round(getattr(m, "gradient", 0.0), 4),
            "gradient_bars": getattr(m, "gradient_bars", 0),
        }

    async def _queue_investigations(self, movers: list) -> None:
        """For unexplained big movers, kick off a targeted X search."""
        now = datetime.utcnow()
        dedupe_cutoff = now - timedelta(minutes=self._inv_dedupe_minutes)
        # Drop expired dedupe entries
        self._inv_last = {t: ts for t, ts in self._inv_last.items() if ts >= dedupe_cutoff}

        # Latest Twitter sentiment per ticker (last 60 min)
        from dolev_ai.db import TickerScoreRow
        recent_cutoff = now - timedelta(minutes=60)
        with self._session_factory() as session:
            recent_scores = {
                r.ticker: r.score for r in session.query(TickerScoreRow)
                .filter(TickerScoreRow.window_end >= recent_cutoff).all()
            }

        # Sort movers by |pct_change| desc, only consider above threshold
        candidates = sorted(
            [m for m in movers if abs(m.pct_change) >= self._inv_threshold],
            key=lambda m: -abs(m.pct_change),
        )
        queued = 0
        for m in candidates:
            if queued >= self._inv_max_per_cycle:
                break
            if m.ticker in self._inv_last:
                continue
            tw_score = recent_scores.get(m.ticker, 0.0)
            # Investigate if: no twitter data, OR sentiment opposes movement
            if tw_score == 0 or (tw_score > 0) != (m.pct_change > 0):
                self._inv_last[m.ticker] = now
                queued += 1
                asyncio.create_task(self._investigate_one(m.ticker, m.pct_change))
        if queued:
            logger.info(f"Investigating {queued} unexplained mover(s)")

    async def _investigate_one(self, ticker: str, pct_change: float) -> None:
        try:
            logger.info(f"Investigating ${ticker} ({pct_change:+.1f}%) — X search")
            tweets = await self._source.search_query(f"${ticker}", max_tweets=15)
            if not tweets:
                logger.info(f"Investigation for ${ticker}: no tweets found")
                return
            with self._session_factory() as session:
                from dolev_ai.db import save_tweets
                new_count = save_tweets(session, tweets)
            logger.info(f"Investigation ${ticker}: {new_count} new tweet(s)")
            if self._extractor is not None and tweets:
                for tw in tweets[:15]:
                    await self._extractor.submit(tw)
        except Exception as e:
            logger.warning(f"Investigation for ${ticker} failed: {e}")

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

        # Latest TradingView movements per ticker (read fresh each evaluate)
        latest_movements: dict = {}
        if self._tv_enabled:
            with self._session_factory() as session:
                latest_movements = load_latest_movements(session, max_age_minutes=30)

        if not records and not latest_movements:
            logger.info("Evaluate: no extractions and no market data in window")
            return

        ticker_scores, theme_scores, edges = aggregate(
            records, window_minutes=window, threshold=self._threshold,
            latest_movements=latest_movements,
            confirmation_weight=self._tv_confirmation_weight,
            strong_move_pct=self._tv_strong_move_pct,
            discovery_weight=self._tv_discovery_weight,
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
