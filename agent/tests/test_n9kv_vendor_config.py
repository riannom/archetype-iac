"""Unit tests for Cisco Nexus 9000v vendor config and domain XML generation.

Verifies that the N9Kv gets e1000 NICs, i440fx machine type, and UEFI boot
so that NX-OS can enumerate Ethernet interfaces and boot without dropping
to the BIOS boot manager.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import agent.providers.libvirt as libvirt_provider
from agent.vendors import VENDOR_CONFIGS, get_libvirt_config


# ---------------------------------------------------------------------------
# Vendor config assertions
# ---------------------------------------------------------------------------


def test_n9kv_nic_driver_is_e1000():
    """NX-OS lacks virtio drivers; e1000 is required for interface enumeration."""
    config = VENDOR_CONFIGS["cisco_n9kv"]
    assert config.nic_driver == "e1000"


def test_n9kv_machine_type_is_i440fx():
    """e1000 has TX hang issues on Q35; i440fx is the reliable choice."""
    config = VENDOR_CONFIGS["cisco_n9kv"]
    assert config.machine_type == "pc-i440fx-6.2"


def test_n9kv_disk_driver_is_sata():
    """NX-OS requires AHCI/SATA to detect bootflash; IDE boots kernel but no bootflash."""
    config = VENDOR_CONFIGS["cisco_n9kv"]
    assert config.disk_driver == "sata"


def test_n9kv_efi_boot_enabled():
    """N9Kv image uses UEFI; legacy BIOS drops to boot manager."""
    config = VENDOR_CONFIGS["cisco_n9kv"]
    assert config.efi_boot is True


def test_n9kv_console_method_is_virsh():
    """N9Kv uses virsh serial console for NX-OS CLI (SSH hits bash underlay)."""
    config = VENDOR_CONFIGS["cisco_n9kv"]
    assert config.console_method == "virsh"


def test_n9kv_readiness_probe_is_log_pattern():
    """N9Kv uses log_pattern probe to detect login prompt before post-boot commands."""
    config = VENDOR_CONFIGS["cisco_n9kv"]
    assert config.readiness_probe == "log_pattern"
    assert config.readiness_pattern is not None
    assert "login:" in config.readiness_pattern


# ---------------------------------------------------------------------------
# LibvirtRuntimeConfig propagation
# ---------------------------------------------------------------------------


def test_get_libvirt_config_propagates_n9kv_settings():
    """Vendor config values must propagate through get_libvirt_config."""
    lc = get_libvirt_config("cisco_n9kv")
    assert lc.efi_boot is True
    assert lc.nic_driver == "e1000"
    assert lc.disk_driver == "sata"
    assert lc.machine_type == "pc-i440fx-6.2"


def test_get_libvirt_config_returns_efi_boot_false_by_default():
    """Devices without explicit efi_boot should default to False."""
    lc = get_libvirt_config("cisco_iosv")
    assert lc.efi_boot is False


# ---------------------------------------------------------------------------
# Domain XML generation
# ---------------------------------------------------------------------------


def _make_provider() -> libvirt_provider.LibvirtProvider:
    p = libvirt_provider.LibvirtProvider.__new__(libvirt_provider.LibvirtProvider)
    p._vlan_allocations = {}
    p._next_vlan = {}
    p._conn = None
    p._uri = "qemu:///system"
    return p


def _generate_xml(provider, *, nic_driver="virtio", efi_boot=False, interface_count=2):
    """Helper to generate domain XML with controllable settings."""
    node_config = {
        "memory": 8192,
        "cpu": 2,
        "machine_type": "pc-i440fx-6.2",
        "disk_driver": "virtio",
        "nic_driver": nic_driver,
        "efi_boot": efi_boot,
        "efi_vars": "",
    }
    xml = provider._generate_domain_xml(
        "test-domain",
        node_config,
        overlay_path=Path("/tmp/test.qcow2"),
        interface_count=interface_count,
        vlan_tags=[2000 + i for i in range(interface_count)],
    )
    return xml


def test_domain_xml_uses_e1000_nic_model(monkeypatch):
    """When nic_driver=e1000, all interface elements should use model type=e1000."""
    provider = _make_provider()
    xml = _generate_xml(provider, nic_driver="e1000", interface_count=2)
    root = ET.fromstring(xml)

    models = [
        iface.find("model").get("type")
        for iface in root.findall(".//devices/interface")
    ]
    assert all(m == "e1000" for m in models), f"Expected all e1000, got {models}"


def test_domain_xml_uses_virtio_nic_model_by_default(monkeypatch):
    """Default nic_driver=virtio should produce virtio model elements."""
    provider = _make_provider()
    xml = _generate_xml(provider, nic_driver="virtio", interface_count=1)
    root = ET.fromstring(xml)

    models = [
        iface.find("model").get("type")
        for iface in root.findall(".//devices/interface")
    ]
    assert all(m == "virtio" for m in models)


def test_domain_xml_efi_boot_sets_firmware_attribute(monkeypatch):
    """efi_boot=True should add firmware='efi' to the <os> element."""
    provider = _make_provider()
    # Stub OVMF path lookups so the code path runs without real files
    monkeypatch.setattr(provider, "_find_ovmf_code_path", lambda: None)
    monkeypatch.setattr(provider, "_find_ovmf_vars_template", lambda: None)

    xml = _generate_xml(provider, efi_boot=True)
    root = ET.fromstring(xml)
    os_elem = root.find("os")
    assert os_elem.get("firmware") == "efi"


def test_domain_xml_no_efi_by_default():
    """Without efi_boot, <os> should NOT have firmware attribute."""
    provider = _make_provider()
    xml = _generate_xml(provider, efi_boot=False)
    root = ET.fromstring(xml)
    os_elem = root.find("os")
    assert os_elem.get("firmware") is None


def test_domain_xml_efi_boot_with_ovmf_adds_loader(monkeypatch):
    """When OVMF firmware exists, efi_boot should add <loader> element."""
    provider = _make_provider()
    monkeypatch.setattr(
        provider, "_find_ovmf_code_path",
        lambda: "/usr/share/OVMF/OVMF_CODE.fd",
    )
    monkeypatch.setattr(provider, "_find_ovmf_vars_template", lambda: None)

    xml = _generate_xml(provider, efi_boot=True)
    root = ET.fromstring(xml)
    loader = root.find(".//os/loader")
    assert loader is not None
    assert "OVMF_CODE" in loader.text


def test_domain_xml_vlan_tags_on_data_interfaces():
    """Each data interface should get its assigned VLAN tag."""
    provider = _make_provider()
    xml = _generate_xml(provider, interface_count=3)
    root = ET.fromstring(xml)

    bridge_ifaces = root.findall(".//devices/interface[@type='bridge']")
    assert len(bridge_ifaces) == 3

    for i, iface in enumerate(bridge_ifaces):
        vlan_tag = iface.find(".//vlan/tag")
        assert vlan_tag is not None, f"Interface {i} missing VLAN tag"
        assert int(vlan_tag.get("id")) == 2000 + i


def test_domain_xml_machine_type_in_os_element():
    """Machine type should appear in the <type> element."""
    provider = _make_provider()
    xml = _generate_xml(provider)
    root = ET.fromstring(xml)
    type_elem = root.find(".//os/type")
    assert type_elem.get("machine") == "pc-i440fx-6.2"


# ---------------------------------------------------------------------------
# Config injection vendor config assertions
# ---------------------------------------------------------------------------


def test_n9kv_config_inject_method_is_bootflash():
    """N9Kv should use bootflash config injection."""
    config = VENDOR_CONFIGS["cisco_n9kv"]
    assert config.config_inject_method == "bootflash"


def test_n9kv_config_inject_path():
    """N9Kv startup-config path on bootflash."""
    config = VENDOR_CONFIGS["cisco_n9kv"]
    assert config.config_inject_path == "/startup-config"


def test_n9kv_post_boot_commands_seed_and_persist_startup_config():
    """N9Kv post-boot commands should import and persist staged startup config."""
    config = VENDOR_CONFIGS["cisco_n9kv"]
    assert "configure terminal ; system no poap ; end" in config.post_boot_commands
    assert "copy bootflash:startup-config running-config" in config.post_boot_commands
    assert "copy running-config startup-config" in config.post_boot_commands


def test_default_device_has_no_config_injection():
    """Devices without explicit config_inject_method should default to 'none'."""
    config = VENDOR_CONFIGS["cisco_iosv"]
    assert config.config_inject_method == "none"


def test_get_libvirt_config_propagates_config_inject_fields():
    """Config injection fields must propagate through get_libvirt_config."""
    lc = get_libvirt_config("cisco_n9kv")
    assert lc.config_inject_method == "bootflash"
    assert lc.config_inject_partition == 0
    assert lc.config_inject_fs_type == "ext2"
    assert lc.config_inject_path == "/startup-config"


def test_get_libvirt_config_fallback_has_no_config_injection():
    """Fallback config should have config_inject_method='none'."""
    lc = get_libvirt_config("unknown_device_xyz_fallback")
    assert lc.config_inject_method == "none"


# ---------------------------------------------------------------------------
# Console POAP abort handling
# ---------------------------------------------------------------------------


def test_prime_console_answers_yes_to_poap_abort():
    """_prime_console_for_prompt must send 'yes' at POAP abort, not Enter."""
    try:
        import pexpect
    except ImportError:
        import pytest as _pytest
        _pytest.skip("pexpect not available")
    from unittest.mock import MagicMock, patch
    from agent.console_extractor import SerialConsoleExtractor

    ext = SerialConsoleExtractor.__new__(SerialConsoleExtractor)
    ext.timeout = 30
    ext.domain_name = "test-domain"
    ext.child = MagicMock()

    sent_lines: list[str] = []
    sent_raw: list[str] = []

    def mock_sendline(text):
        sent_lines.append(text)

    def mock_send(text):
        sent_raw.append(text)

    ext.child.sendline = mock_sendline
    ext.child.send = mock_send

    # Simulate: first expect finds the POAP abort prompt (index 4+N)
    # The POAP abort pattern is the 5th pattern (index 4 after prompt patterns)
    poap_abort_idx = None

    def mock_expect(patterns, timeout=None):
        nonlocal poap_abort_idx
        for i, pat in enumerate(patterns):
            if "Abort" in pat and "Power On Auto Provisioning" in pat:
                poap_abort_idx = i
                return i
        raise pexpect.TIMEOUT("no match")

    ext.child.expect = mock_expect

    result = ext._prime_console_for_prompt(r"[>#]\s*$")

    assert result is True
    # Must have sent "yes" via sendline (not just Enter via send)
    assert "yes" in sent_lines, f"Expected 'yes' in sent_lines, got {sent_lines}"
    # Must NOT have sent Enter before the expect matched
    assert len(sent_raw) == 0, f"Expected no raw sends before POAP abort, got {sent_raw}"


def test_prime_console_sends_enter_when_no_output():
    """_prime_console_for_prompt sends Enter only after expect timeout."""
    try:
        import pexpect
    except ImportError:
        import pytest as _pytest
        _pytest.skip("pexpect not available")
    from unittest.mock import MagicMock
    from agent.console_extractor import SerialConsoleExtractor

    ext = SerialConsoleExtractor.__new__(SerialConsoleExtractor)
    ext.timeout = 30
    ext.domain_name = "test-domain"
    ext.child = MagicMock()

    sent_raw: list[str] = []
    ext.child.send = lambda text: sent_raw.append(text)
    ext.child.sendline = MagicMock()

    call_count = 0
    prompt_idx = 0  # First pattern = prompt

    def mock_expect(patterns, timeout=None):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            raise pexpect.TIMEOUT("no output")
        # Third call: simulate prompt appearing
        return prompt_idx

    ext.child.expect = mock_expect

    result = ext._prime_console_for_prompt(r"[>#]\s*$")

    assert result is True
    # Enter should have been sent during the timeout iterations
    enter_sends = [s for s in sent_raw if s == "\r"]
    assert len(enter_sends) == 2, f"Expected 2 Enter sends, got {len(enter_sends)}"
