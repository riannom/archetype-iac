"""Additional tests for api/app/routers/webhooks.py — deliveries and test endpoint.

Covers:
- GET /{webhook_id}/deliveries — success, empty, not found, pagination
- POST /{webhook_id}/test — success, error paths, not found
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_webhook_in_db(
    db: Session, owner_id: str, **overrides,
) -> models.Webhook:
    """Insert a Webhook directly into the database."""
    defaults = {
        "owner_id": owner_id,
        "name": "Test Hook",
        "url": "https://example.com/hook",
        "events": json.dumps(["lab.deploy_complete"]),
        "enabled": True,
    }
    defaults.update(overrides)
    wh = models.Webhook(**defaults)
    db.add(wh)
    db.commit()
    db.refresh(wh)
    return wh


def _create_delivery(
    db: Session, webhook_id: str, event_type: str = "lab.deploy_complete",
    success: bool = True, status_code: int = 200,
) -> models.WebhookDelivery:
    """Insert a WebhookDelivery row."""
    delivery = models.WebhookDelivery(
        webhook_id=webhook_id,
        event_type=event_type,
        payload=json.dumps({"event": event_type}),
        success=success,
        status_code=status_code,
        duration_ms=150,
    )
    db.add(delivery)
    db.commit()
    db.refresh(delivery)
    return delivery


# ===========================================================================
# GET /{webhook_id}/deliveries
# ===========================================================================


class TestListWebhookDeliveries:

    def test_list_deliveries_success(
        self, test_client: TestClient, test_db: Session,
        test_user: models.User, auth_headers: dict,
    ):
        """Returns deliveries for a webhook owned by the user."""
        wh = _create_webhook_in_db(test_db, test_user.id)
        _create_delivery(test_db, wh.id, success=True, status_code=200)
        _create_delivery(test_db, wh.id, success=False, status_code=500)

        resp = test_client.get(
            f"/webhooks/{wh.id}/deliveries",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "deliveries" in data
        assert len(data["deliveries"]) == 2

    def test_list_deliveries_empty(
        self, test_client: TestClient, test_db: Session,
        test_user: models.User, auth_headers: dict,
    ):
        """Webhook with no deliveries returns empty list."""
        wh = _create_webhook_in_db(test_db, test_user.id)

        resp = test_client.get(
            f"/webhooks/{wh.id}/deliveries",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["deliveries"] == []

    def test_list_deliveries_webhook_not_found(
        self, test_client: TestClient, auth_headers: dict,
    ):
        resp = test_client.get(
            "/webhooks/nonexistent-id/deliveries",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_list_deliveries_pagination(
        self, test_client: TestClient, test_db: Session,
        test_user: models.User, auth_headers: dict,
    ):
        """Pagination with limit and offset works correctly."""
        wh = _create_webhook_in_db(test_db, test_user.id)
        for i in range(5):
            _create_delivery(test_db, wh.id)

        # Request only 2 deliveries
        resp = test_client.get(
            f"/webhooks/{wh.id}/deliveries?limit=2&offset=0",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["deliveries"]) == 2

        # Request with offset
        resp2 = test_client.get(
            f"/webhooks/{wh.id}/deliveries?limit=2&offset=2",
            headers=auth_headers,
        )
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert len(data2["deliveries"]) == 2

    def test_list_deliveries_other_user_returns_404(
        self, test_client: TestClient, test_db: Session,
        test_user: models.User, auth_headers: dict,
    ):
        """A webhook owned by another user returns 404."""
        from app.auth import hash_password

        other_user = models.User(
            username="webhook_other",
            email="webhook_other@example.com",
            hashed_password=hash_password("pass"),
            is_active=True,
            global_role="operator",
        )
        test_db.add(other_user)
        test_db.commit()
        test_db.refresh(other_user)

        wh = _create_webhook_in_db(test_db, other_user.id)

        resp = test_client.get(
            f"/webhooks/{wh.id}/deliveries",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_list_deliveries_requires_auth(self, test_client: TestClient):
        resp = test_client.get("/webhooks/some-id/deliveries")
        assert resp.status_code in (401, 403)


# ===========================================================================
# POST /{webhook_id}/test
# ===========================================================================


class TestTestWebhookEndpoint:

    def test_test_webhook_success(
        self, test_client: TestClient, test_db: Session,
        test_user: models.User, auth_headers: dict, monkeypatch,
    ):
        """Successful test delivery returns success=True."""
        wh = _create_webhook_in_db(test_db, test_user.id)

        import app.webhooks as webhooks_mod

        async def _fake_test_webhook(webhook):
            return (True, 200, None, 45)

        monkeypatch.setattr(webhooks_mod, "test_webhook", _fake_test_webhook)

        resp = test_client.post(
            f"/webhooks/{wh.id}/test",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["status_code"] == 200
        assert data["error"] is None

    def test_test_webhook_delivery_failure(
        self, test_client: TestClient, test_db: Session,
        test_user: models.User, auth_headers: dict, monkeypatch,
    ):
        """Failed test delivery returns success=False with error message."""
        wh = _create_webhook_in_db(test_db, test_user.id)

        import app.webhooks as webhooks_mod

        async def _fake_test_webhook(webhook):
            return (False, None, "Connection refused", 0)

        monkeypatch.setattr(webhooks_mod, "test_webhook", _fake_test_webhook)

        resp = test_client.post(
            f"/webhooks/{wh.id}/test",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error"] == "Connection refused"

    def test_test_webhook_not_found(
        self, test_client: TestClient, auth_headers: dict,
    ):
        resp = test_client.post(
            "/webhooks/nonexistent-id/test",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_test_webhook_other_user_returns_404(
        self, test_client: TestClient, test_db: Session,
        test_user: models.User, auth_headers: dict,
    ):
        """Testing another user's webhook returns 404."""
        from app.auth import hash_password

        other_user = models.User(
            username="wh_other2",
            email="wh_other2@example.com",
            hashed_password=hash_password("pass"),
            is_active=True,
            global_role="operator",
        )
        test_db.add(other_user)
        test_db.commit()
        test_db.refresh(other_user)

        wh = _create_webhook_in_db(test_db, other_user.id)

        resp = test_client.post(
            f"/webhooks/{wh.id}/test",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_test_webhook_requires_auth(self, test_client: TestClient):
        resp = test_client.post("/webhooks/some-id/test")
        assert resp.status_code in (401, 403)
