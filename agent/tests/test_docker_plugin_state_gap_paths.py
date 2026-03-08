from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from docker.errors import APIError, NotFound

import agent.network.plugin_state as plugin_state_mod
from agent.network import docker_plugin as plugin_mod
from agent.network.docker_plugin import (
    DockerOVSPlugin,
    EndpointState,
    LabBridge,
    NetworkState,
    OVS_BRIDGE_PREFIX,
    VLAN_RANGE_START,
)


@pytest.fixture
def plugin(monkeypatch, tmp_path):
    monkeypatch.setattr(plugin_mod.settings, "workspace_path", str(tmp_path))
    monkeypatch.setattr(plugin_mod.settings, "ovs_bridge_name", "arch-ovs")
    return DockerOVSPlugin()


@pytest.fixture
def sync_to_thread(monkeypatch):
    async def _sync(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _sync)


def _api_error(message: str = "api error", status_code: int = 500) -> APIError:
    response = MagicMock()
    response.status_code = status_code
    return APIError(message, response=response)


@pytest.mark.asyncio
async def test_ensure_lab_network_attachments_mixed_outcomes(plugin, sync_to_thread):
    c1 = MagicMock()
    c1.name = "archetype-lab1-r1"
    c1.attrs = {"NetworkSettings": {"Networks": {"attached-net": {"NetworkID": "id-attached"}}}}

    net_ok = MagicMock()
    net_ok.name = "lab1-eth1"
    net_ok.id = "id-ok"
    net_ok.attrs = {"Labels": {"archetype.lab_id": "lab1"}}

    net_already = MagicMock()
    net_already.name = "lab1-eth2"
    net_already.id = "id-already"
    net_already.attrs = {"Labels": {"archetype.lab_id": "lab1"}}
    net_already.connect.side_effect = _api_error("already exists", 409)

    net_fail = MagicMock()
    net_fail.name = "lab1-eth3"
    net_fail.id = "id-fail"
    net_fail.attrs = {"Labels": {"archetype.lab_id": "lab1"}}
    net_fail.connect.side_effect = RuntimeError("connect failed")

    net_fallback = MagicMock()
    net_fallback.name = "lab1-eth4"
    net_fallback.id = "id-fallback"
    net_fallback.attrs = {"Labels": {"archetype.lab_id": "lab1"}}

    plugin.networks["n1"] = NetworkState("id-ok", "lab1", "eth1", "arch-ovs")
    plugin.networks["n2"] = NetworkState("id-already", "lab1", "eth2", "arch-ovs")
    plugin.networks["n3"] = NetworkState("id-fail", "lab1", "eth3", "arch-ovs")
    plugin.networks["n4"] = NetworkState("id-missing", "lab1", "eth4", "arch-ovs")

    client = MagicMock()
    client.containers.list.return_value = [c1]

    def _get_network(key: str):
        if key == "id-ok":
            return net_ok
        if key == "id-already":
            return net_already
        if key == "id-fail":
            return net_fail
        if key == "id-missing":
            raise NotFound("missing")
        if key == "lab1-eth4":
            return net_fallback
        raise NotFound("unknown")

    client.networks.get.side_effect = _get_network

    with patch("docker.from_env", return_value=client):
        await plugin._ensure_lab_network_attachments()

    net_ok.connect.assert_called_once_with(c1)
    net_already.connect.assert_called_once_with(c1)
    net_fail.connect.assert_called_once_with(c1)
    net_fallback.connect.assert_called_once_with(c1)


@pytest.mark.asyncio
async def test_ensure_lab_network_attachments_handles_thread_failure(plugin):
    with patch.object(asyncio, "to_thread", new=AsyncMock(side_effect=RuntimeError("boom"))):
        await plugin._ensure_lab_network_attachments()


@pytest.mark.asyncio
async def test_reconnect_missing_endpoints_from_docker(plugin, sync_to_thread, monkeypatch):
    plugin.networks["n1"] = NetworkState("id-missing", "lab1", "eth1", "arch-ovs")
    plugin.endpoints["ep-1"] = EndpointState(
        endpoint_id="ep-1",
        network_id="id-missing",
        interface_name="eth1",
        host_veth="vh-ep-1",
        cont_veth="vc-ep-1",
        vlan_tag=200,
        container_name="c1",
    )

    net = MagicMock()
    net.attrs = {
        "Containers": {
            "container-1": {"EndpointID": "ep-1"},
            "container-2": {"EndpointID": ""},
        }
    }
    net.connect.side_effect = [None, RuntimeError("connect fail")]

    client = MagicMock()

    def _get_network(key: str):
        if key == "id-missing":
            raise NotFound("by-id missing")
        if key == "lab1-eth1":
            return net
        raise NotFound("unknown")

    client.networks.get.side_effect = _get_network
    monkeypatch.setattr("os.listdir", lambda _path: [])

    with patch("docker.from_env", return_value=client):
        await plugin._reconnect_missing_endpoints_from_docker()

    assert net.disconnect.call_count == 2
    assert net.connect.call_count == 2


@pytest.mark.asyncio
async def test_reconnect_missing_endpoints_prefers_tracked_host_veth(plugin, sync_to_thread, monkeypatch):
    plugin.networks["id-1"] = NetworkState("id-1", "lab1", "eth1", "arch-ovs")
    plugin.endpoints["ep-1"] = EndpointState(
        endpoint_id="ep-1",
        network_id="id-1",
        interface_name="eth1",
        host_veth="vh-tracked",
        cont_veth="vc-tracked",
        vlan_tag=200,
        container_name="c1",
    )

    net = MagicMock()
    net.attrs = {"Containers": {"container-1": {"EndpointID": "ep-1"}}}

    client = MagicMock()
    client.networks.get.return_value = net
    monkeypatch.setattr("os.path.exists", lambda path: path == "/sys/class/net/vh-tracked")
    monkeypatch.setattr("os.listdir", lambda _path: (_ for _ in ()).throw(AssertionError("prefix scan should not run")))

    with patch("docker.from_env", return_value=client):
        await plugin._reconnect_missing_endpoints_from_docker()

    net.disconnect.assert_not_called()
    net.connect.assert_not_called()


@pytest.mark.asyncio
async def test_reconnect_missing_endpoints_handles_thread_failure(plugin):
    with patch.object(asyncio, "to_thread", new=AsyncMock(side_effect=RuntimeError("boom"))):
        await plugin._reconnect_missing_endpoints_from_docker()


@pytest.mark.asyncio
async def test_reconnect_container_to_network_fallback_by_name(plugin, sync_to_thread):
    plugin.networks["id-missing"] = NetworkState("id-missing", "lab1", "eth1", "arch-ovs")
    network = MagicMock()
    container = MagicMock()

    client = MagicMock()

    def _get_network(key: str):
        if key == "id-missing":
            raise NotFound("missing id")
        if key == "lab1-eth1":
            return network
        raise NotFound("unknown network")

    client.networks.get.side_effect = _get_network
    client.containers.get.return_value = container
    network.disconnect.side_effect = NotFound("already gone")

    with patch("docker.from_env", return_value=client):
        ok = await plugin._reconnect_container_to_network("archetype-lab1-r1", "id-missing", "eth1")

    assert ok is True
    network.connect.assert_called_once_with(container)


@pytest.mark.asyncio
async def test_cleanup_orphaned_ovs_ports_uses_tracked_veths(plugin):
    plugin.lab_bridges["lab1"] = LabBridge(lab_id="lab1", bridge_name="arch-ovs")
    plugin.networks["net-1"] = NetworkState("net-1", "lab1", "eth1", "arch-ovs")
    plugin.endpoints["ep1"] = EndpointState(
        endpoint_id="ep1",
        network_id="net-1",
        interface_name="eth1",
        host_veth="vh-keep",
        cont_veth="vc-keep",
        vlan_tag=100,
        container_name="c1",
    )

    async def _ovs_vsctl(*args):
        if args[0] == "list-ports":
            return 0, "vh-keep\nvh-orphan\nvxlan0\n", ""
        return 1, "", ""

    plugin._ovs_vsctl = _ovs_vsctl
    plugin._delete_port = AsyncMock()

    cleaned = await plugin._cleanup_orphaned_ovs_ports()

    assert cleaned == 1
    plugin._delete_port.assert_awaited_once_with("arch-ovs", "vh-orphan")


@pytest.mark.asyncio
async def test_ensure_shared_bridge_create_and_fail_paths(plugin):
    plugin._run_cmd = AsyncMock(return_value=(0, "", ""))
    plugin._ovs_vsctl = AsyncMock(
        side_effect=[
            (1, "", ""),  # br-exists -> missing
            (0, "", ""),  # add-br
            (0, "", ""),  # set-fail-mode
        ]
    )

    await plugin._ensure_shared_bridge()

    plugin._ovs_vsctl.assert_any_await("add-br", "arch-ovs")
    plugin._ovs_vsctl.assert_any_await("set-fail-mode", "arch-ovs", "standalone")
    assert plugin._run_cmd.await_count == 2

    plugin._ovs_vsctl = AsyncMock(
        side_effect=[
            (1, "", ""),      # missing
            (1, "", "bad"),   # add-br failed
        ]
    )
    with pytest.raises(RuntimeError, match="Failed to create shared OVS bridge"):
        await plugin._ensure_shared_bridge()


@pytest.mark.asyncio
async def test_recover_bridge_state_parses_vlan_vxlan_and_external(plugin):
    bridge_name = f"{OVS_BRIDGE_PREFIX}labprefix"

    async def _ovs_vsctl(*args):
        cmd = args[0:3]
        if args[0] == "list-ports":
            return 0, "vh1\nvxlan9\nens5\n", ""
        if args[0:4] == ("get", "interface", "vxlan9", "options:key"):
            return 0, '"5000"\n', ""
        if cmd == ("get", "port", "vh1"):
            return 0, "200\n", ""
        if args == ("get", "interface", "vh1", "external_ids"):
            return 0, '{archetype.lab_id="lab-full"}\n', ""
        if cmd == ("get", "interface", "vh1"):
            return 0, "system\n", ""
        if cmd == ("get", "port", "vxlan9"):
            return 0, "[]\n", ""
        if cmd == ("get", "interface", "vxlan9"):
            return 0, "vxlan\n", ""
        if cmd == ("get", "port", "ens5"):
            return 0, "abc\n", ""
        if cmd == ("get", "interface", "ens5"):
            return 0, "system\n", ""
        return 1, "", ""

    plugin._ovs_vsctl = _ovs_vsctl
    plugin._recover_endpoints_for_bridge = AsyncMock()

    await plugin._recover_bridge_state(bridge_name, skip_endpoints=False)

    bridge = plugin.lab_bridges["lab-full"]
    assert bridge.vxlan_tunnels == {5000: "vxlan9"}
    assert bridge.external_ports == {"ens5": 0}
    plugin._recover_endpoints_for_bridge.assert_awaited_once()


@pytest.mark.asyncio
async def test_recover_bridge_state_skips_without_metadata(plugin):
    bridge_name = f"{OVS_BRIDGE_PREFIX}labprefix"

    async def _ovs_vsctl(*args):
        if args[0] == "list-ports":
            return 0, "vh1\n", ""
        if args == ("get", "port", "vh1", "tag"):
            return 0, "200\n", ""
        if args == ("get", "interface", "vh1", "type"):
            return 0, "system\n", ""
        if args == ("get", "interface", "vh1", "external_ids"):
            return 0, "{}", ""
        return 1, "", ""

    plugin._ovs_vsctl = _ovs_vsctl
    plugin._recover_endpoints_for_bridge = AsyncMock()

    await plugin._recover_bridge_state(bridge_name, skip_endpoints=False)

    assert plugin.lab_bridges == {}
    plugin._recover_endpoints_for_bridge.assert_not_awaited()


@pytest.mark.asyncio
async def test_recover_bridge_state_increments_skip_metric(plugin):
    bridge_name = f"{OVS_BRIDGE_PREFIX}labprefix"
    metric = MagicMock()
    metric.labels.return_value = metric

    async def _ovs_vsctl(*args):
        if args[0] == "list-ports":
            return 0, "vh1\nvh2\n", ""
        if args[0:3] == ("get", "port", "vh1"):
            return 0, "200\n", ""
        if args[0:3] == ("get", "port", "vh2"):
            return 0, "201\n", ""
        if args == ("get", "interface", "vh1", "type"):
            return 0, "system\n", ""
        if args == ("get", "interface", "vh2", "type"):
            return 0, "system\n", ""
        if args == ("get", "interface", "vh1", "external_ids"):
            return 0, '{archetype.lab_id="lab-a"}\n', ""
        if args == ("get", "interface", "vh2", "external_ids"):
            return 0, '{archetype.lab_id="lab-b"}\n', ""
        return 1, "", ""

    plugin._ovs_vsctl = _ovs_vsctl
    plugin._recover_endpoints_for_bridge = AsyncMock()

    with patch.object(plugin_state_mod, "runtime_identity_skips", metric):
        await plugin._recover_bridge_state(bridge_name, skip_endpoints=False)

    metric.labels.assert_called_once_with(
        resource_type="ovs_bridge",
        operation="recover_bridge_state",
        reason="missing_lab_metadata",
    )
    metric.inc.assert_called_once_with()


@pytest.mark.asyncio
async def test_recover_endpoints_for_bridge_and_vlan_parse_fallback(plugin):
    bridge = LabBridge(lab_id="lab1", bridge_name="arch-ovs")
    plugin.networks["net-eth1"] = NetworkState("net-eth1", "lab1", "eth1", "arch-ovs")
    container = SimpleNamespace(
        name="archetype-lab1-r1",
        id="cid1",
        labels={"archetype.lab_id": "lab1"},
        attrs={
            "State": {"Pid": 1234},
            "NetworkSettings": {
                "Networks": {
                    "lab1-eth1": {
                        "NetworkID": "net-eth1",
                        "EndpointID": "docker-ep-1",
                    },
                    "lab1-eth2": {
                        "NetworkID": "net-eth2-missing",
                        "EndpointID": "docker-ep-2",
                    },
                }
            },
        },
    )

    client = MagicMock()
    client.containers.list.return_value = [container]

    async def _ovs_vsctl(*args):
        if args[0:3] == ("get", "port", "vh1"):
            return 0, "301\n", ""
        if args[0:3] == ("get", "port", "vh2"):
            return 0, "not-a-number\n", ""
        if args == ("get", "interface", "vh1", "external_ids"):
            return 0, '{archetype.endpoint_id="docker-ep-1", archetype.interface_name="eth1", archetype.network_id="net-eth1"}\n', ""
        if args == ("get", "interface", "vh2", "external_ids"):
            return 0, "{}", ""
        return 1, "", ""

    plugin._ovs_vsctl = _ovs_vsctl
    plugin._find_interface_in_container = AsyncMock(return_value="eth2")

    with patch("docker.from_env", return_value=client):
        await plugin._recover_endpoints_for_bridge(bridge, ["vh1", "vh2"])

    assert "docker-ep-1" in plugin.endpoints
    assert plugin.endpoints["docker-ep-1"].network_id == "net-eth1"
    assert plugin.endpoints["docker-ep-1"].vlan_tag == 301
    assert plugin.endpoints["recovered-vh2"].vlan_tag == VLAN_RANGE_START
    plugin._find_interface_in_container.assert_awaited_once_with(1234, "vh2")


@pytest.mark.asyncio
async def test_restart_recovery_rebuilds_endpoint_state_from_metadata(plugin):
    bridge_name = "arch-ovs"
    plugin.networks["net-eth1"] = NetworkState("net-eth1", "lab1", "eth1", bridge_name)
    plugin.lab_bridges.clear()
    plugin.endpoints.clear()
    container = SimpleNamespace(
        name="arbitrary-runtime-name",
        id="cid1",
        labels={"archetype.lab_id": "lab1", "archetype.node_name": "r1"},
        attrs={
            "State": {"Pid": 4321},
            "NetworkSettings": {
                "Networks": {
                    "lab1-eth1": {
                        "NetworkID": "net-eth1",
                        "EndpointID": "docker-ep-1",
                    },
                }
            },
        },
    )
    client = MagicMock()
    client.containers.list.return_value = [container]

    async def _ovs_vsctl(*args):
        if args == ("list-ports", bridge_name):
            return 0, "vh1\n", ""
        if args == ("get", "port", "vh1", "tag"):
            return 0, "301\n", ""
        if args == ("get", "interface", "vh1", "type"):
            return 0, "system\n", ""
        if args == ("get", "interface", "vh1", "external_ids"):
            return 0, (
                '{archetype.lab_id="lab1", archetype.endpoint_id="docker-ep-1", '
                'archetype.interface_name="eth1", archetype.network_id="net-eth1"}\n'
            ), ""
        return 1, "", ""

    plugin._ovs_vsctl = _ovs_vsctl

    with patch("docker.from_env", return_value=client):
        await plugin._recover_bridge_state(bridge_name, skip_endpoints=False)

    endpoint = plugin.endpoints["docker-ep-1"]
    assert plugin.lab_bridges["lab1"].bridge_name == bridge_name
    assert endpoint.network_id == "net-eth1"
    assert endpoint.interface_name == "eth1"
    assert endpoint.host_veth == "vh1"
    assert endpoint.container_name == "arbitrary-runtime-name"
    assert endpoint.node_name == "r1"
    assert endpoint.vlan_tag == 301


@pytest.mark.asyncio
async def test_find_interface_in_container_parses_iflink_lines(plugin):
    plugin._run_cmd = AsyncMock(
        side_effect=[
            (0, "77\n", ""),
            (0, "eth0:1\nbadline\neth9:77\nethX:not-int\n", ""),
        ]
    )

    iface = await plugin._find_interface_in_container(1111, "vh1")

    assert iface == "eth9"
