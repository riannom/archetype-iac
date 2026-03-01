"""State reconciliation background task.

This task runs periodically to reconcile the database state with actual
container/VM state on agents. It addresses the fundamental problem of
state drift between the controller's view and reality.

Key scenarios handled:
1. Deploy timeouts - cEOS takes ~400s, VMs take even longer
2. Network partitions - Jobs marked failed even when nodes deployed successfully
3. Stale pending states - Nodes stuck in "pending" with no active job
4. Stale starting states - Labs stuck in "starting" for too long
5. Stuck jobs - Labs with jobs that have exceeded their timeout
6. Link state initialization - Ensure link states exist for deployed labs
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta
from threading import Event, Thread
from uuid import uuid4

import redis

from app import models
from app.config import settings
from app.db import get_redis, get_session
from app.tasks.migration_cleanup import process_pending_migration_cleanups
from app.utils import locks as lock_utils
from app.utils.time import utcnow

# ---------------------------------------------------------------------------
# Re-exports: symbols that were extracted into sub-modules but are still
# imported by external callers via ``from app.tasks.reconciliation import …``
# ---------------------------------------------------------------------------
from app.tasks.reconciliation_refresh import (  # noqa: F401
    refresh_states_from_agents,
    _check_readiness_for_nodes,
)
from app.tasks.reconciliation_db import (  # noqa: F401
    _ensure_link_states_for_lab,
    _backfill_placement_node_ids,
    cleanup_orphaned_node_states,
    _maybe_cleanup_labless_containers,
    _reconcile_single_lab,
)

logger = logging.getLogger(__name__)

# Backward-compat export for tests and existing call sites that patch these symbols.
acquire_link_ops_lock = lock_utils.acquire_link_ops_lock
release_link_ops_lock = lock_utils.release_link_ops_lock
extend_link_ops_lock = lock_utils.extend_link_ops_lock


@contextmanager
def link_ops_lock(lab_id: str):
    """Acquire link ops lock with lease renewal.

    This wrapper intentionally calls module-level acquire/release symbols
    so tests patching app.tasks.reconciliation.acquire_link_ops_lock keep
    working as expected.
    """
    lock_token = acquire_link_ops_lock(lab_id)
    acquired = bool(lock_token)
    renew_stop_event: Event | None = None
    renew_thread: Thread | None = None

    def _renew_until_released() -> None:
        while renew_stop_event and not renew_stop_event.wait(lock_utils.LINK_OPS_LOCK_RENEW_INTERVAL):
            try:
                if not extend_link_ops_lock(lab_id, lock_token):
                    logger.warning(
                        "Failed to renew link ops lock for lab %s; lock may be contended or expired",
                        lab_id,
                    )
                    return
            except Exception as e:  # pragma: no cover - defensive
                logger.warning("Error renewing link ops lock for lab %s: %s", lab_id, e)
                return

    if acquired:
        renew_stop_event = Event()
        renew_thread = Thread(target=_renew_until_released, daemon=True)
        renew_thread.start()

    try:
        yield acquired
    finally:
        if renew_stop_event:
            renew_stop_event.set()
        if renew_thread:
            renew_thread.join(timeout=1.0)
        if acquired:
            release_link_ops_lock(lab_id, lock_token)

# Rate-limit endpoint repairs: lab_id -> last repair attempt time
_last_endpoint_repair: dict[str, datetime] = {}
ENDPOINT_REPAIR_COOLDOWN = timedelta(minutes=2)
_RECONCILIATION_RENEW_INTERVAL_SECONDS = 20


def _set_agent_error(agent: models.Host, error_message: str) -> None:
    """Set or update an agent's error state.

    If this is a new error (agent.last_error was None), sets error_since
    to the current time. Always updates last_error to the new message.

    Args:
        agent: Host model instance
        error_message: Error message to persist
    """
    if agent.last_error is None:
        agent.error_since = utcnow()
    agent.last_error = error_message
    logger.warning(f"Agent {agent.name} error: {error_message}")


def _clear_agent_error(agent: models.Host) -> None:
    """Clear an agent's error state.

    Clears both last_error and error_since when the agent successfully
    responds to queries.

    Args:
        agent: Host model instance
    """
    if agent.last_error is not None:
        logger.info(f"Agent {agent.name} error cleared (was: {agent.last_error})")
        agent.last_error = None
        agent.error_since = None


@contextmanager
def reconciliation_lock(lab_id: str, timeout: int = 60):
    """Acquire a distributed lock before reconciling a lab.

    This prevents multiple reconciliation tasks from running concurrently
    for the same lab, and prevents reconciliation from interfering with
    active jobs.

    Args:
        lab_id: Lab identifier to lock
        timeout: Lock TTL in seconds (auto-releases if holder crashes)

    Yields:
        True if lock was acquired, False if another process holds it.
    """
    lock_key = f"reconcile_lock:{lab_id}"
    r = get_redis()
    lock_token = str(uuid4())
    lock_acquired = False
    renew_stop_event: Event | None = None
    renew_thread: Thread | None = None

    def _renew_until_released() -> None:
        while renew_stop_event and not renew_stop_event.wait(_RECONCILIATION_RENEW_INTERVAL_SECONDS):
            try:
                # Atomic check-and-extend via Redis Lua script
                renewed = bool(
                    r.eval(  # noqa: S307 -- Redis Lua eval, not Python eval
                        "if redis.call('GET', KEYS[1]) == ARGV[1] then "
                        "return redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2])) else return 0 end",
                        1,
                        lock_key,
                        lock_token,
                        timeout,
                    )
                )
            except redis.RedisError as e:
                logger.warning(
                    "Redis error renewing reconciliation lock for lab %s: %s",
                    lab_id,
                    e,
                )
                return
            if not renewed:
                logger.warning(
                    "Failed to renew reconciliation lock for lab %s; lock may have expired or moved",
                    lab_id,
                )
                return

    try:
        # Try to acquire lock with NX (only if not exists) and TTL
        lock_acquired = bool(r.set(lock_key, lock_token, nx=True, ex=timeout))
        if not lock_acquired:
            logger.debug(f"Could not acquire reconciliation lock for lab {lab_id}")
            yield False
            return

        renew_stop_event = Event()
        renew_thread = Thread(target=_renew_until_released, daemon=True)
        renew_thread.start()
        yield True
    except redis.RedisError as e:
        logger.warning(f"Redis error acquiring lock for lab {lab_id}: {e}")
        # Fail closed so we never run concurrent reconciliation without locking.
        yield False
    finally:
        if renew_stop_event:
            renew_stop_event.set()
        if renew_thread:
            renew_thread.join(timeout=1.0)
        if lock_acquired:
            try:
                # Atomic check-and-delete via Redis Lua script
                r.eval(  # noqa: S307 -- Redis Lua eval, not Python eval
                    "if redis.call('GET', KEYS[1]) == ARGV[1] then "
                    "return redis.call('DEL', KEYS[1]) else return 0 end",
                    1,
                    lock_key,
                    lock_token,
                )
            except redis.RedisError:
                pass  # Lock will auto-expire via TTL


async def state_reconciliation_monitor():
    """Background task to periodically reconcile state.

    Runs every reconciliation_interval seconds and queries agents
    for actual container status, updating the database to match reality.
    """
    interval = settings.get_interval("reconciliation")
    logger.info(
        f"State reconciliation monitor started "
        f"(interval: {interval}s)"
    )

    while True:
        try:
            await asyncio.sleep(interval)
            await refresh_states_from_agents()
            await process_pending_migration_cleanups()
        except asyncio.CancelledError:
            logger.info("State reconciliation monitor stopped")
            break
        except Exception as e:
            logger.error(f"Error in state reconciliation monitor: {e}")
            # Continue running - don't let one error stop the monitor


async def reconcile_managed_interfaces():
    """Check managed interface status against actual host state.

    For each AgentManagedInterface record, queries the agent for actual
    interface state and updates sync_status/current_mtu accordingly.
    Runs less frequently than state reconciliation (called externally).
    """
    from app import agent_client  # noqa: F811 -- intentional local re-import

    with get_session() as session:
        interfaces = session.query(models.AgentManagedInterface).all()
        if not interfaces:
            return

        # Group by host
        by_host: dict[str, list] = {}
        for iface in interfaces:
            by_host.setdefault(iface.host_id, []).append(iface)

        for host_id, ifaces in by_host.items():
            agent = session.get(models.Host, host_id)
            if not agent or not agent_client.is_agent_online(agent):
                continue

            try:
                # Get interface details from agent
                details = await agent_client.get_agent_interface_details(agent)
                if not details or not details.get("interfaces"):
                    continue

                actual_interfaces = {i["name"]: i for i in details["interfaces"]}

                for iface in ifaces:
                    actual = actual_interfaces.get(iface.name)
                    if actual:
                        iface.current_mtu = actual.get("mtu")
                        iface.is_up = actual.get("state", "").lower() == "up"
                        if iface.current_mtu == iface.desired_mtu and iface.is_up:
                            iface.sync_status = "synced"
                            iface.sync_error = None
                        else:
                            iface.sync_status = "mismatch"
                    else:
                        iface.sync_status = "mismatch"
                        iface.is_up = False
                    iface.last_sync_at = utcnow()

                session.commit()
            except Exception as e:
                session.rollback()
                logger.warning(f"Failed to reconcile interfaces for host {host_id}: {e}")
