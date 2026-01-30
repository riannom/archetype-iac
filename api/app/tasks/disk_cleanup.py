"""Disk cleanup background task.

This task runs periodically to reclaim disk space from:
1. Orphaned ISO upload temp files (.upload_*.partial)
2. Stale in-memory upload/ISO sessions
3. Docker resources on agents (dangling images, build cache)
4. Old job records and webhook delivery logs
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import agent_client, models
from app.config import settings
from app.db import SessionLocal

logger = logging.getLogger(__name__)


async def cleanup_orphaned_upload_files() -> dict:
    """Delete .upload_*.partial files older than the configured threshold.

    These files are created during chunked ISO uploads and should be cleaned
    up when uploads complete or are cancelled. Orphaned files can accumulate
    if uploads are interrupted without proper cleanup.

    Returns:
        Dict with 'deleted_count', 'deleted_bytes', and 'errors' keys
    """
    upload_dir = Path(settings.iso_upload_dir)
    if not upload_dir.exists():
        return {"deleted_count": 0, "deleted_bytes": 0, "errors": []}

    # Import here to avoid circular imports and check active sessions
    from app.routers.iso import _upload_sessions, _upload_lock

    # Get list of active upload temp files (don't delete these)
    with _upload_lock:
        active_temp_files = {
            session.get("temp_path")
            for session in _upload_sessions.values()
            if session.get("status") == "uploading"
        }

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.cleanup_upload_file_age)
    deleted_count = 0
    deleted_bytes = 0
    errors = []

    try:
        for entry in upload_dir.iterdir():
            if not entry.is_file():
                continue
            if not entry.name.startswith(".upload_") or not entry.name.endswith(".partial"):
                continue

            # Skip if this file is actively being uploaded
            if str(entry) in active_temp_files:
                logger.debug(f"Skipping active upload file: {entry.name}")
                continue

            # Check file age
            try:
                stat = entry.stat()
                mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
                if mtime < cutoff:
                    size = stat.st_size
                    entry.unlink()
                    deleted_count += 1
                    deleted_bytes += size
                    logger.info(f"Deleted orphaned upload file: {entry.name} ({size} bytes)")
            except OSError as e:
                errors.append(f"Failed to process {entry.name}: {e}")
                logger.warning(f"Failed to process orphaned file {entry.name}: {e}")

    except Exception as e:
        errors.append(f"Error scanning upload directory: {e}")
        logger.error(f"Error scanning upload directory: {e}")

    return {
        "deleted_count": deleted_count,
        "deleted_bytes": deleted_bytes,
        "errors": errors,
    }


async def cleanup_stale_upload_sessions() -> dict:
    """Expire stale upload sessions from memory.

    Upload sessions are stored in memory. This cleans up sessions that have
    been idle for too long without completing or being cancelled.

    Returns:
        Dict with 'expired_count' and 'errors' keys
    """
    from app.routers.iso import _upload_sessions, _upload_lock

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.cleanup_upload_session_age)
    expired_count = 0
    expired_ids = []
    errors = []

    with _upload_lock:
        for upload_id, session in list(_upload_sessions.items()):
            try:
                created_at = session.get("created_at")
                if created_at is None:
                    continue

                # Handle both timezone-aware and naive datetimes
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)

                # Only expire sessions that are still "uploading" (not completed/failed)
                if session.get("status") == "uploading" and created_at < cutoff:
                    expired_ids.append(upload_id)

            except Exception as e:
                errors.append(f"Error checking session {upload_id}: {e}")

        # Remove expired sessions
        for upload_id in expired_ids:
            session = _upload_sessions.pop(upload_id, None)
            if session:
                expired_count += 1
                logger.info(f"Expired stale upload session: {upload_id}")

                # Clean up temp file if it exists
                temp_path = session.get("temp_path")
                if temp_path:
                    try:
                        Path(temp_path).unlink(missing_ok=True)
                    except Exception as e:
                        errors.append(f"Failed to delete temp file for {upload_id}: {e}")

    return {"expired_count": expired_count, "errors": errors}


async def cleanup_stale_iso_sessions() -> dict:
    """Expire stale ISO import sessions from memory.

    ISO sessions are stored in memory. This cleans up sessions that have been
    idle for too long without completing or being deleted.

    Returns:
        Dict with 'expired_count' and 'errors' keys
    """
    from app.routers.iso import _sessions, _session_lock

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.cleanup_iso_session_age)
    expired_count = 0
    expired_ids = []
    errors = []

    with _session_lock:
        for session_id, session in list(_sessions.items()):
            try:
                # Don't expire sessions that are actively importing
                if session.status == "importing":
                    continue

                # Check session age
                updated_at = session.updated_at
                if updated_at.tzinfo is None:
                    updated_at = updated_at.replace(tzinfo=timezone.utc)

                if updated_at < cutoff:
                    expired_ids.append(session_id)

            except Exception as e:
                errors.append(f"Error checking ISO session {session_id}: {e}")

        # Remove expired sessions
        for session_id in expired_ids:
            _sessions.pop(session_id, None)
            expired_count += 1
            logger.info(f"Expired stale ISO session: {session_id}")

    return {"expired_count": expired_count, "errors": errors}


async def cleanup_docker_on_agents() -> dict:
    """Call /prune-docker on all online agents.

    This requests each agent to prune dangling Docker images, build cache,
    and optionally unused volumes.

    Returns:
        Dict with 'agents_cleaned', 'space_reclaimed', and 'errors' keys
    """
    if not settings.cleanup_docker_enabled:
        return {"agents_cleaned": 0, "space_reclaimed": 0, "errors": [], "skipped": "disabled"}

    session = SessionLocal()
    try:
        # Get all online agents
        agents = (
            session.query(models.Host)
            .filter(models.Host.status == "online")
            .all()
        )

        if not agents:
            return {"agents_cleaned": 0, "space_reclaimed": 0, "errors": []}

        # Get list of valid lab IDs to protect their images
        valid_lab_ids = [
            lab.id for lab in session.query(models.Lab).all()
        ]

        agents_cleaned = 0
        total_space_reclaimed = 0
        errors = []

        for agent in agents:
            try:
                result = await agent_client.prune_docker_on_agent(
                    agent,
                    valid_lab_ids=valid_lab_ids,
                    prune_dangling_images=settings.cleanup_docker_dangling_images,
                    prune_build_cache=settings.cleanup_docker_build_cache,
                    prune_unused_volumes=settings.cleanup_docker_unused_volumes,
                )

                if result.get("success", False):
                    agents_cleaned += 1
                    space = result.get("space_reclaimed", 0)
                    total_space_reclaimed += space
                    logger.info(
                        f"Docker prune on agent {agent.name}: "
                        f"images={result.get('images_removed', 0)}, "
                        f"cache={result.get('build_cache_removed', 0)}, "
                        f"reclaimed={space} bytes"
                    )
                else:
                    errors.append(f"Agent {agent.name}: {result.get('error', 'unknown error')}")

            except Exception as e:
                errors.append(f"Agent {agent.name}: {e}")
                logger.warning(f"Failed to prune Docker on agent {agent.name}: {e}")

        return {
            "agents_cleaned": agents_cleaned,
            "space_reclaimed": total_space_reclaimed,
            "errors": errors,
        }

    finally:
        session.close()


async def cleanup_old_job_records() -> dict:
    """Delete old job records that are in terminal states.

    Jobs in 'completed', 'failed', or 'cancelled' states older than the
    configured retention period are deleted to reclaim database space.

    Returns:
        Dict with 'deleted_count' and 'errors' keys
    """
    session = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=settings.cleanup_job_retention_days)

        # Find old jobs in terminal states
        old_jobs = (
            session.query(models.Job)
            .filter(
                models.Job.status.in_(["completed", "failed", "cancelled"]),
                models.Job.created_at < cutoff,
            )
            .all()
        )

        deleted_count = 0
        errors = []

        for job in old_jobs:
            try:
                session.delete(job)
                deleted_count += 1
            except Exception as e:
                errors.append(f"Failed to delete job {job.id}: {e}")

        if deleted_count > 0:
            session.commit()
            logger.info(f"Deleted {deleted_count} old job records")

        return {"deleted_count": deleted_count, "errors": errors}

    except Exception as e:
        session.rollback()
        logger.error(f"Error cleaning up old job records: {e}")
        return {"deleted_count": 0, "errors": [str(e)]}

    finally:
        session.close()


async def cleanup_old_webhook_deliveries() -> dict:
    """Delete old webhook delivery records.

    Webhook delivery logs older than the configured retention period are
    deleted to reclaim database space.

    Returns:
        Dict with 'deleted_count' and 'errors' keys
    """
    session = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=settings.cleanup_webhook_retention_days)

        # Delete old deliveries
        result = (
            session.query(models.WebhookDelivery)
            .filter(models.WebhookDelivery.created_at < cutoff)
            .delete(synchronize_session=False)
        )

        session.commit()

        if result > 0:
            logger.info(f"Deleted {result} old webhook delivery records")

        return {"deleted_count": result, "errors": []}

    except Exception as e:
        session.rollback()
        logger.error(f"Error cleaning up old webhook deliveries: {e}")
        return {"deleted_count": 0, "errors": [str(e)]}

    finally:
        session.close()


def get_disk_usage(path: str | Path) -> dict:
    """Get disk usage statistics for a path.

    Returns:
        Dict with 'total', 'used', 'free', and 'percent' keys (in bytes/percent)
    """
    try:
        usage = shutil.disk_usage(path)
        return {
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
            "percent": round((usage.used / usage.total) * 100, 1) if usage.total > 0 else 0,
        }
    except Exception as e:
        logger.warning(f"Failed to get disk usage for {path}: {e}")
        return {"total": 0, "used": 0, "free": 0, "percent": 0, "error": str(e)}


async def run_disk_cleanup() -> dict:
    """Orchestrate all cleanup tasks and log results.

    This is the main entry point for disk cleanup, running all cleanup
    tasks and summarizing results.

    Returns:
        Dict with results from all cleanup tasks
    """
    logger.info("Starting disk cleanup...")

    # Get disk usage before cleanup
    workspace_path = Path(settings.workspace)
    upload_path = Path(settings.iso_upload_dir)

    before_workspace = get_disk_usage(workspace_path)
    before_upload = get_disk_usage(upload_path)

    results = {}

    # Run all cleanup tasks
    try:
        results["upload_files"] = await cleanup_orphaned_upload_files()
    except Exception as e:
        logger.error(f"Error in cleanup_orphaned_upload_files: {e}")
        results["upload_files"] = {"error": str(e)}

    try:
        results["upload_sessions"] = await cleanup_stale_upload_sessions()
    except Exception as e:
        logger.error(f"Error in cleanup_stale_upload_sessions: {e}")
        results["upload_sessions"] = {"error": str(e)}

    try:
        results["iso_sessions"] = await cleanup_stale_iso_sessions()
    except Exception as e:
        logger.error(f"Error in cleanup_stale_iso_sessions: {e}")
        results["iso_sessions"] = {"error": str(e)}

    try:
        results["docker"] = await cleanup_docker_on_agents()
    except Exception as e:
        logger.error(f"Error in cleanup_docker_on_agents: {e}")
        results["docker"] = {"error": str(e)}

    try:
        results["jobs"] = await cleanup_old_job_records()
    except Exception as e:
        logger.error(f"Error in cleanup_old_job_records: {e}")
        results["jobs"] = {"error": str(e)}

    try:
        results["webhooks"] = await cleanup_old_webhook_deliveries()
    except Exception as e:
        logger.error(f"Error in cleanup_old_webhook_deliveries: {e}")
        results["webhooks"] = {"error": str(e)}

    # Get disk usage after cleanup
    after_workspace = get_disk_usage(workspace_path)
    after_upload = get_disk_usage(upload_path)

    results["disk_usage"] = {
        "workspace": {
            "before": before_workspace,
            "after": after_workspace,
            "reclaimed": before_workspace.get("used", 0) - after_workspace.get("used", 0),
        },
        "upload": {
            "before": before_upload,
            "after": after_upload,
            "reclaimed": before_upload.get("used", 0) - after_upload.get("used", 0),
        },
    }

    # Log summary
    upload_files_deleted = results.get("upload_files", {}).get("deleted_count", 0)
    upload_sessions_expired = results.get("upload_sessions", {}).get("expired_count", 0)
    iso_sessions_expired = results.get("iso_sessions", {}).get("expired_count", 0)
    docker_agents = results.get("docker", {}).get("agents_cleaned", 0)
    docker_space = results.get("docker", {}).get("space_reclaimed", 0)
    jobs_deleted = results.get("jobs", {}).get("deleted_count", 0)
    webhooks_deleted = results.get("webhooks", {}).get("deleted_count", 0)

    logger.info(
        f"Disk cleanup completed: "
        f"files={upload_files_deleted}, "
        f"upload_sessions={upload_sessions_expired}, "
        f"iso_sessions={iso_sessions_expired}, "
        f"docker_agents={docker_agents} ({docker_space} bytes), "
        f"jobs={jobs_deleted}, "
        f"webhooks={webhooks_deleted}"
    )

    return results


async def disk_cleanup_monitor():
    """Background task to periodically run disk cleanup.

    Runs every cleanup_interval seconds and logs results.
    """
    logger.info(f"Disk cleanup monitor started (interval: {settings.cleanup_interval}s)")

    while True:
        try:
            await asyncio.sleep(settings.cleanup_interval)
            await run_disk_cleanup()

        except asyncio.CancelledError:
            logger.info("Disk cleanup monitor stopped")
            break
        except Exception as e:
            logger.error(f"Error in disk cleanup monitor: {e}")
            # Continue running - don't let one error stop the monitor
