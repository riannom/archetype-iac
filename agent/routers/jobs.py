"""Deploy and destroy job endpoints."""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException

from agent.config import settings
import agent.agent_state as _state
from agent.agent_state import get_lock_manager, _increment_active_jobs, _decrement_active_jobs
from agent.helpers import get_workspace, get_provider_for_request
from agent.schemas import (
    DeployRequest, DeployTopology, DestroyRequest, JobResult, JobStatus,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["jobs"])


@router.post("/jobs/deploy")
async def deploy_lab(request: DeployRequest) -> JobResult:
    """Deploy a lab topology.

    Uses Redis-based per-lab locking to prevent concurrent deploys for the same lab.
    Locks automatically expire via TTL if agent crashes, ensuring recovery.

    Accepts topology in JSON format only.

    If callback_url is provided, returns 202 Accepted immediately and executes
    the deploy in the background, POSTing the result to the callback URL when done.
    """
    from agent.locks import LockAcquisitionTimeout

    lab_id = request.lab_id
    logger.info(f"Deploy request: lab={lab_id}, job={request.job_id}, provider={request.provider.value}")
    if request.callback_url:
        logger.debug(f"  Async mode with callback: {request.callback_url}")

    # Validate that JSON topology is provided
    if not request.topology:
        raise HTTPException(
            status_code=400,
            detail="No topology provided. Deploy requires 'topology' (JSON)."
        )

    lock_manager = get_lock_manager()
    if lock_manager is None:
        raise HTTPException(
            status_code=503,
            detail="Lock manager not initialized"
        )

    # Async callback mode - return immediately and execute in background
    if request.callback_url:
        # Start async execution
        asyncio.create_task(
            _execute_deploy_with_callback(
                request.job_id,
                lab_id,
                request.topology,
                request.provider.value,
                request.callback_url,
            )
        )
        return JobResult(
            job_id=request.job_id,
            status=JobStatus.ACCEPTED,
            stdout="Deploy accepted for async execution",
        )

    # Synchronous mode - acquire Redis lock with heartbeat and execute
    try:
        async with lock_manager.acquire_with_heartbeat(
            lab_id,
            timeout=settings.lock_acquire_timeout,
            extend_interval=settings.lock_extend_interval,
        ):
            provider = get_provider_for_request(request.provider.value)
            workspace = get_workspace(lab_id)
            logger.info(f"Deploy starting: lab={lab_id}, workspace={workspace}")

            result = await provider.deploy(
                lab_id=lab_id,
                topology=request.topology,
                workspace=workspace,
            )

            logger.info(f"Deploy finished: lab={lab_id}, success={result.success}")

            if result.success:
                job_result = JobResult(
                    job_id=request.job_id,
                    status=JobStatus.COMPLETED,
                    stdout=result.stdout,
                    stderr=result.stderr,
                )
            else:
                job_result = JobResult(
                    job_id=request.job_id,
                    status=JobStatus.FAILED,
                    stdout=result.stdout,
                    stderr=result.stderr,
                    error_message=result.error,
                )

            # Cache result briefly for concurrent requests
            _state._deploy_results[lab_id] = job_result
            asyncio.create_task(_cleanup_deploy_cache(lab_id, delay=5.0))

            return job_result

    except LockAcquisitionTimeout as e:
        logger.warning(f"Timeout waiting for deploy lock on lab {lab_id}: {e}")
        raise HTTPException(
            status_code=503,
            detail=f"Deploy already in progress for lab {lab_id}, try again later"
        )
    except Exception as e:
        logger.error(f"Deploy error for lab {lab_id}: {e}", exc_info=True)
        job_result = JobResult(
            job_id=request.job_id,
            status=JobStatus.FAILED,
            error_message=str(e),
        )
        _state._deploy_results[lab_id] = job_result
        asyncio.create_task(_cleanup_deploy_cache(lab_id, delay=5.0))
        return job_result


async def _execute_deploy_with_callback(
    job_id: str,
    lab_id: str,
    topology: "DeployTopology | None",
    provider_name: str,
    callback_url: str,
) -> None:
    """Execute deploy in background and send result via callback.

    This function handles the async deploy execution pattern:
    1. Acquire the lab lock via Redis with heartbeat (prevents concurrent deploys)
    2. Periodically extend the lock TTL while deploy is running
    3. Execute the deploy operation
    4. POST the result to the callback URL
    5. Handle callback delivery failures with retry

    The Redis lock has a short TTL (2 min) for fast crash recovery, but is
    extended every 30s while the deploy is actively running.

    Args:
        topology: Structured JSON topology (preferred for multi-host)
    """
    from agent.callbacks import CallbackPayload, deliver_callback
    from agent.locks import LockAcquisitionTimeout
    from datetime import datetime, timezone

    _increment_active_jobs()
    try:
        started_at = datetime.now(timezone.utc)
        lock_manager = get_lock_manager()

        if lock_manager is None:
            logger.error(f"Lock manager not initialized for async deploy of lab {lab_id}")
            payload = CallbackPayload(
                job_id=job_id,
                agent_id=_state.AGENT_ID,
                status="failed",
                error_message="Lock manager not initialized",
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
            )
            await deliver_callback(callback_url, payload)
            return

        try:
            async with lock_manager.acquire_with_heartbeat(
                lab_id,
                timeout=settings.lock_acquire_timeout,
                extend_interval=settings.lock_extend_interval,
            ):
                try:
                    from agent.callbacks import HeartbeatSender

                    provider = get_provider_for_request(provider_name)
                    workspace = get_workspace(lab_id)
                    logger.info(f"Async deploy starting: lab={lab_id}, workspace={workspace}")

                    # Send heartbeats during deploy to prove job is active
                    async with HeartbeatSender(callback_url, job_id, interval=30.0):
                        result = await provider.deploy(
                            lab_id=lab_id,
                            topology=topology,
                            workspace=workspace,
                        )

                    logger.info(f"Async deploy finished: lab={lab_id}, success={result.success}")

                    # Build callback payload
                    payload = CallbackPayload(
                        job_id=job_id,
                        agent_id=_state.AGENT_ID,
                        status="completed" if result.success else "failed",
                        stdout=result.stdout or "",
                        stderr=result.stderr or "",
                        error_message=result.error if not result.success else None,
                        started_at=started_at,
                        completed_at=datetime.now(timezone.utc),
                    )

                except Exception as e:
                    logger.error(f"Async deploy error for lab {lab_id}: {e}", exc_info=True)

                    payload = CallbackPayload(
                        job_id=job_id,
                        agent_id=_state.AGENT_ID,
                        status="failed",
                        error_message=str(e),
                        started_at=started_at,
                        completed_at=datetime.now(timezone.utc),
                    )

        except LockAcquisitionTimeout:
            logger.warning(f"Async deploy timeout waiting for lock on lab {lab_id}")
            payload = CallbackPayload(
                job_id=job_id,
                agent_id=_state.AGENT_ID,
                status="failed",
                error_message=f"Deploy already in progress for lab {lab_id}, timed out waiting for lock",
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
            )

        # Deliver callback (outside the lock)
        await deliver_callback(callback_url, payload)
    finally:
        _decrement_active_jobs()


async def _cleanup_deploy_cache(lab_id: str, delay: float = 5.0):
    """Clean up cached deploy result after a delay."""
    await asyncio.sleep(delay)
    _state._deploy_results.pop(lab_id, None)


@router.post("/jobs/destroy")
async def destroy_lab(request: DestroyRequest) -> JobResult:
    """Tear down a lab.

    If callback_url is provided, returns 202 Accepted immediately and executes
    the destroy in the background, POSTing the result to the callback URL when done.
    """
    from agent.locks import LockAcquisitionTimeout

    logger.info(f"Destroy request: lab={request.lab_id}, job={request.job_id}")
    if request.callback_url:
        logger.debug(f"  Async mode with callback: {request.callback_url}")

    lock_manager = get_lock_manager()
    if lock_manager is None:
        raise HTTPException(
            status_code=503,
            detail="Lock manager not initialized"
        )

    # Async callback mode - return immediately and execute in background
    if request.callback_url:
        asyncio.create_task(
            _execute_destroy_with_callback(
                request.job_id,
                request.lab_id,
                request.provider.value,
                request.callback_url,
            )
        )
        return JobResult(
            job_id=request.job_id,
            status=JobStatus.ACCEPTED,
            stdout="Destroy accepted for async execution",
        )

    # Synchronous mode - acquire lock first
    try:
        async with lock_manager.acquire_with_heartbeat(
            request.lab_id,
            timeout=settings.lock_acquire_timeout,
            extend_interval=settings.lock_extend_interval,
        ):
            provider = get_provider_for_request(request.provider.value)
            workspace = get_workspace(request.lab_id)
            result = await provider.destroy(
                lab_id=request.lab_id,
                workspace=workspace,
            )

            if result.success:
                return JobResult(
                    job_id=request.job_id,
                    status=JobStatus.COMPLETED,
                    stdout=result.stdout,
                    stderr=result.stderr,
                )
            else:
                return JobResult(
                    job_id=request.job_id,
                    status=JobStatus.FAILED,
                    stdout=result.stdout,
                    stderr=result.stderr,
                    error_message=result.error,
                )
    except LockAcquisitionTimeout:
        logger.warning(f"Timeout waiting for lock on lab {request.lab_id} for destroy")
        raise HTTPException(
            status_code=503,
            detail=f"Another operation is in progress for lab {request.lab_id}, try again later"
        )


async def _execute_destroy_with_callback(
    job_id: str,
    lab_id: str,
    provider_name: str,
    callback_url: str,
) -> None:
    """Execute destroy in background and send result via callback."""
    from agent.callbacks import CallbackPayload, deliver_callback, HeartbeatSender
    from agent.locks import LockAcquisitionTimeout
    from datetime import datetime, timezone

    _increment_active_jobs()
    try:
        started_at = datetime.now(timezone.utc)
        lock_manager = get_lock_manager()

        if lock_manager is None:
            logger.error(f"Lock manager not initialized for async destroy of lab {lab_id}")
            payload = CallbackPayload(
                job_id=job_id,
                agent_id=_state.AGENT_ID,
                status="failed",
                error_message="Lock manager not initialized",
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
            )
            await deliver_callback(callback_url, payload)
            return

        try:
            async with lock_manager.acquire_with_heartbeat(
                lab_id,
                timeout=settings.lock_acquire_timeout,
                extend_interval=settings.lock_extend_interval,
            ):
                provider = get_provider_for_request(provider_name)
                workspace = get_workspace(lab_id)
                logger.info(f"Async destroy starting: lab={lab_id}, workspace={workspace}")

                # Send heartbeats during destroy to prove job is active
                async with HeartbeatSender(callback_url, job_id, interval=30.0):
                    result = await provider.destroy(
                        lab_id=lab_id,
                        workspace=workspace,
                    )

                logger.info(f"Async destroy finished: lab={lab_id}, success={result.success}")

                payload = CallbackPayload(
                    job_id=job_id,
                    agent_id=_state.AGENT_ID,
                    status="completed" if result.success else "failed",
                    stdout=result.stdout or "",
                    stderr=result.stderr or "",
                    error_message=result.error if not result.success else None,
                    started_at=started_at,
                    completed_at=datetime.now(timezone.utc),
                )

        except LockAcquisitionTimeout:
            logger.warning(f"Lock timeout for async destroy of lab {lab_id}")
            payload = CallbackPayload(
                job_id=job_id,
                agent_id=_state.AGENT_ID,
                status="failed",
                error_message=f"Another operation is in progress for lab {lab_id}",
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
            )

        except Exception as e:
            logger.error(f"Async destroy error for lab {lab_id}: {e}", exc_info=True)

            payload = CallbackPayload(
                job_id=job_id,
                agent_id=_state.AGENT_ID,
                status="failed",
                error_message=str(e),
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
            )

        await deliver_callback(callback_url, payload)
    finally:
        _decrement_active_jobs()
