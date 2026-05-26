"""Forward-return observer.

Single async task that wakes every N seconds, scans signal_observations
rows whose next_due_at has passed, fetches the current price for each,
records the realised return into the appropriate horizon column, and
advances the row's state. Rows transition pending → complete when all
horizons have been recorded.

State lives in the DB so the observer survives restarts.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Awaitable, Callable

from dolev_ai.db import (
    SignalObservationRow,
    due_observations,
    record_observation_return,
)

logger = logging.getLogger(__name__)

PriceFetcher = Callable[[str], Awaitable[float | None]]


class ForwardReturnObserver:
    """Periodically advance pending signal observations.

    Args:
        session_factory: callable returning a Session (used per tick).
        price_fetcher:   async callable(ticker) -> price or None.
        tick_seconds:    wake interval. Default 5s.
    """

    def __init__(
        self,
        session_factory: Callable,
        price_fetcher: PriceFetcher,
        tick_seconds: float = 5.0,
    ) -> None:
        self._session_factory = session_factory
        self._fetch = price_fetcher
        self._tick = tick_seconds
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop())
        logger.info(f"ForwardReturnObserver started (tick={self._tick}s)")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=2.0)
            except asyncio.TimeoutError:
                self._task.cancel()
        self._task = None

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.tick()
            except Exception as e:
                logger.warning(f"Observer tick failed: {e}")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._tick)
            except asyncio.TimeoutError:
                continue

    async def tick(self) -> int:
        """Process one round of due observations. Returns number processed.

        Exposed for tests + manual invocation.
        """
        with self._session_factory() as session:
            due = due_observations(session)
            if not due:
                return 0

            # Group by ticker so we issue one price fetch per distinct symbol
            by_ticker: dict[str, list[SignalObservationRow]] = {}
            for row in due:
                by_ticker.setdefault(row.ticker, []).append(row)

            processed = 0
            for ticker, rows in by_ticker.items():
                price = await self._fetch(ticker)
                if price is None or price <= 0:
                    logger.debug(f"Skipping observations for {ticker}: no price")
                    continue
                for row in rows:
                    horizon = self._next_horizon_for(row)
                    if horizon is None:
                        continue
                    record_observation_return(session, row.id, horizon, price)
                    processed += 1
            return processed

    @staticmethod
    def _next_horizon_for(row: SignalObservationRow) -> int | None:
        try:
            remaining: list[int] = json.loads(row.horizons_remaining_json or "[]")
        except Exception:
            return None
        if not remaining:
            return None
        return remaining[0]
