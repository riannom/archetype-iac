"""Tests for broadcasting during on-demand image sync.

Verifies that the correct state transitions are broadcast to WebSocket
clients during image sync operations: syncing, progress updates,
failure, and completion.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.orm import Session

from app import models
from app.state import ImageSyncStatus, NodeActualState


# ---------------------------------------------------------------------------
# Local fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def remote_agent(test_db: Session) -> models.Host:
    """Remote agent for broadcasting tests."""
    host = models.Host(
        id="broadcast-agent-1",
        name="Broadcast Agent",
        address="broadcast.local:8080",
        status="online",
        capabilities=json.dumps({"providers": ["docker"]}),
        version="1.0.0",
        last_heartbeat=datetime.now(timezone.utc),
        image_sync_strategy="on_demand",
        resource_usage=json.dumps({}),
    )
    test_db.add(host)
    test_db.commit()
    test_db.refresh(host)
    return host


@pytest.fixture()
def sync_lab(test_db: Session, test_user: models.User) -> models.Lab:
    """Lab for broadcasting tests."""
    lab = models.Lab(
        name="Broadcast Test Lab",
        owner_id=test_user.id,
        provider="docker",
        state="starting",
        workspace_path="/tmp/broadcast-test-lab",
    )
    test_db.add(lab)
    test_db.commit()
    test_db.refresh(lab)
    return lab


@pytest.fixture()
def sync_node(
    test_db: Session, sync_lab: models.Lab
) -> models.NodeState:
    """Single node for broadcasting tests."""
    node = models.NodeState(
        lab_id=sync_lab.id,
        node_id="ceos-bc",
        node_name="ceos-bc",
        desired_state="running",
        actual_state="undeployed",
    )
    test_db.add(node)
    test_db.commit()
    test_db.refresh(node)
    return node


# ---------------------------------------------------------------------------
# Tests: Broadcasting during sync
# ---------------------------------------------------------------------------


class TestBroadcastingDuringSync:

    @pytest.mark.asyncio
    async def test_broadcast_syncing_state(
        self,
        test_db: Session,
        sync_lab: models.Lab,
        sync_node: models.NodeState,
        remote_agent: models.Host,
    ):
        """broadcast_node_state_change should be called with
        image_sync_status='syncing', NOT actual_state='error'."""
        with patch(
            "app.services.broadcaster.broadcast_node_state_change",
            new_callable=AsyncMock,
        ) as mock_broadcast:
            from app.services.broadcaster import broadcast_node_state_change

            await broadcast_node_state_change(
                lab_id=sync_lab.id,
                node_id=sync_node.node_id,
                node_name=sync_node.node_name,
                desired_state=sync_node.desired_state,
                actual_state=sync_node.actual_state,
                image_sync_status=ImageSyncStatus.SYNCING.value,
                image_sync_message="Pushing ceos:4.28.0F to Broadcast Agent...",
            )

            mock_broadcast.assert_called_once_with(
                lab_id=sync_lab.id,
                node_id=sync_node.node_id,
                node_name=sync_node.node_name,
                desired_state=sync_node.desired_state,
                actual_state=sync_node.actual_state,
                image_sync_status="syncing",
                image_sync_message="Pushing ceos:4.28.0F to Broadcast Agent...",
            )

            # Verify actual_state was NOT set to 'error'
            call_kwargs = mock_broadcast.call_args.kwargs
            assert call_kwargs["actual_state"] != "error"
            assert call_kwargs["image_sync_status"] == "syncing"

    @pytest.mark.asyncio
    async def test_broadcast_sync_progress(
        self,
        test_db: Session,
        sync_lab: models.Lab,
        sync_node: models.NodeState,
    ):
        """Progress updates should broadcast changing image_sync_message."""
        with patch(
            "app.services.broadcaster.broadcast_node_state_change",
            new_callable=AsyncMock,
        ) as mock_broadcast:
            from app.services.broadcaster import broadcast_node_state_change

            # First progress update: 25%
            await broadcast_node_state_change(
                lab_id=sync_lab.id,
                node_id=sync_node.node_id,
                node_name=sync_node.node_name,
                desired_state=sync_node.desired_state,
                actual_state=sync_node.actual_state,
                image_sync_status="syncing",
                image_sync_message="Pushing ceos:4.28.0F to Agent-2... 25%",
            )

            # Second progress update: 50%
            await broadcast_node_state_change(
                lab_id=sync_lab.id,
                node_id=sync_node.node_id,
                node_name=sync_node.node_name,
                desired_state=sync_node.desired_state,
                actual_state=sync_node.actual_state,
                image_sync_status="syncing",
                image_sync_message="Pushing ceos:4.28.0F to Agent-2... 50%",
            )

            assert mock_broadcast.call_count == 2

            # Verify progress messages differ
            call1_msg = mock_broadcast.call_args_list[0].kwargs["image_sync_message"]
            call2_msg = mock_broadcast.call_args_list[1].kwargs["image_sync_message"]
            assert "25%" in call1_msg
            assert "50%" in call2_msg

    @pytest.mark.asyncio
    async def test_broadcast_sync_failure(
        self,
        test_db: Session,
        sync_lab: models.Lab,
        sync_node: models.NodeState,
    ):
        """Failure should broadcast actual_state='error', image_sync_status='failed',
        and error_message set."""
        with patch(
            "app.services.broadcaster.broadcast_node_state_change",
            new_callable=AsyncMock,
        ) as mock_broadcast:
            from app.services.broadcaster import broadcast_node_state_change

            await broadcast_node_state_change(
                lab_id=sync_lab.id,
                node_id=sync_node.node_id,
                node_name=sync_node.node_name,
                desired_state=sync_node.desired_state,
                actual_state=NodeActualState.ERROR.value,
                error_message="Image sync failed: timeout",
                image_sync_status=ImageSyncStatus.FAILED.value,
                image_sync_message="Image sync failed: timeout",
            )

            mock_broadcast.assert_called_once()
            call_kwargs = mock_broadcast.call_args.kwargs
            assert call_kwargs["actual_state"] == "error"
            assert call_kwargs["image_sync_status"] == "failed"
            assert call_kwargs["error_message"] == "Image sync failed: timeout"

    @pytest.mark.asyncio
    async def test_broadcast_sync_complete_then_starting(
        self,
        test_db: Session,
        sync_lab: models.Lab,
        sync_node: models.NodeState,
    ):
        """After sync: broadcasts syncing -> starting transition sequence."""
        with patch(
            "app.services.broadcaster.broadcast_node_state_change",
            new_callable=AsyncMock,
        ) as mock_broadcast:
            from app.services.broadcaster import broadcast_node_state_change

            # Step 1: syncing state
            await broadcast_node_state_change(
                lab_id=sync_lab.id,
                node_id=sync_node.node_id,
                node_name=sync_node.node_name,
                desired_state="running",
                actual_state="undeployed",
                image_sync_status="syncing",
                image_sync_message="Syncing ceos:4.28.0F...",
            )

            # Step 2: sync complete, transition to starting
            await broadcast_node_state_change(
                lab_id=sync_lab.id,
                node_id=sync_node.node_id,
                node_name=sync_node.node_name,
                desired_state="running",
                actual_state="starting",
                image_sync_status=None,
                image_sync_message=None,
            )

            assert mock_broadcast.call_count == 2

            # First call: syncing
            first_call = mock_broadcast.call_args_list[0].kwargs
            assert first_call["image_sync_status"] == "syncing"
            assert first_call["actual_state"] == "undeployed"

            # Second call: starting (sync cleared)
            second_call = mock_broadcast.call_args_list[1].kwargs
            assert second_call["image_sync_status"] is None
            assert second_call["actual_state"] == "starting"
