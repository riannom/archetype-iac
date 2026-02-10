"""Network backend registry and selector."""

from __future__ import annotations

import logging

from agent.config import settings
from agent.network.backends.base import NetworkBackend
from agent.network.backends.ovs_backend import OVSBackend
from agent.registry import LazySingleton


logger = logging.getLogger(__name__)

def _build_backend() -> NetworkBackend:
    backend_name = (getattr(settings, "network_backend", "ovs") or "ovs").lower()
    if backend_name != "ovs":
        logger.warning(f"Unsupported network backend '{backend_name}', falling back to 'ovs'")
        backend_name = "ovs"

    if backend_name == "ovs":
        return OVSBackend()

    # Fallback guard in case new backends are added without wiring.
    return OVSBackend()


_backend_singleton = LazySingleton(_build_backend)


def get_network_backend() -> NetworkBackend:
    """Return the configured network backend singleton."""
    return _backend_singleton.get()


def reset_network_backend() -> None:
    """Reset the backend singleton (mainly for testing)."""
    _backend_singleton.reset()
