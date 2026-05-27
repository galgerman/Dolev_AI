"""Tests for the Telegram trade-journal formatters."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from dolev_ai.alert.telegram_bot import format_trade_close, format_trade_open
from dolev_ai.strategies.features import FeatureSnapshot


@dataclass
class FakePos:
    ticker: str = "NVDA"
    side: str = "buy"
    shares: int = 22
    entry_price: float = 921.30
    stop_price: float = 902.87
    exit_price: float | None = None
    pnl_pct: float | None = None
    pnl_dollars: float | None = None


def _feat(**kw) -> FeatureSnapshot:
    base = dict(
        gradient=0.52, acceleration=0.18, roc_3m=1.8,
        rel_strength_spy=0.21, rel_strength_sector=0.14,
        vwap_state="above", broke_pmh=True, broke_pml=False,
        rel_volume=4.2, spread_pct=0.04,
    )
    base.update(kw)
    return FeatureSnapshot(**{k: v for k, v in base.items() if k in FeatureSnapshot.__dataclass_fields__})


# ── Open formatter ───────────────────────────────────────────────────────────

def test_open_includes_side_and_ticker():
    text = format_trade_open(FakePos(), confidence=82.0)
    assert "PAPER BUY $NVDA" in text
    assert "conf=82.0/100" in text


def test_open_includes_fill_details():
    text = format_trade_open(FakePos())
    assert "22 sh" in text
    assert "$921.30" in text
    assert "stop $902.87" in text


def test_open_includes_latency_and_slippage():
    text = format_trade_open(FakePos(), latency_ms=240, slippage_bps=3.0)
    assert "latency: 240ms" in text
    assert "slip: +3.0bp" in text


def test_open_renders_components_block():
    components = {
        "gradient": 13.0,
        "acceleration": 9.0,
        "roc_3m": 8.0,
        "rel_volume": 5.0,
    }
    text = format_trade_open(
        FakePos(), components=components, confidence=82.0, features=_feat(),
    )
    assert "gradient" in text
    assert "+13.0" in text
    assert "total" in text


def test_open_features_block_shows_pmh_broke():
    text = format_trade_open(FakePos(), features=_feat(broke_pmh=True))
    assert "pmh" in text
    assert "broke" in text


def test_open_works_without_features_or_components():
    text = format_trade_open(FakePos(), confidence=70.0)
    assert "PAPER BUY $NVDA" in text
    # No empty components table
    assert "total" not in text


# ── Close formatter ──────────────────────────────────────────────────────────

def test_close_shows_pnl_and_exit_price():
    pos = FakePos(
        exit_price=935.0, pnl_pct=0.0148, pnl_dollars=327.0,
    )
    text = format_trade_close(pos, mfe_pct=2.1, mae_pct=-0.3,
                              hold_minutes=23.0, exit_reason="opposite_signal")
    assert "CLOSED BUY $NVDA" in text
    assert "$935.00" in text
    assert "+1.48%" in text
    assert "327.00" in text  # dollar formatting puts the sign before the $
    assert "MFE +2.10%" in text
    assert "MAE -0.30%" in text
    assert "23.0m" in text
    assert "opposite_signal" in text


def test_close_uses_negative_emoji_for_loss():
    pos = FakePos(exit_price=905.0, pnl_pct=-0.018, pnl_dollars=-360.0)
    text = format_trade_close(pos)
    assert "❌" in text
    assert "CLOSED BUY $NVDA" in text


def test_close_handles_missing_fields():
    pos = FakePos(exit_price=921.0)
    text = format_trade_close(pos)
    # No crash, just minimal output
    assert "CLOSED" in text
    assert "$921.00" in text
