"""Node state and reconciliation endpoints for labs."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import db, models, schemas
from app.auth import get_current_user
from app.services.topology import TopologyService
from app.services.state_machine import NodeStateMachine
from app.state import JobStatus, NodeActualState, NodeDesiredState
from app.utils.agents import get_online_agent_for_lab
from app.utils.async_tasks import safe_create_task
from app.utils.http import raise_unavailable
from app.utils.lab import get_lab_or_404, require_lab_editor
from app.utils.nodes import get_node_placement_mapping

# Import shared helper functions from labs router module.
# labs.py imports this module only after these helpers are defined.
from app.routers.labs import (
    _converge_stopped_error_state,
    _create_node_sync_job,
    _enrich_node_state,
    _ensure_node_states_exist,
    _get_or_create_node_state,
    _upsert_node_states,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["labs"])
# ============================================================================
# Node State Management Endpoints
# ============================================================================


def _has_conflicting_job(*args, **kwargs):
    """Resolve via labs module so existing monkeypatch targets keep working."""
    from app.routers import labs as labs_router

    return labs_router.has_conflicting_job(*args, **kwargs)


@router.get("/labs/{lab_id}/nodes/states")
async def list_node_states(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.NodeStatesResponse:
    """Get all node states for a lab.

    Returns the desired and actual state for each node in the topology.
    Auto-creates missing NodeState records for labs with existing topologies.
    Auto-refreshes stale pending states if no active jobs are running.
    """
    from app import agent_client
    from app.utils.lab import get_lab_provider

    lab = get_lab_or_404(lab_id, database, current_user)

    # Sync NodeState records from database topology
    service = TopologyService(database)
    if service.has_nodes(lab.id):
        graph = service.export_to_graph(lab.id)
        _upsert_node_states(database, lab.id, graph)
        database.commit()

    states = (
        database.query(models.NodeState)
        .filter(models.NodeState.lab_id == lab_id)
        .order_by(models.NodeState.node_name)
        .all()
    )

    # Auto-fix stale pending states: if any node is "pending" but no active job exists,
    # refresh from actual container status
    has_pending = any(s.actual_state == NodeActualState.PENDING for s in states)
    if has_pending:
        active_job = (
            database.query(models.Job)
            .filter(
                models.Job.lab_id == lab_id,
                models.Job.status.in_(["pending", JobStatus.RUNNING]),
            )
            .first()
        )
        if not active_job:
            # No active job but states are pending - refresh from container status
            try:
                lab_provider = get_lab_provider(lab)
                agent = await get_online_agent_for_lab(
                    database, lab, required_provider=lab_provider
                )
                if agent:
                    result = await agent_client.get_lab_status_from_agent(agent, lab.id)
                    nodes = result.get("nodes", [])
                    container_status_map = {
                        n.get("name", ""): n.get("status", "unknown") for n in nodes
                    }
                    for ns in states:
                        if ns.actual_state == NodeActualState.PENDING:
                            container_status = container_status_map.get(ns.node_name)
                            if container_status == "running":
                                ns.actual_state = NodeActualState.RUNNING
                                ns.error_message = None
                                if not ns.boot_started_at:
                                    ns.boot_started_at = datetime.now(timezone.utc)
                            elif container_status in ("stopped", "exited"):
                                ns.actual_state = NodeActualState.STOPPED
                                ns.error_message = None
                                ns.boot_started_at = None
                            # NOTE: Don't mark as "undeployed" here if container not found.
                            # In multi-host labs, this agent may not be the one hosting the node.
                            # Let the reconciliation task handle undeployed detection properly.
                    database.commit()
            except Exception:
                pass  # Best effort - don't fail the request if refresh fails

    # Enrich states with host information
    placement_by_node, hosts = get_node_placement_mapping(database, lab_id, lab.agent_id)

    # Build enriched response
    enriched_nodes = []
    for s in states:
        node_data = _enrich_node_state(s)
        # Try placement first, then fall back to lab's agent
        host_id = placement_by_node.get(s.node_name) or lab.agent_id
        if host_id:
            node_data.host_id = host_id
            node_data.host_name = hosts.get(host_id)
        enriched_nodes.append(node_data)

    return schemas.NodeStatesResponse(nodes=enriched_nodes)


@router.get("/labs/{lab_id}/nodes/{node_id}/state")
def get_node_state(
    lab_id: str,
    node_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.NodeStateOut:
    """Get the state for a specific node."""
    lab = get_lab_or_404(lab_id, database, current_user)
    _ensure_node_states_exist(database, lab.id)
    state = _get_or_create_node_state(database, lab.id, node_id)
    return _enrich_node_state(state)


@router.put("/labs/{lab_id}/nodes/{node_id}/desired-state")
def set_node_desired_state(
    lab_id: str,
    node_id: str,
    payload: schemas.NodeStateUpdate,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.NodeStateOut:
    """Set the desired state for a node (running or stopped).

    Auto-triggers sync to immediately apply the change. This eliminates
    the need for a separate "Sync" button in the UI.
    """

    lab = require_lab_editor(lab_id, database, current_user)

    logger.info(
        "User state change request",
        extra={
            "event": "user_state_request",
            "user_id": str(current_user.id),
            "user_email": current_user.email,
            "lab_id": lab_id,
            "node_id": node_id,
            "requested_state": payload.state,
            "endpoint": "set_node_desired_state",
        },
    )

    _ensure_node_states_exist(database, lab.id)
    command = "start" if payload.state == NodeDesiredState.RUNNING else "stop"
    state = _get_or_create_node_state(database, lab.id, node_id, initial_desired_state=payload.state, for_update=True)

    # Centralized guard check (6.1)
    allowed, reason = NodeStateMachine.can_accept_command(state.actual_state, command)
    if not allowed:
        raise HTTPException(status_code=409, detail=reason)

    # Check if state actually changed
    desired_changed = state.desired_state != payload.state

    # Update desired state (may differ from initial if record already existed)
    if desired_changed:
        state.desired_state = payload.state
        normalized = False
        if payload.state == NodeDesiredState.STOPPED:
            normalized = _converge_stopped_error_state(state)
        database.commit()
        database.refresh(state)

        # Auto-sync: immediately trigger reconciliation for this node
        has_conflict, _ = _has_conflicting_job(lab_id, "sync", session=database)
        if (
            not normalized
            and not has_conflict
            and NodeStateMachine.needs_sync(state.actual_state, command)
        ):
            _create_node_sync_job(database, lab, node_id, current_user)

    elif (
        state.desired_state == payload.state
        and payload.state == NodeDesiredState.RUNNING
        and state.actual_state == NodeActualState.ERROR
    ):
        # Retry: node is stuck in error but user wants it running again.
        # Reset enforcement state so the system will attempt reconciliation.
        state.reset_enforcement(clear_error=True)
        database.commit()
        database.refresh(state)

        has_conflict, _ = _has_conflicting_job(lab_id, "sync", session=database)
        if not has_conflict:
            _create_node_sync_job(database, lab, node_id, current_user)
    elif payload.state == NodeDesiredState.STOPPED:
        if _converge_stopped_error_state(state):
            database.commit()
            database.refresh(state)

    return _enrich_node_state(state)


@router.put("/labs/{lab_id}/nodes/desired-state")
async def set_all_nodes_desired_state(
    lab_id: str,
    payload: schemas.NodeStateUpdate,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.NodeStatesResponse:
    """Set the desired state for all nodes in a lab.

    Useful for "Start All" or "Stop All" operations.
    Selectively processes nodes that are ready, skips transitional nodes,
    and returns counts of affected/skipped/already-in-state.
    """
    from app.tasks.jobs import run_node_reconcile
    from app.utils.lab import get_lab_provider

    lab = require_lab_editor(lab_id, database, current_user)

    logger.info(
        "User state change request",
        extra={
            "event": "user_state_request",
            "user_id": str(current_user.id),
            "user_email": current_user.email,
            "lab_id": lab_id,
            "requested_state": payload.state,
            "endpoint": "set_all_nodes_desired_state",
        },
    )

    _ensure_node_states_exist(database, lab.id)
    command = "start" if payload.state == NodeDesiredState.RUNNING else "stop"

    states = (
        database.query(models.NodeState)
        .filter(models.NodeState.lab_id == lab_id)
        .with_for_update()
        .all()
    )

    # Selective processing: classify each node via centralized guards (6.1)
    affected_count = 0
    skipped_transitional_count = 0
    already_in_state_count = 0
    nodes_needing_sync: list[str] = []

    for state in states:
        classification, _reason = NodeStateMachine.can_accept_bulk_command(
            state.actual_state, state.desired_state, command
        )

        if classification == "skip_transitional":
            skipped_transitional_count += 1
            continue

        if classification == "already_in_state":
            already_in_state_count += 1
            continue

        # This node can be processed (proceed or reset_and_proceed)
        state.desired_state = payload.state

        if command == "stop" and _converge_stopped_error_state(state):
            affected_count += 1
            continue

        # Reset enforcement state for error nodes being retried
        if classification == "reset_and_proceed":
            state.reset_enforcement(clear_error=True)

        affected_count += 1

        if NodeStateMachine.needs_sync(state.actual_state, command):
            nodes_needing_sync.append(state.node_id)

    database.commit()

    # Clear enforcement cooldowns so enforcement doesn't ignore the new
    # desired state for minutes due to stale Redis TTL keys
    if affected_count > 0:
        affected_names = [
            s.node_name for s in states
            if s.desired_state == payload.state
        ]
        from app.tasks.state_enforcement import clear_cooldowns_for_lab
        safe_create_task(
            clear_cooldowns_for_lab(lab_id, affected_names),
            name=f"clear_cooldowns:{lab_id}"
        )

    logger.info(
        "Bulk state change result",
        extra={
            "event": "bulk_state_result",
            "lab_id": lab_id,
            "requested_state": payload.state,
            "affected": affected_count,
            "skipped_transitional": skipped_transitional_count,
            "already_in_state": already_in_state_count,
            "nodes_needing_sync": len(nodes_needing_sync),
        },
    )

    # Auto-sync: immediately trigger reconciliation for affected nodes
    if nodes_needing_sync:
        has_conflict, _ = _has_conflicting_job(lab_id, "sync", session=database)
        if not has_conflict:
            provider = get_lab_provider(lab)
            job = models.Job(
                lab_id=lab.id,
                user_id=current_user.id,
                action=f"sync:lab:{','.join(nodes_needing_sync)}",
                status=JobStatus.QUEUED,
            )
            database.add(job)
            database.commit()
            database.refresh(job)

            safe_create_task(
                run_node_reconcile(job.id, lab.id, nodes_needing_sync, provider=provider),
                name=f"sync:bulk:{job.id}"
            )

    # Refresh and return all states with counts
    states = (
        database.query(models.NodeState)
        .filter(models.NodeState.lab_id == lab_id)
        .order_by(models.NodeState.node_name)
        .all()
    )
    return schemas.NodeStatesResponse(
        nodes=[_enrich_node_state(s) for s in states],
        affected=affected_count,
        skipped_transitional=skipped_transitional_count,
        already_in_state=already_in_state_count,
    )


@router.post("/labs/{lab_id}/nodes/refresh")
async def refresh_node_states(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.NodeStatesResponse:
    """Refresh node states from actual container status.

    Queries all agents that have nodes for this lab and updates the NodeState
    records to match. Use this when states appear out of sync with reality.
    """
    from app import agent_client
    from app.utils.lab import get_lab_provider

    lab = get_lab_or_404(lab_id, database, current_user)
    _ensure_node_states_exist(database, lab.id)

    lab_provider = get_lab_provider(lab)

    # Get ALL agents that have nodes for this lab (multi-host support)
    # Same pattern as reconciliation.py
    placements = (
        database.query(models.NodePlacement)
        .filter(models.NodePlacement.lab_id == lab_id)
        .all()
    )
    agent_ids = {p.host_id for p in placements}
    # Map node names to their expected agent for safer state updates
    node_expected_agent: dict[str, str] = {p.node_name: p.host_id for p in placements}

    # Also include the lab's default agent if set
    if lab.agent_id:
        agent_ids.add(lab.agent_id)

    # If no placements and no default, find any healthy agent
    if not agent_ids:
        fallback_agent = await get_online_agent_for_lab(
            database, lab, required_provider=lab_provider
        )
        if fallback_agent:
            agent_ids.add(fallback_agent.id)

    if not agent_ids:
        raise_unavailable("No healthy agent available")

    # Query actual container status from ALL agents
    container_status_map: dict[str, str] = {}
    agents_successfully_queried: set[str] = set()

    for agent_id in agent_ids:
        agent = database.get(models.Host, agent_id)
        if not agent or not agent_client.is_agent_online(agent):
            continue

        try:
            result = await agent_client.get_lab_status_from_agent(agent, lab.id)
            nodes = result.get("nodes", [])
            agent_error = result.get("error")

            # Only count as successfully queried if no error in response
            if not agent_error:
                agents_successfully_queried.add(agent_id)
            else:
                logger.warning(f"Agent {agent.name} returned error for lab {lab_id}: {agent_error}")

            # Still merge any nodes that were returned (partial success)
            for n in nodes:
                node_name = n.get("name", "")
                if node_name:
                    container_status_map[node_name] = n.get("status", "unknown")
        except Exception as e:
            # Log but continue - we'll update states for agents that responded
            logger.warning(f"Failed to query agent {agent.name} for lab {lab_id}: {e}")

    if not agents_successfully_queried:
        raise_unavailable("Failed to reach any agent for this lab")

    # Update NodeState records based on actual container status
    node_states = (
        database.query(models.NodeState)
        .filter(models.NodeState.lab_id == lab_id)
        .all()
    )

    for ns in node_states:
        # Skip nodes with active transitional operations - let the job handle state updates
        # This prevents refresh from overwriting "stopping" with "running" mid-operation
        if ns.stopping_started_at:
            stopping_duration = datetime.now(timezone.utc) - ns.stopping_started_at
            if stopping_duration.total_seconds() < 360:  # 6 minutes
                continue  # Don't overwrite transitional state

        if ns.starting_started_at:
            starting_duration = datetime.now(timezone.utc) - ns.starting_started_at
            if starting_duration.total_seconds() < 360:  # 6 minutes
                continue  # Don't overwrite transitional state

        # Also skip if actual_state is transitional (backup for timestamp edge cases)
        if ns.actual_state in (NodeActualState.STOPPING, NodeActualState.STARTING, NodeActualState.PENDING):
            continue

        container_status = container_status_map.get(ns.node_name)
        if container_status:
            if container_status == "running":
                ns.actual_state = NodeActualState.RUNNING
                ns.stopping_started_at = None  # Clear if recovering
                ns.starting_started_at = None
                ns.error_message = None
                if not ns.boot_started_at:
                    ns.boot_started_at = datetime.now(timezone.utc)
            elif container_status in ("stopped", "exited"):
                ns.actual_state = NodeActualState.STOPPED
                ns.stopping_started_at = None  # Clear if recovering
                ns.starting_started_at = None
                ns.error_message = None
                ns.boot_started_at = None
        else:
            # Container not found - only update if the relevant agent was queried
            expected_agent = node_expected_agent.get(ns.node_name)
            (
                expected_agent in agents_successfully_queried
                if expected_agent
                else len(agents_successfully_queried) > 0
            )
            # If agent was queried but container not found, preserve existing state
            # (don't mark as undeployed - that's reconciliation's job)

    database.commit()

    # Return updated states
    states = (
        database.query(models.NodeState)
        .filter(models.NodeState.lab_id == lab_id)
        .order_by(models.NodeState.node_name)
        .all()
    )
    return schemas.NodeStatesResponse(
        nodes=[_enrich_node_state(s) for s in states]
    )


def _get_out_of_sync_nodes(
    database: Session,
    lab_id: str,
    node_ids: list[str] | None = None,
) -> list[models.NodeState]:
    """Find nodes where actual_state doesn't match desired_state.

    Args:
        database: Database session
        lab_id: Lab ID to check
        node_ids: Optional list of specific node IDs to check. If None, checks all.

    Returns:
        List of NodeState records that need syncing
    """
    query = database.query(models.NodeState).filter(
        models.NodeState.lab_id == lab_id
    )

    if node_ids:
        query = query.filter(models.NodeState.node_id.in_(node_ids))

    states = query.all()

    # A node is out of sync if:
    # - desired=running and actual not in (running, pending)
    # - desired=stopped and actual not in (stopped, undeployed)
    out_of_sync = []
    for state in states:
        if state.desired_state == NodeDesiredState.RUNNING:
            if state.actual_state not in (NodeActualState.RUNNING, NodeActualState.PENDING):
                out_of_sync.append(state)
        elif state.desired_state == NodeDesiredState.STOPPED:
            if state.actual_state not in (NodeActualState.STOPPED, NodeActualState.UNDEPLOYED):
                out_of_sync.append(state)

    return out_of_sync


@router.post("/labs/{lab_id}/nodes/{node_id}/reconcile")
async def reconcile_node(
    lab_id: str,
    node_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.ReconcileResponse:
    """Trigger a reconcile job for a single node.

    This will bring the node's actual state in line with its desired state.
    If the node is already in the correct state, no job is created.
    """
    from app.tasks.jobs import run_node_reconcile
    from app.utils.lab import get_lab_provider, get_node_provider

    lab = require_lab_editor(lab_id, database, current_user)
    _ensure_node_states_exist(database, lab.id)

    # Get or create the node state with correct naming
    _get_or_create_node_state(database, lab.id, node_id, initial_desired_state=NodeDesiredState.RUNNING)

    # Check if node needs reconciliation
    out_of_sync = _get_out_of_sync_nodes(database, lab_id, [node_id])
    if not out_of_sync:
        return schemas.ReconcileResponse(
            job_id="",
            message="Node is already in correct state",
            nodes_to_reconcile=[],
        )

    # Determine provider based on node's image type
    db_node = database.query(models.Node).filter(
        models.Node.lab_id == lab.id,
        models.Node.gui_id == node_id
    ).first()
    if db_node:
        node_provider = get_node_provider(db_node, database)
    else:
        node_provider = get_lab_provider(lab)

    # Get agent for this node
    agent = await get_online_agent_for_lab(database, lab, required_provider=node_provider)
    if not agent:
        raise_unavailable(f"No healthy agent available with {node_provider} support")

    # Note: Don't set state to pending here - let the task handle state transitions
    # after it reads the current state to determine what action is needed

    # Create reconcile job
    job = models.Job(
        lab_id=lab.id,
        user_id=current_user.id,
        action=f"reconcile:node:{node_id}",
        status=JobStatus.QUEUED,
    )
    database.add(job)
    database.commit()
    database.refresh(job)

    # Start background reconcile task
    safe_create_task(
        run_node_reconcile(job.id, lab.id, [node_id], provider=node_provider),
        name=f"reconcile:node:{job.id}"
    )

    return schemas.ReconcileResponse(
        job_id=job.id,
        message="Reconcile job queued",
        nodes_to_reconcile=[node_id],
    )


@router.post("/labs/{lab_id}/reconcile")
async def reconcile_lab(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.ReconcileResponse:
    """Trigger a reconcile job for all out-of-state nodes in a lab.

    This will bring all nodes' actual states in line with their desired states.
    If all nodes are already in the correct state, no job is created.
    """
    from app.tasks.jobs import run_node_reconcile
    from app.utils.lab import get_lab_provider

    lab = require_lab_editor(lab_id, database, current_user)

    # Check for conflicting jobs before proceeding
    has_conflict, conflicting_action = _has_conflicting_job(lab_id, "reconcile")
    if has_conflict:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot reconcile lab: '{conflicting_action}' operation already in progress"
        )
    _ensure_node_states_exist(database, lab.id)

    # Find all nodes needing reconciliation
    out_of_sync = _get_out_of_sync_nodes(database, lab_id)
    if not out_of_sync:
        return schemas.ReconcileResponse(
            job_id="",
            message="All nodes are already in correct state",
            nodes_to_reconcile=[],
        )

    node_ids = [s.node_id for s in out_of_sync]

    # Get agent for this lab
    lab_provider = get_lab_provider(lab)
    agent = await get_online_agent_for_lab(database, lab, required_provider=lab_provider)
    if not agent:
        raise_unavailable(f"No healthy agent available with {lab_provider} support")

    # Note: Don't set states to pending here - let the task handle state transitions
    # after it reads the current states to determine what actions are needed

    # Create reconcile job
    job = models.Job(
        lab_id=lab.id,
        user_id=current_user.id,
        action=f"reconcile:lab:{','.join(node_ids)}",
        status=JobStatus.QUEUED,
    )
    database.add(job)
    database.commit()
    database.refresh(job)

    # Start background reconcile task
    safe_create_task(
        run_node_reconcile(job.id, lab.id, node_ids, provider=lab_provider),
        name=f"reconcile:lab:{job.id}"
    )

    return schemas.ReconcileResponse(
        job_id=job.id,
        message=f"Reconcile job queued for {len(node_ids)} node(s)",
        nodes_to_reconcile=node_ids,
    )
