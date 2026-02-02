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
    CreateNetwork  → Create OVS bridge (once per lab) or register interface network
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
import ipaddress
import json
import logging
import os
import re
import secrets
import signal
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from aiohttp import web

from agent.config import settings

logger = logging.getLogger(__name__)

# Plugin configuration
PLUGIN_NAME = "archetype-ovs"
PLUGIN_SOCKET_PATH = f"/run/docker/plugins/{PLUGIN_NAME}.sock"
PLUGIN_SPEC_PATH = f"/etc/docker/plugins/{PLUGIN_NAME}.spec"

# State persistence path (in workspace directory)
STATE_PERSISTENCE_FILE = "docker_ovs_plugin_state.json"

# OVS configuration
OVS_BRIDGE_PREFIX = "ovs-"
VLAN_RANGE_START = 100
VLAN_RANGE_END = 4000


@dataclass
class LabBridge:
    """State for a lab's OVS bridge."""

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


@dataclass
class ManagementNetwork:
    """State for a lab's management network (eth0)."""

    lab_id: str
    network_id: str
    network_name: str  # archetype-mgmt-{lab_id}
    subnet: str  # e.g., "172.20.1.0/24"
    gateway: str  # e.g., "172.20.1.1"


class DockerOVSPlugin:
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

    def __init__(self):
        self.lab_bridges: dict[str, LabBridge] = {}  # lab_id -> LabBridge
        self.networks: dict[str, NetworkState] = {}  # network_id -> NetworkState
        self.endpoints: dict[str, EndpointState] = {}  # endpoint_id -> EndpointState
        self.management_networks: dict[str, ManagementNetwork] = {}  # lab_id -> ManagementNetwork
        self._lock = asyncio.Lock()
        self._started_at = datetime.now(timezone.utc)
        self._cleanup_task: asyncio.Task | None = None
        self._next_mgmt_subnet_index = 1  # For allocating management subnets
        self._pending_endpoint_reconnects: list[tuple[str, str, str]] = []

        # State persistence
        workspace = Path(settings.workspace_path)
        workspace.mkdir(parents=True, exist_ok=True)
        self._state_file = workspace / STATE_PERSISTENCE_FILE
        self._state_dirty = False  # Track if state needs saving

    # =========================================================================
    # OVS Operations
    # =========================================================================

    async def _run_cmd(self, cmd: list[str]) -> tuple[int, str, str]:
        """Run a shell command asynchronously."""
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return proc.returncode or 0, stdout.decode(), stderr.decode()

    async def _ovs_vsctl(self, *args: str) -> tuple[int, str, str]:
        """Run ovs-vsctl command."""
        return await self._run_cmd(["ovs-vsctl", *args])

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
                raise RuntimeError(f"Failed to create OVS bridge {bridge_name}: {stderr}")

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

    async def _attach_to_ovs(self, bridge_name: str, port_name: str, vlan_tag: int) -> bool:
        """Attach a veth to OVS bridge with VLAN tag."""
        code, _, stderr = await self._ovs_vsctl(
            "add-port", bridge_name, port_name,
            f"tag={vlan_tag}",
            "--", "set", "interface", port_name, "type=system"
        )
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

    def _allocate_vlan(self, lab_bridge: LabBridge) -> int:
        """Allocate next available VLAN tag."""
        vlan = lab_bridge.next_vlan
        lab_bridge.next_vlan += 1
        if lab_bridge.next_vlan > VLAN_RANGE_END:
            lab_bridge.next_vlan = VLAN_RANGE_START
        return vlan

    def _touch_lab(self, lab_id: str) -> None:
        """Update last_activity timestamp for TTL tracking."""
        if lab_id in self.lab_bridges:
            self.lab_bridges[lab_id].last_activity = datetime.now(timezone.utc)

    # =========================================================================
    # State Persistence
    # =========================================================================

    def _serialize_state(self) -> dict[str, Any]:
        """Serialize plugin state to a JSON-compatible dict."""
        return {
            "version": 1,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "next_mgmt_subnet_index": self._next_mgmt_subnet_index,
            "lab_bridges": {
                lab_id: {
                    "lab_id": bridge.lab_id,
                    "bridge_name": bridge.bridge_name,
                    "next_vlan": bridge.next_vlan,
                    "network_ids": list(bridge.network_ids),
                    "last_activity": bridge.last_activity.isoformat(),
                    "vxlan_tunnels": bridge.vxlan_tunnels,
                    "external_ports": bridge.external_ports,
                }
                for lab_id, bridge in self.lab_bridges.items()
            },
            "networks": {
                net_id: {
                    "network_id": net.network_id,
                    "lab_id": net.lab_id,
                    "interface_name": net.interface_name,
                    "bridge_name": net.bridge_name,
                }
                for net_id, net in self.networks.items()
            },
            "endpoints": {
                ep_id: {
                    "endpoint_id": ep.endpoint_id,
                    "network_id": ep.network_id,
                    "interface_name": ep.interface_name,
                    "host_veth": ep.host_veth,
                    "cont_veth": ep.cont_veth,
                    "vlan_tag": ep.vlan_tag,
                    "container_name": ep.container_name,
                }
                for ep_id, ep in self.endpoints.items()
            },
            "management_networks": {
                lab_id: {
                    "lab_id": mgmt.lab_id,
                    "network_id": mgmt.network_id,
                    "network_name": mgmt.network_name,
                    "subnet": mgmt.subnet,
                    "gateway": mgmt.gateway,
                }
                for lab_id, mgmt in self.management_networks.items()
            },
        }

    def _deserialize_state(self, data: dict[str, Any]) -> None:
        """Deserialize plugin state from a JSON dict."""
        version = data.get("version", 1)
        if version != 1:
            logger.warning(f"Unknown state file version {version}, attempting load anyway")

        self._next_mgmt_subnet_index = data.get("next_mgmt_subnet_index", 1)

        # Load lab bridges
        for lab_id, bridge_data in data.get("lab_bridges", {}).items():
            last_activity = datetime.now(timezone.utc)
            if bridge_data.get("last_activity"):
                try:
                    last_activity = datetime.fromisoformat(bridge_data["last_activity"])
                except (ValueError, TypeError):
                    pass

            self.lab_bridges[lab_id] = LabBridge(
                lab_id=bridge_data["lab_id"],
                bridge_name=bridge_data["bridge_name"],
                next_vlan=bridge_data.get("next_vlan", VLAN_RANGE_START),
                network_ids=set(bridge_data.get("network_ids", [])),
                last_activity=last_activity,
                vxlan_tunnels=bridge_data.get("vxlan_tunnels", {}),
                external_ports=bridge_data.get("external_ports", {}),
            )

        # Load networks
        for net_id, net_data in data.get("networks", {}).items():
            self.networks[net_id] = NetworkState(
                network_id=net_data["network_id"],
                lab_id=net_data["lab_id"],
                interface_name=net_data["interface_name"],
                bridge_name=net_data["bridge_name"],
            )

        # Load endpoints
        for ep_id, ep_data in data.get("endpoints", {}).items():
            self.endpoints[ep_id] = EndpointState(
                endpoint_id=ep_data["endpoint_id"],
                network_id=ep_data["network_id"],
                interface_name=ep_data["interface_name"],
                host_veth=ep_data["host_veth"],
                cont_veth=ep_data["cont_veth"],
                vlan_tag=ep_data["vlan_tag"],
                container_name=ep_data.get("container_name"),
            )

        # Load management networks
        for lab_id, mgmt_data in data.get("management_networks", {}).items():
            self.management_networks[lab_id] = ManagementNetwork(
                lab_id=mgmt_data["lab_id"],
                network_id=mgmt_data["network_id"],
                network_name=mgmt_data["network_name"],
                subnet=mgmt_data["subnet"],
                gateway=mgmt_data["gateway"],
            )

    async def _save_state(self) -> None:
        """Save plugin state to disk atomically.

        Uses temp file + rename for atomic writes to prevent corruption.
        Runs file I/O in thread pool to avoid blocking event loop.
        """
        try:
            state = self._serialize_state()
            tmp_path = self._state_file.with_suffix(".tmp")

            # Write to temp file in thread pool to avoid blocking event loop
            def write_state():
                with open(tmp_path, "w") as f:
                    json.dump(state, f, indent=2)
                # Atomic rename
                tmp_path.rename(self._state_file)

            await asyncio.to_thread(write_state)
            self._state_dirty = False

            logger.debug(
                f"Saved plugin state: {len(self.lab_bridges)} bridges, "
                f"{len(self.endpoints)} endpoints"
            )
        except Exception as e:
            logger.error(f"Failed to save plugin state: {e}")

    async def _load_state(self) -> bool:
        """Load plugin state from disk.

        Returns True if state was loaded successfully.
        """
        if not self._state_file.exists():
            logger.info("No persisted plugin state found, starting fresh")
            return False

        try:
            with open(self._state_file, "r") as f:
                data = json.load(f)

            self._deserialize_state(data)

            logger.info(
                f"Loaded plugin state: {len(self.lab_bridges)} bridges, "
                f"{len(self.networks)} networks, {len(self.endpoints)} endpoints"
            )
            return True

        except json.JSONDecodeError as e:
            logger.error(f"Corrupted state file, starting fresh: {e}")
            return False
        except Exception as e:
            logger.error(f"Failed to load plugin state: {e}")
            return False

    async def _mark_dirty_and_save(self) -> None:
        """Mark state as dirty and save to disk.

        Called after any state mutation to ensure persistence.
        """
        self._state_dirty = True
        await self._save_state()

    # =========================================================================
    # State Reconciliation (compares persisted state with OVS reality)
    # =========================================================================

    async def _reconcile_state(self) -> dict[str, Any]:
        """Reconcile persisted state with actual OVS state.

        This handles mismatches between what we think exists (persisted state)
        and what actually exists (OVS bridges/ports). Possible scenarios:

        1. Port in state but not in OVS: Remove from state (OVS was cleaned up)
        2. Port in OVS but not in state: Query Docker to determine if it's ours
        3. Bridge in state but not in OVS: Recreate bridge if Docker networks exist
        4. Endpoint in state but veth missing: Clean up endpoint

        Returns dict with reconciliation statistics.
        """
        stats = {
            "endpoints_removed": 0,
            "endpoints_recovered": 0,
            "endpoints_queued": 0,
            "bridges_recreated": 0,
            "ports_orphaned": 0,
        }

        # For each lab bridge in our state, verify it exists in OVS
        for lab_id, bridge in list(self.lab_bridges.items()):
            code, _, _ = await self._ovs_vsctl("br-exists", bridge.bridge_name)
            if code != 0:
                # Bridge doesn't exist - check if we should recreate it
                if bridge.network_ids:
                    # We have Docker networks expecting this bridge - recreate it
                    logger.warning(
                        f"Bridge {bridge.bridge_name} missing but has {len(bridge.network_ids)} networks, recreating"
                    )
                    await self._ensure_bridge(lab_id)
                    stats["bridges_recreated"] += 1
                else:
                    # No networks - clean up from state
                    logger.info(f"Removing orphaned bridge state for {bridge.bridge_name}")
                    del self.lab_bridges[lab_id]
                continue

            # Bridge exists - verify ports
            code, stdout, _ = await self._ovs_vsctl("list-ports", bridge.bridge_name)
            if code != 0:
                continue

            ovs_ports = set(stdout.strip().split("\n")) if stdout.strip() else set()

        # Verify each endpoint's host veth exists
        endpoints_to_remove: list[tuple[str, bool]] = []
        for ep_id, endpoint in self.endpoints.items():
            # Check if host veth exists
            code, _, _ = await self._run_cmd(["ip", "link", "show", endpoint.host_veth])
            if code != 0:
                # Host veth doesn't exist - queue reconnect after plugin starts.
                queued = self._queue_missing_endpoint_reconnect(endpoint)
                if queued:
                    logger.info(
                        f"Endpoint {ep_id[:12]} veth {endpoint.host_veth} missing, queued reconnect"
                    )
                    stats["endpoints_queued"] += 1
                else:
                    logger.info(
                        f"Endpoint {ep_id[:12]} veth {endpoint.host_veth} missing, removing from state"
                    )
                    stats["endpoints_removed"] += 1
                endpoints_to_remove.append((ep_id, queued))

        for ep_id, _queued in endpoints_to_remove:
            self.endpoints.pop(ep_id, None)

        if any(v > 0 for v in stats.values()):
            await self._save_state()
            logger.info(f"State reconciliation complete: {stats}")

        return stats

    def _queue_missing_endpoint_reconnect(self, endpoint: EndpointState) -> bool:
        if not endpoint.container_name:
            return False

        network_state = self.networks.get(endpoint.network_id)
        if not network_state:
            return False

        self._pending_endpoint_reconnects.append(
            (endpoint.container_name, endpoint.network_id, network_state.interface_name)
        )
        return True

    async def _reconnect_pending_endpoints(self) -> None:
        if not self._pending_endpoint_reconnects:
            return

        pending = list(self._pending_endpoint_reconnects)
        self._pending_endpoint_reconnects.clear()

        for container_name, network_id, interface_name in pending:
            ok = await self._reconnect_container_to_network(
                container_name, network_id, interface_name
            )
            lab_id = self.networks[network_id].lab_id if network_id in self.networks else "unknown"
            if ok:
                logger.info(
                    f"[lab {lab_id}] Reconnected {container_name}:{interface_name} to {network_id}"
                )
            else:
                logger.warning(
                    f"[lab {lab_id}] Failed to reconnect {container_name}:{interface_name} to {network_id}"
                )

    async def _ensure_lab_network_attachments(self) -> None:
        """Ensure containers are attached to all lab OVS networks (eth1..ethN)."""

        def _sync_attach_all() -> list[tuple[str, str, bool]]:
            import docker
            from docker.errors import NotFound, APIError

            client = docker.from_env(timeout=30)
            actions: list[tuple[str, str, bool]] = []

            networks_by_lab: dict[str, list[NetworkState]] = {}
            for network_state in self.networks.values():
                if network_state.interface_name == "eth0":
                    continue
                networks_by_lab.setdefault(network_state.lab_id, []).append(network_state)

            for lab_id, networks in networks_by_lab.items():
                try:
                    containers = client.containers.list(
                        all=True, filters={"label": f"archetype.lab_id={lab_id}"}
                    )
                except Exception:
                    continue

                for container in containers:
                    attached = container.attrs.get("NetworkSettings", {}).get("Networks", {})
                    for network_state in networks:
                        network_name = f"{lab_id}-{network_state.interface_name}"
                        if network_name in attached:
                            continue
                        try:
                            try:
                                network = client.networks.get(network_state.network_id)
                            except NotFound:
                                network = client.networks.get(network_name)
                            network.connect(container)
                            actions.append((container.name, network_name, True))
                        except APIError as e:
                            if "already exists" in str(e).lower():
                                continue
                            actions.append((container.name, network_name, False))
                        except Exception:
                            actions.append((container.name, network_name, False))

            return actions

        try:
            actions = await asyncio.to_thread(_sync_attach_all)
        except Exception as e:
            logger.warning(f"Failed to ensure lab network attachments: {e}")
            return

        for container_name, network_name, ok in actions:
            lab_id = "unknown"
            if "-" in network_name:
                lab_id = network_name.split("-", 1)[0]
            if ok:
                logger.info(f"[lab {lab_id}] Attached {container_name} to {network_name}")
            else:
                logger.warning(f"[lab {lab_id}] Failed to attach {container_name} to {network_name}")

    async def _reconnect_missing_endpoints_from_docker(self) -> None:
        """Reconnect containers where Docker thinks a network is attached but no host veth exists."""

        def _sync_reconnect_missing() -> list[tuple[str, str, str, bool]]:
            import docker
            from docker.errors import NotFound, APIError

            client = docker.from_env(timeout=30)

            def _host_veth_exists(endpoint_id: str) -> bool:
                if not endpoint_id:
                    return False
                prefix = f"vh{endpoint_id[:5]}"
                try:
                    for name in os.listdir("/sys/class/net"):
                        if name.startswith(prefix):
                            return True
                except Exception:
                    return False
                return False

            actions: list[tuple[str, str, str, bool]] = []
            for network_state in self.networks.values():
                network = None
                try:
                    network = client.networks.get(network_state.network_id)
                except NotFound:
                    network_name = f"{network_state.lab_id}-{network_state.interface_name}"
                    try:
                        network = client.networks.get(network_name)
                    except NotFound:
                        continue

                containers = network.attrs.get("Containers") or {}
                for container_id, info in containers.items():
                    endpoint_id = info.get("EndpointID", "")
                    if endpoint_id and _host_veth_exists(endpoint_id):
                        continue

                    try:
                        network.disconnect(container_id, force=True)
                    except (NotFound, APIError):
                        pass

                    try:
                        network.connect(container_id)
                        actions.append((container_id, network_state.interface_name, endpoint_id, True))
                    except Exception:
                        actions.append((container_id, network_state.interface_name, endpoint_id, False))

            return actions

        try:
            actions = await asyncio.to_thread(_sync_reconnect_missing)
        except Exception as e:
            logger.warning(f"Failed to scan Docker networks for missing veths: {e}")
            return

        for container_id, interface_name, endpoint_id, ok in actions:
            lab_id = None
            if endpoint_id and endpoint_id in self.endpoints:
                network_id = self.endpoints[endpoint_id].network_id
                lab_id = self.networks.get(network_id).lab_id if network_id in self.networks else None
            lab_label = lab_id or "unknown"
            if ok:
                logger.info(
                    f"[lab {lab_label}] Reconnected container {container_id[:12]}:{interface_name} "
                    f"(endpoint {endpoint_id[:12] if endpoint_id else 'unknown'})"
                )
            else:
                logger.warning(
                    f"[lab {lab_label}] Failed to reconnect container {container_id[:12]}:{interface_name} "
                    f"(endpoint {endpoint_id[:12] if endpoint_id else 'unknown'})"
                )

    async def _post_start_reconcile(self) -> None:
        await self._ensure_lab_network_attachments()
        await self._reconnect_pending_endpoints()
        await self._reconnect_missing_endpoints_from_docker()

    async def _reconnect_container_to_network(
        self, container_name: str, network_id: str, interface_name: str
    ) -> bool:
        """Reconnect container to network to recreate a missing host veth."""
        network_state = self.networks.get(network_id)
        network_name = None
        if network_state:
            network_name = f"{network_state.lab_id}-{interface_name}"

        def _sync_reconnect() -> bool:
            import docker
            from docker.errors import NotFound, APIError

            client = docker.from_env(timeout=30)

            try:
                network = client.networks.get(network_id)
            except NotFound:
                if network_name:
                    network = client.networks.get(network_name)
                else:
                    return False

            try:
                container = client.containers.get(container_name)
            except NotFound:
                return False

            try:
                network.disconnect(container, force=True)
            except (NotFound, APIError):
                pass

            network.connect(container)
            return True

        try:
            return await asyncio.to_thread(_sync_reconnect)
        except Exception as e:
            logger.warning(
                f"Failed to reconnect {container_name}:{interface_name} to {network_id}: {e}"
            )
            return False

    async def _cleanup_orphaned_ovs_ports(self) -> int:
        """Remove OVS ports that are not tracked in our state.

        These can occur after a crash where Docker created networks
        that we didn't track before the crash.

        Returns number of ports cleaned up.
        """
        cleaned = 0

        for lab_id, bridge in self.lab_bridges.items():
            # Get all ports on this bridge
            code, stdout, _ = await self._ovs_vsctl("list-ports", bridge.bridge_name)
            if code != 0:
                continue

            ovs_ports = set(stdout.strip().split("\n")) if stdout.strip() else set()

            # Get tracked host veths for this bridge
            tracked_veths = set()
            for endpoint in self.endpoints.values():
                network = self.networks.get(endpoint.network_id)
                if network and network.lab_id == lab_id:
                    tracked_veths.add(endpoint.host_veth)

            # Find orphaned ports (excluding special ports like VXLAN, external)
            for port in ovs_ports:
                if not port.startswith("vh"):
                    # Not a container veth, skip
                    continue

                if port not in tracked_veths:
                    # Orphaned port - clean it up
                    logger.warning(f"Removing orphaned OVS port: {port}")
                    await self._delete_port(bridge.bridge_name, port)
                    cleaned += 1

        return cleaned

    # =========================================================================
    # State Recovery (discovers existing OVS state on startup)
    # =========================================================================

    async def _discover_existing_state(self) -> None:
        """Load persisted state and reconcile with OVS on startup.

        Startup sequence:
        1. Load persisted state from disk (if exists)
        2. Reconcile with actual OVS state (clean orphans, detect missing)
        3. If no persisted state, discover from OVS bridges
        4. Clean up orphaned OVS ports not in our tracking

        This enables state recovery after:
        - Normal agent restart (persisted state matches reality)
        - Agent crash (persisted state may be stale)
        - OVS restart (bridges may be missing)
        """
        # Step 1: Try to load persisted state
        loaded = await self._load_state()

        if loaded:
            # Step 2: Reconcile persisted state with OVS reality
            logger.info("Reconciling persisted state with OVS...")
            await self._reconcile_state()

            # Step 3: Clean up orphaned ports
            orphaned = await self._cleanup_orphaned_ovs_ports()
            if orphaned > 0:
                logger.info(f"Cleaned up {orphaned} orphaned OVS ports")

            return

        # No persisted state - just ensure shared bridge exists
        # With the shared bridge architecture, we can't recover lab state from OVS alone
        # since all labs share the same bridge. Labs will be re-registered when
        # Docker networks are created.
        logger.info("No persisted state found, ensuring shared bridge exists...")

        # Ensure the shared bridge exists and is configured
        bridge_name = settings.ovs_bridge_name
        code, _, _ = await self._ovs_vsctl("br-exists", bridge_name)
        if code != 0:
            # Create the shared bridge
            code, _, stderr = await self._ovs_vsctl("add-br", bridge_name)
            if code != 0:
                logger.error(f"Failed to create shared OVS bridge: {stderr}")
                return

            await self._ovs_vsctl("set-fail-mode", bridge_name, "standalone")
            await self._run_cmd([
                "ovs-ofctl", "add-flow", bridge_name,
                "priority=1,actions=normal"
            ])
            await self._run_cmd(["ip", "link", "set", bridge_name, "up"])
            logger.info(f"Created shared OVS bridge: {bridge_name}")
        else:
            logger.info(f"Shared OVS bridge {bridge_name} exists")

        logger.info("State recovery complete (no lab state to recover)")

    async def _recover_bridge_state(self, bridge_name: str, skip_endpoints: bool = False) -> None:
        """Recover state for a single OVS bridge."""
        # Extract lab_id from bridge name (ovs-{lab_id[:12]})
        lab_id_prefix = bridge_name[len(OVS_BRIDGE_PREFIX):]

        # List ports on this bridge
        code, stdout, _ = await self._ovs_vsctl("list-ports", bridge_name)
        if code != 0:
            logger.warning(f"Failed to list ports on {bridge_name}")
            return

        ports = [p.strip() for p in stdout.strip().split("\n") if p.strip()]

        # Determine max VLAN in use
        max_vlan = VLAN_RANGE_START
        vxlan_tunnels: dict[int, str] = {}
        external_ports: dict[str, int] = {}

        for port_name in ports:
            # Get port info including VLAN tag
            code, stdout, _ = await self._ovs_vsctl("get", "port", port_name, "tag")
            if code == 0:
                try:
                    vlan_str = stdout.strip().strip("[]")
                    if vlan_str:
                        vlan = int(vlan_str)
                        max_vlan = max(max_vlan, vlan)
                except (ValueError, TypeError):
                    pass

            # Check if this is a VXLAN port
            code, stdout, _ = await self._ovs_vsctl("get", "interface", port_name, "type")
            if code == 0 and stdout.strip() == "vxlan":
                # Get VNI from options
                code, opt_stdout, _ = await self._ovs_vsctl(
                    "get", "interface", port_name, "options:key"
                )
                if code == 0:
                    try:
                        vni = int(opt_stdout.strip().strip('"'))
                        vxlan_tunnels[vni] = port_name
                    except (ValueError, TypeError):
                        pass

            # Check for external interface (not veth, not vxlan, not internal)
            elif code == 0 and stdout.strip() == "system":
                # Could be external interface if not a veth
                if not port_name.startswith("vh"):
                    code, tag_stdout, _ = await self._ovs_vsctl("get", "port", port_name, "tag")
                    if code == 0:
                        try:
                            tag_str = tag_stdout.strip().strip("[]")
                            vlan = int(tag_str) if tag_str else 0
                            external_ports[port_name] = vlan
                        except (ValueError, TypeError):
                            external_ports[port_name] = 0

        # Try to find the full lab_id by checking Docker containers
        full_lab_id = await self._find_lab_id_from_containers(lab_id_prefix)
        if not full_lab_id:
            full_lab_id = lab_id_prefix  # Fall back to prefix

        # Create LabBridge
        lab_bridge = LabBridge(
            lab_id=full_lab_id,
            bridge_name=bridge_name,
            next_vlan=max_vlan + 1,
            vxlan_tunnels=vxlan_tunnels,
            external_ports=external_ports,
        )
        self.lab_bridges[full_lab_id] = lab_bridge

        logger.info(
            f"Recovered bridge {bridge_name}: lab={full_lab_id}, "
            f"ports={len(ports)}, max_vlan={max_vlan}, "
            f"vxlan_tunnels={len(vxlan_tunnels)}, external={len(external_ports)}"
        )

        # Optionally recover endpoint state by matching veth ports to containers
        # This is expensive (nsenter for each port/container) and usually not needed
        # since Docker will re-register endpoints when containers reconnect
        if not skip_endpoints:
            await self._recover_endpoints_for_bridge(lab_bridge, ports)

    async def _find_lab_id_from_containers(self, lab_id_prefix: str) -> str | None:
        """Find full lab_id by checking Docker container labels."""
        def _sync_find():
            try:
                import docker
                client = docker.from_env()

                for container in client.containers.list(all=True):
                    labels = container.labels
                    lab_id = labels.get("archetype.lab_id", "")
                    if lab_id and lab_id.startswith(lab_id_prefix):
                        return lab_id
            except Exception as e:
                logger.debug(f"Error finding lab_id from containers: {e}")
            return None

        # Run synchronous Docker calls in thread pool to avoid blocking event loop
        return await asyncio.get_event_loop().run_in_executor(None, _sync_find)

    async def _recover_endpoints_for_bridge(
        self, lab_bridge: LabBridge, ports: list[str]
    ) -> None:
        """Recover endpoint state by matching veth ports to containers."""
        try:
            # Run synchronous Docker calls in thread pool
            def _get_container_pids():
                import docker
                client = docker.from_env()
                pids = {}
                for container in client.containers.list():
                    labels = container.labels
                    lab_id = labels.get("archetype.lab_id", "")
                    if lab_id and lab_id.startswith(lab_bridge.lab_id[:12]):
                        pids[container.name] = (
                            container.id,
                            container.attrs["State"]["Pid"],
                        )
                return pids

            container_pids = await asyncio.get_event_loop().run_in_executor(
                None, _get_container_pids
            )

            # For each veth port (vh* pattern), try to find its container
            for port_name in ports:
                if not port_name.startswith("vh"):
                    continue

                # Get VLAN tag
                code, stdout, _ = await self._ovs_vsctl("get", "port", port_name, "tag")
                if code != 0:
                    continue

                try:
                    vlan_str = stdout.strip().strip("[]")
                    vlan_tag = int(vlan_str) if vlan_str else VLAN_RANGE_START
                except (ValueError, TypeError):
                    vlan_tag = VLAN_RANGE_START

                # Try to find which container owns this port by checking ifindex
                for container_name, (container_id, pid) in container_pids.items():
                    interface_name = await self._find_interface_in_container(
                        pid, port_name
                    )
                    if interface_name:
                        # Found the container, create endpoint state
                        endpoint_id = f"recovered-{port_name}"
                        endpoint = EndpointState(
                            endpoint_id=endpoint_id,
                            network_id=f"recovered-{lab_bridge.lab_id}-{interface_name}",
                            interface_name=interface_name,
                            host_veth=port_name,
                            cont_veth=f"peer-{port_name}",  # We don't know the exact name
                            vlan_tag=vlan_tag,
                            container_name=container_name,
                        )
                        self.endpoints[endpoint_id] = endpoint
                        logger.debug(
                            f"Recovered endpoint: {container_name}:{interface_name} "
                            f"-> {port_name} (VLAN {vlan_tag})"
                        )
                        break

        except Exception as e:
            logger.warning(f"Error recovering endpoints: {e}")

    async def _find_interface_in_container(
        self, pid: int, host_veth: str
    ) -> str | None:
        """Find which interface in a container corresponds to a host veth.

        Uses ifindex matching via /sys/class/net.
        """
        try:
            # Get ifindex of host veth's peer
            code, stdout, _ = await self._run_cmd([
                "cat", f"/sys/class/net/{host_veth}/ifindex"
            ])
            if code != 0:
                return None

            host_ifindex = int(stdout.strip())

            # In the container namespace, find interface with matching peer ifindex
            # The peer's iflink should match our ifindex
            code, stdout, _ = await self._run_cmd([
                "nsenter", "-t", str(pid), "-n",
                "sh", "-c",
                "for iface in /sys/class/net/*/iflink; do "
                "echo $(dirname $iface | xargs basename):$(cat $iface); done"
            ])
            if code != 0:
                return None

            for line in stdout.strip().split("\n"):
                if ":" not in line:
                    continue
                iface_name, iflink = line.split(":", 1)
                try:
                    if int(iflink.strip()) == host_ifindex:
                        return iface_name
                except ValueError:
                    continue

        except Exception as e:
            logger.debug(f"Error finding interface in container: {e}")

        return None

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
        checks["management_networks_count"] = len(self.management_networks)

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

    async def _cleanup_expired_labs(self) -> None:
        """Remove resources for labs inactive beyond TTL."""
        now = datetime.now(timezone.utc)
        ttl = timedelta(seconds=settings.lab_ttl_seconds)

        expired_labs = []
        async with self._lock:
            for lab_id, bridge in list(self.lab_bridges.items()):
                age = now - bridge.last_activity
                if age > ttl:
                    expired_labs.append((lab_id, age))

        for lab_id, age in expired_labs:
            logger.info(f"Cleaning up expired lab {lab_id} (inactive {age})")
            await self._full_lab_cleanup(lab_id)

    async def _full_lab_cleanup(self, lab_id: str) -> None:
        """Clean up all resources for a lab."""
        async with self._lock:
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
                del self.endpoints[ep_id]

            # Clean up networks
            networks_to_remove = [
                net_id for net_id, net in self.networks.items()
                if net.lab_id == lab_id
            ]
            for net_id in networks_to_remove:
                del self.networks[net_id]

            # Remove lab tracking (don't delete shared bridge)
            del self.lab_bridges[lab_id]

            # Clean up management network if exists
            if lab_id in self.management_networks:
                await self.delete_management_network(lab_id)

            # Persist state after full lab cleanup
            await self._mark_dirty_and_save()

            logger.info(f"Cleaned up all resources for lab {lab_id}")

    # =========================================================================
    # Management Network (eth0 with DHCP/NAT)
    # =========================================================================

    def _allocate_mgmt_subnet(self) -> tuple[str, str]:
        """Allocate next available /24 subnet from the management range.

        Returns (subnet, gateway) tuple, e.g., ("172.20.1.0/24", "172.20.1.1")
        """
        base = ipaddress.ip_network(settings.mgmt_network_subnet_base)
        # Each lab gets a /24 subnet from the base /16
        subnet_index = self._next_mgmt_subnet_index
        self._next_mgmt_subnet_index += 1

        if self._next_mgmt_subnet_index > 255:
            self._next_mgmt_subnet_index = 1

        # Calculate the /24 subnet
        # Base is like 172.20.0.0/16, we want 172.20.{index}.0/24
        base_octets = str(base.network_address).split(".")
        subnet_str = f"{base_octets[0]}.{base_octets[1]}.{subnet_index}.0/24"
        gateway = f"{base_octets[0]}.{base_octets[1]}.{subnet_index}.1"

        return subnet_str, gateway

    async def create_management_network(
        self, lab_id: str, subnet: str | None = None
    ) -> ManagementNetwork:
        """Create Docker bridge network for management (eth0).

        This creates a standard Docker bridge network with NAT, providing:
        - DHCP-assigned IP addresses for containers
        - NAT for internet access
        - DNS resolution

        Args:
            lab_id: Lab identifier
            subnet: Optional subnet (auto-allocated if not provided)

        Returns:
            ManagementNetwork with network details
        """
        async with self._lock:
            # Check if already exists
            if lab_id in self.management_networks:
                return self.management_networks[lab_id]

            network_name = f"archetype-mgmt-{lab_id[:20]}"

            # Allocate subnet if not provided
            if subnet:
                gateway = str(ipaddress.ip_network(subnet, strict=False).network_address + 1)
            else:
                subnet, gateway = self._allocate_mgmt_subnet()

            # Wrap Docker operations in thread to avoid blocking event loop
            def _sync_create_network():
                import docker
                client = docker.from_env()
                nonlocal subnet, gateway

                # Check if network already exists
                try:
                    existing = client.networks.get(network_name)
                    # Get existing network info
                    network_id = existing.id
                    config = existing.attrs.get("IPAM", {}).get("Config", [{}])[0]
                    subnet = config.get("Subnet", subnet)
                    gateway = config.get("Gateway", gateway)
                    logger.info(f"Management network {network_name} already exists")
                    return network_id, subnet, gateway
                except docker.errors.NotFound:
                    # Create the network
                    ipam_pool = docker.types.IPAMPool(
                        subnet=subnet,
                        gateway=gateway,
                    )
                    ipam_config = docker.types.IPAMConfig(pool_configs=[ipam_pool])

                    network = client.networks.create(
                        name=network_name,
                        driver="bridge",
                        ipam=ipam_config,
                        options={
                            "com.docker.network.bridge.enable_ip_masquerade": (
                                "true" if settings.mgmt_network_enable_nat else "false"
                            ),
                        },
                        labels={
                            "archetype.lab_id": lab_id,
                            "archetype.type": "management",
                        },
                    )
                    logger.info(f"Created management network {network_name}: {subnet}")
                    return network.id, subnet, gateway

            try:
                network_id, subnet, gateway = await asyncio.to_thread(_sync_create_network)

                mgmt_net = ManagementNetwork(
                    lab_id=lab_id,
                    network_id=network_id,
                    network_name=network_name,
                    subnet=subnet,
                    gateway=gateway,
                )
                self.management_networks[lab_id] = mgmt_net

                # Persist state after management network creation
                await self._mark_dirty_and_save()

                return mgmt_net

            except Exception as e:
                logger.error(f"Failed to create management network: {e}")
                raise

    async def attach_to_management(self, container_id: str, lab_id: str) -> str | None:
        """Attach container to management network, returns assigned IP.

        Args:
            container_id: Docker container ID or name
            lab_id: Lab identifier

        Returns:
            Assigned IP address, or None if failed
        """
        mgmt_net = self.management_networks.get(lab_id)
        if not mgmt_net:
            # Create management network if it doesn't exist
            mgmt_net = await self.create_management_network(lab_id)

        # Wrap Docker operations in thread to avoid blocking event loop
        def _sync_attach():
            import docker
            client = docker.from_env()

            network = client.networks.get(mgmt_net.network_name)
            container = client.containers.get(container_id)

            # Check if already connected
            connected_networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})
            if mgmt_net.network_name in connected_networks:
                return connected_networks[mgmt_net.network_name].get("IPAddress")

            # Connect to network
            network.connect(container)

            # Get the assigned IP
            container.reload()
            ip_addr = container.attrs["NetworkSettings"]["Networks"].get(
                mgmt_net.network_name, {}
            ).get("IPAddress")

            logger.info(f"Attached {container_id[:12]} to management network, IP: {ip_addr}")
            return ip_addr

        try:
            return await asyncio.to_thread(_sync_attach)

        except Exception as e:
            logger.error(f"Failed to attach to management network: {e}")
            return None

    async def delete_management_network(self, lab_id: str) -> bool:
        """Remove management network for a lab.

        Args:
            lab_id: Lab identifier

        Returns:
            True if deleted, False otherwise
        """
        mgmt_net = self.management_networks.pop(lab_id, None)
        if not mgmt_net:
            return False

        # Wrap Docker operations in thread to avoid blocking event loop
        def _sync_delete() -> tuple[bool, bool]:
            """Returns (success, needs_save)"""
            import docker
            client = docker.from_env()

            try:
                network = client.networks.get(mgmt_net.network_name)
                network.remove()
                logger.info(f"Deleted management network {mgmt_net.network_name}")
                return True, True
            except docker.errors.NotFound:
                return True, False  # Already gone
            except docker.errors.APIError as e:
                if "has active endpoints" in str(e):
                    # Force disconnect all containers first
                    network = client.networks.get(mgmt_net.network_name)
                    for container_id in list(network.attrs.get("Containers", {}).keys()):
                        try:
                            network.disconnect(container_id, force=True)
                        except Exception:
                            pass
                    network.remove()
                    return True, True
                raise

        try:
            success, needs_save = await asyncio.to_thread(_sync_delete)
            if success and needs_save:
                await self._mark_dirty_and_save()
            return success

        except Exception as e:
            logger.error(f"Failed to delete management network: {e}")
            # Put it back in tracking since we failed
            self.management_networks[lab_id] = mgmt_net
            return False

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
        async with self._lock:
            lab_bridge = self.lab_bridges.get(lab_id)
            if not lab_bridge:
                raise ValueError(f"Lab bridge not found for {lab_id}")

            # Check if tunnel already exists
            if vni in lab_bridge.vxlan_tunnels:
                return lab_bridge.vxlan_tunnels[vni]

            vxlan_port = f"vx{vni}"

            # Create VXLAN interface
            code, _, stderr = await self._run_cmd([
                "ip", "link", "add", vxlan_port,
                "type", "vxlan",
                "id", str(vni),
                "local", local_ip,
                "remote", remote_ip,
                "dstport", str(settings.plugin_vxlan_dst_port),
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
        async with self._lock:
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
        async with self._lock:
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
        async with self._lock:
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
        async with self._lock:
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
                "vlan_range_used": (VLAN_RANGE_START, bridge.next_vlan - 1),
                "vxlan_tunnels": len(bridge.vxlan_tunnels),
                "external_interfaces": list(bridge.external_ports.keys()),
                "last_activity": bridge.last_activity.isoformat(),
            })

        return {
            "healthy": await self._check_ovs_health(),
            "labs_count": len(self.lab_bridges),
            "endpoints_count": len(self.endpoints),
            "networks_count": len(self.networks),
            "management_networks_count": len(self.management_networks),
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
    # Docker Plugin API Handlers
    # =========================================================================

    async def handle_activate(self, request: web.Request) -> web.Response:
        """Handle /Plugin.Activate - Return plugin capabilities."""
        return web.json_response({"Implements": ["NetworkDriver"]})

    async def handle_get_capabilities(self, request: web.Request) -> web.Response:
        """Handle /NetworkDriver.GetCapabilities."""
        return web.json_response({
            "Scope": "local",
            "ConnectivityScope": "local",
        })

    async def handle_create_network(self, request: web.Request) -> web.Response:
        """Handle /NetworkDriver.CreateNetwork - Register interface network."""
        data = await request.json()
        network_id = data.get("NetworkID", "")
        options = data.get("Options", {})

        # Get options
        generic_opts = options.get("com.docker.network.generic", {})
        lab_id = generic_opts.get("lab_id", "")
        interface_name = generic_opts.get("interface_name", "eth1")

        if not lab_id:
            return web.json_response({"Err": "lab_id option is required"})

        logger.info(f"Creating network {network_id[:12]} for lab={lab_id}, interface={interface_name}")

        async with self._lock:
            try:
                # Ensure lab bridge exists
                lab_bridge = await self._ensure_bridge(lab_id)

                # Register network
                network = NetworkState(
                    network_id=network_id,
                    lab_id=lab_id,
                    interface_name=interface_name,
                    bridge_name=lab_bridge.bridge_name,
                )
                self.networks[network_id] = network
                lab_bridge.network_ids.add(network_id)

                # Persist state after network creation
                await self._mark_dirty_and_save()

                logger.info(f"Network {network_id[:12]} created on bridge {lab_bridge.bridge_name}")

            except Exception as e:
                logger.error(f"Failed to create network: {e}")
                return web.json_response({"Err": str(e)})

        return web.json_response({})

    async def handle_delete_network(self, request: web.Request) -> web.Response:
        """Handle /NetworkDriver.DeleteNetwork."""
        data = await request.json()
        network_id = data.get("NetworkID", "")

        async with self._lock:
            network = self.networks.pop(network_id, None)
            if network:
                lab_bridge = self.lab_bridges.get(network.lab_id)
                if lab_bridge:
                    lab_bridge.network_ids.discard(network_id)
                    await self._maybe_delete_bridge(network.lab_id)
                logger.info(f"Deleted network {network_id[:12]}")

                # Persist state after network deletion
                await self._mark_dirty_and_save()

        return web.json_response({})

    async def handle_create_endpoint(self, request: web.Request) -> web.Response:
        """Handle /NetworkDriver.CreateEndpoint - Create veth pair for interface.

        This is called BEFORE the container starts. The veth pair is created
        and attached to OVS. Docker will move the container-side into the
        container's namespace during Join.
        """
        data = await request.json()
        network_id = data.get("NetworkID", "")
        endpoint_id = data.get("EndpointID", "")

        async with self._lock:
            network = self.networks.get(network_id)
            if not network:
                return web.json_response({"Err": f"Network {network_id[:12]} not found"})

            lab_bridge = self.lab_bridges.get(network.lab_id)
            if not lab_bridge:
                return web.json_response({"Err": f"Lab bridge for {network.lab_id} not found"})

            # Generate veth names
            host_veth, cont_veth = self._generate_veth_names(endpoint_id)

            # Allocate VLAN (isolated until hot_connect)
            vlan_tag = self._allocate_vlan(lab_bridge)

            # Create veth pair
            if not await self._create_veth_pair(host_veth, cont_veth):
                return web.json_response({"Err": f"Failed to create veth pair"})

            # Attach to OVS
            if not await self._attach_to_ovs(network.bridge_name, host_veth, vlan_tag):
                await self._run_cmd(["ip", "link", "delete", host_veth])
                return web.json_response({"Err": f"Failed to attach to OVS"})

            # Track endpoint
            endpoint = EndpointState(
                endpoint_id=endpoint_id,
                network_id=network_id,
                interface_name=network.interface_name,
                host_veth=host_veth,
                cont_veth=cont_veth,
                vlan_tag=vlan_tag,
            )
            self.endpoints[endpoint_id] = endpoint

            # Update activity timestamp
            self._touch_lab(network.lab_id)

            # Persist state after endpoint creation
            await self._mark_dirty_and_save()

            logger.info(
                f"Created endpoint {endpoint_id[:12]}: {host_veth} <-> {cont_veth} "
                f"({network.interface_name}, VLAN {vlan_tag})"
            )

        return web.json_response({"Interface": {}})

    async def handle_delete_endpoint(self, request: web.Request) -> web.Response:
        """Handle /NetworkDriver.DeleteEndpoint - Clean up veth pair."""
        data = await request.json()
        network_id = data.get("NetworkID", "")
        endpoint_id = data.get("EndpointID", "")

        async with self._lock:
            endpoint = self.endpoints.pop(endpoint_id, None)
            if endpoint:
                network = self.networks.get(network_id)
                if network:
                    await self._delete_port(network.bridge_name, endpoint.host_veth)
                logger.info(f"Deleted endpoint {endpoint_id[:12]}")

                # Persist state after endpoint deletion
                await self._mark_dirty_and_save()

        return web.json_response({})

    async def handle_join(self, request: web.Request) -> web.Response:
        """Handle /NetworkDriver.Join - Provide interface config to Docker.

        Docker will move the veth into the container namespace and rename it.
        This happens BEFORE the container's init process runs.
        """
        data = await request.json()
        endpoint_id = data.get("EndpointID", "")
        sandbox_key = data.get("SandboxKey", "")

        async with self._lock:
            endpoint = self.endpoints.get(endpoint_id)
            if not endpoint:
                return web.json_response({"Err": f"Endpoint {endpoint_id[:12]} not found"})

            logger.info(
                f"Join endpoint {endpoint_id[:12]} -> {endpoint.interface_name} "
                f"(sandbox: {sandbox_key})"
            )

            return web.json_response({
                "InterfaceName": {
                    "SrcName": endpoint.cont_veth,
                    "DstPrefix": endpoint.interface_name.rstrip("0123456789"),
                },
            })

    async def handle_leave(self, request: web.Request) -> web.Response:
        """Handle /NetworkDriver.Leave - Container disconnecting."""
        data = await request.json()
        endpoint_id = data.get("EndpointID", "")
        logger.debug(f"Leave endpoint {endpoint_id[:12]}")
        return web.json_response({})

    async def handle_endpoint_oper_info(self, request: web.Request) -> web.Response:
        """Handle /NetworkDriver.EndpointOperInfo - Return endpoint operational info."""
        data = await request.json()
        endpoint_id = data.get("EndpointID", "")
        logger.debug(f"EndpointOperInfo for {endpoint_id[:12]}")
        # Return empty Value - Docker just wants to know the endpoint exists
        return web.json_response({"Value": {}})

    async def handle_discover_new(self, request: web.Request) -> web.Response:
        """Handle /NetworkDriver.DiscoverNew."""
        return web.json_response({})

    async def handle_discover_delete(self, request: web.Request) -> web.Response:
        """Handle /NetworkDriver.DiscoverDelete."""
        return web.json_response({})

    async def handle_program_external_connectivity(self, request: web.Request) -> web.Response:
        """Handle /NetworkDriver.ProgramExternalConnectivity."""
        return web.json_response({})

    async def handle_revoke_external_connectivity(self, request: web.Request) -> web.Response:
        """Handle /NetworkDriver.RevokeExternalConnectivity."""
        return web.json_response({})

    # =========================================================================
    # VLAN Management API (for hot-connect / topology links)
    # =========================================================================

    async def _discover_endpoint(
        self,
        lab_id: str,
        container_name: str,
        interface_name: str,
    ) -> "EndpointState | None":
        """Discover an endpoint by matching interface name to untracked endpoints.

        After agent restart, endpoints may have container_name=None because
        the recovery couldn't match veth pairs back to containers. This method
        uses the Docker network membership to find the right veth port.

        Args:
            lab_id: Lab identifier
            container_name: Container name
            interface_name: Interface name inside container

        Returns:
            EndpointState if found, None otherwise
        """
        import docker

        try:
            # Get container's network memberships
            def _get_container_networks():
                client = docker.from_env()
                container = client.containers.get(container_name)
                return container.attrs["NetworkSettings"]["Networks"]

            networks = await asyncio.to_thread(_get_container_networks)

            # Try each attached network to match EndpointID or NetworkID
            for net_name, net_info in networks.items():
                target_endpoint_id = net_info.get("EndpointID")
                target_network_id = net_info.get("NetworkID")

                # Best match: EndpointID from Docker
                if target_endpoint_id and target_endpoint_id in self.endpoints:
                    ep = self.endpoints[target_endpoint_id]
                    if ep.interface_name == interface_name:
                        ep.container_name = container_name
                        logger.info(
                            f"Matched endpoint via EndpointID: {container_name}:{interface_name} -> {ep.host_veth}"
                        )
                        return ep

                # Next best: match by Docker NetworkID + interface name
                if target_network_id:
                    for ep in self.endpoints.values():
                        if ep.network_id == target_network_id and ep.interface_name == interface_name:
                            ep.container_name = container_name
                            logger.info(
                                f"Matched endpoint via NetworkID: {container_name}:{interface_name} -> {ep.host_veth}"
                            )
                            return ep

            # Attempt to reconstruct endpoint state when tracking is missing.
            # This can happen after agent restarts where endpoints are not loaded,
            # but Docker networks and OVS ports still exist.
            code, stdout, _ = await self._ovs_vsctl("list-ports", settings.ovs_bridge_name)
            ovs_ports = set(stdout.strip().split("\n")) if code == 0 and stdout.strip() else set()

            for net_name, net_info in networks.items():
                target_endpoint_id = net_info.get("EndpointID")
                target_network_id = net_info.get("NetworkID")
                if not target_endpoint_id or not target_network_id:
                    continue

                network = self.networks.get(target_network_id)
                if not network or network.interface_name != interface_name:
                    continue

                port_prefix = f"vh{target_endpoint_id[:5]}"
                host_veth = next((p for p in ovs_ports if p.startswith(port_prefix)), None)
                if not host_veth:
                    continue

                vlan_tag = 0
                code, tag_stdout, _ = await self._ovs_vsctl("get", "port", host_veth, "tag")
                if code == 0:
                    tag_str = tag_stdout.strip().strip("[]")
                    if tag_str:
                        try:
                            vlan_tag = int(tag_str)
                        except ValueError:
                            vlan_tag = 0

                endpoint = EndpointState(
                    endpoint_id=target_endpoint_id,
                    network_id=target_network_id,
                    interface_name=interface_name,
                    host_veth=host_veth,
                    cont_veth="",
                    vlan_tag=vlan_tag,
                    container_name=container_name,
                )
                self.endpoints[target_endpoint_id] = endpoint
                await self._mark_dirty_and_save()
                logger.info(
                    f"Reconstructed endpoint: {container_name}:{interface_name} -> {host_veth}"
                )
                return endpoint

            # Fallback: match by interface name for untracked endpoints
            for ep in self.endpoints.values():
                if ep.interface_name == interface_name and not ep.container_name:
                    code, _, _ = await self._ovs_vsctl("get", "port", ep.host_veth, "tag")
                    if code == 0:
                        ep.container_name = container_name
                        logger.info(f"Matched endpoint by interface: {container_name}:{interface_name} -> {ep.host_veth}")
                        return ep

            logger.warning(f"Could not find endpoint for {container_name}:{interface_name}")
            return None

        except Exception as e:
            logger.error(f"Error discovering endpoint {container_name}:{interface_name}: {e}")
            return None

    async def hot_connect(
        self,
        lab_id: str,
        container_a: str,
        iface_a: str,
        container_b: str,
        iface_b: str,
    ) -> int | None:
        """Connect two interfaces by setting them to the same VLAN.

        Args:
            lab_id: Lab identifier
            container_a: First container name
            iface_a: Interface on first container (e.g., "eth1")
            container_b: Second container name
            iface_b: Interface on second container (e.g., "eth1")

        Returns:
            Shared VLAN tag on success, None on failure
        """
        async with self._lock:
            lab_bridge = self.lab_bridges.get(lab_id)
            if not lab_bridge:
                logger.error(f"Lab bridge not found for {lab_id}")
                return None

            # Find endpoints by container name and interface
            ep_a = None
            ep_b = None

            for endpoint in self.endpoints.values():
                if endpoint.container_name == container_a and endpoint.interface_name == iface_a:
                    ep_a = endpoint
                elif endpoint.container_name == container_b and endpoint.interface_name == iface_b:
                    ep_b = endpoint

            # If endpoints not found, try to discover them on-demand
            # This handles cases where agent restarted and lost tracking state
            if not ep_a:
                ep_a = await self._discover_endpoint(lab_id, container_a, iface_a)
            if not ep_b:
                ep_b = await self._discover_endpoint(lab_id, container_b, iface_b)

            if not ep_a or not ep_b:
                logger.error(f"Endpoints not found for {container_a}:{iface_a} or {container_b}:{iface_b}")
                return None

            # Use VLAN from endpoint A
            shared_vlan = ep_a.vlan_tag

            # Update endpoint B to same VLAN
            code, _, stderr = await self._ovs_vsctl(
                "set", "port", ep_b.host_veth, f"tag={shared_vlan}"
            )
            if code != 0:
                logger.error(f"Failed to set VLAN: {stderr}")
                return None

            ep_b.vlan_tag = shared_vlan

            # Update activity timestamp
            self._touch_lab(lab_id)

            # Persist state after hot-connect
            await self._mark_dirty_and_save()

            logger.info(
                f"Connected {container_a}:{iface_a} <-> {container_b}:{iface_b} "
                f"(VLAN {shared_vlan})"
            )
            return shared_vlan

    async def hot_disconnect(
        self,
        lab_id: str,
        container: str,
        interface: str,
    ) -> int | None:
        """Disconnect an interface by giving it a unique VLAN.

        Args:
            lab_id: Lab identifier
            container: Container name
            interface: Interface name

        Returns:
            New unique VLAN tag on success, None on failure
        """
        async with self._lock:
            lab_bridge = self.lab_bridges.get(lab_id)
            if not lab_bridge:
                return None

            # Find endpoint
            endpoint = None
            for ep in self.endpoints.values():
                if ep.container_name == container and ep.interface_name == interface:
                    endpoint = ep
                    break

            if not endpoint:
                return None

            # Allocate new unique VLAN
            new_vlan = self._allocate_vlan(lab_bridge)

            code, _, stderr = await self._ovs_vsctl(
                "set", "port", endpoint.host_veth, f"tag={new_vlan}"
            )
            if code != 0:
                logger.error(f"Failed to set VLAN: {stderr}")
                return None

            endpoint.vlan_tag = new_vlan

            # Update activity timestamp
            self._touch_lab(lab_id)

            # Persist state after hot-disconnect
            await self._mark_dirty_and_save()

            logger.info(f"Disconnected {container}:{interface} (new VLAN {new_vlan})")
            return new_vlan

    # =========================================================================
    # Carrier State Management (for link up/down simulation)
    # =========================================================================

    async def _get_container_pid(self, container_name: str) -> int | None:
        """Get the PID of a container's init process.

        Uses docker inspect to get the container's namespace PID.

        Returns:
            PID on success, None if container not found or not running.
        """
        def _sync_get_pid() -> int | None:
            try:
                import docker
                client = docker.from_env()
                container = client.containers.get(container_name)
                if container.status != "running":
                    logger.warning(f"Container {container_name} is not running")
                    return None
                return container.attrs["State"]["Pid"]
            except Exception as e:
                logger.error(f"Failed to get PID for container {container_name}: {e}")
                return None

        return await asyncio.to_thread(_sync_get_pid)

    async def set_carrier_state(
        self,
        lab_id: str,
        container: str,
        interface: str,
        state: str,
    ) -> bool:
        """Set the carrier state of an interface inside a container.

        This uses `ip link set carrier on/off` via nsenter to simulate
        link up/down at the physical layer. The interface remains configured
        but no traffic can flow when carrier is off.

        Args:
            lab_id: Lab identifier
            container: Container name
            interface: Interface name in container (e.g., "eth1")
            state: "on" or "off"

        Returns:
            True if carrier state was set successfully, False otherwise.
        """
        if state not in ("on", "off"):
            logger.error(f"Invalid carrier state: {state}, must be 'on' or 'off'")
            return False

        # Get container PID for nsenter
        pid = await self._get_container_pid(container)
        if not pid:
            return False

        # Set carrier state inside container namespace
        code, _, stderr = await self._run_cmd([
            "nsenter", "-t", str(pid), "-n",
            "ip", "link", "set", interface, "carrier", state,
        ])

        if code != 0:
            logger.error(f"Failed to set carrier {state} on {container}:{interface}: {stderr}")
            return False

        logger.info(f"Set carrier {state} on {container}:{interface}")
        return True

    async def isolate_port(
        self,
        lab_id: str,
        container: str,
        interface: str,
    ) -> int | None:
        """Isolate a port by giving it a unique VLAN and setting carrier off.

        This effectively disconnects the interface from any L2 domain and
        simulates a cable disconnect at the physical layer.

        Args:
            lab_id: Lab identifier
            container: Container name
            interface: Interface name in container

        Returns:
            New VLAN tag on success, None on failure.
        """
        # First, isolate via VLAN (hot_disconnect logic)
        new_vlan = await self.hot_disconnect(lab_id, container, interface)
        if new_vlan is None:
            return None

        # Then set carrier off to simulate physical disconnect
        if not await self.set_carrier_state(lab_id, container, interface, "off"):
            logger.warning(f"VLAN isolated but carrier off failed for {container}:{interface}")
            # Return the VLAN anyway - isolation still works at L2

        return new_vlan

    async def restore_port(
        self,
        lab_id: str,
        container: str,
        interface: str,
        target_vlan: int,
    ) -> bool:
        """Restore a port to a specific VLAN and set carrier on.

        This reconnects the interface to the specified L2 domain and
        simulates a cable reconnect at the physical layer.

        Args:
            lab_id: Lab identifier
            container: Container name
            interface: Interface name in container
            target_vlan: VLAN tag to set (should match peer's VLAN)

        Returns:
            True on success, False on failure.
        """
        async with self._lock:
            lab_bridge = self.lab_bridges.get(lab_id)
            if not lab_bridge:
                logger.error(f"Lab bridge not found for {lab_id}")
                return False

            # Find endpoint
            endpoint = None
            for ep in self.endpoints.values():
                if ep.container_name == container and ep.interface_name == interface:
                    endpoint = ep
                    break

            if not endpoint:
                logger.error(f"Endpoint not found for {container}:{interface}")
                return False

            # Set VLAN to match peer
            code, _, stderr = await self._ovs_vsctl(
                "set", "port", endpoint.host_veth, f"tag={target_vlan}"
            )
            if code != 0:
                logger.error(f"Failed to set VLAN {target_vlan}: {stderr}")
                return False

            endpoint.vlan_tag = target_vlan
            self._touch_lab(lab_id)
            await self._mark_dirty_and_save()

        # Set carrier on to restore physical link
        if not await self.set_carrier_state(lab_id, container, interface, "on"):
            logger.warning(f"VLAN restored but carrier on failed for {container}:{interface}")
            return False

        logger.info(f"Restored {container}:{interface} to VLAN {target_vlan}")
        return True

    async def get_endpoint_vlan(
        self,
        lab_id: str,
        container: str,
        interface: str,
    ) -> int | None:
        """Get the current VLAN tag for an endpoint.

        Args:
            lab_id: Lab identifier
            container: Container name
            interface: Interface name

        Returns:
            VLAN tag if found, None otherwise.
        """
        async with self._lock:
            for ep in self.endpoints.values():
                if ep.container_name == container and ep.interface_name == interface:
                    return ep.vlan_tag

        ep = await self._discover_endpoint(lab_id, container, interface)
        if ep:
            return ep.vlan_tag
        return None

    def get_container_interface_mapping(
        self,
        lab_id: str,
        container_name: str,
    ) -> dict[str, str]:
        """Get mapping of host veth to intended interface name for a container.

        This is used to fix interface names after container start, when Docker
        may have assigned incorrect names due to network attachment ordering.

        This method works even when endpoint state is lost (e.g., after agent
        restart) by using the network definitions which are more persistent.

        Args:
            lab_id: Lab identifier
            container_name: Container name to get mapping for

        Returns:
            Dict mapping host_veth name to intended interface name.
            Example: {"vh0f3e74300da": "eth1", "vhd93aad3d6f7": "eth2"}
        """
        mapping = {}
        lab_bridge = self.lab_bridges.get(lab_id)
        if not lab_bridge:
            return mapping

        # First try using endpoint state (preferred when available)
        for ep in self.endpoints.values():
            network = self.networks.get(ep.network_id)
            if not network or network.lab_id != lab_id:
                continue

            # Only include if container_name matches or isn't set yet
            if ep.container_name and ep.container_name != container_name:
                continue

            # Skip management network (eth0)
            if ep.interface_name == "eth0":
                continue

            mapping[ep.host_veth] = ep.interface_name

        # If we have endpoint mappings, return them
        if mapping:
            return mapping

        # Fallback: build mapping from network definitions
        # This works when endpoint state is lost but networks still exist
        # The mapping will be applied to ALL containers in the lab (caller filters)
        for network in self.networks.values():
            if network.lab_id != lab_id:
                continue
            # Skip management network
            if network.interface_name == "eth0":
                continue
            # We don't know the exact host_veth for each network without endpoint state,
            # but we can return the intended interface names for the caller to match
            # by checking which veth connects to the container
            # Return network_id -> interface_name for caller to resolve
            mapping[f"network:{network.network_id}"] = network.interface_name

        return mapping

    async def set_endpoint_container_name(self, endpoint_id: str, container_name: str) -> None:
        """Associate endpoint with container name for hot-connect lookups."""
        async with self._lock:
            endpoint = self.endpoints.get(endpoint_id)
            if endpoint:
                endpoint.container_name = container_name
                # Persist state after container name association
                await self._mark_dirty_and_save()

    async def get_endpoint_host_veth(
        self,
        lab_id: str,
        container_name: str,
        interface_name: str,
    ) -> str | None:
        """Get host veth name for a container interface.

        Falls back to endpoint discovery if tracking state is missing.
        """
        async with self._lock:
            for ep in self.endpoints.values():
                network = self.networks.get(ep.network_id)
                if not network or network.lab_id != lab_id:
                    continue
                if ep.container_name == container_name and ep.interface_name == interface_name:
                    return ep.host_veth

        ep = await self._discover_endpoint(lab_id, container_name, interface_name)
        if ep:
            return ep.host_veth
        return None

    def get_lab_status(self, lab_id: str) -> dict[str, Any] | None:
        """Get status of a lab's networks and endpoints."""
        lab_bridge = self.lab_bridges.get(lab_id)
        if not lab_bridge:
            return None

        networks_info = []
        for net_id in lab_bridge.network_ids:
            network = self.networks.get(net_id)
            if network:
                networks_info.append({
                    "network_id": net_id[:12],
                    "interface_name": network.interface_name,
                })

        endpoints_info = []
        for ep in self.endpoints.values():
            network = self.networks.get(ep.network_id)
            if network and network.lab_id == lab_id:
                endpoints_info.append({
                    "endpoint_id": ep.endpoint_id[:12],
                    "container": ep.container_name,
                    "interface": ep.interface_name,
                    "host_veth": ep.host_veth,
                    "vlan": ep.vlan_tag,
                })

        return {
            "lab_id": lab_id,
            "bridge_name": lab_bridge.bridge_name,
            "networks": networks_info,
            "endpoints": endpoints_info,
        }

    def get_all_labs(self) -> list[str]:
        """Get list of all lab IDs with active bridges."""
        return list(self.lab_bridges.keys())

    def get_active_host_veths(self) -> set[str]:
        """Get set of all host-side veth names being tracked.

        Used by cleanup manager to avoid deleting active veths.
        """
        return {ep.host_veth for ep in self.endpoints.values()}

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
