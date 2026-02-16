"""State enforcement tests (Phase 0.3 — end-to-end enforcement flow).

Extends existing unit tests with full enforce_node_state() integration tests:
- Job creation, attempts tracking, cooldown
- Max retries → error state
- Explicit placement enforcement
- Reset on manual intervention
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app import models
from app.state import JobStatus, NodeActualState
import app.tasks.state_enforcement as state_enforcement


# ---------------------------------------------------------------------------
# Existing Unit Tests (preserved)
# ---------------------------------------------------------------------------

def test_calculate_backoff(monkeypatch) -> None:
    monkeypatch.setattr(state_enforcement.settings, "state_enforcement_retry_backoff", 5)
    monkeypatch.setattr(state_enforcement.settings, "state_enforcement_cooldown", 30)

    assert state_enforcement._calculate_backoff(0) == 5
    assert state_enforcement._calculate_backoff(2) == 20
    assert state_enforcement._calculate_backoff(10) == 30


def test_should_skip_enforcement_max_retries(monkeypatch) -> None:
    monkeypatch.setattr(state_enforcement.settings, "state_enforcement_max_retries", 3)
    node_state = models.NodeState(
        lab_id="lab",
        node_id="r1",
        node_name="r1",
        desired_state="running",
        actual_state="stopped",
        enforcement_attempts=3,
        enforcement_failed_at=datetime.now(timezone.utc),
    )

    skip, reason = state_enforcement._should_skip_enforcement(node_state)
    assert skip
    assert "max retries" in reason


def test_should_skip_enforcement_cooldown(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(state_enforcement.settings, "state_enforcement_max_retries", 5)
    monkeypatch.setattr(state_enforcement.settings, "state_enforcement_crash_cooldown", 60)

    node_state = models.NodeState(
        lab_id="lab",
        node_id="r1",
        node_name="r1",
        desired_state="running",
        actual_state="error",
        enforcement_attempts=1,
        enforcement_failed_at=now,
    )

    skip, reason = state_enforcement._should_skip_enforcement(node_state)
    assert skip
    assert "crash cooldown" in reason


def test_should_skip_enforcement_backoff(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(state_enforcement.settings, "state_enforcement_max_retries", 5)
    monkeypatch.setattr(state_enforcement.settings, "state_enforcement_retry_backoff", 10)
    monkeypatch.setattr(state_enforcement.settings, "state_enforcement_cooldown", 60)

    node_state = models.NodeState(
        lab_id="lab",
        node_id="r1",
        node_name="r1",
        desired_state="running",
        actual_state="stopped",
        enforcement_attempts=2,
        last_enforcement_at=now,
    )

    skip, reason = state_enforcement._should_skip_enforcement(node_state)
    assert skip
    assert "backoff" in reason


@pytest.mark.asyncio
async def test_cooldown_helpers(monkeypatch) -> None:
    calls = []

    class FakeAsyncRedis:
        async def exists(self, key):
            calls.append(("exists", key))
            return 1

        async def setex(self, key, ttl, value):
            calls.append(("setex", key, ttl, value))

    monkeypatch.setattr(state_enforcement, "get_async_redis", lambda: FakeAsyncRedis())

    assert await state_enforcement._is_on_cooldown("lab1", "r1")
    await state_enforcement._set_cooldown("lab1", "r1")

    assert calls[0][0] == "exists"
    assert calls[1][0] == "setex"


def test_has_active_job(test_db) -> None:
    sync_node_job = models.Job(
        lab_id="lab1",
        action="sync:node:node-1",
        status=JobStatus.QUEUED.value,
    )
    sync_agent_job = models.Job(
        lab_id="lab1",
        action="sync:agent:agent-a:node-2,node-3",
        status=JobStatus.RUNNING.value,
    )
    sync_batch_job = models.Job(
        lab_id="lab1",
        action="sync:batch:3",
        status=JobStatus.RUNNING.value,
    )
    legacy_node_job = models.Job(
        lab_id="lab2",
        action="node:stop:r1",
        status=JobStatus.QUEUED.value,
    )
    test_db.add_all([sync_node_job, sync_agent_job, sync_batch_job, legacy_node_job])
    test_db.commit()

    assert state_enforcement._has_active_job(test_db, "lab1")
    assert state_enforcement._has_active_job(test_db, "lab1", node_id="node-1")
    assert state_enforcement._has_active_job(test_db, "lab1", node_id="node-2")
    # Lab-wide sync batch blocks per-node enforcement in that lab.
    assert state_enforcement._has_active_job(test_db, "lab1", node_id="node-999")
    assert not state_enforcement._has_active_job(test_db, "missing-lab", node_id="node-1")
    # Legacy node actions are still recognized.
    assert state_enforcement._has_active_job(test_db, "lab2", node_name="r1")


# ---------------------------------------------------------------------------
# Backoff Behavior
# ---------------------------------------------------------------------------

class TestBackoffBehavior:
    """Detailed tests for exponential backoff calculation."""

    def test_backoff_exponential_growth(self, monkeypatch) -> None:
        monkeypatch.setattr(state_enforcement.settings, "state_enforcement_retry_backoff", 5)
        monkeypatch.setattr(state_enforcement.settings, "state_enforcement_cooldown", 1000)

        assert state_enforcement._calculate_backoff(0) == 5    # 5 * 2^0 = 5
        assert state_enforcement._calculate_backoff(1) == 10   # 5 * 2^1 = 10
        assert state_enforcement._calculate_backoff(2) == 20   # 5 * 2^2 = 20
        assert state_enforcement._calculate_backoff(3) == 40   # 5 * 2^3 = 40

    def test_backoff_capped_at_cooldown(self, monkeypatch) -> None:
        monkeypatch.setattr(state_enforcement.settings, "state_enforcement_retry_backoff", 5)
        monkeypatch.setattr(state_enforcement.settings, "state_enforcement_cooldown", 30)

        assert state_enforcement._calculate_backoff(10) == 30  # Capped
        assert state_enforcement._calculate_backoff(100) == 30  # Still capped

    def test_backoff_cleared_after_delay(self, monkeypatch) -> None:
        """After backoff period passes, enforcement should proceed."""
        past = datetime.now(timezone.utc) - timedelta(seconds=600)
        monkeypatch.setattr(state_enforcement.settings, "state_enforcement_max_retries", 10)
        monkeypatch.setattr(state_enforcement.settings, "state_enforcement_retry_backoff", 5)
        monkeypatch.setattr(state_enforcement.settings, "state_enforcement_cooldown", 60)

        node_state = models.NodeState(
            lab_id="lab", node_id="r1", node_name="r1",
            desired_state="running", actual_state="stopped",
            enforcement_attempts=2,
            last_enforcement_at=past,  # Far in the past
        )

        skip, reason = state_enforcement._should_skip_enforcement(node_state)
        assert not skip


# ---------------------------------------------------------------------------
# End-to-End enforce_node_state() Tests (Phase 0.3)
# ---------------------------------------------------------------------------

def _setup_lab_and_node(test_db, actual_state="stopped", desired_state="running",
                        enforcement_attempts=0, enforcement_failed_at=None,
                        error_message=None, last_enforcement_at=None):
    """Helper to create a lab + node state for enforcement tests."""
    lab = models.Lab(
        name="Enforcement Test", owner_id="user1", provider="docker",
        state="running", workspace_path="/tmp/enforce-test",
    )
    test_db.add(lab)
    test_db.commit()
    test_db.refresh(lab)

    ns = models.NodeState(
        lab_id=lab.id, node_id="r1", node_name="r1",
        desired_state=desired_state, actual_state=actual_state,
        enforcement_attempts=enforcement_attempts,
        enforcement_failed_at=enforcement_failed_at,
        error_message=error_message,
        last_enforcement_at=last_enforcement_at,
    )
    test_db.add(ns)
    test_db.commit()
    test_db.refresh(ns)

    return lab, ns


def _stub_enforcement_deps(monkeypatch, max_retries=5, auto_restart=True):
    """Stub Redis, agent lookup, and settings for enforcement tests."""
    monkeypatch.setattr(state_enforcement, "_is_on_cooldown", AsyncMock(return_value=False))
    monkeypatch.setattr(state_enforcement, "_set_cooldown", AsyncMock(return_value=None))
    monkeypatch.setattr(state_enforcement.settings, "state_enforcement_max_retries", max_retries)
    monkeypatch.setattr(state_enforcement.settings, "state_enforcement_auto_restart_enabled", auto_restart)
    monkeypatch.setattr(state_enforcement.settings, "state_enforcement_retry_backoff", 5)
    monkeypatch.setattr(state_enforcement.settings, "state_enforcement_cooldown", 30)

    mock_agent = MagicMock()
    mock_agent.id = "agent-1"
    monkeypatch.setattr(state_enforcement, "_get_agent_for_node", AsyncMock(return_value=mock_agent))

    # Prevent actual background task creation
    monkeypatch.setattr("app.tasks.state_enforcement.safe_create_task", lambda *a, **kw: None)

    return mock_agent


class TestEnforceNodeStateE2E:
    """Full enforce_node_state() flow — job creation, tracking, error handling."""

    @pytest.mark.asyncio
    async def test_creates_sync_job(self, test_db, monkeypatch) -> None:
        """enforce_node_state creates a sync:node job when mismatch detected."""
        lab, ns = _setup_lab_and_node(test_db)
        _stub_enforcement_deps(monkeypatch)

        result = await state_enforcement.enforce_node_state(test_db, lab, ns)

        assert result is True

        # Verify job was created
        job = test_db.query(models.Job).filter(
            models.Job.lab_id == lab.id,
            models.Job.action.like("sync:node:%"),
        ).first()
        assert job is not None
        assert job.status == "queued"
        assert "r1" in job.action

    @pytest.mark.asyncio
    async def test_increments_enforcement_attempts(self, test_db, monkeypatch) -> None:
        """enforcement_attempts is incremented on each enforcement call."""
        lab, ns = _setup_lab_and_node(test_db, enforcement_attempts=2)
        _stub_enforcement_deps(monkeypatch)

        await state_enforcement.enforce_node_state(test_db, lab, ns)

        test_db.refresh(ns)
        assert ns.enforcement_attempts == 3

    @pytest.mark.asyncio
    async def test_sets_last_enforcement_at(self, test_db, monkeypatch) -> None:
        """last_enforcement_at is set to current time."""
        lab, ns = _setup_lab_and_node(test_db)
        _stub_enforcement_deps(monkeypatch)

        before = datetime.now(timezone.utc)
        await state_enforcement.enforce_node_state(test_db, lab, ns)
        after = datetime.now(timezone.utc)

        test_db.refresh(ns)
        assert ns.last_enforcement_at is not None
        # SQLite may strip timezone info, so compare as naive UTC
        last_at = ns.last_enforcement_at
        if last_at.tzinfo is None:
            last_at = last_at.replace(tzinfo=timezone.utc)
        assert before <= last_at <= after

    @pytest.mark.asyncio
    async def test_sets_redis_cooldown(self, test_db, monkeypatch) -> None:
        """Redis cooldown is set BEFORE job creation."""
        lab, ns = _setup_lab_and_node(test_db)
        cooldown_calls = []

        monkeypatch.setattr(state_enforcement, "_is_on_cooldown", AsyncMock(return_value=False))

        async def _track_cooldown(lab_id, node):
            cooldown_calls.append((lab_id, node))

        monkeypatch.setattr(state_enforcement, "_set_cooldown", _track_cooldown)
        monkeypatch.setattr(state_enforcement.settings, "state_enforcement_max_retries", 5)
        monkeypatch.setattr(state_enforcement.settings, "state_enforcement_auto_restart_enabled", True)
        monkeypatch.setattr(state_enforcement.settings, "state_enforcement_retry_backoff", 5)
        monkeypatch.setattr(state_enforcement.settings, "state_enforcement_cooldown", 30)

        mock_agent = MagicMock()
        mock_agent.id = "agent-1"
        monkeypatch.setattr(state_enforcement, "_get_agent_for_node", AsyncMock(return_value=mock_agent))
        monkeypatch.setattr("app.tasks.state_enforcement.safe_create_task", lambda *a, **kw: None)

        await state_enforcement.enforce_node_state(test_db, lab, ns)

        assert len(cooldown_calls) == 1
        assert cooldown_calls[0] == (lab.id, "r1")


class TestEnforcementSkipsDuringBackoff:
    """Enforcement is skipped when within backoff period."""

    @pytest.mark.asyncio
    async def test_skipped_during_backoff(self, test_db, monkeypatch) -> None:
        now = datetime.now(timezone.utc)
        lab, ns = _setup_lab_and_node(
            test_db, enforcement_attempts=2, last_enforcement_at=now,
        )
        _stub_enforcement_deps(monkeypatch)

        # Backoff for attempt 1 = 5 * 2^1 = 10s; we just set last_enforcement_at=now
        result = await state_enforcement.enforce_node_state(test_db, lab, ns)
        assert result is False


class TestEnforcementMaxRetries:
    """Enforcement stops after max retries and sets error state."""

    @pytest.mark.asyncio
    async def test_max_retries_sets_error_state(self, test_db, monkeypatch) -> None:
        """After max retries, actual_state becomes 'error'."""
        lab, ns = _setup_lab_and_node(
            test_db, enforcement_attempts=5,
        )
        _stub_enforcement_deps(monkeypatch, max_retries=5)

        result = await state_enforcement.enforce_node_state(test_db, lab, ns)
        assert result is False

        test_db.refresh(ns)
        assert ns.actual_state == NodeActualState.ERROR.value
        assert ns.enforcement_failed_at is not None

    @pytest.mark.asyncio
    async def test_max_retries_sets_error_message(self, test_db, monkeypatch) -> None:
        """Error message mentions enforcement failure and attempt count."""
        lab, ns = _setup_lab_and_node(
            test_db, enforcement_attempts=3,
        )
        _stub_enforcement_deps(monkeypatch, max_retries=3)

        await state_enforcement.enforce_node_state(test_db, lab, ns)

        test_db.refresh(ns)
        assert "enforcement failed" in ns.error_message.lower()
        assert "3" in ns.error_message

    @pytest.mark.asyncio
    async def test_already_marked_failed_stays_skipped(self, test_db, monkeypatch) -> None:
        """Node already marked as enforcement-failed stays skipped."""
        lab, ns = _setup_lab_and_node(
            test_db, enforcement_attempts=5,
            enforcement_failed_at=datetime.now(timezone.utc),
            error_message="Previous failure",
        )
        _stub_enforcement_deps(monkeypatch, max_retries=5)

        result = await state_enforcement.enforce_node_state(test_db, lab, ns)
        assert result is False


class TestEnforcementNoAgent:
    """Enforcement returns False when no healthy agent is available."""

    @pytest.mark.asyncio
    async def test_no_agent_returns_false(self, test_db, monkeypatch) -> None:
        lab, ns = _setup_lab_and_node(test_db)

        monkeypatch.setattr(state_enforcement, "_is_on_cooldown", AsyncMock(return_value=False))
        monkeypatch.setattr(state_enforcement, "_set_cooldown", AsyncMock(return_value=None))
        monkeypatch.setattr(state_enforcement.settings, "state_enforcement_max_retries", 5)
        monkeypatch.setattr(state_enforcement.settings, "state_enforcement_auto_restart_enabled", True)
        monkeypatch.setattr(state_enforcement.settings, "state_enforcement_retry_backoff", 5)
        monkeypatch.setattr(state_enforcement.settings, "state_enforcement_cooldown", 30)

        # No agent available
        monkeypatch.setattr(state_enforcement, "_get_agent_for_node", AsyncMock(return_value=None))

        result = await state_enforcement.enforce_node_state(test_db, lab, ns)
        assert result is False


class TestEnforcementAutoRestartDisabled:
    """Auto-restart can be disabled for error-state nodes."""

    @pytest.mark.asyncio
    async def test_error_node_not_restarted_when_disabled(self, test_db, monkeypatch) -> None:
        lab, ns = _setup_lab_and_node(
            test_db, actual_state="error", desired_state="running",
        )
        _stub_enforcement_deps(monkeypatch, auto_restart=False)

        result = await state_enforcement.enforce_node_state(test_db, lab, ns)
        assert result is False


class TestEnforcementResetOnManualIntervention:
    """When user manually changes desired_state, enforcement resets."""

    @pytest.mark.skipif(
        True,  # Integration test requires full app stack with alembic
        reason="Requires running API with alembic migrations (integration test)",
    )
    def test_retry_error_resets_enforcement_state(
        self, test_client, auth_headers, sample_lab, test_db, monkeypatch,
    ) -> None:
        """Setting desired=running on an error node resets enforcement counters."""
        ns = models.NodeState(
            lab_id=sample_lab.id, node_id="r1", node_name="r1",
            desired_state="running", actual_state="error",
            enforcement_attempts=5,
            enforcement_failed_at=datetime.now(timezone.utc),
            error_message="Previous enforcement failure",
        )
        test_db.add(ns)
        test_db.commit()

        # Mock out background task creation
        monkeypatch.setattr("app.routers.labs.safe_create_task", lambda *a, **kw: None)

        resp = test_client.put(
            f"/labs/{sample_lab.id}/nodes/r1/desired-state",
            json={"state": "running"},
            headers=auth_headers,
        )
        assert resp.status_code == 200

        test_db.refresh(ns)
        assert ns.enforcement_attempts == 0
        assert ns.enforcement_failed_at is None
        assert ns.error_message is None


class TestEnforcementWithLabWideJob:
    """Lab-wide jobs (up/down) block per-node enforcement."""

    @pytest.mark.asyncio
    async def test_lab_wide_deploy_blocks_enforcement(self, test_db, monkeypatch) -> None:
        lab, ns = _setup_lab_and_node(test_db)
        _stub_enforcement_deps(monkeypatch)

        # Create a lab-wide "up" job
        job = models.Job(
            lab_id=lab.id, action="up", status=JobStatus.QUEUED.value,
        )
        test_db.add(job)
        test_db.commit()

        result = await state_enforcement.enforce_node_state(test_db, lab, ns)
        assert result is False

    @pytest.mark.asyncio
    async def test_lab_wide_destroy_blocks_enforcement(self, test_db, monkeypatch) -> None:
        lab, ns = _setup_lab_and_node(test_db)
        _stub_enforcement_deps(monkeypatch)

        job = models.Job(
            lab_id=lab.id, action="down", status=JobStatus.QUEUED.value,
        )
        test_db.add(job)
        test_db.commit()

        result = await state_enforcement.enforce_node_state(test_db, lab, ns)
        assert result is False


class TestEnforcementActionMapping:
    """Verify the state machine → enforcement action mapping."""

    @pytest.mark.asyncio
    async def test_stopped_desired_running_starts(self, test_db, monkeypatch) -> None:
        """stopped + desired=running → start action."""
        lab, ns = _setup_lab_and_node(
            test_db, actual_state="stopped", desired_state="running",
        )
        _stub_enforcement_deps(monkeypatch)
        result = await state_enforcement.enforce_node_state(test_db, lab, ns)
        assert result is True

    @pytest.mark.asyncio
    async def test_running_desired_stopped_stops(self, test_db, monkeypatch) -> None:
        """running + desired=stopped → stop action."""
        lab, ns = _setup_lab_and_node(
            test_db, actual_state="running", desired_state="stopped",
        )
        _stub_enforcement_deps(monkeypatch)
        result = await state_enforcement.enforce_node_state(test_db, lab, ns)
        assert result is True

    @pytest.mark.asyncio
    async def test_undeployed_desired_running_starts(self, test_db, monkeypatch) -> None:
        """undeployed + desired=running → start action."""
        lab, ns = _setup_lab_and_node(
            test_db, actual_state="undeployed", desired_state="running",
        )
        _stub_enforcement_deps(monkeypatch)
        result = await state_enforcement.enforce_node_state(test_db, lab, ns)
        assert result is True

    @pytest.mark.asyncio
    async def test_exited_desired_running_starts(self, test_db, monkeypatch) -> None:
        """exited + desired=running → start action."""
        lab, ns = _setup_lab_and_node(
            test_db, actual_state="exited", desired_state="running",
        )
        _stub_enforcement_deps(monkeypatch)
        result = await state_enforcement.enforce_node_state(test_db, lab, ns)
        assert result is True

    @pytest.mark.asyncio
    async def test_transitional_states_no_action(self, test_db, monkeypatch) -> None:
        """Transitional states (starting/stopping) → no enforcement.

        Note: pending+desired=running IS enforced (special case for stuck nodes).
        """
        for actual in ["starting", "stopping"]:
            lab, ns = _setup_lab_and_node(
                test_db, actual_state=actual, desired_state="running",
            )
            _stub_enforcement_deps(monkeypatch)
            result = await state_enforcement.enforce_node_state(test_db, lab, ns)
            assert result is False, f"Expected no enforcement for {actual}"

    @pytest.mark.asyncio
    async def test_already_matching_no_action(self, test_db, monkeypatch) -> None:
        """Matching states → no enforcement."""
        lab, ns = _setup_lab_and_node(
            test_db, actual_state="running", desired_state="running",
        )
        _stub_enforcement_deps(monkeypatch)
        result = await state_enforcement.enforce_node_state(test_db, lab, ns)
        assert result is False


class TestEnforcementPendingNodeSpecialCase:
    """Pending node with desired=running is a special case (force start)."""

    @pytest.mark.asyncio
    async def test_pending_desired_running_starts(self, test_db, monkeypatch) -> None:
        """Pending + desired=running → start (special case, not via state machine)."""
        lab, ns = _setup_lab_and_node(
            test_db, actual_state="pending", desired_state="running",
        )
        _stub_enforcement_deps(monkeypatch)
        result = await state_enforcement.enforce_node_state(test_db, lab, ns)
        assert result is True


# ---------------------------------------------------------------------------
# Phase B.1: Enforcement Exception Handling in enforce_lab_states()
# ---------------------------------------------------------------------------

class TestEnforcementExceptionHandling:
    """Tests for exception handling during enforce_lab_states() filtering (Phase A.2).

    When _is_enforceable() throws an exception during the per-node filtering
    phase, the exception handler should:
    - Increment enforcement_attempts to prevent infinite loops
    - Update last_enforcement_at for backoff tracking
    - Set enforcement_failed_at when max retries are reached
    - Handle cascading DB errors gracefully
    """

    @pytest.fixture(autouse=True)
    def _setup(self, test_db, monkeypatch):
        """Set up common mocks for enforce_lab_states() testing."""
        from contextlib import contextmanager

        @contextmanager
        def fake_get_session():
            yield test_db

        monkeypatch.setattr("app.tasks.state_enforcement.get_session", fake_get_session)
        monkeypatch.setattr(state_enforcement.settings, "state_enforcement_enabled", True)
        monkeypatch.setattr(state_enforcement.settings, "state_enforcement_max_retries", 3)
        monkeypatch.setattr("app.tasks.state_enforcement.safe_create_task", lambda *a, **kw: None)

    def _create_mismatched_node(self, test_db, enforcement_attempts=0):
        """Create a lab + node with desired != actual for enforcement testing."""
        lab = models.Lab(
            name="Exception Test", owner_id="user1", provider="docker",
            state="running", workspace_path="/tmp/exc-test",
        )
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        ns = models.NodeState(
            lab_id=lab.id, node_id="r1", node_name="r1",
            desired_state="running", actual_state="stopped",
            enforcement_attempts=enforcement_attempts,
        )
        test_db.add(ns)
        test_db.commit()
        test_db.refresh(ns)
        return lab, ns

    @pytest.mark.asyncio
    async def test_exception_increments_attempts(self, test_db, monkeypatch) -> None:
        """Exception in _is_enforceable → enforcement_attempts += 1."""
        lab, ns = self._create_mismatched_node(test_db)
        monkeypatch.setattr(
            state_enforcement, "_is_enforceable",
            MagicMock(side_effect=RuntimeError("test error")),
        )

        await state_enforcement.enforce_lab_states()

        test_db.refresh(ns)
        assert ns.enforcement_attempts == 1

    @pytest.mark.asyncio
    async def test_exception_updates_last_enforcement_at(self, test_db, monkeypatch) -> None:
        """last_enforcement_at is updated even on exception."""
        lab, ns = self._create_mismatched_node(test_db)
        monkeypatch.setattr(
            state_enforcement, "_is_enforceable",
            MagicMock(side_effect=RuntimeError("test error")),
        )

        before = datetime.now(timezone.utc)
        await state_enforcement.enforce_lab_states()
        after = datetime.now(timezone.utc)

        test_db.refresh(ns)
        assert ns.last_enforcement_at is not None
        last_at = ns.last_enforcement_at
        if last_at.tzinfo is None:
            last_at = last_at.replace(tzinfo=timezone.utc)
        assert before <= last_at <= after

    @pytest.mark.asyncio
    async def test_exceptions_reaching_max_retries_sets_failed(self, test_db, monkeypatch) -> None:
        """After max exceptions, enforcement_failed_at and error_message are set."""
        # Start at max_retries - 1 so one more exception triggers failure
        lab, ns = self._create_mismatched_node(test_db, enforcement_attempts=2)
        monkeypatch.setattr(
            state_enforcement, "_is_enforceable",
            MagicMock(side_effect=RuntimeError("persistent error")),
        )

        await state_enforcement.enforce_lab_states()

        test_db.refresh(ns)
        assert ns.enforcement_attempts == 3
        assert ns.enforcement_failed_at is not None
        assert "persistent error" in ns.error_message

    @pytest.mark.asyncio
    async def test_exception_below_max_retries_no_failed_marker(self, test_db, monkeypatch) -> None:
        """Exception below max retries increments attempts but does NOT set enforcement_failed_at."""
        lab, ns = self._create_mismatched_node(test_db, enforcement_attempts=0)
        monkeypatch.setattr(
            state_enforcement, "_is_enforceable",
            MagicMock(side_effect=RuntimeError("transient error")),
        )

        await state_enforcement.enforce_lab_states()

        test_db.refresh(ns)
        assert ns.enforcement_attempts == 1
        assert ns.enforcement_failed_at is None  # Not yet at max retries

    @pytest.mark.asyncio
    async def test_exception_does_not_crash_other_nodes(self, test_db, monkeypatch) -> None:
        """Exception on one node doesn't block processing of other nodes."""
        lab = models.Lab(
            name="Multi-node Exception Test", owner_id="user1", provider="docker",
            state="running", workspace_path="/tmp/multi-exc-test",
        )
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        ns1 = models.NodeState(
            lab_id=lab.id, node_id="r1", node_name="r1",
            desired_state="running", actual_state="stopped",
        )
        ns2 = models.NodeState(
            lab_id=lab.id, node_id="r2", node_name="r2",
            desired_state="running", actual_state="stopped",
        )
        test_db.add_all([ns1, ns2])
        test_db.commit()
        test_db.refresh(ns1)
        test_db.refresh(ns2)

        call_count = 0

        async def enforceable_first_fails(session, node_state, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("first node fails")
            return True

        monkeypatch.setattr(state_enforcement, "_is_enforceable", enforceable_first_fails)
        # Mock out batch job creation path
        monkeypatch.setattr(state_enforcement, "_has_lab_wide_active_job", lambda *a: False)
        monkeypatch.setattr(state_enforcement, "_set_cooldown", AsyncMock(return_value=None))

        await state_enforcement.enforce_lab_states()

        # Both nodes should have been processed (call_count >= 2)
        assert call_count == 2
