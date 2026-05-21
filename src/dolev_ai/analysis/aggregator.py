"""Per-ticker score aggregation with time decay."""
from __future__ import annotations

import math
from datetime import datetime, timedelta

from dolev_ai.analysis.credibility import credibility
from dolev_ai.analysis.sentiment import score_batch
from dolev_ai.analysis.ticker import extract_tickers
from dolev_ai.models import RawTweet, TickerScore

_SENTIMENT_SIGN = {"positive": 1.0, "negative": -1.0, "neutral": 0.0}
_HALF_LIFE_MINUTES = 30.0  # score halves every 30 min


def _time_decay(tweet_time: datetime, now: datetime) -> float:
    delta_minutes = max(0.0, (now - tweet_time).total_seconds() / 60.0)
    return math.exp(-delta_minutes * math.log(2) / _HALF_LIFE_MINUTES)


def _engagement_weight(tweet: RawTweet) -> float:
    return math.log1p(tweet.like_count + 2 * tweet.retweet_count)


def aggregate(
    tweets: list[RawTweet],
    window_minutes: int = 60,
    now: datetime | None = None,
) -> dict[str, TickerScore]:
    """Compute per-ticker scores from a list of tweets.

    Returns a dict keyed by ticker symbol.
    """
    if now is None:
        now = datetime.utcnow()
    window_start = now - timedelta(minutes=window_minutes)

    # Filter to window
    in_window = [t for t in tweets if t.created_at >= window_start]
    if not in_window:
        return {}

    # Batch sentiment
    texts = [t.text for t in in_window]
    sentiments = score_batch(texts)

    # Per-ticker accumulation
    scores: dict[str, float] = {}
    voice_sets: dict[str, set[str]] = {}   # ticker → set of author handles
    tweet_counts: dict[str, int] = {}
    top_tweets: dict[str, list[tuple[float, str]]] = {}  # (contrib, url)

    for tweet, (label, confidence) in zip(in_window, sentiments):
        sign = _SENTIMENT_SIGN[label]
        if sign == 0.0:
            continue
        cred = credibility(tweet.author)
        decay = _time_decay(tweet.created_at, now)
        eng = _engagement_weight(tweet)
        contrib = sign * confidence * cred * eng * decay

        tickers = extract_tickers(tweet.text)
        for ticker in tickers:
            scores[ticker] = scores.get(ticker, 0.0) + contrib
            voice_sets.setdefault(ticker, set()).add(tweet.author.lower())
            tweet_counts[ticker] = tweet_counts.get(ticker, 0) + 1
            top_tweets.setdefault(ticker, []).append((abs(contrib), tweet.url))

    result: dict[str, TickerScore] = {}
    for ticker, score in scores.items():
        sorted_urls = [url for _, url in sorted(top_tweets[ticker], reverse=True)]
        result[ticker] = TickerScore(
            ticker=ticker,
            score=round(score, 4),
            unique_credible_voices=len(voice_sets[ticker]),
            tweet_count=tweet_counts[ticker],
            window_start=window_start,
            window_end=now,
            top_tweet_urls=sorted_urls[:10],
        )

    return result
