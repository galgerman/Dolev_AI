"""Paper-trading lifecycle: open/close positions on approved signals.

When broker.enabled=true in settings.yaml, orders are routed through
IBKRBroker (real fills on paper account). Otherwise falls back to
yfinance simulation — useful for development and when IBKR is offline.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from dolev_ai.db import (
    PaperPositionRow,
    SignalApprovalRow,
    SignalRow,
    get_approval,
    open_positions,
    position_for_ticker,
    save_signal,
    update_approval_status,
)
from dolev_ai.models import Signal
from dolev_ai.prices import get_price_async

if TYPE_CHECKING:
    from dolev_ai.broker.ibkr import IBKRBroker
    from dolev_ai.broker.risk import RiskManager
    from dolev_ai.events import EventBus

logger = logging.getLogger(__name__)


class ActionKind(str, Enum):
    PROPOSE_OPEN = "propose_open"
    PROPOSE_CLOSE = "propose_close"
    NOOP = "noop"


@dataclass
class PaperAction:
    kind: ActionKind
    signal: Signal
    position: PaperPositionRow | None = None

    @classmethod
    def propose_open(cls, sig: Signal) -> "PaperAction":
        return cls(kind=ActionKind.PROPOSE_OPEN, signal=sig)

    @classmethod
    def propose_close(cls, sig: Signal, pos: PaperPositionRow) -> "PaperAction":
        return cls(kind=ActionKind.PROPOSE_CLOSE, signal=sig, position=pos)

    @classmethod
    def noop(cls, sig: Signal) -> "PaperAction":
        return cls(kind=ActionKind.NOOP, signal=sig)


def handle_signal(sig: Signal, session: Session) -> PaperAction:
    """Decide what to do when a new signal fires for a ticker we may hold."""
    pos = position_for_ticker(session, sig.ticker)
    if pos is None:
        return PaperAction.propose_open(sig)
    if pos.side == sig.side:
        logger.info(f"Ignoring {sig.side} signal for {sig.ticker}: already holding {pos.side}")
        return PaperAction.noop(sig)
    return PaperAction.propose_close(sig, pos)


async def auto_execute(
    sig: Signal,
    session: Session,
    event_bus: "EventBus | None" = None,
    broker: "IBKRBroker | None" = None,
    risk: "RiskManager | None" = None,
) -> tuple[PaperPositionRow | None, str]:
    """Open or close a position without human approval (high-conviction gradient signals).

    Persists the signal to DB first so we have a signal_id to attach to the position.
    Returns (position_row, status_text) — status_text is the Telegram-friendly summary.
    """
    pos = position_for_ticker(session, sig.ticker)
    signal_id = save_signal(session, sig)

    if pos is None:
        # Open new position
        new_pos = await open_position(signal_id, sig, session, event_bus, broker=broker, risk=risk)
        if new_pos is None:
            return None, f"⚠️ Auto-execute failed for ${sig.ticker} (risk/broker blocked)"
        shares_str = f"{new_pos.shares} sh" if new_pos.shares else ""
        stop_str = f"stop=${new_pos.stop_price:.2f}" if new_pos.stop_price else ""
        return new_pos, (
            f"📈 Auto-{sig.side.upper()} {shares_str} ${sig.ticker} @ ${new_pos.entry_price:.2f}  {stop_str}\n"
            f"_{sig.rationale}_" if sig.rationale else
            f"📈 Auto-{sig.side.upper()} {shares_str} ${sig.ticker} @ ${new_pos.entry_price:.2f}  {stop_str}"
        )

    if pos.side == sig.side:
        return None, f"⏸ Already holding {pos.side.upper()} ${sig.ticker} — no action"

    # Opposite side → auto-close
    closed = await close_position(pos.id, signal_id, session, event_bus, broker=broker)
    if closed is None:
        return None, f"⚠️ Auto-close failed for ${sig.ticker}"
    dollar_str = f"  (${closed.pnl_dollars:+,.2f})" if closed.pnl_dollars is not None else ""
    return closed, (
        f"📉 Auto-closed ${sig.ticker} @ ${closed.exit_price:.2f}  "
        f"P&L: {closed.pnl_pct:+.2%}{dollar_str}"
    )


async def open_position(
    signal_id: int,
    sig: Signal,
    session: Session,
    event_bus: "EventBus | None" = None,
    broker: "IBKRBroker | None" = None,
    risk: "RiskManager | None" = None,
) -> PaperPositionRow | None:
    price = await get_price_async(sig.ticker)
    if price is None:
        logger.error(f"Cannot open position for {sig.ticker}: price unavailable")
        return None

    shares: int | None = None
    stop_price: float | None = None
    ibkr_order_id: int | None = None
    ibkr_stop_order_id: int | None = None

    if broker and broker.connected and risk:
        # IBKR path: real order with risk sizing + stop
        portfolio_val = await broker.portfolio_value() or 0.0
        open_count = len(open_positions(session))
        spec = risk.build_order(sig.ticker, sig.side, price, portfolio_val, open_count)
        if spec is None:
            logger.warning(f"Risk gate blocked {sig.ticker} — position not opened")
            return None

        fill = await broker.place_bracket(spec)
        if fill is None:
            logger.error(f"IBKR order failed for {sig.ticker}")
            return None

        price = fill.avg_price
        shares = fill.shares
        stop_price = spec.stop_price
        ibkr_order_id = fill.order_id
        ibkr_stop_order_id = fill.stop_order_id
        logger.info(
            f"IBKR fill: {sig.side.upper()} {shares} {sig.ticker} @ ${price:.2f}  "
            f"stop=${stop_price:.2f}"
        )
    elif risk:
        # Simulation path with risk sizing (no real broker)
        portfolio_val = 100_000.0  # IBKR paper default
        open_count = len(open_positions(session))
        spec = risk.build_order(sig.ticker, sig.side, price, portfolio_val, open_count)
        if spec is None:
            return None
        shares = spec.shares
        stop_price = spec.stop_price

    pos = PaperPositionRow(
        ticker=sig.ticker,
        side=sig.side,
        entry_signal_id=signal_id,
        entry_price=price,
        shares=shares,
        stop_price=stop_price,
        ibkr_order_id=ibkr_order_id,
        ibkr_stop_order_id=ibkr_stop_order_id,
        opened_at=datetime.utcnow(),
        status="open",
    )
    session.add(pos)
    session.commit()

    mode = "IBKR" if ibkr_order_id else "sim"
    logger.info(
        f"Opened [{mode}] {sig.side.upper()} {sig.ticker} @ ${price:.2f}"
        + (f"  {shares} shares  stop=${stop_price:.2f}" if shares else "")
    )

    if event_bus:
        await event_bus.publish({
            "type": "position.opened",
            "ticker": sig.ticker,
            "side": sig.side,
            "entry_price": price,
            "shares": shares,
            "stop_price": stop_price,
            "position_id": pos.id,
            "mode": mode,
        })
    return pos


async def close_position(
    position_id: int,
    exit_signal_id: int,
    session: Session,
    event_bus: "EventBus | None" = None,
    synthesizer=None,
    broker: "IBKRBroker | None" = None,
) -> PaperPositionRow | None:
    pos = session.get(PaperPositionRow, position_id)
    if pos is None or pos.status != "open":
        logger.warning(f"Position {position_id} not found or already closed")
        return None

    if broker and broker.connected and pos.ibkr_order_id is not None:
        # IBKR path: cancel stop, market-close
        exit_price = await broker.close_position(
            pos.ticker, pos.side,
            pos.shares or 1,
            pos.ibkr_stop_order_id,
        )
        if exit_price is None:
            logger.error(f"IBKR close failed for {pos.ticker}")
            return None
        price = exit_price
    else:
        price = await get_price_async(pos.ticker)
        if price is None:
            logger.error(f"Cannot close position {position_id}: price unavailable")
            return None

    pnl_pct = compute_pnl_pct(pos.entry_price, price, pos.side)
    shares_count = pos.shares if isinstance(pos.shares, int) else 0
    pnl_dollars = (price - pos.entry_price) * shares_count * (1 if pos.side == "buy" else -1)

    pos.exit_signal_id = exit_signal_id
    pos.exit_price = price
    pos.closed_at = datetime.utcnow()
    pos.pnl_pct = pnl_pct
    pos.pnl_dollars = round(pnl_dollars, 2)
    pos.status = "closed"
    session.commit()

    shares_int = pos.shares if isinstance(pos.shares, int) else None
    logger.info(
        f"Closed {pos.side.upper()} {pos.ticker} @ ${price:.2f} "
        f"(entry ${pos.entry_price:.2f}  P&L {pnl_pct:+.2%}"
        + (f"  ${pnl_dollars:+,.2f}" if shares_int else "") + ")"
    )

    if synthesizer:
        try:
            retro = synthesizer.analyze_retrospective(pos)
            pos.retrospective = retro
            pos.retrospective_at = datetime.utcnow()
            session.commit()
        except Exception as e:
            logger.warning(f"Retrospective generation failed: {e}")

    if event_bus:
        await event_bus.publish({
            "type": "position.closed",
            "ticker": pos.ticker,
            "side": pos.side,
            "entry_price": pos.entry_price,
            "exit_price": price,
            "pnl_pct": pnl_pct,
            "pnl_dollars": pnl_dollars,
            "shares": pos.shares,
            "position_id": pos.id,
            "retrospective": pos.retrospective,
        })
    return pos


def compute_pnl_pct(entry: float, exit_price: float, side: str) -> float:
    raw = (exit_price - entry) / entry
    return raw if side == "buy" else -raw


async def handle_approval_callback(
    approval_id: int,
    decision: str,
    session: Session,
    event_bus: "EventBus | None" = None,
    synthesizer=None,
    broker: "IBKRBroker | None" = None,
    risk: "RiskManager | None" = None,
) -> str:
    """Process a user decision from Telegram or dashboard. Returns ack message text."""
    approval = get_approval(session, approval_id)
    if approval is None:
        return "Approval not found."
    if approval.status != "pending":
        return f"Already {approval.status}."

    update_approval_status(session, approval_id, decision)

    if decision != "approved":
        verb = "Rejected" if decision == "rejected" else "Expired (no response)"
        return f"⏸ {verb} — no position opened."

    if approval.kind == "open":
        from dolev_ai.db import SignalRow
        sig_row = session.get(SignalRow, approval.signal_id)
        if sig_row is None:
            return "Signal no longer available."
        sig = Signal(
            ticker=sig_row.ticker,
            side=sig_row.side,
            conviction=sig_row.conviction,
            suggested_size_pct=sig_row.suggested_size_pct,
            rationale=sig_row.rationale,
            key_drivers=[],
            generated_at=sig_row.generated_at,
        )
        pos = await open_position(
            approval.signal_id, sig, session, event_bus, broker=broker, risk=risk
        )
        if pos is None:
            return "Could not open position — check logs."
        shares_str = f"  {pos.shares} shares" if pos.shares else ""
        stop_str = f"  stop=${pos.stop_price:.2f}" if pos.stop_price else ""
        return f"✅ Opened {sig.side.upper()} ${sig.ticker} @ ${pos.entry_price:.2f}{shares_str}{stop_str}"

    elif approval.kind == "close":
        if approval.position_id is None:
            return "No position linked to this approval."
        pos = await close_position(
            approval.position_id, approval.signal_id, session, event_bus,
            synthesizer, broker=broker,
        )
        if pos is None:
            return "Could not close position."
        dollar_str = f"  (${pos.pnl_dollars:+,.2f})" if pos.pnl_dollars is not None else ""
        retro_hint = f"\n_{pos.retrospective}_" if pos.retrospective else ""
        return (
            f"✅ Closed ${pos.ticker} @ ${pos.exit_price:.2f}  "
            f"P&L: {pos.pnl_pct:+.2%}{dollar_str}{retro_hint}"
        )

    return "Unknown approval kind."
