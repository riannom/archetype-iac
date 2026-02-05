from __future__ import annotations

from datetime import datetime, timezone

import app.routers.images as images_router  # noqa: F401
import app.routers.infrastructure as infrastructure_router  # noqa: F401
import pytest

from app import models


def test_images_library_assign_unassign_and_list(test_client, auth_headers, monkeypatch) -> None:
    manifest = {
        "images": [
            {"id": "docker:img1", "kind": "docker", "reference": "img1:latest", "device_id": None},
        ]
    }

    monkeypatch.setattr("app.routers.images.load_manifest", lambda: manifest)
    monkeypatch.setattr("app.routers.images.save_manifest", lambda m: None)

    def fake_update_image_entry(manifest_obj, image_id, updates):
        for item in manifest_obj["images"]:
            if item["id"] == image_id:
                item.update(updates)
                return item
        return None

    monkeypatch.setattr("app.routers.images.update_image_entry", fake_update_image_entry)
    monkeypatch.setattr("app.routers.images.find_image_by_id", lambda m, image_id: m["images"][0])

    resp = test_client.get("/images/library", headers=auth_headers)
    assert resp.status_code == 200
    assert len(resp.json()["images"]) == 1

    assign = test_client.post(
        "/images/library/docker:img1/assign",
        json={"device_id": "eos", "is_default": True},
        headers=auth_headers,
    )
    assert assign.status_code == 200
    assert assign.json()["image"]["device_id"] == "eos"

    unassign = test_client.post(
        "/images/library/docker:img1/unassign",
        headers=auth_headers,
    )
    assert unassign.status_code == 200
    assert unassign.json()["image"]["device_id"] is None


def test_images_hosts_and_sync_jobs(test_client, test_db, auth_headers, monkeypatch) -> None:
    host = models.Host(
        id="h1",
        name="Host",
        address="localhost:1",
        status="online",
        version="1.0.0",
    )
    test_db.add(host)
    test_db.commit()

    manifest = {
        "images": [
            {"id": "docker:img1", "kind": "docker", "reference": "img1:latest"},
        ]
    }
    monkeypatch.setattr("app.routers.images.load_manifest", lambda: manifest)
    monkeypatch.setattr("app.routers.images.find_image_by_id", lambda m, image_id: m["images"][0])

    resp = test_client.get("/images/library/docker:img1/hosts", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["hosts"][0]["status"] == "unknown"

    push = test_client.post(
        "/images/library/docker:img1/push",
        json={},
        headers=auth_headers,
    )
    assert push.status_code == 200
    assert push.json()["count"] == 1

    jobs = test_client.get("/images/sync-jobs", headers=auth_headers)
    assert jobs.status_code == 200
    assert len(jobs.json()) == 1

    job_id = jobs.json()[0]["id"]
    job_details = test_client.get(f"/images/sync-jobs/{job_id}", headers=auth_headers)
    assert job_details.status_code == 200

    cancelled = test_client.delete(f"/images/sync-jobs/{job_id}", headers=auth_headers)
    assert cancelled.status_code == 200


def test_infrastructure_settings_and_mesh(test_client, test_db, admin_user, admin_auth_headers) -> None:
    resp = test_client.get("/infrastructure/settings", headers=admin_auth_headers)
    assert resp.status_code == 200

    patch = test_client.patch(
        "/infrastructure/settings",
        json={"overlay_mtu": 1400, "mtu_verification_enabled": False},
        headers=admin_auth_headers,
    )
    assert patch.status_code == 200
    assert patch.json()["overlay_mtu"] == 1400

    host = models.Host(
        id="h1",
        name="Host",
        address="localhost:1",
        status="online",
        version="1.0.0",
    )
    test_db.add(host)
    test_db.commit()

    mesh = test_client.get("/infrastructure/mesh", headers=admin_auth_headers)
    assert mesh.status_code == 200
    assert mesh.json()["settings"]["overlay_mtu"] == 1400
