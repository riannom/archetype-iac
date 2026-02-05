"""Unit tests for boot readiness detection and post-boot commands.

Tests verify:
1. LogPatternProbe correctly detects boot completion patterns
2. run_post_boot_commands() is idempotent (only runs once per container)
3. clear_post_boot_state() resets tracking for container restarts
4. get_probe_for_vendor() returns appropriate probe types
"""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock
import asyncio

from agent.readiness import (
    ReadinessResult,
    NoopProbe,
    LogPatternProbe,
    CliProbe,
    get_probe_for_vendor,
    get_readiness_timeout,
    run_post_boot_commands,
    clear_post_boot_state,
    clear_all_post_boot_state,
    _post_boot_completed,
    CEOS_PROGRESS_PATTERNS,
)
from agent.vendors import get_vendor_config


# --- NoopProbe Tests ---

class TestNoopProbe:
    """Tests for NoopProbe that always returns ready."""

    @pytest.mark.asyncio
    async def test_noop_probe_always_ready(self):
        """NoopProbe always returns ready=True."""
        probe = NoopProbe()
        result = await probe.check("any-container")
        assert result.is_ready is True
        assert "No readiness probe" in result.message


# --- LogPatternProbe Tests ---

class TestLogPatternProbe:
    """Tests for LogPatternProbe that checks container logs."""

    @pytest.mark.asyncio
    async def test_log_pattern_matches(self):
        """Probe returns ready when pattern is found in logs."""
        probe = LogPatternProbe(pattern=r"System ready")

        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.logs.return_value = b"Starting up...\nSystem ready\nListening..."

        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container

        with patch("agent.readiness.docker.from_env", return_value=mock_client):
            result = await probe.check("test-container")

        assert result.is_ready is True
        assert result.progress_percent == 100

    @pytest.mark.asyncio
    async def test_log_pattern_not_found(self):
        """Probe returns not ready when pattern is not in logs."""
        probe = LogPatternProbe(pattern=r"System ready")

        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.logs.return_value = b"Starting up...\nBooting...\nWaiting..."

        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container

        with patch("agent.readiness.docker.from_env", return_value=mock_client):
            result = await probe.check("test-container")

        assert result.is_ready is False

    @pytest.mark.asyncio
    async def test_log_pattern_case_insensitive(self):
        """Pattern matching is case-insensitive."""
        probe = LogPatternProbe(pattern=r"SYSTEM READY")

        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.logs.return_value = b"system ready"

        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container

        with patch("agent.readiness.docker.from_env", return_value=mock_client):
            result = await probe.check("test-container")

        assert result.is_ready is True

    @pytest.mark.asyncio
    async def test_log_pattern_progress_tracking(self):
        """Progress patterns report intermediate progress."""
        progress_patterns = {
            r"Phase 1": 25,
            r"Phase 2": 50,
            r"Phase 3": 75,
        }
        probe = LogPatternProbe(pattern=r"Complete", progress_patterns=progress_patterns)

        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.logs.return_value = b"Phase 1 done\nPhase 2 done\nWaiting..."

        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container

        with patch("agent.readiness.docker.from_env", return_value=mock_client):
            result = await probe.check("test-container")

        assert result.is_ready is False
        assert result.progress_percent == 50  # Max of matched patterns

    @pytest.mark.asyncio
    async def test_container_not_running(self):
        """Returns not ready when container is not running."""
        probe = LogPatternProbe(pattern=r"Ready")

        mock_container = MagicMock()
        mock_container.status = "exited"

        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container

        with patch("agent.readiness.docker.from_env", return_value=mock_client):
            result = await probe.check("test-container")

        assert result.is_ready is False
        assert "not running" in result.message.lower()

    @pytest.mark.asyncio
    async def test_container_not_found(self):
        """Returns not ready when container doesn't exist."""
        import docker.errors

        probe = LogPatternProbe(pattern=r"Ready")

        mock_client = MagicMock()
        mock_client.containers.get.side_effect = docker.errors.NotFound("not found")

        with patch("agent.readiness.docker.from_env", return_value=mock_client):
            result = await probe.check("missing-container")

        assert result.is_ready is False
        assert "not found" in result.message.lower()


# --- CliProbe Tests ---

class TestCliProbe:
    """Tests for CliProbe that executes commands in container."""

    @pytest.mark.asyncio
    async def test_cli_probe_success(self):
        """Returns ready when CLI command succeeds and output matches."""
        probe = CliProbe(cli_command="show version", expected_pattern=r"Arista")

        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.exec_run.return_value = (0, b"Arista vEOS version 4.28")

        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container

        with patch("agent.readiness.docker.from_env", return_value=mock_client):
            result = await probe.check("test-container")

        assert result.is_ready is True

    @pytest.mark.asyncio
    async def test_cli_probe_command_fails(self):
        """Returns not ready when CLI command fails."""
        probe = CliProbe(cli_command="show version", expected_pattern=r"Arista")

        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.exec_run.return_value = (1, b"Error: CLI not available")

        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container

        with patch("agent.readiness.docker.from_env", return_value=mock_client):
            result = await probe.check("test-container")

        assert result.is_ready is False

    @pytest.mark.asyncio
    async def test_cli_probe_pattern_not_matched(self):
        """Returns not ready when output doesn't match pattern."""
        probe = CliProbe(cli_command="show version", expected_pattern=r"Arista")

        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.exec_run.return_value = (0, b"Nokia SR Linux version 23.10")

        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container

        with patch("agent.readiness.docker.from_env", return_value=mock_client):
            result = await probe.check("test-container")

        assert result.is_ready is False


# --- get_probe_for_vendor Tests ---

class TestGetProbeForVendor:
    """Tests for vendor-specific probe selection."""

    def test_ceos_returns_log_pattern_probe(self):
        """cEOS should use LogPatternProbe."""
        probe = get_probe_for_vendor("ceos")
        assert isinstance(probe, LogPatternProbe)

    @pytest.mark.asyncio
    async def test_ceos_progress_patterns_applied(self):
        """cEOS probe should report progress when log patterns match."""
        probe = get_probe_for_vendor("ceos")

        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.logs.return_value = b"ZTP starting..."

        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container

        with patch("agent.readiness.docker.from_env", return_value=mock_client):
            result = await probe.check("test-ceos")

        assert result.is_ready is False
        assert result.progress_percent == CEOS_PROGRESS_PATTERNS[r\"ZTP|zerotouch\"]

    def test_ceos_alias_not_resolved_by_get_vendor_config(self):
        """get_vendor_config doesn't resolve aliases - use get_config_by_device for that."""
        # get_vendor_config does direct lookup only, not alias resolution
        # This documents current behavior - aliases need explicit resolution
        probe = get_probe_for_vendor("eos")
        # Returns NoopProbe because "eos" isn't a direct key in VENDOR_CONFIGS
        assert isinstance(probe, NoopProbe)

    def test_linux_returns_noop_probe(self):
        """Linux containers use NoopProbe (no readiness check)."""
        probe = get_probe_for_vendor("linux")
        assert isinstance(probe, NoopProbe)

    def test_unknown_kind_returns_noop_probe(self):
        """Unknown device kinds fall back to NoopProbe."""
        probe = get_probe_for_vendor("unknown_device")
        assert isinstance(probe, NoopProbe)

    def test_nokia_srlinux_returns_log_pattern_probe(self):
        """Nokia SR Linux should use LogPatternProbe."""
        probe = get_probe_for_vendor("nokia_srlinux")
        assert isinstance(probe, LogPatternProbe)


# --- get_readiness_timeout Tests ---

class TestGetReadinessTimeout:
    """Tests for vendor-specific readiness timeouts."""

    def test_ceos_timeout(self):
        """cEOS has extended timeout (slow boot)."""
        timeout = get_readiness_timeout("ceos")
        assert timeout >= 120  # cEOS takes at least 2 minutes

    def test_linux_timeout(self):
        """Linux has default timeout."""
        timeout = get_readiness_timeout("linux")
        assert timeout == 120  # Default

    def test_unknown_kind_default_timeout(self):
        """Unknown kinds get default timeout."""
        timeout = get_readiness_timeout("unknown")
        assert timeout == 120


# --- Post-boot Commands Tests ---

class TestPostBootCommands:
    """Tests for post-boot command execution."""

    @pytest.fixture(autouse=True)
    def clear_state(self):
        """Clear post-boot state before and after each test."""
        clear_all_post_boot_state()
        yield
        clear_all_post_boot_state()

    @pytest.mark.asyncio
    async def test_commands_run_once(self):
        """Post-boot commands only run once per container."""
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.exec_run.return_value = (0, b"OK")

        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container

        with patch("agent.readiness.docker.from_env", return_value=mock_client):
            # First call should run commands
            result1 = await run_post_boot_commands("test-container", "ceos")
            assert result1 is True
            assert mock_container.exec_run.called

            # Reset mock to track second call
            mock_container.exec_run.reset_mock()

            # Second call should skip (idempotent)
            result2 = await run_post_boot_commands("test-container", "ceos")
            assert result2 is True
            assert not mock_container.exec_run.called  # Should not call again

    @pytest.mark.asyncio
    async def test_clear_state_allows_rerun(self):
        """Clearing state allows commands to run again."""
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.exec_run.return_value = (0, b"OK")

        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container

        with patch("agent.readiness.docker.from_env", return_value=mock_client):
            # First run
            await run_post_boot_commands("test-container", "ceos")
            assert mock_container.exec_run.called

            mock_container.exec_run.reset_mock()

            # Clear state for this container
            clear_post_boot_state("test-container")

            # Should run again after clear
            await run_post_boot_commands("test-container", "ceos")
            assert mock_container.exec_run.called

    @pytest.mark.asyncio
    async def test_no_commands_for_linux(self):
        """Linux containers have no post-boot commands."""
        mock_container = MagicMock()
        mock_container.status = "running"

        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container

        with patch("agent.readiness.docker.from_env", return_value=mock_client):
            result = await run_post_boot_commands("linux-container", "linux")
            assert result is True
            # Should not call exec_run since linux has no post_boot_commands
            assert not mock_container.exec_run.called

    @pytest.mark.asyncio
    async def test_ceos_iptables_commands_executed(self):
        """cEOS post-boot commands should include iptables cleanup."""
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.exec_run.return_value = (0, b"OK")

        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container

        config = get_vendor_config("ceos")
        assert config and config.post_boot_commands

        with patch("agent.readiness.docker.from_env", return_value=mock_client):
            result = await run_post_boot_commands("test-ceos", "ceos")
            assert result is True

        mock_container.exec_run.assert_any_call(config.post_boot_commands[0])

    @pytest.mark.asyncio
    async def test_container_not_running(self):
        """Returns False if container is not running."""
        mock_container = MagicMock()
        mock_container.status = "exited"

        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container

        with patch("agent.readiness.docker.from_env", return_value=mock_client):
            result = await run_post_boot_commands("stopped-container", "ceos")
            assert result is False

    @pytest.mark.asyncio
    async def test_container_not_found(self):
        """Returns False if container doesn't exist."""
        import docker.errors

        mock_client = MagicMock()
        mock_client.containers.get.side_effect = docker.errors.NotFound("not found")

        with patch("agent.readiness.docker.from_env", return_value=mock_client):
            result = await run_post_boot_commands("missing", "ceos")
            assert result is False

    @pytest.mark.asyncio
    async def test_command_failure_continues(self):
        """Non-zero exit codes are logged but don't stop execution."""
        mock_container = MagicMock()
        mock_container.status = "running"
        # Simulate command failure (e.g., iptables rule doesn't exist)
        mock_container.exec_run.return_value = (1, b"Rule not found")

        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container

        with patch("agent.readiness.docker.from_env", return_value=mock_client):
            # Should still return True (commands executed, even if some failed)
            result = await run_post_boot_commands("test-container", "ceos")
            assert result is True


# --- State Management Tests ---

class TestStateManagement:
    """Tests for post-boot state tracking."""

    @pytest.fixture(autouse=True)
    def clear_state(self):
        """Clear state before and after each test."""
        clear_all_post_boot_state()
        yield
        clear_all_post_boot_state()

    def test_clear_single_container(self):
        """clear_post_boot_state removes only specified container."""
        _post_boot_completed.add("container-1")
        _post_boot_completed.add("container-2")

        clear_post_boot_state("container-1")

        assert "container-1" not in _post_boot_completed
        assert "container-2" in _post_boot_completed

    def test_clear_all_containers(self):
        """clear_all_post_boot_state removes all containers."""
        _post_boot_completed.add("container-1")
        _post_boot_completed.add("container-2")
        _post_boot_completed.add("container-3")

        clear_all_post_boot_state()

        assert len(_post_boot_completed) == 0

    def test_clear_nonexistent_container(self):
        """Clearing nonexistent container doesn't raise."""
        _post_boot_completed.add("existing")

        # Should not raise
        clear_post_boot_state("nonexistent")

        assert "existing" in _post_boot_completed


# --- cEOS Progress Patterns Tests ---

class TestCeosProgressPatterns:
    """Tests for cEOS-specific progress detection patterns."""

    def test_progress_patterns_defined(self):
        """cEOS has progress patterns for boot stages."""
        assert len(CEOS_PROGRESS_PATTERNS) > 0

    def test_progress_patterns_increasing(self):
        """Progress percentages should be in reasonable range."""
        for pattern, percent in CEOS_PROGRESS_PATTERNS.items():
            assert 0 < percent < 100
            assert isinstance(pattern, str)

    @pytest.mark.asyncio
    async def test_ceos_probe_has_progress_patterns(self):
        """cEOS LogPatternProbe should include progress patterns."""
        probe = get_probe_for_vendor("ceos")
        assert isinstance(probe, LogPatternProbe)
        assert len(probe._compiled_progress) > 0
