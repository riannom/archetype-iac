from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app import models
from app.webhooks import (
    build_webhook_payload,
    sign_payload,
    deliver_webhook,
    dispatch_webhook_event,
)


def test_build_webhook_payload_includes_job_duration(sample_lab: models.Lab) -> None:
    started_at = datetime.now(timezone.utc) - timedelta(seconds=30)
    completed_at = datetime.now(timezone.utc)
    job = models.Job(
        id="job-1",
        lab_id=sample_lab.id,
        action="deploy",
        status="completed",
        started_at=started_at,
        completed_at=completed_at,
    )

    payload = build_webhook_payload(
        event_type="lab.deploy_complete",
        lab=sample_lab,
        job=job,
        nodes=[{"id": "n1"}],
    )

    assert payload["event"] == "lab.deploy_complete"
    assert payload["lab"]["id"] == sample_lab.id
    assert payload["job"]["id"] == job.id
    assert payload["job"]["duration_seconds"] == pytest.approx(30, rel=0.1)
    assert payload["nodes"] == [{"id": "n1"}]


def test_sign_payload_is_deterministic() -> None:
    payload = '{"ok":true}'
    secret = "super-secret"
    signature = sign_payload(payload, secret)
    assert signature.startswith("sha256=")
    assert signature == sign_payload(payload, secret)


@pytest.mark.asyncio
async def test_deliver_webhook_success(monkeypatch) -> None:
    webhook = models.Webhook(
        id="wh-1",
        owner_id="user-1",
        name="test",
        url="https://example.test/webhook",
        events=json.dumps(["lab.deploy_complete"]),
        secret="secret",
        headers=json.dumps({"X-Custom": "ok"}),
        enabled=True,
    )

    captured = {}

    class DummyResponse:
        status_code = 200

    class DummyClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, content, headers, timeout):
            captured["url"] = url
            captured["content"] = content
            captured["headers"] = headers
            captured["timeout"] = timeout
            return DummyResponse()

    monkeypatch.setattr("app.webhooks.httpx.AsyncClient", lambda: DummyClient())

    success, status, error, _ = await deliver_webhook(
        webhook, {"event": "lab.deploy_complete", "id": "evt-1"}
    )

    assert success is True
    assert status == 200
    assert error is None
    assert captured["url"] == webhook.url
    assert "X-Webhook-Signature" in captured["headers"]
    assert captured["headers"]["X-Custom"] == "ok"


@pytest.mark.asyncio
async def test_dispatch_webhook_event_filters_and_logs(
    test_db, test_engine, sample_lab: models.Lab, test_user: models.User, monkeypatch
) -> None:
    from contextlib import contextmanager

    @contextmanager
    def override_get_session():
        yield test_db

    monkeypatch.setattr("app.webhooks.get_session", override_get_session)

    webhook_ok = models.Webhook(
        id="wh-ok",
        owner_id=test_user.id,
        name="ok",
        url="https://example.test/ok",
        events=json.dumps(["lab.deploy_complete"]),
        enabled=True,
    )
    webhook_skip = models.Webhook(
        id="wh-skip",
        owner_id=test_user.id,
        name="skip",
        url="https://example.test/skip",
        events=json.dumps(["job.failed"]),
        enabled=True,
    )
    test_db.add_all([webhook_ok, webhook_skip])
    test_db.commit()

    async def fake_deliver(webhook, payload, timeout=30.0):
        return True, 200, None, 5

    monkeypatch.setattr("app.webhooks.deliver_webhook", fake_deliver)

    triggered = await dispatch_webhook_event(
        event_type="lab.deploy_complete",
        lab_id=sample_lab.id,
        lab=sample_lab,
    )

    assert triggered == ["wh-ok"]

    refreshed = test_db.get(models.Webhook, "wh-ok")
    assert refreshed.last_delivery_status == "success"
    assert refreshed.last_delivery_at is not None
