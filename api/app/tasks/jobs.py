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

                # If all nodes being synced have the same host, use that
                if node_hosts:
                    unique_hosts = set(node_hosts.values())
                    if len(unique_hosts) == 1:
                        target_agent_id = list(unique_hosts)[0]
                        logger.info(f"Node(s) have explicit host placement: {target_agent_id}")
            except Exception as e:
                logger.warning(f"Failed to parse topology for host placement: {e}")

        # Find the agent - either from explicit placement or affinity
        if target_agent_id:
            # Use the explicitly specified agent
            agent = session.get(models.Host, target_agent_id)
            if agent and not agent_client.is_agent_online(agent):
                # Agent is offline or has stale heartbeat, can't use it
                logger.warning(f"Target agent {target_agent_id} is offline or unresponsive")
                agent = None
        else:
            # Fall back to affinity-based selection
            agent = await agent_client.get_agent_for_lab(
                session,
                lab,
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
            clab_yaml = graph_to_containerlab_yaml(graph, lab.id)

            log_parts.append(f"Deploying topology with {len(graph.nodes)} nodes...")

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

                    # Update NodePlacement records for affinity tracking
                    all_node_names = [ns.node_name for ns in all_states]
                    await _update_node_placements(session, lab_id, agent.id, all_node_names)

                    # Clean up orphan containers on old agents if deploy moved
                    if old_agent_ids and agent.id not in old_agent_ids:
                        log_parts.append("")
                        log_parts.append("=== Orphan Cleanup ===")
                        await _cleanup_orphan_containers(
                            session, lab_id, agent.id, old_agent_ids, log_parts
                        )

                    # After deploy, all nodes are running by default in containerlab
                    # We need to stop nodes where desired_state=stopped
                    nodes_to_stop_after_deploy = [
                        ns for ns in all_states
                        if ns.desired_state == "stopped"
                    ]

                    # First, mark all nodes as running (since clab starts them all)
                    for ns in all_states:
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

                # For multi-host labs, filter topology to only include nodes for this agent
                analysis = analyze_topology(graph, default_host=agent.id)
                host_topologies = split_topology_by_host(graph, analysis)

                if agent.id in host_topologies:
                    # Use filtered topology for this host
                    filtered_graph = host_topologies[agent.id]
                    clab_yaml = graph_to_containerlab_yaml(filtered_graph, lab.id)
                    log_parts.append(f"Redeploying {len(filtered_graph.nodes)} node(s) on {agent.name}...")
                else:
                    # No nodes for this host - shouldn't happen but handle gracefully
                    clab_yaml = graph_to_containerlab_yaml(graph, lab.id)
                    log_parts.append(f"Redeploying topology with {len(graph.nodes)} nodes...")

                try:
                    result = await agent_client.deploy_to_agent(
                        agent, job_id, lab_id, clab_yaml, provider=provider
                    )

                    if result.get("status") == "completed":
                        log_parts.append("Redeploy completed successfully")

                        # Capture management IPs for IaC workflows
                        await _capture_node_ips(session, lab_id, agent)

                        # After redeploy, all nodes are running
                        # Get all node states for this lab to update them
                        all_states = (
                            session.query(models.NodeState)
                            .filter(models.NodeState.lab_id == lab_id)
                            .all()
                        )

                        # Update NodePlacement records for affinity tracking
                        all_node_names = [ns.node_name for ns in all_states]
                        await _update_node_placements(session, lab_id, agent.id, all_node_names)

                        # Clean up orphan containers on old agents if deploy moved
                        if old_agent_ids and agent.id not in old_agent_ids:
                            log_parts.append("")
                            log_parts.append("=== Orphan Cleanup ===")
                            await _cleanup_orphan_containers(
                                session, lab_id, agent.id, old_agent_ids, log_parts
                            )

                        # Mark all nodes as running (clab starts them all)
                        for ns in all_states:
                            ns.actual_state = "running"
                            ns.error_message = None
                            if not ns.boot_started_at:
                                ns.boot_started_at = datetime.now(timezone.utc)

                        # Now stop nodes that should be stopped
                        nodes_to_stop_after = [
                            ns for ns in all_states
                            if ns.desired_state == "stopped"
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
