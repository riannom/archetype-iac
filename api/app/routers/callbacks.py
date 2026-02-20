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

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import db, models, schemas, agent_client
from app.agent_auth import verify_agent_secret
from app.utils.lab import update_lab_state
from app.tasks.live_links import create_link_if_ready, _build_host_to_agent_map
from app.services.link_operational_state import recompute_link_oper_state
from app.services.broadcaster import get_broadcaster
from app.services.interface_naming import normalize_interface

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/callbacks", tags=["callbacks"])


@router.post("/job/{job_id}", response_model=schemas.JobCallbackResponse)
async def job_completion_callback(
    job_id: str,
    payload: schemas.JobCallbackPayload,
    database: Session = Depends(db.get_db),
    _auth: None = Depends(verify_agent_secret),
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
        log_parts.append("Job failed (async callback).")
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
            await _update_lab_from_callback(database, lab, job, payload)

    database.commit()

    logger.info(f"Job {job_id} updated via callback: {payload.status}")
    return schemas.JobCallbackResponse(
        success=True,
        message=f"Job {job_id} updated to {payload.status}",
    )


async def _update_lab_from_callback(
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
        await _update_node_states(database, lab.id, payload.node_states)


async def _update_node_states(
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
    nodes_became_running: list[str] = []

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
                if actual_state == "running" and old_state != "running":
                    nodes_became_running.append(node_name)

    if nodes_became_running:
        try:
            await _auto_connect_pending_links(database, lab_id, nodes_became_running)
            await _auto_reattach_overlay_endpoints(database, lab_id, nodes_became_running)
        except Exception as e:
            logger.warning(f"Auto-connect pending links failed for lab {lab_id}: {e}")


async def _auto_connect_pending_links(
    database: Session,
    lab_id: str,
    node_names: list[str],
) -> None:
    """Attempt to connect pending links involving nodes that just became running."""
    host_to_agent = await _build_host_to_agent_map(database, lab_id)
    if not host_to_agent:
        return

    pending_links = (
        database.query(models.LinkState)
        .filter(
            models.LinkState.lab_id == lab_id,
            models.LinkState.desired_state == "up",
            models.LinkState.actual_state.in_(["pending", "unknown", "down", "error"]),
        )
        .all()
    )

    node_set = set(node_names)
    for ls in pending_links:
        if ls.source_node in node_set or ls.target_node in node_set:
            await create_link_if_ready(database, lab_id, ls, host_to_agent, skip_locked=True)


async def _auto_reattach_overlay_endpoints(
    database: Session,
    lab_id: str,
    node_names: list[str],
) -> None:
    """Re-attach overlay endpoints when nodes transition to running.

    Cross-host links can fail to attach if endpoints aren't ready yet.
    This replays the /overlay/attach step for any running endpoints to
    ensure the VXLAN bridge is wired on both sides.
    """
    host_to_agent = await _build_host_to_agent_map(database, lab_id)
    if not host_to_agent:
        return

    from app.services.link_manager import allocate_vni
    from app.routers.infrastructure import get_or_create_settings

    node_set = set(node_names)
    cross_links = (
        database.query(models.LinkState)
        .filter(
            models.LinkState.lab_id == lab_id,
            models.LinkState.desired_state == "up",
            models.LinkState.is_cross_host.is_(True),
        )
        .all()
    )

    infra = get_or_create_settings(database)
    overlay_mtu = infra.overlay_mtu or 0
    vni_updates = False

    for ls in cross_links:
        if ls.source_host_id not in host_to_agent or ls.target_host_id not in host_to_agent:
            continue

        agent_a = host_to_agent[ls.source_host_id]
        agent_b = host_to_agent[ls.target_host_id]

        vni = ls.vni or allocate_vni(lab_id, ls.link_name)
        if ls.vni is None:
            ls.vni = vni
            vni_updates = True

        local_ip_a = await agent_client.resolve_data_plane_ip(database, agent_a)
        local_ip_b = await agent_client.resolve_data_plane_ip(database, agent_b)

        if ls.source_node in node_set:
            await agent_client.attach_overlay_interface_on_agent(
                agent_a,
                lab_id=lab_id,
                container_name=ls.source_node,
                interface_name=ls.source_interface,
                vni=vni,
                local_ip=local_ip_a,
                remote_ip=local_ip_b,
                link_id=ls.link_name,
                tenant_mtu=overlay_mtu,
            )
        if ls.target_node in node_set:
            await agent_client.attach_overlay_interface_on_agent(
                agent_b,
                lab_id=lab_id,
                container_name=ls.target_node,
                interface_name=ls.target_interface,
                vni=vni,
                local_ip=local_ip_b,
                remote_ip=local_ip_a,
                link_id=ls.link_name,
                tenant_mtu=overlay_mtu,
            )

    if vni_updates:
        database.commit()


@router.post("/job/{job_id}/heartbeat")
def job_heartbeat(
    job_id: str,
    database: Session = Depends(db.get_db),
    _auth: None = Depends(verify_agent_secret),
) -> dict:
    """Receive heartbeat from agent for a running job.

    Agents call this periodically during long-running operations to prove
    the job is still making progress. This allows the job health monitor
    to distinguish between stuck jobs (no heartbeat) and slow jobs (active heartbeat).

    The heartbeat includes no payload - just updating the timestamp is enough.
    """
    job = database.get(models.Job, job_id)
    if not job:
        return {"success": False, "message": "Job not found"}

    if job.status not in ("running", "queued"):
        return {"success": True, "message": f"Job already {job.status}"}

    job.last_heartbeat = datetime.now(timezone.utc)
    database.commit()

    logger.debug(f"Heartbeat received for job {job_id}")
    return {"success": True, "message": "Heartbeat recorded"}


@router.post("/carrier-state")
async def carrier_state_changed(
    payload: schemas.CarrierStateChangeRequest,
    database: Session = Depends(db.get_db),
    _auth: None = Depends(verify_agent_secret),
) -> dict:
    """Agent reports that a container interface's carrier state changed.

    When a NOS shuts down an interface, the host-side veth carrier drops.
    The agent's CarrierMonitor detects this and reports it here.  This
    endpoint:
    1. Updates the matching carrier_state field in the LinkState record
    2. Propagates carrier on/off to the peer endpoint via the remote agent
    3. Recomputes link operational state
    4. Broadcasts the change via WebSocket
    """
    lab_id = payload.lab_id
    node = payload.node
    interface = payload.interface
    carrier = payload.carrier_state

    logger.info(
        "Carrier state change: lab=%s node=%s iface=%s carrier=%s",
        lab_id, node, interface, carrier,
    )

    normalized_interface = normalize_interface(interface) if interface else interface

    # Find the LinkState where this node:interface is either source or target.
    # Match on normalized interface names to handle vendor-form vs ethN drift.
    candidates = (
        database.query(models.LinkState)
        .filter(
            models.LinkState.lab_id == lab_id,
            (
                (models.LinkState.source_node == node)
                | (models.LinkState.target_node == node)
            ),
        )
        .all()
    )
    link_state = next(
        (
            ls for ls in candidates
            if (
                ls.source_node == node
                and normalize_interface(ls.source_interface) == normalized_interface
            ) or (
                ls.target_node == node
                and normalize_interface(ls.target_interface) == normalized_interface
            )
        ),
        None,
    )

    if not link_state:
        logger.warning(
            "No LinkState found for %s:%s in lab %s", node, interface, lab_id,
        )
        return {"success": False, "message": "Link not found"}

    # Determine which side matched and update carrier_state
    is_source = (
        link_state.source_node == node
        and normalize_interface(link_state.source_interface) == normalized_interface
    )

    if is_source:
        link_state.source_carrier_state = carrier
        peer_node = link_state.target_node
        peer_interface = link_state.target_interface
        peer_host_id = link_state.target_host_id
    else:
        link_state.target_carrier_state = carrier
        peer_node = link_state.source_node
        peer_interface = link_state.source_interface
        peer_host_id = link_state.source_host_id

    # Propagate carrier to the peer endpoint via its agent
    if peer_host_id and peer_node and peer_interface:
        peer_agent = database.get(models.Host, peer_host_id)
        if peer_agent and agent_client.is_agent_online(peer_agent):
            try:
                url = (
                    f"http://{peer_agent.address}/labs/{lab_id}"
                    f"/interfaces/{peer_node}/{peer_interface}/carrier"
                )
                client = agent_client.get_http_client()
                response = await client.post(
                    url,
                    json={"state": carrier},
                    timeout=10.0,
                )
                if response.status_code == 200:
                    # Update peer's carrier_state in DB too
                    if is_source:
                        link_state.target_carrier_state = carrier
                    else:
                        link_state.source_carrier_state = carrier
                    logger.info(
                        "Carrier %s propagated to peer %s:%s",
                        carrier, peer_node, peer_interface,
                    )
                else:
                    logger.warning(
                        "Carrier propagation to peer %s:%s failed: HTTP %d",
                        peer_node, peer_interface, response.status_code,
                    )
            except Exception as e:
                logger.warning(
                    "Carrier propagation to peer %s:%s failed: %s",
                    peer_node, peer_interface, e,
                )
        else:
            logger.warning(
                "Peer agent %s offline, skipping carrier propagation", peer_host_id,
            )

    # Recompute operational state
    changed = recompute_link_oper_state(database, link_state)

    database.commit()

    # Broadcast via WebSocket if state changed
    if changed:
        try:
            broadcaster = get_broadcaster()
            await broadcaster.publish_link_state(
                lab_id=lab_id,
                link_name=link_state.link_name,
                desired_state=link_state.desired_state,
                actual_state=link_state.actual_state,
                source_node=link_state.source_node,
                target_node=link_state.target_node,
                source_oper_state=link_state.source_oper_state,
                target_oper_state=link_state.target_oper_state,
                source_oper_reason=link_state.source_oper_reason,
                target_oper_reason=link_state.target_oper_reason,
                oper_epoch=link_state.oper_epoch,
            )
        except Exception as e:
            logger.warning("Failed to broadcast link state change: %s", e)

    return {"success": True, "message": f"Carrier {carrier} processed for {node}:{interface}"}


@router.post("/dead-letter/{job_id}")
def dead_letter_callback(
    job_id: str,
    payload: schemas.JobCallbackPayload,
    database: Session = Depends(db.get_db),
    _auth: None = Depends(verify_agent_secret),
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
def update_progress_callback(
    job_id: str,
    payload: UpdateProgressPayload,
    database: Session = Depends(db.get_db),
    _auth: None = Depends(verify_agent_secret),
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
