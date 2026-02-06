"""Serial console configuration extractor for libvirt VMs.

Uses pexpect to automate interaction with virsh console for
extracting running configurations from network devices.
"""

import logging
import os
import re
import signal
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# Try to import pexpect - it's optional
try:
    import pexpect
    PEXPECT_AVAILABLE = True
except ImportError:
    pexpect = None
    PEXPECT_AVAILABLE = False


@dataclass
class ExtractionResult:
    """Result of a config extraction attempt."""
    success: bool
    config: str = ""
    error: str = ""


@dataclass
class CommandResult:
    """Result of running commands on a VM."""
    success: bool
    commands_run: int = 0
    error: str = ""


class SerialConsoleExtractor:
    """Extract configuration from VM serial console using pexpect."""

    def __init__(
        self,
        domain_name: str,
        libvirt_uri: str = "qemu:///system",
        timeout: int = 30,
    ):
        if not PEXPECT_AVAILABLE:
            raise ImportError("pexpect package is not installed")

        self.domain_name = domain_name
        self.libvirt_uri = libvirt_uri
        self.timeout = timeout
        self.child: Optional["pexpect.spawn"] = None

    def extract_config(
        self,
        command: str = "show running-config",
        username: str = "",
        password: str = "",
        enable_password: str = "",
        prompt_pattern: str = r"[>#]\s*$",
        paging_disable: str = "terminal length 0",
        retries: int = 2,
    ) -> ExtractionResult:
        """Extract configuration from the VM.

        Acquires a per-domain console lock to prevent concurrent access,
        and retries on transient failures with exponential backoff.

        Args:
            command: Command to execute (e.g., "show running-config")
            username: Login username (empty = skip login)
            password: Login password
            enable_password: Enable mode password (empty = skip enable)
            prompt_pattern: Regex pattern to detect CLI prompt
            paging_disable: Command to disable paging (empty = skip)
            retries: Number of retry attempts on failure

        Returns:
            ExtractionResult with config content or error
        """
        # Try piggybacking on an active web console session first
        from agent.console_session_registry import piggyback_extract
        piggyback_result = piggyback_extract(
            domain_name=self.domain_name,
            command=command,
            username=username,
            password=password,
            enable_password=enable_password,
            prompt_pattern=prompt_pattern,
            paging_disable=paging_disable,
            timeout=self.timeout,
        )
        if piggyback_result is not None:
            return piggyback_result

        # No active web console — use normal virsh console with lock + retries
        from agent.virsh_console_lock import console_lock

        last_result = ExtractionResult(success=False, error="No attempts made")
        for attempt in range(1 + retries):
            if attempt > 0:
                delay = 2 ** attempt  # 2s, 4s
                logger.info(
                    f"Retrying config extraction for {self.domain_name} "
                    f"(attempt {attempt + 1}/{1 + retries}) after {delay}s"
                )
                time.sleep(delay)

            try:
                with console_lock(self.domain_name, timeout=60):
                    last_result = self._extract_config_inner(
                        command, username, password,
                        enable_password, prompt_pattern, paging_disable,
                    )
            except TimeoutError:
                last_result = ExtractionResult(
                    success=False,
                    error="Console is locked by another session"
                )

            if last_result.success:
                return last_result

        return last_result

    def _extract_config_inner(
        self,
        command: str,
        username: str,
        password: str,
        enable_password: str,
        prompt_pattern: str,
        paging_disable: str,
    ) -> ExtractionResult:
        """Core extraction logic (called with lock held)."""
        try:
            # Start virsh console
            cmd = f"virsh -c {self.libvirt_uri} console --force {self.domain_name}"
            logger.debug(f"Starting console: {cmd}")
            self.child = pexpect.spawn(cmd, timeout=self.timeout, encoding='utf-8')

            # Wait for initial connection
            # virsh console prints "Connected to domain..." then waits
            try:
                self.child.expect(r"Connected to domain", timeout=10)
                logger.debug("Connected to domain console")
            except pexpect.TIMEOUT:
                return ExtractionResult(
                    success=False,
                    error="Timeout waiting for console connection"
                )

            # Send Enter to get a prompt
            time.sleep(0.5)
            self.child.sendline("")

            # Handle login if credentials provided
            if username:
                if not self._handle_login(username, password, prompt_pattern):
                    return ExtractionResult(
                        success=False,
                        error="Failed to login"
                    )
            else:
                # Just wait for prompt
                if not self._wait_for_prompt(prompt_pattern):
                    return ExtractionResult(
                        success=False,
                        error="Failed to get CLI prompt"
                    )

            # Enter enable mode if password provided
            if enable_password:
                if not self._enter_enable_mode(enable_password, prompt_pattern):
                    return ExtractionResult(
                        success=False,
                        error="Failed to enter enable mode"
                    )

            # Disable terminal paging (critical for full config output)
            if paging_disable:
                self._disable_paging(paging_disable)

            # Execute the config extraction command
            config = self._execute_command(command, prompt_pattern)
            if config is None:
                return ExtractionResult(
                    success=False,
                    error="Timeout waiting for command output"
                )

            # Clean up the config output
            config = self._clean_config(config, command)

            return ExtractionResult(success=True, config=config)

        except pexpect.TIMEOUT:
            return ExtractionResult(
                success=False,
                error="Timeout waiting for console response"
            )
        except pexpect.EOF:
            return ExtractionResult(
                success=False,
                error="Console connection closed unexpectedly"
            )
        except Exception as e:
            logger.exception(f"Config extraction error: {e}")
            return ExtractionResult(
                success=False,
                error=str(e)
            )
        finally:
            self._cleanup()

    def _wait_for_prompt(self, prompt_pattern: str) -> bool:
        """Wait for CLI prompt."""
        try:
            self.child.expect(prompt_pattern, timeout=self.timeout)
            return True
        except pexpect.TIMEOUT:
            logger.warning("Timeout waiting for prompt")
            return False

    def _handle_login(
        self,
        username: str,
        password: str,
        prompt_pattern: str,
    ) -> bool:
        """Handle login prompts."""
        try:
            # Look for username prompt or existing CLI prompt
            index = self.child.expect([
                r"[Uu]sername:",
                r"[Ll]ogin:",
                prompt_pattern,
            ], timeout=self.timeout)

            if index in (0, 1):  # Username prompt
                self.child.sendline(username)
                self.child.expect(r"[Pp]assword:", timeout=self.timeout)
                self.child.sendline(password)
                return self._wait_for_prompt(prompt_pattern)
            # index == 2 means already at prompt, no login needed
            return True
        except pexpect.TIMEOUT:
            logger.warning("Timeout during login")
            return False

    def _enter_enable_mode(self, enable_password: str, prompt_pattern: str) -> bool:
        """Enter privileged EXEC mode."""
        try:
            self.child.sendline("enable")
            index = self.child.expect([
                r"[Pp]assword:",
                prompt_pattern,
            ], timeout=self.timeout)

            if index == 0:  # Password required
                self.child.sendline(enable_password)
                return self._wait_for_prompt(prompt_pattern)
            return True
        except pexpect.TIMEOUT:
            logger.warning("Timeout during enable")
            return False

    def _disable_paging(self, paging_command: str) -> None:
        """Disable terminal paging to get full output."""
        try:
            self.child.sendline(paging_command)
            # Brief wait, don't wait for specific response
            time.sleep(0.5)
            # Consume any output
            try:
                self.child.expect(r".", timeout=1)
            except pexpect.TIMEOUT:
                pass
        except Exception as e:
            logger.debug(f"Error disabling paging (non-fatal): {e}")

    def _execute_command(self, command: str, prompt_pattern: str) -> Optional[str]:
        """Execute command and capture output."""
        try:
            self.child.sendline(command)
            self.child.expect(prompt_pattern, timeout=self.timeout)

            # Get the output (everything between command and prompt)
            output = self.child.before
            if isinstance(output, bytes):
                output = output.decode("utf-8", errors="replace")
            return output
        except pexpect.TIMEOUT:
            logger.warning(f"Timeout executing command: {command}")
            return None

    def _clean_config(self, raw_output: str, command: str) -> str:
        """Clean up config output.

        Removes:
        - ANSI escape sequences
        - The command echo
        - "Building configuration..." messages
        - Extra blank lines
        - Terminal control characters
        """
        # Remove ANSI escape sequences
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        output = ansi_escape.sub('', raw_output)

        # Remove carriage returns
        output = output.replace('\r', '')

        # Split into lines for processing
        lines = output.split('\n')

        # Remove command echo (first line usually)
        if lines and command in lines[0]:
            lines = lines[1:]

        # Remove "Building configuration..." line
        lines = [l for l in lines if not l.strip().startswith("Building configuration")]

        # Remove empty lines at start and end
        while lines and not lines[0].strip():
            lines = lines[1:]
        while lines and not lines[-1].strip():
            lines = lines[:-1]

        return '\n'.join(lines)

    def _cleanup(self) -> None:
        """Clean up pexpect session.

        Uses force=True to ensure the virsh process is terminated,
        with a fallback SIGKILL if it refuses to die.
        """
        if self.child:
            try:
                # Send escape sequence to exit console (Ctrl+])
                self.child.sendcontrol(']')
                time.sleep(0.2)
            except Exception:
                pass
            try:
                self.child.close(force=True)
            except Exception:
                # Last resort: kill the process directly
                try:
                    pid = self.child.pid
                    if pid:
                        os.kill(pid, signal.SIGKILL)
                        logger.debug(f"Force-killed virsh console pid {pid}")
                except (ProcessLookupError, OSError):
                    pass
            self.child = None

    def run_commands(
        self,
        commands: list[str],
        username: str = "",
        password: str = "",
        enable_password: str = "",
        prompt_pattern: str = r"[>#]\s*$",
        retries: int = 2,
    ) -> CommandResult:
        """Run a list of commands on the VM via serial console.

        Acquires a per-domain console lock to prevent concurrent access,
        and retries on transient failures with exponential backoff.

        Args:
            commands: List of commands to execute
            username: Login username (empty = skip login)
            password: Login password
            enable_password: Enable mode password (empty = skip enable)
            prompt_pattern: Regex pattern to detect CLI prompt
            retries: Number of retry attempts on failure

        Returns:
            CommandResult with success status and commands run count
        """
        if not commands:
            return CommandResult(success=True, commands_run=0)

        from agent.virsh_console_lock import console_lock

        last_result = CommandResult(success=False, error="No attempts made")
        for attempt in range(1 + retries):
            if attempt > 0:
                delay = 2 ** attempt
                logger.info(
                    f"Retrying post-boot commands for {self.domain_name} "
                    f"(attempt {attempt + 1}/{1 + retries}) after {delay}s"
                )
                time.sleep(delay)

            try:
                with console_lock(self.domain_name, timeout=60):
                    last_result = self._run_commands_inner(
                        commands, username, password,
                        enable_password, prompt_pattern,
                    )
            except TimeoutError:
                last_result = CommandResult(
                    success=False,
                    error="Console is locked by another session"
                )

            if last_result.success:
                return last_result

        return last_result

    def _run_commands_inner(
        self,
        commands: list[str],
        username: str,
        password: str,
        enable_password: str,
        prompt_pattern: str,
    ) -> CommandResult:
        """Core command execution logic (called with lock held)."""
        try:
            # Start virsh console
            cmd = f"virsh -c {self.libvirt_uri} console --force {self.domain_name}"
            logger.debug(f"Starting console for post-boot commands: {cmd}")
            self.child = pexpect.spawn(cmd, timeout=self.timeout, encoding='utf-8')

            # Wait for initial connection
            try:
                self.child.expect(r"Connected to domain", timeout=10)
                logger.debug("Connected to domain console")
            except pexpect.TIMEOUT:
                return CommandResult(
                    success=False,
                    error="Timeout waiting for console connection"
                )

            # Send Enter to get a prompt
            time.sleep(0.5)
            self.child.sendline("")

            # Handle login if credentials provided
            if username:
                if not self._handle_login(username, password, prompt_pattern):
                    return CommandResult(
                        success=False,
                        error="Failed to login"
                    )
            else:
                # Just wait for prompt
                if not self._wait_for_prompt(prompt_pattern):
                    return CommandResult(
                        success=False,
                        error="Failed to get CLI prompt"
                    )

            # Enter enable mode if password provided
            if enable_password:
                if not self._enter_enable_mode(enable_password, prompt_pattern):
                    return CommandResult(
                        success=False,
                        error="Failed to enter enable mode"
                    )

            # Execute each command
            commands_run = 0
            for command in commands:
                logger.info(f"Running post-boot command on {self.domain_name}: {command}")
                self.child.sendline(command)
                # Wait for prompt after command
                if not self._wait_for_prompt(prompt_pattern):
                    logger.warning(f"Timeout after command: {command}")
                    # Continue with other commands, don't fail completely
                else:
                    commands_run += 1

            return CommandResult(success=True, commands_run=commands_run)

        except pexpect.TIMEOUT:
            return CommandResult(
                success=False,
                error="Timeout waiting for console response"
            )
        except pexpect.EOF:
            return CommandResult(
                success=False,
                error="Console connection closed unexpectedly"
            )
        except Exception as e:
            logger.exception(f"Post-boot commands error: {e}")
            return CommandResult(
                success=False,
                error=str(e)
            )
        finally:
            self._cleanup()


def extract_vm_config(
    domain_name: str,
    kind: str,
    libvirt_uri: str = "qemu:///system",
) -> ExtractionResult:
    """High-level function to extract config from a VM.

    Looks up vendor config for the device kind and uses appropriate
    extraction settings.

    Args:
        domain_name: Libvirt domain name
        kind: Device kind (e.g., "cisco_iosv")
        libvirt_uri: Libvirt connection URI

    Returns:
        ExtractionResult with config or error
    """
    if not PEXPECT_AVAILABLE:
        return ExtractionResult(
            success=False,
            error="pexpect package is not installed"
        )

    from agent.vendors import get_config_extraction_settings

    settings = get_config_extraction_settings(kind)

    if settings.method == "none":
        return ExtractionResult(
            success=False,
            error=f"Config extraction not supported for device type: {kind}"
        )

    if settings.method == "serial":
        extractor = SerialConsoleExtractor(
            domain_name=domain_name,
            libvirt_uri=libvirt_uri,
            timeout=settings.timeout,
        )
        return extractor.extract_config(
            command=settings.command,
            username=settings.user,
            password=settings.password,
            enable_password=settings.enable_password,
            prompt_pattern=settings.prompt_pattern,
            paging_disable=settings.paging_disable,
        )

    # TODO: Add SSH extraction method
    return ExtractionResult(
        success=False,
        error=f"Unsupported extraction method: {settings.method}"
    )


# Track which VMs have had post-boot commands executed (idempotency)
_vm_post_boot_completed: set[str] = set()


def run_vm_post_boot_commands(
    domain_name: str,
    kind: str,
    libvirt_uri: str = "qemu:///system",
) -> CommandResult:
    """Run post-boot commands on a VM via serial console.

    This function is idempotent - it tracks which VMs have already had
    their post-boot commands executed and skips them on subsequent calls.

    Args:
        domain_name: Libvirt domain name
        kind: Device kind (e.g., "cisco_iosv")
        libvirt_uri: Libvirt connection URI

    Returns:
        CommandResult with success status
    """
    # Check if already completed
    if domain_name in _vm_post_boot_completed:
        logger.debug(f"Post-boot commands already run for {domain_name}")
        return CommandResult(success=True, commands_run=0)

    if not PEXPECT_AVAILABLE:
        return CommandResult(
            success=False,
            error="pexpect package is not installed"
        )

    from agent.vendors import get_config_extraction_settings, get_vendor_config

    config = get_vendor_config(kind)
    if config is None or not config.post_boot_commands:
        # No commands to run, mark as complete
        _vm_post_boot_completed.add(domain_name)
        return CommandResult(success=True, commands_run=0)

    # Get extraction settings for console interaction parameters
    extraction_settings = get_config_extraction_settings(kind)

    extractor = SerialConsoleExtractor(
        domain_name=domain_name,
        libvirt_uri=libvirt_uri,
        timeout=30,  # Shorter timeout for simple commands
    )

    result = extractor.run_commands(
        commands=config.post_boot_commands,
        username=extraction_settings.user,
        password=extraction_settings.password,
        enable_password=extraction_settings.enable_password,
        prompt_pattern=extraction_settings.prompt_pattern,
    )

    if result.success:
        _vm_post_boot_completed.add(domain_name)
        logger.info(
            f"Post-boot commands completed for {domain_name}: "
            f"{result.commands_run}/{len(config.post_boot_commands)} commands"
        )
    else:
        logger.warning(f"Post-boot commands failed for {domain_name}: {result.error}")

    return result


def clear_vm_post_boot_cache(domain_name: str | None = None) -> None:
    """Clear the post-boot command completion cache.

    Used when a VM is restarted and needs post-boot commands re-run.

    Args:
        domain_name: Specific domain to clear, or None to clear all
    """
    if domain_name:
        _vm_post_boot_completed.discard(domain_name)
    else:
        _vm_post_boot_completed.clear()
