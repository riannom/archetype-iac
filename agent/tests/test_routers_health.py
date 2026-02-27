"""Tests for the agent health and system info endpoints.

Covers healthz, health, capacity, disk usage, info,
and deployment mode endpoints.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch, AsyncMock

import pytest
from fastapi.testclient import TestClient

import agent.agent_state as _state
from agent.config import settings
from agent.main import app


@pytest.fixture()
def client():
    """TestClient with auth disabled (empty controller_secret)."""
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch):
    """Ensure agent auth middleware does not block test requests."""
    monkeypatch.setattr(settings, "controller_secret", "")


# ---------------------------------------------------------------------------
# 1. /healthz — fast liveness probe
# ---------------------------------------------------------------------------


class TestHealthz:
    """Tests for the /healthz endpoint."""

    def test_healthz_returns_200_ok(self, client: TestClient):
        """Healthz should always return 200 with status ok."""
        resp = client.get("/healthz")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


# ---------------------------------------------------------------------------
# 2. /health — detailed health
# ---------------------------------------------------------------------------


class TestHealth:
    """Tests for the /health endpoint."""

    def test_health_returns_agent_id(self, client: TestClient):
        """Health should include the agent_id."""
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "agent_id" in data
        assert data["agent_id"] == _state.AGENT_ID

    def test_health_returns_registered_state(self, client: TestClient):
        """Health should report registration status."""
        _state.set_registered(False)
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["registered"] is False

    def test_health_returns_timestamp(self, client: TestClient):
        """Health should include a UTC timestamp."""
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "timestamp" in data


# ---------------------------------------------------------------------------
# 3. /capacity — resource allocation snapshot
# ---------------------------------------------------------------------------


class TestCapacity:
    """Tests for the /capacity endpoint."""

    def test_capacity_returns_allocated_resources(self, client: TestClient, monkeypatch):
        """Capacity should include allocated_vcpus and allocated_memory_mb."""
        usage = {
            "cpu_percent": 25.0,
            "memory_percent": 40.0,
            "container_details": [
                {"status": "running", "vcpus": 2, "memory_mb": 4096, "is_system": False},
                {"status": "running", "vcpus": 1, "memory_mb": 2048, "is_system": False},
            ],
            "vm_details": [],
        }

        async def fake_get_resource_usage():
            return usage

        monkeypatch.setattr(
            "agent.routers.health.get_resource_usage",
            fake_get_resource_usage,
        )

        resp = client.get("/capacity")
        assert resp.status_code == 200
        data = resp.json()
        assert data["allocated_vcpus"] == 3
        assert data["allocated_memory_mb"] == 6144

    def test_capacity_error_when_resource_usage_fails(self, client: TestClient, monkeypatch):
        """Capacity should return error when resource gathering fails."""
        async def fake_get_resource_usage():
            return {}

        monkeypatch.setattr(
            "agent.routers.health.get_resource_usage",
            fake_get_resource_usage,
        )

        resp = client.get("/capacity")
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data

    def test_capacity_excludes_system_containers(self, client: TestClient, monkeypatch):
        """System containers should not count toward allocated resources."""
        usage = {
            "cpu_percent": 10.0,
            "container_details": [
                {"status": "running", "vcpus": 4, "memory_mb": 8192, "is_system": True},
                {"status": "running", "vcpus": 1, "memory_mb": 512, "is_system": False},
            ],
            "vm_details": [],
        }

        async def fake_get_resource_usage():
            return usage

        monkeypatch.setattr(
            "agent.routers.health.get_resource_usage",
            fake_get_resource_usage,
        )

        resp = client.get("/capacity")
        assert resp.status_code == 200
        data = resp.json()
        assert data["allocated_vcpus"] == 1
        assert data["allocated_memory_mb"] == 512


# ---------------------------------------------------------------------------
# 4. /disk-usage — disk and memory stats
# ---------------------------------------------------------------------------


class TestDiskUsage:
    """Tests for the /disk-usage endpoint."""

    def test_disk_usage_returns_structured_data(self, client: TestClient, monkeypatch):
        """Disk usage should return disk and memory sections."""
        import psutil as _psutil

        fake_disk = MagicMock(
            total=100 * 1024**3,
            used=40 * 1024**3,
            free=60 * 1024**3,
            percent=40.0,
        )
        fake_memory = MagicMock(
            total=32 * 1024**3,
            used=16 * 1024**3,
            percent=50.0,
        )

        monkeypatch.setattr(_psutil, "disk_usage", lambda path: fake_disk)
        monkeypatch.setattr(_psutil, "virtual_memory", lambda: fake_memory)

        resp = client.get("/disk-usage")
        assert resp.status_code == 200
        data = resp.json()

        assert "disk" in data
        assert "memory" in data
        assert data["disk"]["total_gb"] == 100.0
        assert data["disk"]["percent"] == 40.0
        assert data["memory"]["total_gb"] == 32.0
        assert data["memory"]["percent"] == 50.0


# ---------------------------------------------------------------------------
# 5. /info — agent capabilities
# ---------------------------------------------------------------------------


class TestInfo:
    """Tests for the /info endpoint."""

    def test_info_returns_agent_capabilities(self, client: TestClient, monkeypatch):
        """Info should return agent info including capabilities."""
        resp = client.get("/info")
        assert resp.status_code == 200
        data = resp.json()
        assert "agent_id" in data
        assert "capabilities" in data
        assert "name" in data


# ---------------------------------------------------------------------------
# 6. /deployment-mode — how the agent is deployed
# ---------------------------------------------------------------------------


class TestDeploymentMode:
    """Tests for the /deployment-mode endpoint."""

    def test_deployment_mode_returns_docker(self, client: TestClient, monkeypatch):
        """When running in Docker, mode should be 'docker'."""
        from agent.updater import DeploymentMode

        monkeypatch.setattr(
            "agent.routers.health.detect_deployment_mode",
            lambda: DeploymentMode.DOCKER,
        )

        resp = client.get("/deployment-mode")
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "docker"

    def test_deployment_mode_returns_systemd(self, client: TestClient, monkeypatch):
        """When managed by systemd, mode should be 'systemd'."""
        from agent.updater import DeploymentMode

        monkeypatch.setattr(
            "agent.routers.health.detect_deployment_mode",
            lambda: DeploymentMode.SYSTEMD,
        )

        resp = client.get("/deployment-mode")
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "systemd"

    def test_deployment_mode_includes_version(self, client: TestClient, monkeypatch):
        """Deployment mode response should include the agent version."""
        from agent.updater import DeploymentMode

        monkeypatch.setattr(
            "agent.routers.health.detect_deployment_mode",
            lambda: DeploymentMode.UNKNOWN,
        )

        resp = client.get("/deployment-mode")
        assert resp.status_code == 200
        data = resp.json()
        assert "version" in data
        assert isinstance(data["version"], str)
