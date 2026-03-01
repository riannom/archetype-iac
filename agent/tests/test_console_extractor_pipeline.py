"""Tests for console_extractor.py pipeline functions.

Covers clean_config, validate_extracted_config, _prime_console_for_prompt,
_extract_config_inner, and _get_tcp_serial_port_sync.
"""

from __future__ import annotations

import importlib.util
import sys
import types


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

import pexpect  # noqa: E402 — must come after stub installation

from agent.console_extractor import (
    SerialConsoleExtractor,
    _get_tcp_serial_port_sync,
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


class FakeChild:
    """Minimal pexpect.spawn stand-in for unit tests.

    Set ``expect_sequence`` to a list of indices that ``expect()`` should
    return on successive calls. Each call pops the first entry. When the
    list is exhausted, ``expect()`` raises ``pexpect.TIMEOUT``.
    """

    def __init__(self, expect_sequence: list[int] | None = None):
        self._expect_sequence = list(expect_sequence or [])
        self.before = ""
        self.after = ""
        self._sent: list[str] = []
        self._sendlined: list[str] = []

    def expect(self, patterns, timeout=None):
        if not self._expect_sequence:
            raise pexpect.TIMEOUT("fake timeout")
        return self._expect_sequence.pop(0)

    def send(self, data):
        self._sent.append(data)

    def sendline(self, data):
        self._sendlined.append(data)

    def sendcontrol(self, char):
        pass

    def close(self, force=False):
        pass


# ---------------------------------------------------------------------------
# 1. _clean_config()
# ---------------------------------------------------------------------------


class TestCleanConfig:
    """Tests for SerialConsoleExtractor._clean_config()."""

    def test_removes_ansi_escape_sequences(self):
        """ANSI color codes should be stripped."""
        ext = _make_extractor()
        raw = "\x1b[32mhostname router1\x1b[0m\ninterface Ethernet0"
        result = ext._clean_config(raw, "show running-config")
        assert "\x1b" not in result
        assert "hostname router1" in result
        assert "interface Ethernet0" in result

    def test_removes_command_echo(self):
        """The command echo line should be removed from output."""
        ext = _make_extractor()
        raw = "show running-config\nhostname router1\ninterface Ethernet0\nend"
        result = ext._clean_config(raw, "show running-config")
        assert "show running-config" not in result
        assert "hostname router1" in result

    def test_removes_command_echo_with_prompt_prefix(self):
        """Command echo with a prompt prefix (e.g., 'Router#show ...') should be removed."""
        ext = _make_extractor()
        raw = "Router#show running-config\nhostname router1\ninterface Ethernet0\nend"
        result = ext._clean_config(raw, "show running-config")
        lines = result.strip().splitlines()
        assert not any("show running-config" in ln for ln in lines)

    def test_strips_building_configuration(self):
        """'Building configuration...' banner should be removed."""
        ext = _make_extractor()
        raw = "Building configuration...\n\nhostname router1\ninterface Ethernet0"
        result = ext._clean_config(raw, "show running-config")
        assert "Building configuration" not in result
        assert "hostname router1" in result

    def test_preserves_config_lines(self):
        """Normal configuration lines should pass through unchanged."""
        ext = _make_extractor()
        raw = (
            "hostname router1\n"
            "!\n"
            "interface Ethernet0\n"
            " ip address 10.0.0.1 255.255.255.0\n"
            "!\n"
            "end"
        )
        result = ext._clean_config(raw, "show running-config")
        assert "hostname router1" in result
        assert "interface Ethernet0" in result
        assert "ip address 10.0.0.1 255.255.255.0" in result

    def test_removes_carriage_returns(self):
        """Carriage returns from serial transport should be stripped."""
        ext = _make_extractor()
        raw = "hostname router1\r\ninterface Ethernet0\r\n"
        result = ext._clean_config(raw, "show running-config")
        assert "\r" not in result
        assert "hostname router1" in result


# ---------------------------------------------------------------------------
# 2. _validate_extracted_config()
# ---------------------------------------------------------------------------


class TestValidateExtractedConfig:
    """Tests for SerialConsoleExtractor._validate_extracted_config()."""

    def test_valid_config_passes(self):
        """A multi-line config with recognizable content should validate."""
        ext = _make_extractor()
        config = "hostname router1\n!\ninterface Ethernet0\n ip address 10.0.0.1 255.255.255.0\n!\nend"
        valid, reason = ext._validate_extracted_config(config, "show running-config")
        assert valid is True
        assert reason == ""

    def test_empty_config_fails(self):
        """Empty string should not pass validation."""
        ext = _make_extractor()
        valid, reason = ext._validate_extracted_config("", "show running-config")
        assert valid is False
        assert "empty" in reason.lower()

    def test_single_line_fails(self):
        """A single-line output is not a valid config."""
        ext = _make_extractor()
        valid, reason = ext._validate_extracted_config("hostname router1", "show running-config")
        assert valid is False
        assert "too few lines" in reason.lower()

    def test_cli_error_marker_fails(self):
        """Output containing '% Invalid input' should fail validation."""
        ext = _make_extractor()
        config = "% Invalid input detected at '^' marker\nsome other line"
        valid, reason = ext._validate_extracted_config(config, "show running-config")
        assert valid is False
        assert "cli error marker" in reason.lower()

    def test_incomplete_command_marker_fails(self):
        """Output containing '% Incomplete command' should fail validation."""
        ext = _make_extractor()
        config = "% Incomplete command\nanother line here"
        valid, reason = ext._validate_extracted_config(config, "show running-config")
        assert valid is False
        assert "cli error marker" in reason.lower()

    def test_command_echo_only_fails(self):
        """Output that contains only the command echo should fail."""
        ext = _make_extractor()
        config = "show running-config\nterminal length 0"
        valid, reason = ext._validate_extracted_config(
            config,
            "show running-config",
            paging_disable="terminal length 0",
        )
        assert valid is False
        assert "command echoes" in reason.lower()


# ---------------------------------------------------------------------------
# 3. _prime_console_for_prompt()
# ---------------------------------------------------------------------------


class TestPrimeConsoleForPrompt:
    """Tests for SerialConsoleExtractor._prime_console_for_prompt()."""

    def test_immediate_prompt_detected(self):
        """When the prompt is immediately available, should return True."""
        ext = _make_extractor()
        # First expect() returns index 0 — matches prompt_pattern
        ext.child = FakeChild(expect_sequence=[0])
        result = ext._prime_console_for_prompt(r"[>#]\s*$")
        assert result is True

    def test_poap_abort_answered_with_yes(self):
        """When POAP abort prompt is detected, should send 'yes'."""
        ext = _make_extractor()
        prompt_patterns = ext._prompt_patterns(r"[>#]\s*$")
        num_prompt = len(prompt_patterns)
        # Patterns after prompt: Press RETURN(+0), Username(+1), Login(+2),
        #   config dialog(+3), poap_abort(+4), secure password(+5),
        #   enter admin pw(+6), confirm admin pw(+7)
        # POAP abort is at index num_prompt + 4
        poap_index = num_prompt + 4
        ext.child = FakeChild(expect_sequence=[poap_index])
        result = ext._prime_console_for_prompt(r"[>#]\s*$")
        # POAP abort is a non-Press-RETURN match, so it returns True
        assert result is True
        assert "yes" in ext.child._sendlined

    def test_returns_false_after_max_attempts(self):
        """After 8 failed attempts, should return False."""
        ext = _make_extractor()
        # All attempts time out — FakeChild raises TIMEOUT when sequence is empty
        ext.child = FakeChild(expect_sequence=[])
        result = ext._prime_console_for_prompt(r"[>#]\s*$")
        assert result is False

    def test_press_return_sends_enter_and_continues(self):
        """'Press RETURN' should send Enter and keep trying."""
        ext = _make_extractor()
        prompt_patterns = ext._prompt_patterns(r"[>#]\s*$")
        num_prompt = len(prompt_patterns)
        # First expect: Press RETURN (index num_prompt + 0)
        # Second expect: prompt matched (index 0)
        press_return_index = num_prompt
        ext.child = FakeChild(expect_sequence=[press_return_index, 0])
        result = ext._prime_console_for_prompt(r"[>#]\s*$")
        assert result is True


# ---------------------------------------------------------------------------
# 4. _extract_config_inner()
# ---------------------------------------------------------------------------


class TestExtractConfigInner:
    """Tests for SerialConsoleExtractor._extract_config_inner()."""

    def test_success_no_login(self, monkeypatch):
        """Extraction without login should succeed when all steps work."""
        ext = _make_extractor()
        monkeypatch.setattr(ext, "_spawn_console", lambda: None)
        monkeypatch.setattr(ext, "_prime_console_for_prompt", lambda pat: True)
        monkeypatch.setattr(ext, "_wait_for_prompt", lambda pat: True)
        monkeypatch.setattr(ext, "_attempt_enable_mode", lambda pw, pat: None)
        monkeypatch.setattr(ext, "_disable_paging", lambda cmd, pat: None)
        monkeypatch.setattr(
            ext, "_execute_command",
            lambda cmd, pat: "hostname router1\n!\ninterface Ethernet0\nend",
        )
        monkeypatch.setattr(ext, "_cleanup", lambda: None)

        result = ext._extract_config_inner(
            command="show running-config",
            username="",
            password="",
            enable_password="",
            prompt_pattern=r"[>#]\s*$",
            paging_disable="terminal length 0",
        )

        assert result.success is True
        assert "hostname router1" in result.config

    def test_login_required(self, monkeypatch):
        """Extraction with login credentials should call _handle_login."""
        ext = _make_extractor()
        login_called = {"count": 0}

        monkeypatch.setattr(ext, "_spawn_console", lambda: None)
        monkeypatch.setattr(ext, "_prime_console_for_prompt", lambda pat: True)

        def fake_handle_login(user, pw, pat):
            login_called["count"] += 1
            return True

        monkeypatch.setattr(ext, "_handle_login", fake_handle_login)
        monkeypatch.setattr(ext, "_attempt_enable_mode", lambda pw, pat: None)
        monkeypatch.setattr(ext, "_disable_paging", lambda cmd, pat: None)
        monkeypatch.setattr(
            ext, "_execute_command",
            lambda cmd, pat: "hostname router1\n!\ninterface Ethernet0\nend",
        )
        monkeypatch.setattr(ext, "_cleanup", lambda: None)

        result = ext._extract_config_inner(
            command="show running-config",
            username="admin",
            password="cisco",
            enable_password="",
            prompt_pattern=r"[>#]\s*$",
            paging_disable="terminal length 0",
        )

        assert result.success is True
        assert login_called["count"] == 1

    def test_timeout_failure(self, monkeypatch):
        """When pexpect.TIMEOUT is raised, should return failure."""
        ext = _make_extractor()
        monkeypatch.setattr(ext, "_spawn_console", lambda: None)
        monkeypatch.setattr(ext, "_prime_console_for_prompt", lambda pat: True)
        monkeypatch.setattr(ext, "_wait_for_prompt", lambda pat: True)
        monkeypatch.setattr(ext, "_attempt_enable_mode", lambda pw, pat: None)
        monkeypatch.setattr(ext, "_disable_paging", lambda cmd, pat: None)

        def raise_timeout(cmd, pat):
            raise pexpect.TIMEOUT("timed out")

        monkeypatch.setattr(ext, "_execute_command", raise_timeout)
        monkeypatch.setattr(ext, "_cleanup", lambda: None)

        result = ext._extract_config_inner(
            command="show running-config",
            username="",
            password="",
            enable_password="",
            prompt_pattern=r"[>#]\s*$",
            paging_disable="terminal length 0",
        )

        assert result.success is False
        assert "timeout" in result.error.lower()

    def test_eof_failure(self, monkeypatch):
        """When pexpect.EOF is raised, should return failure."""
        ext = _make_extractor()
        monkeypatch.setattr(ext, "_spawn_console", lambda: None)
        monkeypatch.setattr(ext, "_prime_console_for_prompt", lambda pat: True)
        monkeypatch.setattr(ext, "_wait_for_prompt", lambda pat: True)
        monkeypatch.setattr(ext, "_attempt_enable_mode", lambda pw, pat: None)
        monkeypatch.setattr(ext, "_disable_paging", lambda cmd, pat: None)

        def raise_eof(cmd, pat):
            raise pexpect.EOF("connection closed")

        monkeypatch.setattr(ext, "_execute_command", raise_eof)
        monkeypatch.setattr(ext, "_cleanup", lambda: None)

        result = ext._extract_config_inner(
            command="show running-config",
            username="",
            password="",
            enable_password="",
            prompt_pattern=r"[>#]\s*$",
            paging_disable="terminal length 0",
        )

        assert result.success is False
        assert "closed" in result.error.lower() or "eof" in result.error.lower()

    def test_spawn_failure_returns_error(self, monkeypatch):
        """When _spawn_console returns an error string, extraction should fail."""
        ext = _make_extractor()
        monkeypatch.setattr(ext, "_spawn_console", lambda: "Timeout waiting for console connection")
        monkeypatch.setattr(ext, "_cleanup", lambda: None)

        result = ext._extract_config_inner(
            command="show running-config",
            username="",
            password="",
            enable_password="",
            prompt_pattern=r"[>#]\s*$",
            paging_disable="terminal length 0",
        )

        assert result.success is False
        assert "console connection" in result.error.lower()

    def test_prime_console_failure_returns_error(self, monkeypatch):
        """When _prime_console_for_prompt returns False, extraction should fail."""
        ext = _make_extractor()
        monkeypatch.setattr(ext, "_spawn_console", lambda: None)
        monkeypatch.setattr(ext, "_prime_console_for_prompt", lambda pat: False)
        monkeypatch.setattr(ext, "_cleanup", lambda: None)

        result = ext._extract_config_inner(
            command="show running-config",
            username="",
            password="",
            enable_password="",
            prompt_pattern=r"[>#]\s*$",
            paging_disable="terminal length 0",
        )

        assert result.success is False
        assert "wake console" in result.error.lower()


# ---------------------------------------------------------------------------
# 5. _get_tcp_serial_port_sync()
# ---------------------------------------------------------------------------


class TestGetTcpSerialPort:
    """Tests for _get_tcp_serial_port_sync()."""

    def test_returns_none_when_libvirt_unavailable(self, monkeypatch):
        """When libvirt is not installed, should return None."""
        monkeypatch.setattr(
            "agent.console_extractor._LIBVIRT_AVAILABLE", False,
        )
        result = _get_tcp_serial_port_sync("test-vm")
        assert result is None

    def test_parses_xml_for_tcp_port(self, monkeypatch):
        """When domain XML has a TCP serial, should return the port number."""
        monkeypatch.setattr(
            "agent.console_extractor._LIBVIRT_AVAILABLE", True,
        )

        domain_xml = """<domain>
          <devices>
            <serial type='tcp'>
              <source mode='bind' host='127.0.0.1' service='4567'/>
              <target port='0'/>
            </serial>
          </devices>
        </domain>"""

        class FakeDomain:
            def XMLDesc(self, flags):
                return domain_xml

        class FakeConn:
            def lookupByName(self, name):
                return FakeDomain()

            def close(self):
                pass

        monkeypatch.setattr(
            "agent.console_extractor._libvirt",
            types.SimpleNamespace(open=lambda uri: FakeConn()),
        )

        result = _get_tcp_serial_port_sync("test-vm")
        assert result == 4567

    def test_returns_none_when_no_tcp_serial(self, monkeypatch):
        """When domain XML has no TCP serial port, should return None."""
        monkeypatch.setattr(
            "agent.console_extractor._LIBVIRT_AVAILABLE", True,
        )

        domain_xml = """<domain>
          <devices>
            <serial type='pty'>
              <target port='0'/>
            </serial>
          </devices>
        </domain>"""

        class FakeDomain:
            def XMLDesc(self, flags):
                return domain_xml

        class FakeConn:
            def lookupByName(self, name):
                return FakeDomain()

            def close(self):
                pass

        monkeypatch.setattr(
            "agent.console_extractor._libvirt",
            types.SimpleNamespace(open=lambda uri: FakeConn()),
        )

        result = _get_tcp_serial_port_sync("test-vm")
        assert result is None

    def test_returns_none_on_libvirt_error(self, monkeypatch):
        """When libvirt raises an exception, should return None."""
        monkeypatch.setattr(
            "agent.console_extractor._LIBVIRT_AVAILABLE", True,
        )

        def open_that_fails(uri):
            raise Exception("libvirtd not running")

        monkeypatch.setattr(
            "agent.console_extractor._libvirt",
            types.SimpleNamespace(open=open_that_fails),
        )

        result = _get_tcp_serial_port_sync("test-vm")
        assert result is None

    def test_returns_none_when_conn_is_none(self, monkeypatch):
        """When libvirt.open returns None, should return None."""
        monkeypatch.setattr(
            "agent.console_extractor._LIBVIRT_AVAILABLE", True,
        )

        monkeypatch.setattr(
            "agent.console_extractor._libvirt",
            types.SimpleNamespace(open=lambda uri: None),
        )

        result = _get_tcp_serial_port_sync("test-vm")
        assert result is None
