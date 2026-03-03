"""Overlay state management functions.

Extracted from overlay.py to reduce file size. These functions handle
convergence/state declaration, cache persistence, OVS port batch reading,
and link tunnel recovery.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, TYPE_CHECKING

from agent.config import settings
from agent.network.cmd import (
    ovs_vsctl as _shared_ovs_vsctl,
)

if TYPE_CHECKING:
    from agent.network.overlay import OverlayManager

logger = logging.getLogger(__name__)


async def batch_read_ovs_ports(bridge_name: str) -> dict[str, dict[str, Any]] | None:
    """Read all VXLAN port state from OVS using batch JSON queries.

    Scopes to *bridge_name* first (``list-ports``), then reads Port and
    Interface details in two batch calls — 3 total subprocesses regardless
    of port count.

    Args:
        bridge_name: OVS bridge name to query

    Returns:
        Dict mapping port_name -> {name, tag, type, ofport}, or
        ``None`` if OVS could not be queried (callers must not treat
        this as "no ports exist").
    """
    result: dict[str, dict[str, Any]] = {}

    # Step 0: scope to ports on this bridge only
    code, stdout, _ = await _shared_ovs_vsctl("list-ports", bridge_name)
    if code != 0:
        logger.warning(f"OVS list-ports failed for {bridge_name} (rc={code}), skipping read")
        return None
    bridge_ports = {
        p.strip() for p in stdout.strip().split("\n")
        if p.strip().startswith("vxlan")
    }
    if not bridge_ports:
        return result

    # Batch 1: port names + VLAN tags (global table, filtered to bridge_ports)
    port_tags: dict[str, int] = {}
    code, json_out, _ = await _shared_ovs_vsctl(
        "--format=json", "--", "--columns=name,tag", "list", "Port",
    )
    if code == 0 and json_out.strip():
        try:
            data = json.loads(json_out)
            for row in data.get("data", []):
                name = row[0]
                if not isinstance(name, str) or name not in bridge_ports:
                    continue
                tag = row[1]
                if isinstance(tag, int) and tag > 0:
                    port_tags[name] = tag
                else:
                    port_tags[name] = 0
        except (json.JSONDecodeError, IndexError, TypeError) as e:
            logger.debug(f"Failed to parse batch port tags: {e}")

    if not port_tags:
        return result

    # Batch 2: interface type + ofport (global table, filtered to bridge_ports)
    iface_info: dict[str, tuple[str, int]] = {}  # name -> (type, ofport)
    code, json_out, _ = await _shared_ovs_vsctl(
        "--format=json", "--", "--columns=name,type,ofport", "list", "Interface",
    )
    if code == 0 and json_out.strip():
        try:
            data = json.loads(json_out)
            for row in data.get("data", []):
                name = row[0]
                if not isinstance(name, str) or name not in bridge_ports:
                    continue
                itype = row[1] if isinstance(row[1], str) else ""
                ofport = row[2] if isinstance(row[2], int) else -1
                iface_info[name] = (itype, ofport)
        except (json.JSONDecodeError, IndexError, TypeError) as e:
            logger.debug(f"Failed to parse batch interface info: {e}")

    # Merge into result
    for name, tag in port_tags.items():
        itype, ofport = iface_info.get(name, ("", -1))
        result[name] = {
            "name": name,
            "tag": tag,
            "type": itype,
            "ofport": ofport,
        }

    return result


async def write_declared_state_cache(tunnels: list[dict[str, Any]]) -> None:
    """Write declared state to local cache for API-less recovery."""
    try:
        from datetime import datetime, timezone

        cache_path = Path(settings.workspace_path) / "declared_overlay_state.json"
        cache_path.parent.mkdir(parents=True, exist_ok=True)

        cache_data = {
            "declared_at": datetime.now(timezone.utc).isoformat(),
            "tunnels": tunnels,
        }

        tmp_path = cache_path.with_suffix(".tmp")
        with open(tmp_path, "w") as f:
            json.dump(cache_data, f, indent=2)
        tmp_path.rename(cache_path)
    except Exception as e:
        logger.warning(f"Failed to write declared state cache: {e}")


async def load_declared_state_cache() -> list[dict[str, Any]] | None:
    """Load declared state from local cache for recovery.

    Returns:
        List of tunnel dicts if cache exists and is valid, None otherwise
    """
    try:
        cache_path = Path(settings.workspace_path) / "declared_overlay_state.json"
        if not cache_path.exists():
            return None

        with open(cache_path, "r") as f:
            cache_data = json.load(f)

        tunnels = cache_data.get("tunnels")
        if not tunnels:
            return None

        declared_at = cache_data.get("declared_at", "")
        logger.info(
            f"Loaded declared state cache with {len(tunnels)} tunnels "
            f"(declared at {declared_at})"
        )
        return tunnels
    except Exception as e:
        logger.warning(f"Failed to load declared state cache: {e}")
        return None


async def declare_state(
    manager: OverlayManager,
    tunnels: list[dict[str, Any]],
    declared_labs: list[str] | None = None,
) -> dict[str, Any]:
    """Converge overlay state to match API-declared desired state.

    For each declared tunnel:
    - Port exists with correct VNI + VLAN -> "converged"
    - Port exists with wrong VLAN -> update tag -> "updated"
    - Port missing -> create -> "created"
    - Failure -> "error"

    For declared labs, any tracked vxlan-* port not in the declared set is
    deleted as an orphan (scoped to declared labs only).

    Args:
        manager: OverlayManager instance
        tunnels: List of declared tunnel dicts
        declared_labs: Optional explicit list of labs to scope orphan cleanup

    Returns:
        Dict with "results" list and "orphans_removed" list
    """
    await manager._ensure_ovs_bridge()

    results: list[dict[str, Any]] = []
    orphans_removed: list[str] = []
    declared_labs_set = set(declared_labs or [])
    declared_port_names: set[str] = set()

    ovs_ports = await manager._batch_read_ovs_ports()

    if ovs_ports is None:
        logger.warning("Declare-state: OVS query failed, skipping convergence")
        return {"results": [], "orphans_removed": [], "skipped": "ovs_read_error"}

    for t in tunnels:
        link_id = t["link_id"]
        lab_id = t["lab_id"]
        vni = t["vni"]
        local_ip = t["local_ip"]
        remote_ip = t["remote_ip"]
        expected_vlan = t["expected_vlan"]
        port_name = t["port_name"]
        mtu = t.get("mtu", 0)

        declared_labs_set.add(lab_id)
        declared_port_names.add(port_name)

        try:
            port_info = ovs_ports.get(port_name)

            # Detect broken OVS ports: the OVS entry exists but the
            # underlying Linux VXLAN netdev is gone (ofport == -1).
            # Delete the stale OVS port so it falls through to creation.
            if port_info and port_info.get("ofport") == -1:
                logger.warning(
                    f"Declare-state: {port_name} has ofport=-1 "
                    f"(underlying device missing), deleting stale OVS port"
                )
                await manager._ovs_vsctl("del-port", manager._bridge_name, port_name)
                port_info = None

            if port_info:
                current_tag = port_info.get("tag", 0)
                if current_tag == expected_vlan and expected_vlan > 0:
                    status = "converged"
                elif expected_vlan > 0:
                    await manager._ovs_vsctl(
                        "set", "port", port_name, f"tag={expected_vlan}"
                    )
                    status = "updated"
                    logger.info(
                        f"Declare-state: updated {port_name} tag "
                        f"{current_tag} -> {expected_vlan}"
                    )
                else:
                    status = "converged"

                # Enforce MTU on existing port
                if mtu > 0:
                    try:
                        rc, stdout, _ = await manager._run_cmd(
                            ["ip", "link", "show", port_name],
                        )
                        if rc == 0:
                            mtu_match = re.search(r"mtu (\d+)", stdout)
                            current_mtu = int(mtu_match.group(1)) if mtu_match else 0
                            if current_mtu != mtu:
                                await manager._run_cmd(
                                    ["ip", "link", "set", port_name, "mtu", str(mtu)]
                                )
                                if status == "converged":
                                    status = "updated"
                                logger.info(
                                    f"Declare-state: updated {port_name} MTU "
                                    f"{current_mtu} -> {mtu}"
                                )
                    except Exception as e:
                        logger.warning(f"MTU enforcement failed for {port_name}: {e}")

                # Update in-memory tracking with real link_id
                from agent.network.overlay import LinkTunnel
                manager._link_tunnels[link_id] = LinkTunnel(
                    link_id=link_id,
                    vni=vni,
                    local_ip=local_ip,
                    remote_ip=remote_ip,
                    local_vlan=expected_vlan,
                    interface_name=port_name,
                    lab_id=lab_id,
                    tenant_mtu=mtu if mtu > 0 else settings.overlay_mtu,
                )

                results.append({
                    "link_id": link_id,
                    "lab_id": lab_id,
                    "status": status,
                    "actual_vlan": expected_vlan if status != "converged" else current_tag,
                })
            else:
                # Port missing -- create it
                tenant_mtu = mtu if mtu > 0 else (
                    settings.overlay_mtu if settings.overlay_mtu > 0 else 1500
                )

                if await manager._ip_link_exists(port_name):
                    await manager._run_cmd(["ip", "link", "delete", port_name])

                await manager._create_vxlan_device(
                    name=port_name,
                    vni=vni,
                    local_ip=local_ip,
                    remote_ip=remote_ip,
                    bridge=manager._bridge_name,
                    vlan_tag=expected_vlan if expected_vlan > 0 else None,
                    tenant_mtu=tenant_mtu,
                )

                from agent.network.overlay import LinkTunnel
                manager._link_tunnels[link_id] = LinkTunnel(
                    link_id=link_id,
                    vni=vni,
                    local_ip=local_ip,
                    remote_ip=remote_ip,
                    local_vlan=expected_vlan,
                    interface_name=port_name,
                    lab_id=lab_id,
                    tenant_mtu=tenant_mtu,
                )

                results.append({
                    "link_id": link_id,
                    "lab_id": lab_id,
                    "status": "created",
                    "actual_vlan": expected_vlan,
                })
                logger.info(
                    f"Declare-state: created {port_name} "
                    f"(VNI {vni}, VLAN {expected_vlan}) to {remote_ip}"
                )

        except Exception as e:
            results.append({
                "link_id": link_id,
                "lab_id": lab_id,
                "status": "error",
                "error": str(e),
            })
            logger.error(f"Declare-state error for {port_name}: {e}")

    # Orphan cleanup
    if declared_labs_set:
        for port_name, port_info in ovs_ports.items():
            if port_name in declared_port_names:
                continue
            if not port_name.startswith("vxlan-"):
                continue
            lt_for_port = next(
                (lt for lt in manager._link_tunnels.values()
                 if lt.interface_name == port_name),
                None,
            )
            if not lt_for_port:
                continue
            if lt_for_port.lab_id not in declared_labs_set:
                continue

            try:
                await manager._delete_vxlan_device(port_name, manager._bridge_name)
                orphans_removed.append(port_name)
                keys_to_remove = [
                    k for k, v in manager._link_tunnels.items()
                    if v.interface_name == port_name
                ]
                for k in keys_to_remove:
                    del manager._link_tunnels[k]
                logger.info(f"Declare-state: removed orphan {port_name}")
            except Exception as e:
                logger.warning(f"Failed to remove orphan {port_name}: {e}")

    await manager._write_declared_state_cache(tunnels)

    return {"results": results, "orphans_removed": orphans_removed}


async def recover_link_tunnels(manager: OverlayManager) -> int:
    """Recover link tunnel tracking from local cache or OVS state on startup.

    Tries local declared-state cache first (provides real link IDs).
    Falls back to OVS port scan if no cache (placeholder link IDs).

    Args:
        manager: OverlayManager instance

    Returns:
        Number of link tunnels recovered
    """
    cached = await load_declared_state_cache()
    if cached:
        try:
            result = await manager.declare_state(cached)
            cache_recovered = sum(
                1 for r in result["results"]
                if r["status"] in ("converged", "created", "updated")
            )
            if cache_recovered > 0:
                logger.info(
                    f"Recovered {cache_recovered} link tunnel(s) from declared-state cache"
                )
                return cache_recovered
        except Exception as e:
            logger.warning(f"Cache-based recovery failed, falling back to OVS scan: {e}")

    # Fallback: scan OVS ports (placeholder link_ids)
    recovered = 0
    try:
        code, stdout, _ = await _shared_ovs_vsctl(
            "list-ports", manager._bridge_name
        )
        if code != 0:
            return 0

        port_names = [
            p.strip() for p in stdout.strip().split("\n")
            if p.strip().startswith("vxlan-")
        ]

        for port_name in port_names:
            code, tag_out, _ = await _shared_ovs_vsctl(
                "get", "port", port_name, "tag"
            )
            local_vlan = 0
            if code == 0:
                tag_str = tag_out.strip()
                if tag_str and tag_str != "[]":
                    try:
                        local_vlan = int(tag_str)
                    except ValueError:
                        pass

            from agent.network.overlay_vxlan import read_vxlan_link_info
            vni, remote_ip, local_ip = await read_vxlan_link_info(port_name)

            if not vni or not remote_ip:
                continue

            from agent.network.overlay import LinkTunnel
            tunnel = LinkTunnel(
                link_id=port_name,
                vni=vni,
                local_ip=local_ip,
                remote_ip=remote_ip,
                local_vlan=local_vlan,
                interface_name=port_name,
                lab_id="recovered",
                tenant_mtu=settings.overlay_mtu,
            )
            manager._link_tunnels[port_name] = tunnel
            recovered += 1

        if recovered > 0:
            logger.info(
                f"Recovered {recovered} link tunnel(s) from OVS state"
            )
    except Exception as e:
        logger.warning(f"Link tunnel recovery failed: {e}")

    return recovered
