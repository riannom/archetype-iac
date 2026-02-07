from __future__ import annotations

import json

import app.routers.auth as auth_router  # noqa: F401
import pytest

from app import models


def test_register_and_login_flow(test_client, test_db, monkeypatch) -> None:
    monkeypatch.setattr("app.config.settings.local_auth_enabled", True)

    payload = {"email": "new@example.com", "password": "pass1234"}
    resp = test_client.post("/auth/register", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == payload["email"]

    login = test_client.post("/auth/login", data={"username": payload["email"], "password": payload["password"]})
    assert login.status_code == 200
    token = login.json().get("access_token")
    assert token


def test_register_duplicate_and_password_length(test_client, test_db, monkeypatch) -> None:
    monkeypatch.setattr("app.config.settings.local_auth_enabled", True)

    user = models.User(email="dup@example.com", hashed_password="hash")
    test_db.add(user)
    test_db.commit()

    # Duplicate email with valid password length (min 8 chars)
    resp = test_client.post("/auth/register", json={"email": "dup@example.com", "password": "password123"})
    assert resp.status_code == 409

    # Password too short (< 8 chars) returns 422 validation error
    resp = test_client.post("/auth/register", json={"email": "short@example.com", "password": "pass"})
    assert resp.status_code == 422

    # Password too long (> 72 bytes) returns 422 validation error (Pydantic max_length=72)
    long_pw = "a" * 73
    resp = test_client.post("/auth/register", json={"email": "long@example.com", "password": long_pw})
    assert resp.status_code == 422


def test_login_invalid_credentials(test_client, monkeypatch) -> None:
    monkeypatch.setattr("app.config.settings.local_auth_enabled", True)

    resp = test_client.post("/auth/login", data={"username": "nope", "password": "bad"})
    assert resp.status_code == 401


def test_me_and_preferences(test_client, test_db, test_user, auth_headers) -> None:
    resp = test_client.get("/auth/me", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["email"] == test_user.email

    prefs = test_client.get("/auth/preferences", headers=auth_headers)
    assert prefs.status_code == 200
    assert "notification_settings" in prefs.json()
    assert "canvas_settings" in prefs.json()

    update = {
        "notification_settings": {"bell": {"enabled": False}},
        "canvas_settings": {"showAgentIndicators": False, "consoleInBottomPanel": True},
    }
    patched = test_client.patch("/auth/preferences", json=update, headers=auth_headers)
    assert patched.status_code == 200
    body = patched.json()
    assert body["notification_settings"]["bell"]["enabled"] is False
    assert body["canvas_settings"]["showAgentIndicators"] is False
    assert body["canvas_settings"]["consoleInBottomPanel"] is True

    prefs_again = test_client.get("/auth/preferences", headers=auth_headers)
    assert prefs_again.status_code == 200
    body = prefs_again.json()
    assert body["notification_settings"]["bell"]["enabled"] is False
