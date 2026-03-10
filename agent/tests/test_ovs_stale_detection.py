"""Tests for OVS stale-port detection fallbacks."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.network.ovs import OVSPort, OVSNetworkManager


def _port() -> OVSPort:
    return OVSPort(
        port_name="vh-abcd",
        container_name="lab-r1",
        interface_name="eth1",
        vlan_tag=3001,
        lab_id="lab-1",
    )


@pytest.mark.asyncio
async def test_is_port_stale_uses_host_peer_fallback_when_pid_missing():
    manager = OVSNetworkManager.__new__(OVSNetworkManager)
    manager._ip_link_exists = AsyncMock(return_value=True)
    manager._docker = MagicMock()

    mock_host_veth_peer_missing = AsyncMock(return_value=True)

    with (
        patch("agent.network.ovs_provision.get_container_pid", AsyncMock(return_value=None)),
        patch("agent.network.ovs_provision.host_veth_peer_missing", mock_host_veth_peer_missing),
    ):
        assert await manager.is_port_stale(_port()) is True
    mock_host_veth_peer_missing.assert_awaited_once_with("vh-abcd")


@pytest.mark.asyncio
async def test_is_port_stale_pid_missing_not_stale_when_peer_present():
    manager = OVSNetworkManager.__new__(OVSNetworkManager)
    manager._ip_link_exists = AsyncMock(return_value=True)
    manager._docker = MagicMock()

    mock_host_veth_peer_missing = AsyncMock(return_value=False)

    with (
        patch("agent.network.ovs_provision.get_container_pid", AsyncMock(return_value=None)),
        patch("agent.network.ovs_provision.host_veth_peer_missing", mock_host_veth_peer_missing),
    ):
        assert await manager.is_port_stale(_port()) is False
