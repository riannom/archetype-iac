"""Extended tests for agent/http_client.py — additional edge cases.

Complements test_http_client.py with:
- close_http_client when _client is None
- Timeout configuration validation
"""
from __future__ import annotations

import asyncio


from agent import http_client


def _reset_client() -> None:
    client = http_client._client
    if client is not None and not client.is_closed:
        asyncio.run(client.aclose())
    http_client._client = None


# ---------------------------------------------------------------------------
# close_http_client when client is None
# ---------------------------------------------------------------------------


def test_close_http_client_when_none() -> None:
    """Closing when no client exists is a no-op."""
    _reset_client()
    assert http_client._client is None
    asyncio.run(http_client.close_http_client())
    assert http_client._client is None


def test_close_http_client_already_closed() -> None:
    """Closing an already-closed client is a no-op."""
    _reset_client()
    client = http_client.get_http_client()
    asyncio.run(client.aclose())
    # Client is closed but _client still references it
    assert http_client._client is not None
    assert http_client._client.is_closed

    # close_http_client should handle this gracefully
    asyncio.run(http_client.close_http_client())
    # _client is NOT set to None because the branch checks `not client.is_closed`
    # The function skips aclose for already-closed clients
    _reset_client()


# ---------------------------------------------------------------------------
# Timeout configuration
# ---------------------------------------------------------------------------


def test_http_client_timeout_configuration() -> None:
    """Client is created with expected timeout values."""
    _reset_client()
    client = http_client.get_http_client()

    assert client.timeout.read == 30.0
    assert client.timeout.connect == 5.0

    _reset_client()


# ---------------------------------------------------------------------------
# get_controller_auth_headers edge cases
# ---------------------------------------------------------------------------


def test_controller_auth_headers_whitespace_secret(monkeypatch) -> None:
    """Non-empty whitespace secret still produces headers (not stripped)."""
    monkeypatch.setattr(http_client.settings, "controller_secret", "  ")
    headers = http_client.get_controller_auth_headers()
    assert headers == {"Authorization": "Bearer   "}
