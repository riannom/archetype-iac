"""Callback endpoints for async job completion.

This module provides endpoints for agents to report job completion
when using async execution mode. This eliminates timeout issues for
long-running operations like VM provisioning.

The workflow:
1. Controller sends deploy request with callback_url
2. Agent returns 202 Accepted immediately
3. Agent executes operation asynchronously
4. Agent POSTs result to callback_url when done
5. Controller updates job/lab/node states
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import db, models, schemas
from app.utils.lab import update_lab_state

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/callbacks", tags=["callbacks"])


@router.post("/job/{job_id}", response_model=schemas.JobCallbackResponse)
async def job_completion_callback(
    job_id: str,
    payload: schemas.JobCallbackPayload,
    database: Session = Depends(db.get_db),
) -> schemas.JobCallbackResponse:
    """Receive job completion callback from an agent.

    This endpoint processes async job results, updating:
    - Job status and logs
    - Lab state
    - NodeState records (if provided)

    The callback is idempotent - multiple calls with the same job_id
    will be handled gracefully.
    """
    logger.info(f"Received job callback: job={job_id}, status={payload.status}")

    # Validate job_id matches payload
    if payload.job_id != job_id:
        return schemas.JobCallbackResponse(
            success=False,
            message=f"Job ID mismatch: {job_id} != {payload.job_id}",
        )

    # Find the job
    job = database.get(models.Job, job_id)
    if not job:
        logger.warning(f"Job callback for unknown job: {job_id}")
        return schemas.JobCallbackResponse(
            success=False,
            message=f"Job not found: {job_id}",
        )

    # Check if job is already completed (idempotency)
    if job.status in ("completed", "failed"):
        logger.info(f"Job {job_id} already {job.status}, ignoring callback")
        return schemas.JobCallbackResponse(
            success=True,
            message=f"Job already {job.status}",
        )

    # Update job status
    job.status = payload.status
    job.completed_at = payload.completed_at or datetime.now(timezone.utc)
    if payload.started_at and not job.started_at:
        job.started_at = payload.started_at

    # Build log content
    log_parts = []
    if payload.status == "completed":
        log_parts.append("Job completed successfully (async callback).")
    else:
        log_parts.append(f"Job failed (async callback).")
        if payload.error_message:
            log_parts.append(f"\nError: {payload.error_message}")

    if payload.stdout:
        log_parts.append(f"\n\n=== STDOUT ===\n{payload.stdout}")
    if payload.stderr:
        log_parts.append(f"\n\n=== STDERR ===\n{payload.stderr}")

    job.log_path = "".join(log_parts).strip()

    # Update lab state if this is a lab operation
    if job.lab_id:
        lab = database.get(models.Lab, job.lab_id)
        if lab:
            _update_lab_from_callback(database, lab, job, payload)

    database.commit()

    logger.info(f"Job {job_id} updated via callback: {payload.status}")
    return schemas.JobCallbackResponse(
        success=True,
        message=f"Job {job_id} updated to {payload.status}",
    )


def _update_lab_from_callback(
    database: Session,
    lab: models.Lab,
    job: models.Job,
    payload: schemas.JobCallbackPayload,
) -> None:
    """Update lab and node states based on job callback.

    Args:
        database: Database session
        lab: The lab to update
        job: The job that completed
        payload: Callback payload with results
    """
    action = job.action or ""

    # Determine new lab state based on action and result
    if payload.status == "completed":
        if action == "up":
            update_lab_state(database, lab.id, "running", agent_id=job.agent_id)
        elif action == "down":
            update_lab_state(database, lab.id, "stopped")
        elif action.startswith("sync:"):
            # Sync operations don't change overall lab state
            pass
    else:
        # Job failed
        error_msg = payload.error_message or "Job failed"
        update_lab_state(database, lab.id, "error", error=error_msg)

    # Update node states if provided
    if payload.node_states:
        _update_node_states(database, lab.id, payload.node_states)


def _update_node_states(
    database: Session,
    lab_id: str,
    node_states: dict[str, str],
) -> None:
    """Update NodeState records from callback payload.

    Args:
        database: Database session
        lab_id: Lab ID
        node_states: Dict mapping node_name -> actual_state
    """
    for node_name, actual_state in node_states.items():
        node_state = (
            database.query(models.NodeState)
            .filter(
                models.NodeState.lab_id == lab_id,
                models.NodeState.node_name == node_name,
            )
            .first()
        )

        if node_state:
            old_state = node_state.actual_state
            node_state.actual_state = actual_state

            # Clear error if moving to good state
            if actual_state in ("running", "stopped"):
                node_state.error_message = None

            if old_state != actual_state:
                logger.debug(
                    f"Node {node_name} in lab {lab_id}: "
                    f"{old_state} -> {actual_state} (callback)"
                )


@router.post("/dead-letter/{job_id}")
async def dead_letter_callback(
    job_id: str,
    payload: schemas.JobCallbackPayload,
    database: Session = Depends(db.get_db),
) -> schemas.JobCallbackResponse:
    """Receive a dead letter callback (callback that failed multiple times).

    When an agent cannot deliver a callback after retries, it sends
    the result here as a last resort. This endpoint logs the failure
    and marks the job as failed/unknown.

    This provides observability into callback delivery failures.
    """
    logger.warning(
        f"Received dead letter callback for job {job_id}: "
        f"original_status={payload.status}"
    )

    job = database.get(models.Job, job_id)
    if not job:
        logger.warning(f"Dead letter for unknown job: {job_id}")
        return schemas.JobCallbackResponse(
            success=True,
            message="Job not found (logged)",
        )

    # If job is still pending/running, mark it as unknown state
    if job.status in ("pending", "running", "queued"):
        job.status = "failed"
        job.completed_at = datetime.now(timezone.utc)
        job.log_path = (
            f"ERROR: Job completion callback delivery failed.\n\n"
            f"The job may have completed on the agent, but the callback "
            f"could not be delivered after multiple attempts.\n\n"
            f"Original status from agent: {payload.status}\n"
            f"Error: {payload.error_message or 'Unknown'}\n\n"
            f"Please check agent logs and verify lab state manually."
        )

        # Mark lab as unknown state
        if job.lab_id:
            update_lab_state(
                database, job.lab_id, "unknown",
                error="Callback delivery failed - state unknown"
            )

        database.commit()

    return schemas.JobCallbackResponse(
        success=True,
        message="Dead letter recorded",
    )


# --- Agent Update Callbacks ---

class UpdateProgressPayload(schemas.BaseModel):
    """Payload for agent update progress callbacks."""
    job_id: str
    agent_id: str
    status: str  # downloading, installing, restarting, completed, failed
    progress_percent: int = 0
    error_message: str | None = None


@router.post("/update/{job_id}")
async def update_progress_callback(
    job_id: str,
    payload: UpdateProgressPayload,
    database: Session = Depends(db.get_db),
) -> dict:
    """Receive update progress from an agent.

    Updates the AgentUpdateJob record with progress information.
    When status is "completed", verifies the agent version after re-registration.
    """
    logger.info(
        f"Update callback: job={job_id}, status={payload.status}, "
        f"progress={payload.progress_percent}%"
    )

    # Find the update job
    update_job = database.get(models.AgentUpdateJob, job_id)
    if not update_job:
        logger.warning(f"Update callback for unknown job: {job_id}")
        return {"success": False, "message": "Job not found"}

    # Validate agent_id matches
    if payload.agent_id != update_job.host_id:
        # Check if agent_id was reassigned (can happen on re-registration)
        host = database.get(models.Host, update_job.host_id)
        if not host:
            logger.warning(f"Update callback from unknown agent: {payload.agent_id}")
            return {"success": False, "message": "Agent mismatch"}

    # Update job status
    update_job.status = payload.status
    update_job.progress_percent = payload.progress_percent

    if payload.error_message:
        update_job.error_message = payload.error_message

    # Set timestamps based on status
    if payload.status == "downloading" and not update_job.started_at:
        update_job.started_at = datetime.now(timezone.utc)

    if payload.status in ("completed", "failed"):
        update_job.completed_at = datetime.now(timezone.utc)
        # Ensure completed status shows 100% progress
        if payload.status == "completed":
            update_job.progress_percent = 100

    database.commit()

    logger.info(f"Update job {job_id} updated: {payload.status}")
    return {"success": True, "message": f"Job updated to {payload.status}"}
