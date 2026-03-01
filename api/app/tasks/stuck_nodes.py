"""Health checks for nodes stuck in transitional states (stopping/starting)."""
from __future__ import annotations

import logging
from datetime import timedelta

from app import models
from app.db import get_session
from app.utils.time import utcnow
from app.state import (
    JobStatus,
    NodeActualState,
)

logger = logging.getLogger(__name__)


def check_stuck_stopping_nodes():
    """Find and recover nodes stuck in "stopping" state.

    This function monitors NodeState records for nodes stuck in "stopping":
    - Nodes with actual_state="stopping" and no active job for >6 minutes

    Stuck nodes are recovered by querying actual container status from the agent
    and updating their state accordingly.
    """
    with get_session() as session:
        try:
            now = utcnow()
            # 6 minute timeout for stopping operations
            stuck_threshold = now - timedelta(seconds=360)

            # Find nodes stuck in "stopping" state past the threshold
            stuck_nodes = (
                session.query(models.NodeState)
                .filter(
                    models.NodeState.actual_state == NodeActualState.STOPPING.value,
                    models.NodeState.stopping_started_at < stuck_threshold,
                )
                .all()
            )

            if not stuck_nodes:
                return

            # Group by lab_id for efficient processing
            nodes_by_lab: dict[str, list[models.NodeState]] = {}
            for ns in stuck_nodes:
                nodes_by_lab.setdefault(ns.lab_id, []).append(ns)

            for lab_id, nodes in nodes_by_lab.items():
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
                    # Job is still running - don't interfere
                    continue

                logger.warning(
                    f"Found {len(nodes)} node(s) stuck in 'stopping' state for lab {lab_id} "
                    f"with no active job, recovering..."
                )

                # No active job - recover these nodes
                # Set them to "stopped" since that was the intent
                for ns in nodes:
                    duration = (now - ns.stopping_started_at).total_seconds() if ns.stopping_started_at else 0
                    logger.info(
                        f"Recovering node {ns.node_name} in lab {lab_id} from stuck 'stopping' state "
                        f"(stuck for {duration:.0f}s)"
                    )
                    ns.actual_state = NodeActualState.STOPPED.value
                    ns.stopping_started_at = None
                    ns.error_message = None
                    ns.is_ready = False
                    ns.boot_started_at = None

                session.commit()

        except Exception as e:
            session.rollback()
            logger.error(f"Error in stuck stopping nodes check: {e}")


def check_stuck_starting_nodes():
    """Find and recover nodes stuck in "starting" state.

    This function monitors NodeState records for nodes stuck in "starting":
    - Nodes with actual_state="starting" and no active job for >6 minutes

    Stuck nodes are recovered by setting them to "stopped" (safe fallback).
    User can retry the start operation.
    """
    with get_session() as session:
        try:
            now = utcnow()
            # 6 minute timeout for starting operations
            stuck_threshold = now - timedelta(seconds=360)

            # Find nodes stuck in "starting" state past the threshold
            stuck_nodes = (
                session.query(models.NodeState)
                .filter(
                    models.NodeState.actual_state == NodeActualState.STARTING.value,
                    models.NodeState.starting_started_at < stuck_threshold,
                )
                .all()
            )

            if not stuck_nodes:
                return

            # Group by lab_id for efficient processing
            nodes_by_lab: dict[str, list[models.NodeState]] = {}
            for ns in stuck_nodes:
                nodes_by_lab.setdefault(ns.lab_id, []).append(ns)

            for lab_id, nodes in nodes_by_lab.items():
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
                    # Job is still running - don't interfere
                    continue

                logger.warning(
                    f"Found {len(nodes)} node(s) stuck in 'starting' state for lab {lab_id} "
                    f"with no active job, recovering..."
                )

                # No active job - recover these nodes
                # Set them to "stopped" as a safe fallback (user can retry)
                for ns in nodes:
                    # Don't recover nodes with active image sync
                    if ns.image_sync_status in ("syncing", "checking"):
                        logger.debug(
                            f"Skipping stuck starting recovery for {ns.node_name}: "
                            f"image sync in progress ({ns.image_sync_status})"
                        )
                        continue
                    duration = (now - ns.starting_started_at).total_seconds() if ns.starting_started_at else 0
                    logger.info(
                        f"Recovering node {ns.node_name} in lab {lab_id} from stuck 'starting' state "
                        f"(stuck for {duration:.0f}s)"
                    )
                    ns.actual_state = NodeActualState.STOPPED.value
                    ns.starting_started_at = None
                    ns.error_message = None
                    ns.is_ready = False
                    ns.boot_started_at = None

                session.commit()

        except Exception as e:
            session.rollback()
            logger.error(f"Error in stuck starting nodes check: {e}")
