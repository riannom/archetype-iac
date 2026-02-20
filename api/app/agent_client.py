"""Client for communicating with Archetype agents."""

from __future__ import annotations

import asyncio
import json
import logging
import socket
from datetime import datetime, timedelta, timezone
from typing import Callable, Any

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models
from app.config import settings
from app.metrics import agent_operation_duration
from app.events.publisher import emit_agent_offline
from app.utils.timeouts import AGENT_HTTP_TIMEOUT, AGENT_VTEP_TIMEOUT


logger = logging.getLogger(__name__)


def _get_agent_auth_headers() -> dict[str, str]:
    """Return auth headers for agent requests if secret is configured."""
    if settings.agent_secret:
        return {"Authorization": f"Bearer {settings.agent_secret}"}
    return {}


async def resolve_agent_ip(address: str) -> str:
    """Extract and resolve IP address from agent address.

    Handles both IP addresses and hostnames. For hostnames, performs async DNS
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

    # Resolve hostname to IP (non-blocking)
    try:
        loop = asyncio.get_running_loop()
        results = await loop.getaddrinfo(host, None, family=socket.AF_INET)
        if results:
            ip = results[0][4][0]
            logger.debug(f"Resolved hostname {host} to IP {ip}")
            return ip
        logger.warning(f"No DNS results for hostname {host}, using as-is")
        return host
    except (socket.gaierror, OSError) as e:
        logger.warning(f"Failed to resolve hostname {host}: {e}, using as-is")
        return host


async def resolve_data_plane_ip(session: Session, agent: models.Host) -> str:
    """Resolve the best IP for VXLAN tunnel endpoints.

    Fallback chain:
    1. agent.data_plane_address (explicitly configured)
    2. Transport managed interface IP (synced from agent)
    3. Management address (resolve_agent_ip fallback)
    """
    if agent.data_plane_address:
        return agent.data_plane_address

    # Check transport managed interfaces
    iface = (
        session.query(models.AgentManagedInterface)
        .filter(
            models.AgentManagedInterface.host_id == agent.id,
            models.AgentManagedInterface.interface_type == "transport",
            models.AgentManagedInterface.sync_status == "synced",
            models.AgentManagedInterface.ip_address.isnot(None),
        )
        .first()
    )
    if iface and iface.ip_address:
        ip = iface.ip_address.split("/")[0]
        logger.info(f"Using transport interface IP {ip} for agent {agent.id}")
        # Backfill data_plane_address for visibility and future use.
        if not agent.data_plane_address:
            try:
                agent.data_plane_address = ip
                session.commit()
            except Exception as e:
                session.rollback()
                logger.warning(
                    f"Failed to persist data_plane_address for agent {agent.id}: {e}"
                )
        return ip

    return await resolve_agent_ip(agent.address)


def _data_plane_mtu_ok(
    session: Session,
    agent_a_id: str,
    agent_b_id: str,
    required_mtu: int,
) -> bool:
    """Check if data-plane MTU tests succeeded for both directions."""
    links = (
        session.query(models.AgentLink)
        .filter(
            models.AgentLink.test_path == "data_plane",
            models.AgentLink.test_status == "success",
            models.AgentLink.tested_mtu.isnot(None),
        )
        .all()
    )
    # Build lookup for (src, dst) -> tested_mtu
    mtus = {(lnk.source_agent_id, lnk.target_agent_id): lnk.tested_mtu for lnk in links}
    a_to_b = mtus.get((agent_a_id, agent_b_id))
    b_to_a = mtus.get((agent_b_id, agent_a_id))
    if a_to_b is None or b_to_a is None:
        return False
    return a_to_b >= required_mtu and b_to_a >= required_mtu


# Retry configuration (exported for backward compatibility)
MAX_RETRIES = settings.agent_max_retries

# VTEP operations can be slow due to OVS bridge operations
VTEP_OPERATION_TIMEOUT = AGENT_VTEP_TIMEOUT

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
            timeout=httpx.Timeout(AGENT_HTTP_TIMEOUT),
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


async def _agent_request(
    method: str,
    url: str,
    *,
    json_body: dict | None = None,
    params: dict | None = None,
    timeout: float | None = None,
    max_retries: int | None = None,
    metric_operation: str | None = None,
    metric_host_id: str | None = None,
) -> dict:
    """Make an agent HTTP request with standardized retry/error handling."""
    client = get_http_client()

    async def _do_request() -> dict:
        response = await client.request(
            method,
            url,
            json=json_body,
            params=params,
            timeout=timeout,
            headers=_get_agent_auth_headers(),
        )
        response.raise_for_status()
        if response.status_code == 204:
            return {}
        return response.json()

    status = "success"
    import time as _time
    _t0 = _time.monotonic()
    try:
        return await with_retry(_do_request, max_retries=max_retries)
    except Exception:
        status = "error"
        raise
    finally:
        if metric_operation and metric_host_id:
            try:
                agent_operation_duration.labels(
                    operation=metric_operation,
                    host_id=metric_host_id,
                    status=status,
                ).observe(_time.monotonic() - _t0)
            except Exception:
                pass


def _agent_online_cutoff(timeout_seconds: int | None = None) -> datetime:
    from datetime import timezone

    if timeout_seconds is None:
        timeout_seconds = settings.agent_stale_timeout
    return datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)


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
    TRANSIENT_HTTP_CODES = {429, 502, 503, 504}

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
    return agent.get_capabilities()


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


def count_active_jobs_by_agent(database: Session, agent_ids: list[str]) -> dict[str, int]:
    """Count active jobs for a batch of agents."""
    if not agent_ids:
        return {}
    rows = (
        database.query(models.Job.agent_id, func.count(models.Job.id))
        .filter(
            models.Job.agent_id.in_(agent_ids),
            models.Job.status.in_(["queued", "running"]),
        )
        .group_by(models.Job.agent_id)
        .all()
    )
    return {agent_id: count for agent_id, count in rows}


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
    # Find agents that have sent heartbeat recently
    cutoff = _agent_online_cutoff()
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

    active_job_counts = count_active_jobs_by_agent(database, [agent.id for agent in agents])

    # Filter by required provider capability
    if required_provider:
        agents = [a for a in agents if required_provider in get_agent_providers(a)]
        if not agents:
            logger.warning(f"No agents support required provider: {required_provider}")
            return None

    # Filter by capacity (max_concurrent_jobs)
    agents_with_capacity = []
    for agent in agents:
        active_jobs = active_job_counts.get(agent.id, 0)
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

    # Resource-aware scoring (when enabled)
    if settings.placement_scoring_enabled:
        from app.services.resource_capacity import score_agent

        scored = []
        for agent, active_jobs, max_jobs in agents_with_capacity:
            agent_score = score_agent(agent)
            scored.append((agent, agent_score))
            logger.debug(
                f"Agent {agent.id} ({agent.name}): score={agent_score.score:.3f} "
                f"({agent_score.reason})"
            )

        scored.sort(key=lambda x: x[1].score, reverse=True)
        selected = scored[0][0]
        logger.debug(
            f"Selected agent {selected.id} ({selected.name}) with "
            f"score={scored[0][1].score:.3f}"
        )
        return selected

    # Legacy: sort by load (active_jobs / max_jobs ratio) - least loaded first
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
    placement_counts = (
        database.query(
            models.NodePlacement.host_id,
            func.count(models.NodePlacement.id),
        )
        .filter(models.NodePlacement.lab_id == lab.id)
        .group_by(models.NodePlacement.host_id)
        .all()
    )

    # Count nodes per agent to find the one with most nodes
    agent_node_counts: dict[str, int] = {host_id: count for host_id, count in placement_counts}

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
        asyncio.create_task(emit_agent_offline(agent_id))


async def check_agent_health(agent: models.Host) -> bool:
    """Perform a health check on an agent.

    Returns True if healthy, False otherwise.
    """
    url = f"{get_agent_url(agent)}/health"

    try:
        client = get_http_client()
        response = await client.get(url, timeout=settings.agent_health_check_timeout, headers=_get_agent_auth_headers())
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
    logger.info(
        "Agent request",
        extra={
            "event": "agent_request",
            "method": "deploy",
            "agent_id": agent.id,
            "agent_name": agent.name,
            "lab_id": lab_id,
            "provider": provider,
        },
    )

    import time as _time
    _t0 = _time.monotonic()
    try:
        # Reduce retries for deploy since it's a long operation and agent has its own deduplication
        payload: dict = {
            "job_id": job_id,
            "lab_id": lab_id,
            "provider": provider,
            "topology": topology,
        }
        result = await _agent_request(
            "POST",
            url,
            json_body=payload,
            timeout=settings.agent_deploy_timeout,
            max_retries=1,
        )
        elapsed_ms = int((_time.monotonic() - _t0) * 1000)
        logger.info(
            "Agent response",
            extra={
                "event": "agent_response",
                "method": "deploy",
                "agent_id": agent.id,
                "lab_id": lab_id,
                "status": result.get("status", "unknown"),
                "duration_ms": elapsed_ms,
            },
        )
        return result
    except AgentError as e:
        elapsed_ms = int((_time.monotonic() - _t0) * 1000)
        logger.info(
            "Agent response",
            extra={
                "event": "agent_response",
                "method": "deploy",
                "agent_id": agent.id,
                "lab_id": lab_id,
                "status": "error",
                "duration_ms": elapsed_ms,
                "error": e.message,
            },
        )
        e.agent_id = agent.id
        raise


async def destroy_on_agent(
    agent: models.Host,
    job_id: str,
    lab_id: str,
) -> dict:
    """Send destroy request to agent with retry logic."""
    url = f"{get_agent_url(agent)}/jobs/destroy"
    logger.info(f"Destroying lab {lab_id} via agent {agent.id}")

    try:
        result = await _agent_request(
            "POST",
            url,
            json_body={"job_id": job_id, "lab_id": lab_id},
            timeout=settings.agent_destroy_timeout,
        )
        logger.info(f"Destroy completed for lab {lab_id}: {result.get('status')}")
        return result
    except AgentError as e:
        e.agent_id = agent.id
        raise


async def get_lab_status_from_agent(
    agent: models.Host,
    lab_id: str,
) -> dict:
    """Get lab status from agent with retry logic."""
    url = f"{get_agent_url(agent)}/labs/{lab_id}/status"

    try:
        return await _agent_request(
            "GET",
            url,
            timeout=settings.agent_status_timeout,
            max_retries=1,
            metric_operation="get_lab_status",
            metric_host_id=agent.id,
        )
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
    try:
        return await _agent_request(
            "POST",
            url,
            json_body={"nodes": nodes},
            timeout=settings.agent_deploy_timeout,
            max_retries=0,
        )
    except AgentError as e:
        raise AgentError(
            f"Reconcile request failed: {e}",
            agent_id=agent.id,
        ) from e


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
        return await _agent_request(
            "GET",
            url,
            params=params or None,
            timeout=10.0,
            max_retries=0,
            metric_operation="check_node_readiness",
            metric_host_id=agent.id,
        )
    except Exception as e:
        logger.error(f"Failed to check readiness for {node_name} on agent {agent.id}: {e}")
        return {
            "is_ready": False,
            "message": f"Readiness check failed: {str(e)}",
            "progress_percent": None,
        }


async def get_node_runtime_profile(
    agent: models.Host,
    lab_id: str,
    node_name: str,
    provider_type: str | None = None,
) -> dict:
    """Get runtime profile for a node from an agent."""
    url = f"{get_agent_url(agent)}/labs/{lab_id}/nodes/{node_name}/runtime"
    params = {"provider_type": provider_type} if provider_type else None
    return await _agent_request(
        "GET",
        url,
        params=params,
        timeout=10.0,
        max_retries=0,
        metric_operation="get_node_runtime",
        metric_host_id=agent.id,
    )


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
    from sqlalchemy import or_
    cutoff = _agent_online_cutoff()

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
    from sqlalchemy import or_
    cutoff = _agent_online_cutoff(timeout_seconds)

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
        for aid in marked_offline:
            asyncio.create_task(emit_agent_offline(aid))

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
        result = await _agent_request(
            "POST",
            url,
            json_body={
                "job_id": f"orphan-cleanup-{uuid4()}",
                "lab_id": lab_id,
            },
            timeout=120.0,
            max_retries=0,
        )
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
        result = await _agent_request(
            "DELETE",
            url,
            timeout=60.0,
            max_retries=0,
        )
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
        return await _agent_request(
            "GET",
            url,
            timeout=10.0,
            max_retries=0,
        )
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
        result = await _agent_request(
            "POST",
            url,
            timeout=10.0,
            max_retries=0,
        )
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
    if agent.status != "online":
        return False

    if not agent.last_heartbeat:
        return False

    heartbeat = agent.last_heartbeat
    # SQLite can return naive datetimes in tests; treat those as UTC.
    if heartbeat.tzinfo is None:
        heartbeat = heartbeat.replace(tzinfo=timezone.utc)

    return heartbeat >= _agent_online_cutoff()


async def ping_agent(agent: models.Host, timeout: float = 5.0) -> bool:
    """Verify agent is reachable via HTTP health check.

    Goes beyond heartbeat freshness — actually makes an HTTP call to
    confirm the agent is responsive right now.

    Raises AgentUnavailableError if unreachable.
    """
    url = f"{get_agent_url(agent)}/health"
    try:
        await _agent_request("GET", url, timeout=timeout, max_retries=0)
        return True
    except Exception as e:
        raise AgentUnavailableError(
            f"Agent {agent.name or agent.id} unreachable: {e}",
            agent_id=agent.id,
        ) from e


async def query_agent_capacity(agent: models.Host, timeout: float = 5.0) -> dict:
    """Query real-time capacity from an agent's /capacity endpoint.

    Returns dict with memory_total_gb, cpu_count, allocated_vcpus,
    allocated_memory_mb, etc. Falls back to empty dict on failure.
    """
    url = f"{get_agent_url(agent)}/capacity"
    try:
        return await _agent_request("GET", url, timeout=timeout, max_retries=0)
    except Exception as e:
        logger.warning(
            f"Capacity query failed for agent {agent.name or agent.id}: {e}"
        )
        return {}


# --- Reconciliation Functions ---

async def discover_labs_on_agent(agent: models.Host) -> dict:
    """Discover all running labs on an agent.

    Returns dict with 'labs' key containing list of discovered labs.
    """
    url = f"{get_agent_url(agent)}/discover-labs"

    try:
        return await _agent_request(
            "GET",
            url,
            timeout=30.0,
            max_retries=0,
        )
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
        return await _agent_request(
            "POST",
            url,
            json_body={"valid_lab_ids": valid_lab_ids},
            timeout=120.0,
            max_retries=0,
        )
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


async def reconcile_vxlan_ports_on_agent(
    agent: models.Host,
    valid_port_names: list[str],
    force: bool = False,
    confirm: bool = False,
    allow_empty: bool = False,
) -> dict:
    """Tell agent which VXLAN ports should exist; agent removes the rest.

    Args:
        agent: The agent to reconcile
        valid_port_names: List of VXLAN port names that should be kept

    Returns dict with 'removed_ports' key listing what was cleaned up.
    """
    url = f"{get_agent_url(agent)}/overlay/reconcile-ports"

    try:
        return await _agent_request(
            "POST",
            url,
            json_body={
                "valid_port_names": valid_port_names,
                "force": force,
                "confirm": confirm,
                "allow_empty": allow_empty,
            },
            timeout=60.0,
            max_retries=0,
        )
    except Exception as e:
        logger.error(f"Failed to reconcile VXLAN ports on agent {agent.id}: {e}")
        return {"removed_ports": [], "errors": [str(e)]}


async def declare_overlay_state_on_agent(
    agent: models.Host,
    tunnels: list[dict],
) -> dict:
    """Declare full desired overlay state on an agent.

    The agent converges to match: creates missing, updates drifted,
    removes orphans. This is a superset of reconcile_vxlan_ports_on_agent.

    Args:
        agent: The agent to converge
        tunnels: List of declared tunnel dicts with keys:
            link_id, lab_id, vni, local_ip, remote_ip,
            expected_vlan, port_name, mtu

    Returns:
        Dict with 'results' list and 'orphans_removed' list.
        Falls back to whitelist reconciliation if agent returns 404.
    """
    url = f"{get_agent_url(agent)}/overlay/declare-state"

    try:
        result = await _agent_request(
            "POST",
            url,
            json_body={"tunnels": tunnels},
            timeout=VTEP_OPERATION_TIMEOUT,
            max_retries=0,
        )
        return result
    except Exception as e:
        error_msg = str(e)
        # 404 means agent is old version — fall back to whitelist approach
        if "404" in error_msg or "Not Found" in error_msg:
            logger.warning(
                f"Agent {agent.name} does not support declare-state (404), "
                f"falling back to whitelist reconciliation"
            )
            valid_ports = [t["port_name"] for t in tunnels]
            return await reconcile_vxlan_ports_on_agent(
                agent,
                valid_port_names=valid_ports,
                confirm=True,
            )
        logger.error(f"Failed to declare overlay state on agent {agent.id}: {e}")
        return {"results": [], "orphans_removed": [], "error": error_msg}


async def get_lab_port_state(
    agent: models.Host,
    lab_id: str,
) -> list[dict]:
    """Get OVS port state for a lab from an agent.

    Returns lightweight port info (port name, VLAN tag, carrier)
    for bulk InterfaceMapping refresh.

    Args:
        agent: The agent to query
        lab_id: Lab identifier

    Returns:
        List of port info dicts
    """
    url = f"{get_agent_url(agent)}/labs/{lab_id}/port-state"
    try:
        data = await _agent_request(
            "GET",
            url,
            timeout=10.0,
            max_retries=0,
        )
        return data.get("ports", [])
    except Exception as e:
        logger.warning(f"Get lab port state failed on {agent.name}: {e}")
        return []


async def declare_port_state_on_agent(
    agent: models.Host,
    pairings: list[dict],
) -> dict:
    """Declare same-host port state on an agent.

    The agent converges port VLAN tags to match declared pairings.

    Args:
        agent: The agent to converge
        pairings: List of port pairing dicts with keys:
            link_name, lab_id, port_a, port_b, vlan_tag

    Returns:
        Dict with 'results' list
    """
    url = f"{get_agent_url(agent)}/ports/declare-state"
    try:
        result = await _agent_request(
            "POST",
            url,
            json_body={"pairings": pairings},
            timeout=VTEP_OPERATION_TIMEOUT,
            max_retries=0,
        )
        return result
    except Exception as e:
        logger.error(f"Failed to declare port state on agent {agent.id}: {e}")
        return {"results": [], "error": str(e)}


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
        return await _agent_request(
            "POST",
            url,
            json_body={
                "lab_id": lab_id,
                "keep_node_names": keep_node_names,
            },
            timeout=120.0,
            max_retries=0,
        )
    except Exception as e:
        logger.error(f"Failed to cleanup lab orphans on agent {agent.id}: {e}")
        return {"removed_containers": [], "kept_containers": [], "errors": [str(e)]}


# --- Overlay Networking Functions ---

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
        result = await _agent_request(
            "POST",
            url,
            json_body=payload,
            timeout=30.0,
            max_retries=0,
        )
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
        result = await _agent_request(
            "POST",
            url,
            json_body={"lab_id": lab_id},
            timeout=60.0,
            max_retries=0,
        )
        logger.info(
            f"Overlay cleanup on {agent.id}: "
            f"{result.get('tunnels_deleted', 0)} tunnels, "
            f"{result.get('bridges_deleted', 0)} bridges"
        )
        return result
    except Exception as e:
        logger.error(f"Failed to cleanup overlay on agent {agent.id}: {e}")
        return {"tunnels_deleted": 0, "bridges_deleted": 0, "errors": [str(e)]}


async def get_cleanup_audit_from_agent(agent: models.Host, include_ovs: bool = False) -> dict:
    """Get a dry-run cleanup audit from an agent (no deletions)."""
    url = f"{get_agent_url(agent)}/cleanup/audit"

    try:
        return await _agent_request(
            "POST",
            url,
            json_body={"include_ovs": include_ovs},
            timeout=30.0,
            max_retries=0,
        )
    except Exception as e:
        logger.error(f"Cleanup audit failed on agent {agent.id}: {e}")
        return {"network": {}, "ovs": None, "errors": [str(e)]}


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
        result = await _agent_request(
            "POST",
            url,
            json_body=payload,
            timeout=VTEP_OPERATION_TIMEOUT,
            max_retries=0,
        )
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
        result = await _agent_request(
            "POST",
            url,
            json_body=payload,
            timeout=VTEP_OPERATION_TIMEOUT,
            max_retries=0,
        )
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
        database: Database session (used for transport interface IP lookup)
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
    from app.routers.infrastructure import get_or_create_settings

    # Read overlay MTU from infrastructure settings
    infra = get_or_create_settings(database)
    overlay_mtu = infra.overlay_mtu or 0

    # Prefer data plane addresses for VXLAN tunnels, but only if MTU tests validate it.
    required_mtu = overlay_mtu if overlay_mtu and overlay_mtu > 0 else 1500
    if _data_plane_mtu_ok(database, agent_a.id, agent_b.id, required_mtu):
        agent_ip_a = await resolve_data_plane_ip(database, agent_a)
        agent_ip_b = await resolve_data_plane_ip(database, agent_b)
    else:
        logger.warning(
            "Data-plane MTU test missing/insufficient between agents "
            f"{agent_a.id} and {agent_b.id} (required_mtu={required_mtu}). "
            "Using management IPs for VXLAN; run MTU tests to enable transport."
        )
        agent_ip_a = await resolve_agent_ip(agent_a.address)
        agent_ip_b = await resolve_agent_ip(agent_b.address)

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
                tenant_mtu=overlay_mtu,
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

        # Best-effort rollback: detach the side that succeeded
        rollback_tasks = []
        if attach_a_result.get("success"):
            rollback_tasks.append(
                detach_overlay_interface_on_agent(
                    agent_a,
                    lab_id=lab_id,
                    container_name=node_a,
                    interface_name=interface_a,
                    link_id=link_id,
                )
            )
        if attach_b_result.get("success"):
            rollback_tasks.append(
                detach_overlay_interface_on_agent(
                    agent_b,
                    lab_id=lab_id,
                    container_name=node_b,
                    interface_name=interface_b,
                    link_id=link_id,
                )
            )
        if rollback_tasks:
            try:
                await asyncio.gather(*rollback_tasks)
                logger.info(f"Rolled back partial attachments for {link_id}")
            except Exception as e:
                logger.warning(f"Rollback failed for {link_id}: {e}")
                # Track which agents still have partial state for reconciliation
                agents_with_state = []
                if attach_a_result.get("success"):
                    agents_with_state.append(agent_a.id)
                if attach_b_result.get("success"):
                    agents_with_state.append(agent_b.id)
                return {
                    "success": False,
                    "error": f"Per-link tunnel creation failed: {error_msg}",
                    "vni": vni,
                    "partial_state": True,
                    "agents_with_state": agents_with_state,
                }

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
        return await _agent_request(
            "GET",
            url,
            timeout=10.0,
            max_retries=0,
        )
    except Exception as e:
        logger.error(f"Overlay status failed on agent {agent.id}: {e}")
        return {"tunnels": [], "bridges": [], "error": str(e)}


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
        return await _agent_request(
            "GET",
            url,
            timeout=30.0,
            max_retries=0,
        )
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
        result = await _agent_request(
            "POST",
            url,
            timeout=60.0,
            max_retries=0,
        )
        if result.get("success"):
            logger.info(f"Container {action} completed for {container_name}")
        else:
            logger.warning(f"Container {action} failed for {container_name}: {result.get('error')}")
        return result
    except AgentJobError as e:
        error_msg = e.message
        try:
            if e.stderr and "Response:" in e.stderr:
                error_body = e.stderr.split("Response:", 1)[1].strip()
                data = json.loads(error_body)
                if isinstance(data, dict):
                    error_msg = data.get("detail", error_msg)
        except Exception:
            pass
        logger.error(f"Container {action} failed for {container_name}: {error_msg}")
        return {"success": False, "error": error_msg}
    except Exception as e:
        logger.error(f"Container {action} failed for {container_name}: {e}")
        return {"success": False, "error": str(e)}


async def create_node_on_agent(
    agent: models.Host,
    lab_id: str,
    node_name: str,
    kind: str,
    *,
    image: str | None = None,
    display_name: str | None = None,
    interface_count: int | None = None,
    binds: list[str] | None = None,
    env: dict[str, str] | None = None,
    startup_config: str | None = None,
    provider: str = "docker",
    memory: int | None = None,
    cpu: int | None = None,
    cpu_limit: int | None = None,
    disk_driver: str | None = None,
    nic_driver: str | None = None,
    machine_type: str | None = None,
    libvirt_driver: str | None = None,
    readiness_probe: str | None = None,
    readiness_pattern: str | None = None,
    readiness_timeout: int | None = None,
    efi_boot: bool | None = None,
    efi_vars: str | None = None,
    data_volume_gb: int | None = None,
    image_sha256: str | None = None,
) -> dict:
    """Create a single node container on an agent without starting it.

    Returns:
        Dict with 'success', 'container_name', 'status', and optionally 'error' keys.
    """
    url = f"{get_agent_url(agent)}/labs/{lab_id}/nodes/{node_name}/create?provider={provider}"
    logger.info(
        "Agent request",
        extra={
            "event": "agent_request",
            "method": "create_node",
            "agent_id": agent.id,
            "agent_name": agent.name,
            "lab_id": lab_id,
            "node_name": node_name,
        },
    )

    payload: dict = {"node_name": node_name, "kind": kind}
    if image:
        payload["image"] = image
    if display_name:
        payload["display_name"] = display_name
    if interface_count is not None:
        payload["interface_count"] = interface_count
    if binds:
        payload["binds"] = binds
    if env:
        payload["env"] = env
    if startup_config:
        payload["startup_config"] = startup_config
    if memory:
        payload["memory"] = memory
    if cpu:
        payload["cpu"] = cpu
    if cpu_limit is not None:
        payload["cpu_limit"] = cpu_limit
    if disk_driver:
        payload["disk_driver"] = disk_driver
    if nic_driver:
        payload["nic_driver"] = nic_driver
    if machine_type:
        payload["machine_type"] = machine_type
    if libvirt_driver:
        payload["libvirt_driver"] = libvirt_driver
    if readiness_probe:
        payload["readiness_probe"] = readiness_probe
    if readiness_pattern:
        payload["readiness_pattern"] = readiness_pattern
    if readiness_timeout:
        payload["readiness_timeout"] = readiness_timeout
    if efi_boot is not None:
        payload["efi_boot"] = efi_boot
    if efi_vars:
        payload["efi_vars"] = efi_vars
    if data_volume_gb is not None:
        payload["data_volume_gb"] = data_volume_gb
    if image_sha256:
        payload["image_sha256"] = image_sha256

    import time as _time
    _t0 = _time.monotonic()
    try:
        result = await _agent_request(
            "POST",
            url,
            json_body=payload,
            timeout=120.0,
            max_retries=0,
        )
        elapsed_ms = int((_time.monotonic() - _t0) * 1000)
        agent_operation_duration.labels(
            operation="create_node", host_id=agent.id, status="success",
        ).observe(elapsed_ms / 1000)
        logger.info(
            "Agent response",
            extra={
                "event": "agent_response",
                "method": "create_node",
                "agent_id": agent.id,
                "lab_id": lab_id,
                "node_name": node_name,
                "status": "success" if result.get("success") else "error",
                "duration_ms": elapsed_ms,
                "agent_duration_ms": result.get("duration_ms"),
                "error": result.get("error") if not result.get("success") else None,
            },
        )
        return result
    except Exception as e:
        elapsed_ms = int((_time.monotonic() - _t0) * 1000)
        agent_operation_duration.labels(
            operation="create_node", host_id=agent.id, status="error",
        ).observe(elapsed_ms / 1000)
        logger.info(
            "Agent response",
            extra={
                "event": "agent_response",
                "method": "create_node",
                "agent_id": agent.id,
                "lab_id": lab_id,
                "node_name": node_name,
                "status": "error",
                "duration_ms": elapsed_ms,
                "error": str(e),
            },
        )
        return {"success": False, "error": str(e)}


async def start_node_on_agent(
    agent: models.Host,
    lab_id: str,
    node_name: str,
    *,
    repair_endpoints: bool = True,
    fix_interfaces: bool = True,
    provider: str = "docker",
) -> dict:
    """Start a node on an agent with optional veth repair.

    Returns:
        Dict with 'success', 'status', 'endpoints_repaired', 'interfaces_fixed',
        and optionally 'error' keys.
    """
    url = f"{get_agent_url(agent)}/labs/{lab_id}/nodes/{node_name}/start?provider={provider}"
    logger.info(
        "Agent request",
        extra={
            "event": "agent_request",
            "method": "start_node",
            "agent_id": agent.id,
            "agent_name": agent.name,
            "lab_id": lab_id,
            "node_name": node_name,
        },
    )

    import time as _time
    _t0 = _time.monotonic()
    try:
        result = await _agent_request(
            "POST",
            url,
            json_body={
                "repair_endpoints": repair_endpoints,
                "fix_interfaces": fix_interfaces,
            },
            timeout=120.0,
            max_retries=0,
        )
        elapsed_ms = int((_time.monotonic() - _t0) * 1000)
        agent_operation_duration.labels(
            operation="start_node", host_id=agent.id, status="success",
        ).observe(elapsed_ms / 1000)
        logger.info(
            "Agent response",
            extra={
                "event": "agent_response",
                "method": "start_node",
                "agent_id": agent.id,
                "lab_id": lab_id,
                "node_name": node_name,
                "status": "success" if result.get("success") else "error",
                "duration_ms": elapsed_ms,
                "agent_duration_ms": result.get("duration_ms"),
                "error": result.get("error") if not result.get("success") else None,
            },
        )
        return result
    except Exception as e:
        elapsed_ms = int((_time.monotonic() - _t0) * 1000)
        agent_operation_duration.labels(
            operation="start_node", host_id=agent.id, status="error",
        ).observe(elapsed_ms / 1000)
        logger.info(
            "Agent response",
            extra={
                "event": "agent_response",
                "method": "start_node",
                "agent_id": agent.id,
                "lab_id": lab_id,
                "node_name": node_name,
                "status": "error",
                "duration_ms": elapsed_ms,
                "error": str(e),
            },
        )
        return {"success": False, "error": str(e)}


async def stop_node_on_agent(
    agent: models.Host,
    lab_id: str,
    node_name: str,
    *,
    provider: str = "docker",
) -> dict:
    """Stop a node on an agent.

    Returns:
        Dict with 'success', 'status', and optionally 'error' keys.
    """
    url = f"{get_agent_url(agent)}/labs/{lab_id}/nodes/{node_name}/stop?provider={provider}"
    logger.info(
        "Agent request",
        extra={
            "event": "agent_request",
            "method": "stop_node",
            "agent_id": agent.id,
            "agent_name": agent.name,
            "lab_id": lab_id,
            "node_name": node_name,
        },
    )

    import time as _time
    _t0 = _time.monotonic()
    try:
        result = await _agent_request(
            "POST",
            url,
            timeout=60.0,
            max_retries=0,
        )
        elapsed_ms = int((_time.monotonic() - _t0) * 1000)
        agent_operation_duration.labels(
            operation="stop_node", host_id=agent.id, status="success",
        ).observe(elapsed_ms / 1000)
        logger.info(
            "Agent response",
            extra={
                "event": "agent_response",
                "method": "stop_node",
                "agent_id": agent.id,
                "lab_id": lab_id,
                "node_name": node_name,
                "status": "success" if result.get("success") else "error",
                "duration_ms": elapsed_ms,
                "agent_duration_ms": result.get("duration_ms"),
                "error": result.get("error") if not result.get("success") else None,
            },
        )
        return result
    except Exception as e:
        elapsed_ms = int((_time.monotonic() - _t0) * 1000)
        agent_operation_duration.labels(
            operation="stop_node", host_id=agent.id, status="error",
        ).observe(elapsed_ms / 1000)
        logger.info(
            "Agent response",
            extra={
                "event": "agent_response",
                "method": "stop_node",
                "agent_id": agent.id,
                "lab_id": lab_id,
                "node_name": node_name,
                "status": "error",
                "duration_ms": elapsed_ms,
                "error": str(e),
            },
        )
        return {"success": False, "error": str(e)}


async def destroy_node_on_agent(
    agent: models.Host,
    lab_id: str,
    node_name: str,
    *,
    provider: str = "docker",
) -> dict:
    """Destroy a node container on an agent.

    Returns:
        Dict with 'success', 'container_removed', and optionally 'error' keys.
    """
    url = f"{get_agent_url(agent)}/labs/{lab_id}/nodes/{node_name}?provider={provider}"
    logger.info(
        "Agent request",
        extra={
            "event": "agent_request",
            "method": "destroy_node",
            "agent_id": agent.id,
            "agent_name": agent.name,
            "lab_id": lab_id,
            "node_name": node_name,
        },
    )

    import time as _time
    _t0 = _time.monotonic()
    try:
        result = await _agent_request(
            "DELETE",
            url,
            timeout=60.0,
            max_retries=0,
        )
        elapsed_ms = int((_time.monotonic() - _t0) * 1000)
        agent_operation_duration.labels(
            operation="destroy_node", host_id=agent.id, status="success",
        ).observe(elapsed_ms / 1000)
        logger.info(
            "Agent response",
            extra={
                "event": "agent_response",
                "method": "destroy_node",
                "agent_id": agent.id,
                "lab_id": lab_id,
                "node_name": node_name,
                "status": "success" if result.get("success") else "error",
                "duration_ms": elapsed_ms,
                "agent_duration_ms": result.get("duration_ms"),
                "error": result.get("error") if not result.get("success") else None,
            },
        )
        return result
    except Exception as e:
        elapsed_ms = int((_time.monotonic() - _t0) * 1000)
        agent_operation_duration.labels(
            operation="destroy_node", host_id=agent.id, status="error",
        ).observe(elapsed_ms / 1000)
        logger.info(
            "Agent response",
            extra={
                "event": "agent_response",
                "method": "destroy_node",
                "agent_id": agent.id,
                "lab_id": lab_id,
                "node_name": node_name,
                "status": "error",
                "duration_ms": elapsed_ms,
                "error": str(e),
            },
        )
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
        result = await _agent_request(
            "POST",
            url,
            timeout=120.0,
            max_retries=0,
            metric_operation="extract_configs",
            metric_host_id=agent.id,
        )
        if result.get("success"):
            logger.info(f"Extracted {result.get('extracted_count', 0)} configs for lab {lab_id}")
        else:
            logger.warning(f"Config extraction failed for lab {lab_id}: {result.get('error')}")
        return result
    except Exception as e:
        logger.error(f"Failed to extract configs for lab {lab_id} on agent {agent.id}: {e}")
        return {"success": False, "extracted_count": 0, "error": str(e)}


async def extract_node_config_on_agent(
    agent: models.Host,
    lab_id: str,
    node_name: str,
) -> dict:
    """Extract running config from one node on an agent."""
    url = f"{get_agent_url(agent)}/labs/{lab_id}/nodes/{node_name}/extract-config"
    logger.info(
        f"Extracting config for node {node_name} in lab {lab_id} via agent {agent.id}"
    )

    try:
        result = await _agent_request(
            "POST",
            url,
            timeout=120.0,
            max_retries=0,
            metric_operation="extract_configs",
            metric_host_id=agent.id,
        )
        if result.get("success"):
            logger.info(f"Extracted config for {node_name} in lab {lab_id}")
        else:
            logger.warning(
                f"Node config extraction failed for {node_name} in lab {lab_id}: "
                f"{result.get('error')}"
            )
        return result
    except Exception as e:
        logger.error(
            f"Failed to extract config for {node_name} in lab {lab_id} on agent {agent.id}: {e}"
        )
        return {"success": False, "node_name": node_name, "error": str(e)}


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
        result = await _agent_request(
            "PUT",
            url,
            json_body={"content": content},
            timeout=30.0,
            max_retries=0,
            metric_operation="update_config",
            metric_host_id=agent.id,
        )
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
    prune_stopped_containers: bool = False,
    prune_unused_networks: bool = False,
) -> dict:
    """Request an agent to prune Docker resources.

    Args:
        agent: The agent to clean up
        valid_lab_ids: List of lab IDs whose resources should be protected
        prune_dangling_images: Whether to prune dangling images
        prune_build_cache: Whether to prune build cache
        prune_unused_volumes: Whether to prune unused volumes (conservative)
        prune_stopped_containers: Whether to prune stopped containers (conservative)
        prune_unused_networks: Whether to prune unused networks (conservative)

    Returns:
        Dict with 'success', 'images_removed', 'build_cache_removed',
        'volumes_removed', 'containers_removed', 'networks_removed',
        'space_reclaimed', and 'errors' keys
    """
    url = f"{get_agent_url(agent)}/prune-docker"

    try:
        return await _agent_request(
            "POST",
            url,
            json_body={
                "valid_lab_ids": valid_lab_ids,
                "prune_dangling_images": prune_dangling_images,
                "prune_build_cache": prune_build_cache,
                "prune_unused_volumes": prune_unused_volumes,
                "prune_stopped_containers": prune_stopped_containers,
                "prune_unused_networks": prune_unused_networks,
            },
            timeout=120.0,  # Docker prune can take a while
            max_retries=0,
        )
    except Exception as e:
        logger.error(f"Failed to prune Docker on agent {agent.id}: {e}")
        return {
            "success": False,
            "images_removed": 0,
            "build_cache_removed": 0,
            "volumes_removed": 0,
            "containers_removed": 0,
            "networks_removed": 0,
            "space_reclaimed": 0,
            "errors": [str(e)],
        }


# --- Workspace Cleanup Functions ---


async def cleanup_agent_workspace(agent: models.Host, lab_id: str) -> dict:
    """Tell an agent to remove workspace for a specific lab."""
    url = f"{get_agent_url(agent)}/labs/{lab_id}/workspace"
    try:
        return await _agent_request("DELETE", url, timeout=30.0, max_retries=0)
    except Exception as e:
        logger.warning(f"Failed to cleanup workspace on agent {agent.id} for lab {lab_id}: {e}")
        return {"success": False, "error": str(e)}


async def cleanup_workspaces_on_agent(agent: models.Host, valid_lab_ids: list[str]) -> dict:
    """Tell an agent to remove orphaned workspace directories."""
    url = f"{get_agent_url(agent)}/cleanup-workspaces"
    try:
        return await _agent_request(
            "POST", url,
            json_body={"valid_lab_ids": valid_lab_ids},
            timeout=60.0,
            max_retries=0,
        )
    except Exception as e:
        logger.warning(f"Failed to cleanup workspaces on agent {agent.id}: {e}")
        return {"success": False, "removed": [], "errors": [str(e)]}


# --- MTU Testing Functions ---


async def test_mtu_on_agent(
    agent: models.Host,
    target_ip: str,
    mtu: int,
    source_ip: str | None = None,
) -> dict:
    """Test MTU to a target IP from an agent.

    Runs ping with DF (Don't Fragment) bit set to verify path MTU.
    Also detects link type (direct/routed) via TTL analysis.

    Args:
        agent: The agent to run the test from
        target_ip: Target IP address to test connectivity to
        mtu: MTU size to test
        source_ip: Optional source IP for bind address (data plane testing)

    Returns:
        Dict with 'success', 'tested_mtu', 'link_type', 'latency_ms', 'error' keys
    """
    url = f"{get_agent_url(agent)}/network/test-mtu"

    try:
        payload: dict = {
            "target_ip": target_ip,
            "mtu": mtu,
        }
        if source_ip:
            payload["source_ip"] = source_ip
        return await _agent_request(
            "POST",
            url,
            json_body=payload,
            timeout=30.0,
            max_retries=0,
        )
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
        return await _agent_request(
            "GET",
            url,
            timeout=30.0,
            max_retries=0,
            metric_operation="get_interface_details",
            metric_host_id=agent.id,
        )
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
        return await _agent_request(
            "POST",
            url,
            json_body={"mtu": mtu, "persist": persist},
            timeout=60.0,  # Longer timeout for persistence operations
            max_retries=0,
        )
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


async def provision_interface_on_agent(
    agent: models.Host,
    action: str,
    name: str | None = None,
    parent_interface: str | None = None,
    vlan_id: int | None = None,
    ip_cidr: str | None = None,
    mtu: int | None = None,
    attach_to_ovs: bool = False,
    ovs_vlan_tag: int | None = None,
) -> dict:
    """Provision, configure, or delete an interface on an agent host.

    Args:
        agent: The agent to configure
        action: "create_subinterface", "configure", or "delete"
        name: Interface name (auto-generated for subinterfaces)
        parent_interface: Parent for subinterface creation
        vlan_id: VLAN ID for subinterface
        ip_cidr: IP/CIDR to assign
        mtu: Desired MTU
        attach_to_ovs: Whether to also add to OVS bridge
        ovs_vlan_tag: VLAN tag for OVS attachment

    Returns:
        Dict with 'success', 'interface_name', 'mtu', 'ip_address', 'error' keys
    """
    url = f"{get_agent_url(agent)}/interfaces/provision"
    payload = {
        "action": action,
        "name": name,
        "parent_interface": parent_interface,
        "vlan_id": vlan_id,
        "ip_cidr": ip_cidr,
        "mtu": mtu,
        "attach_to_ovs": attach_to_ovs,
        "ovs_vlan_tag": ovs_vlan_tag,
    }
    # Remove None values
    payload = {k: v for k, v in payload.items() if v is not None}

    try:
        return await _agent_request(
            "POST",
            url,
            json_body=payload,
            timeout=60.0,
            max_retries=0,
        )
    except Exception as e:
        logger.error(f"Failed to provision interface on agent {agent.id}: {e}")
        return {"success": False, "error": str(e)}


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
        result = await _agent_request(
            "POST",
            url,
            json_body={
                "source_node": source_node,
                "source_interface": source_interface,
                "target_node": target_node,
                "target_interface": target_interface,
            },
            timeout=30.0,
            max_retries=0,
        )
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
        result = await _agent_request(
            "DELETE",
            url,
            timeout=30.0,
            max_retries=0,
        )
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
        return await _agent_request(
            "GET",
            url,
            timeout=30.0,
            max_retries=0,
        )
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
        return await _agent_request(
            "GET",
            url,
            timeout=10.0,
            max_retries=0,
        )
    except Exception as e:
        logger.error(f"OVS status failed on agent {agent.id}: {e}")
        return {"bridge_name": "", "initialized": False, "ports": [], "links": []}


async def get_agent_boot_logs(agent: models.Host, lab_id: str | None = None) -> dict:
    """Get boot logs from an agent for a specific lab.

    Returns:
        Dict with 'lab_id' and 'boot_logs' mapping node names to log text
    """
    if not lab_id:
        return {"boot_logs": {}, "error": "lab_id required"}

    url = f"{get_agent_url(agent)}/labs/{lab_id}/boot-logs"

    try:
        return await _agent_request(
            "GET",
            url,
            timeout=15.0,
            max_retries=0,
        )
    except Exception as e:
        logger.error(f"Failed to get boot logs from agent {agent.id}: {e}")
        return {"boot_logs": {}, "error": str(e)}


async def get_agent_ovs_flows(agent: models.Host) -> dict:
    """Get OVS flow table from an agent.

    Returns:
        Dict with 'bridge', 'flows', and optionally 'error'
    """
    url = f"{get_agent_url(agent)}/ovs/flows"

    try:
        return await _agent_request(
            "GET",
            url,
            timeout=10.0,
            max_retries=0,
        )
    except Exception as e:
        logger.error(f"Failed to get OVS flows from agent {agent.id}: {e}")
        return {"bridge": "", "flows": "", "error": str(e)}


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
        result = await _agent_request(
            "POST",
            url,
            json_body={
                "node_name": node_name,
                "interface_name": interface_name,
                "external_interface": external_interface,
                "vlan_tag": vlan_tag,
            },
            timeout=30.0,
            max_retries=0,
        )
        if result.get("success"):
            logger.info(f"External connect succeeded (VLAN {result.get('vlan_tag')})")
        else:
            logger.warning(f"External connect failed: {result.get('error')}")
        return result
    except Exception as e:
        logger.error(f"External connect failed on agent {agent.id}: {e}")
        return {"success": False, "error": str(e)}


async def detach_external_on_agent(
    agent: models.Host,
    external_interface: str,
) -> dict:
    """Detach an external interface from the OVS bridge.

    Called during teardown when no more labs reference this external interface.

    Args:
        agent: The agent where the external interface is connected
        external_interface: External host interface name to detach

    Returns:
        Dict with 'success' and optionally 'error' keys
    """
    url = f"{get_agent_url(agent)}/ovs-plugin/labs/_global/external/{external_interface}"
    logger.info(f"Detaching external interface {external_interface} on agent {agent.id}")

    try:
        result = await _agent_request(
            "DELETE",
            url,
            timeout=30.0,
            max_retries=0,
        )
        if result.get("success"):
            logger.info(f"External detach succeeded for {external_interface}")
        else:
            logger.warning(f"External detach failed: {result.get('error')}")
        return result
    except Exception as e:
        logger.error(f"External detach failed on agent {agent.id}: {e}")
        return {"success": False, "error": str(e)}


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

    try:
        data = await _agent_request(
            "GET",
            url,
            timeout=10.0,
            max_retries=0,
        )
        return data.get("ports", [])
    except Exception as e:
        logger.warning(f"Get lab ports failed: {e}")
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
    try:
        data = await _agent_request(
            "GET",
            url,
            timeout=10.0,
            max_retries=0,
        )
        return data.get("vlan_tag")
    except Exception as e:
        logger.warning(f"Get interface VLAN failed: {e}")
        return None


async def set_port_vlan_on_agent(
    agent: models.Host,
    port_name: str,
    vlan_tag: int,
) -> bool:
    """Set the VLAN tag on an OVS port via the agent.

    Args:
        agent: The agent managing the port
        port_name: OVS port name (e.g., VXLAN port or container veth)
        vlan_tag: VLAN tag to set

    Returns:
        True if successful, False otherwise
    """
    url = f"{get_agent_url(agent)}/overlay/ports/{port_name}/vlan"
    try:
        result = await _agent_request(
            "PUT",
            url,
            json_body={"vlan_tag": vlan_tag},
            timeout=10.0,
            max_retries=0,
        )
        return result.get("success", False)
    except Exception as e:
        logger.warning(f"Set port VLAN failed on {agent.name}: {e}")
        return False


async def repair_endpoints_on_agent(
    agent: models.Host,
    lab_id: str,
    nodes: list[str] | None = None,
) -> dict:
    """Repair missing veth pairs and OVS ports on an agent.

    After agent/container restarts, endpoints may have stale in-memory
    state where the physical veth pairs no longer exist. This triggers
    recreation of the veth pairs, OVS attachment, and namespace moves.

    Args:
        agent: The agent to repair endpoints on
        lab_id: Lab identifier
        nodes: Optional list of node names to repair (all if None)

    Returns:
        Dict with 'success', 'nodes_repaired', 'total_endpoints_repaired',
        'results', and optionally 'error' keys.
    """
    url = f"{get_agent_url(agent)}/labs/{lab_id}/repair-endpoints"
    logger.info(
        f"Repairing endpoints on agent {agent.id} for lab {lab_id}"
        + (f" nodes={nodes}" if nodes else " (all nodes)")
    )

    try:
        result = await _agent_request(
            "POST",
            url,
            json_body={"nodes": nodes or []},
            timeout=60.0,
            max_retries=0,
        )
        repaired = result.get("total_endpoints_repaired", 0)
        if repaired > 0:
            logger.info(
                f"Repaired {repaired} endpoint(s) on agent {agent.id} for lab {lab_id}"
            )
        return result
    except Exception as e:
        logger.error(f"Endpoint repair failed on agent {agent.id}: {e}")
        return {"success": False, "error": str(e)}
