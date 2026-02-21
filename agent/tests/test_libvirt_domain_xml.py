"""Unit tests for libvirt domain XML generation and backing image integrity."""

from __future__ import annotations

import hashlib
import os
import tempfile
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


# ---------------------------------------------------------------------------
# Domain XML: disk cache settings
# ---------------------------------------------------------------------------

class TestDiskCacheSettings:
    """Verify cache='none', io='native', discard='unmap' on all disk elements."""

    def _generate_xml(self, data_volume_path=None):
        p = _make_provider()
        node_config = {
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
        return p._generate_domain_xml(
            "test-domain",
            node_config,
            overlay_path="/tmp/overlay.qcow2",
            data_volume_path=data_volume_path,
            interface_count=1,
            vlan_tags=[2000],
        )

    def test_boot_disk_has_cache_none(self):
        xml = self._generate_xml()
        root = ET.fromstring(xml)
        disks = root.findall(".//disk[@device='disk']")
        assert len(disks) >= 1
        driver = disks[0].find("driver")
        assert driver.get("cache") == "none"
        assert driver.get("io") == "native"
        assert driver.get("discard") == "unmap"

    def test_data_volume_has_cache_none(self):
        xml = self._generate_xml(data_volume_path="/tmp/data.qcow2")
        root = ET.fromstring(xml)
        disks = root.findall(".//disk[@device='disk']")
        assert len(disks) == 2
        for i, disk in enumerate(disks):
            driver = disk.find("driver")
            assert driver.get("cache") == "none", f"disk {i} missing cache=none"
            assert driver.get("io") == "native", f"disk {i} missing io=native"
            assert driver.get("discard") == "unmap", f"disk {i} missing discard=unmap"

    def test_no_writeback_anywhere(self):
        xml = self._generate_xml(data_volume_path="/tmp/data.qcow2")
        assert "writeback" not in xml
        assert "writethrough" not in xml


# ---------------------------------------------------------------------------
# Domain XML: memballoon and rng
# ---------------------------------------------------------------------------

class TestDeviceDefaults:
    """Verify memballoon=none and virtio-rng are present."""

    def _generate_xml(self):
        p = _make_provider()
        node_config = {
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
        return p._generate_domain_xml(
            "test-domain",
            node_config,
            overlay_path="/tmp/overlay.qcow2",
            interface_count=1,
            vlan_tags=[2000],
        )

    def test_memballoon_disabled(self):
        xml = self._generate_xml()
        root = ET.fromstring(xml)
        balloon = root.find(".//memballoon")
        assert balloon is not None, "memballoon element missing"
        assert balloon.get("model") == "none"

    def test_virtio_rng_present(self):
        xml = self._generate_xml()
        root = ET.fromstring(xml)
        rng = root.find(".//rng")
        assert rng is not None, "rng element missing"
        assert rng.get("model") == "virtio"
        backend = rng.find("backend")
        assert backend is not None
        assert backend.get("model") == "random"
        assert backend.text == "/dev/urandom"


# ---------------------------------------------------------------------------
# Backing image integrity check
# ---------------------------------------------------------------------------

class TestVerifyBackingImage:
    """Test _verify_backing_image() SHA256 integrity check logic."""

    def _write_temp_file(self, content: bytes) -> str:
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".qcow2")
        f.write(content)
        f.close()
        return f.name

    def _sha256(self, data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def test_skips_when_no_expected_hash(self):
        """Should silently return when expected_sha256 is None."""
        p = _make_provider()
        # Should not raise
        p._verify_backing_image("/nonexistent/file", None)

    def test_passes_when_hash_matches(self, tmp_path):
        """Should return silently when hashes match."""
        p = _make_provider()
        content = b"test image data for hash verification"
        path = tmp_path / "image.qcow2"
        path.write_bytes(content)
        expected = self._sha256(content)
        # Should not raise
        p._verify_backing_image(str(path), expected)

    def test_raises_on_actual_corruption(self, tmp_path, monkeypatch):
        """Should raise RuntimeError when hash mismatches even after cache drop."""
        p = _make_provider()
        content = b"corrupted image data"
        path = tmp_path / "image.qcow2"
        path.write_bytes(content)
        wrong_hash = self._sha256(b"different data entirely")

        # Mock drop_caches to avoid needing root
        mock_open_calls = []

        original_open = open

        def mock_open_fn(path_arg, *args, **kwargs):
            if str(path_arg) == "/proc/sys/vm/drop_caches":
                mock_open_calls.append(path_arg)
                # Return a no-op writable context manager
                return original_open(os.devnull, "w")
            return original_open(path_arg, *args, **kwargs)

        monkeypatch.setattr("builtins.open", mock_open_fn)

        with pytest.raises(RuntimeError, match="integrity check failed"):
            p._verify_backing_image(str(path), wrong_hash)

        # Verify it attempted to drop caches
        assert len(mock_open_calls) == 1

    def test_recovers_after_cache_drop(self, tmp_path, monkeypatch):
        """Should succeed when second hash (after cache drop) matches."""
        p = _make_provider()
        content = b"good image data"
        path = tmp_path / "image.qcow2"
        path.write_bytes(content)
        correct_hash = self._sha256(content)

        # First call returns wrong hash, second returns correct
        call_count = [0]
        original_compute = p._compute_file_sha256

        def mock_compute(file_path):
            call_count[0] += 1
            if call_count[0] == 1:
                return "deadbeef" * 8  # Wrong hash (64 chars)
            return original_compute(file_path)

        monkeypatch.setattr(p, "_compute_file_sha256", mock_compute)

        # Mock drop_caches
        original_open = open

        def mock_open_fn(path_arg, *args, **kwargs):
            if str(path_arg) == "/proc/sys/vm/drop_caches":
                return original_open(os.devnull, "w")
            return original_open(path_arg, *args, **kwargs)

        monkeypatch.setattr("builtins.open", mock_open_fn)

        # Should not raise — recovery succeeds on second attempt
        p._verify_backing_image(str(path), correct_hash)
        assert call_count[0] == 2

    def test_compute_file_sha256(self, tmp_path):
        """Verify _compute_file_sha256 returns correct hash."""
        p = _make_provider()
        content = b"hello world" * 1000
        path = tmp_path / "test.bin"
        path.write_bytes(content)
        expected = hashlib.sha256(content).hexdigest()
        assert p._compute_file_sha256(str(path)) == expected


# ---------------------------------------------------------------------------
# Shared XML generation helper
# ---------------------------------------------------------------------------


def _gen_xml(provider=None, **overrides):
    """Generate domain XML with sensible defaults and configurable overrides."""
    if provider is None:
        provider = _make_provider()
    node_config = {
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

    # _generate_domain_xml direct keyword arguments
    _method_kwargs = {
        "name", "node_config", "overlay_path", "data_volume_path",
        "interface_count", "vlan_tags", "kind",
        "include_management_interface", "management_network",
        "config_iso_path", "serial_log_path",
    }

    # Route overrides: method params stay as kwargs, everything else → node_config
    for key in list(overrides.keys()):
        if key not in _method_kwargs:
            node_config[key] = overrides.pop(key)

    # Defaults for _generate_domain_xml kwargs
    kwargs = {
        "name": "test-domain",
        "node_config": node_config,
        "overlay_path": "/tmp/overlay.qcow2",
        "interface_count": node_config.get("interface_count", 2),
        "vlan_tags": overrides.pop("vlan_tags", None),
    }
    if kwargs["vlan_tags"] is None:
        iface_count = kwargs["interface_count"]
        reserved = node_config.get("reserved_nics", 0)
        kwargs["vlan_tags"] = [2000 + i for i in range(iface_count + reserved)]

    # Merge remaining overrides as kwargs to _generate_domain_xml
    kwargs.update(overrides)
    return provider._generate_domain_xml(**kwargs)


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------


class TestValidation:
    """Whitelist validation for machine_type, disk_driver, and nic_driver."""

    def test_invalid_machine_type_raises(self):
        with pytest.raises(ValueError, match="Invalid machine type"):
            _gen_xml(machine_type="pc-invalid-999")

    def test_invalid_disk_driver_raises(self):
        with pytest.raises(ValueError, match="Invalid disk driver"):
            _gen_xml(disk_driver="nvme")

    def test_invalid_nic_driver_raises(self):
        with pytest.raises(ValueError, match="Invalid NIC driver"):
            _gen_xml(nic_driver="xgbe_fantasy")

    def test_vmxnet3_substituted_to_virtio(self):
        """VMware vmxnet3 should be auto-substituted to virtio."""
        xml = _gen_xml(nic_driver="vmxnet3", interface_count=1)
        root = ET.fromstring(xml)
        models = [
            iface.find("model").get("type")
            for iface in root.findall(".//devices/interface")
        ]
        assert all(m == "virtio" for m in models)

    def test_vmxnet2_substituted_to_e1000(self):
        """VMware vmxnet2 should be auto-substituted to e1000."""
        xml = _gen_xml(nic_driver="vmxnet2", interface_count=1)
        root = ET.fromstring(xml)
        models = [
            iface.find("model").get("type")
            for iface in root.findall(".//devices/interface")
        ]
        assert all(m == "e1000" for m in models)

    def test_valid_machine_types_accepted(self):
        """All whitelisted machine types should be accepted."""
        for mt in ("pc", "q35", "virt", "pc-i440fx-6.2", "pc-q35-9.0"):
            xml = _gen_xml(machine_type=mt, interface_count=1)
            root = ET.fromstring(xml)
            assert root.find(".//os/type").get("machine") == mt


# ---------------------------------------------------------------------------
# Management interface tests
# ---------------------------------------------------------------------------


class TestManagementInterface:
    """Tests for management NIC generation."""

    def test_management_interface_present(self):
        xml = _gen_xml(
            include_management_interface=True,
            management_network="default",
            interface_count=1,
        )
        root = ET.fromstring(xml)
        net_ifaces = root.findall(".//devices/interface[@type='network']")
        assert len(net_ifaces) == 1

    def test_management_interface_uses_specified_network(self):
        xml = _gen_xml(
            include_management_interface=True,
            management_network="my-mgmt-net",
            interface_count=1,
        )
        root = ET.fromstring(xml)
        net_iface = root.find(".//devices/interface[@type='network']")
        source = net_iface.find("source")
        assert source.get("network") == "my-mgmt-net"

    def test_management_interface_has_no_vlan_tag(self):
        xml = _gen_xml(
            include_management_interface=True,
            interface_count=1,
        )
        root = ET.fromstring(xml)
        net_iface = root.find(".//devices/interface[@type='network']")
        assert net_iface.find(".//vlan") is None

    def test_data_interfaces_still_get_vlan_tags(self):
        xml = _gen_xml(
            include_management_interface=True,
            interface_count=2,
            vlan_tags=[2000, 2001],
        )
        root = ET.fromstring(xml)
        bridge_ifaces = root.findall(".//devices/interface[@type='bridge']")
        assert len(bridge_ifaces) == 2
        for iface in bridge_ifaces:
            assert iface.find(".//vlan/tag") is not None


# ---------------------------------------------------------------------------
# Reserved NIC tests
# ---------------------------------------------------------------------------


class TestReservedNICs:
    """Tests for reserved (dummy) NIC generation."""

    def test_reserved_nics_create_extra_bridge_interfaces(self):
        xml = _gen_xml(reserved_nics=2, interface_count=2)
        root = ET.fromstring(xml)
        bridge_ifaces = root.findall(".//devices/interface[@type='bridge']")
        # 2 reserved + 2 data = 4
        assert len(bridge_ifaces) == 4

    def test_reserved_nics_consume_first_vlan_tags(self):
        xml = _gen_xml(
            reserved_nics=2,
            interface_count=2,
            vlan_tags=[3000, 3001, 3002, 3003],
        )
        root = ET.fromstring(xml)
        bridge_ifaces = root.findall(".//devices/interface[@type='bridge']")
        tags = [
            int(iface.find(".//vlan/tag").get("id"))
            for iface in bridge_ifaces
        ]
        # reserved get tags[0:2], data get tags[2:4]
        assert tags == [3000, 3001, 3002, 3003]

    def test_data_interfaces_offset_by_reserved_nics(self):
        """Data interfaces use vlan_tags starting at reserved_nics offset."""
        xml = _gen_xml(
            reserved_nics=2,
            interface_count=1,
            vlan_tags=[3000, 3001, 3002],
        )
        root = ET.fromstring(xml)
        bridge_ifaces = root.findall(".//devices/interface[@type='bridge']")
        # 3rd interface (index 2) is the first data interface
        data_tag = int(bridge_ifaces[2].find(".//vlan/tag").get("id"))
        assert data_tag == 3002

    def test_reserved_nics_have_distinct_macs(self):
        xml = _gen_xml(reserved_nics=2, interface_count=2)
        root = ET.fromstring(xml)
        bridge_ifaces = root.findall(".//devices/interface[@type='bridge']")
        macs = [iface.find("mac").get("address") for iface in bridge_ifaces]
        # All MACs should be unique
        assert len(set(macs)) == len(macs)


# ---------------------------------------------------------------------------
# Serial console tests
# ---------------------------------------------------------------------------


class TestSerialConsole:
    """Tests for serial console XML generation."""

    def test_default_pty_serial(self):
        xml = _gen_xml()
        root = ET.fromstring(xml)
        serial = root.find(".//devices/serial[@type='pty']")
        assert serial is not None
        console = root.find(".//devices/console[@type='pty']")
        assert console is not None

    def test_tcp_serial_mode(self):
        xml = _gen_xml(serial_type="tcp")
        root = ET.fromstring(xml)
        serial = root.find(".//devices/serial[@type='tcp']")
        assert serial is not None
        protocol = serial.find("protocol")
        assert protocol.get("type") == "telnet"
        source = serial.find("source")
        assert source.get("host") == "127.0.0.1"
        assert source.get("mode") == "bind"

    def test_serial_port_count_multiple(self):
        """serial_port_count=4: first serial + 3 additional PTY serials."""
        xml = _gen_xml(serial_type="tcp", serial_port_count=4)
        root = ET.fromstring(xml)
        all_serials = root.findall(".//devices/serial")
        assert len(all_serials) == 4
        # First is TCP, rest are PTY
        assert all_serials[0].get("type") == "tcp"
        for s in all_serials[1:]:
            assert s.get("type") == "pty"

    def test_tcp_serial_removes_graphics(self):
        """TCP serial type should suppress VNC graphics and video."""
        xml = _gen_xml(serial_type="tcp")
        root = ET.fromstring(xml)
        assert root.find(".//devices/graphics") is None
        assert root.find(".//devices/video") is None

    def test_nographic_removes_graphics(self):
        """nographic=True should suppress VNC graphics and video."""
        xml = _gen_xml(nographic=True)
        root = ET.fromstring(xml)
        assert root.find(".//devices/graphics") is None
        assert root.find(".//devices/video") is None

    def test_default_has_graphics(self):
        """Default PTY serial should include VNC graphics."""
        xml = _gen_xml()
        root = ET.fromstring(xml)
        assert root.find(".//devices/graphics") is not None


# ---------------------------------------------------------------------------
# Stateless EFI tests
# ---------------------------------------------------------------------------


class TestStatelessEFI:
    """Tests for stateless EFI boot via QEMU commandline."""

    def test_stateless_efi_uses_qemu_commandline(self, monkeypatch):
        p = _make_provider()
        monkeypatch.setattr(
            p, "_find_ovmf_code_path",
            lambda: "/usr/share/OVMF/OVMF_CODE.fd",
        )
        monkeypatch.setattr(p, "_find_ovmf_vars_template", lambda: None)

        xml = _gen_xml(provider=p, efi_boot=True, efi_vars="stateless")
        root = ET.fromstring(xml)
        # Should NOT have <loader> or <nvram>
        assert root.find(".//os/loader") is None
        assert root.find(".//os/nvram") is None
        # Should have qemu:commandline with pflash arg
        ns = {"qemu": "http://libvirt.org/schemas/domain/qemu/1.0"}
        args = root.findall(".//qemu:commandline/qemu:arg", ns)
        pflash_found = any("pflash" in a.get("value", "") for a in args)
        assert pflash_found, "Expected pflash in qemu:commandline args"

    def test_stateless_efi_no_firmware_attribute(self, monkeypatch):
        """Stateless EFI should NOT set firmware='efi' on <os>."""
        p = _make_provider()
        monkeypatch.setattr(
            p, "_find_ovmf_code_path",
            lambda: "/usr/share/OVMF/OVMF_CODE.fd",
        )
        monkeypatch.setattr(p, "_find_ovmf_vars_template", lambda: None)

        xml = _gen_xml(provider=p, efi_boot=True, efi_vars="stateless")
        root = ET.fromstring(xml)
        os_elem = root.find("os")
        assert os_elem.get("firmware") is None

    def test_stateless_efi_without_ovmf_still_generates(self, monkeypatch):
        """Missing OVMF should log warning but still generate valid XML."""
        p = _make_provider()
        monkeypatch.setattr(p, "_find_ovmf_code_path", lambda: None)
        monkeypatch.setattr(p, "_find_ovmf_vars_template", lambda: None)

        xml = _gen_xml(provider=p, efi_boot=True, efi_vars="stateless")
        root = ET.fromstring(xml)
        # Should still parse as valid XML
        assert root.tag == "domain"

    def test_stateful_efi_sets_firmware_attribute(self, monkeypatch):
        """Stateful EFI should set firmware='efi' and add <loader>."""
        p = _make_provider()
        monkeypatch.setattr(
            p, "_find_ovmf_code_path",
            lambda: "/usr/share/OVMF/OVMF_CODE.fd",
        )
        monkeypatch.setattr(
            p, "_find_ovmf_vars_template",
            lambda: "/usr/share/OVMF/OVMF_VARS.fd",
        )

        xml = _gen_xml(provider=p, efi_boot=True, efi_vars="")
        root = ET.fromstring(xml)
        os_elem = root.find("os")
        assert os_elem.get("firmware") == "efi"
        loader = root.find(".//os/loader")
        assert loader is not None
        assert "OVMF_CODE" in loader.text
        nvram = root.find(".//os/nvram")
        assert nvram is not None


# ---------------------------------------------------------------------------
# CPU tuning tests
# ---------------------------------------------------------------------------


class TestCPUTuning:
    """Tests for cputune and SMP topology."""

    def test_cpu_limit_generates_cputune(self):
        xml = _gen_xml(cpu=2, cpu_limit=50)
        root = ET.fromstring(xml)
        cputune = root.find("cputune")
        assert cputune is not None
        period = int(cputune.find("period").text)
        quota = int(cputune.find("quota").text)
        assert period == 100000
        # 50% of 2 CPUs = 100000
        assert quota == 100000

    def test_cpu_sockets_generates_topology(self):
        xml = _gen_xml(cpu=4, cpu_sockets=1)
        root = ET.fromstring(xml)
        topology = root.find(".//cpu/topology")
        assert topology is not None
        assert topology.get("sockets") == "1"
        assert topology.get("cores") == "4"
        assert topology.get("threads") == "1"

    def test_cpu_sockets_zero_no_topology(self):
        xml = _gen_xml(cpu=4, cpu_sockets=0)
        root = ET.fromstring(xml)
        topology = root.find(".//cpu/topology")
        assert topology is None

    def test_host_passthrough_always_present(self):
        xml = _gen_xml()
        root = ET.fromstring(xml)
        cpu = root.find("cpu")
        assert cpu is not None
        assert cpu.get("mode") == "host-passthrough"
        assert cpu.get("migratable") == "off"


# ---------------------------------------------------------------------------
# SMBIOS tests
# ---------------------------------------------------------------------------


class TestSMBIOS:
    """Tests for SMBIOS product identification."""

    def test_smbios_product_generates_sysinfo(self):
        xml = _gen_xml(smbios_product="Cisco IOS XRv 9000")
        root = ET.fromstring(xml)
        sysinfo = root.find("sysinfo")
        assert sysinfo is not None
        assert sysinfo.get("type") == "smbios"
        product = sysinfo.find(".//system/entry[@name='product']")
        assert product is not None
        assert product.text == "Cisco IOS XRv 9000"

    def test_no_smbios_product_no_sysinfo(self):
        xml = _gen_xml()  # No smbios_product
        root = ET.fromstring(xml)
        assert root.find("sysinfo") is None

    def test_smbios_mode_in_os_element(self):
        xml = _gen_xml(smbios_product="Cisco IOS XRv 9000")
        root = ET.fromstring(xml)
        smbios = root.find(".//os/smbios")
        assert smbios is not None
        assert smbios.get("mode") == "sysinfo"


# ---------------------------------------------------------------------------
# Metadata tests
# ---------------------------------------------------------------------------


class TestMetadata:
    """Tests for archetype metadata in domain XML."""

    def test_kind_stored_in_metadata(self):
        xml = _gen_xml(kind="cisco_iosv")
        root = ET.fromstring(xml)
        ns = {"a": "http://archetype.io/libvirt/1"}
        kind = root.find(".//metadata/a:node/a:kind", ns)
        assert kind is not None
        assert kind.text == "cisco_iosv"

    def test_readiness_override_stored(self, monkeypatch):
        """Readiness probe different from vendor default should be stored."""
        # cisco_iosv default is "log_pattern"; override to "none"
        xml = _gen_xml(
            kind="cisco_iosv",
            readiness_probe="none",
        )
        root = ET.fromstring(xml)
        ns = {"a": "http://archetype.io/libvirt/1"}
        probe = root.find(".//metadata/a:node/a:readiness_probe", ns)
        assert probe is not None
        assert probe.text == "none"

    def test_readiness_matching_default_not_stored(self):
        """Readiness probe matching vendor default should NOT be stored."""
        xml = _gen_xml(
            kind="cisco_iosv",
            readiness_probe="log_pattern",  # matches cisco_iosv default
        )
        root = ET.fromstring(xml)
        ns = {"a": "http://archetype.io/libvirt/1"}
        probe = root.find(".//metadata/a:node/a:readiness_probe", ns)
        assert probe is None

    def test_tcp_serial_type_stored(self):
        """Non-default serial_type='tcp' should be stored."""
        xml = _gen_xml(kind="cisco_iosv", serial_type="tcp")
        root = ET.fromstring(xml)
        ns = {"a": "http://archetype.io/libvirt/1"}
        st = root.find(".//metadata/a:node/a:serial_type", ns)
        assert st is not None
        assert st.text == "tcp"

    def test_pty_serial_type_not_stored(self):
        """Default serial_type='pty' should NOT be stored."""
        xml = _gen_xml(kind="cisco_iosv", serial_type="pty")
        root = ET.fromstring(xml)
        ns = {"a": "http://archetype.io/libvirt/1"}
        st = root.find(".//metadata/a:node/a:serial_type", ns)
        assert st is None

    def test_no_kind_no_metadata(self):
        """Without kind, no metadata section should be generated."""
        xml = _gen_xml()  # No kind
        root = ET.fromstring(xml)
        assert root.find("metadata") is None


# ---------------------------------------------------------------------------
# MAC address determinism tests
# ---------------------------------------------------------------------------


class TestMACDeterminism:
    """Tests for deterministic MAC address generation."""

    def test_same_inputs_same_mac(self):
        p = _make_provider()
        mac1 = p._generate_mac_address("test-domain", 0)
        mac2 = p._generate_mac_address("test-domain", 0)
        assert mac1 == mac2

    def test_different_index_different_mac(self):
        p = _make_provider()
        mac0 = p._generate_mac_address("test-domain", 0)
        mac1 = p._generate_mac_address("test-domain", 1)
        assert mac0 != mac1

    def test_mac_format_qemu_prefix(self):
        p = _make_provider()
        mac = p._generate_mac_address("test-domain", 0)
        assert mac.startswith("52:54:00:")
        octets = mac.split(":")
        assert len(octets) == 6
        for octet in octets:
            assert len(octet) == 2
            int(octet, 16)  # Should not raise


# ---------------------------------------------------------------------------
# Config ISO tests
# ---------------------------------------------------------------------------


class TestConfigISO:
    """Tests for config ISO (CD-ROM) attachment."""

    def test_config_iso_creates_cdrom(self):
        xml = _gen_xml(config_iso_path="/tmp/config.iso")
        root = ET.fromstring(xml)
        cdrom = root.find(".//disk[@device='cdrom']")
        assert cdrom is not None
        source = cdrom.find("source")
        assert source.get("file") == "/tmp/config.iso"
        target = cdrom.find("target")
        assert target.get("bus") == "ide"
        assert cdrom.find("readonly") is not None

    def test_no_config_iso_no_cdrom(self):
        xml = _gen_xml()  # No config_iso_path
        root = ET.fromstring(xml)
        cdrom = root.find(".//disk[@device='cdrom']")
        assert cdrom is None


# ---------------------------------------------------------------------------
# Vendor round-trip tests
# ---------------------------------------------------------------------------


class TestVendorRoundTrip:
    """Generate XML using real VENDOR_CONFIGS and verify critical properties."""

    def _xml_for_vendor(self, vendor_key, monkeypatch):
        """Generate domain XML using real vendor config values."""
        from agent.vendors import VENDOR_CONFIGS, get_libvirt_config

        vc = VENDOR_CONFIGS[vendor_key]
        lc = get_libvirt_config(vendor_key)

        p = _make_provider()
        monkeypatch.setattr(
            p, "_find_ovmf_code_path",
            lambda: "/usr/share/OVMF/OVMF_CODE.fd",
        )
        monkeypatch.setattr(
            p, "_find_ovmf_vars_template",
            lambda: "/usr/share/OVMF/OVMF_VARS.fd",
        )

        node_config = {
            "memory": lc.memory_mb,
            "cpu": lc.cpu_count,
            "machine_type": lc.machine_type,
            "disk_driver": lc.disk_driver,
            "nic_driver": lc.nic_driver,
            "efi_boot": lc.efi_boot,
            "efi_vars": lc.efi_vars,
            "serial_type": lc.serial_type,
            "serial_port_count": lc.serial_port_count,
            "nographic": lc.nographic,
            "smbios_product": lc.smbios_product,
            "reserved_nics": lc.reserved_nics,
            "cpu_sockets": lc.cpu_sockets,
        }
        iface_count = 4
        reserved = lc.reserved_nics
        vlan_tags = [2000 + i for i in range(iface_count + reserved)]

        xml = p._generate_domain_xml(
            f"test-{vendor_key}",
            node_config,
            overlay_path="/tmp/overlay.qcow2",
            interface_count=iface_count,
            vlan_tags=vlan_tags,
            kind=vendor_key,
        )
        return ET.fromstring(xml)

    def test_iosv_round_trip(self, monkeypatch):
        """IOSv: IDE disk, e1000 NIC, no EFI."""
        root = self._xml_for_vendor("cisco_iosv", monkeypatch)

        # IDE disk driver → hd* target prefix
        disk = root.find(".//disk[@device='disk']")
        assert disk.find("target").get("bus") == "ide"

        # e1000 NICs
        for iface in root.findall(".//devices/interface"):
            assert iface.find("model").get("type") == "e1000"

        # No EFI — no firmware attribute, no qemu:commandline
        os_elem = root.find("os")
        assert os_elem.get("firmware") is None

    def test_xrv9k_round_trip(self, monkeypatch):
        """IOS-XR: TCP serial, 4 serial ports, SMBIOS, reserved_nics=2, stateless EFI, nographic."""
        root = self._xml_for_vendor("cisco_iosxr", monkeypatch)

        # TCP serial
        serial = root.find(".//devices/serial[@type='tcp']")
        assert serial is not None

        # 4 serial ports total
        all_serials = root.findall(".//devices/serial")
        assert len(all_serials) == 4

        # SMBIOS product
        product = root.find(".//sysinfo/system/entry[@name='product']")
        assert product is not None
        assert "XRv 9000" in product.text

        # Reserved NICs (2 extra bridge interfaces before data interfaces)
        bridge_ifaces = root.findall(".//devices/interface[@type='bridge']")
        assert len(bridge_ifaces) == 6  # 2 reserved + 4 data

        # Stateless EFI — qemu:commandline with pflash, no firmware attr
        os_elem = root.find("os")
        assert os_elem.get("firmware") is None
        ns = {"qemu": "http://libvirt.org/schemas/domain/qemu/1.0"}
        args = root.findall(".//qemu:commandline/qemu:arg", ns)
        assert any("pflash" in a.get("value", "") for a in args)

        # Nographic — no VNC
        assert root.find(".//devices/graphics") is None

        # CPU topology: 1 socket
        topology = root.find(".//cpu/topology")
        assert topology is not None
        assert topology.get("sockets") == "1"

    def test_vjunos_switch_round_trip(self, monkeypatch):
        """vJunos Switch: virtio disk+NIC, no EFI."""
        root = self._xml_for_vendor("juniper_vjunosswitch", monkeypatch)

        # Virtio disk
        disk = root.find(".//disk[@device='disk']")
        assert disk.find("target").get("bus") == "virtio"

        # Virtio NICs
        for iface in root.findall(".//devices/interface"):
            assert iface.find("model").get("type") == "virtio"

        # No EFI
        os_elem = root.find("os")
        assert os_elem.get("firmware") is None

    def test_n9kv_round_trip(self, monkeypatch):
        """N9Kv: SATA disk, e1000 NIC, stateful EFI with writable NVRAM."""
        root = self._xml_for_vendor("cisco_n9kv", monkeypatch)

        # SATA disk driver
        disk = root.find(".//disk[@device='disk']")
        assert disk.find("target").get("bus") == "sata"

        # e1000 NICs
        for iface in root.findall(".//devices/interface"):
            assert iface.find("model").get("type") == "e1000"

        # Stateful EFI — <os firmware='efi'> with <loader> and <nvram>
        os_elem = root.find(".//os")
        assert os_elem.get("firmware") == "efi"
        loader = os_elem.find("loader")
        assert loader is not None
        assert loader.get("readonly") == "yes"
        nvram = os_elem.find("nvram")
        assert nvram is not None
        assert nvram.get("template") is not None

        # Machine type i440fx
        type_elem = root.find(".//os/type")
        assert "i440fx" in type_elem.get("machine")


# ---------------------------------------------------------------------------
# Serial log element tests
# ---------------------------------------------------------------------------


class TestSerialLog:
    """Verify <log> element appears in serial devices for lock-free observation."""

    def test_pty_serial_has_log_element(self):
        """PTY serial should include <log> when serial_log_path is provided."""
        xml = _gen_xml(serial_log_path="/tmp/serial-logs/test.log")
        root = ET.fromstring(xml)
        serial = root.find(".//devices/serial[@type='pty']")
        assert serial is not None
        log = serial.find("log")
        assert log is not None, "Missing <log> element in PTY serial"
        assert log.get("file") == "/tmp/serial-logs/test.log"
        assert log.get("append") == "off"

    def test_tcp_serial_has_log_element(self):
        """TCP serial should include <log> when serial_log_path is provided."""
        xml = _gen_xml(
            serial_type="tcp",
            serial_log_path="/tmp/serial-logs/tcp-test.log",
        )
        root = ET.fromstring(xml)
        serial = root.find(".//devices/serial[@type='tcp']")
        assert serial is not None
        log = serial.find("log")
        assert log is not None, "Missing <log> element in TCP serial"
        assert log.get("file") == "/tmp/serial-logs/tcp-test.log"
        assert log.get("append") == "off"

    def test_no_log_without_path(self):
        """No <log> element when serial_log_path is not provided."""
        xml = _gen_xml()
        root = ET.fromstring(xml)
        serial = root.find(".//devices/serial[@type='pty']")
        assert serial is not None
        log = serial.find("log")
        assert log is None, "<log> should not appear without serial_log_path"

    def test_log_path_xml_escaped(self):
        """Paths with special XML chars should be escaped in <log> element."""
        xml = _gen_xml(serial_log_path="/tmp/lab&test/serial.log")
        root = ET.fromstring(xml)
        log = root.find(".//devices/serial/log")
        assert log is not None
        assert log.get("file") == "/tmp/lab&test/serial.log"

    def test_additional_serial_ports_no_log(self):
        """Only the primary serial port (port 0) should have <log>."""
        xml = _gen_xml(
            serial_port_count=2,
            serial_log_path="/tmp/serial.log",
        )
        root = ET.fromstring(xml)
        serials = root.findall(".//devices/serial")
        assert len(serials) >= 2
        # Only the first serial should have <log>
        logs = [s.find("log") for s in serials]
        assert logs[0] is not None, "Primary serial should have <log>"
        for i, log in enumerate(logs[1:], 1):
            assert log is None, f"Serial port {i} should not have <log>"
