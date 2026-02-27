"""Tests for agent overlay, tunnel, external connectivity, and bridge endpoints.

Source: agent/routers/overlay.py
Covers: declare-state, create/delete tunnel, bridge-ports, set-port-vlan,
        port-state, external-connect/disconnect, reconcile-ports, attach/detach-link,
        overlay status, cleanup, bridge patch, MTU test.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from agent.config import settings
from agent.main import app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch):
    monkeypatch.setattr(settings, "controller_secret", "")


# ---------------------------------------------------------------------------
# TestCreateTunnel
# ---------------------------------------------------------------------------


class TestCreateTunnel:
    """Tests for POST /overlay/tunnel."""

    def test_vxlan_disabled_returns_error(self, client, monkeypatch):
        """Returns failure when VXLAN is disabled."""
        monkeypatch.setattr(settings, "enable_vxlan", False)
        resp = client.post("/overlay/tunnel", json={
            "lab_id": "lab1", "link_id": "lk1",
            "local_ip": "10.0.0.1", "remote_ip": "10.0.0.2", "vni": 5000,
        })
        body = resp.json()
        assert body["success"] is False
        assert "not enabled" in body["error"].lower()

    def test_success(self, client, monkeypatch):
        """Successful tunnel creation returns tunnel info."""
        monkeypatch.setattr(settings, "enable_vxlan", True)

        mock_tunnel = SimpleNamespace(
            vni=5000, interface_name="vxlan-lk1",
            local_ip="10.0.0.1", remote_ip="10.0.0.2",
            lab_id="lab1", link_id="lk1", vlan_tag=100,
        )
        backend = MagicMock()
        backend.overlay_create_tunnel = AsyncMock(return_value=mock_tunnel)
        backend.overlay_create_bridge = AsyncMock()

        with patch("agent.routers.overlay.get_network_backend", return_value=backend):
            resp = client.post("/overlay/tunnel", json={
                "lab_id": "lab1", "link_id": "lk1",
                "local_ip": "10.0.0.1", "remote_ip": "10.0.0.2", "vni": 5000,
            })

        body = resp.json()
        assert body["success"] is True
        assert body["tunnel"]["vni"] == 5000

    def test_backend_exception(self, client, monkeypatch):
        """Backend error surfaces in response."""
        monkeypatch.setattr(settings, "enable_vxlan", True)

        backend = MagicMock()
        backend.overlay_create_tunnel = AsyncMock(side_effect=RuntimeError("OVS down"))

        with patch("agent.routers.overlay.get_network_backend", return_value=backend):
            resp = client.post("/overlay/tunnel", json={
                "lab_id": "lab1", "link_id": "lk1",
                "local_ip": "10.0.0.1", "remote_ip": "10.0.0.2", "vni": 5000,
            })

        body = resp.json()
        assert body["success"] is False
        assert "OVS down" in body["error"]


# ---------------------------------------------------------------------------
# TestDeclareState
# ---------------------------------------------------------------------------


class TestDeclareState:
    """Tests for POST /overlay/declare-state."""

    def test_declare_state_converges_tunnels(self, client):
        """Declare state creates/updates tunnels to match desired."""
        overlay_mgr = MagicMock()
        overlay_mgr.declare_state = AsyncMock(return_value={
            "results": [
                {"link_id": "lk1", "lab_id": "lab1", "status": "created", "actual_vlan": 100},
            ],
            "orphans_removed": [],
        })

        with patch("agent.routers.overlay.get_overlay_manager", return_value=overlay_mgr):
            resp = client.post("/overlay/declare-state", json={
                "tunnels": [
                    {"link_id": "lk1", "lab_id": "lab1",
                     "local_ip": "10.0.0.1", "remote_ip": "10.0.0.2",
                     "vni": 5001, "expected_vlan": 100, "port_name": "vxlan-lk1"},
                ],
                "declared_labs": ["lab1"],
            })

        body = resp.json()
        assert body["results"][0]["status"] == "created"
        assert body["orphans_removed"] == []

    def test_declare_state_removes_orphans(self, client):
        """Orphan ports not in declared set are removed."""
        overlay_mgr = MagicMock()
        overlay_mgr.declare_state = AsyncMock(return_value={
            "results": [],
            "orphans_removed": ["vxlan-stale1"],
        })

        with patch("agent.routers.overlay.get_overlay_manager", return_value=overlay_mgr):
            resp = client.post("/overlay/declare-state", json={
                "tunnels": [],
                "declared_labs": ["lab1"],
            })

        body = resp.json()
        assert "vxlan-stale1" in body["orphans_removed"]


# ---------------------------------------------------------------------------
# TestOverlayStatus
# ---------------------------------------------------------------------------


class TestOverlayStatus:
    """Tests for GET /overlay/status."""

    def test_vxlan_disabled_returns_empty(self, client, monkeypatch):
        """Returns empty status when VXLAN is disabled."""
        monkeypatch.setattr(settings, "enable_vxlan", False)
        resp = client.get("/overlay/status")
        body = resp.json()
        assert body.get("tunnels", []) == []

    def test_returns_tunnel_list(self, client, monkeypatch):
        """Returns populated tunnel list from backend."""
        monkeypatch.setattr(settings, "enable_vxlan", True)

        backend = MagicMock()
        backend.overlay_status.return_value = {
            "tunnels": [
                {"vni": 5000, "interface": "vxlan-lk1",
                 "local_ip": "10.0.0.1", "remote_ip": "10.0.0.2",
                 "lab_id": "lab1", "link_id": "lk1"},
            ],
            "bridges": [],
            "vteps": [],
            "link_tunnels": [],
        }

        with patch("agent.routers.overlay.get_network_backend", return_value=backend):
            resp = client.get("/overlay/status")

        body = resp.json()
        assert len(body["tunnels"]) == 1
        assert body["tunnels"][0]["vni"] == 5000


# ---------------------------------------------------------------------------
# TestDeleteBridgePort
# ---------------------------------------------------------------------------


class TestDeleteBridgePort:
    """Tests for DELETE /overlay/bridge-ports/{port_name}."""

    def test_invalid_port_name_rejected(self, client):
        """Invalid port names are rejected."""
        with patch("agent.routers.overlay._validate_port_name", return_value=False):
            resp = client.delete("/overlay/bridge-ports/bad-port")

        body = resp.json()
        assert body["deleted"] is False
        assert "Invalid" in body["message"]

    def test_valid_deletion(self, client):
        """Valid port name triggers ovs-vsctl del-port."""
        async def _fake_subprocess(*args, **kwargs):
            proc = MagicMock()
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(b"", b""))
            return proc

        with patch("agent.routers.overlay._validate_port_name", return_value=True):
            with patch("agent.routers.overlay.asyncio.create_subprocess_exec",
                       side_effect=_fake_subprocess):
                resp = client.delete("/overlay/bridge-ports/vxlan-lk1")

        body = resp.json()
        assert body["deleted"] is True


# ---------------------------------------------------------------------------
# TestSetOverlayPortVlan
# ---------------------------------------------------------------------------


class TestSetOverlayPortVlan:
    """Tests for PUT /overlay/ports/{port_name}/vlan."""

    def test_invalid_port_name(self, client):
        """Invalid port name returns failure."""
        with patch("agent.routers.overlay._validate_port_name", return_value=False):
            resp = client.put("/overlay/ports/bad-name/vlan", json={"vlan_tag": 100})
        body = resp.json()
        assert body["success"] is False

    def test_missing_vlan_tag(self, client):
        """Missing or non-integer vlan_tag returns error."""
        with patch("agent.routers.overlay._validate_port_name", return_value=True):
            resp = client.put("/overlay/ports/vh-port/vlan", json={})
        body = resp.json()
        assert body["success"] is False
        assert "vlan_tag" in body["error"]

    def test_success(self, client):
        """Successful VLAN set returns port and tag."""
        with patch("agent.routers.overlay._validate_port_name", return_value=True):
            with patch("agent.routers.overlay._ovs_set_port_vlan",
                       new_callable=AsyncMock, return_value=True):
                resp = client.put("/overlay/ports/vh-port/vlan", json={"vlan_tag": 200})
        body = resp.json()
        assert body["success"] is True
        assert body["vlan_tag"] == 200


# ---------------------------------------------------------------------------
# TestOverlayCleanup
# ---------------------------------------------------------------------------


class TestOverlayCleanup:
    """Tests for POST /overlay/cleanup."""

    def test_vxlan_disabled_returns_empty(self, client, monkeypatch):
        """Returns empty cleanup result when VXLAN disabled."""
        monkeypatch.setattr(settings, "enable_vxlan", False)
        resp = client.post("/overlay/cleanup", json={"lab_id": "lab1"})
        assert resp.status_code == 200

    def test_success(self, client, monkeypatch):
        """Successful cleanup returns deletion counts."""
        monkeypatch.setattr(settings, "enable_vxlan", True)

        backend = MagicMock()
        backend.overlay_cleanup_lab = AsyncMock(return_value={
            "tunnels_deleted": 2, "bridges_deleted": 1, "errors": [],
        })

        with patch("agent.routers.overlay.get_network_backend", return_value=backend):
            resp = client.post("/overlay/cleanup", json={"lab_id": "lab1"})

        body = resp.json()
        assert body["tunnels_deleted"] == 2
        assert body["bridges_deleted"] == 1


# ---------------------------------------------------------------------------
# TestEnsureVtep
# ---------------------------------------------------------------------------


class TestEnsureVtep:
    """Tests for POST /overlay/vtep."""

    def test_vxlan_disabled(self, client, monkeypatch):
        """Returns error when VXLAN is disabled."""
        monkeypatch.setattr(settings, "enable_vxlan", False)
        resp = client.post("/overlay/vtep", json={
            "local_ip": "10.0.0.1", "remote_ip": "10.0.0.2",
            "remote_host_id": "agent-02",
        })
        body = resp.json()
        assert body["success"] is False

    def test_returns_existing_vtep(self, client, monkeypatch):
        """Returns existing VTEP without creating a new one."""
        monkeypatch.setattr(settings, "enable_vxlan", True)

        existing = SimpleNamespace(
            interface_name="vtep-agent02", vni=4789,
            local_ip="10.0.0.1", remote_ip="10.0.0.2",
            remote_host_id="agent-02", tenant_mtu=8950,
        )
        backend = MagicMock()
        backend.overlay_get_vtep.return_value = existing

        with patch("agent.routers.overlay.get_network_backend", return_value=backend):
            resp = client.post("/overlay/vtep", json={
                "local_ip": "10.0.0.1", "remote_ip": "10.0.0.2",
                "remote_host_id": "agent-02",
            })

        body = resp.json()
        assert body["success"] is True
        assert body["created"] is False

    def test_creates_new_vtep(self, client, monkeypatch):
        """Creates a new VTEP when none exists."""
        monkeypatch.setattr(settings, "enable_vxlan", True)

        new_vtep = SimpleNamespace(
            interface_name="vtep-agent02", vni=4789,
            local_ip="10.0.0.1", remote_ip="10.0.0.2",
            remote_host_id="agent-02", tenant_mtu=8950,
        )
        backend = MagicMock()
        backend.overlay_get_vtep.return_value = None
        backend.overlay_ensure_vtep = AsyncMock(return_value=new_vtep)

        with patch("agent.routers.overlay.get_network_backend", return_value=backend):
            resp = client.post("/overlay/vtep", json={
                "local_ip": "10.0.0.1", "remote_ip": "10.0.0.2",
                "remote_host_id": "agent-02",
            })

        body = resp.json()
        assert body["success"] is True
        assert body["created"] is True


# ---------------------------------------------------------------------------
# TestAttachOverlayInterface
# ---------------------------------------------------------------------------


class TestAttachOverlayInterface:
    """Tests for POST /overlay/attach-link."""

    def test_vxlan_disabled(self, client, monkeypatch):
        """Returns error when VXLAN is disabled."""
        monkeypatch.setattr(settings, "enable_vxlan", False)
        resp = client.post("/overlay/attach-link", json={
            "lab_id": "lab1", "link_id": "lk1",
            "container_name": "r1", "interface_name": "eth1",
            "vni": 5001, "local_ip": "10.0.0.1", "remote_ip": "10.0.0.2",
        })
        body = resp.json()
        assert body["success"] is False

    def test_port_not_found(self, client, monkeypatch):
        """Returns error when OVS port for interface not found."""
        monkeypatch.setattr(settings, "enable_vxlan", True)

        with patch("agent.routers.overlay._resolve_ovs_port",
                    new_callable=AsyncMock, return_value=None):
            resp = client.post("/overlay/attach-link", json={
                "lab_id": "lab1", "link_id": "lk1",
                "container_name": "r1", "interface_name": "eth1",
                "vni": 5001, "local_ip": "10.0.0.1", "remote_ip": "10.0.0.2",
            })

        body = resp.json()
        assert body["success"] is False
        assert "Could not find OVS port" in body["error"]

    def test_success_with_plugin(self, client, monkeypatch):
        """Successful attach with Docker OVS plugin VLAN allocation."""
        monkeypatch.setattr(settings, "enable_vxlan", True)

        port_info = SimpleNamespace(port_name="vh-abc123", vlan_tag=50)
        plugin = MagicMock()
        plugin._ensure_bridge = AsyncMock(return_value="bridge-lab1")
        plugin._allocate_linked_vlan = AsyncMock(return_value=3001)
        plugin._ovs_vsctl = AsyncMock(return_value=(0, "", ""))
        plugin._release_vlan = MagicMock()
        plugin.set_endpoint_vlan_by_host_veth = AsyncMock(return_value=True)

        tunnel = SimpleNamespace(vni=5001)
        backend = MagicMock()
        backend.overlay_create_link_tunnel = AsyncMock(return_value=tunnel)

        with patch("agent.routers.overlay._resolve_ovs_port",
                    new_callable=AsyncMock, return_value=port_info):
            with patch("agent.routers.overlay._get_docker_ovs_plugin", return_value=plugin):
                with patch("agent.routers.overlay.get_network_backend", return_value=backend):
                    resp = client.post("/overlay/attach-link", json={
                        "lab_id": "lab1", "link_id": "lk1",
                        "container_name": "r1", "interface_name": "eth1",
                        "vni": 5001, "local_ip": "10.0.0.1", "remote_ip": "10.0.0.2",
                    })

        body = resp.json()
        assert body["success"] is True
        assert body["local_vlan"] == 3001
        assert body["vni"] == 5001


# ---------------------------------------------------------------------------
# TestDetachOverlayInterface
# ---------------------------------------------------------------------------


class TestDetachOverlayInterface:
    """Tests for POST /overlay/detach-link."""

    def test_vxlan_disabled(self, client, monkeypatch):
        """Returns error when VXLAN is disabled."""
        monkeypatch.setattr(settings, "enable_vxlan", False)
        resp = client.post("/overlay/detach-link", json={
            "lab_id": "lab1", "link_id": "lk1",
            "container_name": "r1", "interface_name": "eth1",
        })
        body = resp.json()
        assert body["success"] is False

    def test_success_isolates_and_deletes(self, client, monkeypatch):
        """Successful detach isolates interface and deletes tunnel."""
        monkeypatch.setattr(settings, "enable_vxlan", True)

        plugin = MagicMock()
        plugin.isolate_port = AsyncMock(return_value=4001)

        provider = MagicMock()
        provider.get_container_name.return_value = "archetype-lab1-r1"

        backend = MagicMock()
        backend.overlay_delete_link_tunnel = AsyncMock(return_value=True)

        with patch("agent.routers.overlay._get_docker_ovs_plugin", return_value=plugin):
            with patch("agent.routers.overlay.get_provider_for_request", return_value=provider):
                with patch("agent.routers.overlay.get_network_backend", return_value=backend):
                    resp = client.post("/overlay/detach-link", json={
                        "lab_id": "lab1", "link_id": "lk1",
                        "container_name": "r1", "interface_name": "eth1",
                    })

        body = resp.json()
        assert body["success"] is True
        assert body["interface_isolated"] is True
        assert body["tunnel_deleted"] is True
        assert body["new_vlan"] == 4001

    def test_tunnel_delete_fails(self, client, monkeypatch):
        """Returns failure when tunnel deletion fails."""
        monkeypatch.setattr(settings, "enable_vxlan", True)

        plugin = MagicMock()
        plugin.isolate_port = AsyncMock(return_value=4001)

        provider = MagicMock()
        provider.get_container_name.return_value = "archetype-lab1-r1"

        backend = MagicMock()
        backend.overlay_delete_link_tunnel = AsyncMock(return_value=False)

        with patch("agent.routers.overlay._get_docker_ovs_plugin", return_value=plugin):
            with patch("agent.routers.overlay.get_provider_for_request", return_value=provider):
                with patch("agent.routers.overlay.get_network_backend", return_value=backend):
                    resp = client.post("/overlay/detach-link", json={
                        "lab_id": "lab1", "link_id": "lk1",
                        "container_name": "r1", "interface_name": "eth1",
                    })

        body = resp.json()
        assert body["success"] is False
        assert body["interface_isolated"] is True


# ---------------------------------------------------------------------------
# TestExternalConnect
# ---------------------------------------------------------------------------


class TestExternalConnect:
    """Tests for POST /labs/{lab_id}/external/connect."""

    def test_ovs_disabled(self, client, monkeypatch):
        """Returns error when OVS is disabled."""
        monkeypatch.setattr(settings, "enable_ovs", False)
        resp = client.post("/labs/lab1/external/connect", json={
            "node_name": "r1", "interface_name": "eth1",
            "external_interface": "ens5",
        })
        body = resp.json()
        assert body["success"] is False

    def test_success_with_node_name(self, client, monkeypatch):
        """Successful connect resolves node name to container."""
        monkeypatch.setattr(settings, "enable_ovs", True)

        provider = MagicMock()
        provider.get_container_name.return_value = "archetype-lab1-r1"

        backend = MagicMock()
        backend.ensure_ovs_initialized = AsyncMock()
        backend.connect_to_external = AsyncMock(return_value=150)

        with patch("agent.routers.overlay.get_provider_for_request", return_value=provider):
            with patch("agent.routers.overlay.get_network_backend", return_value=backend):
                resp = client.post("/labs/lab1/external/connect", json={
                    "node_name": "r1", "interface_name": "eth1",
                    "external_interface": "ens5",
                })

        body = resp.json()
        assert body["success"] is True
        assert body["vlan_tag"] == 150

    def test_missing_node_and_container(self, client, monkeypatch):
        """Returns error when neither container_name nor node_name provided."""
        monkeypatch.setattr(settings, "enable_ovs", True)

        backend = MagicMock()
        backend.ensure_ovs_initialized = AsyncMock()

        with patch("agent.routers.overlay.get_network_backend", return_value=backend):
            resp = client.post("/labs/lab1/external/connect", json={
                "interface_name": "eth1",
                "external_interface": "ens5",
            })

        body = resp.json()
        assert body["success"] is False


# ---------------------------------------------------------------------------
# TestExternalDisconnect
# ---------------------------------------------------------------------------


class TestExternalDisconnect:
    """Tests for POST /labs/{lab_id}/external/disconnect."""

    def test_ovs_disabled(self, client, monkeypatch):
        """Returns error when OVS is disabled."""
        monkeypatch.setattr(settings, "enable_ovs", False)
        resp = client.post("/labs/lab1/external/disconnect", json={
            "external_interface": "ens5",
        })
        body = resp.json()
        assert body["success"] is False

    def test_success(self, client, monkeypatch):
        """Successful disconnect returns success."""
        monkeypatch.setattr(settings, "enable_ovs", True)

        backend = MagicMock()
        backend.ovs_initialized.return_value = True
        backend.detach_external_interface = AsyncMock(return_value=True)

        with patch("agent.routers.overlay.get_network_backend", return_value=backend):
            resp = client.post("/labs/lab1/external/disconnect", json={
                "external_interface": "ens5",
            })

        body = resp.json()
        assert body["success"] is True


# ---------------------------------------------------------------------------
# TestListExternalConnections
# ---------------------------------------------------------------------------


class TestListExternalConnections:
    """Tests for GET /labs/{lab_id}/external."""

    def test_ovs_disabled_returns_empty(self, client, monkeypatch):
        """Returns empty list when OVS is disabled."""
        monkeypatch.setattr(settings, "enable_ovs", False)
        resp = client.get("/labs/lab1/external")
        body = resp.json()
        assert body["connections"] == []

    def test_returns_connections(self, client, monkeypatch):
        """Returns external connections from backend."""
        monkeypatch.setattr(settings, "enable_ovs", True)

        backend = MagicMock()
        backend.ovs_initialized.return_value = True
        backend.list_external_connections = AsyncMock(return_value=[
            {"external_interface": "ens5", "vlan_tag": 100, "connected_ports": ["vh-abc"]},
        ])

        with patch("agent.routers.overlay.get_network_backend", return_value=backend):
            resp = client.get("/labs/lab1/external")

        body = resp.json()
        assert len(body["connections"]) == 1
        assert body["connections"][0]["external_interface"] == "ens5"


# ---------------------------------------------------------------------------
# TestBridgePatch
# ---------------------------------------------------------------------------


class TestBridgePatch:
    """Tests for POST /ovs/patch and DELETE /ovs/patch."""

    def test_create_patch_ovs_disabled(self, client, monkeypatch):
        """Returns error when OVS is disabled."""
        monkeypatch.setattr(settings, "enable_ovs", False)
        resp = client.post("/ovs/patch", json={"target_bridge": "virbr0"})
        body = resp.json()
        assert body["success"] is False

    def test_create_patch_success(self, client, monkeypatch):
        """Successful patch creation returns port name."""
        monkeypatch.setattr(settings, "enable_ovs", True)

        backend = MagicMock()
        backend.ensure_ovs_initialized = AsyncMock()
        backend.create_patch_to_bridge = AsyncMock(return_value="patch-virbr0")

        with patch("agent.routers.overlay.get_network_backend", return_value=backend):
            resp = client.post("/ovs/patch", json={"target_bridge": "virbr0"})

        body = resp.json()
        assert body["success"] is True
        assert body["patch_port"] == "patch-virbr0"

    def test_delete_patch_success(self, client, monkeypatch):
        """Successful patch deletion."""
        monkeypatch.setattr(settings, "enable_ovs", True)

        backend = MagicMock()
        backend.ovs_initialized.return_value = True
        backend.delete_patch_to_bridge = AsyncMock(return_value=True)

        with patch("agent.routers.overlay.get_network_backend", return_value=backend):
            resp = client.request("DELETE", "/ovs/patch", json={"target_bridge": "virbr0"})

        body = resp.json()
        assert body["success"] is True


# ---------------------------------------------------------------------------
# TestReconcilePorts
# ---------------------------------------------------------------------------


class TestReconcilePorts:
    """Tests for POST /overlay/reconcile-ports."""

    def test_empty_valid_names_skips(self, client):
        """Empty valid_port_names without force skips reconciliation."""
        resp = client.post("/overlay/reconcile-ports", json={
            "valid_port_names": [],
        })
        body = resp.json()
        assert body["skipped"] is True

    def test_force_without_confirm_skips(self, client):
        """Force without confirm is rejected."""
        resp = client.post("/overlay/reconcile-ports", json={
            "valid_port_names": ["vxlan-1"],
            "force": True,
            "confirm": False,
        })
        body = resp.json()
        assert body["skipped"] is True
        assert "confirm" in body["reason"]

    def test_force_empty_without_allow_empty_skips(self, client):
        """Force with empty list and no allow_empty is rejected."""
        resp = client.post("/overlay/reconcile-ports", json={
            "valid_port_names": [],
            "force": True,
            "confirm": True,
        })
        body = resp.json()
        assert body["skipped"] is True
        assert "allow_empty" in body["reason"]


# ---------------------------------------------------------------------------
# TestDeclarePortState
# ---------------------------------------------------------------------------


class TestDeclarePortState:
    """Tests for POST /ports/declare-state."""

    def test_ovs_plugin_disabled_returns_empty(self, client, monkeypatch):
        """Returns empty results when OVS plugin is disabled."""
        monkeypatch.setattr(settings, "enable_ovs_plugin", False)
        resp = client.post("/ports/declare-state", json={"pairings": []})
        body = resp.json()
        assert body["results"] == []

    def test_converged_pairing(self, client, monkeypatch):
        """Already-converged pairing returns converged status."""
        monkeypatch.setattr(settings, "enable_ovs_plugin", True)

        plugin = MagicMock()
        plugin._ovs_vsctl = AsyncMock(return_value=(0, "100", ""))
        plugin.set_endpoint_vlan_by_host_veth = AsyncMock(return_value=True)

        with patch("agent.routers.overlay._get_docker_ovs_plugin", return_value=plugin):
            resp = client.post("/ports/declare-state", json={
                "pairings": [{
                    "link_name": "r1:eth1--r2:eth1",
                    "lab_id": "lab1",
                    "port_a": "vh-aaa",
                    "port_b": "vh-bbb",
                    "vlan_tag": 100,
                }],
            })

        body = resp.json()
        assert len(body["results"]) == 1
        assert body["results"][0]["status"] == "converged"


# ---------------------------------------------------------------------------
# TestAttachOverlayExternal
# ---------------------------------------------------------------------------


class TestAttachOverlayExternal:
    """Tests for POST /overlay/attach-external-link."""

    def test_vxlan_disabled(self, client, monkeypatch):
        """Returns error when VXLAN is disabled."""
        monkeypatch.setattr(settings, "enable_vxlan", False)
        resp = client.post("/overlay/attach-external-link", json={
            "lab_id": "lab1", "link_id": "lk1", "external_interface": "eth0.200",
            "vni": 6000, "local_ip": "10.0.0.1", "remote_ip": "10.0.0.2",
            "vlan_tag": 200,
        })
        body = resp.json()
        assert body["success"] is False

    def test_success(self, client, monkeypatch):
        """Successful attach external link."""
        monkeypatch.setattr(settings, "enable_vxlan", True)

        tunnel = SimpleNamespace(vni=6000)
        backend = MagicMock()
        backend.overlay_create_link_tunnel = AsyncMock(return_value=tunnel)

        with patch("agent.routers.overlay.get_network_backend", return_value=backend):
            resp = client.post("/overlay/attach-external-link", json={
                "lab_id": "lab1", "link_id": "lk1", "external_interface": "eth0.200",
                "vni": 6000, "local_ip": "10.0.0.1", "remote_ip": "10.0.0.2",
                "vlan_tag": 200,
            })

        body = resp.json()
        assert body["success"] is True
        assert body["vni"] == 6000
