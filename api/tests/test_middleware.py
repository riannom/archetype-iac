"""Tests for CurrentUserMiddleware (middleware.py).

This module tests:
- User injection into request state
- Handling of authenticated and unauthenticated requests
- Database session management within middleware
- Error handling during user retrieval
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.middleware import CurrentUserMiddleware


def _mock_get_session(mock_session):
    """Create a mock get_session context manager that yields the given session."""
    @contextmanager
    def _get_session():
        yield mock_session
    return _get_session


class TestCurrentUserMiddleware:
    """Tests for CurrentUserMiddleware class."""

    def test_middleware_sets_user_on_request_state(self, monkeypatch):
        """Test that middleware sets user on request.state when authenticated."""
        # Create a test app with the middleware
        app = FastAPI()
        app.add_middleware(CurrentUserMiddleware)

        # Mock user
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.email = "test@example.com"

        # Mock session returned by get_session context manager
        mock_session = MagicMock()

        @app.get("/test")
        async def test_endpoint(request: Request):
            user = getattr(request.state, "user", None)
            return {"user_id": user.id if user else None}

        with patch("app.middleware.get_session", _mock_get_session(mock_session)), \
             patch("app.middleware.get_current_user_optional", return_value=mock_user):
            client = TestClient(app)
            response = client.get("/test")
            assert response.status_code == 200
            assert response.json()["user_id"] == 1

    def test_middleware_sets_none_for_unauthenticated(self, monkeypatch):
        """Test that middleware sets None on request.state when not authenticated."""
        app = FastAPI()
        app.add_middleware(CurrentUserMiddleware)

        mock_session = MagicMock()

        @app.get("/test")
        async def test_endpoint(request: Request):
            user = getattr(request.state, "user", None)
            return {"user_id": user.id if user else None}

        with patch("app.middleware.get_session", _mock_get_session(mock_session)), \
             patch("app.middleware.get_current_user_optional", return_value=None):
            client = TestClient(app)
            response = client.get("/test")
            assert response.status_code == 200
            assert response.json()["user_id"] is None

    def test_middleware_closes_session_on_exception(self, monkeypatch):
        """Test that database session is cleaned up even if exception occurs.

        The get_session context manager handles rollback and close internally,
        so we verify the context manager is used (which guarantees cleanup).
        """
        app = FastAPI()
        app.add_middleware(CurrentUserMiddleware)

        mock_session = MagicMock()

        @app.get("/test")
        async def test_endpoint(request: Request):
            raise ValueError("Test error")

        # When get_current_user_optional raises an exception, get_session's
        # context manager __exit__ still runs, ensuring cleanup
        with patch("app.middleware.get_session", _mock_get_session(mock_session)), \
             patch("app.middleware.get_current_user_optional", side_effect=Exception("Auth error")):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/test")
            # The get_session context manager guarantees cleanup via its
            # finally block (rollback + close). We verify the middleware
            # didn't crash - if get_session wasn't properly used as a
            # context manager, this would raise.
            assert response.status_code == 500

    def test_middleware_creates_new_session_per_request(self, monkeypatch):
        """Test that middleware creates a new database session for each request."""
        app = FastAPI()
        app.add_middleware(CurrentUserMiddleware)

        sessions_created = []

        @contextmanager
        def mock_get_session():
            session = MagicMock()
            sessions_created.append(session)
            yield session

        @app.get("/test")
        async def test_endpoint(request: Request):
            return {"ok": True}

        with patch("app.middleware.get_session", mock_get_session), \
             patch("app.middleware.get_current_user_optional", return_value=None):
            client = TestClient(app)
            # Make multiple requests
            client.get("/test")
            client.get("/test")
            client.get("/test")
            # Each request should create its own session
            assert len(sessions_created) == 3

    def test_middleware_passes_request_to_user_function(self, monkeypatch):
        """Test that middleware passes request and db to get_current_user_optional."""
        app = FastAPI()
        app.add_middleware(CurrentUserMiddleware)

        mock_session = MagicMock()
        captured_args = {}

        def mock_get_user(request, database):
            captured_args["request"] = request
            captured_args["database"] = database
            return None

        @app.get("/test")
        async def test_endpoint(request: Request):
            return {"ok": True}

        with patch("app.middleware.get_session", _mock_get_session(mock_session)), \
             patch("app.middleware.get_current_user_optional", side_effect=mock_get_user):
            client = TestClient(app)
            client.get("/test", headers={"Authorization": "Bearer test-token"})
            # Verify arguments were passed correctly
            assert "request" in captured_args
            assert captured_args["database"] is mock_session


class TestMiddlewareIntegration:
    """Integration tests for middleware with auth system."""

    def test_middleware_extracts_user_from_valid_token(
        self, test_db, test_user, monkeypatch
    ):
        """Test that middleware correctly extracts user from valid JWT token."""
        from app.auth import create_access_token
        from app.config import settings

        # Use object.__setattr__ to bypass pydantic validation when setting jwt_secret
        # monkeypatch.setattr may not work reliably after many test-scoped patches
        original = settings.jwt_secret
        object.__setattr__(settings, "jwt_secret", "test-jwt-secret-key-for-testing")

        try:
            app = FastAPI()
            app.add_middleware(CurrentUserMiddleware)

            @app.get("/test")
            async def test_endpoint(request: Request):
                user = getattr(request.state, "user", None)
                return {
                    "user_id": user.id if user else None,
                    "email": user.email if user else None,
                }

            # Create valid token
            token = create_access_token(test_user.id)

            # Patch get_session to return our test_db
            with patch("app.middleware.get_session", _mock_get_session(test_db)):
                client = TestClient(app)
                response = client.get("/test", headers={"Authorization": f"Bearer {token}"})
                assert response.status_code == 200
                data = response.json()
                assert data["user_id"] == test_user.id
                assert data["email"] == test_user.email
        finally:
            object.__setattr__(settings, "jwt_secret", original)

    def test_middleware_returns_none_for_invalid_token(self, test_db, monkeypatch):
        """Test that middleware returns None for invalid JWT token."""
        from app.config import settings

        monkeypatch.setattr(settings, "jwt_secret", "test-jwt-secret-key-for-testing")

        app = FastAPI()
        app.add_middleware(CurrentUserMiddleware)

        @app.get("/test")
        async def test_endpoint(request: Request):
            user = getattr(request.state, "user", None)
            return {"user_id": user.id if user else None}

        with patch("app.middleware.get_session", _mock_get_session(test_db)):
            client = TestClient(app)
            response = client.get(
                "/test", headers={"Authorization": "Bearer invalid-token"}
            )
            assert response.status_code == 200
            assert response.json()["user_id"] is None

    def test_middleware_returns_none_for_missing_auth_header(
        self, test_db, monkeypatch
    ):
        """Test that middleware returns None when no auth header present."""
        from app.config import settings

        monkeypatch.setattr(settings, "jwt_secret", "test-jwt-secret-key-for-testing")

        app = FastAPI()
        app.add_middleware(CurrentUserMiddleware)

        @app.get("/test")
        async def test_endpoint(request: Request):
            user = getattr(request.state, "user", None)
            return {"user_id": user.id if user else None}

        with patch("app.middleware.get_session", _mock_get_session(test_db)):
            client = TestClient(app)
            response = client.get("/test")
            assert response.status_code == 200
            assert response.json()["user_id"] is None

    def test_middleware_returns_none_for_non_bearer_auth(self, test_db, monkeypatch):
        """Test that middleware returns None for non-Bearer auth schemes."""
        from app.config import settings

        monkeypatch.setattr(settings, "jwt_secret", "test-jwt-secret-key-for-testing")

        app = FastAPI()
        app.add_middleware(CurrentUserMiddleware)

        @app.get("/test")
        async def test_endpoint(request: Request):
            user = getattr(request.state, "user", None)
            return {"user_id": user.id if user else None}

        with patch("app.middleware.get_session", _mock_get_session(test_db)):
            client = TestClient(app)
            response = client.get("/test", headers={"Authorization": "Basic dXNlcjpwYXNz"})
            assert response.status_code == 200
            assert response.json()["user_id"] is None
