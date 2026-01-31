"""Background job execution functions."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone

from app import agent_client, models, webhooks
from app.agent_client import AgentJobError, AgentUnavailableError
from app.db import SessionLocal
from app.topology import analyze_topology, graph_to_containerlab_yaml, split_topology_by_host, yaml_to_graph
from app.utils.lab import update_lab_state

logger = logging.getLogger(__name__)


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
    assigned by containerlab/docker for use in IaC workflows.
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
) -> None:
    """Update NodePlacement records after successful deploy.

    This tracks which agent is running which nodes for affinity.

    Args:
        session: Database session
        lab_id: Lab identifier
        agent_id: Agent that deployed the nodes
        node_names: List of node names that were deployed
    """
    try:
        for node_name in node_names:
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
                existing.status = "deployed"
            else:
                # Create new placement
                placement = models.NodePlacement(
                    lab_id=lab_id,
                    node_name=node_name,
                    host_id=agent_id,
                    status="deployed",
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
    topology_yaml: str | None = None,
    node_name: str | None = None,
    provider: str = "containerlab",
):
    """Run a job on an agent in the background.

    Handles errors gracefully and provides detailed error messages.
    Updates lab state based on job outcome.

    Args:
        job_id: The job ID
        lab_id: The lab ID
        action: Action to perform (up, down, node:start:name, etc.)
        topology_yaml: Topology YAML for deploy actions
        node_name: Node name for node actions
        provider: Provider for the job (default: containerlab)
    """
    session = SessionLocal()
    try:
        job = session.get(models.Job, job_id)
        if not job:
            logger.error(f"Job {job_id} not found in database")
            return

        lab = session.get(models.Lab, lab_id)
        if not lab:
            logger.error(f"Lab {lab_id} not found in database")
            job.status = "failed"
            job.completed_at = datetime.utcnow()
            job.log_path = f"ERROR: Lab {lab_id} not found"
            session.commit()
            return

        # Find a healthy agent with required capability, respecting affinity
        agent = await agent_client.get_agent_for_lab(
            session,
            lab,
            required_provider=provider,
        )
        if not agent:
            job.status = "failed"
            job.completed_at = datetime.utcnow()
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
        job.started_at = datetime.utcnow()
        session.commit()

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
                result = await agent_client.deploy_to_agent(agent, job_id, lab_id, topology_yaml or "", provider=provider)
            elif action == "down":
                result = await agent_client.destroy_on_agent(agent, job_id, lab_id)
            elif action.startswith("node:"):
                # Parse node action: "node:start:nodename" or "node:stop:nodename"
                parts = action.split(":", 2)
                node_action_type = parts[1] if len(parts) > 1 else ""
                node = parts[2] if len(parts) > 2 else ""
                result = await agent_client.node_action_on_agent(agent, job_id, lab_id, node, node_action_type)
            else:
                result = {"status": "failed", "error_message": f"Unknown action: {action}"}

            # Update job based on result
            job.completed_at = datetime.utcnow()

            if result.get("status") == "completed":
                job.status = "completed"
                log_content = f"Job completed successfully.\n\n"

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
            job.completed_at = datetime.utcnow()
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
            job.completed_at = datetime.utcnow()
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
            job.completed_at = datetime.utcnow()
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

    finally:
        session.close()


async def run_multihost_deploy(
    job_id: str,
    lab_id: str,
    topology_yaml: str,
    provider: str = "containerlab",
):
    """Deploy a lab across multiple hosts.

    This function:
    1. Parses the topology to find host assignments
    2. Splits the topology by host
    3. Deploys sub-topologies to each agent in parallel
    4. Sets up VXLAN overlay links for cross-host connections

    Args:
        job_id: The job ID
        lab_id: The lab ID
        topology_yaml: Full topology YAML
        provider: Provider for the job
    """
    session = SessionLocal()
    try:
        job = session.get(models.Job, job_id)
        if not job:
            logger.error(f"Job {job_id} not found in database")
            return

        lab = session.get(models.Lab, lab_id)
        if not lab:
            logger.error(f"Lab {lab_id} not found in database")
            job.status = "failed"
            job.completed_at = datetime.utcnow()
            job.log_path = f"ERROR: Lab {lab_id} not found"
            session.commit()
            return

        # Parse topology YAML to graph
        graph = yaml_to_graph(topology_yaml)

        # Analyze for multi-host deployment
        analysis = analyze_topology(graph)

        # Check if there are nodes without explicit placement
        placed_node_count = sum(len(p) for p in analysis.placements.values())
        total_node_count = len(graph.nodes)

        if placed_node_count < total_node_count:
            # Some nodes don't have explicit host placement
            # Find a default agent for them
            default_agent = await agent_client.get_agent_for_lab(
                session, lab, required_provider=provider
            )
            if default_agent:
                # Re-analyze with default host for unplaced nodes
                analysis = analyze_topology(graph, default_host=default_agent.name)
                logger.info(
                    f"Lab {lab_id} has {total_node_count - placed_node_count} nodes without "
                    f"explicit placement, using {default_agent.name} as default host"
                )
            else:
                # No default agent available - fail the unplaced nodes
                job.status = "failed"
                job.completed_at = datetime.utcnow()
                job.log_path = (
                    f"ERROR: {total_node_count - placed_node_count} nodes have no explicit "
                    f"host placement and no default agent is available"
                )
                update_lab_state(session, lab_id, "error", error="No agent for unplaced nodes")
                session.commit()
                return

        logger.info(
            f"Multi-host deployment for lab {lab_id}: "
            f"{len(analysis.placements)} hosts, "
            f"{len(analysis.cross_host_links)} cross-host links"
        )

        # Update job status
        job.status = "running"
        job.started_at = datetime.utcnow()
        session.commit()

        update_lab_state(session, lab_id, "starting")

        # Dispatch webhook for deploy started
        await _dispatch_webhook("lab.deploy_started", lab, job, session)

        # Map host names to agents
        host_to_agent: dict[str, models.Host] = {}
        missing_hosts = []

        for host_name in analysis.placements:
            agent = await agent_client.get_agent_by_name(
                session, host_name, required_provider=provider
            )
            if agent:
                host_to_agent[host_name] = agent
            else:
                missing_hosts.append(host_name)

        if missing_hosts:
            error_msg = f"Missing or unhealthy agents for hosts: {', '.join(missing_hosts)}"
            job.status = "failed"
            job.completed_at = datetime.utcnow()
            job.log_path = f"ERROR: {error_msg}"
            update_lab_state(session, lab_id, "error", error=error_msg)
            session.commit()
            logger.error(f"Job {job_id} failed: {error_msg}")
            return

        # Split topology by host
        host_topologies = split_topology_by_host(graph, analysis)

        # Deploy to each host in parallel
        deploy_tasks = []
        deploy_results: dict[str, dict] = {}
        log_parts = []

        for host_name, sub_graph in host_topologies.items():
            agent = host_to_agent[host_name]
            sub_yaml = graph_to_containerlab_yaml(sub_graph, lab_id)

            logger.info(
                f"Deploying to host {host_name} (agent {agent.id}): "
                f"{len(sub_graph.nodes)} nodes"
            )
            log_parts.append(f"=== Host: {host_name} ({agent.id}) ===")
            log_parts.append(f"Nodes: {', '.join(n.name for n in sub_graph.nodes)}")

            deploy_tasks.append(
                agent_client.deploy_to_agent(agent, job_id, lab_id, sub_yaml)
            )

        # Wait for all deployments
        results = await asyncio.gather(*deploy_tasks, return_exceptions=True)

        deploy_success = True
        for i, (host_name, result) in enumerate(zip(host_topologies.keys(), results)):
            if isinstance(result, Exception):
                log_parts.append(f"\nDeploy to {host_name} FAILED: {result}")
                deploy_success = False
            else:
                deploy_results[host_name] = result
                status = result.get("status", "unknown")
                log_parts.append(f"\nDeploy to {host_name}: {status}")
                if result.get("stdout"):
                    log_parts.append(f"STDOUT:\n{result['stdout']}")
                if result.get("stderr"):
                    log_parts.append(f"STDERR:\n{result['stderr']}")
                if status != "completed":
                    deploy_success = False

        if not deploy_success:
            job.status = "failed"
            job.completed_at = datetime.utcnow()
            job.log_path = "\n".join(log_parts)
            update_lab_state(session, lab_id, "error", error="Deployment failed on one or more hosts")
            session.commit()
            logger.error(f"Job {job_id} failed: deployment error on one or more hosts")
            return

        # Set up cross-host links via VXLAN overlay
        if analysis.cross_host_links:
            log_parts.append("\n=== Cross-Host Links ===")
            logger.info(f"Setting up {len(analysis.cross_host_links)} cross-host links")

            for chl in analysis.cross_host_links:
                agent_a = host_to_agent.get(chl.host_a)
                agent_b = host_to_agent.get(chl.host_b)

                if not agent_a or not agent_b:
                    log_parts.append(
                        f"SKIP {chl.link_id}: missing agent for {chl.host_a} or {chl.host_b}"
                    )
                    continue

                # Get container names from containerlab naming convention
                # Containerlab names containers as: clab-{lab_id}-{node_name}
                safe_lab_id = re.sub(r'[^a-zA-Z0-9_-]', '', lab_id)[:20]
                container_a = f"clab-{safe_lab_id}-{chl.node_a}"
                container_b = f"clab-{safe_lab_id}-{chl.node_b}"

                result = await agent_client.setup_cross_host_link(
                    database=session,
                    lab_id=lab_id,
                    link_id=chl.link_id,
                    agent_a=agent_a,
                    agent_b=agent_b,
                    node_a=container_a,
                    interface_a=chl.interface_a,
                    node_b=container_b,
                    interface_b=chl.interface_b,
                    ip_a=chl.ip_a,
                    ip_b=chl.ip_b,
                )

                if result.get("success"):
                    log_parts.append(
                        f"Link {chl.link_id}: OK (VNI {result.get('vni')})"
                    )
                else:
                    log_parts.append(
                        f"Link {chl.link_id}: FAILED - {result.get('error')}"
                    )
                    # Don't fail the whole job for overlay issues - containers are running

        # Mark job as completed
        job.status = "completed"
        job.completed_at = datetime.utcnow()
        job.log_path = "\n".join(log_parts)

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
                job.completed_at = datetime.utcnow()
                job.log_path = f"ERROR: Unexpected error: {e}"
                update_lab_state(session, lab_id, "error", error=str(e))
                session.commit()
                # Dispatch webhook for failed deploy
                if lab:
                    await _dispatch_webhook("lab.deploy_failed", lab, job, session)
        except Exception:
            pass
    finally:
        session.close()


async def run_multihost_destroy(
    job_id: str,
    lab_id: str,
    topology_yaml: str,
    provider: str = "containerlab",
):
    """Destroy a multi-host lab.

    This function:
    1. Parses the topology to find host assignments
    2. Cleans up overlay networks on each agent
    3. Destroys containers on each agent

    Args:
        job_id: The job ID
        lab_id: The lab ID
        topology_yaml: Full topology YAML (to identify hosts)
        provider: Provider for the job
    """
    session = SessionLocal()
    try:
        job = session.get(models.Job, job_id)
        if not job:
            logger.error(f"Job {job_id} not found in database")
            return

        lab = session.get(models.Lab, lab_id)
        if not lab:
            logger.error(f"Lab {lab_id} not found in database")
            job.status = "failed"
            job.completed_at = datetime.utcnow()
            job.log_path = f"ERROR: Lab {lab_id} not found"
            session.commit()
            return

        # Parse topology YAML to find hosts
        graph = yaml_to_graph(topology_yaml)
        analysis = analyze_topology(graph)

        logger.info(
            f"Multi-host destroy for lab {lab_id}: "
            f"{len(analysis.placements)} hosts"
        )

        # Update job status
        job.status = "running"
        job.started_at = datetime.utcnow()
        session.commit()

        update_lab_state(session, lab_id, "stopping")

        # Map host names to agents
        host_to_agent: dict[str, models.Host] = {}
        log_parts = []

        for host_name in analysis.placements:
            agent = await agent_client.get_agent_by_name(
                session, host_name, required_provider=provider
            )
            if agent:
                host_to_agent[host_name] = agent
            else:
                log_parts.append(f"WARNING: Agent '{host_name}' not found, skipping")

        if not host_to_agent:
            # No agents found, try single-agent destroy as fallback
            error_msg = "No agents found for multi-host destroy"
            job.status = "failed"
            job.completed_at = datetime.utcnow()
            job.log_path = f"ERROR: {error_msg}"
            update_lab_state(session, lab_id, "error", error=error_msg)
            session.commit()
            logger.error(f"Job {job_id} failed: {error_msg}")
            return

        # First, clean up overlay networks on all agents
        if analysis.cross_host_links:
            log_parts.append("=== Cleaning up overlay networks ===")
            for host_name, agent in host_to_agent.items():
                result = await agent_client.cleanup_overlay_on_agent(agent, lab_id)
                log_parts.append(
                    f"{host_name}: {result.get('tunnels_deleted', 0)} tunnels, "
                    f"{result.get('bridges_deleted', 0)} bridges deleted"
                )
                if result.get("errors"):
                    log_parts.append(f"  Errors: {result['errors']}")

        # Destroy containers on each host in parallel
        log_parts.append("\n=== Destroying containers ===")
        destroy_tasks = []

        for host_name, agent in host_to_agent.items():
            logger.info(f"Destroying on host {host_name} (agent {agent.id})")
            destroy_tasks.append(
                agent_client.destroy_on_agent(agent, job_id, lab_id)
            )

        # Wait for all destroys
        results = await asyncio.gather(*destroy_tasks, return_exceptions=True)

        all_success = True
        for host_name, result in zip(host_to_agent.keys(), results):
            if isinstance(result, Exception):
                log_parts.append(f"{host_name}: FAILED - {result}")
                all_success = False
            else:
                status = result.get("status", "unknown")
                log_parts.append(f"{host_name}: {status}")
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
            job.status = "completed"  # Mark as completed even with partial failures
            update_lab_state(session, lab_id, "stopped")
            log_parts.append("\nWARNING: Some hosts may have had issues during destroy")

        job.completed_at = datetime.utcnow()
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
                job.completed_at = datetime.utcnow()
                job.log_path = f"ERROR: Unexpected error: {e}"
                update_lab_state(session, lab_id, "error", error=str(e))
                session.commit()
        except Exception:
            pass
    finally:
        session.close()


async def run_node_sync(
    job_id: str,
    lab_id: str,
    node_ids: list[str],
    provider: str = "containerlab",
):
    """Sync nodes to match their desired state.

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
        node_ids: List of node IDs to sync
        provider: Provider for the job (default: containerlab)
    """
    from app.storage import topology_path

    session = SessionLocal()
    try:
        job = session.get(models.Job, job_id)
        if not job:
            logger.error(f"Job {job_id} not found in database")
            return

        lab = session.get(models.Lab, lab_id)
        if not lab:
            logger.error(f"Lab {lab_id} not found in database")
            job.status = "failed"
            job.completed_at = datetime.utcnow()
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
            job.completed_at = datetime.utcnow()
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
        topo_path = topology_path(lab.id)
        if topo_path.exists():
            try:
                topology_yaml = topo_path.read_text(encoding="utf-8")
                graph = yaml_to_graph(topology_yaml)

                # Get the node names we're syncing
                node_names_to_sync = {ns.node_name for ns in node_states}

                # Find host assignments for these nodes
                # Use container_name (yaml key like 'ceos_3') not display name ('CEOS-3')
                node_hosts = {}
                for node in graph.nodes:
                    node_key = node.container_name or node.name
                    if node_key in node_names_to_sync and node.host:
                        node_hosts[node_key] = node.host

                # Determine target agent for ALL nodes (explicit and auto-placed)
                # to avoid spawning jobs that race on the same agent
                all_node_agents: dict[str, str] = {}  # node_name -> agent_id

                # First, assign explicit placements
                # Topology host field can be either agent NAME or agent ID
                for node_name, host_value in node_hosts.items():
                    # Try lookup by name first
                    host_agent = await agent_client.get_agent_by_name(session, host_value, required_provider=provider)
                    if not host_agent:
                        # Try lookup by ID (topology might store ID instead of name)
                        host_agent = session.get(models.Host, host_value)
                        if host_agent and not agent_client.is_agent_online(host_agent):
                            host_agent = None
                    if host_agent:
                        all_node_agents[node_name] = host_agent.id
                    else:
                        logger.warning(f"Agent '{host_value}' not found for node {node_name}")

                # Then, determine agent for auto-placed nodes
                auto_placed_nodes = [ns for ns in node_states if ns.node_name not in node_hosts]
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

                if nodes_by_agent:
                    # Pick the first agent to handle in this job
                    agent_ids = list(nodes_by_agent.keys())
                    target_agent_id = agent_ids[0]
                    node_states = nodes_by_agent[target_agent_id]
                    logger.info(f"Processing {len(node_states)} node(s) on agent {target_agent_id}")

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
                        )
                        session.add(other_job)
                        session.commit()
                        session.refresh(other_job)
                        asyncio.create_task(run_node_sync(other_job.id, lab_id, other_node_ids, provider=provider))

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
            except Exception as e:
                logger.warning(f"Failed to parse topology for host placement: {e}")

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
            job.completed_at = datetime.utcnow()
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
        job.started_at = datetime.utcnow()
        session.commit()

        log_parts = []
        log_parts.append(f"=== Node Sync Job ===")
        log_parts.append(f"Lab: {lab_id}")
        log_parts.append(f"Agent: {agent.id} ({agent.name})")
        log_parts.append(f"Nodes: {', '.join(node_ids)}")
        log_parts.append("")

        # Categorize nodes by what action they need
        nodes_need_deploy = []  # undeployed -> running
        nodes_need_start = []   # stopped -> running
        nodes_need_stop = []    # running -> stopped

        for ns in node_states:
            if ns.desired_state == "running":
                if ns.actual_state in ("undeployed", "pending"):
                    nodes_need_deploy.append(ns)
                elif ns.actual_state in ("stopped", "error"):
                    # Both stopped and error states can be started via docker start
                    # Error state may be from intentional stop (exit code 137/143) or crash
                    # Either way, try starting first - if container doesn't exist, it will fail
                    # and we can fall back to deploy
                    nodes_need_start.append(ns)
                # If already running, nothing to do
            elif ns.desired_state == "stopped":
                if ns.actual_state == "running":
                    nodes_need_stop.append(ns)
                # If already stopped/undeployed, nothing to do

        logger.info(
            f"Sync job {job_id}: deploy={len(nodes_need_deploy)}, "
            f"start={len(nodes_need_start)}, stop={len(nodes_need_stop)}"
        )

        # Mark all nodes that need action as pending
        for ns in nodes_need_deploy + nodes_need_start + nodes_need_stop:
            ns.actual_state = "pending"
            ns.error_message = None
        session.commit()

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

        # Phase 1: If any nodes need deploy, we need to deploy the full topology
        # Containerlab doesn't support per-node deploy, so we deploy all and then stop unwanted
        if nodes_need_deploy:
            log_parts.append("=== Phase 1: Deploy Topology ===")

            # Read topology YAML
            topo_path = topology_path(lab.id)
            if not topo_path.exists():
                error_msg = "No topology file found"
                job.status = "failed"
                job.completed_at = datetime.utcnow()
                job.log_path = f"ERROR: {error_msg}"
                for ns in nodes_need_deploy:
                    ns.actual_state = "error"
                    ns.error_message = error_msg
                session.commit()
                return

            topology_yaml = topo_path.read_text(encoding="utf-8")

            # Convert to containerlab format
            graph = yaml_to_graph(topology_yaml)

            # Get the names of nodes we're actually trying to deploy
            nodes_to_deploy_names = {ns.node_name for ns in nodes_need_deploy}

            # IMPORTANT: Include ALL nodes that belong on this agent, not just nodes being deployed
            # Containerlab's --reconfigure will DESTROY nodes not in the topology!
            # So we must include running nodes to prevent them from being removed.
            from app.topology import TopologyGraph

            # Get all nodes that should be on this agent
            all_agent_node_names = set()
            for n in graph.nodes:
                node_key = n.container_name or n.name
                # Check if this node belongs on this agent
                if n.host:
                    # Explicit host - check if it matches this agent (by name or ID)
                    if n.host == agent.name or n.host == agent.id:
                        all_agent_node_names.add(node_key)
                else:
                    # Auto-placed node - include if it's one we're deploying
                    # (auto-placed nodes on this agent are the ones we're syncing)
                    if node_key in nodes_to_deploy_names:
                        all_agent_node_names.add(node_key)
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
                else:
                    # No placement - assume it's on lab's default agent or this agent
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

            # Track which nodes are actually being deployed (the new ones)
            deployed_node_names = nodes_to_deploy_names & filtered_node_names

            if deployed_node_names:
                clab_yaml = graph_to_containerlab_yaml(filtered_graph, lab.id)
                log_parts.append(f"Deploying {len(filtered_graph.nodes)} node(s) on {agent.name}: {', '.join(deployed_node_names)}")
            else:
                log_parts.append(f"No nodes to deploy on {agent.name}")
                for ns in nodes_need_deploy:
                    ns.actual_state = "error"
                    ns.error_message = "No nodes to deploy"
                session.commit()
                nodes_need_deploy = []

            try:
                result = await agent_client.deploy_to_agent(
                    agent, job_id, lab_id, clab_yaml, provider=provider
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
                                ns.boot_started_at = None
                                log_parts.append(f"  {ns.node_name}: stopped")
                            else:
                                ns.actual_state = "error"
                                ns.error_message = stop_result.get("error", "Stop failed")
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

            except Exception as e:
                error_msg = str(e)
                log_parts.append(f"Deploy FAILED: {error_msg}")
                for ns in nodes_need_deploy:
                    ns.actual_state = "error"
                    ns.error_message = error_msg
                session.commit()
                logger.exception(f"Deploy failed in sync job {job_id}: {e}")

        # Phase 2: Start nodes that are stopped but should be running
        # For containerlab: docker start doesn't recreate network interfaces,
        # so we MUST use clab deploy --reconfigure to properly restart nodes.
        # This redeploys the full topology which recreates all veth pairs.
        if nodes_need_start:
            log_parts.append("")
            log_parts.append("=== Phase 2: Start Nodes (via redeploy) ===")
            log_parts.append("Note: Containerlab requires full redeploy to recreate network interfaces")

            # Read topology YAML
            topo_path = topology_path(lab.id)
            if not topo_path.exists():
                error_msg = "No topology file found"
                for ns in nodes_need_start:
                    ns.actual_state = "error"
                    ns.error_message = error_msg
                session.commit()
                log_parts.append(f"Redeploy FAILED: {error_msg}")
            else:
                topology_yaml = topo_path.read_text(encoding="utf-8")
                graph = yaml_to_graph(topology_yaml)

                # Get the names of nodes we're actually trying to start
                nodes_to_start_names = {ns.node_name for ns in nodes_need_start}

                # IMPORTANT: Include ALL nodes that belong on this agent, not just nodes being started
                # Containerlab's --reconfigure will DESTROY nodes not in the topology!
                from app.topology import TopologyGraph

                # Get all nodes that should be on this agent
                all_agent_node_names = set()
                for n in graph.nodes:
                    node_key = n.container_name or n.name
                    if n.host:
                        if n.host == agent.name or n.host == agent.id:
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

                if deployed_node_names:
                    clab_yaml = graph_to_containerlab_yaml(filtered_graph, lab.id)
                    log_parts.append(f"Redeploying {len(filtered_graph.nodes)} node(s) on {agent.name}: {', '.join(deployed_node_names)}")
                else:
                    log_parts.append(f"No nodes to redeploy on {agent.name}")
                    for ns in nodes_need_start:
                        ns.actual_state = "error"
                        ns.error_message = "No nodes to deploy"
                    session.commit()
                    nodes_need_start = []

                try:
                    result = await agent_client.deploy_to_agent(
                        agent, job_id, lab_id, clab_yaml, provider=provider
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
                                ns.error_message = None
                                if not ns.boot_started_at:
                                    ns.boot_started_at = datetime.now(timezone.utc)

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
                                    ns.boot_started_at = None
                                    log_parts.append(f"  {ns.node_name}: stopped")
                                else:
                                    ns.actual_state = "error"
                                    ns.error_message = stop_result.get("error", "Stop failed")
                                    ns.boot_started_at = None
                                    log_parts.append(f"  {ns.node_name}: FAILED - {ns.error_message}")
                    else:
                        error_msg = result.get("error_message", "Redeploy failed")
                        log_parts.append(f"Redeploy FAILED: {error_msg}")
                        for ns in nodes_need_start:
                            ns.actual_state = "error"
                            ns.error_message = error_msg

                    if result.get("stdout"):
                        log_parts.append(f"\nDeploy STDOUT:\n{result['stdout']}")
                    if result.get("stderr"):
                        log_parts.append(f"\nDeploy STDERR:\n{result['stderr']}")

                except Exception as e:
                    error_msg = str(e)
                    log_parts.append(f"Redeploy FAILED: {error_msg}")
                    for ns in nodes_need_start:
                        ns.actual_state = "error"
                        ns.error_message = error_msg
                    logger.exception(f"Redeploy failed in sync job {job_id}: {e}")

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
                        ns.error_message = None
                        ns.boot_started_at = None
                        log_parts.append(f"  {ns.node_name}: stopped")
                    else:
                        ns.actual_state = "error"
                        ns.error_message = result.get("error", "Stop failed")
                        ns.boot_started_at = None
                        log_parts.append(f"  {ns.node_name}: FAILED - {ns.error_message}")
                except Exception as e:
                    ns.actual_state = "error"
                    ns.error_message = str(e)
                    ns.boot_started_at = None
                    log_parts.append(f"  {ns.node_name}: FAILED - {e}")

            session.commit()

        # Check if any nodes are in error state
        error_count = sum(1 for ns in node_states if ns.actual_state == "error")

        if error_count > 0:
            job.status = "failed"
            log_parts.append(f"\nCompleted with {error_count} error(s)")
        else:
            job.status = "completed"
            log_parts.append("\nAll nodes synced successfully")

        job.completed_at = datetime.utcnow()
        job.log_path = "\n".join(log_parts)
        session.commit()

        logger.info(f"Job {job_id} completed with status: {job.status}")

    except Exception as e:
        logger.exception(f"Job {job_id} failed with unexpected error: {e}")
        try:
            job = session.get(models.Job, job_id)
            if job:
                job.status = "failed"
                job.completed_at = datetime.utcnow()
                job.log_path = f"ERROR: Unexpected error: {e}"
                session.commit()
        except Exception:
            pass
    finally:
        session.close()


def _get_container_name(lab_id: str, node_name: str) -> str:
    """Get the containerlab container name for a node.

    Containerlab names containers as: clab-{lab_id}-{node_name}
    Lab ID is sanitized and truncated to ~20 chars.
    """
    safe_lab_id = re.sub(r'[^a-zA-Z0-9_-]', '', lab_id)[:20]
    return f"clab-{safe_lab_id}-{node_name}"
