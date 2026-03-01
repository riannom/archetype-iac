"""DB-backed catalog helpers for device/image identity and compatibility.

Core types, exception classes, token normalization, cache management, and
catalog config helpers. Companion modules (catalog_identity, catalog_query)
hold the bulk of the logic; all public names are re-exported below.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from app import models

log = logging.getLogger(__name__)

# File extensions that represent file-based images (not container images)
_FILE_BASED_EXTENSIONS = frozenset({".qcow2", ".img", ".iol", ".bin"})
_NON_WORD_RE = re.compile(r"[^a-z0-9]+")


# ---------------------------------------------------------------------------
# Exception classes
# ---------------------------------------------------------------------------

class CatalogImageNotFoundError(ValueError):
    """Raised when an image ID is not present in the catalog."""


class CatalogAliasConflictError(ValueError):
    """Raised when an alias resolves to multiple canonical device IDs."""


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class AliasIndex:
    canonical_by_type_id: dict[str, str]
    type_id_by_canonical: dict[str, str]
    canonical_to_aliases: dict[str, set[str]]
    alias_to_canonicals: dict[str, set[str]]


@dataclass
class ImageIndexCache:
    stamp: tuple[Any, ...]
    ordered_images: list[dict[str, Any]]
    ordered_ids: list[str]
    images_by_external_id: dict[str, dict[str, Any]]
    image_ids_by_canonical: dict[str, set[str]]
    alias_index: AliasIndex


# ---------------------------------------------------------------------------
# Module-level state (shared by companion modules)
# ---------------------------------------------------------------------------

_CACHE_LOCK = threading.Lock()
_IMAGE_INDEX_CACHE_BY_BIND: dict[int, ImageIndexCache] = {}
_IDENTITY_SYNC_LOCK = threading.Lock()
_IDENTITY_SYNC_STAMP_BY_BIND: dict[int, str] = {}
_CATALOG_IDENTITY_ADVISORY_LOCK_KEY = 814250991337

_ALIAS_TYPE_RANK = {
    "compatibility": 0,
    "runtime_kind": 1,
    "explicit": 2,
}


# ---------------------------------------------------------------------------
# Token normalization / utility helpers
# ---------------------------------------------------------------------------

def _normalize_token(value: Any) -> str | None:
    if value is None:
        return None
    token = str(value).strip().lower()
    return token or None


def _vendor_key(name: Any) -> str:
    normalized = _normalize_token(name) or "unknown"
    return _NON_WORD_RE.sub("_", normalized).strip("_") or "unknown"


def _json_load(text: str | None) -> dict[str, Any]:
    if not text:
        return {}
    try:
        data = json.loads(text)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _json_dump(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1]
        if not text.endswith("+00:00"):
            text = f"{text}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _normalize_string_set(values: Any) -> list[str]:
    normalized: set[str] = set()
    for value in values or []:
        token = _normalize_token(value)
        if token:
            normalized.add(token)
    return sorted(normalized)


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _bind_cache_key(session: Session) -> int:
    return id(session.get_bind())


def invalidate_image_index_cache(session: Session | None = None) -> None:
    with _CACHE_LOCK:
        if session is None:
            _IMAGE_INDEX_CACHE_BY_BIND.clear()
            return
        _IMAGE_INDEX_CACHE_BY_BIND.pop(_bind_cache_key(session), None)


# ---------------------------------------------------------------------------
# Catalog availability checks
# ---------------------------------------------------------------------------

def catalog_is_seeded(session: Session) -> bool:
    """Return True when catalog tables exist and contain at least one device."""
    try:
        return session.query(models.CatalogDeviceType.id).limit(1).first() is not None
    except (ProgrammingError, Exception):
        try:
            session.rollback()
        except Exception:
            pass
        return False


def _catalog_tables_available(session: Session) -> bool:
    try:
        session.query(models.CatalogVendor.id).limit(1).first()
        session.query(models.CatalogDeviceType.id).limit(1).first()
        session.query(models.CatalogDeviceAlias.id).limit(1).first()
        session.query(models.CatalogDeviceRevision.id).limit(1).first()
        return True
    except (OperationalError, ProgrammingError, Exception):
        try:
            session.rollback()
        except Exception:
            pass
        return False


# ---------------------------------------------------------------------------
# Re-exports from companion modules
# ---------------------------------------------------------------------------

from .catalog_identity import (  # noqa: E402
    DesiredCatalogDevice,
    _acquire_catalog_identity_advisory_lock,
    _build_alias_index,
    _build_desired_catalog_identity_data,
    _catalog_identity_stamp,
    _register_alias,
    _resolve_token_to_canonical_set,
    ensure_catalog_identity_synced,
    get_catalog_compatibility_aliases,
    get_catalog_identity_map,
    resolve_catalog_compatible_device_set,
    resolve_catalog_device_id,
)

from .catalog_query import (  # noqa: E402
    _build_index_stamp,
    _ensure_device_type,
    _ensure_unknown_vendor,
    _get_image_index,
    _persist_catalog_snapshot,
    _project_catalog_images,
    _resolve_writable_canonical_device_id,
    apply_manifest_style_image_update,
    count_catalog_images_for_device,
    delete_catalog_image,
    get_catalog_library_image,
    list_catalog_images_for_device,
    list_catalog_library_images,
    record_catalog_ingest_event,
    sync_catalog_from_manifest,
)
