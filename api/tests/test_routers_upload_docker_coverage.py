"""Coverage tests for app.routers.images.upload_docker — helpers, endpoints, error paths."""
from __future__ import annotations

import io
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.routers import images as img
from app.config import settings
from app.routers.images._shared import (
    _chunk_upload_lock,
    _chunk_upload_sessions,
    _get_progress,
    _update_progress,
)
from app.routers.images.upload_docker import (
    _archive_docker_image,
    _load_image_background,
    _load_image_background_from_archive,
    _run_docker_with_progress,
)
from app.services.resource_monitor import PressureLevel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tar_bytes(names: list[str]) -> bytes:
    """Create a tar archive in memory with the given filenames."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name in names:
            data = b"x"
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _make_tar_file(path: Path, names: list[str]):
    """Create a tar file on disk with the given filenames."""
    with tarfile.open(path, "w") as tf:
        for name in names:
            data = b"x"
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))


@pytest.fixture(autouse=True)
def _clear_chunk_state(monkeypatch):
    """Clear chunk upload sessions before and after each test."""
    monkeypatch.setattr(settings, "image_archive_docker_images", False)
    with _chunk_upload_lock:
        _chunk_upload_sessions.clear()
    yield
    with _chunk_upload_lock:
        _chunk_upload_sessions.clear()


# ---------------------------------------------------------------------------
# 1. _run_docker_with_progress — unit tests
# ---------------------------------------------------------------------------

class TestRunDockerWithProgress:
    """Tests for the _run_docker_with_progress helper."""

    def test_success_captures_output_and_callbacks(self):
        """Successful subprocess returns stdout/stderr and fires callbacks."""
        messages = []
        rc, stdout, stderr = _run_docker_with_progress(
            ["echo", "Loaded image: test:latest"],
            progress_callback=messages.append,
            operation_name="test load",
        )
        assert rc == 0
        assert "Loaded image: test:latest" in stdout
        assert "Starting test load..." in messages
        assert any("Loaded image" in m for m in messages)

    def test_nonzero_return_code(self):
        """Non-zero exit code is captured in return value."""
        rc, stdout, stderr = _run_docker_with_progress(
            ["sh", "-c", "echo 'err' >&2; exit 1"],
            progress_callback=lambda _: None,
            operation_name="fail",
        )
        assert rc == 1
        assert "err" in stderr


# ---------------------------------------------------------------------------
# 2. get_upload_progress endpoint — via test_client
# ---------------------------------------------------------------------------

class TestGetUploadProgress:
    """Tests for GET /images/load/{upload_id}/progress."""

    def test_progress_not_found(self, test_client, auth_headers):
        resp = test_client.get("/images/load/nonexistent/progress", headers=auth_headers)
        assert resp.status_code == 404
        assert "Upload not found" in resp.json()["detail"]

    def test_progress_found(self, test_client, auth_headers):
        _update_progress("prog-1", "loading", "Running docker load...", 60)
        resp = test_client.get("/images/load/prog-1/progress", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["phase"] == "loading"
        assert data["percent"] == 60


# ---------------------------------------------------------------------------
# 3. load_image endpoint — disk pressure, background, admin guard
# ---------------------------------------------------------------------------

class TestLoadImageEndpoint:
    """Tests for POST /images/load."""

    def test_disk_pressure_returns_507(self, test_client, admin_auth_headers, monkeypatch):
        monkeypatch.setattr(
            "app.routers.images.upload_docker.ResourceMonitor.check_disk_pressure",
            lambda: PressureLevel.CRITICAL,
        )
        tar_data = _make_tar_bytes(["manifest.json"])
        resp = test_client.post(
            "/images/load",
            headers=admin_auth_headers,
            files={"file": ("image.tar", io.BytesIO(tar_data), "application/x-tar")},
        )
        assert resp.status_code == 507

    def test_background_mode_returns_upload_id(self, test_client, admin_auth_headers, monkeypatch):
        monkeypatch.setattr(
            "app.routers.images.upload_docker.ResourceMonitor.check_disk_pressure",
            lambda: PressureLevel.NORMAL,
        )
        monkeypatch.setattr(img.threading, "Thread", lambda **kw: MagicMock(start=lambda: None))
        tar_data = _make_tar_bytes(["rootfs/bin"])
        resp = test_client.post(
            "/images/load?background=true",
            headers=admin_auth_headers,
            files={"file": ("image.tar", io.BytesIO(tar_data), "application/x-tar")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "upload_id" in data
        assert data["status"] == "started"

    def test_requires_admin(self, test_client, auth_headers):
        """Regular user cannot upload images."""
        tar_data = _make_tar_bytes(["rootfs/bin"])
        resp = test_client.post(
            "/images/load",
            headers=auth_headers,
            files={"file": ("image.tar", io.BytesIO(tar_data), "application/x-tar")},
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 4. init_chunk_upload endpoint
# ---------------------------------------------------------------------------

class TestInitChunkUpload:
    """Tests for POST /images/upload/init."""

    def test_invalid_kind_rejected(self, test_client, admin_auth_headers, monkeypatch):
        monkeypatch.setattr(
            "app.routers.images.upload_docker.ResourceMonitor.check_disk_pressure",
            lambda: PressureLevel.NORMAL,
        )
        resp = test_client.post(
            "/images/upload/init",
            headers=admin_auth_headers,
            json={"kind": "invalid", "filename": "test.tar", "total_size": 1024},
        )
        assert resp.status_code == 400

    def test_empty_filename_rejected(self, test_client, admin_auth_headers, monkeypatch):
        monkeypatch.setattr(
            "app.routers.images.upload_docker.ResourceMonitor.check_disk_pressure",
            lambda: PressureLevel.NORMAL,
        )
        resp = test_client.post(
            "/images/upload/init",
            headers=admin_auth_headers,
            json={"kind": "docker", "filename": "???", "total_size": 1024},
        )
        assert resp.status_code == 400
        assert "Invalid filename" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 5. complete_chunk_upload endpoint — error conditions
# ---------------------------------------------------------------------------

class TestCompleteChunkUpload:
    """Tests for POST /images/upload/{upload_id}/complete."""

    def test_session_not_found(self, test_client, admin_auth_headers):
        resp = test_client.post("/images/upload/no-such-id/complete", headers=admin_auth_headers)
        assert resp.status_code == 404

    def test_session_not_uploading(self, test_client, admin_auth_headers):
        with _chunk_upload_lock:
            _chunk_upload_sessions["done-sess"] = {
                "upload_id": "done-sess",
                "status": "completed",
                "kind": "docker",
                "filename": "test.tar",
                "total_size": 100,
                "chunk_size": 100,
                "total_chunks": 1,
                "bytes_received": 100,
                "chunks_received": [0],
                "temp_path": "/tmp/fake",
                "final_path": "/tmp/fake-final",
                "error_message": None,
                "created_at": datetime.now(timezone.utc),
            }
        resp = test_client.post("/images/upload/done-sess/complete", headers=admin_auth_headers)
        assert resp.status_code == 400
        assert "completed" in resp.json()["detail"]

    def test_missing_chunks_rejected(self, test_client, admin_auth_headers, tmp_path):
        temp = tmp_path / "partial.bin"
        temp.write_bytes(b"\0" * 300)
        with _chunk_upload_lock:
            _chunk_upload_sessions["partial-sess"] = {
                "upload_id": "partial-sess",
                "status": "uploading",
                "kind": "docker",
                "filename": "test.tar",
                "total_size": 300,
                "chunk_size": 100,
                "total_chunks": 3,
                "bytes_received": 100,
                "chunks_received": [0],
                "temp_path": str(temp),
                "final_path": str(tmp_path / "final.tar"),
                "error_message": None,
                "created_at": datetime.now(timezone.utc),
            }
        resp = test_client.post("/images/upload/partial-sess/complete", headers=admin_auth_headers)
        assert resp.status_code == 400
        assert "Missing chunks" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 6. cancel and status endpoints
# ---------------------------------------------------------------------------

class TestCancelAndStatus:
    """Tests for DELETE /images/upload/{id} and GET /images/upload/{id}."""

    def test_cancel_not_found(self, test_client, admin_auth_headers):
        resp = test_client.delete("/images/upload/missing-id", headers=admin_auth_headers)
        assert resp.status_code == 404

    def test_cancel_success(self, test_client, admin_auth_headers, tmp_path):
        temp = tmp_path / "upload.partial"
        temp.write_bytes(b"\0" * 100)
        with _chunk_upload_lock:
            _chunk_upload_sessions["cancel-me"] = {
                "upload_id": "cancel-me",
                "status": "uploading",
                "kind": "docker",
                "filename": "test.tar",
                "total_size": 100,
                "chunk_size": 100,
                "total_chunks": 1,
                "bytes_received": 0,
                "chunks_received": [],
                "temp_path": str(temp),
                "final_path": str(tmp_path / "final.tar"),
                "error_message": None,
                "created_at": datetime.now(timezone.utc),
            }
        resp = test_client.delete("/images/upload/cancel-me", headers=admin_auth_headers)
        assert resp.status_code == 200
        assert "cancelled" in resp.json()["message"].lower()
        with _chunk_upload_lock:
            assert "cancel-me" not in _chunk_upload_sessions

    def test_status_returns_session_data(self, test_client, auth_headers):
        created = datetime.now(timezone.utc)
        with _chunk_upload_lock:
            _chunk_upload_sessions["status-sess"] = {
                "upload_id": "status-sess",
                "status": "uploading",
                "kind": "docker",
                "filename": "ceos.tar",
                "total_size": 500,
                "chunk_size": 100,
                "total_chunks": 5,
                "bytes_received": 200,
                "chunks_received": [0, 1],
                "temp_path": "/tmp/x",
                "final_path": "/tmp/y",
                "error_message": None,
                "created_at": created,
            }
        resp = test_client.get("/images/upload/status-sess", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["upload_id"] == "status-sess"
        assert data["progress_percent"] == 40


# ---------------------------------------------------------------------------
# 7. _load_image_background — unit tests (mocked subprocess)
# ---------------------------------------------------------------------------

class TestLoadImageBackground:
    """Unit tests for _load_image_background helper."""

    def test_docker_load_success(self, tmp_path, monkeypatch):
        """Successful docker load updates progress to complete."""
        tar_path = tmp_path / "docker.tar"
        _make_tar_file(tar_path, ["manifest.json"])
        content = tar_path.read_bytes()

        fake_result = MagicMock(
            returncode=0,
            stdout="Loaded image: ceos:4.28.0F\n",
            stderr="",
        )
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_result)
        monkeypatch.setattr(
            "app.routers.images.upload_docker._is_docker_image_tar", lambda p: True,
        )
        monkeypatch.setattr(
            "app.routers.images.upload_docker.load_manifest", lambda: {"images": []},
        )
        monkeypatch.setattr(
            "app.routers.images.upload_docker.find_image_by_id", lambda m, i: None,
        )
        monkeypatch.setattr(
            "app.routers.images.upload_docker.detect_device_from_filename",
            lambda r: ("ceos", "4.28.0F"),
        )
        monkeypatch.setattr(
            "app.routers.images.upload_docker.create_image_entry",
            lambda **kw: {"id": kw["image_id"]},
        )
        monkeypatch.setattr(
            "app.routers.images.upload_docker.save_manifest", lambda m: None,
        )

        _load_image_background("bg-1", "docker.tar", content)

        progress = _get_progress("bg-1")
        assert progress is not None
        assert progress["phase"] == "complete"
        assert progress["percent"] == 100

    def test_docker_load_timeout_sets_error(self, tmp_path, monkeypatch):
        """Subprocess timeout sets error progress."""
        tar_path = tmp_path / "docker.tar"
        _make_tar_file(tar_path, ["manifest.json"])
        content = tar_path.read_bytes()

        monkeypatch.setattr(
            "app.routers.images.upload_docker._is_docker_image_tar", lambda p: True,
        )
        monkeypatch.setattr(
            subprocess, "run",
            MagicMock(side_effect=subprocess.TimeoutExpired(cmd="docker", timeout=600)),
        )

        _load_image_background("bg-timeout", "docker.tar", content)

        progress = _get_progress("bg-timeout")
        assert progress is not None
        assert progress["phase"] == "error"
        assert "timed out" in progress["message"]


# ---------------------------------------------------------------------------
# 8. _load_image_background_from_archive — unit tests
# ---------------------------------------------------------------------------

class TestLoadImageBackgroundFromArchive:
    """Unit tests for _load_image_background_from_archive."""

    def test_missing_archive_sets_error(self, tmp_path):
        """Non-existent archive path sets error progress and session status."""
        with _chunk_upload_lock:
            _chunk_upload_sessions["arc-miss"] = {"status": "processing"}

        _load_image_background_from_archive(
            "arc-miss", "missing.tar", str(tmp_path / "nonexistent.tar")
        )

        progress = _get_progress("arc-miss")
        assert progress is not None
        assert progress["phase"] == "error"
        assert "no longer exists" in progress["message"]
        with _chunk_upload_lock:
            assert _chunk_upload_sessions["arc-miss"]["status"] == "failed"

    def test_docker_load_failure_sets_error(self, tmp_path, monkeypatch):
        """Failed docker load sets error in both progress and session."""
        tar_path = tmp_path / "bad.tar"
        _make_tar_file(tar_path, ["manifest.json"])

        with _chunk_upload_lock:
            _chunk_upload_sessions["arc-fail"] = {"status": "processing"}

        monkeypatch.setattr(
            "app.routers.images.upload_docker._is_docker_image_tar", lambda p: True,
        )
        fake_result = MagicMock(returncode=1, stdout="", stderr="Error: invalid tar header")
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_result)

        _load_image_background_from_archive("arc-fail", "bad.tar", str(tar_path))

        progress = _get_progress("arc-fail")
        assert progress["phase"] == "error"
        with _chunk_upload_lock:
            assert _chunk_upload_sessions["arc-fail"]["status"] == "failed"

    def test_success_queues_archive_creation(self, tmp_path, monkeypatch):
        """Successful archive processing queues Docker archive creation."""
        tar_path = tmp_path / "good.tar"
        _make_tar_file(tar_path, ["manifest.json"])

        queued: list[list[str]] = []
        with _chunk_upload_lock:
            _chunk_upload_sessions["arc-ok"] = {"status": "processing"}

        monkeypatch.setattr(
            "app.routers.images.upload_docker._is_docker_image_tar", lambda p: True,
        )
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: MagicMock(returncode=0, stdout="Loaded image: ceos:4.28.0F\n", stderr=""),
        )
        monkeypatch.setattr(
            "app.routers.images.upload_docker.load_manifest", lambda: {"images": []},
        )
        monkeypatch.setattr(
            "app.routers.images.upload_docker.find_image_by_id", lambda m, i: None,
        )
        monkeypatch.setattr(
            "app.routers.images.upload_docker.detect_device_from_filename",
            lambda r: ("ceos", "4.28.0F"),
        )
        monkeypatch.setattr(
            "app.routers.images.upload_docker.create_image_entry",
            lambda **kw: {"id": kw["image_id"]},
        )
        monkeypatch.setattr(
            "app.routers.images.upload_docker.save_manifest", lambda m: None,
        )
        monkeypatch.setattr(
            "app.routers.images.upload_docker._queue_docker_archive_creation",
            lambda refs: queued.append(list(refs)),
        )

        _load_image_background_from_archive("arc-ok", "good.tar", str(tar_path), cleanup_archive=False)

        assert queued == [["ceos:4.28.0F"]]


class TestArchiveDockerImage:
    """Unit tests for Docker archive creation helper."""

    def test_archive_creation_success_persists_ready_metadata(self, tmp_path, monkeypatch):
        archive_target = tmp_path / "archives" / "docker_ceos_4_28_0F.tar"
        updates: list[dict[str, object]] = []

        def fake_run(cmd, **kwargs):
            Path(cmd[3]).write_bytes(b"docker-archive")
            return MagicMock(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(
            "app.routers.images.upload_docker.docker_archive_path",
            lambda image_id: archive_target,
        )
        monkeypatch.setattr(
            "app.routers.images.upload_docker._persist_docker_archive_metadata",
            lambda image_id, payload: updates.append(dict(payload)),
        )
        monkeypatch.setattr(
            "app.routers.images.upload_docker.compute_sha256",
            lambda path: "sha256-ready",
        )
        monkeypatch.setattr(subprocess, "run", fake_run)

        _archive_docker_image("docker:ceos:4.28.0F", "ceos:4.28.0F")

        assert archive_target.exists()
        assert [item["archive_status"] for item in updates] == ["pending", "ready"]
        assert updates[-1]["archive_sha256"] == "sha256-ready"
        assert updates[-1]["archive_size_bytes"] == len(b"docker-archive")

    def test_archive_creation_failure_persists_failed_metadata(self, tmp_path, monkeypatch):
        archive_target = tmp_path / "archives" / "docker_ceos_fail.tar"
        updates: list[dict[str, object]] = []

        monkeypatch.setattr(
            "app.routers.images.upload_docker.docker_archive_path",
            lambda image_id: archive_target,
        )
        monkeypatch.setattr(
            "app.routers.images.upload_docker._persist_docker_archive_metadata",
            lambda image_id, payload: updates.append(dict(payload)),
        )
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: MagicMock(returncode=1, stdout="", stderr="docker save failed"),
        )

        _archive_docker_image("docker:ceos:fail", "ceos:fail")

        assert not archive_target.exists()
        assert [item["archive_status"] for item in updates] == ["pending", "failed"]
        assert updates[-1]["archive_error"] == "docker save failed"
