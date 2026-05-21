"""Tests for in-process agent lifecycle controls."""
from datetime import datetime

import pytest

from dolev_ai.main import Agent


class FakeSource:
    def __init__(self) -> None:
        self.starts = 0
        self.stops = 0

    async def start(self) -> None:
        self.starts += 1

    async def stop(self) -> None:
        self.stops += 1


class FakeScheduler:
    def __init__(self) -> None:
        self.jobs = []
        self.started = False
        self.shutdown_wait = None

    def add_job(self, func, trigger, **kwargs) -> None:
        self.jobs.append((func, trigger, kwargs))

    def start(self) -> None:
        self.started = True

    def shutdown(self, wait: bool) -> None:
        self.shutdown_wait = wait
        self.started = False


def _agent(monkeypatch):
    schedulers = []

    def scheduler_factory():
        scheduler = FakeScheduler()
        schedulers.append(scheduler)
        return scheduler

    monkeypatch.setattr("dolev_ai.main.AsyncIOScheduler", scheduler_factory)
    monkeypatch.setattr("dolev_ai.main.datetime", _FixedDateTime)
    agent = Agent({
        "poll_cadence_minutes": 5,
        "eval_cadence_minutes": 2,
        "playwright": {"headless": True},
        "trust_graph": {"score_threshold": 5.0},
    }, dry_run=True)
    source = FakeSource()
    agent._source = source
    return agent, source, schedulers


class _FixedDateTime(datetime):
    @classmethod
    def utcnow(cls):
        return cls(2026, 5, 21, 20, 0, 0)


@pytest.mark.asyncio
async def test_agent_start_stop_worker_is_idempotent(monkeypatch):
    agent, source, schedulers = _agent(monkeypatch)

    assert agent.status() == {"running": False, "started_at": None, "mode": "dry-run"}
    assert await agent.start_worker() is True
    assert await agent.start_worker() is False

    status = agent.status()
    assert status["running"] is True
    assert status["started_at"] == "2026-05-21T20:00:00"
    assert status["mode"] == "dry-run"
    assert source.starts == 1
    assert len(schedulers) == 1
    assert len(schedulers[0].jobs) == 2

    assert await agent.stop_worker() is True
    assert await agent.stop_worker() is False
    assert agent.status()["running"] is False
    assert source.stops == 1
    assert schedulers[0].shutdown_wait is False
    assert agent.event_bus.replay_events() == [{
        "type": "collection.finished",
        "total": 0,
        "completed": 0,
        "current_handle": None,
        "tweets_found": 0,
        "tickers_found": 0,
    }]
