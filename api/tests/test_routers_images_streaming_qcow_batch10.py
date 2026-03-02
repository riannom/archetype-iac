"""High-yield coverage for app.routers.images streaming/background/qcow2 helpers."""

from __future__ import annotations

import io
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from fastapi import HTTPException

from app.routers import images as img


@pytest.fixture(autouse=True)
def _clear_image_state():
    with img._upload_lock:
        img._upload_progress.clear()
    with img._chunk_upload_lock:
        img._chunk_upload_sessions.clear()
    yield
    with img._upload_lock:
        img._upload_progress.clear()
    with img._chunk_upload_lock:
        img._chunk_upload_sessions.clear()


class _LineStream:
    def __init__(self, lines: list[bytes]):
        self._lines = list(lines)

    async def readline(self) -> bytes:
        if self._lines:
            return self._lines.pop(0)
        return b""


class _LoadProc:
    def __init__(self, lines: list[bytes], returncode: int = 0):
        self.stdout = _LineStream(lines)
        self.returncode = returncode

    async def wait(self):
        return self.returncode


class _ImportProc:
    def __init__(self, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self):
        return self._stdout, self._stderr


def test_load_image_background_decompress_and_docker_load_paths(monkeypatch):
    upload_id = "bg-xz"
    img._load_image_background(upload_id, "image.tar.xz", b"not-real-xz")
    progress = img._get_progress(upload_id)
    assert progress is not None
    assert progress["phase"] == "error"
    assert "decompression failed" in progress["message"].lower()

    upload_id = "bg-docker-timeout"
    monkeypatch.setattr(img, "_is_docker_image_tar", lambda _path: True)
    monkeypatch.setattr(
        img.subprocess,
        "run",
        Mock(side_effect=subprocess.TimeoutExpired(cmd=["docker", "load"], timeout=600)),
    )
    img._load_image_background(upload_id, "image.tar", b"docker-archive")
    progress = img._get_progress(upload_id)
    assert progress is not None
    assert "timed out" in progress["message"].lower()

    upload_id = "bg-docker-fail"
    monkeypatch.setattr(
        img.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="", stderr="load failed"),
    )
    img._load_image_background(upload_id, "image.tar", b"docker-archive")
    progress = img._get_progress(upload_id)
    assert progress is not None
    assert progress["phase"] == "error"
    assert "load failed" in progress["message"]


def test_load_image_background_import_and_duplicate_paths(monkeypatch):
    monkeypatch.setattr(img, "_is_docker_image_tar", lambda _path: False)

    # Import timeout branch.
    monkeypatch.setattr(
        img.subprocess,
        "run",
        Mock(side_effect=subprocess.TimeoutExpired(cmd=["docker", "import"], timeout=600)),
    )
    img._load_image_background("bg-import-timeout", "rootfs.tar", b"rootfs")
    timeout_progress = img._get_progress("bg-import-timeout")
    assert timeout_progress is not None
    assert "timed out" in timeout_progress["message"].lower()

    # Import exception branch.
    monkeypatch.setattr(img.subprocess, "run", Mock(side_effect=RuntimeError("import crash")))
    img._load_image_background("bg-import-exc", "rootfs.tar", b"rootfs")
    exc_progress = img._get_progress("bg-import-exc")
    assert exc_progress is not None
    assert "import failed" in exc_progress["message"].lower()

    # Successful import but duplicate image in manifest branch.
    monkeypatch.setattr(img.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=0))
    monkeypatch.setattr(img, "load_manifest", lambda: {"images": [{"id": "docker:rootfs:imported"}]})
    monkeypatch.setattr(img, "find_image_by_id", lambda *_args, **_kwargs: {"id": "docker:rootfs:imported"})

    img._load_image_background("bg-import-dup", "rootfs.tar", b"rootfs")
    dup_progress = img._get_progress("bg-import-dup")
    assert dup_progress is not None
    assert "already exists" in dup_progress["message"].lower()


def test_load_image_background_success_paths(monkeypatch):
    # Docker load success path.
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

    img._load_image_background("bg-docker-ok", "image.tar", b"docker-archive")
    ok_progress = img._get_progress("bg-docker-ok")
    assert ok_progress is not None
    assert ok_progress["phase"] == "complete"
    assert ok_progress.get("complete") is True
    save_manifest.assert_called_once()
    assert any(entry["id"] == "docker:ceos:4.28.0" for entry in manifest["images"])

    # Import success path.
    monkeypatch.setattr(img, "_is_docker_image_tar", lambda _path: False)
    monkeypatch.setattr(img.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=0))
    manifest2 = {"images": []}
    monkeypatch.setattr(img, "load_manifest", lambda: manifest2)
    img._load_image_background("bg-import-ok", "rootfs.tar", b"rootfs")
    import_ok = img._get_progress("bg-import-ok")
    assert import_ok is not None
    assert import_ok["phase"] == "complete"
    assert import_ok.get("complete") is True


@pytest.mark.asyncio
async def test_load_image_streaming_docker_load_success(monkeypatch):
    monkeypatch.setattr(img, "_is_docker_image_tar", lambda _path: True)

    async def _create_subprocess_exec(*cmd, **_kwargs):  # noqa: ARG001
        assert cmd[:3] == ("docker", "load", "-i")
        return _LoadProc(
            [
                b"Loading layer a1\n",
                b"Loaded image: ceos:4.28.0\n",
                b"",
            ],
            returncode=0,
        )

    monkeypatch.setattr(img.asyncio, "create_subprocess_exec", _create_subprocess_exec)

    manifest = {"images": []}
    monkeypatch.setattr(img, "load_manifest", lambda: manifest)
    monkeypatch.setattr(img, "find_image_by_id", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(img, "detect_device_from_filename", lambda _ref: ("eos", "4.28.0"))
    monkeypatch.setattr(
        img,
        "create_image_entry",
        lambda **kwargs: {"id": kwargs["image_id"], "reference": kwargs["reference"]},
    )
    monkeypatch.setattr(img, "save_manifest", Mock())

    events = []
    async for event in img._load_image_streaming("image.tar", b"docker-archive"):
        events.append(event)

    joined = "".join(events)
    assert "event: progress" in joined
    assert "Loaded: ceos:4.28.0" in joined
    assert "event: complete" in joined


@pytest.mark.asyncio
async def test_load_image_streaming_import_paths(monkeypatch):
    monkeypatch.setattr(img, "_is_docker_image_tar", lambda _path: False)

    async def _import_ok(*cmd, **_kwargs):  # noqa: ARG001
        assert cmd[:2] == ("docker", "import")
        return _ImportProc(returncode=0, stdout=b"sha256:deadbeef\n", stderr=b"")

    monkeypatch.setattr(img.asyncio, "create_subprocess_exec", _import_ok)
    monkeypatch.setattr(img, "load_manifest", lambda: {"images": []})
    monkeypatch.setattr(img, "find_image_by_id", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(img, "detect_device_from_filename", lambda _ref: ("linux", "1"))
    monkeypatch.setattr(img, "create_image_entry", lambda **kwargs: {"id": kwargs["image_id"]})
    monkeypatch.setattr(img, "save_manifest", Mock())

    events = []
    async for event in img._load_image_streaming("rootfs.tar", b"rootfs"):
        events.append(event)
    joined = "".join(events)
    assert "event: progress" in joined
    assert "Import complete" in joined
    assert "event: complete" in joined

    async def _import_fail(*_args, **_kwargs):
        return _ImportProc(returncode=1, stdout=b"", stderr=b"boom")

    monkeypatch.setattr(img.asyncio, "create_subprocess_exec", _import_fail)
    fail_events = []
    async for event in img._load_image_streaming("rootfs.tar", b"rootfs"):
        fail_events.append(event)
    assert "event: error" in "".join(fail_events)


def test_detect_qcow2_paths(monkeypatch, tmp_path):
    target = tmp_path / "device.qcow2"
    target.write_bytes(b"qcow2-bytes")

    monkeypatch.setattr(img, "load_manifest", lambda: {"images": [{"id": f"qcow2:{target.name}"}]})
    monkeypatch.setattr(img, "find_image_by_id", lambda *_args, **_kwargs: {"id": f"qcow2:{target.name}"})
    with pytest.raises(HTTPException) as exc_info:
        img._detect_qcow2(target)
    assert exc_info.value.status_code == 409
    assert not target.exists()

    invalid = tmp_path / "invalid.qcow2"
    invalid.write_bytes(b"bad")
    monkeypatch.setattr(img, "load_manifest", lambda: {"images": []})
    monkeypatch.setattr(img, "find_image_by_id", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(img, "validate_qcow2", lambda _dst: (False, "invalid format"))
    with pytest.raises(HTTPException) as exc_info:
        img._detect_qcow2(invalid)
    assert exc_info.value.status_code == 400
    assert not invalid.exists()

    valid = tmp_path / "csr1000v-17.9.qcow2"
    valid.write_bytes(b"ok")

    resolved_cfg = SimpleNamespace(
        memory=8192,
        cpu=4,
        disk_driver="virtio",
        nic_driver="virtio-net",
        machine_type="q35",
        efi_boot=False,
        max_ports=24,
        vendor="cisco",
    )
    fake_resolver = SimpleNamespace(resolve_config=lambda _device: resolved_cfg)

    monkeypatch.setattr(img, "validate_qcow2", lambda _dst: (True, None))
    monkeypatch.setattr(img, "compute_sha256", lambda _dst: "sha256-value")
    monkeypatch.setattr(img, "detect_device_from_filename", lambda _name: ("csr1000v", "17.9"))
    with patch("app.services.device_resolver.get_resolver", return_value=fake_resolver):
        detected = img._detect_qcow2(valid)

    assert detected["confidence"] == "high"
    assert detected["sha256"] == "sha256-value"
    assert detected["suggested_metadata"]["memory_mb"] == 8192


def test_register_and_finalize_qcow2_paths(monkeypatch, tmp_path):
    qcow = tmp_path / "csr1000v-17.9.qcow2"
    qcow.write_bytes(b"qcow2")

    monkeypatch.setattr(img, "load_manifest", lambda: {"images": []})
    monkeypatch.setattr(img, "find_image_by_id", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        img,
        "create_image_entry",
        lambda **kwargs: {"id": kwargs["image_id"], "device_id": kwargs.get("device_id")},
    )
    save_manifest = Mock()
    monkeypatch.setattr(img, "save_manifest", save_manifest)
    monkeypatch.setattr(img, "detect_qcow2_device_type", lambda _name: ("csr1000v", "csr1000v"))

    queue = SimpleNamespace(enqueue=Mock(return_value=SimpleNamespace(id="job-1")))
    monkeypatch.setattr(img, "get_queue", lambda: queue)

    registered = img._register_qcow2(
        qcow,
        device_id="csr1000v",
        version="17.9",
        sha256="hash",
        auto_build=True,
        metadata={"memory_mb": 4096, "cpu_count": 2},
    )
    assert registered["build_job_id"] == "job-1"
    assert registered["build_status"] == "queued"
    save_manifest.assert_called_once()

    dup = tmp_path / "dup.qcow2"
    dup.write_bytes(b"x")
    monkeypatch.setattr(img, "find_image_by_id", lambda *_args, **_kwargs: {"id": f"qcow2:{dup.name}"})
    with pytest.raises(HTTPException) as exc_info:
        img._register_qcow2(dup)
    assert exc_info.value.status_code == 409

    final = tmp_path / "final.qcow2"
    final.write_bytes(b"ok")
    monkeypatch.setattr(img, "load_manifest", lambda: {"images": []})
    monkeypatch.setattr(img, "find_image_by_id", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(img, "validate_qcow2", lambda _dst: (True, None))
    monkeypatch.setattr(img, "compute_sha256", lambda _dst: "sha-final")
    monkeypatch.setattr(img, "detect_device_from_filename", lambda _name: ("csr1000v", "17.9"))

    called = {}

    def _fake_register(destination, **kwargs):
        called["destination"] = destination
        called["kwargs"] = kwargs
        return {"path": str(destination), "filename": destination.name}

    monkeypatch.setattr(img, "_register_qcow2", _fake_register)

    finalized = img._finalize_qcow2_upload(final, auto_build=False)
    assert finalized["filename"] == "final.qcow2"
    assert called["kwargs"]["auto_build"] is False

    invalid = tmp_path / "invalid-final.qcow2"
    invalid.write_bytes(b"bad")
    monkeypatch.setattr(img, "validate_qcow2", lambda _dst: (False, "broken"))
    with pytest.raises(HTTPException) as exc_info:
        img._finalize_qcow2_upload(invalid)
    assert exc_info.value.status_code == 400
    assert not invalid.exists()
