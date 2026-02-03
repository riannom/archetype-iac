"""Network module for lab connectivity.

This module provides networking capabilities for labs:
- Docker OVS plugin for pre-boot interface provisioning
- Overlay (VXLAN) networking for multi-host connectivity
- Local networking (veth pairs) for intra-host container links
- VLAN management for external network connectivity
- Cleanup utilities for orphaned network resources
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
    cleanup_external_networks,
)
from agent.network.docker_plugin import (
    DockerOVSPlugin,
    get_docker_ovs_plugin,
    run_plugin_standalone,
)
from agent.network.cleanup import (
    NetworkCleanupManager,
    CleanupStats,
    get_cleanup_manager,
)

__all__ = [
    # Docker OVS plugin (pre-boot interface provisioning)
    "DockerOVSPlugin",
    "get_docker_ovs_plugin",
    "run_plugin_standalone",
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
    "cleanup_external_networks",
    # Cleanup utilities
    "NetworkCleanupManager",
    "CleanupStats",
    "get_cleanup_manager",
]
