"""Batch 7: Labs router gap-fill tests.

Covers 8 untested endpoints in api/app/routers/labs.py:
- update_lab (PUT /labs/{id})
- export_graph (GET /labs/{id}/export-graph)
- download_lab_bundle (GET /labs/{id}/download-bundle)
- remove_layout (DELETE /labs/{id}/layout)
- check_nodes_ready (GET /labs/{id}/nodes/ready)
- poll_nodes_ready (GET /labs/{id}/nodes/ready/poll)
- get_node_interfaces (GET /labs/{id}/nodes/{node_id}/interfaces)
- sync_interface_mappings (POST /labs/{id}/interface-mappings/sync)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models, schemas
from app.state import HostStatus, LabState, NodeActualState, NodeDesiredState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_lab(
    db: Session,
    user: models.User,
    *,
    name: str = "test-lab",
    state: str = LabState.STOPPED,
    agent_id: str | None = None,
) -> models.Lab:
    lab = models.Lab(
        name=name, owner_id=user.id, provider="docker",
        state=state, agent_id=agent_id,
    )
    db.add(lab)
    db.commit()
    db.refresh(lab)
    return lab


def _make_node(
    db: Session,
    lab: models.Lab,
    *,
    name: str = "r1",
    device: str = "ceos",
    gui_id: str | None = None,
) -> models.Node:
    node = models.Node(
        lab_id=lab.id,
        name=name,
        container_name=name,
        kind="container",
        device=device,
        gui_id=gui_id or str(uuid4())[:8],
    )
    db.add(node)
    db.commit()
    db.refresh(node)
    return node


def _make_node_state(
    db: Session,
    lab: models.Lab,
    node_name: str,
    *,
    actual_state: str = NodeActualState.RUNNING,
    desired_state: str = NodeDesiredState.RUNNING,
    is_ready: bool = False,
) -> models.NodeState:
    ns = models.NodeState(
        lab_id=lab.id,
        node_name=node_name,
        node_id=str(uuid4())[:8],
        actual_state=actual_state,
        desired_state=desired_state,
        is_ready=is_ready,
    )
    db.add(ns)
    db.commit()
    db.refresh(ns)
    return ns


def _make_host(
    db: Session,
    *,
    name: str = "agent-1",
    address: str = "10.0.0.1:8001",
    status: str = HostStatus.ONLINE,
) -> models.Host:
    host = models.Host(
        id=str(uuid4())[:8],
        name=name,
        address=address,
        status=status,
        capabilities=json.dumps({"providers": ["docker"], "features": []}),
        version="0.4.0",
        last_heartbeat=datetime.now(timezone.utc),
    )
    db.add(host)
    db.commit()
    db.refresh(host)
    return host


# ---------------------------------------------------------------------------
# PUT /labs/{lab_id} — update_lab
# ---------------------------------------------------------------------------


class TestUpdateLab:
    """Tests for PUT /labs/{lab_id}."""

    def test_update_lab_name(
        self, test_client: TestClient, auth_headers: dict, test_db: Session,
        test_user: models.User,
    ):
        lab = _make_lab(test_db, test_user)
        resp = test_client.put(
            f"/labs/{lab.id}",
            json={"name": "Renamed Lab"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Renamed Lab"

    def test_update_lab_not_found(
        self, test_client: TestClient, auth_headers: dict,
    ):
        resp = test_client.put(
            "/labs/nonexistent",
            json={"name": "Nope"},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_update_lab_requires_auth(self, test_client: TestClient):
        resp = test_client.put("/labs/x", json={"name": "y"})
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /labs/{lab_id}/export-graph — export_graph
# ---------------------------------------------------------------------------


class TestExportGraph:
    """Tests for GET /labs/{lab_id}/export-graph."""

    def test_export_graph_no_topology(
        self, test_client: TestClient, auth_headers: dict, test_db: Session,
        test_user: models.User,
    ):
        lab = _make_lab(test_db, test_user)
        resp = test_client.get(
            f"/labs/{lab.id}/export-graph", headers=auth_headers,
        )
        # Empty lab with no nodes → 404
        assert resp.status_code == 404

    def test_export_graph_with_nodes(
        self, test_client: TestClient, auth_headers: dict, test_db: Session,
        test_user: models.User,
    ):
        lab = _make_lab(test_db, test_user)
        _make_node(test_db, lab, name="r1")
        resp = test_client.get(
            f"/labs/{lab.id}/export-graph", headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "nodes" in data
        assert "links" in data

    def test_export_graph_with_layout(
        self, test_client: TestClient, auth_headers: dict, test_db: Session,
        test_user: models.User,
    ):
        lab = _make_lab(test_db, test_user)
        _make_node(test_db, lab, name="r1")

        with patch("app.routers.labs.read_layout") as mock_layout:
            mock_layout.return_value = schemas.LabLayout(version=1)
            resp = test_client.get(
                f"/labs/{lab.id}/export-graph?include_layout=true",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "layout" in data

    def test_export_graph_not_found(
        self, test_client: TestClient, auth_headers: dict,
    ):
        resp = test_client.get("/labs/nonexistent/export-graph", headers=auth_headers)
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /labs/{lab_id}/download-bundle — download_lab_bundle
# ---------------------------------------------------------------------------


class TestDownloadBundle:
    """Tests for GET /labs/{lab_id}/download-bundle."""

    def test_download_bundle_empty_lab(
        self, test_client: TestClient, auth_headers: dict, test_db: Session,
        test_user: models.User,
    ):
        lab = _make_lab(test_db, test_user)
        with patch("app.routers.labs.read_layout", return_value=None), \
             patch("app.routers.labs.lab_workspace", return_value="/tmp/fake-workspace"):
            resp = test_client.get(
                f"/labs/{lab.id}/download-bundle", headers=auth_headers,
            )
        # Should return a zip (even for empty labs)
        assert resp.status_code == 200
        assert resp.headers.get("content-type") in (
            "application/zip",
            "application/x-zip-compressed",
            "application/octet-stream",
        )

    def test_download_bundle_not_found(
        self, test_client: TestClient, auth_headers: dict,
    ):
        resp = test_client.get("/labs/nonexistent/download-bundle", headers=auth_headers)
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /labs/{lab_id}/layout — remove_layout
# ---------------------------------------------------------------------------


class TestRemoveLayout:
    """Tests for DELETE /labs/{lab_id}/layout."""

    def test_remove_layout_success(
        self, test_client: TestClient, auth_headers: dict, test_db: Session,
        test_user: models.User,
    ):
        lab = _make_lab(test_db, test_user)
        with patch("app.routers.labs.delete_layout", return_value=True):
            resp = test_client.delete(
                f"/labs/{lab.id}/layout", headers=auth_headers,
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    def test_remove_layout_not_exists(
        self, test_client: TestClient, auth_headers: dict, test_db: Session,
        test_user: models.User,
    ):
        lab = _make_lab(test_db, test_user)
        with patch("app.routers.labs.delete_layout", return_value=False):
            resp = test_client.delete(
                f"/labs/{lab.id}/layout", headers=auth_headers,
            )
        assert resp.status_code == 404

    def test_remove_layout_not_found_lab(
        self, test_client: TestClient, auth_headers: dict,
    ):
        resp = test_client.delete("/labs/nonexistent/layout", headers=auth_headers)
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /labs/{lab_id}/nodes/ready — check_nodes_ready
# ---------------------------------------------------------------------------


class TestCheckNodesReady:
    """Tests for GET /labs/{lab_id}/nodes/ready."""

    def test_ready_empty_lab(
        self, test_client: TestClient, auth_headers: dict, test_db: Session,
        test_user: models.User,
    ):
        lab = _make_lab(test_db, test_user)
        resp = test_client.get(
            f"/labs/{lab.id}/nodes/ready", headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["lab_id"] == lab.id
        assert data["all_ready"] is True
        assert data["ready_count"] == 0
        assert data["nodes"] == []

    def test_ready_with_running_ready_nodes(
        self, test_client: TestClient, auth_headers: dict, test_db: Session,
        test_user: models.User,
    ):
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, state=LabState.RUNNING, agent_id=host.id)
        _make_node(test_db, lab, name="r1")
        _make_node_state(
            test_db, lab, "r1",
            actual_state=NodeActualState.RUNNING,
            desired_state=NodeDesiredState.RUNNING,
            is_ready=True,
        )
        with patch("app.routers.labs.agent_client") as mock_ac:
            mock_ac.is_agent_online.return_value = True
            mock_ac.check_node_readiness = AsyncMock(return_value={
                "is_ready": True, "progress_percent": 100, "message": "Ready",
            })
            resp = test_client.get(
                f"/labs/{lab.id}/nodes/ready", headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["all_ready"] is True
        assert data["ready_count"] == 1

    def test_ready_with_not_ready_node(
        self, test_client: TestClient, auth_headers: dict, test_db: Session,
        test_user: models.User,
    ):
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, state=LabState.RUNNING, agent_id=host.id)
        _make_node(test_db, lab, name="r1")
        _make_node_state(
            test_db, lab, "r1",
            actual_state=NodeActualState.RUNNING,
            desired_state=NodeDesiredState.RUNNING,
            is_ready=False,
        )
        with patch("app.routers.labs.agent_client") as mock_ac:
            mock_ac.is_agent_online.return_value = True
            mock_ac.check_node_readiness = AsyncMock(return_value={
                "is_ready": False, "progress_percent": 40, "message": "Booting...",
            })
            resp = test_client.get(
                f"/labs/{lab.id}/nodes/ready", headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["all_ready"] is False
        assert data["ready_count"] == 0
        assert data["nodes"][0]["progress_percent"] == 40

    def test_ready_lab_not_found(
        self, test_client: TestClient, auth_headers: dict,
    ):
        resp = test_client.get("/labs/nonexistent/nodes/ready", headers=auth_headers)
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /labs/{lab_id}/nodes/ready/poll — poll_nodes_ready
# ---------------------------------------------------------------------------


class TestPollNodesReady:
    """Tests for GET /labs/{lab_id}/nodes/ready/poll."""

    def test_poll_empty_lab_returns_immediately(
        self, test_client: TestClient, auth_headers: dict, test_db: Session,
        test_user: models.User,
    ):
        lab = _make_lab(test_db, test_user)
        resp = test_client.get(
            f"/labs/{lab.id}/nodes/ready/poll?timeout=10&interval=5",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        # No nodes should run → all_ready is True, returns immediately
        assert data["all_ready"] is True

    def test_poll_lab_not_found(
        self, test_client: TestClient, auth_headers: dict,
    ):
        resp = test_client.get("/labs/nonexistent/nodes/ready/poll", headers=auth_headers)
        assert resp.status_code == 404

    def test_poll_already_ready(
        self, test_client: TestClient, auth_headers: dict, test_db: Session,
        test_user: models.User,
    ):
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, state=LabState.RUNNING, agent_id=host.id)
        _make_node(test_db, lab, name="r1")
        _make_node_state(
            test_db, lab, "r1",
            actual_state=NodeActualState.RUNNING,
            desired_state=NodeDesiredState.RUNNING,
            is_ready=True,
        )
        with patch("app.routers.labs.agent_client") as mock_ac:
            mock_ac.is_agent_online.return_value = True
            mock_ac.check_node_readiness = AsyncMock(return_value={
                "is_ready": True, "progress_percent": 100, "message": "Ready",
            })
            resp = test_client.get(
                f"/labs/{lab.id}/nodes/ready/poll?timeout=10&interval=5",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["all_ready"] is True


# ---------------------------------------------------------------------------
# GET /labs/{lab_id}/nodes/{node_id}/interfaces — get_node_interfaces
# ---------------------------------------------------------------------------


class TestGetNodeInterfaces:
    """Tests for GET /labs/{lab_id}/nodes/{node_id}/interfaces."""

    def test_interfaces_empty(
        self, test_client: TestClient, auth_headers: dict, test_db: Session,
        test_user: models.User,
    ):
        lab = _make_lab(test_db, test_user)
        node = _make_node(test_db, lab, name="r1")
        resp = test_client.get(
            f"/labs/{lab.id}/nodes/{node.gui_id}/interfaces",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["mappings"] == []
        assert data["total"] == 0

    def test_interfaces_node_not_found(
        self, test_client: TestClient, auth_headers: dict, test_db: Session,
        test_user: models.User,
    ):
        lab = _make_lab(test_db, test_user)
        resp = test_client.get(
            f"/labs/{lab.id}/nodes/nonexistent/interfaces",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_interfaces_lab_not_found(
        self, test_client: TestClient, auth_headers: dict,
    ):
        resp = test_client.get(
            "/labs/nonexistent/nodes/x/interfaces", headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_interfaces_with_mappings(
        self, test_client: TestClient, auth_headers: dict, test_db: Session,
        test_user: models.User,
    ):
        lab = _make_lab(test_db, test_user)
        node = _make_node(test_db, lab, name="r1")
        mapping = models.InterfaceMapping(
            lab_id=lab.id,
            node_id=node.id,
            ovs_port="vhabcdef123",
            linux_interface="eth1",
            vendor_interface="Ethernet1",
            device_type="ceos",
        )
        test_db.add(mapping)
        test_db.commit()

        resp = test_client.get(
            f"/labs/{lab.id}/nodes/{node.gui_id}/interfaces",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["mappings"][0]["linux_interface"] == "eth1"
        assert data["mappings"][0]["vendor_interface"] == "Ethernet1"


# ---------------------------------------------------------------------------
# POST /labs/{lab_id}/interface-mappings/sync — sync_interface_mappings
# ---------------------------------------------------------------------------


class TestSyncInterfaceMappings:
    """Tests for POST /labs/{lab_id}/interface-mappings/sync."""

    def test_sync_lab_not_found(
        self, test_client: TestClient, auth_headers: dict,
    ):
        resp = test_client.post(
            "/labs/nonexistent/interface-mappings/sync", headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_sync_success(
        self, test_client: TestClient, auth_headers: dict, test_db: Session,
        test_user: models.User,
    ):
        lab = _make_lab(test_db, test_user)
        with patch("app.routers.labs.interface_mapping_service") as mock_svc:
            mock_svc.populate_all_agents = AsyncMock(return_value={
                "created": 3, "updated": 1, "errors": 0, "agents_queried": 1,
            })
            resp = test_client.post(
                f"/labs/{lab.id}/interface-mappings/sync",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] == 3
        assert data["updated"] == 1
        assert data["agents_queried"] == 1
