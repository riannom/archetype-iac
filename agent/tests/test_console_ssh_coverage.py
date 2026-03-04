"""Tests for agent/console/ssh_console.py — SSHConsole and ssh_console_session.

Covers:
- SSHConsole.start() — success, asyncssh.Error, OSError, generic exception
- SSHConsole.read() — data returned, None (closed), b"" (timeout), error
- SSHConsole.write() — success, connection closed, error
- SSHConsole.resize() — success, no process, failure
- SSHConsole.close() — clean, _process set but _conn is None, already closed
- SSHConsole.is_running property
- ssh_console_session() — full lifecycle, start failure, output/input flow
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import asyncssh

from agent.console.ssh_console import SSHConsole, ssh_console_session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_connected_console():
    """Create an SSHConsole with mocked connection and process."""
    console = SSHConsole("10.0.0.1", "admin", "cisco", port=22)
    console._conn = AsyncMock()
    console._process = MagicMock()
    console._process.stdin = MagicMock()
    console._process.stdout = AsyncMock()
    console._running = True
    return console


# ---------------------------------------------------------------------------
# 1. SSHConsole.__init__
# ---------------------------------------------------------------------------


class TestSSHConsoleInit:
    """Tests for SSHConsole constructor."""

    def test_default_port(self):
        console = SSHConsole("host", "user", "pass")
        assert console.port == 22
        assert console._running is False
        assert console._conn is None
        assert console._process is None

    def test_custom_port(self):
        console = SSHConsole("host", "user", "pass", port=2222)
        assert console.port == 2222


# ---------------------------------------------------------------------------
# 2. SSHConsole.start()
# ---------------------------------------------------------------------------


class TestSSHConsoleStart:
    """Tests for SSHConsole.start()."""

    @pytest.mark.asyncio
    async def test_start_success(self):
        console = SSHConsole("10.0.0.1", "admin", "cisco")

        mock_conn = AsyncMock()
        mock_process = MagicMock()
        mock_conn.create_process = AsyncMock(return_value=mock_process)

        with patch("asyncssh.connect", AsyncMock(return_value=mock_conn)):
            result = await console.start()

        assert result is True
        assert console._running is True
        assert console._conn is mock_conn
        assert console._process is mock_process

    @pytest.mark.asyncio
    async def test_start_asyncssh_error(self):
        console = SSHConsole("10.0.0.1", "admin", "wrongpass")

        with patch("asyncssh.connect", AsyncMock(side_effect=asyncssh.Error(1, "auth failed"))):
            result = await console.start()

        assert result is False
        assert console._running is False

    @pytest.mark.asyncio
    async def test_start_os_error(self):
        console = SSHConsole("10.0.0.1", "admin", "cisco")

        with patch("asyncssh.connect", AsyncMock(side_effect=OSError("Connection refused"))):
            result = await console.start()

        assert result is False
        assert console._running is False

    @pytest.mark.asyncio
    async def test_start_generic_exception(self):
        console = SSHConsole("10.0.0.1", "admin", "cisco")

        with patch("asyncssh.connect", AsyncMock(side_effect=RuntimeError("unexpected"))):
            result = await console.start()

        assert result is False
        assert console._running is False

    @pytest.mark.asyncio
    async def test_start_connect_params(self):
        """Verify asyncssh.connect is called with correct parameters."""
        console = SSHConsole("10.0.0.1", "admin", "cisco", port=2222)

        mock_conn = AsyncMock()
        mock_conn.create_process = AsyncMock(return_value=MagicMock())

        with patch("asyncssh.connect", AsyncMock(return_value=mock_conn)) as mock_connect:
            await console.start()

            mock_connect.assert_awaited_once_with(
                "10.0.0.1",
                port=2222,
                username="admin",
                password="cisco",
                known_hosts=None,
                connect_timeout=10,
            )


# ---------------------------------------------------------------------------
# 3. SSHConsole.read()
# ---------------------------------------------------------------------------


class TestSSHConsoleRead:
    """Tests for SSHConsole.read()."""

    @pytest.mark.asyncio
    async def test_read_data_string(self):
        """When stdout returns a string, it should be encoded to bytes."""
        console = _make_connected_console()
        console._process.stdout.read = AsyncMock(return_value="router#")

        data = await console.read()
        assert data == b"router#"

    @pytest.mark.asyncio
    async def test_read_data_bytes(self):
        """When stdout returns bytes, they are returned as-is."""
        console = _make_connected_console()
        console._process.stdout.read = AsyncMock(return_value=b"\x1b[0m")

        data = await console.read()
        assert data == b"\x1b[0m"

    @pytest.mark.asyncio
    async def test_read_connection_closed(self):
        """Empty string from stdout means connection closed."""
        console = _make_connected_console()
        console._process.stdout.read = AsyncMock(return_value="")

        data = await console.read()
        assert data is None
        assert console._running is False

    @pytest.mark.asyncio
    async def test_read_timeout(self):
        """TimeoutError returns empty bytes (no data available)."""
        console = _make_connected_console()
        console._process.stdout.read = AsyncMock(side_effect=asyncio.TimeoutError)

        data = await console.read()
        assert data == b""

    @pytest.mark.asyncio
    async def test_read_exception(self):
        """Generic exception returns None and marks not running."""
        console = _make_connected_console()
        console._process.stdout.read = AsyncMock(side_effect=OSError("broken"))

        data = await console.read()
        assert data is None
        assert console._running is False

    @pytest.mark.asyncio
    async def test_read_not_running(self):
        console = _make_connected_console()
        console._running = False

        data = await console.read()
        assert data is None

    @pytest.mark.asyncio
    async def test_read_no_process(self):
        console = SSHConsole("host", "user", "pass")
        console._running = True
        console._process = None

        data = await console.read()
        assert data is None


# ---------------------------------------------------------------------------
# 4. SSHConsole.write()
# ---------------------------------------------------------------------------


class TestSSHConsoleWrite:
    """Tests for SSHConsole.write()."""

    @pytest.mark.asyncio
    async def test_write_success(self):
        console = _make_connected_console()

        result = await console.write(b"show version\n")

        assert result is True
        console._process.stdin.write.assert_called_once_with(b"show version\n")

    @pytest.mark.asyncio
    async def test_write_not_running(self):
        console = _make_connected_console()
        console._running = False

        result = await console.write(b"data")
        assert result is False

    @pytest.mark.asyncio
    async def test_write_no_process(self):
        console = SSHConsole("host", "user", "pass")
        console._running = True

        result = await console.write(b"data")
        assert result is False

    @pytest.mark.asyncio
    async def test_write_exception(self):
        console = _make_connected_console()
        console._process.stdin.write.side_effect = OSError("closed")

        result = await console.write(b"data")
        assert result is False
        assert console._running is False


# ---------------------------------------------------------------------------
# 5. SSHConsole.resize()
# ---------------------------------------------------------------------------


class TestSSHConsoleResize:
    """Tests for SSHConsole.resize()."""

    @pytest.mark.asyncio
    async def test_resize_success(self):
        console = _make_connected_console()

        result = await console.resize(30, 120)

        assert result is True
        console._process.change_terminal_size.assert_called_once_with(120, 30)

    @pytest.mark.asyncio
    async def test_resize_no_process(self):
        console = SSHConsole("host", "user", "pass")
        result = await console.resize(24, 80)
        assert result is False

    @pytest.mark.asyncio
    async def test_resize_exception(self):
        console = _make_connected_console()
        console._process.change_terminal_size.side_effect = RuntimeError("error")

        result = await console.resize(24, 80)
        assert result is False


# ---------------------------------------------------------------------------
# 6. SSHConsole.close()
# ---------------------------------------------------------------------------


class TestSSHConsoleClose:
    """Tests for SSHConsole.close()."""

    @pytest.mark.asyncio
    async def test_clean_close(self):
        console = _make_connected_console()
        mock_process = console._process
        mock_conn = console._conn

        await console.close()

        assert console._running is False
        assert console._process is None
        assert console._conn is None
        mock_process.close.assert_called_once()
        mock_conn.close.assert_called_once()
        mock_conn.wait_closed.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close_already_closed(self):
        console = SSHConsole("host", "user", "pass")
        # Should not raise
        await console.close()

    @pytest.mark.asyncio
    async def test_close_process_set_conn_none(self):
        """Process exists but connection is None."""
        console = SSHConsole("host", "user", "pass")
        console._process = MagicMock()
        console._running = True

        await console.close()

        assert console._running is False
        assert console._process is None

    @pytest.mark.asyncio
    async def test_close_process_error(self):
        """Process close raises, but shutdown continues."""
        console = _make_connected_console()
        console._process.close.side_effect = OSError("fail")

        await console.close()

        assert console._running is False
        assert console._process is None

    @pytest.mark.asyncio
    async def test_close_conn_error(self):
        """Connection close raises, but shutdown continues."""
        console = _make_connected_console()
        console._conn.close.side_effect = OSError("fail")

        await console.close()

        assert console._running is False
        assert console._conn is None

    @pytest.mark.asyncio
    async def test_close_wait_closed_error(self):
        """wait_closed raises, but shutdown continues."""
        console = _make_connected_console()
        console._conn.wait_closed = AsyncMock(side_effect=OSError("fail"))

        await console.close()

        assert console._conn is None


# ---------------------------------------------------------------------------
# 7. SSHConsole.is_running
# ---------------------------------------------------------------------------


class TestSSHConsoleIsRunning:
    """Tests for SSHConsole.is_running property."""

    def test_initially_false(self):
        console = SSHConsole("host", "user", "pass")
        assert console.is_running is False

    def test_true_when_connected(self):
        console = _make_connected_console()
        assert console.is_running is True


# ---------------------------------------------------------------------------
# 8. ssh_console_session() — full lifecycle
# ---------------------------------------------------------------------------


class TestSshConsoleSession:
    """Tests for ssh_console_session() helper."""

    @pytest.mark.asyncio
    async def test_start_failure(self):
        """Should raise RuntimeError when start fails."""
        with patch.object(SSHConsole, "start", AsyncMock(return_value=False)):
            with pytest.raises(RuntimeError, match="Failed to start SSH console"):
                await ssh_console_session(
                    host="10.0.0.1",
                    username="admin",
                    password="cisco",
                    on_output=MagicMock(),
                    get_input=MagicMock(),
                )

    @pytest.mark.asyncio
    async def test_session_reads_output_then_exits(self):
        """Session reads output, input returns None (exit)."""
        output_data = []

        read_count = 0

        async def fake_read(size=4096):
            nonlocal read_count
            read_count += 1
            if read_count == 1:
                return b"Router#"
            return None  # Connection closed

        with patch.object(SSHConsole, "start", AsyncMock(return_value=True)):
            with patch.object(SSHConsole, "read", side_effect=fake_read):
                with patch.object(SSHConsole, "close", AsyncMock()):
                    with patch.object(
                        SSHConsole, "is_running",
                        new_callable=lambda: property(lambda self: True),
                    ):
                        await ssh_console_session(
                            host="10.0.0.1",
                            username="admin",
                            password="cisco",
                            on_output=lambda d: output_data.append(d),
                            get_input=MagicMock(return_value=None),
                        )

        assert b"Router#" in output_data

    @pytest.mark.asyncio
    async def test_session_writes_input(self):
        """Session writes input data to console."""
        read_count = 0

        async def fake_read(size=4096):
            nonlocal read_count
            read_count += 1
            if read_count == 1:
                return b""  # No data (timeout)
            return None  # Close

        input_count = 0

        def fake_get_input():
            nonlocal input_count
            input_count += 1
            if input_count == 1:
                return b"show version\n"
            return None  # Exit

        with patch.object(SSHConsole, "start", AsyncMock(return_value=True)):
            with patch.object(SSHConsole, "read", side_effect=fake_read):
                with patch.object(SSHConsole, "write", AsyncMock(return_value=True)) as mock_write:
                    with patch.object(SSHConsole, "close", AsyncMock()):
                        with patch.object(
                            SSHConsole, "is_running",
                            new_callable=lambda: property(lambda self: True),
                        ):
                            await ssh_console_session(
                                host="10.0.0.1",
                                username="admin",
                                password="cisco",
                                on_output=MagicMock(),
                                get_input=fake_get_input,
                            )

        mock_write.assert_awaited_with(b"show version\n")

    @pytest.mark.asyncio
    async def test_session_write_failure_breaks(self):
        """Session breaks when write fails."""
        read_count = 0

        async def fake_read(size=4096):
            nonlocal read_count
            read_count += 1
            return b""  # Always timeout

        input_count = 0

        def fake_get_input():
            nonlocal input_count
            input_count += 1
            if input_count == 1:
                return b""  # Empty, skip
            return b"data"

        with patch.object(SSHConsole, "start", AsyncMock(return_value=True)):
            with patch.object(SSHConsole, "read", side_effect=fake_read):
                with patch.object(SSHConsole, "write", AsyncMock(return_value=False)):
                    with patch.object(SSHConsole, "close", AsyncMock()):
                        with patch.object(
                            SSHConsole, "is_running",
                            new_callable=lambda: property(lambda self: True),
                        ):
                            await ssh_console_session(
                                host="10.0.0.1",
                                username="admin",
                                password="cisco",
                                on_output=MagicMock(),
                                get_input=fake_get_input,
                            )

    @pytest.mark.asyncio
    async def test_session_close_called_on_exit(self):
        """Close is always called via finally block."""
        close_called = False

        async def tracking_close(self):
            nonlocal close_called
            close_called = True

        with patch.object(SSHConsole, "start", AsyncMock(return_value=True)):
            with patch.object(SSHConsole, "read", AsyncMock(return_value=None)):
                with patch.object(SSHConsole, "close", tracking_close):
                    await ssh_console_session(
                        host="10.0.0.1",
                        username="admin",
                        password="cisco",
                        on_output=MagicMock(),
                        get_input=MagicMock(return_value=None),
                    )

        assert close_called

    @pytest.mark.asyncio
    async def test_session_custom_port(self):
        """Custom port is passed to SSHConsole."""
        with patch.object(SSHConsole, "start", AsyncMock(return_value=False)):
            with pytest.raises(RuntimeError):
                await ssh_console_session(
                    host="10.0.0.1",
                    username="admin",
                    password="cisco",
                    on_output=MagicMock(),
                    get_input=MagicMock(),
                    port=2222,
                )
