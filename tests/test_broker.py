"""Tests for RiskManager and IBKRBroker (mocked connection)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dolev_ai.broker.risk import OrderSpec, RiskManager


# ── RiskManager ───────────────────────────────────────────────────────────────

class TestRiskManager:
    def setup_method(self):
        self.risk = RiskManager(max_position_pct=2.0, stop_loss_pct=2.0, max_open_positions=5)

    def test_size_2pct_of_100k(self):
        # 2% of $100k = $2000 budget; at $50/share → 40 shares
        assert self.risk.size(100_000.0, 50.0) == 40

    def test_size_truncates_to_whole_shares(self):
        # 2% of $100k = $2000; at $33/share → 60.6 → 60 shares
        assert self.risk.size(100_000.0, 33.0) == 60

    def test_size_zero_price_returns_zero(self):
        assert self.risk.size(100_000.0, 0.0) == 0

    def test_size_zero_portfolio_returns_zero(self):
        assert self.risk.size(0.0, 50.0) == 0

    def test_stop_price_buy(self):
        # Buy at $100, stop 2% below → $98
        stop = self.risk.stop_price(100.0, "buy")
        assert stop == pytest.approx(98.0)

    def test_stop_price_sell(self):
        # Short at $100, stop 2% above → $102
        stop = self.risk.stop_price(100.0, "sell")
        assert stop == pytest.approx(102.0)

    def test_build_order_happy_path(self):
        spec = self.risk.build_order("NVDA", "buy", 100.0, 100_000.0, 0)
        assert spec is not None
        assert spec.shares == 20           # 2% of $100k / $100 = 20
        assert spec.stop_price == pytest.approx(98.0)
        assert spec.dollar_value == pytest.approx(2000.0)

    def test_build_order_blocked_at_max_positions(self):
        spec = self.risk.build_order("NVDA", "buy", 100.0, 100_000.0, 5)
        assert spec is None

    def test_build_order_blocked_at_4_when_max_is_5(self):
        # Still allowed at 4 open positions
        spec = self.risk.build_order("NVDA", "buy", 100.0, 100_000.0, 4)
        assert spec is not None

    def test_build_order_high_price_gives_at_least_1_share(self):
        # $2000 budget at $1500/share → 1 share
        spec = self.risk.build_order("BRK", "buy", 1500.0, 100_000.0, 0)
        assert spec is not None
        assert spec.shares == 1

    def test_build_order_price_exceeds_budget(self):
        # $2000 budget at $3000/share → 0 shares → blocked
        spec = self.risk.build_order("BRK", "buy", 3000.0, 100_000.0, 0)
        assert spec is None


# ── IBKRBroker (mocked) ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ibkr_connect_success():
    from dolev_ai.broker.ibkr import IBKRBroker
    broker = IBKRBroker(port=7497)
    mock_ib = MagicMock()
    mock_ib.isConnected.return_value = True
    mock_ib.connectAsync = AsyncMock()
    with patch("dolev_ai.broker.ibkr.ibs") as mock_ibs:
        mock_ibs.IB.return_value = mock_ib
        result = await broker.connect()
    assert result is True
    assert broker.connected


@pytest.mark.asyncio
async def test_ibkr_connect_failure():
    from dolev_ai.broker.ibkr import IBKRBroker
    broker = IBKRBroker(port=7497)
    with patch("dolev_ai.broker.ibkr.ibs") as mock_ibs:
        mock_ib = MagicMock()
        mock_ib.connectAsync = AsyncMock(side_effect=ConnectionRefusedError("no TWS"))
        mock_ibs.IB.return_value = mock_ib
        result = await broker.connect()
    assert result is False
    assert not broker.connected


@pytest.mark.asyncio
async def test_ibkr_portfolio_value():
    from dolev_ai.broker.ibkr import IBKRBroker
    broker = IBKRBroker()
    mock_ib = MagicMock()
    mock_ib.isConnected.return_value = True
    mock_ib.reqAccountSummaryAsync = AsyncMock()
    av = MagicMock()
    av.tag = "NetLiquidation"
    av.currency = "USD"
    av.value = "1000000.00"
    mock_ib.accountValues.return_value = [av]
    broker._ib = mock_ib
    val = await broker.portfolio_value()
    assert val == pytest.approx(1_000_000.0)


@pytest.mark.asyncio
async def test_ibkr_not_connected_blocks_order():
    from dolev_ai.broker.ibkr import IBKRBroker
    from dolev_ai.broker.risk import OrderSpec
    broker = IBKRBroker()  # never connected
    spec = OrderSpec("NVDA", "buy", 10, 900.0, 882.0, 9000.0)
    fill = await broker.place_bracket(spec)
    assert fill is None


@pytest.mark.asyncio
async def test_ibkr_disconnect_when_not_connected():
    from dolev_ai.broker.ibkr import IBKRBroker
    broker = IBKRBroker()
    # Should not raise
    await broker.disconnect()
