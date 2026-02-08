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
from app.services.link_validator import verify_link_connected
from app.tasks.link_orchestration import create_same_host_link, create_cross_host_link
from app.services.interface_naming import normalize_interface
from app.utils.link import links_needing_reconciliation_filter
from app.utils.locks import get_link_state_by_id_for_update

logger = logging.getLogger(__name__)

# Configuration
RECONCILIATION_INTERVAL_SECONDS = 60
RECONCILIATION_ENABLED = True


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

    # Build host_to_agent map for all online agents
    agents = (
        session.query(models.Host)
        .filter(models.Host.status == "online")
        .all()
    )
    host_to_agent = {a.id: a for a in agents}

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

                # Attempt repair
                repaired = await attempt_link_repair(session, link, host_to_agent)
                if repaired:
                    results["repaired"] += 1
                    logger.info(f"Link {link.link_name} repaired successfully")
                else:
                    link.actual_state = "error"
                    link.error_message = f"Reconciliation failed: {error}"
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
        logger.warning(f"Link not found for recovery (may have been deleted)")
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

    # Get agent IPs and VNI
    from app.agent_client import resolve_agent_ip
    from app.services.link_manager import allocate_vni
    agent_ip_a = await resolve_agent_ip(agent_a.address)
    agent_ip_b = await resolve_agent_ip(agent_b.address)

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
            )
            if result.get("success"):
                source_ok = True
                link.source_vxlan_attached = True
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
            )
            if result.get("success"):
                target_ok = True
                link.target_vxlan_attached = True
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
        logger.info(f"Link {link.link_name} fully recovered")
        return True
    else:
        link.error_message = (
            f"Partial recovery: source={'ok' if source_ok else 'failed'}, "
            f"target={'ok' if target_ok else 'failed'}"
        )
        return False


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
        logger.warning(f"Link not found for repair (may have been deleted)")
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
                models.VxlanTunnel.link_state_id == None,
                and_(
                    models.VxlanTunnel.status == "cleanup",
                    models.VxlanTunnel.updated_at < cutoff_time,
                ),
            )
        )
        .all()
    )

    count = len(orphaned)
    for tunnel in orphaned:
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

    # Build host_to_agent map
    agents = (
        session.query(models.Host)
        .filter(models.Host.status == "online")
        .all()
    )
    host_to_agent = {a.id: a for a in agents}

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

                # Attempt repair
                repaired = await attempt_link_repair(session, link, host_to_agent)
                if repaired:
                    results["repaired"] += 1
                else:
                    link.actual_state = "error"
                    link.error_message = f"Reconciliation failed: {error}"
                    results["errors"] += 1

        except Exception as e:
            logger.error(f"Error reconciling link {link.link_name}: {e}")
            results["errors"] += 1

    session.commit()
    return results
