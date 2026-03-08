"""VLAN management and carrier state mixin for DockerOVSPlugin.

Contains VLAN Management API (hot_connect, hot_disconnect, etc.)
and Carrier State Management (set_carrier_state, carrier monitor, etc.).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from agent.network.docker_plugin import (
    EndpointState,
    LINKED_VLAN_END,
    LINKED_VLAN_START,
    VLAN_RANGE_END,
    VLAN_RANGE_START,
)

logger = logging.getLogger(__name__)


class PluginVlanMixin:
    """VLAN management and carrier state mixin for DockerOVSPlugin.

    Provides VLAN management API (hot_connect, hot_disconnect,
    endpoint VLAN operations) and carrier state management
    (set_carrier_state, isolate_port, restore_port, etc.).
    """

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
            async def _binding_matches(host_veth: str) -> bool:
                """Verify host_veth is actually the peer of container:interface_name."""
                def _get_container_pid() -> int:
                    client = docker.from_env()
                    container = client.containers.get(container_name)
                    return int(container.attrs.get("State", {}).get("Pid", 0))

                try:
                    pid = await asyncio.to_thread(_get_container_pid)
                except Exception:
                    return False
                if pid <= 0:
                    return False
                mapped_if = await self._find_interface_in_container(pid, host_veth)
                return mapped_if == interface_name

            async def _resolve_host_veth_by_ifindex() -> str | None:
                """Resolve the OVS host veth via exact interface ifindex lookup."""
                def _get_peer_ifindex() -> int | None:
                    client = docker.from_env()
                    container = client.containers.get(container_name)
                    exit_code, output = container.exec_run(
                        ["cat", f"/sys/class/net/{interface_name}/iflink"],
                        demux=False,
                    )
                    if exit_code != 0:
                        return None
                    return int(output.decode().strip())

                try:
                    peer_ifindex = await asyncio.to_thread(_get_peer_ifindex)
                except Exception:
                    return None
                if peer_ifindex is None:
                    return None

                code, stdout, _ = await self._ovs_vsctl(
                    "--data=bare",
                    "--no-heading",
                    "--columns=name",
                    "find",
                    "Interface",
                    f"ifindex={peer_ifindex}",
                )
                if code != 0 or not stdout.strip():
                    return None

                for port_name in stdout.strip().splitlines():
                    port_name = port_name.strip()
                    if port_name:
                        return port_name
                return None

            # Get container's network memberships
            def _get_container_details():
                client = docker.from_env()
                container = client.containers.get(container_name)
                return (
                    container.attrs["NetworkSettings"]["Networks"],
                    (container.labels or {}).get("archetype.node_name"),
                )

            try:
                networks, node_name = await asyncio.to_thread(_get_container_details)
            except docker.errors.NotFound:
                pruned = await self._prune_stale_container_endpoints(lab_id, container_name)
                logger.warning(
                    f"Container not found during endpoint discovery: {container_name}:{interface_name}. "
                    f"Pruned {pruned} stale tracked endpoint(s)."
                )
                return None

            # Try each attached network to match EndpointID or NetworkID
            for net_name, net_info in networks.items():
                target_endpoint_id = net_info.get("EndpointID")
                target_network_id = net_info.get("NetworkID")

                # Best match: EndpointID from Docker
                if target_endpoint_id and target_endpoint_id in self.endpoints:
                    ep = self.endpoints[target_endpoint_id]
                    network = self.networks.get(ep.network_id)
                    if network and network.lab_id != lab_id:
                        continue
                    if ep.interface_name == interface_name:
                        if not await self._validate_endpoint_exists(ep):
                            logger.warning(
                                f"Discovered endpoint {ep.endpoint_id[:12]} is stale "
                                f"(OVS port {ep.host_veth} missing)"
                            )
                            continue  # Try other networks / strategies 3+4
                        if not await _binding_matches(ep.host_veth):
                            logger.warning(
                                f"Endpoint binding mismatch for {container_name}:{interface_name}: "
                                f"{ep.host_veth} is not the current interface peer"
                            )
                            continue
                        ep.container_name = container_name
                        ep.node_name = node_name or ep.node_name
                        logger.info(
                            f"Matched endpoint via EndpointID: {container_name}:{interface_name} -> {ep.host_veth}"
                        )
                        return ep

                # Next best: match by Docker NetworkID + interface name
                if target_network_id:
                    for ep in self.endpoints.values():
                        if ep.network_id == target_network_id and ep.interface_name == interface_name:
                            network = self.networks.get(ep.network_id)
                            if network and network.lab_id != lab_id:
                                continue
                            if not await self._validate_endpoint_exists(ep):
                                logger.warning(
                                    f"Discovered endpoint {ep.endpoint_id[:12]} is stale "
                                    f"(OVS port {ep.host_veth} missing)"
                                )
                                continue  # Try other networks / strategies 3+4
                            if not await _binding_matches(ep.host_veth):
                                logger.warning(
                                    f"Endpoint binding mismatch for {container_name}:{interface_name}: "
                                    f"{ep.host_veth} is not the current interface peer"
                                )
                                continue
                            ep.container_name = container_name
                            ep.node_name = node_name or ep.node_name
                            logger.info(
                                f"Matched endpoint via NetworkID: {container_name}:{interface_name} -> {ep.host_veth}"
                            )
                            return ep

            # Attempt to reconstruct endpoint state when tracking is missing.
            # This can happen after agent restarts where endpoints are not loaded,
            # but Docker networks and OVS ports still exist.
            exact_host_veth = await _resolve_host_veth_by_ifindex()

            for net_name, net_info in networks.items():
                target_endpoint_id = net_info.get("EndpointID")
                target_network_id = net_info.get("NetworkID")
                if not target_endpoint_id or not target_network_id:
                    continue

                network = self.networks.get(target_network_id)
                if not network or network.interface_name != interface_name or network.lab_id != lab_id:
                    continue

                host_veth = exact_host_veth
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
                    node_name=node_name,
                )
                if not await _binding_matches(host_veth):
                    logger.warning(
                        f"Reconstructed endpoint mismatch for {container_name}:{interface_name}: "
                        f"{host_veth} is not the current interface peer"
                    )
                    continue
                self.endpoints[target_endpoint_id] = endpoint
                await self._mark_dirty_and_save()
                logger.info(
                    f"Reconstructed endpoint: {container_name}:{interface_name} -> {host_veth}"
                )
                return endpoint

            # Strict fallback: only match untracked endpoints when network + binding
            # checks produce exactly one unambiguous candidate.
            attached_network_ids = {
                net_info.get("NetworkID")
                for net_info in networks.values()
                if net_info.get("NetworkID")
            }
            fallback_candidates: list[EndpointState] = []
            for ep in self.endpoints.values():
                if ep.interface_name != interface_name or ep.container_name:
                    continue
                if ep.network_id not in attached_network_ids:
                    continue
                network = self.networks.get(ep.network_id)
                if not network or network.lab_id != lab_id:
                    continue
                if not await self._validate_endpoint_exists(ep):
                    continue
                if not await _binding_matches(ep.host_veth):
                    continue
                fallback_candidates.append(ep)

            if len(fallback_candidates) == 1:
                ep = fallback_candidates[0]
                ep.container_name = container_name
                ep.node_name = node_name or ep.node_name
                logger.info(
                    f"Matched endpoint by strict fallback: "
                    f"{container_name}:{interface_name} -> {ep.host_veth}"
                )
                return ep

            if len(fallback_candidates) > 1:
                logger.warning(
                    f"Ambiguous endpoint fallback for {container_name}:{interface_name}; "
                    f"candidates={[ep.host_veth for ep in fallback_candidates]}"
                )

            logger.warning(f"Could not find endpoint for {container_name}:{interface_name}")
            return None

        except Exception as e:
            logger.error(f"Error discovering endpoint {container_name}:{interface_name}: {e}")
            return None

    async def _prune_stale_container_endpoints(self, lab_id: str, container_name: str) -> int:
        """Remove tracked endpoints bound to a container that no longer exists."""
        stale_ids: list[str] = []
        for endpoint_id, endpoint in self.endpoints.items():
            if endpoint.container_name != container_name:
                continue
            network = self.networks.get(endpoint.network_id)
            if network and network.lab_id != lab_id:
                continue
            stale_ids.append(endpoint_id)

        for endpoint_id in stale_ids:
            self.endpoints.pop(endpoint_id, None)

        if stale_ids:
            await self._mark_dirty_and_save()

        return len(stale_ids)

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
        async with self._locked():
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

            # Validate both endpoints actually exist on OVS
            if not await self._validate_endpoint_exists(ep_a):
                logger.error(
                    f"Cannot hot_connect: source endpoint {ep_a.host_veth} "
                    f"not on OVS for {container_a}:{iface_a}"
                )
                return None
            if not await self._validate_endpoint_exists(ep_b):
                logger.error(
                    f"Cannot hot_connect: target endpoint {ep_b.host_veth} "
                    f"not on OVS for {container_b}:{iface_b}"
                )
                return None

            net_a = self.networks.get(ep_a.network_id)
            net_b = self.networks.get(ep_b.network_id)
            if not net_a or not net_b or net_a.lab_id != lab_id or net_b.lab_id != lab_id:
                logger.error(
                    f"Endpoint lab mismatch for hot-connect in lab {lab_id}: "
                    f"{container_a}:{iface_a}={net_a.lab_id if net_a else 'unknown'}, "
                    f"{container_b}:{iface_b}={net_b.lab_id if net_b else 'unknown'}"
                )
                return None

            # Allocate from linked range (DB-managed, convergence-safe)
            shared_vlan = await self._allocate_linked_vlan(lab_bridge)

            # Set BOTH ports to the new linked-range tag
            code, _, stderr = await self._ovs_vsctl(
                "set", "port", ep_a.host_veth, f"tag={shared_vlan}"
            )
            if code != 0:
                logger.error(f"Failed to set VLAN on port A: {stderr}")
                self._release_linked_vlan(shared_vlan)
                return None

            code, _, stderr = await self._ovs_vsctl(
                "set", "port", ep_b.host_veth, f"tag={shared_vlan}"
            )
            if code != 0:
                logger.error(f"Failed to set VLAN on port B: {stderr}")
                self._release_linked_vlan(shared_vlan)
                return None

            # Release old isolated-range tags from both endpoints
            self._release_vlan(ep_a.vlan_tag)
            self._release_vlan(ep_b.vlan_tag)

            ep_a.vlan_tag = shared_vlan
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
        async with self._locked():
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
            new_vlan = await self._allocate_vlan(lab_bridge)

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
        async with self._locked():
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
        read_from_ovs: bool = False,
    ) -> int | None:
        """Get the current VLAN tag for an endpoint.

        Args:
            lab_id: Lab identifier
            container: Container name
            interface: Interface name
            read_from_ovs: If True, read directly from OVS instead of in-memory state.
                          Use this for verification to get ground truth.

        Returns:
            VLAN tag if found, None otherwise.
        """
        async with self._locked():
            for ep in self.endpoints.values():
                if ep.container_name == container and ep.interface_name == interface:
                    if read_from_ovs:
                        if ep.host_veth:
                            code, stdout, _ = await self._run_cmd([
                                "ovs-vsctl", "get", "port", ep.host_veth, "tag"
                            ])
                            if code == 0:
                                try:
                                    return int(stdout.strip())
                                except ValueError:
                                    pass
                        # OVS port missing or read failed — don't return stale value
                        logger.warning(
                            f"Endpoint {ep.endpoint_id[:12]} stale: "
                            f"OVS port {ep.host_veth} missing for {container}:{interface}"
                        )
                        return None
                    return ep.vlan_tag

        ep = await self._discover_endpoint(lab_id, container, interface)
        if ep:
            if read_from_ovs:
                if ep.host_veth:
                    code, stdout, _ = await self._run_cmd([
                        "ovs-vsctl", "get", "port", ep.host_veth, "tag"
                    ])
                    if code == 0:
                        try:
                            return int(stdout.strip())
                        except ValueError:
                            pass
                # OVS port missing or read failed — don't return stale value
                logger.warning(
                    f"Discovered endpoint stale: "
                    f"OVS port {ep.host_veth} missing for {container}:{interface}"
                )
                return None
            return ep.vlan_tag
        return None

    async def set_endpoint_vlan(
        self,
        lab_id: str,
        container: str,
        interface: str,
        vlan_tag: int,
    ) -> bool:
        """Update the VLAN tag for an endpoint in the in-memory state.

        This should be called after externally modifying the OVS port's VLAN tag
        to keep the in-memory state synchronized with OVS reality.

        Args:
            lab_id: Lab identifier
            container: Container name
            interface: Interface name
            vlan_tag: New VLAN tag to record

        Returns:
            True if endpoint was found and updated, False otherwise.
        """
        async with self._locked():
            for ep in self.endpoints.values():
                if ep.container_name == container and ep.interface_name == interface:
                    old_vlan = ep.vlan_tag
                    ep.vlan_tag = vlan_tag
                    self._touch_lab(lab_id)
                    await self._mark_dirty_and_save()
                    logger.debug(
                        f"Updated in-memory VLAN for {container}:{interface}: "
                        f"{old_vlan} -> {vlan_tag}"
                    )
                    return True

        # Try to discover the endpoint if not in tracking
        ep = await self._discover_endpoint(lab_id, container, interface)
        if ep:
            ep.vlan_tag = vlan_tag
            # Add to tracking if discovered
            async with self._locked():
                self.endpoints[ep.endpoint_id] = ep
                await self._mark_dirty_and_save()
            logger.debug(
                f"Discovered and updated VLAN for {container}:{interface}: {vlan_tag}"
            )
            return True

        logger.warning(
            f"Could not find endpoint to update VLAN: {container}:{interface}"
        )
        return False

    async def set_endpoint_vlan_by_host_veth(
        self,
        lab_id: str,
        host_veth: str,
        vlan_tag: int,
    ) -> bool:
        """Update tracked endpoint VLAN by host veth name.

        Use this when OVS VLAN tags are changed outside plugin paths and only
        the host-side port name is known.
        """
        async with self._locked():
            endpoint = next(
                (ep for ep in self.endpoints.values() if ep.host_veth == host_veth),
                None,
            )
            if endpoint is None:
                logger.debug(
                    "No tracked endpoint for host veth %s while syncing VLAN %s",
                    host_veth,
                    vlan_tag,
                )
                return False

            old_vlan = endpoint.vlan_tag
            if old_vlan == vlan_tag:
                return True

            self._release_vlan(old_vlan)
            self._release_linked_vlan(old_vlan)

            if VLAN_RANGE_START <= vlan_tag <= VLAN_RANGE_END:
                self._allocated_vlans.add(vlan_tag)
            elif LINKED_VLAN_START <= vlan_tag <= LINKED_VLAN_END:
                self._allocated_linked_vlans.add(vlan_tag)

            endpoint.vlan_tag = vlan_tag
            self._touch_lab(lab_id)
            await self._mark_dirty_and_save()

            logger.debug(
                "Updated endpoint VLAN by host veth %s: %s -> %s",
                host_veth,
                old_vlan,
                vlan_tag,
            )
            return True

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
            # We don't know the exact host_veth for each network without endpoint state,
            # but we can return the intended interface names for the caller to match
            # by checking which veth connects to the container
            # Return network_id -> interface_name for caller to resolve
            mapping[f"network:{network.network_id}"] = network.interface_name

        return mapping

    async def set_endpoint_container_name(self, endpoint_id: str, container_name: str) -> None:
        """Associate endpoint with container name for hot-connect lookups."""
        async with self._locked():
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
        async with self._locked():
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
