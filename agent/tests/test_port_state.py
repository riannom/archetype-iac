"""Tests for agent port-state endpoint and same-host declare-state.

Covers:
- GET /labs/{lab_id}/port-state: returns OVS port info for lab
- POST /ports/declare-state: converges same-host port VLAN tags
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─── Helpers ──────────────────────────────────────────────────────────────


def _make_plugin_mock(ports_data=None):
    """Create a mock DockerOVSPlugin with get_lab_ports."""
    plugin = MagicMock()
    plugin.get_lab_ports = AsyncMock(return_value=ports_data or [])
    plugin._ovs_vsctl = AsyncMock(return_value=(0, "100", ""))
    return plugin


# ─── Port state tests ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_port_state_returns_correct_data():
    """Port state endpoint returns node/interface/vlan data."""

    ports_data = [
        {
            "port_name": "vh-abc123",
            "bridge_name": "arch-ovs",
            "container": "archetype-lab1-r1",
            "interface": "eth1",
            "vlan_tag": 100,
            "rx_bytes": 0,
            "tx_bytes": 0,
        },
        {
            "port_name": "vh-def456",
            "bridge_name": "arch-ovs",
            "container": "archetype-lab1-r2",
            "interface": "eth1",
            "vlan_tag": 100,
            "rx_bytes": 0,
            "tx_bytes": 0,
        },
    ]

    plugin = _make_plugin_mock(ports_data)

    with patch("agent.main.settings") as mock_settings, \
         patch("agent.main._get_docker_ovs_plugin", return_value=plugin):
        mock_settings.enable_ovs_plugin = True

        from agent.main import get_lab_port_state
        result = await get_lab_port_state("lab1")

    assert len(result.ports) == 2
    assert result.ports[0].node_name == "r1"
    assert result.ports[0].interface_name == "eth1"
    assert result.ports[0].ovs_port_name == "vh-abc123"
    assert result.ports[0].vlan_tag == 100


@pytest.mark.asyncio
async def test_port_state_handles_empty():
    """Port state with no containers returns empty list."""
    plugin = _make_plugin_mock([])

    with patch("agent.main.settings") as mock_settings, \
         patch("agent.main._get_docker_ovs_plugin", return_value=plugin):
        mock_settings.enable_ovs_plugin = True

        from agent.main import get_lab_port_state
        result = await get_lab_port_state("empty-lab")

    assert result.ports == []


@pytest.mark.asyncio
async def test_port_state_disabled_plugin():
    """Port state returns empty when OVS plugin disabled."""
    with patch("agent.main.settings") as mock_settings:
        mock_settings.enable_ovs_plugin = False

        from agent.main import get_lab_port_state
        result = await get_lab_port_state("lab1")

    assert result.ports == []


# ─── Same-host declare-state tests ───────────────────────────────────────


@pytest.mark.asyncio
async def test_declare_port_converged():
    """Ports already matching declared VLAN return converged."""
    from agent.schemas import DeclarePortStateRequest, DeclaredPortPairing

    request = DeclarePortStateRequest(pairings=[
        DeclaredPortPairing(
            link_name="R1:eth1-R2:eth1",
            lab_id="lab1",
            port_a="vh-abc123",
            port_b="vh-def456",
            vlan_tag=100,
        ),
    ])

    plugin = _make_plugin_mock()
    # Both ports return tag=100
    plugin._ovs_vsctl = AsyncMock(return_value=(0, "100", ""))

    with patch("agent.main.settings") as mock_settings, \
         patch("agent.main._get_docker_ovs_plugin", return_value=plugin):
        mock_settings.enable_ovs_plugin = True

        from agent.main import declare_port_state
        result = await declare_port_state(request)

    assert len(result.results) == 1
    assert result.results[0].status == "converged"
    assert result.results[0].actual_vlan == 100


@pytest.mark.asyncio
async def test_declare_port_updates_drifted():
    """Port with wrong VLAN gets updated."""
    from agent.schemas import DeclarePortStateRequest, DeclaredPortPairing

    request = DeclarePortStateRequest(pairings=[
        DeclaredPortPairing(
            link_name="R1:eth1-R2:eth1",
            lab_id="lab1",
            port_a="vh-abc123",
            port_b="vh-def456",
            vlan_tag=200,
        ),
    ])

    plugin = _make_plugin_mock()
    call_count = 0

    async def _mock_ovs_vsctl(*args):
        nonlocal call_count
        call_count += 1
        if args[0] == "get":
            # Return wrong VLAN for first port, correct for second
            if args[2] == "vh-abc123":
                return (0, "100", "")  # Wrong
            return (0, "200", "")  # Correct
        return (0, "", "")  # set command

    plugin._ovs_vsctl = AsyncMock(side_effect=_mock_ovs_vsctl)

    with patch("agent.main.settings") as mock_settings, \
         patch("agent.main._get_docker_ovs_plugin", return_value=plugin):
        mock_settings.enable_ovs_plugin = True

        from agent.main import declare_port_state
        result = await declare_port_state(request)

    assert len(result.results) == 1
    assert result.results[0].status == "updated"


@pytest.mark.asyncio
async def test_declare_port_handles_error():
    """OVS failure returns error status."""
    from agent.schemas import DeclarePortStateRequest, DeclaredPortPairing

    request = DeclarePortStateRequest(pairings=[
        DeclaredPortPairing(
            link_name="R1:eth1-R2:eth1",
            lab_id="lab1",
            port_a="vh-abc123",
            port_b="vh-def456",
            vlan_tag=100,
        ),
    ])

    plugin = _make_plugin_mock()
    plugin._ovs_vsctl = AsyncMock(side_effect=Exception("OVS down"))

    with patch("agent.main.settings") as mock_settings, \
         patch("agent.main._get_docker_ovs_plugin", return_value=plugin):
        mock_settings.enable_ovs_plugin = True

        from agent.main import declare_port_state
        result = await declare_port_state(request)

    assert len(result.results) == 1
    assert result.results[0].status == "error"
    assert "OVS down" in result.results[0].error


@pytest.mark.asyncio
async def test_declare_port_disabled_plugin():
    """Declare-state returns empty when OVS plugin disabled."""
    from agent.schemas import DeclarePortStateRequest, DeclaredPortPairing

    request = DeclarePortStateRequest(pairings=[
        DeclaredPortPairing(
            link_name="R1:eth1-R2:eth1",
            lab_id="lab1",
            port_a="vh-abc123",
            port_b="vh-def456",
            vlan_tag=100,
        ),
    ])

    with patch("agent.main.settings") as mock_settings:
        mock_settings.enable_ovs_plugin = False

        from agent.main import declare_port_state
        result = await declare_port_state(request)

    assert result.results == []
