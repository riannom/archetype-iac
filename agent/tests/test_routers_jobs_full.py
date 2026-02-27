"""Tests for agent deploy/destroy job endpoints (agent/routers/jobs.py).

Covers:
- Deploy endpoint: sync success/failure, lock timeout, missing topology, no lock manager, async 202, exception
- Destroy endpoint: sync success/failure, no lock manager, async 202, lock timeout
- Deploy with callback: callback on success/failure/lock timeout/missing manager, heartbeat
- Cache cleanup after sync deploy
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import agent.agent_state as _state
from agent.config import settings
from agent.locks import LockAcquisitionTimeout
from agent.main import app
from agent.providers.base import DeployResult, DestroyResult
from agent.schemas import (
    DeployRequest,
    DeployTopology,
    DestroyRequest,
    JobResult,
    JobStatus,
    Provider,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _MockLockManager:
    """Lightweight mock lock manager for sync endpoint tests."""

    def __init__(self, *, should_timeout: bool = False):
        self._should_timeout = should_timeout

    @asynccontextmanager
    async def acquire_with_heartbeat(self, lab_id, timeout=30.0, extend_interval=30.0):
        if self._should_timeout:
            raise LockAcquisitionTimeout(lab_id, timeout)
        yield


def _topo() -> dict:
    """Minimal JSON topology dict for requests."""
    return {"nodes": [], "links": []}


def _deploy_body(**overrides) -> dict:
    d = {
        "job_id": "j-1",
        "lab_id": "lab-1",
        "provider": "docker",
        "topology": _topo(),
    }
    d.update(overrides)
    return d


def _destroy_body(**overrides) -> dict:
    d = {
        "job_id": "j-d-1",
        "lab_id": "lab-1",
        "provider": "docker",
    }
    d.update(overrides)
    return d


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    """TestClient with auth disabled."""
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch):
    monkeypatch.setattr(settings, "controller_secret", "")


@pytest.fixture(autouse=True)
def _workspace(tmp_path, monkeypatch):
    def _fake_ws(lab_id: str):
        ws = tmp_path / lab_id
        ws.mkdir(parents=True, exist_ok=True)
        return ws
    monkeypatch.setattr("agent.routers.jobs.get_workspace", _fake_ws)


@pytest.fixture(autouse=True)
def _clean_deploy_cache():
    """Clear cached deploy results between tests."""
    _state._deploy_results.clear()
    yield
    _state._deploy_results.clear()


# ---------------------------------------------------------------------------
# TestDeployEndpoint
# ---------------------------------------------------------------------------


class TestDeployEndpoint:
    """Tests for POST /jobs/deploy (synchronous mode)."""

    def test_sync_success(self, client: TestClient, monkeypatch):
        """Successful sync deploy should return COMPLETED."""
        mock_provider = MagicMock()
        mock_provider.deploy = AsyncMock(return_value=DeployResult(
            success=True, stdout="ok", stderr="",
        ))
        monkeypatch.setattr(
            "agent.routers.jobs.get_lock_manager",
            lambda: _MockLockManager(),
        )
        monkeypatch.setattr(
            "agent.routers.jobs.get_provider_for_request",
            lambda prov: mock_provider,
        )

        resp = client.post("/jobs/deploy", json=_deploy_body())
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["stdout"] == "ok"

    def test_sync_failure(self, client: TestClient, monkeypatch):
        """Failed deploy should return FAILED status (not HTTP error)."""
        mock_provider = MagicMock()
        mock_provider.deploy = AsyncMock(return_value=DeployResult(
            success=False, stdout="", stderr="", error="deploy blew up",
        ))
        monkeypatch.setattr(
            "agent.routers.jobs.get_lock_manager",
            lambda: _MockLockManager(),
        )
        monkeypatch.setattr(
            "agent.routers.jobs.get_provider_for_request",
            lambda prov: mock_provider,
        )

        resp = client.post("/jobs/deploy", json=_deploy_body())
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "failed"
        assert data["error_message"] == "deploy blew up"

    def test_lock_timeout_503(self, client: TestClient, monkeypatch):
        """Lock timeout should produce HTTP 503."""
        monkeypatch.setattr(
            "agent.routers.jobs.get_lock_manager",
            lambda: _MockLockManager(should_timeout=True),
        )
        resp = client.post("/jobs/deploy", json=_deploy_body())
        assert resp.status_code == 503
        assert "already in progress" in resp.json()["detail"]

    def test_no_topology_400(self, client: TestClient, monkeypatch):
        """Missing topology should return 400."""
        monkeypatch.setattr(
            "agent.routers.jobs.get_lock_manager",
            lambda: _MockLockManager(),
        )
        body = _deploy_body()
        body["topology"] = None
        resp = client.post("/jobs/deploy", json=body)
        assert resp.status_code == 400
        assert "No topology" in resp.json()["detail"]

    def test_no_lock_manager_503(self, client: TestClient, monkeypatch):
        """None lock manager should return 503."""
        monkeypatch.setattr(
            "agent.routers.jobs.get_lock_manager",
            lambda: None,
        )
        resp = client.post("/jobs/deploy", json=_deploy_body())
        assert resp.status_code == 503
        assert "Lock manager not initialized" in resp.json()["detail"]

    def test_async_returns_202(self, client: TestClient, monkeypatch):
        """Deploy with callback_url should immediately return ACCEPTED."""
        monkeypatch.setattr(
            "agent.routers.jobs.get_lock_manager",
            lambda: _MockLockManager(),
        )
        with patch("agent.routers.jobs._execute_deploy_with_callback", new_callable=AsyncMock):
            body = _deploy_body(callback_url="http://ctrl/cb")
            resp = client.post("/jobs/deploy", json=body)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "accepted"

    def test_exception_returns_failed(self, client: TestClient, monkeypatch):
        """Unhandled exception should return FAILED result, not crash."""
        mock_provider = MagicMock()
        mock_provider.deploy = AsyncMock(side_effect=RuntimeError("kaboom"))
        monkeypatch.setattr(
            "agent.routers.jobs.get_lock_manager",
            lambda: _MockLockManager(),
        )
        monkeypatch.setattr(
            "agent.routers.jobs.get_provider_for_request",
            lambda prov: mock_provider,
        )

        resp = client.post("/jobs/deploy", json=_deploy_body())
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "failed"
        assert "kaboom" in data["error_message"]


# ---------------------------------------------------------------------------
# TestDestroyEndpoint
# ---------------------------------------------------------------------------


class TestDestroyEndpoint:
    """Tests for POST /jobs/destroy."""

    def test_sync_success(self, client: TestClient, monkeypatch):
        """Successful destroy should return COMPLETED."""
        mock_provider = MagicMock()
        mock_provider.destroy = AsyncMock(return_value=DestroyResult(
            success=True, stdout="destroyed", stderr="",
        ))
        monkeypatch.setattr(
            "agent.routers.jobs.get_lock_manager",
            lambda: _MockLockManager(),
        )
        monkeypatch.setattr(
            "agent.routers.jobs.get_provider_for_request",
            lambda prov: mock_provider,
        )

        resp = client.post("/jobs/destroy", json=_destroy_body())
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"

    def test_sync_failure(self, client: TestClient, monkeypatch):
        """Failed destroy should return FAILED status."""
        mock_provider = MagicMock()
        mock_provider.destroy = AsyncMock(return_value=DestroyResult(
            success=False, error="cleanup failed",
        ))
        monkeypatch.setattr(
            "agent.routers.jobs.get_lock_manager",
            lambda: _MockLockManager(),
        )
        monkeypatch.setattr(
            "agent.routers.jobs.get_provider_for_request",
            lambda prov: mock_provider,
        )

        resp = client.post("/jobs/destroy", json=_destroy_body())
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "failed"
        assert data["error_message"] == "cleanup failed"

    def test_no_lock_manager_503(self, client: TestClient, monkeypatch):
        """None lock manager should return 503."""
        monkeypatch.setattr(
            "agent.routers.jobs.get_lock_manager",
            lambda: None,
        )
        resp = client.post("/jobs/destroy", json=_destroy_body())
        assert resp.status_code == 503

    def test_async_returns_202(self, client: TestClient, monkeypatch):
        """Destroy with callback_url should return ACCEPTED."""
        monkeypatch.setattr(
            "agent.routers.jobs.get_lock_manager",
            lambda: _MockLockManager(),
        )
        with patch("agent.routers.jobs._execute_destroy_with_callback", new_callable=AsyncMock):
            body = _destroy_body(callback_url="http://ctrl/cb")
            resp = client.post("/jobs/destroy", json=body)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "accepted"

    def test_lock_timeout_503(self, client: TestClient, monkeypatch):
        """Lock timeout during destroy should produce HTTP 503."""
        monkeypatch.setattr(
            "agent.routers.jobs.get_lock_manager",
            lambda: _MockLockManager(should_timeout=True),
        )
        resp = client.post("/jobs/destroy", json=_destroy_body())
        assert resp.status_code == 503
        assert "Another operation" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# TestDeployWithCallback
# ---------------------------------------------------------------------------


class TestDeployWithCallback:
    """Tests for async deploy callback delivery (_execute_deploy_with_callback)."""

    @pytest.mark.asyncio
    async def test_callback_on_success(self, monkeypatch):
        """Successful deploy should deliver 'completed' callback."""
        from agent.routers.jobs import _execute_deploy_with_callback

        mock_provider = MagicMock()
        mock_provider.deploy = AsyncMock(return_value=DeployResult(
            success=True, stdout="ok",
        ))

        captured = {}

        async def capture(url, payload):
            captured["payload"] = payload

        monkeypatch.setattr(
            "agent.routers.jobs.get_lock_manager",
            lambda: _MockLockManager(),
        )
        monkeypatch.setattr(
            "agent.routers.jobs.get_provider_for_request",
            lambda prov: mock_provider,
        )

        with patch("agent.callbacks.deliver_callback", side_effect=capture):
            await _execute_deploy_with_callback(
                "j-1", "lab-1", DeployTopology(nodes=[], links=[]),
                "docker", "http://ctrl/cb",
            )

        assert captured["payload"].status == "completed"

    @pytest.mark.asyncio
    async def test_callback_on_failure(self, monkeypatch):
        """Failed deploy should deliver 'failed' callback."""
        from agent.routers.jobs import _execute_deploy_with_callback

        mock_provider = MagicMock()
        mock_provider.deploy = AsyncMock(side_effect=Exception("broke"))

        captured = {}

        async def capture(url, payload):
            captured["payload"] = payload

        monkeypatch.setattr(
            "agent.routers.jobs.get_lock_manager",
            lambda: _MockLockManager(),
        )
        monkeypatch.setattr(
            "agent.routers.jobs.get_provider_for_request",
            lambda prov: mock_provider,
        )

        with patch("agent.callbacks.deliver_callback", side_effect=capture):
            await _execute_deploy_with_callback(
                "j-1", "lab-1", DeployTopology(nodes=[], links=[]),
                "docker", "http://ctrl/cb",
            )

        assert captured["payload"].status == "failed"
        assert "broke" in captured["payload"].error_message

    @pytest.mark.asyncio
    async def test_callback_on_lock_timeout(self, monkeypatch):
        """Lock timeout should deliver 'failed' callback with message."""
        from agent.routers.jobs import _execute_deploy_with_callback

        captured = {}

        async def capture(url, payload):
            captured["payload"] = payload

        monkeypatch.setattr(
            "agent.routers.jobs.get_lock_manager",
            lambda: _MockLockManager(should_timeout=True),
        )

        with patch("agent.callbacks.deliver_callback", side_effect=capture):
            await _execute_deploy_with_callback(
                "j-1", "lab-1", DeployTopology(nodes=[], links=[]),
                "docker", "http://ctrl/cb",
            )

        assert captured["payload"].status == "failed"
        assert "timed out" in captured["payload"].error_message

    @pytest.mark.asyncio
    async def test_callback_on_missing_manager(self, monkeypatch):
        """None lock manager should still deliver failure callback."""
        from agent.routers.jobs import _execute_deploy_with_callback

        captured = {}

        async def capture(url, payload):
            captured["payload"] = payload

        monkeypatch.setattr("agent.routers.jobs.get_lock_manager", lambda: None)

        with patch("agent.callbacks.deliver_callback", side_effect=capture):
            await _execute_deploy_with_callback(
                "j-1", "lab-1", DeployTopology(nodes=[], links=[]),
                "docker", "http://ctrl/cb",
            )

        assert captured["payload"].status == "failed"
        assert "Lock manager not initialized" in captured["payload"].error_message


# ---------------------------------------------------------------------------
# TestCleanupDeployCache
# ---------------------------------------------------------------------------


class TestCleanupDeployCache:
    """Tests for _cleanup_deploy_cache."""

    @pytest.mark.asyncio
    async def test_cleared_after_delay(self):
        """Cache entry should be removed after the delay."""
        from agent.routers.jobs import _cleanup_deploy_cache

        _state._deploy_results["lab-x"] = MagicMock()
        await _cleanup_deploy_cache("lab-x", delay=0.0)
        assert "lab-x" not in _state._deploy_results

    @pytest.mark.asyncio
    async def test_key_removed_idempotent(self):
        """Cleaning a non-existent key should not raise."""
        from agent.routers.jobs import _cleanup_deploy_cache

        await _cleanup_deploy_cache("nonexistent", delay=0.0)
        assert "nonexistent" not in _state._deploy_results
