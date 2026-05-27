"""Tests for ConfidenceScorer."""
from __future__ import annotations

import pytest

from dolev_ai.strategies.confidence import (
    DEFAULT_WEIGHTS,
    ConfidenceResult,
    ConfidenceScorer,
)
from dolev_ai.strategies.features import FeatureSnapshot


def _features(**overrides) -> FeatureSnapshot:
    base = FeatureSnapshot(
        gradient=0.0, roc_1m=0.0, roc_3m=0.0, roc_5m=0.0,
        acceleration=0.0, rel_volume=1.0, breakout_volume_ratio=1.0,
        vwap=100.0, vwap_state="near", extension_pct=0.0,
        broke_pmh=False, broke_pml=False,
        rel_strength_spy=0.0, rel_strength_qqq=0.0, rel_strength_sector=0.0,
        bid=100.0, ask=100.05, spread_pct=0.05,
        bars_count=7,
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


# ── Range + neutrality ───────────────────────────────────────────────────────

def test_neutral_features_near_50():
    """All-zero directional features should score within a few points of the 50 anchor."""
    scorer = ConfidenceScorer()
    res = scorer.score(_features(), side="buy")
    # Small positive (rel_volume=1.0 adds a touch) minus small spread penalty
    assert 47 <= res.score <= 53


def test_score_within_0_100_bounds():
    scorer = ConfidenceScorer()
    # Extreme bullish features
    res = scorer.score(
        _features(
            gradient=10.0,        # way past cap
            acceleration=10.0,
            roc_3m=10.0,
            rel_strength_spy=5.0,
            rel_strength_sector=5.0,
            vwap_state="above",
            broke_pmh=True,
            rel_volume=20.0,
            extension_pct=0.0,
        ),
        side="buy",
    )
    assert res.score <= 100.0
    assert res.score >= 50.0  # clearly bullish

    # Extreme bearish features for buy direction
    res2 = scorer.score(
        _features(
            gradient=-10.0,
            acceleration=-10.0,
            roc_3m=-10.0,
            rel_strength_spy=-5.0,
            rel_strength_sector=-5.0,
            vwap_state="below",
            broke_pml=True,
            extension_pct=10.0,  # very extended
            spread_pct=2.0,      # wide
        ),
        side="buy",
    )
    assert res2.score >= 0.0


# ── Direction inversion ──────────────────────────────────────────────────────

def test_sell_flips_directional_features():
    """A negative gradient should be bullish for a sell signal."""
    scorer = ConfidenceScorer()
    bear = _features(
        gradient=-0.8, acceleration=-0.3, roc_3m=-2.0,
        rel_strength_spy=-0.5, rel_strength_sector=-0.5,
        vwap_state="below", broke_pml=True,
    )
    # As a sell signal these features support the trade → high confidence
    sell = scorer.score(bear, side="sell")
    # As a buy signal these are anti-features → low confidence
    buy = scorer.score(bear, side="buy")
    assert sell.score > buy.score
    assert sell.score > 60


def test_vwap_above_helps_long_hurts_short():
    scorer = ConfidenceScorer()
    f = _features(vwap_state="above")
    buy = scorer.score(f, side="buy")
    sell = scorer.score(f, side="sell")
    assert buy.score > sell.score


def test_broke_pmh_helps_long_not_short():
    scorer = ConfidenceScorer()
    f = _features(broke_pmh=True)
    buy = scorer.score(f, side="buy")
    sell = scorer.score(f, side="sell")
    assert buy.score > sell.score


# ── Monotonicity ─────────────────────────────────────────────────────────────

def test_higher_gradient_higher_score():
    scorer = ConfidenceScorer()
    weak = scorer.score(_features(gradient=0.2), side="buy")
    strong = scorer.score(_features(gradient=0.8), side="buy")
    assert strong.score > weak.score


def test_extension_penalty_reduces_score():
    scorer = ConfidenceScorer()
    normal = scorer.score(_features(gradient=0.5, extension_pct=1.0), side="buy")
    overext = scorer.score(_features(gradient=0.5, extension_pct=8.0), side="buy")
    assert normal.score > overext.score


def test_wide_spread_penalty_reduces_score():
    scorer = ConfidenceScorer()
    tight = scorer.score(_features(gradient=0.5, spread_pct=0.02), side="buy")
    wide = scorer.score(_features(gradient=0.5, spread_pct=1.5), side="buy")
    assert tight.score > wide.score


# ── Components and explanation ───────────────────────────────────────────────

def test_components_populated_for_every_feature():
    scorer = ConfidenceScorer()
    res = scorer.score(_features(gradient=0.5, vwap_state="above"), side="buy")
    # Every default-weight key should appear
    for k in DEFAULT_WEIGHTS:
        assert k in res.components


def test_explanation_highlights_top_contributors():
    scorer = ConfidenceScorer()
    res = scorer.score(
        _features(gradient=0.8, vwap_state="above", broke_pmh=True),
        side="buy",
    )
    # gradient should be the heaviest contributor
    assert "gradient" in res.explanation


# ── Custom weights override ──────────────────────────────────────────────────

def test_custom_weights_override_defaults():
    scorer = ConfidenceScorer(weights={"gradient": 50.0})
    res = scorer.score(_features(gradient=1.0), side="buy")
    # gradient component should reflect the higher weight
    assert res.components["gradient"] == pytest.approx(50.0, abs=0.1)
