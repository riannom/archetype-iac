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


def test_overlay_create_tunnel_error_stops_bridge(test_client):
    backend = _backend_with_overlay()
    backend.overlay_create_tunnel = AsyncMock(side_effect=RuntimeError("boom"))
    backend.overlay_create_bridge = AsyncMock()

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
    assert body["success"] is False
    backend.overlay_create_bridge.assert_not_called()


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


def test_overlay_vtep_trunk_per_remote(test_client):
    backend = _backend_with_overlay()
    vtep_a = SimpleNamespace(
        interface_name="vtep-10.0.0.2",
        vni=200000,
        local_ip="10.0.0.1",
        remote_ip="10.0.0.2",
        remote_host_id=None,
        tenant_mtu=1450,
    )
    vtep_b = SimpleNamespace(
        interface_name="vtep-10.0.0.3",
        vni=200001,
        local_ip="10.0.0.1",
        remote_ip="10.0.0.3",
        remote_host_id=None,
        tenant_mtu=1450,
    )
    backend.overlay_get_vtep = MagicMock(side_effect=[None, vtep_a, None])
    backend.overlay_ensure_vtep = AsyncMock(side_effect=[vtep_a, vtep_b])

    with patch("agent.main.get_network_backend", return_value=backend):
        # First call creates VTEP for 10.0.0.2
        response_a = test_client.post(
            "/overlay/vtep",
            json={
                "local_ip": "10.0.0.1",
                "remote_ip": "10.0.0.2",
            },
        )
        # Second call to same remote should reuse existing VTEP
        response_b = test_client.post(
            "/overlay/vtep",
            json={
                "local_ip": "10.0.0.1",
                "remote_ip": "10.0.0.2",
            },
        )
        # Different remote should create a new VTEP
        response_c = test_client.post(
            "/overlay/vtep",
            json={
                "local_ip": "10.0.0.1",
                "remote_ip": "10.0.0.3",
            },
        )

    assert response_a.status_code == 200
    assert response_b.status_code == 200
    assert response_c.status_code == 200
    assert backend.overlay_ensure_vtep.await_count == 2


def test_overlay_attach_link(test_client):
    backend = _backend_with_overlay()
    call_order: list[str] = []
    backend.overlay_attach_interface = AsyncMock(
        side_effect=lambda **_: call_order.append("attach_interface") or True
    )

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
    backend.overlay_attach_interface.assert_awaited_once_with(
        lab_id="lab1",
        container_name="archetype-lab1-r1",
        interface_name="eth1",
        vlan_tag=3100,
        tenant_mtu=1450,
        link_id="r1:eth1-r2:eth1",
        remote_ip="10.0.0.2",
    )
    backend.overlay_create_tunnel.assert_not_called()
    backend.overlay_create_bridge.assert_not_called()
    assert call_order == ["attach_interface"]


def test_overlay_attach_link_multiple_vlans(test_client):
    backend = _backend_with_overlay()
    call_order: list[str] = []
    backend.overlay_attach_interface = AsyncMock(
        side_effect=lambda **_: call_order.append("attach_interface") or True
    )

    with patch("agent.main.get_network_backend", return_value=backend):
        response_a = test_client.post(
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
        response_b = test_client.post(
            "/overlay/attach-link",
            json={
                "lab_id": "lab1",
                "container_name": "archetype-lab1-r3",
                "interface_name": "eth2",
                "vlan_tag": 3101,
                "tenant_mtu": 1450,
                "link_id": "r3:eth2-r4:eth2",
                "remote_ip": "10.0.0.2",
            },
        )

    assert response_a.status_code == 200
    assert response_b.status_code == 200
    assert backend.overlay_attach_interface.await_count == 2
    backend.overlay_attach_interface.assert_any_await(
        lab_id="lab1",
        container_name="archetype-lab1-r1",
        interface_name="eth1",
        vlan_tag=3100,
        tenant_mtu=1450,
        link_id="r1:eth1-r2:eth1",
        remote_ip="10.0.0.2",
    )
    backend.overlay_attach_interface.assert_any_await(
        lab_id="lab1",
        container_name="archetype-lab1-r3",
        interface_name="eth2",
        vlan_tag=3101,
        tenant_mtu=1450,
        link_id="r3:eth2-r4:eth2",
        remote_ip="10.0.0.2",
    )
    assert call_order == ["attach_interface", "attach_interface"]


def test_overlay_attach_link_rejects_invalid_vlan(test_client):
    backend = _backend_with_overlay()

    with patch("agent.main.get_network_backend", return_value=backend):
        response = test_client.post(
            "/overlay/attach-link",
            json={
                "lab_id": "lab1",
                "container_name": "archetype-lab1-r1",
                "interface_name": "eth1",
                "vlan_tag": None,
                "tenant_mtu": 1450,
                "link_id": "r1:eth1-r2:eth1",
                "remote_ip": "10.0.0.2",
            },
        )

    assert response.status_code in (400, 422)
    backend.overlay_attach_interface.assert_not_called()


def test_overlay_attach_link_missing_remote_ip(test_client):
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
            },
        )

    assert response.status_code == 422
    backend.overlay_attach_interface.assert_not_called()


def test_overlay_attach_link_missing_container(test_client):
    backend = _backend_with_overlay()

    with patch("agent.main.get_network_backend", return_value=backend):
        response = test_client.post(
            "/overlay/attach-link",
            json={
                "lab_id": "lab1",
                "interface_name": "eth1",
                "vlan_tag": 3100,
                "tenant_mtu": 1450,
                "link_id": "r1:eth1-r2:eth1",
                "remote_ip": "10.0.0.2",
            },
        )

    assert response.status_code == 422
    backend.overlay_attach_interface.assert_not_called()


def test_overlay_attach_link_missing_link_id(test_client):
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
                "remote_ip": "10.0.0.2",
            },
        )

    assert response.status_code == 422
    backend.overlay_attach_interface.assert_not_called()


def test_overlay_vtep_missing_remote_ip(test_client):
    backend = _backend_with_overlay()

    with patch("agent.main.get_network_backend", return_value=backend):
        response = test_client.post(
            "/overlay/vtep",
            json={
                "local_ip": "10.0.0.1",
            },
        )

    assert response.status_code == 422
    backend.overlay_ensure_vtep.assert_not_called()


def test_overlay_vtep_invalid_ip_format(test_client):
    backend = _backend_with_overlay()

    with patch("agent.main.get_network_backend", return_value=backend):
        response = test_client.post(
            "/overlay/vtep",
            json={
                "local_ip": "not-an-ip",
                "remote_ip": "10.0.0.2",
            },
        )

    assert response.status_code in (400, 422)
    backend.overlay_ensure_vtep.assert_not_called()


def test_overlay_tunnel_missing_local_ip(test_client):
    backend = _backend_with_overlay()

    with patch("agent.main.get_network_backend", return_value=backend):
        response = test_client.post(
            "/overlay/tunnel",
            json={
                "lab_id": "lab1",
                "link_id": "r1:eth1-r2:eth1",
                "remote_ip": "10.0.0.2",
                "vni": 100,
            },
        )

    assert response.status_code == 422
    backend.overlay_create_tunnel.assert_not_called()


def test_overlay_tunnel_invalid_ip_format(test_client):
    backend = _backend_with_overlay()

    with patch("agent.main.get_network_backend", return_value=backend):
        response = test_client.post(
            "/overlay/tunnel",
            json={
                "lab_id": "lab1",
                "link_id": "r1:eth1-r2:eth1",
                "local_ip": "nope",
                "remote_ip": "10.0.0.2",
                "vni": 100,
            },
        )

    assert response.status_code in (400, 422)
    backend.overlay_create_tunnel.assert_not_called()


def test_overlay_attach_link_backend_error(test_client):
    backend = _backend_with_overlay()
    backend.overlay_attach_interface = AsyncMock(side_effect=RuntimeError("attach failed"))

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
    assert body["success"] is False
    assert body["error"]


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


def test_overlay_detach_link_error_after_isolation(test_client):
    backend = _backend_with_overlay()
    call_order: list[str] = []

    plugin = MagicMock()
    plugin.isolate_port = AsyncMock(side_effect=lambda *_: call_order.append("isolate_port") or 6000)
    provider = MagicMock()
    provider.get_container_name.return_value = "archetype-lab1-r1"

    backend.overlay_detach_interface = AsyncMock(
        side_effect=lambda **_: call_order.append("overlay_detach") or {
            "success": False,
            "vtep_deleted": False,
            "remaining_links": 0,
            "error": "detach failed",
        }
    )

    with patch("agent.main.get_network_backend", return_value=backend):
        with patch("agent.main._get_docker_ovs_plugin", return_value=plugin):
            with patch("agent.main.get_provider_for_request", return_value=provider):
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
    assert body["success"] is False
    assert body["interface_isolated"] is True
    assert body["new_vlan"] == 6000
    assert call_order == ["isolate_port", "overlay_detach"]


def test_overlay_detach_link_preserves_vtep(test_client):
    backend = _backend_with_overlay()
    backend.overlay_detach_interface = AsyncMock(
        return_value={"success": True, "vtep_deleted": False, "remaining_links": 1, "error": None}
    )

    with patch("agent.main.get_network_backend", return_value=backend):
        with patch("agent.main._get_docker_ovs_plugin", return_value=None):
            response = test_client.post(
                "/overlay/detach-link",
                json={
                    "lab_id": "lab1",
                    "container_name": "archetype-lab1-r1",
                    "interface_name": "eth1",
                    "link_id": "r1:eth1-r2:eth1",
                    "remote_ip": "10.0.0.2",
                    "delete_vtep_if_unused": False,
                },
            )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    backend.overlay_detach_interface.assert_awaited_once_with(
        link_id="r1:eth1-r2:eth1",
        remote_ip="10.0.0.2",
        delete_vtep_if_unused=False,
    )


def test_overlay_detach_multiple_links_ordering(test_client):
    backend = _backend_with_overlay()
    call_order: list[str] = []

    plugin = MagicMock()
    plugin.isolate_port = AsyncMock(side_effect=lambda *_: call_order.append("isolate_port") or 5000)
    provider = MagicMock()
    provider.get_container_name.side_effect = ["archetype-lab1-r1", "archetype-lab1-r3"]

    backend.overlay_detach_interface = AsyncMock(
        side_effect=lambda **_: call_order.append("overlay_detach") or {
            "success": True,
            "vtep_deleted": False,
            "remaining_links": 1,
            "error": None,
        }
    )

    with patch("agent.main.get_network_backend", return_value=backend):
        with patch("agent.main._get_docker_ovs_plugin", return_value=plugin):
            with patch("agent.main.get_provider_for_request", return_value=provider):
                response_a = test_client.post(
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
                response_b = test_client.post(
                    "/overlay/detach-link",
                    json={
                        "lab_id": "lab1",
                        "container_name": "archetype-lab1-r3",
                        "interface_name": "eth2",
                        "link_id": "r3:eth2-r4:eth2",
                        "remote_ip": "10.0.0.2",
                        "delete_vtep_if_unused": True,
                    },
                )

    assert response_a.status_code == 200
    assert response_b.status_code == 200
    assert call_order == ["isolate_port", "overlay_detach", "isolate_port", "overlay_detach"]
