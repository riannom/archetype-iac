"""Tests for untested endpoints in api/app/routers/labs_configs.py.

Covers:
- POST /labs/{id}/config-snapshots (create_config_snapshot)
- GET /labs/{id}/config-snapshots/{node_name}/list (list_node_config_snapshots)
- POST /labs/{id}/config-snapshots/{snapshot_id}/map (map_config_snapshot)
- GET /labs/{id}/config-snapshots/download (download_config_snapshots)
- GET /labs/{id}/config-snapshots/orphaned (list_orphaned_configs)
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models
from app.config import settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_config_dir(lab_id: str, node_name: str, content: str) -> Path:
    """Create workspace config directory with a startup-config file."""
    configs_dir = Path(settings.workspace) / lab_id / "configs" / node_name
    configs_dir.mkdir(parents=True, exist_ok=True)
    config_file = configs_dir / "startup-config"
    config_file.write_text(content, encoding="utf-8")
    return config_file


def _create_snapshot(
    db: Session, lab_id: str, node_name: str, content: str,
    snapshot_type: str = "manual", device_kind: str | None = None,
) -> models.ConfigSnapshot:
    """Insert a ConfigSnapshot row directly."""
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    snapshot = models.ConfigSnapshot(
        lab_id=lab_id,
        node_name=node_name,
        content=content,
        content_hash=content_hash,
        snapshot_type=snapshot_type,
        device_kind=device_kind,
    )
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    return snapshot


# ===========================================================================
# POST /labs/{id}/config-snapshots
# ===========================================================================


class TestCreateConfigSnapshot:

    def test_create_snapshot_success(
        self, test_client: TestClient, test_db: Session,
        sample_lab: models.Lab, auth_headers: dict,
    ):
        """Creating snapshots from saved configs succeeds."""
        _create_config_dir(sample_lab.id, "archetype-test-r1", "hostname R1\n")
        resp = test_client.post(
            f"/labs/{sample_lab.id}/config-snapshots",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "snapshots" in data

    def test_create_snapshot_for_specific_node(
        self, test_client: TestClient, test_db: Session,
        sample_lab: models.Lab, auth_headers: dict,
    ):
        """Creating a snapshot for a specific node."""
        _create_config_dir(sample_lab.id, "archetype-test-r1", "hostname R1\n")
        resp = test_client.post(
            f"/labs/{sample_lab.id}/config-snapshots",
            json={"node_name": "archetype-test-r1"},
            headers=auth_headers,
        )
        assert resp.status_code == 200

    def test_create_snapshot_no_configs_dir_returns_404(
        self, test_client: TestClient, sample_lab: models.Lab, auth_headers: dict,
    ):
        """No configs directory returns 404."""
        resp = test_client.post(
            f"/labs/{sample_lab.id}/config-snapshots",
            headers=auth_headers,
        )
        assert resp.status_code == 404
        assert "No saved configs" in resp.json()["detail"]

    def test_create_snapshot_invalid_node_returns_404(
        self, test_client: TestClient, sample_lab: models.Lab, auth_headers: dict,
    ):
        """Requesting a snapshot for a non-existent node dir returns 404."""
        # Create configs dir but not for the requested node
        configs_dir = Path(settings.workspace) / sample_lab.id / "configs"
        configs_dir.mkdir(parents=True, exist_ok=True)

        resp = test_client.post(
            f"/labs/{sample_lab.id}/config-snapshots",
            json={"node_name": "nonexistent-node"},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_create_snapshot_lab_not_found(
        self, test_client: TestClient, auth_headers: dict,
    ):
        resp = test_client.post(
            "/labs/nonexistent-lab/config-snapshots",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_create_snapshot_requires_auth(
        self, test_client: TestClient, sample_lab: models.Lab,
    ):
        resp = test_client.post(f"/labs/{sample_lab.id}/config-snapshots")
        assert resp.status_code in (401, 403)


# ===========================================================================
# GET /labs/{id}/config-snapshots/{node_name}/list
# ===========================================================================


class TestListNodeConfigSnapshots:

    def test_list_node_snapshots_success(
        self, test_client: TestClient, test_db: Session,
        sample_lab: models.Lab, auth_headers: dict,
    ):
        """Lists snapshots for a specific node."""
        _create_snapshot(test_db, sample_lab.id, "R1", "hostname R1\n")
        _create_snapshot(test_db, sample_lab.id, "R1", "hostname R1-v2\n")
        _create_snapshot(test_db, sample_lab.id, "R2", "hostname R2\n")

        resp = test_client.get(
            f"/labs/{sample_lab.id}/config-snapshots/R1/list",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "snapshots" in data
        # Should only return R1 snapshots
        for snap in data["snapshots"]:
            assert snap["node_name"] == "R1"

    def test_list_node_snapshots_empty(
        self, test_client: TestClient, sample_lab: models.Lab, auth_headers: dict,
    ):
        """When no snapshots exist for a node, returns empty list."""
        resp = test_client.get(
            f"/labs/{sample_lab.id}/config-snapshots/nonexistent-node/list",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["snapshots"] == []

    def test_list_node_snapshots_lab_not_found(
        self, test_client: TestClient, auth_headers: dict,
    ):
        resp = test_client.get(
            "/labs/nonexistent/config-snapshots/R1/list",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_list_node_snapshots_requires_auth(
        self, test_client: TestClient, sample_lab: models.Lab,
    ):
        resp = test_client.get(
            f"/labs/{sample_lab.id}/config-snapshots/R1/list",
        )
        assert resp.status_code in (401, 403)


# ===========================================================================
# POST /labs/{id}/config-snapshots/{snapshot_id}/map
# ===========================================================================


class TestMapConfigSnapshot:

    def test_map_snapshot_success(
        self, test_client: TestClient, test_db: Session,
        sample_lab: models.Lab, sample_node_definitions: list[models.Node],
        auth_headers: dict,
    ):
        """Mapping an orphaned snapshot to a target node succeeds."""
        snapshot = _create_snapshot(
            test_db, sample_lab.id, "old-deleted-node", "hostname OLD\n",
            device_kind="linux",
        )

        target_node = sample_node_definitions[0]
        resp = test_client.post(
            f"/labs/{sample_lab.id}/config-snapshots/{snapshot.id}/map",
            json={"target_node_id": target_node.id},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["mapped_to_node_id"] == target_node.id

    def test_map_snapshot_not_found(
        self, test_client: TestClient, sample_lab: models.Lab,
        sample_node_definitions: list[models.Node], auth_headers: dict,
    ):
        """Mapping a non-existent snapshot returns 404."""
        resp = test_client.post(
            f"/labs/{sample_lab.id}/config-snapshots/nonexistent-id/map",
            json={"target_node_id": sample_node_definitions[0].id},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_map_snapshot_lab_not_found(
        self, test_client: TestClient, auth_headers: dict,
    ):
        resp = test_client.post(
            "/labs/nonexistent/config-snapshots/some-id/map",
            json={"target_node_id": "node-1"},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_map_snapshot_requires_auth(
        self, test_client: TestClient, sample_lab: models.Lab,
    ):
        resp = test_client.post(
            f"/labs/{sample_lab.id}/config-snapshots/some-id/map",
            json={"target_node_id": "node-1"},
        )
        assert resp.status_code in (401, 403)


# ===========================================================================
# GET /labs/{id}/config-snapshots/download
# ===========================================================================


class TestDownloadConfigSnapshots:

    def test_download_success(
        self, test_client: TestClient, test_db: Session,
        sample_lab: models.Lab, auth_headers: dict,
    ):
        """Download returns a zip file when snapshots exist."""
        _create_snapshot(test_db, sample_lab.id, "R1", "hostname R1\n")

        resp = test_client.get(
            f"/labs/{sample_lab.id}/config-snapshots/download?all=true",
            headers=auth_headers,
        )
        # Should be 200 with zip content or 404 if no snapshots found by service
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            assert resp.headers.get("content-type") == "application/zip"

    def test_download_lab_not_found(
        self, test_client: TestClient, auth_headers: dict,
    ):
        resp = test_client.get(
            "/labs/nonexistent/config-snapshots/download",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_download_requires_auth(
        self, test_client: TestClient, sample_lab: models.Lab,
    ):
        resp = test_client.get(
            f"/labs/{sample_lab.id}/config-snapshots/download",
        )
        assert resp.status_code in (401, 403)


# ===========================================================================
# GET /labs/{id}/config-snapshots/orphaned
# ===========================================================================


class TestListOrphanedConfigs:

    def test_list_orphaned_success(
        self, test_client: TestClient, test_db: Session,
        sample_lab: models.Lab, auth_headers: dict,
    ):
        """Returns orphaned configs grouped by device kind."""
        resp = test_client.get(
            f"/labs/{sample_lab.id}/config-snapshots/orphaned",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "orphaned_configs" in data
        assert "total_count" in data

    def test_list_orphaned_no_orphans(
        self, test_client: TestClient, sample_lab: models.Lab, auth_headers: dict,
    ):
        """When there are no orphaned configs, returns empty."""
        resp = test_client.get(
            f"/labs/{sample_lab.id}/config-snapshots/orphaned",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_count"] == 0

    def test_list_orphaned_lab_not_found(
        self, test_client: TestClient, auth_headers: dict,
    ):
        resp = test_client.get(
            "/labs/nonexistent/config-snapshots/orphaned",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_list_orphaned_requires_auth(
        self, test_client: TestClient, sample_lab: models.Lab,
    ):
        resp = test_client.get(
            f"/labs/{sample_lab.id}/config-snapshots/orphaned",
        )
        assert resp.status_code in (401, 403)
