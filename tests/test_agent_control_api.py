"""Tests for local agent start/stop controls."""
from datetime import datetime

from fastapi.testclient import TestClient

from dolev_ai.db import init_db
from dolev_ai.events import EventBus
from dolev_ai.web.server import create_app


class FakeAgentControl:
    def __init__(self) -> None:
        self.running = False
        self.started_at = None
        self.starts = 0
        self.stops = 0

    def status(self) -> dict:
        return {
            "running": self.running,
            "started_at": self.started_at,
            "mode": "dry-run",
        }

    async def start_worker(self) -> bool:
        if self.running:
            return False
        self.running = True
        self.started_at = "2026-05-21T20:00:00"
        self.starts += 1
        return True

    async def stop_worker(self) -> bool:
        if not self.running:
            return False
        self.running = False
        self.stops += 1
        return True


def _client(tmp_path, control: FakeAgentControl) -> TestClient:
    return TestClient(create_app(
        event_bus=EventBus(),
        session_factory=init_db(tmp_path / "agent_control.db"),
        threshold=5.0,
        started_at=datetime(2026, 5, 21, 20, 0, 0),
        agent_control=control,
    ))


def test_agent_status_reports_controller_state(tmp_path):
    control = FakeAgentControl()
    client = _client(tmp_path, control)

    resp = client.get("/api/agent/status")

    assert resp.status_code == 200
    assert resp.json() == {
        "running": False,
        "started_at": None,
        "mode": "dry-run",
    }


def test_agent_start_is_idempotent(tmp_path):
    control = FakeAgentControl()
    client = _client(tmp_path, control)

    first = client.post("/api/agent/start")
    second = client.post("/api/agent/start")

    assert first.status_code == 200
    assert first.json()["started"] is True
    assert first.json()["running"] is True
    assert second.status_code == 200
    assert second.json()["started"] is False
    assert second.json()["running"] is True
    assert control.starts == 1


def test_agent_stop_is_idempotent(tmp_path):
    control = FakeAgentControl()
    client = _client(tmp_path, control)
    client.post("/api/agent/start")

    first = client.post("/api/agent/stop")
    second = client.post("/api/agent/stop")

    assert first.status_code == 200
    assert first.json()["stopped"] is True
    assert first.json()["running"] is False
    assert second.status_code == 200
    assert second.json()["stopped"] is False
    assert second.json()["running"] is False
    assert control.stops == 1
