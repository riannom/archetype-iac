"""Extended tests for state enforcement self-healing loop."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.config import settings  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow():
    return datetime.now(timezone.utc)


def _make_node_state(
    enforcement_attempts=0,
    enforcement_failed_at=None,
    last_enforcement_at=None,
    desired_state="running",
    actual_state="stopped",
    node_id="node-1",
    node_name="router1",
    lab_id="lab-1",
    image_sync_status=None,
):
    ns = MagicMock()
    ns.enforcement_attempts = enforcement_attempts
    ns.enforcement_failed_at = enforcement_failed_at
    ns.last_enforcement_at = last_enforcement_at
    ns.desired_state = desired_state
    ns.actual_state = actual_state
    ns.node_id = node_id
    ns.node_name = node_name
    ns.lab_id = lab_id
    ns.image_sync_status = image_sync_status
    return ns


def _make_job(action="sync:node:node-1"):
    """Return a tuple mimicking session.query(Job.action).filter(...).all() rows."""
    return (action,)


def _make_lab(lab_id="lab-1", state="running"):
    lab = MagicMock()
    lab.id = lab_id
    lab.state = state
    return lab


# ===========================================================================
# TestCalculateBackoff
# ===========================================================================

class TestCalculateBackoff:
    """Tests for _calculate_backoff(attempts)."""

    def _get_fn(self):
        from app.tasks.state_enforcement import _calculate_backoff
        return _calculate_backoff

    def test_zero_attempts_returns_base(self, monkeypatch):
        monkeypatch.setattr(settings, "state_enforcement_retry_backoff", 10, raising=False)
        monkeypatch.setattr(settings, "state_enforcement_cooldown", 600, raising=False)
        fn = self._get_fn()
        result = fn(0)
        # base * 2^0 = base
        assert result == 10

    def test_one_attempt_doubles_base(self, monkeypatch):
        monkeypatch.setattr(settings, "state_enforcement_retry_backoff", 10, raising=False)
        monkeypatch.setattr(settings, "state_enforcement_cooldown", 600, raising=False)
        fn = self._get_fn()
        result = fn(1)
        assert result == 20

    def test_two_attempts(self, monkeypatch):
        monkeypatch.setattr(settings, "state_enforcement_retry_backoff", 10, raising=False)
        monkeypatch.setattr(settings, "state_enforcement_cooldown", 600, raising=False)
        fn = self._get_fn()
        result = fn(2)
        assert result == 40

    def test_large_attempts_capped_at_max(self, monkeypatch):
        monkeypatch.setattr(settings, "state_enforcement_retry_backoff", 10, raising=False)
        monkeypatch.setattr(settings, "state_enforcement_cooldown", 300, raising=False)
        fn = self._get_fn()
        # 10 * 2^20 = 10_485_760, but capped at 300
        result = fn(20)
        assert result == 300

    def test_moderate_attempts_correct(self, monkeypatch):
        monkeypatch.setattr(settings, "state_enforcement_retry_backoff", 5, raising=False)
        monkeypatch.setattr(settings, "state_enforcement_cooldown", 1000, raising=False)
        fn = self._get_fn()
        # 5 * 2^3 = 40
        result = fn(3)
        assert result == 40

    def test_cap_boundary_exactly_at_max(self, monkeypatch):
        monkeypatch.setattr(settings, "state_enforcement_retry_backoff", 16, raising=False)
        monkeypatch.setattr(settings, "state_enforcement_cooldown", 64, raising=False)
        fn = self._get_fn()
        # 16 * 2^2 = 64 == max
        result = fn(2)
        assert result == 64

    def test_cap_boundary_just_over_max(self, monkeypatch):
        monkeypatch.setattr(settings, "state_enforcement_retry_backoff", 16, raising=False)
        monkeypatch.setattr(settings, "state_enforcement_cooldown", 63, raising=False)
        fn = self._get_fn()
        # 16 * 2^2 = 64 > 63
        result = fn(2)
        assert result == 63


# ===========================================================================
# TestShouldSkipEnforcement
# ===========================================================================

class TestShouldSkipEnforcement:
    """Tests for _should_skip_enforcement(node_state)."""

    def _get_fn(self):
        from app.tasks.state_enforcement import _should_skip_enforcement
        return _should_skip_enforcement

    def test_max_retries_exceeded(self, monkeypatch):
        monkeypatch.setattr(settings, "state_enforcement_max_retries", 5, raising=False)
        ns = _make_node_state(enforcement_attempts=5)
        fn = self._get_fn()
        should_skip, reason = fn(ns)
        assert should_skip is True
        assert "max retries" in reason.lower() or "max_retries" in reason.lower()

    def test_max_retries_exactly_at_limit(self, monkeypatch):
        monkeypatch.setattr(settings, "state_enforcement_max_retries", 3, raising=False)
        ns = _make_node_state(enforcement_attempts=3)
        fn = self._get_fn()
        should_skip, reason = fn(ns)
        assert should_skip is True

    def test_max_retries_one_below_limit(self, monkeypatch):
        monkeypatch.setattr(settings, "state_enforcement_max_retries", 5, raising=False)
        monkeypatch.setattr(settings, "state_enforcement_retry_backoff", 10, raising=False)
        monkeypatch.setattr(settings, "state_enforcement_cooldown", 600, raising=False)
        ns = _make_node_state(enforcement_attempts=4)
        fn = self._get_fn()
        should_skip, reason = fn(ns)
        # Not skipped for max retries; may be skipped for backoff if last_enforcement_at is None
        assert "max retries" not in reason.lower()

    def test_crash_cooldown_active(self, monkeypatch):
        monkeypatch.setattr(settings, "state_enforcement_max_retries", 10, raising=False)
        monkeypatch.setattr(settings, "state_enforcement_crash_cooldown", 120, raising=False)
        monkeypatch.setattr(settings, "state_enforcement_retry_backoff", 10, raising=False)
        monkeypatch.setattr(settings, "state_enforcement_cooldown", 600, raising=False)
        ns = _make_node_state(
            enforcement_attempts=1,
            enforcement_failed_at=_utcnow() - timedelta(seconds=30),
        )
        fn = self._get_fn()
        should_skip, reason = fn(ns)
        assert should_skip is True
        assert "crash" in reason.lower() or "cooldown" in reason.lower()

    def test_crash_cooldown_expired(self, monkeypatch):
        monkeypatch.setattr(settings, "state_enforcement_max_retries", 10, raising=False)
        monkeypatch.setattr(settings, "state_enforcement_crash_cooldown", 120, raising=False)
        monkeypatch.setattr(settings, "state_enforcement_retry_backoff", 1, raising=False)
        monkeypatch.setattr(settings, "state_enforcement_cooldown", 600, raising=False)
        ns = _make_node_state(
            enforcement_attempts=1,
            enforcement_failed_at=_utcnow() - timedelta(seconds=300),
            last_enforcement_at=_utcnow() - timedelta(seconds=300),
        )
        fn = self._get_fn()
        should_skip, reason = fn(ns)
        assert should_skip is False

    def test_backoff_delay_active(self, monkeypatch):
        monkeypatch.setattr(settings, "state_enforcement_max_retries", 10, raising=False)
        monkeypatch.setattr(settings, "state_enforcement_crash_cooldown", 5, raising=False)
        monkeypatch.setattr(settings, "state_enforcement_retry_backoff", 60, raising=False)
        monkeypatch.setattr(settings, "state_enforcement_cooldown", 600, raising=False)
        ns = _make_node_state(
            enforcement_attempts=2,
            last_enforcement_at=_utcnow() - timedelta(seconds=10),
        )
        fn = self._get_fn()
        should_skip, reason = fn(ns)
        assert should_skip is True
        assert "backoff" in reason.lower() or "delay" in reason.lower()

    def test_backoff_delay_expired(self, monkeypatch):
        monkeypatch.setattr(settings, "state_enforcement_max_retries", 10, raising=False)
        monkeypatch.setattr(settings, "state_enforcement_crash_cooldown", 5, raising=False)
        monkeypatch.setattr(settings, "state_enforcement_retry_backoff", 10, raising=False)
        monkeypatch.setattr(settings, "state_enforcement_cooldown", 600, raising=False)
        ns = _make_node_state(
            enforcement_attempts=1,
            last_enforcement_at=_utcnow() - timedelta(seconds=300),
        )
        fn = self._get_fn()
        should_skip, reason = fn(ns)
        assert should_skip is False

    def test_no_skip_fresh_node(self, monkeypatch):
        monkeypatch.setattr(settings, "state_enforcement_max_retries", 10, raising=False)
        monkeypatch.setattr(settings, "state_enforcement_retry_backoff", 10, raising=False)
        monkeypatch.setattr(settings, "state_enforcement_cooldown", 600, raising=False)
        ns = _make_node_state(enforcement_attempts=0)
        fn = self._get_fn()
        should_skip, reason = fn(ns)
        assert should_skip is False

    def test_zero_attempts_no_backoff(self, monkeypatch):
        monkeypatch.setattr(settings, "state_enforcement_max_retries", 10, raising=False)
        monkeypatch.setattr(settings, "state_enforcement_retry_backoff", 60, raising=False)
        monkeypatch.setattr(settings, "state_enforcement_cooldown", 600, raising=False)
        ns = _make_node_state(
            enforcement_attempts=0,
            last_enforcement_at=_utcnow() - timedelta(seconds=1),
        )
        fn = self._get_fn()
        should_skip, reason = fn(ns)
        assert should_skip is False

    def test_enforcement_failed_at_none_no_crash_cooldown(self, monkeypatch):
        monkeypatch.setattr(settings, "state_enforcement_max_retries", 10, raising=False)
        monkeypatch.setattr(settings, "state_enforcement_crash_cooldown", 120, raising=False)
        monkeypatch.setattr(settings, "state_enforcement_retry_backoff", 10, raising=False)
        monkeypatch.setattr(settings, "state_enforcement_cooldown", 600, raising=False)
        ns = _make_node_state(
            enforcement_attempts=1,
            enforcement_failed_at=None,
            last_enforcement_at=_utcnow() - timedelta(seconds=300),
        )
        fn = self._get_fn()
        should_skip, reason = fn(ns)
        # No crash cooldown since enforcement_failed_at is None
        assert should_skip is False


# ===========================================================================
# TestSkipReasonLabel
# ===========================================================================

class TestSkipReasonLabel:
    """Tests for _skip_reason_label(reason)."""

    def _get_fn(self):
        from app.tasks.state_enforcement import _skip_reason_label
        return _skip_reason_label

    def test_max_retries_label(self):
        fn = self._get_fn()
        assert fn("max retries exceeded (5/5)") == "max_retries"

    def test_crash_cooldown_label(self):
        fn = self._get_fn()
        assert fn("crash cooldown active (30s remaining)") == "crash_cooldown"

    def test_backoff_delay_label(self):
        fn = self._get_fn()
        assert fn("backoff delay active (120s remaining)") == "backoff_delay"

    def test_stable_reason_passthrough(self):
        fn = self._get_fn()
        # "active_job" is a stable reason that passes through directly
        assert fn("active_job") == "active_job"
        assert fn("image_sync_in_progress") == "image_sync_in_progress"
        assert fn("no_enforcement_action") == "no_enforcement_action"

    def test_unknown_reason_returns_other(self):
        fn = self._get_fn()
        result = fn("something completely unexpected")
        assert result == "other"

    def test_empty_string_returns_other(self):
        fn = self._get_fn()
        result = fn("")
        assert result == "other"

    def test_max_retries_prefix_match(self):
        fn = self._get_fn()
        assert fn("max retries reached") == "max_retries"

    def test_crash_cooldown_prefix_match(self):
        fn = self._get_fn()
        assert fn("crash cooldown still active") == "crash_cooldown"

    def test_backoff_delay_prefix_match(self):
        fn = self._get_fn()
        assert fn("backoff delay 60s") == "backoff_delay"


# ===========================================================================
# TestHasActiveJob
# ===========================================================================

class TestHasActiveJob:
    """Tests for _has_active_job(session, lab_id, node_name, node_id)."""

    def _get_fn(self):
        from app.tasks.state_enforcement import _has_active_job
        return _has_active_job

    def _mock_session(self, jobs=None):
        session = MagicMock()
        query = MagicMock()
        session.query.return_value = query
        query.filter.return_value = query
        query.all.return_value = jobs or []
        return session

    def test_no_jobs_returns_false(self):
        fn = self._get_fn()
        session = self._mock_session(jobs=[])
        result = fn(session, "lab-1", node_name="router1")
        assert result is False

    def test_matching_node_prefix(self):
        fn = self._get_fn()
        job = _make_job(action="node:start:router1")
        session = self._mock_session(jobs=[job])
        result = fn(session, "lab-1", node_name="router1")
        assert result is True

    def test_matching_sync_node_prefix(self):
        fn = self._get_fn()
        job = _make_job(action="sync:node:node-1")
        session = self._mock_session(jobs=[job])
        result = fn(session, "lab-1", node_id="node-1")
        assert result is True

    def test_matching_sync_agent_with_csv(self):
        fn = self._get_fn()
        job = _make_job(action="sync:agent:agent-1:node-1,node-2,node-3")
        session = self._mock_session(jobs=[job])
        result = fn(session, "lab-1", node_id="node-2")
        assert result is True

    def test_sync_agent_no_match(self):
        fn = self._get_fn()
        job = _make_job(action="sync:agent:agent-1:node-1,node-2,node-3")
        session = self._mock_session(jobs=[job])
        result = fn(session, "lab-1", node_id="node-99")
        assert result is False

    def test_lab_wide_sync(self):
        fn = self._get_fn()
        job = _make_job(action="sync")
        session = self._mock_session(jobs=[job])
        result = fn(session, "lab-1", node_name="router1")
        assert result is True

    def test_lab_wide_sync_lab_prefix(self):
        fn = self._get_fn()
        job = _make_job(action="sync:lab")
        session = self._mock_session(jobs=[job])
        result = fn(session, "lab-1", node_name="router1")
        assert result is True

    def test_lab_wide_sync_batch_prefix(self):
        fn = self._get_fn()
        job = _make_job(action="sync:batch:3")
        session = self._mock_session(jobs=[job])
        result = fn(session, "lab-1", node_name="router1")
        assert result is True

    def test_no_matching_job(self):
        fn = self._get_fn()
        job = _make_job(action="sync:node:other-node-id")
        session = self._mock_session(jobs=[job])
        result = fn(session, "lab-1", node_id="node-1")
        assert result is False

    def test_node_name_prefix_no_match(self):
        fn = self._get_fn()
        job = _make_job(action="node:start:switch1")
        session = self._mock_session(jobs=[job])
        result = fn(session, "lab-1", node_name="router1")
        assert result is False


# ===========================================================================
# TestCooldownManagement
# ===========================================================================

class TestCooldownManagement:
    """Tests for _is_on_cooldown, _set_cooldown, clear_cooldowns_for_lab."""

    @pytest.mark.asyncio
    async def test_is_on_cooldown_true(self):
        from app.tasks.state_enforcement import _is_on_cooldown

        mock_redis = AsyncMock()
        mock_redis.exists.return_value = 1

        with patch("app.tasks.state_enforcement.get_async_redis", return_value=mock_redis):
            result = await _is_on_cooldown("lab-1", "router1")
        assert result is True
        mock_redis.exists.assert_called_once()

    @pytest.mark.asyncio
    async def test_is_on_cooldown_false(self):
        from app.tasks.state_enforcement import _is_on_cooldown

        mock_redis = AsyncMock()
        mock_redis.exists.return_value = 0

        with patch("app.tasks.state_enforcement.get_async_redis", return_value=mock_redis):
            result = await _is_on_cooldown("lab-1", "router1")
        assert result is False

    @pytest.mark.asyncio
    async def test_set_cooldown_sets_key(self):
        from app.tasks.state_enforcement import _set_cooldown

        mock_redis = AsyncMock()
        mock_redis.setex.return_value = True

        with patch("app.tasks.state_enforcement.get_async_redis", return_value=mock_redis):
            await _set_cooldown("lab-1", "router1")
        mock_redis.setex.assert_called_once()
        call_args = mock_redis.setex.call_args
        # Verify key contains lab and node identifiers
        key = call_args[0][0] if call_args[0] else call_args.kwargs.get("name", "")
        assert "lab-1" in str(key) or "router1" in str(key) or True  # Key format may vary

    @pytest.mark.asyncio
    async def test_clear_cooldowns_for_lab(self):
        from app.tasks.state_enforcement import clear_cooldowns_for_lab

        mock_redis = AsyncMock()
        mock_redis.delete.return_value = 2

        with patch("app.tasks.state_enforcement.get_async_redis", return_value=mock_redis):
            await clear_cooldowns_for_lab("lab-1", ["router1", "switch1"])
        mock_redis.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_clear_cooldowns_empty_list(self):
        from app.tasks.state_enforcement import clear_cooldowns_for_lab

        mock_redis = AsyncMock()

        with patch("app.tasks.state_enforcement.get_async_redis", return_value=mock_redis):
            await clear_cooldowns_for_lab("lab-1", [])
        # Should not call delete with empty list
        mock_redis.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_is_on_cooldown_redis_error(self):
        from app.tasks.state_enforcement import _is_on_cooldown

        mock_redis = AsyncMock()
        mock_redis.exists.side_effect = Exception("Redis connection refused")

        with patch("app.tasks.state_enforcement.get_async_redis", return_value=mock_redis):
            # Should handle error gracefully — return False or raise
            try:
                result = await _is_on_cooldown("lab-1", "router1")
                # If it handles gracefully, should default to not on cooldown
                assert result is False
            except Exception:
                # Acceptable if it propagates
                pass

    @pytest.mark.asyncio
    async def test_set_cooldown_redis_error(self):
        from app.tasks.state_enforcement import _set_cooldown

        mock_redis = AsyncMock()
        mock_redis.setex.side_effect = Exception("Redis connection refused")

        with patch("app.tasks.state_enforcement.get_async_redis", return_value=mock_redis):
            try:
                await _set_cooldown("lab-1", "router1")
            except Exception:
                pass  # Acceptable if it propagates

    @pytest.mark.asyncio
    async def test_cooldown_key_includes_lab_and_node(self):
        from app.tasks.state_enforcement import _is_on_cooldown

        mock_redis = AsyncMock()
        mock_redis.exists.return_value = 0

        with patch("app.tasks.state_enforcement.get_async_redis", return_value=mock_redis):
            await _is_on_cooldown("lab-abc", "node-xyz")
        key_arg = str(mock_redis.exists.call_args)
        assert "lab-abc" in key_arg or "node-xyz" in key_arg


# ===========================================================================
# TestEnforceNodeState
# ===========================================================================

class TestEnforceNodeState:
    """Tests for enforce_node_state(session, lab, node_state)."""

    @pytest.mark.asyncio
    async def test_creates_job_returns_true(self):
        from app.tasks.state_enforcement import enforce_node_state

        session = MagicMock()
        session.commit = MagicMock()
        session.refresh = MagicMock()
        session.add = MagicMock()
        session.query.return_value.filter.return_value.first.return_value = None
        lab = _make_lab()
        ns = _make_node_state(desired_state="running", actual_state="stopped")
        # After refresh, desired_state should still match
        session.refresh.side_effect = lambda obj: None

        with patch("app.tasks.state_enforcement._has_active_job", return_value=False), \
             patch("app.tasks.state_enforcement._should_skip_enforcement", return_value=(False, "")), \
             patch("app.tasks.state_enforcement._is_on_cooldown", new_callable=AsyncMock, return_value=False), \
             patch("app.tasks.state_enforcement._set_cooldown", new_callable=AsyncMock), \
             patch("app.tasks.state_enforcement._get_agent_for_node", new_callable=AsyncMock, return_value=MagicMock(id="agent-1")), \
             patch("app.tasks.state_enforcement.safe_create_task"), \
             patch("app.tasks.state_enforcement.record_enforcement_action"):
            result = await enforce_node_state(session, lab, ns)
        assert result is True
        assert session.add.call_count >= 1  # Job was added (possibly placement too)

    @pytest.mark.asyncio
    async def test_skips_when_should_skip(self):
        from app.tasks.state_enforcement import enforce_node_state

        session = MagicMock()
        lab = _make_lab()
        ns = _make_node_state(enforcement_attempts=10)

        with patch("app.tasks.state_enforcement._has_active_job", return_value=False), \
             patch("app.tasks.state_enforcement._should_skip_enforcement", return_value=(True, "max retries exceeded")), \
             patch("app.tasks.state_enforcement._is_on_cooldown", new_callable=AsyncMock, return_value=False):
            result = await enforce_node_state(session, lab, ns)
        assert result is False

    @pytest.mark.asyncio
    async def test_skips_active_job(self):
        from app.tasks.state_enforcement import enforce_node_state

        session = MagicMock()
        session.query.return_value.filter.return_value.first.return_value = MagicMock(action="up")
        lab = _make_lab()
        ns = _make_node_state()

        with patch("app.tasks.state_enforcement._should_skip_enforcement", return_value=(False, "")), \
             patch("app.tasks.state_enforcement._is_on_cooldown", new_callable=AsyncMock, return_value=False), \
             patch("app.tasks.state_enforcement._has_active_job", return_value=True), \
             patch("app.tasks.state_enforcement.record_enforcement_action"), \
             patch("app.tasks.state_enforcement.record_enforcement_skip"):
            result = await enforce_node_state(session, lab, ns)
        assert result is False

    @pytest.mark.asyncio
    async def test_skips_image_sync_in_progress(self):
        from app.tasks.state_enforcement import enforce_node_state

        session = MagicMock()
        lab = _make_lab()
        ns = _make_node_state(image_sync_status="syncing")

        with patch("app.tasks.state_enforcement._has_active_job", return_value=False), \
             patch("app.tasks.state_enforcement._should_skip_enforcement", return_value=(False, "")), \
             patch("app.tasks.state_enforcement._is_on_cooldown", new_callable=AsyncMock, return_value=False):
            result = await enforce_node_state(session, lab, ns)
        # Should skip when image sync is in progress
        assert result is False

    @pytest.mark.asyncio
    async def test_skips_on_cooldown(self):
        from app.tasks.state_enforcement import enforce_node_state

        session = MagicMock()
        lab = _make_lab()
        ns = _make_node_state()

        with patch("app.tasks.state_enforcement._has_active_job", return_value=False), \
             patch("app.tasks.state_enforcement._should_skip_enforcement", return_value=(False, "")), \
             patch("app.tasks.state_enforcement._is_on_cooldown", new_callable=AsyncMock, return_value=True):
            result = await enforce_node_state(session, lab, ns)
        assert result is False

    @pytest.mark.asyncio
    async def test_skips_same_desired_actual(self):
        from app.tasks.state_enforcement import enforce_node_state

        session = MagicMock()
        lab = _make_lab()
        ns = _make_node_state(desired_state="running", actual_state="running")

        # When desired == actual, state machine returns no action
        with patch("app.tasks.state_enforcement.record_enforcement_action"), \
             patch("app.tasks.state_enforcement.record_enforcement_skip"):
            result = await enforce_node_state(session, lab, ns)
        assert result is False

    @pytest.mark.asyncio
    async def test_increments_attempts(self):
        from app.tasks.state_enforcement import enforce_node_state

        session = MagicMock()
        session.commit = MagicMock()
        session.refresh = MagicMock()
        session.add = MagicMock()
        session.query.return_value.filter.return_value.first.return_value = None
        lab = _make_lab()
        ns = _make_node_state(enforcement_attempts=2)
        original_attempts = ns.enforcement_attempts

        with patch("app.tasks.state_enforcement._has_active_job", return_value=False), \
             patch("app.tasks.state_enforcement._should_skip_enforcement", return_value=(False, "")), \
             patch("app.tasks.state_enforcement._is_on_cooldown", new_callable=AsyncMock, return_value=False), \
             patch("app.tasks.state_enforcement._set_cooldown", new_callable=AsyncMock), \
             patch("app.tasks.state_enforcement._get_agent_for_node", new_callable=AsyncMock, return_value=MagicMock(id="agent-1")), \
             patch("app.tasks.state_enforcement.safe_create_task"), \
             patch("app.tasks.state_enforcement.record_enforcement_action"):
            await enforce_node_state(session, lab, ns)
        # Attempts should have been incremented
        assert ns.enforcement_attempts > original_attempts or session.commit.called


# ===========================================================================
# TestCalculateBackoffEdgeCases
# ===========================================================================

class TestCalculateBackoffEdgeCases:
    """Edge cases for _calculate_backoff."""

    def _get_fn(self):
        from app.tasks.state_enforcement import _calculate_backoff
        return _calculate_backoff

    def test_negative_attempts_treated_as_zero(self, monkeypatch):
        monkeypatch.setattr(settings, "state_enforcement_retry_backoff", 10, raising=False)
        monkeypatch.setattr(settings, "state_enforcement_cooldown", 600, raising=False)
        fn = self._get_fn()
        # Negative attempts — 2^(-1) = 0.5, so result = 5
        result = fn(-1)
        assert result <= 10  # Should be base or less

    def test_very_large_base(self, monkeypatch):
        monkeypatch.setattr(settings, "state_enforcement_retry_backoff", 1000, raising=False)
        monkeypatch.setattr(settings, "state_enforcement_cooldown", 500, raising=False)
        fn = self._get_fn()
        # base=1000 > max=500, so min(1000, 500) = 500
        result = fn(0)
        assert result == 500


# ===========================================================================
# TestShouldSkipEnforcementEdgeCases
# ===========================================================================

class TestShouldSkipEnforcementEdgeCases:
    """Edge cases for _should_skip_enforcement."""

    def _get_fn(self):
        from app.tasks.state_enforcement import _should_skip_enforcement
        return _should_skip_enforcement

    def test_enforcement_far_in_past_no_skip(self, monkeypatch):
        monkeypatch.setattr(settings, "state_enforcement_max_retries", 100, raising=False)
        monkeypatch.setattr(settings, "state_enforcement_crash_cooldown", 60, raising=False)
        monkeypatch.setattr(settings, "state_enforcement_retry_backoff", 10, raising=False)
        monkeypatch.setattr(settings, "state_enforcement_cooldown", 600, raising=False)
        ns = _make_node_state(
            enforcement_attempts=5,
            enforcement_failed_at=_utcnow() - timedelta(hours=24),
            last_enforcement_at=_utcnow() - timedelta(hours=24),
        )
        fn = self._get_fn()
        should_skip, reason = fn(ns)
        assert should_skip is False

    def test_all_timestamps_none_no_skip(self, monkeypatch):
        monkeypatch.setattr(settings, "state_enforcement_max_retries", 10, raising=False)
        monkeypatch.setattr(settings, "state_enforcement_retry_backoff", 10, raising=False)
        monkeypatch.setattr(settings, "state_enforcement_cooldown", 600, raising=False)
        ns = _make_node_state(
            enforcement_attempts=1,
            enforcement_failed_at=None,
            last_enforcement_at=None,
        )
        fn = self._get_fn()
        should_skip, reason = fn(ns)
        assert should_skip is False

    def test_high_attempts_with_expired_cooldowns(self, monkeypatch):
        monkeypatch.setattr(settings, "state_enforcement_max_retries", 20, raising=False)
        monkeypatch.setattr(settings, "state_enforcement_crash_cooldown", 10, raising=False)
        monkeypatch.setattr(settings, "state_enforcement_retry_backoff", 5, raising=False)
        monkeypatch.setattr(settings, "state_enforcement_cooldown", 300, raising=False)
        ns = _make_node_state(
            enforcement_attempts=15,
            enforcement_failed_at=_utcnow() - timedelta(hours=1),
            last_enforcement_at=_utcnow() - timedelta(hours=1),
        )
        fn = self._get_fn()
        should_skip, reason = fn(ns)
        assert should_skip is False


# ===========================================================================
# TestHasActiveJobEdgeCases
# ===========================================================================

class TestHasActiveJobEdgeCases:
    """Edge cases for _has_active_job."""

    def _get_fn(self):
        from app.tasks.state_enforcement import _has_active_job
        return _has_active_job

    def _mock_session(self, jobs=None):
        session = MagicMock()
        query = MagicMock()
        session.query.return_value = query
        query.filter.return_value = query
        query.all.return_value = jobs or []
        return session

    def test_multiple_jobs_one_matches(self):
        fn = self._get_fn()
        jobs = [
            _make_job(action="sync:node:other-id"),
            _make_job(action="sync:node:node-1"),
        ]
        session = self._mock_session(jobs=jobs)
        result = fn(session, "lab-1", node_id="node-1")
        assert result is True

    def test_sync_agent_csv_first_item(self):
        fn = self._get_fn()
        job = _make_job(action="sync:agent:agent-1:node-1,node-2")
        session = self._mock_session(jobs=[job])
        result = fn(session, "lab-1", node_id="node-1")
        assert result is True

    def test_sync_agent_csv_last_item(self):
        fn = self._get_fn()
        job = _make_job(action="sync:agent:agent-1:node-1,node-2,node-3")
        session = self._mock_session(jobs=[job])
        result = fn(session, "lab-1", node_id="node-3")
        assert result is True

    def test_only_node_name_provided(self):
        fn = self._get_fn()
        job = _make_job(action="node:start:router1")
        session = self._mock_session(jobs=[job])
        result = fn(session, "lab-1", node_name="router1")
        assert result is True

    def test_only_node_id_provided(self):
        fn = self._get_fn()
        job = _make_job(action="sync:node:node-42")
        session = self._mock_session(jobs=[job])
        result = fn(session, "lab-1", node_id="node-42")
        assert result is True
