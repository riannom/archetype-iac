"""Live link management for real-time topology changes.

This module handles the creation and teardown of network links when the
topology is modified through the UI while nodes are running. It enables:

1. Immediate link creation when both endpoint nodes are running
2. Queuing of links when endpoint nodes are not yet running (auto-connect
   when both become running via reconciliation)
3. Immediate link teardown when links are removed from the topology

The main entry point is process_link_changes() which is called as a
background task from the update-topology endpoint.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app import agent_client, models
from app.db import get_session
from app.utils.link import lookup_endpoint_hosts
from app.tasks.link_orchestration import (
    create_same_host_link,
    create_cross_host_link,
)
from app.services.link_operational_state import recompute_link_oper_state
from app.services.interface_naming import normalize_for_node
from app.services.link_reservations import (
    claim_link_endpoints,
    release_link_endpoint_reservations,
)
from app.utils.locks import (
    link_ops_lock,
    get_link_state_for_update,
)

logger = logging.getLogger(__name__)


def _sync_oper_state(session: Session, link_state: models.LinkState) -> None:
    recompute_link_oper_state(session, link_state)


def _update_job_log(session: Session, job: models.Job, log_parts: list[str]) -> None:
    """Update job log with current log_parts content."""
    job.log_path = "\n".join(log_parts)
    session.commit()


async def create_link_if_ready(
    session: Session,
    lab_id: str,
    link_state: models.LinkState,
    host_to_agent: dict[str, models.Host],
    log_parts: list[str] | None = None,
    skip_locked: bool = False,
) -> bool:
    """Create link if both endpoint nodes are running.

    This function checks if both nodes at the link's endpoints are in the
    "running" state. If so, it creates the network connection using either
    same-host (OVS hot-connect) or cross-host (VXLAN tunnel) methods.

    Uses row-level locking to prevent concurrent modifications to the
    same LinkState record.

    Args:
        session: Database session
        lab_id: Lab identifier
        link_state: The LinkState record to potentially connect
        host_to_agent: Map of host_id to Host objects for available agents
        log_parts: Optional list to append log messages to
        skip_locked: If True, silently skip rows locked by other transactions

    Returns:
        True if link was created successfully, False otherwise
    """
    if log_parts is None:
        log_parts = []

    # Re-query with row-level lock to prevent concurrent modifications
    link_name = link_state.link_name
    link_state = get_link_state_for_update(session, lab_id, link_name, skip_locked=skip_locked)
    if not link_state:
        if skip_locked:
            logger.debug(f"Link {link_name} skipped (row locked by another transaction)")
        else:
            log_parts.append(f"  {link_name}: FAILED - link state not found")
        return False

    claimed, conflicts = claim_link_endpoints(session, link_state)
    if not claimed:
        link_state.actual_state = "error"
        conflict_list = ", ".join(conflicts) if conflicts else "another link"
        link_state.error_message = (
            f"Endpoint already in use by desired-up link(s): {conflict_list}"
        )
        _sync_oper_state(session, link_state)
        log_parts.append(
            f"  {link_state.link_name}: FAILED - {link_state.error_message}"
        )
        logger.warning(
            "Link %s rejected due to endpoint reservation conflict with %s",
            link_state.link_name,
            conflict_list,
        )
        return False

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
        _sync_oper_state(session, link_state)
        src_status = source_state.actual_state if source_state else "unknown"
        tgt_status = target_state.actual_state if target_state else "unknown"
        log_parts.append(
            f"  {link_state.link_name}: PENDING - waiting for nodes "
            f"(source={src_status}, target={tgt_status})"
        )
        logger.info(
            f"Link {link_state.link_name} queued - waiting for nodes "
            f"(source={src_status}, target={tgt_status})"
        )
        return False

    # Both nodes are running - determine host placement
    source_host_id, target_host_id = lookup_endpoint_hosts(session, link_state)

    if not source_host_id or not target_host_id:
        link_state.actual_state = "error"
        link_state.error_message = "Cannot determine endpoint host placement"
        _sync_oper_state(session, link_state)
        log_parts.append(f"  {link_state.link_name}: FAILED - missing host placement")
        logger.warning(f"Link {link_state.link_name} missing host placement")
        return False

    # Store host IDs in link_state
    link_state.source_host_id = source_host_id
    link_state.target_host_id = target_host_id

    # Check if this is a same-host or cross-host link
    is_cross_host = source_host_id != target_host_id
    link_state.is_cross_host = is_cross_host

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
    log_parts: list[str] | None = None,
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
        log_parts: Optional list to append log messages to

    Returns:
        True if teardown was successful, False otherwise
    """
    if log_parts is None:
        log_parts = []

    link_name = link_info.get("link_name", "unknown")
    is_cross_host = link_info.get("is_cross_host", False)
    actual_state = link_info.get("actual_state", "unknown")

    # Only tear down if link was actually up
    if actual_state not in ("up", "error", "pending"):
        log_parts.append(f"  {link_name}: skipped (was {actual_state})")
        logger.debug(f"Link {link_name} was not active, skipping teardown")
        return True

    source_host_id = link_info.get("source_host_id")
    target_host_id = link_info.get("target_host_id")
    link_state_record = (
        session.query(models.LinkState)
        .filter(
            models.LinkState.lab_id == lab_id,
            models.LinkState.link_name == link_name,
        )
        .first()
    )

    if is_cross_host:
        # Two-phase teardown with rollback for cross-host links
        source_agent = host_to_agent.get(source_host_id) if source_host_id else None
        target_agent = host_to_agent.get(target_host_id) if target_host_id else None

        if not source_agent or not target_agent:
            log_parts.append(f"  {link_name}: skipped (agents unavailable)")
            logger.warning(f"Agents not available for cross-host link {link_name}")
            return True  # Can't clean up without both agents

        # Get link details for the detach operation
        source_node = link_info.get("source_node", "")
        target_node = link_info.get("target_node", "")
        source_iface = normalize_for_node(session, lab_id, source_node, link_info.get("source_interface", "") or "")
        target_iface = normalize_for_node(session, lab_id, target_node, link_info.get("target_interface", "") or "")

        # Get agent IPs for rollback
        source_agent_ip = await agent_client.resolve_agent_ip(source_agent.address)
        target_agent_ip = await agent_client.resolve_agent_ip(target_agent.address)

        # Find and mark tunnel as cleanup
        tunnel = None
        ls = (
            link_state_record
        )
        if ls:
            tunnel = (
                session.query(models.VxlanTunnel)
                .filter(models.VxlanTunnel.link_state_id == ls.id)
                .first()
            )
            if tunnel:
                tunnel.status = "cleanup"
                session.flush()

        # Phase 2a: Detach source interface
        source_ok = False
        try:
            result_a = await agent_client.detach_overlay_interface_on_agent(
                source_agent,
                lab_id=lab_id,
                container_name=source_node,
                interface_name=source_iface,
                link_id=link_name,
            )
            source_ok = result_a.get("success", False)
            if not source_ok:
                logger.warning(f"Detach source failed: {result_a.get('error')}")
        except Exception as e:
            logger.warning(f"Overlay detach on source agent failed: {e}")

        if not source_ok:
            if tunnel:
                tunnel.status = "failed"
            if link_state_record:
                link_state_record.actual_state = "error"
                link_state_record.error_message = "Source detach failed"
                _sync_oper_state(session, link_state_record)
            log_parts.append(f"  {link_name}: FAILED (source detach error)")
            return False

        # Phase 2b: Detach target interface
        target_ok = False
        try:
            result_b = await agent_client.detach_overlay_interface_on_agent(
                target_agent,
                lab_id=lab_id,
                container_name=target_node,
                interface_name=target_iface,
                link_id=link_name,
            )
            target_ok = result_b.get("success", False)
            if not target_ok:
                logger.warning(f"Detach target failed: {result_b.get('error')}")
        except Exception as e:
            logger.warning(f"Overlay detach on target agent failed: {e}")

        if not target_ok:
            # Rollback: Re-attach source interface using per-link VNI model
            logger.warning(f"Target detach failed, rolling back source for {link_name}")
            rollback_vni = tunnel.vni if tunnel else None
            if rollback_vni:
                try:
                    await agent_client.attach_overlay_interface_on_agent(
                        source_agent,
                        lab_id=lab_id,
                        container_name=source_node,
                        interface_name=source_iface,
                        vni=rollback_vni,
                        local_ip=source_agent_ip,
                        remote_ip=target_agent_ip,
                        link_id=link_name,
                    )
                    logger.info(f"Rolled back source attachment for {link_name}")
                except Exception as e:
                    logger.error(f"Rollback failed for {link_name}: {e}")

            if tunnel:
                tunnel.status = "failed"
            if link_state_record:
                link_state_record.actual_state = "error"
                link_state_record.error_message = "Target detach failed after source detach"
                _sync_oper_state(session, link_state_record)
            log_parts.append(f"  {link_name}: FAILED (target detach error, source rolled back)")
            return False

        # Phase 3: Both sides detached - delete tunnel record
        if tunnel:
            session.delete(tunnel)
        if link_state_record:
            link_state_record.actual_state = "down"
            link_state_record.source_carrier_state = "off"
            link_state_record.target_carrier_state = "off"
            link_state_record.source_vxlan_attached = False
            link_state_record.target_vxlan_attached = False
            link_state_record.error_message = None
            _sync_oper_state(session, link_state_record)

        log_parts.append(f"  {link_name}: removed (cross-host VXLAN)")
        logger.info(f"Cross-host link {link_name} torn down")
        return True
    else:
        # Same-host link - call agent to delete it
        host_id = source_host_id or target_host_id
        if not host_id:
            log_parts.append(f"  {link_name}: skipped (no host ID)")
            logger.warning(f"No host ID for same-host link {link_name}")
            return True  # Nothing to clean up

        agent = host_to_agent.get(host_id)
        if not agent:
            log_parts.append(f"  {link_name}: skipped (agent unavailable)")
            logger.warning(f"Agent not available for link {link_name}")
            return True  # Can't clean up if agent is unavailable

        source_node = link_info.get("source_node", "")
        target_node = link_info.get("target_node", "")
        source_iface = normalize_for_node(session, lab_id, source_node, link_info.get("source_interface", "") or "")
        target_iface = normalize_for_node(session, lab_id, target_node, link_info.get("target_interface", "") or "")
        normalized_link_id = link_name
        if source_node and target_node and source_iface and target_iface:
            normalized_link_id = f"{source_node}:{source_iface}-{target_node}:{target_iface}"

        try:
            result = await agent_client.delete_link_on_agent(agent, lab_id, normalized_link_id)
            if result.get("success"):
                if link_state_record:
                    link_state_record.actual_state = "down"
                    link_state_record.source_carrier_state = "off"
                    link_state_record.target_carrier_state = "off"
                    link_state_record.error_message = None
                    _sync_oper_state(session, link_state_record)
                log_parts.append(f"  {link_name}: removed")
                logger.info(f"Same-host link {link_name} torn down")
                return True
            else:
                error = result.get("error", "unknown error")
                if link_state_record:
                    link_state_record.actual_state = "error"
                    link_state_record.error_message = error
                    _sync_oper_state(session, link_state_record)
                log_parts.append(f"  {link_name}: FAILED - {error}")
                logger.warning(
                    f"Same-host link {link_name} teardown failed: {error}"
                )
                return False
        except Exception as e:
            if link_state_record:
                link_state_record.actual_state = "error"
                link_state_record.error_message = str(e)
                _sync_oper_state(session, link_state_record)
            log_parts.append(f"  {link_name}: FAILED - {e}")
            logger.error(f"Failed to tear down link {link_name}: {e}")
            return False


async def process_link_changes(
    lab_id: str,
    added_link_names: list[str],
    removed_link_info: list[dict],
    user_id: str | None = None,
) -> None:
    """Background task to process link additions/removals from update-topology.

    This function is called as a background task when the topology is modified.
    It handles:
    1. Creating new links if both endpoint nodes are running
    2. Tearing down removed links and cleaning up their network resources

    Uses a distributed Redis lock to prevent conflicts with reconciliation.

    Args:
        lab_id: Lab identifier
        added_link_names: List of link names that were added
        removed_link_info: List of dicts with info about removed links
        user_id: User who triggered the change (for job tracking)
    """
    job = None
    log_parts: list[str] = []

    # Acquire distributed lock to prevent conflicts with reconciliation
    with link_ops_lock(lab_id) as lock_acquired:
        if not lock_acquired:
            logger.warning(
                f"Could not acquire link ops lock for lab {lab_id}, "
                f"skipping link changes (will retry via reconciliation)"
            )
            return

        with get_session() as session:
            try:
                # Create a job to track this operation
                add_count = len(added_link_names)
                remove_count = len(removed_link_info)
                action_desc = []
                if add_count > 0:
                    action_desc.append(f"add:{add_count}")
                if remove_count > 0:
                    action_desc.append(f"remove:{remove_count}")

                job = models.Job(
                    lab_id=lab_id,
                    user_id=user_id,
                    action=f"links:{','.join(action_desc)}",
                    status="running",
                    started_at=datetime.now(timezone.utc),
                )
                session.add(job)
                session.commit()
                session.refresh(job)

                log_parts.append("=== Live Link Update ===")
                log_parts.append(f"Lab: {lab_id}")
                log_parts.append(f"Links to add: {add_count}")
                log_parts.append(f"Links to remove: {remove_count}")
                log_parts.append("")

                # Build host_to_agent mapping
                host_to_agent = await _build_host_to_agent_map(session, lab_id)

                if not host_to_agent:
                    log_parts.append("WARNING: No agents available, skipping link operations")
                    logger.warning(f"No agents available for lab {lab_id}, skipping live link operations")
                    job.status = "completed"
                    job.completed_at = datetime.now(timezone.utc)
                    _update_job_log(session, job, log_parts)
                    return

                error_count = 0

                # Process removed links first (teardown)
                if removed_link_info:
                    log_parts.append("=== Removing Links ===")
                    for link_info in removed_link_info:
                        try:
                            success = await teardown_link(session, lab_id, link_info, host_to_agent, log_parts)
                            if not success:
                                error_count += 1
                        except Exception as e:
                            error_count += 1
                            log_parts.append(f"  {link_info.get('link_name')}: FAILED - {e}")
                            logger.error(f"Error tearing down link {link_info.get('link_name')}: {e}")
                    log_parts.append("")

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
                            release_link_endpoint_reservations(session, ls.id)
                            session.delete(ls)

                # Process added links
                if added_link_names:
                    log_parts.append("=== Adding Links ===")
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
                                success = await create_link_if_ready(
                                    session, lab_id, link_state, host_to_agent, log_parts
                                )
                                if not success and link_state.actual_state == "error":
                                    error_count += 1
                            except Exception as e:
                                error_count += 1
                                log_parts.append(f"  {link_name}: FAILED - {e}")
                                logger.error(f"Error creating link {link_name}: {e}")
                                link_state.actual_state = "error"
                                link_state.error_message = str(e)
                                _sync_oper_state(session, link_state)
                    log_parts.append("")

                # Summary
                log_parts.append("=== Summary ===")
                if error_count > 0:
                    log_parts.append(f"Completed with {error_count} error(s)")
                    job.status = "completed"  # Still completed, just with errors
                else:
                    log_parts.append("All link operations completed successfully")
                    job.status = "completed"

                job.completed_at = datetime.now(timezone.utc)
                _update_job_log(session, job, log_parts)
                session.commit()

            except Exception as e:
                logger.error(f"Error processing link changes for lab {lab_id}: {e}")
                log_parts.append(f"\nFATAL ERROR: {e}")
                if job:
                    job.status = "failed"
                    job.completed_at = datetime.now(timezone.utc)
                    _update_job_log(session, job, log_parts)
                session.commit()  # Commit the job status update


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
