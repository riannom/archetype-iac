from __future__ import annotations

from contextlib import contextmanager

import redis
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    future=True,
    pool_size=10,           # Number of persistent connections
    max_overflow=20,        # Additional connections when pool is exhausted
    pool_recycle=300,       # Recycle connections after 5 minutes
    pool_timeout=30,        # Wait max 30s for connection
)
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
