"""Job health monitoring background task.

This task runs periodically to detect and handle stuck jobs:
1. Jobs stuck in "running" state past their timeout
2. Jobs stuck in "queued" state without agent assignment
3. Jobs assigned to offline agents

Stuck jobs are either retried (with agent failover) or marked as failed.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from app import agent_client, models
from app.config import settings
from app.db import get_session
from app.utils.job import get_job_timeout, is_job_stuck
from app.utils.async_tasks import safe_create_task
from app.state import (
    HostStatus,
    JobStatus,
    LabState,
    NodeActualState,
)

logger = logging.getLogger(__name__)


def _timed_out_job_is_non_retryable(action: str, log_text: str | None) -> tuple[bool, str | None]:
    """Detect timeout failures that are deterministic and unlikely to recover by retrying."""
    if not log_text:
        return False, None

    text = log_text.lower()
    checks: list[tuple[tuple[str, ...], str]] = [
        (
            (
                "no image found",
                "no image available",
                "required images not available on agent",
                "upload/sync required images",
            ),
            "missing_image",
        ),
        (
            (
                "explicit host assignments failed",
                "missing or unhealthy agents for hosts",
                "assigned host",
                "no healthy agent available",
            ),
            "host_assignment_or_agent_unavailable",
        ),
        (
            (
                "libvirt error",
                "domain not found",
                "unsupported configuration",
            ),
            "libvirt_runtime_error",
        ),
        (
            (
                "per-link tunnel creation failed",
                "could not find ovs port",
            ),
            "link_tunnel_creation_failed",
        ),
    ]

    for needles, reason in checks:
        if any(needle in text for needle in needles):
            return True, reason

    # Sync retries are especially expensive; avoid retrying known partial-failure signatures.
    if action.startswith("sync:") or action == "sync":
        if "completed with " in text and "error" in text:
            return True, "sync_partial_failure"

    return False, None


def _is_file_path(value: str | None) -> bool:
    """Check if a string is a valid file path (not inline content).

    Returns True only if the value looks like a file path and the file exists.
    """
    if not value:
        return False
    # Content often contains newlines, paths don't
    if "\n" in value:
        return False
    # Paths are typically short
    if len(value) > 4096:
        return False
    # Must be an absolute path
    if not value.startswith("/"):
        return False
    try:
        return Path(value).is_file()
    except OSError:
        return False


def _read_log_for_classification(value: str | None) -> str | None:
    """Return inline log content or a tail of file-backed logs for signature checks."""
    if not value:
        return None
    if not _is_file_path(value):
        return value
    try:
        text = Path(value).read_text(errors="ignore")
        return text[-12000:] if len(text) > 12000 else text
    except Exception:
        return None


async def check_stuck_jobs():
    """Find and handle jobs that are stuck.

    This function:
    1. Finds jobs in 'running' state past their timeout
    2. Finds jobs in 'queued' state for too long without starting
    3. Finds jobs assigned to offline agents
    4. Either retries the job (if under max_retries) or marks it as failed
    """
    with get_session() as session:
        try:
            now = datetime.now(timezone.utc)

            # Find all active jobs (queued or running)
            active_jobs = (
                session.query(models.Job)
                .filter(models.Job.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value]))
                .all()
            )

            if not active_jobs:
                return

            # Check each job for stuck state
            for job in active_jobs:
                try:
                    await _check_single_job(session, job, now)
                except Exception as e:
                    logger.error(f"Error checking job {job.id}: {e}")
                    try:
                        session.rollback()
                    except Exception:
                        pass

        except Exception as e:
            logger.error(f"Error in job health check: {e}")
            try:
                session.rollback()
            except Exception:
                pass


async def _check_single_job(session, job: models.Job, now: datetime):
    """Check if a single job is stuck and handle it."""
    # Check if job is stuck (considers last_heartbeat if present)
    if not is_job_stuck(job.action, job.status, job.started_at, job.created_at, job.last_heartbeat):
        return

    # If this is a child job, check if parent is still active
    if job.parent_job_id:
        parent_job = session.get(models.Job, job.parent_job_id)
        if parent_job and parent_job.status in ("queued", "running"):
            # Parent is still active - skip this child, parent will handle it
            logger.debug(
                f"Skipping stuck child job {job.id} - parent job {job.parent_job_id} is still {parent_job.status}"
            )
            return
        elif not parent_job or parent_job.status in ("completed", "failed", "cancelled"):
            # Orphaned child - parent is done/missing, fail this child (don't retry orphans)
            logger.warning(
                f"Failing orphaned child job {job.id} - parent job {job.parent_job_id} is "
                f"{'missing' if not parent_job else parent_job.status}"
            )
            await _fail_job(session, job, reason="Parent job completed or missing, child orphaned")
            return

    logger.warning(
        f"Detected stuck job {job.id}: action={job.action}, status={job.status}, "
        f"started_at={job.started_at}, last_heartbeat={job.last_heartbeat}, agent_id={job.agent_id}"
    )

    # Check if assigned agent is offline
    agent_offline = False
    if job.agent_id:
        agent = session.get(models.Host, job.agent_id)
        if agent and agent.status != "online":
            agent_offline = True
            logger.warning(f"Job {job.id} agent {job.agent_id} is offline")

    non_retryable, non_retryable_reason = _timed_out_job_is_non_retryable(
        job.action,
        _read_log_for_classification(job.log_path),
    )
    if non_retryable:
        await _fail_job(
            session,
            job,
            reason=(
                "Job timed out with non-retryable failure signature "
                f"({non_retryable_reason}); skipping retry"
            ),
        )
        return

    # Determine if we should retry or fail
    if job.retry_count < settings.job_max_retries:
        await _retry_job(session, job, exclude_agent=job.agent_id if agent_offline else None)
    else:
        await _fail_job(session, job, reason="Job timed out after maximum retries")


async def _retry_job(session, old_job: models.Job, exclude_agent: str | None = None):
    """Create a new job to retry the failed operation.

    Args:
        session: Database session
        old_job: The stuck job to retry
        exclude_agent: Agent ID to exclude from selection (failed agent)
    """
    logger.info(
        f"Retrying job {old_job.id} (attempt {old_job.retry_count + 1}/{settings.job_max_retries})"
    )

    # Deduplication: Check for existing running/queued job with same lab_id and action
    # This prevents creating duplicate retries if multiple stuck jobs are processed
    if old_job.lab_id:
        existing_job = (
            session.query(models.Job)
            .filter(
                models.Job.lab_id == old_job.lab_id,
                models.Job.action == old_job.action,
                models.Job.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value]),
                models.Job.id != old_job.id,
            )
            .first()
        )
        if existing_job:
            logger.info(
                f"Skipping retry for job {old_job.id} - existing job {existing_job.id} "
                f"with same action '{old_job.action}' is already {existing_job.status}"
            )
            # Mark old job as superseded by existing job instead of creating new one
            old_job.status = "cancelled"
            old_job.completed_at = datetime.now(timezone.utc)
            old_job.superseded_by_id = existing_job.id
            old_job.log_path = (old_job.log_path or "") + f"\n\n--- Cancelled: duplicate of job {existing_job.id} ---"
            session.commit()
            return

    # Force-release lock on agent before retry to prevent new job from blocking
    if old_job.agent_id and old_job.lab_id:
        agent = session.get(models.Host, old_job.agent_id)
        if agent and agent.status == HostStatus.ONLINE.value:
            try:
                result = await agent_client.release_agent_lock(agent, old_job.lab_id)
                if result.get("status") == "cleared":
                    logger.info(f"Force-released lock for lab {old_job.lab_id} on agent {old_job.agent_id} before retry")
                elif result.get("status") == "not_found":
                    logger.debug(f"No lock found for lab {old_job.lab_id} on agent {old_job.agent_id}")
                else:
                    logger.warning(f"Could not release lock for lab {old_job.lab_id}: {result}")
            except Exception as e:
                logger.warning(f"Failed to force-release lock for lab {old_job.lab_id}: {e}")

    # Mark old job as failed
    old_job.status = JobStatus.FAILED.value
    old_job.completed_at = datetime.now(timezone.utc)
    timeout_msg = f"Job timed out after {get_job_timeout(old_job.action)}s, retrying (attempt {old_job.retry_count + 1})..."
    if _is_file_path(old_job.log_path):
        # Append timeout message to existing log file
        try:
            with open(old_job.log_path, "a") as f:
                f.write(f"\n\n--- {timeout_msg} ---\n")
        except Exception:
            pass
    elif old_job.log_path:
        # log_path contains inline content, append to it
        old_job.log_path = f"{old_job.log_path}\n\n--- {timeout_msg} ---"
    else:
        old_job.log_path = timeout_msg

    # Create new job with incremented retry count
    new_job = models.Job(
        id=str(uuid4()),
        lab_id=old_job.lab_id,
        user_id=old_job.user_id,
        action=old_job.action,
        status=JobStatus.QUEUED.value,
        retry_count=old_job.retry_count + 1,
    )
    session.add(new_job)
    session.flush()  # Persist new job so superseded_by_id FK is valid

    # Link old job to new job for tracking
    old_job.superseded_by_id = new_job.id

    # Cancel all child jobs of the old parent job
    # The new parent job will spawn fresh children
    child_jobs = (
        session.query(models.Job)
        .filter(
            models.Job.parent_job_id == old_job.id,
            models.Job.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value]),
        )
        .all()
    )
    if child_jobs:
        logger.info(f"Cancelling {len(child_jobs)} child job(s) of retried parent job {old_job.id}")
        for child in child_jobs:
            child.status = "cancelled"
            child.completed_at = datetime.now(timezone.utc)
            child.superseded_by_id = new_job.id
            if child.log_path:
                child.log_path = f"{child.log_path}\n\n--- Cancelled: parent job retried ---"
            else:
                child.log_path = "Cancelled: parent job retried"

    session.commit()
    session.refresh(new_job)

    logger.info(f"Created retry job {new_job.id} for original job {old_job.id}")

    # Trigger the job execution (similar to how jobs.py does it)
    await _trigger_job_execution(session, new_job, exclude_agent)


async def _trigger_job_execution(session, job: models.Job, exclude_agent: str | None = None):
    """Trigger execution of a retry job.

    This imports and calls the appropriate task runner based on the job action.
    """
    from app.tasks.jobs import run_agent_job, run_node_reconcile
    from app.services.topology import TopologyService
    from app.utils.lab import get_lab_provider

    lab = session.get(models.Lab, job.lab_id) if job.lab_id else None
    if not lab:
        logger.error(f"Cannot retry job {job.id}: lab not found")
        job.status = JobStatus.FAILED.value
        job.log_path = "Retry failed: lab not found"
        session.commit()
        return

    provider = get_lab_provider(lab)

    # Get an agent, excluding the failed one
    agent = await agent_client.get_healthy_agent(
        session,
        required_provider=provider,
        prefer_agent_id=lab.agent_id,
        exclude_agents=[exclude_agent] if exclude_agent else None,
    )

    if not agent:
        logger.error(f"Cannot retry job {job.id}: no healthy agent available")
        job.status = JobStatus.FAILED.value
        job.log_path = "Retry failed: no healthy agent available"
        session.commit()
        return

    # Trigger the appropriate task based on action
    if job.action == "up":
        # run_agent_job builds topology from database internally
        topo_service = TopologyService(session)
        if topo_service.has_nodes(lab.id):
            safe_create_task(
                run_agent_job(job.id, lab.id, "up", provider=provider),
                name=f"retry:deploy:{job.id}"
            )
        else:
            logger.error(f"Cannot retry deploy job {job.id}: no topology in database")
            job.status = JobStatus.FAILED.value
            job.log_path = "Retry failed: no topology defined"
            session.commit()

    elif job.action == "down":
        safe_create_task(
            run_agent_job(job.id, lab.id, "down", provider=provider),
            name=f"retry:destroy:{job.id}"
        )

    elif job.action.startswith("sync:"):
        # Sync action: sync:node:nodeid or sync:lab
        # Parse node IDs from action if present (sync:node:nodeid)
        if job.action.startswith("sync:node:"):
            node_id = job.action.split(":", 2)[2]
            node_ids = [node_id]
        else:
            # Full lab sync - need to get all node IDs
            node_states = session.query(models.NodeState).filter(
                models.NodeState.lab_id == job.lab_id
            ).all()
            node_ids = [ns.node_id for ns in node_states]

        if node_ids:
            safe_create_task(
                run_node_reconcile(job.id, lab.id, node_ids, provider),
                name=f"retry:sync:{job.id}"
            )

    else:
        logger.warning(f"Unknown action type for retry: {job.action}")
        job.status = JobStatus.FAILED.value
        job.log_path = f"Retry failed: unknown action type {job.action}"
        session.commit()


async def _fail_job(session, job: models.Job, reason: str):
    """Mark a job as failed and update lab state."""
    logger.error(f"Failing job {job.id}: {reason}")

    job.status = JobStatus.FAILED.value
    job.completed_at = datetime.now(timezone.utc)
    if _is_file_path(job.log_path):
        # Append failure message to existing log file
        try:
            with open(job.log_path, "a") as f:
                f.write(f"\n\n--- Job failed: {reason} ---\n")
        except Exception:
            pass
    elif job.log_path:
        # log_path contains inline content, append to it
        job.log_path = f"{job.log_path}\n\n--- Job failed: {reason} ---"
    else:
        job.log_path = reason

    # Update lab state to error
    if job.lab_id:
        lab = session.get(models.Lab, job.lab_id)
        if lab:
            lab.state = LabState.ERROR.value
            lab.state_error = f"Job {job.action} failed: {reason}"
            lab.state_updated_at = datetime.now(timezone.utc)
            logger.info(f"Set lab {job.lab_id} state to error due to stuck job")

    session.commit()


async def check_orphaned_queued_jobs():
    """Find queued jobs that were never assigned to an agent.

    This catches jobs that got stuck in the queue due to:
    - No agents available at creation time
    - Agent went offline before job was picked up
    - Race conditions in job assignment
    """
    with get_session() as session:
        try:
            now = datetime.now(timezone.utc)
            orphan_cutoff = now - timedelta(minutes=2)

            # Find queued jobs older than 2 minutes without an agent
            orphaned_jobs = (
                session.query(models.Job)
                .filter(
                    models.Job.status == JobStatus.QUEUED.value,
                    models.Job.agent_id.is_(None),
                    models.Job.created_at < orphan_cutoff,
                )
                .all()
            )

            for job in orphaned_jobs:
                logger.warning(f"Found orphaned queued job {job.id} (created {job.created_at})")

                if job.retry_count < settings.job_max_retries:
                    # Try to assign to an available agent
                    await _retry_job(session, job)
                else:
                    await _fail_job(session, job, reason="No agent available to process job")

        except Exception as e:
            logger.error(f"Error checking orphaned jobs: {e}")


async def check_jobs_on_offline_agents():
    """Find running jobs assigned to agents that have gone offline."""
    with get_session() as session:
        try:
            # Find all offline agents
            offline_agents = (
                session.query(models.Host)
                .filter(models.Host.status == HostStatus.OFFLINE.value)
                .all()
            )

            if not offline_agents:
                return

            offline_agent_ids = [a.id for a in offline_agents]

            # Find active jobs on offline agents
            stranded_jobs = (
                session.query(models.Job)
                .filter(
                    models.Job.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value]),
                    models.Job.agent_id.in_(offline_agent_ids),
                )
                .all()
            )

            for job in stranded_jobs:
                logger.warning(
                    f"Job {job.id} is on offline agent {job.agent_id}, "
                    f"retry_count={job.retry_count}"
                )

                if job.retry_count < settings.job_max_retries:
                    # Retry on a different agent
                    await _retry_job(session, job, exclude_agent=job.agent_id)
                else:
                    await _fail_job(session, job, reason="Agent went offline during job execution")

        except Exception as e:
            logger.error(f"Error checking jobs on offline agents: {e}")


async def _check_agent_active_transfers(host: models.Host, job_id: str) -> bool:
    """Query the agent to check if a transfer is genuinely active.

    Returns True if the agent reports the job as active (still transferring),
    False if the agent doesn't know about it or the endpoint is unavailable.
    """
    import httpx

    try:
        url = f"http://{host.address}/images/active-transfers"
        auth_headers = agent_client._get_agent_auth_headers()
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0), headers=auth_headers) as client:
            response = await client.get(url)
            if response.status_code == 404:
                # Old agent without this endpoint — fall through to timeout logic
                return False
            response.raise_for_status()
            data = response.json()
            active_jobs = data.get("active_jobs", {})
            return job_id in active_jobs
    except Exception as e:
        logger.debug(f"Could not query active transfers on {host.name}: {e}")
        return False


async def check_stuck_image_sync_jobs():
    """Find and handle ImageSyncJobs that are stuck.

    This function monitors ImageSyncJob records for stuck jobs:
    1. Jobs in 'pending' state older than image_sync_job_pending_timeout (2 min)
    2. Jobs in 'transferring' or 'loading' state past image_sync_timeout (10 min)
    3. Jobs assigned to hosts that have gone offline

    Before marking a transferring/loading job as stuck, queries the agent's
    active-transfers endpoint to verify the transfer isn't still in progress.

    Stuck jobs are marked as failed with detailed error messages.
    """
    with get_session() as session:
        try:
            now = datetime.now(timezone.utc)

            # Find all active image sync jobs
            active_jobs = (
                session.query(models.ImageSyncJob)
                .filter(models.ImageSyncJob.status.in_(["pending", "transferring", "loading"]))
                .all()
            )

            if not active_jobs:
                return

            for job in active_jobs:
                try:
                    # Check host status
                    host = session.get(models.Host, job.host_id)
                    host_offline = host and host.status != "online"

                    # Determine if job is stuck
                    is_stuck = False
                    error_reason = ""

                    if job.status == "pending":
                        # Pending jobs should start within pending timeout
                        pending_cutoff = now - timedelta(seconds=settings.image_sync_job_pending_timeout)
                        if job.created_at.replace(tzinfo=timezone.utc) < pending_cutoff:
                            is_stuck = True
                            error_reason = f"Job stuck in pending state for over {settings.image_sync_job_pending_timeout}s"
                            if host_offline:
                                error_reason += f" (target host {host.name if host else job.host_id} is offline)"

                    elif job.status in ["transferring", "loading"]:
                        # Active jobs should complete within image_sync_timeout
                        if job.started_at:
                            timeout_cutoff = now - timedelta(seconds=settings.image_sync_timeout)
                            if job.started_at.replace(tzinfo=timezone.utc) < timeout_cutoff:
                                # Before marking as stuck, check if agent reports active transfer
                                if host and not host_offline:
                                    agent_active = await _check_agent_active_transfers(host, job.id)
                                    if agent_active:
                                        # Hard ceiling: force-fail even if agent claims active
                                        hard_ceiling = timedelta(seconds=settings.image_sync_timeout * 2)
                                        if (now - job.started_at.replace(tzinfo=timezone.utc)) > hard_ceiling:
                                            logger.warning(
                                                f"Force-failing ImageSyncJob {job.id} — exceeded "
                                                f"hard ceiling ({settings.image_sync_timeout * 2}s) "
                                                f"despite agent reporting active transfer"
                                            )
                                        else:
                                            logger.info(
                                                f"ImageSyncJob {job.id} exceeds timeout but agent "
                                                f"reports active transfer — skipping"
                                            )
                                            continue
                                is_stuck = True
                                error_reason = f"Job timed out after {settings.image_sync_timeout}s in {job.status} state"

                        # Also check for offline host
                        if host_offline:
                            is_stuck = True
                            error_reason = f"Target host {host.name if host else job.host_id} went offline during transfer"

                    if is_stuck:
                        logger.warning(
                            f"Detected stuck ImageSyncJob {job.id}: status={job.status}, "
                            f"image_id={job.image_id}, host_id={job.host_id}, reason={error_reason}"
                        )

                        # Mark job as failed
                        job.status = JobStatus.FAILED.value
                        job.error_message = error_reason
                        job.completed_at = now
                        session.commit()

                        # Update corresponding ImageHost record
                        image_host = session.query(models.ImageHost).filter(
                            models.ImageHost.image_id == job.image_id,
                            models.ImageHost.host_id == job.host_id
                        ).first()

                        if image_host:
                            image_host.status = "failed"
                            image_host.error_message = error_reason
                            session.commit()

                        logger.info(f"Marked stuck ImageSyncJob {job.id} as failed: {error_reason}")

                except Exception as e:
                    logger.error(f"Error checking ImageSyncJob {job.id}: {e}")

        except Exception as e:
            logger.error(f"Error in ImageSyncJob health check: {e}")


async def check_stuck_locks():
    """Check all agents for stuck deploy locks and clear them.

    This function queries the /locks/status endpoint on each online agent
    to find locks that have been held longer than the configured threshold.
    When stuck locks are found, they are released via /locks/{lab_id}/release.

    This helps recover from scenarios where:
    - Deploy operations hang indefinitely
    - Agent crashes during deploy but restarts with stale lock state
    - Network issues cause deploy to timeout but lock remains
    """
    with get_session() as session:
        try:
            # Find all online agents
            from datetime import timezone as tz
            cutoff = datetime.now(tz.utc) - timedelta(seconds=60)

            online_agents = (
                session.query(models.Host)
                .filter(
                    models.Host.status == HostStatus.ONLINE.value,
                    models.Host.last_heartbeat >= cutoff,
                )
                .all()
            )

            if not online_agents:
                return

            for agent in online_agents:
                try:
                    status = await agent_client.get_agent_lock_status(agent)

                    # Check for errors from the agent
                    if status.get("error"):
                        logger.debug(f"Could not get lock status from agent {agent.id}: {status.get('error')}")
                        continue

                    # Check each lock
                    for lock in status.get("locks", []):
                        if lock.get("is_stuck"):
                            lab_id = lock.get("lab_id")
                            age_seconds = lock.get("age_seconds", 0)

                            logger.warning(
                                f"Found stuck lock on agent {agent.id} ({agent.name}) "
                                f"for lab {lab_id} (held for {age_seconds:.0f}s)"
                            )

                            # Release the stuck lock
                            result = await agent_client.release_agent_lock(agent, lab_id)

                            if result.get("status") == "cleared":
                                logger.info(f"Successfully released stuck lock for lab {lab_id} on agent {agent.id}")
                            else:
                                logger.warning(f"Failed to release stuck lock for lab {lab_id}: {result}")

                except Exception as e:
                    logger.error(f"Failed to check locks on agent {agent.id}: {e}")

        except Exception as e:
            logger.error(f"Error in stuck lock check: {e}")


async def check_stuck_stopping_nodes():
    """Find and recover nodes stuck in "stopping" state.

    This function monitors NodeState records for nodes stuck in "stopping":
    - Nodes with actual_state="stopping" and no active job for >6 minutes

    Stuck nodes are recovered by querying actual container status from the agent
    and updating their state accordingly.
    """
    with get_session() as session:
        try:
            now = datetime.now(timezone.utc)
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
                if ns.lab_id not in nodes_by_lab:
                    nodes_by_lab[ns.lab_id] = []
                nodes_by_lab[ns.lab_id].append(ns)

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
            logger.error(f"Error in stuck stopping nodes check: {e}")


async def check_stuck_starting_nodes():
    """Find and recover nodes stuck in "starting" state.

    This function monitors NodeState records for nodes stuck in "starting":
    - Nodes with actual_state="starting" and no active job for >6 minutes

    Stuck nodes are recovered by setting them to "stopped" (safe fallback).
    User can retry the start operation.
    """
    with get_session() as session:
        try:
            now = datetime.now(timezone.utc)
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
                if ns.lab_id not in nodes_by_lab:
                    nodes_by_lab[ns.lab_id] = []
                nodes_by_lab[ns.lab_id].append(ns)

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
            logger.error(f"Error in stuck starting nodes check: {e}")


async def check_stuck_agent_updates():
    """Find and handle AgentUpdateJob records that are stuck.

    Detects agent update jobs stuck in active states (pending, downloading,
    installing, restarting) past the configured timeout, or assigned to
    agents that have gone offline.
    """
    with get_session() as session:
        try:
            now = datetime.now(timezone.utc)
            active_statuses = ["pending", "downloading", "installing", "restarting"]

            stuck_jobs = (
                session.query(models.AgentUpdateJob)
                .filter(models.AgentUpdateJob.status.in_(active_statuses))
                .all()
            )

            if not stuck_jobs:
                return

            timeout = timedelta(seconds=settings.agent_update_timeout)

            for job in stuck_jobs:
                try:
                    # Check if target agent is offline
                    host = session.get(models.Host, job.host_id)
                    agent_offline = host and host.status != "online"

                    # Determine reference timestamp (started_at if available, else created_at)
                    ref_time = job.started_at or job.created_at
                    if ref_time.tzinfo is None:
                        ref_time = ref_time.replace(tzinfo=timezone.utc)
                    is_timed_out = (now - ref_time) > timeout

                    if agent_offline:
                        reason = f"Agent {host.name if host else job.host_id} went offline during update"
                    elif is_timed_out:
                        age_min = (now - ref_time).total_seconds() / 60
                        reason = f"Timed out after {age_min:.0f} minutes in '{job.status}' state"
                    else:
                        continue

                    logger.warning(
                        f"Detected stuck AgentUpdateJob {job.id}: status={job.status}, "
                        f"host_id={job.host_id}, reason={reason}"
                    )

                    job.status = "failed"
                    job.error_message = reason
                    job.completed_at = now
                    session.commit()

                    logger.info(f"Marked stuck AgentUpdateJob {job.id} as failed: {reason}")

                except Exception as e:
                    logger.error(f"Error checking AgentUpdateJob {job.id}: {e}")

        except Exception as e:
            logger.error(f"Error in agent update health check: {e}")


async def check_orphaned_image_sync_status():
    """Clear stale image_sync_status on NodeState records with no active sync job.

    When image sync callbacks fail to execute (e.g., API crash during transfer),
    NodeState records can be left with image_sync_status="syncing" or "checking"
    even though no ImageSyncJob is still active. This leaves nodes stuck showing
    a blue "syncing" indicator in the UI forever.

    This function finds such orphans and clears their status so reconciliation
    and enforcement can resume normal operation.
    """
    from app.services.broadcaster import broadcast_node_state_change

    with get_session() as session:
        try:
            # Find all NodeState records with active image sync status
            stuck_nodes = (
                session.query(models.NodeState)
                .filter(models.NodeState.image_sync_status.in_(["syncing", "checking"]))
                .all()
            )

            if not stuck_nodes:
                return

            for ns in stuck_nodes:
                # Look up the node's image to check for active sync jobs
                node_def = (
                    session.query(models.Node)
                    .filter(
                        models.Node.lab_id == ns.lab_id,
                        models.Node.container_name == ns.node_name,
                    )
                    .first()
                )
                if not node_def or not node_def.image:
                    # Can't determine image — clear status to unblock
                    logger.warning(
                        f"Clearing orphaned image_sync_status for {ns.node_name} "
                        f"in lab {ns.lab_id}: no node definition or image found"
                    )
                    ns.image_sync_status = None
                    ns.image_sync_message = None
                    session.commit()
                    continue

                # Find the host this node is placed on
                placement = (
                    session.query(models.NodePlacement)
                    .filter(
                        models.NodePlacement.lab_id == ns.lab_id,
                        models.NodePlacement.node_name == ns.node_name,
                    )
                    .first()
                )
                if not placement:
                    logger.warning(
                        f"Clearing orphaned image_sync_status for {ns.node_name} "
                        f"in lab {ns.lab_id}: no placement found"
                    )
                    ns.image_sync_status = None
                    ns.image_sync_message = None
                    session.commit()
                    continue

                # Check if any active ImageSyncJob exists for this image+host
                active_sync = (
                    session.query(models.ImageSyncJob)
                    .filter(
                        models.ImageSyncJob.image_id == node_def.image,
                        models.ImageSyncJob.host_id == placement.host_id,
                        models.ImageSyncJob.status.in_(["pending", "transferring", "loading"]),
                    )
                    .first()
                )

                if active_sync:
                    # Sync job still running — leave status alone
                    continue

                logger.warning(
                    f"Clearing orphaned image_sync_status='{ns.image_sync_status}' for "
                    f"{ns.node_name} in lab {ns.lab_id}: no active ImageSyncJob found"
                )
                old_status = ns.image_sync_status
                ns.image_sync_status = None
                ns.image_sync_message = None
                session.commit()

                # Broadcast so UI clears the blue indicator
                await broadcast_node_state_change(
                    lab_id=ns.lab_id,
                    node_id=ns.id,
                    node_name=ns.node_name,
                    desired_state=ns.desired_state,
                    actual_state=ns.actual_state,
                    is_ready=ns.is_ready,
                    error_message=ns.error_message,
                    image_sync_status=None,
                    image_sync_message=None,
                )

        except Exception as e:
            logger.error(f"Error in orphaned image sync status check: {e}")


async def job_health_monitor():
    """Background task to periodically check job health.

    Runs every job_health_check_interval seconds and:
    1. Checks for stuck running jobs
    2. Checks for orphaned queued jobs
    3. Checks for jobs on offline agents
    4. Checks for stuck image sync jobs
    5. Checks for stuck deploy locks on agents
    6. Checks for nodes stuck in "stopping" state
    7. Checks for nodes stuck in "starting" state
    8. Checks for stuck agent update jobs
    9. Checks for orphaned image_sync_status on node states
    """
    logger.info(
        f"Job health monitor started "
        f"(interval: {settings.job_health_check_interval}s, "
        f"max_retries: {settings.job_max_retries})"
    )

    while True:
        try:
            await asyncio.sleep(settings.job_health_check_interval)

            # Run all health checks
            await check_stuck_jobs()
            await check_orphaned_queued_jobs()
            await check_jobs_on_offline_agents()
            await check_stuck_image_sync_jobs()
            await check_stuck_locks()
            await check_stuck_stopping_nodes()
            await check_stuck_starting_nodes()
            await check_stuck_agent_updates()
            await check_orphaned_image_sync_status()

        except asyncio.CancelledError:
            logger.info("Job health monitor stopped")
            break
        except Exception as e:
            logger.error(f"Error in job health monitor: {e}")
            # Continue running - don't let one error stop the monitor
