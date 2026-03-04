"""Tests for agent/network/transport.py — VXLAN endpoint IP fallback chain."""
from __future__ import annotations

import socket
from unittest.mock import MagicMock, patch

from agent.network import transport


def _reset_data_plane_ip() -> None:
    """Reset module-level state between tests."""
    transport._data_plane_ip = None


# ---------------------------------------------------------------------------
# set_data_plane_ip / get_data_plane_ip round-trip
# ---------------------------------------------------------------------------


def test_set_and_get_data_plane_ip() -> None:
    _reset_data_plane_ip()
    transport.set_data_plane_ip("10.0.0.5")
    assert transport.get_data_plane_ip() == "10.0.0.5"
    _reset_data_plane_ip()


def test_set_data_plane_ip_none_clears() -> None:
    _reset_data_plane_ip()
    transport.set_data_plane_ip("10.0.0.5")
    transport.set_data_plane_ip(None)
    assert transport.get_data_plane_ip() is None
    _reset_data_plane_ip()


def test_get_data_plane_ip_default_is_none() -> None:
    _reset_data_plane_ip()
    assert transport.get_data_plane_ip() is None


# ---------------------------------------------------------------------------
# get_vxlan_local_ip() — fallback chain
# ---------------------------------------------------------------------------


def test_vxlan_local_ip_uses_data_plane_ip_first() -> None:
    """Explicitly set data_plane_ip takes priority."""
    _reset_data_plane_ip()
    transport.set_data_plane_ip("192.168.1.100")

    result = transport.get_vxlan_local_ip()
    assert result == "192.168.1.100"
    _reset_data_plane_ip()


def test_vxlan_local_ip_falls_back_to_settings_local_ip(monkeypatch) -> None:
    """settings.local_ip is the secondary source."""
    _reset_data_plane_ip()

    # The import happens inside get_vxlan_local_ip (from agent.config import settings),
    # so we patch the actual settings object on agent.config
    import agent.config
    monkeypatch.setattr(agent.config.settings, "local_ip", "10.14.23.181")
    try:
        result = transport.get_vxlan_local_ip()
        assert result == "10.14.23.181"
    finally:
        _reset_data_plane_ip()


def test_vxlan_local_ip_falls_back_to_autodetect(monkeypatch) -> None:
    """Auto-detect is the tertiary source."""
    _reset_data_plane_ip()

    import agent.config
    monkeypatch.setattr(agent.config.settings, "local_ip", "")

    with patch.object(transport, "_detect_local_ip", return_value="172.16.0.1"):
        result = transport.get_vxlan_local_ip()
        assert result == "172.16.0.1"

    _reset_data_plane_ip()


# ---------------------------------------------------------------------------
# _detect_local_ip()
# ---------------------------------------------------------------------------


def test_detect_local_ip_success() -> None:
    """Successful socket probe returns detected IP."""
    mock_socket = MagicMock()
    mock_socket.getsockname.return_value = ("192.168.50.10", 0)

    with patch("agent.network.transport.socket.socket", return_value=mock_socket):
        result = transport._detect_local_ip()
        assert result == "192.168.50.10"

    mock_socket.connect.assert_called_once_with(("8.8.8.8", 80))
    mock_socket.close.assert_called_once()


def test_detect_local_ip_socket_failure() -> None:
    """Socket failure returns 127.0.0.1 fallback."""
    with patch(
        "agent.network.transport.socket.socket",
        side_effect=OSError("Network unreachable"),
    ):
        result = transport._detect_local_ip()
        assert result == "127.0.0.1"


def test_detect_local_ip_getsockname_failure() -> None:
    """getsockname failure returns 127.0.0.1 fallback."""
    mock_socket = MagicMock()
    mock_socket.getsockname.side_effect = OSError("not connected")

    with patch("agent.network.transport.socket.socket", return_value=mock_socket):
        result = transport._detect_local_ip()
        assert result == "127.0.0.1"
