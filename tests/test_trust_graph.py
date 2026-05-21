"""Tests for TrustGraphStrategy."""
from datetime import datetime, timedelta

import pytest

from dolev_ai.models import TickerScore
from dolev_ai.strategies.trust_graph import TrustGraphStrategy


def _make_score(ticker: str, score: float, voices: int = 3) -> TickerScore:
    now = datetime.utcnow()
    return TickerScore(
        ticker=ticker,
        score=score,
        unique_credible_voices=voices,
        tweet_count=voices * 2,
        window_start=now - timedelta(hours=1),
        window_end=now,
        top_tweet_urls=[f"https://x.com/test/status/{i}" for i in range(voices)],
    )


def _strategy(**kwargs) -> TrustGraphStrategy:
    return TrustGraphStrategy(
        score_threshold=5.0,
        min_credible_voices=3,
        cooldown_hours=4.0,
        cooldown_override_multiplier=2.0,
        **kwargs,
    )


def test_no_signal_below_threshold():
    strat = _strategy()
    signals = strat.evaluate({"NVDA": _make_score("NVDA", 3.0)})
    assert signals == []


def test_signal_at_threshold():
    strat = _strategy()
    signals = strat.evaluate({"NVDA": _make_score("NVDA", 5.5)})
    assert len(signals) == 1
    assert signals[0].ticker == "NVDA"
    assert signals[0].side == "buy"


def test_sell_signal_on_negative_score():
    strat = _strategy()
    signals = strat.evaluate({"TSLA": _make_score("TSLA", -7.0)})
    assert len(signals) == 1
    assert signals[0].side == "sell"


def test_no_signal_insufficient_voices():
    strat = _strategy()
    signals = strat.evaluate({"NVDA": _make_score("NVDA", 10.0, voices=2)})
    assert signals == []


def test_cooldown_suppresses_signal():
    recent = datetime.utcnow() - timedelta(hours=1)  # 1h ago, within 4h cooldown
    strat = _strategy(last_alert_time_fn=lambda t: recent, last_alert_score_fn=lambda t: 5.5)
    signals = strat.evaluate({"NVDA": _make_score("NVDA", 6.0)})
    assert signals == [], "Should be suppressed by cooldown"


def test_cooldown_bypassed_when_score_doubles():
    recent = datetime.utcnow() - timedelta(hours=1)
    # last alert was at 5.5; new score is 11.5 > 5.5 * 2
    strat = _strategy(last_alert_time_fn=lambda t: recent, last_alert_score_fn=lambda t: 5.5)
    signals = strat.evaluate({"NVDA": _make_score("NVDA", 11.5)})
    assert len(signals) == 1, "Cooldown should be bypassed when score doubles"


def test_no_cooldown_when_outside_window():
    old = datetime.utcnow() - timedelta(hours=5)  # outside 4h cooldown
    strat = _strategy(last_alert_time_fn=lambda t: old, last_alert_score_fn=lambda t: 5.5)
    signals = strat.evaluate({"NVDA": _make_score("NVDA", 6.0)})
    assert len(signals) == 1


def test_conviction_range():
    strat = _strategy()
    signals = strat.evaluate({"NVDA": _make_score("NVDA", 8.0)})
    assert 0 <= signals[0].conviction <= 1


def test_multiple_tickers():
    strat = _strategy()
    scores = {
        "NVDA": _make_score("NVDA", 7.0),
        "TSLA": _make_score("TSLA", -6.0),
        "AAPL": _make_score("AAPL", 2.0),  # below threshold
    }
    signals = strat.evaluate(scores)
    tickers = {s.ticker for s in signals}
    assert "NVDA" in tickers
    assert "TSLA" in tickers
    assert "AAPL" not in tickers
