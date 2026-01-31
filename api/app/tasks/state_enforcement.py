"""State enforcement task - automatically corrects desired vs actual state mismatches.

This task periodically checks for nodes where desired_state != actual_state and
triggers corrective actions (start/stop) to bring actual state in line with desired.

Unlike the reconciliation task (which is read-only and just updates the database),
this task takes corrective action by triggering jobs.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Set

from sqlalchemy.orm import Session

from app import models
from app.config import settings
from app.db import SessionLocal
from app import agent_client

logger = logging.getLogger(__name__)

# In-memory tracking of recent enforcement attempts to avoid retry storms
# Maps (lab_id, node_name) -> last enforcement timestamp
_enforcement_cooldowns: Dict[tuple[str, str], datetime] = {}


def _is_on_cooldown(lab_id: str, node_name: str) -> bool:
    """Check if a node is still on cooldown from a recent enforcement attempt."""
    key = (lab_id, node_name)
    last_attempt = _enforcement_cooldowns.get(key)
    if not last_attempt:
        return False

    cooldown_until = last_attempt + timedelta(seconds=settings.state_enforcement_cooldown)
    return datetime.now(timezone.utc) < cooldown_until


def _set_cooldown(lab_id: str, node_name: str):
    """Mark a node as having a recent enforcement attempt."""
    key = (lab_id, node_name)
    _enforcement_cooldowns[key] = datetime.now(timezone.utc)


def _cleanup_old_cooldowns():
    """Remove expired cooldowns from memory."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=settings.state_enforcement_cooldown * 2)
    expired = [k for k, v in _enforcement_cooldowns.items() if v < cutoff]
    for k in expired:
        del _enforcement_cooldowns[k]


def _has_active_job(session: Session, lab_id: str, node_name: str | None = None) -> bool:
    """Check if there's an active job for this lab/node."""
    query = session.query(models.Job).filter(
        models.Job.lab_id == lab_id,
        models.Job.status.in_(["queued", "running"]),
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

    Priority order:
    1. Node definition's host_id (source of truth for where node should run)
    2. NodePlacement record (runtime placement)
    3. Lab's default agent
    """
    # First check the node definition - this is the source of truth
    node_def = session.query(models.Node).filter(
        models.Node.lab_id == lab.id,
        models.Node.container_name == node_state.node_name,
    ).first()

    if node_def and node_def.host_id:
        agent = session.get(models.Host, node_def.host_id)
        if agent and agent_client.is_agent_online(agent):
            return agent

    # Check if there's a placement record for this node
    placement = session.query(models.NodePlacement).filter(
        models.NodePlacement.lab_id == lab.id,
        models.NodePlacement.node_name == node_state.node_name,
    ).first()

    if placement and placement.host_id:
        agent = session.get(models.Host, placement.host_id)
        if agent and agent_client.is_agent_online(agent):
            return agent

    # Fall back to lab's default agent
    if lab.agent_id:
        agent = session.get(models.Host, lab.agent_id)
        if agent and agent_client.is_agent_online(agent):
            return agent

    # No suitable agent found
    return None


async def enforce_node_state(
    session: Session,
    lab: models.Lab,
    node_state: models.NodeState,
) -> bool:
    """Attempt to correct a single node's state mismatch.

    Returns True if an enforcement job was started, False otherwise.
    """
    from app.tasks.jobs import run_agent_job

    lab_id = lab.id
    node_name = node_state.node_name
    desired = node_state.desired_state
    actual = node_state.actual_state

    # Determine what action is needed
    if desired == "running" and actual in ("stopped", "undeployed", "exited"):
        action = "start"
    elif desired == "stopped" and actual == "running":
        action = "stop"
    else:
        # No clear action for this mismatch (e.g., error states)
        logger.debug(
            f"No enforcement action for {node_name}: desired={desired}, actual={actual}"
        )
        return False

    # Check cooldown
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
        models.Job.status.in_(["queued", "running"]),
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
    if action == "start":
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
        else:
            placement = models.NodePlacement(
                lab_id=lab_id,
                node_name=node_name,
                host_id=agent.id,
                status="deployed",
            )
            session.add(placement)
            logger.info(f"Created placement for {node_name} on agent {agent.id}")

    # Create enforcement job
    job = models.Job(
        lab_id=lab_id,
        user_id=None,  # System-initiated
        action=f"node:{action}:{node_name}",
        status="queued",
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    logger.info(
        f"State enforcement: {action} node {node_name} in lab {lab_id} "
        f"(desired={desired}, actual={actual}, job={job.id})"
    )

    # Set cooldown before starting job
    _set_cooldown(lab_id, node_name)

    # Get lab provider
    from app.utils.lab import get_lab_provider
    provider = get_lab_provider(lab)

    # Start the job
    asyncio.create_task(run_agent_job(
        job.id, lab_id, f"node:{action}:{node_name}", provider=provider
    ))

    return True


async def enforce_lab_states():
    """Find and correct all state mismatches across labs.

    This is the main entry point called periodically by the monitor.
    """
    if not settings.state_enforcement_enabled:
        return

    session = SessionLocal()
    try:
        # Cleanup old cooldowns periodically
        _cleanup_old_cooldowns()

        # Find all node_states where desired != actual for running labs
        mismatched_states = (
            session.query(models.NodeState)
            .join(models.Lab, models.NodeState.lab_id == models.Lab.id)
            .filter(
                models.NodeState.desired_state != models.NodeState.actual_state,
                # Only consider labs that are in a stable state (not transitioning)
                models.Lab.state.in_(["running", "stopped", "error"]),
            )
            .all()
        )

        if not mismatched_states:
            return

        logger.debug(f"Found {len(mismatched_states)} nodes with state mismatches")

        # Process each mismatch
        enforced_count = 0
        for node_state in mismatched_states:
            lab = session.get(models.Lab, node_state.lab_id)
            if not lab:
                continue

            try:
                if await enforce_node_state(session, lab, node_state):
                    enforced_count += 1
            except Exception as e:
                logger.error(
                    f"Error enforcing state for {node_state.node_name} "
                    f"in lab {node_state.lab_id}: {e}"
                )

        if enforced_count > 0:
            logger.info(f"State enforcement triggered {enforced_count} corrective actions")

    except Exception as e:
        logger.error(f"Error in state enforcement: {e}")
    finally:
        session.close()


async def state_enforcement_monitor():
    """Background task to periodically enforce state.

    Runs every state_enforcement_interval seconds and triggers
    corrective actions for nodes where desired_state != actual_state.
    """
    logger.info(
        f"State enforcement monitor started "
        f"(enabled: {settings.state_enforcement_enabled}, "
        f"interval: {settings.state_enforcement_interval}s, "
        f"cooldown: {settings.state_enforcement_cooldown}s)"
    )

    while True:
        try:
            await asyncio.sleep(settings.state_enforcement_interval)
            await enforce_lab_states()
        except asyncio.CancelledError:
            logger.info("State enforcement monitor stopped")
            break
        except Exception as e:
            logger.error(f"Error in state enforcement monitor: {e}")
            # Continue running - don't let one error stop the monitor
