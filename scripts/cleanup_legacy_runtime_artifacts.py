from __future__ import annotations

import argparse
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.config import settings
from agent.network.docker_plugin import _parse_ovs_map

ARCHETYPE_NS = {"archetype": "http://archetype.io/libvirt/1"}
REQUIRED_OVS_EXTERNAL_IDS = (
    "archetype.lab_id",
    "archetype.endpoint_id",
    "archetype.network_id",
    "archetype.interface_name",
)


def _run(args: list[str]) -> tuple[int, str, str]:
    result = subprocess.run(args, capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr


def _parse_libvirt_identity(xml_text: str) -> dict[str, str]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return {}

    identity: dict[str, str] = {}
    for field in ("lab_id", "node_name", "node_definition_id", "provider"):
        value = root.findtext(f".//archetype:{field}", namespaces=ARCHETYPE_NS)
        if value:
            identity[field] = value
    return identity


def _classify_ovs_ports(port_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    legacy_ports: list[dict[str, Any]] = []
    for record in port_records:
        if not record["port_name"].startswith("vh"):
            continue
        ext_ids = record.get("external_ids", {})
        missing = [key for key in REQUIRED_OVS_EXTERNAL_IDS if not ext_ids.get(key)]
        if missing:
            legacy_ports.append(
                {
                    "port_name": record["port_name"],
                    "missing_keys": missing,
                    "external_ids": ext_ids,
                }
            )
    return legacy_ports


def _classify_libvirt_domains(domain_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    legacy_domains: list[dict[str, Any]] = []
    for record in domain_records:
        metadata = record.get("metadata", {})
        missing = [key for key in ("lab_id", "node_name") if not metadata.get(key)]
        if missing:
            legacy_domains.append(
                {
                    "domain_name": record["domain_name"],
                    "missing_keys": missing,
                    "metadata": metadata,
                    "active": record.get("active", False),
                }
            )
    return legacy_domains


def collect_legacy_ovs_ports(bridge_name: str) -> list[dict[str, Any]]:
    code, stdout, stderr = _run(["ovs-vsctl", "list-ports", bridge_name])
    if code != 0:
        raise RuntimeError(stderr.strip() or f"failed to list ports on {bridge_name}")

    port_records: list[dict[str, Any]] = []
    for port_name in [line.strip() for line in stdout.splitlines() if line.strip()]:
        code, ext_ids_raw, _ = _run(["ovs-vsctl", "get", "interface", port_name, "external_ids"])
        ext_ids = _parse_ovs_map(ext_ids_raw) if code == 0 else {}
        port_records.append(
            {
                "port_name": port_name,
                "external_ids": ext_ids,
            }
        )
    return _classify_ovs_ports(port_records)


def collect_legacy_libvirt_domains(uri: str) -> list[dict[str, Any]]:
    try:
        import libvirt  # type: ignore
    except ImportError as exc:
        raise RuntimeError("libvirt-python is required to inspect legacy libvirt domains") from exc

    conn = libvirt.open(uri)
    if conn is None:
        raise RuntimeError(f"failed to connect to libvirt uri {uri}")
    try:
        domain_records: list[dict[str, Any]] = []
        for domain in conn.listAllDomains(0):
            domain_records.append(
                {
                    "domain_name": domain.name(),
                    "metadata": _parse_libvirt_identity(domain.XMLDesc(0)),
                    "active": bool(domain.isActive()),
                }
            )
        return _classify_libvirt_domains(domain_records)
    finally:
        conn.close()


def apply_ovs_cleanup(bridge_name: str, ports: list[dict[str, Any]]) -> list[str]:
    removed: list[str] = []
    for port in ports:
        port_name = port["port_name"]
        code, _, stderr = _run(["ovs-vsctl", "--if-exists", "del-port", bridge_name, port_name])
        if code != 0:
            raise RuntimeError(stderr.strip() or f"failed to remove ovs port {port_name}")
        removed.append(port_name)
    return removed


def apply_libvirt_cleanup(uri: str, domains: list[dict[str, Any]]) -> list[str]:
    try:
        import libvirt  # type: ignore
    except ImportError as exc:
        raise RuntimeError("libvirt-python is required to clean legacy libvirt domains") from exc

    conn = libvirt.open(uri)
    if conn is None:
        raise RuntimeError(f"failed to connect to libvirt uri {uri}")
    removed: list[str] = []
    try:
        for item in domains:
            domain = conn.lookupByName(item["domain_name"])
            if domain.isActive():
                domain.destroy()
            domain.undefine()
            removed.append(item["domain_name"])
    finally:
        conn.close()
    return removed


def build_report(
    *,
    bridge_name: str,
    libvirt_uri: str,
    include_ovs: bool,
    include_libvirt: bool,
    apply: bool,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "apply": apply,
        "ovs_bridge": bridge_name,
        "libvirt_uri": libvirt_uri,
        "ovs": {"enabled": include_ovs, "legacy_ports": [], "removed_ports": []},
        "libvirt": {"enabled": include_libvirt, "legacy_domains": [], "removed_domains": []},
    }

    if include_ovs:
        ovs_ports = collect_legacy_ovs_ports(bridge_name)
        report["ovs"]["legacy_ports"] = ovs_ports
        if apply and ovs_ports:
            report["ovs"]["removed_ports"] = apply_ovs_cleanup(bridge_name, ovs_ports)

    if include_libvirt:
        domains = collect_legacy_libvirt_domains(libvirt_uri)
        report["libvirt"]["legacy_domains"] = domains
        if apply and domains:
            report["libvirt"]["removed_domains"] = apply_libvirt_cleanup(libvirt_uri, domains)

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit or remove legacy runtime artifacts that lack deterministic Archetype metadata."
    )
    parser.add_argument(
        "--bridge",
        default=settings.ovs_bridge_name,
        help="OVS bridge to inspect for legacy vh* ports (default: configured shared bridge).",
    )
    parser.add_argument(
        "--libvirt-uri",
        default=settings.libvirt_uri,
        help="Libvirt URI to inspect for metadata-missing domains.",
    )
    parser.add_argument(
        "--skip-ovs",
        action="store_true",
        help="Skip OVS legacy-port inspection.",
    )
    parser.add_argument(
        "--skip-libvirt",
        action="store_true",
        help="Skip libvirt legacy-domain inspection.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Remove the detected legacy resources instead of reporting only.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print JSON only.",
    )
    args = parser.parse_args(argv)

    report = build_report(
        bridge_name=args.bridge,
        libvirt_uri=args.libvirt_uri,
        include_ovs=not args.skip_ovs,
        include_libvirt=not args.skip_libvirt,
        apply=args.apply,
    )

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    mode = "apply" if args.apply else "dry-run"
    print(f"Legacy runtime artifact cleanup ({mode})")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
