"""Tests for main.py endpoints: /health, /healthz, /disk-usage, /metrics."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# /health endpoint
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    """Tests for GET /health."""

    def test_health_returns_200_with_auth(self, test_client, auth_headers, test_user):
        resp = test_client.get("/health", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["user"] == test_user.email

    def test_health_contains_timestamp(self, test_client, auth_headers, test_user):
        resp = test_client.get("/health", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "timestamp" in data
        # ISO format contains 'T' separator
        assert "T" in data["timestamp"]

    def test_health_without_auth_has_empty_user(self, test_client):
        """Without auth the middleware sets user to None, so user field is empty."""
        resp = test_client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["user"] == ""

    def test_health_admin_user(self, test_client, admin_auth_headers, admin_user):
        resp = test_client.get("/health", headers=admin_auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["user"] == admin_user.email


# ---------------------------------------------------------------------------
# /healthz endpoint (bypass middleware)
# ---------------------------------------------------------------------------


class TestHealthzEndpoint:
    """Tests for GET /healthz."""

    def test_healthz_returns_200(self, test_client):
        resp = test_client.get("/healthz")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_healthz_contains_timestamp(self, test_client):
        resp = test_client.get("/healthz")
        data = resp.json()
        assert "timestamp" in data

    def test_healthz_reports_db_status(self, test_client):
        resp = test_client.get("/healthz")
        data = resp.json()
        # db probe runs — may be "ok" or "error: ..." depending on test env
        assert "db" in data

    def test_healthz_reports_redis_status(self, test_client):
        resp = test_client.get("/healthz")
        data = resp.json()
        assert "redis" in data


# ---------------------------------------------------------------------------
# /disk-usage endpoint
# ---------------------------------------------------------------------------


class TestDiskUsageEndpoint:
    """Tests for GET /disk-usage."""

    def test_disk_usage_admin_returns_data(
        self, test_client, admin_auth_headers, admin_user, monkeypatch
    ):
        mock_status = {
            "disk": {"pressure": "nominal", "percent": 42.0},
            "memory": {"pressure": "nominal", "percent": 30.0},
        }
        monkeypatch.setattr(
            "app.services.resource_monitor.ResourceMonitor.get_status",
            staticmethod(lambda: mock_status),
        )
        resp = test_client.get("/disk-usage", headers=admin_auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["disk"]["percent"] == 42.0

    def test_disk_usage_non_admin_gets_403(
        self, test_client, auth_headers, test_user
    ):
        resp = test_client.get("/disk-usage", headers=auth_headers)
        assert resp.status_code == 403

    def test_disk_usage_no_auth_gets_401(self, test_client):
        resp = test_client.get("/disk-usage")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# /metrics endpoint
# ---------------------------------------------------------------------------


class TestMetricsEndpoint:
    """Tests for GET /metrics."""

    def test_metrics_returns_text(self, test_client, monkeypatch):
        monkeypatch.setattr(
            "app.metrics.update_all_metrics", lambda db: None
        )
        monkeypatch.setattr(
            "app.metrics.get_metrics",
            lambda: (b"# HELP archetype_up\narchetype_up 1\n", "text/plain; version=0.0.4"),
        )
        # Reset the throttle timer so update_all_metrics is called
        import app.main as main_mod
        monkeypatch.setattr(main_mod, "_metrics_last_update", 0.0)

        resp = test_client.get("/metrics")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers["content-type"]
        assert b"archetype_up" in resp.content

    def test_metrics_throttles_updates(self, test_client, monkeypatch):
        """Second call within 15s should NOT call update_all_metrics again."""
        call_count = {"n": 0}

        def counting_update(db):
            call_count["n"] += 1

        monkeypatch.setattr("app.metrics.update_all_metrics", counting_update)
        monkeypatch.setattr(
            "app.metrics.get_metrics",
            lambda: (b"ok", "text/plain"),
        )

        import app.main as main_mod
        monkeypatch.setattr(main_mod, "_metrics_last_update", 0.0)

        # First call — should trigger update
        test_client.get("/metrics")
        assert call_count["n"] == 1

        # Second call — should be throttled (< 15s elapsed)
        test_client.get("/metrics")
        assert call_count["n"] == 1

    def test_metrics_content_type(self, test_client, monkeypatch):
        monkeypatch.setattr("app.metrics.update_all_metrics", lambda db: None)
        monkeypatch.setattr(
            "app.metrics.get_metrics",
            lambda: (b"data", "text/plain; version=0.0.4; charset=utf-8"),
        )
        import app.main as main_mod
        monkeypatch.setattr(main_mod, "_metrics_last_update", 0.0)

        resp = test_client.get("/metrics")
        assert "text/plain" in resp.headers["content-type"]
