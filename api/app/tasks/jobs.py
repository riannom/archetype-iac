"""Background job execution functions.

Job Status Values:
    - queued: Job is waiting to be processed
    - running: Job is currently executing
    - completed: Job finished successfully
    - completed_with_warnings: Job finished but with partial failures (e.g., some
      agents were offline during multi-host destroy). The primary operation
      succeeded but cleanup may be incomplete.
    - failed: Job encountered an error and could not complete
    - cancelled: Job was cancelled by user
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone

import redis

from app import agent_client, models, webhooks
from app.agent_client import AgentJobError, AgentUnavailableError
from app.config import settings
from app.db import get_redis, get_session
from app.services.broadcaster import broadcast_node_state_change, get_broadcaster
from app.services.topology import TopologyService, graph_to_deploy_topology
from app.utils.lab import update_lab_state
from app.utils.async_tasks import safe_create_task

logger = logging.getLogger(__name__)


def acquire_deploy_lock(lab_id: str, node_names: list[str], agent_id: str, timeout: int = 300) -> tuple[bool, list[str]]:
    """Acquire distributed locks for deploying specific nodes.

    Prevents concurrent jobs from deploying the same nodes to different agents.
    Each node gets its own lock key to allow parallel deploys of different nodes.

    Args:
        lab_id: Lab identifier
        node_names: List of node names to lock
        agent_id: Agent ID requesting the lock (stored in lock value for debugging)
        timeout: Lock TTL in seconds (default 5 minutes for deploy operations)

    Returns:
        Tuple of (success, locked_nodes). If success is False, locked_nodes contains
        the nodes that couldn't be locked (held by another agent).
    """
    r = get_redis()
    locked = []
    failed = []

    try:
        for node_name in node_names:
            lock_key = f"deploy_lock:{lab_id}:{node_name}"
            lock_value = f"agent:{agent_id}:time:{datetime.now(timezone.utc).isoformat()}"

            # Try to acquire lock
            lock_acquired = r.set(lock_key, lock_value, nx=True, ex=timeout)
            if lock_acquired:
                locked.append(node_name)
            else:
                # Check who holds the lock
                holder = r.get(lock_key)
                holder_str = holder.decode() if holder else "unknown"
                logger.warning(
                    f"Deploy lock for {node_name} in lab {lab_id} held by {holder_str}, "
                    f"requested by agent {agent_id}"
                )
                failed.append(node_name)

        if failed:
            # Release any locks we acquired
            for node_name in locked:
                try:
                    r.delete(f"deploy_lock:{lab_id}:{node_name}")
                except redis.RedisError:
                    pass
            return False, failed

        return True, locked

    except redis.RedisError as e:
        logger.warning(f"Redis error acquiring deploy lock for lab {lab_id}: {e}")
        # On Redis error, proceed without lock (better than blocking deploy)
        return True, node_names


def release_deploy_lock(lab_id: str, node_names: list[str]) -> None:
    """Release deploy locks for nodes.

    Args:
        lab_id: Lab identifier
        node_names: List of node names to unlock
    """
    r = get_redis()
    for node_name in node_names:
        try:
            r.delete(f"deploy_lock:{lab_id}:{node_name}")
        except redis.RedisError:
            pass  # Lock will auto-expire via TTL


async def _auto_extract_configs_before_destroy(
    session,
    lab: models.Lab,
    agent: models.Host,
) -> None:
    """Auto-extract configs from running nodes before destroy.

    This is called before destroying a lab to preserve running configs.
    Creates snapshots with type 'auto_stop' to distinguish from manual extracts.

    For multi-host labs, this queries all agents that have nodes for the lab
    and extracts configs from each agent concurrently.

    Args:
        session: Database session
        lab: Lab being destroyed
        agent: Agent running the lab (used as fallback if no placements exist)
    """
    if not settings.feature_auto_extract_on_destroy:
        return

    try:
        logger.info(f"Auto-extracting configs before destroy for lab {lab.id}")

        # Get all agents that have nodes for this lab (multi-host support)
        placements = (
            session.query(models.NodePlacement.host_id)
            .filter(models.NodePlacement.lab_id == lab.id)
            .distinct()
            .all()
        )
        host_ids = [p.host_id for p in placements]

        # Fall back to provided agent if no placements exist
        if not host_ids:
            host_ids = [agent.id]

        # Get all healthy agents
        agents = []
        for host_id in host_ids:
            host = session.get(models.Host, host_id)
            if host and agent_client.is_agent_online(host):
                agents.append(host)

        if not agents:
            logger.warning(f"No healthy agents available for auto-extract on lab {lab.id}")
            return

        # Call all agents concurrently to extract configs
        tasks = [agent_client.extract_configs_on_agent(a, lab.id) for a in agents]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Merge configs from all agents
        configs = []
        for a, result in zip(agents, results):
            if isinstance(result, Exception):
                logger.warning(f"Auto-extract failed on agent {a.id}: {result}")
                continue
            if not result.get("success"):
                logger.warning(
                    f"Auto-extract failed on agent {a.id}: {result.get('error', 'Unknown')}"
                )
                continue
            configs.extend(result.get("configs", []))

        if not configs:
            logger.debug(f"No configs extracted before destroy for lab {lab.id}")
            return

        # Create snapshots with auto_stop type
        import hashlib

        snapshots_created = 0
        for config_data in configs:
            node_name = config_data.get("node_name")
            content = config_data.get("content")
            if not node_name or not content:
                continue

            content_hash = hashlib.sha256(content.encode()).hexdigest()[:12]

            # Check for duplicate - skip if content hash matches most recent snapshot
            latest_snapshot = (
                session.query(models.ConfigSnapshot)
                .filter(
                    models.ConfigSnapshot.lab_id == lab.id,
                    models.ConfigSnapshot.node_name == node_name,
                )
                .order_by(models.ConfigSnapshot.created_at.desc())
                .first()
            )

            if latest_snapshot and latest_snapshot.content_hash == content_hash:
                # Content unchanged, skip creating duplicate
                continue

            # Create new snapshot
            snapshot = models.ConfigSnapshot(
                lab_id=lab.id,
                node_name=node_name,
                content=content,
                content_hash=content_hash,
                snapshot_type="auto_stop",
            )
            session.add(snapshot)
            snapshots_created += 1

        session.commit()
        logger.info(
            f"Auto-extracted {len(configs)} configs, "
            f"created {snapshots_created} snapshots before destroy for lab {lab.id}"
        )

    except Exception as e:
        logger.warning(f"Error during auto-extract before destroy: {e}")
        # Continue with destroy even if extraction fails


async def _broadcast_job_progress(
    lab_id: str,
    job_id: str,
    action: str,
    status: str,
    progress_message: str | None = None,
    error_message: str | None = None,
) -> None:
    """Broadcast job progress update via WebSocket.

    Fire-and-forget wrapper that catches exceptions to avoid disrupting job execution.
    """
    try:
        broadcaster = get_broadcaster()
        await broadcaster.publish_job_progress(
            lab_id=lab_id,
            job_id=job_id,
            action=action,
            status=status,
            progress_message=progress_message,
            error_message=error_message,
        )
    except Exception as e:
        logger.debug(f"Failed to broadcast job progress: {e}")


def _get_node_info_for_webhook(session, lab_id: str) -> list[dict]:
    """Get node info for webhook payload."""
    nodes = (
        session.query(models.NodeState)
        .filter(models.NodeState.lab_id == lab_id)
        .all()
    )
    return [
        {
            "name": n.node_name,
            "state": n.actual_state,
            "ready": n.is_ready,
            "management_ip": n.management_ip,
        }
        for n in nodes
    ]


async def _dispatch_webhook(
    event_type: str,
    lab: models.Lab,
    job: models.Job,
    session,
) -> None:
    """Dispatch a webhook event (fire and forget)."""
    try:
        nodes = _get_node_info_for_webhook(session, lab.id)
        await webhooks.dispatch_webhook_event(
            event_type=event_type,
            lab_id=lab.id,
            lab=lab,
            job=job,
            nodes=nodes,
        )
    except Exception as e:
        # Don't fail the job if webhook dispatch fails
        logger.warning(f"Webhook dispatch failed for {event_type}: {e}")


async def _capture_node_ips(session, lab_id: str, agent: models.Host) -> None:
    """Capture management IPs from agent and persist to NodeState records.

    This is called after a successful deploy to capture the container IPs
    assigned by docker for use in IaC workflows.
    """
    try:
        status = await agent_client.get_lab_status_from_agent(agent, lab_id)
        nodes = status.get("nodes", [])

        if not nodes:
            logger.debug(f"No nodes returned in status for lab {lab_id}")
            return

        # Update NodeState records with IP addresses
        for node_info in nodes:
            node_name = node_info.get("name")
            ip_addresses = node_info.get("ip_addresses", [])

            if not node_name:
                continue

            # Find the NodeState record
            node_state = (
                session.query(models.NodeState)
                .filter(
                    models.NodeState.lab_id == lab_id,
                    models.NodeState.node_name == node_name,
                )
                .first()
            )

            if node_state and ip_addresses:
                # Set primary IP (first in list)
                node_state.management_ip = ip_addresses[0] if ip_addresses else None
                # Store all IPs as JSON
                node_state.management_ips_json = json.dumps(ip_addresses)
                logger.debug(f"Captured IPs for {node_name}: {ip_addresses}")

        session.commit()
        logger.info(f"Captured management IPs for {len(nodes)} nodes in lab {lab_id}")

    except Exception as e:
        logger.warning(f"Failed to capture node IPs for lab {lab_id}: {e}")
        # Don't fail the job - IP capture is best-effort


async def _update_node_placements(
    session,
    lab_id: str,
    agent_id: str,
    node_names: list[str],
    status: str = "deployed",
) -> None:
    """Update NodePlacement records for tracking node-to-agent affinity.

    Called early with status="starting" when agent is selected, then
    again with status="deployed" after successful deploy. This ensures
    the UI shows the correct target host during transitional states.

    Args:
        session: Database session
        lab_id: Lab identifier
        agent_id: Agent that will run/is running the nodes
        node_names: List of node names
        status: Placement status ("starting", "deployed", etc.)
    """
    try:
        for node_name in node_names:
            # Look up node definition for FK
            node_def = (
                session.query(models.Node)
                .filter(
                    models.Node.lab_id == lab_id,
                    models.Node.container_name == node_name,
                )
                .first()
            )

            # Check for existing placement
            existing = (
                session.query(models.NodePlacement)
                .filter(
                    models.NodePlacement.lab_id == lab_id,
                    models.NodePlacement.node_name == node_name,
                )
                .first()
            )

            if existing:
                # Update existing placement
                existing.host_id = agent_id
                existing.status = status
                # Backfill node_definition_id if missing
                if node_def and not existing.node_definition_id:
                    existing.node_definition_id = node_def.id
            else:
                # Create new placement with FK
                placement = models.NodePlacement(
                    lab_id=lab_id,
                    node_name=node_name,
                    node_definition_id=node_def.id if node_def else None,
                    host_id=agent_id,
                    status=status,
                )
                session.add(placement)

        session.commit()
        logger.info(f"Updated placements for {len(node_names)} nodes in lab {lab_id} on agent {agent_id}")

    except Exception as e:
        logger.warning(f"Failed to update node placements for lab {lab_id}: {e}")
        # Don't fail the job - placement tracking is best-effort


async def _cleanup_orphan_containers(
    session,
    lab_id: str,
    new_agent_id: str,
    old_agent_ids: set[str],
    log_parts: list[str],
) -> None:
    """Clean up orphan containers on agents that no longer run this lab.

    When a deploy moves to a new agent, containers may be left behind on
    the old agent. This function destroys those orphaned containers.

    Args:
        session: Database session
        lab_id: Lab identifier
        new_agent_id: Agent that now runs the lab
        old_agent_ids: Set of agent IDs that previously had nodes
        log_parts: List to append log messages to
    """
    try:
        for old_agent_id in old_agent_ids:
            if old_agent_id == new_agent_id:
                continue  # Skip the agent we just deployed to

            old_agent = session.get(models.Host, old_agent_id)
            if not old_agent:
                continue

            # Check if agent is online before attempting cleanup
            if not agent_client.is_agent_online(old_agent):
                logger.info(f"Skipping orphan cleanup on offline agent {old_agent_id}")
                log_parts.append(f"Note: Skipped cleanup on offline agent {old_agent.name}")
                continue

            logger.info(f"Cleaning up orphan containers for lab {lab_id} on old agent {old_agent_id}")
            log_parts.append(f"Cleaning up orphans on old agent {old_agent.name}...")

            result = await agent_client.destroy_lab_on_agent(old_agent, lab_id)

            if result.get("status") == "completed":
                log_parts.append(f"  Orphan cleanup succeeded on {old_agent.name}")
                # Remove old placements for this agent
                session.query(models.NodePlacement).filter(
                    models.NodePlacement.lab_id == lab_id,
                    models.NodePlacement.host_id == old_agent_id,
                ).delete()
                session.commit()
            else:
                error = result.get("error", "Unknown error")
                log_parts.append(f"  Orphan cleanup failed on {old_agent.name}: {error}")
                logger.warning(f"Orphan cleanup failed on agent {old_agent_id}: {error}")

    except Exception as e:
        logger.warning(f"Error during orphan cleanup for lab {lab_id}: {e}")
        log_parts.append(f"Warning: Orphan cleanup error: {e}")


async def run_agent_job(
    job_id: str,
    lab_id: str,
    action: str,
    node_name: str | None = None,
    provider: str = "docker",
):
    """Run a job on an agent in the background.

    Handles errors gracefully and provides detailed error messages.
    Updates lab state based on job outcome.

    For deploy actions, topology is built from the database (source of truth).

    Args:
        job_id: The job ID
        lab_id: The lab ID
        action: Action to perform (up, down, node:start:name, etc.)
        node_name: Node name for node actions
        provider: Provider for the job (default: docker)
    """
    with get_session() as session:
        try:
            job = session.get(models.Job, job_id)
            if not job:
                logger.error(f"Job {job_id} not found in database")
                return

            lab = session.get(models.Lab, lab_id)
            if not lab:
                logger.error(f"Lab {lab_id} not found in database")
                job.status = "failed"
                job.completed_at = datetime.now(timezone.utc)
                job.log_path = f"ERROR: Lab {lab_id} not found"
                session.commit()
                return

            # Find a healthy agent with required capability
            # For node-specific actions, use get_agent_for_node() with full priority chain
            # For lab-wide actions, use get_agent_for_lab() (NodePlacement affinity)
            agent = None
            if action.startswith("node:"):
                # Parse node name from action: "node:start:nodename"
                parts = action.split(":", 2)
                target_node_name = parts[2] if len(parts) > 2 else None
                if target_node_name:
                    # Use unified get_agent_for_node() with consistent priority:
                    # Node.host_id → NodePlacement → lab.agent_id → any healthy
                    agent = await agent_client.get_agent_for_node(
                        session,
                        lab_id,
                        target_node_name,
                        required_provider=provider,
                    )

            # For lab-wide actions or if node-specific lookup failed, use lab affinity
            if not agent:
                agent = await agent_client.get_agent_for_lab(
                    session,
                    lab,
                    required_provider=provider,
                )
            if not agent:
                job.status = "failed"
                job.completed_at = datetime.now(timezone.utc)
                job.log_path = (
                    f"ERROR: No healthy agent available.\n\n"
                    f"Required provider: {provider}\n\n"
                    f"Possible causes:\n"
                    f"- No agents are registered\n"
                    f"- All agents are offline or unresponsive\n"
                    f"- No agent supports the required provider\n"
                    f"- All capable agents are at capacity\n\n"
                    f"Check agent status and connectivity."
                )
                update_lab_state(session, lab_id, "error", error="No healthy agent available")
                session.commit()
                logger.warning(f"Job {job_id} failed: no healthy agent available for provider {provider}")
                return

            # Update job with agent assignment and start time
            job.status = "running"
            job.agent_id = agent.id
            job.started_at = datetime.now(timezone.utc)
            session.commit()

            # Broadcast job started
            await _broadcast_job_progress(
                lab_id, job_id, action, "running",
                progress_message=f"Job started on agent {agent.name or agent.id}"
            )

            # Update lab state based on action
            if action == "up":
                update_lab_state(session, lab_id, "starting", agent_id=agent.id)
                # Dispatch webhook for deploy started
                await _dispatch_webhook("lab.deploy_started", lab, job, session)
            elif action == "down":
                update_lab_state(session, lab_id, "stopping", agent_id=agent.id)

            logger.info(f"Job {job_id} started: {action} on lab {lab_id} via agent {agent.id}")

            try:
                if action == "up":
                    # Build JSON topology from database (source of truth)
                    topo_service = TopologyService(session)
                    topology_json = topo_service.build_deploy_topology(lab_id, agent.id)
                    result = await agent_client.deploy_to_agent(
                        agent, job_id, lab_id,
                        topology=topology_json,  # Use JSON, not YAML
                        provider=provider,
                    )
                elif action == "down":
                    # Auto-extract configs before destroying (if enabled)
                    await _auto_extract_configs_before_destroy(session, lab, agent)
                    result = await agent_client.destroy_on_agent(agent, job_id, lab_id)
                else:
                    # Note: node:start/stop actions are deprecated - use sync:node:{id} instead
                    result = {"status": "failed", "error_message": f"Unknown action: {action}"}

                # Update job based on result
                job.completed_at = datetime.now(timezone.utc)

                if result.get("status") == "completed":
                    job.status = "completed"
                    log_content = f"Job completed successfully.\n\n"

                    # Broadcast job completed
                    await _broadcast_job_progress(
                        lab_id, job_id, action, "completed",
                        progress_message="Job completed successfully"
                    )

                    # Update lab state based on completed action
                    if action == "up":
                        update_lab_state(session, lab_id, "running", agent_id=agent.id)
                        # Capture management IPs for IaC workflows
                        await _capture_node_ips(session, lab_id, agent)
                        # Dispatch webhook for successful deploy
                        await _dispatch_webhook("lab.deploy_complete", lab, job, session)
                    elif action == "down":
                        update_lab_state(session, lab_id, "stopped")
                        # Dispatch webhook for destroy complete
                        await _dispatch_webhook("lab.destroy_complete", lab, job, session)

                else:
                    job.status = "failed"
                    error_msg = result.get('error_message', 'Unknown error')
                    log_content = f"Job failed.\n\nError: {error_msg}\n\n"

                    # Broadcast job failed
                    await _broadcast_job_progress(
                        lab_id, job_id, action, "failed",
                        error_message=error_msg
                    )

                    # Update lab state to error
                    update_lab_state(session, lab_id, "error", error=error_msg)

                    # Dispatch webhook for failed job
                    if action == "up":
                        await _dispatch_webhook("lab.deploy_failed", lab, job, session)
                    else:
                        await _dispatch_webhook("job.failed", lab, job, session)

                # Append stdout/stderr if present
                stdout = result.get("stdout", "").strip()
                stderr = result.get("stderr", "").strip()
                if stdout:
                    log_content += f"=== STDOUT ===\n{stdout}\n\n"
                if stderr:
                    log_content += f"=== STDERR ===\n{stderr}\n"

                job.log_path = log_content.strip()
                session.commit()
                logger.info(f"Job {job_id} completed with status: {job.status}")

            except AgentUnavailableError as e:
                job.status = "failed"
                job.completed_at = datetime.now(timezone.utc)
                job.log_path = (
                    f"ERROR: Agent became unavailable during job execution.\n\n"
                    f"Agent ID: {e.agent_id or 'unknown'}\n"
                    f"Details: {e.message}\n\n"
                    f"The job could not be completed. The lab may be in an inconsistent state.\n"
                    f"Consider checking the lab status and retrying the operation."
                )

                # Update lab state to unknown (we don't know what state it's in)
                update_lab_state(session, lab_id, "unknown", error=f"Agent unavailable: {e.message}")

                session.commit()
                logger.error(f"Job {job_id} failed: agent unavailable - {e.message}")

                # Mark agent as offline if we know which one failed
                if e.agent_id:
                    await agent_client.mark_agent_offline(session, e.agent_id)

            except AgentJobError as e:
                job.status = "failed"
                job.completed_at = datetime.now(timezone.utc)
                log_content = f"ERROR: Job execution failed on agent.\n\nDetails: {e.message}\n\n"
                if e.stdout:
                    log_content += f"=== STDOUT ===\n{e.stdout}\n\n"
                if e.stderr:
                    log_content += f"=== STDERR ===\n{e.stderr}\n"
                job.log_path = log_content.strip()

                # Update lab state to error
                update_lab_state(session, lab_id, "error", error=e.message)

                session.commit()
                logger.error(f"Job {job_id} failed: agent job error - {e.message}")

            except Exception as e:
                job.status = "failed"
                job.completed_at = datetime.now(timezone.utc)
                job.log_path = (
                    f"ERROR: Unexpected error during job execution.\n\n"
                    f"Type: {type(e).__name__}\n"
                    f"Details: {str(e)}\n\n"
                    f"Please report this error if it persists."
                )

                # Update lab state to error
                update_lab_state(session, lab_id, "error", error=str(e))

                session.commit()
                logger.exception(f"Job {job_id} failed with unexpected error: {e}")

        except Exception as e:
            # Catch-all for any errors during error handling itself
            logger.exception(f"Critical error in job {job_id}: {e}")


async def run_multihost_deploy(
    job_id: str,
    lab_id: str,
    provider: str = "docker",
):
    """Deploy a lab across multiple hosts.

    This function uses the database `nodes.host_id` as the authoritative source
    for host assignments.

    Steps:
    1. Analyze placements using TopologyService (reads from database)
    2. Build JSON topology for each host (filtered by nodes.host_id)
    3. Deploy to each agent in parallel using structured JSON format
    4. Set up VXLAN overlay links for cross-host connections

    Args:
        job_id: The job ID
        lab_id: The lab ID
        provider: Provider for the job
    """
    with get_session() as session:
        try:
            job = session.get(models.Job, job_id)
            if not job:
                logger.error(f"Job {job_id} not found in database")
                return

            lab = session.get(models.Lab, lab_id)
            if not lab:
                logger.error(f"Lab {lab_id} not found in database")
                job.status = "failed"
                job.completed_at = datetime.now(timezone.utc)
                job.log_path = f"ERROR: Lab {lab_id} not found"
                session.commit()
                return

            # Use TopologyService to analyze placements from DATABASE (not YAML)
            # This is the key fix: nodes.host_id is the source of truth
            topo_service = TopologyService(session)
            nodes = topo_service.get_nodes(lab_id)
            total_node_count = len(nodes)

            # Find nodes without host assignment
            unplaced_nodes = [n for n in nodes if not n.host_id]

            # If some nodes lack host_id, assign them a default agent
            if unplaced_nodes:
                default_agent = await agent_client.get_agent_for_lab(
                    session, lab, required_provider=provider
                )
                if default_agent:
                    # Update nodes in database with default host
                    for node in unplaced_nodes:
                        node.host_id = default_agent.id
                    session.commit()
                    logger.info(
                        f"Lab {lab_id} has {len(unplaced_nodes)} nodes without "
                        f"explicit placement, assigned to {default_agent.name}"
                    )
                else:
                    # No default agent available
                    job.status = "failed"
                    job.completed_at = datetime.now(timezone.utc)
                    job.log_path = (
                        f"ERROR: {len(unplaced_nodes)} nodes have no host assignment "
                        f"and no default agent is available"
                    )
                    update_lab_state(session, lab_id, "error", error="No agent for unplaced nodes")
                    session.commit()
                    return

            # Analyze placements from database
            analysis = topo_service.analyze_placements(lab_id)

            logger.info(
                f"Multi-host deployment for lab {lab_id}: "
                f"{len(analysis.placements)} hosts, "
                f"{len(analysis.cross_host_links)} cross-host links"
            )

            # Update job status
            job.status = "running"
            job.started_at = datetime.now(timezone.utc)
            session.commit()

            # Broadcast job started
            await _broadcast_job_progress(
                lab_id, job_id, "up", "running",
                progress_message=f"Starting multi-host deployment ({len(analysis.placements)} hosts)"
            )

            update_lab_state(session, lab_id, "starting")

            # Dispatch webhook for deploy started
            await _dispatch_webhook("lab.deploy_started", lab, job, session)

            # Map host_id to agent objects
            host_to_agent: dict[str, models.Host] = {}
            missing_hosts = []

            for host_id in analysis.placements:
                agent = session.get(models.Host, host_id)
                if agent and agent_client.is_agent_online(agent):
                    host_to_agent[host_id] = agent
                else:
                    missing_hosts.append(host_id)

            if missing_hosts:
                error_msg = f"Missing or unhealthy agents for hosts: {', '.join(missing_hosts)}"
                job.status = "failed"
                job.completed_at = datetime.now(timezone.utc)
                job.log_path = f"ERROR: {error_msg}"
                update_lab_state(session, lab_id, "error", error=error_msg)
                session.commit()
                logger.error(f"Job {job_id} failed: {error_msg}")
                return

            # Deploy to each host in parallel using JSON topology from database
            deploy_tasks = []
            deploy_results: dict[str, dict] = {}
            log_parts = []
            host_node_names: dict[str, list[str]] = {}  # For logging

            for host_id, node_placements in analysis.placements.items():
                agent = host_to_agent[host_id]

                # Build JSON topology for this host from database
                topology_json = topo_service.build_deploy_topology(lab_id, host_id)
                node_names = [n["name"] for n in topology_json.get("nodes", [])]
                host_node_names[host_id] = node_names

                logger.info(
                    f"Deploying to host {agent.name} ({host_id}): "
                    f"{len(node_names)} nodes"
                )
                log_parts.append(f"=== Host: {agent.name} ({host_id}) ===")
                log_parts.append(f"Nodes: {', '.join(node_names)}")

                # Use JSON topology format
                deploy_tasks.append(
                    agent_client.deploy_to_agent(
                        agent, job_id, lab_id,
                        topology=topology_json,  # New: structured JSON
                    )
                )

            # Wait for all deployments
            results = await asyncio.gather(*deploy_tasks, return_exceptions=True)

            deploy_success = True
            for host_id, result in zip(analysis.placements.keys(), results):
                agent = host_to_agent[host_id]
                if isinstance(result, Exception):
                    log_parts.append(f"\nDeploy to {agent.name} FAILED: {result}")
                    deploy_success = False
                else:
                    deploy_results[host_id] = result
                    status = result.get("status", "unknown")
                    log_parts.append(f"\nDeploy to {agent.name}: {status}")
                    if result.get("stdout"):
                        log_parts.append(f"STDOUT:\n{result['stdout']}")
                    if result.get("stderr"):
                        log_parts.append(f"STDERR:\n{result['stderr']}")
                    if status != "completed":
                        deploy_success = False

            if not deploy_success:
                # Rollback: destroy containers on hosts that succeeded to prevent orphans
                logger.warning(f"Multi-host deploy partially failed for lab {lab_id}, initiating rollback")
                log_parts.append("\n=== Rollback: Cleaning up partially deployed hosts ===")

                rollback_tasks = []
                rollback_hosts = []
                for host_id, result in zip(analysis.placements.keys(), results):
                    # Only rollback hosts where deploy succeeded
                    if not isinstance(result, Exception) and result.get("status") == "completed":
                        agent = host_to_agent.get(host_id)
                        if agent:
                            rollback_tasks.append(
                                agent_client.destroy_on_agent(agent, job_id, lab_id)
                            )
                            rollback_hosts.append(agent.name)

                if rollback_tasks:
                    log_parts.append(f"Rolling back hosts: {', '.join(rollback_hosts)}")
                    rollback_results = await asyncio.gather(*rollback_tasks, return_exceptions=True)

                    for agent_name, rb_result in zip(rollback_hosts, rollback_results):
                        if isinstance(rb_result, Exception):
                            log_parts.append(f"  {agent_name}: rollback FAILED - {rb_result}")
                        else:
                            status = rb_result.get("status", "unknown")
                            log_parts.append(f"  {agent_name}: rollback {status}")
                else:
                    log_parts.append("No hosts to rollback (all failed)")

                job.status = "failed"
                job.completed_at = datetime.now(timezone.utc)
                job.log_path = "\n".join(log_parts)
                update_lab_state(session, lab_id, "error", error="Deployment failed on one or more hosts")
                session.commit()
                logger.error(f"Job {job_id} failed: deployment error on one or more hosts (rollback completed)")
                return

            # Create all links (same-host via OVS hot_connect, cross-host via VXLAN)
            # This handles both link types and creates/updates LinkState records
            from app.tasks.link_orchestration import create_deployment_links

            links_ok, links_failed = await create_deployment_links(
                session, lab_id, host_to_agent, log_parts
            )

            # Fail the job if any links failed
            if links_failed > 0:
                log_parts.append(f"\n=== Link Setup Summary ===")
                log_parts.append(f"Links: {links_ok} OK, {links_failed} failed")
                log_parts.append("\nNote: Containers are deployed but some links failed.")
                job.status = "failed"
                job.completed_at = datetime.now(timezone.utc)
                job.log_path = "\n".join(log_parts)
                update_lab_state(session, lab_id, "error", error=f"Link setup failed: {links_failed} link(s)")
                session.commit()
                logger.error(f"Job {job_id} failed: {links_failed} link(s) failed")
                return

            # Update NodePlacement records for each host
            # This ensures placement tracking matches actual deployment
            for host_id, agent in host_to_agent.items():
                node_names = host_node_names.get(host_id, [])
                if node_names:
                    await _update_node_placements(session, lab_id, agent.id, node_names)

            # Mark job as completed
            job.status = "completed"
            job.completed_at = datetime.now(timezone.utc)
            job.log_path = "\n".join(log_parts)

            # Broadcast job completed
            await _broadcast_job_progress(
                lab_id, job_id, "up", "completed",
                progress_message="Multi-host deployment completed successfully"
            )

            # Update lab state - use first agent as primary
            first_agent = list(host_to_agent.values())[0] if host_to_agent else None
            update_lab_state(
                session, lab_id, "running",
                agent_id=first_agent.id if first_agent else None
            )

            # Capture management IPs from all agents for IaC workflows
            for agent in host_to_agent.values():
                await _capture_node_ips(session, lab_id, agent)

            session.commit()

            # Dispatch webhook for successful deploy
            await _dispatch_webhook("lab.deploy_complete", lab, job, session)

            logger.info(f"Job {job_id} completed: multi-host deployment successful")

        except Exception as e:
            logger.exception(f"Job {job_id} failed with unexpected error: {e}")
            try:
                job = session.get(models.Job, job_id)
                lab = session.get(models.Lab, lab_id)
                if job:
                    job.status = "failed"
                    job.completed_at = datetime.now(timezone.utc)
                    job.log_path = f"ERROR: Unexpected error: {e}"
                    update_lab_state(session, lab_id, "error", error=str(e))
                    session.commit()
                    # Dispatch webhook for failed deploy
                    if lab:
                        await _dispatch_webhook("lab.deploy_failed", lab, job, session)
            except Exception as inner_e:
                logger.exception(f"Critical error handling job {job_id} failure: {inner_e}")


async def run_multihost_destroy(
    job_id: str,
    lab_id: str,
    provider: str = "docker",
):
    """Destroy a multi-host lab.

    This function uses database `nodes.host_id` as the authoritative source
    for host assignments, matching the approach in run_multihost_deploy.

    Steps:
    1. Analyze placements from database (not YAML)
    2. Clean up overlay networks on each agent
    3. Destroy containers on each agent

    Args:
        job_id: The job ID
        lab_id: The lab ID
        provider: Provider for the job
    """
    with get_session() as session:
        try:
            job = session.get(models.Job, job_id)
            if not job:
                logger.error(f"Job {job_id} not found in database")
                return

            lab = session.get(models.Lab, lab_id)
            if not lab:
                logger.error(f"Lab {lab_id} not found in database")
                job.status = "failed"
                job.completed_at = datetime.now(timezone.utc)
                job.log_path = f"ERROR: Lab {lab_id} not found"
                session.commit()
                return

            # Use TopologyService to get placements from DATABASE (not YAML)
            topo_service = TopologyService(session)
            analysis = topo_service.analyze_placements(lab_id)

            logger.info(
                f"Multi-host destroy for lab {lab_id}: "
                f"{len(analysis.placements)} hosts"
            )

            # Update job status
            job.status = "running"
            job.started_at = datetime.now(timezone.utc)
            session.commit()

            update_lab_state(session, lab_id, "stopping")

            # Map host_id to agents
            host_to_agent: dict[str, models.Host] = {}
            log_parts = []

            for host_id in analysis.placements:
                agent = session.get(models.Host, host_id)
                if agent:
                    host_to_agent[host_id] = agent
                else:
                    log_parts.append(f"WARNING: Agent '{host_id}' not found, skipping")

            if not host_to_agent:
                # No agents found, try single-agent destroy as fallback
                error_msg = "No agents found for multi-host destroy"
                job.status = "failed"
                job.completed_at = datetime.now(timezone.utc)
                job.log_path = f"ERROR: {error_msg}"
                update_lab_state(session, lab_id, "error", error=error_msg)
                session.commit()
                logger.error(f"Job {job_id} failed: {error_msg}")
                return

            # First, tear down VXLAN tunnels and clean up VxlanTunnel records
            from app.tasks.link_orchestration import teardown_deployment_links

            tunnels_ok, tunnels_failed = await teardown_deployment_links(
                session, lab_id, host_to_agent, log_parts
            )

            # Destroy containers on each host in parallel
            log_parts.append("\n=== Destroying containers ===")
            destroy_tasks = []

            for host_id, agent in host_to_agent.items():
                logger.info(f"Destroying on host {agent.name} (agent {agent.id})")
                destroy_tasks.append(
                    agent_client.destroy_on_agent(agent, job_id, lab_id)
                )

            # Wait for all destroys
            results = await asyncio.gather(*destroy_tasks, return_exceptions=True)

            all_success = True
            for (host_id, agent), result in zip(host_to_agent.items(), results):
                if isinstance(result, Exception):
                    log_parts.append(f"{agent.name}: FAILED - {result}")
                    all_success = False
                else:
                    status = result.get("status", "unknown")
                    log_parts.append(f"{agent.name}: {status}")
                    if result.get("stdout"):
                        log_parts.append(f"  STDOUT: {result['stdout'][:200]}")
                    if result.get("stderr"):
                        log_parts.append(f"  STDERR: {result['stderr'][:200]}")
                    if status != "completed":
                        all_success = False

            # Update job status
            if all_success:
                job.status = "completed"
                update_lab_state(session, lab_id, "stopped")
            else:
                # Use completed_with_warnings for partial failures
                # This provides visibility that cleanup may be incomplete
                job.status = "completed_with_warnings"
                update_lab_state(session, lab_id, "stopped")
                log_parts.append("\nWARNING: Some hosts may have had issues during destroy")
                log_parts.append("Containers may need manual cleanup on failed hosts.")

            job.completed_at = datetime.now(timezone.utc)
            job.log_path = "\n".join(log_parts)
            session.commit()

            # Dispatch webhook for destroy complete
            await _dispatch_webhook("lab.destroy_complete", lab, job, session)

            logger.info(f"Job {job_id} completed: multi-host destroy {'successful' if all_success else 'with warnings'}")

        except Exception as e:
            logger.exception(f"Job {job_id} failed with unexpected error: {e}")
            try:
                job = session.get(models.Job, job_id)
                if job:
                    job.status = "failed"
                    job.completed_at = datetime.now(timezone.utc)
                    job.log_path = f"ERROR: Unexpected error: {e}"
                    update_lab_state(session, lab_id, "error", error=str(e))
                    session.commit()
            except Exception as inner_e:
                logger.exception(f"Critical error handling job {job_id} failure: {inner_e}")


async def run_node_reconcile(
    job_id: str,
    lab_id: str,
    node_ids: list[str],
    provider: str = "docker",
):
    """Reconcile nodes to match their desired state.

    This function handles the reconciliation logic:
    1. If any node needs to start and is undeployed, deploys the full topology
    2. After deploy, stops all nodes where desired_state=stopped
    3. For already-deployed nodes, uses docker start/stop as needed

    Agent affinity is maintained by:
    - Querying NodePlacement records to find which agent has nodes for this lab
    - Preferring that agent for future deploys
    - Cleaning up orphan containers if deploy moves to a new agent

    Args:
        job_id: The job ID
        lab_id: The lab ID
        node_ids: List of node IDs to reconcile
        provider: Provider for the job (default: docker)
    """
    with get_session() as session:
        try:
            job = session.get(models.Job, job_id)
            if not job:
                logger.error(f"Job {job_id} not found in database")
                return

            lab = session.get(models.Lab, lab_id)
            if not lab:
                logger.error(f"Lab {lab_id} not found in database")
                job.status = "failed"
                job.completed_at = datetime.now(timezone.utc)
                job.log_path = f"ERROR: Lab {lab_id} not found"
                session.commit()
                return

            # Get node states
            node_states = (
                session.query(models.NodeState)
                .filter(
                    models.NodeState.lab_id == lab_id,
                    models.NodeState.node_id.in_(node_ids),
                )
                .all()
            )

            if not node_states:
                job.status = "completed"
                job.completed_at = datetime.now(timezone.utc)
                job.log_path = "No nodes to sync"
                session.commit()
                return

            # Track old agent placements before deploy (for orphan cleanup)
            old_placements = (
                session.query(models.NodePlacement)
                .filter(models.NodePlacement.lab_id == lab_id)
                .all()
            )
            old_agent_ids = {p.host_id for p in old_placements}

            # Check if nodes have specific host placement in topology
            # This takes precedence over NodePlacement records and lab.agent_id
            target_agent_id = None

            # Fix node_name placeholders from lazy initialization using database
            # When NodeState is created lazily (before topology sync), node_name=node_id.
            # We need to resolve this to the actual container_name for operations to work.
            topo_service = TopologyService(session)
            db_nodes_all = topo_service.get_nodes(lab_id)
            db_nodes_by_gui_id = {n.gui_id: n for n in db_nodes_all}

            for ns in node_states:
                if ns.node_name == ns.node_id and ns.node_id in db_nodes_by_gui_id:
                    db_node = db_nodes_by_gui_id[ns.node_id]
                    if db_node.container_name != ns.node_name:
                        logger.info(f"Fixing placeholder node_name: {ns.node_name} -> {db_node.container_name}")
                        ns.node_name = db_node.container_name
                        session.commit()

            # Set transitional states EARLY, before any agent lookup can fail.
            # This ensures users see "stopping" or "starting" before "error" if agent lookup fails.
            for ns in node_states:
                old_state = ns.actual_state
                if ns.desired_state == "stopped" and ns.actual_state == "running":
                    ns.actual_state = "stopping"
                    ns.stopping_started_at = datetime.now(timezone.utc)
                    ns.error_message = None
                elif ns.desired_state == "running" and ns.actual_state in ("stopped", "error"):
                    ns.actual_state = "starting"
                    ns.starting_started_at = datetime.now(timezone.utc)
                    ns.error_message = None
                elif ns.desired_state == "running" and ns.actual_state in ("undeployed", "pending"):
                    ns.actual_state = "pending"
                    ns.error_message = None

                # Broadcast transitional state change to WebSocket clients
                if ns.actual_state != old_state:
                    safe_create_task(
                        broadcast_node_state_change(
                            lab_id=lab_id,
                            node_id=ns.node_id,
                            node_name=ns.node_name,
                            desired_state=ns.desired_state,
                            actual_state=ns.actual_state,
                            is_ready=ns.is_ready,
                            error_message=ns.error_message,
                        ),
                        name=f"broadcast:state:{lab_id}:{ns.node_id}"
                    )
            session.commit()

            # Get the node names we're syncing
            node_names_to_sync = {ns.node_name for ns in node_states}

            # Determine target agent for ALL nodes
            # Priority: 1. nodes.host_id (database source of truth), 2. NodePlacement, 3. default
            all_node_agents: dict[str, str] = {}  # node_name -> agent_id

            # Check database nodes.host_id (source of truth for placement)
            db_nodes = (
                session.query(models.Node)
                .filter(
                    models.Node.lab_id == lab_id,
                    models.Node.container_name.in_(node_names_to_sync),
                )
                .all()
            )
            # Track nodes with explicit placement that failed
            explicit_placement_failures = []

            for db_node in db_nodes:
                if db_node.host_id:
                    # Explicit placement - MUST deploy to this agent or error
                    host_agent = session.get(models.Host, db_node.host_id)
                    if not host_agent:
                        explicit_placement_failures.append(
                            f"{db_node.container_name}: assigned host {db_node.host_id} not found"
                        )
                    elif not agent_client.is_agent_online(host_agent):
                        explicit_placement_failures.append(
                            f"{db_node.container_name}: assigned host {host_agent.name} is offline"
                        )
                    else:
                        all_node_agents[db_node.container_name] = db_node.host_id
                        logger.info(f"Node {db_node.container_name} -> host {host_agent.name} (explicit placement)")

            # Fail fast if any explicit placements can't be honored
            if explicit_placement_failures:
                error_msg = "Cannot deploy - explicit host assignments failed:\n" + "\n".join(explicit_placement_failures)
                job.status = "failed"
                job.completed_at = datetime.now(timezone.utc)
                job.log_path = error_msg
                for ns in node_states:
                    if ns.node_name in [f.split(":")[0] for f in explicit_placement_failures]:
                        ns.actual_state = "error"
                        ns.error_message = "Assigned host unavailable"
                session.commit()
                logger.error(f"Sync job {job_id} failed: {error_msg}")
                return

            # Then, determine agent for remaining auto-placed nodes
            auto_placed_nodes = [ns for ns in node_states if ns.node_name not in all_node_agents]
            if auto_placed_nodes:
                # Check existing placements
                auto_node_names = {ns.node_name for ns in auto_placed_nodes}
                existing_placements = (
                    session.query(models.NodePlacement)
                    .filter(
                        models.NodePlacement.lab_id == lab_id,
                        models.NodePlacement.node_name.in_(auto_node_names),
                    )
                    .all()
                )
                placement_map = {p.node_name: p.host_id for p in existing_placements}

                # Find default agent for nodes without existing placement
                default_agent_id = None
                if lab.agent_id:
                    default_agent = session.get(models.Host, lab.agent_id)
                    if default_agent and agent_client.is_agent_online(default_agent):
                        default_agent_id = lab.agent_id
                if not default_agent_id:
                    healthy_agent = await agent_client.get_healthy_agent(session, required_provider=provider)
                    if healthy_agent:
                        default_agent_id = healthy_agent.id

                for ns in auto_placed_nodes:
                    if ns.node_name in placement_map:
                        # Use existing placement
                        all_node_agents[ns.node_name] = placement_map[ns.node_name]
                    elif default_agent_id:
                        # Use default agent
                        all_node_agents[ns.node_name] = default_agent_id
                    # else: will be handled by fallback logic later

            # Group nodes by their target agent
            nodes_by_agent: dict[str, list] = {}
            nodes_without_agent = []
            for ns in node_states:
                agent_id = all_node_agents.get(ns.node_name)
                if agent_id:
                    if agent_id not in nodes_by_agent:
                        nodes_by_agent[agent_id] = []
                    nodes_by_agent[agent_id].append(ns)
                else:
                    nodes_without_agent.append(ns)

            # Debug: Log node-to-agent mapping for multi-host debugging
            logger.debug(f"Job {job_id}: all_node_agents mapping: {all_node_agents}")
            for agent_id, nodes in nodes_by_agent.items():
                logger.debug(f"Job {job_id}: Agent {agent_id} will handle nodes: {[ns.node_name for ns in nodes]}")

            if nodes_by_agent:
                # Pick the first agent to handle in this job
                agent_ids = list(nodes_by_agent.keys())
                target_agent_id = agent_ids[0]
                original_node_count = len(node_states)
                node_states = nodes_by_agent[target_agent_id]
                logger.info(f"Processing {len(node_states)} node(s) on agent {target_agent_id}")
                logger.debug(
                    f"Job {job_id}: Filtered node_states from {original_node_count} to {len(node_states)} nodes. "
                    f"Remaining nodes: {[ns.node_name for ns in node_states]}"
                )

                # Spawn separate jobs for other agents
                for other_agent_id in agent_ids[1:]:
                    other_nodes = nodes_by_agent[other_agent_id]
                    other_node_ids = [ns.node_id for ns in other_nodes]
                    logger.info(f"Spawning sync job for {len(other_node_ids)} node(s) on agent {other_agent_id}")
                    other_job = models.Job(
                        lab_id=lab_id,
                        user_id=job.user_id,
                        action=f"sync:agent:{other_agent_id}:{','.join(other_node_ids)}",
                        status="queued",
                        parent_job_id=job.id,
                    )
                    session.add(other_job)
                    session.commit()
                    session.refresh(other_job)
                    safe_create_task(
                        run_node_reconcile(other_job.id, lab_id, other_node_ids, provider=provider),
                        name=f"sync:agent:{other_job.id}"
                    )

            # Handle nodes that couldn't be assigned an agent
            # DON'T spawn separate jobs - that can cause infinite loops if agent lookup keeps failing
            if nodes_without_agent:
                if not node_states:
                    # No other nodes with agents, try to handle these with fallback logic
                    node_states = nodes_without_agent
                else:
                    # We have nodes with assigned agents - mark unassigned nodes as error
                    # Don't spawn a job that might loop indefinitely
                    logger.warning(
                        f"Cannot assign agent for {len(nodes_without_agent)} node(s), marking as error"
                    )
                    for ns in nodes_without_agent:
                        ns.actual_state = "error"
                        ns.error_message = "No agent available for explicit host placement"
                    session.commit()

            # Find the agent - either from explicit placement or for non-placed nodes
            if target_agent_id:
                # Use the explicitly specified agent from topology
                agent = session.get(models.Host, target_agent_id)
                if agent and not agent_client.is_agent_online(agent):
                    # Agent is offline or has stale heartbeat, can't use it
                    logger.warning(f"Target agent {target_agent_id} is offline or unresponsive")
                    agent = None
            else:
                # Nodes don't have explicit host - check for existing placements first
                # This keeps nodes on their current agent if they have one
                agent = None  # Initialize before conditional blocks
                node_names_to_sync = {ns.node_name for ns in node_states}
                existing_placements = (
                    session.query(models.NodePlacement)
                    .filter(
                        models.NodePlacement.lab_id == lab_id,
                        models.NodePlacement.node_name.in_(node_names_to_sync),
                    )
                    .all()
                )

                if existing_placements:
                    # Use the agent where these nodes are already placed
                    placement_agents = {p.host_id for p in existing_placements}
                    if len(placement_agents) == 1:
                        placement_agent_id = list(placement_agents)[0]
                        agent = session.get(models.Host, placement_agent_id)
                        if agent and agent_client.is_agent_online(agent):
                            logger.info(f"Using existing placement agent: {agent.name}")
                        else:
                            agent = None

                if not agent:
                    # No existing placement - use lab's default agent or find any healthy one
                    # Don't use affinity here to avoid placing on wrong agent
                    if lab.agent_id:
                        agent = session.get(models.Host, lab.agent_id)
                        if agent and not agent_client.is_agent_online(agent):
                            agent = None

                    if not agent:
                        # Find any healthy agent (no affinity preference)
                        agent = await agent_client.get_healthy_agent(
                            session,
                            required_provider=provider,
                        )
            if not agent:
                job.status = "failed"
                job.completed_at = datetime.now(timezone.utc)
                if target_agent_id:
                    job.log_path = f"ERROR: Target agent {target_agent_id} is offline or unresponsive"
                    error_msg = f"Target agent offline"
                else:
                    job.log_path = f"ERROR: No healthy agent available with {provider} support"
                    error_msg = "No agent available"
                # Mark nodes as error
                for ns in node_states:
                    ns.actual_state = "error"
                    ns.error_message = error_msg
                session.commit()
                logger.warning(f"Job {job_id} failed: no healthy agent available")
                return

            # Update job with agent assignment
            job.status = "running"
            job.agent_id = agent.id
            job.started_at = datetime.now(timezone.utc)
            session.commit()

            # Broadcast job started
            await _broadcast_job_progress(
                lab_id, job_id, job.action, "running",
                progress_message=f"Syncing {len(node_states)} node(s) on {agent.name or agent.id}"
            )

            log_parts = []
            log_parts.append(f"=== Node Sync Job ===")
            log_parts.append(f"Lab: {lab_id}")
            log_parts.append(f"Agent: {agent.id} ({agent.name})")
            log_parts.append(f"Nodes: {', '.join(node_ids)}")
            log_parts.append("")

            # Initialize graph to None - will be loaded later if needed for deploy
            graph = None

            # Categorize nodes by what action they need
            # Note: Transitional states (stopping, starting, pending) were set earlier
            # before agent lookup, so we match on both original and transitional states
            nodes_need_deploy = []  # undeployed/pending -> running
            nodes_need_start = []   # stopped/starting -> running
            nodes_need_stop = []    # running/stopping -> stopped

            for ns in node_states:
                if ns.desired_state == "running":
                    if ns.actual_state in ("undeployed", "pending"):
                        nodes_need_deploy.append(ns)
                    elif ns.actual_state in ("stopped", "error", "starting"):
                        # Both stopped and error states can be started via docker start
                        # Error state may be from intentional stop (exit code 137/143) or crash
                        # Either way, try starting first - if container doesn't exist, it will fail
                        # and we can fall back to deploy
                        # "starting" is already set earlier (transitional state set before agent lookup)
                        nodes_need_start.append(ns)
                    # If already running, nothing to do
                elif ns.desired_state == "stopped":
                    if ns.actual_state in ("running", "stopping"):
                        # "stopping" is already set earlier (transitional state set before agent lookup)
                        nodes_need_stop.append(ns)
                    # If already stopped/undeployed, nothing to do

            logger.info(
                f"Sync job {job_id}: deploy={len(nodes_need_deploy)}, "
                f"start={len(nodes_need_start)}, stop={len(nodes_need_stop)}"
            )

            # Log messages for actions
            if nodes_need_start:
                log_parts.append(f"Starting {len(nodes_need_start)} node(s)...")

            # Migration detection: Check if nodes exist on different agents and clean them up
            # This handles the case where a node's host placement changed
            nodes_to_start_or_deploy = nodes_need_deploy + nodes_need_start
            if nodes_to_start_or_deploy:
                node_names_to_check = [ns.node_name for ns in nodes_to_start_or_deploy]

                # Get current placements for these nodes
                current_placements = (
                    session.query(models.NodePlacement)
                    .filter(
                        models.NodePlacement.lab_id == lab_id,
                        models.NodePlacement.node_name.in_(node_names_to_check),
                    )
                    .all()
                )

                # Find nodes on wrong agents
                migrations_needed = []
                for placement in current_placements:
                    if placement.host_id != agent.id:
                        migrations_needed.append(placement)

                if migrations_needed:
                    log_parts.append("=== Migration: Cleaning up containers on old agents ===")
                    logger.info(
                        f"Migration needed for {len(migrations_needed)} nodes in lab {lab_id}"
                    )

                    # Group by old agent for efficiency
                    old_agent_nodes: dict[str, list[str]] = {}
                    for placement in migrations_needed:
                        if placement.host_id not in old_agent_nodes:
                            old_agent_nodes[placement.host_id] = []
                        old_agent_nodes[placement.host_id].append(placement.node_name)

                    # Stop containers on each old agent
                    for old_agent_id, node_names in old_agent_nodes.items():
                        old_agent = session.get(models.Host, old_agent_id)
                        if not old_agent:
                            log_parts.append(f"  Old agent {old_agent_id} not found, skipping cleanup")
                            continue

                        if not agent_client.is_agent_online(old_agent):
                            log_parts.append(f"  Old agent {old_agent.name} is offline, skipping cleanup")
                            continue

                        log_parts.append(f"  Stopping {len(node_names)} container(s) on {old_agent.name}...")

                        for node_name in node_names:
                            container_name = _get_container_name(lab_id, node_name)
                            try:
                                result = await agent_client.container_action(
                                    old_agent, container_name, "stop"
                                )
                                if result.get("success"):
                                    log_parts.append(f"    {node_name}: stopped on {old_agent.name}")
                                else:
                                    # Container might not exist or already stopped - that's OK
                                    error = result.get("error", "unknown")
                                    log_parts.append(f"    {node_name}: {error}")
                            except Exception as e:
                                log_parts.append(f"    {node_name}: cleanup failed - {e}")

                        # Delete old placement records for migrated nodes
                        for node_name in node_names:
                            session.query(models.NodePlacement).filter(
                                models.NodePlacement.lab_id == lab_id,
                                models.NodePlacement.node_name == node_name,
                                models.NodePlacement.host_id == old_agent_id,
                            ).delete()

                    session.commit()
                    log_parts.append("")

                # Fallback: For nodes without NodePlacement records, check all other agents
                # This handles containers created before placement tracking was added
                #
                # OPTIMIZATION: Skip nodes that have never been deployed (actual_state=undeployed)
                # or have explicit host placement. These nodes can't exist on other agents.
                placed_node_names = {p.node_name for p in current_placements}

                # Get the actual state of nodes we're checking
                node_actual_states = {
                    ns.node_name: ns.actual_state
                    for ns in nodes_to_start_or_deploy
                }

                # Get nodes with explicit host placement from topology
                nodes_with_explicit_host = set()
                if graph:
                    for n in graph.nodes:
                        node_key = n.container_name or n.name
                        if n.host:  # Has explicit host placement
                            nodes_with_explicit_host.add(node_key)

                # Filter to only nodes that:
                # 1. Have no placement record AND
                # 2. Were previously deployed (not undeployed) AND
                # 3. Don't have explicit host placement
                untracked_nodes = [
                    n for n in node_names_to_check
                    if n not in placed_node_names
                    and node_actual_states.get(n) not in ("undeployed", None)
                    and n not in nodes_with_explicit_host
                ]

                if untracked_nodes:
                    # Get all online agents except the target
                    all_agents = (
                        session.query(models.Host)
                        .filter(
                            models.Host.id != agent.id,
                            models.Host.status == "online",
                        )
                        .all()
                    )

                    # Filter to actually online agents
                    other_agents = [a for a in all_agents if agent_client.is_agent_online(a)]

                    if other_agents:
                        log_parts.append("=== Migration: Checking other agents for untracked containers ===")
                        logger.info(
                            f"Checking {len(other_agents)} other agents for {len(untracked_nodes)} "
                            f"untracked nodes in lab {lab_id}"
                        )

                        for other_agent in other_agents:
                            containers_found = []
                            for node_name in untracked_nodes:
                                container_name = _get_container_name(lab_id, node_name)
                                try:
                                    # Try to stop - if it succeeds, container existed
                                    result = await agent_client.container_action(
                                        other_agent, container_name, "stop"
                                    )
                                    if result.get("success"):
                                        containers_found.append(node_name)
                                        log_parts.append(
                                            f"  {node_name}: found and stopped on {other_agent.name}"
                                        )
                                    # If "not found" error, container doesn't exist - that's expected
                                except Exception as e:
                                    logger.debug(f"Container check failed on {other_agent.name}: {e}")

                            if containers_found:
                                logger.info(
                                    f"Stopped {len(containers_found)} containers on {other_agent.name} "
                                    f"during migration for lab {lab_id}"
                                )

                        log_parts.append("")

                # Update placements EARLY with "starting" status so UI shows correct target host
                # This runs after migration detection but before actual deploy/start
                node_names_for_placement = [ns.node_name for ns in nodes_to_start_or_deploy]
                await _update_node_placements(
                    session, lab_id, agent.id, node_names_for_placement, status="starting"
                )

            # Phase 1: If any nodes need deploy, we need to deploy the full topology
            # Containerlab doesn't support per-node deploy, so we deploy all and then stop unwanted
            if nodes_need_deploy:
                log_parts.append("=== Phase 1: Deploy Topology ===")

                # Get topology from database (source of truth)
                if not topo_service.has_nodes(lab_id):
                    error_msg = "No topology defined in database"
                    job.status = "failed"
                    job.completed_at = datetime.now(timezone.utc)
                    job.log_path = f"ERROR: {error_msg}"
                    for ns in nodes_need_deploy:
                        ns.actual_state = "error"
                        ns.error_message = error_msg
                    session.commit()
                    return

                # Get graph from database
                graph = topo_service.export_to_graph(lab_id)

                # Get the names of nodes we're actually trying to deploy
                nodes_to_deploy_names = {ns.node_name for ns in nodes_need_deploy}

                # IMPORTANT: Include ALL nodes that belong on this agent, not just nodes being deployed
                # Containerlab's --reconfigure will DESTROY nodes not in the topology!
                # So we must include running nodes to prevent them from being removed.
                from app.topology import TopologyGraph

                # Get all nodes that should be on this agent using database Node.host_id
                all_agent_node_names = set()
                for n in graph.nodes:
                    node_key = n.container_name or n.name
                    # Check if this node belongs on this agent using host_id from database
                    db_node = (
                        session.query(models.Node)
                        .filter(
                            models.Node.lab_id == lab_id,
                            models.Node.container_name == node_key,
                        )
                        .first()
                    )
                    if db_node and db_node.host_id:
                        # Explicit host - check if it matches this agent
                        if db_node.host_id == agent.id:
                            all_agent_node_names.add(node_key)
                            logger.debug(f"Job {job_id}: Added {node_key} (explicit host match: {db_node.host_id})")
                        else:
                            logger.debug(f"Job {job_id}: Skipped {node_key} (explicit host {db_node.host_id} != agent {agent.id})")
                    else:
                        # Auto-placed node - include if it's one we're deploying
                        # (auto-placed nodes on this agent are the ones we're syncing)
                        if node_key in nodes_to_deploy_names:
                            all_agent_node_names.add(node_key)
                            logger.debug(f"Job {job_id}: Added {node_key} (auto-placed, in nodes_to_deploy_names)")
                        else:
                            # Check if this node has a placement on this agent
                            placement = (
                                session.query(models.NodePlacement)
                                .filter(
                                    models.NodePlacement.lab_id == lab_id,
                                    models.NodePlacement.node_name == node_key,
                                    models.NodePlacement.host_id == agent.id,
                                )
                                .first()
                            )
                            if placement:
                                all_agent_node_names.add(node_key)
                                logger.debug(f"Job {job_id}: Added {node_key} (existing placement on this agent)")

                # Also include nodes that exist (running or stopped) and should be on this agent
                # For nodes without placement records, assume they're on the lab's default agent
                # Include stopped nodes too - they have containers that would be destroyed!
                all_existing = (
                    session.query(models.NodeState)
                    .filter(
                        models.NodeState.lab_id == lab_id,
                        models.NodeState.actual_state.in_(["running", "stopped"]),
                    )
                    .all()
                )
                for ns in all_existing:
                    # Check if this node has a placement
                    placement = (
                        session.query(models.NodePlacement)
                        .filter(
                            models.NodePlacement.lab_id == lab_id,
                            models.NodePlacement.node_name == ns.node_name,
                        )
                        .first()
                    )
                    if placement:
                        if placement.host_id == agent.id:
                            all_agent_node_names.add(ns.node_name)
                            logger.debug(f"Job {job_id}: Added existing {ns.node_name} (placement on this agent)")
                    else:
                        # No placement - assume it's on lab's default agent or this agent
                        if lab.agent_id == agent.id or lab.agent_id is None:
                            all_agent_node_names.add(ns.node_name)
                            logger.debug(
                                f"Job {job_id}: Added existing {ns.node_name} (no placement, lab default agent match: "
                                f"lab.agent_id={lab.agent_id}, agent.id={agent.id})"
                            )

                # Filter topology to include all nodes for this agent
                filtered_nodes = [n for n in graph.nodes if (n.container_name or n.name) in all_agent_node_names]

                # Debug: Log the filtering decision for each node
                for n in graph.nodes:
                    node_key = n.container_name or n.name
                    is_included = node_key in all_agent_node_names
                    logger.debug(
                        f"Node filtering for {node_key}: included={is_included}, "
                        f"agent={agent.id}, all_agent_nodes={list(all_agent_node_names)}"
                    )

                # Include links where BOTH endpoints are in our filtered nodes
                # Build a set with both container names AND GUI IDs since link endpoints use GUI IDs
                filtered_node_names = {n.container_name or n.name for n in filtered_nodes}
                filtered_node_ids = {n.id for n in filtered_nodes}
                filtered_node_identifiers = filtered_node_names | filtered_node_ids
                filtered_links = [
                    link for link in graph.links
                    if all(ep.node in filtered_node_identifiers for ep in link.endpoints)
                ]

                interface_count_map = topo_service.get_interface_count_map(lab_id)
                for n in filtered_nodes:
                    node_key = n.container_name or n.name
                    iface_count = interface_count_map.get(node_key, 0)
                    if iface_count > 0:
                        vars_dict = dict(n.vars or {})
                        vars_dict["interface_count"] = iface_count
                        n.vars = vars_dict

                filtered_graph = TopologyGraph(
                    nodes=filtered_nodes,
                    links=filtered_links,
                    defaults=graph.defaults,
                )

                # Track which nodes are actually being deployed (the new ones)
                deployed_node_names = nodes_to_deploy_names & filtered_node_names

                if not deployed_node_names:
                    log_parts.append(f"No nodes to deploy on {agent.name}")
                    for ns in nodes_need_deploy:
                        ns.actual_state = "error"
                        ns.error_message = "No nodes to deploy"
                    session.commit()
                    nodes_need_deploy = []
                else:
                    # VALIDATION: Verify all nodes in topology belong to this agent
                    # This prevents race conditions where wrong nodes get deployed
                    misplaced_nodes = []
                    for n in filtered_graph.nodes:
                        node_key = n.container_name or n.name
                        db_node = session.query(models.Node).filter(
                            models.Node.lab_id == lab_id,
                            models.Node.container_name == node_key,
                        ).first()
                        if db_node and db_node.host_id and db_node.host_id != agent.id:
                            misplaced_nodes.append(f"{node_key} (assigned to {db_node.host_id})")

                    if misplaced_nodes:
                        error_msg = (
                            f"DEPLOY ABORTED: Nodes assigned to different agent detected in topology: "
                            f"{', '.join(misplaced_nodes)}. This agent: {agent.id}"
                        )
                        logger.error(f"Job {job_id}: {error_msg}")
                        log_parts.append(error_msg)
                        for ns in nodes_need_deploy:
                            ns.actual_state = "error"
                            ns.error_message = "Deploy validation failed - wrong agent"
                        session.commit()
                        job.status = "failed"
                        job.completed_at = datetime.now(timezone.utc)
                        job.log_path = "\n".join(log_parts)
                        session.commit()
                        return

                    # Convert filtered graph to JSON deploy topology
                    topology_json = graph_to_deploy_topology(filtered_graph)
                    log_parts.append(f"Deploying {len(filtered_graph.nodes)} node(s) on {agent.name}: {', '.join(deployed_node_names)}")

                    # Acquire deploy locks for all nodes we're about to deploy
                    all_topology_nodes = [n.container_name or n.name for n in filtered_graph.nodes]
                    lock_acquired, failed_nodes = acquire_deploy_lock(lab_id, all_topology_nodes, agent.id)
                    if not lock_acquired:
                        error_msg = (
                            f"DEPLOY ABORTED: Could not acquire lock for nodes: {', '.join(failed_nodes)}. "
                            f"Another deploy may be in progress."
                        )
                        logger.error(f"Job {job_id}: {error_msg}")
                        log_parts.append(error_msg)
                        for ns in nodes_need_deploy:
                            ns.actual_state = "error"
                            ns.error_message = "Deploy lock conflict"
                        session.commit()
                        job.status = "failed"
                        job.completed_at = datetime.now(timezone.utc)
                        job.log_path = "\n".join(log_parts)
                        session.commit()
                        return

                    try:
                        result = await agent_client.deploy_to_agent(
                            agent, job_id, lab_id,
                            topology=topology_json,  # Use JSON, not YAML
                            provider=provider,
                        )

                        if result.get("status") == "completed":
                            log_parts.append("Deploy completed successfully")

                            # Capture management IPs for IaC workflows
                            await _capture_node_ips(session, lab_id, agent)

                            # Get all node states for this lab to update them
                            all_states = (
                                session.query(models.NodeState)
                                .filter(models.NodeState.lab_id == lab_id)
                                .all()
                            )

                            # Only update NodePlacement for nodes that were actually deployed
                            await _update_node_placements(
                                session, lab_id, agent.id, list(deployed_node_names)
                            )

                            # Clean up orphan containers on old agents if deploy moved
                            if old_agent_ids and agent.id not in old_agent_ids:
                                log_parts.append("")
                                log_parts.append("=== Orphan Cleanup ===")
                                await _cleanup_orphan_containers(
                                    session, lab_id, agent.id, old_agent_ids, log_parts
                                )

                            # After deploy, only deployed nodes are running
                            # We need to stop deployed nodes where desired_state=stopped
                            nodes_to_stop_after_deploy = [
                                ns for ns in all_states
                                if ns.desired_state == "stopped" and ns.node_name in deployed_node_names
                            ]

                            # Mark deployed nodes as running (since clab starts them all)
                            for ns in all_states:
                                if ns.node_name in deployed_node_names:
                                    ns.actual_state = "running"
                                    ns.error_message = None
                                    if not ns.boot_started_at:
                                        ns.boot_started_at = datetime.now(timezone.utc)

                            session.commit()

                            if nodes_to_stop_after_deploy:
                                log_parts.append("")
                                log_parts.append(f"Stopping {len(nodes_to_stop_after_deploy)} nodes with desired_state=stopped...")

                                for ns in nodes_to_stop_after_deploy:
                                    container_name = _get_container_name(lab_id, ns.node_name)
                                    stop_result = await agent_client.container_action(
                                        agent, container_name, "stop"
                                    )
                                    if stop_result.get("success"):
                                        ns.actual_state = "stopped"
                                        ns.stopping_started_at = None
                                        ns.boot_started_at = None
                                        log_parts.append(f"  {ns.node_name}: stopped")
                                    else:
                                        ns.actual_state = "error"
                                        ns.stopping_started_at = None
                                        ns.error_message = stop_result.get("error") or "Stop failed"
                                        ns.boot_started_at = None
                                        log_parts.append(f"  {ns.node_name}: FAILED - {ns.error_message}")

                                session.commit()

                        else:
                            error_msg = result.get("error_message", "Deploy failed")
                            log_parts.append(f"Deploy FAILED: {error_msg}")
                            for ns in nodes_need_deploy:
                                ns.actual_state = "error"
                                ns.error_message = error_msg
                            session.commit()

                        if result.get("stdout"):
                            log_parts.append(f"\nDeploy STDOUT:\n{result['stdout']}")
                        if result.get("stderr"):
                            log_parts.append(f"\nDeploy STDERR:\n{result['stderr']}")

                    except AgentUnavailableError as e:
                        # Transient error - agent unreachable, don't mark nodes as permanently failed
                        error_msg = f"Agent unreachable (transient): {e.message}"
                        log_parts.append(f"Deploy FAILED (transient): {error_msg}")
                        log_parts.append("  Note: This may be a temporary network issue. Nodes will be retried by reconciliation.")
                        for ns in nodes_need_deploy:
                            # Keep nodes in pending state rather than marking as error
                            # Reconciliation will retry when agent becomes available
                            if ns.actual_state not in ("running", "stopped"):
                                ns.actual_state = "pending"
                            ns.error_message = error_msg
                        session.commit()
                        logger.warning(f"Deploy in sync job {job_id} failed due to agent unavailability: {e}")
                    except Exception as e:
                        error_msg = str(e)
                        log_parts.append(f"Deploy FAILED: {error_msg}")
                        for ns in nodes_need_deploy:
                            ns.actual_state = "error"
                            ns.error_message = error_msg
                        session.commit()
                        logger.exception(f"Deploy failed in sync job {job_id}: {e}")
                    finally:
                        # Always release deploy locks
                        release_deploy_lock(lab_id, all_topology_nodes)

            # Phase 2: Start nodes that are stopped but should be running
            # Docker start doesn't recreate network interfaces,
            # so we MUST redeploy to properly restart nodes.
            # This redeploys the full topology which recreates all veth pairs.
            if nodes_need_start:
                log_parts.append("")
                log_parts.append("=== Phase 2: Start Nodes (via redeploy) ===")
                log_parts.append("Note: Full redeploy required to recreate network interfaces")

                # Get topology from database (source of truth)
                if not topo_service.has_nodes(lab_id):
                    error_msg = "No topology defined in database"
                    for ns in nodes_need_start:
                        ns.actual_state = "error"
                        ns.starting_started_at = None  # Clear starting timestamp
                        ns.error_message = error_msg
                    session.commit()
                    log_parts.append(f"Redeploy FAILED: {error_msg}")
                else:
                    graph = topo_service.export_to_graph(lab_id)

                    # Get the names of nodes we're actually trying to start
                    nodes_to_start_names = {ns.node_name for ns in nodes_need_start}

                    # IMPORTANT: Include ALL nodes that belong on this agent, not just nodes being started
                    # Containerlab's --reconfigure will DESTROY nodes not in the topology!
                    from app.topology import TopologyGraph

                    # Get all nodes that should be on this agent using database Node.host_id
                    all_agent_node_names = set()
                    for n in graph.nodes:
                        node_key = n.container_name or n.name
                        # Check if this node belongs on this agent using host_id from database
                        db_node = (
                            session.query(models.Node)
                            .filter(
                                models.Node.lab_id == lab_id,
                                models.Node.container_name == node_key,
                            )
                            .first()
                        )
                        if db_node and db_node.host_id:
                            if db_node.host_id == agent.id:
                                all_agent_node_names.add(node_key)
                        else:
                            if node_key in nodes_to_start_names:
                                all_agent_node_names.add(node_key)
                            else:
                                placement = (
                                    session.query(models.NodePlacement)
                                    .filter(
                                        models.NodePlacement.lab_id == lab_id,
                                        models.NodePlacement.node_name == node_key,
                                        models.NodePlacement.host_id == agent.id,
                                    )
                                    .first()
                                )
                                if placement:
                                    all_agent_node_names.add(node_key)

                    # Also include nodes that exist (running or stopped) and should be on this agent
                    all_existing = (
                        session.query(models.NodeState)
                        .filter(
                            models.NodeState.lab_id == lab_id,
                            models.NodeState.actual_state.in_(["running", "stopped"]),
                        )
                        .all()
                    )
                    for ns in all_existing:
                        placement = (
                            session.query(models.NodePlacement)
                            .filter(
                                models.NodePlacement.lab_id == lab_id,
                                models.NodePlacement.node_name == ns.node_name,
                            )
                            .first()
                        )
                        if placement:
                            if placement.host_id == agent.id:
                                all_agent_node_names.add(ns.node_name)
                        else:
                            if lab.agent_id == agent.id or lab.agent_id is None:
                                all_agent_node_names.add(ns.node_name)

                    # Filter topology to include all nodes for this agent
                    filtered_nodes = [n for n in graph.nodes if (n.container_name or n.name) in all_agent_node_names]

                    # Include links where BOTH endpoints are in our filtered nodes
                    # Build a set with both container names AND GUI IDs since link endpoints use GUI IDs
                    filtered_node_names = {n.container_name or n.name for n in filtered_nodes}
                    filtered_node_ids = {n.id for n in filtered_nodes}
                    filtered_node_identifiers = filtered_node_names | filtered_node_ids
                    filtered_links = [
                        link for link in graph.links
                        if all(ep.node in filtered_node_identifiers for ep in link.endpoints)
                    ]

                    filtered_graph = TopologyGraph(
                        nodes=filtered_nodes,
                        links=filtered_links,
                        defaults=graph.defaults,
                    )

                    # Track which nodes are actually being started (the new ones)
                    deployed_node_names = nodes_to_start_names & filtered_node_names

                    if not deployed_node_names:
                        log_parts.append(f"No nodes to redeploy on {agent.name}")
                        for ns in nodes_need_start:
                            ns.actual_state = "error"
                            ns.starting_started_at = None  # Clear starting timestamp
                            ns.error_message = "No nodes to deploy"
                        session.commit()
                        nodes_need_start = []
                    else:
                        # VALIDATION: Verify all nodes in topology belong to this agent
                        misplaced_nodes = []
                        for n in filtered_graph.nodes:
                            node_key = n.container_name or n.name
                            db_node = session.query(models.Node).filter(
                                models.Node.lab_id == lab_id,
                                models.Node.container_name == node_key,
                            ).first()
                            if db_node and db_node.host_id and db_node.host_id != agent.id:
                                misplaced_nodes.append(f"{node_key} (assigned to {db_node.host_id})")

                        if misplaced_nodes:
                            error_msg = (
                                f"REDEPLOY ABORTED: Nodes assigned to different agent detected: "
                                f"{', '.join(misplaced_nodes)}. This agent: {agent.id}"
                            )
                            logger.error(f"Job {job_id}: {error_msg}")
                            log_parts.append(error_msg)
                            for ns in nodes_need_start:
                                ns.actual_state = "error"
                                ns.starting_started_at = None
                                ns.error_message = "Deploy validation failed - wrong agent"
                            session.commit()
                            job.status = "failed"
                            job.completed_at = datetime.now(timezone.utc)
                            job.log_path = "\n".join(log_parts)
                            session.commit()
                            return

                        # Convert filtered graph to JSON deploy topology
                        topology_json = graph_to_deploy_topology(filtered_graph)
                        log_parts.append(f"Redeploying {len(filtered_graph.nodes)} node(s) on {agent.name}: {', '.join(deployed_node_names)}")

                        # Acquire deploy locks for all nodes we're about to redeploy
                        all_topology_nodes = [n.container_name or n.name for n in filtered_graph.nodes]
                        lock_acquired, failed_nodes = acquire_deploy_lock(lab_id, all_topology_nodes, agent.id)
                        if not lock_acquired:
                            error_msg = (
                                f"REDEPLOY ABORTED: Could not acquire lock for nodes: {', '.join(failed_nodes)}. "
                                f"Another deploy may be in progress."
                            )
                            logger.error(f"Job {job_id}: {error_msg}")
                            log_parts.append(error_msg)
                            for ns in nodes_need_start:
                                ns.actual_state = "error"
                                ns.starting_started_at = None
                                ns.error_message = "Deploy lock conflict"
                            session.commit()
                            job.status = "failed"
                            job.completed_at = datetime.now(timezone.utc)
                            job.log_path = "\n".join(log_parts)
                            session.commit()
                            return

                        try:
                            result = await agent_client.deploy_to_agent(
                                agent, job_id, lab_id,
                                topology=topology_json,  # Use JSON, not YAML
                                provider=provider,
                            )

                            if result.get("status") == "completed":
                                log_parts.append("Redeploy completed successfully")

                                # Capture management IPs for IaC workflows
                                await _capture_node_ips(session, lab_id, agent)

                                # Get all node states for this lab
                                all_states = (
                                    session.query(models.NodeState)
                                    .filter(models.NodeState.lab_id == lab_id)
                                    .all()
                                )

                                # Only update NodePlacement for nodes that were actually deployed
                                await _update_node_placements(
                                    session, lab_id, agent.id, list(deployed_node_names)
                                )

                                # Clean up orphan containers on old agents if deploy moved
                                if old_agent_ids and agent.id not in old_agent_ids:
                                    log_parts.append("")
                                    log_parts.append("=== Orphan Cleanup ===")
                                    await _cleanup_orphan_containers(
                                        session, lab_id, agent.id, old_agent_ids, log_parts
                                    )

                                # Only mark deployed nodes as running (not all nodes)
                                for ns in all_states:
                                    if ns.node_name in deployed_node_names:
                                        ns.actual_state = "running"
                                        ns.starting_started_at = None  # Clear starting timestamp
                                        ns.error_message = None
                                        if not ns.boot_started_at:
                                            ns.boot_started_at = datetime.now(timezone.utc)
                                        log_parts.append(f"  Node {ns.node_name}: started")

                                # Now stop deployed nodes that should be stopped
                                nodes_to_stop_after = [
                                    ns for ns in all_states
                                    if ns.desired_state == "stopped" and ns.node_name in deployed_node_names
                                ]

                                if nodes_to_stop_after:
                                    log_parts.append("")
                                    log_parts.append(f"Stopping {len(nodes_to_stop_after)} nodes with desired_state=stopped...")

                                    for ns in nodes_to_stop_after:
                                        container_name = _get_container_name(lab_id, ns.node_name)
                                        stop_result = await agent_client.container_action(
                                            agent, container_name, "stop"
                                        )
                                        if stop_result.get("success"):
                                            ns.actual_state = "stopped"
                                            ns.stopping_started_at = None
                                            ns.boot_started_at = None
                                            log_parts.append(f"  {ns.node_name}: stopped")
                                        else:
                                            ns.actual_state = "error"
                                            ns.stopping_started_at = None
                                            ns.error_message = stop_result.get("error") or "Stop failed"
                                            ns.boot_started_at = None
                                            log_parts.append(f"  {ns.node_name}: FAILED - {ns.error_message}")
                            else:
                                error_msg = result.get("error_message", "Redeploy failed")
                                log_parts.append(f"Redeploy FAILED: {error_msg}")
                                for ns in nodes_need_start:
                                    ns.actual_state = "error"
                                    ns.starting_started_at = None  # Clear starting timestamp
                                    ns.error_message = error_msg

                            if result.get("stdout"):
                                log_parts.append(f"\nDeploy STDOUT:\n{result['stdout']}")
                            if result.get("stderr"):
                                log_parts.append(f"\nDeploy STDERR:\n{result['stderr']}")

                        except AgentUnavailableError as e:
                            # Transient error - agent unreachable, don't mark nodes as permanently failed
                            error_msg = f"Agent unreachable (transient): {e.message}"
                            log_parts.append(f"Redeploy FAILED (transient): {error_msg}")
                            log_parts.append("  Note: This may be a temporary network issue. Nodes will be retried by reconciliation.")
                            for ns in nodes_need_start:
                                # Keep existing state rather than marking as error
                                # but clear starting timestamp since we're not actively starting
                                ns.starting_started_at = None
                                ns.error_message = error_msg
                            logger.warning(f"Redeploy in sync job {job_id} failed due to agent unavailability: {e}")
                        except Exception as e:
                            error_msg = str(e)
                            log_parts.append(f"Redeploy FAILED: {error_msg}")
                            for ns in nodes_need_start:
                                ns.actual_state = "error"
                                ns.starting_started_at = None  # Clear starting timestamp
                                ns.error_message = error_msg
                            logger.exception(f"Redeploy failed in sync job {job_id}: {e}")
                        finally:
                            # Always release deploy locks
                            release_deploy_lock(lab_id, all_topology_nodes)

                        session.commit()

            # Phase 3: Stop nodes that are running but should be stopped
            if nodes_need_stop:
                log_parts.append("")
                log_parts.append("=== Phase 3: Stop Nodes ===")

                for ns in nodes_need_stop:
                    container_name = _get_container_name(lab_id, ns.node_name)
                    log_parts.append(f"Stopping {ns.node_name} ({container_name})...")

                    # For stop operations, try the target agent first, then fall back to
                    # the lab's default agent if container not found (migration scenario)
                    stop_agent = agent
                    try:
                        result = await agent_client.container_action(
                            stop_agent, container_name, "stop"
                        )
                        # If container not found on target agent, try lab's default agent
                        if not result.get("success") and "not found" in result.get("error", "").lower():
                            if lab.agent_id and lab.agent_id != agent.id:
                                old_agent = session.get(models.Host, lab.agent_id)
                                if old_agent and agent_client.is_agent_online(old_agent):
                                    log_parts.append(f"    Container not on {agent.name}, trying {old_agent.name}...")
                                    stop_agent = old_agent
                                    result = await agent_client.container_action(
                                        stop_agent, container_name, "stop"
                                    )
                        if result.get("success"):
                            ns.actual_state = "stopped"
                            ns.stopping_started_at = None
                            ns.error_message = None
                            ns.boot_started_at = None
                            ns.is_ready = False
                            log_parts.append(f"  {ns.node_name}: stopped")
                            # Broadcast final state immediately so frontend gets authoritative state
                            safe_create_task(
                                broadcast_node_state_change(
                                    lab_id=lab_id,
                                    node_id=ns.node_id,
                                    node_name=ns.node_name,
                                    desired_state=ns.desired_state,
                                    actual_state=ns.actual_state,
                                    is_ready=ns.is_ready,
                                    error_message=ns.error_message,
                                ),
                                name=f"broadcast:stopped:{lab_id}:{ns.node_id}"
                            )
                        else:
                            ns.actual_state = "error"
                            ns.stopping_started_at = None
                            ns.error_message = result.get("error") or "Stop failed"
                            ns.boot_started_at = None
                            ns.is_ready = False
                            log_parts.append(f"  {ns.node_name}: FAILED - {ns.error_message}")
                            # Broadcast error state
                            safe_create_task(
                                broadcast_node_state_change(
                                    lab_id=lab_id,
                                    node_id=ns.node_id,
                                    node_name=ns.node_name,
                                    desired_state=ns.desired_state,
                                    actual_state=ns.actual_state,
                                    is_ready=ns.is_ready,
                                    error_message=ns.error_message,
                                ),
                                name=f"broadcast:error:{lab_id}:{ns.node_id}"
                            )
                    except AgentUnavailableError as e:
                        # Transient error - agent unreachable, don't mark as permanently failed
                        error_msg = f"Agent unreachable (transient): {e.message}"
                        # Keep existing state - reconciliation will handle this
                        ns.error_message = error_msg
                        log_parts.append(f"  {ns.node_name}: FAILED (transient) - {error_msg}")
                        logger.warning(f"Stop {ns.node_name} in job {job_id} failed due to agent unavailability")
                    except Exception as e:
                        ns.actual_state = "error"
                        ns.stopping_started_at = None
                        ns.error_message = str(e)
                        ns.boot_started_at = None
                        ns.is_ready = False
                        log_parts.append(f"  {ns.node_name}: FAILED - {e}")
                        # Broadcast error state
                        safe_create_task(
                            broadcast_node_state_change(
                                lab_id=lab_id,
                                node_id=ns.node_id,
                                node_name=ns.node_name,
                                desired_state=ns.desired_state,
                                actual_state=ns.actual_state,
                                is_ready=ns.is_ready,
                                error_message=ns.error_message,
                            ),
                            name=f"broadcast:error:{lab_id}:{ns.node_id}"
                        )

                session.commit()

            # Phase 4: Create cross-host links if needed
            # This handles VXLAN tunnel setup for links between nodes on different agents.
            # We check after each sync job because both endpoints need to be deployed first.
            await _create_cross_host_links_if_ready(session, lab_id, log_parts)

            # Check if any nodes are in error state
            error_count = sum(1 for ns in node_states if ns.actual_state == "error")

            if error_count > 0:
                job.status = "failed"
                log_parts.append(f"\nCompleted with {error_count} error(s)")
                # Broadcast job failed
                await _broadcast_job_progress(
                    lab_id, job_id, job.action, "failed",
                    error_message=f"Node sync failed: {error_count} error(s)"
                )
            else:
                job.status = "completed"
                log_parts.append("\nAll nodes synced successfully")
                # Broadcast job completed
                await _broadcast_job_progress(
                    lab_id, job_id, job.action, "completed",
                    progress_message=f"Node sync completed successfully"
                )

            job.completed_at = datetime.now(timezone.utc)
            job.log_path = "\n".join(log_parts)
            session.commit()

            logger.info(f"Job {job_id} completed with status: {job.status}")

        except Exception as e:
            logger.exception(f"Job {job_id} failed with unexpected error: {e}")
            try:
                job = session.get(models.Job, job_id)
                if job:
                    job.status = "failed"
                    job.completed_at = datetime.now(timezone.utc)
                    job.log_path = f"ERROR: Unexpected error: {e}"
                    session.commit()
            except Exception as inner_e:
                logger.exception(f"Critical error handling job {job_id} failure: {inner_e}")


async def _create_cross_host_links_if_ready(
    session,
    lab_id: str,
    log_parts: list[str],
) -> None:
    """Create cross-host links (VXLAN tunnels) if both endpoints are ready.

    This is called after each sync job completes to check if any cross-host
    links can now be created. A link can be created when:
    1. Both endpoint nodes are deployed (have containers running)
    2. Both agents are online
    3. The link hasn't already been created

    Args:
        session: Database session
        lab_id: Lab identifier
        log_parts: List to append log messages to
    """
    from app.tasks.link_orchestration import create_deployment_links

    # Check if there are any cross-host links that need creation
    # First, check if any link_states exist with is_cross_host=True and actual_state != "up"
    pending_cross_host = (
        session.query(models.LinkState)
        .filter(
            models.LinkState.lab_id == lab_id,
            models.LinkState.is_cross_host == True,
            models.LinkState.actual_state != "up",
        )
        .count()
    )

    # Also check for links that haven't been categorized yet (no host IDs set)
    uncategorized_links = (
        session.query(models.LinkState)
        .filter(
            models.LinkState.lab_id == lab_id,
            models.LinkState.source_host_id == None,
        )
        .count()
    )

    # Check if there are any links defined that don't have LinkState records yet
    from app.services.topology import TopologyService
    topo_service = TopologyService(session)
    db_links = topo_service.get_links(lab_id)
    existing_link_names = {
        ls.link_name
        for ls in session.query(models.LinkState.link_name)
        .filter(models.LinkState.lab_id == lab_id)
        .all()
    }
    new_links = [l for l in db_links if l.link_name not in existing_link_names]

    # Determine if we need to force VXLAN recreation after agent restarts.
    # If there are cross-host links but no tunnels reported for this lab, rebuild.
    force_recreate = False
    if not pending_cross_host and not uncategorized_links and not new_links:
        cross_host_links = (
            session.query(models.LinkState)
            .filter(
                models.LinkState.lab_id == lab_id,
                models.LinkState.is_cross_host == True,
            )
            .count()
        )
        if cross_host_links > 0:
            placements = (
                session.query(models.NodePlacement)
                .filter(models.NodePlacement.lab_id == lab_id)
                .all()
            )
            host_ids = {p.host_id for p in placements}
            for host_id in host_ids:
                agent = session.get(models.Host, host_id)
                if not agent or not agent_client.is_agent_online(agent):
                    continue
                status = await agent_client.get_overlay_status_from_agent(agent)
                tunnels = [t for t in status.get("tunnels", []) if t.get("lab_id") == lab_id]
                if not tunnels:
                    force_recreate = True
                    break

        if not force_recreate:
            # No cross-host links need creation
            return

    logger.info(
        f"Checking cross-host links for lab {lab_id}: "
        f"{pending_cross_host} pending, {uncategorized_links} uncategorized, {len(new_links)} new"
    )

    # Build host_to_agent map with all online agents
    all_agents = session.query(models.Host).filter(models.Host.status == "online").all()
    host_to_agent: dict[str, models.Host] = {}
    for agent in all_agents:
        if agent_client.is_agent_online(agent):
            host_to_agent[agent.id] = agent

    if not host_to_agent:
        logger.warning(f"No online agents available for cross-host link creation")
        return

    # Call create_deployment_links which handles all the logic:
    # - Creates LinkState records if needed
    # - Determines which links are cross-host based on node placements
    # - Creates VXLAN tunnels for cross-host links where both endpoints are ready
    # - Skips links that are already "up"
    log_parts.append("")
    log_parts.append("=== Phase 4: Cross-Host Links ===")

    try:
        links_ok, links_failed = await create_deployment_links(
            session, lab_id, host_to_agent, log_parts
        )
        if links_ok > 0 or links_failed > 0:
            logger.info(f"Cross-host link creation: {links_ok} OK, {links_failed} failed")
    except Exception as e:
        logger.error(f"Failed to create cross-host links for lab {lab_id}: {e}")
        log_parts.append(f"  Cross-host link creation failed: {e}")


def _get_container_name(lab_id: str, node_name: str, provider: str = "docker") -> str:
    """Get the container name for a node.

    Container naming convention: archetype-{lab_id}-{node_name}

    Lab ID is sanitized and truncated to ~20 chars.

    Args:
        lab_id: Lab identifier
        node_name: Node name in the topology
        provider: Infrastructure provider (unused, kept for compatibility)

    Returns:
        Full container name
    """
    safe_lab_id = re.sub(r'[^a-zA-Z0-9_-]', '', lab_id)[:20]
    safe_node = re.sub(r'[^a-zA-Z0-9_-]', '', node_name)
    return f"archetype-{safe_lab_id}-{safe_node}"
