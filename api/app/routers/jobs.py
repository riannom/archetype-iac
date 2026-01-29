"""Lab lifecycle and job management endpoints."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import agent_client, db, models, schemas
from app.auth import get_current_user
from app.db import SessionLocal
from app.netlab import run_netlab_command
from app.storage import lab_workspace, topology_path
from app.tasks.jobs import run_agent_job, run_multihost_deploy, run_multihost_destroy
from app.topology import analyze_topology, graph_to_containerlab_yaml, yaml_to_graph
from app.config import settings
from app.utils.job import get_job_timeout_at, is_job_stuck
from app.utils.lab import get_lab_or_404, get_lab_provider

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

    # Pattern 1: containerlab level=error format
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

    # Pattern 4: Look for common containerlab/docker errors
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
                    # Check for containerlab format in stderr
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
    job_out.error_summary = _extract_error_summary(job.log_path, job.status)

    return job_out


@router.post("/labs/{lab_id}/up")
async def lab_up(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.JobOut:
    lab = get_lab_or_404(lab_id, database, current_user)

    # Get topology YAML (stored in netlab format)
    topo_path = topology_path(lab.id)
    topology_yaml = topo_path.read_text(encoding="utf-8") if topo_path.exists() else ""

    # Analyze topology for multi-host deployment and convert to containerlab format
    is_multihost = False
    clab_yaml = ""
    graph = None
    analysis = None
    if topology_yaml:
        try:
            graph = yaml_to_graph(topology_yaml)
            analysis = analyze_topology(graph)
            is_multihost = not analysis.single_host
            # Convert to containerlab format for deployment
            clab_yaml = graph_to_containerlab_yaml(graph, lab.id)
            logger.info(
                f"Lab {lab_id} topology analysis: "
                f"single_host={analysis.single_host}, "
                f"hosts={list(analysis.placements.keys())}, "
                f"cross_host_links={len(analysis.cross_host_links)}"
            )
        except Exception as e:
            logger.warning(f"Failed to analyze topology for lab {lab_id}: {e}")

    # Get the provider for this lab
    lab_provider = get_lab_provider(lab)

    if is_multihost and analysis:
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
    else:
        # Single-host deployment: check for any healthy agent
        agent = await agent_client.get_agent_for_lab(database, lab, required_provider=lab_provider)
        if not agent:
            raise HTTPException(status_code=503, detail=f"No healthy agent available with {lab_provider} support")

    # Pre-deploy image sync check (if enabled)
    if settings.image_sync_enabled and settings.image_sync_pre_deploy_check and clab_yaml:
        from app.tasks.image_sync import get_images_from_topology, ensure_images_for_deployment

        # Get image references from topology
        image_refs = get_images_from_topology(clab_yaml)
        if image_refs:
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
                all_ready, missing = await ensure_images_for_deployment(
                    target_host_id,
                    image_refs,
                    timeout=settings.image_sync_timeout,
                    database=database,
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

    # Start background task - choose deployment method based on topology
    # Use containerlab-formatted YAML for deployment
    if is_multihost:
        asyncio.create_task(run_multihost_deploy(
            job.id, lab.id, clab_yaml, provider=lab_provider
        ))
    else:
        asyncio.create_task(run_agent_job(
            job.id, lab.id, "up", topology_yaml=clab_yaml, provider=lab_provider
        ))

    return schemas.JobOut.model_validate(job)


@router.post("/labs/{lab_id}/down")
async def lab_down(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.JobOut:
    lab = get_lab_or_404(lab_id, database, current_user)

    # Get topology YAML to check for multi-host
    topo_path = topology_path(lab.id)
    topology_yaml = topo_path.read_text(encoding="utf-8") if topo_path.exists() else ""

    # Analyze topology for multi-host deployment
    is_multihost = False
    if topology_yaml:
        try:
            graph = yaml_to_graph(topology_yaml)
            analysis = analyze_topology(graph)
            is_multihost = not analysis.single_host
        except Exception as e:
            logger.warning(f"Failed to analyze topology for lab {lab_id}: {e}")

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

    # Start background task - choose destroy method based on topology
    if is_multihost:
        asyncio.create_task(run_multihost_destroy(
            job.id, lab.id, topology_yaml, provider=lab_provider
        ))
    else:
        asyncio.create_task(run_agent_job(
            job.id, lab.id, "down", provider=lab_provider
        ))

    return schemas.JobOut.model_validate(job)


@router.post("/labs/{lab_id}/restart")
async def lab_restart(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.JobOut:
    lab = get_lab_or_404(lab_id, database, current_user)

    # Get the provider for this lab
    lab_provider = get_lab_provider(lab)

    # Check for healthy agent with required capability, respecting affinity
    agent = await agent_client.get_agent_for_lab(database, lab, required_provider=lab_provider)
    if not agent:
        raise HTTPException(status_code=503, detail=f"No healthy agent available with {lab_provider} support")

    # Validate and convert topology BEFORE creating jobs
    topo_path = topology_path(lab.id)
    if not topo_path.exists():
        raise HTTPException(status_code=400, detail="No topology file found for this lab")

    try:
        topology_yaml = topo_path.read_text(encoding="utf-8")
        graph = yaml_to_graph(topology_yaml)
        clab_yaml = graph_to_containerlab_yaml(graph, lab.id)
    except Exception as e:
        logger.error(f"Failed to convert topology for restart of lab {lab.id}: {e}")
        raise HTTPException(status_code=400, detail=f"Failed to convert topology: {str(e)}")

    if not clab_yaml or not clab_yaml.strip():
        raise HTTPException(status_code=400, detail="Topology conversion resulted in empty YAML")

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
        session = SessionLocal()
        try:
            dj = session.get(models.Job, down_job.id)
            uj = session.get(models.Job, up_job.id)
            if dj and dj.status == "failed":
                # Mark up job as cancelled since down failed
                if uj:
                    uj.status = "failed"
                    uj.log = "Cancelled: down phase failed"
                    session.commit()
                return
            # Proceed with up phase
            await run_agent_job(up_job.id, lab.id, "up", topology_yaml=clab_yaml, provider=lab_provider)
        finally:
            session.close()

    asyncio.create_task(restart_sequence())

    return schemas.JobOut.model_validate(down_job)


@router.post("/labs/{lab_id}/nodes/{node}/{action}")
async def node_action(
    lab_id: str,
    node: str,
    action: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.JobOut:
    if action not in ("start", "stop"):
        raise HTTPException(status_code=400, detail="Unsupported node action")

    lab = get_lab_or_404(lab_id, database, current_user)

    # Get the provider for this lab
    lab_provider = get_lab_provider(lab)

    # Check for healthy agent with required capability, respecting affinity
    # Node actions must go to the same agent running the lab
    agent = await agent_client.get_agent_for_lab(database, lab, required_provider=lab_provider)
    if not agent:
        raise HTTPException(status_code=503, detail=f"No healthy agent available with {lab_provider} support")

    # Create job record
    job = models.Job(lab_id=lab.id, user_id=current_user.id, action=f"node:{action}:{node}", status="queued")
    database.add(job)
    database.commit()
    database.refresh(job)

    # Start background task
    asyncio.create_task(run_agent_job(job.id, lab.id, f"node:{action}:{node}", provider=lab_provider))

    return schemas.JobOut.model_validate(job)


@router.get("/labs/{lab_id}/status")
async def lab_status(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict:
    lab = get_lab_or_404(lab_id, database, current_user)

    # Get the provider for this lab
    lab_provider = get_lab_provider(lab)

    # Try to get status from the agent managing this lab (respecting affinity)
    agent = await agent_client.get_agent_for_lab(database, lab, required_provider=lab_provider)
    if agent:
        try:
            result = await agent_client.get_lab_status_from_agent(agent, lab.id)
            return {
                "nodes": result.get("nodes", []),
                "error": result.get("error"),
                "agent_id": agent.id,
                "agent_name": agent.name,
            }
        except Exception as e:
            return {"nodes": [], "error": str(e)}

    # Fallback to old netlab command if no agent
    code, stdout, stderr = run_netlab_command(["netlab", "status"], lab_workspace(lab.id))
    if code != 0:
        return {"raw": "", "error": stderr or "netlab status failed"}
    return {"raw": stdout}


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
    log_path = Path(job.log_path)
    if not log_path.exists():
        raise HTTPException(status_code=404, detail="Log not found")
    content = log_path.read_text(encoding="utf-8")
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
