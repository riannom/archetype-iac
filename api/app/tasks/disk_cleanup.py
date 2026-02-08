"""Disk cleanup background task.

This task runs periodically to reclaim disk space from:
1. Orphaned ISO upload temp files (.upload_*.partial)
2. Stale in-memory upload/ISO sessions
3. Docker resources on agents (dangling images, build cache)
4. Old job records and webhook delivery logs
5. Orphaned database records (config snapshots, sync jobs, etc.)
6. Orphaned lab workspaces and QCOW2 images
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import agent_client, models
from app.config import settings
from app.db import get_session
from app.tasks.cleanup_base import CleanupResult, CleanupRunner, get_valid_lab_ids

logger = logging.getLogger(__name__)


async def cleanup_orphaned_upload_files() -> CleanupResult:
    """Delete .upload_*.partial files older than the configured threshold."""
    result = CleanupResult(task_name="orphaned_upload_files")
    upload_dir = Path(settings.iso_upload_dir)
    if not upload_dir.exists():
        return result

    from app.routers.iso import _upload_sessions, _upload_lock

    with _upload_lock:
        active_temp_files = {
            session.get("temp_path")
            for session in _upload_sessions.values()
            if session.get("status") == "uploading"
        }

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.cleanup_upload_file_age)
    deleted_bytes = 0

    try:
        for entry in upload_dir.iterdir():
            if not entry.is_file():
                continue
            if not entry.name.startswith(".upload_") or not entry.name.endswith(".partial"):
                continue
            if str(entry) in active_temp_files:
                continue

            try:
                stat = entry.stat()
                mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
                if mtime < cutoff:
                    size = stat.st_size
                    entry.unlink()
                    result.deleted += 1
                    deleted_bytes += size
                    logger.info(f"Deleted orphaned upload file: {entry.name} ({size} bytes)")
            except OSError as e:
                result.errors.append(f"Failed to process {entry.name}: {e}")
                logger.warning(f"Failed to process orphaned file {entry.name}: {e}")
    except Exception as e:
        result.errors.append(f"Error scanning upload directory: {e}")
        logger.error(f"Error scanning upload directory: {e}")

    result.details["deleted_bytes"] = deleted_bytes
    return result


async def cleanup_stale_upload_sessions() -> CleanupResult:
    """Expire stale upload sessions from memory."""
    result = CleanupResult(task_name="stale_upload_sessions")
    from app.routers.iso import _upload_sessions, _upload_lock

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.cleanup_upload_session_age)
    expired_ids = []

    with _upload_lock:
        for upload_id, session in list(_upload_sessions.items()):
            try:
                created_at = session.get("created_at")
                if created_at is None:
                    continue
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
                if session.get("status") == "uploading" and created_at < cutoff:
                    expired_ids.append(upload_id)
            except Exception as e:
                result.errors.append(f"Error checking session {upload_id}: {e}")

        for upload_id in expired_ids:
            session = _upload_sessions.pop(upload_id, None)
            if session:
                result.deleted += 1
                logger.info(f"Expired stale upload session: {upload_id}")
                temp_path = session.get("temp_path")
                if temp_path:
                    try:
                        Path(temp_path).unlink(missing_ok=True)
                    except Exception as e:
                        result.errors.append(f"Failed to delete temp file for {upload_id}: {e}")

    return result


async def cleanup_stale_iso_sessions() -> CleanupResult:
    """Expire stale ISO import sessions from memory."""
    result = CleanupResult(task_name="stale_iso_sessions")
    from app.routers.iso import _sessions, _session_lock

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.cleanup_iso_session_age)
    expired_ids = []

    with _session_lock:
        for session_id, session in list(_sessions.items()):
            try:
                if session.status == "importing":
                    continue
                updated_at = session.updated_at
                if updated_at.tzinfo is None:
                    updated_at = updated_at.replace(tzinfo=timezone.utc)
                if updated_at < cutoff:
                    expired_ids.append(session_id)
            except Exception as e:
                result.errors.append(f"Error checking ISO session {session_id}: {e}")

        for session_id in expired_ids:
            _sessions.pop(session_id, None)
            result.deleted += 1
            logger.info(f"Expired stale ISO session: {session_id}")

    return result


async def cleanup_docker_on_agents() -> CleanupResult:
    """Call /prune-docker on all online agents."""
    result = CleanupResult(task_name="docker_prune")
    if not settings.cleanup_docker_enabled:
        result.details["skipped"] = "disabled"
        return result

    with get_session() as session:
        try:
            agents = (
                session.query(models.Host)
                .filter(models.Host.status == "online")
                .all()
            )
            if not agents:
                return result

            valid_lab_ids = list(get_valid_lab_ids(session))
            total_space_reclaimed = 0

            for agent in agents:
                try:
                    prune_result = await agent_client.prune_docker_on_agent(
                        agent,
                        valid_lab_ids=valid_lab_ids,
                        prune_dangling_images=settings.cleanup_docker_dangling_images,
                        prune_build_cache=settings.cleanup_docker_build_cache,
                        prune_unused_volumes=settings.cleanup_docker_unused_volumes,
                    )
                    if prune_result.get("success", False):
                        result.deleted += 1  # Count agents cleaned
                        space = prune_result.get("space_reclaimed", 0)
                        total_space_reclaimed += space
                        logger.info(
                            f"Docker prune on agent {agent.name}: "
                            f"images={prune_result.get('images_removed', 0)}, "
                            f"cache={prune_result.get('build_cache_removed', 0)}, "
                            f"reclaimed={space} bytes"
                        )
                    else:
                        result.errors.append(f"Agent {agent.name}: {prune_result.get('error', 'unknown error')}")
                except Exception as e:
                    result.errors.append(f"Agent {agent.name}: {e}")
                    logger.warning(f"Failed to prune Docker on agent {agent.name}: {e}")

            result.details["space_reclaimed"] = total_space_reclaimed
        except Exception as e:
            logger.error(f"Error in Docker cleanup: {e}")
            result.errors.append(str(e))

    return result


async def cleanup_old_job_records() -> CleanupResult:
    """Delete old job records in terminal states using bulk query."""
    result = CleanupResult(task_name="old_job_records")
    with get_session() as session:
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=settings.cleanup_job_retention_days)
            result.deleted = (
                session.query(models.Job)
                .filter(
                    models.Job.status.in_(["completed", "failed", "cancelled"]),
                    models.Job.created_at < cutoff,
                )
                .delete(synchronize_session=False)
            )
            session.commit()
            if result.deleted > 0:
                logger.info(f"Deleted {result.deleted} old job records")
        except Exception as e:
            logger.error(f"Error cleaning up old job records: {e}")
            result.errors.append(str(e))
    return result


async def cleanup_old_webhook_deliveries() -> CleanupResult:
    """Delete old webhook delivery records using bulk query."""
    result = CleanupResult(task_name="old_webhook_deliveries")
    with get_session() as session:
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=settings.cleanup_webhook_retention_days)
            result.deleted = (
                session.query(models.WebhookDelivery)
                .filter(models.WebhookDelivery.created_at < cutoff)
                .delete(synchronize_session=False)
            )
            session.commit()
            if result.deleted > 0:
                logger.info(f"Deleted {result.deleted} old webhook delivery records")
        except Exception as e:
            logger.error(f"Error cleaning up old webhook deliveries: {e}")
            result.errors.append(str(e))
    return result


async def cleanup_old_config_snapshots() -> CleanupResult:
    """Delete orphaned and old config snapshots using bulk NOT IN subquery."""
    result = CleanupResult(task_name="old_config_snapshots")
    with get_session() as session:
        try:
            # Bulk delete orphaned snapshots (lab no longer exists)
            orphaned_count = (
                session.query(models.ConfigSnapshot)
                .filter(~models.ConfigSnapshot.lab_id.in_(session.query(models.Lab.id)))
                .delete(synchronize_session=False)
            )

            # Delete old snapshots if retention is configured
            aged_count = 0
            if settings.cleanup_config_snapshot_retention_days > 0:
                cutoff = datetime.now(timezone.utc) - timedelta(days=settings.cleanup_config_snapshot_retention_days)
                aged_count = (
                    session.query(models.ConfigSnapshot)
                    .filter(models.ConfigSnapshot.created_at < cutoff)
                    .delete(synchronize_session=False)
                )

            session.commit()
            result.deleted = orphaned_count + aged_count
            result.details["orphaned"] = orphaned_count
            result.details["aged"] = aged_count
            if result.deleted > 0:
                logger.info(f"Deleted {result.deleted} config snapshots (orphaned={orphaned_count}, aged={aged_count})")
        except Exception as e:
            logger.error(f"Error cleaning up config snapshots: {e}")
            result.errors.append(str(e))
    return result


async def cleanup_old_image_sync_jobs() -> CleanupResult:
    """Delete orphaned and old ImageSyncJob records using bulk NOT IN subquery."""
    result = CleanupResult(task_name="old_image_sync_jobs")
    with get_session() as session:
        try:
            # Bulk delete orphaned jobs (host no longer exists)
            orphaned_count = (
                session.query(models.ImageSyncJob)
                .filter(~models.ImageSyncJob.host_id.in_(session.query(models.Host.id)))
                .delete(synchronize_session=False)
            )

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
            result.deleted = orphaned_count + aged_count
            result.details["orphaned"] = orphaned_count
            result.details["aged"] = aged_count
            if result.deleted > 0:
                logger.info(f"Deleted {result.deleted} image sync jobs (orphaned={orphaned_count}, aged={aged_count})")
        except Exception as e:
            logger.error(f"Error cleaning up image sync jobs: {e}")
            result.errors.append(str(e))
    return result


async def cleanup_old_iso_import_jobs() -> CleanupResult:
    """Delete orphaned and old ISOImportJob records using bulk NOT IN subquery."""
    result = CleanupResult(task_name="old_iso_import_jobs")
    with get_session() as session:
        try:
            # Bulk delete orphaned jobs (user no longer exists, but not null)
            orphaned_count = (
                session.query(models.ISOImportJob)
                .filter(
                    models.ISOImportJob.user_id.isnot(None),
                    ~models.ISOImportJob.user_id.in_(session.query(models.User.id)),
                )
                .delete(synchronize_session=False)
            )

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
            result.deleted = orphaned_count + aged_count
            result.details["orphaned"] = orphaned_count
            result.details["aged"] = aged_count
            if result.deleted > 0:
                logger.info(f"Deleted {result.deleted} ISO import jobs (orphaned={orphaned_count}, aged={aged_count})")
        except Exception as e:
            logger.error(f"Error cleaning up ISO import jobs: {e}")
            result.errors.append(str(e))
    return result


async def cleanup_old_agent_update_jobs() -> CleanupResult:
    """Delete orphaned and old AgentUpdateJob records using bulk NOT IN subquery."""
    result = CleanupResult(task_name="old_agent_update_jobs")
    with get_session() as session:
        try:
            # Bulk delete orphaned jobs (host no longer exists)
            orphaned_count = (
                session.query(models.AgentUpdateJob)
                .filter(~models.AgentUpdateJob.host_id.in_(session.query(models.Host.id)))
                .delete(synchronize_session=False)
            )

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
            result.deleted = orphaned_count + aged_count
            result.details["orphaned"] = orphaned_count
            result.details["aged"] = aged_count
            if result.deleted > 0:
                logger.info(f"Deleted {result.deleted} agent update jobs (orphaned={orphaned_count}, aged={aged_count})")
        except Exception as e:
            logger.error(f"Error cleaning up agent update jobs: {e}")
            result.errors.append(str(e))
    return result


async def cleanup_orphaned_image_host_records() -> CleanupResult:
    """Delete orphaned ImageHost records using bulk NOT IN subquery for hosts."""
    result = CleanupResult(task_name="orphaned_image_host_records")
    from app.image_store import load_manifest

    with get_session() as session:
        try:
            # Bulk delete records where host no longer exists
            host_orphaned = (
                session.query(models.ImageHost)
                .filter(~models.ImageHost.host_id.in_(session.query(models.Host.id)))
                .delete(synchronize_session=False)
            )

            # Delete records where image no longer exists in manifest
            # (manifest is file-based, so we still need to load and filter)
            manifest = load_manifest()
            valid_image_ids = {img.get("id") for img in manifest.get("images", []) if img.get("id")}

            image_orphaned = 0
            if valid_image_ids:
                # Delete records with image IDs not in the manifest
                all_records = session.query(models.ImageHost).all()
                for record in all_records:
                    if record.image_id not in valid_image_ids:
                        session.delete(record)
                        image_orphaned += 1
            else:
                # No valid images at all - all records are orphaned
                image_orphaned = session.query(models.ImageHost).delete(synchronize_session=False)

            session.commit()
            result.deleted = host_orphaned + image_orphaned
            result.details["host_orphaned"] = host_orphaned
            result.details["image_orphaned"] = image_orphaned
            if result.deleted > 0:
                logger.info(f"Deleted {result.deleted} orphaned ImageHost records")
        except Exception as e:
            logger.error(f"Error cleaning up orphaned ImageHost records: {e}")
            result.errors.append(str(e))
    return result


async def cleanup_orphaned_lab_workspaces() -> CleanupResult:
    """Delete lab workspace directories that don't belong to any lab."""
    result = CleanupResult(task_name="orphaned_lab_workspaces")
    if not settings.cleanup_orphaned_workspaces:
        result.details["skipped"] = "disabled"
        return result

    from app.storage import workspace_root

    workspace_dir = workspace_root()
    if not workspace_dir.exists():
        return result

    with get_session() as session:
        try:
            valid_lab_ids = get_valid_lab_ids(session)
            deleted_bytes = 0

            for entry in workspace_dir.iterdir():
                if not entry.is_dir():
                    continue
                if entry.name in ("images", "uploads", ".tmp"):
                    continue
                if entry.name not in valid_lab_ids:
                    try:
                        dir_size = sum(f.stat().st_size for f in entry.rglob("*") if f.is_file())
                        shutil.rmtree(entry)
                        result.deleted += 1
                        deleted_bytes += dir_size
                        logger.info(f"Deleted orphaned lab workspace: {entry.name} ({dir_size} bytes)")
                    except Exception as e:
                        result.errors.append(f"Failed to delete {entry.name}: {e}")
                        logger.warning(f"Failed to delete orphaned workspace {entry.name}: {e}")

            result.details["deleted_bytes"] = deleted_bytes
        except Exception as e:
            logger.error(f"Error cleaning up orphaned workspaces: {e}")
            result.errors.append(str(e))
    return result


async def cleanup_orphaned_qcow2_images() -> CleanupResult:
    """Delete QCOW2 image files that aren't referenced in the manifest."""
    result = CleanupResult(task_name="orphaned_qcow2_images")
    if not settings.cleanup_orphaned_qcow2:
        result.details["skipped"] = "disabled"
        return result

    from app.image_store import image_store_root, load_manifest

    image_dir = image_store_root()
    if not image_dir.exists():
        return result

    try:
        manifest = load_manifest()
        referenced_files = set()
        for image in manifest.get("images", []):
            if image.get("kind") == "qcow2":
                reference = image.get("reference", "")
                filename = image.get("filename", "")
                if reference:
                    referenced_files.add(Path(reference).name)
                if filename:
                    referenced_files.add(filename)

        deleted_bytes = 0
        for entry in image_dir.iterdir():
            if not entry.is_file():
                continue
            if not entry.name.lower().endswith(".qcow2"):
                continue
            if entry.name not in referenced_files:
                try:
                    file_size = entry.stat().st_size
                    entry.unlink()
                    result.deleted += 1
                    deleted_bytes += file_size
                    logger.info(f"Deleted orphaned QCOW2 image: {entry.name} ({file_size} bytes)")
                except Exception as e:
                    result.errors.append(f"Failed to delete {entry.name}: {e}")
                    logger.warning(f"Failed to delete orphaned QCOW2 {entry.name}: {e}")

        result.details["deleted_bytes"] = deleted_bytes
    except Exception as e:
        logger.error(f"Error cleaning up orphaned QCOW2 images: {e}")
        result.errors.append(str(e))
    return result


def get_disk_usage(path: str | Path) -> dict:
    """Get disk usage statistics for a path."""
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
    """Orchestrate all cleanup tasks via CleanupRunner."""
    logger.info("Starting disk cleanup...")

    workspace_path = Path(settings.workspace)
    upload_path = Path(settings.iso_upload_dir)

    before_workspace = get_disk_usage(workspace_path)
    before_upload = get_disk_usage(upload_path)

    runner = CleanupRunner()
    results = await runner.run_tasks([
        cleanup_orphaned_upload_files,
        cleanup_stale_upload_sessions,
        cleanup_stale_iso_sessions,
        cleanup_docker_on_agents,
        cleanup_old_job_records,
        cleanup_old_webhook_deliveries,
        cleanup_old_config_snapshots,
        cleanup_old_image_sync_jobs,
        cleanup_old_iso_import_jobs,
        cleanup_old_agent_update_jobs,
        cleanup_orphaned_image_host_records,
        cleanup_orphaned_lab_workspaces,
        cleanup_orphaned_qcow2_images,
    ])

    after_workspace = get_disk_usage(workspace_path)
    after_upload = get_disk_usage(upload_path)

    total_deleted = sum(r.deleted for r in results)
    total_errors = sum(len(r.errors) for r in results)
    total_duration = sum(r.duration_ms for r in results)

    logger.info(
        f"Disk cleanup completed: "
        f"deleted={total_deleted}, errors={total_errors}, "
        f"duration={total_duration:.0f}ms"
    )

    return {
        "results": [r.to_dict() for r in results],
        "summary": {
            "total_deleted": total_deleted,
            "total_errors": total_errors,
            "total_duration_ms": round(total_duration, 1),
        },
        "disk_usage": {
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
        },
    }


async def disk_cleanup_monitor():
    """Background task to periodically run disk cleanup."""
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
