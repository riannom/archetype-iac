"""Docker network management using the OVS plugin.

This module provides helper functions for creating and managing Docker networks
backed by the archetype-ovs plugin. It handles:
- Creating per-interface networks for a lab
- Attaching containers to multiple networks
- Cleaning up networks on lab destroy
- Lab-level network lifecycle (create, delete, prune, recover)

The key benefit is that interfaces are provisioned BEFORE container init runs,
solving the cEOS interface enumeration timing issue.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import docker
from docker.errors import APIError, NotFound

from agent.labels import (
    LABEL_LAB_ID,
    LABEL_NODE_INTERFACE_COUNT,
    LABEL_NODE_KIND,
)
from agent.network.docker_plugin import get_docker_ovs_plugin

logger = logging.getLogger(__name__)

# Plugin driver name
PLUGIN_DRIVER = "archetype-ovs"


class DockerNetworkManager:
    """Manages Docker networks backed by the archetype-ovs plugin."""

    def __init__(self, docker_client: docker.DockerClient):
        self.docker = docker_client
        self.plugin = get_docker_ovs_plugin()

    def _network_name(self, lab_id: str, interface_name: str) -> str:
        """Generate Docker network name for a lab interface."""
        return f"{lab_id}-{interface_name}"

    async def create_lab_networks(
        self,
        lab_id: str,
        interface_count: int = 64,
        interface_prefix: str = "eth",
        start_index: int = 1,
    ) -> list[str]:
        """Create Docker networks for a lab's interfaces.

        Creates one network per interface (eth1, eth2, ..., ethN).
        All networks connect to the shared OVS bridge (arch-ovs).

        Args:
            lab_id: Lab identifier
            interface_count: Number of interfaces to create networks for
            interface_prefix: Interface naming prefix (e.g., "eth", "Ethernet")
            start_index: Starting interface number

        Returns:
            List of created network names
        """
        def _sync_create():
            created_networks = []
            for i in range(interface_count):
                iface_num = start_index + i
                interface_name = f"{interface_prefix}{iface_num}"
                network_name = self._network_name(lab_id, interface_name)

                try:
                    try:
                        self.docker.networks.get(network_name)
                        logger.debug(f"Network {network_name} already exists")
                        created_networks.append(network_name)
                        continue
                    except NotFound:
                        pass

                    self.docker.networks.create(
                        name=network_name,
                        driver=PLUGIN_DRIVER,
                        options={
                            "lab_id": lab_id,
                            "interface_name": interface_name,
                        },
                    )
                    created_networks.append(network_name)
                    logger.debug(f"Created network {network_name} for {interface_name}")

                except APIError as e:
                    logger.error(f"Failed to create network {network_name}: {e}")

            logger.info(f"Created {len(created_networks)} networks for lab {lab_id}")
            return created_networks

        return await asyncio.to_thread(_sync_create)

    async def delete_lab_networks(self, lab_id: str) -> int:
        """Delete all Docker networks for a lab.

        Args:
            lab_id: Lab identifier

        Returns:
            Number of networks deleted
        """
        def _sync_delete():
            deleted_count = 0

            try:
                networks = self.docker.networks.list(
                    filters={"label": f"com.docker.network.driver.name={PLUGIN_DRIVER}"}
                )

                for network in networks:
                    if network.name.startswith(f"{lab_id}-"):
                        try:
                            network.remove()
                            deleted_count += 1
                            logger.debug(f"Deleted network {network.name}")
                        except APIError as e:
                            logger.warning(f"Failed to delete network {network.name}: {e}")

            except APIError as e:
                logger.error(f"Error listing networks: {e}")

            # Also try to delete by name pattern (backup approach)
            for i in range(1, 65):  # eth1 through eth64
                network_name = self._network_name(lab_id, f"eth{i}")
                try:
                    network = self.docker.networks.get(network_name)
                    network.remove()
                    deleted_count += 1
                except NotFound:
                    continue
                except APIError as e:
                    logger.warning(f"Failed to delete network {network_name}: {e}")

            logger.info(f"Deleted {deleted_count} networks for lab {lab_id}")
            return deleted_count

        return await asyncio.to_thread(_sync_delete)

    async def attach_container_to_networks(
        self,
        container_name: str,
        lab_id: str,
        interface_count: int,
        interface_prefix: str = "eth",
        start_index: int = 1,
    ) -> list[str]:
        """Attach a container to lab interface networks.

        Should be called AFTER container creation but BEFORE container start.
        Docker will provision interfaces when the container starts.

        Args:
            container_name: Docker container name
            lab_id: Lab identifier
            interface_count: Number of interfaces to attach
            interface_prefix: Interface naming prefix
            start_index: Starting interface number

        Returns:
            List of attached network names
        """
        def _sync_attach():
            attached = []
            for i in range(interface_count):
                iface_num = start_index + i
                interface_name = f"{interface_prefix}{iface_num}"
                network_name = self._network_name(lab_id, interface_name)

                try:
                    network = self.docker.networks.get(network_name)
                    network.connect(container_name)
                    attached.append(network_name)
                    logger.debug(f"Attached {container_name} to {network_name}")

                except NotFound:
                    logger.warning(f"Network {network_name} not found")
                except APIError as e:
                    if "already exists" in str(e).lower():
                        attached.append(network_name)
                    else:
                        logger.warning(f"Failed to attach {container_name} to {network_name}: {e}")

            logger.info(f"Attached {container_name} to {len(attached)} networks")
            return attached

        return await asyncio.to_thread(_sync_attach)

    async def detach_container_from_networks(
        self,
        container_name: str,
        lab_id: str,
    ) -> int:
        """Detach a container from all lab networks.

        Args:
            container_name: Docker container name
            lab_id: Lab identifier

        Returns:
            Number of networks detached
        """
        def _sync_detach():
            detached_count = 0

            try:
                container = self.docker.containers.get(container_name)
                for network_name in list(container.attrs.get("NetworkSettings", {}).get("Networks", {}).keys()):
                    if network_name.startswith(f"{lab_id}-"):
                        try:
                            network = self.docker.networks.get(network_name)
                            network.disconnect(container_name)
                            detached_count += 1
                        except (NotFound, APIError) as e:
                            logger.warning(f"Failed to detach from {network_name}: {e}")

            except NotFound:
                logger.warning(f"Container {container_name} not found")
            except APIError as e:
                logger.error(f"Error detaching container: {e}")

            return detached_count

        return await asyncio.to_thread(_sync_detach)


# Singleton instance
_network_manager: DockerNetworkManager | None = None


def get_docker_network_manager(docker_client: docker.DockerClient) -> DockerNetworkManager:
    """Get or create the Docker network manager singleton."""
    global _network_manager
    if _network_manager is None:
        _network_manager = DockerNetworkManager(docker_client)
    return _network_manager


# ---------------------------------------------------------------------------
# Standalone network-lifecycle functions extracted from DockerProvider
# ---------------------------------------------------------------------------
# These functions accept the provider instance to access its Docker client,
# retry helper, and naming utilities.

async def create_lab_networks(provider: Any, lab_id: str, max_interfaces: int = 8) -> dict[str, str]:
    """Create Docker networks for lab interfaces via OVS plugin.

    Creates one network per interface (eth0, eth1, ..., ethN).
    All networks share the same OVS bridge (arch-ovs).

    Args:
        provider: DockerProvider instance
        lab_id: Lab identifier
        max_interfaces: Maximum number of data interfaces to create

    Returns:
        Dict mapping interface name (e.g., "eth0") to network name
    """
    async with provider._get_lab_network_lock(lab_id):
        await prune_legacy_lab_networks(provider, lab_id)

        networks: dict[str, str] = {}
        errors: list[str] = []
        lab_prefix = provider._lab_network_prefix(lab_id)

        for i in range(0, max_interfaces + 1):
            interface_name = f"eth{i}"
            network_name = f"{lab_prefix}-{interface_name}"

            try:
                try:
                    existing = await provider._retry_docker_call(
                        f"inspect network {network_name}",
                        provider.docker.networks.get,
                        network_name,
                    )
                    if provider._network_matches_lab_spec(existing, lab_id, interface_name):
                        logger.debug(f"Network {network_name} already exists with expected config")
                        networks[interface_name] = network_name
                        continue

                    await provider._resolve_conflicting_lab_network(
                        network_name,
                        lab_id,
                        interface_name,
                    )
                    networks[interface_name] = network_name
                    continue
                except Exception as _inspect_err:
                    if not isinstance(_inspect_err, NotFound):
                        raise

                try:
                    await provider._retry_docker_call(
                        f"create network {network_name}",
                        provider.docker.networks.create,
                        **provider._lab_network_create_kwargs(network_name, lab_id, interface_name),
                    )
                except Exception as create_err:
                    if not isinstance(create_err, APIError):
                        raise
                    if create_err.status_code == 409:
                        action = await provider._resolve_conflicting_lab_network(
                            network_name,
                            lab_id,
                            interface_name,
                        )
                        logger.warning(
                            f"Resolved network conflict for {network_name} via {action}"
                        )
                    else:
                        raise

                networks[interface_name] = network_name
                logger.debug(f"Ensured network {network_name}")

            except Exception as e:
                msg = f"Failed to ensure network {network_name}: {e}"
                errors.append(msg)
                logger.error(msg)

        expected = max_interfaces + 1
        if expected > 0 and len(networks) < expected:
            missing = expected - len(networks)
            err_detail = "; ".join(errors) if errors else "unknown error"
            raise RuntimeError(
                f"Failed to create {missing}/{expected} Docker networks for lab {lab_id}: {err_detail}"
            )

        logger.info(f"Created {len(networks)} Docker networks for lab {lab_id}")
        return networks


async def delete_lab_networks(provider: Any, lab_id: str) -> int:
    """Delete all Docker networks for a lab.

    Args:
        provider: DockerProvider instance
        lab_id: Lab identifier

    Returns:
        Number of networks deleted
    """
    deleted = 0

    try:
        await prune_legacy_lab_networks(provider, lab_id)
        lab_networks = await provider._retry_docker_call(
            f"list networks for {lab_id} by label",
            provider.docker.networks.list,
            filters={"label": f"{LABEL_LAB_ID}={lab_id}"},
        )

        if not lab_networks:
            all_networks = await provider._retry_docker_call(
                f"list all networks for {lab_id} fallback",
                provider.docker.networks.list,
            )
            safe_prefix = provider._lab_network_prefix(lab_id)
            lab_prefixes = (f"{safe_prefix}-", f"{lab_id}-")
            lab_networks = [
                n for n in all_networks
                if any(n.name.startswith(p) for p in lab_prefixes)
            ]

        for network in lab_networks:
            try:
                await provider._retry_docker_call(
                    f"remove network {network.name}",
                    network.remove,
                )
                deleted += 1
                logger.debug(f"Deleted network {network.name}")
            except APIError as e:
                logger.warning(f"Failed to delete network {network.name}: {e}")
            except Exception as e:
                logger.warning(f"Failed to delete network {network.name}: {e}")

    except (APIError, Exception) as e:
        logger.warning(f"Failed to list networks for lab {lab_id}: {e}")

    if deleted > 0:
        logger.info(f"Deleted {deleted} Docker networks for lab {lab_id}")
    return deleted


async def recover_stale_networks(
    provider: Any,
    container: Any,
    lab_id: str,
) -> bool:
    """Recover from stale network references by reconnecting to current lab networks.

    Returns True if recovery was attempted, False if no recovery was needed.
    """
    from agent.vendors import get_config_by_device

    container_name = container.name

    await asyncio.to_thread(container.reload)
    networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})

    if not networks:
        return False

    safe_prefix = provider._lab_network_prefix(lab_id)
    lab_prefix = f"{safe_prefix}-"
    current_lab_networks: dict[str, Any] = {}
    try:
        labeled = await asyncio.to_thread(
            provider.docker.networks.list,
            filters={"label": f"{LABEL_LAB_ID}={lab_id}"},
        )
        for net in labeled:
            current_lab_networks[net.name] = net

        if not current_lab_networks:
            all_networks = await asyncio.to_thread(provider.docker.networks.list)
            legacy_prefixes = (lab_prefix, f"{lab_id}-")
            for net in all_networks:
                if any(net.name.startswith(p) for p in legacy_prefixes):
                    current_lab_networks[net.name] = net
    except Exception as e:
        logger.warning(f"Failed to list networks: {e}")
        return False

    networks_disconnected: list[str] = []
    for net_name in list(networks.keys()):
        if net_name in ("bridge", "host", "none"):
            continue
        if net_name.startswith(lab_prefix) or net_name.startswith(lab_id):
            try:
                await asyncio.to_thread(provider.docker.networks.get, net_name)
            except NotFound:
                try:
                    logger.debug(f"Network {net_name} not found, will be cleaned up on start")
                except Exception:
                    pass
                networks_disconnected.append(net_name)

    for net_name in list(networks.keys()):
        if net_name in ("bridge", "host", "none"):
            continue
        if net_name.startswith(lab_prefix) or net_name.startswith(lab_id):
            try:
                net = await asyncio.to_thread(provider.docker.networks.get, net_name)
                await asyncio.to_thread(net.disconnect, container_name, force=True)
                logger.debug(f"Disconnected {container_name} from {net_name}")
                if net_name not in networks_disconnected:
                    networks_disconnected.append(net_name)
            except NotFound:
                pass
            except Exception as e:
                logger.debug(f"Could not disconnect from {net_name}: {e}")

    if not networks_disconnected:
        return False

    logger.info(
        f"Disconnected {container_name} from {len(networks_disconnected)} stale networks"
    )

    desired_count: int | None = None
    try:
        labels = container.labels or {}
        raw = labels.get(LABEL_NODE_INTERFACE_COUNT)
        if raw:
            desired_count = int(raw)
    except Exception:
        desired_count = None

    kind = (container.labels or {}).get(LABEL_NODE_KIND)
    if kind and desired_count is not None:
        vc = get_config_by_device(kind)
        if vc and vc.management_interface:
            desired_count = 1 + vc.reserved_nics + desired_count

    def _iface_index(name: str) -> int:
        match = re.search(r"(\d+)$", name)
        return int(match.group(1)) if match else 0

    sorted_networks = sorted(
        current_lab_networks.items(),
        key=lambda kv: _iface_index(kv[0]),
    )
    if desired_count is None:
        desired_count = 1
        logger.warning(
            f"{container_name} missing interface_count label; "
            "reconnecting only eth1 for safety"
        )
    sorted_networks = sorted_networks[:desired_count]

    reconnected = 0
    for net_name, net in sorted_networks:
        try:
            await asyncio.to_thread(net.connect, container_name)
            reconnected += 1
            logger.debug(f"Reconnected {container_name} to {net_name}")
        except APIError as e:
            if "already exists" in str(e).lower():
                reconnected += 1
            else:
                logger.warning(f"Failed to reconnect to {net_name}: {e}")

    logger.info(f"Reconnected {container_name} to {reconnected} lab networks")
    return True


async def prune_legacy_lab_networks(provider: Any, lab_id: str) -> int:
    """Remove legacy lab networks that don't match current naming/labels.

    Disconnects any attached containers before removing the network.
    """
    removed = 0
    current_prefix, legacy_prefix = provider._legacy_lab_network_prefixes(lab_id)
    current_prefix = f"{current_prefix}-"
    legacy_prefix = f"{legacy_prefix}-"

    try:
        all_networks = await asyncio.to_thread(provider.docker.networks.list)
        for net in all_networks:
            name = net.name or ""
            if name.startswith(current_prefix):
                continue
            if not name.startswith(legacy_prefix):
                continue

            containers = net.attrs.get("Containers") or {}
            for cid in containers:
                try:
                    await asyncio.to_thread(net.disconnect, cid, force=True)
                    logger.debug(f"Disconnected {cid[:12]} from legacy network {name}")
                except Exception:
                    pass

            try:
                await asyncio.to_thread(net.remove)
                removed += 1
                logger.info(f"Removed legacy lab network {name} for lab {lab_id}")
            except Exception as e:
                logger.warning(f"Failed to remove legacy network {name}: {e}")
    except Exception as e:
        logger.warning(f"Legacy network prune failed for lab {lab_id}: {e}")

    return removed
