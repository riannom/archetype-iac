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
6. Link state initialization - Ensure link states exist for deployed labs
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import redis

from app import agent_client, models
from app.config import settings
from app.db import SessionLocal, get_redis, get_session
from app.services.broadcaster import broadcast_node_state_change, broadcast_link_state_change
from app.services.topology import TopologyService
from app.utils.job import is_job_within_timeout
from app.utils.link import generate_link_name

logger = logging.getLogger(__name__)


def _set_agent_error(agent: models.Host, error_message: str) -> None:
    """Set or update an agent's error state.

    If this is a new error (agent.last_error was None), sets error_since
    to the current time. Always updates last_error to the new message.

    Args:
        agent: Host model instance
        error_message: Error message to persist
    """
    if agent.last_error is None:
        agent.error_since = datetime.now(timezone.utc)
    agent.last_error = error_message
    logger.warning(f"Agent {agent.name} error: {error_message}")


def _clear_agent_error(agent: models.Host) -> None:
    """Clear an agent's error state.

    Clears both last_error and error_since when the agent successfully
    responds to queries.

    Args:
        agent: Host model instance
    """
    if agent.last_error is not None:
        logger.info(f"Agent {agent.name} error cleared (was: {agent.last_error})")
        agent.last_error = None
        agent.error_since = None


@contextmanager
def reconciliation_lock(lab_id: str, timeout: int = 60):
    """Acquire a distributed lock before reconciling a lab.

    This prevents multiple reconciliation tasks from running concurrently
    for the same lab, and prevents reconciliation from interfering with
    active jobs.

    Args:
        lab_id: Lab identifier to lock
        timeout: Lock TTL in seconds (auto-releases if holder crashes)

    Yields:
        True if lock was acquired, False if another process holds it.
    """
    lock_key = f"reconcile_lock:{lab_id}"
    r = get_redis()

    try:
        # Try to acquire lock with NX (only if not exists) and TTL
        lock_acquired = r.set(lock_key, "1", nx=True, ex=timeout)
        if not lock_acquired:
            logger.debug(f"Could not acquire reconciliation lock for lab {lab_id}")
            yield False
            return
        yield True
    except redis.RedisError as e:
        logger.warning(f"Redis error acquiring lock for lab {lab_id}: {e}")
        # On Redis error, proceed without lock (better than blocking reconciliation)
        yield True
    finally:
        try:
            r.delete(lock_key)
        except redis.RedisError:
            pass  # Lock will auto-expire via TTL




def _ensure_link_states_for_lab(session, lab_id: str) -> int:
    """Ensure LinkState records exist for all links in a lab's topology.

    This is called during reconciliation to create missing link state records
    for labs that may have been deployed before link state tracking was added.

    Uses database as source of truth.

    Returns the number of link states created.
    """
    service = TopologyService(session)
    db_links = service.get_links(lab_id)

    if not db_links:
        return 0

    # Get existing link states
    existing = (
        session.query(models.LinkState)
        .filter(models.LinkState.lab_id == lab_id)
        .all()
    )
    existing_names = {ls.link_name for ls in existing}

    created_count = 0
    for link in db_links:
        if link.link_name not in existing_names:
            # Get node container names for the link state record
            source_node = session.get(models.Node, link.source_node_id)
            target_node = session.get(models.Node, link.target_node_id)
            if not source_node or not target_node:
                continue

            new_state = models.LinkState(
                lab_id=lab_id,
                link_name=link.link_name,
                link_definition_id=link.id,
                source_node=source_node.container_name,
                source_interface=link.source_interface,
                target_node=target_node.container_name,
                target_interface=link.target_interface,
                desired_state="up",
                actual_state="unknown",
            )
            session.add(new_state)
            existing_names.add(link.link_name)
            created_count += 1

    return created_count


def _backfill_placement_node_ids(session, lab_id: str) -> int:
    """Backfill node_definition_id for placements missing it.

    This handles existing placements that were created before the FK was added.
    Called during reconciliation to gradually migrate old data.

    Returns:
        Number of placements updated
    """
    count = 0
    placements = session.query(models.NodePlacement).filter(
        models.NodePlacement.lab_id == lab_id,
        models.NodePlacement.node_definition_id.is_(None),
    ).all()

    for p in placements:
        node = session.query(models.Node).filter(
            models.Node.lab_id == p.lab_id,
            models.Node.container_name == p.node_name,
        ).first()
        if node:
            p.node_definition_id = node.id
            count += 1

    return count


async def refresh_states_from_agents():
    """Query agents and refresh lab/node states with actual container status.

    This function refreshes the database state to match reality by:
    1. Finding labs in transitional states (starting, stopping)
    2. Finding nodes in "pending" state with no active job
    3. Querying agents for actual container status
    4. Updating NodeState.actual_state to match reality
    5. Updating Lab.state based on aggregated node states

    Note: This does NOT take corrective action - it only updates the database
    to reflect the actual state. For enforcement of desired state, see
    state_enforcement.py.
    """
    with get_session() as session:
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

            # Find running nodes that haven't completed boot readiness check
            unready_running_nodes = (
                session.query(models.NodeState)
                .filter(
                    models.NodeState.actual_state == "running",
                    models.NodeState.is_ready == False,
                )
                .all()
            )

            # Find nodes in error state - they may have recovered
            error_nodes = (
                session.query(models.NodeState)
                .filter(models.NodeState.actual_state == "error")
                .all()
            )

            # Find nodes where desired=running but actual=stopped/undeployed
            # These may have been started by state enforcement and need reconciliation
            stale_stopped_nodes = (
                session.query(models.NodeState)
                .filter(
                    models.NodeState.desired_state == "running",
                    models.NodeState.actual_state.in_(["stopped", "undeployed", "exited"]),
                )
                .all()
            )

            # Find running nodes that are missing NodePlacement records
            # This handles cases where deploy jobs failed after containers were created
            from sqlalchemy import and_, exists
            from sqlalchemy.sql import select

            placement_exists_subquery = (
                select(models.NodePlacement.id)
                .where(
                    models.NodePlacement.lab_id == models.NodeState.lab_id,
                    models.NodePlacement.node_name == models.NodeState.node_name,
                )
                .exists()
            )

            running_nodes_without_placement = (
                session.query(models.NodeState)
                .filter(
                    models.NodeState.actual_state == "running",
                    ~placement_exists_subquery,
                )
                .all()
            )

            # Collect unique lab IDs that need reconciliation
            labs_to_reconcile = set()
            for lab in transitional_labs:
                labs_to_reconcile.add(lab.id)
            for node in stale_pending_nodes:
                labs_to_reconcile.add(node.lab_id)
            for node in unready_running_nodes:
                labs_to_reconcile.add(node.lab_id)
            for node in error_nodes:
                labs_to_reconcile.add(node.lab_id)
            for node in running_nodes_without_placement:
                labs_to_reconcile.add(node.lab_id)
            for node in stale_stopped_nodes:
                labs_to_reconcile.add(node.lab_id)

            # FIRST: Always check readiness for running nodes (this doesn't interfere with jobs)
            # This is separate because readiness checks should happen even when jobs are running
            if unready_running_nodes:
                await _check_readiness_for_nodes(session, unready_running_nodes)

            if not labs_to_reconcile:
                return  # Nothing to reconcile

            logger.info(f"Reconciling state for {len(labs_to_reconcile)} lab(s)")

            for lab_id in labs_to_reconcile:
                await _reconcile_single_lab(session, lab_id)

        except Exception as e:
            logger.error(f"Error in state reconciliation: {e}")


async def _check_readiness_for_nodes(session, nodes: list):
    """Check boot readiness for running nodes.

    This is separate from full state reconciliation because readiness checks
    are non-destructive and should happen even when jobs are running.
    """
    from app.utils.lab import get_lab_provider

    # Group nodes by lab_id for efficient agent lookup
    nodes_by_lab: dict[str, list] = {}
    for node in nodes:
        if node.lab_id not in nodes_by_lab:
            nodes_by_lab[node.lab_id] = []
        nodes_by_lab[node.lab_id].append(node)

    for lab_id, lab_nodes in nodes_by_lab.items():
        lab = session.get(models.Lab, lab_id)
        if not lab:
            continue

        try:
            lab_provider = get_lab_provider(lab)
            agent = await agent_client.get_agent_for_lab(
                session, lab, required_provider=lab_provider
            )
            if not agent:
                logger.debug(f"No agent for lab {lab_id}, skipping readiness check")
                continue

            for ns in lab_nodes:
                # Set boot_started_at if not already set
                if not ns.boot_started_at:
                    ns.boot_started_at = datetime.now(timezone.utc)

                try:
                    readiness = await agent_client.check_node_readiness(
                        agent, lab_id, ns.node_name
                    )
                    if readiness.get("is_ready", False):
                        ns.is_ready = True
                        logger.info(f"Node {ns.node_name} in lab {lab_id} is now ready")
                except Exception as e:
                    logger.debug(f"Readiness check failed for {ns.node_name}: {e}")

            session.commit()

        except Exception as e:
            logger.error(f"Error checking readiness for lab {lab_id}: {e}")


async def _reconcile_single_lab(session, lab_id: str):
    """Reconcile a single lab's state with actual container status."""
    from app.utils.lab import get_lab_provider

    lab = session.get(models.Lab, lab_id)
    if not lab:
        return

    # Acquire distributed lock to prevent concurrent reconciliation
    with reconciliation_lock(lab_id) as lock_acquired:
        if not lock_acquired:
            logger.debug(f"Lab {lab_id} reconciliation skipped - another process holds lock")
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

        # Call the actual reconciliation logic (extracted to allow locking)
        await _do_reconcile_lab(session, lab, lab_id)


async def _do_reconcile_lab(session, lab, lab_id: str):
    """Perform the actual reconciliation logic for a lab.

    This is called by _reconcile_single_lab after acquiring the lock.
    """
    from app.utils.lab import get_lab_provider

    # Ensure link states exist for this lab using database (source of truth)
    try:
        links_created = _ensure_link_states_for_lab(session, lab_id)
        if links_created > 0:
            logger.info(f"Created {links_created} link state(s) for lab {lab_id}")
    except Exception as e:
        logger.debug(f"Failed to ensure link states for lab {lab_id}: {e}")

    # Backfill node_definition_id for placements (gradual migration)
    try:
        backfilled = _backfill_placement_node_ids(session, lab_id)
        if backfilled > 0:
            logger.info(f"Backfilled node_definition_id for {backfilled} placement(s) in lab {lab_id}")
            session.commit()
    except Exception as e:
        logger.debug(f"Failed to backfill placement node IDs for lab {lab_id}: {e}")

    # Get ALL agents that have nodes for this lab (multi-host support)
    try:
        lab_provider = get_lab_provider(lab)

        # Find unique agents from NodePlacement records
        placements = (
            session.query(models.NodePlacement)
            .filter(models.NodePlacement.lab_id == lab_id)
            .all()
        )
        agent_ids = {p.host_id for p in placements}
        # Map node names to their expected agent for safer undeployed detection
        node_expected_agent: dict[str, str] = {p.node_name: p.host_id for p in placements}

        # Also include the lab's default agent if set
        if lab.agent_id:
            agent_ids.add(lab.agent_id)

        # If no placements and no default, find any healthy agent
        if not agent_ids:
            fallback_agent = await agent_client.get_agent_for_lab(
                session, lab, required_provider=lab_provider
            )
            if fallback_agent:
                agent_ids.add(fallback_agent.id)

        if not agent_ids:
            logger.warning(f"No agent available to reconcile lab {lab_id}")
            return

        # Query actual container status from ALL agents
        # Track both status and which agent has each container
        container_status_map: dict[str, str] = {}
        container_agent_map: dict[str, str] = {}  # node_name -> agent_id
        agents_successfully_queried: set[str] = set()  # Track which agents responded
        host_to_agent: dict[str, models.Host] = {}  # For live link creation
        for agent_id in agent_ids:
            agent = session.get(models.Host, agent_id)
            if not agent or not agent_client.is_agent_online(agent):
                logger.debug(f"Agent {agent_id} is offline, skipping in reconciliation")
                continue
            host_to_agent[agent_id] = agent

            try:
                result = await agent_client.get_lab_status_from_agent(agent, lab_id)
                nodes = result.get("nodes", [])
                agent_error = result.get("error")

                # Only count as successfully queried if no error in response
                # An error (e.g., Docker state corruption) means we can't trust the results
                if not agent_error:
                    agents_successfully_queried.add(agent_id)
                    # Clear any previous error state - agent responded successfully
                    _clear_agent_error(agent)
                else:
                    logger.warning(
                        f"Agent {agent.name} returned error for lab {lab_id}: {agent_error}"
                    )
                    # Persist the error for visibility in UI
                    _set_agent_error(agent, agent_error)

                # Still merge any nodes that were returned (partial success)
                for n in nodes:
                    node_name = n.get("name", "")
                    if node_name:
                        container_status_map[node_name] = n.get("status", "unknown")
                        container_agent_map[node_name] = agent_id
            except Exception as e:
                logger.warning(f"Failed to query agent {agent.name} for lab {lab_id}: {e}")
                # Persist the error for visibility in UI
                _set_agent_error(agent, f"Query failed: {e}")

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

        # Check for active jobs that might be handling "stopping" nodes
        active_stop_job = (
            session.query(models.Job)
            .filter(
                models.Job.lab_id == lab_id,
                models.Job.status.in_(["queued", "running"]),
            )
            .first()
        )

        for ns in node_states:
            # Skip "stopping" nodes - they're being handled by an active job
            # or need timeout recovery (handled below)
            if ns.actual_state == "stopping":
                if active_stop_job:
                    # Active job is handling this - don't interfere
                    stopped_count += 1  # Count as stopped for lab state calculation
                    continue
                # Check timeout: if stopping > 6 min with no job, recover from container status
                if ns.stopping_started_at:
                    stopping_duration = datetime.now(timezone.utc) - ns.stopping_started_at
                    if stopping_duration.total_seconds() < 360:  # 6 minutes
                        # Still within timeout window, don't interfere
                        stopped_count += 1
                        continue
                    # Timeout exceeded - fall through to normal reconciliation
                    logger.warning(
                        f"Node {ns.node_name} in lab {lab_id} stuck in 'stopping' for "
                        f"{stopping_duration.total_seconds():.0f}s, recovering via reconciliation"
                    )
                else:
                    # No timestamp but no job - something is wrong, recover
                    logger.warning(
                        f"Node {ns.node_name} in lab {lab_id} in 'stopping' state without "
                        f"timestamp or active job, recovering via reconciliation"
                    )

            # Skip "starting" nodes - they're being handled by an active job
            # or need timeout recovery (handled by job_health monitor)
            if ns.actual_state == "starting":
                if active_stop_job:
                    # Active job is handling this - don't interfere
                    running_count += 1  # Count as running for lab state calculation
                    continue
                # Check timeout: if starting > 6 min with no job, recover from container status
                if ns.starting_started_at:
                    starting_duration = datetime.now(timezone.utc) - ns.starting_started_at
                    if starting_duration.total_seconds() < 360:  # 6 minutes
                        # Still within timeout window, don't interfere
                        running_count += 1
                        continue
                    # Timeout exceeded - fall through to normal reconciliation
                    logger.warning(
                        f"Node {ns.node_name} in lab {lab_id} stuck in 'starting' for "
                        f"{starting_duration.total_seconds():.0f}s, recovering via reconciliation"
                    )
                else:
                    # No timestamp but no job - something is wrong, recover
                    logger.warning(
                        f"Node {ns.node_name} in lab {lab_id} in 'starting' state without "
                        f"timestamp or active job, recovering via reconciliation"
                    )

            container_status = container_status_map.get(ns.node_name)
            old_state = ns.actual_state
            old_is_ready = ns.is_ready

            if container_status:
                if container_status == "running":
                    ns.actual_state = "running"
                    ns.stopping_started_at = None  # Clear if recovering from stuck stopping
                    ns.starting_started_at = None  # Clear if recovering from stuck starting
                    ns.error_message = None
                    running_count += 1

                    # Set boot_started_at if not already set (backfill for existing nodes)
                    if not ns.boot_started_at:
                        ns.boot_started_at = datetime.now(timezone.utc)

                    # Check boot readiness for nodes that are running but not yet ready
                    if not ns.is_ready:
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
                    ns.stopping_started_at = None  # Clear if recovering from stuck stopping
                    ns.starting_started_at = None  # Clear if recovering from stuck starting
                    ns.error_message = None
                    ns.is_ready = False
                    ns.boot_started_at = None
                    stopped_count += 1
                elif container_status in ("error", "dead"):
                    ns.actual_state = "error"
                    ns.stopping_started_at = None  # Clear if recovering from stuck stopping
                    ns.starting_started_at = None  # Clear if recovering from stuck starting
                    ns.error_message = f"Container status: {container_status}"
                    ns.is_ready = False
                    ns.boot_started_at = None
                    error_count += 1
                else:
                    # Unknown container status
                    stopped_count += 1
            else:
                # Container not found in status response
                # Only mark as undeployed if we successfully queried the agent that should have it
                # This prevents falsely marking nodes as undeployed when agent is temporarily unreachable
                expected_agent = node_expected_agent.get(ns.node_name)
                agent_was_queried = (
                    expected_agent in agents_successfully_queried
                    if expected_agent
                    else len(agents_successfully_queried) > 0
                )

                if agent_was_queried:
                    # Agent responded but container not found - safe to mark undeployed
                    if ns.actual_state not in ("undeployed", "stopped"):
                        ns.actual_state = "undeployed"
                        ns.error_message = None
                    ns.is_ready = False
                    ns.boot_started_at = None
                    undeployed_count += 1
                else:
                    # Agent didn't respond - preserve existing state to avoid false negatives
                    logger.debug(
                        f"Preserving state for {ns.node_name} - expected agent "
                        f"{expected_agent or 'unknown'} was not successfully queried"
                    )

            if ns.actual_state != old_state or (ns.is_ready != old_is_ready and ns.is_ready):
                logger.info(
                    f"Reconciled node {ns.node_name} in lab {lab_id}: "
                    f"{old_state} -> {ns.actual_state}"
                    + (f" (ready)" if ns.is_ready and not old_is_ready else "")
                )
                # Broadcast state change to WebSocket clients
                # Look up host info for this node
                node_host_id = container_agent_map.get(ns.node_name)
                node_host_name = host_to_agent.get(node_host_id).name if node_host_id and node_host_id in host_to_agent else None
                asyncio.create_task(
                    broadcast_node_state_change(
                        lab_id=lab_id,
                        node_id=ns.node_id,
                        node_name=ns.node_name,
                        desired_state=ns.desired_state,
                        actual_state=ns.actual_state,
                        is_ready=ns.is_ready,
                        error_message=ns.error_message,
                        host_id=node_host_id,
                        host_name=node_host_name,
                    )
                )

        # Ensure NodePlacement records exist for containers found on agents
        # This handles cases where deploy jobs failed after containers were created
        # IMPORTANT: Don't blindly trust where containers are found - check node_def.host_id
        for node_name, agent_id in container_agent_map.items():
            # Look up node definition for FK and host assignment
            node_def = session.query(models.Node).filter(
                models.Node.lab_id == lab_id,
                models.Node.container_name == node_name,
            ).first()

            # Check if container is on the WRONG agent according to node definition
            if node_def and node_def.host_id and node_def.host_id != agent_id:
                logger.warning(
                    f"MISPLACED CONTAINER: {node_name} in lab {lab_id} found on agent {agent_id} "
                    f"but should be on {node_def.host_id}. Skipping placement update. "
                    f"Container may need cleanup."
                )
                # Don't update placement for misplaced containers - this would perpetuate the bug
                continue

            existing_placement = (
                session.query(models.NodePlacement)
                .filter(
                    models.NodePlacement.lab_id == lab_id,
                    models.NodePlacement.node_name == node_name,
                )
                .first()
            )
            if existing_placement:
                # Update if container moved to a different agent (and move is valid per node_def)
                if existing_placement.host_id != agent_id:
                    logger.info(
                        f"Updating placement for {node_name} in lab {lab_id}: "
                        f"{existing_placement.host_id} -> {agent_id}"
                    )
                    existing_placement.host_id = agent_id
                    existing_placement.status = "deployed"
                # Backfill node_definition_id if missing
                if node_def and not existing_placement.node_definition_id:
                    existing_placement.node_definition_id = node_def.id
            else:
                # Create new placement record
                logger.info(
                    f"Creating placement for {node_name} in lab {lab_id} on agent {agent_id}"
                )
                new_placement = models.NodePlacement(
                    lab_id=lab_id,
                    node_name=node_name,
                    node_definition_id=node_def.id if node_def else None,
                    host_id=agent_id,
                    status="deployed",
                )
                session.add(new_placement)

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

        # Reconcile link states based on node states and L2 connectivity
        # Build a map of node name -> actual state for quick lookup
        node_actual_states: dict[str, str] = {}
        for ns in node_states:
            node_actual_states[ns.node_name] = ns.actual_state

        # Update link states
        link_states = (
            session.query(models.LinkState)
            .filter(models.LinkState.lab_id == lab_id)
            .all()
        )

        for ls in link_states:
            old_actual = ls.actual_state
            source_state = node_actual_states.get(ls.source_node, "unknown")
            target_state = node_actual_states.get(ls.target_node, "unknown")

            # Determine link actual state based on endpoint node states
            if source_state == "running" and target_state == "running":
                # Both nodes running - check L2 connectivity
                # If carrier states are off, link is administratively down
                if ls.source_carrier_state == "off" or ls.target_carrier_state == "off":
                    ls.actual_state = "down"
                    ls.error_message = "Carrier disabled on one or more endpoints"
                elif ls.is_cross_host:
                    # For cross-host links, verify VXLAN tunnel exists
                    tunnel = (
                        session.query(models.VxlanTunnel)
                        .filter(
                            models.VxlanTunnel.link_state_id == ls.id,
                            models.VxlanTunnel.status == "active",
                        )
                        .first()
                    )
                    if tunnel:
                        ls.actual_state = "up"
                        ls.error_message = None
                    else:
                        # No active tunnel - link is broken
                        ls.actual_state = "error"
                        ls.error_message = "VXLAN tunnel not active"
                else:
                    # Same-host link - assume up if both nodes are running and carrier on
                    # Full L2 verification would require querying OVS VLAN tags from agent
                    # which adds latency. For now, trust that hot_connect worked.
                    ls.actual_state = "up"
                    ls.error_message = None
            elif source_state in ("error",) or target_state in ("error",):
                # At least one node is in error state
                ls.actual_state = "error"
                ls.error_message = "One or more endpoint nodes in error state"
            elif source_state in ("stopped", "undeployed") or target_state in ("stopped", "undeployed"):
                # At least one node is stopped/undeployed
                ls.actual_state = "down"
                ls.error_message = None
            else:
                # Unknown or transitional states
                ls.actual_state = "unknown"
                ls.error_message = None

            if ls.actual_state != old_actual:
                logger.debug(
                    f"Reconciled link {ls.link_name} in lab {lab_id}: "
                    f"{old_actual} -> {ls.actual_state}"
                )
                # Broadcast link state change to WebSocket clients
                asyncio.create_task(
                    broadcast_link_state_change(
                        lab_id=lab_id,
                        link_name=ls.link_name,
                        desired_state=ls.desired_state,
                        actual_state=ls.actual_state,
                        source_node=ls.source_node,
                        target_node=ls.target_node,
                        error_message=ls.error_message,
                    )
                )

        # Auto-connect pending links when both nodes become running
        # This handles links that were added while nodes were not yet deployed
        # Also handles cross-host links where VXLAN tunnel is missing
        from app.tasks.live_links import create_link_if_ready

        for ls in link_states:
            # Check if link should be connected but isn't
            # Include "error" state for cross-host links with missing tunnels
            should_auto_connect = (
                ls.desired_state == "up"
                and node_actual_states.get(ls.source_node) == "running"
                and node_actual_states.get(ls.target_node) == "running"
                and (
                    ls.actual_state in ("unknown", "pending", "down")
                    or (ls.actual_state == "error" and ls.is_cross_host
                        and ls.error_message == "VXLAN tunnel not active")
                )
            )
            if should_auto_connect:
                # Both nodes running, link should be up but isn't - create it
                logger.info(f"Auto-connecting pending link {ls.link_name}")
                try:
                    await create_link_if_ready(session, lab_id, ls, host_to_agent)
                except Exception as e:
                    logger.error(f"Failed to auto-connect link {ls.link_name}: {e}")
                    ls.actual_state = "error"
                    ls.error_message = str(e)

        # Clean up links marked for deletion (desired_state="deleted")
        for ls in link_states:
            if ls.desired_state == "deleted":
                session.delete(ls)

        session.commit()

    except Exception as e:
        logger.error(f"Failed to reconcile lab {lab_id}: {e}")
        # Don't update state on error - leave it for next cycle
        # Note: session cleanup handled by get_session() context manager


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
            await refresh_states_from_agents()
        except asyncio.CancelledError:
            logger.info("State reconciliation monitor stopped")
            break
        except Exception as e:
            logger.error(f"Error in state reconciliation monitor: {e}")
            # Continue running - don't let one error stop the monitor
