"""VXLAN overlay networking for multi-host lab connectivity.

This module provides VXLAN tunnel management for connecting lab nodes
across multiple hosts. It handles:
- VXLAN interface creation and deletion
- Linux bridge management
- Attaching container interfaces to overlay bridges
- Tunnel establishment between hosts

VXLAN Overview:
- Encapsulates L2 frames in UDP (default port 4789)
- Uses VNI (VXLAN Network Identifier) for isolation
- Each cross-host link gets a unique VNI
- Point-to-point tunnels between agent hosts
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import docker
from docker.errors import NotFound

from agent.config import settings


logger = logging.getLogger(__name__)

# VXLAN default port
VXLAN_PORT = 4789


@dataclass
class VxlanTunnel:
    """Represents a VXLAN tunnel to another host."""

    vni: int  # VXLAN Network Identifier
    local_ip: str  # Local host IP for VXLAN endpoint
    remote_ip: str  # Remote host IP for VXLAN endpoint
    interface_name: str  # Name of the VXLAN interface (e.g., vxlan100000)
    lab_id: str  # Lab this tunnel belongs to
    link_id: str  # Identifier for the link (e.g., "node1:eth0-node2:eth0")

    @property
    def key(self) -> str:
        """Unique key for this tunnel."""
        return f"{self.lab_id}:{self.link_id}"


@dataclass
class OverlayBridge:
    """Represents a Linux bridge for overlay connectivity."""

    name: str  # Bridge name (e.g., archetype-br-100000)
    vni: int  # Associated VNI
    lab_id: str
    link_id: str
    veth_pairs: list[tuple[str, str]] = field(default_factory=list)  # (host_end, container_end)

    @property
    def key(self) -> str:
        """Unique key for this bridge."""
        return f"{self.lab_id}:{self.link_id}"


class OverlayManager:
    """Manages VXLAN overlay networks for multi-host labs.

    This class handles the creation and cleanup of VXLAN tunnels,
    bridges, and container attachments. It uses Linux ip commands
    for network configuration.

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

    async def _ip_link_exists(self, name: str) -> bool:
        """Check if a network interface exists."""
        code, _, _ = await self._run_cmd(["ip", "link", "show", name])
        return code == 0

    async def _bridge_exists(self, name: str) -> bool:
        """Check if a bridge exists."""
        return await self._ip_link_exists(name)

    async def create_tunnel(
        self,
        lab_id: str,
        link_id: str,
        local_ip: str,
        remote_ip: str,
        vni: int | None = None,
    ) -> VxlanTunnel:
        """Create a VXLAN tunnel to another host.

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

        # Check if interface already exists (from previous run)
        if await self._ip_link_exists(interface_name):
            logger.warning(f"VXLAN interface {interface_name} already exists, deleting")
            await self._run_cmd(["ip", "link", "delete", interface_name])

        # Create VXLAN interface
        # ip link add vxlan100000 type vxlan id 100000 local 192.168.1.10 remote 192.168.1.20 dstport 4789
        cmd = [
            "ip", "link", "add", interface_name,
            "type", "vxlan",
            "id", str(vni),
            "local", local_ip,
            "remote", remote_ip,
            "dstport", str(VXLAN_PORT),
        ]

        code, stdout, stderr = await self._run_cmd(cmd)
        if code != 0:
            raise RuntimeError(f"Failed to create VXLAN interface: {stderr}")

        # Bring interface up
        code, _, stderr = await self._run_cmd(["ip", "link", "set", interface_name, "up"])
        if code != 0:
            # Clean up on failure
            await self._run_cmd(["ip", "link", "delete", interface_name])
            raise RuntimeError(f"Failed to bring up VXLAN interface: {stderr}")

        tunnel = VxlanTunnel(
            vni=vni,
            local_ip=local_ip,
            remote_ip=remote_ip,
            interface_name=interface_name,
            lab_id=lab_id,
            link_id=link_id,
        )

        self._tunnels[key] = tunnel
        logger.info(f"Created VXLAN tunnel: {interface_name} (VNI {vni}) to {remote_ip}")

        return tunnel

    async def delete_tunnel(self, tunnel: VxlanTunnel) -> bool:
        """Delete a VXLAN tunnel.

        Args:
            tunnel: The tunnel to delete

        Returns:
            True if deleted successfully, False otherwise
        """
        try:
            # Delete VXLAN interface
            code, _, stderr = await self._run_cmd(["ip", "link", "delete", tunnel.interface_name])
            if code != 0 and "Cannot find device" not in stderr:
                logger.warning(f"Failed to delete VXLAN interface {tunnel.interface_name}: {stderr}")
                return False

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
        """Create a Linux bridge and attach the VXLAN interface.

        Args:
            tunnel: The VXLAN tunnel to bridge

        Returns:
            OverlayBridge object

        Raises:
            RuntimeError: If bridge creation fails
        """
        key = tunnel.key

        # Check if bridge already exists
        if key in self._bridges:
            logger.info(f"Bridge already exists for: {key}")
            return self._bridges[key]

        bridge_name = f"archetype-br-{tunnel.vni}"

        # Delete if exists from previous run
        if await self._bridge_exists(bridge_name):
            logger.warning(f"Bridge {bridge_name} already exists, deleting")
            await self._run_cmd(["ip", "link", "set", bridge_name, "down"])
            await self._run_cmd(["ip", "link", "delete", bridge_name])

        # Create bridge
        code, _, stderr = await self._run_cmd(["ip", "link", "add", bridge_name, "type", "bridge"])
        if code != 0:
            raise RuntimeError(f"Failed to create bridge: {stderr}")

        # Bring bridge up
        code, _, stderr = await self._run_cmd(["ip", "link", "set", bridge_name, "up"])
        if code != 0:
            await self._run_cmd(["ip", "link", "delete", bridge_name])
            raise RuntimeError(f"Failed to bring up bridge: {stderr}")

        # Attach VXLAN interface to bridge
        code, _, stderr = await self._run_cmd([
            "ip", "link", "set", tunnel.interface_name, "master", bridge_name
        ])
        if code != 0:
            await self._run_cmd(["ip", "link", "delete", bridge_name])
            raise RuntimeError(f"Failed to attach VXLAN to bridge: {stderr}")

        bridge = OverlayBridge(
            name=bridge_name,
            vni=tunnel.vni,
            lab_id=tunnel.lab_id,
            link_id=tunnel.link_id,
        )

        self._bridges[key] = bridge
        logger.info(f"Created bridge {bridge_name} with VXLAN {tunnel.interface_name}")

        return bridge

    async def delete_bridge(self, bridge: OverlayBridge) -> bool:
        """Delete a bridge and its veth pairs.

        Args:
            bridge: The bridge to delete

        Returns:
            True if deleted successfully
        """
        try:
            # Delete veth pairs first
            for host_end, _ in bridge.veth_pairs:
                await self._run_cmd(["ip", "link", "delete", host_end])

            # Delete bridge
            await self._run_cmd(["ip", "link", "set", bridge.name, "down"])
            code, _, stderr = await self._run_cmd(["ip", "link", "delete", bridge.name])

            if code != 0 and "Cannot find device" not in stderr:
                logger.warning(f"Failed to delete bridge {bridge.name}: {stderr}")

            # Remove from tracking
            if bridge.key in self._bridges:
                del self._bridges[bridge.key]

            logger.info(f"Deleted bridge: {bridge.name}")
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
        and attaches the other end to the bridge.

        Args:
            bridge: The bridge to attach to
            container_name: Docker container name
            interface_name: Interface name inside container (e.g., eth1)
            ip_address: Optional IP address in CIDR format (e.g., "10.0.0.1/24")

        Returns:
            True if attached successfully
        """
        try:
            # Get container PID for network namespace
            container = self.docker.containers.get(container_name)
            if container.status != "running":
                logger.error(f"Container {container_name} is not running")
                return False

            pid = container.attrs["State"]["Pid"]
            if not pid:
                logger.error(f"Could not get PID for container {container_name}")
                return False

            # Create unique veth names with random suffix to ensure unique MACs
            import secrets
            suffix = secrets.token_hex(2)  # 4 hex chars
            veth_host = f"v{bridge.vni % 10000}{suffix}h"[:15]  # Max 15 chars
            veth_cont = f"v{bridge.vni % 10000}{suffix}c"[:15]

            # Delete if exists
            await self._run_cmd(["ip", "link", "delete", veth_host])

            # Create veth pair
            code, _, stderr = await self._run_cmd([
                "ip", "link", "add", veth_host, "type", "veth", "peer", "name", veth_cont
            ])
            if code != 0:
                raise RuntimeError(f"Failed to create veth pair: {stderr}")

            # Attach host end to bridge
            code, _, stderr = await self._run_cmd([
                "ip", "link", "set", veth_host, "master", bridge.name
            ])
            if code != 0:
                await self._run_cmd(["ip", "link", "delete", veth_host])
                raise RuntimeError(f"Failed to attach veth to bridge: {stderr}")

            # Bring host end up
            await self._run_cmd(["ip", "link", "set", veth_host, "up"])

            # Move container end to container namespace
            code, _, stderr = await self._run_cmd([
                "ip", "link", "set", veth_cont, "netns", str(pid)
            ])
            if code != 0:
                await self._run_cmd(["ip", "link", "delete", veth_host])
                raise RuntimeError(f"Failed to move veth to container namespace: {stderr}")

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

            logger.info(f"Attached container {container_name} to bridge {bridge.name} via {interface_name}")
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
                result["errors"].append(f"Bridge {bridge.name}: {e}")

        # Delete tunnels
        for tunnel in tunnels_to_delete:
            try:
                if await self.delete_tunnel(tunnel):
                    result["tunnels_deleted"] += 1
            except Exception as e:
                result["errors"].append(f"Tunnel {tunnel.interface_name}: {e}")

        logger.info(f"Lab {lab_id} overlay cleanup: {result}")
        return result

    async def get_tunnels_for_lab(self, lab_id: str) -> list[VxlanTunnel]:
        """Get all tunnels for a lab."""
        return [t for t in self._tunnels.values() if t.lab_id == lab_id]

    async def get_bridges_for_lab(self, lab_id: str) -> list[OverlayBridge]:
        """Get all bridges for a lab."""
        return [b for b in self._bridges.values() if b.lab_id == lab_id]

    def get_tunnel_status(self) -> dict[str, Any]:
        """Get status of all tunnels for debugging/monitoring."""
        return {
            "tunnels": [
                {
                    "vni": t.vni,
                    "interface": t.interface_name,
                    "local_ip": t.local_ip,
                    "remote_ip": t.remote_ip,
                    "lab_id": t.lab_id,
                    "link_id": t.link_id,
                }
                for t in self._tunnels.values()
            ],
            "bridges": [
                {
                    "name": b.name,
                    "vni": b.vni,
                    "lab_id": b.lab_id,
                    "link_id": b.link_id,
                    "veth_pairs": b.veth_pairs,
                }
                for b in self._bridges.values()
            ],
        }


class VniAllocator:
    """Allocates unique VNIs for VXLAN tunnels."""

    def __init__(self, base: int | None = None, max_vni: int | None = None):
        self._base = base if base is not None else settings.vxlan_vni_base
        self._max = max_vni if max_vni is not None else settings.vxlan_vni_max
        self._allocated: dict[str, int] = {}  # key -> vni
        self._next_vni = self._base

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

        return vni

    def release(self, lab_id: str, link_id: str) -> None:
        """Release a VNI allocation."""
        key = f"{lab_id}:{link_id}"
        if key in self._allocated:
            del self._allocated[key]

    def get_vni(self, lab_id: str, link_id: str) -> int | None:
        """Get VNI for a link, or None if not allocated."""
        return self._allocated.get(f"{lab_id}:{link_id}")
