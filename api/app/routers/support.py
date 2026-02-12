"""Offline support bundle endpoints."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app import db, models, schemas
from app.auth import get_current_user
from app.enums import GlobalRole
from app.services.permissions import PermissionService
from app.services.support_bundle import run_bundle_generation
from app.utils.async_tasks import safe_create_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/support-bundles", tags=["support"])


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


@router.post("", response_model=schemas.SupportBundleOut)
async def create_support_bundle(
    payload: schemas.SupportBundleCreate,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.SupportBundleOut:
    """Create a support bundle job and start async generation."""
    PermissionService.require_global_role(current_user, GlobalRole.SUPER_ADMIN)

    incident = {
        "summary": payload.summary,
        "repro_steps": payload.repro_steps,
        "expected_behavior": payload.expected_behavior,
        "actual_behavior": payload.actual_behavior,
        "incident_started_at": payload.incident_started_at.isoformat() if payload.incident_started_at else None,
        "incident_ended_at": payload.incident_ended_at.isoformat() if payload.incident_ended_at else None,
        "requested_by": {
            "user_id": current_user.id,
            "username": current_user.username,
            "at": _now_utc().isoformat(),
        },
    }
    options = {
        "impacted_lab_ids": payload.impacted_lab_ids,
        "impacted_agent_ids": payload.impacted_agent_ids,
    }

    bundle = models.SupportBundle(
        user_id=current_user.id,
        status="pending",
        include_configs=payload.include_configs,
        pii_safe=payload.pii_safe,
        time_window_hours=payload.time_window_hours,
        options_json=json.dumps(options),
        incident_json=json.dumps(incident),
    )
    database.add(bundle)
    database.commit()
    database.refresh(bundle)

    safe_create_task(
        run_bundle_generation(bundle.id),
        name=f"support-bundle:{bundle.id}",
    )
    return schemas.SupportBundleOut.model_validate(bundle)


@router.get("/{bundle_id}", response_model=schemas.SupportBundleOut)
def get_support_bundle(
    bundle_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.SupportBundleOut:
    """Get support bundle generation status."""
    PermissionService.require_global_role(current_user, GlobalRole.SUPER_ADMIN)
    bundle = database.get(models.SupportBundle, bundle_id)
    if not bundle:
        raise HTTPException(status_code=404, detail="Support bundle not found")
    return schemas.SupportBundleOut.model_validate(bundle)


@router.get("/{bundle_id}/download")
def download_support_bundle(
    bundle_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> FileResponse:
    """Download a completed support bundle zip."""
    PermissionService.require_global_role(current_user, GlobalRole.SUPER_ADMIN)
    bundle = database.get(models.SupportBundle, bundle_id)
    if not bundle:
        raise HTTPException(status_code=404, detail="Support bundle not found")
    if bundle.status != "completed":
        raise HTTPException(status_code=409, detail=f"Support bundle is {bundle.status}")
    if not bundle.file_path:
        raise HTTPException(status_code=404, detail="Bundle file missing")

    path = Path(bundle.file_path)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Bundle file missing")

    filename = f"archetype_support_bundle_{bundle.id}.zip"
    return FileResponse(
        path=str(path),
        media_type="application/zip",
        filename=filename,
    )


@router.get("", response_model=list[schemas.SupportBundleOut])
def list_support_bundles(
    limit: int = 20,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> list[schemas.SupportBundleOut]:
    """List recently generated support bundles."""
    PermissionService.require_global_role(current_user, GlobalRole.SUPER_ADMIN)
    limit = max(1, min(limit, 100))
    seven_days_ago = _now_utc() - timedelta(days=7)
    bundles = (
        database.query(models.SupportBundle)
        .filter(models.SupportBundle.created_at >= seven_days_ago)
        .order_by(models.SupportBundle.created_at.desc())
        .limit(limit)
        .all()
    )
    return [schemas.SupportBundleOut.model_validate(bundle) for bundle in bundles]
