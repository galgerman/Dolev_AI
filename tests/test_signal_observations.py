"""Tests for the signal_observations table + helpers."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from dolev_ai.db import (
    SignalObservationRow,
    due_observations,
    init_db,
    record_observation_return,
    save_signal_observation,
)


@pytest.fixture
def session(tmp_path):
    db_path = tmp_path / "test.db"
    SessionLocal = init_db(db_path)
    return SessionLocal()


def test_save_observation_populates_next_due(session):
    now = datetime.utcnow()
    obs_id = save_signal_observation(
        session,
        ticker="NVDA", side="buy",
        entry_price=100.0, confidence=75.0,
        components={"gradient": 13.0}, features={"gradient": 0.5},
        horizons_seconds=[30, 60, 180],
        generated_at=now,
    )
    row = session.get(SignalObservationRow, obs_id)
    assert row.ticker == "NVDA"
    assert row.status == "pending"
    assert row.next_due_at == now + timedelta(seconds=30)
    assert json.loads(row.horizons_remaining_json) == [30, 60, 180]


def test_due_observations_filters_correctly(session):
    now = datetime.utcnow()
    # Due 1 minute ago — should be returned
    save_signal_observation(
        session, ticker="A", side="buy", entry_price=10.0,
        confidence=70.0, components={}, features={},
        horizons_seconds=[30], generated_at=now - timedelta(minutes=1, seconds=30),
    )
    # Due 5 minutes from now — should not
    save_signal_observation(
        session, ticker="B", side="buy", entry_price=10.0,
        confidence=70.0, components={}, features={},
        horizons_seconds=[300], generated_at=now,
    )
    due = due_observations(session, now=now)
    tickers = {r.ticker for r in due}
    assert "A" in tickers
    assert "B" not in tickers


def test_record_return_advances_horizons(session):
    now = datetime.utcnow()
    obs_id = save_signal_observation(
        session, ticker="NVDA", side="buy", entry_price=100.0,
        confidence=70.0, components={}, features={},
        horizons_seconds=[30, 60, 180], generated_at=now,
    )
    # First horizon: +30s, price moved to 101.5 → +1.5% return
    record_observation_return(session, obs_id, 30, current_price=101.5)
    row = session.get(SignalObservationRow, obs_id)
    assert row.return_30s == 1.5
    assert row.mfe_pct == 1.5
    assert row.mae_pct == 1.5
    assert row.status == "pending"
    assert json.loads(row.horizons_remaining_json) == [60, 180]
    assert row.next_due_at == now + timedelta(seconds=60)


def test_record_return_completes_after_last_horizon(session):
    obs_id = save_signal_observation(
        session, ticker="NVDA", side="buy", entry_price=100.0,
        confidence=70.0, components={}, features={},
        horizons_seconds=[30],
    )
    record_observation_return(session, obs_id, 30, current_price=99.0)
    row = session.get(SignalObservationRow, obs_id)
    assert row.return_30s == -1.0
    assert row.status == "complete"


def test_short_signal_return_is_directional(session):
    """For a sell signal, price dropping should yield POSITIVE return."""
    obs_id = save_signal_observation(
        session, ticker="NVDA", side="sell", entry_price=100.0,
        confidence=70.0, components={}, features={},
        horizons_seconds=[30],
    )
    record_observation_return(session, obs_id, 30, current_price=98.0)
    row = session.get(SignalObservationRow, obs_id)
    # Price dropped 2% on a short → return = +2%
    assert row.return_30s == 2.0


def test_mfe_mae_track_extremes(session):
    """MFE/MAE should track max favorable / adverse across all observations."""
    obs_id = save_signal_observation(
        session, ticker="NVDA", side="buy", entry_price=100.0,
        confidence=70.0, components={}, features={},
        horizons_seconds=[30, 60, 180],
    )
    record_observation_return(session, obs_id, 30, current_price=102.0)  # +2%
    record_observation_return(session, obs_id, 60, current_price=99.0)   # -1%
    record_observation_return(session, obs_id, 180, current_price=101.0) # +1%
    row = session.get(SignalObservationRow, obs_id)
    assert row.mfe_pct == 2.0   # max favorable
    assert row.mae_pct == -1.0  # max adverse


def test_record_on_complete_row_is_noop(session):
    obs_id = save_signal_observation(
        session, ticker="NVDA", side="buy", entry_price=100.0,
        confidence=70.0, components={}, features={},
        horizons_seconds=[30],
    )
    record_observation_return(session, obs_id, 30, current_price=101.0)
    # Already complete — second call should return None / be ignored
    result = record_observation_return(session, obs_id, 60, current_price=102.0)
    assert result is None
