"""Lab CRUD and topology management endpoints."""
from __future__ import annotations

import asyncio
import io
import json
import logging
import shutil
import zipfile
from datetime import datetime, timezone
from typing import Literal

import yaml
from fastapi import APIRouter, Depends, HTTPException

from sqlalchemy import func
from sqlalchemy.orm import Session

from app import agent_client, db, models, schemas
from app.config import settings
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
from app.utils.lab import get_lab_or_404, get_lab_with_role, get_lab_provider, update_lab_provider_from_nodes, require_lab_editor
from app.utils.agents import get_online_agent_for_lab
from app.utils.nodes import get_node_placement_mapping
from app.utils.http import require_lab_owner, raise_not_found, raise_unavailable
from app.utils.link import generate_link_name
from app.services.interface_naming import normalize_interface
from app.services.link_operational_state import recompute_link_oper_state
from app.services.link_reservations import (
    get_conflicting_link_details,
    sync_link_endpoint_reservations,
)
from app.jobs import has_conflicting_job as _has_conflicting_job
from app.tasks.live_links import create_link_if_ready, _build_host_to_agent_map, teardown_link
from app.tasks.link_reconciliation import reconcile_lab_links
from app.services import interface_mapping as interface_mapping_service
from app.utils.async_tasks import safe_create_task
from app.events.publisher import emit_lab_deleted, emit_link_removed, emit_node_removed
from app.state import (
    HostStatus,
    JobStatus,
    LabState,
    LinkActualState,
    LinkDesiredState,
    NodeActualState,
    NodeDesiredState,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["labs"])


def has_conflicting_job(*args, **kwargs):
    """Backward-compatible export for tests monkeypatching app.routers.labs."""
    return _has_conflicting_job(*args, **kwargs)


def _zip_safe_name(value: str) -> str:
    """Prevent zip path traversal and invalid separators."""
    return (value or "unknown").replace("/", "_").replace("\\", "_")


def _enrich_node_state(state: models.NodeState) -> schemas.NodeStateOut:
    """Convert a NodeState model to schema with all_ips parsed from JSON."""
    node_data = schemas.NodeStateOut.model_validate(state)
    if state.management_ips_json:
        try:
            node_data.all_ips = json.loads(state.management_ips_json)
        except (json.JSONDecodeError, TypeError):
            node_data.all_ips = []
    # Compute will_retry: error state with retries remaining and not permanently failed
    node_data.will_retry = (
        state.actual_state == NodeActualState.ERROR
        and state.desired_state == NodeDesiredState.RUNNING
        and state.enforcement_attempts < settings.state_enforcement_max_retries
        and state.enforcement_failed_at is None
    )
    return node_data


def _get_or_create_node_state(
    database: Session,
    lab_id: str,
    node_id: str,
    initial_desired_state: str = NodeDesiredState.STOPPED,
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
        actual_state=NodeActualState.UNDEPLOYED,
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
        status=JobStatus.QUEUED,
    )
    database.add(job)
    database.commit()
    database.refresh(job)

    safe_create_task(
        run_node_reconcile(job.id, lab.id, [node_id], provider=provider),
        name=f"sync:node:{job.id}"
    )


def _converge_stopped_error_state(state: models.NodeState) -> bool:
    """Force convergence when desired_state=stopped but actual_state=error."""
    if state.desired_state == NodeDesiredState.STOPPED and state.actual_state == NodeActualState.ERROR:
        state.actual_state = NodeActualState.STOPPED
        state.image_sync_status = None
        state.image_sync_message = None
        state.starting_started_at = None
        state.stopping_started_at = None
        state.boot_started_at = None
        state.is_ready = False
        state.reset_enforcement(clear_error=True)
        return True
    return False


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
    # Secondary index by node_name to catch GUI ID changes (prevents duplicates)
    existing_by_name = {ns.node_name: ns for ns in existing_states}

    added_node_ids: list[str] = []
    removed_node_info: list[dict] = []
    # Track old node_ids that were reused via name-match (skip in removal pass)
    reused_old_ids: set[str] = set()

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
        elif container_name in existing_by_name:
            # Node exists by name but GUI ID changed — reuse the existing state
            # to prevent duplicate node_states for the same container
            existing_state = existing_by_name[container_name]
            old_id = existing_state.node_id
            existing_state.node_id = node.id
            reused_old_ids.add(old_id)
        else:
            # Create new with defaults - node_name is set only once at creation
            new_state = models.NodeState(
                lab_id=lab_id,
                node_id=node.id,
                node_name=container_name,
                desired_state=NodeDesiredState.STOPPED,
                actual_state=NodeActualState.UNDEPLOYED,
            )
            database.add(new_state)
            added_node_ids.append(node.id)

    # Collect info about nodes being removed (for teardown)
    for existing_node_id, existing_state in existing_by_node_id.items():
        if existing_node_id in reused_old_ids:
            continue  # This state was reused with a new node_id
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

    if not labs:
        return {"labs": []}

    lab_ids = [lab.id for lab in labs]

    # Count total device nodes per lab (exclude external nodes)
    node_counts = dict(
        database.query(models.Node.lab_id, func.count(models.Node.id))
        .filter(models.Node.lab_id.in_(lab_ids), models.Node.node_type == "device")
        .group_by(models.Node.lab_id)
        .all()
    )

    # Count running nodes per lab from node_states
    running_counts = dict(
        database.query(models.NodeState.lab_id, func.count(models.NodeState.id))
        .filter(models.NodeState.lab_id.in_(lab_ids), models.NodeState.actual_state == NodeActualState.RUNNING)
        .group_by(models.NodeState.lab_id)
        .all()
    )

    # Determine VM vs container by device type using vendor registry
    from agent.vendors import _get_config_by_kind

    device_nodes = (
        database.query(models.Node.lab_id, models.Node.device)
        .filter(models.Node.lab_id.in_(lab_ids), models.Node.node_type == "device")
        .all()
    )
    vm_counts: dict[str, int] = {}
    for nlab_id, device in device_nodes:
        if device:
            config = _get_config_by_kind(device)
            if config and "qcow2" in (config.supported_image_kinds or []):
                vm_counts[nlab_id] = vm_counts.get(nlab_id, 0) + 1

    result = []
    for lab in labs:
        lab_out = schemas.LabOut.model_validate(lab)
        total = node_counts.get(lab.id, 0)
        vms = vm_counts.get(lab.id, 0)
        lab_out.node_count = total
        lab_out.running_count = running_counts.get(lab.id, 0)
        lab_out.vm_count = vms
        lab_out.container_count = total - vms
        result.append(lab_out)

    return {"labs": result}


def _populate_lab_counts(database: Session, lab_out: schemas.LabOut) -> None:
    """Populate node_count, running_count, container_count, vm_count for a single lab."""
    from agent.vendors import _get_config_by_kind

    total = (
        database.query(func.count(models.Node.id))
        .filter(models.Node.lab_id == lab_out.id, models.Node.node_type == "device")
        .scalar()
    ) or 0
    running = (
        database.query(func.count(models.NodeState.id))
        .filter(models.NodeState.lab_id == lab_out.id, models.NodeState.actual_state == NodeActualState.RUNNING)
        .scalar()
    ) or 0
    device_types = (
        database.query(models.Node.device)
        .filter(models.Node.lab_id == lab_out.id, models.Node.node_type == "device")
        .all()
    )
    vms = 0
    for (device,) in device_types:
        if device:
            config = _get_config_by_kind(device)
            if config and "qcow2" in (config.supported_image_kinds or []):
                vms += 1
    lab_out.node_count = total
    lab_out.running_count = running
    lab_out.vm_count = vms
    lab_out.container_count = total - vms


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
    _populate_lab_counts(database, out)
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
    if lab.state in (LabState.RUNNING, LabState.STARTING, LabState.STOPPING):
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
            status=JobStatus.QUEUED,
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

    # Delete workspace files (blocking filesystem walk in thread)
    workspace = lab_workspace(lab.id)

    def _sync_delete_workspace():
        import shutil
        if workspace.exists():
            shutil.rmtree(workspace, ignore_errors=True)

    await asyncio.to_thread(_sync_delete_workspace)

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
    lab = require_lab_editor(lab_id, database, current_user)
    workspace = lab_workspace(lab.id)
    await asyncio.to_thread(workspace.mkdir, parents=True, exist_ok=True)

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
    lab = require_lab_editor(lab_id, database, current_user)
    workspace = lab_workspace(lab.id)
    await asyncio.to_thread(workspace.mkdir, parents=True, exist_ok=True)

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


@router.post("/labs/{lab_id}/check-resources")
def check_resources(
    lab_id: str,
    payload: schemas.CheckResourcesRequest | None = None,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.CheckResourcesResponse:
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
        return schemas.CheckResourcesResponse()

    results = check_multihost_capacity(host_device_map, database)

    response = schemas.CheckResourcesResponse()
    all_warnings = []
    all_errors = []

    for host_id, result in results.items():
        per_host = schemas.PerHostCapacity(
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
    """Download a full lab bundle zip with topology, layout, and configs.

    Includes:
    - Topology definition (YAML + JSON graph)
    - Canvas layout
    - Current active startup-config for every node (from config_json,
      active snapshot, workspace filesystem, or auto-generated)
    - All historical config snapshots
    - Orphaned configs (from deleted nodes)
    """
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

    # Resolve current active startup-config for every node.
    # Reuse already-loaded snapshots to avoid N+1 DB queries.
    nodes = (
        database.query(models.Node)
        .filter(models.Node.lab_id == lab.id)
        .all()
    )
    snapshots_by_id = {s["id"]: s for s in snapshots}
    # Build latest-snapshot-per-node index (snapshots ordered by created_at desc)
    latest_snap_by_node: dict[str, dict] = {}
    for s in snapshots:
        nn = s.get("node_name")
        if nn and nn not in latest_snap_by_node:
            latest_snap_by_node[nn] = s

    active_configs: dict[str, str] = {}  # container_name -> config content
    workspace = lab_workspace(lab.id)
    for node in nodes:
        config_content = None

        # Priority 1: Explicit active snapshot
        if node.active_config_snapshot_id:
            snap = snapshots_by_id.get(node.active_config_snapshot_id)
            if snap:
                config_content = snap.get("content")

        # Priority 2: config_json["startup-config"]
        if not config_content and node.config_json:
            try:
                parsed = json.loads(node.config_json)
                config_content = parsed.get("startup-config")
            except (json.JSONDecodeError, TypeError):
                pass

        # Priority 3: Latest snapshot for this node
        if not config_content:
            latest = latest_snap_by_node.get(node.container_name)
            if latest:
                config_content = latest.get("content")

        # Priority 4: Workspace filesystem
        if not config_content:
            config_path = workspace / "configs" / node.container_name / "startup-config"
            if config_path.exists():
                try:
                    config_content = config_path.read_text(encoding="utf-8")
                except OSError:
                    pass

        if config_content:
            active_configs[node.container_name] = config_content

    # Size check: snapshots + active configs
    total_config_size = sum(len((s.get("content") or "").encode()) for s in snapshots)
    total_config_size += sum(len(c.encode()) for c in active_configs.values())
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

        # Write current active startup-config for each node
        for container_name, content in active_configs.items():
            safe_name = _zip_safe_name(container_name)
            zf.writestr(f"configs/{safe_name}/startup-config", content)

        # Write all historical config snapshots
        seen_paths: set[str] = set()
        for snap in snapshots:
            node_name = _zip_safe_name(str(snap.get("node_name") or "unknown"))
            bucket = "orphaned configs" if snap.get("is_orphaned") else "configs"
            created_at = snap.get("created_at")
            ts = created_at.strftime("%Y%m%d_%H%M%S") if created_at else "unknown"
            snapshot_type = _zip_safe_name(str(snap.get("snapshot_type") or "snapshot"))

            # Deduplicate paths (same-second snapshots of same type)
            base_path = f"{bucket}/{node_name}/{ts}_{snapshot_type}_startup-config"
            path = base_path
            counter = 1
            while path in seen_paths:
                counter += 1
                path = f"{bucket}/{node_name}/{ts}_{snapshot_type}_{counter}_startup-config"
            seen_paths.add(path)

            zf.writestr(path, str(snap.get("content") or ""))

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
                    "active_configs_count": len(active_configs),
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
    lab = require_lab_editor(lab_id, database, current_user)
    write_layout(lab.id, payload)
    return payload


@router.delete("/labs/{lab_id}/layout")
def remove_layout(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, str]:
    """Delete layout data, reverting to auto-layout on next load."""
    lab = require_lab_editor(lab_id, database, current_user)
    deleted = delete_layout(lab.id)
    if not deleted:
        raise_not_found("Layout not found")
    return {"status": "deleted"}

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
    from app.utils.lab import get_lab_provider, get_node_provider

    lab = get_lab_or_404(lab_id, database, current_user)
    _ensure_node_states_exist(database, lab.id)

    # Get all node states
    states = (
        database.query(models.NodeState)
        .filter(models.NodeState.lab_id == lab_id)
        .order_by(models.NodeState.node_name)
        .all()
    )
    # Build node metadata lookup for readiness checks (VMs require kind)
    db_nodes = (
        database.query(models.Node)
        .filter(
            models.Node.lab_id == lab_id,
            models.Node.container_name.in_([s.node_name for s in states]),
        )
        .all()
    )
    nodes_by_name = {n.container_name: n for n in db_nodes}
    node_devices = {n.container_name: n.device for n in db_nodes}
    node_images = {n.container_name: n.image for n in db_nodes}

    # Build per-node placement/agent mapping (multi-host safe).
    placements = (
        database.query(models.NodePlacement)
        .filter(models.NodePlacement.lab_id == lab_id)
        .all()
    )
    placement_by_node = {p.node_name: p.host_id for p in placements}
    host_ids = set(placement_by_node.values())
    if lab.agent_id:
        host_ids.add(lab.agent_id)
    hosts = {}
    if host_ids:
        hosts = {
            h.id: h
            for h in database.query(models.Host).filter(models.Host.id.in_(host_ids)).all()
        }
    lab_provider = get_lab_provider(lab)
    fallback_agent = await get_online_agent_for_lab(
        database, lab, required_provider=lab_provider
    )

    nodes_out = []
    ready_count = 0
    running_count = 0

    for state in states:
        # Check readiness from agent if node is running
        progress_percent = None
        message = None

        if state.actual_state == NodeActualState.RUNNING:
            running_count += 1
            db_node = nodes_by_name.get(state.node_name)
            host_id = placement_by_node.get(state.node_name)
            if not host_id and db_node is not None and db_node.host_id:
                host_id = db_node.host_id
            if not host_id:
                host_id = lab.agent_id
            agent = hosts.get(host_id) if host_id else None
            if agent is not None and not agent_client.is_agent_online(agent):
                agent = None
            if agent is None and fallback_agent is not None:
                agent = fallback_agent

            if agent:
                try:
                    device_kind = node_devices.get(state.node_name)
                    node_image = node_images.get(state.node_name)
                    provider_type = None
                    if node_image:
                        if db_node is not None:
                            provider_type = get_node_provider(db_node)
                    readiness = await agent_client.check_node_readiness(
                        agent,
                        lab.id,
                        state.node_name,
                        kind=device_kind,
                        provider_type=provider_type,
                    )
                    # Update is_ready from agent response
                    if readiness.get("is_ready") and not state.is_ready:
                        state.is_ready = True
                        database.commit()
                    progress_percent = readiness.get("progress_percent")
                    message = readiness.get("message")
                except Exception as e:
                    message = f"Readiness check failed: {e}"
            else:
                message = "No online agent available for node placement"

        if state.is_ready and state.actual_state == NodeActualState.RUNNING:
            ready_count += 1

        nodes_out.append(schemas.NodeReadinessOut(
            node_id=state.node_id,
            node_name=state.node_name,
            is_ready=state.is_ready and state.actual_state == NodeActualState.RUNNING,
            actual_state=state.actual_state,
            progress_percent=progress_percent,
            message=message,
            boot_started_at=state.boot_started_at,
            management_ip=state.management_ip,
        ))

    nodes_should_run = [s for s in states if s.desired_state == NodeDesiredState.RUNNING]
    should_run_count = len(nodes_should_run)
    all_ready = should_run_count == 0 or all(
        s.is_ready and s.actual_state == NodeActualState.RUNNING for s in nodes_should_run
    )

    return schemas.LabReadinessResponse(
        lab_id=lab_id,
        all_ready=all_ready,
        ready_count=ready_count,
        total_count=should_run_count,
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
    from fastapi.responses import JSONResponse
    from app.utils.lab import get_lab_provider, get_node_provider

    # Validate parameters
    timeout = min(max(timeout, 10), 600)  # 10s to 10min
    interval = min(max(interval, 5), 60)  # 5s to 60s

    lab = get_lab_or_404(lab_id, database, current_user)
    _ensure_node_states_exist(database, lab.id)

    db_nodes = (
        database.query(models.Node)
        .filter(models.Node.lab_id == lab_id)
        .all()
    )
    nodes_by_name = {n.container_name: n for n in db_nodes}
    node_devices = {n.container_name: n.device for n in db_nodes}
    node_images = {n.container_name: n.image for n in db_nodes}

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
        nodes_should_run = [s for s in states if s.desired_state == NodeDesiredState.RUNNING]

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

            if state.actual_state == NodeActualState.RUNNING:
                running_count += 1
                if agent and not state.is_ready:
                    try:
                        device_kind = node_devices.get(state.node_name)
                        node_image = node_images.get(state.node_name)
                        provider_type = None
                        if node_image:
                            db_node = nodes_by_name.get(state.node_name)
                            if db_node is not None:
                                provider_type = get_node_provider(db_node)
                        readiness = await agent_client.check_node_readiness(
                            agent,
                            lab.id,
                            state.node_name,
                            kind=device_kind,
                            provider_type=provider_type,
                        )
                        if readiness.get("is_ready"):
                            state.is_ready = True
                            database.commit()
                        progress_percent = readiness.get("progress_percent")
                        message = readiness.get("message")
                    except Exception as e:
                        message = f"Readiness check failed: {e}"

            if state.is_ready and state.actual_state == NodeActualState.RUNNING:
                ready_count += 1

            if state in nodes_should_run:
                nodes_out.append(schemas.NodeReadinessOut(
                    node_id=state.node_id,
                    node_name=state.node_name,
                    is_ready=state.is_ready and state.actual_state == NodeActualState.RUNNING,
                    actual_state=state.actual_state,
                    progress_percent=progress_percent,
                    message=message,
                    boot_started_at=state.boot_started_at,
                    management_ip=state.management_ip,
                ))

        # Check if all nodes that should run are ready
        all_ready = all(
            s.is_ready and s.actual_state == NodeActualState.RUNNING
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
        if state.actual_state == NodeActualState.RUNNING:
            running_count += 1
        if state.is_ready and state.actual_state == NodeActualState.RUNNING:
            ready_count += 1

        nodes_out.append(schemas.NodeReadinessOut(
            node_id=state.node_id,
            node_name=state.node_name,
            is_ready=state.is_ready and state.actual_state == NodeActualState.RUNNING,
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
def export_inventory(
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
    placement_by_node, hosts = get_node_placement_mapping(database, lab_id, lab.agent_id)

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


def _canonicalize_link_endpoints(
    source_node: str,
    source_interface: str,
    target_node: str,
    target_interface: str,
    source_device: str | None = None,
    target_device: str | None = None,
) -> tuple[str, str, str, str]:
    """Normalize and sort endpoints into canonical source/target order."""
    src_i = normalize_interface(source_interface, source_device) if source_interface else "eth0"
    tgt_i = normalize_interface(target_interface, target_device) if target_interface else "eth0"
    if f"{source_node}:{src_i}" <= f"{target_node}:{tgt_i}":
        return source_node, src_i, target_node, tgt_i
    return target_node, tgt_i, source_node, src_i


def _link_state_endpoint_key(
    link_state: models.LinkState,
    node_device_map: dict[str, str | None] | None = None,
) -> tuple[str, str, str, str]:
    """Return canonical endpoint tuple for an existing LinkState row."""
    src_dev = (node_device_map or {}).get(link_state.source_node)
    tgt_dev = (node_device_map or {}).get(link_state.target_node)
    return _canonicalize_link_endpoints(
        link_state.source_node,
        link_state.source_interface,
        link_state.target_node,
        link_state.target_interface,
        source_device=src_dev,
        target_device=tgt_dev,
    )


def _choose_preferred_link_state(
    states: list[models.LinkState],
    node_device_map: dict[str, str | None] | None = None,
) -> models.LinkState:
    """Choose one row to keep when duplicate endpoint records exist."""
    def _is_canonical_row(state: models.LinkState) -> bool:
        src_n, src_i, tgt_n, tgt_i = _link_state_endpoint_key(state, node_device_map)
        canonical_name = generate_link_name(src_n, src_i, tgt_n, tgt_i)
        src_dev = (node_device_map or {}).get(state.source_node)
        tgt_dev = (node_device_map or {}).get(state.target_node)
        stored_src_i = normalize_interface(state.source_interface, src_dev)
        stored_tgt_i = normalize_interface(state.target_interface, tgt_dev)
        return (
            state.link_name == canonical_name
            and state.source_node == src_n
            and stored_src_i == src_i
            and state.target_node == tgt_n
            and stored_tgt_i == tgt_i
        )

    return sorted(
        states,
        key=lambda s: (
            _is_canonical_row(s),
            s.desired_state != "deleted",
            s.updated_at or datetime.min.replace(tzinfo=timezone.utc),
        ),
    )[-1]


def _find_matching_link_state(
    states: list[models.LinkState],
    src_n: str,
    src_i: str,
    tgt_n: str,
    tgt_i: str,
    node_device_map: dict[str, str | None] | None = None,
) -> tuple[models.LinkState | None, list[models.LinkState]]:
    """Find matching LinkState rows by canonical endpoints."""
    key = (src_n, src_i, tgt_n, tgt_i)
    matches = [s for s in states if _link_state_endpoint_key(s, node_device_map) == key]
    if not matches:
        return None, []
    preferred = _choose_preferred_link_state(matches, node_device_map)
    return preferred, [s for s in matches if s.id != preferred.id]


def _parse_link_id_endpoints(link_id: str) -> tuple[str, str, str, str] | None:
    """Best-effort parse for link id format: nodeA:ifaceA-nodeB:ifaceB."""
    if "-" not in link_id or ":" not in link_id:
        return None
    left, right = link_id.split("-", 1)
    if ":" not in left or ":" not in right:
        return None
    src_n, src_i = left.rsplit(":", 1)
    tgt_n, tgt_i = right.rsplit(":", 1)
    if not src_n or not src_i or not tgt_n or not tgt_i:
        return None
    return _canonicalize_link_endpoints(src_n, src_i, tgt_n, tgt_i)


def _sync_link_oper_state(database: Session, link_state: models.LinkState) -> None:
    recompute_link_oper_state(database, link_state)


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
    node_name_to_device: dict[str, str | None] = {
        n.container_name: n.device for n in db_nodes
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
    mutated_states: list[models.LinkState] = []

    for link in graph.links:
        if len(link.endpoints) != 2:
            continue  # Skip non-point-to-point links

        ep_a, ep_b = link.endpoints

        # Skip external endpoints (bridge, macvlan, host)
        if ep_a.type != "node" or ep_b.type != "node":
            continue

        # Resolve node IDs to names and canonicalize endpoints
        source_node = node_id_to_name.get(ep_a.node, ep_a.node)
        target_node = node_id_to_name.get(ep_b.node, ep_b.node)
        src_n, src_i, tgt_n, tgt_i = _canonicalize_link_endpoints(
            source_node,
            ep_a.ifname or "eth0",
            target_node,
            ep_b.ifname or "eth0",
            source_device=node_name_to_device.get(source_node),
            target_device=node_name_to_device.get(target_node),
        )
        link_name = generate_link_name(src_n, src_i, tgt_n, tgt_i)
        current_link_names.add(link_name)

        existing, duplicates = _find_matching_link_state(existing_states, src_n, src_i, tgt_n, tgt_i, node_name_to_device)
        # Old naming variants can collide to the same canonical endpoints.
        # Delete duplicates immediately so the preferred row's link_name
        # rename doesn't hit the unique constraint (uq_link_state_lab_link).
        for duplicate in duplicates:
            existing_states.remove(duplicate)
            database.delete(duplicate)
        if duplicates:
            database.flush()

        if existing:
            # Update existing link state to canonical storage
            existing_changed = (
                existing.link_name != link_name
                or existing.source_node != src_n
                or existing.source_interface != src_i
                or existing.target_node != tgt_n
                or existing.target_interface != tgt_i
            )
            existing.link_name = link_name
            existing.source_node = src_n
            existing.source_interface = src_i
            existing.target_node = tgt_n
            existing.target_interface = tgt_i
            # If this was previously a stale duplicate row, re-activate.
            if existing.desired_state == "deleted":
                existing.desired_state = LinkDesiredState.UP
                existing_changed = True
            if existing_changed:
                mutated_states.append(existing)
            updated_count += 1
        else:
            # Create new link state
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
                desired_state=LinkDesiredState.UP,
                actual_state=LinkActualState.UNKNOWN,
            )
            database.add(new_state)
            existing_states.append(new_state)
            mutated_states.append(new_state)
            added_link_names.append(link_name)
            created_count += 1

    # Collect info about links to remove (for teardown) before deleting
    for existing_state in existing_states:
        if existing_state.link_name not in current_link_names:
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
            mutated_states.append(existing_state)

    for state in mutated_states:
        ok, conflicts = sync_link_endpoint_reservations(database, state)
        if not ok:
            _raise_link_endpoint_conflict(database, state, conflicts)
        _sync_link_oper_state(database, state)

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


def _link_endpoint_payload(state: models.LinkState) -> list[dict[str, str]]:
    return [
        {
            "node_name": state.source_node,
            "interface_name": normalize_interface(state.source_interface or ""),
        },
        {
            "node_name": state.target_node,
            "interface_name": normalize_interface(state.target_interface or ""),
        },
    ]


def _raise_link_endpoint_conflict(
    database: Session,
    state: models.LinkState,
    conflicts: list[str],
) -> None:
    endpoints = _link_endpoint_payload(state)
    conflict_details = get_conflicting_link_details(
        database,
        state.lab_id,
        state.id,
        [(endpoint["node_name"], endpoint["interface_name"]) for endpoint in endpoints],
    )
    all_conflicting_links = sorted(
        {
            link
            for detail in conflict_details
            for link in detail.get("conflicting_links", [])
            if isinstance(link, str)
        }
    )
    if not all_conflicting_links:
        all_conflicting_links = sorted(conflicts)

    raise HTTPException(
        status_code=409,
        detail={
            "code": "link_endpoint_reserved",
            "message": "Endpoint already reserved by desired-up link(s).",
            "link": {
                "lab_id": state.lab_id,
                "link_name": state.link_name,
                "desired_state": state.desired_state,
                "endpoints": endpoints,
            },
            "conflicting_links": all_conflicting_links,
            "conflicting_endpoints": conflict_details,
        },
    )


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
    lab = require_lab_editor(lab_id, database, current_user)
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
    ok, conflicts = sync_link_endpoint_reservations(database, state)
    if not ok:
        database.rollback()
        _raise_link_endpoint_conflict(database, state, conflicts)
    _sync_link_oper_state(database, state)
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
    lab = require_lab_editor(lab_id, database, current_user)
    _ensure_link_states_exist(database, lab.id)

    states = (
        database.query(models.LinkState)
        .filter(models.LinkState.lab_id == lab_id)
        .all()
    )
    for state in states:
        state.desired_state = payload.state
        ok, conflicts = sync_link_endpoint_reservations(database, state)
        if not ok:
            database.rollback()
            _raise_link_endpoint_conflict(database, state, conflicts)
        _sync_link_oper_state(database, state)
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

    created, updated, _, _ = _upsert_link_states(database, lab.id, graph)
    database.commit()

    return schemas.LinkStateRefreshResponse(
        message="Link states refreshed",
        links_created=created,
        links_updated=updated,
    )

# ============================================================================
# Hot-Connect Link Management Endpoints
# ============================================================================


@router.post("/labs/{lab_id}/hot-connect")
async def hot_connect_link(
    lab_id: str,
    request: schemas.HotConnectRequest,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.HotConnectResponse:
    """Hot-connect two interfaces in a running lab.

    This creates a Layer 2 link between two container interfaces without
    restarting any nodes. The link is established by assigning both interfaces
    the same VLAN tag on the OVS bridge.

    Requirements:
    - Lab must be deployed (running state)
    - Both nodes must be running
    - Interfaces must be pre-provisioned via OVS
    """
    lab = require_lab_editor(lab_id, database, current_user)

    # Verify lab is running
    if lab.state not in (LabState.RUNNING, LabState.STARTING):
        raise HTTPException(
            status_code=400,
            detail=f"Lab must be running for hot-connect (current state: {lab.state})"
        )

    # Look up device types for accurate interface normalization
    _src_db_node = (
        database.query(models.Node)
        .filter(models.Node.lab_id == lab_id, models.Node.container_name == request.source_node)
        .first()
    )
    _tgt_db_node = (
        database.query(models.Node)
        .filter(models.Node.lab_id == lab_id, models.Node.container_name == request.target_node)
        .first()
    )
    src_n, src_i, tgt_n, tgt_i = _canonicalize_link_endpoints(
        request.source_node,
        request.source_interface,
        request.target_node,
        request.target_interface,
        source_device=_src_db_node.device if _src_db_node else None,
        target_device=_tgt_db_node.device if _tgt_db_node else None,
    )
    link_name = generate_link_name(src_n, src_i, tgt_n, tgt_i)
    existing_states = (
        database.query(models.LinkState)
        .filter(models.LinkState.lab_id == lab_id)
        .all()
    )
    _device_map: dict[str, str | None] = {}
    if _src_db_node:
        _device_map[_src_db_node.container_name] = _src_db_node.device
    if _tgt_db_node:
        _device_map[_tgt_db_node.container_name] = _tgt_db_node.device
    link_state, duplicate_states = _find_matching_link_state(
        existing_states, src_n, src_i, tgt_n, tgt_i, _device_map
    )
    for duplicate in duplicate_states:
        duplicate.desired_state = "deleted"  # not in LinkDesiredState enum — soft-delete marker

    if not link_state:
        # Backward compatibility fallback: exact name match
        link_state = next((s for s in existing_states if s.link_name == link_name), None)

    if not link_state:

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
            desired_state=LinkDesiredState.UP,
            actual_state=LinkActualState.UNKNOWN,
        )
        database.add(link_state)
        database.flush()
        _sync_link_oper_state(database, link_state)
    else:
        # Ensure canonical storage and reactivate stale records.
        link_state.link_name = link_name
        link_state.source_node = src_n
        link_state.source_interface = src_i
        link_state.target_node = tgt_n
        link_state.target_interface = tgt_i
        if link_state.desired_state == "deleted":
            link_state.desired_state = LinkDesiredState.UP
        _sync_link_oper_state(database, link_state)

    host_to_agent = await _build_host_to_agent_map(database, lab_id)
    if not host_to_agent:
        raise_unavailable("No healthy agent available")

    success = await create_link_if_ready(database, lab_id, link_state, host_to_agent)
    database.commit()

    if success:
        return schemas.HotConnectResponse(
            success=True,
            link_id=link_state.link_name,
            vlan_tag=link_state.vlan_tag,
        )

    if link_state.actual_state == LinkActualState.PENDING:
        return schemas.HotConnectResponse(
            success=False,
            error="Link pending - waiting for nodes to be running",
        )

    return schemas.HotConnectResponse(
        success=False,
        error=link_state.error_message or "Link creation failed",
    )


@router.delete("/labs/{lab_id}/hot-disconnect/{link_id:path}")
async def hot_disconnect_link(
    lab_id: str,
    link_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.HotConnectResponse:
    """Hot-disconnect a link in a running lab.

    This breaks a Layer 2 link between two container interfaces without
    restarting any nodes. The link is broken by assigning each interface
    a separate VLAN tag.

    Args:
        lab_id: Lab identifier
        link_id: Link identifier (format: "node1:iface1-node2:iface2")
    """
    require_lab_editor(lab_id, database, current_user)

    link_states = (
        database.query(models.LinkState)
        .filter(models.LinkState.lab_id == lab_id)
        .all()
    )
    _del_device_map: dict[str, str | None] = {
        n.container_name: n.device
        for n in database.query(models.Node).filter(models.Node.lab_id == lab_id).all()
    }
    parsed = _parse_link_id_endpoints(link_id)
    link_state = None
    if parsed:
        src_n, src_i, tgt_n, tgt_i = parsed
        link_state, _ = _find_matching_link_state(link_states, src_n, src_i, tgt_n, tgt_i, _del_device_map)

    if link_state is None:
        # Backward compatibility with exact legacy IDs.
        for ls in link_states:
            if link_id == f"{ls.source_node}:{ls.source_interface}-{ls.target_node}:{ls.target_interface}" or \
               link_id == f"{ls.target_node}:{ls.target_interface}-{ls.source_node}:{ls.source_interface}":
                link_state = ls
                break

    if not link_state:
        return schemas.HotConnectResponse(success=False, error=f"Link '{link_id}' not found")

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
        return schemas.HotConnectResponse(success=True, link_id=link_id)

    return schemas.HotConnectResponse(
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
    request: schemas.ExternalConnectRequest,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.ExternalConnectResponse:
    """Connect a node interface to an external network.

    This establishes connectivity between a container interface and an
    external host interface (e.g., for internet access, management network,
    or physical lab equipment).

    Requirements:
    - Lab must be deployed (running state)
    - Node must be running
    - External interface must exist on the host
    """
    lab = require_lab_editor(lab_id, database, current_user)

    # Verify lab is running
    if lab.state not in (LabState.RUNNING, LabState.STARTING):
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

    return schemas.ExternalConnectResponse(
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


@router.post("/labs/{lab_id}/cleanup-orphans", response_model=schemas.CleanupOrphansResponse)
async def cleanup_lab_orphans(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.CleanupOrphansResponse:
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
        .filter(models.Host.status == HostStatus.ONLINE)
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

    return schemas.CleanupOrphansResponse(
        removed_by_agent=removed_by_agent,
        errors=errors,
    )


# =============================================================================
# Interface Mapping Endpoints
# =============================================================================


@router.get("/labs/{lab_id}/interface-mappings")
def get_lab_interface_mappings(
    lab_id: str,
    database: Session = Depends(db.get_db),
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
def get_node_interfaces(
    lab_id: str,
    node_id: str,
    database: Session = Depends(db.get_db),
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


@router.post("/labs/{lab_id}/interface-mappings/sync")
async def sync_interface_mappings(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.InterfaceMappingSyncResponse:
    """Sync interface mappings from all agents for a lab.

    Fetches OVS port information from agents and updates the
    interface_mappings table with current state.
    """
    get_lab_or_404(lab_id, database, current_user)

    result = await interface_mapping_service.populate_all_agents(database, lab_id)

    return schemas.InterfaceMappingSyncResponse(
        created=result["created"],
        updated=result["updated"],
        errors=result["errors"],
        agents_queried=result["agents_queried"],
    )


@router.post("/labs/{lab_id}/links/reconcile")
async def reconcile_links(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.LinkReconciliationResponse:
    """Reconcile link states for a lab.

    Verifies all links marked as "up" have matching VLAN tags on both
    endpoints. Attempts to repair any mismatched links.
    """
    require_lab_editor(lab_id, database, current_user)

    result = await reconcile_lab_links(database, lab_id)

    return schemas.LinkReconciliationResponse(
        checked=result["checked"],
        valid=result["valid"],
        repaired=result["repaired"],
        errors=result["errors"],
        skipped=result["skipped"],
    )


def _include_labs_subrouters() -> None:
    """Attach extracted labs subrouters.

    Imported lazily to avoid circular imports while `labs.py` is still loading
    shared helper functions used by these modules.
    """
    if getattr(router, "_labs_subrouters_included", False):
        return

    from app.routers.labs_configs import router as labs_configs_router
    from app.routers.labs_node_states import router as labs_node_states_router

    router.include_router(labs_node_states_router)
    router.include_router(labs_configs_router)
    setattr(router, "_labs_subrouters_included", True)


_include_labs_subrouters()

# Backward compatibility for tests still patching this symbol via app.routers.labs.
from app.routers.labs_configs import _save_config_to_workspace  # noqa: E402,F401
