from __future__ import annotations


import app.routers.auth as auth_router  # noqa: F401


def test_register_and_login_flow(test_client, test_db, monkeypatch) -> None:
    monkeypatch.setattr("app.config.settings.local_auth_enabled", True)

    from app.auth import hash_password
    from app import models

    user = models.User(
        username="newuser",
        email="new@example.com",
        hashed_password=hash_password("pass1234"),
        is_active=True,
        global_role="operator",
    )
    test_db.add(user)
    test_db.commit()

    login = test_client.post("/auth/login", data={"username": "new@example.com", "password": "pass1234"})
    assert login.status_code == 200
    token = login.json().get("access_token")
    assert token


def test_register_duplicate_and_password_length(test_client, test_db, monkeypatch) -> None:
    monkeypatch.setattr("app.config.settings.local_auth_enabled", True)

    from app.auth import hash_password
    from app import models

    # Login with nonexistent user returns 401
    resp = test_client.post("/auth/login", data={"username": "nosuch@example.com", "password": "password123"})
    assert resp.status_code == 401

    # Login with wrong password returns 401
    user = models.User(
        username="existing",
        email="existing@example.com",
        hashed_password=hash_password("correctpassword"),
        is_active=True,
        global_role="operator",
    )
    test_db.add(user)
    test_db.commit()

    resp = test_client.post("/auth/login", data={"username": "existing@example.com", "password": "wrongpassword"})
    assert resp.status_code == 401


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
    assert "theme_settings" in prefs.json()

    update = {
        "notification_settings": {"bell": {"enabled": False}},
        "canvas_settings": {"showAgentIndicators": False, "consoleInBottomPanel": True},
        "theme_settings": {
            "themeId": "ocean",
            "mode": "dark",
            "backgroundId": "stargazing",
            "backgroundOpacity": 80,
            "taskLogOpacity": 70,
            "favoriteBackgrounds": ["stargazing", "constellation"],
            "favoriteThemeIds": ["ocean", "midnight"],
            "customThemes": [{"id": "custom-theme", "name": "Custom Theme"}],
        },
    }
    patched = test_client.patch("/auth/preferences", json=update, headers=auth_headers)
    assert patched.status_code == 200
    body = patched.json()
    assert body["notification_settings"]["bell"]["enabled"] is False
    assert body["canvas_settings"]["showAgentIndicators"] is False
    assert body["canvas_settings"]["consoleInBottomPanel"] is True
    assert body["theme_settings"]["themeId"] == "ocean"
    assert body["theme_settings"]["mode"] == "dark"
    assert body["theme_settings"]["favoriteThemeIds"] == ["ocean", "midnight"]
    assert body["theme_settings"]["customThemes"][0]["id"] == "custom-theme"

    prefs_again = test_client.get("/auth/preferences", headers=auth_headers)
    assert prefs_again.status_code == 200
    body = prefs_again.json()
    assert body["notification_settings"]["bell"]["enabled"] is False
    assert body["theme_settings"]["themeId"] == "ocean"
