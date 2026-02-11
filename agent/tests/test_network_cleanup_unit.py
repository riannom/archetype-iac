from __future__ import annotations

import asyncio

import pytest

from agent.network.cleanup import NetworkCleanupManager, get_cleanup_manager


@pytest.mark.asyncio
async def test_cleanup_veths_dry_run(monkeypatch):
    mgr = NetworkCleanupManager()

    async def fake_veths():
        return [{"name": "archdeadbe", "ifindex": 1, "link_index": 2, "master": None}]

    async def fake_pids():
        return {123}

    async def fake_ifindexes(_pids):
        return {2}

    async def fake_is_orphaned(_veth, _ifindexes):
        return True

    monkeypatch.setattr(mgr, "_get_veth_interfaces", fake_veths)
    monkeypatch.setattr(mgr, "_get_running_container_pids", fake_pids)
    monkeypatch.setattr(mgr, "_get_container_ifindexes", fake_ifindexes)
    monkeypatch.setattr(mgr, "_is_veth_orphaned", fake_is_orphaned)
    async def fake_run_cmd(_cmd):
        return 0, "", ""

    monkeypatch.setattr(mgr, "_run_cmd", fake_run_cmd)

    stats = await mgr.cleanup_orphaned_veths(dry_run=True)
    assert stats.veths_found == 1
    assert stats.veths_orphaned == 1
    assert stats.veths_deleted == 0


@pytest.mark.asyncio
async def test_cleanup_veths_skips_when_no_ifindexes(monkeypatch):
    mgr = NetworkCleanupManager()

    async def fake_veths():
        return [{"name": "archdeadbe", "ifindex": 1, "link_index": 2, "master": None}]

    async def fake_pids():
        return {123}

    async def fake_ifindexes(_pids):
        return set()

    monkeypatch.setattr(mgr, "_get_veth_interfaces", fake_veths)
    monkeypatch.setattr(mgr, "_get_running_container_pids", fake_pids)
    monkeypatch.setattr(mgr, "_get_container_ifindexes", fake_ifindexes)

    stats = await mgr.cleanup_orphaned_veths(dry_run=False)
    assert stats.veths_deleted == 0
    assert stats.veths_orphaned == 0


@pytest.mark.asyncio
async def test_cleanup_bridges_dry_run(monkeypatch):
    mgr = NetworkCleanupManager()

    async def fake_run_cmd(cmd):
        if cmd[:3] == ["ip", "-j", "link"]:
            return 0, "[{\"ifname\": \"abr-1\"}]", ""
        if cmd[:4] == ["ip", "link", "show", "master"]:
            return 0, "", ""
        return 0, "", ""

    monkeypatch.setattr(mgr, "_run_cmd", fake_run_cmd)
    deleted = await mgr.cleanup_orphaned_bridges(dry_run=True)
    assert deleted == 0


@pytest.mark.asyncio
async def test_cleanup_vxlans_dry_run(monkeypatch):
    mgr = NetworkCleanupManager()

    async def fake_run_cmd(cmd):
        if cmd[:3] == ["ip", "-j", "link"]:
            return 0, "[{\"ifname\": \"vxlan10\"}]", ""
        return 0, "", ""

    monkeypatch.setattr(mgr, "_run_cmd", fake_run_cmd)
    deleted = await mgr.cleanup_orphaned_vxlans(dry_run=True)
    assert deleted == 0


@pytest.mark.asyncio
async def test_run_full_cleanup_dry_run(monkeypatch):
    mgr = NetworkCleanupManager()
    from agent.network.cleanup import CleanupStats

    async def fake_veths(dry_run=False):
        return CleanupStats()

    async def fake_bridges(dry_run=False):
        return 0

    async def fake_vxlans(dry_run=False):
        return 0

    monkeypatch.setattr(mgr, "cleanup_orphaned_veths", fake_veths)
    monkeypatch.setattr(mgr, "cleanup_orphaned_bridges", fake_bridges)
    monkeypatch.setattr(mgr, "cleanup_orphaned_vxlans", fake_vxlans)

    stats = await mgr.run_full_cleanup(dry_run=True, include_ovs=False)
    assert stats.veths_deleted == 0


@pytest.mark.asyncio
async def test_periodic_cleanup_start_stop(monkeypatch):
    mgr = NetworkCleanupManager()

    async def fake_loop(_interval):
        return None

    monkeypatch.setattr(mgr, "_periodic_cleanup_loop", fake_loop)
    await mgr.start_periodic_cleanup(interval_seconds=1)
    assert mgr._running is True

    await mgr.stop_periodic_cleanup()
    assert mgr._running is False


def test_get_cleanup_manager_singleton():
    mgr1 = get_cleanup_manager()
    mgr2 = get_cleanup_manager()
    assert mgr1 is mgr2
