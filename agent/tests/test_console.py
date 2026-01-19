"""Tests for console reliability and functionality.

These tests verify that:
1. Docker console connections work
2. PTY allocation works
3. Terminal resize handling works
4. Long sessions remain stable
5. Reconnection works
"""

import asyncio
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from agent.console.docker_exec import DockerConsole


# --- Unit Tests for DockerConsole ---

def test_docker_console_init():
    """Test DockerConsole initialization."""
    console = DockerConsole("test-container")

    assert console.container_name == "test-container"
    assert not console.is_running


def test_docker_console_start_success():
    """Test successful console start."""
    console = DockerConsole("test-container")

    with patch('agent.console.docker_exec.docker.from_env') as mock_from_env:
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_socket = MagicMock()
        mock_socket._sock = MagicMock()

        mock_from_env.return_value = mock_client
        mock_client.containers.get.return_value = mock_container
        mock_client.api.exec_create.return_value = {"Id": "exec-123"}
        mock_client.api.exec_start.return_value = mock_socket

        result = console.start()

        assert result is True
        assert console.is_running
        mock_client.containers.get.assert_called_with("test-container")


def test_docker_console_start_container_not_found():
    """Test console start when container doesn't exist."""
    console = DockerConsole("nonexistent-container")

    with patch('agent.console.docker_exec.docker.from_env') as mock_from_env:
        mock_client = MagicMock()
        mock_from_env.return_value = mock_client

        from docker.errors import NotFound
        mock_client.containers.get.side_effect = NotFound("Container not found")

        result = console.start()

        assert result is False


def test_docker_console_start_container_not_running():
    """Test console start when container is not running."""
    console = DockerConsole("stopped-container")

    with patch('agent.console.docker_exec.docker.from_env') as mock_from_env:
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_container.status = "exited"

        mock_from_env.return_value = mock_client
        mock_client.containers.get.return_value = mock_container

        result = console.start()

        assert result is False


def test_docker_console_write():
    """Test writing to console."""
    console = DockerConsole("test-container")
    console._socket = MagicMock()
    console._socket._sock = MagicMock()
    console._running = True

    result = console.write(b"ls -la\n")

    assert result is True
    console._socket._sock.sendall.assert_called_with(b"ls -la\n")


def test_docker_console_write_when_not_running():
    """Test writing to console when not running."""
    console = DockerConsole("test-container")
    console._running = False

    result = console.write(b"ls -la\n")

    assert result is False


def test_docker_console_resize():
    """Test terminal resize."""
    console = DockerConsole("test-container")

    # Set up internal state - need to patch the docker property
    with patch('agent.console.docker_exec.docker.from_env') as mock_from_env:
        mock_client = MagicMock()
        mock_from_env.return_value = mock_client
        console._exec_id = "exec-123"

        # Access docker property to initialize _docker
        _ = console.docker

        result = console.resize(rows=40, cols=120)

        assert result is True
        mock_client.api.exec_resize.assert_called_with(
            "exec-123",
            height=40,
            width=120,
        )


def test_docker_console_resize_no_exec_id():
    """Test resize when no exec_id."""
    console = DockerConsole("test-container")
    console._exec_id = None

    result = console.resize(rows=40, cols=120)

    assert result is False


def test_docker_console_close():
    """Test closing console."""
    console = DockerConsole("test-container")
    mock_socket = MagicMock()
    console._socket = mock_socket
    console._running = True

    console.close()

    assert not console._running
    assert console._socket is None
    mock_socket.close.assert_called_once()


# --- Tests for Console Session Stability ---

class TestConsoleSessionStability:
    """Tests for long-running console sessions."""

    def test_console_handles_large_output(self):
        """Test that console handles large amounts of output."""
        console = DockerConsole("test-container")
        mock_socket = MagicMock()
        mock_sock = MagicMock()
        mock_socket._sock = mock_sock
        console._socket = mock_socket
        console._running = True

        # Simulate receiving large output
        large_data = b"x" * 65536  # 64KB of data
        mock_sock.recv.return_value = large_data

        with patch('select.select', return_value=([mock_sock], [], [])):
            data = console.read_blocking(timeout=0.1)

        assert len(data) == 65536

    def test_console_handles_binary_data(self):
        """Test that console handles binary data correctly."""
        console = DockerConsole("test-container")
        mock_socket = MagicMock()
        mock_sock = MagicMock()
        mock_socket._sock = mock_sock
        console._socket = mock_socket
        console._running = True

        # Binary data with all byte values
        binary_data = bytes(range(256))
        mock_sock.recv.return_value = binary_data

        with patch('select.select', return_value=([mock_sock], [], [])):
            data = console.read_blocking(timeout=0.1)

        assert data == binary_data

    def test_console_handles_utf8(self):
        """Test that console handles UTF-8 encoded text."""
        console = DockerConsole("test-container")
        mock_socket = MagicMock()
        mock_sock = MagicMock()
        mock_socket._sock = mock_sock
        console._socket = mock_socket
        console._running = True

        # UTF-8 text with special characters
        utf8_text = "Hello \u4e16\u754c \u263a".encode('utf-8')
        mock_sock.recv.return_value = utf8_text

        with patch('select.select', return_value=([mock_sock], [], [])):
            data = console.read_blocking(timeout=0.1)

        assert data == utf8_text

    def test_console_read_timeout(self):
        """Test that read returns empty bytes on timeout."""
        console = DockerConsole("test-container")
        mock_socket = MagicMock()
        mock_sock = MagicMock()
        mock_socket._sock = mock_sock
        console._socket = mock_socket
        console._running = True

        # Simulate timeout - select returns empty
        with patch('select.select', return_value=([], [], [])):
            data = console.read_blocking(timeout=0.1)

        assert data == b""

    def test_console_write_preserves_data(self):
        """Test that write preserves all data."""
        console = DockerConsole("test-container")
        console._socket = MagicMock()
        console._socket._sock = MagicMock()
        console._running = True

        # Control sequences
        data = b"\x1b[H\x1b[2J"  # Clear screen
        console.write(data)

        console._socket._sock.sendall.assert_called_with(data)


# --- Integration Test for Console Reconnection ---

class TestConsoleReconnection:
    """Tests for console reconnection scenarios."""

    def test_reconnect_after_disconnect(self):
        """Test that console can reconnect after disconnect."""
        with patch('agent.console.docker_exec.docker.from_env') as mock_from_env:
            mock_client = MagicMock()
            mock_container = MagicMock()
            mock_container.status = "running"
            mock_socket = MagicMock()
            mock_socket._sock = MagicMock()

            mock_from_env.return_value = mock_client
            mock_client.containers.get.return_value = mock_container
            mock_client.api.exec_create.return_value = {"Id": "exec-1"}
            mock_client.api.exec_start.return_value = mock_socket

            # First connection
            console = DockerConsole("test-container")
            result1 = console.start()
            assert result1 is True

            # Disconnect
            console.close()
            assert not console.is_running

            # Second connection (new console instance)
            mock_client.api.exec_create.return_value = {"Id": "exec-2"}
            console2 = DockerConsole("test-container")
            result2 = console2.start()
            assert result2 is True

    def test_graceful_close_on_container_exit(self):
        """Test that console closes gracefully when container exits."""
        console = DockerConsole("test-container")
        mock_socket = MagicMock()
        mock_sock = MagicMock()
        mock_socket._sock = mock_sock
        console._socket = mock_socket
        console._running = True

        # Simulate container exit - recv returns empty bytes
        mock_sock.recv.return_value = b""

        with patch('select.select', return_value=([mock_sock], [], [])):
            data = console.read_blocking(timeout=0.1)

        # Should return None indicating end of stream
        assert data is None
        assert not console.is_running


# --- Tests for Terminal Control Sequences ---

class TestTerminalControlSequences:
    """Tests for terminal control sequence handling."""

    def test_resize_sends_correct_command(self):
        """Test that resize sends correct API call."""
        console = DockerConsole("test-container")

        with patch('agent.console.docker_exec.docker.from_env') as mock_from_env:
            mock_client = MagicMock()
            mock_from_env.return_value = mock_client
            console._exec_id = "exec-123"

            # Initialize docker client
            _ = console.docker

            result = console.resize(rows=50, cols=200)

            assert result is True
            mock_client.api.exec_resize.assert_called_with(
                "exec-123",
                height=50,
                width=200,
            )

    def test_multiple_resizes(self):
        """Test multiple resize operations."""
        console = DockerConsole("test-container")

        with patch('agent.console.docker_exec.docker.from_env') as mock_from_env:
            mock_client = MagicMock()
            mock_from_env.return_value = mock_client
            console._exec_id = "exec-123"

            # Initialize docker client
            _ = console.docker

            # Multiple resizes
            console.resize(rows=24, cols=80)
            console.resize(rows=40, cols=120)
            console.resize(rows=60, cols=200)

            assert mock_client.api.exec_resize.call_count == 3


# To run these tests:
# cd agent && pytest tests/test_console.py -v
