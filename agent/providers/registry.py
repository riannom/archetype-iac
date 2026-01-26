"""Provider registry for managing infrastructure providers.

This module provides a singleton registry that handles lazy loading and
management of provider instances based on configuration settings.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.providers.base import Provider

logger = logging.getLogger(__name__)


class ProviderRegistry:
    """Singleton registry for infrastructure providers.

    Providers are lazily instantiated based on configuration settings.
    This allows the agent to only load providers that are actually enabled.
    """

    _instance: ProviderRegistry | None = None
    _providers: dict[str, Provider]
    _discovered: bool

    def __new__(cls) -> ProviderRegistry:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._providers = {}
            cls._instance._discovered = False
        return cls._instance

    def _discover_providers(self) -> None:
        """Lazily discover and instantiate enabled providers."""
        if self._discovered:
            return

        from agent.config import settings

        if settings.enable_containerlab:
            try:
                from agent.providers.containerlab import ContainerlabProvider
                provider = ContainerlabProvider()
                self._providers["containerlab"] = provider
                logger.info(f"Registered provider: containerlab")
            except Exception as e:
                logger.error(f"Failed to initialize containerlab provider: {e}")

        if settings.enable_libvirt:
            try:
                from agent.providers.libvirt import LibvirtProvider
                provider = LibvirtProvider()
                self._providers["libvirt"] = provider
                logger.info(f"Registered provider: libvirt")
            except ImportError:
                logger.warning("Libvirt provider not available (module not installed)")
            except Exception as e:
                logger.error(f"Failed to initialize libvirt provider: {e}")

        self._discovered = True
        logger.info(f"Provider discovery complete: {list(self._providers.keys())}")

    def get(self, name: str) -> Provider | None:
        """Get a provider by name.

        Args:
            name: Provider name (e.g., 'containerlab', 'libvirt')

        Returns:
            Provider instance if available, None otherwise
        """
        self._discover_providers()
        return self._providers.get(name)

    def list_available(self) -> list[str]:
        """List all available provider names.

        Returns:
            List of provider names that are available
        """
        self._discover_providers()
        return list(self._providers.keys())

    def is_available(self, name: str) -> bool:
        """Check if a provider is available.

        Args:
            name: Provider name to check

        Returns:
            True if provider is available, False otherwise
        """
        self._discover_providers()
        return name in self._providers

    def get_default(self) -> Provider | None:
        """Get the default provider (first available).

        Returns:
            First available provider, or None if no providers
        """
        self._discover_providers()
        providers = list(self._providers.values())
        return providers[0] if providers else None

    def reset(self) -> None:
        """Reset the registry (mainly for testing)."""
        self._providers = {}
        self._discovered = False


# Module-level singleton instance
_registry = ProviderRegistry()


def get_provider(name: str) -> Provider | None:
    """Get a provider by name.

    Convenience function for accessing the global registry.

    Args:
        name: Provider name

    Returns:
        Provider instance if available, None otherwise
    """
    return _registry.get(name)


def get_default_provider() -> Provider | None:
    """Get the default provider.

    Convenience function for accessing the global registry.

    Returns:
        Default provider instance, or None if no providers
    """
    return _registry.get_default()


def list_providers() -> list[str]:
    """List all available providers.

    Convenience function for accessing the global registry.

    Returns:
        List of available provider names
    """
    return _registry.list_available()


def is_provider_available(name: str) -> bool:
    """Check if a provider is available.

    Convenience function for accessing the global registry.

    Args:
        name: Provider name to check

    Returns:
        True if provider is available
    """
    return _registry.is_available(name)
