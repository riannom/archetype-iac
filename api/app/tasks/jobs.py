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
from app.services.broadcaster import get_broadcaster
from app.services.topology import TopologyService
from app.utils.lab import update_lab_state
from app.events.publisher import emit_deploy_finished, emit_destroy_finished, emit_job_failed
from app.metrics import record_job_completed, record_job_failed, record_job_started
from app.state import (
    HostStatus,
    JobStatus,
    LabState,
    LinkActualState,
)

logger = logging.getLogger(__name__)


def _normalized_job_action(action: str) -> str:
    """Normalize verbose action strings to bounded metric labels."""
    if not action:
        return "unknown"
    if action.startswith("sync:"):
        return "sync"
    if action.startswith("node:"):
        return "node"
    return action.split(":")[0]


def _job_duration_seconds(job: models.Job) -> float | None:
    if job.started_at and job.completed_at:
        return max(0.0, (job.completed_at - job.started_at).total_seconds())
    return None


def _job_queue_wait_seconds(job: models.Job) -> float | None:
    if job.created_at and job.started_at:
        return max(0.0, (job.started_at - job.created_at).total_seconds())
    return None


def _record_started(job: models.Job, action: str) -> None:
    record_job_started(
        _normalized_job_action(action),
        queue_wait_seconds=_job_queue_wait_seconds(job),
    )


def _record_failed(
    job: models.Job,
    action: str,
    *,
    duration_seconds: float | None = None,
) -> None:
    record_job_failed(
        _normalized_job_action(action),
        duration_seconds=duration_seconds,
        failure_message=job.log_path,
    )


async def _run_job_preflight_checks(
    session,
    lab: models.Lab,
    agent: models.Host,
    action: str,
) -> tuple[bool, str | None]:
    """Fail fast on preconditions that would otherwise become long timeouts."""
    if action not in {"up", "down"}:
        return True, None

    try:
        await agent_client.get_lab_status_from_agent(agent, lab.id)
    except Exception as e:
        return False, (
            "ERROR: Agent preflight connectivity check failed.\n\n"
            f"Agent: {agent.name or agent.id}\n"
            f"Details: {e}\n\n"
            "Aborting before deployment/destroy to avoid long timeout."
        )

    if action == "up" and settings.image_sync_enabled and settings.image_sync_pre_deploy_check:
        try:
            from app.tasks.image_sync import ensure_images_for_deployment

            topo_service = TopologyService(session)
            image_refs = topo_service.get_required_images(lab.id)
            if image_refs:
                image_to_nodes = topo_service.get_image_to_nodes_map(lab.id)
                all_ready, missing, _ = await ensure_images_for_deployment(
                    agent.id,
                    image_refs,
                    timeout=min(settings.image_sync_timeout, 300),
                    database=session,
                    lab_id=lab.id,
                    image_to_nodes=image_to_nodes,
                )
                if not all_ready and missing:
                    missing_str = ", ".join(missing[:5])
                    if len(missing) > 5:
                        missing_str += f" (+{len(missing) - 5} more)"
                    return False, (
                        "ERROR: Preflight image check failed.\n\n"
                        f"Agent: {agent.name or agent.id}\n"
                        f"Missing images: {missing_str}\n\n"
                        "Upload/sync required images before retrying."
                    )
        except Exception as e:
            return False, (
                "ERROR: Preflight image validation failed unexpectedly.\n\n"
                f"Details: {e}\n\n"
                "Aborting before deployment to avoid long timeout."
            )

    return True, None


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

        # Create snapshots with auto_stop type via ConfigService
        from app.services.config_service import ConfigService
        config_svc = ConfigService(session)

        # Build node_name -> device_kind lookup
        lab_nodes = (
            session.query(models.Node)
            .filter(models.Node.lab_id == lab.id)
            .all()
        )
        node_device_map = {n.container_name: n.device for n in lab_nodes}

        snapshots_created = 0
        for config_data in configs:
            node_name = config_data.get("node_name")
            content = config_data.get("content")
            if not node_name or not content:
                continue

            snapshot = config_svc.save_extracted_config(
                lab_id=lab.id,
                node_name=node_name,
                content=content,
                snapshot_type="auto_stop",
                device_kind=node_device_map.get(node_name),
                set_as_active=False,  # Don't update active on auto-stop
            )
            if snapshot:
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
                job.status = JobStatus.FAILED.value
                job.completed_at = datetime.now(timezone.utc)
                job.log_path = f"ERROR: Lab {lab_id} not found"
                _record_failed(job, action)
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
                job.status = JobStatus.FAILED.value
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
                update_lab_state(session, lab_id, LabState.ERROR.value, error="No healthy agent available")
                _record_failed(job, action)
                session.commit()
                logger.warning(f"Job {job_id} failed: no healthy agent available for provider {provider}")
                return

            preflight_ok, preflight_error = await _run_job_preflight_checks(
                session, lab, agent, action,
            )
            if not preflight_ok:
                job.status = JobStatus.FAILED.value
                job.completed_at = datetime.now(timezone.utc)
                job.log_path = preflight_error or "ERROR: Preflight check failed"
                update_lab_state(session, lab_id, LabState.ERROR.value, error="Preflight check failed")
                _record_failed(job, action)
                session.commit()
                logger.warning(f"Job {job_id} failed preflight on agent {agent.id}")
                return

            # Update job with agent assignment and start time
            job.status = JobStatus.RUNNING.value
            job.agent_id = agent.id
            job.started_at = datetime.now(timezone.utc)
            session.commit()
            _record_started(job, action)

            # Broadcast job started
            await _broadcast_job_progress(
                lab_id, job_id, action, "running",
                progress_message=f"Job started on agent {agent.name or agent.id}"
            )

            # Update lab state based on action
            if action == "up":
                update_lab_state(session, lab_id, LabState.STARTING.value, agent_id=agent.id)
                # Dispatch webhook for deploy started
                await _dispatch_webhook("lab.deploy_started", lab, job, session)
            elif action == "down":
                update_lab_state(session, lab_id, LabState.STOPPING.value, agent_id=agent.id)

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
                    job.status = JobStatus.COMPLETED.value
                    log_content = "Job completed successfully.\n\n"

                    # Broadcast job completed
                    await _broadcast_job_progress(
                        lab_id, job_id, action, "completed",
                        progress_message="Job completed successfully"
                    )

                    # Update lab state based on completed action
                    if action == "up":
                        update_lab_state(session, lab_id, LabState.RUNNING.value, agent_id=agent.id)
                        # Capture management IPs for IaC workflows
                        await _capture_node_ips(session, lab_id, agent)
                        # Dispatch webhook for successful deploy
                        await _dispatch_webhook("lab.deploy_complete", lab, job, session)
                        asyncio.create_task(emit_deploy_finished(lab_id, agent_id=agent.id, job_id=job_id))
                    elif action == "down":
                        update_lab_state(session, lab_id, LabState.STOPPED.value)
                        # Dispatch webhook for destroy complete
                        await _dispatch_webhook("lab.destroy_complete", lab, job, session)
                        asyncio.create_task(emit_destroy_finished(lab_id, agent_id=agent.id, job_id=job_id))

                else:
                    job.status = JobStatus.FAILED.value
                    error_msg = result.get('error_message', 'Unknown error')
                    log_content = f"Job failed.\n\nError: {error_msg}\n\n"

                    # Broadcast job failed
                    await _broadcast_job_progress(
                        lab_id, job_id, action, "failed",
                        error_message=error_msg
                    )

                    # Update lab state to error
                    update_lab_state(session, lab_id, LabState.ERROR.value, error=error_msg)

                    # Dispatch webhook for failed job
                    if action == "up":
                        await _dispatch_webhook("lab.deploy_failed", lab, job, session)
                    else:
                        await _dispatch_webhook("job.failed", lab, job, session)
                    asyncio.create_task(emit_job_failed(lab_id, job_id=job_id, job_action=action))

                # Append stdout/stderr if present
                stdout = result.get("stdout", "").strip()
                stderr = result.get("stderr", "").strip()
                if stdout:
                    log_content += f"=== STDOUT ===\n{stdout}\n\n"
                if stderr:
                    log_content += f"=== STDERR ===\n{stderr}\n"

                job.log_path = log_content.strip()
                duration_seconds = _job_duration_seconds(job)
                if job.status == JobStatus.COMPLETED.value:
                    record_job_completed(
                        _normalized_job_action(action),
                        duration_seconds=duration_seconds or 0.0,
                    )
                elif job.status == JobStatus.FAILED.value:
                    _record_failed(job, action, duration_seconds=duration_seconds)
                session.commit()
                logger.info(f"Job {job_id} completed with status: {job.status}")

            except AgentUnavailableError as e:
                job.status = JobStatus.FAILED.value
                job.completed_at = datetime.now(timezone.utc)
                job.log_path = (
                    f"ERROR: Agent became unavailable during job execution.\n\n"
                    f"Agent ID: {e.agent_id or 'unknown'}\n"
                    f"Details: {e.message}\n\n"
                    f"The job could not be completed. The lab may be in an inconsistent state.\n"
                    f"Consider checking the lab status and retrying the operation."
                )

                # Update lab state to unknown (we don't know what state it's in)
                update_lab_state(session, lab_id, LabState.UNKNOWN.value, error=f"Agent unavailable: {e.message}")

                _record_failed(job, action, duration_seconds=_job_duration_seconds(job))
                session.commit()
                logger.error(f"Job {job_id} failed: agent unavailable - {e.message}")

                # Mark agent as offline if we know which one failed
                if e.agent_id:
                    await agent_client.mark_agent_offline(session, e.agent_id)

            except AgentJobError as e:
                job.status = JobStatus.FAILED.value
                job.completed_at = datetime.now(timezone.utc)
                log_content = f"ERROR: Job execution failed on agent.\n\nDetails: {e.message}\n\n"
                if e.stdout:
                    log_content += f"=== STDOUT ===\n{e.stdout}\n\n"
                if e.stderr:
                    log_content += f"=== STDERR ===\n{e.stderr}\n"
                job.log_path = log_content.strip()

                # Update lab state to error
                update_lab_state(session, lab_id, LabState.ERROR.value, error=e.message)

                _record_failed(job, action, duration_seconds=_job_duration_seconds(job))
                session.commit()
                logger.error(f"Job {job_id} failed: agent job error - {e.message}")

            except Exception as e:
                job.status = JobStatus.FAILED.value
                job.completed_at = datetime.now(timezone.utc)
                job.log_path = (
                    f"ERROR: Unexpected error during job execution.\n\n"
                    f"Type: {type(e).__name__}\n"
                    f"Details: {str(e)}\n\n"
                    f"Please report this error if it persists."
                )

                # Update lab state to error
                update_lab_state(session, lab_id, LabState.ERROR.value, error=str(e))

                _record_failed(job, action, duration_seconds=_job_duration_seconds(job))
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
                job.status = JobStatus.FAILED.value
                job.completed_at = datetime.now(timezone.utc)
                job.log_path = f"ERROR: Lab {lab_id} not found"
                _record_failed(job, "up")
                session.commit()
                return

            # Use TopologyService to analyze placements from DATABASE (not YAML)
            # This is the key fix: nodes.host_id is the source of truth
            topo_service = TopologyService(session)
            nodes = topo_service.get_nodes(lab_id)
            len(nodes)

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
                    job.status = JobStatus.FAILED.value
                    job.completed_at = datetime.now(timezone.utc)
                    job.log_path = (
                        f"ERROR: {len(unplaced_nodes)} nodes have no host assignment "
                        f"and no default agent is available"
                    )
                    update_lab_state(session, lab_id, LabState.ERROR.value, error="No agent for unplaced nodes")
                    _record_failed(job, "up")
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
            job.status = JobStatus.RUNNING.value
            job.started_at = datetime.now(timezone.utc)
            session.commit()
            _record_started(job, "up")

            # Broadcast job started
            await _broadcast_job_progress(
                lab_id, job_id, "up", "running",
                progress_message=f"Starting multi-host deployment ({len(analysis.placements)} hosts)"
            )

            update_lab_state(session, lab_id, LabState.STARTING.value)

            # Dispatch webhook for deploy started
            await _dispatch_webhook("lab.deploy_started", lab, job, session)

            # Map host_id to agent objects
            host_to_agent: dict[str, models.Host] = {}
            missing_hosts = []

            for host_id in analysis.placements:
                agent = session.get(models.Host, host_id)
                if agent and agent_client.is_agent_online(agent):
                    try:
                        await agent_client.get_lab_status_from_agent(agent, lab_id)
                    except Exception as e:
                        missing_hosts.append(f"{host_id} (preflight connectivity failed: {e})")
                        continue
                    host_to_agent[host_id] = agent
                else:
                    missing_hosts.append(host_id)

            if missing_hosts:
                error_msg = f"Missing or unhealthy agents for hosts: {', '.join(missing_hosts)}"
                job.status = JobStatus.FAILED.value
                job.completed_at = datetime.now(timezone.utc)
                job.log_path = f"ERROR: {error_msg}"
                update_lab_state(session, lab_id, LabState.ERROR.value, error=error_msg)
                _record_failed(job, "up", duration_seconds=_job_duration_seconds(job))
                session.commit()
                logger.error(f"Job {job_id} failed: {error_msg}")
                return

            log_parts: list[str] = []

            # --- Resource capacity check (pre-deploy gate) ---
            if settings.resource_validation_enabled:
                from app.services.resource_capacity import (
                    check_multihost_capacity,
                    format_capacity_error,
                    format_capacity_warnings,
                )

                # Build host_id -> device_types mapping from database nodes
                host_device_map: dict[str, list[str]] = {}
                for node in nodes:
                    hid = node.host_id
                    if hid:
                        if hid not in host_device_map:
                            host_device_map[hid] = []
                        host_device_map[hid].append(node.device or "linux")

                cap_results = check_multihost_capacity(host_device_map, session)
                any_errors = any(not r.fits for r in cap_results.values())

                if any_errors:
                    error_msg = format_capacity_error(cap_results)
                    logger.warning(f"Job {job_id}: Multi-host resource check failed: {error_msg}")
                    job.status = JobStatus.FAILED.value
                    job.completed_at = datetime.now(timezone.utc)
                    job.log_path = f"ERROR: {error_msg}"
                    update_lab_state(session, lab_id, LabState.ERROR.value, error="Insufficient resources")
                    _record_failed(job, "up", duration_seconds=_job_duration_seconds(job))
                    session.commit()
                    return

                cap_warnings = format_capacity_warnings(cap_results)
                if cap_warnings:
                    for w in cap_warnings:
                        logger.warning(f"Job {job_id}: Resource warning: {w}")
                        log_parts.append(f"WARNING: {w}")

            # Deploy to each host in parallel using JSON topology from database
            deploy_tasks = []
            deploy_results: dict[str, dict] = {}
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

                job.status = JobStatus.FAILED.value
                job.completed_at = datetime.now(timezone.utc)
                job.log_path = "\n".join(log_parts)
                update_lab_state(session, lab_id, LabState.ERROR.value, error="Deployment failed on one or more hosts")
                _record_failed(job, "up", duration_seconds=_job_duration_seconds(job))
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
                log_parts.append("\n=== Link Setup Summary ===")
                log_parts.append(f"Links: {links_ok} OK, {links_failed} failed")
                log_parts.append("\nNote: Containers are deployed but some links failed.")
                job.status = JobStatus.FAILED.value
                job.completed_at = datetime.now(timezone.utc)
                job.log_path = "\n".join(log_parts)
                update_lab_state(session, lab_id, LabState.ERROR.value, error=f"Link setup failed: {links_failed} link(s)")
                _record_failed(job, "up", duration_seconds=_job_duration_seconds(job))
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
            job.status = JobStatus.COMPLETED.value
            job.completed_at = datetime.now(timezone.utc)
            job.log_path = "\n".join(log_parts)
            record_job_completed("up", duration_seconds=_job_duration_seconds(job) or 0.0)

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
            asyncio.create_task(emit_deploy_finished(lab_id, job_id=job_id))

            logger.info(f"Job {job_id} completed: multi-host deployment successful")

        except Exception as e:
            logger.exception(f"Job {job_id} failed with unexpected error: {e}")
            try:
                job = session.get(models.Job, job_id)
                lab = session.get(models.Lab, lab_id)
                if job:
                    job.status = JobStatus.FAILED.value
                    job.completed_at = datetime.now(timezone.utc)
                    job.log_path = f"ERROR: Unexpected error: {e}"
                    update_lab_state(session, lab_id, LabState.ERROR.value, error=str(e))
                    _record_failed(job, "up", duration_seconds=_job_duration_seconds(job))
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
                job.status = JobStatus.FAILED.value
                job.completed_at = datetime.now(timezone.utc)
                job.log_path = f"ERROR: Lab {lab_id} not found"
                _record_failed(job, "down")
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
            job.status = JobStatus.RUNNING.value
            job.started_at = datetime.now(timezone.utc)
            session.commit()
            _record_started(job, "down")

            update_lab_state(session, lab_id, LabState.STOPPING.value)

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
                job.status = JobStatus.FAILED.value
                job.completed_at = datetime.now(timezone.utc)
                job.log_path = f"ERROR: {error_msg}"
                update_lab_state(session, lab_id, LabState.ERROR.value, error=error_msg)
                _record_failed(job, "down", duration_seconds=_job_duration_seconds(job))
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

            # Clean up any remaining LinkState records
            # Belt-and-suspenders: teardown_deployment_links should have
            # deleted these, but ensures cleanup even if VXLAN teardown
            # was skipped (e.g., no tunnels existed)
            remaining_link_states = (
                session.query(models.LinkState)
                .filter(models.LinkState.lab_id == lab_id)
                .all()
            )
            if remaining_link_states:
                for ls in remaining_link_states:
                    session.delete(ls)
                session.flush()

            # Update job status
            if all_success:
                job.status = JobStatus.COMPLETED.value
                update_lab_state(session, lab_id, LabState.STOPPED.value)
            else:
                # Use completed_with_warnings for partial failures
                # This provides visibility that cleanup may be incomplete
                job.status = "completed_with_warnings"
                update_lab_state(session, lab_id, LabState.STOPPED.value)
                log_parts.append("\nWARNING: Some hosts may have had issues during destroy")
                log_parts.append("Containers may need manual cleanup on failed hosts.")

            job.completed_at = datetime.now(timezone.utc)
            job.log_path = "\n".join(log_parts)
            if job.status == JobStatus.COMPLETED.value:
                record_job_completed("down", duration_seconds=_job_duration_seconds(job) or 0.0)
            else:
                # completed_with_warnings still reflects a completed destroy action.
                record_job_completed("down", duration_seconds=_job_duration_seconds(job) or 0.0)
            session.commit()

            # Dispatch webhook for destroy complete
            await _dispatch_webhook("lab.destroy_complete", lab, job, session)
            asyncio.create_task(emit_destroy_finished(lab_id, job_id=job_id))

            logger.info(f"Job {job_id} completed: multi-host destroy {'successful' if all_success else 'with warnings'}")

        except Exception as e:
            logger.exception(f"Job {job_id} failed with unexpected error: {e}")
            try:
                job = session.get(models.Job, job_id)
                if job:
                    job.status = JobStatus.FAILED.value
                    job.completed_at = datetime.now(timezone.utc)
                    job.log_path = f"ERROR: Unexpected error: {e}"
                    update_lab_state(session, lab_id, LabState.ERROR.value, error=str(e))
                    _record_failed(job, "down", duration_seconds=_job_duration_seconds(job))
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

    Thin wrapper that delegates to NodeLifecycleManager.
    See api/app/tasks/node_lifecycle.py for the full implementation.

    Args:
        job_id: The job ID
        lab_id: The lab ID
        node_ids: List of node IDs to reconcile
        provider: Provider for the job (default: docker)
    """
    from app.tasks.node_lifecycle import NodeLifecycleManager

    with get_session() as session:
        try:
            job = session.get(models.Job, job_id)
            if not job:
                logger.error(f"Job {job_id} not found in database")
                return

            lab = session.get(models.Lab, lab_id)
            if not lab:
                logger.error(f"Lab {lab_id} not found in database")
                job.status = JobStatus.FAILED.value
                job.completed_at = datetime.now(timezone.utc)
                job.log_path = f"ERROR: Lab {lab_id} not found"
                session.commit()
                return

            manager = NodeLifecycleManager(session, lab, job, node_ids, provider)
            await manager.execute()

        except Exception as e:
            logger.exception(f"Job {job_id} failed with unexpected error: {e}")
            try:
                job = session.get(models.Job, job_id)
                if job:
                    job.status = JobStatus.FAILED.value
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

    Uses link_ops_lock to serialize link_states modifications and prevent
    deadlocks from concurrent flush operations.

    Args:
        session: Database session
        lab_id: Lab identifier
        log_parts: List to append log messages to
    """
    from app.tasks.link_orchestration import create_deployment_links
    from app.utils.locks import link_ops_lock

    # Check if there are any cross-host links that need creation
    # First, check if any link_states exist with is_cross_host=True and actual_state != "up"
    pending_cross_host = (
        session.query(models.LinkState)
        .filter(
            models.LinkState.lab_id == lab_id,
            models.LinkState.is_cross_host,
            models.LinkState.actual_state != LinkActualState.UP.value,
        )
        .count()
    )

    # Also check for links that haven't been categorized yet (no host IDs set)
    uncategorized_links = (
        session.query(models.LinkState)
        .filter(
            models.LinkState.lab_id == lab_id,
            models.LinkState.source_host_id is None,
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
    new_links = [lnk for lnk in db_links if lnk.link_name not in existing_link_names]

    # Determine if we need to force VXLAN recreation after agent restarts.
    # If there are cross-host links but no tunnels reported for this lab, rebuild.
    force_recreate = False
    if not pending_cross_host and not uncategorized_links and not new_links:
        cross_host_links = (
            session.query(models.LinkState)
            .filter(
                models.LinkState.lab_id == lab_id,
                models.LinkState.is_cross_host,
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
                link_tunnels = [t for t in status.get("link_tunnels", []) if t.get("lab_id") == lab_id]
                if not tunnels and not link_tunnels:
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
    all_agents = session.query(models.Host).filter(models.Host.status == HostStatus.ONLINE.value).all()
    host_to_agent: dict[str, models.Host] = {}
    for agent in all_agents:
        if agent_client.is_agent_online(agent):
            host_to_agent[agent.id] = agent

    if not host_to_agent:
        logger.warning("No online agents available for cross-host link creation")
        return

    # Call create_deployment_links which handles all the logic:
    # - Creates LinkState records if needed
    # - Determines which links are cross-host based on node placements
    # - Creates VXLAN tunnels for cross-host links where both endpoints are ready
    # - Skips links that are already "up"
    log_parts.append("")
    log_parts.append("=== Phase 4: Cross-Host Links ===")

    # Serialize link_states modifications via Redis lock to prevent deadlocks
    with link_ops_lock(lab_id) as lock_acquired:
        if not lock_acquired:
            logger.debug(
                f"Skipping cross-host link creation for lab {lab_id}: "
                f"link ops lock held by another operation"
            )
            return

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
