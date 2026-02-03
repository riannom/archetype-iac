"""Centralized state enums for consistent state handling across the application.

This module defines all valid states for labs, nodes, links, and jobs,
eliminating hardcoded strings and providing type safety.

State machines for valid transitions are defined in services/state_machine.py.
"""

from enum import Enum


class LabState(str, Enum):
    """Lab-level state representing aggregate status of all nodes."""

    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"
    UNKNOWN = "unknown"


class NodeDesiredState(str, Enum):
    """What the user wants for a node - only stopped or running."""

    STOPPED = "stopped"
    RUNNING = "running"


class NodeActualState(str, Enum):
    """Current reality of a node's container state."""

    UNDEPLOYED = "undeployed"  # Node not yet deployed (no container exists)
    PENDING = "pending"  # Being deployed for the first time
    STARTING = "starting"  # Already-deployed node being started
    RUNNING = "running"  # Container is running
    STOPPING = "stopping"  # Container is being stopped
    STOPPED = "stopped"  # Container is stopped
    EXITED = "exited"  # Container exited (treated same as stopped)
    ERROR = "error"  # Error state


class LinkDesiredState(str, Enum):
    """What the user wants for a link - up or down."""

    UP = "up"
    DOWN = "down"


class LinkActualState(str, Enum):
    """Current reality of a link's connectivity state."""

    UNKNOWN = "unknown"  # Link state cannot be determined
    PENDING = "pending"  # Waiting to be created
    CREATING = "creating"  # Creation in progress
    UP = "up"  # Enabled and active
    DOWN = "down"  # Administratively disabled
    ERROR = "error"  # Creation or verification failed


class JobStatus(str, Enum):
    """Status of background jobs."""

    QUEUED = "queued"  # Waiting for agent to pick up
    RUNNING = "running"  # Agent is executing
    COMPLETED = "completed"  # Finished successfully
    FAILED = "failed"  # Failed (error or timeout)
    CANCELLED = "cancelled"  # Cancelled by user


class HostStatus(str, Enum):
    """Status of compute hosts running agents."""

    ONLINE = "online"
    OFFLINE = "offline"
    DEGRADED = "degraded"


class CarrierState(str, Enum):
    """Link carrier state for port-down simulation."""

    ON = "on"
    OFF = "off"


class ImageSyncStatus(str, Enum):
    """Status of image sync operations."""

    CHECKING = "checking"
    SYNCING = "syncing"
    SYNCED = "synced"
    FAILED = "failed"


class VxlanTunnelStatus(str, Enum):
    """Status of VXLAN tunnels for cross-host links."""

    PENDING = "pending"
    CREATING = "creating"
    ACTIVE = "active"
    ERROR = "error"
    DELETING = "deleting"
