"""Bulk operation TDD tests (Phase 0.4).

These tests define the NEW behavior from Issue #3:
- Bulk start/stop processes nodes SELECTIVELY (not all-or-nothing)
- Transitional nodes are skipped
- Response includes counts: { affected, skipped_transitional, already_in_state }

These tests will FAIL initially. They define the Phase 1.3 implementation target.
When Phase 1.3 is done, these tests should all pass.

CURRENT BEHAVIOR (to be changed in Phase 1.3):
- Bulk start/stop sets desired_state on ALL nodes unconditionally
- Returns a flat list of NodeStateOut objects
- Blocks entirely with 409 if ANY node is in a transitional state

NEW BEHAVIOR (Phase 1.3 target):
- Skip transitional nodes, process the rest
- Return response with counts: { affected, skipped_transitional, already_in_state }
"""
from __future__ import annotations

import pytest

from app import models


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_nodes(test_db, lab, node_specs: list[tuple[str, str, str]]):
    """Create NodeState records.

    Args:
        node_specs: list of (node_id, desired_state, actual_state)
    """
    nodes = []
    for node_id, desired, actual in node_specs:
        ns = models.NodeState(
            lab_id=lab.id,
            node_id=node_id,
            node_name=node_id,
            desired_state=desired,
            actual_state=actual,
        )
        test_db.add(ns)
        nodes.append(ns)
    test_db.commit()
    for n in nodes:
        test_db.refresh(n)
    return nodes


# ---------------------------------------------------------------------------
# Bulk Start — All Stopped → All Get desired_state=running
# ---------------------------------------------------------------------------

class TestBulkStartAllStopped:
    """When all nodes are stopped, start-all should affect all of them."""

    def test_all_stopped_become_desired_running(
        self, test_client, auth_headers, sample_lab, test_db, monkeypatch,
    ) -> None:
        _create_nodes(test_db, sample_lab, [
            ("r1", "stopped", "stopped"),
            ("r2", "stopped", "stopped"),
            ("r3", "stopped", "stopped"),
        ])
        monkeypatch.setattr("app.routers.labs.safe_create_task", lambda *a, **kw: None)

        resp = test_client.put(
            f"/labs/{sample_lab.id}/nodes/desired-state",
            json={"state": "running"},
            headers=auth_headers,
        )
        assert resp.status_code == 200

        # Verify all nodes now have desired_state=running
        for ns in test_db.query(models.NodeState).filter(
            models.NodeState.lab_id == sample_lab.id
        ).all():
            assert ns.desired_state == "running"


# ---------------------------------------------------------------------------
# Bulk Start — Mixed States (Phase 1.3 NEW behavior)
# ---------------------------------------------------------------------------

class TestBulkStartMixedStates:
    """Phase 1.3: Bulk start with mixed states processes selectively."""

    @pytest.mark.xfail(reason="Phase 1.3 not yet implemented — current behavior is all-or-nothing with 409 blocking")
    def test_start_all_skips_transitional(
        self, test_client, auth_headers, sample_lab, test_db, monkeypatch,
    ) -> None:
        """Starting nodes skip those already in transitional states."""
        _create_nodes(test_db, sample_lab, [
            ("r1", "stopped", "stopped"),      # Should start
            ("r2", "running", "running"),      # Already running — skip
            ("r3", "running", "starting"),     # Transitional — skip
            ("r4", "stopped", "stopping"),     # Transitional — skip
            ("r5", "running", "error"),        # Error — should start
            ("r6", "stopped", "undeployed"),   # Undeployed — should start
        ])
        monkeypatch.setattr("app.routers.labs.safe_create_task", lambda *a, **kw: None)

        resp = test_client.put(
            f"/labs/{sample_lab.id}/nodes/desired-state",
            json={"state": "running"},
            headers=auth_headers,
        )
        assert resp.status_code == 200

        data = resp.json()
        assert data["affected"] == 3         # r1, r5, r6
        assert data["skipped_transitional"] == 2  # r3, r4
        assert data["already_in_state"] == 1      # r2

    @pytest.mark.xfail(reason="Phase 1.3 not yet implemented")
    def test_stop_all_skips_transitional(
        self, test_client, auth_headers, sample_lab, test_db, monkeypatch,
    ) -> None:
        """Stopping nodes skip those already in transitional states."""
        _create_nodes(test_db, sample_lab, [
            ("r1", "running", "running"),      # Should stop
            ("r2", "stopped", "stopped"),      # Already stopped — skip
            ("r3", "running", "starting"),     # Transitional — skip
            ("r4", "stopped", "stopping"),     # Transitional — skip
        ])
        monkeypatch.setattr("app.routers.labs.safe_create_task", lambda *a, **kw: None)

        resp = test_client.put(
            f"/labs/{sample_lab.id}/nodes/desired-state",
            json={"state": "stopped"},
            headers=auth_headers,
        )
        assert resp.status_code == 200

        data = resp.json()
        assert data["affected"] == 1              # r1
        assert data["skipped_transitional"] == 2  # r3, r4
        assert data["already_in_state"] == 1      # r2

    @pytest.mark.xfail(reason="Phase 1.3 not yet implemented")
    def test_noop_when_all_already_in_desired_state(
        self, test_client, auth_headers, sample_lab, test_db, monkeypatch,
    ) -> None:
        """No-op when all nodes are already in the desired state."""
        _create_nodes(test_db, sample_lab, [
            ("r1", "running", "running"),
            ("r2", "running", "running"),
        ])
        monkeypatch.setattr("app.routers.labs.safe_create_task", lambda *a, **kw: None)

        resp = test_client.put(
            f"/labs/{sample_lab.id}/nodes/desired-state",
            json={"state": "running"},
            headers=auth_headers,
        )
        assert resp.status_code == 200

        data = resp.json()
        assert data["affected"] == 0
        assert data["already_in_state"] == 2


# ---------------------------------------------------------------------------
# Bulk Stop — All Running → All Get desired_state=stopped
# ---------------------------------------------------------------------------

class TestBulkStopAllRunning:
    """When all nodes are running, stop-all should affect all of them."""

    def test_all_running_become_desired_stopped(
        self, test_client, auth_headers, sample_lab, test_db, monkeypatch,
    ) -> None:
        _create_nodes(test_db, sample_lab, [
            ("r1", "running", "running"),
            ("r2", "running", "running"),
        ])
        monkeypatch.setattr("app.routers.labs.safe_create_task", lambda *a, **kw: None)

        resp = test_client.put(
            f"/labs/{sample_lab.id}/nodes/desired-state",
            json={"state": "stopped"},
            headers=auth_headers,
        )
        assert resp.status_code == 200

        for ns in test_db.query(models.NodeState).filter(
            models.NodeState.lab_id == sample_lab.id
        ).all():
            assert ns.desired_state == "stopped"


# ---------------------------------------------------------------------------
# Sync Job Creation for Bulk Operations
# ---------------------------------------------------------------------------

class TestBulkSyncJobCreation:
    """Bulk operations should create sync jobs for affected nodes."""

    def test_sync_job_created_for_out_of_sync_nodes(
        self, test_client, auth_headers, sample_lab, test_db, monkeypatch,
    ) -> None:
        """A sync job is created when nodes need state changes."""
        _create_nodes(test_db, sample_lab, [
            ("r1", "stopped", "stopped"),
            ("r2", "stopped", "stopped"),
        ])
        monkeypatch.setattr("app.routers.labs.safe_create_task", lambda *a, **kw: None)

        resp = test_client.put(
            f"/labs/{sample_lab.id}/nodes/desired-state",
            json={"state": "running"},
            headers=auth_headers,
        )
        assert resp.status_code == 200

        # Verify sync job was created
        job = test_db.query(models.Job).filter(
            models.Job.lab_id == sample_lab.id,
            models.Job.action.like("sync:%"),
        ).first()
        assert job is not None
        assert job.status == "queued"

    def test_no_sync_job_when_all_already_in_state(
        self, test_client, auth_headers, sample_lab, test_db, monkeypatch,
    ) -> None:
        """No sync job created when all nodes already in desired state."""
        _create_nodes(test_db, sample_lab, [
            ("r1", "running", "running"),
            ("r2", "running", "running"),
        ])
        monkeypatch.setattr("app.routers.labs.safe_create_task", lambda *a, **kw: None)

        resp = test_client.put(
            f"/labs/{sample_lab.id}/nodes/desired-state",
            json={"state": "running"},
            headers=auth_headers,
        )
        assert resp.status_code == 200

        jobs = test_db.query(models.Job).filter(
            models.Job.lab_id == sample_lab.id,
        ).all()
        assert len(jobs) == 0


# ---------------------------------------------------------------------------
# Error Node Handling in Bulk Operations
# ---------------------------------------------------------------------------

class TestBulkErrorNodeHandling:
    """Error nodes should be included in bulk start (enforcement will retry)."""

    @pytest.mark.xfail(reason="Phase 1.3 not yet implemented — error nodes need special handling")
    def test_error_nodes_get_desired_running_on_start_all(
        self, test_client, auth_headers, sample_lab, test_db, monkeypatch,
    ) -> None:
        """Error nodes should be set to desired=running and counted as affected."""
        _create_nodes(test_db, sample_lab, [
            ("r1", "running", "error"),
        ])
        monkeypatch.setattr("app.routers.labs.safe_create_task", lambda *a, **kw: None)

        resp = test_client.put(
            f"/labs/{sample_lab.id}/nodes/desired-state",
            json={"state": "running"},
            headers=auth_headers,
        )
        assert resp.status_code == 200

        data = resp.json()
        assert data["affected"] == 1

        ns = test_db.query(models.NodeState).filter(
            models.NodeState.lab_id == sample_lab.id,
            models.NodeState.node_id == "r1",
        ).first()
        assert ns.desired_state == "running"


# ---------------------------------------------------------------------------
# Empty Lab
# ---------------------------------------------------------------------------

class TestBulkEmptyLab:
    """Bulk operations on empty lab should be no-ops."""

    @pytest.mark.xfail(reason="Phase 1.3 not yet implemented — needs count response")
    def test_empty_lab_returns_zero_counts(
        self, test_client, auth_headers, sample_lab, test_db, monkeypatch,
    ) -> None:
        monkeypatch.setattr("app.routers.labs.safe_create_task", lambda *a, **kw: None)

        resp = test_client.put(
            f"/labs/{sample_lab.id}/nodes/desired-state",
            json={"state": "running"},
            headers=auth_headers,
        )
        assert resp.status_code == 200

        data = resp.json()
        assert data["affected"] == 0
        assert data["skipped_transitional"] == 0
        assert data["already_in_state"] == 0
