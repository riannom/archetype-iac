"""VXLAN overlay networking for multi-host lab connectivity.

This module provides VXLAN tunnel management for connecting lab nodes
across multiple hosts. It handles:
- VXLAN tunnel creation via OVS
- OVS bridge port management with VLAN isolation
- Attaching container interfaces to overlay
- Tunnel establishment between hosts

VXLAN Overview:
- Encapsulates L2 frames in UDP (default port 4789)
- Uses VNI (VXLAN Network Identifier) for isolation
- Each cross-host link gets a unique VNI
- Point-to-point tunnels between agent hosts

OVS Implementation:
- Uses a single OVS bridge (arch-ovs) for all overlay traffic
- VLAN tags isolate traffic between different links
- VXLAN ports created on OVS for cross-host tunneling
- Standalone fail-mode for normal L2 switching behavior
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

# VXLAN default port
VXLAN_PORT = 4789

# VLAN tag range for overlay isolation (within OVS)
# Use a subset to avoid conflicts with other OVS users
OVERLAY_VLAN_BASE = 3000
OVERLAY_VLAN_MAX = 4000


@dataclass
class VxlanTunnel:
    """Represents a VXLAN tunnel to another host."""

    vni: int  # VXLAN Network Identifier
    local_ip: str  # Local host IP for VXLAN endpoint
    remote_ip: str  # Remote host IP for VXLAN endpoint
    interface_name: str  # Name of the OVS VXLAN port (e.g., vxlan100000)
    lab_id: str  # Lab this tunnel belongs to
    link_id: str  # Identifier for the link (e.g., "node1:eth0-node2:eth0")
    vlan_tag: int  # VLAN tag for OVS isolation

    @property
    def key(self) -> str:
        """Unique key for this tunnel."""
        return f"{self.lab_id}:{self.link_id}"


@dataclass
class OverlayBridge:
    """Represents an OVS-based overlay for a link.

    Note: With OVS, we don't create separate bridges. Instead, we use
    VLAN tags on the shared arch-ovs bridge for isolation. This class
    tracks the VLAN tag and associated ports for a link.
    """

    name: str  # OVS bridge name (always arch-ovs)
    vni: int  # Associated VNI
    vlan_tag: int  # VLAN tag for isolation
    lab_id: str
    link_id: str
    veth_pairs: list[tuple[str, str]] = field(default_factory=list)  # (host_end, container_end)

    @property
    def key(self) -> str:
        """Unique key for this bridge."""
        return f"{self.lab_id}:{self.link_id}"


class OverlayManager:
    """Manages VXLAN overlay networks for multi-host labs using OVS.

    This class handles the creation and cleanup of VXLAN tunnels and
    container attachments using Open vSwitch for improved reliability.

    Key differences from Linux bridge implementation:
    - Uses single OVS bridge (arch-ovs) with VLAN isolation
    - VXLAN tunnels created as OVS ports, not Linux interfaces
    - Standalone fail-mode enables normal L2 switching
    - More reliable unicast forwarding

    Usage:
        manager = OverlayManager()

        # Create a tunnel to another host
        tunnel = await manager.create_tunnel(
            lab_id="lab123",
            link_id="r1:eth0-r2:eth0",
            local_ip="192.168.1.10",
            remote_ip="192.168.1.20",
        )

        # Create a bridge and attach container
        bridge = await manager.create_bridge(tunnel)
        await manager.attach_container(bridge, "clab-lab123-r1", "eth1")

        # Clean up when done
        await manager.cleanup_lab("lab123")
    """

    def __init__(self):
        self._docker: docker.DockerClient | None = None
        self._tunnels: dict[str, VxlanTunnel] = {}  # key -> tunnel
        self._bridges: dict[str, OverlayBridge] = {}  # key -> bridge
        self._vni_allocator = VniAllocator()
        self._ovs_initialized = False
        self._bridge_name = settings.ovs_bridge_name  # Default: "arch-ovs"

    @property
    def docker(self) -> docker.DockerClient:
        """Lazy-initialize Docker client."""
        if self._docker is None:
            self._docker = docker.from_env()
        return self._docker

    async def _run_cmd(self, cmd: list[str]) -> tuple[int, str, str]:
        """Run a shell command asynchronously."""
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
        """Run ovs-vsctl command."""
        cmd = ["ovs-vsctl"] + list(args)
        return await self._run_cmd(cmd)

    async def _ensure_ovs_bridge(self) -> None:
        """Ensure OVS bridge exists and is configured for overlay use."""
        if self._ovs_initialized:
            return

        # Check if OVS is available
        code, _, stderr = await self._ovs_vsctl("--version")
        if code != 0:
            raise RuntimeError(f"OVS not available: {stderr}")

        # Check if bridge exists
        code, _, _ = await self._ovs_vsctl("br-exists", self._bridge_name)
        if code != 0:
            # Bridge doesn't exist, create it
            logger.info(f"Creating OVS bridge for overlay: {self._bridge_name}")
            code, _, stderr = await self._ovs_vsctl("add-br", self._bridge_name)
            if code != 0:
                raise RuntimeError(f"Failed to create OVS bridge: {stderr}")

        # Set fail mode to standalone for normal L2 switching
        # This is critical - secure mode drops all traffic without flows
        code, stdout, _ = await self._ovs_vsctl("get", "bridge", self._bridge_name, "fail_mode")
        current_mode = stdout.strip().strip('"')
        if current_mode != "standalone":
            logger.info(f"Setting OVS bridge {self._bridge_name} to standalone mode")
            await self._ovs_vsctl("set-fail-mode", self._bridge_name, "standalone")

        # Bring bridge up
        await self._run_cmd(["ip", "link", "set", self._bridge_name, "up"])

        self._ovs_initialized = True
        logger.info(f"OVS bridge {self._bridge_name} ready for overlay")

    async def _ip_link_exists(self, name: str) -> bool:
        """Check if a network interface exists."""
        code, _, _ = await self._run_cmd(["ip", "link", "show", name])
        return code == 0

    async def _ovs_port_exists(self, port_name: str) -> bool:
        """Check if an OVS port exists on the bridge."""
        code, stdout, _ = await self._ovs_vsctl("list-ports", self._bridge_name)
        if code != 0:
            return False
        ports = stdout.strip().split("\n")
        return port_name in ports

    def _vni_to_vlan(self, vni: int) -> int:
        """Convert VNI to a VLAN tag for OVS isolation."""
        # Map VNI to VLAN range to avoid conflicts
        return OVERLAY_VLAN_BASE + (vni % (OVERLAY_VLAN_MAX - OVERLAY_VLAN_BASE))

    async def create_tunnel(
        self,
        lab_id: str,
        link_id: str,
        local_ip: str,
        remote_ip: str,
        vni: int | None = None,
    ) -> VxlanTunnel:
        """Create a VXLAN tunnel to another host using OVS.

        Args:
            lab_id: Lab identifier
            link_id: Link identifier (e.g., "node1:eth0-node2:eth0")
            local_ip: Local host IP address for VXLAN endpoint
            remote_ip: Remote host IP address for VXLAN endpoint
            vni: Optional VNI (auto-allocated if not specified)

        Returns:
            VxlanTunnel object representing the created tunnel

        Raises:
            RuntimeError: If tunnel creation fails
        """
        await self._ensure_ovs_bridge()

        key = f"{lab_id}:{link_id}"

        # Check if tunnel already exists
        if key in self._tunnels:
            logger.info(f"Tunnel already exists: {key}")
            return self._tunnels[key]

        # Allocate VNI if not provided
        if vni is None:
            vni = self._vni_allocator.allocate(lab_id, link_id)

        # Create interface name from VNI
        interface_name = f"vxlan{vni}"
        vlan_tag = self._vni_to_vlan(vni)

        # Delete existing OVS port if present (from previous run)
        if await self._ovs_port_exists(interface_name):
            logger.warning(f"VXLAN port {interface_name} already exists, deleting")
            await self._ovs_vsctl("--if-exists", "del-port", self._bridge_name, interface_name)

        # Also clean up any Linux VXLAN interface with same name
        if await self._ip_link_exists(interface_name):
            logger.warning(f"Linux VXLAN interface {interface_name} exists, deleting")
            await self._run_cmd(["ip", "link", "delete", interface_name])

        # Create VXLAN port on OVS
        # Format: ovs-vsctl -- add-port br vxlan123 tag=500 -- set interface vxlan123 type=vxlan options:...
        code, _, stderr = await self._ovs_vsctl(
            "--", "add-port", self._bridge_name, interface_name, f"tag={vlan_tag}",
            "--", "set", "interface", interface_name, "type=vxlan",
            f"options:remote_ip={remote_ip}",
            f"options:local_ip={local_ip}",
            f"options:key={vni}",
        )
        if code != 0:
            raise RuntimeError(f"Failed to create VXLAN port on OVS: {stderr}")

        tunnel = VxlanTunnel(
            vni=vni,
            local_ip=local_ip,
            remote_ip=remote_ip,
            interface_name=interface_name,
            lab_id=lab_id,
            link_id=link_id,
            vlan_tag=vlan_tag,
        )

        self._tunnels[key] = tunnel
        logger.info(f"Created OVS VXLAN tunnel: {interface_name} (VNI {vni}, VLAN {vlan_tag}) to {remote_ip}")

        return tunnel

    async def delete_tunnel(self, tunnel: VxlanTunnel) -> bool:
        """Delete a VXLAN tunnel.

        Args:
            tunnel: The tunnel to delete

        Returns:
            True if deleted successfully, False otherwise
        """
        try:
            # Delete OVS VXLAN port
            code, _, stderr = await self._ovs_vsctl(
                "--if-exists", "del-port", self._bridge_name, tunnel.interface_name
            )
            if code != 0:
                logger.warning(f"Failed to delete OVS VXLAN port {tunnel.interface_name}: {stderr}")

            # Release VNI
            self._vni_allocator.release(tunnel.lab_id, tunnel.link_id)

            # Remove from tracking
            if tunnel.key in self._tunnels:
                del self._tunnels[tunnel.key]

            logger.info(f"Deleted VXLAN tunnel: {tunnel.interface_name}")
            return True

        except Exception as e:
            logger.error(f"Error deleting tunnel: {e}")
            return False

    async def create_bridge(self, tunnel: VxlanTunnel) -> OverlayBridge:
        """Create an overlay bridge entry for a tunnel.

        With OVS, we don't create separate bridges. Instead, we use
        VLAN tags on the shared arch-ovs bridge for isolation.

        Args:
            tunnel: The VXLAN tunnel to bridge

        Returns:
            OverlayBridge object

        Raises:
            RuntimeError: If bridge creation fails
        """
        await self._ensure_ovs_bridge()

        key = tunnel.key

        # Check if bridge already exists
        if key in self._bridges:
            logger.info(f"Bridge already exists for: {key}")
            return self._bridges[key]

        # With OVS, the "bridge" is just tracking - no actual bridge creation needed
        # The VLAN tag provides isolation within the shared arch-ovs bridge
        bridge = OverlayBridge(
            name=self._bridge_name,
            vni=tunnel.vni,
            vlan_tag=tunnel.vlan_tag,
            lab_id=tunnel.lab_id,
            link_id=tunnel.link_id,
        )

        self._bridges[key] = bridge
        logger.info(f"Created overlay bridge entry for VNI {tunnel.vni} (VLAN {tunnel.vlan_tag})")

        return bridge

    async def delete_bridge(self, bridge: OverlayBridge) -> bool:
        """Delete a bridge and its veth pairs.

        Args:
            bridge: The bridge to delete

        Returns:
            True if deleted successfully
        """
        try:
            # Delete veth pairs from OVS and system
            for host_end, _ in bridge.veth_pairs:
                # Remove from OVS
                await self._ovs_vsctl("--if-exists", "del-port", self._bridge_name, host_end)
                # Delete the veth pair
                await self._run_cmd(["ip", "link", "delete", host_end])

            # Remove from tracking
            if bridge.key in self._bridges:
                del self._bridges[bridge.key]

            logger.info(f"Deleted overlay bridge entry: VNI {bridge.vni}")
            return True

        except Exception as e:
            logger.error(f"Error deleting bridge: {e}")
            return False

    async def attach_container(
        self,
        bridge: OverlayBridge,
        container_name: str,
        interface_name: str,
        ip_address: str | None = None,
    ) -> bool:
        """Attach a container interface to the overlay bridge.

        This creates a veth pair, moves one end into the container namespace,
        and attaches the other end to the OVS bridge with the appropriate VLAN tag.

        Args:
            bridge: The bridge to attach to
            container_name: Docker container name
            interface_name: Interface name inside container (e.g., eth1)
            ip_address: Optional IP address in CIDR format (e.g., "10.0.0.1/24")

        Returns:
            True if attached successfully
        """
        try:
            await self._ensure_ovs_bridge()

            # Get container PID for network namespace (wrapped to avoid blocking)
            def _sync_get_container_info():
                container = self.docker.containers.get(container_name)
                if container.status != "running":
                    return None, "not running"
                pid = container.attrs["State"]["Pid"]
                if not pid:
                    return None, "no PID"
                return pid, None

            pid, error = await asyncio.to_thread(_sync_get_container_info)
            if pid is None:
                logger.error(f"Container {container_name}: {error}")
                return False

            # Create unique veth names with random suffix to ensure unique MACs
            suffix = secrets.token_hex(2)  # 4 hex chars
            veth_host = f"v{bridge.vni % 10000}{suffix}h"[:15]  # Max 15 chars
            veth_cont = f"v{bridge.vni % 10000}{suffix}c"[:15]

            # Delete if exists
            await self._ovs_vsctl("--if-exists", "del-port", self._bridge_name, veth_host)
            await self._run_cmd(["ip", "link", "delete", veth_host])

            # Create veth pair
            code, _, stderr = await self._run_cmd([
                "ip", "link", "add", veth_host, "type", "veth", "peer", "name", veth_cont
            ])
            if code != 0:
                raise RuntimeError(f"Failed to create veth pair: {stderr}")

            # Set MTU on veth pair if configured
            if settings.overlay_mtu > 0:
                await self._run_cmd([
                    "ip", "link", "set", veth_host, "mtu", str(settings.overlay_mtu)
                ])
                await self._run_cmd([
                    "ip", "link", "set", veth_cont, "mtu", str(settings.overlay_mtu)
                ])

            # Add host end to OVS with VLAN tag
            code, _, stderr = await self._ovs_vsctl(
                "add-port", self._bridge_name, veth_host, f"tag={bridge.vlan_tag}"
            )
            if code != 0:
                await self._run_cmd(["ip", "link", "delete", veth_host])
                raise RuntimeError(f"Failed to add veth to OVS: {stderr}")

            # Bring host end up
            await self._run_cmd(["ip", "link", "set", veth_host, "up"])

            # Move container end to container namespace
            code, _, stderr = await self._run_cmd([
                "ip", "link", "set", veth_cont, "netns", str(pid)
            ])
            if code != 0:
                await self._ovs_vsctl("--if-exists", "del-port", self._bridge_name, veth_host)
                await self._run_cmd(["ip", "link", "delete", veth_host])
                raise RuntimeError(f"Failed to move veth to container namespace: {stderr}")

            # Delete any existing interface with target name (e.g., dummy interfaces)
            await self._run_cmd([
                "nsenter", "-t", str(pid), "-n",
                "ip", "link", "delete", interface_name
            ])

            # Rename interface inside container and bring it up
            # Use nsenter to execute commands in container network namespace
            await self._run_cmd([
                "nsenter", "-t", str(pid), "-n",
                "ip", "link", "set", veth_cont, "name", interface_name
            ])
            await self._run_cmd([
                "nsenter", "-t", str(pid), "-n",
                "ip", "link", "set", interface_name, "up"
            ])

            # Configure IP address if provided
            if ip_address:
                code, _, stderr = await self._run_cmd([
                    "nsenter", "-t", str(pid), "-n",
                    "ip", "addr", "add", ip_address, "dev", interface_name
                ])
                if code != 0:
                    logger.warning(f"Failed to configure IP {ip_address} on {interface_name}: {stderr}")
                else:
                    logger.info(f"Configured IP {ip_address} on {interface_name}")

            # Track the veth pair
            bridge.veth_pairs.append((veth_host, interface_name))

            logger.info(f"Attached container {container_name} to OVS {self._bridge_name} via {interface_name} (VLAN {bridge.vlan_tag})")
            return True

        except NotFound:
            logger.error(f"Container {container_name} not found")
            return False
        except Exception as e:
            logger.error(f"Error attaching container to bridge: {e}")
            return False

    async def cleanup_lab(self, lab_id: str) -> dict[str, Any]:
        """Clean up all overlay networking for a lab.

        Args:
            lab_id: The lab to clean up

        Returns:
            Summary of cleanup actions
        """
        result = {
            "tunnels_deleted": 0,
            "bridges_deleted": 0,
            "vnis_released": 0,
            "errors": [],
        }

        # Find all tunnels and bridges for this lab
        tunnels_to_delete = [t for t in self._tunnels.values() if t.lab_id == lab_id]
        bridges_to_delete = [b for b in self._bridges.values() if b.lab_id == lab_id]

        # Delete bridges first (they reference tunnels)
        for bridge in bridges_to_delete:
            try:
                if await self.delete_bridge(bridge):
                    result["bridges_deleted"] += 1
            except Exception as e:
                result["errors"].append(f"Bridge VNI {bridge.vni}: {e}")

        # Delete tunnels
        for tunnel in tunnels_to_delete:
            try:
                if await self.delete_tunnel(tunnel):
                    result["tunnels_deleted"] += 1
            except Exception as e:
                result["errors"].append(f"Tunnel {tunnel.interface_name}: {e}")

        # Release all VNI allocations for this lab
        result["vnis_released"] = self._vni_allocator.release_lab(lab_id)

        logger.info(f"Lab {lab_id} overlay cleanup: {result}")
        return result

    async def recover_allocations(self) -> int:
        """Recover VNI allocations from system state on startup.

        Returns:
            Number of VNIs recovered
        """
        return await self._vni_allocator.recover_from_system()

    async def get_tunnels_for_lab(self, lab_id: str) -> list[VxlanTunnel]:
        """Get all tunnels for a lab."""
        return [t for t in self._tunnels.values() if t.lab_id == lab_id]

    async def get_bridges_for_lab(self, lab_id: str) -> list[OverlayBridge]:
        """Get all bridges for a lab."""
        return [b for b in self._bridges.values() if b.lab_id == lab_id]

    def get_tunnel_status(self) -> dict[str, Any]:
        """Get status of all tunnels for debugging/monitoring."""
        return {
            "ovs_bridge": self._bridge_name,
            "tunnels": [
                {
                    "vni": t.vni,
                    "interface": t.interface_name,
                    "local_ip": t.local_ip,
                    "remote_ip": t.remote_ip,
                    "lab_id": t.lab_id,
                    "link_id": t.link_id,
                    "vlan_tag": t.vlan_tag,
                }
                for t in self._tunnels.values()
            ],
            "bridges": [
                {
                    "name": b.name,
                    "vni": b.vni,
                    "vlan_tag": b.vlan_tag,
                    "lab_id": b.lab_id,
                    "link_id": b.link_id,
                    "veth_pairs": b.veth_pairs,
                }
                for b in self._bridges.values()
            ],
        }


class VniAllocator:
    """Allocates unique VNIs for VXLAN tunnels.

    Allocations are persisted to disk to survive agent restarts.
    On startup, the allocator recovers state from:
    1. Persisted allocation file (if exists)
    2. Scanning existing VXLAN interfaces on the system
    """

    def __init__(
        self,
        base: int | None = None,
        max_vni: int | None = None,
        persistence_path: Path | None = None,
    ):
        self._base = base if base is not None else settings.vxlan_vni_base
        self._max = max_vni if max_vni is not None else settings.vxlan_vni_max
        self._allocated: dict[str, int] = {}  # key -> vni
        self._next_vni = self._base

        # Persistence file path
        if persistence_path is None:
            workspace = Path(settings.workspace_path)
            workspace.mkdir(parents=True, exist_ok=True)
            persistence_path = workspace / "vni_allocations.json"
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
            self._next_vni = data.get("next_vni", self._base)

            # Validate loaded VNIs are in range
            valid_allocations = {}
            for key, vni in self._allocated.items():
                if self._base <= vni <= self._max:
                    valid_allocations[key] = vni
                else:
                    logger.warning(f"Ignoring out-of-range VNI allocation: {key}={vni}")

            self._allocated = valid_allocations
            logger.info(f"Loaded {len(self._allocated)} VNI allocations from disk")

        except Exception as e:
            logger.warning(f"Failed to load VNI allocations from disk: {e}")
            self._allocated = {}

    def _save_to_disk(self) -> None:
        """Save allocations to persistence file."""
        try:
            data = {
                "allocations": self._allocated,
                "next_vni": self._next_vni,
            }
            # Write atomically via temp file
            tmp_path = self._persistence_path.with_suffix(".tmp")
            with open(tmp_path, "w") as f:
                json.dump(data, f, indent=2)
            tmp_path.rename(self._persistence_path)

        except Exception as e:
            logger.warning(f"Failed to save VNI allocations to disk: {e}")

    async def recover_from_system(self) -> int:
        """Scan existing VXLAN interfaces/ports and recover allocations.

        This should be called on agent startup to detect VNIs in use
        that may not be in the persisted file (e.g., after crash).

        Returns:
            Number of VNIs recovered from system state
        """
        recovered = 0
        used_vnis = set(self._allocated.values())

        try:
            # Check OVS VXLAN ports first
            proc = await asyncio.create_subprocess_exec(
                "ovs-vsctl", "list-ports", settings.ovs_bridge_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()

            if proc.returncode == 0 and stdout:
                ports = stdout.decode().strip().split("\n")
                for port in ports:
                    if port.startswith("vxlan"):
                        try:
                            vni = int(port[5:])  # Extract VNI from name
                            if self._base <= vni <= self._max and vni not in used_vnis:
                                placeholder_key = f"_recovered:{port}"
                                self._allocated[placeholder_key] = vni
                                used_vnis.add(vni)
                                recovered += 1
                                logger.info(f"Recovered VNI {vni} from OVS port {port}")
                        except ValueError:
                            continue

            # Also check Linux VXLAN interfaces (legacy)
            proc = await asyncio.create_subprocess_exec(
                "ip", "-j", "link", "show", "type", "vxlan",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()

            if proc.returncode == 0 and stdout:
                interfaces = json.loads(stdout.decode()) if stdout else []

                for iface in interfaces:
                    name = iface.get("ifname", "")
                    if name.startswith("vxlan"):
                        try:
                            vni = int(name[5:])
                            if self._base <= vni <= self._max and vni not in used_vnis:
                                placeholder_key = f"_recovered:{name}"
                                self._allocated[placeholder_key] = vni
                                used_vnis.add(vni)
                                recovered += 1
                                logger.info(f"Recovered VNI {vni} from Linux interface {name}")
                        except ValueError:
                            continue

            if recovered > 0:
                self._save_to_disk()
                logger.info(f"Recovered {recovered} VNIs from system state")

        except Exception as e:
            logger.warning(f"Failed to recover VNIs from system: {e}")

        return recovered

    def allocate(self, lab_id: str, link_id: str) -> int:
        """Allocate a VNI for a link.

        Args:
            lab_id: Lab identifier
            link_id: Link identifier

        Returns:
            Allocated VNI

        Raises:
            RuntimeError: If no VNIs available
        """
        key = f"{lab_id}:{link_id}"

        # Return existing allocation if present
        if key in self._allocated:
            return self._allocated[key]

        # Find next available VNI
        attempts = 0
        while self._next_vni in self._allocated.values():
            self._next_vni += 1
            if self._next_vni > self._max:
                self._next_vni = self._base
            attempts += 1
            if attempts > (self._max - self._base):
                raise RuntimeError("No VNIs available")

        vni = self._next_vni
        self._allocated[key] = vni
        self._next_vni += 1

        if self._next_vni > self._max:
            self._next_vni = self._base

        # Persist allocation
        self._save_to_disk()

        return vni

    def release(self, lab_id: str, link_id: str) -> None:
        """Release a VNI allocation."""
        key = f"{lab_id}:{link_id}"
        if key in self._allocated:
            del self._allocated[key]
            self._save_to_disk()

    def release_lab(self, lab_id: str) -> int:
        """Release all VNI allocations for a lab.

        Args:
            lab_id: Lab identifier

        Returns:
            Number of allocations released
        """
        prefix = f"{lab_id}:"
        keys_to_remove = [k for k in self._allocated if k.startswith(prefix)]

        for key in keys_to_remove:
            del self._allocated[key]

        if keys_to_remove:
            self._save_to_disk()
            logger.info(f"Released {len(keys_to_remove)} VNI allocations for lab {lab_id}")

        return len(keys_to_remove)

    def get_vni(self, lab_id: str, link_id: str) -> int | None:
        """Get VNI for a link, or None if not allocated."""
        return self._allocated.get(f"{lab_id}:{link_id}")

    def get_stats(self) -> dict[str, Any]:
        """Get allocator statistics for monitoring."""
        return {
            "total_allocated": len(self._allocated),
            "vni_range": f"{self._base}-{self._max}",
            "next_vni": self._next_vni,
            "persistence_path": str(self._persistence_path),
        }
