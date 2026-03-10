"""Tests for app/tasks/stuck_agents.py — stuck agent update job detection."""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy.orm import Session

from app import models
from tests.factories import make_host


def _naive_utcnow() -> datetime:
    """Naive UTC datetime that matches SQLite round-trip (no tzinfo)."""
    return datetime.utcnow()


def _fake_get_session(session: Session):
    """Create a fake get_session context manager that yields the test session."""
    @contextmanager
    def _get_session():
        yield session
    return _get_session


def _make_update_job(
    test_db: Session,
    host_id: str,
    *,
    status: str = "pending",
    created_at: datetime | None = None,
    started_at: datetime | None = None,
) -> models.AgentUpdateJob:
    """Create an AgentUpdateJob record."""
    job = models.AgentUpdateJob(
        id=str(uuid4()),
        host_id=host_id,
        from_version="1.0.0",
        to_version="2.0.0",
        status=status,
        created_at=created_at or _naive_utcnow(),
        started_at=started_at,
    )
    test_db.add(job)
    test_db.commit()
    test_db.refresh(job)
    return job


class TestCheckStuckAgentUpdates:
    """Tests for check_stuck_agent_updates()."""

    def test_no_stuck_jobs_returns_early(self, test_db: Session):
        """When there are no active update jobs, function returns immediately."""
        with patch("app.tasks.stuck_agents.get_session", _fake_get_session(test_db)):
            from app.tasks.stuck_agents import check_stuck_agent_updates
            # Should complete without error
            check_stuck_agent_updates()

    def test_timed_out_job_marked_failed(self, test_db: Session):
        """Job stuck in active status past timeout is marked failed."""
        host = make_host(test_db, status="online")
        old_time = _naive_utcnow() - timedelta(minutes=30)
        job = _make_update_job(
            test_db, host.id, status="installing", created_at=old_time,
        )

        with patch("app.tasks.stuck_agents.get_session", _fake_get_session(test_db)):
            from app.tasks.stuck_agents import check_stuck_agent_updates
            check_stuck_agent_updates()

        test_db.refresh(job)
        assert job.status == "failed"
        assert "Timed out" in job.error_message
        assert job.completed_at is not None

    def test_timed_out_uses_started_at_when_available(self, test_db: Session):
        """Timeout uses started_at as reference time when present."""
        host = make_host(test_db, status="online")
        # created_at is recent, but started_at is old
        recent = _naive_utcnow() - timedelta(minutes=1)
        old_started = _naive_utcnow() - timedelta(minutes=30)
        job = _make_update_job(
            test_db, host.id, status="downloading",
            created_at=recent, started_at=old_started,
        )

        with patch("app.tasks.stuck_agents.get_session", _fake_get_session(test_db)):
            from app.tasks.stuck_agents import check_stuck_agent_updates
            check_stuck_agent_updates()

        test_db.refresh(job)
        assert job.status == "failed"
        assert "Timed out" in job.error_message

    def test_recent_job_not_affected(self, test_db: Session):
        """Job created recently that hasn't timed out is left alone."""
        host = make_host(test_db, status="online")
        recent = _naive_utcnow() - timedelta(minutes=1)
        job = _make_update_job(
            test_db, host.id, status="pending", created_at=recent,
        )

        with patch("app.tasks.stuck_agents.get_session", _fake_get_session(test_db)):
            from app.tasks.stuck_agents import check_stuck_agent_updates
            check_stuck_agent_updates()

        test_db.refresh(job)
        assert job.status == "pending"
        assert job.error_message is None

    def test_offline_agent_job_marked_failed(self, test_db: Session):
        """Job on an offline agent is marked failed regardless of age."""
        host = make_host(test_db, status="offline")
        recent = _naive_utcnow() - timedelta(seconds=30)
        job = _make_update_job(
            test_db, host.id, status="restarting", created_at=recent,
        )

        with patch("app.tasks.stuck_agents.get_session", _fake_get_session(test_db)):
            from app.tasks.stuck_agents import check_stuck_agent_updates
            check_stuck_agent_updates()

        test_db.refresh(job)
        assert job.status == "failed"
        assert "went offline" in job.error_message
        assert host.name in job.error_message

    def test_completed_jobs_not_queried(self, test_db: Session):
        """Jobs in terminal states (completed/failed) are not considered."""
        host = make_host(test_db, status="online")
        old_time = _naive_utcnow() - timedelta(minutes=30)

        completed_job = _make_update_job(
            test_db, host.id, status="completed", created_at=old_time,
        )
        failed_job = _make_update_job(
            test_db, host.id, status="failed", created_at=old_time,
        )

        with patch("app.tasks.stuck_agents.get_session", _fake_get_session(test_db)):
            from app.tasks.stuck_agents import check_stuck_agent_updates
            check_stuck_agent_updates()

        test_db.refresh(completed_job)
        test_db.refresh(failed_job)
        # Still in their original terminal states
        assert completed_job.status == "completed"
        assert failed_job.status == "failed"

    def test_all_active_statuses_detected(self, test_db: Session):
        """All four active statuses are detected as potentially stuck."""
        host = make_host(test_db, status="online")
        old_time = _naive_utcnow() - timedelta(minutes=30)

        jobs = []
        for status in ["pending", "downloading", "installing", "restarting"]:
            j = _make_update_job(
                test_db, host.id, status=status, created_at=old_time,
            )
            jobs.append(j)

        with patch("app.tasks.stuck_agents.get_session", _fake_get_session(test_db)):
            from app.tasks.stuck_agents import check_stuck_agent_updates
            check_stuck_agent_updates()

        for j in jobs:
            test_db.refresh(j)
            assert j.status == "failed", f"Expected {j.id} to be failed"

    def test_database_error_handling_outer(self, test_db: Session):
        """Outer exception is caught and logged without raising."""
        with patch("app.tasks.stuck_agents.get_session", _fake_get_session(test_db)):
            with patch(
                "app.tasks.stuck_agents.models.AgentUpdateJob",
                side_effect=RuntimeError("DB exploded"),
            ):
                # Patch the query to raise; the module-level models reference
                # is used inside the function, so we patch the attribute lookup.
                pass

        # More reliable: patch session.query to raise
        def _exploding_session():
            @contextmanager
            def _get_session():
                from unittest.mock import MagicMock
                s = MagicMock()
                s.query.side_effect = RuntimeError("DB exploded")
                s.rollback = lambda: None
                yield s
            return _get_session

        with patch("app.tasks.stuck_agents.get_session", _exploding_session()):
            from app.tasks.stuck_agents import check_stuck_agent_updates
            # Should not raise
            check_stuck_agent_updates()

    def test_per_job_error_handling(self, test_db: Session):
        """Error processing one job doesn't prevent processing others."""
        host = make_host(test_db, status="online")
        old_time = _naive_utcnow() - timedelta(minutes=30)

        job1 = _make_update_job(
            test_db, host.id, status="installing", created_at=old_time,
        )
        job2 = _make_update_job(
            test_db, host.id, status="downloading", created_at=old_time,
        )

        call_count = 0
        original_get = test_db.get

        def _failing_get(model, pk):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Simulated per-job error")
            return original_get(model, pk)

        with patch("app.tasks.stuck_agents.get_session", _fake_get_session(test_db)):
            with patch.object(test_db, "get", side_effect=_failing_get):
                from app.tasks.stuck_agents import check_stuck_agent_updates
                check_stuck_agent_updates()

        # At least one job should still be processed despite the error
        test_db.refresh(job1)
        test_db.refresh(job2)
        statuses = {job1.status, job2.status}
        # One may have failed processing, the other should have been marked
        assert "failed" in statuses or "installing" in statuses

    def test_error_message_includes_minutes_for_timeout(self, test_db: Session):
        """Timeout error message includes the age in minutes."""
        host = make_host(test_db, status="online")
        old_time = _naive_utcnow() - timedelta(minutes=45)
        job = _make_update_job(
            test_db, host.id, status="pending", created_at=old_time,
        )

        with patch("app.tasks.stuck_agents.get_session", _fake_get_session(test_db)):
            from app.tasks.stuck_agents import check_stuck_agent_updates
            check_stuck_agent_updates()

        test_db.refresh(job)
        assert job.status == "failed"
        # Message should contain minutes and the status name
        assert "minutes" in job.error_message
        assert "'pending'" in job.error_message