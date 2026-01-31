"""Infrastructure providers for the agent."""

from agent.providers.base import (
    DeployResult,
    DestroyResult,
    NodeActionResult,
    NodeInfo,
    NodeStatus,
    Provider,
    StatusResult,
)
from agent.providers.docker import DockerProvider
from agent.providers.registry import (
    ProviderRegistry,
    get_default_provider,
    get_provider,
    is_provider_available,
    list_providers,
)

__all__ = [
    # Base classes and types
    "Provider",
    "DeployResult",
    "DestroyResult",
    "NodeActionResult",
    "NodeInfo",
    "NodeStatus",
    "StatusResult",
    # Provider implementations
    "DockerProvider",
    # Registry
    "ProviderRegistry",
    "get_provider",
    "get_default_provider",
    "list_providers",
    "is_provider_available",
]
