"""Helpers for provisional live dashboard events during collection."""
from __future__ import annotations

from collections import defaultdict

from dolev_ai.analysis.ticker import extract_tickers
from dolev_ai.models import RawTweet


def build_tweet_ingested_events(tweets: list[RawTweet]) -> list[dict]:
    return [
        {
            "type": "tweet.ingested",
            "id": tweet.id,
            "author": tweet.author,
            "text": tweet.text,
            "created_at": tweet.created_at.isoformat(),
            "url": tweet.url,
            "tickers": extract_tickers(tweet.text),
            "sentiment": "pending",
            "sentiment_score": 0.0,
        }
        for tweet in tweets
    ]


def build_graph_edge_events(tweets: list[RawTweet]) -> list[dict]:
    seen: set[tuple[str, str]] = set()
    events: list[dict] = []
    for tweet in tweets:
        author = tweet.author.lower()
        for ticker in extract_tickers(tweet.text):
            key = (author, ticker)
            if key in seen:
                continue
            seen.add(key)
            events.append({
                "type": "graph.edge_added",
                "author": author,
                "ticker": ticker,
                "weight": 1.0,
                "sentiment": "neutral",
            })
    return events


def build_ticker_discovered_events(tweets: list[RawTweet]) -> list[dict]:
    tweet_counts: dict[str, int] = defaultdict(int)
    voices: dict[str, set[str]] = defaultdict(set)
    for tweet in tweets:
        tickers = set(extract_tickers(tweet.text))
        for ticker in tickers:
            tweet_counts[ticker] += 1
            voices[ticker].add(tweet.author.lower())

    return [
        {
            "type": "ticker.discovered",
            "ticker": ticker,
            "voices": len(voices[ticker]),
            "tweet_count": tweet_counts[ticker],
        }
        for ticker in sorted(tweet_counts)
    ]
