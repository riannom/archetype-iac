"""Lab permission management endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import db, models, schemas
from app.auth import get_current_user
from app.utils.lab import get_lab_or_404
from app.utils.http import require_lab_owner, raise_not_found

router = APIRouter(tags=["permissions"])


@router.get("/labs/{lab_id}/permissions")
def list_permissions(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, list[schemas.PermissionOut]]:
    get_lab_or_404(lab_id, database, current_user)
    permissions = database.query(models.Permission).filter(models.Permission.lab_id == lab_id).all()
    output = []
    for perm in permissions:
        user = database.get(models.User, perm.user_id)
        output.append(
            schemas.PermissionOut(
                id=perm.id,
                lab_id=perm.lab_id,
                user_id=perm.user_id,
                role=perm.role,
                created_at=perm.created_at,
                user_email=user.email if user else None,
                user_username=user.username if user else None,
            )
        )
    return {"permissions": output}


@router.post("/labs/{lab_id}/permissions")
def add_permission(
    lab_id: str,
    payload: schemas.PermissionCreate,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.PermissionOut:
    lab = get_lab_or_404(lab_id, database, current_user)
    require_lab_owner(current_user, lab, db=database)
    # Look up by username first, then email
    user = database.query(models.User).filter(models.User.username == payload.user_identifier.lower()).first()
    if not user:
        user = database.query(models.User).filter(models.User.email == payload.user_identifier).first()
    if not user:
        raise_not_found("User not found")
    permission = models.Permission(lab_id=lab_id, user_id=user.id, role=payload.role)
    database.add(permission)
    database.commit()
    database.refresh(permission)
    return schemas.PermissionOut(
        id=permission.id,
        lab_id=permission.lab_id,
        user_id=permission.user_id,
        role=permission.role,
        created_at=permission.created_at,
        user_email=user.email,
    )


@router.delete("/labs/{lab_id}/permissions/{permission_id}")
def delete_permission(
    lab_id: str,
    permission_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, str]:
    lab = get_lab_or_404(lab_id, database, current_user)
    require_lab_owner(current_user, lab, db=database)
    permission = database.get(models.Permission, permission_id)
    if not permission or permission.lab_id != lab_id:
        raise_not_found("Permission not found")
    database.delete(permission)
    database.commit()
    return {"status": "deleted"}
