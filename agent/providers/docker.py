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
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import docker
from docker.errors import NotFound, APIError, ImageNotFound
from docker.types import IPAMConfig

from agent.config import settings
from agent.network.local import LocalNetworkManager, get_local_manager
from agent.network.ovs import OVSNetworkManager, get_ovs_manager
from agent.network.docker_plugin import DockerOVSPlugin, get_docker_ovs_plugin
from agent.providers.naming import docker_container_name as _docker_name, sanitize_id
from agent.providers.base import (
    DeployResult,
    DestroyResult,
    NodeActionResult,
    NodeInfo,
    NodeStatus,
    Provider,
    StatusResult,
    VlanPersistenceMixin,
)
from agent.schemas import DeployTopology
from agent.vendors import (
    get_config_by_device,
    get_console_credentials,
    get_console_method,
    get_console_shell,
    is_ceos_kind,
    is_cjunos_kind,
)
from agent.providers.docker_setup import (
    setup_ceos_directories,
    setup_cjunos_directories,
    validate_images,
    create_container_config,
    calculate_required_interfaces,
    count_node_interfaces,
)
from agent.providers.docker_config_extract import (
    extract_all_container_configs,
    extract_config_via_docker,
    extract_config_via_ssh,
    extract_config_via_nvram,
)
from agent.providers.docker_networks import (
    create_lab_networks as _create_lab_networks_impl,
    delete_lab_networks as _delete_lab_networks_impl,
    recover_stale_networks as _recover_stale_networks_impl,
    prune_legacy_lab_networks as _prune_legacy_lab_networks_impl,
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
LABEL_NODE_INTERFACE_COUNT = "archetype.node_interface_count"
LABEL_NODE_READINESS_PROBE = "archetype.readiness_probe"
LABEL_NODE_READINESS_PATTERN = "archetype.readiness_pattern"
LABEL_NODE_READINESS_TIMEOUT = "archetype.readiness_timeout"
LABEL_PROVIDER = "archetype.provider"

# Retry policy for transient Docker daemon/API failures on critical operations.
DOCKER_OP_MAX_RETRIES = 3
DOCKER_OP_RETRY_BASE_SECONDS = 0.2


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
    cpu: int | None = None
    cpu_limit: int | None = None
    readiness_probe: str | None = None
    readiness_pattern: str | None = None
    readiness_timeout: int | None = None

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


class DockerProvider(Provider, VlanPersistenceMixin):
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
        self._lab_network_locks: dict[str, asyncio.Lock] = {}
        self.__init_vlan_state__()

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
        return _docker_name(lab_id, node_name)

    def _lab_prefix(self, lab_id: str) -> str:
        """Get container name prefix for a lab."""
        return f"{CONTAINER_PREFIX}-{sanitize_id(lab_id, max_len=20)}"

    def _lab_network_prefix(self, lab_id: str) -> str:
        """Get network name prefix for a lab (sanitized, full ID)."""
        return sanitize_id(lab_id)

    def _legacy_lab_network_prefixes(self, lab_id: str) -> tuple[str, str]:
        """Get current and legacy (truncated) network prefixes for a lab."""
        current_prefix = self._lab_network_prefix(lab_id)
        legacy_prefix = sanitize_id(lab_id, max_len=20)
        return current_prefix, legacy_prefix

    def _get_lab_network_lock(self, lab_id: str) -> asyncio.Lock:
        """Get per-lab lock to serialize network provisioning operations."""
        lock = self._lab_network_locks.get(lab_id)
        if lock is None:
            lock = asyncio.Lock()
            self._lab_network_locks[lab_id] = lock
        return lock

    def _is_transient_docker_error(self, err: Exception) -> bool:
        """Return True if a Docker API error should be retried."""
        if isinstance(err, APIError):
            status = getattr(err, "status_code", None)
            if status in {500, 502, 503, 504}:
                return True

        msg = str(err).lower()
        transient_markers = (
            "timeout",
            "timed out",
            "temporarily unavailable",
            "try again",
            "connection reset",
            "connection aborted",
            "connection refused",
            "broken pipe",
            "eof",
            "bad gateway",
            "service unavailable",
            "internal server error",
            "transport is closing",
            "docker daemon is not running",
        )
        return any(marker in msg for marker in transient_markers)

    async def _retry_docker_call(
        self,
        op_name: str,
        func: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Run a Docker SDK call with bounded retries for transient failures."""
        for attempt in range(1, DOCKER_OP_MAX_RETRIES + 1):
            try:
                return await asyncio.to_thread(func, *args, **kwargs)
            except Exception as err:
                should_retry = self._is_transient_docker_error(err) and attempt < DOCKER_OP_MAX_RETRIES
                if not should_retry:
                    raise
                delay = DOCKER_OP_RETRY_BASE_SECONDS * (2 ** (attempt - 1))
                logger.warning(
                    f"Transient Docker failure during {op_name} "
                    f"(attempt {attempt}/{DOCKER_OP_MAX_RETRIES}): {err}; "
                    f"retrying in {delay:.1f}s"
                )
                await asyncio.sleep(delay)

    def _lab_network_create_kwargs(
        self,
        network_name: str,
        lab_id: str,
        interface_name: str,
    ) -> dict[str, Any]:
        """Build canonical Docker network create arguments for lab interfaces."""
        return {
            "name": network_name,
            "driver": "archetype-ovs",
            "ipam": IPAMConfig(driver="null"),
            "options": {
                "lab_id": lab_id,
                "interface_name": interface_name,
            },
            "labels": {
                LABEL_LAB_ID: lab_id,
                LABEL_PROVIDER: self.name,
                "archetype.type": "lab-interface",
            },
        }

    def _network_matches_lab_spec(
        self,
        network: Any,
        lab_id: str,
        interface_name: str,
    ) -> bool:
        """Check whether an existing Docker network matches expected lab config."""
        attrs = getattr(network, "attrs", {}) or {}
        labels = attrs.get("Labels") or {}
        options = attrs.get("Options") or {}
        driver = attrs.get("Driver")
        return (
            driver == "archetype-ovs"
            and labels.get(LABEL_LAB_ID) == lab_id
            and labels.get(LABEL_PROVIDER) == self.name
            and labels.get("archetype.type") == "lab-interface"
            and options.get("lab_id") == lab_id
            and options.get("interface_name") == interface_name
        )

    async def _resolve_conflicting_lab_network(
        self,
        network_name: str,
        lab_id: str,
        interface_name: str,
    ) -> str:
        """Resolve network conflict safely: reuse valid, recreate stale+unused only."""
        try:
            existing = await self._retry_docker_call(
                f"inspect network {network_name}",
                self.docker.networks.get,
                network_name,
            )
        except NotFound as err:
            raise RuntimeError(
                f"Docker reported conflict for {network_name}, but network lookup failed"
            ) from err

        if self._network_matches_lab_spec(existing, lab_id, interface_name):
            logger.info(f"Reusing existing lab network {network_name} after conflict")
            return "reused"

        attrs = getattr(existing, "attrs", {}) or {}
        containers = attrs.get("Containers") or {}
        if containers:
            raise RuntimeError(
                f"Conflicting network {network_name} has active endpoints; "
                "refusing destructive recreate"
            )

        rollback_driver = attrs.get("Driver")
        rollback_labels = attrs.get("Labels") if isinstance(attrs.get("Labels"), dict) else {}
        rollback_options = attrs.get("Options") if isinstance(attrs.get("Options"), dict) else {}

        logger.warning(
            f"Conflicting network {network_name} has stale config; recreating (no active endpoints)"
        )
        await self._retry_docker_call(
            f"remove stale network {network_name}",
            existing.remove,
        )
        try:
            await self._retry_docker_call(
                f"recreate network {network_name}",
                self.docker.networks.create,
                **self._lab_network_create_kwargs(network_name, lab_id, interface_name),
            )
        except Exception:
            rollback_kwargs: dict[str, Any] = {"name": network_name}
            if isinstance(rollback_driver, str) and rollback_driver:
                rollback_kwargs["driver"] = rollback_driver
            if rollback_labels:
                rollback_kwargs["labels"] = rollback_labels
            if rollback_options:
                rollback_kwargs["options"] = rollback_options

            if "driver" in rollback_kwargs:
                try:
                    await self._retry_docker_call(
                        f"rollback network {network_name}",
                        self.docker.networks.create,
                        **rollback_kwargs,
                    )
                    logger.warning(
                        f"Recreate failed for {network_name}; restored prior network config via rollback"
                    )
                except Exception as rollback_err:
                    logger.error(
                        f"Recreate failed for {network_name} and rollback failed: {rollback_err}"
                    )
            else:
                logger.error(
                    f"Recreate failed for {network_name}; rollback skipped because previous "
                    "network driver metadata was unavailable"
                )
            raise
        return "recreated"

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
            self._next_vlan[lab_id] = self.VLAN_RANGE_START

        max_vlan_seen = self.VLAN_RANGE_START

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
        return validate_images(topology, self.docker)

    def _create_container_config(
        self,
        node: TopologyNode,
        lab_id: str,
        workspace: Path,
        interface_count: int = 0,
    ) -> dict[str, Any]:
        """Build Docker container configuration for a node.

        Returns a dict suitable for docker.containers.create().
        """
        return create_container_config(
            node=node,
            lab_id=lab_id,
            workspace=workspace,
            interface_count=interface_count,
            provider_name=self.name,
            container_name_func=self._container_name,
        )

    def _setup_ceos_directories(
        self,
        node_name: str,
        node: TopologyNode,
        workspace: Path,
    ) -> None:
        """Set up cEOS directories and config files.

        This is a blocking operation meant to run in asyncio.to_thread().
        """
        setup_ceos_directories(node_name, node, workspace)

    def _setup_cjunos_directories(
        self,
        node_name: str,
        node: TopologyNode,
        workspace: Path,
    ) -> None:
        """Set up cJunOS directories and startup config.

        This is a blocking operation meant to run in asyncio.to_thread().
        """
        setup_cjunos_directories(node_name, node, workspace)

    async def _ensure_directories(
        self,
        topology: ParsedTopology,
        workspace: Path,
        use_thread: bool = True,
    ) -> None:
        """Create required directories for nodes (e.g., cEOS flash).

        Runs blocking file I/O in thread pool to avoid blocking event loop.
        """
        for node_name, node in topology.nodes.items():
            if is_ceos_kind(node.kind):
                if use_thread:
                    # Run blocking file operations in thread pool
                    await asyncio.to_thread(
                        self._setup_ceos_directories,
                        node_name,
                        node,
                        workspace,
                    )
                else:
                    self._setup_ceos_directories(node_name, node, workspace)
            elif is_cjunos_kind(node.kind):
                if use_thread:
                    await asyncio.to_thread(
                        self._setup_cjunos_directories,
                        node_name,
                        node,
                        workspace,
                    )
                else:
                    self._setup_cjunos_directories(node_name, node, workspace)

    def _calculate_required_interfaces(self, topology: ParsedTopology) -> int:
        """Calculate the maximum interface index needed for pre-provisioning.

        Returns:
            Number of interfaces to create (max index found + buffer)
        """
        return calculate_required_interfaces(topology)

    def _count_node_interfaces(self, node_name: str, topology: ParsedTopology) -> int:
        """Count the number of interfaces connected to a specific node.

        Returns:
            Max interface index required for this node
        """
        return count_node_interfaces(node_name, topology)

    async def _create_lab_networks(
        self,
        lab_id: str,
        max_interfaces: int = 8,
    ) -> dict[str, str]:
        """Create Docker networks for lab interfaces via OVS plugin.

        Returns:
            Dict mapping interface name (e.g., "eth0") to network name
        """
        return await _create_lab_networks_impl(self, lab_id, max_interfaces)

    async def _delete_lab_networks(self, lab_id: str) -> int:
        """Delete all Docker networks for a lab.

        Returns:
            Number of networks deleted
        """
        return await _delete_lab_networks_impl(self, lab_id)

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
        lab_prefix = self._lab_network_prefix(lab_id)
        for i in range(interface_count):
            iface_num = start_index + i
            interface_name = f"{interface_prefix}{iface_num}"
            network_name = f"{lab_prefix}-{interface_name}"
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
                if self.use_ovs_plugin and node_interface_count < 1:
                    # We always attach at least eth1 when OVS plugin is enabled
                    node_interface_count = 1
                config = self._create_container_config(
                    node, lab_id, workspace, interface_count=node_interface_count
                )

                # Set network mode based on whether OVS plugin is enabled
                # When OVS plugin is enabled, we attach to Docker networks which
                # provision interfaces BEFORE container init runs (critical for cEOS).
                # When disabled, we use "none" mode and provision interfaces post-start.
                if self.use_ovs_plugin:
                    # Determine NIC layout based on vendor config:
                    #   eth0 = management (if management_interface set)
                    #   eth1..ethR = reserved NICs (R = reserved_nics)
                    #   eth(dps)+ = data ports (dps = data_port_start)
                    vendor_config = get_config_by_device(node.kind)
                    has_mgmt = vendor_config and vendor_config.management_interface
                    reserved = vendor_config.reserved_nics if vendor_config else 0

                    lab_prefix = self._lab_network_prefix(lab_id)
                    if has_mgmt:
                        # Management on eth0, then reserved + data
                        first_network = f"{lab_prefix}-eth0"
                        config["network"] = first_network
                        extra_count = reserved + node_interface_count
                        extra_start = 1
                    else:
                        # No management — eth1 is first data port
                        first_network = f"{lab_prefix}-eth1"
                        config["network"] = first_network
                        extra_count = max(node_interface_count - 1, 0)
                        extra_start = 2

                    logger.info(f"Creating container {log_name} with image {config['image']}")

                    # Create container - run in thread pool to avoid blocking event loop
                    logger.debug(f"[{log_name}] Starting container.create...")
                    container = await asyncio.to_thread(
                        lambda cfg=config: self.docker.containers.create(**cfg)
                    )
                    logger.debug(f"[{log_name}] container.create completed")
                    containers[node_name] = container

                    # Attach to remaining interface networks
                    logger.debug(f"[{log_name}] Starting network attachments...")
                    await self._attach_container_to_networks(
                        container=container,
                        lab_id=lab_id,
                        interface_count=extra_count,
                        interface_prefix="eth",
                        start_index=extra_start,
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
            if network.lab_id == lab_id:
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

                # Delete the stale duplicate we just renamed to _old_*
                await asyncio.create_subprocess_exec(
                    "nsenter", "-t", str(pid), "-n",
                    "ip", "link", "delete", temp_name,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                logger.info(f"Deleted stale duplicate {temp_name} in {container_name}")
            else:
                result["errors"].append(
                    f"Failed to rename {actual_name} -> {intended_name}: {error_msg}"
                )
                return

        # Set MTU inside container namespace (Docker resets to 1500 during veth move)
        if settings.local_mtu > 0:
            proc = await asyncio.create_subprocess_exec(
                "nsenter", "-t", str(pid), "-n",
                "ip", "link", "set", intended_name, "mtu", str(settings.local_mtu),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()

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
                readiness_probe = node.readiness_probe or (config.readiness_probe if config else None)
                readiness_pattern = node.readiness_pattern or (config.readiness_pattern if config else None)
                readiness_timeout = node.readiness_timeout or (config.readiness_timeout if config else 120)
                if not readiness_probe or readiness_probe == "none":
                    ready_status[node_name] = True
                    continue

                # Check node-specific timeout
                node_timeout = readiness_timeout
                if elapsed > node_timeout:
                    logger.warning(f"Node {log_name} timed out waiting for readiness")
                    continue

                # Check readiness - use asyncio.to_thread to avoid blocking event loop
                try:
                    await asyncio.to_thread(container.reload)
                    if container.status != "running":
                        all_ready = False
                        continue

                    if readiness_probe == "log_pattern":
                        # Check logs for pattern - run in thread to avoid blocking
                        logs_bytes = await asyncio.to_thread(container.logs, tail=100)
                        logs = logs_bytes.decode(errors="replace")
                        if readiness_pattern:
                            if re.search(readiness_pattern, logs):
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
                cpu=n.cpu,
                cpu_limit=n.cpu_limit,
                readiness_probe=n.readiness_probe,
                readiness_pattern=n.readiness_pattern,
                readiness_timeout=n.readiness_timeout,
            )

        links = []
        for lnk in deploy_topology.links:
            links.append(TopologyLink(
                endpoints=[
                    f"{lnk.source_node}:{lnk.source_interface}",
                    f"{lnk.target_node}:{lnk.target_interface}",
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

        # Validate images (sync Docker SDK calls, run off event loop)
        missing_images = await asyncio.to_thread(self._validate_images, parsed_topology)
        if missing_images:
            logger.error(f"Missing images: {missing_images}")
            error_lines = ["Missing images:"]
            for node_name, image in missing_images:
                log_name = parsed_topology.log_name(node_name)
                error_lines.append(f"  • Node '{log_name}' requires: {image}")
            error_lines.append("")
            error_lines.append("Please upload images via the Images page or import manually.")
            error_msg = "\n".join(error_lines)
            return DeployResult(
                success=False,
                error=f"Missing {len(missing_images)} image(s)",
                stderr=error_msg,
            )

        # Create directories
        await self._ensure_directories(parsed_topology, workspace)

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
            status_result = await self.status(lab_id, workspace)
            error_msg = f"Failed to start {len(failed_starts)} container(s): {', '.join(failed_log_names)}"
            return DeployResult(
                success=False,
                nodes=status_result.nodes,
                stderr=error_msg,
                error=error_msg,
            )

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

            # Clean up Docker networks if OVS plugin is enabled (triggers plugin cleanup)
            if self.use_ovs_plugin:
                networks_deleted = await self._delete_lab_networks(lab_id)
                logger.info(f"Docker network cleanup: {networks_deleted} networks deleted")

            # Clean up local networking
            cleanup_result = await self.local_network.cleanup_lab(lab_id)
            logger.info(f"Local network cleanup: {cleanup_result}")

            # Clean up OVS networking if enabled
            if self.use_ovs and self.ovs_manager._initialized:
                ovs_cleanup_result = await self.ovs_manager.cleanup_lab(lab_id)
                logger.info(f"OVS network cleanup: {ovs_cleanup_result}")

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

        Returns True if recovery was attempted, False if no recovery was needed.
        """
        return await _recover_stale_networks_impl(self, container, lab_id)

    async def _prune_legacy_lab_networks(self, lab_id: str) -> int:
        """Remove legacy lab networks that don't match current naming/labels."""
        return await _prune_legacy_lab_networks_impl(self, lab_id)

    async def start_node(
        self,
        lab_id: str,
        node_name: str,
        workspace: Path,
        *,
        repair_endpoints: bool = True,
        fix_interfaces: bool = True,
    ) -> NodeActionResult:
        """Start a specific node.

        If the container fails to start due to stale network references (e.g., after
        a lab redeploy), this method will attempt to recover by disconnecting from
        stale networks and reconnecting to current lab networks.

        After starting, optionally repairs veth pairs and fixes interface names.
        This enables per-node restart without full topology redeploy.
        """
        container_name = self._container_name(lab_id, node_name)
        endpoints_repaired = 0
        interfaces_fixed = 0

        try:
            container = await asyncio.to_thread(self.docker.containers.get, container_name)

            # First attempt to start
            import time as _time
            from agent.metrics import docker_api_duration
            _docker_t0 = _time.monotonic()
            _docker_status = "success"
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
                        _docker_status = "error"
                        raise  # Re-raise if recovery didn't help
                else:
                    _docker_status = "error"
                    raise  # Re-raise non-network errors
            except Exception:
                _docker_status = "error"
                raise
            finally:
                docker_api_duration.labels(operation="start", status=_docker_status).observe(
                    _time.monotonic() - _docker_t0
                )

            await asyncio.sleep(1)
            await asyncio.to_thread(container.reload)

            # Repair veth pairs that were lost on container stop/restart
            if repair_endpoints and self.use_ovs_plugin:
                try:
                    plugin = get_docker_ovs_plugin()
                    if plugin:
                        repair_results = await plugin.repair_endpoints(lab_id, container_name)
                        endpoints_repaired = sum(
                            1 for r in repair_results if r.get("status") == "repaired"
                        )
                        if endpoints_repaired > 0:
                            logger.info(
                                f"Repaired {endpoints_repaired} endpoints for {container_name}"
                            )
                except Exception as e:
                    logger.warning(f"Failed to repair endpoints for {container_name}: {e}")

            # Fix interface names (Docker may assign them incorrectly)
            if fix_interfaces and self.use_ovs_plugin:
                try:
                    fix_result = await self._fix_interface_names(container_name, lab_id)
                    interfaces_fixed = fix_result.get("fixed", 0)
                except Exception as e:
                    logger.warning(f"Failed to fix interface names for {container_name}: {e}")

            stdout_parts = [f"Started container {container_name}"]
            if endpoints_repaired > 0:
                stdout_parts.append(f"repaired {endpoints_repaired} endpoints")
            if interfaces_fixed > 0:
                stdout_parts.append(f"fixed {interfaces_fixed} interfaces")

            return NodeActionResult(
                success=True,
                node_name=node_name,
                new_status=self._get_container_status(container),
                stdout=", ".join(stdout_parts),
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

    async def _remove_container(
        self,
        lab_id: str,
        node_name: str,
        workspace: Path,
    ) -> None:
        """Remove a single container and clean up per-node resources.

        Stops the container (if running), removes it, clears post-boot state,
        and cleans VLAN allocations for this node. Does NOT clean lab-level
        resources (Docker networks, OVS management network) — callers invoke
        cleanup_lab_resources_if_empty() when appropriate.

        Raises NotFound if the container doesn't exist (caller should handle).
        """
        from agent.readiness import clear_post_boot_state
        import time as _time
        from agent.metrics import docker_api_duration

        container_name = self._container_name(lab_id, node_name)

        container = await asyncio.to_thread(self.docker.containers.get, container_name)

        # Stop if running
        if container.status == "running":
            _docker_t0 = _time.monotonic()
            _docker_status = "success"
            try:
                await asyncio.to_thread(container.stop, timeout=settings.container_stop_timeout)
            except Exception:
                _docker_status = "error"
                raise
            finally:
                docker_api_duration.labels(operation="stop", status=_docker_status).observe(
                    _time.monotonic() - _docker_t0
                )

        # Remove container and volumes
        _docker_t0 = _time.monotonic()
        _docker_status = "success"
        try:
            await asyncio.to_thread(container.remove, force=True, v=True)
        except Exception:
            _docker_status = "error"
            raise
        finally:
            docker_api_duration.labels(operation="remove", status=_docker_status).observe(
                _time.monotonic() - _docker_t0
            )

        logger.info(f"Removed container {container_name}")

        # Clear post-boot state so commands run again on fresh create
        clear_post_boot_state(container_name)

        # Clean up per-node VLAN allocations (keep lab-level tracking)
        lab_vlans = self._vlan_allocations.get(lab_id, {})
        lab_vlans.pop(node_name, None)

    async def stop_node(
        self,
        lab_id: str,
        node_name: str,
        workspace: Path,
    ) -> NodeActionResult:
        """Stop a specific node by removing its container entirely.

        After stop, the container is gone. Starting the node again will
        create a fresh container from the image with the saved startup config.
        """
        container_name = self._container_name(lab_id, node_name)

        try:
            await self._remove_container(lab_id, node_name, workspace)

            return NodeActionResult(
                success=True,
                node_name=node_name,
                new_status=NodeStatus.STOPPED,
                stdout=f"Stopped and removed container {container_name}",
            )

        except NotFound:
            # Container already gone — treat as success
            return NodeActionResult(
                success=True,
                node_name=node_name,
                new_status=NodeStatus.STOPPED,
                stdout=f"Container {container_name} already removed",
            )
        except APIError as e:
            return NodeActionResult(
                success=False,
                node_name=node_name,
                error=f"Docker API error: {e}",
            )

    async def create_node(
        self,
        lab_id: str,
        node_name: str,
        kind: str,
        workspace: Path,
        *,
        image: str | None = None,
        display_name: str | None = None,
        interface_count: int | None = None,
        binds: list[str] | None = None,
        env: dict[str, str] | None = None,
        startup_config: str | None = None,
        memory: int | None = None,
        cpu: int | None = None,
        cpu_limit: int | None = None,
        disk_driver: str | None = None,
        nic_driver: str | None = None,
        machine_type: str | None = None,
        libvirt_driver: str | None = None,
        readiness_probe: str | None = None,
        readiness_pattern: str | None = None,
        readiness_timeout: int | None = None,
        efi_boot: bool | None = None,
        efi_vars: str | None = None,
        data_volume_gb: int | None = None,
        image_sha256: str | None = None,
    ) -> NodeActionResult:
        """Create a single node container without starting it.

        Extracts per-node logic from _create_containers() to allow
        independent container creation without full topology deploy.
        """
        container_name = self._container_name(lab_id, node_name)
        iface_count = interface_count or 4  # Minimum interfaces for flexibility

        try:
            # Build a TopologyNode for _create_container_config
            node = TopologyNode(
                name=node_name,
                kind=kind,
                display_name=display_name,
                image=image,
                interface_count=iface_count,
                binds=binds or [],
                env=env or {},
                startup_config=startup_config,
                cpu=cpu,
                cpu_limit=cpu_limit,
            )
            log_name = node.log_name()

            # Validate image exists
            config = get_config_by_device(kind)
            effective_image = image or (config.default_image if config else None)
            if effective_image:
                try:
                    await asyncio.to_thread(self.docker.images.get, effective_image)
                except ImageNotFound:
                    return NodeActionResult(
                        success=False,
                        node_name=node_name,
                        error=f"Docker image not found: {effective_image}",
                    )

            # Set up vendor-specific directories if needed
            if is_ceos_kind(kind):
                await asyncio.to_thread(
                    self._setup_ceos_directories, node_name, node, workspace
                )
            elif is_cjunos_kind(kind):
                await asyncio.to_thread(
                    self._setup_cjunos_directories, node_name, node, workspace
                )

            # Ensure lab Docker networks (idempotent)
            # Look up vendor config early so we can size networks correctly
            vendor_config = get_config_by_device(kind)
            has_mgmt = vendor_config and vendor_config.management_interface
            reserved = vendor_config.reserved_nics if vendor_config else 0
            if self.use_ovs_plugin:
                # Devices with mgmt + reserved NICs need eth0..eth{reserved+iface_count}
                total_interfaces = (reserved + iface_count) if has_mgmt else iface_count
                await self._create_lab_networks(lab_id, max_interfaces=total_interfaces)

            # Check if container already exists
            try:
                existing = await asyncio.to_thread(
                    self.docker.containers.get, container_name
                )
                if existing.status == "running":
                    logger.info(f"Container {log_name} already running, skipping create")
                    return NodeActionResult(
                        success=True,
                        node_name=node_name,
                        new_status=NodeStatus.RUNNING,
                        stdout=f"Container {container_name} already running",
                    )
                else:
                    logger.info(f"Removing stopped container {log_name}")
                    await asyncio.to_thread(existing.remove, force=True)
            except NotFound:
                pass

            # Build container config
            container_config = self._create_container_config(
                node, lab_id, workspace, interface_count=iface_count
            )

            import time as _time
            from agent.metrics import docker_api_duration
            _docker_t0 = _time.monotonic()
            _docker_status = "success"

            try:
                if self.use_ovs_plugin:
                    lab_prefix = self._lab_network_prefix(lab_id)
                    if has_mgmt:
                        first_network = f"{lab_prefix}-eth0"
                        extra_count = reserved + iface_count
                        extra_start = 1
                    else:
                        first_network = f"{lab_prefix}-eth1"
                        extra_count = max(iface_count - 1, 0)
                        extra_start = 2

                    container_config["network"] = first_network
                    logger.info(f"Creating container {log_name} with image {container_config['image']}")

                    container = await asyncio.to_thread(
                        lambda cfg=container_config: self.docker.containers.create(**cfg)
                    )

                    await self._attach_container_to_networks(
                        container=container,
                        lab_id=lab_id,
                        interface_count=extra_count,
                        interface_prefix="eth",
                        start_index=extra_start,
                    )

                    await asyncio.sleep(0.5)
                else:
                    container_config["network_mode"] = "none"
                    logger.info(f"Creating container {log_name} with image {container_config['image']}")

                    container = await asyncio.to_thread(
                        lambda cfg=container_config: self.docker.containers.create(**cfg)
                    )
            except Exception:
                _docker_status = "error"
                raise
            finally:
                docker_api_duration.labels(operation="create", status=_docker_status).observe(
                    _time.monotonic() - _docker_t0
                )

            return NodeActionResult(
                success=True,
                node_name=node_name,
                new_status=NodeStatus.STOPPED,
                stdout=f"Created container {container_name} (id={container.short_id})",
            )

        except APIError as e:
            return NodeActionResult(
                success=False,
                node_name=node_name,
                error=f"Docker API error: {e}",
            )
        except Exception as e:
            logger.exception(f"Failed to create container for {node_name}: {e}")
            return NodeActionResult(
                success=False,
                node_name=node_name,
                error=f"Container creation failed: {e}",
            )

    async def cleanup_lab_resources_if_empty(
        self,
        lab_id: str,
        workspace: Path | None = None,
    ) -> dict[str, Any]:
        """Clean lab-level resources only when no containers remain for the lab.

        Returns:
            Dict containing:
            - cleaned: bool
            - remaining: int | None
            - networks_deleted: int
            - local_cleanup: bool
            - ovs_cleanup: bool
            - error: str | None
        """
        result: dict[str, Any] = {
            "cleaned": False,
            "remaining": None,
            "networks_deleted": 0,
            "local_cleanup": False,
            "ovs_cleanup": False,
            "error": None,
        }

        try:
            remaining = await self._retry_docker_call(
                f"list containers for lab {lab_id}",
                self.docker.containers.list,
                all=True,
                filters={"label": f"{LABEL_LAB_ID}={lab_id}"},
            )
        except Exception as e:
            result["error"] = str(e)
            logger.warning(f"Failed to check remaining containers for {lab_id}: {e}")
            return result

        remaining_count = len(remaining)
        result["remaining"] = remaining_count
        if remaining_count > 0:
            return result

        result["cleaned"] = True
        logger.info(f"Last container in lab {lab_id}, cleaning up lab-level resources")
        result["networks_deleted"] = await self._delete_lab_networks(lab_id)

        try:
            await self.local_network.cleanup_lab(lab_id)
            result["local_cleanup"] = True
        except Exception as e:
            logger.warning(f"Local network cleanup failed for lab {lab_id}: {e}")

        if self.use_ovs and self.ovs_manager._initialized:
            try:
                await self.ovs_manager.cleanup_lab(lab_id)
                result["ovs_cleanup"] = True
            except Exception as e:
                logger.warning(f"OVS cleanup failed for lab {lab_id}: {e}")

        self._vlan_allocations.pop(lab_id, None)
        self._next_vlan.pop(lab_id, None)
        if workspace is not None:
            self._remove_vlan_file(lab_id, workspace)

        return result

    async def destroy_node(
        self,
        lab_id: str,
        node_name: str,
        workspace: Path,
    ) -> NodeActionResult:
        """Destroy a single node container and clean up all resources.

        Uses _remove_container for per-node cleanup, then additionally
        cleans lab-level resources if this was the last container.
        """
        container_name = self._container_name(lab_id, node_name)

        try:
            try:
                await self._remove_container(lab_id, node_name, workspace)
            except NotFound:
                logger.info(f"Container {container_name} not found, already removed")

            cleanup_result = await self.cleanup_lab_resources_if_empty(lab_id, workspace)
            if cleanup_result.get("error"):
                logger.warning(
                    f"Skipped lab-level cleanup for {lab_id} due to container check error: "
                    f"{cleanup_result['error']}"
                )

            return NodeActionResult(
                success=True,
                node_name=node_name,
                new_status=NodeStatus.STOPPED,
                stdout=f"Destroyed container {container_name}",
            )

        except APIError as e:
            return NodeActionResult(
                success=False,
                node_name=node_name,
                error=f"Docker API error: {e}",
            )
        except Exception as e:
            logger.exception(f"Failed to destroy container {node_name}: {e}")
            return NodeActionResult(
                success=False,
                node_name=node_name,
                error=f"Container destroy failed: {e}",
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

        Returns list of (node_name, config_content) tuples.
        Also saves configs to workspace/configs/{node}/startup-config.
        """
        return await extract_all_container_configs(
            lab_id=lab_id,
            workspace=workspace,
            docker_client=self.docker,
            lab_prefix=self._lab_prefix(lab_id),
            provider_name=self.name,
            get_container_ips_func=self._get_container_ips,
            run_ssh_command_func=self._run_ssh_command,
        )

    async def _extract_config_via_docker(
        self,
        container,
        cmd: str,
        log_name: str,
    ) -> str | None:
        """Extract config from container via docker exec."""
        return await extract_config_via_docker(container, cmd, log_name)

    async def _extract_config_via_ssh(
        self,
        container,
        kind: str,
        cmd: str,
        log_name: str,
    ) -> str | None:
        """Extract config from container via SSH."""
        return await extract_config_via_ssh(
            container, kind, cmd, log_name,
            self._get_container_ips, self._run_ssh_command,
        )

    async def _extract_config_via_nvram(
        self,
        container_name: str,
        workspace: Path,
    ) -> str | None:
        """Extract config from IOL container via NVRAM file."""
        return await extract_config_via_nvram(container_name, workspace)

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

                if not self._is_orphan_lab(lab_id, valid_lab_ids):
                    continue

                logger.info(f"Removing orphan container {container.name} (lab: {lab_id})")
                await asyncio.to_thread(container.remove, force=True)
                removed.append(container.name)
                await self.local_network.cleanup_lab(lab_id)
                self._cleanup_orphan_vlans(
                    lab_id, Path(settings.workspace_path) / lab_id
                )

        except Exception as e:
            logger.error(f"Error during orphan cleanup: {e}")

        return removed
