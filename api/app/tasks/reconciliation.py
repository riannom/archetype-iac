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
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import agent_client, models
from app.config import settings
from app.db import SessionLocal
from app.services.topology import TopologyService
from app.utils.job import is_job_within_timeout

logger = logging.getLogger(__name__)


def _generate_link_name(
    source_node: str,
    source_interface: str,
    target_node: str,
    target_interface: str,
) -> str:
    """Generate a canonical link name from endpoints.

    Link names are sorted alphabetically to ensure the same link always gets
    the same name regardless of endpoint order.
    """
    ep_a = f"{source_node}:{source_interface}"
    ep_b = f"{target_node}:{target_interface}"
    # Sort endpoints alphabetically for consistent naming
    if ep_a <= ep_b:
        return f"{ep_a}-{ep_b}"
    return f"{ep_b}-{ep_a}"


def _ensure_link_states_for_lab(session, lab_id: str, topology_yaml: str | None = None) -> int:
    """Ensure LinkState records exist for all links in a lab's topology.

    This is called during reconciliation to create missing link state records
    for labs that may have been deployed before link state tracking was added.

    Tries database first (source of truth), falls back to YAML if provided.

    Returns the number of link states created.
    """
    from app.topology import yaml_to_graph

    # Try to get links from database first
    service = TopologyService(session)
    db_links = service.get_links(lab_id)

    if db_links:
        # Use database as source of truth
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

    # Fall back to YAML parsing for unmigrated labs
    if not topology_yaml:
        return 0

    try:
        graph = yaml_to_graph(topology_yaml)
    except Exception as e:
        logger.debug(f"Failed to parse topology for lab {lab_id}: {e}")
        return 0

    # Get existing link states
    existing = (
        session.query(models.LinkState)
        .filter(models.LinkState.lab_id == lab_id)
        .all()
    )
    existing_names = {ls.link_name for ls in existing}

    # Build node ID to name mapping
    node_id_to_name: dict[str, str] = {}
    for node in graph.nodes:
        node_id_to_name[node.id] = node.container_name or node.name

    created_count = 0

    for link in graph.links:
        if len(link.endpoints) != 2:
            continue

        ep_a, ep_b = link.endpoints

        # Skip external endpoints
        if ep_a.type != "node" or ep_b.type != "node":
            continue

        source_node = node_id_to_name.get(ep_a.node, ep_a.node)
        target_node = node_id_to_name.get(ep_b.node, ep_b.node)
        source_interface = ep_a.ifname or "eth0"
        target_interface = ep_b.ifname or "eth0"

        link_name = _generate_link_name(
            source_node, source_interface, target_node, target_interface
        )

        if link_name not in existing_names:
            # Ensure canonical ordering
            if f"{source_node}:{source_interface}" <= f"{target_node}:{target_interface}":
                src_n, src_i = source_node, source_interface
                tgt_n, tgt_i = target_node, target_interface
            else:
                src_n, src_i = target_node, target_interface
                tgt_n, tgt_i = source_node, source_interface

            new_state = models.LinkState(
                lab_id=lab_id,
                link_name=link_name,
                source_node=src_n,
                source_interface=src_i,
                target_node=tgt_n,
                target_interface=tgt_i,
                desired_state="up",
                actual_state="unknown",
            )
            session.add(new_state)
            existing_names.add(link_name)
            created_count += 1

    return created_count


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
    finally:
        session.close()


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
    from app.storage import topology_path
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

    # Ensure link states exist for this lab (for backwards compatibility)
    topo_path = topology_path(lab_id)
    if topo_path.exists():
        try:
            topology_yaml = topo_path.read_text(encoding="utf-8")
            links_created = _ensure_link_states_for_lab(session, lab_id, topology_yaml)
            if links_created > 0:
                logger.info(f"Created {links_created} link state(s) for lab {lab_id}")
        except Exception as e:
            logger.debug(f"Failed to ensure link states for lab {lab_id}: {e}")

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
        for agent_id in agent_ids:
            agent = session.get(models.Host, agent_id)
            if not agent or not agent_client.is_agent_online(agent):
                logger.debug(f"Agent {agent_id} is offline, skipping in reconciliation")
                continue

            try:
                result = await agent_client.get_lab_status_from_agent(agent, lab_id)
                nodes = result.get("nodes", [])
                # Merge container status from this agent
                for n in nodes:
                    node_name = n.get("name", "")
                    if node_name:
                        container_status_map[node_name] = n.get("status", "unknown")
                        container_agent_map[node_name] = agent_id
            except Exception as e:
                logger.warning(f"Failed to query agent {agent.name} for lab {lab_id}: {e}")

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

        # Ensure NodePlacement records exist for containers found on agents
        # This handles cases where deploy jobs failed after containers were created
        for node_name, agent_id in container_agent_map.items():
            existing_placement = (
                session.query(models.NodePlacement)
                .filter(
                    models.NodePlacement.lab_id == lab_id,
                    models.NodePlacement.node_name == node_name,
                )
                .first()
            )
            if existing_placement:
                # Update if container moved to a different agent
                if existing_placement.host_id != agent_id:
                    logger.info(
                        f"Updating placement for {node_name} in lab {lab_id}: "
                        f"{existing_placement.host_id} -> {agent_id}"
                    )
                    existing_placement.host_id = agent_id
                    existing_placement.status = "deployed"
            else:
                # Create new placement record
                logger.info(
                    f"Creating placement for {node_name} in lab {lab_id} on agent {agent_id}"
                )
                new_placement = models.NodePlacement(
                    lab_id=lab_id,
                    node_name=node_name,
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

        # Reconcile link states based on node states
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
                # Both nodes running - link is operational
                # If user wants it down, actual is still "up" from infrastructure perspective
                # The desired_state tracks user intent
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
