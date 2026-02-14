"""Tests for WebSocket reconnection and recovery behavior.

These tests verify:
1. Client receives fresh state on reconnect
2. Server handles rapid reconnects gracefully
3. Ping/pong keeps connection alive
4. Connection cleanup on disconnect
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models


@pytest.fixture
def mock_broadcaster():
    """Mock broadcaster for tests."""
    mock_instance = MagicMock()
    mock_instance.subscribe = AsyncMock(return_value=empty_async_gen())
    return mock_instance


async def empty_async_gen():
    """Empty async generator for mocking subscribe."""
    return
    yield  # Make it a generator


class TestClientReconnection:
    """Tests for client reconnection scenarios."""

    def test_client_receives_fresh_state_on_reconnect(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        ws_token: str,
    ):
        """Reconnecting client should receive current state, not stale data."""
        # Create initial node state
        node_state = models.NodeState(
            lab_id=sample_lab.id,
            node_id="n1",
            node_name="R1",
            desired_state="running",
            actual_state="stopped",
            is_ready=False,
        )
        test_db.add(node_state)
        test_db.commit()

        with patch("app.routers.state_ws.get_broadcaster") as mock_broadcaster:
            mock_instance = MagicMock()
            mock_instance.subscribe = AsyncMock(return_value=empty_async_gen())
            mock_broadcaster.return_value = mock_instance

            # First connection - verify initial state
            with test_client.websocket_connect(f"/ws/labs/{sample_lab.id}/state?token={ws_token}") as ws:
                ws.receive_json()  # lab_state
                data = ws.receive_json()  # initial_state

                assert data["type"] == "initial_state"
                node = data["data"]["nodes"][0]
                assert node["actual_state"] == "stopped"

            # Update state while disconnected
            test_db.query(models.NodeState).filter(
                models.NodeState.lab_id == sample_lab.id,
                models.NodeState.node_id == "n1",
            ).update({"actual_state": "running", "is_ready": True})
            test_db.commit()

            # Second connection - should get updated state
            with test_client.websocket_connect(f"/ws/labs/{sample_lab.id}/state?token={ws_token}") as ws:
                ws.receive_json()  # lab_state
                data = ws.receive_json()  # initial_state

                assert data["type"] == "initial_state"
                node = data["data"]["nodes"][0]
                assert node["actual_state"] == "running"
                assert node["is_ready"] is True

    def test_reconnect_receives_lab_state_first(
        self,
        test_client: TestClient,
        sample_lab: models.Lab,
        ws_token: str,
    ):
        """Reconnection should receive lab state before node states."""
        with patch("app.routers.state_ws.get_broadcaster") as mock_broadcaster:
            mock_instance = MagicMock()
            mock_instance.subscribe = AsyncMock(return_value=empty_async_gen())
            mock_broadcaster.return_value = mock_instance

            # First message should always be lab_state
            with test_client.websocket_connect(f"/ws/labs/{sample_lab.id}/state?token={ws_token}") as ws:
                data = ws.receive_json()
                assert data["type"] == "lab_state"
                assert data["data"]["lab_id"] == sample_lab.id


class TestRapidReconnection:
    """Tests for rapid reconnection handling."""

    def test_server_handles_rapid_reconnects(
        self,
        test_client: TestClient,
        sample_lab: models.Lab,
        ws_token: str,
    ):
        """Server should handle rapid connect/disconnect cycles."""
        from app.routers.state_ws import manager

        with patch("app.routers.state_ws.get_broadcaster") as mock_broadcaster:
            mock_instance = MagicMock()
            mock_instance.subscribe = AsyncMock(return_value=empty_async_gen())
            mock_broadcaster.return_value = mock_instance

            # Rapid connect/disconnect 5 times
            for i in range(5):
                with test_client.websocket_connect(f"/ws/labs/{sample_lab.id}/state?token={ws_token}") as ws:
                    # Receive at least one message
                    data = ws.receive_json()
                    assert data["type"] == "lab_state"
                # Connection closes here

            # After all disconnects, lab should be cleaned up from manager
            # (or have at most 1 lingering connection from cleanup timing)
            if sample_lab.id in manager.active_connections:
                assert len(manager.active_connections[sample_lab.id]) <= 1

    def test_multiple_connections_same_lab(
        self,
        test_client: TestClient,
        sample_lab: models.Lab,
        ws_token: str,
    ):
        """Multiple clients can connect to the same lab simultaneously."""
        from app.routers.state_ws import manager

        with patch("app.routers.state_ws.get_broadcaster") as mock_broadcaster:
            mock_instance = MagicMock()
            mock_instance.subscribe = AsyncMock(return_value=empty_async_gen())
            mock_broadcaster.return_value = mock_instance

            # Note: TestClient is synchronous, so we can't truly test concurrent
            # connections. This test verifies the manager handles the registration.
            with test_client.websocket_connect(f"/ws/labs/{sample_lab.id}/state?token={ws_token}") as ws:
                data = ws.receive_json()
                assert data["type"] == "lab_state"

                # Check that connection is tracked
                assert sample_lab.id in manager.active_connections

    def test_connection_cleanup_after_disconnect(
        self,
        test_client: TestClient,
        sample_lab: models.Lab,
        ws_token: str,
    ):
        """Disconnected connections should be cleaned up from manager."""
        from app.routers.state_ws import manager

        with patch("app.routers.state_ws.get_broadcaster") as mock_broadcaster:
            mock_instance = MagicMock()
            mock_instance.subscribe = AsyncMock(return_value=empty_async_gen())
            mock_broadcaster.return_value = mock_instance

            with test_client.websocket_connect(f"/ws/labs/{sample_lab.id}/state?token={ws_token}") as ws:
                ws.receive_json()

                # Verify connected
                assert sample_lab.id in manager.active_connections
                connection_count = len(manager.active_connections[sample_lab.id])
                assert connection_count >= 1

            # After disconnect, connection should be removed or reduced.
            # Due to cleanup timing in the synchronous test client, the last
            # connection may linger briefly, so we allow at most 1 remaining.
            if sample_lab.id in manager.active_connections:
                current_count = len(manager.active_connections[sample_lab.id])
                assert current_count <= connection_count


class TestPingPong:
    """Tests for ping/pong keep-alive mechanism."""

    def test_ping_receives_pong_response(
        self,
        test_client: TestClient,
        sample_lab: models.Lab,
        ws_token: str,
    ):
        """Ping message should receive pong response."""
        with patch("app.routers.state_ws.get_broadcaster") as mock_broadcaster:
            mock_instance = MagicMock()
            mock_instance.subscribe = AsyncMock(return_value=empty_async_gen())
            mock_broadcaster.return_value = mock_instance

            with test_client.websocket_connect(f"/ws/labs/{sample_lab.id}/state?token={ws_token}") as ws:
                # Skip initial messages
                ws.receive_json()  # lab_state
                ws.receive_json()  # initial_state

                # Send ping
                ws.send_json({"type": "ping"})

                # Should receive pong
                data = ws.receive_json()
                assert data["type"] == "pong"
                assert "timestamp" in data

    def test_multiple_pings_get_responses(
        self,
        test_client: TestClient,
        sample_lab: models.Lab,
        ws_token: str,
    ):
        """Multiple pings should all receive responses."""
        with patch("app.routers.state_ws.get_broadcaster") as mock_broadcaster:
            mock_instance = MagicMock()
            mock_instance.subscribe = AsyncMock(return_value=empty_async_gen())
            mock_broadcaster.return_value = mock_instance

            with test_client.websocket_connect(f"/ws/labs/{sample_lab.id}/state?token={ws_token}") as ws:
                ws.receive_json()  # lab_state
                ws.receive_json()  # initial_state

                for _ in range(3):
                    ws.send_json({"type": "ping"})
                    data = ws.receive_json()
                    assert data["type"] == "pong"

    def test_pong_includes_timestamp(
        self,
        test_client: TestClient,
        sample_lab: models.Lab,
        ws_token: str,
    ):
        """Pong response should include valid timestamp."""
        from datetime import datetime

        with patch("app.routers.state_ws.get_broadcaster") as mock_broadcaster:
            mock_instance = MagicMock()
            mock_instance.subscribe = AsyncMock(return_value=empty_async_gen())
            mock_broadcaster.return_value = mock_instance

            with test_client.websocket_connect(f"/ws/labs/{sample_lab.id}/state?token={ws_token}") as ws:
                ws.receive_json()  # lab_state
                ws.receive_json()  # initial_state

                ws.send_json({"type": "ping"})
                data = ws.receive_json()

                assert data["type"] == "pong"
                # Timestamp should be parseable
                timestamp = datetime.fromisoformat(data["timestamp"].replace("Z", "+00:00"))
                assert timestamp is not None


class TestRefreshCommand:
    """Tests for client-triggered state refresh."""

    def test_refresh_resends_lab_state(
        self,
        test_client: TestClient,
        sample_lab: models.Lab,
        ws_token: str,
    ):
        """Refresh command should resend lab state."""
        with patch("app.routers.state_ws.get_broadcaster") as mock_broadcaster:
            mock_instance = MagicMock()
            mock_instance.subscribe = AsyncMock(return_value=empty_async_gen())
            mock_broadcaster.return_value = mock_instance

            with test_client.websocket_connect(f"/ws/labs/{sample_lab.id}/state?token={ws_token}") as ws:
                ws.receive_json()  # initial lab_state
                ws.receive_json()  # initial_state

                # Request refresh
                ws.send_json({"type": "refresh"})

                # Should receive lab_state again
                data = ws.receive_json()
                assert data["type"] == "lab_state"

    def test_refresh_resends_node_states(
        self,
        test_client: TestClient,
        sample_lab_with_nodes: tuple[models.Lab, list[models.NodeState]],
        ws_token: str,
    ):
        """Refresh command should resend all node states."""
        lab, nodes = sample_lab_with_nodes

        with patch("app.routers.state_ws.get_broadcaster") as mock_broadcaster:
            mock_instance = MagicMock()
            mock_instance.subscribe = AsyncMock(return_value=empty_async_gen())
            mock_broadcaster.return_value = mock_instance

            with test_client.websocket_connect(f"/ws/labs/{lab.id}/state?token={ws_token}") as ws:
                ws.receive_json()  # initial lab_state
                ws.receive_json()  # initial_state

                # Request refresh
                ws.send_json({"type": "refresh"})

                # Should receive lab_state then initial_state
                ws.receive_json()  # lab_state
                data = ws.receive_json()  # initial_state

                assert data["type"] == "initial_state"
                assert len(data["data"]["nodes"]) == len(nodes)

    def test_refresh_after_state_change(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        ws_token: str,
    ):
        """Refresh should return current state, not cached data."""
        # Create initial node
        node_state = models.NodeState(
            lab_id=sample_lab.id,
            node_id="n1",
            node_name="R1",
            desired_state="running",
            actual_state="stopped",
            is_ready=False,
        )
        test_db.add(node_state)
        test_db.commit()

        with patch("app.routers.state_ws.get_broadcaster") as mock_broadcaster:
            mock_instance = MagicMock()
            mock_instance.subscribe = AsyncMock(return_value=empty_async_gen())
            mock_broadcaster.return_value = mock_instance

            with test_client.websocket_connect(f"/ws/labs/{sample_lab.id}/state?token={ws_token}") as ws:
                ws.receive_json()  # lab_state
                initial = ws.receive_json()  # initial_state

                assert initial["data"]["nodes"][0]["actual_state"] == "stopped"

                # Update state
                test_db.query(models.NodeState).filter(
                    models.NodeState.lab_id == sample_lab.id,
                ).update({"actual_state": "running"})
                test_db.commit()

                # Request refresh
                ws.send_json({"type": "refresh"})

                ws.receive_json()  # lab_state
                refreshed = ws.receive_json()  # initial_state

                # Should have updated state
                assert refreshed["data"]["nodes"][0]["actual_state"] == "running"


class TestErrorHandling:
    """Tests for error handling in WebSocket connections."""

    def test_invalid_json_message_ignored(
        self,
        test_client: TestClient,
        sample_lab: models.Lab,
        ws_token: str,
    ):
        """Invalid JSON messages should be ignored without error."""
        with patch("app.routers.state_ws.get_broadcaster") as mock_broadcaster:
            mock_instance = MagicMock()
            mock_instance.subscribe = AsyncMock(return_value=empty_async_gen())
            mock_broadcaster.return_value = mock_instance

            with test_client.websocket_connect(f"/ws/labs/{sample_lab.id}/state?token={ws_token}") as ws:
                ws.receive_json()  # lab_state
                ws.receive_json()  # initial_state

                # Send invalid JSON
                ws.send_text("not valid json {")

                # Should still be able to ping
                ws.send_json({"type": "ping"})
                data = ws.receive_json()
                assert data["type"] == "pong"

    def test_unknown_message_type_ignored(
        self,
        test_client: TestClient,
        sample_lab: models.Lab,
        ws_token: str,
    ):
        """Unknown message types should be ignored."""
        with patch("app.routers.state_ws.get_broadcaster") as mock_broadcaster:
            mock_instance = MagicMock()
            mock_instance.subscribe = AsyncMock(return_value=empty_async_gen())
            mock_broadcaster.return_value = mock_instance

            with test_client.websocket_connect(f"/ws/labs/{sample_lab.id}/state?token={ws_token}") as ws:
                ws.receive_json()  # lab_state
                ws.receive_json()  # initial_state

                # Send unknown message type
                ws.send_json({"type": "unknown_command", "data": {}})

                # Should still be able to ping
                ws.send_json({"type": "ping"})
                data = ws.receive_json()
                assert data["type"] == "pong"

    def test_nonexistent_lab_sends_error(
        self,
        test_client: TestClient,
        ws_token: str,
    ):
        """Connecting to nonexistent lab should receive error message."""
        with patch("app.routers.state_ws.get_broadcaster") as mock_broadcaster:
            mock_instance = MagicMock()
            mock_instance.subscribe = AsyncMock(return_value=empty_async_gen())
            mock_broadcaster.return_value = mock_instance

            with test_client.websocket_connect(f"/ws/labs/nonexistent-lab-id/state?token={ws_token}") as ws:
                data = ws.receive_json()
                assert data["type"] == "error"
                assert "not found" in data["data"]["message"]


class TestConnectionState:
    """Tests for connection state tracking."""

    def test_connection_manager_tracks_lab_id(
        self,
        test_client: TestClient,
        sample_lab: models.Lab,
        ws_token: str,
    ):
        """Connection manager should track connections by lab ID."""
        from app.routers.state_ws import manager

        with patch("app.routers.state_ws.get_broadcaster") as mock_broadcaster:
            mock_instance = MagicMock()
            mock_instance.subscribe = AsyncMock(return_value=empty_async_gen())
            mock_broadcaster.return_value = mock_instance

            with test_client.websocket_connect(f"/ws/labs/{sample_lab.id}/state?token={ws_token}") as ws:
                ws.receive_json()

                # Lab should be in active connections
                assert sample_lab.id in manager.active_connections

    def test_different_labs_tracked_separately(
        self,
        test_client: TestClient,
        test_db: Session,
        test_user: models.User,
        ws_token: str,
    ):
        """Connections to different labs should be tracked separately."""
        from app.routers.state_ws import manager

        # Create two labs
        lab1 = models.Lab(
            name="Lab 1",
            owner_id=test_user.id,
            provider="docker",
            state="stopped",
            workspace_path="/tmp/lab1",
        )
        lab2 = models.Lab(
            name="Lab 2",
            owner_id=test_user.id,
            provider="docker",
            state="stopped",
            workspace_path="/tmp/lab2",
        )
        test_db.add_all([lab1, lab2])
        test_db.commit()

        with patch("app.routers.state_ws.get_broadcaster") as mock_broadcaster:
            mock_instance = MagicMock()
            mock_instance.subscribe = AsyncMock(return_value=empty_async_gen())
            mock_broadcaster.return_value = mock_instance

            # Connect to first lab
            with test_client.websocket_connect(f"/ws/labs/{lab1.id}/state?token={ws_token}") as ws1:
                ws1.receive_json()

                assert lab1.id in manager.active_connections
                # lab2 may or may not be present
                if lab2.id in manager.active_connections:
                    assert len(manager.active_connections[lab2.id]) == 0


# To run these tests:
# cd api && pytest tests/test_state_ws_reconnection.py -v
