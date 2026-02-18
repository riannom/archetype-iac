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


def test_resolve_management_network_prefers_poap_for_n9kv(monkeypatch):
    provider = _make_provider()
    monkeypatch.setattr(libvirt_provider.settings, "n9kv_poap_preboot_enabled", True, raising=False)
    monkeypatch.setattr(provider, "_node_uses_dedicated_mgmt_interface", lambda _kind: True)
    monkeypatch.setattr(provider, "_ensure_n9kv_poap_network", lambda _lab, _node: "ap-poap-123")

    include, network = provider._resolve_management_network("lab1", "n9k1", "cisco_n9kv")
    assert include is True
    assert network == "ap-poap-123"


def test_resolve_management_network_falls_back_to_default_when_poap_unavailable(monkeypatch):
    provider = _make_provider()
    monkeypatch.setattr(libvirt_provider.settings, "n9kv_poap_preboot_enabled", True, raising=False)
    monkeypatch.setattr(provider, "_node_uses_dedicated_mgmt_interface", lambda _kind: True)
    monkeypatch.setattr(provider, "_ensure_n9kv_poap_network", lambda _lab, _node: None)
    monkeypatch.setattr(provider, "_ensure_libvirt_network", lambda _name: True)

    include, network = provider._resolve_management_network("lab1", "n9k1", "cisco_n9kv")
    assert include is True
    assert network == "default"


def test_domain_has_dedicated_mgmt_interface_accepts_custom_network():
    provider = _make_provider()

    class _Domain:
        def XMLDesc(self, _flags):  # noqa: N802
            return """
            <domain type='kvm'>
              <devices>
                <interface type='network'>
                  <source network='ap-poap-123'/>
                </interface>
              </devices>
            </domain>
            """.strip()

    assert provider._domain_has_dedicated_mgmt_interface(_Domain()) is True
