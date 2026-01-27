"""Event listener infrastructure for real-time state updates.

This package provides provider-agnostic event listeners that watch for
state changes in containers and VMs, forwarding events to the controller
for real-time state synchronization.

Currently implemented:
- DockerEventListener: Listens to Docker Events API for container state changes

Future listeners:
- LibvirtEventListener: For VM state changes via libvirt
"""

from agent.events.base import NodeEvent, NodeEventListener, NodeEventType
from agent.events.docker_events import DockerEventListener

__all__ = [
    "NodeEvent",
    "NodeEventListener",
    "NodeEventType",
    "DockerEventListener",
]
