"""Extended tests for overlay recovery and convergence functions.

Covers:
- recover_link_tunnels: cache-based recovery, OVS fallback, edge cases
- declare_state: convergence, orphan cleanup, ofport=-1 handling
- batch_read_ovs_ports: JSON parsing, filtering, error handling
- write/load_declared_state_cache: persistence and recovery
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from agent.network.overlay import LinkTunnel, OverlayManager


def _make_manager() -> OverlayManager:
    """Create an OverlayManager with stubs for OVS subprocess calls."""
    mgr = OverlayManager()
    mgr._bridge_name = "arch-ovs"
    mgr._ovs_initialized = True
    return mgr


class TestRecoverLinkTunnelsFromCache:
    """Tests for cache-based link tunnel recovery."""

    @pytest.mark.asyncio
    async def test_recover_from_cache_success(self, monkeypatch):
        """Recovery from declared-state cache should call declare_state."""
        mgr = _make_manager()

        cached_tunnels = [
            {
                "link_id": "r1:eth1-r2:eth1",
                "lab_id": "lab1",
                "vni": 5000,
                "local_ip": "10.0.0.1",
                "remote_ip": "10.0.0.2",
                "expected_vlan": 200,
                "port_name": "vxlan-abc",
                "mtu": 1400,
            }
        ]

        from agent.network import overlay_state
        monkeypatch.setattr(
            overlay_state, "load_declared_state_cache",
            AsyncMock(return_value=cached_tunnels),
        )

        # declare_state returns converged result
        async def fake_declare_state(tunnels, declared_labs=None):
            return {
                "results": [{"link_id": "r1:eth1-r2:eth1", "lab_id": "lab1", "status": "converged"}],
                "orphans_removed": [],
            }

        monkeypatch.setattr(mgr, "declare_state", fake_declare_state)

        count = await mgr.recover_link_tunnels()
        assert count == 1

    @pytest.mark.asyncio
    async def test_recover_from_cache_empty(self, monkeypatch):
        """Empty cache should fall through to OVS scan."""
        mgr = _make_manager()

        from agent.network import overlay_state
        monkeypatch.setattr(
            overlay_state, "load_declared_state_cache",
            AsyncMock(return_value=None),
        )

        # OVS scan returns no ports
        from agent.network import cmd as net_cmd
        monkeypatch.setattr(
            net_cmd, "ovs_vsctl",
            AsyncMock(return_value=(0, "", "")),
        )

        count = await mgr.recover_link_tunnels()
        assert count == 0

    @pytest.mark.asyncio
    async def test_recover_cache_exception_falls_back(self, monkeypatch):
        """Exception during cache recovery falls back to OVS scan."""
        mgr = _make_manager()

        from agent.network import overlay_state
        monkeypatch.setattr(
            overlay_state, "load_declared_state_cache",
            AsyncMock(return_value=[{"link_id": "x", "lab_id": "y", "vni": 1}]),
        )

        async def broken_declare_state(tunnels, declared_labs=None):
            raise RuntimeError("OVS broken")

        monkeypatch.setattr(mgr, "declare_state", broken_declare_state)

        # Fallback OVS scan
        from agent.network import cmd as net_cmd
        monkeypatch.setattr(
            net_cmd, "ovs_vsctl",
            AsyncMock(return_value=(0, "", "")),
        )

        count = await mgr.recover_link_tunnels()
        assert count == 0

    @pytest.mark.asyncio
    async def test_recover_fallback_skips_unknown_ports(self, monkeypatch):
        """Fallback recovery skips ports without known link_id mapping."""
        mgr = _make_manager()
        # No known tunnels for any interface
        mgr._link_tunnels = {}

        from agent.network import overlay_state
        monkeypatch.setattr(
            overlay_state, "load_declared_state_cache",
            AsyncMock(return_value=None),
        )

        call_log = []

        async def fake_ovs_vsctl(*args):
            call_log.append(args)
            if args == ("list-ports", "arch-ovs"):
                return (0, "vxlan-abc123\nvxlan-def456\n", "")
            if args[0] == "get" and args[1] == "port":
                return (0, "200", "")
            return (0, "", "")

        from agent.network import cmd as net_cmd
        monkeypatch.setattr(net_cmd, "ovs_vsctl", fake_ovs_vsctl)

        from agent.network import overlay_vxlan
        monkeypatch.setattr(
            overlay_vxlan, "read_vxlan_link_info",
            AsyncMock(return_value=(5000, "10.0.0.2", "10.0.0.1")),
        )

        count = await mgr.recover_link_tunnels()
        # Should skip because no known link_id mapping exists
        assert count == 0

    @pytest.mark.asyncio
    async def test_recover_fallback_with_known_mapping(self, monkeypatch):
        """Fallback recovery should recover ports with known link_id mapping."""
        mgr = _make_manager()
        # Pre-populate with a known mapping
        mgr._link_tunnels = {
            "r1:eth1-r2:eth1": LinkTunnel(
                link_id="r1:eth1-r2:eth1",
                vni=5000,
                local_ip="10.0.0.1",
                remote_ip="10.0.0.2",
                local_vlan=200,
                interface_name="vxlan-abc123",
                lab_id="lab1",
            ),
        }

        import agent.network.overlay_state as overlay_state_mod
        monkeypatch.setattr(
            overlay_state_mod, "load_declared_state_cache",
            AsyncMock(return_value=None),
        )

        async def fake_ovs_vsctl(*args):
            if args == ("list-ports", "arch-ovs"):
                return (0, "vxlan-abc123\n", "")
            if args[0] == "get" and args[1] == "port":
                return (0, "200", "")
            return (0, "", "")

        # Patch the module-level import in overlay_state
        monkeypatch.setattr(overlay_state_mod, "_shared_ovs_vsctl", fake_ovs_vsctl)

        from agent.network import overlay_vxlan
        monkeypatch.setattr(
            overlay_vxlan, "read_vxlan_link_info",
            AsyncMock(return_value=(5000, "10.0.0.2", "10.0.0.1")),
        )

        count = await mgr.recover_link_tunnels()
        assert count == 1
        assert "r1:eth1-r2:eth1" in mgr._link_tunnels


class TestDeclareState:
    """Tests for declare_state convergence logic."""

    @pytest.mark.asyncio
    async def test_converged_existing_port(self, monkeypatch):
        """Port with correct VNI and VLAN should be 'converged'."""
        mgr = _make_manager()

        async def noop():
            pass

        monkeypatch.setattr(mgr, "_ensure_ovs_bridge", noop)

        async def fake_batch():
            return {
                "vxlan-abc": {"name": "vxlan-abc", "tag": 200, "type": "vxlan", "ofport": 5},
            }

        monkeypatch.setattr(mgr, "_batch_read_ovs_ports", fake_batch)
        monkeypatch.setattr(mgr, "_write_declared_state_cache", AsyncMock())

        tunnels = [{
            "link_id": "r1:eth1-r2:eth1",
            "lab_id": "lab1",
            "vni": 5000,
            "local_ip": "10.0.0.1",
            "remote_ip": "10.0.0.2",
            "expected_vlan": 200,
            "port_name": "vxlan-abc",
            "mtu": 0,
        }]

        result = await mgr.declare_state(tunnels)
        assert result["results"][0]["status"] == "converged"

    @pytest.mark.asyncio
    async def test_updated_wrong_vlan(self, monkeypatch):
        """Port with wrong VLAN tag should be updated."""
        mgr = _make_manager()

        async def noop():
            pass

        monkeypatch.setattr(mgr, "_ensure_ovs_bridge", noop)

        async def fake_batch():
            return {
                "vxlan-abc": {"name": "vxlan-abc", "tag": 100, "type": "vxlan", "ofport": 5},
            }

        monkeypatch.setattr(mgr, "_batch_read_ovs_ports", fake_batch)
        monkeypatch.setattr(mgr, "_ovs_vsctl", AsyncMock(return_value=(0, "", "")))
        monkeypatch.setattr(mgr, "_run_cmd", AsyncMock(return_value=(1, "", "")))
        monkeypatch.setattr(mgr, "_write_declared_state_cache", AsyncMock())

        tunnels = [{
            "link_id": "r1:eth1-r2:eth1",
            "lab_id": "lab1",
            "vni": 5000,
            "local_ip": "10.0.0.1",
            "remote_ip": "10.0.0.2",
            "expected_vlan": 200,
            "port_name": "vxlan-abc",
            "mtu": 0,
        }]

        result = await mgr.declare_state(tunnels)
        assert result["results"][0]["status"] == "updated"

    @pytest.mark.asyncio
    async def test_created_missing_port(self, monkeypatch):
        """Missing port should be created."""
        mgr = _make_manager()

        async def noop():
            pass

        monkeypatch.setattr(mgr, "_ensure_ovs_bridge", noop)

        async def fake_batch():
            return {}  # No ports

        monkeypatch.setattr(mgr, "_batch_read_ovs_ports", fake_batch)
        monkeypatch.setattr(mgr, "_ip_link_exists", AsyncMock(return_value=False))
        monkeypatch.setattr(mgr, "_create_vxlan_device", AsyncMock())
        monkeypatch.setattr(mgr, "_write_declared_state_cache", AsyncMock())

        tunnels = [{
            "link_id": "r1:eth1-r2:eth1",
            "lab_id": "lab1",
            "vni": 5000,
            "local_ip": "10.0.0.1",
            "remote_ip": "10.0.0.2",
            "expected_vlan": 200,
            "port_name": "vxlan-abc",
            "mtu": 1400,
        }]

        result = await mgr.declare_state(tunnels)
        assert result["results"][0]["status"] == "created"
        assert "r1:eth1-r2:eth1" in mgr._link_tunnels

    @pytest.mark.asyncio
    async def test_ofport_negative_one_triggers_recreate(self, monkeypatch):
        """Port with ofport=-1 should be deleted and recreated."""
        mgr = _make_manager()

        async def noop():
            pass

        monkeypatch.setattr(mgr, "_ensure_ovs_bridge", noop)

        async def fake_batch():
            return {
                "vxlan-abc": {"name": "vxlan-abc", "tag": 200, "type": "vxlan", "ofport": -1},
            }

        ovs_calls = []

        async def track_ovs_vsctl(*args):
            ovs_calls.append(args)
            return (0, "", "")

        monkeypatch.setattr(mgr, "_batch_read_ovs_ports", fake_batch)
        monkeypatch.setattr(mgr, "_ovs_vsctl", track_ovs_vsctl)
        monkeypatch.setattr(mgr, "_ip_link_exists", AsyncMock(return_value=False))
        monkeypatch.setattr(mgr, "_create_vxlan_device", AsyncMock())
        monkeypatch.setattr(mgr, "_write_declared_state_cache", AsyncMock())

        tunnels = [{
            "link_id": "r1:eth1-r2:eth1",
            "lab_id": "lab1",
            "vni": 5000,
            "local_ip": "10.0.0.1",
            "remote_ip": "10.0.0.2",
            "expected_vlan": 200,
            "port_name": "vxlan-abc",
            "mtu": 0,
        }]

        result = await mgr.declare_state(tunnels)
        assert result["results"][0]["status"] == "created"
        # Verify del-port was called for the stale port
        assert any("del-port" in str(c) for c in ovs_calls)

    @pytest.mark.asyncio
    async def test_ovs_query_failure_returns_skipped(self, monkeypatch):
        """OVS batch read failure should return skipped status."""
        mgr = _make_manager()

        async def noop():
            pass

        monkeypatch.setattr(mgr, "_ensure_ovs_bridge", noop)
        monkeypatch.setattr(mgr, "_batch_read_ovs_ports", AsyncMock(return_value=None))

        result = await mgr.declare_state([{
            "link_id": "x", "lab_id": "y", "vni": 1,
            "local_ip": "1.1.1.1", "remote_ip": "2.2.2.2",
            "expected_vlan": 100, "port_name": "vxlan-x",
        }])
        assert "skipped" in result

    @pytest.mark.asyncio
    async def test_orphan_cleanup_scoped_to_declared_labs(self, monkeypatch):
        """Orphan cleanup should only remove ports from declared labs."""
        mgr = _make_manager()

        # Pre-populate with a tunnel in lab1
        orphan_lt = LinkTunnel(
            link_id="orphan-link",
            vni=9999,
            local_ip="10.0.0.1",
            remote_ip="10.0.0.2",
            local_vlan=300,
            interface_name="vxlan-orphan",
            lab_id="lab1",
        )
        mgr._link_tunnels["orphan-link"] = orphan_lt

        # And a tunnel in lab2 (should not be removed)
        other_lt = LinkTunnel(
            link_id="other-link",
            vni=8888,
            local_ip="10.0.0.1",
            remote_ip="10.0.0.3",
            local_vlan=400,
            interface_name="vxlan-other",
            lab_id="lab2",
        )
        mgr._link_tunnels["other-link"] = other_lt

        async def noop():
            pass

        monkeypatch.setattr(mgr, "_ensure_ovs_bridge", noop)

        async def fake_batch():
            return {
                "vxlan-orphan": {"name": "vxlan-orphan", "tag": 300, "type": "vxlan", "ofport": 5},
                "vxlan-other": {"name": "vxlan-other", "tag": 400, "type": "vxlan", "ofport": 6},
            }

        monkeypatch.setattr(mgr, "_batch_read_ovs_ports", fake_batch)
        monkeypatch.setattr(mgr, "_delete_vxlan_device", AsyncMock())
        monkeypatch.setattr(mgr, "_write_declared_state_cache", AsyncMock())

        # Declare no tunnels for lab1 (so vxlan-orphan is orphan)
        result = await mgr.declare_state([], declared_labs=["lab1"])
        assert "vxlan-orphan" in result["orphans_removed"]
        # vxlan-other should not be removed (lab2 not in declared_labs)
        assert "vxlan-other" not in result["orphans_removed"]


class TestBatchReadOvsPorts:
    """Tests for batch_read_ovs_ports helper."""

    @pytest.mark.asyncio
    async def test_empty_bridge(self, monkeypatch):
        import agent.network.overlay_state as mod

        monkeypatch.setattr(
            mod, "_shared_ovs_vsctl",
            AsyncMock(return_value=(0, "", "")),
        )

        result = await mod.batch_read_ovs_ports("arch-ovs")
        assert result == {}

    @pytest.mark.asyncio
    async def test_ovs_failure_returns_none(self, monkeypatch):
        import agent.network.overlay_state as mod

        monkeypatch.setattr(
            mod, "_shared_ovs_vsctl",
            AsyncMock(return_value=(1, "", "not found")),
        )

        result = await mod.batch_read_ovs_ports("arch-ovs")
        assert result is None

    @pytest.mark.asyncio
    async def test_filters_non_vxlan_ports(self, monkeypatch):
        import agent.network.overlay_state as mod

        call_count = [0]

        async def fake_ovs_vsctl(*args):
            call_count[0] += 1
            if call_count[0] == 1:
                # list-ports: mix of vxlan and non-vxlan
                return (0, "vxlan-abc\nvnet123\neth0\n", "")
            # For batch reads, return empty JSON
            return (0, '{"data": []}', "")

        monkeypatch.setattr(mod, "_shared_ovs_vsctl", fake_ovs_vsctl)

        result = await mod.batch_read_ovs_ports("arch-ovs")
        # Only vxlan ports should be included (but data was empty)
        assert isinstance(result, dict)


class TestDeclaredStateCache:
    """Tests for write/load declared state cache."""

    @pytest.mark.asyncio
    async def test_write_and_load_round_trip(self, tmp_path, monkeypatch):
        from agent.network.overlay_state import write_declared_state_cache, load_declared_state_cache
        import agent.config

        monkeypatch.setattr(agent.config.settings, "workspace_path", str(tmp_path))

        tunnels = [
            {"link_id": "r1:eth1-r2:eth1", "lab_id": "lab1", "vni": 5000},
        ]

        await write_declared_state_cache(tunnels)
        loaded = await load_declared_state_cache()

        assert loaded is not None
        assert len(loaded) == 1
        assert loaded[0]["link_id"] == "r1:eth1-r2:eth1"

    @pytest.mark.asyncio
    async def test_load_missing_cache_returns_none(self, tmp_path, monkeypatch):
        from agent.network.overlay_state import load_declared_state_cache
        import agent.config

        monkeypatch.setattr(agent.config.settings, "workspace_path", str(tmp_path))

        result = await load_declared_state_cache()
        assert result is None

    @pytest.mark.asyncio
    async def test_load_corrupt_cache_returns_none(self, tmp_path, monkeypatch):
        from agent.network.overlay_state import load_declared_state_cache
        import agent.config

        monkeypatch.setattr(agent.config.settings, "workspace_path", str(tmp_path))

        cache_path = tmp_path / "declared_overlay_state.json"
        cache_path.write_text("not valid json{{{")

        result = await load_declared_state_cache()
        assert result is None

    @pytest.mark.asyncio
    async def test_load_empty_tunnels_returns_none(self, tmp_path, monkeypatch):
        from agent.network.overlay_state import load_declared_state_cache
        import agent.config

        monkeypatch.setattr(agent.config.settings, "workspace_path", str(tmp_path))

        cache_path = tmp_path / "declared_overlay_state.json"
        cache_path.write_text(json.dumps({"tunnels": [], "declared_at": "2026-01-01"}))

        result = await load_declared_state_cache()
        assert result is None
