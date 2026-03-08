"""OVS port provisioning helpers.

Standalone functions for provisioning veth pairs, moving them into container
namespaces, detecting stale ports, and handling container restarts.  These
were originally methods on ``OVSNetworkManager`` and are called from there
via thin delegation wrappers so that external callers are unaffected.

All functions receive the *manager* instance (or its components) as an
explicit first argument to avoid import cycles.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from pathlib import Path
from typing import TYPE_CHECKING, Any

import docker
from docker.errors import NotFound

from agent.config import settings

if TYPE_CHECKING:
    from agent.network.ovs import OVSNetworkManager, OVSPort

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PID lookup
# ---------------------------------------------------------------------------

async def get_container_pid(
    docker_client: docker.DockerClient,
    container_name: str,
) -> int | None:
    """Get the PID of a container's init process.

    Args:
        docker_client: Docker client instance
        container_name: Docker container name

    Returns:
        PID if container is running, None otherwise
    """
    def _sync_get_pid() -> int | None:
        try:
            container = docker_client.containers.get(container_name)
            if container.status != "running":
                logger.warning(f"Container {container_name} is not running")
                return None
            pid = container.attrs["State"]["Pid"]
            if not pid:
                logger.warning(f"Could not get PID for container {container_name}")
                return None
            return pid
        except NotFound:
            logger.warning(f"Container {container_name} not found")
            return None
        except Exception as e:
            logger.error(f"Error getting container PID: {e}")
            return None

    return await asyncio.to_thread(_sync_get_pid)


# ---------------------------------------------------------------------------
# Port name generation
# ---------------------------------------------------------------------------

def generate_port_name(container_name: str, interface_name: str) -> str:
    """Generate OVS port name for a container interface.

    Port names are limited to 15 characters (Linux interface name limit).
    Format: vh-{container_suffix}-{iface}

    Args:
        container_name: Docker container name
        interface_name: Interface name inside container

    Returns:
        Port name (max 15 chars)
    """
    # Extract last part of container name (node name)
    parts = container_name.split("-")
    node_suffix = parts[-1][:4] if parts else container_name[:4]

    # Simplify interface name (eth1 -> e1, Ethernet1 -> E1)
    iface_short = interface_name.replace("Ethernet", "E").replace("eth", "e")[:3]

    # Add random suffix for uniqueness
    suffix = secrets.token_hex(2)

    # vh-{node}-{iface}-{rand} = 2 + 1 + 4 + 1 + 3 + 1 + 4 = 16
    # Trim to fit 15 chars
    port_name = f"vh{node_suffix}{iface_short}{suffix}"[:15]
    return port_name


# ---------------------------------------------------------------------------
# Existing-state discovery (agent restart)
# ---------------------------------------------------------------------------

async def discover_existing_state(mgr: OVSNetworkManager) -> None:
    """Discover existing OVS ports and rebuild internal state.

    Called on initialization when bridge already exists (agent restart).
    This ensures we don't try to re-create ports that already exist.

    Uses batched OVS queries and direct sysfs reads to avoid spawning
    hundreds of subprocesses (which previously took ~60s on restart).
    """
    from agent.network.ovs import OVSLink, OVSPort  # noqa: F811 — local import to avoid circular

    # List all ports on the bridge
    code, stdout, _ = await mgr._ovs_vsctl("list-ports", mgr._bridge_name)
    if code != 0 or not stdout.strip():
        return

    ports = stdout.strip().split("\n")
    vh_ports = [p for p in ports if p.startswith("vh")]
    if not vh_ports:
        return

    discovered_count = 0

    # --- Batch 1: Get all port VLAN tags in one OVS call ---
    port_tags: dict[str, int] = {}
    code, json_out, _ = await mgr._ovs_vsctl(
        "--format=json", "--", "--columns=name,tag", "list", "Port",
    )
    if code == 0 and json_out.strip():
        try:
            data = json.loads(json_out)
            for row in data.get("data", []):
                # Row format: [name, tag] where tag is int or ["set", []]
                name = row[0]
                tag = row[1]
                if isinstance(tag, int) and tag > 0:
                    port_tags[name] = tag
        except (json.JSONDecodeError, IndexError, TypeError) as e:
            logger.debug(f"Failed to parse batch port tags: {e}")

    # --- Batch 2: Read ifindex from sysfs directly (no subprocess) ---
    def _read_ifindexes(port_names: list[str]) -> dict[str, str]:
        """Read ifindex for each port from /sys/class/net/{port}/ifindex."""
        result = {}
        for name in port_names:
            try:
                idx = Path(f"/sys/class/net/{name}/ifindex").read_text().strip()
                if idx:
                    result[name] = idx
            except (OSError, ValueError):
                pass
        return result

    port_ifindexes = await asyncio.to_thread(
        _read_ifindexes, [p for p in vh_ports if p in port_tags]
    )

    # --- Build container interface map (same as before) ---
    container_ifindex_map: dict[str, tuple[str, str, str, str | None]] = {}
    try:
        def _get_containers():
            return mgr.docker.containers.list(
                filters={"label": "archetype.lab_id"}
            )

        containers = await asyncio.to_thread(_get_containers)
        for container in containers:
            pid = container.attrs.get("State", {}).get("Pid")
            if not pid:
                continue

            labels = container.labels or {}
            lab_id = labels.get("archetype.lab_id", "_unknown")
            node_name = labels.get("archetype.node_name")
            code, ns_stdout, _ = await mgr._run_cmd([
                "nsenter", "-t", str(pid), "-n",
                "ip", "-o", "link", "show",
            ])
            if code != 0:
                continue

            for line in ns_stdout.split("\n"):
                if not line.strip():
                    continue

                # Example: "2: eth1@if123: <...>"
                parts = line.split(":")
                if len(parts) < 2:
                    continue

                iface = parts[1].strip().split("@")[0]
                if iface in ("lo", "eth0"):
                    continue

                if "@if" not in parts[1]:
                    continue

                peer_idx = parts[1].split("@if")[1].split(":")[0].strip()
                if not peer_idx:
                    continue

                container_ifindex_map[peer_idx] = (container.name, iface, lab_id, node_name)
    except Exception as e:
        logger.debug(f"Error building container interface map: {e}")

    # --- Match ports to containers using pre-fetched data ---
    for port_name in vh_ports:
        vlan_tag = port_tags.get(port_name)
        if vlan_tag is None:
            continue

        ifindex = port_ifindexes.get(port_name)
        if not ifindex:
            continue

        match = container_ifindex_map.get(ifindex)
        if match:
            container_name, interface_name, lab_id, node_name = match
            port_key = f"{container_name}:{interface_name}"

            port = OVSPort(
                port_name=port_name,
                container_name=container_name,
                interface_name=interface_name,
                vlan_tag=vlan_tag,
                lab_id=lab_id,
                node_name=node_name,
            )
            mgr._ports[port_key] = port
            mgr._vlan_allocator._allocated[port_key] = vlan_tag
            discovered_count += 1

    if discovered_count > 0:
        logger.info(f"Discovered {discovered_count} existing OVS ports after restart")

    # Discover links by finding ports that share VLAN tags
    vlan_to_ports: dict[int, list[str]] = {}
    for key, port in mgr._ports.items():
        if port.vlan_tag not in vlan_to_ports:
            vlan_to_ports[port.vlan_tag] = []
        vlan_to_ports[port.vlan_tag].append(key)

    # Create link records for VLAN tags shared by exactly 2 ports
    for vlan_tag, port_keys in vlan_to_ports.items():
        if len(port_keys) == 2:
            port_a = mgr._ports[port_keys[0]]
            mgr._ports[port_keys[1]]
            link_id = f"{port_keys[0]}-{port_keys[1]}"
            link = OVSLink(
                link_id=link_id,
                lab_id=port_a.lab_id,
                port_a=port_keys[0],
                port_b=port_keys[1],
                vlan_tag=vlan_tag,
            )
            mgr._links[link.key] = link

    if mgr._links:
        logger.info(f"Discovered {len(mgr._links)} existing OVS links")


# ---------------------------------------------------------------------------
# Interface provisioning
# ---------------------------------------------------------------------------

async def provision_interface(
    mgr: OVSNetworkManager,
    container_name: str,
    interface_name: str,
    lab_id: str,
    node_name: str | None = None,
) -> int:
    """Create veth pair and attach to OVS with isolated VLAN tag.

    This provisions a real interface (not dummy) that can be hot-connected
    to other interfaces later. The interface starts isolated with a unique
    VLAN tag.

    Args:
        mgr: OVSNetworkManager instance
        container_name: Docker container name
        interface_name: Interface name inside container (e.g., "eth1")
        lab_id: Lab identifier for tracking
        node_name: Logical node name (optional, used for metadata tracking)

    Returns:
        Allocated VLAN tag

    Raises:
        RuntimeError: If provisioning fails
    """
    from agent.network.ovs import OVSPort  # noqa: F811

    if not mgr._initialized:
        await mgr.initialize()

    port_key = f"{container_name}:{interface_name}"

    # Check if already provisioned
    if port_key in mgr._ports:
        logger.debug(f"Interface already provisioned: {port_key}")
        return mgr._ports[port_key].vlan_tag

    # Get container PID
    pid = await get_container_pid(mgr.docker, container_name)
    if pid is None:
        raise RuntimeError(f"Container {container_name} is not running")

    # Generate port name
    port_name = generate_port_name(container_name, interface_name)

    # Allocate VLAN tag for isolation
    vlan_tag = mgr._vlan_allocator.allocate(port_key)

    # Create veth pair
    veth_cont = f"vc{secrets.token_hex(4)}"[:15]  # Container-side name (temporary)

    # Delete if exists (from previous run)
    if await mgr._ip_link_exists(port_name):
        await mgr._run_cmd(["ip", "link", "delete", port_name])

    try:
        # Create veth pair
        code, _, stderr = await mgr._run_cmd([
            "ip", "link", "add", port_name, "type", "veth", "peer", "name", veth_cont
        ])
        if code != 0:
            raise RuntimeError(f"Failed to create veth pair: {stderr}")

        # Set MTU on veth pair for jumbo frame support
        if settings.local_mtu > 0:
            await mgr._run_cmd([
                "ip", "link", "set", port_name, "mtu", str(settings.local_mtu)
            ])
            await mgr._run_cmd([
                "ip", "link", "set", veth_cont, "mtu", str(settings.local_mtu)
            ])

        # Add host-side to OVS bridge with VLAN tag
        code, _, stderr = await mgr._ovs_vsctl(
            "add-port", mgr._bridge_name, port_name,
            f"tag={vlan_tag}",
            "--", "set", "interface", port_name, "type=system"
        )
        if code != 0:
            await mgr._run_cmd(["ip", "link", "delete", port_name])
            raise RuntimeError(f"Failed to add port to OVS: {stderr}")

        # Bring host-side up
        await mgr._run_cmd(["ip", "link", "set", port_name, "up"])

        # Move container-side to container namespace
        code, _, stderr = await mgr._run_cmd([
            "ip", "link", "set", veth_cont, "netns", str(pid)
        ])
        if code != 0:
            await mgr._ovs_vsctl("del-port", mgr._bridge_name, port_name)
            await mgr._run_cmd(["ip", "link", "delete", port_name])
            raise RuntimeError(f"Failed to move veth to container: {stderr}")

        # Rename interface inside container
        code, _, stderr = await mgr._run_cmd([
            "nsenter", "-t", str(pid), "-n",
            "ip", "link", "set", veth_cont, "name", interface_name
        ])
        if code != 0:
            logger.warning(f"Failed to rename interface to {interface_name}: {stderr}")

        # Bring interface up inside container
        await mgr._run_cmd([
            "nsenter", "-t", str(pid), "-n",
            "ip", "link", "set", interface_name, "up"
        ])

        # Track the port
        port = OVSPort(
            port_name=port_name,
            container_name=container_name,
            interface_name=interface_name,
            vlan_tag=vlan_tag,
            lab_id=lab_id,
            node_name=node_name,
        )
        mgr._ports[port_key] = port

        logger.info(
            f"Provisioned {container_name}:{interface_name} -> "
            f"OVS port {port_name} (VLAN {vlan_tag})"
        )
        return vlan_tag

    except Exception as e:
        # Cleanup on failure
        mgr._vlan_allocator.release(port_key)
        try:
            await mgr._ovs_vsctl("del-port", mgr._bridge_name, port_name)
        except Exception:
            pass
        try:
            await mgr._run_cmd(["ip", "link", "delete", port_name])
        except Exception:
            pass
        raise RuntimeError(f"Failed to provision interface: {e}")


# ---------------------------------------------------------------------------
# Port deletion
# ---------------------------------------------------------------------------

async def delete_port(
    mgr: OVSNetworkManager,
    container_name: str,
    interface_name: str,
) -> bool:
    """Delete an OVS port and release resources.

    Args:
        mgr: OVSNetworkManager instance
        container_name: Container name
        interface_name: Interface name

    Returns:
        True if deleted successfully
    """
    key = f"{container_name}:{interface_name}"
    port = mgr._ports.get(key)

    if not port:
        logger.warning(f"Port not found for deletion: {key}")
        return False

    # Remove from OVS
    code, _, stderr = await mgr._ovs_vsctl(
        "--if-exists", "del-port", mgr._bridge_name, port.port_name
    )
    if code != 0:
        logger.warning(f"Failed to delete OVS port {port.port_name}: {stderr}")

    # Delete veth pair (removing one end deletes both)
    await mgr._run_cmd(["ip", "link", "delete", port.port_name])

    # Release VLAN
    mgr._vlan_allocator.release(key)

    # Remove from tracking
    del mgr._ports[key]

    # Remove any links involving this port
    links_to_remove = [
        link_key for link_key, link in mgr._links.items()
        if link.port_a == key or link.port_b == key
    ]
    for link_key in links_to_remove:
        del mgr._links[link_key]

    logger.info(f"Deleted port: {key}")
    return True


# ---------------------------------------------------------------------------
# Stale port detection
# ---------------------------------------------------------------------------

async def host_veth_peer_missing(port_name: str) -> bool:
    """Best-effort host-side check for a missing veth peer.

    When Docker PID lookup fails, we cannot enter the container namespace.
    As a fallback, read ifindex/iflink from sysfs:
    - iflink <= 0 or iflink == ifindex strongly indicates no live peer.
    Returns False when the check is inconclusive.
    """

    def _read_sysfs_peer_state() -> bool | None:
        try:
            ifindex = int(Path(f"/sys/class/net/{port_name}/ifindex").read_text().strip())
            iflink = int(Path(f"/sys/class/net/{port_name}/iflink").read_text().strip())
        except (FileNotFoundError, ValueError, OSError):
            return None

        if iflink <= 0:
            return True
        if iflink == ifindex:
            return True
        return False

    missing = await asyncio.to_thread(_read_sysfs_peer_state)
    return bool(missing) if missing is not None else False


async def is_port_stale(mgr: OVSNetworkManager, port: OVSPort) -> bool:
    """Check if an OVS port is stale (host-side exists but container peer missing).

    When a container restarts, its network namespace is recreated and the
    veth peer inside is destroyed. The host-side veth (attached to OVS)
    still exists but has no peer - this is a "stale" port.

    Args:
        mgr: OVSNetworkManager instance
        port: OVS port to check

    Returns:
        True if the port is stale (needs reprovisioning), False if healthy
    """
    # Check if host-side veth exists
    if not await mgr._ip_link_exists(port.port_name):
        # Host-side doesn't exist - not stale, just missing entirely
        return False

    # Get container PID
    pid = await get_container_pid(mgr.docker, port.container_name)
    if pid is None:
        # Fallback heuristic when container PID is unavailable.
        return await host_veth_peer_missing(port.port_name)

    # Check if interface exists inside container namespace
    code, stdout, _ = await mgr._run_cmd([
        "nsenter", "-t", str(pid), "-n",
        "ip", "link", "show", port.interface_name
    ])

    # If the interface doesn't exist inside container, port is stale
    return code != 0


async def cleanup_stale_port(mgr: OVSNetworkManager, port: OVSPort) -> None:
    """Remove a stale OVS port and release resources.

    This cleans up the host-side veth and OVS port entry for a port
    whose container-side peer no longer exists.

    Args:
        mgr: OVSNetworkManager instance
        port: OVS port to clean up
    """
    key = port.key

    # Remove from OVS bridge
    code, _, stderr = await mgr._ovs_vsctl(
        "--if-exists", "del-port", mgr._bridge_name, port.port_name
    )
    if code != 0:
        logger.warning(f"Failed to delete OVS port {port.port_name}: {stderr}")

    # Delete host-side veth (if it exists)
    if await mgr._ip_link_exists(port.port_name):
        await mgr._run_cmd(["ip", "link", "delete", port.port_name])

    # Release VLAN allocation
    mgr._vlan_allocator.release(key)

    # Remove from port tracking
    mgr._ports.pop(key, None)

    # Remove any links involving this port
    links_to_remove = [
        link_key for link_key, link in mgr._links.items()
        if link.port_a == key or link.port_b == key
    ]
    for link_key in links_to_remove:
        del mgr._links[link_key]

    logger.debug(f"Cleaned up stale port: {key}")


# ---------------------------------------------------------------------------
# Container restart handling
# ---------------------------------------------------------------------------

async def handle_container_restart(
    mgr: OVSNetworkManager,
    container_name: str,
    lab_id: str,
) -> dict[str, Any]:
    """Handle container restart by reprovisioning stale OVS interfaces.

    When a container restarts, its network namespace is recreated and veth
    peers inside are destroyed. This function:
    1. Finds all tracked ports for the container
    2. Checks which are stale (host-side exists but container peer missing)
    3. Saves link information before cleanup
    4. Cleans up stale ports
    5. Reprovisions fresh veth pairs
    6. Reconnects any previously connected links

    Args:
        mgr: OVSNetworkManager instance
        container_name: Docker container name
        lab_id: Lab identifier

    Returns:
        Summary dict with counts of reprovisioned ports/links and any errors
    """
    result: dict[str, Any] = {
        "ports_reprovisioned": 0,
        "links_reconnected": 0,
        "errors": [],
    }

    # Get all ports for this container
    ports = mgr.get_ports_for_container(container_name)
    if not ports:
        logger.debug(f"No tracked OVS ports for container {container_name}")
        return result

    # Check which ports are stale and collect link info before cleanup
    stale_ports: list[OVSPort] = []
    port_links: dict[str, list[tuple[str, str, str, str]]] = {}  # port_key -> [(cont_a, if_a, cont_b, if_b), ...]

    for port in ports:
        try:
            if await is_port_stale(mgr, port):
                stale_ports.append(port)

                # Find connected links for this port
                port_key = port.key
                connected_links = []
                for link in mgr._links.values():
                    if link.port_a == port_key:
                        # Parse the other endpoint
                        other_key = link.port_b
                        parts = other_key.split(":", 1)
                        if len(parts) == 2:
                            connected_links.append((
                                port.container_name, port.interface_name,
                                parts[0], parts[1]
                            ))
                    elif link.port_b == port_key:
                        other_key = link.port_a
                        parts = other_key.split(":", 1)
                        if len(parts) == 2:
                            connected_links.append((
                                parts[0], parts[1],
                                port.container_name, port.interface_name
                            ))

                if connected_links:
                    port_links[port_key] = connected_links

        except Exception as e:
            result["errors"].append(f"Error checking port {port.key}: {e}")

    if not stale_ports:
        logger.debug(f"No stale OVS ports for container {container_name}")
        return result

    logger.info(
        f"Container {container_name} restart detected - "
        f"reprovisioning {len(stale_ports)} stale OVS interfaces"
    )

    # Clean up stale ports and reprovision
    for port in stale_ports:
        port_key = port.key
        interface_name = port.interface_name

        try:
            # Cleanup the stale port
            await cleanup_stale_port(mgr, port)

            # Reprovision fresh veth pair
            await provision_interface(
                mgr,
                container_name=container_name,
                interface_name=interface_name,
                lab_id=lab_id,
            )
            result["ports_reprovisioned"] += 1

            # Reconnect any links that were previously connected
            if port_key in port_links:
                for link_endpoints in port_links[port_key]:
                    cont_a, if_a, cont_b, if_b = link_endpoints
                    try:
                        await mgr.hot_connect(
                            container_a=cont_a,
                            iface_a=if_a,
                            container_b=cont_b,
                            iface_b=if_b,
                            lab_id=lab_id,
                        )
                        result["links_reconnected"] += 1
                    except Exception as e:
                        result["errors"].append(
                            f"Failed to reconnect link {cont_a}:{if_a} <-> {cont_b}:{if_b}: {e}"
                        )

        except Exception as e:
            result["errors"].append(f"Failed to reprovision {interface_name}: {e}")

    if result["ports_reprovisioned"] > 0:
        logger.info(
            f"Reprovisioned {result['ports_reprovisioned']} interfaces, "
            f"reconnected {result['links_reconnected']} links for {container_name}"
        )

    if result["errors"]:
        for error in result["errors"]:
            logger.warning(error)

    return result
