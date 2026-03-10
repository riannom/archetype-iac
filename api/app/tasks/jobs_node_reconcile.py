"""Node reconciliation job executor and cross-host link helpers.

``run_node_reconcile`` delegates to :class:`NodeLifecycleManager` for the
actual reconciliation work.  ``_create_cross_host_links_if_ready`` is called
after each sync job to wire up VXLAN tunnels when both endpoints are deployed.

Shared utilities (locking, preflight, webhooks, metrics) live in ``jobs.py``.
"""
from __future__ import annotations

import logging

from app import agent_client, models
from app.db import get_session
from app.state import (
    HostStatus,
    JobStatus,
    LinkActualState,
)
from app.utils.db import release_db_transaction_for_io as _release_db_transaction_for_io
from app.utils.time import utcnow

logger = logging.getLogger(__name__)


async def run_node_reconcile(
    job_id: str,
    lab_id: str,
    node_ids: list[str],
    provider: str = "docker",
):
    """Reconcile nodes to match their desired state.

    Thin wrapper that delegates to NodeLifecycleManager.
    See api/app/tasks/node_lifecycle.py for the full implementation.

    Args:
        job_id: The job ID
        lab_id: The lab ID
        node_ids: List of node IDs to reconcile
        provider: Provider for the job (default: docker)
    """
    from app.tasks.node_lifecycle import NodeLifecycleManager

    with get_session() as session:
        try:
            job = session.get(models.Job, job_id)
            if not job:
                logger.error(f"Job {job_id} not found in database")
                return

            lab = session.get(models.Lab, lab_id)
            if not lab:
                logger.error(f"Lab {lab_id} not found in database")
                job.status = JobStatus.FAILED.value
                job.completed_at = utcnow()
                job.log_path = f"ERROR: Lab {lab_id} not found"
                session.commit()
                return

            manager = NodeLifecycleManager(session, lab, job, node_ids, provider)
            await manager.execute()

        except Exception as e:
            logger.exception(f"Job {job_id} failed with unexpected error: {e}")
            try:
                session.rollback()
                job = session.get(models.Job, job_id)
                if job:
                    job.status = JobStatus.FAILED.value
                    job.completed_at = utcnow()
                    job.log_path = f"ERROR: Unexpected error: {e}"
                    session.commit()
            except Exception as inner_e:
                logger.exception(f"Critical error handling job {job_id} failure: {inner_e}")


async def _create_cross_host_links_if_ready(
    session,
    lab_id: str,
    log_parts: list[str],
) -> None:
    """Create cross-host links (VXLAN tunnels) if both endpoints are ready.

    This is called after each sync job completes to check if any cross-host
    links can now be created. A link can be created when:
    1. Both endpoint nodes are deployed (have containers running)
    2. Both agents are online
    3. The link hasn't already been created

    Uses link_ops_lock to serialize link_states modifications and prevent
    deadlocks from concurrent flush operations.

    Args:
        session: Database session
        lab_id: Lab identifier
        log_parts: List to append log messages to
    """
    from app.tasks.link_orchestration import create_deployment_links
    from app.utils.locks import link_ops_lock

    # Check if there are any cross-host links that need creation
    # First, check if any link_states exist with is_cross_host=True and actual_state != "up"
    pending_cross_host = (
        session.query(models.LinkState)
        .filter(
            models.LinkState.lab_id == lab_id,
            models.LinkState.is_cross_host,
            models.LinkState.actual_state != LinkActualState.UP.value,
        )
        .count()
    )

    # Also check for links that haven't been categorized yet (no host IDs set)
    uncategorized_links = (
        session.query(models.LinkState)
        .filter(
            models.LinkState.lab_id == lab_id,
            models.LinkState.source_host_id.is_(None),
        )
        .count()
    )

    # Check if there are any links defined that don't have LinkState records yet
    from app.services.topology import TopologyService
    topo_service = TopologyService(session)
    db_links = topo_service.get_links(lab_id)
    existing_link_names = {
        ls.link_name
        for ls in session.query(models.LinkState.link_name)
        .filter(models.LinkState.lab_id == lab_id)
        .all()
    }
    new_links = [lnk for lnk in db_links if lnk.link_name not in existing_link_names]

    # Determine if we need to force VXLAN recreation after agent restarts.
    # If there are cross-host links but no tunnels reported for this lab, rebuild.
    force_recreate = False
    if not pending_cross_host and not uncategorized_links and not new_links:
        cross_host_links = (
            session.query(models.LinkState)
            .filter(
                models.LinkState.lab_id == lab_id,
                models.LinkState.is_cross_host,
            )
            .count()
        )
        if cross_host_links > 0:
            placements = (
                session.query(models.NodePlacement)
                .filter(models.NodePlacement.lab_id == lab_id)
                .all()
            )
            host_ids = {p.host_id for p in placements}
            for host_id in host_ids:
                agent = session.get(models.Host, host_id)
                if not agent or not agent_client.is_agent_online(agent):
                    continue
                _release_db_transaction_for_io(
                    session,
                    context=f"overlay status probe for lab {lab_id}",
                )
                status = await agent_client.get_overlay_status_from_agent(agent)
                tunnels = [t for t in status.get("tunnels", []) if t.get("lab_id") == lab_id]
                link_tunnels = [t for t in status.get("link_tunnels", []) if t.get("lab_id") == lab_id]
                if not tunnels and not link_tunnels:
                    force_recreate = True
                    break

        if not force_recreate:
            # No cross-host links need creation
            return

    logger.info(
        f"Checking cross-host links for lab {lab_id}: "
        f"{pending_cross_host} pending, {uncategorized_links} uncategorized, {len(new_links)} new"
    )

    # Build host_to_agent map with all online agents
    all_agents = session.query(models.Host).filter(models.Host.status == HostStatus.ONLINE.value).all()
    host_to_agent: dict[str, models.Host] = {}
    for agent in all_agents:
        if agent_client.is_agent_online(agent):
            host_to_agent[agent.id] = agent

    if not host_to_agent:
        logger.warning("No online agents available for cross-host link creation")
        return

    # Call create_deployment_links which handles all the logic:
    # - Creates LinkState records if needed
    # - Determines which links are cross-host based on node placements
    # - Creates VXLAN tunnels for cross-host links where both endpoints are ready
    # - Skips links that are already "up"
    log_parts.append("")
    log_parts.append("=== Phase 4: Cross-Host Links ===")

    # Serialize link_states modifications via Redis lock to prevent deadlocks
    with link_ops_lock(lab_id) as lock_acquired:
        if not lock_acquired:
            logger.debug(
                f"Skipping cross-host link creation for lab {lab_id}: "
                f"link ops lock held by another operation"
            )
            return

        try:
            _release_db_transaction_for_io(
                session,
                context=f"cross-host link creation for lab {lab_id}",
            )
            links_ok, links_failed = await create_deployment_links(
                session, lab_id, host_to_agent, log_parts
            )
            if links_ok > 0 or links_failed > 0:
                logger.info(f"Cross-host link creation: {links_ok} OK, {links_failed} failed")
        except Exception as e:
            logger.error(f"Failed to create cross-host links for lab {lab_id}: {e}")
            log_parts.append(f"  Cross-host link creation failed: {e}")
