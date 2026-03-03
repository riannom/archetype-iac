"""Tests for PluginVlanMixin in agent/network/plugin_vlan.py.

Covers VLAN allocation/release, hot-connect/hot-disconnect logic,
carrier state propagation, port isolation/restoration, endpoint VLAN
lookups, and edge cases (exhaustion, unallocated release, collision).
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from agent.network.docker_plugin import (
    DockerOVSPlugin,
    EndpointState,
    LabBridge,
    NetworkState,
    LINKED_VLAN_END,
    LINKED_VLAN_START,
    VLAN_RANGE_END,
    VLAN_RANGE_START,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_plugin(tmp_path) -> DockerOVSPlugin:
    """Create a DockerOVSPlugin with workspace pointed at tmp_path."""
    import agent.network.docker_plugin as dp
    orig = dp.settings.workspace_path
    dp.settings.workspace_path = str(tmp_path)
    plugin = DockerOVSPlugin()
    dp.settings.workspace_path = orig
    return plugin


def _add_lab(plugin: DockerOVSPlugin, lab_id: str = "lab1") -> LabBridge:
    """Register a lab bridge in the plugin."""
    lb = LabBridge(lab_id=lab_id, bridge_name="arch-ovs")
    plugin.lab_bridges[lab_id] = lb
    return lb


def _add_endpoint(
    plugin: DockerOVSPlugin,
    lab_id: str,
    endpoint_id: str,
    network_id: str,
    interface: str,
    host_veth: str,
    vlan_tag: int,
    container_name: str | None = None,
) -> EndpointState:
    """Register an endpoint and its network in the plugin."""
    ep = EndpointState(
        endpoint_id=endpoint_id,
        network_id=network_id,
        interface_name=interface,
        host_veth=host_veth,
        cont_veth=f"vc{endpoint_id}",
        vlan_tag=vlan_tag,
        container_name=container_name,
    )
    plugin.endpoints[endpoint_id] = ep
    if network_id not in plugin.networks:
        plugin.networks[network_id] = NetworkState(
            network_id=network_id,
            lab_id=lab_id,
            interface_name=interface,
            bridge_name="arch-ovs",
        )
    return ep


def _stub_ovs(plugin: DockerOVSPlugin, monkeypatch) -> AsyncMock:
    """Stub _ovs_vsctl and _mark_dirty_and_save on the plugin."""
    ovs_mock = AsyncMock(return_value=(0, "", ""))
    monkeypatch.setattr(plugin, "_ovs_vsctl", ovs_mock)
    monkeypatch.setattr(plugin, "_mark_dirty_and_save", AsyncMock())
    return ovs_mock


def _stub_allocators(plugin: DockerOVSPlugin, monkeypatch) -> None:
    """Stub bridge tag scanning so allocators don't hit OVS."""
    monkeypatch.setattr(
        plugin,
        "_get_used_vlan_tags_on_bridge",
        AsyncMock(return_value=set()),
    )


# =========================================================================
# VLAN Allocation and Release
# =========================================================================


class TestVlanAllocation:
    """Tests for _allocate_vlan and _release_vlan."""

    @pytest.mark.asyncio
    async def test_allocate_vlan_returns_start_of_range(self, tmp_path, monkeypatch):
        """First allocation should return VLAN_RANGE_START."""
        plugin = _make_plugin(tmp_path)
        lb = _add_lab(plugin)
        _stub_allocators(plugin, monkeypatch)

        vlan = await plugin._allocate_vlan(lb)
        assert vlan == VLAN_RANGE_START
        assert vlan in plugin._allocated_vlans

    @pytest.mark.asyncio
    async def test_allocate_vlan_increments(self, tmp_path, monkeypatch):
        """Successive allocations should produce sequential VLANs."""
        plugin = _make_plugin(tmp_path)
        lb = _add_lab(plugin)
        _stub_allocators(plugin, monkeypatch)

        v1 = await plugin._allocate_vlan(lb)
        v2 = await plugin._allocate_vlan(lb)
        assert v2 == v1 + 1

    @pytest.mark.asyncio
    async def test_allocate_vlan_skips_already_used(self, tmp_path, monkeypatch):
        """Allocator should skip VLANs already in the allocated set."""
        plugin = _make_plugin(tmp_path)
        lb = _add_lab(plugin)
        _stub_allocators(plugin, monkeypatch)

        # Pre-mark the start VLAN as allocated
        plugin._allocated_vlans.add(VLAN_RANGE_START)
        vlan = await plugin._allocate_vlan(lb)
        assert vlan == VLAN_RANGE_START + 1

    @pytest.mark.asyncio
    async def test_allocate_vlan_skips_bridge_used_tags(self, tmp_path, monkeypatch):
        """Allocator should skip VLANs already present on the OVS bridge."""
        plugin = _make_plugin(tmp_path)
        lb = _add_lab(plugin)
        # Simulate bridge already has VLAN_RANGE_START and +1 in use
        monkeypatch.setattr(
            plugin,
            "_get_used_vlan_tags_on_bridge",
            AsyncMock(return_value={VLAN_RANGE_START, VLAN_RANGE_START + 1}),
        )

        vlan = await plugin._allocate_vlan(lb)
        assert vlan == VLAN_RANGE_START + 2

    @pytest.mark.asyncio
    async def test_allocate_vlan_wraps_around(self, tmp_path, monkeypatch):
        """Allocator wraps to start when it reaches VLAN_RANGE_END."""
        plugin = _make_plugin(tmp_path)
        lb = _add_lab(plugin)
        _stub_allocators(plugin, monkeypatch)

        # Position cursor at the end of range
        plugin._global_next_vlan = VLAN_RANGE_END
        v1 = await plugin._allocate_vlan(lb)
        assert v1 == VLAN_RANGE_END

        # Next allocation should wrap to start
        v2 = await plugin._allocate_vlan(lb)
        assert v2 == VLAN_RANGE_START

    def test_release_vlan_removes_from_allocated(self, tmp_path):
        """_release_vlan should discard from both tracking sets."""
        plugin = _make_plugin(tmp_path)
        plugin._allocated_vlans.add(150)
        plugin._allocated_linked_vlans.add(150)

        plugin._release_vlan(150)
        assert 150 not in plugin._allocated_vlans
        assert 150 not in plugin._allocated_linked_vlans

    def test_release_vlan_noop_for_unallocated(self, tmp_path):
        """Releasing an unallocated VLAN should not raise."""
        plugin = _make_plugin(tmp_path)
        # Should not raise
        plugin._release_vlan(9999)
        assert 9999 not in plugin._allocated_vlans


# =========================================================================
# Linked VLAN Allocation (hot-connect range)
# =========================================================================


class TestLinkedVlanAllocation:
    """Tests for _allocate_linked_vlan and _release_linked_vlan."""

    @pytest.mark.asyncio
    async def test_allocate_linked_vlan_starts_at_linked_range(self, tmp_path, monkeypatch):
        """First linked allocation should return LINKED_VLAN_START."""
        plugin = _make_plugin(tmp_path)
        lb = _add_lab(plugin)
        _stub_allocators(plugin, monkeypatch)

        vlan = await plugin._allocate_linked_vlan(lb)
        assert vlan == LINKED_VLAN_START
        assert vlan in plugin._allocated_linked_vlans

    @pytest.mark.asyncio
    async def test_allocate_linked_vlan_skips_used(self, tmp_path, monkeypatch):
        """Linked allocator skips VLANs already allocated."""
        plugin = _make_plugin(tmp_path)
        lb = _add_lab(plugin)
        _stub_allocators(plugin, monkeypatch)

        plugin._allocated_linked_vlans.add(LINKED_VLAN_START)
        vlan = await plugin._allocate_linked_vlan(lb)
        assert vlan == LINKED_VLAN_START + 1

    @pytest.mark.asyncio
    async def test_allocate_linked_vlan_wraps_around(self, tmp_path, monkeypatch):
        """Linked allocator wraps at LINKED_VLAN_END."""
        plugin = _make_plugin(tmp_path)
        lb = _add_lab(plugin)
        _stub_allocators(plugin, monkeypatch)

        plugin._global_next_linked_vlan = LINKED_VLAN_END
        v1 = await plugin._allocate_linked_vlan(lb)
        assert v1 == LINKED_VLAN_END

        v2 = await plugin._allocate_linked_vlan(lb)
        assert v2 == LINKED_VLAN_START

    def test_release_linked_vlan_removes_from_both_sets(self, tmp_path):
        """_release_linked_vlan discards from both tracking sets."""
        plugin = _make_plugin(tmp_path)
        plugin._allocated_linked_vlans.add(2100)
        plugin._allocated_vlans.add(2100)

        plugin._release_linked_vlan(2100)
        assert 2100 not in plugin._allocated_linked_vlans
        assert 2100 not in plugin._allocated_vlans

    def test_release_linked_vlan_noop_for_unallocated(self, tmp_path):
        """Releasing an unallocated linked VLAN should not raise."""
        plugin = _make_plugin(tmp_path)
        plugin._release_linked_vlan(8888)
        assert 8888 not in plugin._allocated_linked_vlans


# =========================================================================
# VLAN Exhaustion
# =========================================================================


class TestVlanExhaustion:
    """Tests for VLAN exhaustion edge cases."""

    @pytest.mark.asyncio
    async def test_isolated_range_exhausted_falls_back_to_linked(self, tmp_path, monkeypatch):
        """When isolated range is full, allocator falls back to linked range."""
        plugin = _make_plugin(tmp_path)
        lb = _add_lab(plugin)
        _stub_allocators(plugin, monkeypatch)

        # Fill entire isolated range
        for v in range(VLAN_RANGE_START, VLAN_RANGE_END + 1):
            plugin._allocated_vlans.add(v)

        vlan = await plugin._allocate_vlan(lb)
        assert LINKED_VLAN_START <= vlan <= LINKED_VLAN_END

    @pytest.mark.asyncio
    async def test_linked_range_exhausted_falls_back_to_isolated(self, tmp_path, monkeypatch):
        """When linked range is full, allocator falls back to isolated range."""
        plugin = _make_plugin(tmp_path)
        lb = _add_lab(plugin)
        _stub_allocators(plugin, monkeypatch)

        # Fill entire linked range
        for v in range(LINKED_VLAN_START, LINKED_VLAN_END + 1):
            plugin._allocated_linked_vlans.add(v)

        vlan = await plugin._allocate_linked_vlan(lb)
        assert VLAN_RANGE_START <= vlan <= VLAN_RANGE_END

    @pytest.mark.asyncio
    async def test_both_ranges_exhausted_raises(self, tmp_path, monkeypatch):
        """When both ranges are full, allocation should raise RuntimeError."""
        plugin = _make_plugin(tmp_path)
        lb = _add_lab(plugin)
        _stub_allocators(plugin, monkeypatch)

        # Fill both ranges
        for v in range(VLAN_RANGE_START, VLAN_RANGE_END + 1):
            plugin._allocated_vlans.add(v)
        for v in range(LINKED_VLAN_START, LINKED_VLAN_END + 1):
            plugin._allocated_linked_vlans.add(v)

        with pytest.raises(RuntimeError, match="No available VLAN tags"):
            await plugin._allocate_vlan(lb)


# =========================================================================
# Hot-Connect
# =========================================================================


class TestHotConnect:
    """Tests for hot_connect VLAN matching logic."""

    @pytest.mark.asyncio
    async def test_hot_connect_assigns_shared_vlan(self, tmp_path, monkeypatch):
        """hot_connect should set both endpoints to the same linked VLAN."""
        plugin = _make_plugin(tmp_path)
        _add_lab(plugin)
        _stub_ovs(plugin, monkeypatch)
        _stub_allocators(plugin, monkeypatch)

        ep_a = _add_endpoint(plugin, "lab1", "ep-a", "net-a", "eth1", "vhA", 100, "r1")
        ep_b = _add_endpoint(plugin, "lab1", "ep-b", "net-b", "eth1", "vhB", 101, "r2")
        monkeypatch.setattr(plugin, "_validate_endpoint_exists", AsyncMock(return_value=True))

        shared_vlan = await plugin.hot_connect("lab1", "r1", "eth1", "r2", "eth1")

        assert shared_vlan is not None
        assert LINKED_VLAN_START <= shared_vlan <= LINKED_VLAN_END
        assert ep_a.vlan_tag == shared_vlan
        assert ep_b.vlan_tag == shared_vlan

    @pytest.mark.asyncio
    async def test_hot_connect_releases_old_vlans(self, tmp_path, monkeypatch):
        """hot_connect should release the old isolated VLANs of both endpoints."""
        plugin = _make_plugin(tmp_path)
        _add_lab(plugin)
        _stub_ovs(plugin, monkeypatch)
        _stub_allocators(plugin, monkeypatch)

        old_a, old_b = 100, 101
        plugin._allocated_vlans.add(old_a)
        plugin._allocated_vlans.add(old_b)

        _add_endpoint(plugin, "lab1", "ep-a", "net-a", "eth1", "vhA", old_a, "r1")
        _add_endpoint(plugin, "lab1", "ep-b", "net-b", "eth1", "vhB", old_b, "r2")
        monkeypatch.setattr(plugin, "_validate_endpoint_exists", AsyncMock(return_value=True))

        await plugin.hot_connect("lab1", "r1", "eth1", "r2", "eth1")

        # Old VLANs should be released
        assert old_a not in plugin._allocated_vlans
        assert old_b not in plugin._allocated_vlans

    @pytest.mark.asyncio
    async def test_hot_connect_no_lab_bridge_returns_none(self, tmp_path, monkeypatch):
        """hot_connect should return None when lab bridge is missing."""
        plugin = _make_plugin(tmp_path)
        _stub_ovs(plugin, monkeypatch)

        result = await plugin.hot_connect("no-lab", "r1", "eth1", "r2", "eth1")
        assert result is None

    @pytest.mark.asyncio
    async def test_hot_connect_endpoint_not_found_returns_none(self, tmp_path, monkeypatch):
        """hot_connect should return None when endpoints cannot be found."""
        plugin = _make_plugin(tmp_path)
        _add_lab(plugin)
        _stub_ovs(plugin, monkeypatch)
        # No endpoints registered, and discover returns None
        monkeypatch.setattr(plugin, "_discover_endpoint", AsyncMock(return_value=None))

        result = await plugin.hot_connect("lab1", "r1", "eth1", "r2", "eth1")
        assert result is None

    @pytest.mark.asyncio
    async def test_hot_connect_ovs_fail_on_port_a_returns_none(self, tmp_path, monkeypatch):
        """hot_connect should return None and release VLAN if OVS set fails on port A."""
        plugin = _make_plugin(tmp_path)
        _add_lab(plugin)
        _stub_allocators(plugin, monkeypatch)
        monkeypatch.setattr(plugin, "_mark_dirty_and_save", AsyncMock())

        _add_endpoint(plugin, "lab1", "ep-a", "net-a", "eth1", "vhA", 100, "r1")
        _add_endpoint(plugin, "lab1", "ep-b", "net-b", "eth1", "vhB", 101, "r2")
        monkeypatch.setattr(plugin, "_validate_endpoint_exists", AsyncMock(return_value=True))

        # First OVS set fails
        monkeypatch.setattr(
            plugin,
            "_ovs_vsctl",
            AsyncMock(return_value=(1, "", "OVS error")),
        )

        result = await plugin.hot_connect("lab1", "r1", "eth1", "r2", "eth1")
        assert result is None

    @pytest.mark.asyncio
    async def test_hot_connect_validates_endpoints_exist_on_ovs(self, tmp_path, monkeypatch):
        """hot_connect should return None if endpoint validation fails."""
        plugin = _make_plugin(tmp_path)
        _add_lab(plugin)
        _stub_ovs(plugin, monkeypatch)
        _stub_allocators(plugin, monkeypatch)

        _add_endpoint(plugin, "lab1", "ep-a", "net-a", "eth1", "vhA", 100, "r1")
        _add_endpoint(plugin, "lab1", "ep-b", "net-b", "eth1", "vhB", 101, "r2")

        # First endpoint exists, second does not
        validate = AsyncMock(side_effect=[True, False])
        monkeypatch.setattr(plugin, "_validate_endpoint_exists", validate)

        result = await plugin.hot_connect("lab1", "r1", "eth1", "r2", "eth1")
        assert result is None


# =========================================================================
# Hot-Disconnect
# =========================================================================


class TestHotDisconnect:
    """Tests for hot_disconnect logic."""

    @pytest.mark.asyncio
    async def test_hot_disconnect_assigns_unique_vlan(self, tmp_path, monkeypatch):
        """hot_disconnect should assign a new unique VLAN to the endpoint."""
        plugin = _make_plugin(tmp_path)
        _add_lab(plugin)
        _stub_ovs(plugin, monkeypatch)
        _stub_allocators(plugin, monkeypatch)

        ep = _add_endpoint(plugin, "lab1", "ep-a", "net-a", "eth1", "vhA", 2050, "r1")

        new_vlan = await plugin.hot_disconnect("lab1", "r1", "eth1")

        assert new_vlan is not None
        assert new_vlan == VLAN_RANGE_START
        assert ep.vlan_tag == new_vlan

    @pytest.mark.asyncio
    async def test_hot_disconnect_no_lab_bridge_returns_none(self, tmp_path, monkeypatch):
        """hot_disconnect returns None when lab bridge is missing."""
        plugin = _make_plugin(tmp_path)
        _stub_ovs(plugin, monkeypatch)

        result = await plugin.hot_disconnect("no-lab", "r1", "eth1")
        assert result is None

    @pytest.mark.asyncio
    async def test_hot_disconnect_endpoint_not_found_returns_none(self, tmp_path, monkeypatch):
        """hot_disconnect returns None when endpoint not found."""
        plugin = _make_plugin(tmp_path)
        _add_lab(plugin)
        _stub_ovs(plugin, monkeypatch)

        result = await plugin.hot_disconnect("lab1", "r1", "eth1")
        assert result is None


# =========================================================================
# Carrier State
# =========================================================================


class TestCarrierState:
    """Tests for set_carrier_state."""

    @pytest.mark.asyncio
    async def test_set_carrier_on(self, tmp_path, monkeypatch):
        """set_carrier_state('on') should run nsenter with carrier on."""
        plugin = _make_plugin(tmp_path)
        monkeypatch.setattr(plugin, "_get_container_pid", AsyncMock(return_value=1234))
        run_cmd = AsyncMock(return_value=(0, "", ""))
        monkeypatch.setattr(plugin, "_run_cmd", run_cmd)

        result = await plugin.set_carrier_state("lab1", "r1", "eth1", "on")

        assert result is True
        cmd = run_cmd.call_args[0][0]
        assert cmd == ["nsenter", "-t", "1234", "-n", "ip", "link", "set", "eth1", "carrier", "on"]

    @pytest.mark.asyncio
    async def test_set_carrier_off(self, tmp_path, monkeypatch):
        """set_carrier_state('off') should run nsenter with carrier off."""
        plugin = _make_plugin(tmp_path)
        monkeypatch.setattr(plugin, "_get_container_pid", AsyncMock(return_value=5678))
        run_cmd = AsyncMock(return_value=(0, "", ""))
        monkeypatch.setattr(plugin, "_run_cmd", run_cmd)

        result = await plugin.set_carrier_state("lab1", "r1", "eth1", "off")

        assert result is True
        cmd = run_cmd.call_args[0][0]
        assert "off" in cmd

    @pytest.mark.asyncio
    async def test_set_carrier_invalid_state_rejected(self, tmp_path):
        """set_carrier_state should reject invalid state values."""
        plugin = _make_plugin(tmp_path)

        result = await plugin.set_carrier_state("lab1", "r1", "eth1", "invalid")
        assert result is False

    @pytest.mark.asyncio
    async def test_set_carrier_no_pid_returns_false(self, tmp_path, monkeypatch):
        """set_carrier_state should return False if container PID not found."""
        plugin = _make_plugin(tmp_path)
        monkeypatch.setattr(plugin, "_get_container_pid", AsyncMock(return_value=None))

        result = await plugin.set_carrier_state("lab1", "r1", "eth1", "on")
        assert result is False

    @pytest.mark.asyncio
    async def test_set_carrier_nsenter_failure(self, tmp_path, monkeypatch):
        """set_carrier_state should return False when nsenter command fails."""
        plugin = _make_plugin(tmp_path)
        monkeypatch.setattr(plugin, "_get_container_pid", AsyncMock(return_value=1234))
        monkeypatch.setattr(plugin, "_run_cmd", AsyncMock(return_value=(1, "", "Device not found")))

        result = await plugin.set_carrier_state("lab1", "r1", "eth1", "on")
        assert result is False


# =========================================================================
# Isolate / Restore Port
# =========================================================================


class TestIsolatePort:
    """Tests for isolate_port (VLAN disconnect + carrier off)."""

    @pytest.mark.asyncio
    async def test_isolate_port_disconnects_and_sets_carrier_off(self, tmp_path, monkeypatch):
        """isolate_port should hot_disconnect and set carrier off."""
        plugin = _make_plugin(tmp_path)
        _add_lab(plugin)
        _stub_ovs(plugin, monkeypatch)
        _stub_allocators(plugin, monkeypatch)

        ep = _add_endpoint(plugin, "lab1", "ep-a", "net-a", "eth1", "vhA", 2050, "r1")
        monkeypatch.setattr(plugin, "_get_container_pid", AsyncMock(return_value=1234))
        monkeypatch.setattr(plugin, "_run_cmd", AsyncMock(return_value=(0, "", "")))

        new_vlan = await plugin.isolate_port("lab1", "r1", "eth1")

        assert new_vlan is not None
        assert ep.vlan_tag == new_vlan

    @pytest.mark.asyncio
    async def test_isolate_port_returns_vlan_even_if_carrier_fails(self, tmp_path, monkeypatch):
        """isolate_port should return VLAN even if carrier off fails."""
        plugin = _make_plugin(tmp_path)
        _add_lab(plugin)
        _stub_ovs(plugin, monkeypatch)
        _stub_allocators(plugin, monkeypatch)

        _add_endpoint(plugin, "lab1", "ep-a", "net-a", "eth1", "vhA", 2050, "r1")
        # Carrier state fails
        monkeypatch.setattr(
            plugin, "set_carrier_state", AsyncMock(return_value=False)
        )

        new_vlan = await plugin.isolate_port("lab1", "r1", "eth1")
        # VLAN isolation still succeeds
        assert new_vlan is not None

    @pytest.mark.asyncio
    async def test_isolate_port_hot_disconnect_fails(self, tmp_path, monkeypatch):
        """isolate_port should return None when hot_disconnect fails."""
        plugin = _make_plugin(tmp_path)
        # No lab bridge -> hot_disconnect returns None
        _stub_ovs(plugin, monkeypatch)

        result = await plugin.isolate_port("no-lab", "r1", "eth1")
        assert result is None


class TestRestorePort:
    """Tests for restore_port (VLAN reconnect + carrier on)."""

    @pytest.mark.asyncio
    async def test_restore_port_sets_vlan_and_carrier(self, tmp_path, monkeypatch):
        """restore_port should set the target VLAN and carrier on."""
        plugin = _make_plugin(tmp_path)
        _add_lab(plugin)
        _stub_ovs(plugin, monkeypatch)

        ep = _add_endpoint(plugin, "lab1", "ep-a", "net-a", "eth1", "vhA", 999, "r1")
        monkeypatch.setattr(plugin, "_get_container_pid", AsyncMock(return_value=1234))
        monkeypatch.setattr(plugin, "_run_cmd", AsyncMock(return_value=(0, "", "")))

        target_vlan = 2050
        result = await plugin.restore_port("lab1", "r1", "eth1", target_vlan)

        assert result is True
        assert ep.vlan_tag == target_vlan

    @pytest.mark.asyncio
    async def test_restore_port_no_lab_bridge(self, tmp_path, monkeypatch):
        """restore_port should return False when lab bridge missing."""
        plugin = _make_plugin(tmp_path)
        _stub_ovs(plugin, monkeypatch)

        result = await plugin.restore_port("no-lab", "r1", "eth1", 2050)
        assert result is False

    @pytest.mark.asyncio
    async def test_restore_port_endpoint_not_found(self, tmp_path, monkeypatch):
        """restore_port should return False when endpoint not found."""
        plugin = _make_plugin(tmp_path)
        _add_lab(plugin)
        _stub_ovs(plugin, monkeypatch)

        result = await plugin.restore_port("lab1", "r1", "eth1", 2050)
        assert result is False

    @pytest.mark.asyncio
    async def test_restore_port_carrier_on_fails(self, tmp_path, monkeypatch):
        """restore_port should return False when carrier on fails."""
        plugin = _make_plugin(tmp_path)
        _add_lab(plugin)
        _stub_ovs(plugin, monkeypatch)

        _add_endpoint(plugin, "lab1", "ep-a", "net-a", "eth1", "vhA", 999, "r1")
        monkeypatch.setattr(plugin, "set_carrier_state", AsyncMock(return_value=False))

        result = await plugin.restore_port("lab1", "r1", "eth1", 2050)
        assert result is False


# =========================================================================
# Endpoint VLAN Getters / Setters
# =========================================================================


class TestGetEndpointVlan:
    """Tests for get_endpoint_vlan."""

    @pytest.mark.asyncio
    async def test_get_endpoint_vlan_from_memory(self, tmp_path, monkeypatch):
        """get_endpoint_vlan should return in-memory VLAN tag."""
        plugin = _make_plugin(tmp_path)
        _add_lab(plugin)
        _add_endpoint(plugin, "lab1", "ep-a", "net-a", "eth1", "vhA", 150, "r1")
        # Stub discover to avoid Docker calls
        monkeypatch.setattr(plugin, "_discover_endpoint", AsyncMock(return_value=None))

        vlan = await plugin.get_endpoint_vlan("lab1", "r1", "eth1")
        assert vlan == 150

    @pytest.mark.asyncio
    async def test_get_endpoint_vlan_from_ovs(self, tmp_path, monkeypatch):
        """get_endpoint_vlan with read_from_ovs should query OVS."""
        plugin = _make_plugin(tmp_path)
        _add_lab(plugin)
        _add_endpoint(plugin, "lab1", "ep-a", "net-a", "eth1", "vhA", 150, "r1")
        monkeypatch.setattr(plugin, "_run_cmd", AsyncMock(return_value=(0, "200\n", "")))

        vlan = await plugin.get_endpoint_vlan("lab1", "r1", "eth1", read_from_ovs=True)
        assert vlan == 200

    @pytest.mark.asyncio
    async def test_get_endpoint_vlan_not_found(self, tmp_path, monkeypatch):
        """get_endpoint_vlan should return None for unknown endpoint."""
        plugin = _make_plugin(tmp_path)
        _add_lab(plugin)
        monkeypatch.setattr(plugin, "_discover_endpoint", AsyncMock(return_value=None))

        vlan = await plugin.get_endpoint_vlan("lab1", "r1", "eth1")
        assert vlan is None


class TestSetEndpointVlan:
    """Tests for set_endpoint_vlan."""

    @pytest.mark.asyncio
    async def test_set_endpoint_vlan_updates_in_memory(self, tmp_path, monkeypatch):
        """set_endpoint_vlan should update the in-memory VLAN tag."""
        plugin = _make_plugin(tmp_path)
        _add_lab(plugin)
        _stub_ovs(plugin, monkeypatch)
        ep = _add_endpoint(plugin, "lab1", "ep-a", "net-a", "eth1", "vhA", 100, "r1")

        result = await plugin.set_endpoint_vlan("lab1", "r1", "eth1", 250)
        assert result is True
        assert ep.vlan_tag == 250

    @pytest.mark.asyncio
    async def test_set_endpoint_vlan_not_found(self, tmp_path, monkeypatch):
        """set_endpoint_vlan should return False when endpoint not found."""
        plugin = _make_plugin(tmp_path)
        _add_lab(plugin)
        monkeypatch.setattr(plugin, "_discover_endpoint", AsyncMock(return_value=None))

        result = await plugin.set_endpoint_vlan("lab1", "r1", "eth1", 250)
        assert result is False


class TestSetEndpointVlanByHostVeth:
    """Tests for set_endpoint_vlan_by_host_veth."""

    @pytest.mark.asyncio
    async def test_update_vlan_by_host_veth(self, tmp_path, monkeypatch):
        """Should update VLAN and release old tag."""
        plugin = _make_plugin(tmp_path)
        _add_lab(plugin)
        _stub_ovs(plugin, monkeypatch)
        ep = _add_endpoint(plugin, "lab1", "ep-a", "net-a", "eth1", "vhA", 150, "r1")
        plugin._allocated_vlans.add(150)

        result = await plugin.set_endpoint_vlan_by_host_veth("lab1", "vhA", 2060)
        assert result is True
        assert ep.vlan_tag == 2060
        assert 150 not in plugin._allocated_vlans
        assert 2060 in plugin._allocated_linked_vlans

    @pytest.mark.asyncio
    async def test_update_vlan_by_host_veth_noop_if_same(self, tmp_path, monkeypatch):
        """Should return True without changes if VLAN is already correct."""
        plugin = _make_plugin(tmp_path)
        _add_lab(plugin)
        _stub_ovs(plugin, monkeypatch)
        _add_endpoint(plugin, "lab1", "ep-a", "net-a", "eth1", "vhA", 2060, "r1")

        result = await plugin.set_endpoint_vlan_by_host_veth("lab1", "vhA", 2060)
        assert result is True

    @pytest.mark.asyncio
    async def test_update_vlan_by_host_veth_not_found(self, tmp_path, monkeypatch):
        """Should return False when no endpoint matches host veth."""
        plugin = _make_plugin(tmp_path)
        _add_lab(plugin)
        _stub_ovs(plugin, monkeypatch)

        result = await plugin.set_endpoint_vlan_by_host_veth("lab1", "vhNONE", 100)
        assert result is False


# =========================================================================
# Utility Methods
# =========================================================================


class TestUtilityMethods:
    """Tests for get_container_interface_mapping, get_lab_status, etc."""

    def test_get_container_interface_mapping(self, tmp_path):
        """Should return host_veth -> interface_name mapping."""
        plugin = _make_plugin(tmp_path)
        _add_lab(plugin)
        _add_endpoint(plugin, "lab1", "ep-a", "net-a", "eth1", "vhA", 100, "r1")
        _add_endpoint(plugin, "lab1", "ep-b", "net-b", "eth2", "vhB", 101, "r1")

        mapping = plugin.get_container_interface_mapping("lab1", "r1")
        assert mapping == {"vhA": "eth1", "vhB": "eth2"}

    def test_get_container_interface_mapping_empty(self, tmp_path):
        """Should return empty dict for unknown lab."""
        plugin = _make_plugin(tmp_path)
        mapping = plugin.get_container_interface_mapping("no-lab", "r1")
        assert mapping == {}

    def test_get_lab_status(self, tmp_path):
        """Should return lab status with networks and endpoints."""
        plugin = _make_plugin(tmp_path)
        lb = _add_lab(plugin)
        lb.network_ids.add("net-a")
        _add_endpoint(plugin, "lab1", "ep-a", "net-a", "eth1", "vhA", 100, "r1")

        status = plugin.get_lab_status("lab1")
        assert status is not None
        assert status["lab_id"] == "lab1"
        assert len(status["networks"]) == 1
        assert len(status["endpoints"]) == 1
        assert status["endpoints"][0]["vlan"] == 100

    def test_get_lab_status_missing(self, tmp_path):
        """Should return None for unknown lab."""
        plugin = _make_plugin(tmp_path)
        assert plugin.get_lab_status("no-lab") is None

    def test_get_all_labs(self, tmp_path):
        """Should return list of all lab IDs."""
        plugin = _make_plugin(tmp_path)
        _add_lab(plugin, "lab1")
        _add_lab(plugin, "lab2")
        assert sorted(plugin.get_all_labs()) == ["lab1", "lab2"]

    def test_get_active_host_veths(self, tmp_path):
        """Should return set of all tracked host veth names."""
        plugin = _make_plugin(tmp_path)
        _add_lab(plugin)
        _add_endpoint(plugin, "lab1", "ep-a", "net-a", "eth1", "vhA", 100, "r1")
        _add_endpoint(plugin, "lab1", "ep-b", "net-b", "eth2", "vhB", 101, "r2")

        veths = plugin.get_active_host_veths()
        assert veths == {"vhA", "vhB"}


# =========================================================================
# Prune Stale Endpoints
# =========================================================================


class TestPruneStaleEndpoints:
    """Tests for _prune_stale_container_endpoints."""

    @pytest.mark.asyncio
    async def test_prune_removes_matching_container_endpoints(self, tmp_path, monkeypatch):
        """Should remove endpoints belonging to the named container."""
        plugin = _make_plugin(tmp_path)
        _add_lab(plugin)
        _stub_ovs(plugin, monkeypatch)
        _add_endpoint(plugin, "lab1", "ep-a", "net-a", "eth1", "vhA", 100, "r1")
        _add_endpoint(plugin, "lab1", "ep-b", "net-b", "eth2", "vhB", 101, "r1")
        _add_endpoint(plugin, "lab1", "ep-c", "net-c", "eth1", "vhC", 102, "r2")

        pruned = await plugin._prune_stale_container_endpoints("lab1", "r1")
        assert pruned == 2
        assert "ep-a" not in plugin.endpoints
        assert "ep-b" not in plugin.endpoints
        assert "ep-c" in plugin.endpoints  # different container

    @pytest.mark.asyncio
    async def test_prune_skips_different_lab(self, tmp_path, monkeypatch):
        """Should not prune endpoints from a different lab."""
        plugin = _make_plugin(tmp_path)
        _add_lab(plugin, "lab1")
        _add_lab(plugin, "lab2")
        _stub_ovs(plugin, monkeypatch)

        _add_endpoint(plugin, "lab2", "ep-a", "net-a-lab2", "eth1", "vhA", 100, "r1")

        pruned = await plugin._prune_stale_container_endpoints("lab1", "r1")
        assert pruned == 0
        assert "ep-a" in plugin.endpoints


# =========================================================================
# set_endpoint_container_name
# =========================================================================


class TestSetEndpointContainerName:
    """Tests for set_endpoint_container_name."""

    @pytest.mark.asyncio
    async def test_associates_container_name(self, tmp_path, monkeypatch):
        """Should set container_name on the endpoint."""
        plugin = _make_plugin(tmp_path)
        _add_lab(plugin)
        _stub_ovs(plugin, monkeypatch)
        ep = _add_endpoint(plugin, "lab1", "ep-a", "net-a", "eth1", "vhA", 100)
        assert ep.container_name is None

        await plugin.set_endpoint_container_name("ep-a", "r1")
        assert ep.container_name == "r1"

    @pytest.mark.asyncio
    async def test_noop_for_unknown_endpoint(self, tmp_path, monkeypatch):
        """Should do nothing for unknown endpoint ID."""
        plugin = _make_plugin(tmp_path)
        _stub_ovs(plugin, monkeypatch)
        # Should not raise
        await plugin.set_endpoint_container_name("ep-unknown", "r1")
