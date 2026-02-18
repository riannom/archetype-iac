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


def test_ensure_n9kv_poap_network_defines_dnsmasq_script_options(monkeypatch):
    provider = _make_provider()
    monkeypatch.setattr(libvirt_provider.settings, "agent_port", 8001, raising=False)
    captured: dict[str, str] = {}

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

    class _Conn:
        def isAlive(self):  # noqa: N802
            return True

        def networkLookupByName(self, _name: str):  # noqa: N802
            raise RuntimeError("not found")

        def networkDefineXML(self, xml: str):  # noqa: N802
            captured["xml"] = xml
            return _Net()

    provider._conn = _Conn()

    name = provider._ensure_n9kv_poap_network("lab1", "n9k1")
    gateway, _, _ = provider._n9kv_poap_subnet("lab1", "n9k1")
    bootfile = provider._n9kv_poap_bootfile_url("lab1", "n9k1", gateway)

    assert name == provider._n9kv_poap_network_name("lab1", "n9k1")
    assert "xmlns:dnsmasq='http://libvirt.org/schemas/network/dnsmasq/1.0'" in captured["xml"]
    assert f"dhcp-option-force=66,{gateway}" in captured["xml"]
    assert f"dhcp-option-force=67,{bootfile}" in captured["xml"]


def test_ensure_n9kv_poap_network_recreates_stale_network(monkeypatch):
    provider = _make_provider()
    monkeypatch.setattr(libvirt_provider.settings, "agent_port", 8001, raising=False)
    captured: dict[str, str] = {}

    class _ExistingNet:
        def __init__(self) -> None:
            self._active = 1
            self.destroyed = 0
            self.undefined = 0
            self.created = 0
            self.autostart = []

        def isActive(self):  # noqa: N802
            return self._active

        def create(self):
            self.created += 1
            self._active = 1

        def destroy(self):
            self.destroyed += 1
            self._active = 0

        def undefine(self):
            self.undefined += 1

        def XMLDesc(self, _flags):  # noqa: N802
            return "<network><name>ap-poap-old</name></network>"

        def setAutostart(self, value: bool):  # noqa: N802
            self.autostart.append(value)

    class _NewNet:
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

    existing = _ExistingNet()
    fresh = _NewNet()

    class _Conn:
        def isAlive(self):  # noqa: N802
            return True

        def networkLookupByName(self, _name: str):  # noqa: N802
            return existing

        def networkDefineXML(self, xml: str):  # noqa: N802
            captured["xml"] = xml
            return fresh

    provider._conn = _Conn()

    name = provider._ensure_n9kv_poap_network("lab1", "n9k1")

    assert name == provider._n9kv_poap_network_name("lab1", "n9k1")
    assert existing.destroyed == 1
    assert existing.undefined == 1
    assert fresh.created == 1
    assert fresh.autostart == [True]
    assert "dhcp-option-force=66," in captured["xml"]


def test_ensure_n9kv_poap_network_keeps_existing_when_options_present(monkeypatch):
    provider = _make_provider()
    monkeypatch.setattr(libvirt_provider.settings, "agent_port", 8001, raising=False)
    gateway, _, _ = provider._n9kv_poap_subnet("lab1", "n9k1")
    bootfile = provider._n9kv_poap_bootfile_url("lab1", "n9k1", gateway)

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

        def XMLDesc(self, _flags):  # noqa: N802
            return (
                "<network>"
                f"<dnsmasq:option value='dhcp-option-force=66,{gateway}'/>"
                f"<dnsmasq:option value='dhcp-option-force=67,{bootfile}'/>"
                "</network>"
            )

    net = _Net()
    calls = {"define": 0}

    class _Conn:
        def isAlive(self):  # noqa: N802
            return True

        def networkLookupByName(self, _name: str):  # noqa: N802
            return net

        def networkDefineXML(self, _xml: str):  # noqa: N802
            calls["define"] += 1
            return None

    provider._conn = _Conn()

    name = provider._ensure_n9kv_poap_network("lab1", "n9k1")

    assert name == provider._n9kv_poap_network_name("lab1", "n9k1")
    assert net.created == 1
    assert net.autostart == [True]
    assert calls["define"] == 0
