"""MFE/MAE tracker for open paper positions.

Per-position async task that polls the live price every N seconds and
updates the row's high-water (mfe_pct) and low-water (mae_pct) marks
in the direction of the position.

Started by paper_trade.open_position; cancelled by paper_trade.close_position.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Awaitable, Callable

from dolev_ai.db import PaperPositionRow

logger = logging.getLogger(__name__)

PriceFetcher = Callable[[str], Awaitable[float | None]]


class PositionTracker:
    """Tracks MFE/MAE for all currently-open positions."""

    def __init__(
        self,
        session_factory: Callable,
        price_fetcher: PriceFetcher,
        poll_seconds: float = 5.0,
    ) -> None:
        self._session_factory = session_factory
        self._fetch = price_fetcher
        self._poll = poll_seconds
        self._tasks: dict[int, asyncio.Task] = {}

    def is_tracking(self, position_id: int) -> bool:
        task = self._tasks.get(position_id)
        return task is not None and not task.done()

    def track(self, position_id: int) -> None:
        """Start watching a position. No-op if already tracked."""
        if self.is_tracking(position_id):
            return
        self._tasks[position_id] = asyncio.create_task(
            self._loop(position_id),
            name=f"PositionTracker-{position_id}",
        )
        logger.debug(f"Tracking position {position_id}")

    async def untrack(self, position_id: int) -> None:
        """Stop watching a position. Safe to call multiple times."""
        task = self._tasks.pop(position_id, None)
        if not task or task.done():
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    async def stop_all(self) -> None:
        for pid in list(self._tasks.keys()):
            await self.untrack(pid)

    async def _loop(self, position_id: int) -> None:
        try:
            while True:
                await self.poll_once(position_id)
                await asyncio.sleep(self._poll)
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.warning(f"PositionTracker[{position_id}] crashed: {e}")

    async def poll_once(self, position_id: int) -> bool:
        """Poll once. Returns True if a watermark was updated.

        Exposed for tests.
        """
        with self._session_factory() as session:
            pos = session.get(PaperPositionRow, position_id)
            if pos is None or pos.status != "open":
                return False
            ticker = pos.ticker
            entry = pos.entry_price
            side = pos.side
            current_mfe = pos.mfe_pct
            current_mae = pos.mae_pct

        if entry <= 0:
            return False
        price = await self._fetch(ticker)
        if price is None or price <= 0:
            return False

        raw_pct = (price - entry) / entry * 100.0
        directional = raw_pct if side == "buy" else -raw_pct

        updated = False
        with self._session_factory() as session:
            pos = session.get(PaperPositionRow, position_id)
            if pos is None or pos.status != "open":
                return False
            if current_mfe is None or directional > (current_mfe or -1e9):
                pos.mfe_pct = round(directional, 4)
                pos.mfe_at = datetime.utcnow()
                updated = True
            if current_mae is None or directional < (current_mae if current_mae is not None else 1e9):
                pos.mae_pct = round(directional, 4)
                pos.mae_at = datetime.utcnow()
                updated = True
            if updated:
                session.commit()
        return updated
