"""Tests for DockerOVSPlugin endpoint discovery logic."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from agent.network.docker_plugin import DockerOVSPlugin, EndpointState, NetworkState


class _FakeContainer:
    def __init__(self, networks: dict):
        self.attrs = {
            "NetworkSettings": {"Networks": networks},
            "State": {"Pid": 1234},
        }


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


def _patch_docker_from_env(monkeypatch, networks: dict):
    """Patch the docker module so local `import docker; docker.from_env()` returns our fake."""
    import docker as real_docker

    monkeypatch.setattr(real_docker, "from_env", lambda **kwargs: _FakeDocker(networks))


def _stub_binding_match(monkeypatch, plugin: DockerOVSPlugin, expected_interface: str):
    """Stub interface peer resolution so binding checks pass in unit tests."""
    monkeypatch.setattr(
        plugin,
        "_find_interface_in_container",
        AsyncMock(return_value=expected_interface),
    )


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

    _patch_docker_from_env(monkeypatch, networks)
    _stub_binding_match(monkeypatch, plugin, "eth1")
    # _validate_endpoint_exists checks OVS port; stub it to return True
    monkeypatch.setattr(plugin, "_validate_endpoint_exists", AsyncMock(return_value=True))

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

    _patch_docker_from_env(monkeypatch, networks)
    _stub_binding_match(monkeypatch, plugin, "eth2")
    monkeypatch.setattr(plugin, "_validate_endpoint_exists", AsyncMock(return_value=True))

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

    _patch_docker_from_env(monkeypatch, networks)
    _stub_binding_match(monkeypatch, plugin, "eth3")

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

    _patch_docker_from_env(monkeypatch, networks)
    _stub_binding_match(monkeypatch, plugin, "eth3")

    # Also stub _ovs_vsctl for the reconstruction fallback (list-ports returns empty)
    async def _ovs_vsctl(*args):
        if args[0] == "list-ports":
            return 0, "", ""
        return 1, "", ""

    monkeypatch.setattr(plugin, "_ovs_vsctl", _ovs_vsctl)

    ep = await plugin._discover_endpoint("lab", "container-3", "eth3")
    assert ep is None


@pytest.mark.asyncio
async def test_discover_endpoint_prunes_stale_on_missing_container(monkeypatch, tmp_path):
    monkeypatch.setattr("agent.network.docker_plugin.settings.workspace_path", str(tmp_path))
    plugin = DockerOVSPlugin()

    endpoint_id = "ep-stale"
    network_id = "net-stale"
    plugin.networks[network_id] = NetworkState(
        network_id=network_id,
        lab_id="lab",
        interface_name="eth1",
        bridge_name="arch-ovs",
    )
    plugin.endpoints[endpoint_id] = EndpointState(
        endpoint_id=endpoint_id,
        network_id=network_id,
        interface_name="eth1",
        host_veth="vhstale",
        cont_veth="vcstale",
        vlan_tag=100,
        container_name="container-stale",
    )

    import docker as real_docker

    class _MissingDocker:
        class _Containers:
            def get(self, _name):
                raise real_docker.errors.NotFound("container missing")

        @property
        def containers(self):
            return self._Containers()

    monkeypatch.setattr(real_docker, "from_env", lambda **kwargs: _MissingDocker())
    monkeypatch.setattr(plugin, "_mark_dirty_and_save", AsyncMock(return_value=None))

    ep = await plugin._discover_endpoint("lab", "container-stale", "eth1")
    assert ep is None
    assert endpoint_id not in plugin.endpoints


@pytest.mark.asyncio
async def test_reconcile_queues_missing_veth(monkeypatch, tmp_path):
    monkeypatch.setattr("agent.network.docker_plugin.settings.workspace_path", str(tmp_path))
    plugin = DockerOVSPlugin()

    endpoint_id = "ep-missing"
    network_id = "net-1"
    plugin.endpoints[endpoint_id] = EndpointState(
        endpoint_id=endpoint_id,
        network_id=network_id,
        interface_name="eth1",
        host_veth="vhmissing",
        cont_veth="vcmissing",
        vlan_tag=100,
        container_name="container-1",
    )
    plugin.networks[network_id] = NetworkState(
        network_id=network_id,
        lab_id="lab",
        interface_name="eth1",
        bridge_name="arch-ovs",
    )

    async def _run_cmd(_args):
        return 1, "", ""

    async def _save_state():
        return None

    monkeypatch.setattr(plugin, "_run_cmd", _run_cmd)
    monkeypatch.setattr(plugin, "_save_state", _save_state)

    stats = await plugin._reconcile_state()
    assert endpoint_id not in plugin.endpoints
    assert stats["endpoints_queued"] == 1
    assert plugin._pending_endpoint_reconnects


@pytest.mark.asyncio
async def test_discover_endpoint_rejects_binding_mismatch(monkeypatch, tmp_path):
    monkeypatch.setattr("agent.network.docker_plugin.settings.workspace_path", str(tmp_path))
    plugin = DockerOVSPlugin()

    endpoint_id = "ep-mismatch"
    network_id = "net-mm"
    plugin.endpoints[endpoint_id] = EndpointState(
        endpoint_id=endpoint_id,
        network_id=network_id,
        interface_name="eth5",
        host_veth="vhwrong",
        cont_veth="vcwrong",
        vlan_tag=100,
        container_name=None,
    )
    plugin.networks[network_id] = NetworkState(
        network_id=network_id,
        lab_id="lab",
        interface_name="eth5",
        bridge_name="arch-ovs",
    )

    networks = {
        "lab-eth5": {
            "EndpointID": endpoint_id,
            "NetworkID": network_id,
        }
    }
    _patch_docker_from_env(monkeypatch, networks)
    monkeypatch.setattr(plugin, "_validate_endpoint_exists", AsyncMock(return_value=True))
    # Mismatch: host veth resolves to a different container interface.
    monkeypatch.setattr(
        plugin,
        "_find_interface_in_container",
        AsyncMock(return_value="eth9"),
    )

    ep = await plugin._discover_endpoint("lab", "container-5", "eth5")
    assert ep is None


@pytest.mark.asyncio
async def test_audit_endpoint_bindings_detects_and_repairs_drift(monkeypatch, tmp_path):
    monkeypatch.setattr("agent.network.docker_plugin.settings.workspace_path", str(tmp_path))
    plugin = DockerOVSPlugin()

    endpoint_id = "ep-audit"
    network_id = "net-audit"
    ep = EndpointState(
        endpoint_id=endpoint_id,
        network_id=network_id,
        interface_name="eth1",
        host_veth="vhold",
        cont_veth="vcold",
        vlan_tag=100,
        container_name="container-a",
    )
    plugin.endpoints[endpoint_id] = ep
    plugin.networks[network_id] = NetworkState(
        network_id=network_id,
        lab_id="lab",
        interface_name="eth1",
        bridge_name="arch-ovs",
    )

    # Simulate discovery refreshing the endpoint host_veth in place.
    async def _discover(_lab, _container, _iface):
        ep.host_veth = "vhnew"
        return ep

    monkeypatch.setattr(plugin, "_discover_endpoint", _discover)

    stats = await plugin._audit_endpoint_bindings()
    assert stats["checked"] == 1
    assert stats["drifted"] == 1
    assert stats["repaired"] == 1
    assert stats["failed"] == 0


@pytest.mark.asyncio
async def test_endpoint_binding_audit_loop_runs_periodically(monkeypatch, tmp_path):
    monkeypatch.setattr("agent.network.docker_plugin.settings.workspace_path", str(tmp_path))
    monkeypatch.setattr("agent.network.docker_plugin.settings.endpoint_binding_audit_interval_seconds", 1)
    plugin = DockerOVSPlugin()

    audit_calls = 0

    async def _fake_audit():
        nonlocal audit_calls
        audit_calls += 1
        return {"checked": 1, "drifted": 0, "repaired": 0, "failed": 0}

    sleep_calls = 0

    async def _fake_sleep(_seconds):
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls > 1:
            raise asyncio.CancelledError()
        return None

    monkeypatch.setattr(plugin, "_audit_endpoint_bindings", _fake_audit)
    monkeypatch.setattr("agent.network.docker_plugin.asyncio.sleep", _fake_sleep)

    await plugin._endpoint_binding_audit_loop()
    assert audit_calls == 1
