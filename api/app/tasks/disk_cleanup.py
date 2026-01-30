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


async def cleanup_old_config_snapshots() -> dict:
    """Delete orphaned and old configuration snapshots.

    Two types of cleanup:
    1. Orphaned: Snapshots for labs that no longer exist (deleted immediately)
    2. Old: Snapshots older than retention period (configurable)

    Returns:
        Dict with 'deleted_count', 'orphaned_count', and 'errors' keys
    """
    session = SessionLocal()
    try:
        orphaned_count = 0
        aged_count = 0
        errors = []

        # Get valid lab IDs
        valid_lab_ids = {lab.id for lab in session.query(models.Lab).all()}

        # Delete orphaned snapshots (lab no longer exists)
        all_snapshots = session.query(models.ConfigSnapshot).all()
        for snapshot in all_snapshots:
            if snapshot.lab_id not in valid_lab_ids:
                try:
                    session.delete(snapshot)
                    orphaned_count += 1
                except Exception as e:
                    errors.append(f"Failed to delete orphaned snapshot {snapshot.id}: {e}")

        # Delete old snapshots if retention is configured
        if settings.cleanup_config_snapshot_retention_days > 0:
            cutoff = datetime.now(timezone.utc) - timedelta(days=settings.cleanup_config_snapshot_retention_days)
            aged_count = (
                session.query(models.ConfigSnapshot)
                .filter(models.ConfigSnapshot.created_at < cutoff)
                .delete(synchronize_session=False)
            )

        session.commit()

        total = orphaned_count + aged_count
        if total > 0:
            logger.info(f"Deleted {total} config snapshots (orphaned={orphaned_count}, aged={aged_count})")

        return {"deleted_count": total, "orphaned_count": orphaned_count, "aged_count": aged_count, "errors": errors}

    except Exception as e:
        session.rollback()
        logger.error(f"Error cleaning up config snapshots: {e}")
        return {"deleted_count": 0, "orphaned_count": 0, "aged_count": 0, "errors": [str(e)]}

    finally:
        session.close()


async def cleanup_old_image_sync_jobs() -> dict:
    """Delete orphaned and old ImageSyncJob records.

    Two types of cleanup:
    1. Orphaned: Jobs for hosts that no longer exist (deleted immediately)
    2. Old: Terminal-state jobs older than retention period

    Returns:
        Dict with 'deleted_count', 'orphaned_count', 'aged_count', and 'errors' keys
    """
    session = SessionLocal()
    try:
        orphaned_count = 0
        aged_count = 0
        errors = []

        # Get valid host IDs
        valid_host_ids = {host.id for host in session.query(models.Host).all()}

        # Delete orphaned jobs (host no longer exists)
        all_jobs = session.query(models.ImageSyncJob).all()
        for job in all_jobs:
            if job.host_id not in valid_host_ids:
                try:
                    session.delete(job)
                    orphaned_count += 1
                except Exception as e:
                    errors.append(f"Failed to delete orphaned sync job {job.id}: {e}")

        # Delete old jobs in terminal states
        cutoff = datetime.now(timezone.utc) - timedelta(days=settings.cleanup_image_sync_job_retention_days)
        aged_count = (
            session.query(models.ImageSyncJob)
            .filter(
                models.ImageSyncJob.status.in_(["completed", "failed", "cancelled"]),
                models.ImageSyncJob.created_at < cutoff,
            )
            .delete(synchronize_session=False)
        )

        session.commit()

        total = orphaned_count + aged_count
        if total > 0:
            logger.info(f"Deleted {total} image sync jobs (orphaned={orphaned_count}, aged={aged_count})")

        return {"deleted_count": total, "orphaned_count": orphaned_count, "aged_count": aged_count, "errors": errors}

    except Exception as e:
        session.rollback()
        logger.error(f"Error cleaning up image sync jobs: {e}")
        return {"deleted_count": 0, "orphaned_count": 0, "aged_count": 0, "errors": [str(e)]}

    finally:
        session.close()


async def cleanup_old_iso_import_jobs() -> dict:
    """Delete orphaned and old ISOImportJob records.

    Two types of cleanup:
    1. Orphaned: Jobs for users that no longer exist (deleted immediately)
    2. Old: Terminal-state jobs older than retention period

    These records can have large TEXT fields (manifest_json, image_progress).

    Returns:
        Dict with 'deleted_count', 'orphaned_count', 'aged_count', and 'errors' keys
    """
    session = SessionLocal()
    try:
        orphaned_count = 0
        aged_count = 0
        errors = []

        # Get valid user IDs
        valid_user_ids = {user.id for user in session.query(models.User).all()}

        # Delete orphaned jobs (user no longer exists, but not null)
        all_jobs = session.query(models.ISOImportJob).filter(
            models.ISOImportJob.user_id.isnot(None)
        ).all()
        for job in all_jobs:
            if job.user_id not in valid_user_ids:
                try:
                    session.delete(job)
                    orphaned_count += 1
                except Exception as e:
                    errors.append(f"Failed to delete orphaned ISO job {job.id}: {e}")

        # Delete old jobs in terminal states
        cutoff = datetime.now(timezone.utc) - timedelta(days=settings.cleanup_iso_import_job_retention_days)
        aged_count = (
            session.query(models.ISOImportJob)
            .filter(
                models.ISOImportJob.status.in_(["completed", "failed", "cancelled"]),
                models.ISOImportJob.created_at < cutoff,
            )
            .delete(synchronize_session=False)
        )

        session.commit()

        total = orphaned_count + aged_count
        if total > 0:
            logger.info(f"Deleted {total} ISO import jobs (orphaned={orphaned_count}, aged={aged_count})")

        return {"deleted_count": total, "orphaned_count": orphaned_count, "aged_count": aged_count, "errors": errors}

    except Exception as e:
        session.rollback()
        logger.error(f"Error cleaning up ISO import jobs: {e}")
        return {"deleted_count": 0, "orphaned_count": 0, "aged_count": 0, "errors": [str(e)]}

    finally:
        session.close()


async def cleanup_old_agent_update_jobs() -> dict:
    """Delete orphaned and old AgentUpdateJob records.

    Two types of cleanup:
    1. Orphaned: Jobs for hosts that no longer exist (deleted immediately)
    2. Old: Terminal-state jobs older than retention period

    Returns:
        Dict with 'deleted_count', 'orphaned_count', 'aged_count', and 'errors' keys
    """
    session = SessionLocal()
    try:
        orphaned_count = 0
        aged_count = 0
        errors = []

        # Get valid host IDs
        valid_host_ids = {host.id for host in session.query(models.Host).all()}

        # Delete orphaned jobs (host no longer exists)
        all_jobs = session.query(models.AgentUpdateJob).all()
        for job in all_jobs:
            if job.host_id not in valid_host_ids:
                try:
                    session.delete(job)
                    orphaned_count += 1
                except Exception as e:
                    errors.append(f"Failed to delete orphaned update job {job.id}: {e}")

        # Delete old jobs in terminal states
        cutoff = datetime.now(timezone.utc) - timedelta(days=settings.cleanup_agent_update_job_retention_days)
        aged_count = (
            session.query(models.AgentUpdateJob)
            .filter(
                models.AgentUpdateJob.status.in_(["completed", "failed"]),
                models.AgentUpdateJob.created_at < cutoff,
            )
            .delete(synchronize_session=False)
        )

        session.commit()

        total = orphaned_count + aged_count
        if total > 0:
            logger.info(f"Deleted {total} agent update jobs (orphaned={orphaned_count}, aged={aged_count})")

        return {"deleted_count": total, "orphaned_count": orphaned_count, "aged_count": aged_count, "errors": errors}

    except Exception as e:
        session.rollback()
        logger.error(f"Error cleaning up agent update jobs: {e}")
        return {"deleted_count": 0, "orphaned_count": 0, "aged_count": 0, "errors": [str(e)]}

    finally:
        session.close()


async def cleanup_orphaned_image_host_records() -> dict:
    """Delete orphaned ImageHost records.

    ImageHost records track which images exist on which agents. This cleans up
    records where either the host or the image reference no longer exists.

    Returns:
        Dict with 'deleted_count' and 'errors' keys
    """
    from app.image_store import load_manifest

    session = SessionLocal()
    try:
        deleted_count = 0
        errors = []

        # Get valid host IDs
        valid_host_ids = {host.id for host in session.query(models.Host).all()}

        # Get valid image IDs from manifest
        manifest = load_manifest()
        valid_image_ids = {img.get("id") for img in manifest.get("images", []) if img.get("id")}

        # Check all ImageHost records
        all_records = session.query(models.ImageHost).all()
        for record in all_records:
            is_orphaned = False

            # Check if host still exists
            if record.host_id not in valid_host_ids:
                is_orphaned = True

            # Check if image still exists in manifest
            if record.image_id not in valid_image_ids:
                is_orphaned = True

            if is_orphaned:
                try:
                    session.delete(record)
                    deleted_count += 1
                except Exception as e:
                    errors.append(f"Failed to delete orphaned ImageHost {record.id}: {e}")

        session.commit()

        if deleted_count > 0:
            logger.info(f"Deleted {deleted_count} orphaned ImageHost records")

        return {"deleted_count": deleted_count, "errors": errors}

    except Exception as e:
        session.rollback()
        logger.error(f"Error cleaning up orphaned ImageHost records: {e}")
        return {"deleted_count": 0, "errors": [str(e)]}

    finally:
        session.close()


async def cleanup_orphaned_lab_workspaces() -> dict:
    """Delete lab workspace directories that don't belong to any lab.

    When labs are deleted, their workspace directories may remain. This
    function identifies and removes directories that don't correspond to
    any lab in the database.

    Returns:
        Dict with 'deleted_count', 'deleted_bytes', and 'errors' keys
    """
    if not settings.cleanup_orphaned_workspaces:
        return {"deleted_count": 0, "deleted_bytes": 0, "errors": [], "skipped": "disabled"}

    from app.storage import workspace_root

    workspace_dir = workspace_root()
    if not workspace_dir.exists():
        return {"deleted_count": 0, "deleted_bytes": 0, "errors": []}

    session = SessionLocal()
    try:
        # Get all valid lab IDs from database
        valid_lab_ids = {lab.id for lab in session.query(models.Lab).all()}

        deleted_count = 0
        deleted_bytes = 0
        errors = []

        # Scan workspace directory for subdirectories
        for entry in workspace_dir.iterdir():
            if not entry.is_dir():
                continue

            # Skip special directories (images, uploads, etc.)
            if entry.name in ("images", "uploads", ".tmp"):
                continue

            # Check if this directory corresponds to a valid lab
            if entry.name not in valid_lab_ids:
                try:
                    # Calculate directory size before deletion
                    dir_size = sum(f.stat().st_size for f in entry.rglob("*") if f.is_file())

                    # Remove the orphaned workspace
                    shutil.rmtree(entry)
                    deleted_count += 1
                    deleted_bytes += dir_size
                    logger.info(f"Deleted orphaned lab workspace: {entry.name} ({dir_size} bytes)")

                except Exception as e:
                    errors.append(f"Failed to delete {entry.name}: {e}")
                    logger.warning(f"Failed to delete orphaned workspace {entry.name}: {e}")

        return {
            "deleted_count": deleted_count,
            "deleted_bytes": deleted_bytes,
            "errors": errors,
        }

    except Exception as e:
        logger.error(f"Error cleaning up orphaned workspaces: {e}")
        return {"deleted_count": 0, "deleted_bytes": 0, "errors": [str(e)]}

    finally:
        session.close()


async def cleanup_orphaned_qcow2_images() -> dict:
    """Delete QCOW2 image files that aren't referenced in the manifest.

    When images are removed from the manifest, the actual files may remain.
    This function identifies and removes QCOW2 files not in the manifest.

    Returns:
        Dict with 'deleted_count', 'deleted_bytes', and 'errors' keys
    """
    if not settings.cleanup_orphaned_qcow2:
        return {"deleted_count": 0, "deleted_bytes": 0, "errors": [], "skipped": "disabled"}

    from app.image_store import image_store_root, load_manifest

    image_dir = image_store_root()
    if not image_dir.exists():
        return {"deleted_count": 0, "deleted_bytes": 0, "errors": []}

    try:
        # Get all QCOW2 references from manifest
        manifest = load_manifest()
        referenced_files = set()

        for image in manifest.get("images", []):
            if image.get("kind") == "qcow2":
                # Reference could be full path or just filename
                reference = image.get("reference", "")
                filename = image.get("filename", "")

                # Extract just the filename from reference if it's a path
                if reference:
                    referenced_files.add(Path(reference).name)
                if filename:
                    referenced_files.add(filename)

        deleted_count = 0
        deleted_bytes = 0
        errors = []

        # Scan for QCOW2 files not in manifest
        for entry in image_dir.iterdir():
            if not entry.is_file():
                continue
            if not entry.name.lower().endswith(".qcow2"):
                continue

            # Check if this file is referenced
            if entry.name not in referenced_files:
                try:
                    file_size = entry.stat().st_size
                    entry.unlink()
                    deleted_count += 1
                    deleted_bytes += file_size
                    logger.info(f"Deleted orphaned QCOW2 image: {entry.name} ({file_size} bytes)")

                except Exception as e:
                    errors.append(f"Failed to delete {entry.name}: {e}")
                    logger.warning(f"Failed to delete orphaned QCOW2 {entry.name}: {e}")

        return {
            "deleted_count": deleted_count,
            "deleted_bytes": deleted_bytes,
            "errors": errors,
        }

    except Exception as e:
        logger.error(f"Error cleaning up orphaned QCOW2 images: {e}")
        return {"deleted_count": 0, "deleted_bytes": 0, "errors": [str(e)]}


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

    # Database record cleanup (orphaned + aged)
    try:
        results["config_snapshots"] = await cleanup_old_config_snapshots()
    except Exception as e:
        logger.error(f"Error in cleanup_old_config_snapshots: {e}")
        results["config_snapshots"] = {"error": str(e)}

    try:
        results["image_sync_jobs"] = await cleanup_old_image_sync_jobs()
    except Exception as e:
        logger.error(f"Error in cleanup_old_image_sync_jobs: {e}")
        results["image_sync_jobs"] = {"error": str(e)}

    try:
        results["iso_import_jobs"] = await cleanup_old_iso_import_jobs()
    except Exception as e:
        logger.error(f"Error in cleanup_old_iso_import_jobs: {e}")
        results["iso_import_jobs"] = {"error": str(e)}

    try:
        results["agent_update_jobs"] = await cleanup_old_agent_update_jobs()
    except Exception as e:
        logger.error(f"Error in cleanup_old_agent_update_jobs: {e}")
        results["agent_update_jobs"] = {"error": str(e)}

    try:
        results["image_host_records"] = await cleanup_orphaned_image_host_records()
    except Exception as e:
        logger.error(f"Error in cleanup_orphaned_image_host_records: {e}")
        results["image_host_records"] = {"error": str(e)}

    # Filesystem cleanup (orphaned workspaces and images)
    try:
        results["orphaned_workspaces"] = await cleanup_orphaned_lab_workspaces()
    except Exception as e:
        logger.error(f"Error in cleanup_orphaned_lab_workspaces: {e}")
        results["orphaned_workspaces"] = {"error": str(e)}

    try:
        results["orphaned_qcow2"] = await cleanup_orphaned_qcow2_images()
    except Exception as e:
        logger.error(f"Error in cleanup_orphaned_qcow2_images: {e}")
        results["orphaned_qcow2"] = {"error": str(e)}

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

    # New cleanup results
    config_snapshots = results.get("config_snapshots", {}).get("deleted_count", 0)
    image_sync_jobs = results.get("image_sync_jobs", {}).get("deleted_count", 0)
    iso_import_jobs = results.get("iso_import_jobs", {}).get("deleted_count", 0)
    agent_update_jobs = results.get("agent_update_jobs", {}).get("deleted_count", 0)
    image_host_records = results.get("image_host_records", {}).get("deleted_count", 0)
    orphaned_workspaces = results.get("orphaned_workspaces", {}).get("deleted_count", 0)
    orphaned_workspace_bytes = results.get("orphaned_workspaces", {}).get("deleted_bytes", 0)
    orphaned_qcow2 = results.get("orphaned_qcow2", {}).get("deleted_count", 0)
    orphaned_qcow2_bytes = results.get("orphaned_qcow2", {}).get("deleted_bytes", 0)

    # Calculate total database records cleaned
    db_records_deleted = (
        jobs_deleted + webhooks_deleted + config_snapshots +
        image_sync_jobs + iso_import_jobs + agent_update_jobs + image_host_records
    )

    # Calculate total filesystem space reclaimed
    fs_bytes_reclaimed = (
        results.get("upload_files", {}).get("deleted_bytes", 0) +
        orphaned_workspace_bytes + orphaned_qcow2_bytes + docker_space
    )

    logger.info(
        f"Disk cleanup completed: "
        f"db_records={db_records_deleted}, "
        f"fs_reclaimed={fs_bytes_reclaimed} bytes, "
        f"workspaces={orphaned_workspaces}, "
        f"qcow2={orphaned_qcow2}, "
        f"docker_agents={docker_agents}"
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
