"""Live node management for real-time topology changes.

This module handles the creation and teardown of network nodes when the
topology is modified through the UI. It extends the live_links.py pattern
to nodes, enabling:

1. Immediate node deployment when a new node is added to the canvas
2. Immediate node destruction when a node is removed from the canvas
3. Queuing of operations when agents are unavailable
4. Debouncing of rapid canvas changes to batch operations

The main entry point is process_node_changes() which is called as a
background task from the update-topology endpoint. Changes are debounced
per lab to prevent firing separate operations for each rapid change.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Set
from uuid import uuid4

from sqlalchemy.orm import Session

from app import agent_client, models
from app.db import SessionLocal
from app.services.broadcaster import broadcast_node_state_change
from app.services.topology import TopologyService
from app.utils.async_tasks import safe_create_task
from app.tasks.jobs import run_node_reconcile

logger = logging.getLogger(__name__)


# Debounce delay in seconds (500ms default)
DEBOUNCE_DELAY = 0.5


class NodeChangeDebouncer:
    """Debounces rapid node changes per lab.

    Accumulates node additions and removals for a short delay before
    processing them as a batch. This prevents excessive API calls when
    a user makes multiple rapid changes to the canvas.
    """

    def __init__(self):
        # Pending changes per lab_id
        self._pending_adds: Dict[str, Set[str]] = defaultdict(set)
        self._pending_removes: Dict[str, List[dict]] = defaultdict(list)
        self._debounce_tasks: Dict[str, asyncio.Task] = {}
        self._locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def add_changes(
        self,
        lab_id: str,
        added_node_ids: List[str],
        removed_node_info: List[dict],
    ) -> None:
        """Queue changes for debounced processing.

        Args:
            lab_id: Lab identifier
            added_node_ids: Nodes that were added
            removed_node_info: Info about nodes that were removed
        """
        async with self._locks[lab_id]:
            # Accumulate changes
            for node_id in added_node_ids:
                self._pending_adds[lab_id].add(node_id)
            for info in removed_node_info:
                # Check if already in pending removes (by node_name)
                node_name = info.get("node_name", "")
                if not any(r.get("node_name") == node_name for r in self._pending_removes[lab_id]):
                    self._pending_removes[lab_id].append(info)

            # Cancel existing debounce task if any
            if lab_id in self._debounce_tasks:
                self._debounce_tasks[lab_id].cancel()
                try:
                    await self._debounce_tasks[lab_id]
                except asyncio.CancelledError:
                    pass

            # Start new debounce task
            self._debounce_tasks[lab_id] = asyncio.create_task(
                self._process_after_delay(lab_id)
            )

    async def _process_after_delay(self, lab_id: str) -> None:
        """Wait for debounce delay, then process accumulated changes."""
        try:
            await asyncio.sleep(DEBOUNCE_DELAY)

            async with self._locks[lab_id]:
                # Get and clear pending changes
                added = list(self._pending_adds.pop(lab_id, set()))
                removed = list(self._pending_removes.pop(lab_id, []))

                # Remove task reference
                self._debounce_tasks.pop(lab_id, None)

            # Process changes if any
            if added or removed:
                logger.info(
                    f"Processing debounced changes for lab {lab_id}: "
                    f"{len(added)} added, {len(removed)} removed"
                )
                await _process_node_changes_impl(lab_id, added, removed)

        except asyncio.CancelledError:
            logger.debug(f"Debounce cancelled for lab {lab_id}, changes merged")
            raise
        except Exception as e:
            logger.error(f"Error in debounced processing for lab {lab_id}: {e}")


# Global debouncer instance
_debouncer: NodeChangeDebouncer | None = None


def get_debouncer() -> NodeChangeDebouncer:
    """Get the singleton debouncer instance."""
    global _debouncer
    if _debouncer is None:
        _debouncer = NodeChangeDebouncer()
    return _debouncer


async def deploy_node_immediately(
    session: Session,
    lab_id: str,
    node_state: models.NodeState,
    lab: models.Lab,
) -> bool:
    """Deploy a single node immediately after it's added to the canvas.

    This is called when a new node is added to the topology and the user
    expects it to deploy automatically. It triggers a sync job for the node.

    Args:
        session: Database session
        lab_id: Lab identifier
        node_state: The NodeState record for the new node
        lab: Lab model instance

    Returns:
        True if deployment was triggered successfully, False otherwise
    """
    from app.utils.lab import get_lab_provider

    # Set node to pending state immediately
    node_state.desired_state = "running"
    node_state.actual_state = "pending"
    node_state.error_message = None
    session.commit()

    # Broadcast state change
    await broadcast_node_state_change(
        lab_id=lab_id,
        node_id=node_state.node_id,
        node_name=node_state.node_name,
        desired_state="running",
        actual_state="pending",
        is_ready=False,
    )

    # Get provider for the lab
    provider = get_lab_provider(lab)

    # Check if agent is available
    agent = await agent_client.get_agent_for_lab(session, lab, required_provider=provider)
    if not agent:
        logger.warning(f"No agent available for immediate deploy of {node_state.node_name}")
        node_state.actual_state = "pending"
        node_state.error_message = "Waiting for agent"
        session.commit()
        await broadcast_node_state_change(
            lab_id=lab_id,
            node_id=node_state.node_id,
            node_name=node_state.node_name,
            desired_state="running",
            actual_state="pending",
            is_ready=False,
            error_message="Waiting for agent",
        )
        return False

    # Create a sync job for this node
    job = models.Job(
        lab_id=lab_id,
        user_id=lab.owner_id,
        action=f"sync:node:{node_state.node_id}",
        status="queued",
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    logger.info(f"Triggered immediate deploy for node {node_state.node_name} (job {job.id})")

    # Run sync in background
    safe_create_task(
        run_node_reconcile(job.id, lab_id, [node_state.node_id], provider=provider),
        name=f"live:deploy:{job.id}"
    )

    return True


async def destroy_node_immediately(
    session: Session,
    lab_id: str,
    node_info: dict,
    host_to_agent: dict[str, models.Host],
) -> bool:
    """Destroy a single node immediately when removed from canvas.

    This is called when a node is removed from the topology. It destroys
    the container on the agent.

    Args:
        session: Database session
        lab_id: Lab identifier
        node_info: Dict with node details (node_id, node_name, host_id, etc.)
        host_to_agent: Map of host_id to Host objects

    Returns:
        True if destruction was successful, False otherwise
    """
    node_name = node_info.get("node_name", "")
    node_id = node_info.get("node_id", "")
    host_id = node_info.get("host_id")
    actual_state = node_info.get("actual_state", "unknown")

    if not node_name:
        logger.warning(f"Cannot destroy node without name: {node_info}")
        return False

    # Only destroy if node was actually deployed
    if actual_state in ("undeployed",):
        logger.debug(f"Node {node_name} was not deployed, skipping destroy")
        # Still need to clean up NodeState, NodePlacement records
        _cleanup_node_records(session, lab_id, node_name)
        return True

    # Find agent for this node
    agent = None
    if host_id and host_id in host_to_agent:
        agent = host_to_agent[host_id]
    else:
        # Try to find agent from any available
        for a in host_to_agent.values():
            if agent_client.is_agent_online(a):
                agent = a
                break

    if not agent:
        logger.warning(f"No agent available to destroy node {node_name}")
        return False

    # Call agent to destroy just this container
    try:
        result = await agent_client.destroy_container_on_agent(agent, lab_id, node_name)
        if result.get("success"):
            logger.info(f"Node {node_name} destroyed on agent {agent.name}")
            # Clean up database records
            _cleanup_node_records(session, lab_id, node_name)
            return True
        else:
            error = result.get("error", "Unknown error")
            logger.warning(f"Failed to destroy node {node_name}: {error}")
            return False
    except Exception as e:
        logger.error(f"Error destroying node {node_name}: {e}")
        return False


def _cleanup_node_records(session: Session, lab_id: str, node_name: str) -> None:
    """Clean up database records for a removed node.

    Deletes NodeState, NodePlacement, and related records.
    """
    # Delete NodeState
    session.query(models.NodeState).filter(
        models.NodeState.lab_id == lab_id,
        models.NodeState.node_name == node_name,
    ).delete()

    # Delete NodePlacement
    session.query(models.NodePlacement).filter(
        models.NodePlacement.lab_id == lab_id,
        models.NodePlacement.node_name == node_name,
    ).delete()

    session.commit()
    logger.debug(f"Cleaned up records for node {node_name} in lab {lab_id}")


async def process_node_changes(
    lab_id: str,
    added_node_ids: list[str],
    removed_node_info: list[dict],
) -> None:
    """Background task to process node additions/removals from update-topology.

    This function queues changes for debounced processing. Multiple rapid
    changes to the same lab will be batched together and processed after
    a short delay (500ms by default).

    Args:
        lab_id: Lab identifier
        added_node_ids: List of node IDs that were added
        removed_node_info: List of dicts with info about removed nodes
    """
    debouncer = get_debouncer()
    await debouncer.add_changes(lab_id, added_node_ids, removed_node_info)


async def _process_node_changes_impl(
    lab_id: str,
    added_node_ids: list[str],
    removed_node_info: list[dict],
) -> None:
    """Implementation of node change processing after debouncing.

    This function handles the actual deployment and destruction of nodes.
    It's called by the debouncer after collecting all pending changes.

    Args:
        lab_id: Lab identifier
        added_node_ids: List of node IDs that were added
        removed_node_info: List of dicts with info about removed nodes
    """
    session = SessionLocal()
    try:
        lab = session.get(models.Lab, lab_id)
        if not lab:
            logger.error(f"Lab {lab_id} not found for live node changes")
            return

        # Build host_to_agent mapping
        host_to_agent = await _build_host_to_agent_map(session, lab_id, lab)

        # Process removed nodes first (teardown)
        for node_info in removed_node_info:
            try:
                await destroy_node_immediately(session, lab_id, node_info, host_to_agent)
            except Exception as e:
                logger.error(f"Error destroying node {node_info.get('node_name')}: {e}")

        # Only auto-deploy new nodes if the lab is in a running state
        # (meaning there are already deployed nodes and user expects live updates)
        if lab.state in ("running", "starting"):
            for node_id in added_node_ids:
                try:
                    # Find the NodeState for this node
                    node_state = (
                        session.query(models.NodeState)
                        .filter(
                            models.NodeState.lab_id == lab_id,
                            models.NodeState.node_id == node_id,
                        )
                        .first()
                    )
                    if node_state and node_state.actual_state in ("undeployed", "stopped"):
                        await deploy_node_immediately(session, lab_id, node_state, lab)
                except Exception as e:
                    logger.error(f"Error deploying node {node_id}: {e}")
        else:
            logger.debug(
                f"Lab {lab_id} is in state '{lab.state}', skipping auto-deploy for new nodes"
            )

    except Exception as e:
        logger.error(f"Error processing node changes for lab {lab_id}: {e}")
        session.rollback()
    finally:
        session.close()


async def _build_host_to_agent_map(
    session: Session,
    lab_id: str,
    lab: models.Lab,
) -> dict[str, models.Host]:
    """Build a mapping of host_id to Host objects for the lab.

    Returns:
        Dict mapping host_id to Host object
    """
    host_to_agent: dict[str, models.Host] = {}

    # Get agents from NodePlacement records
    placements = (
        session.query(models.NodePlacement)
        .filter(models.NodePlacement.lab_id == lab_id)
        .all()
    )
    host_ids = {p.host_id for p in placements}

    # Also include lab's default agent
    if lab.agent_id:
        host_ids.add(lab.agent_id)

    # Load all agents
    for host_id in host_ids:
        agent = session.get(models.Host, host_id)
        if agent and agent_client.is_agent_online(agent):
            host_to_agent[host_id] = agent

    return host_to_agent
