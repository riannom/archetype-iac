"""Tests for the containerlab provider.

These tests verify that:
1. Container name generation works correctly
2. Status mapping works correctly
3. Deploy/destroy lifecycle works
4. Error handling for failures works
5. Cleanup on failed deployments works
"""

import asyncio
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

from agent.providers.containerlab import ContainerlabProvider
from agent.providers.base import NodeStatus


# --- Unit Tests for Container Name Generation ---

def test_lab_prefix_generation():
    """Test that lab prefix is generated correctly."""
    provider = ContainerlabProvider()

    prefix = provider._lab_prefix("abc123")

    assert prefix == "clab-abc123"


def test_lab_prefix_sanitization():
    """Test that special characters are sanitized from lab ID."""
    provider = ContainerlabProvider()

    # Input: lab-with-special!@#chars
    # After removing !@#: lab-with-specialchars (21 chars)
    # Truncated to 20: lab-with-specialchar
    prefix = provider._lab_prefix("lab-with-special!@#chars")

    assert prefix == "clab-lab-with-specialchar"


def test_lab_prefix_truncation():
    """Test that long lab IDs are truncated."""
    provider = ContainerlabProvider()

    prefix = provider._lab_prefix("a" * 50)

    # Should be truncated to 20 chars
    assert prefix == "clab-" + "a" * 20


def test_get_container_name():
    """Test container name construction."""
    provider = ContainerlabProvider()

    name = provider.get_container_name("lab123", "router1")

    assert name == "clab-lab123-router1"


# --- Unit Tests for Status Mapping ---

def test_status_mapping_running():
    """Test Docker status 'running' maps to NodeStatus.RUNNING."""
    provider = ContainerlabProvider()
    mock_container = MagicMock()
    mock_container.status = "running"

    status = provider._get_container_status(mock_container)

    assert status == NodeStatus.RUNNING


def test_status_mapping_created():
    """Test Docker status 'created' maps to NodeStatus.PENDING."""
    provider = ContainerlabProvider()
    mock_container = MagicMock()
    mock_container.status = "created"

    status = provider._get_container_status(mock_container)

    assert status == NodeStatus.PENDING


def test_status_mapping_exited():
    """Test Docker status 'exited' maps to NodeStatus.STOPPED."""
    provider = ContainerlabProvider()
    mock_container = MagicMock()
    mock_container.status = "exited"

    status = provider._get_container_status(mock_container)

    assert status == NodeStatus.STOPPED


def test_status_mapping_unknown():
    """Test unknown Docker status maps to NodeStatus.UNKNOWN."""
    provider = ContainerlabProvider()
    mock_container = MagicMock()
    mock_container.status = "unknown_status"

    status = provider._get_container_status(mock_container)

    assert status == NodeStatus.UNKNOWN


# --- Unit Tests for IP Extraction ---

def test_get_container_ips_single():
    """Test extracting single IP from container."""
    provider = ContainerlabProvider()
    mock_container = MagicMock()
    mock_container.attrs = {
        "NetworkSettings": {
            "Networks": {
                "bridge": {"IPAddress": "172.17.0.2"}
            }
        }
    }

    ips = provider._get_container_ips(mock_container)

    assert ips == ["172.17.0.2"]


def test_get_container_ips_multiple():
    """Test extracting multiple IPs from container."""
    provider = ContainerlabProvider()
    mock_container = MagicMock()
    mock_container.attrs = {
        "NetworkSettings": {
            "Networks": {
                "mgmt": {"IPAddress": "172.20.20.2"},
                "data": {"IPAddress": "10.0.0.2"}
            }
        }
    }

    ips = provider._get_container_ips(mock_container)

    assert len(ips) == 2
    assert "172.20.20.2" in ips
    assert "10.0.0.2" in ips


def test_get_container_ips_empty():
    """Test extracting IPs when none assigned."""
    provider = ContainerlabProvider()
    mock_container = MagicMock()
    mock_container.attrs = {"NetworkSettings": {"Networks": {}}}

    ips = provider._get_container_ips(mock_container)

    assert ips == []


# --- Unit Tests for Node Info Extraction ---

def test_node_from_container_valid():
    """Test converting container to NodeInfo."""
    provider = ContainerlabProvider()
    mock_container = MagicMock()
    mock_container.name = "clab-lab123-router1"
    mock_container.status = "running"
    mock_container.short_id = "abc123"
    mock_container.image.tags = ["ghcr.io/nokia/srlinux:latest"]
    mock_container.attrs = {"NetworkSettings": {"Networks": {}}}

    node = provider._node_from_container(mock_container, "clab-lab123")

    assert node is not None
    assert node.name == "router1"
    assert node.status == NodeStatus.RUNNING
    assert node.container_id == "abc123"
    assert node.image == "ghcr.io/nokia/srlinux:latest"


def test_node_from_container_wrong_prefix():
    """Test that containers with wrong prefix return None."""
    provider = ContainerlabProvider()
    mock_container = MagicMock()
    mock_container.name = "clab-otherlab-router1"

    node = provider._node_from_container(mock_container, "clab-lab123")

    assert node is None


# --- Async Tests for Deploy/Destroy ---

@pytest.mark.asyncio
async def test_deploy_creates_workspace():
    """Test that deploy creates workspace directory."""
    provider = ContainerlabProvider()
    workspace = Path("/tmp/test-workspace-deploy")

    with patch.object(provider, '_run_clab', new_callable=AsyncMock) as mock_clab:
        mock_clab.return_value = (0, "Deployed", "")

        with patch.object(provider, 'status', new_callable=AsyncMock) as mock_status:
            from agent.providers.base import StatusResult
            mock_status.return_value = StatusResult(lab_exists=True, nodes=[])

            try:
                result = await provider.deploy(
                    lab_id="test123",
                    topology_yaml="name: test\n",
                    workspace=workspace,
                )

                assert workspace.exists()
                assert result.success

            finally:
                # Cleanup
                if workspace.exists():
                    import shutil
                    shutil.rmtree(workspace)


@pytest.mark.asyncio
async def test_deploy_writes_topology():
    """Test that deploy writes topology file."""
    provider = ContainerlabProvider()
    workspace = Path("/tmp/test-workspace-topo")

    with patch.object(provider, '_run_clab', new_callable=AsyncMock) as mock_clab:
        mock_clab.return_value = (0, "Deployed", "")

        with patch.object(provider, 'status', new_callable=AsyncMock) as mock_status:
            from agent.providers.base import StatusResult
            mock_status.return_value = StatusResult(lab_exists=True, nodes=[])

            try:
                await provider.deploy(
                    lab_id="test123",
                    topology_yaml="name: test-topology\n",
                    workspace=workspace,
                )

                topo_path = workspace / "topology.clab.yml"
                assert topo_path.exists()
                assert "test-topology" in topo_path.read_text()

            finally:
                if workspace.exists():
                    import shutil
                    shutil.rmtree(workspace)


@pytest.mark.asyncio
async def test_deploy_failure_triggers_cleanup():
    """Test that failed deploy triggers cleanup."""
    provider = ContainerlabProvider()
    workspace = Path("/tmp/test-workspace-cleanup")

    with patch.object(provider, '_run_clab', new_callable=AsyncMock) as mock_clab:
        # First call (deploy) fails, second call (destroy during cleanup) succeeds
        mock_clab.side_effect = [
            (1, "", "Error: deployment failed"),
            (0, "Cleaned", ""),
        ]

        with patch.object(provider, '_cleanup_failed_deploy', new_callable=AsyncMock) as mock_cleanup:
            try:
                result = await provider.deploy(
                    lab_id="test123",
                    topology_yaml="name: test\n",
                    workspace=workspace,
                )

                assert not result.success
                mock_cleanup.assert_called_once()

            finally:
                if workspace.exists():
                    import shutil
                    shutil.rmtree(workspace)


@pytest.mark.asyncio
async def test_status_queries_docker():
    """Test that status queries Docker for containers."""
    provider = ContainerlabProvider()

    mock_container = MagicMock()
    mock_container.name = "clab-test123-node1"
    mock_container.status = "running"
    mock_container.short_id = "abc"
    mock_container.image.tags = ["test:latest"]
    mock_container.attrs = {"NetworkSettings": {"Networks": {}}}

    # Set _docker directly since docker is a property
    mock_docker = MagicMock()
    mock_docker.containers.list.return_value = [mock_container]
    provider._docker = mock_docker

    result = await provider.status("test123", Path("/tmp"))

    assert result.lab_exists
    assert len(result.nodes) == 1
    assert result.nodes[0].name == "node1"


# --- Tests for Lab Discovery ---

@pytest.mark.asyncio
async def test_discover_labs_finds_containers():
    """Test that discover_labs finds containerlab containers."""
    provider = ContainerlabProvider()

    mock_container1 = MagicMock()
    mock_container1.name = "clab-lab1-router1"
    mock_container1.status = "running"
    mock_container1.short_id = "abc"
    mock_container1.image.tags = ["test:latest"]
    mock_container1.attrs = {"NetworkSettings": {"Networks": {}}}

    mock_container2 = MagicMock()
    mock_container2.name = "clab-lab2-switch1"
    mock_container2.status = "running"
    mock_container2.short_id = "def"
    mock_container2.image.tags = ["test:latest"]
    mock_container2.attrs = {"NetworkSettings": {"Networks": {}}}

    # Set _docker directly since docker is a property
    mock_docker = MagicMock()
    mock_docker.containers.list.return_value = [mock_container1, mock_container2]
    provider._docker = mock_docker

    discovered = await provider.discover_labs()

    assert "lab1" in discovered
    assert "lab2" in discovered
    assert len(discovered["lab1"]) == 1
    assert len(discovered["lab2"]) == 1


@pytest.mark.asyncio
async def test_cleanup_orphan_containers():
    """Test that cleanup_orphan_containers removes orphans."""
    provider = ContainerlabProvider()

    # Orphan container (lab not in valid_lab_ids)
    orphan_container = MagicMock()
    orphan_container.name = "clab-deleteme-node1"

    # Valid container
    valid_container = MagicMock()
    valid_container.name = "clab-keepme-node1"

    # Set _docker directly since docker is a property
    mock_docker = MagicMock()
    mock_docker.containers.list.return_value = [orphan_container, valid_container]
    provider._docker = mock_docker

    removed = await provider.cleanup_orphan_containers({"keepme"})

    # Only orphan should be removed
    orphan_container.remove.assert_called_once_with(force=True)
    valid_container.remove.assert_not_called()
    assert "clab-deleteme-node1" in removed


# To run these tests:
# cd agent && pytest tests/test_containerlab_provider.py -v
