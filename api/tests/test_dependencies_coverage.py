"""Tests for RBAC dependency functions."""
from __future__ import annotations
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from fastapi import HTTPException

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.dependencies import require_operator_role, require_admin_role, require_super_admin_role  # noqa: E402
from app.enums import GlobalRole  # noqa: E402


def _make_user(role: GlobalRole) -> MagicMock:
    user = MagicMock()
    user.role = role
    return user


class TestRequireOperatorRole:
    """Tests for the require_operator_role dependency."""

    @patch("app.dependencies.PermissionService.require_global_role")
    def test_allows_operator(self, mock_require):
        user = _make_user(GlobalRole.OPERATOR)
        result = require_operator_role(current_user=user)
        mock_require.assert_called_once_with(user, GlobalRole.OPERATOR)
        assert result is user

    @patch("app.dependencies.PermissionService.require_global_role")
    def test_allows_admin(self, mock_require):
        user = _make_user(GlobalRole.ADMIN)
        result = require_operator_role(current_user=user)
        mock_require.assert_called_once_with(user, GlobalRole.OPERATOR)
        assert result is user

    @patch("app.dependencies.PermissionService.require_global_role")
    def test_allows_super_admin(self, mock_require):
        user = _make_user(GlobalRole.SUPER_ADMIN)
        result = require_operator_role(current_user=user)
        mock_require.assert_called_once_with(user, GlobalRole.OPERATOR)
        assert result is user

    @patch("app.dependencies.PermissionService.require_global_role")
    def test_rejects_viewer(self, mock_require):
        mock_require.side_effect = HTTPException(status_code=403)
        user = _make_user(GlobalRole.VIEWER)
        with pytest.raises(HTTPException) as exc_info:
            require_operator_role(current_user=user)
        assert exc_info.value.status_code == 403
        mock_require.assert_called_once_with(user, GlobalRole.OPERATOR)

    @patch("app.dependencies.PermissionService.require_global_role")
    def test_returns_user_object(self, mock_require):
        user = _make_user(GlobalRole.OPERATOR)
        result = require_operator_role(current_user=user)
        assert result is user


class TestRequireAdminRole:
    """Tests for the require_admin_role dependency."""

    @patch("app.dependencies.PermissionService.require_global_role")
    def test_allows_admin(self, mock_require):
        user = _make_user(GlobalRole.ADMIN)
        result = require_admin_role(current_user=user)
        mock_require.assert_called_once_with(user, GlobalRole.ADMIN)
        assert result is user

    @patch("app.dependencies.PermissionService.require_global_role")
    def test_allows_super_admin(self, mock_require):
        user = _make_user(GlobalRole.SUPER_ADMIN)
        result = require_admin_role(current_user=user)
        mock_require.assert_called_once_with(user, GlobalRole.ADMIN)
        assert result is user

    @patch("app.dependencies.PermissionService.require_global_role")
    def test_rejects_operator(self, mock_require):
        mock_require.side_effect = HTTPException(status_code=403)
        user = _make_user(GlobalRole.OPERATOR)
        with pytest.raises(HTTPException) as exc_info:
            require_admin_role(current_user=user)
        assert exc_info.value.status_code == 403
        mock_require.assert_called_once_with(user, GlobalRole.ADMIN)

    @patch("app.dependencies.PermissionService.require_global_role")
    def test_rejects_viewer(self, mock_require):
        mock_require.side_effect = HTTPException(status_code=403)
        user = _make_user(GlobalRole.VIEWER)
        with pytest.raises(HTTPException) as exc_info:
            require_admin_role(current_user=user)
        assert exc_info.value.status_code == 403
        mock_require.assert_called_once_with(user, GlobalRole.ADMIN)

    @patch("app.dependencies.PermissionService.require_global_role")
    def test_returns_user_object(self, mock_require):
        user = _make_user(GlobalRole.ADMIN)
        result = require_admin_role(current_user=user)
        assert result is user


class TestRequireSuperAdminRole:
    """Tests for the require_super_admin_role dependency."""

    @patch("app.dependencies.PermissionService.require_global_role")
    def test_allows_super_admin(self, mock_require):
        user = _make_user(GlobalRole.SUPER_ADMIN)
        result = require_super_admin_role(current_user=user)
        mock_require.assert_called_once_with(user, GlobalRole.SUPER_ADMIN)
        assert result is user

    @patch("app.dependencies.PermissionService.require_global_role")
    def test_rejects_admin(self, mock_require):
        mock_require.side_effect = HTTPException(status_code=403)
        user = _make_user(GlobalRole.ADMIN)
        with pytest.raises(HTTPException) as exc_info:
            require_super_admin_role(current_user=user)
        assert exc_info.value.status_code == 403
        mock_require.assert_called_once_with(user, GlobalRole.SUPER_ADMIN)

    @patch("app.dependencies.PermissionService.require_global_role")
    def test_rejects_operator(self, mock_require):
        mock_require.side_effect = HTTPException(status_code=403)
        user = _make_user(GlobalRole.OPERATOR)
        with pytest.raises(HTTPException) as exc_info:
            require_super_admin_role(current_user=user)
        assert exc_info.value.status_code == 403
        mock_require.assert_called_once_with(user, GlobalRole.SUPER_ADMIN)

    @patch("app.dependencies.PermissionService.require_global_role")
    def test_rejects_viewer(self, mock_require):
        mock_require.side_effect = HTTPException(status_code=403)
        user = _make_user(GlobalRole.VIEWER)
        with pytest.raises(HTTPException) as exc_info:
            require_super_admin_role(current_user=user)
        assert exc_info.value.status_code == 403
        mock_require.assert_called_once_with(user, GlobalRole.SUPER_ADMIN)

    @patch("app.dependencies.PermissionService.require_global_role")
    def test_returns_user_object(self, mock_require):
        user = _make_user(GlobalRole.SUPER_ADMIN)
        result = require_super_admin_role(current_user=user)
        assert result is user
