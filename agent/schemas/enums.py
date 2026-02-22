"""Enum types for the agent-controller protocol."""

from enum import Enum


class AgentStatus(str, Enum):
    """Agent health status."""
    ONLINE = "online"
    DEGRADED = "degraded"
    OFFLINE = "offline"


class NodeStatus(str, Enum):
    """Container/VM node status."""
    PENDING = "pending"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"
    UNKNOWN = "unknown"


class JobStatus(str, Enum):
    """Job execution status."""
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    # Accepted status for async job execution (callback mode)
    ACCEPTED = "accepted"


class Provider(str, Enum):
    """Supported infrastructure providers."""
    DOCKER = "docker"  # Native Docker management for containers
    LIBVIRT = "libvirt"  # Libvirt for qcow2 VMs


class LinkState(str, Enum):
    """State of a network link."""
    CONNECTED = "connected"  # Link is active, traffic can flow
    DISCONNECTED = "disconnected"  # Link is down, ports isolated
    PENDING = "pending"  # Link is being created/modified
    ERROR = "error"  # Link creation failed
