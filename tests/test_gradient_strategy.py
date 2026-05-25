"""Tests for the gradient-first momentum strategy."""
from __future__ import annotations

from datetime import datetime

import pytest

from dolev_ai.models import MovementSnapshot
from dolev_ai.strategies.gradient import GradientStrategy


def _mv(ticker: str, gradient: float, pct_change: float = 3.0) -> MovementSnapshot:
    return MovementSnapshot(
        ticker=ticker,
        pct_change=pct_change,
        last_price=100.0,
        rel_volume=2.0,
        captured_at=datetime.utcnow(),
        gradient=gradient,
    )


def _strategy(**overrides) -> GradientStrategy:
    defaults = dict(
        entry_threshold_pct_per_min=0.3,
        auto_execute_threshold_pct_per_min=0.5,
        min_total_move_pct=1.5,
        max_trades_per_day=10,
        held_ticker_fn=lambda _: None,
        trades_today_fn=lambda: 0,
        market_open_fn=lambda: True,  # default open for tests
    )
    defaults.update(overrides)
    return GradientStrategy(**defaults)


# ── Threshold tiers ───────────────────────────────────────────────────────────

def test_above_auto_threshold_fires_auto_execute():
    sigs = _strategy().evaluate({"NVDA": _mv("NVDA", gradient=0.6)})
    assert len(sigs) == 1
    assert sigs[0].auto_execute is True
    assert sigs[0].signal.side == "buy"
    assert sigs[0].signal.ticker == "NVDA"


def test_between_entry_and_auto_requires_approval():
    sigs = _strategy().evaluate({"NVDA": _mv("NVDA", gradient=0.4)})
    assert len(sigs) == 1
    assert sigs[0].auto_execute is False


def test_below_entry_no_signal():
    sigs = _strategy().evaluate({"NVDA": _mv("NVDA", gradient=0.2)})
    assert sigs == []


def test_negative_gradient_fires_sell():
    sigs = _strategy().evaluate({"NVDA": _mv("NVDA", gradient=-0.55, pct_change=-3.0)})
    assert len(sigs) == 1
    assert sigs[0].signal.side == "sell"
    assert sigs[0].auto_execute is True


# ── Filters ───────────────────────────────────────────────────────────────────

def test_market_closed_no_signals():
    s = _strategy(market_open_fn=lambda: False)
    sigs = s.evaluate({"NVDA": _mv("NVDA", gradient=0.9)})
    assert sigs == []


def test_total_move_filter_blocks_flat_stock():
    # Strong gradient but tiny total move (noise on a flat stock)
    sigs = _strategy().evaluate({"NVDA": _mv("NVDA", gradient=0.6, pct_change=0.5)})
    assert sigs == []


def test_daily_cap_blocks_all_when_full():
    s = _strategy(trades_today_fn=lambda: 10, max_trades_per_day=10)
    sigs = s.evaluate({"NVDA": _mv("NVDA", gradient=0.6)})
    assert sigs == []


def test_daily_cap_allows_partial_budget():
    """If 8 trades used and budget is 10, only 2 more signals allowed even if 5 qualify."""
    s = _strategy(trades_today_fn=lambda: 8, max_trades_per_day=10)
    movements = {
        f"T{i}": _mv(f"T{i}", gradient=0.6 + i * 0.01) for i in range(5)
    }
    sigs = s.evaluate(movements)
    assert len(sigs) == 2


def test_already_held_same_side_skipped():
    # Already holding NVDA buy → don't fire another buy
    s = _strategy(held_ticker_fn=lambda t: "buy" if t == "NVDA" else None)
    sigs = s.evaluate({"NVDA": _mv("NVDA", gradient=0.6)})
    assert sigs == []


def test_already_held_opposite_side_still_fires():
    # Holding NVDA buy, gradient turns negative → fire sell (reverses position)
    s = _strategy(held_ticker_fn=lambda t: "buy" if t == "NVDA" else None)
    sigs = s.evaluate({"NVDA": _mv("NVDA", gradient=-0.6, pct_change=-2.0)})
    assert len(sigs) == 1
    assert sigs[0].signal.side == "sell"


# ── Ranking ───────────────────────────────────────────────────────────────────

def test_ranks_by_gradient_magnitude():
    """Highest |gradient| should be first in the output list."""
    movements = {
        "LOW":  _mv("LOW",  gradient=0.35),
        "HIGH": _mv("HIGH", gradient=0.80),
        "MID":  _mv("MID",  gradient=0.55),
    }
    sigs = _strategy().evaluate(movements)
    tickers = [s.signal.ticker for s in sigs]
    assert tickers == ["HIGH", "MID", "LOW"]


def test_key_drivers_contain_gradient():
    sigs = _strategy().evaluate({"NVDA": _mv("NVDA", gradient=0.42, pct_change=4.1)})
    drivers = sigs[0].signal.key_drivers
    assert any("gradient" in d for d in drivers)
    assert any("0.420" in d or "+0.420" in d for d in drivers)
