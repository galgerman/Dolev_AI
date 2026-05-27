"""Tests for PositionTracker (MFE/MAE polling)."""
from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from dolev_ai.db import PaperPositionRow, init_db
from dolev_ai.position_tracker import PositionTracker


@pytest.fixture
def session_factory(tmp_path):
    return init_db(tmp_path / "pos.db")


def _open_position(session_factory, ticker="NVDA", side="buy", entry=100.0) -> int:
    with session_factory() as session:
        pos = PaperPositionRow(
            ticker=ticker, side=side,
            entry_signal_id=1, entry_price=entry,
            status="open", opened_at=datetime.utcnow(),
        )
        session.add(pos)
        session.commit()
        return pos.id


@pytest.mark.asyncio
async def test_poll_records_mfe_on_favorable_move(session_factory):
    pid = _open_position(session_factory)
    async def _price(_t): return 102.0
    tracker = PositionTracker(session_factory, _price, poll_seconds=0.1)
    updated = await tracker.poll_once(pid)
    assert updated is True

    with session_factory() as session:
        pos = session.get(PaperPositionRow, pid)
        assert pos.mfe_pct == 2.0
        assert pos.mae_pct == 2.0  # only one sample → both = same


@pytest.mark.asyncio
async def test_poll_short_side_directional_flip(session_factory):
    pid = _open_position(session_factory, side="sell")
    async def _price(_t): return 98.0   # short profits when price drops
    tracker = PositionTracker(session_factory, _price)
    await tracker.poll_once(pid)

    with session_factory() as session:
        pos = session.get(PaperPositionRow, pid)
        # Short: (98-100)/100 = -2%, then flipped → +2% in MFE direction
        assert pos.mfe_pct == 2.0


@pytest.mark.asyncio
async def test_watermarks_track_extremes_over_time(session_factory):
    pid = _open_position(session_factory)
    prices = [101.0, 103.0, 99.0, 100.5]  # MFE=+3, MAE=-1
    idx = 0
    async def _price(_t):
        nonlocal idx
        p = prices[idx]
        idx += 1
        return p

    tracker = PositionTracker(session_factory, _price)
    for _ in prices:
        await tracker.poll_once(pid)

    with session_factory() as session:
        pos = session.get(PaperPositionRow, pid)
        assert pos.mfe_pct == 3.0
        assert pos.mae_pct == -1.0


@pytest.mark.asyncio
async def test_poll_skips_closed_position(session_factory):
    pid = _open_position(session_factory)
    with session_factory() as session:
        pos = session.get(PaperPositionRow, pid)
        pos.status = "closed"
        session.commit()
    async def _price(_t): return 200.0
    tracker = PositionTracker(session_factory, _price)
    updated = await tracker.poll_once(pid)
    assert updated is False


@pytest.mark.asyncio
async def test_track_untrack_lifecycle(session_factory):
    pid = _open_position(session_factory)
    async def _price(_t): return 101.0
    tracker = PositionTracker(session_factory, _price, poll_seconds=0.05)
    tracker.track(pid)
    assert tracker.is_tracking(pid)
    # Idempotent
    tracker.track(pid)
    await asyncio.sleep(0.12)
    await tracker.untrack(pid)
    assert not tracker.is_tracking(pid)
    # Idempotent stop
    await tracker.untrack(pid)
