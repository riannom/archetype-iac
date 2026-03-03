"""Lab CRUD and node state helper endpoints.

NOTE: Several symbols (get_online_agent_for_lab, run_agent_job, TopologyService,
lab_workspace, get_config_by_device) are resolved through the parent package
(``app.routers.labs``) so that test monkeypatching on that path continues to work.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import sys

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import db, models, schemas
from app.auth import get_current_user
from app.enums import GlobalRole
from app.events.publisher import emit_lab_deleted
from app.services.permissions import PermissionService
from app.state import (
    JobStatus,
    LabState,
    NodeActualState,
    NodeDesiredState,
)
from app.tasks.jobs import run_multihost_destroy
from app.utils.http import require_lab_owner
from app.utils.lab import (
    get_lab_or_404,
    get_lab_provider,
    get_lab_with_role,
)


def _pkg():
    """Resolve the parent package for monkeypatch-safe attribute access."""
    return sys.modules["app.routers.labs"]

logger = logging.getLogger(__name__)

router = APIRouter(tags=["labs"])


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

    # Build lookup from GUI ID -> Node definition for node_definition_id FK
    node_defs_by_gui_id: dict[str, models.Node] = {}
    for node_def in (
        database.query(models.Node)
        .filter(models.Node.lab_id == lab_id)
        .all()
    ):
        node_defs_by_gui_id[node_def.gui_id] = node_def

    # Update or create node states
    for node in graph.nodes:
        # Use container_name (YAML key) for container operations, fall back to name
        container_name = node.container_name or node.name
        # Resolve the Node definition FK (stable UUID)
        node_def = node_defs_by_gui_id.get(node.id)
        node_def_id = node_def.id if node_def else None

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
            # Always keep node_definition_id in sync
            if node_def_id and existing_state.node_definition_id != node_def_id:
                existing_state.node_definition_id = node_def_id
        elif container_name in existing_by_name:
            # Node exists by name but GUI ID changed - reuse the existing state
            # to prevent duplicate node_states for the same container
            existing_state = existing_by_name[container_name]
            old_id = existing_state.node_id
            existing_state.node_id = node.id
            existing_state.node_definition_id = node_def_id
            reused_old_ids.add(old_id)
        else:
            # Create new with defaults - node_name is set only once at creation
            new_state = models.NodeState(
                lab_id=lab_id,
                node_id=node.id,
                node_name=container_name,
                node_definition_id=node_def_id,
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
            placement = None
            if existing_state.node_definition_id:
                placement = (
                    database.query(models.NodePlacement)
                    .filter(
                        models.NodePlacement.lab_id == lab_id,
                        models.NodePlacement.node_definition_id == existing_state.node_definition_id,
                    )
                    .first()
                )
            if not placement:
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
                "node_definition_id": existing_state.node_definition_id,
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
    service = _pkg().TopologyService(database)
    if service.has_nodes(lab_id):
        graph = service.export_to_graph(lab_id)
        _upsert_node_states(database, lab_id, graph)
        database.commit()


def _populate_lab_counts(database: Session, lab_out: schemas.LabOut) -> None:
    """Populate node_count, running_count, container_count, vm_count for a single lab."""

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
            config = _pkg().get_config_by_device(device)
            if config and "qcow2" in (config.supported_image_kinds or []):
                vms += 1
    lab_out.node_count = total
    lab_out.running_count = running
    lab_out.vm_count = vms
    lab_out.container_count = total - vms


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

    # Determine VM vs container by device type using vendor registry.

    device_nodes = (
        database.query(models.Node.lab_id, models.Node.device)
        .filter(models.Node.lab_id.in_(lab_ids), models.Node.node_type == "device")
        .all()
    )
    vm_counts: dict[str, int] = {}
    for nlab_id, device in device_nodes:
        if device:
            config = _pkg().get_config_by_device(device)
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
    workspace = _pkg().lab_workspace(lab.id)
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
        service = _pkg().TopologyService(database)
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
                agent = await _pkg().get_online_agent_for_lab(database, lab, required_provider=lab_provider)
                if agent:
                    await _pkg().run_agent_job(
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
    workspace = _pkg().lab_workspace(lab.id)

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
    source = _pkg().lab_workspace(lab.id)
    target = _pkg().lab_workspace(clone.id)
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
