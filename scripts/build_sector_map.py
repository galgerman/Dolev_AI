"""Validate config/sectors.yaml and warm the per-ticker cache.

The runtime ReferenceCache.get_sector_etf() looks up each ticker via
yfinance on first encounter and caches the result to data/ticker_sectors.json.
This script can pre-populate that cache for a list of tickers given on the
command line — useful when you want predictable behavior on day-one.

Usage:
  py -3.12 scripts/build_sector_map.py            # validate config only
  py -3.12 scripts/build_sector_map.py AAPL MSFT  # warm cache for given tickers
"""
from __future__ import annotations

import json
import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).parent.parent
CFG = ROOT / "config" / "sectors.yaml"
CACHE = ROOT / "data" / "ticker_sectors.json"


def load_config() -> dict:
    with open(CFG, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    if "industry_to_etf" not in cfg or "fallback_etf" not in cfg:
        raise SystemExit(f"{CFG} missing required keys (industry_to_etf, fallback_etf)")
    return cfg


def warm_cache(tickers: list[str], cfg: dict) -> None:
    try:
        import yfinance as yf
    except ImportError:
        print("yfinance not installed; skipping cache warming")
        return

    industry_to_etf: dict[str, str] = cfg["industry_to_etf"]
    fallback: str = cfg["fallback_etf"]
    overrides: dict[str, str] = cfg.get("overrides") or {}

    CACHE.parent.mkdir(exist_ok=True)
    cache: dict[str, str] = {}
    if CACHE.exists():
        try:
            cache = json.loads(CACHE.read_text())
        except Exception:
            cache = {}

    for sym in tickers:
        sym = sym.strip().upper()
        if not sym:
            continue
        if sym in overrides:
            cache[sym] = overrides[sym]
            print(f"  {sym}: {overrides[sym]} (override)")
            continue
        if sym in cache:
            print(f"  {sym}: {cache[sym]} (cached)")
            continue
        try:
            info = yf.Ticker(sym).info or {}
            sector = info.get("sector") or ""
            etf = industry_to_etf.get(sector, fallback)
            cache[sym] = etf
            print(f"  {sym}: {etf}  (sector: {sector or '?'})")
        except Exception as e:
            cache[sym] = fallback
            print(f"  {sym}: {fallback}  (lookup failed: {e})")

    CACHE.write_text(json.dumps(cache, indent=2, sort_keys=True))
    print(f"\nCache written to {CACHE} ({len(cache)} tickers)")


def main() -> None:
    cfg = load_config()
    n_sectors = len(cfg["industry_to_etf"])
    n_overrides = len(cfg.get("overrides") or {})
    print(f"Loaded {CFG.name}: {n_sectors} sector mappings, {n_overrides} overrides")
    print(f"Fallback ETF: {cfg['fallback_etf']}")

    tickers = sys.argv[1:]
    if tickers:
        print(f"\nWarming cache for {len(tickers)} ticker(s)…")
        warm_cache(tickers, cfg)


if __name__ == "__main__":
    main()
