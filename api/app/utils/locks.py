"""Distributed locking utilities for coordinating concurrent operations.

This module provides Redis-based distributed locks to prevent race conditions
between reconciliation tasks and live link operations.

Also provides database row-level locking helpers for preventing concurrent
modifications to the same records.
"""
from __future__ import annotations

import logging
from threading import Event, Thread
from contextlib import contextmanager
from typing import Generator, TYPE_CHECKING, TypeVar
from uuid import uuid4

import redis

from app.db import get_redis

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from app import models

logger = logging.getLogger(__name__)


# Lock key patterns
LINK_OPS_LOCK_KEY = "link_ops:{lab_id}"

# Lock timeouts (TTL in seconds)
LINK_OPS_LOCK_TTL = 300  # Auto-release after 5 minutes if holder crashes
LINK_OPS_LOCK_RENEW_INTERVAL = max(5, LINK_OPS_LOCK_TTL // 3)

_RELEASE_IF_OWNER_SCRIPT = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("DEL", KEYS[1])
end
return 0
""".strip()

_EXTEND_IF_OWNER_SCRIPT = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("EXPIRE", KEYS[1], tonumber(ARGV[2]))
end
return 0
""".strip()


def _normalize_redis_value(value: object) -> str | None:
    """Normalize Redis response value to a comparable string."""
    if value is None:
        return None
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except Exception:
            return value.decode("utf-8", errors="ignore")
    return str(value)


def _release_if_owner(r: redis.Redis, lock_key: str, lock_token: str) -> int:
    """Delete lock only when the stored owner token matches."""
    try:
        return int(r.eval(_RELEASE_IF_OWNER_SCRIPT, 1, lock_key, lock_token))
    except Exception:
        # Fallback path for minimal/mocked Redis clients without EVAL.
        current = _normalize_redis_value(r.get(lock_key))
        if current != lock_token:
            return 0
        return int(r.delete(lock_key))


def _extend_if_owner(
    r: redis.Redis, lock_key: str, lock_token: str, additional_seconds: int
) -> bool:
    """Extend lock TTL only when the caller still owns the lock."""
    try:
        return bool(
            r.eval(
                _EXTEND_IF_OWNER_SCRIPT,
                1,
                lock_key,
                lock_token,
                additional_seconds,
            )
        )
    except Exception:
        current = _normalize_redis_value(r.get(lock_key))
        if current != lock_token:
            return False
        return bool(r.expire(lock_key, additional_seconds))


def acquire_link_ops_lock(lab_id: str, timeout: int = 5) -> str | None:
    """Try to acquire lock for link operations on a lab.

    Uses Redis SETNX with TTL to implement distributed locking.
    The lock auto-releases after LINK_OPS_LOCK_TTL seconds if the
    holder crashes without releasing it.

    Args:
        lab_id: Lab identifier to lock
        timeout: Not used (kept for API compatibility), lock is non-blocking

    Returns:
        Owner token if lock was acquired, None otherwise
    """
    try:
        r = get_redis()
        lock_key = LINK_OPS_LOCK_KEY.format(lab_id=lab_id)
        lock_token = str(uuid4())
        acquired = r.set(lock_key, lock_token, nx=True, ex=LINK_OPS_LOCK_TTL)
        if acquired:
            logger.debug(f"Acquired link ops lock for lab {lab_id}")
            return lock_token
        else:
            logger.debug(f"Could not acquire link ops lock for lab {lab_id} - already held")
        return None
    except redis.RedisError as e:
        logger.warning(f"Redis error acquiring link ops lock for lab {lab_id}: {e}")
        # Fail closed on lock backend errors to avoid concurrent mutation races.
        return None


def release_link_ops_lock(lab_id: str, lock_token: str | None) -> bool:
    """Release link operations lock.

    Safe to call even if lock wasn't held - operation is idempotent.
    Only releases the lock when lock_token matches the lock owner.

    Args:
        lab_id: Lab identifier to unlock
        lock_token: Owner token returned by acquire_link_ops_lock()

    Returns:
        True if the lock was released, False otherwise
    """
    if not lock_token:
        return False
    try:
        r = get_redis()
        lock_key = LINK_OPS_LOCK_KEY.format(lab_id=lab_id)
        deleted = _release_if_owner(r, lock_key, lock_token)
        if deleted:
            logger.debug(f"Released link ops lock for lab {lab_id}")
            return True
        logger.debug(
            f"Did not release link ops lock for lab {lab_id} "
            f"(not owner or lock expired)"
        )
        return False
    except redis.RedisError as e:
        logger.warning(f"Redis error releasing link ops lock for lab {lab_id}: {e}")
        # Lock will auto-expire via TTL
        return False


def extend_link_ops_lock(
    lab_id: str,
    lock_token: str | None,
    additional_seconds: int = LINK_OPS_LOCK_TTL,
) -> bool:
    """Extend the TTL of an existing link ops lock.

    Useful for long-running operations that need more time.

    Args:
        lab_id: Lab identifier
        lock_token: Owner token returned by acquire_link_ops_lock()
        additional_seconds: Seconds to set as new TTL

    Returns:
        True if lock existed, caller owned it, and TTL was extended
    """
    if not lock_token:
        return False
    try:
        r = get_redis()
        lock_key = LINK_OPS_LOCK_KEY.format(lab_id=lab_id)
        return _extend_if_owner(r, lock_key, lock_token, additional_seconds)
    except redis.RedisError as e:
        logger.warning(f"Redis error extending link ops lock for lab {lab_id}: {e}")
        return False


def _renew_link_ops_lock_until_released(lab_id: str, lock_token: str, stop_event: Event) -> None:
    """Background lease renewal worker for long-running lock scopes."""
    while not stop_event.wait(LINK_OPS_LOCK_RENEW_INTERVAL):
        if not extend_link_ops_lock(lab_id, lock_token):
            logger.warning(
                f"Failed to renew link ops lock for lab {lab_id}; "
                "lock may be contended or expired"
            )
            return


@contextmanager
def link_ops_lock(lab_id: str) -> Generator[bool, None, None]:
    """Context manager for link operations lock.

    Usage:
        with link_ops_lock(lab_id) as acquired:
            if not acquired:
                # Lock held by another process, skip or retry
                return
            # Do link operations

    Yields:
        True if lock was acquired, False if already held by another process
    """
    lock_token = acquire_link_ops_lock(lab_id)
    acquired = bool(lock_token)
    renew_stop = None
    renew_thread = None
    if lock_token:
        renew_stop = Event()
        renew_thread = Thread(
            target=_renew_link_ops_lock_until_released,
            args=(lab_id, lock_token, renew_stop),
            daemon=True,
        )
        renew_thread.start()
    try:
        yield acquired
    finally:
        if renew_stop:
            renew_stop.set()
        if renew_thread:
            renew_thread.join(timeout=1.0)
        if acquired:
            release_link_ops_lock(lab_id, lock_token)


# =============================================================================
# Database Row-Level Locking
# =============================================================================

ModelT = TypeVar("ModelT")


def _get_for_update(
    session: "Session",
    model: type[ModelT],
    *filters,
    skip_locked: bool = False,
) -> ModelT | None:
    """Helper for row-level locking with SELECT ... FOR UPDATE.

    Args:
        skip_locked: If True, use SKIP LOCKED to silently skip rows
            locked by other transactions instead of blocking.
    """
    return (
        session.query(model)
        .filter(*filters)
        .with_for_update(skip_locked=skip_locked)
        .first()
    )


def get_link_state_for_update(
    session: "Session",
    lab_id: str,
    link_name: str,
    skip_locked: bool = False,
) -> "models.LinkState | None":
    """Get LinkState with row-level lock to prevent concurrent modifications.

    Uses SELECT ... FOR UPDATE to acquire an exclusive lock on the row.
    The lock is held until the transaction commits or rolls back.

    This should be used before any operation that modifies a LinkState
    to prevent race conditions between reconciliation and live operations.

    Args:
        session: Database session (must be in a transaction)
        lab_id: Lab identifier
        link_name: Link name to lock
        skip_locked: If True, silently skip rows locked by other transactions

    Returns:
        LinkState record with exclusive lock, or None if not found/skipped
    """
    from app import models

    return _get_for_update(
        session,
        models.LinkState,
        models.LinkState.lab_id == lab_id,
        models.LinkState.link_name == link_name,
        skip_locked=skip_locked,
    )


def get_link_state_by_id_for_update(
    session: "Session",
    link_state_id: str,
    skip_locked: bool = False,
) -> "models.LinkState | None":
    """Get LinkState by ID with row-level lock.

    Args:
        session: Database session (must be in a transaction)
        link_state_id: LinkState record ID
        skip_locked: If True, silently skip rows locked by other transactions

    Returns:
        LinkState record with exclusive lock, or None if not found/skipped
    """
    from app import models

    return _get_for_update(
        session,
        models.LinkState,
        models.LinkState.id == link_state_id,
        skip_locked=skip_locked,
    )


def get_vxlan_tunnel_for_update(
    session: "Session",
    link_state_id: str,
) -> "models.VxlanTunnel | None":
    """Get VxlanTunnel by link_state_id with row-level lock.

    Args:
        session: Database session (must be in a transaction)
        link_state_id: Associated LinkState ID

    Returns:
        VxlanTunnel record with exclusive lock, or None if not found
    """
    from app import models

    return _get_for_update(
        session,
        models.VxlanTunnel,
        models.VxlanTunnel.link_state_id == link_state_id,
    )
