"""Shared HTTP client for agent-to-controller communication.

Provides a singleton httpx.AsyncClient with connection pooling,
replacing transient clients that were created per-request.
"""
from __future__ import annotations

import httpx

from agent.config import settings

_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    """Get the shared HTTP client with connection pooling.

    Lazily creates the client on first call, then reuses it.
    Re-creates if the previous client was closed.
    """
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=5.0),
        )
    return _client


async def close_http_client() -> None:
    """Close the shared HTTP client. Call during shutdown."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
        _client = None


def get_controller_auth_headers() -> dict[str, str]:
    """Return auth headers for controller requests if secret is configured."""
    if settings.controller_secret:
        return {"Authorization": f"Bearer {settings.controller_secret}"}
    return {}
