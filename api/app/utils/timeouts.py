"""Centralized timeout policy for external service calls.

All timeouts for external dependencies should be defined here to ensure
consistent behavior and easy tuning. Wrapping coroutines with `with_timeout()`
provides uniform logging on timeout failures.
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

# Agent RPC calls (HTTP to compute agents)
AGENT_HTTP_TIMEOUT = 30.0

# VTEP/overlay operations can be slow due to OVS bridge ops
AGENT_VTEP_TIMEOUT = 60.0

# Redis get/set/publish operations
REDIS_OPERATION_TIMEOUT = 5.0

# Internal async operations (non-Redis, non-agent)
INTERNAL_OPERATION_TIMEOUT = 10.0

# DNS resolution timeout
DNS_RESOLVE_TIMEOUT = 5.0

# Database query timeout (also enforced by statement_timeout=30s at DB level)
DB_QUERY_TIMEOUT = 30.0


async def with_timeout(coro, timeout: float, description: str = "operation"):
    """Wrap any coroutine with a timeout and descriptive warning on failure.

    Args:
        coro: Awaitable coroutine
        timeout: Timeout in seconds
        description: Human-readable description for log messages

    Returns:
        Result of the coroutine

    Raises:
        asyncio.TimeoutError: If the operation exceeds the timeout
    """
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning(f"Timeout after {timeout}s: {description}")
        raise
