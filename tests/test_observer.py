"""Tests for ForwardReturnObserver."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

import pytest

from dolev_ai.db import (
    SignalObservationRow,
    init_db,
    save_signal_observation,
)
from dolev_ai.observer import ForwardReturnObserver


@pytest.fixture
def session_factory(tmp_path):
    db_path = tmp_path / "obs.db"
    return init_db(db_path)


async def _fixed_price(ticker: str) -> float:
    return 105.0


@pytest.mark.asyncio
async def test_tick_records_return_when_horizon_due(session_factory):
    """A signal observation whose first horizon has passed should advance."""
    with session_factory() as session:
        obs_id = save_signal_observation(
            session,
            ticker="NVDA", side="buy",
            entry_price=100.0, confidence=70.0,
            components={}, features={},
            horizons_seconds=[30, 60, 180],
            generated_at=datetime.utcnow() - timedelta(seconds=45),
        )

    observer = ForwardReturnObserver(session_factory, _fixed_price, tick_seconds=1.0)
    processed = await observer.tick()
    assert processed == 1

    with session_factory() as session:
        row = session.get(SignalObservationRow, obs_id)
        assert row.return_30s == 5.0
        assert row.status == "pending"
        assert json.loads(row.horizons_remaining_json) == [60, 180]


@pytest.mark.asyncio
async def test_tick_skips_not_due_observations(session_factory):
    with session_factory() as session:
        save_signal_observation(
            session,
            ticker="NVDA", side="buy",
            entry_price=100.0, confidence=70.0,
            components={}, features={},
            horizons_seconds=[300],   # 5min — way in future
        )
    observer = ForwardReturnObserver(session_factory, _fixed_price, tick_seconds=1.0)
    processed = await observer.tick()
    assert processed == 0


@pytest.mark.asyncio
async def test_tick_handles_missing_price(session_factory):
    with session_factory() as session:
        obs_id = save_signal_observation(
            session,
            ticker="NVDA", side="buy",
            entry_price=100.0, confidence=70.0,
            components={}, features={},
            horizons_seconds=[30],
            generated_at=datetime.utcnow() - timedelta(seconds=60),
        )

    async def _none_price(_t):
        return None

    observer = ForwardReturnObserver(session_factory, _none_price)
    processed = await observer.tick()
    assert processed == 0

    with session_factory() as session:
        row = session.get(SignalObservationRow, obs_id)
        # Untouched — still pending, still has its horizon
        assert row.status == "pending"
        assert json.loads(row.horizons_remaining_json) == [30]


@pytest.mark.asyncio
async def test_tick_groups_by_ticker_one_fetch(session_factory):
    """Two observations on the same ticker should trigger a single price fetch."""
    now = datetime.utcnow() - timedelta(seconds=60)
    with session_factory() as session:
        save_signal_observation(
            session, ticker="NVDA", side="buy", entry_price=100.0,
            confidence=70.0, components={}, features={},
            horizons_seconds=[30], generated_at=now,
        )
        save_signal_observation(
            session, ticker="NVDA", side="sell", entry_price=100.0,
            confidence=70.0, components={}, features={},
            horizons_seconds=[30], generated_at=now,
        )

    call_count = 0
    async def _counting(_t):
        nonlocal call_count
        call_count += 1
        return 101.0

    observer = ForwardReturnObserver(session_factory, _counting)
    processed = await observer.tick()
    assert processed == 2
    assert call_count == 1   # single fetch for NVDA


@pytest.mark.asyncio
async def test_start_stop_lifecycle(session_factory):
    observer = ForwardReturnObserver(session_factory, _fixed_price, tick_seconds=0.05)
    observer.start()
    # Wait for at least one tick
    await asyncio.sleep(0.12)
    await observer.stop()
    # Idempotent
    await observer.stop()
