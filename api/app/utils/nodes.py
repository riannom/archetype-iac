"""Node lookup utilities shared across API services."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from app import models


def get_node_by_any_id(
    session: "Session",
    lab_id: str,
    identifier: str,
) -> "models.Node | None":
    """Get a node by container_name or gui_id."""
    from app import models
    from sqlalchemy import or_

    return (
        session.query(models.Node)
        .filter(
            models.Node.lab_id == lab_id,
            or_(
                models.Node.container_name == identifier,
                models.Node.gui_id == identifier,
            ),
        )
        .first()
    )


def resolve_node_host_id(
    session: "Session",
    lab_id: str,
    node_identifier: str,
) -> str | None:
    """Resolve the host_id for a node, falling back to NodePlacement."""
    from app import models

    node = get_node_by_any_id(session, lab_id, node_identifier)
    if node and node.host_id:
        return node.host_id

    node_name = node.container_name if node else node_identifier
    placement = (
        session.query(models.NodePlacement)
        .filter(
            models.NodePlacement.lab_id == lab_id,
            models.NodePlacement.node_name == node_name,
        )
        .first()
    )
    if placement:
        return placement.host_id

    return None
