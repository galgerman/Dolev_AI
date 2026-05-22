"""Tests for the new extraction-based aggregator with theme cascade."""
import json
import pathlib
from datetime import datetime

import pytest

from dolev_ai.analysis import aggregator as agg_mod
from dolev_ai.analysis.aggregator import ExtractionRecord, _time_decay, aggregate
from dolev_ai.models import RawTweet

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "tweets.json"


def _load_raw_tweets() -> list[RawTweet]:
    data = json.loads(FIXTURES.read_text())
    return [
        RawTweet(
            id=t["id"], author=t["author"], text=t["text"],
            created_at=datetime.fromisoformat(t["created_at"]),
            like_count=t["like_count"], retweet_count=t["retweet_count"],
            reply_count=t["reply_count"], url=t["url"],
        )
        for t in data
    ]


def _make_record(tweet, tickers=(), themes=(), sentiment="neutral"):
    return ExtractionRecord(
        tweet=tweet,
        is_finance=True,
        overall_sentiment=sentiment,
        tickers=[(t, sent, conf, True) for t, sent, conf in tickers],
        themes=[(name, sent, conf) for name, sent, conf in themes],
    )


@pytest.fixture(autouse=True)
def patch_credibility(monkeypatch):
    """Known accounts get 0.8, unknowns 0.2 (matches the old test contract)."""
    def fake_credibility(handle):
        known = {"unusual_whales", "lizannsonders", "charliebilello", "deltaone",
                 "soberLook", "markets", "ritholtz", "zerohedge"}
        return 0.8 if handle.lower().replace("@", "") in known else 0.2
    monkeypatch.setattr(agg_mod, "credibility", fake_credibility)


def test_nvda_positive_score():
    tweets = _load_raw_tweets()
    nvda_records = [
        _make_record(t, tickers=[("NVDA", "positive", 0.9)])
        for t in tweets if "NVDA" in t.text.upper()
    ]
    now = datetime(2024, 1, 15, 15, 0, 0)
    ticker_scores, _, _ = aggregate(nvda_records, window_minutes=120, now=now)
    assert "NVDA" in ticker_scores
    assert ticker_scores["NVDA"].score > 0


def test_tsla_negative_score():
    tweets = _load_raw_tweets()
    tsla_records = [
        _make_record(t, tickers=[("TSLA", "negative", 0.85)])
        for t in tweets if "TSLA" in t.text.upper()
    ]
    now = datetime(2024, 1, 15, 15, 0, 0)
    ticker_scores, _, _ = aggregate(tsla_records, window_minutes=120, now=now)
    assert "TSLA" in ticker_scores
    assert ticker_scores["TSLA"].score < 0


def test_empty_window_returns_empty():
    ticker_scores, theme_scores, edges = aggregate([], window_minutes=60)
    assert ticker_scores == {}
    assert theme_scores == {}
    assert edges == []


def test_out_of_window_ignored():
    tweets = _load_raw_tweets()
    records = [_make_record(t, tickers=[("NVDA", "positive", 0.9)]) for t in tweets]
    now = datetime(2024, 1, 16, 0, 0, 0)  # next day
    ticker_scores, _, _ = aggregate(records, window_minutes=30, now=now)
    assert ticker_scores == {}


def test_time_decay_reduces_old_score():
    now = datetime(2024, 1, 15, 15, 0, 0)
    old_time = datetime(2024, 1, 15, 14, 0, 0)
    fresh_time = datetime(2024, 1, 15, 14, 55, 0)
    assert _time_decay(old_time, now) < _time_decay(fresh_time, now)


def test_theme_cascade_boosts_associated_tickers(monkeypatch):
    """A bullish ai_infrastructure theme should boost NVDA (a heavily-weighted associated ticker)."""
    # Provide a minimal themes config: ai_infrastructure → NVDA at weight 1.0
    monkeypatch.setattr(agg_mod, "_themes_config",
                        lambda: {"ai_infrastructure": {"tickers": {"NVDA": 1.0}}})

    tweet = RawTweet(
        id="t1", author="unusual_whales", text="AI chip demand is going parabolic",
        created_at=datetime(2024, 1, 15, 14, 58, 0),
        like_count=100, retweet_count=50, reply_count=10,
        url="https://x.com/test/status/1",
    )
    records = [_make_record(tweet, themes=[("ai_infrastructure", "positive", 0.9)])]
    now = datetime(2024, 1, 15, 15, 0, 0)
    ticker_scores, theme_scores, edges = aggregate(records, window_minutes=60, threshold=5.0, now=now)

    assert "ai_infrastructure" in theme_scores
    assert theme_scores["ai_infrastructure"].score > 0
    # NVDA should now appear via cascade only (no direct mention)
    assert "NVDA" in ticker_scores
    assert ticker_scores["NVDA"].direct_score == 0
    assert ticker_scores["NVDA"].cascade_score > 0
    # Cascade factor = 0.5 → boost = theme_score × 1.0 × 0.5
    expected_cascade = theme_scores["ai_infrastructure"].score * 1.0 * 0.5
    assert abs(ticker_scores["NVDA"].cascade_score - expected_cascade) < 0.01
    # Edges should include both acct→theme and theme→ticker
    edge_types = {e.edge_type for e in edges}
    assert "acct_theme" in edge_types
    assert "theme_ticker" in edge_types


def test_direct_plus_cascade_combined(monkeypatch):
    """Direct ticker mention + a related theme: final = direct + cascade."""
    monkeypatch.setattr(agg_mod, "_themes_config",
                        lambda: {"ai_infrastructure": {"tickers": {"NVDA": 1.0}}})

    tweet = RawTweet(
        id="t1", author="lizannsonders", text="$NVDA - chip demand is going parabolic",
        created_at=datetime(2024, 1, 15, 14, 58, 0),
        like_count=100, retweet_count=50, reply_count=10,
        url="https://x.com/test/status/1",
    )
    records = [_make_record(
        tweet,
        tickers=[("NVDA", "positive", 0.9)],
        themes=[("ai_infrastructure", "positive", 0.9)],
    )]
    now = datetime(2024, 1, 15, 15, 0, 0)
    ticker_scores, _, _ = aggregate(records, window_minutes=60, threshold=5.0, now=now)

    nvda = ticker_scores["NVDA"]
    assert nvda.direct_score > 0
    assert nvda.cascade_score > 0
    assert abs(nvda.score - (nvda.direct_score + nvda.cascade_score)) < 1e-6
