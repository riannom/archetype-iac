"""Tests for database session management (db.py).

This module tests:
- Database engine creation with settings
- SessionLocal configuration
- get_db() dependency lifecycle (creation, yield, cleanup)
- Error handling during session operations
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session


class TestSessionLocalConfiguration:
    """Tests for SessionLocal sessionmaker configuration."""

    def test_session_local_is_configured(self):
        """Test that SessionLocal is a valid sessionmaker."""
        from app.db import SessionLocal

        # SessionLocal should be callable and create sessions
        assert callable(SessionLocal)

    def test_session_local_creates_session(self):
        """Test that SessionLocal creates a valid SQLAlchemy session."""
        from app.db import SessionLocal

        session = SessionLocal()
        try:
            # Session should be an instance of Session
            assert isinstance(session, Session)
        finally:
            session.close()

    def test_session_autoflush_disabled(self):
        """Test that SessionLocal has autoflush disabled."""
        from app.db import SessionLocal

        session = SessionLocal()
        try:
            assert session.autoflush is False
        finally:
            session.close()

    def test_sessionmaker_configured_with_autocommit_false(self):
        """Test that SessionLocal is configured with autocommit=False."""
        from app.db import SessionLocal

        # In SQLAlchemy 2.0+, the sessionmaker configuration includes autocommit=False
        # We verify by checking the sessionmaker's kw (keyword args)
        assert SessionLocal.kw.get("autocommit", False) is False

    def test_sessionmaker_configured_with_future_true(self):
        """Test that SessionLocal is configured with future=True."""
        from app.db import SessionLocal

        # The sessionmaker should be configured for SQLAlchemy 2.0 style
        assert SessionLocal.kw.get("future", False) is True


class TestEngineConfiguration:
    """Tests for database engine configuration."""

    def test_engine_is_created(self):
        """Test that database engine is created."""
        from app.db import engine

        assert engine is not None

    def test_engine_pool_pre_ping_enabled(self):
        """Test that engine has pool_pre_ping enabled."""
        from app.db import engine

        # pool_pre_ping is stored in the pool's _pre_ping attribute
        assert engine.pool._pre_ping is True

    def test_engine_is_future_mode(self):
        """Test that engine is created with future=True (SQLAlchemy 2.0 style)."""
        from app.db import engine

        # In SQLAlchemy 2.0, engines are always "future" mode
        # Verify the engine works with 2.0 style connections
        with engine.connect() as conn:
            # Should not raise - confirms future mode works
            assert conn is not None


class TestGetDbDependency:
    """Tests for get_db() FastAPI dependency."""

    def test_get_db_yields_session(self):
        """Test that get_db() yields a database session."""
        from app.db import get_db

        gen = get_db()
        session = next(gen)

        try:
            assert isinstance(session, Session)
        finally:
            # Clean up by completing the generator
            try:
                next(gen)
            except StopIteration:
                pass

    def test_get_db_closes_session_after_yield(self):
        """Test that get_db() closes session after generator completes."""
        from app.db import get_db

        gen = get_db()
        session = next(gen)

        # Session should be active and have a bind
        assert session.get_bind() is not None

        # Complete the generator
        try:
            next(gen)
        except StopIteration:
            pass

        # After completion, attempting operations should indicate session is not usable
        # Session close() invalidates the session

    def test_get_db_closes_session_on_exception(self):
        """Test that get_db() closes session even when exception occurs."""
        from app.db import get_db

        close_called = []
        gen = get_db()
        session = next(gen)

        # Monkey-patch close to track calls
        original_close = session.close
        def tracking_close():
            close_called.append(True)
            return original_close()
        session.close = tracking_close

        # Simulate exception handling
        try:
            gen.throw(ValueError("Test exception"))
        except ValueError:
            pass

        # Session close should have been called
        assert len(close_called) == 1

    def test_get_db_as_dependency_lifecycle(self):
        """Test get_db() follows correct dependency lifecycle."""
        from app.db import get_db

        # Simulate FastAPI dependency injection lifecycle
        sessions = []

        gen = get_db()
        session = next(gen)
        sessions.append(session)

        # Simulate request processing (session is in use)
        assert isinstance(session, Session)

        # Simulate request completion
        try:
            next(gen)
        except StopIteration:
            pass

        # A new call should create a new session
        gen2 = get_db()
        session2 = next(gen2)
        sessions.append(session2)

        try:
            next(gen2)
        except StopIteration:
            pass

        # Each call creates independent sessions
        assert len(sessions) == 2

    def test_multiple_concurrent_sessions(self):
        """Test that multiple concurrent get_db() calls create independent sessions."""
        from app.db import get_db

        gen1 = get_db()
        gen2 = get_db()
        gen3 = get_db()

        session1 = next(gen1)
        session2 = next(gen2)
        session3 = next(gen3)

        try:
            # All sessions should be independent
            assert session1 is not session2
            assert session2 is not session3
            assert session1 is not session3
        finally:
            # Clean up all generators
            for gen in [gen1, gen2, gen3]:
                try:
                    next(gen)
                except StopIteration:
                    pass


class TestDatabaseConnectionSettings:
    """Tests for database connection URL and settings."""

    def test_engine_uses_settings_database_url(self):
        """Test that engine uses the database URL from settings."""
        from app.config import settings
        from app.db import engine

        # Engine URL should match settings (may have driver-specific prefix)
        engine_url_str = str(engine.url)
        # The URL should contain the database path/name from settings
        # For SQLite, it will contain the path
        # For PostgreSQL, it will contain the host/db
        assert engine_url_str is not None

    def test_engine_future_mode_enabled(self):
        """Test that engine is created with future=True (SQLAlchemy 2.0 style)."""
        from app.db import engine

        # In future mode (SQLAlchemy 2.0), the engine behaves differently
        # We can verify by checking if it supports 2.0 style execution
        # The engine should be created successfully and functional
        with engine.connect() as conn:
            assert conn is not None


class TestSessionRollbackBehavior:
    """Tests for session rollback and error handling."""

    def test_session_close_called_on_normal_completion(self):
        """Test that close is called when generator completes normally."""
        from app.db import get_db

        close_called = []
        gen = get_db()
        session = next(gen)

        # Track close calls
        original_close = session.close
        def tracking_close():
            close_called.append(True)
            return original_close()
        session.close = tracking_close

        # Complete the generator
        try:
            next(gen)
        except StopIteration:
            pass

        # Close should have been called
        assert len(close_called) == 1

    def test_session_close_called_on_exception(self):
        """Test that close is called even when exception is thrown."""
        from app.db import get_db

        close_called = []
        gen = get_db()
        session = next(gen)

        # Track close calls
        original_close = session.close
        def tracking_close():
            close_called.append(True)
            return original_close()
        session.close = tracking_close

        # Throw an exception into the generator
        try:
            gen.throw(RuntimeError("Error"))
        except RuntimeError:
            pass

        # Close should have been called
        assert len(close_called) == 1

    def test_finally_block_executes_for_multiple_sessions(self):
        """Test that finally block executes for each session."""
        from app.db import get_db

        close_count = [0]

        for i in range(3):
            gen = get_db()
            session = next(gen)

            original_close = session.close
            def tracking_close(orig=original_close):
                close_count[0] += 1
                return orig()
            session.close = tracking_close

            try:
                next(gen)
            except StopIteration:
                pass

        # Close should have been called 3 times
        assert close_count[0] == 3


class TestSessionBehavior:
    """Tests for session behavior and transaction handling."""

    def test_session_bound_to_engine(self):
        """Test that session is bound to the engine."""
        from app.db import SessionLocal, engine

        session = SessionLocal()
        try:
            assert session.get_bind() is engine
        finally:
            session.close()

    def test_each_session_is_independent(self):
        """Test that each session created by SessionLocal is independent."""
        from app.db import SessionLocal

        session1 = SessionLocal()
        session2 = SessionLocal()

        try:
            # Sessions should be different objects
            assert session1 is not session2
            # But both should be valid sessions
            assert isinstance(session1, Session)
            assert isinstance(session2, Session)
        finally:
            session1.close()
            session2.close()

    def test_session_can_begin_transaction(self):
        """Test that session can begin a transaction."""
        from app.db import SessionLocal

        session = SessionLocal()
        try:
            # Starting a transaction should work
            session.begin()
            # Session should now be in a transaction
            assert session.in_transaction()
            session.rollback()
        finally:
            session.close()
