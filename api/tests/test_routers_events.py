"""Tests for events router endpoints."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models


class TestNodeEventEndpoint:
    """Tests for POST /events/node."""

    def test_node_event_started(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab_with_nodes: tuple[models.Lab, list[models.NodeState]],
    ):
        """Test node started event updates state to running."""
        lab, nodes = sample_lab_with_nodes
        node = nodes[0]

        response = test_client.post(
            "/events/node",
            json={
                "agent_id": "test-agent",
                "lab_id": lab.id,
                "node_name": node.node_name,
                "container_id": "abc123",
                "event_type": "started",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "running",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

        test_db.refresh(node)
        assert node.actual_state == "running"
        assert node.error_message is None

    def test_node_event_stopped(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab_with_nodes: tuple[models.Lab, list[models.NodeState]],
    ):
        """Test node stopped event updates state to stopped."""
        lab, nodes = sample_lab_with_nodes
        node = nodes[0]
        node.actual_state = "running"
        test_db.commit()

        response = test_client.post(
            "/events/node",
            json={
                "agent_id": "test-agent",
                "lab_id": lab.id,
                "node_name": node.node_name,
                "event_type": "stopped",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "stopped",
            },
        )
        assert response.status_code == 200

        test_db.refresh(node)
        assert node.actual_state == "stopped"

    def test_node_event_died_with_sigkill(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab_with_nodes: tuple[models.Lab, list[models.NodeState]],
    ):
        """Test node died event with exit code 137 (SIGKILL) maps to stopped."""
        lab, nodes = sample_lab_with_nodes
        node = nodes[0]
        node.actual_state = "running"
        test_db.commit()

        response = test_client.post(
            "/events/node",
            json={
                "agent_id": "test-agent",
                "lab_id": lab.id,
                "node_name": node.node_name,
                "event_type": "died",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "exited (code 137)",  # SIGKILL
            },
        )
        assert response.status_code == 200

        test_db.refresh(node)
        assert node.actual_state == "stopped"
        assert node.error_message is None

    def test_node_event_died_with_error(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab_with_nodes: tuple[models.Lab, list[models.NodeState]],
    ):
        """Test node died event with abnormal exit code maps to error."""
        lab, nodes = sample_lab_with_nodes
        node = nodes[0]
        node.actual_state = "running"
        test_db.commit()

        response = test_client.post(
            "/events/node",
            json={
                "agent_id": "test-agent",
                "lab_id": lab.id,
                "node_name": node.node_name,
                "event_type": "died",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "exited (code 1)",
            },
        )
        assert response.status_code == 200

        test_db.refresh(node)
        assert node.actual_state == "error"
        assert "died" in node.error_message.lower()

    def test_node_event_oom(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab_with_nodes: tuple[models.Lab, list[models.NodeState]],
    ):
        """Test OOM event maps to error state."""
        lab, nodes = sample_lab_with_nodes
        node = nodes[0]
        node.actual_state = "running"
        test_db.commit()

        response = test_client.post(
            "/events/node",
            json={
                "agent_id": "test-agent",
                "lab_id": lab.id,
                "node_name": node.node_name,
                "event_type": "oom",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "killed by OOM",
            },
        )
        assert response.status_code == 200

        test_db.refresh(node)
        assert node.actual_state == "error"

    def test_node_event_creating(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab_with_nodes: tuple[models.Lab, list[models.NodeState]],
    ):
        """Test creating event maps to pending state."""
        lab, nodes = sample_lab_with_nodes
        node = nodes[0]

        response = test_client.post(
            "/events/node",
            json={
                "agent_id": "test-agent",
                "lab_id": lab.id,
                "node_name": node.node_name,
                "event_type": "creating",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "creating",
            },
        )
        assert response.status_code == 200

        test_db.refresh(node)
        assert node.actual_state == "pending"

    def test_node_event_lab_not_found(
        self, test_client: TestClient
    ):
        """Test event for non-existent lab is ignored gracefully."""
        response = test_client.post(
            "/events/node",
            json={
                "agent_id": "test-agent",
                "lab_id": "nonexistent-lab",
                "node_name": "R1",
                "event_type": "started",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "running",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "not found" in data["message"].lower()

    def test_node_event_node_state_not_found(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
    ):
        """Test event for non-existent node state is ignored gracefully."""
        response = test_client.post(
            "/events/node",
            json={
                "agent_id": "test-agent",
                "lab_id": sample_lab.id,
                "node_name": "nonexistent-node",
                "event_type": "started",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "running",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "not found" in data["message"].lower()

    def test_node_event_prefix_matching(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab_with_nodes: tuple[models.Lab, list[models.NodeState]],
    ):
        """Test that lab can be found by prefix (truncated lab IDs)."""
        lab, nodes = sample_lab_with_nodes
        node = nodes[0]

        # Lab IDs may be truncated in labels, use a prefix
        lab_prefix = lab.id[:20] if len(lab.id) > 20 else lab.id

        response = test_client.post(
            "/events/node",
            json={
                "agent_id": "test-agent",
                "lab_id": lab_prefix,
                "node_name": node.node_name,
                "event_type": "started",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "running",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_node_event_unknown_type_ignored(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab_with_nodes: tuple[models.Lab, list[models.NodeState]],
    ):
        """Test that unknown event types are ignored."""
        lab, nodes = sample_lab_with_nodes
        node = nodes[0]
        original_state = node.actual_state

        response = test_client.post(
            "/events/node",
            json={
                "agent_id": "test-agent",
                "lab_id": lab.id,
                "node_name": node.node_name,
                "event_type": "unknown-event",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "unknown",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "ignored" in data["message"].lower()

        # State should not have changed
        test_db.refresh(node)
        assert node.actual_state == original_state

    def test_node_event_no_downgrade_from_stopped_on_die(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab_with_nodes: tuple[models.Lab, list[models.NodeState]],
    ):
        """Test that die event doesn't downgrade stopped to error (out of order events)."""
        lab, nodes = sample_lab_with_nodes
        node = nodes[0]
        node.actual_state = "stopped"
        test_db.commit()

        # Die event arrives after stop (out of order)
        response = test_client.post(
            "/events/node",
            json={
                "agent_id": "test-agent",
                "lab_id": lab.id,
                "node_name": node.node_name,
                "event_type": "died",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "exited (code 1)",
            },
        )
        assert response.status_code == 200

        # State should remain stopped, not become error
        test_db.refresh(node)
        assert node.actual_state == "stopped"


class TestBatchEventsEndpoint:
    """Tests for POST /events/batch."""

    def test_batch_events_empty(self, test_client: TestClient):
        """Test batch endpoint with empty events list."""
        response = test_client.post("/events/batch", json=[])
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "no events" in data["message"].lower()

    def test_batch_events_multiple(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab_with_nodes: tuple[models.Lab, list[models.NodeState]],
    ):
        """Test batch endpoint with multiple events."""
        lab, nodes = sample_lab_with_nodes

        events = [
            {
                "agent_id": "test-agent",
                "lab_id": lab.id,
                "node_name": nodes[0].node_name,
                "event_type": "started",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "running",
            },
            {
                "agent_id": "test-agent",
                "lab_id": lab.id,
                "node_name": nodes[1].node_name,
                "event_type": "started",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "running",
            },
        ]

        response = test_client.post("/events/batch", json=events)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "2 events" in data["message"]

        # Verify both nodes were updated
        for node in nodes:
            test_db.refresh(node)
            assert node.actual_state == "running"

    def test_batch_events_partial_success(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab_with_nodes: tuple[models.Lab, list[models.NodeState]],
    ):
        """Test batch endpoint processes valid events even if some fail."""
        lab, nodes = sample_lab_with_nodes

        events = [
            {
                "agent_id": "test-agent",
                "lab_id": lab.id,
                "node_name": nodes[0].node_name,
                "event_type": "started",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "running",
            },
            {
                "agent_id": "test-agent",
                "lab_id": "nonexistent-lab",
                "node_name": "nonexistent-node",
                "event_type": "started",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "running",
            },
        ]

        response = test_client.post("/events/batch", json=events)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        # Should process 1 event successfully
        assert "1 events" in data["message"]

        # First node should be updated
        test_db.refresh(nodes[0])
        assert nodes[0].actual_state == "running"

    def test_batch_events_processing_order(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab_with_nodes: tuple[models.Lab, list[models.NodeState]],
    ):
        """Test batch events are processed in order."""
        lab, nodes = sample_lab_with_nodes
        node = nodes[0]

        # Send events: creating -> started -> stopped
        events = [
            {
                "agent_id": "test-agent",
                "lab_id": lab.id,
                "node_name": node.node_name,
                "event_type": "creating",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "creating",
            },
            {
                "agent_id": "test-agent",
                "lab_id": lab.id,
                "node_name": node.node_name,
                "event_type": "started",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "running",
            },
            {
                "agent_id": "test-agent",
                "lab_id": lab.id,
                "node_name": node.node_name,
                "event_type": "stopped",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "stopped",
            },
        ]

        response = test_client.post("/events/batch", json=events)
        assert response.status_code == 200

        # Final state should be stopped
        test_db.refresh(node)
        assert node.actual_state == "stopped"


class TestEventTypeMapping:
    """Tests for event type to state mapping logic."""

    @pytest.mark.parametrize(
        "event_type,status,expected_state",
        [
            ("started", "running", "running"),
            ("stopped", "stopped", "stopped"),
            ("stop", "stopped", "stopped"),
            ("creating", "creating", "pending"),
            ("destroying", "destroying", "stopped"),
            ("died", "exited (code 137)", "stopped"),  # SIGKILL
            ("died", "exited (code 143)", "stopped"),  # SIGTERM
            ("died", "exited (code 1)", "error"),  # Error exit
            ("kill", "killed", "error"),
            ("oom", "OOM killed", "error"),
        ],
    )
    def test_event_type_mapping(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab_with_nodes: tuple[models.Lab, list[models.NodeState]],
        event_type: str,
        status: str,
        expected_state: str,
    ):
        """Test various event types map to correct states."""
        lab, nodes = sample_lab_with_nodes
        node = nodes[0]
        node.actual_state = "running"  # Start from running
        test_db.commit()

        response = test_client.post(
            "/events/node",
            json={
                "agent_id": "test-agent",
                "lab_id": lab.id,
                "node_name": node.node_name,
                "event_type": event_type,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": status,
            },
        )
        assert response.status_code == 200

        test_db.refresh(node)
        assert node.actual_state == expected_state
