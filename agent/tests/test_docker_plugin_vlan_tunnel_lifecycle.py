"""Tests for DockerOVSPlugin VLAN allocation, VXLAN tunnel lifecycle,
bridge management, stale port cleanup, and endpoint validation.

Covers functional areas not tested by existing carrier/collision tests:
- VLAN allocation from isolated and linked ranges (with collision avoidance)
- VXLAN tunnel create/delete on plugin bridges
- Lab bridge ensure/cleanup lifecycle
- TTL activity tracking
- Veth name generation determinism
- Endpoint existence validation via OVS port-to-br
- Stale port cleanup identifying container ports
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.network.docker_plugin import (
    DockerOVSPlugin,
    EndpointState,
    LabBridge,
    VLAN_RANGE_START,
    VLAN_RANGE_END,
    LINKED_VLAN_START,
)


def _make_plugin() -> DockerOVSPlugin:
    """Create a DockerOVSPlugin suitable for unit testing."""
    plugin = DockerOVSPlugin()
    plugin._allocated_vlans = set()
    plugin._allocated_linked_vlans = set()
    plugin._global_next_vlan = VLAN_RANGE_START
    plugin._global_next_linked_vlan = LINKED_VLAN_START
    return plugin


class TestVlanAllocationIsolated:
    """Tests for VLAN allocation from isolated range (100-2049)."""

    @pytest.mark.asyncio
    async def test_allocate_first_available(self, monkeypatch):
        plugin = _make_plugin()
        monkeypatch.setattr(
            plugin, "_get_used_vlan_tags_on_bridge",
            AsyncMock(return_value=set()),
        )
        lb = MagicMock(lab_id="lab1")
        vlan = await plugin._allocate_vlan(lb)
        assert vlan == VLAN_RANGE_START
        assert vlan in plugin._allocated_vlans

    @pytest.mark.asyncio
    async def test_allocate_skips_bridge_used(self, monkeypatch):
        plugin = _make_plugin()
        plugin._global_next_vlan = 100
        monkeypatch.setattr(
            plugin, "_get_used_vlan_tags_on_bridge",
            AsyncMock(return_value={100, 101, 102}),
        )
        lb = MagicMock(lab_id="lab1")
        vlan = await plugin._allocate_vlan(lb)
        assert vlan == 103

    @pytest.mark.asyncio
    async def test_allocate_skips_internally_tracked(self, monkeypatch):
        plugin = _make_plugin()
        plugin._global_next_vlan = 100
        plugin._allocated_vlans = {100, 101}
        monkeypatch.setattr(
            plugin, "_get_used_vlan_tags_on_bridge",
            AsyncMock(return_value=set()),
        )
        lb = MagicMock(lab_id="lab1")
        vlan = await plugin._allocate_vlan(lb)
        assert vlan == 102

    @pytest.mark.asyncio
    async def test_allocate_wraps_at_range_end(self, monkeypatch):
        plugin = _make_plugin()
        plugin._global_next_vlan = VLAN_RANGE_END
        monkeypatch.setattr(
            plugin, "_get_used_vlan_tags_on_bridge",
            AsyncMock(return_value=set()),
        )
        lb = MagicMock(lab_id="lab1")
        vlan = await plugin._allocate_vlan(lb)
        assert vlan == VLAN_RANGE_END
        # Next allocation should wrap to start
        assert plugin._global_next_vlan == VLAN_RANGE_START


class TestVlanAllocationLinked:
    """Tests for VLAN allocation from linked range (2050-4000)."""

    @pytest.mark.asyncio
    async def test_allocate_linked_first_available(self, monkeypatch):
        plugin = _make_plugin()
        monkeypatch.setattr(
            plugin, "_get_used_vlan_tags_on_bridge",
            AsyncMock(return_value=set()),
        )
        lb = MagicMock(lab_id="lab1")
        vlan = await plugin._allocate_linked_vlan(lb)
        assert vlan == LINKED_VLAN_START
        assert vlan in plugin._allocated_linked_vlans

    @pytest.mark.asyncio
    async def test_allocate_linked_skips_used(self, monkeypatch):
        plugin = _make_plugin()
        plugin._global_next_linked_vlan = LINKED_VLAN_START
        monkeypatch.setattr(
            plugin, "_get_used_vlan_tags_on_bridge",
            AsyncMock(return_value={LINKED_VLAN_START}),
        )
        lb = MagicMock(lab_id="lab1")
        vlan = await plugin._allocate_linked_vlan(lb)
        assert vlan == LINKED_VLAN_START + 1


class TestVlanRelease:
    """Tests for VLAN release."""

    def test_release_vlan_from_isolated(self):
        plugin = _make_plugin()
        plugin._allocated_vlans = {100, 200}
        plugin._release_vlan(100)
        assert 100 not in plugin._allocated_vlans

    def test_release_vlan_from_both_sets(self):
        plugin = _make_plugin()
        plugin._allocated_vlans = {200}
        plugin._allocated_linked_vlans = {200}
        plugin._release_vlan(200)
        assert 200 not in plugin._allocated_vlans
        assert 200 not in plugin._allocated_linked_vlans

    def test_release_linked_vlan(self):
        plugin = _make_plugin()
        plugin._allocated_linked_vlans = {2050, 2051}
        plugin._release_linked_vlan(2050)
        assert 2050 not in plugin._allocated_linked_vlans

    def test_release_nonexistent_no_error(self):
        plugin = _make_plugin()
        plugin._release_vlan(9999)
        plugin._release_linked_vlan(9999)


class TestCleanupStaleOvsPorts:
    """Tests for _cleanup_stale_ovs_ports after container restart."""

    @pytest.mark.asyncio
    async def test_empty_bridge(self):
        plugin = _make_plugin()
        plugin.endpoints = {}
        plugin._ovs_vsctl = AsyncMock(return_value=(0, "", ""))
        plugin._run_cmd = AsyncMock(return_value=(0, "", ""))
        result = await plugin._cleanup_stale_ovs_ports("archetype-lab1-ceos1")
        assert result == 0

    @pytest.mark.asyncio
    async def test_tracked_port_preserved(self):
        plugin = _make_plugin()
        ep = EndpointState(
            endpoint_id="ep1", network_id="net1", interface_name="eth1",
            host_veth="vh-tracked", cont_veth="vc-001", vlan_tag=100,
            container_name="archetype-lab1-ceos1",
        )
        plugin.endpoints = {"ep1": ep}

        async def fake_ovs_vsctl(*args):
            if args[0] == "list-ports":
                return (0, "vh-tracked\nvh-stale", "")
            if args[0] == "get" and args[2] == "vh-stale":
                return (0, '{container_name="archetype-lab1-ceos1"}', "")
            if args[0] == "--if-exists":
                return (0, "", "")
            return (0, "", "")

        plugin._ovs_vsctl = fake_ovs_vsctl
        plugin._run_cmd = AsyncMock(return_value=(0, "", ""))

        result = await plugin._cleanup_stale_ovs_ports("archetype-lab1-ceos1")
        assert result == 1

    @pytest.mark.asyncio
    async def test_different_container_preserved(self):
        plugin = _make_plugin()
        plugin.endpoints = {}

        async def fake_ovs_vsctl(*args):
            if args[0] == "list-ports":
                return (0, "vh-other", "")
            if args[0] == "get" and args[2] == "vh-other":
                return (0, '{container_name="archetype-lab1-ceos2"}', "")
            return (0, "", "")

        plugin._ovs_vsctl = fake_ovs_vsctl
        plugin._run_cmd = AsyncMock(return_value=(0, "", ""))

        result = await plugin._cleanup_stale_ovs_ports("archetype-lab1-ceos1")
        assert result == 0

    @pytest.mark.asyncio
    async def test_ovs_list_failure(self):
        plugin = _make_plugin()
        plugin.endpoints = {}
        plugin._ovs_vsctl = AsyncMock(return_value=(1, "", "error"))
        result = await plugin._cleanup_stale_ovs_ports("archetype-lab1-ceos1")
        assert result == 0


class TestVxlanTunnelLifecycle:
    """Tests for VXLAN tunnel creation and deletion on plugin bridges."""

    @pytest.mark.asyncio
    async def test_create_tunnel(self, monkeypatch):
        plugin = _make_plugin()
        lb = LabBridge(lab_id="lab1", bridge_name="arch-ovs")
        plugin.lab_bridges["lab1"] = lb
        plugin._run_cmd = AsyncMock(return_value=(0, "", ""))
        plugin._ovs_vsctl = AsyncMock(return_value=(0, "", ""))

        async def noop():
            pass
        monkeypatch.setattr(plugin, "_mark_dirty_and_save", noop)

        port = await plugin.create_vxlan_tunnel(
            lab_id="lab1", link_id="r1:eth1-r2:eth1",
            local_ip="10.0.0.1", remote_ip="10.0.0.2",
            vni=50000, vlan_tag=200,
        )
        assert port == "vx50000"
        assert 50000 in lb.vxlan_tunnels

    @pytest.mark.asyncio
    async def test_create_tunnel_idempotent(self, monkeypatch):
        plugin = _make_plugin()
        lb = LabBridge(lab_id="lab1", bridge_name="arch-ovs")
        lb.vxlan_tunnels[50000] = "vx50000"
        plugin.lab_bridges["lab1"] = lb

        port = await plugin.create_vxlan_tunnel(
            lab_id="lab1", link_id="r1:eth1-r2:eth1",
            local_ip="10.0.0.1", remote_ip="10.0.0.2",
            vni=50000, vlan_tag=200,
        )
        assert port == "vx50000"

    @pytest.mark.asyncio
    async def test_create_tunnel_no_lab_bridge(self):
        plugin = _make_plugin()
        with pytest.raises(ValueError, match="Lab bridge not found"):
            await plugin.create_vxlan_tunnel(
                lab_id="nonexistent", link_id="r1:eth1-r2:eth1",
                local_ip="10.0.0.1", remote_ip="10.0.0.2",
                vni=50000, vlan_tag=200,
            )

    @pytest.mark.asyncio
    async def test_delete_tunnel(self, monkeypatch):
        plugin = _make_plugin()
        lb = LabBridge(lab_id="lab1", bridge_name="arch-ovs")
        lb.vxlan_tunnels[50000] = "vx50000"
        plugin.lab_bridges["lab1"] = lb
        plugin._ovs_vsctl = AsyncMock(return_value=(0, "", ""))
        plugin._run_cmd = AsyncMock(return_value=(0, "", ""))

        async def noop():
            pass
        monkeypatch.setattr(plugin, "_mark_dirty_and_save", noop)

        result = await plugin.delete_vxlan_tunnel("lab1", 50000)
        assert result is True
        assert 50000 not in lb.vxlan_tunnels

    @pytest.mark.asyncio
    async def test_delete_tunnel_not_found(self):
        plugin = _make_plugin()
        plugin.lab_bridges["lab1"] = LabBridge(lab_id="lab1", bridge_name="arch-ovs")
        result = await plugin.delete_vxlan_tunnel("lab1", 99999)
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_tunnel_no_lab(self):
        plugin = _make_plugin()
        result = await plugin.delete_vxlan_tunnel("nonexistent", 50000)
        assert result is False


class TestBridgeLifecycle:
    """Tests for lab bridge creation and reuse."""

    @pytest.mark.asyncio
    async def test_ensure_bridge_creates_new(self, monkeypatch):
        plugin = _make_plugin()
        plugin._ovs_vsctl = AsyncMock(return_value=(0, "", ""))
        plugin._run_cmd = AsyncMock(return_value=(0, "", ""))

        import agent.network.docker_plugin as dp
        monkeypatch.setattr(dp.settings, "ovs_bridge_name", "arch-ovs", raising=False)

        async def noop():
            pass
        monkeypatch.setattr(plugin, "_mark_dirty_and_save", noop)

        bridge = await plugin._ensure_bridge("new-lab")
        assert bridge.lab_id == "new-lab"
        assert "new-lab" in plugin.lab_bridges

    @pytest.mark.asyncio
    async def test_ensure_bridge_returns_existing(self):
        plugin = _make_plugin()
        existing = LabBridge(lab_id="lab1", bridge_name="arch-ovs")
        plugin.lab_bridges["lab1"] = existing
        bridge = await plugin._ensure_bridge("lab1")
        assert bridge is existing


class TestTouchLab:
    """Tests for TTL activity timestamp update."""

    def test_updates_timestamp(self):
        plugin = _make_plugin()
        lb = LabBridge(lab_id="lab1", bridge_name="arch-ovs")
        lb.last_activity = datetime(2020, 1, 1, tzinfo=timezone.utc)
        plugin.lab_bridges["lab1"] = lb

        plugin._touch_lab("lab1")
        assert lb.last_activity > datetime(2020, 1, 1, tzinfo=timezone.utc)

    def test_nonexistent_lab_noop(self):
        plugin = _make_plugin()
        plugin._touch_lab("nonexistent")  # Should not raise


class TestVethNameGeneration:
    """Tests for _generate_veth_names."""

    def test_returns_two_names(self):
        plugin = _make_plugin()
        host, cont = plugin._generate_veth_names("abc123")
        assert isinstance(host, str) and len(host) > 0
        assert isinstance(cont, str) and len(cont) > 0
        assert host != cont

    def test_uses_random_suffix(self):
        """Veth names include random suffix so successive calls differ."""
        plugin = _make_plugin()
        h1, c1 = plugin._generate_veth_names("same-id")
        h2, c2 = plugin._generate_veth_names("same-id")
        # Names include random component, so they differ per call
        assert h1 != h2 or c1 != c2

    def test_different_ids_produce_different_names(self):
        plugin = _make_plugin()
        h1, _ = plugin._generate_veth_names("id-a")
        h2, _ = plugin._generate_veth_names("id-b")
        assert h1 != h2


class TestValidateEndpointExists:
    """Tests for _validate_endpoint_exists checking OVS port presence."""

    @pytest.mark.asyncio
    async def test_port_exists(self):
        plugin = _make_plugin()
        ep = EndpointState(
            endpoint_id="ep1", network_id="net1", interface_name="eth1",
            host_veth="vh-001", cont_veth="vc-001", vlan_tag=100,
        )
        plugin._run_cmd = AsyncMock(return_value=(0, "arch-ovs", ""))
        assert await plugin._validate_endpoint_exists(ep) is True

    @pytest.mark.asyncio
    async def test_port_missing(self):
        plugin = _make_plugin()
        ep = EndpointState(
            endpoint_id="ep1", network_id="net1", interface_name="eth1",
            host_veth="vh-001", cont_veth="vc-001", vlan_tag=100,
        )
        plugin._run_cmd = AsyncMock(return_value=(2, "", "no port"))
        assert await plugin._validate_endpoint_exists(ep) is False

    @pytest.mark.asyncio
    async def test_empty_host_veth(self):
        plugin = _make_plugin()
        ep = EndpointState(
            endpoint_id="ep1", network_id="net1", interface_name="eth1",
            host_veth="", cont_veth="vc-001", vlan_tag=100,
        )
        assert await plugin._validate_endpoint_exists(ep) is False
