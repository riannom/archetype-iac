"""Agent registration and management endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app import db, models
from app.agent_auth import verify_agent_secret
from app.auth import get_current_admin, get_current_user
from app.config import settings
from app.metrics import record_agent_stale_image_cleanup, set_agent_stale_image_count
from app.routers.system import get_commit
from app.state import HostStatus, JobStatus, LabState, LinkActualState
from app.utils.http import require_admin
from app.utils.time import utcnow


router = APIRouter(prefix="/agents", tags=["agents"])
logger = logging.getLogger(__name__)

_ACTIVE_UPDATE_STATUSES = ("pending", "downloading", "installing", "restarting")


async def verify_agent_secret_required(request: Request) -> None:
    """Require a bearer header, then verify token when a secret is configured."""
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing agent authorization",
        )
    await verify_agent_secret(request)


def get_latest_agent_version() -> str:
    """Get the latest available agent version.

    Reads from the root VERSION file (same as controller version).

    Returns:
        Version string (e.g., "0.4.0")
    """
    from app.routers.system import get_version
    return get_version()


def _get_agent_auth_headers() -> dict[str, str]:
    """Compatibility wrapper for tests monkeypatching app.routers.agents."""
    from app.agent_client import _get_agent_auth_headers as _agent_auth_headers

    return _agent_auth_headers()


def _get_agent_or_404(database: Session, agent_id: str) -> models.Host:
    host = database.get(models.Host, agent_id)
    if not host:
        raise HTTPException(status_code=404, detail="Agent not found")
    return host


def _require_agent_online(host: models.Host) -> None:
    if host.status != HostStatus.ONLINE:
        raise HTTPException(status_code=503, detail="Agent is offline")


def _resolve_checkout_ref(target_version: str) -> str:
    if re.fullmatch(r'[0-9a-f]{7,40}', target_version):
        return target_version
    return get_commit()


def _update_host_fields(
    database: Session, host: models.Host, agent: AgentInfo
) -> None:
    previous_snapshotter_mode = host.docker_snapshotter_mode
    host.name = agent.name
    host.address = agent.address
    host.status = HostStatus.ONLINE
    host.capabilities = json.dumps(agent.capabilities.model_dump())
    host.version = agent.version
    host.git_sha = agent.commit or host.git_sha
    host.started_at = agent.started_at
    host.is_local = agent.is_local
    host.deployment_mode = agent.deployment_mode or host.deployment_mode
    host.last_heartbeat = utcnow()
    host.data_plane_address = getattr(agent, "data_plane_ip", None)
    host.docker_snapshotter_mode = getattr(agent, "docker_snapshotter_mode", None)
    _invalidate_host_images_for_snapshotter_change(
        database, host, previous_snapshotter_mode, host.docker_snapshotter_mode,
    )


def _host_to_out(host: models.Host) -> HostOut:
    return HostOut(
        id=host.id,
        name=host.name,
        address=host.address,
        status=host.status,
        capabilities=host.get_capabilities(),
        version=host.version,
        git_sha=host.git_sha,
        image_sync_strategy=host.image_sync_strategy or "on_demand",
        last_heartbeat=host.last_heartbeat,
        last_error=host.last_error,
        error_since=host.error_since,
        data_plane_address=host.data_plane_address,
        docker_snapshotter_mode=host.docker_snapshotter_mode,
        created_at=host.created_at,
    )


def _update_job_to_status(job: models.AgentUpdateJob, agent_id: str) -> UpdateStatusResponse:
    return UpdateStatusResponse(
        job_id=job.id,
        agent_id=agent_id,
        from_version=job.from_version,
        to_version=job.to_version,
        status=job.status,
        progress_percent=job.progress_percent,
        error_message=job.error_message,
        started_at=job.started_at,
        completed_at=job.completed_at,
        created_at=job.created_at,
    )


def _find_active_update_job(
    database: Session, agent_id: str,
) -> models.AgentUpdateJob | None:
    return (
        database.query(models.AgentUpdateJob)
        .filter(
            models.AgentUpdateJob.host_id == agent_id,
            models.AgentUpdateJob.status.in_(_ACTIVE_UPDATE_STATUSES),
        )
        .first()
    )


def _create_update_job(
    database: Session, agent_id: str, from_version: str, to_version: str,
) -> tuple[str, models.AgentUpdateJob]:
    job_id = str(uuid4())
    job = models.AgentUpdateJob(
        id=job_id,
        host_id=agent_id,
        from_version=from_version,
        to_version=to_version,
        status="pending",
    )
    database.add(job)
    return job_id, job


async def _send_update_to_agent(
    address: str, job_id: str, checkout_ref: str,
) -> dict:
    callback_url = f"{settings.internal_url}/callbacks/update/{job_id}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"http://{address}/update",
            json={
                "job_id": job_id,
                "target_version": checkout_ref,
                "callback_url": callback_url,
            },
            headers=_get_agent_auth_headers(),
        )
        resp.raise_for_status()
        return resp.json()


def _fail_update_job(
    database: Session, job: models.AgentUpdateJob, error: str,
) -> None:
    job.status = JobStatus.FAILED
    job.error_message = error
    job.completed_at = utcnow()
    database.commit()


def _image_host_to_dict(ih: models.ImageHost) -> dict:
    return {
        "image_id": ih.image_id,
        "reference": ih.reference,
        "status": ih.status,
        "size_bytes": ih.size_bytes,
        "synced_at": ih.synced_at.isoformat() if ih.synced_at else None,
        "error_message": ih.error_message,
    }


def _extract_inventory_error(details: dict) -> str | None:
    return next(
        (
            str(entry.get("reason"))
            for entry in details["stale_images"]
            if entry.get("reference") == "" and entry.get("reason")
        ),
        None,
    )


async def _proxy_agent_get(host: models.Host, path: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{host.address}/{path}", headers=_get_agent_auth_headers()
            )
            response.raise_for_status()
            return response.json()
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Failed to contact agent: {e}")
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=e.response.status_code, detail=f"Agent error: {e}"
        )


# --- Request/Response Schemas ---

class AgentCapabilities(BaseModel):
    """What the agent can do."""
    providers: list[str] = Field(default_factory=list)
    max_concurrent_jobs: int = 4
    features: list[str] = Field(default_factory=list)
    virtualization: dict | None = Field(default_factory=dict)


class AgentInfo(BaseModel):
    """Agent identification and capabilities."""
    agent_id: str
    name: str
    address: str
    capabilities: AgentCapabilities
    version: str = "0.1.0"
    commit: str = ""
    deployment_mode: str = "unknown"
    started_at: datetime | None = None  # When the agent process started
    is_local: bool = False  # True if co-located with controller (enables rebuild)
    data_plane_ip: str | None = None
    docker_snapshotter_mode: str | None = None


class RegistrationRequest(BaseModel):
    """Agent -> Controller: Register this agent."""
    agent: AgentInfo
    token: str | None = None


class RegistrationResponse(BaseModel):
    """Controller -> Agent: Registration result."""
    success: bool
    message: str = ""
    assigned_id: str | None = None


class HeartbeatRequest(BaseModel):
    """Agent -> Controller: I'm still alive."""
    agent_id: str
    status: str = "online"
    active_jobs: int = 0
    resource_usage: dict = Field(default_factory=dict)
    data_plane_ip: str | None = None
    docker_snapshotter_mode: str | None = None


class HeartbeatResponse(BaseModel):
    """Controller -> Agent: Acknowledged."""
    acknowledged: bool
    pending_jobs: list[str] = Field(default_factory=list)


class HostOut(BaseModel):
    """Host info for API responses."""
    id: str
    name: str
    address: str
    status: str
    capabilities: dict
    version: str
    git_sha: str | None = None
    image_sync_strategy: str = "on_demand"
    last_heartbeat: datetime | None
    # Error tracking fields
    last_error: str | None = None
    error_since: datetime | None = None
    # Data plane address for VXLAN tunnels (separate from management address)
    data_plane_address: str | None = None
    docker_snapshotter_mode: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DashboardMetrics(BaseModel):
    """System-wide metrics for dashboard display."""
    agents: dict  # {"online": int, "total": int}
    containers: dict  # {"running": int, "total": int}
    cpu_percent: float
    memory_percent: float
    labs_running: int
    labs_total: int


def _invalidate_host_images_for_snapshotter_change(
    database: Session,
    host: models.Host,
    previous_mode: str | None,
    new_mode: str | None,
) -> int:
    """Mark host image presence missing when Docker image-store mode changes."""
    if not new_mode or previous_mode == new_mode:
        return 0

    updated = (
        database.query(models.ImageHost)
        .filter(
            models.ImageHost.host_id == host.id,
            models.ImageHost.status != "syncing",
            models.ImageHost.status != "missing",
        )
        .update(
            {
                models.ImageHost.status: "missing",
                models.ImageHost.error_message: (
                    f"Docker snapshotter mode changed: {previous_mode or 'unknown'} -> {new_mode}"
                ),
            },
            synchronize_session=False,
        )
    )
    if updated:
        logger.warning(
            "Host %s snapshotter mode changed: %s -> %s; invalidated %s image record(s)",
            host.name,
            previous_mode or "unknown",
            new_mode,
            updated,
        )
    return int(updated)


# --- Endpoints ---

@router.post("/register", response_model=RegistrationResponse)
async def register_agent(
    request: RegistrationRequest,
    _auth: None = Depends(verify_agent_secret_required),
) -> RegistrationResponse:
    """Register a new agent or update existing registration.

    Prevents duplicate agents by checking name and address.
    If an agent with the same name or address already exists,
    updates that record instead of creating a new one.

    When an agent restarts (detected by new started_at timestamp),
    any running jobs on that agent are marked as failed since the
    agent lost execution context.

    After registration, triggers image reconciliation in the background
    to sync ImageHost records with actual agent inventory.
    """
    agent = request.agent

    # Phase 1: All DB registration + cleanup in a worker thread
    def _sync_register():
        from app.db import get_session

        with get_session() as database:
            host_id = None
            is_new_registration = False
            is_restart = False

            existing = database.get(models.Host, agent.agent_id)

            if existing:
                if existing.started_at and agent.started_at:
                    if agent.started_at > existing.started_at:
                        is_restart = True

                _update_host_fields(database, existing, agent)
                database.commit()
                host_id = agent.agent_id

                response = RegistrationResponse(
                    success=True,
                    message="Agent re-registered",
                    assigned_id=agent.agent_id,
                )
            else:
                existing_duplicate = (
                    database.query(models.Host)
                    .filter(or_(
                        models.Host.name == agent.name,
                        models.Host.address == agent.address,
                    ))
                    .first()
                )

                if existing_duplicate:
                    _update_host_fields(database, existing_duplicate, agent)
                    database.commit()
                    host_id = existing_duplicate.id

                    response = RegistrationResponse(
                        success=True,
                        message="Agent re-registered (updated existing record)",
                        assigned_id=existing_duplicate.id,
                    )
                else:
                    host = models.Host(
                        id=agent.agent_id,
                        name=agent.name,
                        address=agent.address,
                        status=HostStatus.ONLINE,
                        capabilities=json.dumps(agent.capabilities.model_dump()),
                        version=agent.version,
                        git_sha=agent.commit or None,
                        started_at=agent.started_at,
                        is_local=agent.is_local,
                        deployment_mode=agent.deployment_mode or "unknown",
                        last_heartbeat=utcnow(),
                        data_plane_address=getattr(agent, "data_plane_ip", None),
                        docker_snapshotter_mode=getattr(agent, "docker_snapshotter_mode", None),
                    )
                    database.add(host)
                    database.commit()
                    host_id = agent.agent_id
                    is_new_registration = True

                    response = RegistrationResponse(
                        success=True,
                        message="Agent registered",
                        assigned_id=agent.agent_id,
                    )

            # Handle restart cleanup in same session (pure DB)
            if is_restart and host_id:
                _handle_agent_restart_cleanup_sync(database, host_id)

            # Check update completion in same session (pure DB)
            if host_id:
                _check_update_completion(database, host_id, agent.version, agent.commit)

            return {
                "response": response,
                "host_id": host_id,
                "is_new_registration": is_new_registration,
            }

    result = await asyncio.to_thread(_sync_register)
    host_id = result["host_id"]

    # Phase 2: Fire-and-forget async tasks (no DB session held)
    if host_id:
        from app.tasks.image_sync import reconcile_agent_images, pull_images_on_registration

        asyncio.create_task(reconcile_agent_images(host_id))

        if result["is_new_registration"]:
            asyncio.create_task(pull_images_on_registration(host_id))

    # Trigger overlay convergence (already uses its own session)
    if host_id:
        async def _converge_agent():
            try:
                from app.db import get_session
                from app.tasks.link_reconciliation import (
                    run_overlay_convergence,
                    run_cross_host_port_convergence,
                    refresh_interface_mappings,
                    run_same_host_convergence,
                )
                from app.tasks.migration_cleanup import process_pending_migration_cleanups_for_agent
                with get_session() as sess:
                    ag = sess.get(models.Host, host_id)
                    if ag and ag.status == HostStatus.ONLINE:
                        host_map = {ag.id: ag}
                        await run_overlay_convergence(sess, host_map)
                        await refresh_interface_mappings(sess, host_map)
                        await run_cross_host_port_convergence(sess, host_map)
                        await run_same_host_convergence(sess, host_map)
                        await process_pending_migration_cleanups_for_agent(sess, ag, limit=50)
                        sess.commit()
            except Exception as e:
                logger.warning(f"Post-registration convergence for {host_id}: {e}")

        asyncio.create_task(_converge_agent())

    return result["response"]


def _handle_agent_restart_cleanup_sync(database: Session, agent_id: str) -> None:
    """Handle cleanup when an agent restarts (synchronous, uses caller's session).

    When an agent restarts, any jobs that were running on it are now
    orphaned since the agent lost its execution context. This function:
    1. Finds all running jobs assigned to this agent
    2. Marks them as failed with appropriate error message
    3. Updates associated lab state if needed
    4. Marks cross-host links for recovery

    Args:
        database: Database session (caller manages lifecycle)
        agent_id: ID of the restarted agent
    """
    stale_jobs = (
        database.query(models.Job)
        .filter(
            models.Job.agent_id == agent_id,
            models.Job.status == JobStatus.RUNNING,
        )
        .all()
    )

    if stale_jobs:
        logger.warning(
            f"Agent {agent_id} restarted - marking {len(stale_jobs)} running jobs as failed"
        )

        now = utcnow()
        for job in stale_jobs:
            job.status = JobStatus.FAILED
            job.completed_at = now
            job.log_path = (job.log_path or "") + "\n--- Agent restarted, job terminated ---"

            logger.info(f"Marked job {job.id} (action={job.action}) as failed due to agent restart")

            if job.lab_id and job.action in ("up", "down"):
                lab = database.get(models.Lab, job.lab_id)
                if lab:
                    lab.state = LabState.ERROR
                    lab.state_error = f"Job {job.action} failed: agent restarted during execution"
                    lab.state_updated_at = now
                    logger.info(f"Set lab {job.lab_id} state to error due to agent restart")

        database.commit()

    # Mark cross-host links for recovery (pure DB)
    _mark_links_for_recovery_sync(database, agent_id)


def _mark_links_for_recovery_sync(database: Session, agent_id: str) -> None:
    """Mark cross-host links as needing recovery after agent restart.

    When an agent restarts, its VXLAN overlay state is lost. This function:
    1. Finds all cross-host links where this agent hosts either endpoint
    2. Marks them as "error" state with appropriate message
    3. Clears the VXLAN attachment flag for this agent's side

    The periodic link reconciliation will then detect these links and
    re-establish connectivity by re-attaching the affected endpoints.

    Args:
        database: Database session (caller manages lifecycle)
        agent_id: ID of the restarted agent
    """
    links = (
        database.query(models.LinkState)
        .filter(
            models.LinkState.actual_state == LinkActualState.UP,
            models.LinkState.is_cross_host,
            or_(
                models.LinkState.source_host_id == agent_id,
                models.LinkState.target_host_id == agent_id,
            ),
        )
        .all()
    )

    if not links:
        logger.debug(f"No cross-host links to recover for agent {agent_id}")
        return

    for link in links:
        link.actual_state = LinkActualState.ERROR
        link.error_message = "Agent restarted, pending recovery"

        if link.source_host_id == agent_id:
            link.source_vxlan_attached = False
        if link.target_host_id == agent_id:
            link.target_vxlan_attached = False

    database.commit()

    logger.info(
        f"Marked {len(links)} cross-host links for recovery after agent {agent_id} restart"
    )


def _check_update_completion(
    database: Session,
    agent_id: str,
    new_version: str,
    new_commit: str,
) -> None:
    """Check if agent re-registration completes an in-progress update job.

    When an agent re-registers after an update, its new version/commit should
    match the update job target. If so, mark the job as completed.
    Also sweeps any stuck "restarting" jobs older than 10 minutes.
    """
    active_jobs = (
        database.query(models.AgentUpdateJob)
        .filter(
            models.AgentUpdateJob.host_id == agent_id,
            models.AgentUpdateJob.status.in_(_ACTIVE_UPDATE_STATUSES),
        )
        .order_by(models.AgentUpdateJob.created_at.desc())
        .all()
    )

    now = utcnow()
    for job in active_jobs:
        # Check version match: exact version match or commit SHA prefix match
        version_match = new_version == job.to_version
        commit_match = (
            new_commit
            and new_commit != "unknown"
            and job.to_version
            and new_commit.startswith(job.to_version)
        )

        if version_match or commit_match:
            job.status = JobStatus.COMPLETED
            job.progress_percent = 100
            job.completed_at = now
            logger.info(
                f"Update job {job.id} completed: agent re-registered with "
                f"version={new_version} commit={new_commit[:8] if new_commit else 'N/A'}"
            )
        else:
            # Agent re-registered but version doesn't match — expire if stale
            started = job.started_at or job.created_at
            if started and started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            age = (now - started).total_seconds() if started else 0
            if age > 300:  # 5 minutes
                job.status = JobStatus.FAILED
                job.error_message = (
                    f"Expired: agent re-registered with version={new_version} "
                    f"but job expected {job.to_version} (stuck {int(age)}s in '{job.status}')"
                )
                job.completed_at = now
                logger.warning(
                    f"Update job {job.id} expired: agent has "
                    f"version={new_version} commit={new_commit[:8] if new_commit else 'N/A'}, "
                    f"expected {job.to_version}"
                )

    if active_jobs:
        database.commit()


@router.post("/{agent_id}/heartbeat", response_model=HeartbeatResponse)
def heartbeat(
    agent_id: str,
    request: HeartbeatRequest,
    database: Session = Depends(db.get_db),
    _auth: None = Depends(verify_agent_secret_required),
) -> HeartbeatResponse:
    """Receive heartbeat from agent."""
    host = database.get(models.Host, agent_id)
    if not host:
        raise HTTPException(status_code=404, detail="Agent not registered")

    # Update status and resource usage
    host.status = request.status
    host.resource_usage = json.dumps(request.resource_usage)
    host.last_heartbeat = utcnow()
    # Update data plane address if agent reports one
    if request.data_plane_ip is not None:
        host.data_plane_address = request.data_plane_ip or None
    previous_snapshotter_mode = host.docker_snapshotter_mode
    if request.docker_snapshotter_mode is not None:
        host.docker_snapshotter_mode = request.docker_snapshotter_mode or None
    _invalidate_host_images_for_snapshotter_change(
        database,
        host,
        previous_snapshotter_mode,
        host.docker_snapshotter_mode,
    )
    database.commit()

    pending_jobs: list[str] = []

    return HeartbeatResponse(
        acknowledged=True,
        pending_jobs=pending_jobs,
    )


@router.get("", response_model=list[HostOut])
def list_agents(
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> list[HostOut]:
    """List all registered agents."""
    hosts = database.query(models.Host).order_by(models.Host.name).all()
    return [_host_to_out(host) for host in hosts]


def _enrich_details(
    details: list[dict],
    labs_by_id: dict[str, str],
    labs_by_prefix: dict[str, tuple[str, str]],
) -> list[dict]:
    """Enrich container/VM detail entries with lab_id and lab_name."""
    from app.utils.lab import find_lab_with_name
    enriched = []
    for entry in details:
        entry = dict(entry)  # copy to avoid mutating cached data
        lab_id, lab_name = find_lab_with_name(
            entry.get("lab_prefix", ""), labs_by_id, labs_by_prefix
        )
        entry["lab_id"] = lab_id
        entry["lab_name"] = lab_name
        enriched.append(entry)
    return enriched


@router.get("/detailed")
def list_agents_detailed(
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> list[dict]:
    """List all agents with full details including resource usage, role, and labs.

    Role is determined by:
    - "agent": Has docker or libvirt provider capabilities
    - "controller": Has no provider capabilities (controller-only host)
    - "agent+controller": Has provider capabilities AND is the same host as controller
    """
    hosts = database.query(models.Host).order_by(models.Host.name).all()

    # Get labs to associate with hosts
    all_labs = database.query(models.Lab).all()
    labs_by_agent: dict[str, list[dict]] = {}
    for lab in all_labs:
        if lab.agent_id:
            labs_by_agent.setdefault(lab.agent_id, []).append({
                "id": lab.id,
                "name": lab.name,
                "state": lab.state,
            })

    # Build lab lookup maps for container/VM enrichment
    labs_by_id = {lab.id: lab.name for lab in all_labs}
    labs_by_prefix = {lab.id[:20]: (lab.id, lab.name) for lab in all_labs}

    # Query ImageHost records grouped by host_id
    image_hosts = database.query(models.ImageHost).filter(
        models.ImageHost.status.in_(["synced", "syncing", "failed"])
    ).all()
    images_by_host: dict[str, list[dict]] = {}
    for ih in image_hosts:
        images_by_host.setdefault(ih.host_id, []).append(_image_host_to_dict(ih))

    result = []
    for host in hosts:
        capabilities = host.get_capabilities()
        resource_usage = host.get_resource_usage()

        # Determine role based on capabilities and is_local flag
        if capabilities.get("providers"):
            role = "agent+controller" if host.is_local else "agent"
        else:
            role = "controller"

        # Get labs for this host
        host_labs = labs_by_agent.get(host.id, [])

        result.append({
            "id": host.id,
            "name": host.name,
            "address": host.address,
            "status": host.status,
            "version": host.version,
            "git_sha": host.git_sha,
            "role": role,
            "capabilities": capabilities,
            "resource_usage": {
                "cpu_percent": resource_usage.get("cpu_percent", 0),
                "memory_percent": resource_usage.get("memory_percent", 0),
                "memory_used_gb": resource_usage.get("memory_used_gb", 0),
                "memory_total_gb": resource_usage.get("memory_total_gb", 0),
                "storage_percent": resource_usage.get("disk_percent", 0),
                "storage_used_gb": resource_usage.get("disk_used_gb", 0),
                "storage_total_gb": resource_usage.get("disk_total_gb", 0),
                "containers_running": resource_usage.get("containers_running", 0),
                "containers_total": resource_usage.get("containers_total", 0),
                "vms_running": resource_usage.get("vms_running", 0),
                "vms_total": resource_usage.get("vms_total", 0),
                "container_details": _enrich_details(
                    resource_usage.get("container_details", []),
                    labs_by_id, labs_by_prefix,
                ),
                "vm_details": _enrich_details(
                    resource_usage.get("vm_details", []),
                    labs_by_id, labs_by_prefix,
                ),
            },
            "images": images_by_host.get(host.id, []),
            "labs": host_labs,
            "lab_count": len(host_labs),
            "started_at": host.started_at.isoformat() if host.started_at else None,
            "last_heartbeat": host.last_heartbeat.isoformat() if host.last_heartbeat else None,
            "image_sync_strategy": host.image_sync_strategy or "on_demand",
            "deployment_mode": host.deployment_mode or "unknown",
            "is_local": host.is_local,
            # Error tracking
            "last_error": host.last_error,
            "error_since": host.error_since.isoformat() if host.error_since else None,
            # Data plane
            "data_plane_address": host.data_plane_address,
        })

    return result


@router.get("/{agent_id}", response_model=HostOut)
def get_agent(
    agent_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> HostOut:
    """Get details of a specific agent."""
    host = _get_agent_or_404(database, agent_id)
    return _host_to_out(host)


@router.get("/{agent_id}/deregister-info")
def get_deregister_info(
    agent_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_admin),
) -> dict:
    """Get pre-flight information before deregistering an agent.

    Returns counts of affected resources so the UI can show an
    informed confirmation dialog.
    """

    host = _get_agent_or_404(database, agent_id)

    # Count affected resources
    labs = (
        database.query(models.Lab)
        .filter(models.Lab.agent_id == agent_id)
        .all()
    )
    running_labs = [{"id": lab.id, "name": lab.name, "state": lab.state} for lab in labs if lab.state in (LabState.RUNNING, LabState.STARTING)]

    node_placements = (
        database.query(models.NodePlacement)
        .filter(models.NodePlacement.host_id == agent_id)
        .count()
    )

    vxlan_tunnels = (
        database.query(models.VxlanTunnel)
        .filter(or_(
            models.VxlanTunnel.agent_a_id == agent_id,
            models.VxlanTunnel.agent_b_id == agent_id,
        ))
        .count()
    )

    cross_host_links = (
        database.query(models.LinkState)
        .filter(or_(
            models.LinkState.source_host_id == agent_id,
            models.LinkState.target_host_id == agent_id,
        ))
        .count()
    )

    nodes_assigned = (
        database.query(models.Node)
        .filter(models.Node.host_id == agent_id)
        .count()
    )

    return {
        "agent_id": agent_id,
        "agent_name": host.name,
        "agent_status": host.status,
        "labs_assigned": len(labs),
        "running_labs": running_labs,
        "node_placements": node_placements,
        "nodes_assigned": nodes_assigned,
        "vxlan_tunnels": vxlan_tunnels,
        "cross_host_links": cross_host_links,
    }


@router.delete("/{agent_id}")
def unregister_agent(
    agent_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """Unregister an agent and clean up all database references.

    This does NOT contact the agent or stop containers. It only removes
    the agent record and cleans up foreign key references so the database
    remains consistent. Topology data (nodes, links, configs) is preserved.

    Requires admin access.
    """
    require_admin(current_user)

    host = _get_agent_or_404(database, agent_id)
    host_name = host.name
    cleanup = {}

    # 1. SET NULL on Lab.agent_id
    count = (
        database.query(models.Lab)
        .filter(models.Lab.agent_id == agent_id)
        .update({models.Lab.agent_id: None})
    )
    cleanup["labs_unassigned"] = count

    # 2. SET NULL on Job.agent_id
    count = (
        database.query(models.Job)
        .filter(models.Job.agent_id == agent_id)
        .update({models.Job.agent_id: None})
    )
    cleanup["jobs_unassigned"] = count

    # 3. SET NULL on Node.host_id
    count = (
        database.query(models.Node)
        .filter(models.Node.host_id == agent_id)
        .update({models.Node.host_id: None})
    )
    cleanup["nodes_unassigned"] = count

    # 4. DELETE NodePlacement rows
    count = (
        database.query(models.NodePlacement)
        .filter(models.NodePlacement.host_id == agent_id)
        .delete()
    )
    cleanup["node_placements_deleted"] = count

    # 5. Clean up LinkState references (SET NULL + clear cross-host flags)
    source_links = (
        database.query(models.LinkState)
        .filter(models.LinkState.source_host_id == agent_id)
        .update({
            models.LinkState.source_host_id: None,
            models.LinkState.is_cross_host: False,
            models.LinkState.source_vxlan_attached: False,
        })
    )
    target_links = (
        database.query(models.LinkState)
        .filter(models.LinkState.target_host_id == agent_id)
        .update({
            models.LinkState.target_host_id: None,
            models.LinkState.is_cross_host: False,
            models.LinkState.target_vxlan_attached: False,
        })
    )
    cleanup["link_states_cleaned"] = source_links + target_links

    # 6. DELETE VxlanTunnel rows
    count = (
        database.query(models.VxlanTunnel)
        .filter(or_(
            models.VxlanTunnel.agent_a_id == agent_id,
            models.VxlanTunnel.agent_b_id == agent_id,
        ))
        .delete()
    )
    cleanup["vxlan_tunnels_deleted"] = count

    # 7. DELETE the host (cascading FKs handle ImageHost, ImageSyncJob,
    #    AgentUpdateJob, AgentLink, AgentNetworkConfig automatically)
    database.delete(host)
    database.commit()

    logger.info(f"Deregistered agent '{host_name}' ({agent_id}): {cleanup}")

    return {
        "status": "deleted",
        "agent_name": host_name,
        "cleanup": cleanup,
    }


class UpdateSyncStrategyRequest(BaseModel):
    """Request to update agent's image sync strategy."""
    strategy: str  # push, pull, on_demand, disabled


@router.put("/{agent_id}/sync-strategy")
def update_sync_strategy(
    agent_id: str,
    request: UpdateSyncStrategyRequest,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_admin),
) -> dict:
    """Update an agent's image synchronization strategy.

    Valid strategies:
    - push: Receive images immediately when uploaded to controller
    - pull: Pull missing images when agent comes online
    - on_demand: Sync only when deployment requires an image
    - disabled: No automatic sync, manual only
    """
    valid_strategies = {"push", "pull", "on_demand", "disabled"}
    if request.strategy not in valid_strategies:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid strategy. Must be one of: {', '.join(valid_strategies)}"
        )

    host = _get_agent_or_404(database, agent_id)
    host.image_sync_strategy = request.strategy
    database.commit()

    return {
        "agent_id": agent_id,
        "strategy": request.strategy,
        "message": f"Sync strategy updated to '{request.strategy}'"
    }


@router.get("/{agent_id}/images")
async def list_agent_images(
    agent_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """Get image sync status for all library images on an agent.

    Returns the status of each library image on this specific agent,
    including whether it's synced, missing, or in progress.
    """
    host = _get_agent_or_404(database, agent_id)
    return await _build_agent_image_details(host, database)


async def _build_agent_image_details(host: models.Host, database: Session) -> dict:
    """Build tracked and live inventory state for a single agent."""
    # Get all ImageHost records for this agent
    image_hosts = database.query(models.ImageHost).filter(
        models.ImageHost.host_id == host.id
    ).all()

    result = [_image_host_to_dict(ih) for ih in image_hosts]

    from app import agent_client
    from app.image_store import load_manifest
    from app.tasks.image_reconciliation import _active_image_references_by_host

    manifest = load_manifest()
    catalog_refs = {
        img.get("reference")
        for img in manifest.get("images", [])
        if img.get("reference")
    }
    active_refs_by_host = _active_image_references_by_host(database)
    keep_refs = set(catalog_refs)
    keep_refs.update(active_refs_by_host.get(host.id, set()))
    tracked_by_reference = {ih.reference: ih for ih in image_hosts if ih.reference}

    inventory: list[dict[str, object]] = []
    stale_images: list[dict[str, object]] = []
    inventory_refreshed_at: str | None = None
    if host.status == HostStatus.ONLINE and agent_client.is_agent_online(host):
        try:
            images_response = await agent_client.get_agent_images(host)
            inventory_refreshed_at = utcnow().isoformat()
            for img_info in images_response.get("images", []):
                candidates: list[str] = []
                reference = img_info.get("reference")
                if isinstance(reference, str) and reference:
                    candidates.append(reference)
                for tag in img_info.get("tags", []):
                    if isinstance(tag, str) and tag:
                        candidates.append(tag)

                kind = img_info.get("kind") or ("docker" if img_info.get("tags") else "file")
                in_use = img_info.get("in_use", False)
                for candidate in candidates:
                    if "<none>" in candidate:
                        continue
                    tracked = tracked_by_reference.get(candidate)
                    is_needed = candidate in keep_refs
                    # Images in use by running containers but not in the
                    # catalog are infrastructure images (platform, monitoring,
                    # etc.) — never flag them as stale.
                    is_infra = in_use and not tracked
                    entry = {
                        "reference": candidate,
                        "display_reference": candidate,
                        "kind": kind,
                        "size_bytes": img_info.get("size_bytes"),
                        "created": img_info.get("created"),
                        "device_id": img_info.get("device_id"),
                        "tracked_image_id": tracked.image_id if tracked else None,
                        "tracked_status": tracked.status if tracked else None,
                        "is_needed": is_needed or is_infra,
                        "is_stale": not is_needed and not is_infra,
                        "reason": None if (is_needed or is_infra) else "Not referenced by catalog or active nodes",
                    }
                    inventory.append(entry)
                    if not is_needed and not is_infra:
                        stale_images.append(entry)
        except Exception as e:
            inventory_refreshed_at = utcnow().isoformat()
            stale_images = [{
                "reference": "",
                "display_reference": "",
                "kind": "unknown",
                "size_bytes": None,
                "created": None,
                "device_id": None,
                "tracked_image_id": None,
                "tracked_status": None,
                "is_needed": False,
                "is_stale": False,
                "reason": f"Failed to query agent inventory: {e}",
            }]

    stale_count = sum(1 for entry in stale_images if entry.get("is_stale"))
    set_agent_stale_image_count(host.id, host.name or host.id, stale_count)
    return {
        "agent_id": host.id,
        "agent_name": host.name,
        "images": result,
        "inventory": inventory,
        "stale_images": stale_images,
        "inventory_refreshed_at": inventory_refreshed_at,
    }


@router.post("/{agent_id}/images/cleanup-stale")
async def cleanup_agent_stale_images(
    agent_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """Delete stale image artifacts from an agent immediately."""
    require_admin(current_user)

    host = _get_agent_or_404(database, agent_id)
    result = await _cleanup_stale_images_for_host(host, database)
    if result.get("status") == "offline":
        raise HTTPException(status_code=503, detail="Agent is offline")
    if result.get("status") == "inventory_error":
        detail = result.get("failed", [{}])[0].get("error") or "Failed to query agent inventory"
        raise HTTPException(status_code=503, detail=detail)
    return result


async def _cleanup_stale_images_for_host(host: models.Host, database: Session) -> dict:
    """Delete stale image artifacts from one host and return a structured result."""
    from app import agent_client

    if host.status != HostStatus.ONLINE or not agent_client.is_agent_online(host):
        return {
            "agent_id": host.id,
            "agent_name": host.name,
            "status": "offline",
            "requested": 0,
            "deleted": [],
            "failed": [],
            "stale_images_remaining": 0,
            "inventory_refreshed_at": None,
        }

    details = await _build_agent_image_details(host, database)
    inventory_error = _extract_inventory_error(details)
    if inventory_error:
        return {
            "agent_id": host.id,
            "agent_name": host.name,
            "status": "inventory_error",
            "requested": 0,
            "deleted": [],
            "failed": [{"reference": "", "error": inventory_error}],
            "stale_images_remaining": 0,
            "inventory_refreshed_at": details.get("inventory_refreshed_at"),
        }

    stale_images = [
        entry for entry in details["stale_images"]
        if entry.get("is_stale") and isinstance(entry.get("reference"), str) and entry.get("reference")
    ]
    stale_references = sorted({str(entry["reference"]) for entry in stale_images})

    deleted: list[str] = []
    failed: list[dict[str, str]] = []
    for reference in stale_references:
        response = await agent_client.delete_image_on_agent(host, reference)
        if response.get("success") and response.get("deleted"):
            deleted.append(reference)
        else:
            failed.append({
                "reference": reference,
                "error": str(response.get("error") or "delete failed"),
            })

    if deleted:
        record_agent_stale_image_cleanup(host.id, host.name or host.id, "deleted", len(deleted))
    if failed:
        record_agent_stale_image_cleanup(host.id, host.name or host.id, "failed", len(failed))

    refreshed = await _build_agent_image_details(host, database)
    return {
        "agent_id": host.id,
        "agent_name": host.name,
        "status": "completed",
        "requested": len(stale_references),
        "deleted": deleted,
        "failed": failed,
        "stale_images_remaining": sum(1 for entry in refreshed["stale_images"] if entry.get("is_stale")),
        "inventory_refreshed_at": refreshed.get("inventory_refreshed_at"),
    }


@router.post("/images/cleanup-stale")
async def cleanup_all_agent_stale_images(
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """Delete stale image artifacts from all agents immediately."""
    require_admin(current_user)

    hosts = database.query(models.Host).all()
    results: list[dict[str, object]] = []
    total_requested = 0
    total_deleted = 0
    total_failed = 0
    skipped_offline = 0

    for host in hosts:
        result = await _cleanup_stale_images_for_host(host, database)
        results.append(result)
        if result.get("status") == "offline":
            skipped_offline += 1
            continue
        total_requested += int(result.get("requested", 0))
        total_deleted += len(result.get("deleted", []))
        total_failed += len(result.get("failed", []))

    return {
        "hosts": results,
        "total_hosts": len(hosts),
        "processed_hosts": len(hosts) - skipped_offline,
        "skipped_offline_hosts": skipped_offline,
        "total_requested": total_requested,
        "total_deleted": total_deleted,
        "total_failed": total_failed,
    }


@router.get("/images/stale-summary")
async def list_stale_agent_images_summary(
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """Summarize stale image artifacts across all agents."""
    hosts = database.query(models.Host).all()
    host_summaries: list[dict[str, object]] = []
    total_stale_images = 0

    for host in hosts:
        details = await _build_agent_image_details(host, database)
        stale_count = sum(1 for entry in details["stale_images"] if entry.get("is_stale"))
        total_stale_images += stale_count
        inventory_error = _extract_inventory_error(details)
        host_summaries.append({
            "agent_id": host.id,
            "agent_name": host.name,
            "status": host.status,
            "stale_image_count": stale_count,
            "inventory_refreshed_at": details.get("inventory_refreshed_at"),
            "inventory_error": inventory_error,
        })

    affected_agents = sum(1 for summary in host_summaries if summary["stale_image_count"])
    return {
        "hosts": host_summaries,
        "total_stale_images": total_stale_images,
        "affected_agents": affected_agents,
    }


@router.post("/{agent_id}/images/reconcile")
async def reconcile_agent_images_endpoint(
    agent_id: str,
    database: Session = Depends(db.get_db),
) -> dict:
    """Trigger image reconciliation for an agent.

    Queries the agent for its actual Docker images and updates
    the ImageHost records to reflect reality. Use this after
    manually loading images on an agent.
    """
    host = _get_agent_or_404(database, agent_id)
    _require_agent_online(host)

    from app.tasks.image_sync import reconcile_agent_images

    # Run reconciliation
    await reconcile_agent_images(agent_id, database)

    return {"message": f"Reconciliation completed for agent '{host.name}'"}


# --- Network Interface/Bridge Discovery Proxy ---

@router.get("/{agent_id}/interfaces")
async def list_agent_interfaces(
    agent_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """Proxy request to agent for listing available network interfaces.

    Used for external network configuration (VLAN parent interfaces).
    """
    host = _get_agent_or_404(database, agent_id)
    _require_agent_online(host)
    return await _proxy_agent_get(host, "interfaces")


@router.get("/{agent_id}/bridges")
async def list_agent_bridges(
    agent_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """Proxy request to agent for listing available Linux bridges.

    Used for external network configuration (bridge mode).
    """
    host = _get_agent_or_404(database, agent_id)
    _require_agent_online(host)
    return await _proxy_agent_get(host, "bridges")


# --- Agent Updates ---

class LatestVersionResponse(BaseModel):
    """Response with latest available agent version."""
    version: str


class TriggerUpdateRequest(BaseModel):
    """Request to trigger an agent update."""
    target_version: str | None = None  # If not specified, uses latest


class BulkUpdateRequest(BaseModel):
    """Request to update multiple agents."""
    agent_ids: list[str]
    target_version: str | None = None


class UpdateJobResponse(BaseModel):
    """Response after triggering an update."""
    job_id: str
    agent_id: str
    from_version: str
    to_version: str
    status: str
    message: str = ""


class UpdateStatusResponse(BaseModel):
    """Status of an update job."""
    job_id: str
    agent_id: str
    from_version: str
    to_version: str
    status: str
    progress_percent: int
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


@router.get("/updates/latest", response_model=LatestVersionResponse)
def get_latest_version(
    current_user: models.User = Depends(get_current_user),
) -> LatestVersionResponse:
    """Get the latest available agent version.

    This reads from the agent/VERSION file in the repository.
    """
    version = get_latest_agent_version()
    return LatestVersionResponse(version=version)


@router.post("/{agent_id}/update", response_model=UpdateJobResponse)
async def trigger_agent_update(
    agent_id: str,
    request: TriggerUpdateRequest | None = None,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_admin),
) -> UpdateJobResponse:
    """Trigger a software update for a specific agent.

    Creates an update job and sends the update request to the agent.
    The agent reports progress via callbacks.
    """
    host = _get_agent_or_404(database, agent_id)
    _require_agent_online(host)

    # Docker agents use rebuild, not update
    if host.deployment_mode == "docker":
        raise HTTPException(
            status_code=400,
            detail="Docker agents use rebuild, not update. Use POST /agents/{agent_id}/rebuild"
        )

    # Check for concurrent update — expire stale jobs automatically
    active_job = _find_active_update_job(database, agent_id)
    if active_job:
        ts = active_job.started_at or active_job.created_at
        if ts and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_seconds = (utcnow() - ts).total_seconds() if ts else 0
        if age_seconds > 300:  # 5 minutes
            _fail_update_job(
                database, active_job,
                f"Expired: stuck in '{active_job.status}' for {int(age_seconds)}s",
            )
            logger.info(f"Auto-expired stale update job {active_job.id} for agent {agent_id}")
        else:
            raise HTTPException(
                status_code=409,
                detail=f"Update already in progress (job {active_job.id})"
            )

    # Determine target version and checkout ref (for git)
    target_version = (request.target_version if request else None) or get_latest_agent_version()
    checkout_ref = _resolve_checkout_ref(target_version)

    # Check if already at target version
    if host.version == target_version:
        raise HTTPException(
            status_code=400,
            detail=f"Agent already at version {target_version}"
        )

    # Create update job
    job_id, update_job = _create_update_job(
        database, agent_id, host.version or "unknown", target_version,
    )
    database.commit()

    # Send update request to agent with commit SHA as target
    try:
        result = await _send_update_to_agent(host.address, job_id, checkout_ref)

        # Update job status based on agent response
        if result.get("accepted"):
            update_job.status = "downloading"
            update_job.started_at = utcnow()
            message = "Update initiated"
        else:
            update_job.status = JobStatus.FAILED
            update_job.error_message = result.get("message", "Agent rejected update")
            update_job.completed_at = utcnow()
            message = result.get("message", "Agent rejected update")

        # Store deployment mode if provided
        if result.get("deployment_mode"):
            host.deployment_mode = result["deployment_mode"]

        database.commit()

        return UpdateJobResponse(
            job_id=job_id,
            agent_id=agent_id,
            from_version=host.version or "unknown",
            to_version=target_version,
            status=update_job.status,
            message=message,
        )

    except httpx.RequestError as e:
        _fail_update_job(database, update_job, f"Failed to contact agent: {e}")
        raise HTTPException(status_code=502, detail=f"Failed to contact agent: {e}")

    except httpx.HTTPStatusError as e:
        _fail_update_job(database, update_job, f"Agent error: HTTP {e.response.status_code}")
        raise HTTPException(
            status_code=e.response.status_code, detail=f"Agent error: {e}"
        )


@router.post("/updates/bulk")
async def trigger_bulk_update(
    request: BulkUpdateRequest,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_admin),
) -> dict:
    """Trigger updates for multiple agents.

    Creates DB jobs sequentially (shared session), then fires HTTP requests
    to agents in parallel via asyncio.gather for faster bulk updates.
    """
    target_version = request.target_version or get_latest_agent_version()
    checkout_ref = _resolve_checkout_ref(target_version)
    results: list[dict] = []
    # Jobs to dispatch: list of (agent_id, job_id, host_address)
    pending_dispatches: list[tuple[str, str, str]] = []

    # Phase 1: Create DB jobs sequentially
    for agent_id in request.agent_ids:
        host = database.get(models.Host, agent_id)
        if not host:
            results.append({"agent_id": agent_id, "success": False, "error": "Agent not found"})
            continue
        if host.status != HostStatus.ONLINE:
            results.append({"agent_id": agent_id, "success": False, "error": "Agent is offline"})
            continue
        if host.deployment_mode == "docker":
            results.append({"agent_id": agent_id, "success": False, "error": "Docker agents use rebuild"})
            continue
        if host.version == target_version:
            results.append({"agent_id": agent_id, "success": False, "error": f"Already at version {target_version}"})
            continue

        # Check concurrent update
        active_job = _find_active_update_job(database, agent_id)
        if active_job:
            results.append({"agent_id": agent_id, "success": False, "error": "Update already in progress"})
            continue

        job_id, _ = _create_update_job(
            database, agent_id, host.version or "unknown", target_version,
        )
        pending_dispatches.append((agent_id, job_id, host.address))

    if pending_dispatches:
        database.commit()

    # Phase 2: Dispatch HTTP requests in parallel
    async def _dispatch_update(agent_id: str, job_id: str, address: str) -> dict:
        try:
            result = await _send_update_to_agent(address, job_id, checkout_ref)
            if result.get("accepted"):
                return {"agent_id": agent_id, "success": True, "job_id": job_id, "_status": "downloading"}
            msg = result.get("message", "Agent rejected update")
            return {
                "agent_id": agent_id, "success": False, "error": msg,
                "job_id": job_id, "_status": "failed", "_error": msg,
            }
        except Exception as e:
            return {
                "agent_id": agent_id, "success": False, "error": str(e),
                "job_id": job_id, "_status": "failed", "_error": str(e),
            }

    if pending_dispatches:
        dispatch_results = await asyncio.gather(
            *[_dispatch_update(aid, jid, addr) for aid, jid, addr in pending_dispatches]
        )

        # Phase 3: Update DB job statuses based on dispatch results
        now = utcnow()
        for dr in dispatch_results:
            job = database.get(models.AgentUpdateJob, dr.get("job_id"))
            if job:
                if dr.get("_status") == "downloading":
                    job.status = "downloading"
                    job.started_at = now
                elif dr.get("_status") == "failed":
                    job.status = JobStatus.FAILED
                    job.error_message = dr.get("_error", "Unknown error")
                    job.completed_at = now

            # Clean internal keys before adding to results
            clean = {k: v for k, v in dr.items() if not k.startswith("_")}
            results.append(clean)

        database.commit()

    return {
        "target_version": target_version,
        "results": results,
        "success_count": sum(1 for r in results if r.get("success")),
        "failure_count": sum(1 for r in results if not r.get("success")),
    }


@router.get("/{agent_id}/update-status", response_model=UpdateStatusResponse | None)
def get_update_status(
    agent_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> UpdateStatusResponse | None:
    """Get the status of the most recent update job for an agent.

    Returns None if no update jobs exist for this agent.
    """
    _get_agent_or_404(database, agent_id)

    # Get most recent update job
    job = (
        database.query(models.AgentUpdateJob)
        .filter(models.AgentUpdateJob.host_id == agent_id)
        .order_by(models.AgentUpdateJob.created_at.desc())
        .first()
    )

    if not job:
        return None

    return _update_job_to_status(job, agent_id)


@router.get("/{agent_id}/update-jobs")
def list_update_jobs(
    agent_id: str,
    limit: int = 10,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> list[UpdateStatusResponse]:
    """List recent update jobs for an agent.

    Returns up to `limit` most recent update jobs.
    """
    _get_agent_or_404(database, agent_id)

    jobs = (
        database.query(models.AgentUpdateJob)
        .filter(models.AgentUpdateJob.host_id == agent_id)
        .order_by(models.AgentUpdateJob.created_at.desc())
        .limit(limit)
        .all()
    )

    return [_update_job_to_status(job, agent_id) for job in jobs]


# --- Docker Agent Rebuild ---

class RebuildResponse(BaseModel):
    """Response from Docker agent rebuild."""
    success: bool
    message: str
    output: str = ""


@router.post("/{agent_id}/rebuild", response_model=RebuildResponse)
async def rebuild_docker_agent(
    agent_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_admin),
) -> RebuildResponse:
    """Rebuild a Docker-deployed agent container.

    This triggers a docker compose rebuild for agents running in Docker.
    Only works for the local agent managed by this controller's docker-compose.

    The rebuild process:
    1. Runs `docker compose up -d --build agent`
    2. The agent container is rebuilt with latest code
    3. Agent re-registers with new version after restart
    """

    host = _get_agent_or_404(database, agent_id)

    # Check if this is a Docker-deployed agent
    if host.deployment_mode != "docker":
        raise HTTPException(
            status_code=400,
            detail=f"Agent is not Docker-deployed (mode: {host.deployment_mode}). "
                   "Use the update endpoint for systemd agents."
        )

    # Check if this is the local agent (we can only rebuild local containers)
    if not host.is_local:
        raise HTTPException(
            status_code=400,
            detail="Can only rebuild local Docker agents. Remote Docker agents "
                   "must be rebuilt on their respective hosts."
        )

    try:
        # Find docker-compose file in mounted project directory
        compose_candidates = [
            Path("/app/project/docker-compose.gui.yml"),
            Path("/app/docker-compose.gui.yml"),
            Path("docker-compose.gui.yml"),
        ]
        compose_file = next(
            (p for p in compose_candidates if p.exists()), None,
        )
        if not compose_file:
            return RebuildResponse(
                success=False,
                message="docker-compose.gui.yml not found. Ensure project directory is mounted.",
            )

        # Run docker compose rebuild using async subprocess to avoid blocking event loop
        # Try docker compose (new) first, fall back to docker-compose (legacy)
        compose_cmd = ["docker", "compose"]
        proc = await asyncio.create_subprocess_exec(
            *compose_cmd, "version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        if proc.returncode != 0:
            compose_cmd = ["docker-compose"]

        # Run the rebuild command with timeout
        full_cmd = compose_cmd + [
            "-p", "archetype-iac",
            "-f", str(compose_file),
            "up", "-d", "--build", "--no-deps", "agent"
        ]
        proc = await asyncio.create_subprocess_exec(
            *full_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=compose_file.parent,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
            output = (stdout.decode() if stdout else "") + (stderr.decode() if stderr else "")

            return RebuildResponse(
                success=proc.returncode == 0,
                message=(
                    "Agent container rebuilt successfully. It will re-register shortly."
                    if proc.returncode == 0
                    else "Rebuild failed"
                ),
                output=output,
            )
        except asyncio.TimeoutError:
            proc.kill()
            return RebuildResponse(
                success=False,
                message="Rebuild timed out after 5 minutes",
            )

    except Exception as e:
        return RebuildResponse(
            success=False,
            message=f"Rebuild error: {str(e)}",
        )
