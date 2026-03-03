"""Tests for app.routers.webhooks — CRUD, validation, and auth checks.

Uses the TestClient + auth fixtures from conftest.py.
"""
from __future__ import annotations



from app import models


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_webhook(client, auth_hdrs, **overrides):
    """Helper to create a webhook and return the response JSON."""
    payload = {
        "name": "CI Hook",
        "url": "https://ci.example.com/hook",
        "events": ["lab.deploy_complete"],
        "enabled": True,
    }
    payload.update(overrides)
    resp = client.post("/webhooks", json=payload, headers=auth_hdrs)
    return resp


# ===========================================================================
# 1. Create webhook — happy path
# ===========================================================================

class TestCreateWebhook:

    def test_create_webhook_success(self, test_client, auth_headers):
        resp = _create_webhook(test_client, auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "CI Hook"
        assert data["url"] == "https://ci.example.com/hook"
        assert data["events"] == ["lab.deploy_complete"]
        assert data["enabled"] is True
        assert data["has_secret"] is False
        assert "id" in data

    def test_create_webhook_with_secret(self, test_client, auth_headers):
        resp = _create_webhook(
            test_client, auth_headers,
            secret="my-secret-key",
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["has_secret"] is True

    def test_create_webhook_with_custom_headers(self, test_client, auth_headers):
        resp = _create_webhook(
            test_client, auth_headers,
            headers={"X-Custom": "value"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["headers"] == {"X-Custom": "value"}


# ===========================================================================
# 2. List webhooks
# ===========================================================================

class TestListWebhooks:

    def test_list_webhooks_empty(self, test_client, auth_headers):
        resp = test_client.get("/webhooks", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["webhooks"] == []

    def test_list_webhooks_returns_created(self, test_client, auth_headers):
        _create_webhook(test_client, auth_headers, name="Hook A")
        _create_webhook(test_client, auth_headers, name="Hook B")

        resp = test_client.get("/webhooks", headers=auth_headers)
        assert resp.status_code == 200
        names = [w["name"] for w in resp.json()["webhooks"]]
        assert "Hook A" in names
        assert "Hook B" in names


# ===========================================================================
# 3. Get webhook
# ===========================================================================

class TestGetWebhook:

    def test_get_existing_webhook(self, test_client, auth_headers):
        create_resp = _create_webhook(test_client, auth_headers)
        wh_id = create_resp.json()["id"]

        resp = test_client.get(f"/webhooks/{wh_id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["id"] == wh_id

    def test_get_nonexistent_webhook_returns_404(self, test_client, auth_headers):
        resp = test_client.get("/webhooks/nonexistent-id", headers=auth_headers)
        assert resp.status_code == 404


# ===========================================================================
# 4. Update webhook
# ===========================================================================

class TestUpdateWebhook:

    def test_update_name(self, test_client, auth_headers):
        create_resp = _create_webhook(test_client, auth_headers)
        wh_id = create_resp.json()["id"]

        resp = test_client.put(
            f"/webhooks/{wh_id}",
            json={"name": "Renamed"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Renamed"

    def test_update_url_invalid_returns_400(self, test_client, auth_headers):
        create_resp = _create_webhook(test_client, auth_headers)
        wh_id = create_resp.json()["id"]

        resp = test_client.put(
            f"/webhooks/{wh_id}",
            json={"url": "ftp://bad-scheme.example.com"},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "http://" in resp.json()["detail"]

    def test_update_events_invalid_returns_400(self, test_client, auth_headers):
        create_resp = _create_webhook(test_client, auth_headers)
        wh_id = create_resp.json()["id"]

        resp = test_client.put(
            f"/webhooks/{wh_id}",
            json={"events": ["bogus.event"]},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "Invalid event types" in resp.json()["detail"]

    def test_update_nonexistent_webhook_returns_404(self, test_client, auth_headers):
        resp = test_client.put(
            "/webhooks/nonexistent",
            json={"name": "X"},
            headers=auth_headers,
        )
        assert resp.status_code == 404


# ===========================================================================
# 5. Delete webhook
# ===========================================================================

class TestDeleteWebhook:

    def test_delete_webhook(self, test_client, auth_headers):
        create_resp = _create_webhook(test_client, auth_headers)
        wh_id = create_resp.json()["id"]

        resp = test_client.delete(f"/webhooks/{wh_id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

        # Confirm it's gone
        get_resp = test_client.get(f"/webhooks/{wh_id}", headers=auth_headers)
        assert get_resp.status_code == 404

    def test_delete_nonexistent_returns_404(self, test_client, auth_headers):
        resp = test_client.delete("/webhooks/nonexistent", headers=auth_headers)
        assert resp.status_code == 404


# ===========================================================================
# 6. URL validation on create
# ===========================================================================

class TestUrlValidation:

    def test_create_with_invalid_url_scheme(self, test_client, auth_headers):
        resp = _create_webhook(
            test_client, auth_headers,
            url="ftp://example.com/hook",
        )
        assert resp.status_code == 400
        assert "http://" in resp.json()["detail"]

    def test_create_with_invalid_event_type(self, test_client, auth_headers):
        resp = _create_webhook(
            test_client, auth_headers,
            events=["not.a.real.event"],
        )
        assert resp.status_code == 400
        assert "Invalid event types" in resp.json()["detail"]


# ===========================================================================
# 7. Auth / permission requirements
# ===========================================================================

class TestAuthRequirements:

    def test_list_webhooks_requires_auth(self, test_client):
        resp = test_client.get("/webhooks")
        assert resp.status_code in (401, 403)

    def test_create_webhook_requires_auth(self, test_client):
        resp = test_client.post(
            "/webhooks",
            json={
                "name": "X",
                "url": "https://example.com",
                "events": ["job.completed"],
            },
        )
        assert resp.status_code in (401, 403)

    def test_other_user_cannot_see_webhook(
        self, test_client, test_db, auth_headers, monkeypatch,
    ):
        """A webhook created by user A should not be visible to user B."""
        from app.auth import create_access_token, hash_password

        # Create webhook as user A
        create_resp = _create_webhook(test_client, auth_headers)
        assert create_resp.status_code == 200
        wh_id = create_resp.json()["id"]

        # Create user B
        user_b = models.User(
            username="otheruser",
            email="other@example.com",
            hashed_password=hash_password("password"),
            is_active=True,
            global_role="operator",
        )
        test_db.add(user_b)
        test_db.commit()
        test_db.refresh(user_b)

        token_b = create_access_token(user_b.id)
        headers_b = {"Authorization": f"Bearer {token_b}"}

        # User B should get 404 for user A's webhook
        resp = test_client.get(f"/webhooks/{wh_id}", headers=headers_b)
        assert resp.status_code == 404

        # User B's list should be empty
        list_resp = test_client.get("/webhooks", headers=headers_b)
        assert list_resp.status_code == 200
        assert list_resp.json()["webhooks"] == []
