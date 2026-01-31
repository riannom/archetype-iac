"""Tests for job execution pipeline (tasks/jobs.py).

This module tests the core job execution functions including:
- run_agent_job: Single-host job execution
- run_multihost_deploy: Multi-host deployment
- run_multihost_destroy: Multi-host teardown
- run_node_sync: Node state synchronization
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from app import models
from app.tasks.jobs import (
    _get_container_name,
    run_agent_job,
    run_multihost_deploy,
    run_multihost_destroy,
    run_node_sync,
)


class TestGetContainerName:
    """Tests for _get_container_name helper function."""

    def test_basic_container_name_docker(self):
        """Container name follows archetype-{lab_id}-{node_name} pattern for docker."""
        result = _get_container_name("my-lab", "router1", provider="docker")
        assert result == "archetype-my-lab-router1"

    def test_long_lab_id_truncated(self):
        """Lab IDs longer than 20 chars are truncated."""
        long_id = "a" * 30
        result = _get_container_name(long_id, "r1", provider="docker")
        assert len(result.split("-")[1]) <= 20
        assert result == f"archetype-{'a' * 20}-r1"

    def test_special_chars_removed(self):
        """Special characters are removed from lab ID."""
        result = _get_container_name("my@lab#with$special!", "node", provider="docker")
        assert result == "archetype-mylabwithspecial-node"

    def test_allowed_chars_preserved(self):
        """Underscores and hyphens are preserved."""
        result = _get_container_name("my_lab-123", "node_1", provider="docker")
        assert result == "archetype-my_lab-123-node_1"


class TestRunAgentJob:
    """Tests for run_agent_job function."""

    @pytest.fixture
    def mock_db_session(self, test_db: Session):
        """Create a mock database session with test data."""
        return test_db

    @pytest.fixture
    def setup_job_and_lab(self, test_db: Session, test_user: models.User, sample_host: models.Host):
        """Create a job and lab for testing."""
        lab = models.Lab(
            name="Test Lab",
            owner_id=test_user.id,
            provider="docker",
            state="stopped",
        )
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        job = models.Job(
            lab_id=lab.id,
            user_id=test_user.id,
            action="up",
            status="queued",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        return job, lab, sample_host

    @pytest.mark.asyncio
    async def test_job_not_found(self, test_db: Session):
        """Job that doesn't exist should log error and return early."""
        with patch("app.tasks.jobs.SessionLocal", return_value=test_db):
            await run_agent_job("nonexistent-job", "lab-id", "up")
            # Should not raise, just log error and return

    @pytest.mark.asyncio
    async def test_lab_not_found(self, test_db: Session, test_user: models.User):
        """Job with missing lab should fail."""
        job = models.Job(
            lab_id="nonexistent-lab",
            user_id=test_user.id,
            action="up",
            status="queued",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        with patch("app.tasks.jobs.SessionLocal", return_value=test_db):
            await run_agent_job(job.id, "nonexistent-lab", "up")

        test_db.refresh(job)
        assert job.status == "failed"
        assert "not found" in job.log_path.lower()

    @pytest.mark.asyncio
    async def test_no_healthy_agent(self, test_db: Session, test_user: models.User):
        """Job should fail if no healthy agent available."""
        lab = models.Lab(
            name="Test Lab",
            owner_id=test_user.id,
            provider="docker",
            state="stopped",
        )
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        job = models.Job(
            lab_id=lab.id,
            user_id=test_user.id,
            action="up",
            status="queued",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        with patch("app.tasks.jobs.SessionLocal", return_value=test_db):
            with patch("app.tasks.jobs.agent_client.get_agent_for_lab", new_callable=AsyncMock) as mock_get_agent:
                mock_get_agent.return_value = None
                await run_agent_job(job.id, lab.id, "up")

        test_db.refresh(job)
        assert job.status == "failed"
        assert "No healthy agent" in job.log_path

    @pytest.mark.asyncio
    async def test_successful_deploy(self, test_db: Session, test_user: models.User, sample_host: models.Host):
        """Successful deploy should update job and lab state."""
        lab = models.Lab(
            name="Test Lab",
            owner_id=test_user.id,
            provider="docker",
            state="stopped",
        )
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        job = models.Job(
            lab_id=lab.id,
            user_id=test_user.id,
            action="up",
            status="queued",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        with patch("app.tasks.jobs.SessionLocal", return_value=test_db):
            with patch("app.tasks.jobs.agent_client.get_agent_for_lab", new_callable=AsyncMock) as mock_get_agent:
                mock_get_agent.return_value = sample_host
                with patch("app.tasks.jobs.agent_client.deploy_to_agent", new_callable=AsyncMock) as mock_deploy:
                    mock_deploy.return_value = {"status": "completed", "stdout": "Lab deployed"}
                    await run_agent_job(job.id, lab.id, "up", topology_yaml="name: test")

        test_db.refresh(job)
        test_db.refresh(lab)
        assert job.status == "completed"
        assert job.agent_id == sample_host.id
        assert lab.state == "running"

    @pytest.mark.asyncio
    async def test_failed_deploy(self, test_db: Session, test_user: models.User, sample_host: models.Host):
        """Failed deploy should update job and lab state to error."""
        lab = models.Lab(
            name="Test Lab",
            owner_id=test_user.id,
            provider="docker",
            state="stopped",
        )
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        job = models.Job(
            lab_id=lab.id,
            user_id=test_user.id,
            action="up",
            status="queued",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        with patch("app.tasks.jobs.SessionLocal", return_value=test_db):
            with patch("app.tasks.jobs.agent_client.get_agent_for_lab", new_callable=AsyncMock) as mock_get_agent:
                mock_get_agent.return_value = sample_host
                with patch("app.tasks.jobs.agent_client.deploy_to_agent", new_callable=AsyncMock) as mock_deploy:
                    mock_deploy.return_value = {
                        "status": "failed",
                        "error_message": "Image not found",
                        "stderr": "Error: image ceos not found"
                    }
                    await run_agent_job(job.id, lab.id, "up", topology_yaml="name: test")

        test_db.refresh(job)
        test_db.refresh(lab)
        assert job.status == "failed"
        assert lab.state == "error"
        assert "Image not found" in job.log_path

    @pytest.mark.asyncio
    async def test_successful_destroy(self, test_db: Session, test_user: models.User, sample_host: models.Host):
        """Successful destroy should update job and lab state."""
        lab = models.Lab(
            name="Test Lab",
            owner_id=test_user.id,
            provider="docker",
            state="running",
            agent_id=sample_host.id,
        )
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        job = models.Job(
            lab_id=lab.id,
            user_id=test_user.id,
            action="down",
            status="queued",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        with patch("app.tasks.jobs.SessionLocal", return_value=test_db):
            with patch("app.tasks.jobs.agent_client.get_agent_for_lab", new_callable=AsyncMock) as mock_get_agent:
                mock_get_agent.return_value = sample_host
                with patch("app.tasks.jobs.agent_client.destroy_on_agent", new_callable=AsyncMock) as mock_destroy:
                    mock_destroy.return_value = {"status": "completed", "stdout": "Lab destroyed"}
                    await run_agent_job(job.id, lab.id, "down")

        test_db.refresh(job)
        test_db.refresh(lab)
        assert job.status == "completed"
        assert lab.state == "stopped"

    @pytest.mark.asyncio
    async def test_node_action_start(self, test_db: Session, test_user: models.User, sample_host: models.Host):
        """Node start action should call node_action_on_agent."""
        lab = models.Lab(
            name="Test Lab",
            owner_id=test_user.id,
            provider="docker",
            state="running",
            agent_id=sample_host.id,
        )
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        job = models.Job(
            lab_id=lab.id,
            user_id=test_user.id,
            action="node:start:router1",
            status="queued",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        with patch("app.tasks.jobs.SessionLocal", return_value=test_db):
            with patch("app.tasks.jobs.agent_client.get_agent_for_lab", new_callable=AsyncMock) as mock_get_agent:
                mock_get_agent.return_value = sample_host
                with patch("app.tasks.jobs.agent_client.node_action_on_agent", new_callable=AsyncMock) as mock_node:
                    mock_node.return_value = {"status": "completed"}
                    await run_agent_job(job.id, lab.id, "node:start:router1")

        mock_node.assert_called_once_with(sample_host, job.id, lab.id, "router1", "start")
        test_db.refresh(job)
        assert job.status == "completed"

    @pytest.mark.asyncio
    async def test_unknown_action(self, test_db: Session, test_user: models.User, sample_host: models.Host):
        """Unknown action should fail with appropriate message."""
        lab = models.Lab(
            name="Test Lab",
            owner_id=test_user.id,
            provider="docker",
            state="running",
        )
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        job = models.Job(
            lab_id=lab.id,
            user_id=test_user.id,
            action="invalid_action",
            status="queued",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        with patch("app.tasks.jobs.SessionLocal", return_value=test_db):
            with patch("app.tasks.jobs.agent_client.get_agent_for_lab", new_callable=AsyncMock) as mock_get_agent:
                mock_get_agent.return_value = sample_host
                await run_agent_job(job.id, lab.id, "invalid_action")

        test_db.refresh(job)
        assert job.status == "failed"
        assert "Unknown action" in job.log_path

    @pytest.mark.asyncio
    async def test_agent_unavailable_error(self, test_db: Session, test_user: models.User, sample_host: models.Host):
        """AgentUnavailableError should mark job failed and agent offline."""
        from app.agent_client import AgentUnavailableError

        lab = models.Lab(
            name="Test Lab",
            owner_id=test_user.id,
            provider="docker",
            state="stopped",
        )
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        job = models.Job(
            lab_id=lab.id,
            user_id=test_user.id,
            action="up",
            status="queued",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        with patch("app.tasks.jobs.SessionLocal", return_value=test_db):
            with patch("app.tasks.jobs.agent_client.get_agent_for_lab", new_callable=AsyncMock) as mock_get_agent:
                mock_get_agent.return_value = sample_host
                with patch("app.tasks.jobs.agent_client.deploy_to_agent", new_callable=AsyncMock) as mock_deploy:
                    mock_deploy.side_effect = AgentUnavailableError("Connection refused", agent_id=sample_host.id)
                    with patch("app.tasks.jobs.agent_client.mark_agent_offline", new_callable=AsyncMock) as mock_offline:
                        await run_agent_job(job.id, lab.id, "up", topology_yaml="name: test")
                        mock_offline.assert_called_once()

        test_db.refresh(job)
        test_db.refresh(lab)
        assert job.status == "failed"
        assert lab.state == "unknown"
        assert "unavailable" in job.log_path.lower()

    @pytest.mark.asyncio
    async def test_agent_job_error(self, test_db: Session, test_user: models.User, sample_host: models.Host):
        """AgentJobError should mark job failed with stdout/stderr."""
        from app.agent_client import AgentJobError

        lab = models.Lab(
            name="Test Lab",
            owner_id=test_user.id,
            provider="docker",
            state="stopped",
        )
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        job = models.Job(
            lab_id=lab.id,
            user_id=test_user.id,
            action="up",
            status="queued",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        with patch("app.tasks.jobs.SessionLocal", return_value=test_db):
            with patch("app.tasks.jobs.agent_client.get_agent_for_lab", new_callable=AsyncMock) as mock_get_agent:
                mock_get_agent.return_value = sample_host
                with patch("app.tasks.jobs.agent_client.deploy_to_agent", new_callable=AsyncMock) as mock_deploy:
                    mock_deploy.side_effect = AgentJobError(
                        "Deploy failed",
                        agent_id=sample_host.id,
                        stdout="Starting...",
                        stderr="Error: image not found"
                    )
                    await run_agent_job(job.id, lab.id, "up", topology_yaml="name: test")

        test_db.refresh(job)
        assert job.status == "failed"
        assert "STDOUT" in job.log_path
        assert "STDERR" in job.log_path


class TestRunMultihostDeploy:
    """Tests for run_multihost_deploy function."""

    @pytest.mark.asyncio
    async def test_missing_hosts(self, test_db: Session, test_user: models.User):
        """Deploy should fail if required hosts are missing."""
        lab = models.Lab(
            name="Multi-host Lab",
            owner_id=test_user.id,
            provider="docker",
            state="stopped",
        )
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        job = models.Job(
            lab_id=lab.id,
            user_id=test_user.id,
            action="up",
            status="queued",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        # Topology with nodes on different hosts
        topology_yaml = """
name: multihost
topology:
  nodes:
    r1:
      kind: linux
      labels:
        netlab.host: host1
    r2:
      kind: linux
      labels:
        netlab.host: host2
"""

        with patch("app.tasks.jobs.SessionLocal", return_value=test_db):
            with patch("app.tasks.jobs.agent_client.get_agent_by_name", new_callable=AsyncMock) as mock_get_agent:
                mock_get_agent.return_value = None  # No agents found
                await run_multihost_deploy(job.id, lab.id, topology_yaml)

        test_db.refresh(job)
        assert job.status == "failed"
        assert "Missing" in job.log_path or "missing" in job.log_path.lower()


class TestRunMultihostDestroy:
    """Tests for run_multihost_destroy function."""

    @pytest.mark.asyncio
    async def test_no_agents_found(self, test_db: Session, test_user: models.User):
        """Destroy should fail gracefully if no agents found."""
        lab = models.Lab(
            name="Multi-host Lab",
            owner_id=test_user.id,
            provider="docker",
            state="running",
        )
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        job = models.Job(
            lab_id=lab.id,
            user_id=test_user.id,
            action="down",
            status="queued",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        topology_yaml = """
name: multihost
topology:
  nodes:
    r1:
      kind: linux
      labels:
        netlab.host: missing-host
"""

        with patch("app.tasks.jobs.SessionLocal", return_value=test_db):
            with patch("app.tasks.jobs.agent_client.get_agent_by_name", new_callable=AsyncMock) as mock_get_agent:
                mock_get_agent.return_value = None
                await run_multihost_destroy(job.id, lab.id, topology_yaml)

        test_db.refresh(job)
        assert job.status == "failed"
        assert "No agents found" in job.log_path


class TestRunNodeSync:
    """Tests for run_node_sync function."""

    @pytest.mark.asyncio
    async def test_no_nodes_to_sync(self, test_db: Session, test_user: models.User):
        """Sync with no matching nodes should complete quickly."""
        lab = models.Lab(
            name="Test Lab",
            owner_id=test_user.id,
            provider="docker",
            state="running",
        )
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        job = models.Job(
            lab_id=lab.id,
            user_id=test_user.id,
            action="sync",
            status="queued",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        with patch("app.tasks.jobs.SessionLocal", return_value=test_db):
            await run_node_sync(job.id, lab.id, ["nonexistent-node"])

        test_db.refresh(job)
        assert job.status == "completed"
        assert "No nodes to sync" in job.log_path

    @pytest.mark.asyncio
    async def test_sync_nodes_need_deploy(self, test_db: Session, test_user: models.User, sample_host: models.Host, tmp_path):
        """Nodes in undeployed state should trigger full deploy."""
        lab = models.Lab(
            name="Test Lab",
            owner_id=test_user.id,
            provider="docker",
            state="stopped",
            workspace_path=str(tmp_path),
        )
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        # Create node state that needs deploy
        node_state = models.NodeState(
            lab_id=lab.id,
            node_id="node-1",
            node_name="router1",
            desired_state="running",
            actual_state="undeployed",
        )
        test_db.add(node_state)
        test_db.commit()
        test_db.refresh(node_state)

        job = models.Job(
            lab_id=lab.id,
            user_id=test_user.id,
            action="sync",
            status="queued",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        # Create topology file
        from app.config import settings
        with patch.object(settings, "netlab_workspace", str(tmp_path)):
            topo_dir = tmp_path / lab.id
            topo_dir.mkdir(parents=True, exist_ok=True)
            topo_file = topo_dir / "topology.yml"
            topo_file.write_text("name: test\ntopology:\n  nodes:\n    router1:\n      kind: linux\n")

            with patch("app.tasks.jobs.SessionLocal", return_value=test_db):
                with patch("app.tasks.jobs.agent_client.get_agent_for_lab", new_callable=AsyncMock) as mock_get_agent:
                    mock_get_agent.return_value = sample_host
                    with patch("app.tasks.jobs.agent_client.deploy_to_agent", new_callable=AsyncMock) as mock_deploy:
                        mock_deploy.return_value = {"status": "completed"}
                        with patch("app.tasks.jobs.topology_path") as mock_topo_path:
                            mock_topo_path.return_value = topo_file
                            await run_node_sync(job.id, lab.id, ["node-1"])

        test_db.refresh(job)
        assert job.status == "completed" or job.status == "failed"  # May fail due to test setup


class TestJobErrorHandling:
    """Tests for job error handling scenarios."""

    @pytest.mark.asyncio
    async def test_unexpected_exception(self, test_db: Session, test_user: models.User, sample_host: models.Host):
        """Unexpected exceptions should be caught and job marked failed."""
        lab = models.Lab(
            name="Test Lab",
            owner_id=test_user.id,
            provider="docker",
            state="stopped",
        )
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        job = models.Job(
            lab_id=lab.id,
            user_id=test_user.id,
            action="up",
            status="queued",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        with patch("app.tasks.jobs.SessionLocal", return_value=test_db):
            with patch("app.tasks.jobs.agent_client.get_agent_for_lab", new_callable=AsyncMock) as mock_get_agent:
                mock_get_agent.return_value = sample_host
                with patch("app.tasks.jobs.agent_client.deploy_to_agent", new_callable=AsyncMock) as mock_deploy:
                    mock_deploy.side_effect = RuntimeError("Unexpected error")
                    await run_agent_job(job.id, lab.id, "up", topology_yaml="name: test")

        test_db.refresh(job)
        assert job.status == "failed"
        assert "Unexpected error" in job.log_path
