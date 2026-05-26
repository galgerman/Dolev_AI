"""Tests for the feature engine (pure functions over OHLCV bars)."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from dolev_ai.strategies.features import (
    FeatureSnapshot,
    RefGradients,
    compute_acceleration,
    compute_breakout_volume_ratio,
    compute_extension,
    compute_features,
    compute_rel_volume,
    compute_roc,
    compute_vwap,
    vwap_state,
)


@dataclass
class Bar:
    open: float
    high: float
    low: float
    close: float
    volume: float


# ── ROC ───────────────────────────────────────────────────────────────────────

def test_roc_1m_basic():
    # closes: [..., 100.0, 101.0] → ROC_1m = 1.0%
    assert compute_roc([100.0, 100.0, 100.0, 101.0], 1) == pytest.approx(1.0)


def test_roc_3m():
    # closes: [..., 100.0, 101.0, 102.0, 103.0] → ROC_3m = 3.0%
    assert compute_roc([100.0, 101.0, 102.0, 103.0], 3) == pytest.approx(3.0)


def test_roc_returns_zero_when_not_enough_bars():
    assert compute_roc([100.0], 3) == 0.0


def test_roc_negative():
    assert compute_roc([100.0, 100.0, 95.0], 1) == pytest.approx(-5.0)


# ── Acceleration ──────────────────────────────────────────────────────────────

def test_acceleration_positive():
    # prev ROC = (101/100-1)*100 = 1.0%; cur ROC = (103/101-1)*100 = 1.98%
    # accel = 1.98 - 1.0 ≈ +0.98
    accel = compute_acceleration([100.0, 101.0, 103.0])
    assert accel == pytest.approx(0.9802, abs=1e-3)


def test_acceleration_negative():
    # prev: +2%, cur: +0.5% → accel = -1.5
    accel = compute_acceleration([100.0, 102.0, 102.51])
    assert accel < 0


def test_acceleration_zero_when_too_few_bars():
    assert compute_acceleration([100.0, 101.0]) == 0.0


# ── VWAP ──────────────────────────────────────────────────────────────────────

def test_vwap_single_bar():
    bars = [Bar(open=100, high=102, low=98, close=100, volume=1000)]
    # typical = (102+98+100)/3 = 100; vwap = 100*1000/1000 = 100
    assert compute_vwap(bars) == 100.0


def test_vwap_weighted_by_volume():
    bars = [
        Bar(open=100, high=100, low=100, close=100, volume=100),
        Bar(open=110, high=110, low=110, close=110, volume=900),
    ]
    # weighted: (100*100 + 110*900)/1000 = 109.0
    assert compute_vwap(bars) == 109.0


def test_vwap_zero_volume():
    bars = [Bar(open=100, high=100, low=100, close=100, volume=0)]
    assert compute_vwap(bars) == 0.0


# ── VWAP state + extension ────────────────────────────────────────────────────

def test_vwap_state_above():
    assert vwap_state(price=101.0, vwap=100.0, near_pct=0.2) == "above"


def test_vwap_state_below():
    assert vwap_state(price=99.0, vwap=100.0) == "below"


def test_vwap_state_near():
    # 100.1 is 0.1% above 100 — within near_pct=0.2
    assert vwap_state(price=100.1, vwap=100.0, near_pct=0.2) == "near"


def test_vwap_state_unknown_when_vwap_zero():
    assert vwap_state(price=100.0, vwap=0.0) == "unknown"


def test_extension_positive_above_vwap():
    assert compute_extension(price=102.0, vwap=100.0) == pytest.approx(2.0)


def test_extension_negative_below_vwap():
    assert compute_extension(price=98.0, vwap=100.0) == pytest.approx(-2.0)


# ── Breakout volume ratio ─────────────────────────────────────────────────────

def test_breakout_volume_ratio_high():
    # prior median = 100; last = 500 → ratio 5.0
    assert compute_breakout_volume_ratio([100, 100, 100, 100, 500]) == 5.0


def test_breakout_volume_ratio_low_when_baseline_zero():
    assert compute_breakout_volume_ratio([0, 0, 0, 0, 1000]) == 0.0


def test_breakout_volume_ratio_too_few_bars():
    assert compute_breakout_volume_ratio([100, 100]) == 0.0


# ── Relative volume ───────────────────────────────────────────────────────────

def test_rel_volume_with_baseline():
    assert compute_rel_volume([0, 0, 500], baseline_avg=100.0) == 5.0


def test_rel_volume_without_baseline_uses_prior_mean():
    # prior mean = 100; last = 300 → 3.0
    assert compute_rel_volume([100, 100, 100, 300]) == 3.0


# ── Top-level compute_features ────────────────────────────────────────────────

def _bars_steady_climb() -> list[Bar]:
    """7 bars climbing 100 → 106, each with 1000 volume."""
    return [
        Bar(open=100 + i, high=100 + i + 0.5, low=100 + i - 0.3,
            close=100 + i + 1, volume=1000)
        for i in range(7)
    ]


def test_compute_features_basic_climb():
    bars = _bars_steady_climb()
    snap = compute_features(
        bars,
        gradient=0.5,
        refs=RefGradients(spy=0.1, qqq=0.15, sector=0.2),
        bid=106.5,
        ask=106.7,
        pmh=105.0,
        pml=99.0,
    )
    assert snap.bars_count == 7
    assert snap.gradient == 0.5
    assert snap.roc_1m > 0
    assert snap.roc_3m > snap.roc_1m   # bigger move over longer window
    assert snap.vwap > 0
    assert snap.vwap_state in ("above", "near", "below")
    assert snap.rel_strength_spy == pytest.approx(0.4, abs=1e-3)
    assert snap.rel_strength_qqq == pytest.approx(0.35, abs=1e-3)
    assert snap.rel_strength_sector == pytest.approx(0.3, abs=1e-3)
    # spread = (ask-bid)/mid*100 = 0.2/106.6*100 ≈ 0.188
    assert snap.spread_pct == pytest.approx(0.188, abs=1e-2)
    # last close = 106+1 = 107, above pmh=105
    assert snap.broke_pmh is True
    assert snap.broke_pml is False


def test_compute_features_no_bars_returns_empty():
    snap = compute_features([])
    assert snap.bars_count == 0
    assert snap.gradient == 0.0


def test_compute_features_no_refs_defaults_to_zero():
    bars = _bars_steady_climb()
    snap = compute_features(bars, gradient=0.5)
    # rel_strength = gradient - 0 = gradient
    assert snap.rel_strength_spy == 0.5
    assert snap.rel_strength_qqq == 0.5
    assert snap.rel_strength_sector == 0.5


def test_compute_features_spread_zero_when_missing_quote():
    bars = _bars_steady_climb()
    snap = compute_features(bars, gradient=0.5)
    assert snap.spread_pct == 0.0
    assert snap.bid == 0.0
    assert snap.ask == 0.0
