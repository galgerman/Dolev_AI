"""Tests for the WebSocket /api/stream endpoint."""
import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from dolev_ai.events import EventBus
from dolev_ai.web.server import create_app


@pytest.fixture
def ws_setup(tmp_path):
    db_path = tmp_path / "ws_test.db"
    from dolev_ai.db import init_db
    factory = init_db(db_path)
    bus = EventBus()
    app = create_app(event_bus=bus, session_factory=factory, threshold=5.0)
    client = TestClient(app)
    return client, bus


def test_ws_connects_and_receives_snapshot(ws_setup):
    client, bus = ws_setup
    with client.websocket_connect("/api/stream") as ws:
        data = json.loads(ws.receive_text())
        assert data["type"] == "snapshot"
        assert "top_tickers" in data
        assert "recent_signals" in data
        assert "graph" in data


def test_ws_receives_published_event(ws_setup):
    import asyncio
    client, bus = ws_setup
    with client.websocket_connect("/api/stream") as ws:
        # Receive snapshot first
        ws.receive_text()
        # Publish an event synchronously via the event loop
        asyncio.get_event_loop().run_until_complete(
            bus.publish({"type": "ticker.score_updated", "ticker": "NVDA", "score": 6.5,
                         "voices": 3, "tweet_count": 5, "threshold": 5.0, "threshold_progress": 1.3})
        )
        msg = json.loads(ws.receive_text())
        assert msg["type"] == "ticker.score_updated"
        assert msg["ticker"] == "NVDA"
