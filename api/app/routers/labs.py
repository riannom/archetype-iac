"""Lab CRUD and topology management endpoints."""
from __future__ import annotations

import asyncio
import io
import json
import logging
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import agent_client, db, models, schemas
from app.auth import get_current_user
from app.services.topology import TopologyService
from app.storage import (
    delete_layout,
    lab_workspace,
    read_layout,
    write_layout,
)
from app.tasks.jobs import run_agent_job, run_multihost_destroy
from app.enums import GlobalRole, LabRole
from app.services.permissions import PermissionService
from app.utils.lab import get_lab_or_404, get_lab_with_role, get_lab_provider, update_lab_provider_from_nodes
from app.utils.agents import get_online_agent_for_lab
from app.utils.http import require_lab_owner, raise_not_found, raise_unavailable
from app.utils.link import generate_link_name
from app.services.interface_naming import normalize_interface
from app.tasks.live_links import create_link_if_ready, _build_host_to_agent_map, teardown_link
from app.tasks.link_reconciliation import reconcile_lab_links
from app.services import interface_mapping as interface_mapping_service
from app.utils.async_tasks import safe_create_task
from app.jobs import has_conflicting_job
from app.services.state_machine import NodeStateMachine
from app.events.publisher import emit_lab_deleted, emit_link_removed, emit_node_removed

logger = logging.getLogger(__name__)

router = APIRouter(tags=["labs"])


def _zip_safe_name(value: str) -> str:
    """Prevent zip path traversal and invalid separators."""
    return (value or "unknown").replace("/", "_").replace("\\", "_")


def _require_lab_editor(lab_id: str, database: Session, user: models.User) -> models.Lab:
    """Get lab and verify user has at least editor role. Raises 403/404."""
    lab, role = get_lab_with_role(lab_id, database, user)
    if role < LabRole.EDITOR:
        raise HTTPException(status_code=403, detail="Editor access required")
    return lab


def _enrich_node_state(state: models.NodeState) -> schemas.NodeStateOut:
    """Convert a NodeState model to schema with all_ips parsed from JSON."""
    node_data = schemas.NodeStateOut.model_validate(state)
    if state.management_ips_json:
        try:
            node_data.all_ips = json.loads(state.management_ips_json)
        except (json.JSONDecodeError, TypeError):
            node_data.all_ips = []
    # Compute will_retry: error state with retries remaining and not permanently failed
    from app.config import settings
    node_data.will_retry = (
        state.actual_state == "error"
        and state.enforcement_attempts < settings.state_enforcement_max_retries
        and state.enforcement_failed_at is None
    )
    return node_data


def _get_or_create_node_state(
    database: Session,
    lab_id: str,
    node_id: str,
    initial_desired_state: str = "stopped",
    for_update: bool = False,
) -> models.NodeState:
    """Get or create NodeState, using Node definition for correct naming.

    This eliminates the node_name=node_id placeholder issue by looking up
    the Node definition to get the correct container_name.

    Args:
        database: Database session
        lab_id: Lab identifier
        node_id: Frontend GUI node ID
        initial_desired_state: Desired state for new records (default: "stopped")
        for_update: If True, acquire row-level lock (SELECT FOR UPDATE)

    Returns:
        Existing or newly created NodeState record
    """
    query = (
        database.query(models.NodeState)
        .filter(
            models.NodeState.lab_id == lab_id,
            models.NodeState.node_id == node_id,
        )
    )
    if for_update:
        query = query.with_for_update()
    state = query.first()
    if state:
        return state

    # Look up Node definition to get correct container_name
    node_def = (
        database.query(models.Node)
        .filter(models.Node.lab_id == lab_id, models.Node.gui_id == node_id)
        .first()
    )

    if node_def:
        node_name = node_def.container_name
        node_definition_id = node_def.id
    else:
        # Fallback: placeholder will be corrected by topology sync
        logger.warning(f"Creating NodeState with placeholder for {node_id}")
        node_name = node_id
        node_definition_id = None

    state = models.NodeState(
        lab_id=lab_id,
        node_id=node_id,
        node_name=node_name,
        node_definition_id=node_definition_id,
        desired_state=initial_desired_state,
        actual_state="undeployed",
    )
    database.add(state)
    database.commit()
    database.refresh(state)
    return state


def _create_node_sync_job(
    database: Session,
    lab: models.Lab,
    node_id: str,
    current_user: models.User,
) -> None:
    """Create a sync job for a single node and start the background task."""
    from app.tasks.jobs import run_node_reconcile
    from app.utils.lab import get_node_provider

    db_node = database.query(models.Node).filter(
        models.Node.lab_id == lab.id,
        models.Node.gui_id == node_id
    ).first()
    if db_node:
        provider = get_node_provider(db_node, database)
    else:
        provider = get_lab_provider(lab)

    job = models.Job(
        lab_id=lab.id,
        user_id=current_user.id,
        action=f"sync:node:{node_id}",
        status="queued",
    )
    database.add(job)
    database.commit()
    database.refresh(job)

    safe_create_task(
        run_node_reconcile(job.id, lab.id, [node_id], provider=provider),
        name=f"sync:node:{job.id}"
    )


def _upsert_node_states(
    database: Session,
    lab_id: str,
    graph: schemas.TopologyGraph,
) -> tuple[list[str], list[dict]]:
    """Create or update NodeState records for all nodes in a topology graph.

    New nodes are initialized with desired_state='stopped', actual_state='undeployed'.
    IMPORTANT: For existing nodes, node_name is NOT updated to preserve container identity.
    This allows display names to change in the UI without breaking container operations.
    Nodes removed from topology have their NodeState records deleted.

    Returns:
        Tuple of (added_node_ids, removed_node_info) for live node operations.
        removed_node_info is a list of dicts with node details for teardown.
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

    added_node_ids: list[str] = []
    removed_node_info: list[dict] = []

    # Update or create node states
    for node in graph.nodes:
        # Use container_name (YAML key) for container operations, fall back to name
        container_name = node.container_name or node.name
        if node.id in existing_by_node_id:
            existing_state = existing_by_node_id[node.id]
            # Fix node_name if it was set as a placeholder from lazy initialization.
            # Lazy init sets node_name=node_id as a temporary value until topology syncs.
            # Once a node is deployed (has a real container_name), we must NOT change it
            # because that would break console/operations for existing containers.
            if existing_state.node_name == node.id and existing_state.node_name != container_name:
                # This was a placeholder - safe to correct it
                existing_state.node_name = container_name
            # If node_name != node_id, it was already set correctly or deployed - don't touch
        else:
            # Create new with defaults - node_name is set only once at creation
            new_state = models.NodeState(
                lab_id=lab_id,
                node_id=node.id,
                node_name=container_name,
                desired_state="stopped",
                actual_state="undeployed",
            )
            database.add(new_state)
            added_node_ids.append(node.id)

    # Collect info about nodes being removed (for teardown)
    for existing_node_id, existing_state in existing_by_node_id.items():
        if existing_node_id not in current_node_ids:
            # Get placement info for teardown
            placement = (
                database.query(models.NodePlacement)
                .filter(
                    models.NodePlacement.lab_id == lab_id,
                    models.NodePlacement.node_name == existing_state.node_name,
                )
                .first()
            )
            # Determine provider before node definition is gone
            provider = "docker"
            node_def = database.query(models.Node).filter(
                models.Node.lab_id == lab_id,
                models.Node.gui_id == existing_node_id,
            ).first()
            if node_def:
                from app.utils.lab import get_node_provider
                try:
                    provider = get_node_provider(node_def, database)
                except Exception:
                    pass
            removed_node_info.append({
                "node_id": existing_state.node_id,
                "node_name": existing_state.node_name,
                "actual_state": existing_state.actual_state,
                "host_id": placement.host_id if placement else None,
                "provider": provider,
            })
            database.delete(existing_state)

    return added_node_ids, removed_node_info


def _ensure_node_states_exist(
    database: Session,
    lab_id: str,
) -> None:
    """Ensure NodeState records exist for all nodes in the topology.

    Uses database as source of truth.
    Safe to call multiple times - idempotent operation.
    """
    service = TopologyService(database)
    if service.has_nodes(lab_id):
        graph = service.export_to_graph(lab_id)
        _upsert_node_states(database, lab_id, graph)
        database.commit()


@router.get("/labs")
def list_labs(
    skip: int = 0,
    limit: int = 50,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, list[schemas.LabOut]]:
    global_role = PermissionService.get_user_global_role(current_user)

    if global_role >= GlobalRole.ADMIN:
        # Admins see all labs
        query = database.query(models.Lab)
    else:
        # Operators see own labs + shared; viewers see only shared
        owned = database.query(models.Lab).filter(models.Lab.owner_id == current_user.id)
        shared = (
            database.query(models.Lab)
            .join(models.Permission, models.Permission.lab_id == models.Lab.id)
            .filter(models.Permission.user_id == current_user.id)
        )
        query = owned.union(shared)

    labs = (
        query.order_by(models.Lab.created_at.desc())
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
    PermissionService.require_global_role(current_user, GlobalRole.OPERATOR)
    lab = models.Lab(name=payload.name, owner_id=current_user.id, provider=payload.provider)
    database.add(lab)
    database.flush()
    workspace = lab_workspace(lab.id)
    workspace.mkdir(parents=True, exist_ok=True)
    lab.workspace_path = str(workspace)
    database.commit()
    database.refresh(lab)
    return schemas.LabOut.model_validate(lab)


@router.get("/labs/{lab_id}")
def get_lab(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.LabOut:
    lab, role = get_lab_with_role(lab_id, database, current_user)
    out = schemas.LabOut.model_validate(lab)
    out.user_role = role.value
    return out


@router.put("/labs/{lab_id}")
def update_lab(
    lab_id: str,
    payload: schemas.LabUpdate,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.LabOut:
    lab = get_lab_or_404(lab_id, database, current_user)
    require_lab_owner(current_user, lab, db=database)
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
    require_lab_owner(current_user, lab, db=database)

    # If lab has running infrastructure, destroy it first
    if lab.state in ("running", "starting", "stopping"):
        logger.info(f"Lab {lab_id} has state '{lab.state}', destroying infrastructure before deletion")

        # Check for multi-host deployment using database
        service = TopologyService(database)
        is_multihost = service.is_multihost(lab.id) if service.has_nodes(lab.id) else False

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
                    destroy_job.id, lab.id, provider=lab_provider
                )
            else:
                # Check for healthy agent
                agent = await get_online_agent_for_lab(database, lab, required_provider=lab_provider)
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
    asyncio.create_task(emit_lab_deleted(lab_id))
    return {"status": "deleted"}


@router.post("/labs/{lab_id}/clone")
def clone_lab(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.LabOut:
    PermissionService.require_global_role(current_user, GlobalRole.OPERATOR)
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


@router.post("/labs/{lab_id}/update-topology-from-yaml")
async def update_topology_from_yaml(
    lab_id: str,
    payload: schemas.LabYamlIn,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.LabOut:
    lab = _require_lab_editor(lab_id, database, current_user)
    workspace = lab_workspace(lab.id)
    workspace.mkdir(parents=True, exist_ok=True)

    # Store topology in database (source of truth)
    service = TopologyService(database)
    service.update_from_yaml(lab.id, payload.content)

    # Update lab provider based on node image types
    # This ensures VMs (IOSv, etc.) use libvirt and containers use docker
    update_lab_provider_from_nodes(database, lab)

    # Sync NodeState/LinkState records from database
    graph = service.export_to_graph(lab.id)
    added_node_ids, removed_node_info = _upsert_node_states(database, lab.id, graph)
    created, updated, added_link_names, removed_link_info = _upsert_link_states(
        database, lab.id, graph
    )

    database.commit()

    # Trigger live link operations in background if there are changes
    if added_link_names or removed_link_info:
        from app.tasks.live_links import process_link_changes
        safe_create_task(
            process_link_changes(lab.id, added_link_names, removed_link_info, current_user.id),
            name=f"live_links:{lab.id}"
        )

    # Trigger live node operations in background if there are changes
    if added_node_ids or removed_node_info:
        from app.tasks.live_nodes import process_node_changes
        safe_create_task(
            process_node_changes(lab.id, added_node_ids, removed_node_info),
            name=f"live_nodes:{lab.id}"
        )

    return schemas.LabOut.model_validate(lab)


@router.get("/labs/{lab_id}/export-yaml")
def export_yaml(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.LabYamlOut:
    lab = get_lab_or_404(lab_id, database, current_user)

    service = TopologyService(database)
    if not service.has_nodes(lab.id):
        raise_not_found("Topology not found")
    return schemas.LabYamlOut(content=service.export_to_yaml(lab.id))


@router.post("/labs/{lab_id}/update-topology")
async def update_topology(
    lab_id: str,
    payload: schemas.TopologyGraph,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.LabOut:
    lab = _require_lab_editor(lab_id, database, current_user)
    workspace = lab_workspace(lab.id)
    workspace.mkdir(parents=True, exist_ok=True)

    # Store topology in database (source of truth)
    service = TopologyService(database)
    try:
        service.update_from_graph(lab.id, payload)
    except ValueError as e:
        # Invalid host assignment or other validation error
        raise HTTPException(status_code=400, detail=str(e))

    # Update lab provider based on node image types
    # This ensures VMs (IOSv, etc.) use libvirt and containers use docker
    update_lab_provider_from_nodes(database, lab)

    # Create/update NodeState records for all nodes in the topology
    added_node_ids, removed_node_info = _upsert_node_states(database, lab.id, payload)

    # Create/update LinkState records for all links in the topology
    created, updated, added_link_names, removed_link_info = _upsert_link_states(
        database, lab.id, payload
    )

    database.commit()

    # Emit cleanup events for removed nodes/links
    for info in removed_node_info:
        asyncio.create_task(emit_node_removed(lab.id, info["node_name"], info.get("host_id")))
    if removed_link_info:
        asyncio.create_task(emit_link_removed(lab.id))

    # Trigger live link operations in background if there are changes
    if added_link_names or removed_link_info:
        from app.tasks.live_links import process_link_changes
        safe_create_task(
            process_link_changes(lab.id, added_link_names, removed_link_info, current_user.id),
            name=f"live_links_update:{lab.id}"
        )

    # Trigger live node operations in background if there are changes
    if added_node_ids or removed_node_info:
        from app.tasks.live_nodes import process_node_changes
        safe_create_task(
            process_node_changes(lab.id, added_node_ids, removed_node_info),
            name=f"live_nodes_update:{lab.id}"
        )

    return schemas.LabOut.model_validate(lab)


class CheckResourcesRequest(BaseModel):
    """Request body for resource capacity check."""
    node_ids: list[str] | None = None  # null = check all nodes


class PerHostCapacity(BaseModel):
    agent_name: str = ""
    fits: bool = True
    has_warnings: bool = False
    projected_memory_pct: float = 0
    projected_cpu_pct: float = 0
    projected_disk_pct: float = 0
    node_count: int = 0
    required_memory_mb: int = 0
    required_cpu_cores: int = 0
    available_memory_mb: float = 0
    available_cpu_cores: float = 0
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class CheckResourcesResponse(BaseModel):
    sufficient: bool = True
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    per_host: dict[str, PerHostCapacity] = Field(default_factory=dict)


@router.post("/labs/{lab_id}/check-resources")
def check_resources(
    lab_id: str,
    payload: CheckResourcesRequest | None = None,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> CheckResourcesResponse:
    """Check if agents have sufficient resources to deploy lab nodes.

    Returns projected resource usage per host with warnings and errors.
    Does not block or modify anything - purely informational.
    """
    from app.services.resource_capacity import (
        check_multihost_capacity,
    )

    lab = get_lab_or_404(lab_id, database, current_user)
    service = TopologyService(database)
    nodes = service.get_nodes(lab_id)

    # Filter to specific nodes if requested
    if payload and payload.node_ids:
        nodes = [n for n in nodes if n.gui_id in payload.node_ids or n.id in payload.node_ids]

    # Build host -> device_types mapping
    host_device_map: dict[str, list[str]] = {}
    unplaced = []
    for node in nodes:
        if node.node_type == "external":
            continue
        if node.host_id:
            if node.host_id not in host_device_map:
                host_device_map[node.host_id] = []
            host_device_map[node.host_id].append(node.device or "linux")
        else:
            unplaced.append(node)

    # Assign unplaced nodes to lab's default agent for estimation
    if unplaced and lab.agent_id:
        if lab.agent_id not in host_device_map:
            host_device_map[lab.agent_id] = []
        for node in unplaced:
            host_device_map[lab.agent_id].append(node.device or "linux")

    if not host_device_map:
        return CheckResourcesResponse()

    results = check_multihost_capacity(host_device_map, database)

    response = CheckResourcesResponse()
    all_warnings = []
    all_errors = []

    for host_id, result in results.items():
        per_host = PerHostCapacity(
            agent_name=result.agent_name,
            fits=result.fits,
            has_warnings=result.has_warnings,
            projected_memory_pct=result.projected_memory_pct,
            projected_cpu_pct=result.projected_cpu_pct,
            projected_disk_pct=result.projected_disk_pct,
            node_count=result.node_count,
            required_memory_mb=result.required_memory_mb,
            required_cpu_cores=result.required_cpu_cores,
            available_memory_mb=result.available_memory_mb,
            available_cpu_cores=result.available_cpu_cores,
            errors=result.errors,
            warnings=result.warnings,
        )
        response.per_host[host_id] = per_host

        if not result.fits:
            response.sufficient = False
            for e in result.errors:
                all_errors.append(f"{result.agent_name}: {e}")

        if result.has_warnings:
            for w in result.warnings:
                all_warnings.append(f"{result.agent_name}: {w}")

    response.warnings = all_warnings
    response.errors = all_errors
    return response


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

    service = TopologyService(database)
    if not service.has_nodes(lab.id):
        raise_not_found("Topology not found")
    graph = service.export_to_graph(lab.id)

    if include_layout:
        layout = read_layout(lab.id)
        return TopologyGraphWithLayout(**graph.model_dump(), layout=layout)
    return graph


@router.get("/labs/{lab_id}/download-bundle")
def download_lab_bundle(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Download a full lab bundle zip with topology, layout, and configs."""
    lab = get_lab_or_404(lab_id, database, current_user)

    service = TopologyService(database)
    has_topology = service.has_nodes(lab.id)
    topology_yaml = service.export_to_yaml(lab.id) if has_topology else "nodes: {}\nlinks: []\n"
    topology_graph = (
        service.export_to_graph(lab.id).model_dump(mode="json")
        if has_topology
        else {"nodes": [], "links": []}
    )

    layout = read_layout(lab.id)
    layout_json = layout.model_dump(mode="json") if layout else None

    from app.services.config_service import ConfigService, MAX_ZIP_SIZE_BYTES

    config_svc = ConfigService(database)
    snapshots = config_svc.list_configs_with_orphan_status(lab_id=lab_id)

    total_config_size = sum(len((s.get("content") or "").encode()) for s in snapshots)
    if total_config_size > MAX_ZIP_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Bundle would exceed {MAX_ZIP_SIZE_BYTES // (1024 * 1024)}MB limit "
                f"({total_config_size // (1024 * 1024)}MB estimated). "
                "Try downloading configs separately."
            ),
        )

    buf = io.BytesIO()
    metadata_by_bucket: dict[str, dict[str, list[dict]]] = {
        "configs": {},
        "orphaned configs": {},
    }

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("topology/topology.yaml", topology_yaml)
        zf.writestr("topology/topology.json", json.dumps(topology_graph, indent=2))
        zf.writestr("topology/layout.json", json.dumps(layout_json, indent=2))

        for snap in snapshots:
            node_name = _zip_safe_name(str(snap.get("node_name") or "unknown"))
            bucket = "orphaned configs" if snap.get("is_orphaned") else "configs"
            created_at = snap.get("created_at")
            ts = created_at.strftime("%Y%m%d_%H%M%S") if created_at else "unknown"
            snapshot_type = _zip_safe_name(str(snap.get("snapshot_type") or "snapshot"))

            zf.writestr(
                f"{bucket}/{node_name}/{ts}_{snapshot_type}_startup-config",
                str(snap.get("content") or ""),
            )

            metadata_by_bucket[bucket].setdefault(node_name, []).append(
                {
                    "id": snap.get("id"),
                    "node_name": snap.get("node_name"),
                    "timestamp": created_at.isoformat() if created_at else None,
                    "type": snap.get("snapshot_type"),
                    "content_hash": snap.get("content_hash"),
                    "device_kind": snap.get("device_kind"),
                    "is_active": bool(snap.get("is_active")),
                }
            )

        for bucket, node_map in metadata_by_bucket.items():
            for node_name, entries in node_map.items():
                zf.writestr(
                    f"{bucket}/{node_name}/metadata.json",
                    json.dumps(entries, indent=2),
                )

        zf.writestr(
            "bundle-metadata.json",
            json.dumps(
                {
                    "lab_id": lab.id,
                    "lab_name": lab.name,
                    "exported_at": datetime.now(timezone.utc).isoformat(),
                    "topology_included": True,
                    "layout_included": True,
                    "snapshot_count": len(snapshots),
                    "configs_count": sum(
                        len(entries) for entries in metadata_by_bucket["configs"].values()
                    ),
                    "orphaned_configs_count": sum(
                        len(entries)
                        for entries in metadata_by_bucket["orphaned configs"].values()
                    ),
                    "directories": ["topology", "configs", "orphaned configs"],
                },
                indent=2,
            ),
        )

    buf.seek(0)
    lab_name = _zip_safe_name(lab.name or lab.id).replace(" ", "_")
    filename = f"{lab_name}_bundle.zip"

    from starlette.responses import StreamingResponse

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


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
        raise_not_found("Layout not found")
    return layout


@router.put("/labs/{lab_id}/layout")
def save_layout(
    lab_id: str,
    payload: schemas.LabLayout,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.LabLayout:
    """Save or update layout data for a lab."""
    lab = _require_lab_editor(lab_id, database, current_user)
    write_layout(lab.id, payload)
    return payload


@router.delete("/labs/{lab_id}/layout")
def remove_layout(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, str]:
    """Delete layout data, reverting to auto-layout on next load."""
    lab = _require_lab_editor(lab_id, database, current_user)
    deleted = delete_layout(lab.id)
    if not deleted:
        raise_not_found("Layout not found")
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

    # Sync NodeState records from database topology
    service = TopologyService(database)
    if service.has_nodes(lab.id):
        graph = service.export_to_graph(lab.id)
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
                agent = await get_online_agent_for_lab(
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
                            # NOTE: Don't mark as "undeployed" here if container not found.
                            # In multi-host labs, this agent may not be the one hosting the node.
                            # Let the reconciliation task handle undeployed detection properly.
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
    state = _get_or_create_node_state(database, lab.id, node_id)
    return _enrich_node_state(state)


@router.put("/labs/{lab_id}/nodes/{node_id}/desired-state")
async def set_node_desired_state(
    lab_id: str,
    node_id: str,
    payload: schemas.NodeStateUpdate,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.NodeStateOut:
    """Set the desired state for a node (running or stopped).

    Auto-triggers sync to immediately apply the change. This eliminates
    the need for a separate "Sync" button in the UI.
    """

    lab = _require_lab_editor(lab_id, database, current_user)

    logger.info(
        "User state change request",
        extra={
            "event": "user_state_request",
            "user_id": str(current_user.id),
            "user_email": current_user.email,
            "lab_id": lab_id,
            "node_id": node_id,
            "requested_state": payload.state,
            "endpoint": "set_node_desired_state",
        },
    )

    _ensure_node_states_exist(database, lab.id)
    command = "start" if payload.state == "running" else "stop"
    state = _get_or_create_node_state(database, lab.id, node_id, initial_desired_state=payload.state, for_update=True)

    # Centralized guard check (6.1)
    allowed, reason = NodeStateMachine.can_accept_command(state.actual_state, command)
    if not allowed:
        raise HTTPException(status_code=409, detail=reason)

    # Check if state actually changed
    desired_changed = state.desired_state != payload.state

    # Update desired state (may differ from initial if record already existed)
    if desired_changed:
        state.desired_state = payload.state
        database.commit()
        database.refresh(state)

        # Auto-sync: immediately trigger reconciliation for this node
        has_conflict, _ = has_conflicting_job(lab_id, "sync", session=database)
        if not has_conflict and NodeStateMachine.needs_sync(state.actual_state, command):
            _create_node_sync_job(database, lab, node_id, current_user)

    elif (
        state.desired_state == payload.state
        and payload.state == "running"
        and state.actual_state == "error"
    ):
        # Retry: node is stuck in error but user wants it running again.
        # Reset enforcement state so the system will attempt reconciliation.
        state.enforcement_attempts = 0
        state.enforcement_failed_at = None
        state.last_enforcement_at = None
        state.error_message = None
        database.commit()
        database.refresh(state)

        has_conflict, _ = has_conflicting_job(lab_id, "sync", session=database)
        if not has_conflict:
            _create_node_sync_job(database, lab, node_id, current_user)

    return _enrich_node_state(state)


@router.put("/labs/{lab_id}/nodes/desired-state")
async def set_all_nodes_desired_state(
    lab_id: str,
    payload: schemas.NodeStateUpdate,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.NodeStatesResponse:
    """Set the desired state for all nodes in a lab.

    Useful for "Start All" or "Stop All" operations.
    Selectively processes nodes that are ready, skips transitional nodes,
    and returns counts of affected/skipped/already-in-state.
    """
    from app.tasks.jobs import run_node_reconcile
    from app.utils.lab import get_lab_provider

    lab = _require_lab_editor(lab_id, database, current_user)

    logger.info(
        "User state change request",
        extra={
            "event": "user_state_request",
            "user_id": str(current_user.id),
            "user_email": current_user.email,
            "lab_id": lab_id,
            "requested_state": payload.state,
            "endpoint": "set_all_nodes_desired_state",
        },
    )

    _ensure_node_states_exist(database, lab.id)
    command = "start" if payload.state == "running" else "stop"

    states = (
        database.query(models.NodeState)
        .filter(models.NodeState.lab_id == lab_id)
        .with_for_update()
        .all()
    )

    # Selective processing: classify each node via centralized guards (6.1)
    affected_count = 0
    skipped_transitional_count = 0
    already_in_state_count = 0
    nodes_needing_sync: list[str] = []

    for state in states:
        classification, _reason = NodeStateMachine.can_accept_bulk_command(
            state.actual_state, state.desired_state, command
        )

        if classification == "skip_transitional":
            skipped_transitional_count += 1
            continue

        if classification == "already_in_state":
            already_in_state_count += 1
            continue

        # This node can be processed (proceed or reset_and_proceed)
        state.desired_state = payload.state

        # Reset enforcement state for error nodes being retried
        if classification == "reset_and_proceed":
            state.enforcement_attempts = 0
            state.enforcement_failed_at = None
            state.last_enforcement_at = None
            state.error_message = None

        affected_count += 1

        if NodeStateMachine.needs_sync(state.actual_state, command):
            nodes_needing_sync.append(state.node_id)

    database.commit()

    # Clear enforcement cooldowns so enforcement doesn't ignore the new
    # desired state for minutes due to stale Redis TTL keys
    if affected_count > 0:
        affected_names = [
            s.node_name for s in states
            if s.desired_state == payload.state
        ]
        from app.tasks.state_enforcement import clear_cooldowns_for_lab
        safe_create_task(
            clear_cooldowns_for_lab(lab_id, affected_names),
            name=f"clear_cooldowns:{lab_id}"
        )

    logger.info(
        "Bulk state change result",
        extra={
            "event": "bulk_state_result",
            "lab_id": lab_id,
            "requested_state": payload.state,
            "affected": affected_count,
            "skipped_transitional": skipped_transitional_count,
            "already_in_state": already_in_state_count,
            "nodes_needing_sync": len(nodes_needing_sync),
        },
    )

    # Auto-sync: immediately trigger reconciliation for affected nodes
    if nodes_needing_sync:
        has_conflict, _ = has_conflicting_job(lab_id, "sync", session=database)
        if not has_conflict:
            provider = get_lab_provider(lab)
            job = models.Job(
                lab_id=lab.id,
                user_id=current_user.id,
                action=f"sync:lab:{','.join(nodes_needing_sync)}",
                status="queued",
            )
            database.add(job)
            database.commit()
            database.refresh(job)

            safe_create_task(
                run_node_reconcile(job.id, lab.id, nodes_needing_sync, provider=provider),
                name=f"sync:bulk:{job.id}"
            )

    # Refresh and return all states with counts
    states = (
        database.query(models.NodeState)
        .filter(models.NodeState.lab_id == lab_id)
        .order_by(models.NodeState.node_name)
        .all()
    )
    return schemas.NodeStatesResponse(
        nodes=[_enrich_node_state(s) for s in states],
        affected=affected_count,
        skipped_transitional=skipped_transitional_count,
        already_in_state=already_in_state_count,
    )


@router.post("/labs/{lab_id}/nodes/refresh")
async def refresh_node_states(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.NodeStatesResponse:
    """Refresh node states from actual container status.

    Queries all agents that have nodes for this lab and updates the NodeState
    records to match. Use this when states appear out of sync with reality.
    """
    from app import agent_client
    from app.utils.lab import get_lab_provider

    lab = get_lab_or_404(lab_id, database, current_user)
    _ensure_node_states_exist(database, lab.id)

    lab_provider = get_lab_provider(lab)

    # Get ALL agents that have nodes for this lab (multi-host support)
    # Same pattern as reconciliation.py
    placements = (
        database.query(models.NodePlacement)
        .filter(models.NodePlacement.lab_id == lab_id)
        .all()
    )
    agent_ids = {p.host_id for p in placements}
    # Map node names to their expected agent for safer state updates
    node_expected_agent: dict[str, str] = {p.node_name: p.host_id for p in placements}

    # Also include the lab's default agent if set
    if lab.agent_id:
        agent_ids.add(lab.agent_id)

    # If no placements and no default, find any healthy agent
    if not agent_ids:
        fallback_agent = await get_online_agent_for_lab(
            database, lab, required_provider=lab_provider
        )
        if fallback_agent:
            agent_ids.add(fallback_agent.id)

    if not agent_ids:
        raise_unavailable("No healthy agent available")

    # Query actual container status from ALL agents
    container_status_map: dict[str, str] = {}
    agents_successfully_queried: set[str] = set()

    for agent_id in agent_ids:
        agent = database.get(models.Host, agent_id)
        if not agent or not agent_client.is_agent_online(agent):
            continue

        try:
            result = await agent_client.get_lab_status_from_agent(agent, lab.id)
            nodes = result.get("nodes", [])
            agent_error = result.get("error")

            # Only count as successfully queried if no error in response
            if not agent_error:
                agents_successfully_queried.add(agent_id)
            else:
                logger.warning(f"Agent {agent.name} returned error for lab {lab_id}: {agent_error}")

            # Still merge any nodes that were returned (partial success)
            for n in nodes:
                node_name = n.get("name", "")
                if node_name:
                    container_status_map[node_name] = n.get("status", "unknown")
        except Exception as e:
            # Log but continue - we'll update states for agents that responded
            logger.warning(f"Failed to query agent {agent.name} for lab {lab_id}: {e}")

    if not agents_successfully_queried:
        raise_unavailable("Failed to reach any agent for this lab")

    # Update NodeState records based on actual container status
    node_states = (
        database.query(models.NodeState)
        .filter(models.NodeState.lab_id == lab_id)
        .all()
    )

    for ns in node_states:
        # Skip nodes with active transitional operations - let the job handle state updates
        # This prevents refresh from overwriting "stopping" with "running" mid-operation
        if ns.stopping_started_at:
            stopping_duration = datetime.now(timezone.utc) - ns.stopping_started_at
            if stopping_duration.total_seconds() < 360:  # 6 minutes
                continue  # Don't overwrite transitional state

        if ns.starting_started_at:
            starting_duration = datetime.now(timezone.utc) - ns.starting_started_at
            if starting_duration.total_seconds() < 360:  # 6 minutes
                continue  # Don't overwrite transitional state

        # Also skip if actual_state is transitional (backup for timestamp edge cases)
        if ns.actual_state in ("stopping", "starting", "pending"):
            continue

        container_status = container_status_map.get(ns.node_name)
        if container_status:
            if container_status == "running":
                ns.actual_state = "running"
                ns.stopping_started_at = None  # Clear if recovering
                ns.starting_started_at = None
                ns.error_message = None
                if not ns.boot_started_at:
                    ns.boot_started_at = datetime.now(timezone.utc)
            elif container_status in ("stopped", "exited"):
                ns.actual_state = "stopped"
                ns.stopping_started_at = None  # Clear if recovering
                ns.starting_started_at = None
                ns.error_message = None
                ns.boot_started_at = None
        else:
            # Container not found - only update if the relevant agent was queried
            expected_agent = node_expected_agent.get(ns.node_name)
            (
                expected_agent in agents_successfully_queried
                if expected_agent
                else len(agents_successfully_queried) > 0
            )
            # If agent was queried but container not found, preserve existing state
            # (don't mark as undeployed - that's reconciliation's job)

    database.commit()

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


@router.post("/labs/{lab_id}/nodes/{node_id}/reconcile")
async def reconcile_node(
    lab_id: str,
    node_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.ReconcileResponse:
    """Trigger a reconcile job for a single node.

    This will bring the node's actual state in line with its desired state.
    If the node is already in the correct state, no job is created.
    """
    from app.tasks.jobs import run_node_reconcile
    from app.utils.lab import get_lab_provider, get_node_provider

    lab = _require_lab_editor(lab_id, database, current_user)
    _ensure_node_states_exist(database, lab.id)

    # Get or create the node state with correct naming
    _get_or_create_node_state(database, lab.id, node_id, initial_desired_state="running")

    # Check if node needs reconciliation
    out_of_sync = _get_out_of_sync_nodes(database, lab_id, [node_id])
    if not out_of_sync:
        return schemas.ReconcileResponse(
            job_id="",
            message="Node is already in correct state",
            nodes_to_reconcile=[],
        )

    # Determine provider based on node's image type
    db_node = database.query(models.Node).filter(
        models.Node.lab_id == lab.id,
        models.Node.gui_id == node_id
    ).first()
    if db_node:
        node_provider = get_node_provider(db_node, database)
    else:
        node_provider = get_lab_provider(lab)

    # Get agent for this node
    agent = await get_online_agent_for_lab(database, lab, required_provider=node_provider)
    if not agent:
        raise_unavailable(f"No healthy agent available with {node_provider} support")

    # Note: Don't set state to pending here - let the task handle state transitions
    # after it reads the current state to determine what action is needed

    # Create reconcile job
    job = models.Job(
        lab_id=lab.id,
        user_id=current_user.id,
        action=f"reconcile:node:{node_id}",
        status="queued",
    )
    database.add(job)
    database.commit()
    database.refresh(job)

    # Start background reconcile task
    safe_create_task(
        run_node_reconcile(job.id, lab.id, [node_id], provider=node_provider),
        name=f"reconcile:node:{job.id}"
    )

    return schemas.ReconcileResponse(
        job_id=job.id,
        message="Reconcile job queued",
        nodes_to_reconcile=[node_id],
    )


@router.post("/labs/{lab_id}/reconcile")
async def reconcile_lab(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.ReconcileResponse:
    """Trigger a reconcile job for all out-of-state nodes in a lab.

    This will bring all nodes' actual states in line with their desired states.
    If all nodes are already in the correct state, no job is created.
    """
    from app.tasks.jobs import run_node_reconcile
    from app.utils.lab import get_lab_provider

    lab = _require_lab_editor(lab_id, database, current_user)

    # Check for conflicting jobs before proceeding
    has_conflict, conflicting_action = has_conflicting_job(lab_id, "reconcile")
    if has_conflict:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot reconcile lab: '{conflicting_action}' operation already in progress"
        )
    _ensure_node_states_exist(database, lab.id)

    # Find all nodes needing reconciliation
    out_of_sync = _get_out_of_sync_nodes(database, lab_id)
    if not out_of_sync:
        return schemas.ReconcileResponse(
            job_id="",
            message="All nodes are already in correct state",
            nodes_to_reconcile=[],
        )

    node_ids = [s.node_id for s in out_of_sync]

    # Get agent for this lab
    lab_provider = get_lab_provider(lab)
    agent = await get_online_agent_for_lab(database, lab, required_provider=lab_provider)
    if not agent:
        raise_unavailable(f"No healthy agent available with {lab_provider} support")

    # Note: Don't set states to pending here - let the task handle state transitions
    # after it reads the current states to determine what actions are needed

    # Create reconcile job
    job = models.Job(
        lab_id=lab.id,
        user_id=current_user.id,
        action=f"reconcile:lab:{','.join(node_ids)}",
        status="queued",
    )
    database.add(job)
    database.commit()
    database.refresh(job)

    # Start background reconcile task
    safe_create_task(
        run_node_reconcile(job.id, lab.id, node_ids, provider=lab_provider),
        name=f"reconcile:lab:{job.id}"
    )

    return schemas.ReconcileResponse(
        job_id=job.id,
        message=f"Reconcile job queued for {len(node_ids)} node(s)",
        nodes_to_reconcile=node_ids,
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
    agent = await get_online_agent_for_lab(database, lab, required_provider=lab_provider)

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
    agent = await get_online_agent_for_lab(database, lab, required_provider=lab_provider)

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
    service = TopologyService(database)
    device_info = {}
    if service.has_nodes(lab.id):
        try:
            graph = service.export_to_graph(lab.id)
            for node in graph.nodes:
                node_name = node.container_name or node.name
                device_info[node_name] = {
                    "device": node.device,
                    "kind": node.device,
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


def _upsert_link_states(
    database: Session,
    lab_id: str,
    graph: schemas.TopologyGraph,
) -> tuple[int, int, list[str], list[dict]]:
    """Create or update LinkState records for all links in a topology graph.

    New links are initialized with desired_state='up', actual_state='unknown'.
    Existing links retain their desired_state (user preference persists).
    Links removed from topology are marked for deletion (caller handles teardown).

    Returns:
        Tuple of (created_count, updated_count, added_link_names, removed_link_info)
        - added_link_names: List of newly created link names
        - removed_link_info: List of dicts with info about removed links for teardown
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
        # Use container_name (YAML key) for consistency
        node_id_to_name[node.id] = node.container_name or node.name

    # Build node name to host_id mapping from database
    # This is used to populate source_host_id/target_host_id on new LinkState records
    db_nodes = (
        database.query(models.Node)
        .filter(models.Node.lab_id == lab_id)
        .all()
    )
    node_name_to_host: dict[str, str | None] = {
        n.container_name: n.host_id for n in db_nodes
    }

    # Also check NodePlacement for nodes without host_id set
    placements = (
        database.query(models.NodePlacement)
        .filter(models.NodePlacement.lab_id == lab_id)
        .all()
    )
    for p in placements:
        if p.node_name not in node_name_to_host or not node_name_to_host.get(p.node_name):
            node_name_to_host[p.node_name] = p.host_id

    # Track which links are in the current topology
    current_link_names: set[str] = set()
    created_count = 0
    updated_count = 0
    added_link_names: list[str] = []
    removed_link_info: list[dict] = []

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
        source_interface = normalize_interface(ep_a.ifname) if ep_a.ifname else "eth0"
        target_interface = normalize_interface(ep_b.ifname) if ep_b.ifname else "eth0"

        # Generate canonical link name
        link_name = generate_link_name(
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

            # Look up host_ids for the endpoints
            src_host_id = node_name_to_host.get(src_n)
            tgt_host_id = node_name_to_host.get(tgt_n)
            is_cross_host = (
                src_host_id is not None
                and tgt_host_id is not None
                and src_host_id != tgt_host_id
            )

            new_state = models.LinkState(
                lab_id=lab_id,
                link_name=link_name,
                source_node=src_n,
                source_interface=src_i,
                target_node=tgt_n,
                target_interface=tgt_i,
                source_host_id=src_host_id,
                target_host_id=tgt_host_id,
                is_cross_host=is_cross_host,
                desired_state="up",
                actual_state="unknown",
            )
            database.add(new_state)
            added_link_names.append(link_name)
            created_count += 1

    # Collect info about links to remove (for teardown) before deleting
    for existing_name, existing_state in existing_by_name.items():
        if existing_name not in current_link_names:
            # Store info needed for teardown before deletion
            removed_link_info.append({
                "link_name": existing_state.link_name,
                "source_node": existing_state.source_node,
                "source_interface": existing_state.source_interface,
                "target_node": existing_state.target_node,
                "target_interface": existing_state.target_interface,
                "is_cross_host": existing_state.is_cross_host,
                "actual_state": existing_state.actual_state,
                "source_host_id": existing_state.source_host_id,
                "target_host_id": existing_state.target_host_id,
                "vni": existing_state.vni,
            })
            # Don't delete here - let the live_links task handle teardown first
            # The task will delete after successful teardown
            # For now, mark as pending deletion but keep the record
            existing_state.desired_state = "deleted"

    return created_count, updated_count, added_link_names, removed_link_info


def _ensure_link_states_exist(
    database: Session,
    lab_id: str,
) -> None:
    """Ensure LinkState records exist for all links in the topology.

    Uses database as source of truth.
    Safe to call multiple times - idempotent operation.
    """
    service = TopologyService(database)
    if service.has_nodes(lab_id):
        graph = service.export_to_graph(lab_id)
        # Ignore the added/removed info - this is just for ensuring records exist
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

    # Sync LinkState records from database topology
    service = TopologyService(database)
    if service.has_nodes(lab.id):
        graph = service.export_to_graph(lab.id)
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
        raise_not_found(f"Link '{link_name}' not found")

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
    lab = _require_lab_editor(lab_id, database, current_user)
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
        raise_not_found(f"Link '{link_name}' not found")

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
    lab = _require_lab_editor(lab_id, database, current_user)
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


@router.post("/labs/{lab_id}/links/refresh")
def refresh_link_states(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.LinkStateRefreshResponse:
    """Refresh link states from the current topology.

    This updates LinkState records to match the current topology.
    New links are created, removed links are deleted.
    Uses database topology as source of truth.
    """
    lab = get_lab_or_404(lab_id, database, current_user)

    service = TopologyService(database)
    if not service.has_nodes(lab.id):
        raise_not_found("Topology not found")
    graph = service.export_to_graph(lab.id)

    created, updated = _upsert_link_states(database, lab.id, graph)
    database.commit()

    return schemas.LinkStateRefreshResponse(
        message="Link states refreshed",
        links_created=created,
        links_updated=updated,
    )


# ============================================================================
# Config Extraction Endpoint
# ============================================================================


def _save_config_to_workspace(workspace: Path, node_name: str, content: str) -> None:
    """Save a config file to the workspace.

    This is a blocking operation meant to run in asyncio.to_thread().
    """
    configs_dir = workspace / "configs"
    configs_dir.mkdir(parents=True, exist_ok=True)
    node_config_dir = configs_dir / node_name
    node_config_dir.mkdir(parents=True, exist_ok=True)
    config_file = node_config_dir / "startup-config"
    config_file.write_text(content, encoding="utf-8")


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

    For multi-host labs, this queries all agents that have nodes for the lab
    and extracts configs from each agent concurrently.

    Args:
        create_snapshot: If True, creates config snapshots after extraction
        snapshot_type: Type of snapshot to create ("manual" or "auto_stop")

    Returns:
        Dict with 'success', 'extracted_count', 'snapshots_created', and optionally 'error' keys
    """
    lab = _require_lab_editor(lab_id, database, current_user)
    lab_provider = get_lab_provider(lab)

    # Get all agents that have nodes for this lab (multi-host support)
    placements = (
        database.query(models.NodePlacement.host_id)
        .filter(models.NodePlacement.lab_id == lab.id)
        .distinct()
        .all()
    )
    host_ids = [p.host_id for p in placements]

    # If no placements exist, fall back to lab's default agent
    if not host_ids:
        agent = await get_online_agent_for_lab(database, lab, required_provider=lab_provider)
        if agent:
            host_ids = [agent.id]

    # Get all healthy agents for these host_ids
    agents = []
    for host_id in host_ids:
        agent = database.get(models.Host, host_id)
        if agent and agent_client.is_agent_online(agent):
            agents.append(agent)

    if not agents:
        raise_unavailable("No healthy agents available")

    # Call all agents concurrently to extract configs
    tasks = [agent_client.extract_configs_on_agent(agent, lab.id) for agent in agents]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Merge configs from all agents
    configs = []
    extracted_count = 0
    errors = []

    for agent, result in zip(agents, results):
        if isinstance(result, Exception):
            errors.append(f"Agent {agent.id}: {result}")
            continue
        if not result.get("success"):
            errors.append(f"Agent {agent.id}: {result.get('error', 'Unknown error')}")
            continue
        extracted_count += result.get("extracted_count", 0)
        configs.extend(result.get("configs", []))

    # If all agents failed, raise error
    if not configs and errors:
        raise HTTPException(
            status_code=500,
            detail=f"Config extraction failed on all agents: {'; '.join(errors)}"
        )
    snapshots_created = 0

    # Build node -> agent mapping from placements for cross-agent sync
    node_to_agent: dict[str, models.Host] = {}
    for placement in database.query(models.NodePlacement).filter(
        models.NodePlacement.lab_id == lab.id
    ).all():
        node_def = database.get(models.Node, placement.node_definition_id)
        agent = database.get(models.Host, placement.host_id)
        if node_def and agent:
            node_to_agent[node_def.container_name] = agent

    # Save configs to API workspace and create snapshots via ConfigService
    if configs:
        from app.services.config_service import ConfigService
        config_svc = ConfigService(database)

        # Build node_name -> device_kind lookup
        lab_nodes = (
            database.query(models.Node)
            .filter(models.Node.lab_id == lab_id)
            .all()
        )
        node_device_map = {n.container_name: n.device for n in lab_nodes}

        for config_data in configs:
            node_name = config_data.get("node_name")
            content = config_data.get("content")
            if not node_name or not content:
                continue

            if create_snapshot:
                device_kind = node_device_map.get(node_name)
                snapshot = config_svc.save_extracted_config(
                    lab_id=lab_id,
                    node_name=node_name,
                    content=content,
                    snapshot_type=snapshot_type,
                    device_kind=device_kind,
                )
                if snapshot:
                    snapshots_created += 1
            else:
                # Even without snapshot, save to workspace and config_json
                config_svc.save_extracted_config(
                    lab_id=lab_id,
                    node_name=node_name,
                    content=content,
                    snapshot_type=snapshot_type,
                    device_kind=node_device_map.get(node_name),
                    set_as_active=False,
                )

        database.commit()

    # Sync configs to all agents to ensure agent workspaces match API workspace
    # This handles cases where configs are modified in API workspace or extracted
    # from a different agent than where the node is currently placed
    sync_errors = []
    for config_data in configs:
        node_name = config_data.get("node_name")
        content = config_data.get("content")
        if not node_name or not content:
            continue

        agent = node_to_agent.get(node_name)
        if agent and agent_client.is_agent_online(agent):
            result = await agent_client.update_config_on_agent(
                agent, lab.id, node_name, content
            )
            if not result.get("success"):
                sync_errors.append(f"{node_name}: {result.get('error', 'unknown')}")

    return {
        "success": True,
        "extracted_count": extracted_count,
        "snapshots_created": snapshots_created,
        "sync_errors": sync_errors if sync_errors else None,
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
    orphaned_only: bool = False,
    device_kind: str | None = None,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.ConfigSnapshotsResponse:
    """List all config snapshots for a lab with orphan and active status.

    Query params:
    - node_name: Filter by node name
    - orphaned_only: Only return snapshots for deleted nodes
    - device_kind: Filter by device type (e.g., "ceos", "srl")

    Returns snapshots with is_orphaned and is_active flags.
    """
    get_lab_or_404(lab_id, database, current_user)

    from app.services.config_service import ConfigService
    config_svc = ConfigService(database)
    results = config_svc.list_configs_with_orphan_status(
        lab_id=lab_id,
        node_name=node_name,
        orphaned_only=orphaned_only,
        device_kind=device_kind,
    )

    return schemas.ConfigSnapshotsResponse(
        snapshots=[schemas.ConfigSnapshotOut(**r) for r in results]
    )


@router.get("/labs/{lab_id}/config-snapshots/{node_name}/list")
def list_node_config_snapshots(
    lab_id: str,
    node_name: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.ConfigSnapshotsResponse:
    """List all config snapshots for a specific node.

    Returns snapshots with is_orphaned and is_active flags,
    ordered by created_at descending (newest first).
    """
    get_lab_or_404(lab_id, database, current_user)

    from app.services.config_service import ConfigService
    config_svc = ConfigService(database)
    results = config_svc.list_configs_with_orphan_status(
        lab_id=lab_id,
        node_name=node_name,
    )

    return schemas.ConfigSnapshotsResponse(
        snapshots=[schemas.ConfigSnapshotOut(**r) for r in results]
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
    lab = _require_lab_editor(lab_id, database, current_user)
    workspace = lab_workspace(lab.id)
    configs_dir = workspace / "configs"

    if not configs_dir.exists():
        raise HTTPException(
            status_code=404,
            detail="No saved configs found. Run 'Extract Configs' first."
        )

    from app.services.config_service import ConfigService
    config_svc = ConfigService(database)

    # Build node_name -> device_kind lookup
    lab_nodes = (
        database.query(models.Node)
        .filter(models.Node.lab_id == lab_id)
        .all()
    )
    node_device_map = {n.container_name: n.device for n in lab_nodes}

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
        current_node_name = node_dir.name

        snapshot = config_svc.save_extracted_config(
            lab_id=lab_id,
            node_name=current_node_name,
            content=content,
            snapshot_type="manual",
            device_kind=node_device_map.get(current_node_name),
            set_as_active=True,
        )
        if snapshot:
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
    _require_lab_editor(lab_id, database, current_user)

    snapshot = (
        database.query(models.ConfigSnapshot)
        .filter(
            models.ConfigSnapshot.id == snapshot_id,
            models.ConfigSnapshot.lab_id == lab_id,
        )
        .first()
    )

    if not snapshot:
        raise_not_found("Snapshot not found")

    database.delete(snapshot)
    database.commit()

    return {"status": "deleted", "snapshot_id": snapshot_id}


@router.delete("/labs/{lab_id}/config-snapshots")
def bulk_delete_config_snapshots(
    lab_id: str,
    node_name: str | None = None,
    orphaned_only: bool = False,
    force: bool = False,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """Bulk delete config snapshots with active config safety guard.

    Query params:
    - node_name: Delete all snapshots for a specific node
    - orphaned_only: Only delete snapshots for nodes no longer in the topology
    - force: Override active config guard (required to delete active startup-configs)

    If any snapshot is the active startup-config for a node and force=False,
    returns 409 with details about which snapshots are active.
    """
    _require_lab_editor(lab_id, database, current_user)

    from app.services.config_service import ConfigService, ActiveConfigGuardError
    config_svc = ConfigService(database)

    try:
        result = config_svc.delete_configs(
            lab_id=lab_id,
            node_name=node_name,
            orphaned_only=orphaned_only,
            force=force,
        )
    except ActiveConfigGuardError as e:
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(e),
                "active_snapshots": e.active_snapshots,
            },
        )

    database.commit()
    return result


@router.post("/labs/{lab_id}/config-snapshots/{snapshot_id}/map")
def map_config_snapshot(
    lab_id: str,
    snapshot_id: str,
    payload: schemas.ConfigMappingRequest,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.ConfigSnapshotOut:
    """Map an orphaned config snapshot to a target node.

    Sets mapped_to_node_id on the snapshot. Validates device_kind
    compatibility (warns on mismatch but does not block).
    """
    _require_lab_editor(lab_id, database, current_user)

    from app.services.config_service import ConfigService
    config_svc = ConfigService(database)

    try:
        snapshot = config_svc.map_config(
            snapshot_id=snapshot_id,
            target_node_id=payload.target_node_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    database.commit()
    database.refresh(snapshot)

    # Get orphan/active status for response
    results = config_svc.list_configs_with_orphan_status(
        lab_id=lab_id,
        node_name=snapshot.node_name,
    )
    match = next((r for r in results if r["id"] == snapshot.id), None)
    if match:
        return schemas.ConfigSnapshotOut(**match)
    return schemas.ConfigSnapshotOut.model_validate(snapshot)


@router.put("/labs/{lab_id}/nodes/{node_name}/active-config")
async def set_active_config(
    lab_id: str,
    node_name: str,
    payload: schemas.SetActiveConfigRequest,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """Set a specific config snapshot as the active startup-config for a node.

    Updates the node's active_config_snapshot_id, syncs content to
    config_json["startup-config"], and pushes to the agent workspace.
    """
    _require_lab_editor(lab_id, database, current_user)

    node = (
        database.query(models.Node)
        .filter(
            models.Node.lab_id == lab_id,
            models.Node.container_name == node_name,
        )
        .first()
    )
    if not node:
        raise_not_found(f"Node '{node_name}' not found")

    from app.services.config_service import ConfigService
    config_svc = ConfigService(database)

    try:
        config_svc.set_active_config(node.id, payload.snapshot_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    database.commit()

    # Push to agent
    placement = (
        database.query(models.NodePlacement)
        .filter(
            models.NodePlacement.lab_id == lab_id,
            models.NodePlacement.node_definition_id == node.id,
        )
        .first()
    )
    if placement:
        agent = database.get(models.Host, placement.host_id)
        if agent and agent_client.is_agent_online(agent):
            snapshot = database.get(models.ConfigSnapshot, payload.snapshot_id)
            if snapshot:
                await agent_client.update_config_on_agent(
                    agent, lab_id, node_name, snapshot.content
                )

    return {
        "success": True,
        "node_name": node_name,
        "active_config_snapshot_id": payload.snapshot_id,
        "message": f"Active config set for '{node_name}'. Reload node to apply.",
    }


@router.get("/labs/{lab_id}/config-snapshots/download")
def download_config_snapshots(
    lab_id: str,
    node_name: list[str] | None = Query(None),
    include_orphaned: bool = False,
    all: bool = False,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Download config snapshots as a zip file.

    Query params:
    - node_name: Specific node(s) to include (repeatable)
    - include_orphaned: Include configs for deleted nodes
    - all: Include everything

    Returns a zip file with configs organized by node and timestamp.
    """
    lab = get_lab_or_404(lab_id, database, current_user)

    from app.services.config_service import ConfigService
    config_svc = ConfigService(database)

    try:
        zip_buf = config_svc.build_download_zip(
            lab_id=lab_id,
            node_names=node_name,
            include_orphaned=include_orphaned,
            all_configs=all,
        )
    except ValueError as e:
        status = 413 if "limit" in str(e).lower() else 404
        raise HTTPException(status_code=status, detail=str(e))

    lab_name = lab.name.replace(" ", "_") if lab.name else lab_id
    filename = f"{lab_name}_configs.zip"

    from starlette.responses import StreamingResponse
    return StreamingResponse(
        zip_buf,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/labs/{lab_id}/config-snapshots/orphaned")
def list_orphaned_configs(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """List orphaned config snapshots grouped by device kind.

    Returns stranded configs (from deleted nodes) organized by device type
    for the config mapping UI.
    """
    get_lab_or_404(lab_id, database, current_user)

    from app.services.config_service import ConfigService
    config_svc = ConfigService(database)
    grouped = config_svc.get_orphaned_configs(lab_id)

    # Convert to serializable format
    result = {}
    for kind, configs in grouped.items():
        result[kind] = [
            schemas.ConfigSnapshotOut(**c).model_dump(mode="json")
            for c in configs
        ]

    return {"orphaned_configs": result, "total_count": sum(len(v) for v in grouped.values())}


# ============================================================================
# Hot-Connect Link Management Endpoints
# ============================================================================


class HotConnectRequest(BaseModel):
    """Request to hot-connect two interfaces."""
    source_node: str
    source_interface: str
    target_node: str
    target_interface: str


class HotConnectResponse(BaseModel):
    """Response from hot-connect request."""
    success: bool
    link_id: str | None = None
    vlan_tag: int | None = None
    error: str | None = None


class ExternalConnectRequest(BaseModel):
    """Request to connect a node to an external network."""
    node_name: str
    interface_name: str
    external_interface: str
    vlan_tag: int | None = None


class ExternalConnectResponse(BaseModel):
    """Response from external connect request."""
    success: bool
    vlan_tag: int | None = None
    error: str | None = None



@router.post("/labs/{lab_id}/hot-connect")
async def hot_connect_link(
    lab_id: str,
    request: HotConnectRequest,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> HotConnectResponse:
    """Hot-connect two interfaces in a running lab.

    This creates a Layer 2 link between two container interfaces without
    restarting any nodes. The link is established by assigning both interfaces
    the same VLAN tag on the OVS bridge.

    Requirements:
    - Lab must be deployed (running state)
    - Both nodes must be running
    - Interfaces must be pre-provisioned via OVS
    """
    lab = _require_lab_editor(lab_id, database, current_user)

    # Verify lab is running
    if lab.state not in ("running", "starting"):
        raise HTTPException(
            status_code=400,
            detail=f"Lab must be running for hot-connect (current state: {lab.state})"
        )

    link_name = generate_link_name(
        request.source_node, request.source_interface,
        request.target_node, request.target_interface,
    )
    link_state = (
        database.query(models.LinkState)
        .filter(
            models.LinkState.lab_id == lab_id,
            models.LinkState.link_name == link_name,
        )
        .first()
    )
    if not link_state:
        if f"{request.source_node}:{request.source_interface}" <= f"{request.target_node}:{request.target_interface}":
            src_n, src_i = request.source_node, request.source_interface
            tgt_n, tgt_i = request.target_node, request.target_interface
        else:
            src_n, src_i = request.target_node, request.target_interface
            tgt_n, tgt_i = request.source_node, request.source_interface

        # Look up host_ids for the endpoints
        src_node = (
            database.query(models.Node)
            .filter(models.Node.lab_id == lab_id, models.Node.container_name == src_n)
            .first()
        )
        tgt_node = (
            database.query(models.Node)
            .filter(models.Node.lab_id == lab_id, models.Node.container_name == tgt_n)
            .first()
        )
        src_host_id = src_node.host_id if src_node else None
        tgt_host_id = tgt_node.host_id if tgt_node else None

        # Fall back to NodePlacement if host_id not set on node
        if not src_host_id:
            placement = (
                database.query(models.NodePlacement)
                .filter(models.NodePlacement.lab_id == lab_id, models.NodePlacement.node_name == src_n)
                .first()
            )
            if placement:
                src_host_id = placement.host_id
        if not tgt_host_id:
            placement = (
                database.query(models.NodePlacement)
                .filter(models.NodePlacement.lab_id == lab_id, models.NodePlacement.node_name == tgt_n)
                .first()
            )
            if placement:
                tgt_host_id = placement.host_id

        is_cross_host = (
            src_host_id is not None
            and tgt_host_id is not None
            and src_host_id != tgt_host_id
        )

        link_state = models.LinkState(
            lab_id=lab_id,
            link_name=link_name,
            source_node=src_n,
            source_interface=src_i,
            target_node=tgt_n,
            target_interface=tgt_i,
            source_host_id=src_host_id,
            target_host_id=tgt_host_id,
            is_cross_host=is_cross_host,
            desired_state="up",
            actual_state="unknown",
        )
        database.add(link_state)
        database.flush()

    host_to_agent = await _build_host_to_agent_map(database, lab_id)
    if not host_to_agent:
        raise_unavailable("No healthy agent available")

    success = await create_link_if_ready(database, lab_id, link_state, host_to_agent)
    database.commit()

    if success:
        return HotConnectResponse(
            success=True,
            link_id=link_state.link_name,
            vlan_tag=link_state.vlan_tag,
        )

    if link_state.actual_state == "pending":
        return HotConnectResponse(
            success=False,
            error="Link pending - waiting for nodes to be running",
        )

    return HotConnectResponse(
        success=False,
        error=link_state.error_message or "Link creation failed",
    )


@router.delete("/labs/{lab_id}/hot-disconnect/{link_id:path}")
async def hot_disconnect_link(
    lab_id: str,
    link_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> HotConnectResponse:
    """Hot-disconnect a link in a running lab.

    This breaks a Layer 2 link between two container interfaces without
    restarting any nodes. The link is broken by assigning each interface
    a separate VLAN tag.

    Args:
        lab_id: Lab identifier
        link_id: Link identifier (format: "node1:iface1-node2:iface2")
    """
    _require_lab_editor(lab_id, database, current_user)

    link_states = (
        database.query(models.LinkState)
        .filter(models.LinkState.lab_id == lab_id)
        .all()
    )
    link_state = None
    for ls in link_states:
        if link_id == f"{ls.source_node}:{ls.source_interface}-{ls.target_node}:{ls.target_interface}" or \
           link_id == f"{ls.target_node}:{ls.target_interface}-{ls.source_node}:{ls.source_interface}":
            link_state = ls
            break

    if not link_state:
        return HotConnectResponse(success=False, error=f"Link '{link_id}' not found")

    host_to_agent = await _build_host_to_agent_map(database, lab_id)
    if not host_to_agent:
        raise_unavailable("No healthy agent available")

    link_info = {
        "link_name": link_state.link_name,
        "source_node": link_state.source_node,
        "source_interface": link_state.source_interface,
        "target_node": link_state.target_node,
        "target_interface": link_state.target_interface,
        "is_cross_host": link_state.is_cross_host,
        "actual_state": link_state.actual_state,
        "source_host_id": link_state.source_host_id,
        "target_host_id": link_state.target_host_id,
        "vni": link_state.vni,
    }
    success = await teardown_link(database, lab_id, link_info, host_to_agent)
    database.commit()

    if success:
        return HotConnectResponse(success=True, link_id=link_id)

    return HotConnectResponse(
        success=False,
        error="Failed to disconnect link",
    )


@router.get("/labs/{lab_id}/live-links")
async def list_live_links(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """List all active OVS links for a running lab.

    This queries the agent for the current state of all OVS-managed links,
    including their VLAN tags and connection state.
    """
    lab = get_lab_or_404(lab_id, database, current_user)

    # Get agent for this lab
    lab_provider = get_lab_provider(lab)
    agent = await get_online_agent_for_lab(database, lab, required_provider=lab_provider)
    if not agent:
        return {"links": [], "error": "No healthy agent available"}

    # Forward to agent
    result = await agent_client.list_links_on_agent(agent, lab.id)
    return result


@router.post("/labs/{lab_id}/external/connect")
async def connect_to_external_network(
    lab_id: str,
    request: ExternalConnectRequest,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> ExternalConnectResponse:
    """Connect a node interface to an external network.

    This establishes connectivity between a container interface and an
    external host interface (e.g., for internet access, management network,
    or physical lab equipment).

    Requirements:
    - Lab must be deployed (running state)
    - Node must be running
    - External interface must exist on the host
    """
    lab = _require_lab_editor(lab_id, database, current_user)

    # Verify lab is running
    if lab.state not in ("running", "starting"):
        raise HTTPException(
            status_code=400,
            detail=f"Lab must be running for external connect (current state: {lab.state})"
        )

    # Get agent for this lab
    lab_provider = get_lab_provider(lab)
    agent = await get_online_agent_for_lab(database, lab, required_provider=lab_provider)
    if not agent:
        raise_unavailable("No healthy agent available")

    # Forward to agent
    result = await agent_client.connect_external_on_agent(
        agent=agent,
        lab_id=lab.id,
        node_name=request.node_name,
        interface_name=request.interface_name,
        external_interface=request.external_interface,
        vlan_tag=request.vlan_tag,
    )

    return ExternalConnectResponse(
        success=result.get("success", False),
        vlan_tag=result.get("vlan_tag"),
        error=result.get("error"),
    )


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

    get_lab_or_404(lab_id, database, current_user)

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
        raise_not_found(f"Snapshot A not found: {payload.snapshot_id_a}")

    if not snapshot_b:
        raise_not_found(f"Snapshot B not found: {payload.snapshot_id_b}")

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


# ============================================================================
# Lab Logs Endpoints
# ============================================================================


@router.get("/labs/{lab_id}/logs")
def get_lab_logs(
    lab_id: str,
    job_id: str | None = None,
    host_id: str | None = None,
    level: str | None = None,
    since: str | None = None,
    search: str | None = None,
    limit: int = 500,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.LabLogsResponse:
    """Get aggregated logs for a lab.

    Combines job logs from all jobs in this lab, parsed with host sections.
    Optionally includes system logs from Loki if configured and user is admin.

    Query parameters:
    - job_id: Filter to logs from a specific job
    - host_id: Filter to logs from a specific host
    - level: Filter by minimum log level (info, warning, error)
    - since: Time filter (e.g., "15m", "1h", "24h")
    - search: Text search in log messages
    - limit: Maximum number of entries to return (default 500)

    Returns structured log entries with host associations and summary info.
    """
    from app.services.log_parser import parse_job_log, filter_entries
    from app.utils.logs import get_log_content

    get_lab_or_404(lab_id, database, current_user)

    # Parse 'since' parameter
    from app.utils.time_range import parse_relative_duration
    since_dt = None
    duration = parse_relative_duration(since, allowed={"15m", "1h", "24h"})
    if duration:
        since_dt = datetime.now(timezone.utc) - duration

    # Query jobs for this lab
    jobs_query = (
        database.query(models.Job)
        .filter(models.Job.lab_id == lab_id)
        .order_by(models.Job.created_at.desc())
    )

    if job_id:
        jobs_query = jobs_query.filter(models.Job.id == job_id)

    # Limit to recent jobs for performance
    jobs = jobs_query.limit(50).all()

    # Build agent_id -> host_name lookup from all jobs
    agent_ids = {job.agent_id for job in jobs if job.agent_id}
    agent_name_map: dict[str, str] = {}
    if agent_ids:
        hosts = (
            database.query(models.Host)
            .filter(models.Host.id.in_(agent_ids))
            .all()
        )
        agent_name_map = {h.id: h.name for h in hosts}

    # Parse all job logs
    all_entries = []
    hosts_found = set()

    for job in jobs:
        log_content = get_log_content(job.log_path)
        if not log_content:
            continue

        # Get host info for this job from its agent_id
        job_host_id = job.agent_id
        job_host_name = agent_name_map.get(job.agent_id) if job.agent_id else None

        parsed = parse_job_log(
            log_content=log_content,
            job_id=job.id,
            job_created_at=job.created_at,
        )

        # If job has an agent, set host info on entries that don't have it
        # and add the host to hosts_found
        for entry in parsed.entries:
            if not entry.host_id and job_host_id:
                entry.host_id = job_host_id
                entry.host_name = job_host_name
            if entry.host_name:
                hosts_found.add(entry.host_name)

        all_entries.extend(parsed.entries)
        hosts_found.update(parsed.hosts)

        # Also add job's host to found hosts
        if job_host_name:
            hosts_found.add(job_host_name)

    # Apply filters
    filtered_entries = filter_entries(
        all_entries,
        host_id=host_id,
        level=level,
        search=search,
        since=since_dt,
    )

    # Sort by timestamp (newest first for display, but API returns oldest first for streaming)
    filtered_entries.sort(key=lambda e: e.timestamp)

    # Apply limit
    has_more = len(filtered_entries) > limit
    limited_entries = filtered_entries[:limit]

    # Count errors
    error_count = sum(1 for e in limited_entries if e.level == "error")

    # Build job summaries for filtering UI
    job_summaries = [
        schemas.LabLogJob(
            id=job.id,
            action=job.action,
            status=job.status,
            created_at=job.created_at,
            completed_at=job.completed_at,
        )
        for job in jobs
    ]

    # Convert entries to response schema
    response_entries = [
        schemas.LabLogEntry(
            timestamp=e.timestamp,
            level=e.level,
            message=e.message,
            host_id=e.host_id,
            host_name=e.host_name,
            job_id=e.job_id,
            source=e.source,
        )
        for e in limited_entries
    ]

    return schemas.LabLogsResponse(
        entries=response_entries,
        jobs=job_summaries,
        hosts=list(hosts_found),
        total_count=len(filtered_entries),
        error_count=error_count,
        has_more=has_more,
    )


class CleanupOrphansResponse(BaseModel):
    """Response from orphan cleanup."""
    removed_by_agent: dict[str, list[str]] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)


@router.post("/labs/{lab_id}/cleanup-orphans", response_model=CleanupOrphansResponse)
async def cleanup_lab_orphans(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> CleanupOrphansResponse:
    """Clean up orphaned containers for a lab across all agents.

    Removes containers for nodes that are no longer assigned to a given agent.
    This happens when nodes are migrated between agents.

    Args:
        lab_id: Lab identifier

    Returns:
        Dict mapping agent names to lists of removed containers
    """
    lab, role = get_lab_with_role(lab_id, database, current_user)
    if role < LabRole.OWNER:
        raise HTTPException(status_code=403, detail="Owner access required")

    # Get all node placements for this lab
    placements = (
        database.query(models.NodePlacement)
        .filter(models.NodePlacement.lab_id == lab_id)
        .all()
    )

    # Build a mapping of host_id -> list of node_names
    nodes_by_host: dict[str, list[str]] = {}
    for placement in placements:
        if placement.host_id not in nodes_by_host:
            nodes_by_host[placement.host_id] = []
        nodes_by_host[placement.host_id].append(placement.node_name)

    # Get all online agents
    agents = (
        database.query(models.Host)
        .filter(models.Host.status == "online")
        .all()
    )

    removed_by_agent: dict[str, list[str]] = {}
    errors: list[str] = []

    # Call each agent to clean up orphans
    for agent in agents:
        # Get the list of nodes that SHOULD be on this agent
        keep_nodes = nodes_by_host.get(agent.id, [])

        try:
            result = await agent_client.cleanup_lab_orphans(
                agent, lab_id, keep_nodes
            )
            if result.get("removed_containers"):
                removed_by_agent[agent.name] = result["removed_containers"]
                logger.info(
                    f"Cleaned up {len(result['removed_containers'])} orphan containers on {agent.name}"
                )
            if result.get("errors"):
                errors.extend(result["errors"])
        except Exception as e:
            error_msg = f"Failed to cleanup orphans on {agent.name}: {e}"
            logger.warning(error_msg)
            errors.append(error_msg)

    return CleanupOrphansResponse(
        removed_by_agent=removed_by_agent,
        errors=errors,
    )


# =============================================================================
# Interface Mapping Endpoints
# =============================================================================


@router.get("/labs/{lab_id}/interface-mappings")
async def get_lab_interface_mappings(
    lab_id: str,
    database: Session = Depends(db.get_session),
    current_user: models.User = Depends(get_current_user),
) -> schemas.InterfaceMappingsResponse:
    """Get all interface mappings for a lab.

    Returns OVS port, Linux interface, and vendor interface mappings
    for all interfaces in the lab.
    """
    get_lab_or_404(lab_id, database, current_user)

    mappings = (
        database.query(models.InterfaceMapping)
        .filter(models.InterfaceMapping.lab_id == lab_id)
        .all()
    )

    return schemas.InterfaceMappingsResponse(
        mappings=[schemas.InterfaceMappingOut.model_validate(m) for m in mappings],
        total=len(mappings),
    )


@router.get("/labs/{lab_id}/nodes/{node_id}/interfaces")
async def get_node_interfaces(
    lab_id: str,
    node_id: str,
    database: Session = Depends(db.get_session),
    current_user: models.User = Depends(get_current_user),
) -> schemas.InterfaceMappingsResponse:
    """Get interface mappings for a specific node.

    Returns OVS port, Linux interface, and vendor interface mappings
    for all interfaces on the specified node.
    """
    get_lab_or_404(lab_id, database, current_user)

    # Get the Node definition
    node = (
        database.query(models.Node)
        .filter(models.Node.lab_id == lab_id, models.Node.gui_id == node_id)
        .first()
    )
    if not node:
        raise_not_found("Node not found")

    mappings = (
        database.query(models.InterfaceMapping)
        .filter(
            models.InterfaceMapping.lab_id == lab_id,
            models.InterfaceMapping.node_id == node.id,
        )
        .all()
    )

    return schemas.InterfaceMappingsResponse(
        mappings=[schemas.InterfaceMappingOut.model_validate(m) for m in mappings],
        total=len(mappings),
    )


class InterfaceMappingSyncResponse(BaseModel):
    """Response from syncing interface mappings."""
    created: int
    updated: int
    errors: int
    agents_queried: int


@router.post("/labs/{lab_id}/interface-mappings/sync")
async def sync_interface_mappings(
    lab_id: str,
    database: Session = Depends(db.get_session),
    current_user: models.User = Depends(get_current_user),
) -> InterfaceMappingSyncResponse:
    """Sync interface mappings from all agents for a lab.

    Fetches OVS port information from agents and updates the
    interface_mappings table with current state.
    """
    get_lab_or_404(lab_id, database, current_user)

    result = await interface_mapping_service.populate_all_agents(database, lab_id)

    return InterfaceMappingSyncResponse(
        created=result["created"],
        updated=result["updated"],
        errors=result["errors"],
        agents_queried=result["agents_queried"],
    )


class LinkReconciliationResponse(BaseModel):
    """Response from link reconciliation."""
    checked: int
    valid: int
    repaired: int
    errors: int
    skipped: int


@router.post("/labs/{lab_id}/links/reconcile")
async def reconcile_links(
    lab_id: str,
    database: Session = Depends(db.get_session),
    current_user: models.User = Depends(get_current_user),
) -> LinkReconciliationResponse:
    """Reconcile link states for a lab.

    Verifies all links marked as "up" have matching VLAN tags on both
    endpoints. Attempts to repair any mismatched links.
    """
    _require_lab_editor(lab_id, database, current_user)

    result = await reconcile_lab_links(database, lab_id)

    return LinkReconciliationResponse(
        checked=result["checked"],
        valid=result["valid"],
        repaired=result["repaired"],
        errors=result["errors"],
        skipped=result["skipped"],
    )
