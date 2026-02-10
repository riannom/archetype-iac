"""Config management service for snapshot operations, mapping, and lifecycle.

Centralizes all config mutation logic:
- Saving extracted configs (triple-write: config_json + snapshot + workspace)
- Setting active startup-config for nodes
- Mapping orphaned configs to replacement nodes
- Bulk deletion with active config safety guard
- Zip download with metadata
- Orphan detection via LEFT JOIN query

Usage:
    from app.services.config_service import ConfigService

    svc = ConfigService(db)
    snapshots = svc.list_configs_with_orphan_status(lab_id)
    svc.set_active_config(node_id, snapshot_id)
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import shutil
import zipfile
from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session

from app import models
from app.storage import lab_workspace

logger = logging.getLogger(__name__)

# Maximum zip download size in bytes (50MB)
MAX_ZIP_SIZE_BYTES = 50 * 1024 * 1024


class ActiveConfigGuardError(Exception):
    """Raised when attempting to delete an active startup-config without force."""

    def __init__(self, active_snapshots: list[dict]):
        self.active_snapshots = active_snapshots
        node_names = ", ".join(s["node_name"] for s in active_snapshots)
        super().__init__(
            f"Cannot delete: snapshots are active startup-configs for: {node_names}. "
            f"Use force=True to override."
        )


class ConfigService:
    """Service for config snapshot operations, mapping, and lifecycle."""

    def __init__(self, db: Session):
        self.db = db

    # -------------------------------------------------------------------------
    # Query operations
    # -------------------------------------------------------------------------

    def list_configs_with_orphan_status(
        self,
        lab_id: str,
        node_name: str | None = None,
        orphaned_only: bool = False,
        device_kind: str | None = None,
    ) -> list[dict]:
        """List config snapshots with orphan status and active indicator.

        Uses a LEFT JOIN to detect orphaned configs (node_name not matching
        any current node in the lab) and checks active_config_snapshot_id
        for the is_active flag.

        Returns list of dicts with snapshot fields + is_orphaned + is_active.
        """
        # Build active snapshot lookup: {snapshot_id: node_container_name}
        nodes = (
            self.db.query(models.Node)
            .filter(models.Node.lab_id == lab_id)
            .all()
        )
        active_snapshot_ids = {
            n.active_config_snapshot_id
            for n in nodes
            if n.active_config_snapshot_id
        }
        current_node_names = {n.container_name for n in nodes}

        # Query snapshots
        query = (
            self.db.query(models.ConfigSnapshot)
            .filter(models.ConfigSnapshot.lab_id == lab_id)
        )
        if node_name:
            query = query.filter(models.ConfigSnapshot.node_name == node_name)
        if device_kind:
            query = query.filter(models.ConfigSnapshot.device_kind == device_kind)

        snapshots = query.order_by(models.ConfigSnapshot.created_at.desc()).all()

        results = []
        for snap in snapshots:
            is_orphaned = snap.node_name not in current_node_names
            if orphaned_only and not is_orphaned:
                continue
            results.append({
                "id": snap.id,
                "lab_id": snap.lab_id,
                "node_name": snap.node_name,
                "content": snap.content,
                "content_hash": snap.content_hash,
                "snapshot_type": snap.snapshot_type,
                "device_kind": snap.device_kind,
                "mapped_to_node_id": snap.mapped_to_node_id,
                "created_at": snap.created_at,
                "is_active": snap.id in active_snapshot_ids,
                "is_orphaned": is_orphaned,
            })

        return results

    def get_orphaned_configs(self, lab_id: str) -> dict[str, list[dict]]:
        """Get stranded configs grouped by device_kind.

        Returns dict mapping device_kind -> list of orphaned snapshot dicts.
        """
        all_configs = self.list_configs_with_orphan_status(lab_id, orphaned_only=True)
        grouped: dict[str, list[dict]] = {}
        for cfg in all_configs:
            kind = cfg.get("device_kind") or "unknown"
            grouped.setdefault(kind, []).append(cfg)
        return grouped

    # -------------------------------------------------------------------------
    # Mutation operations
    # -------------------------------------------------------------------------

    def save_extracted_config(
        self,
        lab_id: str,
        node_name: str,
        content: str,
        snapshot_type: str = "manual",
        device_kind: str | None = None,
        set_as_active: bool = True,
    ) -> models.ConfigSnapshot | None:
        """Save an extracted config with triple-write pattern.

        1. Update Node.config_json["startup-config"]
        2. Create ConfigSnapshot (with dedup check)
        3. Save to workspace filesystem

        Returns the created snapshot, or None if deduplicated (content unchanged).
        """
        content_hash = hashlib.sha256(content.encode()).hexdigest()

        # Check for dedup: skip if latest snapshot has same hash
        latest = (
            self.db.query(models.ConfigSnapshot)
            .filter(
                models.ConfigSnapshot.lab_id == lab_id,
                models.ConfigSnapshot.node_name == node_name,
            )
            .order_by(models.ConfigSnapshot.created_at.desc())
            .first()
        )
        if latest and latest.content_hash == content_hash:
            logger.debug(f"Config unchanged for {node_name}, skipping snapshot")
            return None

        # 1. Create snapshot
        snapshot = models.ConfigSnapshot(
            id=str(uuid4()),
            lab_id=lab_id,
            node_name=node_name,
            content=content,
            content_hash=content_hash,
            snapshot_type=snapshot_type,
            device_kind=device_kind,
        )
        self.db.add(snapshot)

        # 2. Update Node.config_json if node exists
        node = (
            self.db.query(models.Node)
            .filter(
                models.Node.lab_id == lab_id,
                models.Node.container_name == node_name,
            )
            .first()
        )
        if node:
            config = {}
            if node.config_json:
                try:
                    config = json.loads(node.config_json)
                except json.JSONDecodeError:
                    pass
            config["startup-config"] = content
            node.config_json = json.dumps(config)

            # Set as active config
            if set_as_active:
                node.active_config_snapshot_id = snapshot.id

        # 3. Save to workspace
        workspace = lab_workspace(lab_id)
        _save_config_to_workspace(workspace, node_name, content)

        self.db.flush()
        return snapshot

    def set_active_config(self, node_id: str, snapshot_id: str) -> models.Node:
        """Set a specific snapshot as the active startup-config for a node.

        Updates:
        1. Node.active_config_snapshot_id
        2. Node.config_json["startup-config"] with snapshot content
        """
        node = self.db.query(models.Node).get(node_id)
        if not node:
            raise ValueError(f"Node not found: {node_id}")

        snapshot = self.db.query(models.ConfigSnapshot).get(snapshot_id)
        if not snapshot:
            raise ValueError(f"Snapshot not found: {snapshot_id}")

        if snapshot.lab_id != node.lab_id:
            raise ValueError("Snapshot does not belong to the same lab as the node")

        # Update active config FK
        node.active_config_snapshot_id = snapshot_id

        # Sync content to config_json
        config = {}
        if node.config_json:
            try:
                config = json.loads(node.config_json)
            except json.JSONDecodeError:
                pass
        config["startup-config"] = snapshot.content
        node.config_json = json.dumps(config)

        # Save to workspace
        workspace = lab_workspace(node.lab_id)
        _save_config_to_workspace(workspace, node.container_name, snapshot.content)

        self.db.flush()
        logger.info(
            f"Set active config for node {node.container_name}: "
            f"snapshot {snapshot_id} ({snapshot.snapshot_type})"
        )
        return node

    def map_config(
        self,
        snapshot_id: str,
        target_node_id: str,
        set_as_active: bool = False,
    ) -> models.ConfigSnapshot:
        """Map an orphaned config snapshot to a target node.

        Sets mapped_to_node_id on the snapshot. Optionally sets
        the snapshot as the target node's active config.
        """
        snapshot = self.db.query(models.ConfigSnapshot).get(snapshot_id)
        if not snapshot:
            raise ValueError(f"Snapshot not found: {snapshot_id}")

        target_node = self.db.query(models.Node).get(target_node_id)
        if not target_node:
            raise ValueError(f"Target node not found: {target_node_id}")

        if snapshot.lab_id != target_node.lab_id:
            raise ValueError("Snapshot and target node must be in the same lab")

        # Check device_kind compatibility (warn, don't block)
        warning = None
        if snapshot.device_kind and target_node.device:
            if snapshot.device_kind != target_node.device:
                warning = (
                    f"Device type mismatch: config is from '{snapshot.device_kind}' "
                    f"but target node is '{target_node.device}'"
                )
                logger.warning(f"Config mapping: {warning}")

        snapshot.mapped_to_node_id = target_node_id
        self.db.flush()

        if set_as_active:
            self.set_active_config(target_node_id, snapshot_id)

        logger.info(
            f"Mapped config snapshot {snapshot_id} ({snapshot.node_name}) "
            f"to node {target_node.container_name}"
        )
        return snapshot

    def delete_configs(
        self,
        lab_id: str,
        node_name: str | None = None,
        orphaned_only: bool = False,
        snapshot_ids: list[str] | None = None,
        force: bool = False,
    ) -> dict:
        """Bulk delete config snapshots with active config guard.

        Deletes matching snapshots and their workspace files.
        If any snapshot is an active startup-config, raises
        ActiveConfigGuardError unless force=True.

        Returns dict with deleted_count and details.
        """
        # Build query for snapshots to delete
        query = (
            self.db.query(models.ConfigSnapshot)
            .filter(models.ConfigSnapshot.lab_id == lab_id)
        )

        if snapshot_ids:
            query = query.filter(models.ConfigSnapshot.id.in_(snapshot_ids))
        elif node_name:
            query = query.filter(models.ConfigSnapshot.node_name == node_name)

        snapshots_to_delete = query.all()

        # Filter for orphaned-only if requested
        if orphaned_only:
            current_node_names = {
                n.container_name
                for n in self.db.query(models.Node)
                .filter(models.Node.lab_id == lab_id)
                .all()
            }
            snapshots_to_delete = [
                s for s in snapshots_to_delete
                if s.node_name not in current_node_names
            ]

        if not snapshots_to_delete:
            return {"deleted_count": 0, "details": "No matching snapshots found"}

        # Active config guard
        if not force:
            active_snapshot_ids = {
                n.active_config_snapshot_id
                for n in self.db.query(models.Node)
                .filter(models.Node.lab_id == lab_id)
                .all()
                if n.active_config_snapshot_id
            }
            active_conflicts = [
                {"snapshot_id": s.id, "node_name": s.node_name}
                for s in snapshots_to_delete
                if s.id in active_snapshot_ids
            ]
            if active_conflicts:
                raise ActiveConfigGuardError(active_conflicts)

        # Collect node_names for workspace cleanup
        node_names_to_clean = set()
        snapshot_ids_to_delete = []
        for s in snapshots_to_delete:
            snapshot_ids_to_delete.append(s.id)
            node_names_to_clean.add(s.node_name)

        # Clear active_config_snapshot_id references before deleting
        self.db.query(models.Node).filter(
            models.Node.lab_id == lab_id,
            models.Node.active_config_snapshot_id.in_(snapshot_ids_to_delete),
        ).update(
            {models.Node.active_config_snapshot_id: None},
            synchronize_session="fetch",
        )

        # Delete snapshots
        deleted_count = (
            self.db.query(models.ConfigSnapshot)
            .filter(models.ConfigSnapshot.id.in_(snapshot_ids_to_delete))
            .delete(synchronize_session="fetch")
        )

        # Workspace cleanup: remove config dirs for nodes with no remaining snapshots
        workspace = lab_workspace(lab_id)
        cleaned_dirs = []
        for nn in node_names_to_clean:
            remaining = (
                self.db.query(models.ConfigSnapshot)
                .filter(
                    models.ConfigSnapshot.lab_id == lab_id,
                    models.ConfigSnapshot.node_name == nn,
                )
                .count()
            )
            if remaining == 0:
                config_dir = workspace / "configs" / nn
                if config_dir.exists():
                    shutil.rmtree(config_dir)
                    cleaned_dirs.append(nn)

        self.db.flush()
        logger.info(
            f"Deleted {deleted_count} config snapshots for lab {lab_id}. "
            f"Cleaned workspace dirs: {cleaned_dirs}"
        )
        return {
            "deleted_count": deleted_count,
            "cleaned_workspace_dirs": cleaned_dirs,
        }

    def build_download_zip(
        self,
        lab_id: str,
        node_names: list[str] | None = None,
        include_orphaned: bool = False,
        all_configs: bool = False,
    ) -> io.BytesIO:
        """Build a zip file containing config snapshots with metadata.

        Zip structure:
            {node_name}/{timestamp}_{type}_startup-config
            {node_name}/metadata.json

        Raises ValueError if zip would exceed MAX_ZIP_SIZE_BYTES.
        """
        query = (
            self.db.query(models.ConfigSnapshot)
            .filter(models.ConfigSnapshot.lab_id == lab_id)
        )

        if not all_configs and node_names:
            query = query.filter(models.ConfigSnapshot.node_name.in_(node_names))

        if not include_orphaned and not all_configs:
            current_node_names = {
                n.container_name
                for n in self.db.query(models.Node)
                .filter(models.Node.lab_id == lab_id)
                .all()
            }
            query = query.filter(
                models.ConfigSnapshot.node_name.in_(current_node_names)
            )

        snapshots = query.order_by(
            models.ConfigSnapshot.node_name,
            models.ConfigSnapshot.created_at.desc(),
        ).all()

        if not snapshots:
            raise ValueError("No config snapshots found matching the criteria")

        # Estimate size
        total_size = sum(len(s.content.encode()) for s in snapshots)
        if total_size > MAX_ZIP_SIZE_BYTES:
            raise ValueError(
                f"Download would exceed {MAX_ZIP_SIZE_BYTES // (1024*1024)}MB limit "
                f"({total_size // (1024*1024)}MB estimated). "
                f"Try downloading fewer nodes."
            )

        # Build zip
        buf = io.BytesIO()
        metadata_by_node: dict[str, list[dict]] = {}

        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for snap in snapshots:
                ts = snap.created_at.strftime("%Y%m%d_%H%M%S") if snap.created_at else "unknown"
                filename = f"{snap.node_name}/{ts}_{snap.snapshot_type}_startup-config"
                zf.writestr(filename, snap.content)

                # Build metadata
                metadata_by_node.setdefault(snap.node_name, []).append({
                    "id": snap.id,
                    "timestamp": snap.created_at.isoformat() if snap.created_at else None,
                    "type": snap.snapshot_type,
                    "content_hash": snap.content_hash,
                    "device_kind": snap.device_kind,
                })

            # Write metadata per node
            for nn, entries in metadata_by_node.items():
                zf.writestr(
                    f"{nn}/metadata.json",
                    json.dumps(entries, indent=2, default=str),
                )

        buf.seek(0)
        return buf

    # -------------------------------------------------------------------------
    # Startup config resolution for deployment
    # -------------------------------------------------------------------------

    def resolve_startup_config(self, node: models.Node) -> str | None:
        """Resolve the startup-config content for a node deployment.

        Priority chain:
        1. active_config_snapshot_id (explicit user selection)
        2. Node.config_json["startup-config"] (from extraction)
        3. Latest ConfigSnapshot for this node (fallback)
        4. None (agent will generate minimal config)

        Logs which source was used for debugging.
        """
        # Priority 1: Explicit active snapshot
        if node.active_config_snapshot_id:
            snapshot = self.db.query(models.ConfigSnapshot).get(
                node.active_config_snapshot_id
            )
            if snapshot and snapshot.content:
                logger.debug(
                    f"Config for {node.container_name}: "
                    f"using active snapshot {snapshot.id}"
                )
                return snapshot.content
            else:
                logger.warning(
                    f"Config for {node.container_name}: "
                    f"active snapshot {node.active_config_snapshot_id} not found, "
                    f"falling back"
                )

        # Priority 2: config_json["startup-config"]
        if node.config_json:
            try:
                config = json.loads(node.config_json)
                startup = config.get("startup-config")
                if startup:
                    logger.debug(
                        f"Config for {node.container_name}: "
                        f"using config_json startup-config"
                    )
                    return startup
            except json.JSONDecodeError:
                pass

        # Priority 3: Latest snapshot
        if node.lab_id:
            latest = (
                self.db.query(models.ConfigSnapshot)
                .filter(
                    models.ConfigSnapshot.lab_id == node.lab_id,
                    models.ConfigSnapshot.node_name == node.container_name,
                )
                .order_by(models.ConfigSnapshot.created_at.desc())
                .first()
            )
            if latest and latest.content:
                logger.debug(
                    f"Config for {node.container_name}: "
                    f"using latest snapshot {latest.id}"
                )
                return latest.content

        # Priority 4: No config available
        logger.debug(
            f"Config for {node.container_name}: "
            f"no config found, will use minimal/generated"
        )
        return None


# -------------------------------------------------------------------------
# Helper functions
# -------------------------------------------------------------------------

def _save_config_to_workspace(workspace: Path, node_name: str, content: str) -> None:
    """Save a config file to the workspace filesystem."""
    configs_dir = workspace / "configs"
    configs_dir.mkdir(parents=True, exist_ok=True)
    node_config_dir = configs_dir / node_name
    node_config_dir.mkdir(parents=True, exist_ok=True)
    config_file = node_config_dir / "startup-config"
    config_file.write_text(content, encoding="utf-8")
