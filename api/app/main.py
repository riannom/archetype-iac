from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
import lzma
import os
import shutil
import subprocess
import tempfile

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect, UploadFile, File
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware
from fastapi.middleware.cors import CORSMiddleware

from app import db, models, schemas
from app.db import SessionLocal
from app.config import settings
from app.auth import get_current_user, hash_password
from app.catalog import list_devices as catalog_devices, list_images as catalog_images
from app.jobs import enqueue_job
from app.middleware import CurrentUserMiddleware
from app.netlab import run_netlab_command
from app.providers import supported_node_actions, supports_node_actions
from app.routers import agents, auth
from app import agent_client
from app.agent_client import AgentError, AgentUnavailableError, AgentJobError
from app.storage import ensure_topology_file, lab_workspace, topology_path
from app.image_store import qcow2_path, ensure_image_store, load_manifest, save_manifest, detect_device_from_filename
from app.topology import graph_to_yaml, graph_to_containerlab_yaml, yaml_to_graph, analyze_topology, split_topology_by_host


logger = logging.getLogger(__name__)

# Background task handle
_agent_monitor_task: asyncio.Task | None = None

# Agent health check interval in seconds
AGENT_HEALTH_CHECK_INTERVAL = 30


async def agent_health_monitor():
    """Background task to monitor agent health and mark stale agents as offline."""
    logger.info("Agent health monitor started")
    while True:
        try:
            await asyncio.sleep(AGENT_HEALTH_CHECK_INTERVAL)
            session = SessionLocal()
            try:
                marked_offline = await agent_client.update_stale_agents(session)
                if marked_offline:
                    logger.info(f"Marked {len(marked_offline)} agent(s) as offline")
            finally:
                session.close()
        except asyncio.CancelledError:
            logger.info("Agent health monitor stopped")
            break
        except Exception as e:
            logger.error(f"Error in agent health monitor: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - start background tasks on startup, cleanup on shutdown."""
    global _agent_monitor_task

    # Startup
    logger.info("Starting Aura API controller")

    # Create database tables
    logger.info("Creating database tables")
    models.Base.metadata.create_all(bind=db.engine)

    # Seed admin user if configured
    if settings.admin_email and settings.admin_password:
        session = SessionLocal()
        try:
            existing = session.query(models.User).filter(models.User.email == settings.admin_email).first()
            if not existing:
                if len(settings.admin_password.encode("utf-8")) > 72:
                    logger.warning("Skipping admin seed: ADMIN_PASSWORD must be 72 bytes or fewer")
                else:
                    admin = models.User(
                        email=settings.admin_email,
                        hashed_password=hash_password(settings.admin_password),
                        is_admin=True,
                    )
                    session.add(admin)
                    session.commit()
                    logger.info(f"Created admin user: {settings.admin_email}")
        finally:
            session.close()

    # Start agent health monitor background task
    _agent_monitor_task = asyncio.create_task(agent_health_monitor())

    yield

    # Shutdown
    logger.info("Shutting down Aura API controller")

    if _agent_monitor_task:
        _agent_monitor_task.cancel()
        try:
            await _agent_monitor_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Netlab GUI API", version="0.1.0", lifespan=lifespan)
if settings.session_secret:
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, same_site="lax")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8090", "http://127.0.0.1:8090"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(CurrentUserMiddleware)
app.include_router(auth.router)
app.include_router(agents.router)


@app.get("/health")
def health(request: Request) -> dict[str, str]:
    user = request.state.user
    return {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "user": user.email if user else "",
    }


@app.get("/devices")
def list_devices() -> dict[str, object]:
    data = catalog_devices()
    if data.get("error"):
        raise HTTPException(status_code=500, detail=data["error"])
    return data


@app.get("/images")
def list_images() -> dict[str, object]:
    data = catalog_images()
    if data.get("error"):
        raise HTTPException(status_code=500, detail=data["error"])
    return data


def get_lab_or_404(lab_id: str, database: Session, user: models.User) -> models.Lab:
    lab = database.get(models.Lab, lab_id)
    if not lab:
        raise HTTPException(status_code=404, detail="Lab not found")
    if lab.owner_id == user.id or user.is_admin:
        return lab
    allowed = (
        database.query(models.Permission)
        .filter(models.Permission.lab_id == lab_id, models.Permission.user_id == user.id)
        .count()
    )
    if not allowed:
        raise HTTPException(status_code=403, detail="Access denied")
    return lab


@app.get("/labs")
def list_labs(
    skip: int = 0,
    limit: int = 50,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, list[schemas.LabOut]]:
    owned = database.query(models.Lab).filter(models.Lab.owner_id == current_user.id)
    shared = (
        database.query(models.Lab)
        .join(models.Permission, models.Permission.lab_id == models.Lab.id)
        .filter(models.Permission.user_id == current_user.id)
    )
    labs = (
        owned.union(shared)
        .order_by(models.Lab.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return {"labs": [schemas.LabOut.model_validate(lab) for lab in labs]}


@app.post("/labs")
def create_lab(
    payload: schemas.LabCreate,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.LabOut:
    lab = models.Lab(name=payload.name, owner_id=current_user.id)
    database.add(lab)
    database.flush()
    workspace = lab_workspace(lab.id)
    workspace.mkdir(parents=True, exist_ok=True)
    lab.workspace_path = str(workspace)
    ensure_topology_file(lab.id)
    database.commit()
    database.refresh(lab)
    return schemas.LabOut.model_validate(lab)


@app.get("/labs/{lab_id}")
def get_lab(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.LabOut:
    lab = get_lab_or_404(lab_id, database, current_user)
    return schemas.LabOut.model_validate(lab)


@app.delete("/labs/{lab_id}")
def delete_lab(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, str]:
    lab = get_lab_or_404(lab_id, database, current_user)
    if lab.owner_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Access denied")
    workspace = lab_workspace(lab.id)
    if workspace.exists():
        for path in workspace.glob("**/*"):
            if path.is_file():
                path.unlink()
        for path in sorted(workspace.glob("**/*"), reverse=True):
            if path.is_dir():
                path.rmdir()
        workspace.rmdir()
    database.delete(lab)
    database.commit()
    return {"status": "deleted"}


@app.post("/labs/{lab_id}/clone")
def clone_lab(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.LabOut:
    lab = get_lab_or_404(lab_id, database, current_user)
    clone = models.Lab(name=f"{lab.name} (copy)", owner_id=current_user.id)
    database.add(clone)
    database.flush()
    source = lab_workspace(lab.id)
    target = lab_workspace(clone.id)
    target.mkdir(parents=True, exist_ok=True)
    if source.exists():
        for path in source.glob("**/*"):
            if path.is_file():
                relative = path.relative_to(source)
                dest = target / relative
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dest)
    clone.workspace_path = str(target)
    database.commit()
    database.refresh(clone)
    return schemas.LabOut.model_validate(clone)


@app.post("/labs/{lab_id}/import-yaml")
def import_yaml(
    lab_id: str,
    payload: schemas.LabYamlIn,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.LabOut:
    lab = get_lab_or_404(lab_id, database, current_user)
    workspace = lab_workspace(lab.id)
    workspace.mkdir(parents=True, exist_ok=True)
    topology_path(lab.id).write_text(payload.content, encoding="utf-8")
    return schemas.LabOut.model_validate(lab)


@app.get("/labs/{lab_id}/export-yaml")
def export_yaml(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.LabYamlOut:
    lab = get_lab_or_404(lab_id, database, current_user)
    topo_path = topology_path(lab.id)
    if not topo_path.exists():
        raise HTTPException(status_code=404, detail="Topology not found")
    return schemas.LabYamlOut(content=topo_path.read_text(encoding="utf-8"))


@app.post("/labs/{lab_id}/import-graph")
def import_graph(
    lab_id: str,
    payload: schemas.TopologyGraph,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.LabOut:
    lab = get_lab_or_404(lab_id, database, current_user)
    workspace = lab_workspace(lab.id)
    workspace.mkdir(parents=True, exist_ok=True)
    yaml_content = graph_to_yaml(payload)
    topology_path(lab.id).write_text(yaml_content, encoding="utf-8")
    return schemas.LabOut.model_validate(lab)


@app.get("/labs/{lab_id}/export-graph")
def export_graph(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.TopologyGraph:
    lab = get_lab_or_404(lab_id, database, current_user)
    topo_path = topology_path(lab.id)
    if not topo_path.exists():
        raise HTTPException(status_code=404, detail="Topology not found")
    return yaml_to_graph(topo_path.read_text(encoding="utf-8"))


def update_lab_state(session: Session, lab_id: str, state: str, agent_id: str | None = None, error: str | None = None):
    """Update lab state in database."""
    lab = session.get(models.Lab, lab_id)
    if lab:
        lab.state = state
        lab.state_updated_at = datetime.utcnow()
        if agent_id is not None:
            lab.agent_id = agent_id
        if error is not None:
            lab.state_error = error
        elif state not in ("error", "unknown"):
            lab.state_error = None  # Clear error on success
        session.commit()


async def run_agent_job(
    job_id: str,
    lab_id: str,
    action: str,
    topology_yaml: str | None = None,
    node_name: str | None = None,
    required_provider: str = "containerlab",
):
    """Run a job on an agent in the background.

    Handles errors gracefully and provides detailed error messages.
    Updates lab state based on job outcome.

    Args:
        job_id: The job ID
        lab_id: The lab ID
        action: Action to perform (up, down, node:start:name, etc.)
        topology_yaml: Topology YAML for deploy actions
        node_name: Node name for node actions
        required_provider: Provider required for the job (default: containerlab)
    """
    session = SessionLocal()
    try:
        job = session.get(models.Job, job_id)
        if not job:
            logger.error(f"Job {job_id} not found in database")
            return

        lab = session.get(models.Lab, lab_id)
        if not lab:
            logger.error(f"Lab {lab_id} not found in database")
            job.status = "failed"
            job.completed_at = datetime.utcnow()
            job.log_path = f"ERROR: Lab {lab_id} not found"
            session.commit()
            return

        # Find a healthy agent with required capability, respecting affinity
        agent = await agent_client.get_agent_for_lab(
            session,
            lab,
            required_provider=required_provider,
        )
        if not agent:
            job.status = "failed"
            job.completed_at = datetime.utcnow()
            job.log_path = (
                f"ERROR: No healthy agent available.\n\n"
                f"Required provider: {required_provider}\n\n"
                f"Possible causes:\n"
                f"- No agents are registered\n"
                f"- All agents are offline or unresponsive\n"
                f"- No agent supports the required provider\n"
                f"- All capable agents are at capacity\n\n"
                f"Check agent status and connectivity."
            )
            update_lab_state(session, lab_id, "error", error="No healthy agent available")
            session.commit()
            logger.warning(f"Job {job_id} failed: no healthy agent available for provider {required_provider}")
            return

        # Update job with agent assignment and start time
        job.status = "running"
        job.agent_id = agent.id
        job.started_at = datetime.utcnow()
        session.commit()

        # Update lab state based on action
        if action == "up":
            update_lab_state(session, lab_id, "starting", agent_id=agent.id)
        elif action == "down":
            update_lab_state(session, lab_id, "stopping", agent_id=agent.id)

        logger.info(f"Job {job_id} started: {action} on lab {lab_id} via agent {agent.id}")

        try:
            if action == "up":
                result = await agent_client.deploy_to_agent(agent, job_id, lab_id, topology_yaml or "")
            elif action == "down":
                result = await agent_client.destroy_on_agent(agent, job_id, lab_id)
            elif action.startswith("node:"):
                # Parse node action: "node:start:nodename" or "node:stop:nodename"
                parts = action.split(":", 2)
                node_action_type = parts[1] if len(parts) > 1 else ""
                node = parts[2] if len(parts) > 2 else ""
                result = await agent_client.node_action_on_agent(agent, job_id, lab_id, node, node_action_type)
            else:
                result = {"status": "failed", "error_message": f"Unknown action: {action}"}

            # Update job based on result
            job.completed_at = datetime.utcnow()

            if result.get("status") == "completed":
                job.status = "completed"
                log_content = f"Job completed successfully.\n\n"

                # Update lab state based on completed action
                if action == "up":
                    update_lab_state(session, lab_id, "running", agent_id=agent.id)
                elif action == "down":
                    update_lab_state(session, lab_id, "stopped")

            else:
                job.status = "failed"
                error_msg = result.get('error_message', 'Unknown error')
                log_content = f"Job failed.\n\nError: {error_msg}\n\n"

                # Update lab state to error
                update_lab_state(session, lab_id, "error", error=error_msg)

            # Append stdout/stderr if present
            stdout = result.get("stdout", "").strip()
            stderr = result.get("stderr", "").strip()
            if stdout:
                log_content += f"=== STDOUT ===\n{stdout}\n\n"
            if stderr:
                log_content += f"=== STDERR ===\n{stderr}\n"

            job.log_path = log_content.strip()
            session.commit()
            logger.info(f"Job {job_id} completed with status: {job.status}")

        except AgentUnavailableError as e:
            job.status = "failed"
            job.completed_at = datetime.utcnow()
            job.log_path = (
                f"ERROR: Agent became unavailable during job execution.\n\n"
                f"Agent ID: {e.agent_id or 'unknown'}\n"
                f"Details: {e.message}\n\n"
                f"The job could not be completed. The lab may be in an inconsistent state.\n"
                f"Consider checking the lab status and retrying the operation."
            )

            # Update lab state to unknown (we don't know what state it's in)
            update_lab_state(session, lab_id, "unknown", error=f"Agent unavailable: {e.message}")

            session.commit()
            logger.error(f"Job {job_id} failed: agent unavailable - {e.message}")

            # Mark agent as offline if we know which one failed
            if e.agent_id:
                await agent_client.mark_agent_offline(session, e.agent_id)

        except AgentJobError as e:
            job.status = "failed"
            job.completed_at = datetime.utcnow()
            log_content = f"ERROR: Job execution failed on agent.\n\nDetails: {e.message}\n\n"
            if e.stdout:
                log_content += f"=== STDOUT ===\n{e.stdout}\n\n"
            if e.stderr:
                log_content += f"=== STDERR ===\n{e.stderr}\n"
            job.log_path = log_content.strip()

            # Update lab state to error
            update_lab_state(session, lab_id, "error", error=e.message)

            session.commit()
            logger.error(f"Job {job_id} failed: agent job error - {e.message}")

        except Exception as e:
            job.status = "failed"
            job.completed_at = datetime.utcnow()
            job.log_path = (
                f"ERROR: Unexpected error during job execution.\n\n"
                f"Type: {type(e).__name__}\n"
                f"Details: {str(e)}\n\n"
                f"Please report this error if it persists."
            )

            # Update lab state to error
            update_lab_state(session, lab_id, "error", error=str(e))

            session.commit()
            logger.exception(f"Job {job_id} failed with unexpected error: {e}")

    finally:
        session.close()


async def run_multihost_deploy(
    job_id: str,
    lab_id: str,
    topology_yaml: str,
    required_provider: str = "containerlab",
):
    """Deploy a lab across multiple hosts.

    This function:
    1. Parses the topology to find host assignments
    2. Splits the topology by host
    3. Deploys sub-topologies to each agent in parallel
    4. Sets up VXLAN overlay links for cross-host connections

    Args:
        job_id: The job ID
        lab_id: The lab ID
        topology_yaml: Full topology YAML
        required_provider: Provider required for the job
    """
    import yaml as pyyaml

    session = SessionLocal()
    try:
        job = session.get(models.Job, job_id)
        if not job:
            logger.error(f"Job {job_id} not found in database")
            return

        lab = session.get(models.Lab, lab_id)
        if not lab:
            logger.error(f"Lab {lab_id} not found in database")
            job.status = "failed"
            job.completed_at = datetime.utcnow()
            job.log_path = f"ERROR: Lab {lab_id} not found"
            session.commit()
            return

        # Parse topology YAML to graph
        graph = yaml_to_graph(topology_yaml)

        # Analyze for multi-host deployment
        analysis = analyze_topology(graph)

        logger.info(
            f"Multi-host deployment for lab {lab_id}: "
            f"{len(analysis.placements)} hosts, "
            f"{len(analysis.cross_host_links)} cross-host links"
        )

        # Update job status
        job.status = "running"
        job.started_at = datetime.utcnow()
        session.commit()

        update_lab_state(session, lab_id, "starting")

        # Map host names to agents
        host_to_agent: dict[str, models.Host] = {}
        missing_hosts = []

        for host_name in analysis.placements:
            agent = await agent_client.get_agent_by_name(
                session, host_name, required_provider=required_provider
            )
            if agent:
                host_to_agent[host_name] = agent
            else:
                missing_hosts.append(host_name)

        if missing_hosts:
            error_msg = f"Missing or unhealthy agents for hosts: {', '.join(missing_hosts)}"
            job.status = "failed"
            job.completed_at = datetime.utcnow()
            job.log_path = f"ERROR: {error_msg}"
            update_lab_state(session, lab_id, "error", error=error_msg)
            session.commit()
            logger.error(f"Job {job_id} failed: {error_msg}")
            return

        # Split topology by host
        host_topologies = split_topology_by_host(graph, analysis)

        # Deploy to each host in parallel
        deploy_tasks = []
        deploy_results: dict[str, dict] = {}
        log_parts = []

        for host_name, sub_graph in host_topologies.items():
            agent = host_to_agent[host_name]
            sub_yaml = graph_to_containerlab_yaml(sub_graph, lab_id)

            logger.info(
                f"Deploying to host {host_name} (agent {agent.id}): "
                f"{len(sub_graph.nodes)} nodes"
            )
            log_parts.append(f"=== Host: {host_name} ({agent.id}) ===")
            log_parts.append(f"Nodes: {', '.join(n.name for n in sub_graph.nodes)}")

            deploy_tasks.append(
                agent_client.deploy_to_agent(agent, job_id, lab_id, sub_yaml)
            )

        # Wait for all deployments
        results = await asyncio.gather(*deploy_tasks, return_exceptions=True)

        deploy_success = True
        for i, (host_name, result) in enumerate(zip(host_topologies.keys(), results)):
            if isinstance(result, Exception):
                log_parts.append(f"\nDeploy to {host_name} FAILED: {result}")
                deploy_success = False
            else:
                deploy_results[host_name] = result
                status = result.get("status", "unknown")
                log_parts.append(f"\nDeploy to {host_name}: {status}")
                if result.get("stdout"):
                    log_parts.append(f"STDOUT:\n{result['stdout']}")
                if result.get("stderr"):
                    log_parts.append(f"STDERR:\n{result['stderr']}")
                if status != "completed":
                    deploy_success = False

        if not deploy_success:
            job.status = "failed"
            job.completed_at = datetime.utcnow()
            job.log_path = "\n".join(log_parts)
            update_lab_state(session, lab_id, "error", error="Deployment failed on one or more hosts")
            session.commit()
            logger.error(f"Job {job_id} failed: deployment error on one or more hosts")
            return

        # Set up cross-host links via VXLAN overlay
        if analysis.cross_host_links:
            log_parts.append("\n=== Cross-Host Links ===")
            logger.info(f"Setting up {len(analysis.cross_host_links)} cross-host links")

            for chl in analysis.cross_host_links:
                agent_a = host_to_agent.get(chl.host_a)
                agent_b = host_to_agent.get(chl.host_b)

                if not agent_a or not agent_b:
                    log_parts.append(
                        f"SKIP {chl.link_id}: missing agent for {chl.host_a} or {chl.host_b}"
                    )
                    continue

                # Get container names from containerlab naming convention
                # Containerlab names containers as: clab-{lab_id}-{node_name}
                import re
                safe_lab_id = re.sub(r'[^a-zA-Z0-9_-]', '', lab_id)[:20]
                container_a = f"clab-{safe_lab_id}-{chl.node_a}"
                container_b = f"clab-{safe_lab_id}-{chl.node_b}"

                result = await agent_client.setup_cross_host_link(
                    database=session,
                    lab_id=lab_id,
                    link_id=chl.link_id,
                    agent_a=agent_a,
                    agent_b=agent_b,
                    node_a=container_a,
                    interface_a=chl.interface_a,
                    node_b=container_b,
                    interface_b=chl.interface_b,
                    ip_a=chl.ip_a,
                    ip_b=chl.ip_b,
                )

                if result.get("success"):
                    log_parts.append(
                        f"Link {chl.link_id}: OK (VNI {result.get('vni')})"
                    )
                else:
                    log_parts.append(
                        f"Link {chl.link_id}: FAILED - {result.get('error')}"
                    )
                    # Don't fail the whole job for overlay issues - containers are running

        # Mark job as completed
        job.status = "completed"
        job.completed_at = datetime.utcnow()
        job.log_path = "\n".join(log_parts)

        # Update lab state - use first agent as primary
        first_agent = list(host_to_agent.values())[0] if host_to_agent else None
        update_lab_state(
            session, lab_id, "running",
            agent_id=first_agent.id if first_agent else None
        )
        session.commit()

        logger.info(f"Job {job_id} completed: multi-host deployment successful")

    except Exception as e:
        logger.exception(f"Job {job_id} failed with unexpected error: {e}")
        try:
            job = session.get(models.Job, job_id)
            if job:
                job.status = "failed"
                job.completed_at = datetime.utcnow()
                job.log_path = f"ERROR: Unexpected error: {e}"
                update_lab_state(session, lab_id, "error", error=str(e))
                session.commit()
        except Exception:
            pass
    finally:
        session.close()


async def run_multihost_destroy(
    job_id: str,
    lab_id: str,
    topology_yaml: str,
    required_provider: str = "containerlab",
):
    """Destroy a multi-host lab.

    This function:
    1. Parses the topology to find host assignments
    2. Cleans up overlay networks on each agent
    3. Destroys containers on each agent

    Args:
        job_id: The job ID
        lab_id: The lab ID
        topology_yaml: Full topology YAML (to identify hosts)
        required_provider: Provider required for the job
    """
    session = SessionLocal()
    try:
        job = session.get(models.Job, job_id)
        if not job:
            logger.error(f"Job {job_id} not found in database")
            return

        lab = session.get(models.Lab, lab_id)
        if not lab:
            logger.error(f"Lab {lab_id} not found in database")
            job.status = "failed"
            job.completed_at = datetime.utcnow()
            job.log_path = f"ERROR: Lab {lab_id} not found"
            session.commit()
            return

        # Parse topology YAML to find hosts
        graph = yaml_to_graph(topology_yaml)
        analysis = analyze_topology(graph)

        logger.info(
            f"Multi-host destroy for lab {lab_id}: "
            f"{len(analysis.placements)} hosts"
        )

        # Update job status
        job.status = "running"
        job.started_at = datetime.utcnow()
        session.commit()

        update_lab_state(session, lab_id, "stopping")

        # Map host names to agents
        host_to_agent: dict[str, models.Host] = {}
        log_parts = []

        for host_name in analysis.placements:
            agent = await agent_client.get_agent_by_name(
                session, host_name, required_provider=required_provider
            )
            if agent:
                host_to_agent[host_name] = agent
            else:
                log_parts.append(f"WARNING: Agent '{host_name}' not found, skipping")

        if not host_to_agent:
            # No agents found, try single-agent destroy as fallback
            error_msg = "No agents found for multi-host destroy"
            job.status = "failed"
            job.completed_at = datetime.utcnow()
            job.log_path = f"ERROR: {error_msg}"
            update_lab_state(session, lab_id, "error", error=error_msg)
            session.commit()
            logger.error(f"Job {job_id} failed: {error_msg}")
            return

        # First, clean up overlay networks on all agents
        if analysis.cross_host_links:
            log_parts.append("=== Cleaning up overlay networks ===")
            for host_name, agent in host_to_agent.items():
                result = await agent_client.cleanup_overlay_on_agent(agent, lab_id)
                log_parts.append(
                    f"{host_name}: {result.get('tunnels_deleted', 0)} tunnels, "
                    f"{result.get('bridges_deleted', 0)} bridges deleted"
                )
                if result.get("errors"):
                    log_parts.append(f"  Errors: {result['errors']}")

        # Destroy containers on each host in parallel
        log_parts.append("\n=== Destroying containers ===")
        destroy_tasks = []

        for host_name, agent in host_to_agent.items():
            logger.info(f"Destroying on host {host_name} (agent {agent.id})")
            destroy_tasks.append(
                agent_client.destroy_on_agent(agent, job_id, lab_id)
            )

        # Wait for all destroys
        results = await asyncio.gather(*destroy_tasks, return_exceptions=True)

        all_success = True
        for host_name, result in zip(host_to_agent.keys(), results):
            if isinstance(result, Exception):
                log_parts.append(f"{host_name}: FAILED - {result}")
                all_success = False
            else:
                status = result.get("status", "unknown")
                log_parts.append(f"{host_name}: {status}")
                if result.get("stdout"):
                    log_parts.append(f"  STDOUT: {result['stdout'][:200]}")
                if result.get("stderr"):
                    log_parts.append(f"  STDERR: {result['stderr'][:200]}")
                if status != "completed":
                    all_success = False

        # Update job status
        if all_success:
            job.status = "completed"
            update_lab_state(session, lab_id, "stopped")
        else:
            job.status = "completed"  # Mark as completed even with partial failures
            update_lab_state(session, lab_id, "stopped")
            log_parts.append("\nWARNING: Some hosts may have had issues during destroy")

        job.completed_at = datetime.utcnow()
        job.log_path = "\n".join(log_parts)
        session.commit()

        logger.info(f"Job {job_id} completed: multi-host destroy {'successful' if all_success else 'with warnings'}")

    except Exception as e:
        logger.exception(f"Job {job_id} failed with unexpected error: {e}")
        try:
            job = session.get(models.Job, job_id)
            if job:
                job.status = "failed"
                job.completed_at = datetime.utcnow()
                job.log_path = f"ERROR: Unexpected error: {e}"
                update_lab_state(session, lab_id, "error", error=str(e))
                session.commit()
        except Exception:
            pass
    finally:
        session.close()


@app.post("/labs/{lab_id}/up")
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

    if is_multihost:
        # Multi-host deployment: validate all required agents exist
        missing_hosts = []
        for host_name in analysis.placements:
            agent = await agent_client.get_agent_by_name(
                database, host_name, required_provider="containerlab"
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
        agent = await agent_client.get_agent_for_lab(database, lab, required_provider="containerlab")
        if not agent:
            raise HTTPException(status_code=503, detail="No healthy agent available with containerlab support")

    # Create job record
    job = models.Job(lab_id=lab.id, user_id=current_user.id, action="up", status="queued")
    database.add(job)
    database.commit()
    database.refresh(job)

    # Start background task - choose deployment method based on topology
    # Use containerlab-formatted YAML for deployment
    if is_multihost:
        asyncio.create_task(run_multihost_deploy(
            job.id, lab.id, clab_yaml, required_provider="containerlab"
        ))
    else:
        asyncio.create_task(run_agent_job(
            job.id, lab.id, "up", topology_yaml=clab_yaml, required_provider="containerlab"
        ))

    return schemas.JobOut.model_validate(job)


@app.post("/labs/{lab_id}/down")
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

    if not is_multihost:
        # Single-host: check for healthy agent with required capability
        agent = await agent_client.get_agent_for_lab(database, lab, required_provider="containerlab")
        if not agent:
            raise HTTPException(status_code=503, detail="No healthy agent available with containerlab support")

    # Create job record
    job = models.Job(lab_id=lab.id, user_id=current_user.id, action="down", status="queued")
    database.add(job)
    database.commit()
    database.refresh(job)

    # Start background task - choose destroy method based on topology
    if is_multihost:
        asyncio.create_task(run_multihost_destroy(
            job.id, lab.id, topology_yaml, required_provider="containerlab"
        ))
    else:
        asyncio.create_task(run_agent_job(
            job.id, lab.id, "down", required_provider="containerlab"
        ))

    return schemas.JobOut.model_validate(job)


@app.post("/labs/{lab_id}/restart")
async def lab_restart(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.JobOut:
    lab = get_lab_or_404(lab_id, database, current_user)

    # Check for healthy agent with required capability, respecting affinity
    agent = await agent_client.get_agent_for_lab(database, lab, required_provider="containerlab")
    if not agent:
        raise HTTPException(status_code=503, detail="No healthy agent available with containerlab support")

    # Create job record - restart is down then up
    job = models.Job(lab_id=lab.id, user_id=current_user.id, action="restart", status="queued")
    database.add(job)
    database.commit()
    database.refresh(job)

    # Get topology YAML for the up phase and convert to containerlab format
    topo_path = topology_path(lab.id)
    clab_yaml = ""
    if topo_path.exists():
        try:
            topology_yaml = topo_path.read_text(encoding="utf-8")
            graph = yaml_to_graph(topology_yaml)
            clab_yaml = graph_to_containerlab_yaml(graph, lab.id)
        except Exception as e:
            logger.warning(f"Failed to convert topology for restart of lab {lab.id}: {e}")

    # For restart, we do down then up sequentially
    async def restart_sequence():
        await run_agent_job(job.id, lab.id, "down", required_provider="containerlab")
        # Only do up if down succeeded
        session = SessionLocal()
        try:
            j = session.get(models.Job, job.id)
            if j and j.status != "failed":
                j.status = "running"
                session.commit()
                await run_agent_job(job.id, lab.id, "up", topology_yaml=clab_yaml, required_provider="containerlab")
        finally:
            session.close()

    asyncio.create_task(restart_sequence())

    return schemas.JobOut.model_validate(job)


@app.post("/labs/{lab_id}/nodes/{node}/{action}")
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

    # Check for healthy agent with required capability, respecting affinity
    # Node actions must go to the same agent running the lab
    agent = await agent_client.get_agent_for_lab(database, lab, required_provider="containerlab")
    if not agent:
        raise HTTPException(status_code=503, detail="No healthy agent available with containerlab support")

    # Create job record
    job = models.Job(lab_id=lab.id, user_id=current_user.id, action=f"node:{action}:{node}", status="queued")
    database.add(job)
    database.commit()
    database.refresh(job)

    # Start background task
    asyncio.create_task(run_agent_job(job.id, lab.id, f"node:{action}:{node}", required_provider="containerlab"))

    return schemas.JobOut.model_validate(job)


@app.get("/labs/{lab_id}/status")
async def lab_status(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict:
    lab = get_lab_or_404(lab_id, database, current_user)

    # Try to get status from the agent managing this lab (respecting affinity)
    agent = await agent_client.get_agent_for_lab(database, lab, required_provider="containerlab")
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


@app.get("/labs/{lab_id}/jobs")
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
    return {"jobs": [schemas.JobOut.model_validate(job) for job in jobs]}


@app.get("/labs/{lab_id}/permissions")
def list_permissions(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, list[schemas.PermissionOut]]:
    get_lab_or_404(lab_id, database, current_user)
    permissions = database.query(models.Permission).filter(models.Permission.lab_id == lab_id).all()
    output = []
    for perm in permissions:
        user = database.get(models.User, perm.user_id)
        output.append(
            schemas.PermissionOut(
                id=perm.id,
                lab_id=perm.lab_id,
                user_id=perm.user_id,
                role=perm.role,
                created_at=perm.created_at,
                user_email=user.email if user else None,
            )
        )
    return {"permissions": output}


@app.post("/labs/{lab_id}/permissions")
def add_permission(
    lab_id: str,
    payload: schemas.PermissionCreate,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.PermissionOut:
    lab = get_lab_or_404(lab_id, database, current_user)
    if lab.owner_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Access denied")
    user = database.query(models.User).filter(models.User.email == payload.user_email).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    permission = models.Permission(lab_id=lab_id, user_id=user.id, role=payload.role)
    database.add(permission)
    database.commit()
    database.refresh(permission)
    return schemas.PermissionOut(
        id=permission.id,
        lab_id=permission.lab_id,
        user_id=permission.user_id,
        role=permission.role,
        created_at=permission.created_at,
        user_email=user.email,
    )


@app.delete("/labs/{lab_id}/permissions/{permission_id}")
def delete_permission(
    lab_id: str,
    permission_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, str]:
    lab = get_lab_or_404(lab_id, database, current_user)
    if lab.owner_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Access denied")
    permission = database.get(models.Permission, permission_id)
    if not permission or permission.lab_id != lab_id:
        raise HTTPException(status_code=404, detail="Permission not found")
    database.delete(permission)
    database.commit()
    return {"status": "deleted"}


@app.get("/labs/{lab_id}/audit")
def audit_log(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, list[schemas.JobOut]]:
    get_lab_or_404(lab_id, database, current_user)
    return list_jobs(lab_id, database, current_user)


@app.get("/labs/{lab_id}/jobs/{job_id}")
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
    return schemas.JobOut.model_validate(job)


@app.get("/labs/{lab_id}/jobs/{job_id}/log")
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


@app.websocket("/labs/{lab_id}/nodes/{node}/console")
async def console_ws(websocket: WebSocket, lab_id: str, node: str) -> None:
    """Proxy console WebSocket to agent."""
    await websocket.accept()

    database = SessionLocal()
    try:
        lab = database.get(models.Lab, lab_id)
        if not lab:
            await websocket.send_text("Lab not found\r\n")
            await websocket.close(code=1008)
            return

        # For multi-host labs, find which agent has the specific node
        agent = None
        topo_path = topology_path(lab.id)
        if topo_path.exists():
            try:
                topology_yaml = topo_path.read_text(encoding="utf-8")
                graph = yaml_to_graph(topology_yaml)
                analysis = analyze_topology(graph)

                # Check if this is a multi-host lab and find the node's host
                if not analysis.single_host:
                    for host_id, placements in analysis.placements.items():
                        for p in placements:
                            if p.node_name == node:
                                # Found the host for this node, get the agent
                                agent = await agent_client.get_agent_by_name(
                                    database, host_id, required_provider="containerlab"
                                )
                                break
                        if agent:
                            break
            except Exception as e:
                logger.warning(f"Console: topology parsing failed for {lab_id}: {e}")
                # Fall back to default behavior

        # If not found via topology (single-host or node not found), use lab's agent
        if not agent:
            agent = await agent_client.get_agent_for_lab(database, lab, required_provider="containerlab")

        if not agent:
            await websocket.send_text("No healthy agent available\r\n")
            await websocket.close(code=1011)
            return

        # Get agent WebSocket URL
        agent_ws_url = agent_client.get_agent_console_url(agent, lab_id, node)

    finally:
        database.close()

    # Connect to agent WebSocket and proxy
    import websockets

    logger.info(f"Console: connecting to agent at {agent_ws_url}")

    try:
        async with websockets.connect(agent_ws_url) as agent_ws:
            async def forward_to_client():
                """Forward data from agent to client."""
                try:
                    async for message in agent_ws:
                        if isinstance(message, bytes):
                            await websocket.send_bytes(message)
                        else:
                            await websocket.send_text(message)
                except Exception:
                    pass

            async def forward_to_agent():
                """Forward data from client to agent."""
                try:
                    while True:
                        message = await websocket.receive()
                        if message["type"] == "websocket.disconnect":
                            break
                        elif message["type"] == "websocket.receive":
                            if "text" in message:
                                await agent_ws.send(message["text"])
                            elif "bytes" in message:
                                await agent_ws.send(message["bytes"])
                except WebSocketDisconnect:
                    pass
                except Exception:
                    pass

            # Run both directions concurrently
            to_client_task = asyncio.create_task(forward_to_client())
            to_agent_task = asyncio.create_task(forward_to_agent())

            try:
                done, pending = await asyncio.wait(
                    [to_client_task, to_agent_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )

                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

            finally:
                pass

    except Exception as e:
        logger.error(f"Console connection failed to {agent_ws_url}: {e}")
        try:
            await websocket.send_text(f"Console connection failed: {e}\r\n")
        except Exception:
            pass

    try:
        await websocket.close()
    except Exception:
        pass


@app.post("/images/load")
def load_image(
    file: UploadFile = File(...),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, str]:
    filename = file.filename or "image.tar"
    suffixes = Path(filename).suffixes
    suffix = "".join(suffixes) if suffixes else ".tar"
    temp_path = ""
    load_path = ""
    decompressed_path = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
            shutil.copyfileobj(file.file, tmp_file)
            temp_path = tmp_file.name
        load_path = temp_path
        if filename.lower().endswith((".tar.xz", ".txz", ".xz")):
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".tar") as tmp_tar:
                    with lzma.open(temp_path, "rb") as source:
                        shutil.copyfileobj(source, tmp_tar)
                    decompressed_path = tmp_tar.name
                load_path = decompressed_path
            except lzma.LZMAError as exc:
                raise HTTPException(status_code=400, detail=f"Failed to decompress archive: {exc}") from exc
        result = subprocess.run(
            ["docker", "load", "-i", load_path],
            capture_output=True,
            text=True,
            check=False,
        )
        output = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0:
            raise HTTPException(status_code=500, detail=output.strip() or "docker load failed")
        loaded_images = []
        for line in output.splitlines():
            if "Loaded image:" in line:
                loaded_images.append(line.split("Loaded image:", 1)[-1].strip())
            elif "Loaded image ID:" in line:
                loaded_images.append(line.split("Loaded image ID:", 1)[-1].strip())
        if not loaded_images:
            raise HTTPException(status_code=500, detail=output.strip() or "No images detected in archive")
        manifest = load_manifest()
        for image_ref in loaded_images:
            device_id, version = detect_device_from_filename(image_ref)
            manifest["images"].append(
                {
                    "id": f"docker:{image_ref}",
                    "kind": "docker",
                    "reference": image_ref,
                    "device_id": device_id,
                    "version": version,
                }
            )
        save_manifest(manifest)
        return {"output": output.strip() or "Image loaded", "images": loaded_images}
    finally:
        file.file.close()
        if decompressed_path and os.path.exists(decompressed_path):
            os.unlink(decompressed_path)
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


@app.post("/images/qcow2")
def upload_qcow2(
    file: UploadFile = File(...),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, str]:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename")
    if not file.filename.lower().endswith((".qcow2", ".qcow")):
        raise HTTPException(status_code=400, detail="File must be a qcow2 image")
    destination = qcow2_path(Path(file.filename).name)
    try:
        with destination.open("wb") as handle:
            shutil.copyfileobj(file.file, handle)
    finally:
        file.file.close()
    manifest = load_manifest()
    device_id, version = detect_device_from_filename(destination.name)
    manifest["images"].append(
        {
            "id": f"qcow2:{destination.name}",
            "kind": "qcow2",
            "reference": str(destination),
            "device_id": device_id,
            "version": version,
            "filename": destination.name,
        }
    )
    save_manifest(manifest)
    return {"path": str(destination), "filename": destination.name}


@app.get("/images/qcow2")
def list_qcow2(
    current_user: models.User = Depends(get_current_user),
) -> dict[str, list[dict[str, str]]]:
    root = ensure_image_store()
    files = []
    for path in sorted(root.glob("*.qcow2")) + sorted(root.glob("*.qcow")):
        files.append({"filename": path.name, "path": str(path)})
    return {"files": files}


@app.get("/images/library")
def list_image_library(
    current_user: models.User = Depends(get_current_user),
) -> dict[str, list[dict[str, object]]]:
    manifest = load_manifest()
    return {"images": manifest.get("images", [])}


# --- Reconciliation Endpoints ---

@app.post("/reconcile")
async def reconcile_state(
    cleanup_orphans: bool = False,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """Reconcile lab states with actual container status on agents.

    This endpoint queries all healthy agents to discover running containers
    and updates the database to match reality.

    Args:
        cleanup_orphans: If True, also remove containers for labs not in DB

    Returns:
        Summary of reconciliation actions taken
    """
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")

    logger.info("Starting reconciliation")

    result = {
        "agents_queried": 0,
        "labs_updated": 0,
        "labs_discovered": [],
        "orphans_cleaned": [],
        "errors": [],
    }

    # Get all healthy agents
    agents = database.query(models.Host).filter(models.Host.status == "online").all()

    if not agents:
        result["errors"].append("No healthy agents available")
        return result

    # Get all labs from database
    all_labs = database.query(models.Lab).all()
    lab_ids = {lab.id for lab in all_labs}
    lab_by_id = {lab.id: lab for lab in all_labs}

    # Query each agent for discovered labs
    for agent in agents:
        try:
            discovered = await agent_client.discover_labs_on_agent(agent)
            result["agents_queried"] += 1

            for lab_info in discovered.get("labs", []):
                lab_id = lab_info.get("lab_id")
                nodes = lab_info.get("nodes", [])

                if lab_id in lab_by_id:
                    # Lab exists in DB, update its state based on containers
                    lab = lab_by_id[lab_id]

                    # Determine lab state from node states
                    if not nodes:
                        new_state = "stopped"
                    elif all(n.get("status") == "running" for n in nodes):
                        new_state = "running"
                    elif any(n.get("status") == "running" for n in nodes):
                        new_state = "running"  # Partially running = running
                    else:
                        new_state = "stopped"

                    if lab.state != new_state:
                        logger.info(f"Updating lab {lab_id} state: {lab.state} -> {new_state}")
                        lab.state = new_state
                        lab.state_updated_at = datetime.utcnow()
                        lab.agent_id = agent.id
                        result["labs_updated"] += 1

                    result["labs_discovered"].append({
                        "lab_id": lab_id,
                        "state": new_state,
                        "node_count": len(nodes),
                        "agent_id": agent.id,
                    })
                else:
                    # Lab has containers but not in DB - orphan
                    result["labs_discovered"].append({
                        "lab_id": lab_id,
                        "state": "orphan",
                        "node_count": len(nodes),
                        "agent_id": agent.id,
                    })

            # Clean up orphans if requested
            if cleanup_orphans:
                cleanup_result = await agent_client.cleanup_orphans_on_agent(agent, list(lab_ids))
                if cleanup_result.get("removed_containers"):
                    result["orphans_cleaned"].extend(cleanup_result["removed_containers"])
                    logger.info(f"Cleaned up {len(cleanup_result['removed_containers'])} orphan containers on agent {agent.id}")

        except Exception as e:
            error_msg = f"Error querying agent {agent.id}: {str(e)}"
            result["errors"].append(error_msg)
            logger.error(error_msg)

    # Update labs that have no containers running (if they were marked running)
    discovered_lab_ids = {d["lab_id"] for d in result["labs_discovered"] if d["state"] != "orphan"}
    for lab in all_labs:
        if lab.id not in discovered_lab_ids and lab.state == "running":
            logger.info(f"Lab {lab.id} has no containers, marking as stopped")
            lab.state = "stopped"
            lab.state_updated_at = datetime.utcnow()
            result["labs_updated"] += 1

    database.commit()
    logger.info(f"Reconciliation complete: {result['labs_updated']} labs updated")

    return result


@app.get("/labs/{lab_id}/refresh-status")
async def refresh_lab_status(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """Refresh a single lab's status from the agent.

    This updates the lab state in the database based on actual container status.
    """
    lab = get_lab_or_404(lab_id, database, current_user)

    # Try to get status from agent
    agent = await agent_client.get_healthy_agent(database)
    if not agent:
        return {
            "lab_id": lab_id,
            "state": lab.state,
            "nodes": [],
            "error": "No healthy agent available",
        }

    try:
        result = await agent_client.get_lab_status_from_agent(agent, lab.id)
        nodes = result.get("nodes", [])

        # Determine lab state from node states
        if not nodes:
            new_state = "stopped"
        elif all(n.get("status") == "running" for n in nodes):
            new_state = "running"
        elif any(n.get("status") == "running" for n in nodes):
            new_state = "running"
        else:
            new_state = "stopped"

        # Update lab if state changed
        if lab.state != new_state:
            lab.state = new_state
            lab.state_updated_at = datetime.utcnow()
            lab.agent_id = agent.id
            database.commit()

        return {
            "lab_id": lab_id,
            "state": new_state,
            "nodes": nodes,
            "agent_id": agent.id,
        }

    except Exception as e:
        return {
            "lab_id": lab_id,
            "state": lab.state,
            "nodes": [],
            "error": str(e),
        }


@app.post("/images/library/{image_id}")
def update_image_library(
    image_id: str,
    payload: dict,
    current_user: models.User = Depends(get_current_user),
) -> dict[str, object]:
    manifest = load_manifest()
    device_id = payload.get("device_id")
    version = payload.get("version")
    updated = None
    for item in manifest.get("images", []):
        if item.get("id") == image_id:
            item["device_id"] = device_id
            if version is not None:
                item["version"] = version
            updated = item
            break
    if not updated:
        raise HTTPException(status_code=404, detail="Image not found")
    save_manifest(manifest)
    return {"image": updated}
