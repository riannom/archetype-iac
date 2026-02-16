"""Distributed locking utilities for coordinating concurrent operations.

This module provides Redis-based distributed locks to prevent race conditions
between reconciliation tasks and live link operations.

Also provides database row-level locking helpers for preventing concurrent
modifications to the same records.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Generator, TYPE_CHECKING, TypeVar

import redis

from app.db import get_redis

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from app import models

logger = logging.getLogger(__name__)


# Lock key patterns
LINK_OPS_LOCK_KEY = "link_ops:{lab_id}"

# Lock timeouts (TTL in seconds)
LINK_OPS_LOCK_TTL = 60  # Auto-release after 60 seconds if holder crashes


def acquire_link_ops_lock(lab_id: str, timeout: int = 5) -> bool:
    """Try to acquire lock for link operations on a lab.

    Uses Redis SETNX with TTL to implement distributed locking.
    The lock auto-releases after LINK_OPS_LOCK_TTL seconds if the
    holder crashes without releasing it.

    Args:
        lab_id: Lab identifier to lock
        timeout: Not used (kept for API compatibility), lock is non-blocking

    Returns:
        True if lock was acquired, False if already held
    """
    try:
        r = get_redis()
        lock_key = LINK_OPS_LOCK_KEY.format(lab_id=lab_id)
        acquired = r.set(lock_key, "1", nx=True, ex=LINK_OPS_LOCK_TTL)
        if acquired:
            logger.debug(f"Acquired link ops lock for lab {lab_id}")
        else:
            logger.debug(f"Could not acquire link ops lock for lab {lab_id} - already held")
        return bool(acquired)
    except redis.RedisError as e:
        logger.warning(f"Redis error acquiring link ops lock for lab {lab_id}: {e}")
        # On Redis error, proceed without lock (better than blocking)
        return True


def release_link_ops_lock(lab_id: str) -> None:
    """Release link operations lock.

    Safe to call even if lock wasn't held - operation is idempotent.

    Args:
        lab_id: Lab identifier to unlock
    """
    try:
        r = get_redis()
        lock_key = LINK_OPS_LOCK_KEY.format(lab_id=lab_id)
        deleted = r.delete(lock_key)
        if deleted:
            logger.debug(f"Released link ops lock for lab {lab_id}")
    except redis.RedisError as e:
        logger.warning(f"Redis error releasing link ops lock for lab {lab_id}: {e}")
        # Lock will auto-expire via TTL


def extend_link_ops_lock(lab_id: str, additional_seconds: int = LINK_OPS_LOCK_TTL) -> bool:
    """Extend the TTL of an existing link ops lock.

    Useful for long-running operations that need more time.

    Args:
        lab_id: Lab identifier
        additional_seconds: Seconds to set as new TTL

    Returns:
        True if lock existed and was extended, False otherwise
    """
    try:
        r = get_redis()
        lock_key = LINK_OPS_LOCK_KEY.format(lab_id=lab_id)
        return r.expire(lock_key, additional_seconds)
    except redis.RedisError as e:
        logger.warning(f"Redis error extending link ops lock for lab {lab_id}: {e}")
        return False


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
    acquired = acquire_link_ops_lock(lab_id)
    try:
        yield acquired
    finally:
        if acquired:
            release_link_ops_lock(lab_id)


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
