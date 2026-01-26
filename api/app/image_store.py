from __future__ import annotations

from pathlib import Path
import json
import re

from app.config import settings


def image_store_root() -> Path:
    if settings.qcow2_store:
        return Path(settings.qcow2_store)
    return Path(settings.netlab_workspace) / "images"


def ensure_image_store() -> Path:
    path = image_store_root()
    path.mkdir(parents=True, exist_ok=True)
    return path


def qcow2_path(filename: str) -> Path:
    return ensure_image_store() / filename


def manifest_path() -> Path:
    return ensure_image_store() / "manifest.json"


def load_manifest() -> dict:
    path = manifest_path()
    if not path.exists():
        return {"images": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(data: dict) -> None:
    path = manifest_path()
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def rules_path() -> Path:
    return ensure_image_store() / "rules.json"


def load_rules() -> list[dict[str, str]]:
    path = rules_path()
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("rules", [])


def detect_device_from_filename(filename: str) -> tuple[str | None, str | None]:
    name = filename.lower()
    for rule in load_rules():
        pattern = rule.get("pattern")
        device_id = rule.get("device_id")
        if not pattern or not device_id:
            continue
        if re.search(pattern, name):
            return device_id, _extract_version(filename)
    keyword_map = {
        "ceos": "eos",
        "eos": "eos",
        "iosv": "iosv",
        "csr": "csr",
        "nxos": "nxos",
        "viosl2": "iosvl2",
        "iosvl2": "iosvl2",
        "iosxr": "iosxr",
    }
    for keyword, device_id in keyword_map.items():
        if keyword in name:
            return device_id, _extract_version(filename)
    return None, _extract_version(filename)


def _extract_version(filename: str) -> str | None:
    match = re.search(r"(\d+(?:\.\d+){1,3}[A-Za-z0-9]*)", filename)
    return match.group(1) if match else None


def find_image_reference(device_id: str, version: str | None = None) -> str | None:
    """Look up the actual Docker image reference for a device type and version.

    Args:
        device_id: Device type (e.g., 'eos', 'ceos', 'iosv')
        version: Optional version string (e.g., '4.35.1F')

    Returns:
        Docker image reference (e.g., 'ceos64-lab-4.35.1f:imported') or None if not found
    """
    manifest = load_manifest()
    images = manifest.get("images", [])

    # Normalize device_id for matching (eos and ceos are equivalent)
    normalized_device = device_id.lower()
    if normalized_device in ("ceos", "arista_ceos", "arista_eos"):
        normalized_device = "eos"

    # First try exact version match
    if version:
        version_lower = version.lower()
        for img in images:
            if img.get("kind") != "docker":
                continue
            img_device = (img.get("device_id") or "").lower()
            if img_device in ("ceos", "arista_ceos", "arista_eos"):
                img_device = "eos"
            img_version = (img.get("version") or "").lower()
            if img_device == normalized_device and img_version == version_lower:
                return img.get("reference")

    # Fall back to any image for this device type
    for img in images:
        if img.get("kind") != "docker":
            continue
        img_device = (img.get("device_id") or "").lower()
        if img_device in ("ceos", "arista_ceos", "arista_eos"):
            img_device = "eos"
        if img_device == normalized_device:
            return img.get("reference")

    return None
