"""Extended tests for link_states router endpoints.

Covers: hot-connect, hot-disconnect, live-links, external connect,
link reconcile, list/get/set link states, refresh, set-all, link detail.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import app.routers.labs as _labs_pkg


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _auth_headers_for(user):
    from app.auth import create_access_token
    token = create_access_token(user.id)
    return {"Authorization": f"Bearer {token}"}


def _set_lab_state(test_db, lab, state):
    lab.state = state
    test_db.commit()
    test_db.refresh(lab)


def _add_nodes_and_link_state(test_db, lab, host):
    """Create Node + LinkState rows so endpoints find them."""
    from app import models

    n1 = models.Node(
        id="nd-1", lab_id=lab.id, gui_id="g1", display_name="R1",
        container_name="R1", device="linux", host_id=host.id,
    )
    n2 = models.Node(
        id="nd-2", lab_id=lab.id, gui_id="g2", display_name="R2",
        container_name="R2", device="linux", host_id=host.id,
    )
    test_db.add_all([n1, n2])
    test_db.flush()

    link_def = models.Link(
        id="ld-1", lab_id=lab.id, link_name="R1:eth1-R2:eth1",
        source_node_id=n1.id, source_interface="eth1",
        target_node_id=n2.id, target_interface="eth1",
    )
    test_db.add(link_def)
    test_db.flush()

    ls = models.LinkState(
        id="ls-1", lab_id=lab.id, link_definition_id=link_def.id,
        link_name="R1:eth1-R2:eth1",
        source_node="R1", source_interface="eth1",
        target_node="R2", target_interface="eth1",
        desired_state="up", actual_state="up",
        source_host_id=host.id, target_host_id=host.id,
        vlan_tag=100,
    )
    test_db.add(ls)
    test_db.commit()
    return n1, n2, link_def, ls


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def _patch_pkg(monkeypatch, sample_host):
    """Patch heavy dependencies resolved via _pkg() on the labs package."""
    mock_agent_client = MagicMock()
    mock_agent_client.hot_connect_on_agent = AsyncMock(return_value={"success": True, "vlan_tag": 200})
    mock_agent_client.hot_disconnect_on_agent = AsyncMock(return_value={"success": True})
    mock_agent_client.list_links_on_agent = AsyncMock(return_value={"links": [{"name": "R1:eth1-R2:eth1"}]})
    mock_agent_client.connect_external_on_agent = AsyncMock(return_value={"success": True, "vlan_tag": 300})
    mock_agent_client.disconnect_external_on_agent = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(_labs_pkg, "agent_client", mock_agent_client)

    mock_get_agent = AsyncMock(return_value=sample_host)
    monkeypatch.setattr(_labs_pkg, "get_online_agent_for_lab", mock_get_agent)

    mock_reconcile = AsyncMock(return_value={
        "checked": 2, "valid": 2, "repaired": 0, "errors": 0, "skipped": 0,
    })
    monkeypatch.setattr(_labs_pkg, "reconcile_lab_links", mock_reconcile)

    mock_build_host_map = AsyncMock(return_value={"host-1": sample_host})
    monkeypatch.setattr(_labs_pkg, "_build_host_to_agent_map", mock_build_host_map)

    mock_create_link = AsyncMock(return_value=True)
    monkeypatch.setattr(_labs_pkg, "create_link_if_ready", mock_create_link)

    mock_teardown_link = AsyncMock(return_value=True)
    monkeypatch.setattr(_labs_pkg, "teardown_link", mock_teardown_link)

    # sync_link_endpoint_reservations returns (ok, conflicts)
    monkeypatch.setattr(
        _labs_pkg, "sync_link_endpoint_reservations", lambda db, state: (True, [])
    )
    monkeypatch.setattr(
        _labs_pkg, "recompute_link_oper_state", lambda db, state: None
    )

    return {
        "agent_client": mock_agent_client,
        "get_online_agent_for_lab": mock_get_agent,
        "reconcile_lab_links": mock_reconcile,
        "build_host_map": mock_build_host_map,
        "create_link_if_ready": mock_create_link,
        "teardown_link": mock_teardown_link,
    }


# ===================================================================
# Hot-Connect
# ===================================================================

class TestHotConnect:
    """POST /labs/{lab_id}/hot-connect"""

    def test_hot_connect_success(
        self, test_client, test_db, sample_lab, sample_host, auth_headers, _patch_pkg,
    ):
        _set_lab_state(test_db, sample_lab, "running")
        _add_nodes_and_link_state(test_db, sample_lab, sample_host)
        resp = test_client.post(
            f"/labs/{sample_lab.id}/hot-connect",
            json={
                "source_node": "R1",
                "source_interface": "eth1",
                "target_node": "R2",
                "target_interface": "eth1",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True

    def test_hot_connect_lab_not_running(
        self, test_client, test_db, sample_lab, auth_headers, _patch_pkg,
    ):
        _set_lab_state(test_db, sample_lab, "stopped")
        resp = test_client.post(
            f"/labs/{sample_lab.id}/hot-connect",
            json={
                "source_node": "R1",
                "source_interface": "eth1",
                "target_node": "R2",
                "target_interface": "eth1",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "running" in resp.json()["detail"].lower()

    def test_hot_connect_lab_not_found(
        self, test_client, auth_headers, _patch_pkg,
    ):
        resp = test_client.post(
            "/labs/nonexistent/hot-connect",
            json={
                "source_node": "R1",
                "source_interface": "eth1",
                "target_node": "R2",
                "target_interface": "eth1",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_hot_connect_no_auth(
        self, test_client, sample_lab, _patch_pkg,
    ):
        resp = test_client.post(
            f"/labs/{sample_lab.id}/hot-connect",
            json={
                "source_node": "R1",
                "source_interface": "eth1",
                "target_node": "R2",
                "target_interface": "eth1",
            },
        )
        assert resp.status_code == 401

    def test_hot_connect_no_agent(
        self, test_client, test_db, sample_lab, sample_host, auth_headers, _patch_pkg,
    ):
        _set_lab_state(test_db, sample_lab, "running")
        _add_nodes_and_link_state(test_db, sample_lab, sample_host)
        _patch_pkg["build_host_map"].return_value = {}
        resp = test_client.post(
            f"/labs/{sample_lab.id}/hot-connect",
            json={
                "source_node": "R1",
                "source_interface": "eth1",
                "target_node": "R2",
                "target_interface": "eth1",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 503

    def test_hot_connect_create_link_fails(
        self, test_client, test_db, sample_lab, sample_host, auth_headers, _patch_pkg,
    ):
        _set_lab_state(test_db, sample_lab, "running")
        _add_nodes_and_link_state(test_db, sample_lab, sample_host)
        _patch_pkg["create_link_if_ready"].return_value = False
        resp = test_client.post(
            f"/labs/{sample_lab.id}/hot-connect",
            json={
                "source_node": "R1",
                "source_interface": "eth1",
                "target_node": "R2",
                "target_interface": "eth1",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert body.get("error")

    def test_hot_connect_starting_lab_allowed(
        self, test_client, test_db, sample_lab, sample_host, auth_headers, _patch_pkg,
    ):
        _set_lab_state(test_db, sample_lab, "starting")
        _add_nodes_and_link_state(test_db, sample_lab, sample_host)
        resp = test_client.post(
            f"/labs/{sample_lab.id}/hot-connect",
            json={
                "source_node": "R1",
                "source_interface": "eth1",
                "target_node": "R2",
                "target_interface": "eth1",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True


# ===================================================================
# Hot-Disconnect
# ===================================================================

class TestHotDisconnect:
    """DELETE /labs/{lab_id}/hot-disconnect/{link_id}"""

    def test_hot_disconnect_success(
        self, test_client, test_db, sample_lab, sample_host, auth_headers, _patch_pkg,
    ):
        _set_lab_state(test_db, sample_lab, "running")
        _, _, _, ls = _add_nodes_and_link_state(test_db, sample_lab, sample_host)
        resp = test_client.delete(
            f"/labs/{sample_lab.id}/hot-disconnect/{ls.link_name}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_hot_disconnect_link_not_found(
        self, test_client, test_db, sample_lab, sample_host, auth_headers, _patch_pkg,
    ):
        _set_lab_state(test_db, sample_lab, "running")
        resp = test_client.delete(
            f"/labs/{sample_lab.id}/hot-disconnect/NoSuchLink",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert "not found" in body["error"].lower()

    def test_hot_disconnect_no_agent(
        self, test_client, test_db, sample_lab, sample_host, auth_headers, _patch_pkg,
    ):
        _set_lab_state(test_db, sample_lab, "running")
        _add_nodes_and_link_state(test_db, sample_lab, sample_host)
        _patch_pkg["build_host_map"].return_value = {}
        resp = test_client.delete(
            f"/labs/{sample_lab.id}/hot-disconnect/R1:eth1-R2:eth1",
            headers=auth_headers,
        )
        assert resp.status_code == 503

    def test_hot_disconnect_teardown_fails(
        self, test_client, test_db, sample_lab, sample_host, auth_headers, _patch_pkg,
    ):
        _set_lab_state(test_db, sample_lab, "running")
        _add_nodes_and_link_state(test_db, sample_lab, sample_host)
        _patch_pkg["teardown_link"].return_value = False
        resp = test_client.delete(
            f"/labs/{sample_lab.id}/hot-disconnect/R1:eth1-R2:eth1",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is False

    def test_hot_disconnect_no_auth(
        self, test_client, sample_lab, _patch_pkg,
    ):
        resp = test_client.delete(
            f"/labs/{sample_lab.id}/hot-disconnect/R1:eth1-R2:eth1",
        )
        assert resp.status_code == 401


# ===================================================================
# Live Links
# ===================================================================

class TestLiveLinks:
    """GET /labs/{lab_id}/live-links"""

    def test_live_links_success(
        self, test_client, test_db, sample_lab, auth_headers, _patch_pkg,
    ):
        resp = test_client.get(
            f"/labs/{sample_lab.id}/live-links",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "links" in body

    def test_live_links_no_agent(
        self, test_client, test_db, sample_lab, auth_headers, _patch_pkg,
    ):
        _patch_pkg["get_online_agent_for_lab"].return_value = None
        resp = test_client.get(
            f"/labs/{sample_lab.id}/live-links",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["links"] == []
        assert "error" in body

    def test_live_links_lab_not_found(
        self, test_client, auth_headers, _patch_pkg,
    ):
        resp = test_client.get(
            "/labs/nonexistent/live-links",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_live_links_no_auth(
        self, test_client, sample_lab, _patch_pkg,
    ):
        resp = test_client.get(
            f"/labs/{sample_lab.id}/live-links",
        )
        assert resp.status_code == 401


# ===================================================================
# External Connect
# ===================================================================

class TestExternalConnect:
    """POST /labs/{lab_id}/external/connect"""

    def test_external_connect_success(
        self, test_client, test_db, sample_lab, auth_headers, _patch_pkg,
    ):
        _set_lab_state(test_db, sample_lab, "running")
        resp = test_client.post(
            f"/labs/{sample_lab.id}/external/connect",
            json={
                "node_name": "R1",
                "interface_name": "eth1",
                "external_interface": "ens192",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True

    def test_external_connect_lab_not_running(
        self, test_client, test_db, sample_lab, auth_headers, _patch_pkg,
    ):
        _set_lab_state(test_db, sample_lab, "stopped")
        resp = test_client.post(
            f"/labs/{sample_lab.id}/external/connect",
            json={
                "node_name": "R1",
                "interface_name": "eth1",
                "external_interface": "ens192",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_external_connect_no_agent(
        self, test_client, test_db, sample_lab, auth_headers, _patch_pkg,
    ):
        _set_lab_state(test_db, sample_lab, "running")
        _patch_pkg["get_online_agent_for_lab"].return_value = None
        resp = test_client.post(
            f"/labs/{sample_lab.id}/external/connect",
            json={
                "node_name": "R1",
                "interface_name": "eth1",
                "external_interface": "ens192",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 503

    def test_external_connect_with_vlan_tag(
        self, test_client, test_db, sample_lab, auth_headers, _patch_pkg,
    ):
        _set_lab_state(test_db, sample_lab, "running")
        resp = test_client.post(
            f"/labs/{sample_lab.id}/external/connect",
            json={
                "node_name": "R1",
                "interface_name": "eth1",
                "external_interface": "ens192",
                "vlan_tag": 100,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_external_connect_no_auth(
        self, test_client, sample_lab, _patch_pkg,
    ):
        resp = test_client.post(
            f"/labs/{sample_lab.id}/external/connect",
            json={
                "node_name": "R1",
                "interface_name": "eth1",
                "external_interface": "ens192",
            },
        )
        assert resp.status_code == 401


# ===================================================================
# Link Reconciliation
# ===================================================================

class TestLinkReconcile:
    """POST /labs/{lab_id}/links/reconcile"""

    def test_reconcile_success(
        self, test_client, test_db, sample_lab, auth_headers, _patch_pkg,
    ):
        resp = test_client.post(
            f"/labs/{sample_lab.id}/links/reconcile",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["checked"] == 2
        assert body["valid"] == 2
        assert body["repaired"] == 0
        assert body["errors"] == 0
        assert body["skipped"] == 0

    def test_reconcile_lab_not_found(
        self, test_client, auth_headers, _patch_pkg,
    ):
        resp = test_client.post(
            "/labs/nonexistent/links/reconcile",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_reconcile_no_auth(
        self, test_client, sample_lab, _patch_pkg,
    ):
        resp = test_client.post(
            f"/labs/{sample_lab.id}/links/reconcile",
        )
        assert resp.status_code == 401

    def test_reconcile_with_repairs(
        self, test_client, test_db, sample_lab, auth_headers, _patch_pkg,
    ):
        _patch_pkg["reconcile_lab_links"] = AsyncMock(return_value={
            "checked": 3, "valid": 1, "repaired": 2, "errors": 0, "skipped": 0,
        })
        # Re-monkeypatch since we replaced the mock
        import app.routers.labs as pkg
        pkg.reconcile_lab_links = _patch_pkg["reconcile_lab_links"]

        resp = test_client.post(
            f"/labs/{sample_lab.id}/links/reconcile",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["repaired"] == 2


# ===================================================================
# List Link States
# ===================================================================

class TestListLinkStates:
    """GET /labs/{lab_id}/links/states"""

    def test_list_link_states_empty(
        self, test_client, test_db, sample_lab, auth_headers, _patch_pkg, monkeypatch,
    ):
        # Patch TopologyService to have no nodes
        mock_service = MagicMock()
        mock_service.has_nodes.return_value = False
        monkeypatch.setattr(_labs_pkg, "TopologyService", lambda db: mock_service)

        resp = test_client.get(
            f"/labs/{sample_lab.id}/links/states",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["links"] == []

    def test_list_link_states_with_data(
        self, test_client, test_db, sample_lab, sample_host, auth_headers, _patch_pkg, monkeypatch,
    ):
        _add_nodes_and_link_state(test_db, sample_lab, sample_host)
        mock_service = MagicMock()
        mock_service.has_nodes.return_value = False
        monkeypatch.setattr(_labs_pkg, "TopologyService", lambda db: mock_service)

        resp = test_client.get(
            f"/labs/{sample_lab.id}/links/states",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        links = resp.json()["links"]
        assert len(links) >= 1
        assert links[0]["link_name"] == "R1:eth1-R2:eth1"

    def test_list_link_states_not_found(
        self, test_client, auth_headers, _patch_pkg,
    ):
        resp = test_client.get(
            "/labs/nonexistent/links/states",
            headers=auth_headers,
        )
        assert resp.status_code == 404


# ===================================================================
# Get Single Link State
# ===================================================================

class TestGetLinkState:
    """GET /labs/{lab_id}/links/{link_name}/state"""

    def test_get_link_state_success(
        self, test_client, test_db, sample_lab, sample_host, auth_headers, _patch_pkg, monkeypatch,
    ):
        _add_nodes_and_link_state(test_db, sample_lab, sample_host)
        mock_service = MagicMock()
        mock_service.has_nodes.return_value = False
        monkeypatch.setattr(_labs_pkg, "TopologyService", lambda db: mock_service)

        resp = test_client.get(
            f"/labs/{sample_lab.id}/links/R1:eth1-R2:eth1/state",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["link_name"] == "R1:eth1-R2:eth1"

    def test_get_link_state_not_found(
        self, test_client, test_db, sample_lab, auth_headers, _patch_pkg, monkeypatch,
    ):
        mock_service = MagicMock()
        mock_service.has_nodes.return_value = False
        monkeypatch.setattr(_labs_pkg, "TopologyService", lambda db: mock_service)

        resp = test_client.get(
            f"/labs/{sample_lab.id}/links/NoSuch:eth1-Link:eth1/state",
            headers=auth_headers,
        )
        assert resp.status_code == 404


# ===================================================================
# Set Link State (PUT)
# ===================================================================

class TestSetLinkState:
    """PUT /labs/{lab_id}/links/{link_name}/state"""

    def test_set_link_state_up(
        self, test_client, test_db, sample_lab, sample_host, auth_headers, _patch_pkg, monkeypatch,
    ):
        _add_nodes_and_link_state(test_db, sample_lab, sample_host)
        mock_service = MagicMock()
        mock_service.has_nodes.return_value = False
        monkeypatch.setattr(_labs_pkg, "TopologyService", lambda db: mock_service)

        resp = test_client.put(
            f"/labs/{sample_lab.id}/links/R1:eth1-R2:eth1/state",
            json={"state": "up"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["desired_state"] == "up"

    def test_set_link_state_down(
        self, test_client, test_db, sample_lab, sample_host, auth_headers, _patch_pkg, monkeypatch,
    ):
        _add_nodes_and_link_state(test_db, sample_lab, sample_host)
        mock_service = MagicMock()
        mock_service.has_nodes.return_value = False
        monkeypatch.setattr(_labs_pkg, "TopologyService", lambda db: mock_service)

        resp = test_client.put(
            f"/labs/{sample_lab.id}/links/R1:eth1-R2:eth1/state",
            json={"state": "down"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["desired_state"] == "down"

    def test_set_link_state_not_found(
        self, test_client, test_db, sample_lab, auth_headers, _patch_pkg, monkeypatch,
    ):
        mock_service = MagicMock()
        mock_service.has_nodes.return_value = False
        monkeypatch.setattr(_labs_pkg, "TopologyService", lambda db: mock_service)

        resp = test_client.put(
            f"/labs/{sample_lab.id}/links/NoLink:eth1-Here:eth1/state",
            json={"state": "up"},
            headers=auth_headers,
        )
        assert resp.status_code == 404


# ===================================================================
# Link Detail
# ===================================================================

class TestGetLinkDetail:
    """GET /labs/{lab_id}/links/{link_name}/detail"""

    def test_get_link_detail_success(
        self, test_client, test_db, sample_lab, sample_host, auth_headers, _patch_pkg, monkeypatch,
    ):
        _add_nodes_and_link_state(test_db, sample_lab, sample_host)
        mock_service = MagicMock()
        mock_service.has_nodes.return_value = False
        monkeypatch.setattr(_labs_pkg, "TopologyService", lambda db: mock_service)

        resp = test_client.get(
            f"/labs/{sample_lab.id}/links/R1:eth1-R2:eth1/detail",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["link_name"] == "R1:eth1-R2:eth1"
        assert body["source"]["node_name"] == "R1"
        assert body["target"]["node_name"] == "R2"
        assert body["tunnel"] is None  # same-host link

    def test_get_link_detail_not_found(
        self, test_client, test_db, sample_lab, auth_headers, _patch_pkg, monkeypatch,
    ):
        mock_service = MagicMock()
        mock_service.has_nodes.return_value = False
        monkeypatch.setattr(_labs_pkg, "TopologyService", lambda db: mock_service)

        resp = test_client.get(
            f"/labs/{sample_lab.id}/links/Nope:eth1-Gone:eth1/detail",
            headers=auth_headers,
        )
        assert resp.status_code == 404
