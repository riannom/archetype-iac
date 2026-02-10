"""Link-related utility functions."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from app import models


def lookup_endpoint_hosts(
    session: "Session",
    link_state: "models.LinkState",
) -> tuple[str | None, str | None]:
    """Look up which hosts have the source and target nodes for a link.

    First checks Node.host_id (explicit placement), then NodePlacement
    (runtime placement tracking).

    Args:
        session: Database session
        link_state: LinkState object with source/target node info

    Returns:
        Tuple of (source_host_id, target_host_id)
    """
    lab_id = link_state.lab_id

    from app.utils.nodes import resolve_node_host_id

    source_host_id = resolve_node_host_id(session, lab_id, link_state.source_node)
    target_host_id = resolve_node_host_id(session, lab_id, link_state.target_node)

    return source_host_id, target_host_id


def links_needing_reconciliation_filter():
    """Return SQLAlchemy filter for links that need reconciliation attention.

    This includes:
    - Links marked as "up" (for verification)
    - Cross-host links in "error" with desired_state "up" (for recovery,
      including both partial attachment and VLAN tag mismatch cases)

    Returns:
        SQLAlchemy filter expression
    """
    from sqlalchemy import or_, and_
    from app import models

    return and_(
        models.LinkState.actual_state != "cleanup",
        or_(
            models.LinkState.actual_state == "up",
            # All cross-host error links that should be up need attention —
            # includes both partial attachment AND VLAN tag mismatch cases
            (
                (models.LinkState.actual_state == "error") &
                (models.LinkState.is_cross_host == True) &
                (models.LinkState.desired_state == "up")
            ),
        ),
    )


def generate_link_name(
    source_node: str,
    source_interface: str,
    target_node: str,
    target_interface: str,
) -> str:
    """Generate a canonical link name from endpoints.

    Link names are sorted alphabetically to ensure the same link always gets
    the same name regardless of endpoint order.

    Args:
        source_node: Source node name
        source_interface: Source interface name
        target_node: Target node name
        target_interface: Target interface name

    Returns:
        Canonical link name in format "nodeA:ifaceA-nodeB:ifaceB"
    """
    ep_a = f"{source_node}:{source_interface}"
    ep_b = f"{target_node}:{target_interface}"
    if ep_a <= ep_b:
        return f"{ep_a}-{ep_b}"
    return f"{ep_b}-{ep_a}"
