"""Tests for console_extractor.py fallback chain, TCP serial, console lock,
serial log sanitization, and retry behavior.

Covers:
- Fallback chain ordering: piggyback -> virsh/TCP console with retries
- TCP serial: spawn, connection refused, timeout recovery
- Console lock: busy session skips to piggyback, lock timeout handling
- Serial log \\r sanitization in _clean_config
- Retry behavior with max attempts
"""

from __future__ import annotations

import contextlib
import importlib.util
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# Ensure pexpect stub is available so SerialConsoleExtractor can be
# constructed even in environments without the real package.
if importlib.util.find_spec("pexpect") is None:
    _pexpect_stub = types.ModuleType("pexpect")

    class TIMEOUT(Exception):
        pass

    class EOF(Exception):
        pass

    class _FakeSpawn:
        def __init__(self, *args, **kwargs):
            self.before = ""
            self.after = ""

    _pexpect_stub.TIMEOUT = TIMEOUT
    _pexpect_stub.EOF = EOF
    _pexpect_stub.spawn = _FakeSpawn
    sys.modules["pexpect"] = _pexpect_stub

import pexpect  # noqa: E402

import agent.console_extractor as ce
from agent.console_extractor import (
    ExtractionResult,
    CommandResult,
    CommandCaptureResult,
    SerialConsoleExtractor,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_extractor(**kwargs) -> SerialConsoleExtractor:
    """Build a SerialConsoleExtractor via __new__ to skip __init__ validation."""
    ext = SerialConsoleExtractor.__new__(SerialConsoleExtractor)
    ext.domain_name = kwargs.get("domain_name", "test-vm")
    ext.libvirt_uri = kwargs.get("libvirt_uri", "qemu:///system")
    ext.timeout = kwargs.get("timeout", 30)
    ext.tcp_port = kwargs.get("tcp_port", None)
    ext.child = kwargs.get("child", None)
    return ext


def _set_registry_module(monkeypatch, **overrides):
    """Inject a fake agent.console_session_registry into sys.modules."""
    module = types.ModuleType("agent.console_session_registry")
    defaults = {
        "piggyback_extract": lambda **kwargs: None,
        "piggyback_run_commands": lambda **kwargs: None,
        "piggyback_run_commands_capture": lambda **kwargs: None,
        "get_session": lambda domain_name: None,
        "set_console_control_state": lambda domain_name, *, state, message: True,
    }
    defaults.update(overrides)
    for key, value in defaults.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, "agent.console_session_registry", module)
    return module


def _set_virsh_lock_module(monkeypatch, *, console_lock=None, extraction_session=None):
    """Inject a fake agent.virsh_console_lock into sys.modules."""
    module = types.ModuleType("agent.virsh_console_lock")
    module.console_lock = (
        console_lock
        if console_lock is not None
        else (lambda *args, **kwargs: contextlib.nullcontext())
    )
    module.extraction_session = (
        extraction_session
        if extraction_session is not None
        else (lambda *args, **kwargs: contextlib.nullcontext())
    )
    monkeypatch.setitem(sys.modules, "agent.virsh_console_lock", module)
    return module


@contextlib.contextmanager
def _timeout_lock(*args, **kwargs):
    """Simulates a console lock that raises TimeoutError."""
    raise TimeoutError("Console locked")
    yield  # pragma: no cover


# ---------------------------------------------------------------------------
# TestFallbackChain
# ---------------------------------------------------------------------------

class TestFallbackChain:
    """Verify the fallback ordering in extract_config."""

    def test_piggyback_success_stops_chain(self, monkeypatch):
        """When piggyback extraction succeeds with valid config, no virsh spawn."""
        ext = _make_extractor()
        good_result = ExtractionResult(
            success=True,
            config="hostname Router\ninterface Loopback0\n ip address 1.1.1.1 255.255.255.255\n!",
        )
        _set_registry_module(monkeypatch, piggyback_extract=lambda **kw: good_result)
        _set_virsh_lock_module(monkeypatch)

        spawn_called = False
        original_spawn = ext._spawn_console

        def tracked_spawn():
            nonlocal spawn_called
            spawn_called = True
            return original_spawn()

        monkeypatch.setattr(ext, "_spawn_console", tracked_spawn)

        result = ext.extract_config()
        assert result.success is True
        assert "hostname Router" in result.config
        assert not spawn_called

    def test_piggyback_failure_falls_through_to_virsh(self, monkeypatch):
        """When piggyback returns failure, fall through to direct console."""
        ext = _make_extractor()
        failed_pb = ExtractionResult(success=False, error="piggyback session error")
        direct_ok = ExtractionResult(
            success=True,
            config="hostname Switch\nversion 15.2\n!",
        )
        _set_registry_module(monkeypatch, piggyback_extract=lambda **kw: failed_pb)
        _set_virsh_lock_module(monkeypatch)
        monkeypatch.setattr(ext, "_extract_config_inner", lambda *a, **kw: direct_ok)
        monkeypatch.setattr(ce.time, "sleep", lambda _: None)

        result = ext.extract_config()
        assert result.success is True
        assert "hostname Switch" in result.config

    def test_piggyback_none_falls_through(self, monkeypatch):
        """When piggyback returns None (no active session), use direct console."""
        ext = _make_extractor()
        direct_ok = ExtractionResult(
            success=True,
            config="hostname Router\nversion 17.3\n!",
        )
        _set_registry_module(monkeypatch, piggyback_extract=lambda **kw: None)
        _set_virsh_lock_module(monkeypatch)
        monkeypatch.setattr(ext, "_extract_config_inner", lambda *a, **kw: direct_ok)
        monkeypatch.setattr(ce.time, "sleep", lambda _: None)

        result = ext.extract_config()
        assert result.success is True

    def test_piggyback_invalid_output_falls_through(self, monkeypatch):
        """When piggyback succeeds but output is invalid, fall through."""
        ext = _make_extractor()
        # Very short config that won't pass validation
        bad_config = ExtractionResult(success=True, config="ok")
        direct_ok = ExtractionResult(
            success=True,
            config="hostname Router\nversion 17.3\ninterface Loopback0\n!",
        )
        _set_registry_module(monkeypatch, piggyback_extract=lambda **kw: bad_config)
        _set_virsh_lock_module(monkeypatch)
        monkeypatch.setattr(ext, "_extract_config_inner", lambda *a, **kw: direct_ok)
        monkeypatch.setattr(ce.time, "sleep", lambda _: None)

        result = ext.extract_config()
        assert result.success is True
        assert "hostname Router" in result.config

    def test_all_retries_fail_returns_last_error(self, monkeypatch):
        """When all extraction attempts fail, return the last error."""
        ext = _make_extractor()
        fail_result = ExtractionResult(success=False, error="spawn failed")
        _set_registry_module(monkeypatch, piggyback_extract=lambda **kw: None)
        _set_virsh_lock_module(monkeypatch)
        monkeypatch.setattr(ce.time, "sleep", lambda _: None)

        call_count = {"n": 0}

        def _inner(*a, **kw):
            call_count["n"] += 1
            return fail_result

        monkeypatch.setattr(ext, "_extract_config_inner", _inner)

        result = ext.extract_config(retries=2)
        assert call_count["n"] == 3  # 1 initial + 2 retries
        assert result.success is False
        assert "spawn failed" in result.error


# ---------------------------------------------------------------------------
# TestTcpSerial
# ---------------------------------------------------------------------------

class TestTcpSerial:
    """Tests for TCP serial console spawning and error handling."""

    def test_tcp_spawn_successful(self, monkeypatch):
        """TCP serial spawn sets child and returns None (success)."""
        ext = _make_extractor(tcp_port=5000)
        mock_child = MagicMock()
        monkeypatch.setattr(ce, "_reset_tcp_chardev_sync", lambda *a, **kw: None)
        monkeypatch.setattr(ce.pexpect, "spawn", lambda *a, **kw: mock_child)
        monkeypatch.setattr(ce.time, "sleep", lambda _: None)

        err = ext._spawn_console()
        assert err is None
        assert ext.child is mock_child

    def test_tcp_spawn_resets_chardev_before_connect(self, monkeypatch):
        """TCP path calls _reset_tcp_chardev_sync before spawning."""
        ext = _make_extractor(tcp_port=5000, domain_name="my-vm")
        mock_child = MagicMock()
        call_order = []

        def tracked_reset(*a, **kw):
            call_order.append("reset")

        def tracked_spawn(*a, **kw):
            call_order.append("spawn")
            return mock_child

        monkeypatch.setattr(ce, "_reset_tcp_chardev_sync", tracked_reset)
        monkeypatch.setattr(ce.pexpect, "spawn", tracked_spawn)
        monkeypatch.setattr(ce.time, "sleep", lambda _: None)

        ext._spawn_console()
        assert call_order == ["reset", "spawn"]

    def test_tcp_spawn_exception_propagates(self, monkeypatch):
        """If pexpect.spawn raises OSError, _spawn_console propagates it."""
        ext = _make_extractor(tcp_port=5000)
        monkeypatch.setattr(ce, "_reset_tcp_chardev_sync", lambda *a, **kw: None)
        monkeypatch.setattr(
            ce.pexpect, "spawn",
            MagicMock(side_effect=OSError("Connection refused")),
        )
        monkeypatch.setattr(ce.time, "sleep", lambda _: None)

        with pytest.raises(OSError, match="Connection refused"):
            ext._spawn_console()

    def test_virsh_spawn_uses_virsh_console(self, monkeypatch):
        """When tcp_port is None, _spawn_console uses virsh console."""
        ext = _make_extractor(tcp_port=None)
        mock_child = MagicMock()
        mock_child.expect.return_value = 0  # matches "Connected to domain"

        spawn_args = {}

        def capture_spawn(*a, **kw):
            spawn_args["args"] = a
            return mock_child

        monkeypatch.setattr(ce.pexpect, "spawn", capture_spawn)
        monkeypatch.setattr(ce.time, "sleep", lambda _: None)

        err = ext._spawn_console()
        assert err is None
        assert "virsh" in spawn_args["args"][0]


# ---------------------------------------------------------------------------
# TestConsoleLock
# ---------------------------------------------------------------------------

class TestConsoleLock:
    """Tests for console lock interaction during extraction."""

    def test_lock_timeout_returns_locked_error(self, monkeypatch):
        """When console_lock raises TimeoutError, result says locked."""
        ext = _make_extractor()
        _set_registry_module(monkeypatch, piggyback_extract=lambda **kw: None)
        _set_virsh_lock_module(monkeypatch, console_lock=_timeout_lock)
        monkeypatch.setattr(ce.time, "sleep", lambda _: None)

        result = ext.extract_config(retries=0)
        assert result.success is False
        assert "locked" in result.error.lower()

    def test_run_commands_lock_timeout(self, monkeypatch):
        """run_commands returns locked error when console_lock times out."""
        ext = _make_extractor()
        _set_registry_module(monkeypatch, piggyback_run_commands=lambda **kw: None)
        _set_virsh_lock_module(monkeypatch, console_lock=_timeout_lock)
        monkeypatch.setattr(ce.time, "sleep", lambda _: None)

        result = ext.run_commands(commands=["show version"], retries=0)
        assert result.success is False
        assert "locked" in result.error.lower()

    def test_piggyback_routes_run_commands(self, monkeypatch):
        """run_commands uses piggyback result when session is active."""
        ext = _make_extractor()
        pb_result = CommandResult(success=True, commands_run=2)
        _set_registry_module(
            monkeypatch,
            piggyback_run_commands=lambda **kw: pb_result,
        )

        result = ext.run_commands(commands=["cmd1", "cmd2"])
        assert result.success is True
        assert result.commands_run == 2

    def test_run_commands_capture_lock_timeout(self, monkeypatch):
        """run_commands_capture returns locked error on lock timeout."""
        ext = _make_extractor()
        _set_registry_module(
            monkeypatch,
            piggyback_run_commands_capture=lambda **kw: None,
            get_session=lambda domain_name: None,
        )
        _set_virsh_lock_module(monkeypatch, console_lock=_timeout_lock)
        monkeypatch.setattr(ce.time, "sleep", lambda _: None)

        result = ext.run_commands_capture(commands=["show ver"], retries=0)
        assert result.success is False
        assert "locked" in result.error.lower()


# ---------------------------------------------------------------------------
# TestSerialLogSanitization
# ---------------------------------------------------------------------------

class TestSerialLogSanitization:
    """Tests for \\r sanitization in _clean_config."""

    def test_carriage_returns_stripped(self):
        """_clean_config removes \\r characters from raw output."""
        ext = _make_extractor()
        raw = "hostname\r Router\r\ninterface\r Loopback0\r\n!\r\n"
        cleaned = ext._clean_config(raw, "show running-config")
        assert "\r" not in cleaned
        assert "hostname Router" in cleaned

    def test_kernel_panic_detected_despite_carriage_returns(self):
        """Carriage returns in serial output do not mask kernel panic markers.

        _clean_config strips \\r, so downstream pattern matching on the
        cleaned output will find 'Kernel panic' even when the raw serial
        log has interleaved \\r bytes.
        """
        ext = _make_extractor()
        raw = "Ker\rnel pan\ric - not syncing: VFS\r\n"
        cleaned = ext._clean_config(raw, "show running-config")
        assert "\r" not in cleaned
        assert "Kernel panic" in cleaned

    def test_ansi_escape_sequences_removed(self):
        """_clean_config strips ANSI escape codes."""
        ext = _make_extractor()
        raw = "\x1b[32mhostname Router\x1b[0m\ninterface Loopback0\n!\n"
        cleaned = ext._clean_config(raw, "show running-config")
        assert "\x1b" not in cleaned
        assert "hostname Router" in cleaned

    def test_command_echo_removed(self):
        """_clean_config strips the command echo line."""
        ext = _make_extractor()
        raw = "show running-config\nhostname Router\ninterface Loopback0\n!\n"
        cleaned = ext._clean_config(raw, "show running-config")
        lines = [ln for ln in cleaned.splitlines() if ln.strip()]
        assert not any(ln.strip() == "show running-config" for ln in lines)
        assert "hostname Router" in cleaned


# ---------------------------------------------------------------------------
# TestRetryBehavior
# ---------------------------------------------------------------------------

class TestRetryBehavior:
    """Tests for retry/backoff in extract_config and run_commands."""

    def test_extract_config_respects_retry_count(self, monkeypatch):
        """extract_config runs exactly 1 + retries attempts."""
        ext = _make_extractor()
        _set_registry_module(monkeypatch, piggyback_extract=lambda **kw: None)
        _set_virsh_lock_module(monkeypatch)
        monkeypatch.setattr(ce.time, "sleep", lambda _: None)

        call_count = {"n": 0}

        def _inner(*a, **kw):
            call_count["n"] += 1
            return ExtractionResult(success=False, error="fail")

        monkeypatch.setattr(ext, "_extract_config_inner", _inner)

        result = ext.extract_config(retries=3)
        assert call_count["n"] == 4  # 1 initial + 3 retries
        assert result.success is False

    def test_run_commands_respects_retry_count(self, monkeypatch):
        """run_commands runs exactly 1 + retries attempts."""
        ext = _make_extractor()
        _set_registry_module(monkeypatch, piggyback_run_commands=lambda **kw: None)
        _set_virsh_lock_module(monkeypatch)
        monkeypatch.setattr(ce.time, "sleep", lambda _: None)

        call_count = {"n": 0}

        def _inner(*a, **kw):
            call_count["n"] += 1
            return CommandResult(success=False, error="fail")

        monkeypatch.setattr(ext, "_run_commands_inner", _inner)

        result = ext.run_commands(commands=["cmd"], retries=2)
        assert call_count["n"] == 3
        assert result.success is False

    def test_early_success_stops_retrying(self, monkeypatch):
        """If an attempt succeeds, no further retries."""
        ext = _make_extractor()
        _set_registry_module(monkeypatch, piggyback_extract=lambda **kw: None)
        _set_virsh_lock_module(monkeypatch)
        monkeypatch.setattr(ce.time, "sleep", lambda _: None)

        ok = ExtractionResult(success=True, config="hostname OK\nversion 1\n!")
        fail = ExtractionResult(success=False, error="nope")
        call_count = {"n": 0}

        def _inner(*a, **kw):
            call_count["n"] += 1
            return ok if call_count["n"] == 2 else fail

        monkeypatch.setattr(ext, "_extract_config_inner", _inner)

        result = ext.extract_config(retries=5)
        assert result.success is True
        assert call_count["n"] == 2  # stopped after 2nd attempt

    def test_retry_uses_exponential_backoff(self, monkeypatch):
        """Retries sleep with exponential backoff (2^attempt seconds)."""
        ext = _make_extractor()
        _set_registry_module(monkeypatch, piggyback_extract=lambda **kw: None)
        _set_virsh_lock_module(monkeypatch)

        sleep_values = []
        monkeypatch.setattr(ce.time, "sleep", lambda s: sleep_values.append(s))
        monkeypatch.setattr(
            ext, "_extract_config_inner",
            lambda *a, **kw: ExtractionResult(success=False, error="fail"),
        )

        ext.extract_config(retries=2)
        # Attempt 1 (retry): 2^1 = 2s; attempt 2 (retry): 2^2 = 4s
        assert 2 in sleep_values
        assert 4 in sleep_values
