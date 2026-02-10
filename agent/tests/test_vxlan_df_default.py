"""Tests for VXLAN df unset on overlay tunnels.

Verifies that all VXLAN tunnel creation paths use Linux VXLAN devices
with `df unset` instead of OVS-managed VXLAN ports. The `df unset`
flag tells the kernel to clear the DF bit on outer UDP packets,
allowing the kernel to fragment oversized outer packets transparently.
This lets inner packets pass at full MTU while the kernel handles
outer packet fragmentation.

Without df unset, the kernel's VXLAN xmit path copies the inner
packet's DF bit to the outer header, which can cause ICMP "Frag
Needed" or silently dropped oversized inner packets.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock


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
    mgr._ip_link_exists = AsyncMock(return_value=False)
    mgr._initialized = True
    mgr._bridge_name = "arch-ovs"
    return mgr


def _get_run_cmd_args(mock: AsyncMock) -> list[list[str]]:
    """Extract all command lists from a _run_cmd mock."""
    return [call.args[0] for call in mock.call_args_list if call.args]


def _get_ovs_vsctl_args(mock: AsyncMock) -> list[tuple]:
    """Extract all call arg tuples from an _ovs_vsctl mock."""
    return [call.args for call in mock.call_args_list]


def _flatten_args(args: tuple) -> str:
    """Flatten a tuple of args into a single string for searching."""
    return " ".join(str(a) for a in args)


def _find_ip_link_add_cmd(run_cmd_calls: list[list[str]]) -> list[str] | None:
    """Find the 'ip link add ... type vxlan' command in run_cmd calls."""
    for cmd in run_cmd_calls:
        if (isinstance(cmd, list)
            and "ip" in cmd
            and "link" in cmd
            and "add" in cmd
            and "vxlan" in cmd):
            return cmd
    return None


def _has_df_unset(cmd: list[str]) -> bool:
    """Check if an ip link add command uses 'df unset' for VXLAN."""
    try:
        idx = cmd.index("df")
        return idx + 1 < len(cmd) and cmd[idx + 1] == "unset"
    except ValueError:
        return False


def _find_ip_link_delete_cmds(run_cmd_calls: list[list[str]], name: str) -> list[list[str]]:
    """Find 'ip link delete <name>' commands in run_cmd calls."""
    return [
        cmd for cmd in run_cmd_calls
        if (isinstance(cmd, list)
            and "ip" in cmd
            and "link" in cmd
            and "delete" in cmd
            and name in cmd)
    ]


# ===========================================================================
# OverlayManager.create_link_tunnel  (per-link VXLAN — active model)
# ===========================================================================

class TestCreateLinkTunnelDfUnset:
    """Per-link VXLAN ports must use Linux VXLAN devices with df unset."""

    def test_creates_linux_vxlan_device_with_df_unset(self):
        mgr = _make_overlay_manager()

        _run_async(mgr.create_link_tunnel(
            lab_id="lab1",
            link_id="r1:eth1-r2:eth1",
            vni=100001,
            local_ip="10.0.0.1",
            remote_ip="10.0.0.2",
            local_vlan=3100,
        ))

        cmd = _find_ip_link_add_cmd(_get_run_cmd_args(mgr._run_cmd))
        assert cmd is not None, "Expected 'ip link add ... type vxlan' call"
        assert _has_df_unset(cmd), f"'df unset' not found in: {cmd}"
        assert "100001" in cmd or str(100001) in cmd, f"VNI not found in: {cmd}"

    def test_no_ovs_managed_vxlan_type(self):
        """Should NOT create OVS-managed VXLAN (type=vxlan in ovs-vsctl)."""
        mgr = _make_overlay_manager()

        _run_async(mgr.create_link_tunnel(
            lab_id="lab1",
            link_id="r1:eth1-r2:eth1",
            vni=100001,
            local_ip="10.0.0.1",
            remote_ip="10.0.0.2",
            local_vlan=3100,
        ))

        for call_args in _get_ovs_vsctl_args(mgr._ovs_vsctl):
            flat = _flatten_args(call_args)
            assert "type=vxlan" not in flat, (
                f"OVS-managed VXLAN creation found (should use Linux device): {flat}"
            )

    def test_vxlan_device_added_to_ovs_with_vlan_tag(self):
        """VXLAN device should be added to OVS with access-mode VLAN tag."""
        mgr = _make_overlay_manager()

        _run_async(mgr.create_link_tunnel(
            lab_id="lab1",
            link_id="r1:eth1-r2:eth1",
            vni=100001,
            local_ip="10.0.0.1",
            remote_ip="10.0.0.2",
            local_vlan=3100,
        ))

        ovs_calls = _get_ovs_vsctl_args(mgr._ovs_vsctl)
        add_port_calls = [c for c in ovs_calls if "add-port" in _flatten_args(c)]
        assert len(add_port_calls) >= 1, "Expected add-port call"
        add_call = _flatten_args(add_port_calls[0])
        assert "tag=3100" in add_call, f"VLAN tag not found in: {add_call}"

    def test_df_unset_with_auto_mtu_discovery(self):
        """df unset should be set regardless of MTU discovery result."""
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

        cmd = _find_ip_link_add_cmd(_get_run_cmd_args(mgr._run_cmd))
        assert cmd is not None
        assert _has_df_unset(cmd), f"'df unset' not found in: {cmd}"

    def test_df_unset_with_failed_mtu_discovery(self):
        """df unset should be set even when MTU discovery fails."""
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

        cmd = _find_ip_link_add_cmd(_get_run_cmd_args(mgr._run_cmd))
        assert cmd is not None
        assert _has_df_unset(cmd), f"'df unset' not found in: {cmd}"


# ===========================================================================
# OverlayManager.ensure_vtep  (trunk VTEP — legacy model)
# ===========================================================================

class TestEnsureVtepDfUnset:
    """Trunk VTEP VXLAN ports must use Linux VXLAN devices with df unset."""

    def test_creates_linux_vxlan_device_with_df_unset(self):
        mgr = _make_overlay_manager()

        _run_async(mgr.ensure_vtep(
            local_ip="10.0.0.1",
            remote_ip="10.0.0.2",
        ))

        cmd = _find_ip_link_add_cmd(_get_run_cmd_args(mgr._run_cmd))
        assert cmd is not None, "Expected 'ip link add ... type vxlan' call"
        assert _has_df_unset(cmd), f"'df unset' not found in: {cmd}"

    def test_vtep_added_to_ovs_without_vlan_tag(self):
        """Trunk VTEP should be added without VLAN tag (trunk mode)."""
        mgr = _make_overlay_manager()

        _run_async(mgr.ensure_vtep(
            local_ip="10.0.0.1",
            remote_ip="10.0.0.2",
        ))

        ovs_calls = _get_ovs_vsctl_args(mgr._ovs_vsctl)
        add_port_calls = [c for c in ovs_calls if "add-port" in _flatten_args(c)]
        assert len(add_port_calls) >= 1
        add_call = _flatten_args(add_port_calls[0])
        assert "tag=" not in add_call, f"Trunk VTEP should not have VLAN tag: {add_call}"

    def test_vtep_returns_cached_without_recreating(self):
        """Second call for same remote should return cached VTEP, no new commands."""
        mgr = _make_overlay_manager()

        vtep1 = _run_async(mgr.ensure_vtep(
            local_ip="10.0.0.1",
            remote_ip="10.0.0.2",
        ))
        call_count_after_first = mgr._run_cmd.call_count

        vtep2 = _run_async(mgr.ensure_vtep(
            local_ip="10.0.0.1",
            remote_ip="10.0.0.2",
        ))

        assert vtep1 is vtep2
        assert mgr._run_cmd.call_count == call_count_after_first


# ===========================================================================
# OverlayManager.create_tunnel  (legacy per-link VXLAN)
# ===========================================================================

class TestCreateTunnelLegacyDfUnset:
    """Legacy per-link VXLAN tunnels must use Linux devices with df unset."""

    def test_creates_linux_vxlan_device_with_df_unset(self):
        mgr = _make_overlay_manager()

        _run_async(mgr.create_tunnel(
            lab_id="lab1",
            link_id="r1:eth1-r2:eth1",
            vni=100010,
            local_ip="10.0.0.1",
            remote_ip="10.0.0.2",
        ))

        cmd = _find_ip_link_add_cmd(_get_run_cmd_args(mgr._run_cmd))
        assert cmd is not None, "Expected 'ip link add ... type vxlan' call"
        assert _has_df_unset(cmd), f"'df unset' not found in: {cmd}"


# ===========================================================================
# OverlayManager deletion methods clean up Linux devices
# ===========================================================================

class TestVxlanDeviceCleanup:
    """Deletion methods must call 'ip link delete' for Linux VXLAN devices."""

    def test_delete_link_tunnel_removes_linux_device(self):
        mgr = _make_overlay_manager()

        tunnel = _run_async(mgr.create_link_tunnel(
            lab_id="lab1",
            link_id="r1:eth1-r2:eth1",
            vni=100001,
            local_ip="10.0.0.1",
            remote_ip="10.0.0.2",
            local_vlan=3100,
        ))

        mgr._run_cmd.reset_mock()
        _run_async(mgr.delete_link_tunnel("r1:eth1-r2:eth1"))

        delete_cmds = _find_ip_link_delete_cmds(
            _get_run_cmd_args(mgr._run_cmd), tunnel.interface_name
        )
        assert len(delete_cmds) >= 1, (
            f"Expected 'ip link delete {tunnel.interface_name}' call"
        )

    def test_delete_tunnel_removes_linux_device(self):
        mgr = _make_overlay_manager()

        tunnel = _run_async(mgr.create_tunnel(
            lab_id="lab1",
            link_id="r1:eth1-r2:eth1",
            vni=100010,
            local_ip="10.0.0.1",
            remote_ip="10.0.0.2",
        ))

        mgr._run_cmd.reset_mock()
        _run_async(mgr.delete_tunnel(tunnel))

        delete_cmds = _find_ip_link_delete_cmds(
            _get_run_cmd_args(mgr._run_cmd), tunnel.interface_name
        )
        assert len(delete_cmds) >= 1, (
            f"Expected 'ip link delete {tunnel.interface_name}' call"
        )

    def test_delete_vtep_removes_linux_device(self):
        mgr = _make_overlay_manager()

        vtep = _run_async(mgr.ensure_vtep(
            local_ip="10.0.0.1",
            remote_ip="10.0.0.2",
        ))

        mgr._run_cmd.reset_mock()
        _run_async(mgr.delete_vtep("10.0.0.2"))

        delete_cmds = _find_ip_link_delete_cmds(
            _get_run_cmd_args(mgr._run_cmd), vtep.interface_name
        )
        assert len(delete_cmds) >= 1, (
            f"Expected 'ip link delete {vtep.interface_name}' call"
        )


# ===========================================================================
# OVSNetworkManager.create_vxlan_tunnel
# ===========================================================================

class TestOVSManagerDfUnset:
    """OVSNetworkManager VXLAN tunnels must use Linux devices with df unset."""

    def test_creates_linux_vxlan_device_with_df_unset(self):
        mgr = _make_ovs_manager()

        _run_async(mgr.create_vxlan_tunnel(
            vni=200,
            remote_ip="10.0.0.2",
            local_ip="10.0.0.1",
            vlan_tag=3200,
        ))

        cmd = _find_ip_link_add_cmd(_get_run_cmd_args(mgr._run_cmd))
        assert cmd is not None, "Expected 'ip link add ... type vxlan' call"
        assert _has_df_unset(cmd), f"'df unset' not found in: {cmd}"

    def test_no_ovs_managed_vxlan_type(self):
        """Should NOT create OVS-managed VXLAN (type=vxlan in ovs-vsctl)."""
        mgr = _make_ovs_manager()

        _run_async(mgr.create_vxlan_tunnel(
            vni=201,
            remote_ip="10.0.0.2",
            local_ip="10.0.0.1",
            vlan_tag=None,
        ))

        for call_args in _get_ovs_vsctl_args(mgr._ovs_vsctl):
            flat = _flatten_args(call_args)
            assert "type=vxlan" not in flat, (
                f"OVS-managed VXLAN creation found: {flat}"
            )

    def test_delete_removes_linux_device(self):
        mgr = _make_ovs_manager()

        _run_async(mgr.create_vxlan_tunnel(
            vni=202,
            remote_ip="10.0.0.2",
            local_ip="10.0.0.1",
        ))

        mgr._run_cmd.reset_mock()
        _run_async(mgr.delete_vxlan_tunnel(202))

        delete_cmds = _find_ip_link_delete_cmds(
            _get_run_cmd_args(mgr._run_cmd), "vxlan202"
        )
        assert len(delete_cmds) >= 1, "Expected 'ip link delete vxlan202' call"


# ===========================================================================
# DockerOVSPlugin.create_vxlan_tunnel  (Linux VXLAN interface)
# ===========================================================================

class TestDockerPluginDfUnset:
    """DockerOVSPlugin Linux VXLAN interfaces must use df unset."""

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
        assert _has_df_unset(ip_link_cmd), (
            f"'df unset' not found in ip link add command: {ip_link_cmd}"
        )

    def test_df_unset_still_present(self):
        """df unset should still be set alongside nopmtudisc."""
        plugin = DockerOVSPlugin()
        plugin._run_cmd = AsyncMock(return_value=(0, "", ""))
        plugin._ovs_vsctl = AsyncMock(return_value=(0, "", ""))
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

        ip_link_calls = [
            call.args[0] for call in plugin._run_cmd.call_args_list
            if isinstance(call.args[0], list) and "ip" in call.args[0]
            and "add" in call.args[0]
        ]
        assert len(ip_link_calls) >= 1
        ip_link_cmd = ip_link_calls[0]

        assert "df" in ip_link_cmd and "unset" in ip_link_cmd, (
            f"'df unset' not found in ip link add command: {ip_link_cmd}"
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
            vni=302,
            remote_ip="10.0.0.2",
            local_ip="10.0.0.1",
            vlan_tag=3302,
        ))

        assert "ip_link_add" in call_order
        assert "ovs_add_port" in call_order
        assert call_order.index("ip_link_add") < call_order.index("ovs_add_port")


# ===========================================================================
# Cross-cutting: all paths consistent
# ===========================================================================

class TestAllPathsConsistent:
    """Verify all VXLAN creation paths use df unset consistently."""

    def test_overlay_and_ovs_manager_both_use_df_unset(self):
        """Both overlay and OVS manager paths should use df unset."""
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

        overlay_cmd = _find_ip_link_add_cmd(_get_run_cmd_args(overlay._run_cmd))
        ovs_cmd = _find_ip_link_add_cmd(_get_run_cmd_args(ovs._run_cmd))

        assert overlay_cmd is not None and _has_df_unset(overlay_cmd), \
            f"overlay: 'df unset' not found in: {overlay_cmd}"
        assert ovs_cmd is not None and _has_df_unset(ovs_cmd), \
            f"ovs: 'df unset' not found in: {ovs_cmd}"

    def test_no_path_uses_ovs_managed_vxlan(self):
        """No path should use OVS-managed VXLAN (type=vxlan in ovs-vsctl)."""
        overlay = _make_overlay_manager()
        ovs = _make_ovs_manager()

        _run_async(overlay.create_link_tunnel(
            lab_id="lab1",
            link_id="r1:eth1-r2:eth1",
            vni=100060,
            local_ip="10.0.0.1",
            remote_ip="10.0.0.2",
            local_vlan=3100,
        ))

        _run_async(ovs.create_vxlan_tunnel(
            vni=200060,
            remote_ip="10.0.0.2",
            local_ip="10.0.0.1",
            vlan_tag=3200,
        ))

        for call_args in _get_ovs_vsctl_args(overlay._ovs_vsctl):
            assert "type=vxlan" not in _flatten_args(call_args)

        for call_args in _get_ovs_vsctl_args(ovs._ovs_vsctl):
            assert "type=vxlan" not in _flatten_args(call_args)
