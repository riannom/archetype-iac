"""Extended tests for overlay state management (overlay_state.py).

Covers:
- batch_read_ovs_ports: OVS JSON parsing, filtering, edge cases
- write/load_declared_state_cache: persistence round-trip, corruption
- declare_state: convergence, creation, orphan cleanup, error handling
- recover_link_tunnels: cache-based recovery, OVS fallback, edge cases
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from agent.config import settings
from agent.network.overlay import LinkTunnel, OverlayManager
from agent.network.overlay_state import (
    batch_read_ovs_ports,
    declare_state,
    load_declared_state_cache,
    recover_link_tunnels,
    write_declared_state_cache,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_manager(tmp_path: Path) -> OverlayManager:
    """Create an OverlayManager with mocked heavy-weight dependencies."""
    manager = OverlayManager.__new__(OverlayManager)
    manager._bridge_name = "arch-ovs"
    manager._link_tunnels = {}
    manager._vteps = {}
    manager._mtu_cache = {}
    manager._ovs_initialized = False
    manager._run_cmd = AsyncMock(return_value=(0, "", ""))
    manager._ovs_vsctl = AsyncMock(return_value=(0, "", ""))
    manager._ip_link_exists = AsyncMock(return_value=False)
    manager._ensure_ovs_bridge = AsyncMock()
    manager._create_vxlan_device = AsyncMock()
    manager._delete_vxlan_device = AsyncMock()
    manager._write_declared_state_cache = AsyncMock()
    manager._batch_read_ovs_ports = AsyncMock(return_value={})
    return manager


def _tunnel_dict(
    link_id: str = "link-1",
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


# ===================================================================
# batch_read_ovs_ports
# ===================================================================


@pytest.mark.asyncio
async def test_batch_read_no_vxlan_ports():
    """Returns empty dict when bridge has no vxlan-* ports."""
    with patch(
        "agent.network.overlay_state._shared_ovs_vsctl",
        new_callable=AsyncMock,
        return_value=(0, "eth0\nbr-int\n", ""),
    ):
        result = await batch_read_ovs_ports("arch-ovs")
    assert result == {}


@pytest.mark.asyncio
async def test_batch_read_list_ports_failure():
    """Returns None when list-ports command fails."""
    with patch(
        "agent.network.overlay_state._shared_ovs_vsctl",
        new_callable=AsyncMock,
        return_value=(1, "", "ovs-vsctl: error"),
    ):
        result = await batch_read_ovs_ports("arch-ovs")
    assert result is None


@pytest.mark.asyncio
async def test_batch_read_parses_ports_and_interfaces():
    """Correctly merges port tags with interface type/ofport."""

    async def _mock_ovs(*args):
        if args[0] == "list-ports":
            return (0, "vxlan-aaa\nvxlan-bbb\neth0\n", "")
        if args[0] == "--format=json":
            if "Port" in args:
                data = {
                    "data": [
                        ["vxlan-aaa", 3001],
                        ["vxlan-bbb", 3002],
                        ["eth0", 0],
                    ]
                }
                return (0, json.dumps(data), "")
            if "Interface" in args:
                data = {
                    "data": [
                        ["vxlan-aaa", "vxlan", 10],
                        ["vxlan-bbb", "vxlan", 11],
                        ["eth0", "system", 1],
                    ]
                }
                return (0, json.dumps(data), "")
        return (0, "", "")

    with patch(
        "agent.network.overlay_state._shared_ovs_vsctl",
        new_callable=AsyncMock,
        side_effect=_mock_ovs,
    ):
        result = await batch_read_ovs_ports("arch-ovs")

    assert result is not None
    assert len(result) == 2
    assert result["vxlan-aaa"]["tag"] == 3001
    assert result["vxlan-aaa"]["type"] == "vxlan"
    assert result["vxlan-aaa"]["ofport"] == 10
    assert result["vxlan-bbb"]["tag"] == 3002


@pytest.mark.asyncio
async def test_batch_read_zero_tag_for_non_int():
    """Non-integer VLAN tag gets stored as 0."""

    async def _mock_ovs(*args):
        if args[0] == "list-ports":
            return (0, "vxlan-aaa\n", "")
        if args[0] == "--format=json":
            if "Port" in args:
                data = {"data": [["vxlan-aaa", []]]}
                return (0, json.dumps(data), "")
            if "Interface" in args:
                data = {"data": [["vxlan-aaa", "vxlan", 10]]}
                return (0, json.dumps(data), "")
        return (0, "", "")

    with patch(
        "agent.network.overlay_state._shared_ovs_vsctl",
        new_callable=AsyncMock,
        side_effect=_mock_ovs,
    ):
        result = await batch_read_ovs_ports("arch-ovs")

    assert result["vxlan-aaa"]["tag"] == 0


@pytest.mark.asyncio
async def test_batch_read_missing_interface_info():
    """Port without matching interface gets defaults (-1 ofport)."""

    async def _mock_ovs(*args):
        if args[0] == "list-ports":
            return (0, "vxlan-aaa\n", "")
        if args[0] == "--format=json":
            if "Port" in args:
                data = {"data": [["vxlan-aaa", 3001]]}
                return (0, json.dumps(data), "")
            if "Interface" in args:
                # No interface data for vxlan-aaa
                data = {"data": []}
                return (0, json.dumps(data), "")
        return (0, "", "")

    with patch(
        "agent.network.overlay_state._shared_ovs_vsctl",
        new_callable=AsyncMock,
        side_effect=_mock_ovs,
    ):
        result = await batch_read_ovs_ports("arch-ovs")

    assert result["vxlan-aaa"]["type"] == ""
    assert result["vxlan-aaa"]["ofport"] == -1


@pytest.mark.asyncio
async def test_batch_read_malformed_json():
    """Gracefully handles malformed JSON from OVS."""

    async def _mock_ovs(*args):
        if args[0] == "list-ports":
            return (0, "vxlan-aaa\n", "")
        if args[0] == "--format=json":
            return (0, "NOT VALID JSON", "")
        return (0, "", "")

    with patch(
        "agent.network.overlay_state._shared_ovs_vsctl",
        new_callable=AsyncMock,
        side_effect=_mock_ovs,
    ):
        result = await batch_read_ovs_ports("arch-ovs")

    # Both JSON parses fail, port_tags is empty, returns empty result
    assert result == {}


@pytest.mark.asyncio
async def test_batch_read_empty_bridge_output():
    """Returns empty dict when bridge output is empty (no ports at all)."""
    with patch(
        "agent.network.overlay_state._shared_ovs_vsctl",
        new_callable=AsyncMock,
        return_value=(0, "", ""),
    ):
        result = await batch_read_ovs_ports("arch-ovs")
    assert result == {}


# ===================================================================
# write_declared_state_cache / load_declared_state_cache
# ===================================================================


@pytest.mark.asyncio
async def test_cache_round_trip(tmp_path, monkeypatch):
    """Write and load cache produce the same tunnels."""
    monkeypatch.setattr(settings, "workspace_path", str(tmp_path))

    tunnels = [_tunnel_dict(), _tunnel_dict(link_id="link-2", port_name="vxlan-def67890")]

    await write_declared_state_cache(tunnels)

    loaded = await load_declared_state_cache()
    assert loaded is not None
    assert len(loaded) == 2
    assert loaded[0]["link_id"] == "link-1"
    assert loaded[1]["link_id"] == "link-2"


@pytest.mark.asyncio
async def test_load_cache_missing_file(tmp_path, monkeypatch):
    """Returns None when cache file does not exist."""
    monkeypatch.setattr(settings, "workspace_path", str(tmp_path))

    result = await load_declared_state_cache()
    assert result is None


@pytest.mark.asyncio
async def test_load_cache_empty_tunnels(tmp_path, monkeypatch):
    """Returns None when cache has empty tunnels list."""
    monkeypatch.setattr(settings, "workspace_path", str(tmp_path))

    cache_path = tmp_path / "declared_overlay_state.json"
    cache_path.write_text(json.dumps({"declared_at": "2026-01-01", "tunnels": []}))

    result = await load_declared_state_cache()
    assert result is None


@pytest.mark.asyncio
async def test_load_cache_corrupted_json(tmp_path, monkeypatch):
    """Returns None on corrupted JSON."""
    monkeypatch.setattr(settings, "workspace_path", str(tmp_path))

    cache_path = tmp_path / "declared_overlay_state.json"
    cache_path.write_text("{BAD JSON")

    result = await load_declared_state_cache()
    assert result is None


@pytest.mark.asyncio
async def test_write_cache_creates_parent_dirs(tmp_path, monkeypatch):
    """Write cache creates intermediate directories."""
    deep_path = tmp_path / "deep" / "nested"
    monkeypatch.setattr(settings, "workspace_path", str(deep_path))

    await write_declared_state_cache([_tunnel_dict()])

    cache_file = deep_path / "declared_overlay_state.json"
    assert cache_file.exists()


@pytest.mark.asyncio
async def test_write_cache_exception_handled(tmp_path, monkeypatch):
    """Write cache handles exceptions gracefully (no crash)."""
    # Point to a read-only path
    monkeypatch.setattr(settings, "workspace_path", "/proc/nonexistent")

    # Should not raise
    await write_declared_state_cache([_tunnel_dict()])


# ===================================================================
# declare_state
# ===================================================================


@pytest.mark.asyncio
async def test_declare_state_converged(tmp_path):
    """Port with matching VNI and VLAN reports 'converged'."""
    manager = _make_manager(tmp_path)
    manager._batch_read_ovs_ports = AsyncMock(return_value={
        "vxlan-abc12345": {
            "name": "vxlan-abc12345",
            "tag": 3001,
            "type": "vxlan",
            "ofport": 10,
        }
    })

    result = await declare_state(manager, [_tunnel_dict()])

    assert len(result["results"]) == 1
    assert result["results"][0]["status"] == "converged"
    assert "link-1" in manager._link_tunnels


@pytest.mark.asyncio
async def test_declare_state_vlan_update(tmp_path):
    """Port with wrong VLAN tag gets updated."""
    manager = _make_manager(tmp_path)
    manager._batch_read_ovs_ports = AsyncMock(return_value={
        "vxlan-abc12345": {
            "name": "vxlan-abc12345",
            "tag": 9999,  # Wrong VLAN
            "type": "vxlan",
            "ofport": 10,
        }
    })

    result = await declare_state(manager, [_tunnel_dict()])

    assert result["results"][0]["status"] == "updated"
    manager._ovs_vsctl.assert_any_call("set", "port", "vxlan-abc12345", "tag=3001")


@pytest.mark.asyncio
async def test_declare_state_creates_missing_port(tmp_path):
    """Missing port is created from scratch."""
    manager = _make_manager(tmp_path)
    manager._batch_read_ovs_ports = AsyncMock(return_value={})

    result = await declare_state(manager, [_tunnel_dict()])

    assert result["results"][0]["status"] == "created"
    manager._create_vxlan_device.assert_called_once()
    call_kwargs = manager._create_vxlan_device.call_args.kwargs
    assert call_kwargs["name"] == "vxlan-abc12345"
    assert call_kwargs["vni"] == 50000
    assert call_kwargs["vlan_tag"] == 3001


@pytest.mark.asyncio
async def test_declare_state_broken_ofport_recreates(tmp_path):
    """Port with ofport=-1 is deleted and recreated."""
    manager = _make_manager(tmp_path)
    manager._batch_read_ovs_ports = AsyncMock(return_value={
        "vxlan-abc12345": {
            "name": "vxlan-abc12345",
            "tag": 3001,
            "type": "vxlan",
            "ofport": -1,
        }
    })

    result = await declare_state(manager, [_tunnel_dict()])

    assert result["results"][0]["status"] == "created"
    # Stale OVS port should be deleted first
    manager._ovs_vsctl.assert_any_call("del-port", "arch-ovs", "vxlan-abc12345")
    manager._create_vxlan_device.assert_called_once()


@pytest.mark.asyncio
async def test_declare_state_mtu_enforcement(tmp_path):
    """Existing port with wrong MTU gets updated."""
    manager = _make_manager(tmp_path)
    manager._batch_read_ovs_ports = AsyncMock(return_value={
        "vxlan-abc12345": {
            "name": "vxlan-abc12345",
            "tag": 3001,
            "type": "vxlan",
            "ofport": 10,
        }
    })
    # ip link show returns current MTU=1500
    manager._run_cmd = AsyncMock(return_value=(0, "mtu 1500 state UP", ""))

    tunnel = _tunnel_dict(mtu=9000)
    result = await declare_state(manager, [tunnel])

    # Should be "updated" because MTU changed
    assert result["results"][0]["status"] == "updated"


@pytest.mark.asyncio
async def test_declare_state_mtu_already_correct(tmp_path):
    """Port with correct MTU stays converged."""
    manager = _make_manager(tmp_path)
    manager._batch_read_ovs_ports = AsyncMock(return_value={
        "vxlan-abc12345": {
            "name": "vxlan-abc12345",
            "tag": 3001,
            "type": "vxlan",
            "ofport": 10,
        }
    })
    manager._run_cmd = AsyncMock(return_value=(0, "mtu 9000 state UP", ""))

    tunnel = _tunnel_dict(mtu=9000)
    result = await declare_state(manager, [tunnel])

    assert result["results"][0]["status"] == "converged"


@pytest.mark.asyncio
async def test_declare_state_error_handling(tmp_path):
    """Exception in tunnel processing reports 'error' status."""
    manager = _make_manager(tmp_path)
    manager._batch_read_ovs_ports = AsyncMock(return_value={})
    manager._create_vxlan_device = AsyncMock(
        side_effect=RuntimeError("OVS create failed")
    )
    manager._ip_link_exists = AsyncMock(return_value=False)

    result = await declare_state(manager, [_tunnel_dict()])

    assert result["results"][0]["status"] == "error"
    assert "OVS create failed" in result["results"][0]["error"]


@pytest.mark.asyncio
async def test_declare_state_ovs_read_failure(tmp_path):
    """Returns skipped result when OVS read fails."""
    manager = _make_manager(tmp_path)
    manager._batch_read_ovs_ports = AsyncMock(return_value=None)

    result = await declare_state(manager, [_tunnel_dict()])

    assert result.get("skipped") == "ovs_read_error"
    assert result["results"] == []


@pytest.mark.asyncio
async def test_declare_state_orphan_cleanup(tmp_path):
    """Orphan port scoped to declared labs is removed."""
    manager = _make_manager(tmp_path)

    # Pre-populate tracking with an orphan
    orphan_tunnel = LinkTunnel(
        link_id="orphan-link",
        vni=60000,
        local_ip="10.0.0.1",
        remote_ip="10.0.0.3",
        local_vlan=3099,
        interface_name="vxlan-orphan123",
        lab_id="lab-1",
        tenant_mtu=1400,
    )
    manager._link_tunnels["orphan-link"] = orphan_tunnel

    # OVS sees both the declared port and orphan port
    manager._batch_read_ovs_ports = AsyncMock(return_value={
        "vxlan-abc12345": {
            "name": "vxlan-abc12345",
            "tag": 3001,
            "type": "vxlan",
            "ofport": 10,
        },
        "vxlan-orphan123": {
            "name": "vxlan-orphan123",
            "tag": 3099,
            "type": "vxlan",
            "ofport": 12,
        },
    })

    tunnel = _tunnel_dict()
    result = await declare_state(manager, [tunnel], declared_labs=["lab-1"])

    assert "vxlan-orphan123" in result["orphans_removed"]
    manager._delete_vxlan_device.assert_called_once_with("vxlan-orphan123", "arch-ovs")
    assert "orphan-link" not in manager._link_tunnels


@pytest.mark.asyncio
async def test_declare_state_orphan_different_lab_not_removed(tmp_path):
    """Orphan port from a different lab is NOT removed."""
    manager = _make_manager(tmp_path)

    orphan_tunnel = LinkTunnel(
        link_id="other-lab-link",
        vni=60000,
        local_ip="10.0.0.1",
        remote_ip="10.0.0.3",
        local_vlan=3099,
        interface_name="vxlan-otherlab1",
        lab_id="lab-other",
        tenant_mtu=1400,
    )
    manager._link_tunnels["other-lab-link"] = orphan_tunnel

    manager._batch_read_ovs_ports = AsyncMock(return_value={
        "vxlan-abc12345": {
            "name": "vxlan-abc12345",
            "tag": 3001,
            "type": "vxlan",
            "ofport": 10,
        },
        "vxlan-otherlab1": {
            "name": "vxlan-otherlab1",
            "tag": 3099,
            "type": "vxlan",
            "ofport": 12,
        },
    })

    tunnel = _tunnel_dict()
    result = await declare_state(manager, [tunnel], declared_labs=["lab-1"])

    assert result["orphans_removed"] == []
    assert "other-lab-link" in manager._link_tunnels


@pytest.mark.asyncio
async def test_declare_state_multiple_tunnels(tmp_path):
    """Process multiple tunnels in one call."""
    manager = _make_manager(tmp_path)
    manager._batch_read_ovs_ports = AsyncMock(return_value={
        "vxlan-aaa": {
            "name": "vxlan-aaa",
            "tag": 3001,
            "type": "vxlan",
            "ofport": 10,
        },
    })

    tunnels = [
        _tunnel_dict(link_id="link-1", port_name="vxlan-aaa"),
        _tunnel_dict(link_id="link-2", port_name="vxlan-bbb", expected_vlan=3002),
    ]

    result = await declare_state(manager, tunnels)

    statuses = {r["link_id"]: r["status"] for r in result["results"]}
    assert statuses["link-1"] == "converged"
    assert statuses["link-2"] == "created"


@pytest.mark.asyncio
async def test_declare_state_expected_vlan_zero(tmp_path):
    """Port with expected_vlan=0 is converged (no VLAN enforcement)."""
    manager = _make_manager(tmp_path)
    manager._batch_read_ovs_ports = AsyncMock(return_value={
        "vxlan-abc12345": {
            "name": "vxlan-abc12345",
            "tag": 0,
            "type": "vxlan",
            "ofport": 10,
        },
    })

    tunnel = _tunnel_dict(expected_vlan=0)
    result = await declare_state(manager, [tunnel])

    assert result["results"][0]["status"] == "converged"


@pytest.mark.asyncio
async def test_declare_state_stale_ip_link_deleted_before_create(tmp_path):
    """Stale Linux netdev is deleted before creation."""
    manager = _make_manager(tmp_path)
    manager._batch_read_ovs_ports = AsyncMock(return_value={})
    manager._ip_link_exists = AsyncMock(return_value=True)

    result = await declare_state(manager, [_tunnel_dict()])

    assert result["results"][0]["status"] == "created"
    # ip link delete should be called for stale device
    manager._run_cmd.assert_any_call(["ip", "link", "delete", "vxlan-abc12345"])
    manager._create_vxlan_device.assert_called_once()


@pytest.mark.asyncio
async def test_declare_state_updates_in_memory_tracking(tmp_path):
    """Declare state correctly updates _link_tunnels with tunnel metadata."""
    manager = _make_manager(tmp_path)
    manager._batch_read_ovs_ports = AsyncMock(return_value={})

    tunnel = _tunnel_dict(vni=77777, local_ip="192.168.1.1", remote_ip="192.168.1.2")
    await declare_state(manager, [tunnel])

    lt = manager._link_tunnels["link-1"]
    assert lt.vni == 77777
    assert lt.local_ip == "192.168.1.1"
    assert lt.remote_ip == "192.168.1.2"
    assert lt.lab_id == "lab-1"


@pytest.mark.asyncio
async def test_declare_state_writes_cache(tmp_path):
    """Declare state writes cache after processing."""
    manager = _make_manager(tmp_path)
    manager._batch_read_ovs_ports = AsyncMock(return_value={})

    tunnels = [_tunnel_dict()]
    await declare_state(manager, tunnels)

    manager._write_declared_state_cache.assert_called_once_with(tunnels)


# ===================================================================
# recover_link_tunnels
# ===================================================================


@pytest.mark.asyncio
async def test_recover_from_cache(tmp_path, monkeypatch):
    """Recovery uses declared-state cache when available."""
    monkeypatch.setattr(settings, "workspace_path", str(tmp_path))

    manager = _make_manager(tmp_path)

    cached_tunnels = [_tunnel_dict()]

    # Write cache to disk
    await write_declared_state_cache(cached_tunnels)

    # Manager.declare_state should be called with the cached data
    async def _mock_declare_state(tunnels, declared_labs=None):
        return {
            "results": [{"link_id": "link-1", "status": "converged"}],
            "orphans_removed": [],
        }

    manager.declare_state = AsyncMock(side_effect=_mock_declare_state)

    recovered = await recover_link_tunnels(manager)

    assert recovered == 1
    manager.declare_state.assert_called_once()


@pytest.mark.asyncio
async def test_recover_cache_failure_falls_back_to_ovs(tmp_path, monkeypatch):
    """When cache recovery fails, falls back to OVS scan."""
    monkeypatch.setattr(settings, "workspace_path", str(tmp_path))

    manager = _make_manager(tmp_path)

    # Write cache but make declare_state fail
    await write_declared_state_cache([_tunnel_dict()])
    manager.declare_state = AsyncMock(side_effect=RuntimeError("cache broken"))

    # Pre-populate a known mapping for fallback recovery
    existing_tunnel = LinkTunnel(
        link_id="link-1",
        vni=50000,
        local_ip="10.0.0.1",
        remote_ip="10.0.0.2",
        local_vlan=3001,
        interface_name="vxlan-abc12345",
        lab_id="lab-1",
        tenant_mtu=1400,
    )
    manager._link_tunnels["link-1"] = existing_tunnel

    # OVS fallback scan
    with patch(
        "agent.network.overlay_state._shared_ovs_vsctl",
        new_callable=AsyncMock,
    ) as mock_ovs, patch(
        "agent.network.overlay_vxlan.read_vxlan_link_info",
        new_callable=AsyncMock,
        return_value=(50000, "10.0.0.2", "10.0.0.1"),
    ):
        mock_ovs.side_effect = [
            (0, "vxlan-abc12345\n", ""),  # list-ports
            (0, "3001\n", ""),  # get tag
        ]
        recovered = await recover_link_tunnels(manager)

    assert recovered == 1
    assert "link-1" in manager._link_tunnels


@pytest.mark.asyncio
async def test_recover_no_cache_no_known_mappings(tmp_path, monkeypatch):
    """Recovery returns 0 when no cache and no known link_id mappings."""
    monkeypatch.setattr(settings, "workspace_path", str(tmp_path))

    manager = _make_manager(tmp_path)

    with patch(
        "agent.network.overlay_state._shared_ovs_vsctl",
        new_callable=AsyncMock,
        return_value=(0, "vxlan-unknown1\n", ""),
    ), patch(
        "agent.network.overlay_vxlan.read_vxlan_link_info",
        new_callable=AsyncMock,
        return_value=(50000, "10.0.0.2", "10.0.0.1"),
    ):
        # Even though OVS has vxlan ports, no known mappings exist
        # The get-tag call needs to be mocked too
        recovered = await recover_link_tunnels(manager)

    assert recovered == 0


@pytest.mark.asyncio
async def test_recover_ovs_scan_failure(tmp_path, monkeypatch):
    """Recovery handles OVS scan failure gracefully."""
    monkeypatch.setattr(settings, "workspace_path", str(tmp_path))

    manager = _make_manager(tmp_path)

    with patch(
        "agent.network.overlay_state._shared_ovs_vsctl",
        new_callable=AsyncMock,
        return_value=(1, "", "ovs error"),
    ):
        recovered = await recover_link_tunnels(manager)

    assert recovered == 0


@pytest.mark.asyncio
async def test_recover_skips_zero_vni(tmp_path, monkeypatch):
    """Recovery skips ports where VNI is 0 (can't identify link)."""
    monkeypatch.setattr(settings, "workspace_path", str(tmp_path))

    manager = _make_manager(tmp_path)
    manager._link_tunnels["link-1"] = LinkTunnel(
        link_id="link-1",
        vni=50000,
        local_ip="10.0.0.1",
        remote_ip="10.0.0.2",
        local_vlan=3001,
        interface_name="vxlan-abc12345",
        lab_id="lab-1",
        tenant_mtu=1400,
    )

    with patch(
        "agent.network.overlay_state._shared_ovs_vsctl",
        new_callable=AsyncMock,
    ) as mock_ovs, patch(
        "agent.network.overlay_vxlan.read_vxlan_link_info",
        new_callable=AsyncMock,
        return_value=(0, "", ""),  # VNI=0, no remote
    ):
        mock_ovs.side_effect = [
            (0, "vxlan-abc12345\n", ""),
            (0, "3001\n", ""),
        ]
        recovered = await recover_link_tunnels(manager)

    assert recovered == 0


@pytest.mark.asyncio
async def test_recover_tag_parsing_non_integer(tmp_path, monkeypatch):
    """Recovery handles non-integer tag values from OVS."""
    monkeypatch.setattr(settings, "workspace_path", str(tmp_path))

    manager = _make_manager(tmp_path)
    manager._link_tunnels["link-1"] = LinkTunnel(
        link_id="link-1",
        vni=50000,
        local_ip="10.0.0.1",
        remote_ip="10.0.0.2",
        local_vlan=3001,
        interface_name="vxlan-abc12345",
        lab_id="lab-1",
        tenant_mtu=1400,
    )

    with patch(
        "agent.network.overlay_state._shared_ovs_vsctl",
        new_callable=AsyncMock,
    ) as mock_ovs, patch(
        "agent.network.overlay_vxlan.read_vxlan_link_info",
        new_callable=AsyncMock,
        return_value=(50000, "10.0.0.2", "10.0.0.1"),
    ):
        mock_ovs.side_effect = [
            (0, "vxlan-abc12345\n", ""),
            (0, "[]\n", ""),  # Empty set, non-integer tag
        ]
        recovered = await recover_link_tunnels(manager)

    # Should recover with local_vlan=0 (fallback)
    assert recovered == 1
    assert manager._link_tunnels["link-1"].local_vlan == 0


@pytest.mark.asyncio
async def test_recover_preserves_existing_lab_id(tmp_path, monkeypatch):
    """Recovery preserves the lab_id from existing tracking."""
    monkeypatch.setattr(settings, "workspace_path", str(tmp_path))

    manager = _make_manager(tmp_path)
    manager._link_tunnels["link-1"] = LinkTunnel(
        link_id="link-1",
        vni=50000,
        local_ip="10.0.0.1",
        remote_ip="10.0.0.2",
        local_vlan=3001,
        interface_name="vxlan-abc12345",
        lab_id="lab-original",
        tenant_mtu=1400,
    )

    with patch(
        "agent.network.overlay_state._shared_ovs_vsctl",
        new_callable=AsyncMock,
    ) as mock_ovs, patch(
        "agent.network.overlay_vxlan.read_vxlan_link_info",
        new_callable=AsyncMock,
        return_value=(50000, "10.0.0.2", "10.0.0.1"),
    ):
        mock_ovs.side_effect = [
            (0, "vxlan-abc12345\n", ""),
            (0, "3001\n", ""),
        ]
        recovered = await recover_link_tunnels(manager)

    assert recovered == 1
    assert manager._link_tunnels["link-1"].lab_id == "lab-original"


@pytest.mark.asyncio
async def test_declare_state_creation_uses_fallback_mtu(tmp_path):
    """When mtu=0 and overlay_mtu=0, creation falls back to 1500."""
    manager = _make_manager(tmp_path)
    manager._batch_read_ovs_ports = AsyncMock(return_value={})

    with patch.object(settings, "overlay_mtu", 0):
        tunnel = _tunnel_dict(mtu=0)
        await declare_state(manager, [tunnel])

    call_kwargs = manager._create_vxlan_device.call_args.kwargs
    assert call_kwargs["tenant_mtu"] == 1500
