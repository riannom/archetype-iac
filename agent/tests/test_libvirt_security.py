"""Tests for libvirt XML security: whitelist validation and XML escaping."""
from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

import agent.providers.libvirt as libvirt_provider


def _make_provider() -> libvirt_provider.LibvirtProvider:
    p = libvirt_provider.LibvirtProvider.__new__(libvirt_provider.LibvirtProvider)
    p._vlan_allocations = {}
    p._next_vlan = {}
    p._conn = None
    p._uri = "qemu:///system"
    return p


def _base_node_config(**overrides) -> dict:
    cfg = {
        "memory": 2048,
        "cpu": 1,
        "machine_type": "pc-i440fx-6.2",
        "disk_driver": "virtio",
        "nic_driver": "virtio",
        "libvirt_driver": "kvm",
        "efi_boot": False,
        "interface_count": 1,
        "_display_name": "test",
    }
    cfg.update(overrides)
    return cfg


class TestWhitelistValidation:
    """Verify that invalid machine_type, disk_driver, nic_driver are rejected."""

    def test_invalid_machine_type(self):
        p = _make_provider()
        cfg = _base_node_config(machine_type="pc; malicious")
        with pytest.raises(ValueError, match="Invalid machine type"):
            p._generate_domain_xml(
                "test", cfg, overlay_path="/tmp/o.qcow2",
                interface_count=1, vlan_tags=[2000],
            )

    def test_invalid_disk_driver(self):
        p = _make_provider()
        cfg = _base_node_config(disk_driver="raw; drop")
        with pytest.raises(ValueError, match="Invalid disk driver"):
            p._generate_domain_xml(
                "test", cfg, overlay_path="/tmp/o.qcow2",
                interface_count=1, vlan_tags=[2000],
            )

    def test_invalid_nic_driver(self):
        p = _make_provider()
        cfg = _base_node_config(nic_driver="fake-driver")
        with pytest.raises(ValueError, match="Invalid NIC driver"):
            p._generate_domain_xml(
                "test", cfg, overlay_path="/tmp/o.qcow2",
                interface_count=1, vlan_tags=[2000],
            )

    def test_valid_whitelist_values(self):
        p = _make_provider()
        cfg = _base_node_config(
            machine_type="pc-i440fx-8.2",
            disk_driver="virtio",
            nic_driver="e1000",
        )
        xml = p._generate_domain_xml(
            "test", cfg, overlay_path="/tmp/o.qcow2",
            interface_count=1, vlan_tags=[2000],
        )
        assert "<domain" in xml


class TestXmlEscaping:
    """Verify that special XML characters in user input are escaped."""

    def test_xml_chars_in_node_name(self):
        p = _make_provider()
        cfg = _base_node_config()
        dangerous_name = 'node<>&"test'
        xml = p._generate_domain_xml(
            dangerous_name, cfg, overlay_path="/tmp/o.qcow2",
            interface_count=1, vlan_tags=[2000],
        )
        # The raw dangerous characters must not appear unescaped.
        # Parse to ensure well-formed XML.
        root = ET.fromstring(xml)
        name_elem = root.find("name")
        assert name_elem is not None
        assert name_elem.text == dangerous_name
