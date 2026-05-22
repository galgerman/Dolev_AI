"""End-of-day summary builder for Telegram."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from dolev_ai.db import (
    SignalRow,
    TickerScoreRow,
    approvals_on,
    open_positions,
    positions_closed_on,
    positions_opened_on,
)
from dolev_ai.prices import get_price

logger = logging.getLogger(__name__)


def build_eod_summary(session: Session, day_utc: datetime | None = None) -> str:
    day = day_utc or datetime.utcnow()

    # ── Signals fired today ───────────────────────────────────────────────────
    day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    signals = (
        session.query(SignalRow)
        .filter(SignalRow.generated_at >= day_start, SignalRow.generated_at < day_end)
        .order_by(SignalRow.generated_at.desc())
        .all()
    )

    # ── Approval outcomes ─────────────────────────────────────────────────────
    approvals = approvals_on(session, day)
    approved_n = sum(1 for a in approvals if a.status == "approved")
    rejected_n = sum(1 for a in approvals if a.status == "rejected")
    expired_n = sum(1 for a in approvals if a.status == "expired")

    # ── Positions ─────────────────────────────────────────────────────────────
    opened_today = positions_opened_on(session, day)
    closed_today = positions_closed_on(session, day)
    still_open = open_positions(session)

    # ── Top movers that didn't cross threshold ────────────────────────────────
    one_hour_ago = datetime.utcnow() - timedelta(hours=1)
    score_rows = (
        session.query(TickerScoreRow)
        .filter(TickerScoreRow.window_end >= one_hour_ago)
        .order_by(TickerScoreRow.window_end.desc())
        .all()
    )
    seen_tickers: set[str] = set()
    top_movers: list[TickerScoreRow] = []
    for r in score_rows:
        if r.ticker not in seen_tickers:
            seen_tickers.add(r.ticker)
            fired_tickers = {s.ticker for s in signals}
            if r.ticker not in fired_tickers and abs(r.score) > 0:
                top_movers.append(r)
    top_movers = sorted(top_movers, key=lambda r: abs(r.score), reverse=True)[:5]

    # ── Build message ─────────────────────────────────────────────────────────
    lines: list[str] = [
        f"📊 *End-of-Day Summary — {day.strftime('%b %d, %Y')}*",
        "",
    ]

    # Signals section
    if signals:
        buy_n = sum(1 for s in signals if s.side == "buy")
        sell_n = sum(1 for s in signals if s.side == "sell")
        lines.append(f"*Signals fired:* {len(signals)} (🟢 {buy_n} buy · 🔴 {sell_n} sell)")
        for s in signals[:5]:
            lines.append(f"  • {s.side.upper()} ${s.ticker} — {round(s.conviction * 100)}% conviction")
    else:
        lines.append("*Signals fired:* none today")

    lines.append("")

    # Approvals section
    if approvals:
        lines.append(f"*Approvals:* ✅ {approved_n} approved · ❌ {rejected_n} rejected · ⏱ {expired_n} expired")
    lines.append("")

    # Open positions with live P&L
    if still_open:
        lines.append(f"*Open positions ({len(still_open)}):*")
        for pos in still_open:
            price = get_price(pos.ticker)
            if price and pos.entry_price:
                from dolev_ai.paper_trade import compute_pnl_pct
                pnl = compute_pnl_pct(pos.entry_price, price, pos.side)
                pnl_str = f"{pnl:+.2%}"
                color = "🟢" if pnl >= 0 else "🔴"
            else:
                pnl_str = "n/a"
                color = "⚪"
            lines.append(
                f"  {color} {pos.side.upper()} ${pos.ticker} "
                f"@ ${pos.entry_price:.2f} → ${price:.2f if price else '?'} ({pnl_str})"
            )
    else:
        lines.append("*Open positions:* none")

    lines.append("")

    # Closed today with P&L
    if closed_today:
        total_pnl = sum(p.pnl_pct or 0 for p in closed_today) / len(closed_today)
        lines.append(f"*Closed today ({len(closed_today)}, avg P&L {total_pnl:+.2%}):*")
        for pos in closed_today:
            pnl_str = f"{pos.pnl_pct:+.2%}" if pos.pnl_pct is not None else "n/a"
            color = "🟢" if (pos.pnl_pct or 0) >= 0 else "🔴"
            lines.append(f"  {color} {pos.side.upper()} ${pos.ticker}: {pnl_str}")
            if pos.retrospective:
                lines.append(f"    _{pos.retrospective[:120]}…_")

    lines.append("")

    # Top movers below threshold
    if top_movers:
        lines.append("*Watching (below threshold):*")
        for r in top_movers:
            arrow = "↑" if r.score > 0 else "↓"
            lines.append(f"  • ${r.ticker} {arrow} {abs(r.score):.2f}  ({r.unique_credible_voices} voices)")

    return "\n".join(lines)
