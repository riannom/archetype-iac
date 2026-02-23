from __future__ import annotations

from redis import Redis
from rq import Queue
from sqlalchemy import or_

from app.config import settings
from app.db import get_session
from app.models import Job

_redis_conn: Redis | None = None
_queue: Queue | None = None


def get_redis_conn() -> Redis:
    """Lazy Redis connection — avoids module-level connect that hangs in CI."""
    global _redis_conn
    if _redis_conn is None:
        _redis_conn = Redis.from_url(settings.redis_url)
    return _redis_conn


def get_queue() -> Queue:
    """Lazy RQ queue — avoids module-level connect that hangs in CI."""
    global _queue
    if _queue is None:
        _queue = Queue("archetype", connection=get_redis_conn())
    return _queue


# Actions that conflict with each other for concurrent execution
CONFLICTING_ACTIONS = {
    "up": ["up", "down", "sync"],
    "down": ["up", "down", "sync"],
    "sync": ["up", "down"],
}


def has_conflicting_job(lab_id: str, action: str, session=None) -> tuple[bool, str | None]:
    """Check if lab has a running/queued job that conflicts with new action.

    Args:
        lab_id: The lab ID to check
        action: The action being attempted (up, down, sync, etc.)
        session: Optional SQLAlchemy session to use. If provided, uses that
            session (important for transactional consistency with SELECT FOR UPDATE).
            Otherwise creates a new session via get_session().

    Returns:
        Tuple of (has_conflict, conflicting_action_name)
    """
    conflicting_actions = CONFLICTING_ACTIONS.get(action, [])
    if not conflicting_actions:
        return False, None

    # Build OR conditions for both exact and prefix matching.
    # Sync jobs use formats like sync:node:xxx, sync:lab:xxx, sync:batch:N
    # so we need to match both "sync" exactly and "sync:*" prefixes.
    conditions = []
    for action_name in conflicting_actions:
        conditions.append(Job.action == action_name)
        conditions.append(Job.action.like(f"{action_name}:%"))

    if session is not None:
        active_job = (
            session.query(Job)
            .filter(
                Job.lab_id == lab_id,
                Job.status.in_(["queued", "running"]),
                or_(*conditions),
            )
            .first()
        )
        return (True, active_job.action) if active_job else (False, None)

    with get_session() as s:
        active_job = (
            s.query(Job)
            .filter(
                Job.lab_id == lab_id,
                Job.status.in_(["queued", "running"]),
                or_(*conditions),
            )
            .first()
        )
        return (True, active_job.action) if active_job else (False, None)
