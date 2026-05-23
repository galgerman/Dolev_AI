"""Tests for the TradingView source + aggregator confirmation logic."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from dolev_ai.analysis.aggregator import _confirmation_factor, aggregate
from dolev_ai.analysis.aggregator import ExtractionRecord
from dolev_ai.models import MovementSnapshot, RawTweet
from dolev_ai.sources.tradingview import TradingViewSource


# ── _confirmation_factor unit tests ────────────────────────────────────────

def test_confirmation_factor_aligned_boost():
    # Twitter +5, price +5% → boost up to 1 + 0.5 * 1.0 = 1.5
    f = _confirmation_factor(5.0, 5.0, confirmation_weight=0.5, strong_move_pct=5.0)
    assert f == pytest.approx(1.5)


def test_confirmation_factor_contradiction_cut():
    # Twitter +5, price -5% → cut to 1 - 0.5 * 1.0 = 0.5
    f = _confirmation_factor(5.0, -5.0, confirmation_weight=0.5, strong_move_pct=5.0)
    assert f == pytest.approx(0.5)


def test_confirmation_factor_partial_intensity():
    # Twitter +5, price +2.5% → 50% saturation: 1 + 0.5 * 0.5 = 1.25
    f = _confirmation_factor(5.0, 2.5, confirmation_weight=0.5, strong_move_pct=5.0)
    assert f == pytest.approx(1.25)


def test_confirmation_factor_no_movement():
    f = _confirmation_factor(5.0, 0.0, confirmation_weight=0.5, strong_move_pct=5.0)
    assert f == 1.0


def test_confirmation_factor_neutral_twitter():
    f = _confirmation_factor(0.0, 5.0, confirmation_weight=0.5, strong_move_pct=5.0)
    assert f == 1.0


# ── Aggregator integration ───────────────────────────────────────────────────

def _record_with_bull_ticker(ticker: str, author: str = "elonmusk") -> ExtractionRecord:
    now = datetime.utcnow()
    return ExtractionRecord(
        tweet=RawTweet(
            id="t1", author=author, text=f"${ticker} ripping", created_at=now,
            like_count=100, retweet_count=10, reply_count=0,
            url=f"https://x.com/{author}/status/t1",
        ),
        is_finance=True,
        overall_sentiment="positive",
        tickers=[(ticker, "positive", 0.9, True)],
        themes=[],
    )


def test_aggregate_applies_confirmation_boost():
    records = [_record_with_bull_ticker("XLE")]
    mv = {"XLE": MovementSnapshot(
        ticker="XLE", pct_change=5.0, last_price=90.0, rel_volume=2.0,
        captured_at=datetime.utcnow(),
    )}
    ts_no_mv, _, _ = aggregate(records)
    ts_with, _, _ = aggregate(records, latest_movements=mv,
                              confirmation_weight=0.5, strong_move_pct=5.0)
    assert ts_with["XLE"].score == pytest.approx(ts_no_mv["XLE"].score * 1.5, rel=1e-3)
    assert ts_with["XLE"].confirmation_factor == pytest.approx(1.5)
    assert ts_with["XLE"].movement_pct == 5.0


def test_aggregate_applies_contradiction_cut():
    records = [_record_with_bull_ticker("XLE")]
    mv = {"XLE": MovementSnapshot(
        ticker="XLE", pct_change=-5.0, last_price=90.0, rel_volume=2.0,
        captured_at=datetime.utcnow(),
    )}
    ts_with, _, _ = aggregate(records, latest_movements=mv,
                              confirmation_weight=0.5, strong_move_pct=5.0)
    assert ts_with["XLE"].confirmation_factor == pytest.approx(0.5)


def test_aggregate_discovery_only_mover():
    """Movers with no Twitter data should appear as discovery-only entries."""
    mv = {"NVDA": MovementSnapshot(
        ticker="NVDA", pct_change=10.0, last_price=900.0, rel_volume=3.0,
        captured_at=datetime.utcnow(),
    )}
    ts, _, _ = aggregate([], latest_movements=mv, discovery_weight=0.5)
    assert "NVDA" in ts
    assert ts["NVDA"].unique_credible_voices == 0
    assert ts["NVDA"].direct_score == 0.0
    assert ts["NVDA"].movement_pct == 10.0
    assert ts["NVDA"].score == pytest.approx(5.0)   # 10.0 * 0.5


# ── TradingViewSource API parsing ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_source_parses_movers():
    fake_response = {
        "totalCount": 2,
        "data": [
            {"s": "NASDAQ:NVDA", "d": ["NVDA", 900.0, 5.5, 47.0, 1000000, 2.1, 2.2e12]},
            {"s": "NYSE:XLE", "d": ["XLE", 90.0, 3.2, 2.8, 500000, 1.5, 5e10]},
        ],
    }
    src = TradingViewSource()
    src._client = AsyncMock()
    src._client.post = AsyncMock(return_value=AsyncMock(
        raise_for_status=lambda: None,
        json=lambda: fake_response,
    ))
    movers = await src._fetch_side("gainer")
    assert len(movers) == 2
    assert movers[0].ticker == "NVDA"
    assert movers[0].pct_change == pytest.approx(5.5)
    assert movers[0].rel_volume == pytest.approx(2.1)
    assert movers[0].rank == 0
    assert movers[1].ticker == "XLE"
    assert movers[1].rank == 1


@pytest.mark.asyncio
async def test_source_handles_http_failure():
    src = TradingViewSource()
    src._client = AsyncMock()
    src._client.post = AsyncMock(side_effect=Exception("network down"))
    movers = await src._fetch_side("gainer")
    assert movers == []
