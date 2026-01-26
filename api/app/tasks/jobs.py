"""Background job execution functions."""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime

from app import agent_client, models
from app.agent_client import AgentJobError, AgentUnavailableError
from app.db import SessionLocal
from app.topology import analyze_topology, graph_to_containerlab_yaml, split_topology_by_host, yaml_to_graph
from app.utils.lab import update_lab_state

logger = logging.getLogger(__name__)


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
                elif action == "down":
                    update_lab_state(session, lab_id, "stopped")

            else:
                job.status = "failed"
                error_msg = result.get('error_message', 'Unknown error')
                log_content = f"Job failed.\n\nError: {error_msg}\n\n"

                # Update lab state to error
                update_lab_state(session, lab_id, "error", error=error_msg)

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
        session.commit()

        logger.info(f"Job {job_id} completed: multi-host deployment successful")

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
