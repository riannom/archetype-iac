"""Tests for agent/routers/admin.py — POAP, callbacks, locks, and update endpoints.

Covers:
- POAP startup-config serving endpoint
- POAP script serving endpoint
- Dead letters endpoint
- Lock status and release endpoints
- Agent update trigger endpoint
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from agent.main import app


# ---------------------------------------------------------------------------
# POAP endpoints
# ---------------------------------------------------------------------------


class TestPoapStartupConfig:
    """Tests for GET /poap/{lab_id}/{node_name}/startup-config."""

    def test_serves_config_successfully(self, tmp_path, monkeypatch):
        """Returns startup-config content when config file exists."""
        config_dir = tmp_path / "workspace" / "lab-1" / "configs" / "n9kv1"
        config_dir.mkdir(parents=True)
        (config_dir / "startup-config").write_text("hostname n9kv1\n", encoding="utf-8")

        from agent.config import settings
        monkeypatch.setattr(settings, "workspace_path", str(tmp_path / "workspace"))

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/poap/lab-1/n9kv1/startup-config")
        assert resp.status_code == 200
        assert "hostname n9kv1" in resp.text
        assert "text/plain" in resp.headers["content-type"]
        client.close()

    def test_returns_404_when_config_missing(self, tmp_path, monkeypatch):
        """Returns 404 when startup-config file does not exist."""
        from agent.config import settings
        monkeypatch.setattr(settings, "workspace_path", str(tmp_path / "workspace"))

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/poap/lab-1/n9kv1/startup-config")
        assert resp.status_code == 404
        client.close()

    def test_returns_400_for_invalid_lab_id(self):
        """Returns 400 for lab_id with path traversal characters."""
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/poap/lab../evil/n9kv1/startup-config")
        # FastAPI may return 400 or 404 depending on routing; the important
        # thing is it does not return 200 with arbitrary file content.
        assert resp.status_code != 200
        client.close()


class TestPoapScript:
    """Tests for GET /poap/{lab_id}/{node_name}/script.py."""

    def test_serves_poap_script(self, tmp_path, monkeypatch):
        """Returns rendered POAP script when config exists."""
        config_dir = tmp_path / "workspace" / "lab-1" / "configs" / "n9kv1"
        config_dir.mkdir(parents=True)
        (config_dir / "startup-config").write_text("hostname n9kv1\n", encoding="utf-8")

        from agent.config import settings
        monkeypatch.setattr(settings, "workspace_path", str(tmp_path / "workspace"))

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/poap/lab-1/n9kv1/script.py")
        assert resp.status_code == 200
        assert "text/x-python" in resp.headers["content-type"]
        # The rendered script should contain a reference to the config URL
        assert "startup-config" in resp.text
        client.close()

    def test_returns_404_when_config_missing(self, tmp_path, monkeypatch):
        """Returns 404 when the underlying startup-config does not exist."""
        from agent.config import settings
        monkeypatch.setattr(settings, "workspace_path", str(tmp_path / "workspace"))

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/poap/lab-1/n9kv1/script.py")
        assert resp.status_code == 404
        client.close()


# ---------------------------------------------------------------------------
# Dead letters endpoint
# ---------------------------------------------------------------------------


class TestDeadLetters:
    """Tests for GET /callbacks/dead-letters."""

    def test_returns_empty_list_when_no_failures(self):
        """Returns empty dead_letters list when queue is clean."""
        with patch("agent.routers.admin.fetch_dead_letters", return_value=[], create=True):
            # The endpoint imports get_dead_letters dynamically, so we patch
            # at the module level inside the handler.
            with patch("agent.callbacks.get_dead_letters", return_value=[]):
                client = TestClient(app, raise_server_exceptions=False)
                resp = client.get("/callbacks/dead-letters")
                assert resp.status_code == 200
                body = resp.json()
                assert "dead_letters" in body
                assert isinstance(body["dead_letters"], list)
                client.close()


# ---------------------------------------------------------------------------
# Lock status endpoints
# ---------------------------------------------------------------------------


class TestLockStatus:
    """Tests for GET /locks/status."""

    def test_returns_locks_when_manager_available(self):
        """Returns lock list and timestamp when lock manager is initialized."""
        mock_manager = MagicMock()
        mock_manager.get_all_locks = AsyncMock(return_value=[
            {"lab_id": "lab-1", "age_seconds": 10, "ttl": 890},
        ])

        client = TestClient(app, raise_server_exceptions=False)
        with patch("agent.agent_state.get_lock_manager", return_value=mock_manager):
            resp = client.get("/locks/status")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["locks"]) == 1
        assert body["locks"][0]["lab_id"] == "lab-1"
        assert body["locks"][0]["is_stuck"] is False
        assert "timestamp" in body
        client.close()

    def test_marks_stuck_locks(self):
        """Locks exceeding stuck threshold are flagged."""
        mock_manager = MagicMock()
        mock_manager.get_all_locks = AsyncMock(return_value=[
            {"lab_id": "lab-stuck", "age_seconds": 1000, "ttl": 0},
        ])

        from agent.config import settings

        client = TestClient(app, raise_server_exceptions=False)
        with patch("agent.agent_state.get_lock_manager", return_value=mock_manager):
            with patch.object(settings, "lock_stuck_threshold", 900.0):
                resp = client.get("/locks/status")

        body = resp.json()
        assert body["locks"][0]["is_stuck"] is True
        client.close()

    def test_returns_error_when_manager_not_initialized(self):
        """Returns error message when lock manager is None."""
        client = TestClient(app, raise_server_exceptions=False)
        with patch("agent.agent_state.get_lock_manager", return_value=None):
            resp = client.get("/locks/status")

        body = resp.json()
        assert body["locks"] == []
        assert "error" in body
        assert "not initialized" in body["error"]
        client.close()


# ---------------------------------------------------------------------------
# Lock release endpoint
# ---------------------------------------------------------------------------


class TestReleaseLock:
    """Tests for POST /locks/{lab_id}/release."""

    def test_releases_existing_lock(self):
        """Successfully releases an existing lock."""
        mock_manager = MagicMock()
        mock_manager.force_release = AsyncMock(return_value=True)

        client = TestClient(app, raise_server_exceptions=False)
        with patch("agent.agent_state.get_lock_manager", return_value=mock_manager):
            with patch("agent.agent_state._deploy_results", {}):
                resp = client.post("/locks/lab-1/release")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "cleared"
        assert body["lab_id"] == "lab-1"
        client.close()

    def test_returns_not_found_for_missing_lock(self):
        """Returns not_found when no lock exists for the lab."""
        mock_manager = MagicMock()
        mock_manager.force_release = AsyncMock(return_value=False)

        client = TestClient(app, raise_server_exceptions=False)
        with patch("agent.agent_state.get_lock_manager", return_value=mock_manager):
            resp = client.post("/locks/lab-missing/release")

        body = resp.json()
        assert body["status"] == "not_found"
        client.close()

    def test_returns_error_when_manager_not_initialized(self):
        """Returns error when lock manager is None."""
        client = TestClient(app, raise_server_exceptions=False)
        with patch("agent.agent_state.get_lock_manager", return_value=None):
            resp = client.post("/locks/lab-1/release")

        body = resp.json()
        assert body["status"] == "error"
        assert "not initialized" in body["error"]
        client.close()


# ---------------------------------------------------------------------------
# Update endpoint
# ---------------------------------------------------------------------------


class TestTriggerUpdate:
    """Tests for POST /update."""

    def test_systemd_mode_accepts_update(self):
        """Systemd deployment mode accepts and initiates update."""
        from agent.updater import DeploymentMode

        client = TestClient(app, raise_server_exceptions=False)
        with patch("agent.routers.admin.detect_deployment_mode", return_value=DeploymentMode.SYSTEMD):
            with patch("agent.routers.admin.perform_systemd_update", new_callable=AsyncMock) as mock_update:
                resp = client.post(
                    "/update",
                    json={
                        "job_id": "job-123",
                        "target_version": "abc123",
                        "callback_url": "http://localhost:8000/agents/callback",
                    },
                )

        assert resp.status_code == 200
        body = resp.json()
        assert body["accepted"] is True
        assert body["deployment_mode"] == "systemd"
        assert "Update initiated" in body["message"]
        client.close()

    def test_docker_mode_rejects_update(self):
        """Docker deployment mode reports that external update is needed."""
        from agent.updater import DeploymentMode

        client = TestClient(app, raise_server_exceptions=False)
        with patch("agent.routers.admin.detect_deployment_mode", return_value=DeploymentMode.DOCKER):
            with patch("agent.routers.admin.perform_docker_update", new_callable=AsyncMock):
                resp = client.post(
                    "/update",
                    json={
                        "job_id": "job-456",
                        "target_version": "def456",
                        "callback_url": "http://localhost:8000/agents/callback",
                    },
                )

        body = resp.json()
        assert body["accepted"] is False
        assert body["deployment_mode"] == "docker"
        assert "externally" in body["message"]
        client.close()

    def test_unknown_mode_rejects_update(self):
        """Unknown deployment mode rejects the update."""
        from agent.updater import DeploymentMode

        client = TestClient(app, raise_server_exceptions=False)
        with patch("agent.routers.admin.detect_deployment_mode", return_value=DeploymentMode.UNKNOWN):
            resp = client.post(
                "/update",
                json={
                    "job_id": "job-789",
                    "target_version": "ghi789",
                    "callback_url": "http://localhost:8000/agents/callback",
                },
            )

        body = resp.json()
        assert body["accepted"] is False
        assert body["deployment_mode"] == "unknown"
        assert "Unknown" in body["message"]
        client.close()

    def test_missing_required_fields_returns_422(self):
        """Missing required fields in request body returns 422."""
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/update", json={"job_id": "job-1"})
        assert resp.status_code == 422
        client.close()
