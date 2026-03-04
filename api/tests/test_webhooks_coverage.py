"""Tests for app.webhooks — payload building, signing, delivery, and dispatch."""
# ruff: noqa: E402
from __future__ import annotations

import hashlib
import hmac
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.webhooks import (
    build_webhook_payload,
    deliver_webhook,
    dispatch_webhook_event,
    log_delivery,
    sign_payload,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_webhook(**overrides) -> MagicMock:
    """Build a MagicMock that behaves like models.Webhook."""
    wh = MagicMock()
    wh.id = overrides.get("id", "wh-1")
    wh.owner_id = overrides.get("owner_id", "user-1")
    wh.lab_id = overrides.get("lab_id", None)
    wh.name = overrides.get("name", "CI Hook")
    wh.url = overrides.get("url", "https://example.com/hook")
    wh.events = overrides.get("events", '["lab.deploy_complete"]')
    wh.secret = overrides.get("secret", "s3cret")
    wh.headers = overrides.get("headers", None)
    wh.enabled = overrides.get("enabled", True)
    wh.last_delivery_at = None
    wh.last_delivery_status = None
    wh.last_delivery_error = None
    return wh


def _make_lab(**overrides) -> MagicMock:
    lab = MagicMock()
    lab.id = overrides.get("id", "lab-1")
    lab.name = overrides.get("name", "Test Lab")
    lab.state = overrides.get("state", "running")
    lab.owner_id = overrides.get("owner_id", "user-1")
    return lab


def _make_job(**overrides) -> MagicMock:
    job = MagicMock()
    job.id = overrides.get("id", "job-1")
    job.action = overrides.get("action", "up")
    job.status = overrides.get("status", "completed")
    job.started_at = overrides.get("started_at", datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc))
    job.completed_at = overrides.get("completed_at", datetime(2025, 1, 1, 0, 0, 30, tzinfo=timezone.utc))
    return job


# ---------------------------------------------------------------------------
# build_webhook_payload
# ---------------------------------------------------------------------------

class TestBuildWebhookPayload:
    def test_minimal_payload_has_required_keys(self):
        payload = build_webhook_payload("lab.deploy_complete")
        assert payload["event"] == "lab.deploy_complete"
        assert payload["id"].startswith("evt_")
        assert "timestamp" in payload
        # No lab/job/nodes keys when not provided
        assert "lab" not in payload
        assert "job" not in payload
        assert "nodes" not in payload

    def test_lab_included_when_provided(self):
        lab = _make_lab(id="lab-42", name="Fabric", state="running")
        payload = build_webhook_payload("lab.deploy_complete", lab=lab)
        assert payload["lab"] == {"id": "lab-42", "name": "Fabric", "state": "running"}

    def test_job_with_duration(self):
        job = _make_job(
            started_at=datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
            completed_at=datetime(2025, 1, 1, 0, 0, 45, tzinfo=timezone.utc),
        )
        payload = build_webhook_payload("job.completed", job=job)
        assert payload["job"]["duration_seconds"] == 45.0

    def test_job_without_timestamps_has_null_duration(self):
        job = _make_job(started_at=None, completed_at=None)
        payload = build_webhook_payload("job.completed", job=job)
        assert payload["job"]["duration_seconds"] is None

    def test_extra_fields_merged(self):
        payload = build_webhook_payload(
            "test", extra={"custom_key": "custom_value", "nested": {"a": 1}}
        )
        assert payload["custom_key"] == "custom_value"
        assert payload["nested"] == {"a": 1}


# ---------------------------------------------------------------------------
# sign_payload
# ---------------------------------------------------------------------------

class TestSignPayload:
    def test_returns_sha256_prefixed_hex(self):
        result = sign_payload("hello", "secret")
        assert result.startswith("sha256=")
        hex_part = result[len("sha256="):]
        # Valid hex string of correct length (SHA-256 = 64 hex chars)
        assert len(hex_part) == 64
        int(hex_part, 16)  # raises if not hex

    def test_matches_manual_hmac(self):
        payload_str = '{"event":"test"}'
        secret = "my-secret"
        expected = hmac.new(
            secret.encode(), payload_str.encode(), hashlib.sha256
        ).hexdigest()
        assert sign_payload(payload_str, secret) == f"sha256={expected}"


# ---------------------------------------------------------------------------
# deliver_webhook (async)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestDeliverWebhook:
    async def test_success_returns_true(self):
        wh = _make_webhook(secret="s3cret")
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("app.webhooks.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post = AsyncMock(return_value=mock_response)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            success, status, error, dur = await deliver_webhook(wh, {"event": "test"})
            assert success is True
            assert status == 200
            assert error is None
            assert dur >= 0

    async def test_non_2xx_returns_false(self):
        wh = _make_webhook()
        mock_response = MagicMock()
        mock_response.status_code = 500

        with patch("app.webhooks.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post = AsyncMock(return_value=mock_response)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            success, status, error, dur = await deliver_webhook(wh, {"event": "test"})
            assert success is False
            assert status == 500

    async def test_timeout_returns_error(self):
        wh = _make_webhook()

        with patch("app.webhooks.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            success, status, error, dur = await deliver_webhook(wh, {"event": "test"})
            assert success is False
            assert status is None
            assert error == "Request timed out"

    async def test_connect_error(self):
        wh = _make_webhook()

        with patch("app.webhooks.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            success, status, error, dur = await deliver_webhook(wh, {"event": "test"})
            assert success is False
            assert status is None
            assert "Connection error" in error

    async def test_generic_exception(self):
        wh = _make_webhook()

        with patch("app.webhooks.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post = AsyncMock(side_effect=RuntimeError("boom"))
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            success, status, error, dur = await deliver_webhook(wh, {"event": "test"})
            assert success is False
            assert "Unexpected error" in error

    async def test_invalid_headers_json_ignored(self):
        wh = _make_webhook(headers="not-valid-json", secret=None)
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("app.webhooks.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post = AsyncMock(return_value=mock_response)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            success, status, error, dur = await deliver_webhook(wh, {"event": "test"})
            assert success is True

    async def test_no_secret_skips_signature(self):
        wh = _make_webhook(secret=None)
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("app.webhooks.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post = AsyncMock(return_value=mock_response)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            await deliver_webhook(wh, {"event": "test"})

            # Inspect the headers passed to post()
            call_kwargs = instance.post.call_args
            headers_sent = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
            assert "X-Webhook-Signature" not in headers_sent


# ---------------------------------------------------------------------------
# log_delivery
# ---------------------------------------------------------------------------

class TestLogDelivery:
    def test_creates_delivery_and_updates_webhook(self):
        session = MagicMock()
        wh = _make_webhook()
        payload = {"event": "lab.deploy_complete", "lab": {"id": "lab-1"}, "job": {"id": "job-1"}}

        delivery = log_delivery(
            session, wh, "lab.deploy_complete", payload,
            success=True, status_code=200, error=None, duration_ms=123,
        )
        session.add.assert_called_once_with(delivery)
        session.commit.assert_called_once()
        assert wh.last_delivery_status == "success"
        assert wh.last_delivery_error is None

    def test_truncates_response_body_at_1000(self):
        session = MagicMock()
        wh = _make_webhook()
        long_body = "x" * 2000

        delivery = log_delivery(
            session, wh, "test", {"event": "test"},
            success=True, status_code=200, error=None, duration_ms=50,
            response_body=long_body,
        )
        assert len(delivery.response_body) == 1000

    def test_failed_delivery_sets_status(self):
        session = MagicMock()
        wh = _make_webhook()

        log_delivery(
            session, wh, "test", {"event": "test"},
            success=False, status_code=500, error="Server Error", duration_ms=100,
        )
        assert wh.last_delivery_status == "failed"
        assert wh.last_delivery_error == "Server Error"


# ---------------------------------------------------------------------------
# dispatch_webhook_event (async)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestDispatchWebhookEvent:
    async def test_no_user_id_returns_empty(self):
        """When no user_id and no lab, dispatch returns empty list."""
        with patch("app.webhooks.get_session") as mock_gs:
            mock_session = MagicMock()
            mock_gs.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_gs.return_value.__exit__ = MagicMock(return_value=False)
            mock_session.get.return_value = None  # no lab found

            result = await dispatch_webhook_event("lab.deploy_complete", lab_id="lab-x")
            assert result == []

    async def test_no_matching_webhooks_returns_empty(self):
        """When webhooks exist but none match event type, return empty."""
        lab = _make_lab()
        wh = _make_webhook(events='["job.completed"]')  # different event

        with patch("app.webhooks.get_session") as mock_gs:
            mock_session = MagicMock()
            mock_gs.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_gs.return_value.__exit__ = MagicMock(return_value=False)
            mock_session.query.return_value.filter.return_value.all.return_value = [wh]

            result = await dispatch_webhook_event(
                "lab.deploy_complete", lab=lab, user_id="user-1"
            )
            assert result == []

    async def test_lab_scope_filtering(self):
        """Webhook scoped to a different lab should be excluded."""
        lab = _make_lab(id="lab-1")
        wh = _make_webhook(
            events='["lab.deploy_complete"]',
            lab_id="lab-other",  # scoped to different lab
        )

        with patch("app.webhooks.get_session") as mock_gs:
            mock_session = MagicMock()
            mock_gs.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_gs.return_value.__exit__ = MagicMock(return_value=False)
            mock_session.query.return_value.filter.return_value.all.return_value = [wh]

            result = await dispatch_webhook_event(
                "lab.deploy_complete", lab_id="lab-1", lab=lab, user_id="user-1"
            )
            assert result == []
