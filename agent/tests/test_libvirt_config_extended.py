"""Extended tests for libvirt VM XML generation and config helpers.

Covers standalone functions in ``agent.providers.libvirt_xml`` (domain XML
generation, MAC helpers, EFI/OVMF, driver resolution, disk creation, TCP
serial ports) and ``agent.providers.libvirt_config`` (config extraction,
startup config normalization, injection diagnostics).
"""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import agent.providers.libvirt_xml as xml_mod
import agent.providers.libvirt_config as config_mod
from agent.providers.libvirt_config import (
    extract_config,
    extract_config_via_ssh,
    format_injection_diagnostics,
    get_vm_management_ip,
    prepare_startup_config_for_injection,
)
from agent.providers.libvirt_xml import (
    allocate_tcp_serial_port,
    generate_domain_xml,
    generate_mac_address,
    get_tcp_serial_port,
    find_ovmf_code_path,
    find_ovmf_vars_template,
    resolve_domain_driver,
    translate_container_path_to_host,
)


# ---------------------------------------------------------------------------
# Shared constants matching LibvirtProvider class attrs
# ---------------------------------------------------------------------------

VALID_MACHINE_TYPES = {
    "pc", "q35",
    "pc-i440fx-2.9", "pc-q35-2.9",
    "pc-i440fx-4.2", "pc-q35-4.2",
    "pc-i440fx-6.2", "pc-q35-6.2",
    "pc-i440fx-8.2", "pc-q35-8.2",
    "pc-q35-9.0",
    "virt",
}
VALID_DISK_DRIVERS = {"virtio", "ide", "scsi", "sata"}
VALID_NIC_DRIVERS = {
    "virtio", "e1000", "rtl8139", "i82551", "i82557b",
    "i82559er", "ne2k_pci", "pcnet",
}
NIC_DRIVER_SUBSTITUTIONS = {
    "vmxnet3": "virtio",
    "vmxnet2": "e1000",
    "vmxnet": "e1000",
}
ALLOWED_DOMAIN_DRIVERS = {"kvm", "qemu"}


def _default_node_config(**overrides) -> dict:
    """Build a minimal node_config dict with sensible defaults."""
    cfg = {
        "memory": 2048,
        "cpu": 1,
        "machine_type": "pc-i440fx-6.2",
        "disk_driver": "virtio",
        "nic_driver": "virtio",
        "libvirt_driver": "kvm",
        "efi_boot": False,
        "efi_vars": "",
        "interface_count": 2,
        "_display_name": "test",
    }
    cfg.update(overrides)
    return cfg


def _gen(node_config=None, **kwargs):
    """Shortcut for calling generate_domain_xml with default validation sets."""
    if node_config is None:
        node_config = _default_node_config()
    # Pull node_config overrides from kwargs
    _method_kwargs = {
        "name", "node_config", "overlay_path", "data_volume_path",
        "interface_count", "vlan_tags", "kind",
        "include_management_interface", "management_network",
        "config_iso_path", "config_disk_path", "serial_log_path",
    }
    for key in list(kwargs.keys()):
        if key not in _method_kwargs:
            node_config[key] = kwargs.pop(key)
    iface_count = kwargs.get("interface_count", node_config.get("interface_count", 2))
    reserved = node_config.get("reserved_nics", 0)
    if "vlan_tags" not in kwargs:
        kwargs["vlan_tags"] = [2000 + i for i in range(iface_count + reserved)]
    return generate_domain_xml(
        name=kwargs.pop("name", "test-domain"),
        node_config=node_config,
        overlay_path=kwargs.pop("overlay_path", Path("/tmp/overlay.qcow2")),
        valid_machine_types=VALID_MACHINE_TYPES,
        valid_disk_drivers=VALID_DISK_DRIVERS,
        valid_nic_drivers=VALID_NIC_DRIVERS,
        nic_driver_substitutions=NIC_DRIVER_SUBSTITUTIONS,
        allowed_domain_drivers=ALLOWED_DOMAIN_DRIVERS,
        **kwargs,
    )


# ===================================================================
# generate_mac_address
# ===================================================================


class TestGenerateMacAddress:
    """Standalone MAC address generation tests."""

    def test_format_uses_qemu_oui(self):
        mac = generate_mac_address("dom1", 0)
        assert mac.startswith("52:54:00:")
        assert len(mac.split(":")) == 6

    def test_deterministic_same_inputs(self):
        assert generate_mac_address("dom1", 0) == generate_mac_address("dom1", 0)

    def test_different_index_produces_different_mac(self):
        assert generate_mac_address("dom1", 0) != generate_mac_address("dom1", 1)

    def test_different_domain_produces_different_mac(self):
        assert generate_mac_address("dom1", 0) != generate_mac_address("dom2", 0)

    def test_hex_octets_valid(self):
        mac = generate_mac_address("test-domain", 5)
        for octet in mac.split(":"):
            int(octet, 16)  # should not raise

    def test_uses_md5_hash(self):
        """Verify the MAC derivation algorithm matches the implementation."""
        domain, idx = "mydom", 3
        h = hashlib.md5(f"{domain}:{idx}".encode(), usedforsecurity=False).digest()
        expected = f"52:54:00:{h[0]:02x}:{h[1]:02x}:{h[2]:02x}"
        assert generate_mac_address(domain, idx) == expected


# ===================================================================
# resolve_domain_driver
# ===================================================================


class TestResolveDomainDriver:
    """Tests for libvirt domain driver resolution."""

    def test_kvm_accepted(self):
        assert resolve_domain_driver("kvm", "n1", ALLOWED_DOMAIN_DRIVERS) == "kvm"

    def test_qemu_accepted(self):
        assert resolve_domain_driver("qemu", "n1", ALLOWED_DOMAIN_DRIVERS) == "qemu"

    def test_none_defaults_to_kvm(self):
        assert resolve_domain_driver(None, "n1", ALLOWED_DOMAIN_DRIVERS) == "kvm"

    def test_invalid_falls_back_to_kvm(self):
        assert resolve_domain_driver("xen", "n1", ALLOWED_DOMAIN_DRIVERS) == "kvm"

    def test_whitespace_stripped(self):
        assert resolve_domain_driver("  kvm  ", "n1", ALLOWED_DOMAIN_DRIVERS) == "kvm"

    def test_case_insensitive(self):
        assert resolve_domain_driver("KVM", "n1", ALLOWED_DOMAIN_DRIVERS) == "kvm"
        assert resolve_domain_driver("QEMU", "n1", ALLOWED_DOMAIN_DRIVERS) == "qemu"


# ===================================================================
# find_ovmf helpers
# ===================================================================


class TestOVMFDiscovery:
    """Tests for OVMF firmware file discovery."""

    def test_find_ovmf_code_path_returns_first_existing(self, monkeypatch):
        exists_map = {
            "/usr/share/OVMF/OVMF_CODE.fd": False,
            "/usr/share/OVMF/OVMF_CODE_4M.fd": True,
        }
        monkeypatch.setattr("os.path.exists", lambda p: exists_map.get(p, False))
        result = find_ovmf_code_path()
        assert result == "/usr/share/OVMF/OVMF_CODE_4M.fd"

    def test_find_ovmf_code_path_returns_none_when_missing(self, monkeypatch):
        monkeypatch.setattr("os.path.exists", lambda p: False)
        assert find_ovmf_code_path() is None

    def test_find_ovmf_vars_template_returns_first_existing(self, monkeypatch):
        exists_map = {"/usr/share/OVMF/OVMF_VARS.fd": True}
        monkeypatch.setattr("os.path.exists", lambda p: exists_map.get(p, False))
        result = find_ovmf_vars_template()
        assert result == "/usr/share/OVMF/OVMF_VARS.fd"

    def test_find_ovmf_vars_template_returns_none_when_missing(self, monkeypatch):
        monkeypatch.setattr("os.path.exists", lambda p: False)
        assert find_ovmf_vars_template() is None


# ===================================================================
# translate_container_path_to_host
# ===================================================================


class TestTranslateContainerPath:
    """Tests for container-to-host path translation."""

    def test_host_image_path_env_replaces_prefix(self, monkeypatch):
        monkeypatch.setenv("ARCHETYPE_HOST_IMAGE_PATH", "/host/images")
        result = translate_container_path_to_host(
            "/var/lib/archetype/images/disk.qcow2"
        )
        assert result == "/host/images/disk.qcow2"

    def test_host_image_path_env_no_match_returns_original(self, monkeypatch):
        monkeypatch.setenv("ARCHETYPE_HOST_IMAGE_PATH", "/host/images")
        result = translate_container_path_to_host("/other/path/disk.qcow2")
        assert result == "/other/path/disk.qcow2"

    def test_fallback_returns_original_when_no_env_no_volume(self, monkeypatch):
        monkeypatch.delenv("ARCHETYPE_HOST_IMAGE_PATH", raising=False)
        monkeypatch.setattr("os.path.exists", lambda p: False)
        result = translate_container_path_to_host("/some/random/path.qcow2")
        assert result == "/some/random/path.qcow2"


# ===================================================================
# allocate_tcp_serial_port / get_tcp_serial_port
# ===================================================================


class TestTCPSerialPort:
    """Tests for TCP serial port allocation and extraction."""

    def test_allocate_returns_positive_port(self):
        port = allocate_tcp_serial_port()
        assert isinstance(port, int)
        assert port > 0

    def test_allocate_returns_different_ports(self):
        ports = {allocate_tcp_serial_port() for _ in range(5)}
        # At least some should differ (OS picks random ephemeral ports)
        assert len(ports) >= 2

    def test_get_tcp_serial_port_from_domain(self):
        domain_xml = """<domain>
          <devices>
            <serial type='tcp'>
              <source mode='bind' host='127.0.0.1' service='12345'/>
              <protocol type='telnet'/>
            </serial>
          </devices>
        </domain>"""
        domain = MagicMock()
        domain.XMLDesc.return_value = domain_xml
        assert get_tcp_serial_port(domain) == 12345

    def test_get_tcp_serial_port_returns_none_for_pty(self):
        domain_xml = """<domain>
          <devices>
            <serial type='pty'><target port='0'/></serial>
          </devices>
        </domain>"""
        domain = MagicMock()
        domain.XMLDesc.return_value = domain_xml
        assert get_tcp_serial_port(domain) is None

    def test_get_tcp_serial_port_returns_none_on_exception(self):
        domain = MagicMock()
        domain.XMLDesc.side_effect = RuntimeError("boom")
        assert get_tcp_serial_port(domain) is None


# ===================================================================
# Domain XML generation — memory / vCPU
# ===================================================================


class TestDomainXMLMemoryVCPU:
    """Tests for memory and vCPU settings in generated XML."""

    def test_memory_element_matches_config(self):
        xml = _gen(node_config=_default_node_config(memory=4096))
        root = ET.fromstring(xml)
        mem = root.find("memory")
        assert mem.text == "4096"
        assert mem.get("unit") == "MiB"

    def test_vcpu_element_matches_config(self):
        xml = _gen(node_config=_default_node_config(cpu=4))
        root = ET.fromstring(xml)
        vcpu = root.find("vcpu")
        assert vcpu.text == "4"

    def test_domain_name_in_xml(self):
        xml = _gen(name="my-vm")
        root = ET.fromstring(xml)
        assert root.find("name").text == "my-vm"

    def test_domain_type_kvm(self):
        xml = _gen()
        root = ET.fromstring(xml)
        assert root.get("type") == "kvm"

    def test_domain_type_qemu(self):
        xml = _gen(node_config=_default_node_config(libvirt_driver="qemu"))
        root = ET.fromstring(xml)
        assert root.get("type") == "qemu"


# ===================================================================
# Domain XML — disk configurations
# ===================================================================


class TestDomainXMLDisks:
    """Tests for disk element generation."""

    def test_boot_disk_virtio_uses_vd_prefix(self):
        xml = _gen(node_config=_default_node_config(disk_driver="virtio"))
        root = ET.fromstring(xml)
        disk = root.find(".//disk[@device='disk']")
        target = disk.find("target")
        assert target.get("dev") == "vda"
        assert target.get("bus") == "virtio"

    def test_boot_disk_ide_uses_hd_prefix(self):
        xml = _gen(node_config=_default_node_config(disk_driver="ide"))
        root = ET.fromstring(xml)
        disk = root.find(".//disk[@device='disk']")
        target = disk.find("target")
        assert target.get("dev") == "hda"
        assert target.get("bus") == "ide"

    def test_boot_disk_sata_uses_sd_prefix(self):
        xml = _gen(node_config=_default_node_config(disk_driver="sata"))
        root = ET.fromstring(xml)
        disk = root.find(".//disk[@device='disk']")
        target = disk.find("target")
        assert target.get("dev") == "sda"
        assert target.get("bus") == "sata"

    def test_boot_disk_scsi_uses_sd_prefix(self):
        xml = _gen(node_config=_default_node_config(disk_driver="scsi"))
        root = ET.fromstring(xml)
        disk = root.find(".//disk[@device='disk']")
        target = disk.find("target")
        assert target.get("dev") == "sda"
        assert target.get("bus") == "scsi"

    def test_data_volume_second_disk(self):
        xml = _gen(data_volume_path=Path("/tmp/data.qcow2"))
        root = ET.fromstring(xml)
        disks = root.findall(".//disk[@device='disk']")
        assert len(disks) == 2
        assert disks[1].find("source").get("file") == "/tmp/data.qcow2"
        assert disks[1].find("target").get("dev") == "vdb"

    def test_overlay_path_in_source(self):
        xml = _gen(overlay_path=Path("/my/overlay.qcow2"))
        root = ET.fromstring(xml)
        disk = root.find(".//disk[@device='disk']")
        assert disk.find("source").get("file") == "/my/overlay.qcow2"

    def test_all_disks_use_cache_none(self):
        xml = _gen(data_volume_path=Path("/tmp/data.qcow2"))
        root = ET.fromstring(xml)
        for disk in root.findall(".//disk[@device='disk']"):
            drv = disk.find("driver")
            assert drv.get("cache") == "none"
            assert drv.get("io") == "native"
            assert drv.get("discard") == "unmap"


# ===================================================================
# Domain XML — NIC configurations
# ===================================================================


class TestDomainXMLNICs:
    """Tests for NIC interface element generation."""

    def test_bridge_interfaces_use_ovs(self):
        xml = _gen(interface_count=2)
        root = ET.fromstring(xml)
        for iface in root.findall(".//devices/interface[@type='bridge']"):
            vport = iface.find("virtualport")
            assert vport.get("type") == "openvswitch"

    def test_e1000_nic_driver(self):
        xml = _gen(node_config=_default_node_config(nic_driver="e1000"), interface_count=1)
        root = ET.fromstring(xml)
        ifaces = root.findall(".//devices/interface")
        for iface in ifaces:
            assert iface.find("model").get("type") == "e1000"

    def test_rtl8139_nic_driver(self):
        xml = _gen(node_config=_default_node_config(nic_driver="rtl8139"), interface_count=1)
        root = ET.fromstring(xml)
        iface = root.find(".//devices/interface")
        assert iface.find("model").get("type") == "rtl8139"

    def test_vmxnet_substituted_to_e1000(self):
        xml = _gen(node_config=_default_node_config(nic_driver="vmxnet"), interface_count=1)
        root = ET.fromstring(xml)
        iface = root.find(".//devices/interface")
        assert iface.find("model").get("type") == "e1000"

    def test_vlan_tags_assigned_to_interfaces(self):
        xml = _gen(interface_count=3, vlan_tags=[100, 200, 300])
        root = ET.fromstring(xml)
        ifaces = root.findall(".//devices/interface[@type='bridge']")
        tags = [int(i.find(".//vlan/tag").get("id")) for i in ifaces]
        assert tags == [100, 200, 300]

    def test_minimum_one_interface(self):
        """Even with interface_count=0, at least 1 interface is generated."""
        xml = _gen(interface_count=0, vlan_tags=[100])
        root = ET.fromstring(xml)
        ifaces = root.findall(".//devices/interface[@type='bridge']")
        assert len(ifaces) >= 1

    def test_each_interface_has_unique_mac(self):
        xml = _gen(interface_count=4, vlan_tags=[100, 200, 300, 400])
        root = ET.fromstring(xml)
        macs = [
            i.find("mac").get("address")
            for i in root.findall(".//devices/interface[@type='bridge']")
        ]
        assert len(set(macs)) == len(macs)


# ===================================================================
# Domain XML — CPU features
# ===================================================================


class TestDomainXMLCPUFeatures:
    """Tests for CPU topology and feature disable flags."""

    def test_cpu_features_disable_generates_feature_elements(self):
        xml = _gen(cpu_features_disable=["smep", "smap", "pku"])
        root = ET.fromstring(xml)
        cpu = root.find("cpu")
        features = cpu.findall("feature[@policy='disable']")
        names = {f.get("name") for f in features}
        assert names == {"smep", "smap", "pku"}

    def test_cpu_features_empty_no_feature_elements(self):
        xml = _gen(cpu_features_disable=[])
        root = ET.fromstring(xml)
        cpu = root.find("cpu")
        features = cpu.findall("feature")
        assert len(features) == 0

    def test_cpu_topology_with_features(self):
        """Both topology and features can coexist."""
        xml = _gen(cpu=4, cpu_sockets=1, cpu_features_disable=["umip"])
        root = ET.fromstring(xml)
        cpu = root.find("cpu")
        assert cpu.find("topology") is not None
        assert cpu.find("feature[@policy='disable']").get("name") == "umip"

    def test_host_passthrough_mode(self):
        xml = _gen()
        root = ET.fromstring(xml)
        cpu = root.find("cpu")
        assert cpu.get("mode") == "host-passthrough"
        assert cpu.get("migratable") == "off"


# ===================================================================
# Domain XML — serial port setup
# ===================================================================


class TestDomainXMLSerial:
    """Tests for serial/console XML generation."""

    def test_pty_serial_default(self):
        xml = _gen()
        root = ET.fromstring(xml)
        serial = root.find(".//devices/serial[@type='pty']")
        assert serial is not None
        console = root.find(".//devices/console[@type='pty']")
        assert console is not None

    def test_tcp_serial_with_telnet_protocol(self):
        xml = _gen(serial_type="tcp")
        root = ET.fromstring(xml)
        serial = root.find(".//devices/serial[@type='tcp']")
        assert serial is not None
        assert serial.find("protocol").get("type") == "telnet"
        source = serial.find("source")
        assert source.get("mode") == "bind"
        assert source.get("host") == "127.0.0.1"
        assert int(source.get("service")) > 0

    def test_serial_port_count_creates_additional_ports(self):
        xml = _gen(serial_port_count=3)
        root = ET.fromstring(xml)
        serials = root.findall(".//devices/serial")
        assert len(serials) == 3

    def test_tcp_serial_suppresses_graphics(self):
        xml = _gen(serial_type="tcp")
        root = ET.fromstring(xml)
        assert root.find(".//devices/graphics") is None
        assert root.find(".//devices/video") is None

    def test_nographic_suppresses_graphics(self):
        xml = _gen(nographic=True)
        root = ET.fromstring(xml)
        assert root.find(".//devices/graphics") is None

    def test_pty_has_vnc_graphics(self):
        xml = _gen()
        root = ET.fromstring(xml)
        gfx = root.find(".//devices/graphics")
        assert gfx is not None
        assert gfx.get("type") == "vnc"

    def test_serial_log_on_primary_only(self):
        xml = _gen(serial_port_count=2, serial_log_path=Path("/tmp/serial.log"))
        root = ET.fromstring(xml)
        serials = root.findall(".//devices/serial")
        assert serials[0].find("log") is not None
        for s in serials[1:]:
            assert s.find("log") is None


# ===================================================================
# Domain XML — EFI boot configurations
# ===================================================================


class TestDomainXMLEFI:
    """Tests for EFI boot mode XML generation."""

    def test_stateless_efi_qemu_commandline(self, monkeypatch):
        monkeypatch.setattr(xml_mod, "find_ovmf_code_path", lambda: "/usr/share/OVMF/OVMF_CODE.fd")
        xml = _gen(efi_boot=True, efi_vars="stateless")
        root = ET.fromstring(xml)
        # No <loader> or <nvram>
        assert root.find(".//os/loader") is None
        assert root.find(".//os/nvram") is None
        # Has qemu:commandline with pflash
        ns = {"qemu": "http://libvirt.org/schemas/domain/qemu/1.0"}
        args = root.findall(".//qemu:commandline/qemu:arg", ns)
        pflash_values = [a.get("value") for a in args if "pflash" in a.get("value", "")]
        assert len(pflash_values) >= 1

    def test_stateless_efi_no_firmware_attribute(self, monkeypatch):
        monkeypatch.setattr(xml_mod, "find_ovmf_code_path", lambda: "/usr/share/OVMF/OVMF_CODE.fd")
        xml = _gen(efi_boot=True, efi_vars="stateless")
        root = ET.fromstring(xml)
        assert root.find("os").get("firmware") is None

    def test_stateful_efi_has_firmware_attribute(self, monkeypatch):
        monkeypatch.setattr(xml_mod, "find_ovmf_code_path", lambda: "/usr/share/OVMF/OVMF_CODE.fd")
        monkeypatch.setattr(xml_mod, "find_ovmf_vars_template", lambda: "/usr/share/OVMF/OVMF_VARS.fd")
        xml = _gen(efi_boot=True, efi_vars="")
        root = ET.fromstring(xml)
        os_elem = root.find("os")
        assert os_elem.get("firmware") == "efi"
        assert root.find(".//os/loader") is not None
        assert root.find(".//os/nvram") is not None

    def test_efi_smm_off(self, monkeypatch):
        monkeypatch.setattr(xml_mod, "find_ovmf_code_path", lambda: "/usr/share/OVMF/OVMF_CODE.fd")
        xml = _gen(efi_boot=True, efi_vars="stateless")
        root = ET.fromstring(xml)
        smm = root.find(".//features/smm")
        assert smm is not None
        assert smm.get("state") == "off"

    def test_no_efi_no_smm(self):
        xml = _gen(efi_boot=False)
        root = ET.fromstring(xml)
        assert root.find(".//features/smm") is None

    def test_stateless_efi_without_ovmf_still_valid(self, monkeypatch):
        monkeypatch.setattr(xml_mod, "find_ovmf_code_path", lambda: None)
        xml = _gen(efi_boot=True, efi_vars="stateless")
        root = ET.fromstring(xml)
        assert root.tag == "domain"


# ===================================================================
# Domain XML — clock, features, devices
# ===================================================================


class TestDomainXMLMiscDevices:
    """Tests for clock, ACPI, memballoon, RNG."""

    def test_clock_timers_present(self):
        xml = _gen()
        root = ET.fromstring(xml)
        clock = root.find("clock")
        assert clock.get("offset") == "utc"
        timers = {t.get("name"): t for t in clock.findall("timer")}
        assert "rtc" in timers
        assert "pit" in timers
        assert timers["hpet"].get("present") == "no"

    def test_acpi_and_apic_features(self):
        xml = _gen()
        root = ET.fromstring(xml)
        features = root.find("features")
        assert features.find("acpi") is not None
        assert features.find("apic") is not None

    def test_memballoon_none(self):
        xml = _gen()
        root = ET.fromstring(xml)
        balloon = root.find(".//memballoon")
        assert balloon.get("model") == "none"

    def test_rng_virtio_urandom(self):
        xml = _gen()
        root = ET.fromstring(xml)
        rng = root.find(".//rng")
        assert rng.get("model") == "virtio"
        assert rng.find("backend").text == "/dev/urandom"

    def test_emulator_path(self):
        xml = _gen()
        root = ET.fromstring(xml)
        emul = root.find(".//devices/emulator")
        assert emul.text == "/usr/bin/qemu-system-x86_64"


# ===================================================================
# Domain XML — cputune
# ===================================================================


class TestDomainXMLCPUTune:
    """Tests for cpu_limit -> cputune XML generation."""

    def test_cpu_limit_50_pct_2_cpus(self):
        xml = _gen(cpu=2, cpu_limit=50)
        root = ET.fromstring(xml)
        cputune = root.find("cputune")
        assert cputune is not None
        assert int(cputune.find("period").text) == 100000
        assert int(cputune.find("quota").text) == 100000  # 50% of 2 CPUs

    def test_cpu_limit_100_pct(self):
        xml = _gen(cpu=1, cpu_limit=100)
        root = ET.fromstring(xml)
        cputune = root.find("cputune")
        assert int(cputune.find("quota").text) == 100000

    def test_no_cpu_limit_no_cputune(self):
        xml = _gen()
        root = ET.fromstring(xml)
        assert root.find("cputune") is None

    def test_cpu_limit_clamped_to_1(self):
        """cpu_limit below 1 should be clamped to 1%."""
        xml = _gen(cpu=1, cpu_limit=0)
        root = ET.fromstring(xml)
        cputune = root.find("cputune")
        assert cputune is not None
        quota = int(cputune.find("quota").text)
        assert quota > 0


# ===================================================================
# prepare_startup_config_for_injection
# ===================================================================


class TestPrepareStartupConfig:
    """Tests for startup config normalization before injection."""

    PREAMBLE = "hostname {hostname}\nusername admin password cisco"

    def test_empty_config_returns_empty(self):
        result = prepare_startup_config_for_injection(
            "cisco_n9kv", "", node_name="sw1", n9kv_config_preamble=self.PREAMBLE,
        )
        assert result == ""

    def test_none_config_returns_empty(self):
        result = prepare_startup_config_for_injection(
            "cisco_n9kv", None, node_name="sw1", n9kv_config_preamble=self.PREAMBLE,
        )
        assert result == ""

    def test_n9kv_strips_command_echo(self):
        raw = "!Command: show running-config\ninterface Ethernet1/1\n  no shutdown\n"
        result = prepare_startup_config_for_injection(
            "cisco_n9kv", raw, node_name="sw1", n9kv_config_preamble=self.PREAMBLE,
        )
        assert "!Command:" not in result
        assert "interface Ethernet1/1" in result

    def test_n9kv_strips_time_header(self):
        raw = "!Time: Mon Jan 01 00:00:00.000 UTC\nfeature ospf\n"
        result = prepare_startup_config_for_injection(
            "cisco_n9kv", raw, node_name="sw1", n9kv_config_preamble=self.PREAMBLE,
        )
        assert "!Time:" not in result
        assert "feature ospf" in result

    def test_n9kv_strips_running_config_header(self):
        raw = "!Running configuration last done at: 12:00:00\nvlan 10\n"
        result = prepare_startup_config_for_injection(
            "cisco_n9kv", raw, node_name="sw1", n9kv_config_preamble=self.PREAMBLE,
        )
        assert "!Running configuration" not in result
        assert "vlan 10" in result

    def test_n9kv_strips_more_marker(self):
        raw = "--More--\ninterface loopback0\n"
        result = prepare_startup_config_for_injection(
            "cisco_n9kv", raw, node_name="sw1", n9kv_config_preamble=self.PREAMBLE,
        )
        assert "--More--" not in result

    def test_n9kv_strips_prompt_only_lines(self):
        raw = "switch#\ninterface Ethernet1/1\nswitch(config)#\n"
        result = prepare_startup_config_for_injection(
            "cisco_n9kv", raw, node_name="sw1", n9kv_config_preamble=self.PREAMBLE,
        )
        assert "switch#" not in result
        assert "switch(config)#" not in result
        assert "interface Ethernet1/1" in result

    def test_n9kv_prepends_preamble_with_hostname(self):
        raw = "vlan 10\n"
        result = prepare_startup_config_for_injection(
            "cisco_n9kv", raw, node_name="my-switch", n9kv_config_preamble=self.PREAMBLE,
        )
        assert result.startswith("hostname my-switch\n")

    def test_n9kv_default_hostname_when_empty(self):
        raw = "vlan 10\n"
        result = prepare_startup_config_for_injection(
            "cisco_n9kv", raw, node_name="", n9kv_config_preamble=self.PREAMBLE,
        )
        assert "hostname switch\n" in result

    def test_non_n9kv_passthrough(self):
        """Non-N9Kv, non-IOS-XR config should pass through with ANSI/CR stripped."""
        raw = "hostname R1\ninterface GigabitEthernet0/0\n ip address 10.0.0.1 255.255.255.0\n"
        result = prepare_startup_config_for_injection(
            "cisco_iosv", raw, node_name="R1", n9kv_config_preamble=self.PREAMBLE,
        )
        assert result == raw

    def test_ansi_escape_stripped(self):
        raw = "\x1b[32mhostname R1\x1b[0m\n"
        result = prepare_startup_config_for_injection(
            "cisco_iosv", raw, node_name="R1", n9kv_config_preamble=self.PREAMBLE,
        )
        assert "\x1b" not in result
        assert "hostname R1" in result

    def test_carriage_returns_stripped(self):
        raw = "hostname R1\r\ninterface lo0\r\n"
        result = prepare_startup_config_for_injection(
            "cisco_iosv", raw, node_name="R1", n9kv_config_preamble=self.PREAMBLE,
        )
        assert "\r" not in result

    def test_iosxr_strips_building_configuration(self):
        raw = "Building configuration...\n!! IOS XR Configuration 7.0\nhostname XR1\nend\n"
        result = prepare_startup_config_for_injection(
            "cisco_iosxr", raw, node_name="XR1", n9kv_config_preamble=self.PREAMBLE,
        )
        assert "Building configuration" not in result
        assert "!! IOS XR Configuration" not in result
        assert "hostname XR1" in result

    def test_iosxr_strips_rp_prompt(self):
        raw = "RP/0/RP0/CPU0:XR1#show running-config\nhostname XR1\nend\n"
        result = prepare_startup_config_for_injection(
            "cisco_iosxr", raw, node_name="XR1", n9kv_config_preamble=self.PREAMBLE,
        )
        assert "RP/0/RP0/CPU0" not in result
        assert "hostname XR1" in result

    def test_iosxr_strips_last_change_comment(self):
        raw = "!! Last configuration change at Mon Jan 1 00:00:00\nhostname XR1\n"
        result = prepare_startup_config_for_injection(
            "cisco_iosxr", raw, node_name="XR1", n9kv_config_preamble=self.PREAMBLE,
        )
        assert "!! Last configuration" not in result

    def test_n9kv_strips_connected_to_domain(self):
        raw = "Connected to domain switch\nEscape character is '^]'\nfeature bgp\n"
        result = prepare_startup_config_for_injection(
            "cisco_n9kv", raw, node_name="sw1", n9kv_config_preamble=self.PREAMBLE,
        )
        assert "Connected to domain" not in result
        assert "Escape character" not in result
        assert "feature bgp" in result

    def test_result_ends_with_newline(self):
        raw = "vlan 10"
        result = prepare_startup_config_for_injection(
            "cisco_n9kv", raw, node_name="sw1", n9kv_config_preamble=self.PREAMBLE,
        )
        assert result.endswith("\n")


# ===================================================================
# format_injection_diagnostics
# ===================================================================


class TestFormatInjectionDiagnostics:
    """Tests for compact diagnostic string rendering."""

    def test_empty_diag_returns_empty(self):
        assert format_injection_diagnostics(True, {}) == ""

    def test_none_diag_returns_empty(self):
        assert format_injection_diagnostics(True, None) == ""

    def test_ok_flag_included(self):
        result = format_injection_diagnostics(True, {"bytes": 100})
        assert "ok=True" in result

    def test_failed_flag(self):
        result = format_injection_diagnostics(False, {"error": "disk full"})
        assert "ok=False" in result
        assert "error=disk full" in result

    def test_bytes_included(self):
        result = format_injection_diagnostics(True, {"bytes": 4096})
        assert "bytes=4096" in result

    def test_partition_included(self):
        result = format_injection_diagnostics(True, {"resolved_partition": "/dev/nbd0p3"})
        assert "partition=/dev/nbd0p3" in result

    def test_fs_type_included(self):
        result = format_injection_diagnostics(True, {"fs_type": "ext4"})
        assert "fs=ext4" in result

    def test_written_paths_included(self):
        result = format_injection_diagnostics(True, {"written_paths": ["/bootflash/nxos_config.txt"]})
        assert "written=/bootflash/nxos_config.txt" in result

    def test_write_targets_fallback(self):
        result = format_injection_diagnostics(True, {"write_targets": ["/bootflash/startup-config"]})
        assert "targets=/bootflash/startup-config" in result

    def test_exception_included(self):
        result = format_injection_diagnostics(False, {"exception": "OSError: no space"})
        assert "exception=OSError: no space" in result

    def test_full_diagnostics(self):
        diag = {
            "bytes": 2048,
            "resolved_partition": "/dev/nbd0p3",
            "fs_type": "ext4",
            "requested_config_path": "/bootflash/nxos_config.txt",
            "written_paths": ["/bootflash/nxos_config.txt"],
        }
        result = format_injection_diagnostics(True, diag)
        assert "ok=True" in result
        assert "bytes=2048" in result
        assert "partition=/dev/nbd0p3" in result
        assert "fs=ext4" in result
        assert "requested=/bootflash/nxos_config.txt" in result
        assert "written=/bootflash/nxos_config.txt" in result


# ===================================================================
# extract_config
# ===================================================================


class TestExtractConfig:
    """Tests for async config extraction dispatch."""

    @pytest.mark.asyncio
    async def test_returns_none_when_domain_not_found(self):
        run_libvirt = AsyncMock(return_value=None)
        result = await extract_config(
            "lab1", "node1", "cisco_iosv",
            domain_name="arch-lab1-node1",
            uri="qemu:///system",
            run_libvirt_fn=run_libvirt,
            check_domain_running_sync_fn=MagicMock(),
            run_ssh_command_fn=AsyncMock(),
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_domain_not_running(self):
        run_libvirt = AsyncMock(return_value=False)
        result = await extract_config(
            "lab1", "node1", "cisco_iosv",
            domain_name="arch-lab1-node1",
            uri="qemu:///system",
            run_libvirt_fn=run_libvirt,
            check_domain_running_sync_fn=MagicMock(),
            run_ssh_command_fn=AsyncMock(),
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_ssh_extraction_returns_config(self):
        run_libvirt = AsyncMock(return_value=True)
        run_ssh = AsyncMock(return_value="hostname R1\ninterface Gi0/0\n ip addr 10.0.0.1 255.255.255.0\n")

        with patch.object(config_mod, "get_vm_management_ip", new_callable=AsyncMock, return_value="192.168.1.10"):
            with patch.object(config_mod, "get_config_extraction_settings") as mock_settings:
                mock_settings.return_value = SimpleNamespace(
                    method="ssh", user="admin", password="admin",
                    command="show running-config"
                )
                result = await extract_config(
                    "lab1", "node1", "cisco_iosv",
                    domain_name="arch-lab1-node1",
                    uri="qemu:///system",
                    run_libvirt_fn=run_libvirt,
                    check_domain_running_sync_fn=MagicMock(),
                    run_ssh_command_fn=run_ssh,
                )
        assert result is not None
        assert result[0] == "node1"
        assert "hostname R1" in result[1]

    @pytest.mark.asyncio
    async def test_ssh_extraction_discards_short_noise(self):
        """Config shorter than 64 chars without config keywords is discarded."""
        run_libvirt = AsyncMock(return_value=True)
        run_ssh = AsyncMock(return_value="% bad command")

        with patch.object(config_mod, "get_vm_management_ip", new_callable=AsyncMock, return_value="10.0.0.1"):
            with patch.object(config_mod, "get_config_extraction_settings") as mock_settings:
                mock_settings.return_value = SimpleNamespace(
                    method="ssh", user="admin", password="admin",
                    command="show run"
                )
                result = await extract_config(
                    "lab1", "node1", "cisco_iosv",
                    domain_name="arch-lab1-node1",
                    uri="qemu:///system",
                    run_libvirt_fn=run_libvirt,
                    check_domain_running_sync_fn=MagicMock(),
                    run_ssh_command_fn=run_ssh,
                )
        assert result is None

    @pytest.mark.asyncio
    async def test_unknown_method_returns_none(self):
        run_libvirt = AsyncMock(return_value=True)
        with patch.object(config_mod, "get_config_extraction_settings") as mock_settings:
            mock_settings.return_value = SimpleNamespace(method="unknown")
            result = await extract_config(
                "lab1", "node1", "generic",
                domain_name="arch-lab1-node1",
                uri="qemu:///system",
                run_libvirt_fn=run_libvirt,
                check_domain_running_sync_fn=MagicMock(),
                run_ssh_command_fn=AsyncMock(),
            )
        assert result is None


# ===================================================================
# get_vm_management_ip
# ===================================================================


class TestGetVMManagementIP:
    """Tests for VM management IP address lookup."""

    @pytest.mark.asyncio
    async def test_returns_ip_from_agent_source(self):
        mock_result = SimpleNamespace(
            returncode=0,
            stdout=" Name       MAC address          Protocol     Address\n"
                   "------------------------------------------------------------\n"
                   " vnet0      52:54:00:aa:bb:cc    ipv4         192.168.122.50/24\n",
        )
        with patch.object(config_mod, "asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=mock_result)
            ip = await get_vm_management_ip("test-dom", "qemu:///system")
        assert ip == "192.168.122.50"

    @pytest.mark.asyncio
    async def test_skips_localhost_addresses(self):
        mock_result = SimpleNamespace(
            returncode=0,
            stdout="Name MAC Protocol Address\n"
                   "---\n"
                   "lo 00:00:00:00:00:00 ipv4 127.0.0.1/8\n",
        )
        # Agent source returns loopback, DHCP returns nothing, ARP returns nothing
        no_result = SimpleNamespace(returncode=1, stdout="")
        with patch.object(config_mod, "asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(side_effect=[mock_result, no_result, no_result])
            ip = await get_vm_management_ip("test-dom", "qemu:///system")
        assert ip is None

    @pytest.mark.asyncio
    async def test_returns_none_on_exception(self):
        with patch.object(config_mod, "asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(side_effect=RuntimeError("virsh not found"))
            ip = await get_vm_management_ip("test-dom", "qemu:///system")
        assert ip is None


# ===================================================================
# extract_config_via_ssh
# ===================================================================


class TestExtractConfigViaSSH:
    """Tests for SSH-based config extraction."""

    @pytest.mark.asyncio
    async def test_returns_none_without_ip(self):
        with patch.object(config_mod, "get_vm_management_ip", new_callable=AsyncMock, return_value=None):
            result = await extract_config_via_ssh(
                "arch-lab1-node1", "cisco_iosv", "node1",
                uri="qemu:///system",
                run_ssh_command_fn=AsyncMock(),
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_without_command(self):
        with patch.object(config_mod, "get_vm_management_ip", new_callable=AsyncMock, return_value="10.0.0.1"):
            with patch.object(config_mod, "get_config_extraction_settings") as mock_s:
                mock_s.return_value = SimpleNamespace(
                    method="ssh", user="admin", password="admin", command=None,
                )
                result = await extract_config_via_ssh(
                    "arch-lab1-node1", "cisco_iosv", "node1",
                    uri="qemu:///system",
                    run_ssh_command_fn=AsyncMock(),
                )
        assert result is None

    @pytest.mark.asyncio
    async def test_delegates_to_ssh_fn(self):
        ssh_fn = AsyncMock(return_value="hostname R1\n")
        with patch.object(config_mod, "get_vm_management_ip", new_callable=AsyncMock, return_value="10.0.0.1"):
            with patch.object(config_mod, "get_config_extraction_settings") as mock_s:
                mock_s.return_value = SimpleNamespace(
                    method="ssh", user="cisco", password="cisco123",
                    command="show running-config",
                )
                result = await extract_config_via_ssh(
                    "arch-lab1-node1", "cisco_iosv", "node1",
                    uri="qemu:///system",
                    run_ssh_command_fn=ssh_fn,
                )
        assert result == "hostname R1\n"
        ssh_fn.assert_awaited_once_with("10.0.0.1", "cisco", "cisco123", "show running-config", "node1")
