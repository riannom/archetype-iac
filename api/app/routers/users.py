"""User management endpoints (admin-only, except self-service password change)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app import db, models, schemas
from app.auth import get_current_user, hash_password, verify_password
from app.enums import GlobalRole
from app.services.audit import AuditService
from app.services.permissions import PermissionService

router = APIRouter(prefix="/users", tags=["users"])


def _get_client_ip(request: Request) -> str | None:
    """Extract client IP from request."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


@router.get("", response_model=schemas.UserListResponse)
def list_users(
    skip: int = 0,
    limit: int = 50,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.UserListResponse:
    """List all users. Requires admin role."""
    PermissionService.require_global_role(current_user, GlobalRole.ADMIN)
    total = database.query(models.User).count()
    users = (
        database.query(models.User)
        .order_by(models.User.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return schemas.UserListResponse(
        users=[schemas.UserOut.model_validate(u) for u in users],
        total=total,
    )


@router.post("", response_model=schemas.UserOut)
def create_user(
    payload: schemas.UserCreateAdmin,
    request: Request,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.UserOut:
    """Create a new user. Requires admin role."""
    PermissionService.require_global_role(current_user, GlobalRole.ADMIN)

    # Only super_admin can create super_admin users
    if payload.global_role == GlobalRole.SUPER_ADMIN.value:
        PermissionService.require_global_role(current_user, GlobalRole.SUPER_ADMIN)

    username = payload.username.lower()

    # Check uniqueness
    if database.query(models.User).filter(models.User.username == username).first():
        raise HTTPException(status_code=409, detail="Username already taken")
    if payload.email:
        if database.query(models.User).filter(models.User.email == payload.email).first():
            raise HTTPException(status_code=409, detail="Email already registered")

    user = models.User(
        username=username,
        email=payload.email or f"{username}@local",
        hashed_password=hash_password(payload.password),
        global_role=payload.global_role,
        is_admin=payload.global_role in (GlobalRole.SUPER_ADMIN.value, GlobalRole.ADMIN.value),
    )
    database.add(user)
    database.flush()

    AuditService.log(
        database,
        "user_created",
        user_id=current_user.id,
        target_user_id=user.id,
        ip_address=_get_client_ip(request),
        details={"username": username, "global_role": payload.global_role},
    )
    database.commit()
    database.refresh(user)
    return schemas.UserOut.model_validate(user)


@router.get("/{user_id}", response_model=schemas.UserOut)
def get_user(
    user_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.UserOut:
    """Get user details. Admin can view any user; others can view self."""
    if user_id != current_user.id:
        PermissionService.require_global_role(current_user, GlobalRole.ADMIN)
    user = database.get(models.User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return schemas.UserOut.model_validate(user)


@router.patch("/{user_id}", response_model=schemas.UserOut)
def update_user(
    user_id: str,
    payload: schemas.UserUpdateAdmin,
    request: Request,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.UserOut:
    """Update user profile. Requires admin role."""
    PermissionService.require_global_role(current_user, GlobalRole.ADMIN)

    user = database.get(models.User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    changes: dict = {}

    if payload.email is not None:
        existing = database.query(models.User).filter(
            models.User.email == payload.email, models.User.id != user_id
        ).first()
        if existing:
            raise HTTPException(status_code=409, detail="Email already registered")
        changes["email"] = payload.email
        user.email = payload.email

    if payload.global_role is not None:
        # Only super_admin can assign super_admin role
        if payload.global_role == GlobalRole.SUPER_ADMIN.value:
            PermissionService.require_global_role(current_user, GlobalRole.SUPER_ADMIN)
        changes["global_role"] = {"from": user.global_role, "to": payload.global_role}
        user.global_role = payload.global_role
        user.is_admin = payload.global_role in (GlobalRole.SUPER_ADMIN.value, GlobalRole.ADMIN.value)

    AuditService.log(
        database,
        "user_updated",
        user_id=current_user.id,
        target_user_id=user_id,
        ip_address=_get_client_ip(request),
        details=changes,
    )
    database.commit()
    database.refresh(user)
    return schemas.UserOut.model_validate(user)


@router.put("/{user_id}/password")
def change_password(
    user_id: str,
    payload: schemas.PasswordChange,
    request: Request,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, str]:
    """Change password. Self: requires current_password. Admin: no current required."""
    user = database.get(models.User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    is_self = user_id == current_user.id
    if not is_self:
        PermissionService.require_global_role(current_user, GlobalRole.ADMIN)

    # Self-service requires current password
    if is_self:
        if not payload.current_password:
            raise HTTPException(status_code=400, detail="Current password required")
        if not verify_password(payload.current_password, user.hashed_password):
            raise HTTPException(status_code=400, detail="Current password is incorrect")

    user.hashed_password = hash_password(payload.new_password)

    AuditService.log(
        database,
        "password_changed",
        user_id=current_user.id,
        target_user_id=user_id,
        ip_address=_get_client_ip(request),
        details={"self_service": is_self},
    )
    database.commit()
    return {"status": "password_changed"}


@router.post("/{user_id}/deactivate")
def deactivate_user(
    user_id: str,
    request: Request,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, str]:
    """Deactivate a user. Cannot deactivate self."""
    PermissionService.require_global_role(current_user, GlobalRole.ADMIN)

    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot deactivate yourself")

    user = database.get(models.User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_active = False
    AuditService.log(
        database,
        "user_deactivated",
        user_id=current_user.id,
        target_user_id=user_id,
        ip_address=_get_client_ip(request),
    )
    database.commit()
    return {"status": "deactivated"}


@router.post("/{user_id}/activate")
def activate_user(
    user_id: str,
    request: Request,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, str]:
    """Activate a user."""
    PermissionService.require_global_role(current_user, GlobalRole.ADMIN)

    user = database.get(models.User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_active = True
    AuditService.log(
        database,
        "user_activated",
        user_id=current_user.id,
        target_user_id=user_id,
        ip_address=_get_client_ip(request),
    )
    database.commit()
    return {"status": "activated"}
