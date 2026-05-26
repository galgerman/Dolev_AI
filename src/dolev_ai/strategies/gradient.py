"""MomentumStrategy — multi-feature intraday momentum signals.

Evolution of the original GradientStrategy:
  - Builds a FeatureSnapshot from the MovementSnapshot's pre-computed fields
  - Asks ConfidenceScorer for a 0-100 score + per-feature breakdown
  - Tiers signals on confidence (not raw gradient):
      conf >= auto_threshold       → auto_execute=True
      conf >= entry_threshold      → auto_execute=False (approval-required)
  - Same upstream filters: market hours, daily cap, min total move,
    already-held check.

Backwards compatibility: GradientStrategy is kept as an alias and
constructor accepts the legacy %/min thresholds — when only those are
passed, it operates close to the original behavior.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from dolev_ai.models import MovementSnapshot, Signal
from dolev_ai.strategies.confidence import (
    ConfidenceResult,
    ConfidenceScorer,
)
from dolev_ai.strategies.features import FeatureSnapshot
from dolev_ai.utils.market_hours import is_us_market_open

logger = logging.getLogger(__name__)


@dataclass
class MomentumSignal:
    """A scored momentum signal."""
    signal: Signal
    confidence: float                       # 0..100
    components: dict[str, float]            # per-feature contribution
    features: FeatureSnapshot
    auto_execute: bool

    # Backwards-compat alias for old call sites that read .signal/.auto_execute
    @property
    def explanation(self) -> str:
        nonzero = [(k, v) for k, v in self.components.items() if abs(v) > 0.1]
        nonzero.sort(key=lambda kv: abs(kv[1]), reverse=True)
        return ", ".join(f"{k} {v:+.1f}" for k, v in nonzero[:3])


# Old name retained for compatibility with existing call sites/tests.
GradientSignal = MomentumSignal


def _snapshot_from_movement(mv: MovementSnapshot) -> FeatureSnapshot:
    """Build a FeatureSnapshot from MovementSnapshot fields written by IBKRMarketSource.

    All fields are already pre-computed upstream — this is just a copy.
    """
    return FeatureSnapshot(
        gradient=mv.gradient,
        roc_1m=mv.roc_1m,
        roc_3m=mv.roc_3m,
        roc_5m=mv.roc_5m,
        acceleration=mv.acceleration,
        rel_volume=mv.rel_volume,
        breakout_volume_ratio=mv.breakout_volume_ratio,
        vwap=mv.vwap,
        vwap_state=mv.vwap_state,
        extension_pct=mv.extension_pct,
        broke_pmh=mv.broke_pmh,
        broke_pml=mv.broke_pml,
        rel_strength_spy=mv.rel_strength_spy,
        rel_strength_qqq=mv.rel_strength_qqq,
        rel_strength_sector=mv.rel_strength_sector,
        bid=mv.bid,
        ask=mv.ask,
        spread_pct=mv.spread_pct,
        bars_count=0,
    )


class MomentumStrategy:
    """Emits multi-feature momentum signals scored by ConfidenceScorer.

    Args:
        scorer: optional ConfidenceScorer; defaults to one with built-in weights
        auto_execute_confidence: confidence threshold for auto-execute path (0-100)
        approval_confidence: confidence threshold for approval path
        entry_threshold_pct_per_min: legacy gradient minimum (defaults to 0.3)
        min_total_move_pct: |pct_change| filter
        max_trades_per_day: daily cap
        held_ticker_fn / trades_today_fn / market_open_fn: same as before
    """

    def __init__(
        self,
        scorer: ConfidenceScorer | None = None,
        auto_execute_confidence: float = 70.0,
        approval_confidence: float = 50.0,
        entry_threshold_pct_per_min: float = 0.3,
        auto_execute_threshold_pct_per_min: float | None = None,  # legacy alias
        min_total_move_pct: float = 1.5,
        max_trades_per_day: int = 10,
        held_ticker_fn: Callable[[str], str | None] = lambda _: None,
        trades_today_fn: Callable[[], int] = lambda: 0,
        market_open_fn: Callable[[], bool] = is_us_market_open,
    ) -> None:
        self._scorer = scorer or ConfidenceScorer()
        self._auto_conf = auto_execute_confidence
        self._approval_conf = approval_confidence
        self._entry_grad = entry_threshold_pct_per_min
        # Legacy: if caller passed auto_execute_threshold_pct_per_min only,
        # honour the spirit by raising the auto-execute confidence bar.
        if auto_execute_threshold_pct_per_min is not None:
            # If only gradient thresholds were passed, fall back to the
            # original "tier on gradient" behavior at the upstream gate.
            self._legacy_grad_auto = auto_execute_threshold_pct_per_min
        else:
            self._legacy_grad_auto = None
        self._min_move = min_total_move_pct
        self._max_trades = max_trades_per_day
        self._held = held_ticker_fn
        self._trades_today = trades_today_fn
        self._market_open = market_open_fn

    def evaluate(self, movements: dict[str, MovementSnapshot]) -> list[MomentumSignal]:
        if not self._market_open():
            logger.debug("Market closed — no momentum signals")
            return []

        trades_used = self._trades_today()
        if trades_used >= self._max_trades:
            logger.info(
                f"Daily trade limit reached ({trades_used}/{self._max_trades}) — "
                "no more entries until tomorrow"
            )
            return []

        now = datetime.utcnow()
        out: list[MomentumSignal] = []
        budget = self._max_trades - trades_used

        # First pass: score every candidate that passes the basic gates
        scored: list[tuple[float, MovementSnapshot, ConfidenceResult, str, bool]] = []
        for ticker, mv in movements.items():
            grad = mv.gradient
            if abs(grad) < self._entry_grad:
                continue
            if abs(mv.pct_change) < self._min_move:
                continue
            side = "buy" if grad > 0 else "sell"
            if self._held(ticker) == side:
                continue

            snap = _snapshot_from_movement(mv)
            result = self._scorer.score(snap, side=side)

            # Two paths qualify a signal:
            #   1. Confidence >= approval threshold (new multi-feature path)
            #   2. Legacy gradient-only path: abs(grad) past entry threshold
            #      (preserves behaviour when no rich features are available)
            confidence_qualifies = result.score >= self._approval_conf
            legacy_qualifies = self._legacy_grad_auto is not None  # gradient already cleared _entry_grad above
            if not (confidence_qualifies or legacy_qualifies):
                continue

            # Auto-execute: confidence tier OR legacy gradient tier
            auto = result.score >= self._auto_conf
            if self._legacy_grad_auto is not None and abs(grad) >= self._legacy_grad_auto:
                auto = True
            scored.append((result.score, mv, result, side, auto))

        # Rank by confidence descending
        scored.sort(key=lambda x: x[0], reverse=True)

        for score, mv, result, side, auto in scored:
            if budget <= 0:
                break
            snap = _snapshot_from_movement(mv)
            sig = Signal(
                ticker=mv.ticker,
                side=side,
                conviction=round(score / 100.0, 3),
                suggested_size_pct=0.02,
                rationale="",
                key_drivers=[
                    f"confidence: {score:.1f}/100",
                    f"gradient: {mv.gradient:+.3f}%/min",
                    f"roc_3m: {mv.roc_3m:+.2f}%",
                    f"acceleration: {mv.acceleration:+.3f}",
                    f"rel_vol: {mv.rel_volume:.2f}x",
                ],
                generated_at=now,
            )
            out.append(MomentumSignal(
                signal=sig,
                confidence=score,
                components=result.components,
                features=snap,
                auto_execute=auto,
            ))
            budget -= 1

            logger.info(
                f"Momentum signal: {side.upper()} ${mv.ticker} "
                f"conf={score:.1f} grad={mv.gradient:+.3f}%/min  "
                f"{'AUTO' if auto else 'APPROVAL'}"
            )

        return out


# Backwards-compat alias for existing call sites.
GradientStrategy = MomentumStrategy
