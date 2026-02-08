"""Open vSwitch (OVS) network manager for hot-plug networking.

This module provides OVS-based networking with hot-plug support for network
devices that enumerate interfaces at boot time (like cEOS). The key insight
is that by using OVS with VLAN port isolation, we can:

1. Pre-provision real veth pairs at boot time (not dummy interfaces)
2. Attach all veths to a single OVS bridge with unique VLAN tags (isolated)
3. Hot-connect two interfaces by giving them the same VLAN tag
4. Hot-disconnect by assigning different VLAN tags

This approach allows adding/removing links without restarting containers.

Architecture:
                        HOST NAMESPACE
   ┌────────────────────────────────────────────────────────────┐
   │              ┌───────────────────────────────────────┐     │
   │              │         arch-ovs (OVS Bridge)         │     │
   │              │                                       │     │
   │              │  Port vh-A-e1 (tag=1001)             │     │
   │              │  Port vh-A-e2 (tag=1002)             │     │
   │              │  Port vh-B-e1 (tag=1001) ← same tag! │     │
   │              │  Port vh-B-e2 (tag=1003)             │     │
   │              └───────────────┬───────────────────────┘     │
   │        ┌─────────────────────┼─────────────────────┐       │
   │   ┌────┴────┐           ┌────┴────┐           ┌────┴────┐  │
   │   │vh-A-e1  │           │vh-A-e2  │           │vh-B-e1  │  │
   │   └────┬────┘           └────┬────┘           └────┬────┘  │
   └────────┼────────────────────┼────────────────────┼─────────┘
            │  Container A       │                    │ Container B
         ┌──┴──┐             ┌───┴───┐            ┌───┴───┐
         │eth1 │             │eth2   │            │eth1   │
         └─────┘             └───────┘            └───────┘

Link A:eth1 ↔ B:eth1: Both ports share tag=1001 (isolated broadcast domain)
A:eth2 has tag=1002 (isolated, no peer until connected)
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import docker
from docker.errors import NotFound

from agent.config import settings


logger = logging.getLogger(__name__)


# Default OVS bridge name
DEFAULT_BRIDGE_NAME = "arch-ovs"

# VLAN range for isolation (OVS supports 1-4094)
VLAN_START = 100
VLAN_END = 4000


@dataclass
class OVSPort:
    """Represents an OVS port attached to a container interface."""

    port_name: str  # OVS port name (host-side veth)
    container_name: str  # Docker container name
    interface_name: str  # Interface name inside container
    vlan_tag: int  # Current VLAN tag for isolation
    lab_id: str  # Lab this port belongs to

    @property
    def key(self) -> str:
        """Unique key for this port."""
        return f"{self.container_name}:{self.interface_name}"


@dataclass
class OVSLink:
    """Represents a connected link between two OVS ports."""

    link_id: str  # Unique link identifier
    lab_id: str
    port_a: str  # Key of first port (container:interface)
    port_b: str  # Key of second port
    vlan_tag: int  # Shared VLAN tag for this link

    @property
    def key(self) -> str:
        """Unique key for this link."""
        return f"{self.lab_id}:{self.link_id}"


class VlanAllocator:
    """Allocates unique VLAN tags for interface isolation.

    Allocations are persisted to disk to survive agent restarts.
    On startup, the allocator recovers state from:
    1. Persisted allocation file (if exists)
    2. Querying OVS for existing port VLAN tags
    """

    def __init__(
        self,
        start: int = VLAN_START,
        end: int = VLAN_END,
        persistence_path: Path | None = None,
    ):
        self._start = start
        self._end = end
        self._allocated: dict[str, int] = {}  # key -> vlan
        self._next_vlan = start

        # Persistence file path
        if persistence_path is None:
            workspace = Path(settings.workspace_path)
            workspace.mkdir(parents=True, exist_ok=True)
            persistence_path = workspace / "vlan_allocations.json"
        self._persistence_path = persistence_path

        # Load persisted state on init
        self._load_from_disk()

    def _load_from_disk(self) -> None:
        """Load allocations from persistence file."""
        if not self._persistence_path.exists():
            return

        try:
            with open(self._persistence_path, "r") as f:
                data = json.load(f)

            self._allocated = data.get("allocations", {})
            self._next_vlan = data.get("next_vlan", self._start)

            # Validate loaded VLANs are in range
            valid_allocations = {}
            for key, vlan in self._allocated.items():
                if self._start <= vlan <= self._end:
                    valid_allocations[key] = vlan
                else:
                    logger.warning(f"Ignoring out-of-range VLAN allocation: {key}={vlan}")

            self._allocated = valid_allocations
            logger.info(f"Loaded {len(self._allocated)} VLAN allocations from disk")

        except Exception as e:
            logger.warning(f"Failed to load VLAN allocations from disk: {e}")
            self._allocated = {}

    def _save_to_disk(self) -> None:
        """Save allocations to persistence file."""
        try:
            data = {
                "allocations": self._allocated,
                "next_vlan": self._next_vlan,
            }
            # Write atomically via temp file
            tmp_path = self._persistence_path.with_suffix(".tmp")
            with open(tmp_path, "w") as f:
                json.dump(data, f, indent=2)
            tmp_path.rename(self._persistence_path)

        except Exception as e:
            logger.warning(f"Failed to save VLAN allocations to disk: {e}")

    async def recover_from_ovs(self, bridge_name: str) -> int:
        """Scan OVS bridge ports and recover VLAN allocations.

        This should be called on agent startup to detect VLANs in use
        that may not be in the persisted file (e.g., after crash).

        Args:
            bridge_name: OVS bridge to scan

        Returns:
            Number of VLANs recovered from OVS state
        """
        recovered = 0
        used_vlans = set(self._allocated.values())

        try:
            # List all ports on the bridge
            proc = await asyncio.create_subprocess_exec(
                "ovs-vsctl", "list-ports", bridge_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()

            if proc.returncode != 0:
                return 0

            ports = stdout.decode().strip().split("\n") if stdout else []

            for port_name in ports:
                if not port_name or not port_name.startswith("vh"):
                    continue

                # Get VLAN tag for this port
                proc = await asyncio.create_subprocess_exec(
                    "ovs-vsctl", "get", "port", port_name, "tag",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                tag_stdout, _ = await proc.communicate()

                if proc.returncode != 0:
                    continue

                tag_str = tag_stdout.decode().strip()
                if not tag_str or tag_str == "[]":
                    continue

                try:
                    vlan = int(tag_str)
                    if self._start <= vlan <= self._end and vlan not in used_vlans:
                        # Found an in-use VLAN not in our allocations
                        placeholder_key = f"_recovered:{port_name}"
                        self._allocated[placeholder_key] = vlan
                        used_vlans.add(vlan)
                        recovered += 1
                        logger.debug(f"Recovered VLAN {vlan} from OVS port {port_name}")
                except ValueError:
                    continue

            if recovered > 0:
                self._save_to_disk()
                logger.info(f"Recovered {recovered} VLANs from OVS state")

        except Exception as e:
            logger.warning(f"Failed to recover VLANs from OVS: {e}")

        return recovered

    def allocate(self, key: str) -> int:
        """Allocate a VLAN tag for a key.

        Args:
            key: Unique identifier for the allocation

        Returns:
            Allocated VLAN tag

        Raises:
            RuntimeError: If no VLANs available
        """
        # Return existing allocation
        if key in self._allocated:
            return self._allocated[key]

        # Find next available VLAN
        used_vlans = set(self._allocated.values())
        attempts = 0
        while self._next_vlan in used_vlans:
            self._next_vlan += 1
            if self._next_vlan > self._end:
                self._next_vlan = self._start
            attempts += 1
            if attempts > (self._end - self._start):
                raise RuntimeError("No VLANs available")

        vlan = self._next_vlan
        self._allocated[key] = vlan
        self._next_vlan += 1
        if self._next_vlan > self._end:
            self._next_vlan = self._start

        # Persist allocation
        self._save_to_disk()

        return vlan

    def release(self, key: str) -> int | None:
        """Release a VLAN allocation.

        Returns the released VLAN or None if not found.
        """
        vlan = self._allocated.pop(key, None)
        if vlan is not None:
            self._save_to_disk()
        return vlan

    def release_lab(self, lab_id: str) -> int:
        """Release all VLAN allocations for a lab.

        Args:
            lab_id: Lab identifier (matches container names starting with archetype-{lab_id})

        Returns:
            Number of allocations released
        """
        # Keys are in format "container:interface"
        # Container names are "archetype-{lab_id}-{node}"
        prefix = f"archetype-{lab_id[:20]}"
        keys_to_remove = [k for k in self._allocated if k.startswith(prefix)]

        for key in keys_to_remove:
            del self._allocated[key]

        if keys_to_remove:
            self._save_to_disk()
            logger.info(f"Released {len(keys_to_remove)} VLAN allocations for lab {lab_id}")

        return len(keys_to_remove)

    def get_vlan(self, key: str) -> int | None:
        """Get VLAN for a key, or None if not allocated."""
        return self._allocated.get(key)

    def get_keys_for_vlan(self, vlan: int) -> list[str]:
        """Get all keys using a specific VLAN tag."""
        return [k for k, v in self._allocated.items() if v == vlan]

    def get_stats(self) -> dict[str, Any]:
        """Get allocator statistics for monitoring."""
        return {
            "total_allocated": len(self._allocated),
            "vlan_range": f"{self._start}-{self._end}",
            "next_vlan": self._next_vlan,
            "persistence_path": str(self._persistence_path),
        }


class OVSNetworkManager:
    """Manages OVS-based networking with hot-plug support.

    This class handles:
    - OVS bridge initialization
    - Interface provisioning with VLAN isolation
    - Hot-connect/disconnect via VLAN tag manipulation
    - Cross-host VXLAN tunnels through OVS
    - Lab-scoped cleanup

    Usage:
        manager = OVSNetworkManager()
        await manager.initialize()

        # Provision interfaces at container boot
        vlan = await manager.provision_interface(
            container_name="archetype-lab123-r1",
            interface_name="eth1",
            lab_id="lab123",
        )

        # Hot-connect two interfaces
        vlan = await manager.hot_connect(
            container_a="archetype-lab123-r1", iface_a="eth1",
            container_b="archetype-lab123-r2", iface_b="eth1",
        )

        # Hot-disconnect
        await manager.hot_disconnect(
            container_a="archetype-lab123-r1", iface_a="eth1",
            container_b="archetype-lab123-r2", iface_b="eth1",
        )

        # Cleanup lab
        await manager.cleanup_lab("lab123")
    """

    _instance: OVSNetworkManager | None = None

    def __new__(cls) -> OVSNetworkManager:
        """Singleton pattern for global OVS management."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def _init_state(self) -> None:
        """Initialize manager state."""
        self._docker: docker.DockerClient | None = None
        self._vlan_allocator = VlanAllocator()
        self._ports: dict[str, OVSPort] = {}  # key -> port
        self._links: dict[str, OVSLink] = {}  # key -> link
        self._bridge_name = getattr(settings, "ovs_bridge_name", DEFAULT_BRIDGE_NAME)
        self._initialized = False

    @property
    def docker(self) -> docker.DockerClient:
        """Lazy-initialize Docker client."""
        if self._docker is None:
            self._docker = docker.from_env()
        return self._docker

    @property
    def bridge_name(self) -> str:
        """Get OVS bridge name."""
        return self._bridge_name

    async def _run_cmd(self, cmd: list[str]) -> tuple[int, str, str]:
        """Run a shell command asynchronously.

        Args:
            cmd: Command and arguments as list

        Returns:
            Tuple of (return_code, stdout, stderr)
        """
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        return (
            process.returncode or 0,
            stdout.decode(errors="replace"),
            stderr.decode(errors="replace"),
        )

    async def _ovs_vsctl(self, *args: str) -> tuple[int, str, str]:
        """Run ovs-vsctl command.

        Args:
            args: Arguments to ovs-vsctl

        Returns:
            Tuple of (return_code, stdout, stderr)
        """
        cmd = ["ovs-vsctl"] + list(args)
        return await self._run_cmd(cmd)

    async def _ip_link_exists(self, name: str) -> bool:
        """Check if a network interface exists."""
        code, _, _ = await self._run_cmd(["ip", "link", "show", name])
        return code == 0

    async def _get_container_pid(self, container_name: str) -> int | None:
        """Get the PID of a container's init process.

        Args:
            container_name: Docker container name

        Returns:
            PID if container is running, None otherwise
        """
        def _sync_get_pid() -> int | None:
            try:
                container = self.docker.containers.get(container_name)
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

    def _generate_port_name(self, container_name: str, interface_name: str) -> str:
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

    async def _discover_existing_state(self) -> None:
        """Discover existing OVS ports and rebuild internal state.

        Called on initialization when bridge already exists (agent restart).
        This ensures we don't try to re-create ports that already exist.
        """
        # List all ports on the bridge
        code, stdout, _ = await self._ovs_vsctl("list-ports", self._bridge_name)
        if code != 0 or not stdout.strip():
            return

        ports = stdout.strip().split("\n")
        discovered_count = 0

        for port_name in ports:
            # Only process our container veth ports (start with 'vh')
            if not port_name.startswith("vh"):
                continue

            # Get VLAN tag for this port
            code, tag_stdout, _ = await self._ovs_vsctl("get", "port", port_name, "tag")
            vlan_tag = None
            if code == 0 and tag_stdout.strip() and tag_stdout.strip() != "[]":
                try:
                    vlan_tag = int(tag_stdout.strip())
                except ValueError:
                    continue

            if vlan_tag is None:
                continue

            # Try to find which container owns this port by checking the veth peer
            # The peer should be inside a container namespace
            container_name = None
            interface_name = None

            try:
                # Get all running archetype containers (wrapped to avoid blocking)
                def _get_containers():
                    return self.docker.containers.list(
                        filters={"label": "archetype.lab_id"}
                    )
                containers = await asyncio.to_thread(_get_containers)
                for container in containers:
                    pid = container.attrs["State"]["Pid"]
                    if not pid:
                        continue

                    # List interfaces in container namespace
                    code, ns_stdout, _ = await self._run_cmd([
                        "nsenter", "-t", str(pid), "-n",
                        "ip", "-o", "link", "show"
                    ])
                    if code != 0:
                        continue

                    # Parse interface names from output
                    for line in ns_stdout.split("\n"):
                        if not line.strip():
                            continue
                        # Format: "2: eth1@if123: <...>"
                        parts = line.split(":")
                        if len(parts) >= 2:
                            iface = parts[1].strip().split("@")[0]
                            # Skip loopback and management interfaces
                            if iface in ("lo", "eth0"):
                                continue

                            # Check if this interface's peer index matches our port
                            # by checking the ifindex relationship
                            if "@if" in parts[1]:
                                peer_idx = parts[1].split("@if")[1].split(":")[0]
                                # Get our port's ifindex
                                code, idx_out, _ = await self._run_cmd([
                                    "cat", f"/sys/class/net/{port_name}/ifindex"
                                ])
                                if code == 0 and idx_out.strip() == peer_idx:
                                    container_name = container.name
                                    interface_name = iface
                                    break
                    if container_name:
                        break

            except Exception as e:
                logger.debug(f"Error discovering container for port {port_name}: {e}")

            if container_name and interface_name:
                # Found the owner - reconstruct port tracking
                port_key = f"{container_name}:{interface_name}"
                # Get lab_id from container labels (wrapped to avoid blocking)
                def _get_lab_id():
                    return self.docker.containers.get(container_name).labels.get(
                        "archetype.lab_id", "_unknown"
                    )
                lab_id = await asyncio.to_thread(_get_lab_id)

                port = OVSPort(
                    port_name=port_name,
                    container_name=container_name,
                    interface_name=interface_name,
                    vlan_tag=vlan_tag,
                    lab_id=lab_id,
                )
                self._ports[port_key] = port
                self._vlan_allocator._allocated[port_key] = vlan_tag
                discovered_count += 1

        if discovered_count > 0:
            logger.info(f"Discovered {discovered_count} existing OVS ports after restart")

        # Discover links by finding ports that share VLAN tags
        vlan_to_ports: dict[int, list[str]] = {}
        for key, port in self._ports.items():
            if port.vlan_tag not in vlan_to_ports:
                vlan_to_ports[port.vlan_tag] = []
            vlan_to_ports[port.vlan_tag].append(key)

        # Create link records for VLAN tags shared by exactly 2 ports
        for vlan_tag, port_keys in vlan_to_ports.items():
            if len(port_keys) == 2:
                port_a = self._ports[port_keys[0]]
                port_b = self._ports[port_keys[1]]
                link_id = f"{port_keys[0]}-{port_keys[1]}"
                link = OVSLink(
                    link_id=link_id,
                    lab_id=port_a.lab_id,
                    port_a=port_keys[0],
                    port_b=port_keys[1],
                    vlan_tag=vlan_tag,
                )
                self._links[link.key] = link

        if self._links:
            logger.info(f"Discovered {len(self._links)} existing OVS links")

    async def initialize(self) -> None:
        """Initialize OVS bridge if it doesn't exist.

        Creates the arch-ovs bridge and sets required options.
        Safe to call multiple times - idempotent.
        """
        if self._initialized:
            return

        self._init_state()

        # Check if OVS is available
        code, _, stderr = await self._ovs_vsctl("--version")
        if code != 0:
            raise RuntimeError(f"OVS not available: {stderr}")

        # Check if bridge exists
        code, stdout, _ = await self._ovs_vsctl("br-exists", self._bridge_name)
        if code != 0:
            # Bridge doesn't exist, create it
            logger.info(f"Creating OVS bridge: {self._bridge_name}")
            code, _, stderr = await self._ovs_vsctl("add-br", self._bridge_name)
            if code != 0:
                raise RuntimeError(f"Failed to create OVS bridge: {stderr}")

            # Set fail mode to standalone for normal L2 switching
            # Secure mode drops all traffic without explicit OpenFlow rules
            await self._ovs_vsctl(
                "set-fail-mode", self._bridge_name, "standalone"
            )

            # Bring bridge up
            await self._run_cmd(["ip", "link", "set", self._bridge_name, "up"])

            logger.info(f"OVS bridge {self._bridge_name} created and configured")
        else:
            logger.info(f"OVS bridge {self._bridge_name} already exists")
            # Discover existing OVS state after agent restart
            await self._discover_existing_state()

        self._initialized = True

    async def provision_interface(
        self,
        container_name: str,
        interface_name: str,
        lab_id: str,
    ) -> int:
        """Create veth pair and attach to OVS with isolated VLAN tag.

        This provisions a real interface (not dummy) that can be hot-connected
        to other interfaces later. The interface starts isolated with a unique
        VLAN tag.

        Args:
            container_name: Docker container name
            interface_name: Interface name inside container (e.g., "eth1")
            lab_id: Lab identifier for tracking

        Returns:
            Allocated VLAN tag

        Raises:
            RuntimeError: If provisioning fails
        """
        if not self._initialized:
            await self.initialize()

        port_key = f"{container_name}:{interface_name}"

        # Check if already provisioned
        if port_key in self._ports:
            logger.debug(f"Interface already provisioned: {port_key}")
            return self._ports[port_key].vlan_tag

        # Get container PID
        pid = await self._get_container_pid(container_name)
        if pid is None:
            raise RuntimeError(f"Container {container_name} is not running")

        # Generate port name
        port_name = self._generate_port_name(container_name, interface_name)

        # Allocate VLAN tag for isolation
        vlan_tag = self._vlan_allocator.allocate(port_key)

        # Create veth pair
        veth_cont = f"vc{secrets.token_hex(4)}"[:15]  # Container-side name (temporary)

        # Delete if exists (from previous run)
        if await self._ip_link_exists(port_name):
            await self._run_cmd(["ip", "link", "delete", port_name])

        try:
            # Create veth pair
            code, _, stderr = await self._run_cmd([
                "ip", "link", "add", port_name, "type", "veth", "peer", "name", veth_cont
            ])
            if code != 0:
                raise RuntimeError(f"Failed to create veth pair: {stderr}")

            # Set MTU on veth pair for jumbo frame support
            if settings.local_mtu > 0:
                await self._run_cmd([
                    "ip", "link", "set", port_name, "mtu", str(settings.local_mtu)
                ])
                await self._run_cmd([
                    "ip", "link", "set", veth_cont, "mtu", str(settings.local_mtu)
                ])

            # Add host-side to OVS bridge with VLAN tag
            code, _, stderr = await self._ovs_vsctl(
                "add-port", self._bridge_name, port_name,
                f"tag={vlan_tag}",
                "--", "set", "interface", port_name, "type=system"
            )
            if code != 0:
                await self._run_cmd(["ip", "link", "delete", port_name])
                raise RuntimeError(f"Failed to add port to OVS: {stderr}")

            # Bring host-side up
            await self._run_cmd(["ip", "link", "set", port_name, "up"])

            # Move container-side to container namespace
            code, _, stderr = await self._run_cmd([
                "ip", "link", "set", veth_cont, "netns", str(pid)
            ])
            if code != 0:
                await self._ovs_vsctl("del-port", self._bridge_name, port_name)
                await self._run_cmd(["ip", "link", "delete", port_name])
                raise RuntimeError(f"Failed to move veth to container: {stderr}")

            # Rename interface inside container
            code, _, stderr = await self._run_cmd([
                "nsenter", "-t", str(pid), "-n",
                "ip", "link", "set", veth_cont, "name", interface_name
            ])
            if code != 0:
                logger.warning(f"Failed to rename interface to {interface_name}: {stderr}")

            # Bring interface up inside container
            await self._run_cmd([
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
            )
            self._ports[port_key] = port

            logger.info(
                f"Provisioned {container_name}:{interface_name} -> "
                f"OVS port {port_name} (VLAN {vlan_tag})"
            )
            return vlan_tag

        except Exception as e:
            # Cleanup on failure
            self._vlan_allocator.release(port_key)
            try:
                await self._ovs_vsctl("del-port", self._bridge_name, port_name)
            except Exception:
                pass
            try:
                await self._run_cmd(["ip", "link", "delete", port_name])
            except Exception:
                pass
            raise RuntimeError(f"Failed to provision interface: {e}")

    async def hot_connect(
        self,
        container_a: str,
        iface_a: str,
        container_b: str,
        iface_b: str,
        lab_id: str | None = None,
    ) -> int:
        """Connect two interfaces by sharing a VLAN tag.

        This creates a Layer 2 link between two container interfaces by
        assigning them the same VLAN tag on the OVS bridge.

        Args:
            container_a: First container name
            iface_a: First interface name
            container_b: Second container name
            iface_b: Second interface name
            lab_id: Optional lab ID for tracking

        Returns:
            Shared VLAN tag for the link

        Raises:
            RuntimeError: If connection fails
        """
        if not self._initialized:
            await self.initialize()

        key_a = f"{container_a}:{iface_a}"
        key_b = f"{container_b}:{iface_b}"

        # Verify both ports exist
        port_a = self._ports.get(key_a)
        port_b = self._ports.get(key_b)

        if not port_a:
            raise RuntimeError(f"Port not provisioned: {key_a}")
        if not port_b:
            raise RuntimeError(f"Port not provisioned: {key_b}")

        # Use the VLAN tag from port_a (or allocate new shared one)
        shared_vlan = port_a.vlan_tag

        # Update port_b to use the same VLAN tag
        if port_b.vlan_tag != shared_vlan:
            code, _, stderr = await self._ovs_vsctl(
                "set", "port", port_b.port_name, f"tag={shared_vlan}"
            )
            if code != 0:
                raise RuntimeError(f"Failed to update VLAN tag: {stderr}")

            # Release old VLAN allocation for port_b
            self._vlan_allocator.release(key_b)

            # Update port record
            port_b.vlan_tag = shared_vlan

        # Create link record
        link_id = f"{key_a}-{key_b}"
        effective_lab_id = lab_id or port_a.lab_id
        link = OVSLink(
            link_id=link_id,
            lab_id=effective_lab_id,
            port_a=key_a,
            port_b=key_b,
            vlan_tag=shared_vlan,
        )
        self._links[link.key] = link

        logger.info(
            f"Hot-connected {key_a} <-> {key_b} (VLAN {shared_vlan})"
        )
        return shared_vlan

    async def hot_disconnect(
        self,
        container_a: str,
        iface_a: str,
        container_b: str,
        iface_b: str,
    ) -> tuple[int, int]:
        """Disconnect two interfaces by assigning separate VLAN tags.

        This breaks the Layer 2 link between two container interfaces by
        giving each a unique VLAN tag.

        Args:
            container_a: First container name
            iface_a: First interface name
            container_b: Second container name
            iface_b: Second interface name

        Returns:
            Tuple of new VLAN tags (vlan_a, vlan_b)

        Raises:
            RuntimeError: If disconnection fails
        """
        if not self._initialized:
            await self.initialize()

        key_a = f"{container_a}:{iface_a}"
        key_b = f"{container_b}:{iface_b}"

        port_a = self._ports.get(key_a)
        port_b = self._ports.get(key_b)

        if not port_a:
            raise RuntimeError(f"Port not found: {key_a}")
        if not port_b:
            raise RuntimeError(f"Port not found: {key_b}")

        # Allocate new unique VLAN for port_b
        new_vlan_b = self._vlan_allocator.allocate(key_b)

        # Update port_b VLAN tag
        code, _, stderr = await self._ovs_vsctl(
            "set", "port", port_b.port_name, f"tag={new_vlan_b}"
        )
        if code != 0:
            self._vlan_allocator.release(key_b)
            raise RuntimeError(f"Failed to update VLAN tag: {stderr}")

        port_b.vlan_tag = new_vlan_b

        # Remove link record
        link_key = f"{port_a.lab_id}:{key_a}-{key_b}"
        link_key_alt = f"{port_a.lab_id}:{key_b}-{key_a}"
        self._links.pop(link_key, None)
        self._links.pop(link_key_alt, None)

        logger.info(
            f"Hot-disconnected {key_a} (VLAN {port_a.vlan_tag}) <-> "
            f"{key_b} (VLAN {new_vlan_b})"
        )
        return (port_a.vlan_tag, new_vlan_b)

    async def create_vxlan_tunnel(
        self,
        vni: int,
        remote_ip: str,
        local_ip: str,
        vlan_tag: int | None = None,
    ) -> str:
        """Create VXLAN tunnel on OVS for cross-host connectivity.

        Creates a Linux VXLAN device with nopmtudisc to disable PMTUD,
        then adds it to the OVS bridge as a system port. This allows
        inner packets to pass at full MTU while the kernel handles
        outer packet fragmentation transparently.

        Args:
            vni: VXLAN Network Identifier
            remote_ip: Remote host IP
            local_ip: Local host IP
            vlan_tag: Optional VLAN tag to map to this tunnel

        Returns:
            VXLAN port name

        Raises:
            RuntimeError: If tunnel creation fails
        """
        if not self._initialized:
            await self.initialize()

        port_name = f"vxlan{vni}"

        # Delete existing OVS port if present
        await self._ovs_vsctl("--if-exists", "del-port", self._bridge_name, port_name)

        # Delete existing Linux interface if present
        if await self._ip_link_exists(port_name):
            await self._run_cmd(["ip", "link", "delete", port_name])

        # Create Linux VXLAN device with nopmtudisc
        code, _, stderr = await self._run_cmd([
            "ip", "link", "add", port_name, "type", "vxlan",
            "id", str(vni), "local", local_ip, "remote", remote_ip,
            "dstport", "4789", "df", "unset", "nopmtudisc",
        ])
        if code != 0:
            raise RuntimeError(f"Failed to create VXLAN device: {stderr}")

        # Set MTU to match local physical MTU
        await self._run_cmd(["ip", "link", "set", port_name, "mtu", str(settings.local_mtu)])

        # Bring device up
        await self._run_cmd(["ip", "link", "set", port_name, "up"])

        # Add to OVS bridge as system port
        if vlan_tag:
            code, _, stderr = await self._ovs_vsctl(
                "add-port", self._bridge_name, port_name,
                f"tag={vlan_tag}",
            )
        else:
            code, _, stderr = await self._ovs_vsctl(
                "add-port", self._bridge_name, port_name,
            )

        if code != 0:
            await self._run_cmd(["ip", "link", "delete", port_name])
            raise RuntimeError(f"Failed to add VXLAN to OVS: {stderr}")

        logger.info(
            f"Created VXLAN tunnel: {port_name} (VNI {vni}) to {remote_ip}"
        )
        return port_name

    async def delete_vxlan_tunnel(self, vni: int) -> bool:
        """Delete a VXLAN tunnel.

        Args:
            vni: VXLAN Network Identifier

        Returns:
            True if deleted successfully
        """
        port_name = f"vxlan{vni}"
        code, _, stderr = await self._ovs_vsctl(
            "--if-exists", "del-port", self._bridge_name, port_name
        )
        if code != 0:
            logger.warning(f"Failed to delete VXLAN tunnel {port_name}: {stderr}")
            return False

        # Delete the Linux VXLAN device (system ports aren't auto-deleted)
        await self._run_cmd(["ip", "link", "delete", port_name])

        logger.info(f"Deleted VXLAN tunnel: {port_name}")
        return True

    async def set_port_vlan(
        self,
        container_name: str,
        interface_name: str,
        vlan_tag: int,
    ) -> bool:
        """Update the VLAN tag for a port.

        Args:
            container_name: Container name
            interface_name: Interface name
            vlan_tag: New VLAN tag

        Returns:
            True if updated successfully
        """
        key = f"{container_name}:{interface_name}"
        port = self._ports.get(key)

        if not port:
            logger.error(f"Port not found: {key}")
            return False

        code, _, stderr = await self._ovs_vsctl(
            "set", "port", port.port_name, f"tag={vlan_tag}"
        )
        if code != 0:
            logger.error(f"Failed to set VLAN tag: {stderr}")
            return False

        # Update tracking
        old_vlan = port.vlan_tag
        port.vlan_tag = vlan_tag

        logger.debug(f"Updated {key} VLAN: {old_vlan} -> {vlan_tag}")
        return True

    async def delete_port(
        self,
        container_name: str,
        interface_name: str,
    ) -> bool:
        """Delete an OVS port and release resources.

        Args:
            container_name: Container name
            interface_name: Interface name

        Returns:
            True if deleted successfully
        """
        key = f"{container_name}:{interface_name}"
        port = self._ports.get(key)

        if not port:
            logger.warning(f"Port not found for deletion: {key}")
            return False

        # Remove from OVS
        code, _, stderr = await self._ovs_vsctl(
            "--if-exists", "del-port", self._bridge_name, port.port_name
        )
        if code != 0:
            logger.warning(f"Failed to delete OVS port {port.port_name}: {stderr}")

        # Delete veth pair (removing one end deletes both)
        await self._run_cmd(["ip", "link", "delete", port.port_name])

        # Release VLAN
        self._vlan_allocator.release(key)

        # Remove from tracking
        del self._ports[key]

        # Remove any links involving this port
        links_to_remove = [
            link_key for link_key, link in self._links.items()
            if link.port_a == key or link.port_b == key
        ]
        for link_key in links_to_remove:
            del self._links[link_key]

        logger.info(f"Deleted port: {key}")
        return True

    async def cleanup_lab(self, lab_id: str) -> dict[str, Any]:
        """Clean up all OVS resources for a lab.

        This removes all ports, links, and VLAN allocations for a lab.

        Args:
            lab_id: Lab identifier

        Returns:
            Summary of cleanup actions
        """
        result = {
            "ports_deleted": 0,
            "links_deleted": 0,
            "vlans_released": 0,
            "errors": [],
        }

        # Find all ports for this lab
        ports_to_delete = [
            (key, port) for key, port in self._ports.items()
            if port.lab_id == lab_id
        ]

        # Delete links first
        links_to_delete = [
            link_key for link_key, link in self._links.items()
            if link.lab_id == lab_id
        ]
        for link_key in links_to_delete:
            del self._links[link_key]
            result["links_deleted"] += 1

        # Delete ports
        for key, port in ports_to_delete:
            try:
                # Remove from OVS
                await self._ovs_vsctl(
                    "--if-exists", "del-port", self._bridge_name, port.port_name
                )

                # Delete veth
                await self._run_cmd(["ip", "link", "delete", port.port_name])

                # Release VLAN
                self._vlan_allocator.release(key)

                # Remove from tracking
                del self._ports[key]

                result["ports_deleted"] += 1

            except Exception as e:
                result["errors"].append(f"Port {key}: {e}")

        # Release any remaining VLAN allocations for this lab
        # (handles allocations that may not have tracked ports)
        result["vlans_released"] = self._vlan_allocator.release_lab(lab_id)

        logger.info(f"Lab {lab_id} OVS cleanup: {result}")
        return result

    async def recover_allocations(self) -> int:
        """Recover VLAN allocations from OVS state on startup.

        Returns:
            Number of VLANs recovered
        """
        return await self._vlan_allocator.recover_from_ovs(self._bridge_name)

    def get_ports_for_lab(self, lab_id: str) -> list[OVSPort]:
        """Get all OVS ports for a lab."""
        return [p for p in self._ports.values() if p.lab_id == lab_id]

    def get_links_for_lab(self, lab_id: str) -> list[OVSLink]:
        """Get all links for a lab."""
        return [l for l in self._links.values() if l.lab_id == lab_id]

    def get_port(self, container_name: str, interface_name: str) -> OVSPort | None:
        """Get port by container and interface name."""
        key = f"{container_name}:{interface_name}"
        return self._ports.get(key)

    def get_ports_for_container(self, container_name: str) -> list[OVSPort]:
        """Get all OVS ports for a specific container.

        Args:
            container_name: Docker container name

        Returns:
            List of OVSPort objects for this container
        """
        return [p for p in self._ports.values() if p.container_name == container_name]

    async def is_port_stale(self, port: OVSPort) -> bool:
        """Check if an OVS port is stale (host-side exists but container peer missing).

        When a container restarts, its network namespace is recreated and the
        veth peer inside is destroyed. The host-side veth (attached to OVS)
        still exists but has no peer - this is a "stale" port.

        Args:
            port: OVS port to check

        Returns:
            True if the port is stale (needs reprovisioning), False if healthy
        """
        # Check if host-side veth exists
        if not await self._ip_link_exists(port.port_name):
            # Host-side doesn't exist - not stale, just missing entirely
            return False

        # Get container PID
        pid = await self._get_container_pid(port.container_name)
        if pid is None:
            # Container not running - can't check, assume not stale
            return False

        # Check if interface exists inside container namespace
        code, stdout, _ = await self._run_cmd([
            "nsenter", "-t", str(pid), "-n",
            "ip", "link", "show", port.interface_name
        ])

        # If the interface doesn't exist inside container, port is stale
        return code != 0

    async def _cleanup_stale_port(self, port: OVSPort) -> None:
        """Remove a stale OVS port and release resources.

        This cleans up the host-side veth and OVS port entry for a port
        whose container-side peer no longer exists.

        Args:
            port: OVS port to clean up
        """
        key = port.key

        # Remove from OVS bridge
        code, _, stderr = await self._ovs_vsctl(
            "--if-exists", "del-port", self._bridge_name, port.port_name
        )
        if code != 0:
            logger.warning(f"Failed to delete OVS port {port.port_name}: {stderr}")

        # Delete host-side veth (if it exists)
        if await self._ip_link_exists(port.port_name):
            await self._run_cmd(["ip", "link", "delete", port.port_name])

        # Release VLAN allocation
        self._vlan_allocator.release(key)

        # Remove from port tracking
        self._ports.pop(key, None)

        # Remove any links involving this port
        links_to_remove = [
            link_key for link_key, link in self._links.items()
            if link.port_a == key or link.port_b == key
        ]
        for link_key in links_to_remove:
            del self._links[link_key]

        logger.debug(f"Cleaned up stale port: {key}")

    async def handle_container_restart(
        self,
        container_name: str,
        lab_id: str,
    ) -> dict[str, Any]:
        """Handle container restart by reprovisioning stale OVS interfaces.

        When a container restarts, its network namespace is recreated and veth
        peers inside are destroyed. This method:
        1. Finds all tracked ports for the container
        2. Checks which are stale (host-side exists but container peer missing)
        3. Saves link information before cleanup
        4. Cleans up stale ports
        5. Reprovisions fresh veth pairs
        6. Reconnects any previously connected links

        Args:
            container_name: Docker container name
            lab_id: Lab identifier

        Returns:
            Summary dict with counts of reprovisioned ports/links and any errors
        """
        result = {
            "ports_reprovisioned": 0,
            "links_reconnected": 0,
            "errors": [],
        }

        # Get all ports for this container
        ports = self.get_ports_for_container(container_name)
        if not ports:
            logger.debug(f"No tracked OVS ports for container {container_name}")
            return result

        # Check which ports are stale and collect link info before cleanup
        stale_ports: list[OVSPort] = []
        port_links: dict[str, list[tuple[str, str, str, str]]] = {}  # port_key -> [(cont_a, if_a, cont_b, if_b), ...]

        for port in ports:
            try:
                if await self.is_port_stale(port):
                    stale_ports.append(port)

                    # Find connected links for this port
                    port_key = port.key
                    connected_links = []
                    for link in self._links.values():
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
                await self._cleanup_stale_port(port)

                # Reprovision fresh veth pair
                await self.provision_interface(
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
                            await self.hot_connect(
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

    def get_link_by_endpoints(
        self,
        container_a: str,
        iface_a: str,
        container_b: str,
        iface_b: str,
    ) -> OVSLink | None:
        """Find a link by its endpoints."""
        key_a = f"{container_a}:{iface_a}"
        key_b = f"{container_b}:{iface_b}"

        for link in self._links.values():
            if (link.port_a == key_a and link.port_b == key_b) or \
               (link.port_a == key_b and link.port_b == key_a):
                return link
        return None

    async def attach_external_interface(
        self,
        external_interface: str,
        vlan_tag: int | None = None,
        lab_id: str = "_external",
    ) -> int:
        """Attach an external host interface to the OVS bridge.

        This allows connecting lab devices to external networks (internet,
        management networks, physical lab equipment). The external interface
        is added as a port on the OVS bridge.

        Args:
            external_interface: Host interface name (e.g., "eth1", "enp0s3")
            vlan_tag: Optional VLAN tag for isolation. If None, uses trunk mode.
            lab_id: Lab identifier for tracking (default: "_external")

        Returns:
            VLAN tag used (0 for trunk mode)

        Raises:
            RuntimeError: If attachment fails
        """
        if not self._initialized:
            await self.initialize()

        # Check if interface exists
        if not await self._ip_link_exists(external_interface):
            raise RuntimeError(f"Interface {external_interface} does not exist")

        # Check if already attached
        code, stdout, _ = await self._ovs_vsctl("port-to-br", external_interface)
        if code == 0 and stdout.strip() == self._bridge_name:
            logger.info(f"External interface {external_interface} already attached to {self._bridge_name}")
            return vlan_tag or 0

        # Remove from any other bridge
        if code == 0:
            await self._ovs_vsctl("del-port", stdout.strip(), external_interface)

        # Add to OVS bridge
        if vlan_tag:
            code, _, stderr = await self._ovs_vsctl(
                "add-port", self._bridge_name, external_interface,
                f"tag={vlan_tag}"
            )
        else:
            # Trunk mode - no VLAN tag
            code, _, stderr = await self._ovs_vsctl(
                "add-port", self._bridge_name, external_interface
            )

        if code != 0:
            raise RuntimeError(f"Failed to attach {external_interface}: {stderr}")

        # Bring interface up
        await self._run_cmd(["ip", "link", "set", external_interface, "up"])

        logger.info(
            f"Attached external interface {external_interface} to {self._bridge_name} "
            f"(VLAN: {vlan_tag or 'trunk'})"
        )
        return vlan_tag or 0

    async def detach_external_interface(self, external_interface: str) -> bool:
        """Detach an external interface from the OVS bridge.

        Args:
            external_interface: Host interface name

        Returns:
            True if detached successfully
        """
        code, _, stderr = await self._ovs_vsctl(
            "--if-exists", "del-port", self._bridge_name, external_interface
        )
        if code != 0:
            logger.warning(f"Failed to detach {external_interface}: {stderr}")
            return False

        logger.info(f"Detached external interface {external_interface}")
        return True

    async def connect_to_external(
        self,
        container_name: str,
        interface_name: str,
        external_interface: str,
        vlan_tag: int | None = None,
    ) -> int:
        """Connect a container interface to an external network.

        This establishes connectivity between a container interface and an
        external host interface by assigning them the same VLAN tag.

        Args:
            container_name: Container name
            interface_name: Interface name inside container
            external_interface: External host interface
            vlan_tag: Optional specific VLAN tag to use

        Returns:
            VLAN tag used for the connection

        Raises:
            RuntimeError: If connection fails
        """
        if not self._initialized:
            await self.initialize()

        port_key = f"{container_name}:{interface_name}"
        port = self._ports.get(port_key)

        if not port:
            raise RuntimeError(f"Port not provisioned: {port_key}")

        # Use existing VLAN or allocate new one
        if vlan_tag is None:
            vlan_tag = port.vlan_tag

        # Attach external interface with same VLAN tag
        await self.attach_external_interface(
            external_interface=external_interface,
            vlan_tag=vlan_tag,
        )

        # Ensure container port uses same VLAN
        if port.vlan_tag != vlan_tag:
            await self.set_port_vlan(container_name, interface_name, vlan_tag)

        logger.info(
            f"Connected {port_key} to external {external_interface} (VLAN {vlan_tag})"
        )
        return vlan_tag

    async def create_patch_to_bridge(
        self,
        target_bridge: str,
        vlan_tag: int | None = None,
    ) -> str:
        """Create a patch port to another OVS or Linux bridge.

        This creates an internal patch connection between the arch-ovs bridge
        and another bridge (e.g., for connecting to libvirt networks or
        existing infrastructure).

        Args:
            target_bridge: Name of the target bridge
            vlan_tag: Optional VLAN tag for the connection

        Returns:
            Patch port name

        Raises:
            RuntimeError: If patch creation fails
        """
        if not self._initialized:
            await self.initialize()

        # Create patch port names
        patch_local = f"patch-to-{target_bridge[:8]}"
        patch_remote = f"patch-from-arch"

        # Check if target is OVS bridge
        code, _, _ = await self._ovs_vsctl("br-exists", target_bridge)
        is_ovs_bridge = (code == 0)

        if is_ovs_bridge:
            # Create OVS patch ports (internal OVS connection)
            # Local side
            if vlan_tag:
                code, _, stderr = await self._ovs_vsctl(
                    "--may-exist", "add-port", self._bridge_name, patch_local,
                    f"tag={vlan_tag}",
                    "--", "set", "interface", patch_local, "type=patch",
                    f"options:peer={patch_remote}"
                )
            else:
                code, _, stderr = await self._ovs_vsctl(
                    "--may-exist", "add-port", self._bridge_name, patch_local,
                    "--", "set", "interface", patch_local, "type=patch",
                    f"options:peer={patch_remote}"
                )
            if code != 0:
                raise RuntimeError(f"Failed to create local patch: {stderr}")

            # Remote side
            code, _, stderr = await self._ovs_vsctl(
                "--may-exist", "add-port", target_bridge, patch_remote,
                "--", "set", "interface", patch_remote, "type=patch",
                f"options:peer={patch_local}"
            )
            if code != 0:
                # Cleanup local side
                await self._ovs_vsctl("del-port", self._bridge_name, patch_local)
                raise RuntimeError(f"Failed to create remote patch: {stderr}")
        else:
            # Target is Linux bridge - use veth pair
            veth_local = f"v{target_bridge[:6]}l"[:15]
            veth_remote = f"v{target_bridge[:6]}r"[:15]

            # Create veth pair
            await self._run_cmd(["ip", "link", "delete", veth_local])
            code, _, stderr = await self._run_cmd([
                "ip", "link", "add", veth_local, "type", "veth",
                "peer", "name", veth_remote
            ])
            if code != 0:
                raise RuntimeError(f"Failed to create veth pair: {stderr}")

            # Add local end to OVS
            if vlan_tag:
                code, _, stderr = await self._ovs_vsctl(
                    "add-port", self._bridge_name, veth_local,
                    f"tag={vlan_tag}"
                )
            else:
                code, _, stderr = await self._ovs_vsctl(
                    "add-port", self._bridge_name, veth_local
                )
            if code != 0:
                await self._run_cmd(["ip", "link", "delete", veth_local])
                raise RuntimeError(f"Failed to add to OVS: {stderr}")

            # Add remote end to Linux bridge
            code, _, stderr = await self._run_cmd([
                "ip", "link", "set", veth_remote, "master", target_bridge
            ])
            if code != 0:
                await self._ovs_vsctl("del-port", self._bridge_name, veth_local)
                await self._run_cmd(["ip", "link", "delete", veth_local])
                raise RuntimeError(f"Failed to add to Linux bridge: {stderr}")

            # Bring both ends up
            await self._run_cmd(["ip", "link", "set", veth_local, "up"])
            await self._run_cmd(["ip", "link", "set", veth_remote, "up"])

            patch_local = veth_local

        logger.info(f"Created patch to {target_bridge} via {patch_local}")
        return patch_local

    async def delete_patch_to_bridge(self, target_bridge: str) -> bool:
        """Delete a patch connection to another bridge.

        Args:
            target_bridge: Name of the target bridge

        Returns:
            True if deleted successfully
        """
        if not self._initialized:
            return False

        # Check if target is OVS bridge
        code, _, _ = await self._ovs_vsctl("br-exists", target_bridge)
        is_ovs_bridge = (code == 0)

        patch_local = f"patch-to-{target_bridge[:8]}"
        patch_remote = "patch-from-arch"

        if is_ovs_bridge:
            # Delete OVS patch ports
            await self._ovs_vsctl("--if-exists", "del-port", self._bridge_name, patch_local)
            await self._ovs_vsctl("--if-exists", "del-port", target_bridge, patch_remote)
        else:
            # Delete veth pair (Linux bridge)
            veth_local = f"v{target_bridge[:6]}l"[:15]
            await self._ovs_vsctl("--if-exists", "del-port", self._bridge_name, veth_local)
            await self._run_cmd(["ip", "link", "delete", veth_local])

        logger.info(f"Deleted patch to {target_bridge}")
        return True

    async def list_external_connections(self) -> list[dict[str, Any]]:
        """List all external interface connections.

        Returns:
            List of external connection info dicts with interface name and VLAN
        """
        if not self._initialized:
            return []

        connections = []

        # Get all ports on the OVS bridge
        code, stdout, _ = await self._ovs_vsctl("list-ports", self._bridge_name)
        if code != 0:
            return connections

        ports = stdout.strip().split("\n") if stdout.strip() else []

        for port_name in ports:
            # Skip internal ports (our veth pairs start with 'vh')
            if port_name.startswith("vh") or port_name.startswith("vxlan"):
                continue

            # Skip patch ports
            if port_name.startswith("patch-") or port_name.startswith("v") and port_name.endswith("l"):
                continue

            # Get port info
            code, stdout, _ = await self._ovs_vsctl("get", "port", port_name, "tag")
            vlan_tag = None
            if code == 0 and stdout.strip() and stdout.strip() != "[]":
                try:
                    vlan_tag = int(stdout.strip())
                except ValueError:
                    pass

            # Find connected container ports with same VLAN
            connected_ports = []
            if vlan_tag:
                for key, port in self._ports.items():
                    if port.vlan_tag == vlan_tag:
                        connected_ports.append(key)

            connections.append({
                "external_interface": port_name,
                "vlan_tag": vlan_tag,
                "connected_ports": connected_ports,
            })

        return connections

    def get_status(self) -> dict[str, Any]:
        """Get status of all OVS resources for debugging/monitoring."""
        return {
            "bridge": self._bridge_name,
            "initialized": self._initialized,
            "ports": [
                {
                    "key": key,
                    "port_name": p.port_name,
                    "container": p.container_name,
                    "interface": p.interface_name,
                    "vlan_tag": p.vlan_tag,
                    "lab_id": p.lab_id,
                }
                for key, p in self._ports.items()
            ],
            "links": [
                {
                    "link_id": l.link_id,
                    "lab_id": l.lab_id,
                    "port_a": l.port_a,
                    "port_b": l.port_b,
                    "vlan_tag": l.vlan_tag,
                }
                for l in self._links.values()
            ],
            "vlan_allocations": len(self._vlan_allocator._allocated),
        }

    # =========================================================================
    # Reconciliation Helpers
    # =========================================================================

    async def get_all_ovs_ports(self) -> list[dict[str, Any]]:
        """List all ports on the OVS bridge with their VLAN tags.

        Returns a list of dicts with:
        - port_name: Name of the OVS port
        - vlan_tag: VLAN tag (or None for trunk)
        - type: Port type (system, internal, vxlan, etc.)

        Used for reconciliation to compare OVS reality with tracked state.
        """
        if not self._initialized:
            return []

        ports = []

        code, stdout, _ = await self._ovs_vsctl("list-ports", self._bridge_name)
        if code != 0:
            return ports

        port_names = [p.strip() for p in stdout.strip().split("\n") if p.strip()]

        for port_name in port_names:
            port_info: dict[str, Any] = {"port_name": port_name}

            # Get VLAN tag
            code, tag_stdout, _ = await self._ovs_vsctl("get", "port", port_name, "tag")
            if code == 0:
                tag_str = tag_stdout.strip()
                if tag_str and tag_str != "[]":
                    try:
                        port_info["vlan_tag"] = int(tag_str)
                    except ValueError:
                        port_info["vlan_tag"] = None
                else:
                    port_info["vlan_tag"] = None
            else:
                port_info["vlan_tag"] = None

            # Get port type
            code, type_stdout, _ = await self._ovs_vsctl("get", "interface", port_name, "type")
            if code == 0:
                port_info["type"] = type_stdout.strip().strip('"') or "system"
            else:
                port_info["type"] = "unknown"

            # Get interface error (e.g. "No such device" for stale ports)
            code, err_stdout, _ = await self._ovs_vsctl("get", "interface", port_name, "error")
            if code == 0:
                err_str = err_stdout.strip().strip('"')
                port_info["error"] = err_str if err_str and err_str != "[]" else ""
            else:
                port_info["error"] = ""

            ports.append(port_info)

        return ports

    async def delete_orphan_port(self, port_name: str) -> bool:
        """Remove a port that's not tracked in our state.

        This is used during reconciliation to clean up ports that exist
        in OVS but are not in our tracking (e.g., after a crash).

        Args:
            port_name: Name of the OVS port to delete

        Returns:
            True if deleted successfully
        """
        if not self._initialized:
            return False

        # First check it's not one of our tracked ports
        for port in self._ports.values():
            if port.port_name == port_name:
                logger.warning(f"Refusing to delete tracked port {port_name}")
                return False

        # Remove from OVS
        code, _, stderr = await self._ovs_vsctl(
            "--if-exists", "del-port", self._bridge_name, port_name
        )
        if code != 0:
            logger.warning(f"Failed to delete orphan port {port_name}: {stderr}")
            return False

        # Also delete the veth pair if it exists
        await self._run_cmd(["ip", "link", "delete", port_name])

        logger.info(f"Deleted orphan OVS port: {port_name}")
        return True

    async def get_ovs_bridge_state(self) -> dict[str, Any]:
        """Get full OVS bridge state for comparison/debugging.

        Returns comprehensive bridge state including:
        - bridge_exists: Whether the bridge exists
        - bridge_name: Name of the bridge
        - ports: List of port info dicts
        - tracked_ports: List of ports we're tracking
        - orphaned_ports: Ports in OVS but not tracked
        - missing_ports: Ports tracked but not in OVS
        """
        if not self._initialized:
            return {
                "bridge_exists": False,
                "bridge_name": self._bridge_name,
                "ports": [],
                "tracked_ports": [],
                "orphaned_ports": [],
                "missing_ports": [],
            }

        # Check bridge exists
        code, _, _ = await self._ovs_vsctl("br-exists", self._bridge_name)
        bridge_exists = code == 0

        if not bridge_exists:
            return {
                "bridge_exists": False,
                "bridge_name": self._bridge_name,
                "ports": [],
                "tracked_ports": list(self._ports.keys()),
                "orphaned_ports": [],
                "missing_ports": list(self._ports.keys()),
            }

        # Get all OVS ports
        ovs_ports = await self.get_all_ovs_ports()
        ovs_port_names = {p["port_name"] for p in ovs_ports}

        # Get tracked port names
        tracked_port_names = {p.port_name for p in self._ports.values()}

        # Calculate orphaned and missing
        # Only consider vh* ports orphaned if the underlying interface no longer
        # exists (stale device). Ports with live interfaces belong to running
        # containers created by the Docker OVS plugin and must not be deleted.
        orphaned = []
        for p in ovs_ports:
            if p["port_name"] not in tracked_port_names and p["port_name"].startswith("vh"):
                # Check if the interface still exists in the kernel
                error = p.get("error", "")
                if "No such device" in error:
                    orphaned.append(p)
        missing = [key for key, port in self._ports.items()
                   if port.port_name not in ovs_port_names]

        return {
            "bridge_exists": True,
            "bridge_name": self._bridge_name,
            "ports": ovs_ports,
            "tracked_ports": list(self._ports.keys()),
            "orphaned_ports": orphaned,
            "missing_ports": missing,
        }

    async def reconcile_with_ovs(self) -> dict[str, Any]:
        """Reconcile tracked state with actual OVS state.

        This method:
        1. Removes tracking for ports that no longer exist in OVS
        2. Cleans up orphaned OVS ports not in our tracking
        3. Updates VLAN tags if they've drifted

        Returns summary of reconciliation actions.
        """
        stats = {
            "tracked_removed": 0,
            "orphans_deleted": 0,
            "vlans_updated": 0,
            "errors": [],
        }

        if not self._initialized:
            return stats

        bridge_state = await self.get_ovs_bridge_state()

        if not bridge_state["bridge_exists"]:
            # Bridge is gone - clear all tracking
            stats["tracked_removed"] = len(self._ports)
            self._ports.clear()
            self._links.clear()
            logger.warning(f"Bridge {self._bridge_name} missing, cleared all tracking")
            return stats

        # Get OVS port info indexed by name
        ovs_by_name = {p["port_name"]: p for p in bridge_state["ports"]}

        # Remove tracking for missing ports
        for missing_key in bridge_state["missing_ports"]:
            port = self._ports.pop(missing_key, None)
            if port:
                self._vlan_allocator.release(missing_key)
                # Remove any links involving this port
                links_to_remove = [
                    lk for lk, lv in self._links.items()
                    if lv.port_a == missing_key or lv.port_b == missing_key
                ]
                for lk in links_to_remove:
                    del self._links[lk]
                stats["tracked_removed"] += 1

        # Delete orphaned ports
        for orphan in bridge_state["orphaned_ports"]:
            try:
                await self.delete_orphan_port(orphan["port_name"])
                stats["orphans_deleted"] += 1
            except Exception as e:
                stats["errors"].append(f"Failed to delete orphan {orphan['port_name']}: {e}")

        # Check VLAN tag consistency
        for key, port in self._ports.items():
            if port.port_name in ovs_by_name:
                ovs_vlan = ovs_by_name[port.port_name].get("vlan_tag")
                if ovs_vlan is not None and ovs_vlan != port.vlan_tag:
                    # OVS has different VLAN - update our tracking
                    logger.info(
                        f"Port {port.port_name} VLAN drift: tracked={port.vlan_tag}, "
                        f"actual={ovs_vlan}"
                    )
                    port.vlan_tag = ovs_vlan
                    stats["vlans_updated"] += 1

        if any(v > 0 for k, v in stats.items() if k != "errors"):
            logger.info(f"OVS reconciliation: {stats}")

        return stats


# Module-level singleton accessor
_ovs_manager: OVSNetworkManager | None = None


def get_ovs_manager() -> OVSNetworkManager:
    """Get the global OVSNetworkManager instance."""
    global _ovs_manager
    if _ovs_manager is None:
        _ovs_manager = OVSNetworkManager()
    return _ovs_manager
