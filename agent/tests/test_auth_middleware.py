"""Tests for AgentAuthMiddleware in agent/main.py."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent.config import settings
from agent.main import app


@pytest.fixture()
def _enable_auth(monkeypatch):
    """Enable agent auth with a known secret."""
    monkeypatch.setattr(settings, "controller_secret", "test-secret")
    yield
    monkeypatch.setattr(settings, "controller_secret", "")


@pytest.fixture()
def client():
    """TestClient that does NOT disable auth (controller_secret untouched)."""
    return TestClient(app, raise_server_exceptions=False)


class TestAgentAuthMiddleware:
    """Verify bearer-token validation on the agent HTTP middleware."""

    @pytest.mark.usefixtures("_enable_auth")
    def test_valid_bearer_token(self, client: TestClient):
        resp = client.get("/info", headers={"Authorization": "Bearer test-secret"})
        assert resp.status_code == 200

    @pytest.mark.usefixtures("_enable_auth")
    def test_invalid_bearer_token(self, client: TestClient):
        resp = client.get("/info", headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 403
        assert "Invalid authorization" in resp.json()["detail"]

    @pytest.mark.usefixtures("_enable_auth")
    def test_missing_authorization_header(self, client: TestClient):
        resp = client.get("/info")
        assert resp.status_code == 403
        assert "Missing authorization" in resp.json()["detail"]

    def test_empty_secret_disables_auth(self, client: TestClient, monkeypatch):
        monkeypatch.setattr(settings, "controller_secret", "")
        resp = client.get("/info")
        assert resp.status_code == 200

    @pytest.mark.usefixtures("_enable_auth")
    def test_exempt_path_health(self, client: TestClient):
        resp = client.get("/health")
        assert resp.status_code == 200

    @pytest.mark.usefixtures("_enable_auth")
    def test_exempt_path_healthz(self, client: TestClient):
        # /healthz is in EXEMPT_PATHS so middleware should not reject it,
        # but there's no handler so we expect 404 (not 403).
        resp = client.get("/healthz")
        assert resp.status_code != 403
