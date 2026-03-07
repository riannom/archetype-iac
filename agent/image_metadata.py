"""Persistent image-to-device metadata store.

Maps Docker image IDs (immutable sha256 digests) and file paths to device
info, enabling deterministic device identification without keyword guessing.

Storage lives alongside images at ``{image_store_path}/image-metadata.json``.
Concurrent access is protected by ``fcntl.flock`` on a dedicated lock file,
and writes use ``os.replace`` for atomicity.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import tempfile
from pathlib import Path

from agent.config import settings

logger = logging.getLogger(__name__)

_LOCK_PATH: Path | None = None
_METADATA_PATH: Path | None = None


def _metadata_path() -> Path:
    global _METADATA_PATH
    if _METADATA_PATH is None:
        _METADATA_PATH = Path(settings.image_store_path) / "image-metadata.json"
    return _METADATA_PATH


def _lock_path() -> Path:
    global _LOCK_PATH
    if _LOCK_PATH is None:
        _LOCK_PATH = _metadata_path().with_suffix(".lock")
    return _LOCK_PATH


def _empty_metadata() -> dict:
    return {"images": {}, "files": {}}


def _read_metadata() -> dict:
    """Read metadata from disk. Returns empty structure on missing/corrupt."""
    path = _metadata_path()
    if not path.exists():
        return _empty_metadata()
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            return _empty_metadata()
        data.setdefault("images", {})
        data.setdefault("files", {})
        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Failed to read image metadata: {e}")
        return _empty_metadata()


def _write_metadata(data: dict) -> None:
    """Atomic write: temp file + os.replace()."""
    path = _metadata_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, str(path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _with_lock(fn):
    """File-lock decorator for concurrent access protection."""
    def wrapper(*args, **kwargs):
        lock = _lock_path()
        lock.parent.mkdir(parents=True, exist_ok=True)
        with open(lock, "w") as lock_fd:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            try:
                return fn(*args, **kwargs)
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
    return wrapper


@_with_lock
def load_metadata() -> dict:
    """Load metadata (locked). Returns empty structure on missing/corrupt."""
    return _read_metadata()


@_with_lock
def save_metadata(data: dict) -> None:
    """Atomic write (locked)."""
    _write_metadata(data)


def set_docker_image_metadata(
    image_id: str,
    tags: list[str],
    device_id: str,
    source: str,
) -> None:
    """Persist device_id for a Docker image by immutable ID."""
    lock = _lock_path()
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            data = _read_metadata()
            data["images"][image_id] = {
                "device_id": device_id,
                "tags": tags,
                "source": source,
            }
            _write_metadata(data)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)


def set_file_image_metadata(path: str, device_id: str, source: str) -> None:
    """Persist device_id for a file-based image by path."""
    lock = _lock_path()
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            data = _read_metadata()
            data["files"][path] = {
                "device_id": device_id,
                "source": source,
            }
            _write_metadata(data)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)


def remove_docker_image_metadata(image_id: str | None = None, reference: str | None = None) -> int:
    """Remove Docker image metadata by immutable ID and/or tag reference."""
    if not image_id and not reference:
        return 0

    lock = _lock_path()
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            data = _read_metadata()
            removed = 0
            image_entries = data.get("images", {})
            for key, entry in list(image_entries.items()):
                tags = entry.get("tags", [])
                if key == image_id or (reference and reference in tags):
                    image_entries.pop(key, None)
                    removed += 1
            if removed:
                _write_metadata(data)
            return removed
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)


def remove_file_image_metadata(path: str) -> bool:
    """Remove file-based image metadata for a path."""
    lock = _lock_path()
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            data = _read_metadata()
            removed = data.get("files", {}).pop(path, None) is not None
            if removed:
                _write_metadata(data)
            return removed
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)


def lookup_device_id_by_image_id(image_id: str) -> str | None:
    """Direct lookup by immutable Docker image ID."""
    data = _read_metadata()
    entry = data.get("images", {}).get(image_id)
    return entry["device_id"] if entry else None


def lookup_device_id_by_tag(tag: str) -> str | None:
    """Find device_id for a Docker tag via image ID index."""
    data = _read_metadata()
    for entry in data.get("images", {}).values():
        if tag in entry.get("tags", []):
            return entry["device_id"]
    return None


def lookup_device_id_by_path(path: str) -> str | None:
    """Lookup device_id for a file-based image."""
    data = _read_metadata()
    entry = data.get("files", {}).get(path)
    return entry["device_id"] if entry else None
