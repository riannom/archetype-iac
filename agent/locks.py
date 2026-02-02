"""Redis-based distributed deploy locks.

This module provides crash-safe deploy locks using Redis. Unlike in-memory
asyncio.Lock objects, these locks:
1. Survive agent restarts (via Redis persistence)
2. Auto-expire via TTL if agent crashes mid-deploy
3. Can be force-released externally for stuck recovery
4. Provide visibility into lock state across the cluster

The lock implementation uses Redis SET NX with expiry for atomic lock acquisition.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import redis.asyncio as redis

logger = logging.getLogger(__name__)


class LockAcquisitionTimeout(Exception):
    """Raised when lock cannot be acquired within timeout."""

    def __init__(self, lab_id: str, timeout: float):
        self.lab_id = lab_id
        self.timeout = timeout
        super().__init__(f"Could not acquire deploy lock for lab {lab_id} within {timeout}s")


class DeployLockManager:
    """Manages Redis-based deploy locks with TTL.

    This class provides distributed locking for deploy operations using Redis.
    Each lock has a TTL that ensures automatic release if the holding process
    crashes without cleanup.

    Attributes:
        redis: Redis client instance
        lock_ttl: Default TTL for locks in seconds
        agent_id: Agent identifier for lock ownership tracking
    """

    def __init__(
        self,
        redis_url: str,
        lock_ttl: int = 960,
        agent_id: str = "",
    ):
        """Initialize the lock manager.

        Args:
            redis_url: Redis connection URL
            lock_ttl: Lock TTL in seconds (should be slightly longer than deploy_timeout)
            agent_id: Agent identifier for lock ownership
        """
        self.redis_url = redis_url
        self.lock_ttl = lock_ttl
        self.agent_id = agent_id
        self._redis: redis.Redis | None = None
        self._local_locks: dict[str, asyncio.Lock] = {}

    async def _get_redis(self) -> redis.Redis:
        """Get or create Redis connection."""
        if self._redis is None:
            self._redis = redis.from_url(
                self.redis_url,
                decode_responses=True,
                socket_connect_timeout=5.0,
                socket_timeout=5.0,
            )
        return self._redis

    async def ping(self) -> None:
        """Verify Redis connectivity."""
        r = await self._get_redis()
        await r.ping()

    def _lock_key(self, lab_id: str) -> str:
        """Get Redis key for a lab's deploy lock.

        The lock is scoped to both agent and lab, allowing multiple agents
        to deploy their portion of a multi-host lab concurrently while still
        preventing concurrent deploys of the same lab on the same agent.
        """
        return f"deploy_lock:{self.agent_id}:{lab_id}"

    def _lock_value(self) -> str:
        """Get lock value containing ownership info."""
        return f"{self.agent_id}:{time.time()}"

    async def _get_local_lock(self, lab_id: str) -> asyncio.Lock:
        """Get local asyncio lock for a lab.

        This provides local concurrency control to prevent multiple coroutines
        on the same agent from racing to acquire the Redis lock.
        """
        if lab_id not in self._local_locks:
            self._local_locks[lab_id] = asyncio.Lock()
        return self._local_locks[lab_id]

    @asynccontextmanager
    async def acquire(self, lab_id: str, timeout: float = 30.0):
        """Acquire deploy lock with automatic TTL expiry.

        This context manager acquires a distributed lock for the given lab.
        The lock automatically expires after lock_ttl seconds, ensuring
        recovery from crashes.

        Args:
            lab_id: Lab identifier
            timeout: Maximum time to wait for lock acquisition

        Yields:
            None when lock is acquired

        Raises:
            LockAcquisitionTimeout: If lock cannot be acquired within timeout
        """
        r = await self._get_redis()
        lock_key = self._lock_key(lab_id)
        lock_value = self._lock_value()

        # First acquire local lock to prevent local races
        local_lock = await self._get_local_lock(lab_id)

        async with local_lock:
            # Try to acquire Redis lock with TTL
            start_time = asyncio.get_event_loop().time()

            while True:
                # Use SET NX EX for atomic acquire with TTL
                acquired = await r.set(
                    lock_key,
                    lock_value,
                    nx=True,  # Only set if not exists
                    ex=self.lock_ttl,
                )

                if acquired:
                    logger.info(f"Acquired deploy lock for lab {lab_id} (TTL: {self.lock_ttl}s)")
                    break

                # Check timeout
                elapsed = asyncio.get_event_loop().time() - start_time
                if elapsed >= timeout:
                    # Get info about current lock holder
                    current = await r.get(lock_key)
                    ttl = await r.ttl(lock_key)
                    logger.warning(
                        f"Lock acquisition timeout for lab {lab_id} after {timeout}s. "
                        f"Current holder: {current}, TTL: {ttl}s"
                    )
                    raise LockAcquisitionTimeout(lab_id, timeout)

                # Wait before retry
                await asyncio.sleep(0.5)

            try:
                yield
            finally:
                # Release lock - only if we still own it
                current = await r.get(lock_key)
                if current and current.startswith(f"{self.agent_id}:"):
                    await r.delete(lock_key)
                    logger.info(f"Released deploy lock for lab {lab_id}")
                else:
                    logger.warning(
                        f"Lock for lab {lab_id} was released by another process "
                        f"(expected owner starting with {self.agent_id}:, got {current})"
                    )

    async def force_release(self, lab_id: str) -> bool:
        """Force release a lock regardless of owner.

        This is used for stuck recovery when a lock needs to be cleared
        externally (e.g., by controller detecting stuck state).

        Args:
            lab_id: Lab identifier

        Returns:
            True if lock was deleted, False if it didn't exist
        """
        r = await self._get_redis()
        lock_key = self._lock_key(lab_id)

        # Log current lock info before deletion
        current = await r.get(lock_key)
        if current:
            ttl = await r.ttl(lock_key)
            logger.warning(
                f"Force-releasing lock for lab {lab_id} (owner: {current}, TTL: {ttl}s)"
            )

        deleted = await r.delete(lock_key)
        return deleted > 0

    async def get_lock_status(self, lab_id: str) -> dict:
        """Get lock status for a specific lab.

        Args:
            lab_id: Lab identifier

        Returns:
            Dict with lock info including held status, owner, TTL, and age
        """
        r = await self._get_redis()
        lock_key = self._lock_key(lab_id)

        value = await r.get(lock_key)
        ttl = await r.ttl(lock_key)

        if value is None or ttl < 0:
            return {
                "lab_id": lab_id,
                "held": False,
                "ttl": 0,
                "owner": None,
                "age_seconds": 0,
            }

        # Parse owner and acquisition time from value
        parts = value.split(":", 1)
        owner = parts[0] if parts else "unknown"
        try:
            acquired_at = float(parts[1]) if len(parts) > 1 else 0
            age_seconds = time.time() - acquired_at
        except (ValueError, IndexError):
            age_seconds = self.lock_ttl - ttl  # Estimate from TTL

        return {
            "lab_id": lab_id,
            "held": True,
            "ttl": ttl,
            "owner": owner,
            "age_seconds": age_seconds,
            "is_stuck": age_seconds > (self.lock_ttl * 0.9),  # >90% of TTL
        }

    async def get_all_locks(self) -> list[dict]:
        """Get status of all deploy locks for this agent.

        Returns:
            List of lock status dicts for all active locks held by this agent
        """
        r = await self._get_redis()

        # Find all deploy locks for this agent
        prefix = f"deploy_lock:{self.agent_id}:"
        keys = []
        async for key in r.scan_iter(match=f"{prefix}*"):
            keys.append(key)

        locks = []
        for key in keys:
            lab_id = key.replace(prefix, "")
            status = await self.get_lock_status(lab_id)
            if status["held"]:
                locks.append(status)

        return locks

    async def clear_agent_locks(self) -> list[str]:
        """Clear all locks held by this agent.

        Used during startup to clean up any orphaned locks from previous run.

        Returns:
            List of lab_ids whose locks were cleared
        """
        r = await self._get_redis()
        cleared = []
        prefix = f"deploy_lock:{self.agent_id}:"

        async for key in r.scan_iter(match=f"{prefix}*"):
            lab_id = key.replace(prefix, "")
            await r.delete(key)
            cleared.append(lab_id)
            logger.info(f"Cleared orphaned lock for lab {lab_id} from previous run")

        return cleared

    async def extend_lock(self, lab_id: str, extension_seconds: int | None = None) -> bool:
        """Extend the TTL of a held lock.

        This can be used for long-running operations that need more time.

        Args:
            lab_id: Lab identifier
            extension_seconds: Additional seconds to add (defaults to lock_ttl)

        Returns:
            True if lock was extended, False if lock not held by this agent
        """
        if extension_seconds is None:
            extension_seconds = self.lock_ttl

        r = await self._get_redis()
        lock_key = self._lock_key(lab_id)

        # Only extend if we own the lock
        current = await r.get(lock_key)
        if current and current.startswith(f"{self.agent_id}:"):
            await r.expire(lock_key, extension_seconds)
            logger.debug(f"Extended lock for lab {lab_id} by {extension_seconds}s")
            return True

        return False

    @asynccontextmanager
    async def acquire_with_heartbeat(
        self,
        lab_id: str,
        timeout: float = 30.0,
        extend_interval: float = 30.0,
    ):
        """Acquire lock with automatic heartbeat extension.

        This is the preferred method for long-running operations like deploy.
        It periodically extends the lock TTL while the operation is running,
        allowing short TTLs for fast crash recovery while supporting
        long-running deploys.

        Args:
            lab_id: Lab identifier
            timeout: Maximum time to wait for lock acquisition
            extend_interval: How often to extend the lock (should be < lock_ttl)

        Yields:
            None when lock is acquired

        Example:
            async with lock_manager.acquire_with_heartbeat("lab-123"):
                await long_running_deploy()
        """
        extension_task = None

        async def heartbeat_loop():
            """Periodically extend the lock while operation runs."""
            try:
                while True:
                    await asyncio.sleep(extend_interval)
                    extended = await self.extend_lock(lab_id)
                    if not extended:
                        logger.warning(
                            f"Failed to extend lock for lab {lab_id} - "
                            "lock may have been force-released"
                        )
            except asyncio.CancelledError:
                pass

        async with self.acquire(lab_id, timeout=timeout):
            # Start heartbeat task
            extension_task = asyncio.create_task(heartbeat_loop())
            logger.debug(f"Started lock heartbeat for lab {lab_id} (interval: {extend_interval}s)")

            try:
                yield
            finally:
                # Stop heartbeat
                if extension_task:
                    extension_task.cancel()
                    try:
                        await extension_task
                    except asyncio.CancelledError:
                        pass
                    logger.debug(f"Stopped lock heartbeat for lab {lab_id}")

    async def close(self):
        """Close Redis connection."""
        if self._redis:
            await self._redis.close()
            self._redis = None


class NoopDeployLockManager:
    """No-op lock manager for standalone agents without Redis."""

    def __init__(self, agent_id: str = ""):
        self.agent_id = agent_id

    @asynccontextmanager
    async def acquire(self, _lab_id: str, timeout: float = 30.0):
        yield True

    @asynccontextmanager
    async def acquire_with_heartbeat(
        self,
        _lab_id: str,
        timeout: float = 30.0,
        extend_interval: float = 30.0,
    ):
        yield True

    async def clear_agent_locks(self) -> list[str]:
        return []

    async def get_all_locks(self) -> list[dict]:
        return []

    async def get_lock_status(self, _lab_id: str) -> dict:
        return {"lab_id": _lab_id, "held": False, "ttl": 0, "owner": None, "age_seconds": 0}

    async def force_release(self, _lab_id: str) -> bool:
        return True

    async def close(self):
        return None


# Singleton instance for the agent
_lock_manager: DeployLockManager | NoopDeployLockManager | None = None


def get_lock_manager() -> DeployLockManager | NoopDeployLockManager | None:
    """Get the global lock manager instance."""
    return _lock_manager


def set_lock_manager(manager: DeployLockManager | NoopDeployLockManager | None) -> None:
    """Set the global lock manager instance."""
    global _lock_manager
    _lock_manager = manager
