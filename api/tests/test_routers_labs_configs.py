"""Tests for config extraction and snapshot endpoints (routers/labs_configs.py).

This module tests:
- Config extraction from agents (single and multi-agent)
- Setting/clearing active startup-config per node
- Listing, filtering, and deleting config snapshots
- Retrieving saved configs from workspace
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_node(
    test_db: Session,
    lab: models.Lab,
    gui_id: str = "n1",
    display_name: str = "R1",
    container_name: str = "archetype-test-r1",
    device: str = "ceos",
    host_id: str | None = None,
) -> models.Node:
    """Create and persist a Node for testing."""
    node = models.Node(
        id=str(uuid4()),
        lab_id=lab.id,
        gui_id=gui_id,
        display_name=display_name,
        container_name=container_name,
        device=device,
        host_id=host_id,
    )
    test_db.add(node)
    test_db.commit()
    test_db.refresh(node)
    return node


def _make_snapshot(
    test_db: Session,
    lab_id: str,
    node_name: str,
    content: str = "hostname R1\n",
    snapshot_type: str = "manual",
    device_kind: str | None = "ceos",
) -> models.ConfigSnapshot:
    """Create and persist a ConfigSnapshot for testing."""
    snapshot = models.ConfigSnapshot(
        id=str(uuid4()),
        lab_id=lab_id,
        node_name=node_name,
        content=content,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        snapshot_type=snapshot_type,
        device_kind=device_kind,
    )
    test_db.add(snapshot)
    test_db.commit()
    test_db.refresh(snapshot)
    return snapshot


def _write_workspace_config(workspace_root: str, lab_id: str, node_name: str, content: str) -> None:
    """Write a startup-config file into the workspace for a given node."""
    config_dir = Path(workspace_root) / lab_id / "configs" / node_name
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "startup-config").write_text(content, encoding="utf-8")


# ============================================================================
# Extract Configs
# ============================================================================


class TestExtractConfigs:
    """Tests for the POST /labs/{lab_id}/extract-configs endpoint."""

    def test_extract_configs_success(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
    ):
        """Successful extraction returns extracted count and snapshots created."""
        node = _make_node(test_db, sample_lab, host_id=sample_host.id)

        # Create placement so extract knows which agent to talk to
        placement = models.NodePlacement(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            host_id=sample_host.id,
            node_name=node.container_name,
            node_definition_id=node.id,
        )
        test_db.add(placement)
        test_db.commit()

        mock_agent_client = MagicMock()
        mock_agent_client.is_agent_online = MagicMock(return_value=True)
        mock_agent_client.extract_configs_on_agent = AsyncMock(return_value={
            "success": True,
            "extracted_count": 1,
            "configs": [{"node_name": node.container_name, "content": "hostname R1\n"}],
        })
        mock_agent_client.get_http_client.return_value = MagicMock(
            put=AsyncMock(return_value=MagicMock(status_code=200)),
        )
        mock_agent_client._get_agent_auth_headers = MagicMock(return_value={})

        with patch("app.routers.labs.agent_client", mock_agent_client):
            response = test_client.post(
                f"/labs/{sample_lab.id}/extract-configs",
                headers=auth_headers,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["extracted_count"] == 1

    def test_extract_configs_no_online_agents(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Returns 503 when no healthy agents are available."""
        mock_agent_client = MagicMock()
        mock_agent_client.is_agent_online = MagicMock(return_value=False)

        with patch("app.routers.labs.agent_client", mock_agent_client):
            response = test_client.post(
                f"/labs/{sample_lab.id}/extract-configs",
                headers=auth_headers,
            )

        assert response.status_code == 503

    def test_extract_configs_partial_agent_failure(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
    ):
        """Partial agent failures still return data from successful agents."""
        node = _make_node(test_db, sample_lab, host_id=sample_host.id)
        placement = models.NodePlacement(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            host_id=sample_host.id,
            node_name=node.container_name,
            node_definition_id=node.id,
        )
        test_db.add(placement)
        test_db.commit()

        mock_agent_client = MagicMock()
        mock_agent_client.is_agent_online = MagicMock(return_value=True)
        # Return partial success - agent extraction succeeds but sync to agent fails
        mock_agent_client.extract_configs_on_agent = AsyncMock(return_value={
            "success": True,
            "extracted_count": 1,
            "configs": [{"node_name": node.container_name, "content": "hostname R1\n"}],
        })
        mock_agent_client.get_http_client.return_value = MagicMock(
            put=AsyncMock(side_effect=Exception("Agent sync failed")),
        )
        mock_agent_client._get_agent_auth_headers = MagicMock(return_value={})

        with patch("app.routers.labs.agent_client", mock_agent_client):
            response = test_client.post(
                f"/labs/{sample_lab.id}/extract-configs",
                headers=auth_headers,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["extracted_count"] == 1

    def test_extract_configs_creates_snapshots(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
    ):
        """Snapshots are persisted to the database after extraction."""
        node = _make_node(test_db, sample_lab, host_id=sample_host.id)
        placement = models.NodePlacement(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            host_id=sample_host.id,
            node_name=node.container_name,
            node_definition_id=node.id,
        )
        test_db.add(placement)
        test_db.commit()

        config_content = "hostname R1\ninterface Ethernet1\n"
        mock_agent_client = MagicMock()
        mock_agent_client.is_agent_online = MagicMock(return_value=True)
        mock_agent_client.extract_configs_on_agent = AsyncMock(return_value={
            "success": True,
            "extracted_count": 1,
            "configs": [{"node_name": node.container_name, "content": config_content}],
        })
        mock_agent_client.get_http_client.return_value = MagicMock(
            put=AsyncMock(return_value=MagicMock(status_code=200)),
        )
        mock_agent_client._get_agent_auth_headers = MagicMock(return_value={})

        with patch("app.routers.labs.agent_client", mock_agent_client):
            response = test_client.post(
                f"/labs/{sample_lab.id}/extract-configs",
                headers=auth_headers,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["snapshots_created"] >= 1


# ============================================================================
# Set Active Config
# ============================================================================


class TestSetActiveConfig:
    """Tests for PUT /labs/{lab_id}/nodes/{node_name}/active-config."""

    def test_set_active_config_success(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Successfully set an active config for a node."""
        node = _make_node(test_db, sample_lab)
        snapshot = _make_snapshot(test_db, sample_lab.id, node.container_name)

        mock_agent_client = MagicMock()
        mock_agent_client.is_agent_online = MagicMock(return_value=False)

        with patch("app.routers.labs.agent_client", mock_agent_client):
            response = test_client.put(
                f"/labs/{sample_lab.id}/nodes/{node.container_name}/active-config",
                json={"snapshot_id": snapshot.id},
                headers=auth_headers,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["active_config_snapshot_id"] == snapshot.id

    def test_set_active_config_node_not_found(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Returns 404 when node does not exist."""
        mock_agent_client = MagicMock()

        with patch("app.routers.labs.agent_client", mock_agent_client):
            response = test_client.put(
                f"/labs/{sample_lab.id}/nodes/nonexistent-node/active-config",
                json={"snapshot_id": str(uuid4())},
                headers=auth_headers,
            )

        assert response.status_code == 404

    def test_clear_active_config_via_null_snapshot(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Passing null snapshot_id clears the active config."""
        node = _make_node(test_db, sample_lab)

        mock_agent_client = MagicMock()
        mock_agent_client.is_agent_online = MagicMock(return_value=False)

        with patch("app.routers.labs.agent_client", mock_agent_client):
            response = test_client.put(
                f"/labs/{sample_lab.id}/nodes/{node.container_name}/active-config",
                json={"snapshot_id": None},
                headers=auth_headers,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["active_config_snapshot_id"] is None
        assert "cleared" in data["message"].lower()


# ============================================================================
# Config Snapshots
# ============================================================================


class TestConfigSnapshots:
    """Tests for config snapshot CRUD endpoints."""

    def test_list_snapshots_empty(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """List snapshots returns empty when none exist."""
        response = test_client.get(
            f"/labs/{sample_lab.id}/config-snapshots",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["snapshots"] == []

    def test_list_snapshots_filter_by_node(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Filter snapshots by node_name returns only matching."""
        _make_snapshot(test_db, sample_lab.id, "R1")
        _make_snapshot(test_db, sample_lab.id, "R2")

        response = test_client.get(
            f"/labs/{sample_lab.id}/config-snapshots",
            params={"node_name": "R1"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["snapshots"]) == 1
        assert data["snapshots"][0]["node_name"] == "R1"

    def test_delete_snapshot_success(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Delete a specific snapshot by ID."""
        snapshot = _make_snapshot(test_db, sample_lab.id, "R1")

        response = test_client.delete(
            f"/labs/{sample_lab.id}/config-snapshots/{snapshot.id}",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "deleted"

        # Verify removed from DB
        remaining = test_db.get(models.ConfigSnapshot, snapshot.id)
        assert remaining is None

    def test_delete_snapshot_not_found(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Delete nonexistent snapshot returns 404."""
        response = test_client.delete(
            f"/labs/{sample_lab.id}/config-snapshots/{str(uuid4())}",
            headers=auth_headers,
        )
        assert response.status_code == 404

    def test_bulk_delete_active_guard(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Bulk delete with active config guard returns 409 without force."""
        node = _make_node(test_db, sample_lab)
        snapshot = _make_snapshot(test_db, sample_lab.id, node.container_name)

        # Set snapshot as active config on node
        node.active_config_snapshot_id = snapshot.id
        test_db.commit()

        response = test_client.delete(
            f"/labs/{sample_lab.id}/config-snapshots",
            params={"node_name": node.container_name, "force": False},
            headers=auth_headers,
        )
        assert response.status_code == 409

    def test_bulk_delete_with_force(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Bulk delete with force=True overrides active config guard."""
        node = _make_node(test_db, sample_lab)
        snapshot = _make_snapshot(test_db, sample_lab.id, node.container_name)

        # Set snapshot as active config on node
        node.active_config_snapshot_id = snapshot.id
        test_db.commit()

        response = test_client.delete(
            f"/labs/{sample_lab.id}/config-snapshots",
            params={"node_name": node.container_name, "force": True},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["deleted_count"] >= 1


# ============================================================================
# Get Configs (workspace-based)
# ============================================================================


class TestGetConfigs:
    """Tests for GET /labs/{lab_id}/configs endpoints."""

    def test_get_all_configs_empty_workspace(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Returns empty list when no configs saved in workspace."""
        response = test_client.get(
            f"/labs/{sample_lab.id}/configs",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["configs"] == []

    def test_get_node_config_not_found(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Returns 404 when node config does not exist in workspace."""
        response = test_client.get(
            f"/labs/{sample_lab.id}/configs/nonexistent-node",
            headers=auth_headers,
        )
        assert response.status_code == 404

    def test_get_all_configs_returns_saved_configs(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
        tmp_path,
    ):
        """Returns saved configs when workspace has config files."""
        from app.config import settings

        # Write config into workspace
        _write_workspace_config(settings.workspace, sample_lab.id, "R1", "hostname R1\n")

        response = test_client.get(
            f"/labs/{sample_lab.id}/configs",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["configs"]) == 1
        assert data["configs"][0]["node_name"] == "R1"
        assert data["configs"][0]["config"] == "hostname R1\n"
        assert data["configs"][0]["exists"] is True

    def test_get_node_config_success(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
        tmp_path,
    ):
        """Returns config content for an existing node."""
        from app.config import settings

        _write_workspace_config(settings.workspace, sample_lab.id, "R1", "interface Ethernet1\n")

        response = test_client.get(
            f"/labs/{sample_lab.id}/configs/R1",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["node_name"] == "R1"
        assert data["config"] == "interface Ethernet1\n"

    def test_list_snapshots_returns_multiple(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """List snapshots returns all snapshots when no filter applied."""
        _make_snapshot(test_db, sample_lab.id, "R1", content="config1\n")
        _make_snapshot(test_db, sample_lab.id, "R2", content="config2\n")
        _make_snapshot(test_db, sample_lab.id, "R3", content="config3\n")

        response = test_client.get(
            f"/labs/{sample_lab.id}/config-snapshots",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["snapshots"]) == 3
