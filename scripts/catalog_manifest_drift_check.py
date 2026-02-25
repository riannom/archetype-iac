#!/usr/bin/env python3
"""Check manifest.json and DB catalog projection drift.

Compares image IDs plus compatibility/default scope metadata between:
1) /var/lib/archetype/images/manifest.json (or --manifest path)
2) catalog tables projected via app.services.catalog_service
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from sqlalchemy.exc import OperationalError, ProgrammingError


DEFAULT_MANIFEST = Path("/var/lib/archetype/images/manifest.json")
REPO_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPO_ROOT / "api"

for _path in (REPO_ROOT, API_ROOT):
    _path_str = str(_path)
    if _path_str not in sys.path:
        sys.path.insert(0, _path_str)

from app import models  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.image_store import canonicalize_device_ids, normalize_default_device_scope_ids  # noqa: E402
from app.services.catalog_service import catalog_is_seeded, list_catalog_library_images  # noqa: E402


def _normalize_manifest_images(images: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}
    for raw in images:
        image_id = str(raw.get("id") or "").strip()
        if not image_id:
            continue
        compatible = canonicalize_device_ids(list(raw.get("compatible_devices") or []))
        default_scopes = normalize_default_device_scope_ids(raw.get("default_for_devices") or [])
        normalized[image_id] = {
            "id": image_id,
            "compatible_devices": sorted(set(compatible)),
            "default_for_devices": sorted(set(default_scopes)),
        }
    return normalized


def _normalize_catalog_images(images: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}
    for raw in images:
        image_id = str(raw.get("id") or "").strip()
        if not image_id:
            continue
        compatible = canonicalize_device_ids(list(raw.get("compatible_devices") or []))
        default_scopes = normalize_default_device_scope_ids(raw.get("default_for_devices") or [])
        normalized[image_id] = {
            "id": image_id,
            "compatible_devices": sorted(set(compatible)),
            "default_for_devices": sorted(set(default_scopes)),
        }
    return normalized


def _load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"images": []}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Manifest root must be an object: {path}")
    images = payload.get("images", [])
    if not isinstance(images, list):
        raise ValueError(f"Manifest 'images' must be a list: {path}")
    return {"images": images}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help=f"Path to manifest.json (default: {DEFAULT_MANIFEST})",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="Optional path to write full JSON drift report",
    )
    parser.add_argument(
        "--fail-on-drift",
        action="store_true",
        help="Return exit code 1 when drift is detected",
    )
    parser.add_argument(
        "--max-details",
        type=int,
        default=25,
        help="Maximum per-field drift details to print",
    )
    args = parser.parse_args()

    manifest = _load_manifest(args.manifest)
    manifest_images = _normalize_manifest_images(manifest.get("images") or [])

    session = SessionLocal()
    try:
        try:
            session.query(models.CatalogVendor).limit(1).all()
        except (OperationalError, ProgrammingError) as exc:
            print("Catalog tables are missing. Run Alembic upgrade first.")
            print(f"Details: {exc}")
            return 2

        if not catalog_is_seeded(session):
            print("Catalog is not seeded; no manifest/catalog drift to report yet.")
            return 0

        catalog_projection = list_catalog_library_images(session, force_refresh=True)
        catalog_images = _normalize_catalog_images(catalog_projection)
    finally:
        session.close()

    manifest_ids = set(manifest_images.keys())
    catalog_ids = set(catalog_images.keys())

    only_in_manifest = sorted(manifest_ids - catalog_ids)
    only_in_catalog = sorted(catalog_ids - manifest_ids)

    compatibility_drift: list[dict[str, Any]] = []
    default_scope_drift: list[dict[str, Any]] = []
    invariant_violations: list[dict[str, Any]] = []

    for image_id in sorted(manifest_ids.intersection(catalog_ids)):
        manifest_entry = manifest_images[image_id]
        catalog_entry = catalog_images[image_id]

        manifest_compat = manifest_entry["compatible_devices"]
        catalog_compat = catalog_entry["compatible_devices"]
        if manifest_compat != catalog_compat:
            compatibility_drift.append(
                {
                    "image_id": image_id,
                    "manifest": manifest_compat,
                    "catalog": catalog_compat,
                }
            )

        manifest_defaults = manifest_entry["default_for_devices"]
        catalog_defaults = catalog_entry["default_for_devices"]
        if manifest_defaults != catalog_defaults:
            default_scope_drift.append(
                {
                    "image_id": image_id,
                    "manifest": manifest_defaults,
                    "catalog": catalog_defaults,
                }
            )

        catalog_compat_set = set(catalog_compat)
        extra_defaults = sorted(set(catalog_defaults) - catalog_compat_set)
        if extra_defaults:
            invariant_violations.append(
                {
                    "image_id": image_id,
                    "defaults_not_in_compatible": extra_defaults,
                }
            )

    report = {
        "manifest_path": str(args.manifest),
        "counts": {
            "manifest_images": len(manifest_ids),
            "catalog_images": len(catalog_ids),
            "only_in_manifest": len(only_in_manifest),
            "only_in_catalog": len(only_in_catalog),
            "compatibility_drift": len(compatibility_drift),
            "default_scope_drift": len(default_scope_drift),
            "invariant_violations": len(invariant_violations),
        },
        "only_in_manifest": only_in_manifest,
        "only_in_catalog": only_in_catalog,
        "compatibility_drift": compatibility_drift,
        "default_scope_drift": default_scope_drift,
        "invariant_violations": invariant_violations,
    }

    print(f"Manifest: {args.manifest}")
    print(
        "Summary: "
        f"manifest={len(manifest_ids)}, catalog={len(catalog_ids)}, "
        f"only_manifest={len(only_in_manifest)}, only_catalog={len(only_in_catalog)}, "
        f"compat_drift={len(compatibility_drift)}, default_drift={len(default_scope_drift)}, "
        f"invariant_violations={len(invariant_violations)}"
    )
    if only_in_manifest:
        print(f"- only in manifest ({len(only_in_manifest)}): {only_in_manifest[:args.max_details]}")
    if only_in_catalog:
        print(f"- only in catalog ({len(only_in_catalog)}): {only_in_catalog[:args.max_details]}")
    if compatibility_drift:
        print("- compatibility drift examples:")
        for item in compatibility_drift[: args.max_details]:
            print(f"  - {item['image_id']}: manifest={item['manifest']} catalog={item['catalog']}")
    if default_scope_drift:
        print("- default scope drift examples:")
        for item in default_scope_drift[: args.max_details]:
            print(f"  - {item['image_id']}: manifest={item['manifest']} catalog={item['catalog']}")
    if invariant_violations:
        print("- invariant violations (default_for_devices not in compatible_devices):")
        for item in invariant_violations[: args.max_details]:
            print(f"  - {item['image_id']}: {item['defaults_not_in_compatible']}")

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"JSON report written: {args.json_out}")

    has_drift = any(
        [
            only_in_manifest,
            only_in_catalog,
            compatibility_drift,
            default_scope_drift,
            invariant_violations,
        ]
    )
    if args.fail_on_drift and has_drift:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
