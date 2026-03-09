"""State reconciliation – agent refresh and boot-readiness checks.

Extracted from reconciliation.py.  Functions here query agents for actual
container/VM status and update the database to match reality, or check
boot-readiness for running nodes.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta, timezone

from app import agent_client, models
from app.config import settings
from app.metrics import nlm_phase_duration, record_reconciliation_cycle
from app.db import get_session

from app.services.broadcaster import broadcast_node_state_change
from app.services.state_machine import LabStateMachine
from app.state import (
    LabState,
    NodeActualState,
    NodeDesiredState,
)
from app.tasks.jobs import _release_db_transaction_for_io
from app.utils.time import utcnow

logger = logging.getLogger(__name__)


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
    from app.tasks.reconciliation_db import (
        _maybe_cleanup_labless_containers,
        _reconcile_single_lab,
    )

    _cycle_start = time.monotonic()
    _labs_checked = 0
    _state_changes = 0

    with get_session() as session:
        try:
            # Find labs that need reconciliation:
            # - Labs in transitional states (starting, stopping, unknown)
            # - Labs where state has been stuck for too long
            now = utcnow()
            transitional_threshold = now - timedelta(seconds=settings.stale_starting_threshold)

            transitional_labs = (
                session.query(models.Lab)
                .filter(
                    models.Lab.state.in_([LabState.STARTING.value, LabState.STOPPING.value, LabState.UNKNOWN.value]),
                    models.Lab.state_updated_at < transitional_threshold,
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
                    models.NodeState.is_ready.is_(False),
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
                        LabState.STOPPED.value,
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
                _labs_checked = len(labs_to_reconcile)

                for lab_id in labs_to_reconcile:
                    _state_changes += await _reconcile_single_lab(session, lab_id)

            # Periodic global orphan cleanup: remove containers from deleted labs
            # Runs less frequently than per-lab reconciliation since it scans ALL
            # containers on each agent. Every 10th cycle ≈ every 5 minutes at 30s interval.
            await _maybe_cleanup_labless_containers(session)

        except Exception as e:
            logger.error(f"Error in state reconciliation: {e}")
        finally:
            elapsed = time.monotonic() - _cycle_start
            record_reconciliation_cycle(elapsed, _labs_checked, _state_changes)


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

            node_names = [ns.node_name for ns in lab_nodes]
            placements = (
                session.query(models.NodePlacement)
                .filter(
                    models.NodePlacement.lab_id == lab_id,
                    models.NodePlacement.node_name.in_(node_names),
                )
                .all()
            )
            placement_by_node = {
                p.node_name: p.host_id for p in placements if p.host_id
            }

            host_ids = {p.host_id for p in placements if p.host_id}
            if lab.agent_id:
                host_ids.add(lab.agent_id)

            agents_by_id: dict[str, models.Host] = {}
            for host_id in host_ids:
                host = session.get(models.Host, host_id)
                if host and agent_client.is_agent_online(host):
                    agents_by_id[host_id] = host

            if not agents_by_id:
                logger.debug(
                    f"No pre-resolved online agents for lab {lab_id}; "
                    "falling back to dynamic agent lookup"
                )

            pending_boot_started_update = False
            node_snapshots: list[dict[str, object]] = []
            for ns in lab_nodes:
                # Set boot_started_at if not already set
                if not ns.boot_started_at:
                    ns.boot_started_at = utcnow()
                    pending_boot_started_update = True
                node_snapshots.append(
                    {
                        "node_state_id": ns.id,
                        "node_id": ns.node_id,
                        "node_name": ns.node_name,
                        "desired_state": ns.desired_state,
                        "actual_state": ns.actual_state,
                        "error_message": ns.error_message,
                        "boot_started_at": ns.boot_started_at,
                        "host_id": placement_by_node.get(ns.node_name) or lab.agent_id,
                    }
                )

            if pending_boot_started_update:
                _release_db_transaction_for_io(
                    session,
                    context=f"readiness boot timestamp backfill for lab {lab_id}",
                    table="node_states",
                    lab_id=lab_id,
                )

            for node in node_snapshots:
                node_name = str(node["node_name"])
                host_id = node["host_id"]
                agent = agents_by_id.get(host_id) if host_id else None
                if not agent:
                    _release_db_transaction_for_io(
                        session,
                        context=f"readiness agent lookup for {node_name}",
                        table="node_states",
                        lab_id=lab_id,
                    )
                    agent = await agent_client.get_agent_for_node(
                        session,
                        lab_id,
                        node_name,
                        required_provider=lab_provider,
                    )
                if not agent:
                    _release_db_transaction_for_io(
                        session,
                        context=f"readiness lab agent lookup for {node_name}",
                        table="node_states",
                        lab_id=lab_id,
                    )
                    agent = await agent_client.get_agent_for_lab(
                        session,
                        lab,
                        required_provider=lab_provider,
                    )
                if not agent:
                    logger.debug(
                        f"No reachable agent for {node_name} in lab {lab_id}, "
                        "skipping readiness check"
                    )
                    continue

                try:
                    _release_db_transaction_for_io(
                        session,
                        context=f"readiness probe for {node_name}",
                        table="node_states",
                        lab_id=lab_id,
                    )
                    # Get the device kind and determine provider type for this node
                    device_kind = node_devices.get(node_name)
                    provider_type = None
                    if device_kind:
                        # Look up the Node to determine provider from image
                        db_node = next(
                            (n for n in db_nodes if n.container_name == node_name),
                            None,
                        )
                        if db_node and db_node.image:
                            provider_type = get_node_provider(db_node)

                    readiness = await agent_client.check_node_readiness(
                        agent, lab_id, node_name,
                        kind=device_kind,
                        provider_type=provider_type,
                    )
                    if readiness.get("is_ready", False):
                        tracked_ns = session.get(models.NodeState, str(node["node_state_id"]))
                        if tracked_ns is None:
                            continue
                        tracked_ns.is_ready = True
                        # Record boot-wait duration metric
                        boot_started_at = tracked_ns.boot_started_at or node["boot_started_at"]
                        if boot_started_at:
                            if boot_started_at.tzinfo is None:
                                boot_started_at = boot_started_at.replace(tzinfo=timezone.utc)
                            else:
                                boot_started_at = boot_started_at.astimezone(timezone.utc)
                            boot_secs = (utcnow() - boot_started_at).total_seconds()
                            _boot_device = (node_devices.get(node_name) or "linux").lower()
                            nlm_phase_duration.labels(
                                phase="boot_wait", device_type=_boot_device, status="success",
                            ).observe(boot_secs)
                        logger.info(f"Node {node_name} in lab {lab_id} is now ready")
                        session.commit()
                        # Broadcast readiness change to frontend via WebSocket
                        asyncio.create_task(
                            broadcast_node_state_change(
                                lab_id=lab_id,
                                node_id=str(node["node_id"]),
                                node_name=node_name,
                                desired_state=str(node["desired_state"]),
                                actual_state=str(node["actual_state"]),
                                is_ready=True,
                                error_message=str(node["error_message"]) if node["error_message"] else None,
                                host_id=agent.id,
                                host_name=agent.name,
                            )
                        )
                except Exception as e:
                    logger.debug(f"Readiness check failed for {node_name}: {e}")

        except Exception as e:
            logger.error(f"Error checking readiness for lab {lab_id}: {e}")
            try:
                session.rollback()
            except Exception:
                pass
