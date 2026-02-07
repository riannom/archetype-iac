"""Client for communicating with Archetype agents."""

from __future__ import annotations

import asyncio
import atexit
import json
import logging
import socket
from datetime import datetime, timedelta
from typing import TypeVar, Callable, Any

import httpx
from sqlalchemy.orm import Session

from app import models
from app.config import settings
from app.db import SessionLocal


logger = logging.getLogger(__name__)


def resolve_agent_ip(address: str) -> str:
    """Extract and resolve IP address from agent address.

    Handles both IP addresses and hostnames. For hostnames, performs DNS
    resolution to get the actual IP address (needed for VXLAN endpoints).

    Args:
        address: Agent address in format "host:port" or "http://host:port"

    Returns:
        Resolved IP address as string
    """
    # Strip protocol and port
    addr = address.replace("http://", "").replace("https://", "")
    host = addr.split(":")[0]

    # Check if it's already an IP address (simple check for IPv4)
    if all(part.isdigit() for part in host.split(".")):
        return host

    # Resolve hostname to IP
    try:
        ip = socket.gethostbyname(host)
        logger.debug(f"Resolved hostname {host} to IP {ip}")
        return ip
    except socket.gaierror as e:
        logger.warning(f"Failed to resolve hostname {host}: {e}, using as-is")
        return host

# Retry configuration (exported for backward compatibility)
MAX_RETRIES = settings.agent_max_retries

# VTEP operations can be slow due to OVS bridge operations
VTEP_OPERATION_TIMEOUT = 60.0

# Cache for healthy agents
_agent_cache: dict[str, tuple[str, datetime]] = {}  # agent_id -> (address, last_check)

# Shared HTTP client with connection pooling
_http_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    """Get the shared HTTP client with connection pooling.

    Creates the client on first use with appropriate connection limits.
    The client is reused across all agent communication for efficiency.
    """
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            limits=httpx.Limits(
                max_connections=100,
                max_keepalive_connections=20,
            ),
            timeout=httpx.Timeout(30.0),
        )
    return _http_client


async def close_http_client() -> None:
    """Close the shared HTTP client.

    Should be called during application shutdown.
    """
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None


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

    Retries on:
    - Connection errors and timeouts (network issues)
    - Transient 5xx errors (502, 503, 504) that indicate temporary issues

    Does not retry on:
    - 4xx client errors
    - 500 Internal Server Error (likely a bug, not transient)
    """
    if max_retries is None:
        max_retries = settings.agent_max_retries

    # Transient HTTP errors that are worth retrying
    TRANSIENT_HTTP_CODES = {502, 503, 504}

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
            status_code = e.response.status_code

            # Retry transient 5xx errors with backoff
            if status_code in TRANSIENT_HTTP_CODES and attempt < max_retries:
                delay = min(
                    settings.agent_retry_backoff_base * (2 ** attempt),
                    settings.agent_retry_backoff_max,
                )
                logger.warning(
                    f"Agent returned {status_code} (attempt {attempt + 1}/{max_retries + 1}), "
                    f"retrying in {delay:.1f}s"
                )
                await asyncio.sleep(delay)
                continue

            # Capture response body for debugging
            error_body = ""
            try:
                error_body = e.response.text[:500]
            except Exception:
                pass

            logger.error(f"Agent returned error: {status_code}")
            raise AgentJobError(
                f"Agent returned HTTP {status_code}",
                stdout="",
                stderr=f"{e}\nResponse: {error_body}" if error_body else str(e),
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
        required_provider: Provider the agent must support (e.g., "docker", "libvirt")
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
    required_provider: str = "docker",
) -> models.Host | None:
    """Get an agent for a lab, respecting node-level affinity.

    This function uses a multi-level affinity strategy:
    1. Query NodePlacement records to find which agent(s) have nodes for this lab
    2. Prefer the agent with the most nodes (to minimize orphan containers)
    3. Fall back to lab.agent_id if no placements exist
    4. Find a new healthy agent if preferred agent is unavailable

    This prevents nodes from getting deployed on different agents than where
    they were previously running, which would cause duplicate containers.
    """
    # Query NodePlacement to find which agent(s) have nodes for this lab
    placements = (
        database.query(models.NodePlacement)
        .filter(models.NodePlacement.lab_id == lab.id)
        .all()
    )

    # Count nodes per agent to find the one with most nodes
    agent_node_counts: dict[str, int] = {}
    for p in placements:
        agent_node_counts[p.host_id] = agent_node_counts.get(p.host_id, 0) + 1

    # Prefer agent with most nodes (or lab.agent_id as fallback)
    if agent_node_counts:
        preferred_agent_id = max(agent_node_counts, key=agent_node_counts.get)
        logger.debug(
            f"Lab {lab.id} has nodes on {len(agent_node_counts)} agent(s), "
            f"preferring {preferred_agent_id} with {agent_node_counts[preferred_agent_id]} nodes"
        )
    else:
        preferred_agent_id = lab.agent_id

    return await get_healthy_agent(
        database,
        required_provider=required_provider,
        prefer_agent_id=preferred_agent_id,
    )


async def get_agent_for_node(
    database: Session,
    lab_id: str,
    node_name: str,
    required_provider: str = "docker",
) -> models.Host | None:
    """Get an agent for a specific node, respecting host placement priority.

    This function uses a consistent priority order for node placement:
    1. Node.host_id (explicit topology placement) - MUST be honored
    2. NodePlacement record (runtime placement from previous deploy)
    3. lab.agent_id (lab's default agent)
    4. Any healthy agent with required provider

    Args:
        database: Database session
        lab_id: Lab identifier
        node_name: Node's container name (YAML key)
        required_provider: Provider the agent must support

    Returns:
        The agent to use for this node, or None if no healthy agent available
    """
    # Step 1: Check Node.host_id (explicit topology placement)
    node = (
        database.query(models.Node)
        .filter(
            models.Node.lab_id == lab_id,
            models.Node.container_name == node_name,
        )
        .first()
    )

    if node and node.host_id:
        agent = database.get(models.Host, node.host_id)
        if agent and is_agent_online(agent):
            logger.debug(f"Node {node_name}: using explicit host {agent.name} from Node.host_id")
            return agent
        else:
            # Explicit placement but agent unavailable
            logger.warning(f"Node {node_name}: explicit host {node.host_id} is unavailable")
            return None  # Don't fall back - explicit placement must be honored

    # Step 2: Check NodePlacement (runtime placement)
    placement = (
        database.query(models.NodePlacement)
        .filter(
            models.NodePlacement.lab_id == lab_id,
            models.NodePlacement.node_name == node_name,
        )
        .first()
    )

    if placement:
        agent = database.get(models.Host, placement.host_id)
        if agent and is_agent_online(agent):
            logger.debug(f"Node {node_name}: using placed host {agent.name} from NodePlacement")
            return agent
        # Placement exists but agent unavailable - fall through to lab default

    # Step 3: Check lab.agent_id
    lab = database.get(models.Lab, lab_id)
    if lab and lab.agent_id:
        agent = database.get(models.Host, lab.agent_id)
        if agent and is_agent_online(agent):
            logger.debug(f"Node {node_name}: using lab default agent {agent.name}")
            return agent

    # Step 4: Find any healthy agent
    return await get_healthy_agent(
        database,
        required_provider=required_provider,
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
        client = get_http_client()
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
    topology: dict,
    provider: str = "docker",
) -> dict:
    """Internal deploy request (for retry wrapper).

    Args:
        url: Agent deploy endpoint URL
        job_id: Job identifier
        lab_id: Lab identifier
        topology: Structured topology dict
        provider: Provider to use
    """
    payload: dict = {
        "job_id": job_id,
        "lab_id": lab_id,
        "provider": provider,
        "topology": topology,
    }

    client = get_http_client()
    response = await client.post(
        url,
        json=payload,
        timeout=settings.agent_deploy_timeout,
    )
    response.raise_for_status()
    return response.json()


async def deploy_to_agent(
    agent: models.Host,
    job_id: str,
    lab_id: str,
    topology: dict | None = None,
    provider: str = "docker",
) -> dict:
    """Send deploy request to agent with retry logic.

    Args:
        agent: The agent to deploy to
        job_id: Job identifier
        lab_id: Lab identifier
        topology: Structured topology dict
        provider: Provider to use (default: docker)

    Returns:
        Agent response dict
    """
    if topology is None:
        raise ValueError("Deploy requires topology JSON; topology_yaml is no longer supported")

    url = f"{get_agent_url(agent)}/jobs/deploy"
    logger.info(f"Deploying lab {lab_id} via agent {agent.id} using provider {provider} (JSON)")

    try:
        # Reduce retries for deploy since it's a long operation and agent has its own deduplication
        result = await with_retry(
            _do_deploy, url, job_id, lab_id, topology, provider, max_retries=1
        )
        logger.info(f"Deploy completed for lab {lab_id}: {result.get('status')}")
        return result
    except AgentError as e:
        e.agent_id = agent.id
        raise


async def _do_destroy(url: str, job_id: str, lab_id: str) -> dict:
    """Internal destroy request (for retry wrapper)."""
    client = get_http_client()
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


async def _do_get_status(url: str) -> dict:
    """Internal status request (for retry wrapper)."""
    client = get_http_client()
    response = await client.get(
        url,
        timeout=settings.agent_status_timeout,
    )
    response.raise_for_status()
    return response.json()


async def get_lab_status_from_agent(
    agent: models.Host,
    lab_id: str,
) -> dict:
    """Get lab status from agent with retry logic."""
    url = f"{get_agent_url(agent)}/labs/{lab_id}/status"

    try:
        return await with_retry(_do_get_status, url, max_retries=1)
    except AgentError as e:
        e.agent_id = agent.id
        raise


async def reconcile_nodes_on_agent(
    agent: models.Host,
    lab_id: str,
    nodes: list[dict],
) -> dict:
    """Reconcile nodes to their desired states on an agent.

    Args:
        agent: The agent managing the nodes
        lab_id: Lab identifier
        nodes: List of dicts with 'container_name' and 'desired_state' keys

    Returns:
        Dict with 'lab_id', 'results' list, and optionally 'error' key
    """
    url = f"{get_agent_url(agent)}/labs/{lab_id}/nodes/reconcile"
    client = get_http_client()

    try:
        response = await client.post(
            url,
            json={"nodes": nodes},
            timeout=settings.agent_deploy_timeout,
        )
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as e:
        raise AgentError(
            f"Reconcile request failed: {e.response.status_code}",
            agent_id=agent.id,
        )
    except httpx.RequestError as e:
        raise AgentError(
            f"Reconcile request failed: {e}",
            agent_id=agent.id,
        )


def get_agent_console_url(agent: models.Host, lab_id: str, node_name: str) -> str:
    """Get WebSocket URL for console on agent."""
    base = get_agent_url(agent)
    # Convert http to ws
    ws_base = base.replace("http://", "ws://").replace("https://", "wss://")
    return f"{ws_base}/console/{lab_id}/{node_name}"


async def check_node_readiness(
    agent: models.Host,
    lab_id: str,
    node_name: str,
    kind: str | None = None,
    provider_type: str | None = None,
) -> dict:
    """Check if a node has completed its boot sequence.

    Args:
        agent: The agent managing the node
        lab_id: Lab identifier
        node_name: Name of the node to check
        kind: Device kind (e.g., "cisco_iosv") - required for VM readiness
        provider_type: Provider type ("docker" or "libvirt") - auto-detected if None

    Returns:
        Dict with 'is_ready', 'message', and optionally 'progress_percent' keys
    """
    url = f"{get_agent_url(agent)}/labs/{lab_id}/nodes/{node_name}/ready"

    # Add query parameters if provided
    params = {}
    if kind:
        params["kind"] = kind
    if provider_type:
        params["provider_type"] = provider_type

    try:
        client = get_http_client()
        response = await client.get(url, params=params, timeout=10.0)
        response.raise_for_status()
        result = response.json()
        return result
    except Exception as e:
        logger.error(f"Failed to check readiness for {node_name} on agent {agent.id}: {e}")
        return {
            "is_ready": False,
            "message": f"Readiness check failed: {str(e)}",
            "progress_percent": None,
        }


async def get_all_agents(database: Session) -> list[models.Host]:
    """Get all registered agents."""
    return database.query(models.Host).all()


async def get_agent_by_name(
    database: Session,
    name: str,
    required_provider: str | None = None,
) -> models.Host | None:
    """Get a healthy agent by name or ID.

    Args:
        database: Database session
        name: Agent name or ID to look for
        required_provider: Optional provider the agent must support

    Returns:
        Agent if found and healthy, None otherwise
    """
    from datetime import timezone
    from sqlalchemy import or_
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=60)

    # Check both name and ID since topology may store either
    agent = (
        database.query(models.Host)
        .filter(
            or_(models.Host.name == name, models.Host.id == name),
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


# --- Orphan Cleanup Functions ---

async def destroy_lab_on_agent(
    agent: models.Host,
    lab_id: str,
) -> dict:
    """Destroy a lab's containers on a specific agent (for orphan cleanup).

    This is used when a lab has moved to a new agent and we need to
    clean up orphaned containers on the old agent.

    Args:
        agent: The agent to clean up
        lab_id: Lab identifier

    Returns:
        Agent response dict with status and details
    """
    from uuid import uuid4

    url = f"{get_agent_url(agent)}/jobs/destroy"
    logger.info(f"Cleaning up orphan containers for lab {lab_id} on agent {agent.id}")

    try:
        client = get_http_client()
        response = await client.post(
            url,
            json={
                "job_id": f"orphan-cleanup-{uuid4()}",
                "lab_id": lab_id,
            },
            timeout=120.0,
        )
        response.raise_for_status()
        result = response.json()
        logger.info(f"Orphan cleanup completed for lab {lab_id} on agent {agent.id}")
        return result
    except Exception as e:
        logger.error(f"Failed to cleanup orphans for lab {lab_id} on agent {agent.id}: {e}")
        return {"status": "failed", "error": str(e)}


async def destroy_container_on_agent(
    agent: models.Host,
    lab_id: str,
    container_name: str,
) -> dict:
    """Destroy a single container on a specific agent.

    This is used for live node removal when a user deletes a node from
    the canvas. It only removes the specified container, not the whole lab.

    Args:
        agent: The agent hosting the container
        lab_id: Lab identifier
        container_name: Name of the container to destroy

    Returns:
        Dict with 'success' bool and 'error' message if failed
    """
    url = f"{get_agent_url(agent)}/containers/{lab_id}/{container_name}"
    logger.info(f"Destroying container {container_name} for lab {lab_id} on agent {agent.id}")

    try:
        client = get_http_client()
        response = await client.delete(
            url,
            timeout=60.0,
        )
        response.raise_for_status()
        result = response.json()
        logger.info(f"Container {container_name} destroyed on agent {agent.id}")
        return {"success": True, **result}
    except Exception as e:
        logger.error(f"Failed to destroy container {container_name} on agent {agent.id}: {e}")
        return {"success": False, "error": str(e)}


# --- Lock Management Functions ---

async def get_agent_lock_status(agent: models.Host) -> dict:
    """Get lock status from an agent.

    Returns:
        Dict with 'locks' list and 'timestamp'
    """
    url = f"{get_agent_url(agent)}/locks/status"

    try:
        client = get_http_client()
        response = await client.get(url, timeout=10.0)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Failed to get lock status from agent {agent.id}: {e}")
        return {"locks": [], "error": str(e)}


async def release_agent_lock(agent: models.Host, lab_id: str) -> dict:
    """Release a stuck lock on an agent.

    Args:
        agent: The agent holding the lock
        lab_id: Lab whose lock should be released

    Returns:
        Dict with 'status' indicating success/failure
    """
    url = f"{get_agent_url(agent)}/locks/{lab_id}/release"

    try:
        client = get_http_client()
        response = await client.post(url, timeout=10.0)
        response.raise_for_status()
        result = response.json()
        if result.get("status") == "cleared":
            logger.info(f"Released stuck lock for lab {lab_id} on agent {agent.id}")
        return result
    except Exception as e:
        logger.error(f"Failed to release lock for lab {lab_id} on agent {agent.id}: {e}")
        return {"status": "error", "error": str(e)}


# Alias for clarity - force_release emphasizes this is for stuck recovery
force_release_lock = release_agent_lock


def is_agent_online(agent: models.Host) -> bool:
    """Check if an agent is considered online based on heartbeat."""
    from datetime import timezone

    if agent.status != "online":
        return False

    if not agent.last_heartbeat:
        return False

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=60)
    return agent.last_heartbeat >= cutoff


# --- Reconciliation Functions ---

async def discover_labs_on_agent(agent: models.Host) -> dict:
    """Discover all running labs on an agent.

    Returns dict with 'labs' key containing list of discovered labs.
    """
    url = f"{get_agent_url(agent)}/discover-labs"

    try:
        client = get_http_client()
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
        client = get_http_client()
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


def compute_vxlan_port_name(lab_id: str, link_name: str) -> str:
    """Compute the deterministic OVS port name for a per-link VXLAN tunnel.

    Must match agent/network/overlay.py:_link_tunnel_interface_name().
    """
    import hashlib

    combined = f"{lab_id}:{link_name}"
    link_hash = hashlib.md5(combined.encode()).hexdigest()[:8]
    return f"vxlan-{link_hash}"


async def reconcile_vxlan_ports_on_agent(agent: models.Host, valid_port_names: list[str]) -> dict:
    """Tell agent which VXLAN ports should exist; agent removes the rest.

    Args:
        agent: The agent to reconcile
        valid_port_names: List of VXLAN port names that should be kept

    Returns dict with 'removed_ports' key listing what was cleaned up.
    """
    url = f"{get_agent_url(agent)}/overlay/reconcile-ports"

    try:
        client = get_http_client()
        response = await client.post(
            url,
            json={"valid_port_names": valid_port_names},
            timeout=60.0,
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Failed to reconcile VXLAN ports on agent {agent.id}: {e}")
        return {"removed_ports": [], "errors": [str(e)]}


async def cleanup_lab_orphans(
    agent: models.Host,
    lab_id: str,
    keep_node_names: list[str],
) -> dict:
    """Tell agent to clean up orphan containers for a specific lab.

    Used when nodes are migrated between agents. Removes containers for
    nodes that are no longer assigned to this agent.

    Args:
        agent: The agent to clean up
        lab_id: Lab identifier
        keep_node_names: List of node names that should be kept on this agent

    Returns dict with 'removed_containers' and 'kept_containers' keys.
    """
    url = f"{get_agent_url(agent)}/cleanup-lab-orphans"

    try:
        client = get_http_client()
        response = await client.post(
            url,
            json={
                "lab_id": lab_id,
                "keep_node_names": keep_node_names,
            },
            timeout=120.0,
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Failed to cleanup lab orphans on agent {agent.id}: {e}")
        return {"removed_containers": [], "kept_containers": [], "errors": [str(e)]}


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
        client = get_http_client()
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
        client = get_http_client()
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
        client = get_http_client()
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


# --- Per-Link VNI Model Functions ---


async def attach_overlay_interface_on_agent(
    agent: models.Host,
    lab_id: str,
    container_name: str,
    interface_name: str,
    vni: int,
    local_ip: str,
    remote_ip: str,
    link_id: str,
    tenant_mtu: int = 0,
) -> dict:
    """Create a per-link VXLAN tunnel and attach a container interface.

    The agent discovers the container's local VLAN and creates an access-mode
    VXLAN port with tag=<local_vlan> and options:key=<vni>.

    Args:
        agent: The agent where the container is running
        lab_id: Lab identifier
        container_name: Docker container name
        interface_name: Interface name inside container (e.g., eth1)
        vni: VXLAN Network Identifier (shared between both sides)
        local_ip: Agent's own IP for VXLAN endpoint
        remote_ip: Remote agent's IP for VXLAN endpoint
        link_id: Link identifier for tracking
        tenant_mtu: Optional MTU (0 = auto-discover)

    Returns:
        Dict with 'success', 'local_vlan', 'vni', and optionally 'error' keys
    """
    url = f"{get_agent_url(agent)}/overlay/attach-link"

    payload = {
        "lab_id": lab_id,
        "container_name": container_name,
        "interface_name": interface_name,
        "vni": vni,
        "local_ip": local_ip,
        "remote_ip": remote_ip,
        "link_id": link_id,
        "tenant_mtu": tenant_mtu,
    }

    try:
        client = get_http_client()
        response = await client.post(url, json=payload, timeout=VTEP_OPERATION_TIMEOUT)
        response.raise_for_status()
        result = response.json()
        if result.get("success"):
            logger.info(
                f"Attached {container_name}:{interface_name} with VNI {vni} "
                f"(local VLAN {result.get('local_vlan')}) on {agent.id}"
            )
        else:
            logger.warning(f"Overlay attach failed on {agent.id}: {result.get('error')}")
        return result
    except Exception as e:
        logger.error(f"Failed to attach overlay interface on agent {agent.id}: {e}")
        return {"success": False, "error": str(e)}


async def detach_overlay_interface_on_agent(
    agent: models.Host,
    lab_id: str,
    container_name: str,
    interface_name: str,
    link_id: str,
) -> dict:
    """Detach a link from the overlay on an agent.

    This performs a complete detach:
    1. Restores the container interface to an isolated VLAN (unique tag)
    2. Deletes the per-link VXLAN tunnel port

    Args:
        agent: The agent to detach on
        lab_id: Lab identifier
        container_name: Container name (short form, e.g., "eos_1")
        interface_name: Interface name inside container (e.g., eth1)
        link_id: Link identifier for tunnel lookup

    Returns:
        Dict with 'success', 'interface_isolated', 'new_vlan',
        'tunnel_deleted' keys
    """
    url = f"{get_agent_url(agent)}/overlay/detach-link"

    payload = {
        "lab_id": lab_id,
        "container_name": container_name,
        "interface_name": interface_name,
        "link_id": link_id,
    }

    try:
        client = get_http_client()
        response = await client.post(url, json=payload, timeout=VTEP_OPERATION_TIMEOUT)
        response.raise_for_status()
        result = response.json()
        if result.get("success"):
            isolated_msg = f" (interface isolated to VLAN {result.get('new_vlan')})" if result.get("interface_isolated") else ""
            tunnel_msg = " (tunnel deleted)" if result.get("tunnel_deleted") else ""
            logger.info(
                f"Detached {container_name}:{interface_name} link {link_id} "
                f"on {agent.id}{isolated_msg}{tunnel_msg}"
            )
        else:
            logger.warning(f"Overlay detach failed on {agent.id}: {result.get('error')}")
        return result
    except Exception as e:
        logger.error(f"Failed to detach overlay interface on agent {agent.id}: {e}")
        return {"success": False, "error": str(e)}


async def setup_cross_host_link_v2(
    database: Session,
    lab_id: str,
    link_id: str,
    agent_a: models.Host,
    agent_b: models.Host,
    node_a: str,
    interface_a: str,
    node_b: str,
    interface_b: str,
) -> dict:
    """Set up a cross-host link using the per-link VNI model.

    Each cross-host link gets its own VXLAN port on each agent in access mode.
    The agent discovers the container's local VLAN and creates the VXLAN port
    with tag=<local_vlan> and options:key=<vni>. No VLAN coordination needed.

    Args:
        database: Database session (unused, kept for compatibility)
        lab_id: Lab identifier
        link_id: Link identifier
        agent_a: First agent
        agent_b: Second agent
        node_a: Container name on agent_a
        interface_a: Interface name in node_a
        node_b: Container name on agent_b
        interface_b: Interface name in node_b

    Returns:
        Dict with 'success' and status information
    """
    from app.services.link_manager import allocate_vni

    # Resolve agent IPs (handles hostnames like "local-agent")
    agent_ip_a = resolve_agent_ip(agent_a.address)
    agent_ip_b = resolve_agent_ip(agent_b.address)

    # Allocate deterministic per-link VNI
    vni = allocate_vni(lab_id, link_id)

    logger.info(
        f"Setting up cross-host link {link_id} (VNI {vni}): "
        f"{agent_a.id}({agent_ip_a}) <-> {agent_b.id}({agent_ip_b})"
    )

    # Retry logic for container attachments - containers may still be starting
    max_retries = 3
    retry_delay = 2.0  # seconds

    async def attach_with_retry(agent, node, interface, local_ip, remote_ip) -> dict:
        """Attempt attachment with retries for timing issues."""
        last_error = None
        for attempt in range(max_retries):
            result = await attach_overlay_interface_on_agent(
                agent,
                lab_id=lab_id,
                container_name=node,
                interface_name=interface,
                vni=vni,
                local_ip=local_ip,
                remote_ip=remote_ip,
                link_id=link_id,
            )
            if result.get("success"):
                return result
            last_error = result.get("error", "unknown error")
            if "not running" in str(last_error).lower() and attempt < max_retries - 1:
                logger.info(
                    f"Container not running, retrying in {retry_delay}s "
                    f"(attempt {attempt + 1}/{max_retries})"
                )
                await asyncio.sleep(retry_delay)
            else:
                break
        return {"success": False, "error": last_error}

    # Create per-link VXLAN ports on both agents in parallel
    attach_a_result, attach_b_result = await asyncio.gather(
        attach_with_retry(agent_a, node_a, interface_a, agent_ip_a, agent_ip_b),
        attach_with_retry(agent_b, node_b, interface_b, agent_ip_b, agent_ip_a),
    )

    # Check if either attachment failed
    attach_errors = []
    if not attach_a_result.get("success"):
        attach_errors.append(
            f"{agent_a.name}:{node_a}:{interface_a}: {attach_a_result.get('error')}"
        )
    if not attach_b_result.get("success"):
        attach_errors.append(
            f"{agent_b.name}:{node_b}:{interface_b}: {attach_b_result.get('error')}"
        )

    if attach_errors:
        error_msg = "; ".join(attach_errors)
        logger.error(
            f"Per-link tunnel creation failed for {link_id}: {error_msg}"
        )
        return {
            "success": False,
            "error": f"Per-link tunnel creation failed: {error_msg}",
            "vni": vni,
        }

    return {
        "success": True,
        "vni": vni,
        "agent_a": agent_a.id,
        "agent_b": agent_b.id,
        "local_vlans": {
            "a": attach_a_result.get("local_vlan"),
            "b": attach_b_result.get("local_vlan"),
        },
    }


async def get_overlay_status_from_agent(agent: models.Host) -> dict:
    """Get overlay status from an agent.

    Returns:
        Dict with 'tunnels' and 'bridges' lists, or empty on error.
    """
    url = f"{get_agent_url(agent)}/overlay/status"

    try:
        client = get_http_client()
        response = await client.get(url, timeout=10.0)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Overlay status failed on agent {agent.id}: {e}")
        return {"tunnels": [], "bridges": []}


def agent_supports_vxlan(agent: models.Host) -> bool:
    """Check if an agent supports VXLAN overlay."""
    caps = parse_capabilities(agent)
    features = caps.get("features", [])
    return "vxlan" in features


async def get_agent_images(agent: models.Host) -> dict:
    """Get list of Docker images on an agent.

    Args:
        agent: The agent to query

    Returns:
        Dict with 'images' list containing DockerImageInfo objects
        Each image has: id, tags, size_bytes, created
    """
    url = f"{get_agent_url(agent)}/images"

    try:
        client = get_http_client()
        response = await client.get(url, timeout=30.0)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Failed to get images from agent {agent.id}: {e}")
        return {"images": []}


async def container_action(
    agent: models.Host,
    container_name: str,
    action: str,  # "start" or "stop"
    lab_id: str | None = None,
) -> dict:
    """Execute start/stop action on a specific container or VM.

    Args:
        agent: The agent where the container/VM is running
        container_name: Full container name (e.g., "arch-labid-nodename")
        action: "start" or "stop"
        lab_id: Optional lab ID. When provided, uses the reconcile endpoint
                which supports both Docker containers and libvirt VMs.

    Returns:
        Dict with 'success' key and optional 'error' message
    """
    logger.info(f"Container {action} for {container_name} via agent {agent.id}")

    # If lab_id is provided, use the reconcile endpoint which handles both
    # Docker containers and libvirt VMs
    if lab_id:
        desired_state = "running" if action == "start" else "stopped"
        try:
            result = await reconcile_nodes_on_agent(
                agent,
                lab_id,
                nodes=[{"container_name": container_name, "desired_state": desired_state}],
            )
            # Extract result for this specific node
            results = result.get("results", [])
            if results:
                node_result = results[0]
                if node_result.get("success"):
                    logger.info(f"Container {action} completed for {container_name}")
                    return {"success": True, "message": f"Container {node_result.get('action', action)}"}
                else:
                    error_msg = node_result.get("error", f"{action} failed")
                    logger.warning(f"Container {action} failed for {container_name}: {error_msg}")
                    return {"success": False, "error": error_msg}
            else:
                return {"success": False, "error": "No result from reconcile"}
        except AgentError as e:
            logger.error(f"Container {action} failed for {container_name}: {e.message}")
            return {"success": False, "error": e.message}
        except Exception as e:
            logger.error(f"Container {action} failed for {container_name}: {e}")
            return {"success": False, "error": str(e)}

    # Legacy path: use the Docker-only endpoint when lab_id is not provided
    url = f"{get_agent_url(agent)}/containers/{container_name}/{action}"

    try:
        client = get_http_client()
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


async def extract_configs_on_agent(
    agent: models.Host,
    lab_id: str,
) -> dict:
    """Extract running configs from all cEOS nodes in a lab.

    Args:
        agent: The agent managing the lab
        lab_id: Lab identifier

    Returns:
        Dict with 'success', 'extracted_count', and optionally 'error' keys
    """
    url = f"{get_agent_url(agent)}/labs/{lab_id}/extract-configs"
    logger.info(f"Extracting configs for lab {lab_id} via agent {agent.id}")

    try:
        client = get_http_client()
        response = await client.post(url, timeout=120.0)
        response.raise_for_status()
        result = response.json()
        if result.get("success"):
            logger.info(f"Extracted {result.get('extracted_count', 0)} configs for lab {lab_id}")
        else:
            logger.warning(f"Config extraction failed for lab {lab_id}: {result.get('error')}")
        return result
    except Exception as e:
        logger.error(f"Failed to extract configs for lab {lab_id} on agent {agent.id}: {e}")
        return {"success": False, "extracted_count": 0, "error": str(e)}


async def update_config_on_agent(
    agent: models.Host,
    lab_id: str,
    node_name: str,
    content: str,
) -> dict:
    """Push a startup config to an agent for a specific node.

    This syncs the API's extracted config to the agent's workspace so
    it will be used on next container restart/redeploy.

    Args:
        agent: The agent managing the node
        lab_id: Lab identifier
        node_name: Node name (container name without lab prefix)
        content: The config content to save

    Returns:
        Dict with 'success' and optionally 'error' keys
    """
    url = f"{get_agent_url(agent)}/labs/{lab_id}/nodes/{node_name}/config"
    logger.debug(f"Pushing config for {node_name} to agent {agent.id}")

    try:
        client = get_http_client()
        response = await client.put(url, json={"content": content}, timeout=30.0)
        response.raise_for_status()
        result = response.json()
        if result.get("success"):
            logger.debug(f"Pushed config for {node_name} to agent {agent.id}")
        else:
            logger.warning(f"Config push failed for {node_name}: {result.get('error')}")
        return result
    except Exception as e:
        logger.error(f"Failed to push config for {node_name} on agent {agent.id}: {e}")
        return {"success": False, "error": str(e)}


async def prune_docker_on_agent(
    agent: models.Host,
    valid_lab_ids: list[str],
    prune_dangling_images: bool = True,
    prune_build_cache: bool = True,
    prune_unused_volumes: bool = False,
) -> dict:
    """Request an agent to prune Docker resources.

    Args:
        agent: The agent to clean up
        valid_lab_ids: List of lab IDs whose resources should be protected
        prune_dangling_images: Whether to prune dangling images
        prune_build_cache: Whether to prune build cache
        prune_unused_volumes: Whether to prune unused volumes (conservative)

    Returns:
        Dict with 'success', 'images_removed', 'build_cache_removed',
        'volumes_removed', 'space_reclaimed', and 'errors' keys
    """
    url = f"{get_agent_url(agent)}/prune-docker"

    try:
        client = get_http_client()
        response = await client.post(
            url,
            json={
                "valid_lab_ids": valid_lab_ids,
                "prune_dangling_images": prune_dangling_images,
                "prune_build_cache": prune_build_cache,
                "prune_unused_volumes": prune_unused_volumes,
            },
            timeout=120.0,  # Docker prune can take a while
        )
        response.raise_for_status()
        result = response.json()
        return result
    except Exception as e:
        logger.error(f"Failed to prune Docker on agent {agent.id}: {e}")
        return {
            "success": False,
            "images_removed": 0,
            "build_cache_removed": 0,
            "volumes_removed": 0,
            "space_reclaimed": 0,
            "errors": [str(e)],
        }


# --- MTU Testing Functions ---


async def test_mtu_on_agent(
    agent: models.Host,
    target_ip: str,
    mtu: int,
) -> dict:
    """Test MTU to a target IP from an agent.

    Runs ping with DF (Don't Fragment) bit set to verify path MTU.
    Also detects link type (direct/routed) via TTL analysis.

    Args:
        agent: The agent to run the test from
        target_ip: Target IP address to test connectivity to
        mtu: MTU size to test

    Returns:
        Dict with 'success', 'tested_mtu', 'link_type', 'latency_ms', 'error' keys
    """
    url = f"{get_agent_url(agent)}/network/test-mtu"

    try:
        client = get_http_client()
        response = await client.post(
            url,
            json={
                "target_ip": target_ip,
                "mtu": mtu,
            },
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"MTU test failed on agent {agent.id}: {e}")
        return {
            "success": False,
            "error": str(e),
        }


# --- Interface Configuration Functions ---


async def get_agent_interface_details(agent: models.Host) -> dict:
    """Get detailed interface information from an agent.

    Returns all interfaces with their MTU, identifies the default route
    interface, and detects which network manager is in use.

    Args:
        agent: The agent to query

    Returns:
        Dict with 'interfaces', 'default_route_interface', 'network_manager' keys
    """
    url = f"{get_agent_url(agent)}/interfaces/details"

    try:
        client = get_http_client()
        response = await client.get(url, timeout=30.0)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Failed to get interface details from agent {agent.id}: {e}")
        raise


async def set_agent_interface_mtu(
    agent: models.Host,
    interface_name: str,
    mtu: int,
    persist: bool = True,
) -> dict:
    """Set MTU on an agent's interface.

    Applies the MTU change and optionally persists it across reboots.

    Args:
        agent: The agent to configure
        interface_name: Name of the interface
        mtu: MTU value to set
        persist: Whether to persist the change across reboots

    Returns:
        Dict with 'success', 'interface', 'previous_mtu', 'new_mtu',
        'persisted', 'network_manager', 'error' keys
    """
    url = f"{get_agent_url(agent)}/interfaces/{interface_name}/mtu"

    try:
        client = get_http_client()
        response = await client.post(
            url,
            json={"mtu": mtu, "persist": persist},
            timeout=60.0,  # Longer timeout for persistence operations
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Failed to set MTU on agent {agent.id} interface {interface_name}: {e}")
        return {
            "success": False,
            "interface": interface_name,
            "previous_mtu": 0,
            "new_mtu": mtu,
            "persisted": False,
            "error": str(e),
        }


# --- Hot-Connect Link Management Functions ---

async def create_link_on_agent(
    agent: models.Host,
    lab_id: str,
    source_node: str,
    source_interface: str,
    target_node: str,
    target_interface: str,
) -> dict:
    """Hot-connect two interfaces on an agent.

    This creates a link between two container interfaces by assigning
    them the same VLAN tag on the OVS bridge.

    Args:
        agent: The agent managing the lab
        lab_id: Lab identifier
        source_node: Source node name
        source_interface: Source interface name (e.g., "eth1")
        target_node: Target node name
        target_interface: Target interface name

    Returns:
        Dict with 'success', 'link', and optionally 'error' keys
    """
    url = f"{get_agent_url(agent)}/labs/{lab_id}/links"
    logger.info(
        f"Hot-connect on agent {agent.id}: "
        f"{source_node}:{source_interface} <-> {target_node}:{target_interface}"
    )

    try:
        client = get_http_client()
        response = await client.post(
            url,
            json={
                "source_node": source_node,
                "source_interface": source_interface,
                "target_node": target_node,
                "target_interface": target_interface,
            },
            timeout=30.0,
        )
        response.raise_for_status()
        result = response.json()
        if result.get("success"):
            logger.info(f"Hot-connect succeeded: {result.get('link', {}).get('link_id')}")
        else:
            logger.warning(f"Hot-connect failed: {result.get('error')}")
        return result
    except Exception as e:
        logger.error(f"Hot-connect failed on agent {agent.id}: {e}")
        return {"success": False, "error": str(e)}


async def delete_link_on_agent(
    agent: models.Host,
    lab_id: str,
    link_id: str,
) -> dict:
    """Hot-disconnect a link on an agent.

    This breaks a link between two container interfaces by assigning
    them separate VLAN tags.

    Args:
        agent: The agent managing the lab
        lab_id: Lab identifier
        link_id: Link identifier (format: "node1:iface1-node2:iface2")

    Returns:
        Dict with 'success' and optionally 'error' keys
    """
    url = f"{get_agent_url(agent)}/labs/{lab_id}/links/{link_id}"
    logger.info(f"Hot-disconnect on agent {agent.id}: {link_id}")

    try:
        client = get_http_client()
        response = await client.delete(url, timeout=30.0)
        response.raise_for_status()
        result = response.json()
        if result.get("success"):
            logger.info(f"Hot-disconnect succeeded: {link_id}")
        else:
            logger.warning(f"Hot-disconnect failed: {result.get('error')}")
        return result
    except Exception as e:
        logger.error(f"Hot-disconnect failed on agent {agent.id}: {e}")
        return {"success": False, "error": str(e)}


async def list_links_on_agent(
    agent: models.Host,
    lab_id: str,
) -> dict:
    """List all links for a lab on an agent.

    Args:
        agent: The agent managing the lab
        lab_id: Lab identifier

    Returns:
        Dict with 'links' list
    """
    url = f"{get_agent_url(agent)}/labs/{lab_id}/links"

    try:
        client = get_http_client()
        response = await client.get(url, timeout=30.0)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"List links failed on agent {agent.id}: {e}")
        return {"links": []}


async def get_ovs_status_from_agent(agent: models.Host) -> dict:
    """Get OVS networking status from an agent.

    Returns:
        Dict with 'bridge_name', 'initialized', 'ports', 'links'
    """
    url = f"{get_agent_url(agent)}/ovs/status"

    try:
        client = get_http_client()
        response = await client.get(url, timeout=10.0)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"OVS status failed on agent {agent.id}: {e}")
        return {"bridge_name": "", "initialized": False, "ports": [], "links": []}


async def connect_external_on_agent(
    agent: models.Host,
    lab_id: str,
    node_name: str,
    interface_name: str,
    external_interface: str,
    vlan_tag: int | None = None,
) -> dict:
    """Connect a container interface to an external network.

    Args:
        agent: The agent managing the lab
        lab_id: Lab identifier
        node_name: Node name
        interface_name: Interface name inside container
        external_interface: External host interface to connect to
        vlan_tag: Optional VLAN for isolation

    Returns:
        Dict with 'success', 'vlan_tag', and optionally 'error' keys
    """
    url = f"{get_agent_url(agent)}/labs/{lab_id}/external/connect"
    logger.info(
        f"External connect on agent {agent.id}: "
        f"{node_name}:{interface_name} -> {external_interface}"
    )

    try:
        client = get_http_client()
        response = await client.post(
            url,
            json={
                "node_name": node_name,
                "interface_name": interface_name,
                "external_interface": external_interface,
                "vlan_tag": vlan_tag,
            },
            timeout=30.0,
        )
        response.raise_for_status()
        result = response.json()
        if result.get("success"):
            logger.info(f"External connect succeeded (VLAN {result.get('vlan_tag')})")
        else:
            logger.warning(f"External connect failed: {result.get('error')}")
        return result
    except Exception as e:
        logger.error(f"External connect failed on agent {agent.id}: {e}")
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

    # Extract and resolve agent IP addresses (handles hostnames like "local-agent")
    agent_ip_a = resolve_agent_ip(agent_a.address)
    agent_ip_b = resolve_agent_ip(agent_b.address)

    logger.info(f"Setting up cross-host link {link_id}: {agent_a.id}({agent_ip_a}) <-> {agent_b.id}({agent_ip_b})")

    # Create tunnel on agent A (pointing to agent B)
    result_a = await create_tunnel_on_agent(
        agent_a,
        lab_id=lab_id,
        link_id=link_id,
        local_ip=agent_ip_a,
        remote_ip=agent_ip_b,
        vni=vni,
    )

    if not result_a.get("success"):
        return {"success": False, "error": f"Failed to create tunnel on {agent_a.id}: {result_a.get('error')}"}

    # Extract VNI from result to use same on both sides
    tunnel_vni = result_a.get("tunnel", {}).get("vni")
    vlan_tag = result_a.get("tunnel", {}).get("vlan_tag")

    # Create tunnel on agent B (pointing to agent A) with same VNI
    result_b = await create_tunnel_on_agent(
        agent_b,
        lab_id=lab_id,
        link_id=link_id,
        local_ip=agent_ip_b,
        remote_ip=agent_ip_a,
        vni=tunnel_vni,
    )

    if not result_b.get("success"):
        # Clean up tunnel on agent A
        await cleanup_overlay_on_agent(agent_a, lab_id)
        return {"success": False, "error": f"Failed to create tunnel on {agent_b.id}: {result_b.get('error')}"}

    # Attach containers to bridges
    # ip_a/ip_b are the interface IPs from the topology (optional, CIDR format)
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
        "vlan_tag": vlan_tag,
        "agent_a": agent_a.id,
        "agent_b": agent_b.id,
        "attachments": {
            "a": attach_a.get("success", False),
            "b": attach_b.get("success", False),
        },
    }


async def get_lab_ports_from_agent(
    agent: models.Host,
    lab_id: str,
) -> list[dict]:
    """Get OVS port information for a lab from an agent.

    Returns list of port info dicts with:
    - port_name: OVS port name
    - bridge_name: OVS bridge name
    - container: Container name (if known)
    - interface: Linux interface name (e.g., "eth1")
    - vlan_tag: Current VLAN tag

    Args:
        agent: The agent to query
        lab_id: Lab identifier

    Returns:
        List of port info dicts
    """
    url = f"{get_agent_url(agent)}/ovs-plugin/labs/{lab_id}/ports"
    client = get_http_client()

    try:
        response = await client.get(url, timeout=10.0)
        response.raise_for_status()
        data = response.json()
        return data.get("ports", [])
    except httpx.HTTPStatusError as e:
        logger.warning(f"Get lab ports failed ({e.response.status_code}): {e}")
        return []
    except httpx.RequestError as e:
        logger.warning(f"Get lab ports request failed: {e}")
        return []


async def get_interface_vlan_from_agent(
    agent: models.Host,
    lab_id: str,
    node_name: str,
    interface: str,
    read_from_ovs: bool = False,
) -> int | None:
    """Get the current VLAN tag for a specific interface from an agent.

    Args:
        agent: The agent managing the node
        lab_id: Lab identifier
        node_name: Container name or node name
        interface: Interface name (e.g., "eth1")
        read_from_ovs: If True, read directly from OVS instead of in-memory state.
                       Use this for verification to get ground truth.

    Returns:
        VLAN tag or None if not found
    """
    url = f"{get_agent_url(agent)}/labs/{lab_id}/interfaces/{node_name}/{interface}/vlan"
    if read_from_ovs:
        url += "?read_from_ovs=true"
    client = get_http_client()

    try:
        response = await client.get(url, timeout=10.0)
        response.raise_for_status()
        data = response.json()
        return data.get("vlan_tag")
    except httpx.HTTPStatusError as e:
        logger.warning(f"Get interface VLAN failed ({e.response.status_code}): {e}")
        return None
    except httpx.RequestError as e:
        logger.warning(f"Get interface VLAN request failed: {e}")
        return None
