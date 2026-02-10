"""Tests for images router endpoints."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models


@pytest.fixture
def mock_manifest(tmp_path):
    """Create a mock image manifest."""
    manifest_path = tmp_path / "manifest.json"
    manifest = {
        "version": 1,
        "images": [
            {
                "id": "docker:ceos:4.28.0",
                "kind": "docker",
                "reference": "ceos:4.28.0",
                "filename": "cEOS-lab-4.28.0.tar",
                "device_id": "eos",
                "version": "4.28.0",
                "is_default": True,
            },
            {
                "id": "qcow2:veos-4.29.qcow2",
                "kind": "qcow2",
                "reference": str(tmp_path / "veos-4.29.qcow2"),
                "filename": "veos-4.29.qcow2",
                "device_id": "eos",
                "version": "4.29",
            },
        ],
    }
    manifest_path.write_text(json.dumps(manifest))
    return manifest_path, manifest


class TestListImageLibrary:
    """Tests for GET /images/library endpoint."""

    def test_list_library_empty(
        self,
        test_client: TestClient,
        auth_headers: dict,
        tmp_path,
        monkeypatch,
    ):
        """Test listing empty library."""
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps({"version": 1, "images": []}))

        from app.routers import images as images_router

        monkeypatch.setattr(
            images_router, "load_manifest", lambda: {"version": 1, "images": []}
        )

        response = test_client.get("/images/library", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "images" in data
        assert data["images"] == []

    def test_list_library_with_images(
        self,
        test_client: TestClient,
        auth_headers: dict,
        mock_manifest,
        monkeypatch,
    ):
        """Test listing library with images."""
        _, manifest = mock_manifest

        from app.routers import images as images_router

        monkeypatch.setattr(images_router, "load_manifest", lambda: manifest)

        response = test_client.get("/images/library", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert len(data["images"]) == 2

    def test_list_library_unauthenticated(self, test_client: TestClient):
        """Test library access requires authentication."""
        response = test_client.get("/images/library")
        assert response.status_code == 401


class TestUpdateImageLibrary:
    """Tests for POST /images/library/{image_id} endpoint."""

    def test_update_image_metadata(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
        mock_manifest,
        monkeypatch,
    ):
        """Test updating image metadata."""
        manifest_path, manifest = mock_manifest

        from app.routers import images as images_router

        monkeypatch.setattr(images_router, "load_manifest", lambda: manifest.copy())
        monkeypatch.setattr(images_router, "save_manifest", lambda m: None)

        def mock_update(m, image_id, updates):
            for img in m["images"]:
                if img["id"] == image_id:
                    img.update(updates)
                    return img
            return None

        monkeypatch.setattr(images_router, "update_image_entry", mock_update)

        response = test_client.post(
            "/images/library/docker:ceos:4.28.0",
            json={"version": "4.28.1", "notes": "Updated version"},
            headers=admin_auth_headers,
        )
        assert response.status_code == 200

    def test_update_image_not_found(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
        mock_manifest,
        monkeypatch,
    ):
        """Test updating non-existent image."""
        _, manifest = mock_manifest

        from app.routers import images as images_router

        monkeypatch.setattr(images_router, "load_manifest", lambda: manifest.copy())
        monkeypatch.setattr(images_router, "save_manifest", lambda m: None)
        monkeypatch.setattr(images_router, "update_image_entry", lambda m, id, u: None)

        response = test_client.post(
            "/images/library/nonexistent-image",
            json={"version": "1.0.0"},
            headers=admin_auth_headers,
        )
        assert response.status_code == 404


class TestDeleteImage:
    """Tests for DELETE /images/library/{image_id} endpoint."""

    def test_delete_docker_image(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
        mock_manifest,
        monkeypatch,
    ):
        """Test deleting a Docker image."""
        _, manifest = mock_manifest

        from app.routers import images as images_router

        monkeypatch.setattr(images_router, "load_manifest", lambda: manifest.copy())
        monkeypatch.setattr(images_router, "save_manifest", lambda m: None)
        monkeypatch.setattr(
            images_router,
            "find_image_by_id",
            lambda m, id: {"id": id, "kind": "docker", "reference": "ceos:4.28.0"},
        )
        monkeypatch.setattr(images_router, "delete_image_entry", lambda m, id: True)

        response = test_client.delete(
            "/images/library/docker:ceos:4.28.0", headers=admin_auth_headers
        )
        assert response.status_code == 200
        assert "deleted" in response.json()["message"].lower()

    def test_delete_qcow2_removes_file(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
        tmp_path,
        monkeypatch,
    ):
        """Test deleting a qcow2 image also removes the file."""
        qcow2_file = tmp_path / "test.qcow2"
        qcow2_file.write_bytes(b"fake qcow2 content")

        manifest = {
            "images": [
                {
                    "id": "qcow2:test.qcow2",
                    "kind": "qcow2",
                    "reference": str(qcow2_file),
                }
            ]
        }

        from app.routers import images as images_router

        monkeypatch.setattr(images_router, "load_manifest", lambda: manifest.copy())
        monkeypatch.setattr(images_router, "save_manifest", lambda m: None)
        monkeypatch.setattr(
            images_router,
            "find_image_by_id",
            lambda m, id: {
                "id": id,
                "kind": "qcow2",
                "reference": str(qcow2_file),
            },
        )
        monkeypatch.setattr(images_router, "delete_image_entry", lambda m, id: True)

        response = test_client.delete(
            "/images/library/qcow2:test.qcow2", headers=admin_auth_headers
        )
        assert response.status_code == 200
        assert not qcow2_file.exists()

    def test_delete_image_not_found(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
        monkeypatch,
    ):
        """Test deleting non-existent image."""
        from app.routers import images as images_router

        monkeypatch.setattr(images_router, "load_manifest", lambda: {"images": []})
        monkeypatch.setattr(images_router, "find_image_by_id", lambda m, id: None)

        response = test_client.delete(
            "/images/library/nonexistent", headers=admin_auth_headers
        )
        assert response.status_code == 404


class TestAssignImage:
    """Tests for POST /images/library/{image_id}/assign endpoint."""

    def test_assign_image_to_device(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
        mock_manifest,
        monkeypatch,
    ):
        """Test assigning an image to a device type."""
        _, manifest = mock_manifest

        from app.routers import images as images_router

        monkeypatch.setattr(images_router, "load_manifest", lambda: manifest.copy())
        monkeypatch.setattr(images_router, "save_manifest", lambda m: None)

        def mock_update(m, image_id, updates):
            for img in m["images"]:
                if img["id"] == image_id:
                    img.update(updates)
                    return img
            return None

        monkeypatch.setattr(images_router, "update_image_entry", mock_update)

        response = test_client.post(
            "/images/library/docker:ceos:4.28.0/assign",
            json={"device_id": "ceos", "is_default": True},
            headers=admin_auth_headers,
        )
        assert response.status_code == 200

    def test_assign_image_requires_device_id(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
        monkeypatch,
    ):
        """Test assign endpoint requires device_id."""
        from app.routers import images as images_router

        monkeypatch.setattr(images_router, "load_manifest", lambda: {"images": []})

        response = test_client.post(
            "/images/library/docker:ceos:4.28.0/assign",
            json={},
            headers=admin_auth_headers,
        )
        assert response.status_code == 400
        assert "device_id" in response.json()["detail"].lower()


class TestUnassignImage:
    """Tests for POST /images/library/{image_id}/unassign endpoint."""

    def test_unassign_image(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
        mock_manifest,
        monkeypatch,
    ):
        """Test unassigning an image from device type."""
        _, manifest = mock_manifest

        from app.routers import images as images_router

        monkeypatch.setattr(images_router, "load_manifest", lambda: manifest.copy())
        monkeypatch.setattr(images_router, "save_manifest", lambda m: None)

        def mock_update(m, image_id, updates):
            for img in m["images"]:
                if img["id"] == image_id:
                    img.update(updates)
                    return img
            return None

        monkeypatch.setattr(images_router, "update_image_entry", mock_update)

        response = test_client.post(
            "/images/library/docker:ceos:4.28.0/unassign", headers=admin_auth_headers
        )
        assert response.status_code == 200


class TestGetImagesForDevice:
    """Tests for GET /images/devices/{device_id}/images endpoint."""

    def test_get_images_for_device(
        self,
        test_client: TestClient,
        auth_headers: dict,
        mock_manifest,
        monkeypatch,
    ):
        """Test getting images for a specific device type."""
        _, manifest = mock_manifest

        from app.routers import images as images_router

        monkeypatch.setattr(images_router, "load_manifest", lambda: manifest)

        response = test_client.get("/images/devices/eos/images", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "images" in data
        # Both images in mock are for eos
        assert len(data["images"]) == 2

    def test_get_images_normalizes_device_id(
        self,
        test_client: TestClient,
        auth_headers: dict,
        mock_manifest,
        monkeypatch,
    ):
        """Test device ID normalization (ceos -> eos)."""
        _, manifest = mock_manifest

        from app.routers import images as images_router

        monkeypatch.setattr(images_router, "load_manifest", lambda: manifest)

        # ceos should normalize to eos
        response = test_client.get("/images/devices/ceos/images", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert len(data["images"]) == 2


class TestListQcow2:
    """Tests for GET /images/qcow2 endpoint."""

    def test_list_qcow2_empty(
        self,
        test_client: TestClient,
        auth_headers: dict,
        tmp_path,
        monkeypatch,
    ):
        """Test listing qcow2 when none exist."""
        from app.routers import images as images_router

        monkeypatch.setattr(images_router, "ensure_image_store", lambda: tmp_path)

        response = test_client.get("/images/qcow2", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "files" in data
        assert data["files"] == []

    def test_list_qcow2_with_files(
        self,
        test_client: TestClient,
        auth_headers: dict,
        tmp_path,
        monkeypatch,
    ):
        """Test listing qcow2 files."""
        # Create some qcow2 files
        (tmp_path / "test1.qcow2").write_bytes(b"fake")
        (tmp_path / "test2.qcow2").write_bytes(b"fake")
        (tmp_path / "other.txt").write_bytes(b"not a qcow2")

        from app.routers import images as images_router

        monkeypatch.setattr(images_router, "ensure_image_store", lambda: tmp_path)

        response = test_client.get("/images/qcow2", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert len(data["files"]) == 2
        filenames = {f["filename"] for f in data["files"]}
        assert "test1.qcow2" in filenames
        assert "test2.qcow2" in filenames


class TestUploadProgress:
    """Tests for GET /images/load/{upload_id}/progress endpoint."""

    def test_upload_progress_not_found(
        self, test_client: TestClient, auth_headers: dict
    ):
        """Test getting progress for non-existent upload."""
        response = test_client.get(
            "/images/load/nonexistent-upload/progress", headers=auth_headers
        )
        assert response.status_code == 404


class TestImageHostsAndSync:
    """Tests for image synchronization endpoints."""

    def test_get_image_hosts(
        self,
        test_client: TestClient,
        test_db: Session,
        auth_headers: dict,
        sample_host: models.Host,
        monkeypatch,
    ):
        """Test getting host sync status for an image."""
        manifest = {
            "images": [
                {"id": "docker:test:1.0", "kind": "docker", "reference": "test:1.0"}
            ]
        }

        from app.routers import images as images_router

        monkeypatch.setattr(images_router, "load_manifest", lambda: manifest)
        monkeypatch.setattr(
            images_router,
            "find_image_by_id",
            lambda m, id: {"id": id, "kind": "docker"},
        )

        response = test_client.get(
            "/images/library/docker:test:1.0/hosts", headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert "image_id" in data
        assert "hosts" in data

    def test_get_image_hosts_not_found(
        self,
        test_client: TestClient,
        auth_headers: dict,
        monkeypatch,
    ):
        """Test getting hosts for non-existent image."""
        from app.routers import images as images_router

        monkeypatch.setattr(images_router, "load_manifest", lambda: {"images": []})
        monkeypatch.setattr(images_router, "find_image_by_id", lambda m, id: None)

        response = test_client.get(
            "/images/library/nonexistent/hosts", headers=auth_headers
        )
        assert response.status_code == 404

    def test_sync_image_only_docker(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
        monkeypatch,
    ):
        """Test that only Docker images can be synced."""
        manifest = {
            "images": [
                {"id": "qcow2:test.qcow2", "kind": "qcow2", "reference": "/path"}
            ]
        }

        from app.routers import images as images_router

        monkeypatch.setattr(images_router, "load_manifest", lambda: manifest)
        monkeypatch.setattr(
            images_router,
            "find_image_by_id",
            lambda m, id: {"id": id, "kind": "qcow2"},
        )

        response = test_client.post(
            "/images/library/qcow2:test.qcow2/push",
            json={},
            headers=admin_auth_headers,
        )
        assert response.status_code == 400
        assert "docker" in response.json()["detail"].lower()


class TestSyncJobs:
    """Tests for sync job listing endpoints."""

    def test_list_sync_jobs_empty(
        self,
        test_client: TestClient,
        auth_headers: dict,
    ):
        """Test listing sync jobs when none exist."""
        response = test_client.get("/images/sync-jobs", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data == []

    def test_list_sync_jobs_with_filters(
        self,
        test_client: TestClient,
        test_db: Session,
        auth_headers: dict,
        sample_host: models.Host,
    ):
        """Test listing sync jobs with filters."""
        # Create a sync job
        job = models.ImageSyncJob(
            id="sync-job-1",
            image_id="docker:test:1.0",
            host_id=sample_host.id,
            status="completed",
        )
        test_db.add(job)
        test_db.commit()

        response = test_client.get(
            "/images/sync-jobs",
            params={"status": "completed"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["status"] == "completed"

    def test_get_sync_job(
        self,
        test_client: TestClient,
        test_db: Session,
        auth_headers: dict,
        sample_host: models.Host,
    ):
        """Test getting a specific sync job."""
        job = models.ImageSyncJob(
            id="sync-job-2",
            image_id="docker:test:1.0",
            host_id=sample_host.id,
            status="pending",
        )
        test_db.add(job)
        test_db.commit()

        response = test_client.get(
            f"/images/sync-jobs/{job.id}", headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == job.id
        assert data["status"] == "pending"

    def test_get_sync_job_not_found(
        self, test_client: TestClient, auth_headers: dict
    ):
        """Test getting non-existent sync job."""
        response = test_client.get(
            "/images/sync-jobs/nonexistent", headers=auth_headers
        )
        assert response.status_code == 404

    def test_cancel_sync_job(
        self,
        test_client: TestClient,
        test_db: Session,
        admin_user: models.User,
        admin_auth_headers: dict,
        sample_host: models.Host,
    ):
        """Test cancelling a sync job."""
        job = models.ImageSyncJob(
            id="sync-job-3",
            image_id="docker:test:1.0",
            host_id=sample_host.id,
            status="pending",
        )
        test_db.add(job)
        test_db.commit()

        response = test_client.delete(
            f"/images/sync-jobs/{job.id}", headers=admin_auth_headers
        )
        assert response.status_code == 200
        assert response.json()["status"] == "cancelled"

        test_db.refresh(job)
        assert job.status == "cancelled"

    def test_cancel_completed_sync_job_fails(
        self,
        test_client: TestClient,
        test_db: Session,
        admin_user: models.User,
        admin_auth_headers: dict,
        sample_host: models.Host,
    ):
        """Test that completed sync jobs cannot be cancelled."""
        job = models.ImageSyncJob(
            id="sync-job-4",
            image_id="docker:test:1.0",
            host_id=sample_host.id,
            status="completed",
        )
        test_db.add(job)
        test_db.commit()

        response = test_client.delete(
            f"/images/sync-jobs/{job.id}", headers=admin_auth_headers
        )
        assert response.status_code == 400
        assert "cannot cancel" in response.json()["detail"].lower()
