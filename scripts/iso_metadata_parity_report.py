#!/usr/bin/env python3
"""Report ISO metadata parity: parsed -> stored -> runtime-applied.

This script parses an ISO node-definition manifest and compares metadata across:
1) Parsed ISO node definition fields
2) Stored catalog/image metadata (custom device + manifest image entry)
3) Runtime-applied fields used during deploy/create flows

Usage:
  python3 scripts/iso_metadata_parity_report.py --iso /path/to/refplat.iso
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
API_ROOT = ROOT / "api"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.image_store import find_image_by_id, load_manifest  # noqa: E402
from app.iso.extractor import ISOExtractor  # noqa: E402
from app.iso.mapper import map_node_definition_to_device  # noqa: E402
from app.iso.models import ParsedImage, ParsedNodeDefinition  # noqa: E402
from app.iso.parser import ParserRegistry  # noqa: E402
from app.iso import virl2_parser as _virl2_parser  # noqa: F401,E402  # ensure parser registration
from app.services.device_service import get_device_service  # noqa: E402
from app.image_store import find_custom_device  # noqa: E402


PARITY_FIELDS: list[tuple[str, str]] = [
    ("memory", "ram_mb"),
    ("cpu", "cpus"),
    ("disk_driver", "disk_driver"),
    ("nic_driver", "nic_driver"),
    ("machine_type", "machine_type"),
    ("readiness_timeout", "boot_timeout"),
    ("readiness_pattern", "boot_completed_patterns"),
    ("max_ports", "interface_count_default"),
    ("port_naming", "interface_naming_pattern"),
    ("libvirt_driver", "libvirt_driver"),
    ("efi_boot", "efi_boot"),
    ("efi_vars", "efi_vars"),
    ("cpu_limit", "cpu_limit"),
    ("has_loopback", "has_loopback"),
    ("provisioning_driver", "provisioning_driver"),
    ("provisioning_media_type", "provisioning_media_type"),
]

RUNTIME_APPLIED_FIELDS = {
    "memory",
    "cpu",
    "disk_driver",
    "nic_driver",
    "machine_type",
    "readiness_timeout",
    "readiness_pattern",
    "max_ports",
    "port_naming",
    "libvirt_driver",
    "efi_boot",
    "efi_vars",
    "cpu_limit",
}


def _normalize(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        v = value.strip()
        return v if v != "" else None
    if isinstance(value, list):
        return [x for x in value if x is not None]
    return value


def _first_present(*values: Any) -> Any:
    for value in values:
        norm = _normalize(value)
        if norm is not None:
            return norm
    return None


def _extract_raw_node_def_hints(node_def: ParsedNodeDefinition) -> dict[str, Any]:
    return {
        "machine_type": node_def.machine_type,
        "efi_boot": node_def.efi_boot,
        "efi_vars": node_def.efi_vars,
    }


def _build_parsed_fields(node_def: ParsedNodeDefinition) -> dict[str, Any]:
    raw = _extract_raw_node_def_hints(node_def)
    pattern = None
    if node_def.boot_completed_patterns:
        pattern = "|".join(node_def.boot_completed_patterns)
    return {
        "memory": node_def.ram_mb,
        "cpu": node_def.cpus,
        "disk_driver": node_def.disk_driver,
        "nic_driver": node_def.nic_driver,
        "machine_type": raw.get("machine_type"),
        "readiness_timeout": node_def.boot_timeout,
        "readiness_pattern": pattern,
        "max_ports": len(node_def.interfaces) or node_def.interface_count_default,
        "port_naming": node_def.interface_naming_pattern,
        "libvirt_driver": node_def.libvirt_driver,
        "efi_boot": raw.get("efi_boot"),
        "efi_vars": raw.get("efi_vars"),
        "cpu_limit": node_def.cpu_limit,
        "has_loopback": node_def.has_loopback,
        "provisioning_driver": node_def.provisioning_driver,
        "provisioning_media_type": node_def.provisioning_media_type,
    }


def _build_stored_fields(
    device_id: str,
    image_entry: dict[str, Any] | None,
    device_cfg: dict[str, Any] | None,
) -> dict[str, Any]:
    effective = (device_cfg or {}).get("effective", {}) if device_cfg else {}
    return {
        "memory": _first_present(image_entry.get("memory_mb") if image_entry else None, effective.get("memory")),
        "cpu": _first_present(image_entry.get("cpu_count") if image_entry else None, effective.get("cpu")),
        "disk_driver": _first_present(image_entry.get("disk_driver") if image_entry else None, effective.get("diskDriver")),
        "nic_driver": _first_present(image_entry.get("nic_driver") if image_entry else None, effective.get("nicDriver")),
        "machine_type": _first_present(image_entry.get("machine_type") if image_entry else None, effective.get("machineType")),
        "efi_boot": _first_present(image_entry.get("efi_boot") if image_entry else None, effective.get("efiBoot")),
        "efi_vars": _first_present(image_entry.get("efi_vars") if image_entry else None, effective.get("efiVars")),
        "readiness_timeout": _first_present(image_entry.get("boot_timeout") if image_entry else None, effective.get("readinessTimeout")),
        "readiness_pattern": effective.get("readinessPattern"),
        "max_ports": _first_present(image_entry.get("max_ports") if image_entry else None, effective.get("maxPorts")),
        "port_naming": _first_present(image_entry.get("port_naming") if image_entry else None, effective.get("portNaming")),
        "libvirt_driver": _first_present(image_entry.get("libvirt_driver") if image_entry else None, effective.get("libvirtDriver")),
        "cpu_limit": _first_present(image_entry.get("cpu_limit") if image_entry else None, effective.get("cpuLimit")),
        "has_loopback": _first_present(image_entry.get("has_loopback") if image_entry else None, effective.get("hasLoopback")),
        "provisioning_driver": _first_present(image_entry.get("provisioning_driver") if image_entry else None, effective.get("provisioningDriver")),
        "provisioning_media_type": _first_present(image_entry.get("provisioning_media_type") if image_entry else None, effective.get("provisioningMediaType")),
        "device_id": device_id,
    }


def _build_runtime_fields(
    device_id: str,
    image_reference: str | None,
    device_cfg: dict[str, Any] | None,
) -> dict[str, Any]:
    svc = get_device_service()
    specs = svc.resolve_hardware_specs(device_id, None, image_reference)
    effective = (device_cfg or {}).get("effective", {}) if device_cfg else {}
    readiness_timeout = specs.get("readiness_timeout")
    if readiness_timeout is None:
        readiness_timeout = effective.get("readinessTimeout")
    return {
        "memory": specs.get("memory"),
        "cpu": specs.get("cpu"),
        "disk_driver": specs.get("disk_driver"),
        "nic_driver": specs.get("nic_driver"),
        "machine_type": specs.get("machine_type"),
        "libvirt_driver": specs.get("libvirt_driver"),
        "efi_boot": specs.get("efi_boot"),
        "efi_vars": specs.get("efi_vars"),
        "readiness_timeout": readiness_timeout,
        "readiness_pattern": specs.get("readiness_pattern"),
        "max_ports": _first_present(specs.get("max_ports"), effective.get("maxPorts")),
        "port_naming": _first_present(specs.get("port_naming"), effective.get("portNaming")),
        "cpu_limit": (specs.get("cpu_limit") if specs.get("libvirt_driver") == "docker" else None),
        "has_loopback": None,
        "provisioning_driver": None,
        "provisioning_media_type": None,
    }


def _status_for(field: str, parsed: Any, stored: Any, runtime: Any) -> str:
    if _normalize(parsed) is None:
        return "n/a"
    if field in RUNTIME_APPLIED_FIELDS:
        if _normalize(runtime) is not None:
            return "applied"
        if _normalize(stored) is not None:
            return "stored_only"
        return "parsed_only"
    if _normalize(stored) is not None:
        return "stored_not_applied"
    return "parsed_not_stored"


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, list):
        if not value:
            return "-"
        return json.dumps(value)
    return str(value)


def _match_manifest_image(
    parsed_images: list[ParsedImage],
    manifest: dict[str, Any],
    device_id: str,
) -> dict[str, Any] | None:
    for parsed in parsed_images:
        if parsed.image_type == "qcow2":
            image_id = f"qcow2:{parsed.disk_image_filename}"
            entry = find_image_by_id(manifest, image_id)
            if entry and entry.get("device_id") == device_id:
                return entry
        if parsed.image_type == "iol":
            image_id = f"iol:{parsed.id}"
            entry = find_image_by_id(manifest, image_id)
            if entry and entry.get("device_id") == device_id:
                return entry

        # Docker image IDs are runtime-derived from `docker load` output.
        # Use stable manifest filename matching for deterministic lookup.
        for entry in manifest.get("images", []):
            if entry.get("filename") != parsed.disk_image_filename:
                continue
            if entry.get("device_id") == device_id:
                return entry
            compat = entry.get("compatible_devices") or []
            if device_id in compat:
                return entry

    for parsed in parsed_images:
        if parsed.image_type == "qcow2":
            image_id = f"qcow2:{parsed.disk_image_filename}"
            entry = find_image_by_id(manifest, image_id)
            if entry:
                return entry
        if parsed.image_type == "iol":
            image_id = f"iol:{parsed.id}"
            entry = find_image_by_id(manifest, image_id)
            if entry:
                return entry
        for entry in manifest.get("images", []):
            if entry.get("filename") == parsed.disk_image_filename:
                return entry
    return None


async def _parse_iso(iso_path: Path):
    extractor = ISOExtractor(iso_path)
    file_list = await extractor.get_file_names()
    parser = ParserRegistry.get_parser(iso_path, file_list)
    if not parser:
        raise RuntimeError(f"No parser available for ISO: {iso_path}")
    return await parser.parse(iso_path, extractor)


def _resolve_device_config(device_id: str) -> dict[str, Any] | None:
    svc = get_device_service()
    try:
        return svc.get_device_config(device_id)
    except Exception:
        custom = find_custom_device(device_id)
        if custom:
            return {"base": custom, "effective": custom, "overrides": {}}
    return None


def _render_text_report(rows: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    lines.append("ISO Metadata Parity Report")
    lines.append("==========================")
    lines.append("")
    for row in rows:
        lines.append(f"NodeDef: {row['node_definition_id']}  Device: {row['device_id']}")
        lines.append(f"Image: {row.get('image_id') or '-'}")
        lines.append("field | parsed | stored | runtime | status")
        lines.append("----- | ------ | ------ | ------- | ------")
        for field in row["fields"]:
            lines.append(
                f"{field['name']} | {_fmt(field['parsed'])} | {_fmt(field['stored'])} | "
                f"{_fmt(field['runtime'])} | {field['status']}"
            )
        lines.append("")
        gaps = [f["name"] for f in row["fields"] if f["status"] in ("parsed_not_stored", "stored_not_applied", "parsed_only")]
        if gaps:
            lines.append(f"Gaps: {', '.join(gaps)}")
        else:
            lines.append("Gaps: none")
        lines.append("")
    return "\n".join(lines)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iso", required=True, type=Path, help="Path to ISO file")
    parser.add_argument("--json-out", type=Path, default=None, help="Optional JSON output file")
    args = parser.parse_args()

    if not args.iso.exists():
        print(f"ISO not found: {args.iso}")
        return 1

    manifest = load_manifest()
    iso_manifest = await _parse_iso(args.iso)

    images_by_node: dict[str, list[ParsedImage]] = {}
    for image in iso_manifest.images:
        images_by_node.setdefault(image.node_definition_id, []).append(image)

    report_rows: list[dict[str, Any]] = []
    for node_def in iso_manifest.node_definitions:
        device_id = map_node_definition_to_device(node_def) or node_def.id
        node_images = images_by_node.get(node_def.id, [])
        image_entry = _match_manifest_image(node_images, manifest, device_id)
        image_reference = image_entry.get("reference") if image_entry else None

        device_cfg = _resolve_device_config(device_id)
        parsed = _build_parsed_fields(node_def)
        stored = _build_stored_fields(device_id, image_entry, device_cfg)
        runtime = _build_runtime_fields(device_id, image_reference, device_cfg)

        fields: list[dict[str, Any]] = []
        for name, _source in PARITY_FIELDS:
            p = parsed.get(name)
            s = stored.get(name)
            r = runtime.get(name)
            fields.append(
                {
                    "name": name,
                    "parsed": p,
                    "stored": s,
                    "runtime": r,
                    "status": _status_for(name, p, s, r),
                }
            )

        report_rows.append(
            {
                "node_definition_id": node_def.id,
                "device_id": device_id,
                "image_id": image_entry.get("id") if image_entry else None,
                "fields": fields,
            }
        )

    text = _render_text_report(report_rows)
    print(text)

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(
                {
                    "iso": str(args.iso),
                    "node_count": len(report_rows),
                    "rows": report_rows,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\nWrote JSON report: {args.json_out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
