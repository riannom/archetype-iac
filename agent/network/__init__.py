"""Network module for lab connectivity.

This module provides networking capabilities for labs:
- Overlay (VXLAN) networking for multi-host connectivity
- Local networking (veth pairs) for intra-host container links
- VLAN management for external network connectivity
"""

from agent.network.overlay import OverlayManager, VxlanTunnel, OverlayBridge
from agent.network.local import (
    LocalNetworkManager,
    LocalLink,
    ManagedNetwork,
    get_local_manager,
)
from agent.network.vlan import (
    VlanManager,
    VlanInterface,
    get_vlan_manager,
    setup_external_networks,
    cleanup_external_networks,
)

__all__ = [
    # Overlay networking (VXLAN for cross-host)
    "OverlayManager",
    "VxlanTunnel",
    "OverlayBridge",
    # Local networking (veth pairs for same-host)
    "LocalNetworkManager",
    "LocalLink",
    "ManagedNetwork",
    "get_local_manager",
    # VLAN management
    "VlanManager",
    "VlanInterface",
    "get_vlan_manager",
    "setup_external_networks",
    "cleanup_external_networks",
]
