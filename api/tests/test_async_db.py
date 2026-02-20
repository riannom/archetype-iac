"""Tests for async SQLAlchemy infrastructure in app.db."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import _make_async_url, get_async_session


# --- URL conversion ---


def test_make_async_url_psycopg():
    """psycopg sync dialect converts to psycopg_async."""
    url = "postgresql+psycopg://user:pass@host:5432/db"
    assert _make_async_url(url) == "postgresql+psycopg_async://user:pass@host:5432/db"


def test_make_async_url_bare():
    """Bare postgresql:// converts to postgresql+psycopg_async://."""
    url = "postgresql://user:pass@host:5432/db"
    assert _make_async_url(url) == "postgresql+psycopg_async://user:pass@host:5432/db"


def test_make_async_url_sqlite():
    """SQLite URLs pass through unchanged."""
    url = "sqlite:///test.db"
    assert _make_async_url(url) == url


def test_make_async_url_sqlite_memory():
    """SQLite in-memory URLs pass through unchanged."""
    url = "sqlite:///:memory:"
    assert _make_async_url(url) == url


def test_make_async_url_already_async():
    """Already-async URLs pass through unchanged."""
    url = "postgresql+psycopg_async://user:pass@host:5432/db"
    assert _make_async_url(url) == url


# --- Async session lifecycle ---


@pytest.mark.asyncio
async def test_async_session_yields_async_session(async_test_db: AsyncSession):
    """get_async_session() yields an AsyncSession instance."""
    assert isinstance(async_test_db, AsyncSession)


@pytest.mark.asyncio
async def test_async_session_can_execute_query(async_test_db: AsyncSession):
    """AsyncSession can execute a simple query."""
    result = await async_test_db.execute(text("SELECT 1"))
    assert result.scalar_one() == 1


@pytest.mark.asyncio
async def test_get_async_session_context_manager():
    """get_async_session() context manager yields and cleans up."""
    # Patch AsyncSessionLocal to return a mock session
    mock_session = AsyncMock(spec=AsyncSession)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.rollback = AsyncMock()

    with patch("app.db.AsyncSessionLocal", return_value=mock_session):
        async with get_async_session() as session:
            assert session is mock_session

    # Rollback should have been called in finally block
    mock_session.rollback.assert_awaited_once()
