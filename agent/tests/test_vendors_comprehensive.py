"""Comprehensive tests for agent/vendors.py.

Covers VendorConfig dataclass sub-configs, VENDOR_CONFIGS registry
integrity, helper functions (get_console_shell, get_console_method,
get_console_credentials, is_ceos_kind, is_cjunos_kind, etc.),
container/libvirt config builders, and config extraction settings.
"""

from __future__ import annotations

import pytest

from agent.vendors import (
    VENDOR_CONFIGS,
    VendorConfig,
    DeviceType,
    InterfaceConfig,
    VMConfig,
    ConsoleConfig,
    ReadinessConfig,
    UIConfig,
    ContainerRuntimeConfig,
    ConfigExtractionSettings,
    build_device_id_aliases,
    build_device_vendor_map,
    build_filename_keyword_map,
    build_qcow2_device_patterns,
    get_console_shell,
    get_console_method,
    get_console_credentials,
    get_default_image,
    get_vendor_config,
    get_kind_for_device,
    get_container_config,
    get_libvirt_config,
    get_config_extraction_settings,
    get_config_by_device,
    get_vendors_for_ui,
    is_ceos_kind,
    is_cjunos_kind,
    list_supported_kinds,
    get_all_vendors,
)


# =============================================================================
# VendorConfig dataclass and cached sub-config properties
# =============================================================================


class TestVendorConfigDataclass:
    """VendorConfig cached_property accessors return correct sub-configs."""

    def test_interfaces_returns_interface_config(self):
        cfg = VENDOR_CONFIGS["ceos"]
        ifc = cfg.interfaces
        assert isinstance(ifc, InterfaceConfig)
        assert ifc.port_naming == cfg.port_naming
        assert ifc.port_start_index == cfg.port_start_index
        assert ifc.max_ports == cfg.max_ports
        assert ifc.management_interface == cfg.management_interface

    def test_vm_returns_vm_config(self):
        cfg = VENDOR_CONFIGS["cisco_n9kv"]
        vm = cfg.vm
        assert isinstance(vm, VMConfig)
        assert vm.nic_driver == "e1000"
        assert vm.efi_boot is True
        assert vm.serial_port_count == 2

    def test_console_returns_console_config(self):
        cfg = VENDOR_CONFIGS["ceos"]
        con = cfg.console
        assert isinstance(con, ConsoleConfig)
        assert con.console_shell == "FastCli"
        assert con.console_method == "docker_exec"

    def test_readiness_returns_readiness_config(self):
        cfg = VENDOR_CONFIGS["cisco_iosv"]
        rd = cfg.readiness
        assert isinstance(rd, ReadinessConfig)
        assert rd.readiness_probe == "log_pattern"
        assert rd.readiness_timeout == 180

    def test_ui_returns_ui_config(self):
        cfg = VENDOR_CONFIGS["linux"]
        ui = cfg.ui
        assert isinstance(ui, UIConfig)
        assert ui.is_active is True
        assert ui.requires_image is False

    def test_cached_property_returns_same_object(self):
        """Cached property should return the same object on repeated access."""
        cfg = VENDOR_CONFIGS["ceos"]
        assert cfg.interfaces is cfg.interfaces
        assert cfg.vm is cfg.vm

    def test_vm_cpu_features_disable_is_tuple(self):
        """VMConfig.cpu_features_disable should be a tuple (frozen)."""
        cfg = VENDOR_CONFIGS["cisco_n9kv"]
        vm = cfg.vm
        assert isinstance(vm.cpu_features_disable, tuple)
        assert "smep" in vm.cpu_features_disable


# =============================================================================
# VENDOR_CONFIGS registry coverage
# =============================================================================


class TestVendorConfigsCoverage:
    """All VENDOR_CONFIGS entries have required fields and are consistent."""

    def test_all_entries_have_kind(self):
        for key, cfg in VENDOR_CONFIGS.items():
            assert cfg.kind, f"{key} has empty kind"

    def test_all_entries_have_label(self):
        for key, cfg in VENDOR_CONFIGS.items():
            assert cfg.label, f"{key} has empty label"

    def test_all_entries_have_vendor(self):
        for key, cfg in VENDOR_CONFIGS.items():
            assert cfg.vendor, f"{key} has empty vendor"

    def test_kind_values_are_strings(self):
        for key, cfg in VENDOR_CONFIGS.items():
            assert isinstance(cfg.kind, str), f"{key}.kind is not a string"

    def test_no_duplicate_aliases_across_configs(self):
        """No alias should map to two different config keys."""
        seen: dict[str, str] = {}
        for key, cfg in VENDOR_CONFIGS.items():
            for alias in cfg.aliases:
                alias_lower = alias.lower()
                if alias_lower in seen:
                    # Same alias in different config entries is a bug
                    assert seen[alias_lower] == key, (
                        f"Alias '{alias}' appears in both '{seen[alias_lower]}' and '{key}'"
                    )
                seen[alias_lower] = key

    def test_device_type_is_enum(self):
        for key, cfg in VENDOR_CONFIGS.items():
            assert isinstance(cfg.device_type, DeviceType), (
                f"{key}.device_type is not a DeviceType"
            )

    def test_at_least_20_vendors_defined(self):
        """Sanity check: the registry should have a reasonable number of entries."""
        assert len(VENDOR_CONFIGS) >= 20


# =============================================================================
# get_vendor_config
# =============================================================================


class TestGetVendorConfig:
    """Tests for get_vendor_config() direct key lookup."""

    def test_known_device_returns_config(self):
        cfg = get_vendor_config("ceos")
        assert cfg is not None
        assert cfg.kind == "ceos"

    def test_unknown_returns_none(self):
        assert get_vendor_config("totally_unknown_device_xyz") is None

    def test_returns_exact_key_match(self):
        cfg = get_vendor_config("cisco_n9kv")
        assert cfg is not None
        assert cfg.kind == "cisco_n9kv"


# =============================================================================
# get_console_shell
# =============================================================================


class TestGetConsoleShell:
    """Tests for get_console_shell()."""

    def test_ceos_returns_fastcli(self):
        assert get_console_shell("ceos") == "FastCli"

    def test_vyos_returns_vbash(self):
        assert get_console_shell("vyos") == "/bin/vbash"

    def test_srlinux_returns_sr_cli(self):
        assert get_console_shell("nokia_srlinux") == "sr_cli"

    def test_unknown_returns_default(self):
        assert get_console_shell("nonexistent_device_xyz") == "/bin/sh"

    def test_alias_resolution(self):
        """Console shell for alias 'eos' should resolve to cEOS."""
        assert get_console_shell("eos") == "FastCli"


# =============================================================================
# get_console_method
# =============================================================================


class TestGetConsoleMethod:
    """Tests for get_console_method()."""

    def test_container_device_returns_docker_exec(self):
        assert get_console_method("ceos") == "docker_exec"

    def test_vm_virsh_device(self):
        assert get_console_method("cisco_n9kv") == "virsh"

    def test_ssh_console_method(self):
        """cat9000v-q200 uses SSH console method."""
        assert get_console_method("cisco_cat9000v_q200") == "ssh"

    def test_unknown_returns_default(self):
        assert get_console_method("nonexistent_device_xyz") == "docker_exec"


# =============================================================================
# get_console_credentials
# =============================================================================


class TestGetConsoleCredentials:
    """Tests for get_console_credentials()."""

    def test_n9kv_credentials(self):
        user, pw = get_console_credentials("cisco_n9kv")
        assert user == "admin"
        assert pw == "cisco"

    def test_iosxr_credentials(self):
        user, pw = get_console_credentials("cisco_iosxr")
        assert user == "admin"
        assert pw == "cisco"

    def test_default_credentials_for_unknown(self):
        user, pw = get_console_credentials("unknown_device_xyz")
        assert user == "admin"
        assert pw == "admin"

    def test_ceos_default_admin(self):
        user, pw = get_console_credentials("ceos")
        assert user == "admin"
        assert pw == "admin"


# =============================================================================
# Device aliases and kind resolution
# =============================================================================


class TestDeviceAliases:
    """Tests for alias resolution and derived maps."""

    def test_build_device_id_aliases_returns_dict(self):
        aliases = build_device_id_aliases()
        assert isinstance(aliases, dict)
        assert len(aliases) > 0

    def test_build_device_id_aliases_includes_keys(self):
        aliases = build_device_id_aliases()
        for key in VENDOR_CONFIGS:
            assert key.lower() in aliases

    def test_build_device_vendor_map(self):
        vm = build_device_vendor_map()
        assert vm["ceos"] == "Arista"
        assert vm["cisco_n9kv"] == "Cisco"

    def test_is_ceos_kind_canonical(self):
        assert is_ceos_kind("ceos") is True

    def test_is_ceos_kind_alias(self):
        assert is_ceos_kind("eos") is True
        assert is_ceos_kind("arista_eos") is True
        assert is_ceos_kind("arista_ceos") is True

    def test_is_ceos_kind_false_for_other(self):
        assert is_ceos_kind("linux") is False

    def test_is_cjunos_kind_canonical(self):
        assert is_cjunos_kind("juniper_cjunos") is True

    def test_is_cjunos_kind_alias(self):
        assert is_cjunos_kind("cjunos") is True
        assert is_cjunos_kind("cjunos-evolved") is True

    def test_is_cjunos_kind_false_for_other(self):
        assert is_cjunos_kind("ceos") is False

    def test_get_kind_for_device_known_alias(self):
        assert get_kind_for_device("eos") == "ceos"

    def test_get_kind_for_device_unknown_returns_lowercase(self):
        assert get_kind_for_device("UnKnOwN") == "unknown"

    def test_get_kind_for_device_canonical(self):
        assert get_kind_for_device("ceos") == "ceos"

    def test_build_filename_keyword_map(self):
        kw = build_filename_keyword_map()
        assert isinstance(kw, dict)
        assert "ceos" in kw

    def test_build_qcow2_device_patterns(self):
        patterns = build_qcow2_device_patterns()
        assert isinstance(patterns, dict)
        assert len(patterns) > 0


# =============================================================================
# get_container_config
# =============================================================================


class TestGetContainerConfig:
    """Tests for get_container_config()."""

    def test_ceos_env_vars(self):
        cfg = get_container_config("ceos", "sw1", image="ceos:latest", workspace="/tmp/lab")
        assert isinstance(cfg, ContainerRuntimeConfig)
        assert cfg.environment.get("CEOS") == "1"
        assert cfg.environment.get("EOS_PLATFORM") == "ceoslab"
        assert cfg.privileged is True

    def test_linux_minimal_config(self):
        cfg = get_container_config("linux", "host1")
        assert isinstance(cfg, ContainerRuntimeConfig)
        assert cfg.privileged is False
        assert cfg.cmd == ["sleep", "infinity"]

    def test_bind_mount_placeholder_substitution(self):
        cfg = get_container_config("ceos", "sw1", image="ceos:latest", workspace="/w")
        # Check that {workspace} and {node} were substituted
        for bind in cfg.binds:
            assert "{workspace}" not in bind
            assert "{node}" not in bind
        # At least one bind should contain the workspace path
        bind_str = " ".join(cfg.binds)
        assert "/w" in bind_str
        assert "sw1" in bind_str

    def test_hostname_template_substitution(self):
        cfg = get_container_config("linux", "myhost")
        assert cfg.hostname == "myhost"

    def test_unknown_device_falls_back_to_linux(self):
        cfg = get_container_config("completely_unknown_xyz", "node1")
        assert isinstance(cfg, ContainerRuntimeConfig)

    def test_requires_image_raises_without_image(self):
        """Devices that require_image=True should raise if no image is provided."""
        with pytest.raises(ValueError, match="requires an image"):
            get_container_config("cisco_iosv", "rtr1")


# =============================================================================
# get_libvirt_config
# =============================================================================


class TestGetLibvirtConfig:
    """Tests for get_libvirt_config()."""

    def test_n9kv_nic_driver(self):
        lc = get_libvirt_config("cisco_n9kv")
        assert lc.nic_driver == "e1000"

    def test_default_virtio(self):
        """Default fallback should use virtio."""
        lc = get_libvirt_config("unknown_generic_device_xyz")
        assert lc.nic_driver == "virtio"
        assert lc.source == "fallback"

    def test_efi_propagated(self):
        lc = get_libvirt_config("cisco_n9kv")
        assert lc.efi_boot is True
        assert lc.efi_vars == "stateless"

    def test_fallback_defaults(self):
        lc = get_libvirt_config("unknown_generic_device_xyz")
        assert lc.memory_mb == 2048
        assert lc.cpu_count == 1
        assert lc.machine_type == "pc-q35-6.2"
        assert lc.efi_boot is False

    def test_vendor_source_tag(self):
        lc = get_libvirt_config("cisco_n9kv")
        assert lc.source == "vendor"

    def test_memory_intensive_device_raises(self):
        """Memory-intensive device patterns should refuse fallback defaults."""
        with pytest.raises(ValueError, match="No vendor/libvirt profile"):
            get_libvirt_config("cat9000v_custom_unknown")

    def test_iosv_uses_ide_and_e1000(self):
        lc = get_libvirt_config("cisco_iosv")
        assert lc.disk_driver == "ide"
        assert lc.nic_driver == "e1000"
        assert lc.machine_type == "pc-i440fx-6.2"


# =============================================================================
# get_config_extraction_settings
# =============================================================================


class TestGetConfigExtractionSettings:
    """Tests for get_config_extraction_settings()."""

    def test_ceos_docker_method(self):
        s = get_config_extraction_settings("ceos")
        assert isinstance(s, ConfigExtractionSettings)
        assert s.method == "docker"
        assert "FastCli" in s.command

    def test_srlinux_docker_method(self):
        s = get_config_extraction_settings("nokia_srlinux")
        assert s.method == "docker"
        assert "sr_cli" in s.command

    def test_n9kv_serial_method(self):
        s = get_config_extraction_settings("cisco_n9kv")
        assert s.method == "serial"
        assert s.user == "admin"
        assert s.password == "cisco"

    def test_unknown_returns_none_method(self):
        s = get_config_extraction_settings("completely_unknown_xyz")
        assert s.method == "none"
        assert s.command == ""

    def test_iosxr_serial_method(self):
        s = get_config_extraction_settings("cisco_iosxr")
        assert s.method == "serial"
        assert s.user == "admin"
        assert s.password == "cisco"

    def test_vyos_docker_method(self):
        s = get_config_extraction_settings("vyos")
        assert s.method == "docker"


# =============================================================================
# Miscellaneous helper functions
# =============================================================================


class TestMiscHelpers:
    """Tests for list_supported_kinds, get_all_vendors, get_config_by_device, get_default_image."""

    def test_list_supported_kinds(self):
        kinds = list_supported_kinds()
        assert "ceos" in kinds
        assert "linux" in kinds
        assert len(kinds) == len(VENDOR_CONFIGS)

    def test_get_all_vendors(self):
        vendors = get_all_vendors()
        assert len(vendors) == len(VENDOR_CONFIGS)
        assert all(isinstance(v, VendorConfig) for v in vendors)

    def test_get_config_by_device_direct_key(self):
        cfg = get_config_by_device("ceos")
        assert cfg is not None
        assert cfg.kind == "ceos"

    def test_get_config_by_device_alias(self):
        cfg = get_config_by_device("eos")
        assert cfg is not None
        assert cfg.kind == "ceos"

    def test_get_config_by_device_unknown_returns_none(self):
        assert get_config_by_device("totally_unknown_xyz") is None

    def test_get_default_image_vyos(self):
        assert get_default_image("vyos") == "vyos/vyos:1.4-rolling"

    def test_get_default_image_none_for_iosv(self):
        assert get_default_image("cisco_iosv") is None

    def test_get_default_image_unknown(self):
        assert get_default_image("unknown_xyz") is None


# =============================================================================
# get_vendors_for_ui — subcategory completeness and device visibility
# =============================================================================


class TestGetVendorsForUI:
    """Tests for get_vendors_for_ui function."""

    def test_all_vendor_configs_appear_in_output(self):
        """Every device in VENDOR_CONFIGS must appear in the UI output."""
        result = get_vendors_for_ui()
        ui_ids = set()
        for cat in result:
            for dev in cat.get("models", []):
                ui_ids.add(dev["id"])
            for sub in cat.get("subCategories", []):
                for dev in sub.get("models", []):
                    ui_ids.add(dev["id"])
        missing = set(VENDOR_CONFIGS.keys()) - ui_ids
        assert not missing, f"Devices missing from UI output: {missing}"

    def test_wireless_subcategory_present(self):
        """The Wireless subcategory must appear under Network."""
        result = get_vendors_for_ui()
        network = next((c for c in result if c["name"] == "Network"), None)
        assert network is not None
        subcat_names = [s["name"] for s in network.get("subCategories", [])]
        assert "Wireless" in subcat_names

    def test_cat9800_in_wireless_subcategory(self):
        """cat9800 must appear in the Wireless subcategory."""
        result = get_vendors_for_ui()
        network = next((c for c in result if c["name"] == "Network"), None)
        wireless = next(
            (s for s in network.get("subCategories", []) if s["name"] == "Wireless"),
            None,
        )
        assert wireless is not None
        ids = [m["id"] for m in wireless["models"]]
        assert "cat9800" in ids

    def test_no_empty_subcategories_in_output(self):
        """Output should not contain empty subcategories."""
        result = get_vendors_for_ui()
        for cat in result:
            for sub in cat.get("subCategories", []):
                assert sub.get("models"), (
                    f"Empty subcategory '{sub['name']}' in '{cat['name']}'"
                )

    def test_sonic_vs_reports_qcow2_support(self):
        """SONiC should remain visible when the canvas filters for runnable images."""
        result = get_vendors_for_ui()
        sonic = None
        for cat in result:
            for model in cat.get("models", []):
                if model["id"] == "sonic-vs":
                    sonic = model
                    break
            if sonic:
                break
            for sub in cat.get("subCategories", []):
                for model in sub.get("models", []):
                    if model["id"] == "sonic-vs":
                        sonic = model
                        break
                if sonic:
                    break
            if sonic:
                break

        assert sonic is not None
        assert sonic["supportedImageKinds"] == ["qcow2"]


class TestLinuxDeviceSupportedImageKinds:
    """Tests for linux device image kind configuration."""

    def test_linux_accepts_docker_and_qcow2(self):
        """linux device must accept both docker and qcow2 images."""
        config = VENDOR_CONFIGS["linux"]
        assert "docker" in config.supported_image_kinds
        assert "qcow2" in config.supported_image_kinds

    def test_alpine_accepts_docker_and_qcow2(self):
        """alpine device must also accept both kinds."""
        config = VENDOR_CONFIGS["alpine"]
        assert "docker" in config.supported_image_kinds
        assert "qcow2" in config.supported_image_kinds
