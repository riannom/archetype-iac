"""Comprehensive tests for agent/network/plugin_handlers.py.

Covers all 12 Docker network plugin protocol handler methods in
PluginHandlersMixin: Activate, GetCapabilities, CreateNetwork,
DeleteNetwork, CreateEndpoint, DeleteEndpoint, Join, Leave,
EndpointOperInfo, DiscoverNew, DiscoverDelete,
ProgramExternalConnectivity, RevokeExternalConnectivity.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.network import docker_plugin as plugin_mod
from agent.network.docker_plugin import (
    DockerOVSPlugin,
    EndpointState,
    LabBridge,
    NetworkState,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_plugin(monkeypatch, tmp_path):
    """Create a DockerOVSPlugin with mocked settings."""
    monkeypatch.setattr(plugin_mod.settings, "workspace_path", str(tmp_path))
    monkeypatch.setattr(plugin_mod.settings, "ovs_bridge_name", "arch-ovs")
    plugin = DockerOVSPlugin()
    return plugin


def _make_io_plugin(monkeypatch, tmp_path):
    """Create a DockerOVSPlugin with mocked I/O for async tests."""
    plugin = _make_plugin(monkeypatch, tmp_path)
    plugin._ovs_vsctl = AsyncMock(return_value=(0, "", ""))
    plugin._run_cmd = AsyncMock(return_value=(0, "", ""))
    plugin._delete_port = AsyncMock()
    plugin._mark_dirty_and_save = AsyncMock()
    plugin._ensure_bridge = AsyncMock(
        return_value=LabBridge(lab_id="test-lab", bridge_name="arch-ovs")
    )
    plugin._allocate_vlan = AsyncMock(return_value=100)
    plugin._create_veth_pair = AsyncMock(return_value=True)
    plugin._attach_to_ovs = AsyncMock(return_value=True)
    plugin._generate_veth_names = MagicMock(return_value=("vh-test", "vc-test"))
    plugin._touch_lab = MagicMock()
    plugin._release_vlan = MagicMock()
    plugin._maybe_delete_bridge = AsyncMock()
    plugin._bridge_name = "arch-ovs"
    return plugin


def _setup_lab(plugin, lab_id="lab1"):
    """Add a LabBridge and return it."""
    lb = LabBridge(lab_id=lab_id, bridge_name="arch-ovs")
    plugin.lab_bridges[lab_id] = lb
    return lb


def _add_network(plugin, lab_id, net_id, iface="eth1"):
    """Add a NetworkState and register with lab bridge."""
    ns = NetworkState(
        network_id=net_id,
        lab_id=lab_id,
        interface_name=iface,
        bridge_name="arch-ovs",
    )
    plugin.networks[net_id] = ns
    lb = plugin.lab_bridges.get(lab_id)
    if lb:
        lb.network_ids.add(net_id)
    return ns


def _add_endpoint(plugin, ep_id, net_id, iface, vlan, host_veth="vh1",
                  cont_veth="vc1", container_name=None):
    """Add an EndpointState to the plugin."""
    ep = EndpointState(
        endpoint_id=ep_id,
        network_id=net_id,
        interface_name=iface,
        host_veth=host_veth,
        cont_veth=cont_veth,
        vlan_tag=vlan,
        container_name=container_name,
    )
    plugin.endpoints[ep_id] = ep
    plugin._allocated_vlans.add(vlan)
    return ep


def _make_request(data: dict) -> MagicMock:
    """Create a mock aiohttp web.Request that returns given JSON."""
    request = MagicMock()
    request.json = AsyncMock(return_value=data)
    return request


def _response_body(response) -> dict:
    """Extract the JSON body from an aiohttp web.json_response result."""
    # The aiohttp stub stores data in .body, real aiohttp stores in .body as
    # serialized JSON. Handle both.
    body = response.body
    if isinstance(body, dict):
        return body
    if isinstance(body, (str, bytes)):
        return json.loads(body)
    return body


# ===========================================================================
# Plugin.Activate
# ===========================================================================

class TestHandleActivate:
    """Tests for /Plugin.Activate handler."""

    @pytest.mark.asyncio
    async def test_returns_network_driver_capability(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        request = _make_request({})

        response = await plugin.handle_activate(request)
        body = _response_body(response)

        assert body == {"Implements": ["NetworkDriver"]}

    @pytest.mark.asyncio
    async def test_ignores_request_body(self, monkeypatch, tmp_path):
        """Activate ignores whatever is in the request body."""
        plugin = _make_plugin(monkeypatch, tmp_path)
        request = _make_request({"unexpected": "data"})

        response = await plugin.handle_activate(request)
        body = _response_body(response)

        assert body["Implements"] == ["NetworkDriver"]


# ===========================================================================
# NetworkDriver.GetCapabilities
# ===========================================================================

class TestHandleGetCapabilities:
    """Tests for /NetworkDriver.GetCapabilities handler."""

    @pytest.mark.asyncio
    async def test_returns_local_scope(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        request = _make_request({})

        response = await plugin.handle_get_capabilities(request)
        body = _response_body(response)

        assert body["Scope"] == "local"
        assert body["ConnectivityScope"] == "local"

    @pytest.mark.asyncio
    async def test_capabilities_structure(self, monkeypatch, tmp_path):
        """Capabilities response has exactly the expected keys."""
        plugin = _make_plugin(monkeypatch, tmp_path)
        request = _make_request({})

        response = await plugin.handle_get_capabilities(request)
        body = _response_body(response)

        assert set(body.keys()) == {"Scope", "ConnectivityScope"}


# ===========================================================================
# NetworkDriver.CreateNetwork
# ===========================================================================

class TestHandleCreateNetwork:
    """Tests for /NetworkDriver.CreateNetwork handler."""

    @pytest.mark.asyncio
    async def test_creates_network_successfully(self, monkeypatch, tmp_path):
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        request = _make_request({
            "NetworkID": "net-abc123",
            "Options": {
                "com.docker.network.generic": {
                    "lab_id": "lab1",
                    "interface_name": "eth2",
                }
            },
        })

        response = await plugin.handle_create_network(request)
        body = _response_body(response)

        assert body == {}
        assert "net-abc123" in plugin.networks
        net = plugin.networks["net-abc123"]
        assert net.lab_id == "lab1"
        assert net.interface_name == "eth2"
        assert net.bridge_name == "arch-ovs"
        plugin._ensure_bridge.assert_awaited_once_with("lab1")
        plugin._mark_dirty_and_save.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_default_interface_name_is_eth1(self, monkeypatch, tmp_path):
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        request = _make_request({
            "NetworkID": "net-default",
            "Options": {
                "com.docker.network.generic": {
                    "lab_id": "lab1",
                    # No interface_name specified
                }
            },
        })

        response = await plugin.handle_create_network(request)
        body = _response_body(response)

        assert body == {}
        assert plugin.networks["net-default"].interface_name == "eth1"

    @pytest.mark.asyncio
    async def test_missing_lab_id_returns_error(self, monkeypatch, tmp_path):
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        request = _make_request({
            "NetworkID": "net-nolabid",
            "Options": {
                "com.docker.network.generic": {
                    # No lab_id
                    "interface_name": "eth1",
                }
            },
        })

        response = await plugin.handle_create_network(request)
        body = _response_body(response)

        assert "Err" in body
        assert "lab_id" in body["Err"]
        assert "net-nolabid" not in plugin.networks

    @pytest.mark.asyncio
    async def test_empty_options_returns_error(self, monkeypatch, tmp_path):
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        request = _make_request({
            "NetworkID": "net-noopts",
            "Options": {},
        })

        response = await plugin.handle_create_network(request)
        body = _response_body(response)

        assert "Err" in body
        assert "lab_id" in body["Err"]

    @pytest.mark.asyncio
    async def test_missing_options_key_returns_error(self, monkeypatch, tmp_path):
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        request = _make_request({
            "NetworkID": "net-nometa",
            # No "Options" key at all
        })

        response = await plugin.handle_create_network(request)
        body = _response_body(response)

        assert "Err" in body

    @pytest.mark.asyncio
    async def test_ensure_bridge_failure_returns_error(self, monkeypatch, tmp_path):
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        plugin._ensure_bridge = AsyncMock(
            side_effect=RuntimeError("OVS bridge creation failed")
        )
        request = _make_request({
            "NetworkID": "net-fail",
            "Options": {
                "com.docker.network.generic": {
                    "lab_id": "lab1",
                    "interface_name": "eth1",
                }
            },
        })

        response = await plugin.handle_create_network(request)
        body = _response_body(response)

        assert "Err" in body
        assert "OVS bridge creation failed" in body["Err"]
        assert "net-fail" not in plugin.networks

    @pytest.mark.asyncio
    async def test_network_registered_with_lab_bridge(self, monkeypatch, tmp_path):
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        lab_bridge = LabBridge(lab_id="lab1", bridge_name="arch-ovs")
        plugin._ensure_bridge = AsyncMock(return_value=lab_bridge)

        request = _make_request({
            "NetworkID": "net-reg",
            "Options": {
                "com.docker.network.generic": {
                    "lab_id": "lab1",
                    "interface_name": "eth3",
                }
            },
        })

        await plugin.handle_create_network(request)

        assert "net-reg" in lab_bridge.network_ids

    @pytest.mark.asyncio
    async def test_missing_network_id_uses_empty_string(self, monkeypatch, tmp_path):
        """When NetworkID is missing from data, it defaults to empty string."""
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        request = _make_request({
            # No NetworkID
            "Options": {
                "com.docker.network.generic": {
                    "lab_id": "lab1",
                }
            },
        })

        response = await plugin.handle_create_network(request)
        body = _response_body(response)

        assert body == {}
        assert "" in plugin.networks


# ===========================================================================
# NetworkDriver.DeleteNetwork
# ===========================================================================

class TestHandleDeleteNetwork:
    """Tests for /NetworkDriver.DeleteNetwork handler."""

    @pytest.mark.asyncio
    async def test_deletes_existing_network(self, monkeypatch, tmp_path):
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        lb = _setup_lab(plugin, "lab1")
        _add_network(plugin, "lab1", "net-del", "eth1")

        request = _make_request({"NetworkID": "net-del"})

        response = await plugin.handle_delete_network(request)
        body = _response_body(response)

        assert body == {}
        assert "net-del" not in plugin.networks
        assert "net-del" not in lb.network_ids
        plugin._maybe_delete_bridge.assert_awaited_once_with("lab1")
        plugin._mark_dirty_and_save.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_nonexistent_network_succeeds(self, monkeypatch, tmp_path):
        """Docker protocol says DeleteNetwork must succeed even if unknown."""
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        request = _make_request({"NetworkID": "net-ghost"})

        response = await plugin.handle_delete_network(request)
        body = _response_body(response)

        assert body == {}
        plugin._mark_dirty_and_save.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delete_network_without_lab_bridge(self, monkeypatch, tmp_path):
        """Network exists but its lab bridge was already removed."""
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        # Add network without a lab bridge
        ns = NetworkState(
            network_id="net-orphan",
            lab_id="lab-gone",
            interface_name="eth1",
            bridge_name="arch-ovs",
        )
        plugin.networks["net-orphan"] = ns

        request = _make_request({"NetworkID": "net-orphan"})

        response = await plugin.handle_delete_network(request)
        body = _response_body(response)

        assert body == {}
        assert "net-orphan" not in plugin.networks
        plugin._maybe_delete_bridge.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delete_empty_network_id(self, monkeypatch, tmp_path):
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        request = _make_request({})

        response = await plugin.handle_delete_network(request)
        body = _response_body(response)

        assert body == {}


# ===========================================================================
# NetworkDriver.CreateEndpoint
# ===========================================================================

class TestHandleCreateEndpoint:
    """Tests for /NetworkDriver.CreateEndpoint handler."""

    @pytest.mark.asyncio
    async def test_creates_endpoint_successfully(self, monkeypatch, tmp_path):
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        lb = _setup_lab(plugin, "lab1")
        _add_network(plugin, "lab1", "net1", "eth2")

        request = _make_request({
            "NetworkID": "net1",
            "EndpointID": "ep-abc123",
        })

        response = await plugin.handle_create_endpoint(request)
        body = _response_body(response)

        assert body == {"Interface": {}}
        assert "ep-abc123" in plugin.endpoints
        ep = plugin.endpoints["ep-abc123"]
        assert ep.network_id == "net1"
        assert ep.interface_name == "eth2"
        assert ep.host_veth == "vh-test"
        assert ep.cont_veth == "vc-test"
        assert ep.vlan_tag == 100
        plugin._generate_veth_names.assert_called_once_with("ep-abc123")
        plugin._allocate_vlan.assert_awaited_once_with(lb)
        plugin._create_veth_pair.assert_awaited_once_with("vh-test", "vc-test")
        plugin._attach_to_ovs.assert_awaited_once_with(
            "arch-ovs",
            "vh-test",
            100,
            external_ids={
                "archetype.endpoint_id": "ep-abc123",
                "archetype.interface_name": "eth2",
                "archetype.lab_id": "lab1",
                "archetype.network_id": "net1",
            },
        )
        plugin._touch_lab.assert_called_once_with("lab1")
        plugin._mark_dirty_and_save.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unknown_network_returns_error(self, monkeypatch, tmp_path):
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        request = _make_request({
            "NetworkID": "net-missing",
            "EndpointID": "ep-1",
        })

        response = await plugin.handle_create_endpoint(request)
        body = _response_body(response)

        assert "Err" in body
        assert "not found" in body["Err"]
        assert "ep-1" not in plugin.endpoints

    @pytest.mark.asyncio
    async def test_missing_lab_bridge_returns_error(self, monkeypatch, tmp_path):
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        # Add network without a lab bridge
        ns = NetworkState(
            network_id="net-nobridge",
            lab_id="lab-gone",
            interface_name="eth1",
            bridge_name="arch-ovs",
        )
        plugin.networks["net-nobridge"] = ns

        request = _make_request({
            "NetworkID": "net-nobridge",
            "EndpointID": "ep-2",
        })

        response = await plugin.handle_create_endpoint(request)
        body = _response_body(response)

        assert "Err" in body
        assert "Lab bridge" in body["Err"]

    @pytest.mark.asyncio
    async def test_veth_creation_failure_returns_error(self, monkeypatch, tmp_path):
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        _setup_lab(plugin, "lab1")
        _add_network(plugin, "lab1", "net1", "eth1")
        plugin._create_veth_pair = AsyncMock(return_value=False)

        request = _make_request({
            "NetworkID": "net1",
            "EndpointID": "ep-fail-veth",
        })

        response = await plugin.handle_create_endpoint(request)
        body = _response_body(response)

        assert "Err" in body
        assert "veth" in body["Err"].lower()
        assert "ep-fail-veth" not in plugin.endpoints

    @pytest.mark.asyncio
    async def test_ovs_attach_failure_returns_error_and_cleans_veth(
        self, monkeypatch, tmp_path
    ):
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        _setup_lab(plugin, "lab1")
        _add_network(plugin, "lab1", "net1", "eth1")
        plugin._attach_to_ovs = AsyncMock(return_value=False)

        request = _make_request({
            "NetworkID": "net1",
            "EndpointID": "ep-fail-ovs",
        })

        response = await plugin.handle_create_endpoint(request)
        body = _response_body(response)

        assert "Err" in body
        assert "OVS" in body["Err"] or "attach" in body["Err"].lower()
        # Should clean up the veth pair
        plugin._run_cmd.assert_awaited_once_with(["ip", "link", "delete", "vh-test"])
        assert "ep-fail-ovs" not in plugin.endpoints


# ===========================================================================
# NetworkDriver.DeleteEndpoint
# ===========================================================================

class TestHandleDeleteEndpoint:
    """Tests for /NetworkDriver.DeleteEndpoint handler."""

    @pytest.mark.asyncio
    async def test_deletes_existing_endpoint(self, monkeypatch, tmp_path):
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        _setup_lab(plugin, "lab1")
        _add_network(plugin, "lab1", "net1", "eth1")
        _add_endpoint(plugin, "ep-del", "net1", "eth1", 200,
                      host_veth="vh-del", cont_veth="vc-del")

        request = _make_request({
            "NetworkID": "net1",
            "EndpointID": "ep-del",
        })

        response = await plugin.handle_delete_endpoint(request)
        body = _response_body(response)

        assert body == {}
        assert "ep-del" not in plugin.endpoints
        plugin._release_vlan.assert_called_once_with(200)
        plugin._delete_port.assert_awaited_once_with("arch-ovs", "vh-del")
        plugin._mark_dirty_and_save.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_nonexistent_endpoint_succeeds(self, monkeypatch, tmp_path):
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        request = _make_request({
            "NetworkID": "net-whatever",
            "EndpointID": "ep-ghost",
        })

        response = await plugin.handle_delete_endpoint(request)
        body = _response_body(response)

        assert body == {}
        plugin._release_vlan.assert_not_called()
        plugin._delete_port.assert_not_awaited()
        plugin._mark_dirty_and_save.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delete_endpoint_without_matching_network(self, monkeypatch, tmp_path):
        """Endpoint exists but network was already removed."""
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        _add_endpoint(plugin, "ep-orphan", "net-gone", "eth1", 300,
                      host_veth="vh-orphan")

        request = _make_request({
            "NetworkID": "net-gone",
            "EndpointID": "ep-orphan",
        })

        response = await plugin.handle_delete_endpoint(request)
        body = _response_body(response)

        assert body == {}
        assert "ep-orphan" not in plugin.endpoints
        plugin._release_vlan.assert_called_once_with(300)
        # No network => no delete_port call
        plugin._delete_port.assert_not_awaited()


# ===========================================================================
# NetworkDriver.Join
# ===========================================================================

class TestHandleJoin:
    """Tests for /NetworkDriver.Join handler."""

    @pytest.mark.asyncio
    async def test_join_returns_interface_mapping(self, monkeypatch, tmp_path):
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        _add_endpoint(plugin, "ep-join", "net1", "eth3", 100,
                      host_veth="vh-join", cont_veth="vc-join")

        request = _make_request({
            "EndpointID": "ep-join",
            "SandboxKey": "/var/run/docker/netns/abc123",
        })

        response = await plugin.handle_join(request)
        body = _response_body(response)

        assert "InterfaceName" in body
        assert body["InterfaceName"]["SrcName"] == "vc-join"
        assert body["InterfaceName"]["DstName"] == "eth3"

    @pytest.mark.asyncio
    async def test_join_uses_dst_name_not_prefix(self, monkeypatch, tmp_path):
        """Verify DstName is used (exact name) rather than DstPrefix (auto-number)."""
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        _add_endpoint(plugin, "ep-dst", "net1", "eth5", 100,
                      cont_veth="vc-dst")

        request = _make_request({
            "EndpointID": "ep-dst",
            "SandboxKey": "/var/run/docker/netns/xyz",
        })

        response = await plugin.handle_join(request)
        body = _response_body(response)

        # Must have DstName, not DstPrefix
        assert "DstName" in body["InterfaceName"]
        assert "DstPrefix" not in body.get("InterfaceName", {})

    @pytest.mark.asyncio
    async def test_join_unknown_endpoint_returns_error(self, monkeypatch, tmp_path):
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        request = _make_request({
            "EndpointID": "ep-missing",
            "SandboxKey": "/var/run/docker/netns/ns1",
        })

        response = await plugin.handle_join(request)
        body = _response_body(response)

        assert "Err" in body
        assert "not found" in body["Err"]


# ===========================================================================
# NetworkDriver.Leave
# ===========================================================================

class TestHandleLeave:
    """Tests for /NetworkDriver.Leave handler."""

    @pytest.mark.asyncio
    async def test_leave_returns_empty(self, monkeypatch, tmp_path):
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        request = _make_request({
            "EndpointID": "ep-leave",
        })

        response = await plugin.handle_leave(request)
        body = _response_body(response)

        assert body == {}

    @pytest.mark.asyncio
    async def test_leave_with_unknown_endpoint_still_succeeds(self, monkeypatch, tmp_path):
        """Leave is a no-op notification; always succeeds."""
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        request = _make_request({
            "EndpointID": "ep-nonexistent",
        })

        response = await plugin.handle_leave(request)
        body = _response_body(response)

        assert body == {}


# ===========================================================================
# NetworkDriver.EndpointOperInfo
# ===========================================================================

class TestHandleEndpointOperInfo:
    """Tests for /NetworkDriver.EndpointOperInfo handler."""

    @pytest.mark.asyncio
    async def test_returns_empty_value(self, monkeypatch, tmp_path):
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        request = _make_request({
            "EndpointID": "ep-info",
        })

        response = await plugin.handle_endpoint_oper_info(request)
        body = _response_body(response)

        assert body == {"Value": {}}

    @pytest.mark.asyncio
    async def test_oper_info_unknown_endpoint(self, monkeypatch, tmp_path):
        """EndpointOperInfo must work even for unknown endpoints."""
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        request = _make_request({
            "EndpointID": "ep-unknown-oper",
        })

        response = await plugin.handle_endpoint_oper_info(request)
        body = _response_body(response)

        assert body == {"Value": {}}


# ===========================================================================
# NetworkDriver.DiscoverNew / DiscoverDelete
# ===========================================================================

class TestHandleDiscovery:
    """Tests for /NetworkDriver.DiscoverNew and DiscoverDelete handlers."""

    @pytest.mark.asyncio
    async def test_discover_new_returns_empty(self, monkeypatch, tmp_path):
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        request = _make_request({"DiscoveryType": 1, "DiscoveryData": {}})

        response = await plugin.handle_discover_new(request)
        body = _response_body(response)

        assert body == {}

    @pytest.mark.asyncio
    async def test_discover_delete_returns_empty(self, monkeypatch, tmp_path):
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        request = _make_request({"DiscoveryType": 1, "DiscoveryData": {}})

        response = await plugin.handle_discover_delete(request)
        body = _response_body(response)

        assert body == {}


# ===========================================================================
# NetworkDriver.ProgramExternalConnectivity / RevokeExternalConnectivity
# ===========================================================================

class TestHandleExternalConnectivity:
    """Tests for ProgramExternalConnectivity and RevokeExternalConnectivity."""

    @pytest.mark.asyncio
    async def test_program_external_returns_empty(self, monkeypatch, tmp_path):
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        request = _make_request({
            "NetworkID": "net1",
            "EndpointID": "ep1",
            "Options": {},
        })

        response = await plugin.handle_program_external_connectivity(request)
        body = _response_body(response)

        assert body == {}

    @pytest.mark.asyncio
    async def test_revoke_external_returns_empty(self, monkeypatch, tmp_path):
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        request = _make_request({
            "NetworkID": "net1",
            "EndpointID": "ep1",
        })

        response = await plugin.handle_revoke_external_connectivity(request)
        body = _response_body(response)

        assert body == {}
