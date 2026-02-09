from __future__ import annotations

from contextlib import contextmanager

import redis
import redis.asyncio as aioredis
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings

_engine_kwargs: dict = dict(
    pool_pre_ping=True,
    future=True,
)

if settings.database_url.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    _engine_kwargs.update(
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_recycle=300,       # Recycle connections after 5 minutes
        pool_timeout=settings.db_pool_timeout,
        connect_args={"options": "-c statement_timeout=30000"},  # 30s max per SQL statement
    )

engine = create_engine(settings.database_url, **_engine_kwargs)
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


# Shared async Redis client for non-blocking operations
_async_redis: aioredis.Redis | None = None


def get_async_redis() -> aioredis.Redis:
    """Get the shared async Redis client, creating it if necessary.

    Returns a lazily-initialized async Redis client for use in async contexts:
    - State broadcasting (already uses redis.asyncio)
    - Cooldown checks in enforcement
    - Lock operations from async code paths

    The sync get_redis() is kept for RQ worker and other sync contexts.
    """
    global _async_redis
    if _async_redis is None:
        _async_redis = aioredis.from_url(settings.redis_url)
    return _async_redis


def get_db():
    """FastAPI dependency for database sessions.

    Ensures proper cleanup with rollback before close to prevent
    'idle in transaction' connections.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        try:
            db.rollback()  # Release any uncommitted transaction
        except Exception:
            pass
        db.close()


@contextmanager
def get_session():
    """Get a database session with proper cleanup for background tasks.

    This context manager ensures the session is always properly closed,
    with an explicit rollback before close to prevent "idle in transaction"
    connections from accumulating.

    Usage:
        with get_session() as session:
            # do work
            session.commit()  # if needed

    Unlike SessionLocal() which requires manual cleanup, this ensures:
    - Rollback is always called (even if commit succeeded - it's a no-op)
    - Close is always called
    - No "idle in transaction" connections leak
    """
    session = SessionLocal()
    try:
        yield session
    finally:
        try:
            session.rollback()  # Always rollback before close to release transaction
        except Exception:
            pass  # Ignore rollback errors
        session.close()
