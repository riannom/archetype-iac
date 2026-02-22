"""Network interface carrier, isolation, VLAN, MTU, and provisioning endpoints."""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter

from agent.config import settings
from agent.helpers import (
    get_provider_for_request,
    _get_docker_ovs_plugin,
    _interface_name_to_index,
    _resolve_ovs_port,
    _resolve_ovs_port_via_ifindex,
)
from agent.providers import get_provider
from agent.schemas import (
    CarrierStateRequest,
    CarrierStateResponse,
    InterfaceDetail,
    InterfaceDetailsResponse,
    InterfaceProvisionRequest,
    InterfaceProvisionResponse,
    PortIsolateResponse,
    PortRestoreRequest,
    PortRestoreResponse,
    PortVlanResponse,
    SetMtuRequest,
    SetMtuResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["interfaces"])


@router.post("/labs/{lab_id}/interfaces/{node}/{interface}/carrier")
async def set_interface_carrier(
    lab_id: str,
    node: str,
    interface: str,
    request: CarrierStateRequest,
) -> CarrierStateResponse:
    """Set the carrier state of a container or VM interface.

    Uses ``ip link set carrier on/off`` to simulate physical link up/down.
    Supports Docker containers (nsenter into namespace) and libvirt VMs
    (host-side tap interface).
    """
    logger.info(f"Set carrier {request.state}: lab={lab_id}, node={node}, interface={interface}")

    state = request.state

    # Check if this is a libvirt VM node
    libvirt_provider = get_provider("libvirt")
    is_libvirt_node = False
    if libvirt_provider is not None:
        try:
            is_libvirt_node = libvirt_provider.get_node_kind(lab_id, node) is not None
        except Exception:
            is_libvirt_node = False

    try:
        if is_libvirt_node:
            # Libvirt VM: set carrier on the host-side tap interface
            iface_index = _interface_name_to_index(interface)
            tap_name = await libvirt_provider.get_vm_interface_port(
                lab_id, node, iface_index,
            )
            if not tap_name:
                return CarrierStateResponse(
                    success=False, container=node, interface=interface,
                    state=state,
                    error=f"Cannot find tap interface for {node}:{interface}",
                )
            proc = await asyncio.create_subprocess_exec(
                "ip", "link", "set", tap_name, "carrier", state,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr_bytes = await proc.communicate()
            success = proc.returncode == 0
            if not success:
                err = stderr_bytes.decode(errors="replace").strip()
                logger.error(f"Failed to set carrier on tap {tap_name}: {err}")
                return CarrierStateResponse(
                    success=False, container=node, interface=interface,
                    state=state, error=f"ip link set carrier failed: {err}",
                )
            logger.info(f"Set carrier {state} on VM tap {tap_name} ({node}:{interface})")
            return CarrierStateResponse(
                success=True, container=node, interface=interface, state=state,
            )
        else:
            # Docker container: use OVS plugin nsenter approach
            if not settings.enable_ovs_plugin:
                return CarrierStateResponse(
                    success=False, container=node, interface=interface,
                    state=state, error="OVS plugin not enabled",
                )
            plugin = _get_docker_ovs_plugin()
            provider = get_provider_for_request()
            container_name = provider.get_container_name(lab_id, node)
            success = await plugin.set_carrier_state(
                lab_id, container_name, interface, state,
            )
            return CarrierStateResponse(
                success=success, container=container_name,
                interface=interface, state=state,
                error=None if success else "Failed to set carrier state",
            )

    except Exception as e:
        logger.error(f"Set carrier state failed: {e}")
        return CarrierStateResponse(
            success=False, container=node, interface=interface,
            state=state, error=str(e),
        )


@router.post("/labs/{lab_id}/interfaces/{node}/{interface}/isolate")
async def isolate_interface(
    lab_id: str,
    node: str,
    interface: str,
) -> PortIsolateResponse:
    """Isolate a container interface from its L2 domain.

    This assigns the interface a unique VLAN tag and sets carrier off,
    effectively disconnecting it from any other interface.

    Args:
        lab_id: Lab identifier
        node: Node name (container name or node name)
        interface: Interface name in the container

    Returns:
        PortIsolateResponse with new VLAN tag
    """
    if not settings.enable_ovs_plugin:
        return PortIsolateResponse(
            success=False,
            container=node,
            interface=interface,
            error="OVS plugin not enabled",
        )

    logger.info(f"Isolate port: lab={lab_id}, node={node}, interface={interface}")

    try:
        plugin = _get_docker_ovs_plugin()

        # Resolve container name
        provider = get_provider_for_request()
        container_name = provider.get_container_name(lab_id, node)

        vlan_tag = await plugin.isolate_port(lab_id, container_name, interface)

        return PortIsolateResponse(
            success=vlan_tag is not None,
            container=container_name,
            interface=interface,
            vlan_tag=vlan_tag,
            error=None if vlan_tag is not None else "Failed to isolate port",
        )

    except Exception as e:
        logger.error(f"Port isolation failed: {e}")
        return PortIsolateResponse(
            success=False,
            container=node,
            interface=interface,
            error=str(e),
        )


@router.post("/labs/{lab_id}/interfaces/{node}/{interface}/restore")
async def restore_interface(
    lab_id: str,
    node: str,
    interface: str,
    request: PortRestoreRequest,
) -> PortRestoreResponse:
    """Restore a container interface to a specific VLAN and enable carrier.

    This reconnects the interface to the specified L2 domain (VLAN) and
    simulates physical link restoration.

    Args:
        lab_id: Lab identifier
        node: Node name (container name or node name)
        interface: Interface name in the container
        request: Contains target VLAN to restore to

    Returns:
        PortRestoreResponse with success status
    """
    if not settings.enable_ovs_plugin:
        return PortRestoreResponse(
            success=False,
            container=node,
            interface=interface,
            vlan_tag=request.target_vlan,
            error="OVS plugin not enabled",
        )

    logger.info(f"Restore port: lab={lab_id}, node={node}, interface={interface}, vlan={request.target_vlan}")

    try:
        plugin = _get_docker_ovs_plugin()

        # Resolve container name
        provider = get_provider_for_request()
        container_name = provider.get_container_name(lab_id, node)

        success = await plugin.restore_port(lab_id, container_name, interface, request.target_vlan)

        return PortRestoreResponse(
            success=success,
            container=container_name,
            interface=interface,
            vlan_tag=request.target_vlan,
            error=None if success else "Failed to restore port",
        )

    except Exception as e:
        logger.error(f"Port restore failed: {e}")
        return PortRestoreResponse(
            success=False,
            container=node,
            interface=interface,
            vlan_tag=request.target_vlan,
            error=str(e),
        )


@router.get("/labs/{lab_id}/interfaces/{node}/{interface}/vlan")
async def get_interface_vlan(
    lab_id: str,
    node: str,
    interface: str,
    read_from_ovs: bool = False,
) -> PortVlanResponse:
    """Get the current VLAN tag for a node interface.

    Supports both Docker containers and libvirt VMs. Tries the Docker
    OVS plugin first (fast in-memory lookup), then falls back to the
    provider-agnostic _resolve_ovs_port() which also handles libvirt.

    Args:
        lab_id: Lab identifier
        node: Node name (container name or node name)
        interface: Interface name in the node
        read_from_ovs: If True, read directly from OVS instead of in-memory state.
                       Use this for verification to get ground truth.

    Returns:
        PortVlanResponse with current VLAN tag
    """
    if not settings.enable_ovs_plugin:
        return PortVlanResponse(
            container=node,
            interface=interface,
            error="OVS plugin not enabled",
        )

    try:
        plugin = _get_docker_ovs_plugin()

        # Identify libvirt nodes first to avoid Docker discovery noise for VM nodes.
        libvirt_provider = get_provider("libvirt")
        is_libvirt_node = False
        if libvirt_provider is not None:
            try:
                is_libvirt_node = libvirt_provider.get_node_kind(lab_id, node) is not None
            except Exception:
                is_libvirt_node = False

        docker_provider = get_provider("docker")

        # When read_from_ovs is requested (verification), use ifindex-based
        # resolution to avoid the Docker plugin's port swap bug. The plugin
        # can return the wrong host_veth after container restart.
        if read_from_ovs and docker_provider is not None and not is_libvirt_node:
            container_name = docker_provider.get_container_name(lab_id, node)
            resolved = await _resolve_ovs_port_via_ifindex(container_name, interface)
            if resolved:
                return PortVlanResponse(
                    container=container_name,
                    interface=interface,
                    vlan_tag=resolved[1],
                )

        # Fast path: Docker plugin in-memory lookup (non-verification reads).
        if docker_provider is not None and not is_libvirt_node:
            container_name = docker_provider.get_container_name(lab_id, node)
            vlan_tag = await plugin.get_endpoint_vlan(
                lab_id, container_name, interface, read_from_ovs=read_from_ovs
            )
            if vlan_tag is not None:
                return PortVlanResponse(
                    container=container_name,
                    interface=interface,
                    vlan_tag=vlan_tag,
                )

        # Fall back to provider-agnostic resolution (handles libvirt VMs)
        port_info = await _resolve_ovs_port(lab_id, node, interface)
        if port_info is not None:
            vlan_tag = port_info.vlan_tag
            # If read_from_ovs requested, read the actual tag from OVS
            if read_from_ovs:
                _ovs_proc = await asyncio.create_subprocess_exec(
                    "ovs-vsctl", "get", "port", port_info.port_name, "tag",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                _ovs_out, _ = await _ovs_proc.communicate()
                if _ovs_proc.returncode == 0:
                    tag_str = _ovs_out.decode().strip().strip("[]")
                    if tag_str.isdigit():
                        vlan_tag = int(tag_str)
            return PortVlanResponse(
                container=node,
                interface=interface,
                vlan_tag=vlan_tag,
            )

        return PortVlanResponse(
            container=node,
            interface=interface,
            error="Endpoint not found",
        )

    except Exception as e:
        logger.error(f"Get VLAN failed: {e}")
        return PortVlanResponse(
            container=node,
            interface=interface,
            error=str(e),
        )


# --- Network Interface Discovery Endpoints ---

@router.get("/interfaces")
async def list_interfaces() -> dict:
    """List available network interfaces on this host.

    Returns physical interfaces that can be used for VLAN sub-interfaces
    or external network connections.
    """
    import subprocess

    def _sync_list_interfaces() -> dict:
        interfaces = []
        try:
            # Get list of interfaces using ip command
            result = subprocess.run(
                ["ip", "-j", "link", "show"],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode == 0:
                link_data = json.loads(result.stdout)

                for link in link_data:
                    name = link.get("ifname", "")
                    # Skip loopback, docker, and veth interfaces
                    if name in ("lo",) or name.startswith(("docker", "veth", "br-", "clab")):
                        continue

                    # Get interface state and type
                    operstate = link.get("operstate", "unknown")
                    link_type = link.get("link_type", "")

                    # Get IP addresses for this interface
                    addr_result = subprocess.run(
                        ["ip", "-j", "addr", "show", name],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    ipv4_addresses = []
                    if addr_result.returncode == 0:
                        addr_data = json.loads(addr_result.stdout)
                        for iface in addr_data:
                            for addr_info in iface.get("addr_info", []):
                                if addr_info.get("family") == "inet":
                                    ipv4_addresses.append(f"{addr_info['local']}/{addr_info.get('prefixlen', 24)}")

                    interfaces.append({
                        "name": name,
                        "state": operstate,
                        "type": link_type,
                        "ipv4_addresses": ipv4_addresses,
                        "mac": link.get("address"),
                        # Indicate if this is a VLAN sub-interface
                        "is_vlan": "." in name,
                    })

        except Exception as e:
            logger.error(f"Error listing interfaces: {e}")
            return {"interfaces": [], "error": str(e)}

        return {"interfaces": interfaces}

    return await asyncio.to_thread(_sync_list_interfaces)


@router.get("/interfaces/details")
async def get_interface_details() -> InterfaceDetailsResponse:
    """Get detailed interface info including MTU and default route detection.

    Returns all physical interfaces with their current MTU, identifies the
    default route interface, and detects which network manager is in use.
    """
    import json as json_module
    import subprocess

    from agent.network.interface_config import (
        detect_network_manager,
        get_default_route_interface,
        get_interface_mtu,
        is_physical_interface,
    )

    def _sync_get_details() -> InterfaceDetailsResponse:
        interfaces: list[InterfaceDetail] = []
        default_route_iface = get_default_route_interface()
        network_mgr = detect_network_manager()

        try:
            # Get list of all interfaces
            result = subprocess.run(
                ["ip", "-j", "link", "show"],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode == 0:
                link_data = json_module.loads(result.stdout)

                for link in link_data:
                    name = link.get("ifname", "")
                    if not name or name == "lo":
                        continue

                    # Check if physical
                    is_physical = is_physical_interface(name)

                    # Get MTU
                    mtu = get_interface_mtu(name) or link.get("mtu", 1500)

                    # Get state
                    operstate = link.get("operstate", "unknown")

                    # Get MAC address
                    mac = link.get("address")

                    # Get IP addresses
                    addr_result = subprocess.run(
                        ["ip", "-j", "addr", "show", name],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    ipv4_addresses = []
                    if addr_result.returncode == 0:
                        addr_data = json_module.loads(addr_result.stdout)
                        for iface in addr_data:
                            for addr_info in iface.get("addr_info", []):
                                if addr_info.get("family") == "inet":
                                    ipv4_addresses.append(
                                        f"{addr_info['local']}/{addr_info.get('prefixlen', 24)}"
                                    )

                    interfaces.append(InterfaceDetail(
                        name=name,
                        mtu=mtu,
                        is_physical=is_physical,
                        is_default_route=(name == default_route_iface),
                        mac=mac,
                        ipv4_addresses=ipv4_addresses,
                        state=operstate,
                    ))

        except Exception as e:
            logger.error(f"Error getting interface details: {e}")

        return InterfaceDetailsResponse(
            interfaces=interfaces,
            default_route_interface=default_route_iface,
            network_manager=network_mgr,
        )

    return await asyncio.to_thread(_sync_get_details)


@router.post("/interfaces/{interface_name}/mtu")
async def set_interface_mtu(interface_name: str, request: SetMtuRequest) -> SetMtuResponse:
    """Set MTU on a physical interface with optional persistence.

    Args:
        interface_name: Name of the interface to configure
        request: MTU value and persistence settings

    Returns:
        Result of the MTU configuration operation
    """
    from agent.network.interface_config import (
        detect_network_manager,
        get_interface_mtu,
        is_physical_interface,
        set_mtu_persistent,
        set_mtu_runtime,
    )

    # Validate interface and detect network manager in a thread
    # (get_interface_mtu/is_physical_interface are sysfs reads,
    #  detect_network_manager uses subprocess.run -- all blocking)
    def _sync_validate():
        mtu = get_interface_mtu(interface_name)
        is_physical = is_physical_interface(interface_name)
        net_mgr = detect_network_manager()
        return mtu, is_physical, net_mgr

    previous_mtu, is_physical, network_mgr = await asyncio.to_thread(_sync_validate)

    if previous_mtu is None:
        return SetMtuResponse(
            success=False,
            interface=interface_name,
            previous_mtu=0,
            new_mtu=request.mtu,
            error=f"Interface {interface_name} not found",
        )

    if not is_physical:
        logger.warning(f"Setting MTU on non-physical interface {interface_name}")

    # Apply runtime MTU first
    success, error = await set_mtu_runtime(interface_name, request.mtu)
    if not success:
        return SetMtuResponse(
            success=False,
            interface=interface_name,
            previous_mtu=previous_mtu,
            new_mtu=request.mtu,
            network_manager=network_mgr,
            error=error,
        )

    # Persist if requested
    persisted = False
    persist_error = None
    if request.persist:
        if network_mgr == "unknown":
            persist_error = "Cannot persist: unknown network manager"
            logger.warning(f"MTU set on {interface_name} but persistence unavailable: {persist_error}")
        else:
            persisted, persist_error = await set_mtu_persistent(interface_name, request.mtu, network_mgr)
            if not persisted:
                logger.warning(f"MTU set on {interface_name} but persistence failed: {persist_error}")

    # Verify the MTU was applied (sysfs read, fast but keep consistent)
    new_mtu = await asyncio.to_thread(get_interface_mtu, interface_name) or request.mtu

    return SetMtuResponse(
        success=True,
        interface=interface_name,
        previous_mtu=previous_mtu,
        new_mtu=new_mtu,
        persisted=persisted,
        network_manager=network_mgr,
        error=persist_error if not persisted and request.persist else None,
    )


@router.get("/bridges")
async def list_bridges() -> dict:
    """List available Linux bridges on this host.

    Returns bridges that can be used for external network connections.
    """
    import subprocess

    def _sync_list_bridges() -> dict:
        bridges = []
        try:
            # Get list of bridges using bridge command
            result = subprocess.run(
                ["bridge", "-j", "link", "show"],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode == 0:
                import json
                bridge_data = json.loads(result.stdout)

                # Extract unique bridge names (master field)
                seen_bridges = set()
                for link in bridge_data:
                    master = link.get("master")
                    if master and master not in seen_bridges:
                        seen_bridges.add(master)

                # Get details for each bridge
                for bridge_name in sorted(seen_bridges):
                    # Skip docker-managed bridges
                    if bridge_name.startswith(("docker", "br-")):
                        continue

                    bridge_info = {"name": bridge_name, "interfaces": []}

                    # Get interfaces attached to this bridge
                    for link in bridge_data:
                        if link.get("master") == bridge_name:
                            bridge_info["interfaces"].append(link.get("ifname"))

                    bridges.append(bridge_info)

        except FileNotFoundError:
            # bridge command not available, try ip command
            try:
                result = subprocess.run(
                    ["ip", "-j", "link", "show", "type", "bridge"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )

                if result.returncode == 0:
                    import json
                    link_data = json.loads(result.stdout)

                    for link in link_data:
                        name = link.get("ifname", "")
                        # Skip docker-managed bridges
                        if name.startswith(("docker", "br-")):
                            continue

                        bridges.append({
                            "name": name,
                            "state": link.get("operstate", "unknown"),
                            "interfaces": [],  # Would need additional queries
                        })

            except Exception as e:
                logger.error(f"Error listing bridges: {e}")
                return {"bridges": [], "error": str(e)}

        except Exception as e:
            logger.error(f"Error listing bridges: {e}")
            return {"bridges": [], "error": str(e)}

        return {"bridges": bridges}

    return await asyncio.to_thread(_sync_list_bridges)


# --- Interface Provisioning ---

@router.post("/interfaces/provision")
async def provision_interface(
    request: InterfaceProvisionRequest,
) -> InterfaceProvisionResponse:
    """Provision, configure, or delete a network interface on this host.

    Shared plumbing for transport subinterfaces and external connectivity.

    Actions:
    - create_subinterface: Create a VLAN subinterface on a parent interface
    - configure: Set MTU/IP on an existing interface
    - delete: Remove an interface (and detach from OVS if attached)
    """
    import subprocess

    async def run_cmd(cmd: list[str], check: bool = False) -> tuple[int, str, str]:
        proc = await asyncio.get_event_loop().run_in_executor(
            None, lambda: subprocess.run(cmd, capture_output=True, text=True)
        )
        return proc.returncode, proc.stdout, proc.stderr

    try:
        if request.action == "create_subinterface":
            if not request.parent_interface:
                return InterfaceProvisionResponse(
                    success=False, error="parent_interface is required for create_subinterface"
                )
            if request.vlan_id is None:
                return InterfaceProvisionResponse(
                    success=False, error="vlan_id is required for create_subinterface"
                )

            iface_name = request.name or f"{request.parent_interface}.{request.vlan_id}"
            mtu = request.mtu or 9000

            # Check if interface already exists
            rc, _, _ = await run_cmd(["ip", "link", "show", iface_name])
            if rc == 0:
                # Interface exists - verify config and update if needed
                logger.info(f"Interface {iface_name} already exists, updating config")
            else:
                # Create VLAN subinterface
                rc, _, stderr = await run_cmd([
                    "ip", "link", "add", "link", request.parent_interface,
                    "name", iface_name, "type", "vlan", "id", str(request.vlan_id),
                ])
                if rc != 0:
                    return InterfaceProvisionResponse(
                        success=False, error=f"Failed to create subinterface: {stderr.strip()}"
                    )

            # Bump parent MTU if needed (parent must be >= child)
            rc, stdout, _ = await run_cmd(["cat", f"/sys/class/net/{request.parent_interface}/mtu"])
            if rc == 0:
                parent_mtu = int(stdout.strip())
                if parent_mtu < mtu:
                    logger.info(f"Bumping parent {request.parent_interface} MTU from {parent_mtu} to {mtu}")
                    await run_cmd(["ip", "link", "set", request.parent_interface, "mtu", str(mtu)])

            # Set MTU on subinterface
            await run_cmd(["ip", "link", "set", iface_name, "mtu", str(mtu)])

            # Set IP if provided
            if request.ip_cidr:
                # Flush existing IPs and set new one
                await run_cmd(["ip", "addr", "flush", "dev", iface_name])
                rc, _, stderr = await run_cmd(["ip", "addr", "add", request.ip_cidr, "dev", iface_name])
                if rc != 0:
                    return InterfaceProvisionResponse(
                        success=False,
                        interface_name=iface_name,
                        error=f"Failed to set IP: {stderr.strip()}",
                    )

            # Bring interface up
            await run_cmd(["ip", "link", "set", iface_name, "up"])

            # Attach to OVS if requested
            if request.attach_to_ovs:
                bridge = settings.ovs_bridge_name
                cmd = ["ovs-vsctl", "--may-exist", "add-port", bridge, iface_name]
                if request.ovs_vlan_tag is not None:
                    cmd.extend(["tag=" + str(request.ovs_vlan_tag)])
                rc, _, stderr = await run_cmd(cmd)
                if rc != 0:
                    logger.warning(f"Failed to attach {iface_name} to OVS: {stderr.strip()}")

            # Read back actual MTU
            rc, stdout, _ = await run_cmd(["cat", f"/sys/class/net/{iface_name}/mtu"])
            actual_mtu = int(stdout.strip()) if rc == 0 else mtu

            # Read back IP
            actual_ip = request.ip_cidr
            if not actual_ip:
                rc, stdout, _ = await run_cmd(["ip", "-4", "addr", "show", iface_name])
                if rc == 0 and "inet " in stdout:
                    # Parse first inet line
                    for line in stdout.split("\n"):
                        line = line.strip()
                        if line.startswith("inet "):
                            actual_ip = line.split()[1]
                            break

            return InterfaceProvisionResponse(
                success=True,
                interface_name=iface_name,
                mtu=actual_mtu,
                ip_address=actual_ip,
            )

        elif request.action == "configure":
            if not request.name:
                return InterfaceProvisionResponse(
                    success=False, error="name is required for configure action"
                )

            iface_name = request.name

            # Verify interface exists
            rc, _, _ = await run_cmd(["ip", "link", "show", iface_name])
            if rc != 0:
                return InterfaceProvisionResponse(
                    success=False, error=f"Interface {iface_name} does not exist"
                )

            # Set MTU if provided
            if request.mtu:
                await run_cmd(["ip", "link", "set", iface_name, "mtu", str(request.mtu)])

            # Set IP if provided
            if request.ip_cidr:
                await run_cmd(["ip", "addr", "flush", "dev", iface_name])
                await run_cmd(["ip", "addr", "add", request.ip_cidr, "dev", iface_name])

            # Bring up
            await run_cmd(["ip", "link", "set", iface_name, "up"])

            rc, stdout, _ = await run_cmd(["cat", f"/sys/class/net/{iface_name}/mtu"])
            actual_mtu = int(stdout.strip()) if rc == 0 else request.mtu

            return InterfaceProvisionResponse(
                success=True,
                interface_name=iface_name,
                mtu=actual_mtu,
                ip_address=request.ip_cidr,
            )

        elif request.action == "delete":
            if not request.name:
                return InterfaceProvisionResponse(
                    success=False, error="name is required for delete action"
                )

            iface_name = request.name

            # Remove from OVS if attached
            rc, stdout, _ = await run_cmd(["ovs-vsctl", "port-to-br", iface_name])
            if rc == 0:
                bridge = stdout.strip()
                await run_cmd(["ovs-vsctl", "--if-exists", "del-port", bridge, iface_name])

            # Delete the interface
            rc, _, stderr = await run_cmd(["ip", "link", "delete", iface_name])
            if rc != 0:
                return InterfaceProvisionResponse(
                    success=False, error=f"Failed to delete interface: {stderr.strip()}"
                )

            return InterfaceProvisionResponse(
                success=True,
                interface_name=iface_name,
            )

        else:
            return InterfaceProvisionResponse(
                success=False, error=f"Unknown action: {request.action}"
            )

    except Exception as e:
        logger.error(f"Interface provisioning failed: {e}")
        return InterfaceProvisionResponse(success=False, error=str(e))
