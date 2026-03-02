"""Additional archive background-loader coverage for app.routers.images."""

from __future__ import annotations

import lzma
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.routers import images as img


@pytest.fixture(autouse=True)
def _clear_image_upload_state():
    with img._chunk_upload_lock:
        img._chunk_upload_sessions.clear()
    with img._upload_lock:
        img._upload_progress.clear()
    yield
    with img._chunk_upload_lock:
        img._chunk_upload_sessions.clear()
    with img._upload_lock:
        img._upload_progress.clear()


def _seed_session(upload_id: str):
    with img._chunk_upload_lock:
        img._chunk_upload_sessions[upload_id] = {
            "upload_id": upload_id,
            "status": "processing",
            "error_message": None,
            "created_at": datetime.now(timezone.utc),
        }


def _session(upload_id: str) -> dict:
    with img._chunk_upload_lock:
        return dict(img._chunk_upload_sessions[upload_id])


def _write_archive(path: Path, content: bytes = b"archive"):
    path.write_bytes(content)


def test_archive_loader_missing_staged_file_marks_failed(tmp_path):
    upload_id = "missing-file"
    _seed_session(upload_id)

    img._load_image_background_from_archive(
        upload_id,
        "image.tar",
        str(tmp_path / "gone.tar"),
        cleanup_archive=False,
    )

    status = _session(upload_id)
    assert status["status"] == "failed"
    assert "not found" in (status.get("error_message") or "").lower()


def test_archive_loader_xz_decompress_failure_marks_failed(monkeypatch, tmp_path):
    upload_id = "xz-fail"
    _seed_session(upload_id)

    staged = tmp_path / "image.tar.xz"
    _write_archive(staged)

    monkeypatch.setattr(img.lzma, "open", Mock(side_effect=lzma.LZMAError("corrupt")))

    img._load_image_background_from_archive(
        upload_id,
        "image.tar.xz",
        str(staged),
        cleanup_archive=False,
    )

    status = _session(upload_id)
    assert status["status"] == "failed"
    assert "corrupt" in (status.get("error_message") or "")


def test_archive_loader_docker_load_failure_paths(monkeypatch, tmp_path):
    upload_id = "docker-fail"
    _seed_session(upload_id)

    staged = tmp_path / "image.tar"
    _write_archive(staged)

    monkeypatch.setattr(img, "_is_docker_image_tar", lambda _path: True)

    # Timeout path.
    monkeypatch.setattr(
        img.subprocess,
        "run",
        Mock(side_effect=subprocess.TimeoutExpired(cmd=["docker", "load"], timeout=600)),
    )
    img._load_image_background_from_archive(upload_id, "image.tar", str(staged), cleanup_archive=False)
    assert "timed out" in (_session(upload_id).get("error_message") or "")

    # Non-zero return code path.
    with img._chunk_upload_lock:
        img._chunk_upload_sessions[upload_id]["status"] = "processing"
    monkeypatch.setattr(
        img.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="", stderr="docker failed"),
    )
    img._load_image_background_from_archive(upload_id, "image.tar", str(staged), cleanup_archive=False)
    assert "docker failed" in (_session(upload_id).get("error_message") or "")

    # No loaded images path.
    with img._chunk_upload_lock:
        img._chunk_upload_sessions[upload_id]["status"] = "processing"
    monkeypatch.setattr(
        img.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="ok", stderr=""),
    )
    img._load_image_background_from_archive(upload_id, "image.tar", str(staged), cleanup_archive=False)
    assert "No images detected" in (_session(upload_id).get("error_message") or "")


def test_archive_loader_duplicate_and_success_paths(monkeypatch, tmp_path):
    upload_id = "docker-success"
    _seed_session(upload_id)

    staged = tmp_path / "image.tar"
    _write_archive(staged)

    monkeypatch.setattr(img, "_is_docker_image_tar", lambda _path: True)
    monkeypatch.setattr(
        img.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="Loaded image: ceos:4.28.0\n",
            stderr="",
        ),
    )
    monkeypatch.setattr(img, "load_manifest", lambda: {"images": [{"id": "docker:ceos:4.28.0"}]})
    monkeypatch.setattr(img, "find_image_by_id", lambda *_args, **_kwargs: {"id": "docker:ceos:4.28.0"})

    img._load_image_background_from_archive(upload_id, "image.tar", str(staged), cleanup_archive=False)
    assert "already exists" in (_session(upload_id).get("error_message") or "")

    with img._chunk_upload_lock:
        img._chunk_upload_sessions[upload_id]["status"] = "processing"
        img._chunk_upload_sessions[upload_id]["error_message"] = None

    manifest = {"images": []}
    monkeypatch.setattr(img, "load_manifest", lambda: manifest)
    monkeypatch.setattr(img, "find_image_by_id", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(img, "detect_device_from_filename", lambda _ref: ("eos", "4.28.0"))
    monkeypatch.setattr(
        img,
        "create_image_entry",
        lambda **kwargs: {"id": kwargs["image_id"], "reference": kwargs["reference"]},
    )
    save_manifest = Mock()
    monkeypatch.setattr(img, "save_manifest", save_manifest)

    img._load_image_background_from_archive(upload_id, "image.tar", str(staged), cleanup_archive=False)

    status = _session(upload_id)
    assert status["status"] == "completed"
    assert status.get("error_message") is None
    save_manifest.assert_called_once()
    assert any(entry["id"] == "docker:ceos:4.28.0" for entry in manifest["images"])


def test_archive_loader_import_failure_paths(monkeypatch, tmp_path):
    upload_id = "import-fail"
    _seed_session(upload_id)

    staged = tmp_path / "rootfs.tar"
    _write_archive(staged)

    monkeypatch.setattr(img, "_is_docker_image_tar", lambda _path: False)

    # Timeout path for docker import.
    monkeypatch.setattr(
        img.subprocess,
        "run",
        Mock(side_effect=subprocess.TimeoutExpired(cmd=["docker", "import"], timeout=600)),
    )
    img._load_image_background_from_archive(upload_id, "rootfs.tar", str(staged), cleanup_archive=False)
    assert "timed out" in (_session(upload_id).get("error_message") or "")

    # Generic exception path for docker import.
    with img._chunk_upload_lock:
        img._chunk_upload_sessions[upload_id]["status"] = "processing"
    monkeypatch.setattr(img.subprocess, "run", Mock(side_effect=RuntimeError("import crash")))
    img._load_image_background_from_archive(upload_id, "rootfs.tar", str(staged), cleanup_archive=False)
    assert "import crash" in (_session(upload_id).get("error_message") or "")

    # Non-zero return code path.
    with img._chunk_upload_lock:
        img._chunk_upload_sessions[upload_id]["status"] = "processing"
    monkeypatch.setattr(
        img.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=2),
    )
    img._load_image_background_from_archive(upload_id, "rootfs.tar", str(staged), cleanup_archive=False)
    assert "docker import failed" in (_session(upload_id).get("error_message") or "")
