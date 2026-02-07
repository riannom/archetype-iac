"""Tests for live node management.

Tests the live node lifecycle functions in app/tasks/live_nodes.py.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from app import models
from app.tasks.live_nodes import (
    deploy_node_immediately,
    destroy_node_immediately,
    process_node_changes,
    _cleanup_node_records,
    _build_host_to_agent_map,
)


@pytest.fixture
def running_lab(test_db: Session, test_user: models.User) -> models.Lab:
    """Create a lab in running state."""
    lab = models.Lab(
        name="Running Lab",
        owner_id=test_user.id,
        provider="docker",
        state="running",
        workspace_path="/tmp/running-lab",
    )
    test_db.add(lab)
    test_db.commit()
    test_db.refresh(lab)
    return lab


@pytest.fixture
def stopped_lab(test_db: Session, test_user: models.User) -> models.Lab:
    """Create a lab in stopped state."""
    lab = models.Lab(
        name="Stopped Lab",
        owner_id=test_user.id,
        provider="docker",
        state="stopped",
        workspace_path="/tmp/stopped-lab",
    )
    test_db.add(lab)
    test_db.commit()
    test_db.refresh(lab)
    return lab


@pytest.fixture
def deployed_node_state(test_db: Session, running_lab: models.Lab) -> models.NodeState:
    """Create a deployed node state."""
    node = models.NodeState(
        lab_id=running_lab.id,
        node_id="n1",
        node_name="archetype-running-lab-r1",
        desired_state="running",
        actual_state="running",
        is_ready=True,
    )
    test_db.add(node)
    test_db.commit()
    test_db.refresh(node)
    return node


@pytest.fixture
def undeployed_node_state(test_db: Session, running_lab: models.Lab) -> models.NodeState:
    """Create an undeployed node state."""
    node = models.NodeState(
        lab_id=running_lab.id,
        node_id="n2",
        node_name="archetype-running-lab-r2",
        desired_state="stopped",
        actual_state="undeployed",
    )
    test_db.add(node)
    test_db.commit()
    test_db.refresh(node)
    return node


class TestDeployNodeImmediately:
    """Tests for deploy_node_immediately()."""

    @pytest.mark.asyncio
    async def test_sets_pending_state(
        self, test_db: Session, running_lab: models.Lab, undeployed_node_state: models.NodeState, sample_host: models.Host
    ):
        """Should set node to pending state before deploying."""
        with patch("app.tasks.live_nodes.broadcast_node_state_change", new_callable=AsyncMock) as mock_broadcast:
            with patch("app.tasks.live_nodes.agent_client") as mock_agent:
                mock_agent.get_agent_for_lab = AsyncMock(return_value=sample_host)
                with patch("app.tasks.live_nodes.run_node_reconcile", new_callable=AsyncMock):
                    result = await deploy_node_immediately(
                        test_db, running_lab.id, undeployed_node_state, running_lab
                    )

                    assert result is True
                    test_db.refresh(undeployed_node_state)
                    assert undeployed_node_state.desired_state == "running"
                    assert undeployed_node_state.actual_state == "pending"

    @pytest.mark.asyncio
    async def test_broadcasts_state_change(
        self, test_db: Session, running_lab: models.Lab, undeployed_node_state: models.NodeState, sample_host: models.Host
    ):
        """Should broadcast the pending state change."""
        with patch("app.tasks.live_nodes.broadcast_node_state_change", new_callable=AsyncMock) as mock_broadcast:
            with patch("app.tasks.live_nodes.agent_client") as mock_agent:
                mock_agent.get_agent_for_lab = AsyncMock(return_value=sample_host)
                with patch("app.tasks.live_nodes.run_node_reconcile", new_callable=AsyncMock):
                    await deploy_node_immediately(
                        test_db, running_lab.id, undeployed_node_state, running_lab
                    )

                    mock_broadcast.assert_called()
                    call_args = mock_broadcast.call_args
                    assert call_args.kwargs["actual_state"] == "pending"

    @pytest.mark.asyncio
    async def test_returns_false_when_no_agent(
        self, test_db: Session, running_lab: models.Lab, undeployed_node_state: models.NodeState
    ):
        """Should return False when no agent available."""
        with patch("app.tasks.live_nodes.broadcast_node_state_change", new_callable=AsyncMock):
            with patch("app.tasks.live_nodes.agent_client") as mock_agent:
                mock_agent.get_agent_for_lab = AsyncMock(return_value=None)

                result = await deploy_node_immediately(
                    test_db, running_lab.id, undeployed_node_state, running_lab
                )

                assert result is False
                test_db.refresh(undeployed_node_state)
                assert undeployed_node_state.error_message == "Waiting for agent"

    @pytest.mark.asyncio
    async def test_creates_sync_job(
        self, test_db: Session, running_lab: models.Lab, undeployed_node_state: models.NodeState, sample_host: models.Host
    ):
        """Should create a sync job for the node."""
        with patch("app.tasks.live_nodes.broadcast_node_state_change", new_callable=AsyncMock):
            with patch("app.tasks.live_nodes.agent_client") as mock_agent:
                mock_agent.get_agent_for_lab = AsyncMock(return_value=sample_host)
                with patch("app.tasks.live_nodes.run_node_reconcile", new_callable=AsyncMock):
                    await deploy_node_immediately(
                        test_db, running_lab.id, undeployed_node_state, running_lab
                    )

                    # Check job was created
                    job = test_db.query(models.Job).filter(
                        models.Job.lab_id == running_lab.id,
                        models.Job.action.contains("sync:node:")
                    ).first()
                    assert job is not None
                    assert f"sync:node:{undeployed_node_state.node_id}" in job.action


class TestDestroyNodeImmediately:
    """Tests for destroy_node_immediately()."""

    @pytest.mark.asyncio
    async def test_destroys_deployed_node(
        self, test_db: Session, running_lab: models.Lab, deployed_node_state: models.NodeState, sample_host: models.Host
    ):
        """Should destroy a deployed node via agent."""
        host_to_agent = {sample_host.id: sample_host}
        node_info = {
            "node_id": deployed_node_state.node_id,
            "node_name": deployed_node_state.node_name,
            "host_id": sample_host.id,
            "actual_state": "running",
        }

        with patch("app.tasks.live_nodes.agent_client") as mock_agent:
            mock_agent.destroy_container_on_agent = AsyncMock(return_value={"success": True})
            mock_agent.is_agent_online = MagicMock(return_value=True)

            result = await destroy_node_immediately(
                test_db, running_lab.id, node_info, host_to_agent
            )

            assert result is True
            mock_agent.destroy_container_on_agent.assert_called_once_with(
                sample_host, running_lab.id, deployed_node_state.node_name
            )

    @pytest.mark.asyncio
    async def test_skips_undeployed_node(
        self, test_db: Session, running_lab: models.Lab, undeployed_node_state: models.NodeState, sample_host: models.Host
    ):
        """Should skip destruction for undeployed nodes."""
        host_to_agent = {sample_host.id: sample_host}
        node_info = {
            "node_id": undeployed_node_state.node_id,
            "node_name": undeployed_node_state.node_name,
            "host_id": sample_host.id,
            "actual_state": "undeployed",
        }

        with patch("app.tasks.live_nodes.agent_client") as mock_agent:
            mock_agent.destroy_container_on_agent = AsyncMock()
            mock_agent.is_agent_online = MagicMock(return_value=True)

            result = await destroy_node_immediately(
                test_db, running_lab.id, node_info, host_to_agent
            )

            assert result is True
            mock_agent.destroy_container_on_agent.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_false_when_no_agent(self, test_db: Session, running_lab: models.Lab):
        """Should return False when no agent available."""
        node_info = {
            "node_id": "n1",
            "node_name": "test-node",
            "host_id": "missing-host",
            "actual_state": "running",
        }

        result = await destroy_node_immediately(
            test_db, running_lab.id, node_info, {}
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_cleans_up_records_on_success(
        self, test_db: Session, running_lab: models.Lab, deployed_node_state: models.NodeState, sample_host: models.Host
    ):
        """Should clean up database records after successful destruction."""
        # Create placement record
        placement = models.NodePlacement(
            lab_id=running_lab.id,
            node_name=deployed_node_state.node_name,
            host_id=sample_host.id,
        )
        test_db.add(placement)
        test_db.commit()

        host_to_agent = {sample_host.id: sample_host}
        node_info = {
            "node_id": deployed_node_state.node_id,
            "node_name": deployed_node_state.node_name,
            "host_id": sample_host.id,
            "actual_state": "running",
        }

        with patch("app.tasks.live_nodes.agent_client") as mock_agent:
            mock_agent.destroy_container_on_agent = AsyncMock(return_value={"success": True})
            mock_agent.is_agent_online = MagicMock(return_value=True)

            await destroy_node_immediately(
                test_db, running_lab.id, node_info, host_to_agent
            )

            # Check records were deleted
            node_state = test_db.query(models.NodeState).filter(
                models.NodeState.lab_id == running_lab.id,
                models.NodeState.node_name == deployed_node_state.node_name
            ).first()
            assert node_state is None

            placement_record = test_db.query(models.NodePlacement).filter(
                models.NodePlacement.lab_id == running_lab.id,
                models.NodePlacement.node_name == deployed_node_state.node_name
            ).first()
            assert placement_record is None


class TestCleanupNodeRecords:
    """Tests for _cleanup_node_records()."""

    def test_deletes_node_state(
        self, test_db: Session, running_lab: models.Lab, deployed_node_state: models.NodeState
    ):
        """Should delete NodeState record."""
        _cleanup_node_records(test_db, running_lab.id, deployed_node_state.node_name)

        result = test_db.query(models.NodeState).filter(
            models.NodeState.id == deployed_node_state.id
        ).first()
        assert result is None

    def test_deletes_node_placement(
        self, test_db: Session, running_lab: models.Lab, deployed_node_state: models.NodeState, sample_host: models.Host
    ):
        """Should delete NodePlacement record."""
        placement = models.NodePlacement(
            lab_id=running_lab.id,
            node_name=deployed_node_state.node_name,
            host_id=sample_host.id,
        )
        test_db.add(placement)
        test_db.commit()

        # Save the ID before cleanup, because after commit the ORM object expires
        placement_id = placement.id

        _cleanup_node_records(test_db, running_lab.id, deployed_node_state.node_name)

        result = test_db.query(models.NodePlacement).filter(
            models.NodePlacement.id == placement_id
        ).first()
        assert result is None


class TestProcessNodeChanges:
    """Tests for process_node_changes()."""

    @pytest.mark.asyncio
    async def test_processes_removed_nodes_first(
        self, test_db: Session, running_lab: models.Lab, deployed_node_state: models.NodeState, sample_host: models.Host
    ):
        """Should process removals before additions."""
        # Set up lab with agent
        running_lab.agent_id = sample_host.id
        test_db.commit()

        removed_info = [{
            "node_id": deployed_node_state.node_id,
            "node_name": deployed_node_state.node_name,
            "host_id": sample_host.id,
            "actual_state": "running",
        }]

        @contextmanager
        def override_get_session():
            yield test_db

        with patch("app.tasks.live_nodes.get_session", override_get_session):
            with patch("app.tasks.live_nodes.destroy_node_immediately", new_callable=AsyncMock) as mock_destroy:
                mock_destroy.return_value = True
                with patch("app.tasks.live_nodes.agent_client") as mock_agent:
                    mock_agent.is_agent_online = MagicMock(return_value=True)

                    await process_node_changes(running_lab.id, [], removed_info)
                    # Wait for debounced processing to complete
                    await asyncio.sleep(1)

                    mock_destroy.assert_called_once()

    @pytest.mark.asyncio
    async def test_auto_deploys_only_when_running(
        self, test_db: Session, stopped_lab: models.Lab, sample_host: models.Host
    ):
        """Should not auto-deploy when lab is stopped."""
        # Create undeployed node
        node = models.NodeState(
            lab_id=stopped_lab.id,
            node_id="n1",
            node_name="test-node",
            desired_state="stopped",
            actual_state="undeployed",
        )
        test_db.add(node)
        test_db.commit()

        @contextmanager
        def override_get_session():
            yield test_db

        with patch("app.tasks.live_nodes.get_session", override_get_session):
            with patch("app.tasks.live_nodes.deploy_node_immediately", new_callable=AsyncMock) as mock_deploy:
                with patch("app.tasks.live_nodes.agent_client") as mock_agent:
                    mock_agent.is_agent_online = MagicMock(return_value=True)

                    await process_node_changes(stopped_lab.id, ["n1"], [])
                    # Wait for debounced processing to complete
                    await asyncio.sleep(1)

                    # Deploy should not be called for stopped lab
                    mock_deploy.assert_not_called()

    @pytest.mark.asyncio
    async def test_auto_deploys_when_running(
        self, test_db: Session, running_lab: models.Lab, sample_host: models.Host
    ):
        """Should auto-deploy when lab is running."""
        running_lab.agent_id = sample_host.id
        test_db.commit()

        # Create undeployed node
        node = models.NodeState(
            lab_id=running_lab.id,
            node_id="n3",
            node_name="test-node-3",
            desired_state="stopped",
            actual_state="undeployed",
        )
        test_db.add(node)
        test_db.commit()

        @contextmanager
        def override_get_session():
            yield test_db

        with patch("app.tasks.live_nodes.get_session", override_get_session):
            with patch("app.tasks.live_nodes.deploy_node_immediately", new_callable=AsyncMock) as mock_deploy:
                mock_deploy.return_value = True
                with patch("app.tasks.live_nodes.agent_client") as mock_agent:
                    mock_agent.is_agent_online = MagicMock(return_value=True)

                    await process_node_changes(running_lab.id, ["n3"], [])
                    # Wait for debounced processing to complete
                    await asyncio.sleep(1)

                    mock_deploy.assert_called_once()

    @pytest.mark.asyncio
    async def test_handles_missing_lab(self, test_db: Session):
        """Should handle missing lab gracefully."""
        @contextmanager
        def override_get_session():
            yield test_db

        with patch("app.tasks.live_nodes.get_session", override_get_session):
            # Should not raise
            await process_node_changes("nonexistent-lab", ["n1"], [])
            # Wait for debounced processing to complete
            await asyncio.sleep(1)


class TestBuildHostToAgentMap:
    """Tests for _build_host_to_agent_map()."""

    @pytest.mark.asyncio
    async def test_includes_placement_agents(
        self, test_db: Session, running_lab: models.Lab, sample_host: models.Host
    ):
        """Should include agents from NodePlacement records."""
        placement = models.NodePlacement(
            lab_id=running_lab.id,
            node_name="test-node",
            host_id=sample_host.id,
        )
        test_db.add(placement)
        test_db.commit()

        with patch("app.tasks.live_nodes.agent_client") as mock_agent:
            mock_agent.is_agent_online = MagicMock(return_value=True)

            result = await _build_host_to_agent_map(test_db, running_lab.id, running_lab)

            assert sample_host.id in result
            assert result[sample_host.id].id == sample_host.id

    @pytest.mark.asyncio
    async def test_includes_lab_agent(
        self, test_db: Session, running_lab: models.Lab, sample_host: models.Host
    ):
        """Should include lab's default agent."""
        running_lab.agent_id = sample_host.id
        test_db.commit()

        with patch("app.tasks.live_nodes.agent_client") as mock_agent:
            mock_agent.is_agent_online = MagicMock(return_value=True)

            result = await _build_host_to_agent_map(test_db, running_lab.id, running_lab)

            assert sample_host.id in result

    @pytest.mark.asyncio
    async def test_excludes_offline_agents(
        self, test_db: Session, running_lab: models.Lab, offline_host: models.Host
    ):
        """Should exclude offline agents."""
        running_lab.agent_id = offline_host.id
        test_db.commit()

        with patch("app.tasks.live_nodes.agent_client") as mock_agent:
            mock_agent.is_agent_online = MagicMock(return_value=False)

            result = await _build_host_to_agent_map(test_db, running_lab.id, running_lab)

            assert offline_host.id not in result
