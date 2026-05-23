"""TradingView Scanner API client — fetches top gainers / losers in real time.

The endpoint (`scanner.tradingview.com/america/scan`) is what TradingView's own
market-movers pages call. It's anonymous, JSON-based, and far more stable than
scraping HTML.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import httpx

from dolev_ai.models import Mover

logger = logging.getLogger(__name__)

_SCAN_URL = "https://scanner.tradingview.com/america/scan"

# Order matters — matches the "columns" key in the request body
_COLUMNS = [
    "name",                       # 0 → ticker symbol
    "close",                      # 1 → last price
    "change",                     # 2 → pct change (signed)
    "change_abs",                 # 3 → abs $ change (unused)
    "volume",                     # 4 → today's volume (unused — use rel_volume instead)
    "relative_volume_10d_calc",   # 5 → vs 10-day avg, 1.0 = normal
    "market_cap_basic",           # 6 → market cap (USD)
]

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Origin": "https://www.tradingview.com",
    "Referer": "https://www.tradingview.com/",
}


class TradingViewSource:
    def __init__(
        self,
        top_n_per_side: int = 100,
        min_market_cap: float = 100_000_000,
        min_volume: int = 100_000,
        timeout_s: float = 15.0,
    ) -> None:
        self._top_n = top_n_per_side
        self._min_mcap = min_market_cap
        self._min_vol = min_volume
        self._timeout = timeout_s
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        self._client = httpx.AsyncClient(timeout=self._timeout, headers=_HEADERS)

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _build_body(self, side: str) -> dict:
        sort_order = "desc" if side == "gainer" else "asc"
        return {
            "filter": [
                {"left": "exchange", "operation": "in_range", "right": ["NASDAQ", "NYSE"]},
                {"left": "market_cap_basic", "operation": "greater", "right": self._min_mcap},
                {"left": "volume", "operation": "greater", "right": self._min_vol},
            ],
            "options": {"lang": "en"},
            "markets": ["america"],
            "symbols": {"query": {"types": []}, "tickers": []},
            "columns": _COLUMNS,
            "sort": {"sortBy": "change", "sortOrder": sort_order},
            "range": [0, self._top_n],
        }

    async def _fetch_side(self, side: str) -> list[Mover]:
        if self._client is None:
            await self.start()
        assert self._client is not None
        try:
            r = await self._client.post(_SCAN_URL, json=self._build_body(side))
            r.raise_for_status()
        except Exception as e:
            logger.warning(f"TradingView fetch ({side}) failed: {e}")
            return []

        now = datetime.utcnow()
        items = r.json().get("data", []) or []
        out: list[Mover] = []
        for rank, item in enumerate(items):
            d = item.get("d") or []
            if len(d) < 7:
                continue
            try:
                ticker = str(d[0]).upper().strip()
                last_price = float(d[1] or 0)
                pct = float(d[2] or 0)
                rel_vol = float(d[5] or 1.0)
                mcap = float(d[6] or 0)
            except (TypeError, ValueError):
                continue
            if not ticker or not ticker.isalpha():
                continue
            out.append(Mover(
                ticker=ticker, pct_change=pct, last_price=last_price,
                rel_volume=rel_vol, market_cap=mcap, rank=rank,
                side=side, captured_at=now,
            ))
        return out

    async def fetch_movers(self) -> list[Mover]:
        """Returns combined list of gainers + losers, latest snapshot."""
        gainers, losers = await asyncio.gather(
            self._fetch_side("gainer"),
            self._fetch_side("loser"),
        )
        return gainers + losers
