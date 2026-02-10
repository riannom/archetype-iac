from __future__ import annotations

import asyncio

import pytest

from agent.network.overlay import VniAllocator


class _FakeProc:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self._stdout = stdout
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout.encode(), b""


@pytest.mark.asyncio
async def test_recover_from_system_handles_hashed_vxlan_ports(monkeypatch, tmp_path) -> None:
    async def _fake_create_subprocess_exec(*args, **kwargs):
        # ovs-vsctl list-ports -> include hash-named vxlan port
        if args[:2] == ("ovs-vsctl", "list-ports"):
            return _FakeProc(stdout="vxlan-abcdef12\n")
        # ip -d link show vxlan-abcdef12 -> includes VNI
        if args[:4] == ("ip", "-d", "link", "show"):
            return _FakeProc(stdout="vxlan id 4242 remote 10.0.0.2 local 10.0.0.1")
        # ip -j link show type vxlan -> no legacy numeric ports
        if args[:5] == ("ip", "-j", "link", "show", "type"):
            return _FakeProc(stdout="[]")
        return _FakeProc(stdout="", returncode=1)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    allocator = VniAllocator(base=1000, max_vni=5000, persistence_path=tmp_path / "vni.json")
    recovered = await allocator.recover_from_system()

    assert recovered == 1
    assert any(vni == 4242 for vni in allocator._allocated.values())
