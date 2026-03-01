"""Tests for agent/console_session_registry.py.

Covers session registry CRUD, console control state management,
_clean_config output sanitization, _contains_cli_error detection,
PtyInjector I/O, and piggyback extraction/command orchestrators.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent import console_session_registry as registry
from agent.console_session_registry import (
    ActiveConsoleSession,
    _clean_config,
    _contains_cli_error,
    get_console_control_state,
    get_session,
    list_active_domains,
    register_session,
    set_console_control_state,
    unregister_session,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(domain: str = "test-domain", fd: int = 99) -> ActiveConsoleSession:
    """Create an ActiveConsoleSession with a mock websocket and event loop."""
    loop = MagicMock(spec=asyncio.AbstractEventLoop)
    ws = MagicMock()
    ws.send_text = AsyncMock()
    ws.send_bytes = AsyncMock()
    session = ActiveConsoleSession(
        domain_name=domain,
        master_fd=fd,
        loop=loop,
        websocket=ws,
    )
    return session


@pytest.fixture(autouse=True)
def _clean_registry():
    """Ensure the module-level registry is empty before/after each test."""
    with registry._registry_lock:
        registry._registry.clear()
        registry._console_control_state.clear()
    yield
    with registry._registry_lock:
        registry._registry.clear()
        registry._console_control_state.clear()


# =============================================================================
# Session Registry
# =============================================================================


class TestSessionRegistry:
    """register/get/unregister/list operations."""

    def test_register_and_get(self):
        session = _make_session("dom1")
        register_session("dom1", session)
        assert get_session("dom1") is session

    def test_unregister_removes_session(self):
        session = _make_session("dom1")
        register_session("dom1", session)
        unregister_session("dom1")
        assert get_session("dom1") is None

    def test_get_nonexistent_returns_none(self):
        assert get_session("no-such-domain") is None

    def test_list_active_domains(self):
        register_session("dom-a", _make_session("dom-a"))
        register_session("dom-b", _make_session("dom-b"))
        domains = list_active_domains()
        assert sorted(domains) == ["dom-a", "dom-b"]

    def test_list_active_domains_empty(self):
        assert list_active_domains() == []

    def test_unregister_nonexistent_is_silent(self):
        """Unregistering a domain that was never registered should not raise."""
        unregister_session("ghost-domain")  # should not raise


# =============================================================================
# Console Control State
# =============================================================================


class TestConsoleControlState:
    """set/get console control state transitions."""

    def test_set_read_only(self):
        changed = set_console_control_state(
            "dom1", state="read_only", message="Extracting config"
        )
        assert changed is True
        state = get_console_control_state("dom1")
        assert state == ("read_only", "Extracting config")

    def test_set_interactive_clears(self):
        set_console_control_state("dom1", state="read_only", message="busy")
        changed = set_console_control_state(
            "dom1", state="interactive", message="done"
        )
        assert changed is True
        assert get_console_control_state("dom1") is None

    def test_returns_none_when_unset(self):
        assert get_console_control_state("no-such-domain") is None

    def test_duplicate_read_only_no_change(self):
        set_console_control_state("dom1", state="read_only", message="busy")
        changed = set_console_control_state("dom1", state="read_only", message="busy")
        assert changed is False

    def test_interactive_when_already_interactive_no_change(self):
        """Setting interactive when already interactive should return False."""
        changed = set_console_control_state(
            "dom1", state="interactive", message="done"
        )
        assert changed is False


# =============================================================================
# _clean_config
# =============================================================================


class TestCleanConfig:
    """Output cleaning: ANSI, echo, Building configuration, trim."""

    def test_strips_ansi_sequences(self):
        raw = "\x1b[32mhostname R1\x1b[0m\ninterface Gi0/0"
        result = _clean_config(raw, "show running-config")
        assert "\x1b" not in result
        assert "hostname R1" in result

    def test_removes_command_echo(self):
        raw = "Router# show running-config\nhostname R1\nend"
        result = _clean_config(raw, "show running-config")
        assert "show running-config" not in result
        assert "hostname R1" in result

    def test_removes_building_configuration(self):
        raw = "Building configuration...\nhostname R1\nend"
        result = _clean_config(raw, "show running-config")
        assert "Building configuration" not in result
        assert "hostname R1" in result

    def test_trims_leading_trailing_blanks(self):
        raw = "\n\n\nhostname R1\nend\n\n\n"
        result = _clean_config(raw, "show running-config")
        assert result.startswith("hostname")
        assert result.endswith("end")

    def test_carriage_return_removed(self):
        raw = "hostname R1\r\ninterface Gi0/0\r\n"
        result = _clean_config(raw, "show running-config")
        assert "\r" not in result


# =============================================================================
# _contains_cli_error
# =============================================================================


class TestContainsCliError:
    """Detects common CLI error markers."""

    def test_invalid_input(self):
        assert _contains_cli_error("% Invalid input detected at '^' marker.") is True

    def test_ambiguous_command(self):
        assert _contains_cli_error("% Ambiguous command: 'sh run'") is True

    def test_incomplete_command(self):
        assert _contains_cli_error("% Incomplete command.") is True

    def test_unknown_command(self):
        assert _contains_cli_error("% Unknown command 'foo'") is True

    def test_clean_output(self):
        assert _contains_cli_error("hostname Router1\ninterface Gi0/0") is False

    def test_empty_string(self):
        assert _contains_cli_error("") is False

    def test_none_input(self):
        assert _contains_cli_error(None) is False

    def test_case_insensitive(self):
        assert _contains_cli_error("% INVALID INPUT DETECTED") is True


# =============================================================================
# Piggyback Extract
# =============================================================================


class TestPiggybackExtract:
    """Tests for piggyback_extract orchestrator."""

    def test_returns_none_when_no_session(self):
        result = registry.piggyback_extract("no-session-domain")
        assert result is None

    def test_pauses_and_resumes_pty(self):
        session = _make_session("dom1")
        register_session("dom1", session)

        # After piggyback, events should be set (resumed)
        with patch.object(registry, "PtyInjector") as MockInjector:
            mock_inj = MockInjector.return_value
            mock_inj.expect.return_value = "hostname R1\nend"
            mock_inj.last_match = "#"
            mock_inj.drain = MagicMock(return_value=b"")

            result = registry.piggyback_extract(
                "dom1",
                command="show running-config",
                timeout=5,
            )

        # I/O should be resumed after extraction
        assert session.input_paused.is_set()
        assert session.pty_read_paused.is_set()
        assert result is not None
        assert result.success is True

    def test_handles_timeout(self):
        session = _make_session("dom1")
        register_session("dom1", session)

        with patch.object(registry, "PtyInjector") as MockInjector:
            mock_inj = MockInjector.return_value
            mock_inj.expect.side_effect = TimeoutError("timed out")
            mock_inj.last_match = ""
            mock_inj.drain = MagicMock(return_value=b"")

            result = registry.piggyback_extract("dom1", timeout=1)

        assert result is not None
        assert result.success is False
        assert "timeout" in result.error.lower()

    def test_returns_cleaned_config(self):
        session = _make_session("dom1")
        register_session("dom1", session)

        with patch.object(registry, "PtyInjector") as MockInjector:
            mock_inj = MockInjector.return_value

            call_count = [0]
            def fake_expect(pattern, timeout=None):
                call_count[0] += 1
                if call_count[0] == 1:
                    # First call: prompt detection
                    mock_inj.last_match = "#"
                    return "Router"
                elif call_count[0] == 2:
                    # Second call: paging disable
                    mock_inj.last_match = "#"
                    return ""
                else:
                    # Third call: config output
                    mock_inj.last_match = "#"
                    return "show running-config\nhostname R1\nend"

            mock_inj.expect = fake_expect
            mock_inj.last_match = "#"
            mock_inj.drain = MagicMock(return_value=b"")

            result = registry.piggyback_extract("dom1", timeout=5)

        assert result is not None
        assert result.success is True
        assert "hostname R1" in result.config


# =============================================================================
# Piggyback Run Commands
# =============================================================================


class TestPiggybackRunCommands:
    """Tests for piggyback_run_commands orchestrator."""

    def test_returns_none_when_no_session(self):
        result = registry.piggyback_run_commands("no-session", ["show version"])
        assert result is None

    def test_empty_commands_returns_success(self):
        result = registry.piggyback_run_commands("any-domain", [])
        assert result is not None
        assert result.success is True
        assert result.commands_run == 0

    def test_sends_commands_and_returns_result(self):
        session = _make_session("dom1")
        register_session("dom1", session)

        with patch.object(registry, "PtyInjector") as MockInjector:
            mock_inj = MockInjector.return_value
            mock_inj.last_match = "#"
            mock_inj.drain = MagicMock(return_value=b"")

            call_count = [0]
            def fake_expect(pattern, timeout=None):
                call_count[0] += 1
                mock_inj.last_match = "#"
                return "output"

            mock_inj.expect = fake_expect

            result = registry.piggyback_run_commands(
                "dom1", ["terminal length 0", "show version"], timeout=5,
            )

        assert result is not None
        assert result.success is True
        assert result.commands_run == 2

    def test_handles_cli_error(self):
        session = _make_session("dom1")
        register_session("dom1", session)

        with patch.object(registry, "PtyInjector") as MockInjector:
            mock_inj = MockInjector.return_value
            mock_inj.last_match = "#"
            mock_inj.drain = MagicMock(return_value=b"")

            call_count = [0]
            def fake_expect(pattern, timeout=None):
                call_count[0] += 1
                mock_inj.last_match = "#"
                if call_count[0] <= 1:
                    return ""  # prompt
                return "% Invalid input detected"

            mock_inj.expect = fake_expect

            result = registry.piggyback_run_commands(
                "dom1", ["bad_command"], timeout=5,
            )

        assert result is not None
        assert result.success is False
        assert "CLI rejected" in result.error


# =============================================================================
# Piggyback Run Commands Capture
# =============================================================================


class TestPiggybackRunCommandsCapture:
    """Tests for piggyback_run_commands_capture orchestrator."""

    def test_returns_none_when_no_session(self):
        result = registry.piggyback_run_commands_capture("no-session", ["show version"])
        assert result is None

    def test_empty_commands_returns_success(self):
        result = registry.piggyback_run_commands_capture("any-domain", [])
        assert result is not None
        assert result.success is True

    def test_captures_per_command_output(self):
        session = _make_session("dom1")
        register_session("dom1", session)

        with patch.object(registry, "PtyInjector") as MockInjector:
            mock_inj = MockInjector.return_value
            mock_inj.last_match = "#"
            mock_inj.drain = MagicMock(return_value=b"")

            call_count = [0]
            def fake_expect(pattern, timeout=None):
                call_count[0] += 1
                mock_inj.last_match = "#"
                if call_count[0] <= 2:
                    return ""  # prompt + paging disable
                return "Cisco IOS Software, Version 15.1"

            mock_inj.expect = fake_expect

            result = registry.piggyback_run_commands_capture(
                "dom1", ["show version"], timeout=5,
            )

        assert result is not None
        assert result.success is True
        assert result.commands_run == 1
        assert len(result.outputs) == 1
        assert result.outputs[0].success is True

    def test_handles_timeout_per_command(self):
        session = _make_session("dom1")
        register_session("dom1", session)

        with patch.object(registry, "PtyInjector") as MockInjector:
            mock_inj = MockInjector.return_value
            mock_inj.last_match = "#"
            mock_inj.drain = MagicMock(return_value=b"")

            call_count = [0]
            def fake_expect(pattern, timeout=None):
                call_count[0] += 1
                mock_inj.last_match = "#"
                if call_count[0] <= 2:
                    return ""  # prompt + paging
                raise TimeoutError("command timed out")

            mock_inj.expect = fake_expect

            result = registry.piggyback_run_commands_capture(
                "dom1", ["slow_command"], timeout=1,
            )

        assert result is not None
        # Command timed out but we still get a result
        assert len(result.outputs) == 1
        assert result.outputs[0].success is False
        assert "Timeout" in result.outputs[0].error
