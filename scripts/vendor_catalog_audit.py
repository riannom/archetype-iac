#!/usr/bin/env python3
"""Vendor catalog audit: compare agent/vendors.py against CML refplat ISO specs.

Usage:
    # Compare vendor catalog against ISO node definitions
    python scripts/vendor_catalog_audit.py --iso /path/to/refplat.iso

    # Just validate vendor catalog consistency (no ISO needed)
    python scripts/vendor_catalog_audit.py
"""
import argparse
import asyncio
import sys
from pathlib import Path

# Add project root to path so we can import agent/api modules
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))

from agent.vendors import VENDOR_CONFIGS, get_libvirt_config


# Map CML node_definition_id -> vendor config key
CML_TO_VENDOR = {
    "iosv": "cisco_iosv",
    "iosvl2": "cisco_iosvl2",
    "csr1000v": "cisco_csr1000v",
    "cat8000v": "cisco_c8000v",
    "asav": "cisco_asav",
    "nxosv": "cisco_n9kv",
    "nxosv9000": "cisco_n9kv",
    "iosxrv9000": "cisco_iosxr",
    "cat9000v-q200": "cat9800",
    "cat9000v-uadp": "cat9800",
    "cat-sdwan-controller": "cat-sdwan-controller",
    "cat-sdwan-manager": "cat-sdwan-manager",
    "cat-sdwan-validator": "cat-sdwan-validator",
    "cat-sdwan-vedge": "cat-sdwan-vedge",
    "ftdv": "ftdv",
    "fmcv": "fmcv",
}

# Known intentional substitutions (CML driver -> our driver)
KNOWN_SUBSTITUTIONS = {
    ("nic_driver", "vmxnet3"): ["virtio", "e1000"],  # QEMU doesn't support vmxnet3
}


def validate_vendor_catalog():
    """Check vendor catalog for internal consistency issues."""
    issues = []

    for key, config in VENDOR_CONFIGS.items():
        if "qcow2" not in (config.supported_image_kinds or []):
            continue  # Skip non-VM devices

        libvirt_cfg = get_libvirt_config(key)

        # EFI devices should have efi_boot=True
        if config.efi_vars and not config.efi_boot:
            issues.append(
                f"  WARN  {key}: efi_vars='{config.efi_vars}' but efi_boot=False"
            )

        # Data volume devices should mention it in notes
        if config.data_volume_gb > 0 and "volume" not in (config.notes or "").lower():
            issues.append(
                f"  WARN  {key}: data_volume_gb={config.data_volume_gb} but notes don't mention volume"
            )

        # Memory sanity check
        if config.memory < 512:
            issues.append(
                f"  WARN  {key}: memory={config.memory}MB seems too low for a VM"
            )

    return issues


async def compare_with_iso(iso_path: Path):
    """Compare vendor catalog against CML refplat ISO node definitions."""
    from api.app.iso.extractor import ISOExtractor
    from api.app.iso.parser import ParserRegistry

    extractor = ISOExtractor(iso_path)
    file_list = await extractor.get_file_names()
    parser = ParserRegistry.get_parser(iso_path, file_list)

    if not parser:
        print(f"ERROR: Could not detect ISO format for {iso_path}")
        return []

    manifest = await parser.parse(iso_path, extractor)
    mismatches = []

    for node_def in manifest.node_definitions:
        vendor_key = CML_TO_VENDOR.get(node_def.id)
        if not vendor_key:
            mismatches.append(f"  SKIP  {node_def.id}: no vendor mapping defined")
            continue

        config = VENDOR_CONFIGS.get(vendor_key)
        if not config:
            mismatches.append(f"  MISS  {node_def.id}: vendor key '{vendor_key}' not in VENDOR_CONFIGS")
            continue

        # Compare fields
        checks = [
            ("memory", config.memory, node_def.ram_mb, "MB RAM"),
            ("cpu", config.cpu, node_def.cpus, "vCPUs"),
            ("disk_driver", config.disk_driver, node_def.disk_driver, ""),
            ("nic_driver", config.nic_driver, node_def.nic_driver, ""),
        ]

        if hasattr(node_def, "efi_boot"):
            checks.append(("efi_boot", config.efi_boot, node_def.efi_boot, ""))
        if hasattr(node_def, "efi_vars") and node_def.efi_vars:
            checks.append(("efi_vars", config.efi_vars, node_def.efi_vars, ""))

        for field, ours, theirs, unit in checks:
            if ours == theirs:
                continue

            # Check if this is a known intentional substitution
            known = KNOWN_SUBSTITUTIONS.get((field, theirs))
            if known and ours in known:
                continue

            # Memory: we allow over-provisioning (ours >= theirs)
            if field == "memory" and ours >= theirs:
                continue

            severity = "CRIT" if field in ("memory", "disk_driver") else "WARN"
            mismatches.append(
                f"  {severity}  {vendor_key} ({node_def.id}): "
                f"{field} = {ours}{unit} (ours) vs {theirs}{unit} (CML)"
            )

    return mismatches


def main():
    parser = argparse.ArgumentParser(description="Audit vendor catalog against CML specs")
    parser.add_argument("--iso", type=Path, help="Path to CML refplat ISO for comparison")
    args = parser.parse_args()

    print("=== Vendor Catalog Audit ===\n")

    # Phase 1: Internal consistency
    print("--- Internal Consistency ---")
    issues = validate_vendor_catalog()
    if issues:
        for issue in issues:
            print(issue)
    else:
        print("  OK    No internal consistency issues found")

    # Phase 2: ISO comparison
    if args.iso:
        if not args.iso.exists():
            print(f"\nERROR: ISO not found: {args.iso}")
            sys.exit(1)

        print(f"\n--- CML ISO Comparison ({args.iso.name}) ---")
        mismatches = asyncio.run(compare_with_iso(args.iso))
        if mismatches:
            for m in mismatches:
                print(m)
        else:
            print("  OK    All vendor configs match CML specs")

        crit_count = sum(1 for m in mismatches if "CRIT" in m)
        if crit_count:
            print(f"\n{crit_count} critical mismatch(es) found")
            sys.exit(1)
    else:
        print("\n(Skipping ISO comparison - pass --iso to enable)")

    print("\nDone.")


if __name__ == "__main__":
    main()
