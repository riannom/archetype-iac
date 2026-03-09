"""Link validation service for verifying OVS connectivity.

This service verifies that links are actually connected by checking
OVS state on the agents.

Key operations:
- verify_link_connected: Check if a link is actually working
- verify_same_host_link: Verify VLAN tags match for same-host link
- verify_cross_host_link: Verify per-link VXLAN tunnels exist on both agents

Error messages use prefixes to indicate the type of failure:
- VLAN_MISMATCH: Tags drifted but ports exist (lightweight repair possible)
- TUNNEL_MISSING: VXLAN tunnel port doesn't exist (full recreation needed)
- PORT_UNREACHABLE: Agent or port not accessible
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app import agent_client, models
from app.services import interface_mapping as mapping_service
from app.services.interface_naming import normalize_for_node
from app.utils.link import lookup_endpoint_hosts

logger = logging.getLogger(__name__)

# Error type prefixes for structured error identification
VLAN_MISMATCH = "VLAN_MISMATCH"
TUNNEL_MISSING = "TUNNEL_MISSING"
PORT_UNREACHABLE = "PORT_UNREACHABLE"


def _resolve_endpoint_node(
    session: Session,
    lab_id: str,
    node_name: str,
) -> models.Node | None:
    node = (
        session.query(models.Node)
        .filter(
            models.Node.lab_id == lab_id,
            models.Node.container_name == node_name,
        )
        .first()
    )
    if node:
        return node
    return (
        session.query(models.Node)
        .filter(
            models.Node.lab_id == lab_id,
            models.Node.display_name == node_name,
        )
        .first()
    )


def _upsert_interface_mapping(
    session: Session,
    *,
    lab_id: str,
    node_name: str,
    interface_name: str | None,
    ovs_port_name: str | None,
    vlan_tag: int | None,
) -> int:
    if not ovs_port_name:
        return 0

    normalized_iface = normalize_for_node(
        session,
        lab_id,
        node_name,
        interface_name or "",
    )
    if not normalized_iface:
        return 0

    node = _resolve_endpoint_node(session, lab_id, node_name)
    if not node:
        return 0

    existing = (
        session.query(models.InterfaceMapping)
        .filter(
            models.InterfaceMapping.lab_id == lab_id,
            models.InterfaceMapping.node_id == node.id,
            models.InterfaceMapping.linux_interface == normalized_iface,
        )
        .first()
    )
    vendor_interface = mapping_service.linux_to_vendor_interface(
        normalized_iface,
        node.device,
    )

    if existing:
        changed = False
        if existing.ovs_port != ovs_port_name:
            existing.ovs_port = ovs_port_name
            changed = True
        if existing.ovs_bridge != "arch-ovs":
            existing.ovs_bridge = "arch-ovs"
            changed = True
        if existing.vlan_tag != vlan_tag:
            existing.vlan_tag = vlan_tag
            changed = True
        if existing.vendor_interface != vendor_interface:
            existing.vendor_interface = vendor_interface
            changed = True
        if existing.device_type != node.device:
            existing.device_type = node.device
            changed = True
        return 1 if changed else 0

    from uuid import uuid4

    session.add(
        models.InterfaceMapping(
            id=str(uuid4()),
            lab_id=lab_id,
            node_id=node.id,
            ovs_port=ovs_port_name,
            ovs_bridge="arch-ovs",
            vlan_tag=vlan_tag,
            linux_interface=normalized_iface,
            vendor_interface=vendor_interface,
            device_type=node.device,
        )
    )
    return 1


def persist_link_interface_mappings(
    session: Session,
    link_state: models.LinkState,
    *,
    source_ovs_port: str | None = None,
    target_ovs_port: str | None = None,
    source_vlan_tag: int | None = None,
    target_vlan_tag: int | None = None,
) -> int:
    """Persist interface mappings from already-known endpoint OVS ports."""
    changed = 0
    changed += _upsert_interface_mapping(
        session,
        lab_id=link_state.lab_id,
        node_name=link_state.source_node,
        interface_name=link_state.source_interface,
        ovs_port_name=source_ovs_port,
        vlan_tag=source_vlan_tag,
    )
    changed += _upsert_interface_mapping(
        session,
        lab_id=link_state.lab_id,
        node_name=link_state.target_node,
        interface_name=link_state.target_interface,
        ovs_port_name=target_ovs_port,
        vlan_tag=target_vlan_tag,
    )
    if changed:
        session.flush()
    return changed


def is_vlan_mismatch(error: str | None) -> bool:
    """Check if an error message indicates a VLAN mismatch (repairable)."""
    return error is not None and error.startswith(VLAN_MISMATCH)


def _ensure_link_host_placement(
    session: Session,
    link_state: models.LinkState,
) -> tuple[str | None, str | None]:
    """Resolve and backfill endpoint host placement for existing LinkState rows."""
    source_host_id = link_state.source_host_id
    target_host_id = link_state.target_host_id
    if source_host_id and target_host_id:
        return source_host_id, target_host_id

    resolved_source, resolved_target = lookup_endpoint_hosts(session, link_state)
    if not source_host_id and resolved_source:
        link_state.source_host_id = resolved_source
        source_host_id = resolved_source
    if not target_host_id and resolved_target:
        link_state.target_host_id = resolved_target
        target_host_id = resolved_target

    return source_host_id, target_host_id


async def verify_link_connected(
    session: Session,
    link_state: models.LinkState,
    host_to_agent: dict[str, models.Host],
) -> tuple[bool, str | None]:
    """Verify a link is actually connected by checking OVS VLAN tags.

    For same-host links: both interfaces should have the same VLAN tag.
    For cross-host links: both interfaces should have matching VLAN tags,
    and the VXLAN tunnel should be active.

    Args:
        session: Database session
        link_state: The link to verify
        host_to_agent: Map of host_id to Host objects

    Returns:
        (is_valid, error_message) tuple
    """
    if link_state.is_cross_host:
        return await verify_cross_host_link(session, link_state, host_to_agent)
    else:
        return await verify_same_host_link(session, link_state, host_to_agent)


async def verify_same_host_link(
    session: Session,
    link_state: models.LinkState,
    host_to_agent: dict[str, models.Host],
) -> tuple[bool, str | None]:
    """Verify a same-host link by checking VLAN tags match.

    Args:
        session: Database session
        link_state: The link to verify
        host_to_agent: Map of host_id to Host objects

    Returns:
        (is_valid, error_message) tuple
    """
    source_host_id, target_host_id = _ensure_link_host_placement(session, link_state)
    agent = host_to_agent.get(source_host_id or target_host_id)
    if not agent:
        return False, f"Agent not found for host {source_host_id or target_host_id}"

    # Normalize interface names (Ethernet1 -> eth1) for agent queries
    source_iface = normalize_for_node(session, link_state.lab_id, link_state.source_node, link_state.source_interface or "")
    target_iface = normalize_for_node(session, link_state.lab_id, link_state.target_node, link_state.target_interface or "")

    # Get VLAN tags from agent (read directly from OVS for ground truth)
    source_vlan = await agent_client.get_interface_vlan_from_agent(
        agent,
        link_state.lab_id,
        link_state.source_node,
        source_iface,
        read_from_ovs=True,
    )

    target_vlan = await agent_client.get_interface_vlan_from_agent(
        agent,
        link_state.lab_id,
        link_state.target_node,
        target_iface,
        read_from_ovs=True,
    )

    if source_vlan is None:
        return False, f"Could not read VLAN tag for {link_state.source_node}:{link_state.source_interface}"

    if target_vlan is None:
        return False, f"Could not read VLAN tag for {link_state.target_node}:{link_state.target_interface}"

    if source_vlan != target_vlan:
        return False, (
            f"{VLAN_MISMATCH}: {link_state.source_node}:{link_state.source_interface}={source_vlan}, "
            f"{link_state.target_node}:{link_state.target_interface}={target_vlan}"
        )

    # Update link_state with verified VLAN tag
    if link_state.vlan_tag != source_vlan:
        logger.debug(f"Updating link VLAN tag from {link_state.vlan_tag} to {source_vlan}")
        link_state.vlan_tag = source_vlan

    # Backfill per-side tags if not yet stored
    if link_state.source_vlan_tag is None or link_state.source_vlan_tag != source_vlan:
        link_state.source_vlan_tag = source_vlan
    if link_state.target_vlan_tag is None or link_state.target_vlan_tag != target_vlan:
        link_state.target_vlan_tag = target_vlan

    return True, None


async def verify_cross_host_link(
    session: Session,
    link_state: models.LinkState,
    host_to_agent: dict[str, models.Host],
) -> tuple[bool, str | None]:
    """Verify a cross-host link using the per-link VNI model.

    Each side has its own local VLAN (different values are expected).
    We verify that:
    1. Each endpoint has a VLAN tag assigned on its OVS port
    2. The per-link VXLAN tunnel port exists on both agents

    Args:
        session: Database session
        link_state: The link to verify
        host_to_agent: Map of host_id to Host objects

    Returns:
        (is_valid, error_message) tuple
    """
    source_host_id, target_host_id = _ensure_link_host_placement(session, link_state)
    source_agent = host_to_agent.get(source_host_id)
    target_agent = host_to_agent.get(target_host_id)

    if not source_agent:
        return False, f"Source agent not found for host {source_host_id}"

    if not target_agent:
        return False, f"Target agent not found for host {target_host_id}"

    # Normalize interface names (Ethernet1 -> eth1) for agent queries
    source_iface = normalize_for_node(session, link_state.lab_id, link_state.source_node, link_state.source_interface or "")
    target_iface = normalize_for_node(session, link_state.lab_id, link_state.target_node, link_state.target_interface or "")

    # Verify each endpoint has a VLAN tag on its OVS port
    source_vlan = await agent_client.get_interface_vlan_from_agent(
        source_agent,
        link_state.lab_id,
        link_state.source_node,
        source_iface,
        read_from_ovs=True,
    )

    target_vlan = await agent_client.get_interface_vlan_from_agent(
        target_agent,
        link_state.lab_id,
        link_state.target_node,
        target_iface,
        read_from_ovs=True,
    )

    if source_vlan is None:
        return False, f"Could not read VLAN tag from {source_agent.name} for {link_state.source_node}:{link_state.source_interface}"

    if target_vlan is None:
        return False, f"Could not read VLAN tag from {target_agent.name} for {link_state.target_node}:{link_state.target_interface}"

    # Per-link VNI model: VLANs are local to each agent and need NOT match.
    # Verify the per-link VXLAN tunnel exists on both agents via overlay status.
    link_name = link_state.link_name
    for side, agent, expected_vlan in [
        ("source", source_agent, source_vlan),
        ("target", target_agent, target_vlan),
    ]:
        try:
            status = await agent_client.get_overlay_status_from_agent(agent)
            if status.get("error"):
                return False, f"Overlay status unavailable on {agent.name}: {status['error']}"
            link_tunnels = status.get("link_tunnels", [])
            if not link_tunnels:
                return False, f"{TUNNEL_MISSING}: on {agent.name} for link {link_name}"
            # Deterministic identity only: match tunnel by link_id.
            matching_tunnel = next(
                (
                    t for t in link_tunnels
                    if t.get("link_id") == link_name
                ),
                None,
            )
            if not matching_tunnel:
                return False, f"{TUNNEL_MISSING}: on {agent.name} for link {link_name}"

            # Ensure each side's local VLAN on the tunnel matches the endpoint's
            # current access VLAN. Recovered tunnels can exist with stale local_vlan
            # values and still pass existence checks, causing silent blackholes.
            tunnel_vlan = matching_tunnel.get("local_vlan")
            try:
                tunnel_vlan_int = int(tunnel_vlan) if tunnel_vlan is not None else None
            except (TypeError, ValueError):
                tunnel_vlan_int = None
            if tunnel_vlan_int is not None and tunnel_vlan_int != expected_vlan:
                return (
                    False,
                    f"{VLAN_MISMATCH}: VXLAN on {agent.name}: "
                    f"tunnel={tunnel_vlan_int}, endpoint={expected_vlan}",
                )
        except Exception as e:
            return False, f"Could not check overlay status on {agent.name}: {e}"

    # Backfill per-side tags if not yet stored
    if link_state.source_vlan_tag is None or link_state.source_vlan_tag != source_vlan:
        link_state.source_vlan_tag = source_vlan
    if link_state.target_vlan_tag is None or link_state.target_vlan_tag != target_vlan:
        link_state.target_vlan_tag = target_vlan

    return True, None


async def update_interface_mappings(
    session: Session,
    link_state: models.LinkState,
    host_to_agent: dict[str, models.Host],
) -> None:
    """Update interface mappings for both endpoints of a link.

    This syncs the interface_mappings table with the current OVS state
    for the link endpoints.

    Args:
        session: Database session
        link_state: The link to update mappings for
        host_to_agent: Map of host_id to Host objects
    """
    # Get agents for both endpoints
    agents_to_sync = set()
    if link_state.source_host_id:
        agents_to_sync.add(link_state.source_host_id)
    if link_state.target_host_id:
        agents_to_sync.add(link_state.target_host_id)

    for host_id in agents_to_sync:
        agent = host_to_agent.get(host_id)
        if agent:
            try:
                await mapping_service.populate_from_agent(
                    session, link_state.lab_id, agent
                )
            except Exception as e:
                logger.warning(f"Failed to update interface mappings from {agent.name}: {e}")

    await ensure_link_interface_mappings(session, link_state, host_to_agent)


async def ensure_link_interface_mappings(
    session: Session,
    link_state: models.LinkState,
    host_to_agent: dict[str, models.Host],
) -> int:
    """Best-effort endpoint mapping backfill via provider-agnostic agent lookup."""

    async def _ensure_endpoint(
        node_name: str,
        interface_name: str | None,
        host_id: str | None,
    ) -> int:
        if not host_id:
            return 0
        agent = host_to_agent.get(host_id)
        if not agent:
            return 0

        normalized_iface = normalize_for_node(
            session,
            link_state.lab_id,
            node_name,
            interface_name or "",
        )
        if not normalized_iface:
            return 0

        node = _resolve_endpoint_node(session, link_state.lab_id, node_name)
        if not node:
            return 0

        existing = (
            session.query(models.InterfaceMapping)
            .filter(
                models.InterfaceMapping.lab_id == link_state.lab_id,
                models.InterfaceMapping.node_id == node.id,
                models.InterfaceMapping.linux_interface == normalized_iface,
            )
            .first()
        )
        if existing and existing.ovs_port:
            return 0

        details = await agent_client.get_interface_port_details_from_agent(
            agent,
            link_state.lab_id,
            node_name,
            normalized_iface,
            read_from_ovs=True,
        )
        ovs_port_name = details.get("ovs_port_name")
        if not ovs_port_name:
            return 0

        return _upsert_interface_mapping(
            session,
            lab_id=link_state.lab_id,
            node_name=node_name,
            interface_name=normalized_iface,
            ovs_port_name=ovs_port_name,
            vlan_tag=details.get("vlan_tag"),
        )

    created_or_updated = 0
    created_or_updated += await _ensure_endpoint(
        link_state.source_node,
        link_state.source_interface,
        link_state.source_host_id,
    )
    target_host_id = link_state.target_host_id or link_state.source_host_id
    created_or_updated += await _ensure_endpoint(
        link_state.target_node,
        link_state.target_interface,
        target_host_id,
    )
    return created_or_updated
