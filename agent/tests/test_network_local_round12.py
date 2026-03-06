"""Comprehensive tests for agent/network/local.py — Round 12.

Covers: create_link, delete_link, attach_to_bridge, cleanup_lab,
create_link_ovs, delete_link_ovs, get_links_for_lab, get_status,
provision_dummy_interfaces, _get_container_pid, error handling.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import agent.network.local as local_mod
from agent.network.local import LocalLink, LocalNetworkManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset the singleton between tests so state doesn't leak."""
    LocalNetworkManager._instance = None
    local_mod._local_manager = None
    yield
    LocalNetworkManager._instance = None
    local_mod._local_manager = None


@pytest.fixture
def mgr():
    """Create a fresh LocalNetworkManager."""
    return LocalNetworkManager()


def _patch_mgr(monkeypatch, mgr, *, pid_map=None, run_cmd_fn=None, link_exists_fn=None):
    """Helper to patch common methods on a manager instance."""
    if pid_map is not None:
        async def fake_get_pid(name):
            return pid_map.get(name)
        monkeypatch.setattr(mgr, "_get_container_pid", fake_get_pid)

    if run_cmd_fn is not None:
        monkeypatch.setattr(mgr, "_run_cmd", run_cmd_fn)
    else:
        async def ok_cmd(cmd):
            return 0, "", ""
        monkeypatch.setattr(mgr, "_run_cmd", ok_cmd)

    if link_exists_fn is not None:
        monkeypatch.setattr(mgr, "_ip_link_exists", link_exists_fn)
    else:
        async def no_exist(_name):
            return False
        monkeypatch.setattr(mgr, "_ip_link_exists", no_exist)


# ---------------------------------------------------------------------------
# 1. create_link — duplicate key returns existing link
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_link_duplicate_returns_existing(monkeypatch, mgr):
    """When a link with the same key already exists, return it immediately."""
    _patch_mgr(monkeypatch, mgr, pid_map={"a": 100, "b": 200})

    link1 = await mgr.create_link("lab1", "lk1", "a", "b", "eth1", "eth2")
    link2 = await mgr.create_link("lab1", "lk1", "a", "b", "eth1", "eth2")
    assert link1 is link2


# ---------------------------------------------------------------------------
# 2. create_link — container A not running raises RuntimeError
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_link_container_a_not_running(monkeypatch, mgr):
    _patch_mgr(monkeypatch, mgr, pid_map={"b": 200})  # "a" missing

    with pytest.raises(RuntimeError, match="not running or not found"):
        await mgr.create_link("lab1", "lk1", "a", "b", "eth1", "eth2")


# ---------------------------------------------------------------------------
# 3. create_link — container B not running raises RuntimeError
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_link_container_b_not_running(monkeypatch, mgr):
    _patch_mgr(monkeypatch, mgr, pid_map={"a": 100})  # "b" missing

    with pytest.raises(RuntimeError, match="not running or not found"):
        await mgr.create_link("lab1", "lk1", "a", "b", "eth1", "eth2")


# ---------------------------------------------------------------------------
# 4. create_link — veth pair creation failure raises RuntimeError
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_link_veth_creation_fails(monkeypatch, mgr):
    async def failing_cmd(cmd):
        if "add" in cmd and "veth" in cmd:
            return 1, "", "RTNETLINK error"
        return 0, "", ""

    _patch_mgr(monkeypatch, mgr, pid_map={"a": 100, "b": 200}, run_cmd_fn=failing_cmd)

    with pytest.raises(RuntimeError, match="Failed to create link"):
        await mgr.create_link("lab1", "lk1", "a", "b", "eth1", "eth2")


# ---------------------------------------------------------------------------
# 5. create_link — move veth_a to namespace fails, cleans up veth_b
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_link_move_a_fails_cleans_up(monkeypatch, mgr):
    calls = []

    async def selective_cmd(cmd):
        calls.append(list(cmd))
        # Fail on "ip link set <veth> netns <pid_a>" (first netns move)
        if "netns" in cmd and cmd[-1] == "100":
            return 1, "", "namespace error"
        return 0, "", ""

    _patch_mgr(monkeypatch, mgr, pid_map={"a": 100, "b": 200}, run_cmd_fn=selective_cmd)

    with pytest.raises(RuntimeError, match="Failed to create link"):
        await mgr.create_link("lab1", "lk1", "a", "b", "eth1", "eth2")

    # Should have attempted to delete veth_b (cleanup after move failure)
    delete_calls = [c for c in calls if "delete" in c]
    assert len(delete_calls) >= 1


# ---------------------------------------------------------------------------
# 6. create_link — move veth_b to namespace fails
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_link_move_b_fails(monkeypatch, mgr):
    move_count = {"n": 0}

    async def selective_cmd(cmd):
        if "netns" in cmd:
            move_count["n"] += 1
            if move_count["n"] == 2:  # second netns move (veth_b)
                return 1, "", "namespace error"
        return 0, "", ""

    _patch_mgr(monkeypatch, mgr, pid_map={"a": 100, "b": 200}, run_cmd_fn=selective_cmd)

    with pytest.raises(RuntimeError, match="Failed to create link"):
        await mgr.create_link("lab1", "lk1", "a", "b", "eth1", "eth2")


# ---------------------------------------------------------------------------
# 7. create_link — existing veth names are deleted before creation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_link_cleans_existing_veths(monkeypatch, mgr):
    deleted = []

    async def always_exists(_name):
        return True

    async def track_cmd(cmd):
        if "delete" in cmd:
            deleted.append(cmd[-1])
        return 0, "", ""

    _patch_mgr(
        monkeypatch, mgr,
        pid_map={"a": 100, "b": 200},
        run_cmd_fn=track_cmd,
        link_exists_fn=always_exists,
    )

    await mgr.create_link("lab1", "lk1", "a", "b", "eth1", "eth2")
    # Two pre-existing veths should have been deleted
    assert len(deleted) >= 2


# ---------------------------------------------------------------------------
# 8. create_link — IP addresses are configured when provided
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_link_with_ip_addresses(monkeypatch, mgr):
    addr_cmds = []

    async def track_cmd(cmd):
        if "addr" in cmd and "add" in cmd:
            addr_cmds.append(list(cmd))
        return 0, "", ""

    _patch_mgr(monkeypatch, mgr, pid_map={"a": 100, "b": 200}, run_cmd_fn=track_cmd)

    link = await mgr.create_link(
        "lab1", "lk1", "a", "b", "eth1", "eth2",
        ip_a="10.0.0.1/24", ip_b="10.0.0.2/24",
    )
    assert link is not None
    # Two IP address add commands should have been issued
    assert len(addr_cmds) == 2
    assert "10.0.0.1/24" in addr_cmds[0]
    assert "10.0.0.2/24" in addr_cmds[1]


# ---------------------------------------------------------------------------
# 9. delete_link — falls back to container B when A is stopped
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_link_fallback_to_container_b(monkeypatch, mgr):
    run_calls = []

    async def pid_fn(name):
        if name == "b":
            return 456
        return None  # container a stopped

    async def track_cmd(cmd):
        run_calls.append(list(cmd))
        return 0, "", ""

    monkeypatch.setattr(mgr, "_get_container_pid", pid_fn)
    monkeypatch.setattr(mgr, "_run_cmd", track_cmd)

    link = LocalLink(
        lab_id="lab1", link_id="lk1",
        container_a="a", container_b="b",
        iface_a="eth1", iface_b="eth2",
        veth_host_a="va", veth_host_b="vb",
    )
    mgr._links[link.key] = link

    result = await mgr.delete_link(link)
    assert result is True
    assert link.key not in mgr._links
    # Should have used container B's namespace to delete
    nsenter_cmds = [c for c in run_calls if "nsenter" in c]
    assert len(nsenter_cmds) == 1
    assert "456" in nsenter_cmds[0]


# ---------------------------------------------------------------------------
# 10. delete_link — exception returns False
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_link_exception_returns_false(monkeypatch, mgr):
    async def exploding_pid(_name):
        raise RuntimeError("docker exploded")

    monkeypatch.setattr(mgr, "_get_container_pid", exploding_pid)

    link = LocalLink(
        lab_id="lab1", link_id="lk1",
        container_a="a", container_b="b",
        iface_a="eth1", iface_b="eth2",
        veth_host_a="va", veth_host_b="vb",
    )
    result = await mgr.delete_link(link)
    assert result is False


# ---------------------------------------------------------------------------
# 11. attach_to_bridge — happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_attach_to_bridge_success(monkeypatch, mgr):
    cmds = []

    async def track_cmd(cmd):
        cmds.append(list(cmd))
        return 0, "", ""

    _patch_mgr(monkeypatch, mgr, pid_map={"c1": 500}, run_cmd_fn=track_cmd)

    ok = await mgr.attach_to_bridge("c1", "eth1", "br0", ip_address="192.168.1.1/24")
    assert ok is True
    # Should contain: veth create, master set, link up, netns move, rename, up, addr add
    flat = " ".join(str(c) for c in cmds)
    assert "master" in flat
    assert "192.168.1.1/24" in flat


# ---------------------------------------------------------------------------
# 12. attach_to_bridge — container not running returns False
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_attach_to_bridge_container_not_running(monkeypatch, mgr):
    _patch_mgr(monkeypatch, mgr, pid_map={})  # no containers

    ok = await mgr.attach_to_bridge("missing", "eth1", "br0")
    assert ok is False


# ---------------------------------------------------------------------------
# 13. attach_to_bridge — bridge attach failure cleans up
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_attach_to_bridge_bridge_failure(monkeypatch, mgr):
    async def failing_cmd(cmd):
        if "master" in cmd:
            return 1, "", "bridge error"
        return 0, "", ""

    _patch_mgr(monkeypatch, mgr, pid_map={"c1": 500}, run_cmd_fn=failing_cmd)

    ok = await mgr.attach_to_bridge("c1", "eth1", "br0")
    assert ok is False


# ---------------------------------------------------------------------------
# 14. cleanup_lab — deletes tracked links and calls orphan cleanup
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cleanup_lab(monkeypatch, mgr):
    async def ok_cmd(cmd):
        return 0, "", ""

    _patch_mgr(monkeypatch, mgr, pid_map={"a": 100}, run_cmd_fn=ok_cmd)

    # Pre-populate two links, one in target lab, one in another lab
    link1 = LocalLink("lab1", "lk1", "a", "b", "eth1", "eth2", "va", "vb")
    link2 = LocalLink("lab2", "lk2", "c", "d", "eth1", "eth2", "vc", "vd")
    mgr._links[link1.key] = link1
    mgr._links[link2.key] = link2

    # Mock orphan cleanup to avoid importing real NetworkCleanupManager
    mock_cleanup_cls = MagicMock()
    mock_cleanup_cls.return_value.cleanup_orphaned_veths = AsyncMock()
    monkeypatch.setattr(
        "agent.network.cleanup.NetworkCleanupManager", mock_cleanup_cls
    )

    result = await mgr.cleanup_lab("lab1")
    assert result["links_deleted"] == 1
    # lab2 link should still exist
    assert link2.key in mgr._links
    assert link1.key not in mgr._links


# ---------------------------------------------------------------------------
# 15. cleanup_lab — orphan cleanup failure is captured in errors
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cleanup_lab_orphan_error_captured(monkeypatch, mgr):
    async def ok_cmd(cmd):
        return 0, "", ""

    _patch_mgr(monkeypatch, mgr, pid_map={}, run_cmd_fn=ok_cmd)

    monkeypatch.setattr(
        "agent.network.cleanup.NetworkCleanupManager",
        MagicMock(side_effect=RuntimeError("import boom")),
    )

    result = await mgr.cleanup_lab("lab1")
    assert any("Orphaned veth cleanup" in e for e in result["errors"])


# ---------------------------------------------------------------------------
# 16. get_links_for_lab — filters by lab_id
# ---------------------------------------------------------------------------

def test_get_links_for_lab(mgr):
    link1 = LocalLink("lab1", "lk1", "a", "b", "eth1", "eth2", "va", "vb")
    link2 = LocalLink("lab2", "lk2", "c", "d", "eth1", "eth2", "vc", "vd")
    link3 = LocalLink("lab1", "lk3", "e", "f", "eth1", "eth2", "ve", "vf")
    mgr._links[link1.key] = link1
    mgr._links[link2.key] = link2
    mgr._links[link3.key] = link3

    lab1_links = mgr.get_links_for_lab("lab1")
    assert len(lab1_links) == 2
    assert all(lnk.lab_id == "lab1" for lnk in lab1_links)


# ---------------------------------------------------------------------------
# 17. get_status — returns correct structure
# ---------------------------------------------------------------------------

def test_get_status_structure(mgr):
    link = LocalLink("lab1", "lk1", "a", "b", "eth1", "eth2", "va", "vb")
    mgr._links[link.key] = link

    status = mgr.get_status()
    assert "links" in status
    assert len(status["links"]) == 1
    entry = status["links"][0]
    assert entry["lab_id"] == "lab1"
    assert entry["link_id"] == "lk1"
    assert entry["container_a"] == "a"
    assert entry["iface_b"] == "eth2"


# ---------------------------------------------------------------------------
# 18. get_status — empty when no links
# ---------------------------------------------------------------------------

def test_get_status_empty(mgr):
    status = mgr.get_status()
    assert status == {"links": []}


# ---------------------------------------------------------------------------
# 19. create_link_ovs — delegates to OVS hot_connect
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_link_ovs(monkeypatch, mgr):
    mock_ovs = MagicMock()
    mock_ovs.hot_connect = AsyncMock(return_value=42)

    monkeypatch.setattr(
        "agent.network.ovs.get_ovs_manager", lambda: mock_ovs
    )

    vlan = await mgr.create_link_ovs("lab1", "lk1", "a", "b", "eth1", "eth2")
    assert vlan == 42
    mock_ovs.hot_connect.assert_awaited_once_with(
        container_a="a", iface_a="eth1",
        container_b="b", iface_b="eth2",
        lab_id="lab1",
    )
    # Link should be tracked
    assert "lab1:lk1" in mgr._links
    tracked = mgr._links["lab1:lk1"]
    assert tracked.veth_host_a == ""  # Not used with OVS


# ---------------------------------------------------------------------------
# 20. delete_link_ovs — delegates to OVS hot_disconnect
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_link_ovs(monkeypatch, mgr):
    mock_ovs = MagicMock()
    mock_ovs.hot_disconnect = AsyncMock(return_value=(10, 20))

    monkeypatch.setattr(
        "agent.network.ovs.get_ovs_manager", lambda: mock_ovs
    )

    # Pre-populate link
    link = LocalLink("lab1", "lk1", "a", "b", "eth1", "eth2", "", "")
    mgr._links[link.key] = link

    result = await mgr.delete_link_ovs("lab1", "lk1")
    assert result == (10, 20)
    mock_ovs.hot_disconnect.assert_awaited_once()
    assert "lab1:lk1" not in mgr._links


# ---------------------------------------------------------------------------
# 21. delete_link_ovs — missing link returns None
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_link_ovs_missing(monkeypatch, mgr):
    result = await mgr.delete_link_ovs("lab1", "nonexistent")
    assert result is None


# ---------------------------------------------------------------------------
# 22. provision_dummy_interfaces — skips existing interfaces
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_provision_dummy_skips_existing(monkeypatch, mgr):
    async def selective_cmd(cmd):
        # "ip link show ethN" — pretend all exist
        if "show" in cmd:
            return 0, "", ""
        return 0, "", ""

    _patch_mgr(monkeypatch, mgr, pid_map={"c1": 100}, run_cmd_fn=selective_cmd)

    created = await mgr.provision_dummy_interfaces("c1", "eth", 1, 3)
    assert created == 0


# ---------------------------------------------------------------------------
# 23. provision_dummy_interfaces — container not running returns 0
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_provision_dummy_container_not_running(monkeypatch, mgr):
    _patch_mgr(monkeypatch, mgr, pid_map={})

    created = await mgr.provision_dummy_interfaces("missing", "eth", 1, 3)
    assert created == 0


# ---------------------------------------------------------------------------
# 24. provision_dummy_interfaces — creation failure is graceful
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_provision_dummy_creation_failure(monkeypatch, mgr):
    async def failing_cmd(cmd):
        if "show" in cmd:
            return 1, "", ""  # not existing
        if "add" in cmd and "dummy" in cmd:
            return 1, "", "cannot create"
        return 0, "", ""

    _patch_mgr(monkeypatch, mgr, pid_map={"c1": 100}, run_cmd_fn=failing_cmd)

    created = await mgr.provision_dummy_interfaces("c1", "eth", 1, 2)
    assert created == 0


# ---------------------------------------------------------------------------
# 25. _get_container_pid — Docker NotFound returns None
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_container_pid_not_found(monkeypatch, mgr):
    from docker.errors import NotFound

    mock_docker = MagicMock()
    mock_docker.containers.get.side_effect = NotFound("gone")
    monkeypatch.setattr(mgr, "_docker", mock_docker)

    pid = await mgr._get_container_pid("missing")
    assert pid is None


# ---------------------------------------------------------------------------
# 26. _get_container_pid — container not running returns None
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_container_pid_not_running(monkeypatch, mgr):
    mock_container = MagicMock()
    mock_container.status = "exited"

    mock_docker = MagicMock()
    mock_docker.containers.get.return_value = mock_container
    monkeypatch.setattr(mgr, "_docker", mock_docker)

    pid = await mgr._get_container_pid("stopped")
    assert pid is None


# ---------------------------------------------------------------------------
# 27. _get_container_pid — running container returns PID
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_container_pid_success(monkeypatch, mgr):
    mock_container = MagicMock()
    mock_container.status = "running"
    mock_container.attrs = {"State": {"Pid": 12345}}

    mock_docker = MagicMock()
    mock_docker.containers.get.return_value = mock_container
    monkeypatch.setattr(mgr, "_docker", mock_docker)

    pid = await mgr._get_container_pid("mycontainer")
    assert pid == 12345


# ---------------------------------------------------------------------------
# 28. _get_container_pid — zero PID returns None
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_container_pid_zero(monkeypatch, mgr):
    mock_container = MagicMock()
    mock_container.status = "running"
    mock_container.attrs = {"State": {"Pid": 0}}

    mock_docker = MagicMock()
    mock_docker.containers.get.return_value = mock_container
    monkeypatch.setattr(mgr, "_docker", mock_docker)

    pid = await mgr._get_container_pid("zombie")
    assert pid is None


# ---------------------------------------------------------------------------
# 29. LocalLink.key property
# ---------------------------------------------------------------------------

def test_local_link_key():
    link = LocalLink("lab42", "r1:eth1-r2:eth1", "a", "b", "eth1", "eth2", "va", "vb")
    assert link.key == "lab42:r1:eth1-r2:eth1"


# ---------------------------------------------------------------------------
# 30. _generate_veth_name — length within 15-char Linux limit
# ---------------------------------------------------------------------------

def test_veth_name_length(mgr):
    for _ in range(20):
        name = mgr._generate_veth_name("some-long-lab-id")
        assert len(name) <= 15
        assert name.startswith("arch")
