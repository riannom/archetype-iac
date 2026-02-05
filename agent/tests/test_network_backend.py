"""Tests for network backend integration in agent endpoints."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from agent.main import app
from agent.config import settings


@pytest.fixture
def test_client():
    return TestClient(app)


class StubBackend:
    name = "ovs"

    def __init__(self):
        self.overlay_manager = MagicMock()
        self.ovs_manager = MagicMock()
        self.plugin_running = False
        self._ovs_initialized = True
        self.initialize = AsyncMock(return_value={})
        self.shutdown = AsyncMock()

        self.ensure_ovs_initialized = AsyncMock()
        self.get_ovs_status = MagicMock(
            return_value={
                "bridge": "arch-ovs",
                "initialized": True,
                "ports": [],
                "links": [],
                "vlan_allocations": 0,
            }
        )
        self.get_links_for_lab = MagicMock(return_value=[])
        self.handle_container_restart = AsyncMock(
            return_value={"reprovisioned_ports": 0, "reconnected_links": 0, "errors": []}
        )

        self.connect_to_external = AsyncMock(return_value=123)
        self.create_patch_to_bridge = AsyncMock(return_value="patch0")
        self.delete_patch_to_bridge = AsyncMock(return_value=True)
        self.detach_external_interface = AsyncMock(return_value=True)
        self.list_external_connections = AsyncMock(return_value=[])

        self.overlay_create_tunnel = AsyncMock(
            return_value=SimpleNamespace(
                vni=100,
                interface_name="vxlan100",
                local_ip="10.0.0.1",
                remote_ip="10.0.0.2",
                lab_id="lab1",
                link_id="r1:eth1-r2:eth1",
                vlan_tag=3100,
            )
        )
        self.overlay_create_bridge = AsyncMock()
        self.overlay_get_bridges_for_lab = AsyncMock(return_value=[])
        self.overlay_attach_container = AsyncMock(return_value=True)
        self.overlay_cleanup_lab = AsyncMock(
            return_value={"tunnels_deleted": 0, "bridges_deleted": 0, "errors": []}
        )
        self.overlay_status = MagicMock(return_value={"tunnels": [], "bridges": [], "vteps": []})
        self.overlay_get_vtep = MagicMock(return_value=None)
        self.overlay_ensure_vtep = AsyncMock(
            return_value=SimpleNamespace(
                interface_name="vtep-10.0.0.2",
                vni=200000,
                local_ip="10.0.0.1",
                remote_ip="10.0.0.2",
                remote_host_id=None,
                tenant_mtu=1450,
            )
        )
        self.overlay_attach_interface = AsyncMock(return_value=True)
        self.overlay_detach_interface = AsyncMock(
            return_value={
                "success": True,
                "vtep_deleted": False,
                "remaining_links": 0,
                "error": None,
            }
        )
        self.check_port_exists = MagicMock(return_value=True)

    def ovs_initialized(self) -> bool:
        return self._ovs_initialized


def test_ovs_status_uses_backend(test_client):
    backend = StubBackend()

    with patch("agent.main.get_network_backend", return_value=backend):
        response = test_client.get("/ovs/status")

    assert response.status_code == 200
    body = response.json()
    assert body["bridge_name"] == "arch-ovs"
    backend.get_ovs_status.assert_called_once()


def test_external_connect_uses_backend(test_client):
    backend = StubBackend()

    with patch("agent.main.get_network_backend", return_value=backend):
        provider = MagicMock()
        provider.get_container_name.return_value = "archetype-lab1-r1"
        with patch("agent.main.get_provider_for_request", return_value=provider):
            response = test_client.post(
                "/labs/lab1/external/connect",
                json={
                    "node_name": "r1",
                    "interface_name": "eth1",
                    "external_interface": "eth0",
                    "vlan_tag": 123,
                },
            )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["vlan_tag"] == 123
    backend.ensure_ovs_initialized.assert_awaited()
    backend.connect_to_external.assert_awaited()


def test_overlay_create_tunnel_uses_backend(test_client):
    backend = StubBackend()

    with patch("agent.main.get_network_backend", return_value=backend):
        response = test_client.post(
            "/overlay/tunnel",
            json={
                "lab_id": "lab1",
                "link_id": "r1:eth1-r2:eth1",
                "local_ip": "10.0.0.1",
                "remote_ip": "10.0.0.2",
                "vni": 100,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["tunnel"]["vni"] == 100
    backend.overlay_create_tunnel.assert_awaited()
    backend.overlay_create_bridge.assert_awaited()


def test_backend_registry_fallback():
    from agent.network.backends import registry

    registry._backend_instance = None
    original_backend = settings.network_backend

    try:
        settings.network_backend = "unknown"
        backend = registry.get_network_backend()
        assert backend.name == "ovs"
    finally:
        settings.network_backend = original_backend
        registry._backend_instance = None
