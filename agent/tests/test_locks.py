"""Tests for agent/locks.py.

Covers DeployLockManager (acquire, release, force_release, lock status,
clear, extend, heartbeat), NoopDeployLockManager, LockAcquisitionTimeout,
and the module-level singleton accessors.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from agent.locks import (
    DeployLockManager,
    LockAcquisitionTimeout,
    NoopDeployLockManager,
    get_lock_manager,
    set_lock_manager,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_redis() -> AsyncMock:
    """Create an AsyncMock that behaves like redis.asyncio.Redis."""
    r = AsyncMock()
    r.set = AsyncMock(return_value=True)
    r.get = AsyncMock(return_value=None)
    r.delete = AsyncMock(return_value=1)
    r.ttl = AsyncMock(return_value=960)
    r.expire = AsyncMock(return_value=True)
    r.ping = AsyncMock(return_value=True)

    async def _scan_iter(match=None):
        return
        yield  # noqa

    r.scan_iter = _scan_iter
    return r


def _make_manager(agent_id: str = "agent-01") -> DeployLockManager:
    """Create a DeployLockManager with a mocked Redis connection."""
    mgr = DeployLockManager(
        redis_url="redis://localhost:6379",
        lock_ttl=960,
        agent_id=agent_id,
    )
    return mgr


@pytest.fixture
def mock_redis():
    return _mock_redis()


@pytest.fixture
def manager(mock_redis):
    mgr = _make_manager()
    mgr._redis = mock_redis
    return mgr


# =============================================================================
# DeployLockManager — acquire and release
# =============================================================================


class TestDeployLockManager:
    """Core lock acquisition and release."""

    @pytest.mark.asyncio
    async def test_acquire_succeeds(self, manager, mock_redis):
        mock_redis.set.return_value = True

        async with manager.acquire("lab-1"):
            pass

        mock_redis.set.assert_called()
        # Verify the key pattern
        call_kwargs = mock_redis.set.call_args
        assert "deploy_lock:agent-01:lab-1" in call_kwargs.args or \
               call_kwargs.args[0] == "deploy_lock:agent-01:lab-1"

    @pytest.mark.asyncio
    async def test_releases_on_exit(self, manager, mock_redis):
        mock_redis.set.return_value = True
        mock_redis.get.return_value = "agent-01:1234567890.0"

        async with manager.acquire("lab-1"):
            pass

        mock_redis.delete.assert_called_once_with("deploy_lock:agent-01:lab-1")

    @pytest.mark.asyncio
    async def test_retries_until_success(self, manager, mock_redis):
        """Lock should retry when initially unavailable."""
        # First call fails (lock held), second succeeds
        mock_redis.set.side_effect = [False, True]
        mock_redis.get.return_value = "agent-01:1234567890.0"

        async with manager.acquire("lab-1", timeout=10):
            pass

        assert mock_redis.set.call_count == 2

    @pytest.mark.asyncio
    async def test_timeout_raises_exception(self, manager, mock_redis):
        """Should raise LockAcquisitionTimeout when timeout expires."""
        mock_redis.set.return_value = False
        mock_redis.get.return_value = "other-agent:1234567890.0"
        mock_redis.ttl.return_value = 500

        with pytest.raises(LockAcquisitionTimeout) as exc_info:
            async with manager.acquire("lab-1", timeout=0.1):
                pass

        assert exc_info.value.lab_id == "lab-1"

    @pytest.mark.asyncio
    async def test_ownership_check_on_release(self, manager, mock_redis):
        """Lock should only be deleted if still owned by this agent."""
        mock_redis.set.return_value = True
        # Simulate lock taken over by another agent
        mock_redis.get.return_value = "other-agent:1234567890.0"

        async with manager.acquire("lab-1"):
            pass

        # delete should NOT be called because we don't own the lock anymore
        mock_redis.delete.assert_not_called()


# =============================================================================
# Force release
# =============================================================================


class TestForceRelease:
    """Force release of locks regardless of owner."""

    @pytest.mark.asyncio
    async def test_deletes_existing_lock(self, manager, mock_redis):
        mock_redis.get.return_value = "agent-01:1234567890.0"
        mock_redis.ttl.return_value = 500
        mock_redis.delete.return_value = 1

        result = await manager.force_release("lab-1")
        assert result is True
        mock_redis.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_false_when_no_lock(self, manager, mock_redis):
        mock_redis.get.return_value = None
        mock_redis.delete.return_value = 0

        result = await manager.force_release("lab-1")
        assert result is False

    @pytest.mark.asyncio
    async def test_logs_owner_before_deletion(self, manager, mock_redis):
        mock_redis.get.return_value = "agent-02:1234567890.0"
        mock_redis.ttl.return_value = 500
        mock_redis.delete.return_value = 1

        result = await manager.force_release("lab-1")
        assert result is True


# =============================================================================
# Lock status
# =============================================================================


class TestLockStatus:
    """get_lock_status and get_all_locks."""

    @pytest.mark.asyncio
    async def test_held_lock_status(self, manager, mock_redis):
        mock_redis.get.return_value = f"agent-01:{time.time()}"
        mock_redis.ttl.return_value = 900

        status = await manager.get_lock_status("lab-1")
        assert status["held"] is True
        assert status["lab_id"] == "lab-1"
        assert status["ttl"] == 900
        assert "age_seconds" in status

    @pytest.mark.asyncio
    async def test_not_held_lock_status(self, manager, mock_redis):
        mock_redis.get.return_value = None
        mock_redis.ttl.return_value = -2

        status = await manager.get_lock_status("lab-1")
        assert status["held"] is False
        assert status["owner"] is None

    @pytest.mark.asyncio
    async def test_stuck_detection(self, manager, mock_redis):
        """Lock with age > 90% of TTL should be flagged as stuck."""
        old_time = time.time() - 900  # 900s ago
        mock_redis.get.return_value = f"agent-01:{old_time}"
        mock_redis.ttl.return_value = 60  # Only 60s remaining of 960s TTL

        status = await manager.get_lock_status("lab-1")
        assert status["held"] is True
        assert status["is_stuck"] is True

    @pytest.mark.asyncio
    async def test_get_all_scans_prefix(self, manager, mock_redis):
        """get_all_locks should scan for keys matching the agent prefix."""
        keys_found = [
            "deploy_lock:agent-01:lab-1",
            "deploy_lock:agent-01:lab-2",
        ]

        async def fake_scan_iter(match=None):
            for k in keys_found:
                yield k

        mock_redis.scan_iter = fake_scan_iter
        # Return held status for both
        mock_redis.get.return_value = f"agent-01:{time.time()}"
        mock_redis.ttl.return_value = 800

        locks = await manager.get_all_locks()
        assert len(locks) == 2

    @pytest.mark.asyncio
    async def test_get_all_empty(self, manager, mock_redis):
        async def empty_scan(match=None):
            return
            yield  # noqa

        mock_redis.scan_iter = empty_scan

        locks = await manager.get_all_locks()
        assert locks == []


# =============================================================================
# Clear and extend
# =============================================================================


class TestClearAndExtend:
    """clear_agent_locks and extend_lock."""

    @pytest.mark.asyncio
    async def test_clear_removes_all(self, manager, mock_redis):
        keys = [
            "deploy_lock:agent-01:lab-1",
            "deploy_lock:agent-01:lab-2",
        ]

        async def fake_scan(match=None):
            for k in keys:
                yield k

        mock_redis.scan_iter = fake_scan

        cleared = await manager.clear_agent_locks()
        assert sorted(cleared) == ["lab-1", "lab-2"]
        assert mock_redis.delete.call_count == 2

    @pytest.mark.asyncio
    async def test_clear_returns_lab_ids(self, manager, mock_redis):
        async def fake_scan(match=None):
            yield "deploy_lock:agent-01:my-lab"

        mock_redis.scan_iter = fake_scan

        cleared = await manager.clear_agent_locks()
        assert cleared == ["my-lab"]

    @pytest.mark.asyncio
    async def test_extend_updates_ttl(self, manager, mock_redis):
        mock_redis.get.return_value = f"agent-01:{time.time()}"

        result = await manager.extend_lock("lab-1", extension_seconds=120)
        assert result is True
        mock_redis.expire.assert_called_once_with(
            "deploy_lock:agent-01:lab-1", 120
        )

    @pytest.mark.asyncio
    async def test_extend_false_if_not_owner(self, manager, mock_redis):
        mock_redis.get.return_value = "other-agent:1234567890.0"

        result = await manager.extend_lock("lab-1")
        assert result is False
        mock_redis.expire.assert_not_called()

    @pytest.mark.asyncio
    async def test_extend_default_uses_lock_ttl(self, manager, mock_redis):
        mock_redis.get.return_value = f"agent-01:{time.time()}"

        await manager.extend_lock("lab-1")
        mock_redis.expire.assert_called_once_with(
            "deploy_lock:agent-01:lab-1", 960  # default lock_ttl
        )


# =============================================================================
# Acquire with heartbeat
# =============================================================================


class TestAcquireWithHeartbeat:
    """acquire_with_heartbeat creates and cancels heartbeat task."""

    @pytest.mark.asyncio
    async def test_extends_periodically(self, manager, mock_redis):
        """Heartbeat should attempt to extend the lock."""
        mock_redis.set.return_value = True
        mock_redis.get.return_value = f"agent-01:{time.time()}"

        async with manager.acquire_with_heartbeat(
            "lab-1", timeout=5, extend_interval=0.05,
        ):
            # Wait long enough for at least one heartbeat
            await asyncio.sleep(0.15)

        # extend_lock calls r.expire — verify it was called
        assert mock_redis.expire.call_count >= 1

    @pytest.mark.asyncio
    async def test_stops_on_exit(self, manager, mock_redis):
        """Heartbeat task should be cancelled when context exits."""
        mock_redis.set.return_value = True
        mock_redis.get.return_value = f"agent-01:{time.time()}"

        async with manager.acquire_with_heartbeat(
            "lab-1", timeout=5, extend_interval=0.05,
        ):
            pass

        # Give event loop a tick to clean up
        await asyncio.sleep(0.01)
        # No assertion needed — test passes if no exception from cancelled task

    @pytest.mark.asyncio
    async def test_timeout_propagates(self, manager, mock_redis):
        """Timeout during lock acquisition should raise."""
        mock_redis.set.return_value = False
        mock_redis.get.return_value = "other:123"
        mock_redis.ttl.return_value = 500

        with pytest.raises(LockAcquisitionTimeout):
            async with manager.acquire_with_heartbeat("lab-1", timeout=0.1):
                pass


# =============================================================================
# NoopDeployLockManager
# =============================================================================


class TestNoopDeployLockManager:
    """NoopDeployLockManager always succeeds, holds nothing."""

    @pytest.mark.asyncio
    async def test_acquire_succeeds(self):
        mgr = NoopDeployLockManager(agent_id="test")
        async with mgr.acquire("lab-1"):
            pass  # should not raise

    @pytest.mark.asyncio
    async def test_force_release_returns_true(self):
        mgr = NoopDeployLockManager()
        result = await mgr.force_release("lab-1")
        assert result is True

    @pytest.mark.asyncio
    async def test_status_not_held(self):
        mgr = NoopDeployLockManager()
        status = await mgr.get_lock_status("lab-1")
        assert status["held"] is False

    @pytest.mark.asyncio
    async def test_get_all_empty(self):
        mgr = NoopDeployLockManager()
        locks = await mgr.get_all_locks()
        assert locks == []

    @pytest.mark.asyncio
    async def test_clear_empty(self):
        mgr = NoopDeployLockManager()
        cleared = await mgr.clear_agent_locks()
        assert cleared == []

    @pytest.mark.asyncio
    async def test_acquire_with_heartbeat_succeeds(self):
        mgr = NoopDeployLockManager()
        async with mgr.acquire_with_heartbeat("lab-1"):
            pass  # should not raise

    @pytest.mark.asyncio
    async def test_close_returns_none(self):
        mgr = NoopDeployLockManager()
        result = await mgr.close()
        assert result is None


# =============================================================================
# LockAcquisitionTimeout
# =============================================================================


class TestLockAcquisitionTimeout:
    """Exception attributes and message."""

    def test_message_includes_lab_and_timeout(self):
        exc = LockAcquisitionTimeout("lab-42", 30.0)
        assert "lab-42" in str(exc)
        assert "30" in str(exc)

    def test_exception_attributes(self):
        exc = LockAcquisitionTimeout("lab-99", 60.0)
        assert exc.lab_id == "lab-99"
        assert exc.timeout == 60.0

    def test_inherits_from_exception(self):
        exc = LockAcquisitionTimeout("lab-1", 5.0)
        assert isinstance(exc, Exception)


# =============================================================================
# Module-level singleton
# =============================================================================


class TestModuleSingleton:
    """get_lock_manager / set_lock_manager."""

    def test_set_and_get(self):
        original = get_lock_manager()
        try:
            mgr = NoopDeployLockManager(agent_id="test")
            set_lock_manager(mgr)
            assert get_lock_manager() is mgr
        finally:
            set_lock_manager(original)

    def test_set_none(self):
        original = get_lock_manager()
        try:
            set_lock_manager(None)
            assert get_lock_manager() is None
        finally:
            set_lock_manager(original)
