"""Boot readiness detection for network devices.

This module provides a framework for detecting when network devices have
completed their boot sequence and are ready for console interaction.

The problem: Container status "running" doesn't mean the application inside
is ready. Network devices like cEOS take 30-60+ seconds after container start
to complete their boot configuration (zerotouch, AAA, management API, etc.).

Solution: Vendor-configurable readiness probes that check for boot completion
patterns in container logs or via CLI commands.

Post-boot commands: Some vendors require workarounds after boot (e.g., cEOS
needs iptables rules removed). These are run once when readiness is first
detected, tracked via _post_boot_completed set.
"""

from __future__ import annotations

import asyncio
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

import docker

from agent.vendors import get_vendor_config, is_ceos_kind


logger = logging.getLogger(__name__)

# Track containers that have had post-boot commands executed
# Key: container_name, Value: True if commands have been run
_post_boot_completed: set[str] = set()


@dataclass
class ReadinessResult:
    """Result of a readiness probe check."""

    is_ready: bool
    message: str = ""
    progress_percent: Optional[int] = None  # 0-100, None if unknown


class ReadinessProbe(ABC):
    """Base class for readiness probes."""

    @abstractmethod
    async def check(self, container_name: str) -> ReadinessResult:
        """Check if the container's application is ready.

        Args:
            container_name: Docker container name to check

        Returns:
            ReadinessResult with ready status and optional progress
        """
        pass


class NoopProbe(ReadinessProbe):
    """No-op probe that always returns ready.

    Used for devices that don't need readiness detection.
    """

    async def check(self, container_name: str) -> ReadinessResult:
        return ReadinessResult(is_ready=True, message="No readiness probe configured")


class LogPatternProbe(ReadinessProbe):
    """Check container logs for boot completion patterns.

    This probe tails the container's stdout/stderr logs and searches
    for vendor-specific patterns that indicate boot completion.
    """

    def __init__(self, pattern: str, progress_patterns: Optional[dict[str, int]] = None):
        """Initialize log pattern probe.

        Args:
            pattern: Regex pattern to match for boot completion
            progress_patterns: Optional dict of pattern -> progress percent
                              for intermediate progress tracking
        """
        self.pattern = re.compile(pattern, re.IGNORECASE)
        self.progress_patterns = progress_patterns or {}
        # Compile progress patterns
        self._compiled_progress = {
            re.compile(p, re.IGNORECASE): pct
            for p, pct in self.progress_patterns.items()
        }

    async def check(self, container_name: str) -> ReadinessResult:
        """Check container logs for readiness pattern."""
        def _sync_check() -> ReadinessResult:
            try:
                client = docker.from_env()
                container = client.containers.get(container_name)

                if container.status != "running":
                    return ReadinessResult(
                        is_ready=False,
                        message=f"Container not running: {container.status}",
                        progress_percent=0,
                    )

                # Get recent logs (last 500 lines should be enough)
                logs = container.logs(tail=500, timestamps=False).decode("utf-8", errors="replace")

                # Check for completion pattern
                if self.pattern.search(logs):
                    return ReadinessResult(
                        is_ready=True,
                        message="Boot complete",
                        progress_percent=100,
                    )

                # Check for progress patterns
                max_progress = 0
                for compiled_pattern, progress in self._compiled_progress.items():
                    if compiled_pattern.search(logs):
                        max_progress = max(max_progress, progress)

                return ReadinessResult(
                    is_ready=False,
                    message="Boot in progress",
                    progress_percent=max_progress if max_progress > 0 else None,
                )

            except docker.errors.NotFound:
                return ReadinessResult(
                    is_ready=False,
                    message="Container not found",
                    progress_percent=0,
                )
            except Exception as e:
                return ReadinessResult(
                    is_ready=False,
                    message=f"Probe error: {str(e)}",
                )

        return await asyncio.to_thread(_sync_check)


class CliProbe(ReadinessProbe):
    """Check readiness by executing a CLI command and checking output.

    This probe executes a command inside the container and checks if
    the output matches an expected pattern. Useful for devices where
    log parsing isn't reliable.
    """

    def __init__(self, cli_command: str, expected_pattern: str):
        """Initialize CLI probe.

        Args:
            cli_command: Command to execute in container
            expected_pattern: Regex pattern expected in output when ready
        """
        self.cli_command = cli_command
        self.expected_pattern = re.compile(expected_pattern, re.IGNORECASE)

    async def check(self, container_name: str) -> ReadinessResult:
        """Execute CLI command and check output."""
        def _sync_check() -> ReadinessResult:
            try:
                client = docker.from_env()
                container = client.containers.get(container_name)

                if container.status != "running":
                    return ReadinessResult(
                        is_ready=False,
                        message=f"Container not running: {container.status}",
                        progress_percent=0,
                    )

                # Execute command with short timeout
                exit_code, output = container.exec_run(
                    self.cli_command,
                    demux=False,
                )

                output_str = output.decode("utf-8", errors="replace") if output else ""

                if exit_code == 0 and self.expected_pattern.search(output_str):
                    return ReadinessResult(
                        is_ready=True,
                        message="CLI probe successful",
                        progress_percent=100,
                    )

                return ReadinessResult(
                    is_ready=False,
                    message="CLI not ready",
                )

            except docker.errors.NotFound:
                return ReadinessResult(
                    is_ready=False,
                    message="Container not found",
                    progress_percent=0,
                )
            except Exception as e:
                return ReadinessResult(
                    is_ready=False,
                    message=f"CLI probe error: {str(e)}",
                )

        return await asyncio.to_thread(_sync_check)


# Progress patterns for cEOS boot sequence
CEOS_PROGRESS_PATTERNS = {
    r"ZTP|zerotouch": 20,
    r"AAA|authentication": 40,
    r"management api|api http": 60,
    r"hostname": 80,
}


def get_probe_for_vendor(kind: str) -> ReadinessProbe:
    """Get the appropriate readiness probe for a vendor/device kind.

    Args:
        kind: The device kind (e.g., "ceos", "nokia_srlinux")

    Returns:
        ReadinessProbe instance configured for this vendor
    """
    config = get_vendor_config(kind)

    if config is None or config.readiness_probe == "none":
        return NoopProbe()

    if config.readiness_probe == "log_pattern":
        if config.readiness_pattern is None:
            return NoopProbe()

        # Add progress patterns for cEOS
        progress_patterns = {}
        if is_ceos_kind(kind):
            progress_patterns = CEOS_PROGRESS_PATTERNS

        return LogPatternProbe(
            pattern=config.readiness_pattern,
            progress_patterns=progress_patterns,
        )

    if config.readiness_probe == "cli_probe":
        if config.readiness_pattern is None:
            return NoopProbe()
        # For CLI probe, use console_shell as the command
        return CliProbe(
            cli_command=config.console_shell,
            expected_pattern=config.readiness_pattern,
        )

    return NoopProbe()


def get_readiness_timeout(kind: str) -> int:
    """Get the readiness timeout for a vendor/device kind.

    Args:
        kind: The device kind

    Returns:
        Timeout in seconds to wait for readiness
    """
    config = get_vendor_config(kind)
    if config:
        return config.readiness_timeout
    return 120  # Default 2 minutes


async def run_post_boot_commands(container_name: str, kind: str) -> bool:
    """Run post-boot commands for a container if not already done.

    This function is idempotent - it tracks which containers have already
    had their post-boot commands executed and skips them on subsequent calls.

    Args:
        container_name: Docker container name
        kind: Device kind for looking up vendor config

    Returns:
        True if commands were run (or already completed), False on error
    """
    # Check if already completed
    if container_name in _post_boot_completed:
        return True

    config = get_vendor_config(kind)
    if config is None or not config.post_boot_commands:
        # No commands to run, mark as complete
        _post_boot_completed.add(container_name)
        return True

    def _sync_run_commands() -> bool:
        try:
            client = docker.from_env()
            container = client.containers.get(container_name)

            if container.status != "running":
                logger.warning(f"Container {container_name} not running, skipping post-boot commands")
                return False

            for cmd in config.post_boot_commands:
                logger.info(f"Running post-boot command on {container_name}: {cmd}")
                exit_code, output = container.exec_run(
                    ["sh", "-c", cmd],
                    demux=False,
                )
                output_str = output.decode("utf-8", errors="replace") if output else ""
                if exit_code != 0:
                    logger.warning(
                        f"Post-boot command returned {exit_code} on {container_name}: {cmd}\n{output_str}"
                    )
                else:
                    logger.debug(f"Post-boot command succeeded on {container_name}: {cmd}")

            return True

        except docker.errors.NotFound:
            logger.warning(f"Container {container_name} not found for post-boot commands")
            return False
        except Exception as e:
            logger.error(f"Error running post-boot commands on {container_name}: {e}")
            return False

    success = await asyncio.to_thread(_sync_run_commands)
    if success:
        _post_boot_completed.add(container_name)
        logger.info(f"Post-boot commands completed for {container_name}")
    return success


def clear_post_boot_state(container_name: str) -> None:
    """Clear post-boot completion state for a container.

    Call this when a container is restarted so post-boot commands
    will run again on next readiness check.

    Args:
        container_name: Docker container name
    """
    _post_boot_completed.discard(container_name)


def clear_all_post_boot_state() -> None:
    """Clear all post-boot completion state.

    Call this on agent restart.
    """
    _post_boot_completed.clear()
