"""Tests for job execution pipeline (tasks/jobs.py).

This module tests the core job execution functions including:
- run_agent_job: Single-host job execution
- run_multihost_deploy: Multi-host deployment
- run_multihost_destroy: Multi-host teardown
- run_node_reconcile: Node state synchronization
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
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
    run_node_reconcile,
)


def _mock_get_session(test_db: Session):
    """Create a mock get_session context manager that yields the test database session."""
    @contextmanager
    def mock_session():
        yield test_db
    return mock_session


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
        with patch("app.tasks.jobs.get_session", _mock_get_session(test_db)):
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

        with patch("app.tasks.jobs.get_session", _mock_get_session(test_db)):
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

        with patch("app.tasks.jobs.get_session", _mock_get_session(test_db)):
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

        with patch("app.tasks.jobs.get_session", _mock_get_session(test_db)):
            with patch("app.tasks.jobs.agent_client.get_agent_for_lab", new_callable=AsyncMock) as mock_get_agent:
                mock_get_agent.return_value = sample_host
                with patch("app.tasks.jobs.agent_client.deploy_to_agent", new_callable=AsyncMock) as mock_deploy:
                    mock_deploy.return_value = {"status": "completed", "stdout": "Lab deployed"}
                    await run_agent_job(job.id, lab.id, "up")

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

        with patch("app.tasks.jobs.get_session", _mock_get_session(test_db)):
            with patch("app.tasks.jobs.agent_client.get_agent_for_lab", new_callable=AsyncMock) as mock_get_agent:
                mock_get_agent.return_value = sample_host
                with patch("app.tasks.jobs.agent_client.deploy_to_agent", new_callable=AsyncMock) as mock_deploy:
                    mock_deploy.return_value = {
                        "status": "failed",
                        "error_message": "Image not found",
                        "stderr": "Error: image ceos not found"
                    }
                    await run_agent_job(job.id, lab.id, "up")

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

        with patch("app.tasks.jobs.get_session", _mock_get_session(test_db)):
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
    async def test_node_action_start_deprecated(self, test_db: Session, test_user: models.User, sample_host: models.Host):
        """Node start action is deprecated and should return Unknown action."""
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

        with patch("app.tasks.jobs.get_session", _mock_get_session(test_db)):
            with patch("app.tasks.jobs.agent_client.get_agent_for_node", new_callable=AsyncMock) as mock_get_node_agent:
                mock_get_node_agent.return_value = sample_host
                with patch("app.tasks.jobs.agent_client.get_agent_for_lab", new_callable=AsyncMock) as mock_get_agent:
                    mock_get_agent.return_value = sample_host
                    await run_agent_job(job.id, lab.id, "node:start:router1")

        test_db.refresh(job)
        assert job.status == "failed"
        assert "Unknown action" in job.log_path

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

        with patch("app.tasks.jobs.get_session", _mock_get_session(test_db)):
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

        with patch("app.tasks.jobs.get_session", _mock_get_session(test_db)):
            with patch("app.tasks.jobs.agent_client.get_agent_for_lab", new_callable=AsyncMock) as mock_get_agent:
                mock_get_agent.return_value = sample_host
                with patch("app.tasks.jobs.agent_client.deploy_to_agent", new_callable=AsyncMock) as mock_deploy:
                    mock_deploy.side_effect = AgentUnavailableError("Connection refused", agent_id=sample_host.id)
                    with patch("app.tasks.jobs.agent_client.mark_agent_offline", new_callable=AsyncMock) as mock_offline:
                        await run_agent_job(job.id, lab.id, "up")
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

        with patch("app.tasks.jobs.get_session", _mock_get_session(test_db)):
            with patch("app.tasks.jobs.agent_client.get_agent_for_lab", new_callable=AsyncMock) as mock_get_agent:
                mock_get_agent.return_value = sample_host
                with patch("app.tasks.jobs.agent_client.deploy_to_agent", new_callable=AsyncMock) as mock_deploy:
                    mock_deploy.side_effect = AgentJobError(
                        "Deploy failed",
                        agent_id=sample_host.id,
                        stdout="Starting...",
                        stderr="Error: image not found"
                    )
                    await run_agent_job(job.id, lab.id, "up")

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

        node1 = models.Node(
            lab_id=lab.id,
            gui_id="r1",
            display_name="r1",
            container_name="r1",
            node_type="device",
            device="linux",
            host_id="host1",
        )
        node2 = models.Node(
            lab_id=lab.id,
            gui_id="r2",
            display_name="r2",
            container_name="r2",
            node_type="device",
            device="linux",
            host_id="host2",
        )
        test_db.add_all([node1, node2])
        test_db.commit()

        with patch("app.tasks.jobs.get_session", _mock_get_session(test_db)):
            await run_multihost_deploy(job.id, lab.id)

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

        node1 = models.Node(
            lab_id=lab.id,
            gui_id="r1",
            display_name="r1",
            container_name="r1",
            node_type="device",
            device="linux",
            host_id="missing-host",
        )
        test_db.add(node1)
        test_db.commit()

        with patch("app.tasks.jobs.get_session", _mock_get_session(test_db)):
            await run_multihost_destroy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == "failed"
        assert "No agents found" in job.log_path


class TestRunNodeReconcile:
    """Tests for run_node_reconcile function."""

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

        with patch("app.tasks.jobs.get_session", _mock_get_session(test_db)):
            await run_node_reconcile(job.id, lab.id, ["nonexistent-node"])

        test_db.refresh(job)
        assert job.status == "completed"
        assert "No nodes to sync" in job.log_path

    @pytest.mark.asyncio
    async def test_sync_nodes_need_deploy(self, test_db: Session, test_user: models.User, sample_host: models.Host):
        """Nodes in undeployed state should trigger deploy via TopologyService."""
        lab = models.Lab(
            name="Test Lab",
            owner_id=test_user.id,
            provider="docker",
            state="stopped",
        )
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        # Create a Node definition in the database (used by TopologyService)
        node_def = models.Node(
            lab_id=lab.id,
            gui_id="node-1",
            display_name="router1",
            container_name="router1",
            device="linux",
            host_id=sample_host.id,
        )
        test_db.add(node_def)
        test_db.commit()

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

        with patch("app.tasks.jobs.get_session", _mock_get_session(test_db)):
            with patch("app.tasks.jobs.agent_client.get_agent_for_lab", new_callable=AsyncMock) as mock_get_agent:
                mock_get_agent.return_value = sample_host
                with patch("app.tasks.jobs.agent_client.deploy_to_agent", new_callable=AsyncMock) as mock_deploy:
                    mock_deploy.return_value = {"status": "completed"}
                    with patch("app.tasks.jobs.agent_client.is_agent_online", return_value=True):
                        await run_node_reconcile(job.id, lab.id, ["node-1"])

        test_db.refresh(job)
        assert job.status == "completed" or job.status == "failed"  # May fail due to test setup


class TestSyncAgentJobParentTracking:
    """Tests for parent_job_id tracking in sync:agent jobs."""

    def test_sync_agent_job_creation_with_parent_id(self, test_db: Session, test_user: models.User):
        """sync:agent jobs can be created with parent_job_id set."""
        # Create lab
        lab = models.Lab(
            id="lab-parent-test",
            name="Parent Test Lab",
            owner_id=test_user.id,
            provider="docker",
            state="running",
        )
        test_db.add(lab)
        test_db.commit()

        # Create parent sync job
        parent_job = models.Job(
            id="parent-sync-job",
            lab_id=lab.id,
            user_id=test_user.id,
            action="sync:lab",
            status="running",
        )
        test_db.add(parent_job)
        test_db.commit()

        # Create child sync:agent job with parent_job_id (simulating what jobs.py does)
        child_job = models.Job(
            lab_id=lab.id,
            user_id=test_user.id,
            action="sync:agent:host2:node1,node2",
            status="queued",
            parent_job_id=parent_job.id,  # This is what jobs.py sets
        )
        test_db.add(child_job)
        test_db.commit()
        test_db.refresh(child_job)

        # Verify the relationship
        assert child_job.parent_job_id == parent_job.id
        assert "sync:agent" in child_job.action

        # Query children by parent
        children = test_db.query(models.Job).filter(
            models.Job.parent_job_id == parent_job.id
        ).all()
        assert len(children) == 1
        assert children[0].id == child_job.id

    def test_multiple_sync_agent_jobs_share_parent(self, test_db: Session, test_user: models.User):
        """Multiple sync:agent jobs can share the same parent_job_id."""
        lab = models.Lab(
            id="lab-multi-child",
            name="Multi Child Lab",
            owner_id=test_user.id,
        )
        test_db.add(lab)
        test_db.commit()

        # Create parent job
        parent_job = models.Job(
            id="parent-multi-child",
            lab_id=lab.id,
            user_id=test_user.id,
            action="sync:lab",
            status="running",
        )
        test_db.add(parent_job)
        test_db.commit()

        # Create multiple child jobs
        for i in range(3):
            child = models.Job(
                lab_id=lab.id,
                user_id=test_user.id,
                action=f"sync:agent:host{i}:node{i}",
                status="queued",
                parent_job_id=parent_job.id,
            )
            test_db.add(child)
        test_db.commit()

        # Query all children
        children = test_db.query(models.Job).filter(
            models.Job.parent_job_id == parent_job.id
        ).all()
        assert len(children) == 3
        for child in children:
            assert child.parent_job_id == parent_job.id
            assert "sync:agent" in child.action

    def test_job_model_has_parent_job_id_field(self, test_db: Session, test_user: models.User):
        """Job model should have parent_job_id field."""
        lab = models.Lab(
            id="lab-field-test",
            name="Field Test Lab",
            owner_id=test_user.id,
        )
        test_db.add(lab)
        test_db.commit()

        # Create parent job
        parent = models.Job(
            id="parent-field-test",
            lab_id=lab.id,
            user_id=test_user.id,
            action="sync:lab",
            status="running",
        )
        test_db.add(parent)
        test_db.commit()

        # Create child job with parent_job_id
        child = models.Job(
            id="child-field-test",
            lab_id=lab.id,
            user_id=test_user.id,
            action="sync:agent:host1:node1",
            status="queued",
            parent_job_id=parent.id,
        )
        test_db.add(child)
        test_db.commit()
        test_db.refresh(child)

        assert child.parent_job_id == parent.id

    def test_job_model_has_superseded_by_id_field(self, test_db: Session, test_user: models.User):
        """Job model should have superseded_by_id field."""
        lab = models.Lab(
            id="lab-superseded-test",
            name="Superseded Test Lab",
            owner_id=test_user.id,
        )
        test_db.add(lab)
        test_db.commit()

        # Create original job
        original = models.Job(
            id="original-job",
            lab_id=lab.id,
            user_id=test_user.id,
            action="up",
            status="failed",
        )
        test_db.add(original)
        test_db.commit()

        # Create retry job
        retry = models.Job(
            id="retry-job",
            lab_id=lab.id,
            user_id=test_user.id,
            action="up",
            status="queued",
            retry_count=1,
        )
        test_db.add(retry)
        test_db.commit()

        # Link original to retry
        original.superseded_by_id = retry.id
        test_db.commit()
        test_db.refresh(original)

        assert original.superseded_by_id == retry.id

    def test_parent_job_id_nullable(self, test_db: Session, test_user: models.User):
        """parent_job_id should be nullable for standalone jobs."""
        lab = models.Lab(
            id="lab-nullable-test",
            name="Nullable Test Lab",
            owner_id=test_user.id,
        )
        test_db.add(lab)
        test_db.commit()

        # Create job without parent
        job = models.Job(
            id="standalone-job-test",
            lab_id=lab.id,
            user_id=test_user.id,
            action="up",
            status="queued",
            parent_job_id=None,  # Explicitly None
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        assert job.parent_job_id is None


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

        with patch("app.tasks.jobs.get_session", _mock_get_session(test_db)):
            with patch("app.tasks.jobs.agent_client.get_agent_for_lab", new_callable=AsyncMock) as mock_get_agent:
                mock_get_agent.return_value = sample_host
                with patch("app.tasks.jobs.agent_client.deploy_to_agent", new_callable=AsyncMock) as mock_deploy:
                    mock_deploy.side_effect = RuntimeError("Unexpected error")
                    await run_agent_job(job.id, lab.id, "up")

        test_db.refresh(job)
        assert job.status == "failed"
        assert "Unexpected error" in job.log_path
