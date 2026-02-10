"""HTTP helper utilities."""

from __future__ import annotations

from fastapi import HTTPException


def require_admin(user, message: str = "Admin access required") -> None:
    """Raise 403 if user is not an admin."""
    if not getattr(user, "is_admin", False):
        raise HTTPException(status_code=403, detail=message)


def require_lab_owner(user, lab, message: str = "Access denied") -> None:
    """Raise 403 if user is not the lab owner or an admin."""
    if getattr(user, "is_admin", False):
        return
    if getattr(lab, "owner_id", None) != getattr(user, "id", None):
        raise HTTPException(status_code=403, detail=message)


def raise_not_found(detail: str = "Not found") -> None:
    """Raise a standardized 404 error."""
    raise HTTPException(status_code=404, detail=detail)


def raise_unavailable(detail: str = "Service unavailable") -> None:
    """Raise a standardized 503 error."""
    raise HTTPException(status_code=503, detail=detail)
