"""Node lookup utilities shared across API services."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from app import models


def get_node_placement_mapping(
    session: "Session",
    lab_id: str,
    lab_agent_id: str | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    """Build node->host_id mapping and host_id->name lookup from placements.

    Returns:
        (placement_by_node, host_names) where:
        - placement_by_node: {node_name: host_id}
        - host_names: {host_id: host_name}
    """
    from app import models as _m

    placements = (
        session.query(_m.NodePlacement)
        .filter(_m.NodePlacement.lab_id == lab_id)
        .all()
    )
    placement_by_node: dict[str, str] = {p.node_name: p.host_id for p in placements}

    host_ids = set(placement_by_node.values())
    if lab_agent_id:
        host_ids.add(lab_agent_id)

    host_names: dict[str, str] = {}
    if host_ids:
        host_records = (
            session.query(_m.Host)
            .filter(_m.Host.id.in_(host_ids))
            .all()
        )
        host_names = {h.id: h.name for h in host_records}

    return placement_by_node, host_names


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
