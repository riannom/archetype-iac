"""Lab CRUD and topology management endpoints."""
from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from typing import Literal

import yaml
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import agent_client, db, models, schemas
from app.auth import get_current_user
from app.services.topology import TopologyService
from app.storage import (
    delete_layout,
    ensure_topology_file,
    lab_workspace,
    layout_path,
    read_layout,
    topology_path,
    write_layout,
)
from app.tasks.jobs import run_agent_job, run_multihost_destroy
from app.topology import analyze_topology, graph_to_yaml, yaml_to_graph
from app.utils.lab import get_lab_or_404, get_lab_provider

logger = logging.getLogger(__name__)

router = APIRouter(tags=["labs"])


def _enrich_node_state(state: models.NodeState) -> schemas.NodeStateOut:
    """Convert a NodeState model to schema with all_ips parsed from JSON."""
    node_data = schemas.NodeStateOut.model_validate(state)
    if state.management_ips_json:
        try:
            node_data.all_ips = json.loads(state.management_ips_json)
        except (json.JSONDecodeError, TypeError):
            node_data.all_ips = []
    return node_data


def _upsert_node_states(
    database: Session,
    lab_id: str,
    graph: schemas.TopologyGraph,
) -> None:
    """Create or update NodeState records for all nodes in a topology graph.

    New nodes are initialized with desired_state='stopped', actual_state='undeployed'.
    IMPORTANT: For existing nodes, node_name is NOT updated to preserve container identity.
    This allows display names to change in the UI without breaking container operations.
    Nodes removed from topology have their NodeState records deleted.
    """
    # Get current node IDs from graph
    current_node_ids = {node.id for node in graph.nodes}

    # Get existing node states for this lab
    existing_states = (
        database.query(models.NodeState)
        .filter(models.NodeState.lab_id == lab_id)
        .all()
    )
    existing_by_node_id = {ns.node_id: ns for ns in existing_states}

    # Update or create node states
    for node in graph.nodes:
        # Use container_name (YAML key) for containerlab operations, fall back to name
        clab_name = node.container_name or node.name
        if node.id in existing_by_node_id:
            existing_state = existing_by_node_id[node.id]
            # Fix node_name if it was set as a placeholder from lazy initialization.
            # Lazy init sets node_name=node_id as a temporary value until topology syncs.
            # Once a node is deployed (has a real container_name), we must NOT change it
            # because that would break console/operations for existing containers.
            if existing_state.node_name == node.id and existing_state.node_name != clab_name:
                # This was a placeholder - safe to correct it
                existing_state.node_name = clab_name
            # If node_name != node_id, it was already set correctly or deployed - don't touch
        else:
            # Create new with defaults - node_name is set only once at creation
            new_state = models.NodeState(
                lab_id=lab_id,
                node_id=node.id,
                node_name=clab_name,
                desired_state="stopped",
                actual_state="undeployed",
            )
            database.add(new_state)

    # Delete node states for nodes no longer in topology
    for existing_node_id, existing_state in existing_by_node_id.items():
        if existing_node_id not in current_node_ids:
            database.delete(existing_state)


def _ensure_node_states_exist(
    database: Session,
    lab_id: str,
) -> None:
    """Ensure NodeState records exist for all nodes in the topology.

    Reads topology file and calls _upsert_node_states if topology exists.
    Safe to call multiple times - idempotent operation.
    """
    topo_path = topology_path(lab_id)
    if topo_path.exists():
        graph = yaml_to_graph(topo_path.read_text(encoding="utf-8"))
        _upsert_node_states(database, lab_id, graph)
        database.commit()


@router.get("/labs")
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


@router.post("/labs")
def create_lab(
    payload: schemas.LabCreate,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.LabOut:
    lab = models.Lab(name=payload.name, owner_id=current_user.id, provider=payload.provider)
    database.add(lab)
    database.flush()
    workspace = lab_workspace(lab.id)
    workspace.mkdir(parents=True, exist_ok=True)
    lab.workspace_path = str(workspace)
    ensure_topology_file(lab.id)
    database.commit()
    database.refresh(lab)
    return schemas.LabOut.model_validate(lab)


@router.get("/labs/{lab_id}")
def get_lab(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.LabOut:
    lab = get_lab_or_404(lab_id, database, current_user)
    return schemas.LabOut.model_validate(lab)


@router.put("/labs/{lab_id}")
def update_lab(
    lab_id: str,
    payload: schemas.LabUpdate,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.LabOut:
    lab = get_lab_or_404(lab_id, database, current_user)
    if lab.owner_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Access denied")
    if payload.name is not None:
        lab.name = payload.name
    database.commit()
    database.refresh(lab)
    return schemas.LabOut.model_validate(lab)


@router.delete("/labs/{lab_id}")
async def delete_lab(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, str]:
    lab = get_lab_or_404(lab_id, database, current_user)
    if lab.owner_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Access denied")

    # If lab has running infrastructure, destroy it first
    if lab.state in ("running", "starting", "stopping"):
        logger.info(f"Lab {lab_id} has state '{lab.state}', destroying infrastructure before deletion")

        # Check for multi-host deployment (database first, then YAML fallback)
        service = TopologyService(database)
        is_multihost = False
        topology_yaml = ""

        if service.has_nodes(lab.id):
            # Use database for analysis
            is_multihost = service.is_multihost(lab.id)
            topology_yaml = service.export_to_yaml(lab.id)
        else:
            # Fall back to YAML file for unmigrated labs
            topo_path = topology_path(lab.id)
            topology_yaml = topo_path.read_text(encoding="utf-8") if topo_path.exists() else ""
            if topology_yaml:
                try:
                    graph = yaml_to_graph(topology_yaml)
                    analysis = analyze_topology(graph)
                    is_multihost = not analysis.single_host
                except Exception as e:
                    logger.warning(f"Failed to analyze topology for lab {lab_id}: {e}")

        # Get the provider for this lab
        lab_provider = get_lab_provider(lab)

        # Create a job record for the destroy operation
        destroy_job = models.Job(
            lab_id=lab.id,
            user_id=current_user.id,
            action="down",
            status="queued",
        )
        database.add(destroy_job)
        database.commit()
        database.refresh(destroy_job)

        # Run destroy and wait for completion
        try:
            if is_multihost:
                await run_multihost_destroy(
                    destroy_job.id, lab.id, topology_yaml, provider=lab_provider
                )
            else:
                # Check for healthy agent
                agent = await agent_client.get_agent_for_lab(database, lab, required_provider=lab_provider)
                if agent:
                    await run_agent_job(
                        destroy_job.id, lab.id, "down", provider=lab_provider
                    )
                else:
                    logger.warning(f"No healthy agent available to destroy lab {lab_id}, proceeding with deletion")
        except Exception as e:
            logger.error(f"Failed to destroy lab {lab_id} infrastructure: {e}")
            # Continue with deletion even if destroy fails - containers may need manual cleanup

    # Delete related records first to avoid foreign key violations
    # Delete links before nodes due to FK constraints
    database.query(models.Link).filter(models.Link.lab_id == lab_id).delete()
    database.query(models.Node).filter(models.Node.lab_id == lab_id).delete()
    database.query(models.Job).filter(models.Job.lab_id == lab_id).delete()
    database.query(models.Permission).filter(models.Permission.lab_id == lab_id).delete()
    database.query(models.LabFile).filter(models.LabFile.lab_id == lab_id).delete()
    database.query(models.NodePlacement).filter(models.NodePlacement.lab_id == lab_id).delete()
    database.query(models.NodeState).filter(models.NodeState.lab_id == lab_id).delete()
    database.query(models.LinkState).filter(models.LinkState.lab_id == lab_id).delete()
    database.query(models.ConfigSnapshot).filter(models.ConfigSnapshot.lab_id == lab_id).delete()

    # Delete workspace files
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


@router.post("/labs/{lab_id}/clone")
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


@router.post("/labs/{lab_id}/import-yaml")
def import_yaml(
    lab_id: str,
    payload: schemas.LabYamlIn,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.LabOut:
    lab = get_lab_or_404(lab_id, database, current_user)
    workspace = lab_workspace(lab.id)
    workspace.mkdir(parents=True, exist_ok=True)

    # Store topology in database (source of truth)
    service = TopologyService(database)
    service.import_from_yaml(lab.id, payload.content)

    # Also write YAML file for backup/version control
    topology_path(lab.id).write_text(payload.content, encoding="utf-8")

    # Sync NodeState/LinkState records
    graph = yaml_to_graph(payload.content)
    _upsert_node_states(database, lab.id, graph)
    _upsert_link_states(database, lab.id, graph)

    database.commit()
    return schemas.LabOut.model_validate(lab)


@router.get("/labs/{lab_id}/export-yaml")
def export_yaml(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.LabYamlOut:
    lab = get_lab_or_404(lab_id, database, current_user)

    # Try database first (source of truth)
    service = TopologyService(database)
    if service.has_nodes(lab.id):
        return schemas.LabYamlOut(content=service.export_to_yaml(lab.id))

    # Fall back to YAML file for unmigrated labs
    topo_path = topology_path(lab.id)
    if not topo_path.exists():
        raise HTTPException(status_code=404, detail="Topology not found")
    return schemas.LabYamlOut(content=topo_path.read_text(encoding="utf-8"))


@router.post("/labs/{lab_id}/import-graph")
def import_graph(
    lab_id: str,
    payload: schemas.TopologyGraph,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.LabOut:
    lab = get_lab_or_404(lab_id, database, current_user)
    workspace = lab_workspace(lab.id)
    workspace.mkdir(parents=True, exist_ok=True)

    # Store topology in database (source of truth)
    service = TopologyService(database)
    service.import_from_graph(lab.id, payload)

    # Also write YAML file for backup/version control
    yaml_content = graph_to_yaml(payload)
    topology_path(lab.id).write_text(yaml_content, encoding="utf-8")

    # Create/update NodeState records for all nodes in the topology
    _upsert_node_states(database, lab.id, payload)

    # Create/update LinkState records for all links in the topology
    _upsert_link_states(database, lab.id, payload)

    database.commit()

    return schemas.LabOut.model_validate(lab)


class TopologyGraphWithLayout(schemas.TopologyGraph):
    """Topology graph with optional layout data."""

    layout: schemas.LabLayout | None = None


@router.get("/labs/{lab_id}/export-graph")
def export_graph(
    lab_id: str,
    include_layout: bool = False,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.TopologyGraph | TopologyGraphWithLayout:
    lab = get_lab_or_404(lab_id, database, current_user)

    # Try database first (source of truth)
    service = TopologyService(database)
    if service.has_nodes(lab.id):
        graph = service.export_to_graph(lab.id)
    else:
        # Fall back to YAML file for unmigrated labs
        topo_path = topology_path(lab.id)
        if not topo_path.exists():
            raise HTTPException(status_code=404, detail="Topology not found")
        graph = yaml_to_graph(topo_path.read_text(encoding="utf-8"))

    if include_layout:
        layout = read_layout(lab.id)
        return TopologyGraphWithLayout(**graph.model_dump(), layout=layout)
    return graph


@router.get("/labs/{lab_id}/layout")
def get_layout(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.LabLayout:
    """Get layout data for a lab, or 404 if no layout exists."""
    lab = get_lab_or_404(lab_id, database, current_user)
    layout = read_layout(lab.id)
    if layout is None:
        raise HTTPException(status_code=404, detail="Layout not found")
    return layout


@router.put("/labs/{lab_id}/layout")
def save_layout(
    lab_id: str,
    payload: schemas.LabLayout,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.LabLayout:
    """Save or update layout data for a lab."""
    lab = get_lab_or_404(lab_id, database, current_user)
    write_layout(lab.id, payload)
    return payload


@router.delete("/labs/{lab_id}/layout")
def remove_layout(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, str]:
    """Delete layout data, reverting to auto-layout on next load."""
    lab = get_lab_or_404(lab_id, database, current_user)
    deleted = delete_layout(lab.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Layout not found")
    return {"status": "deleted"}


# ============================================================================
# Node State Management Endpoints
# ============================================================================


@router.get("/labs/{lab_id}/nodes/states")
async def list_node_states(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.NodeStatesResponse:
    """Get all node states for a lab.

    Returns the desired and actual state for each node in the topology.
    Auto-creates missing NodeState records for labs with existing topologies.
    Auto-refreshes stale pending states if no active jobs are running.
    """
    from app import agent_client
    from app.utils.lab import get_lab_provider

    lab = get_lab_or_404(lab_id, database, current_user)

    # Check if topology exists and sync NodeState records
    topo_path = topology_path(lab.id)
    if topo_path.exists():
        graph = yaml_to_graph(topo_path.read_text(encoding="utf-8"))
        _upsert_node_states(database, lab.id, graph)
        database.commit()

    states = (
        database.query(models.NodeState)
        .filter(models.NodeState.lab_id == lab_id)
        .order_by(models.NodeState.node_name)
        .all()
    )

    # Auto-fix stale pending states: if any node is "pending" but no active job exists,
    # refresh from actual container status
    has_pending = any(s.actual_state == "pending" for s in states)
    if has_pending:
        active_job = (
            database.query(models.Job)
            .filter(
                models.Job.lab_id == lab_id,
                models.Job.status.in_(["pending", "running"]),
            )
            .first()
        )
        if not active_job:
            # No active job but states are pending - refresh from container status
            try:
                lab_provider = get_lab_provider(lab)
                agent = await agent_client.get_agent_for_lab(
                    database, lab, required_provider=lab_provider
                )
                if agent:
                    result = await agent_client.get_lab_status_from_agent(agent, lab.id)
                    nodes = result.get("nodes", [])
                    container_status_map = {
                        n.get("name", ""): n.get("status", "unknown") for n in nodes
                    }
                    for ns in states:
                        if ns.actual_state == "pending":
                            container_status = container_status_map.get(ns.node_name)
                            if container_status == "running":
                                ns.actual_state = "running"
                                ns.error_message = None
                                if not ns.boot_started_at:
                                    ns.boot_started_at = datetime.now(timezone.utc)
                            elif container_status in ("stopped", "exited"):
                                ns.actual_state = "stopped"
                                ns.error_message = None
                                ns.boot_started_at = None
                            elif not container_status:
                                # Container doesn't exist - mark as undeployed
                                ns.actual_state = "undeployed"
                                ns.error_message = None
                    database.commit()
            except Exception:
                pass  # Best effort - don't fail the request if refresh fails

    # Enrich states with host information
    # 1. Query NodePlacement records for node -> host mapping
    placements = (
        database.query(models.NodePlacement)
        .filter(models.NodePlacement.lab_id == lab_id)
        .all()
    )
    placement_by_node = {p.node_name: p.host_id for p in placements}

    # 2. Get all relevant host IDs (from placements or lab's default agent)
    host_ids = set(placement_by_node.values())
    if lab.agent_id:
        host_ids.add(lab.agent_id)

    # 3. Query host names
    hosts = {}
    if host_ids:
        host_records = (
            database.query(models.Host)
            .filter(models.Host.id.in_(host_ids))
            .all()
        )
        hosts = {h.id: h.name for h in host_records}

    # 4. Build enriched response
    enriched_nodes = []
    for s in states:
        node_data = _enrich_node_state(s)
        # Try placement first, then fall back to lab's agent
        host_id = placement_by_node.get(s.node_name) or lab.agent_id
        if host_id:
            node_data.host_id = host_id
            node_data.host_name = hosts.get(host_id)
        enriched_nodes.append(node_data)

    return schemas.NodeStatesResponse(nodes=enriched_nodes)


@router.get("/labs/{lab_id}/nodes/{node_id}/state")
def get_node_state(
    lab_id: str,
    node_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.NodeStateOut:
    """Get the state for a specific node."""
    lab = get_lab_or_404(lab_id, database, current_user)
    _ensure_node_states_exist(database, lab.id)
    state = (
        database.query(models.NodeState)
        .filter(
            models.NodeState.lab_id == lab_id,
            models.NodeState.node_id == node_id,
        )
        .first()
    )
    if not state:
        # Lazy initialization: create NodeState if it doesn't exist yet
        state = models.NodeState(
            lab_id=lab_id,
            node_id=node_id,
            node_name=node_id,  # Placeholder - will be updated when topology syncs
            desired_state="stopped",
            actual_state="undeployed",
        )
        database.add(state)
        database.commit()
        database.refresh(state)
    return _enrich_node_state(state)


@router.put("/labs/{lab_id}/nodes/{node_id}/desired-state")
def set_node_desired_state(
    lab_id: str,
    node_id: str,
    payload: schemas.NodeStateUpdate,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.NodeStateOut:
    """Set the desired state for a node (running or stopped).

    This only updates the desired state - use /sync to actually apply the change.
    """
    lab = get_lab_or_404(lab_id, database, current_user)
    _ensure_node_states_exist(database, lab.id)
    state = (
        database.query(models.NodeState)
        .filter(
            models.NodeState.lab_id == lab_id,
            models.NodeState.node_id == node_id,
        )
        .first()
    )
    if not state:
        # Lazy initialization: create NodeState if it doesn't exist yet
        # This handles race conditions where UI sends request before topology is saved
        # The node_name will be corrected when _ensure_node_states_exist runs after topology save
        state = models.NodeState(
            lab_id=lab_id,
            node_id=node_id,
            node_name=node_id,  # Placeholder - will be updated when topology syncs
            desired_state=payload.state,
            actual_state="undeployed",
        )
        database.add(state)
        database.commit()
        database.refresh(state)
        return _enrich_node_state(state)

    state.desired_state = payload.state
    database.commit()
    database.refresh(state)
    return _enrich_node_state(state)


@router.put("/labs/{lab_id}/nodes/desired-state")
def set_all_nodes_desired_state(
    lab_id: str,
    payload: schemas.NodeStateUpdate,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.NodeStatesResponse:
    """Set the desired state for all nodes in a lab.

    Useful for "Start All" or "Stop All" operations.
    """
    lab = get_lab_or_404(lab_id, database, current_user)
    _ensure_node_states_exist(database, lab.id)
    states = (
        database.query(models.NodeState)
        .filter(models.NodeState.lab_id == lab_id)
        .all()
    )
    for state in states:
        state.desired_state = payload.state
    database.commit()

    # Refresh and return all states
    states = (
        database.query(models.NodeState)
        .filter(models.NodeState.lab_id == lab_id)
        .order_by(models.NodeState.node_name)
        .all()
    )
    return schemas.NodeStatesResponse(
        nodes=[_enrich_node_state(s) for s in states]
    )


@router.post("/labs/{lab_id}/nodes/refresh")
async def refresh_node_states(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.NodeStatesResponse:
    """Refresh node states from actual container status.

    Queries the agent for real container status and updates the NodeState
    records to match. Use this when states appear out of sync with reality.
    """
    from app import agent_client
    from app.utils.lab import get_lab_provider

    lab = get_lab_or_404(lab_id, database, current_user)
    _ensure_node_states_exist(database, lab.id)

    # Get agent for this lab
    lab_provider = get_lab_provider(lab)
    agent = await agent_client.get_agent_for_lab(database, lab, required_provider=lab_provider)
    if not agent:
        raise HTTPException(status_code=503, detail="No healthy agent available")

    try:
        result = await agent_client.get_lab_status_from_agent(agent, lab.id)
        nodes = result.get("nodes", [])

        # Build a map of container status by node name
        container_status_map = {n.get("name", ""): n.get("status", "unknown") for n in nodes}

        # Update NodeState records based on actual container status
        node_states = (
            database.query(models.NodeState)
            .filter(models.NodeState.lab_id == lab_id)
            .all()
        )

        for ns in node_states:
            container_status = container_status_map.get(ns.node_name)
            if container_status:
                if container_status == "running":
                    ns.actual_state = "running"
                    ns.error_message = None
                    if not ns.boot_started_at:
                        ns.boot_started_at = datetime.now(timezone.utc)
                elif container_status in ("stopped", "exited"):
                    ns.actual_state = "stopped"
                    ns.error_message = None
                    ns.boot_started_at = None

        database.commit()

    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Failed to refresh from agent: {e}")

    # Return updated states
    states = (
        database.query(models.NodeState)
        .filter(models.NodeState.lab_id == lab_id)
        .order_by(models.NodeState.node_name)
        .all()
    )
    return schemas.NodeStatesResponse(
        nodes=[_enrich_node_state(s) for s in states]
    )


def _get_out_of_sync_nodes(
    database: Session,
    lab_id: str,
    node_ids: list[str] | None = None,
) -> list[models.NodeState]:
    """Find nodes where actual_state doesn't match desired_state.

    Args:
        database: Database session
        lab_id: Lab ID to check
        node_ids: Optional list of specific node IDs to check. If None, checks all.

    Returns:
        List of NodeState records that need syncing
    """
    query = database.query(models.NodeState).filter(
        models.NodeState.lab_id == lab_id
    )

    if node_ids:
        query = query.filter(models.NodeState.node_id.in_(node_ids))

    states = query.all()

    # A node is out of sync if:
    # - desired=running and actual not in (running, pending)
    # - desired=stopped and actual not in (stopped, undeployed)
    out_of_sync = []
    for state in states:
        if state.desired_state == "running":
            if state.actual_state not in ("running", "pending"):
                out_of_sync.append(state)
        elif state.desired_state == "stopped":
            if state.actual_state not in ("stopped", "undeployed"):
                out_of_sync.append(state)

    return out_of_sync


@router.post("/labs/{lab_id}/nodes/{node_id}/sync")
async def sync_node(
    lab_id: str,
    node_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.SyncResponse:
    """Trigger a sync job for a single node.

    This will reconcile the node's actual state with its desired state.
    If the node is already in sync, no job is created.
    """
    import asyncio
    from app import agent_client
    from app.tasks.jobs import run_node_sync
    from app.utils.lab import get_lab_provider

    lab = get_lab_or_404(lab_id, database, current_user)
    _ensure_node_states_exist(database, lab.id)

    # Get the node state
    state = (
        database.query(models.NodeState)
        .filter(
            models.NodeState.lab_id == lab_id,
            models.NodeState.node_id == node_id,
        )
        .first()
    )
    if not state:
        # Lazy initialization: create NodeState if it doesn't exist yet
        state = models.NodeState(
            lab_id=lab_id,
            node_id=node_id,
            node_name=node_id,  # Placeholder - will be updated below
            desired_state="running",  # Assume user wants to start it
            actual_state="undeployed",
        )
        database.add(state)
        database.commit()
        # Re-run ensure to get correct node_name from topology
        _ensure_node_states_exist(database, lab.id)
        database.refresh(state)

    # Check if node is out of sync
    out_of_sync = _get_out_of_sync_nodes(database, lab_id, [node_id])
    if not out_of_sync:
        return schemas.SyncResponse(
            job_id="",
            message="Node is already in sync",
            nodes_to_sync=[],
        )

    # Get agent for this lab
    lab_provider = get_lab_provider(lab)
    agent = await agent_client.get_agent_for_lab(database, lab, required_provider=lab_provider)
    if not agent:
        raise HTTPException(status_code=503, detail=f"No healthy agent available with {lab_provider} support")

    # Note: Don't set state to pending here - let the task handle state transitions
    # after it reads the current state to determine what action is needed

    # Create sync job
    job = models.Job(
        lab_id=lab.id,
        user_id=current_user.id,
        action=f"sync:node:{node_id}",
        status="queued",
    )
    database.add(job)
    database.commit()
    database.refresh(job)

    # Start background sync task
    asyncio.create_task(run_node_sync(job.id, lab.id, [node_id], provider=lab_provider))

    return schemas.SyncResponse(
        job_id=job.id,
        message="Sync job queued",
        nodes_to_sync=[node_id],
    )


@router.post("/labs/{lab_id}/sync")
async def sync_lab(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.SyncResponse:
    """Trigger a sync job for all out-of-sync nodes in a lab.

    This will reconcile all nodes' actual states with their desired states.
    If all nodes are already in sync, no job is created.
    """
    import asyncio
    from app import agent_client
    from app.tasks.jobs import run_node_sync
    from app.utils.lab import get_lab_provider

    lab = get_lab_or_404(lab_id, database, current_user)
    _ensure_node_states_exist(database, lab.id)

    # Find all out-of-sync nodes
    out_of_sync = _get_out_of_sync_nodes(database, lab_id)
    if not out_of_sync:
        return schemas.SyncResponse(
            job_id="",
            message="All nodes are already in sync",
            nodes_to_sync=[],
        )

    node_ids = [s.node_id for s in out_of_sync]

    # Get agent for this lab
    lab_provider = get_lab_provider(lab)
    agent = await agent_client.get_agent_for_lab(database, lab, required_provider=lab_provider)
    if not agent:
        raise HTTPException(status_code=503, detail=f"No healthy agent available with {lab_provider} support")

    # Note: Don't set states to pending here - let the task handle state transitions
    # after it reads the current states to determine what actions are needed

    # Create sync job
    job = models.Job(
        lab_id=lab.id,
        user_id=current_user.id,
        action=f"sync:lab:{','.join(node_ids)}",
        status="queued",
    )
    database.add(job)
    database.commit()
    database.refresh(job)

    # Start background sync task
    asyncio.create_task(run_node_sync(job.id, lab.id, node_ids, provider=lab_provider))

    return schemas.SyncResponse(
        job_id=job.id,
        message=f"Sync job queued for {len(node_ids)} node(s)",
        nodes_to_sync=node_ids,
    )


# ============================================================================
# Node Readiness Endpoints (IaC Workflow Support)
# ============================================================================


@router.get("/labs/{lab_id}/nodes/ready")
async def check_nodes_ready(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.LabReadinessResponse:
    """Check readiness status for all nodes in a lab.

    Returns the readiness state of each node, including boot progress
    and management IPs. Useful for CI/CD to poll until lab is ready.

    A node is considered "ready" when:
    - actual_state is "running"
    - is_ready flag is True (boot sequence complete)
    """
    from app.utils.lab import get_lab_provider

    lab = get_lab_or_404(lab_id, database, current_user)
    _ensure_node_states_exist(database, lab.id)

    # Get all node states
    states = (
        database.query(models.NodeState)
        .filter(models.NodeState.lab_id == lab_id)
        .order_by(models.NodeState.node_name)
        .all()
    )

    # Get agent for readiness checks
    lab_provider = get_lab_provider(lab)
    agent = await agent_client.get_agent_for_lab(database, lab, required_provider=lab_provider)

    nodes_out = []
    ready_count = 0
    running_count = 0

    for state in states:
        # Check readiness from agent if node is running
        progress_percent = None
        message = None

        if state.actual_state == "running":
            running_count += 1
            if agent:
                try:
                    readiness = await agent_client.check_node_readiness(
                        agent, lab.id, state.node_name
                    )
                    # Update is_ready from agent response
                    if readiness.get("is_ready") and not state.is_ready:
                        state.is_ready = True
                        database.commit()
                    progress_percent = readiness.get("progress_percent")
                    message = readiness.get("message")
                except Exception as e:
                    message = f"Readiness check failed: {e}"

        if state.is_ready and state.actual_state == "running":
            ready_count += 1

        nodes_out.append(schemas.NodeReadinessOut(
            node_id=state.node_id,
            node_name=state.node_name,
            is_ready=state.is_ready and state.actual_state == "running",
            actual_state=state.actual_state,
            progress_percent=progress_percent,
            message=message,
            boot_started_at=state.boot_started_at,
            management_ip=state.management_ip,
        ))

    return schemas.LabReadinessResponse(
        lab_id=lab_id,
        all_ready=ready_count == len(states) and len(states) > 0,
        ready_count=ready_count,
        total_count=len(states),
        running_count=running_count,
        nodes=nodes_out,
    )


@router.get("/labs/{lab_id}/nodes/ready/poll")
async def poll_nodes_ready(
    lab_id: str,
    timeout: int = 300,
    interval: int = 10,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.LabReadinessResponse:
    """Long-poll until all running nodes are ready or timeout.

    This endpoint blocks until either:
    - All nodes with desired_state=running are ready
    - The timeout is reached

    Args:
        timeout: Maximum seconds to wait (default: 300, max: 600)
        interval: Seconds between checks (default: 10, min: 5)

    Returns:
        LabReadinessResponse with final readiness state

    Response Headers:
        X-Readiness-Status: "complete" if all ready, "timeout" if timed out
    """
    import asyncio
    from fastapi.responses import JSONResponse
    from app.utils.lab import get_lab_provider

    # Validate parameters
    timeout = min(max(timeout, 10), 600)  # 10s to 10min
    interval = min(max(interval, 5), 60)  # 5s to 60s

    lab = get_lab_or_404(lab_id, database, current_user)
    _ensure_node_states_exist(database, lab.id)

    lab_provider = get_lab_provider(lab)
    agent = await agent_client.get_agent_for_lab(database, lab, required_provider=lab_provider)

    start_time = asyncio.get_event_loop().time()
    end_time = start_time + timeout

    while asyncio.get_event_loop().time() < end_time:
        # Refresh session to get latest state
        database.expire_all()

        states = (
            database.query(models.NodeState)
            .filter(models.NodeState.lab_id == lab_id)
            .order_by(models.NodeState.node_name)
            .all()
        )

        # Count nodes that should be running
        nodes_should_run = [s for s in states if s.desired_state == "running"]

        if not nodes_should_run:
            # No nodes expected to run - return immediately
            return schemas.LabReadinessResponse(
                lab_id=lab_id,
                all_ready=True,
                ready_count=0,
                total_count=len(states),
                running_count=0,
                nodes=[],
            )

        # Check readiness for running nodes
        nodes_out = []
        ready_count = 0
        running_count = 0

        for state in states:
            progress_percent = None
            message = None

            if state.actual_state == "running":
                running_count += 1
                if agent and not state.is_ready:
                    try:
                        readiness = await agent_client.check_node_readiness(
                            agent, lab.id, state.node_name
                        )
                        if readiness.get("is_ready"):
                            state.is_ready = True
                            database.commit()
                        progress_percent = readiness.get("progress_percent")
                        message = readiness.get("message")
                    except Exception as e:
                        message = f"Readiness check failed: {e}"

            if state.is_ready and state.actual_state == "running":
                ready_count += 1

            if state in nodes_should_run:
                nodes_out.append(schemas.NodeReadinessOut(
                    node_id=state.node_id,
                    node_name=state.node_name,
                    is_ready=state.is_ready and state.actual_state == "running",
                    actual_state=state.actual_state,
                    progress_percent=progress_percent,
                    message=message,
                    boot_started_at=state.boot_started_at,
                    management_ip=state.management_ip,
                ))

        # Check if all nodes that should run are ready
        all_ready = all(
            s.is_ready and s.actual_state == "running"
            for s in nodes_should_run
        )

        if all_ready:
            response = schemas.LabReadinessResponse(
                lab_id=lab_id,
                all_ready=True,
                ready_count=ready_count,
                total_count=len(states),
                running_count=running_count,
                nodes=nodes_out,
            )
            return JSONResponse(
                content=response.model_dump(mode="json"),
                headers={"X-Readiness-Status": "complete"},
            )

        # Wait before next check
        await asyncio.sleep(interval)

    # Timeout reached - return current state
    states = (
        database.query(models.NodeState)
        .filter(models.NodeState.lab_id == lab_id)
        .order_by(models.NodeState.node_name)
        .all()
    )

    nodes_out = []
    ready_count = 0
    running_count = 0

    for state in states:
        if state.actual_state == "running":
            running_count += 1
        if state.is_ready and state.actual_state == "running":
            ready_count += 1

        nodes_out.append(schemas.NodeReadinessOut(
            node_id=state.node_id,
            node_name=state.node_name,
            is_ready=state.is_ready and state.actual_state == "running",
            actual_state=state.actual_state,
            progress_percent=None,
            message="Timeout waiting for readiness",
            boot_started_at=state.boot_started_at,
            management_ip=state.management_ip,
        ))

    response = schemas.LabReadinessResponse(
        lab_id=lab_id,
        all_ready=False,
        ready_count=ready_count,
        total_count=len(states),
        running_count=running_count,
        nodes=nodes_out,
    )
    return JSONResponse(
        content=response.model_dump(mode="json"),
        headers={"X-Readiness-Status": "timeout"},
    )


# ============================================================================
# Inventory Export Endpoint (IaC Workflow Support)
# ============================================================================


@router.get("/labs/{lab_id}/inventory")
async def export_inventory(
    lab_id: str,
    format: Literal["json", "ansible", "terraform"] = "json",
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.LabInventoryResponse:
    """Export lab node inventory for IaC tools.

    Generates an inventory of all nodes with their management IPs
    in a format suitable for automation tools.

    Formats:
    - json: Structured JSON with all node details
    - ansible: Ansible inventory YAML format
    - terraform: Terraform tfvars JSON format

    Example usage:
        curl -H "Authorization: Bearer $TOKEN" \\
            "$API_URL/labs/{id}/inventory?format=ansible" > inventory.yml
    """
    lab = get_lab_or_404(lab_id, database, current_user)
    _ensure_node_states_exist(database, lab.id)

    # Get node states with IPs
    states = (
        database.query(models.NodeState)
        .filter(models.NodeState.lab_id == lab_id)
        .order_by(models.NodeState.node_name)
        .all()
    )

    # Get topology for device info
    topo_path = topology_path(lab.id)
    device_info = {}
    if topo_path.exists():
        try:
            graph = yaml_to_graph(topo_path.read_text(encoding="utf-8"))
            for node in graph.nodes:
                node_name = node.container_name or node.name
                device_info[node_name] = {
                    "device": node.device,
                    "kind": node.device,  # In clab, device maps to kind
                }
        except Exception:
            pass

    # Get host placements for multi-host
    placements = (
        database.query(models.NodePlacement)
        .filter(models.NodePlacement.lab_id == lab_id)
        .all()
    )
    placement_by_node = {p.node_name: p.host_id for p in placements}

    # Get host names
    host_ids = set(placement_by_node.values())
    if lab.agent_id:
        host_ids.add(lab.agent_id)
    hosts = {}
    if host_ids:
        host_records = (
            database.query(models.Host)
            .filter(models.Host.id.in_(host_ids))
            .all()
        )
        hosts = {h.id: h.name for h in host_records}

    # Build inventory entries
    nodes = []
    for state in states:
        all_ips = []
        if state.management_ips_json:
            try:
                all_ips = json.loads(state.management_ips_json)
            except (json.JSONDecodeError, TypeError):
                pass

        info = device_info.get(state.node_name, {})
        host_id = placement_by_node.get(state.node_name) or lab.agent_id

        nodes.append(schemas.NodeInventoryEntry(
            node_name=state.node_name,
            management_ip=state.management_ip,
            all_ips=all_ips,
            device_type=info.get("device"),
            kind=info.get("kind"),
            host_id=host_id,
            host_name=hosts.get(host_id) if host_id else None,
        ))

    # Generate formatted content based on requested format
    content = None

    if format == "ansible":
        # Ansible inventory YAML format
        ansible_hosts = {}
        for node in nodes:
            host_vars = {}
            if node.management_ip:
                host_vars["ansible_host"] = node.management_ip
            if node.device_type:
                # Map common device types to ansible_network_os
                device_os_map = {
                    "ceos": "arista.eos.eos",
                    "vr-veos": "arista.eos.eos",
                    "srl": "nokia.srlinux.srlinux",
                    "vr-sros": "nokia.sros.sros",
                    "crpd": "juniper.device",
                    "vr-vmx": "juniper.device",
                    "vr-xrv": "cisco.iosxr.iosxr",
                    "vr-csr": "cisco.ios.ios",
                    "vr-n9kv": "cisco.nxos.nxos",
                }
                if node.device_type in device_os_map:
                    host_vars["ansible_network_os"] = device_os_map[node.device_type]
                host_vars["device_type"] = node.device_type
            if node.host_name:
                host_vars["lab_host"] = node.host_name
            ansible_hosts[node.node_name] = host_vars

        inventory = {
            "all": {
                "hosts": ansible_hosts,
                "vars": {
                    "ansible_connection": "network_cli",
                    "lab_id": lab_id,
                    "lab_name": lab.name,
                },
            }
        }
        content = yaml.dump(inventory, default_flow_style=False, sort_keys=False)

    elif format == "terraform":
        # Terraform tfvars JSON format
        tf_nodes = {}
        for node in nodes:
            tf_nodes[node.node_name] = {
                "ip": node.management_ip,
                "all_ips": node.all_ips,
                "device_type": node.device_type,
                "kind": node.kind,
            }
            if node.host_name:
                tf_nodes[node.node_name]["host"] = node.host_name

        terraform_vars = {
            "lab_id": lab_id,
            "lab_name": lab.name,
            "lab_nodes": tf_nodes,
        }
        content = json.dumps(terraform_vars, indent=2)

    return schemas.LabInventoryResponse(
        lab_id=lab_id,
        lab_name=lab.name,
        format=format,
        nodes=nodes,
        content=content,
    )


# ============================================================================
# Link State Management Endpoints
# ============================================================================


def _generate_link_name(
    source_node: str,
    source_interface: str,
    target_node: str,
    target_interface: str,
) -> str:
    """Generate a canonical link name from endpoints.

    Link names are sorted alphabetically to ensure the same link always gets
    the same name regardless of endpoint order.
    """
    ep_a = f"{source_node}:{source_interface}"
    ep_b = f"{target_node}:{target_interface}"
    # Sort endpoints alphabetically for consistent naming
    if ep_a <= ep_b:
        return f"{ep_a}-{ep_b}"
    return f"{ep_b}-{ep_a}"


def _upsert_link_states(
    database: Session,
    lab_id: str,
    graph: schemas.TopologyGraph,
) -> tuple[int, int]:
    """Create or update LinkState records for all links in a topology graph.

    New links are initialized with desired_state='up', actual_state='unknown'.
    Existing links retain their desired_state (user preference persists).
    Links removed from topology have their LinkState records deleted.

    Returns:
        Tuple of (created_count, updated_count)
    """
    # Get existing link states for this lab
    existing_states = (
        database.query(models.LinkState)
        .filter(models.LinkState.lab_id == lab_id)
        .all()
    )
    existing_by_name = {ls.link_name: ls for ls in existing_states}

    # Build node ID to name mapping for resolving link endpoints
    # Node endpoints in links reference node IDs, not names
    node_id_to_name: dict[str, str] = {}
    for node in graph.nodes:
        # Use container_name (YAML key) for consistency with containerlab
        node_id_to_name[node.id] = node.container_name or node.name

    # Track which links are in the current topology
    current_link_names: set[str] = set()
    created_count = 0
    updated_count = 0

    for link in graph.links:
        if len(link.endpoints) != 2:
            continue  # Skip non-point-to-point links

        ep_a, ep_b = link.endpoints

        # Skip external endpoints (bridge, macvlan, host)
        if ep_a.type != "node" or ep_b.type != "node":
            continue

        # Resolve node IDs to names
        source_node = node_id_to_name.get(ep_a.node, ep_a.node)
        target_node = node_id_to_name.get(ep_b.node, ep_b.node)
        source_interface = ep_a.ifname or "eth0"
        target_interface = ep_b.ifname or "eth0"

        # Generate canonical link name
        link_name = _generate_link_name(
            source_node, source_interface, target_node, target_interface
        )
        current_link_names.add(link_name)

        if link_name in existing_by_name:
            # Update existing link state (source/target may have changed order)
            ls = existing_by_name[link_name]
            # Ensure canonical ordering is preserved
            if f"{source_node}:{source_interface}" <= f"{target_node}:{target_interface}":
                ls.source_node = source_node
                ls.source_interface = source_interface
                ls.target_node = target_node
                ls.target_interface = target_interface
            else:
                ls.source_node = target_node
                ls.source_interface = target_interface
                ls.target_node = source_node
                ls.target_interface = source_interface
            updated_count += 1
        else:
            # Create new link state
            # Ensure canonical ordering
            if f"{source_node}:{source_interface}" <= f"{target_node}:{target_interface}":
                src_n, src_i = source_node, source_interface
                tgt_n, tgt_i = target_node, target_interface
            else:
                src_n, src_i = target_node, target_interface
                tgt_n, tgt_i = source_node, source_interface

            new_state = models.LinkState(
                lab_id=lab_id,
                link_name=link_name,
                source_node=src_n,
                source_interface=src_i,
                target_node=tgt_n,
                target_interface=tgt_i,
                desired_state="up",
                actual_state="unknown",
            )
            database.add(new_state)
            created_count += 1

    # Delete link states for links no longer in topology
    for existing_name, existing_state in existing_by_name.items():
        if existing_name not in current_link_names:
            database.delete(existing_state)

    return created_count, updated_count


def _ensure_link_states_exist(
    database: Session,
    lab_id: str,
) -> None:
    """Ensure LinkState records exist for all links in the topology.

    Reads topology file and calls _upsert_link_states if topology exists.
    Safe to call multiple times - idempotent operation.
    """
    topo_path = topology_path(lab_id)
    if topo_path.exists():
        graph = yaml_to_graph(topo_path.read_text(encoding="utf-8"))
        _upsert_link_states(database, lab_id, graph)
        database.commit()


@router.get("/labs/{lab_id}/links/states")
def list_link_states(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.LinkStatesResponse:
    """Get all link states for a lab.

    Returns the desired and actual state for each link in the topology.
    Auto-creates missing LinkState records for labs with existing topologies.
    """
    lab = get_lab_or_404(lab_id, database, current_user)

    # Check if topology exists and sync LinkState records
    topo_path = topology_path(lab.id)
    if topo_path.exists():
        graph = yaml_to_graph(topo_path.read_text(encoding="utf-8"))
        _upsert_link_states(database, lab.id, graph)
        database.commit()

    states = (
        database.query(models.LinkState)
        .filter(models.LinkState.lab_id == lab_id)
        .order_by(models.LinkState.link_name)
        .all()
    )

    return schemas.LinkStatesResponse(
        links=[schemas.LinkStateOut.model_validate(s) for s in states]
    )


@router.get("/labs/{lab_id}/links/{link_name}/state")
def get_link_state(
    lab_id: str,
    link_name: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.LinkStateOut:
    """Get the state for a specific link."""
    lab = get_lab_or_404(lab_id, database, current_user)
    _ensure_link_states_exist(database, lab.id)

    state = (
        database.query(models.LinkState)
        .filter(
            models.LinkState.lab_id == lab_id,
            models.LinkState.link_name == link_name,
        )
        .first()
    )
    if not state:
        raise HTTPException(status_code=404, detail=f"Link '{link_name}' not found")

    return schemas.LinkStateOut.model_validate(state)


@router.put("/labs/{lab_id}/links/{link_name}/state")
def set_link_state(
    lab_id: str,
    link_name: str,
    payload: schemas.LinkStateUpdate,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.LinkStateOut:
    """Set the desired state for a link (up or down).

    This updates the desired state in the database. The actual state
    will be reconciled by the reconciliation system or can be triggered
    by a manual sync operation.
    """
    lab = get_lab_or_404(lab_id, database, current_user)
    _ensure_link_states_exist(database, lab.id)

    state = (
        database.query(models.LinkState)
        .filter(
            models.LinkState.lab_id == lab_id,
            models.LinkState.link_name == link_name,
        )
        .first()
    )
    if not state:
        raise HTTPException(status_code=404, detail=f"Link '{link_name}' not found")

    state.desired_state = payload.state
    database.commit()
    database.refresh(state)

    return schemas.LinkStateOut.model_validate(state)


@router.put("/labs/{lab_id}/links/desired-state")
def set_all_links_desired_state(
    lab_id: str,
    payload: schemas.LinkStateUpdate,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.LinkStatesResponse:
    """Set the desired state for all links in a lab.

    Useful for "Enable All Links" or "Disable All Links" operations.
    """
    lab = get_lab_or_404(lab_id, database, current_user)
    _ensure_link_states_exist(database, lab.id)

    states = (
        database.query(models.LinkState)
        .filter(models.LinkState.lab_id == lab_id)
        .all()
    )
    for state in states:
        state.desired_state = payload.state
    database.commit()

    # Refresh and return all states
    states = (
        database.query(models.LinkState)
        .filter(models.LinkState.lab_id == lab_id)
        .order_by(models.LinkState.link_name)
        .all()
    )
    return schemas.LinkStatesResponse(
        links=[schemas.LinkStateOut.model_validate(s) for s in states]
    )


@router.post("/labs/{lab_id}/links/sync")
def sync_link_states(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.LinkStateSyncResponse:
    """Sync link states from the current topology.

    This refreshes the LinkState records to match the current topology file.
    New links are created, removed links are deleted.
    """
    lab = get_lab_or_404(lab_id, database, current_user)

    topo_path = topology_path(lab.id)
    if not topo_path.exists():
        raise HTTPException(status_code=404, detail="Topology not found")

    graph = yaml_to_graph(topo_path.read_text(encoding="utf-8"))
    created, updated = _upsert_link_states(database, lab.id, graph)
    database.commit()

    return schemas.LinkStateSyncResponse(
        message=f"Link states synchronized",
        links_created=created,
        links_updated=updated,
    )


# ============================================================================
# Config Extraction Endpoint
# ============================================================================


@router.post("/labs/{lab_id}/extract-configs")
async def extract_configs(
    lab_id: str,
    create_snapshot: bool = True,
    snapshot_type: str = "manual",
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """Extract running configs from all cEOS nodes in a lab.

    This endpoint manually triggers config extraction from all running cEOS
    containers in the lab. The configs are received from the agent and saved
    to the API's workspace for persistence.

    Args:
        create_snapshot: If True, creates config snapshots after extraction
        snapshot_type: Type of snapshot to create ("manual" or "auto_stop")

    Returns:
        Dict with 'success', 'extracted_count', 'snapshots_created', and optionally 'error' keys
    """
    lab = get_lab_or_404(lab_id, database, current_user)

    # Get agent for this lab
    lab_provider = get_lab_provider(lab)
    agent = await agent_client.get_agent_for_lab(database, lab, required_provider=lab_provider)
    if not agent:
        raise HTTPException(status_code=503, detail="No healthy agent available")

    # Call agent to extract configs
    result = await agent_client.extract_configs_on_agent(agent, lab.id)

    if not result.get("success"):
        raise HTTPException(
            status_code=500,
            detail=f"Config extraction failed: {result.get('error', 'Unknown error')}"
        )

    extracted_count = result.get("extracted_count", 0)
    configs = result.get("configs", [])
    snapshots_created = 0

    # Save configs to API workspace and create snapshots
    if configs:
        workspace = lab_workspace(lab.id)
        configs_dir = workspace / "configs"
        configs_dir.mkdir(parents=True, exist_ok=True)

        for config_data in configs:
            node_name = config_data.get("node_name")
            content = config_data.get("content")
            if not node_name or not content:
                continue

            # Save config to workspace
            node_config_dir = configs_dir / node_name
            node_config_dir.mkdir(parents=True, exist_ok=True)
            config_file = node_config_dir / "startup-config"
            config_file.write_text(content, encoding="utf-8")

            # Create snapshot if requested
            if create_snapshot:
                content_hash = _compute_content_hash(content)

                # Check for duplicate
                latest_snapshot = (
                    database.query(models.ConfigSnapshot)
                    .filter(
                        models.ConfigSnapshot.lab_id == lab_id,
                        models.ConfigSnapshot.node_name == node_name,
                    )
                    .order_by(models.ConfigSnapshot.created_at.desc())
                    .first()
                )

                if latest_snapshot and latest_snapshot.content_hash == content_hash:
                    continue

                # Create snapshot
                snapshot = models.ConfigSnapshot(
                    lab_id=lab_id,
                    node_name=node_name,
                    content=content,
                    content_hash=content_hash,
                    snapshot_type=snapshot_type,
                )
                database.add(snapshot)
                snapshots_created += 1

            database.commit()

    return {
        "success": True,
        "extracted_count": extracted_count,
        "snapshots_created": snapshots_created,
        "message": f"Extracted {extracted_count} cEOS configs, created {snapshots_created} snapshot(s)",
    }


# ============================================================================
# Saved Config Retrieval Endpoints
# ============================================================================


@router.get("/labs/{lab_id}/configs")
def get_all_configs(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """Get all saved startup configs for a lab.

    Returns a list of configs saved in the workspace/configs/ directory.
    Each config includes the node name, config content, and last modified time.
    """
    lab = get_lab_or_404(lab_id, database, current_user)
    workspace = lab_workspace(lab.id)
    configs_dir = workspace / "configs"

    configs = []
    if configs_dir.exists():
        for node_dir in configs_dir.iterdir():
            if not node_dir.is_dir():
                continue
            config_file = node_dir / "startup-config"
            if config_file.exists():
                stat = config_file.stat()
                configs.append({
                    "node_name": node_dir.name,
                    "config": config_file.read_text(encoding="utf-8"),
                    "last_modified": stat.st_mtime,
                    "exists": True,
                })

    return {"configs": configs}


@router.get("/labs/{lab_id}/configs/{node_name}")
def get_node_config(
    lab_id: str,
    node_name: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """Get saved startup config for a specific node.

    Returns the config content if it exists, or 404 if not found.
    """
    lab = get_lab_or_404(lab_id, database, current_user)
    workspace = lab_workspace(lab.id)
    config_file = workspace / "configs" / node_name / "startup-config"

    if not config_file.exists():
        raise HTTPException(
            status_code=404,
            detail=f"No saved config found for node '{node_name}'"
        )

    stat = config_file.stat()
    return {
        "node_name": node_name,
        "config": config_file.read_text(encoding="utf-8"),
        "last_modified": stat.st_mtime,
        "exists": True,
    }


# ============================================================================
# Config Snapshot Endpoints
# ============================================================================


def _compute_content_hash(content: str) -> str:
    """Compute SHA256 hash of config content for deduplication."""
    import hashlib
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@router.get("/labs/{lab_id}/config-snapshots")
def list_config_snapshots(
    lab_id: str,
    node_name: str | None = None,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.ConfigSnapshotsResponse:
    """List all config snapshots for a lab.

    Optionally filter by node_name query parameter.
    Returns snapshots ordered by created_at descending (newest first).
    """
    lab = get_lab_or_404(lab_id, database, current_user)

    query = (
        database.query(models.ConfigSnapshot)
        .filter(models.ConfigSnapshot.lab_id == lab_id)
    )

    if node_name:
        query = query.filter(models.ConfigSnapshot.node_name == node_name)

    snapshots = query.order_by(models.ConfigSnapshot.created_at.desc()).all()

    return schemas.ConfigSnapshotsResponse(
        snapshots=[schemas.ConfigSnapshotOut.model_validate(s) for s in snapshots]
    )


@router.get("/labs/{lab_id}/config-snapshots/{node_name}/list")
def list_node_config_snapshots(
    lab_id: str,
    node_name: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.ConfigSnapshotsResponse:
    """List all config snapshots for a specific node.

    Returns snapshots ordered by created_at descending (newest first).
    """
    lab = get_lab_or_404(lab_id, database, current_user)

    snapshots = (
        database.query(models.ConfigSnapshot)
        .filter(
            models.ConfigSnapshot.lab_id == lab_id,
            models.ConfigSnapshot.node_name == node_name,
        )
        .order_by(models.ConfigSnapshot.created_at.desc())
        .all()
    )

    return schemas.ConfigSnapshotsResponse(
        snapshots=[schemas.ConfigSnapshotOut.model_validate(s) for s in snapshots]
    )


@router.post("/labs/{lab_id}/config-snapshots")
async def create_config_snapshot(
    lab_id: str,
    payload: schemas.ConfigSnapshotCreate | None = None,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.ConfigSnapshotsResponse:
    """Create config snapshots from current saved configs.

    If node_name is provided, creates a snapshot for that node only.
    Otherwise, creates snapshots for all nodes with saved configs.

    Snapshots are deduplicated by content hash - if the content hasn't
    changed since the last snapshot, a new one won't be created.
    """
    lab = get_lab_or_404(lab_id, database, current_user)
    workspace = lab_workspace(lab.id)
    configs_dir = workspace / "configs"

    if not configs_dir.exists():
        raise HTTPException(
            status_code=404,
            detail="No saved configs found. Run 'Extract Configs' first."
        )

    created_snapshots = []
    node_name = payload.node_name if payload else None

    # Determine which nodes to snapshot
    if node_name:
        node_dirs = [configs_dir / node_name]
        if not node_dirs[0].exists():
            raise HTTPException(
                status_code=404,
                detail=f"No saved config found for node '{node_name}'"
            )
    else:
        node_dirs = [d for d in configs_dir.iterdir() if d.is_dir()]

    for node_dir in node_dirs:
        config_file = node_dir / "startup-config"
        if not config_file.exists():
            continue

        content = config_file.read_text(encoding="utf-8")
        content_hash = _compute_content_hash(content)
        current_node_name = node_dir.name

        # Check for duplicate - skip if content hash matches most recent snapshot
        latest_snapshot = (
            database.query(models.ConfigSnapshot)
            .filter(
                models.ConfigSnapshot.lab_id == lab_id,
                models.ConfigSnapshot.node_name == current_node_name,
            )
            .order_by(models.ConfigSnapshot.created_at.desc())
            .first()
        )

        if latest_snapshot and latest_snapshot.content_hash == content_hash:
            # Content unchanged, skip creating duplicate
            continue

        # Create new snapshot
        snapshot = models.ConfigSnapshot(
            lab_id=lab_id,
            node_name=current_node_name,
            content=content,
            content_hash=content_hash,
            snapshot_type="manual",
        )
        database.add(snapshot)
        created_snapshots.append(snapshot)

    database.commit()

    # Refresh to get database-generated fields
    for snapshot in created_snapshots:
        database.refresh(snapshot)

    return schemas.ConfigSnapshotsResponse(
        snapshots=[schemas.ConfigSnapshotOut.model_validate(s) for s in created_snapshots]
    )


@router.delete("/labs/{lab_id}/config-snapshots/{snapshot_id}")
def delete_config_snapshot(
    lab_id: str,
    snapshot_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """Delete a specific config snapshot."""
    lab = get_lab_or_404(lab_id, database, current_user)

    snapshot = (
        database.query(models.ConfigSnapshot)
        .filter(
            models.ConfigSnapshot.id == snapshot_id,
            models.ConfigSnapshot.lab_id == lab_id,
        )
        .first()
    )

    if not snapshot:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    database.delete(snapshot)
    database.commit()

    return {"status": "deleted", "snapshot_id": snapshot_id}


@router.post("/labs/{lab_id}/config-diff")
def generate_config_diff(
    lab_id: str,
    payload: schemas.ConfigDiffRequest,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.ConfigDiffResponse:
    """Generate a unified diff between two config snapshots.

    Uses Python's difflib to compute the diff. Returns structured diff
    lines with line numbers and change types for easy frontend rendering.
    """
    import difflib

    lab = get_lab_or_404(lab_id, database, current_user)

    # Fetch both snapshots
    snapshot_a = (
        database.query(models.ConfigSnapshot)
        .filter(
            models.ConfigSnapshot.id == payload.snapshot_id_a,
            models.ConfigSnapshot.lab_id == lab_id,
        )
        .first()
    )

    snapshot_b = (
        database.query(models.ConfigSnapshot)
        .filter(
            models.ConfigSnapshot.id == payload.snapshot_id_b,
            models.ConfigSnapshot.lab_id == lab_id,
        )
        .first()
    )

    if not snapshot_a:
        raise HTTPException(
            status_code=404,
            detail=f"Snapshot A not found: {payload.snapshot_id_a}"
        )

    if not snapshot_b:
        raise HTTPException(
            status_code=404,
            detail=f"Snapshot B not found: {payload.snapshot_id_b}"
        )

    # Generate unified diff
    lines_a = snapshot_a.content.splitlines(keepends=True)
    lines_b = snapshot_b.content.splitlines(keepends=True)

    diff = list(difflib.unified_diff(
        lines_a,
        lines_b,
        fromfile=f"{snapshot_a.node_name} ({snapshot_a.created_at.strftime('%Y-%m-%d %H:%M')})",
        tofile=f"{snapshot_b.node_name} ({snapshot_b.created_at.strftime('%Y-%m-%d %H:%M')})",
        lineterm="",
    ))

    # Parse diff into structured lines
    diff_lines: list[schemas.ConfigDiffLine] = []
    additions = 0
    deletions = 0
    line_num_a = 0
    line_num_b = 0

    for line in diff:
        # Strip trailing newline for cleaner display
        line_content = line.rstrip("\n\r")

        if line.startswith("---") or line.startswith("+++"):
            diff_lines.append(schemas.ConfigDiffLine(
                content=line_content,
                type="header",
            ))
        elif line.startswith("@@"):
            # Parse hunk header to get line numbers
            # Format: @@ -start,count +start,count @@
            import re
            match = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
            if match:
                line_num_a = int(match.group(1)) - 1  # -1 because we increment before use
                line_num_b = int(match.group(2)) - 1
            diff_lines.append(schemas.ConfigDiffLine(
                content=line_content,
                type="header",
            ))
        elif line.startswith("-"):
            line_num_a += 1
            deletions += 1
            diff_lines.append(schemas.ConfigDiffLine(
                line_number_a=line_num_a,
                content=line_content[1:],  # Remove leading -
                type="removed",
            ))
        elif line.startswith("+"):
            line_num_b += 1
            additions += 1
            diff_lines.append(schemas.ConfigDiffLine(
                line_number_b=line_num_b,
                content=line_content[1:],  # Remove leading +
                type="added",
            ))
        elif line.startswith(" "):
            line_num_a += 1
            line_num_b += 1
            diff_lines.append(schemas.ConfigDiffLine(
                line_number_a=line_num_a,
                line_number_b=line_num_b,
                content=line_content[1:],  # Remove leading space
                type="unchanged",
            ))

    return schemas.ConfigDiffResponse(
        snapshot_a=schemas.ConfigSnapshotOut.model_validate(snapshot_a),
        snapshot_b=schemas.ConfigSnapshotOut.model_validate(snapshot_b),
        diff_lines=diff_lines,
        additions=additions,
        deletions=deletions,
    )
