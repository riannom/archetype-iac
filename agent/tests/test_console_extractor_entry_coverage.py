"""Tests for console_extractor.py entry-point functions.

Covers extract_vm_config, run_vm_post_boot_commands, and run_vm_cli_commands
with mocked dependencies (no real pexpect/libvirt/virsh needed).
"""

from __future__ import annotations

import sys
import os
import threading
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

# Ensure agent root is on sys.path
_AGENT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _AGENT_ROOT not in sys.path:
    sys.path.insert(0, _AGENT_ROOT)

from agent.console_extractor import (
    CommandCaptureResult,
    CommandResult,
    ExtractionResult,
    extract_vm_config,
    run_vm_cli_commands,
    run_vm_post_boot_commands,
    clear_vm_post_boot_cache,
    _vm_post_boot_completed,
    _vm_post_boot_lock,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_extraction_settings(
    method: str = "serial",
    command: str = "show running-config",
    user: str = "admin",
    password: str = "admin",
    enable_password: str = "",
    timeout: int = 30,
    prompt_pattern: str = r"[>#]\s*$",
    paging_disable: str = "terminal length 0",
):
    """Build a lightweight ConfigExtractionSettings-like object."""
    @dataclass
    class _FakeSettings:
        method: str
        command: str
        user: str
        password: str
        enable_password: str
        timeout: int
        prompt_pattern: str
        paging_disable: str

    return _FakeSettings(
        method=method,
        command=command,
        user=user,
        password=password,
        enable_password=enable_password,
        timeout=timeout,
        prompt_pattern=prompt_pattern,
        paging_disable=paging_disable,
    )


def _make_vendor_config(post_boot_commands=None, serial_type="pty"):
    """Build a minimal VendorConfig-like object."""
    cfg = MagicMock()
    cfg.post_boot_commands = post_boot_commands or []
    cfg.serial_type = serial_type
    return cfg


@pytest.fixture(autouse=True)
def _clear_post_boot_cache():
    """Ensure idempotency cache is clean before each test."""
    with _vm_post_boot_lock:
        _vm_post_boot_completed.clear()
    yield
    with _vm_post_boot_lock:
        _vm_post_boot_completed.clear()


# ===================================================================
# extract_vm_config
# ===================================================================

class TestExtractVmConfig:
    """Tests for the extract_vm_config entry point."""

    @patch("agent.console_extractor.PEXPECT_AVAILABLE", False)
    def test_pexpect_unavailable_returns_error(self):
        result = extract_vm_config("test-vm", "cisco_iosv")
        assert not result.success
        assert "pexpect" in result.error.lower()

    @patch("agent.console_extractor.PEXPECT_AVAILABLE", True)
    @patch("agent.vendors.get_config_extraction_settings")
    def test_method_none_returns_unsupported(self, mock_settings):
        mock_settings.return_value = _make_extraction_settings(method="none")
        result = extract_vm_config("test-vm", "some_device")
        assert not result.success
        assert "not supported" in result.error

    @patch("agent.console_extractor.PEXPECT_AVAILABLE", True)
    @patch("agent.vendors.get_config_extraction_settings")
    def test_unsupported_method_returns_error(self, mock_settings):
        mock_settings.return_value = _make_extraction_settings(method="unknown_method")
        result = extract_vm_config("test-vm", "some_device")
        assert not result.success
        assert "Unsupported extraction method" in result.error

    @patch("agent.console_extractor.PEXPECT_AVAILABLE", True)
    @patch("agent.console_extractor.SerialConsoleExtractor")
    @patch("agent.vendors.get_vendor_config")
    @patch("agent.vendors.get_config_extraction_settings")
    def test_serial_method_delegates_to_extractor(
        self, mock_settings, mock_vconfig, mock_extractor_cls
    ):
        mock_settings.return_value = _make_extraction_settings(method="serial")
        mock_vconfig.return_value = _make_vendor_config(serial_type="pty")
        expected = ExtractionResult(success=True, config="hostname R1")
        mock_extractor_cls.return_value.extract_config.return_value = expected

        result = extract_vm_config("test-vm", "cisco_iosv")

        assert result.success
        assert result.config == "hostname R1"
        mock_extractor_cls.assert_called_once()
        mock_extractor_cls.return_value.extract_config.assert_called_once()

    @patch("agent.console_extractor.PEXPECT_AVAILABLE", True)
    @patch("agent.console_extractor._get_tcp_serial_port_sync", return_value=None)
    @patch("agent.vendors.get_vendor_config")
    @patch("agent.vendors.get_config_extraction_settings")
    def test_serial_tcp_port_lookup_failure(
        self, mock_settings, mock_vconfig, mock_tcp_port
    ):
        mock_settings.return_value = _make_extraction_settings(method="serial")
        mock_vconfig.return_value = _make_vendor_config(serial_type="tcp")

        result = extract_vm_config("test-vm", "cisco_xrv9k")
        assert not result.success
        assert "TCP serial port" in result.error

    @patch("agent.console_extractor.PEXPECT_AVAILABLE", True)
    @patch("agent.console_extractor._get_tcp_serial_port_sync", return_value=4567)
    @patch("agent.console_extractor.SerialConsoleExtractor")
    @patch("agent.vendors.get_vendor_config")
    @patch("agent.vendors.get_config_extraction_settings")
    def test_serial_tcp_port_passed_to_extractor(
        self, mock_settings, mock_vconfig, mock_extractor_cls, mock_tcp_port
    ):
        mock_settings.return_value = _make_extraction_settings(method="serial")
        mock_vconfig.return_value = _make_vendor_config(serial_type="tcp")
        mock_extractor_cls.return_value.extract_config.return_value = ExtractionResult(
            success=True, config="!"
        )

        result = extract_vm_config("test-vm", "cisco_xrv9k")
        assert result.success
        call_kwargs = mock_extractor_cls.call_args[1]
        assert call_kwargs["tcp_port"] == 4567


# ===================================================================
# run_vm_post_boot_commands
# ===================================================================

class TestRunVmPostBootCommands:
    """Tests for the run_vm_post_boot_commands entry point."""

    @patch("agent.console_extractor.PEXPECT_AVAILABLE", False)
    @patch("agent.console_session_registry.set_console_control_state")
    def test_pexpect_unavailable_returns_error(self, mock_control):
        result = run_vm_post_boot_commands("test-vm", "cisco_iosv")
        assert not result.success
        assert "pexpect" in result.error.lower()

    @patch("agent.console_extractor.PEXPECT_AVAILABLE", True)
    @patch("agent.console_session_registry.set_console_control_state")
    @patch("agent.vendors.get_vendor_config")
    def test_no_post_boot_commands_succeeds(self, mock_vconfig, mock_control):
        mock_vconfig.return_value = _make_vendor_config(post_boot_commands=[])
        result = run_vm_post_boot_commands("test-vm", "cisco_iosv")
        assert result.success
        assert result.commands_run == 0

    @patch("agent.console_extractor.PEXPECT_AVAILABLE", True)
    @patch("agent.console_session_registry.set_console_control_state")
    @patch("agent.vendors.get_vendor_config")
    def test_idempotency_skip_on_second_call(self, mock_vconfig, mock_control):
        mock_vconfig.return_value = _make_vendor_config(post_boot_commands=[])
        # First call marks complete
        run_vm_post_boot_commands("idempotent-vm", "cisco_iosv")
        # Second call should skip
        result = run_vm_post_boot_commands("idempotent-vm", "cisco_iosv")
        assert result.success
        assert result.commands_run == 0

    @patch("agent.console_extractor.PEXPECT_AVAILABLE", True)
    @patch("agent.console_session_registry.set_console_control_state")
    @patch("agent.console_extractor.SerialConsoleExtractor")
    @patch("agent.vendors.get_config_extraction_settings")
    @patch("agent.vendors.get_vendor_config")
    def test_delegates_to_extractor_on_commands(
        self, mock_vconfig, mock_settings, mock_extractor_cls, mock_control
    ):
        mock_vconfig.return_value = _make_vendor_config(
            post_boot_commands=["no ip domain-lookup"]
        )
        mock_settings.return_value = _make_extraction_settings()
        expected = CommandResult(success=True, commands_run=1)
        mock_extractor_cls.return_value.run_commands.return_value = expected

        result = run_vm_post_boot_commands("test-vm", "cisco_iosv")
        assert result.success
        assert result.commands_run == 1
        mock_extractor_cls.return_value.run_commands.assert_called_once()

    @patch("agent.console_extractor.PEXPECT_AVAILABLE", True)
    @patch("agent.console_session_registry.set_console_control_state")
    @patch("agent.vendors.get_vendor_config")
    def test_vendor_config_none_succeeds(self, mock_vconfig, mock_control):
        mock_vconfig.return_value = None
        result = run_vm_post_boot_commands("test-vm", "unknown_kind")
        assert result.success
        assert result.commands_run == 0


# ===================================================================
# run_vm_cli_commands
# ===================================================================

class TestRunVmCliCommands:
    """Tests for the run_vm_cli_commands entry point."""

    def test_empty_commands_returns_early(self):
        result = run_vm_cli_commands("test-vm", "cisco_iosv", commands=[])
        assert result.success
        assert result.commands_run == 0
        assert result.outputs == []

    @patch("agent.console_extractor.PEXPECT_AVAILABLE", False)
    def test_pexpect_unavailable_returns_error(self):
        result = run_vm_cli_commands("test-vm", "cisco_iosv", commands=["show version"])
        assert not result.success
        assert "pexpect" in result.error.lower()

    @patch("agent.console_extractor.PEXPECT_AVAILABLE", True)
    @patch("agent.vendors.get_config_extraction_settings")
    def test_method_none_returns_error(self, mock_settings):
        mock_settings.return_value = _make_extraction_settings(method="none")
        result = run_vm_cli_commands("test-vm", "some_device", commands=["show version"])
        assert not result.success
        assert "not supported" in result.error

    @patch("agent.console_extractor.PEXPECT_AVAILABLE", True)
    @patch("agent.console_extractor.SerialConsoleExtractor")
    @patch("agent.vendors.get_vendor_config")
    @patch("agent.vendors.get_config_extraction_settings")
    def test_delegates_to_extractor_with_defaults(
        self, mock_settings, mock_vconfig, mock_extractor_cls
    ):
        mock_settings.return_value = _make_extraction_settings(
            user="admin", password="pass", prompt_pattern=r"#\s*$"
        )
        mock_vconfig.return_value = _make_vendor_config(serial_type="pty")
        expected = CommandCaptureResult(success=True, commands_run=1, outputs=[])
        mock_extractor_cls.return_value.run_commands_capture.return_value = expected

        result = run_vm_cli_commands(
            "test-vm", "cisco_iosv", commands=["show version"]
        )
        assert result.success
        call_kwargs = mock_extractor_cls.return_value.run_commands_capture.call_args[1]
        assert call_kwargs["username"] == "admin"
        assert call_kwargs["password"] == "pass"

    @patch("agent.console_extractor.PEXPECT_AVAILABLE", True)
    @patch("agent.console_extractor.SerialConsoleExtractor")
    @patch("agent.vendors.get_vendor_config")
    @patch("agent.vendors.get_config_extraction_settings")
    def test_overrides_applied(
        self, mock_settings, mock_vconfig, mock_extractor_cls
    ):
        mock_settings.return_value = _make_extraction_settings(
            user="default_user", password="default_pass"
        )
        mock_vconfig.return_value = _make_vendor_config(serial_type="pty")
        expected = CommandCaptureResult(success=True, commands_run=1, outputs=[])
        mock_extractor_cls.return_value.run_commands_capture.return_value = expected

        result = run_vm_cli_commands(
            "test-vm", "cisco_iosv",
            commands=["show ip route"],
            username="custom_user",
            password="custom_pass",
            prompt_pattern=r"Router#",
            timeout=60,
        )
        assert result.success
        call_kwargs = mock_extractor_cls.return_value.run_commands_capture.call_args[1]
        assert call_kwargs["username"] == "custom_user"
        assert call_kwargs["password"] == "custom_pass"
        assert call_kwargs["prompt_pattern"] == r"Router#"
        # timeout override: extractor should be built with 60
        build_kwargs = mock_extractor_cls.call_args[1]
        assert build_kwargs["timeout"] == 60


# ===================================================================
# clear_vm_post_boot_cache
# ===================================================================

class TestClearVmPostBootCache:
    """Tests for clear_vm_post_boot_cache."""

    @patch("agent.console_session_registry.set_console_control_state")
    def test_clear_specific_domain(self, mock_control):
        with _vm_post_boot_lock:
            _vm_post_boot_completed.add("vm-to-clear")
        clear_vm_post_boot_cache("vm-to-clear")
        assert "vm-to-clear" not in _vm_post_boot_completed

    def test_clear_all_domains(self):
        with _vm_post_boot_lock:
            _vm_post_boot_completed.add("vm-a")
            _vm_post_boot_completed.add("vm-b")
        clear_vm_post_boot_cache(None)
        assert len(_vm_post_boot_completed) == 0
