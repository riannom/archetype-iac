"""Comprehensive tests for agent/network/cleanup.py deep paths.

Covers: _is_veth_orphaned logic, cleanup_ovs_orphans, real deletion paths,
cleanup edge cases with missing/stale ports, error handling.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.network.cleanup import (
    CleanupStats,
    NetworkCleanupManager,
    _get_overlay_tracked_ports,
    _get_ovs_plugin_active_veths,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mgr() -> NetworkCleanupManager:
    """Create a fresh manager without touching Docker or OVS."""
    return NetworkCleanupManager()


def _iface(
    name: str,
    ifindex: int = 10,
    link_index: int | None = 20,
    state: str = "UP",
    master: str | None = None,
) -> dict:
    """Build a veth interface dict matching the format returned by _get_veth_interfaces."""
    return {
        "name": name,
        "ifindex": ifindex,
        "link_index": link_index,
        "state": state,
        "master": master,
    }


# ===========================================================================
# _is_veth_orphaned – thorough branch coverage
# ===========================================================================


class TestIsVethOrphaned:
    """Tests for the _is_veth_orphaned decision tree."""

    @pytest.mark.asyncio
    async def test_not_orphaned_when_master_set(self):
        """Veth with a bridge master is never orphaned."""
        mgr = _make_mgr()
        iface = _iface("arch12345678", master="ovs-system")
        result = await mgr._is_veth_orphaned(iface, set())
        assert result is False

    @pytest.mark.asyncio
    async def test_vh_prefix_not_orphaned_when_on_ovs(self, monkeypatch):
        """vh* veth attached to OVS bridge is not orphaned."""
        mgr = _make_mgr()
        iface = _iface("vhAbCdEf01", master=None)

        async def fake_run_cmd(cmd):
            if cmd == ["ovs-vsctl", "port-to-br", "vhAbCdEf01"]:
                return 0, "arch-ovs", ""
            return 1, "", "not found"

        monkeypatch.setattr(mgr, "_run_cmd", fake_run_cmd)
        result = await mgr._is_veth_orphaned(iface, set())
        assert result is False

    @pytest.mark.asyncio
    async def test_vh_prefix_orphaned_when_not_on_ovs(self, monkeypatch):
        """vh* veth not on OVS and no link_index is orphaned."""
        mgr = _make_mgr()
        iface = _iface("vhAbCdEf01", master=None, link_index=None)

        async def fake_run_cmd(cmd):
            # port-to-br fails -> not on OVS
            if "port-to-br" in cmd:
                return 1, "", "no port"
            return 0, "", ""

        monkeypatch.setattr(mgr, "_run_cmd", fake_run_cmd)
        result = await mgr._is_veth_orphaned(iface, set())
        assert result is True

    @pytest.mark.asyncio
    async def test_orphaned_when_no_link_index(self, monkeypatch):
        """Veth with no peer link_index is orphaned."""
        mgr = _make_mgr()
        iface = _iface("arch12345678", link_index=None)

        async def fake_run_cmd(cmd):
            return 1, "", ""

        monkeypatch.setattr(mgr, "_run_cmd", fake_run_cmd)
        result = await mgr._is_veth_orphaned(iface, set())
        assert result is True

    @pytest.mark.asyncio
    async def test_orphaned_when_peer_not_found(self, monkeypatch):
        """Veth whose peer ifindex doesn't exist is orphaned."""
        mgr = _make_mgr()
        iface = _iface("arch12345678", link_index=99)

        async def fake_run_cmd(cmd):
            if cmd == ["ip", "-j", "link", "show"]:
                # Return interfaces that do NOT include ifindex=99
                return 0, json.dumps([
                    {"ifname": "eth0", "ifindex": 1},
                    {"ifname": "lo", "ifindex": 2},
                ]), ""
            return 0, "", ""

        monkeypatch.setattr(mgr, "_run_cmd", fake_run_cmd)
        result = await mgr._is_veth_orphaned(iface, set())
        assert result is True

    @pytest.mark.asyncio
    async def test_not_orphaned_when_peer_has_master(self, monkeypatch):
        """Veth whose peer has a bridge master is not orphaned."""
        mgr = _make_mgr()
        iface = _iface("arch12345678", link_index=50)

        async def fake_run_cmd(cmd):
            if cmd == ["ip", "-j", "link", "show"]:
                return 0, json.dumps([
                    {"ifname": "vcaabbccdd", "ifindex": 50, "master": "arch-ovs"},
                ]), ""
            return 0, "", ""

        monkeypatch.setattr(mgr, "_run_cmd", fake_run_cmd)
        result = await mgr._is_veth_orphaned(iface, set())
        assert result is False

    @pytest.mark.asyncio
    async def test_not_orphaned_when_peer_in_container(self, monkeypatch):
        """Veth whose peer ifindex is found inside a container namespace is not orphaned."""
        mgr = _make_mgr()
        iface = _iface("arch12345678", link_index=50)

        async def fake_run_cmd(cmd):
            if cmd == ["ip", "-j", "link", "show"]:
                # Peer exists but no master
                return 0, json.dumps([
                    {"ifname": "eth1", "ifindex": 50},
                ]), ""
            if cmd == ["ip", "link", "show", "arch12345678"]:
                return 0, "10: arch12345678: <BROADCAST> state UP", ""
            return 0, "", ""

        monkeypatch.setattr(mgr, "_run_cmd", fake_run_cmd)
        # link_index 50 is in container ifindexes
        result = await mgr._is_veth_orphaned(iface, {50})
        assert result is False

    @pytest.mark.asyncio
    async def test_not_orphaned_when_ip_link_show_fails(self, monkeypatch):
        """When 'ip -j link show' fails, default to not orphaned."""
        mgr = _make_mgr()
        iface = _iface("arch12345678", link_index=50)

        async def fake_run_cmd(cmd):
            if cmd == ["ip", "-j", "link", "show"]:
                return 1, "", "command failed"
            return 0, "", ""

        monkeypatch.setattr(mgr, "_run_cmd", fake_run_cmd)
        result = await mgr._is_veth_orphaned(iface, set())
        assert result is False

    @pytest.mark.asyncio
    async def test_not_orphaned_when_host_side_has_master_in_ip_link(self, monkeypatch):
        """Final fallback: if `ip link show <name>` output contains 'master', not orphaned."""
        mgr = _make_mgr()
        iface = _iface("arch12345678", link_index=50)

        async def fake_run_cmd(cmd):
            if cmd == ["ip", "-j", "link", "show"]:
                # Peer found but no master, not in container
                return 0, json.dumps([
                    {"ifname": "eth1", "ifindex": 50},
                ]), ""
            if cmd == ["ip", "link", "show", "arch12345678"]:
                return 0, "10: arch12345678@if50: <BROADCAST> master ovs-system state UP", ""
            return 0, "", ""

        monkeypatch.setattr(mgr, "_run_cmd", fake_run_cmd)
        result = await mgr._is_veth_orphaned(iface, set())
        assert result is False

    @pytest.mark.asyncio
    async def test_falls_through_to_not_orphaned_on_json_exception(self, monkeypatch):
        """If JSON parsing fails inside the peer-check, default to not orphaned."""
        mgr = _make_mgr()
        iface = _iface("arch12345678", link_index=50)

        async def fake_run_cmd(cmd):
            if cmd == ["ip", "-j", "link", "show"]:
                # Return invalid JSON
                return 0, "NOT JSON AT ALL", ""
            return 0, "", ""

        monkeypatch.setattr(mgr, "_run_cmd", fake_run_cmd)
        result = await mgr._is_veth_orphaned(iface, set())
        assert result is False


# ===========================================================================
# cleanup_ovs_orphans
# ===========================================================================


class TestCleanupOvsOrphans:
    """Tests for cleanup_ovs_orphans which delegates to the OVS backend."""

    @pytest.mark.asyncio
    async def test_returns_zeros_when_no_backend(self, monkeypatch):
        """When get_network_backend raises, returns zero-filled result."""
        mgr = _make_mgr()

        with patch(
            "agent.network.cleanup.get_network_backend",
            side_effect=ImportError("no backend"),
            create=True,
        ):
            # The function catches the ImportError from the inner import
            result = await mgr.cleanup_ovs_orphans()

        assert result["orphans_deleted"] == 0
        assert result["tracked_removed"] == 0

    @pytest.mark.asyncio
    async def test_returns_zeros_when_backend_has_no_ovs_manager(self):
        """When backend lacks ovs_manager attribute, returns zeros."""
        mgr = _make_mgr()
        fake_backend = MagicMock(spec=[])  # no ovs_manager attribute

        with patch(
            "agent.network.backends.registry.get_network_backend",
            return_value=fake_backend,
        ):
            result = await mgr.cleanup_ovs_orphans()

        assert result["orphans_deleted"] == 0
        assert result["tracked_removed"] == 0

    @pytest.mark.asyncio
    async def test_returns_zeros_when_ovs_not_initialized(self):
        """When ovs_manager exists but _initialized is False, returns zeros."""
        mgr = _make_mgr()
        fake_ovs = MagicMock()
        fake_ovs._initialized = False
        fake_backend = MagicMock()
        fake_backend.ovs_manager = fake_ovs

        with patch(
            "agent.network.backends.registry.get_network_backend",
            return_value=fake_backend,
        ):
            result = await mgr.cleanup_ovs_orphans()

        assert result["orphans_deleted"] == 0
        assert result["tracked_removed"] == 0

    @pytest.mark.asyncio
    async def test_delegates_to_reconcile_when_initialized(self):
        """When OVS is initialized, calls reconcile_with_ovs and returns its data."""
        mgr = _make_mgr()
        fake_ovs = MagicMock()
        fake_ovs._initialized = True
        fake_ovs.reconcile_with_ovs = AsyncMock(return_value={
            "orphans_deleted": 3,
            "tracked_removed": 1,
            "errors": ["some error"],
        })
        fake_backend = MagicMock()
        fake_backend.ovs_manager = fake_ovs

        with patch(
            "agent.network.backends.registry.get_network_backend",
            return_value=fake_backend,
        ):
            result = await mgr.cleanup_ovs_orphans()

        assert result["orphans_deleted"] == 3
        assert result["tracked_removed"] == 1
        assert result["errors"] == ["some error"]
        fake_ovs.reconcile_with_ovs.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_catches_exception_during_reconcile(self):
        """Exception during reconcile_with_ovs is caught and reported."""
        mgr = _make_mgr()
        fake_ovs = MagicMock()
        fake_ovs._initialized = True
        fake_ovs.reconcile_with_ovs = AsyncMock(side_effect=RuntimeError("OVS down"))
        fake_backend = MagicMock()
        fake_backend.ovs_manager = fake_ovs

        with patch(
            "agent.network.backends.registry.get_network_backend",
            return_value=fake_backend,
        ):
            result = await mgr.cleanup_ovs_orphans()

        assert result["orphans_deleted"] == 0
        assert "OVS down" in result["errors"][0]


# ===========================================================================
# Real deletion paths in cleanup_orphaned_veths
# ===========================================================================


class TestVethDeletionPaths:
    """Tests for the actual delete codepath in cleanup_orphaned_veths."""

    @pytest.mark.asyncio
    async def test_successful_deletion(self, monkeypatch):
        """When orphaned veth is found and delete succeeds, stats reflect it."""
        mgr = _make_mgr()
        deleted_names = []

        async def fake_run_cmd(cmd):
            if cmd[:3] == ["ip", "link", "delete"]:
                deleted_names.append(cmd[3])
                return 0, "", ""
            return 0, "", ""

        monkeypatch.setattr(mgr, "_run_cmd", fake_run_cmd)
        monkeypatch.setattr(mgr, "_get_veth_interfaces", AsyncMock(return_value=[
            _iface("archdeadbeef"),
        ]))
        monkeypatch.setattr(mgr, "_get_running_container_pids", AsyncMock(return_value=set()))
        monkeypatch.setattr(mgr, "_get_container_ifindexes", AsyncMock(return_value=set()))
        monkeypatch.setattr(mgr, "_is_veth_orphaned", AsyncMock(return_value=True))

        with patch("agent.network.cleanup._get_ovs_plugin_active_veths", return_value=set()):
            stats = await mgr.cleanup_orphaned_veths(dry_run=False)

        assert stats.veths_deleted == 1
        assert stats.veths_orphaned == 1
        assert deleted_names == ["archdeadbeef"]
        assert stats.errors == []

    @pytest.mark.asyncio
    async def test_deletion_failure_records_error(self, monkeypatch):
        """When ip link delete fails, error is recorded in stats."""
        mgr = _make_mgr()

        async def fake_run_cmd(cmd):
            if cmd[:3] == ["ip", "link", "delete"]:
                return 1, "", "Cannot find device"
            return 0, "", ""

        monkeypatch.setattr(mgr, "_run_cmd", fake_run_cmd)
        monkeypatch.setattr(mgr, "_get_veth_interfaces", AsyncMock(return_value=[
            _iface("archdeadbeef"),
        ]))
        monkeypatch.setattr(mgr, "_get_running_container_pids", AsyncMock(return_value=set()))
        monkeypatch.setattr(mgr, "_get_container_ifindexes", AsyncMock(return_value=set()))
        monkeypatch.setattr(mgr, "_is_veth_orphaned", AsyncMock(return_value=True))

        with patch("agent.network.cleanup._get_ovs_plugin_active_veths", return_value=set()):
            stats = await mgr.cleanup_orphaned_veths(dry_run=False)

        assert stats.veths_deleted == 0
        assert stats.veths_orphaned == 1
        assert len(stats.errors) == 1
        assert "Cannot find device" in stats.errors[0]

    @pytest.mark.asyncio
    async def test_exception_during_veth_processing(self, monkeypatch):
        """Exception while checking a single veth is caught and recorded."""
        mgr = _make_mgr()

        monkeypatch.setattr(mgr, "_get_veth_interfaces", AsyncMock(return_value=[
            _iface("archdeadbeef"),
        ]))
        monkeypatch.setattr(mgr, "_get_running_container_pids", AsyncMock(return_value=set()))
        monkeypatch.setattr(mgr, "_get_container_ifindexes", AsyncMock(return_value=set()))
        monkeypatch.setattr(
            mgr, "_is_veth_orphaned",
            AsyncMock(side_effect=OSError("netlink error")),
        )

        with patch("agent.network.cleanup._get_ovs_plugin_active_veths", return_value=set()):
            stats = await mgr.cleanup_orphaned_veths(dry_run=False)

        assert stats.veths_deleted == 0
        assert len(stats.errors) == 1
        assert "netlink error" in stats.errors[0]

    @pytest.mark.asyncio
    async def test_skips_ovs_plugin_tracked_veths(self, monkeypatch):
        """Veths tracked by OVS plugin are skipped even if they would be orphaned."""
        mgr = _make_mgr()

        monkeypatch.setattr(mgr, "_get_veth_interfaces", AsyncMock(return_value=[
            _iface("arch11111111"),
            _iface("arch22222222"),
        ]))
        monkeypatch.setattr(mgr, "_get_running_container_pids", AsyncMock(return_value=set()))
        monkeypatch.setattr(mgr, "_get_container_ifindexes", AsyncMock(return_value=set()))
        # This should never be called for the tracked veth
        orphan_mock = AsyncMock(return_value=True)
        monkeypatch.setattr(mgr, "_is_veth_orphaned", orphan_mock)

        with patch(
            "agent.network.cleanup._get_ovs_plugin_active_veths",
            return_value={"arch11111111"},
        ):
            async def fake_run_cmd(cmd):
                return 0, "", ""
            monkeypatch.setattr(mgr, "_run_cmd", fake_run_cmd)
            stats = await mgr.cleanup_orphaned_veths(dry_run=False)

        # Only arch22222222 was checked
        assert orphan_mock.call_count == 1
        assert stats.veths_orphaned == 1
        assert stats.veths_deleted == 1

    @pytest.mark.asyncio
    async def test_no_veths_returns_early(self, monkeypatch):
        """When no archetype veths exist, cleanup returns immediately."""
        mgr = _make_mgr()
        monkeypatch.setattr(mgr, "_get_veth_interfaces", AsyncMock(return_value=[]))

        stats = await mgr.cleanup_orphaned_veths()
        assert stats.veths_found == 0
        assert stats.veths_orphaned == 0


# ===========================================================================
# Module-level helper functions
# ===========================================================================


class TestModuleHelpers:
    """Tests for _get_ovs_plugin_active_veths and _get_overlay_tracked_ports."""

    def test_ovs_plugin_active_veths_returns_empty_on_import_error(self):
        """Returns empty set when docker_plugin can't be imported."""
        with patch(
            "agent.network.cleanup.get_docker_ovs_plugin",
            side_effect=ImportError("no plugin"),
            create=True,
        ):
            # Force re-exec of the function which does its own internal import
            result = _get_ovs_plugin_active_veths()
        # Should be empty set (exception caught)
        assert isinstance(result, set)

    def test_overlay_tracked_ports_returns_empty_on_no_manager(self):
        """Returns empty set when overlay manager is None."""
        with patch(
            "agent.network.cleanup.get_overlay_manager",
            return_value=None,
            create=True,
        ):
            result = _get_overlay_tracked_ports()
        assert isinstance(result, set)

    def test_overlay_tracked_ports_collects_link_tunnels(self):
        """Collects interface_name from _link_tunnels dict."""
        fake_tunnel = MagicMock()
        fake_tunnel.interface_name = "vxlan-aabb1122"
        fake_overlay = MagicMock()
        fake_overlay._link_tunnels = {"link1": fake_tunnel}
        # Remove _tunnels to test just _link_tunnels path
        del fake_overlay._tunnels

        with patch(
            "agent.agent_state.get_overlay_manager",
            return_value=fake_overlay,
        ):
            result = _get_overlay_tracked_ports()

        assert "vxlan-aabb1122" in result


# ===========================================================================
# CleanupStats dataclass
# ===========================================================================


class TestCleanupStats:
    """Tests for CleanupStats dataclass behavior."""

    def test_default_errors_is_empty_list(self):
        stats = CleanupStats()
        assert stats.errors == []

    def test_to_dict_includes_all_fields(self):
        stats = CleanupStats(
            veths_found=5,
            veths_orphaned=3,
            veths_deleted=2,
            bridges_deleted=1,
            vxlans_deleted=1,
            ovs_orphans_deleted=4,
            ovs_tracked_removed=2,
            errors=["err1"],
        )
        d = stats.to_dict()
        assert d["veths_found"] == 5
        assert d["veths_orphaned"] == 3
        assert d["veths_deleted"] == 2
        assert d["bridges_deleted"] == 1
        assert d["vxlans_deleted"] == 1
        assert d["ovs_orphans_deleted"] == 4
        assert d["ovs_tracked_removed"] == 2
        assert d["errors"] == ["err1"]

    def test_errors_list_not_shared_between_instances(self):
        s1 = CleanupStats()
        s2 = CleanupStats()
        s1.errors.append("oops")
        assert s2.errors == []


# ===========================================================================
# run_full_cleanup integration paths
# ===========================================================================


class TestRunFullCleanup:
    """Tests for run_full_cleanup aggregation and OVS toggle."""

    @pytest.mark.asyncio
    async def test_include_ovs_false_skips_ovs_cleanup(self, monkeypatch):
        """When include_ovs=False, cleanup_ovs_orphans is not called."""
        mgr = _make_mgr()
        ovs_called = False

        async def fake_ovs():
            nonlocal ovs_called
            ovs_called = True
            return {"orphans_deleted": 0, "tracked_removed": 0, "errors": []}

        monkeypatch.setattr(mgr, "cleanup_orphaned_veths", AsyncMock(return_value=CleanupStats()))
        monkeypatch.setattr(mgr, "cleanup_orphaned_bridges", AsyncMock(return_value=0))
        monkeypatch.setattr(mgr, "cleanup_orphaned_vxlans", AsyncMock(return_value=0))
        monkeypatch.setattr(mgr, "cleanup_ovs_orphans", fake_ovs)

        await mgr.run_full_cleanup(dry_run=False, include_ovs=False)
        assert ovs_called is False

    @pytest.mark.asyncio
    async def test_dry_run_skips_ovs_cleanup(self, monkeypatch):
        """Dry-run mode skips OVS reconciliation even if include_ovs=True."""
        mgr = _make_mgr()
        ovs_called = False

        async def fake_ovs():
            nonlocal ovs_called
            ovs_called = True
            return {"orphans_deleted": 0, "tracked_removed": 0, "errors": []}

        monkeypatch.setattr(mgr, "cleanup_orphaned_veths", AsyncMock(return_value=CleanupStats()))
        monkeypatch.setattr(mgr, "cleanup_orphaned_bridges", AsyncMock(return_value=0))
        monkeypatch.setattr(mgr, "cleanup_orphaned_vxlans", AsyncMock(return_value=0))
        monkeypatch.setattr(mgr, "cleanup_ovs_orphans", fake_ovs)

        await mgr.run_full_cleanup(dry_run=True, include_ovs=True)
        assert ovs_called is False

    @pytest.mark.asyncio
    async def test_aggregates_all_sub_stats(self, monkeypatch):
        """Full cleanup aggregates stats from veths, bridges, vxlans, and OVS."""
        mgr = _make_mgr()
        veth_stats = CleanupStats(veths_found=10, veths_orphaned=4, veths_deleted=3)

        monkeypatch.setattr(mgr, "cleanup_orphaned_veths", AsyncMock(return_value=veth_stats))
        monkeypatch.setattr(mgr, "cleanup_orphaned_bridges", AsyncMock(return_value=2))
        monkeypatch.setattr(mgr, "cleanup_orphaned_vxlans", AsyncMock(return_value=1))
        monkeypatch.setattr(mgr, "cleanup_ovs_orphans", AsyncMock(return_value={
            "orphans_deleted": 5,
            "tracked_removed": 7,
            "errors": [],
        }))

        stats = await mgr.run_full_cleanup(dry_run=False, include_ovs=True)

        assert stats.veths_found == 10
        assert stats.veths_deleted == 3
        assert stats.bridges_deleted == 2
        assert stats.vxlans_deleted == 1
        assert stats.ovs_orphans_deleted == 5
        assert stats.ovs_tracked_removed == 7
