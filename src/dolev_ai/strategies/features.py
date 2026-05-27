"""Per-ticker feature engine for the momentum research system.

Pure functions over OHLCV bars + reference data. No I/O, no IBKR calls —
the data is fetched once in IBKRMarketSource and passed in here.

Features extracted:
  Momentum:    gradient, roc_1m, roc_3m, roc_5m, acceleration
  Volume:      rel_volume, breakout_volume_ratio
  Structure:   vwap, vwap_state, extension_pct, broke_pmh, broke_pml
  Strength:    rel_strength_spy, rel_strength_qqq, rel_strength_sector
  Liquidity:   bid, ask, spread_pct
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Iterable, Protocol


class BarLike(Protocol):
    """Duck-typed 1-min OHLCV bar (matches ib_insync BarData)."""
    open: float
    high: float
    low: float
    close: float
    volume: float


# ── FeatureSnapshot ─────────────────────────────────────────────────────────

@dataclass
class FeatureSnapshot:
    """Complete feature vector for one ticker at one moment in time."""
    # Momentum
    gradient: float = 0.0          # %/min linear slope (existing)
    roc_1m: float = 0.0            # %
    roc_3m: float = 0.0            # %
    roc_5m: float = 0.0            # %
    acceleration: float = 0.0      # roc_1m_now - roc_1m_prev (% change in %/min)

    # Volume
    rel_volume: float = 0.0        # current volume / avg volume baseline
    breakout_volume_ratio: float = 0.0   # last bar vol / median prior bars vol

    # Structure
    vwap: float = 0.0
    vwap_state: str = "unknown"    # 'above' | 'below' | 'near' | 'unknown'
    extension_pct: float = 0.0     # (price - vwap) / vwap * 100
    broke_pmh: bool = False        # broke premarket high
    broke_pml: bool = False        # broke premarket low

    # Relative strength (mover.gradient - reference.gradient, in %/min)
    rel_strength_spy: float = 0.0
    rel_strength_qqq: float = 0.0
    rel_strength_sector: float = 0.0

    # Liquidity
    bid: float = 0.0
    ask: float = 0.0
    spread_pct: float = 0.0        # (ask - bid) / mid * 100

    # Bookkeeping
    bars_count: int = 0


# ── Pure compute helpers ────────────────────────────────────────────────────

def compute_roc(closes: list[float], window_bars: int) -> float:
    """Rate of change in % over the last `window_bars` 1-min bars.

    Returns (last / close_window_bars_ago - 1) * 100.
    Returns 0.0 if not enough bars or denominator is zero.
    """
    if window_bars < 1 or len(closes) < window_bars + 1:
        return 0.0
    base = closes[-(window_bars + 1)]
    if base == 0:
        return 0.0
    return round((closes[-1] / base - 1.0) * 100.0, 4)


def compute_acceleration(closes: list[float]) -> float:
    """Difference between current 1-min ROC and previous 1-min ROC.

    Positive = momentum strengthening. Negative = weakening.
    Needs at least 3 bars.
    """
    if len(closes) < 3:
        return 0.0
    if closes[-2] == 0 or closes[-3] == 0:
        return 0.0
    cur = (closes[-1] / closes[-2] - 1.0) * 100.0
    prev = (closes[-2] / closes[-3] - 1.0) * 100.0
    return round(cur - prev, 4)


def compute_vwap(bars: Iterable[BarLike]) -> float:
    """Volume-weighted average price = sum(typical * vol) / sum(vol).

    Typical price = (high + low + close) / 3.
    Returns 0.0 if no bars or total volume is zero.
    """
    num = 0.0
    den = 0.0
    for b in bars:
        typ = (b.high + b.low + b.close) / 3.0
        v = float(b.volume or 0)
        num += typ * v
        den += v
    if den == 0:
        return 0.0
    return round(num / den, 4)


def vwap_state(price: float, vwap: float, near_pct: float = 0.2) -> str:
    """Classify price relative to VWAP.

    near_pct: percent distance within which we call it 'near' (default 0.2%).
    """
    if vwap <= 0:
        return "unknown"
    diff_pct = abs(price - vwap) / vwap * 100.0
    if diff_pct <= near_pct:
        return "near"
    return "above" if price > vwap else "below"


def compute_extension(price: float, vwap: float) -> float:
    """How far price is from VWAP, in %. Signed."""
    if vwap <= 0:
        return 0.0
    return round((price - vwap) / vwap * 100.0, 4)


def compute_breakout_volume_ratio(volumes: list[float]) -> float:
    """Last bar volume / median of prior bars volume.

    >1.0 means the breakout candle had above-average volume vs prior bars.
    Returns 0.0 if not enough data or zero baseline.
    """
    if len(volumes) < 4:
        return 0.0
    last = float(volumes[-1] or 0)
    prior = [float(v or 0) for v in volumes[:-1]]
    prior_nonzero = [v for v in prior if v > 0]
    if not prior_nonzero:
        return 0.0
    baseline = statistics.median(prior_nonzero)
    if baseline == 0:
        return 0.0
    return round(last / baseline, 3)


def compute_rel_volume(volumes: list[float], baseline_avg: float | None = None) -> float:
    """Current bar volume vs average baseline.

    If baseline_avg is provided (e.g. 20-day average per-minute volume), use it.
    Otherwise fall back to mean of supplied volumes (same as breakout ratio).
    """
    if not volumes:
        return 0.0
    last = float(volumes[-1] or 0)
    if baseline_avg is not None and baseline_avg > 0:
        return round(last / baseline_avg, 3)
    if len(volumes) < 2:
        return 0.0
    prior = [float(v or 0) for v in volumes[:-1] if v]
    if not prior:
        return 0.0
    mean_v = sum(prior) / len(prior)
    if mean_v == 0:
        return 0.0
    return round(last / mean_v, 3)


# ── Top-level snapshot builder ──────────────────────────────────────────────

@dataclass
class RefGradients:
    """Reference benchmarks' gradients (%/min), used for relative strength."""
    spy: float = 0.0
    qqq: float = 0.0
    sector: float = 0.0


def compute_features(
    bars: list[BarLike],
    *,
    gradient: float = 0.0,
    last_price: float | None = None,
    pmh: float | None = None,
    pml: float | None = None,
    refs: RefGradients | None = None,
    bid: float = 0.0,
    ask: float = 0.0,
    rel_volume_baseline: float | None = None,
) -> FeatureSnapshot:
    """Assemble a FeatureSnapshot from a list of 1-min OHLCV bars + side data.

    `bars` should be chronological (oldest first). `last_price` defaults to
    the last bar's close. `gradient` is the existing %/min slope (already
    computed upstream — we don't recompute to save work).
    """
    if not bars:
        return FeatureSnapshot()

    closes = [float(b.close) for b in bars]
    volumes = [float(b.volume or 0) for b in bars]
    price = last_price if last_price is not None else closes[-1]

    vwap = compute_vwap(bars)
    refs = refs or RefGradients()

    mid = (bid + ask) / 2.0 if (bid > 0 and ask > 0) else 0.0
    spread_pct = round((ask - bid) / mid * 100.0, 4) if mid > 0 else 0.0

    broke_pmh = bool(pmh is not None and pmh > 0 and price > pmh)
    broke_pml = bool(pml is not None and pml > 0 and price < pml)

    return FeatureSnapshot(
        gradient=round(gradient, 4),
        roc_1m=compute_roc(closes, 1),
        roc_3m=compute_roc(closes, 3),
        roc_5m=compute_roc(closes, 5),
        acceleration=compute_acceleration(closes),
        rel_volume=compute_rel_volume(volumes, rel_volume_baseline),
        breakout_volume_ratio=compute_breakout_volume_ratio(volumes),
        vwap=vwap,
        vwap_state=vwap_state(price, vwap),
        extension_pct=compute_extension(price, vwap),
        broke_pmh=broke_pmh,
        broke_pml=broke_pml,
        rel_strength_spy=round(gradient - refs.spy, 4),
        rel_strength_qqq=round(gradient - refs.qqq, 4),
        rel_strength_sector=round(gradient - refs.sector, 4),
        bid=round(bid, 4),
        ask=round(ask, 4),
        spread_pct=spread_pct,
        bars_count=len(bars),
    )
