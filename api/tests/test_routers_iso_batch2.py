"""Additional branch coverage for app.routers.iso."""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException, UploadFile

from app.config import settings
from app.iso.models import ISOFormat, ISOManifest, ISOSession, ParsedImage, ParsedNodeDefinition
from app.routers import iso as iso_router


@pytest.fixture(autouse=True)
def _clear_iso_router_state():
    with iso_router._session_lock:
        iso_router._sessions.clear()
    with iso_router._upload_lock:
        iso_router._upload_sessions.clear()
    yield
    with iso_router._session_lock:
        iso_router._sessions.clear()
    with iso_router._upload_lock:
        iso_router._upload_sessions.clear()


def _user() -> SimpleNamespace:
    return SimpleNamespace(id="u-1")


def test_session_helpers_and_init_upload_branches(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "iso_upload_dir", str(tmp_path))
    session = ISOSession(id="s1", iso_path=str(tmp_path / "a.iso"), status="pending")
    iso_router._save_session(session)
    assert iso_router._get_session("s1") is not None
    iso_router._delete_session("s1")
    assert iso_router._get_session("s1") is None

    with pytest.raises(HTTPException) as bad_ext:
        iso_router.init_upload(
            iso_router.UploadInitRequest(filename="bad.txt", total_size=10, chunk_size=5),
            current_user=_user(),
        )
    assert bad_ext.value.status_code == 400

    (tmp_path / "test.iso").write_bytes(b"x")
    monkeypatch.setattr(iso_router.time, "time", lambda: 111)
    initialized = iso_router.init_upload(
        iso_router.UploadInitRequest(filename="test.iso", total_size=5, chunk_size=2),
        current_user=_user(),
    )
    assert initialized.filename == "test_111.iso"
    assert initialized.total_chunks == 3
    assert Path(iso_router._upload_sessions[initialized.upload_id]["temp_path"]).exists()


@pytest.mark.asyncio
async def test_upload_chunk_error_and_success_paths(monkeypatch, tmp_path):
    upload_id = "up1"
    temp_path = tmp_path / ".upload_up1.partial"
    temp_path.write_bytes(b"\0" * 6)

    base_session = {
        "upload_id": upload_id,
        "filename": "test.iso",
        "total_size": 6,
        "chunk_size": 3,
        "total_chunks": 2,
        "bytes_received": 0,
        "chunks_received": [],
        "temp_path": str(temp_path),
        "final_path": str(tmp_path / "test.iso"),
        "status": "uploading",
        "error_message": None,
        "user_id": "u-1",
        "created_at": iso_router.datetime.now(iso_router.timezone.utc),
    }

    with pytest.raises(HTTPException) as missing:
        await iso_router.upload_chunk(upload_id, index=0, chunk=UploadFile(io.BytesIO(b"abc"), filename="c"), current_user=_user())
    assert missing.value.status_code == 404

    with iso_router._upload_lock:
        iso_router._upload_sessions[upload_id] = {**base_session, "status": "completed"}
    with pytest.raises(HTTPException) as bad_status:
        await iso_router.upload_chunk(upload_id, index=0, chunk=UploadFile(io.BytesIO(b"abc"), filename="c"), current_user=_user())
    assert bad_status.value.status_code == 400

    with iso_router._upload_lock:
        iso_router._upload_sessions[upload_id] = dict(base_session)
    with pytest.raises(HTTPException) as bad_index:
        await iso_router.upload_chunk(upload_id, index=99, chunk=UploadFile(io.BytesIO(b"abc"), filename="c"), current_user=_user())
    assert bad_index.value.status_code == 400

    with pytest.raises(HTTPException) as bad_size:
        await iso_router.upload_chunk(upload_id, index=0, chunk=UploadFile(io.BytesIO(b"ab"), filename="c"), current_user=_user())
    assert bad_size.value.status_code == 400

    with patch("app.routers.iso.asyncio.to_thread", new_callable=AsyncMock, side_effect=IOError("disk")):
        with pytest.raises(HTTPException) as write_err:
            await iso_router.upload_chunk(upload_id, index=0, chunk=UploadFile(io.BytesIO(b"abc"), filename="c"), current_user=_user())
    assert write_err.value.status_code == 500

    async def _expire_session(func):
        func()
        with iso_router._upload_lock:
            iso_router._upload_sessions.pop(upload_id, None)

    with iso_router._upload_lock:
        iso_router._upload_sessions[upload_id] = dict(base_session)
    with patch("app.routers.iso.asyncio.to_thread", new_callable=AsyncMock, side_effect=_expire_session):
        with pytest.raises(HTTPException) as expired:
            await iso_router.upload_chunk(upload_id, index=0, chunk=UploadFile(io.BytesIO(b"abc"), filename="c"), current_user=_user())
    assert expired.value.status_code == 404

    with iso_router._upload_lock:
        iso_router._upload_sessions[upload_id] = dict(base_session)
    ok = await iso_router.upload_chunk(upload_id, index=0, chunk=UploadFile(io.BytesIO(b"abc"), filename="c"), current_user=_user())
    assert ok.total_received == 3
    assert ok.is_complete is False

    dup = await iso_router.upload_chunk(upload_id, index=0, chunk=UploadFile(io.BytesIO(b"abc"), filename="c"), current_user=_user())
    assert dup.total_received == 3


def test_upload_status_complete_and_cancel_paths(monkeypatch, tmp_path):
    upload_id = "up2"
    temp_path = tmp_path / ".upload_up2.partial"
    temp_path.write_bytes(b"\0" * 4)
    final_path = tmp_path / "done.iso"

    with pytest.raises(HTTPException) as missing:
        iso_router.get_upload_status("nope", current_user=_user())
    assert missing.value.status_code == 404

    with iso_router._upload_lock:
        iso_router._upload_sessions[upload_id] = {
            "upload_id": upload_id,
            "filename": "done.iso",
            "total_size": 4,
            "chunk_size": 2,
            "total_chunks": 2,
            "bytes_received": 4,
            "chunks_received": [0, 1],
            "temp_path": str(temp_path),
            "final_path": str(final_path),
            "status": "completed",
            "created_at": iso_router.datetime.now(iso_router.timezone.utc),
        }
    status = iso_router.get_upload_status(upload_id, current_user=_user())
    assert status.progress_percent == 100
    assert status.iso_path == str(final_path)

    with pytest.raises(HTTPException) as complete_missing:
        iso_router.complete_upload("nope", current_user=_user())
    assert complete_missing.value.status_code == 404

    with iso_router._upload_lock:
        iso_router._upload_sessions[upload_id]["status"] = "failed"
    with pytest.raises(HTTPException) as complete_status:
        iso_router.complete_upload(upload_id, current_user=_user())
    assert complete_status.value.status_code == 400

    with iso_router._upload_lock:
        iso_router._upload_sessions[upload_id].update(
            status="uploading",
            chunks_received=[0],
            bytes_received=2,
        )
    with pytest.raises(HTTPException) as missing_chunks:
        iso_router.complete_upload(upload_id, current_user=_user())
    assert missing_chunks.value.status_code == 400

    temp_path.write_bytes(b"\0" * 3)
    with iso_router._upload_lock:
        iso_router._upload_sessions[upload_id]["chunks_received"] = [0, 1]
    with pytest.raises(HTTPException) as size_mismatch:
        iso_router.complete_upload(upload_id, current_user=_user())
    assert size_mismatch.value.status_code == 400

    temp_path.write_bytes(b"\0" * 4)
    with patch("app.routers.iso.shutil.move", side_effect=IOError("move failed")):
        with pytest.raises(HTTPException) as move_fail:
            iso_router.complete_upload(upload_id, current_user=_user())
    assert move_fail.value.status_code == 500

    temp_path.write_bytes(b"\0" * 4)
    with iso_router._upload_lock:
        iso_router._upload_sessions[upload_id]["status"] = "uploading"
        iso_router._upload_sessions[upload_id]["error_message"] = None
    completed = iso_router.complete_upload(upload_id, current_user=_user())
    assert completed.iso_path == str(final_path)
    with iso_router._upload_lock:
        assert iso_router._upload_sessions[upload_id]["status"] == "completed"

    with pytest.raises(HTTPException) as cancel_missing:
        iso_router.cancel_upload("nope", current_user=_user())
    assert cancel_missing.value.status_code == 404

    cancel_id = "cancel1"
    temp_cancel = tmp_path / ".upload_cancel.partial"
    temp_cancel.write_bytes(b"123")
    with iso_router._upload_lock:
        iso_router._upload_sessions[cancel_id] = {
            "upload_id": cancel_id,
            "temp_path": str(temp_cancel),
        }
    cancelled = iso_router.cancel_upload(cancel_id, current_user=_user())
    assert "cancelled" in cancelled["message"]

    cancel_id2 = "cancel2"
    temp_dir = tmp_path / ".upload_dir"
    temp_dir.mkdir()
    with iso_router._upload_lock:
        iso_router._upload_sessions[cancel_id2] = {
            "upload_id": cancel_id2,
            "temp_path": str(temp_dir),
        }
    cancelled2 = iso_router.cancel_upload(cancel_id2, current_user=_user())
    assert "cancelled" in cancelled2["message"]


@pytest.mark.asyncio
async def test_scan_manifest_import_progress_stream_and_delete(monkeypatch, tmp_path):
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    monkeypatch.setattr(settings, "iso_upload_dir", str(upload_root))
    user = _user()

    outside = tmp_path / "outside.iso"
    outside.write_bytes(b"x")
    with pytest.raises(HTTPException) as traversal:
        await iso_router.scan_iso(iso_router.ScanRequest(iso_path=str(outside)), current_user=user)
    assert traversal.value.status_code == 400

    missing = upload_root / "missing.iso"
    with pytest.raises(HTTPException) as missing_err:
        await iso_router.scan_iso(iso_router.ScanRequest(iso_path=str(missing)), current_user=user)
    assert missing_err.value.status_code == 404

    not_file = upload_root / "as_dir.iso"
    not_file.mkdir()
    with pytest.raises(HTTPException) as dir_err:
        await iso_router.scan_iso(iso_router.ScanRequest(iso_path=str(not_file)), current_user=user)
    assert dir_err.value.status_code == 400

    txt_path = upload_root / "test.txt"
    txt_path.write_text("x", encoding="utf-8")
    with pytest.raises(HTTPException) as ext_err:
        await iso_router.scan_iso(iso_router.ScanRequest(iso_path=str(txt_path)), current_user=user)
    assert ext_err.value.status_code == 400

    iso_path = upload_root / "test.iso"
    iso_path.write_bytes(b"fake-iso")
    with patch("app.routers.iso.check_7z_available", new_callable=AsyncMock, return_value=False):
        with pytest.raises(HTTPException) as no7z:
            await iso_router.scan_iso(iso_router.ScanRequest(iso_path=str(iso_path)), current_user=user)
    assert no7z.value.status_code == 500

    fake_extractor = SimpleNamespace(get_file_names=AsyncMock(return_value=["a"]), cleanup=Mock())
    with patch("app.routers.iso.check_7z_available", new_callable=AsyncMock, return_value=True), patch(
        "app.routers.iso.ISOExtractor", return_value=fake_extractor
    ), patch("app.routers.iso.ParserRegistry.get_parser", return_value=None):
        with pytest.raises(HTTPException) as no_parser:
            await iso_router.scan_iso(iso_router.ScanRequest(iso_path=str(iso_path)), current_user=user)
    assert no_parser.value.status_code == 400

    manifest = ISOManifest(iso_path=str(iso_path), format=ISOFormat.VIRL2, size_bytes=10)
    parser = SimpleNamespace(parse=AsyncMock(return_value=manifest))
    with patch("app.routers.iso.check_7z_available", new_callable=AsyncMock, return_value=True), patch(
        "app.routers.iso.ISOExtractor", return_value=fake_extractor
    ), patch("app.routers.iso.ParserRegistry.get_parser", return_value=parser):
        scanned = await iso_router.scan_iso(iso_router.ScanRequest(iso_path=str(iso_path)), current_user=user)
    assert scanned.format == "virl2"
    session_id = scanned.session_id
    assert iso_router._get_session(session_id) is not None

    with pytest.raises(HTTPException):
        iso_router.get_manifest("missing", current_user=user)
    with pytest.raises(HTTPException):
        iso_router.get_manifest("missing2", current_user=user)

    session = iso_router._get_session(session_id)
    assert session is not None
    assert iso_router.get_manifest(session_id, current_user=user)["manifest"]["format"] == "virl2"

    with pytest.raises(HTTPException):
        await iso_router.start_import("missing", iso_router.ImportRequest(image_ids=[]), current_user=user)

    session_no_manifest = ISOSession(id="nomani", iso_path=str(iso_path), status="scanned", manifest=None)
    iso_router._save_session(session_no_manifest)
    with pytest.raises(HTTPException):
        await iso_router.start_import("nomani", iso_router.ImportRequest(image_ids=[]), current_user=user)

    image = ParsedImage(
        id="img-1",
        node_definition_id="n1",
        disk_image_filename="a.qcow2",
        disk_image_path="/a.qcow2",
        image_type="qcow2",
    )
    session.manifest = ISOManifest(
        iso_path=str(iso_path),
        format=ISOFormat.VIRL2,
        size_bytes=10,
        images=[image],
        node_definitions=[ParsedNodeDefinition(id="n1", label="N1")],
    )
    session.status = "importing"
    iso_router._save_session(session)
    with pytest.raises(HTTPException):
        await iso_router.start_import(session_id, iso_router.ImportRequest(image_ids=["img-1"]), current_user=user)

    session.status = "scanned"
    iso_router._save_session(session)
    with pytest.raises(HTTPException):
        await iso_router.start_import(session_id, iso_router.ImportRequest(image_ids=["bad-id"]), current_user=user)

    def _consume_task(coro):
        coro.close()
        return None

    with patch("app.routers.iso.asyncio.create_task", side_effect=_consume_task) as create_task:
        started = await iso_router.start_import(
            session_id,
            iso_router.ImportRequest(image_ids=["img-1"], create_devices=False),
            current_user=user,
        )
    assert started["status"] == "importing"
    create_task.assert_called_once()

    session = iso_router._get_session(session_id)
    assert session is not None
    session.image_progress = {
        "img-1": {"status": "completed"},
        "img-2": {"status": "failed"},
        "img-3": {"status": "extracting"},
    }
    iso_router._save_session(session)
    progress = iso_router.get_import_progress(session_id, current_user=user)
    assert "img-1" in progress.completed_images
    assert "img-2" in progress.failed_images

    with pytest.raises(HTTPException):
        iso_router.get_import_progress("missing", current_user=user)

    with pytest.raises(HTTPException):
        iso_router.get_session_info("missing", current_user=user)
    info = iso_router.get_session_info(session_id, current_user=user)
    assert info.session_id == session_id
    assert "event: progress" in iso_router._sse_event("progress", {"ok": True})

    # Stream: session disappears after first loop iteration -> emits error event.
    seq = [session, session, None]
    with patch("app.routers.iso._get_session", side_effect=lambda sid: seq.pop(0) if seq else None), patch(
        "app.routers.iso.asyncio.sleep", new_callable=AsyncMock
    ):
        stream_resp = await iso_router.stream_import_progress(session_id, current_user=user)
        events = []
        async for evt in stream_resp.body_iterator:
            events.append(evt)
    assert any("event: error" in e for e in events)

    session.status = "importing"
    iso_router._save_session(session)
    with pytest.raises(HTTPException):
        iso_router.delete_session("missing", current_user=user)
    deleted = iso_router.delete_session(session_id, current_user=user)
    assert "deleted" in deleted["message"]
    assert iso_router._get_session(session_id) is None


@pytest.mark.asyncio
async def test_import_single_image_docker_iol_and_unsupported(monkeypatch, tmp_path):
    class _FakeExtractor:
        async def extract_file(self, _src, dest, progress_callback=None, timeout_seconds=None):
            _ = timeout_seconds
            if progress_callback:
                progress_callback(SimpleNamespace(percent=1))
            dest.write_bytes(b"image")

    extractor = _FakeExtractor()
    manifest_data = {"images": []}
    node_def = ParsedNodeDefinition(id="n1", label="Node")

    monkeypatch.setattr(iso_router, "_update_image_progress", lambda *args, **kwargs: None)
    monkeypatch.setattr(iso_router, "get_image_device_mapping", lambda image, _defs: ("dev-1", None))
    monkeypatch.setattr(iso_router, "find_image_by_id", lambda _manifest, _id: None)
    monkeypatch.setattr(
        iso_router,
        "create_image_entry",
        lambda **kwargs: {"id": kwargs["image_id"], "compatible_devices": kwargs.get("compatible_devices", [])},
    )

    docker_proc = SimpleNamespace(
        communicate=AsyncMock(return_value=(b"Loaded image: repo/test:1.0\n", b"")),
        returncode=0,
    )
    with patch("app.routers.iso.asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=docker_proc):
        await iso_router._import_single_image(
            session_id="s1",
            image=ParsedImage(
                id="img-docker",
                node_definition_id="n1",
                disk_image_filename="docker.tar.gz",
                disk_image_path="/docker.tar.gz",
                image_type="docker",
                version="1.0",
            ),
            node_definitions=[node_def],
            extractor=extractor,
            image_store=tmp_path,
            manifest_data=manifest_data,
            create_devices=False,
            iso_source="sample.iso",
        )
    assert any(i["id"] == "docker:repo/test:1.0" for i in manifest_data["images"])

    iol_manifest = {"images": []}
    fake_queue = SimpleNamespace(enqueue=lambda *args, **kwargs: SimpleNamespace(id="job-1"))
    with patch("app.jobs.get_queue", return_value=fake_queue):
        await iso_router._import_single_image(
            session_id="s1",
            image=ParsedImage(
                id="img-iol",
                node_definition_id="n1",
                disk_image_filename="iol.bin",
                disk_image_path="/iol.bin",
                image_type="iol",
                version="15.9",
            ),
            node_definitions=[node_def],
            extractor=extractor,
            image_store=tmp_path,
            manifest_data=iol_manifest,
            create_devices=False,
            iso_source="sample.iso",
        )
    assert any(i["id"].startswith("iol:") for i in iol_manifest["images"])
    assert iol_manifest["images"][0]["build_status"] == "queued"

    with pytest.raises(ValueError):
        await iso_router._import_single_image(
            session_id="s1",
            image=ParsedImage(
                id="img-unknown",
                node_definition_id="n1",
                disk_image_filename="x.bin",
                disk_image_path="/x.bin",
                image_type="unsupported",
            ),
            node_definitions=[node_def],
            extractor=extractor,
            image_store=tmp_path,
            manifest_data={"images": []},
            create_devices=False,
        )
