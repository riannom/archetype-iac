"""CI-friendly tests for OVS plugin VXLAN/external provisioning endpoints."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
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


def test_plugin_vlan_release_does_not_collide_with_active():
    plugin = DockerOVSPlugin()
    lab_a = LabBridge(lab_id="lab-a", bridge_name="arch-ovs")
    lab_b = LabBridge(lab_id="lab-b", bridge_name="arch-ovs")

    vlan_a = plugin._allocate_vlan(lab_a)
    vlan_b = plugin._allocate_vlan(lab_b)

    # Release VLAN A and ensure next allocation doesn't reuse VLAN B.
    plugin._release_vlan(vlan_a)
    vlan_c = plugin._allocate_vlan(lab_a)

    assert vlan_c != vlan_b


def test_plugin_vlan_allocation_no_duplicates_across_many():
    plugin = DockerOVSPlugin()
    labs = [LabBridge(lab_id=f"lab-{i}", bridge_name="arch-ovs") for i in range(5)]

    allocated = set()
    for idx, lab in enumerate(labs):
        for _ in range(10):
            vlan = plugin._allocate_vlan(lab)
            assert vlan not in allocated
            allocated.add(vlan)


def test_plugin_vlan_allocation_interleaved_no_collisions():
    plugin = DockerOVSPlugin()
    lab_a = LabBridge(lab_id="lab-a", bridge_name="arch-ovs")
    lab_b = LabBridge(lab_id="lab-b", bridge_name="arch-ovs")
    lab_c = LabBridge(lab_id="lab-c", bridge_name="arch-ovs")

    sequence = [lab_a, lab_b, lab_c, lab_a, lab_c, lab_b, lab_b, lab_a]
    allocated = []
    for lab in sequence:
        allocated.append(plugin._allocate_vlan(lab))

    assert len(allocated) == len(set(allocated))


def test_hot_connect_rejects_cross_lab_endpoints():
    plugin = DockerOVSPlugin()
    plugin.lab_bridges["lab-a"] = LabBridge(lab_id="lab-a", bridge_name="arch-ovs")

    plugin.networks["net-a"] = SimpleNamespace(lab_id="lab-a", interface_name="eth1", bridge_name="arch-ovs", network_id="net-a")
    plugin.networks["net-b"] = SimpleNamespace(lab_id="lab-b", interface_name="eth1", bridge_name="arch-ovs", network_id="net-b")

    ep_a = SimpleNamespace(endpoint_id="ep-a", network_id="net-a", interface_name="eth1", host_veth="vh1", cont_veth="vc1", vlan_tag=100, container_name="a")
    ep_b = SimpleNamespace(endpoint_id="ep-b", network_id="net-b", interface_name="eth1", host_veth="vh2", cont_veth="vc2", vlan_tag=200, container_name="b")
    plugin.endpoints["ep-a"] = ep_a
    plugin.endpoints["ep-b"] = ep_b

    plugin._ovs_vsctl = AsyncMock(return_value=(0, "", ""))  # should not be called
    plugin._mark_dirty_and_save = AsyncMock()

    result = asyncio.run(
        plugin.hot_connect("lab-a", "a", "eth1", "b", "eth1")
    )

    assert result is None
    plugin._ovs_vsctl.assert_not_called()


def test_hot_connect_rejects_missing_networks():
    plugin = DockerOVSPlugin()
    plugin.lab_bridges["lab-a"] = LabBridge(lab_id="lab-a", bridge_name="arch-ovs")

    ep_a = SimpleNamespace(endpoint_id="ep-a", network_id="net-a", interface_name="eth1", host_veth="vh1", cont_veth="vc1", vlan_tag=100, container_name="a")
    ep_b = SimpleNamespace(endpoint_id="ep-b", network_id="net-b", interface_name="eth1", host_veth="vh2", cont_veth="vc2", vlan_tag=200, container_name="b")
    plugin.endpoints["ep-a"] = ep_a
    plugin.endpoints["ep-b"] = ep_b

    plugin._ovs_vsctl = AsyncMock(return_value=(0, "", ""))  # should not be called

    result = asyncio.run(
        plugin.hot_connect("lab-a", "a", "eth1", "b", "eth1")
    )

    assert result is None
    plugin._ovs_vsctl.assert_not_called()
