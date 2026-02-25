#!/usr/bin/env python3
"""Backfill canonical device/image catalog tables from current sources.

Sources:
- Built-in vendor registry (agent.vendors.VENDOR_CONFIGS)
- Custom devices (app.image_store.load_custom_devices)
- Image manifest (default: /var/lib/archetype/images/manifest.json)

Default mode is dry-run. Use --apply to persist.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any
from uuid import uuid4

from sqlalchemy.exc import OperationalError, ProgrammingError


DEFAULT_MANIFEST = Path("/var/lib/archetype/images/manifest.json")
REPO_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPO_ROOT / "api"

for _path in (REPO_ROOT, API_ROOT):
    _path_str = str(_path)
    if _path_str not in sys.path:
        sys.path.insert(0, _path_str)

from agent.vendors import VENDOR_CONFIGS  # noqa: E402
from app import models  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.image_store import (  # noqa: E402
    canonicalize_device_id,
    canonicalize_device_ids,
    get_image_compatibility_aliases,
    load_custom_devices,
    normalize_default_device_scope_ids,
)


_NON_WORD_RE = re.compile(r"[^a-z0-9]+")
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


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1]
        if text.endswith("+00:00"):
            pass
        else:
            text = f"{text}+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _json_text(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _maybe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _is_builtin_device(device: models.CatalogDeviceType | None) -> bool:
    return bool(device and device.source in {"builtin", "seed"})


@dataclass
class BackfillStats:
    vendors_created: int = 0
    vendors_updated: int = 0
    devices_created: int = 0
    devices_updated: int = 0
    aliases_created: int = 0
    aliases_updated: int = 0
    alias_conflicts: int = 0
    revisions_created: int = 0
    revisions_updated: int = 0
    images_created: int = 0
    images_updated: int = 0
    compat_created: int = 0
    compat_deleted: int = 0
    defaults_created: int = 0
    defaults_updated: int = 0
    defaults_deleted: int = 0
    discovered_devices: int = 0
    warnings: list[str] = field(default_factory=list)


class CatalogBackfill:
    def __init__(self, session, manifest: dict[str, Any], apply: bool) -> None:
        self.session = session
        self.manifest = manifest
        self.apply = apply
        self.stats = BackfillStats()

        self.vendors_by_key = {
            row.vendor_key: row for row in self.session.query(models.CatalogVendor).all()
        }
        self.devices_by_canonical = {
            row.canonical_device_id: row
            for row in self.session.query(models.CatalogDeviceType).all()
        }
        self.aliases_by_key = {
            (row.alias, row.device_type_id): row
            for row in self.session.query(models.CatalogDeviceAlias).all()
        }
        self.revisions_by_key = {
            (row.device_type_id, row.version_tag): row
            for row in self.session.query(models.CatalogDeviceRevision).all()
        }
        self.images_by_external_id = {
            row.external_id: row for row in self.session.query(models.CatalogImage).all()
        }

        self.compat_by_image_id: dict[str, dict[str, models.CatalogImageCompatibility]] = defaultdict(dict)
        for row in self.session.query(models.CatalogImageCompatibility).all():
            self.compat_by_image_id[row.image_id][row.device_type_id] = row

        self.defaults_by_device_id = {
            row.device_type_id: row for row in self.session.query(models.CatalogImageDefault).all()
        }

    def ensure_vendor(self, display_name: Any) -> models.CatalogVendor:
        key = _vendor_key(display_name)
        display = str(display_name or key).strip() or key
        row = self.vendors_by_key.get(key)
        if row is None:
            row = models.CatalogVendor(
                id=str(uuid4()),
                vendor_key=key,
                display_name=display,
                lifecycle_status="active",
                metadata_json="{}",
            )
            self.session.add(row)
            self.session.flush()
            self.vendors_by_key[key] = row
            self.stats.vendors_created += 1
        else:
            changed = False
            if row.display_name != display:
                row.display_name = display
                changed = True
            if row.lifecycle_status != "active":
                row.lifecycle_status = "active"
                changed = True
            if changed:
                self.stats.vendors_updated += 1
        return row

    def ensure_device(
        self,
        canonical_device_id: str,
        vendor_id: str,
        runtime_kind: str | None,
        display_name: str,
        device_class: str | None,
        source: str,
        metadata: dict[str, Any],
        lifecycle_status: str = "active",
    ) -> models.CatalogDeviceType:
        row = self.devices_by_canonical.get(canonical_device_id)
        metadata_json = _json_text(metadata)
        if row is None:
            row = models.CatalogDeviceType(
                id=str(uuid4()),
                canonical_device_id=canonical_device_id,
                vendor_id=vendor_id,
                runtime_kind=runtime_kind,
                display_name=display_name,
                device_class=device_class,
                source=source,
                lifecycle_status=lifecycle_status,
                metadata_json=metadata_json,
            )
            self.session.add(row)
            self.session.flush()
            self.devices_by_canonical[canonical_device_id] = row
            self.stats.devices_created += 1
        else:
            changed = False
            if row.vendor_id != vendor_id:
                row.vendor_id = vendor_id
                changed = True
            if row.runtime_kind != runtime_kind:
                row.runtime_kind = runtime_kind
                changed = True
            if row.display_name != display_name:
                row.display_name = display_name
                changed = True
            if row.device_class != device_class:
                row.device_class = device_class
                changed = True
            if row.source != source:
                row.source = source
                changed = True
            if row.lifecycle_status != lifecycle_status:
                row.lifecycle_status = lifecycle_status
                changed = True
            if row.metadata_json != metadata_json:
                row.metadata_json = metadata_json
                changed = True
            if changed:
                self.stats.devices_updated += 1
        return row

    def ensure_alias(
        self,
        alias: str,
        device: models.CatalogDeviceType,
        alias_type: str,
        source: str = "seed",
    ) -> None:
        normalized = _normalize_token(alias)
        if not normalized or normalized == device.canonical_device_id:
            return

        key = (normalized, device.id)
        existing = self.aliases_by_key.get(key)

        if existing is None:
            row = models.CatalogDeviceAlias(
                id=str(uuid4()),
                device_type_id=device.id,
                alias=normalized,
                alias_type=alias_type,
                source=source,
                is_active=True,
            )
            self.session.add(row)
            self.aliases_by_key[key] = row
            self.stats.aliases_created += 1
            return

        changed = False
        current_rank = _ALIAS_TYPE_RANK.get(existing.alias_type, -1)
        incoming_rank = _ALIAS_TYPE_RANK.get(alias_type, -1)
        if existing.alias_type != alias_type and incoming_rank > current_rank:
            existing.alias_type = alias_type
            changed = True
        if existing.source != source:
            existing.source = source
            changed = True
        if not existing.is_active:
            existing.is_active = True
            changed = True
        if changed:
            self.stats.aliases_updated += 1

    def ensure_revision(
        self,
        device: models.CatalogDeviceType,
        runtime_kind: str | None,
        memory_mb: int | None,
        cpu_count: int | None,
        max_ports: int | None,
        supported_image_kinds: list[str] | None,
        metadata: dict[str, Any],
    ) -> None:
        key = (device.id, "current")
        supported = sorted(
            {
                str(kind).strip().lower()
                for kind in (supported_image_kinds or [])
                if str(kind).strip()
            }
        )
        supported_json = _json_text(supported)
        metadata_json = _json_text(metadata)
        row = self.revisions_by_key.get(key)
        if row is None:
            row = models.CatalogDeviceRevision(
                id=str(uuid4()),
                device_type_id=device.id,
                version_tag="current",
                runtime_kind=runtime_kind,
                memory_mb=memory_mb,
                cpu_count=cpu_count,
                max_ports=max_ports,
                supported_image_kinds_json=supported_json,
                metadata_json=metadata_json,
                is_current=True,
            )
            self.session.add(row)
            self.revisions_by_key[key] = row
            self.stats.revisions_created += 1
            return

        changed = False
        if row.runtime_kind != runtime_kind:
            row.runtime_kind = runtime_kind
            changed = True
        if row.memory_mb != memory_mb:
            row.memory_mb = memory_mb
            changed = True
        if row.cpu_count != cpu_count:
            row.cpu_count = cpu_count
            changed = True
        if row.max_ports != max_ports:
            row.max_ports = max_ports
            changed = True
        if row.supported_image_kinds_json != supported_json:
            row.supported_image_kinds_json = supported_json
            changed = True
        if row.metadata_json != metadata_json:
            row.metadata_json = metadata_json
            changed = True
        if not row.is_current:
            row.is_current = True
            changed = True
        if row.valid_to is not None:
            row.valid_to = None
            changed = True
        if changed:
            self.stats.revisions_updated += 1

    def ensure_image(self, entry: dict[str, Any]) -> models.CatalogImage | None:
        external_id = str(entry.get("id") or "").strip()
        if not external_id:
            self.stats.warnings.append("Skipped manifest image with empty id")
            return None

        row = self.images_by_external_id.get(external_id)
        metadata = dict(entry)
        metadata_json = _json_text(metadata)
        payload = {
            "kind": str(entry.get("kind") or "").lower(),
            "reference": entry.get("reference"),
            "filename": entry.get("filename"),
            "digest_sha256": entry.get("sha256") or entry.get("digest") or entry.get("content_hash"),
            "size_bytes": _maybe_int(entry.get("size_bytes")),
            "vendor_name": entry.get("vendor"),
            "version": entry.get("version"),
            "source": "manifest",
            "metadata_json": metadata_json,
            "imported_at": _parse_timestamp(entry.get("uploaded_at") or entry.get("imported_at")),
        }

        if row is None:
            row = models.CatalogImage(
                id=str(uuid4()),
                external_id=external_id,
                **payload,
            )
            self.session.add(row)
            self.session.flush()
            self.images_by_external_id[external_id] = row
            self.stats.images_created += 1
            return row

        changed = False
        for key, value in payload.items():
            if getattr(row, key) != value:
                setattr(row, key, value)
                changed = True
        if changed:
            self.stats.images_updated += 1
        return row

    def ensure_discovered_device(self, canonical_device_id: str) -> models.CatalogDeviceType:
        existing = self.devices_by_canonical.get(canonical_device_id)
        if existing is not None:
            return existing

        vendor = self.ensure_vendor("Unknown")
        device = self.ensure_device(
            canonical_device_id=canonical_device_id,
            vendor_id=vendor.id,
            runtime_kind=canonical_device_id,
            display_name=canonical_device_id,
            device_class=None,
            source="manifest_discovered",
            metadata={"origin": "manifest"},
        )
        self.ensure_revision(
            device=device,
            runtime_kind=canonical_device_id,
            memory_mb=None,
            cpu_count=None,
            max_ports=None,
            supported_image_kinds=[],
            metadata={"origin": "manifest"},
        )
        self.stats.discovered_devices += 1
        return device

    def set_image_compatibility(self, image: models.CatalogImage, canonical_device_ids: set[str]) -> None:
        target_device_type_ids: set[str] = set()
        for canonical in sorted(canonical_device_ids):
            device = self.devices_by_canonical.get(canonical) or self.ensure_discovered_device(canonical)
            target_device_type_ids.add(device.id)

        existing_rows = self.compat_by_image_id.get(image.id, {})
        existing_ids = set(existing_rows.keys())

        for device_type_id in sorted(target_device_type_ids - existing_ids):
            row = models.CatalogImageCompatibility(
                id=str(uuid4()),
                image_id=image.id,
                device_type_id=device_type_id,
                source="manifest",
            )
            self.session.add(row)
            existing_rows[device_type_id] = row
            self.stats.compat_created += 1

        for device_type_id in sorted(existing_ids - target_device_type_ids):
            row = existing_rows.pop(device_type_id)
            self.session.delete(row)
            self.stats.compat_deleted += 1

        self.compat_by_image_id[image.id] = existing_rows

    def set_defaults(self, desired_defaults: dict[str, str]) -> None:
        desired_by_device_type_id: dict[str, str] = {}
        for canonical_device_id, image_external_id in desired_defaults.items():
            device = self.devices_by_canonical.get(canonical_device_id) or self.ensure_discovered_device(
                canonical_device_id
            )
            image = self.images_by_external_id.get(image_external_id)
            if image is None:
                self.stats.warnings.append(
                    f"Default mapping skipped for device '{canonical_device_id}': image '{image_external_id}' missing"
                )
                continue
            desired_by_device_type_id[device.id] = image.id

        existing_device_ids = set(self.defaults_by_device_id.keys())
        desired_device_ids = set(desired_by_device_type_id.keys())

        for device_type_id in sorted(desired_device_ids):
            image_id = desired_by_device_type_id[device_type_id]
            existing = self.defaults_by_device_id.get(device_type_id)
            if existing is None:
                row = models.CatalogImageDefault(
                    id=str(uuid4()),
                    device_type_id=device_type_id,
                    image_id=image_id,
                    source="manifest",
                )
                self.session.add(row)
                self.defaults_by_device_id[device_type_id] = row
                self.stats.defaults_created += 1
                continue
            if existing.image_id != image_id or existing.source != "manifest":
                existing.image_id = image_id
                existing.source = "manifest"
                self.stats.defaults_updated += 1

        for device_type_id in sorted(existing_device_ids - desired_device_ids):
            row = self.defaults_by_device_id.pop(device_type_id)
            self.session.delete(row)
            self.stats.defaults_deleted += 1

    def record_event(self) -> None:
        payload = {
            "vendors_created": self.stats.vendors_created,
            "vendors_updated": self.stats.vendors_updated,
            "devices_created": self.stats.devices_created,
            "devices_updated": self.stats.devices_updated,
            "images_created": self.stats.images_created,
            "images_updated": self.stats.images_updated,
            "compat_created": self.stats.compat_created,
            "compat_deleted": self.stats.compat_deleted,
            "defaults_created": self.stats.defaults_created,
            "defaults_updated": self.stats.defaults_updated,
            "defaults_deleted": self.stats.defaults_deleted,
            "alias_conflicts": self.stats.alias_conflicts,
            "warnings": self.stats.warnings,
        }
        self.session.add(
            models.CatalogIngestEvent(
                id=str(uuid4()),
                source="cutover",
                event_type="seed_manifest_vendor_catalog",
                summary="Seeded canonical device/image catalog from manifest and vendor registry",
                payload_json=_json_text(payload),
            )
        )

    def seed_vendor_registry(self) -> None:
        compatibility_aliases = get_image_compatibility_aliases()
        for canonical_id, config in sorted(VENDOR_CONFIGS.items()):
            normalized_id = _normalize_token(canonical_id)
            if not normalized_id:
                continue
            vendor = self.ensure_vendor(getattr(config, "vendor", None) or "Unknown")
            runtime_kind = _normalize_token(getattr(config, "kind", None))
            display_name = getattr(config, "label", None) or normalized_id
            device_class = getattr(getattr(config, "device_type", None), "value", None)
            metadata = {
                "icon": getattr(config, "icon", None),
                "aliases": sorted({_normalize_token(a) for a in (getattr(config, "aliases", None) or []) if _normalize_token(a)}),
                "tags": sorted({_normalize_token(t) for t in (getattr(config, "tags", None) or []) if _normalize_token(t)}),
                "vendor_options": bool(getattr(config, "vendor_options", None)),
            }
            device = self.ensure_device(
                canonical_device_id=normalized_id,
                vendor_id=vendor.id,
                runtime_kind=runtime_kind,
                display_name=str(display_name),
                device_class=_normalize_token(device_class),
                source="builtin",
                metadata=metadata,
            )
            if runtime_kind and runtime_kind != normalized_id:
                self.ensure_alias(runtime_kind, device, alias_type="runtime_kind")
            for alias in getattr(config, "aliases", None) or []:
                self.ensure_alias(alias, device, alias_type="explicit")
            for alias in compatibility_aliases.get(normalized_id, []):
                self.ensure_alias(alias, device, alias_type="compatibility")

            self.ensure_revision(
                device=device,
                runtime_kind=runtime_kind,
                memory_mb=_maybe_int(getattr(config, "memory", None)),
                cpu_count=_maybe_int(getattr(config, "cpu", None)),
                max_ports=_maybe_int(getattr(config, "max_ports", None)),
                supported_image_kinds=list(getattr(config, "supported_image_kinds", None) or []),
                metadata={"source": "builtin_vendor_config"},
            )

    def seed_custom_devices(self) -> None:
        for entry in load_custom_devices() or []:
            raw_id = _normalize_token(entry.get("id"))
            if not raw_id:
                self.stats.warnings.append("Skipped custom device with empty id")
                continue
            existing = self.devices_by_canonical.get(raw_id)
            if _is_builtin_device(existing):
                self.stats.warnings.append(
                    f"Skipped custom device '{raw_id}' because built-in device already exists"
                )
                continue

            vendor = self.ensure_vendor(entry.get("vendor") or "Custom")
            runtime_kind = _normalize_token(entry.get("kind")) or raw_id
            device = self.ensure_device(
                canonical_device_id=raw_id,
                vendor_id=vendor.id,
                runtime_kind=runtime_kind,
                display_name=str(entry.get("name") or raw_id),
                device_class=_normalize_token(entry.get("type")),
                source="custom",
                metadata=entry,
            )
            self.ensure_revision(
                device=device,
                runtime_kind=runtime_kind,
                memory_mb=_maybe_int(entry.get("memory")),
                cpu_count=_maybe_int(entry.get("cpu")),
                max_ports=_maybe_int(entry.get("maxPorts")),
                supported_image_kinds=list(entry.get("supportedImageKinds") or []),
                metadata={"source": "custom_device"},
            )

    def seed_manifest_images(self) -> dict[str, str]:
        desired_defaults: dict[str, str] = {}
        for entry in self.manifest.get("images", []) or []:
            if not isinstance(entry, dict):
                continue
            image = self.ensure_image(entry)
            if image is None:
                continue

            raw_device_ids: list[str] = []
            raw_primary = entry.get("device_id")
            if raw_primary:
                raw_device_ids.append(str(raw_primary))
            raw_device_ids.extend(str(v) for v in (entry.get("compatible_devices") or []) if v)
            canonical_ids = set(canonicalize_device_ids(raw_device_ids))

            default_scopes = normalize_default_device_scope_ids(entry.get("default_for_devices") or [])
            if not default_scopes and entry.get("is_default") and raw_primary:
                default_scopes = [str(raw_primary)]
            for scope in default_scopes:
                canonical_scope = canonicalize_device_id(scope) or _normalize_token(scope)
                if not canonical_scope:
                    continue
                # Invariant: defaults must be compatible with the image.
                canonical_ids.add(canonical_scope)
                desired_defaults[canonical_scope] = image.external_id

            self.set_image_compatibility(image, canonical_ids)
        return desired_defaults

    def run(self) -> BackfillStats:
        self.seed_vendor_registry()
        self.seed_custom_devices()
        desired_defaults = self.seed_manifest_images()
        self.set_defaults(desired_defaults)
        if self.apply:
            self.record_event()
        return self.stats


def _load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Manifest not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Manifest root must be an object: {path}")
    if not isinstance(data.get("images", []), list):
        raise ValueError("Manifest field 'images' must be a list")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help=f"Path to manifest.json (default: {DEFAULT_MANIFEST})",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist backfill changes to Postgres (default: dry-run)",
    )
    args = parser.parse_args()

    manifest = _load_manifest(args.manifest)
    session = SessionLocal()
    try:
        try:
            session.query(models.CatalogVendor).limit(1).all()
        except (OperationalError, ProgrammingError) as exc:
            print("Catalog tables are missing. Run Alembic upgrade first (api/alembic revisions 055 and 056).")
            print(f"Details: {exc}")
            session.rollback()
            return 2

        runner = CatalogBackfill(session=session, manifest=manifest, apply=args.apply)
        stats = runner.run()
        if args.apply:
            session.commit()
        else:
            session.rollback()

        print(f"Manifest: {args.manifest}")
        print(f"Mode: {'apply' if args.apply else 'dry-run'}")
        print(
            "Summary: "
            f"vendors +{stats.vendors_created}/~{stats.vendors_updated}, "
            f"devices +{stats.devices_created}/~{stats.devices_updated}, "
            f"aliases +{stats.aliases_created}/~{stats.aliases_updated} "
            f"(conflicts={stats.alias_conflicts}), "
            f"revisions +{stats.revisions_created}/~{stats.revisions_updated}, "
            f"images +{stats.images_created}/~{stats.images_updated}, "
            f"compat +{stats.compat_created}/-{stats.compat_deleted}, "
            f"defaults +{stats.defaults_created}/~{stats.defaults_updated}/-{stats.defaults_deleted}, "
            f"discovered_devices={stats.discovered_devices}"
        )
        if stats.warnings:
            print("Warnings:")
            for warning in stats.warnings[:50]:
                print(f"- {warning}")
            if len(stats.warnings) > 50:
                print(f"- ... {len(stats.warnings) - 50} more")
        if not args.apply:
            print("Dry-run only. Re-run with --apply to persist changes.")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
