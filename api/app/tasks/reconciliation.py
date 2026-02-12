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

import redis

from app import agent_client, db, models
from app.config import settings
from app.metrics import nlm_phase_duration
from app.db import get_redis, get_session
from app.services.broadcaster import broadcast_node_state_change, broadcast_link_state_change
from app.services.topology import TopologyService
from app.utils.job import is_job_within_timeout
from app.utils.locks import acquire_link_ops_lock, release_link_ops_lock
from app.state import (
    JobStatus,
    LabState,
    LinkActualState,
    LinkDesiredState,
    NodeActualState,
    NodeDesiredState,
)
from app.services.state_machine import LabStateMachine

logger = logging.getLogger(__name__)

# Rate-limit endpoint repairs: lab_id -> last repair attempt time
_last_endpoint_repair: dict[str, datetime] = {}
ENDPOINT_REPAIR_COOLDOWN = timedelta(minutes=2)


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

            # Determine host placement from nodes or node_placements
            source_host_id = source_node.host_id
            target_host_id = target_node.host_id

            # Fall back to NodePlacement if node.host_id not set
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

            # Determine if cross-host
            is_cross_host = (
                source_host_id is not None
                and target_host_id is not None
                and source_host_id != target_host_id
            )

            new_state = models.LinkState(
                lab_id=lab_id,
                link_name=link.link_name,
                link_definition_id=link.id,
                source_node=source_node.container_name,
                source_interface=link.source_interface,
                target_node=target_node.container_name,
                target_interface=link.target_interface,
                source_host_id=source_host_id,
                target_host_id=target_host_id,
                is_cross_host=is_cross_host,
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


_lab_orphan_check_counter = 0
_LAB_ORPHAN_CHECK_INTERVAL = settings.lab_orphan_check_multiplier


async def _maybe_cleanup_labless_containers(session):
    """Periodically remove containers belonging to deleted labs.

    When a lab is deleted from the database, its containers on agents become
    invisible to per-lab reconciliation (which iterates DB labs). This function
    tells each agent the full list of valid lab IDs so it can remove any
    containers belonging to labs no longer in the database.

    Runs every _LAB_ORPHAN_CHECK_INTERVAL cycles to avoid excessive overhead.
    """
    global _lab_orphan_check_counter
    _lab_orphan_check_counter += 1
    if _lab_orphan_check_counter < _LAB_ORPHAN_CHECK_INTERVAL:
        return
    _lab_orphan_check_counter = 0

    try:
        from app.tasks.cleanup_base import get_valid_lab_ids
        valid_lab_ids = list(get_valid_lab_ids(session))
        all_agents = session.query(models.Host).all()
        for agent in all_agents:
            if not agent_client.is_agent_online(agent):
                continue
            try:
                result = await agent_client.cleanup_orphans_on_agent(agent, valid_lab_ids)
                removed = result.get("removed_containers", [])
                if removed:
                    logger.info(
                        f"Removed {len(removed)} lab-less container(s) on {agent.name}: {removed}"
                    )
            except Exception as e:
                logger.warning(f"Failed global orphan check on {agent.name}: {e}")
    except Exception as e:
        logger.warning(f"Failed global lab-less container cleanup: {e}")

    # Reconcile VXLAN ports: remove stale ports that no longer match active tunnels
    try:
        active_tunnels = (
            session.query(models.VxlanTunnel)
            .filter(models.VxlanTunnel.status == "active")
            .all()
        )

        # Build expected port names per agent
        agent_valid_ports: dict[str, set[str]] = {}
        for tunnel in active_tunnels:
            link_state = session.get(models.LinkState, tunnel.link_state_id)
            if not link_state:
                continue
            port_name = agent_client.compute_vxlan_port_name(
                str(tunnel.lab_id), link_state.link_name
            )
            for aid in [tunnel.agent_a_id, tunnel.agent_b_id]:
                if aid:
                    agent_valid_ports.setdefault(aid, set()).add(port_name)

        all_agents = session.query(models.Host).all()
        for agent in all_agents:
            if not agent_client.is_agent_online(agent):
                continue
            valid = list(agent_valid_ports.get(str(agent.id), set()))
            try:
                result = await agent_client.reconcile_vxlan_ports_on_agent(agent, valid)
                removed = result.get("removed_ports", [])
                if removed:
                    logger.info(
                        f"Removed {len(removed)} stale VXLAN port(s) on {agent.name}: {removed}"
                    )
            except Exception as e:
                logger.warning(f"Failed VXLAN port reconciliation on {agent.name}: {e}")
    except Exception as e:
        logger.warning(f"Failed VXLAN port reconciliation: {e}")


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
            now - timedelta(seconds=settings.stale_starting_threshold)

            transitional_labs = (
                session.query(models.Lab)
                .filter(
                    models.Lab.state.in_([LabState.STARTING.value, LabState.STOPPING.value, LabState.UNKNOWN.value]),
                )
                .all()
            )

            # Also find labs with nodes in "pending" state for too long
            pending_threshold = now - timedelta(seconds=settings.stale_pending_threshold)
            stale_pending_nodes = (
                session.query(models.NodeState)
                .filter(
                    models.NodeState.actual_state == NodeActualState.PENDING.value,
                    models.NodeState.updated_at < pending_threshold,
                )
                .all()
            )

            # Find running nodes that haven't completed boot readiness check
            unready_running_nodes = (
                session.query(models.NodeState)
                .filter(
                    models.NodeState.actual_state == NodeActualState.RUNNING.value,
                    not models.NodeState.is_ready,
                )
                .all()
            )

            # Find nodes in error state - they may have recovered
            error_nodes = (
                session.query(models.NodeState)
                .filter(models.NodeState.actual_state == NodeActualState.ERROR.value)
                .all()
            )

            # Find nodes where desired=running but actual=stopped/undeployed
            # These may have been started by state enforcement and need reconciliation
            stale_stopped_nodes = (
                session.query(models.NodeState)
                .filter(
                    models.NodeState.desired_state == NodeDesiredState.RUNNING.value,
                    models.NodeState.actual_state.in_([NodeActualState.STOPPED.value, NodeActualState.UNDEPLOYED.value, NodeActualState.EXITED.value]),
                )
                .all()
            )

            # Find running nodes that are missing NodePlacement records
            # This handles cases where deploy jobs failed after containers were created
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
                    models.NodeState.actual_state == NodeActualState.RUNNING.value,
                    ~placement_exists_subquery,
                )
                .all()
            )

            # Find labs with orphan placements (placement exists but node was deleted from topology)
            # NodePlacement.node_definition_id has ondelete=SET NULL, so it becomes NULL when the
            # Node is deleted — we must check by name against the nodes table instead of using the FK.
            node_exists_by_name = (
                select(models.Node.id)
                .where(
                    models.Node.lab_id == models.NodePlacement.lab_id,
                    models.Node.container_name == models.NodePlacement.node_name,
                )
                .exists()
            )
            orphan_placements = (
                session.query(models.NodePlacement)
                .filter(~node_exists_by_name)
                .all()
            )

            # Find labs with inconsistent state (lab.state doesn't match computed state from nodes)
            # This catches cases like lab="running" but all nodes are "stopped"
            from sqlalchemy import func
            inconsistent_labs = []
            stable_labs = (
                session.query(models.Lab)
                .filter(
                    models.Lab.state.in_([LabState.RUNNING.value, LabState.STOPPED.value, LabState.ERROR.value]),
                )
                .all()
            )
            for lab in stable_labs:
                # Aggregate node states for this lab
                state_counts = (
                    session.query(
                        models.NodeState.actual_state,
                        func.count(models.NodeState.id).label('count')
                    )
                    .filter(models.NodeState.lab_id == lab.id)
                    .group_by(models.NodeState.actual_state)
                    .all()
                )
                counts = {state: count for state, count in state_counts}
                running = counts.get(NodeActualState.RUNNING.value, 0)
                stopped = counts.get(NodeActualState.STOPPED.value, 0)
                undeployed = counts.get(NodeActualState.UNDEPLOYED.value, 0)
                error = counts.get(NodeActualState.ERROR.value, 0)
                pending = counts.get(NodeActualState.PENDING.value, 0)
                starting = counts.get(NodeActualState.STARTING.value, 0)
                stopping = counts.get(NodeActualState.STOPPING.value, 0)
                exited = counts.get(NodeActualState.EXITED.value, 0)

                # Compute expected state
                expected_state = LabStateMachine.compute_lab_state(
                    running_count=running,
                    stopped_count=stopped + exited,
                    undeployed_count=undeployed,
                    error_count=error,
                    pending_count=pending,
                    starting_count=starting,
                    stopping_count=stopping,
                )

                if lab.state != expected_state.value:
                    inconsistent_labs.append(lab)
                    logger.info(
                        f"Lab {lab.id} has inconsistent state: current={lab.state}, "
                        f"expected={expected_state.value} (running={running}, stopped={stopped}, "
                        f"undeployed={undeployed}, error={error})"
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
            for placement in orphan_placements:
                labs_to_reconcile.add(placement.lab_id)
                logger.info(f"Lab {placement.lab_id} has orphan placement for deleted node: {placement.node_name}")
            for lab in inconsistent_labs:
                labs_to_reconcile.add(lab.id)

            # Periodic full sweep: verify all deployed labs match agent reality.
            # Catches state drift where DB says "running" but container is stopped.
            _sweep_counter = getattr(refresh_states_from_agents, '_sweep_counter', 0) + 1
            refresh_states_from_agents._sweep_counter = _sweep_counter

            if _sweep_counter % 10 == 0:
                deployed_labs = (
                    session.query(models.Lab)
                    .filter(models.Lab.state.in_([
                        LabState.RUNNING.value, LabState.ERROR.value,
                    ]))
                    .all()
                )
                sweep_count = 0
                for lab in deployed_labs:
                    if lab.id not in labs_to_reconcile:
                        labs_to_reconcile.add(lab.id)
                        sweep_count += 1
                if sweep_count:
                    logger.info(f"Full sweep: adding {sweep_count} deployed lab(s) to reconciliation")

            # FIRST: Always check readiness for running nodes (this doesn't interfere with jobs)
            # This is separate because readiness checks should happen even when jobs are running
            if unready_running_nodes:
                await _check_readiness_for_nodes(session, unready_running_nodes)

            if labs_to_reconcile:
                logger.info(f"Reconciling state for {len(labs_to_reconcile)} lab(s)")

                for lab_id in labs_to_reconcile:
                    await _reconcile_single_lab(session, lab_id)

            # Periodic global orphan cleanup: remove containers from deleted labs
            # Runs less frequently than per-lab reconciliation since it scans ALL
            # containers on each agent. Every 10th cycle ≈ every 5 minutes at 30s interval.
            await _maybe_cleanup_labless_containers(session)

        except Exception as e:
            logger.error(f"Error in state reconciliation: {e}")


async def _check_readiness_for_nodes(session, nodes: list):
    """Check boot readiness for running nodes.

    This is separate from full state reconciliation because readiness checks
    are non-destructive and should happen even when jobs are running.
    """
    from app.utils.lab import get_lab_provider, get_node_provider

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

            # Look up device kinds for all nodes in this lab
            node_devices = {}
            db_nodes = (
                session.query(models.Node)
                .filter(
                    models.Node.lab_id == lab_id,
                    models.Node.container_name.in_([ns.node_name for ns in lab_nodes]),
                )
                .all()
            )
            for db_node in db_nodes:
                node_devices[db_node.container_name] = db_node.device

            for ns in lab_nodes:
                # Set boot_started_at if not already set
                if not ns.boot_started_at:
                    ns.boot_started_at = datetime.now(timezone.utc)

                try:
                    # Get the device kind and determine provider type for this node
                    device_kind = node_devices.get(ns.node_name)
                    provider_type = None
                    if device_kind:
                        # Look up the Node to determine provider from image
                        db_node = next(
                            (n for n in db_nodes if n.container_name == ns.node_name),
                            None,
                        )
                        if db_node and db_node.image:
                            provider_type = get_node_provider(db_node)

                    readiness = await agent_client.check_node_readiness(
                        agent, lab_id, ns.node_name,
                        kind=device_kind,
                        provider_type=provider_type,
                    )
                    if readiness.get("is_ready", False):
                        ns.is_ready = True
                        # Record boot-wait duration metric
                        if ns.boot_started_at:
                            boot_secs = (datetime.now(timezone.utc) - ns.boot_started_at).total_seconds()
                            _boot_device = (node_devices.get(ns.node_name) or "linux").lower()
                            nlm_phase_duration.labels(
                                phase="boot_wait", device_type=_boot_device, status="success",
                            ).observe(boot_secs)
                        logger.info(f"Node {ns.node_name} in lab {lab_id} is now ready")
                except Exception as e:
                    logger.debug(f"Readiness check failed for {ns.node_name}: {e}")

            session.commit()

        except Exception as e:
            logger.error(f"Error checking readiness for lab {lab_id}: {e}")
            try:
                session.rollback()
            except Exception:
                pass


async def _reconcile_single_lab(session, lab_id: str):
    """Reconcile a single lab's state with actual container status."""

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
                models.Job.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value]),
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

    # Normalize link interface names for existing labs
    try:
        topo_service = TopologyService(session)
        normalized = topo_service.normalize_links_for_lab(lab_id)
        if normalized > 0:
            logger.info(f"Normalized {normalized} link record(s) for lab {lab_id}")
    except Exception as e:
        logger.debug(f"Failed to normalize link interfaces for lab {lab_id}: {e}")

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

        # Look up device kinds for all nodes (needed for VM readiness checks)
        db_nodes = (
            session.query(models.Node)
            .filter(models.Node.lab_id == lab_id)
            .all()
        )
        node_devices: dict[str, str | None] = {n.container_name: n.device for n in db_nodes}
        node_images: dict[str, str | None] = {n.container_name: n.image for n in db_nodes}

        # D.2: Build indexed dicts from bulk-loaded data to avoid per-container queries
        nodes_by_container_name = {n.container_name: n for n in db_nodes}
        placements_by_node_name = {p.node_name: p for p in placements}

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

        # Query actual container status from ALL agents (in parallel)
        # Track both status and which agent has each container
        container_status_map: dict[str, str] = {}
        container_agent_map: dict[str, str] = {}  # node_name -> agent_id
        agents_successfully_queried: set[str] = set()  # Track which agents responded
        host_to_agent: dict[str, models.Host] = {}  # For live link creation

        # Build list of online agents to query
        agents_to_query: list[tuple[str, models.Host]] = []
        for agent_id in agent_ids:
            agent = session.get(models.Host, agent_id)
            if not agent or not agent_client.is_agent_online(agent):
                logger.debug(f"Agent {agent_id} is offline, skipping in reconciliation")
                continue
            host_to_agent[agent_id] = agent
            agents_to_query.append((agent_id, agent))

        async def _query_agent(aid: str, ag: models.Host):
            """Query a single agent for lab status."""
            try:
                result = await agent_client.get_lab_status_from_agent(ag, lab_id)
                return aid, ag, result, None
            except Exception as e:
                return aid, ag, None, e

        # Query all agents in parallel
        if agents_to_query:
            query_results = await asyncio.gather(
                *[_query_agent(aid, ag) for aid, ag in agents_to_query],
                return_exceptions=True,
            )

            for item in query_results:
                if isinstance(item, Exception):
                    logger.warning(f"Agent query failed with exception: {item}")
                    continue

                aid, ag, result, error = item
                if error:
                    logger.warning(f"Failed to query agent {ag.name} for lab {lab_id}: {error}")
                    _set_agent_error(ag, f"Query failed: {error}")
                    continue

                nodes = result.get("nodes", [])
                agent_error = result.get("error")

                if not agent_error:
                    agents_successfully_queried.add(aid)
                    _clear_agent_error(ag)
                else:
                    logger.warning(
                        f"Agent {ag.name} returned error for lab {lab_id}: {agent_error}"
                    )
                    _set_agent_error(ag, agent_error)

                for n in nodes:
                    node_name = n.get("name", "")
                    if node_name:
                        container_status_map[node_name] = n.get("status", "unknown")
                        container_agent_map[node_name] = aid

        logger.debug(f"Lab {lab_id} container status: {container_status_map}")

        # === ORPHAN CLEANUP: Remove containers/VMs not in topology ===
        # Build set of valid node names from database definitions
        valid_node_names = {n.container_name for n in db_nodes}

        # Only run cleanup if no active job (don't interfere with deploy/destroy)
        check_active_job = (
            session.query(models.Job)
            .filter(
                models.Job.lab_id == lab_id,
                models.Job.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value]),
            )
            .first()
        )
        if check_active_job:
            logger.debug(
                f"Skipping orphan cleanup for lab {lab_id} - active job {check_active_job.id} in progress"
            )
        else:
            # Find orphan containers: exist on agents but not in database
            orphan_node_names = set(container_status_map.keys()) - valid_node_names
            if orphan_node_names:
                logger.warning(
                    f"Lab {lab_id} has {len(orphan_node_names)} orphan container(s)/VM(s) "
                    f"not in topology: {list(orphan_node_names)}"
                )

                # Clean up orphan containers on each agent
                for agent_id in agents_successfully_queried:
                    agent = host_to_agent.get(agent_id)
                    if not agent:
                        continue

                    try:
                        cleanup_result = await agent_client.cleanup_lab_orphans(
                            agent, lab_id, list(valid_node_names)
                        )
                        removed = cleanup_result.get("removed_containers", [])
                        if removed:
                            logger.info(
                                f"Cleaned up {len(removed)} orphan(s) on agent {agent.name}: {removed}"
                            )
                            # Remove cleaned up containers from status map
                            for name in removed:
                                container_status_map.pop(name, None)
                                container_agent_map.pop(name, None)
                    except Exception as e:
                        logger.warning(f"Failed to cleanup orphans on agent {agent.name}: {e}")

            # Cleanup orphan NodePlacement records in database
            # This runs even if containers are already gone (e.g. manually removed)
            orphan_placements_to_delete = (
                session.query(models.NodePlacement)
                .filter(
                    models.NodePlacement.lab_id == lab_id,
                    ~models.NodePlacement.node_name.in_(valid_node_names),
                )
                .all()
            )
            for placement in orphan_placements_to_delete:
                logger.info(f"Removing orphan placement for node {placement.node_name} in lab {lab_id}")
                session.delete(placement)

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
        active_job = (
            session.query(models.Job)
            .filter(
                models.Job.lab_id == lab_id,
                models.Job.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value]),
            )
            .first()
        )

        # If there's an active job, refresh node_states from DB to pick up any
        # transitional state changes (stopping_started_at, starting_started_at)
        # that the job may have committed after we initially loaded them.
        # This prevents race conditions where reconciliation overwrites "stopping"
        # with "running" because it read stale state.
        if active_job:
            for ns in node_states:
                session.refresh(ns)

        for ns in node_states:
            # Skip nodes where enforcement has permanently failed.
            # Enforcement owns their state — reconciliation must not overwrite
            # the error state, which would cause an infinite retry oscillation.
            if ns.enforcement_failed_at is not None:
                logger.debug(
                    f"Skipping reconciliation for {ns.node_name}: "
                    f"enforcement_failed_at set"
                )
                error_count += 1
                continue

            # Skip nodes with active transitional operations
            # The job will handle state updates - reconciliation should not interfere
            #
            # Check stopping_started_at/starting_started_at FIRST, regardless of actual_state.
            # This handles race conditions where the job hasn't updated actual_state yet
            # but has already started the operation.

            # Check for active stop operation
            if ns.stopping_started_at:
                stopping_duration = datetime.now(timezone.utc) - ns.stopping_started_at
                if stopping_duration.total_seconds() < settings.stale_stopping_threshold:
                    # Stop operation in progress - let the job handle it
                    stopped_count += 1
                    continue
                # Timeout exceeded - fall through to normal reconciliation
                logger.warning(
                    f"Node {ns.node_name} in lab {lab_id} stuck in stopping operation for "
                    f"{stopping_duration.total_seconds():.0f}s, recovering via reconciliation"
                )
                # Clear the stale timestamp
                ns.stopping_started_at = None

            # Check for active start operation
            if ns.starting_started_at:
                starting_duration = datetime.now(timezone.utc) - ns.starting_started_at
                if starting_duration.total_seconds() < settings.stale_node_starting_threshold:
                    # Start operation in progress - let the job handle it
                    running_count += 1
                    continue
                # Timeout exceeded - fall through to normal reconciliation
                logger.warning(
                    f"Node {ns.node_name} in lab {lab_id} stuck in starting operation for "
                    f"{starting_duration.total_seconds():.0f}s, recovering via reconciliation"
                )
                # Clear the stale timestamp
                ns.starting_started_at = None

            # Additional check: skip "stopping" or "starting" states even without timestamp
            # if there's an active job (the job will manage state)
            if ns.actual_state == NodeActualState.STOPPING.value:
                if active_job:
                    stopped_count += 1
                    continue
                # No timestamp and no job - something is wrong, recover
                logger.warning(
                    f"Node {ns.node_name} in lab {lab_id} in 'stopping' state without "
                    f"timestamp or active job, recovering via reconciliation"
                )

            if ns.actual_state == NodeActualState.STARTING.value:
                if active_job:
                    running_count += 1
                    continue
                # No timestamp and no job - something is wrong, recover
                logger.warning(
                    f"Node {ns.node_name} in lab {lab_id} in 'starting' state without "
                    f"timestamp or active job, recovering via reconciliation"
                )

            container_status = container_status_map.get(ns.node_name)
            old_state = ns.actual_state
            old_is_ready = ns.is_ready

            if container_status:
                if container_status == "running":
                    ns.actual_state = NodeActualState.RUNNING.value
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
                            # Get device kind and determine provider type
                            device_kind = node_devices.get(ns.node_name)
                            node_image = node_images.get(ns.node_name)
                            provider_type = None
                            if node_image:
                                # Determine provider from image extension
                                if node_image.endswith((".qcow2", ".img")):
                                    provider_type = "libvirt"
                                else:
                                    provider_type = "docker"

                            readiness = await agent_client.check_node_readiness(
                                agent, lab_id, ns.node_name,
                                kind=device_kind,
                                provider_type=provider_type,
                            )
                            if readiness.get("is_ready", False):
                                ns.is_ready = True
                                # Record boot-wait duration metric
                                if ns.boot_started_at:
                                    boot_secs = (datetime.now(timezone.utc) - ns.boot_started_at).total_seconds()
                                    _boot_device = (device_kind or "linux").lower()
                                    nlm_phase_duration.labels(
                                        phase="boot_wait", device_type=_boot_device, status="success",
                                    ).observe(boot_secs)
                                logger.info(
                                    f"Node {ns.node_name} in lab {lab_id} is now ready"
                                )
                        except Exception as e:
                            logger.debug(f"Readiness check failed for {ns.node_name}: {e}")

                elif container_status in ("stopped", "exited"):
                    ns.actual_state = NodeActualState.STOPPED.value
                    ns.stopping_started_at = None  # Clear if recovering from stuck stopping
                    ns.starting_started_at = None  # Clear if recovering from stuck starting
                    ns.error_message = None
                    ns.is_ready = False
                    ns.boot_started_at = None
                    stopped_count += 1
                elif container_status in ("error", "dead"):
                    ns.actual_state = NodeActualState.ERROR.value
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
                    # Agent responded but container/domain not found — it truly doesn't exist.
                    # Both Docker (ps -a) and libvirt (virsh list --all) include stopped
                    # instances in status, so absence means the node is gone.
                    if ns.actual_state != "undeployed":
                        ns.actual_state = NodeActualState.UNDEPLOYED.value
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
                    "Node state transition",
                    extra={
                        "event": "node_state_transition",
                        "lab_id": lab_id,
                        "node_id": ns.node_id,
                        "node_name": ns.node_name,
                        "old_state": old_state,
                        "new_state": ns.actual_state,
                        "is_ready": ns.is_ready,
                        "trigger": "reconciliation",
                    },
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
        misplaced_containers: dict[str, str] = {}  # node_name -> wrong_agent_id
        for node_name, agent_id in container_agent_map.items():
            # Look up node definition from pre-built index (D.2)
            node_def = nodes_by_container_name.get(node_name)

            # Check if container is on the WRONG agent according to node definition
            if node_def and node_def.host_id and node_def.host_id != agent_id:
                misplaced_containers[node_name] = agent_id
                logger.warning(
                    f"MISPLACED CONTAINER: {node_name} in lab {lab_id} found on agent {agent_id} "
                    f"but should be on {node_def.host_id}. Queued for removal."
                )
                # Don't update placement for misplaced containers - this would perpetuate the bug
                continue

            existing_placement = placements_by_node_name.get(node_name)
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
                placements_by_node_name[node_name] = new_placement

        # Remove misplaced containers from wrong agents
        # Only do this when no active job to avoid interfering with deployments
        if misplaced_containers and not check_active_job:
            for node_name, wrong_agent_id in misplaced_containers.items():
                agent = host_to_agent.get(wrong_agent_id)
                if agent:
                    try:
                        await agent_client.destroy_container_on_agent(agent, lab_id, node_name)
                        logger.info(f"Removed misplaced container {node_name} from agent {agent.name}")
                    except Exception as e:
                        logger.warning(f"Failed to remove misplaced {node_name} from {agent.name}: {e}")

        # Update lab state based on aggregated node states using state machine
        old_lab_state = lab.state
        new_lab_state = LabStateMachine.compute_lab_state(
            running_count=running_count,
            stopped_count=stopped_count,
            undeployed_count=undeployed_count,
            error_count=error_count,
        )
        lab.state = new_lab_state.value
        if new_lab_state == LabState.ERROR:
            lab.state_error = f"{error_count} node(s) in error state"
        else:
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

        # D.4: Batch-load active VXLAN tunnels to avoid per-link queries
        active_tunnels = (
            session.query(models.VxlanTunnel)
            .filter(
                models.VxlanTunnel.lab_id == lab_id,
                models.VxlanTunnel.status == "active",
            )
            .all()
        )
        tunnels_by_link_state_id = {t.link_state_id: t for t in active_tunnels}

        for ls in link_states:
            old_actual = ls.actual_state
            source_state = node_actual_states.get(ls.source_node, "unknown")
            target_state = node_actual_states.get(ls.target_node, "unknown")

            # Determine link actual state based on endpoint node states
            if source_state == NodeActualState.RUNNING.value and target_state == NodeActualState.RUNNING.value:
                # Both nodes running - check L2 connectivity
                # If carrier states are off, link is administratively down
                if ls.source_carrier_state == "off" or ls.target_carrier_state == "off":
                    ls.actual_state = LinkActualState.DOWN.value
                    ls.error_message = "Carrier disabled on one or more endpoints"
                elif ls.is_cross_host:
                    # For cross-host links, verify VXLAN tunnel exists (D.4: dict lookup)
                    tunnel = tunnels_by_link_state_id.get(ls.id)
                    if tunnel:
                        ls.actual_state = LinkActualState.UP.value
                        ls.error_message = None
                    else:
                        # No active tunnel - link is broken
                        ls.actual_state = LinkActualState.ERROR.value
                        ls.error_message = "VXLAN tunnel not active"
                else:
                    # Same-host link - assume up if both nodes are running and carrier on
                    # Full L2 verification would require querying OVS VLAN tags from agent
                    # which adds latency. For now, trust that hot_connect worked.
                    ls.actual_state = LinkActualState.UP.value
                    ls.error_message = None
            elif source_state == NodeActualState.ERROR.value or target_state == NodeActualState.ERROR.value:
                # At least one node is in error state
                ls.actual_state = LinkActualState.ERROR.value
                ls.error_message = "One or more endpoint nodes in error state"
            elif source_state in (NodeActualState.STOPPED.value, NodeActualState.UNDEPLOYED.value) or target_state in (NodeActualState.STOPPED.value, NodeActualState.UNDEPLOYED.value):
                # At least one node is stopped/undeployed
                ls.actual_state = LinkActualState.DOWN.value
                ls.error_message = None
            else:
                # Unknown or transitional states
                ls.actual_state = LinkActualState.UNKNOWN.value
                ls.error_message = None

            if ls.actual_state != old_actual:
                logger.info(
                    "Link state transition",
                    extra={
                        "event": "link_state_transition",
                        "lab_id": lab_id,
                        "link_name": ls.link_name,
                        "old_state": old_actual,
                        "new_state": ls.actual_state,
                        "source_node": ls.source_node,
                        "target_node": ls.target_node,
                        "is_cross_host": ls.is_cross_host,
                        "trigger": "reconciliation",
                    },
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
                        source_oper_state=ls.source_oper_state,
                        target_oper_state=ls.target_oper_state,
                        source_oper_reason=ls.source_oper_reason,
                        target_oper_reason=ls.target_oper_reason,
                        oper_epoch=ls.oper_epoch,
                    )
                )

        # Auto-connect pending links when both nodes become running
        # This handles links that were added while nodes were not yet deployed
        # Also handles cross-host links where VXLAN tunnel is missing
        from app.tasks.live_links import create_link_if_ready

        # Collect links that need auto-connect
        links_to_connect = []
        for ls in link_states:
            # Check if link should be connected but isn't
            # Retry ALL error links, not just specific error types - the link setup
            # functions are idempotent and will reapply correct VLAN tags, recreate
            # VXLAN tunnels, etc. This handles recovery from agent restarts, VLAN
            # mismatches, transient failures, and other error conditions.
            should_auto_connect = (
                ls.desired_state == LinkDesiredState.UP.value
                and node_actual_states.get(ls.source_node) == NodeActualState.RUNNING.value
                and node_actual_states.get(ls.target_node) == NodeActualState.RUNNING.value
                and ls.actual_state in (LinkActualState.UNKNOWN.value, LinkActualState.PENDING.value, LinkActualState.DOWN.value, LinkActualState.ERROR.value)
            )
            if should_auto_connect:
                links_to_connect.append(ls)

        if links_to_connect:
            logger.info(
                f"Auto-connecting {len(links_to_connect)} link(s) in lab {lab_id}: "
                f"{[ls.link_name for ls in links_to_connect]}"
            )

        # Process auto-connect with lock to prevent conflicts with live_links
        if links_to_connect:
            lock_acquired = acquire_link_ops_lock(lab_id)
            if not lock_acquired:
                logger.debug(
                    f"Could not acquire link ops lock for lab {lab_id}, "
                    f"skipping auto-connect (will retry next cycle)"
                )
            else:
                try:
                    # Repair stale endpoints before attempting link creation.
                    # Rate-limited to avoid spamming repair on every cycle.
                    error_links = [
                        ls for ls in links_to_connect
                        if ls.actual_state == LinkActualState.ERROR.value
                    ]
                    if error_links:
                        now = datetime.now(timezone.utc)
                        last_repair = _last_endpoint_repair.get(lab_id)
                        if last_repair is None or (now - last_repair) >= ENDPOINT_REPAIR_COOLDOWN:
                            _last_endpoint_repair[lab_id] = now
                            # Collect unique agents that need repair
                            repair_agents: dict[str, list[str]] = {}
                            for ls in error_links:
                                for node_name in (ls.source_node, ls.target_node):
                                    host_id = node_expected_agent.get(node_name)
                                    if host_id and host_id in host_to_agent:
                                        repair_agents.setdefault(host_id, []).append(node_name)
                            for host_id, nodes in repair_agents.items():
                                agent = host_to_agent[host_id]
                                unique_nodes = list(set(nodes))
                                try:
                                    result = await agent_client.repair_endpoints_on_agent(
                                        agent, lab_id, unique_nodes,
                                    )
                                    repaired = result.get("total_endpoints_repaired", 0)
                                    if repaired > 0:
                                        logger.info(
                                            f"Repaired {repaired} endpoint(s) on {agent.name} "
                                            f"before auto-connect"
                                        )
                                except Exception as e:
                                    logger.warning(f"Endpoint repair on {agent.name} failed: {e}")

                    for ls in links_to_connect:
                        logger.info(f"Auto-connecting pending link {ls.link_name}")
                        try:
                            await create_link_if_ready(session, lab_id, ls, host_to_agent)
                        except Exception as e:
                            logger.error(f"Failed to auto-connect link {ls.link_name}: {e}")
                            ls.actual_state = LinkActualState.ERROR.value
                            ls.error_message = str(e)
                finally:
                    release_link_ops_lock(lab_id)

        # Clean up links marked for deletion (desired_state="deleted")
        for ls in link_states:
            if ls.desired_state == "deleted":
                session.delete(ls)

        # === OBSERVATION ONLY: Log nodes where actual != desired ===
        # Enforcement is handled solely by state_enforcement_monitor() —
        # reconciliation is read-only and does not create jobs.
        out_of_sync_count = 0
        for ns in node_states:
            if ns.actual_state in (NodeActualState.STOPPING.value, NodeActualState.STARTING.value, NodeActualState.PENDING.value):
                continue
            if ns.stopping_started_at or ns.starting_started_at:
                continue
            needs_start = (
                ns.desired_state == NodeDesiredState.RUNNING.value
                and ns.actual_state in (NodeActualState.STOPPED.value, NodeActualState.UNDEPLOYED.value, NodeActualState.ERROR.value)
            )
            needs_stop = (
                ns.desired_state == NodeDesiredState.STOPPED.value
                and ns.actual_state == NodeActualState.RUNNING.value
            )
            if needs_start or needs_stop:
                out_of_sync_count += 1

        if out_of_sync_count:
            logger.debug(
                f"Reconciliation: {out_of_sync_count} node(s) out of sync in lab {lab_id} "
                f"(enforcement deferred to state_enforcement_monitor)"
            )

        session.commit()

    except Exception as e:
        logger.error(f"Failed to reconcile lab {lab_id}: {e}")
        # Rollback any uncommitted changes to prevent idle-in-transaction
        session.rollback()


async def state_reconciliation_monitor():
    """Background task to periodically reconcile state.

    Runs every reconciliation_interval seconds and queries agents
    for actual container status, updating the database to match reality.
    """
    interval = settings.get_interval("reconciliation")
    logger.info(
        f"State reconciliation monitor started "
        f"(interval: {interval}s)"
    )

    while True:
        try:
            await asyncio.sleep(interval)
            await refresh_states_from_agents()
        except asyncio.CancelledError:
            logger.info("State reconciliation monitor stopped")
            break
        except Exception as e:
            logger.error(f"Error in state reconciliation monitor: {e}")
            # Continue running - don't let one error stop the monitor


async def reconcile_managed_interfaces():
    """Check managed interface status against actual host state.

    For each AgentManagedInterface record, queries the agent for actual
    interface state and updates sync_status/current_mtu accordingly.
    Runs less frequently than state reconciliation (called externally).
    """
    from app import agent_client

    session = db.SessionLocal()
    try:
        interfaces = session.query(models.AgentManagedInterface).all()
        if not interfaces:
            return

        # Group by host
        by_host: dict[str, list] = {}
        for iface in interfaces:
            by_host.setdefault(iface.host_id, []).append(iface)

        for host_id, ifaces in by_host.items():
            agent = session.get(models.Host, host_id)
            if not agent or not agent_client.is_agent_online(agent):
                continue

            try:
                # Get interface details from agent
                details = await agent_client.get_agent_interface_details(agent)
                if not details or not details.get("interfaces"):
                    continue

                actual_interfaces = {i["name"]: i for i in details["interfaces"]}

                for iface in ifaces:
                    actual = actual_interfaces.get(iface.name)
                    if actual:
                        iface.current_mtu = actual.get("mtu")
                        iface.is_up = actual.get("state", "").lower() == "up"
                        if iface.current_mtu == iface.desired_mtu and iface.is_up:
                            iface.sync_status = "synced"
                            iface.sync_error = None
                        else:
                            iface.sync_status = "mismatch"
                    else:
                        iface.sync_status = "mismatch"
                        iface.is_up = False
                    iface.last_sync_at = datetime.now(timezone.utc)

                session.commit()
            except Exception as e:
                logger.warning(f"Failed to reconcile interfaces for host {host_id}: {e}")
    finally:
        session.close()
