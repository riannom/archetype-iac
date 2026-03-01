"""Identity building/stamping, alias resolution, and catalog sync with advisory locks."""
from __future__ import annotations

import hashlib
import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

from app import models

if TYPE_CHECKING:
    from .catalog_service import AliasIndex

from .catalog_service import (
    _ALIAS_TYPE_RANK,
    _CATALOG_IDENTITY_ADVISORY_LOCK_KEY,
    _IDENTITY_SYNC_LOCK,
    _IDENTITY_SYNC_STAMP_BY_BIND,
    _bind_cache_key,
    _catalog_tables_available,
    _json_dump,
    _normalize_string_set,
    _normalize_token,
    _to_int,
    _vendor_key,
    catalog_is_seeded,
)

log = logging.getLogger(__name__)


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
    from .catalog_query import record_catalog_ingest_event

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
        from .catalog_service import CatalogAliasConflictError
        raise CatalogAliasConflictError(
            f"Alias '{normalized}' maps to multiple devices: {sorted(matches)}. "
            "Use canonical device ID."
        )
    return normalized if allow_unknown else None


def resolve_catalog_compatible_device_set(session: Session, device_id: str | None) -> set[str]:
    """Resolve a token to all canonical device IDs in its compatibility family."""
    alias_index = _build_alias_index(session)
    return _resolve_token_to_canonical_set(alias_index, device_id)
