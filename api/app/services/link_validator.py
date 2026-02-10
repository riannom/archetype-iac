"""Link validation service for verifying OVS connectivity.

This service verifies that links are actually connected by checking
OVS state on the agents.

Key operations:
- verify_link_connected: Check if a link is actually working
- verify_same_host_link: Verify VLAN tags match for same-host link
- verify_cross_host_link: Verify per-link VXLAN tunnels exist on both agents
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app import agent_client, models
from app.services import interface_mapping as mapping_service
from app.services.interface_naming import normalize_interface

logger = logging.getLogger(__name__)


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
    agent = host_to_agent.get(link_state.source_host_id)
    if not agent:
        return False, f"Agent not found for host {link_state.source_host_id}"

    # Normalize interface names (Ethernet1 -> eth1) for agent queries
    source_iface = normalize_interface(link_state.source_interface) if link_state.source_interface else ""
    target_iface = normalize_interface(link_state.target_interface) if link_state.target_interface else ""

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
        return False, f"VLAN mismatch: {link_state.source_node}:{link_state.source_interface}={source_vlan}, {link_state.target_node}:{link_state.target_interface}={target_vlan}"

    # Update link_state with verified VLAN tag
    if link_state.vlan_tag != source_vlan:
        logger.debug(f"Updating link VLAN tag from {link_state.vlan_tag} to {source_vlan}")
        link_state.vlan_tag = source_vlan

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
    source_agent = host_to_agent.get(link_state.source_host_id)
    target_agent = host_to_agent.get(link_state.target_host_id)

    if not source_agent:
        return False, f"Source agent not found for host {link_state.source_host_id}"

    if not target_agent:
        return False, f"Target agent not found for host {link_state.target_host_id}"

    # Normalize interface names (Ethernet1 -> eth1) for agent queries
    source_iface = normalize_interface(link_state.source_interface) if link_state.source_interface else ""
    target_iface = normalize_interface(link_state.target_interface) if link_state.target_interface else ""

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
    for side, agent in [("source", source_agent), ("target", target_agent)]:
        try:
            status = await agent_client.get_overlay_status_from_agent(agent)
            link_tunnels = status.get("link_tunnels", [])
            if not link_tunnels:
                return False, f"Per-link VXLAN tunnel not found on {agent.name} for link {link_name}"
            # Accept recovered tunnels that still have placeholder link_id but correct port name
            expected_port = agent_client.compute_vxlan_port_name(link_state.lab_id, link_name)
            found = any(
                t.get("link_id") == link_name or t.get("interface_name") == expected_port
                for t in link_tunnels
            )
            if not found:
                return False, f"Per-link VXLAN tunnel not found on {agent.name} for link {link_name}"
        except Exception as e:
            return False, f"Could not check overlay status on {agent.name}: {e}"

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
