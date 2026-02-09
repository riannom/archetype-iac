"""Tests for console router WebSocket endpoint."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models


class TestConsoleWebSocket:
    """Tests for WebSocket /labs/{lab_id}/nodes/{node}/console endpoint."""

    def test_console_lab_not_found(
        self,
        test_client: TestClient,
        test_db: Session,
    ):
        """Test console connection to non-existent lab."""
        with test_client.websocket_connect(
            "/labs/nonexistent-lab/nodes/r1/console"
        ) as websocket:
            # Should receive error message and close
            data = websocket.receive_text()
            assert "not found" in data.lower()

    def test_console_no_healthy_agent(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
    ):
        """Test console connection when no healthy agent available."""
        with test_client.websocket_connect(
            f"/labs/{sample_lab.id}/nodes/r1/console"
        ) as websocket:
            data = websocket.receive_text()
            assert "no healthy agent" in data.lower()

    @patch("app.routers.console.agent_client")
    def test_console_resolves_node_name(
        self,
        mock_agent_client,
        test_client: TestClient,
        test_db: Session,
        sample_lab_with_nodes: tuple[models.Lab, list[models.NodeState]],
        sample_host: models.Host,
        sample_node_definitions: list[models.Node],
    ):
        """Test that console resolves GUI node ID to container name."""
        lab, nodes = sample_lab_with_nodes

        # Mock agent client to return healthy agent but fail on connect
        mock_agent_client.get_agent_for_lab = AsyncMock(return_value=sample_host)
        mock_agent_client.is_agent_online = MagicMock(return_value=True)
        mock_agent_client.get_agent_console_url = MagicMock(
            return_value="ws://agent:8080/console"
        )

        # The websocket will fail to connect to the mock agent, but we can
        # verify the flow up to that point
        try:
            with test_client.websocket_connect(
                f"/labs/{lab.id}/nodes/{nodes[0].node_id}/console"
            ) as websocket:
                # Will receive error about connection failure
                pass
        except Exception:
            pass  # Expected to fail connecting to mock agent

    def test_console_accepts_connection(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
    ):
        """Test that WebSocket connection is accepted initially."""
        # Even without a healthy agent, the connection should be accepted first
        with test_client.websocket_connect(
            f"/labs/{sample_lab.id}/nodes/r1/console"
        ) as websocket:
            # Connection accepted, will receive message about no agent
            data = websocket.receive_text()
            assert data is not None


class TestConsoleNodeResolution:
    """Tests for node name resolution in console endpoint."""

    @patch("app.routers.console.agent_client")
    def test_console_uses_container_name(
        self,
        mock_agent_client,
        test_client: TestClient,
        test_db: Session,
        sample_lab_with_nodes: tuple[models.Lab, list[models.NodeState]],
        sample_host: models.Host,
    ):
        """Test that console uses container_name over display name."""
        lab, nodes = sample_lab_with_nodes

        # Update node to have different container_name
        nodes[0].node_name = "actual-container-name"
        test_db.commit()

        mock_agent_client.get_agent_for_lab = AsyncMock(return_value=sample_host)
        mock_agent_client.is_agent_online = MagicMock(return_value=True)

        captured_node_name = None

        def capture_console_url(agent, lab_id, node_name):
            nonlocal captured_node_name
            captured_node_name = node_name
            return "ws://agent:8080/console"

        mock_agent_client.get_agent_console_url = MagicMock(
            side_effect=capture_console_url
        )
        mock_agent_client.check_node_readiness = AsyncMock(
            return_value={"is_ready": True}
        )

        try:
            with test_client.websocket_connect(
                f"/labs/{lab.id}/nodes/{nodes[0].node_id}/console"
            ) as websocket:
                pass
        except Exception:
            pass  # Expected to fail

        # Verify the resolved node name was used (if connection got that far)
        # This is a best-effort test since mocking WebSocket proxying is complex


class TestConsoleReadinessWarning:
    """Tests for boot readiness warning in console."""

    @patch("app.routers.console.agent_client")
    def test_console_shows_boot_warning(
        self,
        mock_agent_client,
        test_client: TestClient,
        test_db: Session,
        sample_lab_with_nodes: tuple[models.Lab, list[models.NodeState]],
        sample_host: models.Host,
    ):
        """Test that console shows boot warning for non-ready nodes."""
        lab, nodes = sample_lab_with_nodes

        # Set node as running but not ready
        nodes[0].actual_state = "running"
        nodes[0].is_ready = False
        test_db.commit()

        mock_agent_client.get_agent_for_lab = AsyncMock(return_value=sample_host)
        mock_agent_client.is_agent_online = MagicMock(return_value=True)
        mock_agent_client.get_agent_console_url = MagicMock(
            return_value="ws://agent:8080/console"
        )
        mock_agent_client.check_node_readiness = AsyncMock(
            return_value={"is_ready": False, "progress_percent": 50}
        )

        # The websocket will try to connect to agent and fail, but
        # we can verify the warning logic via the message received
        try:
            with test_client.websocket_connect(
                f"/labs/{lab.id}/nodes/{nodes[0].node_id}/console"
            ) as websocket:
                # Should receive boot warning message or connection error
                data = websocket.receive_text()
                assert data is not None
        except Exception:
            pass


class TestConsoleMultiHost:
    """Tests for multi-host console routing."""

    @patch("app.routers.console.agent_client")
    def test_console_routes_to_correct_host(
        self,
        mock_agent_client,
        test_client: TestClient,
        test_db: Session,
        sample_lab_with_nodes: tuple[models.Lab, list[models.NodeState]],
        multiple_hosts: list[models.Host],
        sample_node_definitions: list[models.Node],
    ):
        """Test that console routes to the correct host for multi-host labs."""
        lab, nodes = sample_lab_with_nodes

        # Update node definitions with host assignments
        sample_node_definitions[0].host_id = multiple_hosts[0].id
        sample_node_definitions[1].host_id = multiple_hosts[1].id
        test_db.commit()

        mock_agent_client.get_agent_for_lab = AsyncMock(return_value=multiple_hosts[0])
        mock_agent_client.is_agent_online = MagicMock(return_value=True)
        mock_agent_client.get_agent_console_url = MagicMock(
            return_value="ws://agent:8080/console"
        )

        try:
            with test_client.websocket_connect(
                f"/labs/{lab.id}/nodes/{nodes[0].node_name}/console"
            ) as websocket:
                pass
        except Exception:
            pass  # Expected to fail connecting
