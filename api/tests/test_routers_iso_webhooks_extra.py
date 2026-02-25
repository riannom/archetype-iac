from __future__ import annotations

from unittest.mock import AsyncMock

import app.routers.iso as iso_router  # noqa: F401
import app.routers.webhooks as webhooks_router  # noqa: F401
import pytest

from app.config import settings
from app.iso.models import ParsedImage, ParsedNodeDefinition


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


@pytest.mark.asyncio
async def test_import_single_image_uses_alias_aware_vendor_probe_lookup(monkeypatch, tmp_path) -> None:
    class FakeVendorConfig:
        readiness_probe = "none"

    monkeypatch.setattr(
        "agent.vendors.get_config_by_device",
        lambda device_id: FakeVendorConfig() if device_id == "cisco_c8000v" else None,
    )
    monkeypatch.setattr(
        iso_router,
        "get_image_device_mapping",
        lambda image, node_defs: ("cisco_c8000v", None),
    )
    monkeypatch.setattr(iso_router, "_update_image_progress", lambda *args, **kwargs: None)
    monkeypatch.setattr(iso_router, "find_image_by_id", lambda manifest, image_id: None)

    captured: dict[str, object] = {}

    def fake_create_image_entry(**kwargs):
        captured.update(kwargs)
        return {
            "id": kwargs["image_id"],
            "compatible_devices": kwargs.get("compatible_devices") or [],
        }

    monkeypatch.setattr(iso_router, "create_image_entry", fake_create_image_entry)

    class FakeExtractor:
        async def extract_file(self, source, dest, progress_callback=None, timeout_seconds=None):
            dest.write_bytes(b"qcow2")

    image = ParsedImage(
        id="img1",
        node_definition_id="nd1",
        disk_image_filename="cat9k-shared.qcow2",
        disk_image_path="/images/cat9k-shared.qcow2",
        image_type="qcow2",
        version="17.16.01a",
    )
    node_def = ParsedNodeDefinition(
        id="nd1",
        label="Cat9000v",
        boot_completed_patterns=["login:"],
    )

    manifest_data = {"images": []}
    await iso_router._import_single_image(
        session_id="sess-1",
        image=image,
        node_definitions=[node_def],
        extractor=FakeExtractor(),
        image_store=tmp_path,
        manifest_data=manifest_data,
        create_devices=False,
        iso_source="sample.iso",
    )

    assert captured["device_id"] == "cisco_c8000v"
    assert captured["readiness_probe"] is None
