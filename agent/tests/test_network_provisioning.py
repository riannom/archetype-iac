"""CI-friendly tests for network provisioning endpoints."""

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
    original_enable_ovs = settings.enable_ovs
    settings.enable_docker = False
    settings.enable_ovs_plugin = True
    settings.enable_ovs = True
    client = TestClient(app)
    yield client
    client.close()
    settings.enable_docker = original_enable_docker
    settings.enable_ovs_plugin = original_enable_ovs_plugin
    settings.enable_ovs = original_enable_ovs


def _make_port_info(port_name: str, vlan_tag: int, provider: str = "docker"):
    """Create a SimpleNamespace that mimics OVSPortInfo."""
    return SimpleNamespace(port_name=port_name, vlan_tag=vlan_tag, provider=provider)


def test_hot_connect_uses_ovs_resolution(test_client):
    """hot-connect resolves OVS ports for both endpoints and sets shared VLAN."""
    port_a = _make_port_info("vh_a", 100)
    port_b = _make_port_info("vh_b", 200)

    # _resolve_ovs_port is called twice (source + target)
    with patch("agent.main._resolve_ovs_port", new_callable=AsyncMock, side_effect=[port_a, port_b]):
        with patch("agent.main._ovs_set_port_vlan", new_callable=AsyncMock, return_value=True):
            with patch("agent.main._get_docker_ovs_plugin", return_value=MagicMock(endpoints={})):
                response = test_client.post(
                    "/labs/lab1/links",
                    json={
                        "source_node": "r1",
                        "source_interface": "eth1",
                        "target_node": "r2",
                        "target_interface": "eth1",
                    },
                )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    # VLAN should be source port's VLAN
    assert body["link"]["vlan_tag"] == 100


def test_hot_disconnect_isolates_ports(test_client):
    """hot-disconnect assigns unique VLANs to isolate both endpoints."""
    port_a = _make_port_info("vh_a", 100, "docker")
    port_b = _make_port_info("vh_b", 100, "docker")

    plugin = MagicMock()
    # hot_disconnect returns the new VLAN from the plugin
    plugin.hot_disconnect = AsyncMock(return_value=True)
    provider = MagicMock()
    provider.get_container_name.side_effect = ["archetype-lab1-r1", "archetype-lab1-r2"]

    with patch("agent.main._resolve_ovs_port", new_callable=AsyncMock, side_effect=[port_a, port_b]):
        with patch("agent.main._get_docker_ovs_plugin", return_value=plugin):
            with patch("agent.main.get_provider", return_value=provider):
                response = test_client.delete(
                    "/labs/lab1/links/r1:eth1-r2:eth1",
                )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert plugin.hot_disconnect.await_count == 2


def test_hot_connect_source_not_found(test_client):
    """hot-connect returns error if source port cannot be resolved."""
    # Source port not found, target would succeed
    with patch("agent.main._resolve_ovs_port", new_callable=AsyncMock, side_effect=[None, _make_port_info("vh_b", 200)]):
        response = test_client.post(
            "/labs/lab1/links",
            json={
                "source_node": "r1",
                "source_interface": "eth1",
                "target_node": "r2",
                "target_interface": "eth1",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert body["error"]


def test_hot_connect_invalid_payload(test_client):
    response = test_client.post(
        "/labs/lab1/links",
        json={
            "source_node": "r1",
            # missing source_interface
            "target_node": "r2",
            "target_interface": "eth1",
        },
    )

    assert response.status_code == 422


def test_hot_disconnect_error_returns_failure(test_client):
    """hot-disconnect returns failure when both isolation methods fail."""
    port_a = _make_port_info("vh_a", 100, "docker")
    port_b = _make_port_info("vh_b", 100, "docker")

    plugin = MagicMock()
    plugin.hot_disconnect = AsyncMock(side_effect=RuntimeError("boom"))
    provider = MagicMock()
    provider.get_container_name.side_effect = ["archetype-lab1-r1", "archetype-lab1-r2"]

    with patch("agent.main._resolve_ovs_port", new_callable=AsyncMock, side_effect=[port_a, port_b]):
        with patch("agent.main._get_docker_ovs_plugin", return_value=plugin):
            with patch("agent.main.get_provider", return_value=provider):
                # Also patch _ovs_allocate_unique_vlan to fail (fallback path)
                with patch("agent.main._ovs_allocate_unique_vlan", new_callable=AsyncMock, return_value=None):
                    response = test_client.delete(
                        "/labs/lab1/links/r1:eth1-r2:eth1",
                    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False


def test_hot_disconnect_invalid_link_id(test_client):
    response = test_client.delete(
        "/labs/lab1/links/invalid-format",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert body["error"]


def test_isolate_interface_uses_plugin(test_client):
    plugin = MagicMock()
    call_order: list[str] = []
    plugin.isolate_port = AsyncMock(side_effect=lambda *_: call_order.append("isolate") or 2001)

    provider = MagicMock()
    provider.get_container_name.return_value = "archetype-lab1-r1"

    with patch("agent.main._get_docker_ovs_plugin", return_value=plugin):
        with patch("agent.main.get_provider_for_request", return_value=provider):
            response = test_client.post(
                "/labs/lab1/interfaces/r1/eth1/isolate",
            )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["vlan_tag"] == 2001
    plugin.isolate_port.assert_awaited_once_with("lab1", "archetype-lab1-r1", "eth1")
    assert call_order == ["isolate"]


def test_restore_interface_uses_plugin(test_client):
    plugin = MagicMock()
    call_order: list[str] = []
    plugin.restore_port = AsyncMock(side_effect=lambda *_: call_order.append("restore") or True)

    provider = MagicMock()
    provider.get_container_name.return_value = "archetype-lab1-r1"

    with patch("agent.main._get_docker_ovs_plugin", return_value=plugin):
        with patch("agent.main.get_provider_for_request", return_value=provider):
            response = test_client.post(
                "/labs/lab1/interfaces/r1/eth1/restore",
                json={"target_vlan": 2222},
            )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["vlan_tag"] == 2222
    plugin.restore_port.assert_awaited_once_with("lab1", "archetype-lab1-r1", "eth1", 2222)
    assert call_order == ["restore"]


def test_get_interface_vlan_uses_plugin(test_client):
    plugin = MagicMock()
    plugin.get_endpoint_vlan = AsyncMock(return_value=777)

    provider = MagicMock()
    provider.get_container_name.return_value = "archetype-lab1-r1"

    with patch("agent.main._get_docker_ovs_plugin", return_value=plugin):
        with patch("agent.main.get_provider", return_value=provider):
            response = test_client.get(
                "/labs/lab1/interfaces/r1/eth1/vlan",
            )

    assert response.status_code == 200
    body = response.json()
    assert body["vlan_tag"] == 777
    plugin.get_endpoint_vlan.assert_awaited_once_with(
        "lab1", "archetype-lab1-r1", "eth1", read_from_ovs=False
    )


def test_get_interface_vlan_reads_from_ovs(test_client):
    plugin = MagicMock()
    plugin.get_endpoint_vlan = AsyncMock(return_value=888)

    provider = MagicMock()
    provider.get_container_name.return_value = "archetype-lab1-r1"

    with patch("agent.main._get_docker_ovs_plugin", return_value=plugin):
        with patch("agent.main.get_provider", return_value=provider):
            response = test_client.get(
                "/labs/lab1/interfaces/r1/eth1/vlan?read_from_ovs=true",
            )

    assert response.status_code == 200
    body = response.json()
    assert body["vlan_tag"] == 888
    plugin.get_endpoint_vlan.assert_awaited_once_with(
        "lab1", "archetype-lab1-r1", "eth1", read_from_ovs=True
    )


@pytest.mark.asyncio
async def test_resolve_ovs_port_prefers_discovery_over_stale_endpoint(monkeypatch):
    """Resolver should prefer discovered endpoint over stale in-memory endpoint."""
    from agent import main as main_mod

    stale_ep = SimpleNamespace(
        container_name="archetype-lab1-r1",
        interface_name="eth5",
        host_veth="vh-stale",
        vlan_tag=1140,
    )
    discovered_ep = SimpleNamespace(
        container_name="archetype-lab1-r1",
        interface_name="eth5",
        host_veth="vh-current",
        vlan_tag=2999,
    )

    plugin = MagicMock()
    plugin.endpoints = {"stale": stale_ep}
    plugin._discover_endpoint = AsyncMock(return_value=discovered_ep)
    plugin._validate_endpoint_exists = AsyncMock(return_value=True)

    docker_provider = MagicMock()
    docker_provider.get_container_name.return_value = "archetype-lab1-r1"

    def _get_provider(name: str):
        if name == "docker":
            return docker_provider
        return None

    monkeypatch.setattr("agent.main.get_provider", _get_provider)
    monkeypatch.setattr("agent.main._get_docker_ovs_plugin", lambda: plugin)

    result = await main_mod._resolve_ovs_port("lab1", "r1", "eth5")
    assert result is not None
    assert result.provider == "docker"
    assert result.port_name == "vh-current"
    assert result.vlan_tag == 2999
    plugin._discover_endpoint.assert_awaited_once_with("lab1", "archetype-lab1-r1", "eth5")


@pytest.mark.asyncio
async def test_hot_connect_uses_discovered_current_vlan_not_stale():
    """Hot-connect should use current discovered source endpoint VLAN, not stale cached VLAN."""
    stale_source = SimpleNamespace(
        container_name="archetype-lab1-r1",
        interface_name="eth5",
        host_veth="vh-stale-source",
        vlan_tag=1140,
    )
    source_current = SimpleNamespace(
        container_name="archetype-lab1-r1",
        interface_name="eth5",
        host_veth="vh-current-source",
        vlan_tag=2999,
    )
    target_current = SimpleNamespace(
        container_name="archetype-lab1-r2",
        interface_name="eth0",
        host_veth="vh-current-target",
        vlan_tag=1200,
    )

    plugin = MagicMock()
    plugin.endpoints = {"stale": stale_source}

    async def _discover(_lab_id: str, container_name: str, interface_name: str):
        if container_name == "archetype-lab1-r1" and interface_name == "eth5":
            return source_current
        if container_name == "archetype-lab1-r2" and interface_name == "eth0":
            return target_current
        return None

    plugin._discover_endpoint = AsyncMock(side_effect=_discover)
    plugin._validate_endpoint_exists = AsyncMock(return_value=True)

    docker_provider = MagicMock()
    docker_provider.get_container_name.side_effect = lambda _lab, node: f"archetype-lab1-{node}"

    def _get_provider(name: str):
        if name == "docker":
            return docker_provider
        return None

    from agent.main import create_link
    from agent.schemas import LinkCreate

    with patch("agent.main.get_provider", side_effect=_get_provider):
        with patch("agent.main._get_docker_ovs_plugin", return_value=plugin):
            with patch("agent.main._ovs_set_port_vlan", new_callable=AsyncMock, return_value=True) as set_vlan:
                response = await create_link(
                    "lab1",
                    LinkCreate(
                        source_node="r1",
                        source_interface="eth5",
                        target_node="r2",
                        target_interface="eth0",
                    ),
                )

    assert response.success is True
    assert response.link is not None
    assert response.link.vlan_tag == 2999
    set_vlan.assert_awaited_once_with("vh-current-target", 2999)
