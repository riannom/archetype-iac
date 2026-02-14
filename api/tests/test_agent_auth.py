"""Tests for verify_agent_secret dependency from api/app/agent_auth.py."""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models
from app.config import settings


class TestVerifyAgentSecret:
    """Verify agent auth dependency on callback endpoints."""

    def test_valid_token(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_host: models.Host,
        monkeypatch,
    ):
        monkeypatch.setattr(settings, "agent_secret", "test-secret")
        resp = test_client.post(
            "/callbacks/job/nonexistent-job",
            json={"status": "completed", "result": {}},
            headers={"Authorization": "Bearer test-secret"},
        )
        # 404 (job not found) proves auth passed
        assert resp.status_code != 403

    def test_invalid_token(
        self,
        test_client: TestClient,
        test_db: Session,
        monkeypatch,
    ):
        monkeypatch.setattr(settings, "agent_secret", "test-secret")
        resp = test_client.post(
            "/callbacks/job/nonexistent-job",
            json={"status": "completed", "result": {}},
            headers={"Authorization": "Bearer wrong"},
        )
        assert resp.status_code == 403
        assert "Invalid agent authorization" in resp.json()["detail"]

    def test_missing_header(
        self,
        test_client: TestClient,
        test_db: Session,
        monkeypatch,
    ):
        monkeypatch.setattr(settings, "agent_secret", "test-secret")
        resp = test_client.post(
            "/callbacks/job/nonexistent-job",
            json={"status": "completed", "result": {}},
        )
        assert resp.status_code == 403
        assert "Missing agent authorization" in resp.json()["detail"]

    def test_empty_secret_disables_auth(
        self,
        test_client: TestClient,
        test_db: Session,
        monkeypatch,
    ):
        monkeypatch.setattr(settings, "agent_secret", "")
        resp = test_client.post(
            "/callbacks/job/nonexistent-job",
            json={"status": "completed", "result": {}},
        )
        # No auth required, passes through to handler (404 = job not found)
        assert resp.status_code != 403
