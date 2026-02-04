"""Shared lab utility functions."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app import models

if TYPE_CHECKING:
    from collections.abc import Mapping


def find_lab_by_prefix(
    prefix: str,
    labs_by_id: Mapping[str, models.Lab] | Mapping[str, str],
    labs_by_prefix: Mapping[str, str] | Mapping[str, tuple[str, str]] | None = None,
) -> str | None:
    """Find lab ID by prefix match.

    Containerlab truncates lab IDs to ~20 characters, so container names
    may only have a prefix of the actual lab ID. This function handles
    both exact matches and prefix-based lookups.

    Args:
        prefix: The lab ID or truncated prefix to search for
        labs_by_id: Dict mapping lab IDs to Lab objects or lab names
        labs_by_prefix: Optional dict mapping truncated prefixes to lab IDs
                       (or tuples of (lab_id, lab_name))

    Returns:
        The full lab ID if found, None otherwise
    """
    if not prefix:
        return None
    # Try exact match first
    if prefix in labs_by_id:
        return prefix
    # Try prefix lookup
    if labs_by_prefix and prefix in labs_by_prefix:
        value = labs_by_prefix[prefix]
        # Handle both str and tuple[str, str] formats
        return value[0] if isinstance(value, tuple) else value
    # Try partial prefix match
    for lab_id in labs_by_id:
        if lab_id.startswith(prefix):
            return lab_id
    return None


def find_lab_with_name(
    prefix: str,
    labs_by_id: Mapping[str, str],
    labs_by_prefix: Mapping[str, tuple[str, str]] | None = None,
) -> tuple[str | None, str | None]:
    """Find lab ID and name by prefix match.

    Similar to find_lab_by_prefix but returns both the lab ID and name.

    Args:
        prefix: The lab ID or truncated prefix to search for
        labs_by_id: Dict mapping lab IDs to lab names
        labs_by_prefix: Optional dict mapping truncated prefixes to (lab_id, lab_name) tuples

    Returns:
        Tuple of (lab_id, lab_name), or (None, None) if not found
    """
    if not prefix:
        return None, None
    # Try exact match first
    if prefix in labs_by_id:
        return prefix, labs_by_id[prefix]
    # Try prefix lookup
    if labs_by_prefix and prefix in labs_by_prefix:
        return labs_by_prefix[prefix]
    # Try partial prefix match
    for lab_id, lab_name in labs_by_id.items():
        if lab_id.startswith(prefix):
            return lab_id, lab_name
    return None, None


def get_lab_provider(lab: models.Lab) -> str:
    """Get the provider for a lab.

    Returns the lab's configured provider, defaulting to docker
    for backward compatibility with labs that don't have a provider set.
    """
    return lab.provider if lab.provider else "docker"


def get_node_provider(node: models.Node, session) -> str:
    """Get the provider for a specific node based on its image type.

    Args:
        node: The node to get provider for
        session: Database session for looking up image info

    Returns:
        Provider name: "libvirt" for qcow2/img images, "docker" otherwise
    """
    from app.image_store import get_image_provider
    from app.services.topology import resolve_node_image, resolve_device_kind

    # Resolve the node's image
    kind = resolve_device_kind(node.device)
    image = node.image or resolve_node_image(node.device, kind, node.image, node.version)

    return get_image_provider(image)


def update_lab_provider_from_nodes(session: Session, lab: models.Lab) -> str:
    """Update lab provider based on the nodes it contains.

    Examines all nodes in the lab and sets the provider to "libvirt" if any
    node requires a VM (qcow2/img image), otherwise "docker".

    This ensures labs automatically use the correct provider when VM-based
    devices like IOSv are added.

    Args:
        session: Database session
        lab: Lab model to update

    Returns:
        The determined provider ("docker" or "libvirt")
    """
    from app.image_store import get_image_provider
    from app.services.topology import resolve_node_image, resolve_device_kind

    # Get all nodes for this lab
    nodes = session.query(models.Node).filter(models.Node.lab_id == lab.id).all()

    # Check if any node requires libvirt
    for node in nodes:
        kind = resolve_device_kind(node.device)
        image = resolve_node_image(node.device, kind, node.image, node.version)
        if image and get_image_provider(image) == "libvirt":
            # Found a VM node - lab needs libvirt
            if lab.provider != "libvirt":
                lab.provider = "libvirt"
                session.commit()
            return "libvirt"

    # No VM nodes found - use docker
    if lab.provider != "docker":
        lab.provider = "docker"
        session.commit()
    return "docker"


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
