"""Topology, YAML, graph, layout, and bundle endpoints for labs.

NOTE: Several symbols (TopologyService, read_layout, delete_layout, lab_workspace,
process_link_changes, process_node_changes, safe_create_task) are resolved through
the parent package (``app.routers.labs``) so that test monkeypatching on that path
continues to work after the split from the monolithic ``labs.py``.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import sys
import zipfile
from datetime import datetime, timezone

import yaml
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import db, models, schemas
from app.auth import get_current_user
from app.events.publisher import emit_link_removed, emit_node_removed
# write_layout is NOT patched by tests, safe to import directly
from app.storage import write_layout
from app.utils.http import raise_not_found
from app.utils.lab import (
    get_lab_or_404,
    require_lab_editor,
    update_lab_provider_from_nodes,
)

from ._shared import _zip_safe_name
from .crud import _upsert_node_states
from .link_states import _upsert_link_states


def _pkg():
    """Resolve the parent package for monkeypatch-safe attribute access."""
    return sys.modules["app.routers.labs"]

logger = logging.getLogger(__name__)

router = APIRouter(tags=["labs"])


@router.post("/labs/{lab_id}/update-topology-from-yaml")
async def update_topology_from_yaml(
    lab_id: str,
    payload: schemas.LabYamlIn,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.LabOut:
    lab = require_lab_editor(lab_id, database, current_user)
    workspace = _pkg().lab_workspace(lab.id)
    await asyncio.to_thread(workspace.mkdir, parents=True, exist_ok=True)

    # Store topology in database (source of truth)
    service = _pkg().TopologyService(database)
    try:
        service.update_from_yaml(lab.id, payload.content)
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

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
        _pkg().safe_create_task(
            _pkg().process_link_changes(lab.id, added_link_names, removed_link_info, current_user.id),
            name=f"live_links:{lab.id}"
        )

    # Trigger live node operations in background if there are changes
    if added_node_ids or removed_node_info:
        _pkg().safe_create_task(
            _pkg().process_node_changes(lab.id, added_node_ids, removed_node_info),
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

    service = _pkg().TopologyService(database)
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
    workspace = _pkg().lab_workspace(lab.id)
    await asyncio.to_thread(workspace.mkdir, parents=True, exist_ok=True)

    # Store topology in database (source of truth)
    service = _pkg().TopologyService(database)
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
        _pkg().safe_create_task(
            _pkg().process_link_changes(lab.id, added_link_names, removed_link_info, current_user.id),
            name=f"live_links_update:{lab.id}"
        )

    # Trigger live node operations in background if there are changes
    if added_node_ids or removed_node_info:
        _pkg().safe_create_task(
            _pkg().process_node_changes(lab.id, added_node_ids, removed_node_info),
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
    service = _pkg().TopologyService(database)
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

    service = _pkg().TopologyService(database)
    if not service.has_nodes(lab.id):
        raise_not_found("Topology not found")
    graph = service.export_to_graph(lab.id)

    if include_layout:
        layout = _pkg().read_layout(lab.id)
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

    service = _pkg().TopologyService(database)
    has_topology = service.has_nodes(lab.id)
    topology_yaml = service.export_to_yaml(lab.id) if has_topology else "nodes: {}\nlinks: []\n"
    topology_graph = (
        service.export_to_graph(lab.id).model_dump(mode="json")
        if has_topology
        else {"nodes": [], "links": []}
    )

    layout = _pkg().read_layout(lab.id)
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
    workspace = _pkg().lab_workspace(lab.id)
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
    layout = _pkg().read_layout(lab.id)
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
    deleted = _pkg().delete_layout(lab.id)
    if not deleted:
        raise_not_found("Layout not found")
    return {"status": "deleted"}
