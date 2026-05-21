from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from dolev_ai.models import RawTweet


class TweetSource(ABC):
    """Abstraction over data sources (Playwright, X API, StockTwits…)."""

    @abstractmethod
    async def fetch_user_timeline(
        self, handle: str, since: datetime
    ) -> list[RawTweet]: ...

    @abstractmethod
    async def fetch_feed_engagements(
        self, handles: list[str], since: datetime
    ) -> list[RawTweet]:
        """Return retweets/quotes/replies *by* the given handles.

        Used to expand the trust graph — if a seed account retweets someone,
        that content joins the corpus.
        """
        ...
