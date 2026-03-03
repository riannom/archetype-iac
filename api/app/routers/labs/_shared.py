"""Shared utilities for the labs router package.

Functions here are imported by multiple sub-modules and/or by external modules
(e.g. labs_node_states, labs_configs, jobs, tests) that monkeypatch them via
``app.routers.labs.<name>``.
"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app import models, schemas
from app.config import settings
from app.jobs import has_conflicting_job as _has_conflicting_job
from app.state import NodeActualState, NodeDesiredState
from app.utils.async_tasks import safe_create_task
from app.utils.http import raise_not_found

def has_conflicting_job(*args, **kwargs):
    """Backward-compatible export for tests monkeypatching app.routers.labs."""
    return _has_conflicting_job(*args, **kwargs)


def get_config_by_device(device_id: str):
    """Backward-compatible export for tests monkeypatching app.routers.labs."""
    from agent.vendors import get_config_by_device as _get_config_by_device

    return _get_config_by_device(device_id)


def _zip_safe_name(value: str) -> str:
    """Prevent zip path traversal and invalid separators."""
    return (value or "unknown").replace("/", "_").replace("\\", "_")


def _enrich_node_state(state: models.NodeState) -> schemas.NodeStateOut:
    """Convert a NodeState model to schema with all_ips parsed from JSON."""
    node_data = schemas.NodeStateOut.model_validate(state)
    if state.management_ips_json:
        try:
            node_data.all_ips = json.loads(state.management_ips_json)
        except (json.JSONDecodeError, TypeError):
            node_data.all_ips = []
    # Compute will_retry: error state with retries remaining and not permanently failed
    node_data.will_retry = (
        state.actual_state == NodeActualState.ERROR
        and state.desired_state == NodeDesiredState.RUNNING
        and state.enforcement_attempts < settings.state_enforcement_max_retries
        and state.enforcement_failed_at is None
    )
    return node_data


def _get_or_create_node_state(
    database: Session,
    lab_id: str,
    node_id: str,
    initial_desired_state: str = NodeDesiredState.STOPPED,
    for_update: bool = False,
) -> models.NodeState:
    """Get or create NodeState, using Node definition for correct naming.

    This eliminates the node_name=node_id placeholder issue by looking up
    the Node definition to get the correct container_name.

    Args:
        database: Database session
        lab_id: Lab identifier
        node_id: Frontend GUI node ID
        initial_desired_state: Desired state for new records (default: "stopped")
        for_update: If True, acquire row-level lock (SELECT FOR UPDATE)

    Returns:
        Existing or newly created NodeState record
    """
    query = (
        database.query(models.NodeState)
        .filter(
            models.NodeState.lab_id == lab_id,
            models.NodeState.node_id == node_id,
        )
    )
    if for_update:
        query = query.with_for_update()
    state = query.first()
    if state:
        if not state.node_definition_id:
            node_def = (
                database.query(models.Node)
                .filter(models.Node.lab_id == lab_id, models.Node.gui_id == node_id)
                .first()
            )
            if node_def:
                state.node_definition_id = node_def.id
                if state.node_name != node_def.container_name:
                    state.node_name = node_def.container_name
                database.commit()
                database.refresh(state)
        return state

    # Look up Node definition to get correct container_name
    node_def = (
        database.query(models.Node)
        .filter(models.Node.lab_id == lab_id, models.Node.gui_id == node_id)
        .first()
    )
    if not node_def:
        raise_not_found(f"Node '{node_id}' not found in topology")

    state = models.NodeState(
        lab_id=lab_id,
        node_id=node_id,
        node_name=node_def.container_name,
        node_definition_id=node_def.id,
        desired_state=initial_desired_state,
        actual_state=NodeActualState.UNDEPLOYED,
    )
    database.add(state)
    database.commit()
    database.refresh(state)
    return state


def _create_node_sync_job(
    database: Session,
    lab: models.Lab,
    node_id: str,
    current_user: models.User,
) -> None:
    """Create a sync job for a single node and start the background task."""
    from app.tasks.jobs import run_node_reconcile
    from app.utils.lab import get_node_provider, get_lab_provider

    db_node = database.query(models.Node).filter(
        models.Node.lab_id == lab.id,
        models.Node.gui_id == node_id
    ).first()
    if db_node:
        provider = get_node_provider(db_node, database)
    else:
        provider = get_lab_provider(lab)

    from app.state import JobStatus

    job = models.Job(
        lab_id=lab.id,
        user_id=current_user.id,
        action=f"sync:node:{node_id}",
        status=JobStatus.QUEUED,
    )
    database.add(job)
    database.commit()
    database.refresh(job)

    safe_create_task(
        run_node_reconcile(job.id, lab.id, [node_id], provider=provider),
        name=f"sync:node:{job.id}"
    )


def _converge_stopped_error_state(state: models.NodeState) -> bool:
    """Force convergence when desired_state=stopped but actual_state=error."""
    if state.desired_state == NodeDesiredState.STOPPED and state.actual_state == NodeActualState.ERROR:
        state.actual_state = NodeActualState.STOPPED
        state.image_sync_status = None
        state.image_sync_message = None
        state.starting_started_at = None
        state.stopping_started_at = None
        state.boot_started_at = None
        state.is_ready = False
        state.reset_enforcement(clear_error=True)
        return True
    return False
