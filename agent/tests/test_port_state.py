"""Tests for agent port-state endpoint and same-host declare-state.

Covers:
- GET /labs/{lab_id}/port-state: returns OVS port info for lab
- POST /ports/declare-state: converges same-host port VLAN tags
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from agent.network.carrier_monitor import MonitoredPort


# ─── Helpers ──────────────────────────────────────────────────────────────


def _make_plugin_mock(ports_data=None):
    """Create a mock DockerOVSPlugin with get_lab_ports."""
    plugin = MagicMock()
    plugin.get_lab_ports = AsyncMock(return_value=ports_data or [])
    plugin._ovs_vsctl = AsyncMock(return_value=(0, "100", ""))
    plugin.set_endpoint_vlan_by_host_veth = AsyncMock(return_value=True)
    return plugin


# ─── Port state tests ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_port_state_returns_correct_data():
    """Port state endpoint returns node/interface/vlan data."""
    class _FakeProc:
        def __init__(self, stdout: bytes, returncode: int = 0, stderr: bytes = b"") -> None:
            self.returncode = returncode
            self._stdout = stdout
            self._stderr = stderr

        async def communicate(self):
            return self._stdout, self._stderr

    async def _fake_create_subprocess_exec(*cmd, **_kwargs):
        if cmd[:2] == ("ovs-vsctl", "list-ports"):
            return _FakeProc(b"vh-abc123\nvh-def456\n")
        if cmd[:4] == ("ovs-vsctl", "get", "interface", "vh-abc123"):
            return _FakeProc(b"111\n")
        if cmd[:4] == ("ovs-vsctl", "get", "port", "vh-abc123"):
            return _FakeProc(b"100\n")
        if cmd[:4] == ("ovs-vsctl", "get", "interface", "vh-def456"):
            return _FakeProc(b"222\n")
        if cmd[:4] == ("ovs-vsctl", "get", "port", "vh-def456"):
            return _FakeProc(b"100\n")
        return _FakeProc(b"", returncode=1, stderr=b"unexpected command")

    container_a = MagicMock()
    container_a.name = "archetype-11111111-2222-3333-4444-555555555555-r1"
    container_a.labels = {"archetype.node_name": "r1"}
    container_a.exec_run.return_value = (0, b"eth0:10\neth1:111\n")

    container_b = MagicMock()
    container_b.name = "archetype-11111111-2222-3333-4444-555555555555-r2"
    container_b.labels = {"archetype.node_name": "r2"}
    container_b.exec_run.return_value = (0, b"eth0:20\neth1:222\n")

    docker_client = MagicMock()
    docker_client.containers.list.return_value = [container_a, container_b]

    def _get_provider(name: str):
        if name == "docker":
            return MagicMock()
        return None

    with patch("agent.routers.overlay.get_provider", side_effect=_get_provider):
        with patch("docker.from_env", return_value=docker_client):
            with patch("agent.routers.overlay.asyncio.create_subprocess_exec", side_effect=_fake_create_subprocess_exec):
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
    docker_client = MagicMock()
    docker_client.containers.list.return_value = []

    def _get_provider(name: str):
        if name == "docker":
            return MagicMock()
        return None

    with patch("agent.routers.overlay.get_provider", side_effect=_get_provider):
        with patch("docker.from_env", return_value=docker_client):
            from agent.main import get_lab_port_state
            result = await get_lab_port_state("empty-lab")

    assert result.ports == []


@pytest.mark.asyncio
async def test_port_state_includes_libvirt_vm_ports():
    """Port state includes libvirt VM ports so stale VM mappings can be refreshed."""
    class _FakeProc:
        def __init__(self, stdout: bytes, returncode: int = 0, stderr: bytes = b"") -> None:
            self.returncode = returncode
            self._stdout = stdout
            self._stderr = stderr

        async def communicate(self):
            return self._stdout, self._stderr

    async def _fake_create_subprocess_exec(*cmd, **_kwargs):
        if cmd[:2] == ("ovs-vsctl", "list-ports"):
            return _FakeProc(b"vnet643\nvnet668\n")
        if cmd[:4] == ("ovs-vsctl", "get", "port", "vnet643"):
            return _FakeProc(b"2050\n")
        if cmd[:4] == ("ovs-vsctl", "get", "port", "vnet668"):
            return _FakeProc(b"2052\n")
        return _FakeProc(b"", returncode=1, stderr=b"unexpected command")

    libvirt_provider = MagicMock()
    libvirt_provider.refresh_vm_monitored_ports = AsyncMock()
    libvirt_provider.get_vm_monitored_ports.return_value = {
        "vnet643": MonitoredPort(
            port_name="vnet643",
            container_name="arch-lab-ceos_6",
            interface_name="eth1",
            lab_id="lab1",
            node_name="cat9000v_uadp_8",
        ),
        "vnet668": MonitoredPort(
            port_name="vnet668",
            container_name="arch-lab-n9kv",
            interface_name="eth1",
            lab_id="lab1",
            node_name="cisco_n9kv_4",
        ),
    }

    def _get_provider(name: str):
        if name == "docker":
            return None
        if name == "libvirt":
            return libvirt_provider
        return None

    with patch("agent.routers.overlay.get_provider", side_effect=_get_provider):
        with patch("agent.routers.overlay.asyncio.create_subprocess_exec", side_effect=_fake_create_subprocess_exec):
            from agent.main import get_lab_port_state
            result = await get_lab_port_state("lab1")

    assert [(p.node_name, p.interface_name, p.ovs_port_name, p.vlan_tag) for p in result.ports] == [
        ("cat9000v_uadp_8", "eth1", "vnet643", 2050),
        ("cisco_n9kv_4", "eth1", "vnet668", 2052),
    ]
    libvirt_provider.refresh_vm_monitored_ports.assert_awaited_once()


@pytest.mark.asyncio
async def test_port_state_disabled_plugin():
    """Port state returns empty when no Docker provider is available."""
    with patch("agent.routers.overlay.get_provider", return_value=None):
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

    with patch("agent.routers.overlay.settings") as mock_settings, \
         patch("agent.routers.overlay._get_docker_ovs_plugin", return_value=plugin):
        mock_settings.enable_ovs_plugin = True

        from agent.main import declare_port_state
        result = await declare_port_state(request)

    assert len(result.results) == 1
    assert result.results[0].status == "converged"
    assert result.results[0].actual_vlan == 100
    assert plugin.set_endpoint_vlan_by_host_veth.await_count == 2


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

    with patch("agent.routers.overlay.settings") as mock_settings, \
         patch("agent.routers.overlay._get_docker_ovs_plugin", return_value=plugin):
        mock_settings.enable_ovs_plugin = True

        from agent.main import declare_port_state
        result = await declare_port_state(request)

    assert len(result.results) == 1
    assert result.results[0].status == "updated"
    plugin.set_endpoint_vlan_by_host_veth.assert_has_awaits(
        [
            call("lab1", "vh-abc123", 200),
            call("lab1", "vh-def456", 200),
        ],
        any_order=False,
    )


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

    with patch("agent.routers.overlay.settings") as mock_settings, \
         patch("agent.routers.overlay._get_docker_ovs_plugin", return_value=plugin):
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

    with patch("agent.routers.overlay.settings") as mock_settings:
        mock_settings.enable_ovs_plugin = False

        from agent.main import declare_port_state
        result = await declare_port_state(request)

    assert result.results == []
