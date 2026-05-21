from __future__ import annotations

from abc import ABC, abstractmethod

from dolev_ai.models import Signal, TickerScore


class SignalStrategy(ABC):
    """Decides which TickerScores cross the threshold and become Signals."""

    @abstractmethod
    def evaluate(self, ticker_scores: dict[str, TickerScore]) -> list[Signal]: ...
