"""Pydantic response schemas for the REST/WS API."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class TickerScoreOut(BaseModel):
    ticker: str
    score: float
    unique_credible_voices: int
    tweet_count: int
    window_start: datetime
    window_end: datetime
    top_tweet_urls: list[str]
    threshold_progress: float


class SignalOut(BaseModel):
    ticker: str
    side: Literal["buy", "sell"]
    conviction: float
    suggested_size_pct: float
    rationale: str
    key_drivers: list[str]
    generated_at: datetime


class AccountOut(BaseModel):
    handle: str
    tier: int
    credibility: float


class GraphNodeOut(BaseModel):
    id: str
    type: Literal["account", "ticker"]
    label: str
    size: float        # credibility for accounts, abs(score) for tickers
    sentiment: str     # "positive" | "negative" | "neutral" (for tickers)
    tier: int          # 0 for tickers


class GraphEdgeOut(BaseModel):
    source: str   # account handle
    target: str   # ticker symbol
    weight: float
    sentiment: str


class GraphSnapshotOut(BaseModel):
    nodes: list[GraphNodeOut]
    edges: list[GraphEdgeOut]


class HealthOut(BaseModel):
    ok: bool
    started_at: datetime
    threshold: float
    subscriber_count: int


class DrilldownTweetOut(BaseModel):
    id: str
    author: str
    text: str
    created_at: datetime
    like_count: int
    retweet_count: int
    url: str


class DrilldownAccountContrib(BaseModel):
    handle: str
    tier: int
    credibility: float
    tweet_count: int


class TickerDrilldownOut(BaseModel):
    ticker: str
    current_score: float
    threshold_progress: float
    unique_credible_voices: int
    score_history: list[tuple[datetime, float]]   # (timestamp, score) pairs
    contributing_accounts: list[DrilldownAccountContrib]
    recent_tweets: list[DrilldownTweetOut]


class SnapshotEvent(BaseModel):
    type: Literal["snapshot"] = "snapshot"
    top_tickers: list[TickerScoreOut]
    recent_signals: list[SignalOut]
    graph: GraphSnapshotOut
