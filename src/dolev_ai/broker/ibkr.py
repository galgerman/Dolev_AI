"""Interactive Brokers broker adapter using ib_insync.

Connects to TWS or IB Gateway. Use port 7497 for TWS paper,
4002 for IB Gateway paper.

The broker is intentionally thin — it translates OrderSpec → IBKR
bracket orders and returns fills. All risk logic lives in risk.py.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Lazy import so the rest of the app loads even if ib_insync isn't installed
try:
    import ib_insync as ibs
    _IBS_AVAILABLE = True
except ImportError:
    _IBS_AVAILABLE = False
    logger.warning("ib_insync not installed — IBKRBroker will not connect")


@dataclass
class Fill:
    ticker: str
    side: str
    shares: int
    avg_price: float
    order_id: int
    stop_order_id: int


class IBKRBroker:
    """Async wrapper around ib_insync for paper (and eventually live) trading.

    Usage:
        broker = IBKRBroker(host='127.0.0.1', port=7497, client_id=1)
        await broker.connect()
        portfolio_value = await broker.portfolio_value()
        fill = await broker.place_bracket(spec)
        await broker.close_position('NVDA', 'buy', 100)
        await broker.disconnect()
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 7497,
        client_id: int = 1,
        timeout: float = 10.0,
    ) -> None:
        self._host = host
        self._port = port
        self._client_id = client_id
        self._timeout = timeout
        self._ib: "ibs.IB | None" = None

    @property
    def connected(self) -> bool:
        return self._ib is not None and self._ib.isConnected()

    async def connect(self) -> bool:
        if not _IBS_AVAILABLE:
            logger.error("ib_insync not available — cannot connect")
            return False
        ib = ibs.IB()
        try:
            await asyncio.wait_for(
                ib.connectAsync(self._host, self._port, clientId=self._client_id),
                timeout=self._timeout,
            )
            self._ib = ib
            logger.info(f"IBKR connected: {self._host}:{self._port} (client {self._client_id})")
            return True
        except Exception as e:
            logger.error(f"IBKR connection failed: {e}")
            return False

    async def disconnect(self) -> None:
        if self._ib and self._ib.isConnected():
            self._ib.disconnect()
        self._ib = None

    async def portfolio_value(self) -> float | None:
        """Return total net liquidation value of the account."""
        if not self.connected:
            return None
        assert self._ib is not None
        try:
            await self._ib.reqAccountSummaryAsync()
            for av in self._ib.accountValues():
                if av.tag == "NetLiquidation" and av.currency == "USD":
                    return float(av.value)
        except Exception as e:
            logger.warning(f"portfolio_value failed: {e}")
        return None

    async def place_bracket(self, spec: "OrderSpec") -> Fill | None:  # noqa: F821
        """Submit a bracket order: market entry + stop loss.

        Returns Fill with the actual fill price and both order IDs.
        Stop is placed immediately after the entry fills.
        """
        if not self.connected:
            logger.error("IBKR not connected — cannot place order")
            return None
        assert self._ib is not None

        contract = ibs.Stock(spec.ticker, "SMART", "USD")
        try:
            await self._ib.qualifyContractsAsync(contract)
        except Exception as e:
            logger.error(f"Contract qualify failed for {spec.ticker}: {e}")
            return None

        action = "BUY" if spec.side == "buy" else "SELL"
        stop_action = "SELL" if spec.side == "buy" else "BUY"

        # Market order for entry
        entry_order = ibs.MarketOrder(action, spec.shares)
        entry_trade = self._ib.placeOrder(contract, entry_order)
        logger.info(f"IBKR: placed {action} {spec.shares} {spec.ticker} (market)")

        # Wait for fill (up to 30s)
        try:
            await asyncio.wait_for(
                self._wait_fill(entry_trade),
                timeout=30.0,
            )
        except asyncio.TimeoutError:
            logger.error(f"Entry order for {spec.ticker} timed out waiting for fill")
            return None

        fill_price = entry_trade.orderStatus.avgFillPrice or spec.entry_price

        # Stop loss order
        stop_order = ibs.StopOrder(stop_action, spec.shares, spec.stop_price)
        stop_trade = self._ib.placeOrder(contract, stop_order)
        logger.info(
            f"IBKR: placed stop {stop_action} {spec.shares} {spec.ticker} "
            f"@ ${spec.stop_price:.2f}"
        )

        return Fill(
            ticker=spec.ticker,
            side=spec.side,
            shares=spec.shares,
            avg_price=round(fill_price, 4),
            order_id=entry_trade.order.orderId,
            stop_order_id=stop_trade.order.orderId,
        )

    async def close_position(
        self,
        ticker: str,
        side: str,
        shares: int,
        stop_order_id: int | None = None,
    ) -> float | None:
        """Market-close a position and cancel its stop. Returns fill price."""
        if not self.connected:
            logger.error("IBKR not connected — cannot close position")
            return None
        assert self._ib is not None

        # Cancel stop first to avoid double-fill
        if stop_order_id is not None:
            try:
                open_trades = {t.order.orderId: t for t in self._ib.openTrades()}
                if stop_order_id in open_trades:
                    self._ib.cancelOrder(open_trades[stop_order_id].order)
                    logger.info(f"IBKR: cancelled stop order {stop_order_id} for {ticker}")
            except Exception as e:
                logger.warning(f"Could not cancel stop {stop_order_id}: {e}")

        contract = ibs.Stock(ticker, "SMART", "USD")
        close_action = "SELL" if side == "buy" else "BUY"
        close_order = ibs.MarketOrder(close_action, shares)
        close_trade = self._ib.placeOrder(contract, close_order)
        logger.info(f"IBKR: closing {ticker} — {close_action} {shares} (market)")

        try:
            await asyncio.wait_for(self._wait_fill(close_trade), timeout=30.0)
        except asyncio.TimeoutError:
            logger.error(f"Close order for {ticker} timed out")
            return None

        return round(close_trade.orderStatus.avgFillPrice or 0.0, 4)

    @staticmethod
    async def _wait_fill(trade: "ibs.Trade") -> None:
        """Await until the trade reaches a filled/cancelled terminal state."""
        while not trade.isDone():
            await asyncio.sleep(0.2)
