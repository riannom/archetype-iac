"""CI-friendly tests for VXLAN overlay endpoints."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from agent.main import app
from agent.config import settings


@pytest.fixture
def test_client():
    original_enable_docker = settings.enable_docker
    original_enable_ovs_plugin = settings.enable_ovs_plugin
    original_enable_vxlan = settings.enable_vxlan
    settings.enable_docker = False
    settings.enable_ovs_plugin = False
    settings.enable_vxlan = True
    client = TestClient(app)
    yield client
    client.close()
    settings.enable_docker = original_enable_docker
    settings.enable_ovs_plugin = original_enable_ovs_plugin
    settings.enable_vxlan = original_enable_vxlan


def _backend_with_overlay():
    backend = MagicMock()
    backend.overlay_create_tunnel = AsyncMock(
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
    backend.overlay_create_bridge = AsyncMock()
    backend.overlay_cleanup_lab = AsyncMock(return_value={"tunnels_deleted": 1, "bridges_deleted": 1, "errors": []})
    backend.overlay_status = MagicMock(return_value={"tunnels": [], "bridges": [], "vteps": []})
    backend.overlay_get_vtep = MagicMock(return_value=None)
    backend.overlay_ensure_vtep = AsyncMock(
        return_value=SimpleNamespace(
            interface_name="vtep-10.0.0.2",
            vni=200000,
            local_ip="10.0.0.1",
            remote_ip="10.0.0.2",
            remote_host_id=None,
            tenant_mtu=1450,
        )
    )
    backend.overlay_attach_interface = AsyncMock(return_value=True)
    backend.overlay_detach_interface = AsyncMock(
        return_value={"success": True, "vtep_deleted": False, "remaining_links": 0, "error": None}
    )
    backend.overlay_get_bridges_for_lab = AsyncMock(return_value=[])
    backend.overlay_attach_container = AsyncMock(return_value=True)
    return backend


def test_overlay_create_tunnel(test_client):
    backend = _backend_with_overlay()
    tunnel = SimpleNamespace(
        vni=100,
        interface_name="vxlan100",
        local_ip="10.0.0.1",
        remote_ip="10.0.0.2",
        lab_id="lab1",
        link_id="r1:eth1-r2:eth1",
        vlan_tag=3100,
    )
    call_order: list[str] = []
    backend.overlay_create_tunnel = AsyncMock(
        side_effect=lambda **_: call_order.append("create_tunnel") or tunnel
    )
    backend.overlay_create_bridge = AsyncMock(side_effect=lambda *_: call_order.append("create_bridge"))

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
    backend.overlay_create_tunnel.assert_awaited_once()
    backend.overlay_create_bridge.assert_awaited_once()
    assert call_order == ["create_tunnel", "create_bridge"]


def test_overlay_attach_container_selects_bridge(test_client):
    backend = _backend_with_overlay()
    bridge = SimpleNamespace(link_id="r1:eth1-r2:eth1")
    backend.overlay_get_bridges_for_lab = AsyncMock(return_value=[bridge])
    backend.overlay_attach_container = AsyncMock(return_value=True)

    provider = MagicMock()
    provider.get_container_name.return_value = "archetype-lab1-r1"

    with patch("agent.main.get_network_backend", return_value=backend):
        with patch("agent.main.get_provider", return_value=provider):
            response = test_client.post(
                "/overlay/attach",
                json={
                    "lab_id": "lab1",
                    "link_id": "r1:eth1-r2:eth1",
                    "container_name": "r1",
                    "interface_name": "eth1",
                    "ip_address": None,
                },
            )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    backend.overlay_get_bridges_for_lab.assert_awaited_once_with("lab1")
    backend.overlay_attach_container.assert_awaited_once()


def test_overlay_status(test_client):
    backend = _backend_with_overlay()

    with patch("agent.main.get_network_backend", return_value=backend):
        response = test_client.get("/overlay/status")

    assert response.status_code == 200
    backend.overlay_status.assert_called_once()


def test_overlay_cleanup(test_client):
    backend = _backend_with_overlay()

    with patch("agent.main.get_network_backend", return_value=backend):
        response = test_client.post(
            "/overlay/cleanup",
            json={"lab_id": "lab1"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["tunnels_deleted"] == 1
    backend.overlay_cleanup_lab.assert_awaited_once()


def test_overlay_vtep(test_client):
    backend = _backend_with_overlay()

    with patch("agent.main.get_network_backend", return_value=backend):
        response = test_client.post(
            "/overlay/vtep",
            json={
                "local_ip": "10.0.0.1",
                "remote_ip": "10.0.0.2",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    backend.overlay_ensure_vtep.assert_awaited_once()

def test_overlay_vtep_existing_returns_cached(test_client):
    backend = _backend_with_overlay()
    existing = SimpleNamespace(
        interface_name="vtep-10.0.0.2",
        vni=200000,
        local_ip="10.0.0.1",
        remote_ip="10.0.0.2",
        remote_host_id=None,
        tenant_mtu=1450,
    )
    backend.overlay_get_vtep = MagicMock(return_value=existing)

    with patch("agent.main.get_network_backend", return_value=backend):
        response = test_client.post(
            "/overlay/vtep",
            json={
                "local_ip": "10.0.0.1",
                "remote_ip": "10.0.0.2",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["created"] is False
    backend.overlay_get_vtep.assert_called_once_with("10.0.0.2")
    backend.overlay_ensure_vtep.assert_not_called()


def test_overlay_attach_link(test_client):
    backend = _backend_with_overlay()

    with patch("agent.main.get_network_backend", return_value=backend):
        response = test_client.post(
            "/overlay/attach-link",
            json={
                "lab_id": "lab1",
                "container_name": "archetype-lab1-r1",
                "interface_name": "eth1",
                "vlan_tag": 3100,
                "tenant_mtu": 1450,
                "link_id": "r1:eth1-r2:eth1",
                "remote_ip": "10.0.0.2",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    backend.overlay_attach_interface.assert_awaited_once()


def test_overlay_detach_link(test_client):
    backend = _backend_with_overlay()
    call_order: list[str] = []

    with patch("agent.main.get_network_backend", return_value=backend):
        plugin = MagicMock()
        plugin.isolate_port = AsyncMock(side_effect=lambda *_: call_order.append("isolate_port") or 4242)
        provider = MagicMock()
        provider.get_container_name.return_value = "archetype-lab1-r1"

        with patch("agent.main._get_docker_ovs_plugin", return_value=plugin):
            with patch("agent.main.get_provider_for_request", return_value=provider):
                backend.overlay_detach_interface = AsyncMock(
                    side_effect=lambda **_: call_order.append("overlay_detach") or {
                        "success": True,
                        "vtep_deleted": False,
                        "remaining_links": 0,
                        "error": None,
                    }
                )
                response = test_client.post(
                    "/overlay/detach-link",
                    json={
                        "lab_id": "lab1",
                        "container_name": "archetype-lab1-r1",
                        "interface_name": "eth1",
                        "link_id": "r1:eth1-r2:eth1",
                        "remote_ip": "10.0.0.2",
                        "delete_vtep_if_unused": True,
                    },
                )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["interface_isolated"] is True
    assert body["new_vlan"] == 4242
    plugin.isolate_port.assert_awaited_once_with("lab1", "archetype-lab1-r1", "eth1")
    backend.overlay_detach_interface.assert_awaited_once()
    assert call_order == ["isolate_port", "overlay_detach"]
