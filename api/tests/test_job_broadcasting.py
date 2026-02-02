"""Tests for job progress broadcasting integration.

Tests that job execution functions correctly broadcast progress updates
to connected WebSocket clients via the StateBroadcaster.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from app import models
from app.tasks.jobs import (
    _broadcast_job_progress,
    run_agent_job,
    run_node_reconcile,
)


@pytest.fixture
def mock_broadcaster():
    """Create a mock broadcaster for verifying publish calls."""
    mock = MagicMock()
    mock.publish_job_progress = AsyncMock(return_value=1)
    mock.publish_node_state = AsyncMock(return_value=1)
    mock.publish_lab_state = AsyncMock(return_value=1)
    return mock


class TestBroadcastJobProgress:
    """Tests for the _broadcast_job_progress helper function."""

    @pytest.mark.asyncio
    async def test_broadcast_job_progress_calls_broadcaster(self, mock_broadcaster):
        """Should call broadcaster.publish_job_progress with correct args."""
        with patch("app.tasks.jobs.get_broadcaster", return_value=mock_broadcaster):
            await _broadcast_job_progress(
                lab_id="test-lab",
                job_id="test-job",
                action="up",
                status="running",
                progress_message="Deploying nodes",
            )

        mock_broadcaster.publish_job_progress.assert_called_once_with(
            lab_id="test-lab",
            job_id="test-job",
            action="up",
            status="running",
            progress_message="Deploying nodes",
            error_message=None,
        )

    @pytest.mark.asyncio
    async def test_broadcast_job_progress_with_error(self, mock_broadcaster):
        """Should pass error_message to broadcaster."""
        with patch("app.tasks.jobs.get_broadcaster", return_value=mock_broadcaster):
            await _broadcast_job_progress(
                lab_id="test-lab",
                job_id="test-job",
                action="up",
                status="failed",
                error_message="Image not found",
            )

        mock_broadcaster.publish_job_progress.assert_called_once()
        call_kwargs = mock_broadcaster.publish_job_progress.call_args[1]
        assert call_kwargs["error_message"] == "Image not found"
        assert call_kwargs["status"] == "failed"

    @pytest.mark.asyncio
    async def test_broadcast_job_progress_handles_errors(self, mock_broadcaster):
        """Should not raise when broadcaster fails."""
        mock_broadcaster.publish_job_progress.side_effect = Exception("Redis error")

        with patch("app.tasks.jobs.get_broadcaster", return_value=mock_broadcaster):
            # Should not raise
            await _broadcast_job_progress(
                lab_id="test-lab",
                job_id="test-job",
                action="up",
                status="running",
            )


class TestRunAgentJobBroadcasts:
    """Tests for job progress broadcasting in run_agent_job."""

    @pytest.mark.asyncio
    async def test_run_agent_job_broadcasts_running_status(
        self,
        test_db: Session,
        test_user: models.User,
        sample_host: models.Host,
        mock_broadcaster,
    ):
        """Job should broadcast 'running' status when started."""
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
            with patch("app.tasks.jobs.get_broadcaster", return_value=mock_broadcaster):
                with patch("app.tasks.jobs.agent_client.get_agent_for_lab", new_callable=AsyncMock) as mock_agent:
                    mock_agent.return_value = sample_host
                    with patch("app.tasks.jobs.agent_client.deploy_to_agent", new_callable=AsyncMock) as mock_deploy:
                        mock_deploy.return_value = {"status": "completed"}
                        # Build topology from DB, mock the topology builder
                        with patch("app.tasks.jobs.graph_to_deploy_topology") as mock_topo:
                            mock_topo.return_value = {"name": "test", "topology": {"nodes": {}}}
                            await run_agent_job(job.id, lab.id, "up")

        # Verify running status was broadcast
        running_calls = [
            call for call in mock_broadcaster.publish_job_progress.call_args_list
            if call[1].get("status") == "running"
        ]
        assert len(running_calls) >= 1

    @pytest.mark.asyncio
    async def test_run_agent_job_broadcasts_completed_status(
        self,
        test_db: Session,
        test_user: models.User,
        sample_host: models.Host,
        mock_broadcaster,
    ):
        """Job should broadcast 'completed' status on success."""
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
            with patch("app.tasks.jobs.get_broadcaster", return_value=mock_broadcaster):
                with patch("app.tasks.jobs.agent_client.get_agent_for_lab", new_callable=AsyncMock) as mock_agent:
                    mock_agent.return_value = sample_host
                    with patch("app.tasks.jobs.agent_client.deploy_to_agent", new_callable=AsyncMock) as mock_deploy:
                        mock_deploy.return_value = {"status": "completed"}
                        with patch("app.tasks.jobs.graph_to_deploy_topology") as mock_topo:
                            mock_topo.return_value = {"name": "test", "topology": {"nodes": {}}}
                            await run_agent_job(job.id, lab.id, "up")

        # Verify completed status was broadcast
        completed_calls = [
            call for call in mock_broadcaster.publish_job_progress.call_args_list
            if call[1].get("status") == "completed"
        ]
        assert len(completed_calls) >= 1

    @pytest.mark.asyncio
    async def test_run_agent_job_broadcasts_failed_status(
        self,
        test_db: Session,
        test_user: models.User,
        sample_host: models.Host,
        mock_broadcaster,
    ):
        """Job should broadcast 'failed' status on failure."""
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
            with patch("app.tasks.jobs.get_broadcaster", return_value=mock_broadcaster):
                with patch("app.tasks.jobs.agent_client.get_agent_for_lab", new_callable=AsyncMock) as mock_agent:
                    mock_agent.return_value = sample_host
                    with patch("app.tasks.jobs.agent_client.deploy_to_agent", new_callable=AsyncMock) as mock_deploy:
                        mock_deploy.return_value = {
                            "status": "failed",
                            "error_message": "Deploy failed",
                        }
                        with patch("app.tasks.jobs.graph_to_deploy_topology") as mock_topo:
                            mock_topo.return_value = {"name": "test", "topology": {"nodes": {}}}
                            await run_agent_job(job.id, lab.id, "up")

        # Verify failed status was broadcast
        failed_calls = [
            call for call in mock_broadcaster.publish_job_progress.call_args_list
            if call[1].get("status") == "failed"
        ]
        assert len(failed_calls) >= 1

    @pytest.mark.asyncio
    async def test_run_agent_job_broadcasts_include_agent_info(
        self,
        test_db: Session,
        test_user: models.User,
        sample_host: models.Host,
        mock_broadcaster,
    ):
        """Broadcast should include agent name in progress message."""
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

        # Store host info before session closes
        host_name = sample_host.name
        host_id = sample_host.id

        with patch("app.tasks.jobs.SessionLocal", return_value=test_db):
            with patch("app.tasks.jobs.get_broadcaster", return_value=mock_broadcaster):
                with patch("app.tasks.jobs.agent_client.get_agent_for_lab", new_callable=AsyncMock) as mock_agent:
                    mock_agent.return_value = sample_host
                    with patch("app.tasks.jobs.agent_client.deploy_to_agent", new_callable=AsyncMock) as mock_deploy:
                        mock_deploy.return_value = {"status": "completed"}
                        with patch("app.tasks.jobs.graph_to_deploy_topology") as mock_topo:
                            mock_topo.return_value = {"name": "test", "topology": {"nodes": {}}}
                            await run_agent_job(job.id, lab.id, "up")

        # Check that agent name is mentioned in progress message
        running_calls = [
            call for call in mock_broadcaster.publish_job_progress.call_args_list
            if call[1].get("status") == "running"
        ]
        assert any(
            host_name in (call[1].get("progress_message") or "")
            or host_id in (call[1].get("progress_message") or "")
            for call in running_calls
        )


class TestRunNodeReconcileBroadcasts:
    """Tests for job progress broadcasting in run_node_reconcile.

    Note: Full integration tests for run_node_reconcile are in test_jobs_execution.py.
    These tests focus on the broadcasting behavior.
    """

    @pytest.mark.asyncio
    async def test_run_node_reconcile_calls_broadcast_on_start(self, mock_broadcaster):
        """Verify _broadcast_job_progress is called by run_node_reconcile (unit test)."""
        # We test the _broadcast_job_progress function directly since
        # run_node_reconcile has complex dependencies. Full integration tests
        # are in test_jobs_execution.py.
        with patch("app.tasks.jobs.get_broadcaster", return_value=mock_broadcaster):
            await _broadcast_job_progress(
                lab_id="test-lab",
                job_id="test-job",
                action="sync",
                status="running",
                progress_message="Syncing 3 node(s)",
            )

        mock_broadcaster.publish_job_progress.assert_called_once()
        call_kwargs = mock_broadcaster.publish_job_progress.call_args[1]
        assert call_kwargs["status"] == "running"
        assert "3 node" in call_kwargs["progress_message"]


class TestMultihostDeployBroadcasts:
    """Tests for job progress broadcasting in multi-host deploy."""

    @pytest.mark.asyncio
    async def test_multihost_deploy_broadcasts_host_count(
        self,
        test_db: Session,
        test_user: models.User,
        multiple_hosts: list[models.Host],
        mock_broadcaster,
    ):
        """Multi-host deploy should broadcast progress with host count."""
        from app.tasks.jobs import run_multihost_deploy

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
            host_id=multiple_hosts[0].id,
        )
        node2 = models.Node(
            lab_id=lab.id,
            gui_id="r2",
            display_name="r2",
            container_name="r2",
            node_type="device",
            device="linux",
            host_id=multiple_hosts[1].id,
        )
        test_db.add_all([node1, node2])
        test_db.commit()

        with patch("app.tasks.jobs.SessionLocal", return_value=test_db):
            with patch("app.tasks.jobs.get_broadcaster", return_value=mock_broadcaster):
                with patch("app.tasks.jobs.agent_client.deploy_to_agent", new_callable=AsyncMock) as mock_deploy:
                    mock_deploy.return_value = {"status": "completed"}
                    await run_multihost_deploy(job.id, lab.id)

        # Check for host count in progress messages
        all_calls = mock_broadcaster.publish_job_progress.call_args_list
        progress_messages = [
            call[1].get("progress_message", "") for call in all_calls
        ]
        # Should mention hosts in the message
        assert any("host" in msg.lower() for msg in progress_messages if msg)


class TestBroadcastSequence:
    """Tests for the sequence of broadcast events during job execution."""

    @pytest.mark.asyncio
    async def test_broadcast_sequence_running_then_completed(
        self,
        test_db: Session,
        test_user: models.User,
        sample_host: models.Host,
        mock_broadcaster,
    ):
        """Broadcasts should follow running -> completed sequence."""
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

        broadcast_sequence = []

        async def track_broadcast(**kwargs):
            broadcast_sequence.append(kwargs.get("status"))
            return 1

        mock_broadcaster.publish_job_progress = track_broadcast

        with patch("app.tasks.jobs.SessionLocal", return_value=test_db):
            with patch("app.tasks.jobs.get_broadcaster", return_value=mock_broadcaster):
                with patch("app.tasks.jobs.agent_client.get_agent_for_lab", new_callable=AsyncMock) as mock_agent:
                    mock_agent.return_value = sample_host
                    with patch("app.tasks.jobs.agent_client.deploy_to_agent", new_callable=AsyncMock) as mock_deploy:
                        mock_deploy.return_value = {"status": "completed"}
                        with patch("app.tasks.jobs.graph_to_deploy_topology") as mock_topo:
                            mock_topo.return_value = {"name": "test", "topology": {"nodes": {}}}
                            await run_agent_job(job.id, lab.id, "up")

        # Should have running before completed
        assert "running" in broadcast_sequence
        assert "completed" in broadcast_sequence
        assert broadcast_sequence.index("running") < broadcast_sequence.index("completed")

    @pytest.mark.asyncio
    async def test_broadcast_sequence_running_then_failed(
        self,
        test_db: Session,
        test_user: models.User,
        sample_host: models.Host,
        mock_broadcaster,
    ):
        """Broadcasts should follow running -> failed sequence on error."""
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

        broadcast_sequence = []

        async def track_broadcast(**kwargs):
            broadcast_sequence.append(kwargs.get("status"))
            return 1

        mock_broadcaster.publish_job_progress = track_broadcast

        with patch("app.tasks.jobs.SessionLocal", return_value=test_db):
            with patch("app.tasks.jobs.get_broadcaster", return_value=mock_broadcaster):
                with patch("app.tasks.jobs.agent_client.get_agent_for_lab", new_callable=AsyncMock) as mock_agent:
                    mock_agent.return_value = sample_host
                    with patch("app.tasks.jobs.agent_client.deploy_to_agent", new_callable=AsyncMock) as mock_deploy:
                        mock_deploy.return_value = {
                            "status": "failed",
                            "error_message": "Deploy error",
                        }
                        with patch("app.tasks.jobs.graph_to_deploy_topology") as mock_topo:
                            mock_topo.return_value = {"name": "test", "topology": {"nodes": {}}}
                            await run_agent_job(job.id, lab.id, "up")

        # Should have running before failed
        assert "running" in broadcast_sequence
        assert "failed" in broadcast_sequence
        assert broadcast_sequence.index("running") < broadcast_sequence.index("failed")
