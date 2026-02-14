"""VXLAN overlay networking for multi-host lab connectivity.

This module provides VXLAN tunnel management for connecting lab nodes
across multiple hosts. It handles:
- VXLAN tunnel creation via Linux VXLAN devices + OVS
- OVS bridge port management with VLAN isolation
- Attaching container interfaces to overlay
- Tunnel establishment between hosts

VXLAN Overview:
- Encapsulates L2 frames in UDP (default port 4789)
- Uses VNI (VXLAN Network Identifier) for isolation
- Each cross-host link gets a unique VNI
- Point-to-point tunnels between agent hosts

Implementation:
- Uses Linux VXLAN devices with `nopmtudisc` for MTU transparency
- Devices added to OVS bridge (arch-ovs) as system ports
- VLAN tags isolate traffic between different links
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
from agent.network.cmd import run_cmd as _shared_run_cmd, ovs_vsctl as _shared_ovs_vsctl, ip_link_exists as _shared_ip_link_exists
from agent.providers.naming import docker_container_name as _build_container_name, DOCKER_PREFIX as CONTAINER_PREFIX


logger = logging.getLogger(__name__)

# VXLAN default port
VXLAN_PORT = 4789

# VLAN tag range for overlay isolation (within OVS)
# Use a subset to avoid conflicts with other OVS users
OVERLAY_VLAN_BASE = 3000
OVERLAY_VLAN_MAX = 4000


@dataclass
class VxlanTunnel:
    """Represents a VXLAN tunnel to another host (legacy per-link model)."""

    vni: int  # VXLAN Network Identifier
    local_ip: str  # Local host IP for VXLAN endpoint
    remote_ip: str  # Remote host IP for VXLAN endpoint
    interface_name: str  # Name of the OVS VXLAN port (e.g., vxlan100000)
    lab_id: str  # Lab this tunnel belongs to
    link_id: str  # Identifier for the link (e.g., "node1:eth0-node2:eth0")
    vlan_tag: int  # VLAN tag for OVS isolation
    tenant_mtu: int = 0  # Discovered tenant MTU (path MTU - VXLAN overhead), 0 = use default

    @property
    def key(self) -> str:
        """Unique key for this tunnel."""
        return f"{self.lab_id}:{self.link_id}"


@dataclass
class Vtep:
    """Represents a VXLAN Tunnel Endpoint to a remote host (legacy trunk model).

    In the old architecture, there was one VTEP per remote host (not per link).
    The VTEP was created in trunk mode (no VLAN tag) and carried traffic for
    all cross-host links to that remote host. This required both sides to use
    matching VLANs, which was fragile.

    Deprecated: Use LinkTunnel (per-link VNI model) instead.
    """

    interface_name: str  # OVS port name (e.g., "vtep-10.0.0.2")
    vni: int  # Derived from host-pair hash (deterministic)
    local_ip: str  # Local VXLAN endpoint IP
    remote_ip: str  # Remote VXLAN endpoint IP
    remote_host_id: str | None = None  # Optional remote host identifier
    tenant_mtu: int = 0  # Discovered path MTU minus VXLAN overhead
    links: set[str] = field(default_factory=set)  # link_ids using this VTEP

    @property
    def key(self) -> str:
        """Unique key for this VTEP (keyed by remote IP)."""
        return self.remote_ip

    @property
    def link_count(self) -> int:
        """Number of links using this VTEP."""
        return len(self.links)


@dataclass
class LinkTunnel:
    """Represents a per-link VXLAN tunnel port (access-mode).

    In the per-link VNI model, each cross-host link gets its own VXLAN port
    on OVS in access mode (with tag=). The VNI is the shared link identifier,
    and each side uses its container's local VLAN as the tag. This eliminates
    the need for VLAN coordination between hosts.

    OVS access-mode behavior:
    - Egress: strips the local VLAN tag, encapsulates with VNI
    - Ingress: decapsulates VNI, adds the local VLAN tag
    """

    link_id: str  # Unique link identifier
    vni: int  # VXLAN Network Identifier (shared between both sides)
    local_ip: str  # Local VXLAN endpoint IP
    remote_ip: str  # Remote VXLAN endpoint IP
    local_vlan: int  # Local container VLAN tag (access mode)
    interface_name: str  # OVS port name (e.g., "vxlan-abc12345")
    lab_id: str  # Lab this tunnel belongs to
    tenant_mtu: int = 0  # Discovered tenant MTU

    @property
    def key(self) -> str:
        """Unique key for this tunnel."""
        return self.link_id


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
    tenant_mtu: int = 0  # MTU for tenant interfaces (0 = use default)
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
        await manager.attach_container(bridge, "archetype-lab123-r1", "eth1")

        # Clean up when done
        await manager.cleanup_lab("lab123")
    """

    # VXLAN encapsulation overhead in bytes
    # 14 (outer Ethernet) + 20 (IP) + 8 (UDP) + 8 (VXLAN) = 50 bytes
    VXLAN_OVERHEAD = 50

    async def _read_vxlan_link_info(self, interface_name: str) -> tuple[int, str, str]:
        """Read VNI/remote/local from a Linux VXLAN device.

        Returns (vni, remote_ip, local_ip). Zero/empty values on failure.
        """
        code, link_out, _ = await self._run_cmd([
            "ip", "-d", "link", "show", interface_name
        ])
        if code != 0:
            return 0, "", ""

        vni = 0
        remote_ip = ""
        local_ip = ""
        parts = link_out.split()
        for i, part in enumerate(parts):
            if part == "id" and i + 1 < len(parts):
                try:
                    vni = int(parts[i + 1])
                except ValueError:
                    pass
            elif part == "remote" and i + 1 < len(parts):
                remote_ip = parts[i + 1]
            elif part == "local" and i + 1 < len(parts):
                local_ip = parts[i + 1]

        return vni, remote_ip, local_ip

    def __init__(self):
        self._docker: docker.DockerClient | None = None
        self._tunnels: dict[str, VxlanTunnel] = {}  # key -> tunnel (legacy)
        self._bridges: dict[str, OverlayBridge] = {}  # key -> bridge (legacy)
        self._vteps: dict[str, Vtep] = {}  # remote_ip -> VTEP (legacy trunk model)
        self._link_tunnels: dict[str, LinkTunnel] = {}  # link_id -> per-link VXLAN port
        self._vni_allocator = VniAllocator()
        self._ovs_initialized = False
        self._bridge_name = settings.ovs_bridge_name  # Default: "arch-ovs"
        self._mtu_cache: dict[str, int] = {}  # remote_ip -> discovered path MTU

    @property
    def docker(self) -> docker.DockerClient:
        """Lazy-initialize Docker client."""
        if self._docker is None:
            self._docker = docker.from_env()
        return self._docker

    async def _run_cmd(self, cmd: list[str]) -> tuple[int, str, str]:
        """Run a shell command asynchronously."""
        return await _shared_run_cmd(cmd)

    async def _ovs_vsctl(self, *args: str) -> tuple[int, str, str]:
        """Run ovs-vsctl command."""
        return await _shared_ovs_vsctl(*args)

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
        return await _shared_ip_link_exists(name)

    async def _ovs_port_exists(self, port_name: str) -> bool:
        """Check if an OVS port exists on the bridge."""
        code, stdout, _ = await self._ovs_vsctl("list-ports", self._bridge_name)
        if code != 0:
            return False
        ports = stdout.strip().split("\n")
        return port_name in ports

    async def _create_vxlan_device(
        self,
        name: str,
        vni: int,
        local_ip: str,
        remote_ip: str,
        bridge: str,
        vlan_tag: int | None = None,
        tenant_mtu: int = 0,
    ) -> None:
        """Create a Linux VXLAN device with nopmtudisc and add it to OVS.

        Uses Linux VXLAN devices instead of OVS-managed VXLAN ports so that
        nopmtudisc disables all PMTUD checking on the tunnel. This allows
        inner packets to pass through at full MTU while the kernel handles
        outer packet fragmentation transparently.

        Args:
            name: Interface name for the VXLAN device
            vni: VXLAN Network Identifier
            local_ip: Local IP for VXLAN endpoint
            remote_ip: Remote IP for VXLAN endpoint
            bridge: OVS bridge name to add the port to
            vlan_tag: Optional VLAN tag (access mode) or None (trunk mode)
            tenant_mtu: API-supplied overlay MTU (0 = use agent default)

        Raises:
            RuntimeError: If device creation fails
        """
        # Create Linux VXLAN device with df unset to allow outer fragmentation
        code, _, stderr = await self._run_cmd([
            "ip", "link", "add", name, "type", "vxlan",
            "id", str(vni), "local", local_ip, "remote", remote_ip,
            "dstport", str(VXLAN_PORT), "df", "unset",
        ])
        if code != 0:
            raise RuntimeError(f"Failed to create VXLAN device {name}: {stderr}")

        # Set MTU to desired overlay/tenant MTU (not local_mtu which is for veth pairs)
        # Priority: API-supplied tenant_mtu > agent config overlay_mtu > 1500 fallback
        # With df=unset, the kernel fragments oversized outer packets transparently,
        # so the VXLAN device can advertise the full tenant MTU (e.g. 1500) even when
        # the underlay path MTU is also 1500. This hides fragmentation from the overlay.
        vxlan_mtu = tenant_mtu if tenant_mtu > 0 else (settings.overlay_mtu if settings.overlay_mtu > 0 else 1500)
        await self._run_cmd(["ip", "link", "set", name, "mtu", str(vxlan_mtu)])

        # Bring device up
        await self._run_cmd(["ip", "link", "set", name, "up"])

        # Add to OVS bridge as a system port
        if vlan_tag is not None:
            code, _, stderr = await self._ovs_vsctl(
                "add-port", bridge, name, f"tag={vlan_tag}",
            )
        else:
            code, _, stderr = await self._ovs_vsctl(
                "add-port", bridge, name,
            )
        if code != 0:
            # Clean up the device on failure
            await self._run_cmd(["ip", "link", "delete", name])
            raise RuntimeError(f"Failed to add VXLAN device {name} to OVS: {stderr}")

    async def _delete_vxlan_device(self, name: str, bridge: str) -> None:
        """Remove a VXLAN device from OVS and delete the Linux interface.

        Args:
            name: Interface name of the VXLAN device
            bridge: OVS bridge name to remove the port from
        """
        await self._ovs_vsctl("--if-exists", "del-port", bridge, name)
        # Linux VXLAN devices added as system ports aren't auto-deleted
        await self._run_cmd(["ip", "link", "delete", name])

    async def _discover_path_mtu(self, remote_ip: str) -> int:
        """Discover the path MTU to a remote IP address.

        Uses ping with DF (Don't Fragment) bit set to find the maximum
        MTU that works on the path. Starts with jumbo frame size and
        does binary search down to find working MTU.

        Args:
            remote_ip: Target IP address to test

        Returns:
            Discovered path MTU, or 0 if discovery fails (use fallback)
        """
        # Check cache first
        if remote_ip in self._mtu_cache:
            cached = self._mtu_cache[remote_ip]
            logger.debug(f"Using cached MTU {cached} for {remote_ip}")
            return cached

        # MTU candidates to test (common values)
        # Start high and work down - most infrastructure supports at least 1500
        test_mtus = [9000, 4000, 1500]

        # Use data plane IP as source for MTU discovery pings when available
        from agent.network.transport import get_data_plane_ip
        dp_ip = get_data_plane_ip()

        async def test_mtu(mtu: int) -> bool:
            """Test if a specific MTU works."""
            # Payload = MTU - 20 (IP header) - 8 (ICMP header)
            payload_size = mtu - 28
            if payload_size < 0:
                return False

            try:
                ping_args = [
                    "ping",
                    "-M", "do",  # Don't fragment
                    "-c", "1",  # Single ping
                    "-W", "2",  # 2 second timeout
                    "-s", str(payload_size),
                ]
                if dp_ip:
                    ping_args.extend(["-I", dp_ip])
                ping_args.append(remote_ip)

                process = await asyncio.create_subprocess_exec(
                    *ping_args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=5.0,
                )

                if process.returncode == 0:
                    return True

                # Check for fragmentation error (MTU too large)
                combined = stdout.decode() + stderr.decode()
                if "message too long" in combined.lower() or "frag needed" in combined.lower():
                    return False

                # Other errors (host unreachable, etc.)
                return False

            except asyncio.TimeoutError:
                return False
            except Exception as e:
                logger.debug(f"MTU test error for {remote_ip} at {mtu}: {e}")
                return False

        # Test each MTU candidate
        discovered_mtu = 0
        for mtu in test_mtus:
            logger.debug(f"Testing MTU {mtu} to {remote_ip}")
            if await test_mtu(mtu):
                discovered_mtu = mtu
                logger.info(f"Path MTU to {remote_ip}: {mtu} bytes")
                break

        if discovered_mtu == 0:
            logger.warning(
                f"MTU discovery failed for {remote_ip}, will use fallback overlay_mtu={settings.overlay_mtu}"
            )
        else:
            # Cache the result
            self._mtu_cache[remote_ip] = discovered_mtu

        return discovered_mtu

    def _vni_to_vlan(self, vni: int) -> int:
        """Convert VNI to a VLAN tag for OVS isolation."""
        # Map VNI to VLAN range to avoid conflicts
        return OVERLAY_VLAN_BASE + (vni % (OVERLAY_VLAN_MAX - OVERLAY_VLAN_BASE))

    def _host_pair_vni(self, local_ip: str, remote_ip: str) -> int:
        """Generate a deterministic VNI for a host-pair.

        The VNI is derived from a hash of the sorted IP addresses, ensuring
        both hosts generate the same VNI for their shared VTEP.

        Args:
            local_ip: Local host IP address
            remote_ip: Remote host IP address

        Returns:
            Deterministic VNI in the configured range
        """
        import hashlib

        # Sort IPs to ensure same hash regardless of which side calls
        sorted_ips = tuple(sorted([local_ip, remote_ip]))
        combined = f"{sorted_ips[0]}:{sorted_ips[1]}"
        hash_val = int(hashlib.md5(combined.encode()).hexdigest()[:8], 16)
        # Map to VNI range (avoid first 1000 reserved)
        vni_range = self._vni_allocator._max - self._vni_allocator._base
        return self._vni_allocator._base + (hash_val % vni_range)

    async def ensure_vtep(
        self,
        local_ip: str,
        remote_ip: str,
        remote_host_id: str | None = None,
    ) -> Vtep:
        """Ensure a VTEP exists to the remote host.

        This implements the new trunk VTEP model where there is one VTEP
        per remote host, not one per link. The VTEP is created in trunk
        mode (no VLAN tag) and all cross-host links share it.

        Args:
            local_ip: Local host IP address for VXLAN endpoint
            remote_ip: Remote host IP address for VXLAN endpoint
            remote_host_id: Optional identifier for the remote host

        Returns:
            Vtep object (existing or newly created)

        Raises:
            RuntimeError: If VTEP creation fails
        """
        await self._ensure_ovs_bridge()

        # Check if VTEP already exists for this remote host
        if remote_ip in self._vteps:
            existing = self._vteps[remote_ip]
            logger.debug(f"VTEP already exists for {remote_ip}: {existing.interface_name}")
            return existing

        # Generate deterministic VNI from host-pair
        vni = self._host_pair_vni(local_ip, remote_ip)

        # Use overlay_mtu directly — with df=unset, outer fragmentation is transparent
        tenant_mtu = settings.overlay_mtu if settings.overlay_mtu > 0 else 1500
        logger.info(f"VTEP tenant MTU for {remote_ip}: {tenant_mtu}")

        # Create interface name from remote IP (replace dots with dashes)
        # e.g., "vtep-10-0-0-2" for remote IP 10.0.0.2
        safe_ip = remote_ip.replace(".", "-")
        interface_name = f"vtep-{safe_ip}"[:15]  # OVS port names max 15 chars

        # Delete existing port if present (from previous run)
        if await self._ovs_port_exists(interface_name):
            logger.warning(f"VTEP port {interface_name} already exists, deleting")
            await self._delete_vxlan_device(interface_name, self._bridge_name)

        # Also clean up any leftover Linux interface
        if await self._ip_link_exists(interface_name):
            await self._run_cmd(["ip", "link", "delete", interface_name])

        # Create Linux VXLAN device in TRUNK mode (no tag)
        await self._create_vxlan_device(
            name=interface_name,
            vni=vni,
            local_ip=local_ip,
            remote_ip=remote_ip,
            bridge=self._bridge_name,
            vlan_tag=None,
        )

        vtep = Vtep(
            interface_name=interface_name,
            vni=vni,
            local_ip=local_ip,
            remote_ip=remote_ip,
            remote_host_id=remote_host_id,
            tenant_mtu=tenant_mtu,
        )

        self._vteps[remote_ip] = vtep
        logger.info(
            f"Created trunk VTEP {interface_name} (VNI {vni}, MTU {tenant_mtu}) to {remote_ip}"
        )

        return vtep

    def get_vtep(self, remote_ip: str) -> Vtep | None:
        """Get the VTEP for a remote host, if it exists."""
        return self._vteps.get(remote_ip)

    def get_all_vteps(self) -> list[Vtep]:
        """Get all active VTEPs."""
        return list(self._vteps.values())

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

        # Use overlay_mtu directly — with df=unset, outer fragmentation is transparent
        tenant_mtu = settings.overlay_mtu if settings.overlay_mtu > 0 else 1500
        logger.info(f"Tunnel tenant MTU for {remote_ip}: {tenant_mtu}")

        # Create interface name from VNI
        interface_name = f"vxlan{vni}"
        vlan_tag = self._vni_to_vlan(vni)

        # Delete existing port if present (from previous run)
        if await self._ovs_port_exists(interface_name):
            logger.warning(f"VXLAN port {interface_name} already exists, deleting")
            await self._delete_vxlan_device(interface_name, self._bridge_name)

        # Also clean up any leftover Linux VXLAN interface
        if await self._ip_link_exists(interface_name):
            logger.warning(f"Linux VXLAN interface {interface_name} exists, deleting")
            await self._run_cmd(["ip", "link", "delete", interface_name])

        # Create Linux VXLAN device with access-mode VLAN tag
        await self._create_vxlan_device(
            name=interface_name,
            vni=vni,
            local_ip=local_ip,
            remote_ip=remote_ip,
            bridge=self._bridge_name,
            vlan_tag=vlan_tag,
        )

        tunnel = VxlanTunnel(
            vni=vni,
            local_ip=local_ip,
            remote_ip=remote_ip,
            interface_name=interface_name,
            lab_id=lab_id,
            link_id=link_id,
            vlan_tag=vlan_tag,
            tenant_mtu=tenant_mtu,
        )

        self._tunnels[key] = tunnel
        logger.info(
            f"Created OVS VXLAN tunnel: {interface_name} (VNI {vni}, VLAN {vlan_tag}, tenant MTU {tenant_mtu}) to {remote_ip}"
        )

        return tunnel

    async def delete_tunnel(self, tunnel: VxlanTunnel) -> bool:
        """Delete a VXLAN tunnel.

        Args:
            tunnel: The tunnel to delete

        Returns:
            True if deleted successfully, False otherwise
        """
        try:
            # Delete OVS port and Linux VXLAN device
            await self._delete_vxlan_device(tunnel.interface_name, self._bridge_name)

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
            tenant_mtu=tunnel.tenant_mtu,
        )

        self._bridges[key] = bridge
        logger.info(f"Created overlay bridge entry for VNI {tunnel.vni} (VLAN {tunnel.vlan_tag}, MTU {tunnel.tenant_mtu})")

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

            # Build full container name if only short name was provided.
            if not container_name.startswith(CONTAINER_PREFIX + "-"):
                full_container_name = _build_container_name(bridge.lab_id, container_name)
                logger.debug(
                    f"Expanded container name: {container_name} -> {full_container_name}"
                )
            else:
                full_container_name = container_name

            # Prefer using pre-provisioned OVS ports from the Docker OVS plugin.
            # This preserves interfaces created before boot (critical for cEOS).
            try:
                from agent.network.docker_plugin import get_docker_ovs_plugin

                plugin = get_docker_ovs_plugin()
                host_veth = await plugin.get_endpoint_host_veth(
                    bridge.lab_id, full_container_name, interface_name
                )
                if host_veth:
                    code, _, stderr = await self._ovs_vsctl(
                        "set", "port", host_veth, f"tag={bridge.vlan_tag}"
                    )
                    if code != 0:
                        raise RuntimeError(f"Failed to set VLAN on {host_veth}: {stderr}")

                    await self._run_cmd(["ip", "link", "set", host_veth, "up"])

                    logger.info(
                        f"Attached existing OVS port {host_veth} for "
                        f"{full_container_name}:{interface_name} to VLAN {bridge.vlan_tag}"
                    )
                    return True
            except Exception as e:
                logger.warning(
                    f"OVS plugin attach failed for {full_container_name}:{interface_name}, "
                    f"falling back to veth attach: {e}"
                )

            # Get container PID for network namespace (wrapped to avoid blocking)
            def _sync_get_container_info():
                container = self.docker.containers.get(full_container_name)
                if container.status != "running":
                    return None, "not running"
                pid = container.attrs["State"]["Pid"]
                if not pid:
                    return None, "no PID"
                return pid, None

            pid, error = await asyncio.to_thread(_sync_get_container_info)
            if pid is None:
                logger.error(f"Container {full_container_name}: {error}")
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

            # Set MTU for overlay link
            # Use bridge.tenant_mtu (overlay effective MTU) since containers on
            # overlay links shouldn't advertise jumbo MTU
            mtu_to_use = bridge.tenant_mtu if bridge.tenant_mtu > 0 else settings.overlay_mtu
            if mtu_to_use > 0 and settings.overlay_clamp_host_mtu:
                await self._run_cmd([
                    "ip", "link", "set", veth_host, "mtu", str(mtu_to_use)
                ])
                logger.debug(f"Set host veth MTU to {mtu_to_use} for overlay link")
            if mtu_to_use > 0 and not settings.overlay_preserve_container_mtu:
                await self._run_cmd([
                    "ip", "link", "set", veth_cont, "mtu", str(mtu_to_use)
                ])
                logger.debug(f"Set container veth MTU to {mtu_to_use} for overlay link")

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

            logger.info(f"Attached container {full_container_name} to OVS {self._bridge_name} via {interface_name} (VLAN {bridge.vlan_tag})")
            return True

        except NotFound:
            logger.error(f"Container {full_container_name} not found")
            return False
        except Exception as e:
            logger.error(f"Error attaching container to bridge: {e}")
            return False

    async def attach_overlay_interface(
        self,
        lab_id: str,
        container_name: str,
        interface_name: str,
        vlan_tag: int,
        tenant_mtu: int = 0,
        link_id: str | None = None,
        remote_ip: str | None = None,
    ) -> bool:
        """Attach a container interface to the overlay with a specific VLAN tag.

        This is the new method for the trunk VTEP model. It sets the VLAN tag
        directly on the container's OVS port. The VTEP should already exist
        (created via ensure_vtep) in trunk mode carrying all VLANs.

        Args:
            lab_id: Lab identifier (for logging/tracking)
            container_name: Docker container name
            interface_name: Interface name inside container (e.g., eth1)
            vlan_tag: VLAN tag for link isolation (must match remote side)
            tenant_mtu: MTU for the interface (0 = use default)
            link_id: Unique identifier for the link (for VTEP reference counting)
            remote_ip: Remote VTEP IP (for VTEP reference counting)

        Returns:
            True if attached successfully
        """
        try:
            await self._ensure_ovs_bridge()

            # Build full container name if only short name was provided.
            # The API may send short names (e.g., "eos_1") but Docker containers
            # use full names (e.g., "archetype-{lab_id}-eos_1").
            if not container_name.startswith(CONTAINER_PREFIX + "-"):
                full_container_name = _build_container_name(lab_id, container_name)
                logger.debug(
                    f"Expanded container name: {container_name} -> {full_container_name}"
                )
            else:
                full_container_name = container_name

            # Prefer using pre-provisioned OVS ports from the Docker OVS plugin.
            # This preserves interfaces created before boot (critical for cEOS).
            try:
                from agent.network.docker_plugin import get_docker_ovs_plugin

                plugin = get_docker_ovs_plugin()
                host_veth = await plugin.get_endpoint_host_veth(
                    lab_id, full_container_name, interface_name
                )
                if host_veth:
                    code, _, stderr = await self._ovs_vsctl(
                        "set", "port", host_veth, f"tag={vlan_tag}"
                    )
                    if code != 0:
                        raise RuntimeError(f"Failed to set VLAN on {host_veth}: {stderr}")

                    await self._run_cmd(["ip", "link", "set", host_veth, "up"])

                    # Update the docker plugin's in-memory state to stay in sync
                    # This ensures get_endpoint_vlan() returns the correct value
                    await plugin.set_endpoint_vlan(
                        lab_id, full_container_name, interface_name, vlan_tag
                    )

                    # Track link -> VTEP association for reference counting
                    if link_id and remote_ip and remote_ip in self._vteps:
                        self._vteps[remote_ip].links.add(link_id)
                        logger.debug(
                            f"VTEP {remote_ip} now has {self._vteps[remote_ip].link_count} links"
                        )

                    logger.info(
                        f"Attached OVS port {host_veth} for {full_container_name}:{interface_name} "
                        f"with VLAN {vlan_tag} (trunk VTEP model)"
                    )
                    return True
            except Exception as e:
                logger.warning(
                    f"OVS plugin attach failed for {full_container_name}:{interface_name}, "
                    f"falling back to veth attach: {e}"
                )

            # Fallback: Create new veth pair and attach to OVS
            def _sync_get_container_info():
                container = self.docker.containers.get(full_container_name)
                if container.status != "running":
                    return None, "not running"
                pid = container.attrs["State"]["Pid"]
                if not pid:
                    return None, "no PID"
                return pid, None

            pid, error = await asyncio.to_thread(_sync_get_container_info)
            if pid is None:
                logger.error(f"Container {full_container_name}: {error}")
                return False

            # Create unique veth names
            suffix = secrets.token_hex(2)
            veth_host = f"vo{vlan_tag % 10000}{suffix}"[:15]
            veth_cont = f"vc{vlan_tag % 10000}{suffix}"[:15]

            # Delete if exists
            await self._ovs_vsctl("--if-exists", "del-port", self._bridge_name, veth_host)
            await self._run_cmd(["ip", "link", "delete", veth_host])

            # Create veth pair
            code, _, stderr = await self._run_cmd([
                "ip", "link", "add", veth_host, "type", "veth", "peer", "name", veth_cont
            ])
            if code != 0:
                raise RuntimeError(f"Failed to create veth pair: {stderr}")

            # Set MTU - use tenant_mtu since containers on overlay links
            # shouldn't advertise jumbo MTU
            mtu_to_use = tenant_mtu if tenant_mtu > 0 else settings.overlay_mtu
            if mtu_to_use > 0:
                await self._run_cmd(["ip", "link", "set", veth_host, "mtu", str(mtu_to_use)])
                await self._run_cmd(["ip", "link", "set", veth_cont, "mtu", str(mtu_to_use)])

            # Add host end to OVS with VLAN tag
            code, _, stderr = await self._ovs_vsctl(
                "add-port", self._bridge_name, veth_host, f"tag={vlan_tag}"
            )
            if code != 0:
                await self._run_cmd(["ip", "link", "delete", veth_host])
                raise RuntimeError(f"Failed to add veth to OVS: {stderr}")

            await self._run_cmd(["ip", "link", "set", veth_host, "up"])

            # Move container end to container namespace
            code, _, stderr = await self._run_cmd([
                "ip", "link", "set", veth_cont, "netns", str(pid)
            ])
            if code != 0:
                await self._ovs_vsctl("--if-exists", "del-port", self._bridge_name, veth_host)
                await self._run_cmd(["ip", "link", "delete", veth_host])
                raise RuntimeError(f"Failed to move veth to container namespace: {stderr}")

            # Delete any existing interface with target name
            await self._run_cmd([
                "nsenter", "-t", str(pid), "-n",
                "ip", "link", "delete", interface_name
            ])

            # Rename and bring up
            await self._run_cmd([
                "nsenter", "-t", str(pid), "-n",
                "ip", "link", "set", veth_cont, "name", interface_name
            ])
            await self._run_cmd([
                "nsenter", "-t", str(pid), "-n",
                "ip", "link", "set", interface_name, "up"
            ])

            # Track link -> VTEP association for reference counting
            if link_id and remote_ip and remote_ip in self._vteps:
                self._vteps[remote_ip].links.add(link_id)
                logger.debug(
                    f"VTEP {remote_ip} now has {self._vteps[remote_ip].link_count} links"
                )

            logger.info(
                f"Attached {full_container_name}:{interface_name} to OVS "
                f"via {veth_host} with VLAN {vlan_tag} (trunk VTEP model)"
            )
            return True

        except NotFound:
            logger.error(f"Container {full_container_name} not found")
            return False
        except Exception as e:
            logger.error(f"Error attaching container interface: {e}")
            return False

    async def detach_overlay_interface(
        self,
        link_id: str,
        remote_ip: str,
        delete_vtep_if_unused: bool = True,
    ) -> dict[str, Any]:
        """Detach a link and optionally delete the VTEP if no more links use it.

        This implements VTEP reference counting cleanup. When the last link
        using a VTEP is detached, the VTEP can be deleted to free resources.

        Args:
            link_id: The link identifier to detach
            remote_ip: Remote VTEP IP address
            delete_vtep_if_unused: If True, delete VTEP when no links remain

        Returns:
            Dict with detach results:
            - success: bool
            - vtep_deleted: bool
            - remaining_links: int
            - error: str | None
        """
        result = {
            "success": False,
            "vtep_deleted": False,
            "remaining_links": 0,
            "error": None,
        }

        vtep = self._vteps.get(remote_ip)
        if not vtep:
            result["error"] = f"VTEP not found for remote IP {remote_ip}"
            logger.warning(result["error"])
            return result

        # Remove link from VTEP's reference set
        vtep.links.discard(link_id)
        result["remaining_links"] = vtep.link_count
        result["success"] = True

        logger.info(
            f"Detached link {link_id} from VTEP {remote_ip}, "
            f"{vtep.link_count} links remaining"
        )

        # Delete VTEP if no more links use it
        if delete_vtep_if_unused and vtep.link_count == 0:
            deleted = await self.delete_vtep(remote_ip)
            result["vtep_deleted"] = deleted
            if deleted:
                logger.info(f"Deleted unused VTEP {vtep.interface_name} to {remote_ip}")

        return result

    async def delete_vtep(self, remote_ip: str) -> bool:
        """Delete a VTEP and its Linux VXLAN device.

        This should only be called when no links are using the VTEP.
        Use detach_overlay_interface for safe reference-counted deletion.

        Args:
            remote_ip: Remote IP of the VTEP to delete

        Returns:
            True if deleted successfully, False otherwise
        """
        vtep = self._vteps.get(remote_ip)
        if not vtep:
            logger.warning(f"VTEP not found for remote IP {remote_ip}")
            return False

        # Safety check: warn if links still reference this VTEP
        if vtep.link_count > 0:
            logger.warning(
                f"Deleting VTEP {vtep.interface_name} with {vtep.link_count} remaining links"
            )

        try:
            # Delete OVS port and Linux VXLAN device
            await self._delete_vxlan_device(vtep.interface_name, self._bridge_name)

            # Remove from tracking
            del self._vteps[remote_ip]
            logger.info(f"Deleted VTEP {vtep.interface_name} to {remote_ip}")
            return True

        except Exception as e:
            logger.error(f"Error deleting VTEP {vtep.interface_name}: {e}")
            return False

    # --- Per-Link VNI Model ---

    @staticmethod
    def _link_tunnel_interface_name(lab_id: str, link_id: str) -> str:
        """Generate a deterministic OVS port name for a per-link VXLAN tunnel.

        Uses MD5 hash of lab_id:link_id to create a unique, deterministic name.
        Format: vxlan-{hash8} (max 14 chars, within OVS 15-char limit).
        """
        import hashlib

        combined = f"{lab_id}:{link_id}"
        link_hash = hashlib.md5(combined.encode()).hexdigest()[:8]
        return f"vxlan-{link_hash}"

    async def create_link_tunnel(
        self,
        lab_id: str,
        link_id: str,
        vni: int,
        local_ip: str,
        remote_ip: str,
        local_vlan: int,
        tenant_mtu: int = 0,
    ) -> LinkTunnel:
        """Create a per-link access-mode VXLAN port on OVS.

        Each cross-host link gets its own VXLAN port with:
        - tag=<local_vlan>: access mode using the container's existing VLAN
        - options:key=<vni>: shared VNI identifier (same on both sides)

        OVS access-mode behavior handles VLAN translation automatically:
        - Egress: strips local VLAN, encapsulates with VNI
        - Ingress: decapsulates VNI, adds local VLAN

        Args:
            lab_id: Lab identifier
            link_id: Link identifier (e.g., "node1:eth1-node2:eth1")
            vni: VXLAN Network Identifier (must match remote side)
            local_ip: Local host IP for VXLAN endpoint
            remote_ip: Remote host IP for VXLAN endpoint
            local_vlan: Container's local VLAN tag for access mode
            tenant_mtu: MTU for tenant traffic (0 = auto-discover)

        Returns:
            LinkTunnel object representing the created tunnel

        Raises:
            RuntimeError: If tunnel creation fails
        """
        await self._ensure_ovs_bridge()

        # Return existing tunnel if already created for this link
        if link_id in self._link_tunnels:
            existing = self._link_tunnels[link_id]
            if await self._ovs_port_exists(existing.interface_name):
                # Check if VLAN tag needs updating (container restart scenario)
                if local_vlan > 0 and local_vlan != existing.local_vlan:
                    await self._ovs_vsctl(
                        "set", "port", existing.interface_name, f"tag={local_vlan}"
                    )
                    old_vlan = existing.local_vlan
                    existing.local_vlan = local_vlan
                    logger.info(
                        f"Updated VLAN tag for {link_id}: "
                        f"{existing.interface_name} tag {old_vlan} -> {local_vlan}"
                    )
                else:
                    logger.info(
                        f"Link tunnel already exists for {link_id}: {existing.interface_name}"
                    )
                return existing
            else:
                logger.warning(
                    f"Link tunnel {existing.interface_name} missing from OVS for {link_id}, recreating"
                )
                del self._link_tunnels[link_id]

        # Use overlay_mtu directly — with df=unset, the kernel handles outer
        # fragmentation transparently so we present full MTU to the overlay
        if tenant_mtu <= 0:
            tenant_mtu = settings.overlay_mtu if settings.overlay_mtu > 0 else 1500
            logger.info(f"Link tunnel MTU for {link_id}: {tenant_mtu}")

        interface_name = self._link_tunnel_interface_name(lab_id, link_id)

        # If a recovered tunnel exists under a placeholder key, rebind it to the real link_id
        for existing_key, existing in list(self._link_tunnels.items()):
            if existing.interface_name != interface_name:
                continue
            if await self._ovs_port_exists(interface_name):
                if local_vlan > 0 and local_vlan != existing.local_vlan:
                    await self._ovs_vsctl(
                        "set", "port", existing.interface_name, f"tag={local_vlan}"
                    )
                    old_vlan = existing.local_vlan
                    existing.local_vlan = local_vlan
                    logger.info(
                        f"Updated VLAN tag for {link_id}: "
                        f"{existing.interface_name} tag {old_vlan} -> {local_vlan}"
                    )
                existing.link_id = link_id
                existing.lab_id = lab_id
                existing.vni = vni
                existing.local_ip = local_ip
                existing.remote_ip = remote_ip
                existing.tenant_mtu = tenant_mtu
                if existing_key != link_id:
                    del self._link_tunnels[existing_key]
                    self._link_tunnels[link_id] = existing
                logger.info(
                    f"Rebound recovered link tunnel {interface_name} to link_id {link_id}"
                )
                return existing
            # Port missing, drop stale tracking and recreate
            del self._link_tunnels[existing_key]
            break

        # Delete existing port if present (from previous run / recovery)
        if await self._ovs_port_exists(interface_name):
            vni_found, remote_found, local_found = await self._read_vxlan_link_info(interface_name)
            if vni_found == vni and remote_found == remote_ip and (not local_ip or local_found == local_ip):
                if local_vlan > 0:
                    code, tag_out, _ = await self._ovs_vsctl("get", "port", interface_name, "tag")
                    current_tag = 0
                    if code == 0:
                        tag_str = tag_out.strip()
                        if tag_str and tag_str != "[]":
                            try:
                                current_tag = int(tag_str)
                            except ValueError:
                                current_tag = 0
                    if current_tag != local_vlan:
                        await self._ovs_vsctl(
                            "set", "port", interface_name, f"tag={local_vlan}"
                        )
                        logger.info(
                            f"Updated VLAN tag for adopted tunnel {interface_name}: "
                            f"{current_tag} -> {local_vlan}"
                        )
                tunnel = LinkTunnel(
                    link_id=link_id,
                    vni=vni,
                    local_ip=local_ip or local_found,
                    remote_ip=remote_ip,
                    local_vlan=local_vlan,
                    interface_name=interface_name,
                    lab_id=lab_id,
                    tenant_mtu=tenant_mtu,
                )
                self._link_tunnels[link_id] = tunnel
                logger.info(
                    f"Adopted existing link tunnel {interface_name} for {link_id} without recreation"
                )
                return tunnel
            logger.warning(f"Link tunnel port {interface_name} already exists, replacing")
            await self._delete_vxlan_device(interface_name, self._bridge_name)

        # Also clean up any leftover Linux interface
        if await self._ip_link_exists(interface_name):
            await self._run_cmd(["ip", "link", "delete", interface_name])

        # Create access-mode Linux VXLAN device
        # tag= makes it an access port: strips VLAN on egress, adds on ingress
        await self._create_vxlan_device(
            name=interface_name,
            vni=vni,
            local_ip=local_ip,
            remote_ip=remote_ip,
            bridge=self._bridge_name,
            vlan_tag=local_vlan,
            tenant_mtu=tenant_mtu,
        )

        tunnel = LinkTunnel(
            link_id=link_id,
            vni=vni,
            local_ip=local_ip,
            remote_ip=remote_ip,
            local_vlan=local_vlan,
            interface_name=interface_name,
            lab_id=lab_id,
            tenant_mtu=tenant_mtu,
        )

        self._link_tunnels[link_id] = tunnel
        logger.info(
            f"Created per-link tunnel {interface_name} "
            f"(VNI {vni}, VLAN {local_vlan}, MTU {tenant_mtu}) to {remote_ip}"
        )

        return tunnel

    async def delete_link_tunnel(self, link_id: str, lab_id: str | None = None) -> bool:
        """Delete a per-link VXLAN tunnel port.

        Args:
            link_id: The link identifier whose tunnel to delete
            lab_id: Optional lab identifier (used to compute port name)

        Returns:
            True if deleted successfully, False otherwise
        """
        tunnel = self._link_tunnels.get(link_id)
        if not tunnel:
            # Allow deletion by port name for recovered tunnels (link_id == interface_name)
            for existing in self._link_tunnels.values():
                if existing.interface_name == link_id:
                    tunnel = existing
                    break

        if not tunnel and lab_id:
            expected_name = self._link_tunnel_interface_name(lab_id, link_id)
            for existing in self._link_tunnels.values():
                if existing.interface_name == expected_name:
                    tunnel = existing
                    break
            if not tunnel:
                # Best-effort delete by port name even if tracking is missing
                if await self._ovs_port_exists(expected_name):
                    try:
                        await self._delete_vxlan_device(expected_name, self._bridge_name)
                        logger.info(f"Deleted untracked link tunnel {expected_name} for {link_id}")
                        return True
                    except Exception as e:
                        logger.error(f"Error deleting untracked link tunnel {expected_name}: {e}")
                        return False

        if not tunnel:
            logger.warning(f"No link tunnel found for {link_id}")
            return False

        try:
            # Delete OVS port and Linux VXLAN device
            await self._delete_vxlan_device(tunnel.interface_name, self._bridge_name)

            # Remove from tracking using actual key
            if tunnel.link_id in self._link_tunnels:
                del self._link_tunnels[tunnel.link_id]
            logger.info(f"Deleted link tunnel {tunnel.interface_name} for {link_id}")
            return True

        except Exception as e:
            logger.error(f"Error deleting link tunnel for {link_id}: {e}")
            return False

    def get_link_tunnel(self, link_id: str) -> LinkTunnel | None:
        """Get a per-link tunnel by link_id."""
        return self._link_tunnels.get(link_id)

    def get_all_link_tunnels(self) -> list[LinkTunnel]:
        """Get all active per-link tunnels."""
        return list(self._link_tunnels.values())

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
            "link_tunnels_deleted": 0,
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

        # Delete per-link tunnels (new model)
        link_tunnels_to_delete = [
            lt for lt in self._link_tunnels.values() if lt.lab_id == lab_id
        ]
        for lt in link_tunnels_to_delete:
            try:
                if await self.delete_link_tunnel(lt.link_id):
                    result["link_tunnels_deleted"] += 1
            except Exception as e:
                result["errors"].append(f"LinkTunnel {lt.interface_name}: {e}")

        # Release all VNI allocations for this lab
        result["vnis_released"] = self._vni_allocator.release_lab(lab_id)

        # Prune any recovered allocations that no longer exist
        try:
            result["vnis_recovered_pruned"] = await self._vni_allocator.prune_recovered_from_system(
                self._bridge_name
            )
        except Exception as e:
            result["errors"].append(f"VNI prune: {e}")

        logger.info(f"Lab {lab_id} overlay cleanup: {result}")
        return result

    async def recover_allocations(self) -> int:
        """Recover VNI allocations from system state on startup.

        Returns:
            Number of VNIs recovered
        """
        return await self._vni_allocator.recover_from_system()

    async def recover_link_tunnels(self) -> int:
        """Recover link tunnel tracking from OVS/Linux state on startup.

        After agent restart, _link_tunnels is empty but VXLAN ports may still
        exist on OVS. Without recovery, the periodic cleanup treats them as
        orphans and deletes them, breaking cross-host links.

        Scans for vxlan-* ports on OVS, reads VNI/remote/local from the Linux
        device, and rebuilds _link_tunnels entries so they're protected from
        orphan cleanup.

        Returns:
            Number of link tunnels recovered
        """
        recovered = 0
        try:
            code, stdout, _ = await self._ovs_vsctl(
                "list-ports", self._bridge_name
            )
            if code != 0:
                return 0

            port_names = [
                p.strip() for p in stdout.strip().split("\n")
                if p.strip().startswith("vxlan-")
            ]

            for port_name in port_names:
                # Read VLAN tag from OVS
                code, tag_out, _ = await self._ovs_vsctl(
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

                # Read VNI, remote IP, local IP from Linux VXLAN device
                vni, remote_ip, local_ip = await self._read_vxlan_link_info(port_name)

                if not vni or not remote_ip:
                    continue

                # We can't recover link_id or lab_id from the hash-based name,
                # so use the port name as a placeholder link_id. The API will
                # overwrite with the real link_id on the next attach-link call.
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
                self._link_tunnels[port_name] = tunnel
                recovered += 1

            if recovered > 0:
                logger.info(
                    f"Recovered {recovered} link tunnel(s) from OVS state"
                )
        except Exception as e:
            logger.warning(f"Link tunnel recovery failed: {e}")

        return recovered

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
            "mtu_cache": dict(self._mtu_cache),
            # Legacy trunk VTEP model (deprecated)
            "vteps": [
                {
                    "interface": v.interface_name,
                    "vni": v.vni,
                    "local_ip": v.local_ip,
                    "remote_ip": v.remote_ip,
                    "remote_host_id": v.remote_host_id,
                    "tenant_mtu": v.tenant_mtu,
                    "link_count": v.link_count,
                    "links": list(v.links),
                }
                for v in self._vteps.values()
            ],
            # Legacy per-link tunnels
            "tunnels": [
                {
                    "vni": t.vni,
                    "interface": t.interface_name,
                    "local_ip": t.local_ip,
                    "remote_ip": t.remote_ip,
                    "lab_id": t.lab_id,
                    "link_id": t.link_id,
                    "vlan_tag": t.vlan_tag,
                    "tenant_mtu": t.tenant_mtu,
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
                    "tenant_mtu": b.tenant_mtu,
                    "veth_pairs": b.veth_pairs,
                }
                for b in self._bridges.values()
            ],
            # Per-link access-mode VXLAN tunnels (current model)
            "link_tunnels": [
                {
                    "link_id": lt.link_id,
                    "vni": lt.vni,
                    "local_ip": lt.local_ip,
                    "remote_ip": lt.remote_ip,
                    "local_vlan": lt.local_vlan,
                    "interface_name": lt.interface_name,
                    "lab_id": lt.lab_id,
                    "tenant_mtu": lt.tenant_mtu,
                }
                for lt in self._link_tunnels.values()
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
                            # Non-numeric VXLAN port (e.g., vxlan-<hash>) - read VNI from device
                            try:
                                proc_info = await asyncio.create_subprocess_exec(
                                    "ip", "-d", "link", "show", port,
                                    stdout=asyncio.subprocess.PIPE,
                                    stderr=asyncio.subprocess.PIPE,
                                )
                                info_out, _ = await proc_info.communicate()
                                if proc_info.returncode != 0:
                                    continue
                                parts = info_out.decode().split()
                                vni = 0
                                for i, part in enumerate(parts):
                                    if part == "id" and i + 1 < len(parts):
                                        try:
                                            vni = int(parts[i + 1])
                                        except ValueError:
                                            vni = 0
                                        break
                                if vni and self._base <= vni <= self._max and vni not in used_vnis:
                                    placeholder_key = f"_recovered:{port}"
                                    self._allocated[placeholder_key] = vni
                                    used_vnis.add(vni)
                                    recovered += 1
                                    logger.info(f"Recovered VNI {vni} from OVS port {port}")
                            except Exception:
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

    async def prune_recovered_from_system(self, bridge_name: str) -> int:
        """Remove recovered allocations that no longer exist on the system.

        Returns number of recovered entries removed.
        """
        removed = 0
        existing_names: set[str] = set()

        try:
            # OVS ports (includes vxlan-<hash> access ports)
            proc = await asyncio.create_subprocess_exec(
                "ovs-vsctl", "list-ports", bridge_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0 and stdout:
                existing_names.update(
                    p.strip() for p in stdout.decode().split("\n") if p.strip()
                )

            # Linux VXLAN interfaces (legacy vxlan<id>)
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
                    if name:
                        existing_names.add(name)

            keys_to_remove = [
                k for k in self._allocated.keys()
                if k.startswith("_recovered:") and k.split(":", 1)[1] not in existing_names
            ]
            for key in keys_to_remove:
                del self._allocated[key]
                removed += 1

            if removed > 0:
                self._save_to_disk()
                logger.info(f"Pruned {removed} recovered VNI allocations")

        except Exception as e:
            logger.warning(f"Failed to prune recovered VNI allocations: {e}")

        return removed

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
