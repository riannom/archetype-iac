"""CI-friendly tests for console WebSocket routing."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch

from agent.main import app


@pytest.fixture
def test_client():
    client = TestClient(app)
    yield client
    client.close()


def _libvirt_handler():
    async def _handler(ws, lab_id, node_name):
        await ws.send_text("libvirt")
        await ws.close()
    return _handler


def _docker_handler():
    async def _handler(ws, container_name, node_name, shell_cmd):
        await ws.send_text("docker")
        await ws.close()
    return _handler


def test_console_websocket_libvirt_requested(test_client):
    with patch("agent.main._console_websocket_libvirt", new_callable=AsyncMock) as mock_libvirt:
        mock_libvirt.side_effect = _libvirt_handler()
        with test_client.websocket_connect("/console/lab1/r1?provider_type=libvirt") as ws:
            msg = ws.receive_text()

    assert msg == "libvirt"
    mock_libvirt.assert_awaited_once()


def test_console_websocket_libvirt_requested_skips_docker(test_client):
    provider = MagicMock()
    provider.get_container_name.return_value = "archetype-lab1-r1"

    with patch("agent.main.get_provider", return_value=provider):
        with patch("agent.main._get_console_config", new_callable=AsyncMock) as mock_cfg:
            with patch("agent.main._console_websocket_libvirt", new_callable=AsyncMock) as mock_libvirt:
                mock_libvirt.side_effect = _libvirt_handler()
                with test_client.websocket_connect("/console/lab1/r1?provider_type=libvirt") as ws:
                    msg = ws.receive_text()

    assert msg == "libvirt"
    mock_libvirt.assert_awaited_once()
    mock_cfg.assert_not_awaited()


def test_console_websocket_docker_path(test_client):
    provider = MagicMock()
    provider.get_container_name.return_value = "archetype-lab1-r1"

    with patch("agent.main.get_provider", return_value=provider):
        with patch("agent.main._get_console_config", new_callable=AsyncMock) as mock_cfg:
            mock_cfg.return_value = ("docker_exec", "/bin/sh", None, None)
            with patch("agent.main._console_websocket_docker", new_callable=AsyncMock) as mock_docker:
                mock_docker.side_effect = _docker_handler()
                with test_client.websocket_connect("/console/lab1/r1") as ws:
                    msg = ws.receive_text()

    assert msg == "docker"
    mock_docker.assert_awaited_once()


def test_console_websocket_config_error(test_client):
    provider = MagicMock()
    provider.get_container_name.return_value = "archetype-lab1-r1"

    with patch("agent.main.get_provider", return_value=provider):
        with patch("agent.main._get_console_config", new_callable=AsyncMock) as mock_cfg:
            mock_cfg.side_effect = RuntimeError("config failed")
            with test_client.websocket_connect("/console/lab1/r1") as ws:
                msg = ws.receive_text()

    assert "Error" in msg


def test_console_websocket_fallback_to_libvirt(test_client):
    with patch("agent.main.get_provider", return_value=None):
        with patch("agent.main._console_websocket_libvirt", new_callable=AsyncMock) as mock_libvirt:
            mock_libvirt.side_effect = _libvirt_handler()
            with test_client.websocket_connect("/console/lab1/r1") as ws:
                msg = ws.receive_text()

    assert msg == "libvirt"
    mock_libvirt.assert_awaited_once()


def test_console_websocket_ssh_path(test_client):
    provider = MagicMock()
    provider.get_container_name.return_value = "archetype-lab1-r1"

    with patch("agent.main.get_provider", return_value=provider):
        with patch("agent.main._get_console_config", new_callable=AsyncMock) as mock_cfg:
            mock_cfg.return_value = ("ssh", "/bin/sh", "admin", "admin")
            with patch("agent.main._console_websocket_ssh", new_callable=AsyncMock) as mock_ssh:
                mock_ssh.side_effect = _docker_handler()
                with test_client.websocket_connect("/console/lab1/r1") as ws:
                    msg = ws.receive_text()

    assert msg == "docker"
    mock_ssh.assert_awaited_once()


def test_console_websocket_libvirt_error(test_client):
    with patch("agent.main.get_provider", return_value=None):
        with patch("agent.main._console_websocket_libvirt", new_callable=AsyncMock) as mock_libvirt:
            mock_libvirt.side_effect = RuntimeError("libvirt failed")
            with test_client.websocket_connect("/console/lab1/r1") as ws:
                msg = ws.receive_text()

    assert "Error" in msg


def test_console_websocket_libvirt_fallback_when_docker_missing(test_client):
    provider = MagicMock()
    provider.get_container_name.return_value = "archetype-lab1-r1"

    with patch("agent.main.get_provider", return_value=provider):
        with patch("agent.main.docker.from_env", side_effect=Exception("docker missing")):
            with patch("agent.main._console_websocket_libvirt", new_callable=AsyncMock) as mock_libvirt:
                mock_libvirt.side_effect = _libvirt_handler()
                with test_client.websocket_connect("/console/lab1/r1") as ws:
                    msg = ws.receive_text()

    assert msg == "libvirt"
    mock_libvirt.assert_awaited_once()


def test_console_websocket_ssh_error(test_client):
    provider = MagicMock()
    provider.get_container_name.return_value = "archetype-lab1-r1"

    with patch("agent.main.get_provider", return_value=provider):
        with patch("agent.main._get_console_config", new_callable=AsyncMock) as mock_cfg:
            mock_cfg.return_value = ("ssh", "/bin/sh", "admin", "admin")
            with patch("agent.main._console_websocket_ssh", new_callable=AsyncMock) as mock_ssh:
                mock_ssh.side_effect = RuntimeError("ssh failed")
                with test_client.websocket_connect("/console/lab1/r1") as ws:
                    msg = ws.receive_text()

    assert "Error" in msg
