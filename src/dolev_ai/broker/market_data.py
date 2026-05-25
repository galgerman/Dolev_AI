"""IBKR market data source — scanner + price gradient computation.

Replaces (or supplements) TradingView scanner. Uses TWS/Gateway to:
  1. Run a scanner subscription for top gainers + losers on US major exchanges
  2. Fetch the last N 1-minute bars for the top candidates
  3. Compute a linear-regression gradient (slope in %/min) for each

The gradient is the key differentiator from TradingView:
  TradingView: "NVDA is up 5.2% from open"
  IBKR gradient: "NVDA is moving at +0.38%/min right now"

A high gradient magnitude means the move is happening NOW, not hours ago.
"""
from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

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
    # Normalise: convert to % change relative to first bar
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
    """Raw scanner result before gradient enrichment."""
    ticker: str
    pct_change: float      # % from previous close (signed)
    last_price: float
    volume: int
    side: str              # 'gainer' | 'loser'
    rank: int


@dataclass
class GradientMover:
    """Scanner result enriched with gradient and bar history."""
    ticker: str
    pct_change: float      # % from open (total move)
    last_price: float
    volume: int
    side: str
    rank: int
    gradient: float        # %/min linear slope — the key signal
    gradient_bars: int     # how many bars the gradient is computed over
    bar_closes: list[float] = field(default_factory=list)  # for charting
    captured_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ── IBKR Scanner + gradient source ───────────────────────────────────────────

_SCAN_CODES = {
    "gainer": "TOP_PERC_GAIN",
    "loser":  "TOP_PERC_LOSE",
}

# IBKR historical data rate limit: max ~60 requests per 10 min.
# We batch small requests and stagger them.
_HIST_SEMAPHORE_LIMIT = 5   # concurrent historical requests


class IBKRMarketSource:
    """Fetches top movers from IBKR and enriches with price gradient.

    Designed to be a drop-in replacement for TradingViewSource.
    Requires an active TWS / IB Gateway connection (IBKRBroker).
    """

    def __init__(
        self,
        broker: "IBKRBroker",  # noqa: F821
        top_n_per_side: int = 50,
        gradient_top_n: int = 20,      # gradient computed only for top N by |%|
        gradient_bars: int = 10,        # last N 1-min bars
        min_market_cap_m: float = 100.0,
        min_volume: int = 100_000,
    ) -> None:
        self._broker = broker
        self._top_n = top_n_per_side
        self._gradient_top_n = gradient_top_n
        self._gradient_bars = gradient_bars
        self._min_mcap_m = min_market_cap_m
        self._min_vol = min_volume
        self._sem = asyncio.Semaphore(_HIST_SEMAPHORE_LIMIT)

    @property
    def _ib(self):
        return self._broker._ib

    async def _scan_side(self, side: str) -> list[CandidateMover]:
        if not self._broker.connected:
            return []
        scan = ibs.ScannerSubscription()
        scan.instrument = "STK"
        scan.locationCode = "STK.US.MAJOR"   # NASDAQ + NYSE
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
                # IBKR scanner gives % change in sd.distance field (signed string)
                pct = float(sd.distance or "0")
                price = float(getattr(sd.contractDetails.contract, "strike", 0) or 0)
                # fallback: re-request ticker snapshot for last/pct
                movers.append(CandidateMover(
                    ticker=sym,
                    pct_change=pct,
                    last_price=price,
                    volume=0,
                    side=side,
                    rank=i,
                ))
            except Exception:
                continue
        return movers

    async def _enrich_snapshot(self, mover: CandidateMover) -> CandidateMover:
        """Fetch live snapshot (last price, pct_change, volume) for one ticker."""
        if not self._broker.connected:
            return mover
        contract = ibs.Stock(mover.ticker, "SMART", "USD")
        try:
            ticker = self._ib.reqMktData(contract, "221,233", snapshot=True)
            await asyncio.sleep(1.5)   # snapshot needs ~1s to populate
            self._ib.cancelMktData(contract)
            if ticker.last and not math.isnan(ticker.last):
                mover.last_price = ticker.last
            if ticker.volume and not math.isnan(ticker.volume):
                mover.volume = int(ticker.volume)
            if ticker.changePercent and not math.isnan(ticker.changePercent):
                mover.pct_change = ticker.changePercent
        except Exception as e:
            logger.debug(f"Snapshot failed for {mover.ticker}: {e}")
        return mover

    async def _compute_gradient(self, mover: CandidateMover) -> GradientMover:
        """Fetch last N 1-min bars and compute linear slope (%/min)."""
        bar_closes: list[float] = []
        gradient = 0.0

        if self._broker.connected:
            contract = ibs.Stock(mover.ticker, "SMART", "USD")
            try:
                async with self._sem:
                    bars = await asyncio.wait_for(
                        self._ib.reqHistoricalDataAsync(
                            contract,
                            endDateTime="",
                            durationStr=f"{self._gradient_bars + 2} M",
                            barSizeSetting="1 min",
                            whatToShow="TRADES",
                            useRTH=False,
                            formatDate=1,
                            keepUpToDate=False,
                        ),
                        timeout=10.0,
                    )
                if bars:
                    bar_closes = [b.close for b in bars[-self._gradient_bars:]]
                    gradient = round(_linear_slope(bar_closes), 4)
            except Exception as e:
                logger.debug(f"Historical data failed for {mover.ticker}: {e}")

        return GradientMover(
            ticker=mover.ticker,
            pct_change=mover.pct_change,
            last_price=mover.last_price,
            volume=mover.volume,
            side=mover.side,
            rank=mover.rank,
            gradient=gradient,
            gradient_bars=len(bar_closes),
            bar_closes=bar_closes,
            captured_at=datetime.now(timezone.utc),
        )

    async def fetch_movers(self) -> list[GradientMover]:
        """Scan top gainers + losers, enrich top candidates with gradient."""
        gainers, losers = await asyncio.gather(
            self._scan_side("gainer"),
            self._scan_side("loser"),
        )
        all_candidates = gainers + losers
        if not all_candidates:
            logger.warning("IBKR scanner returned no candidates")
            return []

        # Sort by |pct_change| — take top N for gradient computation
        by_move = sorted(all_candidates, key=lambda m: abs(m.pct_change), reverse=True)
        top = by_move[:self._gradient_top_n]
        rest = by_move[self._gradient_top_n:]

        # Compute gradients concurrently (semaphore limits IBKR rate)
        top_enriched = await asyncio.gather(*[self._compute_gradient(m) for m in top])

        # Rest gets gradient=0 (no bars fetched, saves API quota)
        rest_enriched = [
            GradientMover(
                ticker=m.ticker, pct_change=m.pct_change, last_price=m.last_price,
                volume=m.volume, side=m.side, rank=m.rank,
                gradient=0.0, gradient_bars=0,
            )
            for m in rest
        ]

        result = list(top_enriched) + rest_enriched
        logger.info(
            f"IBKR movers: {len(gainers)} gainers / {len(losers)} losers — "
            f"gradient computed for {len(top_enriched)} tickers"
        )
        return result
