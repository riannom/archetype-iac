"""Tests for OverlayHealthMonitor VXLAN self-healing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from agent.network.overlay_health import OverlayHealthMonitor


def _make_overlay(tmp_path: Path):
    """Create a mock OverlayManager with basic attributes."""
    from agent.network.overlay import OverlayManager

    with patch("agent.network.overlay.settings") as mock_settings:
        mock_settings.workspace_path = str(tmp_path)
        mock_settings.vxlan_vni_base = 100000
        mock_settings.vxlan_vni_max = 200000
        mock_settings.overlay_mtu = 1400
        mock_settings.ovs_bridge_name = "arch-ovs"
        mock_settings.enable_vxlan = True
        overlay = OverlayManager.__new__(OverlayManager)
        overlay._bridge_name = "arch-ovs"
        overlay._link_tunnels = {}
        overlay._vteps = {}
        overlay._mtu_cache = {}
        overlay._data_plane_ip = "10.0.0.1"
        overlay._cleanup_mgr = None
        overlay._run_cmd = AsyncMock(return_value=(0, "", ""))
        overlay._ovs_vsctl = AsyncMock(return_value=(0, "", ""))
        overlay._ip_link_exists = AsyncMock(return_value=False)
        overlay._ensure_ovs_bridge = AsyncMock()
        overlay._create_vxlan_device = AsyncMock()
        overlay._delete_vxlan_device = AsyncMock()
    return overlay


@pytest.mark.asyncio
async def test_repair_broken_tunnel(tmp_path):
    """Health monitor repairs tunnel with ofport=-1."""
    overlay = _make_overlay(tmp_path)

    from agent.network.overlay import LinkTunnel
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

    with patch(
        "agent.agent_state.get_overlay_manager",
        return_value=overlay,
    ), patch(
        "agent.network.overlay_state.batch_read_ovs_ports",
        new_callable=AsyncMock,
        return_value={
            "vxlan-abc12345": {
                "name": "vxlan-abc12345",
                "tag": 3001,
                "type": "vxlan",
                "ofport": -1,
            },
        },
    ):
        result = await monitor.check_and_repair()

    assert result["checked"] == 1
    assert result["repaired"] == 1

    # Stale port deleted, then device recreated
    overlay._ovs_vsctl.assert_any_call("del-port", "arch-ovs", "vxlan-abc12345")
    overlay._create_vxlan_device.assert_called_once()
    call_kwargs = overlay._create_vxlan_device.call_args.kwargs
    assert call_kwargs["name"] == "vxlan-abc12345"
    assert call_kwargs["vni"] == 50000
    assert call_kwargs["vlan_tag"] == 3001


@pytest.mark.asyncio
async def test_healthy_tunnels_not_repaired(tmp_path):
    """Health monitor skips tunnels with valid ofport."""
    overlay = _make_overlay(tmp_path)

    from agent.network.overlay import LinkTunnel
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

    with patch(
        "agent.agent_state.get_overlay_manager",
        return_value=overlay,
    ), patch(
        "agent.network.overlay_state.batch_read_ovs_ports",
        new_callable=AsyncMock,
        return_value={
            "vxlan-abc12345": {
                "name": "vxlan-abc12345",
                "tag": 3001,
                "type": "vxlan",
                "ofport": 10,
            },
        },
    ):
        result = await monitor.check_and_repair()

    assert result["checked"] == 1
    assert result["repaired"] == 0
    overlay._create_vxlan_device.assert_not_called()


@pytest.mark.asyncio
async def test_missing_ovs_port_repaired(tmp_path):
    """Tunnel tracked in memory but missing from OVS is recreated."""
    overlay = _make_overlay(tmp_path)

    from agent.network.overlay import LinkTunnel
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

    with patch(
        "agent.agent_state.get_overlay_manager",
        return_value=overlay,
    ), patch(
        "agent.network.overlay_state.batch_read_ovs_ports",
        new_callable=AsyncMock,
        return_value={},  # Port not in OVS at all
    ):
        result = await monitor.check_and_repair()

    assert result["checked"] == 1
    assert result["repaired"] == 1

    # Should recreate without del-port (nothing to delete)
    del_port_calls = [
        c for c in overlay._ovs_vsctl.call_args_list
        if len(c.args) >= 2 and c.args[0] == "del-port"
    ]
    assert len(del_port_calls) == 0
    overlay._create_vxlan_device.assert_called_once()
    call_kwargs = overlay._create_vxlan_device.call_args.kwargs
    assert call_kwargs["name"] == "vxlan-abc12345"
    assert call_kwargs["vni"] == 50000


@pytest.mark.asyncio
async def test_no_tunnels_tracked(tmp_path):
    """No-op when overlay manager has no tracked tunnels."""
    overlay = _make_overlay(tmp_path)

    monitor = OverlayHealthMonitor(interval=60)

    with patch(
        "agent.agent_state.get_overlay_manager",
        return_value=overlay,
    ):
        result = await monitor.check_and_repair()

    assert result["checked"] == 0
    assert result["repaired"] == 0


@pytest.mark.asyncio
async def test_repair_failure_does_not_crash(tmp_path):
    """Repair failure for one tunnel doesn't prevent checking others."""
    overlay = _make_overlay(tmp_path)

    from agent.network.overlay import LinkTunnel
    overlay._link_tunnels["link-1"] = LinkTunnel(
        link_id="link-1",
        vni=50000,
        local_ip="10.0.0.1",
        remote_ip="10.0.0.2",
        local_vlan=3001,
        interface_name="vxlan-fail1234",
        lab_id="lab-1",
        tenant_mtu=1400,
    )
    overlay._link_tunnels["link-2"] = LinkTunnel(
        link_id="link-2",
        vni=50001,
        local_ip="10.0.0.1",
        remote_ip="10.0.0.3",
        local_vlan=3002,
        interface_name="vxlan-ok567890",
        lab_id="lab-1",
        tenant_mtu=1400,
    )

    # First create call fails, second succeeds
    call_count = 0
    async def _create_or_fail(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("OVS error")

    overlay._create_vxlan_device = AsyncMock(side_effect=_create_or_fail)

    monitor = OverlayHealthMonitor(interval=60)

    with patch(
        "agent.agent_state.get_overlay_manager",
        return_value=overlay,
    ), patch(
        "agent.network.overlay_state.batch_read_ovs_ports",
        new_callable=AsyncMock,
        return_value={
            "vxlan-fail1234": {"name": "vxlan-fail1234", "tag": 3001, "type": "vxlan", "ofport": -1},
            "vxlan-ok567890": {"name": "vxlan-ok567890", "tag": 3002, "type": "vxlan", "ofport": -1},
        },
    ):
        result = await monitor.check_and_repair()

    assert result["checked"] == 2
    assert result["repaired"] == 1  # Only the second one succeeded


@pytest.mark.asyncio
async def test_start_stop():
    """Monitor can be started and stopped cleanly."""
    monitor = OverlayHealthMonitor(interval=300)

    # Patch check_and_repair to prevent actual work
    with patch.object(monitor, "check_and_repair", new_callable=AsyncMock):
        await monitor.start()
        assert monitor._running is True
        assert monitor._task is not None

        await monitor.stop()
        assert monitor._running is False
        assert monitor._task is None
