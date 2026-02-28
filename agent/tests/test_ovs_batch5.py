"""Batch 5 tests for OVSNetworkManager — covers VlanAllocator gaps
(recover_from_ovs, prune_recovered, get_keys_for_vlan, get_stats, corrupt load),
OVSNetworkManager methods (set_port_vlan, delete_port, handle_container_restart,
attach/detach external, connect_to_external, create/delete_patch_to_bridge,
get_all_ovs_ports, delete_orphan_port, get_ovs_bridge_state, reconcile_with_ovs,
_generate_port_name, list_external_connections, get_ports_for_container,
get_link_by_endpoints, initialize).
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.network.ovs import (
    OVSLink,
    OVSNetworkManager,
    OVSPort,
    VlanAllocator,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_singleton():
    OVSNetworkManager._instance = None
    yield
    OVSNetworkManager._instance = None


def _make_allocator(tmp_path, start=100, end=4000):
    return VlanAllocator(start=start, end=end, persistence_path=tmp_path / "v.json")


def _make_manager(tmp_path):
    mgr = OVSNetworkManager()
    mgr._initialized = True
    mgr._bridge_name = "test-ovs"
    mgr._ports = {}
    mgr._links = {}
    mgr._vlan_allocator = _make_allocator(tmp_path)
    mgr._docker = MagicMock()
    mgr._ovs_vsctl = AsyncMock(return_value=(0, "", ""))
    mgr._run_cmd = AsyncMock(return_value=(0, "", ""))
    mgr._ip_link_exists = AsyncMock(return_value=False)
    mgr._get_container_pid = AsyncMock(return_value=42)
    return mgr


def _add_port(mgr, container, iface, lab_id, vlan=None, port_name=None):
    key = f"{container}:{iface}"
    if vlan is None:
        vlan = mgr._vlan_allocator.allocate(key)
    else:
        mgr._vlan_allocator._allocated[key] = vlan
    if port_name is None:
        port_name = f"vh-{container[:4]}-{iface}"
    port = OVSPort(
        port_name=port_name, container_name=container,
        interface_name=iface, vlan_tag=vlan, lab_id=lab_id,
    )
    mgr._ports[key] = port
    return port


# ===========================================================================
# VlanAllocator — previously untested methods
# ===========================================================================
class TestVlanAllocatorExtended:
    @pytest.mark.asyncio
    async def test_recover_from_ovs(self, tmp_path):
        alloc = _make_allocator(tmp_path)

        async def fake_subprocess(*args, stdout=None, stderr=None):
            proc = MagicMock()
            cmd = " ".join(args)
            if "list-ports" in cmd:
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"vh-r1-e1\nvh-r2-e1\nvxlan100\n", b""))
            elif "vh-r1-e1" in cmd and "tag" in cmd:
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"200", b""))
            elif "vh-r2-e1" in cmd and "tag" in cmd:
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"300", b""))
            elif "vxlan100" in cmd:
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"2050", b""))
            else:
                proc.returncode = 1
                proc.communicate = AsyncMock(return_value=(b"", b"error"))
            return proc

        with patch("agent.network.ovs.asyncio.create_subprocess_exec", side_effect=fake_subprocess):
            recovered = await alloc.recover_from_ovs("test-ovs")

        assert recovered == 2
        assert "_recovered:vh-r1-e1" in alloc._allocated
        assert alloc._allocated["_recovered:vh-r1-e1"] == 200
        assert "_recovered:vh-r2-e1" in alloc._allocated

    @pytest.mark.asyncio
    async def test_recover_skips_already_allocated(self, tmp_path):
        alloc = _make_allocator(tmp_path)
        alloc._allocated["existing-key"] = 200

        async def fake_subprocess(*args, stdout=None, stderr=None):
            proc = MagicMock()
            cmd = " ".join(args)
            if "list-ports" in cmd:
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"vh-r1-e1\n", b""))
            elif "tag" in cmd:
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"200", b""))
            else:
                proc.returncode = 1
                proc.communicate = AsyncMock(return_value=(b"", b""))
            return proc

        with patch("agent.network.ovs.asyncio.create_subprocess_exec", side_effect=fake_subprocess):
            recovered = await alloc.recover_from_ovs("test-ovs")

        assert recovered == 0

    @pytest.mark.asyncio
    async def test_prune_recovered(self, tmp_path):
        alloc = _make_allocator(tmp_path)
        alloc._allocated["_recovered:vh-old"] = 500
        alloc._allocated["_recovered:vh-still-here"] = 501
        alloc._allocated["normal-key"] = 100

        async def fake_subprocess(*args, stdout=None, stderr=None):
            proc = MagicMock()
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(b"vh-still-here\nvh-other\n", b""))
            return proc

        with patch("agent.network.ovs.asyncio.create_subprocess_exec", side_effect=fake_subprocess):
            removed = await alloc.prune_recovered_from_ovs("test-ovs")

        assert removed == 1
        assert "_recovered:vh-old" not in alloc._allocated
        assert "_recovered:vh-still-here" in alloc._allocated
        assert "normal-key" in alloc._allocated

    def test_get_keys_for_vlan(self, tmp_path):
        alloc = _make_allocator(tmp_path)
        alloc._allocated = {"key-a": 100, "key-b": 100, "key-c": 200}
        result = alloc.get_keys_for_vlan(100)
        assert sorted(result) == ["key-a", "key-b"]

    def test_get_keys_for_vlan_none(self, tmp_path):
        alloc = _make_allocator(tmp_path)
        assert alloc.get_keys_for_vlan(9999) == []

    def test_get_stats(self, tmp_path):
        alloc = _make_allocator(tmp_path)
        alloc._allocated = {"k1": 100, "k2": 200}
        stats = alloc.get_stats()
        assert stats["total_allocated"] == 2
        assert stats["vlan_range"] == "100-4000"
        assert stats["next_vlan"] == 100

    def test_corrupt_file_load(self, tmp_path):
        path = tmp_path / "v.json"
        path.write_text("not valid json{{{")
        alloc = VlanAllocator(start=100, end=4000, persistence_path=path)
        assert alloc._allocated == {}


# ===========================================================================
# OVSNetworkManager._generate_port_name
# ===========================================================================
class TestGeneratePortName:
    def test_length_limit(self, tmp_path):
        mgr = _make_manager(tmp_path)
        name = mgr._generate_port_name("archetype-lab123-very-long-router-name", "Ethernet99")
        assert len(name) <= 15

    def test_eth_shortening(self, tmp_path):
        mgr = _make_manager(tmp_path)
        name = mgr._generate_port_name("archetype-lab1-r1", "eth1")
        assert "vh" in name

    def test_ethernet_shortening(self, tmp_path):
        mgr = _make_manager(tmp_path)
        name = mgr._generate_port_name("archetype-lab1-r1", "Ethernet1")
        assert "vh" in name


# ===========================================================================
# set_port_vlan
# ===========================================================================
class TestSetPortVlan:
    @pytest.mark.asyncio
    async def test_success(self, tmp_path):
        mgr = _make_manager(tmp_path)
        _add_port(mgr, "r1", "eth1", "lab1", vlan=100)

        result = await mgr.set_port_vlan("r1", "eth1", 200)
        assert result is True
        assert mgr._ports["r1:eth1"].vlan_tag == 200

    @pytest.mark.asyncio
    async def test_port_not_found(self, tmp_path):
        mgr = _make_manager(tmp_path)
        result = await mgr.set_port_vlan("missing", "eth1", 200)
        assert result is False

    @pytest.mark.asyncio
    async def test_ovs_failure(self, tmp_path):
        mgr = _make_manager(tmp_path)
        _add_port(mgr, "r1", "eth1", "lab1", vlan=100)
        mgr._ovs_vsctl = AsyncMock(return_value=(1, "", "OVS error"))

        result = await mgr.set_port_vlan("r1", "eth1", 200)
        assert result is False
        assert mgr._ports["r1:eth1"].vlan_tag == 100


# ===========================================================================
# delete_port
# ===========================================================================
class TestDeletePort:
    @pytest.mark.asyncio
    async def test_success(self, tmp_path):
        mgr = _make_manager(tmp_path)
        _add_port(mgr, "r1", "eth1", "lab1", vlan=100)

        result = await mgr.delete_port("r1", "eth1")
        assert result is True
        assert "r1:eth1" not in mgr._ports

    @pytest.mark.asyncio
    async def test_not_found(self, tmp_path):
        mgr = _make_manager(tmp_path)
        result = await mgr.delete_port("missing", "eth1")
        assert result is False

    @pytest.mark.asyncio
    async def test_removes_associated_links(self, tmp_path):
        mgr = _make_manager(tmp_path)
        _add_port(mgr, "r1", "eth1", "lab1", vlan=100)
        _add_port(mgr, "r2", "eth1", "lab1", vlan=100)
        mgr._links["lab1:link-1"] = OVSLink(
            link_id="link-1", lab_id="lab1", port_a="r1:eth1", port_b="r2:eth1", vlan_tag=100,
        )

        await mgr.delete_port("r1", "eth1")
        assert "lab1:link-1" not in mgr._links


# ===========================================================================
# handle_container_restart
# ===========================================================================
class TestHandleContainerRestart:
    @pytest.mark.asyncio
    async def test_reprovisions_stale_ports(self, tmp_path):
        mgr = _make_manager(tmp_path)
        _add_port(mgr, "r1", "eth1", "lab1", vlan=100, port_name="vh-r1-e1")

        mgr.is_port_stale = AsyncMock(return_value=True)
        mgr._cleanup_stale_port = AsyncMock()
        mgr.provision_interface = AsyncMock(return_value=200)

        result = await mgr.handle_container_restart("r1", "lab1")
        assert result["ports_reprovisioned"] >= 1
        mgr._cleanup_stale_port.assert_called_once()
        mgr.provision_interface.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_stale_ports(self, tmp_path):
        mgr = _make_manager(tmp_path)
        _add_port(mgr, "r1", "eth1", "lab1", vlan=100)

        mgr.is_port_stale = AsyncMock(return_value=False)

        result = await mgr.handle_container_restart("r1", "lab1")
        assert result["ports_reprovisioned"] == 0

    @pytest.mark.asyncio
    async def test_no_ports_for_container(self, tmp_path):
        mgr = _make_manager(tmp_path)
        result = await mgr.handle_container_restart("unknown", "lab1")
        assert result["ports_reprovisioned"] == 0


# ===========================================================================
# attach_external_interface / detach_external_interface
# ===========================================================================
class TestExternalInterfaceOVS:
    @pytest.mark.asyncio
    async def test_attach_with_vlan(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._ip_link_exists = AsyncMock(return_value=True)
        mgr._ovs_vsctl = AsyncMock(side_effect=[
            (1, "", ""),  # port-to-br
            (0, "", ""),  # add-port
        ])

        vlan = await mgr.attach_external_interface("enp0s8", vlan_tag=500)
        assert vlan == 500

    @pytest.mark.asyncio
    async def test_attach_trunk_mode(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._ip_link_exists = AsyncMock(return_value=True)
        mgr._ovs_vsctl = AsyncMock(side_effect=[
            (1, "", ""),  # port-to-br
            (0, "", ""),  # add-port
        ])

        vlan = await mgr.attach_external_interface("enp0s8")
        assert vlan == 0

    @pytest.mark.asyncio
    async def test_attach_already_on_bridge(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._ip_link_exists = AsyncMock(return_value=True)
        mgr._ovs_vsctl = AsyncMock(return_value=(0, "test-ovs", ""))

        vlan = await mgr.attach_external_interface("enp0s8", vlan_tag=500)
        assert vlan == 500

    @pytest.mark.asyncio
    async def test_attach_interface_not_found(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._ip_link_exists = AsyncMock(return_value=False)

        with pytest.raises(RuntimeError, match="does not exist"):
            await mgr.attach_external_interface("bad-iface")

    @pytest.mark.asyncio
    async def test_detach_success(self, tmp_path):
        mgr = _make_manager(tmp_path)
        result = await mgr.detach_external_interface("enp0s8")
        assert result is True

    @pytest.mark.asyncio
    async def test_detach_failure(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._ovs_vsctl = AsyncMock(return_value=(1, "", "error"))
        result = await mgr.detach_external_interface("enp0s8")
        assert result is False


# ===========================================================================
# connect_to_external
# ===========================================================================
class TestConnectToExternal:
    @pytest.mark.asyncio
    async def test_uses_port_vlan(self, tmp_path):
        mgr = _make_manager(tmp_path)
        _add_port(mgr, "r1", "eth1", "lab1", vlan=300)
        mgr.attach_external_interface = AsyncMock(return_value=300)

        result = await mgr.connect_to_external("r1", "eth1", "enp0s8")
        assert result == 300

    @pytest.mark.asyncio
    async def test_with_explicit_vlan(self, tmp_path):
        mgr = _make_manager(tmp_path)
        _add_port(mgr, "r1", "eth1", "lab1", vlan=300)
        mgr.attach_external_interface = AsyncMock(return_value=500)
        mgr.set_port_vlan = AsyncMock(return_value=True)

        result = await mgr.connect_to_external("r1", "eth1", "enp0s8", vlan_tag=500)
        assert result == 500
        mgr.set_port_vlan.assert_called_once()

    @pytest.mark.asyncio
    async def test_port_not_provisioned(self, tmp_path):
        mgr = _make_manager(tmp_path)
        with pytest.raises(RuntimeError, match="not provisioned"):
            await mgr.connect_to_external("missing", "eth1", "enp0s8")


# ===========================================================================
# create_patch_to_bridge / delete_patch_to_bridge
# ===========================================================================
class TestPatchToBridge:
    @pytest.mark.asyncio
    async def test_ovs_bridge_patch(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._ovs_vsctl = AsyncMock(side_effect=[
            (0, "", ""),   # br-exists
            (0, "", ""),   # add-port local
            (0, "", ""),   # add-port remote
        ])

        name = await mgr.create_patch_to_bridge("target-br", vlan_tag=100)
        assert name.startswith("patch-to-")

    @pytest.mark.asyncio
    async def test_linux_bridge_veth(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._ovs_vsctl = AsyncMock(side_effect=[
            (1, "", ""),   # br-exists
            (0, "", ""),   # add-port local to OVS
        ])

        name = await mgr.create_patch_to_bridge("linux-br")
        assert len(name) <= 15

    @pytest.mark.asyncio
    async def test_ovs_patch_remote_failure_cleans_up(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._ovs_vsctl = AsyncMock(side_effect=[
            (0, "", ""),           # br-exists
            (0, "", ""),           # add-port local
            (1, "", "remote fail"),  # add-port remote
        ])

        with pytest.raises(RuntimeError, match="Failed to create remote patch"):
            await mgr.create_patch_to_bridge("target-br")

    @pytest.mark.asyncio
    async def test_delete_ovs_patch(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._ovs_vsctl = AsyncMock(side_effect=[
            (0, "", ""),  # br-exists (OVS)
            (0, "", ""),  # del-port local
            (0, "", ""),  # del-port remote
        ])
        result = await mgr.delete_patch_to_bridge("target-br")
        assert result is True

    @pytest.mark.asyncio
    async def test_delete_linux_patch(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._ovs_vsctl = AsyncMock(side_effect=[
            (1, "", ""),  # br-exists (Linux)
            (0, "", ""),  # del-port OVS side
        ])
        result = await mgr.delete_patch_to_bridge("linux-br")
        assert result is True

    @pytest.mark.asyncio
    async def test_delete_not_initialized(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._initialized = False
        result = await mgr.delete_patch_to_bridge("target-br")
        assert result is False


# ===========================================================================
# get_all_ovs_ports
# ===========================================================================
class TestGetAllOvsPorts:
    @pytest.mark.asyncio
    async def test_parses_ports(self, tmp_path):
        mgr = _make_manager(tmp_path)

        async def fake_ovs(*args):
            if args == ("list-ports", "test-ovs"):
                return 0, "vh-r1-e1\nvxlan-abc\n", ""
            elif args[0] == "get" and args[1] == "port":
                return 0, "200", ""
            elif args[0] == "get" and args[1] == "interface" and args[3] == "type":
                return 0, '"system"', ""
            elif args[0] == "get" and args[1] == "interface" and args[3] == "error":
                return 0, '""', ""
            return 0, "", ""

        mgr._ovs_vsctl = fake_ovs
        ports = await mgr.get_all_ovs_ports()
        assert len(ports) == 2
        assert ports[0]["port_name"] == "vh-r1-e1"
        assert ports[0]["vlan_tag"] == 200

    @pytest.mark.asyncio
    async def test_not_initialized(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._initialized = False
        assert await mgr.get_all_ovs_ports() == []


# ===========================================================================
# delete_orphan_port
# ===========================================================================
class TestDeleteOrphanPort:
    @pytest.mark.asyncio
    async def test_deletes_untracked(self, tmp_path):
        mgr = _make_manager(tmp_path)
        result = await mgr.delete_orphan_port("vh-orphan")
        assert result is True

    @pytest.mark.asyncio
    async def test_refuses_tracked_port(self, tmp_path):
        mgr = _make_manager(tmp_path)
        _add_port(mgr, "r1", "eth1", "lab1", vlan=100, port_name="vh-r1-e1")

        result = await mgr.delete_orphan_port("vh-r1-e1")
        assert result is False

    @pytest.mark.asyncio
    async def test_ovs_failure(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._ovs_vsctl = AsyncMock(return_value=(1, "", "fail"))
        result = await mgr.delete_orphan_port("vh-orphan")
        assert result is False

    @pytest.mark.asyncio
    async def test_not_initialized(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._initialized = False
        result = await mgr.delete_orphan_port("vh-orphan")
        assert result is False


# ===========================================================================
# get_ovs_bridge_state
# ===========================================================================
class TestGetOvsBridgeState:
    @pytest.mark.asyncio
    async def test_not_initialized(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._initialized = False
        state = await mgr.get_ovs_bridge_state()
        assert state["bridge_exists"] is False

    @pytest.mark.asyncio
    async def test_bridge_missing(self, tmp_path):
        mgr = _make_manager(tmp_path)
        _add_port(mgr, "r1", "eth1", "lab1", vlan=100)
        mgr._ovs_vsctl = AsyncMock(return_value=(1, "", ""))

        state = await mgr.get_ovs_bridge_state()
        assert state["bridge_exists"] is False
        assert len(state["missing_ports"]) > 0

    @pytest.mark.asyncio
    async def test_detects_orphaned_and_missing(self, tmp_path):
        mgr = _make_manager(tmp_path)
        _add_port(mgr, "r1", "eth1", "lab1", vlan=100, port_name="vh-r1-e1")

        mgr.get_all_ovs_ports = AsyncMock(return_value=[
            {"port_name": "vh-orphan", "vlan_tag": 200, "type": "system", "error": "No such device"},
        ])
        mgr._ovs_vsctl = AsyncMock(return_value=(0, "", ""))

        state = await mgr.get_ovs_bridge_state()
        assert state["bridge_exists"] is True
        assert len(state["orphaned_ports"]) == 1
        assert state["orphaned_ports"][0]["port_name"] == "vh-orphan"
        assert "r1:eth1" in state["missing_ports"]


# ===========================================================================
# reconcile_with_ovs
# ===========================================================================
class TestReconcileWithOvs:
    @pytest.mark.asyncio
    async def test_not_initialized(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._initialized = False
        stats = await mgr.reconcile_with_ovs()
        assert stats["tracked_removed"] == 0

    @pytest.mark.asyncio
    async def test_bridge_gone_clears_all(self, tmp_path):
        mgr = _make_manager(tmp_path)
        _add_port(mgr, "r1", "eth1", "lab1", vlan=100)

        mgr.get_ovs_bridge_state = AsyncMock(return_value={
            "bridge_exists": False,
            "ports": [],
            "tracked_ports": ["r1:eth1"],
            "orphaned_ports": [],
            "missing_ports": ["r1:eth1"],
        })

        stats = await mgr.reconcile_with_ovs()
        assert stats["tracked_removed"] == 1
        assert len(mgr._ports) == 0

    @pytest.mark.asyncio
    async def test_removes_missing_and_orphans(self, tmp_path):
        mgr = _make_manager(tmp_path)
        _add_port(mgr, "r1", "eth1", "lab1", vlan=100, port_name="vh-r1-e1")
        mgr._links["lab1:link-1"] = OVSLink(
            link_id="link-1", lab_id="lab1", port_a="r1:eth1", port_b="r2:eth1", vlan_tag=100,
        )

        mgr.get_ovs_bridge_state = AsyncMock(return_value={
            "bridge_exists": True,
            "bridge_name": "test-ovs",
            "ports": [
                {"port_name": "vh-orphan", "vlan_tag": 200, "error": "No such device"},
            ],
            "tracked_ports": ["r1:eth1"],
            "orphaned_ports": [{"port_name": "vh-orphan"}],
            "missing_ports": ["r1:eth1"],
        })
        mgr.delete_orphan_port = AsyncMock(return_value=True)

        stats = await mgr.reconcile_with_ovs()
        assert stats["tracked_removed"] == 1
        assert stats["orphans_deleted"] == 1
        assert "r1:eth1" not in mgr._ports
        assert "lab1:link-1" not in mgr._links

    @pytest.mark.asyncio
    async def test_vlan_drift_correction(self, tmp_path):
        mgr = _make_manager(tmp_path)
        _add_port(mgr, "r1", "eth1", "lab1", vlan=100, port_name="vh-r1-e1")

        mgr.get_ovs_bridge_state = AsyncMock(return_value={
            "bridge_exists": True,
            "bridge_name": "test-ovs",
            "ports": [
                {"port_name": "vh-r1-e1", "vlan_tag": 999},
            ],
            "tracked_ports": ["r1:eth1"],
            "orphaned_ports": [],
            "missing_ports": [],
        })

        stats = await mgr.reconcile_with_ovs()
        assert stats["vlans_updated"] == 1
        assert mgr._ports["r1:eth1"].vlan_tag == 999


# ===========================================================================
# Simple accessors
# ===========================================================================
class TestSimpleAccessors:
    def test_get_ports_for_container(self, tmp_path):
        mgr = _make_manager(tmp_path)
        _add_port(mgr, "r1", "eth1", "lab1", vlan=100)
        _add_port(mgr, "r1", "eth2", "lab1", vlan=101)
        _add_port(mgr, "r2", "eth1", "lab1", vlan=102)

        ports = mgr.get_ports_for_container("r1")
        assert len(ports) == 2

    def test_get_link_by_endpoints(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._links["lab1:link-1"] = OVSLink(
            link_id="link-1", lab_id="lab1", port_a="r1:eth1", port_b="r2:eth1", vlan_tag=100,
        )
        link = mgr.get_link_by_endpoints("r1", "eth1", "r2", "eth1")
        assert link is not None
        assert link.link_id == "link-1"

        link_rev = mgr.get_link_by_endpoints("r2", "eth1", "r1", "eth1")
        assert link_rev is not None

        assert mgr.get_link_by_endpoints("r1", "eth1", "r3", "eth1") is None

    def test_get_port(self, tmp_path):
        mgr = _make_manager(tmp_path)
        _add_port(mgr, "r1", "eth1", "lab1", vlan=100)
        assert mgr.get_port("r1", "eth1") is not None
        assert mgr.get_port("missing", "eth1") is None


# ===========================================================================
# initialize
# ===========================================================================
class TestInitialize:
    @pytest.mark.asyncio
    async def test_creates_bridge_when_missing(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._initialized = False

        call_log = []

        async def fake_ovs(*args):
            call_log.append(args)
            if args == ("--version",):
                return 0, "2.17.0", ""
            elif args[0] == "br-exists":
                return 1, "", ""
            elif args[0] == "add-br":
                return 0, "", ""
            elif args[0] == "set-fail-mode":
                return 0, "", ""
            return 0, "", ""

        mgr._ovs_vsctl = fake_ovs

        await mgr.initialize()
        assert mgr._initialized is True
        assert any("add-br" in str(c) for c in call_log)

    @pytest.mark.asyncio
    async def test_idempotent(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._ovs_vsctl = AsyncMock()
        await mgr.initialize()
        mgr._ovs_vsctl.assert_not_called()

    @pytest.mark.asyncio
    async def test_ovs_not_available(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._initialized = False
        mgr._ovs_vsctl = AsyncMock(return_value=(1, "", "not found"))

        with pytest.raises(RuntimeError, match="OVS not available"):
            await mgr.initialize()

    @pytest.mark.asyncio
    async def test_existing_bridge_discovers_state(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._initialized = False
        mgr._discover_existing_state = AsyncMock()

        async def fake_ovs(*args):
            if args == ("--version",):
                return 0, "2.17.0", ""
            elif args[0] == "br-exists":
                return 0, "", ""
            return 0, "", ""

        mgr._ovs_vsctl = fake_ovs

        await mgr.initialize()
        assert mgr._initialized is True
        mgr._discover_existing_state.assert_called_once()


# ===========================================================================
# list_external_connections (not initialized)
# ===========================================================================
class TestListExternalConnections:
    @pytest.mark.asyncio
    async def test_not_initialized(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._initialized = False
        result = await mgr.list_external_connections()
        assert result == []
