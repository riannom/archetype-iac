"""Tests for agent/console/docker_exec.py — DockerConsole and console_session.

Covers:
- DockerConsole.start() — success, container not running, NotFound, APIError, generic
- DockerConsole.start_async() — async wrapper
- DockerConsole.read_blocking() — with data, timeout, closed, error
- DockerConsole.read() — data, no data (BlockingIOError), closed, error
- DockerConsole.read_nonblocking() — data available, no data, closed, error
- DockerConsole.write() — success, connection closed, error
- DockerConsole.resize() — success, no exec_id, error
- DockerConsole.get_socket_fileno() — socket available, not available, error
- DockerConsole.close() — clean close, already closed
- DockerConsole.is_running property
- console_session() coroutine — full lifecycle
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agent.console.docker_exec import DockerConsole, console_session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_socket():
    """Create a mock socket object with _sock."""
    mock = MagicMock()
    mock._sock = MagicMock()
    return mock


def _make_console_with_socket():
    """Create a DockerConsole with a mocked running session."""
    console = DockerConsole("test-container")
    console._socket = _make_mock_socket()
    console._exec_id = "exec-123"
    console._running = True
    return console


# ---------------------------------------------------------------------------
# 1. DockerConsole.start()
# ---------------------------------------------------------------------------


class TestDockerConsoleStart:
    """Tests for DockerConsole.start()."""

    def test_start_success(self):
        console = DockerConsole("my-container")

        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.id = "container-id-123"

        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container
        mock_client.api.exec_create.return_value = {"Id": "exec-abc"}
        mock_client.api.exec_start.return_value = _make_mock_socket()

        console._docker = mock_client

        result = console.start(shell="/bin/bash")

        assert result is True
        assert console._running is True
        assert console._exec_id == "exec-abc"
        mock_client.api.exec_create.assert_called_once_with(
            "container-id-123",
            "/bin/bash",
            stdin=True,
            stdout=True,
            stderr=True,
            tty=True,
        )

    def test_start_container_not_running(self):
        console = DockerConsole("my-container")

        mock_container = MagicMock()
        mock_container.status = "exited"

        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container
        console._docker = mock_client

        result = console.start()

        assert result is False
        assert console._running is False

    def test_start_container_not_found(self):
        import docker.errors

        console = DockerConsole("nonexistent")

        mock_client = MagicMock()
        mock_client.containers.get.side_effect = docker.errors.NotFound("not found")
        console._docker = mock_client

        result = console.start()
        assert result is False

    def test_start_api_error(self):
        import docker.errors

        console = DockerConsole("my-container")

        mock_client = MagicMock()
        mock_client.containers.get.side_effect = docker.errors.APIError("api error")
        console._docker = mock_client

        result = console.start()
        assert result is False

    def test_start_generic_exception(self):
        console = DockerConsole("my-container")

        mock_client = MagicMock()
        mock_client.containers.get.side_effect = RuntimeError("unexpected")
        console._docker = mock_client

        result = console.start()
        assert result is False


# ---------------------------------------------------------------------------
# 2. DockerConsole.start_async()
# ---------------------------------------------------------------------------


class TestDockerConsoleStartAsync:
    """Tests for DockerConsole.start_async()."""

    @pytest.mark.asyncio
    async def test_start_async_delegates(self):
        console = DockerConsole("my-container")

        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.id = "cid"

        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container
        mock_client.api.exec_create.return_value = {"Id": "exec-1"}
        mock_client.api.exec_start.return_value = _make_mock_socket()
        console._docker = mock_client

        result = await console.start_async("/bin/sh")
        assert result is True


# ---------------------------------------------------------------------------
# 3. DockerConsole.read_blocking()
# ---------------------------------------------------------------------------


class TestDockerConsoleReadBlocking:
    """Tests for DockerConsole.read_blocking()."""

    def test_read_blocking_with_data(self):
        console = _make_console_with_socket()

        with patch("agent.console.docker_exec.DockerConsole.read_blocking"):
            # Test the actual method instead
            pass

        # Direct test
        console._socket._sock.recv.return_value = b"hello world"

        # Patch select.select to report ready
        with patch("select.select", return_value=([console._socket._sock], [], [])):
            data = console.read_blocking(timeout=1.0)

        assert data == b"hello world"

    def test_read_blocking_timeout(self):
        console = _make_console_with_socket()

        with patch("select.select", return_value=([], [], [])):
            data = console.read_blocking(timeout=0.01)

        assert data == b""  # Timeout returns empty bytes

    def test_read_blocking_connection_closed(self):
        console = _make_console_with_socket()
        console._socket._sock.recv.return_value = b""

        with patch("select.select", return_value=([console._socket._sock], [], [])):
            data = console.read_blocking()

        assert data is None
        assert console._running is False

    def test_read_blocking_no_socket(self):
        console = DockerConsole("test")
        console._running = True
        console._socket = None

        result = console.read_blocking()
        assert result is None

    def test_read_blocking_not_running(self):
        console = _make_console_with_socket()
        console._running = False

        result = console.read_blocking()
        assert result is None

    def test_read_blocking_exception(self):
        console = _make_console_with_socket()

        with patch("select.select", side_effect=OSError("broken pipe")):
            data = console.read_blocking()

        assert data is None
        assert console._running is False


# ---------------------------------------------------------------------------
# 4. DockerConsole.read() (non-blocking)
# ---------------------------------------------------------------------------


class TestDockerConsoleRead:
    """Tests for DockerConsole.read()."""

    def test_read_with_data(self):
        console = _make_console_with_socket()
        console._socket._sock.recv.return_value = b"output data"

        data = console.read()
        assert data == b"output data"

    def test_read_no_data_blocking_io(self):
        console = _make_console_with_socket()
        console._socket._sock.recv.side_effect = BlockingIOError

        data = console.read()
        assert data == b""

    def test_read_connection_closed(self):
        console = _make_console_with_socket()
        console._socket._sock.recv.return_value = b""

        data = console.read()
        assert data is None
        assert console._running is False

    def test_read_no_socket(self):
        console = DockerConsole("test")
        data = console.read()
        assert data is None

    def test_read_outer_exception(self):
        console = _make_console_with_socket()
        console._socket._sock.setblocking.side_effect = OSError("broken")

        data = console.read()
        assert data is None
        assert console._running is False


# ---------------------------------------------------------------------------
# 5. DockerConsole.read_nonblocking()
# ---------------------------------------------------------------------------


class TestDockerConsoleReadNonblocking:
    """Tests for DockerConsole.read_nonblocking()."""

    def test_data_available(self):
        console = _make_console_with_socket()
        console._socket._sock.recv.return_value = b"some data"

        data = console.read_nonblocking()
        assert data == b"some data"

    def test_no_data_available(self):
        console = _make_console_with_socket()
        console._socket._sock.recv.side_effect = BlockingIOError

        data = console.read_nonblocking()
        assert data == b""

    def test_connection_closed(self):
        console = _make_console_with_socket()
        console._socket._sock.recv.return_value = b""

        data = console.read_nonblocking()
        assert data is None
        assert console._running is False

    def test_not_running(self):
        console = _make_console_with_socket()
        console._running = False

        data = console.read_nonblocking()
        assert data is None

    def test_outer_exception(self):
        console = _make_console_with_socket()
        console._socket._sock.setblocking.side_effect = OSError("fail")

        data = console.read_nonblocking()
        assert data is None
        assert console._running is False


# ---------------------------------------------------------------------------
# 6. DockerConsole.write()
# ---------------------------------------------------------------------------


class TestDockerConsoleWrite:
    """Tests for DockerConsole.write()."""

    def test_write_success(self):
        console = _make_console_with_socket()

        result = console.write(b"input data")
        assert result is True
        console._socket._sock.sendall.assert_called_once_with(b"input data")

    def test_write_not_running(self):
        console = _make_console_with_socket()
        console._running = False

        result = console.write(b"data")
        assert result is False

    def test_write_no_socket(self):
        console = DockerConsole("test")
        console._running = True

        result = console.write(b"data")
        assert result is False

    def test_write_exception(self):
        console = _make_console_with_socket()
        console._socket._sock.sendall.side_effect = BrokenPipeError("closed")

        result = console.write(b"data")
        assert result is False
        assert console._running is False


# ---------------------------------------------------------------------------
# 7. DockerConsole.resize()
# ---------------------------------------------------------------------------


class TestDockerConsoleResize:
    """Tests for DockerConsole.resize()."""

    def test_resize_success(self):
        console = _make_console_with_socket()
        mock_client = MagicMock()
        console._docker = mock_client

        result = console.resize(24, 80)

        assert result is True
        mock_client.api.exec_resize.assert_called_once_with("exec-123", height=24, width=80)

    def test_resize_no_exec_id(self):
        console = DockerConsole("test")
        console._exec_id = None

        result = console.resize(24, 80)
        assert result is False

    def test_resize_exception(self):
        console = _make_console_with_socket()
        mock_client = MagicMock()
        mock_client.api.exec_resize.side_effect = RuntimeError("error")
        console._docker = mock_client

        result = console.resize(24, 80)
        assert result is False


# ---------------------------------------------------------------------------
# 8. DockerConsole.get_socket_fileno()
# ---------------------------------------------------------------------------


class TestDockerConsoleGetSocketFileno:
    """Tests for DockerConsole.get_socket_fileno()."""

    def test_socket_available(self):
        console = _make_console_with_socket()
        console._socket._sock.fileno.return_value = 42

        result = console.get_socket_fileno()
        assert result == 42

    def test_no_socket(self):
        console = DockerConsole("test")
        result = console.get_socket_fileno()
        assert result is None

    def test_not_running(self):
        console = _make_console_with_socket()
        console._running = False

        result = console.get_socket_fileno()
        assert result is None

    def test_fileno_exception(self):
        console = _make_console_with_socket()
        console._socket._sock.fileno.side_effect = OSError("closed")

        result = console.get_socket_fileno()
        assert result is None


# ---------------------------------------------------------------------------
# 9. DockerConsole.close()
# ---------------------------------------------------------------------------


class TestDockerConsoleClose:
    """Tests for DockerConsole.close()."""

    def test_clean_close(self):
        console = _make_console_with_socket()
        mock_socket = console._socket

        console.close()

        assert console._running is False
        assert console._socket is None
        assert console._exec_id is None
        mock_socket.close.assert_called_once()

    def test_close_already_closed(self):
        console = DockerConsole("test")
        console._running = False
        console._socket = None
        console._exec_id = None

        # Should not raise
        console.close()

    def test_close_socket_error(self):
        console = _make_console_with_socket()
        console._socket.close.side_effect = OSError("error")

        # Should not raise
        console.close()
        assert console._socket is None
        assert console._running is False


# ---------------------------------------------------------------------------
# 10. DockerConsole.is_running property
# ---------------------------------------------------------------------------


class TestDockerConsoleIsRunning:
    """Tests for DockerConsole.is_running property."""

    def test_initially_false(self):
        console = DockerConsole("test")
        assert console.is_running is False

    def test_true_after_start(self):
        console = _make_console_with_socket()
        assert console.is_running is True


# ---------------------------------------------------------------------------
# 11. DockerConsole.docker lazy property
# ---------------------------------------------------------------------------


class TestDockerConsoleLazyDocker:
    """Tests for DockerConsole.docker lazy init."""

    def test_lazy_init(self):
        console = DockerConsole("test")
        assert console._docker is None

        mock_client = MagicMock()
        with patch("docker.from_env", return_value=mock_client):
            client = console.docker

        assert client is mock_client
        assert console._docker is mock_client

    def test_returns_cached_client(self):
        console = DockerConsole("test")
        mock_client = MagicMock()
        console._docker = mock_client

        assert console.docker is mock_client


# ---------------------------------------------------------------------------
# 12. console_session() coroutine
# ---------------------------------------------------------------------------


class TestConsoleSession:
    """Tests for console_session() async helper."""

    @pytest.mark.asyncio
    async def test_session_start_failure(self):
        """Should raise RuntimeError when start fails."""
        with patch.object(DockerConsole, "start", return_value=False):
            with pytest.raises(RuntimeError, match="Failed to start console"):
                await console_session(
                    container_name="test-container",
                    on_output=MagicMock(),
                    get_input=MagicMock(),
                )

    @pytest.mark.asyncio
    async def test_session_connection_closed(self):
        """Session exits when read_blocking returns None (connection closed)."""
        output_data = []

        call_count = 0

        def fake_read_blocking(size=4096, timeout=0.1):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return b"welcome"
            return None  # Connection closed

        input_count = 0

        def fake_get_input():
            nonlocal input_count
            input_count += 1
            if input_count == 1:
                return b""  # Empty (no input yet)
            return None  # Exit

        with patch.object(DockerConsole, "start", return_value=True):
            with patch.object(DockerConsole, "read_blocking", side_effect=fake_read_blocking):
                with patch.object(DockerConsole, "close"):
                    with patch.object(
                        DockerConsole, "is_running",
                        new_callable=lambda: property(lambda self: True),
                    ):
                        await console_session(
                            container_name="test-container",
                            on_output=lambda d: output_data.append(d),
                            get_input=fake_get_input,
                            check_interval=0.001,
                        )

        assert b"welcome" in output_data

    @pytest.mark.asyncio
    async def test_session_write_failure_breaks(self):
        """Session breaks when write fails."""
        read_count = 0

        def fake_read_blocking(size=4096, timeout=0.1):
            nonlocal read_count
            read_count += 1
            if read_count <= 2:
                return b""  # Timeout, no data
            return b"data"

        input_count = 0

        def fake_get_input():
            nonlocal input_count
            input_count += 1
            if input_count == 1:
                return b""  # Empty, skip
            return b"some input"

        with patch.object(DockerConsole, "start", return_value=True):
            with patch.object(DockerConsole, "read_blocking", side_effect=fake_read_blocking):
                with patch.object(DockerConsole, "write", return_value=False):
                    with patch.object(DockerConsole, "close"):
                        with patch.object(
                            DockerConsole, "is_running",
                            new_callable=lambda: property(lambda self: True),
                        ):
                            await console_session(
                                container_name="test",
                                on_output=MagicMock(),
                                get_input=fake_get_input,
                                check_interval=0.001,
                            )

    @pytest.mark.asyncio
    async def test_session_close_called_on_exit(self):
        """Close is always called, even on early exit."""
        close_called = False
        original_close = DockerConsole.close

        def tracking_close(self):
            nonlocal close_called
            close_called = True
            original_close(self)

        with patch.object(DockerConsole, "start", return_value=True):
            with patch.object(DockerConsole, "read_blocking", return_value=None):
                with patch.object(DockerConsole, "close", tracking_close):
                    await console_session(
                        container_name="test",
                        on_output=MagicMock(),
                        get_input=MagicMock(return_value=None),
                        check_interval=0.001,
                    )

        assert close_called
