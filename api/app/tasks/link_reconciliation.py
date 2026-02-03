"""Link reconciliation background task.

This module periodically verifies that link states match actual OVS
configuration and attempts repair if discrepancies are found.

Key operations:
1. Query all links marked as "up"
2. Verify VLAN tags match on both endpoints
3. Attempt repair if mismatch detected
4. Mark as error if repair fails
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app import agent_client, models
from app.db import SessionLocal
from app.services.link_validator import verify_link_connected
from app.tasks.link_orchestration import create_same_host_link, create_cross_host_link
from app.topology import _normalize_interface_name

logger = logging.getLogger(__name__)

# Configuration
RECONCILIATION_INTERVAL_SECONDS = 60
RECONCILIATION_ENABLED = True


async def reconcile_link_states(session: Session) -> dict:
    """Reconcile link_states with actual OVS configuration.

    For each link marked as "up":
    1. Query VLAN tags from both agents
    2. Compare with expected configuration
    3. If mismatch: attempt repair or mark as error

    Args:
        session: Database session

    Returns:
        Dict with reconciliation results
    """
    results = {
        "checked": 0,
        "valid": 0,
        "repaired": 0,
        "errors": 0,
        "skipped": 0,
    }

    # Get all links marked as "up"
    up_links = (
        session.query(models.LinkState)
        .filter(models.LinkState.actual_state == "up")
        .all()
    )

    if not up_links:
        return results

    # Build host_to_agent map for all online agents
    agents = (
        session.query(models.Host)
        .filter(models.Host.status == "online")
        .all()
    )
    host_to_agent = {a.id: a for a in agents}

    for link in up_links:
        results["checked"] += 1

        # Skip if required agents are offline
        if link.source_host_id and link.source_host_id not in host_to_agent:
            results["skipped"] += 1
            continue
        if link.target_host_id and link.target_host_id not in host_to_agent:
            results["skipped"] += 1
            continue

        try:
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


async def attempt_link_repair(
    session: Session,
    link: models.LinkState,
    host_to_agent: dict[str, models.Host],
) -> bool:
    """Try to repair a broken link by re-calling hot_connect or VTEP attach.

    Args:
        session: Database session
        link: The link to repair
        host_to_agent: Map of host_id to Host objects

    Returns:
        True if repair succeeded, False otherwise
    """
    log_parts: list[str] = []

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

            session = SessionLocal()
            try:
                results = await reconcile_link_states(session)

                if results["checked"] > 0:
                    logger.info(
                        f"Link reconciliation: checked={results['checked']}, "
                        f"valid={results['valid']}, repaired={results['repaired']}, "
                        f"errors={results['errors']}, skipped={results['skipped']}"
                    )

            except Exception as e:
                logger.error(f"Link reconciliation error: {e}")
            finally:
                session.close()

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
        "errors": 0,
        "skipped": 0,
    }

    # Get all links for this lab marked as "up"
    up_links = (
        session.query(models.LinkState)
        .filter(
            models.LinkState.lab_id == lab_id,
            models.LinkState.actual_state == "up",
        )
        .all()
    )

    if not up_links:
        return results

    # Build host_to_agent map
    agents = (
        session.query(models.Host)
        .filter(models.Host.status == "online")
        .all()
    )
    host_to_agent = {a.id: a for a in agents}

    for link in up_links:
        results["checked"] += 1

        # Skip if required agents are offline
        if link.source_host_id and link.source_host_id not in host_to_agent:
            results["skipped"] += 1
            continue
        if link.target_host_id and link.target_host_id not in host_to_agent:
            results["skipped"] += 1
            continue

        try:
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
