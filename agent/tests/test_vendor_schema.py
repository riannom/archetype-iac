"""Unit tests for vendor_schema.py — vendor configuration schema definitions.

Tests cover:
- DeviceType enum values
- All frozen sub-config dataclasses (immutability, field access)
- VendorConfig default field values
- VendorConfig cached_property sub-config accessors
- VendorConfig field propagation to sub-configs
- Edge cases: empty fields, custom values, list/dict defaults
"""

from __future__ import annotations

import pytest
from dataclasses import FrozenInstanceError

from agent.vendor_schema import (
    DeviceType,
    InterfaceConfig,
    ResourceConfig,
    VMConfig,
    ConsoleConfig,
    ReadinessConfig,
    ConfigExtractionConfig,
    ConfigInjectionConfig,
    ContainerConfig,
    UIConfig,
    VendorConfig,
)


class TestDeviceType:
    """Tests for DeviceType enum."""

    def test_all_device_types_exist(self):
        assert DeviceType.ROUTER == "router"
        assert DeviceType.SWITCH == "switch"
        assert DeviceType.FIREWALL == "firewall"
        assert DeviceType.HOST == "host"
        assert DeviceType.CONTAINER == "container"
        assert DeviceType.EXTERNAL == "external"

    def test_device_type_count(self):
        assert len(DeviceType) == 6

    def test_device_type_is_str_enum(self):
        assert isinstance(DeviceType.ROUTER, str)
        assert DeviceType.ROUTER == "router"


class TestInterfaceConfig:
    """Tests for InterfaceConfig frozen dataclass."""

    def test_creation_and_fields(self):
        cfg = InterfaceConfig(
            port_naming="eth",
            port_start_index=0,
            max_ports=8,
            management_interface=None,
        )
        assert cfg.port_naming == "eth"
        assert cfg.port_start_index == 0
        assert cfg.max_ports == 8
        assert cfg.management_interface is None

    def test_frozen_immutability(self):
        cfg = InterfaceConfig("eth", 0, 8, None)
        with pytest.raises(FrozenInstanceError):
            cfg.port_naming = "ge"

    def test_with_management_interface(self):
        cfg = InterfaceConfig("GigabitEthernet", 1, 16, "mgmt0")
        assert cfg.management_interface == "mgmt0"


class TestResourceConfig:
    """Tests for ResourceConfig frozen dataclass."""

    def test_creation_and_fields(self):
        cfg = ResourceConfig(memory=2048, cpu=2)
        assert cfg.memory == 2048
        assert cfg.cpu == 2

    def test_frozen_immutability(self):
        cfg = ResourceConfig(memory=1024, cpu=1)
        with pytest.raises(FrozenInstanceError):
            cfg.memory = 4096


class TestVMConfig:
    """Tests for VMConfig frozen dataclass."""

    def test_creation_with_all_fields(self):
        cfg = VMConfig(
            disk_driver="virtio",
            nic_driver="e1000",
            machine_type="pc-q35-6.2",
            data_volume_gb=0,
            efi_boot=True,
            efi_vars="stateless",
            serial_type="tcp",
            nographic=True,
            serial_port_count=2,
            smbios_product="Cisco N9Kv",
            force_stop=True,
            reserved_nics=2,
            cpu_sockets=2,
            needs_nested_vmx=False,
            cpu_features_disable=("smep", "smap"),
        )
        assert cfg.disk_driver == "virtio"
        assert cfg.nic_driver == "e1000"
        assert cfg.efi_boot is True
        assert cfg.efi_vars == "stateless"
        assert cfg.serial_port_count == 2
        assert cfg.reserved_nics == 2
        assert cfg.cpu_features_disable == ("smep", "smap")

    def test_frozen_immutability(self):
        cfg = VMConfig(
            disk_driver="virtio", nic_driver="virtio", machine_type="pc-q35-6.2",
            data_volume_gb=0, efi_boot=False, efi_vars="", serial_type="pty",
            nographic=False, serial_port_count=1, smbios_product="",
            force_stop=True, reserved_nics=0, cpu_sockets=0,
            needs_nested_vmx=False, cpu_features_disable=(),
        )
        with pytest.raises(FrozenInstanceError):
            cfg.disk_driver = "ide"

    def test_cpu_features_disable_is_tuple(self):
        cfg = VMConfig(
            disk_driver="virtio", nic_driver="virtio", machine_type="pc-q35-6.2",
            data_volume_gb=0, efi_boot=False, efi_vars="", serial_type="pty",
            nographic=False, serial_port_count=1, smbios_product="",
            force_stop=True, reserved_nics=0, cpu_sockets=0,
            needs_nested_vmx=False, cpu_features_disable=("pku", "umip"),
        )
        assert isinstance(cfg.cpu_features_disable, tuple)


class TestConsoleConfig:
    """Tests for ConsoleConfig frozen dataclass."""

    def test_creation(self):
        cfg = ConsoleConfig(
            console_method="ssh",
            console_shell="/bin/bash",
            console_user="admin",
            console_password="secret",
            default_credentials="admin / secret",
        )
        assert cfg.console_method == "ssh"
        assert cfg.console_user == "admin"

    def test_frozen_immutability(self):
        cfg = ConsoleConfig("docker_exec", "bash", "root", "pass", "")
        with pytest.raises(FrozenInstanceError):
            cfg.console_method = "ssh"


class TestReadinessConfig:
    """Tests for ReadinessConfig frozen dataclass."""

    def test_creation(self):
        cfg = ReadinessConfig(
            readiness_probe="log_pattern",
            readiness_pattern=r"System ready",
            readiness_timeout=300,
        )
        assert cfg.readiness_probe == "log_pattern"
        assert cfg.readiness_pattern == r"System ready"
        assert cfg.readiness_timeout == 300

    def test_none_pattern(self):
        cfg = ReadinessConfig("none", None, 120)
        assert cfg.readiness_pattern is None

    def test_frozen_immutability(self):
        cfg = ReadinessConfig("none", None, 120)
        with pytest.raises(FrozenInstanceError):
            cfg.readiness_probe = "cli_probe"


class TestConfigExtractionConfig:
    """Tests for ConfigExtractionConfig frozen dataclass."""

    def test_creation(self):
        cfg = ConfigExtractionConfig(
            config_extract_method="serial",
            config_extract_command="show running-config",
            config_extract_user="admin",
            config_extract_password="cisco",
            config_extract_enable_password="",
            config_extract_timeout=30,
            config_extract_prompt_pattern=r"[\w\-]+[>#]\s*$",
            config_extract_paging_disable="terminal length 0",
        )
        assert cfg.config_extract_method == "serial"
        assert cfg.config_extract_timeout == 30

    def test_frozen_immutability(self):
        cfg = ConfigExtractionConfig("none", "", "", "", "", 30, "", "")
        with pytest.raises(FrozenInstanceError):
            cfg.config_extract_method = "ssh"


class TestConfigInjectionConfig:
    """Tests for ConfigInjectionConfig frozen dataclass."""

    def test_creation(self):
        cfg = ConfigInjectionConfig(
            config_inject_method="bootflash",
            config_inject_partition=1,
            config_inject_fs_type="ext2",
            config_inject_path="/startup-config",
            config_inject_iso_volume_label="",
            config_inject_iso_filename="",
        )
        assert cfg.config_inject_method == "bootflash"
        assert cfg.config_inject_partition == 1

    def test_frozen_immutability(self):
        cfg = ConfigInjectionConfig("none", 0, "ext2", "/", "", "")
        with pytest.raises(FrozenInstanceError):
            cfg.config_inject_method = "iso"


class TestContainerConfig:
    """Tests for ContainerConfig frozen dataclass."""

    def test_creation(self):
        cfg = ContainerConfig(
            environment={"FOO": "bar"},
            capabilities=["NET_ADMIN"],
            privileged=True,
            binds=["/host:/container"],
            entrypoint="/init",
            cmd=["--flag"],
            network_mode="none",
            sysctls={"net.ipv4.ip_forward": "1"},
            runtime="",
            hostname_template="{node}",
            post_boot_commands=["echo hello"],
        )
        assert cfg.environment == {"FOO": "bar"}
        assert cfg.privileged is True
        assert cfg.entrypoint == "/init"
        assert len(cfg.post_boot_commands) == 1

    def test_frozen_immutability(self):
        cfg = ContainerConfig({}, [], False, [], None, None, "none", {}, "", "{node}", [])
        with pytest.raises(FrozenInstanceError):
            cfg.privileged = True


class TestUIConfig:
    """Tests for UIConfig frozen dataclass."""

    def test_creation(self):
        cfg = UIConfig(
            icon="fa-router",
            versions=["4.28", "4.29"],
            is_active=True,
            requires_image=True,
            supported_image_kinds=["docker"],
            documentation_url="https://docs.arista.com",
            license_required=False,
            tags=["bgp", "mpls"],
        )
        assert cfg.icon == "fa-router"
        assert len(cfg.versions) == 2
        assert cfg.requires_image is True
        assert "bgp" in cfg.tags

    def test_frozen_immutability(self):
        cfg = UIConfig("fa-box", [], True, False, [], None, False, [])
        with pytest.raises(FrozenInstanceError):
            cfg.is_active = False


class TestVendorConfigDefaults:
    """Tests for VendorConfig default field values."""

    def test_minimal_creation(self):
        cfg = VendorConfig(
            kind="test_device",
            vendor="TestVendor",
            console_shell="/bin/sh",
            default_image=None,
        )
        assert cfg.kind == "test_device"
        assert cfg.vendor == "TestVendor"
        assert cfg.default_image is None

    def test_default_values(self):
        cfg = VendorConfig(kind="test", vendor="V", console_shell="sh", default_image=None)
        assert cfg.notes == ""
        assert cfg.aliases == []
        assert cfg.platform == ""
        assert cfg.device_type == DeviceType.CONTAINER
        assert cfg.category == "Compute"
        assert cfg.subcategory is None
        assert cfg.label == ""
        assert cfg.icon == "fa-box"
        assert cfg.is_active is True
        assert cfg.port_naming == "eth"
        assert cfg.port_start_index == 0
        assert cfg.max_ports == 8
        assert cfg.management_interface is None
        assert cfg.memory == 1024
        assert cfg.cpu == 1
        assert cfg.disk_driver == "virtio"
        assert cfg.nic_driver == "virtio"
        assert cfg.machine_type == "pc-q35-6.2"
        assert cfg.efi_boot is False
        assert cfg.efi_vars == ""
        assert cfg.serial_type == "pty"
        assert cfg.nographic is False
        assert cfg.serial_port_count == 1
        assert cfg.force_stop is True
        assert cfg.reserved_nics == 0
        assert cfg.cpu_sockets == 0
        assert cfg.needs_nested_vmx is False
        assert cfg.cpu_features_disable == []
        assert cfg.requires_image is True
        assert cfg.supported_image_kinds == ["docker"]
        assert cfg.readiness_probe == "none"
        assert cfg.readiness_pattern is None
        assert cfg.readiness_timeout == 120
        assert cfg.console_method == "docker_exec"
        assert cfg.console_user == "admin"
        assert cfg.console_password == "admin"
        assert cfg.config_extract_method == "none"
        assert cfg.config_inject_method == "none"
        assert cfg.environment == {}
        assert cfg.capabilities == ["NET_ADMIN"]
        assert cfg.privileged is False
        assert cfg.network_mode == "none"
        assert cfg.hostname_template == "{node}"
        assert cfg.post_boot_commands == []


class TestVendorConfigSubConfigs:
    """Tests for VendorConfig cached_property sub-config accessors."""

    @pytest.fixture
    def cfg(self):
        return VendorConfig(
            kind="ceos",
            vendor="Arista",
            console_shell="Cli",
            default_image="ceos:latest",
            port_naming="Ethernet",
            port_start_index=1,
            max_ports=64,
            management_interface="Management0",
            memory=4096,
            cpu=2,
            disk_driver="virtio",
            nic_driver="e1000",
            machine_type="pc-q35-6.2",
            data_volume_gb=10,
            efi_boot=True,
            efi_vars="stateless",
            serial_type="tcp",
            nographic=True,
            serial_port_count=2,
            smbios_product="Test Product",
            force_stop=False,
            reserved_nics=2,
            cpu_sockets=2,
            needs_nested_vmx=True,
            cpu_features_disable=["smep", "smap"],
            console_method="ssh",
            console_user="operator",
            console_password="pass123",
            default_credentials="operator / pass123",
            readiness_probe="log_pattern",
            readiness_pattern=r"System ready",
            readiness_timeout=300,
            config_extract_method="serial",
            config_extract_command="show run",
            config_extract_user="admin",
            config_extract_password="cisco",
            config_extract_enable_password="enable",
            config_extract_timeout=60,
            config_extract_prompt_pattern=r"#$",
            config_extract_paging_disable="terminal length 0",
            config_inject_method="bootflash",
            config_inject_partition=1,
            config_inject_fs_type="ext4",
            config_inject_path="/boot/config",
            config_inject_iso_volume_label="config",
            config_inject_iso_filename="startup.cfg",
            environment={"INTFTYPE": "eth"},
            capabilities=["NET_ADMIN", "SYS_ADMIN"],
            privileged=True,
            binds=["/flash:/mnt/flash"],
            entrypoint="/sbin/init",
            cmd=["--debug"],
            network_mode="bridge",
            sysctls={"net.ipv4.ip_forward": "1"},
            runtime="runsc",
            hostname_template="{node}-host",
            post_boot_commands=["iptables -F"],
            icon="fa-router",
            versions=["4.28", "4.29"],
            is_active=True,
            requires_image=True,
            supported_image_kinds=["docker", "qcow2"],
            documentation_url="https://docs.example.com",
            license_required=True,
            tags=["bgp", "mpls"],
        )

    def test_interfaces_accessor(self, cfg):
        iface = cfg.interfaces
        assert isinstance(iface, InterfaceConfig)
        assert iface.port_naming == "Ethernet"
        assert iface.port_start_index == 1
        assert iface.max_ports == 64
        assert iface.management_interface == "Management0"

    def test_resources_accessor(self, cfg):
        res = cfg.resources
        assert isinstance(res, ResourceConfig)
        assert res.memory == 4096
        assert res.cpu == 2

    def test_vm_accessor(self, cfg):
        vm = cfg.vm
        assert isinstance(vm, VMConfig)
        assert vm.disk_driver == "virtio"
        assert vm.nic_driver == "e1000"
        assert vm.efi_boot is True
        assert vm.efi_vars == "stateless"
        assert vm.serial_type == "tcp"
        assert vm.nographic is True
        assert vm.serial_port_count == 2
        assert vm.smbios_product == "Test Product"
        assert vm.force_stop is False
        assert vm.reserved_nics == 2
        assert vm.cpu_sockets == 2
        assert vm.needs_nested_vmx is True
        # cpu_features_disable should be converted to tuple
        assert vm.cpu_features_disable == ("smep", "smap")
        assert isinstance(vm.cpu_features_disable, tuple)

    def test_console_accessor(self, cfg):
        con = cfg.console
        assert isinstance(con, ConsoleConfig)
        assert con.console_method == "ssh"
        assert con.console_shell == "Cli"
        assert con.console_user == "operator"
        assert con.console_password == "pass123"
        assert con.default_credentials == "operator / pass123"

    def test_readiness_accessor(self, cfg):
        rd = cfg.readiness
        assert isinstance(rd, ReadinessConfig)
        assert rd.readiness_probe == "log_pattern"
        assert rd.readiness_pattern == r"System ready"
        assert rd.readiness_timeout == 300

    def test_config_extraction_accessor(self, cfg):
        ce = cfg.config_extraction
        assert isinstance(ce, ConfigExtractionConfig)
        assert ce.config_extract_method == "serial"
        assert ce.config_extract_command == "show run"
        assert ce.config_extract_user == "admin"
        assert ce.config_extract_timeout == 60

    def test_config_injection_accessor(self, cfg):
        ci = cfg.config_injection
        assert isinstance(ci, ConfigInjectionConfig)
        assert ci.config_inject_method == "bootflash"
        assert ci.config_inject_partition == 1
        assert ci.config_inject_fs_type == "ext4"
        assert ci.config_inject_iso_volume_label == "config"

    def test_container_accessor(self, cfg):
        ctr = cfg.container
        assert isinstance(ctr, ContainerConfig)
        assert ctr.environment == {"INTFTYPE": "eth"}
        assert "NET_ADMIN" in ctr.capabilities
        assert ctr.privileged is True
        assert ctr.entrypoint == "/sbin/init"
        assert ctr.cmd == ["--debug"]
        assert ctr.network_mode == "bridge"
        assert ctr.runtime == "runsc"
        assert ctr.hostname_template == "{node}-host"
        assert ctr.post_boot_commands == ["iptables -F"]

    def test_ui_accessor(self, cfg):
        ui = cfg.ui
        assert isinstance(ui, UIConfig)
        assert ui.icon == "fa-router"
        assert ui.versions == ["4.28", "4.29"]
        assert ui.is_active is True
        assert ui.requires_image is True
        assert ui.supported_image_kinds == ["docker", "qcow2"]
        assert ui.documentation_url == "https://docs.example.com"
        assert ui.license_required is True
        assert ui.tags == ["bgp", "mpls"]

    def test_cached_property_returns_same_instance(self, cfg):
        """Sub-config accessors use cached_property so they return the same object."""
        assert cfg.interfaces is cfg.interfaces
        assert cfg.vm is cfg.vm
        assert cfg.console is cfg.console
        assert cfg.readiness is cfg.readiness
        assert cfg.ui is cfg.ui

    def test_vendor_config_is_mutable(self):
        """VendorConfig itself is not frozen (unlike sub-configs)."""
        cfg = VendorConfig(kind="test", vendor="V", console_shell="sh", default_image=None)
        cfg.kind = "changed"
        assert cfg.kind == "changed"
