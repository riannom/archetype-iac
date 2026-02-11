from __future__ import annotations

import pytest

from agent.network import vlan as vlan_mod


def test_vlan_pool_allocation_and_release():
    pool = vlan_mod.VlanPool(start=100, end=105)

    v1 = pool.allocate()
    v2 = pool.allocate()

    assert v1 == 100
    assert v2 == 101

    pool.release(v1)
    v3 = pool.allocate()
    assert v3 == 100


def test_vlan_pool_exhaustion():
    pool = vlan_mod.VlanPool(start=1, end=2)
    assert pool.allocate() == 1
    assert pool.allocate() == 2

    with pytest.raises(RuntimeError):
        pool.allocate()


