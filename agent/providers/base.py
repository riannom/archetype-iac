"""Base provider interface for infrastructure orchestration."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.schemas import DeployTopology


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
