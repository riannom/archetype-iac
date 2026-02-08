"""Unit tests for per-node lifecycle operations in DockerProvider (Phase 3).

Tests cover:
- create_node(): container creation, cEOS setup, existing container handling, missing image
- start_node(): veth repair, interface fix, not found handling
- destroy_node(): container removal, cleanup, last-in-lab network deletion, idempotent success

All Docker and OVS dependencies are mocked for isolated unit testing.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock, call

import pytest
from docker.errors import NotFound, APIError, ImageNotFound

from agent.providers.base import NodeActionResult, NodeStatus
from agent.providers.docker import DockerProvider, TopologyNode


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_docker_client():
    """Create a mock Docker client with standard sub-objects."""
    client = MagicMock()
    client.networks = MagicMock()
    client.containers = MagicMock()
    client.images = MagicMock()
    return client


@pytest.fixture
def provider(mock_docker_client):
    """Create a DockerProvider with mocked Docker client and network managers."""
    with patch("agent.providers.docker.docker.from_env", return_value=mock_docker_client):
        with patch("agent.providers.docker.get_local_manager") as mock_lm:
            with patch("agent.providers.docker.get_ovs_manager"):
                with patch("agent.providers.docker.get_docker_ovs_plugin"):
                    p = DockerProvider()
                    p._docker = mock_docker_client
                    p._local_network = AsyncMock()
                    yield p


@pytest.fixture
def workspace(tmp_path):
    """Provide a temporary workspace directory."""
    return tmp_path


@pytest.fixture
def mock_vendor_config():
    """Return a mock VendorConfig with a default_image."""
    config = MagicMock()
    config.default_image = "linux:latest"
    config.kind = "linux"
    return config


@pytest.fixture
def mock_container():
    """Create a mock Docker container object."""
    container = MagicMock()
    container.short_id = "abc123"
    container.status = "running"
    container.name = "archetype-lab1-node1"
    container.labels = {
        "archetype.lab_id": "lab1",
        "archetype.node_name": "node1",
        "archetype.node_kind": "linux",
        "archetype.provider": "docker",
    }
    container.attrs = {
        "State": {"Pid": 12345},
        "NetworkSettings": {"Networks": {}},
    }
    container.reload = MagicMock()
    container.start = MagicMock()
    container.stop = MagicMock()
    container.remove = MagicMock()
    return container


# ---------------------------------------------------------------------------
# create_node tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_node_basic(provider, workspace, mock_docker_client, mock_vendor_config):
    """create_node builds config correctly, creates container, returns STOPPED."""
    created_container = MagicMock()
    created_container.short_id = "def456"

    # Image exists
    mock_docker_client.images.get.return_value = MagicMock()

    # No existing container
    mock_docker_client.containers.get.side_effect = NotFound("not found")

    # Container creation returns our mock
    mock_docker_client.containers.create.return_value = created_container

    with patch("agent.providers.docker.get_config_by_device", return_value=mock_vendor_config):
        with patch("agent.providers.docker.is_ceos_kind", return_value=False):
            with patch("agent.providers.docker.get_container_config") as mock_gcc:
                mock_gcc.return_value = MagicMock(
                    environment={"FOO": "bar"},
                    binds=[],
                    capabilities=[],
                    sysctls={},
                    ports={},
                    image="linux:latest",
                    privileged=False,
                    command=None,
                    entrypoint=None,
                )
                result = await provider.create_node(
                    lab_id="lab1",
                    node_name="node1",
                    kind="linux",
                    workspace=workspace,
                    image="linux:latest",
                )

    assert result.success is True
    assert result.node_name == "node1"
    assert result.new_status == NodeStatus.STOPPED
    assert "def456" in result.stdout
    mock_docker_client.containers.create.assert_called_once()


@pytest.mark.asyncio
async def test_create_node_ceos(provider, workspace, mock_docker_client):
    """create_node sets up cEOS directories and CLAB_INTFS for cEOS devices."""
    created_container = MagicMock()
    created_container.short_id = "ceos01"

    mock_docker_client.images.get.return_value = MagicMock()
    mock_docker_client.containers.get.side_effect = NotFound("not found")
    mock_docker_client.containers.create.return_value = created_container

    ceos_config = MagicMock()
    ceos_config.default_image = "ceos:4.30.0F"
    ceos_config.kind = "ceos"

    with patch("agent.providers.docker.get_config_by_device", return_value=ceos_config):
        with patch("agent.providers.docker.is_ceos_kind", return_value=True):
            with patch("agent.providers.docker.get_container_config") as mock_gcc:
                mock_gcc.return_value = MagicMock(
                    environment={"INTFTYPE": "eth", "CEOS": "1"},
                    binds=[],
                    capabilities=[],
                    sysctls={},
                    ports={},
                    image="ceos:4.30.0F",
                    privileged=False,
                    command=None,
                    entrypoint=None,
                )
                result = await provider.create_node(
                    lab_id="lab1",
                    node_name="eos1",
                    kind="ceos",
                    workspace=workspace,
                    image="ceos:4.30.0F",
                    interface_count=4,
                )

    assert result.success is True
    assert result.new_status == NodeStatus.STOPPED

    # Verify cEOS flash directory was created
    flash_dir = workspace / "configs" / "eos1" / "flash"
    assert flash_dir.exists()

    # Verify systemd env config was created
    systemd_env = workspace / "configs" / "eos1" / "systemd" / "ceos-env.conf"
    assert systemd_env.exists()
    content = systemd_env.read_text()
    assert "EOS_PLATFORM=ceoslab" in content


@pytest.mark.asyncio
async def test_create_node_existing_running(
    provider, workspace, mock_docker_client, mock_container, mock_vendor_config
):
    """create_node returns success without recreating when container is already running."""
    mock_docker_client.images.get.return_value = MagicMock()
    mock_container.status = "running"
    mock_docker_client.containers.get.return_value = mock_container

    with patch("agent.providers.docker.get_config_by_device", return_value=mock_vendor_config):
        with patch("agent.providers.docker.is_ceos_kind", return_value=False):
            result = await provider.create_node(
                lab_id="lab1",
                node_name="node1",
                kind="linux",
                workspace=workspace,
            )

    assert result.success is True
    assert result.node_name == "node1"
    assert result.new_status == NodeStatus.RUNNING
    assert "already running" in result.stdout

    # Should NOT have created a new container
    mock_docker_client.containers.create.assert_not_called()
    # Should NOT have removed the existing container
    mock_container.remove.assert_not_called()


@pytest.mark.asyncio
async def test_create_node_existing_stopped(
    provider, workspace, mock_docker_client, mock_container, mock_vendor_config
):
    """create_node removes stopped container and recreates it."""
    mock_docker_client.images.get.return_value = MagicMock()

    # First .get() returns stopped container, subsequent gets raise NotFound
    # (because we removed it and the code doesn't call get again after removal)
    mock_container.status = "exited"
    mock_docker_client.containers.get.return_value = mock_container

    new_container = MagicMock()
    new_container.short_id = "new789"
    mock_docker_client.containers.create.return_value = new_container

    with patch("agent.providers.docker.get_config_by_device", return_value=mock_vendor_config):
        with patch("agent.providers.docker.is_ceos_kind", return_value=False):
            with patch("agent.providers.docker.get_container_config") as mock_gcc:
                mock_gcc.return_value = MagicMock(
                    environment={},
                    binds=[],
                    capabilities=[],
                    sysctls={},
                    ports={},
                    image="linux:latest",
                    privileged=False,
                    command=None,
                    entrypoint=None,
                )
                result = await provider.create_node(
                    lab_id="lab1",
                    node_name="node1",
                    kind="linux",
                    workspace=workspace,
                )

    assert result.success is True
    assert result.new_status == NodeStatus.STOPPED

    # Old container should have been removed with force
    mock_container.remove.assert_called_once_with(force=True)

    # New container should have been created
    mock_docker_client.containers.create.assert_called_once()
    assert "new789" in result.stdout


@pytest.mark.asyncio
async def test_create_node_missing_image(
    provider, workspace, mock_docker_client, mock_vendor_config
):
    """create_node returns error when Docker image is not found."""
    mock_docker_client.images.get.side_effect = ImageNotFound("not found")

    with patch("agent.providers.docker.get_config_by_device", return_value=mock_vendor_config):
        with patch("agent.providers.docker.is_ceos_kind", return_value=False):
            result = await provider.create_node(
                lab_id="lab1",
                node_name="node1",
                kind="linux",
                workspace=workspace,
                image="nonexistent:latest",
            )

    assert result.success is False
    assert result.node_name == "node1"
    assert "not found" in result.error.lower()
    assert "nonexistent:latest" in result.error

    # Should NOT have attempted to create a container
    mock_docker_client.containers.create.assert_not_called()


# ---------------------------------------------------------------------------
# start_node tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_node_with_veth_repair(
    provider, workspace, mock_docker_client, mock_container
):
    """start_node calls repair_endpoints and _fix_interface_names after start."""
    mock_container.status = "running"
    mock_docker_client.containers.get.return_value = mock_container

    mock_plugin = AsyncMock()
    mock_plugin.repair_endpoints.return_value = [
        {"status": "repaired", "interface": "eth1"},
        {"status": "repaired", "interface": "eth2"},
        {"status": "ok", "interface": "eth3"},
    ]

    mock_fix_ifaces = AsyncMock(
        return_value={"fixed": 1, "already_correct": 2, "errors": []}
    )

    with patch("agent.providers.docker.get_docker_ovs_plugin", return_value=mock_plugin):
        with patch.object(provider, "_fix_interface_names", mock_fix_ifaces):
            with patch.object(
                type(provider), "use_ovs_plugin", new_callable=PropertyMock, return_value=True
            ):
                result = await provider.start_node(
                    lab_id="lab1",
                    node_name="node1",
                    workspace=workspace,
                    repair_endpoints=True,
                    fix_interfaces=True,
                )

    assert result.success is True
    assert result.node_name == "node1"
    assert result.new_status == NodeStatus.RUNNING

    # Verify container was started
    mock_container.start.assert_called_once()

    # Verify repair_endpoints was called
    mock_plugin.repair_endpoints.assert_awaited_once()

    # Verify _fix_interface_names was called
    mock_fix_ifaces.assert_awaited_once()

    # Verify stdout mentions the repairs
    assert "repaired 2 endpoints" in result.stdout
    assert "fixed 1 interfaces" in result.stdout


@pytest.mark.asyncio
async def test_start_node_without_repair(
    provider, workspace, mock_docker_client, mock_container
):
    """start_node with repair_endpoints=False skips veth repair."""
    mock_container.status = "running"
    mock_docker_client.containers.get.return_value = mock_container

    mock_plugin = AsyncMock()
    mock_fix_ifaces = AsyncMock(
        return_value={"fixed": 0, "already_correct": 0, "errors": []}
    )

    with patch("agent.providers.docker.get_docker_ovs_plugin", return_value=mock_plugin):
        with patch.object(provider, "_fix_interface_names", mock_fix_ifaces):
            with patch.object(
                type(provider), "use_ovs_plugin", new_callable=PropertyMock, return_value=True
            ):
                result = await provider.start_node(
                    lab_id="lab1",
                    node_name="node1",
                    workspace=workspace,
                    repair_endpoints=False,
                    fix_interfaces=False,
                )

    assert result.success is True
    assert result.node_name == "node1"

    # Container should still be started
    mock_container.start.assert_called_once()

    # Repair should NOT have been called
    mock_plugin.repair_endpoints.assert_not_awaited()

    # Fix interfaces should NOT have been called
    mock_fix_ifaces.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_node_not_found(provider, workspace, mock_docker_client):
    """start_node returns error when container does not exist."""
    mock_docker_client.containers.get.side_effect = NotFound("not found")

    result = await provider.start_node(
        lab_id="lab1",
        node_name="missing_node",
        workspace=workspace,
    )

    assert result.success is False
    assert result.node_name == "missing_node"
    assert "not found" in result.error.lower()


# ---------------------------------------------------------------------------
# destroy_node tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_destroy_node_basic(
    provider, workspace, mock_docker_client, mock_container
):
    """destroy_node removes container, clears post-boot state, cleans VLANs."""
    mock_docker_client.containers.get.return_value = mock_container

    # Other containers still exist in the lab
    other_container = MagicMock()
    other_container.labels = {"archetype.lab_id": "lab1"}
    mock_docker_client.containers.list.return_value = [other_container]

    # Pre-populate VLAN allocations for the node
    provider._vlan_allocations["lab1"] = {"node1": [100, 101], "node2": [102, 103]}

    with patch("agent.readiness.clear_post_boot_state") as mock_clear:
        result = await provider.destroy_node(
            lab_id="lab1",
            node_name="node1",
            workspace=workspace,
        )

    assert result.success is True
    assert result.node_name == "node1"
    assert result.new_status == NodeStatus.STOPPED
    assert "Destroyed" in result.stdout

    # Container removed with force and v flags
    mock_container.remove.assert_called_once_with(force=True, v=True)

    # Post-boot state cleared
    mock_clear.assert_called_once_with("archetype-lab1-node1")

    # VLAN allocations for this node should be cleaned
    assert "node1" not in provider._vlan_allocations["lab1"]
    # Other node's VLANs should still be there
    assert "node2" in provider._vlan_allocations["lab1"]


@pytest.mark.asyncio
async def test_destroy_node_last_in_lab(
    provider, workspace, mock_docker_client, mock_container
):
    """destroy_node deletes lab networks when it is the last container."""
    mock_docker_client.containers.get.return_value = mock_container

    # No remaining containers in the lab
    mock_docker_client.containers.list.return_value = []

    # Pre-populate VLAN allocations
    provider._vlan_allocations["lab1"] = {"node1": [100, 101]}
    provider._next_vlan["lab1"] = 102

    with patch("agent.readiness.clear_post_boot_state"):
        with patch.object(
            provider, "_delete_lab_networks", new_callable=AsyncMock
        ) as mock_delete_nets:
            result = await provider.destroy_node(
                lab_id="lab1",
                node_name="node1",
                workspace=workspace,
            )

    assert result.success is True

    # Lab networks should be cleaned up
    mock_delete_nets.assert_awaited_once_with("lab1")

    # Lab-level VLAN tracking should be cleaned up
    assert "lab1" not in provider._vlan_allocations
    assert "lab1" not in provider._next_vlan


@pytest.mark.asyncio
async def test_destroy_node_not_found(provider, workspace, mock_docker_client):
    """destroy_node is idempotent: returns success even if container doesn't exist."""
    mock_docker_client.containers.get.side_effect = NotFound("not found")

    # No remaining containers at all
    mock_docker_client.containers.list.return_value = []

    with patch("agent.readiness.clear_post_boot_state") as mock_clear:
        with patch.object(
            provider, "_delete_lab_networks", new_callable=AsyncMock
        ) as mock_delete_nets:
            result = await provider.destroy_node(
                lab_id="lab1",
                node_name="ghost_node",
                workspace=workspace,
            )

    assert result.success is True
    assert result.node_name == "ghost_node"

    # Post-boot state should still be cleared (idempotent)
    container_name = provider._container_name("lab1", "ghost_node")
    mock_clear.assert_called_once_with(container_name)

    # Since no containers remain, networks should be deleted
    mock_delete_nets.assert_awaited_once_with("lab1")
