"""Extended tests for agent/providers/libvirt_readiness.py.

Covers:
- extract_probe_markers: parsing markers= payload from details
- classify_console_result: normalizing console outcomes
- check_tcp_port: TCP connectivity check
- run_post_boot_commands: gating, delegation, error handling
- run_n9kv_loader_recovery: retry limits, cooldown, attempts
- run_n9kv_panic_recovery: staleness, log-size checks, force restart
- run_n9kv_poap_skip: one-shot guard, pexpect unavailable
- run_n9kv_admin_password_setup: completion tracking
- check_readiness: orchestration with various probe outcomes
"""

from __future__ import annotations

import time
import types
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.providers.libvirt_readiness import (
    extract_probe_markers,
    classify_console_result,
    check_tcp_port,
    run_post_boot_commands,
    run_n9kv_loader_recovery,
    run_n9kv_panic_recovery,
    run_n9kv_poap_skip,
    run_n9kv_admin_password_setup,
    check_readiness,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass
class FakeCommandOutput:
    output: str = ""
    error: str | None = None


@dataclass
class FakeConsoleResult:
    success: bool = True
    outputs: list = None
    error: str | None = None
    commands_run: int = 0

    def __post_init__(self):
        if self.outputs is None:
            self.outputs = []


# ---------------------------------------------------------------------------
# extract_probe_markers
# ---------------------------------------------------------------------------

class TestExtractProbeMarkers:
    def test_no_details(self):
        assert extract_probe_markers(None) == set()
        assert extract_probe_markers("") == set()

    def test_no_markers_key(self):
        assert extract_probe_markers("some random details") == set()

    def test_markers_none(self):
        assert extract_probe_markers("markers=none") == set()

    def test_single_marker(self):
        result = extract_probe_markers("markers=loader_prompt")
        assert result == {"loader_prompt"}

    def test_multiple_markers(self):
        result = extract_probe_markers("markers=poap_failure,startup_config_ref,bootflash_startup_path")
        assert result == {"poap_failure", "startup_config_ref", "bootflash_startup_path"}

    def test_markers_with_prefix(self):
        result = extract_probe_markers("console_reason=pexpect_output; markers=kernel_panic; tail=...")
        assert result == {"kernel_panic"}

    def test_markers_with_whitespace(self):
        result = extract_probe_markers("markers= loader_prompt , admin_password_prompt ")
        assert result == {"loader_prompt", "admin_password_prompt"}

    def test_empty_markers_value(self):
        assert extract_probe_markers("markers=") == set()


# ---------------------------------------------------------------------------
# classify_console_result
# ---------------------------------------------------------------------------

class TestClassifyConsoleResult:
    def test_success(self):
        result = FakeConsoleResult(success=True)
        assert classify_console_result(result) == "sent"

    def test_timeout(self):
        output = FakeCommandOutput(error="Timeout waiting for command output")
        result = FakeConsoleResult(success=False, outputs=[output])
        assert classify_console_result(result) == "sent_handoff_timeout"

    def test_console_closed(self):
        result = FakeConsoleResult(success=False, error="Console connection closed unexpectedly")
        assert classify_console_result(result) == "sent_console_closed"

    def test_generic_failure(self):
        result = FakeConsoleResult(success=False, error="Something else went wrong")
        assert classify_console_result(result) == "failed"

    def test_no_outputs_no_error(self):
        result = FakeConsoleResult(success=False, outputs=[], error=None)
        assert classify_console_result(result) == "failed"


# ---------------------------------------------------------------------------
# check_tcp_port
# ---------------------------------------------------------------------------

class TestCheckTcpPort:
    def test_connection_refused(self):
        # Use a port that's very unlikely to be open
        assert check_tcp_port("127.0.0.1", 59999, 0.1) is False

    def test_invalid_host(self):
        assert check_tcp_port("192.0.2.1", 22, 0.1) is False

    def test_zero_timeout(self):
        assert check_tcp_port("127.0.0.1", 59999, 0.001) is False


# ---------------------------------------------------------------------------
# run_n9kv_loader_recovery
# ---------------------------------------------------------------------------

class TestRunN9kvLoaderRecovery:
    @pytest.mark.asyncio
    async def test_max_attempts_reached(self):
        attempts = {"dom1": 5}
        last_at = {}
        result = await run_n9kv_loader_recovery(
            "dom1", "cisco_n9kv", "qemu:///system",
            recovery_attempts=attempts,
            recovery_last_at=last_at,
            max_attempts=5,
            cooldown=30.0,
        )
        assert result == "skipped_max_attempts"

    @pytest.mark.asyncio
    async def test_cooldown_active(self):
        attempts = {"dom1": 2}
        last_at = {"dom1": time.monotonic()}
        result = await run_n9kv_loader_recovery(
            "dom1", "cisco_n9kv", "qemu:///system",
            recovery_attempts=attempts,
            recovery_last_at=last_at,
            max_attempts=5,
            cooldown=30.0,
        )
        assert result == "skipped_cooldown"

    @pytest.mark.asyncio
    async def test_pexpect_unavailable(self):
        attempts = {}
        last_at = {}
        with patch("agent.providers.libvirt_readiness.run_vm_cli_commands", create=True), \
             patch("agent.providers.libvirt_readiness.PEXPECT_AVAILABLE", False, create=True):
            # Patch the import to return PEXPECT_AVAILABLE=False
            fake_mod = types.ModuleType("agent.console_extractor")
            fake_mod.run_vm_cli_commands = MagicMock()
            fake_mod.PEXPECT_AVAILABLE = False
            with patch.dict("sys.modules", {"agent.console_extractor": fake_mod}):
                result = await run_n9kv_loader_recovery(
                    "dom1", "cisco_n9kv", "qemu:///system",
                    recovery_attempts=attempts,
                    recovery_last_at=last_at,
                    max_attempts=5,
                    cooldown=30.0,
                )
        assert result == "skipped_pexpect_unavailable"

    @pytest.mark.asyncio
    async def test_exception_increments_attempts(self):
        attempts = {}
        last_at = {}

        fake_mod = types.ModuleType("agent.console_extractor")
        fake_mod.run_vm_cli_commands = MagicMock(side_effect=RuntimeError("boom"))
        fake_mod.PEXPECT_AVAILABLE = True
        with patch.dict("sys.modules", {"agent.console_extractor": fake_mod}):
            result = await run_n9kv_loader_recovery(
                "dom1", "cisco_n9kv", "qemu:///system",
                recovery_attempts=attempts,
                recovery_last_at=last_at,
                max_attempts=5,
                cooldown=30.0,
            )
        assert result == "error"
        assert attempts["dom1"] == 1

    @pytest.mark.asyncio
    async def test_success_increments_attempts(self):
        attempts = {}
        last_at = {}

        fake_result = FakeConsoleResult(success=True, commands_run=1)

        fake_mod = types.ModuleType("agent.console_extractor")
        fake_mod.run_vm_cli_commands = MagicMock(return_value=fake_result)
        fake_mod.PEXPECT_AVAILABLE = True
        with patch.dict("sys.modules", {"agent.console_extractor": fake_mod}):
            result = await run_n9kv_loader_recovery(
                "dom1", "cisco_n9kv", "qemu:///system",
                recovery_attempts=attempts,
                recovery_last_at=last_at,
                max_attempts=5,
                cooldown=30.0,
            )
        assert result == "sent"
        assert attempts["dom1"] == 1


# ---------------------------------------------------------------------------
# run_n9kv_panic_recovery
# ---------------------------------------------------------------------------

class TestRunN9kvPanicRecovery:
    @pytest.mark.asyncio
    async def test_max_attempts_reached(self):
        result = await run_n9kv_panic_recovery(
            "dom1", "cisco_n9kv", "/tmp/serial.log",
            run_libvirt_fn=AsyncMock(),
            conn=MagicMock(),
            panic_attempts={"dom1": 3},
            panic_last_at={},
            panic_last_log_size={},
            max_attempts=3,
            cooldown=60.0,
        )
        assert result == "skipped_max_attempts"

    @pytest.mark.asyncio
    async def test_cooldown_active(self):
        result = await run_n9kv_panic_recovery(
            "dom1", "cisco_n9kv", "/tmp/serial.log",
            run_libvirt_fn=AsyncMock(),
            conn=MagicMock(),
            panic_attempts={"dom1": 1},
            panic_last_at={"dom1": time.monotonic()},
            panic_last_log_size={"dom1": 100},
            max_attempts=3,
            cooldown=60.0,
        )
        assert result == "skipped_cooldown"

    @pytest.mark.asyncio
    async def test_first_detection_records_size(self, tmp_path):
        log_file = tmp_path / "serial.log"
        log_file.write_text("kernel panic output here")
        log_sizes = {}

        result = await run_n9kv_panic_recovery(
            "dom1", "cisco_n9kv", str(log_file),
            run_libvirt_fn=AsyncMock(),
            conn=MagicMock(),
            panic_attempts={},
            panic_last_at={},
            panic_last_log_size=log_sizes,
            max_attempts=3,
            cooldown=60.0,
        )
        assert result == "skipped_first_detection"
        assert "dom1" in log_sizes

    @pytest.mark.asyncio
    async def test_log_still_growing(self, tmp_path):
        log_file = tmp_path / "serial.log"
        log_file.write_text("kernel panic output here, more data")
        log_sizes = {"dom1": 10}  # smaller than current

        result = await run_n9kv_panic_recovery(
            "dom1", "cisco_n9kv", str(log_file),
            run_libvirt_fn=AsyncMock(),
            conn=MagicMock(),
            panic_attempts={},
            panic_last_at={},
            panic_last_log_size=log_sizes,
            max_attempts=3,
            cooldown=60.0,
        )
        assert result == "skipped_log_growing"

    @pytest.mark.asyncio
    async def test_stuck_vm_restarted(self, tmp_path):
        log_file = tmp_path / "serial.log"
        log_file.write_text("kernel panic")
        file_size = log_file.stat().st_size

        panic_attempts = {}
        panic_last_at = {}
        panic_last_log_size = {"dom1": file_size}  # same size = stuck

        result = await run_n9kv_panic_recovery(
            "dom1", "cisco_n9kv", str(log_file),
            run_libvirt_fn=AsyncMock(),
            conn=MagicMock(),
            panic_attempts=panic_attempts,
            panic_last_at=panic_last_at,
            panic_last_log_size=panic_last_log_size,
            max_attempts=3,
            cooldown=60.0,
        )
        assert result == "restarted"
        assert panic_attempts["dom1"] == 1
        assert "dom1" not in panic_last_log_size  # cleared after restart

    @pytest.mark.asyncio
    async def test_restart_failure(self, tmp_path):
        log_file = tmp_path / "serial.log"
        log_file.write_text("kernel panic")
        file_size = log_file.stat().st_size

        async def failing_libvirt_fn(fn, *args):
            raise RuntimeError("libvirt error")

        panic_attempts = {}
        result = await run_n9kv_panic_recovery(
            "dom1", "cisco_n9kv", str(log_file),
            run_libvirt_fn=failing_libvirt_fn,
            conn=MagicMock(),
            panic_attempts=panic_attempts,
            panic_last_at={},
            panic_last_log_size={"dom1": file_size},
            max_attempts=3,
            cooldown=60.0,
        )
        assert result == "error"
        assert panic_attempts["dom1"] == 1


# ---------------------------------------------------------------------------
# run_n9kv_poap_skip
# ---------------------------------------------------------------------------

class TestRunN9kvPoapSkip:
    @pytest.mark.asyncio
    async def test_already_attempted(self):
        attempted = {"dom1"}
        result = await run_n9kv_poap_skip(
            "dom1", "cisco_n9kv", "qemu:///system",
            poap_skip_attempted=attempted,
        )
        assert result == "skipped_already_attempted"

    @pytest.mark.asyncio
    async def test_pexpect_unavailable(self):
        attempted = set()
        fake_mod = types.ModuleType("agent.console_extractor")
        fake_mod.run_vm_cli_commands = MagicMock()
        fake_mod.PEXPECT_AVAILABLE = False
        with patch.dict("sys.modules", {"agent.console_extractor": fake_mod}):
            result = await run_n9kv_poap_skip(
                "dom1", "cisco_n9kv", "qemu:///system",
                poap_skip_attempted=attempted,
            )
        assert result == "skipped_pexpect_unavailable"
        # Still marked attempted even though pexpect unavailable
        assert "dom1" in attempted

    @pytest.mark.asyncio
    async def test_success(self):
        attempted = set()
        fake_result = FakeConsoleResult(success=True, commands_run=1)
        fake_mod = types.ModuleType("agent.console_extractor")
        fake_mod.run_vm_cli_commands = MagicMock(return_value=fake_result)
        fake_mod.PEXPECT_AVAILABLE = True
        with patch.dict("sys.modules", {"agent.console_extractor": fake_mod}):
            result = await run_n9kv_poap_skip(
                "dom1", "cisco_n9kv", "qemu:///system",
                poap_skip_attempted=attempted,
            )
        assert result == "sent"
        assert "dom1" in attempted

    @pytest.mark.asyncio
    async def test_exception_returns_error(self):
        attempted = set()
        fake_mod = types.ModuleType("agent.console_extractor")
        fake_mod.run_vm_cli_commands = MagicMock(side_effect=RuntimeError("connection failed"))
        fake_mod.PEXPECT_AVAILABLE = True
        with patch.dict("sys.modules", {"agent.console_extractor": fake_mod}):
            result = await run_n9kv_poap_skip(
                "dom1", "cisco_n9kv", "qemu:///system",
                poap_skip_attempted=attempted,
            )
        assert result == "error"


# ---------------------------------------------------------------------------
# run_n9kv_admin_password_setup
# ---------------------------------------------------------------------------

class TestRunN9kvAdminPasswordSetup:
    @pytest.mark.asyncio
    async def test_already_completed(self):
        completed = {"dom1"}
        result = await run_n9kv_admin_password_setup(
            "dom1", "cisco_n9kv", "qemu:///system",
            admin_password_completed=completed,
        )
        assert result == "skipped_already_completed"

    @pytest.mark.asyncio
    async def test_pexpect_unavailable(self):
        completed = set()
        fake_mod = types.ModuleType("agent.console_extractor")
        fake_mod.run_vm_cli_commands = MagicMock()
        fake_mod.PEXPECT_AVAILABLE = False
        with patch.dict("sys.modules", {"agent.console_extractor": fake_mod}):
            result = await run_n9kv_admin_password_setup(
                "dom1", "cisco_n9kv", "qemu:///system",
                admin_password_completed=completed,
            )
        assert result == "skipped_pexpect_unavailable"

    @pytest.mark.asyncio
    async def test_success_marks_completed(self):
        completed = set()
        fake_result = FakeConsoleResult(success=True, commands_run=1)
        fake_mod = types.ModuleType("agent.console_extractor")
        fake_mod.run_vm_cli_commands = MagicMock(return_value=fake_result)
        fake_mod.PEXPECT_AVAILABLE = True
        with patch.dict("sys.modules", {"agent.console_extractor": fake_mod}):
            result = await run_n9kv_admin_password_setup(
                "dom1", "cisco_n9kv", "qemu:///system",
                admin_password_completed=completed,
            )
        assert result == "sent"
        assert "dom1" in completed

    @pytest.mark.asyncio
    async def test_failure_does_not_mark_completed(self):
        completed = set()
        fake_result = FakeConsoleResult(success=False, error="failed")
        fake_mod = types.ModuleType("agent.console_extractor")
        fake_mod.run_vm_cli_commands = MagicMock(return_value=fake_result)
        fake_mod.PEXPECT_AVAILABLE = True
        with patch.dict("sys.modules", {"agent.console_extractor": fake_mod}):
            result = await run_n9kv_admin_password_setup(
                "dom1", "cisco_n9kv", "qemu:///system",
                admin_password_completed=completed,
            )
        assert result == "failed"
        assert "dom1" not in completed


# ---------------------------------------------------------------------------
# run_post_boot_commands
# ---------------------------------------------------------------------------

class TestRunPostBootCommands:
    @pytest.mark.asyncio
    async def test_n9kv_disabled_skips(self, monkeypatch):
        """N9Kv with boot mutations disabled should skip and clear state."""
        import agent.config
        monkeypatch.setattr(agent.config.settings, "n9kv_boot_modifications_enabled", False)

        cleared = []

        result = await run_post_boot_commands(
            "dom1", "cisco_n9kv", "qemu:///system",
            canonical_kind_fn=lambda k: "cisco_n9kv",
            clear_console_state_fn=lambda d: cleared.append(d),
        )
        assert result is True
        assert "dom1" in cleared

    @pytest.mark.asyncio
    async def test_non_n9kv_with_pexpect_unavailable(self, monkeypatch):
        """Non-N9Kv device with pexpect unavailable returns True (skip)."""
        import agent.config
        monkeypatch.setattr(agent.config.settings, "n9kv_boot_modifications_enabled", True)

        fake_mod = types.ModuleType("agent.console_extractor")
        fake_mod.run_vm_post_boot_commands = MagicMock()
        fake_mod.PEXPECT_AVAILABLE = False
        with patch.dict("sys.modules", {"agent.console_extractor": fake_mod}):
            result = await run_post_boot_commands(
                "dom1", "cisco_iosv", "qemu:///system",
                canonical_kind_fn=lambda k: "cisco_iosv",
                clear_console_state_fn=lambda d: None,
            )
        assert result is True

    @pytest.mark.asyncio
    async def test_exception_returns_false(self, monkeypatch):
        """Exception during post-boot commands returns False."""
        import agent.config
        monkeypatch.setattr(agent.config.settings, "n9kv_boot_modifications_enabled", True)

        fake_mod = types.ModuleType("agent.console_extractor")
        fake_mod.run_vm_post_boot_commands = MagicMock(side_effect=RuntimeError("timeout"))
        fake_mod.PEXPECT_AVAILABLE = True
        with patch.dict("sys.modules", {"agent.console_extractor": fake_mod}):
            result = await run_post_boot_commands(
                "dom1", "cisco_iosv", "qemu:///system",
                canonical_kind_fn=lambda k: "cisco_iosv",
                clear_console_state_fn=lambda d: None,
            )
        assert result is False


# ---------------------------------------------------------------------------
# check_readiness
# ---------------------------------------------------------------------------

class TestCheckReadiness:
    @pytest.mark.asyncio
    async def test_libvirt_not_available(self, monkeypatch):
        """Returns not ready when libvirt is unavailable."""
        # Force ImportError for libvirt
        with patch.dict("sys.modules", {"libvirt": None}):
            # The function tries `import libvirt` which will fail
            result = await check_readiness(
                lab_id="lab1",
                node_name="r1",
                kind="cisco_n9kv",
                domain_name="arch-lab1-r1",
                uri="qemu:///system",
                run_libvirt_fn=AsyncMock(return_value=None),
                check_readiness_domain_sync_fn=MagicMock(),
                get_vm_management_ip_fn=AsyncMock(),
                canonical_kind_fn=lambda k: k,
                clear_console_state_fn=lambda d: None,
                loader_recovery_attempts={},
                loader_recovery_last_at={},
                loader_max_attempts=5,
                loader_cooldown=30.0,
                panic_recovery_attempts={},
                panic_recovery_last_at={},
                panic_last_log_size={},
                panic_max_attempts=3,
                panic_cooldown=60.0,
                poap_skip_attempted=set(),
                admin_password_completed=set(),
                conn=MagicMock(),
            )
        # Domain not found since run_libvirt_fn returns None
        assert result.is_ready is False

    @pytest.mark.asyncio
    async def test_domain_not_found(self, monkeypatch):
        """Returns not ready when domain lookup returns None."""
        fake_libvirt = types.ModuleType("libvirt")
        fake_libvirt.VIR_DOMAIN_RUNNING = 1
        monkeypatch.setitem(__import__("sys").modules, "libvirt", fake_libvirt)

        result = await check_readiness(
            lab_id="lab1",
            node_name="r1",
            kind="cisco_iosv",
            domain_name="arch-lab1-r1",
            uri="qemu:///system",
            run_libvirt_fn=AsyncMock(return_value=None),
            check_readiness_domain_sync_fn=MagicMock(),
            get_vm_management_ip_fn=AsyncMock(),
            canonical_kind_fn=lambda k: k,
            clear_console_state_fn=lambda d: None,
            loader_recovery_attempts={},
            loader_recovery_last_at={},
            loader_max_attempts=5,
            loader_cooldown=30.0,
            panic_recovery_attempts={},
            panic_recovery_last_at={},
            panic_last_log_size={},
            panic_max_attempts=3,
            panic_cooldown=60.0,
            poap_skip_attempted=set(),
            admin_password_completed=set(),
            conn=MagicMock(),
        )
        assert result.is_ready is False
        assert "not found" in result.message

    @pytest.mark.asyncio
    async def test_vm_not_running(self, monkeypatch):
        """Returns not ready when VM is not in running state."""
        fake_libvirt = types.ModuleType("libvirt")
        fake_libvirt.VIR_DOMAIN_RUNNING = 1
        monkeypatch.setitem(__import__("sys").modules, "libvirt", fake_libvirt)

        result = await check_readiness(
            lab_id="lab1",
            node_name="r1",
            kind="cisco_iosv",
            domain_name="arch-lab1-r1",
            uri="qemu:///system",
            run_libvirt_fn=AsyncMock(return_value=(3, {})),  # state=3 (shutdown)
            check_readiness_domain_sync_fn=MagicMock(),
            get_vm_management_ip_fn=AsyncMock(),
            canonical_kind_fn=lambda k: k,
            clear_console_state_fn=lambda d: None,
            loader_recovery_attempts={},
            loader_recovery_last_at={},
            loader_max_attempts=5,
            loader_cooldown=30.0,
            panic_recovery_attempts={},
            panic_recovery_last_at={},
            panic_last_log_size={},
            panic_max_attempts=3,
            panic_cooldown=60.0,
            poap_skip_attempted=set(),
            admin_password_completed=set(),
            conn=MagicMock(),
        )
        assert result.is_ready is False
        assert "not running" in result.message.lower()
