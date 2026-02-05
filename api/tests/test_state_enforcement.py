from __future__ import annotations

from datetime import datetime, timedelta, timezone
import pytest

from app import models
from app.state import JobStatus
import app.tasks.state_enforcement as state_enforcement


def test_calculate_backoff(monkeypatch) -> None:
    monkeypatch.setattr(state_enforcement.settings, "state_enforcement_retry_backoff", 5)
    monkeypatch.setattr(state_enforcement.settings, "state_enforcement_cooldown", 30)

    assert state_enforcement._calculate_backoff(0) == 5
    assert state_enforcement._calculate_backoff(2) == 20
    assert state_enforcement._calculate_backoff(10) == 30


def test_should_skip_enforcement_max_retries(monkeypatch) -> None:
    monkeypatch.setattr(state_enforcement.settings, "state_enforcement_max_retries", 3)
    node_state = models.NodeState(
        lab_id="lab",
        node_id="r1",
        node_name="r1",
        desired_state="running",
        actual_state="stopped",
        enforcement_attempts=3,
        enforcement_failed_at=datetime.now(timezone.utc),
    )

    skip, reason = state_enforcement._should_skip_enforcement(node_state)
    assert skip
    assert "max retries" in reason


def test_should_skip_enforcement_cooldown(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(state_enforcement.settings, "state_enforcement_max_retries", 5)
    monkeypatch.setattr(state_enforcement.settings, "state_enforcement_crash_cooldown", 60)

    node_state = models.NodeState(
        lab_id="lab",
        node_id="r1",
        node_name="r1",
        desired_state="running",
        actual_state="error",
        enforcement_attempts=1,
        enforcement_failed_at=now,
    )

    skip, reason = state_enforcement._should_skip_enforcement(node_state)
    assert skip
    assert "crash cooldown" in reason


def test_should_skip_enforcement_backoff(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(state_enforcement.settings, "state_enforcement_max_retries", 5)
    monkeypatch.setattr(state_enforcement.settings, "state_enforcement_retry_backoff", 10)
    monkeypatch.setattr(state_enforcement.settings, "state_enforcement_cooldown", 60)

    node_state = models.NodeState(
        lab_id="lab",
        node_id="r1",
        node_name="r1",
        desired_state="running",
        actual_state="stopped",
        enforcement_attempts=2,
        last_enforcement_at=now,
    )

    skip, reason = state_enforcement._should_skip_enforcement(node_state)
    assert skip
    assert "backoff" in reason


def test_cooldown_helpers(monkeypatch) -> None:
    calls = []

    class FakeRedis:
        def exists(self, key):
            calls.append(("exists", key))
            return 1

        def setex(self, key, ttl, value):
            calls.append(("setex", key, ttl, value))

    monkeypatch.setattr(state_enforcement, "get_redis", lambda: FakeRedis())

    assert state_enforcement._is_on_cooldown("lab1", "r1")
    state_enforcement._set_cooldown("lab1", "r1")

    assert calls[0][0] == "exists"
    assert calls[1][0] == "setex"


def test_has_active_job(test_db) -> None:
    job = models.Job(
        lab_id="lab1",
        action="node:stop:r1",
        status=JobStatus.QUEUED.value,
    )
    test_db.add(job)
    test_db.commit()

    assert state_enforcement._has_active_job(test_db, "lab1")
    assert state_enforcement._has_active_job(test_db, "lab1", "r1")
    assert not state_enforcement._has_active_job(test_db, "lab1", "r2")
