"""Batch 5 tests for OverlayManager — covers dataclass properties,
helper functions (_vni_to_vlan, _host_pair_vni, _link_tunnel_interface_name,
_read_vxlan_link_info, _batch_read_ovs_ports), cleanup_lab, recover_link_tunnels,
get_tunnel_status, and simple accessors.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.network.overlay import (
    LinkTunnel,
    OverlayBridge,
    OverlayManager,
    OVERLAY_VLAN_BASE,
    OVERLAY_VLAN_MAX,
    Vtep,
    VxlanTunnel,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_overlay(monkeypatch, tmp_path):
    """Create an OverlayManager with mocked I/O, bypassing __init__ Docker."""
    monkeypatch.setattr(
        "agent.network.overlay.settings",
        SimpleNamespace(
            ovs_bridge_name="arch-ovs",
            overlay_mtu=1400,
            workspace_path=str(tmp_path),
            vxlan_vni_base=100000,
            vxlan_vni_max=200000,
        ),
    )
    mgr = OverlayManager.__new__(OverlayManager)
    mgr._docker = None
    mgr._tunnels = {}
    mgr._bridges = {}
    mgr._vteps = {}
    mgr._link_tunnels = {}
    mgr._ovs_initialized = True
    mgr._bridge_name = "arch-ovs"
    mgr._mtu_cache = {}
    mgr._run_cmd = AsyncMock(return_value=(0, "", ""))
    mgr._ovs_vsctl = AsyncMock(return_value=(0, "", ""))
    mgr._ip_link_exists = AsyncMock(return_value=False)
    mgr._ensure_ovs_bridge = AsyncMock()
    mgr._create_vxlan_device = AsyncMock()
    mgr._delete_vxlan_device = AsyncMock()
    mgr._batch_read_ovs_ports = AsyncMock(return_value={})
    mgr._write_declared_state_cache = AsyncMock()
    return mgr


# ===========================================================================
# Dataclass properties
# ===========================================================================
class TestDataclassProperties:
    def test_vxlan_tunnel_key(self):
        t = VxlanTunnel(
            vni=100000, local_ip="10.0.0.1", remote_ip="10.0.0.2",
            interface_name="vxlan100000", lab_id="lab1", link_id="r1:eth1-r2:eth1",
            vlan_tag=2050,
        )
        assert t.key == "lab1:r1:eth1-r2:eth1"

    def test_vtep_key_and_link_count(self):
        v = Vtep(
            interface_name="vtep-10.0.0.2", vni=150000,
            local_ip="10.0.0.1", remote_ip="10.0.0.2",
            links={"link1", "link2", "link3"},
        )
        assert v.key == "10.0.0.2"
        assert v.link_count == 3

    def test_vtep_empty_links(self):
        v = Vtep(
            interface_name="vtep-10.0.0.3", vni=150001,
            local_ip="10.0.0.1", remote_ip="10.0.0.3",
        )
        assert v.link_count == 0

    def test_link_tunnel_key(self):
        lt = LinkTunnel(
            link_id="r1:eth1-r2:eth1", vni=100001,
            local_ip="10.0.0.1", remote_ip="10.0.0.2",
            local_vlan=2051, interface_name="vxlan-abc12345", lab_id="lab1",
        )
        assert lt.key == "r1:eth1-r2:eth1"

    def test_overlay_bridge_key(self):
        b = OverlayBridge(
            name="arch-ovs", vni=100000, vlan_tag=2050,
            lab_id="lab1", link_id="r1:eth1-r2:eth1",
        )
        assert b.key == "lab1:r1:eth1-r2:eth1"


# ===========================================================================
# _vni_to_vlan
# ===========================================================================
class TestVniToVlan:
    def test_basic_mapping(self, monkeypatch, tmp_path):
        mgr = _make_overlay(monkeypatch, tmp_path)
        result = mgr._vni_to_vlan(0)
        assert result == OVERLAY_VLAN_BASE

    def test_wraps_within_range(self, monkeypatch, tmp_path):
        mgr = _make_overlay(monkeypatch, tmp_path)
        vlan_range = OVERLAY_VLAN_MAX - OVERLAY_VLAN_BASE
        # VNI exactly at range boundary should wrap
        result = mgr._vni_to_vlan(vlan_range)
        assert result == OVERLAY_VLAN_BASE

    def test_always_in_range(self, monkeypatch, tmp_path):
        mgr = _make_overlay(monkeypatch, tmp_path)
        for vni in [0, 1, 100, 999999, 16777215]:
            vlan = mgr._vni_to_vlan(vni)
            assert OVERLAY_VLAN_BASE <= vlan < OVERLAY_VLAN_MAX


# ===========================================================================
# _host_pair_vni
# ===========================================================================
class TestHostPairVni:
    def test_deterministic(self, monkeypatch, tmp_path):
        mgr = _make_overlay(monkeypatch, tmp_path)
        vni1 = mgr._host_pair_vni("10.0.0.1", "10.0.0.2")
        vni2 = mgr._host_pair_vni("10.0.0.1", "10.0.0.2")
        assert vni1 == vni2

    def test_symmetric(self, monkeypatch, tmp_path):
        """Both hosts should generate the same VNI."""
        mgr = _make_overlay(monkeypatch, tmp_path)
        vni_forward = mgr._host_pair_vni("10.0.0.1", "10.0.0.2")
        vni_reverse = mgr._host_pair_vni("10.0.0.2", "10.0.0.1")
        assert vni_forward == vni_reverse

    def test_different_pairs_different_vnis(self, monkeypatch, tmp_path):
        mgr = _make_overlay(monkeypatch, tmp_path)
        vni1 = mgr._host_pair_vni("10.0.0.1", "10.0.0.2")
        vni2 = mgr._host_pair_vni("10.0.0.1", "10.0.0.3")
        assert vni1 != vni2

    def test_in_configured_range(self, monkeypatch, tmp_path):
        mgr = _make_overlay(monkeypatch, tmp_path)
        vni = mgr._host_pair_vni("192.168.1.100", "192.168.1.200")
        assert 100000 <= vni < 200000


# ===========================================================================
# _link_tunnel_interface_name
# ===========================================================================
class TestLinkTunnelInterfaceName:
    def test_format(self):
        name = OverlayManager._link_tunnel_interface_name("lab1", "r1:eth1-r2:eth1")
        assert name.startswith("vxlan-")
        assert len(name) <= 14  # within 15-char OVS limit

    def test_deterministic(self):
        name1 = OverlayManager._link_tunnel_interface_name("lab1", "link-1")
        name2 = OverlayManager._link_tunnel_interface_name("lab1", "link-1")
        assert name1 == name2

    def test_different_inputs_different_names(self):
        name1 = OverlayManager._link_tunnel_interface_name("lab1", "link-1")
        name2 = OverlayManager._link_tunnel_interface_name("lab1", "link-2")
        assert name1 != name2

    def test_hash_length(self):
        name = OverlayManager._link_tunnel_interface_name("lab1", "link-1")
        # Format: "vxlan-" + 8 hex chars
        assert len(name) == 14


# ===========================================================================
# _read_vxlan_link_info
# ===========================================================================
class TestReadVxlanLinkInfo:
    @pytest.mark.asyncio
    async def test_parses_output(self, monkeypatch, tmp_path):
        mgr = _make_overlay(monkeypatch, tmp_path)
        # Simulated `ip -d link show` output for a VXLAN device
        ip_output = (
            "18: vxlan-abc12345: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1450 ...\n"
            "    link/ether fa:16:3e:xx:xx:xx brd ff:ff:ff:ff:ff:ff\n"
            "    vxlan id 100001 remote 10.0.0.2 local 10.0.0.1 dev eth0 srcport 0 0 dstport 4789\n"
        )
        mgr._run_cmd = AsyncMock(return_value=(0, ip_output, ""))

        vni, remote_ip, local_ip = await mgr._read_vxlan_link_info("vxlan-abc12345")
        assert vni == 100001
        assert remote_ip == "10.0.0.2"
        assert local_ip == "10.0.0.1"

    @pytest.mark.asyncio
    async def test_command_failure(self, monkeypatch, tmp_path):
        mgr = _make_overlay(monkeypatch, tmp_path)
        mgr._run_cmd = AsyncMock(return_value=(1, "", "not found"))

        vni, remote_ip, local_ip = await mgr._read_vxlan_link_info("bad-dev")
        assert vni == 0
        assert remote_ip == ""
        assert local_ip == ""

    @pytest.mark.asyncio
    async def test_invalid_vni(self, monkeypatch, tmp_path):
        mgr = _make_overlay(monkeypatch, tmp_path)
        ip_output = "    vxlan id notanumber remote 10.0.0.2 local 10.0.0.1\n"
        mgr._run_cmd = AsyncMock(return_value=(0, ip_output, ""))

        vni, remote_ip, local_ip = await mgr._read_vxlan_link_info("dev1")
        assert vni == 0
        assert remote_ip == "10.0.0.2"

    @pytest.mark.asyncio
    async def test_partial_output(self, monkeypatch, tmp_path):
        mgr = _make_overlay(monkeypatch, tmp_path)
        ip_output = "    vxlan id 50000 remote 10.0.0.5\n"  # no local
        mgr._run_cmd = AsyncMock(return_value=(0, ip_output, ""))

        vni, remote_ip, local_ip = await mgr._read_vxlan_link_info("dev1")
        assert vni == 50000
        assert remote_ip == "10.0.0.5"
        assert local_ip == ""


# ===========================================================================
# _batch_read_ovs_ports (actual logic, not mocked)
# ===========================================================================
class TestBatchReadOvsPorts:
    @pytest.mark.asyncio
    async def test_reads_vxlan_ports(self, monkeypatch, tmp_path):
        mgr = _make_overlay(monkeypatch, tmp_path)
        # Remove the mock to test actual implementation
        del mgr._batch_read_ovs_ports

        call_count = 0

        async def fake_ovs(*args):
            nonlocal call_count
            call_count += 1
            if args == ("list-ports", "arch-ovs"):
                return 0, "vxlan-abc12345\nvh-r1-e1\nvxlan-def67890\n", ""
            elif args == ("get", "port", "vxlan-abc12345", "tag"):
                return 0, "2050", ""
            elif args == ("get", "interface", "vxlan-abc12345", "type"):
                return 0, '"vxlan"', ""
            elif args == ("get", "port", "vxlan-def67890", "tag"):
                return 0, "[]", ""
            elif args == ("get", "interface", "vxlan-def67890", "type"):
                return 0, '"vxlan"', ""
            return 0, "", ""

        mgr._ovs_vsctl = fake_ovs
        result = await mgr._batch_read_ovs_ports()

        assert "vxlan-abc12345" in result
        assert result["vxlan-abc12345"]["tag"] == 2050
        assert "vxlan-def67890" in result
        assert result["vxlan-def67890"]["tag"] == 0
        # vh-r1-e1 should be skipped (not vxlan prefix)
        assert "vh-r1-e1" not in result

    @pytest.mark.asyncio
    async def test_empty_bridge(self, monkeypatch, tmp_path):
        mgr = _make_overlay(monkeypatch, tmp_path)
        del mgr._batch_read_ovs_ports
        mgr._ovs_vsctl = AsyncMock(return_value=(1, "", "no bridge"))

        result = await mgr._batch_read_ovs_ports()
        assert result == {}


# ===========================================================================
# cleanup_lab
# ===========================================================================
class TestCleanupLab:
    @pytest.mark.asyncio
    async def test_cleans_all_types(self, monkeypatch, tmp_path):
        mgr = _make_overlay(monkeypatch, tmp_path)

        # Add a tunnel, bridge, and link_tunnel for the target lab
        mgr._tunnels["lab1:link-1"] = VxlanTunnel(
            vni=100000, local_ip="10.0.0.1", remote_ip="10.0.0.2",
            interface_name="vxlan100000", lab_id="lab1", link_id="link-1",
            vlan_tag=2050,
        )
        mgr._bridges["lab1:r1:eth1-r2:eth1"] = OverlayBridge(
            name="arch-ovs", vni=100000, vlan_tag=2050,
            lab_id="lab1", link_id="r1:eth1-r2:eth1",
        )
        mgr._link_tunnels["lt-1"] = LinkTunnel(
            link_id="lt-1", vni=100001, local_ip="10.0.0.1", remote_ip="10.0.0.2",
            local_vlan=2051, interface_name="vxlan-abc", lab_id="lab1",
        )

        # Add unrelated lab resources that should survive
        mgr._tunnels["lab2:link-2"] = VxlanTunnel(
            vni=200000, local_ip="10.0.0.1", remote_ip="10.0.0.3",
            interface_name="vxlan200000", lab_id="lab2", link_id="link-2",
            vlan_tag=2060,
        )

        mgr.delete_tunnel = AsyncMock(return_value=True)
        mgr.delete_bridge = AsyncMock(return_value=True)
        mgr.delete_link_tunnel = AsyncMock(return_value=True)

        result = await mgr.cleanup_lab("lab1")

        assert result["tunnels_deleted"] == 1
        assert result["bridges_deleted"] == 1
        assert result["link_tunnels_deleted"] == 1
        assert len(result["errors"]) == 0
        # lab2 resources should remain
        assert "lab2:link-2" in mgr._tunnels

    @pytest.mark.asyncio
    async def test_empty_lab(self, monkeypatch, tmp_path):
        mgr = _make_overlay(monkeypatch, tmp_path)
        result = await mgr.cleanup_lab("nonexistent")
        assert result["tunnels_deleted"] == 0
        assert result["link_tunnels_deleted"] == 0

    @pytest.mark.asyncio
    async def test_error_handling(self, monkeypatch, tmp_path):
        mgr = _make_overlay(monkeypatch, tmp_path)
        mgr._link_tunnels["lt-err"] = LinkTunnel(
            link_id="lt-err", vni=100099, local_ip="10.0.0.1", remote_ip="10.0.0.2",
            local_vlan=2099, interface_name="vxlan-err", lab_id="lab-err",
        )
        mgr.delete_link_tunnel = AsyncMock(side_effect=Exception("OVS exploded"))

        result = await mgr.cleanup_lab("lab-err")
        assert len(result["errors"]) == 1
        assert "OVS exploded" in result["errors"][0]


# ===========================================================================
# recover_link_tunnels
# ===========================================================================
class TestRecoverLinkTunnels:
    @pytest.mark.asyncio
    async def test_cache_recovery(self, monkeypatch, tmp_path):
        mgr = _make_overlay(monkeypatch, tmp_path)

        cached_tunnels = [
            {"link_id": "link-1", "vni": 100001, "local_ip": "10.0.0.1",
             "remote_ip": "10.0.0.2", "local_vlan": 2050, "lab_id": "lab1"},
        ]
        mgr.load_declared_state_cache = AsyncMock(return_value=cached_tunnels)
        mgr.declare_state = AsyncMock(return_value={
            "results": [{"link_id": "link-1", "status": "created"}],
        })

        count = await mgr.recover_link_tunnels()
        assert count == 1
        mgr.declare_state.assert_called_once_with(cached_tunnels)

    @pytest.mark.asyncio
    async def test_ovs_scan_fallback(self, monkeypatch, tmp_path):
        mgr = _make_overlay(monkeypatch, tmp_path)
        mgr.load_declared_state_cache = AsyncMock(return_value=None)

        call_seq = 0

        async def fake_ovs(*args):
            nonlocal call_seq
            call_seq += 1
            if args == ("list-ports", "arch-ovs"):
                return 0, "vxlan-aaa11111\nvxlan-bbb22222\nvh-r1-e1\n", ""
            elif args[0] == "get" and args[1] == "port":
                port_name = args[2]
                if port_name == "vxlan-aaa11111":
                    return 0, "2050", ""
                elif port_name == "vxlan-bbb22222":
                    return 0, "2051", ""
            return 0, "", ""

        mgr._ovs_vsctl = fake_ovs

        async def fake_read(name):
            if name == "vxlan-aaa11111":
                return 100001, "10.0.0.2", "10.0.0.1"
            elif name == "vxlan-bbb22222":
                return 100002, "10.0.0.3", "10.0.0.1"
            return 0, "", ""

        mgr._read_vxlan_link_info = fake_read

        count = await mgr.recover_link_tunnels()
        assert count == 2
        assert "vxlan-aaa11111" in mgr._link_tunnels
        assert "vxlan-bbb22222" in mgr._link_tunnels
        # Placeholder lab_id for OVS-scan recovery
        assert mgr._link_tunnels["vxlan-aaa11111"].lab_id == "recovered"

    @pytest.mark.asyncio
    async def test_ovs_scan_skips_invalid(self, monkeypatch, tmp_path):
        mgr = _make_overlay(monkeypatch, tmp_path)
        mgr.load_declared_state_cache = AsyncMock(return_value=None)

        mgr._ovs_vsctl = AsyncMock(side_effect=[
            (0, "vxlan-xxx\n", ""),  # list-ports
            (0, "2050", ""),          # get tag
        ])
        mgr._read_vxlan_link_info = AsyncMock(return_value=(0, "", ""))  # Invalid

        count = await mgr.recover_link_tunnels()
        assert count == 0

    @pytest.mark.asyncio
    async def test_cache_recovery_failure_falls_back(self, monkeypatch, tmp_path):
        mgr = _make_overlay(monkeypatch, tmp_path)
        mgr.load_declared_state_cache = AsyncMock(return_value=[{"bad": "data"}])
        mgr.declare_state = AsyncMock(side_effect=Exception("parse error"))
        mgr._ovs_vsctl = AsyncMock(return_value=(1, "", ""))  # OVS scan also empty

        count = await mgr.recover_link_tunnels()
        assert count == 0


# ===========================================================================
# get_tunnel_status
# ===========================================================================
class TestGetTunnelStatus:
    def test_returns_all_sections(self, monkeypatch, tmp_path):
        mgr = _make_overlay(monkeypatch, tmp_path)
        mgr._vteps["10.0.0.2"] = Vtep(
            interface_name="vtep-10.0.0.2", vni=150000,
            local_ip="10.0.0.1", remote_ip="10.0.0.2",
        )
        mgr._link_tunnels["link-1"] = LinkTunnel(
            link_id="link-1", vni=100001, local_ip="10.0.0.1", remote_ip="10.0.0.2",
            local_vlan=2050, interface_name="vxlan-abc", lab_id="lab1",
        )

        status = mgr.get_tunnel_status()

        assert status["ovs_bridge"] == "arch-ovs"
        assert "vteps" in status
        assert "link_tunnels" in status
        assert len(status["vteps"]) == 1
        assert len(status["link_tunnels"]) == 1

    def test_empty_state(self, monkeypatch, tmp_path):
        mgr = _make_overlay(monkeypatch, tmp_path)
        status = mgr.get_tunnel_status()
        assert status["ovs_bridge"] == "arch-ovs"
        assert len(status["vteps"]) == 0
        assert len(status["link_tunnels"]) == 0


# ===========================================================================
# Simple accessors
# ===========================================================================
class TestAccessors:
    @pytest.mark.asyncio
    async def test_get_tunnels_for_lab(self, monkeypatch, tmp_path):
        mgr = _make_overlay(monkeypatch, tmp_path)
        mgr._tunnels["lab1:link-1"] = VxlanTunnel(
            vni=100000, local_ip="10.0.0.1", remote_ip="10.0.0.2",
            interface_name="vx100000", lab_id="lab1", link_id="link-1", vlan_tag=2050,
        )
        mgr._tunnels["lab2:link-2"] = VxlanTunnel(
            vni=200000, local_ip="10.0.0.1", remote_ip="10.0.0.3",
            interface_name="vx200000", lab_id="lab2", link_id="link-2", vlan_tag=2060,
        )

        result = await mgr.get_tunnels_for_lab("lab1")
        assert len(result) == 1
        assert result[0].vni == 100000

    @pytest.mark.asyncio
    async def test_get_bridges_for_lab(self, monkeypatch, tmp_path):
        mgr = _make_overlay(monkeypatch, tmp_path)
        mgr._bridges["lab1:100000"] = OverlayBridge(
            vni=100000, interface_name="br", lab_id="lab1", bridge_name="lb",
        )
        result = await mgr.get_bridges_for_lab("lab1")
        assert len(result) == 1

    def test_get_all_vteps(self, monkeypatch, tmp_path):
        mgr = _make_overlay(monkeypatch, tmp_path)
        mgr._vteps["10.0.0.2"] = Vtep(
            interface_name="vtep1", vni=1, local_ip="10.0.0.1", remote_ip="10.0.0.2",
        )
        assert len(mgr.get_all_vteps()) == 1

    def test_get_all_link_tunnels(self, monkeypatch, tmp_path):
        mgr = _make_overlay(monkeypatch, tmp_path)
        mgr._link_tunnels["lt1"] = LinkTunnel(
            link_id="lt1", vni=1, local_ip="x", remote_ip="y",
            local_vlan=100, interface_name="vxlan-x", lab_id="lab1",
        )
        result = mgr.get_all_link_tunnels()
        assert len(result) == 1

    def test_get_link_tunnel(self, monkeypatch, tmp_path):
        mgr = _make_overlay(monkeypatch, tmp_path)
        lt = LinkTunnel(
            link_id="lt1", vni=1, local_ip="x", remote_ip="y",
            local_vlan=100, interface_name="vxlan-x", lab_id="lab1",
        )
        mgr._link_tunnels["lt1"] = lt
        assert mgr.get_link_tunnel("lt1") is lt
        assert mgr.get_link_tunnel("missing") is None


# ===========================================================================
# load_declared_state_cache / _write_declared_state_cache
# ===========================================================================
class TestDeclaredStateCache:
    @pytest.mark.asyncio
    async def test_write_and_load_roundtrip(self, monkeypatch, tmp_path):
        mgr = _make_overlay(monkeypatch, tmp_path)
        # Remove mock to test real implementation
        del mgr._write_declared_state_cache

        tunnels = [
            {"link_id": "link-1", "vni": 100001, "local_vlan": 2050},
            {"link_id": "link-2", "vni": 100002, "local_vlan": 2051},
        ]
        await mgr._write_declared_state_cache(tunnels)

        loaded = await mgr.load_declared_state_cache()
        assert loaded is not None
        assert len(loaded) == 2
        assert loaded[0]["link_id"] == "link-1"

    @pytest.mark.asyncio
    async def test_load_missing_file(self, monkeypatch, tmp_path):
        mgr = _make_overlay(monkeypatch, tmp_path)
        result = await mgr.load_declared_state_cache()
        assert result is None

    @pytest.mark.asyncio
    async def test_load_corrupt_file(self, monkeypatch, tmp_path):
        mgr = _make_overlay(monkeypatch, tmp_path)
        cache_path = tmp_path / "declared_overlay_state.json"
        cache_path.write_text("not json{{{")

        result = await mgr.load_declared_state_cache()
        assert result is None

    @pytest.mark.asyncio
    async def test_load_empty_tunnels(self, monkeypatch, tmp_path):
        mgr = _make_overlay(monkeypatch, tmp_path)
        cache_path = tmp_path / "declared_overlay_state.json"
        cache_path.write_text(json.dumps({"tunnels": [], "declared_at": "2026-01-01"}))

        result = await mgr.load_declared_state_cache()
        assert result is None  # Empty list is treated as no cache


# ===========================================================================
# _ensure_ovs_bridge (actual logic)
# ===========================================================================
class TestEnsureOvsBridge:
    @pytest.mark.asyncio
    async def test_creates_bridge_when_missing(self, monkeypatch, tmp_path):
        mgr = _make_overlay(monkeypatch, tmp_path)
        mgr._ovs_initialized = False
        del mgr._ensure_ovs_bridge

        call_log = []

        async def fake_ovs(*args):
            call_log.append(args)
            if args == ("--version",):
                return 0, "2.17.0", ""
            elif args == ("br-exists", "arch-ovs"):
                return 1, "", ""  # bridge doesn't exist
            elif args[0] == "add-br":
                return 0, "", ""
            elif args[0] == "get":
                return 0, '"standalone"', ""
            elif args[0] == "set-fail-mode":
                return 0, "", ""
            return 0, "", ""

        mgr._ovs_vsctl = fake_ovs
        mgr._run_cmd = AsyncMock(return_value=(0, "", ""))

        await mgr._ensure_ovs_bridge()
        assert mgr._ovs_initialized is True
        assert any("add-br" in str(c) for c in call_log)

    @pytest.mark.asyncio
    async def test_skips_when_initialized(self, monkeypatch, tmp_path):
        mgr = _make_overlay(monkeypatch, tmp_path)
        mgr._ovs_initialized = True
        del mgr._ensure_ovs_bridge

        mgr._ovs_vsctl = AsyncMock()
        await mgr._ensure_ovs_bridge()
        mgr._ovs_vsctl.assert_not_called()

    @pytest.mark.asyncio
    async def test_ovs_not_available_raises(self, monkeypatch, tmp_path):
        mgr = _make_overlay(monkeypatch, tmp_path)
        mgr._ovs_initialized = False
        del mgr._ensure_ovs_bridge

        mgr._ovs_vsctl = AsyncMock(return_value=(1, "", "ovs not found"))

        with pytest.raises(RuntimeError, match="OVS not available"):
            await mgr._ensure_ovs_bridge()
