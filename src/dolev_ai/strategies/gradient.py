"""GradientStrategy — intraday momentum signals from IBKR price gradient.

Fires signals when a ticker's price-change rate (slope of last N 1-min bars,
expressed as %/min) crosses a threshold while the market is open. Bypasses
Twitter entirely — the price action IS the signal.

Two conviction tiers:
  - |gradient| >= auto_execute_threshold     → auto-execute, post-fill notification
  - |gradient| >= entry_threshold (but lower) → standard Telegram approval (60s timeout)

Daily trade cap, market-hours gate, and "not-already-held" check.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from dolev_ai.models import MovementSnapshot, Signal
from dolev_ai.utils.market_hours import is_us_market_open

logger = logging.getLogger(__name__)


@dataclass
class GradientSignal:
    """Wraps Signal with the auto-execute flag."""
    signal: Signal
    auto_execute: bool


class GradientStrategy:
    """Emits gradient-driven momentum signals.

    Args:
        entry_threshold_pct_per_min: |gradient| must exceed this (e.g. 0.3 %/min)
        auto_execute_threshold_pct_per_min: above this, no human approval needed
        min_total_move_pct: |pct_change_today| must exceed this (filters flat noise)
        max_trades_per_day: daily cap on auto-executes
        held_ticker_fn: callable(ticker) → "buy"|"sell"|None — current position side
        trades_today_fn: callable() → int — count of positions opened today
    """

    def __init__(
        self,
        entry_threshold_pct_per_min: float = 0.3,
        auto_execute_threshold_pct_per_min: float = 0.5,
        min_total_move_pct: float = 1.5,
        max_trades_per_day: int = 10,
        held_ticker_fn: Callable[[str], str | None] = lambda _: None,
        trades_today_fn: Callable[[], int] = lambda: 0,
        market_open_fn: Callable[[], bool] = is_us_market_open,
    ) -> None:
        self._entry = entry_threshold_pct_per_min
        self._auto = auto_execute_threshold_pct_per_min
        self._min_move = min_total_move_pct
        self._max_trades = max_trades_per_day
        self._held = held_ticker_fn
        self._trades_today = trades_today_fn
        self._market_open = market_open_fn

    def evaluate(self, movements: dict[str, MovementSnapshot]) -> list[GradientSignal]:
        if not self._market_open():
            logger.debug("Market closed — no gradient signals")
            return []

        trades_used = self._trades_today()
        if trades_used >= self._max_trades:
            logger.info(
                f"Daily trade limit reached ({trades_used}/{self._max_trades}) — "
                "no more entries until tomorrow"
            )
            return []

        now = datetime.utcnow()
        out: list[GradientSignal] = []
        budget = self._max_trades - trades_used

        # Rank by |gradient| so the strongest moves are evaluated first
        ranked = sorted(
            movements.items(),
            key=lambda kv: abs(kv[1].gradient),
            reverse=True,
        )

        for ticker, mv in ranked:
            if budget <= 0:
                break

            grad = mv.gradient
            if abs(grad) < self._entry:
                continue
            if abs(mv.pct_change) < self._min_move:
                continue

            # Direction: gradient sign decides the side
            side = "buy" if grad > 0 else "sell"

            # Skip if already holding this direction
            held_side = self._held(ticker)
            if held_side == side:
                continue
            # If holding the opposite side, this becomes an exit signal.
            # paper_trade.handle_signal already routes opposite-side → close.
            # No special handling needed here.

            auto = abs(grad) >= self._auto
            conviction = min(1.0, abs(grad) / (self._auto * 2))  # saturates at 2× auto
            suggested_size_pct = 0.02  # always 2% (RiskManager enforces this anyway)

            sig = Signal(
                ticker=ticker,
                side=side,
                conviction=round(conviction, 3),
                suggested_size_pct=suggested_size_pct,
                rationale="",  # filled by Synthesizer
                key_drivers=[
                    f"gradient: {grad:+.3f}%/min",
                    f"total move: {mv.pct_change:+.2f}%",
                    f"last price: ${mv.last_price:.2f}",
                ],
                generated_at=now,
            )
            out.append(GradientSignal(signal=sig, auto_execute=auto))
            budget -= 1

            logger.info(
                f"Gradient signal: {side.upper()} ${ticker} "
                f"gradient={grad:+.3f}%/min total={mv.pct_change:+.2f}%  "
                f"{'AUTO' if auto else 'APPROVAL'}"
            )

        return out
