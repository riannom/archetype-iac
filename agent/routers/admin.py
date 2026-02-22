"""POAP, callbacks, locks, and update endpoints."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from starlette.responses import PlainTextResponse

import agent.agent_state as _state
from agent.config import settings
from agent.helpers import _load_node_startup_config, _render_n9kv_poap_script
from agent.schemas import UpdateRequest, UpdateResponse
from agent.updater import DeploymentMode, detect_deployment_mode, perform_docker_update, perform_systemd_update

logger = logging.getLogger(__name__)
router = APIRouter(tags=["admin"])


@router.get("/poap/{lab_id}/{node_name}/startup-config")
def poap_startup_config(lab_id: str, node_name: str, request: Request):
    """Serve node startup-config for pre-boot POAP script fetch."""
    logger.info(
        "POAP startup-config request",
        extra={
            "event": "poap_startup_config_request",
            "lab_id": lab_id,
            "node_name": node_name,
            "client_host": request.client.host if request.client else None,
        },
    )
    content = _load_node_startup_config(lab_id, node_name)
    return PlainTextResponse(content=content, media_type="text/plain; charset=utf-8")


@router.get("/poap/{lab_id}/{node_name}/script.py")
def poap_script(lab_id: str, node_name: str, request: Request):
    """Serve a generated POAP script that fetches and applies startup-config."""
    logger.info(
        "POAP script request",
        extra={
            "event": "poap_script_request",
            "lab_id": lab_id,
            "node_name": node_name,
            "client_host": request.client.host if request.client else None,
        },
    )
    _load_node_startup_config(lab_id, node_name)  # Ensure config exists before serving script.
    base_url = f"{request.url.scheme}://{request.url.netloc}"
    config_url = f"{base_url}/poap/{lab_id}/{node_name}/startup-config"
    script = _render_n9kv_poap_script(config_url)
    return PlainTextResponse(content=script, media_type="text/x-python")


@router.get("/callbacks/dead-letters")
def get_dead_letters():
    """Get failed callbacks that couldn't be delivered.

    Returns the dead letter queue contents for monitoring/debugging.
    """
    from agent.callbacks import get_dead_letters as fetch_dead_letters
    return {"dead_letters": fetch_dead_letters()}


# --- Lock Status Endpoints ---

@router.get("/locks/status")
async def get_lock_status():
    """Get status of all deploy locks on this agent.

    Returns information about currently held locks including:
    - lab_id: The lab holding the lock
    - ttl: Remaining time-to-live in seconds
    - age_seconds: How long the lock has been held
    - is_stuck: Whether the lock exceeds the stuck threshold
    - owner: Agent ID that owns the lock

    Used by controller to detect and clean up stuck locks.
    """
    now = datetime.now(timezone.utc)
    lock_manager = _state.get_lock_manager()

    if lock_manager is None:
        return {"locks": [], "timestamp": now.isoformat(), "error": "Lock manager not initialized"}

    try:
        locks = await lock_manager.get_all_locks()
        # Add is_stuck flag based on controller threshold
        for lock in locks:
            lock["is_stuck"] = lock.get("age_seconds", 0) > settings.lock_stuck_threshold
        return {"locks": locks, "timestamp": now.isoformat()}
    except Exception as e:
        logger.error(f"Failed to get lock status: {e}")
        return {"locks": [], "timestamp": now.isoformat(), "error": str(e)}


@router.post("/locks/{lab_id}/release")
async def release_lock(lab_id: str):
    """Force release a stuck deploy lock for a lab.

    This uses Redis to forcibly release the lock, allowing new deploys
    to proceed immediately. The lock manager handles ownership checks
    and logs appropriate warnings.

    Returns:
        status: "cleared" if lock was released, "not_found" if no lock existed
    """
    lock_manager = _state.get_lock_manager()

    if lock_manager is None:
        return {"status": "error", "lab_id": lab_id, "error": "Lock manager not initialized"}

    try:
        # Force release via Redis
        released = await lock_manager.force_release(lab_id)

        # Also clear cached results
        _state._deploy_results.pop(lab_id, None)

        if released:
            logger.info(f"Force-released lock for lab {lab_id}")
            return {"status": "cleared", "lab_id": lab_id}
        else:
            return {"status": "not_found", "lab_id": lab_id}
    except Exception as e:
        logger.error(f"Failed to release lock for lab {lab_id}: {e}")
        return {"status": "error", "lab_id": lab_id, "error": str(e)}


# --- Agent Update Endpoint ---

@router.post("/update")
async def trigger_update(request: UpdateRequest) -> UpdateResponse:
    """Receive update command from controller.

    Detects deployment mode and initiates appropriate update procedure:
    - Systemd mode: git pull + pip install + systemctl restart
    - Docker mode: Reports back - controller handles container restart

    The agent reports progress via callbacks to the callback_url.
    """
    logger.info(f"Update request received: job={request.job_id}, target={request.target_version}")

    # Detect deployment mode
    mode = detect_deployment_mode()
    logger.info(f"Detected deployment mode: {mode.value}")

    if mode == DeploymentMode.SYSTEMD:
        # Start async update process
        asyncio.create_task(
            perform_systemd_update(
                job_id=request.job_id,
                agent_id=_state.AGENT_ID,
                target_version=request.target_version,
                callback_url=request.callback_url,
            )
        )
        return UpdateResponse(
            accepted=True,
            message="Update initiated",
            deployment_mode=mode.value,
        )

    elif mode == DeploymentMode.DOCKER:
        # Docker update needs external handling
        asyncio.create_task(
            perform_docker_update(
                job_id=request.job_id,
                agent_id=_state.AGENT_ID,
                target_version=request.target_version,
                callback_url=request.callback_url,
            )
        )
        return UpdateResponse(
            accepted=False,
            message="Docker deployment detected. Update must be performed externally.",
            deployment_mode=mode.value,
        )

    else:
        # Unknown deployment mode
        return UpdateResponse(
            accepted=False,
            message="Unknown deployment mode. Cannot perform automatic update.",
            deployment_mode=mode.value,
        )
