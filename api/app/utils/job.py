"""Job utility functions."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.config import settings


def get_job_timeout(action: str) -> int:
    """Get timeout in seconds based on job action.

    Args:
        action: Job action string (e.g., "up", "down", "sync:node:xxx", "node:start:xxx")

    Returns:
        Timeout in seconds for the given action type.
    """
    if action == "up":
        return settings.job_timeout_deploy
    elif action == "down":
        return settings.job_timeout_destroy
    elif action.startswith("sync:"):
        return settings.job_timeout_sync
    elif action.startswith("node:"):
        return settings.job_timeout_node
    else:
        # Default to longest timeout for unknown actions
        return settings.job_timeout_deploy


def get_job_timeout_at(action: str, started_at: datetime | None) -> datetime | None:
    """Calculate when a job should timeout.

    Args:
        action: Job action string
        started_at: When the job started (None if not started yet)

    Returns:
        Datetime when job should timeout, or None if not started.
    """
    if started_at is None:
        return None

    timeout_seconds = get_job_timeout(action)
    # Ensure started_at is timezone-aware
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    return started_at + timedelta(seconds=timeout_seconds)


def is_job_stuck(
    action: str,
    status: str,
    started_at: datetime | None,
    created_at: datetime | None = None,
) -> bool:
    """Check if a job is stuck (past its expected runtime).

    A job is considered stuck if:
    - It's in 'running' state and past its timeout
    - It's in 'queued' state for more than 2 minutes without starting

    Args:
        action: Job action string
        status: Current job status
        started_at: When the job started
        created_at: When the job was created (for queued jobs)

    Returns:
        True if the job appears to be stuck.
    """
    now = datetime.now(timezone.utc)

    if status == "running" and started_at is not None:
        # Ensure started_at is timezone-aware
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        timeout_at = get_job_timeout_at(action, started_at)
        if timeout_at and now > timeout_at:
            return True

    elif status == "queued" and created_at is not None:
        # Queued job not started within 2 minutes is considered stuck
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        queued_timeout = created_at + timedelta(minutes=2)
        if now > queued_timeout:
            return True

    return False


def is_job_within_timeout(
    action: str,
    status: str,
    started_at: datetime | None,
    created_at: datetime | None = None,
) -> bool:
    """Check if a job is still within its expected runtime window.

    This is the inverse of is_job_stuck but also considers the grace period.
    Used by reconciliation to decide whether to skip a lab.

    Args:
        action: Job action string
        status: Current job status
        started_at: When the job started
        created_at: When the job was created

    Returns:
        True if the job should still be given time to complete.
    """
    now = datetime.now(timezone.utc)

    if status not in ("queued", "running"):
        return False

    if status == "running" and started_at is not None:
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        timeout_seconds = get_job_timeout(action)
        # Add grace period for reconciliation decisions
        total_wait = timeout_seconds + settings.job_stuck_grace_period
        deadline = started_at + timedelta(seconds=total_wait)
        return now <= deadline

    elif status == "queued" and created_at is not None:
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        # Queued jobs get 2 minutes + grace period
        deadline = created_at + timedelta(minutes=2, seconds=settings.job_stuck_grace_period)
        return now <= deadline

    return True  # Default to giving benefit of doubt
