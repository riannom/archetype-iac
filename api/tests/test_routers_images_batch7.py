"""Batch 7: Images router gap-fill tests.

Covers 9 untested endpoints in api/app/routers/images.py:
- load_image (POST /images/load)
- upload_qcow2 (POST /images/qcow2)
- upload_iol (POST /images/iol)
- get_chunk_upload_status (GET /images/upload/{id})
- cancel_chunk_upload (DELETE /images/upload/{id})
- confirm_qcow2_upload (POST /images/upload/{id}/confirm)
- stream_image (GET /images/library/{id}/stream)
- trigger_docker_build (POST /images/library/{id}/build-docker)
- backfill_checksums (POST /images/backfill-checksums)
"""
from __future__ import annotations

import io
import json
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient



# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tar_bytes(filename: str = "test.txt", content: bytes = b"hello") -> bytes:
    """Create a minimal tar archive in memory."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        info = tarfile.TarInfo(name=filename)
        info.size = len(content)
        tf.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def _make_docker_tar_bytes() -> bytes:
    """Create a tar that looks like a Docker image (has manifest.json)."""
    buf = io.BytesIO()
    manifest = json.dumps([{"Config": "config.json", "Layers": []}]).encode()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        info = tarfile.TarInfo(name="manifest.json")
        info.size = len(manifest)
        tf.addfile(info, io.BytesIO(manifest))
    return buf.getvalue()


# ---------------------------------------------------------------------------
# POST /images/load
# ---------------------------------------------------------------------------


class TestLoadImage:
    """Tests for POST /images/load."""

    def test_load_requires_admin(self, test_client: TestClient, auth_headers: dict):
        """Regular users cannot load images."""
        data = _make_tar_bytes()
        resp = test_client.post(
            "/images/load",
            files={"file": ("test.tar", io.BytesIO(data), "application/x-tar")},
            headers=auth_headers,
        )
        assert resp.status_code == 403

    @patch("app.routers.images.ResourceMonitor")
    def test_load_disk_pressure_critical(
        self, mock_rm, test_client: TestClient, admin_auth_headers: dict,
    ):
        """Reject upload when disk space is critically low."""
        from app.services.resource_monitor import PressureLevel
        mock_rm.check_disk_pressure.return_value = PressureLevel.CRITICAL

        data = _make_tar_bytes()
        resp = test_client.post(
            "/images/load",
            files={"file": ("test.tar", io.BytesIO(data), "application/x-tar")},
            headers=admin_auth_headers,
        )
        assert resp.status_code == 507

    @patch("app.routers.images.ResourceMonitor")
    @patch("app.routers.images._load_image_sync")
    def test_load_sync_mode(
        self, mock_load, mock_rm, test_client: TestClient, admin_auth_headers: dict,
    ):
        """Synchronous load returns result directly."""
        from app.services.resource_monitor import PressureLevel
        mock_rm.check_disk_pressure.return_value = PressureLevel.NORMAL
        mock_load.return_value = {"status": "loaded", "images": ["test:latest"]}

        data = _make_tar_bytes()
        resp = test_client.post(
            "/images/load",
            files={"file": ("test.tar", io.BytesIO(data), "application/x-tar")},
            headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        mock_load.assert_called_once()

    @patch("app.routers.images.ResourceMonitor")
    def test_load_background_mode(
        self, mock_rm, test_client: TestClient, admin_auth_headers: dict,
    ):
        """Background mode returns upload_id immediately."""
        from app.services.resource_monitor import PressureLevel
        mock_rm.check_disk_pressure.return_value = PressureLevel.NORMAL

        data = _make_tar_bytes()
        with patch("app.routers.images._load_image_background"):
            resp = test_client.post(
                "/images/load?background=true",
                files={"file": ("test.tar", io.BytesIO(data), "application/x-tar")},
                headers=admin_auth_headers,
            )
        assert resp.status_code == 200
        result = resp.json()
        assert "upload_id" in result
        assert result["status"] == "started"


# ---------------------------------------------------------------------------
# POST /images/qcow2
# ---------------------------------------------------------------------------


class TestUploadQcow2:
    """Tests for POST /images/qcow2."""

    def test_upload_requires_admin(self, test_client: TestClient, auth_headers: dict):
        resp = test_client.post(
            "/images/qcow2",
            files={"file": ("test.qcow2", io.BytesIO(b"\x00" * 64), "application/octet-stream")},
            headers=auth_headers,
        )
        assert resp.status_code == 403

    @patch("app.routers.images.ResourceMonitor")
    def test_upload_missing_filename(
        self, mock_rm, test_client: TestClient, admin_auth_headers: dict,
    ):
        from app.services.resource_monitor import PressureLevel
        mock_rm.check_disk_pressure.return_value = PressureLevel.NORMAL

        resp = test_client.post(
            "/images/qcow2",
            files={"file": ("", io.BytesIO(b"\x00"), "application/octet-stream")},
            headers=admin_auth_headers,
        )
        assert resp.status_code == 400

    @patch("app.routers.images.ResourceMonitor")
    def test_upload_wrong_extension(
        self, mock_rm, test_client: TestClient, admin_auth_headers: dict,
    ):
        from app.services.resource_monitor import PressureLevel
        mock_rm.check_disk_pressure.return_value = PressureLevel.NORMAL

        resp = test_client.post(
            "/images/qcow2",
            files={"file": ("test.iso", io.BytesIO(b"\x00"), "application/octet-stream")},
            headers=admin_auth_headers,
        )
        assert resp.status_code == 400

    @patch("app.routers.images.ResourceMonitor")
    @patch("app.routers.images._finalize_qcow2_upload")
    @patch("app.routers.images.qcow2_path")
    def test_upload_qcow2_success(
        self, mock_path, mock_finalize, mock_rm,
        test_client: TestClient, admin_auth_headers: dict, tmp_path: Path,
    ):
        from app.services.resource_monitor import PressureLevel
        mock_rm.check_disk_pressure.return_value = PressureLevel.NORMAL

        dest = tmp_path / "csr1000v-universalk9.17.03.06.qcow2"
        mock_path.return_value = dest
        mock_finalize.return_value = {"device_id": "csr1000v", "status": "registered"}

        resp = test_client.post(
            "/images/qcow2",
            files={"file": ("csr1000v-universalk9.17.03.06.qcow2", io.BytesIO(b"\x00" * 64), "application/octet-stream")},
            headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        mock_finalize.assert_called_once()

    @patch("app.routers.images.ResourceMonitor")
    @patch("app.routers.images._finalize_qcow2_upload")
    @patch("app.routers.images.qcow2_path")
    def test_upload_qcow2_gz_success(
        self, mock_path, mock_finalize, mock_rm,
        test_client: TestClient, admin_auth_headers: dict, tmp_path: Path,
    ):
        from app.services.resource_monitor import PressureLevel
        mock_rm.check_disk_pressure.return_value = PressureLevel.NORMAL

        dest = tmp_path / "sonic-vs.img.gz"
        mock_path.return_value = dest
        mock_finalize.return_value = {"device_id": "sonic-vs", "status": "registered"}

        resp = test_client.post(
            "/images/qcow2",
            files={"file": ("sonic-vs.img.gz", io.BytesIO(b"\x00" * 64), "application/octet-stream")},
            headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        mock_finalize.assert_called_once()


# ---------------------------------------------------------------------------
# POST /images/iol
# ---------------------------------------------------------------------------


class TestUploadIol:
    """Tests for POST /images/iol."""

    def test_upload_iol_requires_admin(self, test_client: TestClient, auth_headers: dict):
        resp = test_client.post(
            "/images/iol",
            files={"file": ("i86bi_linux_l3-adventerprisek9-ms.160.bin", io.BytesIO(b"\x7fELF"), "application/octet-stream")},
            headers=auth_headers,
        )
        assert resp.status_code == 403

    @patch("app.routers.images.ResourceMonitor")
    def test_upload_iol_missing_filename(
        self, mock_rm, test_client: TestClient, admin_auth_headers: dict,
    ):
        from app.services.resource_monitor import PressureLevel
        mock_rm.check_disk_pressure.return_value = PressureLevel.NORMAL

        resp = test_client.post(
            "/images/iol",
            files={"file": ("", io.BytesIO(b"\x7f"), "application/octet-stream")},
            headers=admin_auth_headers,
        )
        assert resp.status_code == 400

    @patch("app.routers.images.ResourceMonitor")
    def test_upload_iol_unrecognized_device(
        self, mock_rm, test_client: TestClient, admin_auth_headers: dict,
    ):
        from app.services.resource_monitor import PressureLevel
        mock_rm.check_disk_pressure.return_value = PressureLevel.NORMAL

        resp = test_client.post(
            "/images/iol",
            files={"file": ("random_binary.bin", io.BytesIO(b"\x7f"), "application/octet-stream")},
            headers=admin_auth_headers,
        )
        assert resp.status_code == 400
        assert "Could not detect" in resp.json()["detail"]

    @patch("app.routers.images.ResourceMonitor")
    @patch("app.routers.images.find_image_by_id", return_value={"id": "existing"})
    @patch("app.routers.images.load_manifest", return_value={"images": []})
    def test_upload_iol_duplicate(
        self, mock_manifest, mock_find, mock_rm,
        test_client: TestClient, admin_auth_headers: dict,
    ):
        from app.services.resource_monitor import PressureLevel
        mock_rm.check_disk_pressure.return_value = PressureLevel.NORMAL

        resp = test_client.post(
            "/images/iol",
            files={"file": ("i86bi_linux_l3-adventerprisek9-ms.160.bin", io.BytesIO(b"\x7f"), "application/octet-stream")},
            headers=admin_auth_headers,
        )
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# GET /images/upload/{upload_id}
# ---------------------------------------------------------------------------


class TestGetChunkUploadStatus:
    """Tests for GET /images/upload/{upload_id}."""

    def test_status_not_found(self, test_client: TestClient, auth_headers: dict):
        resp = test_client.get("/images/upload/nonexistent", headers=auth_headers)
        assert resp.status_code == 404

    def test_status_returns_session(self, test_client: TestClient, auth_headers: dict):
        from app.routers.images import _chunk_upload_sessions, _chunk_upload_lock

        session_data = {
            "upload_id": "test123",
            "kind": "qcow2",
            "filename": "test.qcow2",
            "total_size": 1024,
            "bytes_received": 512,
            "chunks_received": [0],
            "status": "uploading",
            "error_message": None,
            "created_at": datetime.now(timezone.utc),
        }
        with _chunk_upload_lock:
            _chunk_upload_sessions["test123"] = session_data
        try:
            resp = test_client.get("/images/upload/test123", headers=auth_headers)
            assert resp.status_code == 200
            data = resp.json()
            assert data["upload_id"] == "test123"
            assert data["progress_percent"] == 50
        finally:
            with _chunk_upload_lock:
                _chunk_upload_sessions.pop("test123", None)


# ---------------------------------------------------------------------------
# DELETE /images/upload/{upload_id}
# ---------------------------------------------------------------------------


class TestCancelChunkUpload:
    """Tests for DELETE /images/upload/{upload_id}."""

    def test_cancel_not_found(self, test_client: TestClient, admin_auth_headers: dict):
        resp = test_client.delete("/images/upload/nonexistent", headers=admin_auth_headers)
        assert resp.status_code == 404

    def test_cancel_success(self, test_client: TestClient, admin_auth_headers: dict, tmp_path: Path):
        from app.routers.images import _chunk_upload_sessions, _chunk_upload_lock

        temp_file = tmp_path / ".upload_cancel123.partial"
        temp_file.write_bytes(b"\x00" * 64)

        session_data = {
            "upload_id": "cancel123",
            "kind": "qcow2",
            "filename": "test.qcow2",
            "total_size": 1024,
            "bytes_received": 0,
            "chunks_received": [],
            "status": "uploading",
            "temp_path": str(temp_file),
            "final_path": str(tmp_path / "test.qcow2"),
        }
        with _chunk_upload_lock:
            _chunk_upload_sessions["cancel123"] = session_data

        resp = test_client.delete("/images/upload/cancel123", headers=admin_auth_headers)
        assert resp.status_code == 200
        assert "cancelled" in resp.json()["message"].lower()

        # Session should be removed
        with _chunk_upload_lock:
            assert "cancel123" not in _chunk_upload_sessions


# ---------------------------------------------------------------------------
# POST /images/upload/{upload_id}/confirm
# ---------------------------------------------------------------------------


class TestConfirmQcow2Upload:
    """Tests for POST /images/upload/{upload_id}/confirm."""

    def test_confirm_not_found(self, test_client: TestClient, admin_auth_headers: dict):
        resp = test_client.post(
            "/images/upload/nonexistent/confirm",
            json={},
            headers=admin_auth_headers,
        )
        assert resp.status_code == 404

    def test_confirm_wrong_status(self, test_client: TestClient, admin_auth_headers: dict):
        from app.routers.images import _chunk_upload_sessions, _chunk_upload_lock

        session_data = {
            "upload_id": "conf123",
            "kind": "qcow2",
            "status": "uploading",  # Not awaiting_confirmation
        }
        with _chunk_upload_lock:
            _chunk_upload_sessions["conf123"] = session_data
        try:
            resp = test_client.post(
                "/images/upload/conf123/confirm",
                json={},
                headers=admin_auth_headers,
            )
            assert resp.status_code == 400
        finally:
            with _chunk_upload_lock:
                _chunk_upload_sessions.pop("conf123", None)


# ---------------------------------------------------------------------------
# GET /images/library/{image_id}/stream
# ---------------------------------------------------------------------------


class TestStreamImage:
    """Tests for GET /images/library/{image_id}/stream."""

    @patch("app.routers.images.load_manifest", return_value={"images": []})
    @patch("app.routers.images.find_image_by_id", return_value=None)
    def test_stream_image_not_found(
        self, mock_find, mock_manifest, test_client: TestClient, auth_headers: dict,
    ):
        resp = test_client.get("/images/library/nonexistent/stream", headers=auth_headers)
        assert resp.status_code == 404

    @patch("app.routers.images.find_image_by_id")
    @patch("app.routers.images.load_manifest", return_value={"images": []})
    def test_stream_non_docker_image(
        self, mock_manifest, mock_find, test_client: TestClient, auth_headers: dict,
    ):
        mock_find.return_value = {"id": "qcow2:test", "kind": "qcow2", "reference": "/path"}
        resp = test_client.get("/images/library/qcow2%3Atest/stream", headers=auth_headers)
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    @patch("app.routers.images.find_image_by_id")
    @patch("app.routers.images.load_manifest", return_value={"images": []})
    def test_stream_missing_reference(
        self, mock_manifest, mock_find, test_client: TestClient, auth_headers: dict,
    ):
        mock_find.return_value = {"id": "docker:test", "kind": "docker", "reference": ""}
        resp = test_client.get("/images/library/docker%3Atest/stream", headers=auth_headers)
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /images/library/{image_id}/build-docker
# ---------------------------------------------------------------------------


class TestTriggerDockerBuild:
    """Tests for POST /images/library/{image_id}/build-docker."""

    @patch("app.routers.images.find_image_by_id", return_value=None)
    @patch("app.routers.images.load_manifest", return_value={"images": []})
    def test_build_image_not_found(
        self, mock_manifest, mock_find, test_client: TestClient, admin_auth_headers: dict,
    ):
        resp = test_client.post(
            "/images/library/nonexistent/build-docker",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 404

    @patch("app.routers.images.find_image_by_id")
    @patch("app.routers.images.load_manifest", return_value={"images": []})
    def test_build_non_qcow2_rejected(
        self, mock_manifest, mock_find, test_client: TestClient, admin_auth_headers: dict,
    ):
        mock_find.return_value = {"id": "docker:test", "kind": "docker", "reference": "test:latest"}
        resp = test_client.post(
            "/images/library/docker%3Atest/build-docker",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 400
        assert "qcow2" in resp.json()["detail"].lower()

    @patch("app.routers.images.find_image_by_id")
    @patch("app.routers.images.load_manifest", return_value={"images": []})
    def test_build_file_not_on_disk(
        self, mock_manifest, mock_find, test_client: TestClient, admin_auth_headers: dict,
    ):
        mock_find.return_value = {
            "id": "qcow2:test.qcow2",
            "kind": "qcow2",
            "reference": "/nonexistent/path.qcow2",
        }
        resp = test_client.post(
            "/images/library/qcow2%3Atest.qcow2/build-docker",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 400
        assert "not found" in resp.json()["detail"].lower()

    @patch("app.routers.images.detect_qcow2_device_type", return_value=(None, None))
    @patch("app.routers.images.find_image_by_id")
    @patch("app.routers.images.load_manifest", return_value={"images": []})
    def test_build_unrecognized_device(
        self, mock_manifest, mock_find, mock_detect,
        test_client: TestClient, admin_auth_headers: dict, tmp_path: Path,
    ):
        qcow2_file = tmp_path / "unknown.qcow2"
        qcow2_file.write_bytes(b"\x00" * 64)
        mock_find.return_value = {
            "id": "qcow2:unknown.qcow2",
            "kind": "qcow2",
            "reference": str(qcow2_file),
            "filename": "unknown.qcow2",
        }
        resp = test_client.post(
            "/images/library/qcow2%3Aunknown.qcow2/build-docker",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 400
        assert "not recognized" in resp.json()["detail"].lower()

    @patch("app.routers.images.get_queue")
    @patch("app.routers.images.detect_qcow2_device_type", return_value=("csr1000v", "vrnetlab/csr"))
    @patch("app.routers.images.find_image_by_id")
    @patch("app.routers.images.load_manifest", return_value={"images": []})
    def test_build_success(
        self, mock_manifest, mock_find, mock_detect, mock_queue,
        test_client: TestClient, admin_auth_headers: dict, tmp_path: Path,
    ):
        qcow2_file = tmp_path / "csr1000v.qcow2"
        qcow2_file.write_bytes(b"\x00" * 64)
        mock_find.return_value = {
            "id": "qcow2:csr1000v.qcow2",
            "kind": "qcow2",
            "reference": str(qcow2_file),
            "filename": "csr1000v.qcow2",
            "device_id": "csr1000v",
            "version": "17.03.06",
        }
        mock_job = MagicMock()
        mock_job.id = "rq-job-123"
        mock_queue.return_value.enqueue.return_value = mock_job

        resp = test_client.post(
            "/images/library/qcow2%3Acsr1000v.qcow2/build-docker",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["job_id"] == "rq-job-123"
        assert data["status"] == "queued"


# ---------------------------------------------------------------------------
# POST /images/backfill-checksums
# ---------------------------------------------------------------------------


class TestBackfillChecksums:
    """Tests for POST /images/backfill-checksums."""

    @patch("app.routers.images.save_manifest")
    @patch("app.routers.images.load_manifest")
    def test_backfill_skips_already_checksummed(
        self, mock_manifest, mock_save,
        test_client: TestClient, admin_auth_headers: dict,
    ):
        mock_manifest.return_value = {
            "images": [
                {"id": "qcow2:a", "kind": "qcow2", "sha256": "abc123", "reference": "/path"},
            ]
        }
        resp = test_client.post("/images/backfill-checksums", headers=admin_auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["updated"] == 0
        mock_save.assert_not_called()

    @patch("app.routers.images.save_manifest")
    @patch("app.routers.images.load_manifest")
    def test_backfill_skips_non_qcow2(
        self, mock_manifest, mock_save,
        test_client: TestClient, admin_auth_headers: dict,
    ):
        mock_manifest.return_value = {
            "images": [
                {"id": "docker:test", "kind": "docker", "reference": "test:latest"},
            ]
        }
        resp = test_client.post("/images/backfill-checksums", headers=admin_auth_headers)
        assert resp.status_code == 200
        assert resp.json()["updated"] == 0

    @patch("app.routers.images.save_manifest")
    @patch("app.routers.images.load_manifest")
    def test_backfill_reports_missing_files(
        self, mock_manifest, mock_save,
        test_client: TestClient, admin_auth_headers: dict,
    ):
        mock_manifest.return_value = {
            "images": [
                {"id": "qcow2:missing", "kind": "qcow2", "reference": "/nonexistent/file.qcow2"},
            ]
        }
        resp = test_client.post("/images/backfill-checksums", headers=admin_auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["updated"] == 0
        assert len(data["errors"]) == 1
        assert "not found" in data["errors"][0].lower()

    @patch("app.routers.images.save_manifest")
    @patch("app.utils.image_integrity.compute_sha256", return_value="deadbeef1234")
    @patch("app.routers.images.load_manifest")
    def test_backfill_computes_checksum(
        self, mock_manifest, mock_sha, mock_save,
        test_client: TestClient, admin_auth_headers: dict, tmp_path: Path,
    ):
        qcow2_file = tmp_path / "test.qcow2"
        qcow2_file.write_bytes(b"\x00" * 64)

        mock_manifest.return_value = {
            "images": [
                {"id": "qcow2:test", "kind": "qcow2", "reference": str(qcow2_file)},
            ]
        }
        resp = test_client.post("/images/backfill-checksums", headers=admin_auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["updated"] == 1
        mock_save.assert_called_once()

    def test_backfill_requires_admin(self, test_client: TestClient, auth_headers: dict):
        resp = test_client.post("/images/backfill-checksums", headers=auth_headers)
        assert resp.status_code == 403
