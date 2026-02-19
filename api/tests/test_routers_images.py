"""Tests for images router endpoints."""
from __future__ import annotations

import json
from datetime import datetime, timezone

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


class TestChunkedImageUpload:
    """Tests for chunked upload endpoints used by docker/qcow2 flows."""

    def test_chunked_qcow2_upload_complete(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
        tmp_path,
        monkeypatch,
    ):
        """QCOW2 chunked upload should finalize and return completion result."""
        from app.routers import images as images_router

        images_router._chunk_upload_sessions.clear()
        images_router._upload_progress.clear()
        monkeypatch.setattr(images_router, "qcow2_path", lambda filename: tmp_path / filename)

        finalized: dict[str, object] = {}

        def fake_finalize(path, *, auto_build=True):
            finalized["path"] = str(path)
            finalized["auto_build"] = auto_build
            return {"path": str(path), "filename": path.name}

        monkeypatch.setattr(images_router, "_finalize_qcow2_upload", fake_finalize)

        init_response = test_client.post(
            "/images/upload/init",
            json={
                "kind": "qcow2",
                "filename": "vjunos-router-25.4R1.12.qcow2",
                "total_size": 5,
                "chunk_size": 5,
            },
            headers=admin_auth_headers,
        )
        assert init_response.status_code == 200
        upload_id = init_response.json()["upload_id"]

        chunk_response = test_client.post(
            f"/images/upload/{upload_id}/chunk",
            params={"index": 0},
            files={"chunk": ("chunk.bin", b"hello", "application/octet-stream")},
            headers=admin_auth_headers,
        )
        assert chunk_response.status_code == 200
        assert chunk_response.json()["progress_percent"] == 100

        complete_response = test_client.post(
            f"/images/upload/{upload_id}/complete",
            headers=admin_auth_headers,
        )
        assert complete_response.status_code == 200
        complete_data = complete_response.json()
        assert complete_data["status"] == "completed"
        assert complete_data["kind"] == "qcow2"
        assert complete_data["result"]["filename"] == "vjunos-router-25.4R1.12.qcow2"
        assert finalized["auto_build"] is True

        final_path = tmp_path / "vjunos-router-25.4R1.12.qcow2"
        assert final_path.exists()
        assert final_path.read_bytes() == b"hello"

    def test_chunked_docker_upload_starts_processing(
        self,
        test_client: TestClient,
        auth_headers: dict,
        admin_user: models.User,
        admin_auth_headers: dict,
        monkeypatch,
    ):
        """Docker chunked upload should trigger background processing using upload_id."""
        from app.routers import images as images_router

        images_router._chunk_upload_sessions.clear()
        images_router._upload_progress.clear()

        def fake_loader(upload_id: str, filename: str, archive_path: str, cleanup_archive: bool = True):
            images_router._update_progress(
                upload_id,
                "complete",
                "Image loaded successfully",
                100,
                images=["test:image"],
                complete=True,
            )
            with images_router._chunk_upload_lock:
                if upload_id in images_router._chunk_upload_sessions:
                    images_router._chunk_upload_sessions[upload_id]["status"] = "completed"

        class ImmediateThread:
            def __init__(self, target, args=(), daemon=None):
                self.target = target
                self.args = args

            def start(self):
                self.target(*self.args)

        monkeypatch.setattr(images_router, "_load_image_background_from_archive", fake_loader)
        monkeypatch.setattr(images_router.threading, "Thread", ImmediateThread)

        init_response = test_client.post(
            "/images/upload/init",
            json={
                "kind": "docker",
                "filename": "test-image.tar",
                "total_size": 4,
                "chunk_size": 4,
            },
            headers=admin_auth_headers,
        )
        assert init_response.status_code == 200
        upload_id = init_response.json()["upload_id"]

        chunk_response = test_client.post(
            f"/images/upload/{upload_id}/chunk",
            params={"index": 0},
            files={"chunk": ("chunk.bin", b"data", "application/octet-stream")},
            headers=admin_auth_headers,
        )
        assert chunk_response.status_code == 200

        complete_response = test_client.post(
            f"/images/upload/{upload_id}/complete",
            headers=admin_auth_headers,
        )
        assert complete_response.status_code == 200
        complete_data = complete_response.json()
        assert complete_data["status"] == "processing"
        assert complete_data["kind"] == "docker"
        assert complete_data["upload_id"] == upload_id

        progress_response = test_client.get(
            f"/images/load/{upload_id}/progress",
            headers=auth_headers,
        )
        assert progress_response.status_code == 200
        progress_data = progress_response.json()
        assert progress_data.get("complete") is True
        assert progress_data.get("images") == ["test:image"]


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

    def test_sync_non_docker_image_accepted(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
        monkeypatch,
    ):
        """Test that non-docker images can also be synced (all kinds supported)."""
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
        # No docker-only restriction; fails because no online hosts in test DB
        assert response.status_code == 400
        assert "no online hosts" in response.json()["detail"].lower()


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


class TestIolBuildManagement:
    """Tests for IOL build status and retry endpoints."""

    def test_get_iol_build_status(
        self,
        test_client: TestClient,
        auth_headers: dict,
        tmp_path,
        monkeypatch,
    ):
        """IOL build status should report built Docker image when present."""
        iol_file = tmp_path / "i86bi-linux-l3.bin"
        iol_file.write_bytes(b"fake-iol")
        manifest = {
            "images": [
                {
                    "id": "iol:i86bi-linux-l3.bin",
                    "kind": "iol",
                    "reference": str(iol_file),
                    "filename": iol_file.name,
                    "device_id": "iol-xe",
                    "build_status": "building",
                    "build_job_id": "rq-job-1",
                }
            ]
        }

        class _QueueStub:
            def fetch_job(self, _job_id):
                return None

        from app.routers import images as images_router
        from app.tasks import iol_build as iol_build_task

        monkeypatch.setattr(images_router, "load_manifest", lambda: manifest)
        monkeypatch.setattr(
            images_router,
            "find_image_by_id",
            lambda m, image_id: next((img for img in m["images"] if img["id"] == image_id), None),
        )
        monkeypatch.setattr(iol_build_task, "get_iol_build_status", lambda _image_id: {
            "built": True,
            "docker_image_id": "docker:archetype/iol-xe:15.9",
            "docker_reference": "archetype/iol-xe:15.9",
        })
        monkeypatch.setattr(images_router, "queue", _QueueStub())

        response = test_client.get(
            "/images/library/iol%3Ai86bi-linux-l3.bin/build-status",
            headers=auth_headers,
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["built"] is True
        assert payload["status"] == "complete"
        assert payload["docker_reference"] == "archetype/iol-xe:15.9"

    def test_get_iol_build_status_prefers_active_queue_state(
        self,
        test_client: TestClient,
        auth_headers: dict,
        tmp_path,
        monkeypatch,
    ):
        """Queue state should override stale manifest status for active builds."""
        iol_file = tmp_path / "i86bi-linux-l2.bin"
        iol_file.write_bytes(b"fake-iol")
        manifest = {
            "images": [
                {
                    "id": "iol:i86bi-linux-l2.bin",
                    "kind": "iol",
                    "reference": str(iol_file),
                    "filename": iol_file.name,
                    "device_id": "iol-l2",
                    "build_status": "failed",
                    "build_error": "old failure",
                    "build_job_id": "rq-job-2",
                }
            ]
        }

        class _RQJobStub:
            def get_status(self, refresh=True):  # noqa: ARG002
                return "started"

        class _QueueStub:
            def fetch_job(self, _job_id):
                return _RQJobStub()

        from app.routers import images as images_router
        from app.tasks import iol_build as iol_build_task

        monkeypatch.setattr(images_router, "load_manifest", lambda: manifest)
        monkeypatch.setattr(
            images_router,
            "find_image_by_id",
            lambda m, image_id: next((img for img in m["images"] if img["id"] == image_id), None),
        )
        monkeypatch.setattr(iol_build_task, "get_iol_build_status", lambda _image_id: None)
        monkeypatch.setattr(images_router, "queue", _QueueStub())

        response = test_client.get(
            "/images/library/iol%3Ai86bi-linux-l2.bin/build-status",
            headers=auth_headers,
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["built"] is False
        assert payload["status"] == "building"

    def test_retry_iol_build_enqueues_job(
        self,
        test_client: TestClient,
        admin_auth_headers: dict,
        tmp_path,
        monkeypatch,
    ):
        """Retry endpoint should enqueue a new IOL build and persist metadata."""
        iol_file = tmp_path / "i86bi-linux-l3.bin"
        iol_file.write_bytes(b"fake-iol")
        manifest = {
            "images": [
                {
                    "id": "iol:i86bi-linux-l3.bin",
                    "kind": "iol",
                    "reference": str(iol_file),
                    "filename": iol_file.name,
                    "device_id": "iol-xe",
                }
            ]
        }
        enqueue_calls: list[dict] = []

        class _QueuedJob:
            id = "rq-build-123"

        class _QueueStub:
            def fetch_job(self, _job_id):
                return None

            def enqueue(self, _func, **kwargs):
                enqueue_calls.append(kwargs)
                return _QueuedJob()

        from app.routers import images as images_router

        monkeypatch.setattr(images_router, "load_manifest", lambda: manifest)
        monkeypatch.setattr(images_router, "save_manifest", lambda _manifest: None)
        monkeypatch.setattr(
            images_router,
            "find_image_by_id",
            lambda m, image_id: next((img for img in m["images"] if img["id"] == image_id), None),
        )
        monkeypatch.setattr(images_router, "queue", _QueueStub())

        response = test_client.post(
            "/images/library/iol%3Ai86bi-linux-l3.bin/retry-build",
            headers=admin_auth_headers,
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["build_job_id"] == "rq-build-123"
        assert payload["build_status"] == "queued"
        assert enqueue_calls
        assert enqueue_calls[0]["iol_image_id"] == "iol:i86bi-linux-l3.bin"
        assert manifest["images"][0]["build_job_id"] == "rq-build-123"

    def test_retry_iol_build_rejects_active_job(
        self,
        test_client: TestClient,
        admin_auth_headers: dict,
        tmp_path,
        monkeypatch,
    ):
        """Retry should fail if an IOL build is already queued/running."""
        iol_file = tmp_path / "i86bi-linux-l3.bin"
        iol_file.write_bytes(b"fake-iol")
        manifest = {
            "images": [
                {
                    "id": "iol:i86bi-linux-l3.bin",
                    "kind": "iol",
                    "reference": str(iol_file),
                    "filename": iol_file.name,
                    "device_id": "iol-xe",
                    "build_job_id": "rq-build-active",
                }
            ]
        }

        class _RQJobStub:
            def get_status(self, refresh=True):  # noqa: ARG002
                return "queued"

        class _QueueStub:
            def fetch_job(self, _job_id):
                return _RQJobStub()

        from app.routers import images as images_router

        monkeypatch.setattr(images_router, "load_manifest", lambda: manifest)
        monkeypatch.setattr(
            images_router,
            "find_image_by_id",
            lambda m, image_id: next((img for img in m["images"] if img["id"] == image_id), None),
        )
        monkeypatch.setattr(images_router, "queue", _QueueStub())

        response = test_client.post(
            "/images/library/iol%3Ai86bi-linux-l3.bin/retry-build",
            headers=admin_auth_headers,
        )
        assert response.status_code == 409

    def test_ignore_iol_build_failure_marks_ignored(
        self,
        test_client: TestClient,
        admin_auth_headers: dict,
        tmp_path,
        monkeypatch,
    ):
        """Users can mark failed IOL builds as ignored from the UI."""
        iol_file = tmp_path / "i86bi-linux-l3.bin"
        iol_file.write_bytes(b"fake-iol")
        manifest = {
            "images": [
                {
                    "id": "iol:i86bi-linux-l3.bin",
                    "kind": "iol",
                    "reference": str(iol_file),
                    "filename": iol_file.name,
                    "device_id": "iol-xe",
                    "build_status": "failed",
                    "build_error": "build failed",
                    "build_job_id": "rq-build-failed",
                }
            ]
        }

        class _RQJobStub:
            def get_status(self, refresh=True):  # noqa: ARG002
                return "failed"

        class _QueueStub:
            def fetch_job(self, _job_id):
                return _RQJobStub()

        from app.routers import images as images_router

        monkeypatch.setattr(images_router, "load_manifest", lambda: manifest)
        monkeypatch.setattr(images_router, "save_manifest", lambda _manifest: None)
        monkeypatch.setattr(
            images_router,
            "find_image_by_id",
            lambda m, image_id: next((img for img in m["images"] if img["id"] == image_id), None),
        )
        monkeypatch.setattr(images_router, "queue", _QueueStub())

        response = test_client.post(
            "/images/library/iol%3Ai86bi-linux-l3.bin/ignore-build-failure",
            headers=admin_auth_headers,
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["build_status"] == "ignored"
        assert manifest["images"][0]["build_status"] == "ignored"
        assert manifest["images"][0]["build_ignored_at"]
        assert manifest["images"][0]["build_ignored_by"]

    def test_get_iol_build_status_keeps_ignored_state(
        self,
        test_client: TestClient,
        auth_headers: dict,
        tmp_path,
        monkeypatch,
    ):
        """Ignored build status should not be overwritten by stale failed queue status."""
        iol_file = tmp_path / "i86bi-linux-l3.bin"
        iol_file.write_bytes(b"fake-iol")
        manifest = {
            "images": [
                {
                    "id": "iol:i86bi-linux-l3.bin",
                    "kind": "iol",
                    "reference": str(iol_file),
                    "filename": iol_file.name,
                    "device_id": "iol-xe",
                    "build_status": "ignored",
                    "build_job_id": "rq-build-failed",
                }
            ]
        }

        class _RQJobStub:
            exc_info = "Traceback: failed"

            def get_status(self, refresh=True):  # noqa: ARG002
                return "failed"

        class _QueueStub:
            def fetch_job(self, _job_id):
                return _RQJobStub()

        from app.routers import images as images_router
        from app.tasks import iol_build as iol_build_task

        monkeypatch.setattr(images_router, "load_manifest", lambda: manifest)
        monkeypatch.setattr(
            images_router,
            "find_image_by_id",
            lambda m, image_id: next((img for img in m["images"] if img["id"] == image_id), None),
        )
        monkeypatch.setattr(iol_build_task, "get_iol_build_status", lambda _image_id: None)
        monkeypatch.setattr(images_router, "queue", _QueueStub())

        response = test_client.get(
            "/images/library/iol%3Ai86bi-linux-l3.bin/build-status",
            headers=auth_headers,
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "ignored"
        assert "failed" in payload["build_error"]

    def test_get_iol_build_diagnostics_includes_queue_job_data(
        self,
        test_client: TestClient,
        auth_headers: dict,
        tmp_path,
        monkeypatch,
    ):
        """Diagnostics endpoint should expose queue metadata and traceback tail."""
        iol_file = tmp_path / "i86bi-linux-l3.bin"
        iol_file.write_bytes(b"fake-iol")
        manifest = {
            "images": [
                {
                    "id": "iol:i86bi-linux-l3.bin",
                    "kind": "iol",
                    "reference": str(iol_file),
                    "filename": iol_file.name,
                    "device_id": "iol-xe",
                    "build_status": "failed",
                    "build_error": "build failed",
                    "build_job_id": "rq-build-failed",
                }
            ]
        }
        now = datetime.now(timezone.utc)

        class _RQJobStub:
            created_at = now
            enqueued_at = now
            started_at = now
            ended_at = now
            last_heartbeat = now
            result = {"success": False}
            exc_info = "Traceback (most recent call last):\nValueError: invalid ELF header"

            def get_status(self, refresh=True):  # noqa: ARG002
                return "failed"

        class _QueueStub:
            def fetch_job(self, _job_id):
                return _RQJobStub()

        from app.routers import images as images_router
        from app.tasks import iol_build as iol_build_task

        monkeypatch.setattr(images_router, "load_manifest", lambda: manifest)
        monkeypatch.setattr(
            images_router,
            "find_image_by_id",
            lambda m, image_id: next((img for img in m["images"] if img["id"] == image_id), None),
        )
        monkeypatch.setattr(iol_build_task, "get_iol_build_status", lambda _image_id: None)
        monkeypatch.setattr(images_router, "queue", _QueueStub())

        response = test_client.get(
            "/images/library/iol%3Ai86bi-linux-l3.bin/build-diagnostics",
            headers=auth_headers,
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "failed"
        assert payload["queue_job"]["id"] == "rq-build-failed"
        assert payload["queue_job"]["status"] == "failed"
        assert "invalid ELF header" in payload["queue_job"]["error_log"]
