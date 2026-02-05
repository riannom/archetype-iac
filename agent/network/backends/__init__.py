"""Network backend implementations and registry."""

from agent.network.backends.base import NetworkBackend
from agent.network.backends.registry import get_network_backend

__all__ = ["NetworkBackend", "get_network_backend"]
