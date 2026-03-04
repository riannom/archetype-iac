"""Tests for app/services/permissions.py — PermissionService RBAC logic."""
from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app import models
from app.auth import hash_password
from app.enums import GlobalRole, LabRole
from app.services.permissions import PermissionService


def _make_user(
    test_db: Session,
    *,
    global_role: str = "operator",
    username: str | None = None,
) -> models.User:
    """Create a user with the given global role."""
    user = models.User(
        username=username or f"user-{uuid4().hex[:8]}",
        email=f"{uuid4().hex[:8]}@example.com",
        hashed_password=hash_password("password"),
        is_active=True,
        global_role=global_role,
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)
    return user


def _make_lab(test_db: Session, owner: models.User) -> models.Lab:
    """Create a lab owned by the given user."""
    lab = models.Lab(
        name=f"Lab-{uuid4().hex[:8]}",
        owner_id=owner.id,
        provider="docker",
        state="stopped",
        workspace_path="/tmp/test",
    )
    test_db.add(lab)
    test_db.commit()
    test_db.refresh(lab)
    return lab


def _make_permission(
    test_db: Session, lab: models.Lab, user: models.User, role: str,
) -> models.Permission:
    """Create an explicit Permission record."""
    perm = models.Permission(
        lab_id=lab.id,
        user_id=user.id,
        role=role,
    )
    test_db.add(perm)
    test_db.commit()
    test_db.refresh(perm)
    return perm


# ── get_effective_lab_role ──────────────────────────────────────────────


class TestGetEffectiveLabRole:
    """Tests for PermissionService.get_effective_lab_role()."""

    def test_global_admin_gets_owner_access(self, test_db: Session):
        """Global admin should have owner-level access to any lab."""
        admin = _make_user(test_db, global_role="admin")
        owner = _make_user(test_db, global_role="operator")
        lab = _make_lab(test_db, owner)

        role = PermissionService.get_effective_lab_role(admin, lab, test_db)
        assert role == LabRole.OWNER

    def test_global_super_admin_gets_owner_access(self, test_db: Session):
        """Global super_admin should also have owner-level access."""
        super_admin = _make_user(test_db, global_role="super_admin")
        owner = _make_user(test_db, global_role="operator")
        lab = _make_lab(test_db, owner)

        role = PermissionService.get_effective_lab_role(super_admin, lab, test_db)
        assert role == LabRole.OWNER

    def test_lab_owner_gets_owner_role(self, test_db: Session):
        """The lab owner should get the owner role."""
        user = _make_user(test_db, global_role="operator")
        lab = _make_lab(test_db, user)

        role = PermissionService.get_effective_lab_role(user, lab, test_db)
        assert role == LabRole.OWNER

    def test_explicit_editor_permission(self, test_db: Session):
        """User with explicit editor permission gets editor role."""
        owner = _make_user(test_db, global_role="operator")
        editor_user = _make_user(test_db, global_role="operator")
        lab = _make_lab(test_db, owner)
        _make_permission(test_db, lab, editor_user, "editor")

        role = PermissionService.get_effective_lab_role(editor_user, lab, test_db)
        assert role == LabRole.EDITOR

    def test_explicit_viewer_permission(self, test_db: Session):
        """User with explicit viewer permission gets viewer role."""
        owner = _make_user(test_db, global_role="operator")
        viewer_user = _make_user(test_db, global_role="operator")
        lab = _make_lab(test_db, owner)
        _make_permission(test_db, lab, viewer_user, "viewer")

        role = PermissionService.get_effective_lab_role(viewer_user, lab, test_db)
        assert role == LabRole.VIEWER

    def test_explicit_owner_permission(self, test_db: Session):
        """User with explicit owner permission gets owner role."""
        owner = _make_user(test_db, global_role="operator")
        co_owner = _make_user(test_db, global_role="operator")
        lab = _make_lab(test_db, owner)
        _make_permission(test_db, lab, co_owner, "owner")

        role = PermissionService.get_effective_lab_role(co_owner, lab, test_db)
        assert role == LabRole.OWNER

    def test_unknown_role_string_falls_back_to_viewer(self, test_db: Session):
        """Permission with unknown role string falls back to viewer."""
        owner = _make_user(test_db, global_role="operator")
        other = _make_user(test_db, global_role="operator")
        lab = _make_lab(test_db, owner)
        _make_permission(test_db, lab, other, "unknown_role_xyz")

        role = PermissionService.get_effective_lab_role(other, lab, test_db)
        assert role == LabRole.VIEWER

    def test_no_permission_returns_none(self, test_db: Session):
        """User with no relationship to a lab gets None."""
        owner = _make_user(test_db, global_role="operator")
        stranger = _make_user(test_db, global_role="operator")
        lab = _make_lab(test_db, owner)

        role = PermissionService.get_effective_lab_role(stranger, lab, test_db)
        assert role is None

    def test_priority_admin_over_explicit_permission(self, test_db: Session):
        """Global admin bypasses explicit permission check entirely."""
        admin = _make_user(test_db, global_role="admin")
        owner = _make_user(test_db, global_role="operator")
        lab = _make_lab(test_db, owner)
        # Even if admin has a viewer permission record, admin trumps
        _make_permission(test_db, lab, admin, "viewer")

        role = PermissionService.get_effective_lab_role(admin, lab, test_db)
        assert role == LabRole.OWNER

    def test_viewer_global_role_no_lab_access(self, test_db: Session):
        """Global viewer without explicit permission gets None."""
        owner = _make_user(test_db, global_role="operator")
        viewer = _make_user(test_db, global_role="viewer")
        lab = _make_lab(test_db, owner)

        role = PermissionService.get_effective_lab_role(viewer, lab, test_db)
        assert role is None


# ── require_lab_role ────────────────────────────────────────────────────


class TestRequireLabRole:
    """Tests for PermissionService.require_lab_role()."""

    def test_access_denied_no_role(self, test_db: Session):
        """Raises 403 when user has no access to the lab."""
        owner = _make_user(test_db, global_role="operator")
        stranger = _make_user(test_db, global_role="operator")
        lab = _make_lab(test_db, owner)

        with pytest.raises(HTTPException) as exc_info:
            PermissionService.require_lab_role(
                stranger, lab, test_db, LabRole.VIEWER,
            )
        assert exc_info.value.status_code == 403
        assert "Access denied" in str(exc_info.value.detail)

    def test_role_below_minimum_raises_403(self, test_db: Session):
        """Raises 403 when user's role is below minimum required."""
        owner = _make_user(test_db, global_role="operator")
        viewer_user = _make_user(test_db, global_role="operator")
        lab = _make_lab(test_db, owner)
        _make_permission(test_db, lab, viewer_user, "viewer")

        with pytest.raises(HTTPException) as exc_info:
            PermissionService.require_lab_role(
                viewer_user, lab, test_db, LabRole.EDITOR,
            )
        assert exc_info.value.status_code == 403
        assert "editor" in str(exc_info.value.detail).lower()

    def test_role_below_minimum_custom_message(self, test_db: Session):
        """Custom message is used when role is below minimum."""
        owner = _make_user(test_db, global_role="operator")
        viewer_user = _make_user(test_db, global_role="operator")
        lab = _make_lab(test_db, owner)
        _make_permission(test_db, lab, viewer_user, "viewer")

        with pytest.raises(HTTPException) as exc_info:
            PermissionService.require_lab_role(
                viewer_user, lab, test_db, LabRole.OWNER,
                message="You need to be an owner",
            )
        assert exc_info.value.status_code == 403
        assert "You need to be an owner" in str(exc_info.value.detail)

    def test_sufficient_role_returns_effective_role(self, test_db: Session):
        """Returns the effective role when access is granted."""
        owner = _make_user(test_db, global_role="operator")
        editor_user = _make_user(test_db, global_role="operator")
        lab = _make_lab(test_db, owner)
        _make_permission(test_db, lab, editor_user, "editor")

        result = PermissionService.require_lab_role(
            editor_user, lab, test_db, LabRole.VIEWER,
        )
        assert result == LabRole.EDITOR

    def test_owner_passes_any_minimum(self, test_db: Session):
        """Lab owner passes any minimum role check."""
        owner = _make_user(test_db, global_role="operator")
        lab = _make_lab(test_db, owner)

        result = PermissionService.require_lab_role(
            owner, lab, test_db, LabRole.OWNER,
        )
        assert result == LabRole.OWNER

    def test_exact_role_match_passes(self, test_db: Session):
        """User with exactly the minimum role passes."""
        owner = _make_user(test_db, global_role="operator")
        editor_user = _make_user(test_db, global_role="operator")
        lab = _make_lab(test_db, owner)
        _make_permission(test_db, lab, editor_user, "editor")

        result = PermissionService.require_lab_role(
            editor_user, lab, test_db, LabRole.EDITOR,
        )
        assert result == LabRole.EDITOR


# ── is_admin_or_above ───────────────────────────────────────────────────


class TestIsAdminOrAbove:
    """Tests for PermissionService.is_admin_or_above()."""

    def test_admin_returns_true(self, test_db: Session):
        user = _make_user(test_db, global_role="admin")
        assert PermissionService.is_admin_or_above(user) is True

    def test_super_admin_returns_true(self, test_db: Session):
        user = _make_user(test_db, global_role="super_admin")
        assert PermissionService.is_admin_or_above(user) is True

    def test_operator_returns_false(self, test_db: Session):
        user = _make_user(test_db, global_role="operator")
        assert PermissionService.is_admin_or_above(user) is False

    def test_viewer_returns_false(self, test_db: Session):
        user = _make_user(test_db, global_role="viewer")
        assert PermissionService.is_admin_or_above(user) is False

    def test_invalid_role_returns_false(self, test_db: Session):
        """User with an invalid role string defaults to operator (not admin)."""
        user = _make_user(test_db, global_role="bogus_role")
        assert PermissionService.is_admin_or_above(user) is False
