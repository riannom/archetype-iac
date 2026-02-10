"""HTTP helper utilities."""

from __future__ import annotations

from fastapi import HTTPException

from app.enums import GlobalRole, LabRole
from app.services.permissions import PermissionService


def require_admin(user, message: str = "Admin access required") -> None:
    """Raise 403 if user does not have admin-level global role."""
    PermissionService.require_global_role(user, GlobalRole.ADMIN, message)


def require_lab_owner(user, lab, message: str = "Access denied", db=None) -> None:
    """Raise 403 if user does not have owner-level access to the lab.

    Args:
        user: Current user model
        lab: Lab model
        message: Error message for 403
        db: SQLAlchemy session (required for RBAC permission lookup)
    """
    if db is None:
        raise ValueError("db session is required for RBAC permission checks")
    PermissionService.require_lab_role(user, lab, db, LabRole.OWNER, message)


def raise_not_found(detail: str = "Not found") -> None:
    """Raise a standardized 404 error."""
    raise HTTPException(status_code=404, detail=detail)


def raise_unavailable(detail: str = "Service unavailable") -> None:
    """Raise a standardized 503 error."""
    raise HTTPException(status_code=503, detail=detail)
