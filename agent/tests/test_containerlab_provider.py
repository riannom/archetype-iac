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

    with patch.object(provider, '_pre_deploy_cleanup', new_callable=AsyncMock) as mock_cleanup:
        mock_cleanup.return_value = []

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
                    # Topology is wrapped in containerlab format with lab_id as name
                    content = topo_path.read_text()
                    assert "name: test123" in content
                    assert "topology:" in content

                finally:
                    if workspace.exists():
                        import shutil
                        shutil.rmtree(workspace)


@pytest.mark.asyncio
async def test_deploy_failure_triggers_cleanup():
    """Test that failed deploy triggers cleanup.

    With the new fallback logic:
    1. First attempt with --reconfigure fails
    2. _cleanup_failed_deploy is called
    3. Second attempt (fresh deploy) also fails
    4. _cleanup_failed_deploy is called again
    """
    provider = ContainerlabProvider()
    workspace = Path("/tmp/test-workspace-cleanup")

    with patch.object(provider, '_pre_deploy_cleanup', new_callable=AsyncMock) as mock_pre_cleanup:
        mock_pre_cleanup.return_value = []

        with patch.object(provider, '_run_clab', new_callable=AsyncMock) as mock_clab:
            # Both deploy attempts fail
            mock_clab.side_effect = [
                (1, "", "Error: reconfigure failed"),
                (1, "", "Error: fresh deploy failed"),
            ]

            with patch.object(provider, '_cleanup_failed_deploy', new_callable=AsyncMock) as mock_cleanup:
                try:
                    result = await provider.deploy(
                        lab_id="test123",
                        topology_yaml="name: test\n",
                        workspace=workspace,
                    )

                    assert not result.success
                    # Cleanup should be called twice (after each failed attempt)
                    assert mock_cleanup.call_count == 2

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


# --- Tests for Subprocess Timeout ---

@pytest.mark.asyncio
async def test_run_clab_timeout_kills_process():
    """Test that _run_clab kills the process when timeout is exceeded."""
    provider = ContainerlabProvider()
    workspace = Path("/tmp")

    # Create a mock process that will "hang"
    mock_process = AsyncMock()
    mock_process.returncode = None
    mock_process.kill = MagicMock()
    mock_process.wait = AsyncMock()

    # communicate() will timeout
    async def hang_forever():
        await asyncio.sleep(10)  # Simulate hanging
        return (b"", b"")

    mock_process.communicate = hang_forever

    with patch('asyncio.create_subprocess_exec', new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = mock_process

        with pytest.raises(TimeoutError) as exc_info:
            await provider._run_clab(["deploy", "-t", "test.yml"], workspace, timeout=0.1)

        assert "timed out after 0.1s" in str(exc_info.value)
        mock_process.kill.assert_called_once()
        mock_process.wait.assert_called_once()


@pytest.mark.asyncio
async def test_run_clab_success_within_timeout():
    """Test that _run_clab completes successfully within timeout."""
    provider = ContainerlabProvider()
    workspace = Path("/tmp")

    mock_process = AsyncMock()
    mock_process.returncode = 0
    mock_process.communicate = AsyncMock(return_value=(b"Success", b""))

    with patch('asyncio.create_subprocess_exec', new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = mock_process

        returncode, stdout, stderr = await provider._run_clab(
            ["deploy", "-t", "test.yml"],
            workspace,
            timeout=10
        )

        assert returncode == 0
        assert stdout == "Success"
        assert stderr == ""


@pytest.mark.asyncio
async def test_run_clab_uses_default_timeout():
    """Test that _run_clab uses settings.deploy_timeout when no timeout specified."""
    provider = ContainerlabProvider()
    workspace = Path("/tmp")

    mock_process = AsyncMock()
    mock_process.returncode = 0
    mock_process.communicate = AsyncMock(return_value=(b"OK", b""))

    with patch('asyncio.create_subprocess_exec', new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = mock_process

        with patch('asyncio.wait_for', new_callable=AsyncMock) as mock_wait_for:
            mock_wait_for.return_value = (b"OK", b"")

            from agent.config import settings
            await provider._run_clab(["deploy"], workspace)

            # Verify wait_for was called with the default timeout
            mock_wait_for.assert_called_once()
            call_args = mock_wait_for.call_args
            assert call_args.kwargs.get('timeout') == settings.deploy_timeout


# --- Tests for Pre-Deploy Cleanup ---

@pytest.mark.asyncio
async def test_pre_deploy_cleanup_removes_exited_containers():
    """Test that pre-deploy cleanup removes exited containers."""
    provider = ContainerlabProvider()

    exited_container = MagicMock()
    exited_container.name = "clab-test123-node1"
    exited_container.status = "exited"

    running_container = MagicMock()
    running_container.name = "clab-test123-node2"
    running_container.status = "running"

    mock_docker = MagicMock()
    mock_docker.containers.list.return_value = [exited_container, running_container]
    provider._docker = mock_docker

    removed = await provider._pre_deploy_cleanup("test123", Path("/tmp"))

    # Only exited container should be removed
    exited_container.remove.assert_called_once_with(force=True)
    running_container.remove.assert_not_called()
    assert "clab-test123-node1" in removed


@pytest.mark.asyncio
async def test_pre_deploy_cleanup_removes_dead_containers():
    """Test that pre-deploy cleanup removes dead containers."""
    provider = ContainerlabProvider()

    dead_container = MagicMock()
    dead_container.name = "clab-test123-dead"
    dead_container.status = "dead"

    mock_docker = MagicMock()
    mock_docker.containers.list.return_value = [dead_container]
    provider._docker = mock_docker

    removed = await provider._pre_deploy_cleanup("test123", Path("/tmp"))

    dead_container.remove.assert_called_once_with(force=True)
    assert len(removed) == 1


@pytest.mark.asyncio
async def test_pre_deploy_cleanup_removes_created_containers():
    """Test that pre-deploy cleanup removes created (never started) containers."""
    provider = ContainerlabProvider()

    created_container = MagicMock()
    created_container.name = "clab-test123-stuck"
    created_container.status = "created"

    mock_docker = MagicMock()
    mock_docker.containers.list.return_value = [created_container]
    provider._docker = mock_docker

    removed = await provider._pre_deploy_cleanup("test123", Path("/tmp"))

    created_container.remove.assert_called_once_with(force=True)
    assert "clab-test123-stuck" in removed


@pytest.mark.asyncio
async def test_pre_deploy_cleanup_keeps_running_containers():
    """Test that pre-deploy cleanup preserves running containers."""
    provider = ContainerlabProvider()

    running_container = MagicMock()
    running_container.name = "clab-test123-healthy"
    running_container.status = "running"

    mock_docker = MagicMock()
    mock_docker.containers.list.return_value = [running_container]
    provider._docker = mock_docker

    removed = await provider._pre_deploy_cleanup("test123", Path("/tmp"))

    running_container.remove.assert_not_called()
    assert len(removed) == 0


@pytest.mark.asyncio
async def test_pre_deploy_cleanup_handles_remove_failure():
    """Test that pre-deploy cleanup continues when remove fails."""
    provider = ContainerlabProvider()

    container1 = MagicMock()
    container1.name = "clab-test123-node1"
    container1.status = "exited"
    container1.remove.side_effect = Exception("Remove failed")

    container2 = MagicMock()
    container2.name = "clab-test123-node2"
    container2.status = "dead"

    mock_docker = MagicMock()
    mock_docker.containers.list.return_value = [container1, container2]
    provider._docker = mock_docker

    removed = await provider._pre_deploy_cleanup("test123", Path("/tmp"))

    # Both should be attempted
    container1.remove.assert_called_once()
    container2.remove.assert_called_once()
    # Only container2 succeeds
    assert "clab-test123-node2" in removed
    assert "clab-test123-node1" not in removed


# --- Tests for Deploy Fallback Logic ---

@pytest.mark.asyncio
async def test_deploy_calls_pre_deploy_cleanup():
    """Test that deploy calls pre-deploy cleanup before deploying."""
    provider = ContainerlabProvider()
    workspace = Path("/tmp/test-predeploy-cleanup")

    with patch.object(provider, '_pre_deploy_cleanup', new_callable=AsyncMock) as mock_cleanup:
        mock_cleanup.return_value = []

        with patch.object(provider, '_run_clab', new_callable=AsyncMock) as mock_clab:
            mock_clab.return_value = (0, "Deployed", "")

            with patch.object(provider, 'status', new_callable=AsyncMock) as mock_status:
                from agent.providers.base import StatusResult
                mock_status.return_value = StatusResult(lab_exists=True, nodes=[])

                try:
                    await provider.deploy(
                        lab_id="test123",
                        topology_yaml="name: test\n",
                        workspace=workspace,
                    )

                    mock_cleanup.assert_called_once_with("test123", workspace)

                finally:
                    if workspace.exists():
                        import shutil
                        shutil.rmtree(workspace)


@pytest.mark.asyncio
async def test_deploy_fallback_on_reconfigure_failure():
    """Test that deploy falls back to fresh deploy when --reconfigure fails."""
    provider = ContainerlabProvider()
    workspace = Path("/tmp/test-fallback")

    with patch.object(provider, '_pre_deploy_cleanup', new_callable=AsyncMock) as mock_pre_cleanup:
        mock_pre_cleanup.return_value = []

        with patch.object(provider, '_cleanup_failed_deploy', new_callable=AsyncMock) as mock_cleanup:
            with patch.object(provider, '_run_clab', new_callable=AsyncMock) as mock_clab:
                # First call (--reconfigure) fails, second call (fresh deploy) succeeds
                mock_clab.side_effect = [
                    (1, "", "Error: reconfigure failed"),
                    (0, "Fresh deploy succeeded", ""),
                ]

                with patch.object(provider, 'status', new_callable=AsyncMock) as mock_status:
                    from agent.providers.base import StatusResult
                    mock_status.return_value = StatusResult(lab_exists=True, nodes=[])

                    try:
                        result = await provider.deploy(
                            lab_id="test123",
                            topology_yaml="name: test\n",
                            workspace=workspace,
                        )

                        # Should succeed via fallback
                        assert result.success

                        # Verify both deploy attempts were made
                        assert mock_clab.call_count == 2

                        # First call should have --reconfigure
                        first_call_args = mock_clab.call_args_list[0][0][0]
                        assert "--reconfigure" in first_call_args

                        # Second call should NOT have --reconfigure
                        second_call_args = mock_clab.call_args_list[1][0][0]
                        assert "--reconfigure" not in second_call_args

                        # Cleanup should be called before fresh deploy
                        mock_cleanup.assert_called()

                    finally:
                        if workspace.exists():
                            import shutil
                            shutil.rmtree(workspace)


@pytest.mark.asyncio
async def test_deploy_fails_when_both_attempts_fail():
    """Test that deploy fails when both --reconfigure and fresh deploy fail."""
    provider = ContainerlabProvider()
    workspace = Path("/tmp/test-both-fail")

    with patch.object(provider, '_pre_deploy_cleanup', new_callable=AsyncMock) as mock_pre_cleanup:
        mock_pre_cleanup.return_value = []

        with patch.object(provider, '_cleanup_failed_deploy', new_callable=AsyncMock):
            with patch.object(provider, '_run_clab', new_callable=AsyncMock) as mock_clab:
                # Both attempts fail
                mock_clab.side_effect = [
                    (1, "", "Error: reconfigure failed"),
                    (1, "", "Error: fresh deploy also failed"),
                ]

                try:
                    result = await provider.deploy(
                        lab_id="test123",
                        topology_yaml="name: test\n",
                        workspace=workspace,
                    )

                    assert not result.success
                    assert "failed with exit code" in result.error

                finally:
                    if workspace.exists():
                        import shutil
                        shutil.rmtree(workspace)


@pytest.mark.asyncio
async def test_deploy_handles_timeout_during_reconfigure():
    """Test that deploy handles timeout during --reconfigure attempt."""
    provider = ContainerlabProvider()
    workspace = Path("/tmp/test-timeout-reconfigure")

    with patch.object(provider, '_pre_deploy_cleanup', new_callable=AsyncMock) as mock_pre_cleanup:
        mock_pre_cleanup.return_value = []

        with patch.object(provider, '_cleanup_failed_deploy', new_callable=AsyncMock) as mock_cleanup:
            with patch.object(provider, '_run_clab', new_callable=AsyncMock) as mock_clab:
                # First call times out
                mock_clab.side_effect = TimeoutError("timed out after 900s")

                try:
                    result = await provider.deploy(
                        lab_id="test123",
                        topology_yaml="name: test\n",
                        workspace=workspace,
                    )

                    assert not result.success
                    assert "timed out" in result.error
                    mock_cleanup.assert_called_once()

                finally:
                    if workspace.exists():
                        import shutil
                        shutil.rmtree(workspace)


@pytest.mark.asyncio
async def test_deploy_handles_timeout_during_fresh_deploy():
    """Test that deploy handles timeout during fresh deploy fallback."""
    provider = ContainerlabProvider()
    workspace = Path("/tmp/test-timeout-fresh")

    with patch.object(provider, '_pre_deploy_cleanup', new_callable=AsyncMock) as mock_pre_cleanup:
        mock_pre_cleanup.return_value = []

        with patch.object(provider, '_cleanup_failed_deploy', new_callable=AsyncMock) as mock_cleanup:
            with patch.object(provider, '_run_clab', new_callable=AsyncMock) as mock_clab:
                # First call fails, second call times out
                mock_clab.side_effect = [
                    (1, "", "Error: reconfigure failed"),
                    TimeoutError("timed out after 900s"),
                ]

                try:
                    result = await provider.deploy(
                        lab_id="test123",
                        topology_yaml="name: test\n",
                        workspace=workspace,
                    )

                    assert not result.success
                    assert "timed out" in result.error
                    # Cleanup should be called twice (after reconfigure fail + after timeout)
                    assert mock_cleanup.call_count == 2

                finally:
                    if workspace.exists():
                        import shutil
                        shutil.rmtree(workspace)


# --- Tests for cEOS Flash Directory Creation ---

def test_ensure_ceos_flash_dirs_creates_directories():
    """Test that flash directories are created for cEOS nodes."""
    provider = ContainerlabProvider()
    workspace = Path("/tmp/test-ceos-flash-dirs")

    topology_yaml = """
name: test-lab
topology:
  nodes:
    router1:
      kind: ceos
      image: ceos:latest
    switch1:
      kind: ceos
      image: ceos:latest
    host1:
      kind: linux
      image: alpine:latest
"""

    try:
        workspace.mkdir(parents=True, exist_ok=True)
        provider._ensure_ceos_flash_dirs(topology_yaml, workspace)

        # Flash directories should exist for cEOS nodes
        assert (workspace / "configs" / "router1" / "flash").exists()
        assert (workspace / "configs" / "switch1" / "flash").exists()

        # No flash directory for linux node
        assert not (workspace / "configs" / "host1" / "flash").exists()

    finally:
        import shutil
        if workspace.exists():
            shutil.rmtree(workspace)


def test_ensure_ceos_flash_dirs_no_ceos_nodes():
    """Test that no directories are created when there are no cEOS nodes."""
    provider = ContainerlabProvider()
    workspace = Path("/tmp/test-no-ceos-flash")

    topology_yaml = """
name: test-lab
topology:
  nodes:
    host1:
      kind: linux
    router1:
      kind: srlinux
"""

    try:
        workspace.mkdir(parents=True, exist_ok=True)
        provider._ensure_ceos_flash_dirs(topology_yaml, workspace)

        # configs directory should not exist (no cEOS nodes)
        assert not (workspace / "configs").exists()

    finally:
        import shutil
        if workspace.exists():
            shutil.rmtree(workspace)


def test_ensure_ceos_flash_dirs_flat_topology():
    """Test flash directory creation with flat (non-wrapped) topology format."""
    provider = ContainerlabProvider()
    workspace = Path("/tmp/test-flat-topo-flash")

    # Flat format (not wrapped in 'topology' key)
    topology_yaml = """
nodes:
  ceos1:
    kind: ceos
  linux1:
    kind: linux
"""

    try:
        workspace.mkdir(parents=True, exist_ok=True)
        provider._ensure_ceos_flash_dirs(topology_yaml, workspace)

        # Flash directory should exist for cEOS node
        assert (workspace / "configs" / "ceos1" / "flash").exists()

        # No flash directory for linux node
        assert not (workspace / "configs" / "linux1" / "flash").exists()

    finally:
        import shutil
        if workspace.exists():
            shutil.rmtree(workspace)


def test_ensure_ceos_flash_dirs_idempotent():
    """Test that calling _ensure_ceos_flash_dirs multiple times is safe."""
    provider = ContainerlabProvider()
    workspace = Path("/tmp/test-idempotent-flash")

    topology_yaml = """
name: test
topology:
  nodes:
    ceos1:
      kind: ceos
"""

    try:
        workspace.mkdir(parents=True, exist_ok=True)

        # Call multiple times
        provider._ensure_ceos_flash_dirs(topology_yaml, workspace)
        provider._ensure_ceos_flash_dirs(topology_yaml, workspace)
        provider._ensure_ceos_flash_dirs(topology_yaml, workspace)

        # Directory should exist
        assert (workspace / "configs" / "ceos1" / "flash").exists()

    finally:
        import shutil
        if workspace.exists():
            shutil.rmtree(workspace)


def test_ensure_ceos_flash_dirs_handles_invalid_yaml():
    """Test that invalid YAML doesn't cause errors."""
    provider = ContainerlabProvider()
    workspace = Path("/tmp/test-invalid-yaml-flash")

    try:
        workspace.mkdir(parents=True, exist_ok=True)

        # Invalid YAML - should not raise, just log warning
        provider._ensure_ceos_flash_dirs("not: valid: yaml: {{{", workspace)

        # Also test empty YAML
        provider._ensure_ceos_flash_dirs("", workspace)
        provider._ensure_ceos_flash_dirs(None, workspace)

    finally:
        import shutil
        if workspace.exists():
            shutil.rmtree(workspace)


@pytest.mark.asyncio
async def test_deploy_creates_ceos_flash_dirs():
    """Test that deploy creates flash directories for cEOS nodes."""
    provider = ContainerlabProvider()
    workspace = Path("/tmp/test-deploy-ceos-flash")

    topology_yaml = """
name: test
topology:
  nodes:
    ceos1:
      kind: ceos
      image: ceos:latest
"""

    with patch.object(provider, '_pre_deploy_cleanup', new_callable=AsyncMock) as mock_cleanup:
        mock_cleanup.return_value = []

        with patch.object(provider, '_run_clab', new_callable=AsyncMock) as mock_clab:
            mock_clab.return_value = (0, "Deployed", "")

            with patch.object(provider, 'status', new_callable=AsyncMock) as mock_status:
                from agent.providers.base import StatusResult
                mock_status.return_value = StatusResult(lab_exists=True, nodes=[])

                try:
                    await provider.deploy(
                        lab_id="test123",
                        topology_yaml=topology_yaml,
                        workspace=workspace,
                    )

                    # Flash directory should exist for cEOS node
                    assert (workspace / "configs" / "ceos1" / "flash").exists()

                finally:
                    import shutil
                    if workspace.exists():
                        shutil.rmtree(workspace)


# To run these tests:
# cd agent && pytest tests/test_containerlab_provider.py -v
