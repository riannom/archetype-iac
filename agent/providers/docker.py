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
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import docker
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
    get_config_extraction_settings,
    get_console_credentials,
    get_console_method,
    get_container_config,
    get_console_shell,
    is_ceos_kind,
)


logger = logging.getLogger(__name__)


# Container name prefix for Archetype-managed containers
CONTAINER_PREFIX = "archetype"

# VLAN range for container interfaces (same as OVS plugin)
VLAN_RANGE_START = 100
VLAN_RANGE_END = 4000

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

normalize_eth_names() {
    if [ "$REQUIRED_INTFS_NUM" -le 0 ]; then
        return 0
    fi

    missing=0
    i=1
    while [ "$i" -le "$REQUIRED_INTFS_NUM" ]; do
        if [ ! -e "/sys/class/net/eth${i}" ]; then
            missing=1
            break
        fi
        i=$((i + 1))
    done

    if [ "$missing" -eq 0 ]; then
        return 0
    fi

    echo "if-wait: Normalizing eth interface names before init"

    tmpfile="/tmp/if-wait-eths"
    ip -o link show | awk -F': ' '/: eth[0-9]+/ {name=$2; sub(/@.*/,"",name); print $1, name}' | sort -n > "$tmpfile"

    # Rename all eth* to unique temp names to avoid collisions
    while read -r idx name; do
        ip link set "$name" down 2>/dev/null || true
        ip link set "$name" name "tmp_ceos_${idx}" 2>/dev/null || true
    done < "$tmpfile"

    # Rename temp interfaces to eth1..ethN in ifindex order
    i=1
    while read -r idx _; do
        if [ "$i" -le "$REQUIRED_INTFS_NUM" ]; then
            ip link set "tmp_ceos_${idx}" name "eth${i}" 2>/dev/null || true
            ip link set "eth${i}" up 2>/dev/null || true
            i=$((i + 1))
        fi
    done < "$tmpfile"
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

    normalize_eth_names
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
    interface_count: int | None = None  # UI maxPorts (or higher if links demand it)
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
        # VLAN tracking for persistence (matches LibvirtProvider pattern)
        # {lab_id: {node_name: [vlan_tags]}}
        self._vlan_allocations: dict[str, dict[str, list[int]]] = {}
        # Next VLAN to allocate per lab
        self._next_vlan: dict[str, int] = {}

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

    # =========================================================================
    # VLAN Persistence (matches LibvirtProvider pattern for feature parity)
    # =========================================================================

    def _vlans_dir(self, workspace: Path) -> Path:
        """Get directory for VLAN allocation files."""
        vlans = workspace / "vlans"
        vlans.mkdir(parents=True, exist_ok=True)
        return vlans

    def _save_vlan_allocations(self, lab_id: str, workspace: Path) -> None:
        """Persist VLAN allocations to file for recovery after agent restart.

        Saves the current VLAN allocations for a lab to a JSON file.
        This enables recovery of network state when the agent restarts
        or when a lab is redeployed.

        Args:
            lab_id: Lab identifier
            workspace: Lab workspace path
        """
        allocations = self._vlan_allocations.get(lab_id, {})
        next_vlan = self._next_vlan.get(lab_id, VLAN_RANGE_START)

        vlan_data = {
            "allocations": allocations,
            "next_vlan": next_vlan,
        }

        vlans_dir = self._vlans_dir(workspace)
        vlan_file = vlans_dir / f"{lab_id}.json"

        try:
            with open(vlan_file, "w") as f:
                json.dump(vlan_data, f, indent=2)
            logger.debug(f"Saved VLAN allocations for lab {lab_id} to {vlan_file}")
        except Exception as e:
            logger.warning(f"Failed to save VLAN allocations for lab {lab_id}: {e}")

    def _load_vlan_allocations(self, lab_id: str, workspace: Path) -> bool:
        """Load VLAN allocations from file.

        Restores VLAN allocation state from a previously saved JSON file.
        Used during stale network recovery to restore state after agent restart.

        Args:
            lab_id: Lab identifier
            workspace: Lab workspace path

        Returns:
            True if allocations were loaded, False if file doesn't exist or load failed
        """
        vlans_dir = self._vlans_dir(workspace)
        vlan_file = vlans_dir / f"{lab_id}.json"

        if not vlan_file.exists():
            return False

        try:
            with open(vlan_file) as f:
                vlan_data = json.load(f)

            allocations = vlan_data.get("allocations", {})
            next_vlan = vlan_data.get("next_vlan", VLAN_RANGE_START)

            self._vlan_allocations[lab_id] = allocations
            self._next_vlan[lab_id] = next_vlan

            logger.info(
                f"Loaded VLAN allocations for lab {lab_id}: "
                f"{len(allocations)} nodes, next_vlan={next_vlan}"
            )
            return True

        except Exception as e:
            logger.warning(f"Failed to load VLAN allocations for lab {lab_id}: {e}")
            return False

    def _remove_vlan_file(self, lab_id: str, workspace: Path) -> None:
        """Remove VLAN allocation file for a lab.

        Called during destroy to clean up the VLAN file when a lab is removed.

        Args:
            lab_id: Lab identifier
            workspace: Lab workspace path
        """
        vlans_dir = self._vlans_dir(workspace)
        vlan_file = vlans_dir / f"{lab_id}.json"

        if vlan_file.exists():
            try:
                vlan_file.unlink()
                logger.debug(f"Removed VLAN file for lab {lab_id}")
            except Exception as e:
                logger.warning(f"Failed to remove VLAN file for lab {lab_id}: {e}")

    def get_node_vlans(self, lab_id: str, node_name: str) -> list[int]:
        """Get the VLAN tags allocated to a container's interfaces.

        Args:
            lab_id: Lab identifier
            node_name: Node name

        Returns:
            List of VLAN tags, or empty list if not found
        """
        return self._vlan_allocations.get(lab_id, {}).get(node_name, [])

    async def _recover_stale_network(
        self,
        lab_id: str,
        workspace: Path,
    ) -> dict[str, list[int]]:
        """Recover network state for a lab being redeployed.

        This method attempts to restore VLAN allocations from a previous
        deployment. When the agent restarts or a lab is redeployed, the
        in-memory VLAN allocations are lost. This method:

        1. Loads VLAN allocations from the persisted JSON file
        2. Validates that the containers still exist via Docker API
        3. Returns the recovered allocations for reuse

        The recovered allocations can be used to avoid reallocating VLANs
        for nodes that already have working network connectivity.

        Args:
            lab_id: Lab identifier
            workspace: Lab workspace path

        Returns:
            Dict mapping node_name -> list of VLAN tags for recovered nodes.
            Empty dict if no recovery was possible.
        """
        recovered: dict[str, list[int]] = {}

        # Try to load existing VLAN allocations
        if not self._load_vlan_allocations(lab_id, workspace):
            logger.debug(f"No VLAN allocations to recover for lab {lab_id}")
            return recovered

        allocations = self._vlan_allocations.get(lab_id, {})
        if not allocations:
            return recovered

        # Check which allocations have valid containers still running
        try:
            # Get all containers for this lab
            containers = await asyncio.to_thread(
                self.docker.containers.list,
                all=True,
                filters={"label": f"{LABEL_LAB_ID}={lab_id}"},
            )

            existing_nodes = set()
            for container in containers:
                labels = container.labels or {}
                node_name = labels.get(LABEL_NODE_NAME)
                if node_name:
                    existing_nodes.add(node_name)

            # Keep allocations for nodes that still have containers
            for node_name, vlans in allocations.items():
                if node_name in existing_nodes:
                    recovered[node_name] = vlans
                    logger.info(
                        f"Recovered VLAN allocation for {node_name}: {vlans}"
                    )
                else:
                    logger.debug(
                        f"Discarding stale VLAN allocation for {node_name} "
                        "(container no longer exists)"
                    )

            # Update in-memory state to only keep valid allocations
            self._vlan_allocations[lab_id] = recovered

            if recovered:
                logger.info(
                    f"Recovered network state for lab {lab_id}: "
                    f"{len(recovered)} nodes with valid VLAN allocations"
                )
                # Re-save the cleaned allocations
                self._save_vlan_allocations(lab_id, workspace)

        except Exception as e:
            logger.warning(f"Error during stale network recovery for lab {lab_id}: {e}")
            return {}

        return recovered

    async def _capture_container_vlans(
        self,
        lab_id: str,
        topology: ParsedTopology,
        workspace: Path,
    ) -> None:
        """Capture VLAN allocations from OVS for all containers in the topology.

        Queries OVS for the current VLAN tag of each container interface
        and updates the in-memory VLAN tracking. This enables persistence
        across agent restarts.

        Args:
            lab_id: Lab identifier
            topology: Parsed topology with node information
            workspace: Lab workspace path for saving allocations
        """
        if lab_id not in self._vlan_allocations:
            self._vlan_allocations[lab_id] = {}
        if lab_id not in self._next_vlan:
            self._next_vlan[lab_id] = VLAN_RANGE_START

        max_vlan_seen = VLAN_RANGE_START

        for node_name, node in topology.nodes.items():
            container_name = self._container_name(lab_id, node_name)
            vlans: list[int] = []

            try:
                container = await asyncio.to_thread(
                    self.docker.containers.get, container_name
                )
                pid = container.attrs["State"]["Pid"]

                # Get list of all interfaces in the container (except lo)
                proc = await asyncio.create_subprocess_exec(
                    "nsenter", "-t", str(pid), "-n",
                    "ls", "/sys/class/net/",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await proc.communicate()
                if proc.returncode != 0:
                    continue

                interfaces = [
                    iface.strip()
                    for iface in stdout.decode().split()
                    if iface.strip() and iface.strip() != "lo"
                ]

                for interface in interfaces:
                    vlan = await self._get_interface_vlan(pid, interface)
                    if vlan is not None:
                        vlans.append(vlan)
                        max_vlan_seen = max(max_vlan_seen, vlan)

                if vlans:
                    self._vlan_allocations[lab_id][node_name] = vlans
                    logger.debug(f"Captured VLANs for {node_name}: {vlans}")

            except NotFound:
                logger.debug(f"Container {container_name} not found, skipping VLAN capture")
            except Exception as e:
                logger.warning(f"Error capturing VLANs for {node_name}: {e}")

        # Update next_vlan to be above any captured VLANs
        self._next_vlan[lab_id] = max(self._next_vlan[lab_id], max_vlan_seen + 1)

        # Persist the captured allocations
        if self._vlan_allocations.get(lab_id):
            self._save_vlan_allocations(lab_id, workspace)
            logger.info(
                f"Captured and saved VLAN allocations for lab {lab_id}: "
                f"{len(self._vlan_allocations[lab_id])} nodes"
            )

    async def _get_interface_vlan(self, pid: int, interface: str) -> int | None:
        """Get the VLAN tag for a container interface from OVS.

        Args:
            pid: Container PID
            interface: Interface name inside the container

        Returns:
            VLAN tag if found, None otherwise
        """
        try:
            # Get the peer interface index from inside container
            proc = await asyncio.create_subprocess_exec(
                "nsenter", "-t", str(pid), "-n", "-m",
                "cat", f"/sys/class/net/{interface}/iflink",
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

            host_veth = None
            for line in stdout.decode().split("\n"):
                if line.startswith(f"{peer_idx}:"):
                    parts = line.split(":")
                    if len(parts) >= 2:
                        host_veth = parts[1].strip().split("@")[0]
                        break

            if not host_veth:
                return None

            # Get VLAN tag from OVS
            proc = await asyncio.create_subprocess_exec(
                "ovs-vsctl", "get", "port", host_veth, "tag",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                return None

            vlan_str = stdout.decode().strip().strip("[]")
            if vlan_str:
                return int(vlan_str)
            return None

        except Exception:
            return None

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
            # Don't auto-restart — agent manages lifecycle via deploy/destroy.
            # "unless-stopped" races the OVS plugin socket on host reboot.
            "restart_policy": {"Name": "no"},
        }

        # Capabilities
        if runtime_config.capabilities:
            config["cap_add"] = runtime_config.capabilities

        # Privileged mode
        if runtime_config.privileged:
            config["privileged"] = True

        # Use host cgroup namespace to enable stats collection on cgroups v2
        # Without this, Docker can't read cgroup stats for containers that run
        # their own init system (like cEOS) due to cgroup namespace isolation
        config["cgroupns"] = "host"

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
        # before starting init - this prevents the platform detection race condition.
        # interface_count comes from the controller's UI-configured port count,
        # ensuring the device sees all ports at boot (not just linked ones).
        if is_ceos_kind(node.kind) and interface_count > 0:
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

    def _setup_ceos_directories(
        self,
        node_name: str,
        node: TopologyNode,
        workspace: Path,
    ) -> None:
        """Set up cEOS directories and config files.

        This is a blocking operation meant to run in asyncio.to_thread().
        """
        import shutil

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

    async def _ensure_directories(
        self,
        topology: ParsedTopology,
        workspace: Path,
    ) -> None:
        """Create required directories for nodes (e.g., cEOS flash).

        Runs blocking file I/O in thread pool to avoid blocking event loop.
        """
        for node_name, node in topology.nodes.items():
            if is_ceos_kind(node.kind):
                # Run blocking file operations in thread pool
                await asyncio.to_thread(
                    self._setup_ceos_directories,
                    node_name,
                    node,
                    workspace,
                )

    def _calculate_required_interfaces(self, topology: ParsedTopology) -> int:
        """Calculate the maximum interface index needed for pre-provisioning.

        Strategy:
        - The controller sends per-node interface_count based on the UI's
          configured maxPorts (vendor defaults or overrides). We use those
          counts to pre-provision interfaces before boot for devices like cEOS.
          If a link references a higher interface, we raise the count so the
          device still boots with that interface present.
        - We size the lab's OVS networks to the maximum interface_count across
          all nodes, and also consider any explicitly referenced link interfaces.
        - Add a small buffer for flexibility when creating new links.

        Args:
            topology: Parsed topology with nodes and links

        Returns:
            Number of interfaces to create (max index found + buffer)
        """
        max_index = 0

        # Respect explicit interface counts (e.g., cross-host links not in topology.links)
        for node in topology.nodes.values():
            if node.interface_count and node.interface_count > max_index:
                max_index = node.interface_count

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
            Max interface index required for this node
        """
        node = topology.nodes.get(node_name)
        if node and node.interface_count:
            return node.interface_count

        max_index = 0

        for link in topology.links:
            for endpoint in link.endpoints:
                if ":" in endpoint:
                    ep_node, interface = endpoint.split(":", 1)
                    if ep_node == node_name:
                        # Extract interface number
                        match = re.search(r"(\d+)$", interface)
                        if match:
                            max_index = max(max_index, int(match.group(1)))

        return max_index

    async def _create_lab_networks(
        self,
        lab_id: str,
        max_interfaces: int = 8,
    ) -> dict[str, str]:
        """Create Docker networks for lab interfaces via OVS plugin.

        Creates one network per interface (eth1, eth2, ..., ethN).
        All networks share the same OVS bridge (arch-ovs).

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
                (from UI-configured maxPorts or vendor defaults, potentially
                raised by explicit link references).
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
                is_ceos = node and is_ceos_kind(node.kind)

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
                    # Fix interface names - Docker may have assigned them incorrectly
                    # due to network attachment ordering
                    try:
                        await self._fix_interface_names(container.name, lab_id)
                    except Exception as e:
                        logger.warning(f"Failed to fix interface names for {log_name}: {e}")
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

    async def _fix_interface_names(
        self,
        container_name: str,
        lab_id: str,
    ) -> dict[str, Any]:
        """Fix container interface names after Docker start.

        Docker assigns interface names based on network attachment order, not
        the intended names from the OVS plugin. This method:
        1. Detects when OVS endpoints are missing (e.g., after agent restart)
        2. Reconnects networks to force endpoint recreation
        3. Renames interfaces to match intended names (eth1, eth2, etc.)

        Args:
            container_name: Docker container name
            lab_id: Lab identifier

        Returns:
            Dict with counts of fixed interfaces and any errors.
        """
        result = {"fixed": 0, "already_correct": 0, "reconnected": 0, "errors": []}

        plugin = get_docker_ovs_plugin()
        if not plugin:
            return result

        try:
            container = await asyncio.to_thread(self.docker.containers.get, container_name)
            pid = container.attrs["State"]["Pid"]
            if not pid:
                result["errors"].append("Container not running")
                return result
            node_kind = container.labels.get(LABEL_NODE_KIND)

            # Get container's network attachments
            networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})
        except Exception as e:
            result["errors"].append(f"Failed to get container: {e}")
            return result

        # Build mapping of network_id -> intended interface name from plugin
        network_to_interface = {}
        for network in plugin.networks.values():
            if network.lab_id == lab_id and network.interface_name != "eth0":
                network_to_interface[network.network_id] = network.interface_name

        if not network_to_interface:
            logger.debug(f"No OVS networks found for lab {lab_id}")
            return result

        # Get OVS ports for the shared bridge
        bridge_name = settings.ovs_bridge_name
        proc = await asyncio.create_subprocess_exec(
            "ovs-vsctl", "list-ports", bridge_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        ovs_ports = stdout.decode().strip().split("\n") if proc.returncode == 0 else []
        ovs_ports = [p for p in ovs_ports if p]  # Filter empty

        # Check which of this container's OVS network attachments are missing from OVS
        # We need to reconnect networks whose endpoint ports don't exist
        networks_to_reconnect = []
        for network_name, network_info in networks.items():
            network_id = network_info.get("NetworkID", "")
            if network_id not in network_to_interface:
                continue  # Skip non-OVS networks

            endpoint_id = network_info.get("EndpointID", "")
            if not endpoint_id:
                continue

            # Check if OVS port exists for this endpoint (format: vh{endpoint_id[:5]}...)
            port_prefix = f"vh{endpoint_id[:5]}"
            port_exists = any(p.startswith(port_prefix) for p in ovs_ports)
            if not port_exists:
                networks_to_reconnect.append(network_name)

        # Reconnect networks that are missing OVS ports
        if networks_to_reconnect:
            logger.info(
                f"Missing OVS ports for {container_name}, reconnecting "
                f"{len(networks_to_reconnect)} networks..."
            )
            for network_name in networks_to_reconnect:
                try:
                    # Disconnect
                    docker_network = await asyncio.to_thread(self.docker.networks.get, network_name)
                    await asyncio.to_thread(docker_network.disconnect, container_name)
                    # Reconnect
                    await asyncio.to_thread(docker_network.connect, container_name)
                    result["reconnected"] += 1
                    logger.info(f"Reconnected {container_name} to {network_name}")
                except Exception as e:
                    logger.warning(f"Failed to reconnect {network_name}: {e}")
                    result["errors"].append(f"Failed to reconnect {network_name}: {e}")

            # Refresh container info after reconnection
            if result["reconnected"] > 0:
                await asyncio.sleep(1)  # Give Docker time to process
                container = await asyncio.to_thread(self.docker.containers.get, container_name)
                pid = container.attrs["State"]["Pid"]
                networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})

        # For each network attachment, check if the interface name is correct
        for network_name, network_info in networks.items():
            try:
                network_id = network_info.get("NetworkID", "")
                if network_id not in network_to_interface:
                    continue  # Not an OVS network

                intended_name = network_to_interface[network_id]
                endpoint_id = network_info.get("EndpointID", "")
                if not endpoint_id:
                    continue

                # Find the veth for this endpoint by checking OVS ports
                # Endpoint IDs from Docker are used to generate veth names
                # Format: vh{endpoint_id[:5]}{random}
                host_veth = None
                net_bridge = plugin.networks[network_id].bridge_name

                # List ports on the OVS bridge
                proc = await asyncio.create_subprocess_exec(
                    "ovs-vsctl", "list-ports", net_bridge,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await proc.communicate()
                if proc.returncode == 0:
                    ports = stdout.decode().strip().split("\n")
                    # Find port that matches endpoint prefix
                    for port in ports:
                        if port.startswith(f"vh{endpoint_id[:5]}"):
                            host_veth = port
                            break

                if not host_veth:
                    # Try finding by checking all ports' peer indexes
                    continue

                # Get the host veth's peer ifindex (the interface inside the container)
                proc = await asyncio.create_subprocess_exec(
                    "cat", f"/sys/class/net/{host_veth}/iflink",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await proc.communicate()
                if proc.returncode != 0:
                    continue

                peer_ifindex = stdout.decode().strip()

                # Find what name this interface has inside the container
                actual_name = await self._find_interface_by_ifindex(pid, peer_ifindex)
                if not actual_name:
                    continue

                if actual_name == intended_name:
                    result["already_correct"] += 1
                    continue

                # Rename interface inside container
                await self._rename_container_interface(
                    pid, actual_name, intended_name, container_name, result
                )

            except Exception as e:
                result["errors"].append(f"Error processing network {network_name}: {e}")

        # Re-apply vendor post-boot commands if needed (e.g., cEOS iptables)
        if node_kind:
            await self._run_post_boot_commands(container_name, node_kind)

        if result["fixed"] > 0:
            logger.info(
                f"Fixed {result['fixed']} interface names in {container_name}"
            )

        return result

    async def _find_interface_by_ifindex(self, pid: int, ifindex: str) -> str | None:
        """Find interface name in container by its ifindex.

        Uses `ip link show` which is network-namespace aware, unlike
        /sys/class/net which is mount-namespace based.
        """
        proc = await asyncio.create_subprocess_exec(
            "nsenter", "-t", str(pid), "-n",
            "ip", "-o", "link", "show",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return None

        # Parse output like: "4207: eth6@if4208: <BROADCAST,..."
        for line in stdout.decode().strip().split("\n"):
            parts = line.split(":", 2)
            if len(parts) >= 2:
                idx = parts[0].strip()
                name = parts[1].strip().split("@")[0]  # Remove @ifXXX suffix
                if idx == ifindex:
                    return name
        return None

    async def _rename_container_interface(
        self,
        pid: int,
        actual_name: str,
        intended_name: str,
        container_name: str,
        result: dict[str, Any],
    ) -> None:
        """Rename an interface inside a container."""
        logger.info(f"Renaming {actual_name} -> {intended_name} in {container_name}")

        # First bring interface down
        proc = await asyncio.create_subprocess_exec(
            "nsenter", "-t", str(pid), "-n",
            "ip", "link", "set", actual_name, "down",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

        # Rename
        proc = await asyncio.create_subprocess_exec(
            "nsenter", "-t", str(pid), "-n",
            "ip", "link", "set", actual_name, "name", intended_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            error_msg = stderr.decode().strip()
            # Interface may already exist with that name (from docker_gwbridge)
            if "File exists" in error_msg:
                # Need to rename the conflicting interface first
                temp_base = f"_old_{intended_name}"
                temp_name = temp_base
                # Ensure unique temp name
                for i in range(1, 6):
                    check = await asyncio.create_subprocess_exec(
                        "nsenter", "-t", str(pid), "-n",
                        "ip", "link", "show", temp_name,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    await check.communicate()
                    if check.returncode != 0:
                        break
                    temp_name = f"{temp_base}_{i}"

                await asyncio.create_subprocess_exec(
                    "nsenter", "-t", str(pid), "-n",
                    "ip", "link", "set", intended_name, "down",
                )
                await asyncio.create_subprocess_exec(
                    "nsenter", "-t", str(pid), "-n",
                    "ip", "link", "set", intended_name, "name", temp_name,
                )
                # Now try the rename again
                proc = await asyncio.create_subprocess_exec(
                    "nsenter", "-t", str(pid), "-n",
                    "ip", "link", "set", actual_name, "name", intended_name,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, stderr = await proc.communicate()
                if proc.returncode != 0:
                    result["errors"].append(
                        f"Failed to rename {actual_name} -> {intended_name}: {stderr.decode()}"
                    )
                    return
            else:
                result["errors"].append(
                    f"Failed to rename {actual_name} -> {intended_name}: {error_msg}"
                )
                return

        # Bring interface back up
        proc = await asyncio.create_subprocess_exec(
            "nsenter", "-t", str(pid), "-n",
            "ip", "link", "set", intended_name, "up",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

        result["fixed"] += 1

    async def _plugin_hot_connect(
        self,
        lab_id: str,
        container_a: str,
        iface_a: str,
        container_b: str,
        iface_b: str,
    ) -> bool:
        """Connect two interfaces using the shared OVS bridge.

        Finds OVS ports by container endpoint and sets matching VLAN tags.

        Args:
            lab_id: Lab identifier (unused, kept for API compatibility)
            container_a: First container name
            iface_a: Interface on first container
            container_b: Second container name
            iface_b: Interface on second container

        Returns:
            True if successful, False otherwise
        """
        bridge_name = settings.ovs_bridge_name  # Shared bridge (arch-ovs)

        # Find OVS ports attached to this container's interfaces
        # Ports are named with pattern: vh{endpoint_prefix}{random}
        # We need to find them by checking which port goes to which container

        async def find_ovs_port(container_name: str, interface_name: str) -> str | None:
            """Find OVS port name for a container interface."""
            try:
                container = await asyncio.to_thread(self.docker.containers.get, container_name)
                pid = container.attrs["State"]["Pid"]

                # Get interface's peer index from inside container
                # Need both -n (net) and -m (mount) namespaces to access /sys/class/net
                proc = await asyncio.create_subprocess_exec(
                    "nsenter", "-t", str(pid), "-n", "-m",
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

        When OVS plugin is enabled, uses shared OVS bridge with VLAN matching.
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
                    # Use OVS plugin's shared bridge
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

    async def _run_post_boot_commands(self, container_name: str, kind: str) -> None:
        """Run vendor-specific post-boot commands on a container.

        This handles workarounds like removing cEOS iptables DROP rules.
        """
        from agent.readiness import run_post_boot_commands
        try:
            await run_post_boot_commands(container_name, kind)
        except Exception as e:
            logger.warning(f"Post-boot commands failed for {container_name}: {e}")

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
                                # Run post-boot commands (e.g., remove cEOS iptables rules)
                                await self._run_post_boot_commands(container.name, node.kind)
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
            interface_count = n.interface_count
            if is_ceos_kind(n.kind) and (not interface_count or interface_count <= 0):
                config = get_config_by_device(n.kind)
                fallback = config.max_ports if config else 0
                if fallback > 0:
                    interface_count = fallback
                    logger.warning(
                        f"cEOS {n.name}: interface_count missing; defaulting to {fallback} for pre-provisioning"
                    )
            nodes[n.name] = TopologyNode(
                name=n.name,
                kind=n.kind,
                display_name=n.display_name,
                image=n.image,
                host=None,  # Not needed for execution; host routing done by controller
                interface_count=interface_count,
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
        workspace: Path,
        agent_id: str | None = None,
    ) -> DeployResult:
        """Deploy a topology using Docker SDK.

        Steps:
        1. Parse topology (JSON)
        2. Validate images exist
        3. Create required directories
        4. Create containers (network mode: none)
        5. Start containers
        6. Create local links (veth pairs)
        7. Wait for readiness
        """
        workspace.mkdir(parents=True, exist_ok=True)

        # Parse topology from JSON only
        if topology:
            parsed_topology = self._topology_from_json(topology)
        else:
            return DeployResult(
                success=False,
                error="No topology provided (JSON required)",
            )
        if not parsed_topology.nodes:
            return DeployResult(
                success=False,
                error="No nodes found in topology",
            )

        logger.info(f"Deploying lab {lab_id} with {len(parsed_topology.nodes)} nodes")

        # Attempt to recover stale network state from previous deployment
        # This handles the case where the agent restarted and lost in-memory VLAN allocations
        recovered_vlans = await self._recover_stale_network(lab_id, workspace)
        if recovered_vlans:
            logger.info(
                f"Recovered network state for {len(recovered_vlans)} existing containers"
            )

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

        # Capture and persist VLAN allocations for recovery on restart
        await self._capture_container_vlans(lab_id, parsed_topology, workspace)

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

            # Clean up VLAN allocations for this lab (in-memory and on disk)
            if lab_id in self._vlan_allocations:
                del self._vlan_allocations[lab_id]
            if lab_id in self._next_vlan:
                del self._next_vlan[lab_id]
            self._remove_vlan_file(lab_id, workspace)

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
            all_containers: dict[str, Any] = {}

            # Find containers by label - may fail if Docker has stale container references
            try:
                containers = await asyncio.to_thread(
                    self.docker.containers.list,
                    all=True,
                    filters={"label": f"{LABEL_LAB_ID}={lab_id}"},
                )
                for c in containers:
                    all_containers[c.id] = c
            except Exception as e:
                # Label query failed (possibly due to ghost container references)
                # Log and continue with prefix-based fallback
                logger.warning(f"Label-based container query failed for lab {lab_id}: {e}")

            # Also find by prefix (fallback) - more resilient to Docker state issues
            prefix = self._lab_prefix(lab_id)
            try:
                prefix_containers = await asyncio.to_thread(
                    self.docker.containers.list,
                    all=True,
                    filters={"name": prefix},
                )
                for c in prefix_containers:
                    all_containers[c.id] = c
            except Exception as e:
                logger.warning(f"Prefix-based container query failed for lab {lab_id}: {e}")

            for container in all_containers.values():
                try:
                    node = self._node_from_container(container)
                    if node:
                        nodes.append(node)
                except Exception as e:
                    # Skip containers that can't be inspected (stale references)
                    logger.warning(f"Failed to get node info from container {container.id}: {e}")

            return StatusResult(
                lab_exists=len(nodes) > 0,
                nodes=nodes,
            )

        except Exception as e:
            return StatusResult(
                lab_exists=False,
                error=str(e),
            )

    async def _recover_stale_networks(
        self,
        container: Any,
        lab_id: str,
    ) -> bool:
        """Recover from stale network references by reconnecting to current lab networks.

        When a lab is redeployed while a node is stopped, the old container may reference
        Docker networks that no longer exist. This method:
        1. Disconnects from all stale/missing networks
        2. Reconnects to the current lab networks

        Returns True if recovery was attempted, False if no recovery was needed.
        """
        container_name = container.name

        # Get container's current network attachments
        await asyncio.to_thread(container.reload)
        networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})

        if not networks:
            return False

        # Find current lab networks (format: {lab_id}-eth{N})
        lab_prefix = f"{lab_id}-"
        current_lab_networks = {}
        try:
            all_networks = await asyncio.to_thread(self.docker.networks.list)
            for net in all_networks:
                if net.name.startswith(lab_prefix):
                    current_lab_networks[net.name] = net
        except Exception as e:
            logger.warning(f"Failed to list networks: {e}")
            return False

        # Disconnect from all networks that are stale or no longer exist
        networks_disconnected = []
        for net_name in list(networks.keys()):
            # Skip built-in networks
            if net_name in ("bridge", "host", "none"):
                continue

            # Check if this is a lab network that no longer exists or has wrong ID
            if net_name.startswith(lab_prefix) or net_name.startswith(lab_id):
                try:
                    # Try to get the network - if it doesn't exist, disconnect
                    await asyncio.to_thread(self.docker.networks.get, net_name)
                except NotFound:
                    # Network doesn't exist, need to disconnect
                    try:
                        # Can't disconnect from non-existent network normally,
                        # but we'll try anyway and catch the error
                        logger.debug(f"Network {net_name} not found, will be cleaned up on start")
                    except Exception:
                        pass
                    networks_disconnected.append(net_name)

        # Now disconnect from ALL lab-related networks so we can reconnect cleanly
        for net_name in list(networks.keys()):
            if net_name in ("bridge", "host", "none"):
                continue
            if net_name.startswith(lab_prefix) or net_name.startswith(lab_id):
                try:
                    net = await asyncio.to_thread(self.docker.networks.get, net_name)
                    await asyncio.to_thread(net.disconnect, container_name, force=True)
                    logger.debug(f"Disconnected {container_name} from {net_name}")
                    if net_name not in networks_disconnected:
                        networks_disconnected.append(net_name)
                except NotFound:
                    pass  # Network already gone
                except Exception as e:
                    logger.debug(f"Could not disconnect from {net_name}: {e}")

        if not networks_disconnected:
            return False

        logger.info(
            f"Disconnected {container_name} from {len(networks_disconnected)} stale networks"
        )

        # Reconnect to current lab networks
        reconnected = 0
        for net_name, net in sorted(current_lab_networks.items()):
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

    async def start_node(
        self,
        lab_id: str,
        node_name: str,
        workspace: Path,
    ) -> NodeActionResult:
        """Start a specific node.

        If the container fails to start due to stale network references (e.g., after
        a lab redeploy), this method will attempt to recover by disconnecting from
        stale networks and reconnecting to current lab networks.
        """
        container_name = self._container_name(lab_id, node_name)

        try:
            container = await asyncio.to_thread(self.docker.containers.get, container_name)

            # First attempt to start
            try:
                await asyncio.to_thread(container.start)
            except APIError as e:
                # Check if this is a stale network error
                error_msg = str(e).lower()
                if "network" in error_msg and "not found" in error_msg:
                    logger.warning(
                        f"Container {container_name} has stale network references, "
                        "attempting recovery..."
                    )
                    # Try to recover networks
                    recovered = await self._recover_stale_networks(container, lab_id)
                    if recovered:
                        # Retry start after recovery
                        await asyncio.to_thread(container.reload)
                        await asyncio.to_thread(container.start)
                        logger.info(f"Successfully started {container_name} after network recovery")
                    else:
                        raise  # Re-raise if recovery didn't help
                else:
                    raise  # Re-raise non-network errors

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
        from agent.readiness import clear_post_boot_state

        container_name = self._container_name(lab_id, node_name)

        try:
            container = await asyncio.to_thread(self.docker.containers.get, container_name)
            await asyncio.to_thread(container.stop, timeout=settings.container_stop_timeout)
            await asyncio.to_thread(container.reload)

            # Clear post-boot state so commands run again on restart
            clear_post_boot_state(container_name)

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
        """Get console command for a container.

        Supports two console methods:
        - docker_exec: Use docker exec with vendor-specific shell (default)
        - ssh: Use SSH to container's management IP address

        The console method is determined by the device's vendor config.
        """
        container_name = self._container_name(lab_id, node_name)

        try:
            container = await asyncio.to_thread(self.docker.containers.get, container_name)
            if container.status != "running":
                return None

            kind = container.labels.get(LABEL_NODE_KIND, "linux")
            console_method = get_console_method(kind)

            if console_method == "ssh":
                # Get container IP and SSH credentials
                ips = self._get_container_ips(container)
                if not ips:
                    logger.warning(f"No IP address found for SSH console to {container_name}")
                    return None

                ip = ips[0]  # Use first available IP
                user, password = get_console_credentials(kind)

                # Use sshpass for non-interactive password authentication
                # -o StrictHostKeyChecking=no: Don't prompt for host key verification
                # -o UserKnownHostsFile=/dev/null: Don't save host key
                # -o LogLevel=ERROR: Reduce SSH output noise
                return [
                    "sshpass", "-p", password,
                    "ssh",
                    "-o", "StrictHostKeyChecking=no",
                    "-o", "UserKnownHostsFile=/dev/null",
                    "-o", "LogLevel=ERROR",
                    f"{user}@{ip}",
                ]
            else:
                # Default: docker exec with vendor-specific shell
                shell = get_console_shell(kind)
                return ["docker", "exec", "-it", container_name, shell]

        except NotFound:
            return None
        except Exception:
            return None

    def get_container_name(self, lab_id: str, node_name: str) -> str:
        """Get the Docker container name for a node."""
        return self._container_name(lab_id, node_name)

    async def _extract_all_container_configs(
        self,
        lab_id: str,
        workspace: Path,
    ) -> list[tuple[str, str]]:
        """Extract running configs from all containers in a lab that support it.

        Checks each container's vendor config for extraction method and command.
        Supports:
        - config_extract_method="docker": Use docker exec
        - config_extract_method="ssh": Use SSH to container's management IP

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

                if not node_name or not kind:
                    continue

                # Look up vendor config extraction settings
                extraction_settings = get_config_extraction_settings(kind)

                # Skip containers that don't support extraction
                if extraction_settings.method not in ("docker", "ssh"):
                    continue

                log_name = _log_name_from_labels(labels)

                if container.status != "running":
                    logger.warning(f"Skipping {log_name}: container not running")
                    continue

                try:
                    cmd = extraction_settings.command
                    if not cmd:
                        logger.warning(f"No extraction command for {kind}, skipping {log_name}")
                        continue

                    config_content = None

                    if extraction_settings.method == "ssh":
                        # Extract via SSH
                        config_content = await self._extract_config_via_ssh(
                            container, kind, cmd, log_name
                        )
                    else:
                        # Extract via docker exec (default)
                        config_content = await self._extract_config_via_docker(
                            container, cmd, log_name
                        )

                    if not config_content or not config_content.strip():
                        logger.warning(f"Empty config from {log_name}")
                        continue

                    # Save to workspace/configs/{node}/startup-config
                    config_dir = workspace / "configs" / node_name
                    config_dir.mkdir(parents=True, exist_ok=True)
                    config_path = config_dir / "startup-config"
                    config_path.write_text(config_content)

                    extracted.append((node_name, config_content))
                    logger.info(f"Extracted config from {log_name} ({kind})")

                except Exception as e:
                    logger.error(f"Error extracting config from {log_name}: {e}")

        except Exception as e:
            logger.error(f"Error during config extraction for lab {lab_id}: {e}")

        return extracted

    async def _extract_config_via_docker(
        self,
        container,
        cmd: str,
        log_name: str,
    ) -> str | None:
        """Extract config from container via docker exec.

        Args:
            container: Docker container object
            cmd: Command to run
            log_name: Display name for logging

        Returns:
            Config content string or None on failure
        """
        try:
            # Use shell execution for complex commands with quotes/pipes
            exec_cmd = ["sh", "-c", cmd]

            result = await asyncio.to_thread(
                container.exec_run,
                exec_cmd,
                demux=True,
            )
            stdout, stderr = result.output

            if result.exit_code != 0:
                stderr_str = stderr.decode("utf-8") if stderr else ""
                logger.warning(
                    f"Failed to extract config from {log_name}: "
                    f"exit={result.exit_code}, stderr={stderr_str}"
                )
                return None

            return stdout.decode("utf-8") if stdout else None

        except Exception as e:
            logger.error(f"Docker exec failed for {log_name}: {e}")
            return None

    async def _extract_config_via_ssh(
        self,
        container,
        kind: str,
        cmd: str,
        log_name: str,
    ) -> str | None:
        """Extract config from container via SSH.

        Args:
            container: Docker container object
            kind: Device kind for credential lookup
            cmd: Command to run
            log_name: Display name for logging

        Returns:
            Config content string or None on failure
        """
        try:
            # Get container IP
            ips = self._get_container_ips(container)
            if not ips:
                logger.warning(f"No IP address found for SSH extraction from {log_name}")
                return None

            ip = ips[0]
            user, password = get_console_credentials(kind)

            # Run SSH command with sshpass
            proc = await asyncio.create_subprocess_exec(
                "sshpass", "-p", password,
                "ssh",
                "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
                "-o", "LogLevel=ERROR",
                "-o", "ConnectTimeout=10",
                f"{user}@{ip}",
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                stderr_str = stderr.decode("utf-8") if stderr else ""
                logger.warning(
                    f"SSH extraction failed for {log_name}: "
                    f"exit={proc.returncode}, stderr={stderr_str}"
                )
                return None

            return stdout.decode("utf-8") if stdout else None

        except Exception as e:
            logger.error(f"SSH extraction failed for {log_name}: {e}")
            return None

    # Backwards compatibility alias
    async def _extract_all_ceos_configs(
        self,
        lab_id: str,
        workspace: Path,
    ) -> list[tuple[str, str]]:
        """Deprecated: Use _extract_all_container_configs instead.

        Kept for backwards compatibility.
        """
        return await self._extract_all_container_configs(lab_id, workspace)

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

                    # Clean up VLAN allocations for orphaned lab
                    if lab_id in self._vlan_allocations:
                        del self._vlan_allocations[lab_id]
                    if lab_id in self._next_vlan:
                        del self._next_vlan[lab_id]
                    # Remove VLAN file if workspace exists
                    lab_workspace = Path(settings.workspace_path) / lab_id
                    if lab_workspace.exists():
                        self._remove_vlan_file(lab_id, lab_workspace)

        except Exception as e:
            logger.error(f"Error during orphan cleanup: {e}")

        return removed
