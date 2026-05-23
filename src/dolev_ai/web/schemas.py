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
    direct_score: float = 0.0
    cascade_score: float = 0.0
    confirmation_factor: float = 1.0
    movement_pct: float | None = None


class MoverOut(BaseModel):
    ticker: str
    pct_change: float
    last_price: float
    rel_volume: float
    market_cap: float = 0.0
    rank: int = 0
    side: Literal["gainer", "loser"]
    captured_at: datetime


class MoversSnapshotOut(BaseModel):
    gainers: list[MoverOut]
    losers: list[MoverOut]
    captured_at: datetime | None = None


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
    type: Literal["account", "ticker", "theme"]
    label: str
    size: float        # credibility for accounts, abs(score) for tickers/themes
    sentiment: str     # "positive" | "negative" | "neutral"
    tier: int          # 0 for tickers/themes


class GraphEdgeOut(BaseModel):
    source: str
    target: str
    weight: float
    sentiment: str
    edge_type: str = "acct_ticker"
    tweet_id: str | None = None
    id: int | None = None


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
    sentiment: str | None = None       # "positive" | "negative" | "neutral" for the drilldown ticker
    confidence: float | None = None    # 0.0 – 1.0 LLM confidence
    explicit: bool | None = None       # ticker mentioned literally vs inferred


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
    movement_pct: float | None = None
    movement_rel_volume: float | None = None
    movement_last_price: float | None = None
    confirmation_factor: float = 1.0


class SnapshotEvent(BaseModel):
    type: Literal["snapshot"] = "snapshot"
    top_tickers: list[TickerScoreOut]
    recent_signals: list[SignalOut]
    graph: GraphSnapshotOut


# ── Extraction / theme / LLM schemas ─────────────────────────────────────

class TickerMentionOut(BaseModel):
    ticker: str
    sentiment: str
    confidence: float
    explicit: bool = False


class ThemeMentionOut(BaseModel):
    theme: str
    sentiment: str
    confidence: float


class ExtractionOut(BaseModel):
    id: int
    tweet_id: str
    author: str
    text: str
    url: str
    is_finance: bool
    overall_sentiment: str
    summary: str
    tickers: list[TickerMentionOut]
    themes: list[ThemeMentionOut]
    model: str
    latency_ms: int
    created_at: datetime


class ThemeScoreOut(BaseModel):
    theme: str
    score: float
    voices: int
    tweet_count: int
    threshold_progress: float
    cascade_targets: list[str] = []   # tickers this theme cascades to


class EdgeDetailOut(BaseModel):
    id: int
    from_id: str
    to_id: str
    edge_type: str
    weight: float
    sentiment: str
    tweet_id: str | None
    created_at: datetime
    # If tweet_id present, full tweet
    tweet: DrilldownTweetOut | None = None


class LLMStatusOut(BaseModel):
    provider: str
    model: str
    endpoint: str
    healthy: bool
    backlog: int
    capacity: int
    running: bool
    calls_total: int = 0
    calls_finance: int = 0
    calls_errors: int = 0
    avg_latency_ms: int = 0
    last_call_at: float | None = None
    dropped_total: int = 0


class DBStatsOut(BaseModel):
    tweets: int
    extractions: int
    extractions_finance: int
    tickers: int
    themes: int
    edges: int
    signals: int
    last_tweet_at: datetime | None = None
    last_extraction_at: datetime | None = None


class DBTableRowOut(BaseModel):
    """Generic row view for the DB browser."""
    id: int | str
    fields: dict


class PaperPositionOut(BaseModel):
    id: int
    ticker: str
    side: str
    entry_price: float
    opened_at: datetime
    exit_price: float | None = None
    closed_at: datetime | None = None
    pnl_pct: float | None = None
    status: str
    retrospective: str | None = None
    live_price: float | None = None
    unrealized_pnl_pct: float | None = None


class SignalApprovalOut(BaseModel):
    id: int
    signal_id: int
    kind: str
    position_id: int | None = None
    telegram_message_id: int | None = None
    status: str
    prompted_at: datetime
    responded_at: datetime | None = None
    ticker: str = ""
    side: str = ""

