"""State management mixin for DockerOVSPlugin.

Contains State Persistence, Stale State Garbage Collection,
State Reconciliation, and State Recovery sections.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from agent.config import settings
from agent.network.docker_plugin import (
    EndpointState,
    LabBridge,
    LINKED_VLAN_START,
    NetworkState,
    OVS_BRIDGE_PREFIX,
    VLAN_RANGE_END,
    VLAN_RANGE_START,
)

logger = logging.getLogger(__name__)


class PluginStateMixin:
    """State management mixin for DockerOVSPlugin.

    Provides state persistence, stale state garbage collection,
    state reconciliation, and state recovery functionality.
    """

    # =========================================================================
    # State Persistence
    # =========================================================================

    def _serialize_state(self) -> dict[str, Any]:
        """Serialize plugin state to a JSON-compatible dict."""
        return {
            "version": 1,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "global_next_vlan": self._global_next_vlan,
            "global_next_linked_vlan": self._global_next_linked_vlan,
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
        }

    def _deserialize_state(self, data: dict[str, Any]) -> None:
        """Deserialize plugin state from a JSON dict."""
        version = data.get("version", 1)
        if version != 1:
            logger.warning(f"Unknown state file version {version}, attempting load anyway")

        self._global_next_vlan = data.get("global_next_vlan", VLAN_RANGE_START)
        self._global_next_linked_vlan = data.get("global_next_linked_vlan", LINKED_VLAN_START)

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

        self._allocated_vlans = {ep.vlan_tag for ep in self.endpoints.values()}
        if "global_next_vlan" not in data:
            if self._allocated_vlans:
                max_used = max(self._allocated_vlans)
                next_vlan = max_used + 1
                if next_vlan > VLAN_RANGE_END:
                    next_vlan = VLAN_RANGE_START
                self._global_next_vlan = next_vlan


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
            if self._migrate_state_to_shared_bridge():
                await self._save_state()

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

    def _migrate_state_to_shared_bridge(self) -> bool:
        """Rewrite persisted per-lab bridge references to the shared bridge."""
        shared_bridge = settings.ovs_bridge_name
        updated = False

        for lab_id, bridge in self.lab_bridges.items():
            if bridge.bridge_name != shared_bridge:
                logger.warning(
                    f"Updating persisted bridge for lab {lab_id} "
                    f"from {bridge.bridge_name} to {shared_bridge}"
                )
                bridge.bridge_name = shared_bridge
                updated = True

        for net_id, network in self.networks.items():
            if network.bridge_name != shared_bridge:
                logger.warning(
                    f"Updating persisted network {net_id[:12]} bridge "
                    f"from {network.bridge_name} to {shared_bridge}"
                )
                network.bridge_name = shared_bridge
                updated = True

        return updated

    async def _mark_dirty_and_save(self) -> None:
        """Mark state as dirty and save to disk.

        Called after any state mutation to ensure persistence.
        """
        self._state_dirty = True
        await self._save_state()

    # =========================================================================
    # Stale State Garbage Collection
    # =========================================================================

    async def cleanup_stale_state(self) -> dict[str, int]:
        """Remove plugin state entries that no longer correspond to real Docker resources.

        Cross-references self.networks and self.endpoints against actual Docker
        networks and containers. Entries whose backing Docker resource no longer
        exists are removed from plugin state.

        Must be called within ``async with self._locked()`` for thread safety.

        Returns dict with cleanup statistics.
        """
        stats = {"networks_removed": 0, "endpoints_removed": 0}

        # --- Discover live Docker networks ---
        code, stdout, _ = await self._run_cmd(
            ["docker", "network", "ls", "--no-trunc", "--format", "{{.ID}}"]
        )
        if code != 0:
            logger.warning("cleanup_stale_state: failed to list Docker networks, skipping")
            return stats
        live_network_ids: set[str] = set()
        if stdout.strip():
            live_network_ids = {line.strip() for line in stdout.strip().splitlines() if line.strip()}

        # --- Discover live Docker containers (by name, matching endpoint.container_name) ---
        live_container_names: set[str] = set()
        code, stdout, _ = await self._run_cmd(
            ["docker", "ps", "-a", "--no-trunc", "--format", "{{.Names}}"]
        )
        if code != 0:
            logger.warning("cleanup_stale_state: failed to list Docker containers, skipping")
            return stats
        if stdout.strip():
            live_container_names = {line.strip() for line in stdout.strip().splitlines() if line.strip()}

        # --- Clean stale networks ---
        stale_network_ids: list[str] = []
        for network_id in list(self.networks.keys()):
            # Docker network IDs from the plugin are full-length (64 chars).
            # Check both exact match and prefix match for safety.
            found = network_id in live_network_ids or any(
                live_id.startswith(network_id) or network_id.startswith(live_id)
                for live_id in live_network_ids
            )
            if not found:
                stale_network_ids.append(network_id)

        for network_id in stale_network_ids:
            network = self.networks.pop(network_id)
            # Also remove from lab_bridge's network_ids set
            lab_bridge = self.lab_bridges.get(network.lab_id)
            if lab_bridge and network_id in lab_bridge.network_ids:
                lab_bridge.network_ids.discard(network_id)
            logger.info(
                "cleanup_stale_state: removed stale network %s (lab=%s, iface=%s)",
                network_id[:12],
                network.lab_id,
                network.interface_name,
            )
            stats["networks_removed"] += 1

        # --- Clean stale endpoints ---
        # An endpoint is stale if:
        #   1. Its network_id is no longer tracked (was just cleaned or was orphaned), AND
        #   2. Its container_name does not match any live container
        stale_endpoint_ids: list[str] = []
        for endpoint_id, ep in list(self.endpoints.items()):
            network_gone = ep.network_id not in self.networks
            container_gone = True
            if ep.container_name:
                container_gone = ep.container_name not in live_container_names
            # Conservative: only remove if BOTH the network and container are gone,
            # OR if the network is gone and there's no container_name to check.
            if network_gone and container_gone:
                stale_endpoint_ids.append(endpoint_id)

        for endpoint_id in stale_endpoint_ids:
            ep = self.endpoints.pop(endpoint_id)
            # Delete the actual OVS port (may already be gone)
            if ep.host_veth:
                try:
                    await self._delete_port(self._bridge_name, ep.host_veth)
                except Exception:
                    pass  # Port already removed
            self._release_vlan(ep.vlan_tag)
            logger.info(
                "cleanup_stale_state: removed stale endpoint %s (container=%s, iface=%s, vlan=%d)",
                endpoint_id[:12],
                ep.container_name or "<unknown>",
                ep.interface_name,
                ep.vlan_tag,
            )
            stats["endpoints_removed"] += 1

        # --- Clean empty lab_bridges ---
        empty_labs: list[str] = []
        for lab_id, bridge in list(self.lab_bridges.items()):
            if not bridge.network_ids and not bridge.vxlan_tunnels and not bridge.external_ports:
                # No networks, tunnels, or external ports -- check no endpoints reference this lab
                has_endpoints = any(
                    self.networks.get(ep.network_id, None) and
                    self.networks[ep.network_id].lab_id == lab_id
                    for ep in self.endpoints.values()
                )
                if not has_endpoints:
                    empty_labs.append(lab_id)

        for lab_id in empty_labs:
            del self.lab_bridges[lab_id]
            logger.info("cleanup_stale_state: removed empty lab_bridge for lab %s", lab_id)

        # --- Persist if anything changed ---
        total_removed = stats["networks_removed"] + stats["endpoints_removed"] + len(empty_labs)
        if total_removed > 0:
            self._state_dirty = True
            await self._save_state()
            logger.info(
                "cleanup_stale_state: cleaned %d networks, %d endpoints, %d empty bridges",
                stats["networks_removed"],
                stats["endpoints_removed"],
                len(empty_labs),
            )
        else:
            logger.debug("cleanup_stale_state: no stale entries found")

        return stats

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

            set(stdout.strip().split("\n")) if stdout.strip() else set()

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
            endpoint = self.endpoints.pop(ep_id, None)
            if endpoint:
                self._release_vlan(endpoint.vlan_tag)

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
        """Ensure containers are attached to all lab OVS networks (eth0..ethN)."""

        def _sync_attach_all() -> list[tuple[str, str, bool]]:
            import docker
            from docker.errors import NotFound, APIError

            client = docker.from_env(timeout=30)
            actions: list[tuple[str, str, bool]] = []

            networks_by_lab: dict[str, list[NetworkState]] = {}
            for network_state in self.networks.values():
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
                    # Build set of already-attached network IDs to prevent duplicates
                    attached_network_ids = set()
                    for net_info in attached.values():
                        nid = net_info.get("NetworkID", "")
                        if nid:
                            attached_network_ids.add(nid)

                    for network_state in networks:
                        network = None
                        try:
                            network = client.networks.get(network_state.network_id)
                            network_name = network.name
                        except NotFound:
                            network_name = f"{lab_id}-{network_state.interface_name}"
                            try:
                                network = client.networks.get(network_name)
                            except NotFound:
                                continue

                        # Skip unlabeled legacy networks to avoid duplicate attachments
                        labels = (network.attrs.get("Labels") or {})
                        if not labels.get("archetype.lab_id"):
                            continue

                        # Check by both name AND network ID to catch dual-naming
                        if network_name in attached:
                            continue
                        if network.id in attached_network_ids:
                            continue

                        try:
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
        await self._ensure_shared_bridge()

        logger.info("State recovery complete (no lab state to recover)")

    async def _ensure_shared_bridge(self) -> None:
        """Ensure the shared arch-ovs bridge exists and is configured."""
        bridge_name = settings.ovs_bridge_name
        code, _, _ = await self._ovs_vsctl("br-exists", bridge_name)
        if code != 0:
            code, _, stderr = await self._ovs_vsctl("add-br", bridge_name)
            if code != 0:
                raise RuntimeError(f"Failed to create shared OVS bridge: {stderr}")
            await self._ovs_vsctl("set-fail-mode", bridge_name, "standalone")
            await self._run_cmd([
                "ovs-ofctl", "add-flow", bridge_name,
                "priority=1,actions=normal"
            ])
            await self._run_cmd(["ip", "link", "set", bridge_name, "up"])
            logger.info(f"Created shared OVS bridge: {bridge_name}")
        else:
            logger.info(f"Shared OVS bridge {bridge_name} exists")

    async def _migrate_per_lab_bridges(self) -> None:
        """Move ports from legacy per-lab bridges (ovs-*) to shared arch-ovs."""
        bridge_name = settings.ovs_bridge_name
        code, stdout, _ = await self._ovs_vsctl("list-br")
        if code != 0:
            return

        bridges = [b.strip() for b in stdout.strip().split("\n") if b.strip()]
        for old_bridge in bridges:
            if old_bridge == bridge_name:
                continue
            if not old_bridge.startswith(OVS_BRIDGE_PREFIX):
                continue

            # Move ports from old bridge to shared bridge
            code, ports_out, _ = await self._ovs_vsctl("list-ports", old_bridge)
            ports = [p.strip() for p in ports_out.strip().split("\n") if p.strip()] if code == 0 else []
            if not ports:
                # Remove empty legacy bridge
                await self._ovs_vsctl("--if-exists", "del-br", old_bridge)
                logger.info(f"Removed empty legacy bridge {old_bridge}")
                continue

            for port in ports:
                # Preserve VLAN tag if present
                vlan_tag = None
                code, tag_out, _ = await self._ovs_vsctl("get", "port", port, "tag")
                if code == 0:
                    tag_str = tag_out.strip().strip("[]")
                    if tag_str:
                        try:
                            vlan_tag = int(tag_str)
                        except ValueError:
                            vlan_tag = None

                await self._ovs_vsctl("--if-exists", "del-port", old_bridge, port)
                if vlan_tag is not None:
                    await self._ovs_vsctl("add-port", bridge_name, port, f"tag={vlan_tag}")
                else:
                    await self._ovs_vsctl("add-port", bridge_name, port)

            await self._ovs_vsctl("--if-exists", "del-br", old_bridge)
            logger.info(f"Migrated ports from {old_bridge} to {bridge_name}")

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
        return await asyncio.get_running_loop().run_in_executor(None, _sync_find)

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

            container_pids = await asyncio.get_running_loop().run_in_executor(
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
