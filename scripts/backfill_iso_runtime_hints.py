#!/usr/bin/env python3
"""Backfill manifest runtime hints from ISO node definitions.

Updates existing manifest image entries (for example older imports) with
runtime hints parsed from ISO metadata for qcow2/docker/iol images:
  - memory_mb, cpu_count
  - disk_driver, nic_driver, machine_type
  - libvirt_driver
  - readiness_probe, readiness_pattern
  - boot_timeout
  - efi_boot, efi_vars
  - cpu_limit, has_loopback
  - provisioning_driver, provisioning_media_type

Usage:
  python3 scripts/backfill_iso_runtime_hints.py --iso /path/to/refplat.iso --apply
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
API_ROOT = ROOT / "api"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.image_store import load_manifest, save_manifest  # noqa: E402
from app.iso.extractor import ISOExtractor  # noqa: E402
from app.iso.models import ParsedNodeDefinition  # noqa: E402
from app.iso.parser import ParserRegistry  # noqa: E402
from app.iso import virl2_parser as _virl2_parser  # noqa: F401,E402


def _hints_from_node_def(node_def: ParsedNodeDefinition) -> dict[str, Any]:
    return {
        "memory_mb": node_def.ram_mb,
        "cpu_count": node_def.cpus,
        "disk_driver": node_def.disk_driver,
        "nic_driver": node_def.nic_driver,
        "machine_type": node_def.machine_type,
        "libvirt_driver": node_def.libvirt_driver,
        "boot_timeout": node_def.boot_timeout,
        "readiness_probe": "log_pattern" if node_def.boot_completed_patterns else None,
        "readiness_pattern": "|".join(node_def.boot_completed_patterns) if node_def.boot_completed_patterns else None,
        "efi_boot": node_def.efi_boot,
        "efi_vars": node_def.efi_vars,
        "max_ports": len(node_def.interfaces) or node_def.interface_count_default,
        "port_naming": node_def.interface_naming_pattern,
        "cpu_limit": node_def.cpu_limit,
        "has_loopback": node_def.has_loopback,
        "provisioning_driver": node_def.provisioning_driver,
        "provisioning_media_type": node_def.provisioning_media_type,
    }


def _find_manifest_entries(
    manifest: dict[str, Any],
    image_filename: str,
    image_type: str,
    parsed_image_id: str | None = None,
) -> list[dict[str, Any]]:
    image_id_prefix = {
        "qcow2": "qcow2",
        "iol": "iol",
        "docker": "docker",
    }.get(image_type)
    target_id = f"{image_id_prefix}:{parsed_image_id}" if image_id_prefix and parsed_image_id else None
    results: list[dict[str, Any]] = []
    seen: set[str] = set()

    for entry in manifest.get("images", []):
        entry_id = entry.get("id")
        if target_id and entry_id == target_id and entry_id not in seen:
            results.append(entry)
            seen.add(entry_id)
    for entry in manifest.get("images", []):
        entry_id = entry.get("id")
        if entry.get("filename") == image_filename and entry_id not in seen:
            results.append(entry)
            seen.add(entry_id)
    for entry in manifest.get("images", []):
        entry_id = entry.get("id")
        if Path(entry.get("reference", "")).name == image_filename and entry_id not in seen:
            results.append(entry)
            seen.add(entry_id)
    return results


async def _parse_iso(iso_path: Path):
    extractor = ISOExtractor(iso_path)
    file_list = await extractor.get_file_names()
    parser = ParserRegistry.get_parser(iso_path, file_list)
    if not parser:
        raise RuntimeError(f"No parser available for ISO: {iso_path}")
    return await parser.parse(iso_path, extractor)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iso", required=True, type=Path, help="Path to ISO file")
    parser.add_argument("--apply", action="store_true", help="Write updates to manifest.json")
    args = parser.parse_args()

    if not args.iso.exists():
        print(f"ISO not found: {args.iso}")
        return 1

    iso_manifest = await _parse_iso(args.iso)
    manifest = load_manifest()
    node_defs = {n.id: n for n in iso_manifest.node_definitions}

    changed = 0
    scanned = 0
    unmatched = 0
    for image in iso_manifest.images:
        if image.image_type not in {"qcow2", "docker", "iol"}:
            continue
        scanned += 1
        node_def = node_defs.get(image.node_definition_id)
        if not node_def:
            continue

        entries = _find_manifest_entries(
            manifest,
            image.disk_image_filename,
            image.image_type,
            image.id,
        )
        if not entries:
            unmatched += 1
            continue

        hints = _hints_from_node_def(node_def)
        for entry in entries:
            updates: dict[str, Any] = {}
            for key, value in hints.items():
                if entry.get(key) != value:
                    updates[key] = value
            if updates:
                entry.update(updates)
                changed += 1
                print(f"update {entry.get('id')}: {updates}")

    print(
        f"scanned_images={scanned} updated_entries={changed} "
        f"unmatched_images={unmatched} apply={args.apply}"
    )

    if args.apply and changed > 0:
        save_manifest(manifest)
        print("manifest.json updated")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
