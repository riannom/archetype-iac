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
import shlex
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

import docker
import subprocess

from agent.vendors import get_vendor_config, is_ceos_kind

# Try to import libvirt - it's optional
try:
    import libvirt
    LIBVIRT_AVAILABLE = True
except ImportError:
    libvirt = None
    LIBVIRT_AVAILABLE = False

# Try to import pexpect - optional but preferred for VM console interaction
try:
    import pexpect
    PEXPECT_AVAILABLE = True
except ImportError:
    pexpect = None
    PEXPECT_AVAILABLE = False


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


class LibvirtLogPatternProbe(ReadinessProbe):
    """Check VM serial console output for boot completion patterns.

    This probe reads from the libvirt VM's serial console log and searches
    for vendor-specific patterns that indicate boot completion.
    """

    def __init__(
        self,
        pattern: str,
        domain_name: str,
        uri: str = "qemu:///system",
        progress_patterns: Optional[dict[str, int]] = None,
    ):
        """Initialize libvirt log pattern probe.

        Args:
            pattern: Regex pattern to match for boot completion
            domain_name: Libvirt domain name
            uri: Libvirt connection URI
            progress_patterns: Optional dict of pattern -> progress percent
        """
        self.pattern = re.compile(pattern, re.IGNORECASE)
        self.domain_name = domain_name
        self.uri = uri
        self.progress_patterns = progress_patterns or {}
        self._compiled_progress = {
            re.compile(p, re.IGNORECASE): pct
            for p, pct in self.progress_patterns.items()
        }

    async def check(self, container_name: str) -> ReadinessResult:
        """Check VM console for readiness pattern.

        Note: container_name is the node name, not directly used here.
        We use self.domain_name for the actual libvirt lookup.
        """
        def _sync_check() -> ReadinessResult:
            if not LIBVIRT_AVAILABLE:
                return ReadinessResult(
                    is_ready=False,
                    message="Libvirt not available",
                )

            try:
                conn = libvirt.open(self.uri)
                if conn is None:
                    return ReadinessResult(
                        is_ready=False,
                        message="Failed to connect to libvirt",
                    )

                try:
                    domain = conn.lookupByName(self.domain_name)
                except libvirt.libvirtError:
                    conn.close()
                    return ReadinessResult(
                        is_ready=False,
                        message="VM domain not found",
                        progress_percent=0,
                    )

                state, _ = domain.state()
                if state != libvirt.VIR_DOMAIN_RUNNING:
                    conn.close()
                    return ReadinessResult(
                        is_ready=False,
                        message="VM not running",
                        progress_percent=0,
                    )

                # Try to get console output via virsh console with timeout
                # This captures the serial console buffer
                console_output = self._get_console_output()

                conn.close()

                if not console_output:
                    return ReadinessResult(
                        is_ready=False,
                        message="No console output available",
                        progress_percent=5,
                    )

                # Check for completion pattern
                if self.pattern.search(console_output):
                    return ReadinessResult(
                        is_ready=True,
                        message="Boot complete",
                        progress_percent=100,
                    )

                # Check for progress patterns
                max_progress = 10  # VM is running, some progress
                for compiled_pattern, progress in self._compiled_progress.items():
                    if compiled_pattern.search(console_output):
                        max_progress = max(max_progress, progress)

                return ReadinessResult(
                    is_ready=False,
                    message="Boot in progress",
                    progress_percent=max_progress,
                )

            except Exception as e:
                return ReadinessResult(
                    is_ready=False,
                    message=f"Probe error: {str(e)}",
                )

        return await asyncio.to_thread(_sync_check)

    def _get_console_output(self) -> str:
        """Get console output from VM serial port.

        Uses a non-blocking lock to skip gracefully if another session
        (config extraction or web console) is using the serial console.
        This prevents readiness probes from force-disconnecting active sessions.
        """
        from agent.virsh_console_lock import try_console_lock

        with try_console_lock(self.domain_name) as acquired:
            if not acquired:
                logger.debug(
                    f"Console locked for {self.domain_name}, "
                    "skipping readiness probe this cycle"
                )
                return ""

            try:
                if PEXPECT_AVAILABLE:
                    cmd = (
                        f"virsh -c {shlex.quote(self.uri)} "
                        f"console --force {shlex.quote(self.domain_name)}"
                    )
                    child = pexpect.spawn(cmd, timeout=8, encoding="utf-8")
                    chunks: list[str] = []
                    try:
                        child.expect(r"Connected to domain", timeout=4)
                        chunks.append(child.before or "")
                        chunks.append(child.after or "")
                    except pexpect.TIMEOUT:
                        pass

                    child.sendline("")
                    deadline = time.time() + 3.0
                    while time.time() < deadline:
                        try:
                            data = child.read_nonblocking(size=4096, timeout=0.4)
                            if data:
                                chunks.append(data)
                        except pexpect.TIMEOUT:
                            continue
                        except pexpect.EOF:
                            break

                    try:
                        child.sendcontrol("]")
                        child.sendline("quit")
                    except Exception:
                        pass
                    child.close(force=True)
                    return "".join(chunks)

                virsh_cmd = (
                    f"timeout 6 virsh -c {shlex.quote(self.uri)} "
                    f"console --force {shlex.quote(self.domain_name)}"
                )
                cmd = f"script -q -c {shlex.quote(virsh_cmd)} /dev/null"
                result = subprocess.run(
                    ["timeout", "10", "bash", "-lc", cmd],
                    capture_output=True,
                    text=True,
                    timeout=12,
                    stdin=subprocess.DEVNULL,
                )
                return result.stdout + result.stderr
            except Exception as e:
                logger.debug(f"Error getting console output: {e}")
                return ""


# Progress patterns for cEOS boot sequence
CEOS_PROGRESS_PATTERNS = {
    r"ZTP|zerotouch": 20,
    r"AAA|authentication": 40,
    r"management api|api http": 60,
    r"hostname": 80,
}

# Progress patterns for Cisco IOS/IOS-XE VM boot sequence
CISCO_IOS_PROGRESS_PATTERNS = {
    r"Loading|Initializing": 10,
    r"Cisco IOS Software": 30,
    r"nvram|startup-config": 50,
    r"interface|GigabitEthernet": 70,
    r"Ready|Press RETURN": 90,
}

# Progress patterns for Cisco ASA VM boot sequence
CISCO_ASA_PROGRESS_PATTERNS = {
    r"Loading|Booting": 10,
    r"Cisco Adaptive Security": 30,
    r"interface|GigabitEthernet": 50,
    r"crypto": 70,
}

# Progress patterns for Juniper VM boot sequence
JUNIPER_PROGRESS_PATTERNS = {
    r"FreeBSD|Booting": 10,
    r"Juniper|junos": 30,
    r"kernel": 50,
    r"mgd": 70,
}


def get_libvirt_probe(
    kind: str,
    domain_name: str,
    uri: str = "qemu:///system",
    readiness_probe: str | None = None,
    readiness_pattern: str | None = None,
) -> ReadinessProbe:
    """Get the appropriate readiness probe for a VM device.

    Args:
        kind: The device kind (e.g., "cisco_iosv", "cisco_csr1000v")
        domain_name: Libvirt domain name for the VM
        uri: Libvirt connection URI
        readiness_probe: Optional explicit probe type override
        readiness_pattern: Optional explicit readiness pattern override

    Returns:
        ReadinessProbe instance configured for this VM
    """
    config = get_vendor_config(kind)
    probe_type = readiness_probe if readiness_probe is not None else (config.readiness_probe if config else None)
    pattern = readiness_pattern if readiness_pattern is not None else (config.readiness_pattern if config else None)

    if config is None and probe_type is None:
        return NoopProbe()

    if probe_type == "none":
        return NoopProbe()

    if probe_type == "log_pattern":
        if pattern is None:
            return NoopProbe()

        # Select progress patterns based on device kind
        progress_patterns: dict[str, int] = {}
        kind_lower = kind.lower()
        if "iosv" in kind_lower or "csr" in kind_lower or "c8000v" in kind_lower:
            progress_patterns = CISCO_IOS_PROGRESS_PATTERNS
        elif "asa" in kind_lower:
            progress_patterns = CISCO_ASA_PROGRESS_PATTERNS
        elif "juniper" in kind_lower or "vsrx" in kind_lower or "vqfx" in kind_lower:
            progress_patterns = JUNIPER_PROGRESS_PATTERNS

        return LibvirtLogPatternProbe(
            pattern=pattern,
            domain_name=domain_name,
            uri=uri,
            progress_patterns=progress_patterns,
        )

    return NoopProbe()


def get_probe_for_vendor(
    kind: str,
    readiness_probe: str | None = None,
    readiness_pattern: str | None = None,
) -> ReadinessProbe:
    """Get the appropriate readiness probe for a vendor/device kind.

    Args:
        kind: The device kind (e.g., "ceos", "nokia_srlinux")

    Returns:
        ReadinessProbe instance configured for this vendor
    """
    config = get_vendor_config(kind)
    probe_type = readiness_probe if readiness_probe is not None else (config.readiness_probe if config else None)
    pattern = readiness_pattern if readiness_pattern is not None else (config.readiness_pattern if config else None)

    if config is None and probe_type is None:
        return NoopProbe()

    if probe_type == "none":
        return NoopProbe()

    if probe_type == "log_pattern":
        if pattern is None:
            return NoopProbe()

        # Add progress patterns for cEOS
        progress_patterns = {}
        if is_ceos_kind(kind):
            progress_patterns = CEOS_PROGRESS_PATTERNS

        return LogPatternProbe(
            pattern=pattern,
            progress_patterns=progress_patterns,
        )

    if probe_type == "cli_probe":
        if pattern is None:
            return NoopProbe()
        # For CLI probe, use console_shell as the command
        return CliProbe(
            cli_command=config.console_shell if config else "sh",
            expected_pattern=pattern,
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
