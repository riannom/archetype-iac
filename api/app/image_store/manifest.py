"""Manifest I/O: load, save, normalize, and query the image manifest."""
from __future__ import annotations

import json
import logging

from app.config import settings

from .aliases import (
    PLATFORM_SIBLINGS,
    RUNNABLE_IMAGE_KINDS,
    _maybe_backfill_specific_linux_device,
    _maybe_backfill_vjunos_evolved_device,
    _maybe_correct_device_via_filename,
    canonicalize_device_id,
    canonicalize_device_ids,
    get_vendor_for_device,
    image_matches_device,
    is_image_default_for_device,
    normalize_default_device_scope_id,
    normalize_default_device_scope_ids,
)
from .paths import manifest_path

logger = logging.getLogger(__name__)


def _normalize_manifest_images(manifest: dict) -> None:
    """Canonicalize image metadata and backfill sensible default flags."""
    images = manifest.get("images", [])
    if not isinstance(images, list):
        return

    for image in images:
        if not isinstance(image, dict):
            continue

        raw_device_id = _maybe_backfill_specific_linux_device(image)
        raw_device_id = _maybe_backfill_vjunos_evolved_device(image, raw_device_id)
        raw_device_id = _maybe_correct_device_via_filename(image, raw_device_id)
        raw_compatible_devices = list(image.get("compatible_devices") or [])

        # If a legacy Linux assignment is clearly a distinct device type and
        # compatibility only listed linux, shift compatibility to the inferred type.
        inferred_scope = normalize_default_device_scope_id(raw_device_id)
        compat_scopes = {
            normalize_default_device_scope_id(dev) for dev in raw_compatible_devices if dev
        }
        if inferred_scope in {"frr", "haproxy", "alpine", "tcl"} and compat_scopes <= {"linux"}:
            raw_compatible_devices = [raw_device_id]

        canonical_device_id = canonicalize_device_id(raw_device_id)
        compatible_devices = canonicalize_device_ids(raw_compatible_devices)
        if canonical_device_id and canonical_device_id not in compatible_devices:
            compatible_devices.append(canonical_device_id)

        # Expand platform siblings: if any compatible device shares a platform
        # group, add all siblings (e.g. cat9800 -> cat9000v-q200, cat9000v-uadp).
        expanded = set(compatible_devices)
        for dev_id in list(expanded):
            for sibling in PLATFORM_SIBLINGS.get(dev_id, []):
                expanded.add(sibling)
        compatible_devices = list(expanded)

        image["device_id"] = canonical_device_id
        image["compatible_devices"] = compatible_devices
        if canonical_device_id:
            image["vendor"] = get_vendor_for_device(canonical_device_id)
        default_for_devices = normalize_default_device_scope_ids(image.get("default_for_devices") or [])
        if not default_for_devices and image.get("is_default") and canonical_device_id:
            # Legacy manifest entries only tracked a single boolean default.
            scope = normalize_default_device_scope_id(canonical_device_id)
            default_for_devices = [scope] if scope else []
        for scope in list(default_for_devices):
            canonical_scope = canonicalize_device_id(scope)
            if canonical_scope and canonical_scope not in compatible_devices:
                compatible_devices.append(canonical_scope)
        image["compatible_devices"] = compatible_devices
        image["default_for_devices"] = default_for_devices
        image["is_default"] = bool(default_for_devices)

    _backfill_single_image_defaults(manifest)


def _backfill_single_image_defaults(manifest: dict) -> None:
    """Mark a runnable image default when it is the only match for a device."""
    images = [img for img in manifest.get("images", []) if isinstance(img, dict)]
    if not images:
        return

    candidate_devices: set[str] = set()
    for image in images:
        kind = str(image.get("kind") or "").lower()
        if kind not in RUNNABLE_IMAGE_KINDS:
            continue
        for device_id in image.get("compatible_devices") or []:
            canonical = canonicalize_device_id(device_id)
            if canonical:
                candidate_devices.add(canonical)

    for device_id in candidate_devices:
        matches = [
            image
            for image in images
            if str(image.get("kind") or "").lower() in RUNNABLE_IMAGE_KINDS
            and image_matches_device(image, device_id)
        ]
        if len(matches) == 1:
            scopes = normalize_default_device_scope_ids(matches[0].get("default_for_devices") or [])
            scope = normalize_default_device_scope_id(device_id)
            if scope and scope not in scopes:
                scopes.append(scope)
                matches[0]["default_for_devices"] = scopes
            matches[0]["is_default"] = bool(matches[0].get("default_for_devices"))


def load_manifest() -> dict:
    # DB-first cutover: once catalog tables are seeded, read image library from DB.
    try:
        from app.db import get_session
        from app.services.catalog_service import catalog_is_seeded, list_catalog_library_images

        with get_session() as session:
            if catalog_is_seeded(session):
                return {"images": list_catalog_library_images(session)}
    except Exception:
        logger.debug("Catalog-backed manifest read failed; falling back to file", exc_info=True)

    path = manifest_path()
    if not path.exists():
        return {"images": []}
    manifest = json.loads(path.read_text(encoding="utf-8"))
    _normalize_manifest_images(manifest)
    return manifest


def save_manifest(data: dict) -> None:
    _normalize_manifest_images(data)
    wrote_catalog = False
    try:
        from app.db import get_session
        from app.services.catalog_service import catalog_is_seeded, sync_catalog_from_manifest

        with get_session() as session:
            if catalog_is_seeded(session):
                # Runtime callers usually submit a partial in-memory payload derived
                # from the current library. Avoid destructive pruning here to prevent
                # accidental catalog truncation during transient fallback reads.
                sync_catalog_from_manifest(
                    session,
                    data,
                    source="image_store.save_manifest",
                    prune_missing=False,
                )
                session.commit()
                wrote_catalog = True
    except Exception:
        logger.warning("Failed to persist image catalog from manifest payload", exc_info=True)

    # Runtime manifest writes are deprecated once DB catalog is seeded.
    if wrote_catalog and not settings.catalog_manifest_mirror_enabled:
        return

    path = manifest_path()
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def find_image_by_id(manifest: dict, image_id: str) -> dict | None:
    """Find an image entry by its ID."""
    for item in manifest.get("images", []):
        if item.get("id") == image_id:
            return item
    return None


def find_image_by_reference(manifest: dict, reference: str) -> dict | None:
    """Find an image entry by its Docker reference or file path."""
    for item in manifest.get("images", []):
        if item.get("reference") == reference:
            return item
    return None


def get_device_image_count(device_id: str) -> int:
    """Count how many images are assigned to a device type.

    Checks both 'device_id' field and 'compatible_devices' list.
    """
    manifest = load_manifest()
    return sum(1 for img in manifest.get("images", [])
               if image_matches_device(img, device_id))


def find_image_reference(device_id: str, version: str | None = None) -> str | None:
    """Look up the image reference for a device type and version.

    Supports Docker and qcow2 images. Raw IOL binaries (kind="iol") are
    excluded -- they must be built into Docker images first.

    Args:
        device_id: Device type (e.g., 'eos', 'ceos', 'iosv', 'cisco_iosv')
        version: Optional version string (e.g., '4.35.1F')

    Returns:
        Image reference (Docker tag or file path for qcow2/IOL) or None if not found
    """
    manifest = load_manifest()
    images = manifest.get("images", [])

    # Supported image kinds
    supported_kinds = ("docker", "qcow2")

    # First try exact version match
    if version:
        version_lower = version.lower()
        for img in images:
            if img.get("kind") not in supported_kinds:
                continue
            img_version = (img.get("version") or "").lower()
            if image_matches_device(img, device_id) and img_version == version_lower:
                return img.get("reference")

    # Fall back to default image for this device type
    for img in images:
        if img.get("kind") not in supported_kinds:
            continue
        if image_matches_device(img, device_id) and is_image_default_for_device(img, device_id):
            return img.get("reference")

    # Fall back to any image for this device type
    for img in images:
        if img.get("kind") not in supported_kinds:
            continue
        if image_matches_device(img, device_id):
            return img.get("reference")

    return None
