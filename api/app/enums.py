"""RBAC role enumerations with hierarchy ordering."""
from __future__ import annotations

from enum import Enum


class GlobalRole(str, Enum):
    """Global user roles with hierarchical ordering.

    Hierarchy: super_admin > admin > operator > viewer
    """

    SUPER_ADMIN = "super_admin"
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"

    @property
    def _rank(self) -> int:
        return _GLOBAL_ROLE_RANK[self]

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, GlobalRole):
            return NotImplemented
        return self._rank >= other._rank

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, GlobalRole):
            return NotImplemented
        return self._rank > other._rank

    def __le__(self, other: object) -> bool:
        if not isinstance(other, GlobalRole):
            return NotImplemented
        return self._rank <= other._rank

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, GlobalRole):
            return NotImplemented
        return self._rank < other._rank


class LabRole(str, Enum):
    """Per-lab roles with hierarchical ordering.

    Hierarchy: owner > editor > viewer
    """

    OWNER = "owner"
    EDITOR = "editor"
    VIEWER = "viewer"

    @property
    def _rank(self) -> int:
        return _LAB_ROLE_RANK[self]

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, LabRole):
            return NotImplemented
        return self._rank >= other._rank

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, LabRole):
            return NotImplemented
        return self._rank > other._rank

    def __le__(self, other: object) -> bool:
        if not isinstance(other, LabRole):
            return NotImplemented
        return self._rank <= other._rank

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, LabRole):
            return NotImplemented
        return self._rank < other._rank


_GLOBAL_ROLE_RANK = {
    GlobalRole.VIEWER: 1,
    GlobalRole.OPERATOR: 2,
    GlobalRole.ADMIN: 3,
    GlobalRole.SUPER_ADMIN: 4,
}

_LAB_ROLE_RANK = {
    LabRole.VIEWER: 1,
    LabRole.EDITOR: 2,
    LabRole.OWNER: 3,
}
