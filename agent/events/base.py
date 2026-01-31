"""Base interface for event listeners.

This module defines the abstract interface that all event listeners must
implement, enabling provider-agnostic event handling for containers and VMs.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable, Awaitable


class NodeEventType(str, Enum):
    """Types of node state change events."""

    # Container/VM started and is now running
    STARTED = "started"

    # Container/VM stopped (graceful or forced)
    STOPPED = "stopped"

    # Container/VM died unexpectedly (crash, OOM, etc.)
    DIED = "died"

    # Container/VM is being created
    CREATING = "creating"

    # Container/VM is being destroyed
    DESTROYING = "destroying"

    # Health check state changed
    HEALTH_CHANGED = "health_changed"


@dataclass
class NodeEvent:
    """A node state change event.

    Attributes:
        lab_id: The lab ID this node belongs to
        node_name: The node name (as known to Archetype)
        container_id: The container/VM ID (if available)
        event_type: The type of state change
        timestamp: When the event occurred
        status: Current status string from the provider
        attributes: Additional provider-specific attributes
    """

    lab_id: str
    node_name: str
    container_id: str | None
    event_type: NodeEventType
    timestamp: datetime
    status: str
    attributes: dict | None = None


# Type alias for event callbacks
EventCallback = Callable[[NodeEvent], Awaitable[None]]


class NodeEventListener(ABC):
    """Abstract base class for node event listeners.

    Event listeners watch for state changes from a specific provider
    (Docker, libvirt, etc.) and invoke callbacks when events occur.

    Implementations should:
    1. Filter events to only managed nodes (e.g., archetype-* or clab-* containers)
    2. Parse provider-specific events into NodeEvent objects
    3. Handle reconnection on connection loss
    4. Be cancellable via the stop() method
    """

    @abstractmethod
    async def start(self, callback: EventCallback) -> None:
        """Start listening for events.

        This method should run indefinitely until stop() is called.
        When an event matching our criteria is received, invoke the callback.

        Args:
            callback: Async function to call with each NodeEvent
        """
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Stop listening for events.

        This should cleanly shut down the listener and release resources.
        """
        pass

    @abstractmethod
    def is_running(self) -> bool:
        """Check if the listener is currently running.

        Returns:
            True if actively listening, False otherwise
        """
        pass
