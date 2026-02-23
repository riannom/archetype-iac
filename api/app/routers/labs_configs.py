"""Config extraction and snapshot endpoints for labs."""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app import db, models, schemas
from app.auth import get_current_user
from app.state import HostStatus
from app.storage import lab_workspace
from app.utils.http import raise_not_found, raise_unavailable
from app.utils.lab import get_lab_or_404, get_lab_provider, require_lab_editor

router = APIRouter(tags=["labs"])


def _agent_client():
    """Resolve agent_client via labs module for test monkeypatch compatibility."""
    from app.routers import labs as labs_router

    return labs_router.agent_client
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
    agent_client = _agent_client()

    # Phase 1: Gather agent info from DB
    def _sync_prepare():
        with db.get_session() as database:
            lab = require_lab_editor(lab_id, database, current_user)
            lab_provider = get_lab_provider(lab)

            placements = (
                database.query(models.NodePlacement.host_id)
                .filter(models.NodePlacement.lab_id == lab.id)
                .distinct()
                .all()
            )
            host_ids = [p.host_id for p in placements]

            # If no placements, fall back (inline get_online_agent_for_lab - pure DB)
            if not host_ids:
                from app.agent_client import _agent_online_cutoff, get_agent_providers
                from sqlalchemy import func as sa_func

                cutoff = _agent_online_cutoff()
                pc = (
                    database.query(models.NodePlacement.host_id, sa_func.count(models.NodePlacement.id))
                    .filter(models.NodePlacement.lab_id == lab.id)
                    .group_by(models.NodePlacement.host_id)
                    .all()
                )
                anc = {h: c for h, c in pc}
                preferred_id = max(anc, key=anc.get) if anc else lab.agent_id

                agents = (
                    database.query(models.Host)
                    .filter(models.Host.status == HostStatus.ONLINE, models.Host.last_heartbeat >= cutoff)
                    .all()
                )
                if lab_provider:
                    agents = [a for a in agents if lab_provider in get_agent_providers(a)]
                fallback = None
                if preferred_id:
                    fallback = next((a for a in agents if a.id == preferred_id), None)
                if not fallback and agents:
                    fallback = agents[0]
                if fallback:
                    host_ids = [fallback.id]

            # Get healthy agents
            agent_infos = []
            for host_id in host_ids:
                agent = database.get(models.Host, host_id)
                if agent and agent_client.is_agent_online(agent):
                    agent_infos.append({"id": agent.id, "address": agent.address})

            if not agent_infos:
                raise_unavailable("No healthy agents available")

            # Build node -> agent address mapping for cross-agent sync
            node_to_agent_address: dict[str, str] = {}
            for placement in database.query(models.NodePlacement).filter(
                models.NodePlacement.lab_id == lab.id
            ).all():
                node_def = None
                if placement.node_definition_id:
                    node_def = database.get(models.Node, placement.node_definition_id)
                elif placement.node_name:
                    node_def = (
                        database.query(models.Node)
                        .filter(
                            models.Node.lab_id == lab.id,
                            models.Node.container_name == placement.node_name,
                        )
                        .first()
                    )
                agent = database.get(models.Host, placement.host_id)
                if node_def and agent and agent_client.is_agent_online(agent):
                    node_to_agent_address[node_def.container_name] = agent.address

            return {
                "lab_id": lab.id,
                "agent_infos": agent_infos,
                "node_to_agent_address": node_to_agent_address,
            }

    phase1 = await asyncio.to_thread(_sync_prepare)
    real_lab_id = phase1["lab_id"]

    # Phase 2: Async config extraction from all agents concurrently
    from types import SimpleNamespace
    agent_stubs = [SimpleNamespace(**info) for info in phase1["agent_infos"]]
    extraction_tasks = [agent_client.extract_configs_on_agent(stub, real_lab_id) for stub in agent_stubs]
    results = await asyncio.gather(*extraction_tasks, return_exceptions=True)

    configs = []
    extracted_count = 0
    errors = []

    for agent_info, result in zip(phase1["agent_infos"], results):
        if isinstance(result, Exception):
            errors.append(f"Agent {agent_info['id']}: {result}")
            continue
        if not result.get("success"):
            errors.append(f"Agent {agent_info['id']}: {result.get('error', 'Unknown error')}")
            continue
        extracted_count += result.get("extracted_count", 0)
        configs.extend(result.get("configs", []))

    if not configs and errors:
        raise HTTPException(
            status_code=500,
            detail=f"Config extraction failed on all agents: {'; '.join(errors)}"
        )

    # Phase 3: Save configs to DB in worker thread
    snapshots_created = 0
    if configs:
        def _sync_save_configs():
            from app.services.config_service import ConfigService

            nonlocal snapshots_created
            with db.get_session() as database:
                config_svc = ConfigService(database)

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
                        config_svc.save_extracted_config(
                            lab_id=lab_id,
                            node_name=node_name,
                            content=content,
                            snapshot_type=snapshot_type,
                            device_kind=node_device_map.get(node_name),
                            set_as_active=False,
                        )

                database.commit()

        await asyncio.to_thread(_sync_save_configs)

    # Phase 4: Async config sync to agents
    node_to_agent_address = phase1["node_to_agent_address"]
    sync_errors = []
    for config_data in configs:
        node_name = config_data.get("node_name")
        content = config_data.get("content")
        if not node_name or not content:
            continue

        agent_address = node_to_agent_address.get(node_name)
        if agent_address:
            try:
                url = f"http://{agent_address}/labs/{real_lab_id}/nodes/{node_name}/config"
                client = agent_client.get_http_client()
                response = await client.put(
                    url,
                    json={"content": content},
                    timeout=30.0,
                    headers=agent_client._get_agent_auth_headers(),
                )
                if response.status_code != 200:
                    sync_errors.append(f"{node_name}: HTTP {response.status_code}")
            except Exception as e:
                sync_errors.append(f"{node_name}: {e}")

    return {
        "success": True,
        "extracted_count": extracted_count,
        "snapshots_created": snapshots_created,
        "sync_errors": sync_errors if sync_errors else None,
        "message": f"Extracted {extracted_count} cEOS configs, created {snapshots_created} snapshot(s)",
    }


@router.post("/labs/{lab_id}/nodes/{node_id}/extract-config")
async def extract_node_config(
    lab_id: str,
    node_id: str,
    create_snapshot: bool = True,
    snapshot_type: str = "manual",
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """Extract running config from a specific node in the lab."""
    agent_client = _agent_client()
    lab = require_lab_editor(lab_id, database, current_user)

    node = (
        database.query(models.Node)
        .filter(models.Node.lab_id == lab.id, models.Node.gui_id == node_id)
        .first()
    )
    if not node:
        raise_not_found(f"Node '{node_id}' not found")

    # External nodes do not have device configs.
    if node.node_type == "external":
        raise HTTPException(status_code=400, detail="Cannot extract config from external network nodes")

    node_name = node.container_name
    placement = (
        database.query(models.NodePlacement)
        .filter(
            models.NodePlacement.lab_id == lab.id,
            models.NodePlacement.node_name == node_name,
        )
        .first()
    )
    host_id = (
        placement.host_id
        or node.host_id
        or lab.agent_id
    )
    if not host_id:
        raise_unavailable(f"No host assignment found for node '{node_name}'")

    agent = database.get(models.Host, host_id)
    if not agent or not agent_client.is_agent_online(agent):
        raise_unavailable(f"No healthy agent available for node '{node_name}'")

    result = await agent_client.extract_node_config_on_agent(agent, lab.id, node_name)
    if not result.get("success"):
        raise HTTPException(
            status_code=500,
            detail=(
                f"Config extraction failed for {node_name}: "
                f"{result.get('error', 'Unknown error')}"
            ),
        )

    content = result.get("content")
    if not content:
        raise HTTPException(status_code=500, detail=f"Config extraction returned empty content for {node_name}")

    snapshots_created = 0
    from app.services.config_service import ConfigService
    config_svc = ConfigService(database)
    if create_snapshot:
        snapshot = config_svc.save_extracted_config(
            lab_id=lab_id,
            node_name=node_name,
            content=content,
            snapshot_type=snapshot_type,
            device_kind=node.device,
        )
        if snapshot:
            snapshots_created = 1
    else:
        config_svc.save_extracted_config(
            lab_id=lab_id,
            node_name=node_name,
            content=content,
            snapshot_type=snapshot_type,
            device_kind=node.device,
            set_as_active=False,
        )
    database.commit()

    sync_result = await agent_client.update_config_on_agent(agent, lab.id, node_name, content)
    sync_error = None if sync_result.get("success") else sync_result.get("error", "unknown")

    return {
        "success": True,
        "node_id": node_id,
        "node_name": node_name,
        "snapshots_created": snapshots_created,
        "sync_error": sync_error,
        "message": f"Extracted config for {node_name}",
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
def create_config_snapshot(
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
    lab = require_lab_editor(lab_id, database, current_user)
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
    require_lab_editor(lab_id, database, current_user)

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
    require_lab_editor(lab_id, database, current_user)

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
    require_lab_editor(lab_id, database, current_user)

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
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """Set a specific config snapshot as the active startup-config for a node.

    Updates the node's active_config_snapshot_id, syncs content to
    config_json["startup-config"], and pushes to the agent workspace.
    """
    snapshot_id = payload.snapshot_id
    agent_client = _agent_client()

    # Phase 1: All DB work in a worker thread
    def _sync_set_config():
        from app.services.config_service import ConfigService

        with db.get_session() as database:
            require_lab_editor(lab_id, database, current_user)

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

            config_svc = ConfigService(database)
            try:
                config_svc.set_active_config(node.id, snapshot_id)
            except ValueError as e:
                raise HTTPException(status_code=404, detail=str(e))

            database.commit()

            # Gather agent push info before session closes
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
                    snapshot = database.get(models.ConfigSnapshot, snapshot_id)
                    if snapshot:
                        return {
                            "agent_address": agent.address,
                            "agent_id": agent.id,
                            "config_content": snapshot.content,
                        }
            return None

    push_info = await asyncio.to_thread(_sync_set_config)

    # Phase 2: Async agent push (no DB session held)
    if push_info:
        url = (
            f"http://{push_info['agent_address']}"
            f"/labs/{lab_id}/nodes/{node_name}/config"
        )
        client = agent_client.get_http_client()
        try:
            await client.put(
                url,
                json={"content": push_info["config_content"]},
                timeout=30.0,
                headers=agent_client._get_agent_auth_headers(),
            )
        except Exception:
            pass  # Best-effort push, non-critical

    return {
        "success": True,
        "node_name": node_name,
        "active_config_snapshot_id": snapshot_id,
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
