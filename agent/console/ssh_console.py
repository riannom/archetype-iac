"""SSH-based console for vrnetlab/VM containers."""

from __future__ import annotations

import asyncio
import logging
from typing import Callable

import asyncssh

logger = logging.getLogger(__name__)


class SSHConsole:
    """Manage an interactive SSH console session to a container.

    Used for vrnetlab-based containers where the router/device console
    is accessed via SSH to the container's management IP, not docker exec.
    """

    def __init__(self, host: str, username: str, password: str, port: int = 22):
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self._conn: asyncssh.SSHClientConnection | None = None
        self._process: asyncssh.SSHClientProcess | None = None
        self._running = False

    async def start(self) -> bool:
        """Start an SSH session with PTY.

        Returns True if session started successfully.
        """
        try:
            # Connect with password auth, no host key checking
            self._conn = await asyncssh.connect(
                self.host,
                port=self.port,
                username=self.username,
                password=self.password,
                known_hosts=None,  # Disable host key checking
                connect_timeout=10,
            )

            # Start interactive shell with PTY
            self._process = await self._conn.create_process(
                term_type="xterm-256color",
                term_size=(80, 24),
            )

            self._running = True
            logger.info(f"SSH console connected to {self.host}")
            return True

        except asyncssh.Error as e:
            logger.error(f"SSH connection failed to {self.host}: {e}")
            return False
        except OSError as e:
            logger.error(f"Network error connecting to {self.host}: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error connecting to {self.host}: {e}")
            return False

    async def resize(self, rows: int, cols: int) -> bool:
        """Resize the PTY."""
        if not self._process:
            return False
        try:
            self._process.change_terminal_size(cols, rows)
            return True
        except Exception as e:
            logger.debug(f"Resize failed: {e}")
            return False

    async def write(self, data: bytes) -> bool:
        """Write data to the SSH session's stdin."""
        if not self._process or not self._running:
            return False
        try:
            self._process.stdin.write(data)
            return True
        except Exception as e:
            logger.debug(f"Write failed: {e}")
            self._running = False
            return False

    async def read(self, size: int = 4096) -> bytes | None:
        """Read data from the SSH session's stdout.

        Returns None if connection is closed or error occurs.
        """
        if not self._process or not self._running:
            return None
        try:
            data = await asyncio.wait_for(
                self._process.stdout.read(size),
                timeout=0.1,
            )
            if not data:
                self._running = False
                return None
            return data.encode() if isinstance(data, str) else data
        except asyncio.TimeoutError:
            return b""  # No data available
        except Exception as e:
            logger.debug(f"Read error: {e}")
            self._running = False
            return None

    @property
    def is_running(self) -> bool:
        """Check if the console session is still active."""
        return self._running

    async def close(self):
        """Close the SSH session."""
        self._running = False
        if self._process:
            try:
                self._process.close()
            except Exception:
                pass
            self._process = None
        if self._conn:
            try:
                self._conn.close()
                await self._conn.wait_closed()
            except Exception:
                pass
            self._conn = None


async def ssh_console_session(
    host: str,
    username: str,
    password: str,
    on_output: Callable[[bytes], None],
    get_input: Callable[[], bytes | None],
    port: int = 22,
) -> None:
    """Run an async SSH console session.

    Args:
        host: SSH host (container IP)
        username: SSH username
        password: SSH password
        on_output: Callback when output is available
        get_input: Callback to get input (returns None to exit)
        port: SSH port (default 22)
    """
    console = SSHConsole(host, username, password, port)

    if not await console.start():
        raise RuntimeError(f"Failed to start SSH console to {host}")

    try:
        while console.is_running:
            # Read output
            data = await console.read()
            if data is None:
                break
            if data:
                on_output(data)

            # Check for input
            input_data = get_input()
            if input_data is None:
                break
            if input_data:
                if not await console.write(input_data):
                    break

            # Small yield to other tasks
            await asyncio.sleep(0)

    finally:
        await console.close()
