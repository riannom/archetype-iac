"""Network cleanup utilities for orphaned resources.

This module provides periodic cleanup tasks for:
- Orphaned veth pairs (host-side remains when container is deleted)
- Stale OVS ports
- Orphaned overlay bridges/tunnels

These resources can accumulate when containers are force-deleted or
when the agent crashes during cleanup operations.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

import docker
from docker.errors import NotFound

from agent.config import settings


logger = logging.getLogger(__name__)


# Interface naming patterns used by Archetype
# veth pairs from local.py: arch{random_hex}
# veth pairs from ovs.py: vh{suffix}
# veth pairs from overlay.py: v{vni}{suffix}h, v{vni}{suffix}c
ARCHETYPE_VETH_PATTERNS = [
    re.compile(r"^arch[0-9a-f]{8}$"),  # Local veth pairs
    re.compile(r"^vh\w+$"),  # OVS veth pairs
    re.compile(r"^v\d+[0-9a-f]+[hc]$"),  # Overlay veth pairs
    re.compile(r"^vc[0-9a-f]+$"),  # Container-side veth (OVS)
]

# Bridge naming patterns
ARCHETYPE_BRIDGE_PATTERNS = [
    re.compile(r"^abr-\d+$"),  # Overlay bridges
    re.compile(r"^ovs-\w+$"),  # OVS lab bridges
]

# VXLAN interface patterns
ARCHETYPE_VXLAN_PATTERNS = [
    re.compile(r"^vxlan\d+$"),  # VXLAN tunnels
]


@dataclass
class CleanupStats:
    """Statistics from a cleanup run."""
    veths_found: int = 0
    veths_orphaned: int = 0
    veths_deleted: int = 0
    bridges_deleted: int = 0
    vxlans_deleted: int = 0
    errors: list[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []

    def to_dict(self) -> dict[str, Any]:
        return {
            "veths_found": self.veths_found,
            "veths_orphaned": self.veths_orphaned,
            "veths_deleted": self.veths_deleted,
            "bridges_deleted": self.bridges_deleted,
            "vxlans_deleted": self.vxlans_deleted,
            "errors": self.errors,
        }


class NetworkCleanupManager:
    """Manages periodic cleanup of orphaned network resources.

    Usage:
        manager = NetworkCleanupManager()

        # Run a single cleanup pass
        stats = await manager.cleanup_orphaned_veths()

        # Start periodic cleanup (runs in background)
        await manager.start_periodic_cleanup(interval_seconds=300)

        # Stop periodic cleanup
        await manager.stop_periodic_cleanup()
    """

    def __init__(self):
        self._docker: docker.DockerClient | None = None
        self._cleanup_task: asyncio.Task | None = None
        self._running = False

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

    def _is_archetype_veth(self, interface_name: str) -> bool:
        """Check if an interface name matches Archetype naming patterns."""
        return any(pattern.match(interface_name) for pattern in ARCHETYPE_VETH_PATTERNS)

    def _is_archetype_bridge(self, interface_name: str) -> bool:
        """Check if a bridge name matches Archetype naming patterns."""
        return any(pattern.match(interface_name) for pattern in ARCHETYPE_BRIDGE_PATTERNS)

    def _is_archetype_vxlan(self, interface_name: str) -> bool:
        """Check if an interface is an Archetype VXLAN tunnel."""
        return any(pattern.match(interface_name) for pattern in ARCHETYPE_VXLAN_PATTERNS)

    async def _get_running_container_pids(self) -> set[int]:
        """Get PIDs of all running containers with archetype labels."""
        pids = set()
        try:
            containers = self.docker.containers.list(
                filters={"label": "archetype.lab_id"}
            )
            for container in containers:
                if container.status == "running":
                    pid = container.attrs.get("State", {}).get("Pid")
                    if pid:
                        pids.add(pid)
        except Exception as e:
            logger.warning(f"Failed to get container PIDs: {e}")
        return pids

    async def _get_veth_interfaces(self) -> list[dict[str, Any]]:
        """List all veth interfaces on the host."""
        interfaces = []

        try:
            # Use ip -j link show type veth for JSON output
            code, stdout, _ = await self._run_cmd([
                "ip", "-j", "link", "show", "type", "veth"
            ])

            if code != 0:
                return interfaces

            import json
            data = json.loads(stdout) if stdout else []

            for iface in data:
                name = iface.get("ifname", "")
                if self._is_archetype_veth(name):
                    interfaces.append({
                        "name": name,
                        "ifindex": iface.get("ifindex"),
                        "link_index": iface.get("link_index"),  # Peer's ifindex
                        "state": iface.get("operstate", ""),
                    })

        except Exception as e:
            logger.warning(f"Failed to list veth interfaces: {e}")

        return interfaces

    async def _is_veth_orphaned(self, interface: dict[str, Any], container_pids: set[int]) -> bool:
        """Check if a veth interface is orphaned (peer not in any container).

        A veth is orphaned if:
        1. Its peer doesn't exist (peer was deleted with container)
        2. Its peer is not in any running archetype container's namespace
        """
        name = interface["name"]
        link_index = interface.get("link_index")

        if not link_index:
            # No peer link index - might be orphaned
            return True

        # Check if peer exists
        code, stdout, _ = await self._run_cmd([
            "ip", "-j", "link", "show"
        ])

        if code != 0:
            return False  # Can't determine, assume not orphaned

        try:
            import json
            all_interfaces = json.loads(stdout) if stdout else []

            # Find the peer interface by ifindex
            peer = None
            for iface in all_interfaces:
                if iface.get("ifindex") == link_index:
                    peer = iface
                    break

            if not peer:
                # Peer doesn't exist - orphaned
                logger.debug(f"Veth {name} has no peer (ifindex {link_index})")
                return True

            # Peer exists - check if it's in a container namespace
            # If the peer is still in the host namespace (no @ifX suffix in name),
            # it might be waiting to be moved to a container
            peer_name = peer.get("ifname", "")

            # If peer is on OVS bridge or a known bridge, it's not orphaned
            # (it's the host-side of an active connection)
            master = peer.get("master")
            if master:
                return False

            # Check if the host-side veth has a master (attached to bridge/OVS)
            # If it does, it's likely still in use
            code, stdout, _ = await self._run_cmd([
                "ip", "link", "show", name
            ])
            if "master" in stdout:
                return False

        except Exception as e:
            logger.debug(f"Error checking veth {name}: {e}")

        # Default to not orphaned if we can't determine
        return False

    async def cleanup_orphaned_veths(self, dry_run: bool = False) -> CleanupStats:
        """Find and delete orphaned veth interfaces.

        Args:
            dry_run: If True, don't delete, just report what would be deleted

        Returns:
            CleanupStats with counts and any errors
        """
        stats = CleanupStats()

        # Get all archetype veth interfaces
        veths = await self._get_veth_interfaces()
        stats.veths_found = len(veths)

        if not veths:
            return stats

        # Get running container PIDs
        container_pids = await self._get_running_container_pids()

        # Check each veth for orphan status
        for veth in veths:
            name = veth["name"]
            try:
                if await self._is_veth_orphaned(veth, container_pids):
                    stats.veths_orphaned += 1

                    if dry_run:
                        logger.info(f"[DRY RUN] Would delete orphaned veth: {name}")
                    else:
                        # Delete the veth (deleting one end deletes the pair)
                        code, _, stderr = await self._run_cmd([
                            "ip", "link", "delete", name
                        ])
                        if code == 0:
                            stats.veths_deleted += 1
                            logger.info(f"Deleted orphaned veth: {name}")
                        else:
                            stats.errors.append(f"Failed to delete {name}: {stderr}")

            except Exception as e:
                stats.errors.append(f"Error processing {name}: {e}")

        if stats.veths_deleted > 0 or stats.veths_orphaned > 0:
            logger.info(
                f"Veth cleanup: found={stats.veths_found}, "
                f"orphaned={stats.veths_orphaned}, deleted={stats.veths_deleted}"
            )

        return stats

    async def cleanup_orphaned_bridges(self, dry_run: bool = False) -> int:
        """Find and delete orphaned Linux bridges created by Archetype.

        Returns number of bridges deleted.
        """
        deleted = 0

        try:
            # List all bridges
            code, stdout, _ = await self._run_cmd([
                "ip", "-j", "link", "show", "type", "bridge"
            ])

            if code != 0:
                return 0

            import json
            bridges = json.loads(stdout) if stdout else []

            for bridge in bridges:
                name = bridge.get("ifname", "")
                if not self._is_archetype_bridge(name):
                    continue

                # Check if bridge has any ports
                code, stdout, _ = await self._run_cmd([
                    "ip", "link", "show", "master", name
                ])

                # If no ports are attached, bridge is orphaned
                if not stdout.strip():
                    if dry_run:
                        logger.info(f"[DRY RUN] Would delete orphaned bridge: {name}")
                    else:
                        await self._run_cmd(["ip", "link", "set", name, "down"])
                        code, _, stderr = await self._run_cmd([
                            "ip", "link", "delete", name
                        ])
                        if code == 0:
                            deleted += 1
                            logger.info(f"Deleted orphaned bridge: {name}")
                        else:
                            logger.warning(f"Failed to delete bridge {name}: {stderr}")

        except Exception as e:
            logger.warning(f"Error during bridge cleanup: {e}")

        return deleted

    async def cleanup_orphaned_vxlans(self, dry_run: bool = False) -> int:
        """Find and delete orphaned VXLAN interfaces.

        VXLAN interfaces are orphaned if they're not attached to any bridge.

        Returns number of VXLAN interfaces deleted.
        """
        deleted = 0

        try:
            # List all VXLAN interfaces
            code, stdout, _ = await self._run_cmd([
                "ip", "-j", "link", "show", "type", "vxlan"
            ])

            if code != 0:
                return 0

            import json
            vxlans = json.loads(stdout) if stdout else []

            for vxlan in vxlans:
                name = vxlan.get("ifname", "")
                if not self._is_archetype_vxlan(name):
                    continue

                # Check if VXLAN is attached to a bridge
                master = vxlan.get("master")
                if master:
                    continue  # Still attached, not orphaned

                if dry_run:
                    logger.info(f"[DRY RUN] Would delete orphaned VXLAN: {name}")
                else:
                    code, _, stderr = await self._run_cmd([
                        "ip", "link", "delete", name
                    ])
                    if code == 0:
                        deleted += 1
                        logger.info(f"Deleted orphaned VXLAN: {name}")
                    else:
                        logger.warning(f"Failed to delete VXLAN {name}: {stderr}")

        except Exception as e:
            logger.warning(f"Error during VXLAN cleanup: {e}")

        return deleted

    async def run_full_cleanup(self, dry_run: bool = False) -> CleanupStats:
        """Run all cleanup tasks.

        Args:
            dry_run: If True, don't delete, just report

        Returns:
            Combined cleanup statistics
        """
        stats = await self.cleanup_orphaned_veths(dry_run=dry_run)
        stats.bridges_deleted = await self.cleanup_orphaned_bridges(dry_run=dry_run)
        stats.vxlans_deleted = await self.cleanup_orphaned_vxlans(dry_run=dry_run)

        if not dry_run and (stats.veths_deleted > 0 or stats.bridges_deleted > 0 or stats.vxlans_deleted > 0):
            logger.info(
                f"Network cleanup complete: "
                f"veths={stats.veths_deleted}, "
                f"bridges={stats.bridges_deleted}, "
                f"vxlans={stats.vxlans_deleted}"
            )

        return stats

    async def _periodic_cleanup_loop(self, interval_seconds: int) -> None:
        """Background loop for periodic cleanup."""
        logger.info(f"Starting periodic network cleanup (interval: {interval_seconds}s)")

        while self._running:
            try:
                await asyncio.sleep(interval_seconds)
                if self._running:
                    await self.run_full_cleanup()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Error during periodic cleanup: {e}")

        logger.info("Periodic network cleanup stopped")

    async def start_periodic_cleanup(self, interval_seconds: int = 300) -> None:
        """Start periodic cleanup task.

        Args:
            interval_seconds: How often to run cleanup (default: 5 minutes)
        """
        if self._running:
            logger.warning("Periodic cleanup already running")
            return

        self._running = True
        self._cleanup_task = asyncio.create_task(
            self._periodic_cleanup_loop(interval_seconds)
        )

    async def stop_periodic_cleanup(self) -> None:
        """Stop periodic cleanup task."""
        if not self._running:
            return

        self._running = False
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None


# Module-level singleton
_cleanup_manager: NetworkCleanupManager | None = None


def get_cleanup_manager() -> NetworkCleanupManager:
    """Get the global NetworkCleanupManager instance."""
    global _cleanup_manager
    if _cleanup_manager is None:
        _cleanup_manager = NetworkCleanupManager()
    return _cleanup_manager
