"""Network backend registry and selector."""

from __future__ import annotations

import logging

from agent.config import settings
from agent.network.backends.base import NetworkBackend
from agent.network.backends.ovs_backend import OVSBackend


logger = logging.getLogger(__name__)

_backend_instance: NetworkBackend | None = None


def get_network_backend() -> NetworkBackend:
    """Return the configured network backend singleton."""
    global _backend_instance
    if _backend_instance is not None:
        return _backend_instance

    backend_name = (getattr(settings, "network_backend", "ovs") or "ovs").lower()
    if backend_name != "ovs":
        logger.warning(f"Unsupported network backend '{backend_name}', falling back to 'ovs'")
        backend_name = "ovs"

    if backend_name == "ovs":
        _backend_instance = OVSBackend()

    return _backend_instance
