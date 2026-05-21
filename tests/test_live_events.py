"""Tests for live collection event helpers."""
from datetime import datetime

from dolev_ai.live_events import (
    build_graph_edge_events,
    build_ticker_discovered_events,
    build_tweet_ingested_events,
)
from dolev_ai.models import RawTweet


def _tweet(id_: str, author: str, text: str) -> RawTweet:
    return RawTweet(
        id=id_,
        author=author,
        text=text,
        created_at=datetime(2026, 5, 21, 12, 0, 0),
        like_count=2,
        retweet_count=1,
        reply_count=0,
        url=f"https://x.com/{author}/status/{id_}",
    )


def test_build_tweet_ingested_events_extracts_tickers():
    events = build_tweet_ingested_events([
        _tweet("1", "DeItaone", "$NVDA and $TSLA moving"),
        _tweet("2", "Reuters", "No cashtags here"),
    ])

    assert events[0]["type"] == "tweet.ingested"
    assert events[0]["author"] == "DeItaone"
    assert events[0]["tickers"] == ["NVDA", "TSLA"]
    assert events[0]["sentiment"] == "pending"
    assert events[1]["tickers"] == []


def test_build_graph_edge_events_deduplicates_author_ticker_pairs():
    events = build_graph_edge_events([
        _tweet("1", "DeItaone", "$NVDA first mention"),
        _tweet("2", "DeItaone", "$NVDA second mention"),
        _tweet("3", "Reuters", "$NVDA confirms"),
    ])

    assert events == [
        {"type": "graph.edge_added", "author": "deitaone", "ticker": "NVDA", "weight": 1.0, "sentiment": "neutral"},
        {"type": "graph.edge_added", "author": "reuters", "ticker": "NVDA", "weight": 1.0, "sentiment": "neutral"},
    ]


def test_build_ticker_discovered_events_counts_tweets_and_voices():
    events = build_ticker_discovered_events([
        _tweet("1", "DeItaone", "$NVDA first mention"),
        _tweet("2", "DeItaone", "$NVDA second mention"),
        _tweet("3", "Reuters", "$NVDA and $TSLA"),
    ])

    assert events == [
        {"type": "ticker.discovered", "ticker": "NVDA", "voices": 2, "tweet_count": 3},
        {"type": "ticker.discovered", "ticker": "TSLA", "voices": 1, "tweet_count": 1},
    ]
