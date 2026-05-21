"""Extract and validate $TICKER mentions from tweet text."""
from __future__ import annotations

import csv
import pathlib
import re

UNIVERSE_PATH = pathlib.Path(__file__).parent.parent.parent.parent / "config" / "universe.csv"

# Single-letter or very common false-positive tickers to skip even when $-prefixed
_BLOCKLIST: frozenset[str] = frozenset({
    "USD", "EUR", "GBP", "JPY", "CAD",  # currencies
    "ETF", "SPX", "NDX", "VIX",          # indices / generic terms
    "GDP", "CPI", "PPI", "Fed",          # macro terms
})

_CASHTAG_RE = re.compile(r"\$([A-Z]{1,5})\b")

_universe: frozenset[str] | None = None


def _load_universe() -> frozenset[str]:
    global _universe
    if _universe is not None:
        return _universe
    if not UNIVERSE_PATH.exists():
        # Fallback: accept any $-prefixed 1-5 letter token (less precise)
        _universe = frozenset()
        return _universe
    with open(UNIVERSE_PATH, newline="") as f:
        reader = csv.DictReader(f)
        _universe = frozenset(row["symbol"].strip().upper() for row in reader)
    return _universe


def extract_tickers(text: str) -> list[str]:
    """Return list of valid NYSE/NASDAQ tickers mentioned in text."""
    universe = _load_universe()
    found: list[str] = []
    for match in _CASHTAG_RE.finditer(text.upper()):
        sym = match.group(1)
        if sym in _BLOCKLIST:
            continue
        if universe and sym not in universe:
            continue
        if sym not in found:
            found.append(sym)
    return found


def reload_universe() -> None:
    """Force re-read of universe.csv (call after download_universe.py runs)."""
    global _universe
    _universe = None
