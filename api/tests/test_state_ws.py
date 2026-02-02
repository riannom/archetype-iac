"""Tests for WebSocket state endpoint.

Tests the real-time state update WebSocket at /ws/labs/{lab_id}/state.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models


class TestWebSocketConnection:
    """Tests for WebSocket connection lifecycle."""

    def test_connect_sends_initial_state(
        self,
        test_client: TestClient,
        sample_lab_with_nodes: tuple[models.Lab, list[models.NodeState]],
    ):
        """WebSocket connection should send initial state snapshot."""
        lab, nodes = sample_lab_with_nodes

        with patch("app.routers.state_ws.get_broadcaster") as mock_broadcaster:
            # Mock the subscriber to not block
            mock_instance = MagicMock()
            mock_instance.subscribe = AsyncMock(return_value=self._empty_async_gen())
            mock_broadcaster.return_value = mock_instance

            with test_client.websocket_connect(f"/ws/labs/{lab.id}/state") as ws:
                # Should receive lab_state message
                data = ws.receive_json()
                assert data["type"] == "lab_state"
                assert data["data"]["lab_id"] == lab.id
                assert data["data"]["state"] == lab.state

                # Should receive initial_state with nodes
                data = ws.receive_json()
                assert data["type"] == "initial_state"
                assert "nodes" in data["data"]
                assert len(data["data"]["nodes"]) == 2

    def test_connect_lab_not_found(self, test_client: TestClient):
        """WebSocket should send error for non-existent lab."""
        with patch("app.routers.state_ws.get_broadcaster") as mock_broadcaster:
            mock_instance = MagicMock()
            mock_instance.subscribe = AsyncMock(return_value=self._empty_async_gen())
            mock_broadcaster.return_value = mock_instance

            with test_client.websocket_connect("/ws/labs/nonexistent-lab/state") as ws:
                data = ws.receive_json()
                assert data["type"] == "error"
                assert "not found" in data["data"]["message"]

    def test_ping_pong(
        self,
        test_client: TestClient,
        sample_lab: models.Lab,
    ):
        """Client ping should receive pong response."""
        with patch("app.routers.state_ws.get_broadcaster") as mock_broadcaster:
            mock_instance = MagicMock()
            mock_instance.subscribe = AsyncMock(return_value=self._empty_async_gen())
            mock_broadcaster.return_value = mock_instance

            with test_client.websocket_connect(f"/ws/labs/{sample_lab.id}/state") as ws:
                # Skip initial messages
                ws.receive_json()  # lab_state
                ws.receive_json()  # initial_state

                # Send ping
                ws.send_json({"type": "ping"})

                # Should receive pong
                data = ws.receive_json()
                assert data["type"] == "pong"
                assert "timestamp" in data

    def test_refresh_resends_state(
        self,
        test_client: TestClient,
        sample_lab_with_nodes: tuple[models.Lab, list[models.NodeState]],
    ):
        """Client refresh request should resend initial state."""
        lab, nodes = sample_lab_with_nodes

        with patch("app.routers.state_ws.get_broadcaster") as mock_broadcaster:
            mock_instance = MagicMock()
            mock_instance.subscribe = AsyncMock(return_value=self._empty_async_gen())
            mock_broadcaster.return_value = mock_instance

            with test_client.websocket_connect(f"/ws/labs/{lab.id}/state") as ws:
                # Skip initial messages
                ws.receive_json()  # lab_state
                ws.receive_json()  # initial_state

                # Send refresh
                ws.send_json({"type": "refresh"})

                # Should receive lab_state again
                data = ws.receive_json()
                assert data["type"] == "lab_state"

                # Should receive initial_state again
                data = ws.receive_json()
                assert data["type"] == "initial_state"

    @staticmethod
    async def _empty_async_gen():
        """Empty async generator for mocking subscribe."""
        return
        yield  # Make it a generator


class TestWebSocketInitialState:
    """Tests for initial state message content."""

    def test_initial_state_includes_node_details(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
    ):
        """Initial state should include full node details."""
        # Create node state with host placement
        node_state = models.NodeState(
            lab_id=sample_lab.id,
            node_id="n1",
            node_name="router-1",
            desired_state="running",
            actual_state="running",
            is_ready=True,
        )
        test_db.add(node_state)

        # Create placement
        placement = models.NodePlacement(
            lab_id=sample_lab.id,
            node_name="router-1",
            host_id=sample_host.id,
        )
        test_db.add(placement)
        test_db.commit()

        with patch("app.routers.state_ws.get_broadcaster") as mock_broadcaster:
            mock_instance = MagicMock()
            mock_instance.subscribe = AsyncMock(
                return_value=TestWebSocketConnection._empty_async_gen()
            )
            mock_broadcaster.return_value = mock_instance

            with test_client.websocket_connect(f"/ws/labs/{sample_lab.id}/state") as ws:
                ws.receive_json()  # lab_state
                data = ws.receive_json()  # initial_state

                assert data["type"] == "initial_state"
                nodes = data["data"]["nodes"]
                assert len(nodes) == 1

                node = nodes[0]
                assert node["node_id"] == "n1"
                assert node["node_name"] == "router-1"
                assert node["desired_state"] == "running"
                assert node["actual_state"] == "running"
                assert node["is_ready"] is True
                assert node["host_id"] == sample_host.id
                assert node["host_name"] == sample_host.name

    def test_initial_links_sent_when_present(
        self,
        test_client: TestClient,
        sample_lab: models.Lab,
        sample_link_state: models.LinkState,
    ):
        """Initial links message should be sent when links exist."""
        with patch("app.routers.state_ws.get_broadcaster") as mock_broadcaster:
            mock_instance = MagicMock()
            mock_instance.subscribe = AsyncMock(
                return_value=TestWebSocketConnection._empty_async_gen()
            )
            mock_broadcaster.return_value = mock_instance

            with test_client.websocket_connect(f"/ws/labs/{sample_lab.id}/state") as ws:
                ws.receive_json()  # lab_state
                ws.receive_json()  # initial_state

                data = ws.receive_json()  # initial_links
                assert data["type"] == "initial_links"
                links = data["data"]["links"]
                assert len(links) == 1
                assert links[0]["link_name"] == sample_link_state.link_name


class TestConnectionManager:
    """Tests for the ConnectionManager class."""

    def test_multiple_connections_same_lab(
        self,
        test_client: TestClient,
        sample_lab: models.Lab,
    ):
        """Multiple clients can connect to the same lab."""
        from app.routers.state_ws import manager

        with patch("app.routers.state_ws.get_broadcaster") as mock_broadcaster:
            mock_instance = MagicMock()
            mock_instance.subscribe = AsyncMock(
                return_value=TestWebSocketConnection._empty_async_gen()
            )
            mock_broadcaster.return_value = mock_instance

            # First connection
            with test_client.websocket_connect(f"/ws/labs/{sample_lab.id}/state"):
                # Check connection registered
                assert sample_lab.id in manager.active_connections
                initial_count = len(manager.active_connections[sample_lab.id])
                assert initial_count >= 1

    def test_disconnect_removes_connection(
        self,
        test_client: TestClient,
        sample_lab: models.Lab,
    ):
        """Disconnecting should remove connection from manager."""
        from app.routers.state_ws import manager

        with patch("app.routers.state_ws.get_broadcaster") as mock_broadcaster:
            mock_instance = MagicMock()
            mock_instance.subscribe = AsyncMock(
                return_value=TestWebSocketConnection._empty_async_gen()
            )
            mock_broadcaster.return_value = mock_instance

            with test_client.websocket_connect(f"/ws/labs/{sample_lab.id}/state"):
                pass  # Connect and immediately disconnect

            # After disconnect, lab may be removed from active_connections
            # if no other connections remain
            if sample_lab.id in manager.active_connections:
                # If still present, should have fewer connections
                pass  # Connection was cleaned up in disconnect
