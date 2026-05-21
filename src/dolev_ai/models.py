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


@dataclass
class Signal:
    ticker: str
    side: Literal["buy", "sell"]
    conviction: float           # 0..1
    suggested_size_pct: float   # fraction of portfolio, e.g. 0.05 = 5%
    rationale: str              # 2-4 sentences, from Claude
    key_drivers: list[str]      # tweet URLs
    generated_at: datetime
