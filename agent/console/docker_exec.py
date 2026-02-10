"""Docker exec console with PTY support."""

from __future__ import annotations

import asyncio
from typing import Callable

import docker
from docker.errors import NotFound, APIError

from agent.config import settings


async def _run_in_thread(func, *args, **kwargs):
    """Run a blocking function in a thread pool."""
    import functools
    return await asyncio.to_thread(functools.partial(func, *args, **kwargs))


class DockerConsole:
    """Manage an interactive console session to a Docker container.

    Uses Docker's exec API with PTY for proper terminal emulation.
    """

    def __init__(self, container_name: str):
        self.container_name = container_name
        self._docker: docker.DockerClient | None = None
        self._exec_id: str | None = None
        self._socket = None
        self._running = False

    @property
    def docker(self) -> docker.DockerClient:
        """Lazy-initialize Docker client."""
        if self._docker is None:
            self._docker = docker.from_env()
        return self._docker

    def start(self, shell: str = "/bin/sh") -> bool:
        """Start an exec session with PTY (synchronous version).

        Returns True if session started successfully.

        Note: For async contexts, use start_async() instead to avoid blocking.
        """
        try:
            container = self.docker.containers.get(self.container_name)
            if container.status != "running":
                return False

            # Create exec instance with PTY
            exec_instance = self.docker.api.exec_create(
                container.id,
                shell,
                stdin=True,
                stdout=True,
                stderr=True,
                tty=True,
            )
            self._exec_id = exec_instance["Id"]

            # Start exec and get socket
            self._socket = self.docker.api.exec_start(
                self._exec_id,
                socket=True,
                tty=True,
            )
            self._running = True
            return True

        except NotFound:
            return False
        except APIError:
            return False
        except Exception:
            return False

    async def start_async(self, shell: str = "/bin/sh") -> bool:
        """Start an exec session with PTY (async version).

        Wraps the blocking Docker calls in asyncio.to_thread() to avoid
        blocking the event loop.

        Returns True if session started successfully.
        """
        return await asyncio.to_thread(self.start, shell)

    def resize(self, rows: int, cols: int) -> bool:
        """Resize the PTY."""
        if not self._exec_id:
            return False
        try:
            self.docker.api.exec_resize(self._exec_id, height=rows, width=cols)
            return True
        except Exception:
            return False

    def write(self, data: bytes) -> bool:
        """Write data to the container's stdin."""
        if not self._socket or not self._running:
            return False
        try:
            self._socket._sock.sendall(data)
            return True
        except Exception:
            self._running = False
            return False

    def read(self, size: int = 4096) -> bytes | None:
        """Read data from the container's stdout/stderr.

        Returns None if connection is closed or error occurs.
        """
        if not self._socket or not self._running:
            return None
        try:
            # Set socket to non-blocking for async compatibility
            self._socket._sock.setblocking(False)
            try:
                data = self._socket._sock.recv(size)
                if not data:
                    self._running = False
                    return None
                return data
            except BlockingIOError:
                return b""  # No data available
        except Exception:
            self._running = False
            return None

    def read_blocking(self, size: int = 4096, timeout: float = 0.1) -> bytes | None:
        """Read data with a timeout.

        Returns None if connection is closed or error occurs.
        Returns empty bytes if timeout reached.
        """
        if not self._socket or not self._running:
            return None
        try:
            import select
            sock = self._socket._sock
            ready, _, _ = select.select([sock], [], [], timeout)
            if ready:
                data = sock.recv(size)
                if not data:
                    self._running = False
                    return None
                return data
            return b""  # Timeout, no data
        except Exception:
            self._running = False
            return None

    @property
    def is_running(self) -> bool:
        """Check if the console session is still active."""
        return self._running

    def get_socket_fileno(self) -> int | None:
        """Get the raw socket file descriptor for event-driven I/O.

        Returns the file descriptor number, or None if socket not available.
        """
        if not self._socket or not self._running:
            return None
        try:
            return self._socket._sock.fileno()
        except Exception:
            return None

    def read_nonblocking(self, size: int = 4096) -> bytes | None:
        """Read immediately available data without any blocking.

        Returns:
            - bytes: Data that was available
            - b"": No data available (would block)
            - None: Connection closed or error
        """
        if not self._socket or not self._running:
            return None
        try:
            self._socket._sock.setblocking(False)
            try:
                data = self._socket._sock.recv(size)
                if not data:
                    self._running = False
                    return None
                return data
            except BlockingIOError:
                return b""  # No data available right now
        except Exception:
            self._running = False
            return None

    def close(self):
        """Close the console session."""
        self._running = False
        if self._socket:
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None
        self._exec_id = None


async def console_session(
    container_name: str,
    on_output: Callable[[bytes], None],
    get_input: Callable[[], bytes | None],
    shell: str = "/bin/sh",
    check_interval: float | None = None,
) -> None:
    """Run an async console session.

    Args:
        container_name: Name of the Docker container
        on_output: Callback when output is available
        get_input: Callback to get input (returns None to exit)
        shell: Shell to use
        check_interval: Interval to check for I/O
    """
    if check_interval is None:
        check_interval = settings.console_read_timeout

    console = DockerConsole(container_name)

    if not console.start(shell):
        raise RuntimeError(f"Failed to start console for {container_name}")

    try:
        while console.is_running:
            # Read output
            data = console.read_blocking(timeout=check_interval)
            if data is None:
                break  # Connection closed
            if data:
                on_output(data)

            # Check for input
            input_data = get_input()
            if input_data is None:
                break  # Client requested exit
            if input_data:
                if not console.write(input_data):
                    break

            # Small yield to other tasks
            await asyncio.sleep(0)

    finally:
        console.close()
