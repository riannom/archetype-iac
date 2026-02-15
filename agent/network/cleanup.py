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
import time
from dataclasses import dataclass
from typing import Any

import docker

from agent.network.cmd import run_cmd as _shared_run_cmd


logger = logging.getLogger(__name__)


def _get_ovs_plugin_active_veths() -> set[str]:
    """Get active veths from OVS plugin if available.

    Returns empty set if plugin is not initialized.
    """
    try:
        from agent.network.docker_plugin import get_docker_ovs_plugin
        plugin = get_docker_ovs_plugin()
        if plugin:
            return plugin.get_active_host_veths()
    except Exception:
        pass
    return set()


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
    ovs_orphans_deleted: int = 0
    ovs_vxlan_orphans_deleted: int = 0
    ovs_tracked_removed: int = 0
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
            "ovs_orphans_deleted": self.ovs_orphans_deleted,
            "ovs_vxlan_orphans_deleted": self.ovs_vxlan_orphans_deleted,
            "ovs_tracked_removed": self.ovs_tracked_removed,
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
        self._last_api_reconcile_at: float | None = None

    @property
    def docker(self) -> docker.DockerClient:
        """Lazy-initialize Docker client."""
        if self._docker is None:
            self._docker = docker.from_env()
        return self._docker

    async def _run_cmd(self, cmd: list[str]) -> tuple[int, str, str]:
        """Run a shell command asynchronously."""
        return await _shared_run_cmd(cmd)

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
        def _sync_get_pids() -> set[int]:
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

        return await asyncio.to_thread(_sync_get_pids)

    async def _get_container_ifindexes(self, pids: set[int] | None = None) -> set[int]:
        """Collect interface ifindex values from running container namespaces."""
        ifindexes: set[int] = set()
        if pids is None:
            pids = await self._get_running_container_pids()
        if not pids:
            return ifindexes

        for pid in pids:
            try:
                code, stdout, _ = await self._run_cmd([
                    "nsenter", "-t", str(pid), "-n",
                    "ip", "-o", "link", "show",
                ])
                if code != 0:
                    continue
                for line in stdout.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    # Format: "2: eth0@if3: <...>"
                    try:
                        idx_str = line.split(":", 1)[0].strip()
                        ifindexes.add(int(idx_str))
                    except ValueError:
                        continue
            except Exception as e:
                logger.debug(f"Failed to read netns interfaces for pid {pid}: {e}")

        return ifindexes

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
                        "master": iface.get("master"),  # Bridge/OVS master if attached
                    })

        except Exception as e:
            logger.warning(f"Failed to list veth interfaces: {e}")

        return interfaces

    async def _is_veth_orphaned(self, interface: dict[str, Any], container_ifindexes: set[int]) -> bool:
        """Check if a veth interface is orphaned (peer not in any container).

        A veth is orphaned if:
        1. Its peer doesn't exist (peer was deleted with container)
        2. Its peer is not in any running archetype container's namespace
        3. It's not attached to any bridge (overlay veths are attached to bridges)
        """
        name = interface["name"]
        link_index = interface.get("link_index")

        # If veth is attached to a bridge/OVS, it's not orphaned
        # This is important for overlay veths where the peer is in a container namespace
        # and not visible from the host
        master = interface.get("master")
        if master:
            logger.debug(f"Veth {name} has master {master}, not orphaned")
            return False

        # If this is an OVS-managed port, it's not orphaned
        if name.startswith("vh"):
            code, _, _ = await self._run_cmd(["ovs-vsctl", "port-to-br", name])
            if code == 0:
                return False

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
            peer.get("ifname", "")

            # If peer is on OVS bridge or a known bridge, it's not orphaned
            # (it's the host-side of an active connection)
            master = peer.get("master")
            if master:
                return False

            # If peer ifindex is seen inside a container namespace, it's not orphaned
            if link_index in container_ifindexes:
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

        # Get interface ifindexes in running container namespaces
        running_pids = await self._get_running_container_pids()
        container_ifindexes = await self._get_container_ifindexes(running_pids)
        if running_pids and not container_ifindexes:
            logger.warning(
                "Container PIDs detected but no netns ifindexes found; "
                "skipping veth deletion to avoid false positives"
            )
            return stats

        # Get active veths tracked by OVS plugin - these should never be deleted
        ovs_active_veths = _get_ovs_plugin_active_veths()
        if ovs_active_veths:
            logger.debug(f"OVS plugin tracking {len(ovs_active_veths)} active veths")

        # Check each veth for orphan status
        for veth in veths:
            name = veth["name"]
            try:
                # Skip veths that are tracked by the OVS plugin
                if name in ovs_active_veths:
                    logger.debug(f"Skipping veth {name}: tracked by OVS plugin")
                    continue

                if await self._is_veth_orphaned(veth, container_ifindexes):
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

    async def cleanup_ovs_orphans(self) -> dict[str, Any]:
        """Reconcile OVS bridge state with tracked ports.

        Calls reconcile_with_ovs() on the OVS manager, which:
        1. Removes tracking for ports that no longer exist in OVS
        2. Cleans up orphaned vh* OVS ports not in tracking
        3. Updates VLAN tags if they've drifted

        Returns dict with orphans_deleted, tracked_removed counts.
        """
        result = {"orphans_deleted": 0, "tracked_removed": 0, "errors": []}
        try:
            from agent.network.backends.registry import get_network_backend
            backend = get_network_backend()
            if not hasattr(backend, 'ovs_manager') or not backend.ovs_manager._initialized:
                return result
            reconcile_result = await backend.ovs_manager.reconcile_with_ovs()
            result["orphans_deleted"] = reconcile_result.get("orphans_deleted", 0)
            result["tracked_removed"] = reconcile_result.get("tracked_removed", 0)
            result["errors"] = reconcile_result.get("errors", [])
        except Exception as e:
            logger.warning(f"OVS orphan cleanup failed: {e}")
            result["errors"].append(str(e))
        return result

    def record_api_reconcile(self) -> None:
        """Record that the API just performed VXLAN port reconciliation.

        Suppresses heuristic cleanup for 15 minutes since the API's
        DB-driven approach is authoritative.
        """
        self._last_api_reconcile_at = time.monotonic()

    async def cleanup_ovs_vxlan_orphans(self) -> int:
        """Clean up orphaned VXLAN ports on the OVS bridge.

        Finds VXLAN-type ports on the OVS bridge that are not tracked by
        the overlay manager (not in _tunnels, _vteps, or _link_tunnels).
        These can accumulate when:
        - An agent is offline during lab destroy
        - Lab destroy fails partway through
        - Agent crashes during overlay teardown

        Skipped when the API has recently performed DB-driven reconciliation
        (within the last 15 minutes), since the API's whitelist approach is
        more accurate than the agent's in-memory tracking.

        Returns number of orphaned VXLAN ports deleted.
        """
        # Suppress when API reconciliation is active (within 15 min)
        if self._last_api_reconcile_at is not None:
            elapsed = time.monotonic() - self._last_api_reconcile_at
            if elapsed < 900:  # 15 minutes
                logger.debug(
                    "Skipping heuristic cleanup, API reconciliation active "
                    f"({int(elapsed)}s ago)"
                )
                return 0

        deleted = 0
        try:
            from agent.network.backends.registry import get_network_backend
            backend = get_network_backend()
            if not hasattr(backend, 'ovs_manager') or not backend.ovs_manager._initialized:
                return 0
            if not hasattr(backend, 'overlay_manager'):
                return 0

            ovs_mgr = backend.ovs_manager
            overlay_mgr = backend.overlay_manager

            # Get all ports on the OVS bridge
            all_ports = await ovs_mgr.get_all_ovs_ports()

            # Build set of tracked VXLAN port names from overlay manager
            tracked_vxlan_names: set[str] = set()
            for tunnel in overlay_mgr._tunnels.values():
                tracked_vxlan_names.add(tunnel.interface_name)
            for vtep in overlay_mgr._vteps.values():
                tracked_vxlan_names.add(vtep.interface_name)
            for link_tunnel in overlay_mgr._link_tunnels.values():
                tracked_vxlan_names.add(link_tunnel.interface_name)

            # Find and delete untracked VXLAN ports
            for port in all_ports:
                if port.get("type") != "vxlan":
                    continue
                port_name = port["port_name"]
                if port_name in tracked_vxlan_names:
                    continue
                # This VXLAN port is not tracked by overlay manager - orphaned
                try:
                    success = await ovs_mgr.delete_orphan_port(port_name)
                    if success:
                        deleted += 1
                        logger.info(f"Deleted orphaned VXLAN port: {port_name}")
                except Exception as e:
                    logger.warning(f"Failed to delete orphaned VXLAN port {port_name}: {e}")

            if deleted > 0:
                logger.info(f"OVS VXLAN orphan cleanup: {deleted} ports deleted")
        except Exception as e:
            logger.warning(f"OVS VXLAN orphan cleanup failed: {e}")
        return deleted

    async def run_full_cleanup(self, dry_run: bool = False, include_ovs: bool = True) -> CleanupStats:
        """Run all cleanup tasks.

        Args:
            dry_run: If True, don't delete, just report
            include_ovs: If True, run OVS reconciliation and VXLAN orphan cleanup.
                Set to False at startup before the agent is fully registered,
                since overlay manager tracking is empty until reconciliation
                populates it (which would cause active VXLAN ports to be
                incorrectly identified as orphans).

        Returns:
            Combined cleanup statistics
        """
        stats = await self.cleanup_orphaned_veths(dry_run=dry_run)
        stats.bridges_deleted = await self.cleanup_orphaned_bridges(dry_run=dry_run)
        stats.vxlans_deleted = await self.cleanup_orphaned_vxlans(dry_run=dry_run)

        # OVS reconciliation (only when agent is fully registered and reconciled)
        if not dry_run and include_ovs:
            ovs_result = await self.cleanup_ovs_orphans()
            stats.ovs_orphans_deleted = ovs_result["orphans_deleted"]
            stats.ovs_tracked_removed = ovs_result["tracked_removed"]
            stats.ovs_vxlan_orphans_deleted = await self.cleanup_ovs_vxlan_orphans()

        has_cleanup = (
            stats.veths_deleted > 0 or stats.bridges_deleted > 0 or
            stats.vxlans_deleted > 0 or stats.ovs_orphans_deleted > 0 or
            stats.ovs_vxlan_orphans_deleted > 0 or stats.ovs_tracked_removed > 0
        )
        if not dry_run and has_cleanup:
            logger.info(
                f"Network cleanup complete: "
                f"veths={stats.veths_deleted}, "
                f"bridges={stats.bridges_deleted}, "
                f"vxlans={stats.vxlans_deleted}, "
                f"ovs_orphans={stats.ovs_orphans_deleted}, "
                f"ovs_vxlan_orphans={stats.ovs_vxlan_orphans_deleted}, "
                f"ovs_tracked_removed={stats.ovs_tracked_removed}"
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
