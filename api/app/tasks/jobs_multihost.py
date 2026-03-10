"""Multi-host deploy and destroy job executors.

These functions orchestrate lab deploy/destroy across multiple compute agents.
Shared utilities (locking, preflight, webhooks, metrics) live in ``jobs.py``.
"""
from __future__ import annotations

import asyncio
import logging

from app import agent_client, models
from app.config import settings
from app.db import get_session
from app.events.publisher import (
    emit_deploy_finished,
    emit_destroy_finished,
    emit_job_failed,
)
from app.metrics import record_job_completed
from app.services.topology import TopologyService
from app.state import (
    JobStatus,
    LabState,
    LinkActualState,
)
from app.tasks.jobs import (
    _capture_node_ips,
    _dispatch_webhook,
    _job_duration_seconds,
    _record_failed,
    _record_started,
    _update_node_placements,
)
from app.utils.db import release_db_transaction_for_io as _release_db_transaction_for_io
from app.utils.lab import get_node_provider
from app.utils.job import broadcast_job_progress as _broadcast_job_progress
from app.utils.lab import update_lab_state
from app.utils.time import utcnow

logger = logging.getLogger(__name__)


async def _deploy_host_provider_groups(
    agent: models.Host,
    job_id: str,
    lab_id: str,
    provider_specs: list[tuple[str, list[str], dict]],
) -> list[tuple[str, list[str], dict | Exception]]:
    """Deploy provider groups sequentially on one host.

    Agents serialize deploys with a per-lab lock, so same-host provider
    groups must not be dispatched concurrently.
    """
    host_results: list[tuple[str, list[str], dict | Exception]] = []
    for index, (provider_name, provider_node_names, provider_topology) in enumerate(provider_specs):
        try:
            result = await agent_client.deploy_to_agent(
                agent,
                job_id,
                lab_id,
                topology=provider_topology,
                provider=provider_name,
            )
        except Exception as exc:
            host_results.append((provider_name, provider_node_names, exc))
            for skipped_provider_name, skipped_node_names, _skipped_topology in provider_specs[index + 1:]:
                host_results.append(
                    (
                        skipped_provider_name,
                        skipped_node_names,
                        RuntimeError(
                            f"skipped after earlier provider failure on host {agent.name}"
                        ),
                    )
                )
            break

        host_results.append((provider_name, provider_node_names, result))
        if result.get("status") != "completed":
            for skipped_provider_name, skipped_node_names, _skipped_topology in provider_specs[index + 1:]:
                host_results.append(
                    (
                        skipped_provider_name,
                        skipped_node_names,
                        RuntimeError(
                            f"skipped after {provider_name} returned status {result.get('status', 'unknown')}"
                        ),
                    )
                )
            break

    return host_results


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
                job.completed_at = utcnow()
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
                    job.completed_at = utcnow()
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
            job.started_at = utcnow()
            session.commit()
            _record_started(job, "up")

            # Broadcast job started
            await _broadcast_job_progress(
                lab_id, job_id, "up", "running",
                progress_message=f"Starting multi-host deployment ({len(analysis.placements)} hosts)"
            )

            update_lab_state(session, lab_id, LabState.STARTING.value)

            # Dispatch webhook for deploy started
            _release_db_transaction_for_io(
                session,
                context=f"deploy-started webhook for job {job_id}",
            )
            await _dispatch_webhook("lab.deploy_started", lab, job, session)

            # Map host_id to agent objects
            host_to_agent: dict[str, models.Host] = {}
            missing_hosts = []

            for host_id in analysis.placements:
                agent = session.get(models.Host, host_id)
                if agent and agent_client.is_agent_online(agent):
                    try:
                        _release_db_transaction_for_io(
                            session,
                            context=f"multihost preflight on host {host_id}",
                        )
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
                job.completed_at = utcnow()
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
                    job.completed_at = utcnow()
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

            # Deploy to each host in parallel, but run provider groups
            # sequentially on the same host because each agent enforces a
            # per-lab deploy lock.
            deploy_tasks = []
            deploy_task_meta: list[tuple[str, models.Host, list[tuple[str, list[str], dict]]]] = []
            deploy_results: dict[tuple[str, str], dict] = {}
            host_node_names: dict[str, list[str]] = {}  # For placement updates
            nodes_by_host: dict[str, list[models.Node]] = {}
            for node in nodes:
                if node.host_id:
                    nodes_by_host.setdefault(node.host_id, []).append(node)

            for host_id, node_placements in analysis.placements.items():
                agent = host_to_agent[host_id]

                # Build JSON topology for this host from database
                topology_json = topo_service.build_deploy_topology(lab_id, host_id)
                node_names = [n["name"] for n in topology_json.get("nodes", [])]
                host_node_names[host_id] = node_names
                host_nodes = nodes_by_host.get(host_id, [])
                provider_to_names: dict[str, list[str]] = {}
                for node in host_nodes:
                    runtime_name = None
                    for attr_name in ("container_name", "display_name", "name", "gui_id"):
                        attr_value = getattr(node, attr_name, None)
                        if isinstance(attr_value, str) and attr_value:
                            runtime_name = attr_value
                            break
                    if not runtime_name:
                        continue
                    provider_name = get_node_provider(node, session)
                    provider_to_names.setdefault(provider_name, []).append(runtime_name)

                if not provider_to_names and node_names:
                    provider_to_names[provider] = list(node_names)

                logger.info(
                    f"Deploying to host {agent.name} ({host_id}): "
                    f"{len(node_names)} nodes across {len(provider_to_names)} provider group(s)"
                )
                log_parts.append(f"=== Host: {agent.name} ({host_id}) ===")
                log_parts.append(f"Nodes: {', '.join(node_names)}")

                provider_specs: list[tuple[str, list[str], dict]] = []
                for provider_name, provider_node_names in sorted(
                    provider_to_names.items(),
                    key=lambda item: (item[0] != "libvirt", item[0]),
                ):
                    provider_node_set = set(provider_node_names)
                    provider_topology = {
                        "nodes": [
                            node_def
                            for node_def in topology_json.get("nodes", [])
                            if node_def.get("name") in provider_node_set
                        ],
                        "links": [
                            link_def
                            for link_def in topology_json.get("links", [])
                            if link_def.get("source_node") in provider_node_set
                            and link_def.get("target_node") in provider_node_set
                        ],
                    }
                    log_parts.append(
                        f"  Provider {provider_name}: {', '.join(provider_node_names)}"
                    )
                    provider_specs.append(
                        (provider_name, list(provider_node_names), provider_topology)
                    )
                deploy_tasks.append(
                    _deploy_host_provider_groups(
                        agent,
                        job_id,
                        lab_id,
                        provider_specs,
                    )
                )
                deploy_task_meta.append((host_id, agent, provider_specs))

            # Wait for all deployments
            _release_db_transaction_for_io(
                session,
                context=f"multihost deploy gather for job {job_id}",
            )
            results = await asyncio.gather(*deploy_tasks, return_exceptions=True)

            deploy_success = True
            for (host_id, agent, provider_specs), host_results in zip(deploy_task_meta, results):
                if isinstance(host_results, Exception):
                    log_parts.append(f"\nDeploy to {agent.name} FAILED: {host_results}")
                    deploy_success = False
                    continue
                for provider_name, provider_node_names, result in host_results:
                    if isinstance(result, Exception):
                        log_parts.append(f"\nDeploy to {agent.name}/{provider_name} FAILED: {result}")
                        deploy_success = False
                    else:
                        deploy_results[(host_id, provider_name)] = result
                        status = result.get("status", "unknown")
                        log_parts.append(f"\nDeploy to {agent.name}/{provider_name}: {status}")
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
                rollback_targets = []
                for (host_id, agent, provider_specs), host_results in zip(deploy_task_meta, results):
                    if isinstance(host_results, Exception):
                        continue
                    for provider_name, _provider_node_names, result in host_results:
                        if isinstance(result, Exception):
                            continue
                        if result.get("status") == "completed":
                            rollback_tasks.append(
                                agent_client.destroy_on_agent(
                                    agent,
                                    job_id,
                                    lab_id,
                                    provider=provider_name,
                                )
                            )
                            rollback_targets.append(f"{agent.name}/{provider_name}")

                if rollback_tasks:
                    log_parts.append(f"Rolling back providers: {', '.join(rollback_targets)}")
                    _release_db_transaction_for_io(
                        session,
                        context=f"multihost rollback gather for job {job_id}",
                    )
                    rollback_results = await asyncio.gather(*rollback_tasks, return_exceptions=True)

                    for target_name, rb_result in zip(rollback_targets, rollback_results):
                        if isinstance(rb_result, Exception):
                            log_parts.append(f"  {target_name}: rollback FAILED - {rb_result}")
                        else:
                            status = rb_result.get("status", "unknown")
                            log_parts.append(f"  {target_name}: rollback {status}")
                else:
                    log_parts.append("No hosts to rollback (all failed)")

                job.status = JobStatus.FAILED.value
                job.completed_at = utcnow()
                job.log_path = "\n".join(log_parts)
                update_lab_state(session, lab_id, LabState.ERROR.value, error="Deployment failed on one or more hosts")
                _record_failed(job, "up", duration_seconds=_job_duration_seconds(job))
                session.commit()
                logger.error(f"Job {job_id} failed: deployment error on one or more hosts (rollback completed)")
                return

            # Create all links (same-host via OVS hot_connect, cross-host via VXLAN)
            # This handles both link types and creates/updates LinkState records
            from app.tasks.link_orchestration import create_deployment_links

            _release_db_transaction_for_io(
                session,
                context=f"multihost link creation for job {job_id}",
            )
            links_ok, links_failed = await create_deployment_links(
                session, lab_id, host_to_agent, log_parts
            )

            # Fail the job if any links failed
            if links_failed > 0:
                log_parts.append("\n=== Link Setup Summary ===")
                log_parts.append(f"Links: {links_ok} OK, {links_failed} failed")
                log_parts.append("\nNote: Containers are deployed but some links failed.")
                job.status = JobStatus.FAILED.value
                job.completed_at = utcnow()
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
                    _release_db_transaction_for_io(
                        session,
                        context=f"placement update for host {host_id} in job {job_id}",
                    )
                    await _update_node_placements(session, lab_id, agent.id, node_names)

            # Mark job as completed
            job.status = JobStatus.COMPLETED.value
            job.completed_at = utcnow()
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
                _release_db_transaction_for_io(
                    session,
                    context=f"capture node IPs for host {agent.id} in job {job_id}",
                )
                await _capture_node_ips(session, lab_id, agent)

            session.commit()

            # Dispatch webhook for successful deploy
            _release_db_transaction_for_io(
                session,
                context=f"deploy-complete webhook for job {job_id}",
            )
            await _dispatch_webhook("lab.deploy_complete", lab, job, session)
            asyncio.create_task(emit_deploy_finished(lab_id, job_id=job_id))

            logger.info(f"Job {job_id} completed: multi-host deployment successful")

        except Exception as e:
            logger.exception(f"Job {job_id} failed with unexpected error: {e}")
            try:
                session.rollback()
                job = session.get(models.Job, job_id)
                lab = session.get(models.Lab, lab_id)
                if job:
                    job.status = JobStatus.FAILED.value
                    job.completed_at = utcnow()
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
                job.completed_at = utcnow()
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
            job.started_at = utcnow()
            session.commit()
            _record_started(job, "down")

            update_lab_state(session, lab_id, LabState.STOPPING.value)

            # Map host_id to reachable agents
            host_to_agent: dict[str, models.Host] = {}
            log_parts = []
            missing_hosts: list[str] = []
            unavailable_hosts: list[str] = []

            for host_id in analysis.placements:
                agent = session.get(models.Host, host_id)
                if not agent:
                    missing_hosts.append(host_id)
                    log_parts.append(f"WARNING: Agent '{host_id}' not found, skipping")
                elif agent_client.is_agent_online(agent):
                    host_to_agent[host_id] = agent
                else:
                    unavailable_hosts.append(host_id)
                    log_parts.append(
                        f"WARNING: Agent '{host_id}' is offline/unreachable, destroy deferred"
                    )

            if not host_to_agent:
                # No reachable agents found.
                details: list[str] = []
                if missing_hosts:
                    details.append(f"missing={', '.join(missing_hosts)}")
                if unavailable_hosts:
                    details.append(f"offline={', '.join(unavailable_hosts)}")
                detail_suffix = f" ({'; '.join(details)})" if details else ""
                error_msg = f"No online agents found for multi-host destroy{detail_suffix}"
                job.status = JobStatus.FAILED.value
                job.completed_at = utcnow()
                job.log_path = f"ERROR: {error_msg}"
                update_lab_state(session, lab_id, LabState.ERROR.value, error=error_msg)
                _record_failed(job, "down", duration_seconds=_job_duration_seconds(job))
                session.commit()
                logger.error(f"Job {job_id} failed: {error_msg}")
                return

            # First, tear down VXLAN tunnels and clean up VxlanTunnel records
            from app.tasks.link_orchestration import teardown_deployment_links

            _release_db_transaction_for_io(
                session,
                context=f"multihost link teardown for job {job_id}",
            )
            tunnels_ok, tunnels_failed = await teardown_deployment_links(
                session, lab_id, host_to_agent, log_parts
            )

            # Destroy node runtimes on each host, grouped by provider so mixed
            # Docker/libvirt labs are fully torn down.
            log_parts.append("\n=== Destroying node runtimes ===")
            destroy_tasks = []
            destroy_meta: list[tuple[str, models.Host, str]] = []
            nodes = (
                session.query(models.Node)
                .filter(models.Node.lab_id == lab_id)
                .all()
            )
            nodes_by_host: dict[str, list[models.Node]] = {}
            for node in nodes:
                if not node.host_id or node.node_type == "external":
                    continue
                nodes_by_host.setdefault(node.host_id, []).append(node)

            for host_id, agent in host_to_agent.items():
                host_nodes = nodes_by_host.get(host_id, [])
                provider_names = sorted({
                    get_node_provider(node, session)
                    for node in host_nodes
                    if node.container_name
                })
                if not provider_names:
                    log_parts.append(f"{agent.name}: completed")
                    log_parts.append("  STDOUT: No node runtimes assigned")
                    continue
                logger.info(
                    "Destroying lab %s on host %s across providers: %s",
                    lab_id,
                    agent.name,
                    ", ".join(provider_names),
                )
                for provider_name in provider_names:
                    destroy_tasks.append(
                        agent_client.destroy_on_agent(
                            agent,
                            job_id,
                            lab_id,
                            provider=provider_name,
                        )
                    )
                    destroy_meta.append((host_id, agent, provider_name))

            # Wait for all destroys
            _release_db_transaction_for_io(
                session,
                context=f"multihost destroy gather for job {job_id}",
            )
            results = await asyncio.gather(*destroy_tasks, return_exceptions=True)

            all_success = tunnels_failed == 0 and not missing_hosts and not unavailable_hosts
            if tunnels_failed:
                log_parts.append(
                    f"WARNING: Overlay teardown incomplete ({tunnels_failed} tunnel teardown failure(s))"
                )
            if missing_hosts:
                log_parts.append(
                    f"WARNING: Missing agent records: {', '.join(missing_hosts)}"
                )
            if unavailable_hosts:
                log_parts.append(
                    f"WARNING: Offline/unreachable agents: {', '.join(unavailable_hosts)}"
                )

            for (host_id, agent, provider_name), result in zip(destroy_meta, results):
                if isinstance(result, Exception):
                    log_parts.append(f"{agent.name}/{provider_name}: FAILED - {result}")
                    all_success = False
                else:
                    status = result.get("status", "unknown")
                    log_parts.append(f"{agent.name}/{provider_name}: {status}")
                    if result.get("stdout"):
                        log_parts.append(f"  STDOUT: {result['stdout'][:200]}")
                    if result.get("stderr"):
                        log_parts.append(f"  STDERR: {result['stderr'][:200]}")
                    if status != "completed":
                        all_success = False

            # Query remaining LinkState records.
            remaining_link_states = (
                session.query(models.LinkState)
                .filter(models.LinkState.lab_id == lab_id)
                .all()
            )

            # Update job status
            if all_success:
                # Full success: safe to remove any lingering link state rows.
                if remaining_link_states:
                    for ls in remaining_link_states:
                        session.delete(ls)
                    session.flush()
                job.status = JobStatus.COMPLETED.value
                update_lab_state(session, lab_id, LabState.STOPPED.value)
            else:
                # Preserve link rows for retry paths and make desired state explicit.
                for ls in remaining_link_states:
                    if ls.desired_state != "deleted":
                        ls.desired_state = "deleted"
                    if ls.actual_state == LinkActualState.UP.value:
                        ls.actual_state = LinkActualState.ERROR.value
                        ls.error_message = "Destroy incomplete; pending retry"
                session.flush()

                # Use completed_with_warnings for partial failures.
                job.status = JobStatus.COMPLETED_WITH_WARNINGS.value
                update_lab_state(
                    session,
                    lab_id,
                    LabState.ERROR.value,
                    error="Destroy completed with warnings; cleanup pending retry",
                )
                log_parts.append("\nWARNING: Some hosts may have had issues during destroy")
                log_parts.append("Containers may need manual cleanup on failed hosts.")

            job.completed_at = utcnow()
            job.log_path = "\n".join(log_parts)
            if job.status == JobStatus.COMPLETED.value:
                record_job_completed("down", duration_seconds=_job_duration_seconds(job) or 0.0)
            else:
                # completed_with_warnings still reflects a completed destroy action.
                record_job_completed("down", duration_seconds=_job_duration_seconds(job) or 0.0)
            session.commit()

            if all_success:
                # Dispatch webhook for destroy complete
                _release_db_transaction_for_io(
                    session,
                    context=f"destroy-complete webhook for job {job_id}",
                )
                await _dispatch_webhook("lab.destroy_complete", lab, job, session)
                asyncio.create_task(emit_destroy_finished(lab_id, job_id=job_id))
            else:
                # Surface partial-destroy as a warning failure event to operators.
                _release_db_transaction_for_io(
                    session,
                    context=f"destroy-warning webhook for job {job_id}",
                )
                await _dispatch_webhook("job.failed", lab, job, session)
                asyncio.create_task(
                    emit_job_failed(lab_id, job_id=job_id, job_action="down")
                )

            logger.info(f"Job {job_id} completed: multi-host destroy {'successful' if all_success else 'with warnings'}")

        except Exception as e:
            logger.exception(f"Job {job_id} failed with unexpected error: {e}")
            try:
                session.rollback()
                job = session.get(models.Job, job_id)
                if job:
                    job.status = JobStatus.FAILED.value
                    job.completed_at = utcnow()
                    job.log_path = f"ERROR: Unexpected error: {e}"
                    update_lab_state(session, lab_id, LabState.ERROR.value, error=str(e))
                    _record_failed(job, "down", duration_seconds=_job_duration_seconds(job))
                    session.commit()
            except Exception as inner_e:
                logger.exception(f"Critical error handling job {job_id} failure: {inner_e}")
