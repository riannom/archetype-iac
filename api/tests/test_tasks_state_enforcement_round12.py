"""State enforcement deep-branch tests (Round 12).

Targets under-tested conditional branches in state_enforcement.py:
- _is_enforceable(): all conditional paths including transitional states,
  naive datetime handling, image_sync "checking" variant, and preloaded
  active-job set interactions
- _should_skip_enforcement(): naive datetime (SQLite) branch via _ensure_aware
- state_enforcement_monitor(): loop iteration, error recovery, CancelledError
- _has_active_job(): lab-wide-only query path (no node_name/node_id)
- enforce_node_state(): placement creation when node_def is missing,
  placement backfill without node_definition_id
- _has_lab_wide_active_job(): DB query fallback path
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app import models
from app.state import JobStatus, NodeActualState
import app.tasks.state_enforcement as se


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mk_ns(
    *,
    lab_id: str = "lab-1",
    node_id: str = "n1",
    node_name: str = "r1",
    desired: str = "running",
    actual: str = "stopped",
    attempts: int = 0,
    failed_at: datetime | None = None,
    last_at: datetime | None = None,
    image_sync_status: str | None = None,
    error_message: str | None = None,
) -> models.NodeState:
    return models.NodeState(
        lab_id=lab_id,
        node_id=node_id,
        node_name=node_name,
        desired_state=desired,
        actual_state=actual,
        enforcement_attempts=attempts,
        enforcement_failed_at=failed_at,
        last_enforcement_at=last_at,
        image_sync_status=image_sync_status,
        error_message=error_message,
    )


def _stub_settings(monkeypatch, **overrides):
    """Apply common settings for enforcement tests."""
    defaults = dict(
        state_enforcement_max_retries=5,
        state_enforcement_retry_backoff=5,
        state_enforcement_cooldown=30,
        state_enforcement_crash_cooldown=60,
        state_enforcement_auto_restart_enabled=True,
    )
    defaults.update(overrides)
    for key, val in defaults.items():
        monkeypatch.setattr(se.settings, key, val)


# ===========================================================================
# 1. _is_enforceable: transitional actual states yield no action
# ===========================================================================


class TestIsEnforceableTransitionalStates:
    """Nodes in transitional states (starting, stopping) are not enforceable."""

    @pytest.mark.asyncio
    async def test_starting_state_not_enforceable(self, monkeypatch, test_db):
        _stub_settings(monkeypatch)
        ns = _mk_ns(actual="starting", desired="running")
        result = await se._is_enforceable(test_db, ns)
        assert result is False

    @pytest.mark.asyncio
    async def test_stopping_state_not_enforceable(self, monkeypatch, test_db):
        _stub_settings(monkeypatch)
        ns = _mk_ns(actual="stopping", desired="stopped")
        result = await se._is_enforceable(test_db, ns)
        assert result is False


# ===========================================================================
# 2. _is_enforceable: image_sync_status == "checking" also skips
# ===========================================================================


class TestIsEnforceableImageSyncChecking:
    """image_sync_status='checking' should skip enforcement just like 'syncing'."""

    @pytest.mark.asyncio
    async def test_checking_skips_enforcement(self, monkeypatch, test_db):
        _stub_settings(monkeypatch)
        ns = _mk_ns(image_sync_status="checking")
        result = await se._is_enforceable(test_db, ns)
        assert result is False


# ===========================================================================
# 3. _should_skip_enforcement: naive datetime branch (_ensure_aware)
# ===========================================================================


class TestShouldSkipNaiveDatetime:
    """SQLite strips timezone info; _ensure_aware should handle naive datetimes."""

    def test_naive_enforcement_failed_at_triggers_cooldown(self, monkeypatch):
        _stub_settings(monkeypatch, state_enforcement_crash_cooldown=120)
        # Create a naive datetime (as SQLite would return)
        naive_failed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        ns = _mk_ns(attempts=1, failed_at=naive_failed_at)
        skip, reason = se._should_skip_enforcement(ns)
        assert skip is True
        assert "crash cooldown" in reason

    def test_naive_last_enforcement_at_triggers_backoff(self, monkeypatch):
        _stub_settings(monkeypatch)
        # Naive datetime, recent enforcement
        naive_last_at = datetime.now(timezone.utc).replace(tzinfo=None)
        ns = _mk_ns(attempts=2, last_at=naive_last_at)
        skip, reason = se._should_skip_enforcement(ns)
        assert skip is True
        assert "backoff" in reason

    def test_max_retries_reached_without_failed_at(self, monkeypatch):
        """When enforcement_failed_at is None but max retries reached,
        returns 'max retries reached' (not 'exhausted')."""
        _stub_settings(monkeypatch, state_enforcement_max_retries=3)
        ns = _mk_ns(attempts=3, failed_at=None)
        skip, reason = se._should_skip_enforcement(ns)
        assert skip is True
        assert "max retries reached" in reason

    def test_max_retries_with_failed_at_returns_exhausted(self, monkeypatch):
        """When enforcement_failed_at is set AND max retries reached,
        returns 'max retries exhausted'."""
        _stub_settings(monkeypatch, state_enforcement_max_retries=3)
        ns = _mk_ns(
            attempts=3,
            failed_at=datetime.now(timezone.utc),
        )
        skip, reason = se._should_skip_enforcement(ns)
        assert skip is True
        assert "max retries exhausted" in reason


# ===========================================================================
# 4. _is_enforceable: max retries marks failure and schedules notification
#    (with existing error_message preserved in "Last error:")
# ===========================================================================


class TestIsEnforceableMaxRetriesErrorPreservation:
    """When max retries hit, the original error_message is included in the new message."""

    @pytest.mark.asyncio
    async def test_original_error_preserved(self, monkeypatch, test_db):
        _stub_settings(monkeypatch, state_enforcement_max_retries=2)
        monkeypatch.setattr(
            se, "_should_skip_enforcement",
            lambda _ns: (True, "max retries reached"),
        )

        scheduled = []

        def _safe(coro, *, name):
            scheduled.append(name)
            if asyncio.iscoroutine(coro):
                coro.close()

        monkeypatch.setattr(se, "safe_create_task", _safe)
        monkeypatch.setattr(se, "record_enforcement_action", lambda *_: None)
        monkeypatch.setattr(se, "record_enforcement_exhausted", lambda: None)

        ns = _mk_ns(attempts=2, error_message="container OOM killed")
        result = await se._is_enforceable(test_db, ns)

        assert result is False
        assert ns.actual_state == NodeActualState.ERROR.value
        assert "container OOM killed" in (ns.error_message or "")
        assert "Last error" in (ns.error_message or "")
        assert scheduled  # notification was scheduled

    @pytest.mark.asyncio
    async def test_unknown_error_when_none(self, monkeypatch, test_db):
        """When original error_message is None, 'unknown' is used."""
        _stub_settings(monkeypatch, state_enforcement_max_retries=2)
        monkeypatch.setattr(
            se, "_should_skip_enforcement",
            lambda _ns: (True, "max retries reached"),
        )

        def _safe(coro, *, name):
            if asyncio.iscoroutine(coro):
                coro.close()

        monkeypatch.setattr(se, "safe_create_task", _safe)
        monkeypatch.setattr(se, "record_enforcement_action", lambda *_: None)
        monkeypatch.setattr(se, "record_enforcement_exhausted", lambda: None)

        ns = _mk_ns(attempts=2, error_message=None)
        await se._is_enforceable(test_db, ns)
        assert "unknown" in (ns.error_message or "")


# ===========================================================================
# 5. _is_enforceable: preloaded active job sets — both name and id checked
# ===========================================================================


class TestIsEnforceablePreloadedJobSets:
    """Test the D.1 optimization: preloaded sets bypass DB queries."""

    @pytest.mark.asyncio
    async def test_name_set_blocks(self, monkeypatch, test_db):
        _stub_settings(monkeypatch)
        monkeypatch.setattr(se, "_should_skip_enforcement", lambda _ns: (False, ""))
        monkeypatch.setattr(se, "_is_on_cooldown", AsyncMock(return_value=False))

        ns = _mk_ns(lab_id="lab-x", node_name="sw1", node_id="id-1")
        result = await se._is_enforceable(
            test_db, ns,
            active_job_node_names={("lab-x", "sw1")},
            active_job_node_ids=set(),
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_id_set_blocks_when_name_does_not_match(self, monkeypatch, test_db):
        _stub_settings(monkeypatch)
        monkeypatch.setattr(se, "_should_skip_enforcement", lambda _ns: (False, ""))
        monkeypatch.setattr(se, "_is_on_cooldown", AsyncMock(return_value=False))

        ns = _mk_ns(lab_id="lab-x", node_name="sw1", node_id="id-1")
        result = await se._is_enforceable(
            test_db, ns,
            active_job_node_names=set(),  # name not matched
            active_job_node_ids={("lab-x", "id-1")},  # but id matches
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_neither_set_matches_allows_enforcement(self, monkeypatch, test_db):
        _stub_settings(monkeypatch)
        monkeypatch.setattr(se, "_should_skip_enforcement", lambda _ns: (False, ""))
        monkeypatch.setattr(se, "_is_on_cooldown", AsyncMock(return_value=False))

        ns = _mk_ns(lab_id="lab-x", node_name="sw1", node_id="id-1")
        result = await se._is_enforceable(
            test_db, ns,
            active_job_node_names=set(),
            active_job_node_ids=set(),
        )
        assert result is True


# ===========================================================================
# 6. _has_active_job: lab-wide query (no node_name, no node_id)
# ===========================================================================


class TestHasActiveJobLabWideOnly:
    """When neither node_name nor node_id is given, the function does a simple
    lab-wide check for any active job."""

    def test_lab_has_active_job(self, test_db):
        lab = models.Lab(
            name="Job check", owner_id="u1", provider="docker",
            state="running", workspace_path="/tmp/jc",
        )
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        job = models.Job(
            lab_id=lab.id, action="up", status=JobStatus.QUEUED.value,
        )
        test_db.add(job)
        test_db.commit()

        assert se._has_active_job(test_db, lab.id) is True

    def test_lab_has_no_active_job(self, test_db):
        lab = models.Lab(
            name="Empty", owner_id="u1", provider="docker",
            state="running", workspace_path="/tmp/e",
        )
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        assert se._has_active_job(test_db, lab.id) is False

    def test_completed_jobs_not_counted(self, test_db):
        lab = models.Lab(
            name="Done", owner_id="u1", provider="docker",
            state="running", workspace_path="/tmp/d",
        )
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        job = models.Job(
            lab_id=lab.id, action="up", status="completed",
        )
        test_db.add(job)
        test_db.commit()

        assert se._has_active_job(test_db, lab.id) is False


# ===========================================================================
# 7. _has_active_job: sync:agent with short action string (< 4 parts)
# ===========================================================================


class TestHasActiveJobSyncAgentShortAction:
    """sync:agent action with fewer than 4 parts should not match any node."""

    def test_short_sync_agent_no_match(self, test_db):
        lab = models.Lab(
            name="Short", owner_id="u1", provider="docker",
            state="running", workspace_path="/tmp/s",
        )
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        # sync:agent:agent-1 has only 3 parts — missing node CSV
        job = models.Job(
            lab_id=lab.id, action="sync:agent:agent-1",
            status=JobStatus.QUEUED.value,
        )
        test_db.add(job)
        test_db.commit()

        result = se._has_active_job(
            test_db, lab.id, node_name="r1", node_id="n1",
        )
        assert result is False


# ===========================================================================
# 8. state_enforcement_monitor: normal iteration + error + cancel
# ===========================================================================


class TestStateEnforcementMonitor:
    """Test the monitor loop lifecycle."""

    @pytest.mark.asyncio
    async def test_normal_iteration_then_cancel(self, monkeypatch):
        """Monitor calls enforce_lab_states, then exits on CancelledError."""
        monkeypatch.setattr(se.settings, "state_enforcement_enabled", True)
        monkeypatch.setattr(se.settings, "state_enforcement_cooldown", 10)
        monkeypatch.setattr(
            type(se.settings), "get_interval", lambda self, _name: 0,
        )

        call_count = 0

        async def _fake_enforce():
            nonlocal call_count
            call_count += 1
            raise asyncio.CancelledError()

        monkeypatch.setattr(se.asyncio, "sleep", AsyncMock(return_value=None))
        monkeypatch.setattr(se, "enforce_lab_states", _fake_enforce)

        await se.state_enforcement_monitor()
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_error_continues_loop(self, monkeypatch):
        """Generic exceptions don't stop the monitor — it keeps looping."""
        monkeypatch.setattr(se.settings, "state_enforcement_enabled", True)
        monkeypatch.setattr(se.settings, "state_enforcement_cooldown", 10)
        monkeypatch.setattr(
            type(se.settings), "get_interval", lambda self, _name: 0,
        )

        calls = {"n": 0}

        async def _fake_enforce():
            calls["n"] += 1
            if calls["n"] <= 3:
                raise RuntimeError("transient error")
            raise asyncio.CancelledError()

        monkeypatch.setattr(se.asyncio, "sleep", AsyncMock(return_value=None))
        monkeypatch.setattr(se, "enforce_lab_states", _fake_enforce)

        await se.state_enforcement_monitor()
        assert calls["n"] == 4  # 3 errors + 1 cancel


# ===========================================================================
# 9. enforce_node_state: placement creation skipped when node_def missing
# ===========================================================================


class TestEnforceNodeStatePlacementBranches:
    """Test placement creation/update branches in enforce_node_state."""

    @pytest.mark.asyncio
    async def test_no_placement_no_node_def_logs_warning(self, test_db, monkeypatch):
        """When node_def is not found and no placement exists, a warning is
        logged and no placement is created."""
        lab = models.Lab(
            name="No placement", owner_id="u1", provider="docker",
            state="running", workspace_path="/tmp/np",
        )
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        ns = _mk_ns(lab_id=lab.id, node_name="orphan", node_id="orphan-id")
        test_db.add(ns)
        test_db.commit()
        test_db.refresh(ns)

        agent = MagicMock()
        agent.id = "a1"

        _stub_settings(monkeypatch)
        monkeypatch.setattr(se, "_is_on_cooldown", AsyncMock(return_value=False))
        monkeypatch.setattr(se, "_set_cooldown", AsyncMock(return_value=None))
        monkeypatch.setattr(se, "_has_active_job", lambda *a, **kw: False)
        monkeypatch.setattr(se, "_get_agent_for_node", AsyncMock(return_value=agent))

        import app.utils.lab as lab_utils
        monkeypatch.setattr(lab_utils, "get_lab_provider", lambda _: "docker")

        scheduled = []

        def _safe(coro, *, name):
            scheduled.append(name)
            if asyncio.iscoroutine(coro):
                coro.close()

        monkeypatch.setattr(se, "safe_create_task", _safe)
        monkeypatch.setattr(se, "record_enforcement_action", lambda *_: None)

        result = await se.enforce_node_state(test_db, lab, ns)
        assert result is True

        # No placement should have been created
        placements = test_db.query(models.NodePlacement).filter(
            models.NodePlacement.lab_id == lab.id,
        ).all()
        assert len(placements) == 0

    @pytest.mark.asyncio
    async def test_existing_placement_backfills_node_definition_id(
        self, test_db, monkeypatch,
    ):
        """When placement exists without node_definition_id, it gets backfilled."""
        lab = models.Lab(
            name="Backfill", owner_id="u1", provider="docker",
            state="running", workspace_path="/tmp/bf",
        )
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        node = models.Node(
            lab_id=lab.id, gui_id="g1", display_name="r1",
            container_name="r1", node_type="device", device="ceos",
        )
        test_db.add(node)
        test_db.commit()
        test_db.refresh(node)

        ns = _mk_ns(lab_id=lab.id, node_name="r1", node_id="n1")
        ns.node_definition_id = node.id
        test_db.add(ns)
        test_db.commit()
        test_db.refresh(ns)

        agent = MagicMock()
        agent.id = "a1"

        # Placement with no node_definition_id
        placement = models.NodePlacement(
            lab_id=lab.id, node_name="r1",
            node_definition_id=None, host_id=agent.id, status="deployed",
        )
        test_db.add(placement)
        test_db.commit()

        _stub_settings(monkeypatch)
        monkeypatch.setattr(se, "_is_on_cooldown", AsyncMock(return_value=False))
        monkeypatch.setattr(se, "_set_cooldown", AsyncMock(return_value=None))
        monkeypatch.setattr(se, "_has_active_job", lambda *a, **kw: False)
        monkeypatch.setattr(se, "_get_agent_for_node", AsyncMock(return_value=agent))

        import app.utils.lab as lab_utils
        monkeypatch.setattr(lab_utils, "get_node_provider", lambda *_: "docker")
        monkeypatch.setattr(lab_utils, "get_lab_provider", lambda _: "docker")

        def _safe(coro, *, name):
            if asyncio.iscoroutine(coro):
                coro.close()

        monkeypatch.setattr(se, "safe_create_task", _safe)
        monkeypatch.setattr(se, "record_enforcement_action", lambda *_: None)

        result = await se.enforce_node_state(test_db, lab, ns)
        assert result is True

        test_db.refresh(placement)
        assert placement.node_definition_id == node.id


# ===========================================================================
# 10. _is_enforceable: auto-restart disabled blocks error→running enforcement
# ===========================================================================


class TestIsEnforceableAutoRestartDisabled:
    """When auto_restart is disabled, error-state nodes are not enforceable."""

    @pytest.mark.asyncio
    async def test_error_node_blocked_when_auto_restart_off(self, monkeypatch, test_db):
        _stub_settings(monkeypatch, state_enforcement_auto_restart_enabled=False)
        ns = _mk_ns(actual="error", desired="running")
        result = await se._is_enforceable(test_db, ns)
        assert result is False

    @pytest.mark.asyncio
    async def test_error_node_allowed_when_auto_restart_on(self, monkeypatch, test_db):
        _stub_settings(monkeypatch)
        monkeypatch.setattr(se, "_should_skip_enforcement", lambda _: (False, ""))
        monkeypatch.setattr(se, "_is_on_cooldown", AsyncMock(return_value=False))
        monkeypatch.setattr(se, "_has_active_job", lambda *a, **kw: False)

        ns = _mk_ns(actual="error", desired="running")
        result = await se._is_enforceable(test_db, ns)
        assert result is True


# ===========================================================================
# 11. _has_lab_wide_active_job: DB fallback when preloaded set is None
# ===========================================================================


class TestHasLabWideActiveJobDBFallback:
    """Verify the DB query path when labs_with_active_jobs is None."""

    def test_db_fallback_finds_sync_lab_job(self, test_db):
        lab = models.Lab(
            name="SyncLab", owner_id="u1", provider="docker",
            state="running", workspace_path="/tmp/sl",
        )
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        job = models.Job(
            lab_id=lab.id, action="sync:lab", status=JobStatus.RUNNING.value,
        )
        test_db.add(job)
        test_db.commit()

        assert se._has_lab_wide_active_job(test_db, lab.id) is True

    def test_db_fallback_finds_sync_batch_job(self, test_db):
        lab = models.Lab(
            name="BatchLab", owner_id="u1", provider="docker",
            state="running", workspace_path="/tmp/bl",
        )
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        job = models.Job(
            lab_id=lab.id, action="sync:batch:5",
            status=JobStatus.QUEUED.value,
        )
        test_db.add(job)
        test_db.commit()

        assert se._has_lab_wide_active_job(test_db, lab.id) is True

    def test_db_fallback_node_job_not_lab_wide(self, test_db):
        lab = models.Lab(
            name="NodeOnly", owner_id="u1", provider="docker",
            state="running", workspace_path="/tmp/no",
        )
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        job = models.Job(
            lab_id=lab.id, action="sync:node:n1",
            status=JobStatus.QUEUED.value,
        )
        test_db.add(job)
        test_db.commit()

        assert se._has_lab_wide_active_job(test_db, lab.id) is False


# ===========================================================================
# 12. enforce_lab_states: exception in _is_enforceable cascading rollback
# ===========================================================================


class TestEnforceLabStatesExceptionRecovery:
    """Ensure that exceptions during per-node filtering don't crash the loop,
    and that nested rollback failures are silently absorbed."""

    @pytest.fixture(autouse=True)
    def _setup(self, test_db, monkeypatch):
        @contextmanager
        def fake_get_session():
            yield test_db

        monkeypatch.setattr(se, "get_session", fake_get_session)
        monkeypatch.setattr(se.settings, "state_enforcement_enabled", True)
        monkeypatch.setattr(se, "safe_create_task", lambda *a, **kw: None)

    @pytest.mark.asyncio
    async def test_exception_increments_and_commit_failure_does_not_crash(
        self, test_db, monkeypatch,
    ):
        """When _is_enforceable raises and the subsequent commit also fails,
        the outer except catches it without crashing."""
        _stub_settings(monkeypatch, state_enforcement_max_retries=10)

        lab = models.Lab(
            name="Nested exc", owner_id="u1", provider="docker",
            state="running", workspace_path="/tmp/ne",
        )
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        ns = _mk_ns(lab_id=lab.id, node_id="bad", node_name="bad")
        test_db.add(ns)
        test_db.commit()

        call_count = 0

        async def _boom(session, node_state, **kwargs):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("filter error")

        monkeypatch.setattr(se, "_is_enforceable", _boom)

        # Make commit fail after rollback (nested exception path)
        original_commit = test_db.commit
        commit_call = {"n": 0}

        def _flaky_commit():
            commit_call["n"] += 1
            # Let the first commit (from rollback recovery) fail
            if commit_call["n"] == 1:
                raise RuntimeError("commit failed")
            return original_commit()

        monkeypatch.setattr(test_db, "commit", _flaky_commit)

        # Should not raise
        await se.enforce_lab_states()
        assert call_count == 1
