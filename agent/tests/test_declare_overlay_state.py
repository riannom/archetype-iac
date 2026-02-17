"""Tests for agent-side overlay declare-state convergence.

Covers:
- OverlayManager.declare_state(): convergence logic (create/update/converge/orphan)
- _batch_read_ovs_ports(): OVS state batch reader
- Local cache write/load for API-less recovery
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from agent.network.overlay import OverlayManager


# ─── Helpers ──────────────────────────────────────────────────────────────


def _make_overlay(tmp_path: Path) -> OverlayManager:
    """Create an OverlayManager with mocked dependencies."""
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


def _tunnel_dict(
    link_id: str = "R1:eth1-R2:eth1",
    lab_id: str = "lab-1",
    vni: int = 50000,
    local_ip: str = "10.0.0.1",
    remote_ip: str = "10.0.0.2",
    expected_vlan: int = 3001,
    port_name: str = "vxlan-abc12345",
    mtu: int = 0,
) -> dict:
    return {
        "link_id": link_id,
        "lab_id": lab_id,
        "vni": vni,
        "local_ip": local_ip,
        "remote_ip": remote_ip,
        "expected_vlan": expected_vlan,
        "port_name": port_name,
        "mtu": mtu,
    }


# ─── declare_state convergence tests ─────────────────────────────────────


@pytest.mark.asyncio
async def test_declare_creates_missing_tunnels(tmp_path):
    """Missing VXLAN port is created with correct VNI and VLAN."""
    overlay = _make_overlay(tmp_path)

    # No existing OVS ports
    overlay._batch_read_ovs_ports = AsyncMock(return_value={})
    overlay._write_declared_state_cache = AsyncMock()

    result = await overlay.declare_state([
        _tunnel_dict(port_name="vxlan-abc12345", vni=50000, expected_vlan=3001),
    ])

    assert len(result["results"]) == 1
    assert result["results"][0]["status"] == "created"
    assert result["results"][0]["link_id"] == "R1:eth1-R2:eth1"

    # VXLAN device should have been created
    overlay._create_vxlan_device.assert_called_once()
    call_kwargs = overlay._create_vxlan_device.call_args
    assert call_kwargs.kwargs["name"] == "vxlan-abc12345"
    assert call_kwargs.kwargs["vni"] == 50000
    assert call_kwargs.kwargs["vlan_tag"] == 3001

    # In-memory tracking updated
    assert "R1:eth1-R2:eth1" in overlay._link_tunnels


@pytest.mark.asyncio
async def test_declare_updates_drifted_vlan(tmp_path):
    """Port with wrong VLAN tag gets updated."""
    overlay = _make_overlay(tmp_path)

    # Existing port with wrong VLAN
    overlay._batch_read_ovs_ports = AsyncMock(return_value={
        "vxlan-abc12345": {"name": "vxlan-abc12345", "tag": 3099, "type": "vxlan"},
    })
    overlay._write_declared_state_cache = AsyncMock()

    result = await overlay.declare_state([
        _tunnel_dict(port_name="vxlan-abc12345", expected_vlan=3001),
    ])

    assert result["results"][0]["status"] == "updated"

    # OVS command to update VLAN tag
    overlay._ovs_vsctl.assert_any_call(
        "set", "port", "vxlan-abc12345", "tag=3001"
    )


@pytest.mark.asyncio
async def test_declare_converged_no_changes(tmp_path):
    """Port with correct VLAN tag needs no changes."""
    overlay = _make_overlay(tmp_path)

    overlay._batch_read_ovs_ports = AsyncMock(return_value={
        "vxlan-abc12345": {"name": "vxlan-abc12345", "tag": 3001, "type": "vxlan"},
    })
    overlay._write_declared_state_cache = AsyncMock()

    result = await overlay.declare_state([
        _tunnel_dict(port_name="vxlan-abc12345", expected_vlan=3001),
    ])

    assert result["results"][0]["status"] == "converged"

    # No VXLAN device creation or VLAN update
    overlay._create_vxlan_device.assert_not_called()
    # Only the _batch_read_ovs_ports call, no "set port" calls
    set_calls = [
        c for c in overlay._ovs_vsctl.call_args_list
        if len(c.args) >= 2 and c.args[0] == "set"
    ]
    assert len(set_calls) == 0


@pytest.mark.asyncio
async def test_declare_removes_orphans(tmp_path):
    """VXLAN ports not in declared set are removed for declared labs."""
    overlay = _make_overlay(tmp_path)

    # Existing orphan port (not in declared set)
    overlay._batch_read_ovs_ports = AsyncMock(return_value={
        "vxlan-abc12345": {"name": "vxlan-abc12345", "tag": 3001, "type": "vxlan"},
        "vxlan-orphan99": {"name": "vxlan-orphan99", "tag": 3002, "type": "vxlan"},
    })
    overlay._write_declared_state_cache = AsyncMock()

    result = await overlay.declare_state([
        _tunnel_dict(port_name="vxlan-abc12345"),
    ])

    assert "vxlan-orphan99" in result["orphans_removed"]
    overlay._delete_vxlan_device.assert_called_once_with("vxlan-orphan99", "arch-ovs")


@pytest.mark.asyncio
async def test_declare_orphan_scoped_to_declared_labs(tmp_path):
    """Orphan cleanup only applies to ports from declared labs."""
    overlay = _make_overlay(tmp_path)

    # Simulate a tracked port from a different lab
    from agent.network.overlay import LinkTunnel
    overlay._link_tunnels["other-link"] = LinkTunnel(
        link_id="other-link",
        vni=60000,
        local_ip="10.0.0.1",
        remote_ip="10.0.0.3",
        local_vlan=3099,
        interface_name="vxlan-otherlab",
        lab_id="lab-OTHER",
        tenant_mtu=1400,
    )

    overlay._batch_read_ovs_ports = AsyncMock(return_value={
        "vxlan-abc12345": {"name": "vxlan-abc12345", "tag": 3001, "type": "vxlan"},
        "vxlan-otherlab": {"name": "vxlan-otherlab", "tag": 3099, "type": "vxlan"},
    })
    overlay._write_declared_state_cache = AsyncMock()

    result = await overlay.declare_state([
        _tunnel_dict(port_name="vxlan-abc12345", lab_id="lab-1"),
    ])

    # vxlan-otherlab belongs to lab-OTHER, not lab-1 → should NOT be removed
    assert "vxlan-otherlab" not in result["orphans_removed"]
    overlay._delete_vxlan_device.assert_not_called()


@pytest.mark.asyncio
async def test_declare_idempotent(tmp_path):
    """Calling declare_state twice with same input produces same result."""
    overlay = _make_overlay(tmp_path)

    overlay._batch_read_ovs_ports = AsyncMock(return_value={})
    overlay._write_declared_state_cache = AsyncMock()

    tunnel = _tunnel_dict()

    # First call — creates
    result1 = await overlay.declare_state([tunnel])
    assert result1["results"][0]["status"] == "created"

    # Now the port "exists" (from in-memory tracking + mock)
    overlay._batch_read_ovs_ports = AsyncMock(return_value={
        "vxlan-abc12345": {"name": "vxlan-abc12345", "tag": 3001, "type": "vxlan"},
    })

    # Second call — converged
    result2 = await overlay.declare_state([tunnel])
    assert result2["results"][0]["status"] == "converged"


@pytest.mark.asyncio
async def test_declare_partial_failure(tmp_path):
    """Some tunnels succeed while others fail."""
    overlay = _make_overlay(tmp_path)

    overlay._batch_read_ovs_ports = AsyncMock(return_value={})
    overlay._write_declared_state_cache = AsyncMock()

    # First tunnel succeeds, second fails
    call_count = 0
    async def _create_or_fail(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise Exception("OVS bridge error")

    overlay._create_vxlan_device = AsyncMock(side_effect=_create_or_fail)

    result = await overlay.declare_state([
        _tunnel_dict(link_id="link-ok", port_name="vxlan-ok123456"),
        _tunnel_dict(link_id="link-fail", port_name="vxlan-fail1234"),
    ])

    statuses = {r["link_id"]: r["status"] for r in result["results"]}
    assert statuses["link-ok"] == "created"
    assert statuses["link-fail"] == "error"

    # Only successful tunnel in tracking
    assert "link-ok" in overlay._link_tunnels
    assert "link-fail" not in overlay._link_tunnels


@pytest.mark.asyncio
async def test_declare_empty_declaration(tmp_path):
    """Empty declaration for a lab is valid (cleanup-only)."""
    overlay = _make_overlay(tmp_path)

    overlay._batch_read_ovs_ports = AsyncMock(return_value={})
    overlay._write_declared_state_cache = AsyncMock()

    result = await overlay.declare_state([])

    assert result["results"] == []
    assert result["orphans_removed"] == []


@pytest.mark.asyncio
async def test_declare_updates_link_tunnels_dict(tmp_path):
    """declare_state updates _link_tunnels with real link_id."""
    overlay = _make_overlay(tmp_path)

    overlay._batch_read_ovs_ports = AsyncMock(return_value={})
    overlay._write_declared_state_cache = AsyncMock()

    await overlay.declare_state([
        _tunnel_dict(link_id="R1:eth1-R2:eth1", vni=50000),
        _tunnel_dict(link_id="R3:eth1-R4:eth1", vni=50001, port_name="vxlan-def67890"),
    ])

    assert "R1:eth1-R2:eth1" in overlay._link_tunnels
    assert "R3:eth1-R4:eth1" in overlay._link_tunnels

    lt = overlay._link_tunnels["R1:eth1-R2:eth1"]
    assert lt.vni == 50000
    assert lt.local_vlan == 3001
    assert lt.interface_name == "vxlan-abc12345"


@pytest.mark.asyncio
async def test_declare_writes_local_cache(tmp_path):
    """declare_state writes cache file to workspace."""
    overlay = _make_overlay(tmp_path)

    overlay._batch_read_ovs_ports = AsyncMock(return_value={})

    # Use real cache writer
    with patch("agent.network.overlay.settings") as mock_settings:
        mock_settings.workspace_path = str(tmp_path)
        mock_settings.overlay_mtu = 1400

        tunnels = [_tunnel_dict()]
        await overlay.declare_state(tunnels)

    cache_path = tmp_path / "declared_overlay_state.json"
    assert cache_path.exists()

    cache_data = json.loads(cache_path.read_text())
    assert "declared_at" in cache_data
    assert len(cache_data["tunnels"]) == 1
    assert cache_data["tunnels"][0]["link_id"] == "R1:eth1-R2:eth1"


@pytest.mark.asyncio
async def test_cache_load_on_startup(tmp_path):
    """Cache file can be loaded for recovery."""
    overlay = _make_overlay(tmp_path)

    # Write cache file
    cache_data = {
        "declared_at": "2026-02-14T22:40:00Z",
        "tunnels": [_tunnel_dict()],
    }
    cache_path = tmp_path / "declared_overlay_state.json"
    cache_path.write_text(json.dumps(cache_data))

    with patch("agent.network.overlay.settings") as mock_settings:
        mock_settings.workspace_path = str(tmp_path)

        loaded = await overlay.load_declared_state_cache()

    assert loaded is not None
    assert len(loaded) == 1
    assert loaded[0]["link_id"] == "R1:eth1-R2:eth1"


@pytest.mark.asyncio
async def test_declare_overwrites_cache_recovery(tmp_path):
    """New declare-state call overwrites stale cache."""
    overlay = _make_overlay(tmp_path)

    # Write old cache
    old_cache = {
        "declared_at": "2026-01-01T00:00:00Z",
        "tunnels": [_tunnel_dict(link_id="old-link")],
    }
    cache_path = tmp_path / "declared_overlay_state.json"
    cache_path.write_text(json.dumps(old_cache))

    overlay._batch_read_ovs_ports = AsyncMock(return_value={})

    # declare_state with new tunnels
    new_tunnels = [_tunnel_dict(link_id="new-link")]

    with patch("agent.network.overlay.settings") as mock_settings:
        mock_settings.workspace_path = str(tmp_path)
        mock_settings.overlay_mtu = 1400

        await overlay.declare_state(new_tunnels)

    # Cache should be overwritten
    updated_cache = json.loads(cache_path.read_text())
    assert len(updated_cache["tunnels"]) == 1
    assert updated_cache["tunnels"][0]["link_id"] == "new-link"
    assert updated_cache["declared_at"] != "2026-01-01T00:00:00Z"
