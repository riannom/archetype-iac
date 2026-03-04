"""Extended tests for VXLAN device lifecycle (overlay_vxlan.py) and
health monitoring (overlay_health.py).

Covers:
- create_vxlan_device: creation, retry on "already exists", MTU, OVS add, errors
- delete_vxlan_device: cleanup, partial failures
- discover_path_mtu: cache hits, probing, failure modes
- read_vxlan_link_info: parsing, edge cases
- ip_link_exists: delegation
- OverlayHealthMonitor: start/stop lifecycle, multi-tunnel repair, edge cases
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.config import settings
from agent.network.overlay import LinkTunnel, OverlayManager
from agent.network.overlay_health import OverlayHealthMonitor
from agent.network.overlay_vxlan import (
    VXLAN_PORT,
    create_vxlan_device,
    delete_vxlan_device,
    discover_path_mtu,
    ip_link_exists,
    read_vxlan_link_info,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_overlay(tmp_path: Path) -> OverlayManager:
    """Create an OverlayManager with mocked dependencies."""
    overlay = OverlayManager.__new__(OverlayManager)
    overlay._bridge_name = "arch-ovs"
    overlay._link_tunnels = {}
    overlay._vteps = {}
    overlay._mtu_cache = {}
    overlay._ovs_initialized = False
    overlay._run_cmd = AsyncMock(return_value=(0, "", ""))
    overlay._ovs_vsctl = AsyncMock(return_value=(0, "", ""))
    overlay._ip_link_exists = AsyncMock(return_value=False)
    overlay._ensure_ovs_bridge = AsyncMock()
    overlay._create_vxlan_device = AsyncMock()
    overlay._delete_vxlan_device = AsyncMock()
    return overlay


# ===================================================================
# create_vxlan_device
# ===================================================================


@pytest.mark.asyncio
async def test_create_vxlan_device_basic():
    """Creates VXLAN device, sets MTU, brings up, adds to OVS."""
    calls = []

    async def _run_cmd(cmd):
        calls.append(cmd)
        return (0, "", "")

    async def _ovs_vsctl(*args):
        calls.append(list(args))
        return (0, "", "")

    with patch("agent.network.overlay_vxlan._shared_run_cmd", side_effect=_run_cmd), \
         patch("agent.network.overlay_vxlan._shared_ovs_vsctl", side_effect=_ovs_vsctl), \
         patch.object(settings, "overlay_mtu", 1400):

        await create_vxlan_device(
            name="vxlan-test1",
            vni=50000,
            local_ip="10.0.0.1",
            remote_ip="10.0.0.2",
            bridge="arch-ovs",
            vlan_tag=3001,
            tenant_mtu=1400,
        )

    # Check ip link add was called
    add_call = calls[0]
    assert "vxlan" in add_call
    assert "50000" in add_call  # VNI
    assert "10.0.0.1" in add_call  # local
    assert "10.0.0.2" in add_call  # remote
    assert str(VXLAN_PORT) in add_call

    # MTU set
    mtu_call = calls[1]
    assert "mtu" in mtu_call
    assert "1400" in mtu_call

    # Link up
    up_call = calls[2]
    assert "up" in up_call

    # OVS add-port with tag
    ovs_call = calls[3]
    assert "add-port" in ovs_call
    assert "tag=3001" in ovs_call


@pytest.mark.asyncio
async def test_create_vxlan_device_no_vlan_tag():
    """Creates VXLAN device without VLAN tag (trunk mode)."""
    ovs_calls = []

    async def _run_cmd(cmd):
        return (0, "", "")

    async def _ovs_vsctl(*args):
        ovs_calls.append(list(args))
        return (0, "", "")

    with patch("agent.network.overlay_vxlan._shared_run_cmd", side_effect=_run_cmd), \
         patch("agent.network.overlay_vxlan._shared_ovs_vsctl", side_effect=_ovs_vsctl):

        await create_vxlan_device(
            name="vxlan-trunk",
            vni=50000,
            local_ip="10.0.0.1",
            remote_ip="10.0.0.2",
            bridge="arch-ovs",
            vlan_tag=None,
            tenant_mtu=1500,
        )

    # OVS add-port WITHOUT tag=
    assert len(ovs_calls) == 1
    assert "tag=" not in " ".join(str(x) for x in ovs_calls[0])


@pytest.mark.asyncio
async def test_create_vxlan_device_retry_on_already_exists():
    """Retries creation after deleting stale device on 'already exists' error."""
    call_count = 0

    async def _run_cmd(cmd):
        nonlocal call_count
        if cmd[0] == "ip" and "add" in cmd:
            call_count += 1
            if call_count == 1:
                return (1, "", "RTNETLINK answers: File exists - already exists")
            return (0, "", "")
        return (0, "", "")

    async def _ovs_vsctl(*args):
        return (0, "", "")

    with patch("agent.network.overlay_vxlan._shared_run_cmd", side_effect=_run_cmd), \
         patch("agent.network.overlay_vxlan._shared_ovs_vsctl", side_effect=_ovs_vsctl):

        await create_vxlan_device(
            name="vxlan-retry",
            vni=50000,
            local_ip="10.0.0.1",
            remote_ip="10.0.0.2",
            bridge="arch-ovs",
            tenant_mtu=1400,
        )

    # Should have tried twice (initial + retry)
    assert call_count == 2


@pytest.mark.asyncio
async def test_create_vxlan_device_fails_after_retry():
    """Raises RuntimeError if creation still fails after retry."""

    async def _run_cmd(cmd):
        if cmd[0] == "ip" and "add" in cmd:
            return (1, "", "already exists")
        if cmd[0] == "ip" and "delete" in cmd:
            return (0, "", "")
        return (0, "", "")

    with patch("agent.network.overlay_vxlan._shared_run_cmd", side_effect=_run_cmd):
        with pytest.raises(RuntimeError, match="Failed to create VXLAN device"):
            await create_vxlan_device(
                name="vxlan-fail",
                vni=50000,
                local_ip="10.0.0.1",
                remote_ip="10.0.0.2",
                bridge="arch-ovs",
            )


@pytest.mark.asyncio
async def test_create_vxlan_device_ovs_add_port_failure_cleans_up():
    """On OVS add-port failure, VXLAN device is cleaned up."""
    deleted = []

    async def _run_cmd(cmd):
        if "delete" in cmd:
            deleted.append(cmd)
        return (0, "", "")

    async def _ovs_vsctl(*args):
        if "add-port" in args:
            return (1, "", "OVS error: cannot add port")
        return (0, "", "")

    with patch("agent.network.overlay_vxlan._shared_run_cmd", side_effect=_run_cmd), \
         patch("agent.network.overlay_vxlan._shared_ovs_vsctl", side_effect=_ovs_vsctl):

        with pytest.raises(RuntimeError, match="Failed to add VXLAN device"):
            await create_vxlan_device(
                name="vxlan-ovsfail",
                vni=50000,
                local_ip="10.0.0.1",
                remote_ip="10.0.0.2",
                bridge="arch-ovs",
            )

    # Device should be deleted after OVS failure
    assert any("vxlan-ovsfail" in str(d) for d in deleted)


@pytest.mark.asyncio
async def test_create_vxlan_device_default_mtu_from_settings():
    """Uses settings.overlay_mtu when tenant_mtu=0."""
    mtu_set = []

    async def _run_cmd(cmd):
        if "mtu" in cmd:
            mtu_set.append(cmd)
        return (0, "", "")

    async def _ovs_vsctl(*args):
        return (0, "", "")

    with patch("agent.network.overlay_vxlan._shared_run_cmd", side_effect=_run_cmd), \
         patch("agent.network.overlay_vxlan._shared_ovs_vsctl", side_effect=_ovs_vsctl), \
         patch.object(settings, "overlay_mtu", 1450):

        await create_vxlan_device(
            name="vxlan-defmtu",
            vni=50000,
            local_ip="10.0.0.1",
            remote_ip="10.0.0.2",
            bridge="arch-ovs",
            tenant_mtu=0,
        )

    assert any("1450" in cmd for cmd in mtu_set)


@pytest.mark.asyncio
async def test_create_vxlan_device_fallback_mtu_1500():
    """Falls back to 1500 when both tenant_mtu and overlay_mtu are 0."""
    mtu_set = []

    async def _run_cmd(cmd):
        if "mtu" in cmd:
            mtu_set.append(cmd)
        return (0, "", "")

    async def _ovs_vsctl(*args):
        return (0, "", "")

    with patch("agent.network.overlay_vxlan._shared_run_cmd", side_effect=_run_cmd), \
         patch("agent.network.overlay_vxlan._shared_ovs_vsctl", side_effect=_ovs_vsctl), \
         patch.object(settings, "overlay_mtu", 0):

        await create_vxlan_device(
            name="vxlan-1500",
            vni=50000,
            local_ip="10.0.0.1",
            remote_ip="10.0.0.2",
            bridge="arch-ovs",
            tenant_mtu=0,
        )

    assert any("1500" in cmd for cmd in mtu_set)


@pytest.mark.asyncio
async def test_create_vxlan_non_retry_failure():
    """Raises RuntimeError on creation failure that is not 'already exists'."""

    async def _run_cmd(cmd):
        if cmd[0] == "ip" and "add" in cmd:
            return (1, "", "some other error")
        return (0, "", "")

    with patch("agent.network.overlay_vxlan._shared_run_cmd", side_effect=_run_cmd):
        with pytest.raises(RuntimeError, match="Failed to create VXLAN device"):
            await create_vxlan_device(
                name="vxlan-err",
                vni=50000,
                local_ip="10.0.0.1",
                remote_ip="10.0.0.2",
                bridge="arch-ovs",
            )


# ===================================================================
# delete_vxlan_device
# ===================================================================


@pytest.mark.asyncio
async def test_delete_vxlan_device_success():
    """Deletes OVS port and Linux device cleanly."""
    with patch("agent.network.overlay_vxlan._shared_ovs_vsctl", new_callable=AsyncMock, return_value=(0, "", "")) as mock_ovs, \
         patch("agent.network.overlay_vxlan._shared_run_cmd", new_callable=AsyncMock, return_value=(0, "", "")) as mock_cmd:

        await delete_vxlan_device("vxlan-test", "arch-ovs")

    mock_ovs.assert_called_once_with("--if-exists", "del-port", "arch-ovs", "vxlan-test")
    mock_cmd.assert_called_once_with(["ip", "link", "delete", "vxlan-test"])


@pytest.mark.asyncio
async def test_delete_vxlan_device_partial_failure():
    """Logs warning when one of the delete operations fails."""
    with patch("agent.network.overlay_vxlan._shared_ovs_vsctl", new_callable=AsyncMock, return_value=(0, "", "")), \
         patch("agent.network.overlay_vxlan._shared_run_cmd", new_callable=AsyncMock, return_value=(1, "", "No such device")):

        # Should not raise
        await delete_vxlan_device("vxlan-gone", "arch-ovs")


@pytest.mark.asyncio
async def test_delete_vxlan_device_both_fail():
    """Does not raise even when both operations fail."""
    with patch("agent.network.overlay_vxlan._shared_ovs_vsctl", new_callable=AsyncMock, return_value=(1, "", "error")), \
         patch("agent.network.overlay_vxlan._shared_run_cmd", new_callable=AsyncMock, return_value=(1, "", "error")):

        await delete_vxlan_device("vxlan-none", "arch-ovs")


# ===================================================================
# read_vxlan_link_info
# ===================================================================


@pytest.mark.asyncio
async def test_read_vxlan_link_info_success():
    """Parses VNI, remote, and local from ip link output."""
    ip_output = (
        "5: vxlan-abc12345: <BROADCAST,MULTICAST,UP> mtu 1400 state UP\n"
        "    link/ether 5e:1a:2b:3c:4d:5e brd ff:ff:ff:ff:ff:ff\n"
        "    vxlan id 50000 remote 10.0.0.2 local 10.0.0.1 dstport 4789\n"
    )

    with patch(
        "agent.network.overlay_vxlan._shared_run_cmd",
        new_callable=AsyncMock,
        return_value=(0, ip_output, ""),
    ):
        vni, remote_ip, local_ip = await read_vxlan_link_info("vxlan-abc12345")

    assert vni == 50000
    assert remote_ip == "10.0.0.2"
    assert local_ip == "10.0.0.1"


@pytest.mark.asyncio
async def test_read_vxlan_link_info_device_not_found():
    """Returns zeros/empty when device does not exist."""
    with patch(
        "agent.network.overlay_vxlan._shared_run_cmd",
        new_callable=AsyncMock,
        return_value=(1, "", "Device not found"),
    ):
        vni, remote_ip, local_ip = await read_vxlan_link_info("vxlan-missing")

    assert vni == 0
    assert remote_ip == ""
    assert local_ip == ""


@pytest.mark.asyncio
async def test_read_vxlan_link_info_partial_output():
    """Handles output with only some fields present."""
    ip_output = "vxlan id 12345 dstport 4789\n"

    with patch(
        "agent.network.overlay_vxlan._shared_run_cmd",
        new_callable=AsyncMock,
        return_value=(0, ip_output, ""),
    ):
        vni, remote_ip, local_ip = await read_vxlan_link_info("vxlan-partial")

    assert vni == 12345
    assert remote_ip == ""
    assert local_ip == ""


@pytest.mark.asyncio
async def test_read_vxlan_link_info_non_integer_vni():
    """Non-integer VNI stays 0."""
    ip_output = "vxlan id notanum remote 10.0.0.2 local 10.0.0.1\n"

    with patch(
        "agent.network.overlay_vxlan._shared_run_cmd",
        new_callable=AsyncMock,
        return_value=(0, ip_output, ""),
    ):
        vni, remote_ip, local_ip = await read_vxlan_link_info("vxlan-bad")

    assert vni == 0
    assert remote_ip == "10.0.0.2"
    assert local_ip == "10.0.0.1"


# ===================================================================
# ip_link_exists
# ===================================================================


@pytest.mark.asyncio
async def test_ip_link_exists_delegates():
    """ip_link_exists delegates to shared implementation."""
    with patch(
        "agent.network.overlay_vxlan._shared_ip_link_exists",
        new_callable=AsyncMock,
        return_value=True,
    ):
        result = await ip_link_exists("eth0")
    assert result is True


@pytest.mark.asyncio
async def test_ip_link_exists_not_found():
    """ip_link_exists returns False when device is missing."""
    with patch(
        "agent.network.overlay_vxlan._shared_ip_link_exists",
        new_callable=AsyncMock,
        return_value=False,
    ):
        result = await ip_link_exists("no-such-dev")
    assert result is False


# ===================================================================
# discover_path_mtu
# ===================================================================


@pytest.mark.asyncio
async def test_discover_mtu_cache_hit():
    """Returns cached MTU without probing."""
    cache = {"10.0.0.2": 9000}

    with patch("agent.network.transport.get_data_plane_ip", return_value="10.0.0.1"):
        result = await discover_path_mtu("10.0.0.2", cache)

    assert result == 9000


@pytest.mark.asyncio
async def test_discover_mtu_first_probe_succeeds():
    """Returns 9000 when first (jumbo) probe succeeds."""
    cache: dict[str, int] = {}

    async def _mock_subprocess(*args, **kwargs):
        proc = MagicMock()
        proc.returncode = 0

        async def communicate():
            return b"64 bytes from 10.0.0.2", b""

        proc.communicate = communicate
        return proc

    with patch("agent.network.transport.get_data_plane_ip", return_value="10.0.0.1"), \
         patch("asyncio.create_subprocess_exec", side_effect=_mock_subprocess):

        result = await discover_path_mtu("10.0.0.2", cache)

    assert result == 9000
    assert cache["10.0.0.2"] == 9000


@pytest.mark.asyncio
async def test_discover_mtu_all_probes_fail():
    """Returns 0 when all MTU probes fail."""
    cache: dict[str, int] = {}

    async def _mock_subprocess(*args, **kwargs):
        proc = MagicMock()
        proc.returncode = 1

        async def communicate():
            return b"", b"message too long"

        proc.communicate = communicate
        return proc

    with patch("agent.network.transport.get_data_plane_ip", return_value="10.0.0.1"), \
         patch("asyncio.create_subprocess_exec", side_effect=_mock_subprocess):

        result = await discover_path_mtu("10.0.0.2", cache)

    assert result == 0
    assert "10.0.0.2" not in cache


@pytest.mark.asyncio
async def test_discover_mtu_timeout():
    """Returns 0 on timeout for all probes."""
    cache: dict[str, int] = {}

    async def _mock_subprocess(*args, **kwargs):
        raise asyncio.TimeoutError()

    with patch("agent.network.transport.get_data_plane_ip", return_value=None), \
         patch("asyncio.create_subprocess_exec", side_effect=_mock_subprocess):

        result = await discover_path_mtu("10.0.0.3", cache)

    assert result == 0


# ===================================================================
# OverlayHealthMonitor — extended tests
# ===================================================================


@pytest.mark.asyncio
async def test_health_monitor_no_overlay_manager():
    """check_and_repair returns zeros when overlay manager is None."""
    monitor = OverlayHealthMonitor(interval=60)

    with patch(
        "agent.agent_state.get_overlay_manager",
        return_value=None,
    ):
        result = await monitor.check_and_repair()

    assert result["checked"] == 0
    assert result["repaired"] == 0


@pytest.mark.asyncio
async def test_health_monitor_empty_tunnels(tmp_path):
    """check_and_repair returns zeros when no tunnels tracked."""
    overlay = _make_overlay(tmp_path)

    monitor = OverlayHealthMonitor(interval=60)

    with patch("agent.agent_state.get_overlay_manager", return_value=overlay):
        result = await monitor.check_and_repair()

    assert result["checked"] == 0
    assert result["repaired"] == 0


@pytest.mark.asyncio
async def test_health_monitor_multiple_tunnels_mixed(tmp_path):
    """Repairs broken tunnels while leaving healthy ones alone."""
    overlay = _make_overlay(tmp_path)

    overlay._link_tunnels["link-healthy"] = LinkTunnel(
        link_id="link-healthy",
        vni=50000,
        local_ip="10.0.0.1",
        remote_ip="10.0.0.2",
        local_vlan=3001,
        interface_name="vxlan-healthy1",
        lab_id="lab-1",
        tenant_mtu=1400,
    )
    overlay._link_tunnels["link-broken"] = LinkTunnel(
        link_id="link-broken",
        vni=50001,
        local_ip="10.0.0.1",
        remote_ip="10.0.0.3",
        local_vlan=3002,
        interface_name="vxlan-broken01",
        lab_id="lab-1",
        tenant_mtu=1400,
    )
    overlay._link_tunnels["link-missing"] = LinkTunnel(
        link_id="link-missing",
        vni=50002,
        local_ip="10.0.0.1",
        remote_ip="10.0.0.4",
        local_vlan=3003,
        interface_name="vxlan-missing1",
        lab_id="lab-1",
        tenant_mtu=1400,
    )

    monitor = OverlayHealthMonitor(interval=60)

    with patch("agent.agent_state.get_overlay_manager", return_value=overlay), \
         patch(
             "agent.network.overlay_state.batch_read_ovs_ports",
             new_callable=AsyncMock,
             return_value={
                 "vxlan-healthy1": {"name": "vxlan-healthy1", "tag": 3001, "type": "vxlan", "ofport": 10},
                 "vxlan-broken01": {"name": "vxlan-broken01", "tag": 3002, "type": "vxlan", "ofport": -1},
                 # vxlan-missing1 is not in OVS at all
             },
         ):
        result = await monitor.check_and_repair()

    assert result["checked"] == 3
    assert result["repaired"] == 2

    # del-port should only be called for the broken one (not missing)
    del_port_calls = [
        c for c in overlay._ovs_vsctl.call_args_list
        if len(c.args) >= 2 and c.args[0] == "del-port"
    ]
    assert len(del_port_calls) == 1
    assert del_port_calls[0].args[2] == "vxlan-broken01"

    # create should be called for both broken and missing
    assert overlay._create_vxlan_device.call_count == 2


@pytest.mark.asyncio
async def test_health_monitor_vlan_zero_passes_none(tmp_path):
    """Repair passes vlan_tag=None when local_vlan is 0."""
    overlay = _make_overlay(tmp_path)

    overlay._link_tunnels["link-no-vlan"] = LinkTunnel(
        link_id="link-no-vlan",
        vni=50000,
        local_ip="10.0.0.1",
        remote_ip="10.0.0.2",
        local_vlan=0,
        interface_name="vxlan-novlan01",
        lab_id="lab-1",
        tenant_mtu=1400,
    )

    monitor = OverlayHealthMonitor(interval=60)

    with patch("agent.agent_state.get_overlay_manager", return_value=overlay), \
         patch(
             "agent.network.overlay_state.batch_read_ovs_ports",
             new_callable=AsyncMock,
             return_value={},
         ):
        await monitor.check_and_repair()

    call_kwargs = overlay._create_vxlan_device.call_args.kwargs
    assert call_kwargs["vlan_tag"] is None


@pytest.mark.asyncio
async def test_health_monitor_double_start():
    """Starting monitor twice logs warning, doesn't create duplicate tasks."""
    monitor = OverlayHealthMonitor(interval=300)

    with patch.object(monitor, "check_and_repair", new_callable=AsyncMock, return_value={"checked": 0, "repaired": 0}):
        await monitor.start()
        first_task = monitor._task

        await monitor.start()  # Should log warning
        assert monitor._task is first_task  # Same task

        await monitor.stop()


@pytest.mark.asyncio
async def test_health_monitor_stop_idempotent():
    """Stopping an already stopped monitor is a no-op."""
    monitor = OverlayHealthMonitor(interval=300)

    # Never started
    await monitor.stop()  # Should not raise
    assert monitor._running is False


@pytest.mark.asyncio
async def test_health_monitor_initial_check_failure():
    """Start handles initial check failure gracefully."""
    monitor = OverlayHealthMonitor(interval=300)

    with patch.object(
        monitor,
        "check_and_repair",
        new_callable=AsyncMock,
        side_effect=RuntimeError("initial check failed"),
    ):
        # Should not raise despite check failure
        await monitor.start()
        assert monitor._running is True

        await monitor.stop()


@pytest.mark.asyncio
async def test_health_monitor_repair_preserves_tenant_mtu(tmp_path):
    """Repaired tunnel uses the original tenant_mtu value."""
    overlay = _make_overlay(tmp_path)

    overlay._link_tunnels["link-1"] = LinkTunnel(
        link_id="link-1",
        vni=50000,
        local_ip="10.0.0.1",
        remote_ip="10.0.0.2",
        local_vlan=3001,
        interface_name="vxlan-mtu12345",
        lab_id="lab-1",
        tenant_mtu=9000,
    )

    monitor = OverlayHealthMonitor(interval=60)

    with patch("agent.agent_state.get_overlay_manager", return_value=overlay), \
         patch(
             "agent.network.overlay_state.batch_read_ovs_ports",
             new_callable=AsyncMock,
             return_value={
                 "vxlan-mtu12345": {"name": "vxlan-mtu12345", "tag": 3001, "type": "vxlan", "ofport": -1},
             },
         ):
        await monitor.check_and_repair()

    call_kwargs = overlay._create_vxlan_device.call_args.kwargs
    assert call_kwargs["tenant_mtu"] == 9000


@pytest.mark.asyncio
async def test_health_monitor_ovs_query_failure(tmp_path):
    """Returns skipped result when batch_read_ovs_ports returns None."""
    overlay = _make_overlay(tmp_path)
    overlay._link_tunnels["link-1"] = LinkTunnel(
        link_id="link-1",
        vni=50000,
        local_ip="10.0.0.1",
        remote_ip="10.0.0.2",
        local_vlan=3001,
        interface_name="vxlan-abc12345",
        lab_id="lab-1",
        tenant_mtu=1400,
    )

    monitor = OverlayHealthMonitor(interval=60)

    with patch("agent.agent_state.get_overlay_manager", return_value=overlay), \
         patch(
             "agent.network.overlay_state.batch_read_ovs_ports",
             new_callable=AsyncMock,
             return_value=None,
         ):
        result = await monitor.check_and_repair()

    assert result.get("skipped") == "ovs_read_error"
    assert result["checked"] == 0
    overlay._create_vxlan_device.assert_not_called()


@pytest.mark.asyncio
async def test_health_monitor_concurrent_removal_guard(tmp_path):
    """Skips recreation when tunnel is removed between iteration and await."""
    overlay = _make_overlay(tmp_path)

    overlay._link_tunnels["link-1"] = LinkTunnel(
        link_id="link-1",
        vni=50000,
        local_ip="10.0.0.1",
        remote_ip="10.0.0.2",
        local_vlan=3001,
        interface_name="vxlan-race12345",
        lab_id="lab-1",
        tenant_mtu=1400,
    )

    # Remove the tunnel during the del-port call
    original_ovs = overlay._ovs_vsctl

    async def _race_del_port(*args):
        if args[0] == "del-port":
            overlay._link_tunnels.pop("link-1", None)
        return (0, "", "")

    overlay._ovs_vsctl = AsyncMock(side_effect=_race_del_port)

    monitor = OverlayHealthMonitor(interval=60)

    with patch("agent.agent_state.get_overlay_manager", return_value=overlay), \
         patch(
             "agent.network.overlay_state.batch_read_ovs_ports",
             new_callable=AsyncMock,
             return_value={
                 "vxlan-race12345": {"name": "vxlan-race12345", "tag": 3001, "type": "vxlan", "ofport": -1},
             },
         ):
        result = await monitor.check_and_repair()

    assert result["repaired"] == 0
    overlay._create_vxlan_device.assert_not_called()


@pytest.mark.asyncio
async def test_health_monitor_interval_stored():
    """Constructor stores custom interval."""
    monitor = OverlayHealthMonitor(interval=120)
    assert monitor._interval == 120


@pytest.mark.asyncio
async def test_health_monitor_default_interval():
    """Default interval is 60 seconds."""
    monitor = OverlayHealthMonitor()
    assert monitor._interval == 60
