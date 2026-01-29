"""ISO image scanning and import endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import db, models
from app.auth import get_current_user
from app.config import settings
from app.image_store import (
    add_custom_device,
    create_image_entry,
    ensure_image_store,
    find_custom_device,
    find_image_by_id,
    load_manifest,
    qcow2_path,
    save_manifest,
)
from app.iso import (
    ISOExtractor,
    ISOManifest,
    ISOSession,
    ImageImportProgress,
    ParsedImage,
    ParsedNodeDefinition,
)
from app.iso.extractor import check_7z_available
from app.iso.mapper import get_image_device_mapping
from app.iso.parser import ParserRegistry

router = APIRouter(prefix="/iso", tags=["iso"])
logger = logging.getLogger(__name__)

# In-memory session storage (could be Redis for production)
_sessions: dict[str, ISOSession] = {}
_session_lock = threading.Lock()

# Chunked upload session storage
_upload_sessions: dict[str, dict] = {}
_upload_lock = threading.Lock()

# Default chunk size: 10MB
DEFAULT_CHUNK_SIZE = 10 * 1024 * 1024


def _get_session(session_id: str) -> ISOSession | None:
    """Get a session by ID."""
    with _session_lock:
        return _sessions.get(session_id)


def _save_session(session: ISOSession):
    """Save a session."""
    with _session_lock:
        _sessions[session.id] = session


def _delete_session(session_id: str):
    """Delete a session."""
    with _session_lock:
        _sessions.pop(session_id, None)


# --- Request/Response Models ---


class ISOFileInfo(BaseModel):
    """Information about an ISO file in the upload directory."""
    name: str
    path: str
    size_bytes: int
    modified_at: datetime


class BrowseResponse(BaseModel):
    """Response from browsing ISO upload directory."""
    upload_dir: str
    files: list[ISOFileInfo]


class ScanRequest(BaseModel):
    """Request to scan an ISO file."""
    iso_path: str = Field(..., description="Filesystem path to ISO file")


class ScanResponse(BaseModel):
    """Response from scanning an ISO."""
    session_id: str
    iso_path: str
    format: str
    size_bytes: int
    node_definitions: list[dict]
    images: list[dict]
    parse_errors: list[str]


class ImportRequest(BaseModel):
    """Request to import images from an ISO."""
    image_ids: list[str] = Field(..., description="Image IDs to import")
    create_devices: bool = Field(default=True, description="Create device types for unknown definitions")


class ImportProgressResponse(BaseModel):
    """Progress response for import operation."""
    session_id: str
    status: str
    progress_percent: int
    error_message: str | None = None
    image_progress: dict[str, dict]
    completed_images: list[str]
    failed_images: list[str]


class SessionInfoResponse(BaseModel):
    """Information about an ISO session."""
    session_id: str
    iso_path: str
    status: str
    progress_percent: int
    error_message: str | None = None
    selected_images: list[str]
    manifest: dict | None = None
    created_at: datetime
    updated_at: datetime


# --- Upload Models ---


class UploadInitRequest(BaseModel):
    """Request to initialize a chunked upload."""
    filename: str = Field(..., description="Name of the ISO file")
    total_size: int = Field(..., description="Total file size in bytes")
    chunk_size: int = Field(default=DEFAULT_CHUNK_SIZE, description="Size of each chunk")


class UploadInitResponse(BaseModel):
    """Response from initializing an upload."""
    upload_id: str
    filename: str
    total_size: int
    chunk_size: int
    total_chunks: int
    upload_path: str


class UploadChunkResponse(BaseModel):
    """Response from uploading a chunk."""
    upload_id: str
    chunk_index: int
    bytes_received: int
    total_received: int
    progress_percent: int
    is_complete: bool


class UploadStatusResponse(BaseModel):
    """Status of an upload session."""
    upload_id: str
    filename: str
    total_size: int
    bytes_received: int
    progress_percent: int
    chunks_received: list[int]
    status: str
    error_message: str | None = None
    iso_path: str | None = None
    created_at: datetime


class UploadCompleteResponse(BaseModel):
    """Response from completing an upload."""
    upload_id: str
    filename: str
    iso_path: str
    total_size: int
    md5_hash: str | None = None


# --- Endpoints ---


@router.get("/browse", response_model=BrowseResponse)
async def browse_iso_files(
    current_user: models.User = Depends(get_current_user),
):
    """List ISO files in the upload directory.

    Returns all .iso files found in the configured upload directory.
    Users can copy ISOs to this directory via SFTP/SCP for import.

    Upload directory: /var/lib/archetype-gui/uploads/
    """
    upload_dir = Path(settings.iso_upload_dir)

    # Create directory if it doesn't exist
    upload_dir.mkdir(parents=True, exist_ok=True)

    files: list[ISOFileInfo] = []

    try:
        for entry in upload_dir.iterdir():
            if entry.is_file() and entry.suffix.lower() == ".iso":
                stat = entry.stat()
                files.append(ISOFileInfo(
                    name=entry.name,
                    path=str(entry),
                    size_bytes=stat.st_size,
                    modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                ))

        # Sort by modification time, newest first
        files.sort(key=lambda f: f.modified_at, reverse=True)

    except PermissionError:
        raise HTTPException(
            status_code=500,
            detail=f"Cannot read upload directory: {upload_dir}"
        )

    return BrowseResponse(
        upload_dir=str(upload_dir),
        files=files,
    )


# --- Chunked Upload Endpoints ---


@router.post("/upload/init", response_model=UploadInitResponse)
async def init_upload(
    request: UploadInitRequest,
    current_user: models.User = Depends(get_current_user),
):
    """Initialize a chunked upload session.

    For large ISO files (15GB+), use chunked upload to avoid timeouts.
    This returns an upload_id that should be used for subsequent chunk uploads.

    Typical workflow:
    1. POST /iso/upload/init - get upload_id
    2. POST /iso/upload/{upload_id}/chunk?index=0 - upload first chunk
    3. POST /iso/upload/{upload_id}/chunk?index=1 - upload second chunk
    4. ...continue for all chunks...
    5. POST /iso/upload/{upload_id}/complete - finalize upload
    6. POST /iso/scan - scan the completed ISO
    """
    # Validate filename
    if not request.filename.lower().endswith(".iso"):
        raise HTTPException(status_code=400, detail="Filename must end with .iso")

    # Sanitize filename
    safe_filename = "".join(
        c for c in request.filename
        if c.isalnum() or c in "._-"
    )
    if not safe_filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    upload_dir = Path(settings.iso_upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)

    upload_id = str(uuid4())[:12]
    dest_path = upload_dir / safe_filename

    # Check if file already exists
    if dest_path.exists():
        # Add timestamp suffix to avoid collision
        stem = dest_path.stem
        suffix = dest_path.suffix
        timestamp = int(time.time())
        safe_filename = f"{stem}_{timestamp}{suffix}"
        dest_path = upload_dir / safe_filename

    # Calculate chunks
    chunk_size = request.chunk_size or DEFAULT_CHUNK_SIZE
    total_chunks = (request.total_size + chunk_size - 1) // chunk_size

    # Create upload session
    temp_path = upload_dir / f".upload_{upload_id}.partial"

    with _upload_lock:
        _upload_sessions[upload_id] = {
            "upload_id": upload_id,
            "filename": safe_filename,
            "total_size": request.total_size,
            "chunk_size": chunk_size,
            "total_chunks": total_chunks,
            "bytes_received": 0,
            "chunks_received": [],
            "temp_path": str(temp_path),
            "final_path": str(dest_path),
            "status": "uploading",
            "error_message": None,
            "user_id": str(current_user.id),
            "created_at": datetime.utcnow(),
        }

    # Pre-allocate file (sparse)
    with open(temp_path, "wb") as f:
        f.seek(request.total_size - 1)
        f.write(b"\0")

    logger.info(f"Upload session {upload_id} initialized for {safe_filename} ({request.total_size} bytes)")

    return UploadInitResponse(
        upload_id=upload_id,
        filename=safe_filename,
        total_size=request.total_size,
        chunk_size=chunk_size,
        total_chunks=total_chunks,
        upload_path=str(dest_path),
    )


@router.post("/upload/{upload_id}/chunk", response_model=UploadChunkResponse)
async def upload_chunk(
    upload_id: str,
    index: int = Query(..., description="Chunk index (0-based)"),
    chunk: UploadFile = File(..., description="Chunk data"),
    current_user: models.User = Depends(get_current_user),
):
    """Upload a single chunk of the ISO file.

    Chunks can be uploaded in any order and can be retried on failure.
    Each chunk is written to the correct offset in the destination file.
    """
    with _upload_lock:
        session = _upload_sessions.get(upload_id)
        if not session:
            raise HTTPException(status_code=404, detail="Upload session not found")
        if session["status"] != "uploading":
            raise HTTPException(status_code=400, detail=f"Upload is {session['status']}")
        # Make a copy to avoid holding lock during I/O
        session = dict(session)

    # Validate chunk index
    if index < 0 or index >= session["total_chunks"]:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid chunk index {index}. Valid range: 0-{session['total_chunks']-1}"
        )

    # Calculate offset and expected size
    chunk_size = session["chunk_size"]
    offset = index * chunk_size
    expected_size = min(chunk_size, session["total_size"] - offset)

    # Read chunk data
    chunk_data = await chunk.read()
    actual_size = len(chunk_data)

    if actual_size != expected_size:
        raise HTTPException(
            status_code=400,
            detail=f"Chunk size mismatch. Expected {expected_size}, got {actual_size}"
        )

    # Write chunk to file at correct offset
    temp_path = Path(session["temp_path"])
    try:
        with open(temp_path, "r+b") as f:
            f.seek(offset)
            f.write(chunk_data)
    except IOError as e:
        logger.error(f"Failed to write chunk {index} for upload {upload_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to write chunk: {e}")

    # Update session
    with _upload_lock:
        if upload_id not in _upload_sessions:
            raise HTTPException(status_code=404, detail="Upload session expired")

        if index not in _upload_sessions[upload_id]["chunks_received"]:
            _upload_sessions[upload_id]["chunks_received"].append(index)
            _upload_sessions[upload_id]["bytes_received"] += actual_size

        total_received = _upload_sessions[upload_id]["bytes_received"]
        chunks_received = len(_upload_sessions[upload_id]["chunks_received"])
        is_complete = chunks_received == session["total_chunks"]

    progress_percent = int((total_received / session["total_size"]) * 100)

    logger.debug(f"Upload {upload_id}: chunk {index} received ({actual_size} bytes, {progress_percent}% complete)")

    return UploadChunkResponse(
        upload_id=upload_id,
        chunk_index=index,
        bytes_received=actual_size,
        total_received=total_received,
        progress_percent=progress_percent,
        is_complete=is_complete,
    )


@router.get("/upload/{upload_id}", response_model=UploadStatusResponse)
async def get_upload_status(
    upload_id: str,
    current_user: models.User = Depends(get_current_user),
):
    """Get the current status of an upload session.

    Use this to check progress or resume an interrupted upload.
    The chunks_received list shows which chunks have been successfully uploaded.
    """
    with _upload_lock:
        session = _upload_sessions.get(upload_id)
        if not session:
            raise HTTPException(status_code=404, detail="Upload session not found")

        return UploadStatusResponse(
            upload_id=session["upload_id"],
            filename=session["filename"],
            total_size=session["total_size"],
            bytes_received=session["bytes_received"],
            progress_percent=int((session["bytes_received"] / session["total_size"]) * 100),
            chunks_received=sorted(session["chunks_received"]),
            status=session["status"],
            error_message=session.get("error_message"),
            iso_path=session["final_path"] if session["status"] == "completed" else None,
            created_at=session["created_at"],
        )


@router.post("/upload/{upload_id}/complete", response_model=UploadCompleteResponse)
async def complete_upload(
    upload_id: str,
    current_user: models.User = Depends(get_current_user),
):
    """Finalize a chunked upload.

    This verifies all chunks have been received and moves the file
    to its final location in the upload directory.

    After completion, use POST /iso/scan with the returned iso_path
    to parse the ISO contents.
    """
    with _upload_lock:
        session = _upload_sessions.get(upload_id)
        if not session:
            raise HTTPException(status_code=404, detail="Upload session not found")
        if session["status"] != "uploading":
            raise HTTPException(status_code=400, detail=f"Upload is {session['status']}")
        session = dict(session)

    # Verify all chunks received
    received = set(session["chunks_received"])
    expected = set(range(session["total_chunks"]))
    missing = expected - received

    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Missing chunks: {sorted(missing)[:10]}{'...' if len(missing) > 10 else ''}"
        )

    # Move temp file to final location
    temp_path = Path(session["temp_path"])
    final_path = Path(session["final_path"])

    try:
        # Verify file size
        actual_size = temp_path.stat().st_size
        if actual_size != session["total_size"]:
            raise HTTPException(
                status_code=400,
                detail=f"File size mismatch. Expected {session['total_size']}, got {actual_size}"
            )

        # Move to final location
        shutil.move(str(temp_path), str(final_path))

        logger.info(f"Upload {upload_id} completed: {final_path}")

    except IOError as e:
        logger.error(f"Failed to finalize upload {upload_id}: {e}")
        with _upload_lock:
            if upload_id in _upload_sessions:
                _upload_sessions[upload_id]["status"] = "failed"
                _upload_sessions[upload_id]["error_message"] = str(e)
        raise HTTPException(status_code=500, detail=f"Failed to finalize upload: {e}")

    # Update session status
    with _upload_lock:
        if upload_id in _upload_sessions:
            _upload_sessions[upload_id]["status"] = "completed"
            _upload_sessions[upload_id]["final_path"] = str(final_path)

    return UploadCompleteResponse(
        upload_id=upload_id,
        filename=session["filename"],
        iso_path=str(final_path),
        total_size=session["total_size"],
        md5_hash=None,  # Could calculate if needed
    )


@router.delete("/upload/{upload_id}")
async def cancel_upload(
    upload_id: str,
    current_user: models.User = Depends(get_current_user),
):
    """Cancel and clean up an upload session.

    This removes any partially uploaded data.
    """
    with _upload_lock:
        session = _upload_sessions.pop(upload_id, None)

    if not session:
        raise HTTPException(status_code=404, detail="Upload session not found")

    # Clean up temp file
    temp_path = Path(session["temp_path"])
    if temp_path.exists():
        try:
            temp_path.unlink()
            logger.info(f"Upload {upload_id} cancelled, temp file removed")
        except IOError as e:
            logger.warning(f"Failed to remove temp file for upload {upload_id}: {e}")

    return {"message": "Upload cancelled"}


# --- Scan Endpoints ---


@router.post("/scan", response_model=ScanResponse)
async def scan_iso(
    request: ScanRequest,
    current_user: models.User = Depends(get_current_user),
):
    """Scan an ISO file at a filesystem path and parse its contents.

    This endpoint is designed for large ISOs (15GB+) that are already
    on the server. It parses the ISO structure and returns a manifest
    of available node definitions and images.

    The returned session_id can be used to:
    - Import selected images via POST /iso/{session_id}/import
    - Get progress via GET /iso/{session_id}/progress
    - Clean up via DELETE /iso/{session_id}
    """
    iso_path = Path(request.iso_path)

    # Validate path
    if not iso_path.exists():
        raise HTTPException(status_code=404, detail=f"ISO file not found: {iso_path}")
    if not iso_path.is_file():
        raise HTTPException(status_code=400, detail=f"Path is not a file: {iso_path}")
    if not str(iso_path).lower().endswith(".iso"):
        raise HTTPException(status_code=400, detail="File must be an ISO image")

    # Check 7z is available
    if not await check_7z_available():
        raise HTTPException(
            status_code=500,
            detail="7z (p7zip) is not available. Install with: apt install p7zip-full"
        )

    # Create session
    session_id = str(uuid4())[:8]
    session = ISOSession(
        id=session_id,
        iso_path=str(iso_path),
        status="scanning",
    )
    _save_session(session)

    try:
        # Create extractor and list files
        extractor = ISOExtractor(iso_path)
        file_list = await extractor.get_file_names()

        # Find appropriate parser
        parser = ParserRegistry.get_parser(iso_path, file_list)
        if not parser:
            raise HTTPException(
                status_code=400,
                detail="Unrecognized ISO format. Supported formats: VIRL2/CML2"
            )

        # Parse ISO
        manifest = await parser.parse(iso_path, extractor)
        session.manifest = manifest
        session.status = "scanned"
        _save_session(session)

        # Convert to response format
        return ScanResponse(
            session_id=session_id,
            iso_path=str(iso_path),
            format=manifest.format.value,
            size_bytes=manifest.size_bytes,
            node_definitions=[nd.model_dump() for nd in manifest.node_definitions],
            images=[img.model_dump() for img in manifest.images],
            parse_errors=manifest.parse_errors,
        )

    except HTTPException:
        _delete_session(session_id)
        raise
    except Exception as e:
        logger.exception(f"Failed to scan ISO: {e}")
        _delete_session(session_id)
        raise HTTPException(status_code=500, detail=f"Failed to scan ISO: {e}")


@router.get("/{session_id}/manifest")
async def get_manifest(
    session_id: str,
    current_user: models.User = Depends(get_current_user),
):
    """Get the parsed manifest for an ISO session."""
    session = _get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if not session.manifest:
        raise HTTPException(status_code=400, detail="ISO has not been scanned yet")

    return {
        "session_id": session_id,
        "manifest": session.manifest.model_dump(),
    }


@router.post("/{session_id}/import")
async def start_import(
    session_id: str,
    request: ImportRequest,
    current_user: models.User = Depends(get_current_user),
):
    """Start importing selected images from the ISO.

    This starts a background import job. Use GET /iso/{session_id}/progress
    or GET /iso/{session_id}/stream to track progress.
    """
    session = _get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if not session.manifest:
        raise HTTPException(status_code=400, detail="ISO has not been scanned yet")

    if session.status == "importing":
        raise HTTPException(status_code=400, detail="Import already in progress")

    # Validate image IDs
    valid_ids = {img.id for img in session.manifest.images}
    invalid_ids = [i for i in request.image_ids if i not in valid_ids]
    if invalid_ids:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid image IDs: {invalid_ids}"
        )

    # Initialize session for import
    session.selected_images = request.image_ids
    session.create_devices = request.create_devices
    session.status = "importing"
    session.image_progress = {
        img_id: ImageImportProgress(image_id=img_id).model_dump()
        for img_id in request.image_ids
    }
    _save_session(session)

    # Start background import task
    asyncio.create_task(_execute_import(session_id))

    return {
        "session_id": session_id,
        "status": "importing",
        "image_count": len(request.image_ids),
    }


@router.get("/{session_id}/progress", response_model=ImportProgressResponse)
async def get_import_progress(
    session_id: str,
    current_user: models.User = Depends(get_current_user),
):
    """Poll for import progress."""
    session = _get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    completed = [
        img_id for img_id, prog in session.image_progress.items()
        if isinstance(prog, dict) and prog.get("status") == "completed"
    ]
    failed = [
        img_id for img_id, prog in session.image_progress.items()
        if isinstance(prog, dict) and prog.get("status") == "failed"
    ]

    return ImportProgressResponse(
        session_id=session_id,
        status=session.status,
        progress_percent=session.progress_percent,
        error_message=session.error_message,
        image_progress=session.image_progress,
        completed_images=completed,
        failed_images=failed,
    )


@router.get("/{session_id}/stream")
async def stream_import_progress(
    session_id: str,
    current_user: models.User = Depends(get_current_user),
):
    """Stream import progress via Server-Sent Events."""
    session = _get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    async def generate() -> AsyncGenerator[str, None]:
        last_progress = -1
        last_status = ""

        while True:
            session = _get_session(session_id)
            if not session:
                yield _sse_event("error", {"message": "Session not found"})
                break

            # Send update if progress changed
            if session.progress_percent != last_progress or session.status != last_status:
                last_progress = session.progress_percent
                last_status = session.status

                yield _sse_event("progress", {
                    "status": session.status,
                    "progress_percent": session.progress_percent,
                    "error_message": session.error_message,
                    "image_progress": session.image_progress,
                })

            # Check if done
            if session.status in ("completed", "failed", "cancelled"):
                yield _sse_event("complete", {
                    "status": session.status,
                    "error_message": session.error_message,
                })
                break

            await asyncio.sleep(0.5)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.delete("/{session_id}")
async def delete_session(
    session_id: str,
    current_user: models.User = Depends(get_current_user),
):
    """Delete an ISO session and clean up any temporary files."""
    session = _get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.status == "importing":
        session.status = "cancelled"
        _save_session(session)

    _delete_session(session_id)
    return {"message": "Session deleted"}


@router.get("/{session_id}")
async def get_session_info(
    session_id: str,
    current_user: models.User = Depends(get_current_user),
) -> SessionInfoResponse:
    """Get information about an ISO session."""
    session = _get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    return SessionInfoResponse(
        session_id=session.id,
        iso_path=session.iso_path,
        status=session.status,
        progress_percent=session.progress_percent,
        error_message=session.error_message,
        selected_images=session.selected_images,
        manifest=session.manifest.model_dump() if session.manifest else None,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


# --- Helper Functions ---


def _sse_event(event_type: str, data: dict) -> str:
    """Format a Server-Sent Event."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


async def _execute_import(session_id: str):
    """Execute the import in the background."""
    session = _get_session(session_id)
    if not session or not session.manifest:
        return

    iso_path = Path(session.iso_path)
    extractor = ISOExtractor(iso_path)
    image_store = ensure_image_store()

    try:
        manifest_data = load_manifest()
        total_images = len(session.selected_images)
        completed_count = 0

        for image_id in session.selected_images:
            # Check for cancellation
            session = _get_session(session_id)
            if not session or session.status == "cancelled":
                logger.info(f"Import cancelled for session {session_id}")
                return

            # Find the image in manifest
            image = next(
                (img for img in session.manifest.images if img.id == image_id),
                None
            )
            if not image:
                _update_image_progress(session_id, image_id, "failed", 0, f"Image {image_id} not found")
                continue

            try:
                await _import_single_image(
                    session_id,
                    image,
                    session.manifest.node_definitions,
                    extractor,
                    image_store,
                    manifest_data,
                    session.create_devices,
                )
                completed_count += 1

            except Exception as e:
                logger.exception(f"Failed to import image {image_id}: {e}")
                _update_image_progress(session_id, image_id, "failed", 0, str(e))

            # Update overall progress
            session = _get_session(session_id)
            if session:
                session.progress_percent = int((completed_count / total_images) * 100)
                _save_session(session)

        # Save final manifest
        save_manifest(manifest_data)

        # Mark session complete
        session = _get_session(session_id)
        if session:
            session.status = "completed"
            session.progress_percent = 100
            session.completed_at = datetime.utcnow()
            _save_session(session)

    except Exception as e:
        logger.exception(f"Import failed for session {session_id}: {e}")
        session = _get_session(session_id)
        if session:
            session.status = "failed"
            session.error_message = str(e)
            _save_session(session)

    finally:
        extractor.cleanup()


async def _import_single_image(
    session_id: str,
    image: ParsedImage,
    node_definitions: list[ParsedNodeDefinition],
    extractor: ISOExtractor,
    image_store: Path,
    manifest_data: dict,
    create_devices: bool,
):
    """Import a single image from the ISO."""
    image_id = image.id
    _update_image_progress(session_id, image_id, "extracting", 5)

    # Determine device mapping
    device_id, new_device_config = get_image_device_mapping(image, node_definitions)

    # Create device if needed
    if new_device_config and create_devices:
        existing = find_custom_device(new_device_config["id"])
        if not existing:
            logger.info(f"Creating custom device: {new_device_config['id']}")
            add_custom_device(new_device_config)

    # Extract the disk image
    if not image.disk_image_path:
        raise ValueError(f"No disk image path for {image_id}")

    def progress_callback(p):
        _update_image_progress(
            session_id, image_id, "extracting",
            5 + int(p.percent * 0.85),  # 5% to 90%
        )

    if image.image_type == "qcow2":
        # Extract qcow2 to image store
        dest_path = image_store / image.disk_image_filename
        await extractor.extract_file(
            image.disk_image_path,
            dest_path,
            progress_callback=progress_callback,
            timeout_seconds=settings.iso_extraction_timeout,
        )

        # Create manifest entry
        entry = create_image_entry(
            image_id=f"qcow2:{image.disk_image_filename}",
            kind="qcow2",
            reference=str(dest_path),
            filename=image.disk_image_filename,
            device_id=device_id,
            version=image.version,
            size_bytes=dest_path.stat().st_size,
        )

    elif image.image_type == "docker":
        # Extract tar.gz and load into Docker
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir) / image.disk_image_filename
            await extractor.extract_file(
                image.disk_image_path,
                temp_path,
                progress_callback=progress_callback,
                timeout_seconds=settings.iso_extraction_timeout,
            )

            _update_image_progress(session_id, image_id, "loading", 92)

            # Load into Docker
            result = subprocess.run(
                ["docker", "load", "-i", str(temp_path)],
                capture_output=True,
                text=True,
                timeout=600,
            )

            if result.returncode != 0:
                raise RuntimeError(f"docker load failed: {result.stderr}")

            # Parse loaded image name
            docker_ref = None
            for line in (result.stdout + result.stderr).splitlines():
                if "Loaded image:" in line:
                    docker_ref = line.split("Loaded image:", 1)[-1].strip()
                    break
                elif "Loaded image ID:" in line:
                    docker_ref = line.split("Loaded image ID:", 1)[-1].strip()
                    break

            if not docker_ref:
                raise RuntimeError("Could not determine loaded image reference")

            # Create manifest entry
            entry = create_image_entry(
                image_id=f"docker:{docker_ref}",
                kind="docker",
                reference=docker_ref,
                filename=image.disk_image_filename,
                device_id=device_id,
                version=image.version,
                size_bytes=temp_path.stat().st_size,
            )

    else:
        raise ValueError(f"Unsupported image type: {image.image_type}")

    # Check for duplicates
    if find_image_by_id(manifest_data, entry["id"]):
        logger.warning(f"Image {entry['id']} already exists, skipping")
    else:
        manifest_data["images"].append(entry)

    _update_image_progress(session_id, image_id, "completed", 100)


def _update_image_progress(
    session_id: str,
    image_id: str,
    status: str,
    progress_percent: int,
    error_message: str | None = None,
):
    """Update progress for a specific image."""
    session = _get_session(session_id)
    if not session:
        return

    if image_id not in session.image_progress:
        session.image_progress[image_id] = {}

    session.image_progress[image_id].update({
        "image_id": image_id,
        "status": status,
        "progress_percent": progress_percent,
        "error_message": error_message,
    })

    if status == "extracting" and "started_at" not in session.image_progress[image_id]:
        session.image_progress[image_id]["started_at"] = datetime.utcnow().isoformat()

    if status in ("completed", "failed"):
        session.image_progress[image_id]["completed_at"] = datetime.utcnow().isoformat()

    session.updated_at = datetime.utcnow()
    _save_session(session)
