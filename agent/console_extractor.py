"""Serial console configuration extractor for libvirt VMs.

Uses pexpect to automate interaction with virsh console for
extracting running configurations from network devices.
"""

import logging
import os
import re
import signal
import time
from dataclasses import dataclass, field
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


@dataclass
class CommandOutput:
    """Captured output for a single CLI command."""
    command: str
    success: bool
    output: str = ""
    error: str = ""


@dataclass
class CommandCaptureResult:
    """Result of running and capturing CLI commands on a VM."""
    success: bool
    commands_run: int = 0
    outputs: list[CommandOutput] = field(default_factory=list)
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
            if not piggyback_result.success:
                # Fall back to dedicated virsh extraction path on piggyback failure.
                logger.info(
                    "Piggyback extraction failed for %s, falling back to direct console: %s",
                    self.domain_name,
                    piggyback_result.error,
                )
            else:
                valid, reason = self._validate_extracted_config(
                    config=piggyback_result.config,
                    command=command,
                    paging_disable=paging_disable,
                )
                if valid:
                    return piggyback_result
                logger.info(
                    "Piggyback output invalid for %s (%s); falling back to direct console",
                    self.domain_name,
                    reason,
                )

        # No active web console — use normal virsh console with lock + retries
        from agent.virsh_console_lock import console_lock, extraction_session

        last_result = ExtractionResult(success=False, error="No attempts made")
        with extraction_session(self.domain_name):
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

            if not self._prime_console_for_prompt(prompt_pattern):
                return ExtractionResult(
                    success=False,
                    error="Failed to wake console prompt",
                )

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
            else:
                # IOSv often lands in user EXEC mode (">"). Try best-effort
                # privilege escalation even without an explicit enable password.
                self._attempt_enable_mode(enable_password, prompt_pattern)

            # Disable terminal paging (critical for full config output)
            if paging_disable:
                self._disable_paging(paging_disable, prompt_pattern)

            # Execute the config extraction command
            raw_config = self._execute_command(command, prompt_pattern)
            if raw_config is None:
                return ExtractionResult(
                    success=False,
                    error="Timeout waiting for command output"
                )

            config = self._clean_config(raw_config, command)
            valid, reason = self._validate_extracted_config(
                config=config,
                command=command,
                paging_disable=paging_disable,
            )

            # Some IOS variants can return short/partial running-config output.
            # Fall back to startup-config when running-config is not credible.
            if not valid and "running-config" in command.lower():
                fallback_command = command.lower().replace("running-config", "startup-config")
                fallback_raw = self._execute_command(fallback_command, prompt_pattern)
                if fallback_raw is not None:
                    fallback_config = self._clean_config(fallback_raw, fallback_command)
                    fallback_valid, _ = self._validate_extracted_config(
                        config=fallback_config,
                        command=fallback_command,
                        paging_disable=paging_disable,
                    )
                    if fallback_valid:
                        logger.info(
                            "Using startup-config fallback for %s after invalid running-config capture",
                            self.domain_name,
                        )
                        config = fallback_config
                        valid = True

            if not valid:
                return ExtractionResult(
                    success=False,
                    error=f"Captured output not recognized as configuration: {reason}",
                )

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
        prompt_patterns = self._prompt_patterns(prompt_pattern)
        wait_timeout = max(5, min(self.timeout, 15))

        # Some platforms print an intermediate banner and require another Enter
        # before showing the interactive CLI prompt.
        for _ in range(4):
            try:
                index = self.child.expect(
                    [
                        *prompt_patterns,
                        r"Press RETURN to get started!",
                        r"Would you like to enter the initial configuration dialog\?\s*\[yes/no\]:",
                    ],
                    timeout=wait_timeout,
                )
                if index < len(prompt_patterns):
                    return True
                if index == len(prompt_patterns):
                    self.child.send("\r")
                    continue
                # Decline the setup wizard and continue to prompt detection.
                self.child.sendline("no")
                self.child.send("\r")
            except pexpect.TIMEOUT:
                self.child.send("\r")

        tail = ""
        try:
            tail = (self.child.before or "")[-200:]
        except Exception:
            tail = ""
        logger.warning("Timeout waiting for prompt (buffer tail=%r)", tail)
        return False

    def _prime_console_for_prompt(self, prompt_pattern: str) -> bool:
        """Wake serial console by sending Enter several times.

        IOSv often requires multiple Enter key presses before it emits a prompt.
        """
        prompt_patterns = self._prompt_patterns(prompt_pattern)
        patterns = [
            *prompt_patterns,
            r"Press RETURN to get started!",
            r"[Uu]sername:",
            r"[Ll]ogin:",
            r"Would you like to enter the initial configuration dialog\?\s*\[yes/no\]:",
            r"Abort\s+Power\s+On\s+Auto\s+Provisioning[^\r\n]*\(yes/no\)\[no\]:",
            r"Would you like to enforce secure password standard\s*\(yes/no\)\s*\[y\]:",
            r"Enter the password for \"admin\":",
            r"Confirm the password for \"admin\":",
        ]
        for _ in range(8):
            self.child.send("\r")
            try:
                idx = self.child.expect(patterns, timeout=2)
                if idx < len(prompt_patterns):
                    return True
                if patterns[idx].startswith(r"Would you like to enter"):
                    self.child.sendline("no")
                    self.child.send("\r")
                # Console is awake; let _handle_login drive any remaining
                # onboarding prompts (POAP abort, admin password setup, etc).
                if patterns[idx] != r"Press RETURN to get started!":
                    return True
            except pexpect.TIMEOUT:
                pass
            time.sleep(0.35)
        return False

    def _prompt_patterns(self, prompt_pattern: str) -> list[str]:
        """Build prompt patterns with a Cisco-mode fallback.

        Many Cisco CLIs switch prompts by mode (for example, Router(config)#).
        If vendor prompt patterns are too strict, fall back to this generic form.
        """
        patterns = [prompt_pattern]
        cisco_mode_prompt = r"[\w.\-]+(?:\([^)\r\n]+\))?[>#]\s*$"
        generic_line_prompt = r"(?m)^[^\r\n]*[>#]\s*$"
        if prompt_pattern != cisco_mode_prompt:
            patterns.append(cisco_mode_prompt)
        if prompt_pattern != generic_line_prompt:
            patterns.append(generic_line_prompt)
        return patterns

    @staticmethod
    def _is_strong_admin_password(password: str) -> bool:
        """Return True when password is likely to satisfy NX-OS first-boot policy."""
        if len(password) < 8:
            return False
        checks = [
            re.search(r"[a-z]", password),
            re.search(r"[A-Z]", password),
            re.search(r"[0-9]", password),
            re.search(r"[^A-Za-z0-9]", password),
        ]
        return all(checks)

    def _bootstrap_admin_password(self, password: str) -> str:
        """Pick a first-boot admin password for onboarding prompts.

        Some vendors (notably NX-OSv) enforce password complexity before the
        usual login prompt appears. Use the configured password when possible,
        otherwise fall back to a deterministic strong default.
        """
        candidate = (password or "").strip()
        if self._is_strong_admin_password(candidate):
            return candidate
        return "Archetype123!"

    def _handle_login(
        self,
        username: str,
        password: str,
        prompt_pattern: str,
    ) -> bool:
        """Handle login and first-boot onboarding prompts."""
        prompt_patterns = self._prompt_patterns(prompt_pattern)
        bootstrap_password = self._bootstrap_admin_password(password)
        password_candidates: list[str] = []
        configured_password = (password or "").strip()
        if configured_password:
            password_candidates.append(configured_password)
        if bootstrap_password and bootstrap_password not in password_candidates:
            password_candidates.append(bootstrap_password)
        if "" not in password_candidates:
            password_candidates.append("")
        password_index = 0
        patterns = [
            *prompt_patterns,
            r"[Uu]sername:",
            r"[Ll]ogin:",
            r"[Pp]assword:",
            r"Abort\s+Power\s+On\s+Auto\s+Provisioning[^\r\n]*\(yes/no\)\[no\]:",
            r"Would you like to enter (?:the )?(?:initial|basic) configuration dialog[^\r\n]*\(yes/no\)[^\r\n]*:",
            r"Would you like to enforce secure password standard\s*\(yes/no\)\s*\[y\]:",
            r"Enter the password for \"admin\":",
            r"Confirm the password for \"admin\":",
            r"Wrong Password,\s*Reason:",
            r"[Ll]ogin incorrect",
        ]

        step_timeout = max(2, min(self.timeout, 8))
        deadline = time.time() + max(5, self.timeout)
        while time.time() < deadline:
            try:
                index = self.child.expect(patterns, timeout=step_timeout)
            except pexpect.TIMEOUT:
                # Keep nudging the console while the device transitions through
                # boot/onboarding states.
                self.child.send("\r")
                continue

            # Existing CLI prompt.
            if index < len(prompt_patterns):
                return True

            action = index - len(prompt_patterns)

            if action in (0, 1):  # Username/login prompt
                self.child.sendline(username)
                continue

            if action == 2:  # Password prompt after username/login
                candidate = password_candidates[min(password_index, len(password_candidates) - 1)]
                self.child.sendline(candidate)
                continue

            if action == 3:  # POAP abort
                self.child.sendline("yes")
                continue

            if action == 4:  # Basic/initial config dialog
                self.child.sendline("no")
                continue

            if action == 5:  # Enforce secure password standard
                self.child.sendline("yes")
                continue

            if action in (6, 7):  # Enter/confirm admin password
                self.child.sendline(bootstrap_password)
                continue

            if action in (8, 9):
                if password_index < len(password_candidates) - 1:
                    password_index += 1
                continue

        logger.warning("Login prompt handling timed out")
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

    def _attempt_enable_mode(self, enable_password: str, prompt_pattern: str) -> None:
        """Best-effort privilege escalation.

        If no enable password is configured, submit an empty password to
        accommodate labs where enable has no password.
        """
        try:
            self.child.sendline("enable")
            index = self.child.expect(
                [r"[Pp]assword:", prompt_pattern, r"% ?[Ii]nvalid input.*"],
                timeout=min(self.timeout, 6),
            )
            if index == 0:
                self.child.sendline(enable_password or "")
                self._wait_for_prompt(prompt_pattern)
        except Exception:
            logger.debug("Enable escalation attempt skipped", exc_info=True)

    def _disable_paging(self, paging_command: str, prompt_pattern: str) -> None:
        """Disable terminal paging to get full output."""
        try:
            self.child.sendline(paging_command)
            # Wait for prompt so output from this command does not pollute
            # the next command capture.
            self.child.expect(self._prompt_patterns(prompt_pattern), timeout=min(self.timeout, 5))
        except Exception as e:
            logger.debug(f"Error disabling paging (non-fatal): {e}")

    def _execute_command(self, command: str, prompt_pattern: str) -> Optional[str]:
        """Execute command and capture output."""
        try:
            self.child.sendline(command)
            self.child.expect(self._prompt_patterns(prompt_pattern), timeout=self.timeout)

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
        - Command echo lines (with or without prompt prefixes)
        - "Building configuration..." messages
        - Stray prompt/banner lines from serial transports
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

        # Remove command echo lines, including optional prompt prefixes.
        cmd_pat = re.compile(
            rf"^\s*(?:[^\s]+(?:\([^)\r\n]+\))?[>#]\s*)?{re.escape(command)}\s*$",
            re.IGNORECASE,
        )
        # Remove pure prompt lines left in captured output.
        prompt_only_pat = re.compile(
            r"^\s*[A-Za-z0-9_.-]+(?:\([^)\r\n]+\))?[>#]\s*$"
        )
        cleaned_lines = []
        for line in lines:
            stripped = line.strip()
            if cmd_pat.match(line):
                continue
            if not stripped:
                cleaned_lines.append(line)
                continue
            if stripped.startswith("Building configuration"):
                continue
            if stripped.startswith("Connected to domain"):
                continue
            if stripped.startswith("Escape character is"):
                continue
            if stripped == "--More--":
                continue
            if prompt_only_pat.match(line):
                continue
            cleaned_lines.append(line)
        lines = cleaned_lines

        # Remove empty lines at start and end
        while lines and not lines[0].strip():
            lines = lines[1:]
        while lines and not lines[-1].strip():
            lines = lines[:-1]

        return '\n'.join(lines)

    def _validate_extracted_config(
        self,
        config: str,
        command: str,
        paging_disable: str = "",
    ) -> tuple[bool, str]:
        """Validate that extracted output looks like a real config payload."""
        text = (config or "").strip()
        if not text:
            return False, "empty output"

        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if len(lines) < 2:
            return False, f"too few lines ({len(lines)})"

        lowered = text.lower()
        for marker in (
            "% invalid input",
            "% incomplete command",
            "% ambiguous command",
            "% unknown command",
        ):
            if marker in lowered:
                return False, f"cli error marker detected: {marker}"

        command_l = command.strip().lower()
        paging_l = paging_disable.strip().lower()
        echo_only = {command_l}
        if paging_l:
            echo_only.add(paging_l)
        stripped_lines = [
            re.sub(r"^[^\s]+[>#]\s*", "", ln).strip().lower()
            for ln in lines
        ]
        if all(ln in echo_only for ln in stripped_lines):
            return False, "output contains only command echoes"

        # Very short output is usually a prompt/echo artifact.
        config_markers = (
            "current configuration",
            "version ",
            "hostname ",
            "interface ",
            "!",
        )
        if len(text) < 64 and not any(marker in lowered for marker in config_markers):
            return False, f"output too short ({len(text)} bytes)"

        return True, ""

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

    def run_commands_capture(
        self,
        commands: list[str],
        username: str = "",
        password: str = "",
        enable_password: str = "",
        prompt_pattern: str = r"[>#]\s*$",
        paging_disable: str = "terminal length 0",
        attempt_enable: bool = True,
        retries: int = 2,
    ) -> CommandCaptureResult:
        """Run and capture CLI command output via serial console.

        Acquires a per-domain console lock to prevent concurrent access,
        and retries on transient failures with exponential backoff.

        Args:
            commands: List of commands to execute
            username: Login username (empty = skip login)
            password: Login password
            enable_password: Enable mode password (empty = skip enable)
            prompt_pattern: Regex pattern to detect CLI prompt
            paging_disable: Command to disable paging (empty = skip)
            attempt_enable: Whether to run enable-mode escalation
            retries: Number of retry attempts on failure

        Returns:
            CommandCaptureResult with per-command output
        """
        if not commands:
            return CommandCaptureResult(success=True, commands_run=0, outputs=[])

        from agent.virsh_console_lock import console_lock

        last_result = CommandCaptureResult(success=False, error="No attempts made")
        for attempt in range(1 + retries):
            if attempt > 0:
                delay = 2 ** attempt
                logger.info(
                    f"Retrying CLI command capture for {self.domain_name} "
                    f"(attempt {attempt + 1}/{1 + retries}) after {delay}s"
                )
                time.sleep(delay)

            try:
                with console_lock(self.domain_name, timeout=60):
                    last_result = self._run_commands_capture_inner(
                        commands,
                        username,
                        password,
                        enable_password,
                        prompt_pattern,
                        paging_disable,
                        attempt_enable,
                    )
            except TimeoutError:
                last_result = CommandCaptureResult(
                    success=False,
                    error="Console is locked by another session",
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

            if not self._prime_console_for_prompt(prompt_pattern):
                return CommandResult(
                    success=False,
                    error="Failed to wake console prompt",
                )

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
            else:
                self._attempt_enable_mode(enable_password, prompt_pattern)

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

    def _run_commands_capture_inner(
        self,
        commands: list[str],
        username: str,
        password: str,
        enable_password: str,
        prompt_pattern: str,
        paging_disable: str,
        attempt_enable: bool,
    ) -> CommandCaptureResult:
        """Core command capture logic (called with lock held)."""
        try:
            cmd = f"virsh -c {self.libvirt_uri} console --force {self.domain_name}"
            logger.debug(f"Starting console for CLI capture: {cmd}")
            self.child = pexpect.spawn(cmd, timeout=self.timeout, encoding='utf-8')

            try:
                self.child.expect(r"Connected to domain", timeout=10)
                logger.debug("Connected to domain console")
            except pexpect.TIMEOUT:
                return CommandCaptureResult(
                    success=False,
                    error="Timeout waiting for console connection",
                )

            if not self._prime_console_for_prompt(prompt_pattern):
                logger.info(
                    "Initial prompt priming failed for %s, continuing with login flow",
                    self.domain_name,
                )

            if username:
                if not self._handle_login(username, password, prompt_pattern):
                    tail = ""
                    try:
                        tail = (self.child.before or "")[-240:]
                    except Exception:
                        tail = ""
                    return CommandCaptureResult(
                        success=False,
                        error=f"Failed to login (buffer tail={tail!r})",
                    )
            else:
                if not self._wait_for_prompt(prompt_pattern):
                    tail = ""
                    try:
                        tail = (self.child.before or "")[-240:]
                    except Exception:
                        tail = ""
                    return CommandCaptureResult(
                        success=False,
                        error=f"Failed to get CLI prompt (buffer tail={tail!r})",
                    )

            if attempt_enable:
                if enable_password:
                    if not self._enter_enable_mode(enable_password, prompt_pattern):
                        return CommandCaptureResult(
                            success=False,
                            error="Failed to enter enable mode",
                        )
                else:
                    self._attempt_enable_mode(enable_password, prompt_pattern)

            if paging_disable:
                self._disable_paging(paging_disable, prompt_pattern)

            commands_run = 0
            outputs: list[CommandOutput] = []
            for command in commands:
                logger.info(f"Running CLI capture command on {self.domain_name}: {command}")
                raw_output = self._execute_command(command, prompt_pattern)
                if raw_output is None:
                    outputs.append(
                        CommandOutput(
                            command=command,
                            success=False,
                            error="Timeout waiting for command output",
                        )
                    )
                    continue

                outputs.append(
                    CommandOutput(
                        command=command,
                        success=True,
                        output=self._clean_config(raw_output, command),
                    )
                )
                commands_run += 1

            success = commands_run == len(commands)
            error = ""
            if not success:
                failed = len(commands) - commands_run
                error = f"{failed} command(s) failed"
            return CommandCaptureResult(
                success=success,
                commands_run=commands_run,
                outputs=outputs,
                error=error,
            )

        except pexpect.TIMEOUT:
            return CommandCaptureResult(
                success=False,
                error="Timeout waiting for console response",
            )
        except pexpect.EOF:
            return CommandCaptureResult(
                success=False,
                error="Console connection closed unexpectedly",
            )
        except Exception as e:
            logger.exception(f"CLI capture commands error: {e}")
            return CommandCaptureResult(
                success=False,
                error=str(e),
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


def run_vm_cli_commands(
    domain_name: str,
    kind: str,
    commands: list[str],
    libvirt_uri: str = "qemu:///system",
    username: str | None = None,
    password: str | None = None,
    enable_password: str | None = None,
    prompt_pattern: str | None = None,
    paging_disable: str | None = None,
    attempt_enable: bool = True,
    timeout: int | None = None,
    retries: int = 2,
) -> CommandCaptureResult:
    """Run and capture arbitrary CLI commands on a VM via serial console.

    Args:
        domain_name: Libvirt domain name
        kind: Device kind (e.g., "cisco_n9kv")
        commands: CLI commands to execute and capture
        libvirt_uri: Libvirt connection URI
        username: Optional username override
        password: Optional password override
        enable_password: Optional enable-password override
        prompt_pattern: Optional prompt regex override
        paging_disable: Optional paging-disable command override
        attempt_enable: Whether to attempt enable-mode escalation
        timeout: Optional per-command timeout override in seconds
        retries: Number of retry attempts on failure

    Returns:
        CommandCaptureResult with per-command outputs.
    """
    if not commands:
        return CommandCaptureResult(success=True, commands_run=0, outputs=[])

    if not PEXPECT_AVAILABLE:
        return CommandCaptureResult(
            success=False,
            error="pexpect package is not installed",
        )

    from agent.vendors import get_config_extraction_settings

    extraction_settings = get_config_extraction_settings(kind)
    if extraction_settings.method == "none":
        return CommandCaptureResult(
            success=False,
            error=f"Config extraction not supported for device type: {kind}",
        )

    effective_timeout = extraction_settings.timeout
    if isinstance(timeout, int) and timeout > 0:
        effective_timeout = timeout

    extractor = SerialConsoleExtractor(
        domain_name=domain_name,
        libvirt_uri=libvirt_uri,
        timeout=effective_timeout,
    )

    resolved_username = extraction_settings.user if username is None else username
    resolved_password = extraction_settings.password if password is None else password
    resolved_enable_password = (
        extraction_settings.enable_password
        if enable_password is None
        else enable_password
    )
    if prompt_pattern is None:
        resolved_prompt_pattern = extraction_settings.prompt_pattern or r"[>#]\s*$"
    else:
        resolved_prompt_pattern = prompt_pattern
    resolved_paging_disable = (
        extraction_settings.paging_disable
        if paging_disable is None
        else paging_disable
    )

    return extractor.run_commands_capture(
        commands=commands,
        username=resolved_username,
        password=resolved_password,
        enable_password=resolved_enable_password,
        prompt_pattern=resolved_prompt_pattern,
        paging_disable=resolved_paging_disable,
        attempt_enable=attempt_enable,
        retries=max(0, retries),
    )


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
