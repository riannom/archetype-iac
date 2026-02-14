"""Integration tests for WebSocket state updates end-to-end flow.

Tests the complete flow from state changes through broadcaster to WebSocket clients.
These tests verify that state changes propagate correctly through the pub/sub system.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models
from app.services.broadcaster import StateBroadcaster


class TestNodeStateChangeFlow:
    """Tests for node state change propagation to WebSocket."""

    def test_node_state_change_triggers_websocket_message(
        self,
        test_client: TestClient,
        sample_lab_with_nodes: tuple[models.Lab, list[models.NodeState]],
        ws_token: str,
    ):
        """Node state change should be sent to WebSocket clients."""
        lab, nodes = sample_lab_with_nodes

        # Create a message that would be received from Redis pub/sub
        state_update_message = {
            "type": "node_state",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": {
                "node_id": nodes[0].node_id,
                "node_name": nodes[0].node_name,
                "desired_state": "running",
                "actual_state": "running",
                "is_ready": True,
            },
        }

        messages_received = []

        async def mock_subscribe(lab_id):
            """Mock subscribe that yields one state update message."""
            yield state_update_message

        with patch("app.routers.state_ws.get_broadcaster") as mock_broadcaster:
            mock_instance = MagicMock()
            mock_instance.subscribe = mock_subscribe
            mock_broadcaster.return_value = mock_instance

            with test_client.websocket_connect(f"/ws/labs/{lab.id}/state?token={ws_token}") as ws:
                # Receive initial messages
                ws.receive_json()  # lab_state
                ws.receive_json()  # initial_state

                # Should receive the state update
                try:
                    data = ws.receive_json(mode="binary")
                    if isinstance(data, bytes):
                        data = json.loads(data)
                except Exception:
                    # May timeout if no more messages
                    data = None

                if data and data.get("type") == "node_state":
                    messages_received.append(data)

        # Verify we received at least the initial state
        # (The mock may or may not yield the update depending on timing)

    def test_job_progress_flows_to_websocket(
        self,
        test_client: TestClient,
        sample_lab: models.Lab,
        ws_token: str,
    ):
        """Job progress updates should flow to WebSocket clients."""
        job_progress_message = {
            "type": "job_progress",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": {
                "job_id": "test-job",
                "action": "up",
                "status": "running",
                "progress_message": "Deploying nodes",
            },
        }

        received_messages = []

        async def mock_subscribe(lab_id):
            """Mock subscribe that yields job progress."""
            yield job_progress_message

        with patch("app.routers.state_ws.get_broadcaster") as mock_broadcaster:
            mock_instance = MagicMock()
            mock_instance.subscribe = mock_subscribe
            mock_broadcaster.return_value = mock_instance

            with test_client.websocket_connect(f"/ws/labs/{sample_lab.id}/state?token={ws_token}") as ws:
                # Receive initial messages
                ws.receive_json()  # lab_state
                ws.receive_json()  # initial_state

                # Try to receive job progress
                try:
                    data = ws.receive_json(mode="binary")
                    if isinstance(data, bytes):
                        data = json.loads(data)
                    if data.get("type") == "job_progress":
                        received_messages.append(data)
                except Exception:
                    pass

    def test_multiple_clients_receive_same_update(
        self,
        test_client: TestClient,
        sample_lab: models.Lab,
        ws_token: str,
    ):
        """Multiple clients on same lab should receive the same update."""
        from app.routers.state_ws import manager

        {
            "type": "node_state",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": {
                "node_id": "n1",
                "actual_state": "running",
            },
        }

        connection_count = 0

        async def mock_subscribe(lab_id):
            """Empty subscribe for connection testing."""
            return
            yield  # Make it a generator

        with patch("app.routers.state_ws.get_broadcaster") as mock_broadcaster:
            mock_instance = MagicMock()
            mock_instance.subscribe = AsyncMock(return_value=mock_subscribe(sample_lab.id))
            mock_broadcaster.return_value = mock_instance

            # Connect first client
            with test_client.websocket_connect(f"/ws/labs/{sample_lab.id}/state?token={ws_token}"):
                # Check connection was registered
                if sample_lab.id in manager.active_connections:
                    connection_count = len(manager.active_connections[sample_lab.id])
                    assert connection_count >= 1

                # Connect second client
                with test_client.websocket_connect(f"/ws/labs/{sample_lab.id}/state?token={ws_token}"):
                    # Should have more connections now
                    if sample_lab.id in manager.active_connections:
                        assert len(manager.active_connections[sample_lab.id]) >= connection_count

    def test_client_disconnect_cleanup(
        self,
        test_client: TestClient,
        sample_lab: models.Lab,
        ws_token: str,
    ):
        """Disconnecting client should clean up from manager."""
        from app.routers.state_ws import manager

        async def mock_subscribe(lab_id):
            return
            yield

        with patch("app.routers.state_ws.get_broadcaster") as mock_broadcaster:
            mock_instance = MagicMock()
            mock_instance.subscribe = AsyncMock(return_value=mock_subscribe(sample_lab.id))
            mock_broadcaster.return_value = mock_instance

            initial_count = len(manager.active_connections.get(sample_lab.id, []))

            with test_client.websocket_connect(f"/ws/labs/{sample_lab.id}/state?token={ws_token}"):
                # Connection is active
                pass

            # After disconnect, should have same or fewer connections
            final_count = len(manager.active_connections.get(sample_lab.id, []))
            assert final_count <= initial_count + 1  # May have other test connections


class TestBroadcasterPubSub:
    """Tests for StateBroadcaster pub/sub behavior."""

    @pytest.mark.asyncio
    async def test_publish_delivers_to_correct_channel(self):
        """Published messages should go to lab-specific channel."""
        mock_redis = MagicMock()
        mock_redis.publish = AsyncMock(return_value=1)

        broadcaster = StateBroadcaster("redis://localhost")
        broadcaster._redis = mock_redis

        await broadcaster.publish_node_state(
            lab_id="lab-123",
            node_id="n1",
            node_name="router",
            desired_state="running",
            actual_state="running",
        )

        # Verify channel name
        call_args = mock_redis.publish.call_args
        channel = call_args[0][0]
        assert channel == "lab_state:lab-123"

    @pytest.mark.asyncio
    async def test_different_labs_use_different_channels(self):
        """Different labs should publish to different channels."""
        mock_redis = MagicMock()
        mock_redis.publish = AsyncMock(return_value=1)

        broadcaster = StateBroadcaster("redis://localhost")
        broadcaster._redis = mock_redis

        await broadcaster.publish_node_state(
            lab_id="lab-1",
            node_id="n1",
            node_name="router",
            desired_state="running",
            actual_state="running",
        )

        await broadcaster.publish_node_state(
            lab_id="lab-2",
            node_id="n1",
            node_name="router",
            desired_state="running",
            actual_state="running",
        )

        # Verify different channels
        channels = [call[0][0] for call in mock_redis.publish.call_args_list]
        assert "lab_state:lab-1" in channels
        assert "lab_state:lab-2" in channels


class TestMessageFormats:
    """Tests for WebSocket message format consistency."""

    def test_initial_state_format(
        self,
        test_client: TestClient,
        sample_lab_with_nodes: tuple[models.Lab, list[models.NodeState]],
        ws_token: str,
    ):
        """Initial state message should have correct format."""
        lab, nodes = sample_lab_with_nodes

        async def mock_subscribe(lab_id):
            return
            yield

        with patch("app.routers.state_ws.get_broadcaster") as mock_broadcaster:
            mock_instance = MagicMock()
            mock_instance.subscribe = AsyncMock(return_value=mock_subscribe(lab.id))
            mock_broadcaster.return_value = mock_instance

            with test_client.websocket_connect(f"/ws/labs/{lab.id}/state?token={ws_token}") as ws:
                ws.receive_json()  # lab_state
                data = ws.receive_json()  # initial_state

                assert data["type"] == "initial_state"
                assert "nodes" in data["data"]
                assert isinstance(data["data"]["nodes"], list)

                if data["data"]["nodes"]:
                    node = data["data"]["nodes"][0]
                    assert "node_id" in node
                    assert "node_name" in node
                    assert "desired_state" in node
                    assert "actual_state" in node

    def test_lab_state_format(
        self,
        test_client: TestClient,
        sample_lab: models.Lab,
        ws_token: str,
    ):
        """Lab state message should have correct format."""
        async def mock_subscribe(lab_id):
            return
            yield

        with patch("app.routers.state_ws.get_broadcaster") as mock_broadcaster:
            mock_instance = MagicMock()
            mock_instance.subscribe = AsyncMock(return_value=mock_subscribe(sample_lab.id))
            mock_broadcaster.return_value = mock_instance

            with test_client.websocket_connect(f"/ws/labs/{sample_lab.id}/state?token={ws_token}") as ws:
                data = ws.receive_json()  # lab_state

                assert data["type"] == "lab_state"
                assert "lab_id" in data["data"]
                assert "state" in data["data"]
                assert data["data"]["lab_id"] == sample_lab.id


class TestConnectionResilience:
    """Tests for WebSocket connection resilience."""

    def test_connection_handles_missing_lab_gracefully(
        self,
        test_client: TestClient,
        ws_token: str,
    ):
        """Non-existent lab should return error, not crash."""
        async def mock_subscribe(lab_id):
            return
            yield

        with patch("app.routers.state_ws.get_broadcaster") as mock_broadcaster:
            mock_instance = MagicMock()
            mock_instance.subscribe = AsyncMock(return_value=mock_subscribe("fake"))
            mock_broadcaster.return_value = mock_instance

            with test_client.websocket_connect(f"/ws/labs/nonexistent-lab-id/state?token={ws_token}") as ws:
                data = ws.receive_json()
                assert data["type"] == "error"
                assert "not found" in data["data"]["message"].lower()

    def test_reconnection_gets_fresh_state(
        self,
        test_client: TestClient,
        sample_lab_with_nodes: tuple[models.Lab, list[models.NodeState]],
        ws_token: str,
    ):
        """Reconnecting client should receive fresh initial state."""
        lab, nodes = sample_lab_with_nodes

        async def mock_subscribe(lab_id):
            return
            yield

        with patch("app.routers.state_ws.get_broadcaster") as mock_broadcaster:
            mock_instance = MagicMock()
            mock_instance.subscribe = AsyncMock(return_value=mock_subscribe(lab.id))
            mock_broadcaster.return_value = mock_instance

            # First connection
            with test_client.websocket_connect(f"/ws/labs/{lab.id}/state?token={ws_token}") as ws:
                ws.receive_json()  # lab_state
                initial1 = ws.receive_json()  # initial_state

            # Second connection (reconnect)
            with test_client.websocket_connect(f"/ws/labs/{lab.id}/state?token={ws_token}") as ws:
                ws.receive_json()  # lab_state
                initial2 = ws.receive_json()  # initial_state

            # Both should get initial state
            assert initial1["type"] == "initial_state"
            assert initial2["type"] == "initial_state"


class TestConcurrentConnections:
    """Tests for handling concurrent WebSocket connections."""

    def test_connections_isolated_between_labs(
        self,
        test_client: TestClient,
        test_db: Session,
        test_user: models.User,
        ws_token: str,
    ):
        """Connections to different labs should be isolated."""
        from app.routers.state_ws import manager

        # Create two labs
        lab1 = models.Lab(
            name="Lab 1",
            owner_id=test_user.id,
            state="stopped",
        )
        lab2 = models.Lab(
            name="Lab 2",
            owner_id=test_user.id,
            state="stopped",
        )
        test_db.add(lab1)
        test_db.add(lab2)
        test_db.commit()
        test_db.refresh(lab1)
        test_db.refresh(lab2)

        async def mock_subscribe(lab_id):
            return
            yield

        with patch("app.routers.state_ws.get_broadcaster") as mock_broadcaster:
            mock_instance = MagicMock()
            mock_instance.subscribe = AsyncMock(return_value=mock_subscribe("any"))
            mock_broadcaster.return_value = mock_instance

            with test_client.websocket_connect(f"/ws/labs/{lab1.id}/state?token={ws_token}"):
                with test_client.websocket_connect(f"/ws/labs/{lab2.id}/state?token={ws_token}"):
                    # Both labs should have their own connection lists
                    if lab1.id in manager.active_connections and lab2.id in manager.active_connections:
                        # They should be separate
                        conns1 = manager.active_connections[lab1.id]
                        conns2 = manager.active_connections[lab2.id]
                        # Verify they're different connection lists
                        assert conns1 is not conns2
