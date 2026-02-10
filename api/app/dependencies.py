"""FastAPI dependency functions for RBAC enforcement."""
from __future__ import annotations

from fastapi import Depends

from app import models
from app.auth import get_current_user
from app.enums import GlobalRole
from app.services.permissions import PermissionService


def require_operator_role(
    current_user: models.User = Depends(get_current_user),
) -> models.User:
    """Require the user to have at least operator global role."""
    PermissionService.require_global_role(current_user, GlobalRole.OPERATOR)
    return current_user


def require_admin_role(
    current_user: models.User = Depends(get_current_user),
) -> models.User:
    """Require the user to have at least admin global role."""
    PermissionService.require_global_role(current_user, GlobalRole.ADMIN)
    return current_user


def require_super_admin_role(
    current_user: models.User = Depends(get_current_user),
) -> models.User:
    """Require the user to have super_admin global role."""
    PermissionService.require_global_role(current_user, GlobalRole.SUPER_ADMIN)
    return current_user
