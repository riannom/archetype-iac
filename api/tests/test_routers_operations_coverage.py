"""Tests for api/app/routers/labs/operations.py — config diff, cleanup orphans,
inventory export, infra notifications, and interface mappings.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from app import models


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config_snapshot(
    db: Session,
    lab_id: str,
    node_name: str,
    content: str,
    *,
    snapshot_type: str = "manual",
) -> models.ConfigSnapshot:
    snap = models.ConfigSnapshot(
        lab_id=lab_id,
        node_name=node_name,
        content=content,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        snapshot_type=snapshot_type,
        created_at=datetime.now(timezone.utc),
    )
    db.add(snap)
    db.commit()
    db.refresh(snap)
    return snap


def _make_node_state(
    db: Session,
    lab_id: str,
    node_id: str,
    node_name: str,
    *,
    desired: str = "stopped",
    actual: str = "undeployed",
    is_ready: bool = False,
    error_message: str | None = None,
) -> models.NodeState:
    ns = models.NodeState(
        lab_id=lab_id,
        node_id=node_id,
        node_name=node_name,
        desired_state=desired,
        actual_state=actual,
        is_ready=is_ready,
        error_message=error_message,
    )
    db.add(ns)
    db.commit()
    db.refresh(ns)
    return ns


# ============================================================================
# 1. Config Diff — happy path
# ============================================================================


class TestConfigDiff:
    def test_config_diff_returns_structured_diff(
        self, test_client, auth_headers, test_db, sample_lab
    ):
        snap_a = _make_config_snapshot(
            test_db, sample_lab.id, "R1", "hostname R1\ninterface eth0\n ip address 10.0.0.1/24\n"
        )
        snap_b = _make_config_snapshot(
            test_db, sample_lab.id, "R1", "hostname R1\ninterface eth0\n ip address 10.0.0.2/24\n"
        )

        resp = test_client.post(
            f"/labs/{sample_lab.id}/config-diff",
            json={"snapshot_id_a": snap_a.id, "snapshot_id_b": snap_b.id},
            headers=auth_headers,
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["additions"] >= 1
        assert data["deletions"] >= 1
        assert len(data["diff_lines"]) > 0
        # Verify structured line types
        types_found = {line["type"] for line in data["diff_lines"]}
        assert "header" in types_found

    def test_config_diff_snapshot_a_not_found(
        self, test_client, auth_headers, test_db, sample_lab
    ):
        snap_b = _make_config_snapshot(test_db, sample_lab.id, "R1", "content")
        resp = test_client.post(
            f"/labs/{sample_lab.id}/config-diff",
            json={"snapshot_id_a": "nonexistent", "snapshot_id_b": snap_b.id},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_config_diff_snapshot_b_not_found(
        self, test_client, auth_headers, test_db, sample_lab
    ):
        snap_a = _make_config_snapshot(test_db, sample_lab.id, "R1", "content")
        resp = test_client.post(
            f"/labs/{sample_lab.id}/config-diff",
            json={"snapshot_id_a": snap_a.id, "snapshot_id_b": "nonexistent"},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_config_diff_identical_content(
        self, test_client, auth_headers, test_db, sample_lab
    ):
        content = "hostname R1\n"
        snap_a = _make_config_snapshot(test_db, sample_lab.id, "R1", content)
        snap_b = _make_config_snapshot(test_db, sample_lab.id, "R1", content)

        resp = test_client.post(
            f"/labs/{sample_lab.id}/config-diff",
            json={"snapshot_id_a": snap_a.id, "snapshot_id_b": snap_b.id},
            headers=auth_headers,
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["additions"] == 0
        assert data["deletions"] == 0


# ============================================================================
# 2. Cleanup Orphans
# ============================================================================


class TestCleanupOrphans:
    def test_cleanup_orphans_success(
        self, test_client, auth_headers, test_db, sample_lab, sample_host, monkeypatch
    ):
        """Cleanup calls agent and returns removed containers."""
        import app.routers.labs as labs_pkg

        mock_ac = MagicMock()
        mock_ac.cleanup_lab_orphans = AsyncMock(
            return_value={"removed_containers": ["old-container-1"], "errors": []}
        )
        monkeypatch.setattr(labs_pkg, "agent_client", mock_ac)

        resp = test_client.post(
            f"/labs/{sample_lab.id}/cleanup-orphans",
            headers=auth_headers,
        )

        assert resp.status_code == 200
        data = resp.json()
        assert sample_host.name in data["removed_by_agent"]
        assert "old-container-1" in data["removed_by_agent"][sample_host.name]
        assert data["errors"] == []

    def test_cleanup_orphans_agent_error(
        self, test_client, auth_headers, test_db, sample_lab, sample_host, monkeypatch
    ):
        """Agent failure is captured in errors list, not raised."""
        import app.routers.labs as labs_pkg

        mock_ac = MagicMock()
        mock_ac.cleanup_lab_orphans = AsyncMock(side_effect=RuntimeError("connection refused"))
        monkeypatch.setattr(labs_pkg, "agent_client", mock_ac)

        resp = test_client.post(
            f"/labs/{sample_lab.id}/cleanup-orphans",
            headers=auth_headers,
        )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["errors"]) == 1
        assert "connection refused" in data["errors"][0]


# ============================================================================
# 3. Inventory Export
# ============================================================================


class TestInventoryExport:
    def test_inventory_json_format(
        self, test_client, auth_headers, test_db, sample_lab, monkeypatch
    ):
        _make_node_state(test_db, sample_lab.id, "n1", "R1")

        # Mock TopologyService so it doesn't try real DB queries for graph export
        import app.routers.labs as labs_pkg
        mock_ts_cls = MagicMock()
        mock_ts_inst = MagicMock()
        mock_ts_inst.has_nodes.return_value = False
        mock_ts_cls.return_value = mock_ts_inst
        monkeypatch.setattr(labs_pkg, "TopologyService", mock_ts_cls)

        resp = test_client.get(
            f"/labs/{sample_lab.id}/inventory?format=json",
            headers=auth_headers,
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["lab_id"] == sample_lab.id
        assert data["format"] == "json"
        assert len(data["nodes"]) == 1
        assert data["nodes"][0]["node_name"] == "R1"
        # json format has no formatted content
        assert data["content"] is None

    def test_inventory_ansible_format(
        self, test_client, auth_headers, test_db, sample_lab, monkeypatch
    ):
        ns = _make_node_state(test_db, sample_lab.id, "n1", "R1")
        ns.management_ip = "10.0.0.1"
        test_db.commit()

        import app.routers.labs as labs_pkg
        mock_ts_cls = MagicMock()
        mock_ts_inst = MagicMock()
        mock_ts_inst.has_nodes.return_value = False
        mock_ts_cls.return_value = mock_ts_inst
        monkeypatch.setattr(labs_pkg, "TopologyService", mock_ts_cls)

        resp = test_client.get(
            f"/labs/{sample_lab.id}/inventory?format=ansible",
            headers=auth_headers,
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["format"] == "ansible"
        assert data["content"] is not None
        assert "ansible_host" in data["content"]
        assert "10.0.0.1" in data["content"]


# ============================================================================
# 4. Infra Notifications
# ============================================================================


class TestInfraNotifications:
    def test_no_notifications_clean_lab(
        self, test_client, auth_headers, test_db, sample_lab
    ):
        resp = test_client.get(
            f"/labs/{sample_lab.id}/infra/notifications",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["notifications"] == []

    def test_tunnel_failed_notification(
        self, test_client, auth_headers, test_db, sample_lab, multiple_hosts
    ):
        # Create a failed VxlanTunnel
        link_state = models.LinkState(
            lab_id=sample_lab.id,
            link_name="R1:eth1-R3:eth1",
            source_node="R1",
            source_interface="eth1",
            target_node="R3",
            target_interface="eth1",
            desired_state="up",
            actual_state="pending",
            is_cross_host=True,
            source_host_id=multiple_hosts[0].id,
            target_host_id=multiple_hosts[1].id,
        )
        test_db.add(link_state)
        test_db.flush()

        tunnel = models.VxlanTunnel(
            lab_id=sample_lab.id,
            link_state_id=link_state.id,
            vni=99999,
            vlan_tag=500,
            agent_a_id=multiple_hosts[0].id,
            agent_a_ip="192.168.1.1",
            agent_b_id=multiple_hosts[1].id,
            agent_b_ip="192.168.1.2",
            status="failed",
            error_message="VXLAN port creation timeout",
        )
        test_db.add(tunnel)
        test_db.commit()

        resp = test_client.get(
            f"/labs/{sample_lab.id}/infra/notifications",
            headers=auth_headers,
        )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["notifications"]) >= 1
        tunnel_notif = [n for n in data["notifications"] if n["category"] == "tunnel_failed"]
        assert len(tunnel_notif) == 1
        assert tunnel_notif[0]["severity"] == "error"
        assert "VNI 99999" in tunnel_notif[0]["title"]

    def test_node_error_notification(
        self, test_client, auth_headers, test_db, sample_lab
    ):
        _make_node_state(
            test_db, sample_lab.id, "n1", "R1",
            actual="error", error_message="OOM killed",
        )

        resp = test_client.get(
            f"/labs/{sample_lab.id}/infra/notifications",
            headers=auth_headers,
        )

        assert resp.status_code == 200
        data = resp.json()
        node_notifs = [n for n in data["notifications"] if n["category"] == "node_error"]
        assert len(node_notifs) == 1
        assert node_notifs[0]["severity"] == "error"
        assert "R1" in node_notifs[0]["title"]
