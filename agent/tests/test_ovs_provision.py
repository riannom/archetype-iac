"""Tests for agent/network/ovs_provision.py — OVS port provisioning helpers.

Covers:
- Port name generation and length limits
- Container PID lookup (running, stopped, not found)
- Interface provisioning orchestration (veth creation, OVS attachment, namespace move)
- Failure recovery during provisioning (cleanup on partial failure)
- Port deletion and resource release
- Stale port detection (namespace check, sysfs fallback)
- Stale port cleanup
- Container restart handling (reprovisioning + link reconnection)
- Discovery of existing state after agent restart
"""
from __future__ import annotations

import json as _json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.network.ovs import OVSLink, OVSNetworkManager, OVSPort, VlanAllocator


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_singleton():
    """Reset OVSNetworkManager singleton between tests."""
    OVSNetworkManager._instance = None
    yield
    OVSNetworkManager._instance = None


def _make_manager(tmp_path: Path) -> OVSNetworkManager:
    """Create an OVSNetworkManager with mocked async primitives."""
    mgr = OVSNetworkManager()
    mgr._initialized = True
    mgr._bridge_name = "test-ovs"
    mgr._ports = {}
    mgr._links = {}
    mgr._vlan_allocator = VlanAllocator(
        start=100, end=4000, persistence_path=tmp_path / "v.json",
    )
    mgr._docker = MagicMock()

    # Default mocks: all commands succeed
    mgr._ovs_vsctl = AsyncMock(return_value=(0, "", ""))
    mgr._run_cmd = AsyncMock(return_value=(0, "", ""))
    mgr._ip_link_exists = AsyncMock(return_value=False)
    return mgr


def _make_port(
    port_name: str = "vhnodie1ab12",
    container_name: str = "archetype-lab1-r1",
    interface_name: str = "eth1",
    vlan_tag: int = 2001,
    lab_id: str = "lab1",
) -> OVSPort:
    return OVSPort(
        port_name=port_name,
        container_name=container_name,
        interface_name=interface_name,
        vlan_tag=vlan_tag,
        lab_id=lab_id,
    )


# ---------------------------------------------------------------------------
# TestGeneratePortName
# ---------------------------------------------------------------------------

class TestGeneratePortName:
    """Tests for generate_port_name()."""

    def test_max_length_15_chars(self) -> None:
        from agent.network.ovs_provision import generate_port_name
        name = generate_port_name("archetype-lab123-router1", "eth1")
        assert len(name) <= 15

    def test_max_length_with_long_interface(self) -> None:
        from agent.network.ovs_provision import generate_port_name
        name = generate_port_name("archetype-lab1-r1", "Ethernet10")
        assert len(name) <= 15

    def test_unique_per_call(self) -> None:
        """Random suffix makes each call produce a different name."""
        from agent.network.ovs_provision import generate_port_name
        names = {generate_port_name("archetype-lab1-r1", "eth1") for _ in range(20)}
        # With 4 hex chars of randomness, 20 calls should all be unique
        assert len(names) == 20

    def test_contains_node_suffix(self) -> None:
        """Port name incorporates part of the container name."""
        from agent.network.ovs_provision import generate_port_name
        name = generate_port_name("archetype-lab1-router1", "eth1")
        # Name starts with "vh" prefix
        assert name.startswith("vh")


# ---------------------------------------------------------------------------
# TestGetContainerPid
# ---------------------------------------------------------------------------

class TestGetContainerPid:
    """Tests for get_container_pid()."""

    @pytest.mark.asyncio
    async def test_returns_pid_for_running_container(self) -> None:
        from agent.network.ovs_provision import get_container_pid

        container = MagicMock()
        container.status = "running"
        container.attrs = {"State": {"Pid": 12345}}

        docker_client = MagicMock()
        docker_client.containers.get.return_value = container

        pid = await get_container_pid(docker_client, "my-container")
        assert pid == 12345

    @pytest.mark.asyncio
    async def test_returns_none_for_stopped_container(self) -> None:
        from agent.network.ovs_provision import get_container_pid

        container = MagicMock()
        container.status = "exited"
        container.attrs = {"State": {"Pid": 0}}

        docker_client = MagicMock()
        docker_client.containers.get.return_value = container

        pid = await get_container_pid(docker_client, "my-container")
        assert pid is None

    @pytest.mark.asyncio
    async def test_returns_none_when_container_not_found(self) -> None:
        from agent.network.ovs_provision import get_container_pid
        from docker.errors import NotFound

        docker_client = MagicMock()
        docker_client.containers.get.side_effect = NotFound("gone")

        pid = await get_container_pid(docker_client, "ghost")
        assert pid is None

    @pytest.mark.asyncio
    async def test_returns_none_on_zero_pid(self) -> None:
        from agent.network.ovs_provision import get_container_pid

        container = MagicMock()
        container.status = "running"
        container.attrs = {"State": {"Pid": 0}}

        docker_client = MagicMock()
        docker_client.containers.get.return_value = container

        pid = await get_container_pid(docker_client, "my-container")
        assert pid is None


# ---------------------------------------------------------------------------
# TestProvisionInterface
# ---------------------------------------------------------------------------

class TestProvisionInterface:
    """Tests for provision_interface() — the standalone function."""

    @pytest.mark.asyncio
    async def test_creates_veth_adds_to_ovs_and_tracks(self, tmp_path: Path) -> None:
        """Full happy path: veth creation, OVS attachment, namespace move, tracking."""
        from agent.network.ovs_provision import provision_interface

        mgr = _make_manager(tmp_path)

        # Mock get_container_pid (the standalone function called inside provision_interface)
        with patch("agent.network.ovs_provision.get_container_pid", new_callable=AsyncMock, return_value=42):
            vlan = await provision_interface(mgr, "archetype-lab1-r1", "eth1", "lab1")

        assert isinstance(vlan, int)
        assert vlan >= 100

        key = "archetype-lab1-r1:eth1"
        assert key in mgr._ports
        assert mgr._ports[key].vlan_tag == vlan
        assert mgr._ports[key].lab_id == "lab1"

    @pytest.mark.asyncio
    async def test_returns_existing_vlan_if_already_provisioned(self, tmp_path: Path) -> None:
        """Calling provision_interface twice returns the same VLAN without re-creating."""
        from agent.network.ovs_provision import provision_interface

        mgr = _make_manager(tmp_path)

        with patch("agent.network.ovs_provision.get_container_pid", new_callable=AsyncMock, return_value=42):
            v1 = await provision_interface(mgr, "archetype-lab1-r1", "eth1", "lab1")
            # Reset call counts to check no new commands are issued
            mgr._run_cmd.reset_mock()
            mgr._ovs_vsctl.reset_mock()

            v2 = await provision_interface(mgr, "archetype-lab1-r1", "eth1", "lab1")

        assert v1 == v2
        # No subprocess calls on the second invocation
        mgr._run_cmd.assert_not_awaited()
        mgr._ovs_vsctl.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_raises_when_container_not_running(self, tmp_path: Path) -> None:
        from agent.network.ovs_provision import provision_interface

        mgr = _make_manager(tmp_path)

        with patch("agent.network.ovs_provision.get_container_pid", new_callable=AsyncMock, return_value=None):
            with pytest.raises(RuntimeError, match="not running"):
                await provision_interface(mgr, "archetype-lab1-r1", "eth1", "lab1")

    @pytest.mark.asyncio
    async def test_cleans_existing_veth_before_creating(self, tmp_path: Path) -> None:
        """If port_name already exists as a link, it gets deleted first."""
        from agent.network.ovs_provision import provision_interface

        mgr = _make_manager(tmp_path)
        mgr._ip_link_exists = AsyncMock(return_value=True)

        with patch("agent.network.ovs_provision.get_container_pid", new_callable=AsyncMock, return_value=42):
            await provision_interface(mgr, "archetype-lab1-r1", "eth1", "lab1")

        # Should have called ip link delete for the pre-existing interface
        delete_calls = [
            c for c in mgr._run_cmd.call_args_list
            if c.args[0][:3] == ["ip", "link", "delete"]
        ]
        assert len(delete_calls) >= 1

    @pytest.mark.asyncio
    async def test_ovs_add_port_failure_releases_vlan(self, tmp_path: Path) -> None:
        """When OVS add-port fails, VLAN allocation is rolled back."""
        from agent.network.ovs_provision import provision_interface

        mgr = _make_manager(tmp_path)

        async def _selective_fail(*args):
            if "add-port" in args:
                return (1, "", "OVS add-port failed")
            return (0, "", "")

        mgr._ovs_vsctl = AsyncMock(side_effect=_selective_fail)

        with patch("agent.network.ovs_provision.get_container_pid", new_callable=AsyncMock, return_value=42):
            with pytest.raises(RuntimeError, match="Failed to provision interface"):
                await provision_interface(mgr, "archetype-lab1-r1", "eth1", "lab1")

        # VLAN allocation must have been cleaned up
        assert mgr._vlan_allocator.get_vlan("archetype-lab1-r1:eth1") is None

    @pytest.mark.asyncio
    async def test_namespace_move_failure_cleans_ovs_and_veth(self, tmp_path: Path) -> None:
        """If moving veth to container namespace fails, both OVS port and veth are cleaned."""
        from agent.network.ovs_provision import provision_interface

        mgr = _make_manager(tmp_path)
        call_count = 0

        async def _fail_netns_move(cmd):
            nonlocal call_count
            call_count += 1
            # Fail the "ip link set ... netns ..." command
            if len(cmd) >= 4 and cmd[0] == "ip" and "netns" in cmd:
                return (1, "", "netns move failed")
            return (0, "", "")

        mgr._run_cmd = AsyncMock(side_effect=_fail_netns_move)

        with patch("agent.network.ovs_provision.get_container_pid", new_callable=AsyncMock, return_value=42):
            with pytest.raises(RuntimeError, match="Failed to provision interface"):
                await provision_interface(mgr, "archetype-lab1-r1", "eth1", "lab1")

        assert mgr._vlan_allocator.get_vlan("archetype-lab1-r1:eth1") is None
        assert "archetype-lab1-r1:eth1" not in mgr._ports

    @pytest.mark.asyncio
    async def test_triggers_initialize_if_not_initialized(self, tmp_path: Path) -> None:
        """If manager is not initialized, provision_interface calls initialize()."""
        from agent.network.ovs_provision import provision_interface

        mgr = _make_manager(tmp_path)
        mgr._initialized = False
        mgr.initialize = AsyncMock()

        with patch("agent.network.ovs_provision.get_container_pid", new_callable=AsyncMock, return_value=42):
            await provision_interface(mgr, "archetype-lab1-r1", "eth1", "lab1")

        mgr.initialize.assert_awaited_once()


# ---------------------------------------------------------------------------
# TestDeletePort
# ---------------------------------------------------------------------------

class TestDeletePort:
    """Tests for delete_port()."""

    @pytest.mark.asyncio
    async def test_deletes_ovs_port_and_veth(self, tmp_path: Path) -> None:
        from agent.network.ovs_provision import delete_port

        mgr = _make_manager(tmp_path)
        port = _make_port()
        key = port.key
        mgr._ports[key] = port
        mgr._vlan_allocator._allocated[key] = port.vlan_tag

        result = await delete_port(mgr, port.container_name, port.interface_name)
        assert result is True
        assert key not in mgr._ports
        assert mgr._vlan_allocator.get_vlan(key) is None

    @pytest.mark.asyncio
    async def test_returns_false_if_port_not_found(self, tmp_path: Path) -> None:
        from agent.network.ovs_provision import delete_port

        mgr = _make_manager(tmp_path)
        result = await delete_port(mgr, "no-container", "eth99")
        assert result is False

    @pytest.mark.asyncio
    async def test_removes_associated_links(self, tmp_path: Path) -> None:
        from agent.network.ovs_provision import delete_port

        mgr = _make_manager(tmp_path)
        port_a = _make_port(port_name="vhA", container_name="c-a", interface_name="eth1")
        port_b = _make_port(port_name="vhB", container_name="c-b", interface_name="eth1")
        mgr._ports[port_a.key] = port_a
        mgr._ports[port_b.key] = port_b
        mgr._vlan_allocator._allocated[port_a.key] = port_a.vlan_tag

        link = OVSLink(
            link_id="link1",
            lab_id="lab1",
            port_a=port_a.key,
            port_b=port_b.key,
            vlan_tag=2001,
        )
        mgr._links[link.key] = link

        await delete_port(mgr, "c-a", "eth1")

        # Link should have been removed along with the port
        assert len(mgr._links) == 0
        # port_b should still exist
        assert port_b.key in mgr._ports


# ---------------------------------------------------------------------------
# TestHostVethPeerMissing
# ---------------------------------------------------------------------------

class TestHostVethPeerMissing:
    """Tests for host_veth_peer_missing() sysfs fallback."""

    @pytest.mark.asyncio
    async def test_missing_peer_detected_via_sysfs(self) -> None:
        """When iflink == ifindex, peer is considered missing."""
        from agent.network.ovs_provision import host_veth_peer_missing

        # iflink == ifindex => no live peer
        with patch("agent.network.ovs_provision.Path") as MockPath:
            mock_ifindex = MagicMock()
            mock_ifindex.read_text.return_value = "42\n"
            mock_iflink = MagicMock()
            mock_iflink.read_text.return_value = "42\n"

            def path_factory(p):
                if "ifindex" in str(p):
                    return mock_ifindex
                if "iflink" in str(p):
                    return mock_iflink
                return MagicMock()

            MockPath.side_effect = path_factory
            result = await host_veth_peer_missing("vhport1")

        assert result is True

    @pytest.mark.asyncio
    async def test_healthy_peer_returns_false(self) -> None:
        """When iflink != ifindex and iflink > 0, peer is healthy."""
        from agent.network.ovs_provision import host_veth_peer_missing

        with patch("agent.network.ovs_provision.Path") as MockPath:
            mock_ifindex = MagicMock()
            mock_ifindex.read_text.return_value = "42\n"
            mock_iflink = MagicMock()
            mock_iflink.read_text.return_value = "99\n"

            def path_factory(p):
                if "ifindex" in str(p):
                    return mock_ifindex
                if "iflink" in str(p):
                    return mock_iflink
                return MagicMock()

            MockPath.side_effect = path_factory
            result = await host_veth_peer_missing("vhport1")

        assert result is False

    @pytest.mark.asyncio
    async def test_sysfs_read_error_returns_false(self) -> None:
        """FileNotFoundError from sysfs is treated as inconclusive (False)."""
        from agent.network.ovs_provision import host_veth_peer_missing

        with patch("agent.network.ovs_provision.Path") as MockPath:
            mock_path = MagicMock()
            mock_path.read_text.side_effect = FileNotFoundError
            MockPath.return_value = mock_path
            result = await host_veth_peer_missing("vhport1")

        assert result is False


# ---------------------------------------------------------------------------
# TestIsPortStale
# ---------------------------------------------------------------------------

class TestIsPortStale:
    """Tests for is_port_stale()."""

    @pytest.mark.asyncio
    async def test_healthy_port_not_stale(self, tmp_path: Path) -> None:
        """Port with live namespace peer is not stale."""
        from agent.network.ovs_provision import is_port_stale

        mgr = _make_manager(tmp_path)
        mgr._ip_link_exists = AsyncMock(return_value=True)
        port = _make_port()

        with patch("agent.network.ovs_provision.get_container_pid", new_callable=AsyncMock, return_value=42):
            # nsenter ip link show succeeds => not stale
            mgr._run_cmd = AsyncMock(return_value=(0, "2: eth1@if7", ""))
            result = await is_port_stale(mgr, port)

        assert result is False

    @pytest.mark.asyncio
    async def test_stale_when_namespace_peer_missing(self, tmp_path: Path) -> None:
        """Port is stale when container peer interface doesn't exist."""
        from agent.network.ovs_provision import is_port_stale

        mgr = _make_manager(tmp_path)
        mgr._ip_link_exists = AsyncMock(return_value=True)
        port = _make_port()

        with patch("agent.network.ovs_provision.get_container_pid", new_callable=AsyncMock, return_value=42):
            # nsenter ip link show fails => peer missing => stale
            mgr._run_cmd = AsyncMock(return_value=(1, "", "Device does not exist"))
            result = await is_port_stale(mgr, port)

        assert result is True

    @pytest.mark.asyncio
    async def test_not_stale_when_host_veth_missing(self, tmp_path: Path) -> None:
        """If host-side veth doesn't exist at all, it's not 'stale' — just gone."""
        from agent.network.ovs_provision import is_port_stale

        mgr = _make_manager(tmp_path)
        mgr._ip_link_exists = AsyncMock(return_value=False)
        port = _make_port()

        result = await is_port_stale(mgr, port)
        assert result is False

    @pytest.mark.asyncio
    async def test_falls_back_to_sysfs_when_pid_unavailable(self, tmp_path: Path) -> None:
        """When container PID can't be obtained, falls back to sysfs heuristic."""
        from agent.network.ovs_provision import is_port_stale

        mgr = _make_manager(tmp_path)
        mgr._ip_link_exists = AsyncMock(return_value=True)
        port = _make_port()

        with patch("agent.network.ovs_provision.get_container_pid", new_callable=AsyncMock, return_value=None), \
             patch("agent.network.ovs_provision.host_veth_peer_missing", new_callable=AsyncMock, return_value=True):
            result = await is_port_stale(mgr, port)

        assert result is True


# ---------------------------------------------------------------------------
# TestCleanupStalePort
# ---------------------------------------------------------------------------

class TestCleanupStalePort:
    """Tests for cleanup_stale_port()."""

    @pytest.mark.asyncio
    async def test_removes_from_ovs_veth_tracking_and_links(self, tmp_path: Path) -> None:
        from agent.network.ovs_provision import cleanup_stale_port

        mgr = _make_manager(tmp_path)
        port = _make_port()
        key = port.key
        mgr._ports[key] = port
        mgr._vlan_allocator._allocated[key] = port.vlan_tag
        mgr._ip_link_exists = AsyncMock(return_value=True)

        # Add a link involving this port
        link = OVSLink(
            link_id="link1", lab_id="lab1",
            port_a=key, port_b="other:eth1", vlan_tag=2001,
        )
        mgr._links[link.key] = link

        await cleanup_stale_port(mgr, port)

        assert key not in mgr._ports
        assert mgr._vlan_allocator.get_vlan(key) is None
        assert len(mgr._links) == 0

    @pytest.mark.asyncio
    async def test_skips_veth_delete_when_not_present(self, tmp_path: Path) -> None:
        from agent.network.ovs_provision import cleanup_stale_port

        mgr = _make_manager(tmp_path)
        port = _make_port()
        mgr._ports[port.key] = port
        mgr._ip_link_exists = AsyncMock(return_value=False)

        await cleanup_stale_port(mgr, port)

        # ip link delete should NOT be called since link doesn't exist
        delete_calls = [
            c for c in mgr._run_cmd.call_args_list
            if c.args[0][:3] == ["ip", "link", "delete"]
        ]
        assert len(delete_calls) == 0


# ---------------------------------------------------------------------------
# TestHandleContainerRestart
# ---------------------------------------------------------------------------

class TestHandleContainerRestart:
    """Tests for handle_container_restart()."""

    @pytest.mark.asyncio
    async def test_no_tracked_ports_returns_zero_counts(self, tmp_path: Path) -> None:
        from agent.network.ovs_provision import handle_container_restart

        mgr = _make_manager(tmp_path)
        result = await handle_container_restart(mgr, "archetype-lab1-r1", "lab1")

        assert result["ports_reprovisioned"] == 0
        assert result["links_reconnected"] == 0
        assert result["errors"] == []

    @pytest.mark.asyncio
    async def test_reprovisions_stale_ports(self, tmp_path: Path) -> None:
        from agent.network.ovs_provision import handle_container_restart

        mgr = _make_manager(tmp_path)
        port = _make_port()
        mgr._ports[port.key] = port
        mgr._vlan_allocator._allocated[port.key] = port.vlan_tag

        with patch("agent.network.ovs_provision.is_port_stale", new_callable=AsyncMock, return_value=True), \
             patch("agent.network.ovs_provision.cleanup_stale_port", new_callable=AsyncMock) as mock_cleanup, \
             patch("agent.network.ovs_provision.provision_interface", new_callable=AsyncMock, return_value=3001) as mock_provision:

            result = await handle_container_restart(mgr, "archetype-lab1-r1", "lab1")

        assert result["ports_reprovisioned"] == 1
        mock_cleanup.assert_awaited_once()
        mock_provision.assert_awaited_once_with(
            mgr,
            container_name="archetype-lab1-r1",
            interface_name="eth1",
            lab_id="lab1",
        )

    @pytest.mark.asyncio
    async def test_reconnects_links_after_reprovisioning(self, tmp_path: Path) -> None:
        from agent.network.ovs_provision import handle_container_restart

        mgr = _make_manager(tmp_path)

        port_a = _make_port(
            port_name="vhA", container_name="archetype-lab1-r1",
            interface_name="eth1", vlan_tag=2001,
        )
        port_b = _make_port(
            port_name="vhB", container_name="archetype-lab1-r2",
            interface_name="eth1", vlan_tag=2001,
        )
        mgr._ports[port_a.key] = port_a
        mgr._ports[port_b.key] = port_b

        link = OVSLink(
            link_id=f"{port_a.key}-{port_b.key}",
            lab_id="lab1",
            port_a=port_a.key,
            port_b=port_b.key,
            vlan_tag=2001,
        )
        mgr._links[link.key] = link

        mgr.hot_connect = AsyncMock(return_value=2001)

        with patch("agent.network.ovs_provision.is_port_stale", new_callable=AsyncMock, return_value=True), \
             patch("agent.network.ovs_provision.cleanup_stale_port", new_callable=AsyncMock), \
             patch("agent.network.ovs_provision.provision_interface", new_callable=AsyncMock, return_value=3001):

            result = await handle_container_restart(mgr, "archetype-lab1-r1", "lab1")

        assert result["ports_reprovisioned"] == 1
        assert result["links_reconnected"] == 1
        mgr.hot_connect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_stale_ports_skips_reprovisioning(self, tmp_path: Path) -> None:
        from agent.network.ovs_provision import handle_container_restart

        mgr = _make_manager(tmp_path)
        port = _make_port()
        mgr._ports[port.key] = port

        with patch("agent.network.ovs_provision.is_port_stale", new_callable=AsyncMock, return_value=False):
            result = await handle_container_restart(mgr, "archetype-lab1-r1", "lab1")

        assert result["ports_reprovisioned"] == 0

    @pytest.mark.asyncio
    async def test_records_errors_on_reprovision_failure(self, tmp_path: Path) -> None:
        from agent.network.ovs_provision import handle_container_restart

        mgr = _make_manager(tmp_path)
        port = _make_port()
        mgr._ports[port.key] = port

        with patch("agent.network.ovs_provision.is_port_stale", new_callable=AsyncMock, return_value=True), \
             patch("agent.network.ovs_provision.cleanup_stale_port", new_callable=AsyncMock), \
             patch("agent.network.ovs_provision.provision_interface", new_callable=AsyncMock,
                   side_effect=RuntimeError("container died")):

            result = await handle_container_restart(mgr, "archetype-lab1-r1", "lab1")

        assert result["ports_reprovisioned"] == 0
        assert len(result["errors"]) == 1
        assert "container died" in result["errors"][0]

    @pytest.mark.asyncio
    async def test_records_errors_on_link_reconnect_failure(self, tmp_path: Path) -> None:
        from agent.network.ovs_provision import handle_container_restart

        mgr = _make_manager(tmp_path)

        port_a = _make_port(
            port_name="vhA", container_name="archetype-lab1-r1",
            interface_name="eth1", vlan_tag=2001,
        )
        port_b = _make_port(
            port_name="vhB", container_name="archetype-lab1-r2",
            interface_name="eth1", vlan_tag=2001,
        )
        mgr._ports[port_a.key] = port_a
        mgr._ports[port_b.key] = port_b

        link = OVSLink(
            link_id=f"{port_a.key}-{port_b.key}",
            lab_id="lab1",
            port_a=port_a.key,
            port_b=port_b.key,
            vlan_tag=2001,
        )
        mgr._links[link.key] = link

        mgr.hot_connect = AsyncMock(side_effect=RuntimeError("VLAN exhausted"))

        with patch("agent.network.ovs_provision.is_port_stale", new_callable=AsyncMock, return_value=True), \
             patch("agent.network.ovs_provision.cleanup_stale_port", new_callable=AsyncMock), \
             patch("agent.network.ovs_provision.provision_interface", new_callable=AsyncMock, return_value=3001):

            result = await handle_container_restart(mgr, "archetype-lab1-r1", "lab1")

        assert result["ports_reprovisioned"] == 1
        assert result["links_reconnected"] == 0
        assert len(result["errors"]) == 1
        assert "VLAN exhausted" in result["errors"][0]


# ---------------------------------------------------------------------------
# TestDiscoverExistingState
# ---------------------------------------------------------------------------

class TestDiscoverExistingState:
    """Tests for discover_existing_state()."""

    @pytest.mark.asyncio
    async def test_empty_bridge_is_noop(self, tmp_path: Path) -> None:
        """No ports on bridge => nothing discovered."""
        from agent.network.ovs_provision import discover_existing_state

        mgr = _make_manager(tmp_path)
        mgr._ovs_vsctl = AsyncMock(return_value=(0, "", ""))

        await discover_existing_state(mgr)

        assert len(mgr._ports) == 0
        assert len(mgr._links) == 0

    @pytest.mark.asyncio
    async def test_discovers_ports_and_links(self, tmp_path: Path) -> None:
        """Discovers ports from OVS and rebuilds internal state."""
        from agent.network.ovs_provision import discover_existing_state

        mgr = _make_manager(tmp_path)

        # Two vh ports sharing the same VLAN tag => should discover a link
        batch_port_json = _json.dumps({
            "data": [
                ["vhport1", 2001],
                ["vhport2", 2001],
            ]
        })

        c1 = MagicMock()
        c1.name = "archetype-lab1-r1"
        c1.attrs = {"State": {"Pid": 111}}
        c1.labels = {"archetype.lab_id": "lab1"}

        c2 = MagicMock()
        c2.name = "archetype-lab1-r2"
        c2.attrs = {"State": {"Pid": 222}}
        c2.labels = {"archetype.lab_id": "lab1"}

        mgr._docker = MagicMock()
        mgr._docker.containers.list.return_value = [c1, c2]

        async def ovs_vsctl_side_effect(*args):
            if args == ("list-ports", "test-ovs"):
                return 0, "vhport1\nvhport2", ""
            if args == ("--format=json", "--", "--columns=name,tag", "list", "Port"):
                return 0, batch_port_json, ""
            return 1, "", "unexpected"

        async def run_cmd_side_effect(cmd):
            if cmd[:8] == ["nsenter", "-t", "111", "-n", "ip", "-o", "link", "show"]:
                return 0, "2: eth1@if101: <BROADCAST>", ""
            if cmd[:8] == ["nsenter", "-t", "222", "-n", "ip", "-o", "link", "show"]:
                return 0, "2: eth1@if102: <BROADCAST>", ""
            return 1, "", ""

        mgr._ovs_vsctl = AsyncMock(side_effect=ovs_vsctl_side_effect)
        mgr._run_cmd = AsyncMock(side_effect=run_cmd_side_effect)

        # Mock sysfs reads and Docker container list
        _ifindex_map = {"vhport1": "101", "vhport2": "102"}

        async def _fake_to_thread(fn, *args, **kwargs):
            if args and isinstance(args[0], list) and all(isinstance(x, str) for x in args[0]):
                return {name: _ifindex_map[name] for name in args[0] if name in _ifindex_map}
            return fn(*args, **kwargs)

        with patch("agent.network.ovs_provision.asyncio.to_thread", side_effect=_fake_to_thread):
            await discover_existing_state(mgr)

        # Should have discovered 2 ports
        assert len(mgr._ports) == 2
        assert "archetype-lab1-r1:eth1" in mgr._ports
        assert "archetype-lab1-r2:eth1" in mgr._ports

        # Both share VLAN 2001 => should discover 1 link
        assert len(mgr._links) == 1

    @pytest.mark.asyncio
    async def test_skips_non_vh_ports(self, tmp_path: Path) -> None:
        """Ports not starting with 'vh' are ignored."""
        from agent.network.ovs_provision import discover_existing_state

        mgr = _make_manager(tmp_path)

        async def ovs_vsctl_side_effect(*args):
            if args == ("list-ports", "test-ovs"):
                return 0, "vxlan100\nbr-int\neth0", ""
            return 0, "", ""

        mgr._ovs_vsctl = AsyncMock(side_effect=ovs_vsctl_side_effect)

        with patch("agent.network.ovs_provision.asyncio.to_thread", new_callable=AsyncMock):
            await discover_existing_state(mgr)

        assert len(mgr._ports) == 0

    @pytest.mark.asyncio
    async def test_discovery_ignores_lo_and_eth0(self, tmp_path: Path) -> None:
        """Container lo and eth0 interfaces are excluded from discovery."""
        from agent.network.ovs_provision import discover_existing_state

        mgr = _make_manager(tmp_path)

        batch_port_json = _json.dumps({
            "data": [["vhport1", 2001]]
        })

        c1 = MagicMock()
        c1.name = "archetype-lab1-r1"
        c1.attrs = {"State": {"Pid": 111}}
        c1.labels = {"archetype.lab_id": "lab1"}

        mgr._docker = MagicMock()
        mgr._docker.containers.list.return_value = [c1]

        async def ovs_vsctl_side_effect(*args):
            if args == ("list-ports", "test-ovs"):
                return 0, "vhport1", ""
            if args == ("--format=json", "--", "--columns=name,tag", "list", "Port"):
                return 0, batch_port_json, ""
            return 1, "", ""

        async def run_cmd_side_effect(cmd):
            if "nsenter" in cmd:
                # Only lo and eth0 — no data interfaces
                return 0, "1: lo: <LOOPBACK>\n2: eth0@if99: <BROADCAST>", ""
            return 1, "", ""

        mgr._ovs_vsctl = AsyncMock(side_effect=ovs_vsctl_side_effect)
        mgr._run_cmd = AsyncMock(side_effect=run_cmd_side_effect)

        _ifindex_map = {"vhport1": "99"}

        async def _fake_to_thread(fn, *args, **kwargs):
            if args and isinstance(args[0], list) and all(isinstance(x, str) for x in args[0]):
                return {name: _ifindex_map[name] for name in args[0] if name in _ifindex_map}
            return fn(*args, **kwargs)

        with patch("agent.network.ovs_provision.asyncio.to_thread", side_effect=_fake_to_thread):
            await discover_existing_state(mgr)

        # eth0 should have been skipped
        assert len(mgr._ports) == 0
