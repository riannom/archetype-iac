"""Base provider interface for infrastructure orchestration."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


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
        """Provider name (e.g., 'containerlab', 'libvirt')."""
        ...

    @abstractmethod
    async def deploy(
        self,
        lab_id: str,
        topology_yaml: str,
        workspace: Path,
    ) -> DeployResult:
        """Deploy a topology.

        Args:
            lab_id: Unique identifier for the lab
            topology_yaml: The topology definition in YAML format
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
