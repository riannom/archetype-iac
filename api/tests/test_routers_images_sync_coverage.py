"""Tests for app.routers.images.sync — image sync endpoints coverage."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MOCK_MANIFEST = [
    {
        "id": "docker:ceos:4.28.0F",
        "reference": "ceos:4.28.0F",
        "kind": "docker",
        "device_id": "arista_ceos",
    },
    {
        "id": "file:/images/iosv.qcow2",
        "reference": "/images/iosv.qcow2",
        "kind": "qcow2",
        "device_id": "cisco_iosv",
    },
]


def _mock_find_image(manifest, image_id):
    """Mock find_image_by_id that searches MOCK_MANIFEST."""
    for img in manifest:
        if img["id"] == image_id:
            return img
    return None


def _make_sync_job(
    test_db: Session,
    host_id: str,
    *,
    image_id: str = "docker:ceos:4.28.0F",
    status: str = "pending",
    job_id: str | None = None,
) -> models.ImageSyncJob:
    job = models.ImageSyncJob(
        id=job_id or str(uuid4()),
        image_id=image_id,
        host_id=host_id,
        status=status,
    )
    test_db.add(job)
    test_db.commit()
    test_db.refresh(job)
    return job


# ---------------------------------------------------------------------------
# GET /images/library/{image_id}/hosts
# ---------------------------------------------------------------------------

class TestGetImageHosts:
    def test_image_not_found(
        self, test_client: TestClient, auth_headers: dict
    ):
        with (
            patch("app.routers.images.sync.load_manifest", return_value=MOCK_MANIFEST),
            patch("app.routers.images.sync.find_image_by_id", return_value=None),
        ):
            resp = test_client.get("/images/library/nonexistent/hosts", headers=auth_headers)
        assert resp.status_code == 404

    def test_no_hosts(
        self, test_client: TestClient, auth_headers: dict
    ):
        with (
            patch("app.routers.images.sync.load_manifest", return_value=MOCK_MANIFEST),
            patch("app.routers.images.sync.find_image_by_id", side_effect=_mock_find_image),
        ):
            resp = test_client.get(
                "/images/library/docker:ceos:4.28.0F/hosts",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["image_id"] == "docker:ceos:4.28.0F"
        assert data["hosts"] == []

    def test_with_host_and_image_host_record(
        self,
        test_client: TestClient,
        test_db: Session,
        auth_headers: dict,
        sample_host: models.Host,
    ):
        # Create an ImageHost record
        ih = models.ImageHost(
            id=str(uuid4()),
            image_id="docker:ceos:4.28.0F",
            host_id=sample_host.id,
            reference="ceos:4.28.0F",
            status="synced",
            synced_at=datetime.now(timezone.utc),
        )
        test_db.add(ih)
        test_db.commit()

        with (
            patch("app.routers.images.sync.load_manifest", return_value=MOCK_MANIFEST),
            patch("app.routers.images.sync.find_image_by_id", side_effect=_mock_find_image),
        ):
            resp = test_client.get(
                "/images/library/docker:ceos:4.28.0F/hosts",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["hosts"]) == 1
        assert data["hosts"][0]["status"] == "synced"

    def test_host_without_image_host_record(
        self,
        test_client: TestClient,
        test_db: Session,
        auth_headers: dict,
        sample_host: models.Host,
    ):
        """A host with no ImageHost record shows status 'unknown'."""
        with (
            patch("app.routers.images.sync.load_manifest", return_value=MOCK_MANIFEST),
            patch("app.routers.images.sync.find_image_by_id", side_effect=_mock_find_image),
        ):
            resp = test_client.get(
                "/images/library/docker:ceos:4.28.0F/hosts",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["hosts"]) == 1
        assert data["hosts"][0]["status"] == "unknown"


# ---------------------------------------------------------------------------
# GET /images/library/{image_id}/stream
# ---------------------------------------------------------------------------

class TestStreamImage:
    def test_image_not_found(
        self, test_client: TestClient, auth_headers: dict
    ):
        with (
            patch("app.routers.images.sync.load_manifest", return_value=MOCK_MANIFEST),
            patch("app.routers.images.sync.find_image_by_id", return_value=None),
        ):
            resp = test_client.get("/images/library/nonexistent/stream", headers=auth_headers)
        assert resp.status_code == 404

    def test_non_docker_image_rejected(
        self, test_client: TestClient, auth_headers: dict
    ):
        qcow2_image = {"id": "qcow2:iosv", "kind": "qcow2", "reference": "/images/iosv.qcow2"}
        with (
            patch("app.routers.images.sync.load_manifest", return_value=[qcow2_image]),
            patch("app.routers.images.sync.find_image_by_id", return_value=qcow2_image),
        ):
            resp = test_client.get(
                "/images/library/qcow2:iosv/stream",
                headers=auth_headers,
            )
        assert resp.status_code == 400
        assert "Docker" in resp.json()["detail"]

    def test_no_reference_rejected(
        self, test_client: TestClient, auth_headers: dict
    ):
        no_ref_image = {"id": "docker:empty", "kind": "docker", "reference": ""}
        with (
            patch("app.routers.images.sync.load_manifest", return_value=[no_ref_image]),
            patch("app.routers.images.sync.find_image_by_id", return_value=no_ref_image),
        ):
            resp = test_client.get(
                "/images/library/docker:empty/stream",
                headers=auth_headers,
            )
        assert resp.status_code == 400
        assert "reference" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# GET /images/sync-jobs
# ---------------------------------------------------------------------------

class TestListSyncJobs:
    def test_empty(self, test_client: TestClient, auth_headers: dict):
        resp = test_client.get("/images/sync-jobs", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_with_jobs(
        self,
        test_client: TestClient,
        test_db: Session,
        auth_headers: dict,
        sample_host: models.Host,
    ):
        _make_sync_job(test_db, sample_host.id, status="pending")
        _make_sync_job(test_db, sample_host.id, status="completed")

        resp = test_client.get("/images/sync-jobs", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    def test_filter_by_status(
        self,
        test_client: TestClient,
        test_db: Session,
        auth_headers: dict,
        sample_host: models.Host,
    ):
        _make_sync_job(test_db, sample_host.id, status="pending")
        _make_sync_job(test_db, sample_host.id, status="completed")

        resp = test_client.get(
            "/images/sync-jobs?status=pending", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["status"] == "pending"

    def test_filter_by_host_id(
        self,
        test_client: TestClient,
        test_db: Session,
        auth_headers: dict,
        sample_host: models.Host,
    ):
        _make_sync_job(test_db, sample_host.id)

        resp = test_client.get(
            f"/images/sync-jobs?host_id={sample_host.id}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1

    def test_limit(
        self,
        test_client: TestClient,
        test_db: Session,
        auth_headers: dict,
        sample_host: models.Host,
    ):
        for _ in range(5):
            _make_sync_job(test_db, sample_host.id)

        resp = test_client.get(
            "/images/sync-jobs?limit=2", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2


# ---------------------------------------------------------------------------
# GET /images/sync-jobs/{job_id}
# ---------------------------------------------------------------------------

class TestGetSyncJob:
    def test_not_found(self, test_client: TestClient, auth_headers: dict):
        resp = test_client.get("/images/sync-jobs/nonexistent", headers=auth_headers)
        assert resp.status_code == 404

    def test_found(
        self,
        test_client: TestClient,
        test_db: Session,
        auth_headers: dict,
        sample_host: models.Host,
    ):
        job = _make_sync_job(test_db, sample_host.id, job_id="test-job-sync")

        resp = test_client.get("/images/sync-jobs/test-job-sync", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "test-job-sync"
        assert data["host_name"] == sample_host.name


# ---------------------------------------------------------------------------
# DELETE /images/sync-jobs/{job_id}
# ---------------------------------------------------------------------------

class TestCancelSyncJob:
    def test_not_found(self, test_client: TestClient, admin_auth_headers: dict):
        resp = test_client.delete(
            "/images/sync-jobs/nonexistent", headers=admin_auth_headers
        )
        assert resp.status_code == 404

    def test_cancel_pending_job(
        self,
        test_client: TestClient,
        test_db: Session,
        admin_auth_headers: dict,
        sample_host: models.Host,
    ):
        job = _make_sync_job(
            test_db, sample_host.id, job_id="cancel-me", status="pending"
        )

        resp = test_client.delete(
            "/images/sync-jobs/cancel-me", headers=admin_auth_headers
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

        test_db.refresh(job)
        assert job.status == "cancelled"

    def test_cancel_completed_job_rejected(
        self,
        test_client: TestClient,
        test_db: Session,
        admin_auth_headers: dict,
        sample_host: models.Host,
    ):
        _make_sync_job(
            test_db, sample_host.id, job_id="done-job", status="completed"
        )

        resp = test_client.delete(
            "/images/sync-jobs/done-job", headers=admin_auth_headers
        )
        assert resp.status_code == 400
        assert "Cannot cancel" in resp.json()["detail"]

    def test_cancel_failed_job_rejected(
        self,
        test_client: TestClient,
        test_db: Session,
        admin_auth_headers: dict,
        sample_host: models.Host,
    ):
        _make_sync_job(
            test_db, sample_host.id, job_id="fail-job", status="failed"
        )

        resp = test_client.delete(
            "/images/sync-jobs/fail-job", headers=admin_auth_headers
        )
        assert resp.status_code == 400

    def test_cancel_updates_image_host(
        self,
        test_client: TestClient,
        test_db: Session,
        admin_auth_headers: dict,
        sample_host: models.Host,
    ):
        job = _make_sync_job(
            test_db, sample_host.id, job_id="cancel-ih", status="transferring"
        )
        # Create matching ImageHost record
        ih = models.ImageHost(
            id=str(uuid4()),
            image_id=job.image_id,
            host_id=sample_host.id,
            reference="ceos:4.28.0F",
            status="syncing",
        )
        test_db.add(ih)
        test_db.commit()

        resp = test_client.delete(
            "/images/sync-jobs/cancel-ih", headers=admin_auth_headers
        )
        assert resp.status_code == 200

        test_db.refresh(ih)
        assert ih.status == "unknown"

    def test_requires_admin(
        self,
        test_client: TestClient,
        test_db: Session,
        auth_headers: dict,  # regular user, not admin
        sample_host: models.Host,
    ):
        _make_sync_job(
            test_db, sample_host.id, job_id="no-admin", status="pending"
        )

        resp = test_client.delete(
            "/images/sync-jobs/no-admin", headers=auth_headers
        )
        # Should be 403 for non-admin
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /images/library/{image_id}/push
# ---------------------------------------------------------------------------

class TestPushImageToHosts:
    def test_image_not_found(
        self, test_client: TestClient, admin_auth_headers: dict
    ):
        with (
            patch("app.routers.images.sync.load_manifest", return_value=MOCK_MANIFEST),
            patch("app.routers.images.sync.find_image_by_id", return_value=None),
        ):
            resp = test_client.post(
                "/images/library/nonexistent/push",
                json={"host_ids": None},
                headers=admin_auth_headers,
            )
        assert resp.status_code == 404

    def test_no_online_hosts(
        self, test_client: TestClient, admin_auth_headers: dict
    ):
        with (
            patch("app.routers.images.sync.load_manifest", return_value=MOCK_MANIFEST),
            patch("app.routers.images.sync.find_image_by_id", side_effect=_mock_find_image),
        ):
            resp = test_client.post(
                "/images/library/docker:ceos:4.28.0F/push",
                json={"host_ids": None},
                headers=admin_auth_headers,
            )
        assert resp.status_code == 400
        assert "No online hosts" in resp.json()["detail"]

    def test_requires_admin(
        self, test_client: TestClient, auth_headers: dict
    ):
        resp = test_client.post(
            "/images/library/docker:ceos:4.28.0F/push",
            json={"host_ids": None},
            headers=auth_headers,
        )
        assert resp.status_code == 403
