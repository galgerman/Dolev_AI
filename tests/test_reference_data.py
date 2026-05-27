"""Tests for ReferenceCache (sector ETF + gradient + premarket H/L)."""
from __future__ import annotations

import json
import pathlib
from datetime import date, datetime, timedelta, timezone

import pytest
import yaml

from dolev_ai.strategies.reference_data import ReferenceCache


@pytest.fixture
def cfg_file(tmp_path: pathlib.Path) -> pathlib.Path:
    path = tmp_path / "sectors.yaml"
    path.write_text(yaml.safe_dump({
        "industry_to_etf": {
            "Technology": "XLK",
            "Healthcare": "XLV",
        },
        "fallback_etf": "QQQ",
        "overrides": {"GME": "XLY"},
    }))
    return path


@pytest.fixture
def cache_file(tmp_path: pathlib.Path) -> pathlib.Path:
    return tmp_path / "ticker_sectors.json"


@pytest.fixture
def cache(cfg_file: pathlib.Path, cache_file: pathlib.Path) -> ReferenceCache:
    return ReferenceCache(config_path=cfg_file, cache_path=cache_file)


# ── Per-cycle gradients ──────────────────────────────────────────────────────

def test_gradient_starts_zero(cache: ReferenceCache):
    assert cache.get_ref_gradient("SPY") == 0.0


def test_gradient_set_get_roundtrip(cache: ReferenceCache):
    cache.set_ref_gradient("SPY", 0.42)
    assert cache.get_ref_gradient("SPY") == 0.42


def test_start_cycle_clears_gradients(cache: ReferenceCache):
    cache.set_ref_gradient("SPY", 0.42)
    cache.set_ref_gradient("XLK", -0.15)
    cache.start_cycle()
    assert cache.get_ref_gradient("SPY") == 0.0
    assert cache.get_ref_gradient("XLK") == 0.0


def test_gradient_case_insensitive(cache: ReferenceCache):
    cache.set_ref_gradient("spy", 0.3)
    assert cache.get_ref_gradient("SPY") == 0.3


# ── Premarket H/L ────────────────────────────────────────────────────────────

def test_premarket_set_get(cache: ReferenceCache):
    cache.set_premarket_hl("NVDA", high=920.0, low=900.0)
    assert cache.get_premarket_hl("NVDA") == (920.0, 900.0)


def test_premarket_missing_returns_none(cache: ReferenceCache):
    assert cache.get_premarket_hl("XYZ") is None


def test_premarket_invalidates_on_stale_date(cache: ReferenceCache):
    yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1))
    cache._premarket_cache["AAPL"] = (200.0, 195.0, yesterday)
    # Stale → returns None and removes from cache
    assert cache.get_premarket_hl("AAPL") is None
    assert "AAPL" not in cache._premarket_cache


def test_clear_stale_premarket_keeps_today_entries(cache: ReferenceCache):
    today = datetime.now(timezone.utc).date()
    cache._premarket_cache["NEW"] = (100.0, 95.0, today)
    cache._premarket_cache["OLD"] = (50.0, 45.0, today - timedelta(days=2))
    cache.clear_stale_premarket()
    assert "NEW" in cache._premarket_cache
    assert "OLD" not in cache._premarket_cache


# ── Sector ETF mapping ───────────────────────────────────────────────────────

def test_override_takes_precedence(cache: ReferenceCache):
    assert cache.get_sector_etf("GME") == "XLY"


def test_cache_hit_skips_yfinance(cache: ReferenceCache):
    cache._sector_cache["AAPL"] = "XLK"
    assert cache.get_sector_etf("AAPL") == "XLK"


def test_fallback_when_yfinance_unavailable(monkeypatch, cache: ReferenceCache):
    """If yfinance lookup fails, fall back to QQQ and cache the result."""
    def _mock_lookup(self, ticker):
        return self._fallback_etf  # simulate failure path
    monkeypatch.setattr(
        ReferenceCache, "_lookup_sector_via_yfinance", _mock_lookup
    )
    assert cache.get_sector_etf("UNKNOWN") == "QQQ"
    assert cache._sector_cache["UNKNOWN"] == "QQQ"


def test_sector_cache_persists_to_disk(cache: ReferenceCache, cache_file: pathlib.Path, monkeypatch):
    def _mock_lookup(self, ticker):
        return "XLK"
    monkeypatch.setattr(ReferenceCache, "_lookup_sector_via_yfinance", _mock_lookup)
    cache.get_sector_etf("MSFT")
    # Reload from disk
    saved = json.loads(cache_file.read_text())
    assert saved["MSFT"] == "XLK"


def test_known_sectors_includes_spy_qqq(cache: ReferenceCache):
    etfs = cache.known_sectors()
    assert "SPY" in etfs
    assert "QQQ" in etfs
    assert "XLK" in etfs  # from config
    assert "XLV" in etfs


def test_case_insensitive_sector_lookup(cache: ReferenceCache):
    cache._sector_cache["AAPL"] = "XLK"
    assert cache.get_sector_etf("aapl") == "XLK"
