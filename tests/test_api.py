"""Tests for the REST API endpoints."""
import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from dolev_ai.events import EventBus
from dolev_ai.web.server import create_app


@pytest.fixture
def client(tmp_path):
    """TestClient with an in-memory SQLite DB."""
    from dolev_ai.db import Base, init_db
    # Use a temp DB
    db_path = tmp_path / "test.db"
    session_factory = init_db(db_path)
    bus = EventBus()
    app = create_app(
        event_bus=bus,
        session_factory=session_factory,
        threshold=5.0,
        started_at=datetime(2024, 1, 15, 12, 0, 0),
    )
    return TestClient(app)


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["threshold"] == 5.0


def test_tickers_live_empty(client):
    resp = client.get("/api/tickers/live")
    assert resp.status_code == 200
    assert resp.json() == []


def test_ticker_drilldown_404(client):
    resp = client.get("/api/tickers/UNKNOWN")
    assert resp.status_code == 404


def test_signals_recent_empty(client):
    resp = client.get("/api/signals/recent")
    assert resp.status_code == 200
    assert resp.json() == []


def test_graph_snapshot_empty(client):
    resp = client.get("/api/graph/snapshot")
    assert resp.status_code == 200
    data = resp.json()
    assert "nodes" in data
    assert "edges" in data


def test_accounts_returns_list(client):
    resp = client.get("/api/accounts")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_tickers_live_with_data(client, tmp_path):
    """Seed DB with a ticker score row and verify it appears in /tickers/live."""
    from dolev_ai.db import init_db
    from dolev_ai.db import TickerScoreRow, Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    db_path = tmp_path / "test2.db"
    factory = init_db(db_path)
    bus = EventBus()
    app = create_app(event_bus=bus, session_factory=factory, threshold=5.0)
    tc = TestClient(app)

    # Seed a row
    with factory() as session:
        session.add(TickerScoreRow(
            ticker="NVDA", score=7.5,
            unique_credible_voices=4, tweet_count=10,
            window_start=datetime.utcnow(), window_end=datetime.utcnow(),
            top_tweet_urls=json.dumps(["https://x.com/test/status/1"]),
        ))
        session.commit()

    resp = tc.get("/api/tickers/live")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["ticker"] == "NVDA"
    assert data[0]["score"] == 7.5
    assert data[0]["threshold_progress"] == pytest.approx(7.5 / 5.0)
