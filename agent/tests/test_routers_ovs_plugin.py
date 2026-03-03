"""Tests for the OVS plugin router endpoints (agent/routers/ovs_plugin.py).

Covers:
- OVS status endpoint (/ovs/status)
- OVS flows endpoint (/ovs/flows)
- Docker OVS plugin health, status, lab status, ports, flows
- VXLAN tunnel create/delete
- External interface attach/detach/list
- Boot logs endpoint
- Error handling and OVS-disabled paths
"""
from __future__ import annotations

import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

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


def _make_plugin(**overrides):
    """Build a mock plugin with async methods returning real values.

    Uses plain coroutine functions rather than AsyncMock to avoid
    any interaction quirks between MagicMock attribute access and
    TestClient's event loop handling.
    """
    plugin = types.SimpleNamespace(**overrides)
    return plugin


# ---------------------------------------------------------------------------
# 1. GET /ovs/status
# ---------------------------------------------------------------------------


class TestOVSStatus:
    """Tests for the /ovs/status endpoint."""

    def test_ovs_status_disabled(self, client: TestClient, monkeypatch):
        """When OVS is disabled, returns empty bridge and initialized=False."""
        monkeypatch.setattr(settings, "enable_ovs", False)
        resp = client.get("/ovs/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["bridge_name"] == ""
        assert data["initialized"] is False

    def test_ovs_status_success(self, client: TestClient, monkeypatch):
        """OVS status returns bridge info, ports, and links."""
        monkeypatch.setattr(settings, "enable_ovs", True)

        fake_status = {
            "bridge": "arch-ovs",
            "initialized": True,
            "ports": [
                {
                    "port_name": "vh12345",
                    "container": "archetype-lab1-r1",
                    "interface": "eth1",
                    "vlan_tag": 101,
                    "lab_id": "lab1",
                },
            ],
            "links": [
                {
                    "link_id": "link-1",
                    "lab_id": "lab1",
                    "port_a": "archetype-lab1-r1:eth1",
                    "port_b": "archetype-lab1-r2:eth1",
                    "vlan_tag": 200,
                },
            ],
            "vlan_allocations": 5,
        }

        mock_backend = MagicMock()
        mock_backend.get_ovs_status.return_value = fake_status

        # The endpoint does `from agent.network.backends.registry import get_network_backend`
        # inside the function body. Patch the module in sys.modules.
        registry_stub = types.ModuleType("agent.network.backends.registry")
        registry_stub.get_network_backend = lambda: mock_backend

        with patch.dict("sys.modules", {"agent.network.backends.registry": registry_stub}):
            resp = client.get("/ovs/status")

        assert resp.status_code == 200
        data = resp.json()
        assert data["bridge_name"] == "arch-ovs"
        assert data["initialized"] is True
        assert len(data["ports"]) == 1
        assert data["ports"][0]["port_name"] == "vh12345"
        assert data["ports"][0]["container_name"] == "archetype-lab1-r1"
        assert data["ports"][0]["vlan_tag"] == 101
        assert len(data["links"]) == 1
        assert data["links"][0]["link_id"] == "link-1"
        assert data["links"][0]["source_node"] == "r1"
        assert data["links"][0]["target_node"] == "r2"
        assert data["links"][0]["state"] == "connected"
        assert data["vlan_allocations"] == 5

    def test_ovs_status_backend_exception(self, client: TestClient, monkeypatch):
        """When OVS backend raises, returns empty/uninitialized gracefully."""
        monkeypatch.setattr(settings, "enable_ovs", True)

        def _raise():
            raise RuntimeError("OVS not available")

        registry_stub = types.ModuleType("agent.network.backends.registry")
        registry_stub.get_network_backend = _raise

        with patch.dict("sys.modules", {"agent.network.backends.registry": registry_stub}):
            resp = client.get("/ovs/status")

        assert resp.status_code == 200
        data = resp.json()
        assert data["bridge_name"] == ""
        assert data["initialized"] is False


# ---------------------------------------------------------------------------
# 2. GET /ovs/flows
# ---------------------------------------------------------------------------


class TestOVSFlows:
    """Tests for the /ovs/flows endpoint."""

    def test_ovs_flows_disabled(self, client: TestClient, monkeypatch):
        """When OVS is disabled, returns error message."""
        monkeypatch.setattr(settings, "enable_ovs", False)
        resp = client.get("/ovs/flows")
        assert resp.status_code == 200
        data = resp.json()
        assert data["bridge"] == ""
        assert data["error"] == "OVS not enabled"

    def test_ovs_flows_success(self, client: TestClient, monkeypatch):
        """OVS flows returns flow table dump when successful."""
        monkeypatch.setattr(settings, "enable_ovs", True)
        monkeypatch.setattr(settings, "ovs_bridge_name", "arch-ovs")

        fake_result = MagicMock()
        fake_result.stdout = "NXST_FLOW reply: cookie=0x0, table=0"
        fake_result.stderr = ""
        fake_result.returncode = 0

        with patch("subprocess.run", return_value=fake_result):
            resp = client.get("/ovs/flows")

        assert resp.status_code == 200
        data = resp.json()
        assert data["bridge"] == "arch-ovs"
        assert "NXST_FLOW" in data["flows"]
        assert data["error"] is None

    def test_ovs_flows_subprocess_failure(self, client: TestClient, monkeypatch):
        """When ovs-ofctl fails, returns error in response."""
        monkeypatch.setattr(settings, "enable_ovs", True)
        monkeypatch.setattr(settings, "ovs_bridge_name", "arch-ovs")

        fake_result = MagicMock()
        fake_result.stdout = ""
        fake_result.stderr = "ovs-ofctl: arch-ovs is not a bridge"
        fake_result.returncode = 1

        with patch("subprocess.run", return_value=fake_result):
            resp = client.get("/ovs/flows")

        assert resp.status_code == 200
        data = resp.json()
        assert data["bridge"] == "arch-ovs"
        assert "not a bridge" in data["error"]

    def test_ovs_flows_exception(self, client: TestClient, monkeypatch):
        """When subprocess raises, returns error message."""
        monkeypatch.setattr(settings, "enable_ovs", True)
        monkeypatch.setattr(settings, "ovs_bridge_name", "arch-ovs")

        with patch("subprocess.run", side_effect=FileNotFoundError("ovs-ofctl not found")):
            resp = client.get("/ovs/flows")

        assert resp.status_code == 200
        data = resp.json()
        assert data["flows"] == ""
        assert "ovs-ofctl not found" in data["error"]


# ---------------------------------------------------------------------------
# 3. GET /ovs-plugin/health
# ---------------------------------------------------------------------------


class TestPluginHealth:
    """Tests for the /ovs-plugin/health endpoint."""

    def test_plugin_health_disabled(self, client: TestClient, monkeypatch):
        """When OVS plugin is disabled, returns healthy=False."""
        monkeypatch.setattr(settings, "enable_ovs_plugin", False)
        resp = client.get("/ovs-plugin/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["healthy"] is False

    def test_plugin_health_success(self, client: TestClient, monkeypatch):
        """Returns plugin health data when plugin is available."""
        monkeypatch.setattr(settings, "enable_ovs_plugin", True)

        health_data = {
            "healthy": True,
            "checks": {"bridge": True, "docker": True},
            "uptime_seconds": 3600.0,
            "started_at": "2026-03-01T00:00:00Z",
        }

        async def fake_health_check():
            return health_data

        plugin = _make_plugin(health_check=fake_health_check)

        with patch("agent.routers.ovs_plugin._get_docker_ovs_plugin", return_value=plugin):
            resp = client.get("/ovs-plugin/health")

        assert resp.status_code == 200
        data = resp.json()
        assert data["healthy"] is True
        assert data["checks"]["bridge"] is True
        assert data["uptime_seconds"] == 3600.0
        assert data["started_at"] == "2026-03-01T00:00:00Z"

    def test_plugin_health_exception(self, client: TestClient, monkeypatch):
        """When plugin raises, returns healthy=False gracefully."""
        monkeypatch.setattr(settings, "enable_ovs_plugin", True)

        with patch(
            "agent.routers.ovs_plugin._get_docker_ovs_plugin",
            side_effect=RuntimeError("plugin crashed"),
        ):
            resp = client.get("/ovs-plugin/health")

        assert resp.status_code == 200
        data = resp.json()
        assert data["healthy"] is False


# ---------------------------------------------------------------------------
# 4. GET /ovs-plugin/status
# ---------------------------------------------------------------------------


class TestPluginStatus:
    """Tests for the /ovs-plugin/status endpoint."""

    def test_plugin_status_disabled(self, client: TestClient, monkeypatch):
        """When OVS plugin is disabled, returns healthy=False."""
        monkeypatch.setattr(settings, "enable_ovs_plugin", False)
        resp = client.get("/ovs-plugin/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["healthy"] is False

    def test_plugin_status_success(self, client: TestClient, monkeypatch):
        """Returns comprehensive plugin status with bridges."""
        monkeypatch.setattr(settings, "enable_ovs_plugin", True)

        status_data = {
            "healthy": True,
            "labs_count": 2,
            "endpoints_count": 6,
            "networks_count": 2,
            "management_networks_count": 1,
            "bridges": [
                {
                    "lab_id": "lab1",
                    "bridge_name": "arch-ovs",
                    "port_count": 4,
                    "vlan_range_used": [100, 104],
                    "vxlan_tunnels": 1,
                    "external_interfaces": ["ens192"],
                    "last_activity": "2026-03-01T00:00:00Z",
                },
            ],
            "uptime_seconds": 7200.0,
        }

        async def fake_get_plugin_status():
            return status_data

        plugin = _make_plugin(get_plugin_status=fake_get_plugin_status)

        with patch("agent.routers.ovs_plugin._get_docker_ovs_plugin", return_value=plugin):
            resp = client.get("/ovs-plugin/status")

        assert resp.status_code == 200
        data = resp.json()
        assert data["healthy"] is True
        assert data["labs_count"] == 2
        assert data["endpoints_count"] == 6
        assert len(data["bridges"]) == 1
        assert data["bridges"][0]["lab_id"] == "lab1"
        assert data["bridges"][0]["port_count"] == 4

    def test_plugin_status_exception(self, client: TestClient, monkeypatch):
        """When plugin status raises, returns healthy=False."""
        monkeypatch.setattr(settings, "enable_ovs_plugin", True)

        with patch(
            "agent.routers.ovs_plugin._get_docker_ovs_plugin",
            side_effect=RuntimeError("plugin unavailable"),
        ):
            resp = client.get("/ovs-plugin/status")

        assert resp.status_code == 200
        data = resp.json()
        assert data["healthy"] is False


# ---------------------------------------------------------------------------
# 5. GET /ovs-plugin/labs/{lab_id}/ports
# ---------------------------------------------------------------------------


class TestPluginLabPorts:
    """Tests for the /ovs-plugin/labs/{lab_id}/ports endpoint."""

    def test_lab_ports_disabled(self, client: TestClient, monkeypatch):
        """When plugin is disabled, returns empty ports list."""
        monkeypatch.setattr(settings, "enable_ovs_plugin", False)
        resp = client.get("/ovs-plugin/labs/lab1/ports")
        assert resp.status_code == 200
        data = resp.json()
        assert data["lab_id"] == "lab1"
        assert data["ports"] == []

    def test_lab_ports_success(self, client: TestClient, monkeypatch):
        """Returns port details for a specific lab."""
        monkeypatch.setattr(settings, "enable_ovs_plugin", True)

        async def fake_get_lab_ports(lab_id):
            return [
                {
                    "port_name": "vh12345",
                    "bridge_name": "arch-ovs",
                    "container": "archetype-lab1-r1",
                    "interface": "eth1",
                    "vlan_tag": 101,
                    "rx_bytes": 1024,
                    "tx_bytes": 2048,
                },
            ]

        plugin = _make_plugin(get_lab_ports=fake_get_lab_ports)

        with patch("agent.routers.ovs_plugin._get_docker_ovs_plugin", return_value=plugin):
            resp = client.get("/ovs-plugin/labs/lab1/ports")

        assert resp.status_code == 200
        data = resp.json()
        assert data["lab_id"] == "lab1"
        assert len(data["ports"]) == 1
        assert data["ports"][0]["port_name"] == "vh12345"
        assert data["ports"][0]["rx_bytes"] == 1024
        assert data["ports"][0]["tx_bytes"] == 2048

    def test_lab_ports_exception_returns_empty(self, client: TestClient, monkeypatch):
        """When plugin raises, returns empty ports list gracefully."""
        monkeypatch.setattr(settings, "enable_ovs_plugin", True)

        async def fake_get_lab_ports(lab_id):
            raise RuntimeError("OVS down")

        plugin = _make_plugin(get_lab_ports=fake_get_lab_ports)

        with patch("agent.routers.ovs_plugin._get_docker_ovs_plugin", return_value=plugin):
            resp = client.get("/ovs-plugin/labs/lab1/ports")

        assert resp.status_code == 200
        data = resp.json()
        assert data["ports"] == []


# ---------------------------------------------------------------------------
# 6. GET /ovs-plugin/labs/{lab_id}/flows
# ---------------------------------------------------------------------------


class TestPluginLabFlows:
    """Tests for the /ovs-plugin/labs/{lab_id}/flows endpoint."""

    def test_lab_flows_disabled(self, client: TestClient, monkeypatch):
        """When plugin is disabled, returns error."""
        monkeypatch.setattr(settings, "enable_ovs_plugin", False)
        resp = client.get("/ovs-plugin/labs/lab1/flows")
        assert resp.status_code == 200
        data = resp.json()
        assert data["error"] == "OVS plugin not enabled"

    def test_lab_flows_success(self, client: TestClient, monkeypatch):
        """Returns flow data for a specific lab."""
        monkeypatch.setattr(settings, "enable_ovs_plugin", True)

        async def fake_get_lab_flows(lab_id):
            return {
                "bridge": "arch-ovs",
                "flow_count": 2,
                "flows": [
                    "table=0, priority=100, dl_vlan=101",
                    "table=0, priority=100, dl_vlan=102",
                ],
            }

        plugin = _make_plugin(get_lab_flows=fake_get_lab_flows)

        with patch("agent.routers.ovs_plugin._get_docker_ovs_plugin", return_value=plugin):
            resp = client.get("/ovs-plugin/labs/lab1/flows")

        assert resp.status_code == 200
        data = resp.json()
        assert data["bridge"] == "arch-ovs"
        assert data["flow_count"] == 2
        assert len(data["flows"]) == 2

    def test_lab_flows_with_error(self, client: TestClient, monkeypatch):
        """When plugin returns error in flow data, passes it through."""
        monkeypatch.setattr(settings, "enable_ovs_plugin", True)

        async def fake_get_lab_flows(lab_id):
            return {"error": "Bridge not found for lab"}

        plugin = _make_plugin(get_lab_flows=fake_get_lab_flows)

        with patch("agent.routers.ovs_plugin._get_docker_ovs_plugin", return_value=plugin):
            resp = client.get("/ovs-plugin/labs/lab1/flows")

        assert resp.status_code == 200
        data = resp.json()
        assert data["error"] == "Bridge not found for lab"


# ---------------------------------------------------------------------------
# 7. POST/DELETE /ovs-plugin/labs/{lab_id}/vxlan
# ---------------------------------------------------------------------------


class TestPluginVxlan:
    """Tests for VXLAN tunnel create/delete endpoints."""

    def test_create_vxlan_disabled(self, client: TestClient, monkeypatch):
        """When plugin is disabled, returns success=False."""
        monkeypatch.setattr(settings, "enable_ovs_plugin", False)
        resp = client.post("/ovs-plugin/labs/lab1/vxlan", json={
            "link_id": "link-1",
            "local_ip": "10.0.0.1",
            "remote_ip": "10.0.0.2",
            "vni": 5000,
            "vlan_tag": 200,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "not enabled" in data["error"]

    def test_create_vxlan_success(self, client: TestClient, monkeypatch):
        """Successful VXLAN tunnel creation returns port name."""
        monkeypatch.setattr(settings, "enable_ovs_plugin", True)

        async def fake_create_vxlan_tunnel(**kwargs):
            return "vxlan-5000"

        plugin = _make_plugin(create_vxlan_tunnel=fake_create_vxlan_tunnel)

        with patch("agent.routers.ovs_plugin._get_docker_ovs_plugin", return_value=plugin):
            resp = client.post("/ovs-plugin/labs/lab1/vxlan", json={
                "link_id": "link-1",
                "local_ip": "10.0.0.1",
                "remote_ip": "10.0.0.2",
                "vni": 5000,
                "vlan_tag": 200,
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["port_name"] == "vxlan-5000"

    def test_create_vxlan_exception(self, client: TestClient, monkeypatch):
        """When tunnel creation raises, returns success=False with error."""
        monkeypatch.setattr(settings, "enable_ovs_plugin", True)

        async def fake_create_vxlan_tunnel(**kwargs):
            raise RuntimeError("VXLAN port conflict")

        plugin = _make_plugin(create_vxlan_tunnel=fake_create_vxlan_tunnel)

        with patch("agent.routers.ovs_plugin._get_docker_ovs_plugin", return_value=plugin):
            resp = client.post("/ovs-plugin/labs/lab1/vxlan", json={
                "link_id": "link-1",
                "local_ip": "10.0.0.1",
                "remote_ip": "10.0.0.2",
                "vni": 5000,
                "vlan_tag": 200,
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "VXLAN port conflict" in data["error"]

    def test_delete_vxlan_disabled(self, client: TestClient, monkeypatch):
        """When plugin is disabled, delete returns success=False."""
        monkeypatch.setattr(settings, "enable_ovs_plugin", False)
        resp = client.delete("/ovs-plugin/labs/lab1/vxlan/5000")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False

    def test_delete_vxlan_success(self, client: TestClient, monkeypatch):
        """Successful VXLAN tunnel deletion."""
        monkeypatch.setattr(settings, "enable_ovs_plugin", True)

        async def fake_delete_vxlan_tunnel(lab_id, vni):
            return True

        plugin = _make_plugin(delete_vxlan_tunnel=fake_delete_vxlan_tunnel)

        with patch("agent.routers.ovs_plugin._get_docker_ovs_plugin", return_value=plugin):
            resp = client.delete("/ovs-plugin/labs/lab1/vxlan/5000")

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    def test_delete_vxlan_not_found(self, client: TestClient, monkeypatch):
        """When tunnel not found, returns success=False."""
        monkeypatch.setattr(settings, "enable_ovs_plugin", True)

        async def fake_delete_vxlan_tunnel(lab_id, vni):
            return False

        plugin = _make_plugin(delete_vxlan_tunnel=fake_delete_vxlan_tunnel)

        with patch("agent.routers.ovs_plugin._get_docker_ovs_plugin", return_value=plugin):
            resp = client.delete("/ovs-plugin/labs/lab1/vxlan/9999")

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "not found" in data["error"].lower()


# ---------------------------------------------------------------------------
# 8. External interface endpoints
# ---------------------------------------------------------------------------


class TestPluginExternal:
    """Tests for external interface attach/detach/list endpoints."""

    def test_attach_external_disabled(self, client: TestClient, monkeypatch):
        """When plugin is disabled, attach returns success=False."""
        monkeypatch.setattr(settings, "enable_ovs_plugin", False)
        resp = client.post("/ovs-plugin/labs/lab1/external", json={
            "external_interface": "ens192",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False

    def test_attach_external_success(self, client: TestClient, monkeypatch):
        """Successful external interface attachment returns vlan_tag."""
        monkeypatch.setattr(settings, "enable_ovs_plugin", True)

        async def fake_attach(**kwargs):
            return 300

        plugin = _make_plugin(attach_external_interface=fake_attach)

        with patch("agent.routers.ovs_plugin._get_docker_ovs_plugin", return_value=plugin):
            resp = client.post("/ovs-plugin/labs/lab1/external", json={
                "external_interface": "ens192",
                "vlan_tag": 300,
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["vlan_tag"] == 300

    def test_attach_external_exception(self, client: TestClient, monkeypatch):
        """When attachment raises, returns success=False with error."""
        monkeypatch.setattr(settings, "enable_ovs_plugin", True)

        async def fake_attach(**kwargs):
            raise RuntimeError("Interface not found on host")

        plugin = _make_plugin(attach_external_interface=fake_attach)

        with patch("agent.routers.ovs_plugin._get_docker_ovs_plugin", return_value=plugin):
            resp = client.post("/ovs-plugin/labs/lab1/external", json={
                "external_interface": "ens999",
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "Interface not found" in data["error"]

    def test_detach_external_disabled(self, client: TestClient, monkeypatch):
        """When plugin is disabled, detach returns success=False."""
        monkeypatch.setattr(settings, "enable_ovs_plugin", False)
        resp = client.delete("/ovs-plugin/labs/lab1/external/ens192")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False

    def test_detach_external_success(self, client: TestClient, monkeypatch):
        """Successful external interface detachment."""
        monkeypatch.setattr(settings, "enable_ovs_plugin", True)

        async def fake_detach(lab_id, interface):
            return True

        plugin = _make_plugin(detach_external_interface=fake_detach)

        with patch("agent.routers.ovs_plugin._get_docker_ovs_plugin", return_value=plugin):
            resp = client.delete("/ovs-plugin/labs/lab1/external/ens192")

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    def test_detach_external_not_found(self, client: TestClient, monkeypatch):
        """When interface not found, returns success=False."""
        monkeypatch.setattr(settings, "enable_ovs_plugin", True)

        async def fake_detach(lab_id, interface):
            return False

        plugin = _make_plugin(detach_external_interface=fake_detach)

        with patch("agent.routers.ovs_plugin._get_docker_ovs_plugin", return_value=plugin):
            resp = client.delete("/ovs-plugin/labs/lab1/external/ens999")

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "not found" in data["error"].lower()

    def test_list_external_disabled(self, client: TestClient, monkeypatch):
        """When plugin is disabled, list returns empty interfaces."""
        monkeypatch.setattr(settings, "enable_ovs_plugin", False)
        resp = client.get("/ovs-plugin/labs/lab1/external")
        assert resp.status_code == 200
        data = resp.json()
        assert data["lab_id"] == "lab1"
        assert data["interfaces"] == []

    def test_list_external_success(self, client: TestClient, monkeypatch):
        """Returns list of external interfaces attached to a lab."""
        monkeypatch.setattr(settings, "enable_ovs_plugin", True)

        def fake_list(lab_id):
            return {"ens192": 300, "ens193": 301}

        plugin = _make_plugin(list_external_interfaces=fake_list)

        with patch("agent.routers.ovs_plugin._get_docker_ovs_plugin", return_value=plugin):
            resp = client.get("/ovs-plugin/labs/lab1/external")

        assert resp.status_code == 200
        data = resp.json()
        assert data["lab_id"] == "lab1"
        assert len(data["interfaces"]) == 2
        iface_map = {i["interface"]: i["vlan_tag"] for i in data["interfaces"]}
        assert iface_map["ens192"] == 300
        assert iface_map["ens193"] == 301

    def test_list_external_exception(self, client: TestClient, monkeypatch):
        """When list raises, returns empty interfaces gracefully."""
        monkeypatch.setattr(settings, "enable_ovs_plugin", True)

        def fake_list(lab_id):
            raise RuntimeError("OVS unavailable")

        plugin = _make_plugin(list_external_interfaces=fake_list)

        with patch("agent.routers.ovs_plugin._get_docker_ovs_plugin", return_value=plugin):
            resp = client.get("/ovs-plugin/labs/lab1/external")

        assert resp.status_code == 200
        data = resp.json()
        assert data["interfaces"] == []


# ---------------------------------------------------------------------------
# 9. GET /labs/{lab_id}/boot-logs
# ---------------------------------------------------------------------------


class TestBootLogs:
    """Tests for the /labs/{lab_id}/boot-logs endpoint."""

    def test_boot_logs_no_providers(self, client: TestClient):
        """When no providers available, returns empty boot_logs."""
        with patch("agent.routers.ovs_plugin.get_provider", return_value=None):
            resp = client.get("/labs/lab1/boot-logs")

        assert resp.status_code == 200
        data = resp.json()
        assert data["lab_id"] == "lab1"
        assert data["boot_logs"] == {}

    def test_boot_logs_docker_nodes(self, client: TestClient, tmp_path):
        """Returns boot logs for Docker nodes."""
        mock_docker = MagicMock()

        node1 = MagicMock()
        node1.name = "r1"
        node2 = MagicMock()
        node2.name = "r2"

        status_response = MagicMock()
        status_response.nodes = [node1, node2]
        mock_docker.status = AsyncMock(return_value=status_response)

        def side_effect(provider_name):
            if provider_name == "docker":
                return mock_docker
            return None

        async def fake_boot_logs(name, tail_lines=200):
            return f"Starting {name}...\nReady."

        with patch("agent.routers.ovs_plugin.get_provider", side_effect=side_effect):
            with patch("agent.routers.ovs_plugin.get_workspace", return_value=tmp_path):
                # _get_container_boot_logs is imported inside the function
                # from agent.routers.console, so patch there
                with patch(
                    "agent.routers.console._get_container_boot_logs",
                    side_effect=fake_boot_logs,
                ):
                    resp = client.get("/labs/lab1/boot-logs")

        assert resp.status_code == 200
        data = resp.json()
        assert "r1" in data["boot_logs"]
        assert "r2" in data["boot_logs"]
        assert "Starting r1" in data["boot_logs"]["r1"]

    def test_boot_logs_docker_error_handled(self, client: TestClient, tmp_path):
        """Docker provider errors are caught and don't crash the endpoint."""
        mock_docker = MagicMock()
        mock_docker.status = AsyncMock(side_effect=RuntimeError("Docker unavailable"))

        def side_effect(provider_name):
            if provider_name == "docker":
                return mock_docker
            return None

        with patch("agent.routers.ovs_plugin.get_provider", side_effect=side_effect):
            with patch("agent.routers.ovs_plugin.get_workspace", return_value=tmp_path):
                resp = client.get("/labs/lab1/boot-logs")

        assert resp.status_code == 200
        data = resp.json()
        assert data["lab_id"] == "lab1"
        # Boot logs should be empty since docker provider failed
        assert data["boot_logs"] == {}
