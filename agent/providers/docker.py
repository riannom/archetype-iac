"""Docker provider for native container management.

This provider manages containers directly using the Docker SDK. It provides:
- Container lifecycle management (create, start, stop, remove)
- Local networking via veth pairs (LocalNetworkManager)
- Integration with overlay networking for multi-host labs
- Vendor-specific container configuration from vendors.py
- Readiness detection for slow-boot devices

Architecture:
    DockerProvider creates containers with networking in "none" mode, then
    uses LocalNetworkManager to create veth pairs between containers. For
    cross-host links, OverlayManager creates VXLAN tunnels.

    ┌─────────────────┐      ┌─────────────────┐
    │  DockerProvider │      │ LocalNetworkMgr │
    │  (containers)   │──────│ (veth pairs)    │
    └─────────────────┘      └─────────────────┘
            │                        │
            └────────────────────────┘
                      │
            ┌─────────┴─────────┐
            │  OverlayManager   │
            │ (VXLAN for xhost) │
            └───────────────────┘
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import docker
import yaml
from docker.errors import NotFound, APIError, ImageNotFound
from docker.types import Mount, IPAMConfig

from agent.config import settings
from agent.network.local import LocalNetworkManager, get_local_manager
from agent.network.ovs import OVSNetworkManager, get_ovs_manager
from agent.network.docker_plugin import DockerOVSPlugin, get_docker_ovs_plugin
from agent.providers.base import (
    DeployResult,
    DestroyResult,
    NodeActionResult,
    NodeInfo,
    NodeStatus,
    Provider,
    StatusResult,
)
from agent.schemas import DeployLink, DeployNode, DeployTopology
from agent.vendors import (
    VendorConfig,
    get_config_by_device,
    get_container_config,
    get_console_shell,
)


logger = logging.getLogger(__name__)


# Container name prefix for Archetype-managed containers
CONTAINER_PREFIX = "archetype"

# Interface wait script for cEOS (adapted from containerlab)
#
# cEOS has a platform detection race condition: Ark.getPlatform() returns None
# if network interfaces aren't available when systemd services start, causing
# boot failures (VEosLabInit skips init, EosInitStage tries modprobe rbfd).
#
# This script runs BEFORE /sbin/init, waiting for CLAB_INTFS interfaces to
# appear in /sys/class/net/. See vendors.py cEOS section for full details.
IF_WAIT_SCRIPT = """#!/bin/sh

# Validate CLAB_INTFS environment variable
REQUIRED_INTFS_NUM=${CLAB_INTFS:-0}
if ! echo "$REQUIRED_INTFS_NUM" | grep -qE '^[0-9]+$' || [ "$REQUIRED_INTFS_NUM" -eq 0 ]; then
    echo "if-wait: CLAB_INTFS not set or invalid, skipping interface wait"
    REQUIRED_INTFS_NUM=0
fi

TIMEOUT=300  # 5 minute timeout
WAIT_TIME=0

int_calc() {
    if [ ! -d "/sys/class/net/" ]; then
        echo "if-wait: /sys/class/net/ not accessible"
        AVAIL_INTFS_NUM=0
        return 1
    fi

    # Count eth1+ interfaces (excluding eth0 which is management)
    AVAIL_INTFS_NUM=$(ls -1 /sys/class/net/ 2>/dev/null | grep -cE '^eth[1-9]')
    return 0
}

# Only wait for interfaces if CLAB_INTFS is set
if [ "$REQUIRED_INTFS_NUM" -gt 0 ]; then
    echo "if-wait: Waiting for $REQUIRED_INTFS_NUM interfaces (timeout: ${TIMEOUT}s)"

    while [ "$WAIT_TIME" -lt "$TIMEOUT" ]; do
        if ! int_calc; then
            echo "if-wait: Failed to check interfaces, continuing..."
            break
        fi

        if [ "$AVAIL_INTFS_NUM" -ge "$REQUIRED_INTFS_NUM" ]; then
            echo "if-wait: Found $AVAIL_INTFS_NUM interfaces (required: $REQUIRED_INTFS_NUM)"
            break
        fi

        # Log every 5 seconds to reduce noise
        if [ $((WAIT_TIME % 5)) -eq 0 ]; then
            echo "if-wait: Have $AVAIL_INTFS_NUM of $REQUIRED_INTFS_NUM interfaces (waited ${WAIT_TIME}s)"
        fi
        sleep 1
        WAIT_TIME=$((WAIT_TIME + 1))
    done

    if [ "$WAIT_TIME" -ge "$TIMEOUT" ]; then
        echo "if-wait: Timeout reached, proceeding with $AVAIL_INTFS_NUM interfaces"
    fi
fi

echo "if-wait: Starting init"
"""

# Label keys for container metadata
LABEL_LAB_ID = "archetype.lab_id"
LABEL_NODE_NAME = "archetype.node_name"
LABEL_NODE_DISPLAY_NAME = "archetype.node_display_name"
LABEL_NODE_KIND = "archetype.node_kind"
LABEL_PROVIDER = "archetype.provider"


def _log_name_from_labels(labels: dict[str, str]) -> str:
    """Format node name for logging from container labels."""
    node_name = labels.get(LABEL_NODE_NAME, "")
    display_name = labels.get(LABEL_NODE_DISPLAY_NAME, "")
    if display_name and display_name != node_name:
        return f"{display_name}({node_name})"
    return node_name


@dataclass
class TopologyNode:
    """Parsed node from topology YAML."""
    name: str
    kind: str
    display_name: str | None = None  # Human-readable name for logs
    image: str | None = None
    host: str | None = None
    binds: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    ports: list[str] = field(default_factory=list)
    startup_config: str | None = None
    exec_: list[str] = field(default_factory=list)  # Post-start commands

    def log_name(self) -> str:
        """Format node name for logging: 'DisplayName(id)' or just 'id'."""
        if self.display_name and self.display_name != self.name:
            return f"{self.display_name}({self.name})"
        return self.name


@dataclass
class TopologyLink:
    """Parsed link from topology YAML."""
    endpoints: list[str]  # ["node1:eth1", "node2:eth1"]


@dataclass
class ParsedTopology:
    """Parsed topology representation."""
    name: str
    nodes: dict[str, TopologyNode]
    links: list[TopologyLink]

    def log_name(self, node_name: str) -> str:
        """Get formatted log name for a node: 'DisplayName(id)' or just 'id'."""
        node = self.nodes.get(node_name)
        if node:
            return node.log_name()
        return node_name


class DockerProvider(Provider):
    """Native Docker container management provider.

    This provider manages containers directly using the Docker SDK,
    providing full control over the container lifecycle.

    Networking:
    - When OVS is enabled (default), uses OVS-based networking with hot-plug support
    - Interfaces are pre-provisioned at boot via OVS veth pairs with VLAN isolation
    - Links are created by assigning matching VLAN tags (hot-connect)
    - When OVS is disabled, falls back to traditional veth-pair networking
    """

    def __init__(self):
        self._docker: docker.DockerClient | None = None
        self._local_network: LocalNetworkManager | None = None
        self._ovs_manager: OVSNetworkManager | None = None

    @property
    def name(self) -> str:
        return "docker"

    @property
    def display_name(self) -> str:
        return "Docker (Native)"

    @property
    def docker(self) -> docker.DockerClient:
        """Lazy-initialize Docker client with extended timeout for slow operations."""
        if self._docker is None:
            # Use docker_client_timeout for Docker operations since container creation
            # can be slow (image extraction, network setup, etc.)
            # Default 60s is too short for cEOS and other complex containers
            self._docker = docker.from_env(timeout=settings.docker_client_timeout)
        return self._docker

    @property
    def local_network(self) -> LocalNetworkManager:
        """Get local network manager instance."""
        if self._local_network is None:
            self._local_network = get_local_manager()
        return self._local_network

    @property
    def ovs_manager(self) -> OVSNetworkManager:
        """Get OVS network manager instance."""
        if self._ovs_manager is None:
            self._ovs_manager = get_ovs_manager()
        return self._ovs_manager

    @property
    def use_ovs(self) -> bool:
        """Check if OVS networking is enabled."""
        return getattr(settings, "enable_ovs", True)

    @property
    def use_ovs_plugin(self) -> bool:
        """Check if OVS Docker plugin is enabled for pre-boot interface provisioning."""
        return getattr(settings, "enable_ovs_plugin", True) and self.use_ovs

    @property
    def ovs_plugin(self) -> DockerOVSPlugin:
        """Get OVS Docker plugin instance."""
        return get_docker_ovs_plugin()

    def _container_name(self, lab_id: str, node_name: str) -> str:
        """Generate container name for a node.

        Format: archetype-{lab_id}-{node_name}
        """
        safe_lab_id = re.sub(r'[^a-zA-Z0-9_-]', '', lab_id)[:20]
        safe_node = re.sub(r'[^a-zA-Z0-9_-]', '', node_name)
        return f"{CONTAINER_PREFIX}-{safe_lab_id}-{safe_node}"

    def _lab_prefix(self, lab_id: str) -> str:
        """Get container name prefix for a lab."""
        safe_lab_id = re.sub(r'[^a-zA-Z0-9_-]', '', lab_id)[:20]
        return f"{CONTAINER_PREFIX}-{safe_lab_id}"

    def _parse_topology(self, topology_yaml: str, lab_id: str) -> ParsedTopology:
        """Parse topology YAML to internal representation.

        Handles both wrapped and flat formats.
        """
        topo = yaml.safe_load(topology_yaml)
        if not topo:
            return ParsedTopology(name=lab_id, nodes={}, links=[])

        # Handle wrapped format: {name: ..., topology: {nodes: ..., links: ...}}
        if "topology" in topo:
            name = topo.get("name", lab_id)
            nodes_raw = topo.get("topology", {}).get("nodes", {})
            links_raw = topo.get("topology", {}).get("links", [])
        else:
            # Flat format: {nodes: ..., links: ...}
            name = lab_id
            nodes_raw = topo.get("nodes", {})
            links_raw = topo.get("links", [])

        # Parse nodes
        nodes = {}
        for node_name, node_config in (nodes_raw or {}).items():
            if not isinstance(node_config, dict):
                continue
            nodes[node_name] = TopologyNode(
                name=node_name,
                kind=node_config.get("kind", "linux"),
                display_name=node_config.get("_display_name"),
                image=node_config.get("image"),
                host=node_config.get("host"),
                binds=node_config.get("binds", []),
                env=node_config.get("env", {}),
                ports=node_config.get("ports", []),
                startup_config=node_config.get("startup-config"),
                exec_=node_config.get("exec", []),
            )

        # Parse links
        links = []
        for link in (links_raw or []):
            if isinstance(link, dict):
                endpoints = link.get("endpoints", [])
            elif isinstance(link, list):
                endpoints = link
            elif isinstance(link, str):
                # String format: "node1:eth1 -- node2:eth1"
                parts = link.replace("--", " ").split()
                endpoints = [p for p in parts if ":" in p]
            else:
                continue
            if len(endpoints) >= 2:
                links.append(TopologyLink(endpoints=endpoints[:2]))

        return ParsedTopology(name=name, nodes=nodes, links=links)

    def _validate_images(self, topology: ParsedTopology) -> list[tuple[str, str]]:
        """Check that all required images exist.

        Returns list of (node_name, image) tuples for missing images.
        """
        missing = []
        for node_name, node in topology.nodes.items():
            # Get effective image
            config = get_config_by_device(node.kind)
            image = node.image or (config.default_image if config else None)
            if not image:
                continue

            try:
                self.docker.images.get(image)
            except ImageNotFound:
                missing.append((node_name, image))
            except APIError as e:
                logger.warning(f"Error checking image {image}: {e}")

        return missing

    def _create_container_config(
        self,
        node: TopologyNode,
        lab_id: str,
        workspace: Path,
        interface_count: int = 0,
    ) -> dict[str, Any]:
        """Build Docker container configuration for a node.

        Args:
            node: The topology node configuration
            lab_id: Lab identifier
            workspace: Path to lab workspace
            interface_count: Number of interfaces this node has (for cEOS CLAB_INTFS)

        Returns a dict suitable for docker.containers.create().
        """
        # Get vendor config
        runtime_config = get_container_config(
            device=node.kind,
            node_name=node.name,
            image=node.image,
            workspace=str(workspace),
        )

        # Merge environment variables (topology overrides vendor defaults)
        env = dict(runtime_config.environment)
        env.update(node.env)

        # Build labels
        labels = {
            LABEL_LAB_ID: lab_id,
            LABEL_NODE_NAME: node.name,
            LABEL_NODE_KIND: node.kind,
            LABEL_PROVIDER: self.name,
        }
        if node.display_name:
            labels[LABEL_NODE_DISPLAY_NAME] = node.display_name

        # Process binds from runtime config and node-specific binds
        binds = list(runtime_config.binds)
        binds.extend(node.binds)

        # Build container configuration
        # Note: network_mode is NOT set here - it's handled dynamically in
        # _create_containers based on whether OVS plugin is enabled.
        config: dict[str, Any] = {
            "image": runtime_config.image,
            "name": self._container_name(lab_id, node.name),
            "hostname": runtime_config.hostname,
            "environment": env,
            "labels": labels,
            "detach": True,
            "tty": True,
            "stdin_open": True,
            # Auto-restart on crash, but respect explicit stops
            "restart_policy": {"Name": "unless-stopped"},
        }

        # Capabilities
        if runtime_config.capabilities:
            config["cap_add"] = runtime_config.capabilities

        # Privileged mode
        if runtime_config.privileged:
            config["privileged"] = True

        # Volume binds
        if binds:
            config["volumes"] = {}
            for bind in binds:
                if ":" in bind:
                    host_path, container_path = bind.split(":", 1)
                    # Handle read-only mounts
                    ro = False
                    if container_path.endswith(":ro"):
                        container_path = container_path[:-3]
                        ro = True
                    config["volumes"][host_path] = {
                        "bind": container_path,
                        "mode": "ro" if ro else "rw",
                    }

        # Sysctls
        if runtime_config.sysctls:
            config["sysctls"] = runtime_config.sysctls

        # Entry command - ensure entrypoint is a list for Docker SDK
        # For cEOS, we wrap the init process with if-wait.sh to wait for interfaces
        # before starting init - this prevents the platform detection race condition
        if node.kind == "ceos" and interface_count > 0:
            # Set CLAB_INTFS so the if-wait.sh script knows how many interfaces to wait for
            config["environment"]["CLAB_INTFS"] = str(interface_count)

            # Use bash wrapper to run if-wait.sh before /sbin/init
            # The script is created in flash dir by _ensure_directories()
            config["entrypoint"] = ["/bin/bash", "-c"]
            config["command"] = ["/mnt/flash/if-wait.sh ; exec /sbin/init"]
            logger.debug(f"cEOS {node.name}: using if-wait.sh wrapper with CLAB_INTFS={interface_count}")
        elif runtime_config.entrypoint:
            # Docker SDK expects entrypoint as a list
            if isinstance(runtime_config.entrypoint, str):
                config["entrypoint"] = [runtime_config.entrypoint]
            else:
                config["entrypoint"] = runtime_config.entrypoint

        if runtime_config.cmd and "command" not in config:
            config["command"] = runtime_config.cmd

        # Ensure at least one of entrypoint or command is set
        # Some images (like cEOS) have ENTRYPOINT [] which clears defaults
        if "entrypoint" not in config and "command" not in config:
            config["command"] = ["sleep", "infinity"]

        return config

    async def _ensure_directories(
        self,
        topology: ParsedTopology,
        workspace: Path,
    ) -> None:
        """Create required directories for nodes (e.g., cEOS flash)."""
        for node_name, node in topology.nodes.items():
            if node.kind == "ceos":
                flash_dir = workspace / "configs" / node_name / "flash"
                flash_dir.mkdir(parents=True, exist_ok=True)
                logger.debug(f"Created flash directory: {flash_dir}")

                # Create systemd environment config for cEOS
                # This is needed because systemd services don't inherit
                # Docker container environment variables
                systemd_dir = workspace / "configs" / node_name / "systemd"
                systemd_dir.mkdir(parents=True, exist_ok=True)
                env_file = systemd_dir / "ceos-env.conf"
                env_file.write_text(
                    "[Manager]\n"
                    "DefaultEnvironment=EOS_PLATFORM=ceoslab CEOS=1 "
                    "container=docker ETBA=1 SKIP_ZEROTOUCH_BARRIER_IN_SYSDBINIT=1 "
                    "INTFTYPE=eth MGMT_INTF=eth0 CEOS_NOZEROTOUCH=1\n"
                )
                logger.debug(f"Created cEOS systemd env config: {env_file}")

                # Write startup-config to flash directory
                # cEOS reads startup-config from /mnt/flash/startup-config
                startup_config_path = flash_dir / "startup-config"

                # Check for existing startup-config in configs/{node}/startup-config
                # (this is where extracted configs are saved)
                extracted_config = workspace / "configs" / node_name / "startup-config"

                if node.startup_config:
                    # Use startup-config from topology YAML
                    startup_config_path.write_text(node.startup_config)
                    logger.debug(f"Wrote startup-config from topology for {node.log_name()}")
                elif extracted_config.exists():
                    # Copy previously extracted config to flash
                    import shutil
                    shutil.copy2(extracted_config, startup_config_path)
                    logger.debug(f"Copied extracted startup-config for {node.log_name()}")
                elif not startup_config_path.exists():
                    # Create minimal startup-config with essential initialization
                    # Use display_name for hostname if available, otherwise node_name
                    hostname = node.display_name or node_name
                    minimal_config = f"""! Minimal cEOS startup config
hostname {hostname}
!
no aaa root
!
username admin privilege 15 role network-admin nopassword
!
"""
                    startup_config_path.write_text(minimal_config)
                    logger.debug(f"Created minimal startup-config for {node.log_name()}")

                # Create zerotouch-config to disable ZTP
                # This file's presence tells cEOS to skip Zero Touch Provisioning
                zerotouch_config = flash_dir / "zerotouch-config"
                if not zerotouch_config.exists():
                    zerotouch_config.write_text("DISABLE=True\n")
                    logger.debug(f"Created zerotouch-config for {node.log_name()}")

                # Create if-wait.sh script to wait for interfaces before boot
                # This prevents the platform detection race where Ark.getPlatform()
                # returns None because init runs before interfaces are ready
                if_wait_script = flash_dir / "if-wait.sh"
                if_wait_script.write_text(IF_WAIT_SCRIPT)
                if_wait_script.chmod(0o755)
                logger.debug(f"Created if-wait.sh for {node.log_name()}")

    def _calculate_required_interfaces(self, topology: ParsedTopology) -> int:
        """Calculate the maximum interface index needed based on topology links.

        Examines all links to find the highest interface number used,
        then adds a small buffer for flexibility.

        Args:
            topology: Parsed topology with nodes and links

        Returns:
            Number of interfaces to create (max index found + buffer)
        """
        max_index = 0

        for link in topology.links:
            for endpoint in link.endpoints:
                # Endpoint format: "node:eth1" or "node:Ethernet1"
                if ":" in endpoint:
                    _, interface = endpoint.split(":", 1)
                    # Extract number from interface name (eth1, Ethernet1, etc.)
                    import re
                    match = re.search(r"(\d+)$", interface)
                    if match:
                        index = int(match.group(1))
                        max_index = max(max_index, index)

        # Add buffer of 4 interfaces for flexibility (connecting new links)
        # Minimum of 4 interfaces even if no links defined
        return max(max_index + 4, 4)

    def _count_node_interfaces(self, node_name: str, topology: ParsedTopology) -> int:
        """Count the number of interfaces connected to a specific node.

        Args:
            node_name: Name of the node
            topology: Parsed topology with links

        Returns:
            Number of interfaces this node has in the topology
        """
        interfaces = set()

        for link in topology.links:
            for endpoint in link.endpoints:
                if ":" in endpoint:
                    ep_node, interface = endpoint.split(":", 1)
                    if ep_node == node_name:
                        # Extract interface number
                        match = re.search(r"(\d+)$", interface)
                        if match:
                            interfaces.add(int(match.group(1)))

        return len(interfaces)

    async def _create_lab_networks(
        self,
        lab_id: str,
        max_interfaces: int = 8,
    ) -> dict[str, str]:
        """Create Docker networks for lab interfaces via OVS plugin.

        Creates one network per interface (eth1, eth2, ..., ethN).
        All networks share the same OVS bridge (ovs-{lab_id}).

        Args:
            lab_id: Lab identifier
            max_interfaces: Maximum number of interfaces to create

        Returns:
            Dict mapping interface name (e.g., "eth1") to network name
        """
        networks = {}

        for i in range(1, max_interfaces + 1):
            interface_name = f"eth{i}"
            network_name = f"{lab_id}-{interface_name}"

            try:
                # Check if network already exists (run in thread to avoid blocking event loop)
                try:
                    await asyncio.to_thread(self.docker.networks.get, network_name)
                    logger.debug(f"Network {network_name} already exists")
                    networks[interface_name] = network_name
                    continue
                except NotFound:
                    pass

                # Create network via Docker API - plugin handles OVS bridge
                # Use null IPAM driver to avoid consuming IP address space.
                # These networks are L2-only (OVS switching), no IP allocation needed.
                # Run in thread pool to avoid blocking event loop (OVS plugin needs it)
                await asyncio.to_thread(
                    self.docker.networks.create,
                    name=network_name,
                    driver="archetype-ovs",
                    ipam=IPAMConfig(driver="null"),
                    options={
                        "lab_id": lab_id,
                        "interface_name": interface_name,
                    },
                )
                networks[interface_name] = network_name
                logger.debug(f"Created network {network_name}")

            except APIError as e:
                logger.error(f"Failed to create network {network_name}: {e}")

        logger.info(f"Created {len(networks)} Docker networks for lab {lab_id}")
        return networks

    async def _delete_lab_networks(self, lab_id: str) -> int:
        """Delete all Docker networks for a lab.

        Uses efficient query-first approach: lists networks matching the lab's
        name prefix, then deletes only those that exist. Much faster than the
        previous brute-force approach that tried 325 network names.

        Args:
            lab_id: Lab identifier

        Returns:
            Number of networks deleted
        """
        deleted = 0

        try:
            # Query networks by name prefix (efficient - single API call)
            # Networks are named: {lab_id}-eth1, {lab_id}-Ethernet1, etc.
            # Run in thread to avoid blocking event loop
            all_networks = await asyncio.to_thread(self.docker.networks.list)

            # Filter to networks that start with this lab's prefix
            lab_prefix = f"{lab_id}-"
            lab_networks = [n for n in all_networks if n.name.startswith(lab_prefix)]

            for network in lab_networks:
                try:
                    await asyncio.to_thread(network.remove)
                    deleted += 1
                    logger.debug(f"Deleted network {network.name}")
                except APIError as e:
                    # Network might be in use or already deleted
                    logger.warning(f"Failed to delete network {network.name}: {e}")

        except APIError as e:
            logger.warning(f"Failed to list networks for lab {lab_id}: {e}")

        if deleted > 0:
            logger.info(f"Deleted {deleted} Docker networks for lab {lab_id}")
        return deleted

    async def _attach_container_to_networks(
        self,
        container: Any,
        lab_id: str,
        interface_count: int,
        interface_prefix: str = "eth",
        start_index: int = 1,
    ) -> list[str]:
        """Attach container to lab interface networks.

        Called after container creation but before container start.
        Docker provisions interfaces when the container starts.

        Args:
            container: Docker container object
            lab_id: Lab identifier
            interface_count: Number of interfaces to attach
            interface_prefix: Interface naming prefix
            start_index: Starting interface number

        Returns:
            List of attached network names
        """
        # Build list of networks to attach
        networks_to_attach = []
        for i in range(interface_count):
            iface_num = start_index + i
            interface_name = f"{interface_prefix}{iface_num}"
            network_name = f"{lab_id}-{interface_name}"
            networks_to_attach.append(network_name)

        # Attach all networks in a single thread to avoid thread pool exhaustion
        # Each network.connect() triggers Docker plugin callbacks which need the event loop
        def attach_all_networks(docker_client, net_names: list[str], cont_id: str, cont_name: str) -> list[str]:
            import logging
            log = logging.getLogger(__name__)
            attached = []
            log.info(f"[{cont_name}] attach_all_networks starting: {len(net_names)} networks")
            for net_name in net_names:
                try:
                    log.debug(f"[{cont_name}] Attaching to {net_name}...")
                    network = docker_client.networks.get(net_name)
                    network.connect(cont_id)
                    attached.append(net_name)
                    log.debug(f"[{cont_name}] Attached to {net_name}")
                except Exception as e:
                    if "already exists" in str(e).lower():
                        attached.append(net_name)
                    elif "not found" in str(e).lower():
                        log.warning(f"[{cont_name}] Network {net_name} not found")
                    else:
                        log.warning(f"[{cont_name}] Failed to attach to {net_name}: {e}")
            log.info(f"[{cont_name}] attach_all_networks completed: {len(attached)} attached")
            return attached

        attached = await asyncio.to_thread(
            attach_all_networks, self.docker, networks_to_attach, container.id, container.name
        )

        for net_name in attached:
            logger.debug(f"Attached {container.name} to {net_name}")

        return attached

    async def _create_containers(
        self,
        topology: ParsedTopology,
        lab_id: str,
        workspace: Path,
    ) -> dict[str, Any]:
        """Create all containers for a topology.

        Returns dict mapping node_name -> container object.
        """
        containers = {}

        # Calculate the number of interfaces actually needed based on topology links
        # This avoids creating 64 networks per node which exhausts Docker's IP pool
        required_interfaces = self._calculate_required_interfaces(topology)
        logger.info(f"Lab {lab_id} requires {required_interfaces} interfaces based on topology")

        # Create lab networks if OVS plugin is enabled
        # Always use "eth" naming for Docker networks for consistency
        # The OVS plugin handles interface naming inside containers
        if self.use_ovs_plugin:
            await self._create_lab_networks(lab_id, max_interfaces=required_interfaces)

        try:
            for node_name, node in topology.nodes.items():
                container_name = self._container_name(lab_id, node_name)
                log_name = node.log_name()

                # Check if container already exists (run in thread to avoid blocking)
                try:
                    existing = await asyncio.to_thread(
                        self.docker.containers.get, container_name
                    )
                    if existing.status == "running":
                        logger.info(f"Container {log_name} already running")
                        containers[node_name] = existing
                        continue
                    else:
                        logger.info(f"Removing stopped container {log_name}")
                        await asyncio.to_thread(existing.remove, force=True)
                except NotFound:
                    pass

                # Build container config
                # Count interfaces for this specific node (for cEOS if-wait.sh)
                node_interface_count = self._count_node_interfaces(node_name, topology)
                config = self._create_container_config(
                    node, lab_id, workspace, interface_count=node_interface_count
                )

                # Set network mode based on whether OVS plugin is enabled
                # When OVS plugin is enabled, we attach to Docker networks which
                # provision interfaces BEFORE container init runs (critical for cEOS).
                # When disabled, we use "none" mode and provision interfaces post-start.
                if self.use_ovs_plugin:
                    # Use the pre-calculated required_interfaces count
                    # This avoids creating 64 interfaces per node (vendor max_ports)
                    # and only creates what's actually needed based on topology links

                    # Docker network names always use "eth" prefix for consistency
                    # The OVS plugin handles renaming inside the container based on
                    # the interface_name option passed during network creation
                    first_network = f"{lab_id}-eth1"
                    config["network"] = first_network
                    logger.info(f"Creating container {log_name} with image {config['image']}")

                    # Create container - run in thread pool to avoid blocking event loop
                    logger.debug(f"[{log_name}] Starting container.create...")
                    container = await asyncio.to_thread(
                        lambda cfg=config: self.docker.containers.create(**cfg)
                    )
                    logger.debug(f"[{log_name}] container.create completed")
                    containers[node_name] = container

                    # Attach to remaining interface networks (eth2, eth3, ...)
                    logger.debug(f"[{log_name}] Starting network attachments...")
                    await self._attach_container_to_networks(
                        container=container,
                        lab_id=lab_id,
                        interface_count=required_interfaces - 1,  # Already attached to eth1
                        interface_prefix="eth",
                        start_index=2,  # Start from eth2
                    )
                    logger.debug(f"[{log_name}] Network attachments completed")

                    # Docker processes network.connect() asynchronously - the call returns
                    # before Docker finishes creating endpoints. Wait briefly to let Docker
                    # complete endpoint creation before proceeding.
                    await asyncio.sleep(0.5)
                else:
                    # Legacy mode: use "none" network, provision interfaces post-start
                    config["network_mode"] = "none"
                    logger.info(f"Creating container {log_name} with image {config['image']}")

                    container = await asyncio.to_thread(
                        lambda cfg=config: self.docker.containers.create(**cfg)
                    )
                    containers[node_name] = container

        except Exception as e:
            # Clean up partially created resources on failure to prevent leaks
            logger.error(f"Container creation failed, cleaning up: {e}")

            # Remove any containers that were created before the failure
            for node_name, container in containers.items():
                try:
                    await asyncio.to_thread(container.remove, force=True, v=True)
                    logger.debug(f"Cleaned up container for {node_name}")
                except Exception as cleanup_err:
                    logger.warning(f"Failed to clean up container {node_name}: {cleanup_err}")

            # Clean up Docker networks to prevent IP address exhaustion
            if self.use_ovs_plugin:
                try:
                    deleted = await self._delete_lab_networks(lab_id)
                    logger.info(f"Cleaned up {deleted} networks after failed container creation")
                except Exception as net_err:
                    logger.warning(f"Failed to clean up networks: {net_err}")

            raise

        return containers

    async def _start_containers(
        self,
        containers: dict[str, Any],
        topology: ParsedTopology,
        lab_id: str,
    ) -> list[str]:
        """Start all containers and provision interfaces as needed.

        When OVS plugin is enabled, interfaces are already provisioned via Docker
        networks (created in _create_containers), so no post-start provisioning needed.

        When using legacy OVS mode (plugin disabled), provisions real veth pairs
        via OVS for hot-plug support after container start.

        When OVS is disabled entirely, falls back to dummy interfaces.

        Returns list of node names that failed to start.
        """
        failed = []

        # Initialize legacy OVS manager if OVS is enabled but plugin is not
        if self.use_ovs and not self.use_ovs_plugin:
            try:
                await self.ovs_manager.initialize()
            except Exception as e:
                logger.warning(f"OVS initialization failed, falling back to legacy networking: {e}")

        # Track if we've started a cEOS container (for staggered boot)
        ceos_started = False

        for node_name, container in containers.items():
            try:
                log_name = topology.log_name(node_name)
                node = topology.nodes.get(node_name)
                is_ceos = node and node.kind in ("ceos", "eos")

                # Stagger cEOS container starts to avoid modprobe race condition
                # When multiple cEOS instances start simultaneously, they race to
                # load kernel modules (tun, etc.) which can cause boot failures
                if is_ceos and ceos_started:
                    logger.info(f"Waiting 5s before starting {log_name} (cEOS stagger)")
                    await asyncio.sleep(5)

                if container.status != "running":
                    # Run in thread pool - start triggers network plugin callbacks
                    await asyncio.to_thread(container.start)
                    logger.info(f"Started container {log_name}")

                if is_ceos:
                    ceos_started = True

                # Skip interface provisioning if OVS plugin is handling it
                # (interfaces already exist via Docker network attachments)
                if self.use_ovs_plugin:
                    logger.debug(f"Interfaces for {log_name} provisioned via OVS plugin")
                    continue

                # Legacy interface provisioning (post-start)
                node = topology.nodes.get(node_name)
                if node:
                    config = get_config_by_device(node.kind)
                    if config:
                        if self.use_ovs and self.ovs_manager._initialized:
                            # Use OVS-based provisioning for hot-plug support
                            await self._provision_ovs_interfaces(
                                container_name=container.name,
                                interface_prefix=config.port_naming,
                                start_index=config.port_start_index,
                                count=config.max_ports,
                                lab_id=lab_id,
                            )
                        elif hasattr(config, 'provision_interfaces') and config.provision_interfaces:
                            # Legacy fallback: use dummy interfaces
                            await self.local_network.provision_dummy_interfaces(
                                container_name=container.name,
                                interface_prefix=config.port_naming,
                                start_index=config.port_start_index,
                                count=config.max_ports,
                            )

            except Exception as e:
                logger.error(f"Failed to start {container.name}: {e}")
                failed.append(node_name)
        return failed

    async def _provision_ovs_interfaces(
        self,
        container_name: str,
        interface_prefix: str,
        start_index: int,
        count: int,
        lab_id: str,
    ) -> int:
        """Provision interfaces via OVS for hot-plug support.

        Creates real veth pairs attached to OVS bridge with unique VLAN tags.
        Each interface is isolated until hot-connected to another interface.

        Args:
            container_name: Docker container name
            interface_prefix: Interface name prefix (e.g., "eth", "Ethernet")
            start_index: Starting interface number
            count: Number of interfaces to create
            lab_id: Lab identifier for tracking

        Returns:
            Number of interfaces successfully provisioned
        """
        provisioned = 0

        for i in range(count):
            iface_num = start_index + i
            # Determine interface name based on prefix
            if interface_prefix.endswith("-"):
                # e.g., "e1-" -> "e1-1", "e1-2" (SR Linux style)
                iface_name = f"{interface_prefix}{iface_num}"
            else:
                iface_name = f"{interface_prefix}{iface_num}"

            try:
                await self.ovs_manager.provision_interface(
                    container_name=container_name,
                    interface_name=iface_name,
                    lab_id=lab_id,
                )
                provisioned += 1
            except Exception as e:
                logger.warning(f"Failed to provision OVS interface {iface_name}: {e}")
                # Continue with remaining interfaces

        if provisioned > 0:
            logger.info(f"Provisioned {provisioned} OVS interfaces in {container_name}")

        return provisioned

    async def _plugin_hot_connect(
        self,
        lab_id: str,
        container_a: str,
        iface_a: str,
        container_b: str,
        iface_b: str,
    ) -> bool:
        """Connect two interfaces using the OVS plugin's per-lab bridge.

        Finds OVS ports by container endpoint and sets matching VLAN tags.

        Args:
            lab_id: Lab identifier
            container_a: First container name
            iface_a: Interface on first container
            container_b: Second container name
            iface_b: Interface on second container

        Returns:
            True if successful, False otherwise
        """
        bridge_name = f"ovs-{lab_id[:12]}"

        # Find OVS ports attached to this container's interfaces
        # Ports are named with pattern: vh{endpoint_prefix}{random}
        # We need to find them by checking which port goes to which container

        async def find_ovs_port(container_name: str, interface_name: str) -> str | None:
            """Find OVS port name for a container interface."""
            try:
                container = await asyncio.to_thread(self.docker.containers.get, container_name)
                pid = container.attrs["State"]["Pid"]

                # Get interface's peer index from inside container
                proc = await asyncio.create_subprocess_exec(
                    "nsenter", "-t", str(pid), "-n",
                    "cat", f"/sys/class/net/{interface_name}/iflink",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await proc.communicate()
                if proc.returncode != 0:
                    return None

                peer_idx = stdout.decode().strip()

                # Find host interface with this index
                proc = await asyncio.create_subprocess_exec(
                    "ip", "-o", "link", "show",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await proc.communicate()

                for line in stdout.decode().split("\n"):
                    if line.startswith(f"{peer_idx}:"):
                        # Format: "123: vethXXX@if456: <...>"
                        parts = line.split(":")
                        if len(parts) >= 2:
                            port_name = parts[1].strip().split("@")[0]
                            # Verify it's on our bridge
                            proc = await asyncio.create_subprocess_exec(
                                "ovs-vsctl", "port-to-br", port_name,
                                stdout=asyncio.subprocess.PIPE,
                                stderr=asyncio.subprocess.PIPE,
                            )
                            br_out, _ = await proc.communicate()
                            if br_out.decode().strip() == bridge_name:
                                return port_name
                return None
            except Exception as e:
                logger.error(f"Error finding OVS port for {container_name}:{interface_name}: {e}")
                return None

        # Find ports for both endpoints
        port_a = await find_ovs_port(container_a, iface_a)
        port_b = await find_ovs_port(container_b, iface_b)

        if not port_a or not port_b:
            logger.error(f"Could not find OVS ports for {container_a}:{iface_a} or {container_b}:{iface_b}")
            return False

        # Get VLAN tag from port_a
        proc = await asyncio.create_subprocess_exec(
            "ovs-vsctl", "get", "port", port_a, "tag",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        vlan_tag = stdout.decode().strip()

        # Set port_b to same VLAN tag
        proc = await asyncio.create_subprocess_exec(
            "ovs-vsctl", "set", "port", port_b, f"tag={vlan_tag}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

        if proc.returncode == 0:
            logger.info(f"Connected {container_a}:{iface_a} <-> {container_b}:{iface_b} (VLAN {vlan_tag})")
            return True
        else:
            logger.error(f"Failed to set VLAN tag on {port_b}")
            return False

    async def _create_links(
        self,
        topology: ParsedTopology,
        lab_id: str,
    ) -> int:
        """Create links between containers.

        When OVS plugin is enabled, uses plugin's per-lab bridge with VLAN matching.
        When legacy OVS is enabled, uses global OVS bridge with hot-connect.
        When OVS is disabled, uses traditional veth pairs.

        Returns number of links created.
        """
        created = 0
        for i, link in enumerate(topology.links):
            if len(link.endpoints) < 2:
                continue

            # Parse endpoints
            # Format: "node:interface" or "node:interface:ip"
            ep_a = link.endpoints[0].split(":")
            ep_b = link.endpoints[1].split(":")

            node_a = ep_a[0]
            iface_a = ep_a[1] if len(ep_a) > 1 else f"eth{i+1}"
            ip_a = ep_a[2] if len(ep_a) > 2 else None

            node_b = ep_b[0]
            iface_b = ep_b[1] if len(ep_b) > 1 else f"eth{i+1}"
            ip_b = ep_b[2] if len(ep_b) > 2 else None

            container_a = self._container_name(lab_id, node_a)
            container_b = self._container_name(lab_id, node_b)

            link_id = f"{node_a}:{iface_a}-{node_b}:{iface_b}"

            try:
                if self.use_ovs_plugin:
                    # Use OVS plugin's per-lab bridge
                    await self._plugin_hot_connect(
                        lab_id=lab_id,
                        container_a=container_a,
                        iface_a=iface_a,
                        container_b=container_b,
                        iface_b=iface_b,
                    )
                elif self.use_ovs and self.ovs_manager._initialized:
                    # Use legacy OVS hot-connect (global bridge)
                    await self.ovs_manager.hot_connect(
                        container_a=container_a,
                        iface_a=iface_a,
                        container_b=container_b,
                        iface_b=iface_b,
                        lab_id=lab_id,
                    )
                else:
                    # Fallback to traditional veth pairs
                    await self.local_network.create_link(
                        lab_id=lab_id,
                        link_id=link_id,
                        container_a=container_a,
                        container_b=container_b,
                        iface_a=iface_a,
                        iface_b=iface_b,
                        ip_a=ip_a,
                        ip_b=ip_b,
                    )
                created += 1
            except Exception as e:
                logger.error(f"Failed to create link {link_id}: {e}")

        return created

    async def _wait_for_readiness(
        self,
        topology: ParsedTopology,
        lab_id: str,
        containers: dict[str, Any],
        timeout: float = 300.0,
    ) -> dict[str, bool]:
        """Wait for containers to be ready based on vendor-specific probes.

        Returns dict mapping node_name -> ready status.
        """
        ready_status = {name: False for name in containers.keys()}
        start_time = asyncio.get_event_loop().time()

        while True:
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > timeout:
                break

            all_ready = True
            for node_name, container in containers.items():
                if ready_status[node_name]:
                    continue

                node = topology.nodes.get(node_name)
                if not node:
                    ready_status[node_name] = True
                    continue

                log_name = node.log_name()
                config = get_config_by_device(node.kind)
                if not config or config.readiness_probe == "none":
                    ready_status[node_name] = True
                    continue

                # Check node-specific timeout
                node_timeout = config.readiness_timeout
                if elapsed > node_timeout:
                    logger.warning(f"Node {log_name} timed out waiting for readiness")
                    continue

                # Check readiness - use asyncio.to_thread to avoid blocking event loop
                try:
                    await asyncio.to_thread(container.reload)
                    if container.status != "running":
                        all_ready = False
                        continue

                    if config.readiness_probe == "log_pattern":
                        # Check logs for pattern - run in thread to avoid blocking
                        logs_bytes = await asyncio.to_thread(container.logs, tail=100)
                        logs = logs_bytes.decode(errors="replace")
                        if config.readiness_pattern:
                            if re.search(config.readiness_pattern, logs):
                                ready_status[node_name] = True
                                logger.info(f"Node {log_name} is ready")
                            else:
                                all_ready = False
                        else:
                            ready_status[node_name] = True
                    else:
                        ready_status[node_name] = True

                except Exception as e:
                    logger.debug(f"Error checking readiness for {log_name}: {e}")
                    all_ready = False

            if all_ready:
                break

            await asyncio.sleep(5)

        return ready_status

    def _get_container_status(self, container) -> NodeStatus:
        """Map Docker container status to NodeStatus."""
        status = container.status.lower()
        if status == "running":
            return NodeStatus.RUNNING
        elif status == "created":
            return NodeStatus.PENDING
        elif status in ("exited", "dead"):
            return NodeStatus.STOPPED
        elif status == "paused":
            return NodeStatus.STOPPED
        elif status == "restarting":
            return NodeStatus.STARTING
        else:
            return NodeStatus.UNKNOWN

    def _get_container_ips(self, container) -> list[str]:
        """Extract IP addresses from container."""
        ips = []
        try:
            networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})
            for net_name, net_info in networks.items():
                if net_info.get("IPAddress"):
                    ips.append(net_info["IPAddress"])
        except Exception:
            pass
        return ips

    def _node_from_container(self, container) -> NodeInfo | None:
        """Convert Docker container to NodeInfo."""
        labels = container.labels or {}

        node_name = labels.get(LABEL_NODE_NAME)
        if not node_name:
            return None

        return NodeInfo(
            name=node_name,
            status=self._get_container_status(container),
            container_id=container.short_id,
            image=container.image.tags[0] if container.image.tags else str(container.image.id)[:12],
            ip_addresses=self._get_container_ips(container),
        )

    def _topology_from_json(self, deploy_topology: DeployTopology) -> ParsedTopology:
        """Convert DeployTopology (JSON) to internal ParsedTopology.

        Args:
            deploy_topology: Structured JSON topology from controller

        Returns:
            ParsedTopology for internal use
        """
        nodes = {}
        for n in deploy_topology.nodes:
            nodes[n.name] = TopologyNode(
                name=n.name,
                kind=n.kind,
                display_name=n.display_name,
                image=n.image,
                host=None,  # Not needed for execution; host routing done by controller
                binds=n.binds,
                env=n.env,
                ports=n.ports,
                startup_config=n.startup_config,
                exec_=n.exec_cmds,
            )

        links = []
        for l in deploy_topology.links:
            links.append(TopologyLink(
                endpoints=[
                    f"{l.source_node}:{l.source_interface}",
                    f"{l.target_node}:{l.target_interface}",
                ]
            ))

        return ParsedTopology(name="lab", nodes=nodes, links=links)

    async def deploy(
        self,
        lab_id: str,
        topology: DeployTopology | None,
        topology_yaml: str | None,
        workspace: Path,
        agent_id: str | None = None,
    ) -> DeployResult:
        """Deploy a topology using Docker SDK.

        Accepts topology in two formats:
        - topology: Structured JSON format (preferred)
        - topology_yaml: Legacy YAML string format

        Steps:
        1. Parse topology (from JSON or YAML)
        2. Validate images exist
        3. Create required directories
        4. Create containers (network mode: none)
        5. Start containers
        6. Create local links (veth pairs)
        7. Wait for readiness
        """
        workspace.mkdir(parents=True, exist_ok=True)

        # Parse topology from JSON or YAML
        if topology:
            parsed_topology = self._topology_from_json(topology)
        elif topology_yaml:
            parsed_topology = self._parse_topology(topology_yaml, lab_id)
        else:
            return DeployResult(
                success=False,
                error="No topology provided (need either JSON or YAML)",
            )
        if not parsed_topology.nodes:
            return DeployResult(
                success=False,
                error="No nodes found in topology",
            )

        logger.info(f"Deploying lab {lab_id} with {len(parsed_topology.nodes)} nodes")

        # Validate images
        missing_images = self._validate_images(parsed_topology)
        if missing_images:
            logger.error(f"Missing images: {missing_images}")
            error_lines = ["Missing Docker images:"]
            for node_name, image in missing_images:
                log_name = parsed_topology.log_name(node_name)
                error_lines.append(f"  • Node '{log_name}' requires: {image}")
            error_lines.append("")
            error_lines.append("Please upload images via the Images page or import manually.")
            error_msg = "\n".join(error_lines)
            return DeployResult(
                success=False,
                error=f"Missing {len(missing_images)} Docker image(s)",
                stderr=error_msg,
            )

        # Create directories
        await self._ensure_directories(parsed_topology, workspace)

        # Create management network
        try:
            await self.local_network.create_management_network(lab_id)
        except Exception as e:
            logger.warning(f"Failed to create management network: {e}")

        # Create containers
        try:
            containers = await self._create_containers(parsed_topology, lab_id, workspace)
        except Exception as e:
            logger.error(f"Failed to create containers: {e}")
            return DeployResult(
                success=False,
                error=f"Failed to create containers: {e}",
            )

        # Start containers
        failed_starts = await self._start_containers(containers, parsed_topology, lab_id)
        if failed_starts:
            failed_log_names = [parsed_topology.log_name(n) for n in failed_starts]
            logger.warning(f"Some containers failed to start: {failed_log_names}")

        # Create local links
        links_created = await self._create_links(parsed_topology, lab_id)
        logger.info(f"Created {links_created} local links")

        # Wait for readiness
        ready_status = await self._wait_for_readiness(
            parsed_topology, lab_id, containers, timeout=settings.deploy_timeout
        )
        not_ready = [name for name, ready in ready_status.items() if not ready]
        if not_ready:
            not_ready_log_names = [parsed_topology.log_name(n) for n in not_ready]
            logger.warning(f"Some nodes not ready after timeout: {not_ready_log_names}")

        # Get final status
        status_result = await self.status(lab_id, workspace)

        stdout_lines = [
            f"Deployed {len(containers)} containers",
            f"Created {links_created} links",
        ]
        if not_ready:
            not_ready_log_names = [parsed_topology.log_name(n) for n in not_ready]
            stdout_lines.append(f"Warning: {len(not_ready)} nodes not fully ready: {', '.join(not_ready_log_names)}")

        return DeployResult(
            success=True,
            nodes=status_result.nodes,
            stdout="\n".join(stdout_lines),
        )

    async def destroy(
        self,
        lab_id: str,
        workspace: Path,
    ) -> DestroyResult:
        """Destroy all containers and networking for a lab."""
        prefix = self._lab_prefix(lab_id)
        removed = 0
        volumes_removed = 0
        errors = []

        try:
            # Find all containers for this lab - run in thread to avoid blocking
            containers = await asyncio.to_thread(
                self.docker.containers.list,
                all=True,
                filters={"label": f"{LABEL_LAB_ID}={lab_id}"},
            )

            # Also find by prefix (fallback)
            prefix_containers = await asyncio.to_thread(
                self.docker.containers.list,
                all=True,
                filters={"name": prefix},
            )
            all_containers = {c.id: c for c in containers}
            for c in prefix_containers:
                all_containers[c.id] = c

            # Remove containers
            for container in all_containers.values():
                try:
                    await asyncio.to_thread(container.remove, force=True, v=True)  # v=True removes anonymous volumes
                    removed += 1
                    logger.info(f"Removed container {container.name}")
                except Exception as e:
                    errors.append(f"Failed to remove {container.name}: {e}")

            # Clean up orphaned volumes for this lab
            volumes_removed = await self._cleanup_lab_volumes(lab_id)
            if volumes_removed > 0:
                logger.info(f"Volume cleanup: {volumes_removed} volumes removed")

            # Clean up local networking
            cleanup_result = await self.local_network.cleanup_lab(lab_id)
            logger.info(f"Local network cleanup: {cleanup_result}")

            # Clean up OVS networking if enabled
            if self.use_ovs and self.ovs_manager._initialized:
                ovs_cleanup_result = await self.ovs_manager.cleanup_lab(lab_id)
                logger.info(f"OVS network cleanup: {ovs_cleanup_result}")

            # Clean up Docker networks if OVS plugin is enabled
            if self.use_ovs_plugin:
                networks_deleted = await self._delete_lab_networks(lab_id)
                logger.info(f"Docker network cleanup: {networks_deleted} networks deleted")

        except Exception as e:
            errors.append(f"Error during destroy: {e}")

        success = len(errors) == 0
        stdout_parts = [f"Removed {removed} containers"]
        if volumes_removed > 0:
            stdout_parts.append(f"Removed {volumes_removed} volumes")
        return DestroyResult(
            success=success,
            stdout=", ".join(stdout_parts),
            stderr="\n".join(errors) if errors else "",
            error=errors[0] if errors else None,
        )

    async def _cleanup_lab_volumes(self, lab_id: str) -> int:
        """Clean up orphaned Docker volumes for a lab.

        Removes volumes that:
        1. Have the archetype.lab_id label matching this lab
        2. Are dangling (not attached to any container)

        Args:
            lab_id: Lab identifier

        Returns:
            Number of volumes removed
        """
        removed = 0

        try:
            # Find volumes with our lab label - run in thread to avoid blocking
            volumes = await asyncio.to_thread(
                self.docker.volumes.list,
                filters={"label": f"{LABEL_LAB_ID}={lab_id}"}
            )

            for volume in volumes:
                try:
                    await asyncio.to_thread(volume.remove, force=True)
                    removed += 1
                    logger.debug(f"Removed volume {volume.name}")
                except APIError as e:
                    # Volume might still be in use
                    logger.debug(f"Could not remove volume {volume.name}: {e}")

            # Also prune any dangling volumes (not tied to a container)
            # This catches volumes that weren't labeled but were created by our containers
            prune_result = await asyncio.to_thread(
                self.docker.volumes.prune,
                filters={"dangling": "true"}
            )
            if prune_result.get("VolumesDeleted"):
                pruned_count = len(prune_result["VolumesDeleted"])
                removed += pruned_count
                logger.debug(f"Pruned {pruned_count} dangling volumes")

        except APIError as e:
            logger.warning(f"Failed to cleanup volumes for lab {lab_id}: {e}")

        return removed

    async def status(
        self,
        lab_id: str,
        workspace: Path,
    ) -> StatusResult:
        """Get status of all nodes in a lab."""
        nodes: list[NodeInfo] = []

        try:
            # Find containers by label - run in thread to avoid blocking
            containers = await asyncio.to_thread(
                self.docker.containers.list,
                all=True,
                filters={"label": f"{LABEL_LAB_ID}={lab_id}"},
            )

            # Also find by prefix (fallback)
            prefix = self._lab_prefix(lab_id)
            prefix_containers = await asyncio.to_thread(
                self.docker.containers.list,
                all=True,
                filters={"name": prefix},
            )

            all_containers = {c.id: c for c in containers}
            for c in prefix_containers:
                all_containers[c.id] = c

            for container in all_containers.values():
                node = self._node_from_container(container)
                if node:
                    nodes.append(node)

            return StatusResult(
                lab_exists=len(nodes) > 0,
                nodes=nodes,
            )

        except Exception as e:
            return StatusResult(
                lab_exists=False,
                error=str(e),
            )

    async def start_node(
        self,
        lab_id: str,
        node_name: str,
        workspace: Path,
    ) -> NodeActionResult:
        """Start a specific node."""
        container_name = self._container_name(lab_id, node_name)

        try:
            container = await asyncio.to_thread(self.docker.containers.get, container_name)
            await asyncio.to_thread(container.start)
            await asyncio.sleep(1)
            await asyncio.to_thread(container.reload)

            return NodeActionResult(
                success=True,
                node_name=node_name,
                new_status=self._get_container_status(container),
                stdout=f"Started container {container_name}",
            )

        except NotFound:
            return NodeActionResult(
                success=False,
                node_name=node_name,
                error=f"Container {container_name} not found",
            )
        except APIError as e:
            return NodeActionResult(
                success=False,
                node_name=node_name,
                error=f"Docker API error: {e}",
            )

    async def stop_node(
        self,
        lab_id: str,
        node_name: str,
        workspace: Path,
    ) -> NodeActionResult:
        """Stop a specific node."""
        container_name = self._container_name(lab_id, node_name)

        try:
            container = await asyncio.to_thread(self.docker.containers.get, container_name)
            await asyncio.to_thread(container.stop, timeout=settings.container_stop_timeout)
            await asyncio.to_thread(container.reload)

            return NodeActionResult(
                success=True,
                node_name=node_name,
                new_status=self._get_container_status(container),
                stdout=f"Stopped container {container_name}",
            )

        except NotFound:
            return NodeActionResult(
                success=False,
                node_name=node_name,
                error=f"Container {container_name} not found",
            )
        except APIError as e:
            return NodeActionResult(
                success=False,
                node_name=node_name,
                error=f"Docker API error: {e}",
            )

    async def get_console_command(
        self,
        lab_id: str,
        node_name: str,
        workspace: Path,
    ) -> list[str] | None:
        """Get docker exec command for console access."""
        container_name = self._container_name(lab_id, node_name)

        try:
            container = await asyncio.to_thread(self.docker.containers.get, container_name)
            if container.status != "running":
                return None

            # Get vendor-specific shell
            kind = container.labels.get(LABEL_NODE_KIND, "linux")
            shell = get_console_shell(kind)

            return ["docker", "exec", "-it", container_name, shell]

        except NotFound:
            return None
        except Exception:
            return None

    def get_container_name(self, lab_id: str, node_name: str) -> str:
        """Get the Docker container name for a node."""
        return self._container_name(lab_id, node_name)

    async def _extract_all_ceos_configs(
        self,
        lab_id: str,
        workspace: Path,
    ) -> list[tuple[str, str]]:
        """Extract running-config from all cEOS containers in a lab.

        Returns list of (node_name, config_content) tuples.
        Also saves configs to workspace/configs/{node}/startup-config.
        """
        extracted = []
        prefix = self._lab_prefix(lab_id)

        try:
            containers = await asyncio.to_thread(
                self.docker.containers.list,
                filters={
                    "name": prefix,
                    "label": LABEL_PROVIDER + "=" + self.name,
                },
            )

            for container in containers:
                labels = container.labels or {}
                node_name = labels.get(LABEL_NODE_NAME)
                kind = labels.get(LABEL_NODE_KIND, "")

                # Only extract from cEOS containers
                if kind != "ceos" or not node_name:
                    continue

                log_name = _log_name_from_labels(labels)

                if container.status != "running":
                    logger.warning(f"Skipping {log_name}: container not running")
                    continue

                try:
                    # Execute 'show running-config' via FastCli with privilege level 15
                    result = await asyncio.to_thread(
                        container.exec_run,
                        ["FastCli", "-p", "15", "-c", "show running-config"],
                        demux=True,
                    )
                    stdout, stderr = result.output

                    if result.exit_code != 0:
                        logger.warning(
                            f"Failed to extract config from {log_name}: "
                            f"exit={result.exit_code}, stderr={stderr}"
                        )
                        continue

                    config_content = stdout.decode("utf-8") if stdout else ""
                    if not config_content.strip():
                        logger.warning(f"Empty config from {log_name}")
                        continue

                    # Save to workspace/configs/{node}/startup-config
                    config_dir = workspace / "configs" / node_name
                    config_dir.mkdir(parents=True, exist_ok=True)
                    config_path = config_dir / "startup-config"
                    config_path.write_text(config_content)

                    extracted.append((node_name, config_content))
                    logger.info(f"Extracted config from {log_name}")

                except Exception as e:
                    logger.error(f"Error extracting config from {log_name}: {e}")

        except Exception as e:
            logger.error(f"Error during config extraction for lab {lab_id}: {e}")

        return extracted

    async def discover_labs(self) -> dict[str, list[NodeInfo]]:
        """Discover all running labs managed by this provider.

        Returns dict mapping lab_id -> list of NodeInfo.
        """
        discovered: dict[str, list[NodeInfo]] = {}

        try:
            containers = await asyncio.to_thread(
                self.docker.containers.list,
                all=True,
                filters={"label": LABEL_PROVIDER + "=" + self.name},
            )

            for container in containers:
                labels = container.labels or {}
                lab_id = labels.get(LABEL_LAB_ID)
                if not lab_id:
                    continue

                node = self._node_from_container(container)
                if node:
                    if lab_id not in discovered:
                        discovered[lab_id] = []
                    discovered[lab_id].append(node)

            logger.info(f"Discovered {len(discovered)} labs with DockerProvider")

        except Exception as e:
            logger.error(f"Error discovering labs: {e}")

        return discovered

    async def cleanup_orphan_containers(self, valid_lab_ids: set[str]) -> list[str]:
        """Remove containers for labs that no longer exist.

        Args:
            valid_lab_ids: Set of lab IDs that are known to be valid.

        Returns:
            List of container names that were removed.
        """
        removed = []
        try:
            containers = await asyncio.to_thread(
                self.docker.containers.list,
                all=True,
                filters={"label": LABEL_PROVIDER + "=" + self.name},
            )
            for container in containers:
                lab_id = container.labels.get(LABEL_LAB_ID, "")
                if not lab_id:
                    continue

                # Check if this lab_id is in the valid set
                # Handle both exact matches and prefix matches (for truncated IDs)
                is_orphan = lab_id not in valid_lab_ids
                if is_orphan:
                    # Also check for prefix matches (lab IDs may be truncated)
                    is_orphan = not any(
                        vid.startswith(lab_id) or lab_id.startswith(vid[:20])
                        for vid in valid_lab_ids
                    )

                if is_orphan:
                    logger.info(f"Removing orphan container {container.name} (lab: {lab_id})")
                    await asyncio.to_thread(container.remove, force=True)
                    removed.append(container.name)
                    await self.local_network.cleanup_lab(lab_id)

        except Exception as e:
            logger.error(f"Error during orphan cleanup: {e}")

        return removed
