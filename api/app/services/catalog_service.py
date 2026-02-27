"""DB-backed catalog helpers for device/image identity and compatibility."""
from __future__ import annotations

import logging
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
import threading
from typing import Any
from uuid import uuid4

from sqlalchemy import func, text as sa_text
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from app import models

log = logging.getLogger(__name__)

# File extensions that represent file-based images (not container images)
_FILE_BASED_EXTENSIONS = frozenset({".qcow2", ".img", ".iol", ".bin"})
_NON_WORD_RE = re.compile(r"[^a-z0-9]+")


class CatalogImageNotFoundError(ValueError):
    """Raised when an image ID is not present in the catalog."""


class CatalogAliasConflictError(ValueError):
    """Raised when an alias resolves to multiple canonical device IDs."""


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


def _bind_cache_key(session: Session) -> int:
    return id(session.get_bind())


def invalidate_image_index_cache(session: Session | None = None) -> None:
    with _CACHE_LOCK:
        if session is None:
            _IMAGE_INDEX_CACHE_BY_BIND.clear()
            return
        _IMAGE_INDEX_CACHE_BY_BIND.pop(_bind_cache_key(session), None)


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


@dataclass
class DesiredCatalogDevice:
    canonical_device_id: str
    vendor_key: str
    runtime_kind: str | None
    display_name: str
    device_class: str | None
    source: str
    lifecycle_status: str
    metadata_json: str
    aliases: dict[str, str]
    revision_runtime_kind: str | None
    revision_memory_mb: int | None
    revision_cpu_count: int | None
    revision_max_ports: int | None
    revision_supported_image_kinds_json: str
    revision_metadata_json: str


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


def _normalize_string_set(values: Any) -> list[str]:
    normalized: set[str] = set()
    for value in values or []:
        token = _normalize_token(value)
        if token:
            normalized.add(token)
    return sorted(normalized)


def _register_alias(
    alias_map: dict[str, str],
    canonical_device_id: str,
    alias: Any,
    alias_type: str,
) -> None:
    normalized = _normalize_token(alias)
    if not normalized or normalized == canonical_device_id:
        return
    existing_type = alias_map.get(normalized)
    existing_rank = _ALIAS_TYPE_RANK.get(existing_type or "", -1)
    incoming_rank = _ALIAS_TYPE_RANK.get(alias_type, -1)
    if existing_type is None or incoming_rank >= existing_rank:
        alias_map[normalized] = alias_type


def _build_desired_catalog_identity_data() -> tuple[dict[str, dict[str, Any]], dict[str, DesiredCatalogDevice]]:
    from agent.vendors import VENDOR_CONFIGS
    from app.image_store import get_image_compatibility_aliases, load_custom_devices

    desired_vendors: dict[str, dict[str, Any]] = {}
    desired_devices: dict[str, DesiredCatalogDevice] = {}

    compatibility_aliases: dict[str, list[str]] = {}
    for raw_canonical, raw_aliases in (get_image_compatibility_aliases() or {}).items():
        canonical = _normalize_token(raw_canonical)
        if not canonical:
            continue
        aliases = _normalize_string_set(raw_aliases)
        if aliases:
            compatibility_aliases[canonical] = aliases

    def ensure_vendor(display_name: Any) -> str:
        vendor_key = _vendor_key(display_name)
        desired_vendors.setdefault(
            vendor_key,
            {
                "vendor_key": vendor_key,
                "display_name": str(display_name or vendor_key).strip() or vendor_key,
                "lifecycle_status": "active",
                "metadata_json": "{}",
            },
        )
        return vendor_key

    for raw_canonical, config in sorted(VENDOR_CONFIGS.items()):
        canonical = _normalize_token(raw_canonical)
        if not canonical:
            continue
        vendor_key = ensure_vendor(getattr(config, "vendor", None) or "Unknown")
        runtime_kind = _normalize_token(getattr(config, "kind", None))
        explicit_aliases = _normalize_string_set(getattr(config, "aliases", None) or [])
        tags = _normalize_string_set(getattr(config, "tags", None) or [])

        alias_map: dict[str, str] = {}
        _register_alias(alias_map, canonical, runtime_kind, "runtime_kind")
        for alias in explicit_aliases:
            _register_alias(alias_map, canonical, alias, "explicit")
        for alias in compatibility_aliases.get(canonical, []):
            _register_alias(alias_map, canonical, alias, "compatibility")

        metadata_json = _json_dump(
            {
                "icon": getattr(config, "icon", None),
                "aliases": explicit_aliases,
                "tags": tags,
                "vendor_options": bool(getattr(config, "vendor_options", None)),
            }
        )
        desired_devices[canonical] = DesiredCatalogDevice(
            canonical_device_id=canonical,
            vendor_key=vendor_key,
            runtime_kind=runtime_kind,
            display_name=str(getattr(config, "label", None) or canonical),
            device_class=_normalize_token(getattr(getattr(config, "device_type", None), "value", None)),
            source="builtin",
            lifecycle_status="active",
            metadata_json=metadata_json,
            aliases=alias_map,
            revision_runtime_kind=runtime_kind,
            revision_memory_mb=_to_int(getattr(config, "memory", None)),
            revision_cpu_count=_to_int(getattr(config, "cpu", None)),
            revision_max_ports=_to_int(getattr(config, "max_ports", None)),
            revision_supported_image_kinds_json=json.dumps(
                _normalize_string_set(getattr(config, "supported_image_kinds", None) or []),
                sort_keys=True,
                separators=(",", ":"),
            ),
            revision_metadata_json=_json_dump({"source": "builtin_vendor_config"}),
        )

    for entry in load_custom_devices() or []:
        if not isinstance(entry, dict):
            continue
        canonical = _normalize_token(entry.get("id"))
        if not canonical:
            continue
        # Built-ins always win over custom devices.
        existing = desired_devices.get(canonical)
        if existing and existing.source == "builtin":
            continue

        vendor_key = ensure_vendor(entry.get("vendor") or "Custom")
        runtime_kind = _normalize_token(entry.get("kind")) or canonical
        alias_map: dict[str, str] = {}
        _register_alias(alias_map, canonical, runtime_kind, "runtime_kind")
        for alias in entry.get("aliases") or []:
            _register_alias(alias_map, canonical, alias, "explicit")

        desired_devices[canonical] = DesiredCatalogDevice(
            canonical_device_id=canonical,
            vendor_key=vendor_key,
            runtime_kind=runtime_kind,
            display_name=str(entry.get("name") or canonical),
            device_class=_normalize_token(entry.get("type")),
            source="custom",
            lifecycle_status="active",
            metadata_json=_json_dump(entry),
            aliases=alias_map,
            revision_runtime_kind=runtime_kind,
            revision_memory_mb=_to_int(entry.get("memory")),
            revision_cpu_count=_to_int(entry.get("cpu")),
            revision_max_ports=_to_int(entry.get("maxPorts")),
            revision_supported_image_kinds_json=json.dumps(
                _normalize_string_set(entry.get("supportedImageKinds") or []),
                sort_keys=True,
                separators=(",", ":"),
            ),
            revision_metadata_json=_json_dump({"source": "custom_device"}),
        )

    return desired_vendors, desired_devices


def _catalog_identity_stamp(
    desired_vendors: dict[str, dict[str, Any]],
    desired_devices: dict[str, DesiredCatalogDevice],
) -> str:
    payload = {
        "vendors": [
            desired_vendors[key]
            for key in sorted(desired_vendors.keys())
        ],
        "devices": [
            {
                "canonical_device_id": device.canonical_device_id,
                "vendor_key": device.vendor_key,
                "runtime_kind": device.runtime_kind,
                "display_name": device.display_name,
                "device_class": device.device_class,
                "source": device.source,
                "lifecycle_status": device.lifecycle_status,
                "metadata_json": device.metadata_json,
                "aliases": sorted(device.aliases.items()),
                "revision_runtime_kind": device.revision_runtime_kind,
                "revision_memory_mb": device.revision_memory_mb,
                "revision_cpu_count": device.revision_cpu_count,
                "revision_max_ports": device.revision_max_ports,
                "revision_supported_image_kinds_json": device.revision_supported_image_kinds_json,
                "revision_metadata_json": device.revision_metadata_json,
            }
            for _, device in sorted(desired_devices.items(), key=lambda item: item[0])
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _acquire_catalog_identity_advisory_lock(session: Session) -> None:
    bind = session.get_bind()
    if bind is None or bind.dialect.name != "postgresql":
        return
    session.execute(
        sa_text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": _CATALOG_IDENTITY_ADVISORY_LOCK_KEY},
    )


def ensure_catalog_identity_synced(
    session: Session,
    *,
    force: bool = False,
    source: str = "runtime_identity_sync",
) -> dict[str, Any]:
    """Upsert vendor/device/alias identity rows from runtime sources.

    This keeps catalog identity tables synchronized with the live vendor registry
    and custom-device definitions. On PostgreSQL, a transaction advisory lock is
    used to serialize concurrent sync attempts across processes.
    """
    if not _catalog_tables_available(session):
        return {"applied": False, "reason": "catalog_tables_unavailable"}

    desired_vendors, desired_devices = _build_desired_catalog_identity_data()
    stamp = _catalog_identity_stamp(desired_vendors, desired_devices)
    bind_key = _bind_cache_key(session)

    with _IDENTITY_SYNC_LOCK:
        if (
            not force
            and _IDENTITY_SYNC_STAMP_BY_BIND.get(bind_key) == stamp
            and catalog_is_seeded(session)
        ):
            return {"applied": False, "reason": "cache_hit", "stamp": stamp}

        try:
            _acquire_catalog_identity_advisory_lock(session)

            vendor_rows = session.query(models.CatalogVendor).all()
            vendors_by_key = {row.vendor_key: row for row in vendor_rows}

            device_rows = session.query(models.CatalogDeviceType).all()
            devices_by_canonical = {row.canonical_device_id: row for row in device_rows}

            alias_rows = session.query(models.CatalogDeviceAlias).all()
            aliases_by_pair = {(row.alias, row.device_type_id): row for row in alias_rows}

            revision_rows = (
                session.query(models.CatalogDeviceRevision)
                .filter(models.CatalogDeviceRevision.version_tag == "current")
                .all()
            )
            revisions_by_key = {
                (row.device_type_id, row.version_tag): row
                for row in revision_rows
            }

            stats = {
                "vendors_created": 0,
                "vendors_updated": 0,
                "devices_created": 0,
                "devices_updated": 0,
                "devices_retired": 0,
                "aliases_created": 0,
                "aliases_updated": 0,
                "aliases_deactivated": 0,
                "revisions_created": 0,
                "revisions_updated": 0,
            }

            for vendor_key, desired in desired_vendors.items():
                row = vendors_by_key.get(vendor_key)
                if row is None:
                    row = models.CatalogVendor(
                        id=str(uuid4()),
                        vendor_key=vendor_key,
                        display_name=desired["display_name"],
                        lifecycle_status=desired["lifecycle_status"],
                        metadata_json=desired["metadata_json"],
                    )
                    session.add(row)
                    vendors_by_key[vendor_key] = row
                    stats["vendors_created"] += 1
                    continue

                changed = False
                if row.display_name != desired["display_name"]:
                    row.display_name = desired["display_name"]
                    changed = True
                if row.lifecycle_status != desired["lifecycle_status"]:
                    row.lifecycle_status = desired["lifecycle_status"]
                    changed = True
                if row.metadata_json != desired["metadata_json"]:
                    row.metadata_json = desired["metadata_json"]
                    changed = True
                if changed:
                    stats["vendors_updated"] += 1

            managed_device_ids: set[str] = set()
            desired_alias_pairs: set[tuple[str, str]] = set()
            managed_scope_device_ids = {
                row.id for row in device_rows if row.source in {"builtin", "custom"}
            }

            for canonical, desired in sorted(desired_devices.items(), key=lambda item: item[0]):
                vendor_row = vendors_by_key.get(desired.vendor_key)
                if vendor_row is None:
                    continue

                row = devices_by_canonical.get(canonical)
                if row is None:
                    row = models.CatalogDeviceType(
                        id=str(uuid4()),
                        canonical_device_id=canonical,
                        vendor_id=vendor_row.id,
                        runtime_kind=desired.runtime_kind,
                        display_name=desired.display_name,
                        device_class=desired.device_class,
                        source=desired.source,
                        lifecycle_status=desired.lifecycle_status,
                        metadata_json=desired.metadata_json,
                    )
                    session.add(row)
                    devices_by_canonical[canonical] = row
                    stats["devices_created"] += 1
                else:
                    changed = False
                    if row.vendor_id != vendor_row.id:
                        row.vendor_id = vendor_row.id
                        changed = True
                    if row.runtime_kind != desired.runtime_kind:
                        row.runtime_kind = desired.runtime_kind
                        changed = True
                    if row.display_name != desired.display_name:
                        row.display_name = desired.display_name
                        changed = True
                    if row.device_class != desired.device_class:
                        row.device_class = desired.device_class
                        changed = True
                    if row.source != desired.source:
                        row.source = desired.source
                        changed = True
                    if row.lifecycle_status != desired.lifecycle_status:
                        row.lifecycle_status = desired.lifecycle_status
                        changed = True
                    if row.metadata_json != desired.metadata_json:
                        row.metadata_json = desired.metadata_json
                        changed = True
                    if changed:
                        stats["devices_updated"] += 1

                managed_device_ids.add(row.id)
                managed_scope_device_ids.add(row.id)

                revision_key = (row.id, "current")
                revision = revisions_by_key.get(revision_key)
                if revision is None:
                    revision = models.CatalogDeviceRevision(
                        id=str(uuid4()),
                        device_type_id=row.id,
                        version_tag="current",
                        runtime_kind=desired.revision_runtime_kind,
                        memory_mb=desired.revision_memory_mb,
                        cpu_count=desired.revision_cpu_count,
                        max_ports=desired.revision_max_ports,
                        supported_image_kinds_json=desired.revision_supported_image_kinds_json,
                        metadata_json=desired.revision_metadata_json,
                        is_current=True,
                    )
                    session.add(revision)
                    revisions_by_key[revision_key] = revision
                    stats["revisions_created"] += 1
                else:
                    changed = False
                    if revision.runtime_kind != desired.revision_runtime_kind:
                        revision.runtime_kind = desired.revision_runtime_kind
                        changed = True
                    if revision.memory_mb != desired.revision_memory_mb:
                        revision.memory_mb = desired.revision_memory_mb
                        changed = True
                    if revision.cpu_count != desired.revision_cpu_count:
                        revision.cpu_count = desired.revision_cpu_count
                        changed = True
                    if revision.max_ports != desired.revision_max_ports:
                        revision.max_ports = desired.revision_max_ports
                        changed = True
                    if revision.supported_image_kinds_json != desired.revision_supported_image_kinds_json:
                        revision.supported_image_kinds_json = desired.revision_supported_image_kinds_json
                        changed = True
                    if revision.metadata_json != desired.revision_metadata_json:
                        revision.metadata_json = desired.revision_metadata_json
                        changed = True
                    if not revision.is_current:
                        revision.is_current = True
                        changed = True
                    if revision.valid_to is not None:
                        revision.valid_to = None
                        changed = True
                    if changed:
                        stats["revisions_updated"] += 1

                for alias, alias_type in desired.aliases.items():
                    desired_alias_pairs.add((alias, row.id))
                    alias_row = aliases_by_pair.get((alias, row.id))
                    if alias_row is None:
                        alias_row = models.CatalogDeviceAlias(
                            id=str(uuid4()),
                            device_type_id=row.id,
                            alias=alias,
                            alias_type=alias_type,
                            source=source,
                            is_active=True,
                        )
                        session.add(alias_row)
                        aliases_by_pair[(alias, row.id)] = alias_row
                        stats["aliases_created"] += 1
                        continue

                    changed = False
                    current_rank = _ALIAS_TYPE_RANK.get(alias_row.alias_type, -1)
                    incoming_rank = _ALIAS_TYPE_RANK.get(alias_type, -1)
                    if alias_row.alias_type != alias_type and incoming_rank > current_rank:
                        alias_row.alias_type = alias_type
                        changed = True
                    if alias_row.source != source:
                        alias_row.source = source
                        changed = True
                    if not alias_row.is_active:
                        alias_row.is_active = True
                        changed = True
                    if changed:
                        stats["aliases_updated"] += 1

            for row in device_rows:
                if row.source not in {"builtin", "custom"}:
                    continue
                if row.id in managed_device_ids:
                    continue
                if row.lifecycle_status != "retired":
                    row.lifecycle_status = "retired"
                    stats["devices_retired"] += 1

            for (alias, device_type_id), row in aliases_by_pair.items():
                if device_type_id not in managed_scope_device_ids:
                    continue
                if (alias, device_type_id) in desired_alias_pairs:
                    continue
                if row.is_active:
                    row.is_active = False
                    stats["aliases_deactivated"] += 1

            has_changes = any(value > 0 for value in stats.values())
            if has_changes:
                record_catalog_ingest_event(
                    session,
                    source=source,
                    event_type="identity_sync",
                    summary="Synchronized catalog identity tables",
                    payload={
                        **stats,
                        "device_count": len(desired_devices),
                        "vendor_count": len(desired_vendors),
                        "stamp": stamp,
                    },
                )
                session.commit()

            _IDENTITY_SYNC_STAMP_BY_BIND[bind_key] = stamp
            return {
                "applied": has_changes,
                "reason": "updated" if has_changes else "already_current",
                "stamp": stamp,
                **stats,
            }
        except Exception:
            session.rollback()
            raise


def _build_alias_index(session: Session) -> AliasIndex:
    canonical_by_type_id: dict[str, str] = {}
    type_id_by_canonical: dict[str, str] = {}
    canonical_to_aliases: dict[str, set[str]] = defaultdict(set)
    alias_to_canonicals: dict[str, set[str]] = defaultdict(set)

    for row in session.query(models.CatalogDeviceType).all():
        canonical = _normalize_token(row.canonical_device_id)
        if not canonical:
            continue
        canonical_by_type_id[row.id] = canonical
        type_id_by_canonical[canonical] = row.id

        runtime_kind = _normalize_token(row.runtime_kind)
        if runtime_kind and runtime_kind != canonical:
            canonical_to_aliases[canonical].add(runtime_kind)
            alias_to_canonicals[runtime_kind].add(canonical)

    alias_rows = (
        session.query(models.CatalogDeviceAlias)
        .filter(models.CatalogDeviceAlias.is_active.is_(True))
        .all()
    )
    for row in alias_rows:
        canonical = canonical_by_type_id.get(row.device_type_id)
        alias = _normalize_token(row.alias)
        if not canonical or not alias or alias == canonical:
            continue
        canonical_to_aliases[canonical].add(alias)
        alias_to_canonicals[alias].add(canonical)

    return AliasIndex(
        canonical_by_type_id=canonical_by_type_id,
        type_id_by_canonical=type_id_by_canonical,
        canonical_to_aliases=canonical_to_aliases,
        alias_to_canonicals=alias_to_canonicals,
    )


def get_catalog_compatibility_aliases(session: Session) -> dict[str, list[str]]:
    """Return canonical_device_id -> compatibility alias list from DB."""
    alias_index = _build_alias_index(session)
    return {
        canonical: sorted(aliases)
        for canonical, aliases in alias_index.canonical_to_aliases.items()
        if aliases
    }


def get_catalog_identity_map(session: Session) -> dict[str, Any]:
    """Return canonical/runtime/alias identity metadata from DB tables."""
    alias_index = _build_alias_index(session)
    runtime_kind_by_canonical: dict[str, str | None] = {}
    canonical_to_aliases: dict[str, list[str]] = {}

    for row in session.query(models.CatalogDeviceType).all():
        canonical = _normalize_token(row.canonical_device_id)
        if not canonical:
            continue
        runtime_kind_by_canonical[canonical] = _normalize_token(row.runtime_kind)
        canonical_to_aliases[canonical] = sorted(alias_index.canonical_to_aliases.get(canonical, set()))

    alias_to_canonicals = {
        alias: sorted(canonicals)
        for alias, canonicals in alias_index.alias_to_canonicals.items()
        if canonicals
    }

    # Frontend alias resolution should only use unambiguous tokens.
    interface_aliases: dict[str, str] = {}
    for canonical in runtime_kind_by_canonical:
        interface_aliases[canonical] = canonical
    for alias, canonicals in alias_to_canonicals.items():
        if len(canonicals) == 1:
            interface_aliases[alias] = canonicals[0]

    return {
        "canonical_to_runtime_kind": runtime_kind_by_canonical,
        "canonical_to_aliases": canonical_to_aliases,
        "alias_to_canonicals": alias_to_canonicals,
        "interface_aliases": interface_aliases,
    }


def _resolve_token_to_canonical_set(alias_index: AliasIndex, device_id: str | None) -> set[str]:
    normalized = _normalize_token(device_id)
    if not normalized:
        return set()

    seed: set[str] = set()
    if normalized in alias_index.type_id_by_canonical:
        seed.add(normalized)
    seed.update(alias_index.alias_to_canonicals.get(normalized, set()))
    if not seed:
        return set()

    # Resolve reverse alias relationships: shared alias token maps to families.
    resolved = set(seed)
    for canonical in list(seed):
        for alias in alias_index.canonical_to_aliases.get(canonical, set()):
            resolved.update(alias_index.alias_to_canonicals.get(alias, set()))
    return resolved


def resolve_catalog_device_id(
    session: Session,
    device_id: str | None,
    *,
    allow_unknown: bool = True,
) -> str | None:
    """Resolve token -> canonical device ID using DB aliases."""
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
    return normalized if allow_unknown else None


def resolve_catalog_compatible_device_set(session: Session, device_id: str | None) -> set[str]:
    """Resolve a token to all canonical device IDs in its compatibility family."""
    alias_index = _build_alias_index(session)
    return _resolve_token_to_canonical_set(alias_index, device_id)


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
        if row.imported_at:
            image["imported_at"] = row.imported_at.isoformat().replace("+00:00", "Z")

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
                metadata_json=_json_dump(image),
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
        row.vendor_name = image.get("vendor")
        row.version = image.get("version")
        row.source = str(image.get("source") or row.source or "api")
        imported_at = _parse_timestamp(image.get("uploaded_at") or image.get("imported_at"))
        if imported_at:
            row.imported_at = imported_at
        row.metadata_json = _json_dump(image)

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
) -> None:
    """Synchronize catalog image tables from a manifest payload."""
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

    for row in session.query(models.CatalogImage).all():
        if row.external_id in desired_external_ids:
            continue
        session.delete(row)

    record_catalog_ingest_event(
        session,
        source=source,
        event_type="manifest_sync",
        summary="Synchronized catalog from manifest",
        payload={"image_count": len(desired_external_ids)},
    )
    invalidate_image_index_cache(session)
