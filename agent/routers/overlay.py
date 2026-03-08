"""Overlay, tunnel, external connectivity, and bridge endpoints."""
from __future__ import annotations

import asyncio
import re
import logging

import docker
from fastapi import APIRouter, HTTPException

from agent.config import settings
from agent.agent_state import get_overlay_manager
from agent.helpers import (
    get_provider_for_request,
    _get_docker_ovs_plugin,
    _validate_port_name,
    _resolve_ovs_port,
    _ovs_set_port_vlan,
)
from agent.network.backends.registry import get_network_backend
from agent.providers import get_provider
from agent.schemas import (
    AttachContainerRequest,
    AttachContainerResponse,
    AttachOverlayExternalRequest,
    AttachOverlayExternalResponse,
    AttachOverlayInterfaceRequest,
    AttachOverlayInterfaceResponse,
    BridgeDeletePatchRequest,
    BridgeDeletePatchResponse,
    BridgePatchRequest,
    BridgePatchResponse,
    CleanupAuditRequest,
    CleanupAuditResponse,
    CleanupOverlayRequest,
    CleanupOverlayResponse,
    CreateTunnelRequest,
    CreateTunnelResponse,
    DeclareOverlayStateRequest,
    DeclareOverlayStateResponse,
    DeclaredTunnelResult,
    DeclarePortStateRequest,
    DeclarePortStateResponse,
    DeclaredPortResult,
    DetachOverlayInterfaceRequest,
    DetachOverlayInterfaceResponse,
    EnsureVtepRequest,
    EnsureVtepResponse,
    ExternalConnectRequest,
    ExternalConnectResponse,
    ExternalConnectionInfo,
    ExternalDisconnectRequest,
    ExternalDisconnectResponse,
    ExternalListResponse,
    MtuTestRequest,
    MtuTestResponse,
    OverlayStatusResponse,
    PortInfo,
    PortStateResponse,
    TunnelInfo,
    VtepInfo,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["overlay"])


@router.post("/overlay/tunnel")
async def create_tunnel(request: CreateTunnelRequest) -> CreateTunnelResponse:
    """Create a VXLAN tunnel to another host.

    This creates a VXLAN interface and associated bridge for
    connecting lab nodes across hosts.
    """
    if not settings.enable_vxlan:
        return CreateTunnelResponse(
            success=False,
            error="VXLAN overlay not enabled on this agent",
        )

    logger.info(f"Creating tunnel: lab={request.lab_id}, link={request.link_id}, remote={request.remote_ip}")

    try:
        # Create VXLAN tunnel
        backend = get_network_backend()
        tunnel = await backend.overlay_create_tunnel(
            lab_id=request.lab_id,
            link_id=request.link_id,
            local_ip=request.local_ip,
            remote_ip=request.remote_ip,
            vni=request.vni,
        )

        # Create bridge and attach VXLAN
        await backend.overlay_create_bridge(tunnel)

        return CreateTunnelResponse(
            success=True,
            tunnel=TunnelInfo(
                vni=tunnel.vni,
                interface_name=tunnel.interface_name,
                local_ip=tunnel.local_ip,
                remote_ip=tunnel.remote_ip,
                lab_id=tunnel.lab_id,
                link_id=tunnel.link_id,
                vlan_tag=tunnel.vlan_tag,
            ),
        )

    except Exception as e:
        logger.error(f"Tunnel creation failed: {e}")
        return CreateTunnelResponse(
            success=False,
            error=str(e),
        )


@router.post("/overlay/attach")
async def attach_container(request: AttachContainerRequest) -> AttachContainerResponse:
    """Attach a container to an overlay bridge.

    This creates a veth pair, moves one end into the container,
    and attaches the other to the overlay bridge.
    """
    if not settings.enable_vxlan:
        return AttachContainerResponse(
            success=False,
            error="VXLAN overlay not enabled on this agent",
        )

    # Convert short container name to full Docker container name
    # The API sends short names like "eos_1", but Docker needs the full name
    # like "archetype-d35ec857-eos_1"
    provider = get_provider("docker")
    if provider is None:
        return AttachContainerResponse(
            success=False,
            error="Docker provider not available",
        )
    full_container_name = provider.get_container_name(request.lab_id, request.container_name)

    # Convert interface name for cEOS containers
    # cEOS uses INTFTYPE=eth, meaning CLI "Ethernet1" maps to Linux "eth1"
    interface_name = request.interface_name
    try:
        def _get_container_env() -> list[str]:
            c = provider.docker.containers.get(full_container_name)
            return c.attrs.get("Config", {}).get("Env", [])

        env_vars = await asyncio.to_thread(_get_container_env)
        intftype = None
        for env in env_vars:
            if env.startswith("INTFTYPE="):
                intftype = env.split("=", 1)[1]
                break
        if intftype == "eth" and interface_name.startswith("Ethernet"):
            # Convert Ethernet1 -> eth1, Ethernet2 -> eth2, etc.
            match = re.match(r"Ethernet(\d+)", interface_name)
            if match:
                interface_name = f"eth{match.group(1)}"
                logger.info(f"Converted interface name: {request.interface_name} -> {interface_name}")
    except Exception as e:
        logger.warning(f"Could not check container env for interface conversion: {e}")

    logger.info(f"Attaching container: {full_container_name} to bridge for {request.link_id}")

    try:
        # Get the bridge for this link
        backend = get_network_backend()
        bridges = await backend.overlay_get_bridges_for_lab(request.lab_id)
        bridge = None
        for b in bridges:
            if b.link_id == request.link_id:
                bridge = b
                break

        if not bridge:
            return AttachContainerResponse(
                success=False,
                error=f"No bridge found for link {request.link_id}",
            )

        # Attach container
        success = await backend.overlay_attach_container(
            bridge=bridge,
            container_name=full_container_name,
            interface_name=interface_name,
            ip_address=request.ip_address,
        )

        if success:
            return AttachContainerResponse(success=True)
        else:
            return AttachContainerResponse(
                success=False,
                error="Failed to attach container to bridge",
            )

    except Exception as e:
        logger.error(f"Container attachment failed: {e}")
        return AttachContainerResponse(
            success=False,
            error=str(e),
        )


@router.post("/overlay/cleanup")
async def cleanup_overlay(request: CleanupOverlayRequest) -> CleanupOverlayResponse:
    """Clean up all overlay networking for a lab."""
    if not settings.enable_vxlan:
        return CleanupOverlayResponse()

    logger.info(f"Cleaning up overlay for lab: {request.lab_id}")

    try:
        backend = get_network_backend()
        result = await backend.overlay_cleanup_lab(request.lab_id)

        return CleanupOverlayResponse(
            tunnels_deleted=result["tunnels_deleted"],
            bridges_deleted=result["bridges_deleted"],
            errors=result["errors"],
        )

    except Exception as e:
        logger.error(f"Overlay cleanup failed: {e}")
        return CleanupOverlayResponse(errors=[str(e)])


@router.post("/cleanup/audit")
async def cleanup_audit(request: CleanupAuditRequest) -> CleanupAuditResponse:
    """Dry-run cleanup audit (no deletions)."""
    errors: list[str] = []

    try:
        from agent.network.cleanup import get_cleanup_manager
        cleanup_mgr = get_cleanup_manager()
        stats = await cleanup_mgr.run_full_cleanup(dry_run=True, include_ovs=False)
        network = stats.to_dict()
    except Exception as e:
        network = {}
        errors.append(f"network_audit_failed: {e}")

    ovs_result = None
    if request.include_ovs:
        try:
            backend = get_network_backend()
            ovs_mgr = getattr(backend, "ovs_manager", None)
            overlay_mgr = getattr(backend, "overlay_manager", None)
            ovs_result = {
                "bridge_initialized": False,
                "orphaned_ports": [],
                "vxlan_orphan_ports": [],
            }

            if ovs_mgr and getattr(ovs_mgr, "_initialized", False):
                ovs_result["bridge_initialized"] = True
                bridge_state = await ovs_mgr.get_ovs_bridge_state()
                ovs_result["orphaned_ports"] = [
                    p.get("port_name") for p in bridge_state.get("orphaned_ports", [])
                ]

                if overlay_mgr:
                    tracked_vxlan = set()
                    for t in overlay_mgr._tunnels.values():
                        tracked_vxlan.add(t.interface_name)
                    for vtep in overlay_mgr._vteps.values():
                        tracked_vxlan.add(vtep.interface_name)
                    for lt in overlay_mgr._link_tunnels.values():
                        tracked_vxlan.add(lt.interface_name)

                    all_ports = await ovs_mgr.get_all_ovs_ports()
                    ovs_result["vxlan_orphan_ports"] = [
                        p.get("port_name")
                        for p in all_ports
                        if p.get("type") == "vxlan" and p.get("port_name") not in tracked_vxlan
                    ]
        except Exception as e:
            errors.append(f"ovs_audit_failed: {e}")
            if ovs_result is None:
                ovs_result = {"bridge_initialized": False, "orphaned_ports": [], "vxlan_orphan_ports": []}

    return CleanupAuditResponse(
        network=network,
        ovs=ovs_result,
        errors=errors,
    )


@router.get("/overlay/status")
async def overlay_status() -> OverlayStatusResponse:
    """Get status of all overlay networks on this agent."""
    if not settings.enable_vxlan:
        return OverlayStatusResponse()

    try:
        backend = get_network_backend()
        status = backend.overlay_status()

        tunnels = [
            TunnelInfo(
                vni=t["vni"],
                interface_name=t["interface"],
                local_ip=t["local_ip"],
                remote_ip=t["remote_ip"],
                lab_id=t["lab_id"],
                link_id=t["link_id"],
            )
            for t in status["tunnels"]
        ]

        return OverlayStatusResponse(
            vteps=status.get("vteps", []),
            tunnels=tunnels,
            bridges=status["bridges"],
            link_tunnels=status.get("link_tunnels", []),
        )

    except Exception as e:
        logger.error(f"Overlay status failed: {e}")
        return OverlayStatusResponse()



@router.delete("/overlay/bridge-ports/{port_name}")
async def delete_bridge_port(port_name: str):
    """Delete an OVS port by name (for cleanup of orphaned ports)."""
    if not _validate_port_name(port_name):
        return {"deleted": False, "port_name": port_name, "message": "Invalid port name"}

    bridge = settings.ovs_bridge_name or "arch-ovs"

    async def run(*args: str) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        return proc.returncode, (stdout or stderr).decode().strip()

    code, output = await run("ovs-vsctl", "--if-exists", "del-port", bridge, port_name)
    return {
        "deleted": code == 0,
        "port_name": port_name,
        "message": output or "ok",
    }


@router.put("/overlay/ports/{port_name}/vlan")
async def set_overlay_port_vlan(port_name: str, request: dict):
    """Set the VLAN tag on an OVS port.

    Used by the controller to repair VLAN drift without full link recreation.
    """
    if not _validate_port_name(port_name):
        return {"success": False, "error": "Invalid port name"}

    vlan_tag = request.get("vlan_tag")
    if vlan_tag is None or not isinstance(vlan_tag, int):
        return {"success": False, "error": "vlan_tag (integer) is required"}

    if await _ovs_set_port_vlan(port_name, vlan_tag):
        return {"success": True, "port_name": port_name, "vlan_tag": vlan_tag}
    return {"success": False, "error": f"Failed to set VLAN {vlan_tag} on port {port_name}"}


@router.get("/overlay/bridge-ports")
async def overlay_bridge_ports():
    """Debug: query actual OVS bridge for VXLAN ports and their config."""
    bridge = settings.ovs_bridge_name or "arch-ovs"

    async def run(*args: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        return stdout.decode().strip()

    # Get all ports on the bridge
    ports_raw = await run("ovs-vsctl", "list-ports", bridge)
    all_ports = ports_raw.split("\n") if ports_raw else []

    vxlan_ports = []
    other_ports = []
    for port_name in all_ports:
        if not port_name:
            continue
        iface_type = await run(
            "ovs-vsctl", "get", "interface", port_name, "type"
        )
        # Detect VXLAN ports by OVS type OR by name pattern (Linux VXLAN
        # devices added as system ports have type="" in OVS)
        is_vxlan = (
            iface_type == "vxlan"
            or port_name.startswith(("vxlan-", "vxlan", "vtep"))
        )
        if is_vxlan:
            tag = await run("ovs-vsctl", "get", "port", port_name, "tag")
            options = await run("ovs-vsctl", "get", "interface", port_name, "options")
            stats = await run(
                "ovs-vsctl", "get", "interface", port_name, "statistics"
            )
            vxlan_ports.append({
                "name": port_name,
                "tag": tag,
                "options": options,
                "statistics": stats,
                "type": iface_type,
            })
        else:
            tag = await run("ovs-vsctl", "get", "port", port_name, "tag")
            other_ports.append({"name": port_name, "tag": tag})

    fdb_raw = await run("ovs-appctl", "fdb/show", bridge)

    return {
        "bridge": bridge,
        "total_ports": len(all_ports),
        "vxlan_ports": vxlan_ports,
        "container_ports_sample": other_ports[:10],
        "container_ports_all": other_ports,
        "fdb_lines": fdb_raw.split("\n")[:40] if fdb_raw else [],
    }


@router.get("/overlay/port-ifindex")
async def overlay_port_ifindex():
    """Get ifindex for all non-VXLAN ports on the OVS bridge.

    Used to match container veth peers to OVS port names.
    """
    bridge = settings.ovs_bridge_name or "arch-ovs"

    async def run_cmd(*args: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        return stdout.decode().strip()

    ports_raw = await run_cmd("ovs-vsctl", "list-ports", bridge)
    all_ports = ports_raw.split("\n") if ports_raw else []

    results = []
    for port_name in all_ports:
        if not port_name or port_name.startswith(("vxlan-", "vtep")):
            continue
        tag = await run_cmd("ovs-vsctl", "get", "port", port_name, "tag")
        ifindex = await run_cmd(
            "ovs-vsctl", "get", "interface", port_name, "ifindex"
        )
        results.append({"name": port_name, "tag": tag, "ifindex": ifindex})

    return {"ports": results}


@router.post("/overlay/declare-state")
async def declare_overlay_state(request: DeclareOverlayStateRequest):
    """Converge overlay state to match API-declared desired state.

    Creates missing tunnels, updates drifted VLAN tags, removes orphans.
    This is a strict superset of /overlay/reconcile-ports.
    """
    overlay = get_overlay_manager()
    tunnel_dicts = [t.model_dump() for t in request.tunnels]
    result = await overlay.declare_state(
        tunnel_dicts,
        declared_labs=request.declared_labs,
    )

    return DeclareOverlayStateResponse(
        results=[
            DeclaredTunnelResult(**r)
            for r in result["results"]
        ],
        orphans_removed=result.get("orphans_removed", []),
    )


@router.get("/labs/{lab_id}/port-state")
async def get_lab_port_state(lab_id: str) -> PortStateResponse:
    """Get OVS port state for all container interfaces in a lab.

    Uses ifindex matching to verify correct veth-to-interface mapping,
    bypassing stale Docker plugin state that can have swapped mappings
    after agent restarts. Reads actual VLAN tags from OVS.
    """
    try:
        docker_provider = get_provider("docker")
        if docker_provider is None:
            return PortStateResponse(ports=[])

        # Step 1: Collect per-container interface->iflink data via Docker SDK
        # in a worker thread so we do not block the asyncio event loop.
        def _collect_container_iflinks() -> list[tuple[str, str, str]]:
            client = docker.from_env(timeout=settings.docker_client_timeout)
            containers = client.containers.list(
                filters={"label": f"archetype.lab_id={lab_id}"},
            )
            if not containers:
                return []

            results: list[tuple[str, str, str]] = []
            for container in containers:
                cname = container.name or ""
                labels = getattr(container, "labels", {}) or {}
                node_name = labels.get("archetype.node_name") or cname
                try:
                    exit_code, output = container.exec_run(
                        [
                            "sh",
                            "-c",
                            "for iface in /sys/class/net/eth*; do "
                            "name=$(basename $iface); "
                            "iflink=$(cat $iface/iflink 2>/dev/null); "
                            "echo $name:$iflink; done",
                        ],
                        demux=False,
                    )
                except Exception:
                    continue

                if exit_code != 0:
                    continue
                results.append((node_name, cname, output.decode("utf-8", errors="replace")))

            return results

        container_iflinks = await asyncio.to_thread(_collect_container_iflinks)
        if not container_iflinks:
            return PortStateResponse(ports=[])

        # Step 2: Build ifindex -> ovs_port_name map from OVS
        bridge = settings.ovs_bridge_name or "arch-ovs"
        proc = await asyncio.create_subprocess_exec(
            "ovs-vsctl", "list-ports", bridge,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        ovs_ports = [
            p.strip() for p in stdout.decode().strip().split("\n")
            if p.strip() and p.strip().startswith("vh")
        ]

        ifindex_to_port: dict[int, tuple[str, int]] = {}  # ifindex -> (name, tag)
        for port_name in ovs_ports:
            proc = await asyncio.create_subprocess_exec(
                "ovs-vsctl", "get", "interface", port_name, "ifindex",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            idx_out, _ = await proc.communicate()
            proc2 = await asyncio.create_subprocess_exec(
                "ovs-vsctl", "get", "port", port_name, "tag",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            tag_out, _ = await proc2.communicate()
            try:
                ifidx = int(idx_out.decode().strip())
                tag_str = tag_out.decode().strip()
                tag = int(tag_str) if tag_str and tag_str != "[]" else 0
                ifindex_to_port[ifidx] = (port_name, tag)
            except (ValueError, TypeError):
                continue

        # Step 3: For each container, read interface iflinks and match
        ports = []
        for node_name, _cname, iface_output in container_iflinks:
            for line in iface_output.strip().split("\n"):
                if ":" not in line:
                    continue
                iface_name, peer_idx_str = line.strip().split(":", 1)
                if iface_name == "eth0":
                    continue  # Skip management interface
                try:
                    peer_ifindex = int(peer_idx_str.strip())
                except (ValueError, TypeError):
                    continue

                port_info = ifindex_to_port.get(peer_ifindex)
                if port_info:
                    ovs_port_name, vlan_tag = port_info
                    ports.append(PortInfo(
                        node_name=node_name,
                        interface_name=iface_name,
                        ovs_port_name=ovs_port_name,
                        vlan_tag=vlan_tag,
                    ))

        return PortStateResponse(ports=ports)
    except Exception as e:
        logger.error(f"Port state query failed for lab {lab_id}: {e}")
        return PortStateResponse(ports=[])


@router.post("/ports/declare-state")
async def declare_port_state(request: DeclarePortStateRequest) -> DeclarePortStateResponse:
    """Converge same-host port state to match API-declared pairings.

    For each declared pairing, ensures both OVS ports have the
    declared VLAN tag. Creates L2 connectivity by VLAN matching.
    """
    if not settings.enable_ovs_plugin:
        return DeclarePortStateResponse(results=[])

    try:
        plugin = _get_docker_ovs_plugin()
    except Exception as e:
        logger.error(f"Port declare-state failed: {e}")
        return DeclarePortStateResponse(results=[])

    async def _sync_plugin_endpoint_vlan(lab_id: str, port_name: str, vlan_tag: int) -> None:
        sync_fn = getattr(plugin, "set_endpoint_vlan_by_host_veth", None)
        if not callable(sync_fn):
            return
        try:
            synced = await sync_fn(lab_id, port_name, vlan_tag)
            if not synced:
                logger.debug(
                    "Port declare-state: no tracked endpoint for %s while syncing VLAN %s",
                    port_name,
                    vlan_tag,
                )
        except Exception as sync_exc:
            logger.warning(
                "Port declare-state: failed to sync plugin state for %s to VLAN %s: %s",
                port_name,
                vlan_tag,
                sync_exc,
            )

    results = []
    for pairing in request.pairings:
        try:
            # Check current VLAN tags on both ports
            tag_a = None
            tag_b = None

            code_a, out_a, _ = await plugin._ovs_vsctl(
                "get", "port", pairing.port_a, "tag"
            )
            if code_a == 0:
                tag_str = out_a.strip()
                if tag_str and tag_str != "[]":
                    try:
                        tag_a = int(tag_str)
                    except ValueError:
                        pass

            code_b, out_b, _ = await plugin._ovs_vsctl(
                "get", "port", pairing.port_b, "tag"
            )
            if code_b == 0:
                tag_str = out_b.strip()
                if tag_str and tag_str != "[]":
                    try:
                        tag_b = int(tag_str)
                    except ValueError:
                        pass

            # Check if both match declared VLAN
            if tag_a == pairing.vlan_tag and tag_b == pairing.vlan_tag:
                results.append(DeclaredPortResult(
                    link_name=pairing.link_name,
                    lab_id=pairing.lab_id,
                    status="converged",
                    actual_vlan=pairing.vlan_tag,
                ))
                await _sync_plugin_endpoint_vlan(
                    pairing.lab_id,
                    pairing.port_a,
                    pairing.vlan_tag,
                )
                await _sync_plugin_endpoint_vlan(
                    pairing.lab_id,
                    pairing.port_b,
                    pairing.vlan_tag,
                )
            else:
                # Update mismatched ports
                updated = False
                if tag_a != pairing.vlan_tag:
                    code, _, err = await plugin._ovs_vsctl(
                        "set", "port", pairing.port_a, f"tag={pairing.vlan_tag}"
                    )
                    if code != 0:
                        raise Exception(f"Failed to set VLAN on {pairing.port_a}: {err}")
                    updated = True

                if tag_b != pairing.vlan_tag:
                    code, _, err = await plugin._ovs_vsctl(
                        "set", "port", pairing.port_b, f"tag={pairing.vlan_tag}"
                    )
                    if code != 0:
                        raise Exception(f"Failed to set VLAN on {pairing.port_b}: {err}")
                    updated = True

                results.append(DeclaredPortResult(
                    link_name=pairing.link_name,
                    lab_id=pairing.lab_id,
                    status="updated" if updated else "converged",
                    actual_vlan=pairing.vlan_tag,
                ))
                await _sync_plugin_endpoint_vlan(
                    pairing.lab_id,
                    pairing.port_a,
                    pairing.vlan_tag,
                )
                await _sync_plugin_endpoint_vlan(
                    pairing.lab_id,
                    pairing.port_b,
                    pairing.vlan_tag,
                )

        except Exception as e:
            results.append(DeclaredPortResult(
                link_name=pairing.link_name,
                lab_id=pairing.lab_id,
                status="error",
                error=str(e),
            ))
            logger.error(f"Port declare-state error for {pairing.link_name}: {e}")

    return DeclarePortStateResponse(results=results)


@router.post("/overlay/reconcile-ports")
async def reconcile_overlay_ports(request: dict):
    """Remove stale VXLAN ports not in the valid set.

    The API knows which VXLAN ports should exist (from vxlan_tunnels DB table).
    It sends the valid port names here; we delete any VXLAN port not in the list.
    """
    valid_port_names = set(request.get("valid_port_names", []))
    force = bool(request.get("force", False))
    confirm = bool(request.get("confirm", False))
    allow_empty = bool(request.get("allow_empty", False))
    if force and not confirm:
        return {
            "removed_ports": [],
            "valid_count": len(valid_port_names),
            "skipped": True,
            "reason": "force requires confirm=true",
        }
    if not valid_port_names and not force:
        return {
            "removed_ports": [],
            "valid_count": 0,
            "skipped": True,
            "reason": "empty valid_port_names",
        }
    if force and not valid_port_names and not allow_empty:
        return {
            "removed_ports": [],
            "valid_count": 0,
            "skipped": True,
            "reason": "empty valid_port_names requires allow_empty=true when force=true",
        }
    bridge = settings.ovs_bridge_name or "arch-ovs"

    async def run(*args: str) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        return proc.returncode, (stdout or stderr).decode().strip()

    # List all ports on the bridge
    _, ports_raw = await run("ovs-vsctl", "list-ports", bridge)
    all_ports = ports_raw.split("\n") if ports_raw else []

    removed = []
    for port_name in all_ports:
        if not port_name:
            continue
        # Check if this port is a VXLAN type (OVS-managed or Linux device)
        _, iface_type = await run("ovs-vsctl", "get", "interface", port_name, "type")
        is_vxlan = (
            iface_type == "vxlan"
            or port_name.startswith(("vxlan-", "vxlan"))
        )
        if not is_vxlan:
            continue
        # Skip if it's in the valid set
        if port_name in valid_port_names:
            continue
        # Validate port name before deletion to prevent injection
        if not _validate_port_name(port_name):
            logger.warning(f"Skipping invalid port name during reconciliation: {port_name!r}")
            continue
        # Delete the stale VXLAN port from OVS
        code, msg = await run("ovs-vsctl", "--if-exists", "del-port", bridge, port_name)
        if code == 0:
            # Clean up Linux VXLAN device (system ports aren't auto-deleted by OVS)
            await run("ip", "link", "delete", port_name)
            removed.append(port_name)
            logger.info(f"Removed stale VXLAN port: {port_name}")
        else:
            logger.warning(f"Failed to remove VXLAN port {port_name}: {msg}")

    return {"removed_ports": removed, "valid_count": len(valid_port_names)}


@router.post("/overlay/vtep")
async def ensure_vtep(request: EnsureVtepRequest) -> EnsureVtepResponse:
    """Ensure a VTEP exists to the remote host.

    This implements the new trunk VTEP model where there is one VTEP per
    remote host (not one per link). The VTEP is created in trunk mode
    (no VLAN tag) and all cross-host links to that remote host share it.

    If a VTEP already exists to the remote host, it is returned without
    creating a new one.
    """
    if not settings.enable_vxlan:
        return EnsureVtepResponse(
            success=False,
            error="VXLAN overlay is disabled on this agent",
        )

    try:
        # Check if VTEP already exists
        backend = get_network_backend()
        existing = backend.overlay_get_vtep(request.remote_ip)
        if existing:
            return EnsureVtepResponse(
                success=True,
                vtep=VtepInfo(
                    interface_name=existing.interface_name,
                    vni=existing.vni,
                    local_ip=existing.local_ip,
                    remote_ip=existing.remote_ip,
                    remote_host_id=existing.remote_host_id,
                    tenant_mtu=existing.tenant_mtu,
                ),
                created=False,
            )

        # Create new VTEP
        vtep = await backend.overlay_ensure_vtep(
            local_ip=request.local_ip,
            remote_ip=request.remote_ip,
            remote_host_id=request.remote_host_id,
        )

        return EnsureVtepResponse(
            success=True,
            vtep=VtepInfo(
                interface_name=vtep.interface_name,
                vni=vtep.vni,
                local_ip=vtep.local_ip,
                remote_ip=vtep.remote_ip,
                remote_host_id=vtep.remote_host_id,
                tenant_mtu=vtep.tenant_mtu,
            ),
            created=True,
        )

    except Exception as e:
        logger.error(f"Ensure VTEP failed: {e}")
        return EnsureVtepResponse(success=False, error=str(e))


@router.post("/overlay/attach-link")
async def attach_overlay_interface(
    request: AttachOverlayInterfaceRequest,
) -> AttachOverlayInterfaceResponse:
    """Create a per-link VXLAN tunnel and attach a node interface.

    Per-link VNI model: each cross-host link gets its own VXLAN port on OVS
    in access mode. The agent discovers the node's local VLAN from OVS
    (supports both Docker containers and libvirt VMs) and creates an
    access-mode VXLAN port with tag=<local_vlan> and options:key=<vni>.

    No prior VTEP creation is needed -- this endpoint is self-contained.
    """
    if not settings.enable_vxlan:
        return AttachOverlayInterfaceResponse(
            success=False,
            error="VXLAN overlay is disabled on this agent",
        )

    try:
        # Step 1: Discover the node's current OVS port
        port_info = await _resolve_ovs_port(
            request.lab_id, request.container_name, request.interface_name
        )
        if not port_info:
            return AttachOverlayInterfaceResponse(
                success=False,
                error=(
                    f"Could not find OVS port for "
                    f"{request.container_name}:{request.interface_name}"
                ),
            )

        # Step 2: Allocate a linked-range VLAN for the container port + VXLAN tunnel
        plugin = _get_docker_ovs_plugin()
        if plugin:
            lab_bridge = await plugin._ensure_bridge(request.lab_id)
            local_vlan = await plugin._allocate_linked_vlan(lab_bridge)
            # Set the container port to the new linked VLAN
            await plugin._ovs_vsctl(
                "set", "port", port_info.port_name, f"tag={local_vlan}"
            )
            # Release old isolated-range tag
            if port_info.vlan_tag > 0:
                plugin._release_vlan(port_info.vlan_tag)
            sync_fn = getattr(plugin, "set_endpoint_vlan_by_host_veth", None)
            if callable(sync_fn):
                try:
                    await sync_fn(request.lab_id, port_info.port_name, local_vlan)
                except Exception as sync_exc:
                    logger.warning(
                        "Attach-link plugin state sync failed for %s: %s",
                        port_info.port_name,
                        sync_exc,
                    )
        else:
            # Fallback: use the container's current tag (no plugin available)
            local_vlan = port_info.vlan_tag

        if local_vlan <= 0:
            return AttachOverlayInterfaceResponse(
                success=False,
                error=(
                    f"Invalid VLAN tag ({local_vlan}) for "
                    f"{request.container_name}:{request.interface_name}"
                ),
            )

        # Step 3: Create per-link access-mode VXLAN port
        backend = get_network_backend()
        tunnel = await backend.overlay_create_link_tunnel(
            lab_id=request.lab_id,
            link_id=request.link_id,
            vni=request.vni,
            local_ip=request.local_ip,
            remote_ip=request.remote_ip,
            local_vlan=local_vlan,
            tenant_mtu=request.tenant_mtu,
        )

        return AttachOverlayInterfaceResponse(
            success=True,
            local_vlan=local_vlan,
            vni=tunnel.vni,
        )

    except Exception as e:
        logger.error(f"Attach overlay interface failed: {e}")
        return AttachOverlayInterfaceResponse(success=False, error=str(e))


@router.post("/overlay/detach-link")
async def detach_overlay_interface(
    request: DetachOverlayInterfaceRequest,
) -> DetachOverlayInterfaceResponse:
    """Detach a link from the overlay network.

    This performs a complete detach:
    1. Isolates the container interface by assigning a unique VLAN tag
    2. Deletes the per-link VXLAN tunnel port
    """
    if not settings.enable_vxlan:
        return DetachOverlayInterfaceResponse(
            success=False,
            error="VXLAN overlay is disabled on this agent",
        )

    logger.info(
        f"Detach overlay interface: lab={request.lab_id}, "
        f"container={request.container_name}, interface={request.interface_name}, "
        f"link_id={request.link_id}"
    )

    interface_isolated = False
    new_vlan = None
    tunnel_deleted = False

    try:
        # Step 1: Isolate the interface by assigning a unique VLAN
        try:
            plugin = _get_docker_ovs_plugin()
            if plugin is None:
                logger.warning("Docker OVS plugin not available, skipping interface isolation")
            else:
                provider = get_provider_for_request()
                container_name = provider.get_container_name(request.lab_id, request.container_name)

                new_vlan = await plugin.isolate_port(
                    request.lab_id,
                    container_name,
                    request.interface_name,
                )
                if new_vlan is not None:
                    interface_isolated = True
                    logger.info(
                        f"Interface {container_name}:{request.interface_name} "
                        f"isolated to VLAN {new_vlan}"
                    )
                else:
                    logger.warning(
                        f"Failed to isolate {container_name}:{request.interface_name}"
                    )
        except Exception as e:
            logger.warning(f"Interface isolation failed (continuing with tunnel cleanup): {e}")

        # Step 2: Delete the per-link VXLAN tunnel port
        backend = get_network_backend()
        tunnel_deleted = await backend.overlay_delete_link_tunnel(
            link_id=request.link_id,
            lab_id=request.lab_id,
        )

        if not tunnel_deleted:
            return DetachOverlayInterfaceResponse(
                success=False,
                interface_isolated=interface_isolated,
                new_vlan=new_vlan,
                tunnel_deleted=tunnel_deleted,
                error="Failed to delete VXLAN tunnel",
            )

        return DetachOverlayInterfaceResponse(
            success=True,
            interface_isolated=interface_isolated,
            new_vlan=new_vlan,
            tunnel_deleted=tunnel_deleted,
        )

    except Exception as e:
        logger.error(f"Detach overlay interface failed: {e}")
        return DetachOverlayInterfaceResponse(
            success=False,
            interface_isolated=interface_isolated,
            new_vlan=new_vlan,
            error=str(e),
        )


@router.post("/overlay/attach-external-link")
async def attach_overlay_external(
    request: AttachOverlayExternalRequest,
) -> AttachOverlayExternalResponse:
    """Create a per-link VXLAN tunnel for an external interface.

    Similar to attach_overlay_interface but for external (non-container)
    interfaces. Uses the provided VLAN tag directly instead of discovering
    it from a container.
    """
    if not settings.enable_vxlan:
        return AttachOverlayExternalResponse(
            success=False,
            error="VXLAN overlay is disabled on this agent",
        )

    try:
        backend = get_network_backend()
        tunnel = await backend.overlay_create_link_tunnel(
            lab_id=request.lab_id,
            link_id=request.link_id,
            vni=request.vni,
            local_ip=request.local_ip,
            remote_ip=request.remote_ip,
            local_vlan=request.vlan_tag,
            tenant_mtu=0,
        )

        return AttachOverlayExternalResponse(
            success=True,
            vni=tunnel.vni,
        )

    except Exception as e:
        logger.error(f"Attach overlay external failed: {e}")
        return AttachOverlayExternalResponse(success=False, error=str(e))


@router.post("/network/test-mtu")
async def test_mtu(request: MtuTestRequest) -> MtuTestResponse:
    """Test MTU to a target IP address.

    Runs ping with DF (Don't Fragment) bit set to verify the network path
    supports the requested MTU. Also detects link type (direct/routed) via
    TTL analysis.

    Link type detection:
    - TTL >= 64: Direct/switched (L2 adjacent)
    - TTL < 64: Routed (TTL decremented by intermediate hops)

    Args:
        request: Target IP and MTU to test

    Returns:
        MtuTestResponse with test results
    """
    import ipaddress as _ipaddress

    target_ip = request.target_ip
    mtu = request.mtu

    # Validate IP addresses before passing to subprocess
    try:
        _ipaddress.ip_address(target_ip)
        if request.source_ip:
            _ipaddress.ip_address(request.source_ip)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid IP address")

    # Calculate ping payload size: MTU - 20 (IP header) - 8 (ICMP header)
    payload_size = mtu - 28

    if payload_size < 0:
        return MtuTestResponse(
            success=False,
            error=f"MTU {mtu} too small (minimum 28 bytes for IP + ICMP headers)",
        )

    source_ip = request.source_ip
    logger.info(f"Testing MTU {mtu} to {target_ip} (payload size: {payload_size}, source: {source_ip or 'auto'})")

    try:
        # Run ping with DF bit set (-M do = don't fragment)
        # -c 3: send 3 pings
        # -W 5: 5 second timeout
        # -s: payload size
        # -I: source address (for data plane testing)
        ping_args = [
            "ping",
            "-M", "do",  # Don't fragment
            "-c", "3",
            "-W", "5",
            "-s", str(payload_size),
        ]
        if source_ip:
            ping_args.extend(["-I", source_ip])
        ping_args.append(target_ip)

        process = await asyncio.create_subprocess_exec(
            *ping_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=20.0,
        )
        stdout_text = stdout.decode(errors="replace")
        stderr_text = stderr.decode(errors="replace")

        if process.returncode != 0:
            # Check for "message too long" which indicates MTU issue
            combined = stdout_text + stderr_text
            if "message too long" in combined.lower() or "frag needed" in combined.lower():
                return MtuTestResponse(
                    success=False,
                    error=f"Path MTU too small for {mtu} bytes",
                )
            return MtuTestResponse(
                success=False,
                error=f"Ping failed: {stderr_text.strip() or stdout_text.strip() or 'Unknown error'}",
            )

        # Parse ping output for TTL and latency
        ttl = None
        latency_ms = None
        link_type = "unknown"

        # Parse TTL from "ttl=64" pattern
        ttl_match = re.search(r"ttl=(\d+)", stdout_text, re.IGNORECASE)
        if ttl_match:
            ttl = int(ttl_match.group(1))
            # Determine link type based on TTL
            # Common default TTLs: Linux=64, Windows=128, Cisco=255
            # If TTL >= 64, likely direct; lower values suggest routing hops
            if ttl >= 64:
                link_type = "direct"
            else:
                link_type = "routed"

        # Parse latency from rtt summary or individual ping
        # Format: "rtt min/avg/max/mdev = 0.123/0.456/0.789/0.111 ms"
        rtt_match = re.search(r"rtt min/avg/max/mdev = [\d.]+/([\d.]+)/", stdout_text)
        if rtt_match:
            latency_ms = float(rtt_match.group(1))
        else:
            # Try to get from individual ping line: "time=0.123 ms"
            time_match = re.search(r"time=([\d.]+)\s*ms", stdout_text)
            if time_match:
                latency_ms = float(time_match.group(1))

        logger.info(
            f"MTU test to {target_ip}: success, "
            f"mtu={mtu}, ttl={ttl}, latency={latency_ms}ms, type={link_type}"
        )

        return MtuTestResponse(
            success=True,
            tested_mtu=mtu,
            link_type=link_type,
            latency_ms=latency_ms,
            ttl=ttl,
        )

    except asyncio.TimeoutError:
        return MtuTestResponse(
            success=False,
            error="Ping timed out",
        )
    except Exception as e:
        logger.error(f"MTU test failed: {e}")
        return MtuTestResponse(
            success=False,
            error=str(e),
        )


@router.post("/labs/{lab_id}/external/connect")
async def connect_to_external(
    lab_id: str,
    request: ExternalConnectRequest,
) -> ExternalConnectResponse:
    """Connect a container interface to an external network.

    This establishes connectivity between a container interface and an
    external host interface (e.g., for internet access, management network,
    or physical lab equipment).

    Args:
        lab_id: Lab identifier
        request: Connection request with container/interface and external interface

    Returns:
        ExternalConnectResponse with VLAN tag or error
    """
    if not settings.enable_ovs:
        return ExternalConnectResponse(
            success=False,
            error="OVS networking not enabled on this agent",
        )

    logger.info(
        f"External connect request: lab={lab_id}, "
        f"node={request.node_name}, interface={request.interface_name}, "
        f"external={request.external_interface}"
    )

    try:
        backend = get_network_backend()
        await backend.ensure_ovs_initialized()

        # Resolve container name
        if request.container_name:
            container_name = request.container_name
        elif request.node_name:
            provider = get_provider_for_request()
            container_name = provider.get_container_name(lab_id, request.node_name)
        else:
            return ExternalConnectResponse(
                success=False,
                error="Either container_name or node_name must be provided",
            )

        # Connect to external network
        vlan_tag = await backend.connect_to_external(
            container_name=container_name,
            interface_name=request.interface_name,
            external_interface=request.external_interface,
            vlan_tag=request.vlan_tag,
        )

        return ExternalConnectResponse(
            success=True,
            vlan_tag=vlan_tag,
        )

    except Exception as e:
        logger.error(f"External connect failed: {e}")
        return ExternalConnectResponse(
            success=False,
            error=str(e),
        )


@router.post("/ovs/patch")
async def create_bridge_patch(request: BridgePatchRequest) -> BridgePatchResponse:
    """Create a patch connection to another OVS or Linux bridge.

    This establishes connectivity between the arch-ovs bridge and another
    bridge (e.g., libvirt virbr0, Docker bridge, or physical bridge).

    Args:
        request: Patch request with target bridge name and optional VLAN

    Returns:
        BridgePatchResponse with patch port name or error
    """
    if not settings.enable_ovs:
        return BridgePatchResponse(
            success=False,
            error="OVS networking not enabled on this agent",
        )

    logger.info(f"Bridge patch request: target={request.target_bridge}")

    try:
        backend = get_network_backend()
        await backend.ensure_ovs_initialized()

        patch_port = await backend.create_patch_to_bridge(
            target_bridge=request.target_bridge,
            vlan_tag=request.vlan_tag,
        )

        return BridgePatchResponse(
            success=True,
            patch_port=patch_port,
        )

    except Exception as e:
        logger.error(f"Bridge patch failed: {e}")
        return BridgePatchResponse(
            success=False,
            error=str(e),
        )


@router.delete("/ovs/patch")
async def delete_bridge_patch(request: BridgeDeletePatchRequest) -> BridgeDeletePatchResponse:
    """Delete a patch connection to another bridge.

    This removes connectivity between the arch-ovs bridge and another bridge.

    Args:
        request: Delete request with target bridge name

    Returns:
        BridgeDeletePatchResponse with success status
    """
    if not settings.enable_ovs:
        return BridgeDeletePatchResponse(
            success=False,
            error="OVS networking not enabled on this agent",
        )

    logger.info(f"Bridge patch delete request: target={request.target_bridge}")

    try:
        backend = get_network_backend()
        if not backend.ovs_initialized():
            return BridgeDeletePatchResponse(
                success=False,
                error="OVS not initialized",
            )

        success = await backend.delete_patch_to_bridge(request.target_bridge)
        return BridgeDeletePatchResponse(success=success)

    except Exception as e:
        logger.error(f"Bridge patch delete failed: {e}")
        return BridgeDeletePatchResponse(
            success=False,
            error=str(e),
        )


@router.post("/labs/{lab_id}/external/disconnect")
async def disconnect_from_external(
    lab_id: str,
    request: ExternalDisconnectRequest,
) -> ExternalDisconnectResponse:
    """Disconnect an external network interface.

    This detaches an external host interface from the OVS bridge,
    breaking connectivity to any container interfaces that were connected.

    Args:
        lab_id: Lab identifier
        request: Disconnect request with external interface name

    Returns:
        ExternalDisconnectResponse with success status
    """
    if not settings.enable_ovs:
        return ExternalDisconnectResponse(
            success=False,
            error="OVS networking not enabled on this agent",
        )

    logger.info(f"External disconnect request: lab={lab_id}, interface={request.external_interface}")

    try:
        backend = get_network_backend()
        if not backend.ovs_initialized():
            return ExternalDisconnectResponse(
                success=False,
                error="OVS not initialized",
            )

        success = await backend.detach_external_interface(request.external_interface)
        return ExternalDisconnectResponse(success=success)

    except Exception as e:
        logger.error(f"External disconnect failed: {e}")
        return ExternalDisconnectResponse(
            success=False,
            error=str(e),
        )


@router.get("/labs/{lab_id}/external")
async def list_external_connections(lab_id: str) -> ExternalListResponse:
    """List all external network connections.

    Returns all external interfaces attached to the OVS bridge and their
    connected container interfaces.

    Args:
        lab_id: Lab identifier (used for filtering, currently returns all)

    Returns:
        ExternalListResponse with list of external connections
    """
    if not settings.enable_ovs:
        return ExternalListResponse(connections=[])

    try:
        backend = get_network_backend()
        if not backend.ovs_initialized():
            return ExternalListResponse(connections=[])

        connections_data = await backend.list_external_connections()

        connections = [
            ExternalConnectionInfo(
                external_interface=c["external_interface"],
                vlan_tag=c["vlan_tag"],
                connected_ports=c["connected_ports"],
            )
            for c in connections_data
        ]

        return ExternalListResponse(connections=connections)

    except Exception as e:
        logger.error(f"List external connections failed: {e}")
        return ExternalListResponse(connections=[])
