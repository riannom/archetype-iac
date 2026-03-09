"""Docker tar upload endpoints: single-shot, chunked, and streaming SSE."""
from __future__ import annotations

import asyncio
import lzma
import logging
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse

from app import models
from app.auth import get_current_admin, get_current_user
from app.config import settings
from app.db import get_session
from app.image_store import (
    create_image_entry,
    detect_device_from_filename,
    docker_archive_path,
    find_image_by_id,
    load_manifest,
    save_manifest,
    update_image_entry,
)
from app.services.catalog_service import (
    CatalogImageNotFoundError,
    apply_manifest_style_image_update,
    catalog_is_seeded,
)
from app.services.resource_monitor import PressureLevel, ResourceMonitor
from app.utils.image_integrity import compute_sha256

from ._shared import (
    DEFAULT_CHUNK_SIZE,
    ImageChunkUploadChunkResponse,
    ImageChunkUploadCompleteResponse,
    ImageChunkUploadInitRequest,
    ImageChunkUploadInitResponse,
    ImageChunkUploadStatusResponse,
    _chunk_upload_destination,
    _chunk_upload_lock,
    _chunk_upload_sessions,
    _cleanup_chunk_upload_session_files,
    _cleanup_expired_chunk_upload_sessions,
    _cleanup_temp_files,
    _decompress_xz_file,
    _format_size,
    _get_progress,
    _is_supported_qcow2_upload_filename,
    _is_docker_image_tar,
    _resolved_qcow2_upload_filename,
    _sanitize_upload_filename,
    _send_sse_event,
    _update_progress,
    _write_content_to_tempfile,
    threading,
)

router = APIRouter(tags=["images"])
logger = logging.getLogger(__name__)


def _persist_docker_archive_metadata(image_id: str, updates: dict[str, object]) -> None:
    try:
        with get_session() as session:
            if catalog_is_seeded(session):
                try:
                    apply_manifest_style_image_update(
                        session,
                        image_id,
                        updates,
                        event_type="image_archive_update",
                        summary=f"Updated Docker archive metadata for '{image_id}'",
                        source="api.images.upload_docker",
                    )
                except CatalogImageNotFoundError:
                    session.rollback()
                    return
                session.commit()
                return
    except Exception:
        logger.warning("Failed to persist Docker archive metadata for %s", image_id, exc_info=True)
        return

    manifest = load_manifest()
    updated = update_image_entry(manifest, image_id, updates)
    if updated:
        save_manifest(manifest)


def _archive_docker_image(image_id: str, reference: str) -> None:
    archive_path = docker_archive_path(image_id)
    partial_path = archive_path.with_suffix(f"{archive_path.suffix}.partial")
    archive_started_at = datetime.now(timezone.utc)
    _persist_docker_archive_metadata(
        image_id,
        {
            "archive_path": str(archive_path),
            "archive_status": "pending",
            "archive_sha256": None,
            "archive_size_bytes": None,
            "archive_created_at": None,
            "archive_verified_at": None,
            "archive_error": None,
        },
    )

    try:
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        if partial_path.exists():
            partial_path.unlink()

        result = subprocess.run(
            ["docker", "save", "-o", str(partial_path), reference],
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
        )
        output = ((result.stdout or "") + (result.stderr or "")).strip()
        if result.returncode != 0:
            raise RuntimeError(output or "docker save failed")

        archive_size_bytes = partial_path.stat().st_size
        archive_sha256 = compute_sha256(partial_path)
        os.replace(partial_path, archive_path)
        archive_timestamp = archive_started_at.isoformat()
        _persist_docker_archive_metadata(
            image_id,
            {
                "archive_path": str(archive_path),
                "archive_status": "ready",
                "archive_sha256": archive_sha256,
                "archive_size_bytes": archive_size_bytes,
                "archive_created_at": archive_timestamp,
                "archive_verified_at": archive_timestamp,
                "archive_error": None,
            },
        )
    except Exception as exc:
        if partial_path.exists():
            partial_path.unlink()
        _persist_docker_archive_metadata(
            image_id,
            {
                "archive_path": str(archive_path),
                "archive_status": "failed",
                "archive_error": str(exc),
                "archive_verified_at": None,
            },
        )
        logger.warning("Failed to create Docker archive for %s (%s)", image_id, reference, exc_info=True)


def _queue_docker_archive_creation(image_refs: list[str]) -> None:
    for image_ref in image_refs:
        request_docker_archive_creation(f"docker:{image_ref}", image_ref)


def request_docker_archive_creation(image_id: str, reference: str, *, force: bool = False) -> bool:
    if not force and not settings.image_archive_docker_images:
        return False

    manifest = load_manifest()
    image = find_image_by_id(manifest, image_id)
    if image is None:
        return False

    archive_status = str(image.get("archive_status") or "none").lower()
    if archive_status == "pending":
        return False
    if archive_status == "ready":
        archive_path = str(image.get("archive_path") or "").strip()
        if archive_path and Path(archive_path).exists():
            return False

    _persist_docker_archive_metadata(
        image_id,
        {
            "archive_status": "pending",
            "archive_error": None,
        },
    )
    thread = threading.Thread(
        target=_archive_docker_image,
        args=(image_id, reference),
        daemon=True,
    )
    thread.start()
    return True


def _run_docker_with_progress(
    cmd: list[str],
    progress_callback: callable,
    operation_name: str,
) -> tuple[int, str, str]:
    """Run a docker command and stream progress updates.

    Args:
        cmd: The docker command to run
        progress_callback: Function to call with status updates
        operation_name: Name of the operation for status messages

    Returns:
        Tuple of (return_code, stdout, stderr)
    """
    progress_callback(f"Starting {operation_name}...")

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    stdout_lines = []
    stderr_lines = []

    # Read output in real-time
    def read_stream(stream, lines_list, is_stderr=False):
        for line in iter(stream.readline, ''):
            line = line.strip()
            if line:
                lines_list.append(line)
                # Parse docker load progress messages
                if not is_stderr:
                    if "Loading layer" in line or "Loaded image" in line:
                        progress_callback(line)
                    elif line.startswith("sha256:"):
                        progress_callback(f"Processing layer: {line[:20]}...")

    # Run reading in threads to handle both streams
    stdout_thread = threading.Thread(target=read_stream, args=(process.stdout, stdout_lines, False))
    stderr_thread = threading.Thread(target=read_stream, args=(process.stderr, stderr_lines, True))

    stdout_thread.start()
    stderr_thread.start()

    # Wait for process to complete
    process.wait()
    stdout_thread.join()
    stderr_thread.join()

    return process.returncode, "\n".join(stdout_lines), "\n".join(stderr_lines)


@router.get("/load/{upload_id}/progress")
def get_upload_progress(
    upload_id: str,
    current_user: models.User = Depends(get_current_user),
):
    """Poll for upload progress."""
    progress = _get_progress(upload_id)
    if progress is None:
        raise HTTPException(status_code=404, detail="Upload not found")
    return progress


@router.post("/load")
def load_image(
    file: UploadFile = File(...),
    current_user: models.User = Depends(get_current_admin),
    stream: bool = Query(default=False, description="Stream progress updates via SSE"),
    background: bool = Query(default=False, description="Run in background with polling"),
):
    """Load a Docker image from a tar archive.

    If background=true, returns immediately with an upload_id for polling progress.
    If stream=true, returns Server-Sent Events with progress updates.
    Otherwise returns a JSON response when complete.
    Requires admin access.
    """

    if ResourceMonitor.check_disk_pressure() == PressureLevel.CRITICAL:
        raise HTTPException(status_code=507, detail="Insufficient disk space for upload")

    if background:
        # Background mode with polling
        import uuid
        upload_id = str(uuid.uuid4())[:8]
        filename = file.filename or "image.tar"
        content = file.file.read()
        file.file.close()

        _update_progress(upload_id, "starting", "Upload received, starting import...", 5)

        # Start background thread
        thread = threading.Thread(
            target=_load_image_background,
            args=(upload_id, filename, content),
            daemon=True
        )
        thread.start()

        return {"upload_id": upload_id, "status": "started"}

    if stream:
        # Read file content BEFORE returning StreamingResponse
        # because UploadFile gets closed after endpoint returns
        filename = file.filename or "image.tar"
        content = file.file.read()
        file.file.close()

        return StreamingResponse(
            _load_image_streaming(filename, content),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",  # Disable nginx buffering
            },
        )

    # Non-streaming mode (original behavior)
    return _load_image_sync(file)


@router.post("/upload/init", response_model=ImageChunkUploadInitResponse)
def init_chunk_upload(
    request: ImageChunkUploadInitRequest,
    current_user: models.User = Depends(get_current_admin),
):
    """Initialize a chunked upload for docker/qcow2 image files."""

    if ResourceMonitor.check_disk_pressure() == PressureLevel.CRITICAL:
        raise HTTPException(status_code=507, detail="Insufficient disk space for upload")

    kind = (request.kind or "").strip().lower()
    if kind not in {"docker", "qcow2"}:
        raise HTTPException(status_code=400, detail="Upload kind must be 'docker' or 'qcow2'")

    safe_filename = _sanitize_upload_filename(request.filename)
    if not safe_filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    if kind == "qcow2" and not _is_supported_qcow2_upload_filename(safe_filename):
        raise HTTPException(
            status_code=400,
            detail="Filename must end with .qcow2/.qcow/.img (optionally .gz)",
        )

    _cleanup_expired_chunk_upload_sessions()

    upload_id = str(uuid4())[:12]
    final_path = _chunk_upload_destination(kind, upload_id, safe_filename)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = final_path.parent / f".upload_{upload_id}.partial"

    if kind == "qcow2":
        manifest = load_manifest()
        resolved_filename = _resolved_qcow2_upload_filename(safe_filename) or safe_filename
        potential_id = f"qcow2:{resolved_filename}"
        if find_image_by_id(manifest, potential_id):
            raise HTTPException(
                status_code=409,
                detail=f"Image '{resolved_filename}' already exists in the library",
            )
        resolved_path = final_path.parent / resolved_filename
        if resolved_path.exists():
            raise HTTPException(
                status_code=409,
                detail=f"Image '{resolved_filename}' already exists on disk",
            )

    chunk_size = request.chunk_size or DEFAULT_CHUNK_SIZE
    total_chunks = (request.total_size + chunk_size - 1) // chunk_size

    with open(temp_path, "wb") as handle:
        handle.seek(request.total_size - 1)
        handle.write(b"\0")

    with _chunk_upload_lock:
        _chunk_upload_sessions[upload_id] = {
            "upload_id": upload_id,
            "kind": kind,
            "filename": safe_filename,
            "total_size": request.total_size,
            "chunk_size": chunk_size,
            "total_chunks": total_chunks,
            "bytes_received": 0,
            "chunks_received": [],
            "temp_path": str(temp_path),
            "final_path": str(final_path),
            "status": "uploading",
            "error_message": None,
            "auto_build": bool(request.auto_build),
            "auto_confirm": bool(request.auto_confirm),
            "user_id": str(current_user.id),
            "created_at": datetime.now(timezone.utc),
        }

    return ImageChunkUploadInitResponse(
        upload_id=upload_id,
        kind=kind,
        filename=safe_filename,
        total_size=request.total_size,
        chunk_size=chunk_size,
        total_chunks=total_chunks,
    )


@router.post("/upload/{upload_id}/chunk", response_model=ImageChunkUploadChunkResponse)
async def upload_chunk(
    upload_id: str,
    index: int = Query(..., description="Chunk index (0-based)"),
    chunk: UploadFile = File(..., description="Chunk data"),
    current_user: models.User = Depends(get_current_admin),
):
    """Upload one chunk to an active chunked upload session."""
    with _chunk_upload_lock:
        session = _chunk_upload_sessions.get(upload_id)
        if not session:
            raise HTTPException(status_code=404, detail="Upload session not found")
        if session["status"] != "uploading":
            raise HTTPException(status_code=400, detail=f"Upload is {session['status']}")
        session = dict(session)

    if index < 0 or index >= session["total_chunks"]:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid chunk index {index}. Valid range: 0-{session['total_chunks'] - 1}",
        )

    chunk_size = session["chunk_size"]
    offset = index * chunk_size
    expected_size = min(chunk_size, session["total_size"] - offset)

    chunk_data = await chunk.read()
    actual_size = len(chunk_data)
    if actual_size != expected_size:
        raise HTTPException(
            status_code=400,
            detail=f"Chunk size mismatch. Expected {expected_size}, got {actual_size}",
        )

    temp_path = Path(session["temp_path"])

    def _sync_write_chunk():
        with open(temp_path, "r+b") as handle:
            handle.seek(offset)
            handle.write(chunk_data)

    try:
        await asyncio.to_thread(_sync_write_chunk)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to write chunk: {exc}") from exc

    with _chunk_upload_lock:
        current = _chunk_upload_sessions.get(upload_id)
        if not current:
            raise HTTPException(status_code=404, detail="Upload session expired")
        if index not in current["chunks_received"]:
            current["chunks_received"].append(index)
            current["bytes_received"] += actual_size
        total_received = current["bytes_received"]
        chunks_received = len(current["chunks_received"])
        is_complete = chunks_received == current["total_chunks"]

    return ImageChunkUploadChunkResponse(
        upload_id=upload_id,
        chunk_index=index,
        bytes_received=actual_size,
        total_received=total_received,
        progress_percent=int((total_received / session["total_size"]) * 100),
        is_complete=is_complete,
    )


@router.get("/upload/{upload_id}", response_model=ImageChunkUploadStatusResponse)
def get_chunk_upload_status(
    upload_id: str,
    current_user: models.User = Depends(get_current_user),
):
    """Get the status of a chunked upload session."""
    with _chunk_upload_lock:
        session = _chunk_upload_sessions.get(upload_id)
        if not session:
            raise HTTPException(status_code=404, detail="Upload session not found")

        return ImageChunkUploadStatusResponse(
            upload_id=session["upload_id"],
            kind=session["kind"],
            filename=session["filename"],
            total_size=session["total_size"],
            bytes_received=session["bytes_received"],
            progress_percent=int((session["bytes_received"] / session["total_size"]) * 100),
            chunks_received=sorted(session["chunks_received"]),
            status=session["status"],
            error_message=session.get("error_message"),
            created_at=session["created_at"],
        )


@router.post("/upload/{upload_id}/complete", response_model=ImageChunkUploadCompleteResponse)
def complete_chunk_upload(
    upload_id: str,
    current_user: models.User = Depends(get_current_admin),
):
    """Finalize a chunked upload and trigger image processing."""
    with _chunk_upload_lock:
        session = _chunk_upload_sessions.get(upload_id)
        if not session:
            raise HTTPException(status_code=404, detail="Upload session not found")
        if session["status"] != "uploading":
            raise HTTPException(status_code=400, detail=f"Upload is {session['status']}")
        session = dict(session)

    received = set(session["chunks_received"])
    expected = set(range(session["total_chunks"]))
    missing = expected - received
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Missing chunks: {sorted(missing)[:10]}{'...' if len(missing) > 10 else ''}",
        )

    temp_path = Path(session["temp_path"])
    final_path = Path(session["final_path"])

    try:
        actual_size = temp_path.stat().st_size
        if actual_size != session["total_size"]:
            raise HTTPException(
                status_code=400,
                detail=f"File size mismatch. Expected {session['total_size']}, got {actual_size}",
            )
        shutil.move(str(temp_path), str(final_path))
    except HTTPException as exc:
        with _chunk_upload_lock:
            if upload_id in _chunk_upload_sessions:
                _chunk_upload_sessions[upload_id]["status"] = "failed"
                _chunk_upload_sessions[upload_id]["error_message"] = str(exc.detail)
        raise
    except OSError as exc:
        with _chunk_upload_lock:
            if upload_id in _chunk_upload_sessions:
                _chunk_upload_sessions[upload_id]["status"] = "failed"
                _chunk_upload_sessions[upload_id]["error_message"] = str(exc)
        raise HTTPException(status_code=500, detail=f"Failed to finalize upload: {exc}") from exc

    kind = session["kind"]
    if kind == "qcow2":
        with _chunk_upload_lock:
            if upload_id in _chunk_upload_sessions:
                _chunk_upload_sessions[upload_id]["status"] = "processing"

        auto_confirm = session.get("auto_confirm", True)

        if not auto_confirm:
            # Two-phase mode: detect only, store results, await confirmation.
            from .upload_vm import _detect_qcow2, _resolve_qcow2_upload_path
            resolved_final_path = _resolve_qcow2_upload_path(final_path)

            try:
                detection = _detect_qcow2(final_path)
            except Exception as exc:
                with _chunk_upload_lock:
                    if upload_id in _chunk_upload_sessions:
                        _chunk_upload_sessions[upload_id]["status"] = "failed"
                        _chunk_upload_sessions[upload_id]["error_message"] = str(exc)
                raise

            with _chunk_upload_lock:
                if upload_id in _chunk_upload_sessions:
                    _chunk_upload_sessions[upload_id]["status"] = "awaiting_confirmation"
                    _chunk_upload_sessions[upload_id]["final_path"] = str(resolved_final_path)
                    _chunk_upload_sessions[upload_id]["detection"] = detection

            return ImageChunkUploadCompleteResponse(
                upload_id=upload_id,
                kind=kind,
                filename=session["filename"],
                status="awaiting_confirmation",
                result=detection,
            )

        # Auto-confirm mode: detect + register in one shot (legacy behavior).
        from .upload_vm import _finalize_qcow2_upload

        try:
            result = _finalize_qcow2_upload(
                final_path,
                auto_build=bool(session.get("auto_build", True)),
            )
        except Exception as exc:
            with _chunk_upload_lock:
                if upload_id in _chunk_upload_sessions:
                    _chunk_upload_sessions[upload_id]["status"] = "failed"
                    _chunk_upload_sessions[upload_id]["error_message"] = str(exc)
            raise

        with _chunk_upload_lock:
            if upload_id in _chunk_upload_sessions:
                _chunk_upload_sessions[upload_id]["status"] = "completed"
                _chunk_upload_sessions[upload_id]["final_path"] = str(final_path)

        return ImageChunkUploadCompleteResponse(
            upload_id=upload_id,
            kind=kind,
            filename=session["filename"],
            status="completed",
            result=result,
        )

    _update_progress(upload_id, "starting", "Upload received, starting import...", 5)
    with _chunk_upload_lock:
        if upload_id in _chunk_upload_sessions:
            _chunk_upload_sessions[upload_id]["status"] = "processing"
            _chunk_upload_sessions[upload_id]["final_path"] = str(final_path)

    thread = threading.Thread(
        target=_load_image_background_from_archive,
        args=(upload_id, session["filename"], str(final_path), True),
        daemon=True,
    )
    thread.start()

    return ImageChunkUploadCompleteResponse(
        upload_id=upload_id,
        kind=kind,
        filename=session["filename"],
        status="processing",
        result={"upload_id": upload_id},
    )


@router.delete("/upload/{upload_id}")
def cancel_chunk_upload(
    upload_id: str,
    current_user: models.User = Depends(get_current_admin),
):
    """Cancel and clean up a chunked upload session."""
    with _chunk_upload_lock:
        session = _chunk_upload_sessions.pop(upload_id, None)

    if not session:
        raise HTTPException(status_code=404, detail="Upload session not found")

    session["status"] = "cancelled"
    _cleanup_chunk_upload_session_files(session)
    return {"message": "Upload cancelled"}


def _load_image_background(upload_id: str, filename: str, content: bytes):
    """Process image upload in background thread with progress updates."""
    print(f"[UPLOAD {upload_id}] Starting background processing for {filename}")
    suffixes = Path(filename).suffixes
    suffix = "".join(suffixes) if suffixes else ".tar"
    temp_path = ""
    load_path = ""
    decompressed_path = ""

    try:
        # Phase 1: Save to temp file
        print(f"[UPLOAD {upload_id}] Phase 1: Saving file")
        _update_progress(upload_id, "saving", f"Saving {filename}...", 10)

        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
            tmp_file.write(content)
            temp_path = tmp_file.name

        load_path = temp_path
        file_size = os.path.getsize(temp_path)
        print(f"[UPLOAD {upload_id}] File saved: {file_size} bytes, checking if decompression needed")
        _update_progress(upload_id, "saved", f"File saved ({_format_size(file_size)})", 30)

        # Phase 2: Decompress if needed
        print(f"[UPLOAD {upload_id}] Filename: {filename}, checking for .xz extension")
        if filename.lower().endswith((".tar.xz", ".txz", ".xz")):
            print(f"[UPLOAD {upload_id}] Phase 2: Decompressing XZ archive")
            _update_progress(upload_id, "decompressing", "Decompressing XZ archive...", 35)
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".tar") as tmp_tar:
                    with lzma.open(temp_path, "rb") as source:
                        shutil.copyfileobj(source, tmp_tar)
                    decompressed_path = tmp_tar.name
                load_path = decompressed_path
                decompressed_size = os.path.getsize(decompressed_path)
                print(f"[UPLOAD {upload_id}] Decompression complete: {decompressed_size} bytes")
                _update_progress(upload_id, "decompressed", "Decompression complete", 50)
            except lzma.LZMAError as exc:
                print(f"[UPLOAD {upload_id}] Decompression failed: {exc}")
                _update_progress(upload_id, "error", f"Decompression failed: {exc}", 0, error=True)
                return
        else:
            print(f"[UPLOAD {upload_id}] No decompression needed")

        # Phase 3: Detect format
        print(f"[UPLOAD {upload_id}] Phase 3: Detecting format of {load_path}")
        _update_progress(upload_id, "detecting", "Detecting image format...", 55)
        is_docker_image = _is_docker_image_tar(load_path)
        print(f"[UPLOAD {upload_id}] Is docker image: {is_docker_image}")
        loaded_images = []

        # Phase 4: Import
        if is_docker_image:
            print(f"[UPLOAD {upload_id}] Phase 4: Running docker load")
            _update_progress(upload_id, "loading", "Running docker load...", 60)
            try:
                result = subprocess.run(
                    ["docker", "load", "-i", load_path],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=600,  # 10 minute timeout
                )
                print(f"[UPLOAD {upload_id}] docker load returned: {result.returncode}")
            except subprocess.TimeoutExpired:
                print(f"[UPLOAD {upload_id}] docker load timed out after 600 seconds")
                _update_progress(upload_id, "error", "docker load timed out", 0, error=True)
                return
            output = (result.stdout or "") + (result.stderr or "")
            if result.returncode != 0:
                _update_progress(upload_id, "error", output.strip() or "docker load failed", 0, error=True)
                return
            for line in output.splitlines():
                if "Loaded image:" in line:
                    loaded_images.append(line.split("Loaded image:", 1)[-1].strip())
                elif "Loaded image ID:" in line:
                    loaded_images.append(line.split("Loaded image ID:", 1)[-1].strip())
        else:
            base_name = Path(filename).stem
            for ext in [".tar", ".gz", ".xz"]:
                if base_name.lower().endswith(ext):
                    base_name = base_name[:-len(ext)]
            image_name = base_name.lower().replace(" ", "-").replace("_", "-")
            image_tag = f"{image_name}:imported"

            print(f"[UPLOAD {upload_id}] Phase 4: Importing as {image_tag}", flush=True)
            _update_progress(upload_id, "importing", f"Importing as {image_tag}...", 60)

            try:
                # Use file-based output capture to avoid pipe deadlocks in daemon threads
                import sys
                print(f"[UPLOAD {upload_id}] Starting docker import subprocess", flush=True)
                sys.stdout.flush()
                sys.stderr.flush()

                # Create temp files for output capture (avoids pipe blocking)
                with tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.out') as stdout_file:
                    with tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.err') as stderr_file:
                        stdout_path = stdout_file.name
                        stderr_path = stderr_file.name

                try:
                    with open(stdout_path, 'w') as stdout_f, open(stderr_path, 'w') as stderr_f:
                        print(f"[UPLOAD {upload_id}] Running: docker import {load_path} {image_tag}", flush=True)
                        result = subprocess.run(
                            ["docker", "import", load_path, image_tag],
                            stdout=stdout_f,
                            stderr=stderr_f,
                            timeout=600,
                        )
                        result_returncode = result.returncode

                    # Read output from files
                    with open(stdout_path, 'r') as f:
                        stdout_content = f.read()
                    with open(stderr_path, 'r') as f:
                        stderr_content = f.read()
                    output = stdout_content + stderr_content
                    print(f"[UPLOAD {upload_id}] docker import returned: {result_returncode}", flush=True)
                finally:
                    # Clean up temp output files
                    for p in [stdout_path, stderr_path]:
                        if os.path.exists(p):
                            os.unlink(p)

            except subprocess.TimeoutExpired:
                print(f"[UPLOAD {upload_id}] docker import timed out after 600 seconds", flush=True)
                _update_progress(upload_id, "error", "docker import timed out", 0, error=True)
                return
            except Exception as e:
                import traceback
                print(f"[UPLOAD {upload_id}] docker import exception: {e}", flush=True)
                traceback.print_exc()
                _update_progress(upload_id, "error", f"docker import failed: {e}", 0, error=True)
                return
            if result_returncode != 0:
                print(f"[UPLOAD {upload_id}] docker import failed: {output}", flush=True)
                _update_progress(upload_id, "error", output.strip() or "docker import failed", 0, error=True)
                return
            loaded_images.append(image_tag)
            output = f"Imported as {image_tag}"
            print(f"[UPLOAD {upload_id}] Import successful: {image_tag}")

        if not loaded_images:
            print(f"[UPLOAD {upload_id}] No images detected")
            _update_progress(upload_id, "error", "No images detected", 0, error=True)
            return

        # Phase 5: Update manifest
        print(f"[UPLOAD {upload_id}] Phase 5: Updating manifest")
        _update_progress(upload_id, "finalizing", "Updating image library...", 95)

        manifest = load_manifest()
        for image_ref in loaded_images:
            potential_id = f"docker:{image_ref}"
            if find_image_by_id(manifest, potential_id):
                print(f"[UPLOAD {upload_id}] Image already exists: {image_ref}")
                _update_progress(upload_id, "error", f"Image {image_ref} already exists", 0, error=True)
                return

        for image_ref in loaded_images:
            device_id, version = detect_device_from_filename(image_ref)
            entry = create_image_entry(
                image_id=f"docker:{image_ref}",
                kind="docker",
                reference=image_ref,
                filename=filename,
                device_id=device_id,
                version=version,
                size_bytes=file_size,
            )
            manifest["images"].append(entry)
        save_manifest(manifest)
        _queue_docker_archive_creation(loaded_images)

        print(f"[UPLOAD {upload_id}] Complete! Setting progress to 100%")
        _update_progress(upload_id, "complete", output or "Image loaded successfully", 100,
                        images=loaded_images, complete=True)
        print(f"[UPLOAD {upload_id}] Progress updated to complete")

    except Exception as e:
        import traceback
        print(f"[UPLOAD {upload_id}] Exception: {e}")
        traceback.print_exc()
        _update_progress(upload_id, "error", str(e), 0, error=True)
    finally:
        if decompressed_path and os.path.exists(decompressed_path):
            os.unlink(decompressed_path)
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def _load_image_background_from_archive(
    upload_id: str,
    filename: str,
    archive_path: str,
    cleanup_archive: bool = True,
):
    """Process an uploaded Docker archive already staged on disk."""
    temp_path = archive_path
    load_path = temp_path
    decompressed_path = ""

    try:
        if not os.path.exists(temp_path):
            _update_progress(upload_id, "error", "Staged upload file no longer exists", 0, error=True)
            with _chunk_upload_lock:
                if upload_id in _chunk_upload_sessions:
                    _chunk_upload_sessions[upload_id]["status"] = "failed"
                    _chunk_upload_sessions[upload_id]["error_message"] = "Staged upload file not found"
            return

        file_size = os.path.getsize(temp_path)
        _update_progress(upload_id, "saved", f"File saved ({_format_size(file_size)})", 30)

        if filename.lower().endswith((".tar.xz", ".txz", ".xz")):
            _update_progress(upload_id, "decompressing", "Decompressing XZ archive...", 35)
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".tar") as tmp_tar:
                    with lzma.open(temp_path, "rb") as source:
                        shutil.copyfileobj(source, tmp_tar)
                    decompressed_path = tmp_tar.name
                load_path = decompressed_path
                _update_progress(upload_id, "decompressed", "Decompression complete", 50)
            except lzma.LZMAError as exc:
                _update_progress(upload_id, "error", f"Decompression failed: {exc}", 0, error=True)
                with _chunk_upload_lock:
                    if upload_id in _chunk_upload_sessions:
                        _chunk_upload_sessions[upload_id]["status"] = "failed"
                        _chunk_upload_sessions[upload_id]["error_message"] = str(exc)
                return

        _update_progress(upload_id, "detecting", "Detecting image format...", 55)
        is_docker_image = _is_docker_image_tar(load_path)
        loaded_images: list[str] = []

        if is_docker_image:
            _update_progress(upload_id, "loading", "Running docker load...", 60)
            try:
                result = subprocess.run(
                    ["docker", "load", "-i", load_path],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=600,
                )
            except subprocess.TimeoutExpired:
                _update_progress(upload_id, "error", "docker load timed out", 0, error=True)
                with _chunk_upload_lock:
                    if upload_id in _chunk_upload_sessions:
                        _chunk_upload_sessions[upload_id]["status"] = "failed"
                        _chunk_upload_sessions[upload_id]["error_message"] = "docker load timed out"
                return

            output = (result.stdout or "") + (result.stderr or "")
            if result.returncode != 0:
                _update_progress(upload_id, "error", output.strip() or "docker load failed", 0, error=True)
                with _chunk_upload_lock:
                    if upload_id in _chunk_upload_sessions:
                        _chunk_upload_sessions[upload_id]["status"] = "failed"
                        _chunk_upload_sessions[upload_id]["error_message"] = output.strip() or "docker load failed"
                return

            for line in output.splitlines():
                if "Loaded image:" in line:
                    loaded_images.append(line.split("Loaded image:", 1)[-1].strip())
                elif "Loaded image ID:" in line:
                    loaded_images.append(line.split("Loaded image ID:", 1)[-1].strip())
        else:
            base_name = Path(filename).stem
            for ext in [".tar", ".gz", ".xz"]:
                if base_name.lower().endswith(ext):
                    base_name = base_name[:-len(ext)]
            image_name = base_name.lower().replace(" ", "-").replace("_", "-")
            image_tag = f"{image_name}:imported"
            _update_progress(upload_id, "importing", f"Importing as {image_tag}...", 60)

            try:
                with tempfile.NamedTemporaryFile(mode="w+", delete=False, suffix=".out") as stdout_file:
                    with tempfile.NamedTemporaryFile(mode="w+", delete=False, suffix=".err") as stderr_file:
                        stdout_path = stdout_file.name
                        stderr_path = stderr_file.name

                try:
                    with open(stdout_path, "w") as stdout_f, open(stderr_path, "w") as stderr_f:
                        result = subprocess.run(
                            ["docker", "import", load_path, image_tag],
                            stdout=stdout_f,
                            stderr=stderr_f,
                            timeout=600,
                        )
                        result_returncode = result.returncode

                    with open(stdout_path, "r") as stdout_f:
                        stdout_content = stdout_f.read()
                    with open(stderr_path, "r") as stderr_f:
                        stderr_content = stderr_f.read()
                    output = stdout_content + stderr_content
                finally:
                    for path in [stdout_path, stderr_path]:
                        if os.path.exists(path):
                            os.unlink(path)
            except subprocess.TimeoutExpired:
                _update_progress(upload_id, "error", "docker import timed out", 0, error=True)
                with _chunk_upload_lock:
                    if upload_id in _chunk_upload_sessions:
                        _chunk_upload_sessions[upload_id]["status"] = "failed"
                        _chunk_upload_sessions[upload_id]["error_message"] = "docker import timed out"
                return
            except Exception as exc:
                _update_progress(upload_id, "error", f"docker import failed: {exc}", 0, error=True)
                with _chunk_upload_lock:
                    if upload_id in _chunk_upload_sessions:
                        _chunk_upload_sessions[upload_id]["status"] = "failed"
                        _chunk_upload_sessions[upload_id]["error_message"] = str(exc)
                return

            if result_returncode != 0:
                _update_progress(upload_id, "error", output.strip() or "docker import failed", 0, error=True)
                with _chunk_upload_lock:
                    if upload_id in _chunk_upload_sessions:
                        _chunk_upload_sessions[upload_id]["status"] = "failed"
                        _chunk_upload_sessions[upload_id]["error_message"] = output.strip() or "docker import failed"
                return

            loaded_images.append(image_tag)
            output = f"Imported as {image_tag}"

        if not loaded_images:
            _update_progress(upload_id, "error", "No images detected", 0, error=True)
            with _chunk_upload_lock:
                if upload_id in _chunk_upload_sessions:
                    _chunk_upload_sessions[upload_id]["status"] = "failed"
                    _chunk_upload_sessions[upload_id]["error_message"] = "No images detected"
            return

        _update_progress(upload_id, "finalizing", "Updating image library...", 95)
        manifest = load_manifest()
        for image_ref in loaded_images:
            potential_id = f"docker:{image_ref}"
            if find_image_by_id(manifest, potential_id):
                _update_progress(upload_id, "error", f"Image {image_ref} already exists", 0, error=True)
                with _chunk_upload_lock:
                    if upload_id in _chunk_upload_sessions:
                        _chunk_upload_sessions[upload_id]["status"] = "failed"
                        _chunk_upload_sessions[upload_id]["error_message"] = f"Image {image_ref} already exists"
                return

        for image_ref in loaded_images:
            device_id, version = detect_device_from_filename(image_ref)
            entry = create_image_entry(
                image_id=f"docker:{image_ref}",
                kind="docker",
                reference=image_ref,
                filename=filename,
                device_id=device_id,
                version=version,
                size_bytes=file_size,
            )
            manifest["images"].append(entry)
        save_manifest(manifest)
        _queue_docker_archive_creation(loaded_images)

        _update_progress(
            upload_id,
            "complete",
            output or "Image loaded successfully",
            100,
            images=loaded_images,
            complete=True,
        )
        with _chunk_upload_lock:
            if upload_id in _chunk_upload_sessions:
                _chunk_upload_sessions[upload_id]["status"] = "completed"
                _chunk_upload_sessions[upload_id]["error_message"] = None
    except Exception as exc:
        _update_progress(upload_id, "error", str(exc), 0, error=True)
        with _chunk_upload_lock:
            if upload_id in _chunk_upload_sessions:
                _chunk_upload_sessions[upload_id]["status"] = "failed"
                _chunk_upload_sessions[upload_id]["error_message"] = str(exc)
    finally:
        if decompressed_path and os.path.exists(decompressed_path):
            os.unlink(decompressed_path)
        if cleanup_archive and temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


async def _load_image_streaming(filename: str, content: bytes):
    """Stream image loading progress via Server-Sent Events.

    Args:
        filename: Original filename of the uploaded file
        content: File content as bytes (already read from UploadFile)
    """
    suffixes = Path(filename).suffixes
    suffix = "".join(suffixes) if suffixes else ".tar"
    temp_path = ""
    load_path = ""
    decompressed_path = ""

    try:
        # Phase 1: Save uploaded file to temp (run in thread to avoid blocking event loop)
        total_size = len(content)
        yield _send_sse_event("progress", {
            "phase": "saving",
            "message": f"Processing uploaded file: {filename} ({_format_size(total_size)})...",
            "percent": 5,
        })

        # Write content to temp file in a thread to avoid blocking
        temp_path, file_size = await asyncio.to_thread(
            _write_content_to_tempfile, content, suffix
        )
        load_path = temp_path

        yield _send_sse_event("progress", {
            "phase": "saved",
            "message": f"File saved ({_format_size(file_size)}). Checking format...",
            "percent": 30,
        })

        # Phase 2: Decompress if needed (run in thread to avoid blocking event loop)
        if filename.lower().endswith((".tar.xz", ".txz", ".xz")):
            yield _send_sse_event("progress", {
                "phase": "decompressing",
                "message": "Decompressing XZ archive (this may take a while for large files)...",
                "percent": 35,
            })

            try:
                # Decompress in a thread to avoid blocking the event loop
                decompressed_path, decompressed_size = await asyncio.to_thread(
                    _decompress_xz_file, temp_path
                )
                load_path = decompressed_path

                yield _send_sse_event("progress", {
                    "phase": "decompressed",
                    "message": f"Decompression complete ({_format_size(decompressed_size)})",
                    "percent": 50,
                })
            except lzma.LZMAError as exc:
                yield _send_sse_event("error", {
                    "message": f"Failed to decompress archive: {exc}",
                })
                return

        # Phase 3: Detect format (run in thread to avoid blocking event loop)
        yield _send_sse_event("progress", {
            "phase": "detecting",
            "message": "Detecting image format...",
            "percent": 55,
        })

        is_docker_image = await asyncio.to_thread(_is_docker_image_tar, load_path)
        loaded_images = []
        output = ""

        # Phase 4: Load into Docker
        if is_docker_image:
            yield _send_sse_event("progress", {
                "phase": "loading",
                "message": "Docker image detected. Running 'docker load' (this may take several minutes)...",
                "percent": 60,
            })

            # Run docker load with async subprocess for progress tracking
            proc = await asyncio.create_subprocess_exec(
                "docker", "load", "-i", load_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )

            output_lines = []
            layer_count = 0

            # Read output line by line asynchronously
            while True:
                line_bytes = await proc.stdout.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode().strip()
                if line:
                    output_lines.append(line)
                    if "Loading layer" in line:
                        layer_count += 1
                        yield _send_sse_event("progress", {
                            "phase": "loading",
                            "message": f"Loading layer {layer_count}...",
                            "detail": line,
                            "percent": min(95, 60 + layer_count * 3),
                        })
                    elif "Loaded image:" in line:
                        image_name = line.split("Loaded image:", 1)[-1].strip()
                        loaded_images.append(image_name)
                        yield _send_sse_event("progress", {
                            "phase": "loading",
                            "message": f"Loaded: {image_name}",
                            "percent": 95,
                        })
                    elif "Loaded image ID:" in line:
                        image_id = line.split("Loaded image ID:", 1)[-1].strip()
                        loaded_images.append(image_id)

            await proc.wait()
            output = "\n".join(output_lines)

            if proc.returncode != 0:
                yield _send_sse_event("error", {
                    "message": output.strip() or "docker load failed",
                })
                return
        else:
            # Raw filesystem tar (e.g., cEOS) - use docker import
            base_name = Path(filename).stem
            for ext in [".tar", ".gz", ".xz"]:
                if base_name.lower().endswith(ext):
                    base_name = base_name[:-len(ext)]
            image_name = base_name.lower().replace(" ", "-").replace("_", "-")
            image_tag = f"{image_name}:imported"

            yield _send_sse_event("progress", {
                "phase": "importing",
                "message": f"Filesystem archive detected. Importing as '{image_tag}'...",
                "percent": 60,
            })
            await asyncio.sleep(0)  # Yield to event loop

            print(f"[SSE] Starting docker import for {image_tag}")

            # Use async subprocess to avoid blocking the event loop
            proc = await asyncio.create_subprocess_exec(
                "docker", "import", load_path, image_tag,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await proc.communicate()
            output = (stdout_bytes.decode() if stdout_bytes else "") + (stderr_bytes.decode() if stderr_bytes else "")

            print(f"[SSE] Docker import completed, returncode={proc.returncode}")

            if proc.returncode != 0:
                print(f"[SSE] Docker import failed: {output}")
                yield _send_sse_event("error", {
                    "message": output.strip() or "docker import failed",
                })
                return

            image_id = output.strip().split(":")[-1][:12] if output.strip() else ""
            loaded_images.append(image_tag)
            output = f"Imported filesystem as {image_tag} (ID: {image_id})"

            print(f"[SSE] Import successful: {image_tag}, sending progress event")
            yield _send_sse_event("progress", {
                "phase": "imported",
                "message": f"Import complete: {image_tag}",
                "percent": 95,
            })
            await asyncio.sleep(0)  # Yield to event loop

        if not loaded_images:
            yield _send_sse_event("error", {
                "message": output.strip() or "No images detected in archive",
            })
            return

        # Phase 5: Update manifest
        yield _send_sse_event("progress", {
            "phase": "finalizing",
            "message": "Updating image library...",
            "percent": 98,
        })

        manifest = load_manifest()

        # Check for duplicates
        for image_ref in loaded_images:
            potential_id = f"docker:{image_ref}"
            if find_image_by_id(manifest, potential_id):
                yield _send_sse_event("error", {
                    "message": f"Image '{image_ref}' already exists in the library",
                })
                return

        for image_ref in loaded_images:
            device_id, version = detect_device_from_filename(image_ref)
            entry = create_image_entry(
                image_id=f"docker:{image_ref}",
                kind="docker",
                reference=image_ref,
                filename=filename,
                device_id=device_id,
                version=version,
                size_bytes=file_size,
            )
            manifest["images"].append(entry)
        save_manifest(manifest)
        _queue_docker_archive_creation(loaded_images)

        # Final success event - add small delay to ensure proper flushing
        await asyncio.sleep(0.1)
        yield _send_sse_event("complete", {
            "message": output.strip() or "Image loaded successfully",
            "images": loaded_images,
            "percent": 100,
        })
        # Extra newline to ensure event boundary
        yield "\n"

    except Exception as e:
        import traceback
        traceback.print_exc()
        yield _send_sse_event("error", {
            "message": str(e),
        })
        yield "\n"
    finally:
        # Clean up temp files (run in thread to avoid blocking event loop)
        await asyncio.to_thread(_cleanup_temp_files, decompressed_path, temp_path)


def _load_image_sync(file: UploadFile) -> dict:
    """Original synchronous image loading (non-streaming)."""
    filename = file.filename or "image.tar"
    suffixes = Path(filename).suffixes
    suffix = "".join(suffixes) if suffixes else ".tar"
    temp_path = ""
    load_path = ""
    decompressed_path = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
            shutil.copyfileobj(file.file, tmp_file)
            temp_path = tmp_file.name
        load_path = temp_path
        if filename.lower().endswith((".tar.xz", ".txz", ".xz")):
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".tar") as tmp_tar:
                    with lzma.open(temp_path, "rb") as source:
                        shutil.copyfileobj(source, tmp_tar)
                    decompressed_path = tmp_tar.name
                load_path = decompressed_path
            except lzma.LZMAError as exc:
                raise HTTPException(status_code=400, detail=f"Failed to decompress archive: {exc}") from exc

        # Detect tar format and use appropriate command
        is_docker_image = _is_docker_image_tar(load_path)
        loaded_images = []

        if is_docker_image:
            # Standard Docker image from `docker save`
            result = subprocess.run(
                ["docker", "load", "-i", load_path],
                capture_output=True,
                text=True,
                check=False,
            )
            output = (result.stdout or "") + (result.stderr or "")
            if result.returncode != 0:
                raise HTTPException(status_code=500, detail=output.strip() or "docker load failed")
            for line in output.splitlines():
                if "Loaded image:" in line:
                    loaded_images.append(line.split("Loaded image:", 1)[-1].strip())
                elif "Loaded image ID:" in line:
                    loaded_images.append(line.split("Loaded image ID:", 1)[-1].strip())
        else:
            # Raw filesystem tar (e.g., cEOS) - use docker import
            # Derive image name from filename
            base_name = Path(filename).stem
            # Remove common extensions that might remain
            for ext in [".tar", ".gz", ".xz"]:
                if base_name.lower().endswith(ext):
                    base_name = base_name[:-len(ext)]
            # Create a clean image name
            image_name = base_name.lower().replace(" ", "-").replace("_", "-")
            image_tag = f"{image_name}:imported"

            result = subprocess.run(
                ["docker", "import", load_path, image_tag],
                capture_output=True,
                text=True,
                check=False,
            )
            output = (result.stdout or "") + (result.stderr or "")
            if result.returncode != 0:
                raise HTTPException(status_code=500, detail=output.strip() or "docker import failed")
            # docker import outputs the image ID
            image_id = output.strip().split(":")[-1][:12] if output.strip() else ""
            loaded_images.append(image_tag)
            output = f"Imported filesystem as {image_tag} (ID: {image_id})"

        if not loaded_images:
            raise HTTPException(status_code=500, detail=output.strip() or "No images detected in archive")

        # Get file size if available
        file_size = None
        if temp_path and os.path.exists(temp_path):
            file_size = os.path.getsize(temp_path)

        manifest = load_manifest()

        # Check for duplicate images before adding
        for image_ref in loaded_images:
            potential_id = f"docker:{image_ref}"
            if find_image_by_id(manifest, potential_id):
                raise HTTPException(
                    status_code=409,
                    detail=f"Image '{image_ref}' already exists in the library"
                )

        for image_ref in loaded_images:
            device_id, version = detect_device_from_filename(image_ref)
            entry = create_image_entry(
                image_id=f"docker:{image_ref}",
                kind="docker",
                reference=image_ref,
                filename=filename,
                device_id=device_id,
                version=version,
                size_bytes=file_size,
            )
            manifest["images"].append(entry)
        save_manifest(manifest)
        _queue_docker_archive_creation(loaded_images)
        return {"output": output.strip() or "Image loaded", "images": loaded_images}
    finally:
        file.file.close()
        if decompressed_path and os.path.exists(decompressed_path):
            os.unlink(decompressed_path)
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
