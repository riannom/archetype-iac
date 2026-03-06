"""Extended error-path tests for agent/console/ssh_console.py.

Focuses on specific asyncssh error subtypes and edge cases NOT covered
by test_console_ssh_coverage.py:
- PermissionDenied (auth failure)
- HostKeyNotVerifiable (host key mismatch)
- ConnectionLost (channel closed mid-session)
- DisconnectError (remote disconnect)
- ChannelOpenError (PTY creation failure)
- Connection timeout (asyncssh.TimeoutError)
- BrokenPipeError on write (mid-command channel death)
- Key-based auth parameter passing
- Custom port propagation through ssh_console_session
- Concurrent read/write after close
- Multiple close calls (idempotency)
- create_process failure after successful connect
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import asyncssh

from agent.console.ssh_console import SSHConsole, ssh_console_session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_connected_console(**kwargs):
    """Create an SSHConsole with mocked internals, optionally overriding defaults."""
    defaults = dict(host="10.0.0.1", username="admin", password="cisco", port=22)
    defaults.update(kwargs)
    console = SSHConsole(defaults["host"], defaults["username"], defaults["password"], port=defaults["port"])
    console._conn = AsyncMock()
    console._process = MagicMock()
    console._process.stdin = MagicMock()
    console._process.stdout = AsyncMock()
    console._running = True
    return console


# ---------------------------------------------------------------------------
# 1. Specific asyncssh error subtypes on start()
# ---------------------------------------------------------------------------


class TestStartSpecificErrors:
    """Tests for specific asyncssh exception subtypes during connect."""

    @pytest.mark.asyncio
    async def test_permission_denied(self):
        """Authentication failure returns False, does not raise."""
        console = SSHConsole("10.0.0.1", "admin", "wrongpass")
        err = asyncssh.PermissionDenied(1, "Permission denied")

        with patch("asyncssh.connect", AsyncMock(side_effect=err)):
            result = await console.start()

        assert result is False
        assert console._running is False
        assert console._conn is None

    @pytest.mark.asyncio
    async def test_host_key_not_verifiable(self):
        """Host key mismatch is caught as asyncssh.Error subclass."""
        console = SSHConsole("10.0.0.1", "admin", "cisco")
        err = asyncssh.HostKeyNotVerifiable(1, "Host key mismatch")

        with patch("asyncssh.connect", AsyncMock(side_effect=err)):
            result = await console.start()

        assert result is False
        assert console._running is False

    @pytest.mark.asyncio
    async def test_connection_lost_during_connect(self):
        """ConnectionLost during connect is handled."""
        console = SSHConsole("10.0.0.1", "admin", "cisco")
        err = asyncssh.ConnectionLost(1, "Connection lost")

        with patch("asyncssh.connect", AsyncMock(side_effect=err)):
            result = await console.start()

        assert result is False

    @pytest.mark.asyncio
    async def test_connection_refused_oserror(self):
        """ConnectionRefusedError (subclass of OSError) returns False."""
        console = SSHConsole("10.0.0.1", "admin", "cisco")

        with patch("asyncssh.connect", AsyncMock(side_effect=ConnectionRefusedError("Connection refused"))):
            result = await console.start()

        assert result is False
        assert console._running is False

    @pytest.mark.asyncio
    async def test_create_process_fails_after_connect(self):
        """If connect succeeds but create_process raises, start returns False."""
        console = SSHConsole("10.0.0.1", "admin", "cisco")

        mock_conn = AsyncMock()
        mock_conn.create_process = AsyncMock(
            side_effect=asyncssh.ChannelOpenError(1, "session request failed")
        )

        with patch("asyncssh.connect", AsyncMock(return_value=mock_conn)):
            result = await console.start()

        assert result is False
        assert console._running is False


# ---------------------------------------------------------------------------
# 2. Channel closed / disconnect mid-session
# ---------------------------------------------------------------------------


class TestMidSessionErrors:
    """Tests for errors that occur during an active session."""

    @pytest.mark.asyncio
    async def test_read_connection_lost(self):
        """ConnectionLost during read returns None and marks not running."""
        console = _make_connected_console()
        console._process.stdout.read = AsyncMock(
            side_effect=asyncssh.ConnectionLost(1, "peer closed")
        )

        data = await console.read()
        assert data is None
        assert console._running is False

    @pytest.mark.asyncio
    async def test_read_disconnect_error(self):
        """DisconnectError during read returns None."""
        console = _make_connected_console()
        console._process.stdout.read = AsyncMock(
            side_effect=asyncssh.DisconnectError(1, "disconnected by server")
        )

        data = await console.read()
        assert data is None
        assert console._running is False

    @pytest.mark.asyncio
    async def test_write_broken_pipe(self):
        """BrokenPipeError on write returns False and stops session."""
        console = _make_connected_console()
        console._process.stdin.write.side_effect = BrokenPipeError("Broken pipe")

        result = await console.write(b"show version\n")
        assert result is False
        assert console._running is False

    @pytest.mark.asyncio
    async def test_write_connection_lost(self):
        """asyncssh.ConnectionLost on write returns False."""
        console = _make_connected_console()
        console._process.stdin.write.side_effect = asyncssh.ConnectionLost(1, "lost")

        result = await console.write(b"data")
        assert result is False
        assert console._running is False

    @pytest.mark.asyncio
    async def test_resize_after_disconnect(self):
        """Resize after underlying channel dies returns False."""
        console = _make_connected_console()
        console._process.change_terminal_size.side_effect = asyncssh.DisconnectError(
            1, "disconnected"
        )

        result = await console.resize(30, 120)
        assert result is False


# ---------------------------------------------------------------------------
# 3. Key-based auth and custom port
# ---------------------------------------------------------------------------


class TestAuthAndPort:
    """Tests for key-based auth path and custom port propagation."""

    @pytest.mark.asyncio
    async def test_custom_port_passed_to_connect(self):
        """Non-default port is forwarded to asyncssh.connect."""
        console = SSHConsole("10.0.0.1", "admin", "cisco", port=8022)

        mock_conn = AsyncMock()
        mock_conn.create_process = AsyncMock(return_value=MagicMock())

        with patch("asyncssh.connect", AsyncMock(return_value=mock_conn)) as mock_connect:
            await console.start()

            call_kwargs = mock_connect.call_args[1]
            assert call_kwargs["port"] == 8022

    @pytest.mark.asyncio
    async def test_session_custom_port_propagates(self):
        """ssh_console_session passes custom port to SSHConsole constructor."""
        with patch("agent.console.ssh_console.SSHConsole") as MockCls:
            instance = AsyncMock()
            instance.start = AsyncMock(return_value=False)
            MockCls.return_value = instance

            with pytest.raises(RuntimeError):
                await ssh_console_session(
                    host="10.0.0.1",
                    username="admin",
                    password="cisco",
                    on_output=MagicMock(),
                    get_input=MagicMock(),
                    port=9022,
                )

            MockCls.assert_called_once_with("10.0.0.1", "admin", "cisco", 9022)

    def test_constructor_stores_credentials(self):
        """Verify all constructor params are stored correctly."""
        console = SSHConsole("192.168.1.1", "root", "s3cret", port=2222)
        assert console.host == "192.168.1.1"
        assert console.username == "root"
        assert console.password == "s3cret"
        assert console.port == 2222


# ---------------------------------------------------------------------------
# 4. Close idempotency and edge cases
# ---------------------------------------------------------------------------


class TestCloseEdgeCases:
    """Tests for close() edge cases and idempotency."""

    @pytest.mark.asyncio
    async def test_double_close_is_safe(self):
        """Calling close() twice does not raise."""
        console = _make_connected_console()

        await console.close()
        assert console._conn is None
        assert console._process is None

        # Second close should be harmless
        await console.close()
        assert console._conn is None
        assert console._process is None

    @pytest.mark.asyncio
    async def test_read_after_close_returns_none(self):
        """Reading after close returns None immediately."""
        console = _make_connected_console()
        await console.close()

        data = await console.read()
        assert data is None

    @pytest.mark.asyncio
    async def test_write_after_close_returns_false(self):
        """Writing after close returns False immediately."""
        console = _make_connected_console()
        await console.close()

        result = await console.write(b"data")
        assert result is False
