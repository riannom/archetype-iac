"""Tests for app/tasks/job_health.py - Job health monitoring background task."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
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

        with patch("app.tasks.job_health.SessionLocal", return_value=test_db):
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

        with patch("app.tasks.job_health.SessionLocal", return_value=test_db):
            await check_stuck_image_sync_jobs()
            test_db.refresh(job)
            assert job.status == "failed"

    @pytest.mark.asyncio
    async def test_marks_job_failed_when_host_offline(self, test_db: Session, offline_host: models.Host):
        """Should mark sync job as failed when target host is offline."""
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

        with patch("app.tasks.job_health.SessionLocal", return_value=test_db):
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
