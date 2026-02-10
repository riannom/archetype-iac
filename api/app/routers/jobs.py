"""Lab lifecycle and job management endpoints."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import agent_client, db, models, schemas
from app.auth import get_current_user
from app.db import get_session
from app.netlab import run_netlab_command
from app.services.topology import TopologyService
from app.storage import lab_workspace
from app.tasks.jobs import run_agent_job, run_multihost_deploy, run_multihost_destroy
from app.topology import analyze_topology
from app.config import settings
from app.utils.job import get_job_timeout_at, is_job_stuck
from app.utils.lab import get_lab_or_404, get_lab_provider
from app.utils.logs import get_log_content
from app.utils.async_tasks import safe_create_task
from app.jobs import has_conflicting_job
from app.services.state_machine import NodeStateMachine

logger = logging.getLogger(__name__)

router = APIRouter(tags=["jobs"])


def _extract_error_summary(log_content: str | None, status: str) -> str | None:
    """Extract an informative error summary from job log content.

    Looks for common error patterns and returns a concise message.
    For multiline errors (like EOS startup errors), combines relevant context.
    """
    import re

    if status != "failed" or not log_content:
        return None

    # Look for specific error patterns in order of priority
    lines = log_content.strip().split("\n")

    # Pattern 1: level=error format
    # Example: level=error msg="failed to create container"
    for line in lines:
        match = re.search(r'level=error\s+msg="([^"]+)"', line)
        if match:
            return match.group(1)[:200]

    # Pattern 2: "Error: <message>" line (possibly multiline)
    for i, line in enumerate(lines):
        line_stripped = line.strip()
        if line_stripped.startswith("Error:"):
            error_msg = line_stripped[6:].strip()
            # Check if the next line continues the error (not a separator or section header)
            if i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                if next_line and not next_line.startswith(("=", "-", "Exit", "STDOUT", "STDERR")):
                    error_msg = f"{error_msg} - {next_line}"
            return error_msg[:200]
        if line_stripped.startswith("ERROR:"):
            msg = line_stripped[6:].strip()
            # Skip generic headers
            if msg and not msg.endswith("on agent."):
                return msg[:200]

    # Pattern 3: "Details: <message>" line
    for line in lines:
        line = line.strip()
        if line.startswith("Details:"):
            return line[8:].strip()[:200]

    # Pattern 4: Look for common docker errors
    error_patterns = [
        "missing image",
        "image not found",
        "no such image",
        "pull access denied",
        "connection refused",
        "permission denied",
        "network not found",
        "container already exists",
        "port is already allocated",
        "cannot connect",
        "failed to create",
        "failed to start",
        "timed out",
        "timeout",
        "unhealthy",
        "not healthy",
        "exit code",
        "exited with",
    ]

    for line in lines:
        line_lower = line.lower()
        for pattern in error_patterns:
            if pattern in line_lower:
                return line.strip()[:200]

    # Pattern 5: First non-empty, meaningful line after "STDERR" section
    in_stderr = False
    stderr_lines = []
    for line in lines:
        if "STDERR" in line or "stderr" in line.lower():
            in_stderr = True
            continue
        if in_stderr:
            stripped = line.strip()
            # Skip empty lines and separators
            if stripped and not stripped.startswith(("=", "-")):
                stderr_lines.append(stripped)
                # Get first meaningful error line from stderr
                if len(stderr_lines) == 1:
                    # Check for level=error format in stderr
                    match = re.search(r'level=error\s+msg="([^"]+)"', stripped)
                    if match:
                        return match.group(1)[:200]
                    # Return this line if it looks like an error
                    if any(p in stripped.lower() for p in ["error", "fail", "cannot", "unable"]):
                        return stripped[:200]

    # If we collected stderr lines, return the first one
    if stderr_lines:
        return stderr_lines[0][:200]

    # Fallback: First line that looks like an error
    for line in lines:
        line = line.strip()
        if line and not line.startswith("=") and not line.startswith("-"):
            if "fail" in line.lower() or "error" in line.lower():
                return line[:200]

    # Last resort: "Job failed" generic
    return "Job failed - check logs for details"


def _enrich_job_output(job: models.Job) -> schemas.JobOut:
    """Convert a Job model to JobOut schema with computed fields."""
    job_out = schemas.JobOut.model_validate(job)

    # Compute timeout_at
    job_out.timeout_at = get_job_timeout_at(job.action, job.started_at)

    # Compute is_stuck
    job_out.is_stuck = is_job_stuck(
        job.action,
        job.status,
        job.started_at,
        job.created_at,
    )

    # Extract error summary for failed jobs
    log_content = get_log_content(job.log_path)
    job_out.error_summary = _extract_error_summary(log_content, job.status)

    return job_out


@router.post("/labs/{lab_id}/up")
async def lab_up(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.JobOut:
    lab = get_lab_or_404(lab_id, database, current_user)

    # Check for conflicting jobs before proceeding
    has_conflict, conflicting_action = has_conflicting_job(lab_id, "up")
    if has_conflict:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot start lab: '{conflicting_action}' operation already in progress"
        )

    # Use TopologyService for analysis (database is source of truth)
    service = TopologyService(database)
    is_multihost = False
    graph = None
    analysis = None
    has_explicit_placements = False

    if not service.has_nodes(lab.id):
        raise HTTPException(status_code=400, detail="No topology defined for this lab")

    try:
        db_analysis = service.analyze_placements(lab.id)
        has_explicit_placements = bool(db_analysis.placements)
        is_multihost = not db_analysis.single_host or has_explicit_placements
        graph = service.export_to_graph(lab.id)
        # Convert to legacy analysis format for compatibility
        analysis = analyze_topology(graph)
        logger.info(
            f"Lab {lab_id} topology analysis: "
            f"single_host={db_analysis.single_host}, "
            f"hosts={list(db_analysis.placements.keys())}, "
            f"cross_host_links={len(db_analysis.cross_host_links)}, "
            f"has_explicit_placements={has_explicit_placements}"
        )
    except Exception as e:
        logger.warning(f"Failed to analyze topology for lab {lab_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to analyze topology: {e}")

    # Get the provider for this lab
    lab_provider = get_lab_provider(lab)

    if is_multihost and analysis and graph:
        # Multi-host deployment: validate all required agents exist
        missing_hosts = []
        for host_name in analysis.placements:
            agent = await agent_client.get_agent_by_name(
                database, host_name, required_provider=lab_provider
            )
            if not agent:
                missing_hosts.append(host_name)

        if missing_hosts:
            raise HTTPException(
                status_code=503,
                detail=f"Missing or unhealthy agents for hosts: {', '.join(missing_hosts)}"
            )

        # Check if there are nodes without explicit placement
        placed_node_count = sum(len(p) for p in analysis.placements.values())
        total_node_count = len(graph.nodes)

        if placed_node_count < total_node_count:
            # Some nodes don't have explicit host placement
            # Find a default agent for them (lab's preferred agent or any healthy)
            default_agent = await agent_client.get_agent_for_lab(
                database, lab, required_provider=lab_provider
            )
            if not default_agent:
                raise HTTPException(
                    status_code=503,
                    detail=f"No healthy agent available for nodes without explicit host placement"
                )

            # Re-analyze topology with default host for unplaced nodes
            analysis = analyze_topology(graph, default_host=default_agent.name)
            logger.info(
                f"Lab {lab_id} has {total_node_count - placed_node_count} nodes without "
                f"explicit placement, using {default_agent.name} as default host"
            )
    else:
        # Single-host deployment: check for any healthy agent
        agent = await agent_client.get_agent_for_lab(database, lab, required_provider=lab_provider)
        if not agent:
            raise HTTPException(status_code=503, detail=f"No healthy agent available with {lab_provider} support")

    # Pre-deploy image sync check (if enabled)
    image_sync_log: list[str] = []
    if settings.image_sync_enabled and settings.image_sync_pre_deploy_check:
        from app.tasks.image_sync import ensure_images_for_deployment

        # Get image references from database (uses same resolution as deployment)
        image_refs = service.get_required_images(lab.id)
        if image_refs:
            # Get image-to-nodes mapping for status updates
            image_to_nodes = service.get_image_to_nodes_map(lab.id)

            # Determine target agent for single-host or first agent for multi-host
            target_host_id = None
            if is_multihost and analysis:
                # For multi-host, check all agents have required images
                # For now, we check the first placement's host
                first_host = list(analysis.placements.keys())[0]
                target_agent = await agent_client.get_agent_by_name(
                    database, first_host, required_provider=lab_provider
                )
                if target_agent:
                    target_host_id = target_agent.id
            else:
                # Single-host deployment uses the selected agent
                target_host_id = agent.id if agent else None

            if target_host_id:
                all_ready, missing, image_sync_log = await ensure_images_for_deployment(
                    target_host_id,
                    image_refs,
                    timeout=settings.image_sync_timeout,
                    database=database,
                    lab_id=lab.id,
                    image_to_nodes=image_to_nodes,
                )
                if not all_ready and missing:
                    # Images still missing after sync attempt
                    missing_str = ", ".join(missing[:3])  # Show first 3
                    if len(missing) > 3:
                        missing_str += f" (+{len(missing) - 3} more)"
                    raise HTTPException(
                        status_code=503,
                        detail=f"Required images not available on agent: {missing_str}. "
                               f"Upload images or manually sync them to the agent."
                    )

    # Create job record
    job = models.Job(lab_id=lab.id, user_id=current_user.id, action="up", status="queued")
    database.add(job)
    database.commit()
    database.refresh(job)

    # Set desired_state for ALL nodes so enforcement and NLM know
    # these nodes should be running after deploy.
    # Use SELECT FOR UPDATE to prevent races with concurrent state changes.
    # Reset enforcement counters so stale circuit breakers don't block the new deploy.
    node_states = (
        database.query(models.NodeState)
        .filter(models.NodeState.lab_id == lab.id)
        .with_for_update()
        .all()
    )
    for ns in node_states:
        ns.desired_state = "running"
        ns.enforcement_attempts = 0
        ns.enforcement_failed_at = None
        ns.last_enforcement_at = None
        ns.error_message = None
    database.commit()

    # Clear enforcement cooldowns so enforcement picks up new desired state immediately
    if node_states:
        from app.tasks.state_enforcement import clear_cooldowns_for_lab
        safe_create_task(
            clear_cooldowns_for_lab(lab.id, [ns.node_name for ns in node_states]),
            name=f"clear_cooldowns:{lab.id}"
        )

    # Start background task - choose deployment method based on topology
    # Deploy functions build topology from database (source of truth)
    if is_multihost:
        safe_create_task(
            run_multihost_deploy(job.id, lab.id, provider=lab_provider),
            name=f"deploy:multihost:{job.id}"
        )
    else:
        safe_create_task(
            run_agent_job(job.id, lab.id, "up", provider=lab_provider),
            name=f"deploy:single:{job.id}"
        )

    # Build response with image sync events
    job_out = schemas.JobOut.model_validate(job)
    if image_sync_log:
        job_out.image_sync_events = image_sync_log
    return job_out


@router.post("/labs/{lab_id}/down")
async def lab_down(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.JobOut:
    lab = get_lab_or_404(lab_id, database, current_user)

    # Check for conflicting jobs before proceeding
    has_conflict, conflicting_action = has_conflicting_job(lab_id, "down")
    if has_conflict:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot stop lab: '{conflicting_action}' operation already in progress"
        )

    # Use TopologyService for analysis (database is source of truth)
    service = TopologyService(database)
    is_multihost = service.is_multihost(lab.id) if service.has_nodes(lab.id) else False

    # Get the provider for this lab
    lab_provider = get_lab_provider(lab)

    if not is_multihost:
        # Single-host: check for healthy agent with required capability
        agent = await agent_client.get_agent_for_lab(database, lab, required_provider=lab_provider)
        if not agent:
            raise HTTPException(status_code=503, detail=f"No healthy agent available with {lab_provider} support")

    # Create job record
    job = models.Job(lab_id=lab.id, user_id=current_user.id, action="down", status="queued")
    database.add(job)
    database.commit()
    database.refresh(job)

    # Set desired_state so enforcement doesn't restart destroyed containers.
    # Use SELECT FOR UPDATE to prevent races with concurrent state changes.
    # Reset enforcement counters so stale circuit breakers don't block future ops.
    node_states = (
        database.query(models.NodeState)
        .filter(models.NodeState.lab_id == lab.id)
        .with_for_update()
        .all()
    )
    for ns in node_states:
        ns.desired_state = "stopped"
        ns.enforcement_attempts = 0
        ns.enforcement_failed_at = None
        ns.last_enforcement_at = None
    database.commit()

    # Clear enforcement cooldowns so enforcement picks up new desired state immediately
    if node_states:
        from app.tasks.state_enforcement import clear_cooldowns_for_lab
        safe_create_task(
            clear_cooldowns_for_lab(lab.id, [ns.node_name for ns in node_states]),
            name=f"clear_cooldowns:{lab.id}"
        )

    # Start background task - choose destroy method based on topology
    # Destroy functions use database for host analysis (source of truth)
    if is_multihost:
        safe_create_task(
            run_multihost_destroy(job.id, lab.id, provider=lab_provider),
            name=f"destroy:multihost:{job.id}"
        )
    else:
        safe_create_task(
            run_agent_job(job.id, lab.id, "down", provider=lab_provider),
            name=f"destroy:single:{job.id}"
        )

    return schemas.JobOut.model_validate(job)


@router.post("/labs/{lab_id}/restart")
async def lab_restart(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.JobOut:
    lab = get_lab_or_404(lab_id, database, current_user)

    # Check for conflicting jobs before proceeding (restart involves down + up)
    has_conflict, conflicting_action = has_conflicting_job(lab_id, "down")
    if has_conflict:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot restart lab: '{conflicting_action}' operation already in progress"
        )

    # Get the provider for this lab
    lab_provider = get_lab_provider(lab)

    # Check for healthy agent with required capability, respecting affinity
    agent = await agent_client.get_agent_for_lab(database, lab, required_provider=lab_provider)
    if not agent:
        raise HTTPException(status_code=503, detail=f"No healthy agent available with {lab_provider} support")

    # Validate topology exists in database (source of truth)
    service = TopologyService(database)
    if not service.has_nodes(lab.id):
        raise HTTPException(status_code=400, detail="No topology defined for this lab")

    # Create separate jobs for down and up phases
    down_job = models.Job(lab_id=lab.id, user_id=current_user.id, action="down", status="queued")
    database.add(down_job)
    database.commit()
    database.refresh(down_job)

    up_job = models.Job(lab_id=lab.id, user_id=current_user.id, action="up", status="queued")
    database.add(up_job)
    database.commit()
    database.refresh(up_job)

    # For restart, we do down then up sequentially with separate jobs
    async def restart_sequence():
        await run_agent_job(down_job.id, lab.id, "down", provider=lab_provider)
        # Check if down succeeded before starting up
        with get_session() as session:
            dj = session.get(models.Job, down_job.id)
            uj = session.get(models.Job, up_job.id)
            if dj and dj.status == "failed":
                # Mark up job as cancelled since down failed
                if uj:
                    uj.status = "failed"
                    uj.log = "Cancelled: down phase failed"
                    session.commit()
                return
        # Proceed with up phase (topology is built from database)
        await run_agent_job(up_job.id, lab.id, "up", provider=lab_provider)

    safe_create_task(restart_sequence(), name=f"restart:{down_job.id}")

    return schemas.JobOut.model_validate(down_job)


@router.post("/labs/{lab_id}/nodes/{node}/{action}",
              deprecated=True)
async def node_action(
    lab_id: str,
    node: str,
    action: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.JobOut:
    """Start or stop a specific node.

    Deprecated: Use PUT /labs/{lab_id}/nodes/{node_id}/desired-state instead.
    This endpoint applies the same guards as set_node_desired_state:
    transitional state rejection, conflicting job check, error retry.
    """
    if action not in ("start", "stop"):
        raise HTTPException(status_code=400, detail="Unsupported node action")

    lab = get_lab_or_404(lab_id, database, current_user)

    # Resolve node by id or name (with SELECT FOR UPDATE to prevent races)
    node_state = (
        database.query(models.NodeState)
        .filter(
            models.NodeState.lab_id == lab.id,
            models.NodeState.node_id == node,
        )
        .with_for_update()
        .first()
    )
    if not node_state:
        node_state = (
            database.query(models.NodeState)
            .filter(
                models.NodeState.lab_id == lab.id,
                models.NodeState.node_name == node,
            )
            .with_for_update()
            .first()
        )
    if not node_state:
        raise HTTPException(status_code=404, detail=f"Node '{node}' not found")

    node_id = node_state.node_id
    desired_state = "running" if action == "start" else "stopped"
    command = "start" if action == "start" else "stop"

    # Centralized guard check (6.1)
    allowed, reason = NodeStateMachine.can_accept_command(node_state.actual_state, command)
    if not allowed:
        raise HTTPException(status_code=409, detail=reason)

    desired_changed = node_state.desired_state != desired_state

    if desired_changed:
        node_state.desired_state = desired_state
        database.commit()
        database.refresh(node_state)

        has_conflict, _ = has_conflicting_job(lab_id, "sync", session=database)
        if not has_conflict and NodeStateMachine.needs_sync(node_state.actual_state, command):
            from app.routers.labs import _create_node_sync_job
            _create_node_sync_job(database, lab, node_id, current_user)

    elif (
        node_state.desired_state == desired_state
        and desired_state == "running"
        and node_state.actual_state == "error"
    ):
        # Retry: reset enforcement state
        node_state.enforcement_attempts = 0
        node_state.enforcement_failed_at = None
        node_state.last_enforcement_at = None
        node_state.error_message = None
        database.commit()
        database.refresh(node_state)

        has_conflict, _ = has_conflicting_job(lab_id, "sync", session=database)
        if not has_conflict:
            from app.routers.labs import _create_node_sync_job
            _create_node_sync_job(database, lab, node_id, current_user)

    # Return latest job for this node, or create a no-op
    latest_job = (
        database.query(models.Job)
        .filter(
            models.Job.lab_id == lab.id,
            models.Job.action == f"sync:node:{node_id}",
        )
        .order_by(models.Job.created_at.desc())
        .first()
    )
    if latest_job and latest_job.status == "queued":
        return schemas.JobOut.model_validate(latest_job)

    noop_job = models.Job(
        lab_id=lab.id,
        user_id=current_user.id,
        action=f"sync:node:{node_id}",
        status="completed",
    )
    database.add(noop_job)
    database.commit()
    database.refresh(noop_job)
    return schemas.JobOut.model_validate(noop_job)


@router.get("/labs/{lab_id}/status")
async def lab_status(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict:
    lab = get_lab_or_404(lab_id, database, current_user)

    # Get the provider for this lab
    lab_provider = get_lab_provider(lab)

    # Query NodePlacement to find all agents with nodes for this lab
    placements = (
        database.query(models.NodePlacement)
        .filter(models.NodePlacement.lab_id == lab.id)
        .all()
    )

    # Get unique agent IDs from placements
    agent_ids = set(p.host_id for p in placements)

    # Also include lab's default agent if set
    if lab.agent_id:
        agent_ids.add(lab.agent_id)

    if not agent_ids:
        # No agents found - try to get any healthy agent
        agent = await agent_client.get_healthy_agent(database, required_provider=lab_provider)
        if agent:
            agent_ids.add(agent.id)

    # Fetch agents from database
    agents = []
    if agent_ids:
        agents = database.query(models.Host).filter(models.Host.id.in_(agent_ids)).all()

    if not agents:
        # Fallback to old netlab command if no agents
        code, stdout, stderr = run_netlab_command(["netlab", "status"], lab_workspace(lab.id))
        if code != 0:
            return {"raw": "", "error": stderr or "netlab status failed"}
        return {"raw": stdout}

    # Aggregate status from all agents
    all_nodes = []
    errors = []
    agent_info = []

    async def fetch_agent_status(agent: models.Host):
        try:
            result = await agent_client.get_lab_status_from_agent(agent, lab.id)
            nodes = result.get("nodes", [])
            # Add agent info to each node
            for node in nodes:
                node["agent_id"] = agent.id
                node["agent_name"] = agent.name
            return nodes, result.get("error"), agent
        except Exception as e:
            return [], str(e), agent

    # Fetch status from all agents concurrently
    results = await asyncio.gather(*[fetch_agent_status(a) for a in agents])

    seen_nodes = set()  # Track which nodes we've already added
    for nodes, error, agent in results:
        if error:
            errors.append(f"{agent.name or agent.id}: {error}")
        for node in nodes:
            # Avoid duplicates - use node name as key
            node_name = node.get("name", "")
            if node_name not in seen_nodes:
                all_nodes.append(node)
                seen_nodes.add(node_name)
        agent_info.append({"id": agent.id, "name": agent.name})

    return {
        "nodes": all_nodes,
        "error": "; ".join(errors) if errors else None,
        "agents": agent_info,
        "is_multi_host": len(agents) > 1,
    }


@router.get("/labs/{lab_id}/jobs")
def list_jobs(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, list[schemas.JobOut]]:
    get_lab_or_404(lab_id, database, current_user)
    jobs = (
        database.query(models.Job)
        .filter(models.Job.lab_id == lab_id)
        .order_by(models.Job.created_at.desc())
        .all()
    )
    return {"jobs": [_enrich_job_output(job) for job in jobs]}


@router.get("/labs/{lab_id}/jobs/{job_id}")
def get_job(
    lab_id: str,
    job_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.JobOut:
    get_lab_or_404(lab_id, database, current_user)
    job = database.get(models.Job, job_id)
    if not job or job.lab_id != lab_id:
        raise HTTPException(status_code=404, detail="Job not found")
    return _enrich_job_output(job)


@router.get("/labs/{lab_id}/jobs/{job_id}/log")
def get_job_log(
    lab_id: str,
    job_id: str,
    tail: int | None = None,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, str]:
    get_lab_or_404(lab_id, database, current_user)
    job = database.get(models.Job, job_id)
    if not job or job.lab_id != lab_id:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.log_path:
        raise HTTPException(status_code=404, detail="Log not found")

    # log_path can be either:
    # 1. An actual file path (legacy jobs from app/jobs.py)
    # 2. The log content directly (jobs from app/tasks/jobs.py)
    content = job.log_path
    if _is_likely_file_path(job.log_path):
        try:
            log_path = Path(job.log_path)
            if log_path.exists() and log_path.is_file():
                content = log_path.read_text(encoding="utf-8")
        except OSError:
            # Path too long or other OS error - use as content
            pass

    if tail:
        lines = content.splitlines()
        content = "\n".join(lines[-tail:])
    return {"log": content}


@router.get("/labs/{lab_id}/audit")
def audit_log(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, list[schemas.JobOut]]:
    get_lab_or_404(lab_id, database, current_user)
    return list_jobs(lab_id, database, current_user)


@router.post("/labs/{lab_id}/jobs/{job_id}/cancel")
async def cancel_job(
    lab_id: str,
    job_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.JobOut:
    """Cancel a running or queued job.

    Marks the job as 'cancelled' and sets the lab state to 'unknown'
    so reconciliation can determine actual state.
    """
    lab = get_lab_or_404(lab_id, database, current_user)
    job = database.get(models.Job, job_id)

    if not job or job.lab_id != lab_id:
        raise HTTPException(status_code=404, detail="Job not found")

    # Can only cancel queued or running jobs
    if job.status not in ("queued", "running"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel job with status '{job.status}'. Only 'queued' or 'running' jobs can be cancelled."
        )

    logger.info(f"Cancelling job {job_id} for lab {lab_id} (was {job.status})")

    # Mark job as cancelled
    job.status = "cancelled"
    job.completed_at = datetime.now(timezone.utc)

    # Append cancellation note to log
    if job.log_path:
        try:
            with open(job.log_path, "a") as f:
                f.write(f"\n\n--- Job cancelled by user at {job.completed_at.isoformat()} ---\n")
        except Exception:
            pass
    else:
        job.log_path = f"Job cancelled by user at {job.completed_at.isoformat()}"

    # Set lab state to unknown so reconciliation will determine actual state
    lab.state = "unknown"
    lab.state_error = "Job cancelled by user - awaiting state reconciliation"
    lab.state_updated_at = datetime.now(timezone.utc)

    database.commit()
    database.refresh(job)

    # Best-effort attempt to signal agent to stop work (if applicable)
    # Note: This is a fire-and-forget since the agent may not support cancellation
    if job.agent_id:
        try:
            agent = database.get(models.Host, job.agent_id)
            if agent and agent.status == "online":
                # Future: Could add agent cancel endpoint here
                # await agent_client.cancel_job_on_agent(agent, job_id)
                pass
        except Exception as e:
            logger.debug(f"Could not signal agent to cancel job: {e}")

    return _enrich_job_output(job)
