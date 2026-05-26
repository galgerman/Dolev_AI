"""IBKR market data source — scanner + multi-feature enrichment.

For each cycle:
  1. Run scanner subscriptions for top gainers + losers (US major exchanges)
  2. Fetch SPY/QQQ + the relevant sector ETFs once into ReferenceCache
  3. For each top candidate: pull a single 7-minute window of 1-min OHLCV
     bars + bid/ask snapshot, and compute a FeatureSnapshot
  4. Lazily fetch premarket H/L per ticker (cached for the day)

Emits EnrichedMover rows that carry the full feature vector. Older
GradientMover usage is preserved via an alias.
"""
from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from dolev_ai.strategies.features import (
    FeatureSnapshot,
    RefGradients,
    compute_features,
)

logger = logging.getLogger(__name__)

try:
    import ib_insync as ibs
    _IBS_AVAILABLE = True
except ImportError:
    _IBS_AVAILABLE = False


# ── Gradient helpers ──────────────────────────────────────────────────────────

def _linear_slope(values: list[float]) -> float:
    """Return OLS slope of values vs index, in units of values/step.

    Normalised to starting value so result is in %/bar.
    """
    n = len(values)
    if n < 2 or values[0] == 0:
        return 0.0
    ys = [(v / values[0] - 1.0) * 100.0 for v in values]
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den = sum((x - mean_x) ** 2 for x in xs)
    return num / den if den != 0 else 0.0


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class CandidateMover:
    """Raw scanner result before enrichment."""
    ticker: str
    pct_change: float
    last_price: float
    volume: int
    side: str               # 'gainer' | 'loser'
    rank: int


@dataclass
class EnrichedMover:
    """Scanner result enriched with full FeatureSnapshot + bar history.

    Flattened so save_movements()/MovementSnapshot can read via getattr.
    """
    ticker: str
    pct_change: float
    last_price: float
    volume: int
    side: str
    rank: int
    # Momentum
    gradient: float = 0.0
    gradient_bars: int = 0
    roc_1m: float = 0.0
    roc_3m: float = 0.0
    roc_5m: float = 0.0
    acceleration: float = 0.0
    # Volume
    rel_volume: float = 0.0
    breakout_volume_ratio: float = 0.0
    # Structure
    vwap: float = 0.0
    vwap_state: str = "unknown"
    extension_pct: float = 0.0
    broke_pmh: bool = False
    broke_pml: bool = False
    # Relative strength
    rel_strength_spy: float = 0.0
    rel_strength_qqq: float = 0.0
    rel_strength_sector: float = 0.0
    sector_etf: Optional[str] = None
    # Liquidity
    bid: float = 0.0
    ask: float = 0.0
    spread_pct: float = 0.0
    # Bookkeeping
    bar_closes: list[float] = field(default_factory=list)
    captured_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def from_features(
        cls,
        candidate: CandidateMover,
        snap: FeatureSnapshot,
        sector_etf: Optional[str],
        bar_closes: list[float],
    ) -> "EnrichedMover":
        return cls(
            ticker=candidate.ticker,
            pct_change=candidate.pct_change,
            last_price=candidate.last_price,
            volume=candidate.volume,
            side=candidate.side,
            rank=candidate.rank,
            gradient=snap.gradient,
            gradient_bars=snap.bars_count,
            roc_1m=snap.roc_1m,
            roc_3m=snap.roc_3m,
            roc_5m=snap.roc_5m,
            acceleration=snap.acceleration,
            rel_volume=snap.rel_volume,
            breakout_volume_ratio=snap.breakout_volume_ratio,
            vwap=snap.vwap,
            vwap_state=snap.vwap_state,
            extension_pct=snap.extension_pct,
            broke_pmh=snap.broke_pmh,
            broke_pml=snap.broke_pml,
            rel_strength_spy=snap.rel_strength_spy,
            rel_strength_qqq=snap.rel_strength_qqq,
            rel_strength_sector=snap.rel_strength_sector,
            sector_etf=sector_etf,
            bid=snap.bid,
            ask=snap.ask,
            spread_pct=snap.spread_pct,
            bar_closes=bar_closes,
            captured_at=datetime.now(timezone.utc),
        )


# Backwards-compat alias — old code paths refer to GradientMover.
GradientMover = EnrichedMover


# ── IBKR Scanner + multi-feature source ──────────────────────────────────────

_SCAN_CODES = {
    "gainer": "TOP_PERC_GAIN",
    "loser":  "TOP_PERC_LOSE",
}

_HIST_SEMAPHORE_LIMIT = 5   # concurrent historical requests
_REF_FETCH_BARS = 10        # bars used for reference ETF gradient


class IBKRMarketSource:
    """Fetches top movers + multi-feature enrichment from IBKR."""

    def __init__(
        self,
        broker: "IBKRBroker",  # noqa: F821
        reference_cache=None,
        top_n_per_side: int = 50,
        gradient_top_n: int = 20,
        gradient_bars: int = 10,
        feature_bars: int = 12,         # OHLCV window for the feature engine
        min_market_cap_m: float = 100.0,
        min_volume: int = 100_000,
        snapshot_enabled: bool = True,
    ) -> None:
        self._broker = broker
        self._ref_cache = reference_cache
        self._top_n = top_n_per_side
        self._gradient_top_n = gradient_top_n
        self._gradient_bars = gradient_bars
        self._feature_bars = feature_bars
        self._min_mcap_m = min_market_cap_m
        self._min_vol = min_volume
        self._snapshot_enabled = snapshot_enabled
        self._sem = asyncio.Semaphore(_HIST_SEMAPHORE_LIMIT)

    @property
    def _ib(self):
        return self._broker._ib

    # ── Scanner ──────────────────────────────────────────────────────────────

    async def _scan_side(self, side: str) -> list[CandidateMover]:
        if not self._broker.connected:
            return []
        scan = ibs.ScannerSubscription()
        scan.instrument = "STK"
        scan.locationCode = "STK.US.MAJOR"
        scan.scanCode = _SCAN_CODES[side]
        scan.numberOfRows = self._top_n
        scan.aboveVolume = self._min_vol
        scan.marketCapAbove = self._min_mcap_m * 1_000_000

        try:
            results = await asyncio.wait_for(
                self._ib.reqScannerDataAsync(scan),
                timeout=15.0,
            )
        except Exception as e:
            logger.warning(f"IBKR scanner ({side}) failed: {e}")
            return []

        movers: list[CandidateMover] = []
        for i, sd in enumerate(results):
            try:
                sym = sd.contractDetails.contract.symbol
                pct = float(sd.distance or "0")
                price = float(getattr(sd.contractDetails.contract, "strike", 0) or 0)
                movers.append(CandidateMover(
                    ticker=sym, pct_change=pct, last_price=price,
                    volume=0, side=side, rank=i,
                ))
            except Exception:
                continue
        return movers

    # ── Bar history (single fetch per ticker) ────────────────────────────────

    async def _fetch_bars(self, ticker: str, duration_minutes: int):
        """Fetch 1-min TRADES bars including extended hours."""
        if not self._broker.connected:
            return []
        contract = ibs.Stock(ticker, "SMART", "USD")
        try:
            async with self._sem:
                bars = await asyncio.wait_for(
                    self._ib.reqHistoricalDataAsync(
                        contract,
                        endDateTime="",
                        durationStr=f"{duration_minutes} M",
                        barSizeSetting="1 min",
                        whatToShow="TRADES",
                        useRTH=False,
                        formatDate=1,
                        keepUpToDate=False,
                    ),
                    timeout=10.0,
                )
            return list(bars or [])
        except Exception as e:
            logger.debug(f"Historical data failed for {ticker}: {e}")
            return []

    async def _fetch_snapshot(self, ticker: str) -> tuple[float, float, float, int]:
        """Live snapshot: returns (last, bid, ask, volume). 0.0 / 0 when missing."""
        if not (self._snapshot_enabled and self._broker.connected):
            return (0.0, 0.0, 0.0, 0)
        contract = ibs.Stock(ticker, "SMART", "USD")
        last = bid = ask = 0.0
        volume = 0
        try:
            tk = self._ib.reqMktData(contract, "221,233", snapshot=True)
            await asyncio.sleep(1.5)
            self._ib.cancelMktData(contract)
            if tk.last and not math.isnan(tk.last):
                last = float(tk.last)
            if tk.bid and not math.isnan(tk.bid):
                bid = float(tk.bid)
            if tk.ask and not math.isnan(tk.ask):
                ask = float(tk.ask)
            if tk.volume and not math.isnan(tk.volume):
                volume = int(tk.volume)
        except Exception as e:
            logger.debug(f"Snapshot failed for {ticker}: {e}")
        return (last, bid, ask, volume)

    # ── Premarket H/L (lazy, cached per day) ────────────────────────────────

    async def _fetch_premarket_hl(self, ticker: str) -> Optional[tuple[float, float]]:
        """Fetch today's premarket high/low (04:00–09:30 ET) via 5-min bars.

        Returns (high, low) or None on failure / no premarket data.
        """
        if not self._broker.connected:
            return None
        contract = ibs.Stock(ticker, "SMART", "USD")
        try:
            async with self._sem:
                bars = await asyncio.wait_for(
                    self._ib.reqHistoricalDataAsync(
                        contract,
                        endDateTime="",
                        durationStr="1 D",
                        barSizeSetting="5 mins",
                        whatToShow="TRADES",
                        useRTH=False,
                        formatDate=1,
                        keepUpToDate=False,
                    ),
                    timeout=15.0,
                )
        except Exception as e:
            logger.debug(f"Premarket bar fetch failed for {ticker}: {e}")
            return None

        # Filter to 04:00–09:30 ET. IBKR returns bars in exchange tz; the date
        # field is timezone-aware when formatDate=1 — fall back if not.
        from zoneinfo import ZoneInfo
        et = ZoneInfo("America/New_York")
        today_et = datetime.now(et).date()
        highs: list[float] = []
        lows: list[float] = []
        for b in bars or []:
            ts = getattr(b, "date", None)
            if ts is None:
                continue
            ts_dt = ts if isinstance(ts, datetime) else datetime.combine(ts, datetime.min.time())
            try:
                ts_et = ts_dt.astimezone(et)
            except Exception:
                ts_et = ts_dt
            if ts_et.date() != today_et:
                continue
            mins = ts_et.hour * 60 + ts_et.minute
            if 4 * 60 <= mins < 9 * 60 + 30:
                if not math.isnan(b.high):
                    highs.append(float(b.high))
                if not math.isnan(b.low):
                    lows.append(float(b.low))
        if not highs or not lows:
            return None
        return (max(highs), min(lows))

    async def _ensure_premarket_hl(self, ticker: str) -> Optional[tuple[float, float]]:
        """Get from cache; fetch + cache on miss."""
        if self._ref_cache:
            hit = self._ref_cache.get_premarket_hl(ticker)
            if hit:
                return hit
        result = await self._fetch_premarket_hl(ticker)
        if result and self._ref_cache:
            self._ref_cache.set_premarket_hl(ticker, result[0], result[1])
        return result

    # ── Reference ETF gradients ──────────────────────────────────────────────

    async def _prefetch_reference_gradients(self, symbols: list[str]) -> None:
        """Pull SPY/QQQ/sector ETF bars in parallel; cache their gradients."""
        if not self._ref_cache or not symbols:
            return
        async def _one(sym: str) -> None:
            bars = await self._fetch_bars(sym, _REF_FETCH_BARS + 2)
            if not bars:
                return
            closes = [float(b.close) for b in bars[-_REF_FETCH_BARS:]]
            grad = round(_linear_slope(closes), 4)
            self._ref_cache.set_ref_gradient(sym, grad)
        await asyncio.gather(*(_one(s) for s in symbols))

    def _refs_for_candidate(self, ticker: str) -> tuple[Optional[str], RefGradients]:
        if not self._ref_cache:
            return (None, RefGradients())
        etf = self._ref_cache.get_sector_etf(ticker)
        return (etf, RefGradients(
            spy=self._ref_cache.get_ref_gradient("SPY"),
            qqq=self._ref_cache.get_ref_gradient("QQQ"),
            sector=self._ref_cache.get_ref_gradient(etf),
        ))

    # ── Per-candidate enrichment ─────────────────────────────────────────────

    async def _enrich(self, mover: CandidateMover) -> EnrichedMover:
        # 1) Bars (single fetch, used for everything)
        bars = await self._fetch_bars(mover.ticker, self._feature_bars + 2)
        bar_closes = [float(b.close) for b in bars[-self._gradient_bars:]] if bars else []
        gradient = round(_linear_slope(bar_closes), 4) if bar_closes else 0.0

        # 2) Live snapshot for bid/ask + freshest last price
        last, bid, ask, volume = await self._fetch_snapshot(mover.ticker)
        if last > 0:
            mover.last_price = last
        if volume > 0:
            mover.volume = volume

        # 3) Reference gradients + sector
        sector_etf, refs = self._refs_for_candidate(mover.ticker)

        # 4) Premarket H/L (cached per day)
        pmh = pml = None
        pm = await self._ensure_premarket_hl(mover.ticker)
        if pm:
            pmh, pml = pm

        # 5) Build FeatureSnapshot
        snap = compute_features(
            bars or [],
            gradient=gradient,
            last_price=mover.last_price,
            pmh=pmh, pml=pml,
            refs=refs,
            bid=bid, ask=ask,
        )

        return EnrichedMover.from_features(mover, snap, sector_etf, bar_closes)

    # ── Public entry point ───────────────────────────────────────────────────

    async def fetch_movers(self) -> list[EnrichedMover]:
        """Scan top gainers + losers, enrich top candidates with full features."""
        # Reset per-cycle reference gradient cache
        if self._ref_cache:
            self._ref_cache.start_cycle()

        gainers, losers = await asyncio.gather(
            self._scan_side("gainer"),
            self._scan_side("loser"),
        )
        all_candidates = gainers + losers
        if not all_candidates:
            logger.warning("IBKR scanner returned no candidates")
            return []

        # Sort by |pct_change| — take top N for enrichment
        by_move = sorted(all_candidates, key=lambda m: abs(m.pct_change), reverse=True)
        top = by_move[:self._gradient_top_n]
        rest = by_move[self._gradient_top_n:]

        # Resolve sector ETFs for the top candidates so we know which
        # reference symbols to prefetch.
        sector_etfs: set[str] = set()
        if self._ref_cache:
            for cand in top:
                sector_etfs.add(self._ref_cache.get_sector_etf(cand.ticker))
        ref_symbols = sorted({"SPY", "QQQ", *sector_etfs})

        await self._prefetch_reference_gradients(ref_symbols)

        # Enrich top candidates concurrently
        top_enriched = await asyncio.gather(*[self._enrich(m) for m in top])

        # Rest: minimal placeholder, no bars fetched
        rest_enriched = [
            EnrichedMover(
                ticker=m.ticker, pct_change=m.pct_change, last_price=m.last_price,
                volume=m.volume, side=m.side, rank=m.rank,
            )
            for m in rest
        ]

        result = list(top_enriched) + rest_enriched
        logger.info(
            f"IBKR movers: {len(gainers)} gainers / {len(losers)} losers — "
            f"features computed for {len(top_enriched)} tickers "
            f"({len(ref_symbols)} ref ETFs)"
        )
        return result
