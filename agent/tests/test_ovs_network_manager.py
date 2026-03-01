"""Tests for OVSNetworkManager and VlanAllocator in agent/network/ovs.py.

Covers VlanAllocator allocation, release, persistence, and range handling,
plus OVSNetworkManager provision_interface, hot_connect, hot_disconnect,
and cleanup_lab methods.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.network.ovs import OVSNetworkManager, VlanAllocator


# ---------------------------------------------------------------------------
# VlanAllocator
# ---------------------------------------------------------------------------


class TestVlanAllocator:
    """VlanAllocator unit tests."""

    def test_unique_vlans(self, tmp_path: Path) -> None:
        """Each allocation must return a distinct VLAN tag."""
        alloc = VlanAllocator(start=100, end=110, persistence_path=tmp_path / "v.json")
        vlans = {alloc.allocate(f"key-{i}") for i in range(5)}
        assert len(vlans) == 5

    def test_reuse_existing_key(self, tmp_path: Path) -> None:
        """Allocating the same key again returns the same VLAN."""
        alloc = VlanAllocator(start=100, end=110, persistence_path=tmp_path / "v.json")
        first = alloc.allocate("r1:eth1")
        second = alloc.allocate("r1:eth1")
        assert first == second

    def test_wrap_around(self, tmp_path: Path) -> None:
        """When end of range is hit, allocator wraps to start."""
        alloc = VlanAllocator(start=100, end=102, persistence_path=tmp_path / "v.json")
        v1 = alloc.allocate("a:e1")
        v2 = alloc.allocate("b:e1")
        v3 = alloc.allocate("c:e1")
        assert {v1, v2, v3} == {100, 101, 102}

    def test_exhaustion_raises(self, tmp_path: Path) -> None:
        """RuntimeError when all VLANs are exhausted."""
        alloc = VlanAllocator(start=100, end=101, persistence_path=tmp_path / "v.json")
        alloc.allocate("a:e1")
        alloc.allocate("b:e1")
        with pytest.raises(RuntimeError, match="No VLANs available"):
            alloc.allocate("c:e1")

    def test_release(self, tmp_path: Path) -> None:
        """Released VLAN is no longer tracked."""
        alloc = VlanAllocator(start=100, end=110, persistence_path=tmp_path / "v.json")
        vlan = alloc.allocate("r1:eth1")
        released = alloc.release("r1:eth1")
        assert released == vlan
        assert alloc.get_vlan("r1:eth1") is None

    def test_release_nonexistent(self, tmp_path: Path) -> None:
        """Releasing a key that was never allocated returns None."""
        alloc = VlanAllocator(start=100, end=110, persistence_path=tmp_path / "v.json")
        assert alloc.release("ghost:eth1") is None

    def test_release_lab_by_prefix(self, tmp_path: Path) -> None:
        """release_lab removes all keys matching the archetype-{lab_id} prefix."""
        alloc = VlanAllocator(start=100, end=120, persistence_path=tmp_path / "v.json")
        alloc.allocate("archetype-lab-abc-r1:eth1")
        alloc.allocate("archetype-lab-abc-r2:eth1")
        alloc.allocate("archetype-lab-xyz-r1:eth1")

        released = alloc.release_lab("lab-abc")
        assert released == 2
        assert alloc.get_vlan("archetype-lab-abc-r1:eth1") is None
        assert alloc.get_vlan("archetype-lab-xyz-r1:eth1") is not None

    def test_persist_and_reload(self, tmp_path: Path) -> None:
        """Allocations survive a new allocator instance."""
        path = tmp_path / "v.json"
        a1 = VlanAllocator(start=100, end=110, persistence_path=path)
        v = a1.allocate("r1:eth1")

        a2 = VlanAllocator(start=100, end=110, persistence_path=path)
        assert a2.get_vlan("r1:eth1") == v

    def test_ignore_out_of_range_on_load(self, tmp_path: Path) -> None:
        """VLANs outside the configured range are discarded on load."""
        path = tmp_path / "v.json"
        data = {
            "allocations": {"good:e1": 105, "bad:e1": 9999},
            "next_vlan": 106,
        }
        path.write_text(json.dumps(data))

        alloc = VlanAllocator(start=100, end=110, persistence_path=path)
        assert alloc.get_vlan("good:e1") == 105
        assert alloc.get_vlan("bad:e1") is None


# ---------------------------------------------------------------------------
# OVSNetworkManager helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Reset the OVSNetworkManager singleton between tests."""
    OVSNetworkManager._instance = None
    yield
    OVSNetworkManager._instance = None


def _make_manager(tmp_path: Path) -> OVSNetworkManager:
    """Create an OVSNetworkManager with mocked async primitives."""
    mgr = OVSNetworkManager()
    mgr._initialized = True
    mgr._bridge_name = "test-ovs"
    mgr._ports = {}
    mgr._links = {}
    mgr._vlan_allocator = VlanAllocator(
        start=100, end=4000, persistence_path=tmp_path / "v.json"
    )
    mgr._docker = MagicMock()

    # Default mocks: commands succeed
    mgr._ovs_vsctl = AsyncMock(return_value=(0, "", ""))
    mgr._run_cmd = AsyncMock(return_value=(0, "", ""))
    mgr._ip_link_exists = AsyncMock(return_value=False)
    mgr._get_container_pid = AsyncMock(return_value=42)
    return mgr


# ---------------------------------------------------------------------------
# TestProvisionInterface
# ---------------------------------------------------------------------------


class TestProvisionInterface:
    """Tests for OVSNetworkManager.provision_interface."""

    @pytest.mark.asyncio
    async def test_allocates_vlan_and_tracks_port(self, tmp_path: Path) -> None:
        """Provisioning creates a veth pair, attaches to OVS, and tracks the port."""
        mgr = _make_manager(tmp_path)
        vlan = await mgr.provision_interface("archetype-lab1-r1", "eth1", "lab1")
        assert isinstance(vlan, int)
        assert vlan >= 100
        port = mgr.get_port("archetype-lab1-r1", "eth1")
        assert port is not None
        assert port.vlan_tag == vlan

    @pytest.mark.asyncio
    async def test_already_provisioned_returns_existing(self, tmp_path: Path) -> None:
        """Calling provision_interface again returns the existing VLAN."""
        mgr = _make_manager(tmp_path)
        v1 = await mgr.provision_interface("archetype-lab1-r1", "eth1", "lab1")
        v2 = await mgr.provision_interface("archetype-lab1-r1", "eth1", "lab1")
        assert v1 == v2

    @pytest.mark.asyncio
    async def test_no_container_raises(self, tmp_path: Path) -> None:
        """RuntimeError when the container is not running."""
        mgr = _make_manager(tmp_path)
        mgr._get_container_pid = AsyncMock(return_value=None)

        with pytest.raises(RuntimeError, match="not running"):
            await mgr.provision_interface("archetype-lab1-r1", "eth1", "lab1")

    @pytest.mark.asyncio
    async def test_ovs_add_port_fails_cleans_up(self, tmp_path: Path) -> None:
        """OVS add-port failure triggers VLAN release and interface cleanup."""
        mgr = _make_manager(tmp_path)

        call_count = 0

        async def _selective_fail(*args):
            nonlocal call_count
            if "add-port" in args:
                return (1, "", "add-port failed")
            return (0, "", "")

        mgr._ovs_vsctl = AsyncMock(side_effect=_selective_fail)

        with pytest.raises(RuntimeError, match="Failed to provision interface"):
            await mgr.provision_interface("archetype-lab1-r1", "eth1", "lab1")

        # VLAN allocation should have been cleaned up
        assert mgr._vlan_allocator.get_vlan("archetype-lab1-r1:eth1") is None


# ---------------------------------------------------------------------------
# TestHotConnect
# ---------------------------------------------------------------------------


class TestHotConnect:
    """Tests for OVSNetworkManager.hot_connect."""

    @pytest.mark.asyncio
    async def test_assigns_shared_vlan_tag(self, tmp_path: Path) -> None:
        """hot_connect gives both ports the same VLAN tag."""
        mgr = _make_manager(tmp_path)
        await mgr.provision_interface("archetype-lab1-r1", "eth1", "lab1")
        await mgr.provision_interface("archetype-lab1-r2", "eth1", "lab1")

        shared = await mgr.hot_connect(
            "archetype-lab1-r1", "eth1",
            "archetype-lab1-r2", "eth1",
            lab_id="lab1",
        )
        port_a = mgr.get_port("archetype-lab1-r1", "eth1")
        port_b = mgr.get_port("archetype-lab1-r2", "eth1")
        assert port_a.vlan_tag == port_b.vlan_tag == shared

    @pytest.mark.asyncio
    async def test_missing_port_raises(self, tmp_path: Path) -> None:
        """hot_connect raises when a port is not provisioned."""
        mgr = _make_manager(tmp_path)
        await mgr.provision_interface("archetype-lab1-r1", "eth1", "lab1")

        with pytest.raises(RuntimeError, match="Port not provisioned"):
            await mgr.hot_connect(
                "archetype-lab1-r1", "eth1",
                "archetype-lab1-r2", "eth1",
            )


# ---------------------------------------------------------------------------
# TestHotDisconnect
# ---------------------------------------------------------------------------


class TestHotDisconnect:
    """Tests for OVSNetworkManager.hot_disconnect."""

    @pytest.mark.asyncio
    async def test_assigns_separate_vlans(self, tmp_path: Path) -> None:
        """After disconnect, both ports have different VLANs."""
        mgr = _make_manager(tmp_path)
        await mgr.provision_interface("archetype-lab1-r1", "eth1", "lab1")
        await mgr.provision_interface("archetype-lab1-r2", "eth1", "lab1")
        await mgr.hot_connect(
            "archetype-lab1-r1", "eth1",
            "archetype-lab1-r2", "eth1",
            lab_id="lab1",
        )

        vlan_a, vlan_b = await mgr.hot_disconnect(
            "archetype-lab1-r1", "eth1",
            "archetype-lab1-r2", "eth1",
        )
        assert vlan_a != vlan_b

    @pytest.mark.asyncio
    async def test_removes_link_record(self, tmp_path: Path) -> None:
        """Disconnect removes the link from internal tracking."""
        mgr = _make_manager(tmp_path)
        await mgr.provision_interface("archetype-lab1-r1", "eth1", "lab1")
        await mgr.provision_interface("archetype-lab1-r2", "eth1", "lab1")
        await mgr.hot_connect(
            "archetype-lab1-r1", "eth1",
            "archetype-lab1-r2", "eth1",
            lab_id="lab1",
        )
        assert len(mgr.get_links_for_lab("lab1")) == 1

        await mgr.hot_disconnect(
            "archetype-lab1-r1", "eth1",
            "archetype-lab1-r2", "eth1",
        )
        assert len(mgr.get_links_for_lab("lab1")) == 0


# ---------------------------------------------------------------------------
# TestCleanupLab
# ---------------------------------------------------------------------------


class TestCleanupLab:
    """Tests for OVSNetworkManager.cleanup_lab."""

    @pytest.mark.asyncio
    async def test_removes_all_ports_and_links(self, tmp_path: Path) -> None:
        """cleanup_lab removes every port and link for the given lab."""
        mgr = _make_manager(tmp_path)
        await mgr.provision_interface("archetype-lab1-r1", "eth1", "lab1")
        await mgr.provision_interface("archetype-lab1-r2", "eth1", "lab1")
        await mgr.hot_connect(
            "archetype-lab1-r1", "eth1",
            "archetype-lab1-r2", "eth1",
            lab_id="lab1",
        )

        result = await mgr.cleanup_lab("lab1")
        assert result["ports_deleted"] == 2
        assert result["links_deleted"] == 1
        assert mgr.get_ports_for_lab("lab1") == []
        assert mgr.get_links_for_lab("lab1") == []

    @pytest.mark.asyncio
    async def test_continues_on_port_error(self, tmp_path: Path) -> None:
        """If one port fails to delete, the rest are still attempted."""
        mgr = _make_manager(tmp_path)
        await mgr.provision_interface("archetype-lab1-r1", "eth1", "lab1")
        await mgr.provision_interface("archetype-lab1-r2", "eth1", "lab1")

        # Make OVS del-port fail for one call, succeed for the other
        call_idx = 0

        async def _fail_first(*args):
            nonlocal call_idx
            call_idx += 1
            if "--if-exists" in args and call_idx == 1:
                raise RuntimeError("simulated OVS failure")
            return (0, "", "")

        mgr._ovs_vsctl = AsyncMock(side_effect=_fail_first)

        result = await mgr.cleanup_lab("lab1")
        assert len(result["errors"]) >= 1
        # At least one port should have been cleaned
        assert result["ports_deleted"] >= 1

    @pytest.mark.asyncio
    async def test_lab2_unaffected(self, tmp_path: Path) -> None:
        """Cleaning lab1 does not touch lab2 resources."""
        mgr = _make_manager(tmp_path)
        await mgr.provision_interface("archetype-lab1-r1", "eth1", "lab1")
        await mgr.provision_interface("archetype-lab2-r1", "eth1", "lab2")

        await mgr.cleanup_lab("lab1")

        assert mgr.get_ports_for_lab("lab1") == []
        assert len(mgr.get_ports_for_lab("lab2")) == 1
