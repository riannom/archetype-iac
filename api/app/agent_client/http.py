"""Core HTTP primitives, retry logic, and exception classes for agent communication."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Any

import httpx

from app.config import settings
from app.metrics import agent_operation_duration
from app.utils.timeouts import AGENT_HTTP_TIMEOUT, AGENT_VTEP_TIMEOUT


logger = logging.getLogger(__name__)


# Retry configuration (exported for backward compatibility)
MAX_RETRIES = settings.agent_max_retries

# VTEP operations can be slow due to OVS bridge operations
VTEP_OPERATION_TIMEOUT = AGENT_VTEP_TIMEOUT

# Cache for healthy agents
_agent_cache: dict[str, tuple[str, datetime]] = {}  # agent_id -> (address, last_check)

# Shared HTTP client with connection pooling
_http_client: httpx.AsyncClient | None = None


def _get_agent_auth_headers() -> dict[str, str]:
    """Return auth headers for agent requests if secret is configured."""
    if settings.agent_secret:
        return {"Authorization": f"Bearer {settings.agent_secret}"}
    return {}


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

    if timeout_seconds is None:
        timeout_seconds = settings.agent_stale_timeout
    return datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)


async def _safe_agent_request(
    agent,
    method: str,
    path: str,
    *,
    fallback: dict | None = None,
    description: str = "",
    json_body: dict | None = None,
    timeout: float | None = None,
    max_retries: int = 0,
    metric_operation: str | None = None,
    log_level: str = "warning",
) -> dict:
    """Make an agent request with automatic error handling and fallback.

    Builds the full URL from agent, calls _agent_request(), catches
    exceptions, logs at the specified level, and returns the fallback dict.
    """
    from app.agent_client.selection import get_agent_url

    url = f"{get_agent_url(agent)}/{path.lstrip('/')}"
    try:
        return await _agent_request(
            method,
            url,
            json_body=json_body,
            timeout=timeout,
            max_retries=max_retries,
            metric_operation=metric_operation,
            metric_host_id=agent.id if metric_operation else None,
        )
    except Exception as e:
        getattr(logger, log_level)(
            f"{description or path} failed on agent {agent.id}: {e}"
        )
        return dict(fallback) if fallback else {}


async def _timed_node_operation(
    agent,
    method: str,
    url: str,
    operation: str,
    lab_id: str,
    node_name: str,
    *,
    json_body: dict | None = None,
    timeout: float = 120.0,
) -> dict:
    """Execute a node operation with timing, metrics, and structured logging."""
    logger.info(
        "Agent request",
        extra={
            "event": "agent_request",
            "method": operation,
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
            method,
            url,
            json_body=json_body,
            timeout=timeout,
            max_retries=0,
        )
        elapsed_ms = int((_time.monotonic() - _t0) * 1000)
        agent_operation_duration.labels(
            operation=operation, host_id=agent.id, status="success",
        ).observe(elapsed_ms / 1000)
        logger.info(
            "Agent response",
            extra={
                "event": "agent_response",
                "method": operation,
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
            operation=operation, host_id=agent.id, status="error",
        ).observe(elapsed_ms / 1000)
        logger.info(
            "Agent response",
            extra={
                "event": "agent_response",
                "method": operation,
                "agent_id": agent.id,
                "lab_id": lab_id,
                "node_name": node_name,
                "status": "error",
                "duration_ms": elapsed_ms,
                "error": str(e),
            },
        )
        return {"success": False, "error": str(e)}
