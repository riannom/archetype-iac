"""Data validation tests for agent/vendor_registry.py.

Validates the integrity of every VENDOR_CONFIGS entry — required fields,
enum values, regex patterns, resource specifications, interface naming
consistency, and cross-entry uniqueness constraints.

This is a DATA VALIDATION suite, not a runtime behavior suite.  It catches
typos, missing fields, and structural inconsistencies in the 1,600-line
registry blob before they surface as production bugs.
"""

from __future__ import annotations

import re

import pytest

from agent.vendor_registry import VENDOR_CONFIGS
from agent.vendor_schema import DeviceType, VendorConfig


# Valid values for string-enum fields (drawn from VendorConfig docstrings
# and the consumer modules that interpret these values).
_VALID_CONSOLE_METHODS = {"docker_exec", "ssh", "virsh"}
_VALID_READINESS_PROBES = {"none", "log_pattern", "cli_probe"}
_VALID_CONFIG_EXTRACT_METHODS = {"none", "docker", "serial", "ssh", "nvram"}
_VALID_CONFIG_INJECT_METHODS = {"none", "bootflash", "iso", "config_disk"}
_VALID_IMAGE_KINDS = {"docker", "qcow2", "iol"}
_VALID_DISK_DRIVERS = {"virtio", "ide", "sata"}
_VALID_NIC_DRIVERS = {"virtio", "e1000", "rtl8139"}
_VALID_SERIAL_TYPES = {"pty", "tcp"}
_VALID_CATEGORIES = {"Network", "Security", "Compute", "Cloud & External"}


# =============================================================================
# Required fields and type checks
# =============================================================================


class TestRequiredFields:
    """Every VENDOR_CONFIGS entry must have all mandatory fields populated."""

    @pytest.fixture(params=list(VENDOR_CONFIGS.keys()))
    def entry(self, request):
        """Parametrize over every registry key."""
        key = request.param
        return key, VENDOR_CONFIGS[key]

    def test_kind_is_nonempty_string(self, entry):
        key, cfg = entry
        assert isinstance(cfg.kind, str) and cfg.kind, f"{key}: kind is empty"

    def test_vendor_is_nonempty_string(self, entry):
        key, cfg = entry
        assert isinstance(cfg.vendor, str) and cfg.vendor, f"{key}: vendor is empty"

    def test_label_is_nonempty_string(self, entry):
        key, cfg = entry
        assert isinstance(cfg.label, str) and cfg.label, f"{key}: label is empty"

    def test_console_shell_is_nonempty_string(self, entry):
        key, cfg = entry
        assert isinstance(cfg.console_shell, str) and cfg.console_shell, (
            f"{key}: console_shell is empty"
        )

    def test_device_type_is_enum(self, entry):
        key, cfg = entry
        assert isinstance(cfg.device_type, DeviceType), (
            f"{key}: device_type={cfg.device_type!r} is not a DeviceType"
        )

    def test_category_is_valid(self, entry):
        key, cfg = entry
        assert cfg.category in _VALID_CATEGORIES, (
            f"{key}: category={cfg.category!r} not in {_VALID_CATEGORIES}"
        )


# =============================================================================
# No duplicate device_type IDs (keys)
# =============================================================================


class TestNoDuplicates:
    """Registry keys must be unique and consistent with the entries they hold."""

    def test_no_duplicate_keys(self):
        """Dict keys are inherently unique, but verify count matches expectations."""
        keys = list(VENDOR_CONFIGS.keys())
        assert len(keys) == len(set(keys))

    def test_at_least_20_entries(self):
        """Sanity guard: the registry should never shrink below a reasonable size."""
        assert len(VENDOR_CONFIGS) >= 20

    def test_no_duplicate_aliases_across_entries(self):
        """An alias must not resolve to two different registry keys."""
        seen: dict[str, str] = {}
        for key, cfg in VENDOR_CONFIGS.items():
            for alias in cfg.aliases:
                norm = alias.lower()
                if norm in seen:
                    assert seen[norm] == key, (
                        f"Alias '{alias}' claimed by both '{seen[norm]}' and '{key}'"
                    )
                seen[norm] = key

    def test_aliases_do_not_collide_with_keys(self):
        """No alias should shadow an existing registry key belonging to a different entry."""
        for key, cfg in VENDOR_CONFIGS.items():
            for alias in cfg.aliases:
                norm = alias.lower()
                if norm in VENDOR_CONFIGS and norm != key:
                    # The alias collides with a different top-level key
                    pytest.fail(
                        f"{key}: alias '{alias}' collides with registry key '{norm}'"
                    )


# =============================================================================
# Port naming / interface configuration
# =============================================================================


class TestPortNamingConsistency:
    """Interface configuration fields must be self-consistent."""

    @pytest.fixture(params=list(VENDOR_CONFIGS.keys()))
    def entry(self, request):
        key = request.param
        return key, VENDOR_CONFIGS[key]

    def test_port_start_index_is_non_negative(self, entry):
        key, cfg = entry
        assert cfg.port_start_index >= 0, (
            f"{key}: port_start_index={cfg.port_start_index} is negative"
        )

    def test_max_ports_is_positive(self, entry):
        key, cfg = entry
        assert cfg.max_ports > 0, f"{key}: max_ports={cfg.max_ports} must be > 0"

    def test_port_naming_is_nonempty(self, entry):
        key, cfg = entry
        assert isinstance(cfg.port_naming, str) and cfg.port_naming, (
            f"{key}: port_naming is empty"
        )

    def test_management_interface_is_string_or_none(self, entry):
        key, cfg = entry
        assert cfg.management_interface is None or isinstance(cfg.management_interface, str), (
            f"{key}: management_interface has unexpected type {type(cfg.management_interface)}"
        )


# =============================================================================
# Console and credential specifications
# =============================================================================


class TestConsoleSpecifications:
    """Console method and credential fields use valid values."""

    @pytest.fixture(params=list(VENDOR_CONFIGS.keys()))
    def entry(self, request):
        key = request.param
        return key, VENDOR_CONFIGS[key]

    def test_console_method_is_valid(self, entry):
        key, cfg = entry
        assert cfg.console_method in _VALID_CONSOLE_METHODS, (
            f"{key}: console_method={cfg.console_method!r} not in {_VALID_CONSOLE_METHODS}"
        )

    def test_console_user_is_string(self, entry):
        key, cfg = entry
        assert isinstance(cfg.console_user, str), (
            f"{key}: console_user is not a string"
        )

    def test_console_password_is_string(self, entry):
        key, cfg = entry
        assert isinstance(cfg.console_password, str), (
            f"{key}: console_password is not a string"
        )


# =============================================================================
# Readiness probe validation
# =============================================================================


class TestReadinessProbes:
    """Readiness probe configurations must be internally consistent."""

    @pytest.fixture(params=list(VENDOR_CONFIGS.keys()))
    def entry(self, request):
        key = request.param
        return key, VENDOR_CONFIGS[key]

    def test_readiness_probe_is_valid(self, entry):
        key, cfg = entry
        assert cfg.readiness_probe in _VALID_READINESS_PROBES, (
            f"{key}: readiness_probe={cfg.readiness_probe!r} not in {_VALID_READINESS_PROBES}"
        )

    def test_log_pattern_probe_has_pattern(self, entry):
        """If readiness_probe is log_pattern, readiness_pattern must be set."""
        key, cfg = entry
        if cfg.readiness_probe == "log_pattern":
            assert cfg.readiness_pattern, (
                f"{key}: readiness_probe='log_pattern' but readiness_pattern is empty/None"
            )

    def test_readiness_pattern_compiles_as_regex(self, entry):
        """readiness_pattern, if set, must be a valid regex."""
        key, cfg = entry
        if cfg.readiness_pattern:
            try:
                re.compile(cfg.readiness_pattern)
            except re.error as exc:
                pytest.fail(f"{key}: readiness_pattern is invalid regex: {exc}")

    def test_readiness_timeout_is_positive(self, entry):
        key, cfg = entry
        assert cfg.readiness_timeout > 0, (
            f"{key}: readiness_timeout={cfg.readiness_timeout} must be > 0"
        )


# =============================================================================
# Image kind validation
# =============================================================================


class TestImageKindValues:
    """supported_image_kinds entries must be recognized values."""

    @pytest.fixture(params=list(VENDOR_CONFIGS.keys()))
    def entry(self, request):
        key = request.param
        return key, VENDOR_CONFIGS[key]

    def test_supported_image_kinds_are_valid(self, entry):
        key, cfg = entry
        for kind in cfg.supported_image_kinds:
            assert kind in _VALID_IMAGE_KINDS, (
                f"{key}: supported_image_kinds contains unknown '{kind}'"
            )

    def test_supported_image_kinds_is_nonempty(self, entry):
        key, cfg = entry
        assert len(cfg.supported_image_kinds) > 0, (
            f"{key}: supported_image_kinds is empty"
        )


# =============================================================================
# VM entries — resource requirements
# =============================================================================


class TestVMResourceRequirements:
    """Devices supporting qcow2 images must have sensible VM resource specs."""

    @pytest.fixture(
        params=[
            (k, v) for k, v in VENDOR_CONFIGS.items()
            if "qcow2" in v.supported_image_kinds
        ],
        ids=lambda kv: kv[0],
    )
    def vm_entry(self, request):
        return request.param

    def test_memory_is_at_least_256mb(self, vm_entry):
        key, cfg = vm_entry
        assert cfg.memory >= 256, (
            f"{key}: VM memory={cfg.memory}MB is below 256MB minimum"
        )

    def test_cpu_is_at_least_1(self, vm_entry):
        key, cfg = vm_entry
        assert cfg.cpu >= 1, f"{key}: VM cpu={cfg.cpu} must be >= 1"

    def test_disk_driver_is_valid(self, vm_entry):
        key, cfg = vm_entry
        assert cfg.disk_driver in _VALID_DISK_DRIVERS, (
            f"{key}: disk_driver={cfg.disk_driver!r} not in {_VALID_DISK_DRIVERS}"
        )

    def test_nic_driver_is_valid(self, vm_entry):
        key, cfg = vm_entry
        assert cfg.nic_driver in _VALID_NIC_DRIVERS, (
            f"{key}: nic_driver={cfg.nic_driver!r} not in {_VALID_NIC_DRIVERS}"
        )

    def test_serial_type_is_valid(self, vm_entry):
        key, cfg = vm_entry
        assert cfg.serial_type in _VALID_SERIAL_TYPES, (
            f"{key}: serial_type={cfg.serial_type!r} not in {_VALID_SERIAL_TYPES}"
        )

    def test_serial_port_count_is_positive(self, vm_entry):
        key, cfg = vm_entry
        assert cfg.serial_port_count >= 1, (
            f"{key}: serial_port_count={cfg.serial_port_count} must be >= 1"
        )


# =============================================================================
# Config extraction / injection
# =============================================================================


class TestConfigExtractionInjection:
    """Config extraction and injection method fields use recognized values."""

    @pytest.fixture(params=list(VENDOR_CONFIGS.keys()))
    def entry(self, request):
        key = request.param
        return key, VENDOR_CONFIGS[key]

    def test_config_extract_method_is_valid(self, entry):
        key, cfg = entry
        assert cfg.config_extract_method in _VALID_CONFIG_EXTRACT_METHODS, (
            f"{key}: config_extract_method={cfg.config_extract_method!r} "
            f"not in {_VALID_CONFIG_EXTRACT_METHODS}"
        )

    def test_config_inject_method_is_valid(self, entry):
        key, cfg = entry
        assert cfg.config_inject_method in _VALID_CONFIG_INJECT_METHODS, (
            f"{key}: config_inject_method={cfg.config_inject_method!r} "
            f"not in {_VALID_CONFIG_INJECT_METHODS}"
        )

    def test_serial_extraction_has_credentials(self, entry):
        """Serial extraction requires user and password for console login."""
        key, cfg = entry
        if cfg.config_extract_method == "serial" and cfg.config_extract_user:
            assert cfg.config_extract_password, (
                f"{key}: serial extraction has user but no password"
            )

    def test_extraction_prompt_pattern_compiles(self, entry):
        """config_extract_prompt_pattern must be a valid regex."""
        key, cfg = entry
        if cfg.config_extract_prompt_pattern:
            try:
                re.compile(cfg.config_extract_prompt_pattern)
            except re.error as exc:
                pytest.fail(
                    f"{key}: config_extract_prompt_pattern is invalid regex: {exc}"
                )

    def test_iso_injection_has_filename(self, entry):
        """ISO injection requires a filename for the config inside the ISO."""
        key, cfg = entry
        if cfg.config_inject_method == "iso":
            assert cfg.config_inject_iso_filename, (
                f"{key}: config_inject_method='iso' but config_inject_iso_filename is empty"
            )


# =============================================================================
# Environment variable specifications
# =============================================================================


class TestEnvironmentVariables:
    """Container environment variables must be dict[str, str]."""

    @pytest.fixture(params=list(VENDOR_CONFIGS.keys()))
    def entry(self, request):
        key = request.param
        return key, VENDOR_CONFIGS[key]

    def test_environment_is_dict(self, entry):
        key, cfg = entry
        assert isinstance(cfg.environment, dict), (
            f"{key}: environment is not a dict"
        )

    def test_environment_keys_are_strings(self, entry):
        key, cfg = entry
        for env_key in cfg.environment:
            assert isinstance(env_key, str) and env_key, (
                f"{key}: environment key {env_key!r} is not a non-empty string"
            )

    def test_environment_values_are_strings(self, entry):
        key, cfg = entry
        for env_key, env_val in cfg.environment.items():
            assert isinstance(env_val, str), (
                f"{key}: environment[{env_key!r}]={env_val!r} is not a string"
            )

    def test_capabilities_are_strings(self, entry):
        key, cfg = entry
        for cap in cfg.capabilities:
            assert isinstance(cap, str) and cap, (
                f"{key}: capability {cap!r} is not a non-empty string"
            )

    def test_sysctls_keys_and_values_are_strings(self, entry):
        key, cfg = entry
        for sk, sv in cfg.sysctls.items():
            assert isinstance(sk, str) and sk, (
                f"{key}: sysctl key {sk!r} is not a non-empty string"
            )
            assert isinstance(sv, str), (
                f"{key}: sysctl[{sk!r}]={sv!r} is not a string"
            )


# =============================================================================
# Filename patterns (regex validation for image detection)
# =============================================================================


class TestFilenamePatterns:
    """Filename patterns used for image detection must be valid regexes."""

    @pytest.fixture(
        params=[
            (k, v) for k, v in VENDOR_CONFIGS.items()
            if v.filename_patterns
        ],
        ids=lambda kv: kv[0],
    )
    def entry_with_patterns(self, request):
        return request.param

    def test_filename_patterns_compile(self, entry_with_patterns):
        key, cfg = entry_with_patterns
        for pattern in cfg.filename_patterns:
            try:
                re.compile(pattern)
            except re.error as exc:
                pytest.fail(
                    f"{key}: filename_pattern '{pattern}' is invalid regex: {exc}"
                )

    def test_filename_patterns_are_nonempty_strings(self, entry_with_patterns):
        key, cfg = entry_with_patterns
        for pattern in cfg.filename_patterns:
            assert isinstance(pattern, str) and pattern, (
                f"{key}: filename_pattern contains empty/non-string entry"
            )


# =============================================================================
# Known device alias resolution
# =============================================================================


class TestKnownAliasResolution:
    """Well-known aliases must resolve to the expected registry key."""

    _EXPECTED_ALIASES = {
        # cEOS aliases
        "eos": "ceos",
        "arista_eos": "ceos",
        "arista_ceos": "ceos",
        # Cisco NX-OSv aliases
        "nxos": "cisco_n9kv",
        "n9kv": "cisco_n9kv",
        "nxosv9000": "cisco_n9kv",
        # IOSv alias
        "iosv": "cisco_iosv",
        # IOS-XR aliases
        "iosxr": "cisco_iosxr",
        "xrv9k": "cisco_iosxr",
        "iosxrv9000": "cisco_iosxr",
        # CSR aliases
        "csr": "cisco_csr1000v",
        "csr1000v": "cisco_csr1000v",
        # vJunos aliases
        "vjunos-router": "juniper_vjunosrouter",
        "vjunos-switch": "juniper_vjunosswitch",
        "vjunos": "juniper_vjunosswitch",
        # cJunos aliases
        "cjunos": "juniper_cjunos",
        "cjunos-evolved": "juniper_cjunos",
        # C8000v aliases
        "cat8000v": "c8000v",
        "cat-sdwan-edge": "c8000v",
        # IOL aliases
        "iol": "iol-xe",
    }

    @pytest.fixture(params=list(_EXPECTED_ALIASES.items()), ids=lambda kv: kv[0])
    def alias_pair(self, request):
        return request.param

    def test_alias_resolves_to_expected_key(self, alias_pair):
        alias, expected_key = alias_pair
        # Build alias->key map from registry
        alias_map: dict[str, str] = {}
        for key, cfg in VENDOR_CONFIGS.items():
            for a in cfg.aliases:
                alias_map[a.lower()] = key
        assert alias.lower() in alias_map, (
            f"Alias '{alias}' not found in any VENDOR_CONFIGS entry"
        )
        assert alias_map[alias.lower()] == expected_key, (
            f"Alias '{alias}' resolves to '{alias_map[alias.lower()]}', expected '{expected_key}'"
        )


# =============================================================================
# Interface naming conventions per vendor family
# =============================================================================


class TestInterfaceNamingByVendorFamily:
    """Verify vendor-family interface naming conventions are consistent."""

    def _get_configs_by_vendor(self, vendor: str) -> list[tuple[str, VendorConfig]]:
        return [(k, v) for k, v in VENDOR_CONFIGS.items() if v.vendor == vendor]

    def test_juniper_devices_use_consistent_port_naming(self):
        """Juniper devices should use ge-0/0/ or et-0/0/ style naming."""
        juniper_patterns = {"ge-0/0/", "et-0/0/", "eth"}  # eth for crpd
        for key, cfg in self._get_configs_by_vendor("Juniper"):
            assert cfg.port_naming in juniper_patterns, (
                f"{key}: Juniper device uses unexpected port_naming={cfg.port_naming!r}"
            )

    def test_cisco_ios_devices_use_gigabitethernet_or_eth(self):
        """Cisco IOS/IOS-XE devices should use GigabitEthernet-style naming."""
        cisco_entries = self._get_configs_by_vendor("Cisco")
        for key, cfg in cisco_entries:
            if "ios" in key.lower() or "csr" in key.lower():
                assert "Gigabit" in cfg.port_naming or "Ethernet" in cfg.port_naming, (
                    f"{key}: Cisco IOS device uses unexpected port_naming={cfg.port_naming!r}"
                )

    def test_linux_style_devices_use_eth(self):
        """Generic linux/compute devices should use 'eth' port naming."""
        for key, cfg in VENDOR_CONFIGS.items():
            if cfg.kind == "linux" and cfg.device_type == DeviceType.HOST:
                assert cfg.port_naming == "eth", (
                    f"{key}: Linux host device uses unexpected port_naming={cfg.port_naming!r}"
                )


# =============================================================================
# Structural completeness of VendorConfig instances
# =============================================================================


class TestStructuralCompleteness:
    """Verify that VendorConfig instances are valid dataclass instances."""

    def test_all_values_are_vendor_config_instances(self):
        for key, cfg in VENDOR_CONFIGS.items():
            assert isinstance(cfg, VendorConfig), (
                f"{key}: value is {type(cfg).__name__}, expected VendorConfig"
            )

    def test_aliases_are_lists_of_strings(self):
        for key, cfg in VENDOR_CONFIGS.items():
            assert isinstance(cfg.aliases, list), (
                f"{key}: aliases is not a list"
            )
            for alias in cfg.aliases:
                assert isinstance(alias, str), (
                    f"{key}: alias {alias!r} is not a string"
                )

    def test_tags_are_lists_of_strings(self):
        for key, cfg in VENDOR_CONFIGS.items():
            assert isinstance(cfg.tags, list), f"{key}: tags is not a list"
            for tag in cfg.tags:
                assert isinstance(tag, str), (
                    f"{key}: tag {tag!r} is not a string"
                )

    def test_binds_are_lists_of_strings(self):
        for key, cfg in VENDOR_CONFIGS.items():
            assert isinstance(cfg.binds, list), f"{key}: binds is not a list"
            for bind in cfg.binds:
                assert isinstance(bind, str), (
                    f"{key}: bind {bind!r} is not a string"
                )

    def test_post_boot_commands_are_lists_of_strings(self):
        for key, cfg in VENDOR_CONFIGS.items():
            assert isinstance(cfg.post_boot_commands, list), (
                f"{key}: post_boot_commands is not a list"
            )
            for cmd in cfg.post_boot_commands:
                assert isinstance(cmd, str), (
                    f"{key}: post_boot_command {cmd!r} is not a string"
                )

    def test_default_startup_config_hostname_placeholder(self):
        """default_startup_config containing '{hostname}' must be format-safe."""
        for key, cfg in VENDOR_CONFIGS.items():
            if cfg.default_startup_config and "{hostname}" in cfg.default_startup_config:
                try:
                    cfg.default_startup_config.format(hostname="test-node")
                except (KeyError, IndexError) as exc:
                    pytest.fail(
                        f"{key}: default_startup_config has broken format string: {exc}"
                    )
