"""Shared state, utilities, and Pydantic models for the images package."""
from __future__ import annotations

import asyncio
import json
import lzma
import os
import shutil
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import AsyncGenerator
from uuid import uuid4

import threading as _threading
from pydantic import BaseModel, Field

from app.image_store import (
    qcow2_path,
)

# Module-local thread shim so tests can monkeypatch img.threading.Thread
# without affecting global threading behavior in other subsystems.
threading = SimpleNamespace(Lock=_threading.Lock, Thread=_threading.Thread)

# Track upload progress for polling
_upload_progress: dict[str, dict] = {}
_upload_lock = threading.Lock()

# Chunked upload session storage
_chunk_upload_sessions: dict[str, dict] = {}
_chunk_upload_lock = threading.Lock()

DEFAULT_CHUNK_SIZE = 10 * 1024 * 1024
CHUNK_UPLOAD_TTL_SECONDS = 24 * 60 * 60
_CHUNK_UPLOAD_DIR = Path(tempfile.gettempdir()) / "archetype-image-uploads"


def _is_docker_image_tar(tar_path: str) -> bool:
    """Check if tar is a Docker image (has manifest.json) vs raw filesystem.

    Docker images have manifest.json or repositories at the root level,
    typically in the first few entries. We only check the first 20 entries
    to avoid reading the entire tar for large filesystem archives.
    """
    try:
        with tarfile.open(tar_path, "r:*") as tf:
            # Only check first 20 entries - Docker metadata is always at the start
            for i, member in enumerate(tf):
                if i >= 20:
                    break
                name = member.name.lstrip("./")
                if name in ("manifest.json", "repositories"):
                    return True
            return False
    except Exception:
        return False


def _format_size(size_bytes: int) -> str:
    """Format bytes as human-readable size."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def _update_progress(upload_id: str, phase: str, message: str, percent: int, **kwargs):
    """Update progress for an upload."""
    with _upload_lock:
        _upload_progress[upload_id] = {
            "phase": phase,
            "message": message,
            "percent": percent,
            "timestamp": time.time(),
            **kwargs
        }


def _get_progress(upload_id: str) -> dict | None:
    """Get progress for an upload."""
    with _upload_lock:
        return _upload_progress.get(upload_id)


def _clear_progress(upload_id: str):
    """Clear progress for an upload."""
    with _upload_lock:
        _upload_progress.pop(upload_id, None)


def _send_sse_event(event_type: str, data: dict) -> str:
    """Format a Server-Sent Event message."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


def _write_content_to_tempfile(content: bytes, suffix: str) -> tuple[str, int]:
    """Write content to a temporary file and return (path, size).

    This is a blocking operation meant to run in asyncio.to_thread().
    """
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
        tmp_file.write(content)
        temp_path = tmp_file.name
    file_size = os.path.getsize(temp_path)
    return temp_path, file_size


def _decompress_xz_file(source_path: str) -> tuple[str, int]:
    """Decompress an XZ file and return (decompressed_path, size).

    This is a blocking operation meant to run in asyncio.to_thread().
    """
    with tempfile.NamedTemporaryFile(delete=False, suffix=".tar") as tmp_tar:
        with lzma.open(source_path, "rb") as source:
            shutil.copyfileobj(source, tmp_tar)
        decompressed_path = tmp_tar.name
    decompressed_size = os.path.getsize(decompressed_path)
    return decompressed_path, decompressed_size


def _cleanup_temp_files(*paths: str) -> None:
    """Clean up temporary files.

    This is a blocking operation meant to run in asyncio.to_thread().
    """
    for path in paths:
        if path and os.path.exists(path):
            os.unlink(path)


def _sanitize_upload_filename(filename: str) -> str:
    """Sanitize uploaded filename to a safe subset."""
    safe = "".join(c for c in filename if c.isalnum() or c in "._-")
    return safe


def _cleanup_chunk_upload_session_files(session: dict) -> None:
    """Delete temporary files associated with a chunked upload session."""
    temp_path = session.get("temp_path")
    if isinstance(temp_path, str) and temp_path and os.path.exists(temp_path):
        try:
            os.unlink(temp_path)
        except OSError:
            pass

    final_path = session.get("final_path")
    if (
        isinstance(final_path, str)
        and final_path
        and session.get("kind") == "docker"
        and session.get("status") in {"uploading", "completed", "failed", "cancelled"}
        and os.path.exists(final_path)
    ):
        try:
            os.unlink(final_path)
        except OSError:
            pass


def _cleanup_expired_chunk_upload_sessions() -> None:
    """Remove stale chunk upload sessions and their temp files."""
    now = datetime.now(timezone.utc)
    expired_ids: list[str] = []

    with _chunk_upload_lock:
        for upload_id, session in _chunk_upload_sessions.items():
            created_at = session.get("created_at")
            if not isinstance(created_at, datetime):
                continue
            age_seconds = (now - created_at).total_seconds()
            if age_seconds > CHUNK_UPLOAD_TTL_SECONDS:
                expired_ids.append(upload_id)

        for upload_id in expired_ids:
            session = _chunk_upload_sessions.pop(upload_id, None)
            if session:
                _cleanup_chunk_upload_session_files(session)


def _chunk_upload_destination(kind: str, upload_id: str, safe_filename: str) -> Path:
    """Resolve the final destination path for a chunked upload."""
    if kind == "qcow2":
        return qcow2_path(safe_filename)

    _CHUNK_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    return _CHUNK_UPLOAD_DIR / f"{upload_id}-{safe_filename}"


# --- Pydantic models for chunked upload ---

class ImageChunkUploadInitRequest(BaseModel):
    """Request to initialize a chunked image upload."""

    kind: str = Field(..., description="Upload kind: docker or qcow2")
    filename: str = Field(..., description="Original filename")
    total_size: int = Field(..., gt=0, description="Total file size in bytes")
    chunk_size: int = Field(default=DEFAULT_CHUNK_SIZE, gt=0, description="Chunk size in bytes")
    auto_build: bool = Field(default=True, description="Auto-build Docker image for qcow2 uploads")
    auto_confirm: bool = Field(default=True, description="Auto-confirm qcow2 uploads (skip two-phase)")


class ImageChunkUploadInitResponse(BaseModel):
    """Response from initializing chunked image upload."""

    upload_id: str
    kind: str
    filename: str
    total_size: int
    chunk_size: int
    total_chunks: int


class ImageChunkUploadChunkResponse(BaseModel):
    """Response from uploading a chunk."""

    upload_id: str
    chunk_index: int
    bytes_received: int
    total_received: int
    progress_percent: int
    is_complete: bool


class ImageChunkUploadStatusResponse(BaseModel):
    """Status of a chunked upload session."""

    upload_id: str
    kind: str
    filename: str
    total_size: int
    bytes_received: int
    progress_percent: int
    chunks_received: list[int]
    status: str
    error_message: str | None = None
    created_at: datetime


class ImageChunkUploadCompleteResponse(BaseModel):
    """Response from completing a chunked upload."""

    upload_id: str
    kind: str
    filename: str
    status: str
    result: dict[str, object] | None = None
