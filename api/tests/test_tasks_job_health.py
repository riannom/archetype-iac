"""Tests for app/tasks/job_health.py - Job health monitoring background task."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app import models


class TestCheckStuckJobs:
    """Tests for the check_stuck_jobs function."""

    @pytest.mark.asyncio
    async def test_no_active_jobs(self, test_db: Session):
        """Should return early when no active jobs exist."""
        from app.tasks.job_health import check_stuck_jobs

        with patch("app.tasks.job_health.SessionLocal", return_value=test_db):
            # No jobs in database - should complete without error
            await check_stuck_jobs()

    @pytest.mark.asyncio
    async def test_ignores_completed_jobs(self, test_db: Session, sample_lab: models.Lab, test_user: models.User):
        """Should not process completed jobs."""
        from app.tasks.job_health import check_stuck_jobs

        # Create a completed job
        job = models.Job(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="completed",
            completed_at=datetime.now(timezone.utc),
        )
        test_db.add(job)
        test_db.commit()

        with patch("app.tasks.job_health.SessionLocal", return_value=test_db):
            await check_stuck_jobs()
            # Job should still be completed
            test_db.refresh(job)
            assert job.status == "completed"

    @pytest.mark.asyncio
    async def test_skips_job_within_timeout(self, test_db: Session, running_job: models.Job):
        """Should skip jobs that are still within their timeout window."""
        from app.tasks.job_health import check_stuck_jobs

        with patch("app.tasks.job_health.SessionLocal", return_value=test_db):
            await check_stuck_jobs()
            # Job should still be running
            test_db.refresh(running_job)
            assert running_job.status == "running"


class TestCheckSingleJobParentChild:
    """Tests for parent-child job handling in _check_single_job."""

    @pytest.mark.asyncio
    async def test_skips_child_job_when_parent_active(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """Should skip stuck child job if parent is still running."""
        from app.tasks.job_health import _check_single_job

        # Create parent job that's still running
        parent_job = models.Job(
            id="parent-job-1",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="sync:lab",
            status="running",
            started_at=datetime.now(timezone.utc),
        )
        test_db.add(parent_job)
        test_db.commit()

        # Create stuck child job
        child_job = models.Job(
            id="child-job-1",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="sync:agent:agent1:node1",
            status="running",
            parent_job_id=parent_job.id,
            started_at=datetime.now(timezone.utc) - timedelta(minutes=30),  # Stuck
        )
        test_db.add(child_job)
        test_db.commit()

        now = datetime.now(timezone.utc)

        with patch("app.tasks.job_health.is_job_stuck", return_value=True):
            await _check_single_job(test_db, child_job, now)

            # Child should still be running (skipped because parent is active)
            test_db.refresh(child_job)
            assert child_job.status == "running"

    @pytest.mark.asyncio
    async def test_fails_orphaned_child_job(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """Should fail stuck child job if parent is completed/failed."""
        from app.tasks.job_health import _check_single_job

        # Create completed parent job
        parent_job = models.Job(
            id="parent-job-1",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="sync:lab",
            status="completed",
            completed_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        test_db.add(parent_job)
        test_db.commit()

        # Create stuck child job (orphaned)
        child_job = models.Job(
            id="child-job-1",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="sync:agent:agent1:node1",
            status="running",
            parent_job_id=parent_job.id,
            started_at=datetime.now(timezone.utc) - timedelta(minutes=30),
        )
        test_db.add(child_job)
        test_db.commit()

        now = datetime.now(timezone.utc)

        with patch("app.tasks.job_health.is_job_stuck", return_value=True):
            await _check_single_job(test_db, child_job, now)

            # Child should be failed (orphaned)
            test_db.refresh(child_job)
            assert child_job.status == "failed"
            assert "orphaned" in child_job.log_path.lower()

    @pytest.mark.asyncio
    async def test_fails_child_job_with_missing_parent(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """Should fail stuck child job if parent is missing."""
        from app.tasks.job_health import _check_single_job

        # Create stuck child job with non-existent parent
        child_job = models.Job(
            id="child-job-1",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="sync:agent:agent1:node1",
            status="running",
            parent_job_id="non-existent-parent-id",
            started_at=datetime.now(timezone.utc) - timedelta(minutes=30),
        )
        test_db.add(child_job)
        test_db.commit()

        now = datetime.now(timezone.utc)

        with patch("app.tasks.job_health.is_job_stuck", return_value=True):
            await _check_single_job(test_db, child_job, now)

            # Child should be failed (orphaned due to missing parent)
            test_db.refresh(child_job)
            assert child_job.status == "failed"
            assert "missing" in child_job.log_path.lower() or "orphaned" in child_job.log_path.lower()

    @pytest.mark.asyncio
    async def test_skips_child_job_when_parent_queued(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """Should skip stuck child job if parent is still queued."""
        from app.tasks.job_health import _check_single_job

        # Create parent job that's queued (not yet running)
        parent_job = models.Job(
            id="parent-job-queued",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="sync:lab",
            status="queued",
        )
        test_db.add(parent_job)
        test_db.commit()

        # Create stuck child job
        child_job = models.Job(
            id="child-job-queued-parent",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="sync:agent:agent1:node1",
            status="running",
            parent_job_id=parent_job.id,
            started_at=datetime.now(timezone.utc) - timedelta(minutes=30),
        )
        test_db.add(child_job)
        test_db.commit()

        now = datetime.now(timezone.utc)

        with patch("app.tasks.job_health.is_job_stuck", return_value=True):
            await _check_single_job(test_db, child_job, now)

            # Child should still be running (skipped because parent is queued)
            test_db.refresh(child_job)
            assert child_job.status == "running"

    @pytest.mark.asyncio
    async def test_fails_orphaned_child_when_parent_failed(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """Should fail stuck child job if parent has failed."""
        from app.tasks.job_health import _check_single_job

        # Create failed parent job
        parent_job = models.Job(
            id="parent-job-failed",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="sync:lab",
            status="failed",
            completed_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        test_db.add(parent_job)
        test_db.commit()

        # Create stuck child job (orphaned)
        child_job = models.Job(
            id="child-job-failed-parent",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="sync:agent:agent1:node1",
            status="running",
            parent_job_id=parent_job.id,
            started_at=datetime.now(timezone.utc) - timedelta(minutes=30),
        )
        test_db.add(child_job)
        test_db.commit()

        now = datetime.now(timezone.utc)

        with patch("app.tasks.job_health.is_job_stuck", return_value=True):
            await _check_single_job(test_db, child_job, now)

            # Child should be failed (orphaned)
            test_db.refresh(child_job)
            assert child_job.status == "failed"
            assert "orphaned" in child_job.log_path.lower()

    @pytest.mark.asyncio
    async def test_fails_orphaned_child_when_parent_cancelled(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """Should fail stuck child job if parent was cancelled."""
        from app.tasks.job_health import _check_single_job

        # Create cancelled parent job
        parent_job = models.Job(
            id="parent-job-cancelled",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="sync:lab",
            status="cancelled",
            completed_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        test_db.add(parent_job)
        test_db.commit()

        # Create stuck child job (orphaned)
        child_job = models.Job(
            id="child-job-cancelled-parent",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="sync:agent:agent1:node1",
            status="running",
            parent_job_id=parent_job.id,
            started_at=datetime.now(timezone.utc) - timedelta(minutes=30),
        )
        test_db.add(child_job)
        test_db.commit()

        now = datetime.now(timezone.utc)

        with patch("app.tasks.job_health.is_job_stuck", return_value=True):
            await _check_single_job(test_db, child_job, now)

            # Child should be failed (orphaned)
            test_db.refresh(child_job)
            assert child_job.status == "failed"
            assert "orphaned" in child_job.log_path.lower()

    @pytest.mark.asyncio
    async def test_processes_job_without_parent_normally(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User, sample_host: models.Host
    ):
        """Jobs without parent_job_id should follow normal retry logic."""
        from app.tasks.job_health import _check_single_job

        # Create stuck job without parent
        job = models.Job(
            id="standalone-job",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="running",
            agent_id=sample_host.id,
            started_at=datetime.now(timezone.utc) - timedelta(minutes=30),
            parent_job_id=None,  # No parent
        )
        test_db.add(job)
        test_db.commit()

        now = datetime.now(timezone.utc)

        with patch("app.tasks.job_health.is_job_stuck", return_value=True):
            with patch("app.tasks.job_health._retry_job", new_callable=AsyncMock) as mock_retry:
                await _check_single_job(test_db, job, now)

                # Should have called _retry_job since it's a normal stuck job
                mock_retry.assert_called_once()

    @pytest.mark.asyncio
    async def test_retries_parent_job_normally(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User, sample_host: models.Host
    ):
        """Parent jobs (jobs with children but no parent) are retried normally."""
        from app.tasks.job_health import _check_single_job

        # Create parent job (has children, but no parent_job_id)
        parent_job = models.Job(
            id="parent-job-to-retry",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="sync:lab",
            status="running",
            agent_id=sample_host.id,
            started_at=datetime.now(timezone.utc) - timedelta(minutes=30),
            parent_job_id=None,
        )
        test_db.add(parent_job)
        test_db.commit()

        # Create child job
        child_job = models.Job(
            id="child-of-parent-to-retry",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="sync:agent:agent1:node1",
            status="running",
            parent_job_id=parent_job.id,
        )
        test_db.add(child_job)
        test_db.commit()

        now = datetime.now(timezone.utc)

        with patch("app.tasks.job_health.is_job_stuck", return_value=True):
            with patch("app.tasks.job_health._retry_job", new_callable=AsyncMock) as mock_retry:
                await _check_single_job(test_db, parent_job, now)

                # Parent should be retried normally
                mock_retry.assert_called_once()


class TestCheckOrphanedQueuedJobs:
    """Tests for the check_orphaned_queued_jobs function."""

    @pytest.mark.asyncio
    async def test_no_orphaned_jobs(self, test_db: Session):
        """Should complete without error when no orphaned jobs exist."""
        from app.tasks.job_health import check_orphaned_queued_jobs

        with patch("app.tasks.job_health.SessionLocal", return_value=test_db):
            await check_orphaned_queued_jobs()

    @pytest.mark.asyncio
    async def test_ignores_recent_queued_jobs(self, test_db: Session, sample_job: models.Job):
        """Should not process recently queued jobs."""
        from app.tasks.job_health import check_orphaned_queued_jobs

        with patch("app.tasks.job_health.SessionLocal", return_value=test_db):
            await check_orphaned_queued_jobs()
            # Job should still be queued
            test_db.refresh(sample_job)
            assert sample_job.status == "queued"


class TestCheckJobsOnOfflineAgents:
    """Tests for the check_jobs_on_offline_agents function."""

    @pytest.mark.asyncio
    async def test_no_offline_agents(self, test_db: Session, sample_host: models.Host):
        """Should return early when no offline agents exist."""
        from app.tasks.job_health import check_jobs_on_offline_agents

        # sample_host is online by default
        with patch("app.tasks.job_health.SessionLocal", return_value=test_db):
            await check_jobs_on_offline_agents()

    @pytest.mark.asyncio
    async def test_no_jobs_on_offline_agents(self, test_db: Session, offline_host: models.Host):
        """Should handle offline agents with no jobs."""
        from app.tasks.job_health import check_jobs_on_offline_agents

        with patch("app.tasks.job_health.SessionLocal", return_value=test_db):
            await check_jobs_on_offline_agents()


class TestCheckStuckImageSyncJobs:
    """Tests for the check_stuck_image_sync_jobs function."""

    @pytest.mark.asyncio
    async def test_no_active_sync_jobs(self, test_db: Session):
        """Should return early when no active sync jobs exist."""
        from app.tasks.job_health import check_stuck_image_sync_jobs

        with patch("app.tasks.job_health.SessionLocal", return_value=test_db):
            await check_stuck_image_sync_jobs()

    @pytest.mark.asyncio
    async def test_ignores_completed_sync_jobs(self, test_db: Session, sample_host: models.Host):
        """Should not process completed sync jobs."""
        from app.tasks.job_health import check_stuck_image_sync_jobs

        # Create a completed sync job
        job = models.ImageSyncJob(
            id=str(uuid4()),
            image_id="docker:test:1.0",
            host_id=sample_host.id,
            status="completed",
            completed_at=datetime.now(timezone.utc),
        )
        test_db.add(job)
        test_db.commit()

        with patch("app.tasks.job_health.SessionLocal", return_value=test_db):
            await check_stuck_image_sync_jobs()
            test_db.refresh(job)
            assert job.status == "completed"

    @pytest.mark.asyncio
    async def test_marks_stuck_pending_job_as_failed(self, test_db: Session, sample_host: models.Host, monkeypatch):
        """Should mark pending sync job as failed if stuck too long."""
        from contextlib import contextmanager
        from app.tasks.job_health import check_stuck_image_sync_jobs
        from app.config import settings

        # Set a short timeout for testing
        monkeypatch.setattr(settings, "image_sync_job_pending_timeout", 60)

        # Create a pending sync job that's been waiting too long
        job = models.ImageSyncJob(
            id=str(uuid4()),
            image_id="docker:test:1.0",
            host_id=sample_host.id,
            status="pending",
            created_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        test_db.add(job)
        test_db.commit()

        @contextmanager
        def fake_get_session():
            yield test_db

        with patch("app.tasks.job_health.get_session", fake_get_session):
            await check_stuck_image_sync_jobs()
            test_db.refresh(job)
            assert job.status == "failed"
            assert job.error_message is not None

    @pytest.mark.asyncio
    async def test_ignores_recent_pending_job(self, test_db: Session, sample_image_sync_job: models.ImageSyncJob):
        """Should not fail recently created pending jobs."""
        from app.tasks.job_health import check_stuck_image_sync_jobs

        with patch("app.tasks.job_health.SessionLocal", return_value=test_db):
            await check_stuck_image_sync_jobs()
            test_db.refresh(sample_image_sync_job)
            assert sample_image_sync_job.status == "pending"

    @pytest.mark.asyncio
    async def test_marks_stuck_transferring_job_as_failed(self, test_db: Session, sample_host: models.Host, monkeypatch):
        """Should mark transferring sync job as failed if timed out."""
        from contextlib import contextmanager
        from app.tasks.job_health import check_stuck_image_sync_jobs
        from app.config import settings

        monkeypatch.setattr(settings, "image_sync_timeout", 300)

        # Create a transferring job that's timed out
        job = models.ImageSyncJob(
            id=str(uuid4()),
            image_id="docker:test:1.0",
            host_id=sample_host.id,
            status="transferring",
            started_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
        test_db.add(job)
        test_db.commit()

        @contextmanager
        def fake_get_session():
            yield test_db

        with patch("app.tasks.job_health.get_session", fake_get_session):
            await check_stuck_image_sync_jobs()
            test_db.refresh(job)
            assert job.status == "failed"

    @pytest.mark.asyncio
    async def test_marks_job_failed_when_host_offline(self, test_db: Session, offline_host: models.Host):
        """Should mark sync job as failed when target host is offline."""
        from contextlib import contextmanager
        from app.tasks.job_health import check_stuck_image_sync_jobs

        # Create a transferring job on offline host
        job = models.ImageSyncJob(
            id=str(uuid4()),
            image_id="docker:test:1.0",
            host_id=offline_host.id,
            status="transferring",
            started_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        test_db.add(job)
        test_db.commit()

        @contextmanager
        def fake_get_session():
            yield test_db

        with patch("app.tasks.job_health.get_session", fake_get_session):
            await check_stuck_image_sync_jobs()
            test_db.refresh(job)
            assert job.status == "failed"
            assert "offline" in job.error_message.lower()


class TestJobHealthMonitor:
    """Tests for the job_health_monitor background task."""

    @pytest.mark.asyncio
    async def test_runs_all_health_checks(self):
        """Should run all health check functions each iteration."""
        from app.tasks.job_health import job_health_monitor

        with patch("app.tasks.job_health.check_stuck_jobs", new_callable=AsyncMock) as mock_stuck:
            with patch("app.tasks.job_health.check_orphaned_queued_jobs", new_callable=AsyncMock) as mock_orphaned:
                with patch("app.tasks.job_health.check_jobs_on_offline_agents", new_callable=AsyncMock) as mock_offline:
                    with patch("app.tasks.job_health.check_stuck_image_sync_jobs", new_callable=AsyncMock) as mock_sync:
                        with patch("app.tasks.job_health.check_stuck_locks", new_callable=AsyncMock) as mock_locks:
                            with patch("app.tasks.job_health.check_stuck_stopping_nodes", new_callable=AsyncMock) as mock_stopping:
                                with patch("app.tasks.job_health.check_stuck_starting_nodes", new_callable=AsyncMock) as mock_starting:
                                    with patch("app.tasks.job_health.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                                        call_count = 0
                                        async def sleep_and_cancel(seconds):
                                            nonlocal call_count
                                            call_count += 1
                                            if call_count > 1:
                                                raise asyncio.CancelledError()
                                        mock_sleep.side_effect = sleep_and_cancel

                                        await job_health_monitor()

                                        mock_stuck.assert_called_once()
                                        mock_orphaned.assert_called_once()
                                        mock_offline.assert_called_once()
                                        mock_sync.assert_called_once()
                                        mock_locks.assert_called_once()
                                        mock_stopping.assert_called_once()
                                        mock_starting.assert_called_once()

    @pytest.mark.asyncio
    async def test_stops_on_cancelled_error(self):
        """Should stop gracefully when cancelled."""
        from app.tasks.job_health import job_health_monitor

        with patch("app.tasks.job_health.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            mock_sleep.side_effect = asyncio.CancelledError()

            await job_health_monitor()

    @pytest.mark.asyncio
    async def test_continues_on_general_exception(self):
        """Should continue running after handling an exception."""
        from app.tasks.job_health import job_health_monitor

        with patch("app.tasks.job_health.check_stuck_jobs", new_callable=AsyncMock) as mock_stuck:
            call_count = 0
            async def check_with_error():
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise Exception("Test error")
            mock_stuck.side_effect = check_with_error

            with patch("app.tasks.job_health.check_orphaned_queued_jobs", new_callable=AsyncMock):
                with patch("app.tasks.job_health.check_jobs_on_offline_agents", new_callable=AsyncMock):
                    with patch("app.tasks.job_health.check_stuck_image_sync_jobs", new_callable=AsyncMock):
                        with patch("app.tasks.job_health.check_stuck_locks", new_callable=AsyncMock):
                            with patch("app.tasks.job_health.check_stuck_stopping_nodes", new_callable=AsyncMock):
                                with patch("app.tasks.job_health.check_stuck_starting_nodes", new_callable=AsyncMock):
                                    with patch("app.tasks.job_health.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                                        sleep_count = 0
                                        async def sleep_and_cancel(seconds):
                                            nonlocal sleep_count
                                            sleep_count += 1
                                            if sleep_count > 2:
                                                raise asyncio.CancelledError()
                                        mock_sleep.side_effect = sleep_and_cancel

                                        await job_health_monitor()


class TestRetryJob:
    """Tests for the _retry_job function."""

    @pytest.mark.asyncio
    async def test_creates_new_job_with_incremented_retry_count(self, test_db: Session, sample_job: models.Job):
        """Should create a new job with retry_count incremented."""
        from app.tasks.job_health import _retry_job

        original_retry_count = sample_job.retry_count

        with patch("app.tasks.job_health._trigger_job_execution", new_callable=AsyncMock):
            await _retry_job(test_db, sample_job)

            # Check old job was marked failed
            test_db.refresh(sample_job)
            assert sample_job.status == "failed"

            # Check new job was created
            new_jobs = test_db.query(models.Job).filter(
                models.Job.lab_id == sample_job.lab_id,
                models.Job.status == "queued",
            ).all()
            assert len(new_jobs) == 1
            assert new_jobs[0].retry_count == original_retry_count + 1

    @pytest.mark.asyncio
    async def test_sets_superseded_by_id_on_old_job(self, test_db: Session, sample_job: models.Job):
        """Should set superseded_by_id on old job pointing to new job."""
        from app.tasks.job_health import _retry_job

        with patch("app.tasks.job_health._trigger_job_execution", new_callable=AsyncMock):
            await _retry_job(test_db, sample_job)

            test_db.refresh(sample_job)
            assert sample_job.superseded_by_id is not None

            # Verify the superseded_by_id points to the new job
            new_job = test_db.get(models.Job, sample_job.superseded_by_id)
            assert new_job is not None
            assert new_job.status == "queued"
            assert new_job.action == sample_job.action

    @pytest.mark.asyncio
    async def test_cancels_child_jobs_on_parent_retry(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """Should cancel all child jobs when parent job is retried."""
        from app.tasks.job_health import _retry_job

        # Create parent job
        parent_job = models.Job(
            id="parent-job-1",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="sync:lab",
            status="running",
            started_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
        test_db.add(parent_job)
        test_db.commit()

        # Create child jobs
        child1 = models.Job(
            id="child-job-1",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="sync:agent:agent1:node1",
            status="running",
            parent_job_id=parent_job.id,
        )
        child2 = models.Job(
            id="child-job-2",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="sync:agent:agent2:node2",
            status="queued",
            parent_job_id=parent_job.id,
        )
        test_db.add_all([child1, child2])
        test_db.commit()

        with patch("app.tasks.job_health._trigger_job_execution", new_callable=AsyncMock):
            await _retry_job(test_db, parent_job)

            # Verify children were cancelled
            test_db.refresh(child1)
            test_db.refresh(child2)
            assert child1.status == "cancelled"
            assert child2.status == "cancelled"
            assert "parent job retried" in child1.log_path.lower()
            assert "parent job retried" in child2.log_path.lower()

            # Verify superseded_by_id points to new parent job
            assert child1.superseded_by_id == parent_job.superseded_by_id
            assert child2.superseded_by_id == parent_job.superseded_by_id

    @pytest.mark.asyncio
    async def test_deduplication_prevents_duplicate_retry(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """Should not create duplicate retry if job with same action already exists."""
        from app.tasks.job_health import _retry_job

        # Create existing queued job
        existing_job = models.Job(
            id="existing-job-1",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="queued",
        )
        test_db.add(existing_job)

        # Create stuck job that would be retried
        stuck_job = models.Job(
            id="stuck-job-1",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="running",
            started_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
        test_db.add(stuck_job)
        test_db.commit()

        with patch("app.tasks.job_health._trigger_job_execution", new_callable=AsyncMock) as mock_trigger:
            await _retry_job(test_db, stuck_job)

            # Verify no new job was created (trigger not called)
            mock_trigger.assert_not_called()

            # Verify stuck job was marked as cancelled (not failed) with superseded_by_id
            test_db.refresh(stuck_job)
            assert stuck_job.status == "cancelled"
            assert stuck_job.superseded_by_id == existing_job.id
            assert "duplicate" in stuck_job.log_path.lower()

    @pytest.mark.asyncio
    async def test_does_not_cancel_completed_children(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """Should not cancel already completed child jobs when parent is retried."""
        from app.tasks.job_health import _retry_job

        # Create parent job
        parent_job = models.Job(
            id="parent-with-completed-child",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="sync:lab",
            status="running",
            started_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
        test_db.add(parent_job)
        test_db.commit()

        # Create completed child job
        completed_child = models.Job(
            id="completed-child",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="sync:agent:agent1:node1",
            status="completed",
            parent_job_id=parent_job.id,
            completed_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        test_db.add(completed_child)
        test_db.commit()

        with patch("app.tasks.job_health._trigger_job_execution", new_callable=AsyncMock):
            await _retry_job(test_db, parent_job)

            # Completed child should remain completed
            test_db.refresh(completed_child)
            assert completed_child.status == "completed"
            assert completed_child.superseded_by_id is None

    @pytest.mark.asyncio
    async def test_does_not_cancel_failed_children(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """Should not cancel already failed child jobs when parent is retried."""
        from app.tasks.job_health import _retry_job

        # Create parent job
        parent_job = models.Job(
            id="parent-with-failed-child",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="sync:lab",
            status="running",
            started_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
        test_db.add(parent_job)
        test_db.commit()

        # Create failed child job
        failed_child = models.Job(
            id="failed-child",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="sync:agent:agent1:node1",
            status="failed",
            parent_job_id=parent_job.id,
            completed_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            log_path="Original failure reason",
        )
        test_db.add(failed_child)
        test_db.commit()

        with patch("app.tasks.job_health._trigger_job_execution", new_callable=AsyncMock):
            await _retry_job(test_db, parent_job)

            # Failed child should remain failed with original log
            test_db.refresh(failed_child)
            assert failed_child.status == "failed"
            assert failed_child.superseded_by_id is None
            assert "Original failure reason" in failed_child.log_path

    @pytest.mark.asyncio
    async def test_retry_with_no_children(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """Should retry parent job normally when no child jobs exist."""
        from app.tasks.job_health import _retry_job

        # Create parent job with no children
        parent_job = models.Job(
            id="parent-no-children",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="sync:lab",
            status="running",
            started_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
        test_db.add(parent_job)
        test_db.commit()

        with patch("app.tasks.job_health._trigger_job_execution", new_callable=AsyncMock) as mock_trigger:
            await _retry_job(test_db, parent_job)

            # Should have created new job and triggered execution
            mock_trigger.assert_called_once()

            test_db.refresh(parent_job)
            assert parent_job.status == "failed"
            assert parent_job.superseded_by_id is not None

    @pytest.mark.asyncio
    async def test_dedup_with_existing_running_job(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """Should not create retry if running job with same action exists."""
        from app.tasks.job_health import _retry_job

        # Create existing running job
        existing_job = models.Job(
            id="existing-running-job",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="running",
            started_at=datetime.now(timezone.utc),
        )
        test_db.add(existing_job)

        # Create stuck job that would be retried
        stuck_job = models.Job(
            id="stuck-job-dedup-running",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="running",
            started_at=datetime.now(timezone.utc) - timedelta(minutes=30),
        )
        test_db.add(stuck_job)
        test_db.commit()

        with patch("app.tasks.job_health._trigger_job_execution", new_callable=AsyncMock) as mock_trigger:
            await _retry_job(test_db, stuck_job)

            # Verify no new job was created
            mock_trigger.assert_not_called()

            # Verify stuck job was marked as cancelled
            test_db.refresh(stuck_job)
            assert stuck_job.status == "cancelled"
            assert stuck_job.superseded_by_id == existing_job.id

    @pytest.mark.asyncio
    async def test_no_dedup_for_different_action(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """Should create retry even if job with different action exists."""
        from app.tasks.job_health import _retry_job

        # Create existing job with different action
        existing_job = models.Job(
            id="existing-different-action",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="down",  # Different action
            status="queued",
        )
        test_db.add(existing_job)

        # Create stuck job
        stuck_job = models.Job(
            id="stuck-job-different-action",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",  # Different from existing
            status="running",
            started_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
        test_db.add(stuck_job)
        test_db.commit()

        with patch("app.tasks.job_health._trigger_job_execution", new_callable=AsyncMock) as mock_trigger:
            await _retry_job(test_db, stuck_job)

            # Should have created new job (different actions don't dedupe)
            mock_trigger.assert_called_once()

            test_db.refresh(stuck_job)
            assert stuck_job.status == "failed"  # Normal retry marks as failed
            assert stuck_job.superseded_by_id is not None
            # Superseded_by_id should point to NEW job, not existing one
            assert stuck_job.superseded_by_id != existing_job.id

    @pytest.mark.asyncio
    async def test_no_dedup_for_different_lab(
        self, test_db: Session, test_user: models.User
    ):
        """Should create retry even if job for different lab exists."""
        from app.tasks.job_health import _retry_job

        # Create two labs
        lab1 = models.Lab(id="lab-1", name="Lab 1", owner_id=test_user.id)
        lab2 = models.Lab(id="lab-2", name="Lab 2", owner_id=test_user.id)
        test_db.add_all([lab1, lab2])
        test_db.commit()

        # Create existing job for lab1
        existing_job = models.Job(
            id="existing-job-lab1",
            lab_id=lab1.id,
            user_id=test_user.id,
            action="up",
            status="queued",
        )
        test_db.add(existing_job)

        # Create stuck job for lab2
        stuck_job = models.Job(
            id="stuck-job-lab2",
            lab_id=lab2.id,  # Different lab
            user_id=test_user.id,
            action="up",
            status="running",
            started_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
        test_db.add(stuck_job)
        test_db.commit()

        with patch("app.tasks.job_health._trigger_job_execution", new_callable=AsyncMock) as mock_trigger:
            await _retry_job(test_db, stuck_job)

            # Should have created new job (different labs don't dedupe)
            mock_trigger.assert_called_once()

            test_db.refresh(stuck_job)
            assert stuck_job.status == "failed"
            assert stuck_job.superseded_by_id != existing_job.id

    @pytest.mark.asyncio
    async def test_no_dedup_against_completed_jobs(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """Completed jobs should not trigger deduplication."""
        from app.tasks.job_health import _retry_job

        # Create completed job with same action
        completed_job = models.Job(
            id="completed-job-same-action",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="completed",
            completed_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        test_db.add(completed_job)

        # Create stuck job
        stuck_job = models.Job(
            id="stuck-job-completed-exists",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="running",
            started_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
        test_db.add(stuck_job)
        test_db.commit()

        with patch("app.tasks.job_health._trigger_job_execution", new_callable=AsyncMock) as mock_trigger:
            await _retry_job(test_db, stuck_job)

            # Should have created new job (completed jobs don't count for dedup)
            mock_trigger.assert_called_once()

            test_db.refresh(stuck_job)
            assert stuck_job.status == "failed"

    @pytest.mark.asyncio
    async def test_no_dedup_against_failed_jobs(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """Failed jobs should not trigger deduplication."""
        from app.tasks.job_health import _retry_job

        # Create failed job with same action
        failed_job = models.Job(
            id="failed-job-same-action",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="failed",
            completed_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        test_db.add(failed_job)

        # Create stuck job
        stuck_job = models.Job(
            id="stuck-job-failed-exists",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="running",
            started_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
        test_db.add(stuck_job)
        test_db.commit()

        with patch("app.tasks.job_health._trigger_job_execution", new_callable=AsyncMock) as mock_trigger:
            await _retry_job(test_db, stuck_job)

            # Should have created new job (failed jobs don't count for dedup)
            mock_trigger.assert_called_once()

            test_db.refresh(stuck_job)
            assert stuck_job.status == "failed"

    @pytest.mark.asyncio
    async def test_job_without_lab_id_bypasses_dedup(
        self, test_db: Session, test_user: models.User
    ):
        """Jobs without lab_id should bypass deduplication safely."""
        from app.tasks.job_health import _retry_job

        # Create stuck job without lab_id
        stuck_job = models.Job(
            id="stuck-job-no-lab",
            lab_id=None,  # No lab
            user_id=test_user.id,
            action="cleanup",
            status="running",
            started_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
        test_db.add(stuck_job)
        test_db.commit()

        with patch("app.tasks.job_health._trigger_job_execution", new_callable=AsyncMock) as mock_trigger:
            await _retry_job(test_db, stuck_job)

            # Should have created new job without dedup check
            mock_trigger.assert_called_once()

            test_db.refresh(stuck_job)
            assert stuck_job.status == "failed"


class TestFailJob:
    """Tests for the _fail_job function."""

    @pytest.mark.asyncio
    async def test_marks_job_as_failed(self, test_db: Session, sample_job: models.Job):
        """Should mark job as failed with reason."""
        from app.tasks.job_health import _fail_job

        await _fail_job(test_db, sample_job, reason="Test failure reason")

        test_db.refresh(sample_job)
        assert sample_job.status == "failed"
        assert sample_job.completed_at is not None

    @pytest.mark.asyncio
    async def test_updates_lab_state_to_error(self, test_db: Session, sample_job: models.Job, sample_lab: models.Lab):
        """Should set lab state to error when job fails."""
        from app.tasks.job_health import _fail_job

        await _fail_job(test_db, sample_job, reason="Test failure")

        test_db.refresh(sample_lab)
        assert sample_lab.state == "error"
        assert sample_lab.state_error is not None


class TestParentChildIntegration:
    """Integration tests for parent-child job scenarios."""

    @pytest.mark.asyncio
    async def test_multiple_stuck_jobs_deduplicated(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """Multiple stuck jobs for same lab/action should result in only one retry."""
        from app.tasks.job_health import _retry_job

        # Create one stuck job that will be retried first
        stuck_job = models.Job(
            id="stuck-job-to-retry",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="running",
            started_at=datetime.now(timezone.utc) - timedelta(minutes=30),
        )
        test_db.add(stuck_job)
        test_db.commit()

        # Retry the first stuck job - this creates a new queued job
        with patch("app.tasks.job_health._trigger_job_execution", new_callable=AsyncMock):
            await _retry_job(test_db, stuck_job)

        test_db.refresh(stuck_job)
        new_job_id = stuck_job.superseded_by_id
        assert new_job_id is not None

        # Verify the new job was created and is queued
        new_job = test_db.get(models.Job, new_job_id)
        assert new_job is not None
        assert new_job.status == "queued"

        # Now create another stuck job with the same action
        another_stuck_job = models.Job(
            id="another-stuck-job",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="running",
            started_at=datetime.now(timezone.utc) - timedelta(minutes=20),
        )
        test_db.add(another_stuck_job)
        test_db.commit()

        # Retry the second stuck job - should dedupe against the new queued job
        with patch("app.tasks.job_health._trigger_job_execution", new_callable=AsyncMock) as mock_trigger:
            await _retry_job(test_db, another_stuck_job)

            # Should not create another job (deduped)
            mock_trigger.assert_not_called()

            test_db.refresh(another_stuck_job)
            assert another_stuck_job.status == "cancelled"
            assert another_stuck_job.superseded_by_id == new_job_id

        # Create a third stuck job
        third_stuck_job = models.Job(
            id="third-stuck-job",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="running",
            started_at=datetime.now(timezone.utc) - timedelta(minutes=15),
        )
        test_db.add(third_stuck_job)
        test_db.commit()

        # Retry the third - should also dedupe
        with patch("app.tasks.job_health._trigger_job_execution", new_callable=AsyncMock) as mock_trigger:
            await _retry_job(test_db, third_stuck_job)

            mock_trigger.assert_not_called()

            test_db.refresh(third_stuck_job)
            assert third_stuck_job.status == "cancelled"
            assert third_stuck_job.superseded_by_id == new_job_id

    @pytest.mark.asyncio
    async def test_parent_and_child_both_stuck_only_parent_retried(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User, sample_host: models.Host
    ):
        """When both parent and child are stuck, only parent should be retried."""
        from app.tasks.job_health import _check_single_job

        # Create stuck parent job
        parent_job = models.Job(
            id="stuck-parent",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="sync:lab",
            status="running",
            agent_id=sample_host.id,
            started_at=datetime.now(timezone.utc) - timedelta(minutes=30),
        )
        test_db.add(parent_job)
        test_db.commit()

        # Create stuck child job
        child_job = models.Job(
            id="stuck-child",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="sync:agent:agent1:node1",
            status="running",
            parent_job_id=parent_job.id,
            started_at=datetime.now(timezone.utc) - timedelta(minutes=30),
        )
        test_db.add(child_job)
        test_db.commit()

        now = datetime.now(timezone.utc)

        # Check child first - should be skipped (parent still running)
        with patch("app.tasks.job_health.is_job_stuck", return_value=True):
            await _check_single_job(test_db, child_job, now)

            test_db.refresh(child_job)
            assert child_job.status == "running"  # Skipped

        # Now check parent - should be retried
        with patch("app.tasks.job_health.is_job_stuck", return_value=True):
            with patch("app.tasks.job_health._retry_job", new_callable=AsyncMock) as mock_retry:
                await _check_single_job(test_db, parent_job, now)

                mock_retry.assert_called_once()

    @pytest.mark.asyncio
    async def test_full_retry_cycle_flow(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """Test full flow: parent retry cancels children, creates new parent."""
        from app.tasks.job_health import _retry_job

        # Create parent job
        parent_job = models.Job(
            id="parent-full-cycle",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="sync:lab",
            status="running",
            started_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
        test_db.add(parent_job)
        test_db.commit()

        # Create multiple children
        children = []
        for i in range(3):
            child = models.Job(
                id=f"child-full-cycle-{i}",
                lab_id=sample_lab.id,
                user_id=test_user.id,
                action=f"sync:agent:agent{i}:node{i}",
                status="running" if i < 2 else "queued",
                parent_job_id=parent_job.id,
            )
            test_db.add(child)
            children.append(child)
        test_db.commit()

        with patch("app.tasks.job_health._trigger_job_execution", new_callable=AsyncMock):
            await _retry_job(test_db, parent_job)

        # Verify parent is failed with superseded_by_id
        test_db.refresh(parent_job)
        assert parent_job.status == "failed"
        new_parent_id = parent_job.superseded_by_id
        assert new_parent_id is not None

        # Verify new parent job exists
        new_parent = test_db.get(models.Job, new_parent_id)
        assert new_parent is not None
        assert new_parent.status == "queued"
        assert new_parent.action == "sync:lab"
        assert new_parent.retry_count == 1

        # Verify all children were cancelled and point to new parent
        for child in children:
            test_db.refresh(child)
            assert child.status == "cancelled"
            assert child.superseded_by_id == new_parent_id


class TestEdgeCases:
    """Edge case tests for parent-child job tracking."""

    @pytest.mark.asyncio
    async def test_deeply_nested_job_hierarchy(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """Jobs with grandparent relationships should be handled correctly."""
        from app.tasks.job_health import _check_single_job

        # Create grandparent job (still running)
        grandparent = models.Job(
            id="grandparent-job",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="deploy:lab",
            status="running",
            started_at=datetime.now(timezone.utc),
        )
        test_db.add(grandparent)
        test_db.commit()

        # Create parent job (child of grandparent)
        parent = models.Job(
            id="parent-nested",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="sync:lab",
            status="running",
            parent_job_id=grandparent.id,
            started_at=datetime.now(timezone.utc),
        )
        test_db.add(parent)
        test_db.commit()

        # Create grandchild job (child of parent)
        grandchild = models.Job(
            id="grandchild-job",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="sync:agent:agent1:node1",
            status="running",
            parent_job_id=parent.id,
            started_at=datetime.now(timezone.utc) - timedelta(minutes=30),
        )
        test_db.add(grandchild)
        test_db.commit()

        now = datetime.now(timezone.utc)

        # Check grandchild - parent is running, should skip
        with patch("app.tasks.job_health.is_job_stuck", return_value=True):
            await _check_single_job(test_db, grandchild, now)

            test_db.refresh(grandchild)
            # Grandchild should be skipped because its immediate parent is running
            assert grandchild.status == "running"

    @pytest.mark.asyncio
    async def test_child_with_explicit_null_parent_id(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User, sample_host: models.Host
    ):
        """Jobs with explicit NULL parent_job_id should work normally."""
        from app.tasks.job_health import _check_single_job

        # Create job with explicit None parent_job_id
        job = models.Job(
            id="explicit-null-parent",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="running",
            agent_id=sample_host.id,
            started_at=datetime.now(timezone.utc) - timedelta(minutes=30),
            parent_job_id=None,  # Explicit None
        )
        test_db.add(job)
        test_db.commit()

        now = datetime.now(timezone.utc)

        with patch("app.tasks.job_health.is_job_stuck", return_value=True):
            with patch("app.tasks.job_health._retry_job", new_callable=AsyncMock) as mock_retry:
                await _check_single_job(test_db, job, now)

                # Should proceed with normal retry logic
                mock_retry.assert_called_once()

    @pytest.mark.asyncio
    async def test_superseded_chain(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """Test chain of superseded jobs (job1 -> job2 -> job3)."""
        from app.tasks.job_health import _retry_job

        # Create first job
        job1 = models.Job(
            id="job-chain-1",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="running",
            started_at=datetime.now(timezone.utc) - timedelta(minutes=20),
        )
        test_db.add(job1)
        test_db.commit()

        # First retry
        with patch("app.tasks.job_health._trigger_job_execution", new_callable=AsyncMock):
            await _retry_job(test_db, job1)

        test_db.refresh(job1)
        job2_id = job1.superseded_by_id
        job2 = test_db.get(models.Job, job2_id)
        assert job2 is not None
        assert job2.retry_count == 1

        # Simulate job2 getting stuck and retried
        job2.status = "running"
        job2.started_at = datetime.now(timezone.utc) - timedelta(minutes=15)
        test_db.commit()

        with patch("app.tasks.job_health._trigger_job_execution", new_callable=AsyncMock):
            await _retry_job(test_db, job2)

        test_db.refresh(job2)
        job3_id = job2.superseded_by_id
        job3 = test_db.get(models.Job, job3_id)
        assert job3 is not None
        assert job3.retry_count == 2

        # Verify chain: job1 -> job2 -> job3
        assert job1.superseded_by_id == job2.id
        assert job2.superseded_by_id == job3.id

    @pytest.mark.asyncio
    async def test_child_with_log_path_appends_message(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """Child job with existing log_path should append cancellation message."""
        from app.tasks.job_health import _retry_job

        # Create parent job
        parent_job = models.Job(
            id="parent-log-test",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="sync:lab",
            status="running",
            started_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
        test_db.add(parent_job)
        test_db.commit()

        # Create child with existing log
        child_job = models.Job(
            id="child-with-log",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="sync:agent:agent1:node1",
            status="running",
            parent_job_id=parent_job.id,
            log_path="Initial sync started\nProgress: 50%",
        )
        test_db.add(child_job)
        test_db.commit()

        with patch("app.tasks.job_health._trigger_job_execution", new_callable=AsyncMock):
            await _retry_job(test_db, parent_job)

        test_db.refresh(child_job)
        assert child_job.status == "cancelled"
        # Should contain both original log and cancellation message
        assert "Initial sync started" in child_job.log_path
        assert "Progress: 50%" in child_job.log_path
        assert "parent job retried" in child_job.log_path.lower()

    @pytest.mark.asyncio
    async def test_child_without_log_path_gets_message(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """Child job without log_path should get cancellation message set."""
        from app.tasks.job_health import _retry_job

        # Create parent job
        parent_job = models.Job(
            id="parent-no-log-test",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="sync:lab",
            status="running",
            started_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
        test_db.add(parent_job)
        test_db.commit()

        # Create child without log
        child_job = models.Job(
            id="child-no-log",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="sync:agent:agent1:node1",
            status="running",
            parent_job_id=parent_job.id,
            log_path=None,
        )
        test_db.add(child_job)
        test_db.commit()

        with patch("app.tasks.job_health._trigger_job_execution", new_callable=AsyncMock):
            await _retry_job(test_db, parent_job)

        test_db.refresh(child_job)
        assert child_job.status == "cancelled"
        assert child_job.log_path is not None
        assert "parent job retried" in child_job.log_path.lower()
