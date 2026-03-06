"""Tests for agent/routers/console.py — helper functions and dispatch (round 11)."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch



def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# _get_console_config
# ---------------------------------------------------------------------------


class TestGetConsoleConfig:

    def test_returns_defaults_on_exception(self):
        from agent.routers.console import _get_console_config

        with patch("agent.routers.console.get_docker_client") as mock_dc:
            mock_dc.side_effect = RuntimeError("no docker")
            method, shell, user, pw = _run(_get_console_config("test-container"))
        assert method == "docker_exec"
        assert shell == "/bin/sh"

    def test_returns_ssh_for_ssh_kind(self):
        from agent.routers.console import _get_console_config

        mock_container = MagicMock()
        mock_container.labels = {"archetype.node_kind": "cisco_csr1000v"}

        with patch("agent.routers.console.get_docker_client") as mock_dc, \
             patch("agent.routers.console.get_console_method", return_value="ssh"), \
             patch("agent.routers.console.get_console_shell", return_value="/bin/sh"), \
             patch("agent.routers.console.get_console_credentials", return_value=("admin", "admin")):
            mock_dc.return_value.containers.get.return_value = mock_container
            method, shell, user, pw = _run(_get_console_config("test-csr"))
        assert method == "ssh"

    def test_returns_docker_exec_for_native(self):
        from agent.routers.console import _get_console_config

        mock_container = MagicMock()
        mock_container.labels = {"archetype.node_kind": "linux"}

        with patch("agent.routers.console.get_docker_client") as mock_dc, \
             patch("agent.routers.console.get_console_method", return_value="docker_exec"), \
             patch("agent.routers.console.get_console_shell", return_value="/bin/bash"), \
             patch("agent.routers.console.get_console_credentials", return_value=("root", "")):
            mock_dc.return_value.containers.get.return_value = mock_container
            method, shell, user, pw = _run(_get_console_config("test-linux"))
        assert method == "docker_exec"
        assert shell == "/bin/bash"


# ---------------------------------------------------------------------------
# _get_container_ip
# ---------------------------------------------------------------------------


class TestGetContainerIp:

    def test_first_ip_returned(self):
        from agent.routers.console import _get_container_ip

        mock_container = MagicMock()
        mock_container.attrs = {
            "NetworkSettings": {
                "Networks": {
                    "bridge": {"IPAddress": "172.17.0.2"},
                }
            }
        }
        with patch("agent.routers.console.get_docker_client") as mock_dc:
            mock_dc.return_value.containers.get.return_value = mock_container
            ip = _run(_get_container_ip("test-container"))
        assert ip == "172.17.0.2"

    def test_no_networks_returns_none(self):
        from agent.routers.console import _get_container_ip

        mock_container = MagicMock()
        mock_container.attrs = {"NetworkSettings": {"Networks": {}}}
        with patch("agent.routers.console.get_docker_client") as mock_dc:
            mock_dc.return_value.containers.get.return_value = mock_container
            assert _run(_get_container_ip("test-container")) is None

    def test_exception_returns_none(self):
        from agent.routers.console import _get_container_ip

        with patch("agent.routers.console.get_docker_client") as mock_dc:
            mock_dc.side_effect = RuntimeError("fail")
            assert _run(_get_container_ip("test-container")) is None


# ---------------------------------------------------------------------------
# _get_container_boot_logs
# ---------------------------------------------------------------------------


class TestGetContainerBootLogs:

    def test_returns_logs(self):
        from agent.routers.console import _get_container_boot_logs

        mock_container = MagicMock()
        mock_container.logs.return_value = b"Starting services...\nReady\n"
        with patch("agent.routers.console.get_docker_client") as mock_dc:
            mock_dc.return_value.containers.get.return_value = mock_container
            logs = _run(_get_container_boot_logs("test-container"))
        assert "Starting services" in logs

    def test_empty_returns_none(self):
        from agent.routers.console import _get_container_boot_logs

        mock_container = MagicMock()
        mock_container.logs.return_value = b"   \n"
        with patch("agent.routers.console.get_docker_client") as mock_dc:
            mock_dc.return_value.containers.get.return_value = mock_container
            assert _run(_get_container_boot_logs("test-container")) is None

    def test_exception_returns_none(self):
        from agent.routers.console import _get_container_boot_logs

        with patch("agent.routers.console.get_docker_client") as mock_dc:
            mock_dc.side_effect = RuntimeError("fail")
            assert _run(_get_container_boot_logs("test-container")) is None


# ---------------------------------------------------------------------------
# _check_container_exists
# ---------------------------------------------------------------------------


class TestCheckContainerExists:

    def test_true_when_exists(self):
        from agent.routers.console import _check_container_exists

        with patch("agent.routers.console.get_docker_client") as mock_dc:
            mock_dc.return_value.containers.get.return_value = MagicMock()
            assert _run(_check_container_exists("test")) is True

    def test_false_on_exception(self):
        from agent.routers.console import _check_container_exists

        with patch("agent.routers.console.get_docker_client") as mock_dc:
            mock_dc.return_value.containers.get.side_effect = Exception("not found")
            assert _run(_check_container_exists("test")) is False
