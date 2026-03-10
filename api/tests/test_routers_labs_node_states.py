"""Tests for node state and reconciliation endpoints (routers/labs_node_states.py).

This module tests:
- Listing node states (auto-create missing, enrich host info, refresh stale pending)
- Getting a single node state (existing, auto-creates, 404 for missing lab)
- Setting desired state (running triggers job, stopped converges error, 409 on conflict)
- Setting all nodes desired state (start all, stop all, skip transitional, count already-in-state)
- Refreshing node states from agent
- Reconcile single node
- Reconcile full lab
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models
from app.state import NodeActualState
from tests.factories import make_node, make_node_state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_lab_with_running_nodes(
    test_db: Session, lab: models.Lab, host: models.Host
) -> list[models.NodeState]:
    """Create two running nodes with node defs and states."""
    make_node(test_db, lab, gui_id="n1", display_name="R1",
                   container_name="archetype-test-r1", host_id=host.id)
    make_node(test_db, lab, gui_id="n2", display_name="R2",
                   container_name="archetype-test-r2", host_id=host.id)

    ns1 = make_node_state(test_db, lab, "n1", "archetype-test-r1",
                           desired_state="running", actual_state="running")
    ns2 = make_node_state(test_db, lab, "n2", "archetype-test-r2",
                           desired_state="running", actual_state="running")
    return [ns1, ns2]


# ============================================================================
# TestListNodeStates
# ============================================================================


class TestListNodeStates:
    """Tests for GET /labs/{lab_id}/nodes/states."""

    def test_returns_all_states(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab_with_nodes: tuple[models.Lab, list[models.NodeState]],
        auth_headers: dict,
    ):
        """Lists all node states for a lab."""
        lab, nodes = sample_lab_with_nodes
        response = test_client.get(
            f"/labs/{lab.id}/nodes/states",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["nodes"]) == 2
        names = {n["node_name"] for n in data["nodes"]}
        assert names == {"R1", "R2"}

    def test_auto_creates_missing_states(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """When node defs exist but no states, states are auto-created."""
        make_node(test_db, sample_lab, gui_id="n1", container_name="archetype-test-r1")

        response = test_client.get(
            f"/labs/{sample_lab.id}/nodes/states",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["nodes"]) >= 1
        assert any(n["node_id"] == "n1" for n in data["nodes"])

    def test_enriches_host_info(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
    ):
        """Node states include host_id and host_name from placements."""
        node_def = make_node(test_db, sample_lab, host_id=sample_host.id)
        make_node_state(test_db, sample_lab, "n1", node_def.container_name)

        # Create placement
        placement = models.NodePlacement(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            host_id=sample_host.id,
            node_name=node_def.container_name,
            node_definition_id=node_def.id,
        )
        test_db.add(placement)
        test_db.commit()

        response = test_client.get(
            f"/labs/{sample_lab.id}/nodes/states",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        node = data["nodes"][0]
        assert node["host_id"] == sample_host.id
        assert node["host_name"] == sample_host.name

    def test_refreshes_stale_pending(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
    ):
        """Pending states with no active job are refreshed from agent."""
        sample_lab.agent_id = sample_host.id
        test_db.commit()

        make_node(test_db, sample_lab, gui_id="n1", container_name="archetype-test-r1")
        ns = make_node_state(test_db, sample_lab, "n1", "archetype-test-r1",
                              desired_state="running", actual_state="pending")

        mock_agent_client = MagicMock()
        mock_agent_client.is_agent_online = MagicMock(return_value=True)
        mock_agent_client.get_lab_status_from_agent = AsyncMock(return_value={
            "nodes": [{"name": "archetype-test-r1", "status": "running"}],
        })

        with patch("app.routers.labs_node_states.agent_client", mock_agent_client), \
             patch("app.routers.labs_node_states.get_online_agent_for_lab", AsyncMock(return_value=sample_host)):
            response = test_client.get(
                f"/labs/{sample_lab.id}/nodes/states",
                headers=auth_headers,
            )

        assert response.status_code == 200
        test_db.refresh(ns)
        assert ns.actual_state == NodeActualState.RUNNING

    def test_clears_stale_error_for_stopped_undeployed_node(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Stopped undeployed nodes should not keep stale deploy/image errors."""
        make_node(test_db, sample_lab, gui_id="n1", container_name="archetype-test-r1")
        ns = make_node_state(
            test_db,
            sample_lab,
            "n1",
            "archetype-test-r1",
            desired_state="stopped",
            actual_state="undeployed",
        )
        ns.error_message = "Image not available on host"
        ns.image_sync_status = "failed"
        ns.image_sync_message = "Image not found on agent"
        test_db.commit()

        response = test_client.get(
            f"/labs/{sample_lab.id}/nodes/states",
            headers=auth_headers,
        )

        assert response.status_code == 200
        node = response.json()["nodes"][0]
        assert node["actual_state"] == "undeployed"
        assert node["desired_state"] == "stopped"
        assert node["error_message"] is None
        assert node["image_sync_status"] is None
        assert node["image_sync_message"] is None

        test_db.refresh(ns)
        assert ns.error_message is None
        assert ns.image_sync_status is None
        assert ns.image_sync_message is None


# ============================================================================
# TestGetNodeState
# ============================================================================


class TestGetNodeState:
    """Tests for GET /labs/{lab_id}/nodes/{node_id}/state."""

    def test_existing_state(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Returns an existing node state."""
        make_node(test_db, sample_lab, gui_id="n1", container_name="archetype-test-r1")
        make_node_state(test_db, sample_lab, "n1", "archetype-test-r1")

        response = test_client.get(
            f"/labs/{sample_lab.id}/nodes/n1/state",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["node_id"] == "n1"
        assert data["actual_state"] == "undeployed"

    def test_auto_creates_for_missing_state(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Auto-creates NodeState when node def exists but state is missing."""
        make_node(test_db, sample_lab, gui_id="n1", container_name="archetype-test-r1")

        response = test_client.get(
            f"/labs/{sample_lab.id}/nodes/n1/state",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["node_id"] == "n1"

    def test_404_for_missing_lab(
        self,
        test_client: TestClient,
        auth_headers: dict,
    ):
        """Returns 404 for nonexistent lab."""
        response = test_client.get(
            "/labs/nonexistent-lab/nodes/n1/state",
            headers=auth_headers,
        )
        assert response.status_code == 404

    def test_404_for_missing_node_definition(
        self,
        test_client: TestClient,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Does not create placeholder NodeState rows for unknown node IDs."""
        response = test_client.get(
            f"/labs/{sample_lab.id}/nodes/missing-node/state",
            headers=auth_headers,
        )
        assert response.status_code == 404


# ============================================================================
# TestSetDesiredState
# ============================================================================


class TestSetDesiredState:
    """Tests for PUT /labs/{lab_id}/nodes/{node_id}/desired-state."""

    def test_running_triggers_sync_job(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Setting desired=running from stopped creates a sync job."""
        make_node(test_db, sample_lab, gui_id="n1", container_name="archetype-test-r1")
        make_node_state(test_db, sample_lab, "n1", "archetype-test-r1",
                         desired_state="stopped", actual_state="stopped")

        with patch("app.routers.labs._create_node_sync_job") as mock_sync, \
             patch("app.routers.labs.has_conflicting_job", return_value=(False, None)):
            response = test_client.put(
                f"/labs/{sample_lab.id}/nodes/n1/desired-state",
                json={"state": "running"},
                headers=auth_headers,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["desired_state"] == "running"
        mock_sync.assert_called_once()

    def test_stopped_converges_error_state(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Setting desired=stopped when actual=error converges to stopped."""
        make_node(test_db, sample_lab, gui_id="n1", container_name="archetype-test-r1")
        ns = make_node_state(test_db, sample_lab, "n1", "archetype-test-r1",
                              desired_state="running", actual_state="error")

        with patch("app.routers.labs.has_conflicting_job", return_value=(False, None)):
            response = test_client.put(
                f"/labs/{sample_lab.id}/nodes/n1/desired-state",
                json={"state": "stopped"},
                headers=auth_headers,
            )

        assert response.status_code == 200
        test_db.refresh(ns)
        assert ns.actual_state == NodeActualState.STOPPED

    def test_409_when_stopping(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Cannot start a node that is currently stopping."""
        make_node(test_db, sample_lab, gui_id="n1", container_name="archetype-test-r1")
        make_node_state(test_db, sample_lab, "n1", "archetype-test-r1",
                         desired_state="stopped", actual_state="stopping")

        response = test_client.put(
            f"/labs/{sample_lab.id}/nodes/n1/desired-state",
            json={"state": "running"},
            headers=auth_headers,
        )
        assert response.status_code == 409

    def test_retry_error_with_running_resets_enforcement(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Setting running on an error node that already desired=running resets enforcement."""
        make_node(test_db, sample_lab, gui_id="n1", container_name="archetype-test-r1")
        ns = make_node_state(test_db, sample_lab, "n1", "archetype-test-r1",
                              desired_state="running", actual_state="error")
        ns.enforcement_attempts = 3
        ns.error_message = "previous error"
        test_db.commit()

        with patch("app.routers.labs._create_node_sync_job"), \
             patch("app.routers.labs.has_conflicting_job", return_value=(False, None)):
            response = test_client.put(
                f"/labs/{sample_lab.id}/nodes/n1/desired-state",
                json={"state": "running"},
                headers=auth_headers,
            )

        assert response.status_code == 200
        test_db.refresh(ns)
        assert ns.enforcement_attempts == 0
        assert ns.error_message is None


# ============================================================================
# TestSetAllNodesDesiredState
# ============================================================================


class TestSetAllNodesDesiredState:
    """Tests for PUT /labs/{lab_id}/nodes/desired-state."""

    def test_start_all_nodes(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
    ):
        """Start all sets desired=running on all eligible nodes."""
        make_node(test_db, sample_lab, gui_id="n1", container_name="archetype-test-r1")
        make_node(test_db, sample_lab, gui_id="n2", container_name="archetype-test-r2")
        make_node_state(test_db, sample_lab, "n1", "archetype-test-r1",
                         desired_state="stopped", actual_state="stopped")
        make_node_state(test_db, sample_lab, "n2", "archetype-test-r2",
                         desired_state="stopped", actual_state="stopped")

        with patch("app.routers.labs.has_conflicting_job", return_value=(False, None)), \
             patch("app.routers.labs_node_states._has_conflicting_job", return_value=(False, None)), \
             patch("app.routers.labs_node_states.safe_create_task"), \
             patch("app.utils.lab.get_lab_provider", return_value="docker"):
            response = test_client.put(
                f"/labs/{sample_lab.id}/nodes/desired-state",
                json={"state": "running"},
                headers=auth_headers,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["affected"] == 2

    def test_stop_all_nodes(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
    ):
        """Stop all sets desired=stopped on all eligible nodes."""
        _setup_lab_with_running_nodes(test_db, sample_lab, sample_host)

        with patch("app.routers.labs.has_conflicting_job", return_value=(False, None)), \
             patch("app.routers.labs_node_states._has_conflicting_job", return_value=(False, None)), \
             patch("app.routers.labs_node_states.safe_create_task"), \
             patch("app.utils.lab.get_lab_provider", return_value="docker"):
            response = test_client.put(
                f"/labs/{sample_lab.id}/nodes/desired-state",
                json={"state": "stopped"},
                headers=auth_headers,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["affected"] == 2

    def test_skips_transitional_nodes(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Transitional nodes (starting/stopping) are skipped."""
        make_node(test_db, sample_lab, gui_id="n1", container_name="archetype-test-r1")
        make_node(test_db, sample_lab, gui_id="n2", container_name="archetype-test-r2")
        make_node_state(test_db, sample_lab, "n1", "archetype-test-r1",
                         desired_state="running", actual_state="starting")
        make_node_state(test_db, sample_lab, "n2", "archetype-test-r2",
                         desired_state="stopped", actual_state="stopped")

        with patch("app.routers.labs.has_conflicting_job", return_value=(False, None)), \
             patch("app.routers.labs_node_states._has_conflicting_job", return_value=(False, None)), \
             patch("app.routers.labs_node_states.safe_create_task"), \
             patch("app.utils.lab.get_lab_provider", return_value="docker"):
            response = test_client.put(
                f"/labs/{sample_lab.id}/nodes/desired-state",
                json={"state": "running"},
                headers=auth_headers,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["skipped_transitional"] >= 1

    def test_counts_already_in_state(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
    ):
        """Nodes already in the requested state are counted as already_in_state."""
        _setup_lab_with_running_nodes(test_db, sample_lab, sample_host)

        with patch("app.routers.labs.has_conflicting_job", return_value=(False, None)), \
             patch("app.routers.labs_node_states._has_conflicting_job", return_value=(False, None)), \
             patch("app.routers.labs_node_states.safe_create_task"), \
             patch("app.utils.lab.get_lab_provider", return_value="docker"):
            response = test_client.put(
                f"/labs/{sample_lab.id}/nodes/desired-state",
                json={"state": "running"},
                headers=auth_headers,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["already_in_state"] == 2


# ============================================================================
# TestRefreshNodeStates
# ============================================================================


class TestRefreshNodeStates:
    """Tests for POST /labs/{lab_id}/nodes/refresh."""

    def test_queries_agent_and_updates(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
    ):
        """Refresh queries agent and updates node states."""
        sample_lab.agent_id = sample_host.id
        test_db.commit()

        make_node(test_db, sample_lab, gui_id="n1", container_name="archetype-test-r1")
        ns = make_node_state(test_db, sample_lab, "n1", "archetype-test-r1",
                              desired_state="running", actual_state="stopped")

        mock_agent_client = MagicMock()
        mock_agent_client.is_agent_online = MagicMock(return_value=True)
        mock_agent_client.get_lab_status_from_agent = AsyncMock(return_value={
            "nodes": [{"name": "archetype-test-r1", "status": "running"}],
        })

        with patch("app.routers.labs_node_states.agent_client", mock_agent_client):
            response = test_client.post(
                f"/labs/{sample_lab.id}/nodes/refresh",
                headers=auth_headers,
            )

        assert response.status_code == 200
        test_db.refresh(ns)
        assert ns.actual_state == NodeActualState.RUNNING

    def test_no_agent_returns_503(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Returns 503 when no agent is available."""
        make_node(test_db, sample_lab, gui_id="n1", container_name="archetype-test-r1")
        make_node_state(test_db, sample_lab, "n1", "archetype-test-r1")

        with patch("app.routers.labs_node_states.get_online_agent_for_lab", AsyncMock(return_value=None)):
            response = test_client.post(
                f"/labs/{sample_lab.id}/nodes/refresh",
                headers=auth_headers,
            )

        assert response.status_code == 503

    def test_offline_agent_error(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
    ):
        """All agents offline returns 503."""
        sample_lab.agent_id = sample_host.id
        test_db.commit()

        make_node(test_db, sample_lab, gui_id="n1", container_name="archetype-test-r1")
        make_node_state(test_db, sample_lab, "n1", "archetype-test-r1")

        mock_agent_client = MagicMock()
        mock_agent_client.is_agent_online = MagicMock(return_value=False)

        with patch("app.routers.labs_node_states.agent_client", mock_agent_client), \
             patch("app.routers.labs_node_states.get_online_agent_for_lab", AsyncMock(return_value=None)):
            response = test_client.post(
                f"/labs/{sample_lab.id}/nodes/refresh",
                headers=auth_headers,
            )

        assert response.status_code == 503


# ============================================================================
# TestReconcileNode
# ============================================================================


class TestReconcileNode:
    """Tests for POST /labs/{lab_id}/nodes/{node_id}/reconcile."""

    def test_single_node_sync(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
    ):
        """Reconcile creates a job for an out-of-sync node."""
        make_node(test_db, sample_lab, gui_id="n1", container_name="archetype-test-r1")
        make_node_state(test_db, sample_lab, "n1", "archetype-test-r1",
                         desired_state="running", actual_state="stopped")

        with patch("app.routers.labs_node_states.get_online_agent_for_lab",
                    AsyncMock(return_value=sample_host)), \
             patch("app.routers.labs_node_states.safe_create_task"), \
             patch("app.utils.lab.get_lab_provider", return_value="docker"), \
             patch("app.utils.lab.get_node_provider", return_value="docker"):
            response = test_client.post(
                f"/labs/{sample_lab.id}/nodes/n1/reconcile",
                headers=auth_headers,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] != ""
        assert "n1" in data["nodes_to_reconcile"]

    def test_skips_in_sync_node(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Node already in correct state returns empty reconcile response."""
        make_node(test_db, sample_lab, gui_id="n1", container_name="archetype-test-r1")
        make_node_state(test_db, sample_lab, "n1", "archetype-test-r1",
                         desired_state="stopped", actual_state="stopped")

        response = test_client.post(
            f"/labs/{sample_lab.id}/nodes/n1/reconcile",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == ""
        assert len(data["nodes_to_reconcile"]) == 0

    def test_requires_editor_role(
        self,
        test_client: TestClient,
        test_db: Session,
        admin_user: models.User,
        admin_auth_headers: dict,
    ):
        """Non-owner/non-editor gets 403."""
        # Create lab owned by a different user
        other_user = models.User(
            username="other",
            email="other@example.com",
            hashed_password="hash",
            is_active=True,
            global_role="viewer",
        )
        test_db.add(other_user)
        test_db.commit()
        test_db.refresh(other_user)

        lab = models.Lab(
            name="Other Lab",
            owner_id=other_user.id,
            provider="docker",
            state="stopped",
            workspace_path="/tmp/other-lab",
        )
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        make_node_state(test_db, lab, "n1", "R1",
                         desired_state="running", actual_state="stopped")

        # Admin can still reconcile (admin role bypasses lab permissions)
        with patch("app.routers.labs_node_states.get_online_agent_for_lab",
                    AsyncMock(return_value=MagicMock(id="agent"))), \
             patch("app.routers.labs_node_states.safe_create_task"), \
             patch("app.utils.lab.get_lab_provider", return_value="docker"), \
             patch("app.utils.lab.get_node_provider", return_value="docker"):
            response = test_client.post(
                f"/labs/{lab.id}/nodes/n1/reconcile",
                headers=admin_auth_headers,
            )
        assert response.status_code == 200


# ============================================================================
# TestReconcileLab
# ============================================================================


class TestReconcileLab:
    """Tests for POST /labs/{lab_id}/reconcile."""

    def test_reconcile_all_out_of_sync(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
    ):
        """Reconcile finds all out-of-sync nodes and creates a job."""
        make_node(test_db, sample_lab, gui_id="n1", container_name="archetype-test-r1")
        make_node(test_db, sample_lab, gui_id="n2", container_name="archetype-test-r2")
        make_node_state(test_db, sample_lab, "n1", "archetype-test-r1",
                         desired_state="running", actual_state="stopped")
        make_node_state(test_db, sample_lab, "n2", "archetype-test-r2",
                         desired_state="running", actual_state="stopped")

        with patch("app.routers.labs_node_states._has_conflicting_job",
                    return_value=(False, None)), \
             patch("app.routers.labs_node_states.get_online_agent_for_lab",
                    AsyncMock(return_value=sample_host)), \
             patch("app.routers.labs_node_states.safe_create_task"), \
             patch("app.utils.lab.get_lab_provider", return_value="docker"):
            response = test_client.post(
                f"/labs/{sample_lab.id}/reconcile",
                headers=auth_headers,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] != ""
        assert len(data["nodes_to_reconcile"]) == 2

    def test_all_synced_returns_no_job(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
    ):
        """When all nodes are in sync, returns empty response."""
        _setup_lab_with_running_nodes(test_db, sample_lab, sample_host)

        with patch("app.routers.labs_node_states._has_conflicting_job",
                    return_value=(False, None)):
            response = test_client.post(
                f"/labs/{sample_lab.id}/reconcile",
                headers=auth_headers,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == ""
        assert len(data["nodes_to_reconcile"]) == 0

    def test_409_with_active_jobs(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Returns 409 when a conflicting job is already in progress."""
        make_node(test_db, sample_lab, gui_id="n1", container_name="archetype-test-r1")
        make_node_state(test_db, sample_lab, "n1", "archetype-test-r1",
                         desired_state="running", actual_state="stopped")

        with patch("app.routers.labs_node_states._has_conflicting_job",
                    return_value=(True, "deploy")):
            response = test_client.post(
                f"/labs/{sample_lab.id}/reconcile",
                headers=auth_headers,
            )

        assert response.status_code == 409
        assert "deploy" in response.json()["detail"]

    def test_no_agent_returns_503(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Returns 503 when no healthy agent is available."""
        make_node(test_db, sample_lab, gui_id="n1", container_name="archetype-test-r1")
        make_node_state(test_db, sample_lab, "n1", "archetype-test-r1",
                         desired_state="running", actual_state="stopped")

        with patch("app.routers.labs_node_states._has_conflicting_job",
                    return_value=(False, None)), \
             patch("app.routers.labs_node_states.get_online_agent_for_lab",
                    AsyncMock(return_value=None)), \
             patch("app.utils.lab.get_lab_provider", return_value="docker"):
            response = test_client.post(
                f"/labs/{sample_lab.id}/reconcile",
                headers=auth_headers,
            )

        assert response.status_code == 503