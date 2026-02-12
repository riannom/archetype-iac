"""Concurrency test suite (Phase 0.2).

Tests deploy locks, API transitional state guards (409 responses),
simulated dual enforcement, and concurrent bulk + individual operations.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app import models
from app.state import JobStatus


# ---------------------------------------------------------------------------
# Deploy Lock Tests
# ---------------------------------------------------------------------------

class TestDeployLock:
    """Redis-based deploy lock: acquire, contend, expire (TTL), release."""

    def test_acquire_lock_succeeds(self, monkeypatch) -> None:
        """First acquire should succeed."""
        from app.tasks.jobs import acquire_deploy_lock

        calls = {}

        class FakeRedis:
            def set(self, key, value, nx=False, ex=None):
                calls[key] = value
                return True  # Lock acquired

        monkeypatch.setattr("app.tasks.jobs.get_redis", lambda: FakeRedis())

        ok, locked = acquire_deploy_lock("lab1", ["r1", "r2"], "agent-1")
        assert ok is True
        assert set(locked) == {"r1", "r2"}

    def test_contention_second_acquire_fails(self, monkeypatch) -> None:
        """Second acquire on same nodes should fail and report which nodes are held."""
        from app.tasks.jobs import acquire_deploy_lock

        held_locks: dict[str, str] = {}

        class FakeRedis:
            def set(self, key, value, nx=False, ex=None):
                if key in held_locks:
                    return False  # Already held
                held_locks[key] = value
                return True

            def get(self, key):
                val = held_locks.get(key, "unknown")
                return val.encode() if isinstance(val, str) else val

            def delete(self, key):
                held_locks.pop(key, None)

        monkeypatch.setattr("app.tasks.jobs.get_redis", lambda: FakeRedis())

        # First agent acquires
        ok1, _ = acquire_deploy_lock("lab1", ["r1", "r2"], "agent-1")
        assert ok1 is True

        # Second agent tries same nodes
        ok2, failed = acquire_deploy_lock("lab1", ["r1", "r2"], "agent-2")
        assert ok2 is False
        assert "r1" in failed

    def test_release_frees_lock(self, monkeypatch) -> None:
        """After release, a new acquire should succeed."""
        from app.tasks.jobs import acquire_deploy_lock, release_deploy_lock

        held: dict[str, str] = {}

        class FakeRedis:
            def set(self, key, value, nx=False, ex=None):
                if key in held:
                    return False
                held[key] = value
                return True

            def get(self, key):
                return held.get(key, b"unknown")

            def delete(self, key):
                held.pop(key, None)

        monkeypatch.setattr("app.tasks.jobs.get_redis", lambda: FakeRedis())

        acquire_deploy_lock("lab1", ["r1"], "agent-1")
        release_deploy_lock("lab1", ["r1"])

        ok, _ = acquire_deploy_lock("lab1", ["r1"], "agent-2")
        assert ok is True

    def test_partial_failure_releases_acquired(self, monkeypatch) -> None:
        """If some nodes fail to lock, all acquired locks are released."""
        from app.tasks.jobs import acquire_deploy_lock

        held: dict[str, str] = {}

        class FakeRedis:
            def set(self, key, value, nx=False, ex=None):
                if key in held:
                    return False
                held[key] = value
                return True

            def get(self, key):
                return held.get(key, b"unknown")

            def delete(self, key):
                held.pop(key, None)

        fake = FakeRedis()
        monkeypatch.setattr("app.tasks.jobs.get_redis", lambda: fake)

        # Pre-lock r2 as another agent (bytes, as real Redis returns)
        held["deploy_lock:lab1:r2"] = b"agent:other:time:2026-01-01"

        ok, failed = acquire_deploy_lock("lab1", ["r1", "r2"], "agent-1")
        assert ok is False
        assert "r2" in failed
        # r1 should have been released (rolled back)
        assert "deploy_lock:lab1:r1" not in held

    def test_redis_error_allows_deploy(self, monkeypatch) -> None:
        """On Redis error, deploy proceeds without lock (fail-open)."""
        import redis as redis_lib
        from app.tasks.jobs import acquire_deploy_lock

        class BrokenRedis:
            def set(self, *a, **kw):
                raise redis_lib.RedisError("connection refused")

        monkeypatch.setattr("app.tasks.jobs.get_redis", lambda: BrokenRedis())

        ok, nodes = acquire_deploy_lock("lab1", ["r1"], "agent-1")
        assert ok is True  # Fail-open


# ---------------------------------------------------------------------------
# API Guard Tests — Single Node 409 on Conflicting Operations
# ---------------------------------------------------------------------------

class TestSingleNodeGuards:
    """HTTP 409 when starting a stopping node or stopping a starting node."""

    def test_409_start_while_stopping(
        self, test_client: TestClient, auth_headers: dict,
        sample_lab: models.Lab, test_db,
    ) -> None:
        """Starting a node that is currently stopping must return 409."""
        # Create a node in stopping state
        ns = models.NodeState(
            lab_id=sample_lab.id,
            node_id="r1",
            node_name="r1",
            desired_state="running",
            actual_state="stopping",
        )
        test_db.add(ns)
        test_db.commit()

        resp = test_client.put(
            f"/labs/{sample_lab.id}/nodes/r1/desired-state",
            json={"state": "running"},
            headers=auth_headers,
        )
        assert resp.status_code == 409
        assert "stopping" in resp.json()["detail"].lower()

    def test_stop_while_starting_is_allowed(
        self, test_client: TestClient, auth_headers: dict,
        sample_lab: models.Lab, test_db,
    ) -> None:
        """Stopping a node that is currently starting is allowed.

        VMs can take minutes to boot, so users should be able to abort
        a slow start by issuing a stop command.
        """
        ns = models.NodeState(
            lab_id=sample_lab.id,
            node_id="r1",
            node_name="r1",
            desired_state="running",
            actual_state="starting",
        )
        test_db.add(ns)
        test_db.commit()

        resp = test_client.put(
            f"/labs/{sample_lab.id}/nodes/r1/desired-state",
            json={"state": "stopped"},
            headers=auth_headers,
        )
        # Stop while starting is allowed (abort slow boot)
        assert resp.status_code == 200

    def test_start_already_running_is_noop(
        self, test_client: TestClient, auth_headers: dict,
        sample_lab: models.Lab, test_db,
    ) -> None:
        """Starting a running node should succeed (no-op) — not create duplicate commands."""
        ns = models.NodeState(
            lab_id=sample_lab.id,
            node_id="r1",
            node_name="r1",
            desired_state="running",
            actual_state="running",
        )
        test_db.add(ns)
        test_db.commit()

        resp = test_client.put(
            f"/labs/{sample_lab.id}/nodes/r1/desired-state",
            json={"state": "running"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        # No new job should be created (already in desired state)
        jobs = test_db.query(models.Job).filter(
            models.Job.lab_id == sample_lab.id
        ).all()
        assert len(jobs) == 0

    def test_stop_already_stopped_is_noop(
        self, test_client: TestClient, auth_headers: dict,
        sample_lab: models.Lab, test_db,
    ) -> None:
        """Stopping a stopped node should succeed (no-op)."""
        ns = models.NodeState(
            lab_id=sample_lab.id,
            node_id="r1",
            node_name="r1",
            desired_state="stopped",
            actual_state="stopped",
        )
        test_db.add(ns)
        test_db.commit()

        resp = test_client.put(
            f"/labs/{sample_lab.id}/nodes/r1/desired-state",
            json={"state": "stopped"},
            headers=auth_headers,
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# API Guard Tests — Bulk Operation 409
# ---------------------------------------------------------------------------

class TestBulkGuards:
    """HTTP 409 when bulk start/stop conflicts with transitional nodes."""

    def test_bulk_start_skips_stopping_nodes(
        self, test_client: TestClient, auth_headers: dict,
        sample_lab: models.Lab, test_db,
    ) -> None:
        """Bulk start-all skips nodes in 'stopping' state (returns 200)."""
        for i, state in enumerate(["running", "stopping"]):
            ns = models.NodeState(
                lab_id=sample_lab.id,
                node_id=f"r{i+1}",
                node_name=f"r{i+1}",
                desired_state="running" if state == "running" else "stopped",
                actual_state=state,
            )
            test_db.add(ns)
        test_db.commit()

        resp = test_client.put(
            f"/labs/{sample_lab.id}/nodes/desired-state",
            json={"state": "running"},
            headers=auth_headers,
        )
        # Endpoint now skips transitional nodes instead of returning 409
        assert resp.status_code == 200

    def test_bulk_stop_skips_starting_nodes(
        self, test_client: TestClient, auth_headers: dict,
        sample_lab: models.Lab, test_db,
    ) -> None:
        """Bulk stop-all skips nodes in 'starting' state (returns 200)."""
        for i, state in enumerate(["stopped", "starting"]):
            ns = models.NodeState(
                lab_id=sample_lab.id,
                node_id=f"r{i+1}",
                node_name=f"r{i+1}",
                desired_state="stopped" if state == "stopped" else "running",
                actual_state=state,
            )
            test_db.add(ns)
        test_db.commit()

        resp = test_client.put(
            f"/labs/{sample_lab.id}/nodes/desired-state",
            json={"state": "stopped"},
            headers=auth_headers,
        )
        # Endpoint now skips transitional nodes instead of returning 409
        assert resp.status_code == 200

    def test_bulk_start_succeeds_when_no_stopping(
        self, test_client: TestClient, auth_headers: dict,
        sample_lab: models.Lab, test_db, monkeypatch,
    ) -> None:
        """Bulk start succeeds when no nodes are stopping."""
        for i in range(3):
            ns = models.NodeState(
                lab_id=sample_lab.id,
                node_id=f"r{i+1}",
                node_name=f"r{i+1}",
                desired_state="stopped",
                actual_state="stopped",
            )
            test_db.add(ns)
        test_db.commit()

        # Mock out the sync task to prevent background task execution
        monkeypatch.setattr(
            "app.routers.labs.safe_create_task", lambda *a, **kw: None
        )

        resp = test_client.put(
            f"/labs/{sample_lab.id}/nodes/desired-state",
            json={"state": "running"},
            headers=auth_headers,
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Simulated Dual Enforcement — Only One Should Create a Job
# ---------------------------------------------------------------------------

class TestDualEnforcement:
    """Two enforcement cycles on same node — only one should win."""

    @pytest.mark.asyncio
    async def test_enforcement_skips_node_with_active_job(self, test_db, monkeypatch) -> None:
        """If an enforcement job already exists, second call skips."""
        import app.tasks.state_enforcement as se

        lab = models.Lab(
            name="Test", owner_id="user1", provider="docker",
            state="running", workspace_path="/tmp/test",
        )
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        ns = models.NodeState(
            lab_id=lab.id, node_id="r1", node_name="r1",
            desired_state="running", actual_state="stopped",
            enforcement_attempts=0,
        )
        test_db.add(ns)
        test_db.commit()

        # Simulate: first enforcement already created an active job
        active_job = models.Job(
            lab_id=lab.id, action="node:stop:r1",
            status=JobStatus.QUEUED.value,
        )
        test_db.add(active_job)
        test_db.commit()

        # Stub out Redis and agent lookups (must be async since source awaits them)
        monkeypatch.setattr(se, "_is_on_cooldown", AsyncMock(return_value=False))
        monkeypatch.setattr(se, "_set_cooldown", AsyncMock(return_value=None))

        mock_agent = MagicMock()
        mock_agent.id = "agent-1"
        monkeypatch.setattr(se, "_get_agent_for_node", AsyncMock(return_value=mock_agent))
        monkeypatch.setattr(se.settings, "state_enforcement_max_retries", 10)
        monkeypatch.setattr(se.settings, "state_enforcement_auto_restart_enabled", True)

        # Second enforcement call should skip (active job exists)
        result = await se.enforce_node_state(test_db, lab, ns)
        assert result is False

    @pytest.mark.asyncio
    async def test_enforcement_cooldown_prevents_repeat(self, test_db, monkeypatch) -> None:
        """Redis cooldown prevents enforcement from running twice quickly."""
        import app.tasks.state_enforcement as se

        lab = models.Lab(
            name="Test", owner_id="user1", provider="docker",
            state="running", workspace_path="/tmp/test",
        )
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        ns = models.NodeState(
            lab_id=lab.id, node_id="r1", node_name="r1",
            desired_state="running", actual_state="stopped",
            enforcement_attempts=0,
        )
        test_db.add(ns)
        test_db.commit()

        monkeypatch.setattr(se.settings, "state_enforcement_max_retries", 10)
        monkeypatch.setattr(se.settings, "state_enforcement_auto_restart_enabled", True)

        # Simulate: cooldown is active (must be async since source awaits it)
        monkeypatch.setattr(se, "_is_on_cooldown", AsyncMock(return_value=True))

        mock_agent = MagicMock()
        mock_agent.id = "agent-1"
        monkeypatch.setattr(se, "_get_agent_for_node", AsyncMock(return_value=mock_agent))

        result = await se.enforce_node_state(test_db, lab, ns)
        assert result is False


# ---------------------------------------------------------------------------
# Concurrent Bulk + Individual — Conflict Detection
# ---------------------------------------------------------------------------

class TestBulkIndividualConflict:
    """Conflict detection between bulk and individual operations."""

    def test_conflicting_job_blocks_sync(self, test_db) -> None:
        """An active 'up' job conflicts with 'sync' actions."""

        lab = models.Lab(
            name="Test", owner_id="user1", provider="docker",
            state="running", workspace_path="/tmp/test",
        )
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        job = models.Job(
            lab_id=lab.id, action="up",
            status=JobStatus.QUEUED.value,
        )
        test_db.add(job)
        test_db.commit()

        # has_conflicting_job uses its own session, so we need to mock it
        # to use our test session. Instead, test the CONFLICTING_ACTIONS logic directly.
        from app.jobs import CONFLICTING_ACTIONS
        assert "up" in CONFLICTING_ACTIONS["sync"]
        assert "down" in CONFLICTING_ACTIONS["sync"]
        assert "sync" in CONFLICTING_ACTIONS["up"]

    def test_sync_does_not_conflict_with_sync(self) -> None:
        """Two sync operations don't conflict with each other."""
        from app.jobs import CONFLICTING_ACTIONS
        assert "sync" not in CONFLICTING_ACTIONS.get("sync", [])

    def test_up_conflicts_with_down(self) -> None:
        """Up and down conflict with each other."""
        from app.jobs import CONFLICTING_ACTIONS
        assert "down" in CONFLICTING_ACTIONS["up"]
        assert "up" in CONFLICTING_ACTIONS["down"]

    def test_unknown_action_has_no_conflicts(self) -> None:
        """Unknown actions have no conflicts (fail-open)."""
        from app.jobs import CONFLICTING_ACTIONS
        assert CONFLICTING_ACTIONS.get("unknown_action", []) == []


# ---------------------------------------------------------------------------
# SELECT FOR UPDATE — Row Lock Tests (Phase 6.2)
# ---------------------------------------------------------------------------

class TestRowLevelLocking:
    """Tests for row-level locking on desired state setters."""

    def test_get_or_create_with_for_update(self, test_db) -> None:
        """_get_or_create_node_state with for_update=True acquires row lock."""
        from app.routers.labs import _get_or_create_node_state

        lab = models.Lab(
            name="Test", owner_id="user1", provider="docker",
            state="stopped", workspace_path="/tmp/test",
        )
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        # Create a node state
        ns = models.NodeState(
            lab_id=lab.id, node_id="r1", node_name="r1",
            desired_state="stopped", actual_state="stopped",
        )
        test_db.add(ns)
        test_db.commit()

        # for_update=True should succeed (SQLite doesn't enforce FOR UPDATE
        # but the code path is exercised)
        state = _get_or_create_node_state(test_db, lab.id, "r1", for_update=True)
        assert state.node_id == "r1"
        assert state.desired_state == "stopped"

    def test_get_or_create_without_for_update(self, test_db) -> None:
        """_get_or_create_node_state with for_update=False (default) works normally."""
        from app.routers.labs import _get_or_create_node_state

        lab = models.Lab(
            name="Test", owner_id="user1", provider="docker",
            state="stopped", workspace_path="/tmp/test",
        )
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        # Create a node state
        ns = models.NodeState(
            lab_id=lab.id, node_id="r1", node_name="r1",
            desired_state="stopped", actual_state="stopped",
        )
        test_db.add(ns)
        test_db.commit()

        state = _get_or_create_node_state(test_db, lab.id, "r1")
        assert state.node_id == "r1"

    def test_has_conflicting_job_with_session(self, test_db) -> None:
        """has_conflicting_job uses provided session instead of creating new one."""
        from app.jobs import has_conflicting_job

        lab = models.Lab(
            name="Test", owner_id="user1", provider="docker",
            state="running", workspace_path="/tmp/test",
        )
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        # No conflicting job
        has_conflict, _ = has_conflicting_job(lab.id, "sync", session=test_db)
        assert has_conflict is False

        # Add a conflicting job
        job = models.Job(
            lab_id=lab.id, action="up",
            status="queued",
        )
        test_db.add(job)
        test_db.commit()

        # Now should detect conflict
        has_conflict, action = has_conflicting_job(lab.id, "sync", session=test_db)
        assert has_conflict is True
        assert action == "up"

    def test_has_conflicting_job_without_session(self, monkeypatch) -> None:
        """has_conflicting_job uses get_session when no session provided."""
        from contextlib import contextmanager
        from app.jobs import has_conflicting_job

        # Mock get_session context manager to verify it's used
        mock_session = MagicMock()
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.first.return_value = None
        mock_session.query.return_value = mock_query

        @contextmanager
        def mock_get_session():
            yield mock_session

        monkeypatch.setattr("app.jobs.get_session", mock_get_session)

        has_conflict, _ = has_conflicting_job("lab1", "sync")
        assert has_conflict is False
        # Verify the session was used (query was called)
        mock_session.query.assert_called()


class TestLinkStateRapidUpdates:
    """Rapid link desired-state writes should remain consistent."""

    def test_rapid_set_link_state_keeps_oper_epoch_monotonic(
        self,
        test_client: TestClient,
        auth_headers: dict,
        sample_lab: models.Lab,
        test_db,
    ) -> None:
        link = models.LinkState(
            lab_id=sample_lab.id,
            link_name="r1:eth1-r2:eth1",
            source_node="r1",
            source_interface="eth1",
            target_node="r2",
            target_interface="eth1",
            desired_state="up",
            actual_state="unknown",
        )
        test_db.add(link)
        test_db.commit()

        epochs: list[int] = []
        for state in ["down", "up", "down", "up", "down"]:
            resp = test_client.put(
                f"/labs/{sample_lab.id}/links/{link.link_name}/state",
                json={"state": state},
                headers=auth_headers,
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["desired_state"] == state
            epochs.append(data["oper_epoch"])

        assert epochs == sorted(epochs)
        final = test_db.query(models.LinkState).filter(models.LinkState.id == link.id).first()
        assert final is not None
        assert final.desired_state == "down"
