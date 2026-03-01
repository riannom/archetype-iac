from __future__ import annotations

import asyncio

from agent import http_client


def _reset_client() -> None:
    client = http_client._client
    if client is not None and not client.is_closed:
        asyncio.run(client.aclose())
    http_client._client = None


def test_get_http_client_returns_singleton() -> None:
    _reset_client()
    client_one = http_client.get_http_client()
    client_two = http_client.get_http_client()

    assert client_one is client_two

    _reset_client()


def test_get_http_client_recreates_closed_client() -> None:
    _reset_client()
    client_one = http_client.get_http_client()
    asyncio.run(client_one.aclose())

    client_two = http_client.get_http_client()
    assert client_two is not client_one
    assert client_two.is_closed is False

    _reset_client()


def test_close_http_client_resets_singleton() -> None:
    _reset_client()
    _ = http_client.get_http_client()

    asyncio.run(http_client.close_http_client())

    assert http_client._client is None


def test_get_controller_auth_headers(monkeypatch) -> None:
    monkeypatch.setattr(http_client.settings, "controller_secret", "shared-secret")
    assert http_client.get_controller_auth_headers() == {
        "Authorization": "Bearer shared-secret"
    }

    monkeypatch.setattr(http_client.settings, "controller_secret", "")
    assert http_client.get_controller_auth_headers() == {}
