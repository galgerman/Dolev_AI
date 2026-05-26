"""Reference data cache: SPY/QQQ/sector-ETF gradients + premarket H/L.

Two distinct lifetimes:
  - per-cycle:  reference gradients (cleared at the start of each scan cycle)
  - per-day:    premarket high/low (cleared at UTC midnight)
  - persistent: ticker → sector ETF mapping (file-backed, lazy via yfinance)

ReferenceCache is a passive data holder. IBKRMarketSource populates the
gradients and premarket H/L during fetch_movers; FeatureSnapshot reads them.
"""
from __future__ import annotations

import json
import logging
import pathlib
from datetime import date, datetime, timezone
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

_ROOT = pathlib.Path(__file__).parent.parent.parent.parent
_DEFAULT_CFG = _ROOT / "config" / "sectors.yaml"
_DEFAULT_CACHE = _ROOT / "data" / "ticker_sectors.json"


class ReferenceCache:
    """Per-cycle gradients + per-day premarket + persistent sector map."""

    def __init__(
        self,
        config_path: pathlib.Path = _DEFAULT_CFG,
        cache_path: pathlib.Path = _DEFAULT_CACHE,
    ) -> None:
        self._config_path = pathlib.Path(config_path)
        self._cache_path = pathlib.Path(cache_path)
        cfg = self._load_config()
        self._industry_to_etf: dict[str, str] = cfg.get("industry_to_etf", {})
        self._fallback_etf: str = cfg.get("fallback_etf", "QQQ")
        self._overrides: dict[str, str] = cfg.get("overrides") or {}

        self._sector_cache: dict[str, str] = self._load_sector_cache()
        self._gradient_cache: dict[str, float] = {}
        self._premarket_cache: dict[str, tuple[float, float, date]] = {}

    # ── config + persistence ─────────────────────────────────────────────

    def _load_config(self) -> dict:
        if not self._config_path.exists():
            logger.warning(f"sectors.yaml not found at {self._config_path}")
            return {}
        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            logger.error(f"Failed to load {self._config_path}: {e}")
            return {}

    def _load_sector_cache(self) -> dict[str, str]:
        if not self._cache_path.exists():
            return {}
        try:
            return json.loads(self._cache_path.read_text())
        except Exception as e:
            logger.warning(f"Failed to load sector cache: {e}")
            return {}

    def _persist_sector_cache(self) -> None:
        try:
            self._cache_path.parent.mkdir(exist_ok=True)
            self._cache_path.write_text(json.dumps(self._sector_cache, indent=2, sort_keys=True))
        except Exception as e:
            logger.warning(f"Failed to persist sector cache: {e}")

    # ── per-cycle gradient cache ─────────────────────────────────────────

    def start_cycle(self) -> None:
        """Invalidate per-cycle data. Call at the start of each scan."""
        self._gradient_cache.clear()

    def set_ref_gradient(self, symbol: str, gradient: float) -> None:
        self._gradient_cache[symbol.upper()] = gradient

    def get_ref_gradient(self, symbol: str) -> float:
        return self._gradient_cache.get(symbol.upper(), 0.0)

    # ── per-day premarket H/L cache ──────────────────────────────────────

    def set_premarket_hl(self, ticker: str, high: float, low: float) -> None:
        today = datetime.now(timezone.utc).date()
        self._premarket_cache[ticker.upper()] = (high, low, today)

    def get_premarket_hl(self, ticker: str) -> Optional[tuple[float, float]]:
        today = datetime.now(timezone.utc).date()
        hit = self._premarket_cache.get(ticker.upper())
        if not hit:
            return None
        high, low, when = hit
        if when != today:
            del self._premarket_cache[ticker.upper()]
            return None
        return (high, low)

    def clear_stale_premarket(self) -> None:
        """Remove premarket entries from previous days. Called by scheduler."""
        today = datetime.now(timezone.utc).date()
        stale = [t for t, (_, _, d) in self._premarket_cache.items() if d != today]
        for t in stale:
            del self._premarket_cache[t]

    # ── sector ETF mapping (persistent) ──────────────────────────────────

    def get_sector_etf(self, ticker: str) -> str:
        ticker = ticker.upper()
        if ticker in self._overrides:
            return self._overrides[ticker]
        if ticker in self._sector_cache:
            return self._sector_cache[ticker]
        etf = self._lookup_sector_via_yfinance(ticker)
        self._sector_cache[ticker] = etf
        self._persist_sector_cache()
        return etf

    def _lookup_sector_via_yfinance(self, ticker: str) -> str:
        try:
            import yfinance as yf
        except ImportError:
            logger.debug("yfinance unavailable — falling back to default ETF")
            return self._fallback_etf
        try:
            info = yf.Ticker(ticker).info or {}
            sector = info.get("sector") or ""
            etf = self._industry_to_etf.get(sector, self._fallback_etf)
            logger.debug(f"Sector lookup {ticker}: {sector!r} → {etf}")
            return etf
        except Exception as e:
            logger.debug(f"yfinance sector lookup failed for {ticker}: {e}")
            return self._fallback_etf

    # ── introspection ────────────────────────────────────────────────────

    @property
    def fallback_etf(self) -> str:
        return self._fallback_etf

    def known_sectors(self) -> set[str]:
        """Distinct ETFs in use (for reference-gradient prefetch planning)."""
        etfs = set(self._industry_to_etf.values())
        etfs.add(self._fallback_etf)
        etfs.update(self._sector_cache.values())
        etfs.update(self._overrides.values())
        # Always include SPY and QQQ for market-wide relative strength
        etfs.add("SPY")
        etfs.add("QQQ")
        return etfs
