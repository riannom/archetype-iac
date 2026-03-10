"""Docker Network Plugin for OVS Integration.

This plugin provides Docker networking backed by Open vSwitch, enabling:
- Single shared OVS bridge (arch-ovs) for all labs
- VLAN-based logical segmentation for link isolation
- Pre-boot interface provisioning (interfaces exist before container init)
- Hot-connect/disconnect for topology changes via VLAN remapping
- Seamless cross-host connectivity via VXLAN tunnels on same bridge

Architecture:
    - Single OVS bridge (arch-ovs) shared by all labs and VXLAN tunnels
    - Each container interface = one Docker network attachment
    - VLAN tags isolate interfaces until hot_connect links them
    - Cross-host links work automatically (VXLAN tunnels on same bridge)

Docker Plugin Lifecycle:
    CreateNetwork  → Register interface network on shared OVS bridge
    CreateEndpoint → Create veth pair, attach to OVS with unique VLAN
    Join           → Return interface name, Docker moves veth into container
    Leave          → Container disconnecting
    DeleteEndpoint → Clean up veth and OVS port
    DeleteNetwork  → Clean up (bridge deleted when last interface network removed)

Usage:
    # Start the plugin (runs as part of agent or standalone)
    python -m agent.network.docker_plugin

    # Create lab bridge network
    docker network create -d archetype-ovs \\
        -o lab_id=project_2 \\
        -o interface_name=eth1 \\
        project_2-eth1

    # Create additional interface networks
    docker network create -d archetype-ovs -o lab_id=project_2 -o interface_name=eth2 project_2-eth2

    # Run container attached to multiple networks (one per interface)
    docker create --network project_2-eth1 --name eos-1 ceos:latest
    docker network connect project_2-eth2 eos-1
    docker start eos-1
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import signal
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from aiohttp import web

from agent.config import settings
from agent.network.cmd import run_cmd as _run_cmd
from agent.network.ovs_vlan_tags import used_vlan_tags_on_bridge_from_ovs_outputs

logger = logging.getLogger(__name__)

def _parse_ovs_map(raw: str) -> dict[str, str]:
    """Parse a simple OVS ``map`` string into a Python dict."""
    text = (raw or "").strip()
    if not text or text == "{}":
        return {}
    if text.startswith("{") and text.endswith("}"):
        text = text[1:-1].strip()
    if not text:
        return {}

    result: dict[str, str] = {}
    for entry in text.split(","):
        if "=" not in entry:
            continue
        key, value = entry.split("=", 1)
        result[key.strip().strip('"')] = value.strip().strip('"')
    return result


# Plugin configuration
PLUGIN_NAME = "archetype-ovs"
PLUGIN_SOCKET_PATH = f"/run/docker/plugins/{PLUGIN_NAME}.sock"
PLUGIN_SPEC_PATH = f"/etc/docker/plugins/{PLUGIN_NAME}.spec"

# State persistence path (in workspace directory)
STATE_PERSISTENCE_FILE = "docker_ovs_plugin_state.json"

# OVS configuration
OVS_BRIDGE_PREFIX = "ovs-"

# Isolated range: unlinked container ports (ephemeral, plugin-assigned)
VLAN_RANGE_START = 100
VLAN_RANGE_END = 2049

# Linked range: linked ports (DB-stored, convergence-managed)
LINKED_VLAN_START = 2050
LINKED_VLAN_END = 4000


@dataclass
class LabBridge:
    """State for a lab's VLAN allocations on the shared OVS bridge."""

    lab_id: str
    bridge_name: str
    # VLAN allocator
    next_vlan: int = VLAN_RANGE_START
    # Track networks using this bridge
    network_ids: set[str] = field(default_factory=set)
    # Activity tracking for TTL cleanup
    last_activity: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # VXLAN tunnels: vni -> port_name
    vxlan_tunnels: dict[int, str] = field(default_factory=dict)
    # External interfaces: iface -> vlan
    external_ports: dict[str, int] = field(default_factory=dict)


@dataclass
class NetworkState:
    """State for a Docker network (one per interface)."""

    network_id: str
    lab_id: str
    interface_name: str  # e.g., "eth1", "eth2"
    bridge_name: str


@dataclass
class EndpointState:
    """State for a container endpoint."""

    endpoint_id: str
    network_id: str
    interface_name: str
    host_veth: str
    cont_veth: str
    vlan_tag: int
    container_name: str | None = None
    node_name: str | None = None


from agent.network.plugin_state import PluginStateMixin  # noqa: E402
from agent.network.plugin_handlers import PluginHandlersMixin  # noqa: E402
from agent.network.plugin_vlan import PluginVlanMixin  # noqa: E402


class DockerOVSPlugin(PluginStateMixin, PluginHandlersMixin, PluginVlanMixin):
    """Docker Network Plugin backed by Open vSwitch.

    Each Docker network maps to one interface on the lab's OVS bridge.
    Containers attach to multiple networks to get multiple interfaces.
    All interfaces are provisioned BEFORE container init runs.

    State Persistence:
        The plugin persists its state to disk to survive agent restarts.
        On startup, it loads persisted state and reconciles with actual
        OVS bridge state to handle:
        - Agent restarts (state in file matches OVS)
        - Agent crashes (OVS may have drifted from last saved state)
        - OVS restarts (bridges recreated, need reprovisioning)
    """

    # Delegate to shared cmd utility; instance-level override for testing.
    _run_cmd = staticmethod(_run_cmd)

    def __init__(self):
        self.lab_bridges: dict[str, LabBridge] = {}  # lab_id -> LabBridge
        self.networks: dict[str, NetworkState] = {}  # network_id -> NetworkState
        self.endpoints: dict[str, EndpointState] = {}  # endpoint_id -> EndpointState
        # Lazily initialized the first time we need mutual exclusion inside an
        # async context. Creating asyncio primitives in __init__ can bind them
        # to a non-running (or already closed) loop, which breaks under pytest
        # on Python 3.11+ with "Event loop is closed".
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None
        self._started_at = datetime.now(timezone.utc)
        self._cleanup_task: asyncio.Task | None = None
        self._binding_audit_task: asyncio.Task | None = None
        self._pending_endpoint_reconnects: list[tuple[str, str, str]] = []
        self._allocated_vlans: set[int] = set()
        self._global_next_vlan = VLAN_RANGE_START
        self._allocated_linked_vlans: set[int] = set()
        self._global_next_linked_vlan = LINKED_VLAN_START

        # State persistence
        workspace = Path(settings.workspace_path)
        workspace.mkdir(parents=True, exist_ok=True)
        self._state_file = workspace / STATE_PERSISTENCE_FILE
        self._state_dirty = False  # Track if state needs saving
        self._stale_gc_counter = 0  # Counts audit cycles for periodic GC

    @asynccontextmanager
    async def _locked(self):
        """Serialize plugin state mutations without binding locks at __init__."""
        # In production the plugin lives for the lifetime of the agent, so the
        # running loop is stable. In unit tests (and Starlette/FastAPI
        # TestClient), it's common to create and destroy multiple event loops in
        # a single process. If we keep a lock bound to a loop that's now closed,
        # subsequent uses can fail with "Event loop is closed".
        loop = asyncio.get_running_loop()
        if (
            self._lock is None
            or self._lock_loop is None
            or self._lock_loop is not loop
            or self._lock_loop.is_closed()
        ):
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        async with self._lock:
            yield

    # =========================================================================
    # OVS Operations
    # =========================================================================

    async def _ovs_vsctl(self, *args: str) -> tuple[int, str, str]:
        """Run ovs-vsctl command."""
        import time as _time

        status = "success"
        start = _time.monotonic()
        try:
            code, stdout, stderr = await self._run_cmd(["ovs-vsctl", *args])
            if code != 0:
                status = "error"
            return code, stdout, stderr
        except Exception:
            status = "error"
            raise
        finally:
            try:
                from agent.metrics import ovs_operation_duration

                operation = "unknown"
                for arg in args:
                    if not arg.startswith("-"):
                        operation = arg
                        break
                ovs_operation_duration.labels(
                    operation=operation,
                    status=status,
                ).observe(_time.monotonic() - start)
            except Exception:
                pass

    async def _validate_endpoint_exists(self, ep: EndpointState) -> bool:
        """Check if endpoint's host veth actually exists as an OVS port.

        This is the single validation gate — every method that returns
        endpoint data should call this to avoid returning stale state
        after agent/container restarts where veth pairs no longer exist.
        """
        if not ep.host_veth:
            return False
        code, _, _ = await self._run_cmd(["ovs-vsctl", "port-to-br", ep.host_veth])
        return code == 0

    async def _ensure_bridge(self, lab_id: str) -> LabBridge:
        """Ensure OVS bridge exists and lab state is tracked.

        Uses the shared arch-ovs bridge for all labs. This enables:
        - Same-host links via VLAN tag matching
        - Cross-host links via VXLAN tunnels (already on arch-ovs)
        """
        if lab_id in self.lab_bridges:
            return self.lab_bridges[lab_id]

        # Use shared bridge for all labs (enables cross-host VXLAN connectivity)
        bridge_name = settings.ovs_bridge_name  # Default: "arch-ovs"

        # Check if bridge exists
        code, _, _ = await self._ovs_vsctl("br-exists", bridge_name)
        if code != 0:
            # Create bridge
            code, _, stderr = await self._ovs_vsctl("add-br", bridge_name)
            if code != 0:
                # Test and legacy call-sites may mock br-exists only and leave
                # add-br returning a generic non-zero with empty stderr.
                if (stderr or "").strip():
                    raise RuntimeError(f"Failed to create OVS bridge {bridge_name}: {stderr}")

                fallback_code, _, fallback_stderr = await self._run_cmd(
                    ["ovs-vsctl", "add-br", bridge_name]
                )
                if fallback_code != 0:
                    raise RuntimeError(
                        f"Failed to create OVS bridge {bridge_name}: {fallback_stderr}"
                    )

            # Set fail mode to standalone for normal L2 switching
            # Secure mode drops all traffic without explicit OpenFlow rules
            await self._ovs_vsctl("set-fail-mode", bridge_name, "standalone")

            # Add default flow to allow traffic within same VLAN
            await self._run_cmd([
                "ovs-ofctl", "add-flow", bridge_name,
                "priority=1,actions=normal"
            ])

            # Bring bridge up
            await self._run_cmd(["ip", "link", "set", bridge_name, "up"])

            logger.info(f"Created OVS bridge: {bridge_name}")
        else:
            logger.debug(f"OVS bridge {bridge_name} already exists")

        lab_bridge = LabBridge(lab_id=lab_id, bridge_name=bridge_name)
        self.lab_bridges[lab_id] = lab_bridge
        return lab_bridge

    async def _maybe_delete_bridge(self, lab_id: str) -> None:
        """Remove lab tracking if no networks are using it.

        Note: The shared arch-ovs bridge is never deleted, only lab tracking is removed.
        """
        lab_bridge = self.lab_bridges.get(lab_id)
        if not lab_bridge:
            return

        if lab_bridge.network_ids:
            # Still has networks
            return

        # No more networks for this lab, remove tracking
        # Don't delete the shared bridge - it's used by other labs and VXLAN tunnels
        logger.info(f"Removed lab {lab_id} tracking (bridge {lab_bridge.bridge_name} retained)")
        del self.lab_bridges[lab_id]

    async def _create_veth_pair(self, host_name: str, cont_name: str) -> bool:
        """Create a veth pair."""
        code, _, stderr = await self._run_cmd([
            "ip", "link", "add", host_name, "type", "veth", "peer", "name", cont_name
        ])
        if code != 0:
            logger.error(f"Failed to create veth pair {host_name}/{cont_name}: {stderr}")
            return False

        # Set MTU on veth pair for jumbo frame support
        if settings.local_mtu > 0:
            await self._run_cmd([
                "ip", "link", "set", host_name, "mtu", str(settings.local_mtu)
            ])
            await self._run_cmd([
                "ip", "link", "set", cont_name, "mtu", str(settings.local_mtu)
            ])

        return True

    async def _attach_to_ovs(
        self,
        bridge_name: str,
        port_name: str,
        vlan_tag: int,
        external_ids: dict[str, str] | None = None,
    ) -> bool:
        """Attach a veth to OVS bridge with VLAN tag."""
        ovs_args = [
            "add-port", bridge_name, port_name,
            f"tag={vlan_tag}",
            "--", "set", "interface", port_name, "type=system",
        ]
        if external_ids:
            for key, value in sorted(external_ids.items()):
                ovs_args.append(f"external_ids:{key}={value}")

        code, _, stderr = await self._ovs_vsctl(*ovs_args)
        if code != 0:
            logger.error(f"Failed to attach {port_name} to OVS: {stderr}")
            return False

        # Bring host-side up
        await self._run_cmd(["ip", "link", "set", port_name, "up"])
        return True

    async def _delete_port(self, bridge_name: str, port_name: str) -> None:
        """Delete a port from OVS bridge and remove veth."""
        await self._ovs_vsctl("--if-exists", "del-port", bridge_name, port_name)
        await self._run_cmd(["ip", "link", "delete", port_name])

    def _generate_veth_names(self, endpoint_id: str) -> tuple[str, str]:
        """Generate unique veth pair names (max 15 chars each)."""
        suffix = secrets.token_hex(3)
        host_veth = f"vh{endpoint_id[:5]}{suffix}"[:15]
        cont_veth = f"vc{endpoint_id[:5]}{suffix}"[:15]
        return host_veth, cont_veth

    async def _get_used_vlan_tags_on_bridge(self, bridge_name: str) -> set[int]:
        """Return VLAN tags currently in-use on an OVS bridge.

        This is a safety net to prevent VLAN tag collisions across providers
        sharing the same bridge (docker plugin, libvirt VMs, VXLAN tunnels).
        """
        code, ports_out, _ = await self._ovs_vsctl("list-ports", bridge_name)
        if code != 0:
            return set()
        if not ports_out.strip():
            return set()

        code, csv_out, _ = await self._ovs_vsctl(
            "--format=csv",
            "--columns=name,tag",
            "list",
            "port",
        )
        if code != 0:
            return set()

        return used_vlan_tags_on_bridge_from_ovs_outputs(
            bridge_list_ports_output=ports_out,
            list_port_name_tag_csv=csv_out,
        )

    async def _allocate_vlan(self, lab_bridge: LabBridge) -> int:
        """Allocate next available VLAN tag across all labs.

        Prefer the isolated range (100-2049). If exhausted, spill into the
        linked range (2050-4000) to avoid hard failure.
        """
        used_on_bridge = await self._get_used_vlan_tags_on_bridge(settings.ovs_bridge_name)
        used_vlans = set(self._allocated_vlans) | set(self._allocated_linked_vlans) | used_on_bridge

        # Pass 1: preferred isolated range
        max_attempts = VLAN_RANGE_END - VLAN_RANGE_START + 1
        vlan = self._global_next_vlan
        for _ in range(max_attempts):
            if vlan not in used_vlans:
                self._allocated_vlans.add(vlan)
                self._global_next_vlan = VLAN_RANGE_START if vlan >= VLAN_RANGE_END else vlan + 1
                return vlan

            vlan += 1
            if vlan > VLAN_RANGE_END:
                vlan = VLAN_RANGE_START

        # Pass 2: fallback to linked range if isolated range is exhausted
        logger.warning(
            "Isolated VLAN range exhausted on %s; falling back to linked range %s-%s",
            settings.ovs_bridge_name,
            LINKED_VLAN_START,
            LINKED_VLAN_END,
        )
        max_attempts = LINKED_VLAN_END - LINKED_VLAN_START + 1
        vlan = self._global_next_linked_vlan
        for _ in range(max_attempts):
            if vlan not in used_vlans:
                self._allocated_linked_vlans.add(vlan)
                self._global_next_linked_vlan = (
                    LINKED_VLAN_START if vlan >= LINKED_VLAN_END else vlan + 1
                )
                return vlan

            vlan += 1
            if vlan > LINKED_VLAN_END:
                vlan = LINKED_VLAN_START

        raise RuntimeError("No available VLAN tags in isolated or linked ranges")

    def _release_vlan(self, vlan_tag: int) -> None:
        """Release a VLAN tag back to the pool.

        Fallback allocations can come from either range, so release from both
        tracking sets.
        """
        self._allocated_vlans.discard(vlan_tag)
        self._allocated_linked_vlans.discard(vlan_tag)

    async def _allocate_linked_vlan(self, lab_bridge: LabBridge) -> int:
        """Allocate next available VLAN tag from the linked range (2050-4000).

        Used for linked ports (hot_connect, attach-overlay) where the tag
        is stored in the DB and managed by convergence.
        If linked range is exhausted, spill into isolated range.
        """
        used_on_bridge = await self._get_used_vlan_tags_on_bridge(settings.ovs_bridge_name)
        used_vlans = set(self._allocated_linked_vlans) | set(self._allocated_vlans) | used_on_bridge

        # Pass 1: preferred linked range
        max_attempts = LINKED_VLAN_END - LINKED_VLAN_START + 1
        vlan = self._global_next_linked_vlan
        for _ in range(max_attempts):
            if vlan not in used_vlans:
                self._allocated_linked_vlans.add(vlan)
                self._global_next_linked_vlan = (
                    LINKED_VLAN_START if vlan >= LINKED_VLAN_END else vlan + 1
                )
                return vlan

            vlan += 1
            if vlan > LINKED_VLAN_END:
                vlan = LINKED_VLAN_START

        # Pass 2: fallback to isolated range if linked range is exhausted
        logger.warning(
            "Linked VLAN range exhausted on %s; falling back to isolated range %s-%s",
            settings.ovs_bridge_name,
            VLAN_RANGE_START,
            VLAN_RANGE_END,
        )
        max_attempts = VLAN_RANGE_END - VLAN_RANGE_START + 1
        vlan = self._global_next_vlan
        for _ in range(max_attempts):
            if vlan not in used_vlans:
                self._allocated_vlans.add(vlan)
                self._global_next_vlan = VLAN_RANGE_START if vlan >= VLAN_RANGE_END else vlan + 1
                return vlan

            vlan += 1
            if vlan > VLAN_RANGE_END:
                vlan = VLAN_RANGE_START

        raise RuntimeError("No available VLAN tags in linked or isolated ranges")

    def _release_linked_vlan(self, vlan_tag: int) -> None:
        """Release a linked VLAN tag back to the pool.

        Fallback allocations can come from either range, so release from both
        tracking sets.
        """
        self._allocated_linked_vlans.discard(vlan_tag)
        self._allocated_vlans.discard(vlan_tag)

    def _touch_lab(self, lab_id: str) -> None:
        """Update last_activity timestamp for TTL tracking."""
        if lab_id in self.lab_bridges:
            self.lab_bridges[lab_id].last_activity = datetime.now(timezone.utc)

    # =========================================================================
    # Health Check
    # =========================================================================

    async def health_check(self) -> dict[str, Any]:
        """Check plugin health and return status information."""
        checks = {}

        # Check socket exists
        checks["socket_exists"] = os.path.exists(PLUGIN_SOCKET_PATH)

        # Check OVS availability
        code, _, _ = await self._ovs_vsctl("--version")
        checks["ovs_available"] = code == 0

        # Count resources
        checks["bridges_count"] = len(self.lab_bridges)
        checks["networks_count"] = len(self.networks)
        checks["endpoints_count"] = len(self.endpoints)
        checks["management_networks_count"] = 0  # Deprecated: management on OVS now

        # State persistence status
        checks["state_file_exists"] = self._state_file.exists()
        checks["state_dirty"] = self._state_dirty

        # Calculate uptime
        uptime = datetime.now(timezone.utc) - self._started_at

        # Overall health
        healthy = checks["socket_exists"] and checks["ovs_available"]

        return {
            "healthy": healthy,
            "checks": checks,
            "uptime_seconds": uptime.total_seconds(),
            "started_at": self._started_at.isoformat(),
            "state_file": str(self._state_file),
        }

    async def _check_ovs_health(self) -> bool:
        """Quick check if OVS is responding."""
        code, _, _ = await self._ovs_vsctl("--version")
        return code == 0

    # =========================================================================
    # TTL Cleanup
    # =========================================================================

    async def _start_ttl_cleanup(self) -> None:
        """Start the TTL cleanup background task if enabled."""
        if settings.lab_ttl_enabled:
            self._cleanup_task = asyncio.create_task(self._ttl_cleanup_loop())
            logger.info(
                f"TTL cleanup enabled: TTL={settings.lab_ttl_seconds}s, "
                f"interval={settings.lab_ttl_check_interval}s"
            )

    async def _stop_ttl_cleanup(self) -> None:
        """Stop the TTL cleanup background task."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None

    async def _start_endpoint_binding_audit(self) -> None:
        """Start periodic endpoint binding audit task if enabled."""
        if settings.endpoint_binding_audit_enabled:
            self._binding_audit_task = asyncio.create_task(
                self._endpoint_binding_audit_loop()
            )
            logger.info(
                "Endpoint binding audit enabled: interval=%ss",
                settings.endpoint_binding_audit_interval_seconds,
            )

    async def _stop_endpoint_binding_audit(self) -> None:
        """Stop periodic endpoint binding audit task."""
        if self._binding_audit_task:
            self._binding_audit_task.cancel()
            try:
                await self._binding_audit_task
            except asyncio.CancelledError:
                pass
            self._binding_audit_task = None

    async def _ttl_cleanup_loop(self) -> None:
        """Background task to clean up expired labs."""
        while True:
            try:
                await asyncio.sleep(settings.lab_ttl_check_interval)
                await self._cleanup_expired_labs()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"TTL cleanup error: {e}")

    async def _endpoint_binding_audit_loop(self) -> None:
        """Background task to verify tracked endpoint bindings remain valid.

        Also runs stale state garbage collection every 10 audit cycles
        (~20 minutes at the default 120s interval).
        """
        while True:
            try:
                await asyncio.sleep(settings.endpoint_binding_audit_interval_seconds)
                stats = await self._audit_endpoint_bindings()
                if stats["drifted"] > 0 or stats["failed"] > 0:
                    logger.warning(
                        "Endpoint binding audit: checked=%s drifted=%s repaired=%s failed=%s",
                        stats["checked"],
                        stats["drifted"],
                        stats["repaired"],
                        stats["failed"],
                    )

                # Run stale state GC every 10 audit cycles
                self._stale_gc_counter += 1
                if self._stale_gc_counter >= 10:
                    self._stale_gc_counter = 0
                    try:
                        async with self._locked():
                            await self.cleanup_stale_state()
                    except Exception as e:
                        logger.error(f"Stale state GC error: {e}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Endpoint binding audit error: {e}")

    async def _audit_endpoint_bindings(self) -> dict[str, int]:
        """Verify endpoint host_veth bindings and refresh stale mappings."""
        stats = {"checked": 0, "drifted": 0, "repaired": 0, "failed": 0}
        candidates: list[tuple[str, str, str, str]] = []
        async with self._locked():
            for ep in self.endpoints.values():
                if not ep.container_name:
                    continue
                network = self.networks.get(ep.network_id)
                if not network:
                    continue
                candidates.append(
                    (network.lab_id, ep.container_name, ep.interface_name, ep.host_veth)
                )

        for lab_id, container_name, interface_name, expected_host_veth in candidates:
            stats["checked"] += 1
            discovered = await self._discover_endpoint(lab_id, container_name, interface_name)
            if not discovered:
                stats["failed"] += 1
                continue
            if discovered.host_veth != expected_host_veth:
                stats["drifted"] += 1
                stats["repaired"] += 1
                logger.warning(
                    "Endpoint binding drift repaired: %s:%s %s -> %s",
                    container_name,
                    interface_name,
                    expected_host_veth,
                    discovered.host_veth,
                )
        return stats

    async def _cleanup_expired_labs(self) -> None:
        """Remove resources for labs inactive beyond TTL."""
        now = datetime.now(timezone.utc)
        ttl = timedelta(seconds=settings.lab_ttl_seconds)

        expired_labs = []
        async with self._locked():
            for lab_id, bridge in list(self.lab_bridges.items()):
                age = now - bridge.last_activity
                if age > ttl:
                    expired_labs.append((lab_id, age))

        for lab_id, age in expired_labs:
            # Safety check: skip cleanup if any containers still exist for this lab
            has_containers = await self._lab_has_any_containers(lab_id)
            if has_containers:
                logger.info(
                    f"Skipping TTL cleanup for lab {lab_id}: containers still present"
                )
                continue
            logger.info(f"Cleaning up expired lab {lab_id} (inactive {age})")
            await self._full_lab_cleanup(lab_id)

    async def _lab_has_any_containers(self, lab_id: str) -> bool:
        """Check if any containers exist for a lab (running or stopped)."""
        def _sync_check() -> bool:
            try:
                import docker
                client = docker.from_env(timeout=30)
                containers = client.containers.list(
                    all=True, filters={"label": f"archetype.lab_id={lab_id}"}
                )
                return bool(containers)
            except Exception:
                return True  # Fail-safe: treat as present to avoid destructive cleanup

        return await asyncio.to_thread(_sync_check)

    async def _full_lab_cleanup(self, lab_id: str) -> None:
        """Clean up all resources for a lab."""
        async with self._locked():
            lab_bridge = self.lab_bridges.get(lab_id)
            if not lab_bridge:
                return

            # Clean up VXLAN tunnels
            for vni, port_name in list(lab_bridge.vxlan_tunnels.items()):
                await self._ovs_vsctl("--if-exists", "del-port", lab_bridge.bridge_name, port_name)
                # Also remove VXLAN interface
                vxlan_iface = f"vxlan{vni}"
                await self._run_cmd(["ip", "link", "delete", vxlan_iface])

            # Clean up external interfaces
            for iface in list(lab_bridge.external_ports.keys()):
                await self._ovs_vsctl("--if-exists", "del-port", lab_bridge.bridge_name, iface)

            # Clean up endpoints
            endpoints_to_remove = []
            for ep_id, endpoint in self.endpoints.items():
                network = self.networks.get(endpoint.network_id)
                if network and network.lab_id == lab_id:
                    await self._delete_port(lab_bridge.bridge_name, endpoint.host_veth)
                    endpoints_to_remove.append(ep_id)

            for ep_id in endpoints_to_remove:
                endpoint = self.endpoints.pop(ep_id, None)
                if endpoint:
                    self._release_vlan(endpoint.vlan_tag)

            # Clean up networks
            networks_to_remove = [
                net_id for net_id, net in self.networks.items()
                if net.lab_id == lab_id
            ]
            for net_id in networks_to_remove:
                del self.networks[net_id]

            # Remove lab tracking (don't delete shared bridge)
            del self.lab_bridges[lab_id]

            # Persist state after full lab cleanup
            await self._mark_dirty_and_save()

            logger.info(f"Cleaned up all resources for lab {lab_id}")




    # =========================================================================
    # Multi-Host VXLAN Support
    # =========================================================================

    async def create_vxlan_tunnel(
        self,
        lab_id: str,
        link_id: str,
        local_ip: str,
        remote_ip: str,
        vni: int,
        vlan_tag: int,
    ) -> str:
        """Create VXLAN port on lab bridge for cross-host link.

        This creates a VXLAN tunnel interface and attaches it to the lab's
        OVS bridge with the specified VLAN tag, enabling L2 connectivity
        between containers on different hosts.

        Args:
            lab_id: Lab identifier
            link_id: Unique link identifier
            local_ip: Local IP for VXLAN endpoint
            remote_ip: Remote agent's IP for VXLAN endpoint
            vni: VXLAN Network Identifier
            vlan_tag: OVS VLAN tag for this link

        Returns:
            VXLAN port name
        """
        async with self._locked():
            lab_bridge = self.lab_bridges.get(lab_id)
            if not lab_bridge:
                raise ValueError(f"Lab bridge not found for {lab_id}")

            # Check if tunnel already exists
            if vni in lab_bridge.vxlan_tunnels:
                return lab_bridge.vxlan_tunnels[vni]

            vxlan_port = f"vx{vni}"

            # Create VXLAN interface with df unset to allow outer fragmentation.
            # This allows inner packets at full MTU while the kernel handles
            # outer packet fragmentation transparently.
            code, _, stderr = await self._run_cmd([
                "ip", "link", "add", vxlan_port,
                "type", "vxlan",
                "id", str(vni),
                "local", local_ip,
                "remote", remote_ip,
                "dstport", str(settings.plugin_vxlan_dst_port),
                "df", "unset",
            ])
            if code != 0 and "File exists" not in stderr:
                raise RuntimeError(f"Failed to create VXLAN interface: {stderr}")

            # Bring interface up
            await self._run_cmd(["ip", "link", "set", vxlan_port, "up"])

            # Add to OVS bridge with VLAN tag
            code, _, stderr = await self._ovs_vsctl(
                "add-port", lab_bridge.bridge_name, vxlan_port,
                f"tag={vlan_tag}",
            )
            if code != 0:
                # Cleanup and raise
                await self._run_cmd(["ip", "link", "delete", vxlan_port])
                raise RuntimeError(f"Failed to add VXLAN port to OVS: {stderr}")

            lab_bridge.vxlan_tunnels[vni] = vxlan_port
            self._touch_lab(lab_id)

            # Persist state after VXLAN tunnel creation
            await self._mark_dirty_and_save()

            logger.info(
                f"Created VXLAN tunnel: {vxlan_port} (VNI={vni}, "
                f"remote={remote_ip}, VLAN={vlan_tag})"
            )
            return vxlan_port

    async def delete_vxlan_tunnel(self, lab_id: str, vni: int) -> bool:
        """Remove VXLAN tunnel.

        Args:
            lab_id: Lab identifier
            vni: VXLAN Network Identifier

        Returns:
            True if deleted, False if not found
        """
        async with self._locked():
            lab_bridge = self.lab_bridges.get(lab_id)
            if not lab_bridge:
                return False

            vxlan_port = lab_bridge.vxlan_tunnels.pop(vni, None)
            if not vxlan_port:
                return False

            # Remove from OVS
            await self._ovs_vsctl("--if-exists", "del-port", lab_bridge.bridge_name, vxlan_port)

            # Delete VXLAN interface
            await self._run_cmd(["ip", "link", "delete", vxlan_port])

            # Persist state after VXLAN tunnel deletion
            await self._mark_dirty_and_save()

            logger.info(f"Deleted VXLAN tunnel: {vxlan_port} (VNI={vni})")
            return True

    # =========================================================================
    # External Network Attachment
    # =========================================================================

    async def attach_external_interface(
        self,
        lab_id: str,
        external_interface: str,
        vlan_tag: int | None = None,
    ) -> int:
        """Attach host interface to lab's OVS bridge.

        This connects a physical host interface to the lab's OVS bridge,
        enabling containers to communicate with external networks.

        Args:
            lab_id: Lab identifier
            external_interface: Host interface name (e.g., "eth1", "enp0s8")
            vlan_tag: Optional VLAN tag (None = trunk mode)

        Returns:
            VLAN tag used (0 for trunk mode)
        """
        async with self._locked():
            lab_bridge = self.lab_bridges.get(lab_id)
            if not lab_bridge:
                raise ValueError(f"Lab bridge not found for {lab_id}")

            # Check if already attached
            if external_interface in lab_bridge.external_ports:
                return lab_bridge.external_ports[external_interface]

            # Verify interface exists
            code, _, _ = await self._run_cmd([
                "ip", "link", "show", external_interface
            ])
            if code != 0:
                raise ValueError(f"Interface {external_interface} not found")

            # Add to OVS bridge
            cmd = ["add-port", lab_bridge.bridge_name, external_interface]
            if vlan_tag is not None:
                cmd.append(f"tag={vlan_tag}")

            code, _, stderr = await self._ovs_vsctl(*cmd)
            if code != 0:
                raise RuntimeError(f"Failed to attach interface: {stderr}")

            # Bring interface up
            await self._run_cmd(["ip", "link", "set", external_interface, "up"])

            actual_vlan = vlan_tag or 0
            lab_bridge.external_ports[external_interface] = actual_vlan
            self._touch_lab(lab_id)

            # Persist state after external interface attachment
            await self._mark_dirty_and_save()

            logger.info(
                f"Attached external interface {external_interface} to "
                f"{lab_bridge.bridge_name} (VLAN={actual_vlan})"
            )
            return actual_vlan

    async def connect_to_external(
        self,
        lab_id: str,
        container_name: str,
        interface_name: str,
        external_interface: str,
    ) -> int:
        """Connect container interface to external network via shared VLAN.

        This sets the container's interface to the same VLAN as the external
        interface, enabling L2 connectivity.

        Args:
            lab_id: Lab identifier
            container_name: Container name
            interface_name: Interface name in container (e.g., "eth1")
            external_interface: External interface already attached to bridge

        Returns:
            Shared VLAN tag
        """
        async with self._locked():
            lab_bridge = self.lab_bridges.get(lab_id)
            if not lab_bridge:
                raise ValueError(f"Lab bridge not found for {lab_id}")

            # Get external interface VLAN
            vlan_tag = lab_bridge.external_ports.get(external_interface)
            if vlan_tag is None:
                raise ValueError(f"External interface {external_interface} not attached")

            # Find container endpoint
            endpoint = None
            for ep in self.endpoints.values():
                if ep.container_name == container_name and ep.interface_name == interface_name:
                    endpoint = ep
                    break

            if not endpoint:
                raise ValueError(f"Endpoint not found for {container_name}:{interface_name}")

            # Set endpoint to same VLAN as external interface
            code, _, stderr = await self._ovs_vsctl(
                "set", "port", endpoint.host_veth, f"tag={vlan_tag}"
            )
            if code != 0:
                raise RuntimeError(f"Failed to set VLAN: {stderr}")

            endpoint.vlan_tag = vlan_tag
            self._touch_lab(lab_id)

            # Persist state after external connection
            await self._mark_dirty_and_save()

            logger.info(
                f"Connected {container_name}:{interface_name} to external "
                f"{external_interface} (VLAN={vlan_tag})"
            )
            return vlan_tag

    async def detach_external_interface(self, lab_id: str, external_interface: str) -> bool:
        """Remove external interface from bridge.

        Args:
            lab_id: Lab identifier
            external_interface: Host interface name

        Returns:
            True if removed, False if not found
        """
        async with self._locked():
            lab_bridge = self.lab_bridges.get(lab_id)
            if not lab_bridge:
                return False

            if external_interface not in lab_bridge.external_ports:
                return False

            # Remove from OVS
            await self._ovs_vsctl(
                "--if-exists", "del-port", lab_bridge.bridge_name, external_interface
            )

            del lab_bridge.external_ports[external_interface]

            # Persist state after external interface detachment
            await self._mark_dirty_and_save()

            logger.info(
                f"Detached external interface {external_interface} from "
                f"{lab_bridge.bridge_name}"
            )
            return True

    def list_external_interfaces(self, lab_id: str) -> dict[str, int]:
        """List external interfaces attached to a lab.

        Returns:
            Dict of interface_name -> vlan_tag
        """
        lab_bridge = self.lab_bridges.get(lab_id)
        if not lab_bridge:
            return {}
        return dict(lab_bridge.external_ports)

    def get_lab_vlan_range(self, lab_id: str) -> tuple[int, int]:
        """Get min/max VLAN tag used by a lab's endpoints."""
        vlan_tags = []
        for ep in self.endpoints.values():
            network = self.networks.get(ep.network_id)
            if network and network.lab_id == lab_id:
                vlan_tags.append(ep.vlan_tag)
        if not vlan_tags:
            return (0, 0)
        return (min(vlan_tags), max(vlan_tags))

    # =========================================================================
    # Status and Debug Methods
    # =========================================================================

    async def get_plugin_status(self) -> dict[str, Any]:
        """Get comprehensive plugin status."""
        bridges_info = []
        for lab_id, bridge in self.lab_bridges.items():
            # Count endpoints for this lab
            endpoint_count = sum(
                1 for ep in self.endpoints.values()
                if self.networks.get(ep.network_id, NetworkState("", lab_id, "", "")).lab_id == lab_id
            )

            bridges_info.append({
                "lab_id": lab_id,
                "bridge_name": bridge.bridge_name,
                "port_count": endpoint_count,
                "vlan_range_used": self.get_lab_vlan_range(lab_id),
                "vxlan_tunnels": len(bridge.vxlan_tunnels),
                "external_interfaces": list(bridge.external_ports.keys()),
                "last_activity": bridge.last_activity.isoformat(),
            })

        return {
            "healthy": await self._check_ovs_health(),
            "labs_count": len(self.lab_bridges),
            "endpoints_count": len(self.endpoints),
            "networks_count": len(self.networks),
            "management_networks_count": 0,
            "bridges": bridges_info,
            "uptime_seconds": (datetime.now(timezone.utc) - self._started_at).total_seconds(),
        }

    async def get_lab_ports(self, lab_id: str) -> list[dict[str, Any]]:
        """Get detailed port information for a lab."""
        lab_bridge = self.lab_bridges.get(lab_id)
        if not lab_bridge:
            return []

        ports_info = []
        for ep in self.endpoints.values():
            network = self.networks.get(ep.network_id)
            if not network or network.lab_id != lab_id:
                continue

            # Get port statistics from OVS
            rx_bytes = 0
            tx_bytes = 0
            code, stdout, _ = await self._run_cmd([
                "ovs-vsctl", "get", "interface", ep.host_veth, "statistics"
            ])
            if code == 0:
                # Parse statistics JSON-like output
                try:
                    stats_str = stdout.strip()
                    # OVS returns format like {rx_bytes=123, tx_bytes=456, ...}
                    for part in stats_str.strip("{}").split(", "):
                        if "=" in part:
                            key, value = part.split("=", 1)
                            if key.strip() == "rx_bytes":
                                rx_bytes = int(value)
                            elif key.strip() == "tx_bytes":
                                tx_bytes = int(value)
                except Exception:
                    pass

            ports_info.append({
                "port_name": ep.host_veth,
                "bridge_name": lab_bridge.bridge_name,
                "container": ep.container_name,
                "interface": ep.interface_name,
                "vlan_tag": ep.vlan_tag,
                "rx_bytes": rx_bytes,
                "tx_bytes": tx_bytes,
            })

        return ports_info

    async def get_lab_flows(self, lab_id: str) -> dict[str, Any]:
        """Get OVS flow information for a lab."""
        lab_bridge = self.lab_bridges.get(lab_id)
        if not lab_bridge:
            return {"error": "Lab not found"}

        # Get flows from OVS
        code, stdout, stderr = await self._run_cmd([
            "ovs-ofctl", "dump-flows", lab_bridge.bridge_name
        ])
        if code != 0:
            return {"error": f"Failed to get flows: {stderr}"}

        # Parse flows into structured format
        flows = []
        for line in stdout.strip().split("\n"):
            if not line or line.startswith("NXST_FLOW"):
                continue
            # Basic parsing - each line is a flow
            flows.append(line.strip())

        return {
            "bridge": lab_bridge.bridge_name,
            "flow_count": len(flows),
            "flows": flows,
        }

    # =========================================================================
    # Endpoint Repair (recreate missing veth pairs after restart)
    # =========================================================================

    async def _cleanup_stale_ovs_ports(self, container_name: str) -> int:
        """Remove OVS ports from a previous incarnation of a container.

        After a container restart, old host-side veths linger as OVS ports.
        This scans all OVS ports and removes those whose external_ids
        reference this container but are not tracked by current endpoints.

        Args:
            container_name: Container name to match against

        Returns:
            Number of stale ports removed
        """
        bridge_name = settings.ovs_bridge_name
        code, ports_raw, _ = await self._ovs_vsctl("list-ports", bridge_name)
        if code != 0 or not ports_raw.strip():
            return 0

        # Build set of host veths currently tracked for this container
        tracked_veths = {
            ep.host_veth
            for ep in self.endpoints.values()
            if ep.container_name == container_name
        }

        removed = 0
        for port in ports_raw.strip().split("\n"):
            port = port.strip()
            if not port or port in tracked_veths:
                continue

            # Check if port's external_ids reference our container
            code, ext_ids, _ = await self._ovs_vsctl(
                "get", "interface", port, "external_ids"
            )
            if code != 0:
                continue

            if container_name not in ext_ids:
                continue

            # Stale port from previous container incarnation
            logger.warning(
                f"Removing stale OVS port {port} for container {container_name}"
            )
            await self._ovs_vsctl("--if-exists", "del-port", bridge_name, port)
            await self._run_cmd(["ip", "link", "delete", port])
            removed += 1

        return removed

    async def repair_endpoints(
        self,
        lab_id: str,
        container_name: str,
    ) -> list[dict[str, Any]]:
        """Repair missing veth pairs and OVS ports for a container.

        After agent/container restarts, the plugin may have in-memory endpoint
        records but the physical veth pairs no longer exist. This method:
        1. Finds all endpoints for a container
        2. Checks which ones are missing from OVS
        3. Recreates the veth pair and OVS attachment
        4. Moves the container side into the container namespace

        Returns:
            List of dicts with repair results per endpoint.
        """
        async with self._locked():
            return await self._repair_endpoints_locked(lab_id, container_name)

    async def _repair_endpoints_locked(
        self,
        lab_id: str,
        container_name: str,
    ) -> list[dict[str, Any]]:
        """Inner implementation of repair_endpoints, called under self._locked()."""
        results: list[dict[str, Any]] = []

        # Pre-repair: remove stale OVS ports from previous container incarnation
        await self._cleanup_stale_ovs_ports(container_name)

        pid = await self._get_container_pid(container_name)

        async def _binding_matches(ep: EndpointState) -> bool:
            if not pid or not ep.host_veth:
                return False
            mapped_if = await self._find_interface_in_container(pid, ep.host_veth)
            return mapped_if == ep.interface_name

        # Collect stale endpoints for this container
        stale_eps: list[EndpointState] = []
        for ep in self.endpoints.values():
            if ep.container_name == container_name:
                if not await self._validate_endpoint_exists(ep):
                    stale_eps.append(ep)
                elif not await _binding_matches(ep):
                    stale_eps.append(ep)
                    logger.info(
                        "Repairing endpoint binding drift for %s:%s (tracked host veth %s)",
                        container_name,
                        ep.interface_name,
                        ep.host_veth,
                    )
                else:
                    results.append({
                        "interface": ep.interface_name,
                        "status": "ok",
                        "message": f"Port {ep.host_veth} already exists",
                    })

        if not stale_eps:
            # Also check for endpoints without container_name set
            # (can happen after agent restart)
            import docker as docker_lib

            try:
                def _get_networks():
                    client = docker_lib.from_env()
                    ctr = client.containers.get(container_name)
                    return ctr.attrs["NetworkSettings"]["Networks"]

                networks = await asyncio.to_thread(_get_networks)
                for net_name, net_info in networks.items():
                    eid = net_info.get("EndpointID")
                    nid = net_info.get("NetworkID")

                    # Match by EndpointID (direct)
                    if eid and eid in self.endpoints:
                        ep = self.endpoints[eid]
                        if not await self._validate_endpoint_exists(ep):
                            ep.container_name = container_name
                            stale_eps.append(ep)
                        continue

                    # Fallback: match by NetworkID for recreated containers
                    if nid:
                        for ep in self.endpoints.values():
                            if ep.network_id == nid and not await self._validate_endpoint_exists(ep):
                                ep.container_name = container_name
                                stale_eps.append(ep)
            except Exception as e:
                logger.warning(f"Could not inspect container {container_name}: {e}")

        if not stale_eps:
            return results

        if not pid:
            for ep in stale_eps:
                results.append({
                    "interface": ep.interface_name,
                    "status": "error",
                    "message": f"Container {container_name} not running (no PID)",
                })
            return results

        bridge_name = settings.ovs_bridge_name

        for ep in stale_eps:
            iface = ep.interface_name
            try:
                # Clean up old OVS port if it exists (stale reference)
                await self._ovs_vsctl("--if-exists", "del-port", bridge_name, ep.host_veth)
                # Clean up old veth if it exists
                await self._run_cmd(["ip", "link", "delete", ep.host_veth])

                # Generate new veth names (old ones may conflict)
                host_veth, cont_veth = self._generate_veth_names(ep.endpoint_id)

                # Create veth pair
                if not await self._create_veth_pair(host_veth, cont_veth):
                    results.append({
                        "interface": iface,
                        "status": "error",
                        "message": f"Failed to create veth pair {host_veth}/{cont_veth}",
                    })
                    continue

                # Attach host side to OVS bridge with stored VLAN tag
                network = self.networks.get(ep.network_id)
                if not await self._attach_to_ovs(
                    bridge_name,
                    host_veth,
                    ep.vlan_tag,
                    external_ids={
                        "archetype.endpoint_id": ep.endpoint_id,
                        "archetype.interface_name": ep.interface_name,
                        "archetype.lab_id": network.lab_id if network else lab_id,
                        "archetype.network_id": ep.network_id,
                        **({"archetype.node_name": ep.node_name} if ep.node_name else {}),
                    },
                ):
                    await self._run_cmd(["ip", "link", "delete", host_veth])
                    results.append({
                        "interface": iface,
                        "status": "error",
                        "message": f"Failed to attach {host_veth} to OVS",
                    })
                    continue

                # Move container side into container namespace
                code, _, stderr = await self._run_cmd([
                    "ip", "link", "set", cont_veth, "netns", str(pid),
                ])
                if code != 0:
                    logger.error(f"Failed to move {cont_veth} to ns {pid}: {stderr}")
                    await self._ovs_vsctl("--if-exists", "del-port", bridge_name, host_veth)
                    await self._run_cmd(["ip", "link", "delete", host_veth])
                    results.append({
                        "interface": iface,
                        "status": "error",
                        "message": f"Failed to move veth to container namespace: {stderr}",
                    })
                    continue

                # Rename inside namespace to correct interface name
                code, _, stderr = await self._run_cmd([
                    "nsenter", "-t", str(pid), "-n",
                    "ip", "link", "set", cont_veth, "name", iface,
                ])
                if code != 0:
                    logger.error(f"Failed to rename {cont_veth} to {iface}: {stderr}")
                    results.append({
                        "interface": iface,
                        "status": "error",
                        "message": f"Failed to rename interface: {stderr}",
                    })
                    continue

                # Set MTU inside namespace if configured
                if settings.local_mtu > 0:
                    await self._run_cmd([
                        "nsenter", "-t", str(pid), "-n",
                        "ip", "link", "set", iface, "mtu", str(settings.local_mtu),
                    ])

                # Bring up inside namespace
                await self._run_cmd([
                    "nsenter", "-t", str(pid), "-n",
                    "ip", "link", "set", iface, "up",
                ])

                # Update endpoint state with new veth names
                old_host_veth = ep.host_veth
                ep.host_veth = host_veth
                ep.cont_veth = cont_veth
                await self._mark_dirty_and_save()

                logger.info(
                    f"Repaired endpoint {container_name}:{iface}: "
                    f"{old_host_veth} -> {host_veth} (VLAN {ep.vlan_tag})"
                )
                results.append({
                    "interface": iface,
                    "status": "repaired",
                    "host_veth": host_veth,
                    "vlan_tag": ep.vlan_tag,
                })

            except Exception as e:
                logger.error(f"Failed to repair {container_name}:{iface}: {e}")
                results.append({
                    "interface": iface,
                    "status": "error",
                    "message": str(e),
                })

        return results

    # =========================================================================
    # HTTP Server
    # =========================================================================

    def create_app(self) -> web.Application:
        """Create the aiohttp application with plugin routes."""
        app = web.Application()

        # Plugin activation
        app.router.add_post("/Plugin.Activate", self.handle_activate)

        # Network driver endpoints
        app.router.add_post("/NetworkDriver.GetCapabilities", self.handle_get_capabilities)
        app.router.add_post("/NetworkDriver.CreateNetwork", self.handle_create_network)
        app.router.add_post("/NetworkDriver.DeleteNetwork", self.handle_delete_network)
        app.router.add_post("/NetworkDriver.CreateEndpoint", self.handle_create_endpoint)
        app.router.add_post("/NetworkDriver.DeleteEndpoint", self.handle_delete_endpoint)
        app.router.add_post("/NetworkDriver.Join", self.handle_join)
        app.router.add_post("/NetworkDriver.Leave", self.handle_leave)
        app.router.add_post("/NetworkDriver.EndpointOperInfo", self.handle_endpoint_oper_info)
        app.router.add_post("/NetworkDriver.DiscoverNew", self.handle_discover_new)
        app.router.add_post("/NetworkDriver.DiscoverDelete", self.handle_discover_delete)
        app.router.add_post("/NetworkDriver.ProgramExternalConnectivity", self.handle_program_external_connectivity)
        app.router.add_post("/NetworkDriver.RevokeExternalConnectivity", self.handle_revoke_external_connectivity)

        # Store plugin reference in app for status endpoint
        app["plugin"] = self

        return app

    async def start(self, socket_path: str = PLUGIN_SOCKET_PATH) -> web.AppRunner:
        """Start the plugin server.

        Returns the AppRunner for lifecycle management.
        """
        # Ensure plugin directory exists
        socket_dir = os.path.dirname(socket_path)
        os.makedirs(socket_dir, exist_ok=True)

        # Remove stale socket
        if os.path.exists(socket_path):
            os.remove(socket_path)

        # Ensure shared bridge and migrate legacy per-lab bridges.
        await self._ensure_shared_bridge()
        await self._migrate_per_lab_bridges()

        # Discover existing state on startup (enables recovery after restart)
        await self._discover_existing_state()

        app = self.create_app()
        runner = web.AppRunner(app)
        await runner.setup()

        site = web.UnixSite(runner, socket_path)
        await site.start()

        # Set socket permissions so Docker can access it
        os.chmod(socket_path, 0o755)

        logger.info(f"Docker OVS plugin listening on {socket_path}")

        # Create plugin spec file for Docker discovery
        await self._create_plugin_spec(socket_path)

        # Start TTL cleanup if enabled
        await self._start_ttl_cleanup()
        # Start endpoint binding drift audit if enabled
        await self._start_endpoint_binding_audit()

        # Reconcile after plugin is listening.
        asyncio.create_task(self._post_start_reconcile())

        return runner

    async def _create_plugin_spec(self, socket_path: str) -> None:
        """Create plugin spec file for Docker discovery."""
        spec_dir = os.path.dirname(PLUGIN_SPEC_PATH)
        os.makedirs(spec_dir, exist_ok=True)

        with open(PLUGIN_SPEC_PATH, "w") as f:
            f.write(f"unix://{socket_path}\n")

        logger.info(f"Created plugin spec at {PLUGIN_SPEC_PATH}")

    async def shutdown(self) -> None:
        """Graceful shutdown - save state and stop cleanup task."""
        logger.info("Docker OVS plugin shutting down...")

        # Stop TTL cleanup task
        await self._stop_ttl_cleanup()
        # Stop endpoint binding audit task
        await self._stop_endpoint_binding_audit()

        # Save final state
        if self._state_dirty or True:  # Always save on shutdown
            logger.info("Saving plugin state before shutdown...")
            await self._save_state()

        logger.info("Docker OVS plugin shutdown complete")


# Singleton instance
_plugin_instance: DockerOVSPlugin | None = None


def get_docker_ovs_plugin() -> DockerOVSPlugin:
    """Get or create the singleton plugin instance."""
    global _plugin_instance
    if _plugin_instance is None:
        _plugin_instance = DockerOVSPlugin()
    return _plugin_instance


async def run_plugin_standalone() -> None:
    """Run the plugin as a standalone daemon."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    plugin = get_docker_ovs_plugin()
    runner = await plugin.start()

    # Wait for shutdown signal
    stop_event = asyncio.Event()

    def handle_signal():
        stop_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, handle_signal)

    logger.info("Plugin running. Press Ctrl+C to stop.")
    await stop_event.wait()

    logger.info("Shutting down...")
    await plugin.shutdown()
    await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(run_plugin_standalone())
