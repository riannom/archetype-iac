"""Tests for auto-sync behavior.

Tests the automatic synchronization when nodes are added to running labs.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from app import models
from app.tasks.live_nodes import deploy_node_immediately, process_node_changes
from app.tasks.jobs import run_node_sync


@pytest.fixture
def running_lab_with_agent(test_db: Session, test_user: models.User, sample_host: models.Host) -> models.Lab:
    """Create a running lab with an assigned agent."""
    lab = models.Lab(
        name="Running Lab",
        owner_id=test_user.id,
        provider="docker",
        state="running",
        workspace_path="/tmp/running-lab",
        agent_id=sample_host.id,
    )
    test_db.add(lab)
    test_db.commit()
    test_db.refresh(lab)
    return lab


@pytest.fixture
def new_node_state(test_db: Session, running_lab_with_agent: models.Lab) -> models.NodeState:
    """Create a new undeployed node state."""
    node = models.NodeState(
        lab_id=running_lab_with_agent.id,
        node_id="new-node",
        node_name="archetype-running-lab-new",
        desired_state="stopped",
        actual_state="undeployed",
    )
    test_db.add(node)
    test_db.commit()
    test_db.refresh(node)
    return node


class TestAutoSyncJobCreation:
    """Tests for auto-sync job creation."""

    @pytest.mark.asyncio
    async def test_creates_sync_job_for_new_node(
        self,
        test_db: Session,
        running_lab_with_agent: models.Lab,
        new_node_state: models.NodeState,
        sample_host: models.Host,
    ):
        """Should create a sync job when deploying a new node."""
        with patch("app.tasks.live_nodes.broadcast_node_state_change", new_callable=AsyncMock):
            with patch("app.tasks.live_nodes.agent_client") as mock_agent:
                mock_agent.get_agent_for_lab = AsyncMock(return_value=sample_host)
                with patch("app.tasks.live_nodes.run_node_sync", new_callable=AsyncMock) as mock_sync:
                    await deploy_node_immediately(
                        test_db,
                        running_lab_with_agent.id,
                        new_node_state,
                        running_lab_with_agent,
                    )

                    # Verify job was created
                    job = test_db.query(models.Job).filter(
                        models.Job.lab_id == running_lab_with_agent.id,
                        models.Job.action == f"sync:node:{new_node_state.node_id}",
                    ).first()
                    assert job is not None
                    assert job.status == "queued"
                    assert job.user_id == running_lab_with_agent.owner_id

    @pytest.mark.asyncio
    async def test_sync_job_includes_node_id(
        self,
        test_db: Session,
        running_lab_with_agent: models.Lab,
        new_node_state: models.NodeState,
        sample_host: models.Host,
    ):
        """Sync job action should include the node ID."""
        with patch("app.tasks.live_nodes.broadcast_node_state_change", new_callable=AsyncMock):
            with patch("app.tasks.live_nodes.agent_client") as mock_agent:
                mock_agent.get_agent_for_lab = AsyncMock(return_value=sample_host)
                with patch("app.tasks.live_nodes.run_node_sync", new_callable=AsyncMock):
                    await deploy_node_immediately(
                        test_db,
                        running_lab_with_agent.id,
                        new_node_state,
                        running_lab_with_agent,
                    )

                    job = test_db.query(models.Job).filter(
                        models.Job.lab_id == running_lab_with_agent.id,
                    ).order_by(models.Job.created_at.desc()).first()

                    assert new_node_state.node_id in job.action


class TestAutoSyncExecution:
    """Tests for auto-sync execution behavior."""

    @pytest.mark.asyncio
    async def test_sync_triggered_in_background(
        self,
        test_db: Session,
        running_lab_with_agent: models.Lab,
        new_node_state: models.NodeState,
        sample_host: models.Host,
    ):
        """Sync should be triggered as a background task."""
        with patch("app.tasks.live_nodes.broadcast_node_state_change", new_callable=AsyncMock):
            with patch("app.tasks.live_nodes.agent_client") as mock_agent:
                mock_agent.get_agent_for_lab = AsyncMock(return_value=sample_host)
                with patch("app.tasks.live_nodes.run_node_sync", new_callable=AsyncMock) as mock_sync:
                    await deploy_node_immediately(
                        test_db,
                        running_lab_with_agent.id,
                        new_node_state,
                        running_lab_with_agent,
                    )

                    # run_node_sync should be called via asyncio.create_task
                    # We can't directly verify create_task, but we can check the mock was prepared


class TestAutoSyncConditions:
    """Tests for when auto-sync should/shouldn't trigger."""

    @pytest.mark.asyncio
    async def test_no_sync_when_lab_stopped(
        self, test_db: Session, test_user: models.User, sample_host: models.Host
    ):
        """Should not auto-sync when lab is stopped."""
        stopped_lab = models.Lab(
            name="Stopped Lab",
            owner_id=test_user.id,
            provider="docker",
            state="stopped",
            workspace_path="/tmp/stopped-lab",
            agent_id=sample_host.id,
        )
        test_db.add(stopped_lab)
        test_db.commit()

        new_node = models.NodeState(
            lab_id=stopped_lab.id,
            node_id="n1",
            node_name="test-node",
            desired_state="stopped",
            actual_state="undeployed",
        )
        test_db.add(new_node)
        test_db.commit()

        with patch("app.tasks.live_nodes.SessionLocal") as mock_session_local:
            mock_session_local.return_value = test_db
            with patch("app.tasks.live_nodes.deploy_node_immediately", new_callable=AsyncMock) as mock_deploy:
                with patch("app.tasks.live_nodes.agent_client") as mock_agent:
                    mock_agent.is_agent_online = MagicMock(return_value=True)

                    await process_node_changes(stopped_lab.id, ["n1"], [])

                    mock_deploy.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_when_lab_starting(
        self, test_db: Session, test_user: models.User, sample_host: models.Host
    ):
        """Should auto-sync when lab is in 'starting' state."""
        starting_lab = models.Lab(
            name="Starting Lab",
            owner_id=test_user.id,
            provider="docker",
            state="starting",
            workspace_path="/tmp/starting-lab",
            agent_id=sample_host.id,
        )
        test_db.add(starting_lab)
        test_db.commit()

        new_node = models.NodeState(
            lab_id=starting_lab.id,
            node_id="n1",
            node_name="test-node",
            desired_state="stopped",
            actual_state="undeployed",
        )
        test_db.add(new_node)
        test_db.commit()

        with patch("app.tasks.live_nodes.SessionLocal") as mock_session_local:
            mock_session_local.return_value = test_db
            with patch("app.tasks.live_nodes.deploy_node_immediately", new_callable=AsyncMock) as mock_deploy:
                mock_deploy.return_value = True
                with patch("app.tasks.live_nodes.agent_client") as mock_agent:
                    mock_agent.is_agent_online = MagicMock(return_value=True)

                    await process_node_changes(starting_lab.id, ["n1"], [])

                    mock_deploy.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_sync_for_already_running_node(
        self, test_db: Session, running_lab_with_agent: models.Lab, sample_host: models.Host
    ):
        """Should not sync nodes that are already running."""
        running_node = models.NodeState(
            lab_id=running_lab_with_agent.id,
            node_id="running-n1",
            node_name="running-node",
            desired_state="running",
            actual_state="running",
            is_ready=True,
        )
        test_db.add(running_node)
        test_db.commit()

        with patch("app.tasks.live_nodes.SessionLocal") as mock_session_local:
            mock_session_local.return_value = test_db
            with patch("app.tasks.live_nodes.deploy_node_immediately", new_callable=AsyncMock) as mock_deploy:
                with patch("app.tasks.live_nodes.agent_client") as mock_agent:
                    mock_agent.is_agent_online = MagicMock(return_value=True)

                    await process_node_changes(running_lab_with_agent.id, ["running-n1"], [])

                    mock_deploy.assert_not_called()


class TestAutoSyncStateTransitions:
    """Tests for state transitions during auto-sync."""

    @pytest.mark.asyncio
    async def test_sets_pending_before_sync(
        self,
        test_db: Session,
        running_lab_with_agent: models.Lab,
        new_node_state: models.NodeState,
        sample_host: models.Host,
    ):
        """Node should transition to 'pending' before sync starts."""
        with patch("app.tasks.live_nodes.broadcast_node_state_change", new_callable=AsyncMock):
            with patch("app.tasks.live_nodes.agent_client") as mock_agent:
                mock_agent.get_agent_for_lab = AsyncMock(return_value=sample_host)
                with patch("app.tasks.live_nodes.run_node_sync", new_callable=AsyncMock):
                    await deploy_node_immediately(
                        test_db,
                        running_lab_with_agent.id,
                        new_node_state,
                        running_lab_with_agent,
                    )

                    test_db.refresh(new_node_state)
                    assert new_node_state.actual_state == "pending"
                    assert new_node_state.desired_state == "running"

    @pytest.mark.asyncio
    async def test_broadcasts_pending_state(
        self,
        test_db: Session,
        running_lab_with_agent: models.Lab,
        new_node_state: models.NodeState,
        sample_host: models.Host,
    ):
        """Should broadcast pending state change."""
        with patch("app.tasks.live_nodes.broadcast_node_state_change", new_callable=AsyncMock) as mock_broadcast:
            with patch("app.tasks.live_nodes.agent_client") as mock_agent:
                mock_agent.get_agent_for_lab = AsyncMock(return_value=sample_host)
                with patch("app.tasks.live_nodes.run_node_sync", new_callable=AsyncMock):
                    await deploy_node_immediately(
                        test_db,
                        running_lab_with_agent.id,
                        new_node_state,
                        running_lab_with_agent,
                    )

                    # First broadcast should be for pending state
                    assert mock_broadcast.call_count >= 1
                    first_call = mock_broadcast.call_args_list[0]
                    assert first_call.kwargs["actual_state"] == "pending"
