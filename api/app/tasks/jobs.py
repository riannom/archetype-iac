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
from datetime import datetime, timezone
import redis

from app import agent_client, models, webhooks
from app.agent_client import AgentJobError, AgentUnavailableError
from app.config import settings
from app.db import get_redis, get_session
from app.services.broadcaster import get_broadcaster as _get_broadcaster
from app.services.topology import TopologyService
from app.utils.job import broadcast_job_progress as _broadcast_job_progress
from app.utils.lab import get_node_provider, update_lab_state
from app.events.publisher import (
    emit_deploy_finished,
    emit_destroy_finished,
    emit_job_failed,
    emit_node_placement_changed,
)
from app.metrics import (
    record_job_completed,
    record_job_failed,
    record_job_started,
)
from app.utils.db import (
    release_db_transaction_for_io as _release_db_tx_for_io_impl,
    reset_session_after_db_error as _reset_session_after_db_error_impl,
)
from app.state import (
    HostStatus,
    JobStatus,
    LabState,
    LinkActualState,
)
from app.utils.time import utcnow

logger = logging.getLogger(__name__)
# Backward-compatible symbol for tests/patching call-sites that import directly.
get_broadcaster = _get_broadcaster


def _get_container_name(lab_id: str, node_name: str, provider: str = "docker") -> str:
    """Return stable runtime name for a node based on provider.

    Kept as a compatibility shim for tests/importers; naming logic is
    centralized in app.utils.naming.
    """
    from app.utils.naming import docker_container_name, libvirt_domain_name

    if provider in {"libvirt", "kvm"}:
        return libvirt_domain_name(lab_id, node_name)
    return docker_container_name(lab_id, node_name)


def _normalized_job_action(action: str) -> str:
    """Normalize verbose action strings to bounded metric labels."""
    if not action:
        return "unknown"
    if action.startswith("sync:"):
        return "sync"
    if action.startswith("node:"):
        return "node"
    return action.split(":")[0]


def _as_utc_aware(dt: datetime | None) -> datetime | None:
    """Normalize DB datetimes to UTC-aware values for safe arithmetic."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _job_duration_seconds(job: models.Job) -> float | None:
    started_at = _as_utc_aware(job.started_at)
    completed_at = _as_utc_aware(job.completed_at)
    if started_at and completed_at:
        return max(0.0, (completed_at - started_at).total_seconds())
    return None


def _job_queue_wait_seconds(job: models.Job) -> float | None:
    created_at = _as_utc_aware(job.created_at)
    started_at = _as_utc_aware(job.started_at)
    if created_at and started_at:
        return max(0.0, (started_at - created_at).total_seconds())
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


# Delegate to shared utility; keep underscored aliases for backward compatibility.
_reset_session_after_db_error = _reset_session_after_db_error_impl
_release_db_transaction_for_io = _release_db_tx_for_io_impl


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
        _release_db_transaction_for_io(
            session,
            context=f"agent preflight connectivity check for lab {lab.id}",
        )
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
                _release_db_transaction_for_io(
                    session,
                    context=f"image preflight check for lab {lab.id}",
                )
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
            lock_value = f"agent:{agent_id}:time:{utcnow().isoformat()}"

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


async def _cleanup_network_records_after_destroy(
    session,
    lab_id: str,
    agent: models.Host,
) -> None:
    """Clean up VxlanTunnel and LinkState records after single-host destroy.

    The multi-host destroy path calls teardown_deployment_links() explicitly;
    the single-host path was missing this step, leaking network records.
    This function reuses teardown_deployment_links() when tunnels exist and
    falls back to a direct LinkState delete for same-host-only labs.
    """
    from app.tasks.link_orchestration import teardown_deployment_links

    try:
        tunnels = (
            session.query(models.VxlanTunnel)
            .filter(models.VxlanTunnel.lab_id == lab_id)
            .all()
        )

        if tunnels:
            # Build host_to_agent map from distinct agent IDs in tunnel records
            agent_ids: set[str] = set()
            for t in tunnels:
                agent_ids.add(t.agent_a_id)
                agent_ids.add(t.agent_b_id)

            agents = (
                session.query(models.Host)
                .filter(models.Host.id.in_(agent_ids))
                .all()
            )
            host_to_agent = {a.id: a for a in agents}

            log_parts: list[str] = []
            _release_db_transaction_for_io(
                session,
                context=f"network cleanup after destroy for lab {lab_id}",
            )
            ok, fail = await teardown_deployment_links(
                session, lab_id, host_to_agent, log_parts,
            )
            if log_parts:
                logger.info(
                    "Single-host destroy network cleanup for lab %s: %s",
                    lab_id,
                    "; ".join(log_parts),
                )
        else:
            # No tunnels — still clean up same-host LinkState records
            deleted = (
                session.query(models.LinkState)
                .filter(models.LinkState.lab_id == lab_id)
                .delete(synchronize_session="fetch")
            )
            if deleted:
                session.commit()
                logger.info(
                    "Cleaned up %d same-host LinkState record(s) for lab %s",
                    deleted,
                    lab_id,
                )
    except Exception as e:
        logger.warning(
            "Network cleanup after destroy failed for lab %s: %s", lab_id, e
        )
        try:
            session.rollback()
        except Exception:
            pass


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
        _reset_session_after_db_error(
            session,
            context=f"auto-extract before destroy for lab {lab.id}",
        )
        logger.warning(f"Error during auto-extract before destroy: {e}")
        # Continue with destroy even if extraction fails


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
        _reset_session_after_db_error(
            session,
            context=f"dispatching webhook {event_type} for lab {lab.id}",
        )
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

        node_defs_by_name = {
            node.container_name: node
            for node in (
                session.query(models.Node)
                .filter(models.Node.lab_id == lab_id)
                .all()
            )
        }
        node_states_by_definition_id = {
            node_state.node_definition_id: node_state
            for node_state in (
                session.query(models.NodeState)
                .filter(
                    models.NodeState.lab_id == lab_id,
                    models.NodeState.node_definition_id.is_not(None),
                )
                .all()
            )
            if node_state.node_definition_id
        }

        # Update NodeState records with IP addresses
        for node_info in nodes:
            node_name = node_info.get("name")
            ip_addresses = node_info.get("ip_addresses", [])

            if not node_name:
                continue

            node_def = node_defs_by_name.get(node_name)
            if not node_def:
                logger.debug(
                    "Skipping IP capture for %s/%s: node definition not found",
                    lab_id,
                    node_name,
                )
                continue
            node_state = node_states_by_definition_id.get(node_def.id)

            if node_state and ip_addresses:
                # Set primary IP (first in list)
                node_state.management_ip = ip_addresses[0] if ip_addresses else None
                # Store all IPs as JSON
                node_state.management_ips_json = json.dumps(ip_addresses)
                logger.debug(f"Captured IPs for {node_name}: {ip_addresses}")

        session.commit()
        logger.info(f"Captured management IPs for {len(nodes)} nodes in lab {lab_id}")

    except Exception as e:
        _reset_session_after_db_error(
            session,
            context=f"capturing node IPs for lab {lab_id}",
        )
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
        placement_moves: list[tuple[str, str, str]] = []
        runtime_ids_by_name: dict[str, str] = {}
        agent = session.get(models.Host, agent_id)
        if agent:
            try:
                status_result = await agent_client.get_lab_status_from_agent(agent, lab_id)
                runtime_ids_by_name = {
                    node.get("name", ""): node.get("runtime_id", "")
                    for node in status_result.get("nodes", [])
                    if node.get("name") and node.get("runtime_id")
                }
            except Exception as status_error:
                logger.warning(
                    "Failed to collect runtime IDs for lab %s on agent %s: %s",
                    lab_id,
                    agent_id,
                    status_error,
                )
        node_defs = (
            session.query(models.Node)
            .filter(
                models.Node.lab_id == lab_id,
                models.Node.container_name.in_(node_names),
            )
            .all()
        )
        node_defs_by_name = {node.container_name: node for node in node_defs}
        placement_rows = (
            session.query(models.NodePlacement)
            .filter(
                models.NodePlacement.lab_id == lab_id,
                models.NodePlacement.node_definition_id.in_([n.id for n in node_defs]),
            )
            .all()
            if node_defs
            else []
        )
        placements_by_node_definition_id = {
            placement.node_definition_id: placement
            for placement in placement_rows
            if placement.node_definition_id
        }

        for node_name in node_names:
            node_def = node_defs_by_name.get(node_name)
            if not node_def:
                logger.warning(
                    "Skipping placement update for %s/%s: node definition not found",
                    lab_id,
                    node_name,
                )
                continue
            existing = placements_by_node_definition_id.get(node_def.id)

            if existing:
                # Update existing placement
                old_host_id = existing.host_id
                existing.host_id = agent_id
                existing.status = status
                existing.node_name = node_def.container_name
                runtime_id = runtime_ids_by_name.get(node_def.container_name)
                if runtime_id:
                    existing.runtime_id = runtime_id
                if old_host_id and old_host_id != agent_id:
                    placement_moves.append((node_def.container_name, old_host_id, agent_id))
            else:
                # Create new placement with FK
                placement = models.NodePlacement(
                    lab_id=lab_id,
                    node_name=node_def.container_name,
                    node_definition_id=node_def.id,
                    host_id=agent_id,
                    runtime_id=runtime_ids_by_name.get(node_def.container_name),
                    status=status,
                )
                session.add(placement)
                placements_by_node_definition_id[node_def.id] = placement

        session.commit()
        logger.info(f"Updated placements for {len(node_names)} nodes in lab {lab_id} on agent {agent_id}")

        for node_name, old_agent_id, new_agent_id in placement_moves:
            try:
                await emit_node_placement_changed(
                    lab_id=lab_id,
                    node_name=node_name,
                    agent_id=new_agent_id,
                    old_agent_id=old_agent_id,
                )
            except Exception as emit_error:
                logger.warning(
                    "Failed to emit NODE_PLACEMENT_CHANGED for %s in lab %s: %s",
                    node_name,
                    lab_id,
                    emit_error,
                )

    except Exception as e:
        _reset_session_after_db_error(
            session,
            context=f"updating node placements for lab {lab_id}",
        )
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
    the old agent. This function removes only stale containers for the lab
    while preserving any nodes that are still assigned to that host.

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

            node_defs = (
                session.query(models.Node)
                .filter(models.Node.lab_id == lab_id)
                .all()
            )
            node_defs_by_id = {node.id: node for node in node_defs}

            # Keep any nodes still assigned to this old agent (partial migration).
            keep_node_names = [
                node_defs_by_id[p.node_definition_id].container_name
                for p in (
                    session.query(models.NodePlacement)
                    .filter(
                        models.NodePlacement.lab_id == lab_id,
                        models.NodePlacement.host_id == old_agent_id,
                        models.NodePlacement.node_definition_id.is_not(None),
                    )
                    .all()
                )
                if p.node_definition_id in node_defs_by_id
            ]
            keep_node_name_set = set(keep_node_names)
            nodes_by_name = {
                node.container_name: node
                for node in node_defs
                if node.container_name
            }
            candidate_cleanup_names = [
                node_name
                for node_name in nodes_by_name.keys()
                if node_name not in keep_node_name_set
            ]

            # Check if agent is online before attempting cleanup
            if not agent_client.is_agent_online(old_agent):
                logger.info(f"Skipping orphan cleanup on offline agent {old_agent_id}")
                log_parts.append(f"Note: Skipped cleanup on offline agent {old_agent.name}")
                from app.tasks.migration_cleanup import enqueue_node_migration_cleanup

                queued = 0
                for node_name in candidate_cleanup_names:
                    node = nodes_by_name.get(node_name)
                    provider = get_node_provider(node, session) if node else "docker"
                    enqueue_node_migration_cleanup(
                        session,
                        lab_id,
                        node_name,
                        old_agent_id,
                        provider=provider,
                        reason="Old agent offline during orphan cleanup",
                    )
                    queued += 1
                if queued:
                    session.commit()
                    log_parts.append(
                        f"  Queued deferred cleanup for {queued} node(s) on offline agent {old_agent.name}"
                    )
                continue

            logger.info(
                f"Cleaning up orphan containers for lab {lab_id} on old agent "
                f"{old_agent_id} (keeping {len(keep_node_names)} assigned node(s))"
            )
            log_parts.append(f"Cleaning up orphans on old agent {old_agent.name}...")

            result = await agent_client.cleanup_lab_orphans(
                old_agent,
                lab_id,
                keep_node_names,
            )
            errors = result.get("errors", [])
            removed = result.get("removed_containers", [])

            if errors:
                error_msg = "; ".join(str(e) for e in errors)
                log_parts.append(f"  Orphan cleanup failed on {old_agent.name}: {error_msg}")
                logger.warning(f"Orphan cleanup failed on agent {old_agent_id}: {error_msg}")
            elif removed:
                log_parts.append(
                    f"  Orphan cleanup removed {len(removed)} container(s) on "
                    f"{old_agent.name}: {', '.join(removed)}"
                )
            else:
                log_parts.append(f"  Orphan cleanup: no stale containers found on {old_agent.name}")

    except Exception as e:
        _reset_session_after_db_error(
            session,
            context=f"orphan cleanup for lab {lab_id}",
        )
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
                job.completed_at = utcnow()
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
                job.completed_at = utcnow()
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

            _release_db_transaction_for_io(
                session,
                context=f"preflight checks for job {job_id}",
            )
            preflight_ok, preflight_error = await _run_job_preflight_checks(
                session, lab, agent, action,
            )
            if not preflight_ok:
                job.status = JobStatus.FAILED.value
                job.completed_at = utcnow()
                job.log_path = preflight_error or "ERROR: Preflight check failed"
                update_lab_state(session, lab_id, LabState.ERROR.value, error="Preflight check failed")
                _record_failed(job, action)
                session.commit()
                logger.warning(f"Job {job_id} failed preflight on agent {agent.id}")
                return

            # Update job with agent assignment and start time
            job.status = JobStatus.RUNNING.value
            job.agent_id = agent.id
            job.started_at = utcnow()
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
                    _release_db_transaction_for_io(
                        session,
                        context=f"deploy request for job {job_id}",
                    )
                    result = await agent_client.deploy_to_agent(
                        agent, job_id, lab_id,
                        topology=topology_json,  # Use JSON, not YAML
                        provider=provider,
                    )
                elif action == "down":
                    # Auto-extract configs before destroying (if enabled)
                    await _auto_extract_configs_before_destroy(session, lab, agent)
                    _release_db_transaction_for_io(
                        session,
                        context=f"destroy request for job {job_id}",
                    )
                    result = await agent_client.destroy_on_agent(agent, job_id, lab_id)

                    # Clean up network records (VxlanTunnel + LinkState) that the
                    # single-host path would otherwise leak.  The multi-host path
                    # already calls teardown_deployment_links(); this makes the
                    # two paths behave identically.
                    if result.get("status") == "completed":
                        await _cleanup_network_records_after_destroy(
                            session, lab_id, agent,
                        )
                else:
                    # Note: node:start/stop actions are deprecated - use sync:node:{id} instead
                    result = {"status": "failed", "error_message": f"Unknown action: {action}"}

                # Update job based on result
                job.completed_at = utcnow()

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
                        _release_db_transaction_for_io(
                            session,
                            context=f"capture node IPs for job {job_id}",
                        )
                        await _capture_node_ips(session, lab_id, agent)
                        # Dispatch webhook for successful deploy
                        _release_db_transaction_for_io(
                            session,
                            context=f"deploy webhook dispatch for job {job_id}",
                        )
                        await _dispatch_webhook("lab.deploy_complete", lab, job, session)
                        asyncio.create_task(emit_deploy_finished(lab_id, agent_id=agent.id, job_id=job_id))
                    elif action == "down":
                        update_lab_state(session, lab_id, LabState.STOPPED.value)
                        # Dispatch webhook for destroy complete
                        _release_db_transaction_for_io(
                            session,
                            context=f"destroy webhook dispatch for job {job_id}",
                        )
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
                        _release_db_transaction_for_io(
                            session,
                            context=f"failed deploy webhook dispatch for job {job_id}",
                        )
                        await _dispatch_webhook("lab.deploy_failed", lab, job, session)
                    else:
                        _release_db_transaction_for_io(
                            session,
                            context=f"failed job webhook dispatch for job {job_id}",
                        )
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
                job.completed_at = utcnow()
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
                job.completed_at = utcnow()
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
                job.completed_at = utcnow()
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
            _reset_session_after_db_error(
                session,
                context=f"critical error handling for job {job_id}",
            )
            # Catch-all for any errors during error handling itself
            logger.exception(f"Critical error in job {job_id}: {e}")


# ---------------------------------------------------------------------------
# Re-exports: symbols that moved to sibling modules but are still imported
# from ``app.tasks.jobs`` by external callers.  Keeping them here avoids
# changing any import site across the codebase.
# ---------------------------------------------------------------------------
from app.tasks.jobs_multihost import (  # noqa: F401, E402
    run_multihost_deploy,
    run_multihost_destroy,
)


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
                job.completed_at = utcnow()
                job.log_path = f"ERROR: Lab {lab_id} not found"
                session.commit()
                return

            manager = NodeLifecycleManager(session, lab, job, node_ids, provider)
            await manager.execute()

        except Exception as e:
            logger.exception(f"Job {job_id} failed with unexpected error: {e}")
            try:
                session.rollback()
                job = session.get(models.Job, job_id)
                if job:
                    job.status = JobStatus.FAILED.value
                    job.completed_at = utcnow()
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
            models.LinkState.source_host_id.is_(None),
        )
        .count()
    )

    # Check if there are any links defined that don't have LinkState records yet
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
                _release_db_transaction_for_io(
                    session,
                    context=f"overlay status probe for lab {lab_id}",
                )
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
            _release_db_transaction_for_io(
                session,
                context=f"cross-host link creation for lab {lab_id}",
            )
            links_ok, links_failed = await create_deployment_links(
                session, lab_id, host_to_agent, log_parts
            )
            if links_ok > 0 or links_failed > 0:
                logger.info(f"Cross-host link creation: {links_ok} OK, {links_failed} failed")
        except Exception as e:
            logger.error(f"Failed to create cross-host links for lab {lab_id}: {e}")
            log_parts.append(f"  Cross-host link creation failed: {e}")
