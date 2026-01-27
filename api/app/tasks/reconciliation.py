"""State reconciliation background task.

This task runs periodically to reconcile the database state with actual
container/VM state on agents. It addresses the fundamental problem of
state drift between the controller's view and reality.

Key scenarios handled:
1. Deploy timeouts - cEOS takes ~400s, VMs take even longer
2. Network partitions - Jobs marked failed even when nodes deployed successfully
3. Stale pending states - Nodes stuck in "pending" with no active job
4. Stale starting states - Labs stuck in "starting" for too long
5. Stuck jobs - Labs with jobs that have exceeded their timeout
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app import agent_client, models
from app.config import settings
from app.db import SessionLocal
from app.utils.job import is_job_within_timeout

logger = logging.getLogger(__name__)


async def reconcile_lab_states():
    """Query agents and reconcile lab/node states with actual container status.

    This function:
    1. Finds labs in transitional states (starting, stopping)
    2. Finds nodes in "pending" state with no active job
    3. Queries agents for actual container status
    4. Updates NodeState.actual_state to match reality
    5. Updates Lab.state based on aggregated node states
    """
    session = SessionLocal()
    try:
        # Find labs that need reconciliation:
        # - Labs in transitional states (starting, stopping, unknown)
        # - Labs where state has been stuck for too long
        now = datetime.now(timezone.utc)
        stale_cutoff = now - timedelta(seconds=settings.stale_starting_threshold)

        transitional_labs = (
            session.query(models.Lab)
            .filter(
                models.Lab.state.in_(["starting", "stopping", "unknown"]),
            )
            .all()
        )

        # Also find labs with nodes in "pending" state for too long
        pending_threshold = now - timedelta(seconds=settings.stale_pending_threshold)
        stale_pending_nodes = (
            session.query(models.NodeState)
            .filter(
                models.NodeState.actual_state == "pending",
                models.NodeState.updated_at < pending_threshold,
            )
            .all()
        )

        # Collect unique lab IDs that need reconciliation
        labs_to_reconcile = set()
        for lab in transitional_labs:
            labs_to_reconcile.add(lab.id)
        for node in stale_pending_nodes:
            labs_to_reconcile.add(node.lab_id)

        if not labs_to_reconcile:
            return  # Nothing to reconcile

        logger.info(f"Reconciling state for {len(labs_to_reconcile)} lab(s)")

        for lab_id in labs_to_reconcile:
            await _reconcile_single_lab(session, lab_id)

    except Exception as e:
        logger.error(f"Error in state reconciliation: {e}")
    finally:
        session.close()


async def _reconcile_single_lab(session, lab_id: str):
    """Reconcile a single lab's state with actual container status."""
    from app.utils.lab import get_lab_provider

    lab = session.get(models.Lab, lab_id)
    if not lab:
        return

    # Check if there's an active job for this lab
    active_job = (
        session.query(models.Job)
        .filter(
            models.Job.lab_id == lab_id,
            models.Job.status.in_(["pending", "running", "queued"]),
        )
        .first()
    )

    if active_job:
        # Check if job is still within its expected timeout window
        if is_job_within_timeout(
            active_job.action,
            active_job.status,
            active_job.started_at,
            active_job.created_at,
        ):
            logger.debug(f"Lab {lab_id} has active job {active_job.id}, skipping reconciliation")
            return
        else:
            # Job is stuck - log warning but proceed with reconciliation
            # The job_health_monitor will handle the stuck job separately
            logger.warning(
                f"Lab {lab_id} has stuck job {active_job.id} "
                f"(action={active_job.action}, status={active_job.status}), "
                f"proceeding with state reconciliation"
            )

    # Get an agent to query for status
    try:
        lab_provider = get_lab_provider(lab)
        agent = await agent_client.get_agent_for_lab(
            session, lab, required_provider=lab_provider
        )

        if not agent:
            logger.warning(f"No agent available to reconcile lab {lab_id}")
            return

        # Query actual container status from agent
        result = await agent_client.get_lab_status_from_agent(agent, lab_id)
        nodes = result.get("nodes", [])

        # Build a map of container status by node name
        container_status_map = {
            n.get("name", ""): n.get("status", "unknown") for n in nodes
        }

        logger.debug(f"Lab {lab_id} container status: {container_status_map}")

        # Update NodeState records based on actual container status
        node_states = (
            session.query(models.NodeState)
            .filter(models.NodeState.lab_id == lab_id)
            .all()
        )

        running_count = 0
        stopped_count = 0
        error_count = 0
        undeployed_count = 0

        for ns in node_states:
            container_status = container_status_map.get(ns.node_name)
            old_state = ns.actual_state
            old_is_ready = ns.is_ready

            if container_status:
                if container_status == "running":
                    ns.actual_state = "running"
                    ns.error_message = None
                    running_count += 1

                    # Check boot readiness for nodes that are running but not yet ready
                    if not ns.is_ready:
                        # Set boot_started_at if not already set
                        if not ns.boot_started_at:
                            ns.boot_started_at = datetime.now(timezone.utc)

                        # Poll agent for readiness status
                        try:
                            readiness = await agent_client.check_node_readiness(
                                agent, lab_id, ns.node_name
                            )
                            if readiness.get("is_ready", False):
                                ns.is_ready = True
                                logger.info(
                                    f"Node {ns.node_name} in lab {lab_id} is now ready"
                                )
                        except Exception as e:
                            logger.debug(f"Readiness check failed for {ns.node_name}: {e}")

                elif container_status in ("stopped", "exited"):
                    ns.actual_state = "stopped"
                    ns.error_message = None
                    ns.is_ready = False
                    ns.boot_started_at = None
                    stopped_count += 1
                elif container_status in ("error", "dead"):
                    ns.actual_state = "error"
                    ns.error_message = f"Container status: {container_status}"
                    ns.is_ready = False
                    ns.boot_started_at = None
                    error_count += 1
                else:
                    # Unknown container status
                    stopped_count += 1
            else:
                # Container doesn't exist - mark as undeployed
                if ns.actual_state not in ("undeployed", "stopped"):
                    ns.actual_state = "undeployed"
                    ns.error_message = None
                ns.is_ready = False
                ns.boot_started_at = None
                undeployed_count += 1

            if ns.actual_state != old_state:
                logger.info(
                    f"Reconciled node {ns.node_name} in lab {lab_id}: "
                    f"{old_state} -> {ns.actual_state}"
                )
            if ns.is_ready != old_is_ready and ns.is_ready:
                logger.info(
                    f"Node {ns.node_name} in lab {lab_id} boot complete"
                )

        # Update lab state based on aggregated node states
        old_lab_state = lab.state
        if error_count > 0:
            lab.state = "error"
            lab.state_error = f"{error_count} node(s) in error state"
        elif running_count > 0 and stopped_count == 0 and undeployed_count == 0:
            lab.state = "running"
            lab.state_error = None
        elif running_count == 0 and (stopped_count > 0 or undeployed_count > 0):
            lab.state = "stopped"
            lab.state_error = None
        elif running_count > 0:
            # Mixed state - some running, some stopped/undeployed
            # This is a valid partial deployment
            lab.state = "running"
            lab.state_error = None

        lab.state_updated_at = datetime.now(timezone.utc)

        if lab.state != old_lab_state:
            logger.info(f"Reconciled lab {lab_id} state: {old_lab_state} -> {lab.state}")

        session.commit()

    except Exception as e:
        logger.error(f"Failed to reconcile lab {lab_id}: {e}")
        # Don't update state on error - leave it for next cycle


async def state_reconciliation_monitor():
    """Background task to periodically reconcile state.

    Runs every reconciliation_interval seconds and queries agents
    for actual container status, updating the database to match reality.
    """
    logger.info(
        f"State reconciliation monitor started "
        f"(interval: {settings.reconciliation_interval}s)"
    )

    while True:
        try:
            await asyncio.sleep(settings.reconciliation_interval)
            await reconcile_lab_states()
        except asyncio.CancelledError:
            logger.info("State reconciliation monitor stopped")
            break
        except Exception as e:
            logger.error(f"Error in state reconciliation monitor: {e}")
            # Continue running - don't let one error stop the monitor
