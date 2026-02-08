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
from app.db import SessionLocal, get_redis, get_session
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


def _is_on_cooldown(lab_id: str, node_name: str) -> bool:
    """Check if a node is still on cooldown from a recent enforcement attempt.

    Uses Redis EXIST to check if the cooldown key exists (TTL handles expiry).
    """
    try:
        return get_redis().exists(_cooldown_key(lab_id, node_name)) > 0
    except redis.RedisError as e:
        logger.warning(f"Redis error checking cooldown: {e}")
        # On Redis error, assume not on cooldown to avoid blocking enforcement
        return False


def _set_cooldown(lab_id: str, node_name: str):
    """Mark a node as having a recent enforcement attempt.

    Uses Redis SETEX with TTL equal to the cooldown period.
    """
    try:
        get_redis().setex(
            _cooldown_key(lab_id, node_name),
            settings.state_enforcement_cooldown,
            "1"
        )
    except redis.RedisError as e:
        logger.warning(f"Redis error setting cooldown: {e}")
        # Continue even if Redis fails - enforcement will still work, just might retry sooner


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
    if _is_on_cooldown(lab_id, node_name):
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

    # Set cooldown BEFORE creating job to prevent race with concurrent iterations
    # This ensures other enforcement loop iterations see the cooldown immediately
    _set_cooldown(lab_id, node_name)

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


def _is_enforceable(
    session: Session,
    node_state: models.NodeState,
) -> bool:
    """Check if a node passes all pre-filtering for enforcement.

    Runs the same checks as enforce_node_state() but without creating jobs:
    - Action determination (state machine)
    - Skip checks (max retries, backoff, cooldown)
    - Active job checks (per-node)

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
    if _is_on_cooldown(lab_id, node_name):
        logger.debug(f"Node {node_name} in lab {lab_id} is on enforcement cooldown")
        return False

    # Check for active per-node jobs
    if _has_active_job(session, lab_id, node_name):
        logger.debug(f"Node {node_name} in lab {lab_id} has active job, skipping enforcement")
        return False

    return True


def _has_lab_wide_active_job(session: Session, lab_id: str) -> bool:
    """Check if a lab has an active deploy/destroy job."""
    return session.query(models.Job).filter(
        models.Job.lab_id == lab_id,
        models.Job.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value]),
        models.Job.action.in_(["up", "down"]),
    ).first() is not None


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

            # Phase 1: Per-node filtering (skip checks, cooldown, backoff, active jobs)
            # Group passing nodes by lab_id
            enforceable_by_lab: Dict[str, list[models.NodeState]] = {}
            for node_state in mismatched_states:
                try:
                    if _is_enforceable(session, node_state):
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

            # Phase 2: Create one batch job per lab
            enforced_count = 0
            now = datetime.now(timezone.utc)

            for lab_id, nodes in enforceable_by_lab.items():
                lab = session.get(models.Lab, lab_id)
                if not lab:
                    continue

                # Skip if lab has active deploy/destroy
                if _has_lab_wide_active_job(session, lab_id):
                    logger.debug(f"Lab {lab_id} has active deploy/destroy job, skipping batch enforcement")
                    continue

                try:
                    # Update per-node tracking
                    node_ids = []
                    for ns in nodes:
                        _set_cooldown(lab_id, ns.node_name)
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
