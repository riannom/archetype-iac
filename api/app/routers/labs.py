"""Lab CRUD and topology management endpoints."""
from __future__ import annotations

import shutil
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import db, models, schemas
from app.auth import get_current_user
from app.storage import (
    delete_layout,
    ensure_topology_file,
    lab_workspace,
    layout_path,
    read_layout,
    topology_path,
    write_layout,
)
from app.topology import graph_to_yaml, yaml_to_graph
from app.utils.lab import get_lab_or_404

router = APIRouter(tags=["labs"])


def _upsert_node_states(
    database: Session,
    lab_id: str,
    graph: schemas.TopologyGraph,
) -> None:
    """Create or update NodeState records for all nodes in a topology graph.

    New nodes are initialized with desired_state='stopped', actual_state='undeployed'.
    Existing nodes have their node_name updated but state fields are preserved.
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

    # Create mapping of node_id -> node for lookups
    nodes_by_id = {node.id: node for node in graph.nodes}

    # Update or create node states
    for node in graph.nodes:
        if node.id in existing_by_node_id:
            # Update existing - only update node_name in case it changed
            existing = existing_by_node_id[node.id]
            existing.node_name = node.name
        else:
            # Create new with defaults
            new_state = models.NodeState(
                lab_id=lab_id,
                node_id=node.id,
                node_name=node.name,
                desired_state="stopped",
                actual_state="undeployed",
            )
            database.add(new_state)

    # Delete node states for nodes no longer in topology
    for existing_node_id, existing_state in existing_by_node_id.items():
        if existing_node_id not in current_node_ids:
            database.delete(existing_state)


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


@router.delete("/labs/{lab_id}")
def delete_lab(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, str]:
    lab = get_lab_or_404(lab_id, database, current_user)
    if lab.owner_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Access denied")

    # Delete related records first to avoid foreign key violations
    database.query(models.Job).filter(models.Job.lab_id == lab_id).delete()
    database.query(models.Permission).filter(models.Permission.lab_id == lab_id).delete()
    database.query(models.LabFile).filter(models.LabFile.lab_id == lab_id).delete()
    database.query(models.NodePlacement).filter(models.NodePlacement.lab_id == lab_id).delete()

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
    topology_path(lab.id).write_text(payload.content, encoding="utf-8")
    return schemas.LabOut.model_validate(lab)


@router.get("/labs/{lab_id}/export-yaml")
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
    yaml_content = graph_to_yaml(payload)
    topology_path(lab.id).write_text(yaml_content, encoding="utf-8")

    # Create/update NodeState records for all nodes in the topology
    _upsert_node_states(database, lab.id, payload)
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
def list_node_states(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.NodeStatesResponse:
    """Get all node states for a lab.

    Returns the desired and actual state for each node in the topology.
    """
    get_lab_or_404(lab_id, database, current_user)
    states = (
        database.query(models.NodeState)
        .filter(models.NodeState.lab_id == lab_id)
        .order_by(models.NodeState.node_name)
        .all()
    )
    return schemas.NodeStatesResponse(
        nodes=[schemas.NodeStateOut.model_validate(s) for s in states]
    )


@router.get("/labs/{lab_id}/nodes/{node_id}/state")
def get_node_state(
    lab_id: str,
    node_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.NodeStateOut:
    """Get the state for a specific node."""
    get_lab_or_404(lab_id, database, current_user)
    state = (
        database.query(models.NodeState)
        .filter(
            models.NodeState.lab_id == lab_id,
            models.NodeState.node_id == node_id,
        )
        .first()
    )
    if not state:
        raise HTTPException(status_code=404, detail="Node state not found")
    return schemas.NodeStateOut.model_validate(state)


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
    get_lab_or_404(lab_id, database, current_user)
    state = (
        database.query(models.NodeState)
        .filter(
            models.NodeState.lab_id == lab_id,
            models.NodeState.node_id == node_id,
        )
        .first()
    )
    if not state:
        raise HTTPException(status_code=404, detail="Node state not found")

    state.desired_state = payload.state
    database.commit()
    database.refresh(state)
    return schemas.NodeStateOut.model_validate(state)


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
    get_lab_or_404(lab_id, database, current_user)
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
        nodes=[schemas.NodeStateOut.model_validate(s) for s in states]
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
        raise HTTPException(status_code=404, detail="Node state not found")

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

    # Mark node as pending
    state.actual_state = "pending"
    state.error_message = None
    database.commit()

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

    # Mark all syncing nodes as pending
    for state in out_of_sync:
        state.actual_state = "pending"
        state.error_message = None
    database.commit()

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
