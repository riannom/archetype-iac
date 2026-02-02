"""Tests for DockerOVSPlugin endpoint discovery logic."""
from __future__ import annotations

import pytest

from agent.network.docker_plugin import DockerOVSPlugin, EndpointState, NetworkState


class _FakeContainer:
    def __init__(self, networks: dict):
        self.attrs = {"NetworkSettings": {"Networks": networks}}


class _FakeDocker:
    def __init__(self, networks: dict):
        self._container = _FakeContainer(networks)

    class _Containers:
        def __init__(self, container):
            self._container = container

        def get(self, _name):
            return self._container

    @property
    def containers(self):
        return self._Containers(self._container)


@pytest.mark.asyncio
async def test_discover_endpoint_matches_by_endpoint_id(monkeypatch, tmp_path):
    monkeypatch.setattr("agent.network.docker_plugin.settings.workspace_path", str(tmp_path))
    plugin = DockerOVSPlugin()

    endpoint_id = "ep-123"
    network_id = "net-1"
    plugin.endpoints[endpoint_id] = EndpointState(
        endpoint_id=endpoint_id,
        network_id=network_id,
        interface_name="eth1",
        host_veth="vhabc",
        cont_veth="vcabc",
        vlan_tag=100,
        container_name=None,
    )
    plugin.networks[network_id] = NetworkState(
        network_id=network_id,
        lab_id="lab",
        interface_name="eth1",
        bridge_name="arch-ovs",
    )

    networks = {
        "lab-eth1": {
            "EndpointID": endpoint_id,
            "NetworkID": network_id,
        }
    }

    monkeypatch.setattr(
        "agent.network.docker_plugin.docker.from_env",
        lambda: _FakeDocker(networks),
    )

    ep = await plugin._discover_endpoint("lab", "container-1", "eth1")
    assert ep is plugin.endpoints[endpoint_id]
    assert ep.container_name == "container-1"


@pytest.mark.asyncio
async def test_discover_endpoint_matches_by_network_id(monkeypatch, tmp_path):
    monkeypatch.setattr("agent.network.docker_plugin.settings.workspace_path", str(tmp_path))
    plugin = DockerOVSPlugin()

    endpoint_id = "ep-456"
    network_id = "net-2"
    plugin.endpoints[endpoint_id] = EndpointState(
        endpoint_id=endpoint_id,
        network_id=network_id,
        interface_name="eth2",
        host_veth="vhdef",
        cont_veth="vcdef",
        vlan_tag=200,
        container_name=None,
    )
    plugin.networks[network_id] = NetworkState(
        network_id=network_id,
        lab_id="lab",
        interface_name="eth2",
        bridge_name="arch-ovs",
    )

    networks = {
        "something-not-eth2": {
            "EndpointID": "missing",
            "NetworkID": network_id,
        }
    }

    monkeypatch.setattr(
        "agent.network.docker_plugin.docker.from_env",
        lambda: _FakeDocker(networks),
    )

    ep = await plugin._discover_endpoint("lab", "container-2", "eth2")
    assert ep is plugin.endpoints[endpoint_id]
    assert ep.container_name == "container-2"


@pytest.mark.asyncio
async def test_discover_endpoint_reconstructs_from_ports(monkeypatch, tmp_path):
    monkeypatch.setattr("agent.network.docker_plugin.settings.workspace_path", str(tmp_path))
    plugin = DockerOVSPlugin()

    endpoint_id = "ep-789"
    network_id = "net-3"
    plugin.networks[network_id] = NetworkState(
        network_id=network_id,
        lab_id="lab",
        interface_name="eth3",
        bridge_name="arch-ovs",
    )

    networks = {
        "random-name": {
            "EndpointID": endpoint_id,
            "NetworkID": network_id,
        }
    }

    monkeypatch.setattr(
        "agent.network.docker_plugin.docker.from_env",
        lambda: _FakeDocker(networks),
    )

    async def _ovs_vsctl(*args):
        if args == ("list-ports", "arch-ovs"):
            return 0, f"vh{endpoint_id[:5]}xyz\n", ""
        if args == ("get", "port", f"vh{endpoint_id[:5]}xyz", "tag"):
            return 0, "[123]", ""
        return 1, "", ""

    async def _noop():
        return None

    monkeypatch.setattr(plugin, "_ovs_vsctl", _ovs_vsctl)
    monkeypatch.setattr(plugin, "_mark_dirty_and_save", _noop)

    ep = await plugin._discover_endpoint("lab", "container-3", "eth3")
    assert ep is not None
    assert ep.endpoint_id == endpoint_id
    assert ep.host_veth.startswith("vh")
    assert ep.vlan_tag == 123
    assert ep.container_name == "container-3"


@pytest.mark.asyncio
async def test_discover_endpoint_returns_none_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr("agent.network.docker_plugin.settings.workspace_path", str(tmp_path))
    plugin = DockerOVSPlugin()

    networks = {
        "lab-eth3": {
            "EndpointID": "missing",
            "NetworkID": "missing-net",
        }
    }

    monkeypatch.setattr(
        "agent.network.docker_plugin.docker.from_env",
        lambda: _FakeDocker(networks),
    )

    ep = await plugin._discover_endpoint("lab", "container-3", "eth3")
    assert ep is None
