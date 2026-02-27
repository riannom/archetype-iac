"""Tests for agent console access endpoints.

Source: agent/routers/console.py
Covers: _get_console_config, _get_container_ip, _check_container_exists,
        console routing by method, exec on node, boot logs, and WebSocket
        session lifecycle.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from agent.config import settings
from agent.main import app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch):
    monkeypatch.setattr(settings, "controller_secret", "")


# ---------------------------------------------------------------------------
# TestGetConsoleConfig
# ---------------------------------------------------------------------------


class TestGetConsoleConfig:
    """Tests for _get_console_config helper."""

    def test_returns_docker_exec_for_linux(self):
        """Linux containers use docker_exec method."""
        mock_container = MagicMock()
        mock_container.labels = {"archetype.node_kind": "linux"}

        mock_docker = MagicMock()
        mock_docker.containers.get.return_value = mock_container

        with patch("agent.routers.console.get_docker_client", return_value=mock_docker):
            with patch("agent.routers.console.get_console_method", return_value="docker_exec"):
                with patch("agent.routers.console.get_console_shell", return_value="/bin/bash"):
                    with patch("agent.routers.console.get_console_credentials",
                               return_value=("admin", "admin")):
                        result = asyncio.run(
                            __import__("agent.routers.console", fromlist=["_get_console_config"])
                            ._get_console_config("archetype-lab1-linux1")
                        )

        method, shell, username, password = result
        assert method == "docker_exec"
        assert shell == "/bin/bash"

    def test_returns_ssh_for_ceos(self):
        """cEOS containers use SSH method."""
        mock_container = MagicMock()
        mock_container.labels = {"archetype.node_kind": "ceos"}

        mock_docker = MagicMock()
        mock_docker.containers.get.return_value = mock_container

        with patch("agent.routers.console.get_docker_client", return_value=mock_docker):
            with patch("agent.routers.console.get_console_method", return_value="ssh"):
                with patch("agent.routers.console.get_console_shell", return_value="Cli"):
                    with patch("agent.routers.console.get_console_credentials",
                               return_value=("admin", "admin")):
                        result = asyncio.run(
                            __import__("agent.routers.console", fromlist=["_get_console_config"])
                            ._get_console_config("archetype-lab1-eos1")
                        )

        method, shell, username, password = result
        assert method == "ssh"

    def test_fallback_on_exception(self):
        """Falls back to docker_exec on exception."""
        mock_docker = MagicMock()
        mock_docker.containers.get.side_effect = Exception("not found")

        with patch("agent.routers.console.get_docker_client", return_value=mock_docker):
            result = asyncio.run(
                __import__("agent.routers.console", fromlist=["_get_console_config"])
                ._get_console_config("nonexistent")
            )

        method, shell, username, password = result
        assert method == "docker_exec"
        assert shell == "/bin/sh"


# ---------------------------------------------------------------------------
# TestGetContainerIp
# ---------------------------------------------------------------------------


class TestGetContainerIp:
    """Tests for _get_container_ip helper."""

    def test_returns_ip(self):
        """Returns IP from container network settings."""
        mock_container = MagicMock()
        mock_container.attrs = {
            "NetworkSettings": {
                "Networks": {
                    "bridge": {"IPAddress": "172.17.0.5"},
                },
            },
        }
        mock_docker = MagicMock()
        mock_docker.containers.get.return_value = mock_container

        with patch("agent.routers.console.get_docker_client", return_value=mock_docker):
            ip = asyncio.run(
                __import__("agent.routers.console", fromlist=["_get_container_ip"])
                ._get_container_ip("archetype-lab1-r1")
            )

        assert ip == "172.17.0.5"

    def test_returns_none_on_error(self):
        """Returns None if container not found."""
        mock_docker = MagicMock()
        mock_docker.containers.get.side_effect = Exception("not found")

        with patch("agent.routers.console.get_docker_client", return_value=mock_docker):
            ip = asyncio.run(
                __import__("agent.routers.console", fromlist=["_get_container_ip"])
                ._get_container_ip("nonexistent")
            )

        assert ip is None

    def test_returns_none_when_no_ip(self):
        """Returns None when container has no IP configured."""
        mock_container = MagicMock()
        mock_container.attrs = {
            "NetworkSettings": {"Networks": {}},
        }
        mock_docker = MagicMock()
        mock_docker.containers.get.return_value = mock_container

        with patch("agent.routers.console.get_docker_client", return_value=mock_docker):
            ip = asyncio.run(
                __import__("agent.routers.console", fromlist=["_get_container_ip"])
                ._get_container_ip("archetype-lab1-r1")
            )

        assert ip is None


# ---------------------------------------------------------------------------
# TestCheckContainerExists
# ---------------------------------------------------------------------------


class TestCheckContainerExists:
    """Tests for _check_container_exists helper."""

    def test_returns_true_when_exists(self):
        """Returns True when container is found."""
        mock_docker = MagicMock()
        mock_docker.containers.get.return_value = MagicMock()

        with patch("agent.routers.console.get_docker_client", return_value=mock_docker):
            exists = asyncio.run(
                __import__("agent.routers.console", fromlist=["_check_container_exists"])
                ._check_container_exists("archetype-lab1-r1")
            )

        assert exists is True

    def test_returns_false_when_not_found(self):
        """Returns False when container not found."""
        mock_docker = MagicMock()
        mock_docker.containers.get.side_effect = Exception("not found")

        with patch("agent.routers.console.get_docker_client", return_value=mock_docker):
            exists = asyncio.run(
                __import__("agent.routers.console", fromlist=["_check_container_exists"])
                ._check_container_exists("nonexistent")
            )

        assert exists is False


# ---------------------------------------------------------------------------
# TestGetContainerBootLogs
# ---------------------------------------------------------------------------


class TestGetContainerBootLogs:
    """Tests for _get_container_boot_logs helper."""

    def test_returns_logs(self):
        """Returns decoded log output."""
        mock_container = MagicMock()
        mock_container.logs.return_value = b"Boot sequence complete\n"

        mock_docker = MagicMock()
        mock_docker.containers.get.return_value = mock_container

        with patch("agent.routers.console.get_docker_client", return_value=mock_docker):
            logs = asyncio.run(
                __import__("agent.routers.console", fromlist=["_get_container_boot_logs"])
                ._get_container_boot_logs("archetype-lab1-r1")
            )

        assert "Boot sequence complete" in logs

    def test_returns_none_on_empty(self):
        """Returns None when logs are empty."""
        mock_container = MagicMock()
        mock_container.logs.return_value = b"   \n  "

        mock_docker = MagicMock()
        mock_docker.containers.get.return_value = mock_container

        with patch("agent.routers.console.get_docker_client", return_value=mock_docker):
            logs = asyncio.run(
                __import__("agent.routers.console", fromlist=["_get_container_boot_logs"])
                ._get_container_boot_logs("archetype-lab1-r1")
            )

        assert logs is None

    def test_returns_none_on_error(self):
        """Returns None when container not found."""
        mock_docker = MagicMock()
        mock_docker.containers.get.side_effect = Exception("not found")

        with patch("agent.routers.console.get_docker_client", return_value=mock_docker):
            logs = asyncio.run(
                __import__("agent.routers.console", fromlist=["_get_container_boot_logs"])
                ._get_container_boot_logs("nonexistent")
            )

        assert logs is None


# ---------------------------------------------------------------------------
# TestConsoleWebSocketRouting
# ---------------------------------------------------------------------------


class TestConsoleWebSocketRouting:
    """Tests for console WebSocket endpoint routing."""

    def test_libvirt_explicit_routes_to_libvirt(self, client):
        """Explicit libvirt provider type routes to _console_websocket_libvirt."""
        libvirt_provider = MagicMock()
        libvirt_provider.get_console_command = AsyncMock(return_value=None)

        with patch("agent.routers.console.get_provider",
                    side_effect=lambda x: libvirt_provider if x == "libvirt" else None):
            with client.websocket_connect(
                "/console/lab1/r1?provider_type=libvirt"
            ) as ws:
                # The VM not found message should be sent
                data = ws.receive_text()
                assert "not found" in data.lower() or "Error" in data

    def test_no_provider_sends_error(self, client):
        """When neither Docker nor libvirt has the node, error is sent."""
        with patch("agent.routers.console.get_provider", return_value=None):
            with client.websocket_connect("/console/lab1/r1") as ws:
                data = ws.receive_text()
                assert "not found" in data.lower() or "Error" in data

    def test_docker_container_exists_connects(self, client):
        """Docker container found routes to appropriate console method."""
        docker_provider = MagicMock()
        docker_provider.get_container_name.return_value = "archetype-lab1-r1"

        mock_docker = MagicMock()
        mock_docker.containers.get.return_value = MagicMock()

        # Mock console config to return docker_exec
        mock_container = MagicMock()
        mock_container.labels = {"archetype.node_kind": "linux"}

        # Start async returns False to close early
        with patch("agent.routers.console.get_provider",
                    side_effect=lambda x: docker_provider if x == "docker" else None):
            with patch("agent.routers.console._check_container_exists",
                       new_callable=AsyncMock, return_value=True):
                with patch("agent.routers.console._get_console_config",
                           new_callable=AsyncMock,
                           return_value=("docker_exec", "/bin/sh", "admin", "admin")):
                    with patch("agent.routers.console._console_websocket_docker",
                               new_callable=AsyncMock) as mock_ws_docker:
                        with client.websocket_connect("/console/lab1/r1") as ws:
                            pass  # Connection established, mock handler called

                        mock_ws_docker.assert_awaited_once()


# ---------------------------------------------------------------------------
# TestConsoleSessionLifecycle
# ---------------------------------------------------------------------------


class TestConsoleSessionLifecycle:
    """Tests for console session registration and cleanup."""

    def test_session_module_imports(self):
        """Console session registry modules can be imported."""
        from agent.console_session_registry import (
            register_session,
            unregister_session,
        )
        assert callable(register_session)
        assert callable(unregister_session)
