"""Paper-trading lifecycle: open/close positions on approved signals."""
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
    get_approval,
    open_positions,
    position_for_ticker,
    update_approval_status,
)
from dolev_ai.models import Signal
from dolev_ai.prices import get_price_async

if TYPE_CHECKING:
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
    # Opposite side — propose close
    return PaperAction.propose_close(sig, pos)


async def open_position(
    signal_id: int,
    sig: Signal,
    session: Session,
    event_bus: "EventBus | None" = None,
) -> PaperPositionRow | None:
    price = await get_price_async(sig.ticker)
    if price is None:
        logger.error(f"Cannot open position for {sig.ticker}: price unavailable")
        return None
    pos = PaperPositionRow(
        ticker=sig.ticker,
        side=sig.side,
        entry_signal_id=signal_id,
        entry_price=price,
        opened_at=datetime.utcnow(),
        status="open",
    )
    session.add(pos)
    session.commit()
    logger.info(f"Opened paper {sig.side} {sig.ticker} @ ${price:.2f}")
    if event_bus:
        await event_bus.publish({
            "type": "position.opened",
            "ticker": sig.ticker,
            "side": sig.side,
            "entry_price": price,
            "position_id": pos.id,
        })
    return pos


async def close_position(
    position_id: int,
    exit_signal_id: int,
    session: Session,
    event_bus: "EventBus | None" = None,
    synthesizer=None,
) -> PaperPositionRow | None:
    pos = session.get(PaperPositionRow, position_id)
    if pos is None or pos.status != "open":
        logger.warning(f"Position {position_id} not found or already closed")
        return None

    price = await get_price_async(pos.ticker)
    if price is None:
        logger.error(f"Cannot close position {position_id}: price unavailable")
        return None

    pnl = compute_pnl_pct(pos.entry_price, price, pos.side)
    pos.exit_signal_id = exit_signal_id
    pos.exit_price = price
    pos.closed_at = datetime.utcnow()
    pos.pnl_pct = pnl
    pos.status = "closed"
    session.commit()

    logger.info(
        f"Closed paper {pos.side} {pos.ticker} @ ${price:.2f} "
        f"(entry ${pos.entry_price:.2f}, P&L {pnl:+.2%})"
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
            "pnl_pct": pnl,
            "position_id": pos.id,
            "retrospective": pos.retrospective,
        })
    return pos


def compute_pnl_pct(entry: float, exit_price: float, side: str) -> float:
    raw = (exit_price - entry) / entry
    return raw if side == "buy" else -raw


async def handle_approval_callback(
    approval_id: int,
    decision: str,           # 'approved' | 'rejected' | 'expired'
    session: Session,
    event_bus: "EventBus | None" = None,
    synthesizer=None,
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
        # Re-fetch the signal
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
        pos = await open_position(approval.signal_id, sig, session, event_bus)
        if pos is None:
            return "Could not fetch price — position not opened."
        return f"✅ Opened {sig.side.upper()} ${sig.ticker} @ ${pos.entry_price:.2f}"

    elif approval.kind == "close":
        if approval.position_id is None:
            return "No position linked to this approval."
        pos = await close_position(
            approval.position_id, approval.signal_id, session, event_bus, synthesizer
        )
        if pos is None:
            return "Could not close position."
        retro_hint = f"\n_{pos.retrospective}_" if pos.retrospective else ""
        return (
            f"✅ Closed ${pos.ticker} @ ${pos.exit_price:.2f}  "
            f"P&L: {pos.pnl_pct:+.2%}{retro_hint}"
        )

    return "Unknown approval kind."
