"""Link reconciliation background task.

This module periodically verifies that link states match actual OVS
configuration and attempts repair if discrepancies are found.

Key operations:
1. Query all links marked as "up" or in "error" state needing recovery
2. Verify VLAN tags match on both endpoints
3. Attempt repair if mismatch detected (supports partial re-attachment)
4. Mark as error if repair fails
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app import agent_client, models
from app.config import settings
from app.db import get_session
from app.services.link_operational_state import recompute_link_oper_state
from app.services.link_validator import verify_link_connected, is_vlan_mismatch
from app.tasks.link_orchestration import create_same_host_link, create_cross_host_link
from app.services.interface_naming import normalize_interface
from app.utils.link import links_needing_reconciliation_filter
from app.utils.locks import get_link_state_by_id_for_update

logger = logging.getLogger(__name__)

# Configuration
RECONCILIATION_INTERVAL_SECONDS = 60
RECONCILIATION_ENABLED = True


def _sync_oper_state(session: Session, link_state: models.LinkState) -> None:
    recompute_link_oper_state(session, link_state)


async def _cleanup_deleted_links(
    session: Session,
    host_to_agent: dict[str, models.Host],
    lab_id: str | None = None,
) -> int:
    """Remove LinkState records marked as deleted and tear down their tunnels.

    This prevents stale VXLAN overlays from persisting after interface renames
    (e.g., Ethernet -> eth) or topology updates.
    """
    from app.tasks.live_links import teardown_link

    query = session.query(models.LinkState).filter(
        models.LinkState.desired_state == "deleted"
    )
    if lab_id:
        query = query.filter(models.LinkState.lab_id == lab_id)
    deleted_links = query.all()

    if not deleted_links:
        return 0

    removed = 0
    for link_state in deleted_links:
        link_info = {
            "link_name": link_state.link_name,
            "source_node": link_state.source_node,
            "source_interface": link_state.source_interface,
            "target_node": link_state.target_node,
            "target_interface": link_state.target_interface,
            "is_cross_host": link_state.is_cross_host,
            "actual_state": link_state.actual_state,
            "source_host_id": link_state.source_host_id,
            "target_host_id": link_state.target_host_id,
            "vni": link_state.vni,
        }
        try:
            await teardown_link(session, link_state.lab_id, link_info, host_to_agent)
        except Exception as e:
            logger.warning(
                f"Failed to teardown deleted link {link_state.link_name}: {e}"
            )

        # Always delete VXLAN tunnel records tied to this LinkState
        session.query(models.VxlanTunnel).filter(
            models.VxlanTunnel.link_state_id == link_state.id
        ).delete(synchronize_session=False)

        session.delete(link_state)
        removed += 1

    session.commit()
    return removed


async def reconcile_link_states(session: Session) -> dict:
    """Reconcile link_states with actual OVS configuration.

    Processes two categories of links:
    1. Links marked as "up" - verify VLAN tags match
    2. Cross-host links in "error" state with partial attachment - attempt recovery

    Args:
        session: Database session

    Returns:
        Dict with reconciliation results
    """
    results = {
        "checked": 0,
        "valid": 0,
        "repaired": 0,
        "recovered": 0,
        "errors": 0,
        "skipped": 0,
    }

    # Build host_to_agent map for all online agents
    agents = (
        session.query(models.Host)
        .filter(models.Host.status == "online")
        .all()
    )
    host_to_agent = {a.id: a for a in agents}

    # Remove deleted links first to prevent stale overlays
    deleted_removed = await _cleanup_deleted_links(session, host_to_agent)
    if deleted_removed > 0:
        logger.info(f"Cleaned up {deleted_removed} deleted LinkState record(s)")

    # Get links that need attention:
    # - Links marked as "up" (verification)
    # - Cross-host links in "error" with partial attachment (recovery)
    links_to_check = (
        session.query(models.LinkState)
        .filter(links_needing_reconciliation_filter())
        .all()
    )

    if not links_to_check:
        return results

    for link in links_to_check:
        results["checked"] += 1

        # Skip if required agents are offline
        if link.source_host_id and link.source_host_id not in host_to_agent:
            results["skipped"] += 1
            continue
        if link.target_host_id and link.target_host_id not in host_to_agent:
            results["skipped"] += 1
            continue

        try:
            # Handle error links needing recovery
            if link.actual_state == "error" and link.is_cross_host:
                logger.info(
                    f"Attempting recovery for link {link.link_name} "
                    f"(source_attached={link.source_vxlan_attached}, "
                    f"target_attached={link.target_vxlan_attached})"
                )
                recovered = await attempt_partial_recovery(session, link, host_to_agent)
                if recovered:
                    results["recovered"] += 1
                    logger.info(f"Link {link.link_name} recovered successfully")
                else:
                    results["errors"] += 1
                    logger.error(f"Link {link.link_name} recovery failed")
                continue

            # For "up" links, verify connectivity
            is_valid, error = await verify_link_connected(session, link, host_to_agent)

            if is_valid:
                results["valid"] += 1
            else:
                logger.warning(f"Link {link.link_name} verification failed: {error}")

                if error and error.startswith("Overlay status unavailable"):
                    results["skipped"] += 1
                    continue

                # Try lightweight VLAN repair first if it's a VLAN mismatch
                if is_vlan_mismatch(error):
                    vlan_repaired = await attempt_vlan_repair(session, link, host_to_agent)
                    if vlan_repaired:
                        # Re-verify after repair
                        is_valid2, _ = await verify_link_connected(session, link, host_to_agent)
                        if is_valid2:
                            results["repaired"] += 1
                            logger.info(f"Link {link.link_name} VLAN repair succeeded")
                            continue

                # Fall through to full link repair
                repaired = await attempt_link_repair(session, link, host_to_agent)
                if repaired:
                    results["repaired"] += 1
                    logger.info(f"Link {link.link_name} repaired successfully")
                else:
                    link.actual_state = "error"
                    link.error_message = f"Reconciliation failed: {error}"
                    _sync_oper_state(session, link)
                    results["errors"] += 1
                    logger.error(f"Link {link.link_name} repair failed")

        except Exception as e:
            logger.error(f"Error reconciling link {link.link_name}: {e}")
            results["errors"] += 1

    session.commit()
    return results


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
    link = get_link_state_by_id_for_update(session, link.id)
    if not link:
        logger.warning("Link not found for recovery (may have been deleted)")
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
    link = get_link_state_by_id_for_update(session, link.id)
    if not link:
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

    # Determine which side drifted by comparing to DB-stored tag
    db_tag = link.vlan_tag
    if db_tag is not None and source_vlan == db_tag:
        # Target drifted — set target to match source
        fix_node, fix_iface, fix_to = link.target_node, target_iface, source_vlan
    elif db_tag is not None and target_vlan == db_tag:
        # Source drifted — set source to match target
        fix_node, fix_iface, fix_to = link.source_node, source_iface, target_vlan
    else:
        # Both drifted or no DB tag — pick source as canonical, set target to match
        fix_node, fix_iface, fix_to = link.target_node, target_iface, source_vlan

    # Get the OVS port name for the drifted interface
    port_info = await agent_client.get_interface_vlan_from_agent(
        agent, link.lab_id, fix_node, fix_iface, read_from_ovs=False,
    )
    # We need the port name, not just the VLAN. Use the agent's port resolution.
    # The set_port_vlan endpoint works on port names — we need to resolve
    # container:interface -> OVS port name. Use the hot_connect approach instead:
    # just re-call create_link_on_agent which is idempotent.
    # Actually, for same-host links the simplest fix is to re-call hot_connect
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
    """Repair cross-host link by updating VXLAN tunnel port tags.

    After a container restart, the container port gets a new VLAN tag.
    The VXLAN tunnel port still has the old tag. Fix by updating the
    tunnel port to match the container's current tag.
    """
    source_agent = host_to_agent.get(link.source_host_id)
    target_agent = host_to_agent.get(link.target_host_id)
    if not source_agent or not target_agent:
        return False

    source_iface = normalize_interface(link.source_interface) if link.source_interface else ""
    target_iface = normalize_interface(link.target_interface) if link.target_interface else ""
    vxlan_port = agent_client.compute_vxlan_port_name(link.lab_id, link.link_name)

    repaired = True
    for side, agent, node, iface, tag_attr in [
        ("source", source_agent, link.source_node, source_iface, "source_vlan_tag"),
        ("target", target_agent, link.target_node, target_iface, "target_vlan_tag"),
    ]:
        # Read the container port's current VLAN (authoritative after restart)
        endpoint_vlan = await agent_client.get_interface_vlan_from_agent(
            agent, link.lab_id, node, iface, read_from_ovs=True,
        )
        if endpoint_vlan is None:
            logger.warning(f"Cannot read {side} endpoint VLAN for {link.link_name}")
            repaired = False
            continue

        # Check if the VXLAN tunnel port has the right tag
        status = await agent_client.get_overlay_status_from_agent(agent)
        if status.get("error"):
            repaired = False
            continue

        matching_tunnel = next(
            (
                t for t in status.get("link_tunnels", [])
                if t.get("link_id") == link.link_name
                or t.get("interface_name") == vxlan_port
            ),
            None,
        )
        if not matching_tunnel:
            # Tunnel port missing entirely — can't do lightweight repair
            repaired = False
            continue

        tunnel_vlan = matching_tunnel.get("local_vlan")
        try:
            tunnel_vlan_int = int(tunnel_vlan) if tunnel_vlan is not None else None
        except (TypeError, ValueError):
            tunnel_vlan_int = None

        if tunnel_vlan_int != endpoint_vlan:
            # Update the VXLAN port's VLAN tag to match the container
            ok = await agent_client.set_port_vlan_on_agent(agent, vxlan_port, endpoint_vlan)
            if ok:
                setattr(link, tag_attr, endpoint_vlan)
                logger.info(
                    f"Cross-host VLAN repair on {agent.name} for {link.link_name}: "
                    f"tunnel {vxlan_port} tag {tunnel_vlan_int} -> {endpoint_vlan}"
                )
            else:
                repaired = False
        else:
            # Tag already correct, just update DB
            setattr(link, tag_attr, endpoint_vlan)

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
    link = get_link_state_by_id_for_update(session, link.id)
    if not link:
        logger.warning("Link not found for repair (may have been deleted)")
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


async def cleanup_orphaned_link_states(session: Session) -> int:
    """Clean up orphaned LinkState records and their VXLAN tunnels.

    Orphaned LinkStates have link_definition_id IS NULL, meaning the Link
    definition they referenced was deleted (e.g., interface rename from
    eth to Ethernet). Their VXLAN ports persist on OVS until explicitly
    torn down.

    Only deletes non-"up" orphans — actively working links that just lost
    their definition are left alone to avoid disruption.

    Args:
        session: Database session

    Returns:
        Number of orphaned LinkStates deleted
    """
    orphaned = (
        session.query(models.LinkState)
        .filter(
            models.LinkState.link_definition_id is None,
            models.LinkState.actual_state != "up",
        )
        .all()
    )

    if not orphaned:
        return 0

    # Build host_to_agent map for teardown calls
    agents = (
        session.query(models.Host)
        .filter(models.Host.status == "online")
        .all()
    )
    host_to_agent = {a.id: a for a in agents}

    count = 0
    for ls in orphaned:
        # Check for associated VxlanTunnel and tear down on agents
        tunnel = (
            session.query(models.VxlanTunnel)
            .filter(models.VxlanTunnel.link_state_id == ls.id)
            .first()
        )
        if tunnel:
            # Tear down VXLAN ports on both agents
            for agent_id, node, iface in [
                (tunnel.agent_a_id, ls.source_node, ls.source_interface),
                (tunnel.agent_b_id, ls.target_node, ls.target_interface),
            ]:
                agent = host_to_agent.get(agent_id)
                if agent:
                    try:
                        await agent_client.detach_overlay_interface_on_agent(
                            agent,
                            lab_id=ls.lab_id,
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
                else:
                    logger.debug(
                        f"Agent {agent_id} offline, skipping VXLAN teardown for "
                        f"orphaned link {ls.link_name}"
                    )

        logger.info(
            f"Deleting orphaned LinkState: {ls.link_name} "
            f"(actual_state={ls.actual_state}, definition_id={ls.link_definition_id})"
        )
        session.delete(ls)
        count += 1

    if count > 0:
        session.commit()

    return count


async def cleanup_orphaned_tunnels(session: Session) -> int:
    """Clean up orphaned VxlanTunnel records.

    Orphaned tunnels are those where:
    - link_state_id is NULL (LinkState was deleted but tunnel remained)
    - status is "cleanup" for more than 5 minutes (teardown stalled)

    Args:
        session: Database session

    Returns:
        Number of orphaned tunnels deleted
    """
    cutoff_time = datetime.now(timezone.utc) - timedelta(seconds=settings.orphaned_tunnel_cleanup_timeout)

    orphaned = (
        session.query(models.VxlanTunnel)
        .filter(
            or_(
                models.VxlanTunnel.link_state_id is None,
                and_(
                    models.VxlanTunnel.status == "cleanup",
                    models.VxlanTunnel.updated_at < cutoff_time,
                ),
            )
        )
        .all()
    )

    # Build host_to_agent map for teardown calls
    agents = (
        session.query(models.Host)
        .filter(models.Host.status == "online")
        .all()
    )
    host_to_agent = {a.id: a for a in agents}

    count = len(orphaned)
    for tunnel in orphaned:
        link_state = None
        if tunnel.link_state_id is not None:
            link_state = (
                session.query(models.LinkState)
                .filter(models.LinkState.id == tunnel.link_state_id)
                .first()
            )

        if link_state:
            for agent_id, node, iface in [
                (tunnel.agent_a_id, link_state.source_node, link_state.source_interface),
                (tunnel.agent_b_id, link_state.target_node, link_state.target_interface),
            ]:
                agent = host_to_agent.get(agent_id)
                if agent:
                    try:
                        await agent_client.detach_overlay_interface_on_agent(
                            agent,
                            lab_id=link_state.lab_id,
                            container_name=node,
                            interface_name=normalize_interface(iface) if iface else "",
                            link_id=link_state.link_name,
                        )
                        logger.info(
                            f"Torn down VXLAN port for orphaned tunnel on agent {agent.name} "
                            f"(link {link_state.link_name})"
                        )
                    except Exception as e:
                        logger.warning(
                            f"Failed to detach VXLAN port for orphaned tunnel on agent "
                            f"{agent_id}: {e}"
                        )
                else:
                    logger.debug(
                        f"Agent {agent_id} offline, skipping VXLAN teardown "
                        f"for orphaned tunnel (link_state_id={tunnel.link_state_id})"
                    )

        logger.debug(
            f"Deleting orphaned tunnel: vni={tunnel.vni}, "
            f"link_state_id={tunnel.link_state_id}, status={tunnel.status}"
        )
        session.delete(tunnel)

    if count > 0:
        session.commit()

    return count


async def link_reconciliation_monitor():
    """Background task that periodically reconciles link states.

    This runs as a long-lived task, checking link states at regular intervals.
    """
    logger.info("Link reconciliation monitor started")

    while True:
        try:
            await asyncio.sleep(RECONCILIATION_INTERVAL_SECONDS)

            if not RECONCILIATION_ENABLED:
                continue

            with get_session() as session:
                try:
                    results = await reconcile_link_states(session)

                    if results["checked"] > 0:
                        logger.info(
                            f"Link reconciliation: checked={results['checked']}, "
                            f"valid={results['valid']}, repaired={results['repaired']}, "
                            f"recovered={results['recovered']}, "
                            f"errors={results['errors']}, skipped={results['skipped']}"
                        )

                    # Clean up orphaned LinkState records (and their VXLAN ports)
                    ls_deleted = await cleanup_orphaned_link_states(session)
                    if ls_deleted > 0:
                        logger.info(f"Cleaned up {ls_deleted} orphaned LinkState records")

                    # Clean up orphaned VxlanTunnel records
                    orphans_deleted = await cleanup_orphaned_tunnels(session)
                    if orphans_deleted > 0:
                        logger.info(f"Cleaned up {orphans_deleted} orphaned VxlanTunnel records")

                except Exception as e:
                    logger.error(f"Link reconciliation error: {e}")

        except asyncio.CancelledError:
            logger.info("Link reconciliation monitor cancelled")
            break
        except Exception as e:
            logger.error(f"Link reconciliation monitor error: {e}")
            # Continue running despite errors
            await asyncio.sleep(10)


async def reconcile_lab_links(session: Session, lab_id: str) -> dict:
    """Reconcile links for a specific lab.

    This can be called on-demand (e.g., after deployment) to verify
    all links in a lab are properly connected.

    Args:
        session: Database session
        lab_id: Lab identifier

    Returns:
        Dict with reconciliation results
    """
    results = {
        "checked": 0,
        "valid": 0,
        "repaired": 0,
        "recovered": 0,
        "errors": 0,
        "skipped": 0,
    }

    # Build host_to_agent map
    agents = (
        session.query(models.Host)
        .filter(models.Host.status == "online")
        .all()
    )
    host_to_agent = {a.id: a for a in agents}

    # Remove deleted links first to prevent stale overlays
    deleted_removed = await _cleanup_deleted_links(session, host_to_agent, lab_id=lab_id)
    if deleted_removed > 0:
        logger.info(f"Cleaned up {deleted_removed} deleted LinkState record(s) for lab {lab_id}")

    # Get links that need attention for this lab
    links_to_check = (
        session.query(models.LinkState)
        .filter(
            models.LinkState.lab_id == lab_id,
            links_needing_reconciliation_filter(),
        )
        .all()
    )

    if not links_to_check:
        return results

    for link in links_to_check:
        results["checked"] += 1

        # Skip if required agents are offline
        if link.source_host_id and link.source_host_id not in host_to_agent:
            results["skipped"] += 1
            continue
        if link.target_host_id and link.target_host_id not in host_to_agent:
            results["skipped"] += 1
            continue

        try:
            # Handle error links needing recovery
            if link.actual_state == "error" and link.is_cross_host:
                recovered = await attempt_partial_recovery(session, link, host_to_agent)
                if recovered:
                    results["recovered"] += 1
                else:
                    results["errors"] += 1
                continue

            # For "up" links, verify connectivity
            is_valid, error = await verify_link_connected(session, link, host_to_agent)

            if is_valid:
                results["valid"] += 1
            else:
                logger.warning(f"Link {link.link_name} verification failed: {error}")

                # Try lightweight VLAN repair first if it's a VLAN mismatch
                if is_vlan_mismatch(error):
                    vlan_repaired = await attempt_vlan_repair(session, link, host_to_agent)
                    if vlan_repaired:
                        is_valid2, _ = await verify_link_connected(session, link, host_to_agent)
                        if is_valid2:
                            results["repaired"] += 1
                            continue

                # Fall through to full link repair
                repaired = await attempt_link_repair(session, link, host_to_agent)
                if repaired:
                    results["repaired"] += 1
                else:
                    link.actual_state = "error"
                    link.error_message = f"Reconciliation failed: {error}"
                    _sync_oper_state(session, link)
                    results["errors"] += 1

        except Exception as e:
            logger.error(f"Error reconciling link {link.link_name}: {e}")
            results["errors"] += 1

    session.commit()
    return results
