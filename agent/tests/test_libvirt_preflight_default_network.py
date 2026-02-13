"""Unit tests for libvirt default network preflight.

The libvirt provider should ensure the management network exists and is active
when a VM requires a dedicated mgmt NIC.
"""

from __future__ import annotations

import agent.providers.libvirt as libvirt_provider


def _make_provider() -> libvirt_provider.LibvirtProvider:
    provider = libvirt_provider.LibvirtProvider.__new__(libvirt_provider.LibvirtProvider)
    provider._vlan_allocations = {}
    provider._next_vlan = {}
    provider._conn = None
    provider._uri = "qemu:///system"
    return provider


def test_ensure_libvirt_network_starts_and_autostarts_when_inactive(monkeypatch):
    provider = _make_provider()

    class _Net:
        def __init__(self) -> None:
            self._active = 0
            self.created = 0
            self.autostart = []

        def isActive(self):  # noqa: N802
            return self._active

        def create(self):
            self.created += 1
            self._active = 1

        def setAutostart(self, value: bool):  # noqa: N802
            self.autostart.append(value)

    net = _Net()

    class _Conn:
        def isAlive(self):  # noqa: N802
            return True

        def networkLookupByName(self, name: str):  # noqa: N802
            assert name == "default"
            return net

    provider._conn = _Conn()

    assert provider._ensure_libvirt_network("default") is True
    assert net.created == 1
    assert net.autostart == [True]


def test_ensure_libvirt_network_returns_false_when_missing(monkeypatch):
    provider = _make_provider()

    class _Conn:
        def isAlive(self):  # noqa: N802
            return True

        def networkLookupByName(self, _name: str):  # noqa: N802
            return None

    provider._conn = _Conn()
    assert provider._ensure_libvirt_network("default") is False

