"""Additional branch coverage for agent.helpers asynchronous helpers."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from agent import helpers
from agent.config import settings


class _Proc:
    def __init__(self, stdout: bytes, returncode: int = 0):
        self._stdout = stdout
        self.returncode = returncode

    async def communicate(self):
        return self._stdout, b""


@pytest.mark.asyncio
async def test_resolve_ovs_port_via_ifindex_branches(monkeypatch):
    async def _run_direct(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(helpers.asyncio, "to_thread", _run_direct)
    monkeypatch.setattr(helpers, "_resolve_ifindex_sync", lambda *_args: 17)

    calls = {"count": 0}

    async def _create_subprocess_exec(*_args, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return _Proc(b"vh-stale\nvh-good\n")
        if calls["count"] == 2:
            return _Proc(b"not-a-number\n")
        return _Proc(b"17\n")

    monkeypatch.setattr(helpers.asyncio, "create_subprocess_exec", _create_subprocess_exec)
    monkeypatch.setattr(helpers, "_ovs_get_port_vlan", AsyncMock(return_value=2049))

    resolved = await helpers._resolve_ovs_port_via_ifindex("arch-lab-r1", "eth1")
    assert resolved == ("vh-good", 2049)

    async def _fail_list_ports(*_args, **_kwargs):
        return _Proc(b"", returncode=1)

    monkeypatch.setattr(helpers.asyncio, "create_subprocess_exec", _fail_list_ports)
    assert await helpers._resolve_ovs_port_via_ifindex("arch-lab-r1", "eth1") is None


@pytest.mark.asyncio
async def test_ovs_get_port_vlan_and_list_used_vlans_paths(monkeypatch):
    async def _proc_nonzero(*_args, **_kwargs):
        return _Proc(b"", returncode=1)

    monkeypatch.setattr(helpers.asyncio, "create_subprocess_exec", _proc_nonzero)
    assert await helpers._ovs_get_port_vlan("vh1") is None

    async def _proc_empty(*_args, **_kwargs):
        return _Proc(b"[]\n", returncode=0)

    monkeypatch.setattr(helpers.asyncio, "create_subprocess_exec", _proc_empty)
    assert await helpers._ovs_get_port_vlan("vh1") is None

    async def _proc_bad(*_args, **_kwargs):
        return _Proc(b"abc\n", returncode=0)

    monkeypatch.setattr(helpers.asyncio, "create_subprocess_exec", _proc_bad)
    assert await helpers._ovs_get_port_vlan("vh1") is None

    async def _proc_good(*_args, **_kwargs):
        return _Proc(b"300\n", returncode=0)

    monkeypatch.setattr(helpers.asyncio, "create_subprocess_exec", _proc_good)
    assert await helpers._ovs_get_port_vlan("vh1") == 300

    async def _ports(*_args, **_kwargs):
        return _Proc(b"vh1\nvh2\n", returncode=0)

    monkeypatch.setattr(helpers.asyncio, "create_subprocess_exec", _ports)
    monkeypatch.setattr(
        helpers,
        "_ovs_get_port_vlan",
        AsyncMock(side_effect=[None, 2500]),
    )
    assert await helpers._ovs_list_used_vlans("arch-ovs") == {2500}


@pytest.mark.asyncio
async def test_resolve_ovs_port_libvirt_vlan_fallback(monkeypatch):
    libvirt_provider = SimpleNamespace(
        get_node_kind_async=AsyncMock(return_value="xrv"),
        get_vm_interface_port=AsyncMock(return_value="vnet7"),
        get_node_vlans=lambda *_args, **_kwargs: [333],
    )

    def _get_provider(name: str):
        if name == "libvirt":
            return libvirt_provider
        return None

    monkeypatch.setattr(helpers, "get_provider", _get_provider)
    monkeypatch.setattr(helpers, "_ovs_get_port_vlan", AsyncMock(return_value=None))

    result = await helpers._resolve_ovs_port("lab-1", "r1", "eth1")
    assert result is not None
    assert result.provider == "libvirt"
    assert result.port_name == "vnet7"
    assert result.vlan_tag == 333

    libvirt_provider.get_vm_interface_port = AsyncMock(side_effect=RuntimeError("lookup failed"))
    assert await helpers._resolve_ovs_port("lab-1", "r1", "eth1") is None


@pytest.mark.asyncio
async def test_fix_running_interfaces_restart_and_error_paths(monkeypatch):
    async def _run_direct(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(helpers.asyncio, "to_thread", _run_direct)
    monkeypatch.setattr(helpers.asyncio, "sleep", AsyncMock())

    exited = SimpleNamespace(
        name="arch-lab-r1",
        status="exited",
        labels={"archetype.lab_id": "lab-1"},
        attrs={"State": {"ExitCode": 255, "Error": ""}},
        start=Mock(),
    )
    running = SimpleNamespace(
        name="arch-lab-r2",
        status="running",
        labels={"archetype.lab_id": "lab-1"},
        attrs={"State": {}},
        start=Mock(),
    )
    no_lab = SimpleNamespace(
        name="arch-lab-r3",
        status="running",
        labels={},
        attrs={"State": {}},
        start=Mock(),
    )

    async def _fix_interface_names(container_name: str, _lab_id: str):
        if container_name == "arch-lab-r2":
            raise RuntimeError("fix failed")
        return None

    provider = SimpleNamespace(
        docker=SimpleNamespace(
            containers=SimpleNamespace(list=Mock(return_value=[exited, running, no_lab]))
        ),
        _fix_interface_names=AsyncMock(side_effect=_fix_interface_names),
    )
    monkeypatch.setattr(helpers, "get_provider", lambda name: provider if name == "docker" else None)

    await helpers._fix_running_interfaces()

    assert exited.start.call_count >= 1
    assert provider._fix_interface_names.await_count >= 1

    failing_provider = SimpleNamespace(
        docker=SimpleNamespace(containers=SimpleNamespace(list=Mock(side_effect=RuntimeError("list boom")))),
        _fix_interface_names=AsyncMock(),
    )
    monkeypatch.setattr(helpers, "get_provider", lambda name: failing_provider if name == "docker" else None)
    await helpers._fix_running_interfaces()


@pytest.mark.asyncio
async def test_cleanup_lingering_virsh_sessions_paths(monkeypatch):
    async def _run_direct(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(helpers.asyncio, "to_thread", _run_direct)

    registry_mod = ModuleType("agent.console_session_registry")
    registry_mod.list_active_domains = lambda: ["dom-1", "dom-2"]
    unregister = Mock(side_effect=[None, RuntimeError("stale")])
    registry_mod.unregister_session = unregister

    virsh_mod = ModuleType("agent.virsh_console_lock")

    def _kill(domain: str) -> int:
        if domain == "dom-2":
            raise RuntimeError("kill failed")
        return 1

    virsh_mod.kill_orphaned_virsh = _kill

    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(sys.modules, "agent.console_session_registry", registry_mod)
        mp.setitem(sys.modules, "agent.virsh_console_lock", virsh_mod)
        await helpers._cleanup_lingering_virsh_sessions()

    assert unregister.call_count == 2

    broken_registry_mod = ModuleType("agent.console_session_registry")
    broken_registry_mod.list_active_domains = Mock(side_effect=RuntimeError("registry down"))
    broken_registry_mod.unregister_session = Mock()

    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(sys.modules, "agent.console_session_registry", broken_registry_mod)
        mp.setitem(sys.modules, "agent.virsh_console_lock", virsh_mod)
        await helpers._cleanup_lingering_virsh_sessions()


@pytest.mark.asyncio
async def test_fix_running_interfaces_returns_when_no_docker_provider(monkeypatch):
    monkeypatch.setattr(helpers.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(helpers, "get_provider", lambda _name: None)
    await helpers._fix_running_interfaces()


@pytest.mark.asyncio
async def test_ovs_allocate_unique_vlan_respects_bridge_setting(monkeypatch):
    monkeypatch.setattr(settings, "ovs_bridge_name", "custom-ovs")
    monkeypatch.setattr(helpers, "_ovs_list_used_vlans", AsyncMock(return_value={100, 101, 102}))
    monkeypatch.setattr(helpers, "_ovs_set_port_vlan", AsyncMock(return_value=True))
    vlan = await helpers._ovs_allocate_unique_vlan("vh9")
    assert vlan == 103
