"""Tests for VXLAN recovery edge cases in overlay networking.

Covers ofport=-1 detection, ghost device cleanup, recover_link_tunnels,
cleanup_orphan_vxlan_ports, VXLAN device "already exists" auto-delete/retry,
and declare_state convergence cycles.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from agent.network.overlay import LinkTunnel, OverlayManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_manager(monkeypatch) -> OverlayManager:
    """Create an OverlayManager with noop bridge init."""
    mgr = OverlayManager()

    async def _noop():
        return None

    monkeypatch.setattr(mgr, "_ensure_ovs_bridge", _noop)
    return mgr


def _make_tunnel_dict(
    link_id: str = "r1:eth1-r2:eth1",
    lab_id: str = "lab1",
    vni: int = 9000,
    local_ip: str = "10.0.0.1",
    remote_ip: str = "10.0.0.2",
    expected_vlan: int = 2100,
    port_name: str = "vxlan-abc12345",
    mtu: int = 0,
) -> dict[str, Any]:
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


# ===========================================================================
# TestOfportDetection
# ===========================================================================

class TestOfportDetection:
    """Tests for ofport=-1 detection in declare_state convergence."""

    @pytest.mark.asyncio
    async def test_ofport_minus_one_triggers_port_deletion_and_recreation(self, monkeypatch):
        """When OVS reports ofport=-1, the stale port is deleted and recreated."""
        mgr = _make_manager(monkeypatch)
        deleted_ports: list[str] = []
        created_devices: list[str] = []

        async def _batch_read_ovs_ports():
            return {
                "vxlan-abc12345": {
                    "name": "vxlan-abc12345",
                    "tag": 2100,
                    "type": "system",
                    "ofport": -1,
                },
            }

        async def _ovs_vsctl(*args):
            if "del-port" in args:
                deleted_ports.append(args[-1])
            return 0, "", ""

        async def _ip_link_exists(name):
            return False

        async def _create_vxlan_device(**kwargs):
            created_devices.append(kwargs["name"])

        async def _write_cache(tunnels):
            pass

        monkeypatch.setattr(mgr, "_batch_read_ovs_ports", _batch_read_ovs_ports)
        monkeypatch.setattr(mgr, "_ovs_vsctl", _ovs_vsctl)
        monkeypatch.setattr(mgr, "_ip_link_exists", _ip_link_exists)
        monkeypatch.setattr(mgr, "_create_vxlan_device", _create_vxlan_device)
        monkeypatch.setattr(mgr, "_write_declared_state_cache", _write_cache)

        tunnel = _make_tunnel_dict()
        result = await mgr.declare_state([tunnel])

        assert "vxlan-abc12345" in deleted_ports
        assert "vxlan-abc12345" in created_devices
        assert result["results"][0]["status"] == "created"

    @pytest.mark.asyncio
    async def test_ofport_zero_treated_as_valid(self, monkeypatch):
        """ofport=0 is a valid port number and should NOT trigger deletion."""
        mgr = _make_manager(monkeypatch)
        deleted_ports: list[str] = []

        async def _batch_read_ovs_ports():
            return {
                "vxlan-abc12345": {
                    "name": "vxlan-abc12345",
                    "tag": 2100,
                    "type": "system",
                    "ofport": 0,
                },
            }

        async def _ovs_vsctl(*args):
            if "del-port" in args:
                deleted_ports.append(args[-1])
            return 0, "", ""

        async def _write_cache(tunnels):
            pass

        monkeypatch.setattr(mgr, "_batch_read_ovs_ports", _batch_read_ovs_ports)
        monkeypatch.setattr(mgr, "_ovs_vsctl", _ovs_vsctl)
        monkeypatch.setattr(mgr, "_write_declared_state_cache", _write_cache)

        tunnel = _make_tunnel_dict()
        result = await mgr.declare_state([tunnel])

        assert len(deleted_ports) == 0
        assert result["results"][0]["status"] == "converged"

    @pytest.mark.asyncio
    async def test_ofport_positive_treated_as_valid(self, monkeypatch):
        """A positive ofport means the port is healthy."""
        mgr = _make_manager(monkeypatch)

        async def _batch_read_ovs_ports():
            return {
                "vxlan-abc12345": {
                    "name": "vxlan-abc12345",
                    "tag": 2100,
                    "type": "system",
                    "ofport": 5,
                },
            }

        async def _ovs_vsctl(*args):
            return 0, "", ""

        async def _write_cache(tunnels):
            pass

        monkeypatch.setattr(mgr, "_batch_read_ovs_ports", _batch_read_ovs_ports)
        monkeypatch.setattr(mgr, "_ovs_vsctl", _ovs_vsctl)
        monkeypatch.setattr(mgr, "_write_declared_state_cache", _write_cache)

        result = await mgr.declare_state([_make_tunnel_dict()])
        assert result["results"][0]["status"] == "converged"

    @pytest.mark.asyncio
    async def test_ofport_minus_one_with_creation_failure(self, monkeypatch):
        """If recreation after ofport=-1 deletion fails, status should be error."""
        mgr = _make_manager(monkeypatch)

        async def _batch_read_ovs_ports():
            return {
                "vxlan-abc12345": {
                    "name": "vxlan-abc12345",
                    "tag": 2100,
                    "type": "system",
                    "ofport": -1,
                },
            }

        async def _ovs_vsctl(*args):
            return 0, "", ""

        async def _ip_link_exists(name):
            return False

        async def _create_vxlan_device(**kwargs):
            raise RuntimeError("device creation failed")

        async def _write_cache(tunnels):
            pass

        monkeypatch.setattr(mgr, "_batch_read_ovs_ports", _batch_read_ovs_ports)
        monkeypatch.setattr(mgr, "_ovs_vsctl", _ovs_vsctl)
        monkeypatch.setattr(mgr, "_ip_link_exists", _ip_link_exists)
        monkeypatch.setattr(mgr, "_create_vxlan_device", _create_vxlan_device)
        monkeypatch.setattr(mgr, "_write_declared_state_cache", _write_cache)

        result = await mgr.declare_state([_make_tunnel_dict()])
        assert result["results"][0]["status"] == "error"
        assert "device creation failed" in result["results"][0]["error"]


# ===========================================================================
# TestGhostDeviceCleanup
# ===========================================================================

class TestGhostDeviceCleanup:
    """Tests for ghost Linux VXLAN device cleanup during creation."""

    @pytest.mark.asyncio
    async def test_ghost_device_deleted_before_creation(self, monkeypatch):
        """If a Linux VXLAN device exists but OVS port is missing, delete it first."""
        mgr = _make_manager(monkeypatch)
        deleted_links: list[str] = []

        async def _batch_read_ovs_ports():
            return {}  # no OVS ports

        async def _ip_link_exists(name):
            return True  # ghost device exists

        async def _run_cmd(cmd):
            if cmd[0] == "ip" and "delete" in cmd:
                deleted_links.append(cmd[3])
            return 0, "", ""

        async def _create_vxlan_device(**kwargs):
            pass

        async def _ovs_vsctl(*args):
            return 0, "", ""

        async def _write_cache(tunnels):
            pass

        monkeypatch.setattr(mgr, "_batch_read_ovs_ports", _batch_read_ovs_ports)
        monkeypatch.setattr(mgr, "_ip_link_exists", _ip_link_exists)
        monkeypatch.setattr(mgr, "_run_cmd", _run_cmd)
        monkeypatch.setattr(mgr, "_create_vxlan_device", _create_vxlan_device)
        monkeypatch.setattr(mgr, "_ovs_vsctl", _ovs_vsctl)
        monkeypatch.setattr(mgr, "_write_declared_state_cache", _write_cache)

        result = await mgr.declare_state([_make_tunnel_dict()])
        assert "vxlan-abc12345" in deleted_links
        assert result["results"][0]["status"] == "created"

    @pytest.mark.asyncio
    async def test_no_ghost_device_skips_deletion(self, monkeypatch):
        """If no ghost Linux device exists, skip the ip link delete step."""
        mgr = _make_manager(monkeypatch)
        deleted_links: list[str] = []

        async def _batch_read_ovs_ports():
            return {}

        async def _ip_link_exists(name):
            return False

        async def _run_cmd(cmd):
            if cmd[0] == "ip" and "delete" in cmd:
                deleted_links.append(cmd[3])
            return 0, "", ""

        async def _create_vxlan_device(**kwargs):
            pass

        async def _ovs_vsctl(*args):
            return 0, "", ""

        async def _write_cache(tunnels):
            pass

        monkeypatch.setattr(mgr, "_batch_read_ovs_ports", _batch_read_ovs_ports)
        monkeypatch.setattr(mgr, "_ip_link_exists", _ip_link_exists)
        monkeypatch.setattr(mgr, "_run_cmd", _run_cmd)
        monkeypatch.setattr(mgr, "_create_vxlan_device", _create_vxlan_device)
        monkeypatch.setattr(mgr, "_ovs_vsctl", _ovs_vsctl)
        monkeypatch.setattr(mgr, "_write_declared_state_cache", _write_cache)

        result = await mgr.declare_state([_make_tunnel_dict()])
        assert len(deleted_links) == 0
        assert result["results"][0]["status"] == "created"


# ===========================================================================
# TestRecoverLinkTunnels
# ===========================================================================

class TestRecoverLinkTunnels:
    """Tests for recover_link_tunnels rebuilding state from OVS/cache."""

    @pytest.mark.asyncio
    async def test_recover_from_declared_state_cache(self, monkeypatch, tmp_path):
        """Recovery from local cache calls declare_state with cached tunnels."""
        from agent.config import settings
        monkeypatch.setattr(settings, "workspace_path", str(tmp_path))

        mgr = _make_manager(monkeypatch)
        declared_calls: list[list] = []

        cached_tunnels = [_make_tunnel_dict()]
        cache_data = {"declared_at": "2026-01-01T00:00:00Z", "tunnels": cached_tunnels}
        cache_path = tmp_path / "declared_overlay_state.json"
        cache_path.write_text(json.dumps(cache_data))

        async def _declare_state(tunnels, declared_labs=None):
            declared_calls.append(tunnels)
            return {
                "results": [{"status": "converged"}],
                "orphans_removed": [],
            }

        monkeypatch.setattr(mgr, "declare_state", _declare_state)

        recovered = await mgr.recover_link_tunnels()
        assert recovered == 1
        assert len(declared_calls) == 1
        assert declared_calls[0] == cached_tunnels

    @pytest.mark.asyncio
    async def test_recover_falls_back_to_ovs_scan(self, monkeypatch, tmp_path):
        """When cache is absent, falls back to OVS port scan for known mappings."""
        from agent.config import settings
        monkeypatch.setattr(settings, "workspace_path", str(tmp_path))

        mgr = _make_manager(monkeypatch)

        # Pre-populate a known link mapping
        iface_name = "vxlan-deadbeef"
        mgr._link_tunnels["link-1"] = LinkTunnel(
            link_id="link-1",
            vni=8000,
            local_ip="10.0.0.1",
            remote_ip="10.0.0.2",
            local_vlan=2200,
            interface_name=iface_name,
            lab_id="lab1",
        )

        async def _ovs_vsctl(*args):
            if "list-ports" in args:
                return 0, f"{iface_name}\nsome-other-port\n", ""
            if "get" in args and "tag" in args:
                return 0, "2200", ""
            return 0, "", ""

        monkeypatch.setattr(
            "agent.network.overlay_state._shared_ovs_vsctl", _ovs_vsctl
        )
        monkeypatch.setattr(
            "agent.network.overlay_vxlan.read_vxlan_link_info",
            AsyncMock(return_value=(8000, "10.0.0.2", "10.0.0.1")),
        )

        recovered = await mgr.recover_link_tunnels()
        assert recovered == 1
        assert mgr._link_tunnels["link-1"].vni == 8000

    @pytest.mark.asyncio
    async def test_recover_skips_unknown_ports(self, monkeypatch, tmp_path):
        """Ports without a known link_id mapping are skipped."""
        from agent.config import settings
        monkeypatch.setattr(settings, "workspace_path", str(tmp_path))

        mgr = _make_manager(monkeypatch)
        # No pre-populated link tunnels — nothing known

        async def _ovs_vsctl(*args):
            if "list-ports" in args:
                return 0, "vxlan-unknown1\n", ""
            if "get" in args and "tag" in args:
                return 0, "2300", ""
            return 0, "", ""

        monkeypatch.setattr(
            "agent.network.overlay_state._shared_ovs_vsctl", _ovs_vsctl
        )
        monkeypatch.setattr(
            "agent.network.overlay_vxlan.read_vxlan_link_info",
            AsyncMock(return_value=(7000, "10.0.0.3", "10.0.0.1")),
        )

        recovered = await mgr.recover_link_tunnels()
        assert recovered == 0

    @pytest.mark.asyncio
    async def test_recover_cache_failure_falls_back(self, monkeypatch, tmp_path):
        """If cache-based recovery raises, falls back to OVS scan."""
        from agent.config import settings
        monkeypatch.setattr(settings, "workspace_path", str(tmp_path))

        mgr = _make_manager(monkeypatch)

        # Write a valid cache
        cached_tunnels = [_make_tunnel_dict()]
        cache_data = {"declared_at": "2026-01-01T00:00:00Z", "tunnels": cached_tunnels}
        cache_path = tmp_path / "declared_overlay_state.json"
        cache_path.write_text(json.dumps(cache_data))

        async def _declare_state_fail(tunnels, declared_labs=None):
            raise RuntimeError("declare_state broke")

        monkeypatch.setattr(mgr, "declare_state", _declare_state_fail)

        async def _ovs_vsctl(*args):
            if "list-ports" in args:
                return 0, "", ""
            return 0, "", ""

        monkeypatch.setattr(
            "agent.network.overlay_state._shared_ovs_vsctl", _ovs_vsctl
        )

        recovered = await mgr.recover_link_tunnels()
        assert recovered == 0  # nothing to recover in fallback either


# ===========================================================================
# TestCleanupOrphanPorts
# ===========================================================================

class TestCleanupOrphanPorts:
    """Tests for orphan port cleanup in declare_state."""

    @pytest.mark.asyncio
    async def test_orphan_port_in_declared_lab_gets_removed(self, monkeypatch):
        """A tracked vxlan- port not in declared set is deleted if lab matches."""
        mgr = _make_manager(monkeypatch)
        deleted_devices: list[str] = []

        orphan_name = "vxlan-orphan99"
        mgr._link_tunnels["orphan-link"] = LinkTunnel(
            link_id="orphan-link",
            vni=7777,
            local_ip="10.0.0.1",
            remote_ip="10.0.0.2",
            local_vlan=2500,
            interface_name=orphan_name,
            lab_id="lab1",
        )

        async def _batch_read_ovs_ports():
            return {
                orphan_name: {
                    "name": orphan_name,
                    "tag": 2500,
                    "type": "system",
                    "ofport": 3,
                },
            }

        async def _delete_vxlan_device(name, bridge):
            deleted_devices.append(name)

        async def _write_cache(tunnels):
            pass

        monkeypatch.setattr(mgr, "_batch_read_ovs_ports", _batch_read_ovs_ports)
        monkeypatch.setattr(mgr, "_delete_vxlan_device", _delete_vxlan_device)
        monkeypatch.setattr(mgr, "_write_declared_state_cache", _write_cache)

        # Declare empty tunnel list for lab1 — orphan should be cleaned
        result = await mgr.declare_state([], declared_labs=["lab1"])
        assert orphan_name in deleted_devices
        assert orphan_name in result["orphans_removed"]
        assert "orphan-link" not in mgr._link_tunnels

    @pytest.mark.asyncio
    async def test_orphan_port_in_different_lab_not_removed(self, monkeypatch):
        """An orphan port belonging to a non-declared lab is left alone."""
        mgr = _make_manager(monkeypatch)
        deleted_devices: list[str] = []

        orphan_name = "vxlan-otherlab"
        mgr._link_tunnels["other-link"] = LinkTunnel(
            link_id="other-link",
            vni=8888,
            local_ip="10.0.0.1",
            remote_ip="10.0.0.2",
            local_vlan=2600,
            interface_name=orphan_name,
            lab_id="lab2",
        )

        async def _batch_read_ovs_ports():
            return {
                orphan_name: {
                    "name": orphan_name,
                    "tag": 2600,
                    "type": "system",
                    "ofport": 4,
                },
            }

        async def _delete_vxlan_device(name, bridge):
            deleted_devices.append(name)

        async def _write_cache(tunnels):
            pass

        monkeypatch.setattr(mgr, "_batch_read_ovs_ports", _batch_read_ovs_ports)
        monkeypatch.setattr(mgr, "_delete_vxlan_device", _delete_vxlan_device)
        monkeypatch.setattr(mgr, "_write_declared_state_cache", _write_cache)

        result = await mgr.declare_state([], declared_labs=["lab1"])
        assert len(deleted_devices) == 0
        assert len(result["orphans_removed"]) == 0

    @pytest.mark.asyncio
    async def test_untracked_vxlan_port_not_removed(self, monkeypatch):
        """A vxlan- port not in _link_tunnels is NOT removed (safety guard)."""
        mgr = _make_manager(monkeypatch)
        deleted_devices: list[str] = []

        async def _batch_read_ovs_ports():
            return {
                "vxlan-untracked": {
                    "name": "vxlan-untracked",
                    "tag": 2700,
                    "type": "system",
                    "ofport": 5,
                },
            }

        async def _delete_vxlan_device(name, bridge):
            deleted_devices.append(name)

        async def _write_cache(tunnels):
            pass

        monkeypatch.setattr(mgr, "_batch_read_ovs_ports", _batch_read_ovs_ports)
        monkeypatch.setattr(mgr, "_delete_vxlan_device", _delete_vxlan_device)
        monkeypatch.setattr(mgr, "_write_declared_state_cache", _write_cache)

        result = await mgr.declare_state([], declared_labs=["lab1"])
        assert len(deleted_devices) == 0


# ===========================================================================
# TestVxlanDeviceCreation
# ===========================================================================

class TestVxlanDeviceCreation:
    """Tests for VXLAN device creation with already-exists retry logic."""

    @pytest.mark.asyncio
    async def test_already_exists_triggers_delete_and_retry(self):
        """create_vxlan_device retries after deleting stale device."""
        calls: list[list[str]] = []

        call_count = {"create": 0}

        async def mock_run_cmd(cmd):
            calls.append(cmd)
            if cmd[0] == "ip" and cmd[2] == "add":
                call_count["create"] += 1
                if call_count["create"] == 1:
                    return 1, "", "RTNETLINK answers: File exists already exists"
                return 0, "", ""
            return 0, "", ""

        async def mock_ovs_vsctl(*args):
            return 0, "", ""

        with patch("agent.network.overlay_vxlan._shared_run_cmd", mock_run_cmd), \
             patch("agent.network.overlay_vxlan._shared_ovs_vsctl", mock_ovs_vsctl):
            from agent.network.overlay_vxlan import create_vxlan_device
            await create_vxlan_device(
                name="vxlan-test1",
                vni=5000,
                local_ip="10.0.0.1",
                remote_ip="10.0.0.2",
                bridge="arch-ovs",
                vlan_tag=2100,
            )

        # Should have: create(fail) -> delete -> create(success) -> mtu -> up -> add-port
        delete_cmds = [c for c in calls if c[0] == "ip" and "delete" in c]
        assert len(delete_cmds) == 1
        assert "vxlan-test1" in delete_cmds[0]

    @pytest.mark.asyncio
    async def test_creation_fails_permanently_raises(self):
        """If retry also fails, RuntimeError is raised."""
        async def mock_run_cmd(cmd):
            if cmd[0] == "ip" and cmd[2] == "add":
                return 1, "", "some error already exists"
            return 0, "", ""

        async def mock_ovs_vsctl(*args):
            return 0, "", ""

        with patch("agent.network.overlay_vxlan._shared_run_cmd", mock_run_cmd), \
             patch("agent.network.overlay_vxlan._shared_ovs_vsctl", mock_ovs_vsctl):
            from agent.network.overlay_vxlan import create_vxlan_device
            with pytest.raises(RuntimeError, match="Failed to create VXLAN device"):
                await create_vxlan_device(
                    name="vxlan-fail",
                    vni=5001,
                    local_ip="10.0.0.1",
                    remote_ip="10.0.0.2",
                    bridge="arch-ovs",
                )

    @pytest.mark.asyncio
    async def test_creation_succeeds_first_try(self):
        """Clean creation without stale device."""
        calls: list[list[str]] = []

        async def mock_run_cmd(cmd):
            calls.append(cmd)
            return 0, "", ""

        async def mock_ovs_vsctl(*args):
            return 0, "", ""

        with patch("agent.network.overlay_vxlan._shared_run_cmd", mock_run_cmd), \
             patch("agent.network.overlay_vxlan._shared_ovs_vsctl", mock_ovs_vsctl):
            from agent.network.overlay_vxlan import create_vxlan_device
            await create_vxlan_device(
                name="vxlan-clean",
                vni=5002,
                local_ip="10.0.0.1",
                remote_ip="10.0.0.2",
                bridge="arch-ovs",
                vlan_tag=2200,
            )

        delete_cmds = [c for c in calls if "delete" in c]
        assert len(delete_cmds) == 0

    @pytest.mark.asyncio
    async def test_ovs_add_port_failure_cleans_up_device(self):
        """If OVS add-port fails, the Linux VXLAN device is cleaned up."""
        calls: list[list[str]] = []

        async def mock_run_cmd(cmd):
            calls.append(cmd)
            return 0, "", ""

        async def mock_ovs_vsctl(*args):
            if "add-port" in args:
                return 1, "", "ovs error"
            return 0, "", ""

        with patch("agent.network.overlay_vxlan._shared_run_cmd", mock_run_cmd), \
             patch("agent.network.overlay_vxlan._shared_ovs_vsctl", mock_ovs_vsctl):
            from agent.network.overlay_vxlan import create_vxlan_device
            with pytest.raises(RuntimeError, match="Failed to add VXLAN device"):
                await create_vxlan_device(
                    name="vxlan-ovsfail",
                    vni=5003,
                    local_ip="10.0.0.1",
                    remote_ip="10.0.0.2",
                    bridge="arch-ovs",
                )

        cleanup_cmds = [c for c in calls if c[0] == "ip" and "delete" in c]
        assert any("vxlan-ovsfail" in c for c in cleanup_cmds)


# ===========================================================================
# TestDeclareState
# ===========================================================================

class TestDeclareState:
    """Tests for declare_state convergence cycle."""

    @pytest.mark.asyncio
    async def test_converged_when_vlan_matches(self, monkeypatch):
        """Port with matching VLAN is marked converged."""
        mgr = _make_manager(monkeypatch)

        async def _batch_read_ovs_ports():
            return {
                "vxlan-abc12345": {
                    "name": "vxlan-abc12345",
                    "tag": 2100,
                    "type": "system",
                    "ofport": 3,
                },
            }

        async def _ovs_vsctl(*args):
            return 0, "", ""

        async def _write_cache(tunnels):
            pass

        monkeypatch.setattr(mgr, "_batch_read_ovs_ports", _batch_read_ovs_ports)
        monkeypatch.setattr(mgr, "_ovs_vsctl", _ovs_vsctl)
        monkeypatch.setattr(mgr, "_write_declared_state_cache", _write_cache)

        result = await mgr.declare_state([_make_tunnel_dict()])
        assert result["results"][0]["status"] == "converged"
        assert "r1:eth1-r2:eth1" in mgr._link_tunnels

    @pytest.mark.asyncio
    async def test_vlan_mismatch_triggers_update(self, monkeypatch):
        """Port with wrong VLAN tag gets updated."""
        mgr = _make_manager(monkeypatch)
        set_calls: list[tuple] = []

        async def _batch_read_ovs_ports():
            return {
                "vxlan-abc12345": {
                    "name": "vxlan-abc12345",
                    "tag": 1999,
                    "type": "system",
                    "ofport": 3,
                },
            }

        async def _ovs_vsctl(*args):
            if args and args[0] == "set":
                set_calls.append(args)
            return 0, "", ""

        async def _write_cache(tunnels):
            pass

        monkeypatch.setattr(mgr, "_batch_read_ovs_ports", _batch_read_ovs_ports)
        monkeypatch.setattr(mgr, "_ovs_vsctl", _ovs_vsctl)
        monkeypatch.setattr(mgr, "_write_declared_state_cache", _write_cache)

        result = await mgr.declare_state([_make_tunnel_dict(expected_vlan=2100)])
        assert result["results"][0]["status"] == "updated"
        assert any("tag=2100" in str(c) for c in set_calls)

    @pytest.mark.asyncio
    async def test_missing_port_gets_created(self, monkeypatch):
        """A completely missing port triggers creation."""
        mgr = _make_manager(monkeypatch)
        created: list[str] = []

        async def _batch_read_ovs_ports():
            return {}

        async def _ip_link_exists(name):
            return False

        async def _create_vxlan_device(**kwargs):
            created.append(kwargs["name"])

        async def _ovs_vsctl(*args):
            return 0, "", ""

        async def _write_cache(tunnels):
            pass

        monkeypatch.setattr(mgr, "_batch_read_ovs_ports", _batch_read_ovs_ports)
        monkeypatch.setattr(mgr, "_ip_link_exists", _ip_link_exists)
        monkeypatch.setattr(mgr, "_create_vxlan_device", _create_vxlan_device)
        monkeypatch.setattr(mgr, "_ovs_vsctl", _ovs_vsctl)
        monkeypatch.setattr(mgr, "_write_declared_state_cache", _write_cache)

        result = await mgr.declare_state([_make_tunnel_dict()])
        assert result["results"][0]["status"] == "created"
        assert "vxlan-abc12345" in created

    @pytest.mark.asyncio
    async def test_ovs_query_failure_skips_convergence(self, monkeypatch):
        """When OVS batch read returns None, convergence is skipped."""
        mgr = _make_manager(monkeypatch)

        async def _batch_read_ovs_ports():
            return None

        async def _write_cache(tunnels):
            pass

        monkeypatch.setattr(mgr, "_batch_read_ovs_ports", _batch_read_ovs_ports)
        monkeypatch.setattr(mgr, "_write_declared_state_cache", _write_cache)

        result = await mgr.declare_state([_make_tunnel_dict()])
        assert result.get("skipped") == "ovs_read_error"
        assert len(result["results"]) == 0

    @pytest.mark.asyncio
    async def test_mtu_enforcement_on_existing_port(self, monkeypatch):
        """When mtu is set and differs from current, it gets updated."""
        mgr = _make_manager(monkeypatch)
        run_cmds: list[list[str]] = []

        async def _batch_read_ovs_ports():
            return {
                "vxlan-abc12345": {
                    "name": "vxlan-abc12345",
                    "tag": 2100,
                    "type": "system",
                    "ofport": 3,
                },
            }

        async def _ovs_vsctl(*args):
            return 0, "", ""

        async def _run_cmd(cmd):
            run_cmds.append(cmd)
            if "show" in cmd:
                return 0, "5: vxlan-abc12345: <BROADCAST,MULTICAST,UP> mtu 1400 ...", ""
            return 0, "", ""

        async def _write_cache(tunnels):
            pass

        monkeypatch.setattr(mgr, "_batch_read_ovs_ports", _batch_read_ovs_ports)
        monkeypatch.setattr(mgr, "_ovs_vsctl", _ovs_vsctl)
        monkeypatch.setattr(mgr, "_run_cmd", _run_cmd)
        monkeypatch.setattr(mgr, "_write_declared_state_cache", _write_cache)

        result = await mgr.declare_state([_make_tunnel_dict(mtu=1500)])
        assert result["results"][0]["status"] == "updated"
        mtu_set = [c for c in run_cmds if "mtu" in c and "set" in c]
        assert len(mtu_set) == 1
        assert "1500" in mtu_set[0]

    @pytest.mark.asyncio
    async def test_multiple_tunnels_processed(self, monkeypatch):
        """Multiple tunnels in a single declare_state call are all processed."""
        mgr = _make_manager(monkeypatch)

        async def _batch_read_ovs_ports():
            return {}

        async def _ip_link_exists(name):
            return False

        async def _create_vxlan_device(**kwargs):
            pass

        async def _ovs_vsctl(*args):
            return 0, "", ""

        async def _write_cache(tunnels):
            pass

        monkeypatch.setattr(mgr, "_batch_read_ovs_ports", _batch_read_ovs_ports)
        monkeypatch.setattr(mgr, "_ip_link_exists", _ip_link_exists)
        monkeypatch.setattr(mgr, "_create_vxlan_device", _create_vxlan_device)
        monkeypatch.setattr(mgr, "_ovs_vsctl", _ovs_vsctl)
        monkeypatch.setattr(mgr, "_write_declared_state_cache", _write_cache)

        tunnels = [
            _make_tunnel_dict(link_id="link-1", port_name="vxlan-aaa11111"),
            _make_tunnel_dict(link_id="link-2", port_name="vxlan-bbb22222"),
            _make_tunnel_dict(link_id="link-3", port_name="vxlan-ccc33333"),
        ]
        result = await mgr.declare_state(tunnels)
        assert len(result["results"]) == 3
        assert all(r["status"] == "created" for r in result["results"])
