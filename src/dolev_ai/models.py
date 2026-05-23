from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


@dataclass
class RawTweet:
    id: str
    author: str          # handle without @
    text: str
    created_at: datetime
    like_count: int
    retweet_count: int
    reply_count: int
    url: str


@dataclass
class Account:
    handle: str
    tier: int            # 1, 2, or 3
    credibility: float   # 0..1, derived from tier + overrides


@dataclass
class TickerScore:
    ticker: str
    score: float                    # signed: positive = bullish, negative = bearish
    unique_credible_voices: int
    tweet_count: int
    window_start: datetime
    window_end: datetime
    top_tweet_urls: list[str] = field(default_factory=list)
    threshold_progress: float = 0.0  # abs(score)/threshold ∈ [0, ∞); ≥1.0 means threshold crossed
    direct_score: float = 0.0        # score from explicit/inferred ticker mentions
    cascade_score: float = 0.0       # score cascaded from theme activations
    confirmation_factor: float = 1.0  # multiplier applied from TradingView movement (1.0 = no data)
    movement_pct: float | None = None  # latest TradingView pct change for ticker, or None


@dataclass
class Mover:
    """A ticker currently in TradingView's top gainers/losers."""
    ticker: str
    pct_change: float       # signed: -8.2 = down 8.2%
    last_price: float
    rel_volume: float       # vs 10-day average; 1.0 = normal
    market_cap: float       # USD
    rank: int               # rank within fetched side (0 = strongest)
    side: str               # "gainer" | "loser"
    captured_at: datetime


@dataclass
class MovementSnapshot:
    """Latest movement state for one ticker — used by the aggregator."""
    ticker: str
    pct_change: float
    last_price: float
    rel_volume: float
    captured_at: datetime


@dataclass
class ThemeScore:
    theme: str
    score: float                    # signed
    voices: int                     # unique credible authors
    tweet_count: int
    window_start: datetime
    window_end: datetime


@dataclass
class Signal:
    ticker: str
    side: Literal["buy", "sell"]
    conviction: float           # 0..1
    suggested_size_pct: float   # fraction of portfolio, e.g. 0.05 = 5%
    rationale: str              # 2-4 sentences, from Claude
    key_drivers: list[str]      # tweet URLs
    generated_at: datetime
