"""CI-friendly tests for network provisioning endpoints."""

from __future__ import annotations

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


def test_hot_connect_uses_plugin(test_client):
    plugin = MagicMock()
    call_order: list[str] = []
    plugin.hot_connect = AsyncMock(side_effect=lambda *_: call_order.append("hot_connect") or 1234)

    provider = MagicMock()
    provider.get_container_name.side_effect = ["archetype-lab1-r1", "archetype-lab1-r2"]

    with patch("agent.main._get_docker_ovs_plugin", return_value=plugin):
        with patch("agent.main.get_provider_for_request", return_value=provider):
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
    assert body["link"]["vlan_tag"] == 1234
    plugin.hot_connect.assert_awaited_once()
    assert call_order == ["hot_connect"]


def test_hot_disconnect_uses_plugin(test_client):
    plugin = MagicMock()
    call_order: list[str] = []
    plugin.hot_disconnect = AsyncMock(side_effect=lambda *_: call_order.append("hot_disconnect") or True)

    provider = MagicMock()
    provider.get_container_name.side_effect = ["archetype-lab1-r1", "archetype-lab1-r2"]

    with patch("agent.main._get_docker_ovs_plugin", return_value=plugin):
        with patch("agent.main.get_provider_for_request", return_value=provider):
            response = test_client.delete(
                "/labs/lab1/links/r1:eth1-r2:eth1",
            )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert plugin.hot_disconnect.await_count == 2
    assert call_order == ["hot_disconnect", "hot_disconnect"]


def test_hot_connect_cross_lab_blocked(test_client):
    plugin = MagicMock()
    plugin.hot_connect = AsyncMock(return_value=None)

    provider = MagicMock()
    provider.get_container_name.side_effect = ["archetype-lab1-r1", "archetype-lab2-r2"]

    with patch("agent.main._get_docker_ovs_plugin", return_value=plugin):
        with patch("agent.main.get_provider_for_request", return_value=provider):
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


def test_hot_disconnect_error_returns_failure(test_client):
    plugin = MagicMock()
    plugin.hot_disconnect = AsyncMock(side_effect=RuntimeError("boom"))

    provider = MagicMock()
    provider.get_container_name.side_effect = ["archetype-lab1-r1", "archetype-lab1-r2"]

    with patch("agent.main._get_docker_ovs_plugin", return_value=plugin):
        with patch("agent.main.get_provider_for_request", return_value=provider):
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
        with patch("agent.main.get_provider_for_request", return_value=provider):
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
        with patch("agent.main.get_provider_for_request", return_value=provider):
            response = test_client.get(
                "/labs/lab1/interfaces/r1/eth1/vlan?read_from_ovs=true",
            )

    assert response.status_code == 200
    body = response.json()
    assert body["vlan_tag"] == 888
    plugin.get_endpoint_vlan.assert_awaited_once_with(
        "lab1", "archetype-lab1-r1", "eth1", read_from_ovs=True
    )
