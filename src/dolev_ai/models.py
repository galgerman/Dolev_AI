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
    """A ticker currently in top gainers/losers (TradingView or IBKR scanner)."""
    ticker: str
    pct_change: float       # signed: -8.2 = down 8.2%
    last_price: float
    rel_volume: float       # vs 10-day average; 1.0 = normal (TradingView) or 0 (IBKR)
    market_cap: float       # USD
    rank: int               # rank within fetched side (0 = strongest)
    side: str               # "gainer" | "loser"
    captured_at: datetime
    gradient: float = 0.0   # %/min linear slope over last N 1-min bars (IBKR only)
    gradient_bars: int = 0  # how many bars gradient was computed over


@dataclass
class MovementSnapshot:
    """Latest movement state for one ticker — used by the aggregator and strategies."""
    ticker: str
    pct_change: float
    last_price: float
    rel_volume: float
    captured_at: datetime
    gradient: float = 0.0   # %/min — 0.0 means not available
    # Extended feature fields (research system)
    roc_1m: float = 0.0
    roc_3m: float = 0.0
    roc_5m: float = 0.0
    acceleration: float = 0.0
    vwap: float = 0.0
    vwap_state: str = "unknown"
    extension_pct: float = 0.0
    broke_pmh: bool = False
    broke_pml: bool = False
    rel_strength_spy: float = 0.0
    rel_strength_qqq: float = 0.0
    rel_strength_sector: float = 0.0
    sector_etf: str | None = None
    bid: float = 0.0
    ask: float = 0.0
    spread_pct: float = 0.0
    breakout_volume_ratio: float = 0.0


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
