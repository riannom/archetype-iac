"""Client for communicating with Archetype agents."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import TypeVar, Callable, Any

import httpx
from sqlalchemy.orm import Session

from app import models
from app.config import settings
from app.db import SessionLocal


logger = logging.getLogger(__name__)

# Cache for healthy agents
_agent_cache: dict[str, tuple[str, datetime]] = {}  # agent_id -> (address, last_check)


class AgentError(Exception):
    """Base exception for agent communication errors."""
    def __init__(self, message: str, agent_id: str | None = None, retriable: bool = False):
        super().__init__(message)
        self.message = message
        self.agent_id = agent_id
        self.retriable = retriable


class AgentUnavailableError(AgentError):
    """Agent is not reachable."""
    def __init__(self, message: str, agent_id: str | None = None):
        super().__init__(message, agent_id, retriable=True)


class AgentJobError(AgentError):
    """Job execution failed on agent."""
    def __init__(self, message: str, agent_id: str | None = None, stdout: str = "", stderr: str = ""):
        super().__init__(message, agent_id, retriable=False)
        self.stdout = stdout
        self.stderr = stderr


async def with_retry(
    func: Callable[..., Any],
    *args,
    max_retries: int | None = None,
    **kwargs,
) -> Any:
    """Execute an async function with exponential backoff retry logic.

    Only retries on connection errors and timeouts, not on application errors.
    """
    if max_retries is None:
        max_retries = settings.agent_max_retries

    last_exception = None

    for attempt in range(max_retries + 1):
        try:
            return await func(*args, **kwargs)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as e:
            last_exception = e
            if attempt < max_retries:
                delay = min(
                    settings.agent_retry_backoff_base * (2 ** attempt),
                    settings.agent_retry_backoff_max,
                )
                logger.warning(
                    f"Agent request failed (attempt {attempt + 1}/{max_retries + 1}), "
                    f"retrying in {delay:.1f}s: {e}"
                )
                await asyncio.sleep(delay)
            else:
                logger.error(f"Agent request failed after {max_retries + 1} attempts: {e}")
                raise AgentUnavailableError(
                    f"Agent unreachable after {max_retries + 1} attempts: {e}"
                )
        except httpx.HTTPStatusError as e:
            # Don't retry on HTTP errors (4xx, 5xx) - these are application-level
            logger.error(f"Agent returned error: {e.response.status_code}")
            raise AgentJobError(
                f"Agent returned HTTP {e.response.status_code}",
                stdout="",
                stderr=str(e),
            )

    # Should never reach here, but just in case
    if last_exception:
        raise AgentUnavailableError(f"Agent request failed: {last_exception}")
    raise AgentUnavailableError("Agent request failed for unknown reason")


def parse_capabilities(agent: models.Host) -> dict:
    """Parse agent capabilities from JSON string."""
    try:
        return json.loads(agent.capabilities) if agent.capabilities else {}
    except json.JSONDecodeError:
        logger.warning(f"Failed to parse capabilities for agent {agent.id}")
        return {}


def get_agent_providers(agent: models.Host) -> list[str]:
    """Get list of providers supported by an agent."""
    caps = parse_capabilities(agent)
    return caps.get("providers", [])


def get_agent_max_jobs(agent: models.Host) -> int:
    """Get max concurrent jobs for an agent."""
    caps = parse_capabilities(agent)
    return caps.get("max_concurrent_jobs", 4)  # Default to 4


def count_active_jobs(database: Session, agent_id: str) -> int:
    """Count number of active (queued or running) jobs on an agent."""
    return (
        database.query(models.Job)
        .filter(
            models.Job.agent_id == agent_id,
            models.Job.status.in_(["queued", "running"]),
        )
        .count()
    )


async def get_healthy_agent(
    database: Session,
    required_provider: str | None = None,
    prefer_agent_id: str | None = None,
    exclude_agents: list[str] | None = None,
) -> models.Host | None:
    """Get a healthy agent to handle jobs with capability-based selection.

    Implements:
    - Capability filtering: Only returns agents that support the required provider
    - Load balancing: Prefers agents with fewer active jobs
    - Resource constraints: Skips agents at max_concurrent_jobs capacity
    - Affinity: Prefers specified agent if healthy and has capacity

    Args:
        database: Database session
        required_provider: Provider the agent must support (e.g., "containerlab", "libvirt")
        prefer_agent_id: Agent ID to prefer for affinity (e.g., lab's current agent)
        exclude_agents: Agent IDs to exclude (e.g., previously failed agents)

    Returns:
        A healthy agent with capacity, or None if none available.
    """
    # Find agents that have sent heartbeat recently (within 60 seconds)
    from datetime import timezone
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=60)
    exclude_agents = exclude_agents or []

    query = database.query(models.Host).filter(
        models.Host.status == "online",
        models.Host.last_heartbeat >= cutoff,
    )

    # Exclude specific agents
    if exclude_agents:
        query = query.filter(~models.Host.id.in_(exclude_agents))

    agents = query.all()

    if not agents:
        return None

    # Filter by required provider capability
    if required_provider:
        agents = [a for a in agents if required_provider in get_agent_providers(a)]
        if not agents:
            logger.warning(f"No agents support required provider: {required_provider}")
            return None

    # Filter by capacity (max_concurrent_jobs)
    agents_with_capacity = []
    for agent in agents:
        active_jobs = count_active_jobs(database, agent.id)
        max_jobs = get_agent_max_jobs(agent)
        if active_jobs < max_jobs:
            agents_with_capacity.append((agent, active_jobs, max_jobs))

    if not agents_with_capacity:
        logger.warning("All agents are at capacity")
        return None

    # If we have a preferred agent (affinity), try to use it
    if prefer_agent_id:
        for agent, active_jobs, max_jobs in agents_with_capacity:
            if agent.id == prefer_agent_id:
                logger.debug(f"Using preferred agent {agent.id} (affinity)")
                return agent

    # Sort by load (active_jobs / max_jobs ratio) - least loaded first
    agents_with_capacity.sort(key=lambda x: x[1] / x[2] if x[2] > 0 else float('inf'))

    selected = agents_with_capacity[0][0]
    logger.debug(
        f"Selected agent {selected.id} ({selected.name}) with "
        f"{agents_with_capacity[0][1]}/{agents_with_capacity[0][2]} active jobs"
    )
    return selected


async def get_agent_for_lab(
    database: Session,
    lab: models.Lab,
    required_provider: str = "containerlab",
) -> models.Host | None:
    """Get an agent for a lab, respecting affinity.

    If the lab already has an assigned agent that is healthy, use it.
    Otherwise, find a new healthy agent with the required capability.
    """
    return await get_healthy_agent(
        database,
        required_provider=required_provider,
        prefer_agent_id=lab.agent_id,
    )


async def mark_agent_offline(database: Session, agent_id: str) -> None:
    """Mark an agent as offline when it becomes unreachable."""
    agent = database.get(models.Host, agent_id)
    if agent and agent.status != "offline":
        agent.status = "offline"
        database.commit()
        logger.warning(f"Agent {agent_id} marked offline")


async def check_agent_health(agent: models.Host) -> bool:
    """Perform a health check on an agent.

    Returns True if healthy, False otherwise.
    """
    url = f"{get_agent_url(agent)}/health"

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=settings.agent_health_check_timeout)
            if response.status_code == 200:
                return True
    except Exception as e:
        logger.debug(f"Health check failed for agent {agent.id}: {e}")

    return False


def get_agent_url(agent: models.Host) -> str:
    """Build base URL for agent API."""
    address = agent.address
    if not address.startswith("http"):
        address = f"http://{address}"
    return address


async def _do_deploy(
    url: str,
    job_id: str,
    lab_id: str,
    topology_yaml: str,
    provider: str = "containerlab",
) -> dict:
    """Internal deploy request (for retry wrapper)."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            url,
            json={
                "job_id": job_id,
                "lab_id": lab_id,
                "topology_yaml": topology_yaml,
                "provider": provider,
            },
            timeout=settings.agent_deploy_timeout,
        )
        response.raise_for_status()
        return response.json()


async def deploy_to_agent(
    agent: models.Host,
    job_id: str,
    lab_id: str,
    topology_yaml: str,
    provider: str = "containerlab",
) -> dict:
    """Send deploy request to agent with retry logic.

    Args:
        agent: The agent to deploy to
        job_id: Job identifier
        lab_id: Lab identifier
        topology_yaml: Topology YAML content
        provider: Provider to use (default: containerlab)

    Returns:
        Agent response dict
    """
    url = f"{get_agent_url(agent)}/jobs/deploy"
    logger.info(f"Deploying lab {lab_id} via agent {agent.id} using provider {provider}")

    try:
        # Reduce retries for deploy since it's a long operation and agent has its own deduplication
        result = await with_retry(_do_deploy, url, job_id, lab_id, topology_yaml, provider, max_retries=1)
        logger.info(f"Deploy completed for lab {lab_id}: {result.get('status')}")
        return result
    except AgentError as e:
        e.agent_id = agent.id
        raise


async def _do_destroy(url: str, job_id: str, lab_id: str) -> dict:
    """Internal destroy request (for retry wrapper)."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            url,
            json={
                "job_id": job_id,
                "lab_id": lab_id,
            },
            timeout=settings.agent_destroy_timeout,
        )
        response.raise_for_status()
        return response.json()


async def destroy_on_agent(
    agent: models.Host,
    job_id: str,
    lab_id: str,
) -> dict:
    """Send destroy request to agent with retry logic."""
    url = f"{get_agent_url(agent)}/jobs/destroy"
    logger.info(f"Destroying lab {lab_id} via agent {agent.id}")

    try:
        result = await with_retry(_do_destroy, url, job_id, lab_id)
        logger.info(f"Destroy completed for lab {lab_id}: {result.get('status')}")
        return result
    except AgentError as e:
        e.agent_id = agent.id
        raise


async def _do_node_action(
    url: str,
    job_id: str,
    lab_id: str,
    node_name: str,
    action: str,
) -> dict:
    """Internal node action request (for retry wrapper)."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            url,
            json={
                "job_id": job_id,
                "lab_id": lab_id,
                "node_name": node_name,
                "action": action,
            },
            timeout=settings.agent_node_action_timeout,
        )
        response.raise_for_status()
        return response.json()


async def node_action_on_agent(
    agent: models.Host,
    job_id: str,
    lab_id: str,
    node_name: str,
    action: str,
) -> dict:
    """Send node action request to agent with retry logic."""
    url = f"{get_agent_url(agent)}/jobs/node-action"
    logger.info(f"Node action {action} on {node_name} in lab {lab_id} via agent {agent.id}")

    try:
        result = await with_retry(_do_node_action, url, job_id, lab_id, node_name, action)
        logger.info(f"Node action completed for {node_name}: {result.get('status')}")
        return result
    except AgentError as e:
        e.agent_id = agent.id
        raise


async def _do_get_status(url: str, lab_id: str) -> dict:
    """Internal status request (for retry wrapper)."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            url,
            json={"lab_id": lab_id},
            timeout=settings.agent_status_timeout,
        )
        response.raise_for_status()
        return response.json()


async def get_lab_status_from_agent(
    agent: models.Host,
    lab_id: str,
) -> dict:
    """Get lab status from agent with retry logic."""
    url = f"{get_agent_url(agent)}/labs/status"

    try:
        return await with_retry(_do_get_status, url, lab_id, max_retries=1)
    except AgentError as e:
        e.agent_id = agent.id
        raise


def get_agent_console_url(agent: models.Host, lab_id: str, node_name: str) -> str:
    """Get WebSocket URL for console on agent."""
    base = get_agent_url(agent)
    # Convert http to ws
    ws_base = base.replace("http://", "ws://").replace("https://", "wss://")
    return f"{ws_base}/console/{lab_id}/{node_name}"


async def get_all_agents(database: Session) -> list[models.Host]:
    """Get all registered agents."""
    return database.query(models.Host).all()


async def get_agent_by_name(
    database: Session,
    name: str,
    required_provider: str | None = None,
) -> models.Host | None:
    """Get a healthy agent by name.

    Args:
        database: Database session
        name: Agent name to look for
        required_provider: Optional provider the agent must support

    Returns:
        Agent if found and healthy, None otherwise
    """
    from datetime import timezone
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=60)

    agent = (
        database.query(models.Host)
        .filter(
            models.Host.name == name,
            models.Host.status == "online",
            models.Host.last_heartbeat >= cutoff,
        )
        .first()
    )

    if not agent:
        logger.warning(f"Agent '{name}' not found or not healthy")
        return None

    # Check provider capability if required
    if required_provider and required_provider not in get_agent_providers(agent):
        logger.warning(f"Agent '{name}' does not support provider '{required_provider}'")
        return None

    return agent


async def update_stale_agents(database: Session, timeout_seconds: int | None = None) -> list[str]:
    """Mark agents as offline if their heartbeat is stale.

    Returns list of agent IDs that were marked offline.
    """
    if timeout_seconds is None:
        timeout_seconds = settings.agent_stale_timeout
    from datetime import timezone
    from sqlalchemy import or_

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)

    # Mark as stale if:
    # 1. last_heartbeat is older than cutoff, OR
    # 2. last_heartbeat is NULL (never heartbeated)
    stale_agents = (
        database.query(models.Host)
        .filter(
            models.Host.status == "online",
            or_(
                models.Host.last_heartbeat < cutoff,
                models.Host.last_heartbeat.is_(None),
            ),
        )
        .all()
    )

    marked_offline = []
    for agent in stale_agents:
        agent.status = "offline"
        marked_offline.append(agent.id)
        logger.warning(f"Agent {agent.id} ({agent.name}) marked offline due to stale heartbeat")

    if marked_offline:
        database.commit()

    return marked_offline


# --- Reconciliation Functions ---

async def discover_labs_on_agent(agent: models.Host) -> dict:
    """Discover all running labs on an agent.

    Returns dict with 'labs' key containing list of discovered labs.
    """
    url = f"{get_agent_url(agent)}/discover-labs"

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=30.0)
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error(f"Failed to discover labs on agent {agent.id}: {e}")
        return {"labs": [], "error": str(e)}


async def cleanup_orphans_on_agent(agent: models.Host, valid_lab_ids: list[str]) -> dict:
    """Tell agent to clean up orphan containers.

    Args:
        agent: The agent to clean up
        valid_lab_ids: List of lab IDs that should be kept

    Returns dict with 'removed_containers' key listing what was cleaned up.
    """
    url = f"{get_agent_url(agent)}/cleanup-orphans"

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                json={"valid_lab_ids": valid_lab_ids},
                timeout=120.0,
            )
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error(f"Failed to cleanup orphans on agent {agent.id}: {e}")
        return {"removed_containers": [], "errors": [str(e)]}


# --- Overlay Networking Functions ---

async def create_tunnel_on_agent(
    agent: models.Host,
    lab_id: str,
    link_id: str,
    local_ip: str,
    remote_ip: str,
    vni: int | None = None,
) -> dict:
    """Create a VXLAN tunnel on an agent.

    Args:
        agent: The agent to create the tunnel on
        lab_id: Lab identifier
        link_id: Link identifier (e.g., "node1:eth0-node2:eth0")
        local_ip: Agent's local IP for VXLAN endpoint
        remote_ip: Remote agent's IP for VXLAN endpoint
        vni: Optional VNI (auto-allocated if not specified)

    Returns:
        Dict with 'success', 'tunnel', and optionally 'error' keys
    """
    url = f"{get_agent_url(agent)}/overlay/tunnel"

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                json={
                    "lab_id": lab_id,
                    "link_id": link_id,
                    "local_ip": local_ip,
                    "remote_ip": remote_ip,
                    "vni": vni,
                },
                timeout=30.0,
            )
            response.raise_for_status()
            result = response.json()
            if result.get("success"):
                logger.info(f"Created tunnel on {agent.id}: {link_id} -> {remote_ip}")
            else:
                logger.warning(f"Tunnel creation failed on {agent.id}: {result.get('error')}")
            return result
    except Exception as e:
        logger.error(f"Failed to create tunnel on agent {agent.id}: {e}")
        return {"success": False, "error": str(e)}


async def attach_container_on_agent(
    agent: models.Host,
    lab_id: str,
    link_id: str,
    container_name: str,
    interface_name: str,
    ip_address: str | None = None,
) -> dict:
    """Attach a container to an overlay bridge on an agent.

    Args:
        agent: The agent where the container is running
        lab_id: Lab identifier
        link_id: Link identifier (matches the tunnel/bridge)
        container_name: Docker container name
        interface_name: Interface name inside container (e.g., eth1)
        ip_address: Optional IP address in CIDR format (e.g., "10.0.0.1/24")

    Returns:
        Dict with 'success' and optionally 'error' keys
    """
    url = f"{get_agent_url(agent)}/overlay/attach"

    payload = {
        "lab_id": lab_id,
        "link_id": link_id,
        "container_name": container_name,
        "interface_name": interface_name,
    }
    if ip_address:
        payload["ip_address"] = ip_address

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=30.0)
            response.raise_for_status()
            result = response.json()
            if result.get("success"):
                ip_info = f" with IP {ip_address}" if ip_address else ""
                logger.info(f"Attached {container_name} to overlay on {agent.id}{ip_info}")
            else:
                logger.warning(f"Container attachment failed on {agent.id}: {result.get('error')}")
            return result
    except Exception as e:
        logger.error(f"Failed to attach container on agent {agent.id}: {e}")
        return {"success": False, "error": str(e)}


async def cleanup_overlay_on_agent(agent: models.Host, lab_id: str) -> dict:
    """Clean up all overlay networking for a lab on an agent.

    Args:
        agent: The agent to clean up
        lab_id: Lab identifier

    Returns:
        Dict with 'tunnels_deleted', 'bridges_deleted', and 'errors' keys
    """
    url = f"{get_agent_url(agent)}/overlay/cleanup"

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                json={"lab_id": lab_id},
                timeout=60.0,
            )
            response.raise_for_status()
            result = response.json()
            logger.info(
                f"Overlay cleanup on {agent.id}: "
                f"{result.get('tunnels_deleted', 0)} tunnels, "
                f"{result.get('bridges_deleted', 0)} bridges"
            )
            return result
    except Exception as e:
        logger.error(f"Failed to cleanup overlay on agent {agent.id}: {e}")
        return {"tunnels_deleted": 0, "bridges_deleted": 0, "errors": [str(e)]}


async def get_overlay_status_from_agent(agent: models.Host) -> dict:
    """Get overlay network status from an agent.

    Returns:
        Dict with 'tunnels' and 'bridges' lists
    """
    url = f"{get_agent_url(agent)}/overlay/status"

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=10.0)
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error(f"Failed to get overlay status from agent {agent.id}: {e}")
        return {"tunnels": [], "bridges": []}


def agent_supports_vxlan(agent: models.Host) -> bool:
    """Check if an agent supports VXLAN overlay."""
    caps = parse_capabilities(agent)
    features = caps.get("features", [])
    return "vxlan" in features


async def container_action(
    agent: models.Host,
    container_name: str,
    action: str,  # "start" or "stop"
) -> dict:
    """Execute start/stop action on a specific container.

    Args:
        agent: The agent where the container is running
        container_name: Full container name (e.g., "clab-labid-nodename")
        action: "start" or "stop"

    Returns:
        Dict with 'success' key and optional 'error' message
    """
    url = f"{get_agent_url(agent)}/containers/{container_name}/{action}"
    logger.info(f"Container {action} for {container_name} via agent {agent.id}")

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, timeout=60.0)
            response.raise_for_status()
            result = response.json()
            if result.get("success"):
                logger.info(f"Container {action} completed for {container_name}")
            else:
                logger.warning(f"Container {action} failed for {container_name}: {result.get('error')}")
            return result
    except httpx.HTTPStatusError as e:
        error_msg = f"HTTP {e.response.status_code}"
        try:
            error_data = e.response.json()
            error_msg = error_data.get("detail", error_msg)
        except Exception:
            pass
        logger.error(f"Container {action} failed for {container_name}: {error_msg}")
        return {"success": False, "error": error_msg}
    except Exception as e:
        logger.error(f"Container {action} failed for {container_name}: {e}")
        return {"success": False, "error": str(e)}


async def setup_cross_host_link(
    database: Session,
    lab_id: str,
    link_id: str,
    agent_a: models.Host,
    agent_b: models.Host,
    node_a: str,
    interface_a: str,
    node_b: str,
    interface_b: str,
    ip_a: str | None = None,
    ip_b: str | None = None,
    vni: int | None = None,
) -> dict:
    """Set up a cross-host link between two agents.

    This creates VXLAN tunnels on both agents and attaches the
    specified containers to the overlay bridges.

    Args:
        database: Database session
        lab_id: Lab identifier
        link_id: Link identifier
        agent_a: First agent
        agent_b: Second agent
        node_a: Container name on agent_a
        interface_a: Interface name in node_a
        node_b: Container name on agent_b
        interface_b: Interface name in node_b
        ip_a: Optional IP address for node_a's interface (CIDR format)
        ip_b: Optional IP address for node_b's interface (CIDR format)
        vni: Optional VNI (auto-allocated if not specified)

    Returns:
        Dict with 'success' and status information
    """
    # Check both agents support VXLAN
    if not agent_supports_vxlan(agent_a):
        return {"success": False, "error": f"Agent {agent_a.id} does not support VXLAN"}
    if not agent_supports_vxlan(agent_b):
        return {"success": False, "error": f"Agent {agent_b.id} does not support VXLAN"}

    # Extract agent IP addresses from their addresses
    # Format is usually "host:port" or "http://host:port"
    addr_a = agent_a.address.replace("http://", "").replace("https://", "")
    addr_b = agent_b.address.replace("http://", "").replace("https://", "")
    ip_a = addr_a.split(":")[0]
    ip_b = addr_b.split(":")[0]

    logger.info(f"Setting up cross-host link {link_id}: {agent_a.id}({ip_a}) <-> {agent_b.id}({ip_b})")

    # Create tunnel on agent A (pointing to agent B)
    result_a = await create_tunnel_on_agent(
        agent_a,
        lab_id=lab_id,
        link_id=link_id,
        local_ip=ip_a,
        remote_ip=ip_b,
        vni=vni,
    )

    if not result_a.get("success"):
        return {"success": False, "error": f"Failed to create tunnel on {agent_a.id}: {result_a.get('error')}"}

    # Extract VNI from result to use same on both sides
    tunnel_vni = result_a.get("tunnel", {}).get("vni")

    # Create tunnel on agent B (pointing to agent A) with same VNI
    result_b = await create_tunnel_on_agent(
        agent_b,
        lab_id=lab_id,
        link_id=link_id,
        local_ip=ip_b,
        remote_ip=ip_a,
        vni=tunnel_vni,
    )

    if not result_b.get("success"):
        # Clean up tunnel on agent A
        await cleanup_overlay_on_agent(agent_a, lab_id)
        return {"success": False, "error": f"Failed to create tunnel on {agent_b.id}: {result_b.get('error')}"}

    # Attach containers to bridges
    attach_a = await attach_container_on_agent(
        agent_a,
        lab_id=lab_id,
        link_id=link_id,
        container_name=node_a,
        interface_name=interface_a,
        ip_address=ip_a,
    )

    if not attach_a.get("success"):
        logger.warning(f"Container attachment on {agent_a.id} failed: {attach_a.get('error')}")

    attach_b = await attach_container_on_agent(
        agent_b,
        lab_id=lab_id,
        link_id=link_id,
        container_name=node_b,
        interface_name=interface_b,
        ip_address=ip_b,
    )

    if not attach_b.get("success"):
        logger.warning(f"Container attachment on {agent_b.id} failed: {attach_b.get('error')}")

    return {
        "success": True,
        "vni": tunnel_vni,
        "agent_a": agent_a.id,
        "agent_b": agent_b.id,
        "attachments": {
            "a": attach_a.get("success", False),
            "b": attach_b.get("success", False),
        },
    }
