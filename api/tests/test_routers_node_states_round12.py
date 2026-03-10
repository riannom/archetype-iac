"""Round 12 deep-path tests for node state and reconciliation endpoints.

Targets under-tested paths in api/app/routers/labs_node_states.py:
- reconcile_lab: conflicting job detection, mixed sync/in-sync nodes, no agent
- reconcile_node: no agent available, node already in sync, node not in DB
- refresh_node_states: multi-agent querying, transitional state skipping,
  agent errors, stopped/exited mapping, fallback agent resolution
- list_node_states: pending refresh with stopped containers, agent exception
  during pending refresh, active job blocks pending refresh
- set_node_desired_state: conflicting job blocks sync, stopped-on-stopped
  convergence (no-op path), retry with conflict
- set_all_nodes_desired_state: error nodes reset_and_proceed, stop-all
  converges error nodes, no nodes needing sync
- _get_out_of_sync_nodes: desired=stopped with running actual, specific
  node_ids filter, empty lab
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models
from app.state import NodeActualState, NodeDesiredState
from tests.factories import make_node, make_node_state, make_placement


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# ============================================================================
# TestReconcileLabDeepPaths
# ============================================================================


class TestReconcileLabDeepPaths:
    """Deep-path tests for POST /labs/{lab_id}/reconcile."""

    def test_mixed_sync_and_out_of_sync_nodes(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
    ):
        """Only out-of-sync nodes appear in nodes_to_reconcile."""
        make_node(test_db, sample_lab, gui_id="n1", container_name="r1")
        make_node(test_db, sample_lab, gui_id="n2", container_name="r2")
        # n1 is in sync (desired=running, actual=running)
        make_node_state(test_db, sample_lab, "n1", "r1",
                         desired_state="running", actual_state="running")
        # n2 is out of sync (desired=running, actual=stopped)
        make_node_state(test_db, sample_lab, "n2", "r2",
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
        assert len(data["nodes_to_reconcile"]) == 1
        assert "n2" in data["nodes_to_reconcile"]

    def test_reconcile_lab_404_for_missing_lab(
        self,
        test_client: TestClient,
        auth_headers: dict,
    ):
        """Returns 404 for a nonexistent lab."""
        response = test_client.post(
            "/labs/nonexistent-lab-id/reconcile",
            headers=auth_headers,
        )
        assert response.status_code == 404

    def test_reconcile_lab_desired_stopped_actual_running(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
    ):
        """Nodes desired=stopped but actual=running are out of sync."""
        make_node(test_db, sample_lab, gui_id="n1", container_name="r1")
        make_node_state(test_db, sample_lab, "n1", "r1",
                         desired_state="stopped", actual_state="running")

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
        assert len(data["nodes_to_reconcile"]) == 1
        assert "n1" in data["nodes_to_reconcile"]

    def test_reconcile_lab_empty_lab_no_nodes(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Lab with no node states returns empty reconcile (no job)."""
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


# ============================================================================
# TestReconcileNodeDeepPaths
# ============================================================================


class TestReconcileNodeDeepPaths:
    """Deep-path tests for POST /labs/{lab_id}/nodes/{node_id}/reconcile."""

    def test_reconcile_node_no_agent_returns_503(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Returns 503 when no agent is available for the node."""
        make_node(test_db, sample_lab, gui_id="n1", container_name="r1")
        make_node_state(test_db, sample_lab, "n1", "r1",
                         desired_state="running", actual_state="stopped")

        with patch("app.routers.labs_node_states.get_online_agent_for_lab",
                    AsyncMock(return_value=None)), \
             patch("app.utils.lab.get_lab_provider", return_value="docker"), \
             patch("app.utils.lab.get_node_provider", return_value="docker"):
            response = test_client.post(
                f"/labs/{sample_lab.id}/nodes/n1/reconcile",
                headers=auth_headers,
            )

        assert response.status_code == 503

    def test_reconcile_node_desired_stopped_actual_undeployed_is_in_sync(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Desired=stopped, actual=undeployed is considered in-sync."""
        make_node(test_db, sample_lab, gui_id="n1", container_name="r1")
        make_node_state(test_db, sample_lab, "n1", "r1",
                         desired_state="stopped", actual_state="undeployed")

        response = test_client.post(
            f"/labs/{sample_lab.id}/nodes/n1/reconcile",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == ""
        assert data["nodes_to_reconcile"] == []

    def test_reconcile_node_uses_node_provider_when_db_node_exists(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
    ):
        """When DB node exists, get_node_provider is used (not get_lab_provider)."""
        make_node(test_db, sample_lab, gui_id="n1", container_name="r1",
                       device="ceos")
        make_node_state(test_db, sample_lab, "n1", "r1",
                         desired_state="running", actual_state="error")

        mock_get_node_provider = MagicMock(return_value="docker")

        with patch("app.routers.labs_node_states.get_online_agent_for_lab",
                    AsyncMock(return_value=sample_host)), \
             patch("app.routers.labs_node_states.safe_create_task"), \
             patch("app.utils.lab.get_lab_provider", return_value="libvirt") as mock_lab_prov, \
             patch("app.utils.lab.get_node_provider", mock_get_node_provider):
            response = test_client.post(
                f"/labs/{sample_lab.id}/nodes/n1/reconcile",
                headers=auth_headers,
            )

        assert response.status_code == 200
        mock_get_node_provider.assert_called_once()
        # get_lab_provider should NOT be called for provider determination
        # when the DB node is found
        mock_lab_prov.assert_not_called()


# ============================================================================
# TestRefreshNodeStatesDeepPaths
# ============================================================================


class TestRefreshNodeStatesDeepPaths:
    """Deep-path tests for POST /labs/{lab_id}/nodes/refresh."""

    def test_refresh_skips_stopping_transitional_state(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
    ):
        """Nodes in transitional 'stopping' state are not overwritten by refresh.

        The backup check (actual_state in transitional set) catches this even
        without timestamps, which avoids SQLite tz-naive/aware mismatch.
        """
        sample_lab.agent_id = sample_host.id
        test_db.commit()

        make_node(test_db, sample_lab, gui_id="n1", container_name="r1")
        ns = make_node_state(
            test_db, sample_lab, "n1", "r1",
            desired_state="stopped", actual_state="stopping",
        )

        mock_ac = MagicMock()
        mock_ac.is_agent_online = MagicMock(return_value=True)
        mock_ac.get_lab_status_from_agent = AsyncMock(return_value={
            "nodes": [{"name": "r1", "status": "running"}],
        })

        with patch("app.routers.labs_node_states.agent_client", mock_ac):
            response = test_client.post(
                f"/labs/{sample_lab.id}/nodes/refresh",
                headers=auth_headers,
            )

        assert response.status_code == 200
        test_db.refresh(ns)
        # Should still be stopping, not overwritten to running
        assert ns.actual_state == NodeActualState.STOPPING

    def test_refresh_skips_starting_transitional_state(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
    ):
        """Nodes in transitional 'starting' state are not overwritten by refresh."""
        sample_lab.agent_id = sample_host.id
        test_db.commit()

        make_node(test_db, sample_lab, gui_id="n1", container_name="r1")
        ns = make_node_state(
            test_db, sample_lab, "n1", "r1",
            desired_state="running", actual_state="starting",
        )

        mock_ac = MagicMock()
        mock_ac.is_agent_online = MagicMock(return_value=True)
        mock_ac.get_lab_status_from_agent = AsyncMock(return_value={
            "nodes": [{"name": "r1", "status": "running"}],
        })

        with patch("app.routers.labs_node_states.agent_client", mock_ac):
            response = test_client.post(
                f"/labs/{sample_lab.id}/nodes/refresh",
                headers=auth_headers,
            )

        assert response.status_code == 200
        test_db.refresh(ns)
        assert ns.actual_state == NodeActualState.STARTING

    def test_refresh_skips_pending_transitional_state(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
    ):
        """Nodes in transitional 'pending' state are not overwritten by refresh."""
        sample_lab.agent_id = sample_host.id
        test_db.commit()

        make_node(test_db, sample_lab, gui_id="n1", container_name="r1")
        ns = make_node_state(
            test_db, sample_lab, "n1", "r1",
            desired_state="running", actual_state="pending",
        )

        mock_ac = MagicMock()
        mock_ac.is_agent_online = MagicMock(return_value=True)
        mock_ac.get_lab_status_from_agent = AsyncMock(return_value={
            "nodes": [{"name": "r1", "status": "running"}],
        })

        with patch("app.routers.labs_node_states.agent_client", mock_ac):
            response = test_client.post(
                f"/labs/{sample_lab.id}/nodes/refresh",
                headers=auth_headers,
            )

        assert response.status_code == 200
        test_db.refresh(ns)
        assert ns.actual_state == NodeActualState.PENDING

    def test_refresh_updates_stopped_container(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
    ):
        """Container reporting 'exited' maps node to STOPPED state."""
        sample_lab.agent_id = sample_host.id
        test_db.commit()

        make_node(test_db, sample_lab, gui_id="n1", container_name="r1")
        ns = make_node_state(
            test_db, sample_lab, "n1", "r1",
            desired_state="stopped", actual_state="running",
            boot_started_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        )

        mock_ac = MagicMock()
        mock_ac.is_agent_online = MagicMock(return_value=True)
        mock_ac.get_lab_status_from_agent = AsyncMock(return_value={
            "nodes": [{"name": "r1", "status": "exited"}],
        })

        with patch("app.routers.labs_node_states.agent_client", mock_ac):
            response = test_client.post(
                f"/labs/{sample_lab.id}/nodes/refresh",
                headers=auth_headers,
            )

        assert response.status_code == 200
        test_db.refresh(ns)
        assert ns.actual_state == NodeActualState.STOPPED
        assert ns.boot_started_at is None
        assert ns.error_message is None

    def test_refresh_agent_error_still_merges_partial_nodes(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
    ):
        """When agent returns an error field, nodes are still merged but agent
        is not counted as successfully queried. If no agents succeed, 503."""
        sample_lab.agent_id = sample_host.id
        test_db.commit()

        make_node(test_db, sample_lab, gui_id="n1", container_name="r1")
        make_node_state(test_db, sample_lab, "n1", "r1",
                         desired_state="running", actual_state="stopped")

        mock_ac = MagicMock()
        mock_ac.is_agent_online = MagicMock(return_value=True)
        # Agent returns error alongside partial data
        mock_ac.get_lab_status_from_agent = AsyncMock(return_value={
            "nodes": [{"name": "r1", "status": "running"}],
            "error": "partial failure",
        })

        with patch("app.routers.labs_node_states.agent_client", mock_ac):
            response = test_client.post(
                f"/labs/{sample_lab.id}/nodes/refresh",
                headers=auth_headers,
            )

        # Agent returned error, so agents_successfully_queried is empty -> 503
        assert response.status_code == 503

    def test_refresh_agent_exception_continues_to_next(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
        multiple_hosts: list[models.Host],
    ):
        """When one agent raises an exception, the refresh continues with others."""
        # Use two placements on different agents
        node1 = make_node(test_db, sample_lab, gui_id="n1", container_name="r1",
                               host_id=multiple_hosts[0].id)
        node2 = make_node(test_db, sample_lab, gui_id="n2", container_name="r2",
                               host_id=multiple_hosts[1].id)
        ns1 = make_node_state(test_db, sample_lab, "n1", "r1",
                               desired_state="running", actual_state="stopped")
        ns2 = make_node_state(test_db, sample_lab, "n2", "r2",
                               desired_state="running", actual_state="stopped")

        make_placement(test_db, sample_lab, multiple_hosts[0], "r1", node1)
        make_placement(test_db, sample_lab, multiple_hosts[1], "r2", node2)

        call_count = 0

        async def mock_get_status(agent, lab_id):
            nonlocal call_count
            call_count += 1
            if agent.id == multiple_hosts[0].id:
                raise ConnectionError("agent-1 unreachable")
            return {"nodes": [{"name": "r2", "status": "running"}]}

        mock_ac = MagicMock()
        mock_ac.is_agent_online = MagicMock(return_value=True)
        mock_ac.get_lab_status_from_agent = AsyncMock(side_effect=mock_get_status)

        with patch("app.routers.labs_node_states.agent_client", mock_ac):
            response = test_client.post(
                f"/labs/{sample_lab.id}/nodes/refresh",
                headers=auth_headers,
            )

        assert response.status_code == 200
        test_db.refresh(ns2)
        assert ns2.actual_state == NodeActualState.RUNNING
        # ns1 should remain stopped since agent-1 failed
        test_db.refresh(ns1)
        assert ns1.actual_state == NodeActualState.STOPPED

    def test_refresh_fallback_agent_when_no_placements(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
    ):
        """When no placements and no lab.agent_id, uses fallback agent."""
        # No agent_id set, no placements
        sample_lab.agent_id = None
        test_db.commit()

        make_node(test_db, sample_lab, gui_id="n1", container_name="r1")
        ns = make_node_state(test_db, sample_lab, "n1", "r1",
                              desired_state="running", actual_state="stopped")

        mock_ac = MagicMock()
        mock_ac.is_agent_online = MagicMock(return_value=True)
        mock_ac.get_lab_status_from_agent = AsyncMock(return_value={
            "nodes": [{"name": "r1", "status": "running"}],
        })

        with patch("app.routers.labs_node_states.agent_client", mock_ac), \
             patch("app.routers.labs_node_states.get_online_agent_for_lab",
                    AsyncMock(return_value=sample_host)):
            response = test_client.post(
                f"/labs/{sample_lab.id}/nodes/refresh",
                headers=auth_headers,
            )

        assert response.status_code == 200
        test_db.refresh(ns)
        assert ns.actual_state == NodeActualState.RUNNING

    def test_refresh_preserves_state_for_missing_container(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
    ):
        """Container not found on agent preserves existing state (not marked undeployed)."""
        sample_lab.agent_id = sample_host.id
        test_db.commit()

        make_node(test_db, sample_lab, gui_id="n1", container_name="r1")
        ns = make_node_state(test_db, sample_lab, "n1", "r1",
                              desired_state="running", actual_state="running")

        mock_ac = MagicMock()
        mock_ac.is_agent_online = MagicMock(return_value=True)
        mock_ac.get_lab_status_from_agent = AsyncMock(return_value={
            "nodes": [],  # Container not found
        })

        with patch("app.routers.labs_node_states.agent_client", mock_ac):
            response = test_client.post(
                f"/labs/{sample_lab.id}/nodes/refresh",
                headers=auth_headers,
            )

        assert response.status_code == 200
        test_db.refresh(ns)
        # State should NOT change to undeployed - that's reconciliation's job
        assert ns.actual_state == NodeActualState.RUNNING

    def test_refresh_sets_boot_started_at_for_newly_running(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
    ):
        """When node transitions to running and had no boot_started_at, it is set."""
        sample_lab.agent_id = sample_host.id
        test_db.commit()

        make_node(test_db, sample_lab, gui_id="n1", container_name="r1")
        ns = make_node_state(test_db, sample_lab, "n1", "r1",
                              desired_state="running", actual_state="stopped")
        assert ns.boot_started_at is None

        mock_ac = MagicMock()
        mock_ac.is_agent_online = MagicMock(return_value=True)
        mock_ac.get_lab_status_from_agent = AsyncMock(return_value={
            "nodes": [{"name": "r1", "status": "running"}],
        })

        with patch("app.routers.labs_node_states.agent_client", mock_ac):
            response = test_client.post(
                f"/labs/{sample_lab.id}/nodes/refresh",
                headers=auth_headers,
            )

        assert response.status_code == 200
        test_db.refresh(ns)
        assert ns.actual_state == NodeActualState.RUNNING
        assert ns.boot_started_at is not None


# ============================================================================
# TestListNodeStatesDeepPaths
# ============================================================================


class TestListNodeStatesDeepPaths:
    """Deep-path tests for GET /labs/{lab_id}/nodes/states."""

    def test_pending_refresh_maps_stopped_container(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
    ):
        """Pending node refreshed to 'stopped' when container reports stopped."""
        sample_lab.agent_id = sample_host.id
        test_db.commit()

        make_node(test_db, sample_lab, gui_id="n1", container_name="r1")
        ns = make_node_state(test_db, sample_lab, "n1", "r1",
                              desired_state="running", actual_state="pending")

        mock_ac = MagicMock()
        mock_ac.is_agent_online = MagicMock(return_value=True)
        mock_ac.get_lab_status_from_agent = AsyncMock(return_value={
            "nodes": [{"name": "r1", "status": "stopped"}],
        })

        with patch("app.routers.labs_node_states.agent_client", mock_ac), \
             patch("app.routers.labs_node_states.get_online_agent_for_lab",
                    AsyncMock(return_value=sample_host)):
            response = test_client.get(
                f"/labs/{sample_lab.id}/nodes/states",
                headers=auth_headers,
            )

        assert response.status_code == 200
        test_db.refresh(ns)
        assert ns.actual_state == NodeActualState.STOPPED
        assert ns.boot_started_at is None

    def test_pending_refresh_skipped_when_active_job_exists(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        test_user: models.User,
        auth_headers: dict,
    ):
        """Pending states are NOT refreshed when an active job exists."""
        sample_lab.agent_id = sample_host.id
        test_db.commit()

        make_node(test_db, sample_lab, gui_id="n1", container_name="r1")
        ns = make_node_state(test_db, sample_lab, "n1", "r1",
                              desired_state="running", actual_state="pending")

        # Create an active job
        job = models.Job(
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="running",
        )
        test_db.add(job)
        test_db.commit()

        # Agent should NOT be called
        mock_ac = MagicMock()
        mock_ac.get_lab_status_from_agent = AsyncMock(
            side_effect=AssertionError("Agent should not be called"),
        )

        with patch("app.routers.labs_node_states.agent_client", mock_ac), \
             patch("app.routers.labs_node_states.get_online_agent_for_lab",
                    AsyncMock(return_value=sample_host)):
            response = test_client.get(
                f"/labs/{sample_lab.id}/nodes/states",
                headers=auth_headers,
            )

        assert response.status_code == 200
        test_db.refresh(ns)
        assert ns.actual_state == NodeActualState.PENDING

    def test_pending_refresh_exception_is_swallowed(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
    ):
        """Agent exception during pending refresh is caught, request still succeeds."""
        sample_lab.agent_id = sample_host.id
        test_db.commit()

        make_node(test_db, sample_lab, gui_id="n1", container_name="r1")
        ns = make_node_state(test_db, sample_lab, "n1", "r1",
                              desired_state="running", actual_state="pending")

        with patch("app.routers.labs_node_states.agent_client"), \
             patch("app.routers.labs_node_states.get_online_agent_for_lab",
                    AsyncMock(side_effect=ConnectionError("boom"))):
            response = test_client.get(
                f"/labs/{sample_lab.id}/nodes/states",
                headers=auth_headers,
            )

        assert response.status_code == 200
        test_db.refresh(ns)
        # State unchanged because exception was caught
        assert ns.actual_state == NodeActualState.PENDING

    def test_no_pending_states_skips_refresh(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """When no nodes are pending, agent is never queried for refresh."""
        make_node(test_db, sample_lab, gui_id="n1", container_name="r1")
        make_node_state(test_db, sample_lab, "n1", "r1",
                         desired_state="running", actual_state="running")

        with patch("app.routers.labs_node_states.get_online_agent_for_lab",
                    AsyncMock(side_effect=AssertionError("Should not be called"))):
            response = test_client.get(
                f"/labs/{sample_lab.id}/nodes/states",
                headers=auth_headers,
            )

        assert response.status_code == 200


# ============================================================================
# TestSetDesiredStateDeepPaths
# ============================================================================


class TestSetDesiredStateDeepPaths:
    """Deep-path tests for PUT /labs/{lab_id}/nodes/{node_id}/desired-state."""

    def test_conflicting_job_blocks_sync_job_creation(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """When a conflicting job exists, sync job is not created even though
        desired state changes."""
        make_node(test_db, sample_lab, gui_id="n1", container_name="r1")
        make_node_state(test_db, sample_lab, "n1", "r1",
                         desired_state="stopped", actual_state="stopped")

        with patch("app.routers.labs._create_node_sync_job") as mock_sync, \
             patch("app.routers.labs.has_conflicting_job",
                    return_value=(True, "deploy")):
            response = test_client.put(
                f"/labs/{sample_lab.id}/nodes/n1/desired-state",
                json={"state": "running"},
                headers=auth_headers,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["desired_state"] == "running"
        # Sync job should NOT be created because of conflicting job
        mock_sync.assert_not_called()

    def test_stopped_on_already_stopped_converges_error(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Setting desired=stopped when already desired=stopped but actual=error
        still converges the error state (no-change branch)."""
        make_node(test_db, sample_lab, gui_id="n1", container_name="r1")
        ns = make_node_state(test_db, sample_lab, "n1", "r1",
                              desired_state="stopped", actual_state="error")

        with patch("app.routers.labs.has_conflicting_job",
                    return_value=(False, None)):
            response = test_client.put(
                f"/labs/{sample_lab.id}/nodes/n1/desired-state",
                json={"state": "stopped"},
                headers=auth_headers,
            )

        assert response.status_code == 200
        test_db.refresh(ns)
        # _converge_stopped_error_state should fire on the no-change path
        assert ns.actual_state == NodeActualState.STOPPED

    def test_retry_error_with_conflicting_job_skips_sync(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Retry (running on error node already desired=running) resets enforcement
        but does NOT create sync job when conflicting job exists."""
        make_node(test_db, sample_lab, gui_id="n1", container_name="r1")
        ns = make_node_state(test_db, sample_lab, "n1", "r1",
                              desired_state="running", actual_state="error")
        ns.enforcement_attempts = 5
        ns.error_message = "deploy failed"
        test_db.commit()

        with patch("app.routers.labs._create_node_sync_job") as mock_sync, \
             patch("app.routers.labs.has_conflicting_job",
                    return_value=(True, "sync")):
            response = test_client.put(
                f"/labs/{sample_lab.id}/nodes/n1/desired-state",
                json={"state": "running"},
                headers=auth_headers,
            )

        assert response.status_code == 200
        test_db.refresh(ns)
        assert ns.enforcement_attempts == 0
        assert ns.error_message is None
        mock_sync.assert_not_called()


# ============================================================================
# TestSetAllNodesDesiredStateDeepPaths
# ============================================================================


class TestSetAllNodesDesiredStateDeepPaths:
    """Deep-path tests for PUT /labs/{lab_id}/nodes/desired-state."""

    def test_stop_all_converges_error_nodes(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Stop-all on error nodes converges them to stopped without sync."""
        make_node(test_db, sample_lab, gui_id="n1", container_name="r1")
        ns = make_node_state(test_db, sample_lab, "n1", "r1",
                              desired_state="running", actual_state="error")

        with patch("app.routers.labs.has_conflicting_job", return_value=(False, None)), \
             patch("app.routers.labs_node_states._has_conflicting_job",
                    return_value=(False, None)), \
             patch("app.routers.labs_node_states.safe_create_task"), \
             patch("app.utils.lab.get_lab_provider", return_value="docker"):
            response = test_client.put(
                f"/labs/{sample_lab.id}/nodes/desired-state",
                json={"state": "stopped"},
                headers=auth_headers,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["affected"] == 1
        test_db.refresh(ns)
        assert ns.desired_state == NodeDesiredState.STOPPED
        assert ns.actual_state == NodeActualState.STOPPED

    def test_start_all_resets_error_nodes(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Start-all on error nodes with desired=running resets enforcement
        (reset_and_proceed classification)."""
        make_node(test_db, sample_lab, gui_id="n1", container_name="r1")
        # desired=running + actual=error triggers reset_and_proceed
        ns = make_node_state(test_db, sample_lab, "n1", "r1",
                              desired_state="running", actual_state="error")
        ns.enforcement_attempts = 3
        ns.error_message = "previous failure"
        test_db.commit()

        with patch("app.routers.labs.has_conflicting_job", return_value=(False, None)), \
             patch("app.routers.labs_node_states._has_conflicting_job",
                    return_value=(False, None)), \
             patch("app.routers.labs_node_states.safe_create_task"), \
             patch("app.utils.lab.get_lab_provider", return_value="docker"):
            response = test_client.put(
                f"/labs/{sample_lab.id}/nodes/desired-state",
                json={"state": "running"},
                headers=auth_headers,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["affected"] == 1
        test_db.refresh(ns)
        assert ns.desired_state == NodeDesiredState.RUNNING
        assert ns.enforcement_attempts == 0
        assert ns.error_message is None

    def test_no_nodes_needing_sync_skips_job_creation(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """When affected nodes don't need sync (e.g., error converged), no job."""
        make_node(test_db, sample_lab, gui_id="n1", container_name="r1")
        # Error node + stop request = converges to stopped (no sync needed)
        make_node_state(test_db, sample_lab, "n1", "r1",
                         desired_state="running", actual_state="error")

        mock_safe_task = MagicMock()

        with patch("app.routers.labs.has_conflicting_job", return_value=(False, None)), \
             patch("app.routers.labs_node_states._has_conflicting_job",
                    return_value=(False, None)), \
             patch("app.routers.labs_node_states.safe_create_task", mock_safe_task), \
             patch("app.utils.lab.get_lab_provider", return_value="docker"):
            response = test_client.put(
                f"/labs/{sample_lab.id}/nodes/desired-state",
                json={"state": "stopped"},
                headers=auth_headers,
            )

        assert response.status_code == 200
        # safe_create_task should only be called for cooldown clearing, not reconcile
        for call in mock_safe_task.call_args_list:
            # No call should be for a sync/reconcile job
            if len(call.kwargs) > 0 and "name" in call.kwargs:
                assert not call.kwargs["name"].startswith("sync:bulk:")


# ============================================================================
# TestGetOutOfSyncNodes
# ============================================================================


class TestGetOutOfSyncNodes:
    """Unit tests for _get_out_of_sync_nodes helper."""

    def test_desired_stopped_actual_running_is_out_of_sync(
        self,
        test_db: Session,
        sample_lab: models.Lab,
    ):
        """desired=stopped, actual=running -> out of sync."""
        from app.routers.labs_node_states import _get_out_of_sync_nodes

        make_node_state(test_db, sample_lab, "n1", "r1",
                         desired_state="stopped", actual_state="running")

        result = _get_out_of_sync_nodes(test_db, sample_lab.id)
        assert len(result) == 1
        assert result[0].node_id == "n1"

    def test_desired_running_actual_running_is_in_sync(
        self,
        test_db: Session,
        sample_lab: models.Lab,
    ):
        """desired=running, actual=running -> in sync."""
        from app.routers.labs_node_states import _get_out_of_sync_nodes

        make_node_state(test_db, sample_lab, "n1", "r1",
                         desired_state="running", actual_state="running")

        result = _get_out_of_sync_nodes(test_db, sample_lab.id)
        assert len(result) == 0

    def test_desired_stopped_actual_undeployed_is_in_sync(
        self,
        test_db: Session,
        sample_lab: models.Lab,
    ):
        """desired=stopped, actual=undeployed -> in sync."""
        from app.routers.labs_node_states import _get_out_of_sync_nodes

        make_node_state(test_db, sample_lab, "n1", "r1",
                         desired_state="stopped", actual_state="undeployed")

        result = _get_out_of_sync_nodes(test_db, sample_lab.id)
        assert len(result) == 0

    def test_desired_running_actual_error_is_out_of_sync(
        self,
        test_db: Session,
        sample_lab: models.Lab,
    ):
        """desired=running, actual=error -> out of sync."""
        from app.routers.labs_node_states import _get_out_of_sync_nodes

        make_node_state(test_db, sample_lab, "n1", "r1",
                         desired_state="running", actual_state="error")

        result = _get_out_of_sync_nodes(test_db, sample_lab.id)
        assert len(result) == 1

    def test_filter_by_specific_node_ids(
        self,
        test_db: Session,
        sample_lab: models.Lab,
    ):
        """node_ids parameter filters to only specified nodes."""
        from app.routers.labs_node_states import _get_out_of_sync_nodes

        make_node_state(test_db, sample_lab, "n1", "r1",
                         desired_state="running", actual_state="stopped")
        make_node_state(test_db, sample_lab, "n2", "r2",
                         desired_state="running", actual_state="stopped")

        result = _get_out_of_sync_nodes(test_db, sample_lab.id, node_ids=["n1"])
        assert len(result) == 1
        assert result[0].node_id == "n1"

    def test_empty_lab_returns_empty_list(
        self,
        test_db: Session,
        sample_lab: models.Lab,
    ):
        """Lab with no node states returns empty list."""
        from app.routers.labs_node_states import _get_out_of_sync_nodes

        result = _get_out_of_sync_nodes(test_db, sample_lab.id)
        assert result == []

    def test_desired_running_actual_pending_is_in_sync(
        self,
        test_db: Session,
        sample_lab: models.Lab,
    ):
        """desired=running, actual=pending -> in sync (pending counts as progressing)."""
        from app.routers.labs_node_states import _get_out_of_sync_nodes

        make_node_state(test_db, sample_lab, "n1", "r1",
                         desired_state="running", actual_state="pending")

        result = _get_out_of_sync_nodes(test_db, sample_lab.id)
        assert len(result) == 0