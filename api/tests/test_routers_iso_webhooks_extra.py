from __future__ import annotations

from unittest.mock import AsyncMock

import app.routers.iso as iso_router  # noqa: F401
import app.routers.webhooks as webhooks_router  # noqa: F401

from app.config import settings


def test_iso_browse_empty(test_client, auth_headers, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "iso_upload_dir", str(tmp_path))

    resp = test_client.get("/iso/browse", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["files"] == []


def test_iso_upload_init_and_status(test_client, auth_headers, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "iso_upload_dir", str(tmp_path))

    init = test_client.post(
        "/iso/upload/init",
        json={"filename": "test.iso", "total_size": 5, "chunk_size": 5},
        headers=auth_headers,
    )
    assert init.status_code == 200
    upload_id = init.json()["upload_id"]

    status = test_client.get(f"/iso/upload/{upload_id}", headers=auth_headers)
    assert status.status_code == 200


def test_webhook_crud_and_test(test_client, test_db, test_user, auth_headers, monkeypatch) -> None:
    monkeypatch.setattr("app.webhooks.test_webhook", AsyncMock(return_value=(True, 200, None, 5)))
    monkeypatch.setattr("app.webhooks.build_webhook_payload", lambda event_type, extra=None: {"event": event_type})
    monkeypatch.setattr("app.webhooks.log_delivery", lambda *args, **kwargs: None)

    create = test_client.post(
        "/webhooks",
        json={
            "name": "Test",
            "url": "https://example.com/hook",
            "events": ["job.completed"],
            "enabled": True,
        },
        headers=auth_headers,
    )
    assert create.status_code == 200
    webhook_id = create.json()["id"]

    list_resp = test_client.get("/webhooks", headers=auth_headers)
    assert list_resp.status_code == 200
    assert len(list_resp.json()["webhooks"]) == 1

    get_resp = test_client.get(f"/webhooks/{webhook_id}", headers=auth_headers)
    assert get_resp.status_code == 200

    update = test_client.put(
        f"/webhooks/{webhook_id}",
        json={"name": "Updated", "events": ["job.completed"]},
        headers=auth_headers,
    )
    assert update.status_code == 200

    test_resp = test_client.post(f"/webhooks/{webhook_id}/test", headers=auth_headers)
    assert test_resp.status_code == 200
    assert test_resp.json()["success"] is True

    delete = test_client.delete(f"/webhooks/{webhook_id}", headers=auth_headers)
    assert delete.status_code == 200
