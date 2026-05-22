"""LLM provider abstraction — local Ollama/LM Studio + future cloud fallback."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal

Sentiment = Literal["positive", "negative", "neutral"]


@dataclass
class TickerMention:
    ticker: str
    sentiment: Sentiment
    confidence: float            # 0..1
    explicit: bool = False       # True if matched $TICKER or company name verbatim


@dataclass
class ThemeMention:
    theme: str                   # MUST be a key from config/themes.yaml
    sentiment: Sentiment
    confidence: float


@dataclass
class Extraction:
    post_id: str
    is_finance: bool
    tickers: list[TickerMention]
    themes: list[ThemeMention]
    overall_sentiment: Sentiment
    summary: str                 # 1-sentence, shown in UI hover
    model: str
    latency_ms: int
    raw_json: str = ""           # untouched LLM output for audit/debug


class LLMProvider(ABC):
    """Async interface for extracting structured signals from tweets."""

    name: str = "unknown"
    model: str = "unknown"

    @abstractmethod
    async def healthcheck(self) -> bool:
        """Quick liveness check — does the endpoint respond and is the model loaded?"""

    @abstractmethod
    async def extract_batch(self, tweets: list) -> list[Extraction]:
        """Extract a batch of tweets. Length and order of result matches input."""

    async def aclose(self) -> None:
        """Override to release HTTP clients etc."""
