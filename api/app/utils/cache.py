"""Simple Redis-based cache for expensive API responses.

Uses sync Redis (same as the rest of the request path) with JSON serialization.
Cache misses return None, allowing the caller to compute and store the value.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.db import get_redis

logger = logging.getLogger(__name__)

DEFAULT_TTL = 30  # seconds


def cache_get(key: str) -> Any | None:
    """Get a cached value. Returns None on miss or error."""
    try:
        r = get_redis()
        data = r.get(f"cache:{key}")
        if data is not None:
            return json.loads(data)
    except Exception:
        pass  # Cache miss on error - caller computes fresh
    return None


def cache_set(key: str, value: Any, ttl: int = DEFAULT_TTL) -> None:
    """Store a value in cache with TTL. Silently ignores errors."""
    try:
        r = get_redis()
        r.setex(f"cache:{key}", ttl, json.dumps(value, default=str))
    except Exception:
        pass  # Best-effort caching
