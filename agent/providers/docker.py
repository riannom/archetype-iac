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
from docker.types import Mount

from agent.config import settings
from agent.network.local import LocalNetworkManager, get_local_manager
from agent.network.ovs import OVSNetworkManager, get_ovs_manager
from agent.providers.base import (
    DeployResult,
    DestroyResult,
    NodeActionResult,
    NodeInfo,
    NodeStatus,
    Provider,
    StatusResult,
)
from agent.vendors import (
    VendorConfig,
    get_config_by_device,
    get_container_config,
    get_console_shell,
)


logger = logging.getLogger(__name__)


# Container name prefix for Archetype-managed containers
CONTAINER_PREFIX = "archetype"

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
        """Lazy-initialize Docker client."""
        if self._docker is None:
            self._docker = docker.from_env()
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
    ) -> dict[str, Any]:
        """Build Docker container configuration for a node.

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
        config: dict[str, Any] = {
            "image": runtime_config.image,
            "name": self._container_name(lab_id, node.name),
            "hostname": runtime_config.hostname,
            "environment": env,
            "labels": labels,
            "network_mode": "none",  # We manage networking separately
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
        if runtime_config.entrypoint:
            # Docker SDK expects entrypoint as a list
            if isinstance(runtime_config.entrypoint, str):
                config["entrypoint"] = [runtime_config.entrypoint]
            else:
                config["entrypoint"] = runtime_config.entrypoint

        if runtime_config.cmd:
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
                    minimal_config = f"""! Minimal cEOS startup config
hostname {node_name}
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

        for node_name, node in topology.nodes.items():
            container_name = self._container_name(lab_id, node_name)
            log_name = node.log_name()

            # Check if container already exists
            try:
                existing = self.docker.containers.get(container_name)
                if existing.status == "running":
                    logger.info(f"Container {log_name} already running")
                    containers[node_name] = existing
                    continue
                else:
                    logger.info(f"Removing stopped container {log_name}")
                    existing.remove(force=True)
            except NotFound:
                pass

            # Build container config
            config = self._create_container_config(node, lab_id, workspace)

            # Create container
            logger.info(f"Creating container {log_name} with image {config['image']}")
            container = self.docker.containers.create(**config)
            containers[node_name] = container

        return containers

    async def _start_containers(
        self,
        containers: dict[str, Any],
        topology: ParsedTopology,
        lab_id: str,
    ) -> list[str]:
        """Start all containers and provision interfaces as needed.

        When OVS is enabled, provisions real veth pairs via OVS for hot-plug support.
        When OVS is disabled, falls back to dummy interfaces for devices that need them.

        Returns list of node names that failed to start.
        """
        failed = []

        # Initialize OVS if enabled
        if self.use_ovs:
            try:
                await self.ovs_manager.initialize()
            except Exception as e:
                logger.warning(f"OVS initialization failed, falling back to legacy networking: {e}")

        for node_name, container in containers.items():
            try:
                log_name = topology.log_name(node_name)
                if container.status != "running":
                    container.start()
                    logger.info(f"Started container {log_name}")

                # Provision interfaces based on networking mode
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

    async def _create_links(
        self,
        topology: ParsedTopology,
        lab_id: str,
    ) -> int:
        """Create links between containers.

        When OVS is enabled, uses hot-connect (VLAN tag matching).
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
                if self.use_ovs and self.ovs_manager._initialized:
                    # Use OVS hot-connect (VLAN tag matching)
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

                # Check readiness
                try:
                    container.reload()
                    if container.status != "running":
                        all_ready = False
                        continue

                    if config.readiness_probe == "log_pattern":
                        # Check logs for pattern
                        logs = container.logs(tail=100).decode(errors="replace")
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

    async def deploy(
        self,
        lab_id: str,
        topology_yaml: str,
        workspace: Path,
        agent_id: str | None = None,
    ) -> DeployResult:
        """Deploy a topology using Docker SDK.

        Steps:
        1. Parse topology
        2. Validate images exist
        3. Create required directories
        4. Create containers (network mode: none)
        5. Start containers
        6. Create local links (veth pairs)
        7. Wait for readiness
        """
        workspace.mkdir(parents=True, exist_ok=True)

        # Parse topology
        topology = self._parse_topology(topology_yaml, lab_id)
        if not topology.nodes:
            return DeployResult(
                success=False,
                error="No nodes found in topology",
            )

        logger.info(f"Deploying lab {lab_id} with {len(topology.nodes)} nodes")

        # Validate images
        missing_images = self._validate_images(topology)
        if missing_images:
            error_lines = ["Missing Docker images:"]
            for node_name, image in missing_images:
                log_name = topology.log_name(node_name)
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
        await self._ensure_directories(topology, workspace)

        # Create management network
        try:
            await self.local_network.create_management_network(lab_id)
        except Exception as e:
            logger.warning(f"Failed to create management network: {e}")

        # Create containers
        try:
            containers = await self._create_containers(topology, lab_id, workspace)
        except Exception as e:
            logger.error(f"Failed to create containers: {e}")
            return DeployResult(
                success=False,
                error=f"Failed to create containers: {e}",
            )

        # Start containers
        failed_starts = await self._start_containers(containers, topology, lab_id)
        if failed_starts:
            failed_log_names = [topology.log_name(n) for n in failed_starts]
            logger.warning(f"Some containers failed to start: {failed_log_names}")

        # Create local links
        links_created = await self._create_links(topology, lab_id)
        logger.info(f"Created {links_created} local links")

        # Wait for readiness
        ready_status = await self._wait_for_readiness(
            topology, lab_id, containers, timeout=settings.deploy_timeout
        )
        not_ready = [name for name, ready in ready_status.items() if not ready]
        if not_ready:
            not_ready_log_names = [topology.log_name(n) for n in not_ready]
            logger.warning(f"Some nodes not ready after timeout: {not_ready_log_names}")

        # Get final status
        status_result = await self.status(lab_id, workspace)

        stdout_lines = [
            f"Deployed {len(containers)} containers",
            f"Created {links_created} links",
        ]
        if not_ready:
            not_ready_log_names = [topology.log_name(n) for n in not_ready]
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
        errors = []

        try:
            # Find all containers for this lab
            containers = self.docker.containers.list(
                all=True,
                filters={"label": f"{LABEL_LAB_ID}={lab_id}"},
            )

            # Also find by prefix (fallback)
            prefix_containers = self.docker.containers.list(
                all=True,
                filters={"name": prefix},
            )
            all_containers = {c.id: c for c in containers}
            for c in prefix_containers:
                all_containers[c.id] = c

            # Remove containers
            for container in all_containers.values():
                try:
                    container.remove(force=True)
                    removed += 1
                    logger.info(f"Removed container {container.name}")
                except Exception as e:
                    errors.append(f"Failed to remove {container.name}: {e}")

            # Clean up local networking
            cleanup_result = await self.local_network.cleanup_lab(lab_id)
            logger.info(f"Local network cleanup: {cleanup_result}")

            # Clean up OVS networking if enabled
            if self.use_ovs and self.ovs_manager._initialized:
                ovs_cleanup_result = await self.ovs_manager.cleanup_lab(lab_id)
                logger.info(f"OVS network cleanup: {ovs_cleanup_result}")

        except Exception as e:
            errors.append(f"Error during destroy: {e}")

        success = len(errors) == 0
        return DestroyResult(
            success=success,
            stdout=f"Removed {removed} containers",
            stderr="\n".join(errors) if errors else "",
            error=errors[0] if errors else None,
        )

    async def status(
        self,
        lab_id: str,
        workspace: Path,
    ) -> StatusResult:
        """Get status of all nodes in a lab."""
        nodes: list[NodeInfo] = []

        try:
            # Find containers by label
            containers = self.docker.containers.list(
                all=True,
                filters={"label": f"{LABEL_LAB_ID}={lab_id}"},
            )

            # Also find by prefix (fallback)
            prefix = self._lab_prefix(lab_id)
            prefix_containers = self.docker.containers.list(
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
            container = self.docker.containers.get(container_name)
            container.start()
            await asyncio.sleep(1)
            container.reload()

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
            container = self.docker.containers.get(container_name)
            container.stop(timeout=settings.container_stop_timeout)
            container.reload()

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
            container = self.docker.containers.get(container_name)
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
            containers = self.docker.containers.list(
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
                    result = container.exec_run(
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
            containers = self.docker.containers.list(
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
            containers = self.docker.containers.list(
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
                    container.remove(force=True)
                    removed.append(container.name)
                    await self.local_network.cleanup_lab(lab_id)

        except Exception as e:
            logger.error(f"Error during orphan cleanup: {e}")

        return removed
