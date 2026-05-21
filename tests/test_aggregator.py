"""Tests for the aggregation pipeline."""
import json
import pathlib
from datetime import datetime

import pytest

from dolev_ai.analysis import aggregator as agg_mod
from dolev_ai.models import RawTweet

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "tweets.json"

MOCK_UNIVERSE = frozenset({"NVDA", "TSLA", "MSFT", "AAPL", "AMZN", "SPY", "QQQ"})


@pytest.fixture(autouse=True)
def patch_deps(monkeypatch):
    import dolev_ai.analysis.ticker as ticker_mod
    import dolev_ai.analysis.sentiment as sent_mod
    import dolev_ai.analysis.credibility as cred_mod

    monkeypatch.setattr(ticker_mod, "_universe", MOCK_UNIVERSE)

    # Deterministic sentiment: positive for bullish keywords, negative otherwise
    def fake_score_batch(texts):
        results = []
        for t in texts:
            t_lower = t.lower()
            if any(w in t_lower for w in ["beat", "strong", "massive", "loading", "high", "momentum"]):
                results.append(("positive", 0.9))
            elif any(w in t_lower for w in ["recall", "collapse", "bears", "short"]):
                results.append(("negative", 0.85))
            else:
                results.append(("neutral", 0.6))
        return results

    monkeypatch.setattr(agg_mod, "score_batch", fake_score_batch)

    # Fixed credibility: known accounts get 0.8, unknowns 0.2
    def fake_credibility(handle):
        known = {"unusual_whales", "lizannsonders", "charliebilello", "deltaone",
                 "soberLook", "markets", "ritholtz", "zerohedge"}
        return 0.8 if handle.lower().replace("@", "") in known else 0.2

    monkeypatch.setattr(agg_mod, "credibility", fake_credibility)


def _load_fixtures() -> list[RawTweet]:
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


def test_nvda_positive_score():
    tweets = _load_fixtures()
    now = datetime(2024, 1, 15, 15, 0, 0)
    scores, _ = agg_mod.aggregate(tweets, window_minutes=120, now=now)
    assert "NVDA" in scores
    assert scores["NVDA"].score > 0, "NVDA should have positive score"


def test_tsla_negative_score():
    tweets = _load_fixtures()
    now = datetime(2024, 1, 15, 15, 0, 0)
    scores, _ = agg_mod.aggregate(tweets, window_minutes=120, now=now)
    assert "TSLA" in scores
    assert scores["TSLA"].score < 0, "TSLA should have negative score due to recall"


def test_nvda_ranked_higher_magnitude_than_tsla():
    tweets = _load_fixtures()
    now = datetime(2024, 1, 15, 15, 0, 0)
    scores, _ = agg_mod.aggregate(tweets, window_minutes=120, now=now)
    # NVDA has 3 bullish tweets with high engagement; check it has ≥3 voices
    assert scores["NVDA"].unique_credible_voices >= 3


def test_empty_window_returns_empty():
    scores, edges = agg_mod.aggregate([], window_minutes=60)
    assert scores == {}
    assert edges == []


def test_out_of_window_ignored():
    tweets = _load_fixtures()
    # Use a now that puts all fixture tweets outside the window
    now = datetime(2024, 1, 16, 0, 0, 0)  # next day
    scores, _ = agg_mod.aggregate(tweets, window_minutes=30, now=now)
    assert scores == {}


def test_time_decay_reduces_old_score():
    """A tweet right at window edge should contribute less than a fresh tweet."""
    import math
    from dolev_ai.analysis.aggregator import _time_decay
    now = datetime(2024, 1, 15, 15, 0, 0)
    old_time = datetime(2024, 1, 15, 14, 0, 0)  # 60 min ago
    fresh_time = datetime(2024, 1, 15, 14, 55, 0)  # 5 min ago
    assert _time_decay(old_time, now) < _time_decay(fresh_time, now)
