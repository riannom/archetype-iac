"""Deferred cleanup queue for node migrations."""
from __future__ import annotations

import logging
from datetime import timedelta

from app import agent_client, models
from app.db import get_session
from app.utils.time import utcnow

logger = logging.getLogger(__name__)

# After this many failed destroy attempts, leave the record failed for operator review.
MAX_MIGRATION_CLEANUP_ATTEMPTS = 8
# Reclaim "running" rows older than this to handle worker crashes/restarts.
RUNNING_CLAIM_STALE_SECONDS = 600


def _is_missing_artifact_error(error: str) -> bool:
    value = (error or "").lower()
    if not value:
        return False
    missing_markers = (
        "not found",
        "does not exist",
        "no such",
        "unknown container",
        "domain not found",
        "404",
    )
    return any(marker in value for marker in missing_markers)


def enqueue_node_migration_cleanup(
    session,
    lab_id: str,
    node_name: str,
    old_host_id: str,
    provider: str = "docker",
    reason: str | None = None,
) -> models.NodeMigrationCleanup:
    """Create or refresh a deferred cleanup item for a migrated node."""
    existing = (
        session.query(models.NodeMigrationCleanup)
        .filter(
            models.NodeMigrationCleanup.lab_id == lab_id,
            models.NodeMigrationCleanup.node_name == node_name,
            models.NodeMigrationCleanup.old_host_id == old_host_id,
        )
        .first()
    )
    if existing:
        if existing.status == "failed":
            # Re-queue failed entries if migration retries ask for cleanup again.
            existing.status = "pending"
        existing.provider = provider or existing.provider
        if reason:
            existing.last_error = reason
        return existing

    record = models.NodeMigrationCleanup(
        lab_id=lab_id,
        node_name=node_name,
        old_host_id=old_host_id,
        provider=provider or "docker",
        status="pending",
        last_error=reason,
    )
    session.add(record)
    return record


async def process_pending_migration_cleanups_for_agent(
    session,
    agent: models.Host,
    *,
    limit: int = 25,
) -> dict[str, int]:
    """Process pending migration cleanup rows for one online agent.

    Reclaims stale rows stuck in "running" state before claiming pending work.
    """
    stats = {"reclaimed": 0, "claimed": 0, "completed": 0, "retried": 0, "failed": 0}
    if not agent_client.is_agent_online(agent):
        return stats

    now = utcnow()
    stale_before = now - timedelta(seconds=RUNNING_CLAIM_STALE_SECONDS)
    reclaimed = (
        session.query(models.NodeMigrationCleanup)
        .filter(
            models.NodeMigrationCleanup.old_host_id == agent.id,
            models.NodeMigrationCleanup.status == "running",
            (
                (models.NodeMigrationCleanup.last_attempt_at.is_(None))
                | (models.NodeMigrationCleanup.last_attempt_at < stale_before)
            ),
        )
        .update(
            {
                models.NodeMigrationCleanup.status: "pending",
                models.NodeMigrationCleanup.last_error: (
                    "Reclaimed stale running cleanup after worker interruption"
                ),
            },
            synchronize_session=False,
        )
    )
    if reclaimed:
        session.commit()
        stats["reclaimed"] = reclaimed

    pending_rows = (
        session.query(models.NodeMigrationCleanup)
        .filter(
            models.NodeMigrationCleanup.old_host_id == agent.id,
            models.NodeMigrationCleanup.status == "pending",
        )
        .order_by(models.NodeMigrationCleanup.created_at.asc())
        .with_for_update(skip_locked=True)
        .limit(limit)
        .all()
    )
    if not pending_rows:
        return stats

    work_items: list[tuple[str, str, str, str]] = []
    for row in pending_rows:
        row.status = "running"
        row.attempt_count += 1
        row.last_attempt_at = now
        work_items.append((row.id, row.lab_id, row.node_name, row.provider))
    session.commit()
    stats["claimed"] = len(work_items)

    for cleanup_id, lab_id, node_name, provider in work_items:
        error = ""
        success = False
        try:
            result = await agent_client.destroy_node_on_agent(
                agent,
                lab_id,
                node_name,
                provider=provider or "docker",
            )
            success = bool(result.get("success"))
            error = str(result.get("error") or "")
        except Exception as exc:
            error = str(exc)

        row = session.get(models.NodeMigrationCleanup, cleanup_id)
        if not row:
            continue

        if success or _is_missing_artifact_error(error):
            session.delete(row)
            session.commit()
            stats["completed"] += 1
            continue

        row.status = "pending"
        row.last_error = error or "cleanup failed"
        if row.attempt_count >= MAX_MIGRATION_CLEANUP_ATTEMPTS:
            row.status = "failed"
            stats["failed"] += 1
        else:
            stats["retried"] += 1
        session.commit()

    if stats["claimed"]:
        logger.info(
            "Processed migration cleanup queue on %s: reclaimed=%s claimed=%s completed=%s retried=%s failed=%s",
            agent.name,
            stats["reclaimed"],
            stats["claimed"],
            stats["completed"],
            stats["retried"],
            stats["failed"],
        )
    return stats


async def process_pending_migration_cleanups(limit_per_agent: int = 25) -> dict[str, dict[str, int]]:
    """Drain pending migration cleanup queue for all online agents."""
    by_agent: dict[str, dict[str, int]] = {}
    with get_session() as session:
        host_ids = [
            row[0]
            for row in (
                session.query(models.NodeMigrationCleanup.old_host_id)
                .filter(models.NodeMigrationCleanup.status.in_(["pending", "running"]))
                .distinct()
                .all()
            )
        ]

        for host_id in host_ids:
            agent = session.get(models.Host, host_id)
            if not agent or not agent_client.is_agent_online(agent):
                continue
            by_agent[host_id] = await process_pending_migration_cleanups_for_agent(
                session,
                agent,
                limit=limit_per_agent,
            )
    return by_agent
