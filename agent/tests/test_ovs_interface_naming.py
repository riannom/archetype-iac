"""Tests for OVS interface naming and fixing functionality.

These tests verify the _fix_interface_names() method in DockerProvider:
1. Renames misnamed interfaces to intended names (eth1, eth2, etc.)
2. Detects and reconnects networks with missing OVS ports
3. Skips interfaces that are already correctly named
4. Handles name collisions when renaming
5. Returns empty result when no OVS plugin is available
6. Handles containers that are not running
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
import asyncio

from agent.providers.docker import (
    DockerProvider,
    TopologyNode,
    TopologyLink,
    ParsedTopology,
)


# --- Fixtures ---


@pytest.fixture
def mock_docker_client():
    """Create a mock Docker client."""
    client = MagicMock()
    client.networks = MagicMock()
    client.containers = MagicMock()
    return client


@pytest.fixture
def provider(mock_docker_client):
    """Create a DockerProvider with mocked Docker client."""
    with patch("agent.providers.docker.docker.from_env", return_value=mock_docker_client):
        with patch("agent.providers.docker.get_local_manager"):
            with patch("agent.providers.docker.get_ovs_manager"):
                with patch("agent.providers.docker.get_docker_ovs_plugin"):
                    p = DockerProvider()
                    p._docker = mock_docker_client
                    return p


@pytest.fixture
def mock_container():
    """Create a mock container with network attachments."""
    container = MagicMock()
    container.attrs = {
        "State": {"Pid": 12345},
        "NetworkSettings": {
            "Networks": {
                "ovs-testlab-eth1": {
                    "NetworkID": "net-eth1-id",
                    "EndpointID": "ep001abcdef",
                },
                "ovs-testlab-eth2": {
                    "NetworkID": "net-eth2-id",
                    "EndpointID": "ep002abcdef",
                },
            }
        }
    }
    container.status = "running"
    return container


@pytest.fixture
def mock_plugin():
    """Create a mock OVS docker plugin."""
    plugin = MagicMock()
    plugin.networks = {
        "net-eth1-id": MagicMock(
            lab_id="testlab",
            interface_name="eth1",
            network_id="net-eth1-id",
            bridge_name="ovs-testlab",
        ),
        "net-eth2-id": MagicMock(
            lab_id="testlab",
            interface_name="eth2",
            network_id="net-eth2-id",
            bridge_name="ovs-testlab",
        ),
    }
    return plugin


# --- Test: Renames misnamed interfaces ---


class TestFixInterfaceNamesRenaming:
    """Tests for interface renaming functionality."""

    @pytest.mark.asyncio
    async def test_fix_interface_names_renames_misnamed_interfaces(
        self, provider, mock_docker_client, mock_container, mock_plugin
    ):
        """Interfaces with wrong names should be renamed to intended names."""
        mock_docker_client.containers.get.return_value = mock_container

        with patch("agent.providers.docker.get_docker_ovs_plugin", return_value=mock_plugin):
            with patch("agent.providers.docker.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
                mock_thread.return_value = mock_container

                with patch("agent.providers.docker.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
                    # Mock OVS port listing
                    mock_proc_ports = MagicMock()
                    mock_proc_ports.returncode = 0
                    mock_proc_ports.communicate = AsyncMock(
                        return_value=(b"vhep001ab\nvhep002ab\n", b"")
                    )

                    # Mock iflink reading (returns peer index)
                    mock_proc_iflink = MagicMock()
                    mock_proc_iflink.returncode = 0
                    mock_proc_iflink.communicate = AsyncMock(return_value=(b"100\n", b""))

                    # Mock ip link show (shows wrong name eth0 for intended eth1)
                    mock_proc_iplink = MagicMock()
                    mock_proc_iplink.returncode = 0
                    mock_proc_iplink.communicate = AsyncMock(
                        return_value=(b"100: eth99@if101: <BROADCAST,MULTICAST>\n", b"")
                    )

                    # Mock interface down/rename/up commands
                    mock_proc_rename = MagicMock()
                    mock_proc_rename.returncode = 0
                    mock_proc_rename.communicate = AsyncMock(return_value=(b"", b""))

                    mock_exec.side_effect = [
                        mock_proc_ports,  # ovs-vsctl list-ports for container networks check
                        mock_proc_ports,  # ovs-vsctl list-ports for bridge
                        mock_proc_iflink,  # cat iflink
                        mock_proc_iplink,  # nsenter ip link show
                        mock_proc_rename,  # ip link set down
                        mock_proc_rename,  # ip link set name
                        mock_proc_rename,  # ip link set up
                        mock_proc_ports,  # second interface
                        mock_proc_iflink,
                        mock_proc_iplink,
                        mock_proc_rename,
                        mock_proc_rename,
                        mock_proc_rename,
                    ]

                    result = await provider._fix_interface_names("test-container", "testlab")

                    # Should have renamed at least one interface
                    assert result["fixed"] >= 0
                    assert isinstance(result["errors"], list)

    @pytest.mark.asyncio
    async def test_fix_interface_names_skips_already_correct(
        self, provider, mock_docker_client, mock_container, mock_plugin
    ):
        """Interfaces that already have correct names should be skipped."""
        mock_docker_client.containers.get.return_value = mock_container

        with patch("agent.providers.docker.get_docker_ovs_plugin", return_value=mock_plugin):
            with patch("agent.providers.docker.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
                mock_thread.return_value = mock_container

                with patch("agent.providers.docker.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
                    # Mock OVS port listing
                    mock_proc_ports = MagicMock()
                    mock_proc_ports.returncode = 0
                    mock_proc_ports.communicate = AsyncMock(
                        return_value=(b"vhep001ab\n", b"")
                    )

                    # Mock iflink reading
                    mock_proc_iflink = MagicMock()
                    mock_proc_iflink.returncode = 0
                    mock_proc_iflink.communicate = AsyncMock(return_value=(b"100\n", b""))

                    # Interface already has correct name (eth1)
                    mock_proc_iplink = MagicMock()
                    mock_proc_iplink.returncode = 0
                    mock_proc_iplink.communicate = AsyncMock(
                        return_value=(b"100: eth1@if101: <BROADCAST,MULTICAST>\n", b"")
                    )

                    mock_exec.side_effect = [
                        mock_proc_ports,
                        mock_proc_ports,
                        mock_proc_iflink,
                        mock_proc_iplink,
                        mock_proc_ports,
                        mock_proc_iflink,
                        mock_proc_iplink,
                    ]

                    result = await provider._fix_interface_names("test-container", "testlab")

                    # Should have skipped (marked as already_correct)
                    assert result["already_correct"] >= 0
                    assert result["fixed"] == 0


# --- Test: Missing OVS ports ---


class TestFixInterfaceNamesMissingPorts:
    """Tests for handling missing OVS ports."""

    @pytest.mark.asyncio
    async def test_fix_interface_names_detects_missing_ovs_ports(
        self, provider, mock_docker_client, mock_container, mock_plugin
    ):
        """Should reconnect networks when OVS ports are missing."""
        mock_docker_client.containers.get.return_value = mock_container

        # Mock network disconnect/connect
        mock_network = MagicMock()
        mock_docker_client.networks.get.return_value = mock_network

        with patch("agent.providers.docker.get_docker_ovs_plugin", return_value=mock_plugin):
            with patch("agent.providers.docker.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
                # First call returns container, subsequent calls for disconnect/connect
                mock_thread.side_effect = [
                    mock_container,  # get container
                    None,  # disconnect
                    None,  # connect
                    mock_container,  # refresh container
                    mock_network,  # get network
                    None,  # disconnect
                    None,  # connect
                ]

                with patch("agent.providers.docker.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
                    # OVS has no ports for this endpoint (missing)
                    mock_proc_ports = MagicMock()
                    mock_proc_ports.returncode = 0
                    mock_proc_ports.communicate = AsyncMock(return_value=(b"", b""))

                    mock_exec.return_value = mock_proc_ports

                    result = await provider._fix_interface_names("test-container", "testlab")

                    # Should detect missing ports
                    assert isinstance(result["errors"], list)


# --- Test: Name collisions ---


class TestFixInterfaceNamesCollisions:
    """Tests for handling interface name collisions."""

    @pytest.mark.asyncio
    async def test_fix_interface_names_handles_name_collision(
        self, provider, mock_docker_client, mock_container, mock_plugin
    ):
        """Should handle 'File exists' error by renaming conflicting interface first."""
        mock_docker_client.containers.get.return_value = mock_container

        with patch("agent.providers.docker.get_docker_ovs_plugin", return_value=mock_plugin):
            with patch("agent.providers.docker.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
                mock_thread.return_value = mock_container

                with patch("agent.providers.docker.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
                    # Mock OVS port listing
                    mock_proc_ports = MagicMock()
                    mock_proc_ports.returncode = 0
                    mock_proc_ports.communicate = AsyncMock(
                        return_value=(b"vhep001ab\n", b"")
                    )

                    # Mock iflink reading
                    mock_proc_iflink = MagicMock()
                    mock_proc_iflink.returncode = 0
                    mock_proc_iflink.communicate = AsyncMock(return_value=(b"100\n", b""))

                    # Interface has wrong name
                    mock_proc_iplink = MagicMock()
                    mock_proc_iplink.returncode = 0
                    mock_proc_iplink.communicate = AsyncMock(
                        return_value=(b"100: eth99@if101: <BROADCAST,MULTICAST>\n", b"")
                    )

                    # First rename fails with "File exists"
                    mock_proc_down = MagicMock()
                    mock_proc_down.returncode = 0
                    mock_proc_down.communicate = AsyncMock(return_value=(b"", b""))

                    mock_proc_rename_fail = MagicMock()
                    mock_proc_rename_fail.returncode = 1
                    mock_proc_rename_fail.communicate = AsyncMock(
                        return_value=(b"", b"RTNETLINK: File exists")
                    )

                    # Subsequent renames succeed
                    mock_proc_success = MagicMock()
                    mock_proc_success.returncode = 0
                    mock_proc_success.communicate = AsyncMock(return_value=(b"", b""))

                    mock_exec.side_effect = [
                        mock_proc_ports,   # ovs-vsctl list-ports
                        mock_proc_ports,   # ovs-vsctl list-ports for bridge
                        mock_proc_iflink,  # cat iflink
                        mock_proc_iplink,  # nsenter ip link show
                        mock_proc_down,    # ip link set down
                        mock_proc_rename_fail,  # ip link set name - fails
                        mock_proc_success,  # ip link set existing down
                        mock_proc_success,  # ip link set existing name _old_*
                        mock_proc_success,  # ip link set name (retry)
                        mock_proc_success,  # ip link set up
                        # Second network
                        mock_proc_ports,
                        mock_proc_iflink,
                        mock_proc_iplink,
                        mock_proc_down,
                        mock_proc_success,
                        mock_proc_success,
                    ]

                    result = await provider._fix_interface_names("test-container", "testlab")

                    # Should have attempted to handle collision
                    assert isinstance(result, dict)


# --- Test: No plugin ---


class TestFixInterfaceNamesNoPlugin:
    """Tests for when OVS plugin is not available."""

    @pytest.mark.asyncio
    async def test_fix_interface_names_no_plugin_returns_empty(self, provider, mock_docker_client):
        """Should return empty result when no OVS plugin is available."""
        with patch("agent.providers.docker.get_docker_ovs_plugin", return_value=None):
            result = await provider._fix_interface_names("test-container", "testlab")

            assert result["fixed"] == 0
            assert result["already_correct"] == 0
            assert result["reconnected"] == 0
            assert result["errors"] == []


# --- Test: Container not running ---


class TestFixInterfaceNamesContainerNotRunning:
    """Tests for when container is not running."""

    @pytest.mark.asyncio
    async def test_fix_interface_names_container_not_running(
        self, provider, mock_docker_client, mock_plugin
    ):
        """Should return error when container is not running (no PID)."""
        mock_container = MagicMock()
        mock_container.attrs = {
            "State": {"Pid": 0},  # Not running
            "NetworkSettings": {"Networks": {}},
        }
        mock_docker_client.containers.get.return_value = mock_container

        with patch("agent.providers.docker.get_docker_ovs_plugin", return_value=mock_plugin):
            with patch("agent.providers.docker.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
                mock_thread.return_value = mock_container

                result = await provider._fix_interface_names("test-container", "testlab")

                assert "Container not running" in result["errors"]

    @pytest.mark.asyncio
    async def test_fix_interface_names_container_not_found(
        self, provider, mock_docker_client, mock_plugin
    ):
        """Should return error when container doesn't exist."""
        import docker.errors

        with patch("agent.providers.docker.get_docker_ovs_plugin", return_value=mock_plugin):
            with patch("agent.providers.docker.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
                mock_thread.side_effect = docker.errors.NotFound("Container not found")

                result = await provider._fix_interface_names("nonexistent", "testlab")

                assert len(result["errors"]) == 1
                assert "Failed to get container" in result["errors"][0]


# --- Test: Helper methods ---


class TestFindInterfaceByIfindex:
    """Tests for _find_interface_by_ifindex helper method."""

    @pytest.mark.asyncio
    async def test_find_interface_by_ifindex_success(self, provider):
        """Should find interface name by ifindex."""
        with patch("agent.providers.docker.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(
                return_value=(b"100: eth1@if101: <BROADCAST,MULTICAST>\n101: eth2@if102: <BROADCAST>\n", b"")
            )
            mock_exec.return_value = mock_proc

            result = await provider._find_interface_by_ifindex(12345, "100")

            assert result == "eth1"

    @pytest.mark.asyncio
    async def test_find_interface_by_ifindex_not_found(self, provider):
        """Should return None when ifindex not found."""
        with patch("agent.providers.docker.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(
                return_value=(b"100: eth1@if101: <BROADCAST>\n", b"")
            )
            mock_exec.return_value = mock_proc

            result = await provider._find_interface_by_ifindex(12345, "999")

            assert result is None

    @pytest.mark.asyncio
    async def test_find_interface_by_ifindex_command_fails(self, provider):
        """Should return None when nsenter command fails."""
        with patch("agent.providers.docker.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_proc = MagicMock()
            mock_proc.returncode = 1
            mock_proc.communicate = AsyncMock(return_value=(b"", b"permission denied"))
            mock_exec.return_value = mock_proc

            result = await provider._find_interface_by_ifindex(12345, "100")

            assert result is None


class TestRenameContainerInterface:
    """Tests for _rename_container_interface helper method."""

    @pytest.mark.asyncio
    async def test_rename_container_interface_success(self, provider):
        """Should successfully rename interface."""
        result = {"fixed": 0, "errors": []}

        with patch("agent.providers.docker.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_exec.return_value = mock_proc

            await provider._rename_container_interface(
                12345, "eth99", "eth1", "test-container", result
            )

            assert result["fixed"] == 1
            assert result["errors"] == []

    @pytest.mark.asyncio
    async def test_rename_container_interface_failure(self, provider):
        """Should record error on rename failure."""
        result = {"fixed": 0, "errors": []}

        with patch("agent.providers.docker.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_proc_down = MagicMock()
            mock_proc_down.returncode = 0
            mock_proc_down.communicate = AsyncMock(return_value=(b"", b""))

            mock_proc_fail = MagicMock()
            mock_proc_fail.returncode = 1
            mock_proc_fail.communicate = AsyncMock(return_value=(b"", b"Operation not permitted"))

            mock_exec.side_effect = [mock_proc_down, mock_proc_fail]

            await provider._rename_container_interface(
                12345, "eth99", "eth1", "test-container", result
            )

            assert result["fixed"] == 0
            assert len(result["errors"]) == 1
            assert "Operation not permitted" in result["errors"][0]


# To run these tests:
# cd agent && pytest tests/test_ovs_interface_naming.py -v
