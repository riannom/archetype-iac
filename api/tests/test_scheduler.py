from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from app import scheduler


class DummyConn:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, *args, **kwargs):
        return None


class DummyEngine:
    def __init__(self, pool=None):
        self.pool = pool

    def connect(self):
        return DummyConn()


@pytest.mark.asyncio
async def test_healthz_reports_monitor_counts(monkeypatch):
    pool = SimpleNamespace(
        size=lambda: 3,
        checkedin=lambda: 2,
        checkedout=lambda: 1,
        overflow=lambda: 0,
    )
    monkeypatch.setattr(scheduler.db, "engine", DummyEngine(pool=pool))

    task_active = asyncio.create_task(asyncio.sleep(0.1))
    task_done = asyncio.create_task(asyncio.sleep(0))
    await asyncio.sleep(0)

    scheduler._monitor_tasks = [task_active, task_done]

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/healthz",
        "headers": [],
        "client": ("127.0.0.1", 1234),
    }
    request = Request(scope)
    response = await scheduler.healthz(request)

    payload = json.loads(response.body)
    assert payload["status"] == "ok"
    assert payload["service"] == "scheduler"
    assert payload["monitors"]["total"] == 2
    assert payload["monitors"]["active"] == 1


@pytest.mark.asyncio
async def test_startup_starts_monitors(monkeypatch):
    started = []

    async def fake_supervised_task(_fn, name: str, *args, **kwargs):
        started.append(name)

    def fake_safe_create_task(coro, name=None, **kwargs):
        return asyncio.create_task(coro)

    monkeypatch.setattr(scheduler, "supervised_task", fake_supervised_task)
    monkeypatch.setattr(scheduler, "safe_create_task", fake_safe_create_task)
    monkeypatch.setattr(scheduler, "setup_asyncio_exception_handler", lambda: None)
    monkeypatch.setattr(scheduler.db, "engine", DummyEngine())

    scheduler._monitor_tasks = []
    monkeypatch.setattr(scheduler.settings, "cleanup_event_driven_enabled", True)

    await scheduler.startup()

    assert len(scheduler._monitor_tasks) == 8
    assert "cleanup_event_monitor" in started


@pytest.mark.asyncio
async def test_shutdown_cancels_tasks(monkeypatch):
    closed = {"called": False}

    async def fake_close_publisher():
        closed["called"] = True

    monkeypatch.setattr(scheduler, "close_publisher", fake_close_publisher)

    evt = asyncio.Event()

    async def waiter():
        await evt.wait()

    t1 = asyncio.create_task(waiter())
    t2 = asyncio.create_task(waiter())
    scheduler._monitor_tasks = [t1, t2]

    await scheduler.shutdown()

    assert all(t.cancelled() or t.done() for t in (t1, t2))
    assert closed["called"] is True


