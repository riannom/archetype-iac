"""CI-friendly tests for OVS plugin VXLAN/external provisioning endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from agent.main import app
from agent.config import settings
from agent.network.docker_plugin import DockerOVSPlugin, LabBridge


@pytest.fixture
def test_client():
    original_enable_ovs_plugin = settings.enable_ovs_plugin
    settings.enable_ovs_plugin = True
    client = TestClient(app)
    yield client
    client.close()
    settings.enable_ovs_plugin = original_enable_ovs_plugin


def test_plugin_vxlan_create_delete(test_client):
    plugin = MagicMock()
    plugin.create_vxlan_tunnel = AsyncMock(return_value="vxlan123")
    plugin.delete_vxlan_tunnel = AsyncMock(return_value=True)

    with patch("agent.main._get_docker_ovs_plugin", return_value=plugin):
        response = test_client.post(
            "/ovs-plugin/labs/lab1/vxlan",
            json={
                "vni": 123,
                "remote_ip": "10.0.0.2",
                "local_ip": "10.0.0.1",
                "link_id": "r1:eth1-r2:eth1",
                "vlan_tag": 3100,
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["port_name"] == "vxlan123"
        plugin.create_vxlan_tunnel.assert_awaited_once()

        response = test_client.delete("/ovs-plugin/labs/lab1/vxlan/123")

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        plugin.delete_vxlan_tunnel.assert_awaited_once_with("lab1", 123)


def test_plugin_external_attach_detach_list(test_client):
    plugin = MagicMock()
    plugin.attach_external_interface = AsyncMock(return_value=2001)
    plugin.detach_external_interface = AsyncMock(return_value=True)
    plugin.list_external_interfaces = MagicMock(return_value={"eth0": 2001})

    with patch("agent.main._get_docker_ovs_plugin", return_value=plugin):
        response = test_client.post(
            "/ovs-plugin/labs/lab1/external",
            json={
                "external_interface": "eth0",
                "vlan_tag": 2001,
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["vlan_tag"] == 2001
        plugin.attach_external_interface.assert_awaited_once()

        response = test_client.get("/ovs-plugin/labs/lab1/external")
        assert response.status_code == 200
        body = response.json()
        interfaces = {(item["interface"], item["vlan_tag"]) for item in body["interfaces"]}
        assert ("eth0", 2001) in interfaces

        response = test_client.delete("/ovs-plugin/labs/lab1/external/eth0")
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        plugin.detach_external_interface.assert_awaited_once_with("lab1", "eth0")


def test_plugin_vlan_allocation_unique_across_labs():
    plugin = DockerOVSPlugin()
    lab_a = LabBridge(lab_id="lab-a", bridge_name="arch-ovs")
    lab_b = LabBridge(lab_id="lab-b", bridge_name="arch-ovs")

    vlan_a = plugin._allocate_vlan(lab_a)
    vlan_b = plugin._allocate_vlan(lab_b)

    assert vlan_a != vlan_b
