"""Client for communicating with Aura agents."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import TypeVar, Callable, Any

import httpx
from sqlalchemy.orm import Session

from app import models
from app.db import SessionLocal


logger = logging.getLogger(__name__)

# Cache for healthy agents
_agent_cache: dict[str, tuple[str, datetime]] = {}  # agent_id -> (address, last_check)
CACHE_TTL = timedelta(seconds=30)

# Retry configuration
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 1.0  # Base delay in seconds
RETRY_BACKOFF_MAX = 10.0  # Maximum delay in seconds

# Timeout configuration
DEPLOY_TIMEOUT = 300.0  # 5 minutes
DESTROY_TIMEOUT = 120.0  # 2 minutes
NODE_ACTION_TIMEOUT = 60.0
STATUS_TIMEOUT = 30.0
HEALTH_CHECK_TIMEOUT = 5.0


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
    max_retries: int = MAX_RETRIES,
    **kwargs,
) -> Any:
    """Execute an async function with exponential backoff retry logic.

    Only retries on connection errors and timeouts, not on application errors.
    """
    last_exception = None

    for attempt in range(max_retries + 1):
        try:
            return await func(*args, **kwargs)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as e:
            last_exception = e
            if attempt < max_retries:
                delay = min(RETRY_BACKOFF_BASE * (2 ** attempt), RETRY_BACKOFF_MAX)
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


async def get_healthy_agent(database: Session) -> models.Host | None:
    """Get a healthy agent to handle jobs.

    For now, returns the first online agent. In future, this will
    implement load balancing and affinity.
    """
    # Find agents that have sent heartbeat recently (within 60 seconds)
    cutoff = datetime.utcnow() - timedelta(seconds=60)

    agent = (
        database.query(models.Host)
        .filter(
            models.Host.status == "online",
            models.Host.last_heartbeat >= cutoff,
        )
        .first()
    )

    return agent


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
            response = await client.get(url, timeout=HEALTH_CHECK_TIMEOUT)
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
) -> dict:
    """Internal deploy request (for retry wrapper)."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            url,
            json={
                "job_id": job_id,
                "lab_id": lab_id,
                "topology_yaml": topology_yaml,
                "provider": "containerlab",
            },
            timeout=DEPLOY_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()


async def deploy_to_agent(
    agent: models.Host,
    job_id: str,
    lab_id: str,
    topology_yaml: str,
) -> dict:
    """Send deploy request to agent with retry logic."""
    url = f"{get_agent_url(agent)}/jobs/deploy"
    logger.info(f"Deploying lab {lab_id} via agent {agent.id}")

    try:
        result = await with_retry(_do_deploy, url, job_id, lab_id, topology_yaml)
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
            timeout=DESTROY_TIMEOUT,
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
            timeout=NODE_ACTION_TIMEOUT,
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
            timeout=STATUS_TIMEOUT,
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


async def update_stale_agents(database: Session, timeout_seconds: int = 90) -> list[str]:
    """Mark agents as offline if their heartbeat is stale.

    Returns list of agent IDs that were marked offline.
    """
    cutoff = datetime.utcnow() - timedelta(seconds=timeout_seconds)

    stale_agents = (
        database.query(models.Host)
        .filter(
            models.Host.status == "online",
            models.Host.last_heartbeat < cutoff,
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
