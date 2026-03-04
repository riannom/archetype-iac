"""Tests for OIDC endpoints in api/app/routers/auth.py.

Covers:
- GET /auth/oidc/login — OIDC not configured (503)
- GET /auth/oidc/callback — OIDC not configured, token exchange failure,
  missing email, success flow, existing user, deactivated user, redirect URL
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models
from app.config import settings


@pytest.fixture
def _oidc_configured():
    """Temporarily configure OIDC settings for tests that need them."""
    _orig_issuer = settings.oidc_issuer_url
    _orig_client = settings.oidc_client_id
    _orig_redirect = getattr(settings, "oidc_app_redirect_url", None)

    object.__setattr__(settings, "oidc_issuer_url", "https://idp.example.com")
    object.__setattr__(settings, "oidc_client_id", "test-client-id")

    yield

    object.__setattr__(settings, "oidc_issuer_url", _orig_issuer or "")
    object.__setattr__(settings, "oidc_client_id", _orig_client or "")
    object.__setattr__(settings, "oidc_app_redirect_url", _orig_redirect)


@pytest.fixture
def mock_oidc_client(_oidc_configured):
    """Provide a mock OIDC client on the oauth object, cleaned up after test."""
    from app.routers import auth as auth_mod

    mock_client = MagicMock()
    # Manually set the attribute (don't use monkeypatch to avoid cleanup issues)
    _had_oidc = hasattr(auth_mod.oauth, "oidc")
    _old_oidc = getattr(auth_mod.oauth, "oidc", None) if _had_oidc else None
    auth_mod.oauth.oidc = mock_client

    yield mock_client

    # Cleanup
    if _had_oidc:
        auth_mod.oauth.oidc = _old_oidc
    else:
        try:
            delattr(auth_mod.oauth, "oidc")
        except AttributeError:
            pass


# ===========================================================================
# GET /auth/oidc/login
# ===========================================================================


class TestOidcLogin:

    def test_oidc_login_not_configured_returns_503(
        self, test_client: TestClient,
    ):
        """When OIDC is not configured, returns 503."""
        resp = test_client.get("/auth/oidc/login", follow_redirects=False)
        assert resp.status_code == 503
        assert "OIDC not configured" in resp.json()["detail"]


# ===========================================================================
# GET /auth/oidc/callback
# ===========================================================================


class TestOidcCallback:

    def test_oidc_callback_not_configured_returns_503(
        self, test_client: TestClient,
    ):
        """When OIDC is not configured, callback returns 503."""
        resp = test_client.get("/auth/oidc/callback")
        assert resp.status_code == 503
        assert "OIDC not configured" in resp.json()["detail"]

    def test_oidc_callback_token_exchange_failure(
        self, test_client: TestClient, mock_oidc_client,
    ):
        """When token exchange fails (OAuthError), returns 401."""
        from authlib.integrations.starlette_client import OAuthError

        mock_oidc_client.authorize_access_token = AsyncMock(
            side_effect=OAuthError(error="invalid_grant", description="Token expired")
        )

        resp = test_client.get("/auth/oidc/callback")
        assert resp.status_code == 401

    def test_oidc_callback_missing_email_returns_400(
        self, test_client: TestClient, mock_oidc_client,
    ):
        """When OIDC response has no email claim, returns 400."""
        mock_token = {"access_token": "test", "id_token": "test"}
        mock_oidc_client.authorize_access_token = AsyncMock(return_value=mock_token)
        mock_oidc_client.parse_id_token = AsyncMock(return_value={"sub": "12345"})

        resp = test_client.get("/auth/oidc/callback")
        assert resp.status_code == 400
        assert "email" in resp.json()["detail"].lower()

    def test_oidc_callback_success_creates_user(
        self, test_client: TestClient, test_db: Session, mock_oidc_client,
    ):
        """Successful OIDC callback creates a new user and returns a token."""
        object.__setattr__(settings, "oidc_app_redirect_url", "")

        mock_token = {"access_token": "test", "id_token": "test"}
        mock_oidc_client.authorize_access_token = AsyncMock(return_value=mock_token)
        mock_oidc_client.parse_id_token = AsyncMock(return_value={
            "email": "oidc-user@example.com",
            "preferred_username": "oidcuser",
            "sub": "12345",
        })

        resp = test_client.get("/auth/oidc/callback")
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data

        user = test_db.query(models.User).filter(
            models.User.email == "oidc-user@example.com"
        ).first()
        assert user is not None
        assert user.username == "oidcuser"

    def test_oidc_callback_existing_user_returns_token(
        self, test_client: TestClient, test_db: Session, mock_oidc_client,
    ):
        """OIDC callback for existing user returns token without creating a new user."""
        from app.auth import hash_password

        existing_user = models.User(
            username="existing_oidc",
            email="existing@example.com",
            hashed_password=hash_password("dummy"),
            is_active=True,
            global_role="operator",
        )
        test_db.add(existing_user)
        test_db.commit()

        object.__setattr__(settings, "oidc_app_redirect_url", "")

        mock_token = {"access_token": "test", "id_token": "test"}
        mock_oidc_client.authorize_access_token = AsyncMock(return_value=mock_token)
        mock_oidc_client.parse_id_token = AsyncMock(return_value={
            "email": "existing@example.com",
            "sub": "99999",
        })

        resp = test_client.get("/auth/oidc/callback")
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data

        count = test_db.query(models.User).filter(
            models.User.email == "existing@example.com"
        ).count()
        assert count == 1

    def test_oidc_callback_deactivated_user_returns_401(
        self, test_client: TestClient, test_db: Session, mock_oidc_client,
    ):
        """OIDC callback for a deactivated user returns 401."""
        from app.auth import hash_password

        deactivated_user = models.User(
            username="deactivated",
            email="deactivated@example.com",
            hashed_password=hash_password("dummy"),
            is_active=False,
            global_role="operator",
        )
        test_db.add(deactivated_user)
        test_db.commit()

        mock_token = {"access_token": "test", "id_token": "test"}
        mock_oidc_client.authorize_access_token = AsyncMock(return_value=mock_token)
        mock_oidc_client.parse_id_token = AsyncMock(return_value={
            "email": "deactivated@example.com",
            "sub": "deactivated-sub",
        })

        resp = test_client.get("/auth/oidc/callback")
        assert resp.status_code == 401
        assert "deactivated" in resp.json()["detail"].lower()

    def test_oidc_callback_with_redirect_url(
        self, test_client: TestClient, test_db: Session, mock_oidc_client,
    ):
        """When oidc_app_redirect_url is set, returns a redirect with token."""
        from app.auth import hash_password

        user = models.User(
            username="redirect_user",
            email="redirect@example.com",
            hashed_password=hash_password("dummy"),
            is_active=True,
            global_role="operator",
        )
        test_db.add(user)
        test_db.commit()

        object.__setattr__(settings, "oidc_app_redirect_url", "https://app.example.com/")

        mock_token = {"access_token": "test", "id_token": "test"}
        mock_oidc_client.authorize_access_token = AsyncMock(return_value=mock_token)
        mock_oidc_client.parse_id_token = AsyncMock(return_value={
            "email": "redirect@example.com",
            "sub": "redirect-sub",
        })

        resp = test_client.get("/auth/oidc/callback", follow_redirects=False)
        assert resp.status_code == 307
        location = resp.headers.get("location", "")
        assert location.startswith("https://app.example.com/")
        assert "#token=" in location
