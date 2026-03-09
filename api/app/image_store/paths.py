"""Workspace path helpers for the image store."""
from __future__ import annotations

import logging
import re
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)
_NON_ALNUM_RE = re.compile(r"[^A-Za-z0-9]+")


def image_store_root() -> Path:
    if settings.qcow2_store:
        return Path(settings.qcow2_store)
    return Path(settings.workspace) / "images"


def ensure_image_store() -> Path:
    path = image_store_root()
    path.mkdir(parents=True, exist_ok=True)
    return path


def docker_archive_root() -> Path:
    return image_store_root() / "archives"


def ensure_docker_archive_root() -> Path:
    path = docker_archive_root()
    path.mkdir(parents=True, exist_ok=True)
    return path


def docker_archive_path(image_id: str) -> Path:
    slug = _NON_ALNUM_RE.sub("_", image_id).strip("_") or "image"
    return ensure_docker_archive_root() / f"{slug}.tar"


def qcow2_path(filename: str) -> Path:
    return ensure_image_store() / filename


def iol_path(filename: str) -> Path:
    return ensure_image_store() / filename


def manifest_path() -> Path:
    return ensure_image_store() / "manifest.json"


def rules_path() -> Path:
    return ensure_image_store() / "rules.json"


def custom_devices_path() -> Path:
    """Path to the custom device types JSON file."""
    return ensure_image_store() / "custom_devices.json"


def hidden_devices_path() -> Path:
    """Path to the hidden devices JSON file."""
    return ensure_image_store() / "hidden_devices.json"


def device_overrides_path() -> Path:
    """Path to the device configuration overrides JSON file."""
    return ensure_image_store() / "device_overrides.json"
