"""Link orchestration for lab deployments.

This module handles the creation and teardown of network links during
lab lifecycle operations. It coordinates with agents to establish
L2 connectivity between container interfaces.

Key operations:
1. After container deployment, create all links for the lab
2. Set up VXLAN tunnels for cross-host links
3. Update LinkState records with actual connectivity status
4. Teardown links during lab destruction
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app import agent_client, models
from app.services.link_manager import LinkManager
from app.services.link_validator import verify_link_connected, update_interface_mappings
from app.services.topology import TopologyService
from app.topology import _normalize_interface_name

logger = logging.getLogger(__name__)


async def create_deployment_links(
    session: Session,
    lab_id: str,
    host_to_agent: dict[str, models.Host],
    log_parts: list[str] | None = None,
) -> tuple[int, int]:
    """Create all links for a lab after deployment.

    This should be called after all containers are deployed and the
    OVS plugin has registered their endpoints.

    Args:
        session: Database session
        lab_id: Lab identifier
        host_to_agent: Map of host_id to Host objects for available agents
        log_parts: Optional list to append log messages to

    Returns:
        Tuple of (successful_links, failed_links) counts
    """
    if log_parts is None:
        log_parts = []

    # Get link definitions from database
    topo_service = TopologyService(session)
    db_links = topo_service.get_links(lab_id)

    if not db_links:
        logger.debug(f"No links defined for lab {lab_id}")
        return 0, 0

    logger.info(f"Creating {len(db_links)} links for lab {lab_id}")
    log_parts.append(f"\n=== Creating {len(db_links)} Links ===")

    # Ensure LinkState records exist for all links
    existing_states = {
        ls.link_name: ls
        for ls in session.query(models.LinkState)
        .filter(models.LinkState.lab_id == lab_id)
        .all()
    }

    link_manager = LinkManager(session)
    success_count = 0
    fail_count = 0

    for link in db_links:
        # Get source and target node info
        source_node = session.get(models.Node, link.source_node_id)
        target_node = session.get(models.Node, link.target_node_id)

        if not source_node or not target_node:
            logger.warning(f"Link {link.link_name} has missing node reference")
            fail_count += 1
            log_parts.append(f"  {link.link_name}: FAILED - missing node reference")
            continue

        # Get or create LinkState
        if link.link_name in existing_states:
            link_state = existing_states[link.link_name]
        else:
            link_state = models.LinkState(
                lab_id=lab_id,
                link_definition_id=link.id,
                link_name=link.link_name,
                source_node=source_node.container_name,
                source_interface=link.source_interface,
                target_node=target_node.container_name,
                target_interface=link.target_interface,
                desired_state="up",
                actual_state="pending",
            )
            session.add(link_state)
            session.flush()  # Get ID

        # Determine host placement for each endpoint
        source_host_id = source_node.host_id
        target_host_id = target_node.host_id

        # Fall back to NodePlacement if not set on Node
        if not source_host_id:
            placement = (
                session.query(models.NodePlacement)
                .filter(
                    models.NodePlacement.lab_id == lab_id,
                    models.NodePlacement.node_name == source_node.container_name,
                )
                .first()
            )
            if placement:
                source_host_id = placement.host_id

        if not target_host_id:
            placement = (
                session.query(models.NodePlacement)
                .filter(
                    models.NodePlacement.lab_id == lab_id,
                    models.NodePlacement.node_name == target_node.container_name,
                )
                .first()
            )
            if placement:
                target_host_id = placement.host_id

        if not source_host_id or not target_host_id:
            logger.warning(f"Link {link.link_name} has missing host placement")
            link_state.actual_state = "error"
            link_state.error_message = "Missing host placement for one or more endpoints"
            fail_count += 1
            log_parts.append(f"  {link.link_name}: FAILED - missing host placement")
            continue

        # Store host IDs in link_state
        link_state.source_host_id = source_host_id
        link_state.target_host_id = target_host_id

        # Check if this is a same-host or cross-host link
        is_cross_host = source_host_id != target_host_id
        link_state.is_cross_host = is_cross_host

        # Skip same-host links that are already up (idempotent)
        # Cross-host links are re-applied to ensure tunnels are recreated after restarts.
        if link_state.actual_state == "up" and not is_cross_host:
            logger.debug(f"Link {link.link_name} already up, skipping")
            success_count += 1
            continue

        if is_cross_host:
            # Create cross-host link via VXLAN
            success = await create_cross_host_link(
                session, lab_id, link_state, host_to_agent, log_parts
            )
        else:
            # Create same-host link via OVS hot_connect
            success = await create_same_host_link(
                session, lab_id, link_state, host_to_agent, log_parts
            )

        if success:
            success_count += 1
        else:
            fail_count += 1

    session.commit()

    logger.info(f"Link creation complete: {success_count} succeeded, {fail_count} failed")
    log_parts.append(f"\nLink creation: {success_count} OK, {fail_count} failed")

    return success_count, fail_count


async def create_same_host_link(
    session: Session,
    lab_id: str,
    link_state: models.LinkState,
    host_to_agent: dict[str, models.Host],
    log_parts: list[str],
    verify: bool = True,
) -> bool:
    """Create a link between interfaces on the same host with atomic semantics.

    Uses OVS hot_connect to put both interfaces in the same VLAN.
    Only marks link as "up" after verifying VLAN tags match.

    State machine: pending -> creating -> up
                                       `-> error

    Args:
        session: Database session
        lab_id: Lab identifier
        link_state: LinkState record to update
        host_to_agent: Map of host_id to Host objects
        log_parts: List to append log messages to
        verify: Whether to verify VLAN tags after creation (default: True)

    Returns:
        True if link was created successfully, False otherwise
    """
    agent = host_to_agent.get(link_state.source_host_id)
    if not agent:
        link_state.actual_state = "error"
        link_state.error_message = f"Agent not found for host {link_state.source_host_id}"
        log_parts.append(f"  {link_state.link_name}: FAILED - agent not found")
        return False

    # Set state to "creating" before making any changes
    link_state.actual_state = "creating"
    session.flush()

    try:
        source_iface = _normalize_interface_name(link_state.source_interface) if link_state.source_interface else ""
        target_iface = _normalize_interface_name(link_state.target_interface) if link_state.target_interface else ""

        result = await agent_client.create_link_on_agent(
            agent,
            lab_id=lab_id,
            source_node=link_state.source_node,
            source_interface=source_iface,
            target_node=link_state.target_node,
            target_interface=target_iface,
        )

        if not result.get("success"):
            link_state.actual_state = "error"
            link_state.error_message = result.get("error", "hot_connect failed")
            log_parts.append(f"  {link_state.link_name}: FAILED - {link_state.error_message}")
            return False

        # Store the reported VLAN tag
        reported_vlan = result.get("vlan_tag")
        link_state.vlan_tag = reported_vlan

        # Verify the link is actually connected (VLAN tags match)
        if verify:
            is_valid, error = await verify_link_connected(session, link_state, host_to_agent)
            if not is_valid:
                link_state.actual_state = "error"
                link_state.error_message = f"Verification failed: {error}"
                log_parts.append(f"  {link_state.link_name}: FAILED - {link_state.error_message}")
                return False

            # Update interface mappings after successful link creation
            await update_interface_mappings(session, link_state, host_to_agent)

        # Only now mark as "up"
        link_state.actual_state = "up"
        link_state.source_carrier_state = "on"
        link_state.target_carrier_state = "on"
        link_state.error_message = None
        log_parts.append(
            f"  {link_state.link_name}: OK (VLAN {link_state.vlan_tag})"
        )
        return True

    except Exception as e:
        logger.error(f"Failed to create same-host link {link_state.link_name}: {e}")
        link_state.actual_state = "error"
        link_state.error_message = str(e)
        log_parts.append(f"  {link_state.link_name}: FAILED - {e}")
        return False


async def create_cross_host_link(
    session: Session,
    lab_id: str,
    link_state: models.LinkState,
    host_to_agent: dict[str, models.Host],
    log_parts: list[str],
    verify: bool = True,
) -> bool:
    """Create a link between interfaces on different hosts with atomic semantics.

    Uses the new trunk VTEP model: one VTEP per host-pair (not per link),
    with VLAN tags providing link isolation.

    State machine: pending -> creating -> up
                                       `-> error

    Args:
        session: Database session
        lab_id: Lab identifier
        link_state: LinkState record to update
        host_to_agent: Map of host_id to Host objects
        log_parts: List to append log messages to
        verify: Whether to verify VLAN tags after creation (default: True)

    Returns:
        True if link was created successfully, False otherwise
    """
    agent_a = host_to_agent.get(link_state.source_host_id)
    agent_b = host_to_agent.get(link_state.target_host_id)

    if not agent_a or not agent_b:
        link_state.actual_state = "error"
        link_state.error_message = "One or more agents not available"
        log_parts.append(f"  {link_state.link_name}: FAILED - agents not available")
        return False

    # Set state to "creating" before making any changes
    link_state.actual_state = "creating"
    session.flush()

    try:
        interface_a = _normalize_interface_name(link_state.source_interface) if link_state.source_interface else ""
        interface_b = _normalize_interface_name(link_state.target_interface) if link_state.target_interface else ""

        # Use new trunk VTEP model (setup_cross_host_link_v2)
        result = await agent_client.setup_cross_host_link_v2(
            database=session,
            lab_id=lab_id,
            link_id=link_state.link_name,
            agent_a=agent_a,
            agent_b=agent_b,
            node_a=link_state.source_node,
            interface_a=interface_a,
            node_b=link_state.target_node,
            interface_b=interface_b,
        )

        if not result.get("success"):
            link_state.actual_state = "error"
            link_state.error_message = result.get("error", "VTEP setup failed")
            log_parts.append(f"  {link_state.link_name}: FAILED - {link_state.error_message}")
            return False

        # Store VLAN tag (no longer per-link VNI in new model)
        vlan_tag = result.get("vlan_tag", 0)
        link_state.vlan_tag = vlan_tag

        # Verify the link is actually connected (VLAN tags match on both agents)
        if verify:
            is_valid, error = await verify_link_connected(session, link_state, host_to_agent)
            if not is_valid:
                link_state.actual_state = "error"
                link_state.error_message = f"Verification failed: {error}"
                log_parts.append(f"  {link_state.link_name}: FAILED - {link_state.error_message}")
                return False

            # Update interface mappings after successful link creation
            await update_interface_mappings(session, link_state, host_to_agent)

        # Create VxlanTunnel record for tracking (VNI is now per-host-pair)
        agent_ip_a = _extract_agent_ip(agent_a)
        agent_ip_b = _extract_agent_ip(agent_b)

        # Check if tunnel record already exists for this link
        existing_tunnel = (
            session.query(models.VxlanTunnel)
            .filter(models.VxlanTunnel.link_state_id == link_state.id)
            .first()
        )
        if not existing_tunnel:
            tunnel = models.VxlanTunnel(
                lab_id=lab_id,
                link_state_id=link_state.id,
                vni=0,  # Not meaningful in new model (VTEP VNI is per-host-pair)
                vlan_tag=vlan_tag,
                agent_a_id=agent_a.id,
                agent_a_ip=agent_ip_a,
                agent_b_id=agent_b.id,
                agent_b_ip=agent_ip_b,
                status="active",
            )
            session.add(tunnel)
        else:
            existing_tunnel.vlan_tag = vlan_tag
            existing_tunnel.status = "active"

        # Only now mark as "up"
        link_state.actual_state = "up"
        link_state.source_carrier_state = "on"
        link_state.target_carrier_state = "on"
        link_state.error_message = None
        log_parts.append(
            f"  {link_state.link_name}: OK (VLAN {vlan_tag}, cross-host VTEP)"
        )
        return True

    except Exception as e:
        logger.error(f"Failed to create cross-host link {link_state.link_name}: {e}")
        link_state.actual_state = "error"
        link_state.error_message = str(e)
        log_parts.append(f"  {link_state.link_name}: FAILED - {e}")
        return False


async def teardown_deployment_links(
    session: Session,
    lab_id: str,
    host_to_agent: dict[str, models.Host],
    log_parts: list[str] | None = None,
) -> tuple[int, int]:
    """Tear down all links for a lab during destruction.

    This cleans up VXLAN tunnels and VxlanTunnel records.

    Args:
        session: Database session
        lab_id: Lab identifier
        host_to_agent: Map of host_id to Host objects
        log_parts: Optional list to append log messages to

    Returns:
        Tuple of (successful_teardowns, failed_teardowns) counts
    """
    if log_parts is None:
        log_parts = []

    # Get all VXLAN tunnels for this lab
    tunnels = (
        session.query(models.VxlanTunnel)
        .filter(models.VxlanTunnel.lab_id == lab_id)
        .all()
    )

    if not tunnels:
        logger.debug(f"No VXLAN tunnels to tear down for lab {lab_id}")
        return 0, 0

    logger.info(f"Tearing down {len(tunnels)} VXLAN tunnels for lab {lab_id}")
    log_parts.append(f"\n=== Tearing Down {len(tunnels)} VXLAN Tunnels ===")

    success_count = 0
    fail_count = 0

    # Track which agents need overlay cleanup (deduplicate)
    agents_to_cleanup: set[str] = set()
    for tunnel in tunnels:
        agents_to_cleanup.add(tunnel.agent_a_id)
        agents_to_cleanup.add(tunnel.agent_b_id)
        tunnel.status = "cleanup"

    # Clean up overlay on each agent
    for agent_id in agents_to_cleanup:
        agent = host_to_agent.get(agent_id)
        if not agent:
            logger.warning(f"Agent {agent_id} not available for cleanup")
            fail_count += 1
            log_parts.append(f"  Agent {agent_id}: FAILED - not available")
            continue

        try:
            result = await agent_client.cleanup_overlay_on_agent(agent, lab_id)
            if result.get("errors"):
                logger.warning(f"Cleanup errors on {agent.name}: {result['errors']}")
            log_parts.append(
                f"  Agent {agent.name}: OK "
                f"({result.get('tunnels_deleted', 0)} tunnels, "
                f"{result.get('bridges_deleted', 0)} bridges)"
            )
            success_count += 1
        except Exception as e:
            logger.error(f"Failed to cleanup overlay on agent {agent_id}: {e}")
            log_parts.append(f"  Agent {agent_id}: FAILED - {e}")
            fail_count += 1

    # Delete VxlanTunnel records
    for tunnel in tunnels:
        session.delete(tunnel)

    # Update LinkState records
    link_states = (
        session.query(models.LinkState)
        .filter(
            models.LinkState.lab_id == lab_id,
            models.LinkState.is_cross_host == True,
        )
        .all()
    )
    for ls in link_states:
        ls.vni = None
        ls.vlan_tag = None
        ls.actual_state = "down"

    session.commit()

    logger.info(f"VXLAN teardown complete: {success_count} agents OK, {fail_count} failed")
    return success_count, fail_count


def _extract_agent_ip(agent: models.Host) -> str:
    """Extract IP address from agent's address field."""
    addr = agent.address.replace("http://", "").replace("https://", "")
    return addr.split(":")[0]
