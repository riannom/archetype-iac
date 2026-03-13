"""Index building, caching, image querying, image persistence, and manifest integration."""
from __future__ import annotations

import logging
import os
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models

from .catalog_service import (
    _CACHE_LOCK,
    _IMAGE_INDEX_CACHE_BY_BIND,
    CatalogAliasConflictError,
    CatalogImageNotFoundError,
    ImageIndexCache,
    _bind_cache_key,
    _json_dump,
    _json_load,
    _normalize_token,
    _parse_timestamp,
    _to_int,
    invalidate_image_index_cache,
)
from .catalog_identity import (
    _build_alias_index,
    _resolve_token_to_canonical_set,
)

log = logging.getLogger(__name__)


# File extensions that represent file-based images (not container images)
_FILE_BASED_EXTENSIONS = frozenset({".qcow2", ".img", ".iol", ".bin"})

def _serialize_timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat().replace("+00:00", "Z")


def _archive_metadata_from_row(row: models.CatalogImage) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "archive_status": row.archive_status or "none",
        "archive_size_bytes": row.archive_size_bytes,
        "archive_error": row.archive_error,
    }
    if row.archive_path:
        metadata["archive_path"] = row.archive_path
    if row.archive_sha256:
        metadata["archive_sha256"] = row.archive_sha256
    archive_created_at = _serialize_timestamp(row.archive_created_at)
    if archive_created_at:
        metadata["archive_created_at"] = archive_created_at
    archive_verified_at = _serialize_timestamp(row.archive_verified_at)
    if archive_verified_at:
        metadata["archive_verified_at"] = archive_verified_at
    return metadata


def _serialize_catalog_image_metadata(image: dict[str, Any], row: models.CatalogImage) -> str:
    payload = dict(image)
    for key, value in _archive_metadata_from_row(row).items():
        payload[key] = value
    return _json_dump(payload)


def _build_index_stamp(session: Session) -> tuple[Any, ...]:
    img_count, img_max = session.query(
        func.count(models.CatalogImage.id),
        func.max(models.CatalogImage.updated_at),
    ).one()
    compat_count, compat_max = session.query(
        func.count(models.CatalogImageCompatibility.id),
        func.max(models.CatalogImageCompatibility.created_at),
    ).one()
    default_count, default_max = session.query(
        func.count(models.CatalogImageDefault.id),
        func.max(models.CatalogImageDefault.updated_at),
    ).one()
    device_count, device_max = session.query(
        func.count(models.CatalogDeviceType.id),
        func.max(models.CatalogDeviceType.updated_at),
    ).one()
    alias_count, alias_max = session.query(
        func.count(models.CatalogDeviceAlias.id),
        func.max(models.CatalogDeviceAlias.updated_at),
    ).one()
    return (
        img_count,
        img_max,
        compat_count,
        compat_max,
        default_count,
        default_max,
        device_count,
        device_max,
        alias_count,
        alias_max,
    )


def _project_catalog_images(session: Session) -> ImageIndexCache:
    alias_index = _build_alias_index(session)

    image_rows = session.query(models.CatalogImage).all()
    compat_rows = (
        session.query(
            models.CatalogImageCompatibility.image_id,
            models.CatalogDeviceType.canonical_device_id,
        )
        .join(
            models.CatalogDeviceType,
            models.CatalogDeviceType.id == models.CatalogImageCompatibility.device_type_id,
        )
        .all()
    )
    default_rows = (
        session.query(
            models.CatalogImageDefault.image_id,
            models.CatalogDeviceType.canonical_device_id,
        )
        .join(
            models.CatalogDeviceType,
            models.CatalogDeviceType.id == models.CatalogImageDefault.device_type_id,
        )
        .all()
    )

    compat_by_image_dbid: dict[str, set[str]] = defaultdict(set)
    for image_db_id, canonical in compat_rows:
        normalized = _normalize_token(canonical)
        if normalized:
            compat_by_image_dbid[image_db_id].add(normalized)

    defaults_by_image_dbid: dict[str, set[str]] = defaultdict(set)
    for image_db_id, canonical in default_rows:
        normalized = _normalize_token(canonical)
        if normalized:
            defaults_by_image_dbid[image_db_id].add(normalized)

    ordered_images: list[dict[str, Any]] = []
    ordered_ids: list[str] = []
    images_by_external_id: dict[str, dict[str, Any]] = {}
    image_ids_by_canonical: dict[str, set[str]] = defaultdict(set)

    for row in sorted(image_rows, key=lambda item: item.external_id):
        image = _json_load(row.metadata_json)
        image["id"] = row.external_id
        image["kind"] = row.kind
        image["reference"] = row.reference
        image["filename"] = row.filename
        image["sha256"] = row.digest_sha256
        image["size_bytes"] = row.size_bytes
        image["vendor"] = row.vendor_name
        image["version"] = row.version
        image["source"] = row.source
        imported_at = _serialize_timestamp(row.imported_at)
        if imported_at:
            image["imported_at"] = imported_at
        image["archive_status"] = row.archive_status or "none"
        image["archive_size_bytes"] = row.archive_size_bytes
        image["archive_error"] = row.archive_error
        if row.archive_sha256:
            image["archive_sha256"] = row.archive_sha256
        else:
            image.pop("archive_sha256", None)
        archive_created_at = _serialize_timestamp(row.archive_created_at)
        if archive_created_at:
            image["archive_created_at"] = archive_created_at
        else:
            image.pop("archive_created_at", None)
        archive_verified_at = _serialize_timestamp(row.archive_verified_at)
        if archive_verified_at:
            image["archive_verified_at"] = archive_verified_at
        else:
            image.pop("archive_verified_at", None)
        image.pop("archive_path", None)

        compatible = sorted(compat_by_image_dbid.get(row.id, set()))
        raw_primary = _normalize_token(image.get("device_id"))
        primary = None
        if raw_primary:
            if raw_primary in alias_index.type_id_by_canonical:
                primary = raw_primary
            else:
                matches = alias_index.alias_to_canonicals.get(raw_primary, set())
                if len(matches) == 1:
                    primary = next(iter(matches))
                elif raw_primary in compatible:
                    primary = raw_primary
        if primary and primary not in compatible:
            compatible.append(primary)
            compatible.sort()
        if not primary and compatible:
            primary = compatible[0]
        image["device_id"] = primary
        image["compatible_devices"] = compatible

        default_scopes = sorted(defaults_by_image_dbid.get(row.id, set()))
        image["default_for_devices"] = default_scopes
        image["is_default"] = bool(default_scopes)

        for canonical in compatible:
            image_ids_by_canonical[canonical].add(row.external_id)

        ordered_ids.append(row.external_id)
        ordered_images.append(image)
        images_by_external_id[row.external_id] = image

    return ImageIndexCache(
        stamp=_build_index_stamp(session),
        ordered_images=ordered_images,
        ordered_ids=ordered_ids,
        images_by_external_id=images_by_external_id,
        image_ids_by_canonical=image_ids_by_canonical,
        alias_index=alias_index,
    )


def _get_image_index(session: Session, *, force_refresh: bool = False) -> ImageIndexCache:
    bind_key = _bind_cache_key(session)
    stamp = _build_index_stamp(session)
    with _CACHE_LOCK:
        if not force_refresh:
            cached = _IMAGE_INDEX_CACHE_BY_BIND.get(bind_key)
            if cached and cached.stamp == stamp:
                return cached

    rebuilt = _project_catalog_images(session)
    with _CACHE_LOCK:
        _IMAGE_INDEX_CACHE_BY_BIND[bind_key] = rebuilt
    return rebuilt


def list_catalog_library_images(session: Session, *, force_refresh: bool = False) -> list[dict[str, Any]]:
    """List all catalog images projected as manifest-like entries."""
    return list(_get_image_index(session, force_refresh=force_refresh).ordered_images)


def get_catalog_library_image(
    session: Session,
    image_id: str,
    *,
    force_refresh: bool = False,
) -> dict[str, Any] | None:
    return _get_image_index(session, force_refresh=force_refresh).images_by_external_id.get(image_id)


def list_catalog_images_for_device(session: Session, device_id: str) -> list[dict[str, Any]]:
    """Return catalog images compatible with the given device token."""
    image_index = _get_image_index(session)
    canonical_targets = _resolve_token_to_canonical_set(image_index.alias_index, device_id)
    if not canonical_targets:
        return []

    matched_ids: set[str] = set()
    for canonical in canonical_targets:
        matched_ids.update(image_index.image_ids_by_canonical.get(canonical, set()))
    if not matched_ids:
        return []

    return [
        image_index.images_by_external_id[external_id]
        for external_id in image_index.ordered_ids
        if external_id in matched_ids
    ]


def count_catalog_images_for_device(session: Session, device_id: str) -> int:
    """Count images compatible with a device token using cached index."""
    return len(list_catalog_images_for_device(session, device_id))


def _ensure_unknown_vendor(session: Session) -> models.CatalogVendor:
    unknown_key = "unknown"
    vendor = (
        session.query(models.CatalogVendor)
        .filter(models.CatalogVendor.vendor_key == unknown_key)
        .first()
    )
    if vendor:
        return vendor
    vendor = models.CatalogVendor(
        id=str(uuid4()),
        vendor_key=unknown_key,
        display_name="Unknown",
        lifecycle_status="active",
        metadata_json="{}",
    )
    session.add(vendor)
    session.flush()
    return vendor


def _ensure_device_type(session: Session, canonical_device_id: str) -> models.CatalogDeviceType:
    existing = (
        session.query(models.CatalogDeviceType)
        .filter(models.CatalogDeviceType.canonical_device_id == canonical_device_id)
        .first()
    )
    if existing:
        return existing

    vendor = _ensure_unknown_vendor(session)
    row = models.CatalogDeviceType(
        id=str(uuid4()),
        canonical_device_id=canonical_device_id,
        vendor_id=vendor.id,
        runtime_kind=canonical_device_id,
        display_name=canonical_device_id,
        device_class=None,
        source="manifest_discovered",
        lifecycle_status="active",
        metadata_json=_json_dump({"origin": "catalog_api"}),
    )
    session.add(row)
    session.flush()

    revision = models.CatalogDeviceRevision(
        id=str(uuid4()),
        device_type_id=row.id,
        version_tag="current",
        runtime_kind=canonical_device_id,
        memory_mb=None,
        cpu_count=None,
        max_ports=None,
        supported_image_kinds_json="[]",
        metadata_json=_json_dump({"origin": "catalog_api"}),
        is_current=True,
    )
    session.add(revision)
    session.flush()
    return row


def _resolve_writable_canonical_device_id(session: Session, device_id: str | None) -> str | None:
    normalized = _normalize_token(device_id)
    if not normalized:
        return None

    alias_index = _build_alias_index(session)
    if normalized in alias_index.type_id_by_canonical:
        return normalized
    matches = alias_index.alias_to_canonicals.get(normalized, set())
    if len(matches) == 1:
        return next(iter(matches))
    if len(matches) > 1:
        raise CatalogAliasConflictError(
            f"Alias '{normalized}' maps to multiple devices: {sorted(matches)}. "
            "Use canonical device ID."
        )
    return normalized


def _persist_catalog_snapshot(
    session: Session,
    image_entries: list[dict[str, Any]],
) -> None:
    image_rows = {
        row.external_id: row for row in session.query(models.CatalogImage).all()
    }
    compat_rows_by_image: dict[str, dict[str, models.CatalogImageCompatibility]] = defaultdict(dict)
    for row in session.query(models.CatalogImageCompatibility).all():
        compat_rows_by_image[row.image_id][row.device_type_id] = row
    defaults_by_device_type_id = {
        row.device_type_id: row for row in session.query(models.CatalogImageDefault).all()
    }
    device_type_by_canonical = {
        row.canonical_device_id: row for row in session.query(models.CatalogDeviceType).all()
    }

    for image in image_entries:
        external_id = str(image.get("id") or "").strip()
        if not external_id:
            continue

        # Validate file-based images exist on disk before creating NEW entries.
        # Existing rows are still updated (e.g. during unassign/reassign).
        reference = image.get("reference") or ""
        if reference and external_id not in image_rows:
            _ext = os.path.splitext(reference)[1].lower()
            if _ext in _FILE_BASED_EXTENSIONS:
                from app.image_store import image_store_root
                full_path = image_store_root() / os.path.basename(reference)
                if not full_path.exists():
                    log.warning(
                        "Skipping phantom catalog entry %s: file %s not found",
                        external_id, full_path,
                    )
                    continue

        row = image_rows.get(external_id)
        if row is None:
            row = models.CatalogImage(
                id=str(uuid4()),
                external_id=external_id,
                kind=str(image.get("kind") or "").lower() or "unknown",
                source=str(image.get("source") or "api"),
                metadata_json="{}",
            )
            session.add(row)
            session.flush()
            image_rows[external_id] = row

        row.kind = str(image.get("kind") or row.kind or "").lower()
        row.reference = image.get("reference")
        row.filename = image.get("filename")
        row.digest_sha256 = (
            image.get("sha256")
            or image.get("digest")
            or image.get("content_hash")
        )
        row.size_bytes = _to_int(image.get("size_bytes"))
        if "archive_path" in image:
            row.archive_path = image.get("archive_path")
        archive_status = image.get("archive_status")
        if archive_status is not None:
            row.archive_status = str(archive_status).strip().lower() or "none"
        elif row.archive_status is None:
            row.archive_status = "none"
        if "archive_sha256" in image:
            row.archive_sha256 = image.get("archive_sha256")
        if "archive_size_bytes" in image:
            row.archive_size_bytes = _to_int(image.get("archive_size_bytes"))
        if "archive_error" in image:
            row.archive_error = image.get("archive_error")
        if "archive_created_at" in image:
            row.archive_created_at = _parse_timestamp(image.get("archive_created_at"))
        if "archive_verified_at" in image:
            row.archive_verified_at = _parse_timestamp(image.get("archive_verified_at"))
        row.vendor_name = image.get("vendor")
        row.version = image.get("version")
        row.source = str(image.get("source") or row.source or "api")
        imported_at = _parse_timestamp(image.get("uploaded_at") or image.get("imported_at"))
        if imported_at:
            row.imported_at = imported_at
        row.metadata_json = _serialize_catalog_image_metadata(image, row)

        desired_compat: set[str] = set()
        primary = _resolve_writable_canonical_device_id(session, image.get("device_id"))
        if primary:
            desired_compat.add(primary)
        for token in image.get("compatible_devices") or []:
            canonical = _resolve_writable_canonical_device_id(session, token)
            if canonical:
                desired_compat.add(canonical)

        desired_defaults: set[str] = set()
        for token in image.get("default_for_devices") or []:
            canonical = _resolve_writable_canonical_device_id(session, token)
            if canonical:
                desired_defaults.add(canonical)
        if not desired_defaults and image.get("is_default") and primary:
            desired_defaults.add(primary)
        if desired_defaults:
            # Invariant: a device default must also be device-compatible.
            desired_compat.update(desired_defaults)

        desired_type_ids: set[str] = set()
        for canonical in sorted(desired_compat):
            device_type = device_type_by_canonical.get(canonical)
            if device_type is None:
                device_type = _ensure_device_type(session, canonical)
                device_type_by_canonical[canonical] = device_type
            desired_type_ids.add(device_type.id)

        existing_compat = compat_rows_by_image.get(row.id, {})
        existing_type_ids = set(existing_compat.keys())
        for device_type_id in sorted(desired_type_ids - existing_type_ids):
            compat_row = models.CatalogImageCompatibility(
                id=str(uuid4()),
                image_id=row.id,
                device_type_id=device_type_id,
                source="api",
            )
            session.add(compat_row)
            existing_compat[device_type_id] = compat_row
        for device_type_id in sorted(existing_type_ids - desired_type_ids):
            session.delete(existing_compat.pop(device_type_id))
        compat_rows_by_image[row.id] = existing_compat

        desired_default_type_ids: set[str] = set()
        for canonical in sorted(desired_defaults):
            device_type = device_type_by_canonical.get(canonical)
            if device_type is None:
                device_type = _ensure_device_type(session, canonical)
                device_type_by_canonical[canonical] = device_type
            desired_default_type_ids.add(device_type.id)

        for device_type_id in desired_default_type_ids:
            default_row = defaults_by_device_type_id.get(device_type_id)
            if default_row is None:
                default_row = models.CatalogImageDefault(
                    id=str(uuid4()),
                    device_type_id=device_type_id,
                    image_id=row.id,
                    source="api",
                )
                session.add(default_row)
                defaults_by_device_type_id[device_type_id] = default_row
            else:
                default_row.image_id = row.id
                default_row.source = "api"

        for device_type_id, default_row in list(defaults_by_device_type_id.items()):
            if default_row.image_id != row.id:
                continue
            if device_type_id in desired_default_type_ids:
                continue
            session.delete(default_row)
            defaults_by_device_type_id.pop(device_type_id, None)

    session.flush()


def record_catalog_ingest_event(
    session: Session,
    *,
    source: str,
    event_type: str,
    summary: str,
    payload: dict[str, Any],
) -> None:
    session.add(
        models.CatalogIngestEvent(
            id=str(uuid4()),
            source=source,
            event_type=event_type,
            summary=summary,
            payload_json=_json_dump(payload),
        )
    )


def apply_manifest_style_image_update(
    session: Session,
    image_id: str,
    updates: dict[str, Any],
    *,
    event_type: str,
    summary: str,
    source: str = "api.images",
) -> dict[str, Any]:
    """Apply update semantics using image_store.update_image_entry against DB projection."""
    from app.image_store import update_image_entry

    snapshot = list_catalog_library_images(session, force_refresh=True)
    manifest_obj = {"images": [dict(item) for item in snapshot]}
    updated = update_image_entry(manifest_obj, image_id, dict(updates))
    if not updated:
        raise CatalogImageNotFoundError(f"Image '{image_id}' not found")

    _persist_catalog_snapshot(session, manifest_obj["images"])
    record_catalog_ingest_event(
        session,
        source=source,
        event_type=event_type,
        summary=summary,
        payload={"image_id": image_id, "updates": updates},
    )
    invalidate_image_index_cache(session)
    return dict(updated)


def delete_catalog_image(
    session: Session,
    image_id: str,
    *,
    source: str = "api.images",
) -> dict[str, Any]:
    """Delete image row and compatibility/default records from catalog."""
    row = (
        session.query(models.CatalogImage)
        .filter(models.CatalogImage.external_id == image_id)
        .first()
    )
    if row is None:
        raise CatalogImageNotFoundError(f"Image '{image_id}' not found")

    snapshot = get_catalog_library_image(session, image_id, force_refresh=True) or {"id": image_id}
    session.delete(row)
    record_catalog_ingest_event(
        session,
        source=source,
        event_type="image_delete",
        summary=f"Deleted image '{image_id}'",
        payload={"image_id": image_id},
    )
    invalidate_image_index_cache(session)
    return snapshot


def sync_catalog_from_manifest(
    session: Session,
    manifest: dict[str, Any],
    *,
    source: str = "manifest_sync",
    prune_missing: bool = True,
) -> None:
    """Synchronize catalog image tables from a manifest payload.

    Args:
        session: Active SQLAlchemy session.
        manifest: Manifest-style payload containing ``images`` entries.
        source: Event source label recorded in ingest history.
        prune_missing: When True, delete catalog rows not present in payload.
            When False, only upsert/update rows from the payload.
    """
    images = [
        dict(entry)
        for entry in (manifest.get("images") or [])
        if isinstance(entry, dict)
    ]
    desired_external_ids = {
        str(entry.get("id") or "").strip()
        for entry in images
        if str(entry.get("id") or "").strip()
    }

    _persist_catalog_snapshot(session, images)

    deleted_count = 0
    if prune_missing:
        deleted_count = (
            session.query(models.CatalogImage)
            .filter(~models.CatalogImage.external_id.in_(desired_external_ids))
            .delete(synchronize_session="fetch")
        )

    record_catalog_ingest_event(
        session,
        source=source,
        event_type="manifest_sync",
        summary="Synchronized catalog from manifest",
        payload={
            "image_count": len(desired_external_ids),
            "prune_missing": prune_missing,
            "deleted_count": deleted_count,
        },
    )
    invalidate_image_index_cache(session)
