"""Tests for agent/network/plugin_handlers.py — Docker OVS plugin handlers (round 11)."""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

from aiohttp import web


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_request(data: dict) -> MagicMock:
    """Create a mock aiohttp.web.Request with json() returning data."""
    req = MagicMock(spec=web.Request)
    req.json = AsyncMock(return_value=data)
    return req


class _TestPlugin:
    """Minimal plugin object with mixin methods and required state."""

    def __init__(self):
        from agent.network.plugin_handlers import PluginHandlersMixin
        # Mix in handlers
        for attr in dir(PluginHandlersMixin):
            if attr.startswith("handle_"):
                setattr(self, attr, getattr(PluginHandlersMixin, attr).__get__(self))

        self.networks = {}
        self.endpoints = {}
        self.lab_bridges = {}
        self._locked = self._make_locked
        self._ensure_bridge = AsyncMock()
        self._allocate_vlan = AsyncMock(return_value=100)
        self._generate_veth_names = MagicMock(return_value=("vh1234", "vc5678"))
        self._create_veth_pair = AsyncMock(return_value=True)
        self._attach_to_ovs = AsyncMock(return_value=True)
        self._mark_dirty_and_save = AsyncMock()
        self._run_cmd = AsyncMock(return_value=(0, "", ""))
        self._touch_lab = MagicMock()
        self._release_vlan = MagicMock()
        self._delete_port = AsyncMock()
        self._maybe_delete_bridge = AsyncMock()

    @asynccontextmanager
    async def _make_locked(self):
        yield


# ---------------------------------------------------------------------------
# handle_activate / handle_get_capabilities
# ---------------------------------------------------------------------------


class TestActivateCapabilities:

    def test_activate_implements_network_driver(self):
        plugin = _TestPlugin()
        resp = _run(plugin.handle_activate(_make_request({})))
        body = json.loads(resp.body) if hasattr(resp, 'body') else resp.body
        if isinstance(body, dict):
            assert "NetworkDriver" in body.get("Implements", [])

    def test_get_capabilities_scope_local(self):
        plugin = _TestPlugin()
        resp = _run(plugin.handle_get_capabilities(_make_request({})))
        body = json.loads(resp.body) if hasattr(resp, 'body') else resp.body
        if isinstance(body, dict):
            assert body.get("Scope") == "local"


# ---------------------------------------------------------------------------
# handle_create_network
# ---------------------------------------------------------------------------


class TestCreateNetwork:

    def test_missing_lab_id_returns_error(self):
        plugin = _TestPlugin()
        req = _make_request({
            "NetworkID": "net-1",
            "Options": {"com.docker.network.generic": {}},
        })
        resp = _run(plugin.handle_create_network(req))
        body = resp.body if isinstance(resp.body, dict) else json.loads(resp.body)
        assert "Err" in body

    def test_bridge_exception_returns_error(self):
        plugin = _TestPlugin()
        plugin._ensure_bridge = AsyncMock(side_effect=RuntimeError("OVS down"))
        req = _make_request({
            "NetworkID": "net-1",
            "Options": {"com.docker.network.generic": {"lab_id": "lab1", "interface_name": "eth1"}},
        })
        resp = _run(plugin.handle_create_network(req))
        body = resp.body if isinstance(resp.body, dict) else json.loads(resp.body)
        assert "Err" in body

    def test_happy_path_persists_state(self):
        plugin = _TestPlugin()
        bridge = MagicMock()
        bridge.bridge_name = "arch-ovs"
        bridge.network_ids = set()
        plugin._ensure_bridge = AsyncMock(return_value=bridge)

        req = _make_request({
            "NetworkID": "net-1",
            "Options": {"com.docker.network.generic": {"lab_id": "lab1", "interface_name": "eth1"}},
        })
        _run(plugin.handle_create_network(req))
        assert "net-1" in plugin.networks
        assert plugin.networks["net-1"].lab_id == "lab1"
        plugin._mark_dirty_and_save.assert_awaited_once()

    def test_custom_interface_name(self):
        plugin = _TestPlugin()
        bridge = MagicMock()
        bridge.bridge_name = "arch-ovs"
        bridge.network_ids = set()
        plugin._ensure_bridge = AsyncMock(return_value=bridge)

        req = _make_request({
            "NetworkID": "net-2",
            "Options": {"com.docker.network.generic": {"lab_id": "lab1", "interface_name": "eth5"}},
        })
        _run(plugin.handle_create_network(req))
        assert plugin.networks["net-2"].interface_name == "eth5"


# ---------------------------------------------------------------------------
# handle_delete_network
# ---------------------------------------------------------------------------


class TestDeleteNetwork:

    def test_state_persisted(self):
        plugin = _TestPlugin()
        from agent.network.docker_plugin import NetworkState
        plugin.networks["net-1"] = NetworkState(
            network_id="net-1", lab_id="lab1",
            interface_name="eth1", bridge_name="arch-ovs",
        )
        plugin.lab_bridges["lab1"] = MagicMock(network_ids={"net-1"})

        req = _make_request({"NetworkID": "net-1"})
        _run(plugin.handle_delete_network(req))
        assert "net-1" not in plugin.networks
        plugin._mark_dirty_and_save.assert_awaited()


# ---------------------------------------------------------------------------
# handle_create_endpoint
# ---------------------------------------------------------------------------


class TestCreateEndpoint:

    def test_veth_names_from_generator(self):
        plugin = _TestPlugin()
        from agent.network.docker_plugin import NetworkState
        plugin.networks["net-1"] = NetworkState(
            network_id="net-1", lab_id="lab1",
            interface_name="eth1", bridge_name="arch-ovs",
        )
        plugin.lab_bridges["lab1"] = MagicMock(bridge_name="arch-ovs")

        req = _make_request({"NetworkID": "net-1", "EndpointID": "ep-1"})
        _run(plugin.handle_create_endpoint(req))

        assert "ep-1" in plugin.endpoints
        ep = plugin.endpoints["ep-1"]
        assert ep.host_veth == "vh1234"
        assert ep.cont_veth == "vc5678"
        assert ep.vlan_tag == 100

    def test_network_not_found_error(self):
        plugin = _TestPlugin()
        req = _make_request({"NetworkID": "nonexistent", "EndpointID": "ep-1"})
        resp = _run(plugin.handle_create_endpoint(req))
        body = resp.body if isinstance(resp.body, dict) else json.loads(resp.body)
        assert "Err" in body


# ---------------------------------------------------------------------------
# handle_join
# ---------------------------------------------------------------------------


class TestJoin:

    def test_response_shape(self):
        plugin = _TestPlugin()
        from agent.network.docker_plugin import EndpointState
        plugin.endpoints["ep-1"] = EndpointState(
            endpoint_id="ep-1", network_id="net-1",
            interface_name="eth1", host_veth="vh1234",
            cont_veth="vc5678", vlan_tag=100,
        )
        req = _make_request({"EndpointID": "ep-1", "SandboxKey": "/var/run/docker/xxx"})
        resp = _run(plugin.handle_join(req))
        body = resp.body if isinstance(resp.body, dict) else json.loads(resp.body)
        assert "InterfaceName" in body
        iface = body["InterfaceName"]
        assert iface["SrcName"] == "vc5678"
        assert iface["DstName"] == "eth1"
        # No gateway
        assert "Gateway" not in body
