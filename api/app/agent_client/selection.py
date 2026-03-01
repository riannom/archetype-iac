"""Agent discovery, health checking, and selection logic."""

from __future__ import annotations

import asyncio
import logging
import socket
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models
from app.config import settings
from app.events.publisher import emit_agent_offline
from app.agent_client.http import (
    _agent_online_cutoff,
    _agent_request,
    _safe_agent_request,
    get_http_client,
    _get_agent_auth_headers,
    AgentUnavailableError,
)


logger = logging.getLogger(__name__)


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


def get_agent_providers(agent: models.Host) -> list[str]:
    """Get list of providers supported by an agent."""
    caps = agent.get_capabilities()
    return caps.get("providers", [])


def get_agent_max_jobs(agent: models.Host) -> int:
    """Get max concurrent jobs for an agent."""
    caps = agent.get_capabilities()
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


def get_agent_console_url(agent: models.Host, lab_id: str, node_name: str) -> str:
    """Get WebSocket URL for console on agent."""
    base = get_agent_url(agent)
    # Convert http to ws
    ws_base = base.replace("http://", "ws://").replace("https://", "wss://")
    return f"{ws_base}/console/{lab_id}/{node_name}"


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
    """Query real-time capacity from an agent's /capacity endpoint."""
    return await _safe_agent_request(
        agent, "GET", "/capacity",
        timeout=timeout, description="Capacity query",
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


def agent_supports_vxlan(agent: models.Host) -> bool:
    """Check if an agent supports VXLAN overlay."""
    caps = agent.get_capabilities()
    features = caps.get("features", [])
    return "vxlan" in features
