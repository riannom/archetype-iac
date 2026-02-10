from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.network.cleanup import get_cleanup_manager
from agent.network.backends.registry import get_network_backend


async def _collect_ovs_audit() -> dict[str, Any]:
    result: dict[str, Any] = {
        "bridge_initialized": False,
        "orphaned_ports": [],
        "vxlan_orphan_ports": [],
        "errors": [],
    }

    backend = get_network_backend()
    ovs_mgr = getattr(backend, "ovs_manager", None)
    overlay_mgr = getattr(backend, "overlay_manager", None)

    if not ovs_mgr or not getattr(ovs_mgr, "_initialized", False):
        return result

    result["bridge_initialized"] = True

    try:
        bridge_state = await ovs_mgr.get_ovs_bridge_state()
        result["orphaned_ports"] = [
            p.get("port_name") for p in bridge_state.get("orphaned_ports", [])
        ]
    except Exception as e:
        result["errors"].append(f"bridge_state: {e}")

    try:
        if overlay_mgr:
            tracked_vxlan = set()
            for t in overlay_mgr._tunnels.values():
                tracked_vxlan.add(t.interface_name)
            for vtep in overlay_mgr._vteps.values():
                tracked_vxlan.add(vtep.interface_name)
            for lt in overlay_mgr._link_tunnels.values():
                tracked_vxlan.add(lt.interface_name)

            all_ports = await ovs_mgr.get_all_ovs_ports()
            result["vxlan_orphan_ports"] = [
                p.get("port_name")
                for p in all_ports
                if p.get("type") == "vxlan" and p.get("port_name") not in tracked_vxlan
            ]
    except Exception as e:
        result["errors"].append(f"vxlan_ports: {e}")

    return result


async def _run_audit(include_ovs: bool) -> dict[str, Any]:
    cleanup_mgr = get_cleanup_manager()
    stats = await cleanup_mgr.run_full_cleanup(dry_run=True, include_ovs=False)
    output: dict[str, Any] = {"network": stats.to_dict()}

    if include_ovs:
        output["ovs"] = await _collect_ovs_audit()

    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Dry-run cleanup audit (no deletions).")
    parser.add_argument(
        "--include-ovs",
        action="store_true",
        help="Include OVS orphan/overlay VXLAN port audit (read-only).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output JSON only.",
    )
    args = parser.parse_args()

    output = asyncio.run(_run_audit(include_ovs=args.include_ovs))

    if args.json:
        print(json.dumps(output, indent=2, sort_keys=True))
        return

    print("Cleanup audit (dry-run)")
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
