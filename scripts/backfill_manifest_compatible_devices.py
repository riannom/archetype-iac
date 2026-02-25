#!/usr/bin/env python3
"""Backfill compatible_devices for shared qcow2 artifacts in manifest.json.

This script groups qcow2 image entries by shared artifact filename and merges
device compatibility across all entries in each group. It is safe to run
multiple times (idempotent).

Default mode is dry-run. Use --apply to write changes.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


DEFAULT_MANIFEST = Path("/var/lib/archetype/images/manifest.json")
REPO_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPO_ROOT / "api"

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

try:
    from app.image_store import canonicalize_device_ids as _canonicalize_device_ids
except Exception:
    _canonicalize_device_ids = None


def _artifact_key(image: dict[str, Any]) -> str | None:
    if (image.get("kind") or "").lower() != "qcow2":
        return None
    filename = image.get("filename")
    if isinstance(filename, str) and filename.strip():
        return filename.strip().lower()
    reference = image.get("reference")
    if isinstance(reference, str) and reference.strip():
        return Path(reference).name.lower()
    image_id = image.get("id")
    if isinstance(image_id, str) and image_id.startswith("qcow2:"):
        return image_id.split(":", 1)[1].strip().lower()
    return None


def _normalize_devices(values: list[Any]) -> list[str]:
    raw_devices: list[str] = []
    for raw in values:
        if not isinstance(raw, str):
            continue
        dev = raw.strip()
        if not dev:
            continue
        raw_devices.append(dev)

    if _canonicalize_device_ids is not None:
        try:
            canonicalized = _canonicalize_device_ids(raw_devices)
        except Exception:
            canonicalized = raw_devices
    else:
        canonicalized = raw_devices

    out: list[str] = []
    seen: set[str] = set()
    for dev in canonicalized:
        key = str(dev).strip().lower()
        if not key:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    out.sort(key=str.lower)
    return out


def backfill_manifest(manifest: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    images = manifest.get("images", [])
    if not isinstance(images, list):
        return manifest, []

    groups: dict[str, list[dict[str, Any]]] = {}
    for image in images:
        if not isinstance(image, dict):
            continue
        key = _artifact_key(image)
        if not key:
            continue
        groups.setdefault(key, []).append(image)

    updates: list[dict[str, Any]] = []
    for key, group in groups.items():
        if len(group) < 2:
            continue
        merged_devices: list[Any] = []
        for entry in group:
            if entry.get("device_id"):
                merged_devices.append(entry.get("device_id"))
            merged_devices.extend(entry.get("compatible_devices") or [])
        normalized = _normalize_devices(merged_devices)
        for entry in group:
            before = _normalize_devices(list(entry.get("compatible_devices") or []))
            if before != normalized:
                entry["compatible_devices"] = normalized
                updates.append(
                    {
                        "image_id": entry.get("id"),
                        "artifact": key,
                        "before": before,
                        "after": normalized,
                    }
                )

    return manifest, updates


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
        help="Write updated manifest to disk (default is dry-run)",
    )
    args = parser.parse_args()

    if not args.manifest.exists():
        print(f"Manifest not found: {args.manifest}")
        return 1

    data = json.loads(args.manifest.read_text(encoding="utf-8"))
    updated, changes = backfill_manifest(data)

    print(f"Manifest: {args.manifest}")
    print(f"Changes detected: {len(changes)}")
    for change in changes:
        print(
            f"- {change['image_id']} ({change['artifact']}): "
            f"{change['before']} -> {change['after']}"
        )

    if args.apply and changes:
        args.manifest.write_text(json.dumps(updated, indent=2), encoding="utf-8")
        print("Applied changes.")
    elif args.apply:
        print("No changes to apply.")
    else:
        print("Dry-run only. Re-run with --apply to persist changes.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
