"""Agent registration and management endpoints."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app import db, models
from app.auth import get_current_user
from app.config import settings
from app.routers.system import get_commit
from app.utils.http import require_admin


router = APIRouter(prefix="/agents", tags=["agents"])


def get_latest_agent_version() -> str:
    """Get the latest available agent version.

    Reads from the root VERSION file (same as controller version).

    Returns:
        Version string (e.g., "0.4.0")
    """
    from app.routers.system import get_version
    return get_version()


# --- Request/Response Schemas ---

class AgentCapabilities(BaseModel):
    """What the agent can do."""
    providers: list[str] = Field(default_factory=list)
    max_concurrent_jobs: int = 4
    features: list[str] = Field(default_factory=list)


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


# --- Endpoints ---

@router.post("/register", response_model=RegistrationResponse)
async def register_agent(
    request: RegistrationRequest,
    database: Session = Depends(db.get_db),
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
    host_id = None
    is_new_registration = False
    is_restart = False

    # First check if agent already exists by ID
    existing = database.get(models.Host, agent.agent_id)

    if existing:
        # Detect restart: if started_at is newer than what we have on record
        if existing.started_at and agent.started_at:
            if agent.started_at > existing.started_at:
                is_restart = True

        # Update existing registration (same agent reconnecting)
        existing.name = agent.name
        existing.address = agent.address
        existing.status = "online"
        existing.capabilities = json.dumps(agent.capabilities.model_dump())
        existing.version = agent.version
        existing.git_sha = agent.commit or existing.git_sha
        existing.started_at = agent.started_at
        existing.is_local = agent.is_local
        existing.deployment_mode = agent.deployment_mode or existing.deployment_mode
        existing.last_heartbeat = datetime.now(timezone.utc)
        existing.data_plane_address = getattr(agent, "data_plane_ip", None)
        database.commit()
        host_id = agent.agent_id

        response = RegistrationResponse(
            success=True,
            message="Agent re-registered",
            assigned_id=agent.agent_id,
        )
    else:
        # Check for existing agent with same name or address (agent restarted with new ID)
        existing_by_name = (
            database.query(models.Host)
            .filter(models.Host.name == agent.name)
            .first()
        )
        existing_by_address = (
            database.query(models.Host)
            .filter(models.Host.address == agent.address)
            .first()
        )

        # Prefer matching by name, fall back to address
        existing_duplicate = existing_by_name or existing_by_address

        if existing_duplicate:
            # Update existing record in place to preserve foreign key references
            # (labs and jobs may reference this agent)
            existing_duplicate.name = agent.name
            existing_duplicate.address = agent.address
            existing_duplicate.status = "online"
            existing_duplicate.capabilities = json.dumps(agent.capabilities.model_dump())
            existing_duplicate.version = agent.version
            existing_duplicate.git_sha = agent.commit or existing_duplicate.git_sha
            existing_duplicate.started_at = agent.started_at
            existing_duplicate.is_local = agent.is_local
            existing_duplicate.deployment_mode = agent.deployment_mode or existing_duplicate.deployment_mode
            existing_duplicate.last_heartbeat = datetime.now(timezone.utc)
            existing_duplicate.data_plane_address = getattr(agent, "data_plane_ip", None)
            database.commit()
            host_id = existing_duplicate.id

            # Return the existing ID so agent can use it for heartbeats
            response = RegistrationResponse(
                success=True,
                message="Agent re-registered (updated existing record)",
                assigned_id=existing_duplicate.id,
            )
        else:
            # Create new agent (first time registration)
            host = models.Host(
                id=agent.agent_id,
                name=agent.name,
                address=agent.address,
                status="online",
                capabilities=json.dumps(agent.capabilities.model_dump()),
                version=agent.version,
                git_sha=agent.commit or None,
                started_at=agent.started_at,
                is_local=agent.is_local,
                deployment_mode=agent.deployment_mode or "unknown",
                last_heartbeat=datetime.now(timezone.utc),
                data_plane_address=getattr(agent, "data_plane_ip", None),
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

    # Handle agent restart: mark stale jobs as failed
    if is_restart and host_id:
        await _handle_agent_restart_cleanup(database, host_id)

    # Check if re-registration completes an in-progress update job
    if host_id:
        _check_update_completion(database, host_id, agent.version, agent.commit)

    # Trigger image reconciliation in background
    if host_id and settings.image_sync_enabled:
        from app.tasks.image_sync import reconcile_agent_images, pull_images_on_registration

        # Reconcile image inventory
        asyncio.create_task(reconcile_agent_images(host_id))

        # If pull strategy, trigger image sync
        if is_new_registration:
            asyncio.create_task(pull_images_on_registration(host_id))

    return response


async def _handle_agent_restart_cleanup(database: Session, agent_id: str) -> None:
    """Handle cleanup when an agent restarts.

    When an agent restarts, any jobs that were running on it are now
    orphaned since the agent lost its execution context. This function:
    1. Finds all running jobs assigned to this agent
    2. Marks them as failed with appropriate error message
    3. Updates associated lab state if needed
    4. Triggers link reconciliation for labs with nodes on this agent

    Args:
        database: Database session
        agent_id: ID of the restarted agent
    """
    import logging
    logger = logging.getLogger(__name__)

    # Find all running jobs on this agent
    stale_jobs = (
        database.query(models.Job)
        .filter(
            models.Job.agent_id == agent_id,
            models.Job.status == "running",
        )
        .all()
    )

    # Handle stale jobs
    if stale_jobs:
        logger.warning(
            f"Agent {agent_id} restarted - marking {len(stale_jobs)} running jobs as failed"
        )

        now = datetime.now(timezone.utc)
        for job in stale_jobs:
            job.status = "failed"
            job.completed_at = now
            job.log_path = (job.log_path or "") + "\n--- Agent restarted, job terminated ---"

            logger.info(f"Marked job {job.id} (action={job.action}) as failed due to agent restart")

            # Update lab state to error if this was a deploy/destroy job
            if job.lab_id and job.action in ("up", "down"):
                lab = database.get(models.Lab, job.lab_id)
                if lab:
                    lab.state = "error"
                    lab.state_error = f"Job {job.action} failed: agent restarted during execution"
                    lab.state_updated_at = now
                    logger.info(f"Set lab {job.lab_id} state to error due to agent restart")

        database.commit()

    # Mark cross-host links for recovery
    # This sets the appropriate attachment flags so link reconciliation
    # knows which side needs to be re-attached
    await _mark_links_for_recovery(database, agent_id)


async def _mark_links_for_recovery(database: Session, agent_id: str) -> None:
    """Mark cross-host links as needing recovery after agent restart.

    When an agent restarts, its VXLAN overlay state is lost. This function:
    1. Finds all cross-host links where this agent hosts either endpoint
    2. Marks them as "error" state with appropriate message
    3. Clears the VXLAN attachment flag for this agent's side

    The periodic link reconciliation will then detect these links and
    re-establish connectivity by re-attaching the affected endpoints.

    Args:
        database: Database session
        agent_id: ID of the restarted agent
    """
    import logging
    logger = logging.getLogger(__name__)

    # Find all cross-host links involving this agent where actual_state is "up"
    links = (
        database.query(models.LinkState)
        .filter(
            models.LinkState.actual_state == "up",
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
        link.actual_state = "error"
        link.error_message = "Agent restarted, pending recovery"

        # Clear the attachment flag for the restarted agent's side
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
    import logging
    logger = logging.getLogger(__name__)

    active_statuses = ("pending", "downloading", "installing", "restarting")
    active_jobs = (
        database.query(models.AgentUpdateJob)
        .filter(
            models.AgentUpdateJob.host_id == agent_id,
            models.AgentUpdateJob.status.in_(active_statuses),
        )
        .order_by(models.AgentUpdateJob.created_at.desc())
        .all()
    )

    now = datetime.now(timezone.utc)
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
            job.status = "completed"
            job.progress_percent = 100
            job.completed_at = now
            logger.info(
                f"Update job {job.id} completed: agent re-registered with "
                f"version={new_version} commit={new_commit[:8] if new_commit else 'N/A'}"
            )
        elif job.status == "restarting":
            # Agent restarted but version doesn't match - check if timed out
            if job.started_at and (now - job.started_at).total_seconds() > 600:
                job.status = "failed"
                job.error_message = "Agent did not re-register with expected version after update"
                job.completed_at = now
                logger.warning(
                    f"Update job {job.id} failed: agent re-registered with "
                    f"version={new_version} but expected {job.to_version}"
                )

    if active_jobs:
        database.commit()


@router.post("/{agent_id}/heartbeat", response_model=HeartbeatResponse)
def heartbeat(
    agent_id: str,
    request: HeartbeatRequest,
    database: Session = Depends(db.get_db),
) -> HeartbeatResponse:
    """Receive heartbeat from agent."""
    host = database.get(models.Host, agent_id)

    if not host:
        raise HTTPException(status_code=404, detail="Agent not registered")

    # Update status and resource usage
    host.status = request.status
    host.resource_usage = json.dumps(request.resource_usage)
    host.last_heartbeat = datetime.now(timezone.utc)
    # Update data plane address if agent reports one
    if request.data_plane_ip is not None:
        host.data_plane_address = request.data_plane_ip or None
    database.commit()

    # TODO: Check for pending jobs to dispatch
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

    result = []
    for host in hosts:
        try:
            capabilities = json.loads(host.capabilities)
        except (json.JSONDecodeError, TypeError):
            capabilities = {}

        result.append(HostOut(
            id=host.id,
            name=host.name,
            address=host.address,
            status=host.status,
            capabilities=capabilities,
            version=host.version,
            git_sha=host.git_sha,
            image_sync_strategy=host.image_sync_strategy or "on_demand",
            last_heartbeat=host.last_heartbeat,
            last_error=host.last_error,
            error_since=host.error_since,
            created_at=host.created_at,
        ))

    return result


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
            if lab.agent_id not in labs_by_agent:
                labs_by_agent[lab.agent_id] = []
            labs_by_agent[lab.agent_id].append({
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
        if ih.host_id not in images_by_host:
            images_by_host[ih.host_id] = []
        images_by_host[ih.host_id].append({
            "image_id": ih.image_id,
            "reference": ih.reference,
            "status": ih.status,
            "size_bytes": ih.size_bytes,
            "synced_at": ih.synced_at.isoformat() if ih.synced_at else None,
            "error_message": ih.error_message,
        })

    result = []
    for host in hosts:
        try:
            capabilities = json.loads(host.capabilities) if host.capabilities else {}
        except (json.JSONDecodeError, TypeError):
            capabilities = {}

        try:
            resource_usage = json.loads(host.resource_usage) if host.resource_usage else {}
        except (json.JSONDecodeError, TypeError):
            resource_usage = {}

        # Determine role based on capabilities and is_local flag
        providers = capabilities.get("providers", [])
        has_provider = len(providers) > 0

        if has_provider:
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
    host = database.get(models.Host, agent_id)

    if not host:
        raise HTTPException(status_code=404, detail="Agent not found")

    try:
        capabilities = json.loads(host.capabilities)
    except (json.JSONDecodeError, TypeError):
        capabilities = {}

    return HostOut(
        id=host.id,
        name=host.name,
        address=host.address,
        status=host.status,
        capabilities=capabilities,
        version=host.version,
        git_sha=host.git_sha,
        image_sync_strategy=host.image_sync_strategy or "on_demand",
        last_heartbeat=host.last_heartbeat,
        last_error=host.last_error,
        error_since=host.error_since,
        created_at=host.created_at,
    )


@router.get("/{agent_id}/deregister-info")
def get_deregister_info(
    agent_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """Get pre-flight information before deregistering an agent.

    Returns counts of affected resources so the UI can show an
    informed confirmation dialog.
    """
    require_admin(current_user)

    host = database.get(models.Host, agent_id)
    if not host:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Count affected resources
    labs = (
        database.query(models.Lab)
        .filter(models.Lab.agent_id == agent_id)
        .all()
    )
    running_labs = [{"id": lab.id, "name": lab.name, "state": lab.state} for lab in labs if lab.state in ("running", "starting")]

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
    import logging
    logger = logging.getLogger(__name__)

    require_admin(current_user)

    host = database.get(models.Host, agent_id)
    if not host:
        raise HTTPException(status_code=404, detail="Agent not found")

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
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """Update an agent's image synchronization strategy.

    Valid strategies:
    - push: Receive images immediately when uploaded to controller
    - pull: Pull missing images when agent comes online
    - on_demand: Sync only when deployment requires an image
    - disabled: No automatic sync, manual only
    """
    require_admin(current_user)
    valid_strategies = {"push", "pull", "on_demand", "disabled"}
    if request.strategy not in valid_strategies:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid strategy. Must be one of: {', '.join(valid_strategies)}"
        )

    host = database.get(models.Host, agent_id)
    if not host:
        raise HTTPException(status_code=404, detail="Agent not found")

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
    host = database.get(models.Host, agent_id)
    if not host:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Get all ImageHost records for this agent
    image_hosts = database.query(models.ImageHost).filter(
        models.ImageHost.host_id == agent_id
    ).all()

    result = []
    for ih in image_hosts:
        result.append({
            "image_id": ih.image_id,
            "reference": ih.reference,
            "status": ih.status,
            "size_bytes": ih.size_bytes,
            "synced_at": ih.synced_at.isoformat() if ih.synced_at else None,
            "error_message": ih.error_message,
        })

    return {
        "agent_id": agent_id,
        "agent_name": host.name,
        "images": result,
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
    host = database.get(models.Host, agent_id)
    if not host:
        raise HTTPException(status_code=404, detail="Agent not found")

    if host.status != "online":
        raise HTTPException(status_code=503, detail="Agent is offline")

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
    import httpx

    host = database.get(models.Host, agent_id)
    if not host:
        raise HTTPException(status_code=404, detail="Agent not found")

    if host.status != "online":
        raise HTTPException(status_code=503, detail="Agent is offline")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{host.address}/interfaces")
            response.raise_for_status()
            return response.json()
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Failed to contact agent: {e}")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"Agent error: {e}")


@router.get("/{agent_id}/bridges")
async def list_agent_bridges(
    agent_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """Proxy request to agent for listing available Linux bridges.

    Used for external network configuration (bridge mode).
    """
    import httpx

    host = database.get(models.Host, agent_id)
    if not host:
        raise HTTPException(status_code=404, detail="Agent not found")

    if host.status != "online":
        raise HTTPException(status_code=503, detail="Agent is offline")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{host.address}/bridges")
            response.raise_for_status()
            return response.json()
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Failed to contact agent: {e}")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"Agent error: {e}")


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
    current_user: models.User = Depends(get_current_user),
) -> UpdateJobResponse:
    """Trigger a software update for a specific agent.

    Creates an update job and sends the update request to the agent.
    The agent reports progress via callbacks.
    """
    require_admin(current_user)
    import httpx

    host = database.get(models.Host, agent_id)
    if not host:
        raise HTTPException(status_code=404, detail="Agent not found")

    if host.status != "online":
        raise HTTPException(status_code=503, detail="Agent is offline")

    # Docker agents use rebuild, not update
    if host.deployment_mode == "docker":
        raise HTTPException(
            status_code=400,
            detail="Docker agents use rebuild, not update. Use POST /agents/{agent_id}/rebuild"
        )

    # Check for concurrent update
    active_job = (
        database.query(models.AgentUpdateJob)
        .filter(
            models.AgentUpdateJob.host_id == agent_id,
            models.AgentUpdateJob.status.in_(("pending", "downloading", "installing", "restarting")),
        )
        .first()
    )
    if active_job:
        raise HTTPException(
            status_code=409,
            detail=f"Update already in progress (job {active_job.id})"
        )

    # Determine target version and checkout ref (for git)
    target_version = (request.target_version if request else None) or get_latest_agent_version()
    # Use target_version as checkout ref if it looks like a commit SHA,
    # otherwise fall back to the API's own commit (for version-based updates)
    import re
    if re.fullmatch(r'[0-9a-f]{7,40}', target_version):
        checkout_ref = target_version
    else:
        checkout_ref = get_commit()

    # Check if already at target version
    if host.version == target_version:
        raise HTTPException(
            status_code=400,
            detail=f"Agent already at version {target_version}"
        )

    # Create update job
    job_id = str(uuid4())
    update_job = models.AgentUpdateJob(
        id=job_id,
        host_id=agent_id,
        from_version=host.version or "unknown",
        to_version=target_version,
        status="pending",
    )
    database.add(update_job)
    database.commit()

    # Build callback URL
    callback_url = f"{settings.internal_url}/callbacks/update/{job_id}"

    # Send update request to agent with commit SHA as target
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"http://{host.address}/update",
                json={
                    "job_id": job_id,
                    "target_version": checkout_ref,
                    "callback_url": callback_url,
                },
            )
            response.raise_for_status()
            result = response.json()

            # Update job status based on agent response
            if result.get("accepted"):
                update_job.status = "downloading"
                update_job.started_at = datetime.now(timezone.utc)
                message = "Update initiated"
            else:
                update_job.status = "failed"
                update_job.error_message = result.get("message", "Agent rejected update")
                update_job.completed_at = datetime.now(timezone.utc)
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
        # Update job as failed
        update_job.status = "failed"
        update_job.error_message = f"Failed to contact agent: {e}"
        update_job.completed_at = datetime.now(timezone.utc)
        database.commit()

        raise HTTPException(status_code=502, detail=f"Failed to contact agent: {e}")

    except httpx.HTTPStatusError as e:
        update_job.status = "failed"
        update_job.error_message = f"Agent error: HTTP {e.response.status_code}"
        update_job.completed_at = datetime.now(timezone.utc)
        database.commit()

        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"Agent error: {e}"
        )


@router.post("/updates/bulk")
async def trigger_bulk_update(
    request: BulkUpdateRequest,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """Trigger updates for multiple agents.

    Creates DB jobs sequentially (shared session), then fires HTTP requests
    to agents in parallel via asyncio.gather for faster bulk updates.
    """
    require_admin(current_user)
    import httpx
    import logging
    logging.getLogger(__name__)

    target_version = request.target_version or get_latest_agent_version()
    # Use target_version as checkout ref if it looks like a commit SHA,
    # otherwise fall back to the API's own commit (for version-based updates)
    import re
    if re.fullmatch(r'[0-9a-f]{7,40}', target_version):
        checkout_ref = target_version
    else:
        checkout_ref = get_commit()
    results: list[dict] = []
    # Jobs to dispatch: list of (agent_id, job_id, host_address)
    pending_dispatches: list[tuple[str, str, str]] = []

    # Phase 1: Create DB jobs sequentially
    for agent_id in request.agent_ids:
        host = database.get(models.Host, agent_id)
        if not host:
            results.append({"agent_id": agent_id, "success": False, "error": "Agent not found"})
            continue
        if host.status != "online":
            results.append({"agent_id": agent_id, "success": False, "error": "Agent is offline"})
            continue
        if host.deployment_mode == "docker":
            results.append({"agent_id": agent_id, "success": False, "error": "Docker agents use rebuild"})
            continue
        if host.version == target_version:
            results.append({"agent_id": agent_id, "success": False, "error": f"Already at version {target_version}"})
            continue

        # Check concurrent update
        active_job = (
            database.query(models.AgentUpdateJob)
            .filter(
                models.AgentUpdateJob.host_id == agent_id,
                models.AgentUpdateJob.status.in_(("pending", "downloading", "installing", "restarting")),
            )
            .first()
        )
        if active_job:
            results.append({"agent_id": agent_id, "success": False, "error": "Update already in progress"})
            continue

        job_id = str(uuid4())
        update_job = models.AgentUpdateJob(
            id=job_id,
            host_id=agent_id,
            from_version=host.version or "unknown",
            to_version=target_version,
            status="pending",
        )
        database.add(update_job)
        pending_dispatches.append((agent_id, job_id, host.address))

    if pending_dispatches:
        database.commit()

    # Phase 2: Dispatch HTTP requests in parallel
    async def _dispatch_update(agent_id: str, job_id: str, address: str) -> dict:
        callback_url = f"{settings.internal_url}/callbacks/update/{job_id}"
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"http://{address}/update",
                    json={
                        "job_id": job_id,
                        "target_version": checkout_ref,
                        "callback_url": callback_url,
                    },
                )
                resp.raise_for_status()
                result = resp.json()

                if result.get("accepted"):
                    return {"agent_id": agent_id, "success": True, "job_id": job_id, "_status": "downloading"}
                else:
                    return {
                        "agent_id": agent_id, "success": False,
                        "error": result.get("message", "Agent rejected update"),
                        "job_id": job_id, "_status": "failed",
                        "_error": result.get("message", "Agent rejected update"),
                    }
        except Exception as e:
            return {
                "agent_id": agent_id, "success": False,
                "error": str(e), "job_id": job_id,
                "_status": "failed", "_error": str(e),
            }

    if pending_dispatches:
        dispatch_results = await asyncio.gather(
            *[_dispatch_update(aid, jid, addr) for aid, jid, addr in pending_dispatches]
        )

        # Phase 3: Update DB job statuses based on dispatch results
        now = datetime.now(timezone.utc)
        for dr in dispatch_results:
            job = database.get(models.AgentUpdateJob, dr.get("job_id"))
            if job:
                if dr.get("_status") == "downloading":
                    job.status = "downloading"
                    job.started_at = now
                elif dr.get("_status") == "failed":
                    job.status = "failed"
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
    host = database.get(models.Host, agent_id)
    if not host:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Get most recent update job
    job = (
        database.query(models.AgentUpdateJob)
        .filter(models.AgentUpdateJob.host_id == agent_id)
        .order_by(models.AgentUpdateJob.created_at.desc())
        .first()
    )

    if not job:
        return None

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
    host = database.get(models.Host, agent_id)
    if not host:
        raise HTTPException(status_code=404, detail="Agent not found")

    jobs = (
        database.query(models.AgentUpdateJob)
        .filter(models.AgentUpdateJob.host_id == agent_id)
        .order_by(models.AgentUpdateJob.created_at.desc())
        .limit(limit)
        .all()
    )

    return [
        UpdateStatusResponse(
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
        for job in jobs
    ]


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
    current_user: models.User = Depends(get_current_user),
) -> RebuildResponse:
    """Rebuild a Docker-deployed agent container.

    This triggers a docker compose rebuild for agents running in Docker.
    Only works for the local agent managed by this controller's docker-compose.

    The rebuild process:
    1. Runs `docker compose up -d --build agent`
    2. The agent container is rebuilt with latest code
    3. Agent re-registers with new version after restart
    """
    require_admin(current_user)

    host = database.get(models.Host, agent_id)
    if not host:
        raise HTTPException(status_code=404, detail="Agent not found")

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
        compose_file = Path("/app/project/docker-compose.gui.yml")
        if not compose_file.exists():
            # Try alternate locations
            for alt_path in ["/app/docker-compose.gui.yml", "docker-compose.gui.yml"]:
                if Path(alt_path).exists():
                    compose_file = Path(alt_path)
                    break

        if not compose_file.exists():
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
            stdout_str = stdout.decode() if stdout else ""
            stderr_str = stderr.decode() if stderr else ""

            if proc.returncode == 0:
                return RebuildResponse(
                    success=True,
                    message="Agent container rebuilt successfully. It will re-register shortly.",
                    output=stdout_str + stderr_str,
                )
            else:
                return RebuildResponse(
                    success=False,
                    message="Rebuild failed",
                    output=stdout_str + stderr_str,
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
