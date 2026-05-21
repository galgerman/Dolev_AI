"""TrustGraphStrategy — emits signals when conviction threshold is crossed."""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Callable

from dolev_ai.models import Signal, TickerScore
from dolev_ai.strategies import SignalStrategy


class TrustGraphStrategy(SignalStrategy):
    def __init__(
        self,
        score_threshold: float = 5.0,
        min_credible_voices: int = 3,
        cooldown_hours: float = 4.0,
        cooldown_override_multiplier: float = 2.0,
        last_alert_time_fn: Callable[[str], datetime | None] = lambda _: None,
        last_alert_score_fn: Callable[[str], float | None] = lambda _: None,
    ) -> None:
        self._threshold = score_threshold
        self._min_voices = min_credible_voices
        self._cooldown = timedelta(hours=cooldown_hours)
        self._override_mult = cooldown_override_multiplier
        self._last_alert_time = last_alert_time_fn
        self._last_alert_score = last_alert_score_fn

    def evaluate(self, ticker_scores: dict[str, TickerScore]) -> list[Signal]:
        signals: list[Signal] = []
        now = datetime.utcnow()

        for ticker, ts in ticker_scores.items():
            abs_score = abs(ts.score)

            # minimum voices check
            if ts.unique_credible_voices < self._min_voices:
                continue

            # threshold check
            if abs_score < self._threshold:
                continue

            # cooldown check
            last_time = self._last_alert_time(ticker)
            if last_time is not None:
                elapsed = now - last_time
                if elapsed < self._cooldown:
                    last_score = self._last_alert_score(ticker) or 0.0
                    if abs_score < abs(last_score) * self._override_mult:
                        continue  # still in cooldown, score hasn't doubled

            side = "buy" if ts.score > 0 else "sell"
            conviction = min(1.0, abs_score / (self._threshold * 3))
            suggested_size_pct = min(0.05, conviction * 0.10)

            signals.append(Signal(
                ticker=ticker,
                side=side,
                conviction=round(conviction, 3),
                suggested_size_pct=round(suggested_size_pct, 4),
                rationale="",               # filled by Synthesizer
                key_drivers=ts.top_tweet_urls[:10],
                generated_at=now,
            ))

        return signals
