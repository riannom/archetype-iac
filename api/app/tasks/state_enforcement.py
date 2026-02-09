"""State enforcement task - automatically corrects desired vs actual state mismatches.

This task periodically checks for nodes where desired_state != actual_state and
triggers corrective actions (start/stop) to bring actual state in line with desired.

Unlike the reconciliation task (which is read-only and just updates the database),
this task takes corrective action by triggering jobs.

Enhanced features:
- Retry tracking: Tracks enforcement attempts per node in the database
- Exponential backoff: Uses min(base * 2^attempts, max) for retry delays
- Max retries: After N attempts, marks node as error and stops retrying
- Crash cooldown: Nodes that crash within cooldown are not auto-restarted
- Broadcaster integration: Notifies UI of enforcement failures
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Set

import redis
from sqlalchemy.orm import Session

from app import models
from app.config import settings
from app.db import get_async_redis, get_session
from app import agent_client
from app.utils.async_tasks import safe_create_task
from app.state import (
    JobStatus,
    LabState,
    NodeActualState,
    NodeDesiredState,
)
from app.services.state_machine import NodeStateMachine

logger = logging.getLogger(__name__)


def _calculate_backoff(attempts: int) -> int:
    """Calculate exponential backoff delay for retry attempts.

    Uses min(base * 2^attempts, max_cooldown) formula.

    Args:
        attempts: Number of previous attempts (0-based)

    Returns:
        Delay in seconds before next retry
    """
    base = settings.state_enforcement_retry_backoff
    max_delay = settings.state_enforcement_cooldown
    delay = base * (2 ** attempts)
    return min(delay, max_delay)


def _should_skip_enforcement(node_state: models.NodeState) -> tuple[bool, str]:
    """Check if enforcement should be skipped for a node.

    Args:
        node_state: The node state to check

    Returns:
        Tuple of (should_skip, reason)
    """
    now = datetime.now(timezone.utc)

    def _ensure_aware(dt: datetime | None) -> datetime | None:
        """Ensure datetime is timezone-aware (SQLite strips tz info)."""
        if dt is not None and dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

    # Skip if max retries exhausted
    if node_state.enforcement_attempts >= settings.state_enforcement_max_retries:
        if node_state.enforcement_failed_at:
            return True, "max retries exhausted"
        # Mark as failed if not already marked
        return True, "max retries reached"

    # Skip if within crash cooldown
    failed_at = _ensure_aware(node_state.enforcement_failed_at)
    if failed_at:
        cooldown_end = failed_at + timedelta(
            seconds=settings.state_enforcement_crash_cooldown
        )
        if now < cooldown_end:
            remaining = (cooldown_end - now).seconds
            return True, f"in crash cooldown ({remaining}s remaining)"

    # Skip if within backoff delay
    last_at = _ensure_aware(node_state.last_enforcement_at)
    if last_at and node_state.enforcement_attempts > 0:
        backoff = _calculate_backoff(node_state.enforcement_attempts - 1)
        backoff_end = last_at + timedelta(seconds=backoff)
        if now < backoff_end:
            remaining = (backoff_end - now).seconds
            return True, f"in backoff delay ({remaining}s remaining)"

    return False, ""


async def _notify_enforcement_failure(
    lab_id: str, node_state: models.NodeState
) -> None:
    """Notify the UI of an enforcement failure via broadcaster.

    Args:
        lab_id: Lab identifier
        node_state: The node that failed enforcement
    """
    try:
        from app.services.broadcaster import get_broadcaster

        broadcaster = get_broadcaster()
        await broadcaster.publish_node_state(lab_id, {
            "node_id": node_state.node_id,
            "node_name": node_state.node_name,
            "desired_state": node_state.desired_state,
            "actual_state": "error",
            "is_ready": False,
            "error_message": (
                f"State enforcement failed after {node_state.enforcement_attempts} attempts. "
                f"Manual intervention required."
            ),
        })
    except Exception as e:
        logger.warning(f"Failed to notify UI of enforcement failure: {e}")


def _cooldown_key(lab_id: str, node_name: str) -> str:
    """Generate Redis key for a node's enforcement cooldown."""
    return f"enforcement_cooldown:{lab_id}:{node_name}"


async def _is_on_cooldown(lab_id: str, node_name: str) -> bool:
    """Check if a node is still on cooldown from a recent enforcement attempt.

    Uses async Redis EXISTS to check if the cooldown key exists (TTL handles expiry).
    """
    try:
        r = get_async_redis()
        return await r.exists(_cooldown_key(lab_id, node_name)) > 0
    except redis.RedisError as e:
        logger.warning(f"Redis error checking cooldown: {e}")
        # On Redis error, assume not on cooldown to avoid blocking enforcement
        return False


async def _set_cooldown(lab_id: str, node_name: str):
    """Mark a node as having a recent enforcement attempt.

    Uses async Redis SETEX with TTL equal to the cooldown period.
    """
    try:
        r = get_async_redis()
        await r.setex(
            _cooldown_key(lab_id, node_name),
            settings.state_enforcement_cooldown,
            "1"
        )
    except redis.RedisError as e:
        logger.warning(f"Redis error setting cooldown: {e}")
        # Continue even if Redis fails - enforcement will still work, just might retry sooner


async def clear_cooldowns_for_lab(lab_id: str, node_names: list[str]):
    """Clear enforcement cooldown keys for nodes in a lab.

    Called when user triggers an explicit operation (Start All, Stop All,
    Deploy, Destroy) so that enforcement doesn't ignore the new desired
    state for up to 5 minutes due to stale cooldown keys.
    """
    if not node_names:
        return
    try:
        r = get_async_redis()
        keys = [_cooldown_key(lab_id, name) for name in node_names]
        deleted = await r.delete(*keys)
        if deleted:
            logger.info(f"Cleared {deleted} enforcement cooldown(s) for lab {lab_id}")
    except redis.RedisError as e:
        logger.warning(f"Redis error clearing cooldowns for lab {lab_id}: {e}")


def _has_active_job(session: Session, lab_id: str, node_name: str | None = None) -> bool:
    """Check if there's an active job for this lab/node."""
    query = session.query(models.Job).filter(
        models.Job.lab_id == lab_id,
        models.Job.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value]),
    )

    if node_name:
        # Check for node-specific jobs
        query = query.filter(
            models.Job.action.like(f"node:%:{node_name}")
        )

    return query.first() is not None


async def _get_agent_for_node(
    session: Session, lab: models.Lab, node_state: models.NodeState
) -> models.Host | None:
    """Get the agent that should handle actions for a node.

    Uses FK-first lookup strategy for reliability, falls back to string matching.

    Priority order:
    1. Node definition's host_id (via FK, then string match)
    2. NodePlacement record (via FK, then string match)
    3. Lab's default agent
    """
    node_def = None

    # 1. Try FK lookup first (most reliable)
    if node_state.node_definition_id:
        node_def = session.get(models.Node, node_state.node_definition_id)

    # 2. Fall back to string matching
    if not node_def:
        node_def = session.query(models.Node).filter(
            models.Node.lab_id == lab.id,
            models.Node.container_name == node_state.node_name,
        ).first()

        # Link for future lookups
        if node_def and not node_state.node_definition_id:
            node_state.node_definition_id = node_def.id
            logger.info(f"Linked NodeState {node_state.node_id} to Node {node_def.id}")

    if node_def and node_def.host_id:
        agent = session.get(models.Host, node_def.host_id)
        if agent and agent_client.is_agent_online(agent):
            return agent

    # 3. Check NodePlacement (FK-first, then string)
    placement = None
    if node_state.node_definition_id:
        placement = session.query(models.NodePlacement).filter(
            models.NodePlacement.lab_id == lab.id,
            models.NodePlacement.node_definition_id == node_state.node_definition_id,
        ).first()

    if not placement:
        placement = session.query(models.NodePlacement).filter(
            models.NodePlacement.lab_id == lab.id,
            models.NodePlacement.node_name == node_state.node_name,
        ).first()

    if placement and placement.host_id:
        agent = session.get(models.Host, placement.host_id)
        if agent and agent_client.is_agent_online(agent):
            return agent

    # 4. Fall back to lab's default agent
    if lab.agent_id:
        agent = session.get(models.Host, lab.agent_id)
        if agent and agent_client.is_agent_online(agent):
            return agent

    # 5. Fall back to any healthy agent with required provider
    # Use node-specific provider for mixed labs (docker vs libvirt)
    from app.utils.lab import get_lab_provider, get_node_provider
    if node_def:
        provider = get_node_provider(node_def, session)
    else:
        provider = get_lab_provider(lab)
    return await agent_client.get_healthy_agent(session, required_provider=provider)


async def enforce_node_state(
    session: Session,
    lab: models.Lab,
    node_state: models.NodeState,
) -> bool:
    """Attempt to correct a single node's state mismatch.

    This function:
    1. Determines the appropriate corrective action (start/stop)
    2. Checks if enforcement should be skipped (max retries, cooldown, backoff)
    3. Tracks enforcement attempts in the database
    4. Triggers a sync job to correct the state
    5. Notifies UI if max retries are exhausted

    Returns True if an enforcement job was started, False otherwise.
    """
    from app.tasks.jobs import run_node_reconcile

    lab_id = lab.id
    node_name = node_state.node_name
    desired = node_state.desired_state
    actual = node_state.actual_state
    now = datetime.now(timezone.utc)

    # Determine what action is needed using state machine
    action = NodeStateMachine.get_enforcement_action(
        NodeActualState(actual) if actual in [s.value for s in NodeActualState] else NodeActualState.ERROR,
        NodeDesiredState(desired) if desired in [s.value for s in NodeDesiredState] else NodeDesiredState.STOPPED,
    )

    # Handle special case: pending node that needs to be started
    # This handles nodes stuck in pending state (e.g., agent was unavailable when added)
    if desired == NodeDesiredState.RUNNING.value and actual == NodeActualState.PENDING.value:
        action = "start"

    # Handle special case: auto-restart disabled for error state
    if action == "start" and actual == NodeActualState.ERROR.value:
        if not settings.state_enforcement_auto_restart_enabled:
            logger.debug(f"Auto-restart disabled for {node_name}, skipping")
            return False

    if not action:
        # No clear action for this mismatch
        logger.debug(
            f"No enforcement action for {node_name}: desired={desired}, actual={actual}"
        )
        return False

    # Check if enforcement should be skipped (max retries, backoff, cooldown)
    should_skip, reason = _should_skip_enforcement(node_state)
    if should_skip:
        # If max retries reached and not yet marked as failed, mark it now
        if "max retries" in reason and not node_state.enforcement_failed_at:
            node_state.enforcement_failed_at = now
            node_state.actual_state = NodeActualState.ERROR.value
            original_error = node_state.error_message
            node_state.error_message = (
                f"State enforcement failed after {node_state.enforcement_attempts} attempts. "
                f"Last error: {original_error or 'unknown'}"
            )
            session.commit()
            logger.warning(
                f"Node {node_name} in lab {lab_id} exceeded max enforcement retries "
                f"({node_state.enforcement_attempts}). Marking as error."
            )
            # Notify UI of the failure
            safe_create_task(
                _notify_enforcement_failure(lab_id, node_state),
                name=f"notify:enforcement:{lab_id}:{node_name}"
            )
        else:
            logger.debug(f"Node {node_name} in lab {lab_id}: {reason}")
        return False

    # Check legacy Redis cooldown (for backward compatibility)
    if await _is_on_cooldown(lab_id, node_name):
        logger.debug(f"Node {node_name} in lab {lab_id} is on enforcement cooldown")
        return False

    # Check for active jobs
    if _has_active_job(session, lab_id, node_name):
        logger.debug(f"Node {node_name} in lab {lab_id} has active job, skipping enforcement")
        return False

    # Check for lab-wide active jobs (deploy/destroy)
    lab_job = session.query(models.Job).filter(
        models.Job.lab_id == lab_id,
        models.Job.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value]),
        models.Job.action.in_(["up", "down"]),
    ).first()
    if lab_job:
        logger.debug(f"Lab {lab_id} has active deploy/destroy job, skipping enforcement")
        return False

    # Get agent for this node
    agent = await _get_agent_for_node(session, lab, node_state)
    if not agent:
        logger.warning(
            f"Cannot enforce state for {node_name} in lab {lab_id}: no healthy agent"
        )
        return False

    # Ensure placement record matches the agent we're using
    node_def = None
    if action == "start":
        # Get node_definition_id for FK-based placement
        if node_state.node_definition_id:
            node_def = session.get(models.Node, node_state.node_definition_id)
        if not node_def:
            node_def = session.query(models.Node).filter(
                models.Node.lab_id == lab_id,
                models.Node.container_name == node_name,
            ).first()

        placement = session.query(models.NodePlacement).filter(
            models.NodePlacement.lab_id == lab_id,
            models.NodePlacement.node_name == node_name,
        ).first()

        if placement:
            if placement.host_id != agent.id:
                logger.info(
                    f"Updating placement for {node_name}: {placement.host_id} -> {agent.id}"
                )
                placement.host_id = agent.id
            # Backfill node_definition_id if missing
            if node_def and not placement.node_definition_id:
                placement.node_definition_id = node_def.id
        else:
            placement = models.NodePlacement(
                lab_id=lab_id,
                node_name=node_name,
                node_definition_id=node_def.id if node_def else None,
                host_id=agent.id,
                status="deployed",
            )
            session.add(placement)
            logger.info(f"Created placement for {node_name} on agent {agent.id}")

    # Re-verify desired_state hasn't changed since we started checks
    # (e.g. user clicked Stop All between check and job creation)
    session.refresh(node_state)
    current_desired = node_state.desired_state
    if current_desired != desired:
        logger.info(
            f"Desired state changed for {node_name} ({desired} -> {current_desired}), "
            f"skipping enforcement"
        )
        return False

    # Set cooldown BEFORE creating job to prevent race with concurrent iterations
    # This ensures other enforcement loop iterations see the cooldown immediately
    await _set_cooldown(lab_id, node_name)

    # Track enforcement attempt in database
    node_state.enforcement_attempts += 1
    node_state.last_enforcement_at = now
    # Clear failed marker since we're retrying
    if node_state.enforcement_failed_at:
        logger.info(f"Retrying failed node {node_name} after crash cooldown")
        node_state.enforcement_failed_at = None

    # Create enforcement job using sync path for proper transitional states
    # Use node_id (GUI ID) for sync job, which will resolve to container_name
    node_id = node_state.node_id
    job = models.Job(
        lab_id=lab_id,
        user_id=None,  # System-initiated
        action=f"sync:node:{node_id}",
        status="queued",
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    logger.info(
        f"State enforcement: {action} node {node_name} in lab {lab_id} "
        f"(desired={desired}, actual={actual}, attempt={node_state.enforcement_attempts}, "
        f"job={job.id})"
    )

    # Determine provider based on node's image type
    from app.utils.lab import get_lab_provider, get_node_provider
    if node_def:
        provider = get_node_provider(node_def, session)
    else:
        provider = get_lab_provider(lab)

    # Start sync job - this sets transitional states (starting/stopping)
    safe_create_task(
        run_node_reconcile(job.id, lab_id, [node_id], provider=provider),
        name=f"enforce:sync:{job.id}"
    )

    return True


async def _is_enforceable(
    session: Session,
    node_state: models.NodeState,
    active_job_nodes: Set[tuple[str, str]] | None = None,
) -> bool:
    """Check if a node passes all pre-filtering for enforcement.

    Runs the same checks as enforce_node_state() but without creating jobs:
    - Action determination (state machine)
    - Skip checks (max retries, backoff, cooldown)
    - Active job checks (per-node)

    Args:
        session: Database session
        node_state: The node state to check
        active_job_nodes: Optional pre-loaded set of (lab_id, node_name) tuples
            with active jobs. When provided, replaces per-node DB query (D.1).

    Side effect: marks nodes as failed if max retries exhausted.
    Returns True if the node should be included in a batch enforcement job.
    """
    desired = node_state.desired_state
    actual = node_state.actual_state
    node_name = node_state.node_name
    lab_id = node_state.lab_id
    now = datetime.now(timezone.utc)

    # Determine what action is needed using state machine
    action = NodeStateMachine.get_enforcement_action(
        NodeActualState(actual) if actual in [s.value for s in NodeActualState] else NodeActualState.ERROR,
        NodeDesiredState(desired) if desired in [s.value for s in NodeDesiredState] else NodeDesiredState.STOPPED,
    )

    # Handle special case: pending node that needs to be started
    if desired == NodeDesiredState.RUNNING.value and actual == NodeActualState.PENDING.value:
        action = "start"

    # Handle special case: auto-restart disabled for error state
    if action == "start" and actual == NodeActualState.ERROR.value:
        if not settings.state_enforcement_auto_restart_enabled:
            logger.debug(f"Auto-restart disabled for {node_name}, skipping")
            return False

    if not action:
        logger.debug(
            f"No enforcement action for {node_name}: desired={desired}, actual={actual}"
        )
        return False

    # Check if enforcement should be skipped (max retries, backoff, cooldown)
    should_skip, reason = _should_skip_enforcement(node_state)
    if should_skip:
        if "max retries" in reason and not node_state.enforcement_failed_at:
            node_state.enforcement_failed_at = now
            node_state.actual_state = NodeActualState.ERROR.value
            original_error = node_state.error_message
            node_state.error_message = (
                f"State enforcement failed after {node_state.enforcement_attempts} attempts. "
                f"Last error: {original_error or 'unknown'}"
            )
            session.commit()
            logger.warning(
                f"Node {node_name} in lab {lab_id} exceeded max enforcement retries "
                f"({node_state.enforcement_attempts}). Marking as error."
            )
            safe_create_task(
                _notify_enforcement_failure(lab_id, node_state),
                name=f"notify:enforcement:{lab_id}:{node_name}"
            )
        else:
            logger.debug(f"Node {node_name} in lab {lab_id}: {reason}")
        return False

    # Check legacy Redis cooldown
    if await _is_on_cooldown(lab_id, node_name):
        logger.debug(f"Node {node_name} in lab {lab_id} is on enforcement cooldown")
        return False

    # D.1: Check for active per-node jobs using pre-loaded set or fallback to DB query
    if active_job_nodes is not None:
        if (lab_id, node_name) in active_job_nodes:
            logger.debug(f"Node {node_name} in lab {lab_id} has active job, skipping enforcement")
            return False
    else:
        if _has_active_job(session, lab_id, node_name):
            logger.debug(f"Node {node_name} in lab {lab_id} has active job, skipping enforcement")
            return False

    return True


def _has_lab_wide_active_job(
    session: Session,
    lab_id: str,
    labs_with_active_jobs: Set[str] | None = None,
) -> bool:
    """Check if a lab has an active deploy/destroy job.

    Args:
        session: Database session
        lab_id: Lab identifier
        labs_with_active_jobs: Optional pre-loaded set of lab IDs with active
            deploy/destroy jobs. When provided, replaces DB query (D.1).
    """
    if labs_with_active_jobs is not None:
        return lab_id in labs_with_active_jobs
    return session.query(models.Job).filter(
        models.Job.lab_id == lab_id,
        models.Job.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value]),
        models.Job.action.in_(["up", "down"]),
    ).first() is not None


async def _try_extract_configs(
    session: Session,
    lab: models.Lab,
    nodes: list[models.NodeState],
    hosts_by_id: Dict[str, models.Host] | None = None,
) -> None:
    """Best-effort config extraction before enforcement restart.

    Extracts running configs from nodes that are about to be restarted
    (exited/error state) and saves them as snapshots. Silent on failure —
    the container may already be gone.

    Args:
        session: Database session
        lab: Lab model instance
        nodes: List of node states being enforced
        hosts_by_id: Optional pre-loaded host dict. When provided,
            replaces per-host session.get() calls (D.3).
    """
    if not settings.feature_auto_extract_on_enforcement:
        return

    # Only extract from nodes that might still have configs (exited, error)
    restart_nodes = [
        ns for ns in nodes
        if ns.actual_state in (NodeActualState.EXITED.value, NodeActualState.ERROR.value)
    ]
    if not restart_nodes:
        return

    lab_id = lab.id
    try:
        # Get agents hosting this lab's nodes
        placements = (
            session.query(models.NodePlacement.host_id)
            .filter(models.NodePlacement.lab_id == lab_id)
            .distinct()
            .all()
        )
        host_ids = [p.host_id for p in placements]
        if not host_ids and lab.agent_id:
            host_ids = [lab.agent_id]

        agents = []
        for host_id in host_ids:
            # D.3: Use pre-loaded hosts when available, fall back to DB
            host = hosts_by_id.get(host_id) if hosts_by_id else session.get(models.Host, host_id)
            if host and agent_client.is_agent_online(host):
                agents.append(host)

        if not agents:
            return

        # Extract concurrently from all agents
        import asyncio as _asyncio
        tasks = [agent_client.extract_configs_on_agent(a, lab_id) for a in agents]
        results = await _asyncio.gather(*tasks, return_exceptions=True)

        # Collect configs
        configs = []
        for a, result in zip(agents, results):
            if isinstance(result, Exception):
                continue
            if not result.get("success"):
                continue
            configs.extend(result.get("configs", []))

        if not configs:
            return

        # Filter to only the nodes being restarted
        restart_names = {ns.node_name for ns in restart_nodes}
        from app.services.config_service import ConfigService
        config_svc = ConfigService(session)

        # Build device kind lookup
        lab_nodes = (
            session.query(models.Node)
            .filter(models.Node.lab_id == lab_id)
            .all()
        )
        node_device_map = {n.container_name: n.device for n in lab_nodes}

        for config_data in configs:
            node_name = config_data.get("node_name")
            content = config_data.get("content")
            if not node_name or not content or node_name not in restart_names:
                continue
            config_svc.save_extracted_config(
                lab_id=lab_id,
                node_name=node_name,
                content=content,
                snapshot_type="auto_restart",
                device_kind=node_device_map.get(node_name),
                set_as_active=False,
            )

        logger.info(
            f"Auto-extracted configs for {len(restart_names)} restart nodes in lab {lab_id}"
        )
    except Exception as e:
        logger.debug(f"Config extraction before enforcement failed for lab {lab_id}: {e}")


async def enforce_lab_states():
    """Find and correct all state mismatches across labs.

    Batches enforceable nodes by lab, creating one job per lab instead of
    one per node. This reduces job count, NLM instances, and HTTP round-trips.
    """
    if not settings.state_enforcement_enabled:
        return

    with get_session() as session:
        try:
            from app.tasks.jobs import run_node_reconcile
            from app.utils.lab import get_lab_provider

            # Find all node_states where desired != actual for running labs
            mismatched_states = (
                session.query(models.NodeState)
                .join(models.Lab, models.NodeState.lab_id == models.Lab.id)
                .filter(
                    models.NodeState.desired_state != models.NodeState.actual_state,
                    # Only consider labs that are in a stable state (not transitioning)
                    models.Lab.state.in_([LabState.RUNNING.value, LabState.STOPPED.value, LabState.ERROR.value]),
                )
                .all()
            )

            if not mismatched_states:
                return

            logger.debug(f"Found {len(mismatched_states)} nodes with state mismatches")

            # D.1: Batch-load active jobs for all affected labs (replaces per-node LIKE queries)
            lab_ids = {ns.lab_id for ns in mismatched_states}

            # Batch-load active node-level jobs
            active_node_jobs = (
                session.query(models.Job.lab_id, models.Job.action)
                .filter(
                    models.Job.lab_id.in_(lab_ids),
                    models.Job.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value]),
                    models.Job.action.like("node:%"),
                )
                .all()
            )

            # Build lookup set: extract node_name from "node:{action}:{node_name}" pattern
            active_job_nodes: Set[tuple[str, str]] = set()
            for lab_id_j, action in active_node_jobs:
                parts = action.split(":")
                if len(parts) >= 3:
                    active_job_nodes.add((lab_id_j, parts[2]))

            # Batch-load lab-wide jobs (deploy/destroy)
            labs_with_active_jobs: Set[str] = {
                j.lab_id for j in
                session.query(models.Job.lab_id)
                .filter(
                    models.Job.lab_id.in_(lab_ids),
                    models.Job.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value]),
                    models.Job.action.in_(["up", "down"]),
                )
                .all()
            }

            # Phase 1: Per-node filtering (skip checks, cooldown, backoff, active jobs)
            # Group passing nodes by lab_id
            enforceable_by_lab: Dict[str, list[models.NodeState]] = {}
            for node_state in mismatched_states:
                try:
                    if await _is_enforceable(session, node_state, active_job_nodes=active_job_nodes):
                        enforceable_by_lab.setdefault(node_state.lab_id, []).append(node_state)
                except Exception as e:
                    logger.error(
                        f"Error filtering {node_state.node_name} "
                        f"in lab {node_state.lab_id}: {e}"
                    )
                    try:
                        session.rollback()
                        # Increment attempts to prevent infinite loop on persistent exceptions
                        node_state.enforcement_attempts += 1
                        node_state.last_enforcement_at = datetime.now(timezone.utc)
                        if node_state.enforcement_attempts >= settings.state_enforcement_max_retries:
                            node_state.enforcement_failed_at = datetime.now(timezone.utc)
                            node_state.error_message = (
                                f"Enforcement exception after {node_state.enforcement_attempts} "
                                f"attempts: {e}"
                            )
                        session.commit()
                    except Exception:
                        try:
                            session.rollback()
                        except Exception:
                            pass

            if not enforceable_by_lab:
                return

            # D.3: Pre-load hosts referenced by placements and lab agents
            all_lab_ids = set(enforceable_by_lab.keys())
            all_placements = (
                session.query(models.NodePlacement)
                .filter(models.NodePlacement.lab_id.in_(all_lab_ids))
                .all()
            )
            all_host_ids: Set[str] = {p.host_id for p in all_placements if p.host_id}
            # Include lab default agents
            for _lab_id in all_lab_ids:
                _lab = session.get(models.Lab, _lab_id)
                if _lab and _lab.agent_id:
                    all_host_ids.add(_lab.agent_id)

            hosts_by_id: Dict[str, models.Host] = {}
            if all_host_ids:
                hosts_by_id = {
                    h.id: h for h in
                    session.query(models.Host).filter(models.Host.id.in_(all_host_ids)).all()
                }

            # Phase 2: Create one batch job per lab
            enforced_count = 0
            now = datetime.now(timezone.utc)

            for lab_id, nodes in enforceable_by_lab.items():
                lab = session.get(models.Lab, lab_id)
                if not lab:
                    continue

                # Skip if lab has active deploy/destroy (D.1: use pre-loaded set)
                if _has_lab_wide_active_job(session, lab_id, labs_with_active_jobs=labs_with_active_jobs):
                    logger.debug(f"Lab {lab_id} has active deploy/destroy job, skipping batch enforcement")
                    continue

                try:
                    # Best-effort config extraction before restarting crashed nodes
                    await _try_extract_configs(session, lab, nodes, hosts_by_id=hosts_by_id)

                    # Update per-node tracking
                    node_ids = []
                    for ns in nodes:
                        await _set_cooldown(lab_id, ns.node_name)
                        ns.enforcement_attempts += 1
                        ns.last_enforcement_at = now
                        if ns.enforcement_failed_at:
                            logger.info(f"Retrying failed node {ns.node_name} after crash cooldown")
                            ns.enforcement_failed_at = None
                        node_ids.append(ns.node_id)

                    # Create one batch job for all nodes in this lab
                    job = models.Job(
                        lab_id=lab_id,
                        user_id=None,  # System-initiated
                        action=f"sync:batch:{len(node_ids)}",
                        status="queued",
                    )
                    session.add(job)
                    session.commit()
                    session.refresh(job)

                    provider = get_lab_provider(lab)

                    logger.info(
                        f"State enforcement: batch {len(node_ids)} nodes in lab {lab_id} "
                        f"(job={job.id}, provider={provider})"
                    )

                    safe_create_task(
                        run_node_reconcile(job.id, lab_id, node_ids, provider=provider),
                        name=f"enforce:batch:{job.id}"
                    )

                    enforced_count += len(node_ids)
                except Exception as e:
                    logger.error(f"Error creating batch enforcement for lab {lab_id}: {e}")
                    try:
                        session.rollback()
                    except Exception:
                        pass

            if enforced_count > 0:
                logger.info(f"State enforcement triggered {enforced_count} corrective actions")

        except Exception as e:
            logger.error(f"Error in state enforcement: {e}")
            try:
                session.rollback()
            except Exception:
                pass


async def state_enforcement_monitor():
    """Background task to periodically enforce state.

    Runs every state_enforcement_interval seconds and triggers
    corrective actions for nodes where desired_state != actual_state.
    """
    interval = settings.get_interval("state_enforcement")
    logger.info(
        f"State enforcement monitor started "
        f"(enabled: {settings.state_enforcement_enabled}, "
        f"interval: {interval}s, "
        f"cooldown: {settings.state_enforcement_cooldown}s)"
    )

    while True:
        try:
            await asyncio.sleep(interval)
            await enforce_lab_states()
        except asyncio.CancelledError:
            logger.info("State enforcement monitor stopped")
            break
        except Exception as e:
            logger.error(f"Error in state enforcement monitor: {e}")
            # Continue running - don't let one error stop the monitor
