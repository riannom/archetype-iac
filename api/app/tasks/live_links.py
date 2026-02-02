"""Live link management for real-time topology changes.

This module handles the creation and teardown of network links when the
topology is modified through the UI while nodes are running. It enables:

1. Immediate link creation when both endpoint nodes are running
2. Queuing of links when endpoint nodes are not yet running (auto-connect
   when both become running via reconciliation)
3. Immediate link teardown when links are removed from the topology

The main entry point is process_link_changes() which is called as a
background task from the import-graph endpoint.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app import agent_client, models
from app.db import SessionLocal
from app.services.link_manager import LinkManager, allocate_vni
from app.tasks.link_orchestration import (
    create_same_host_link,
    create_cross_host_link,
)

logger = logging.getLogger(__name__)


async def create_link_if_ready(
    session: Session,
    lab_id: str,
    link_state: models.LinkState,
    host_to_agent: dict[str, models.Host],
) -> bool:
    """Create link if both endpoint nodes are running.

    This function checks if both nodes at the link's endpoints are in the
    "running" state. If so, it creates the network connection using either
    same-host (OVS hot-connect) or cross-host (VXLAN tunnel) methods.

    Args:
        session: Database session
        lab_id: Lab identifier
        link_state: The LinkState record to potentially connect
        host_to_agent: Map of host_id to Host objects for available agents

    Returns:
        True if link was created successfully, False otherwise
    """
    # Check NodeState.actual_state for both endpoints
    source_state = (
        session.query(models.NodeState)
        .filter(
            models.NodeState.lab_id == lab_id,
            models.NodeState.node_name == link_state.source_node,
        )
        .first()
    )
    target_state = (
        session.query(models.NodeState)
        .filter(
            models.NodeState.lab_id == lab_id,
            models.NodeState.node_name == link_state.target_node,
        )
        .first()
    )

    # Check if both nodes are running
    source_running = source_state and source_state.actual_state == "running"
    target_running = target_state and target_state.actual_state == "running"

    if not source_running or not target_running:
        # One or both nodes not running - mark link as pending for later auto-connect
        link_state.actual_state = "pending"
        link_state.error_message = None
        logger.info(
            f"Link {link_state.link_name} queued - waiting for nodes "
            f"(source={source_state.actual_state if source_state else 'unknown'}, "
            f"target={target_state.actual_state if target_state else 'unknown'})"
        )
        return False

    # Both nodes are running - determine host placement
    source_host_id, target_host_id = _lookup_endpoint_hosts(session, link_state)

    if not source_host_id or not target_host_id:
        link_state.actual_state = "error"
        link_state.error_message = "Cannot determine endpoint host placement"
        logger.warning(f"Link {link_state.link_name} missing host placement")
        return False

    # Store host IDs in link_state
    link_state.source_host_id = source_host_id
    link_state.target_host_id = target_host_id

    # Check if this is a same-host or cross-host link
    is_cross_host = source_host_id != target_host_id
    link_state.is_cross_host = is_cross_host

    # Create log_parts for the link creation functions
    log_parts: list[str] = []

    if is_cross_host:
        success = await create_cross_host_link(
            session, lab_id, link_state, host_to_agent, log_parts
        )
    else:
        success = await create_same_host_link(
            session, lab_id, link_state, host_to_agent, log_parts
        )

    if success:
        logger.info(f"Live link created: {link_state.link_name}")
    else:
        logger.warning(
            f"Live link creation failed: {link_state.link_name} - "
            f"{link_state.error_message}"
        )

    return success


async def teardown_link(
    session: Session,
    lab_id: str,
    link_info: dict,
    host_to_agent: dict[str, models.Host],
) -> bool:
    """Tear down an existing link.

    For same-host links, calls the agent to delete the link (which assigns
    unique VLAN tags to isolate the interfaces).

    For cross-host links, cleans up the VXLAN tunnel infrastructure and
    removes the VxlanTunnel record.

    Args:
        session: Database session
        lab_id: Lab identifier
        link_info: Dict with link details from the removed LinkState
        host_to_agent: Map of host_id to Host objects

    Returns:
        True if teardown was successful, False otherwise
    """
    link_name = link_info.get("link_name", "unknown")
    is_cross_host = link_info.get("is_cross_host", False)
    actual_state = link_info.get("actual_state", "unknown")

    # Only tear down if link was actually up
    if actual_state not in ("up", "error", "pending"):
        logger.debug(f"Link {link_name} was not active, skipping teardown")
        return True

    source_host_id = link_info.get("source_host_id")
    target_host_id = link_info.get("target_host_id")

    if is_cross_host:
        # Clean up VXLAN tunnel on both agents
        success = True

        # Clean up on source host
        if source_host_id:
            agent = host_to_agent.get(source_host_id)
            if agent:
                try:
                    await agent_client.cleanup_overlay_on_agent(agent, lab_id)
                except Exception as e:
                    logger.warning(f"Overlay cleanup on source agent failed: {e}")
                    success = False

        # Clean up on target host
        if target_host_id and target_host_id != source_host_id:
            agent = host_to_agent.get(target_host_id)
            if agent:
                try:
                    await agent_client.cleanup_overlay_on_agent(agent, lab_id)
                except Exception as e:
                    logger.warning(f"Overlay cleanup on target agent failed: {e}")
                    success = False

        # Delete VxlanTunnel record if exists
        # We need the link_state_id but we only have link_info
        # Since the LinkState is about to be deleted, query VxlanTunnel by other means
        tunnels = (
            session.query(models.VxlanTunnel)
            .filter(models.VxlanTunnel.lab_id == lab_id)
            .all()
        )
        for tunnel in tunnels:
            # Check if this tunnel belongs to the removed link by checking
            # if its link_state no longer exists or is marked for deletion
            ls = session.get(models.LinkState, tunnel.link_state_id)
            if ls and ls.link_name == link_name:
                session.delete(tunnel)

        if success:
            logger.info(f"Cross-host link {link_name} torn down")
        return success
    else:
        # Same-host link - call agent to delete it
        host_id = source_host_id or target_host_id
        if not host_id:
            logger.warning(f"No host ID for same-host link {link_name}")
            return True  # Nothing to clean up

        agent = host_to_agent.get(host_id)
        if not agent:
            logger.warning(f"Agent not available for link {link_name}")
            return True  # Can't clean up if agent is unavailable

        try:
            result = await agent_client.delete_link_on_agent(agent, lab_id, link_name)
            if result.get("success"):
                logger.info(f"Same-host link {link_name} torn down")
                return True
            else:
                logger.warning(
                    f"Same-host link {link_name} teardown failed: {result.get('error')}"
                )
                return False
        except Exception as e:
            logger.error(f"Failed to tear down link {link_name}: {e}")
            return False


async def process_link_changes(
    lab_id: str,
    added_link_names: list[str],
    removed_link_info: list[dict],
) -> None:
    """Background task to process link additions/removals from import-graph.

    This function is called as a background task when the topology is modified.
    It handles:
    1. Creating new links if both endpoint nodes are running
    2. Tearing down removed links and cleaning up their network resources

    Args:
        lab_id: Lab identifier
        added_link_names: List of link names that were added
        removed_link_info: List of dicts with info about removed links
    """
    session = SessionLocal()
    try:
        # Build host_to_agent mapping
        host_to_agent = await _build_host_to_agent_map(session, lab_id)

        if not host_to_agent:
            logger.warning(f"No agents available for lab {lab_id}, skipping live link operations")
            return

        # Process removed links first (teardown)
        for link_info in removed_link_info:
            try:
                await teardown_link(session, lab_id, link_info, host_to_agent)
            except Exception as e:
                logger.error(f"Error tearing down link {link_info.get('link_name')}: {e}")

        # Now delete the LinkState records for removed links
        for link_info in removed_link_info:
            link_name = link_info.get("link_name")
            if link_name:
                ls = (
                    session.query(models.LinkState)
                    .filter(
                        models.LinkState.lab_id == lab_id,
                        models.LinkState.link_name == link_name,
                    )
                    .first()
                )
                if ls:
                    session.delete(ls)

        # Process added links
        for link_name in added_link_names:
            link_state = (
                session.query(models.LinkState)
                .filter(
                    models.LinkState.lab_id == lab_id,
                    models.LinkState.link_name == link_name,
                )
                .first()
            )
            if link_state:
                try:
                    await create_link_if_ready(session, lab_id, link_state, host_to_agent)
                except Exception as e:
                    logger.error(f"Error creating link {link_name}: {e}")
                    link_state.actual_state = "error"
                    link_state.error_message = str(e)

        session.commit()

    except Exception as e:
        logger.error(f"Error processing link changes for lab {lab_id}: {e}")
        session.rollback()
    finally:
        session.close()


def _lookup_endpoint_hosts(
    session: Session,
    link_state: models.LinkState,
) -> tuple[str | None, str | None]:
    """Look up which hosts have the source and target nodes.

    First checks Node.host_id (explicit placement), then NodePlacement
    (runtime placement tracking).

    Returns:
        Tuple of (source_host_id, target_host_id)
    """
    lab_id = link_state.lab_id

    source_host_id = None
    target_host_id = None

    # Check Node.host_id first (explicit placement)
    source_node = (
        session.query(models.Node)
        .filter(
            models.Node.lab_id == lab_id,
            models.Node.container_name == link_state.source_node,
        )
        .first()
    )
    if source_node and source_node.host_id:
        source_host_id = source_node.host_id

    target_node = (
        session.query(models.Node)
        .filter(
            models.Node.lab_id == lab_id,
            models.Node.container_name == link_state.target_node,
        )
        .first()
    )
    if target_node and target_node.host_id:
        target_host_id = target_node.host_id

    # Fall back to NodePlacement
    if not source_host_id:
        placement = (
            session.query(models.NodePlacement)
            .filter(
                models.NodePlacement.lab_id == lab_id,
                models.NodePlacement.node_name == link_state.source_node,
            )
            .first()
        )
        if placement:
            source_host_id = placement.host_id

    if not target_host_id:
        placement = (
            session.query(models.NodePlacement)
            .filter(
                models.NodePlacement.lab_id == lab_id,
                models.NodePlacement.node_name == link_state.target_node,
            )
            .first()
        )
        if placement:
            target_host_id = placement.host_id

    return source_host_id, target_host_id


async def _build_host_to_agent_map(
    session: Session,
    lab_id: str,
) -> dict[str, models.Host]:
    """Build a mapping of host_id to Host objects for the lab.

    This includes all agents that have nodes deployed for this lab,
    plus the lab's default agent if set.

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
    lab = session.get(models.Lab, lab_id)
    if lab and lab.agent_id:
        host_ids.add(lab.agent_id)

    # Load all agents
    for host_id in host_ids:
        agent = session.get(models.Host, host_id)
        if agent and agent_client.is_agent_online(agent):
            host_to_agent[host_id] = agent

    return host_to_agent
