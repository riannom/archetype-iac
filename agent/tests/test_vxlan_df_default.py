"""Tests for VXLAN df_default=false on outer packets.

Verifies that all VXLAN tunnel creation paths disable the DF bit on
outer encapsulated packets, allowing the underlay to fragment oversized
packets instead of dropping them with ICMP "frag needed".

Without df_default=false, overlay MTU is capped at (underlay_mtu - 50),
e.g. 1450 on a 1500 underlay.  With df_default=false, inner packets up
to the interface MTU pass through and the underlay fragments/reassembles
the outer encapsulated packets transparently.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.network.overlay import OverlayManager
from agent.network.ovs import OVSNetworkManager
from agent.network.docker_plugin import DockerOVSPlugin, LabBridge


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_async(coro):
    """Run a coroutine synchronously for testing."""
    return asyncio.run(coro)


def _make_overlay_manager() -> OverlayManager:
    """Create an OverlayManager with mocked system calls."""
    mgr = OverlayManager()
    mgr._ovs_vsctl = AsyncMock(return_value=(0, "", ""))
    mgr._run_cmd = AsyncMock(return_value=(0, "", ""))
    mgr._ensure_ovs_bridge = AsyncMock()
    mgr._ovs_port_exists = AsyncMock(return_value=False)
    mgr._ip_link_exists = AsyncMock(return_value=False)
    mgr._discover_path_mtu = AsyncMock(return_value=1500)
    mgr._ovs_initialized = True
    return mgr


def _make_ovs_manager() -> OVSNetworkManager:
    """Create an OVSNetworkManager with mocked system calls."""
    mgr = OVSNetworkManager()
    mgr._ovs_vsctl = AsyncMock(return_value=(0, "", ""))
    mgr._run_cmd = AsyncMock(return_value=(0, "", ""))
    mgr._initialized = True
    mgr._bridge_name = "arch-ovs"
    return mgr


def _get_ovs_vsctl_args(mock: AsyncMock) -> list[tuple]:
    """Extract all call arg tuples from an _ovs_vsctl mock."""
    return [call.args for call in mock.call_args_list]


def _flatten_args(args: tuple) -> str:
    """Flatten a tuple of args into a single string for searching."""
    return " ".join(str(a) for a in args)


# ===========================================================================
# OverlayManager.create_link_tunnel  (per-link VXLAN — active model)
# ===========================================================================

class TestCreateLinkTunnelDfDefault:
    """Per-link access-mode VXLAN ports must set df_default=false."""

    def test_df_default_false_in_ovs_command(self):
        mgr = _make_overlay_manager()

        _run_async(mgr.create_link_tunnel(
            lab_id="lab1",
            link_id="r1:eth1-r2:eth1",
            vni=100001,
            local_ip="10.0.0.1",
            remote_ip="10.0.0.2",
            local_vlan=3100,
        ))

        # Find the add-port call
        calls = _get_ovs_vsctl_args(mgr._ovs_vsctl)
        assert len(calls) >= 1, "Expected at least one ovs-vsctl call"

        add_port_call = _flatten_args(calls[-1])
        assert "options:df_default=false" in add_port_call, (
            f"df_default=false not found in ovs-vsctl args: {add_port_call}"
        )

    def test_df_default_present_with_auto_mtu_discovery(self):
        """df_default=false should be set regardless of MTU discovery result."""
        mgr = _make_overlay_manager()
        mgr._discover_path_mtu = AsyncMock(return_value=9000)

        _run_async(mgr.create_link_tunnel(
            lab_id="lab1",
            link_id="r1:eth1-r2:eth1",
            vni=100002,
            local_ip="10.0.0.1",
            remote_ip="10.0.0.2",
            local_vlan=3100,
        ))

        add_port_call = _flatten_args(_get_ovs_vsctl_args(mgr._ovs_vsctl)[-1])
        assert "options:df_default=false" in add_port_call

    def test_df_default_present_with_explicit_mtu(self):
        """df_default=false should be set even when tenant_mtu is provided."""
        mgr = _make_overlay_manager()

        _run_async(mgr.create_link_tunnel(
            lab_id="lab1",
            link_id="r1:eth1-r2:eth1",
            vni=100003,
            local_ip="10.0.0.1",
            remote_ip="10.0.0.2",
            local_vlan=3100,
            tenant_mtu=1400,
        ))

        add_port_call = _flatten_args(_get_ovs_vsctl_args(mgr._ovs_vsctl)[-1])
        assert "options:df_default=false" in add_port_call

    def test_df_default_present_with_failed_mtu_discovery(self):
        """df_default=false should be set even when MTU discovery fails."""
        mgr = _make_overlay_manager()
        mgr._discover_path_mtu = AsyncMock(return_value=0)

        _run_async(mgr.create_link_tunnel(
            lab_id="lab1",
            link_id="r1:eth1-r2:eth1",
            vni=100004,
            local_ip="10.0.0.1",
            remote_ip="10.0.0.2",
            local_vlan=3100,
        ))

        add_port_call = _flatten_args(_get_ovs_vsctl_args(mgr._ovs_vsctl)[-1])
        assert "options:df_default=false" in add_port_call


# ===========================================================================
# OverlayManager.ensure_vtep  (trunk VTEP — legacy model)
# ===========================================================================

class TestEnsureVtepDfDefault:
    """Trunk VTEP VXLAN ports must set df_default=false."""

    def test_df_default_false_in_vtep_creation(self):
        mgr = _make_overlay_manager()

        _run_async(mgr.ensure_vtep(
            local_ip="10.0.0.1",
            remote_ip="10.0.0.2",
        ))

        calls = _get_ovs_vsctl_args(mgr._ovs_vsctl)
        # Filter to just add-port calls (skip --if-exists del-port)
        add_calls = [c for c in calls if "add-port" in _flatten_args(c)]
        assert len(add_calls) >= 1, "Expected an add-port call for VTEP"

        vtep_call = _flatten_args(add_calls[0])
        assert "options:df_default=false" in vtep_call, (
            f"df_default=false not found in VTEP creation: {vtep_call}"
        )

    def test_vtep_returns_cached_without_recreating(self):
        """Second call for same remote should return cached VTEP, no OVS calls."""
        mgr = _make_overlay_manager()

        vtep1 = _run_async(mgr.ensure_vtep(
            local_ip="10.0.0.1",
            remote_ip="10.0.0.2",
        ))
        call_count_after_first = mgr._ovs_vsctl.call_count

        vtep2 = _run_async(mgr.ensure_vtep(
            local_ip="10.0.0.1",
            remote_ip="10.0.0.2",
        ))

        assert vtep1 is vtep2
        assert mgr._ovs_vsctl.call_count == call_count_after_first


# ===========================================================================
# OverlayManager._create_tunnel  (legacy per-link VXLAN)
# ===========================================================================

class TestCreateTunnelLegacyDfDefault:
    """Legacy per-link VXLAN tunnels must set df_default=false."""

    def test_df_default_false_in_legacy_tunnel(self):
        mgr = _make_overlay_manager()

        _run_async(mgr.create_tunnel(
            lab_id="lab1",
            link_id="r1:eth1-r2:eth1",
            vni=100010,
            local_ip="10.0.0.1",
            remote_ip="10.0.0.2",
        ))

        calls = _get_ovs_vsctl_args(mgr._ovs_vsctl)
        add_calls = [c for c in calls if "add-port" in _flatten_args(c)]
        assert len(add_calls) >= 1

        tunnel_call = _flatten_args(add_calls[0])
        assert "options:df_default=false" in tunnel_call, (
            f"df_default=false not found in legacy tunnel creation: {tunnel_call}"
        )


# ===========================================================================
# OVSNetworkManager.create_vxlan_tunnel
# ===========================================================================

class TestOVSManagerDfDefault:
    """OVSNetworkManager VXLAN tunnels must include df_default=false."""

    def test_df_default_false_with_vlan_tag(self):
        mgr = _make_ovs_manager()

        _run_async(mgr.create_vxlan_tunnel(
            vni=200,
            remote_ip="10.0.0.2",
            local_ip="10.0.0.1",
            vlan_tag=3200,
        ))

        calls = _get_ovs_vsctl_args(mgr._ovs_vsctl)
        # Find the add-port call (skip del-port)
        add_calls = [c for c in calls if "add-port" in _flatten_args(c)]
        assert len(add_calls) >= 1

        cmd = _flatten_args(add_calls[0])
        assert "df_default=false" in cmd, (
            f"df_default=false not found in OVS VXLAN creation: {cmd}"
        )

    def test_df_default_false_without_vlan_tag(self):
        mgr = _make_ovs_manager()

        _run_async(mgr.create_vxlan_tunnel(
            vni=201,
            remote_ip="10.0.0.2",
            local_ip="10.0.0.1",
            vlan_tag=None,
        ))

        calls = _get_ovs_vsctl_args(mgr._ovs_vsctl)
        add_calls = [c for c in calls if "add-port" in _flatten_args(c)]
        assert len(add_calls) >= 1

        cmd = _flatten_args(add_calls[0])
        assert "df_default=false" in cmd, (
            f"df_default=false not found in trunkless OVS VXLAN: {cmd}"
        )


# ===========================================================================
# DockerOVSPlugin.create_vxlan_tunnel  (Linux VXLAN interface)
# ===========================================================================

class TestDockerPluginDfUnset:
    """DockerOVSPlugin Linux VXLAN interfaces must use 'df unset'."""

    def test_df_unset_in_ip_link_add(self):
        plugin = DockerOVSPlugin()
        plugin._run_cmd = AsyncMock(return_value=(0, "", ""))
        plugin._ovs_vsctl = AsyncMock(return_value=(0, "", ""))
        plugin._mark_dirty_and_save = AsyncMock()

        # Set up a lab bridge
        lab_bridge = LabBridge(lab_id="lab1", bridge_name="arch-ovs")
        plugin.lab_bridges["lab1"] = lab_bridge

        _run_async(plugin.create_vxlan_tunnel(
            lab_id="lab1",
            link_id="r1:eth1-r2:eth1",
            vni=300,
            remote_ip="10.0.0.2",
            local_ip="10.0.0.1",
            vlan_tag=3300,
        ))

        # Find the 'ip link add' call
        ip_link_calls = [
            call.args[0] for call in plugin._run_cmd.call_args_list
            if isinstance(call.args[0], list) and "ip" in call.args[0]
            and "add" in call.args[0]
        ]
        assert len(ip_link_calls) >= 1, "Expected an 'ip link add' call"

        ip_link_cmd = ip_link_calls[0]
        assert "df" in ip_link_cmd and "unset" in ip_link_cmd, (
            f"'df unset' not found in ip link add command: {ip_link_cmd}"
        )

        # Verify 'df' immediately precedes 'unset'
        df_idx = ip_link_cmd.index("df")
        assert ip_link_cmd[df_idx + 1] == "unset", (
            f"Expected 'unset' after 'df', got: {ip_link_cmd[df_idx + 1]}"
        )

    def test_vxlan_port_added_to_ovs_after_creation(self):
        """Verify OVS add-port happens after Linux VXLAN interface is created."""
        plugin = DockerOVSPlugin()
        call_order = []

        async def mock_run_cmd(cmd, **kwargs):
            if isinstance(cmd, list) and "ip" in cmd and "add" in cmd:
                call_order.append("ip_link_add")
            elif isinstance(cmd, list) and "ip" in cmd and "set" in cmd:
                call_order.append("ip_link_set")
            return (0, "", "")

        plugin._run_cmd = mock_run_cmd
        plugin._ovs_vsctl = AsyncMock(
            side_effect=lambda *args, **kwargs: (
                call_order.append("ovs_add_port") or (0, "", "")
            )
        )
        plugin._mark_dirty_and_save = AsyncMock()

        lab_bridge = LabBridge(lab_id="lab1", bridge_name="arch-ovs")
        plugin.lab_bridges["lab1"] = lab_bridge

        _run_async(plugin.create_vxlan_tunnel(
            lab_id="lab1",
            link_id="r1:eth1-r2:eth1",
            vni=301,
            remote_ip="10.0.0.2",
            local_ip="10.0.0.1",
            vlan_tag=3301,
        ))

        assert "ip_link_add" in call_order
        assert "ovs_add_port" in call_order
        assert call_order.index("ip_link_add") < call_order.index("ovs_add_port")


# ===========================================================================
# Cross-cutting: all paths consistent
# ===========================================================================

class TestAllPathsConsistent:
    """Verify all VXLAN creation paths consistently disable DF."""

    def test_overlay_link_tunnel_and_ovs_manager_both_disable_df(self):
        """Both the overlay and OVS manager paths should disable DF."""
        overlay = _make_overlay_manager()
        ovs = _make_ovs_manager()

        _run_async(overlay.create_link_tunnel(
            lab_id="lab1",
            link_id="r1:eth1-r2:eth1",
            vni=100050,
            local_ip="10.0.0.1",
            remote_ip="10.0.0.2",
            local_vlan=3100,
        ))

        _run_async(ovs.create_vxlan_tunnel(
            vni=200050,
            remote_ip="10.0.0.2",
            local_ip="10.0.0.1",
            vlan_tag=3200,
        ))

        overlay_cmd = _flatten_args(_get_ovs_vsctl_args(overlay._ovs_vsctl)[-1])
        ovs_cmd = _flatten_args(_get_ovs_vsctl_args(ovs._ovs_vsctl)[-1])

        assert "df_default=false" in overlay_cmd
        assert "df_default=false" in ovs_cmd
