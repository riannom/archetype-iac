"""Tests for login rate limiting in api/app/routers/auth.py."""
from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException


def _make_request(ip: str = "127.0.0.1"):
    """Create a mock Request with a client IP."""
    req = MagicMock()
    req.client = MagicMock()
    req.client.host = ip
    return req


def _make_redis_module(fake_client=None, *, raise_on_connect=False):
    """Create a mock redis module for sys.modules injection.

    The function under test does ``import redis`` at call-time, so we
    must replace the *module* in sys.modules before each call.
    """
    mod = ModuleType("redis")
    if raise_on_connect:
        mod.from_url = MagicMock(side_effect=ConnectionError("Redis down"))  # type: ignore[attr-defined]
    else:
        mod.from_url = MagicMock(return_value=fake_client)  # type: ignore[attr-defined]
    return mod


class _FakeRedis:
    """Minimal Redis stub that tracks incr/expire calls."""

    def __init__(self, counters: dict[str, int] | None = None):
        self._state: dict[str, int] = dict(counters or {})

    def incr(self, key: str) -> int:
        self._state[key] = self._state.get(key, 0) + 1
        return self._state[key]

    def expire(self, key: str, ttl: int) -> None:  # noqa: ARG002
        pass


class TestLoginRateLimit:
    """Tests for _check_login_rate_limit()."""

    def _call(self, request, username, fake_redis_module):
        """Call the rate-limit function with a patched redis module."""
        original = sys.modules.get("redis")
        sys.modules["redis"] = fake_redis_module
        try:
            from app.routers.auth import _check_login_rate_limit

            _check_login_rate_limit(request, username)
        finally:
            if original is not None:
                sys.modules["redis"] = original
            else:
                sys.modules.pop("redis", None)

    def test_under_limit(self):
        client = _FakeRedis()
        mod = _make_redis_module(client)
        request = _make_request()
        # Should not raise
        self._call(request, "user1", mod)

    def test_exceed_per_ip_limit(self):
        client = _FakeRedis({"login_rate:ip:10.0.0.1": 20})
        mod = _make_redis_module(client)
        request = _make_request(ip="10.0.0.1")
        with pytest.raises(HTTPException) as exc_info:
            self._call(request, "user1", mod)
        assert exc_info.value.status_code == 429
        assert "Too many login attempts" in exc_info.value.detail

    def test_exceed_per_user_limit(self):
        client = _FakeRedis({"login_rate:user:admin": 10})
        mod = _make_redis_module(client)
        request = _make_request()
        with pytest.raises(HTTPException) as exc_info:
            self._call(request, "admin", mod)
        assert exc_info.value.status_code == 429
        assert "Too many login attempts" in exc_info.value.detail

    def test_redis_unavailable_fails_open(self):
        mod = _make_redis_module(raise_on_connect=True)
        request = _make_request()
        # Should not raise — fails open
        self._call(request, "user1", mod)
