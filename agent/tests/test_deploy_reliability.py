"""Tests for deploy reliability features.

These tests verify:
1. Lock acquisition timeout during deploy
2. Proper handling of concurrent deploy requests
3. Async callback mode with timeouts
4. Redis-based lock manager behavior
"""

import asyncio
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from contextlib import asynccontextmanager

from agent.schemas import (
    DeployRequest,
    JobResult,
    JobStatus,
    Provider,
)
from agent.locks import DeployLockManager, LockAcquisitionTimeout


# --- Mock Lock Manager for Testing ---

class MockLockManager:
    """Mock lock manager for testing without Redis."""

    def __init__(self):
        self._locks: dict[str, bool] = {}
        self._should_timeout = False
        self._force_released: list[str] = []

    def set_timeout_mode(self, should_timeout: bool):
        """Configure whether acquire should timeout."""
        self._should_timeout = should_timeout

    @asynccontextmanager
    async def acquire(self, lab_id: str, timeout: float = 30.0):
        if self._should_timeout:
            raise LockAcquisitionTimeout(lab_id, timeout)
        self._locks[lab_id] = True
        try:
            yield
        finally:
            self._locks.pop(lab_id, None)

    async def force_release(self, lab_id: str) -> bool:
        self._force_released.append(lab_id)
        return self._locks.pop(lab_id, False) or True

    async def get_all_locks(self) -> list[dict]:
        return [{"lab_id": k, "held": True} for k in self._locks]

    async def get_lock_status(self, lab_id: str) -> dict:
        return {"lab_id": lab_id, "held": lab_id in self._locks}

    async def clear_agent_locks(self) -> list[str]:
        cleared = list(self._locks.keys())
        self._locks.clear()
        return cleared

    async def extend_lock(self, lab_id: str, extension_seconds: int | None = None) -> bool:
        """Mock extend lock."""
        return lab_id in self._locks

    @asynccontextmanager
    async def acquire_with_heartbeat(
        self,
        lab_id: str,
        timeout: float = 30.0,
        extend_interval: float = 30.0,
    ):
        """Mock acquire with heartbeat - same as acquire for testing."""
        async with self.acquire(lab_id, timeout):
            yield


# --- Tests for Lock Acquisition Timeout ---

@pytest.mark.asyncio
async def test_deploy_returns_503_when_lock_timeout():
    """Test that deploy returns 503 when lock cannot be acquired within timeout."""
    from agent.main import deploy_lab
    from fastapi import HTTPException

    lab_id = "test-lock-timeout"

    # Create mock that times out
    mock_manager = MockLockManager()
    mock_manager.set_timeout_mode(True)

    with patch('agent.main.get_lock_manager', return_value=mock_manager):
        request = DeployRequest(
            job_id="job-123",
            lab_id=lab_id,
            topology_yaml="name: test\n",
            provider=Provider.DOCKER,
        )

        # Try to deploy - should timeout
        with pytest.raises(HTTPException) as exc_info:
            await deploy_lab(request)

        assert exc_info.value.status_code == 503
        assert "already in progress" in exc_info.value.detail


@pytest.mark.asyncio
async def test_deploy_acquires_lock_when_available():
    """Test that deploy acquires lock and proceeds when available."""
    from agent.main import deploy_lab
    from agent.providers.base import DeployResult

    lab_id = "test-lock-success"
    mock_manager = MockLockManager()

    with patch('agent.main.get_lock_manager', return_value=mock_manager):
        with patch('agent.main.get_provider_for_request') as mock_get_provider:
            mock_provider = MagicMock()
            mock_provider.deploy = AsyncMock(return_value=DeployResult(
                success=True,
                stdout="Deployed",
                stderr="",
            ))
            mock_get_provider.return_value = mock_provider

            request = DeployRequest(
                job_id="job-456",
                lab_id=lab_id,
                topology_yaml="name: test\n",
                provider=Provider.DOCKER,
            )

            result = await deploy_lab(request)

            assert result.status == JobStatus.COMPLETED
            mock_provider.deploy.assert_called_once()


@pytest.mark.asyncio
async def test_deploy_returns_503_when_lock_manager_not_initialized():
    """Test that deploy returns 503 when lock manager is not initialized."""
    from agent.main import deploy_lab
    from fastapi import HTTPException

    with patch('agent.main.get_lock_manager', return_value=None):
        request = DeployRequest(
            job_id="job-no-manager",
            lab_id="test-lab",
            topology_yaml="name: test\n",
            provider=Provider.DOCKER,
        )

        with pytest.raises(HTTPException) as exc_info:
            await deploy_lab(request)

        assert exc_info.value.status_code == 503
        assert "Lock manager not initialized" in exc_info.value.detail


# --- Tests for Async Callback Mode ---

@pytest.mark.asyncio
async def test_async_deploy_returns_accepted():
    """Test that async deploy with callback returns 202 Accepted immediately."""
    from agent.main import deploy_lab

    lab_id = "test-async-accepted"
    mock_manager = MockLockManager()

    request = DeployRequest(
        job_id="job-async",
        lab_id=lab_id,
        topology_yaml="name: test\n",
        provider=Provider.DOCKER,
        callback_url="http://localhost:8000/callback",
    )

    with patch('agent.main.get_lock_manager', return_value=mock_manager):
        with patch('agent.main._execute_deploy_with_callback', new_callable=AsyncMock):
            result = await deploy_lab(request)

            assert result.status == JobStatus.ACCEPTED
            assert "accepted for async execution" in result.stdout


@pytest.mark.asyncio
async def test_async_deploy_callback_sends_timeout_on_lock_failure():
    """Test that async deploy sends failure callback when lock times out."""
    from agent.main import _execute_deploy_with_callback

    lab_id = "test-async-lock-timeout"

    # Create mock that times out
    mock_manager = MockLockManager()
    mock_manager.set_timeout_mode(True)

    callback_payload = None

    async def capture_callback(url, payload):
        nonlocal callback_payload
        callback_payload = payload

    with patch('agent.main.get_lock_manager', return_value=mock_manager):
        with patch('agent.callbacks.deliver_callback', side_effect=capture_callback):
            await _execute_deploy_with_callback(
                job_id="job-timeout",
                lab_id=lab_id,
                topology_yaml="name: test\n",
                provider_name="docker",
                callback_url="http://localhost:8000/callback",
            )

    # Verify callback was sent with failure
    assert callback_payload is not None
    assert callback_payload.status == "failed"
    assert "timed out" in callback_payload.error_message


@pytest.mark.asyncio
async def test_async_deploy_callback_sends_success():
    """Test that async deploy sends success callback on completion."""
    from agent.main import _execute_deploy_with_callback
    from agent.providers.base import DeployResult

    lab_id = "test-async-success"
    mock_manager = MockLockManager()

    callback_payload = None

    async def capture_callback(url, payload):
        nonlocal callback_payload
        callback_payload = payload

    with patch('agent.main.get_lock_manager', return_value=mock_manager):
        with patch('agent.main.get_provider_for_request') as mock_get_provider:
            mock_provider = MagicMock()
            mock_provider.deploy = AsyncMock(return_value=DeployResult(
                success=True,
                stdout="Deployed successfully",
                stderr="",
            ))
            mock_get_provider.return_value = mock_provider

            with patch('agent.callbacks.deliver_callback', side_effect=capture_callback):
                await _execute_deploy_with_callback(
                    job_id="job-success",
                    lab_id=lab_id,
                    topology_yaml="name: test\n",
                    provider_name="docker",
                    callback_url="http://localhost:8000/callback",
                )

    # Verify callback was sent with success
    assert callback_payload is not None
    assert callback_payload.status == "completed"
    assert callback_payload.stdout == "Deployed successfully"


@pytest.mark.asyncio
async def test_async_deploy_callback_sends_error_on_exception():
    """Test that async deploy sends error callback on exception."""
    from agent.main import _execute_deploy_with_callback

    lab_id = "test-async-error"
    mock_manager = MockLockManager()

    callback_payload = None

    async def capture_callback(url, payload):
        nonlocal callback_payload
        callback_payload = payload

    with patch('agent.main.get_lock_manager', return_value=mock_manager):
        with patch('agent.main.get_provider_for_request') as mock_get_provider:
            mock_provider = MagicMock()
            mock_provider.deploy = AsyncMock(side_effect=Exception("Deploy explosion"))
            mock_get_provider.return_value = mock_provider

            with patch('agent.callbacks.deliver_callback', side_effect=capture_callback):
                await _execute_deploy_with_callback(
                    job_id="job-error",
                    lab_id=lab_id,
                    topology_yaml="name: test\n",
                    provider_name="docker",
                    callback_url="http://localhost:8000/callback",
                )

    # Verify callback was sent with error
    assert callback_payload is not None
    assert callback_payload.status == "failed"
    assert "Deploy explosion" in callback_payload.error_message


@pytest.mark.asyncio
async def test_async_deploy_fails_when_lock_manager_not_initialized():
    """Test that async deploy sends failure callback when lock manager not initialized."""
    from agent.main import _execute_deploy_with_callback

    callback_payload = None

    async def capture_callback(url, payload):
        nonlocal callback_payload
        callback_payload = payload

    with patch('agent.main.get_lock_manager', return_value=None):
        with patch('agent.callbacks.deliver_callback', side_effect=capture_callback):
            await _execute_deploy_with_callback(
                job_id="job-no-manager",
                lab_id="test-lab",
                topology_yaml="name: test\n",
                provider_name="docker",
                callback_url="http://localhost:8000/callback",
            )

    assert callback_payload is not None
    assert callback_payload.status == "failed"
    assert "Lock manager not initialized" in callback_payload.error_message


# --- Tests for Lock Manager ---

@pytest.mark.asyncio
async def test_lock_manager_acquire_and_release():
    """Test basic lock acquire and release."""
    manager = MockLockManager()

    async with manager.acquire("lab-1"):
        # Lock should be held
        status = await manager.get_lock_status("lab-1")
        assert status["held"] is True

    # Lock should be released
    status = await manager.get_lock_status("lab-1")
    assert status["held"] is False


@pytest.mark.asyncio
async def test_lock_manager_force_release():
    """Test force releasing a lock."""
    manager = MockLockManager()

    # Acquire lock
    manager._locks["lab-1"] = True

    # Force release
    result = await manager.force_release("lab-1")
    assert result is True
    assert "lab-1" in manager._force_released

    # Lock should be gone
    status = await manager.get_lock_status("lab-1")
    assert status["held"] is False


@pytest.mark.asyncio
async def test_lock_manager_timeout():
    """Test lock acquisition timeout."""
    manager = MockLockManager()
    manager.set_timeout_mode(True)

    with pytest.raises(LockAcquisitionTimeout) as exc_info:
        async with manager.acquire("lab-1", timeout=1.0):
            pass

    assert exc_info.value.lab_id == "lab-1"


# --- Tests for Config Settings ---

def test_timeout_settings_exist():
    """Test that timeout settings are configured."""
    from agent.config import settings

    assert hasattr(settings, 'deploy_timeout')
    assert hasattr(settings, 'destroy_timeout')
    assert hasattr(settings, 'lock_acquire_timeout')
    assert hasattr(settings, 'redis_url')
    assert hasattr(settings, 'lock_ttl')
    assert hasattr(settings, 'lock_extend_interval')

    # Verify reasonable defaults
    assert settings.deploy_timeout == 900.0  # 15 minutes
    assert settings.destroy_timeout == 300.0  # 5 minutes
    assert settings.lock_acquire_timeout == 30.0  # 30 seconds
    assert settings.lock_ttl == 120  # 2 minutes (short, with heartbeat extension)
    assert settings.lock_extend_interval == 30.0  # extend every 30 seconds


def test_lock_extend_interval_less_than_ttl():
    """Test that lock extend interval is less than TTL."""
    from agent.config import settings

    assert settings.lock_extend_interval < settings.lock_ttl, \
        "lock_extend_interval must be less than lock_ttl to prevent expiry during active deploy"


def test_lock_ttl_is_short_for_crash_recovery():
    """Test that lock TTL is short enough for fast crash recovery."""
    from agent.config import settings

    # Lock should expire within 5 minutes for reasonable crash recovery
    assert settings.lock_ttl <= 300, \
        f"lock_ttl ({settings.lock_ttl}s) should be <= 300s for fast crash recovery"


@pytest.mark.asyncio
async def test_acquire_with_heartbeat_calls_extend():
    """Test that acquire_with_heartbeat extends lock periodically."""
    from agent.locks import DeployLockManager

    # This test uses a mock to verify extend is called
    extend_calls = []

    class TrackingMockManager(MockLockManager):
        async def extend_lock(self, lab_id: str, extension_seconds: int | None = None) -> bool:
            extend_calls.append(lab_id)
            return True

        @asynccontextmanager
        async def acquire_with_heartbeat(
            self,
            lab_id: str,
            timeout: float = 30.0,
            extend_interval: float = 0.05,  # Very short for testing
        ):
            async def heartbeat_loop():
                try:
                    while True:
                        await asyncio.sleep(extend_interval)
                        await self.extend_lock(lab_id)
                except asyncio.CancelledError:
                    pass

            async with self.acquire(lab_id, timeout):
                task = asyncio.create_task(heartbeat_loop())
                try:
                    yield
                finally:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

    manager = TrackingMockManager()

    async with manager.acquire_with_heartbeat("lab-1", extend_interval=0.05):
        # Wait long enough for at least one extension
        await asyncio.sleep(0.15)

    # Should have extended at least once
    assert len(extend_calls) >= 1
    assert all(lab_id == "lab-1" for lab_id in extend_calls)


# To run these tests:
# cd agent && pytest tests/test_deploy_reliability.py -v
