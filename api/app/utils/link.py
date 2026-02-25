"""Link-related utility functions."""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.services.interface_naming import normalize_interface

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
    - Links marked as "up" (for verification, or teardown if desired="down")
    - Links in "error" with desired_state "up" (for recovery)
    - Links "down"/"pending" with desired_state "up" (for creation)

    Returns:
        SQLAlchemy filter expression
    """
    from sqlalchemy import or_, and_
    from app import models

    return and_(
        models.LinkState.actual_state != "cleanup",
        or_(
            models.LinkState.actual_state == "up",
            # Error links that should be up need attention —
            # includes both same-host and cross-host recovery paths.
            (
                (models.LinkState.actual_state == "error") &
                (models.LinkState.desired_state == "up")
            ),
            # Links that are down/pending but should be up — needs creation
            (
                (models.LinkState.actual_state.in_(["down", "pending"])) &
                (models.LinkState.desired_state == "up")
            ),
            # Links that are up but should be down — needs teardown
            (
                (models.LinkState.actual_state == "up") &
                (models.LinkState.desired_state == "down")
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


def canonicalize_link_endpoints(
    source_node: str,
    source_interface: str,
    target_node: str,
    target_interface: str,
    source_device: str | None = None,
    target_device: str | None = None,
) -> tuple[str, str, str, str]:
    """Normalize and sort endpoints into canonical source/target order."""
    src_i = normalize_interface(source_interface, source_device) if source_interface else "eth0"
    tgt_i = normalize_interface(target_interface, target_device) if target_interface else "eth0"
    if f"{source_node}:{src_i}" <= f"{target_node}:{tgt_i}":
        return source_node, src_i, target_node, tgt_i
    return target_node, tgt_i, source_node, src_i


def link_state_endpoint_key(
    link_state: "models.LinkState",
    node_device_map: dict[str, str | None] | None = None,
) -> tuple[str, str, str, str]:
    """Return canonical endpoint tuple for an existing LinkState row."""
    src_dev = (node_device_map or {}).get(link_state.source_node)
    tgt_dev = (node_device_map or {}).get(link_state.target_node)
    return canonicalize_link_endpoints(
        link_state.source_node,
        link_state.source_interface,
        link_state.target_node,
        link_state.target_interface,
        source_device=src_dev,
        target_device=tgt_dev,
    )
