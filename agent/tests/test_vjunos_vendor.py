"""Vendor config assertions for vJunOS device family."""

from __future__ import annotations

import pytest

from agent.vendors import (
    VENDOR_CONFIGS,
    get_config_extraction_settings,
    get_libvirt_config,
)

VJUNOS_KINDS = [
    "juniper_vjunosrouter",
    "juniper_vjunosevolved",
    "juniper_vjunosswitch",
]


class TestVJunOSExtractionSettings:
    """Verify config extraction settings for all vJunOS variants."""

    @pytest.mark.parametrize("kind", VJUNOS_KINDS)
    def test_extraction_method_is_serial(self, kind):
        settings = get_config_extraction_settings(kind)
        assert settings.method == "serial"

    @pytest.mark.parametrize("kind", VJUNOS_KINDS)
    def test_extraction_command(self, kind):
        settings = get_config_extraction_settings(kind)
        assert settings.command == "show configuration"

    @pytest.mark.parametrize("kind", VJUNOS_KINDS)
    def test_extraction_credentials(self, kind):
        settings = get_config_extraction_settings(kind)
        assert settings.user == "admin"
        assert settings.password == "admin@123"

    @pytest.mark.parametrize("kind", VJUNOS_KINDS)
    def test_extraction_paging_disable(self, kind):
        settings = get_config_extraction_settings(kind)
        assert settings.paging_disable == "set cli screen-length 0"

    @pytest.mark.parametrize("kind", VJUNOS_KINDS)
    def test_extraction_prompt_pattern(self, kind):
        settings = get_config_extraction_settings(kind)
        assert "@" in settings.prompt_pattern


class TestVJunOSInjectionSettings:
    """Verify config injection settings for all vJunOS variants."""

    @pytest.mark.parametrize("kind", VJUNOS_KINDS)
    def test_injection_method_is_config_disk(self, kind):
        libvirt_cfg = get_libvirt_config(kind)
        assert libvirt_cfg.config_inject_method == "config_disk"

    @pytest.mark.parametrize("kind", VJUNOS_KINDS)
    def test_vendor_config_has_config_disk(self, kind):
        cfg = VENDOR_CONFIGS[kind]
        assert cfg.config_inject_method == "config_disk"


class TestVJunOSVMSettings:
    """Verify VM-specific settings for vJunOS variants."""

    @pytest.mark.parametrize("kind", VJUNOS_KINDS)
    def test_needs_nested_vmx(self, kind):
        cfg = VENDOR_CONFIGS[kind]
        assert cfg.needs_nested_vmx is True

    @pytest.mark.parametrize("kind", VJUNOS_KINDS)
    def test_readiness_probe(self, kind):
        cfg = VENDOR_CONFIGS[kind]
        assert cfg.readiness_probe == "log_pattern"
        assert cfg.readiness_pattern == r"login:"
