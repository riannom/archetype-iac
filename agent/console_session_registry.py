"""Registry of active web console sessions for piggyback config extraction.

When a user has a web console open to a VM, config extraction can
"piggyback" on that session's PTY instead of opening a competing
virsh console. This avoids lock contention and keeps the user connected.

I/O flow during piggybacking:
  1. write_pty() paused (user keystrokes dropped)
  2. read_pty() paused (injector owns all reads from master_fd)
  3. PtyInjector writes commands to master_fd
  4. PtyInjector reads output from master_fd, forwards to WS so user sees it
  5. After extraction: both resume, user continues normally
"""

import asyncio
import json
import logging
import os
import re
import select
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

from agent.console_extractor import (
    CommandCaptureResult,
    CommandOutput,
    CommandResult,
    ExtractionResult,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ActiveConsoleSession dataclass
# ---------------------------------------------------------------------------

@dataclass
class ActiveConsoleSession:
    """Represents an active web console PTY session."""
    domain_name: str
    master_fd: int
    loop: asyncio.AbstractEventLoop
    websocket: object  # WebSocket instance
    input_paused: threading.Event = field(default_factory=threading.Event)
    pty_read_paused: threading.Event = field(default_factory=threading.Event)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self):
        # Events start *set* = flowing (not paused)
        self.input_paused.set()
        self.pty_read_paused.set()


# ---------------------------------------------------------------------------
# Session registry (module-level, thread-safe)
# ---------------------------------------------------------------------------

_registry: dict[str, ActiveConsoleSession] = {}
_registry_lock = threading.Lock()
_console_control_state: dict[str, tuple[str, str]] = {}


def register_session(domain_name: str, session: ActiveConsoleSession) -> None:
    control: Optional[tuple[str, str]] = None
    with _registry_lock:
        _registry[domain_name] = session
        control = _console_control_state.get(domain_name)
    logger.debug(f"Registered console session for {domain_name}")
    if control is not None:
        state, message = control
        _send_console_control(
            session,
            state=state,
            message=message,
        )


def unregister_session(domain_name: str) -> None:
    with _registry_lock:
        _registry.pop(domain_name, None)
    logger.debug(f"Unregistered console session for {domain_name}")


def get_session(domain_name: str) -> Optional[ActiveConsoleSession]:
    with _registry_lock:
        return _registry.get(domain_name)


def list_active_domains() -> list[str]:
    """Return a snapshot of domains with active web console sessions."""
    with _registry_lock:
        return list(_registry.keys())


def set_console_control_state(
    domain_name: str,
    *,
    state: str,
    message: str,
) -> bool:
    """Persist and broadcast console control mode for a domain.

    Returns:
        True when state changed, False when effective state is unchanged.
    """
    target_state = "read_only" if state == "read_only" else "interactive"

    changed = False
    session: Optional[ActiveConsoleSession] = None
    with _registry_lock:
        current = _console_control_state.get(domain_name)
        if target_state == "read_only":
            desired = (target_state, message)
            if current != desired:
                _console_control_state[domain_name] = desired
                changed = True
        else:
            if current is not None:
                _console_control_state.pop(domain_name, None)
                changed = True
        session = _registry.get(domain_name)

    if changed and session is not None:
        _send_console_control(
            session,
            state=target_state,
            message=message,
        )
    return changed


def get_console_control_state(domain_name: str) -> Optional[tuple[str, str]]:
    """Return persisted control state for a domain, if any."""
    with _registry_lock:
        return _console_control_state.get(domain_name)


# ---------------------------------------------------------------------------
# PtyInjector — minimal pexpect replacement for raw PTY I/O
# ---------------------------------------------------------------------------

class PtyInjector:
    """Send commands and read output on a raw PTY fd.

    Unlike pexpect, this operates on an existing master_fd rather than
    spawning a new process. Output is optionally forwarded to the user's
    browser via *ws_forward*.
    """

    def __init__(
        self,
        fd: int,
        ws_forward: Optional[Callable[[bytes], None]] = None,
        default_timeout: float = 30,
    ):
        self.fd = fd
        self.ws_forward = ws_forward
        self.default_timeout = default_timeout
        self._buffer = b""
        self.last_match = ""

    def send(self, text: str) -> None:
        os.write(self.fd, text.encode())

    def sendline(self, text: str) -> None:
        os.write(self.fd, (text + "\r").encode())

    def drain(self, duration: float = 0.5) -> bytes:
        """Flush pending output for *duration* seconds."""
        collected = b""
        deadline = _monotonic() + duration
        while True:
            remaining = deadline - _monotonic()
            if remaining <= 0:
                break
            r, _, _ = select.select([self.fd], [], [], max(remaining, 0.01))
            if r:
                try:
                    chunk = os.read(self.fd, 4096)
                    if chunk:
                        collected += chunk
                        if self.ws_forward:
                            self.ws_forward(chunk)
                except (BlockingIOError, OSError):
                    break
            else:
                break
        return collected

    def expect(
        self,
        pattern: str,
        timeout: Optional[float] = None,
    ) -> str:
        """Read until *pattern* matches accumulated output.

        Returns the text *before* the match. Raises TimeoutError if
        the pattern isn't seen within *timeout* seconds, or OSError
        if the fd goes bad.
        """
        timeout = timeout or self.default_timeout
        regex = re.compile(pattern.encode() if isinstance(pattern, str) else pattern)
        deadline = _monotonic() + timeout

        while True:
            m = regex.search(self._buffer)
            if m:
                before = self._buffer[:m.start()]
                self.last_match = m.group(0).decode("utf-8", errors="replace")
                self._buffer = self._buffer[m.end():]
                return before.decode("utf-8", errors="replace")

            remaining = deadline - _monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"Timed out waiting for pattern {pattern!r} "
                    f"(buffer tail: {self._buffer[-200:]!r})"
                )

            r, _, _ = select.select([self.fd], [], [], min(remaining, 0.5))
            if r:
                try:
                    chunk = os.read(self.fd, 4096)
                except OSError as e:
                    raise OSError(f"PTY read error: {e}") from e
                if not chunk:
                    raise OSError("PTY fd returned empty read (closed)")
                self._buffer += chunk
                if self.ws_forward:
                    self.ws_forward(chunk)


def _monotonic() -> float:
    """Wrapper for time.monotonic (makes testing easier)."""
    import time
    return time.monotonic()


def _send_console_control(
    session: ActiveConsoleSession,
    *,
    state: str,
    message: str,
) -> None:
    """Send a UI control message to the active console websocket."""
    payload = json.dumps(
        {
            "type": "console-control",
            "state": state,
            "message": message,
        }
    )

    async def _deliver() -> None:
        await session.websocket.send_text(payload)

    try:
        current_loop = None
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        # When already on the target event loop thread, scheduling with
        # run_coroutine_threadsafe and waiting for result can deadlock.
        if current_loop is session.loop:
            session.loop.create_task(_deliver())
            return

        asyncio.run_coroutine_threadsafe(
            _deliver(),
            session.loop,
        ).result(timeout=2)
    except Exception:
        # Best-effort only: websocket may have disconnected between checks.
        pass


# ---------------------------------------------------------------------------
# _clean_config — reuses logic from console_extractor
# ---------------------------------------------------------------------------

def _clean_config(raw_output: str, command: str) -> str:
    """Clean up config output (mirrors SerialConsoleExtractor._clean_config)."""
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    output = ansi_escape.sub('', raw_output)
    output = output.replace('\r', '')
    lines = output.split('\n')
    cmd_pat = re.compile(rf"^\s*(?:[^\s]+[>#]\s*)?{re.escape(command)}\s*$", re.IGNORECASE)
    lines = [line for line in lines if not cmd_pat.match(line)]
    lines = [line for line in lines if not line.strip().startswith("Building configuration")]
    while lines and not lines[0].strip():
        lines = lines[1:]
    while lines and not lines[-1].strip():
        lines = lines[:-1]
    return '\n'.join(lines)


def _contains_cli_error(output: str) -> bool:
    lowered = (output or "").lower()
    markers = (
        "% invalid input",
        "% incomplete command",
        "% ambiguous command",
        "% unknown command",
    )
    return any(marker in lowered for marker in markers)


# ---------------------------------------------------------------------------
# piggyback_extract — orchestrator
# ---------------------------------------------------------------------------

def piggyback_extract(
    domain_name: str,
    command: str = "show running-config",
    username: str = "",
    password: str = "",
    enable_password: str = "",
    prompt_pattern: str = r"[>#]\s*$",
    paging_disable: str = "terminal length 0",
    timeout: float = 30,
) -> Optional[ExtractionResult]:
    """Attempt to extract config via an active web console session.

    Returns:
        ExtractionResult on success or failure if a session was found.
        None if no active session exists (caller should fall back to
        the normal virsh console extraction path).
    """
    session = get_session(domain_name)
    if session is None:
        return None

    # Try to acquire the session lock (prevents concurrent piggybacks)
    if not session._lock.acquire(timeout=5):
        logger.debug(f"Piggyback lock busy for {domain_name}, falling back")
        return None

    try:
        # Re-check session is still registered (may have been torn down)
        if get_session(domain_name) is None:
            return None

        logger.info(f"Piggybacking config extraction on web console for {domain_name}")

        # --- Pause user I/O ---
        session.input_paused.clear()
        session.pty_read_paused.clear()
        set_console_control_state(
            domain_name,
            state="read_only",
            message="Configuration in progress. Console is view-only.",
        )

        # Small delay to let in-flight reads drain
        import time
        time.sleep(0.2)

        def ws_forward(data: bytes) -> None:
            """Forward PTY output to user's browser (best-effort)."""
            try:
                asyncio.run_coroutine_threadsafe(
                    session.websocket.send_bytes(data),
                    session.loop,
                ).result(timeout=2)
            except Exception:
                pass

        injector = PtyInjector(
            fd=session.master_fd,
            ws_forward=ws_forward,
            default_timeout=timeout,
        )

        # Notify user
        ws_forward(b"\r\n\x1b[93m--- Config extraction in progress ---\x1b[0m\r\n")

        # Clear user's current line and get to a clean prompt
        injector.send("\x15")  # Ctrl+U — clear line
        time.sleep(0.1)
        injector.sendline("")  # Press Enter to get a fresh prompt

        try:
            # Wait for a prompt (user may be at login, enable, or exec)
            prompt_text = injector.expect(prompt_pattern, timeout=10)
            prompt_match = (getattr(injector, "last_match", "") or "").strip()

            # Check if we're in config mode — exit it
            if "(config" in prompt_match or "(config" in prompt_text:
                injector.sendline("end")
                injector.expect(prompt_pattern, timeout=5)
                prompt_match = (getattr(injector, "last_match", "") or "").strip()

            # Handle login if needed (Username: prompt in buffer)
            # This is unlikely during an active web console but handle it
            if username and ("Username:" in prompt_text or "Login:" in prompt_text):
                injector.sendline(username)
                injector.expect(r"[Pp]assword:", timeout=10)
                injector.sendline(password)
                injector.expect(prompt_pattern, timeout=10)

            # Enter enable mode if needed (prompt ends with >)
            if prompt_match.endswith(">"):
                injector.sendline("enable")
                try:
                    injector.expect(r"[Pp]assword:", timeout=5)
                    injector.sendline(enable_password or "")
                    injector.expect(prompt_pattern, timeout=10)
                    prompt_match = (getattr(injector, "last_match", "") or "").strip()
                except TimeoutError:
                    # Some devices switch directly to # without a password prompt.
                    try:
                        injector.expect(prompt_pattern, timeout=3)
                        prompt_match = (getattr(injector, "last_match", "") or "").strip()
                    except TimeoutError:
                        pass

            # Disable paging
            if paging_disable:
                injector.sendline(paging_disable)
                try:
                    injector.expect(prompt_pattern, timeout=5)
                    prompt_match = (getattr(injector, "last_match", "") or "").strip()
                except TimeoutError:
                    injector.drain(1.0)

            # `show running-config` generally requires privileged EXEC.
            if "running-config" in command.lower() and prompt_match.endswith(">"):
                return ExtractionResult(
                    success=False,
                    error="Device remained in user EXEC mode (>) after enable attempt",
                )

            # Execute extraction command
            injector.sendline(command)
            raw_output = injector.expect(prompt_pattern, timeout=timeout)
            if _contains_cli_error(raw_output):
                return ExtractionResult(
                    success=False,
                    error="CLI rejected extraction command",
                )

            config = _clean_config(raw_output, command)

            ws_forward(b"\r\n\x1b[93m--- Config extraction complete ---\x1b[0m\r\n")

            logger.info(
                f"Piggyback extraction succeeded for {domain_name} "
                f"({len(config)} bytes)"
            )
            return ExtractionResult(success=True, config=config)

        except TimeoutError as e:
            ws_forward(b"\r\n\x1b[91m--- Config extraction timed out ---\x1b[0m\r\n")
            logger.warning(f"Piggyback extraction timed out for {domain_name}: {e}")
            return ExtractionResult(success=False, error=f"Piggyback timeout: {e}")

        except OSError as e:
            ws_forward(b"\r\n\x1b[91m--- Config extraction failed ---\x1b[0m\r\n")
            logger.warning(f"Piggyback extraction PTY error for {domain_name}: {e}")
            return ExtractionResult(success=False, error=f"PTY error: {e}")

    except Exception as e:
        logger.exception(f"Unexpected error in piggyback extraction for {domain_name}")
        return ExtractionResult(success=False, error=f"Unexpected error: {e}")

    finally:
        set_console_control_state(
            domain_name,
            state="interactive",
            message="Configuration completed. Interactive control restored.",
        )
        # --- Resume user I/O ---
        session.input_paused.set()
        session.pty_read_paused.set()
        session._lock.release()


def piggyback_run_commands(
    domain_name: str,
    commands: list[str],
    username: str = "",
    password: str = "",
    enable_password: str = "",
    prompt_pattern: str = r"[>#]\s*$",
    timeout: float = 30,
) -> Optional[CommandResult]:
    """Attempt to run commands via an active web console session.

    While running, user input is paused (view-only mode) and restored
    automatically when command execution finishes.

    Returns:
        CommandResult on success/failure if a session was found.
        None if no active session exists (caller should fall back to
        normal direct console automation).
    """
    if not commands:
        return CommandResult(success=True, commands_run=0)

    session = get_session(domain_name)
    if session is None:
        return None

    if not session._lock.acquire(timeout=5):
        logger.debug(f"Piggyback lock busy for {domain_name}, falling back")
        return None

    ws_forward: Callable[[bytes], None] = lambda _data: None
    try:
        if get_session(domain_name) is None:
            return None

        logger.info(f"Piggybacking command automation on web console for {domain_name}")

        session.input_paused.clear()
        session.pty_read_paused.clear()
        set_console_control_state(
            domain_name,
            state="read_only",
            message="Configuration in progress. Console is view-only.",
        )

        import time
        time.sleep(0.2)

        def _ws_forward(data: bytes) -> None:
            try:
                asyncio.run_coroutine_threadsafe(
                    session.websocket.send_bytes(data),
                    session.loop,
                ).result(timeout=2)
            except Exception:
                pass

        ws_forward = _ws_forward

        injector = PtyInjector(
            fd=session.master_fd,
            ws_forward=ws_forward,
            default_timeout=timeout,
        )

        injector.send("\x15")
        time.sleep(0.1)
        injector.sendline("")

        prompt_text = injector.expect(prompt_pattern, timeout=10)
        prompt_match = (getattr(injector, "last_match", "") or "").strip()

        if "(config" in prompt_match or "(config" in prompt_text:
            injector.sendline("end")
            injector.expect(prompt_pattern, timeout=5)
            prompt_match = (getattr(injector, "last_match", "") or "").strip()

        if username and ("Username:" in prompt_text or "Login:" in prompt_text):
            injector.sendline(username)
            injector.expect(r"[Pp]assword:", timeout=10)
            injector.sendline(password)
            injector.expect(prompt_pattern, timeout=10)
            prompt_match = (getattr(injector, "last_match", "") or "").strip()

        if prompt_match.endswith(">"):
            injector.sendline("enable")
            try:
                injector.expect(r"[Pp]assword:", timeout=5)
                injector.sendline(enable_password or "")
                injector.expect(prompt_pattern, timeout=10)
            except TimeoutError:
                try:
                    injector.expect(prompt_pattern, timeout=3)
                except TimeoutError:
                    pass

        commands_run = 0
        for command in commands:
            logger.info(f"Piggyback running command on {domain_name}: {command}")
            injector.sendline(command)
            output = injector.expect(prompt_pattern, timeout=timeout)
            if _contains_cli_error(output):
                return CommandResult(
                    success=False,
                    commands_run=commands_run,
                    error=f"CLI rejected command: {command}",
                )
            commands_run += 1

        ws_forward(
            b"\r\n\x1b[93m--- Automation complete; interactive control restored ---\x1b[0m\r\n"
        )
        logger.info(
            f"Piggyback command automation succeeded for {domain_name} "
            f"({commands_run}/{len(commands)} commands)"
        )
        return CommandResult(success=True, commands_run=commands_run)

    except TimeoutError as e:
        ws_forward(
            b"\r\n\x1b[91m--- Automation timed out; restoring control ---\x1b[0m\r\n"
        )
        logger.warning(f"Piggyback command automation timed out for {domain_name}: {e}")
        return CommandResult(success=False, error=f"Piggyback timeout: {e}")

    except OSError as e:
        ws_forward(
            b"\r\n\x1b[91m--- Automation failed; restoring control ---\x1b[0m\r\n"
        )
        logger.warning(f"Piggyback command automation PTY error for {domain_name}: {e}")
        return CommandResult(success=False, error=f"PTY error: {e}")

    except Exception as e:
        logger.exception(
            f"Unexpected error in piggyback command automation for {domain_name}"
        )
        return CommandResult(success=False, error=f"Unexpected error: {e}")

    finally:
        set_console_control_state(
            domain_name,
            state="interactive",
            message="Configuration completed. Interactive control restored.",
        )
        session.input_paused.set()
        session.pty_read_paused.set()
        session._lock.release()


def piggyback_run_commands_capture(
    domain_name: str,
    commands: list[str],
    username: str = "",
    password: str = "",
    enable_password: str = "",
    prompt_pattern: str = r"[>#]\s*$",
    paging_disable: str = "terminal length 0",
    attempt_enable: bool = True,
    timeout: float = 30,
) -> Optional[CommandCaptureResult]:
    """Attempt to run and capture commands via an active web console session.

    Like piggyback_run_commands() but returns per-command captured output.
    Used by boot intervention handlers that need to read CLI responses.

    Returns:
        CommandCaptureResult on success/failure if a session was found.
        None if no active session exists (caller should fall back to
        normal direct console automation).
    """
    if not commands:
        return CommandCaptureResult(success=True, commands_run=0, outputs=[])

    session = get_session(domain_name)
    if session is None:
        return None

    if not session._lock.acquire(timeout=5):
        logger.debug(f"Piggyback capture lock busy for {domain_name}, falling back")
        return None

    ws_forward: Callable[[bytes], None] = lambda _data: None
    try:
        if get_session(domain_name) is None:
            return None

        logger.info(f"Piggybacking CLI capture on web console for {domain_name}")

        session.input_paused.clear()
        session.pty_read_paused.clear()
        set_console_control_state(
            domain_name,
            state="read_only",
            message="Configuration in progress. Console is view-only.",
        )

        import time
        time.sleep(0.2)

        def _ws_forward(data: bytes) -> None:
            try:
                asyncio.run_coroutine_threadsafe(
                    session.websocket.send_bytes(data),
                    session.loop,
                ).result(timeout=2)
            except Exception:
                pass

        ws_forward = _ws_forward

        injector = PtyInjector(
            fd=session.master_fd,
            ws_forward=ws_forward,
            default_timeout=timeout,
        )

        # Prime the prompt
        injector.send("\x15")
        time.sleep(0.1)
        injector.sendline("")

        prompt_text = injector.expect(prompt_pattern, timeout=10)
        prompt_match = (getattr(injector, "last_match", "") or "").strip()

        # Exit config mode if needed
        if "(config" in prompt_match or "(config" in prompt_text:
            injector.sendline("end")
            injector.expect(prompt_pattern, timeout=5)
            prompt_match = (getattr(injector, "last_match", "") or "").strip()

        # Handle login if needed
        if username and ("Username:" in prompt_text or "Login:" in prompt_text):
            injector.sendline(username)
            injector.expect(r"[Pp]assword:", timeout=10)
            injector.sendline(password)
            injector.expect(prompt_pattern, timeout=10)
            prompt_match = (getattr(injector, "last_match", "") or "").strip()

        # Enable mode
        if attempt_enable and prompt_match.endswith(">"):
            injector.sendline("enable")
            try:
                injector.expect(r"[Pp]assword:", timeout=5)
                injector.sendline(enable_password or "")
                injector.expect(prompt_pattern, timeout=10)
            except TimeoutError:
                try:
                    injector.expect(prompt_pattern, timeout=3)
                except TimeoutError:
                    pass

        # Disable paging
        if paging_disable:
            injector.sendline(paging_disable)
            try:
                injector.expect(prompt_pattern, timeout=5)
            except TimeoutError:
                pass

        # Execute commands and capture output
        commands_run = 0
        outputs: list[CommandOutput] = []
        for command in commands:
            logger.info(f"Piggyback capture running on {domain_name}: {command}")
            injector.sendline(command)
            try:
                output = injector.expect(prompt_pattern, timeout=timeout)
            except TimeoutError:
                outputs.append(CommandOutput(
                    command=command,
                    success=False,
                    error="Timeout waiting for command output",
                ))
                continue

            if _contains_cli_error(output):
                outputs.append(CommandOutput(
                    command=command,
                    success=False,
                    output=output,
                    error=f"CLI rejected command: {command}",
                ))
                continue

            # Clean output: strip the echoed command and trailing prompt
            clean = output
            lines = clean.splitlines()
            if lines and command in lines[0]:
                lines = lines[1:]
            clean = "\n".join(lines).strip()

            outputs.append(CommandOutput(
                command=command,
                success=True,
                output=clean,
            ))
            commands_run += 1

        ws_forward(
            b"\r\n\x1b[93m--- Automation complete; interactive control restored ---\x1b[0m\r\n"
        )

        success = commands_run == len(commands)
        error = ""
        if not success:
            failed = len(commands) - commands_run
            error = f"{failed} command(s) failed"

        logger.info(
            f"Piggyback CLI capture succeeded for {domain_name} "
            f"({commands_run}/{len(commands)} commands)"
        )
        return CommandCaptureResult(
            success=success,
            commands_run=commands_run,
            outputs=outputs,
            error=error,
        )

    except TimeoutError as e:
        ws_forward(
            b"\r\n\x1b[91m--- Automation timed out; restoring control ---\x1b[0m\r\n"
        )
        logger.warning(f"Piggyback CLI capture timed out for {domain_name}: {e}")
        return CommandCaptureResult(success=False, error=f"Piggyback timeout: {e}")

    except OSError as e:
        ws_forward(
            b"\r\n\x1b[91m--- Automation failed; restoring control ---\x1b[0m\r\n"
        )
        logger.warning(f"Piggyback CLI capture PTY error for {domain_name}: {e}")
        return CommandCaptureResult(success=False, error=f"PTY error: {e}")

    except Exception as e:
        logger.exception(
            f"Unexpected error in piggyback CLI capture for {domain_name}"
        )
        return CommandCaptureResult(success=False, error=f"Unexpected error: {e}")

    finally:
        set_console_control_state(
            domain_name,
            state="interactive",
            message="Configuration completed. Interactive control restored.",
        )
        session.input_paused.set()
        session.pty_read_paused.set()
        session._lock.release()
