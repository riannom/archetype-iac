"""Tests for the disk cleanup background task."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import tempfile
import os

import pytest

from app.tasks.disk_cleanup import (
    cleanup_orphaned_upload_files,
    cleanup_stale_upload_sessions,
    cleanup_stale_iso_sessions,
    cleanup_old_job_records,
    cleanup_old_webhook_deliveries,
    cleanup_docker_on_agents,
    get_disk_usage,
    run_disk_cleanup,
)


class TestCleanupOrphanedUploadFiles:
    """Tests for cleanup_orphaned_upload_files function."""

    @pytest.fixture
    def temp_upload_dir(self, tmp_path):
        """Create a temporary upload directory."""
        upload_dir = tmp_path / "uploads"
        upload_dir.mkdir()
        return upload_dir

    @pytest.mark.asyncio
    async def test_deletes_old_partial_files(self, temp_upload_dir):
        """Test that old .partial files are deleted."""
        with patch("app.tasks.disk_cleanup.settings") as mock_settings:
            mock_settings.iso_upload_dir = str(temp_upload_dir)
            mock_settings.cleanup_upload_file_age = 3600  # 1 hour

            # Create an old partial file
            old_file = temp_upload_dir / ".upload_test123.partial"
            old_file.write_bytes(b"test data")

            # Set modification time to 2 hours ago
            old_time = datetime.now().timestamp() - 7200
            os.utime(old_file, (old_time, old_time))

            # Mock the upload sessions to have no active uploads
            with patch("app.tasks.disk_cleanup._upload_sessions", {}), \
                 patch("app.tasks.disk_cleanup._upload_lock", MagicMock()):
                # Need to patch the import path
                with patch("app.routers.iso._upload_sessions", {}), \
                     patch("app.routers.iso._upload_lock", MagicMock()):
                    result = await cleanup_orphaned_upload_files()

            assert result["deleted_count"] == 1
            assert not old_file.exists()

    @pytest.mark.asyncio
    async def test_keeps_recent_partial_files(self, temp_upload_dir):
        """Test that recent .partial files are kept."""
        with patch("app.tasks.disk_cleanup.settings") as mock_settings:
            mock_settings.iso_upload_dir = str(temp_upload_dir)
            mock_settings.cleanup_upload_file_age = 3600  # 1 hour

            # Create a recent partial file
            recent_file = temp_upload_dir / ".upload_recent.partial"
            recent_file.write_bytes(b"test data")
            # File is new, so it should be kept

            with patch("app.routers.iso._upload_sessions", {}), \
                 patch("app.routers.iso._upload_lock", MagicMock()):
                result = await cleanup_orphaned_upload_files()

            assert result["deleted_count"] == 0
            assert recent_file.exists()

    @pytest.mark.asyncio
    async def test_skips_active_upload_files(self, temp_upload_dir):
        """Test that files being actively uploaded are skipped."""
        with patch("app.tasks.disk_cleanup.settings") as mock_settings:
            mock_settings.iso_upload_dir = str(temp_upload_dir)
            mock_settings.cleanup_upload_file_age = 3600

            # Create an old partial file
            active_file = temp_upload_dir / ".upload_active.partial"
            active_file.write_bytes(b"test data")
            old_time = datetime.now().timestamp() - 7200
            os.utime(active_file, (old_time, old_time))

            # Mock an active upload session
            mock_sessions = {
                "active": {
                    "temp_path": str(active_file),
                    "status": "uploading",
                }
            }

            with patch("app.routers.iso._upload_sessions", mock_sessions), \
                 patch("app.routers.iso._upload_lock", MagicMock()):
                result = await cleanup_orphaned_upload_files()

            assert result["deleted_count"] == 0
            assert active_file.exists()

    @pytest.mark.asyncio
    async def test_ignores_non_partial_files(self, temp_upload_dir):
        """Test that regular files are not touched."""
        with patch("app.tasks.disk_cleanup.settings") as mock_settings:
            mock_settings.iso_upload_dir = str(temp_upload_dir)
            mock_settings.cleanup_upload_file_age = 3600

            # Create a regular ISO file
            iso_file = temp_upload_dir / "test.iso"
            iso_file.write_bytes(b"test data")

            # Set modification time to 2 hours ago
            old_time = datetime.now().timestamp() - 7200
            os.utime(iso_file, (old_time, old_time))

            with patch("app.routers.iso._upload_sessions", {}), \
                 patch("app.routers.iso._upload_lock", MagicMock()):
                result = await cleanup_orphaned_upload_files()

            assert result["deleted_count"] == 0
            assert iso_file.exists()


class TestCleanupStaleUploadSessions:
    """Tests for cleanup_stale_upload_sessions function."""

    @pytest.mark.asyncio
    async def test_expires_old_sessions(self):
        """Test that old upload sessions are expired."""
        with patch("app.tasks.disk_cleanup.settings") as mock_settings:
            mock_settings.cleanup_upload_session_age = 3600  # 1 hour

            old_session = {
                "upload_id": "old123",
                "status": "uploading",
                "created_at": datetime.now(timezone.utc) - timedelta(hours=2),
                "temp_path": "/tmp/nonexistent.partial",
            }

            mock_sessions = {"old123": old_session}

            with patch("app.routers.iso._upload_sessions", mock_sessions), \
                 patch("app.routers.iso._upload_lock", MagicMock()):
                result = await cleanup_stale_upload_sessions()

            assert result["expired_count"] == 1

    @pytest.mark.asyncio
    async def test_keeps_recent_sessions(self):
        """Test that recent upload sessions are kept."""
        with patch("app.tasks.disk_cleanup.settings") as mock_settings:
            mock_settings.cleanup_upload_session_age = 3600

            recent_session = {
                "upload_id": "recent123",
                "status": "uploading",
                "created_at": datetime.now(timezone.utc) - timedelta(minutes=30),
            }

            mock_sessions = {"recent123": recent_session}

            with patch("app.routers.iso._upload_sessions", mock_sessions), \
                 patch("app.routers.iso._upload_lock", MagicMock()):
                result = await cleanup_stale_upload_sessions()

            assert result["expired_count"] == 0

    @pytest.mark.asyncio
    async def test_keeps_completed_sessions(self):
        """Test that completed sessions are not expired (they already completed)."""
        with patch("app.tasks.disk_cleanup.settings") as mock_settings:
            mock_settings.cleanup_upload_session_age = 3600

            completed_session = {
                "upload_id": "completed123",
                "status": "completed",
                "created_at": datetime.now(timezone.utc) - timedelta(hours=2),
            }

            mock_sessions = {"completed123": completed_session}

            with patch("app.routers.iso._upload_sessions", mock_sessions), \
                 patch("app.routers.iso._upload_lock", MagicMock()):
                result = await cleanup_stale_upload_sessions()

            assert result["expired_count"] == 0


class TestCleanupStaleISOSessions:
    """Tests for cleanup_stale_iso_sessions function."""

    @pytest.mark.asyncio
    async def test_expires_old_iso_sessions(self):
        """Test that old ISO sessions are expired."""
        with patch("app.tasks.disk_cleanup.settings") as mock_settings:
            mock_settings.cleanup_iso_session_age = 3600

            # Create a mock ISOSession
            mock_session = MagicMock()
            mock_session.status = "scanned"
            mock_session.updated_at = datetime.now(timezone.utc) - timedelta(hours=2)

            mock_sessions = {"old123": mock_session}

            with patch("app.routers.iso._sessions", mock_sessions), \
                 patch("app.routers.iso._session_lock", MagicMock()):
                result = await cleanup_stale_iso_sessions()

            assert result["expired_count"] == 1

    @pytest.mark.asyncio
    async def test_keeps_importing_sessions(self):
        """Test that actively importing sessions are kept."""
        with patch("app.tasks.disk_cleanup.settings") as mock_settings:
            mock_settings.cleanup_iso_session_age = 3600

            mock_session = MagicMock()
            mock_session.status = "importing"
            mock_session.updated_at = datetime.now(timezone.utc) - timedelta(hours=2)

            mock_sessions = {"importing123": mock_session}

            with patch("app.routers.iso._sessions", mock_sessions), \
                 patch("app.routers.iso._session_lock", MagicMock()):
                result = await cleanup_stale_iso_sessions()

            assert result["expired_count"] == 0


class TestCleanupOldJobRecords:
    """Tests for cleanup_old_job_records function."""

    @pytest.mark.asyncio
    async def test_deletes_old_completed_jobs(self):
        """Test that old completed jobs are deleted."""
        with patch("app.tasks.disk_cleanup.settings") as mock_settings:
            mock_settings.cleanup_job_retention_days = 30

            # Mock job query
            old_job = MagicMock()
            old_job.id = "old-job-123"

            mock_query = MagicMock()
            mock_query.filter.return_value = mock_query
            mock_query.all.return_value = [old_job]

            mock_session = MagicMock()
            mock_session.query.return_value = mock_query

            with patch("app.tasks.disk_cleanup.SessionLocal", return_value=mock_session):
                result = await cleanup_old_job_records()

            assert result["deleted_count"] == 1
            mock_session.delete.assert_called_once_with(old_job)
            mock_session.commit.assert_called()


class TestCleanupOldWebhookDeliveries:
    """Tests for cleanup_old_webhook_deliveries function."""

    @pytest.mark.asyncio
    async def test_deletes_old_deliveries(self):
        """Test that old webhook deliveries are deleted."""
        with patch("app.tasks.disk_cleanup.settings") as mock_settings:
            mock_settings.cleanup_webhook_retention_days = 7

            mock_query = MagicMock()
            mock_query.filter.return_value = mock_query
            mock_query.delete.return_value = 5  # 5 records deleted

            mock_session = MagicMock()
            mock_session.query.return_value = mock_query

            with patch("app.tasks.disk_cleanup.SessionLocal", return_value=mock_session):
                result = await cleanup_old_webhook_deliveries()

            assert result["deleted_count"] == 5
            mock_session.commit.assert_called()


class TestCleanupDockerOnAgents:
    """Tests for cleanup_docker_on_agents function."""

    @pytest.mark.asyncio
    async def test_disabled_returns_skipped(self):
        """Test that disabled cleanup returns skipped status."""
        with patch("app.tasks.disk_cleanup.settings") as mock_settings:
            mock_settings.cleanup_docker_enabled = False

            result = await cleanup_docker_on_agents()

            assert result["skipped"] == "disabled"
            assert result["agents_cleaned"] == 0

    @pytest.mark.asyncio
    async def test_calls_prune_on_online_agents(self):
        """Test that prune is called on all online agents."""
        with patch("app.tasks.disk_cleanup.settings") as mock_settings:
            mock_settings.cleanup_docker_enabled = True
            mock_settings.cleanup_docker_dangling_images = True
            mock_settings.cleanup_docker_build_cache = True
            mock_settings.cleanup_docker_unused_volumes = False

            mock_agent = MagicMock()
            mock_agent.id = "agent-1"
            mock_agent.name = "agent-1"
            mock_agent.status = "online"

            mock_lab = MagicMock()
            mock_lab.id = "lab-1"

            mock_session = MagicMock()
            mock_host_query = MagicMock()
            mock_host_query.filter.return_value = mock_host_query
            mock_host_query.all.return_value = [mock_agent]

            mock_lab_query = MagicMock()
            mock_lab_query.all.return_value = [mock_lab]

            def query_side_effect(model):
                if model.__name__ == "Host":
                    return mock_host_query
                elif model.__name__ == "Lab":
                    return mock_lab_query
                return MagicMock()

            mock_session.query.side_effect = query_side_effect

            mock_prune = AsyncMock(return_value={
                "success": True,
                "images_removed": 2,
                "build_cache_removed": 1,
                "volumes_removed": 0,
                "space_reclaimed": 1000000,
            })

            with patch("app.tasks.disk_cleanup.SessionLocal", return_value=mock_session), \
                 patch("app.tasks.disk_cleanup.agent_client.prune_docker_on_agent", mock_prune):
                result = await cleanup_docker_on_agents()

            assert result["agents_cleaned"] == 1
            assert result["space_reclaimed"] == 1000000
            mock_prune.assert_called_once()


class TestGetDiskUsage:
    """Tests for get_disk_usage function."""

    def test_returns_usage_dict(self, tmp_path):
        """Test that disk usage returns expected dict."""
        result = get_disk_usage(tmp_path)

        assert "total" in result
        assert "used" in result
        assert "free" in result
        assert "percent" in result
        assert isinstance(result["total"], int)
        assert isinstance(result["percent"], float)

    def test_handles_invalid_path(self):
        """Test that invalid path returns error."""
        result = get_disk_usage("/nonexistent/path/that/does/not/exist")

        assert result["total"] == 0
        assert "error" in result


class TestRunDiskCleanup:
    """Tests for run_disk_cleanup function."""

    @pytest.mark.asyncio
    async def test_runs_all_cleanup_tasks(self):
        """Test that run_disk_cleanup runs all tasks."""
        with patch("app.tasks.disk_cleanup.settings") as mock_settings:
            mock_settings.workspace = "/tmp"
            mock_settings.iso_upload_dir = "/tmp"

            mock_results = {
                "deleted_count": 0,
                "deleted_bytes": 0,
                "expired_count": 0,
                "errors": [],
            }

            with patch("app.tasks.disk_cleanup.cleanup_orphaned_upload_files", AsyncMock(return_value=mock_results)), \
                 patch("app.tasks.disk_cleanup.cleanup_stale_upload_sessions", AsyncMock(return_value=mock_results)), \
                 patch("app.tasks.disk_cleanup.cleanup_stale_iso_sessions", AsyncMock(return_value=mock_results)), \
                 patch("app.tasks.disk_cleanup.cleanup_docker_on_agents", AsyncMock(return_value=mock_results)), \
                 patch("app.tasks.disk_cleanup.cleanup_old_job_records", AsyncMock(return_value=mock_results)), \
                 patch("app.tasks.disk_cleanup.cleanup_old_webhook_deliveries", AsyncMock(return_value=mock_results)):
                result = await run_disk_cleanup()

            assert "upload_files" in result
            assert "upload_sessions" in result
            assert "iso_sessions" in result
            assert "docker" in result
            assert "jobs" in result
            assert "webhooks" in result
            assert "disk_usage" in result

    @pytest.mark.asyncio
    async def test_continues_on_task_error(self):
        """Test that cleanup continues even if one task fails."""
        with patch("app.tasks.disk_cleanup.settings") as mock_settings:
            mock_settings.workspace = "/tmp"
            mock_settings.iso_upload_dir = "/tmp"

            mock_results = {"deleted_count": 0, "errors": []}

            with patch("app.tasks.disk_cleanup.cleanup_orphaned_upload_files", AsyncMock(side_effect=Exception("Test error"))), \
                 patch("app.tasks.disk_cleanup.cleanup_stale_upload_sessions", AsyncMock(return_value=mock_results)), \
                 patch("app.tasks.disk_cleanup.cleanup_stale_iso_sessions", AsyncMock(return_value=mock_results)), \
                 patch("app.tasks.disk_cleanup.cleanup_docker_on_agents", AsyncMock(return_value=mock_results)), \
                 patch("app.tasks.disk_cleanup.cleanup_old_job_records", AsyncMock(return_value=mock_results)), \
                 patch("app.tasks.disk_cleanup.cleanup_old_webhook_deliveries", AsyncMock(return_value=mock_results)):
                result = await run_disk_cleanup()

            # Should have error but other tasks should have run
            assert "error" in result["upload_files"]
            assert "upload_sessions" in result
