"""Comprehensive tests for DockerOVSPlugin (agent/network/docker_plugin.py).

Covers:
- Plugin initialization and data structures
- Bridge ensure/delete lifecycle
- Veth pair generation
- VLAN allocation and release (isolated + linked ranges)
- Network create/delete via handler protocol
- Endpoint create/delete via handler protocol
- Join handler (interface naming)
- hot_connect / hot_disconnect (VLAN tag matching for L2 links)
- VXLAN tunnel create/delete
- External interface attach/detach
- Health check
- TTL cleanup
- State serialization / deserialization round-trip
- Error handling paths
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.config import settings
from agent.network.docker_plugin import (
    DockerOVSPlugin,
    EndpointState,
    LabBridge,
    LINKED_VLAN_END,
    LINKED_VLAN_START,
    NetworkState,
    VLAN_RANGE_END,
    VLAN_RANGE_START,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_plugin(tmp_path) -> DockerOVSPlugin:
    """Create a DockerOVSPlugin with workspace pointing at tmp_path."""
    with patch.object(settings, "workspace_path", str(tmp_path)):
        return DockerOVSPlugin()


def _stub_ovs(plugin: DockerOVSPlugin, monkeypatch) -> dict[str, list]:
    """Stub all subprocess-executing methods on the plugin.

    Returns a dict where calls are recorded by method name.
    """
    calls: dict[str, list] = {"_ovs_vsctl": [], "_run_cmd": []}

    async def _ovs_vsctl(*args):
        calls["_ovs_vsctl"].append(args)
        # Default: bridge exists, commands succeed
        if args and args[0] == "br-exists":
            return 0, "", ""
        if args and args[0] == "list-ports":
            return 0, "", ""
        if args and args[0] == "--version":
            return 0, "ovs-vsctl 3.1.0", ""
        return 0, "", ""

    async def _run_cmd(cmd):
        calls["_run_cmd"].append(cmd)
        return 0, "", ""

    monkeypatch.setattr(plugin, "_ovs_vsctl", _ovs_vsctl)
    monkeypatch.setattr(plugin, "_run_cmd", _run_cmd)
    return calls


def _stub_save(plugin: DockerOVSPlugin, monkeypatch):
    """Stub _mark_dirty_and_save to no-op."""
    async def _noop():
        pass
    monkeypatch.setattr(plugin, "_mark_dirty_and_save", _noop)


def _make_aiohttp_request(data: dict) -> MagicMock:
    """Create a mock aiohttp.web.Request with json() returning data."""
    request = MagicMock()
    request.json = AsyncMock(return_value=data)
    return request


def _parse_response_body(resp) -> dict:
    """Parse aiohttp response body to dict.

    Works with both real aiohttp (body is bytes) and the test stub
    (body is already a dict).
    """
    body = resp.body
    if isinstance(body, (bytes, bytearray)):
        return json.loads(body)
    return body


def _seed_lab(plugin: DockerOVSPlugin, lab_id: str = "lab1") -> LabBridge:
    """Pre-seed a lab bridge in the plugin state."""
    bridge = LabBridge(lab_id=lab_id, bridge_name=settings.ovs_bridge_name)
    plugin.lab_bridges[lab_id] = bridge
    return bridge


def _seed_network(
    plugin: DockerOVSPlugin,
    network_id: str,
    lab_id: str = "lab1",
    interface_name: str = "eth1",
) -> NetworkState:
    """Pre-seed a network in the plugin state."""
    net = NetworkState(
        network_id=network_id,
        lab_id=lab_id,
        interface_name=interface_name,
        bridge_name=settings.ovs_bridge_name,
    )
    plugin.networks[network_id] = net
    bridge = plugin.lab_bridges.get(lab_id)
    if bridge:
        bridge.network_ids.add(network_id)
    return net


def _seed_endpoint(
    plugin: DockerOVSPlugin,
    endpoint_id: str,
    network_id: str,
    interface_name: str = "eth1",
    vlan_tag: int = 100,
    container_name: str | None = None,
    host_veth: str = "vhtest1",
    cont_veth: str = "vctest1",
) -> EndpointState:
    """Pre-seed an endpoint in the plugin state."""
    ep = EndpointState(
        endpoint_id=endpoint_id,
        network_id=network_id,
        interface_name=interface_name,
        host_veth=host_veth,
        cont_veth=cont_veth,
        vlan_tag=vlan_tag,
        container_name=container_name,
    )
    plugin.endpoints[endpoint_id] = ep
    plugin._allocated_vlans.add(vlan_tag)
    return ep


# ===========================================================================
# Initialization & Data Structures
# ===========================================================================


class TestPluginInit:
    """Plugin constructor and data structure tests."""

    def test_init_creates_empty_state(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "workspace_path", str(tmp_path))
        plugin = DockerOVSPlugin()

        assert plugin.lab_bridges == {}
        assert plugin.networks == {}
        assert plugin.endpoints == {}
        assert plugin._allocated_vlans == set()
        assert plugin._global_next_vlan == VLAN_RANGE_START
        assert plugin._global_next_linked_vlan == LINKED_VLAN_START

    def test_init_creates_workspace_directory(self, tmp_path, monkeypatch):
        ws = tmp_path / "deep" / "nested"
        monkeypatch.setattr(settings, "workspace_path", str(ws))
        DockerOVSPlugin()
        assert ws.exists()

    def test_state_file_path_inside_workspace(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "workspace_path", str(tmp_path))
        plugin = DockerOVSPlugin()
        assert plugin._state_file.parent == tmp_path

    def test_lab_bridge_dataclass_defaults(self):
        bridge = LabBridge(lab_id="lab1", bridge_name="arch-ovs")
        assert bridge.next_vlan == VLAN_RANGE_START
        assert bridge.network_ids == set()
        assert bridge.vxlan_tunnels == {}
        assert bridge.external_ports == {}

    def test_endpoint_state_defaults(self):
        ep = EndpointState(
            endpoint_id="ep1",
            network_id="net1",
            interface_name="eth1",
            host_veth="vh1",
            cont_veth="vc1",
            vlan_tag=100,
        )
        assert ep.container_name is None


# ===========================================================================
# Bridge Ensure / Delete
# ===========================================================================


class TestBridgeLifecycle:
    """Tests for _ensure_bridge and _maybe_delete_bridge."""

    @pytest.mark.asyncio
    async def test_ensure_bridge_creates_new(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        created_bridge = False

        async def _ovs_vsctl(*args):
            nonlocal created_bridge
            if args[0] == "br-exists":
                return 1, "", ""  # bridge does not exist
            if args[0] == "add-br":
                created_bridge = True
                return 0, "", ""
            return 0, "", ""

        monkeypatch.setattr(plugin, "_ovs_vsctl", _ovs_vsctl)
        monkeypatch.setattr(plugin, "_run_cmd", AsyncMock(return_value=(0, "", "")))

        bridge = await plugin._ensure_bridge("lab1")
        assert bridge.lab_id == "lab1"
        assert bridge.bridge_name == settings.ovs_bridge_name
        assert "lab1" in plugin.lab_bridges
        assert created_bridge

    @pytest.mark.asyncio
    async def test_ensure_bridge_returns_cached(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        calls = _stub_ovs(plugin, monkeypatch)

        bridge1 = await plugin._ensure_bridge("lab1")
        bridge2 = await plugin._ensure_bridge("lab1")
        assert bridge1 is bridge2
        # br-exists should only be called once (first call)
        br_exists_calls = [c for c in calls["_ovs_vsctl"] if c[0] == "br-exists"]
        assert len(br_exists_calls) == 1

    @pytest.mark.asyncio
    async def test_maybe_delete_bridge_removes_empty(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        _seed_lab(plugin, "lab1")
        # Empty network_ids => should be deleted
        await plugin._maybe_delete_bridge("lab1")
        assert "lab1" not in plugin.lab_bridges

    @pytest.mark.asyncio
    async def test_maybe_delete_bridge_keeps_with_networks(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        bridge = _seed_lab(plugin, "lab1")
        bridge.network_ids.add("net-abc")
        await plugin._maybe_delete_bridge("lab1")
        assert "lab1" in plugin.lab_bridges

    @pytest.mark.asyncio
    async def test_maybe_delete_bridge_noop_for_unknown(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        # Should not raise for unknown lab
        await plugin._maybe_delete_bridge("nonexistent")


# ===========================================================================
# Veth Pair Generation
# ===========================================================================


class TestVethGeneration:
    """Tests for _generate_veth_names."""

    def test_veth_names_fit_15_chars(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        host, cont = plugin._generate_veth_names("abcdef1234567890")
        assert len(host) <= 15
        assert len(cont) <= 15

    def test_veth_names_start_with_prefix(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        host, cont = plugin._generate_veth_names("ep-test")
        assert host.startswith("vh")
        assert cont.startswith("vc")

    def test_veth_names_unique(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        h1, c1 = plugin._generate_veth_names("ep1")
        h2, c2 = plugin._generate_veth_names("ep1")
        # Random suffix makes them unique even for same endpoint
        assert h1 != h2 or c1 != c2


# ===========================================================================
# VLAN Allocation
# ===========================================================================


class TestVlanAllocation:
    """Tests for _allocate_vlan and _release_vlan."""

    @pytest.mark.asyncio
    async def test_allocate_vlan_returns_in_isolated_range(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        _stub_ovs(plugin, monkeypatch)
        bridge = _seed_lab(plugin)

        vlan = await plugin._allocate_vlan(bridge)
        assert VLAN_RANGE_START <= vlan <= VLAN_RANGE_END
        assert vlan in plugin._allocated_vlans

    @pytest.mark.asyncio
    async def test_allocate_vlan_skips_used(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        _stub_ovs(plugin, monkeypatch)
        bridge = _seed_lab(plugin)

        # Pre-allocate the first few
        plugin._allocated_vlans = {VLAN_RANGE_START, VLAN_RANGE_START + 1}
        plugin._global_next_vlan = VLAN_RANGE_START

        vlan = await plugin._allocate_vlan(bridge)
        assert vlan == VLAN_RANGE_START + 2

    @pytest.mark.asyncio
    async def test_allocate_vlan_wraps_around(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        _stub_ovs(plugin, monkeypatch)
        bridge = _seed_lab(plugin)

        # Set next_vlan to end of range
        plugin._global_next_vlan = VLAN_RANGE_END
        vlan1 = await plugin._allocate_vlan(bridge)
        assert vlan1 == VLAN_RANGE_END

        # Next allocation should wrap to start
        vlan2 = await plugin._allocate_vlan(bridge)
        assert vlan2 == VLAN_RANGE_START

    def test_release_vlan_removes_from_both_sets(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        plugin._allocated_vlans.add(500)
        plugin._allocated_linked_vlans.add(500)

        plugin._release_vlan(500)
        assert 500 not in plugin._allocated_vlans
        assert 500 not in plugin._allocated_linked_vlans

    @pytest.mark.asyncio
    async def test_allocate_linked_vlan_returns_in_linked_range(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        _stub_ovs(plugin, monkeypatch)
        bridge = _seed_lab(plugin)

        vlan = await plugin._allocate_linked_vlan(bridge)
        assert LINKED_VLAN_START <= vlan <= LINKED_VLAN_END
        assert vlan in plugin._allocated_linked_vlans

    def test_release_linked_vlan(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        plugin._allocated_linked_vlans.add(2500)
        plugin._allocated_vlans.add(2500)

        plugin._release_linked_vlan(2500)
        assert 2500 not in plugin._allocated_linked_vlans
        assert 2500 not in plugin._allocated_vlans


# ===========================================================================
# Network Create / Delete Handlers
# ===========================================================================


class TestNetworkHandlers:
    """Tests for handle_create_network and handle_delete_network."""

    @pytest.mark.asyncio
    async def test_create_network_success(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        _stub_ovs(plugin, monkeypatch)
        _stub_save(plugin, monkeypatch)

        request = _make_aiohttp_request({
            "NetworkID": "net-12345",
            "Options": {
                "com.docker.network.generic": {
                    "lab_id": "lab1",
                    "interface_name": "eth1",
                },
            },
        })

        await plugin.handle_create_network(request)
        assert "net-12345" in plugin.networks
        net = plugin.networks["net-12345"]
        assert net.lab_id == "lab1"
        assert net.interface_name == "eth1"
        assert "lab1" in plugin.lab_bridges

    @pytest.mark.asyncio
    async def test_create_network_missing_lab_id(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)

        request = _make_aiohttp_request({
            "NetworkID": "net-abc",
            "Options": {
                "com.docker.network.generic": {},
            },
        })

        await plugin.handle_create_network(request)
        # Should return error response, network not added
        assert "net-abc" not in plugin.networks

    @pytest.mark.asyncio
    async def test_create_network_default_interface(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        _stub_ovs(plugin, monkeypatch)
        _stub_save(plugin, monkeypatch)

        request = _make_aiohttp_request({
            "NetworkID": "net-xyz",
            "Options": {
                "com.docker.network.generic": {
                    "lab_id": "lab1",
                    # No interface_name specified
                },
            },
        })

        await plugin.handle_create_network(request)
        assert plugin.networks["net-xyz"].interface_name == "eth1"  # default

    @pytest.mark.asyncio
    async def test_delete_network_removes_state(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        _stub_ovs(plugin, monkeypatch)
        _stub_save(plugin, monkeypatch)
        bridge = _seed_lab(plugin)
        _seed_network(plugin, "net-del", lab_id="lab1")

        request = _make_aiohttp_request({"NetworkID": "net-del"})
        await plugin.handle_delete_network(request)

        assert "net-del" not in plugin.networks
        assert "net-del" not in bridge.network_ids

    @pytest.mark.asyncio
    async def test_delete_network_triggers_bridge_cleanup(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        _stub_ovs(plugin, monkeypatch)
        _stub_save(plugin, monkeypatch)
        _seed_lab(plugin)
        _seed_network(plugin, "net-only", lab_id="lab1")

        request = _make_aiohttp_request({"NetworkID": "net-only"})
        await plugin.handle_delete_network(request)

        # Lab bridge should be removed since no networks remain
        assert "lab1" not in plugin.lab_bridges

    @pytest.mark.asyncio
    async def test_delete_network_noop_for_unknown(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)

        request = _make_aiohttp_request({"NetworkID": "nonexistent"})
        await plugin.handle_delete_network(request)
        # Should not raise


# ===========================================================================
# Endpoint Create / Delete Handlers
# ===========================================================================


class TestEndpointHandlers:
    """Tests for handle_create_endpoint and handle_delete_endpoint."""

    @pytest.mark.asyncio
    async def test_create_endpoint_success(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        _stub_save(plugin, monkeypatch)

        async def _ovs_vsctl(*args):
            if args[0] == "br-exists":
                return 0, "", ""
            if args[0] == "list-ports":
                return 0, "", ""
            return 0, "", ""

        monkeypatch.setattr(plugin, "_ovs_vsctl", _ovs_vsctl)
        monkeypatch.setattr(plugin, "_run_cmd", AsyncMock(return_value=(0, "", "")))

        _seed_lab(plugin)
        _seed_network(plugin, "net-1", lab_id="lab1", interface_name="eth1")

        request = _make_aiohttp_request({
            "NetworkID": "net-1",
            "EndpointID": "ep-new-1",
        })

        await plugin.handle_create_endpoint(request)
        assert "ep-new-1" in plugin.endpoints
        ep = plugin.endpoints["ep-new-1"]
        assert ep.interface_name == "eth1"
        assert ep.host_veth.startswith("vh")
        assert VLAN_RANGE_START <= ep.vlan_tag <= VLAN_RANGE_END

    @pytest.mark.asyncio
    async def test_create_endpoint_missing_network(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)

        request = _make_aiohttp_request({
            "NetworkID": "nonexistent",
            "EndpointID": "ep-bad",
        })

        await plugin.handle_create_endpoint(request)
        assert "ep-bad" not in plugin.endpoints

    @pytest.mark.asyncio
    async def test_create_endpoint_veth_failure_rolls_back(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        _stub_save(plugin, monkeypatch)

        async def _ovs_vsctl(*args):
            if args[0] == "list-ports":
                return 0, "", ""
            return 0, "", ""

        monkeypatch.setattr(plugin, "_ovs_vsctl", _ovs_vsctl)

        # Veth creation fails
        async def _run_cmd(cmd):
            if "veth" in str(cmd):
                return 1, "", "veth create failed"
            return 0, "", ""

        monkeypatch.setattr(plugin, "_run_cmd", _run_cmd)

        _seed_lab(plugin)
        _seed_network(plugin, "net-1", lab_id="lab1")

        request = _make_aiohttp_request({
            "NetworkID": "net-1",
            "EndpointID": "ep-fail",
        })

        await plugin.handle_create_endpoint(request)
        assert "ep-fail" not in plugin.endpoints

    @pytest.mark.asyncio
    async def test_delete_endpoint_cleans_up(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        _stub_ovs(plugin, monkeypatch)
        _stub_save(plugin, monkeypatch)

        _seed_lab(plugin)
        _seed_network(plugin, "net-1", lab_id="lab1")
        _seed_endpoint(plugin, "ep-del", "net-1", vlan_tag=150)

        request = _make_aiohttp_request({
            "NetworkID": "net-1",
            "EndpointID": "ep-del",
        })

        await plugin.handle_delete_endpoint(request)
        assert "ep-del" not in plugin.endpoints
        # VLAN should be released
        assert 150 not in plugin._allocated_vlans

    @pytest.mark.asyncio
    async def test_delete_endpoint_noop_for_unknown(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)

        request = _make_aiohttp_request({
            "NetworkID": "net-1",
            "EndpointID": "nonexistent",
        })

        await plugin.handle_delete_endpoint(request)
        # Should not raise


# ===========================================================================
# Join Handler
# ===========================================================================


class TestJoinHandler:
    """Tests for handle_join."""

    @pytest.mark.asyncio
    async def test_join_returns_interface_config(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        _seed_lab(plugin)
        _seed_network(plugin, "net-1")
        _seed_endpoint(
            plugin, "ep-join", "net-1",
            interface_name="eth2", cont_veth="vcjoin",
        )

        request = _make_aiohttp_request({
            "EndpointID": "ep-join",
            "SandboxKey": "/var/run/docker/netns/abc",
        })

        resp = await plugin.handle_join(request)
        body = _parse_response_body(resp)
        assert body["InterfaceName"]["SrcName"] == "vcjoin"
        assert body["InterfaceName"]["DstName"] == "eth2"

    @pytest.mark.asyncio
    async def test_join_unknown_endpoint_returns_error(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)

        request = _make_aiohttp_request({
            "EndpointID": "nonexistent",
            "SandboxKey": "/var/run/docker/netns/abc",
        })

        resp = await plugin.handle_join(request)
        body = _parse_response_body(resp)
        assert "Err" in body


# ===========================================================================
# Hot Connect / Hot Disconnect
# ===========================================================================


class TestHotConnect:
    """Tests for hot_connect and hot_disconnect."""

    @pytest.mark.asyncio
    async def test_hot_connect_sets_shared_vlan(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        _stub_save(plugin, monkeypatch)

        async def _ovs_vsctl(*args):
            if args[0] == "list-ports":
                return 0, "", ""
            return 0, "", ""

        monkeypatch.setattr(plugin, "_ovs_vsctl", _ovs_vsctl)
        monkeypatch.setattr(plugin, "_run_cmd", AsyncMock(return_value=(0, "", "")))
        monkeypatch.setattr(plugin, "_validate_endpoint_exists", AsyncMock(return_value=True))

        _seed_lab(plugin)
        _seed_network(plugin, "net-a", interface_name="eth1")
        _seed_network(plugin, "net-b", interface_name="eth1")
        _seed_endpoint(
            plugin, "ep-a", "net-a",
            interface_name="eth1", vlan_tag=100,
            container_name="container-a", host_veth="vha",
        )
        _seed_endpoint(
            plugin, "ep-b", "net-b",
            interface_name="eth1", vlan_tag=101,
            container_name="container-b", host_veth="vhb",
        )

        shared_vlan = await plugin.hot_connect(
            "lab1", "container-a", "eth1", "container-b", "eth1"
        )

        assert shared_vlan is not None
        assert LINKED_VLAN_START <= shared_vlan <= LINKED_VLAN_END
        assert plugin.endpoints["ep-a"].vlan_tag == shared_vlan
        assert plugin.endpoints["ep-b"].vlan_tag == shared_vlan

    @pytest.mark.asyncio
    async def test_hot_connect_no_lab_bridge_returns_none(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)

        result = await plugin.hot_connect(
            "nonexistent", "a", "eth1", "b", "eth1"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_hot_connect_missing_endpoint_returns_none(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        _stub_ovs(plugin, monkeypatch)
        _seed_lab(plugin)

        # Stub _discover_endpoint to return None (cannot find endpoints)
        monkeypatch.setattr(plugin, "_discover_endpoint", AsyncMock(return_value=None))

        result = await plugin.hot_connect(
            "lab1", "missing-a", "eth1", "missing-b", "eth1"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_hot_connect_validates_endpoints_exist_on_ovs(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        _stub_save(plugin, monkeypatch)

        async def _ovs_vsctl(*args):
            return 0, "", ""

        monkeypatch.setattr(plugin, "_ovs_vsctl", _ovs_vsctl)

        # ep_a exists on OVS, ep_b does not
        validate_calls = []

        async def _validate(ep):
            validate_calls.append(ep.endpoint_id)
            return ep.endpoint_id == "ep-a"

        monkeypatch.setattr(plugin, "_validate_endpoint_exists", _validate)

        _seed_lab(plugin)
        _seed_network(plugin, "net-a")
        _seed_network(plugin, "net-b")
        _seed_endpoint(plugin, "ep-a", "net-a", container_name="a", host_veth="vha")
        _seed_endpoint(plugin, "ep-b", "net-b", container_name="b", host_veth="vhb")

        result = await plugin.hot_connect("lab1", "a", "eth1", "b", "eth1")
        assert result is None

    @pytest.mark.asyncio
    async def test_hot_connect_releases_old_vlans(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        _stub_save(plugin, monkeypatch)

        async def _ovs_vsctl(*args):
            if args[0] == "list-ports":
                return 0, "", ""
            return 0, "", ""

        monkeypatch.setattr(plugin, "_ovs_vsctl", _ovs_vsctl)
        monkeypatch.setattr(plugin, "_validate_endpoint_exists", AsyncMock(return_value=True))

        _seed_lab(plugin)
        _seed_network(plugin, "net-a")
        _seed_network(plugin, "net-b")
        _seed_endpoint(plugin, "ep-a", "net-a", vlan_tag=100, container_name="a", host_veth="vha")
        _seed_endpoint(plugin, "ep-b", "net-b", vlan_tag=101, container_name="b", host_veth="vhb")

        assert 100 in plugin._allocated_vlans
        assert 101 in plugin._allocated_vlans

        await plugin.hot_connect("lab1", "a", "eth1", "b", "eth1")

        # Old isolated VLANs should be released
        assert 100 not in plugin._allocated_vlans
        assert 101 not in plugin._allocated_vlans

    @pytest.mark.asyncio
    async def test_hot_disconnect_assigns_new_vlan(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        _stub_save(plugin, monkeypatch)

        async def _ovs_vsctl(*args):
            if args[0] == "list-ports":
                return 0, "", ""
            return 0, "", ""

        monkeypatch.setattr(plugin, "_ovs_vsctl", _ovs_vsctl)

        _seed_lab(plugin)
        _seed_network(plugin, "net-a")
        _seed_endpoint(
            plugin, "ep-a", "net-a",
            vlan_tag=2100, container_name="container-a", host_veth="vha",
        )

        new_vlan = await plugin.hot_disconnect("lab1", "container-a", "eth1")
        assert new_vlan is not None
        assert new_vlan != 2100
        assert VLAN_RANGE_START <= new_vlan <= VLAN_RANGE_END
        assert plugin.endpoints["ep-a"].vlan_tag == new_vlan

    @pytest.mark.asyncio
    async def test_hot_disconnect_no_lab_returns_none(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)

        result = await plugin.hot_disconnect("nonexistent", "c", "eth1")
        assert result is None

    @pytest.mark.asyncio
    async def test_hot_disconnect_missing_endpoint_returns_none(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        _seed_lab(plugin)

        result = await plugin.hot_disconnect("lab1", "no-such-container", "eth1")
        assert result is None


# ===========================================================================
# VXLAN Tunnel Create / Delete
# ===========================================================================


class TestVxlanTunnels:
    """Tests for create_vxlan_tunnel and delete_vxlan_tunnel."""

    @pytest.mark.asyncio
    async def test_create_vxlan_tunnel_success(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        _stub_ovs(plugin, monkeypatch)
        _stub_save(plugin, monkeypatch)
        _seed_lab(plugin)

        port = await plugin.create_vxlan_tunnel(
            lab_id="lab1",
            link_id="link-1",
            local_ip="10.0.0.1",
            remote_ip="10.0.0.2",
            vni=50000,
            vlan_tag=2050,
        )

        assert port == "vx50000"
        assert 50000 in plugin.lab_bridges["lab1"].vxlan_tunnels
        assert plugin.lab_bridges["lab1"].vxlan_tunnels[50000] == "vx50000"

    @pytest.mark.asyncio
    async def test_create_vxlan_tunnel_returns_existing(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        _stub_ovs(plugin, monkeypatch)
        _stub_save(plugin, monkeypatch)
        bridge = _seed_lab(plugin)
        bridge.vxlan_tunnels[50000] = "vx50000"

        port = await plugin.create_vxlan_tunnel(
            lab_id="lab1",
            link_id="link-1",
            local_ip="10.0.0.1",
            remote_ip="10.0.0.2",
            vni=50000,
            vlan_tag=2050,
        )

        assert port == "vx50000"

    @pytest.mark.asyncio
    async def test_create_vxlan_tunnel_no_lab_raises(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)

        with pytest.raises(ValueError, match="Lab bridge not found"):
            await plugin.create_vxlan_tunnel(
                lab_id="nonexistent",
                link_id="link-1",
                local_ip="10.0.0.1",
                remote_ip="10.0.0.2",
                vni=50000,
                vlan_tag=2050,
            )

    @pytest.mark.asyncio
    async def test_delete_vxlan_tunnel_success(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        _stub_ovs(plugin, monkeypatch)
        _stub_save(plugin, monkeypatch)
        bridge = _seed_lab(plugin)
        bridge.vxlan_tunnels[50000] = "vx50000"

        result = await plugin.delete_vxlan_tunnel("lab1", 50000)
        assert result is True
        assert 50000 not in bridge.vxlan_tunnels

    @pytest.mark.asyncio
    async def test_delete_vxlan_tunnel_not_found(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        _seed_lab(plugin)

        result = await plugin.delete_vxlan_tunnel("lab1", 99999)
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_vxlan_tunnel_no_lab(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)

        result = await plugin.delete_vxlan_tunnel("nonexistent", 50000)
        assert result is False


# ===========================================================================
# External Interface Attach / Detach
# ===========================================================================


class TestExternalInterfaces:
    """Tests for attach/detach/list external interfaces."""

    @pytest.mark.asyncio
    async def test_attach_external_interface(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        _stub_ovs(plugin, monkeypatch)
        _stub_save(plugin, monkeypatch)
        monkeypatch.setattr(plugin, "_run_cmd", AsyncMock(return_value=(0, "", "")))
        _seed_lab(plugin)

        vlan = await plugin.attach_external_interface("lab1", "enp0s8", vlan_tag=300)
        assert vlan == 300
        assert "enp0s8" in plugin.lab_bridges["lab1"].external_ports
        assert plugin.lab_bridges["lab1"].external_ports["enp0s8"] == 300

    @pytest.mark.asyncio
    async def test_attach_external_interface_trunk_mode(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        _stub_ovs(plugin, monkeypatch)
        _stub_save(plugin, monkeypatch)
        monkeypatch.setattr(plugin, "_run_cmd", AsyncMock(return_value=(0, "", "")))
        _seed_lab(plugin)

        vlan = await plugin.attach_external_interface("lab1", "enp0s8")
        assert vlan == 0  # trunk mode

    @pytest.mark.asyncio
    async def test_attach_external_interface_already_attached(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        _stub_ovs(plugin, monkeypatch)
        bridge = _seed_lab(plugin)
        bridge.external_ports["enp0s8"] = 300

        vlan = await plugin.attach_external_interface("lab1", "enp0s8")
        assert vlan == 300  # Returns existing

    @pytest.mark.asyncio
    async def test_attach_external_interface_not_found(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        _stub_ovs(plugin, monkeypatch)
        _seed_lab(plugin)

        # Interface doesn't exist
        monkeypatch.setattr(plugin, "_run_cmd", AsyncMock(return_value=(1, "", "not found")))

        with pytest.raises(ValueError, match="not found"):
            await plugin.attach_external_interface("lab1", "nonexistent")

    @pytest.mark.asyncio
    async def test_detach_external_interface(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        _stub_ovs(plugin, monkeypatch)
        _stub_save(plugin, monkeypatch)
        bridge = _seed_lab(plugin)
        bridge.external_ports["enp0s8"] = 300

        result = await plugin.detach_external_interface("lab1", "enp0s8")
        assert result is True
        assert "enp0s8" not in bridge.external_ports

    @pytest.mark.asyncio
    async def test_detach_external_interface_not_attached(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        _seed_lab(plugin)

        result = await plugin.detach_external_interface("lab1", "nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_detach_external_interface_no_lab(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)

        result = await plugin.detach_external_interface("nonexistent", "eth0")
        assert result is False

    def test_list_external_interfaces(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        bridge = _seed_lab(plugin)
        bridge.external_ports = {"enp0s8": 300, "eth2": 0}

        result = plugin.list_external_interfaces("lab1")
        assert result == {"enp0s8": 300, "eth2": 0}

    def test_list_external_interfaces_empty(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        assert plugin.list_external_interfaces("nonexistent") == {}


# ===========================================================================
# Health Check
# ===========================================================================


class TestHealthCheck:
    """Tests for health_check."""

    @pytest.mark.asyncio
    async def test_health_check_healthy(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        _stub_ovs(plugin, monkeypatch)

        # Simulate socket existing
        monkeypatch.setattr("os.path.exists", lambda p: True)

        result = await plugin.health_check()
        assert result["healthy"] is True
        assert result["checks"]["ovs_available"] is True
        assert "uptime_seconds" in result

    @pytest.mark.asyncio
    async def test_health_check_ovs_unavailable(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)

        async def _ovs_vsctl(*args):
            if args[0] == "--version":
                return 1, "", "not found"
            return 0, "", ""

        monkeypatch.setattr(plugin, "_ovs_vsctl", _ovs_vsctl)
        monkeypatch.setattr("os.path.exists", lambda p: True)

        result = await plugin.health_check()
        assert result["healthy"] is False
        assert result["checks"]["ovs_available"] is False

    @pytest.mark.asyncio
    async def test_health_check_counts_resources(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        _stub_ovs(plugin, monkeypatch)
        monkeypatch.setattr("os.path.exists", lambda p: True)

        _seed_lab(plugin, "lab1")
        _seed_lab(plugin, "lab2")
        _seed_network(plugin, "net-1", lab_id="lab1")
        _seed_endpoint(plugin, "ep-1", "net-1")

        result = await plugin.health_check()
        assert result["checks"]["bridges_count"] == 2
        assert result["checks"]["networks_count"] == 1
        assert result["checks"]["endpoints_count"] == 1


# ===========================================================================
# State Serialization / Deserialization
# ===========================================================================


class TestStatePersistence:
    """Tests for state serialization round-trip."""

    def test_serialize_state(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        _seed_lab(plugin)
        _seed_network(plugin, "net-1")
        _seed_endpoint(plugin, "ep-1", "net-1", vlan_tag=150, container_name="ctr-1")

        state = plugin._serialize_state()

        assert state["version"] == 1
        assert "lab1" in state["lab_bridges"]
        assert "net-1" in state["networks"]
        assert "ep-1" in state["endpoints"]
        assert state["endpoints"]["ep-1"]["vlan_tag"] == 150
        assert state["endpoints"]["ep-1"]["container_name"] == "ctr-1"

    def test_deserialize_state(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)

        state_data = {
            "version": 1,
            "global_next_vlan": 200,
            "global_next_linked_vlan": 2100,
            "lab_bridges": {
                "lab1": {
                    "lab_id": "lab1",
                    "bridge_name": "arch-ovs",
                    "next_vlan": 200,
                    "network_ids": ["net-1"],
                    "last_activity": datetime.now(timezone.utc).isoformat(),
                    "vxlan_tunnels": {},
                    "external_ports": {},
                },
            },
            "networks": {
                "net-1": {
                    "network_id": "net-1",
                    "lab_id": "lab1",
                    "interface_name": "eth1",
                    "bridge_name": "arch-ovs",
                },
            },
            "endpoints": {
                "ep-1": {
                    "endpoint_id": "ep-1",
                    "network_id": "net-1",
                    "interface_name": "eth1",
                    "host_veth": "vh12345",
                    "cont_veth": "vc12345",
                    "vlan_tag": 150,
                    "container_name": "ctr-1",
                },
            },
        }

        plugin._deserialize_state(state_data)

        assert "lab1" in plugin.lab_bridges
        assert plugin.lab_bridges["lab1"].bridge_name == "arch-ovs"
        assert "net-1" in plugin.networks
        assert "ep-1" in plugin.endpoints
        assert plugin.endpoints["ep-1"].container_name == "ctr-1"
        assert plugin._global_next_vlan == 200
        assert plugin._global_next_linked_vlan == 2100
        assert 150 in plugin._allocated_vlans

    def test_serialize_deserialize_roundtrip(self, tmp_path, monkeypatch):
        plugin1 = _make_plugin(tmp_path)
        bridge = _seed_lab(plugin1)
        _seed_network(plugin1, "net-1")
        _seed_endpoint(plugin1, "ep-1", "net-1", vlan_tag=150, container_name="ctr-1")
        bridge.vxlan_tunnels[50000] = "vx50000"
        bridge.external_ports["enp0s8"] = 300

        serialized = plugin1._serialize_state()

        plugin2 = _make_plugin(tmp_path)
        plugin2._deserialize_state(serialized)

        assert list(plugin2.lab_bridges.keys()) == list(plugin1.lab_bridges.keys())
        assert list(plugin2.networks.keys()) == list(plugin1.networks.keys())
        assert list(plugin2.endpoints.keys()) == list(plugin1.endpoints.keys())
        assert plugin2.endpoints["ep-1"].vlan_tag == 150
        assert plugin2.lab_bridges["lab1"].vxlan_tunnels == {50000: "vx50000"}
        assert plugin2.lab_bridges["lab1"].external_ports == {"enp0s8": 300}

    @pytest.mark.asyncio
    async def test_save_and_load_state(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        _seed_lab(plugin)
        _seed_network(plugin, "net-1")
        _seed_endpoint(plugin, "ep-1", "net-1", vlan_tag=200)

        await plugin._save_state()
        assert plugin._state_file.exists()

        plugin2 = _make_plugin(tmp_path)
        # Stub _migrate_state_to_shared_bridge to avoid side effects
        monkeypatch.setattr(plugin2, "_migrate_state_to_shared_bridge", lambda: False)
        loaded = await plugin2._load_state()
        assert loaded is True
        assert "ep-1" in plugin2.endpoints
        assert plugin2.endpoints["ep-1"].vlan_tag == 200

    @pytest.mark.asyncio
    async def test_load_state_handles_corrupt_file(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        plugin._state_file.write_text("NOT VALID JSON{{{")

        loaded = await plugin._load_state()
        assert loaded is False

    @pytest.mark.asyncio
    async def test_load_state_handles_missing_file(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        # No state file on disk

        loaded = await plugin._load_state()
        assert loaded is False


# ===========================================================================
# Miscellaneous Plugin Methods
# ===========================================================================


class TestMiscMethods:
    """Tests for utility and status methods."""

    def test_touch_lab_updates_timestamp(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        bridge = _seed_lab(plugin)
        old_time = bridge.last_activity

        # Small delay to ensure different timestamp
        import time
        time.sleep(0.01)
        plugin._touch_lab("lab1")

        assert bridge.last_activity >= old_time

    def test_touch_lab_noop_for_unknown(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        # Should not raise
        plugin._touch_lab("nonexistent")

    def test_get_lab_vlan_range(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        _seed_lab(plugin)
        _seed_network(plugin, "net-1")
        _seed_network(plugin, "net-2", interface_name="eth2")
        _seed_endpoint(plugin, "ep-1", "net-1", vlan_tag=100)
        _seed_endpoint(plugin, "ep-2", "net-2", vlan_tag=200, host_veth="vhtest2", cont_veth="vctest2")

        low, high = plugin.get_lab_vlan_range("lab1")
        assert low == 100
        assert high == 200

    def test_get_lab_vlan_range_empty(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        assert plugin.get_lab_vlan_range("nonexistent") == (0, 0)

    def test_get_all_labs(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        _seed_lab(plugin, "lab1")
        _seed_lab(plugin, "lab2")

        labs = plugin.get_all_labs()
        assert set(labs) == {"lab1", "lab2"}

    def test_get_active_host_veths(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        _seed_lab(plugin)
        _seed_network(plugin, "net-1")
        _seed_endpoint(plugin, "ep-1", "net-1", host_veth="vh-abc")
        _seed_endpoint(
            plugin, "ep-2", "net-1",
            host_veth="vh-def", vlan_tag=101,
        )

        veths = plugin.get_active_host_veths()
        assert veths == {"vh-abc", "vh-def"}

    def test_get_lab_status(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        _seed_lab(plugin)
        _seed_network(plugin, "net-1")
        _seed_endpoint(plugin, "ep-1", "net-1", container_name="ctr-1", vlan_tag=150)

        status = plugin.get_lab_status("lab1")
        assert status is not None
        assert status["lab_id"] == "lab1"
        assert len(status["networks"]) == 1
        assert len(status["endpoints"]) == 1
        assert status["endpoints"][0]["vlan"] == 150

    def test_get_lab_status_unknown_lab(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        assert plugin.get_lab_status("nonexistent") is None

    def test_get_container_interface_mapping(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        _seed_lab(plugin)
        _seed_network(plugin, "net-1", interface_name="eth1")
        _seed_network(plugin, "net-2", interface_name="eth2")
        _seed_endpoint(
            plugin, "ep-1", "net-1",
            interface_name="eth1", container_name="ctr-1",
            host_veth="vh-a",
        )
        _seed_endpoint(
            plugin, "ep-2", "net-2",
            interface_name="eth2", container_name="ctr-1",
            host_veth="vh-b", vlan_tag=101,
        )

        mapping = plugin.get_container_interface_mapping("lab1", "ctr-1")
        assert mapping == {"vh-a": "eth1", "vh-b": "eth2"}

    def test_get_container_interface_mapping_filters_other_containers(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        _seed_lab(plugin)
        _seed_network(plugin, "net-1")
        _seed_network(plugin, "net-2", interface_name="eth2")
        _seed_endpoint(
            plugin, "ep-1", "net-1",
            container_name="other-ctr", host_veth="vh-a",
        )
        # Second endpoint belongs to our container
        _seed_endpoint(
            plugin, "ep-2", "net-2",
            interface_name="eth2",
            container_name="my-ctr", host_veth="vh-b",
            vlan_tag=101,
        )

        mapping = plugin.get_container_interface_mapping("lab1", "my-ctr")
        # Should include only our container's endpoint, not the other one
        assert "vh-a" not in mapping
        assert mapping == {"vh-b": "eth2"}


# ===========================================================================
# Handler No-ops (simple protocol handlers)
# ===========================================================================


class TestSimpleHandlers:
    """Tests for simple pass-through handlers."""

    @pytest.mark.asyncio
    async def test_handle_activate(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        request = _make_aiohttp_request({})

        resp = await plugin.handle_activate(request)
        body = _parse_response_body(resp)
        assert body["Implements"] == ["NetworkDriver"]

    @pytest.mark.asyncio
    async def test_handle_get_capabilities(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        request = _make_aiohttp_request({})

        resp = await plugin.handle_get_capabilities(request)
        body = _parse_response_body(resp)
        assert body["Scope"] == "local"

    @pytest.mark.asyncio
    async def test_handle_leave(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        request = _make_aiohttp_request({"EndpointID": "ep-123"})

        await plugin.handle_leave(request)
        # Should succeed with empty response

    @pytest.mark.asyncio
    async def test_handle_endpoint_oper_info(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        request = _make_aiohttp_request({"EndpointID": "ep-123"})

        resp = await plugin.handle_endpoint_oper_info(request)
        body = _parse_response_body(resp)
        assert body["Value"] == {}

    @pytest.mark.asyncio
    async def test_handle_discover_new(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        request = _make_aiohttp_request({})
        await plugin.handle_discover_new(request)

    @pytest.mark.asyncio
    async def test_handle_discover_delete(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        request = _make_aiohttp_request({})
        await plugin.handle_discover_delete(request)

    @pytest.mark.asyncio
    async def test_handle_program_external_connectivity(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        request = _make_aiohttp_request({})
        await plugin.handle_program_external_connectivity(request)

    @pytest.mark.asyncio
    async def test_handle_revoke_external_connectivity(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        request = _make_aiohttp_request({})
        await plugin.handle_revoke_external_connectivity(request)


# ===========================================================================
# Endpoint VLAN Operations
# ===========================================================================


class TestEndpointVlanOps:
    """Tests for set_endpoint_vlan, set_endpoint_vlan_by_host_veth, get_endpoint_vlan."""

    @pytest.mark.asyncio
    async def test_set_endpoint_vlan_updates_tracked(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        _stub_save(plugin, monkeypatch)
        _seed_lab(plugin)
        _seed_network(plugin, "net-1")
        _seed_endpoint(plugin, "ep-1", "net-1", vlan_tag=100, container_name="ctr-1")

        result = await plugin.set_endpoint_vlan("lab1", "ctr-1", "eth1", 2500)
        assert result is True
        assert plugin.endpoints["ep-1"].vlan_tag == 2500

    @pytest.mark.asyncio
    async def test_get_endpoint_vlan_from_memory(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        _seed_lab(plugin)
        _seed_network(plugin, "net-1")
        _seed_endpoint(plugin, "ep-1", "net-1", vlan_tag=150, container_name="ctr-1")

        vlan = await plugin.get_endpoint_vlan("lab1", "ctr-1", "eth1")
        assert vlan == 150

    @pytest.mark.asyncio
    async def test_get_endpoint_vlan_from_ovs(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        _seed_lab(plugin)
        _seed_network(plugin, "net-1")
        _seed_endpoint(
            plugin, "ep-1", "net-1",
            vlan_tag=150, container_name="ctr-1", host_veth="vhtest1",
        )

        monkeypatch.setattr(
            plugin, "_run_cmd",
            AsyncMock(return_value=(0, "2500", "")),
        )

        vlan = await plugin.get_endpoint_vlan("lab1", "ctr-1", "eth1", read_from_ovs=True)
        assert vlan == 2500

    @pytest.mark.asyncio
    async def test_set_endpoint_vlan_by_host_veth(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        _stub_save(plugin, monkeypatch)
        _seed_lab(plugin)
        _seed_network(plugin, "net-1")
        _seed_endpoint(plugin, "ep-1", "net-1", vlan_tag=100, host_veth="vhtarget")

        result = await plugin.set_endpoint_vlan_by_host_veth("lab1", "vhtarget", 2500)
        assert result is True
        assert plugin.endpoints["ep-1"].vlan_tag == 2500
        # Should be tracked in linked set since 2500 > LINKED_VLAN_START
        assert 2500 in plugin._allocated_linked_vlans

    @pytest.mark.asyncio
    async def test_set_endpoint_vlan_by_host_veth_same_tag_noop(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        _seed_lab(plugin)
        _seed_network(plugin, "net-1")
        _seed_endpoint(plugin, "ep-1", "net-1", vlan_tag=100, host_veth="vhtarget")

        result = await plugin.set_endpoint_vlan_by_host_veth("lab1", "vhtarget", 100)
        assert result is True

    @pytest.mark.asyncio
    async def test_set_endpoint_vlan_by_host_veth_not_found(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)

        result = await plugin.set_endpoint_vlan_by_host_veth("lab1", "nonexistent", 100)
        assert result is False

    @pytest.mark.asyncio
    async def test_set_endpoint_container_name(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        _stub_save(plugin, monkeypatch)
        _seed_lab(plugin)
        _seed_network(plugin, "net-1")
        _seed_endpoint(plugin, "ep-1", "net-1")

        assert plugin.endpoints["ep-1"].container_name is None
        await plugin.set_endpoint_container_name("ep-1", "my-container")
        assert plugin.endpoints["ep-1"].container_name == "my-container"


# ===========================================================================
# Full Lab Cleanup
# ===========================================================================


class TestFullLabCleanup:
    """Tests for _full_lab_cleanup."""

    @pytest.mark.asyncio
    async def test_full_lab_cleanup_removes_everything(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)
        _stub_ovs(plugin, monkeypatch)
        _stub_save(plugin, monkeypatch)

        bridge = _seed_lab(plugin)
        bridge.vxlan_tunnels[50000] = "vx50000"
        bridge.external_ports["enp0s8"] = 300
        _seed_network(plugin, "net-1")
        _seed_endpoint(plugin, "ep-1", "net-1", vlan_tag=150)

        await plugin._full_lab_cleanup("lab1")

        assert "lab1" not in plugin.lab_bridges
        assert "net-1" not in plugin.networks
        assert "ep-1" not in plugin.endpoints
        assert 150 not in plugin._allocated_vlans

    @pytest.mark.asyncio
    async def test_full_lab_cleanup_noop_for_unknown(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)

        # Should not raise
        await plugin._full_lab_cleanup("nonexistent")


# ===========================================================================
# Validate Endpoint Exists
# ===========================================================================


class TestValidateEndpoint:
    """Tests for _validate_endpoint_exists."""

    @pytest.mark.asyncio
    async def test_validates_ovs_port_presence(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)

        async def _run_cmd(cmd):
            if "port-to-br" in cmd:
                return 0, "arch-ovs", ""
            return 0, "", ""

        monkeypatch.setattr(plugin, "_run_cmd", _run_cmd)

        ep = EndpointState(
            endpoint_id="ep-1", network_id="net-1",
            interface_name="eth1", host_veth="vhtest",
            cont_veth="vctest", vlan_tag=100,
        )
        assert await plugin._validate_endpoint_exists(ep) is True

    @pytest.mark.asyncio
    async def test_rejects_missing_ovs_port(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)

        async def _run_cmd(cmd):
            return 1, "", "no such port"

        monkeypatch.setattr(plugin, "_run_cmd", _run_cmd)

        ep = EndpointState(
            endpoint_id="ep-1", network_id="net-1",
            interface_name="eth1", host_veth="vhmissing",
            cont_veth="vcmissing", vlan_tag=100,
        )
        assert await plugin._validate_endpoint_exists(ep) is False

    @pytest.mark.asyncio
    async def test_rejects_empty_host_veth(self, tmp_path, monkeypatch):
        plugin = _make_plugin(tmp_path)

        ep = EndpointState(
            endpoint_id="ep-1", network_id="net-1",
            interface_name="eth1", host_veth="",
            cont_veth="vctest", vlan_tag=100,
        )
        assert await plugin._validate_endpoint_exists(ep) is False
