from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request
from sqlalchemy.orm import Session

from app import models
from app.auth import hash_password
from app.enums import GlobalRole
from app.routers import users as users_router


class TestUsersRouter:
    def test_list_users_requires_admin(self, test_client: TestClient, auth_headers: dict):
        response = test_client.get("/users", headers=auth_headers)
        assert response.status_code == 403

    def test_list_users_admin(self, test_client: TestClient, admin_auth_headers: dict):
        response = test_client.get("/users", headers=admin_auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "users" in data
        assert "total" in data

    def test_get_user_self_allowed(self, test_client: TestClient, auth_headers: dict, test_user: models.User):
        response = test_client.get(f"/users/{test_user.id}", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["id"] == test_user.id

    def test_get_user_other_requires_admin(
        self,
        test_client: TestClient,
        auth_headers: dict,
        admin_user: models.User,
    ):
        response = test_client.get(f"/users/{admin_user.id}", headers=auth_headers)
        assert response.status_code == 403

    def test_create_user_admin(self, test_client: TestClient, admin_auth_headers: dict, test_db: Session):
        payload = {
            "username": "newuser",
            "password": "newpassword1",
            "email": "newuser@example.com",
            "global_role": "operator",
        }
        response = test_client.post("/users", json=payload, headers=admin_auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["username"] == "newuser"
        assert data["email"] == "newuser@example.com"

        user = test_db.query(models.User).filter(models.User.username == "newuser").first()
        assert user is not None

    def test_create_user_super_admin_requires_super_admin(
        self,
        test_client: TestClient,
        admin_auth_headers: dict,
    ):
        payload = {
            "username": "superuser",
            "password": "newpassword1",
            "email": "superuser@example.com",
            "global_role": GlobalRole.SUPER_ADMIN.value,
        }
        response = test_client.post("/users", json=payload, headers=admin_auth_headers)
        assert response.status_code == 403

    def test_create_user_uniqueness_conflict(
        self,
        test_client: TestClient,
        admin_auth_headers: dict,
        test_user: models.User,
    ):
        payload = {
            "username": test_user.username,
            "password": "newpassword1",
            "email": "unique@example.com",
            "global_role": "operator",
        }
        response = test_client.post("/users", json=payload, headers=admin_auth_headers)
        assert response.status_code == 409

    def test_update_user_email_conflict(
        self,
        test_client: TestClient,
        admin_auth_headers: dict,
        test_db: Session,
        test_user: models.User,
    ):
        other = models.User(
            username="other",
            email="other@example.com",
            hashed_password=hash_password("password123"),
            is_active=True,
        )
        test_db.add(other)
        test_db.commit()

        payload = {"email": "other@example.com"}
        response = test_client.patch(f"/users/{test_user.id}", json=payload, headers=admin_auth_headers)
        assert response.status_code == 409

    def test_change_password_self_requires_current(
        self,
        test_client: TestClient,
        auth_headers: dict,
        test_user: models.User,
    ):
        payload = {"new_password": "anotherpass1"}
        response = test_client.put(f"/users/{test_user.id}/password", json=payload, headers=auth_headers)
        assert response.status_code == 400

    def test_change_password_self_wrong_current(
        self,
        test_client: TestClient,
        auth_headers: dict,
        test_user: models.User,
    ):
        payload = {"current_password": "wrong", "new_password": "anotherpass1"}
        response = test_client.put(f"/users/{test_user.id}/password", json=payload, headers=auth_headers)
        assert response.status_code == 400

    def test_change_password_self_success(
        self,
        test_client: TestClient,
        auth_headers: dict,
        test_user: models.User,
    ):
        payload = {"current_password": "testpassword123", "new_password": "anotherpass1"}
        response = test_client.put(f"/users/{test_user.id}/password", json=payload, headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["status"] == "password_changed"

    def test_change_password_admin_for_other(
        self,
        test_client: TestClient,
        admin_auth_headers: dict,
        test_user: models.User,
    ):
        payload = {"new_password": "anotherpass1"}
        response = test_client.put(f"/users/{test_user.id}/password", json=payload, headers=admin_auth_headers)
        assert response.status_code == 200

    def test_deactivate_user_admin(self, test_client: TestClient, admin_auth_headers: dict, test_user: models.User, test_db: Session):
        response = test_client.post(f"/users/{test_user.id}/deactivate", headers=admin_auth_headers)
        assert response.status_code == 200

        test_db.refresh(test_user)
        assert test_user.is_active is False

    def test_deactivate_self_forbidden(self, test_client: TestClient, admin_auth_headers: dict, admin_user: models.User):
        response = test_client.post(f"/users/{admin_user.id}/deactivate", headers=admin_auth_headers)
        assert response.status_code == 400


def test_get_client_ip_forwarded_for():
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/users",
        "headers": [(b"x-forwarded-for", b"203.0.113.9, 10.0.0.1")],
        "client": ("10.0.0.1", 1234),
    }
    req = Request(scope)
    assert users_router._get_client_ip(req) == "203.0.113.9"


def test_get_client_ip_fallback_to_client():
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/users",
        "headers": [],
        "client": ("10.0.0.2", 5678),
    }
    req = Request(scope)
    assert users_router._get_client_ip(req) == "10.0.0.2"

