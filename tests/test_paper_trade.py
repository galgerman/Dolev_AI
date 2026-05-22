"""Unit tests for paper_trade.py — no Telegram, no yfinance, no LLM needed."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dolev_ai.models import Signal
from dolev_ai.paper_trade import (
    ActionKind,
    PaperAction,
    compute_pnl_pct,
    handle_signal,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _sig(ticker="NVDA", side="buy") -> Signal:
    return Signal(
        ticker=ticker, side=side, conviction=0.8,
        suggested_size_pct=0.05, rationale="test",
        key_drivers=[], generated_at=datetime.utcnow(),
    )


def _mock_session(open_pos=None):
    from dolev_ai.db import PaperPositionRow
    session = MagicMock()
    session.get.return_value = None

    if open_pos is None:
        session.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
    else:
        session.query.return_value.filter.return_value.order_by.return_value.first.return_value = open_pos
    return session


# ── compute_pnl_pct ───────────────────────────────────────────────────────────

def test_pnl_buy_profit():
    assert compute_pnl_pct(100.0, 110.0, "buy") == pytest.approx(0.10)

def test_pnl_buy_loss():
    assert compute_pnl_pct(100.0, 90.0, "buy") == pytest.approx(-0.10)

def test_pnl_sell_profit():
    # Short: price goes down → profit
    assert compute_pnl_pct(100.0, 90.0, "sell") == pytest.approx(0.10)

def test_pnl_sell_loss():
    # Short: price goes up → loss
    assert compute_pnl_pct(100.0, 110.0, "sell") == pytest.approx(-0.10)


# ── handle_signal ─────────────────────────────────────────────────────────────

def test_no_open_position_proposes_open():
    session = _mock_session(open_pos=None)
    action = handle_signal(_sig("NVDA", "buy"), session)
    assert action.kind == ActionKind.PROPOSE_OPEN

def test_same_side_existing_position_is_noop():
    from dolev_ai.db import PaperPositionRow
    pos = MagicMock(spec=PaperPositionRow)
    pos.side = "buy"
    pos.ticker = "NVDA"
    session = _mock_session(open_pos=pos)
    action = handle_signal(_sig("NVDA", "buy"), session)
    assert action.kind == ActionKind.NOOP

def test_opposite_side_proposes_close():
    from dolev_ai.db import PaperPositionRow
    pos = MagicMock(spec=PaperPositionRow)
    pos.side = "buy"
    pos.ticker = "NVDA"
    session = _mock_session(open_pos=pos)
    action = handle_signal(_sig("NVDA", "sell"), session)
    assert action.kind == ActionKind.PROPOSE_CLOSE
    assert action.position is pos


# ── open_position (async) ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_open_position_creates_row():
    from dolev_ai.paper_trade import open_position

    session = MagicMock()
    session.add = MagicMock()
    session.commit = MagicMock()

    with patch("dolev_ai.paper_trade.get_price_async", AsyncMock(return_value=450.0)):
        pos = await open_position(1, _sig("LMT", "buy"), session)

    assert pos is not None
    assert pos.ticker == "LMT"
    assert pos.entry_price == 450.0
    assert pos.status == "open"
    session.add.assert_called_once()
    session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_open_position_returns_none_when_price_unavailable():
    from dolev_ai.paper_trade import open_position

    session = MagicMock()
    with patch("dolev_ai.paper_trade.get_price_async", AsyncMock(return_value=None)):
        pos = await open_position(1, _sig("LMT", "buy"), session)
    assert pos is None


# ── close_position (async) ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_close_position_computes_pnl():
    from dolev_ai.db import PaperPositionRow
    from dolev_ai.paper_trade import close_position

    pos = MagicMock(spec=PaperPositionRow)
    pos.ticker = "NVDA"
    pos.side = "buy"
    pos.entry_price = 400.0
    pos.status = "open"
    pos.opened_at = datetime.utcnow()
    pos.retrospective = None

    session = MagicMock()
    session.get.return_value = pos
    session.commit = MagicMock()

    with patch("dolev_ai.paper_trade.get_price_async", AsyncMock(return_value=440.0)):
        closed = await close_position(7, 2, session)

    assert closed is pos
    assert closed.exit_price == 440.0
    assert closed.pnl_pct == pytest.approx(0.10)
    assert closed.status == "closed"


# ── handle_approval_callback ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_approval_rejected_returns_ack():
    from dolev_ai.db import SignalApprovalRow
    from dolev_ai.paper_trade import handle_approval_callback

    approval = MagicMock(spec=SignalApprovalRow)
    approval.status = "pending"
    approval.kind = "open"
    approval.signal_id = 1
    approval.position_id = None

    session = MagicMock()
    session.get.return_value = approval

    with patch("dolev_ai.paper_trade.update_approval_status") as mock_upd:
        ack = await handle_approval_callback(1, "rejected", session)

    assert "Rejected" in ack or "rejected" in ack.lower()
    mock_upd.assert_called_once_with(session, 1, "rejected")


@pytest.mark.asyncio
async def test_approval_already_resolved():
    from dolev_ai.db import SignalApprovalRow
    from dolev_ai.paper_trade import handle_approval_callback

    approval = MagicMock(spec=SignalApprovalRow)
    approval.status = "approved"

    session = MagicMock()
    session.get.return_value = approval

    ack = await handle_approval_callback(1, "approved", session)
    assert "approved" in ack.lower()
