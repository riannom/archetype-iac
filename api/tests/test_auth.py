"""Tests for authentication endpoints."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models
from app.auth import hash_password


class TestLogin:
    """Tests for /auth/login endpoint."""

    def test_login_valid_credentials(
        self,
        test_client: TestClient,
        test_user: models.User,
    ):
        """Test login with valid credentials returns token."""
        response = test_client.post(
            "/auth/login",
            data={"username": test_user.email, "password": "testpassword123"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

    def test_login_invalid_password(
        self,
        test_client: TestClient,
        test_user: models.User,
    ):
        """Test login with wrong password returns 401."""
        response = test_client.post(
            "/auth/login",
            data={"username": test_user.email, "password": "wrongpassword"},
        )
        assert response.status_code == 401
        assert "Invalid credentials" in response.json()["detail"]

    def test_login_nonexistent_user(
        self,
        test_client: TestClient,
    ):
        """Test login with non-existent user returns 401."""
        response = test_client.post(
            "/auth/login",
            data={"username": "nonexistent@example.com", "password": "anypassword"},
        )
        assert response.status_code == 401
        assert "Invalid credentials" in response.json()["detail"]

    def test_login_disabled_when_local_auth_off(
        self,
        test_client: TestClient,
        monkeypatch,
    ):
        """Test login is disabled when local_auth_enabled is False."""
        from app.config import settings

        monkeypatch.setattr(settings, "local_auth_enabled", False)

        response = test_client.post(
            "/auth/login",
            data={"username": "test@example.com", "password": "anypassword"},
        )
        assert response.status_code == 403
        assert "Local auth is disabled" in response.json()["detail"]


class TestRegister:
    """Tests for /auth/register endpoint."""

    def test_register_new_user(
        self,
        test_client: TestClient,
        test_db: Session,
    ):
        """Test registering a new user."""
        response = test_client.post(
            "/auth/register",
            json={"email": "newuser@example.com", "password": "newpassword123"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["email"] == "newuser@example.com"
        assert data["is_active"] is True
        assert data["is_admin"] is False
        assert "id" in data

        # Verify user was created in database
        user = (
            test_db.query(models.User)
            .filter(models.User.email == "newuser@example.com")
            .first()
        )
        assert user is not None

    def test_register_duplicate_email(
        self,
        test_client: TestClient,
        test_user: models.User,
    ):
        """Test registering with existing email returns 409."""
        response = test_client.post(
            "/auth/register",
            json={"email": test_user.email, "password": "newpassword123"},
        )
        assert response.status_code == 409
        assert "already registered" in response.json()["detail"]

    def test_register_password_too_short(
        self,
        test_client: TestClient,
    ):
        """Test registering with short password returns 422."""
        response = test_client.post(
            "/auth/register",
            json={"email": "test@example.com", "password": "short"},
        )
        assert response.status_code == 422

    def test_register_password_too_long(
        self,
        test_client: TestClient,
    ):
        """Test registering with password > 72 bytes returns 400."""
        # bcrypt has a 72-byte limit
        long_password = "a" * 73
        response = test_client.post(
            "/auth/register",
            json={"email": "test@example.com", "password": long_password},
        )
        assert response.status_code == 400
        assert "72 bytes" in response.json()["detail"]

    def test_register_invalid_email(
        self,
        test_client: TestClient,
    ):
        """Test registering with invalid email returns 422."""
        response = test_client.post(
            "/auth/register",
            json={"email": "not-an-email", "password": "validpassword123"},
        )
        assert response.status_code == 422

    def test_register_disabled_when_local_auth_off(
        self,
        test_client: TestClient,
        monkeypatch,
    ):
        """Test registration is disabled when local_auth_enabled is False."""
        from app.config import settings

        monkeypatch.setattr(settings, "local_auth_enabled", False)

        response = test_client.post(
            "/auth/register",
            json={"email": "new@example.com", "password": "validpassword123"},
        )
        assert response.status_code == 403
        assert "Local auth is disabled" in response.json()["detail"]


class TestMe:
    """Tests for /auth/me endpoint."""

    def test_me_authenticated(
        self,
        test_client: TestClient,
        test_user: models.User,
        auth_headers: dict,
    ):
        """Test getting current user info with valid token."""
        response = test_client.get("/auth/me", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == test_user.id
        assert data["email"] == test_user.email
        assert data["is_active"] is True
        assert data["is_admin"] is False

    def test_me_admin_user(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
    ):
        """Test getting admin user info."""
        response = test_client.get("/auth/me", headers=admin_auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == admin_user.id
        assert data["is_admin"] is True

    def test_me_unauthenticated(
        self,
        test_client: TestClient,
    ):
        """Test getting user info without token returns 401."""
        response = test_client.get("/auth/me")
        assert response.status_code == 401

    def test_me_invalid_token(
        self,
        test_client: TestClient,
    ):
        """Test getting user info with invalid token returns 401."""
        response = test_client.get(
            "/auth/me", headers={"Authorization": "Bearer invalid-token"}
        )
        assert response.status_code == 401

    def test_me_expired_token(
        self,
        test_client: TestClient,
        test_user: models.User,
        monkeypatch,
    ):
        """Test getting user info with expired token returns 401."""
        from datetime import datetime, timedelta, timezone

        from jose import jwt

        from app.config import settings

        monkeypatch.setattr(settings, "jwt_secret", "test-jwt-secret-key-for-testing")

        # Create an expired token
        expire = datetime.now(timezone.utc) - timedelta(minutes=30)
        payload = {"sub": test_user.id, "exp": expire}
        expired_token = jwt.encode(
            payload, settings.jwt_secret, algorithm=settings.jwt_algorithm
        )

        response = test_client.get(
            "/auth/me", headers={"Authorization": f"Bearer {expired_token}"}
        )
        assert response.status_code == 401


class TestProtectedRoutes:
    """Tests for protected route access."""

    def test_protected_route_requires_auth(
        self,
        test_client: TestClient,
    ):
        """Test that protected routes require authentication."""
        # Try accessing labs without auth
        response = test_client.get("/labs")
        assert response.status_code == 401

    def test_protected_route_with_auth(
        self,
        test_client: TestClient,
        auth_headers: dict,
    ):
        """Test that protected routes work with valid auth."""
        response = test_client.get("/labs", headers=auth_headers)
        assert response.status_code == 200

    def test_token_from_login_works(
        self,
        test_client: TestClient,
        test_user: models.User,
    ):
        """Test that token obtained from login can access protected routes."""
        # Get token
        login_response = test_client.post(
            "/auth/login",
            data={"username": test_user.email, "password": "testpassword123"},
        )
        assert login_response.status_code == 200
        token = login_response.json()["access_token"]

        # Use token to access protected route
        response = test_client.get(
            "/labs", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200


class TestAdminRoutes:
    """Tests for admin-only functionality."""

    def test_admin_can_update_others_lab(
        self,
        test_client: TestClient,
        test_db: Session,
        test_user: models.User,
        admin_auth_headers: dict,
    ):
        """Test that admin can update labs owned by other users."""
        # Create lab owned by test_user
        lab = models.Lab(name="User Lab", owner_id=test_user.id, provider="containerlab")
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        # Admin updates it
        response = test_client.put(
            f"/labs/{lab.id}",
            json={"name": "Admin Updated"},
            headers=admin_auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["name"] == "Admin Updated"

    def test_admin_can_delete_others_lab(
        self,
        test_client: TestClient,
        test_db: Session,
        test_user: models.User,
        admin_auth_headers: dict,
    ):
        """Test that admin can delete labs owned by other users."""
        # Create lab owned by test_user
        lab = models.Lab(name="User Lab", owner_id=test_user.id, provider="containerlab")
        test_db.add(lab)
        test_db.commit()
        lab_id = lab.id

        # Admin deletes it
        response = test_client.delete(f"/labs/{lab_id}", headers=admin_auth_headers)
        assert response.status_code == 200

        # Verify deleted
        deleted_lab = test_db.query(models.Lab).filter(models.Lab.id == lab_id).first()
        assert deleted_lab is None

    def test_non_admin_cannot_update_others_lab(
        self,
        test_client: TestClient,
        test_db: Session,
        admin_user: models.User,
        auth_headers: dict,
    ):
        """Test that non-admin cannot update labs they don't own."""
        # Create lab owned by admin
        lab = models.Lab(
            name="Admin Lab", owner_id=admin_user.id, provider="containerlab"
        )
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        # Regular user tries to update
        response = test_client.put(
            f"/labs/{lab.id}",
            json={"name": "Hacked"},
            headers=auth_headers,
        )
        # Should fail because test_user doesn't have access
        assert response.status_code in (403, 404)
