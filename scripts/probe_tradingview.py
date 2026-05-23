"""Probe the TradingView scanner API to confirm schema."""
import httpx
import json

BODY = {
    "filter": [
        {"left": "exchange", "operation": "in_range", "right": ["NASDAQ", "NYSE"]},
        {"left": "market_cap_basic", "operation": "greater", "right": 100_000_000},
        {"left": "volume", "operation": "greater", "right": 100_000},
    ],
    "options": {"lang": "en"},
    "markets": ["america"],
    "symbols": {"query": {"types": []}, "tickers": []},
    "columns": [
        "name", "close", "change", "change_abs", "volume",
        "relative_volume_10d_calc", "market_cap_basic",
    ],
    "sort": {"sortBy": "change", "sortOrder": "desc"},
    "range": [0, 5],
}

r = httpx.post(
    "https://scanner.tradingview.com/america/scan",
    json=BODY,
    headers={"User-Agent": "Mozilla/5.0"},
    timeout=15,
)
print(f"status: {r.status_code}")
data = r.json()
print(json.dumps(data, indent=2)[:1500])
