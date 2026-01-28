"""Network overlay module for multi-host connectivity."""

from agent.network.overlay import OverlayManager, VxlanTunnel, OverlayBridge
from agent.network.vlan import (
    VlanManager,
    VlanInterface,
    get_vlan_manager,
    setup_external_networks,
    cleanup_external_networks,
)

__all__ = [
    "OverlayManager",
    "VxlanTunnel",
    "OverlayBridge",
    "VlanManager",
    "VlanInterface",
    "get_vlan_manager",
    "setup_external_networks",
    "cleanup_external_networks",
]
