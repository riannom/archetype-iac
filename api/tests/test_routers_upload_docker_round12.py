"""Round 12 deep-path coverage for app.routers.images.upload_docker.

Targets: archive error handling, chunk cancellation edge cases,
upload validation, and background processing branches not covered
in the existing test_routers_upload_docker_coverage.py.
"""
from __future__ import annotations

import io
import lzma
import os
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.routers.images._shared import (
    _chunk_upload_lock,
    _chunk_upload_sessions,
    _get_progress,
    _update_progress,
)
from app.routers.images.upload_docker import (
    _load_image_background,
    _load_image_background_from_archive,
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
def _clear_chunk_state():
    """Clear chunk upload sessions before and after each test."""
    with _chunk_upload_lock:
        _chunk_upload_sessions.clear()
    yield
    with _chunk_upload_lock:
        _chunk_upload_sessions.clear()


# ---------------------------------------------------------------------------
# 1. _load_image_background — XZ decompression failure (LZMAError)
# ---------------------------------------------------------------------------

class TestLoadImageBackgroundXzFailure:
    """XZ decompression errors set progress to error phase."""

    def test_lzma_error_sets_error_progress(self, tmp_path, monkeypatch):
        """Invalid XZ content triggers LZMAError and sets error progress."""
        # Create a file pretending to be .tar.xz but with invalid content
        bad_xz = tmp_path / "bad.tar.xz"
        bad_xz.write_bytes(b"not-valid-xz-data")
        content = bad_xz.read_bytes()

        # _is_docker_image_tar should not be reached, but mock it anyway
        monkeypatch.setattr(
            "app.routers.images.upload_docker._is_docker_image_tar", lambda p: True,
        )

        _load_image_background("xz-fail-1", "bad.tar.xz", content)

        progress = _get_progress("xz-fail-1")
        assert progress is not None
        assert progress["phase"] == "error"
        assert "decompression" in progress["message"].lower() or "lzma" in progress["message"].lower()


# ---------------------------------------------------------------------------
# 2. _load_image_background — non-docker tar (docker import) success
# ---------------------------------------------------------------------------

class TestLoadImageBackgroundDockerImport:
    """Tests for the docker-import branch (raw filesystem tar)."""

    def test_docker_import_success(self, tmp_path, monkeypatch):
        """Successful docker import for raw filesystem tar updates manifest."""
        tar_path = tmp_path / "ceos-lab.tar"
        # No manifest.json → not a docker image → import path
        _make_tar_file(tar_path, ["rootfs/bin/sh"])
        content = tar_path.read_bytes()

        monkeypatch.setattr(
            "app.routers.images.upload_docker._is_docker_image_tar", lambda p: False,
        )
        # Fake subprocess.run for docker import — use file-based capture mock
        fake_result = MagicMock(returncode=0)
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_result)

        # Mock the temp file read to return image ID output
        _orig_open = open

        class _FakeFile:
            def __init__(self, content="sha256:abc123def456"):
                self._content = content

            def read(self):
                return self._content

            def write(self, data):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        # We need to handle the complex file-based stdout/stderr capture.
        # Rather than mock open, mock at the subprocess level and
        # also mock the manifest functions.
        monkeypatch.setattr(
            "app.routers.images.upload_docker.load_manifest", lambda: {"images": []},
        )
        monkeypatch.setattr(
            "app.routers.images.upload_docker.find_image_by_id", lambda m, i: None,
        )
        monkeypatch.setattr(
            "app.routers.images.upload_docker.detect_device_from_filename",
            lambda r: ("ceos", "imported"),
        )
        monkeypatch.setattr(
            "app.routers.images.upload_docker.create_image_entry",
            lambda **kw: {"id": kw["image_id"]},
        )
        monkeypatch.setattr(
            "app.routers.images.upload_docker.save_manifest", lambda m: None,
        )

        _load_image_background("import-ok-1", "ceos-lab.tar", content)

        progress = _get_progress("import-ok-1")
        assert progress is not None
        assert progress["phase"] == "complete"
        assert progress["percent"] == 100

    def test_docker_import_timeout(self, tmp_path, monkeypatch):
        """docker import timeout sets error progress."""
        tar_path = tmp_path / "big.tar"
        _make_tar_file(tar_path, ["rootfs/bin/sh"])
        content = tar_path.read_bytes()

        monkeypatch.setattr(
            "app.routers.images.upload_docker._is_docker_image_tar", lambda p: False,
        )
        monkeypatch.setattr(
            subprocess, "run",
            MagicMock(side_effect=subprocess.TimeoutExpired(cmd="docker", timeout=600)),
        )

        _load_image_background("import-timeout-1", "big.tar", content)

        progress = _get_progress("import-timeout-1")
        assert progress is not None
        assert progress["phase"] == "error"
        assert "timed out" in progress["message"]

    def test_docker_import_nonzero_exit(self, tmp_path, monkeypatch):
        """docker import non-zero exit sets error progress."""
        tar_path = tmp_path / "bad.tar"
        _make_tar_file(tar_path, ["rootfs/bin/sh"])
        content = tar_path.read_bytes()

        monkeypatch.setattr(
            "app.routers.images.upload_docker._is_docker_image_tar", lambda p: False,
        )
        fake_result = MagicMock(returncode=1)
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_result)

        _load_image_background("import-fail-1", "bad.tar", content)

        progress = _get_progress("import-fail-1")
        assert progress is not None
        assert progress["phase"] == "error"


# ---------------------------------------------------------------------------
# 3. _load_image_background — no images detected
# ---------------------------------------------------------------------------

class TestLoadImageBackgroundNoImages:
    """Docker load succeeds but no 'Loaded image:' lines in output."""

    def test_no_images_detected_sets_error(self, tmp_path, monkeypatch):
        tar_path = tmp_path / "empty.tar"
        _make_tar_file(tar_path, ["manifest.json"])
        content = tar_path.read_bytes()

        monkeypatch.setattr(
            "app.routers.images.upload_docker._is_docker_image_tar", lambda p: True,
        )
        # docker load succeeds but outputs nothing recognizable
        fake_result = MagicMock(returncode=0, stdout="Nothing happened\n", stderr="")
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_result)

        _load_image_background("no-img-1", "empty.tar", content)

        progress = _get_progress("no-img-1")
        assert progress is not None
        assert progress["phase"] == "error"
        assert "no images" in progress["message"].lower()


# ---------------------------------------------------------------------------
# 4. _load_image_background — duplicate image in manifest
# ---------------------------------------------------------------------------

class TestLoadImageBackgroundDuplicate:
    """Image already exists in manifest after docker load."""

    def test_duplicate_image_sets_error(self, tmp_path, monkeypatch):
        tar_path = tmp_path / "dup.tar"
        _make_tar_file(tar_path, ["manifest.json"])
        content = tar_path.read_bytes()

        monkeypatch.setattr(
            "app.routers.images.upload_docker._is_docker_image_tar", lambda p: True,
        )
        fake_result = MagicMock(
            returncode=0, stdout="Loaded image: ceos:4.28\n", stderr=""
        )
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_result)
        monkeypatch.setattr(
            "app.routers.images.upload_docker.load_manifest",
            lambda: {"images": [{"id": "docker:ceos:4.28"}]},
        )
        # find_image_by_id returns existing entry → duplicate
        monkeypatch.setattr(
            "app.routers.images.upload_docker.find_image_by_id",
            lambda m, i: {"id": i},
        )

        _load_image_background("dup-1", "dup.tar", content)

        progress = _get_progress("dup-1")
        assert progress is not None
        assert progress["phase"] == "error"
        assert "already exists" in progress["message"]


# ---------------------------------------------------------------------------
# 5. _load_image_background_from_archive — XZ decompression failure
# ---------------------------------------------------------------------------

class TestArchiveXzDecompressionFailure:
    """LZMAError during XZ decompression in _load_image_background_from_archive."""

    def test_xz_decompression_failure_sets_session_failed(self, tmp_path):
        """Invalid XZ data sets both progress error and session status."""
        bad_xz = tmp_path / "bad.tar.xz"
        bad_xz.write_bytes(b"not-valid-xz")

        with _chunk_upload_lock:
            _chunk_upload_sessions["arc-xz-fail"] = {"status": "processing"}

        _load_image_background_from_archive(
            "arc-xz-fail", "bad.tar.xz", str(bad_xz)
        )

        progress = _get_progress("arc-xz-fail")
        assert progress is not None
        assert progress["phase"] == "error"

        with _chunk_upload_lock:
            assert _chunk_upload_sessions["arc-xz-fail"]["status"] == "failed"


# ---------------------------------------------------------------------------
# 6. _load_image_background_from_archive — docker load timeout
# ---------------------------------------------------------------------------

class TestArchiveDockerLoadTimeout:
    """docker load timeout in _load_image_background_from_archive."""

    def test_timeout_sets_session_failed(self, tmp_path, monkeypatch):
        tar_path = tmp_path / "timeout.tar"
        _make_tar_file(tar_path, ["manifest.json"])

        with _chunk_upload_lock:
            _chunk_upload_sessions["arc-timeout"] = {"status": "processing"}

        monkeypatch.setattr(
            "app.routers.images.upload_docker._is_docker_image_tar", lambda p: True,
        )
        monkeypatch.setattr(
            subprocess, "run",
            MagicMock(side_effect=subprocess.TimeoutExpired(cmd="docker", timeout=600)),
        )

        _load_image_background_from_archive(
            "arc-timeout", "timeout.tar", str(tar_path)
        )

        progress = _get_progress("arc-timeout")
        assert progress["phase"] == "error"
        assert "timed out" in progress["message"]

        with _chunk_upload_lock:
            assert _chunk_upload_sessions["arc-timeout"]["status"] == "failed"


# ---------------------------------------------------------------------------
# 7. _load_image_background_from_archive — docker import (non-docker tar)
# ---------------------------------------------------------------------------

class TestArchiveDockerImportPath:
    """docker import branch in _load_image_background_from_archive."""

    def test_import_success_updates_manifest_and_completes(self, tmp_path, monkeypatch):
        tar_path = tmp_path / "rootfs.tar"
        _make_tar_file(tar_path, ["rootfs/etc/hosts"])

        with _chunk_upload_lock:
            _chunk_upload_sessions["arc-import-ok"] = {"status": "processing"}

        monkeypatch.setattr(
            "app.routers.images.upload_docker._is_docker_image_tar", lambda p: False,
        )
        fake_result = MagicMock(returncode=0)
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_result)
        monkeypatch.setattr(
            "app.routers.images.upload_docker.load_manifest", lambda: {"images": []},
        )
        monkeypatch.setattr(
            "app.routers.images.upload_docker.find_image_by_id", lambda m, i: None,
        )
        monkeypatch.setattr(
            "app.routers.images.upload_docker.detect_device_from_filename",
            lambda r: ("linux", "imported"),
        )
        monkeypatch.setattr(
            "app.routers.images.upload_docker.create_image_entry",
            lambda **kw: {"id": kw["image_id"]},
        )
        monkeypatch.setattr(
            "app.routers.images.upload_docker.save_manifest", lambda m: None,
        )

        _load_image_background_from_archive(
            "arc-import-ok", "rootfs.tar", str(tar_path), cleanup_archive=False
        )

        progress = _get_progress("arc-import-ok")
        assert progress is not None
        assert progress["phase"] == "complete"
        assert progress["percent"] == 100

        with _chunk_upload_lock:
            assert _chunk_upload_sessions["arc-import-ok"]["status"] == "completed"

    def test_import_timeout_sets_session_failed(self, tmp_path, monkeypatch):
        tar_path = tmp_path / "rootfs.tar"
        _make_tar_file(tar_path, ["rootfs/etc/hosts"])

        with _chunk_upload_lock:
            _chunk_upload_sessions["arc-import-to"] = {"status": "processing"}

        monkeypatch.setattr(
            "app.routers.images.upload_docker._is_docker_image_tar", lambda p: False,
        )
        monkeypatch.setattr(
            subprocess, "run",
            MagicMock(side_effect=subprocess.TimeoutExpired(cmd="docker", timeout=600)),
        )

        _load_image_background_from_archive(
            "arc-import-to", "rootfs.tar", str(tar_path), cleanup_archive=False
        )

        progress = _get_progress("arc-import-to")
        assert progress["phase"] == "error"
        assert "timed out" in progress["message"]

        with _chunk_upload_lock:
            assert _chunk_upload_sessions["arc-import-to"]["status"] == "failed"

    def test_import_nonzero_exit_sets_session_failed(self, tmp_path, monkeypatch):
        tar_path = tmp_path / "rootfs.tar"
        _make_tar_file(tar_path, ["rootfs/etc/hosts"])

        with _chunk_upload_lock:
            _chunk_upload_sessions["arc-import-nz"] = {"status": "processing"}

        monkeypatch.setattr(
            "app.routers.images.upload_docker._is_docker_image_tar", lambda p: False,
        )
        fake_result = MagicMock(returncode=1)
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_result)

        _load_image_background_from_archive(
            "arc-import-nz", "rootfs.tar", str(tar_path), cleanup_archive=False
        )

        progress = _get_progress("arc-import-nz")
        assert progress["phase"] == "error"

        with _chunk_upload_lock:
            assert _chunk_upload_sessions["arc-import-nz"]["status"] == "failed"


# ---------------------------------------------------------------------------
# 8. _load_image_background_from_archive — no images + duplicate
# ---------------------------------------------------------------------------

class TestArchiveNoImagesAndDuplicate:
    """Edge cases: no images detected, duplicate image in manifest."""

    def test_no_images_detected(self, tmp_path, monkeypatch):
        tar_path = tmp_path / "empty.tar"
        _make_tar_file(tar_path, ["manifest.json"])

        with _chunk_upload_lock:
            _chunk_upload_sessions["arc-no-img"] = {"status": "processing"}

        monkeypatch.setattr(
            "app.routers.images.upload_docker._is_docker_image_tar", lambda p: True,
        )
        # docker load succeeds but no Loaded image lines
        fake_result = MagicMock(returncode=0, stdout="OK\n", stderr="")
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_result)

        _load_image_background_from_archive(
            "arc-no-img", "empty.tar", str(tar_path), cleanup_archive=False
        )

        progress = _get_progress("arc-no-img")
        assert progress["phase"] == "error"
        assert "no images" in progress["message"].lower()

        with _chunk_upload_lock:
            assert _chunk_upload_sessions["arc-no-img"]["status"] == "failed"

    def test_duplicate_image_in_manifest(self, tmp_path, monkeypatch):
        tar_path = tmp_path / "dup.tar"
        _make_tar_file(tar_path, ["manifest.json"])

        with _chunk_upload_lock:
            _chunk_upload_sessions["arc-dup"] = {"status": "processing"}

        monkeypatch.setattr(
            "app.routers.images.upload_docker._is_docker_image_tar", lambda p: True,
        )
        fake_result = MagicMock(
            returncode=0, stdout="Loaded image: srlinux:latest\n", stderr=""
        )
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_result)
        monkeypatch.setattr(
            "app.routers.images.upload_docker.load_manifest",
            lambda: {"images": [{"id": "docker:srlinux:latest"}]},
        )
        monkeypatch.setattr(
            "app.routers.images.upload_docker.find_image_by_id",
            lambda m, i: {"id": i},
        )

        _load_image_background_from_archive(
            "arc-dup", "dup.tar", str(tar_path), cleanup_archive=False
        )

        progress = _get_progress("arc-dup")
        assert progress["phase"] == "error"
        assert "already exists" in progress["message"]

        with _chunk_upload_lock:
            assert _chunk_upload_sessions["arc-dup"]["status"] == "failed"


# ---------------------------------------------------------------------------
# 9. upload_chunk endpoint — validation errors
# ---------------------------------------------------------------------------

class TestUploadChunkValidation:
    """Tests for POST /images/upload/{id}/chunk validation paths."""

    def test_chunk_session_not_found(self, test_client, admin_auth_headers):
        resp = test_client.post(
            "/images/upload/nonexistent/chunk?index=0",
            headers=admin_auth_headers,
            files={"chunk": ("chunk.bin", io.BytesIO(b"\0" * 100), "application/octet-stream")},
        )
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_chunk_session_not_uploading(self, test_client, admin_auth_headers):
        """Cannot upload chunk when session is not in 'uploading' state."""
        with _chunk_upload_lock:
            _chunk_upload_sessions["done-chunk"] = {
                "upload_id": "done-chunk",
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
        resp = test_client.post(
            "/images/upload/done-chunk/chunk?index=0",
            headers=admin_auth_headers,
            files={"chunk": ("chunk.bin", io.BytesIO(b"\0" * 100), "application/octet-stream")},
        )
        assert resp.status_code == 400
        assert "completed" in resp.json()["detail"]

    def test_chunk_invalid_index(self, test_client, admin_auth_headers, tmp_path):
        """Chunk index out of range returns 400."""
        temp = tmp_path / "upload.partial"
        temp.write_bytes(b"\0" * 100)
        with _chunk_upload_lock:
            _chunk_upload_sessions["idx-bad"] = {
                "upload_id": "idx-bad",
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
        # index=5 is out of range for total_chunks=1
        resp = test_client.post(
            "/images/upload/idx-bad/chunk?index=5",
            headers=admin_auth_headers,
            files={"chunk": ("chunk.bin", io.BytesIO(b"\0" * 100), "application/octet-stream")},
        )
        assert resp.status_code == 400
        assert "invalid chunk index" in resp.json()["detail"].lower()

    def test_chunk_size_mismatch(self, test_client, admin_auth_headers, tmp_path):
        """Chunk data size not matching expected size returns 400."""
        temp = tmp_path / "upload.partial"
        temp.write_bytes(b"\0" * 200)
        with _chunk_upload_lock:
            _chunk_upload_sessions["sz-bad"] = {
                "upload_id": "sz-bad",
                "status": "uploading",
                "kind": "docker",
                "filename": "test.tar",
                "total_size": 200,
                "chunk_size": 200,
                "total_chunks": 1,
                "bytes_received": 0,
                "chunks_received": [],
                "temp_path": str(temp),
                "final_path": str(tmp_path / "final.tar"),
                "error_message": None,
                "created_at": datetime.now(timezone.utc),
            }
        # Send 50 bytes but expected is 200
        resp = test_client.post(
            "/images/upload/sz-bad/chunk?index=0",
            headers=admin_auth_headers,
            files={"chunk": ("chunk.bin", io.BytesIO(b"\0" * 50), "application/octet-stream")},
        )
        assert resp.status_code == 400
        assert "size mismatch" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 10. complete_chunk_upload — file size mismatch, OSError
# ---------------------------------------------------------------------------

class TestCompleteChunkUploadDeepPaths:
    """Deep paths in POST /images/upload/{id}/complete."""

    def test_file_size_mismatch(self, test_client, admin_auth_headers, tmp_path):
        """Actual file size != declared total_size returns 400."""
        temp = tmp_path / ".upload_sz.partial"
        # Write 50 bytes but declare total_size=100
        temp.write_bytes(b"\0" * 50)
        with _chunk_upload_lock:
            _chunk_upload_sessions["sz-mismatch"] = {
                "upload_id": "sz-mismatch",
                "status": "uploading",
                "kind": "docker",
                "filename": "test.tar",
                "total_size": 100,
                "chunk_size": 100,
                "total_chunks": 1,
                "bytes_received": 100,
                "chunks_received": [0],
                "temp_path": str(temp),
                "final_path": str(tmp_path / "final.tar"),
                "error_message": None,
                "created_at": datetime.now(timezone.utc),
            }
        resp = test_client.post(
            "/images/upload/sz-mismatch/complete",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 400
        assert "size mismatch" in resp.json()["detail"].lower()

        # Session should be marked failed
        with _chunk_upload_lock:
            assert _chunk_upload_sessions["sz-mismatch"]["status"] == "failed"

    def test_os_error_on_move(self, test_client, admin_auth_headers, tmp_path, monkeypatch):
        """OSError during shutil.move returns 500."""
        temp = tmp_path / ".upload_mv.partial"
        temp.write_bytes(b"\0" * 100)
        with _chunk_upload_lock:
            _chunk_upload_sessions["mv-fail"] = {
                "upload_id": "mv-fail",
                "status": "uploading",
                "kind": "docker",
                "filename": "test.tar",
                "total_size": 100,
                "chunk_size": 100,
                "total_chunks": 1,
                "bytes_received": 100,
                "chunks_received": [0],
                "temp_path": str(temp),
                "final_path": str(tmp_path / "nonexistent-dir" / "final.tar"),
                "error_message": None,
                "created_at": datetime.now(timezone.utc),
            }

        monkeypatch.setattr(
            "app.routers.images.upload_docker.shutil.move",
            MagicMock(side_effect=OSError("Permission denied")),
        )

        resp = test_client.post(
            "/images/upload/mv-fail/complete",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 500
        assert "failed to finalize" in resp.json()["detail"].lower()

        with _chunk_upload_lock:
            assert _chunk_upload_sessions["mv-fail"]["status"] == "failed"

    def test_docker_complete_triggers_background_processing(
        self, test_client, admin_auth_headers, tmp_path, monkeypatch
    ):
        """Successful docker complete initiates background processing."""
        from app.routers import images as img

        temp = tmp_path / ".upload_ok.partial"
        content = b"\0" * 100
        temp.write_bytes(content)
        final = tmp_path / "final.tar"

        with _chunk_upload_lock:
            _chunk_upload_sessions["bg-proc"] = {
                "upload_id": "bg-proc",
                "status": "uploading",
                "kind": "docker",
                "filename": "test.tar",
                "total_size": 100,
                "chunk_size": 100,
                "total_chunks": 1,
                "bytes_received": 100,
                "chunks_received": [0],
                "temp_path": str(temp),
                "final_path": str(final),
                "error_message": None,
                "created_at": datetime.now(timezone.utc),
            }

        # Mock Thread so we don't actually run background processing
        monkeypatch.setattr(img.threading, "Thread", lambda **kw: MagicMock(start=lambda: None))

        resp = test_client.post(
            "/images/upload/bg-proc/complete",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "processing"
        assert data["upload_id"] == "bg-proc"


# ---------------------------------------------------------------------------
# 11. cancel_chunk_upload — already-cleaned temp files
# ---------------------------------------------------------------------------

class TestCancelChunkUploadEdgeCases:
    """Cancel endpoint handles missing temp files gracefully."""

    def test_cancel_with_missing_temp_files(self, test_client, admin_auth_headers):
        """Cancel succeeds even when temp files no longer exist on disk."""
        with _chunk_upload_lock:
            _chunk_upload_sessions["cancel-no-files"] = {
                "upload_id": "cancel-no-files",
                "status": "uploading",
                "kind": "docker",
                "filename": "test.tar",
                "total_size": 100,
                "chunk_size": 100,
                "total_chunks": 1,
                "bytes_received": 0,
                "chunks_received": [],
                "temp_path": "/tmp/nonexistent-path-abc123.partial",
                "final_path": "/tmp/nonexistent-path-abc123-final.tar",
                "error_message": None,
                "created_at": datetime.now(timezone.utc),
            }

        resp = test_client.delete(
            "/images/upload/cancel-no-files",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        assert "cancelled" in resp.json()["message"].lower()

        with _chunk_upload_lock:
            assert "cancel-no-files" not in _chunk_upload_sessions


# ---------------------------------------------------------------------------
# 12. _load_image_sync — XZ decompression + docker import branch
# ---------------------------------------------------------------------------

class TestLoadImageSync:
    """Tests for the synchronous _load_image_sync path (stream=false, background=false)."""

    def test_sync_xz_decompression_error(self, test_client, admin_auth_headers, monkeypatch):
        """Invalid XZ file returns 400."""
        monkeypatch.setattr(
            "app.routers.images.upload_docker.ResourceMonitor.check_disk_pressure",
            lambda: PressureLevel.NORMAL,
        )

        # Build a fake .tar.xz that is NOT valid lzma
        bad_data = b"not-an-xz-file"
        resp = test_client.post(
            "/images/load",
            headers=admin_auth_headers,
            files={"file": ("image.tar.xz", io.BytesIO(bad_data), "application/x-xz")},
        )
        assert resp.status_code == 400
        assert "decompress" in resp.json()["detail"].lower()

    def test_sync_docker_load_failure(self, test_client, admin_auth_headers, monkeypatch):
        """docker load failure returns 500."""
        monkeypatch.setattr(
            "app.routers.images.upload_docker.ResourceMonitor.check_disk_pressure",
            lambda: PressureLevel.NORMAL,
        )
        monkeypatch.setattr(
            "app.routers.images.upload_docker._is_docker_image_tar", lambda p: True,
        )
        fake_result = MagicMock(returncode=1, stdout="", stderr="Error: tar invalid")
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_result)

        tar_data = _make_tar_bytes(["manifest.json"])
        resp = test_client.post(
            "/images/load",
            headers=admin_auth_headers,
            files={"file": ("image.tar", io.BytesIO(tar_data), "application/x-tar")},
        )
        assert resp.status_code == 500

    def test_sync_docker_import_for_raw_tar(self, test_client, admin_auth_headers, monkeypatch):
        """Raw filesystem tar uses docker import and succeeds."""
        monkeypatch.setattr(
            "app.routers.images.upload_docker.ResourceMonitor.check_disk_pressure",
            lambda: PressureLevel.NORMAL,
        )
        monkeypatch.setattr(
            "app.routers.images.upload_docker._is_docker_image_tar", lambda p: False,
        )
        fake_result = MagicMock(
            returncode=0, stdout="sha256:abc123def456\n", stderr=""
        )
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_result)
        monkeypatch.setattr(
            "app.routers.images.upload_docker.load_manifest", lambda: {"images": []},
        )
        monkeypatch.setattr(
            "app.routers.images.upload_docker.find_image_by_id", lambda m, i: None,
        )
        monkeypatch.setattr(
            "app.routers.images.upload_docker.detect_device_from_filename",
            lambda r: ("linux", "imported"),
        )
        monkeypatch.setattr(
            "app.routers.images.upload_docker.create_image_entry",
            lambda **kw: {"id": kw["image_id"]},
        )
        monkeypatch.setattr(
            "app.routers.images.upload_docker.save_manifest", lambda m: None,
        )

        tar_data = _make_tar_bytes(["rootfs/etc/hosts"])
        resp = test_client.post(
            "/images/load",
            headers=admin_auth_headers,
            files={"file": ("ceos-lab.tar", io.BytesIO(tar_data), "application/x-tar")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "images" in data
        assert any("imported" in img for img in data["images"])

    def test_sync_no_images_detected(self, test_client, admin_auth_headers, monkeypatch):
        """No images parsed from docker load output returns 500."""
        monkeypatch.setattr(
            "app.routers.images.upload_docker.ResourceMonitor.check_disk_pressure",
            lambda: PressureLevel.NORMAL,
        )
        monkeypatch.setattr(
            "app.routers.images.upload_docker._is_docker_image_tar", lambda p: True,
        )
        fake_result = MagicMock(returncode=0, stdout="", stderr="")
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_result)

        tar_data = _make_tar_bytes(["manifest.json"])
        resp = test_client.post(
            "/images/load",
            headers=admin_auth_headers,
            files={"file": ("image.tar", io.BytesIO(tar_data), "application/x-tar")},
        )
        assert resp.status_code == 500
        assert "no images" in resp.json()["detail"].lower()

    def test_sync_duplicate_image_returns_409(self, test_client, admin_auth_headers, monkeypatch):
        """Image already in manifest returns 409."""
        monkeypatch.setattr(
            "app.routers.images.upload_docker.ResourceMonitor.check_disk_pressure",
            lambda: PressureLevel.NORMAL,
        )
        monkeypatch.setattr(
            "app.routers.images.upload_docker._is_docker_image_tar", lambda p: True,
        )
        fake_result = MagicMock(
            returncode=0, stdout="Loaded image: ceos:4.28\n", stderr=""
        )
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_result)
        monkeypatch.setattr(
            "app.routers.images.upload_docker.load_manifest",
            lambda: {"images": [{"id": "docker:ceos:4.28"}]},
        )
        monkeypatch.setattr(
            "app.routers.images.upload_docker.find_image_by_id",
            lambda m, i: {"id": i},
        )

        tar_data = _make_tar_bytes(["manifest.json"])
        resp = test_client.post(
            "/images/load",
            headers=admin_auth_headers,
            files={"file": ("image.tar", io.BytesIO(tar_data), "application/x-tar")},
        )
        assert resp.status_code == 409
        assert "already exists" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 13. init_chunk_upload — qcow2 validation edge cases
# ---------------------------------------------------------------------------

class TestInitChunkUploadQcow2Validation:
    """qcow2 upload init — filename and duplicate checks."""

    def test_qcow2_bad_extension_rejected(self, test_client, admin_auth_headers, monkeypatch):
        monkeypatch.setattr(
            "app.routers.images.upload_docker.ResourceMonitor.check_disk_pressure",
            lambda: PressureLevel.NORMAL,
        )
        resp = test_client.post(
            "/images/upload/init",
            headers=admin_auth_headers,
            json={"kind": "qcow2", "filename": "image.iso", "total_size": 1024},
        )
        assert resp.status_code == 400
        assert "qcow2" in resp.json()["detail"].lower()

    def test_disk_pressure_on_chunk_init(self, test_client, admin_auth_headers, monkeypatch):
        monkeypatch.setattr(
            "app.routers.images.upload_docker.ResourceMonitor.check_disk_pressure",
            lambda: PressureLevel.CRITICAL,
        )
        resp = test_client.post(
            "/images/upload/init",
            headers=admin_auth_headers,
            json={"kind": "docker", "filename": "test.tar", "total_size": 1024},
        )
        assert resp.status_code == 507

    def test_chunk_status_not_found(self, test_client, auth_headers):
        resp = test_client.get("/images/upload/nonexistent-id", headers=auth_headers)
        assert resp.status_code == 404
