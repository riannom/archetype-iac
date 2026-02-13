"""Unit tests for LibvirtProvider console command selection.

Regression: for SSH-console devices (e.g. cat9000v-q200) we must fall back to
virsh console when SSH isn't actually usable (auth failure, device not ready).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import agent.providers.libvirt as libvirt_provider


def _run(coro):
    return asyncio.run(coro)


def _make_provider() -> libvirt_provider.LibvirtProvider:
    # Avoid __init__ side effects; follow existing test patterns.
    provider = libvirt_provider.LibvirtProvider.__new__(libvirt_provider.LibvirtProvider)
    provider._vlan_allocations = {}
    provider._next_vlan = {}
    provider._conn = None
    provider._uri = "qemu:///system"
    return provider


class _FakeConn:
    def __init__(self, domain):
        self._domain = domain

    def isAlive(self) -> bool:  # noqa: N802 (libvirt style)
        return True

    def lookupByName(self, _name):  # noqa: N802 (libvirt style)
        return self._domain


class _FakeDomain:
    def state(self):
        return (1, 0)  # VIR_DOMAIN_RUNNING, reason

    def XMLDesc(self):
        return """<domain type="qemu">
  <metadata>
    <archetype:node xmlns:archetype="http://archetype.io/libvirt/1">
      <archetype:kind>cat9000v-q200</archetype:kind>
    </archetype:node>
  </metadata>
</domain>
"""

class _DummyLibvirt:
    VIR_DOMAIN_RUNNING = 1

    class libvirtError(Exception):
        pass


@pytest.mark.asyncio
async def test_get_console_command_falls_back_to_virsh_when_ssh_probe_fails(monkeypatch, tmp_path):
    provider = _make_provider()

    # Ensure provider.conn doesn't try to open libvirt.
    provider._conn = _FakeConn(_FakeDomain())

    # CI environments may not have libvirt-python installed; patch in a minimal shim.
    monkeypatch.setattr(libvirt_provider, "libvirt", _DummyLibvirt)
    # Avoid spawning real threads in unit tests (keeps pytest session from hanging
    # in environments where the event loop doesn't shut down the default executor).
    async def _to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)
    monkeypatch.setattr(libvirt_provider.asyncio, "to_thread", _to_thread)

    # Force "ssh" console for this kind.
    monkeypatch.setattr(libvirt_provider, "get_console_method", lambda _kind: "ssh")
    monkeypatch.setattr(libvirt_provider, "get_console_credentials", lambda _kind: ("admin", "admin"))

    async def _ip(_domain: str) -> str:
        return "192.168.122.233"

    monkeypatch.setattr(provider, "_get_vm_management_ip", _ip)

    # SSH probe fails -> should fall back to virsh console.
    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(returncode=255, stdout="", stderr="Permission denied")

    monkeypatch.setattr(libvirt_provider.subprocess, "run", _fake_run)

    cmd = await provider.get_console_command("lab1", "node1", workspace=tmp_path)
    assert cmd is not None
    assert cmd[:4] == ["virsh", "-c", "qemu:///system", "console"]


@pytest.mark.asyncio
async def test_get_console_command_uses_ssh_when_probe_succeeds(monkeypatch, tmp_path):
    provider = _make_provider()
    provider._conn = _FakeConn(_FakeDomain())

    monkeypatch.setattr(libvirt_provider, "libvirt", _DummyLibvirt)
    async def _to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)
    monkeypatch.setattr(libvirt_provider.asyncio, "to_thread", _to_thread)

    monkeypatch.setattr(libvirt_provider, "get_console_method", lambda _kind: "ssh")
    monkeypatch.setattr(libvirt_provider, "get_console_credentials", lambda _kind: ("admin", "admin"))

    async def _ip(_domain: str) -> str:
        return "192.168.122.233"

    monkeypatch.setattr(provider, "_get_vm_management_ip", _ip)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(libvirt_provider.subprocess, "run", _fake_run)

    cmd = await provider.get_console_command("lab1", "node1", workspace=tmp_path)
    assert cmd is not None
    assert cmd[0:3] == ["sshpass", "-p", "admin"]
    assert "admin@192.168.122.233" in cmd[-1]
