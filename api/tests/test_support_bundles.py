from __future__ import annotations

from app import models
from app.auth import create_access_token, hash_password
from app.config import settings


def _super_admin_headers(test_db, monkeypatch):
    user = models.User(
        username="superadmin",
        email="superadmin@example.com",
        hashed_password=hash_password("superadminpassword123"),
        is_active=True,
        global_role="super_admin",
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)
    monkeypatch.setattr(settings, "jwt_secret", "test-jwt-secret-key-for-testing")
    token = create_access_token(user.id)
    return {"Authorization": f"Bearer {token}"}


def test_create_support_bundle_requires_super_admin(test_client, auth_headers):
    payload = {
        "summary": "Deploy fails",
        "repro_steps": "Press up and wait for failure",
        "expected_behavior": "Lab should start",
        "actual_behavior": "Lab enters error",
        "time_window_hours": 24,
        "impacted_lab_ids": [],
        "impacted_agent_ids": [],
        "include_configs": False,
        "pii_safe": True,
    }
    resp = test_client.post("/support-bundles", json=payload, headers=auth_headers)
    assert resp.status_code == 403


def test_create_and_list_support_bundles_super_admin(test_client, test_db, monkeypatch):
    from app.routers import support as support_router

    def _skip_task(coro, **_kwargs):
        coro.close()
        return None

    monkeypatch.setattr(support_router, "safe_create_task", _skip_task)
    headers = _super_admin_headers(test_db, monkeypatch)

    payload = {
        "summary": "Node startup issue",
        "repro_steps": "Start node r1 then observe timeout",
        "expected_behavior": "Node starts and becomes ready",
        "actual_behavior": "Node remains in starting state",
        "time_window_hours": 24,
        "impacted_lab_ids": [],
        "impacted_agent_ids": [],
        "include_configs": False,
        "pii_safe": True,
    }
    create_resp = test_client.post("/support-bundles", json=payload, headers=headers)
    assert create_resp.status_code == 200
    body = create_resp.json()
    assert body["status"] == "pending"
    assert body["pii_safe"] is True
    assert body["include_configs"] is False

    list_resp = test_client.get("/support-bundles", headers=headers)
    assert list_resp.status_code == 200
    bundles = list_resp.json()
    assert len(bundles) >= 1
    assert any(b["id"] == body["id"] for b in bundles)


def test_download_support_bundle_completed(test_client, test_db, monkeypatch, tmp_path):
    headers = _super_admin_headers(test_db, monkeypatch)
    user_id = test_db.query(models.User.id).first()[0]

    bundle_file = tmp_path / "bundle.zip"
    bundle_file.write_bytes(b"zip-content")
    bundle = models.SupportBundle(
        user_id=user_id,
        status="completed",
        include_configs=False,
        pii_safe=True,
        time_window_hours=24,
        options_json="{}",
        incident_json="{}",
        file_path=str(bundle_file),
        size_bytes=bundle_file.stat().st_size,
    )
    test_db.add(bundle)
    test_db.commit()
    test_db.refresh(bundle)

    resp = test_client.get(f"/support-bundles/{bundle.id}/download", headers=headers)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/zip")
