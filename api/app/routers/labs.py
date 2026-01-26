"""Lab CRUD and topology management endpoints."""
from __future__ import annotations

import shutil

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
