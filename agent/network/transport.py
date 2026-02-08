"""Data plane transport module for VXLAN endpoint IP management.

Provides a fallback chain for determining which IP address VXLAN
tunnels should bind to:
  1. Explicitly configured data_plane_ip (set by controller via transport config)
  2. settings.local_ip (agent env var ARCHETYPE_AGENT_LOCAL_IP)
  3. Auto-detected default route IP

This separation allows VXLAN traffic to use a different interface
(e.g., a jumbo-frame VLAN subinterface) than the management interface
used for agent-to-controller communication.
"""
from __future__ import annotations

import logging
import socket

logger = logging.getLogger(__name__)

# Module-level state: set by controller-driven transport configuration
_data_plane_ip: str | None = None


def set_data_plane_ip(ip: str | None) -> None:
    """Set the data plane IP for VXLAN tunnels.

    Called during agent bootstrap after transport config is applied.
    """
    global _data_plane_ip
    _data_plane_ip = ip
    if ip:
        logger.info(f"Data plane IP set to {ip}")
    else:
        logger.info("Data plane IP cleared, will use fallback chain")


def get_data_plane_ip() -> str | None:
    """Get the explicitly configured data plane IP, or None."""
    return _data_plane_ip


def get_vxlan_local_ip() -> str:
    """Get the IP address for VXLAN tunnel endpoints.

    Fallback chain:
    1. data_plane_ip (set by transport config from controller)
    2. settings.local_ip (ARCHETYPE_AGENT_LOCAL_IP env var)
    3. Auto-detect from default route

    Returns:
        IP address string to use as VXLAN local endpoint.
    """
    # 1. Data plane IP from transport config
    if _data_plane_ip:
        return _data_plane_ip

    # 2. settings.local_ip
    from agent.config import settings
    if settings.local_ip:
        return settings.local_ip

    # 3. Auto-detect
    return _detect_local_ip()


def _detect_local_ip() -> str:
    """Detect the local IP address by connecting to a known external address.

    Uses UDP socket trick (no actual data is sent) to find the IP address
    of the interface that would be used to reach 8.8.8.8.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        logger.warning("Could not auto-detect local IP, using 127.0.0.1")
        return "127.0.0.1"
