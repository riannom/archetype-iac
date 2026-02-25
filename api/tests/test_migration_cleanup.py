from __future__ import annotations

from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app import models
from app.tasks.migration_cleanup import (
    FAILED_RETRY_STALE_SECONDS,
    MAX_MIGRATION_CLEANUP_ATTEMPTS,
    RUNNING_CLAIM_STALE_SECONDS,
    enqueue_node_migration_cleanup,
    process_pending_migration_cleanups,
    process_pending_migration_cleanups_for_agent,
)


def _make_host(test_db, host_id: str, name: str, status: str = "online") -> models.Host:
    host = models.Host(
        id=host_id,
        name=name,
        address=f"{host_id}.local:8080",
        status=status,
        capabilities='{"providers":["docker"]}',
        version="1.0.0",
        resource_usage='{"cpu_percent": 1, "memory_percent": 1, "disk_percent": 1}',
        last_heartbeat=datetime.now(timezone.utc),
    )
    test_db.add(host)
    test_db.commit()
    test_db.refresh(host)
    return host


@pytest.mark.asyncio
async def test_process_pending_cleanup_success_deletes_row(test_db, sample_lab):
    host = _make_host(test_db, "old-host", "Old Host")
    enqueue_node_migration_cleanup(
        test_db,
        sample_lab.id,
        "R1",
        host.id,
        provider="docker",
        reason="offline old host",
    )
    test_db.commit()

    with patch("app.tasks.migration_cleanup.agent_client") as mock_ac:
        mock_ac.is_agent_online = MagicMock(return_value=True)
        mock_ac.destroy_node_on_agent = AsyncMock(return_value={"success": True})
        stats = await process_pending_migration_cleanups_for_agent(test_db, host)

    assert stats["reclaimed"] == 0
    assert stats["claimed"] == 1
    assert stats["completed"] == 1
    remaining = test_db.query(models.NodeMigrationCleanup).count()
    assert remaining == 0


@pytest.mark.asyncio
async def test_process_pending_cleanup_marks_failed_after_max_attempts(test_db, sample_lab):
    host = _make_host(test_db, "old-host", "Old Host")
    row = enqueue_node_migration_cleanup(
        test_db,
        sample_lab.id,
        "R2",
        host.id,
        provider="docker",
    )
    row.attempt_count = MAX_MIGRATION_CLEANUP_ATTEMPTS - 1
    test_db.commit()

    with patch("app.tasks.migration_cleanup.agent_client") as mock_ac:
        mock_ac.is_agent_online = MagicMock(return_value=True)
        mock_ac.destroy_node_on_agent = AsyncMock(
            return_value={"success": False, "error": "destroy failed"}
        )
        stats = await process_pending_migration_cleanups_for_agent(test_db, host)

    assert stats["reclaimed"] == 0
    assert stats["claimed"] == 1
    assert stats["failed"] == 1
    refreshed = test_db.get(models.NodeMigrationCleanup, row.id)
    assert refreshed is not None
    assert refreshed.status == "failed"
    assert refreshed.attempt_count == MAX_MIGRATION_CLEANUP_ATTEMPTS


@pytest.mark.asyncio
async def test_reclaims_stale_running_rows_and_retries(test_db, sample_lab):
    host = _make_host(test_db, "old-host", "Old Host")
    row = enqueue_node_migration_cleanup(
        test_db,
        sample_lab.id,
        "R3",
        host.id,
        provider="docker",
    )
    row.status = "running"
    row.last_attempt_at = datetime.now(timezone.utc) - timedelta(
        seconds=RUNNING_CLAIM_STALE_SECONDS + 5
    )
    test_db.commit()

    with patch("app.tasks.migration_cleanup.agent_client") as mock_ac:
        mock_ac.is_agent_online = MagicMock(return_value=True)
        mock_ac.destroy_node_on_agent = AsyncMock(return_value={"success": True})
        stats = await process_pending_migration_cleanups_for_agent(test_db, host)

    assert stats["reclaimed"] == 1
    assert stats["claimed"] == 1
    assert stats["completed"] == 1
    assert test_db.query(models.NodeMigrationCleanup).count() == 0


@pytest.mark.asyncio
async def test_reclaims_stale_failed_rows_and_retries(test_db, sample_lab):
    host = _make_host(test_db, "old-host", "Old Host")
    row = enqueue_node_migration_cleanup(
        test_db,
        sample_lab.id,
        "R5",
        host.id,
        provider="docker",
    )
    row.status = "failed"
    row.attempt_count = MAX_MIGRATION_CLEANUP_ATTEMPTS
    row.last_attempt_at = datetime.now(timezone.utc) - timedelta(
        seconds=FAILED_RETRY_STALE_SECONDS + 5
    )
    test_db.commit()

    with patch("app.tasks.migration_cleanup.agent_client") as mock_ac:
        mock_ac.is_agent_online = MagicMock(return_value=True)
        mock_ac.destroy_node_on_agent = AsyncMock(return_value={"success": True})
        stats = await process_pending_migration_cleanups_for_agent(test_db, host)

    assert stats["reclaimed_failed"] == 1
    assert stats["claimed"] == 1
    assert stats["completed"] == 1
    assert test_db.query(models.NodeMigrationCleanup).count() == 0


@pytest.mark.asyncio
async def test_running_rows_are_included_in_global_scan(test_db, sample_lab):
    host = _make_host(test_db, "old-host", "Old Host")
    row = enqueue_node_migration_cleanup(
        test_db,
        sample_lab.id,
        "R4",
        host.id,
        provider="docker",
    )
    row.status = "running"
    row.last_attempt_at = datetime.now(timezone.utc) - timedelta(
        seconds=RUNNING_CLAIM_STALE_SECONDS + 5
    )
    test_db.commit()

    @contextmanager
    def _session_ctx():
        yield test_db

    with patch("app.tasks.migration_cleanup.get_session", _session_ctx), \
         patch("app.tasks.migration_cleanup.agent_client") as mock_ac:
        mock_ac.is_agent_online = MagicMock(return_value=True)
        mock_ac.destroy_node_on_agent = AsyncMock(return_value={"success": True})
        stats_by_agent = await process_pending_migration_cleanups()

    assert host.id in stats_by_agent
    assert stats_by_agent[host.id]["reclaimed"] == 1
    assert stats_by_agent[host.id]["completed"] == 1


@pytest.mark.asyncio
async def test_failed_rows_are_included_in_global_scan(test_db, sample_lab):
    host = _make_host(test_db, "old-host", "Old Host")
    row = enqueue_node_migration_cleanup(
        test_db,
        sample_lab.id,
        "R6",
        host.id,
        provider="docker",
    )
    row.status = "failed"
    row.attempt_count = MAX_MIGRATION_CLEANUP_ATTEMPTS
    row.last_attempt_at = datetime.now(timezone.utc) - timedelta(
        seconds=FAILED_RETRY_STALE_SECONDS + 5
    )
    test_db.commit()

    @contextmanager
    def _session_ctx():
        yield test_db

    with patch("app.tasks.migration_cleanup.get_session", _session_ctx), \
         patch("app.tasks.migration_cleanup.agent_client") as mock_ac:
        mock_ac.is_agent_online = MagicMock(return_value=True)
        mock_ac.destroy_node_on_agent = AsyncMock(return_value={"success": True})
        stats_by_agent = await process_pending_migration_cleanups()

    assert host.id in stats_by_agent
    assert stats_by_agent[host.id]["reclaimed_failed"] == 1
    assert stats_by_agent[host.id]["completed"] == 1
