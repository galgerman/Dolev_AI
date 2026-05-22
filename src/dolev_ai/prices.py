"""Live price fetching via yfinance with a short in-process TTL cache."""
from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger(__name__)

_cache: dict[str, tuple[float, float]] = {}  # ticker → (price, timestamp)
_CACHE_TTL = 60.0  # seconds


def get_price(ticker: str, cache_ttl: float = _CACHE_TTL) -> float | None:
    """Return the latest price for ticker, or None on failure."""
    now = time.monotonic()
    if ticker in _cache:
        price, ts = _cache[ticker]
        if now - ts < cache_ttl:
            return price
    try:
        import yfinance as yf
        data = yf.Ticker(ticker).fast_info
        price = float(data.last_price or data.previous_close)
        if price and price > 0:
            _cache[ticker] = (price, now)
            return price
    except Exception as e:
        logger.warning(f"yfinance price fetch failed for {ticker}: {e}")
    return None


async def get_price_async(ticker: str, cache_ttl: float = _CACHE_TTL) -> float | None:
    return await asyncio.to_thread(get_price, ticker, cache_ttl)
