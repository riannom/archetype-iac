"""Additional helper/chunk-upload coverage for app.routers.images."""

from __future__ import annotations

import io
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, UploadFile

from app.routers import images as img
from app.services.resource_monitor import PressureLevel


def _user() -> SimpleNamespace:
    return SimpleNamespace(id="u-1")


@pytest.fixture(autouse=True)
def _clear_chunk_state():
    with img._chunk_upload_lock:
        img._chunk_upload_sessions.clear()
    yield
    with img._chunk_upload_lock:
        img._chunk_upload_sessions.clear()


def _make_tar(path: Path, names: list[str]):
    with tarfile.open(path, "w") as tf:
        for name in names:
            data = b"x"
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))


def test_image_helper_functions(monkeypatch, tmp_path):
    tar_with_manifest = tmp_path / "docker.tar"
    tar_without_manifest = tmp_path / "raw.tar"
    _make_tar(tar_with_manifest, ["manifest.json"])
    _make_tar(tar_without_manifest, ["rootfs/file.txt"])
    assert img._is_docker_image_tar(str(tar_with_manifest)) is True
    assert img._is_docker_image_tar(str(tar_without_manifest)) is False
    assert img._is_docker_image_tar(str(tmp_path / "missing.tar")) is False

    assert img._format_size(10) == "10.0 B"
    assert img._format_size(1024) == "1.0 KB"

    parsed_node = SimpleNamespace(
        ram_mb=4096,
        cpus=2,
        disk_driver="virtio",
        nic_driver="e1000",
        machine_type="q35",
        efi_boot=True,
        efi_vars="vars.fd",
        boot_timeout=600,
        boot_completed_patterns=["login:", "ready"],
        interfaces=["eth1", "eth2"],
        interface_count_default=8,
        interface_naming_pattern="eth",
        libvirt_driver="kvm",
    )
    fake_parser = SimpleNamespace(_parse_node_definition=lambda _yaml, _src: parsed_node)
    with patch("app.iso.virl2_parser.VIRL2Parser", return_value=fake_parser):
        sidecar = img._parse_sidecar_metadata("dummy: true")
    assert sidecar["memory_mb"] == 4096
    assert sidecar["readiness_pattern"] == "login:|ready"

    fake_parser_none = SimpleNamespace(_parse_node_definition=lambda _yaml, _src: None)
    with patch("app.iso.virl2_parser.VIRL2Parser", return_value=fake_parser_none):
        assert img._parse_sidecar_metadata("dummy: true") == {}

    assert img._sanitize_upload_filename("bad name?.tar") == "badname.tar"

    temp = tmp_path / ".tmp.part"
    final = tmp_path / "final.tar"
    temp.write_bytes(b"x")
    final.write_bytes(b"x")
    img._cleanup_chunk_upload_session_files(
        {"temp_path": str(temp), "final_path": str(final), "kind": "docker", "status": "uploading"}
    )
    assert not temp.exists()
    assert not final.exists()

    monkeypatch.setattr(img, "_CHUNK_UPLOAD_DIR", tmp_path / "uploads")
    assert img._chunk_upload_destination("docker", "up1", "image.tar").name == "up1-image.tar"
    with patch("app.routers.images.qcow2_path", return_value=tmp_path / "x.qcow2"):
        assert img._chunk_upload_destination("qcow2", "up2", "x.qcow2").name == "x.qcow2"

    old_id = "old"
    new_id = "new"
    old_file = tmp_path / ".old"
    new_file = tmp_path / ".new"
    old_file.write_bytes(b"x")
    new_file.write_bytes(b"x")
    with img._chunk_upload_lock:
        img._chunk_upload_sessions[old_id] = {
            "created_at": datetime.now(timezone.utc) - timedelta(days=2),
            "temp_path": str(old_file),
            "final_path": str(tmp_path / "old.final"),
            "kind": "docker",
            "status": "uploading",
        }
        img._chunk_upload_sessions[new_id] = {
            "created_at": datetime.now(timezone.utc),
            "temp_path": str(new_file),
            "final_path": str(tmp_path / "new.final"),
            "kind": "docker",
            "status": "uploading",
        }
    img._cleanup_expired_chunk_upload_sessions()
    with img._chunk_upload_lock:
        assert old_id not in img._chunk_upload_sessions
        assert new_id in img._chunk_upload_sessions


@pytest.mark.asyncio
async def test_chunk_upload_init_and_chunk_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(img.ResourceMonitor, "check_disk_pressure", staticmethod(lambda: PressureLevel.NORMAL))
    monkeypatch.setattr(img, "_CHUNK_UPLOAD_DIR", tmp_path / "uploads")

    with pytest.raises(HTTPException):
        img.init_chunk_upload(
            img.ImageChunkUploadInitRequest(kind="bad", filename="x.tar", total_size=10, chunk_size=5),
            current_user=_user(),
        )
    with pytest.raises(HTTPException):
        img.init_chunk_upload(
            img.ImageChunkUploadInitRequest(kind="docker", filename="???", total_size=10, chunk_size=5),
            current_user=_user(),
        )
    with pytest.raises(HTTPException):
        img.init_chunk_upload(
            img.ImageChunkUploadInitRequest(kind="qcow2", filename="x.txt", total_size=10, chunk_size=5),
            current_user=_user(),
        )

    with patch("app.routers.images.load_manifest", return_value={"images": [{"id": "qcow2:test.qcow2"}]}), patch(
        "app.routers.images.find_image_by_id", return_value={"id": "qcow2:test.qcow2"}
    ):
        with pytest.raises(HTTPException):
            img.init_chunk_upload(
                img.ImageChunkUploadInitRequest(kind="qcow2", filename="test.qcow2", total_size=10, chunk_size=5),
                current_user=_user(),
            )

    with patch("app.routers.images.load_manifest", return_value={"images": []}), patch(
        "app.routers.images.find_image_by_id", return_value=None
    ), patch("app.routers.images.qcow2_path", return_value=tmp_path / "exists.qcow2"):
        (tmp_path / "exists.qcow2").write_bytes(b"x")
        with pytest.raises(HTTPException):
            img.init_chunk_upload(
                img.ImageChunkUploadInitRequest(kind="qcow2", filename="exists.qcow2", total_size=10, chunk_size=5),
                current_user=_user(),
            )

    initialized = img.init_chunk_upload(
        img.ImageChunkUploadInitRequest(kind="docker", filename="img.tar", total_size=6, chunk_size=3),
        current_user=_user(),
    )
    upload_id = initialized.upload_id

    with pytest.raises(HTTPException):
        await img.upload_chunk("missing", index=0, chunk=UploadFile(io.BytesIO(b"abc"), filename="c"), current_user=_user())

    with img._chunk_upload_lock:
        img._chunk_upload_sessions[upload_id]["status"] = "processing"
    with pytest.raises(HTTPException):
        await img.upload_chunk(upload_id, index=0, chunk=UploadFile(io.BytesIO(b"abc"), filename="c"), current_user=_user())

    with img._chunk_upload_lock:
        img._chunk_upload_sessions[upload_id]["status"] = "uploading"
    with pytest.raises(HTTPException):
        await img.upload_chunk(upload_id, index=99, chunk=UploadFile(io.BytesIO(b"abc"), filename="c"), current_user=_user())
    with pytest.raises(HTTPException):
        await img.upload_chunk(upload_id, index=0, chunk=UploadFile(io.BytesIO(b"ab"), filename="c"), current_user=_user())

    with patch("app.routers.images.asyncio.to_thread", new_callable=AsyncMock, side_effect=OSError("disk")):
        with pytest.raises(HTTPException):
            await img.upload_chunk(upload_id, index=0, chunk=UploadFile(io.BytesIO(b"abc"), filename="c"), current_user=_user())

    async def _expire_after_write(fn):
        fn()
        with img._chunk_upload_lock:
            img._chunk_upload_sessions.pop(exp_id, None)

    img.init_chunk_upload(
        img.ImageChunkUploadInitRequest(kind="docker", filename="img.tar", total_size=6, chunk_size=3),
        current_user=_user(),
    )
    exp_id = next(reversed(img._chunk_upload_sessions.keys()))
    with patch("app.routers.images.asyncio.to_thread", new_callable=AsyncMock, side_effect=_expire_after_write):
        with pytest.raises(HTTPException):
            await img.upload_chunk(exp_id, index=0, chunk=UploadFile(io.BytesIO(b"abc"), filename="c"), current_user=_user())

    ok = await img.upload_chunk(upload_id, index=0, chunk=UploadFile(io.BytesIO(b"abc"), filename="c"), current_user=_user())
    assert ok.total_received == 3

    with pytest.raises(HTTPException):
        img.get_chunk_upload_status("missing", current_user=_user())
    status = img.get_chunk_upload_status(upload_id, current_user=_user())
    assert status.progress_percent == 50

    with pytest.raises(HTTPException):
        img.cancel_chunk_upload("missing", current_user=_user())
    cancelled = img.cancel_chunk_upload(upload_id, current_user=_user())
    assert "cancelled" in cancelled["message"].lower()


def test_complete_and_confirm_qcow2_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(img.ResourceMonitor, "check_disk_pressure", staticmethod(lambda: PressureLevel.NORMAL))
    upload_id = "up-complete"
    temp_path = tmp_path / ".partial"
    final_path = tmp_path / "final.qcow2"
    temp_path.write_bytes(b"\0" * 6)

    with pytest.raises(HTTPException):
        img.complete_chunk_upload("missing", current_user=_user())

    with img._chunk_upload_lock:
        img._chunk_upload_sessions[upload_id] = {
            "upload_id": upload_id,
            "kind": "qcow2",
            "filename": "final.qcow2",
            "total_size": 6,
            "total_chunks": 2,
            "chunks_received": [0, 1],
            "temp_path": str(temp_path),
            "final_path": str(final_path),
            "status": "completed",
            "auto_build": True,
            "auto_confirm": True,
            "created_at": datetime.now(timezone.utc),
        }
    with pytest.raises(HTTPException):
        img.complete_chunk_upload(upload_id, current_user=_user())

    with img._chunk_upload_lock:
        img._chunk_upload_sessions[upload_id]["status"] = "uploading"
        img._chunk_upload_sessions[upload_id]["chunks_received"] = [0]
    with pytest.raises(HTTPException):
        img.complete_chunk_upload(upload_id, current_user=_user())

    with img._chunk_upload_lock:
        img._chunk_upload_sessions[upload_id]["chunks_received"] = [0, 1]
    temp_path.write_bytes(b"\0" * 5)
    with pytest.raises(HTTPException):
        img.complete_chunk_upload(upload_id, current_user=_user())

    temp_path.write_bytes(b"\0" * 6)
    with patch("app.routers.images.shutil.move", side_effect=OSError("move failed")):
        with pytest.raises(HTTPException):
            img.complete_chunk_upload(upload_id, current_user=_user())

    temp_path.write_bytes(b"\0" * 6)
    with img._chunk_upload_lock:
        img._chunk_upload_sessions[upload_id].update(auto_confirm=False, status="uploading")
    with patch("app.routers.images._detect_qcow2", return_value={"detected_device_id": "csr1000v"}):
        waiting = img.complete_chunk_upload(upload_id, current_user=_user())
    assert waiting.status == "awaiting_confirmation"
    with img._chunk_upload_lock:
        assert img._chunk_upload_sessions[upload_id]["status"] == "awaiting_confirmation"

    with img._chunk_upload_lock:
        img._chunk_upload_sessions[upload_id]["status"] = "uploading"
        img._chunk_upload_sessions[upload_id]["auto_confirm"] = False
        temp_path2 = tmp_path / ".partial2"
        temp_path2.write_bytes(b"\0" * 6)
        img._chunk_upload_sessions[upload_id]["temp_path"] = str(temp_path2)
        img._chunk_upload_sessions[upload_id]["final_path"] = str(tmp_path / "final2.qcow2")
    with patch("app.routers.images._detect_qcow2", side_effect=RuntimeError("detect failed")):
        with pytest.raises(RuntimeError):
            img.complete_chunk_upload(upload_id, current_user=_user())
    with img._chunk_upload_lock:
        assert img._chunk_upload_sessions[upload_id]["status"] == "failed"

    temp_path3 = tmp_path / ".partial3"
    temp_path3.write_bytes(b"\0" * 6)
    with img._chunk_upload_lock:
        img._chunk_upload_sessions[upload_id].update(
            status="uploading",
            auto_confirm=True,
            auto_build=True,
            temp_path=str(temp_path3),
            final_path=str(tmp_path / "final3.qcow2"),
        )
    with patch("app.routers.images._finalize_qcow2_upload", return_value={"registered": True}):
        done = img.complete_chunk_upload(upload_id, current_user=_user())
    assert done.status == "completed"

    docker_id = "up-docker"
    temp_docker = tmp_path / ".docker.part"
    final_docker = tmp_path / "img.tar"
    temp_docker.write_bytes(b"\0" * 4)
    with img._chunk_upload_lock:
        img._chunk_upload_sessions[docker_id] = {
            "upload_id": docker_id,
            "kind": "docker",
            "filename": "img.tar",
            "total_size": 4,
            "total_chunks": 1,
            "chunks_received": [0],
            "temp_path": str(temp_docker),
            "final_path": str(final_docker),
            "status": "uploading",
            "created_at": datetime.now(timezone.utc),
        }

    class _Thread:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def start(self):
            return None

    with patch.object(img.threading, "Thread", side_effect=lambda **kw: _Thread(**kw)):
        processing = img.complete_chunk_upload(docker_id, current_user=_user())
    assert processing.status == "processing"

    confirm_id = "confirm1"
    with img._chunk_upload_lock:
        img._chunk_upload_sessions[confirm_id] = {
            "upload_id": confirm_id,
            "filename": "img.qcow2",
            "status": "awaiting_confirmation",
            "final_path": str(tmp_path / "missing.qcow2"),
            "detection": {"detected_device_id": "csr1000v", "detected_version": "17.9", "sha256": "abc", "size_bytes": 123},
        }
    with pytest.raises(HTTPException):
        img.confirm_qcow2_upload(confirm_id, img.Qcow2ConfirmRequest(), current_user=_user())

    live = tmp_path / "live.qcow2"
    live.write_bytes(b"x")
    with img._chunk_upload_lock:
        img._chunk_upload_sessions[confirm_id] = {
            "upload_id": confirm_id,
            "filename": "img.qcow2",
            "status": "awaiting_confirmation",
            "final_path": str(live),
            "detection": {"detected_device_id": "csr1000v", "detected_version": "17.9", "sha256": "abc", "size_bytes": 123},
        }
    with patch("app.routers.images._register_qcow2", return_value={"ok": True}):
        confirmed = img.confirm_qcow2_upload(
            confirm_id,
            img.Qcow2ConfirmRequest(device_id="csr1000v", version="17.9", metadata={"memory_mb": 4096}),
            current_user=_user(),
        )
    assert confirmed.status == "completed"
