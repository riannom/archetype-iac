"""Docker network management using the OVS plugin.

This module provides helper functions for creating and managing Docker networks
backed by the archetype-ovs plugin. It handles:
- Creating per-interface networks for a lab
- Attaching containers to multiple networks
- Cleaning up networks on lab destroy

The key benefit is that interfaces are provisioned BEFORE container init runs,
solving the cEOS interface enumeration timing issue.
"""

from __future__ import annotations

import logging

import docker
from docker.errors import APIError, NotFound

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
        created_networks = []

        for i in range(interface_count):
            iface_num = start_index + i
            interface_name = f"{interface_prefix}{iface_num}"
            network_name = self._network_name(lab_id, interface_name)

            try:
                # Check if network already exists
                try:
                    self.docker.networks.get(network_name)
                    logger.debug(f"Network {network_name} already exists")
                    created_networks.append(network_name)
                    continue
                except NotFound:
                    pass

                # Create network via Docker API
                # The plugin will handle OVS bridge creation
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
                # Continue with other networks

        logger.info(f"Created {len(created_networks)} networks for lab {lab_id}")
        return created_networks

    async def delete_lab_networks(self, lab_id: str) -> int:
        """Delete all Docker networks for a lab.

        Args:
            lab_id: Lab identifier

        Returns:
            Number of networks deleted
        """
        deleted_count = 0

        try:
            # Find all networks with our naming pattern
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
                # May already be attached
                if "already exists" in str(e).lower():
                    attached.append(network_name)
                else:
                    logger.warning(f"Failed to attach {container_name} to {network_name}: {e}")

        logger.info(f"Attached {container_name} to {len(attached)} networks")
        return attached

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


# Singleton instance
_network_manager: DockerNetworkManager | None = None


def get_docker_network_manager(docker_client: docker.DockerClient) -> DockerNetworkManager:
    """Get or create the Docker network manager singleton."""
    global _network_manager
    if _network_manager is None:
        _network_manager = DockerNetworkManager(docker_client)
    return _network_manager
