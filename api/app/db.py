from __future__ import annotations

import redis
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

# Shared Redis client for distributed locking and caching
_redis: redis.Redis | None = None


def get_redis() -> redis.Redis:
    """Get the shared Redis client, creating it if necessary.

    Returns a lazily-initialized Redis client that can be used for:
    - Distributed locking (reconciliation, job limits)
    - Cooldown tracking (state enforcement)
    - Any other Redis-based caching needs
    """
    global _redis
    if _redis is None:
        _redis = redis.from_url(settings.redis_url)
    return _redis


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
