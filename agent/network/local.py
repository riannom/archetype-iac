"""Local networking for intra-host container connectivity.

This module provides local link management for containers on the same host,
complementing the overlay.py module which handles cross-host VXLAN tunnels.

Features:
- Docker bridge network creation for management traffic
- veth pair creation for direct container-to-container links
- Interface namespace movement and configuration
- IP address assignment within container namespaces
- Lab-scoped cleanup

Architecture:
    Each link between containers on the same host uses a veth pair:

    Container A          Container B
    ┌──────────┐        ┌──────────┐
    │   eth1   │        │   eth1   │
    └────┬─────┘        └────┬─────┘
         │    veth pair      │
         └───────────────────┘

    The veth pair is created in the root namespace, then each end is moved
    into the respective container's network namespace and renamed.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from dataclasses import dataclass, field
from typing import Any

import docker
from docker.errors import NotFound, APIError

from agent.config import settings


logger = logging.getLogger(__name__)


# Management network prefix
MGMT_NETWORK_PREFIX = "archetype-mgmt"

# Interface name prefix for veth pairs
VETH_PREFIX = "arch"


@dataclass
class LocalLink:
    """Represents a local veth pair link between containers."""

    lab_id: str
    link_id: str  # Unique identifier for this link
    container_a: str  # Container name
    container_b: str
    iface_a: str  # Interface name inside container A
    iface_b: str
    veth_host_a: str  # Host-side veth name (for cleanup)
    veth_host_b: str

    @property
    def key(self) -> str:
        """Unique key for this link."""
        return f"{self.lab_id}:{self.link_id}"


@dataclass
class ManagedNetwork:
    """Represents a Docker management network for a lab."""

    lab_id: str
    network_id: str
    network_name: str


class LocalNetworkManager:
    """Manages local (intra-host) networking for labs.

    This class handles:
    - Management network creation (Docker bridge for OOB access)
    - veth pair creation for direct container links
    - Interface namespace manipulation
    - Lab-scoped cleanup

    Usage:
        manager = LocalNetworkManager()

        # Create management network for a lab
        network = await manager.create_management_network(lab_id)

        # Create link between two containers
        link = await manager.create_link(
            lab_id="lab123",
            link_id="r1:eth1-r2:eth1",
            container_a="archetype-lab123-r1",
            container_b="archetype-lab123-r2",
            iface_a="eth1",
            iface_b="eth1",
        )

        # Clean up all networking for lab
        await manager.cleanup_lab("lab123")
    """

    _instance: LocalNetworkManager | None = None

    def __new__(cls) -> LocalNetworkManager:
        """Singleton pattern for global network management."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self) -> None:
        """Initialize manager state."""
        self._docker: docker.DockerClient | None = None
        self._links: dict[str, LocalLink] = {}  # key -> link
        self._networks: dict[str, ManagedNetwork] = {}  # lab_id -> network

    @property
    def docker(self) -> docker.DockerClient:
        """Lazy-initialize Docker client."""
        if self._docker is None:
            self._docker = docker.from_env()
        return self._docker

    async def _run_cmd(self, cmd: list[str]) -> tuple[int, str, str]:
        """Run a shell command asynchronously.

        Args:
            cmd: Command and arguments as list

        Returns:
            Tuple of (return_code, stdout, stderr)
        """
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

    async def _get_container_pid(self, container_name: str) -> int | None:
        """Get the PID of a container's init process.

        Args:
            container_name: Docker container name

        Returns:
            PID if container is running, None otherwise
        """
        def _sync_get_pid() -> int | None:
            try:
                container = self.docker.containers.get(container_name)
                if container.status != "running":
                    logger.warning(f"Container {container_name} is not running")
                    return None
                pid = container.attrs["State"]["Pid"]
                if not pid:
                    logger.warning(f"Could not get PID for container {container_name}")
                    return None
                return pid
            except NotFound:
                logger.warning(f"Container {container_name} not found")
                return None
            except Exception as e:
                logger.error(f"Error getting container PID: {e}")
                return None

        return await asyncio.to_thread(_sync_get_pid)

    def _generate_veth_name(self, lab_id: str) -> str:
        """Generate a unique veth interface name.

        Names are limited to 15 characters (Linux interface name limit).
        Format: arch{random_hex} (e.g., "arch3f8a2b")
        """
        suffix = secrets.token_hex(4)  # 8 hex chars
        # arch = 4 chars, suffix = 8 chars, total = 12 chars (within 15 limit)
        return f"{VETH_PREFIX}{suffix}"

    async def _check_subnet_conflict(self, requested_subnet: str) -> str | None:
        """Check if a subnet conflicts with existing Docker networks.

        Args:
            requested_subnet: Subnet in CIDR format (e.g., "172.20.0.0/24")

        Returns:
            Name of conflicting network, or None if no conflict
        """
        def _sync_check() -> str | None:
            try:
                import ipaddress
                requested_net = ipaddress.ip_network(requested_subnet, strict=False)

                # Get all existing Docker networks
                networks = self.docker.networks.list()

                for network in networks:
                    # Get IPAM config for this network
                    ipam = network.attrs.get("IPAM", {})
                    configs = ipam.get("Config", [])

                    for config in configs:
                        existing_subnet = config.get("Subnet")
                        if not existing_subnet:
                            continue

                        existing_net = ipaddress.ip_network(existing_subnet, strict=False)

                        # Check for overlap
                        if requested_net.overlaps(existing_net):
                            return network.name

            except Exception as e:
                logger.warning(f"Error checking subnet conflict: {e}")

            return None

        return await asyncio.to_thread(_sync_check)

    async def create_management_network(
        self,
        lab_id: str,
        subnet: str | None = None,
    ) -> ManagedNetwork:
        """Create a Docker bridge network for management traffic.

        This network provides out-of-band management access to containers,
        separate from the data plane links.

        Args:
            lab_id: Lab identifier
            subnet: Optional CIDR subnet (e.g., "172.20.0.0/24")

        Returns:
            ManagedNetwork object

        Raises:
            RuntimeError: If network creation fails or subnet conflicts
        """
        # Check if network already exists
        if lab_id in self._networks:
            logger.debug(f"Management network already exists for lab {lab_id}")
            return self._networks[lab_id]

        network_name = f"{MGMT_NETWORK_PREFIX}-{lab_id[:20]}"

        # Wrap Docker network operations to avoid blocking
        def _sync_get_or_create_network():
            # Check if Docker network already exists
            existing_networks = self.docker.networks.list(names=[network_name])
            if existing_networks:
                return existing_networks[0], True  # network, is_existing
            return None, False

        try:
            existing_network, is_existing = await asyncio.to_thread(_sync_get_or_create_network)

            if existing_network:
                logger.info(f"Using existing management network: {network_name}")
                managed = ManagedNetwork(
                    lab_id=lab_id,
                    network_id=existing_network.id,
                    network_name=network_name,
                )
                self._networks[lab_id] = managed
                return managed

            # Check for subnet conflicts before creating
            if subnet:
                conflicting_network = await self._check_subnet_conflict(subnet)
                if conflicting_network:
                    raise RuntimeError(
                        f"Subnet {subnet} conflicts with existing network '{conflicting_network}'. "
                        f"Choose a different subnet or delete the conflicting network."
                    )

            # Create network in thread to avoid blocking
            def _sync_create_network():
                # Create IPAM config if subnet specified
                ipam_config = None
                if subnet:
                    ipam_pool = docker.types.IPAMPool(subnet=subnet)
                    ipam_config = docker.types.IPAMConfig(pool_configs=[ipam_pool])

                # Create Docker bridge network
                return self.docker.networks.create(
                    name=network_name,
                    driver="bridge",
                    ipam=ipam_config,
                    labels={
                        "archetype.lab_id": lab_id,
                        "archetype.type": "management",
                    },
                )

            network = await asyncio.to_thread(_sync_create_network)

            managed = ManagedNetwork(
                lab_id=lab_id,
                network_id=network.id,
                network_name=network_name,
            )
            self._networks[lab_id] = managed

            logger.info(f"Created management network: {network_name} ({network.short_id})")
            return managed

        except APIError as e:
            raise RuntimeError(f"Failed to create management network: {e}")

    async def delete_management_network(self, lab_id: str) -> bool:
        """Delete a lab's management network.

        Args:
            lab_id: Lab identifier

        Returns:
            True if deleted successfully
        """
        if lab_id not in self._networks:
            # Try to find and delete by name
            network_name = f"{MGMT_NETWORK_PREFIX}-{lab_id[:20]}"
            try:
                networks = self.docker.networks.list(names=[network_name])
                for network in networks:
                    try:
                        network.remove()
                        logger.info(f"Deleted management network: {network_name}")
                    except Exception as e:
                        logger.warning(f"Failed to delete network {network_name}: {e}")
                return True
            except Exception as e:
                logger.warning(f"Error finding network {network_name}: {e}")
                return False

        managed = self._networks[lab_id]
        try:
            network = self.docker.networks.get(managed.network_id)
            network.remove()
            del self._networks[lab_id]
            logger.info(f"Deleted management network: {managed.network_name}")
            return True
        except NotFound:
            del self._networks[lab_id]
            return True
        except Exception as e:
            logger.error(f"Failed to delete management network: {e}")
            return False

    async def create_link(
        self,
        lab_id: str,
        link_id: str,
        container_a: str,
        container_b: str,
        iface_a: str,
        iface_b: str,
        ip_a: str | None = None,
        ip_b: str | None = None,
    ) -> LocalLink:
        """Create a veth pair connecting two containers.

        This creates a direct Layer 2 link between containers, suitable for
        network lab scenarios requiring L2 connectivity (MAC addresses, STP, LLDP).

        Args:
            lab_id: Lab identifier
            link_id: Unique link identifier (e.g., "r1:eth1-r2:eth1")
            container_a: Name of first container
            container_b: Name of second container
            iface_a: Interface name inside container A (e.g., "eth1")
            iface_b: Interface name inside container B (e.g., "eth1")
            ip_a: Optional IP address for interface A (CIDR format, e.g., "10.0.0.1/24")
            ip_b: Optional IP address for interface B (CIDR format)

        Returns:
            LocalLink object representing the created link

        Raises:
            RuntimeError: If link creation fails
        """
        key = f"{lab_id}:{link_id}"

        # Check if link already exists
        if key in self._links:
            logger.info(f"Link already exists: {key}")
            return self._links[key]

        # Get container PIDs
        pid_a = await self._get_container_pid(container_a)
        pid_b = await self._get_container_pid(container_b)

        if pid_a is None:
            raise RuntimeError(f"Container {container_a} is not running or not found")
        if pid_b is None:
            raise RuntimeError(f"Container {container_b} is not running or not found")

        # Generate unique veth names
        veth_a = self._generate_veth_name(lab_id)
        veth_b = self._generate_veth_name(lab_id)

        # Ensure veth names don't already exist
        if await self._ip_link_exists(veth_a):
            await self._run_cmd(["ip", "link", "delete", veth_a])
        if await self._ip_link_exists(veth_b):
            await self._run_cmd(["ip", "link", "delete", veth_b])

        try:
            # Create veth pair
            # ip link add veth_a type veth peer name veth_b
            code, _, stderr = await self._run_cmd([
                "ip", "link", "add", veth_a, "type", "veth", "peer", "name", veth_b
            ])
            if code != 0:
                raise RuntimeError(f"Failed to create veth pair: {stderr}")

            # Move veth_a to container A's namespace
            # ip link set veth_a netns {pid_a}
            code, _, stderr = await self._run_cmd([
                "ip", "link", "set", veth_a, "netns", str(pid_a)
            ])
            if code != 0:
                await self._run_cmd(["ip", "link", "delete", veth_b])
                raise RuntimeError(f"Failed to move {veth_a} to container namespace: {stderr}")

            # Move veth_b to container B's namespace
            code, _, stderr = await self._run_cmd([
                "ip", "link", "set", veth_b, "netns", str(pid_b)
            ])
            if code != 0:
                # veth_a is already in container, try to clean up
                await self._run_cmd([
                    "nsenter", "-t", str(pid_a), "-n",
                    "ip", "link", "delete", veth_a
                ])
                raise RuntimeError(f"Failed to move {veth_b} to container namespace: {stderr}")

            # Delete any existing interface with target name in container A
            # (e.g., dummy interfaces created by provision_dummy_interfaces)
            await self._run_cmd([
                "nsenter", "-t", str(pid_a), "-n",
                "ip", "link", "delete", iface_a
            ])

            # Rename interface in container A
            # nsenter -t {pid_a} -n ip link set {veth_a} name {iface_a}
            code, _, stderr = await self._run_cmd([
                "nsenter", "-t", str(pid_a), "-n",
                "ip", "link", "set", veth_a, "name", iface_a
            ])
            if code != 0:
                logger.warning(f"Failed to rename {veth_a} to {iface_a}: {stderr}")
                # Continue anyway - interface might already exist with target name

            # Delete any existing interface with target name in container B
            await self._run_cmd([
                "nsenter", "-t", str(pid_b), "-n",
                "ip", "link", "delete", iface_b
            ])

            # Rename interface in container B
            code, _, stderr = await self._run_cmd([
                "nsenter", "-t", str(pid_b), "-n",
                "ip", "link", "set", veth_b, "name", iface_b
            ])
            if code != 0:
                logger.warning(f"Failed to rename {veth_b} to {iface_b}: {stderr}")

            # Bring up interface in container A
            code, _, stderr = await self._run_cmd([
                "nsenter", "-t", str(pid_a), "-n",
                "ip", "link", "set", iface_a, "up"
            ])
            if code != 0:
                logger.warning(f"Failed to bring up {iface_a}: {stderr}")

            # Bring up interface in container B
            code, _, stderr = await self._run_cmd([
                "nsenter", "-t", str(pid_b), "-n",
                "ip", "link", "set", iface_b, "up"
            ])
            if code != 0:
                logger.warning(f"Failed to bring up {iface_b}: {stderr}")

            # Configure IP addresses if provided
            if ip_a:
                code, _, stderr = await self._run_cmd([
                    "nsenter", "-t", str(pid_a), "-n",
                    "ip", "addr", "add", ip_a, "dev", iface_a
                ])
                if code != 0:
                    logger.warning(f"Failed to configure IP {ip_a} on {iface_a}: {stderr}")
                else:
                    logger.debug(f"Configured IP {ip_a} on {container_a}:{iface_a}")

            if ip_b:
                code, _, stderr = await self._run_cmd([
                    "nsenter", "-t", str(pid_b), "-n",
                    "ip", "addr", "add", ip_b, "dev", iface_b
                ])
                if code != 0:
                    logger.warning(f"Failed to configure IP {ip_b} on {iface_b}: {stderr}")
                else:
                    logger.debug(f"Configured IP {ip_b} on {container_b}:{iface_b}")

            # Create and track link
            link = LocalLink(
                lab_id=lab_id,
                link_id=link_id,
                container_a=container_a,
                container_b=container_b,
                iface_a=iface_a,
                iface_b=iface_b,
                veth_host_a=veth_a,  # These are now inside container namespaces
                veth_host_b=veth_b,
            )
            self._links[key] = link

            logger.info(f"Created link: {container_a}:{iface_a} <-> {container_b}:{iface_b}")
            return link

        except Exception as e:
            # Attempt cleanup
            try:
                await self._run_cmd(["ip", "link", "delete", veth_a])
            except Exception:
                pass
            try:
                await self._run_cmd(["ip", "link", "delete", veth_b])
            except Exception:
                pass
            raise RuntimeError(f"Failed to create link: {e}")

    async def delete_link(self, link: LocalLink) -> bool:
        """Delete a local link.

        Deleting one end of a veth pair automatically deletes the other end.

        Args:
            link: The link to delete

        Returns:
            True if deleted successfully
        """
        try:
            # Get PID of one container (either will work)
            pid = await self._get_container_pid(link.container_a)
            if pid:
                # Delete interface from within container namespace
                # Deleting one end of veth pair deletes both
                await self._run_cmd([
                    "nsenter", "-t", str(pid), "-n",
                    "ip", "link", "delete", link.iface_a
                ])
            else:
                # Container might be stopped, try other container
                pid = await self._get_container_pid(link.container_b)
                if pid:
                    await self._run_cmd([
                        "nsenter", "-t", str(pid), "-n",
                        "ip", "link", "delete", link.iface_b
                    ])

            # Remove from tracking
            if link.key in self._links:
                del self._links[link.key]

            logger.info(f"Deleted link: {link.link_id}")
            return True

        except Exception as e:
            logger.warning(f"Error deleting link {link.link_id}: {e}")
            return False

    async def attach_to_bridge(
        self,
        container_name: str,
        interface_name: str,
        bridge_name: str,
        ip_address: str | None = None,
    ) -> bool:
        """Attach a container interface to an existing Linux bridge.

        This is useful for connecting containers to external networks or
        overlay bridges (for cross-host connectivity).

        Args:
            container_name: Docker container name
            interface_name: Interface name inside container
            bridge_name: Linux bridge to attach to
            ip_address: Optional IP address (CIDR format)

        Returns:
            True if attached successfully
        """
        pid = await self._get_container_pid(container_name)
        if pid is None:
            logger.error(f"Container {container_name} not running")
            return False

        try:
            # Generate veth name
            veth_host = self._generate_veth_name("")
            veth_cont = self._generate_veth_name("")

            # Create veth pair
            code, _, stderr = await self._run_cmd([
                "ip", "link", "add", veth_host, "type", "veth", "peer", "name", veth_cont
            ])
            if code != 0:
                raise RuntimeError(f"Failed to create veth pair: {stderr}")

            # Attach host end to bridge
            code, _, stderr = await self._run_cmd([
                "ip", "link", "set", veth_host, "master", bridge_name
            ])
            if code != 0:
                await self._run_cmd(["ip", "link", "delete", veth_host])
                raise RuntimeError(f"Failed to attach to bridge: {stderr}")

            # Bring up host end
            await self._run_cmd(["ip", "link", "set", veth_host, "up"])

            # Move container end to namespace
            code, _, stderr = await self._run_cmd([
                "ip", "link", "set", veth_cont, "netns", str(pid)
            ])
            if code != 0:
                await self._run_cmd(["ip", "link", "delete", veth_host])
                raise RuntimeError(f"Failed to move veth to namespace: {stderr}")

            # Rename and bring up in container
            await self._run_cmd([
                "nsenter", "-t", str(pid), "-n",
                "ip", "link", "set", veth_cont, "name", interface_name
            ])
            await self._run_cmd([
                "nsenter", "-t", str(pid), "-n",
                "ip", "link", "set", interface_name, "up"
            ])

            # Configure IP if provided
            if ip_address:
                await self._run_cmd([
                    "nsenter", "-t", str(pid), "-n",
                    "ip", "addr", "add", ip_address, "dev", interface_name
                ])

            logger.info(f"Attached {container_name}:{interface_name} to bridge {bridge_name}")
            return True

        except Exception as e:
            logger.error(f"Failed to attach to bridge: {e}")
            return False

    async def cleanup_lab(self, lab_id: str) -> dict[str, Any]:
        """Clean up all local networking for a lab.

        Args:
            lab_id: Lab identifier

        Returns:
            Summary of cleanup actions
        """
        result = {
            "links_deleted": 0,
            "networks_deleted": 0,
            "errors": [],
        }

        # Delete all links for this lab
        links_to_delete = [l for l in self._links.values() if l.lab_id == lab_id]
        for link in links_to_delete:
            try:
                if await self.delete_link(link):
                    result["links_deleted"] += 1
            except Exception as e:
                result["errors"].append(f"Link {link.link_id}: {e}")

        # Delete management network
        try:
            if await self.delete_management_network(lab_id):
                result["networks_deleted"] += 1
        except Exception as e:
            result["errors"].append(f"Management network: {e}")

        logger.info(f"Lab {lab_id} local cleanup: {result}")
        return result

    def get_links_for_lab(self, lab_id: str) -> list[LocalLink]:
        """Get all local links for a lab."""
        return [l for l in self._links.values() if l.lab_id == lab_id]

    def get_network_for_lab(self, lab_id: str) -> ManagedNetwork | None:
        """Get management network for a lab."""
        return self._networks.get(lab_id)

    def get_status(self) -> dict[str, Any]:
        """Get status of all managed local networks for debugging."""
        return {
            "links": [
                {
                    "lab_id": l.lab_id,
                    "link_id": l.link_id,
                    "container_a": l.container_a,
                    "container_b": l.container_b,
                    "iface_a": l.iface_a,
                    "iface_b": l.iface_b,
                }
                for l in self._links.values()
            ],
            "networks": [
                {
                    "lab_id": n.lab_id,
                    "network_id": n.network_id,
                    "network_name": n.network_name,
                }
                for n in self._networks.values()
            ],
        }

    async def provision_dummy_interfaces(
        self,
        container_name: str,
        interface_prefix: str,
        start_index: int,
        count: int,
    ) -> int:
        """Create dummy interfaces inside a container.

        Some network devices (like cEOS) require interfaces to exist before
        the device boots, otherwise the control plane won't detect them.

        This creates dummy interfaces named {prefix}{index} inside the container.
        For cEOS with prefix="eth" and start_index=1, this creates eth1, eth2, etc.

        Args:
            container_name: Docker container name
            interface_prefix: Interface name prefix (e.g., "eth", "Ethernet")
            start_index: Starting interface number
            count: Number of interfaces to create

        Returns:
            Number of interfaces successfully created
        """
        pid = await self._get_container_pid(container_name)
        if pid is None:
            logger.error(f"Container {container_name} not running for interface provisioning")
            return 0

        created = 0
        for i in range(count):
            iface_num = start_index + i
            # Determine interface name based on prefix
            # For "Ethernet" -> "Ethernet1", "Ethernet2", etc. (cEOS style)
            # For "eth" -> "eth1", "eth2", etc. (Linux style)
            if interface_prefix.endswith("-"):
                # e.g., "e1-" -> "e1-1", "e1-2" (SR Linux style)
                iface_name = f"{interface_prefix}{iface_num}"
            else:
                iface_name = f"{interface_prefix}{iface_num}"

            # Check if interface already exists
            code, _, _ = await self._run_cmd([
                "nsenter", "-t", str(pid), "-n",
                "ip", "link", "show", iface_name
            ])
            if code == 0:
                # Interface exists, skip
                continue

            # Create dummy interface
            code, _, stderr = await self._run_cmd([
                "nsenter", "-t", str(pid), "-n",
                "ip", "link", "add", iface_name, "type", "dummy"
            ])
            if code != 0:
                logger.warning(f"Failed to create dummy interface {iface_name}: {stderr}")
                continue

            # Bring interface up
            await self._run_cmd([
                "nsenter", "-t", str(pid), "-n",
                "ip", "link", "set", iface_name, "up"
            ])

            created += 1

        if created > 0:
            logger.info(f"Created {created} dummy interfaces in {container_name}")

        return created


    async def create_link_ovs(
        self,
        lab_id: str,
        link_id: str,
        container_a: str,
        container_b: str,
        iface_a: str,
        iface_b: str,
    ) -> int:
        """Create a link using OVS hot-connect.

        This is an alternative to create_link() that uses OVS VLAN tag matching
        instead of veth pairs. It requires interfaces to already be provisioned
        via OVS (see OVSNetworkManager.provision_interface).

        Args:
            lab_id: Lab identifier
            link_id: Unique link identifier
            container_a: First container name
            container_b: Second container name
            iface_a: First interface name
            iface_b: Second interface name

        Returns:
            Shared VLAN tag for the link
        """
        from agent.network.ovs import get_ovs_manager

        ovs = get_ovs_manager()
        vlan = await ovs.hot_connect(
            container_a=container_a,
            iface_a=iface_a,
            container_b=container_b,
            iface_b=iface_b,
            lab_id=lab_id,
        )

        # Track link for cleanup
        key = f"{lab_id}:{link_id}"
        link = LocalLink(
            lab_id=lab_id,
            link_id=link_id,
            container_a=container_a,
            container_b=container_b,
            iface_a=iface_a,
            iface_b=iface_b,
            veth_host_a="",  # Not used with OVS
            veth_host_b="",
        )
        self._links[key] = link

        return vlan

    async def delete_link_ovs(
        self,
        lab_id: str,
        link_id: str,
    ) -> tuple[int, int] | None:
        """Delete a link using OVS hot-disconnect.

        This is an alternative to delete_link() that uses OVS VLAN tag separation.

        Args:
            lab_id: Lab identifier
            link_id: Link identifier

        Returns:
            Tuple of new VLAN tags (vlan_a, vlan_b) or None if not found
        """
        from agent.network.ovs import get_ovs_manager

        key = f"{lab_id}:{link_id}"
        link = self._links.get(key)

        if not link:
            logger.warning(f"Link not found for OVS disconnect: {key}")
            return None

        ovs = get_ovs_manager()
        vlans = await ovs.hot_disconnect(
            container_a=link.container_a,
            iface_a=link.iface_a,
            container_b=link.container_b,
            iface_b=link.iface_b,
        )

        # Remove from tracking
        del self._links[key]

        return vlans


# Module-level singleton accessor
_local_manager: LocalNetworkManager | None = None


def get_local_manager() -> LocalNetworkManager:
    """Get the global LocalNetworkManager instance."""
    global _local_manager
    if _local_manager is None:
        _local_manager = LocalNetworkManager()
    return _local_manager
