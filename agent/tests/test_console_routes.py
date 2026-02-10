"""CI-friendly tests for console WebSocket routing."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from agent.main import console_websocket


def _run(coro):
    return asyncio.run(coro)


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent_texts: list[str] = []
        self.accepted = False
        self.closed = False
        self.close_code: int | None = None

    async def accept(self) -> None:
        self.accepted = True

    async def send_text(self, data: str) -> None:
        self.sent_texts.append(data)

    async def close(self, code: int = 1000) -> None:
        self.closed = True
        self.close_code = code


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


def _ssh_handler():
    async def _handler(ws, container_name, node_name, username, password):
        await ws.send_text("docker")
        await ws.close()
        return True
    return _handler


def test_console_websocket_libvirt_requested():
    with patch("agent.main._console_websocket_libvirt", new_callable=AsyncMock) as mock_libvirt:
        mock_libvirt.side_effect = _libvirt_handler()
        ws = FakeWebSocket()
        _run(console_websocket(ws, "lab1", "r1", provider_type="libvirt"))
        msg = ws.sent_texts[-1]

    assert msg == "libvirt"
    mock_libvirt.assert_awaited_once()


def test_console_websocket_libvirt_requested_skips_docker():
    provider = MagicMock()
    provider.get_container_name.return_value = "archetype-lab1-r1"

    with patch("agent.main.get_provider", return_value=provider):
        with patch("agent.main._get_console_config", new_callable=AsyncMock) as mock_cfg:
            with patch("agent.main._console_websocket_libvirt", new_callable=AsyncMock) as mock_libvirt:
                mock_libvirt.side_effect = _libvirt_handler()
                ws = FakeWebSocket()
                _run(console_websocket(ws, "lab1", "r1", provider_type="libvirt"))
                msg = ws.sent_texts[-1]

    assert msg == "libvirt"
    mock_libvirt.assert_awaited_once()
    mock_cfg.assert_not_awaited()


def test_console_websocket_docker_path():
    provider = MagicMock()
    provider.get_container_name.return_value = "archetype-lab1-r1"

    with patch("agent.main.get_provider", return_value=provider):
        with patch("agent.main._check_container_exists", new_callable=AsyncMock, return_value=True):
            with patch("agent.main._get_console_config", new_callable=AsyncMock) as mock_cfg:
                mock_cfg.return_value = ("docker_exec", "/bin/sh", None, None)
                with patch("agent.main._console_websocket_docker", new_callable=AsyncMock) as mock_docker:
                    mock_docker.side_effect = _docker_handler()
                    ws = FakeWebSocket()
                    _run(console_websocket(ws, "lab1", "r1"))
                    msg = ws.sent_texts[-1]

    assert msg == "docker"
    mock_docker.assert_awaited_once()


def test_console_websocket_config_error():
    provider = MagicMock()
    provider.get_container_name.return_value = "archetype-lab1-r1"

    with patch("agent.main.get_provider", return_value=provider):
        with patch("agent.main._check_container_exists", new_callable=AsyncMock, return_value=True):
            with patch("agent.main._get_console_config", new_callable=AsyncMock) as mock_cfg:
                mock_cfg.side_effect = RuntimeError("config failed")
                ws = FakeWebSocket()
                _run(console_websocket(ws, "lab1", "r1"))
                msg = ws.sent_texts[-1]

    assert "Error" in msg


def test_console_websocket_fallback_to_libvirt():
    def _provider_by_name(name):
        if name == "libvirt":
            return MagicMock()
        return None

    with patch("agent.main.get_provider", side_effect=_provider_by_name):
        with patch("agent.main._console_websocket_libvirt", new_callable=AsyncMock) as mock_libvirt:
            mock_libvirt.side_effect = _libvirt_handler()
            ws = FakeWebSocket()
            _run(console_websocket(ws, "lab1", "r1"))
            msg = ws.sent_texts[-1]

    assert msg == "libvirt"
    mock_libvirt.assert_awaited_once()


def test_console_websocket_ssh_path():
    provider = MagicMock()
    provider.get_container_name.return_value = "archetype-lab1-r1"

    with patch("agent.main.get_provider", return_value=provider):
        with patch("agent.main._check_container_exists", new_callable=AsyncMock, return_value=True):
            with patch("agent.main._get_console_config", new_callable=AsyncMock) as mock_cfg:
                mock_cfg.return_value = ("ssh", "/bin/sh", "admin", "admin")
                with patch("agent.main._console_websocket_ssh", new_callable=AsyncMock) as mock_ssh:
                    mock_ssh.side_effect = _ssh_handler()
                    ws = FakeWebSocket()
                    _run(console_websocket(ws, "lab1", "r1"))
                    msg = ws.sent_texts[-1]

    assert msg == "docker"
    mock_ssh.assert_awaited_once()


def test_console_websocket_libvirt_error():
    with patch("agent.main.get_provider", return_value=None):
        with patch("agent.main._console_websocket_libvirt", new_callable=AsyncMock) as mock_libvirt:
            mock_libvirt.side_effect = RuntimeError("libvirt failed")
            ws = FakeWebSocket()
            _run(console_websocket(ws, "lab1", "r1"))
            msg = ws.sent_texts[-1]

    assert "Error" in msg


def test_console_websocket_libvirt_fallback_when_docker_missing():
    provider = MagicMock()
    provider.get_container_name.return_value = "archetype-lab1-r1"

    with patch("agent.main.get_provider", return_value=provider):
        with patch("agent.main._check_container_exists", new_callable=AsyncMock, return_value=False):
            with patch("agent.main._console_websocket_libvirt", new_callable=AsyncMock) as mock_libvirt:
                mock_libvirt.side_effect = _libvirt_handler()
                ws = FakeWebSocket()
                _run(console_websocket(ws, "lab1", "r1"))
                msg = ws.sent_texts[-1]

    assert msg == "libvirt"
    mock_libvirt.assert_awaited_once()


def test_console_websocket_ssh_error():
    provider = MagicMock()
    provider.get_container_name.return_value = "archetype-lab1-r1"

    with patch("agent.main.get_provider", return_value=provider):
        with patch("agent.main._check_container_exists", new_callable=AsyncMock, return_value=True):
            with patch("agent.main._get_console_config", new_callable=AsyncMock) as mock_cfg:
                mock_cfg.return_value = ("ssh", "/bin/sh", "admin", "admin")
                with patch("agent.main._console_websocket_ssh", new_callable=AsyncMock) as mock_ssh:
                    mock_ssh.side_effect = RuntimeError("ssh failed")
                    ws = FakeWebSocket()
                    _run(console_websocket(ws, "lab1", "r1"))
                    msg = ws.sent_texts[-1]

    assert "Error" in msg
