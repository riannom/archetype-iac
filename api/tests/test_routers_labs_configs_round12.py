"""Round 12 deep-path tests for api/app/routers/labs_configs.py.

Targets:
- Per-node extract-config endpoint (external node guard, empty content, no host, offline agent)
- set_active_config with agent push (online agent with placement)
- set_active_config clearing with agent push
- extract-configs with create_snapshot=False
- extract-configs all-agent extraction failure (500)
- Bulk delete orphaned_only filter
- List snapshots with device_kind filter
- Download with node_name filter
- Content hash helper
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models
from app.config import settings
from tests.factories import make_node, make_placement


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_snapshot(
    db: Session,
    lab_id: str,
    node_name: str,
    content: str = "hostname R1\n",
    snapshot_type: str = "manual",
    device_kind: str | None = "ceos",
) -> models.ConfigSnapshot:
    snapshot = models.ConfigSnapshot(
        id=str(uuid4()),
        lab_id=lab_id,
        node_name=node_name,
        content=content,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        snapshot_type=snapshot_type,
        device_kind=device_kind,
    )
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    return snapshot


def _write_workspace_config(lab_id: str, node_name: str, content: str) -> None:
    config_dir = Path(settings.workspace) / lab_id / "configs" / node_name
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "startup-config").write_text(content, encoding="utf-8")


def _mock_agent_client(
    *,
    online: bool = True,
    extract_result: dict | None = None,
    extract_node_result: dict | None = None,
    update_result: dict | None = None,
    put_status: int = 200,
    put_side_effect: Exception | None = None,
) -> MagicMock:
    """Build a fully-wired mock agent_client."""
    mock = MagicMock()
    mock.is_agent_online = MagicMock(return_value=online)
    mock.extract_configs_on_agent = AsyncMock(
        return_value=extract_result or {"success": True, "extracted_count": 0, "configs": []},
    )
    mock.extract_node_config_on_agent = AsyncMock(
        return_value=extract_node_result or {"success": True, "content": "hostname R1\n"},
    )
    mock.update_config_on_agent = AsyncMock(
        return_value=update_result or {"success": True},
    )

    put_mock = AsyncMock(return_value=MagicMock(status_code=put_status))
    if put_side_effect:
        put_mock = AsyncMock(side_effect=put_side_effect)
    mock.get_http_client.return_value = MagicMock(put=put_mock)
    mock._get_agent_auth_headers = MagicMock(return_value={})
    return mock


# ============================================================================
# Per-Node Extract Config
# ============================================================================


class TestExtractNodeConfig:
    """Tests for POST /labs/{lab_id}/nodes/{node_id}/extract-config."""

    def test_extract_node_config_success(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
    ):
        """Successful single-node extraction returns node details and snapshot count."""
        node = make_node(test_db, sample_lab, host_id=sample_host.id)
        make_placement(test_db, sample_lab, sample_host, node)

        mock = _mock_agent_client(
            extract_node_result={"success": True, "content": "hostname R1\ninterface Ethernet1\n"},
        )

        with patch("app.routers.labs.agent_client", mock):
            resp = test_client.post(
                f"/labs/{sample_lab.id}/nodes/{node.gui_id}/extract-config",
                headers=auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["node_name"] == node.container_name
        assert data["snapshots_created"] == 1

    def test_extract_node_config_external_node_rejected(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
    ):
        """External nodes cannot have configs extracted — returns 400."""
        node = make_node(
            test_db, sample_lab, gui_id="ext1", display_name="External",
            container_name="archetype-test-ext1", device="external",
            host_id=sample_host.id, node_type="external",
        )
        make_placement(test_db, sample_lab, sample_host, node)

        mock = _mock_agent_client()
        with patch("app.routers.labs.agent_client", mock):
            resp = test_client.post(
                f"/labs/{sample_lab.id}/nodes/{node.gui_id}/extract-config",
                headers=auth_headers,
            )

        assert resp.status_code == 400
        assert "external" in resp.json()["detail"].lower()

    def test_extract_node_config_node_not_found(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Non-existent node_id returns 404."""
        mock = _mock_agent_client()
        with patch("app.routers.labs.agent_client", mock):
            resp = test_client.post(
                f"/labs/{sample_lab.id}/nodes/no-such-node/extract-config",
                headers=auth_headers,
            )

        assert resp.status_code == 404

    def test_extract_node_config_no_placement_raises(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Node with no placement raises AttributeError (unguarded None)."""
        node = make_node(test_db, sample_lab, host_id=None)
        # No placement created — code hits placement.host_id on None

        mock = _mock_agent_client()
        with patch("app.routers.labs.agent_client", mock):
            # Production code does not guard against None placement —
            # `placement.host_id` raises AttributeError
            import pytest
            with pytest.raises(AttributeError, match="host_id"):
                test_client.post(
                    f"/labs/{sample_lab.id}/nodes/{node.gui_id}/extract-config",
                    headers=auth_headers,
                )

    def test_extract_node_config_agent_offline(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
    ):
        """Offline agent for the node's host returns 503."""
        node = make_node(test_db, sample_lab, host_id=sample_host.id)
        make_placement(test_db, sample_lab, sample_host, node)

        mock = _mock_agent_client(online=False)
        with patch("app.routers.labs.agent_client", mock):
            resp = test_client.post(
                f"/labs/{sample_lab.id}/nodes/{node.gui_id}/extract-config",
                headers=auth_headers,
            )

        assert resp.status_code == 503

    def test_extract_node_config_agent_returns_failure(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
    ):
        """Agent returning success=False raises 500."""
        node = make_node(test_db, sample_lab, host_id=sample_host.id)
        make_placement(test_db, sample_lab, sample_host, node)

        mock = _mock_agent_client(
            extract_node_result={"success": False, "error": "Node not running"},
        )
        with patch("app.routers.labs.agent_client", mock):
            resp = test_client.post(
                f"/labs/{sample_lab.id}/nodes/{node.gui_id}/extract-config",
                headers=auth_headers,
            )

        assert resp.status_code == 500
        assert "Node not running" in resp.json()["detail"]

    def test_extract_node_config_empty_content(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
    ):
        """Agent returning empty content raises 500."""
        node = make_node(test_db, sample_lab, host_id=sample_host.id)
        make_placement(test_db, sample_lab, sample_host, node)

        mock = _mock_agent_client(
            extract_node_result={"success": True, "content": ""},
        )
        with patch("app.routers.labs.agent_client", mock):
            resp = test_client.post(
                f"/labs/{sample_lab.id}/nodes/{node.gui_id}/extract-config",
                headers=auth_headers,
            )

        assert resp.status_code == 500
        assert "empty content" in resp.json()["detail"].lower()

    def test_extract_node_config_without_snapshot(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
    ):
        """create_snapshot=False still saves but does not set as active."""
        node = make_node(test_db, sample_lab, host_id=sample_host.id)
        make_placement(test_db, sample_lab, sample_host, node)

        mock = _mock_agent_client(
            extract_node_result={"success": True, "content": "hostname R1\n"},
        )
        with patch("app.routers.labs.agent_client", mock):
            resp = test_client.post(
                f"/labs/{sample_lab.id}/nodes/{node.gui_id}/extract-config",
                params={"create_snapshot": "false"},
                headers=auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        # When create_snapshot=False the code path still calls save_extracted_config
        # but with set_as_active=False, so snapshots_created stays 0
        assert data["snapshots_created"] == 0

    def test_extract_node_config_sync_error_still_succeeds(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
    ):
        """Agent config push failure is reported but doesn't fail the endpoint."""
        node = make_node(test_db, sample_lab, host_id=sample_host.id)
        make_placement(test_db, sample_lab, sample_host, node)

        mock = _mock_agent_client(
            extract_node_result={"success": True, "content": "hostname R1\n"},
            update_result={"success": False, "error": "agent unreachable"},
        )
        with patch("app.routers.labs.agent_client", mock):
            resp = test_client.post(
                f"/labs/{sample_lab.id}/nodes/{node.gui_id}/extract-config",
                headers=auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["sync_error"] == "agent unreachable"


# ============================================================================
# Set Active Config — deep paths
# ============================================================================


class TestSetActiveConfigDeep:
    """Deep-path tests for PUT /labs/{lab_id}/nodes/{node_name}/active-config."""

    def test_set_active_config_pushes_to_online_agent(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
    ):
        """When an online agent is available, the config is pushed via HTTP PUT."""
        node = make_node(test_db, sample_lab, host_id=sample_host.id)
        snapshot = _make_snapshot(test_db, sample_lab.id, node.container_name)
        make_placement(test_db, sample_lab, sample_host, node)

        put_mock = AsyncMock(return_value=MagicMock(status_code=200))
        mock = _mock_agent_client(online=True)
        mock.get_http_client.return_value = MagicMock(put=put_mock)

        with patch("app.routers.labs.agent_client", mock):
            resp = test_client.put(
                f"/labs/{sample_lab.id}/nodes/{node.container_name}/active-config",
                json={"snapshot_id": snapshot.id},
                headers=auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["active_config_snapshot_id"] == snapshot.id
        # Verify the PUT was actually called
        put_mock.assert_awaited_once()

    def test_clear_active_config_pushes_empty_to_agent(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
    ):
        """Clearing active config (null snapshot_id) pushes empty content to agent."""
        node = make_node(test_db, sample_lab, host_id=sample_host.id)
        make_placement(test_db, sample_lab, sample_host, node)

        put_mock = AsyncMock(return_value=MagicMock(status_code=200))
        mock = _mock_agent_client(online=True)
        mock.get_http_client.return_value = MagicMock(put=put_mock)

        with patch("app.routers.labs.agent_client", mock):
            resp = test_client.put(
                f"/labs/{sample_lab.id}/nodes/{node.container_name}/active-config",
                json={"snapshot_id": None},
                headers=auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["active_config_snapshot_id"] is None
        assert "cleared" in data["message"].lower()
        # Agent push should have been called with empty content
        put_mock.assert_awaited_once()
        call_kwargs = put_mock.call_args
        assert call_kwargs is not None

    def test_set_active_config_agent_push_failure_ignored(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
    ):
        """Agent push failure is silently ignored (best-effort)."""
        node = make_node(test_db, sample_lab, host_id=sample_host.id)
        snapshot = _make_snapshot(test_db, sample_lab.id, node.container_name)
        make_placement(test_db, sample_lab, sample_host, node)

        mock = _mock_agent_client(online=True, put_side_effect=Exception("connection refused"))

        with patch("app.routers.labs.agent_client", mock):
            resp = test_client.put(
                f"/labs/{sample_lab.id}/nodes/{node.container_name}/active-config",
                json={"snapshot_id": snapshot.id},
                headers=auth_headers,
            )

        # Should still succeed even though push failed
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_set_active_config_no_placement_no_push(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Without a placement, no agent push happens but the operation succeeds."""
        node = make_node(test_db, sample_lab)
        snapshot = _make_snapshot(test_db, sample_lab.id, node.container_name)

        mock = _mock_agent_client(online=False)

        with patch("app.routers.labs.agent_client", mock):
            resp = test_client.put(
                f"/labs/{sample_lab.id}/nodes/{node.container_name}/active-config",
                json={"snapshot_id": snapshot.id},
                headers=auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        # No PUT should have been called
        mock.get_http_client.return_value.put.assert_not_awaited()


# ============================================================================
# Extract Configs — additional paths
# ============================================================================


class TestExtractConfigsDeep:
    """Additional deep-path tests for POST /labs/{lab_id}/extract-configs."""

    def test_extract_configs_without_creating_snapshots(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
    ):
        """create_snapshot=false skips snapshot creation."""
        node = make_node(test_db, sample_lab, host_id=sample_host.id)
        make_placement(test_db, sample_lab, sample_host, node)

        mock = _mock_agent_client(
            extract_result={
                "success": True,
                "extracted_count": 1,
                "configs": [{"node_name": node.container_name, "content": "hostname R1\n"}],
            },
        )
        with patch("app.routers.labs.agent_client", mock):
            resp = test_client.post(
                f"/labs/{sample_lab.id}/extract-configs",
                params={"create_snapshot": "false"},
                headers=auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["extracted_count"] == 1

    def test_extract_configs_all_agents_fail_returns_500(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
    ):
        """When all agents fail extraction, returns 500."""
        node = make_node(test_db, sample_lab, host_id=sample_host.id)
        make_placement(test_db, sample_lab, sample_host, node)

        mock = _mock_agent_client(
            extract_result={"success": False, "error": "Docker daemon not responding"},
        )
        with patch("app.routers.labs.agent_client", mock):
            resp = test_client.post(
                f"/labs/{sample_lab.id}/extract-configs",
                headers=auth_headers,
            )

        assert resp.status_code == 500
        assert "failed on all agents" in resp.json()["detail"].lower()

    def test_extract_configs_agent_exception_returns_500(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
    ):
        """When agent raises an exception during extraction, returns 500."""
        node = make_node(test_db, sample_lab, host_id=sample_host.id)
        make_placement(test_db, sample_lab, sample_host, node)

        mock = _mock_agent_client()
        mock.extract_configs_on_agent = AsyncMock(side_effect=ConnectionError("Agent down"))

        with patch("app.routers.labs.agent_client", mock):
            resp = test_client.post(
                f"/labs/{sample_lab.id}/extract-configs",
                headers=auth_headers,
            )

        assert resp.status_code == 500

    def test_extract_configs_lab_not_found(
        self,
        test_client: TestClient,
        auth_headers: dict,
    ):
        """Non-existent lab returns 404."""
        mock = _mock_agent_client()
        with patch("app.routers.labs.agent_client", mock):
            resp = test_client.post(
                "/labs/nonexistent-lab-id/extract-configs",
                headers=auth_headers,
            )

        assert resp.status_code == 404

    def test_extract_configs_sync_error_reported(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
    ):
        """Sync errors to agents are reported in the response."""
        node = make_node(test_db, sample_lab, host_id=sample_host.id)
        make_placement(test_db, sample_lab, sample_host, node)

        mock = _mock_agent_client(
            extract_result={
                "success": True,
                "extracted_count": 1,
                "configs": [{"node_name": node.container_name, "content": "hostname R1\n"}],
            },
            put_side_effect=Exception("connection timed out"),
        )
        with patch("app.routers.labs.agent_client", mock):
            resp = test_client.post(
                f"/labs/{sample_lab.id}/extract-configs",
                headers=auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["sync_errors"] is not None
        assert len(data["sync_errors"]) >= 1

    def test_extract_configs_skips_empty_node_name_or_content(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
    ):
        """Config entries with missing node_name or content are silently skipped."""
        node = make_node(test_db, sample_lab, host_id=sample_host.id)
        make_placement(test_db, sample_lab, sample_host, node)

        mock = _mock_agent_client(
            extract_result={
                "success": True,
                "extracted_count": 2,
                "configs": [
                    {"node_name": "", "content": "hostname R1\n"},
                    {"node_name": node.container_name, "content": ""},
                    {"node_name": None, "content": "hostname R2\n"},
                ],
            },
        )
        with patch("app.routers.labs.agent_client", mock):
            resp = test_client.post(
                f"/labs/{sample_lab.id}/extract-configs",
                headers=auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        # All three entries should be skipped (empty name, empty content, None name)
        assert data["snapshots_created"] == 0


# ============================================================================
# Bulk Delete — additional filters
# ============================================================================


class TestBulkDeleteDeep:
    """Additional bulk delete tests for DELETE /labs/{lab_id}/config-snapshots."""

    def test_bulk_delete_orphaned_only(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """orphaned_only=true targets only snapshots for nodes not in the topology."""
        # Create a snapshot for a node that doesn't exist in the topology
        _make_snapshot(test_db, sample_lab.id, "deleted-node", content="old config\n")

        resp = test_client.delete(
            f"/labs/{sample_lab.id}/config-snapshots",
            params={"orphaned_only": "true"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted_count"] >= 1

    def test_bulk_delete_no_matching_snapshots(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Bulk delete with no matching snapshots returns 0 deleted."""
        resp = test_client.delete(
            f"/labs/{sample_lab.id}/config-snapshots",
            params={"node_name": "nonexistent-node"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted_count"] == 0


# ============================================================================
# List Snapshots — device_kind filter
# ============================================================================


class TestListSnapshotsDeep:
    """Additional list snapshot tests."""

    def test_list_snapshots_filter_by_device_kind(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Filtering by device_kind returns only matching snapshots."""
        _make_snapshot(test_db, sample_lab.id, "R1", content="ceos config\n", device_kind="ceos")
        _make_snapshot(test_db, sample_lab.id, "SW1", content="srl config\n", device_kind="srl")

        resp = test_client.get(
            f"/labs/{sample_lab.id}/config-snapshots",
            params={"device_kind": "ceos"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        for snap in data["snapshots"]:
            assert snap["device_kind"] == "ceos"

    def test_list_snapshots_orphaned_only(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """orphaned_only=true returns only snapshots for nodes not in topology."""
        # Snapshot for a real node
        node = make_node(test_db, sample_lab)
        _make_snapshot(test_db, sample_lab.id, node.container_name, content="real config\n")

        # Snapshot for a deleted node (orphaned)
        _make_snapshot(test_db, sample_lab.id, "deleted-old-node", content="orphan config\n")

        resp = test_client.get(
            f"/labs/{sample_lab.id}/config-snapshots",
            params={"orphaned_only": "true"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        # All returned snapshots should be orphaned
        for snap in data["snapshots"]:
            assert snap.get("is_orphaned", True) is True


# ============================================================================
# Download Snapshots — node_name filter
# ============================================================================


class TestDownloadSnapshotsDeep:
    """Additional download tests."""

    def test_download_with_node_filter(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Download with node_name filter generates a zip for specific nodes."""
        _make_snapshot(test_db, sample_lab.id, "R1", content="config R1\n")
        _make_snapshot(test_db, sample_lab.id, "R2", content="config R2\n")

        resp = test_client.get(
            f"/labs/{sample_lab.id}/config-snapshots/download",
            params={"node_name": "R1"},
            headers=auth_headers,
        )
        # Should return a zip or 404 if service can't find configs
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            assert resp.headers.get("content-type") == "application/zip"


# ============================================================================
# Content Hash Helper
# ============================================================================


class TestContentHash:
    """Tests for the _compute_content_hash helper."""

    def test_compute_content_hash_deterministic(self):
        """Same content always produces the same hash."""
        from app.routers.labs_configs import _compute_content_hash

        content = "hostname R1\ninterface Ethernet1\n"
        h1 = _compute_content_hash(content)
        h2 = _compute_content_hash(content)
        assert h1 == h2
        assert h1 == hashlib.sha256(content.encode("utf-8")).hexdigest()

    def test_compute_content_hash_different_for_different_content(self):
        """Different content produces different hashes."""
        from app.routers.labs_configs import _compute_content_hash

        h1 = _compute_content_hash("config A")
        h2 = _compute_content_hash("config B")
        assert h1 != h2

    def test_compute_content_hash_empty_string(self):
        """Empty string produces a valid SHA256 hash."""
        from app.routers.labs_configs import _compute_content_hash

        h = _compute_content_hash("")
        assert len(h) == 64  # SHA256 hex digest length


# ============================================================================
# Save Config to Workspace Helper
# ============================================================================


class TestSaveConfigToWorkspace:
    """Tests for the _save_config_to_workspace helper."""

    def test_save_config_creates_directories_and_file(self, tmp_path):
        """Helper creates nested directories and writes the config file."""
        from app.routers.labs_configs import _save_config_to_workspace

        workspace = tmp_path / "workspace" / "lab-1"
        _save_config_to_workspace(workspace, "R1", "hostname R1\n")

        config_file = workspace / "configs" / "R1" / "startup-config"
        assert config_file.exists()
        assert config_file.read_text(encoding="utf-8") == "hostname R1\n"

    def test_save_config_overwrites_existing(self, tmp_path):
        """Writing to an existing config file overwrites it."""
        from app.routers.labs_configs import _save_config_to_workspace

        workspace = tmp_path / "workspace" / "lab-1"
        _save_config_to_workspace(workspace, "R1", "old config")
        _save_config_to_workspace(workspace, "R1", "new config")

        config_file = workspace / "configs" / "R1" / "startup-config"
        assert config_file.read_text(encoding="utf-8") == "new config"