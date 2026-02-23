"""Base provider interface for infrastructure orchestration."""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.schemas import DeployTopology

logger = logging.getLogger(__name__)


class NodeStatus(str, Enum):
    """Status of a node."""
    PENDING = "pending"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"
    UNKNOWN = "unknown"


@dataclass
class NodeInfo:
    """Information about a running node."""
    name: str
    status: NodeStatus
    container_id: str | None = None
    image: str | None = None
    ip_addresses: list[str] = field(default_factory=list)
    interfaces: dict[str, str] = field(default_factory=dict)  # iface -> ip
    error: str | None = None


@dataclass
class DeployResult:
    """Result of a deploy operation."""
    success: bool
    nodes: list[NodeInfo] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    error: str | None = None


@dataclass
class DestroyResult:
    """Result of a destroy operation."""
    success: bool
    stdout: str = ""
    stderr: str = ""
    error: str | None = None


@dataclass
class StatusResult:
    """Result of a status query."""
    lab_exists: bool
    nodes: list[NodeInfo] = field(default_factory=list)
    error: str | None = None


@dataclass
class NodeActionResult:
    """Result of a node start/stop action."""
    success: bool
    node_name: str
    new_status: NodeStatus = NodeStatus.UNKNOWN
    stdout: str = ""
    stderr: str = ""
    error: str | None = None


class Provider(ABC):
    """Abstract base class for infrastructure providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name (e.g., 'docker', 'libvirt')."""
        ...

    @property
    def display_name(self) -> str:
        """Human-readable display name for the provider.

        Defaults to capitalized version of name.
        """
        return self.name.capitalize()

    @property
    def capabilities(self) -> list[str]:
        """List of capabilities supported by this provider.

        Default capabilities that all providers support.
        Subclasses can override to add/remove capabilities.
        """
        return ["deploy", "destroy", "status", "node_actions", "console"]

    @abstractmethod
    async def deploy(
        self,
        lab_id: str,
        topology: "DeployTopology | None",
        workspace: Path,
    ) -> DeployResult:
        """Deploy a topology.

        Accepts topology in JSON format only.

        Args:
            lab_id: Unique identifier for the lab
            topology: Structured topology definition (JSON format)
            workspace: Directory to use for lab files

        Returns:
            DeployResult with success status and node info
        """
        ...

    @abstractmethod
    async def destroy(
        self,
        lab_id: str,
        workspace: Path,
    ) -> DestroyResult:
        """Destroy a deployed topology.

        Args:
            lab_id: Unique identifier for the lab
            workspace: Directory containing lab files

        Returns:
            DestroyResult with success status
        """
        ...

    @abstractmethod
    async def status(
        self,
        lab_id: str,
        workspace: Path,
    ) -> StatusResult:
        """Get status of all nodes in a lab.

        Args:
            lab_id: Unique identifier for the lab
            workspace: Directory containing lab files

        Returns:
            StatusResult with node information
        """
        ...

    @abstractmethod
    async def start_node(
        self,
        lab_id: str,
        node_name: str,
        workspace: Path,
    ) -> NodeActionResult:
        """Start a specific node.

        Args:
            lab_id: Unique identifier for the lab
            node_name: Name of the node to start
            workspace: Directory containing lab files

        Returns:
            NodeActionResult with success status
        """
        ...

    @abstractmethod
    async def stop_node(
        self,
        lab_id: str,
        node_name: str,
        workspace: Path,
    ) -> NodeActionResult:
        """Stop a specific node.

        Args:
            lab_id: Unique identifier for the lab
            node_name: Name of the node to stop
            workspace: Directory containing lab files

        Returns:
            NodeActionResult with success status
        """
        ...

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

        Default implementation raises NotImplementedError.
        Providers that support per-node lifecycle should override this.
        """
        raise NotImplementedError(
            f"Provider {self.name} does not support per-node create"
        )

    async def destroy_node(
        self,
        lab_id: str,
        node_name: str,
        workspace: Path,
    ) -> NodeActionResult:
        """Destroy a single node container and clean up resources.

        Default implementation raises NotImplementedError.
        Providers that support per-node lifecycle should override this.
        """
        raise NotImplementedError(
            f"Provider {self.name} does not support per-node destroy"
        )

    async def get_console_command(
        self,
        lab_id: str,
        node_name: str,
        workspace: Path,
    ) -> list[str] | None:
        """Get command to connect to node console.

        Returns None if console access is not supported.
        Default implementation returns None.
        """
        return None

    async def discover_labs(self) -> dict[str, list[NodeInfo]]:
        """Discover all running labs managed by this provider.

        Optional method for providers that support lab discovery.
        Returns dict mapping lab_id -> list of NodeInfo.

        Default implementation returns empty dict.
        """
        return {}

    async def cleanup_orphan_resources(
        self,
        valid_lab_ids: set[str],
        workspace_base: Path | None = None,
    ) -> dict[str, list[str]]:
        """Remove resources for labs that no longer exist.

        Optional method for providers that support orphan cleanup.
        Discovers all resources managed by this provider and removes those
        belonging to labs not in the valid_lab_ids set.

        Args:
            valid_lab_ids: Set of lab IDs that are known to be valid.
            workspace_base: Base workspace path for file cleanup.

        Returns:
            Dict with provider-specific keys listing removed items.
            Common keys: 'containers', 'domains', 'disks', 'networks'.

        Default implementation returns empty dict (no cleanup).
        """
        return {}


class VlanPersistenceMixin:
    """Shared VLAN allocation persistence for Docker and Libvirt providers.

    Provides file-backed VLAN allocation tracking so that network state
    survives agent restarts.  Both DockerProvider and LibvirtProvider use
    identical logic for persisting / loading / removing VLAN files and for
    querying per-node VLAN tags, so this mixin eliminates the duplication.

    Subclasses must call ``__init_vlan_state__()`` from their own
    ``__init__`` to initialise the required instance attributes.
    """

    VLAN_RANGE_START = 100
    VLAN_RANGE_END = 2049

    def __init_vlan_state__(self) -> None:
        """Initialise VLAN tracking dictionaries.

        Must be called from the provider's ``__init__``.
        """
        self._vlan_allocations: dict[str, dict[str, list[int]]] = {}
        self._next_vlan: dict[str, int] = {}

    # -- directory helpers ---------------------------------------------------

    def _vlans_dir(self, workspace: Path) -> Path:
        """Get directory for VLAN allocation files."""
        vlans = workspace / "vlans"
        vlans.mkdir(parents=True, exist_ok=True)
        return vlans

    # -- persistence ---------------------------------------------------------

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
        next_vlan = self._next_vlan.get(lab_id, self.VLAN_RANGE_START)

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
            next_vlan = vlan_data.get("next_vlan", self.VLAN_RANGE_START)

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

    # -- query ---------------------------------------------------------------

    def get_node_vlans(self, lab_id: str, node_name: str) -> list[int]:
        """Get the VLAN tags allocated to a node's interfaces.

        Args:
            lab_id: Lab identifier
            node_name: Node name

        Returns:
            List of VLAN tags, or empty list if not found
        """
        return self._vlan_allocations.get(lab_id, {}).get(node_name, [])
