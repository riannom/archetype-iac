"""Tests for deploy reliability features.

These tests verify:
1. Lock acquisition timeout during deploy
2. Proper handling of concurrent deploy requests
3. Async callback mode with timeouts
"""

import asyncio
import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from agent.schemas import (
    DeployRequest,
    JobResult,
    JobStatus,
    Provider,
)


# --- Tests for Lock Acquisition Timeout ---

@pytest.mark.asyncio
async def test_deploy_returns_503_when_lock_timeout():
    """Test that deploy returns 503 when lock cannot be acquired within timeout."""
    from agent.main import deploy_lab, _deploy_locks
    from agent.config import settings
    from fastapi import HTTPException

    lab_id = "test-lock-timeout"

    # Create and hold a lock
    if lab_id not in _deploy_locks:
        _deploy_locks[lab_id] = asyncio.Lock()

    lock = _deploy_locks[lab_id]

    # Patch settings to use a short timeout
    with patch.object(settings, 'lock_acquire_timeout', 0.1):
        # Hold the lock
        async with lock:
            request = DeployRequest(
                job_id="job-123",
                lab_id=lab_id,
                topology_yaml="name: test\n",
                provider=Provider.CONTAINERLAB,
            )

            # Try to deploy while lock is held - should timeout
            with pytest.raises(HTTPException) as exc_info:
                await deploy_lab(request)

            assert exc_info.value.status_code == 503
            assert "already in progress" in exc_info.value.detail

    # Cleanup
    _deploy_locks.pop(lab_id, None)


@pytest.mark.asyncio
async def test_deploy_acquires_lock_when_available():
    """Test that deploy acquires lock and proceeds when available."""
    from agent.main import deploy_lab, _deploy_locks, get_workspace
    from agent.providers.base import DeployResult

    lab_id = "test-lock-success"

    # Create fresh lock
    _deploy_locks[lab_id] = asyncio.Lock()

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
            provider=Provider.CONTAINERLAB,
        )

        result = await deploy_lab(request)

        assert result.status == JobStatus.COMPLETED
        mock_provider.deploy.assert_called_once()

    # Cleanup
    _deploy_locks.pop(lab_id, None)


@pytest.mark.asyncio
async def test_deploy_caches_result_for_concurrent_requests():
    """Test that concurrent deploy requests get cached result."""
    from agent.main import deploy_lab, _deploy_locks, _deploy_results
    from agent.providers.base import DeployResult

    lab_id = "test-cache"

    # Create fresh lock
    _deploy_locks[lab_id] = asyncio.Lock()

    deploy_call_count = 0

    async def slow_deploy(*args, **kwargs):
        nonlocal deploy_call_count
        deploy_call_count += 1
        await asyncio.sleep(0.2)  # Simulate slow deploy
        return DeployResult(success=True, stdout="Deployed", stderr="")

    with patch('agent.main.get_provider_for_request') as mock_get_provider:
        mock_provider = MagicMock()
        mock_provider.deploy = slow_deploy
        mock_get_provider.return_value = mock_provider

        request1 = DeployRequest(
            job_id="job-1",
            lab_id=lab_id,
            topology_yaml="name: test\n",
            provider=Provider.CONTAINERLAB,
        )

        request2 = DeployRequest(
            job_id="job-2",
            lab_id=lab_id,
            topology_yaml="name: test\n",
            provider=Provider.CONTAINERLAB,
        )

        # Start two concurrent deploys
        task1 = asyncio.create_task(deploy_lab(request1))
        await asyncio.sleep(0.05)  # Let first task start
        task2 = asyncio.create_task(deploy_lab(request2))

        result1, result2 = await asyncio.gather(task1, task2)

        # Both should succeed
        assert result1.status == JobStatus.COMPLETED
        assert result2.status == JobStatus.COMPLETED

        # Deploy should only be called once (second gets cached result)
        assert deploy_call_count == 1

    # Cleanup
    _deploy_locks.pop(lab_id, None)
    _deploy_results.pop(lab_id, None)


# --- Tests for Async Callback Mode with Timeout ---

@pytest.mark.asyncio
async def test_async_deploy_returns_accepted():
    """Test that async deploy with callback returns 202 Accepted immediately."""
    from agent.main import deploy_lab, _deploy_locks

    lab_id = "test-async-accepted"
    _deploy_locks[lab_id] = asyncio.Lock()

    request = DeployRequest(
        job_id="job-async",
        lab_id=lab_id,
        topology_yaml="name: test\n",
        provider=Provider.CONTAINERLAB,
        callback_url="http://localhost:8000/callback",
    )

    with patch('agent.main._execute_deploy_with_callback', new_callable=AsyncMock):
        result = await deploy_lab(request)

        assert result.status == JobStatus.ACCEPTED
        assert "accepted for async execution" in result.stdout

    # Cleanup
    _deploy_locks.pop(lab_id, None)


@pytest.mark.asyncio
async def test_async_deploy_callback_sends_timeout_on_lock_failure():
    """Test that async deploy sends failure callback when lock times out."""
    from agent.main import _execute_deploy_with_callback, _deploy_locks
    from agent.config import settings

    lab_id = "test-async-lock-timeout"

    # Create and hold a lock
    _deploy_locks[lab_id] = asyncio.Lock()
    lock = _deploy_locks[lab_id]

    callback_payload = None

    async def capture_callback(url, payload):
        nonlocal callback_payload
        callback_payload = payload

    # Patch settings to use a short timeout
    with patch.object(settings, 'lock_acquire_timeout', 0.1):
        with patch('agent.callbacks.deliver_callback', side_effect=capture_callback):
            # Hold the lock
            async with lock:
                # Start async deploy (should timeout)
                await _execute_deploy_with_callback(
                    job_id="job-timeout",
                    lab_id=lab_id,
                    topology_yaml="name: test\n",
                    provider_name="containerlab",
                    callback_url="http://localhost:8000/callback",
                    lock=lock,
                )

    # Verify callback was sent with failure
    assert callback_payload is not None
    assert callback_payload.status == "failed"
    assert "timed out" in callback_payload.error_message

    # Cleanup
    _deploy_locks.pop(lab_id, None)


@pytest.mark.asyncio
async def test_async_deploy_callback_sends_success():
    """Test that async deploy sends success callback on completion."""
    from agent.main import _execute_deploy_with_callback, _deploy_locks
    from agent.providers.base import DeployResult

    lab_id = "test-async-success"
    _deploy_locks[lab_id] = asyncio.Lock()
    lock = _deploy_locks[lab_id]

    callback_payload = None

    async def capture_callback(url, payload):
        nonlocal callback_payload
        callback_payload = payload

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
                provider_name="containerlab",
                callback_url="http://localhost:8000/callback",
                lock=lock,
            )

    # Verify callback was sent with success
    assert callback_payload is not None
    assert callback_payload.status == "completed"
    assert callback_payload.stdout == "Deployed successfully"

    # Cleanup
    _deploy_locks.pop(lab_id, None)


@pytest.mark.asyncio
async def test_async_deploy_callback_sends_error_on_exception():
    """Test that async deploy sends error callback on exception."""
    from agent.main import _execute_deploy_with_callback, _deploy_locks

    lab_id = "test-async-error"
    _deploy_locks[lab_id] = asyncio.Lock()
    lock = _deploy_locks[lab_id]

    callback_payload = None

    async def capture_callback(url, payload):
        nonlocal callback_payload
        callback_payload = payload

    with patch('agent.main.get_provider_for_request') as mock_get_provider:
        mock_provider = MagicMock()
        mock_provider.deploy = AsyncMock(side_effect=Exception("Deploy explosion"))
        mock_get_provider.return_value = mock_provider

        with patch('agent.callbacks.deliver_callback', side_effect=capture_callback):
            await _execute_deploy_with_callback(
                job_id="job-error",
                lab_id=lab_id,
                topology_yaml="name: test\n",
                provider_name="containerlab",
                callback_url="http://localhost:8000/callback",
                lock=lock,
            )

    # Verify callback was sent with error
    assert callback_payload is not None
    assert callback_payload.status == "failed"
    assert "Deploy explosion" in callback_payload.error_message

    # Cleanup
    _deploy_locks.pop(lab_id, None)


# --- Tests for Config Settings ---

def test_timeout_settings_exist():
    """Test that timeout settings are configured."""
    from agent.config import settings

    assert hasattr(settings, 'deploy_timeout')
    assert hasattr(settings, 'destroy_timeout')
    assert hasattr(settings, 'lock_acquire_timeout')

    # Verify reasonable defaults
    assert settings.deploy_timeout == 900.0  # 15 minutes
    assert settings.destroy_timeout == 300.0  # 5 minutes
    assert settings.lock_acquire_timeout == 30.0  # 30 seconds


# To run these tests:
# cd agent && pytest tests/test_deploy_reliability.py -v
