"""Shared lab utility functions."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app import models


def get_lab_provider(lab: models.Lab) -> str:
    """Get the provider for a lab.

    Returns the lab's configured provider, defaulting to docker
    for backward compatibility with labs that don't have a provider set.
    """
    return lab.provider if lab.provider else "docker"


def get_lab_or_404(lab_id: str, database: Session, user: models.User) -> models.Lab:
    """Get a lab by ID, checking permissions.

    Raises HTTPException 404 if lab not found, 403 if access denied.
    """
    lab = database.get(models.Lab, lab_id)
    if not lab:
        raise HTTPException(status_code=404, detail="Lab not found")
    if lab.owner_id == user.id or user.is_admin:
        return lab
    allowed = (
        database.query(models.Permission)
        .filter(models.Permission.lab_id == lab_id, models.Permission.user_id == user.id)
        .count()
    )
    if not allowed:
        raise HTTPException(status_code=403, detail="Access denied")
    return lab


def update_lab_state(
    session: Session,
    lab_id: str,
    state: str,
    agent_id: str | None = None,
    error: str | None = None,
):
    """Update lab state in database."""
    lab = session.get(models.Lab, lab_id)
    if lab:
        lab.state = state
        lab.state_updated_at = datetime.now(timezone.utc)
        if agent_id is not None:
            lab.agent_id = agent_id
        if error is not None:
            lab.state_error = error
        elif state not in ("error", "unknown"):
            lab.state_error = None  # Clear error on success
        session.commit()
