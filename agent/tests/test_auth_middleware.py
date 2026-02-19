"""Tests for AgentAuthMiddleware in agent/main.py."""
from __future__ import annotations

from pathlib import Path

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
        # /healthz is in EXEMPT_PATHS and has a handler; should be reachable
        # without auth and return healthy.
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json().get("status") == "ok"

    @pytest.mark.usefixtures("_enable_auth")
    def test_exempt_path_poap_prefix(self, client: TestClient):
        # POAP paths must be reachable without controller auth headers.
        # This endpoint returns 400 due to invalid path params, but should never
        # be blocked by middleware as 403.
        resp = client.get("/poap/invalid-lab/invalid.node/script.py")
        assert resp.status_code != 403

    @pytest.mark.usefixtures("_enable_auth")
    def test_exempt_path_poap_with_existing_config(self, client: TestClient, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(settings, "workspace_path", str(tmp_path))
        cfg = tmp_path / "lab1" / "configs" / "n9k1"
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "startup-config").write_text("hostname n9k1\n", encoding="utf-8")

        resp = client.get("/poap/lab1/n9k1/startup-config")
        assert resp.status_code == 200
        assert "hostname n9k1" in resp.text
