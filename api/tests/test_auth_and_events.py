from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import models
from app.dependencies import require_admin_role, require_operator_role, require_super_admin_role
from app.enums import GlobalRole
from app.events.cleanup_events import CLEANUP_CHANNEL, CleanupEvent, CleanupEventType
from app.events import publisher as events_publisher
from app.services.audit import AuditService


class FakeRedisAsync:
    def __init__(self):
        self.published = []
        self.closed = False

    async def publish(self, channel: str, payload: str):
        self.published.append((channel, payload))

    async def close(self):
        self.closed = True

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_publish_cleanup_event_success(monkeypatch):
    fake = FakeRedisAsync()

    async def fake_get_redis():
        return fake

    monkeypatch.setattr(events_publisher, "_get_redis", fake_get_redis)

    event = CleanupEvent(event_type=CleanupEventType.LAB_DELETED, lab_id="lab-1")
    await events_publisher.publish_cleanup_event(event)

    assert fake.published
    channel, payload = fake.published[0]
    assert channel == CLEANUP_CHANNEL
    assert "lab_deleted" in payload


@pytest.mark.asyncio
async def test_publish_cleanup_event_failure_is_suppressed(monkeypatch):
    class ExplodingRedis:
        async def publish(self, channel: str, payload: str):
            raise RuntimeError("boom")

    async def fake_get_redis():
        return ExplodingRedis()

    monkeypatch.setattr(events_publisher, "_get_redis", fake_get_redis)

    event = CleanupEvent(event_type=CleanupEventType.NODE_REMOVED, lab_id="lab-1")
    await events_publisher.publish_cleanup_event(event)


@pytest.mark.asyncio
async def test_emit_wrappers_forward_event(monkeypatch):
    events = []

    async def fake_publish(event: CleanupEvent):
        events.append(event)

    monkeypatch.setattr(events_publisher, "publish_cleanup_event", fake_publish)

    await events_publisher.emit_job_failed("lab-1", job_id="job-1", job_action="deploy")
    await events_publisher.emit_agent_offline("agent-1")

    assert [e.event_type for e in events] == [CleanupEventType.JOB_FAILED, CleanupEventType.AGENT_OFFLINE]


@pytest.mark.asyncio
async def test_close_publisher_closes_connection(monkeypatch):
    fake = FakeRedisAsync()
    events_publisher._publisher_redis = fake

    await events_publisher.close_publisher()

    assert fake.closed is True
    assert events_publisher._publisher_redis is None


def test_audit_service_logs_entry(test_db):
    AuditService.log(test_db, event_type="login_success", user_id="user-1")

    row = test_db.query(models.AuditLog).first()
    assert row is not None
    assert row.event_type == "login_success"
    assert row.user_id == "user-1"


def test_dependencies_require_roles():
    admin = SimpleNamespace(global_role=GlobalRole.ADMIN.value)
    super_admin = SimpleNamespace(global_role=GlobalRole.SUPER_ADMIN.value)
    operator = SimpleNamespace(global_role=GlobalRole.OPERATOR.value)

    assert require_operator_role(operator) is operator
    assert require_admin_role(admin) is admin
    assert require_super_admin_role(super_admin) is super_admin

    with pytest.raises(HTTPException):
        require_admin_role(operator)
