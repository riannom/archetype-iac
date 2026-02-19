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

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app import agent_client, models
from app.agent_client import compute_vxlan_port_name
from app.services.link_operational_state import recompute_link_oper_state
from app.services.link_manager import LinkManager
from app.services.link_validator import verify_link_connected, update_interface_mappings
from app.services.topology import TopologyService
from app.services.interface_naming import normalize_interface

logger = logging.getLogger(__name__)


def _sync_oper_state(session: Session, link_state: models.LinkState) -> None:
    recompute_link_oper_state(session, link_state)


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

    # Normalize link interface names (Ethernet1 -> eth1) before creating
    # LinkState records. This prevents naming mismatches between old Link
    # definitions and new LinkState records.
    try:
        normalized = topo_service.normalize_links_for_lab(lab_id)
        if normalized > 0:
            logger.info(f"Normalized {normalized} link record(s) before deployment")
            session.flush()
    except Exception as e:
        logger.warning(f"Failed to normalize link interfaces: {e}")

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

    # Clean up orphaned LinkState records from previous deploys
    # These may have different interface naming (Ethernet vs eth)
    current_link_names = {link.link_name for link in db_links}
    orphaned_states = [
        ls for ls in existing_states.values()
        if ls.link_name not in current_link_names
    ]
    for ls in orphaned_states:
        # Tear down VXLAN ports on agents before deleting the DB record
        tunnel = (
            session.query(models.VxlanTunnel)
            .filter(models.VxlanTunnel.link_state_id == ls.id)
            .first()
        )
        if tunnel:
            for agent_id, node, iface in [
                (tunnel.agent_a_id, ls.source_node, ls.source_interface),
                (tunnel.agent_b_id, ls.target_node, ls.target_interface),
            ]:
                agent = host_to_agent.get(agent_id)
                if agent:
                    try:
                        await agent_client.detach_overlay_interface_on_agent(
                            agent,
                            lab_id=lab_id,
                            container_name=node,
                            interface_name=normalize_interface(iface) if iface else "",
                            link_id=ls.link_name,
                        )
                        logger.info(
                            f"Torn down VXLAN port for orphaned link {ls.link_name} "
                            f"on agent {agent.name}"
                        )
                    except Exception as e:
                        logger.warning(
                            f"Failed to tear down VXLAN port for orphaned link "
                            f"{ls.link_name} on agent {agent_id}: {e}"
                        )

        logger.info(f"Cleaning up orphaned LinkState: {ls.link_name}")
        session.delete(ls)
    if orphaned_states:
        session.flush()
        # Refresh existing_states after cleanup
        existing_states = {
            name: ls for name, ls in existing_states.items()
            if name in current_link_names
        }

    LinkManager(session)
    success_count = 0
    fail_count = 0

    # Collect external network links for batch processing (shared VLAN per external network)
    external_link_groups: dict[str, list[tuple]] = {}  # ext_node_id -> [(link, link_state, source, target)]

    for link in db_links:
        # Get source and target node info
        source_node = session.get(models.Node, link.source_node_id)
        target_node = session.get(models.Node, link.target_node_id)

        if not source_node or not target_node:
            logger.warning(f"Link {link.link_name} has missing node reference")
            fail_count += 1
            log_parts.append(f"  {link.link_name}: FAILED - missing node reference")
            continue

        # Detect external network nodes
        source_is_external = source_node.node_type == "external"
        target_is_external = target_node.node_type == "external"

        if source_is_external and target_is_external:
            logger.warning(f"Link {link.link_name} has both endpoints as external - skipping")
            fail_count += 1
            log_parts.append(f"  {link.link_name}: FAILED - both endpoints external")
            continue

        if source_is_external or target_is_external:
            # External network link - collect for batch processing
            ext_node = source_node if source_is_external else target_node
            device_node = target_node if source_is_external else source_node
            device_interface = link.target_interface if source_is_external else link.source_interface

            # Get or create LinkState (use _ext: prefix for external endpoint name)
            ext_name = f"_ext:{ext_node.managed_interface_id or ext_node.container_name}"
            if link.link_name in existing_states:
                link_state = existing_states[link.link_name]
            else:
                link_state = models.LinkState(
                    lab_id=lab_id,
                    link_definition_id=link.id,
                    link_name=link.link_name,
                    source_node=device_node.container_name,
                    source_interface=device_interface,
                    target_node=ext_name,
                    target_interface="_external",
                    desired_state="up",
                    actual_state="pending",
                )
                session.add(link_state)
                session.flush()

            external_link_groups.setdefault(ext_node.id, []).append(
                (link, link_state, device_node, ext_node, device_interface)
            )
            continue

        # Get or create LinkState for regular links
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
            _sync_oper_state(session, link_state)
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

    # Process external network link groups
    for ext_node_id, group in external_link_groups.items():
        ok, fail = await create_external_network_links(
            session, lab_id, ext_node_id, group, host_to_agent, log_parts
        )
        success_count += ok
        fail_count += fail

    session.commit()

    logger.info(f"Link creation complete: {success_count} succeeded, {fail_count} failed")
    log_parts.append(f"\nLink creation: {success_count} OK, {fail_count} failed")

    return success_count, fail_count


async def create_external_network_links(
    session: Session,
    lab_id: str,
    ext_node_id: str,
    links: list[tuple],
    host_to_agent: dict[str, models.Host],
    log_parts: list[str],
) -> tuple[int, int]:
    """Process all links to a single external network node as a batch.

    All devices connected to the same external network share one OVS VLAN tag
    on the external interface's host, creating a true L2 broadcast domain.
    Cross-host devices get VXLAN tunnels back to the hub host.

    Args:
        session: Database session
        lab_id: Lab identifier
        ext_node_id: External network node ID
        links: List of (link, link_state, device_node, ext_node, device_interface) tuples
        host_to_agent: Map of host_id to Host objects
        log_parts: Log messages list

    Returns:
        Tuple of (success_count, fail_count)
    """
    success_count = 0
    fail_count = 0

    if not links:
        return 0, 0

    # All tuples share the same ext_node
    ext_node = links[0][3]

    # Validate managed interface
    if not ext_node.managed_interface_id:
        for _, link_state, _, _, _ in links:
            link_state.actual_state = "error"
            link_state.error_message = "External network has no managed interface configured"
            _sync_oper_state(session, link_state)
            fail_count += 1
        log_parts.append(f"  External {ext_node.display_name}: FAILED - no managed interface")
        return success_count, fail_count

    mi = session.get(models.AgentManagedInterface, ext_node.managed_interface_id)
    if not mi:
        for _, link_state, _, _, _ in links:
            link_state.actual_state = "error"
            link_state.error_message = "Managed interface not found (may have been deleted)"
            _sync_oper_state(session, link_state)
            fail_count += 1
        log_parts.append(f"  External {ext_node.display_name}: FAILED - managed interface not found")
        return success_count, fail_count

    ext_host_id = mi.host_id
    ext_agent = host_to_agent.get(ext_host_id)
    if not ext_agent:
        for _, link_state, _, _, _ in links:
            link_state.actual_state = "error"
            link_state.error_message = "Agent for external interface not available"
            _sync_oper_state(session, link_state)
            fail_count += 1
        log_parts.append(f"  External {ext_node.display_name}: FAILED - agent offline")
        return success_count, fail_count

    logger.info(
        f"Processing external network '{ext_node.display_name}' "
        f"(interface {mi.name} on {ext_agent.name}) with {len(links)} connected devices"
    )
    log_parts.append(
        f"\n  External '{ext_node.display_name}' ({mi.name} on {ext_agent.name}): "
        f"{len(links)} device(s)"
    )

    # Group devices by host (same-host vs cross-host relative to external interface)
    same_host_links = []
    cross_host_by_host: dict[str, list[tuple]] = {}  # remote_host_id -> links

    for link_tuple in links:
        _, link_state, device_node, _, device_interface = link_tuple

        # Resolve device host
        device_host_id = device_node.host_id
        if not device_host_id:
            placement = (
                session.query(models.NodePlacement)
                .filter(
                    models.NodePlacement.lab_id == lab_id,
                    models.NodePlacement.node_name == device_node.container_name,
                )
                .first()
            )
            if placement:
                device_host_id = placement.host_id

        if not device_host_id:
            link_state.actual_state = "error"
            link_state.error_message = "Device has no host placement"
            _sync_oper_state(session, link_state)
            fail_count += 1
            log_parts.append(f"    {device_node.container_name}: FAILED - no host placement")
            continue

        link_state.source_host_id = device_host_id
        link_state.target_host_id = ext_host_id

        if device_host_id == ext_host_id:
            same_host_links.append(link_tuple)
        else:
            link_state.is_cross_host = True
            cross_host_by_host.setdefault(device_host_id, []).append(link_tuple)

    # Process same-host devices: connect to external interface, share VLAN
    allocated_vlan: int | None = None  # VLAN tag shared by all same-host devices

    for link_tuple in same_host_links:
        _, link_state, device_node, _, device_interface = link_tuple

        try:
            link_state.actual_state = "creating"
            result = await agent_client.connect_external_on_agent(
                agent=ext_agent,
                lab_id=lab_id,
                node_name=device_node.container_name,
                interface_name=device_interface,
                external_interface=mi.name,
                vlan_tag=allocated_vlan,  # None for first device (agent allocates)
            )

            if result.get("success"):
                returned_vlan = result.get("vlan_tag")
                if allocated_vlan is None and returned_vlan:
                    allocated_vlan = returned_vlan
                link_state.actual_state = "up"
                link_state.vlan_tag = returned_vlan
                link_state.source_carrier_state = "on"
                link_state.target_carrier_state = "on"
                _sync_oper_state(session, link_state)
                success_count += 1
                log_parts.append(
                    f"    {device_node.container_name}:{device_interface} -> "
                    f"{mi.name} VLAN {returned_vlan}: OK"
                )
            else:
                link_state.actual_state = "error"
                link_state.error_message = result.get("error", "Unknown error")
                _sync_oper_state(session, link_state)
                fail_count += 1
                log_parts.append(
                    f"    {device_node.container_name}:{device_interface}: "
                    f"FAILED - {result.get('error')}"
                )
        except Exception as e:
            link_state.actual_state = "error"
            link_state.error_message = str(e)
            _sync_oper_state(session, link_state)
            fail_count += 1
            logger.error(f"External connect failed: {e}")

    # Process cross-host devices: VXLAN tunnel from remote host to external host
    for remote_host_id, remote_links in cross_host_by_host.items():
        remote_agent = host_to_agent.get(remote_host_id)
        if not remote_agent:
            for _, link_state, device_node, _, _ in remote_links:
                link_state.actual_state = "error"
                link_state.error_message = "Remote agent not available"
                _sync_oper_state(session, link_state)
                fail_count += 1
            log_parts.append(f"    Remote host {remote_host_id}: FAILED - agent offline")
            continue

        for link_tuple in remote_links:
            link, link_state, device_node, _, device_interface = link_tuple

            try:
                link_state.actual_state = "creating"

                # Use setup_cross_host_link_v2 which handles both sides
                # External side: ext_agent, Device side: remote_agent
                result = await agent_client.setup_cross_host_link_v2(
                    database=session,
                    lab_id=lab_id,
                    link_id=link_state.id,
                    agent_a=ext_agent,
                    agent_b=remote_agent,
                    node_a=f"_ext:{mi.name}",
                    interface_a="_external",
                    node_b=device_node.container_name,
                    interface_b=device_interface,
                )

                if result.get("success"):
                    vni = result.get("vni", 0)
                    link_state.actual_state = "up"
                    link_state.vni = vni
                    link_state.source_carrier_state = "on"
                    link_state.target_carrier_state = "on"
                    link_state.source_vxlan_attached = True
                    link_state.target_vxlan_attached = True
                    _sync_oper_state(session, link_state)

                    # Create VxlanTunnel record
                    ext_ip = await agent_client.resolve_agent_ip(ext_agent.address)
                    remote_ip = await agent_client.resolve_agent_ip(remote_agent.address)

                    existing_tunnel = (
                        session.query(models.VxlanTunnel)
                        .filter(models.VxlanTunnel.link_state_id == link_state.id)
                        .first()
                    )
                    if not existing_tunnel:
                        tunnel = models.VxlanTunnel(
                            lab_id=lab_id,
                            link_state_id=link_state.id,
                            vni=vni,
                            vlan_tag=0,
                            agent_a_id=ext_agent.id,
                            agent_a_ip=ext_ip,
                            agent_b_id=remote_agent.id,
                            agent_b_ip=remote_ip,
                            status="active",
                            port_name=compute_vxlan_port_name(lab_id, link_state.link_name),
                        )
                        session.add(tunnel)
                    else:
                        existing_tunnel.vni = vni
                        existing_tunnel.status = "active"
                        if not existing_tunnel.port_name:
                            existing_tunnel.port_name = compute_vxlan_port_name(lab_id, link_state.link_name)

                    success_count += 1
                    log_parts.append(
                        f"    {device_node.container_name}:{device_interface} -> "
                        f"{mi.name} (VXLAN VNI {vni}): OK"
                    )
                else:
                    link_state.actual_state = "error"
                    link_state.error_message = result.get("error", "Unknown error")
                    _sync_oper_state(session, link_state)
                    fail_count += 1
                    log_parts.append(
                        f"    {device_node.container_name}:{device_interface}: "
                        f"FAILED - {result.get('error')}"
                    )
            except Exception as e:
                link_state.actual_state = "error"
                link_state.error_message = str(e)
                _sync_oper_state(session, link_state)
                fail_count += 1
                logger.error(f"Cross-host external link failed: {e}")

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
        _sync_oper_state(session, link_state)
        log_parts.append(f"  {link_state.link_name}: FAILED - agent not found")
        return False

    # Set state to "creating" before making any changes
    link_state.actual_state = "creating"
    _sync_oper_state(session, link_state)
    session.flush()

    try:
        # Look up device types for accurate interface normalization
        _src_node = (
            session.query(models.Node)
            .filter(models.Node.lab_id == lab_id, models.Node.container_name == link_state.source_node)
            .first()
        )
        _tgt_node = (
            session.query(models.Node)
            .filter(models.Node.lab_id == lab_id, models.Node.container_name == link_state.target_node)
            .first()
        )
        source_iface = normalize_interface(link_state.source_interface, _src_node.device if _src_node else None) if link_state.source_interface else ""
        target_iface = normalize_interface(link_state.target_interface, _tgt_node.device if _tgt_node else None) if link_state.target_interface else ""

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
            _sync_oper_state(session, link_state)
            log_parts.append(f"  {link_state.link_name}: FAILED - {link_state.error_message}")
            return False

        # Store the reported VLAN tag (same for both sides on same-host links).
        # New agent responses nest it under result["link"]["vlan_tag"], while
        # older mocks/callers still return top-level result["vlan_tag"].
        link_data = result.get("link", {})
        reported_vlan = link_data.get("vlan_tag") if isinstance(link_data, dict) else None
        if reported_vlan is None:
            reported_vlan = result.get("vlan_tag")
        link_state.vlan_tag = reported_vlan
        link_state.source_vlan_tag = reported_vlan
        link_state.target_vlan_tag = reported_vlan

        # Verify the link is actually connected (VLAN tags match)
        if verify:
            is_valid, error = await verify_link_connected(session, link_state, host_to_agent)
            if not is_valid:
                link_state.actual_state = "error"
                link_state.error_message = f"Verification failed: {error}"
                _sync_oper_state(session, link_state)
                log_parts.append(f"  {link_state.link_name}: FAILED - {link_state.error_message}")
                return False

            # Update interface mappings after successful link creation
            await update_interface_mappings(session, link_state, host_to_agent)

        # Only now mark as "up"
        link_state.actual_state = "up"
        link_state.source_carrier_state = "on"
        link_state.target_carrier_state = "on"
        link_state.error_message = None
        _sync_oper_state(session, link_state)
        log_parts.append(
            f"  {link_state.link_name}: OK (VLAN {link_state.vlan_tag})"
        )
        return True

    except Exception as e:
        logger.error(f"Failed to create same-host link {link_state.link_name}: {e}")
        link_state.actual_state = "error"
        link_state.error_message = str(e)
        _sync_oper_state(session, link_state)
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
        _sync_oper_state(session, link_state)
        log_parts.append(f"  {link_state.link_name}: FAILED - agents not available")
        return False

    # Set state to "creating" before making any changes
    link_state.actual_state = "creating"
    _sync_oper_state(session, link_state)
    session.flush()

    try:
        # Look up device types for accurate interface normalization
        _src_node = (
            session.query(models.Node)
            .filter(models.Node.lab_id == lab_id, models.Node.container_name == link_state.source_node)
            .first()
        )
        _tgt_node = (
            session.query(models.Node)
            .filter(models.Node.lab_id == lab_id, models.Node.container_name == link_state.target_node)
            .first()
        )
        interface_a = normalize_interface(link_state.source_interface, _src_node.device if _src_node else None) if link_state.source_interface else ""
        interface_b = normalize_interface(link_state.target_interface, _tgt_node.device if _tgt_node else None) if link_state.target_interface else ""

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
            error_msg = result.get("error", "Link tunnel setup failed")
            if result.get("partial_state"):
                error_msg = f"PARTIAL_STATE: {error_msg}"
            link_state.error_message = error_msg
            _sync_oper_state(session, link_state)
            log_parts.append(f"  {link_state.link_name}: FAILED - {link_state.error_message}")
            return False

        # Store VNI and per-side VLAN tags
        vni = result.get("vni", 0)
        link_state.vni = vni
        local_vlans = result.get("local_vlans", {})
        link_state.source_vlan_tag = local_vlans.get("a")
        link_state.target_vlan_tag = local_vlans.get("b")

        # Verify the link is actually connected
        if verify:
            is_valid, error = await verify_link_connected(session, link_state, host_to_agent)
            if not is_valid:
                link_state.actual_state = "error"
                link_state.error_message = f"Verification failed: {error}"
                _sync_oper_state(session, link_state)
                log_parts.append(f"  {link_state.link_name}: FAILED - {link_state.error_message}")
                return False

            # Update interface mappings after successful link creation
            await update_interface_mappings(session, link_state, host_to_agent)

        # Create VxlanTunnel record for tracking
        agent_ip_a = await agent_client.resolve_agent_ip(agent_a.address)
        agent_ip_b = await agent_client.resolve_agent_ip(agent_b.address)

        # Guard: clean up duplicate tunnels with same VNI between same agent pair
        existing_dup = (
            session.query(models.VxlanTunnel)
            .filter(
                models.VxlanTunnel.vni == vni,
                models.VxlanTunnel.status != "cleanup",
                models.VxlanTunnel.link_state_id != link_state.id,
                or_(
                    and_(
                        models.VxlanTunnel.agent_a_id == agent_a.id,
                        models.VxlanTunnel.agent_b_id == agent_b.id,
                    ),
                    and_(
                        models.VxlanTunnel.agent_a_id == agent_b.id,
                        models.VxlanTunnel.agent_b_id == agent_a.id,
                    ),
                ),
            )
            .first()
        )
        if existing_dup:
            logger.warning(
                f"Duplicate tunnel detected: VNI={vni}, "
                f"existing={existing_dup.id}, new link={link_state.id}"
            )
            # Tear down the stale duplicate
            for dup_agent in (agent_a, agent_b):
                try:
                    await agent_client.detach_overlay_interface_on_agent(
                        dup_agent,
                        lab_id=existing_dup.lab_id,
                        container_name="",
                        interface_name="",
                        link_id=f"dup-{existing_dup.id}",
                    )
                except Exception:
                    pass
            session.delete(existing_dup)

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
                vni=vni,
                vlan_tag=0,  # Not used in per-link VNI model
                agent_a_id=agent_a.id,
                agent_a_ip=agent_ip_a,
                agent_b_id=agent_b.id,
                agent_b_ip=agent_ip_b,
                status="active",
                port_name=compute_vxlan_port_name(lab_id, link_state.link_name),
            )
            session.add(tunnel)
        else:
            # Refresh endpoint ownership on every reconcile/recreate.
            # Host placement can change after node migration, restart recovery,
            # or split-brain cleanup; stale agent IDs here will misdirect
            # declare-state convergence to the wrong host.
            existing_tunnel.agent_a_id = agent_a.id
            existing_tunnel.agent_a_ip = agent_ip_a
            existing_tunnel.agent_b_id = agent_b.id
            existing_tunnel.agent_b_ip = agent_ip_b
            existing_tunnel.vni = vni
            existing_tunnel.status = "active"
            if not existing_tunnel.port_name:
                existing_tunnel.port_name = compute_vxlan_port_name(lab_id, link_state.link_name)

        # Only now mark as "up" and set attachment flags
        link_state.actual_state = "up"
        link_state.source_carrier_state = "on"
        link_state.target_carrier_state = "on"
        link_state.source_vxlan_attached = True
        link_state.target_vxlan_attached = True
        link_state.error_message = None
        _sync_oper_state(session, link_state)
        log_parts.append(
            f"  {link_state.link_name}: OK (VNI {vni}, per-link VXLAN)"
        )
        return True

    except Exception as e:
        logger.error(f"Failed to create cross-host link {link_state.link_name}: {e}")
        link_state.actual_state = "error"
        link_state.error_message = str(e)
        _sync_oper_state(session, link_state)
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

    # Detach external interfaces (only when no other lab references them)
    external_nodes = (
        session.query(models.Node)
        .filter(
            models.Node.lab_id == lab_id,
            models.Node.node_type == "external",
            models.Node.managed_interface_id.isnot(None),
        )
        .all()
    )
    for ext_node in external_nodes:
        mi = session.get(models.AgentManagedInterface, ext_node.managed_interface_id)
        if not mi:
            continue
        # Reference count: only detach if no OTHER lab uses this interface
        other_refs = (
            session.query(models.Node)
            .filter(
                models.Node.managed_interface_id == ext_node.managed_interface_id,
                models.Node.lab_id != lab_id,
                models.Node.node_type == "external",
            )
            .count()
        )
        if other_refs == 0:
            agent = host_to_agent.get(mi.host_id)
            if agent:
                try:
                    await agent_client.detach_external_on_agent(agent, mi.name)
                    log_parts.append(f"  Detached external interface {mi.name} from {agent.name}")
                except Exception as e:
                    logger.warning(f"Failed to detach external {mi.name}: {e}")

    # Delete ALL LinkState records for this lab (not just cross-host)
    # Fresh records will be created on the next deploy
    all_link_states = (
        session.query(models.LinkState)
        .filter(models.LinkState.lab_id == lab_id)
        .all()
    )
    for ls in all_link_states:
        session.delete(ls)

    session.commit()

    logger.info(f"VXLAN teardown complete: {success_count} agents OK, {fail_count} failed")
    return success_count, fail_count
