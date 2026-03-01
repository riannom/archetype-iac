"""Tests for job orchestration functions in app.tasks.jobs."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from app import models


class TestJobHelpers:
    """Tests for utility functions in tasks/jobs.py."""

    def test_get_container_name_docker(self):
        """Docker container name uses archetype- prefix."""
        from app.tasks.jobs import _get_container_name

        name = _get_container_name("lab-123", "r1", provider="docker")
        assert "lab-123" in name
        assert "r1" in name

    def test_get_container_name_libvirt(self):
        """Libvirt domain name uses arch- prefix."""
        from app.tasks.jobs import _get_container_name

        name = _get_container_name("lab-123", "r1", provider="libvirt")
        assert "lab-123" in name
        assert "r1" in name

    def test_get_container_name_kvm(self):
        """KVM provider maps to libvirt naming."""
        from app.tasks.jobs import _get_container_name

        name = _get_container_name("lab-123", "r1", provider="kvm")
        assert "lab-123" in name
        assert "r1" in name

    def test_normalized_job_action_simple(self):
        """Simple action strings pass through unchanged."""
        from app.tasks.jobs import _normalized_job_action

        assert _normalized_job_action("up") == "up"
        assert _normalized_job_action("down") == "down"

    def test_normalized_job_action_sync_prefix(self):
        """sync: prefix is normalized to 'sync'."""
        from app.tasks.jobs import _normalized_job_action

        assert _normalized_job_action("sync:node:123") == "sync"

    def test_normalized_job_action_node_prefix(self):
        """node: prefix is normalized to 'node'."""
        from app.tasks.jobs import _normalized_job_action

        assert _normalized_job_action("node:start:r1") == "node"

    def test_normalized_job_action_empty(self):
        """Empty action returns 'unknown'."""
        from app.tasks.jobs import _normalized_job_action

        assert _normalized_job_action("") == "unknown"

    def test_as_utc_aware_none(self):
        """None input returns None."""
        from app.tasks.jobs import _as_utc_aware

        assert _as_utc_aware(None) is None

    def test_as_utc_aware_naive(self):
        """Naive datetime gets UTC timezone attached."""
        from app.tasks.jobs import _as_utc_aware

        dt = datetime(2024, 1, 1, 12, 0, 0)
        result = _as_utc_aware(dt)
        assert result is not None
        assert result.tzinfo is not None

    def test_as_utc_aware_already_utc(self):
        """UTC-aware datetime passes through unchanged."""
        from app.tasks.jobs import _as_utc_aware

        dt = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = _as_utc_aware(dt)
        assert result == dt

    def test_job_duration_seconds_complete(self):
        """Duration calculated from started_at to completed_at."""
        from app.tasks.jobs import _job_duration_seconds

        job = MagicMock()
        job.started_at = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        job.completed_at = datetime(2024, 1, 1, 12, 1, 30, tzinfo=timezone.utc)
        assert _job_duration_seconds(job) == 90.0

    def test_job_duration_seconds_missing(self):
        """Duration returns None when timestamps are missing."""
        from app.tasks.jobs import _job_duration_seconds

        job = MagicMock()
        job.started_at = None
        job.completed_at = None
        assert _job_duration_seconds(job) is None

    def test_job_duration_seconds_no_completed(self):
        """Duration returns None when completed_at is missing."""
        from app.tasks.jobs import _job_duration_seconds

        job = MagicMock()
        job.started_at = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        job.completed_at = None
        assert _job_duration_seconds(job) is None


class TestAcquireDeployLock:
    """Tests for deploy lock acquisition via Redis."""

    def test_acquire_lock_success(self, monkeypatch):
        """Successfully acquire lock for all nodes."""
        from app.tasks.jobs import acquire_deploy_lock

        mock_redis = MagicMock()
        mock_redis.set.return_value = True
        monkeypatch.setattr("app.tasks.jobs.get_redis", lambda: mock_redis)

        success, locked = acquire_deploy_lock("lab-1", ["r1", "r2"], "agent-1")
        assert success is True
        assert len(locked) == 2

    def test_acquire_lock_conflict(self, monkeypatch):
        """Lock failure when another agent holds a lock."""
        from app.tasks.jobs import acquire_deploy_lock

        mock_redis = MagicMock()
        # First call succeeds, second fails
        mock_redis.set.side_effect = [True, False]
        mock_redis.get.return_value = b"agent:other:time:2024-01-01"
        monkeypatch.setattr("app.tasks.jobs.get_redis", lambda: mock_redis)

        success, failed = acquire_deploy_lock("lab-1", ["r1", "r2"], "agent-1")
        assert success is False
        assert "r2" in failed

    def test_acquire_lock_redis_error(self, monkeypatch):
        """Redis error results in graceful fallback (proceed without lock)."""
        import redis as redis_lib
        from app.tasks.jobs import acquire_deploy_lock

        mock_redis = MagicMock()
        mock_redis.set.side_effect = redis_lib.RedisError("Connection refused")
        monkeypatch.setattr("app.tasks.jobs.get_redis", lambda: mock_redis)

        success, nodes = acquire_deploy_lock("lab-1", ["r1"], "agent-1")
        assert success is True
        assert nodes == ["r1"]


class TestHasConflictingJob:
    """Tests for conflicting job detection."""

    def test_no_conflict_when_no_jobs(self, test_db: Session, sample_lab: models.Lab):
        """No conflict when no active jobs exist."""
        from app.jobs import has_conflicting_job

        conflict, action = has_conflicting_job(sample_lab.id, "up", session=test_db)
        assert conflict is False
        assert action is None

    def test_conflict_with_running_up(
        self, test_db: Session, sample_lab: models.Lab, running_job: models.Job
    ):
        """Active 'up' job conflicts with new 'up' request."""
        from app.jobs import has_conflicting_job

        conflict, action = has_conflicting_job(sample_lab.id, "up", session=test_db)
        assert conflict is True
        assert action is not None

    def test_completed_job_no_conflict(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """Completed jobs do not cause conflicts."""
        from app.jobs import has_conflicting_job

        completed = models.Job(
            id="completed-job",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="completed",
        )
        test_db.add(completed)
        test_db.commit()

        conflict, action = has_conflicting_job(sample_lab.id, "up", session=test_db)
        assert conflict is False


class TestRunJobPreflightChecks:
    """Tests for preflight validation before deploy."""

    @pytest.mark.asyncio
    async def test_preflight_agent_connectivity_failure(self):
        """Preflight fails when agent is unreachable."""
        from app.tasks.jobs import _run_job_preflight_checks

        mock_session = MagicMock()
        mock_lab = MagicMock()
        mock_lab.id = "lab-1"
        mock_agent = MagicMock()
        mock_agent.id = "agent-1"
        mock_agent.name = "Agent 1"

        with patch(
            "app.tasks.jobs.agent_client.get_lab_status_from_agent",
            new_callable=AsyncMock,
            side_effect=ConnectionError("Connection refused"),
        ):
            ok, msg = await _run_job_preflight_checks(
                mock_session, mock_lab, mock_agent, "up"
            )
        assert ok is False
        assert "preflight" in msg.lower()

    @pytest.mark.asyncio
    async def test_preflight_success(self):
        """Preflight passes when agent is reachable."""
        from app.tasks.jobs import _run_job_preflight_checks

        mock_session = MagicMock()
        mock_lab = MagicMock()
        mock_lab.id = "lab-1"
        mock_agent = MagicMock()
        mock_agent.id = "agent-1"
        mock_agent.name = "Agent 1"

        with patch(
            "app.tasks.jobs.agent_client.get_lab_status_from_agent",
            new_callable=AsyncMock,
            return_value={"nodes": []},
        ), patch(
            "app.tasks.jobs.settings"
        ) as mock_settings:
            mock_settings.image_sync_enabled = False
            mock_settings.image_sync_pre_deploy_check = False

            ok, msg = await _run_job_preflight_checks(
                mock_session, mock_lab, mock_agent, "up"
            )
        assert ok is True
        assert msg is None

    @pytest.mark.asyncio
    async def test_preflight_skipped_for_non_deploy(self):
        """Preflight is skipped for actions other than up/down."""
        from app.tasks.jobs import _run_job_preflight_checks

        mock_session = MagicMock()
        mock_lab = MagicMock()
        mock_agent = MagicMock()

        ok, msg = await _run_job_preflight_checks(
            mock_session, mock_lab, mock_agent, "sync:node:r1"
        )
        assert ok is True
        assert msg is None


class TestJobCallbackCompletion:
    """Tests for job status updates on completion."""

    def test_job_queue_wait_seconds(self):
        """Queue wait calculated from created_at to started_at."""
        from app.tasks.jobs import _job_queue_wait_seconds

        job = MagicMock()
        job.created_at = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        job.started_at = datetime(2024, 1, 1, 12, 0, 10, tzinfo=timezone.utc)
        assert _job_queue_wait_seconds(job) == 10.0

    def test_job_queue_wait_seconds_none(self):
        """Queue wait returns None when timestamps are missing."""
        from app.tasks.jobs import _job_queue_wait_seconds

        job = MagicMock()
        job.created_at = None
        job.started_at = None
        assert _job_queue_wait_seconds(job) is None


class TestReleaseDeploy:
    """Tests for deploy lock release."""

    def test_release_deploy_lock(self, monkeypatch):
        """Release deploy lock deletes Redis keys."""
        from app.tasks.jobs import release_deploy_lock

        mock_redis = MagicMock()
        monkeypatch.setattr("app.tasks.jobs.get_redis", lambda: mock_redis)

        release_deploy_lock("lab-1", ["r1", "r2"])
        assert mock_redis.delete.call_count == 2

    def test_release_deploy_lock_redis_error(self, monkeypatch):
        """Release lock handles Redis errors gracefully."""
        import redis as redis_lib
        from app.tasks.jobs import release_deploy_lock

        mock_redis = MagicMock()
        mock_redis.delete.side_effect = redis_lib.RedisError("Connection lost")
        monkeypatch.setattr("app.tasks.jobs.get_redis", lambda: mock_redis)

        # Should not raise
        release_deploy_lock("lab-1", ["r1"])
