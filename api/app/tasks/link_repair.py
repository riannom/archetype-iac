"""Link repair operations for broken or drifted links.

Extracted from link_reconciliation.py. Provides:
- attempt_partial_recovery: Re-attach missing sides of cross-host links
- attempt_vlan_repair: Fix VLAN tag drift (same-host + cross-host)
- attempt_link_repair: Full link re-creation as last resort
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app import agent_client, models
from app.services.interface_naming import normalize_interface
from app.services.link_operational_state import recompute_link_oper_state
from app.tasks.link_orchestration import create_same_host_link, create_cross_host_link
from app.utils.locks import get_link_state_by_id_for_update

logger = logging.getLogger(__name__)


def _sync_oper_state(session: Session, link_state: models.LinkState) -> None:
    recompute_link_oper_state(session, link_state)


async def attempt_partial_recovery(
    session: Session,
    link: models.LinkState,
    host_to_agent: dict[str, models.Host],
) -> bool:
    """Attempt partial recovery of a cross-host link after agent restart.

    This function re-attaches only the missing side(s) of a link instead
    of recreating the entire link. This is more efficient when only one
    agent restarted.

    Args:
        session: Database session
        link: The link to recover
        host_to_agent: Map of host_id to Host objects

    Returns:
        True if recovery succeeded, False otherwise
    """
    # Re-query with row-level lock to prevent concurrent modifications
    link = get_link_state_by_id_for_update(session, link.id, skip_locked=True)
    if not link:
        logger.debug("Link skipped for recovery (locked or deleted)")
        return False

    if not link.is_cross_host:
        # This shouldn't happen, but handle gracefully
        logger.warning(f"Partial recovery called on same-host link {link.link_name}")
        return False

    agent_a = host_to_agent.get(link.source_host_id)
    agent_b = host_to_agent.get(link.target_host_id)

    if not agent_a or not agent_b:
        logger.warning(f"Agents not available for link {link.link_name} recovery")
        return False

    # Get agent IPs, VNI, and overlay MTU
    from app.agent_client import resolve_data_plane_ip
    from app.services.link_manager import allocate_vni
    from app.routers.infrastructure import get_or_create_settings
    agent_ip_a = await resolve_data_plane_ip(session, agent_a)
    agent_ip_b = await resolve_data_plane_ip(session, agent_b)
    infra = get_or_create_settings(session)
    overlay_mtu = infra.overlay_mtu or 0

    # Ensure VNI is set (agent discovers local VLANs independently)
    if not link.vni:
        link.vni = allocate_vni(link.lab_id, link.link_name)

    interface_a = normalize_interface(link.source_interface) if link.source_interface else ""
    interface_b = normalize_interface(link.target_interface) if link.target_interface else ""

    source_ok = link.source_vxlan_attached
    target_ok = link.target_vxlan_attached

    # Re-attach source side if needed
    if not source_ok:
        try:
            result = await agent_client.attach_overlay_interface_on_agent(
                agent_a,
                lab_id=link.lab_id,
                container_name=link.source_node,
                interface_name=interface_a,
                vni=link.vni if link.vni else allocate_vni(link.lab_id, link.link_name),
                local_ip=agent_ip_a,
                remote_ip=agent_ip_b,
                link_id=link.link_name,
                tenant_mtu=overlay_mtu,
            )
            if result.get("success"):
                source_ok = True
                link.source_vxlan_attached = True
                link.source_vlan_tag = result.get("local_vlan")
                logger.info(f"Re-attached source side of {link.link_name}")
            else:
                logger.error(f"Failed to re-attach source: {result.get('error')}")
        except Exception as e:
            logger.error(f"Source re-attachment failed for {link.link_name}: {e}")

    # Re-attach target side if needed
    if not target_ok:
        try:
            result = await agent_client.attach_overlay_interface_on_agent(
                agent_b,
                lab_id=link.lab_id,
                container_name=link.target_node,
                interface_name=interface_b,
                vni=link.vni if link.vni else allocate_vni(link.lab_id, link.link_name),
                local_ip=agent_ip_b,
                remote_ip=agent_ip_a,
                link_id=link.link_name,
                tenant_mtu=overlay_mtu,
            )
            if result.get("success"):
                target_ok = True
                link.target_vxlan_attached = True
                link.target_vlan_tag = result.get("local_vlan")
                logger.info(f"Re-attached target side of {link.link_name}")
            else:
                logger.error(f"Failed to re-attach target: {result.get('error')}")
        except Exception as e:
            logger.error(f"Target re-attachment failed for {link.link_name}: {e}")

    # Check if both sides are now attached
    if source_ok and target_ok:
        link.actual_state = "up"
        link.error_message = None
        link.source_carrier_state = "on"
        link.target_carrier_state = "on"
        _sync_oper_state(session, link)
        logger.info(f"Link {link.link_name} fully recovered")
        return True
    else:
        link.error_message = (
            f"Partial recovery: source={'ok' if source_ok else 'failed'}, "
            f"target={'ok' if target_ok else 'failed'}"
        )
        _sync_oper_state(session, link)
        return False


async def attempt_vlan_repair(
    session: Session,
    link: models.LinkState,
    host_to_agent: dict[str, models.Host],
) -> bool:
    """Lightweight repair: fix VLAN tag drift without full link recreation.

    For same-host links: re-set both ports to the DB-stored vlan_tag.
    For cross-host links: update the VXLAN tunnel port tag to match
    the container's current VLAN (which changed after container restart).

    Args:
        session: Database session
        link: The link with VLAN mismatch
        host_to_agent: Map of host_id to Host objects

    Returns:
        True if repair succeeded, False otherwise
    """
    link = get_link_state_by_id_for_update(session, link.id, skip_locked=True)
    if not link:
        logger.debug("Link skipped for VLAN repair (locked or deleted)")
        return False

    try:
        if not link.is_cross_host:
            return await _repair_same_host_vlan(session, link, host_to_agent)
        else:
            return await _repair_cross_host_vlan(session, link, host_to_agent)
    except Exception as e:
        logger.error(f"VLAN repair failed for {link.link_name}: {e}")
        return False


async def _repair_same_host_vlan(
    session: Session,
    link: models.LinkState,
    host_to_agent: dict[str, models.Host],
) -> bool:
    """Repair same-host link by re-matching VLAN tags.

    After a container restart, one side gets a new VLAN tag while the other
    keeps the old one. Fix by reading both current tags and setting the
    drifted port to match the other.
    """
    agent = host_to_agent.get(link.source_host_id)
    if not agent:
        return False

    source_iface = normalize_interface(link.source_interface) if link.source_interface else ""
    target_iface = normalize_interface(link.target_interface) if link.target_interface else ""

    # Read current VLAN tags from OVS (ground truth)
    source_vlan = await agent_client.get_interface_vlan_from_agent(
        agent, link.lab_id, link.source_node, source_iface, read_from_ovs=True,
    )
    target_vlan = await agent_client.get_interface_vlan_from_agent(
        agent, link.lab_id, link.target_node, target_iface, read_from_ovs=True,
    )

    if source_vlan is None or target_vlan is None:
        return False

    if source_vlan == target_vlan:
        # Already matching — update DB and return success
        link.vlan_tag = source_vlan
        link.source_vlan_tag = source_vlan
        link.target_vlan_tag = source_vlan
        return True

    # For same-host links the simplest fix is to re-call hot_connect
    # which will re-match the VLAN tags. This is already lightweight.
    result = await agent_client.create_link_on_agent(
        agent,
        lab_id=link.lab_id,
        source_node=link.source_node,
        source_interface=source_iface,
        target_node=link.target_node,
        target_interface=target_iface,
    )

    if result.get("success"):
        # vlan_tag is nested inside the "link" sub-object from agent response
        link_data = result.get("link", {})
        new_vlan = link_data.get("vlan_tag") if isinstance(link_data, dict) else None
        link.vlan_tag = new_vlan
        link.source_vlan_tag = new_vlan
        link.target_vlan_tag = new_vlan
        logger.info(f"Same-host VLAN repair succeeded for {link.link_name}: tag={new_vlan}")
        return True

    return False


async def _repair_cross_host_vlan(
    session: Session,
    link: models.LinkState,
    host_to_agent: dict[str, models.Host],
) -> bool:
    """Repair cross-host link by pushing DB-stored VLAN tags to reality.

    DB is the source of truth. After a container restart the container
    port gets a new random VLAN tag. Fix by pushing the DB tag to BOTH
    the container OVS port AND the VXLAN tunnel port.
    """
    source_agent = host_to_agent.get(link.source_host_id)
    target_agent = host_to_agent.get(link.target_host_id)
    if not source_agent or not target_agent:
        return False

    vxlan_port = agent_client.compute_vxlan_port_name(link.lab_id, link.link_name)

    repaired = True
    for side, agent, node, iface, db_vlan in [
        ("source", source_agent, link.source_node, link.source_interface, link.source_vlan_tag),
        ("target", target_agent, link.target_node, link.target_interface, link.target_vlan_tag),
    ]:
        if not db_vlan:
            logger.warning(f"No DB VLAN tag for {side} of {link.link_name}")
            repaired = False
            continue

        # Discover container's OVS port name via port-state (fresh, not IM)
        iface_norm = normalize_interface(iface) if iface else ""
        ports = await agent_client.get_lab_port_state(agent, link.lab_id)
        container_port = None
        if ports:
            for p in ports:
                if p.get("node_name") == node and p.get("interface_name") == iface_norm:
                    container_port = p.get("ovs_port_name")
                    break

        # Push DB tag to container port
        if container_port:
            ok = await agent_client.set_port_vlan_on_agent(agent, container_port, db_vlan)
            if ok:
                logger.info(
                    f"Cross-host VLAN repair ({side}): container port "
                    f"{container_port} -> tag={db_vlan}"
                )
            else:
                logger.error(
                    f"Failed to set container port {container_port} "
                    f"to tag={db_vlan} on {agent.name}"
                )
                repaired = False
        else:
            logger.warning(
                f"Cannot find container port for {node}:{iface_norm} "
                f"on {agent.name} for {link.link_name}"
            )
            repaired = False

        # Push DB tag to VXLAN tunnel port
        ok = await agent_client.set_port_vlan_on_agent(agent, vxlan_port, db_vlan)
        if ok:
            logger.info(
                f"Cross-host VLAN repair ({side}): tunnel port "
                f"{vxlan_port} -> tag={db_vlan}"
            )
        else:
            logger.error(
                f"Failed to set tunnel port {vxlan_port} "
                f"to tag={db_vlan} on {agent.name}"
            )
            repaired = False

    return repaired


async def attempt_link_repair(
    session: Session,
    link: models.LinkState,
    host_to_agent: dict[str, models.Host],
) -> bool:
    """Try to repair a broken link by re-calling hot_connect or VTEP attach.

    Uses row-level locking to prevent concurrent modifications.

    Args:
        session: Database session
        link: The link to repair
        host_to_agent: Map of host_id to Host objects

    Returns:
        True if repair succeeded, False otherwise
    """
    log_parts: list[str] = []

    # Re-query with row-level lock to prevent concurrent modifications
    link = get_link_state_by_id_for_update(session, link.id, skip_locked=True)
    if not link:
        logger.debug("Link skipped for repair (locked or deleted)")
        return False

    try:
        if link.is_cross_host:
            # Re-create cross-host link
            success = await create_cross_host_link(
                session,
                link.lab_id,
                link,
                host_to_agent,
                log_parts,
                verify=True,  # Verify after repair
            )
        else:
            # Re-create same-host link
            success = await create_same_host_link(
                session,
                link.lab_id,
                link,
                host_to_agent,
                log_parts,
                verify=True,  # Verify after repair
            )

        return success

    except Exception as e:
        logger.error(f"Link repair failed for {link.link_name}: {e}")
        return False
