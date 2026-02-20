"""Event receiving endpoints for real-time state updates.

This module provides endpoints for agents to push state change events
to the controller, enabling real-time state synchronization without polling.

Events are processed immediately and NodeState records are updated
to reflect the actual container/VM state. State changes are broadcast
to connected WebSocket clients for real-time UI updates.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import db, models, schemas
from app.agent_auth import verify_agent_secret
from app.services.broadcaster import broadcast_node_state_change

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/events", tags=["events"])


def _find_lab_by_prefix(database: Session, lab_prefix: str) -> models.Lab | None:
    """Find a lab by its ID prefix.

    Lab IDs may be truncated to ~20 chars in container labels, so we need to find
    labs that start with the given prefix.

    Args:
        database: Database session
        lab_prefix: Lab ID prefix from container labels

    Returns:
        Lab if found, None otherwise
    """
    if not lab_prefix:
        return None

    # Try exact match first
    lab = database.query(models.Lab).filter(models.Lab.id == lab_prefix).first()
    if lab:
        return lab

    # Try prefix match (lab IDs may be truncated to ~20 chars)
    labs = database.query(models.Lab).filter(
        models.Lab.id.startswith(lab_prefix)
    ).all()

    if len(labs) == 1:
        return labs[0]
    elif len(labs) > 1:
        # Multiple matches - try to find the best one
        # Prefer exact length match
        for lab in labs:
            if len(lab.id) == len(lab_prefix):
                return lab
        # Just return the first one
        return labs[0]

    return None


def _find_node_state(
    database: Session, lab_id: str, node_name: str
) -> models.NodeState | None:
    """Find a NodeState record by lab ID and node name.

    Args:
        database: Database session
        lab_id: Lab ID
        node_name: Node name from container labels

    Returns:
        NodeState if found, None otherwise
    """
    return (
        database.query(models.NodeState)
        .filter(
            models.NodeState.lab_id == lab_id,
            models.NodeState.node_name == node_name,
        )
        .first()
    )


def _event_type_to_actual_state(
    event_type: str, status: str, current_state: str | None = None
) -> str:
    """Map event type to NodeState.actual_state value.

    Args:
        event_type: Event type from agent (started, stopped, died, etc.)
        status: Status string with additional details (e.g., "exited (code 137)")
        current_state: Current actual_state, used to prevent invalid transitions

    Returns:
        Appropriate actual_state value, or empty string to skip update
    """
    if event_type == "started":
        return "running"
    elif event_type in ("stopped", "stop"):
        return "stopped"
    elif event_type in ("died", "kill", "oom"):
        # Exit code 137 = SIGKILL (128 + 9), typically from docker stop
        # Exit code 143 = SIGTERM (128 + 15), also from docker stop
        # These are intentional stops, not errors
        if "code 137" in status or "code 143" in status:
            return "stopped"
        # Status "kill" means Docker sent SIGKILL - this is from docker stop timeout
        # Treat it as a successful stop, not an error
        if status == "kill":
            return "stopped"
        # Don't downgrade from "stopped" to "error" - this can happen when
        # events arrive out of order (die after stop)
        if current_state == "stopped":
            return ""  # Skip update
        # If we were already stopping, treat any death as a successful stop
        if current_state == "stopping":
            return "stopped"
        return "error"
    elif event_type == "creating":
        return "pending"
    elif event_type == "destroying":
        return "stopped"
    else:
        # Unknown event type - don't change state
        return ""


@router.post("/node", response_model=schemas.NodeEventResponse)
async def receive_node_event(
    payload: schemas.NodeEventPayload,
    database: Session = Depends(db.get_db),
    _auth: None = Depends(verify_agent_secret),
) -> schemas.NodeEventResponse:
    """Receive a node state change event from an agent.

    This endpoint processes real-time state updates from agents,
    updating NodeState records to match actual container/VM state.

    Events are processed immediately - no authentication required
    as this is internal agent-to-controller communication.
    """
    logger.debug(
        f"Received node event: {payload.event_type} for "
        f"{payload.node_name} in lab {payload.lab_id}"
    )

    # Find the lab by prefix
    lab = _find_lab_by_prefix(database, payload.lab_id)
    if not lab:
        # Lab not found - might be a stale container
        logger.debug(f"Lab not found for prefix: {payload.lab_id}")
        return schemas.NodeEventResponse(
            success=True,
            message="Lab not found (ignored)",
        )

    # Find the NodeState record
    node_state = _find_node_state(database, lab.id, payload.node_name)
    if not node_state:
        # NodeState not found - this can happen if topology was changed
        logger.debug(
            f"NodeState not found for {payload.node_name} in lab {lab.id}"
        )
        return schemas.NodeEventResponse(
            success=True,
            message="NodeState not found (ignored)",
        )

    # Update the NodeState
    old_state = node_state.actual_state

    # Map event type to actual_state, considering current state
    new_state = _event_type_to_actual_state(
        payload.event_type, payload.status, current_state=old_state
    )
    if not new_state:
        logger.debug(f"Event type {payload.event_type} ignored (no state change)")
        return schemas.NodeEventResponse(
            success=True,
            message="Event ignored (no state change)",
        )

    node_state.actual_state = new_state

    # Clear transitional timestamps when reaching final states
    if new_state in ("stopped", "running", "error"):
        node_state.stopping_started_at = None
        node_state.starting_started_at = None

    # Set or clear error message
    if new_state == "error":
        node_state.error_message = f"Container {payload.event_type}: {payload.status}"
    else:
        node_state.error_message = None

    # Log significant state changes
    if old_state != new_state:
        logger.info(
            f"Node {payload.node_name} in lab {lab.id}: "
            f"{old_state} -> {new_state} (event: {payload.event_type})"
        )

    database.commit()

    # Broadcast state change to WebSocket clients
    if old_state != new_state:
        asyncio.create_task(
            broadcast_node_state_change(
                lab_id=lab.id,
                node_id=node_state.node_id,
                node_name=node_state.node_name,
                desired_state=node_state.desired_state,
                actual_state=new_state,
                is_ready=node_state.is_ready,
                error_message=node_state.error_message,
            )
        )

    return schemas.NodeEventResponse(
        success=True,
        message=f"Updated {payload.node_name}: {old_state} -> {new_state}",
    )


@router.post("/batch", response_model=schemas.NodeEventResponse)
def receive_batch_events(
    events: list[schemas.NodeEventPayload],
    database: Session = Depends(db.get_db),
    _auth: None = Depends(verify_agent_secret),
) -> schemas.NodeEventResponse:
    """Receive multiple node events in a single request.

    For efficiency, agents can batch multiple events together.
    Events are processed in order.
    """
    if not events:
        return schemas.NodeEventResponse(success=True, message="No events to process")

    processed = 0
    errors = 0

    for payload in events:
        try:
            # Find the lab
            lab = _find_lab_by_prefix(database, payload.lab_id)
            if not lab:
                continue

            # Find the NodeState
            node_state = _find_node_state(database, lab.id, payload.node_name)
            if not node_state:
                continue

            # Map and update state, considering current state
            old_state = node_state.actual_state
            new_state = _event_type_to_actual_state(
                payload.event_type, payload.status, current_state=old_state
            )
            if not new_state:
                continue

            node_state.actual_state = new_state

            if new_state == "error":
                node_state.error_message = f"Container {payload.event_type}: {payload.status}"
            else:
                node_state.error_message = None

            if old_state != new_state:
                logger.info(
                    f"Node {payload.node_name} in lab {lab.id}: "
                    f"{old_state} -> {new_state}"
                )

            processed += 1

        except Exception as e:
            logger.error(f"Error processing event: {e}")
            errors += 1

    database.commit()

    return schemas.NodeEventResponse(
        success=True,
        message=f"Processed {processed} events ({errors} errors)",
    )
