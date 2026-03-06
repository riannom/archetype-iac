"""Tests for RBAC role enum hierarchy ordering."""
from __future__ import annotations
import sys
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.enums import GlobalRole, LabRole  # noqa: E402


class TestGlobalRoleComparisons:
    """Test all comparison operators across GlobalRole pairs."""

    @pytest.mark.parametrize("higher,lower", [
        (GlobalRole.SUPER_ADMIN, GlobalRole.ADMIN),
        (GlobalRole.SUPER_ADMIN, GlobalRole.OPERATOR),
        (GlobalRole.SUPER_ADMIN, GlobalRole.VIEWER),
        (GlobalRole.ADMIN, GlobalRole.OPERATOR),
        (GlobalRole.ADMIN, GlobalRole.VIEWER),
        (GlobalRole.OPERATOR, GlobalRole.VIEWER),
    ])
    def test_gt(self, higher, lower):
        assert higher > lower
        assert not lower > higher

    @pytest.mark.parametrize("higher,lower", [
        (GlobalRole.SUPER_ADMIN, GlobalRole.ADMIN),
        (GlobalRole.SUPER_ADMIN, GlobalRole.OPERATOR),
        (GlobalRole.SUPER_ADMIN, GlobalRole.VIEWER),
        (GlobalRole.ADMIN, GlobalRole.OPERATOR),
        (GlobalRole.ADMIN, GlobalRole.VIEWER),
        (GlobalRole.OPERATOR, GlobalRole.VIEWER),
    ])
    def test_ge(self, higher, lower):
        assert higher >= lower
        assert not lower >= higher

    @pytest.mark.parametrize("higher,lower", [
        (GlobalRole.SUPER_ADMIN, GlobalRole.ADMIN),
        (GlobalRole.SUPER_ADMIN, GlobalRole.OPERATOR),
        (GlobalRole.SUPER_ADMIN, GlobalRole.VIEWER),
        (GlobalRole.ADMIN, GlobalRole.OPERATOR),
        (GlobalRole.ADMIN, GlobalRole.VIEWER),
        (GlobalRole.OPERATOR, GlobalRole.VIEWER),
    ])
    def test_lt(self, higher, lower):
        assert lower < higher
        assert not higher < lower

    @pytest.mark.parametrize("higher,lower", [
        (GlobalRole.SUPER_ADMIN, GlobalRole.ADMIN),
        (GlobalRole.SUPER_ADMIN, GlobalRole.OPERATOR),
        (GlobalRole.SUPER_ADMIN, GlobalRole.VIEWER),
        (GlobalRole.ADMIN, GlobalRole.OPERATOR),
        (GlobalRole.ADMIN, GlobalRole.VIEWER),
        (GlobalRole.OPERATOR, GlobalRole.VIEWER),
    ])
    def test_le(self, higher, lower):
        assert lower <= higher
        assert not higher <= lower

    @pytest.mark.parametrize("role", list(GlobalRole))
    def test_ge_self(self, role):
        assert role >= role

    @pytest.mark.parametrize("role", list(GlobalRole))
    def test_le_self(self, role):
        assert role <= role

    @pytest.mark.parametrize("role", list(GlobalRole))
    def test_not_gt_self(self, role):
        assert not role > role

    @pytest.mark.parametrize("role", list(GlobalRole))
    def test_not_lt_self(self, role):
        assert not role < role


class TestLabRoleComparisons:
    """Test all comparison operators across LabRole pairs."""

    @pytest.mark.parametrize("higher,lower", [
        (LabRole.OWNER, LabRole.EDITOR),
        (LabRole.OWNER, LabRole.VIEWER),
        (LabRole.EDITOR, LabRole.VIEWER),
    ])
    def test_gt(self, higher, lower):
        assert higher > lower
        assert not lower > higher

    @pytest.mark.parametrize("higher,lower", [
        (LabRole.OWNER, LabRole.EDITOR),
        (LabRole.OWNER, LabRole.VIEWER),
        (LabRole.EDITOR, LabRole.VIEWER),
    ])
    def test_ge(self, higher, lower):
        assert higher >= lower
        assert not lower >= higher

    @pytest.mark.parametrize("higher,lower", [
        (LabRole.OWNER, LabRole.EDITOR),
        (LabRole.OWNER, LabRole.VIEWER),
        (LabRole.EDITOR, LabRole.VIEWER),
    ])
    def test_lt(self, higher, lower):
        assert lower < higher
        assert not higher < lower

    @pytest.mark.parametrize("higher,lower", [
        (LabRole.OWNER, LabRole.EDITOR),
        (LabRole.OWNER, LabRole.VIEWER),
        (LabRole.EDITOR, LabRole.VIEWER),
    ])
    def test_le(self, higher, lower):
        assert lower <= higher
        assert not higher <= lower

    @pytest.mark.parametrize("role", list(LabRole))
    def test_ge_self(self, role):
        assert role >= role

    @pytest.mark.parametrize("role", list(LabRole))
    def test_le_self(self, role):
        assert role <= role

    @pytest.mark.parametrize("role", list(LabRole))
    def test_not_gt_self(self, role):
        assert not role > role

    @pytest.mark.parametrize("role", list(LabRole))
    def test_not_lt_self(self, role):
        assert not role < role


class TestSameRoleEquality:
    """Test that identical enum members are equal."""

    @pytest.mark.parametrize("role", list(GlobalRole))
    def test_global_role_eq_self(self, role):
        assert role == role

    @pytest.mark.parametrize("role", list(LabRole))
    def test_lab_role_eq_self(self, role):
        assert role == role

    def test_different_global_roles_not_equal(self):
        assert GlobalRole.ADMIN != GlobalRole.VIEWER

    def test_different_lab_roles_not_equal(self):
        assert LabRole.OWNER != LabRole.VIEWER


class TestCrossEnumComparisons:
    """Comparisons between GlobalRole and LabRole return NotImplemented."""

    def test_global_gt_lab_returns_not_implemented(self):
        assert GlobalRole.ADMIN.__gt__(LabRole.OWNER) is NotImplemented

    def test_global_ge_lab_returns_not_implemented(self):
        assert GlobalRole.ADMIN.__ge__(LabRole.OWNER) is NotImplemented

    def test_global_lt_lab_returns_not_implemented(self):
        assert GlobalRole.ADMIN.__lt__(LabRole.OWNER) is NotImplemented

    def test_global_le_lab_returns_not_implemented(self):
        assert GlobalRole.ADMIN.__le__(LabRole.OWNER) is NotImplemented

    def test_lab_gt_global_returns_not_implemented(self):
        assert LabRole.OWNER.__gt__(GlobalRole.ADMIN) is NotImplemented

    def test_lab_ge_global_returns_not_implemented(self):
        assert LabRole.OWNER.__ge__(GlobalRole.ADMIN) is NotImplemented

    def test_lab_lt_global_returns_not_implemented(self):
        assert LabRole.OWNER.__lt__(GlobalRole.ADMIN) is NotImplemented

    def test_lab_le_global_returns_not_implemented(self):
        assert LabRole.OWNER.__le__(GlobalRole.ADMIN) is NotImplemented


class TestNonRoleComparisons:
    """Comparisons against non-enum types return NotImplemented."""

    def test_global_gt_int_returns_not_implemented(self):
        assert GlobalRole.ADMIN.__gt__(1) is NotImplemented

    def test_global_ge_int_returns_not_implemented(self):
        assert GlobalRole.ADMIN.__ge__(1) is NotImplemented

    def test_global_lt_int_returns_not_implemented(self):
        assert GlobalRole.ADMIN.__lt__(1) is NotImplemented

    def test_global_le_int_returns_not_implemented(self):
        assert GlobalRole.ADMIN.__le__(1) is NotImplemented

    def test_lab_gt_str_returns_not_implemented(self):
        assert LabRole.OWNER.__gt__("owner") is NotImplemented

    def test_lab_ge_str_returns_not_implemented(self):
        assert LabRole.OWNER.__ge__("owner") is NotImplemented

    def test_lab_lt_str_returns_not_implemented(self):
        assert LabRole.OWNER.__lt__("owner") is NotImplemented

    def test_lab_le_str_returns_not_implemented(self):
        assert LabRole.OWNER.__le__("owner") is NotImplemented


class TestStringValues:
    """Enum .value matches expected string."""

    def test_global_super_admin(self):
        assert GlobalRole.SUPER_ADMIN.value == "super_admin"

    def test_global_admin(self):
        assert GlobalRole.ADMIN.value == "admin"

    def test_global_operator(self):
        assert GlobalRole.OPERATOR.value == "operator"

    def test_global_viewer(self):
        assert GlobalRole.VIEWER.value == "viewer"

    def test_lab_owner(self):
        assert LabRole.OWNER.value == "owner"

    def test_lab_editor(self):
        assert LabRole.EDITOR.value == "editor"

    def test_lab_viewer(self):
        assert LabRole.VIEWER.value == "viewer"

    def test_global_role_is_str(self):
        assert isinstance(GlobalRole.ADMIN, str)

    def test_lab_role_is_str(self):
        assert isinstance(LabRole.OWNER, str)

    def test_global_role_str_equality(self):
        assert GlobalRole.ADMIN == "admin"

    def test_lab_role_str_equality(self):
        assert LabRole.OWNER == "owner"
