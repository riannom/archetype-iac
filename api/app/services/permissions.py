"""Centralized RBAC permission service."""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app import models
from app.enums import GlobalRole, LabRole


class PermissionService:
    """Centralizes all permission checks for RBAC enforcement."""

    @staticmethod
    def get_user_global_role(user: models.User) -> GlobalRole:
        """Get the user's GlobalRole enum from the stored string."""
        try:
            return GlobalRole(user.global_role)
        except (ValueError, AttributeError):
            return GlobalRole.OPERATOR

    @staticmethod
    def get_effective_lab_role(
        user: models.User, lab: models.Lab, db: Session
    ) -> LabRole | None:
        """Determine the user's effective role for a specific lab.

        Priority:
        1. Global admin/super_admin -> owner-level access to all labs
        2. Lab owner -> owner
        3. Explicit permission record -> viewer/editor/owner from record
        4. None -> no access

        Returns:
            LabRole if user has access, None if no access.
        """
        role = PermissionService.get_user_global_role(user)

        # Global admins get owner-level access to all labs
        if role >= GlobalRole.ADMIN:
            return LabRole.OWNER

        # Lab owner
        if lab.owner_id == user.id:
            return LabRole.OWNER

        # Check explicit permission
        perm = (
            db.query(models.Permission)
            .filter(
                models.Permission.lab_id == lab.id,
                models.Permission.user_id == user.id,
            )
            .first()
        )
        if perm:
            try:
                return LabRole(perm.role)
            except ValueError:
                # Unknown role string — treat as viewer
                return LabRole.VIEWER

        return None

    @staticmethod
    def require_global_role(
        user: models.User,
        minimum: GlobalRole,
        message: str | None = None,
    ) -> None:
        """Raise 403 if user's global_role is below minimum."""
        role = PermissionService.get_user_global_role(user)
        if role < minimum:
            detail = message or f"{minimum.value} access required"
            raise HTTPException(status_code=403, detail=detail)

    @staticmethod
    def require_lab_role(
        user: models.User,
        lab: models.Lab,
        db: Session,
        minimum: LabRole,
        message: str | None = None,
    ) -> LabRole:
        """Raise 403 if user's effective lab role is below minimum.

        Returns the effective role on success (useful for callers that
        want to know the actual role).
        """
        role = PermissionService.get_effective_lab_role(user, lab, db)
        if role is None:
            raise HTTPException(status_code=403, detail="Access denied")
        if role < minimum:
            detail = message or f"{minimum.value} access required"
            raise HTTPException(status_code=403, detail=detail)
        return role

    @staticmethod
    def is_admin_or_above(user: models.User) -> bool:
        """Check if user has admin or super_admin global role."""
        role = PermissionService.get_user_global_role(user)
        return role >= GlobalRole.ADMIN
