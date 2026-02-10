from __future__ import annotations

from pathlib import Path

from agent.network.ovs import VlanAllocator


def test_vlan_allocator_persists_allocations(tmp_path: Path) -> None:
    persistence = tmp_path / "alloc.json"
    allocator = VlanAllocator(start=100, end=101, persistence_path=persistence)

    vlan_a = allocator.allocate("container-a:eth1")
    vlan_b = allocator.allocate("container-b:eth1")

    assert vlan_a != vlan_b
    assert persistence.exists()

    new_allocator = VlanAllocator(start=100, end=101, persistence_path=persistence)
    assert new_allocator.get_vlan("container-a:eth1") == vlan_a
    assert new_allocator.get_vlan("container-b:eth1") == vlan_b


def test_vlan_allocator_release(tmp_path: Path) -> None:
    persistence = tmp_path / "alloc.json"
    allocator = VlanAllocator(start=200, end=201, persistence_path=persistence)

    vlan = allocator.allocate("container-a:eth1")
    released = allocator.release("container-a:eth1")

    assert released == vlan
    assert allocator.get_vlan("container-a:eth1") is None


def test_vlan_allocator_release_lab(tmp_path: Path) -> None:
    persistence = tmp_path / "alloc.json"
    allocator = VlanAllocator(start=300, end=302, persistence_path=persistence)

    allocator.allocate("archetype-lab-1234-node1:eth1")
    allocator.allocate("archetype-lab-1234-node2:eth1")
    allocator.allocate("archetype-other-node:eth1")

    released = allocator.release_lab("lab-1234")
    assert released == 2
