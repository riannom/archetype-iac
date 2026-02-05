from __future__ import annotations

import json
from pathlib import Path

from agent.network.overlay import VniAllocator


def test_vni_allocator_allocates_and_persists(tmp_path: Path) -> None:
    persistence = tmp_path / "vni.json"
    allocator = VniAllocator(base=10000, max_vni=10001, persistence_path=persistence)

    vni_a = allocator.allocate("lab1", "link1")
    vni_b = allocator.allocate("lab1", "link2")

    assert vni_a != vni_b
    assert persistence.exists()

    new_allocator = VniAllocator(base=10000, max_vni=10001, persistence_path=persistence)
    assert new_allocator.get_vni("lab1", "link1") == vni_a
    assert new_allocator.get_vni("lab1", "link2") == vni_b


def test_vni_allocator_release_lab(tmp_path: Path) -> None:
    persistence = tmp_path / "vni.json"
    allocator = VniAllocator(base=20000, max_vni=20005, persistence_path=persistence)

    allocator.allocate("lab-x", "a")
    allocator.allocate("lab-x", "b")
    allocator.allocate("lab-y", "a")

    released = allocator.release_lab("lab-x")
    assert released == 2
    assert allocator.get_vni("lab-x", "a") is None
    assert allocator.get_vni("lab-y", "a") is not None
