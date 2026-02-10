"""Plugin architecture for vendor configurations.

This module defines the plugin interface for extending Archetype with
new network device vendors. Plugins can provide:
- Device configuration metadata
- Container lifecycle hooks
- Boot readiness detection
- Custom interface naming

Usage:
    from agent.plugins import VendorPlugin, PluginMetadata, VendorConfig

    class MyVendorPlugin(VendorPlugin):
        @property
        def metadata(self) -> PluginMetadata:
            return PluginMetadata(
                name="myvendor",
                version="1.0.0",
                description="My custom vendor plugin",
            )

        @property
        def vendor_configs(self) -> list[VendorConfig]:
            return [...]

Plugin Discovery:
    Plugins are discovered from:
    1. Built-in plugins in agent/plugins/builtin/
    2. Custom plugins in configured plugin directories
    3. Entry points (agent.plugins namespace)
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DeviceType(Enum):
    """Device type categories."""
    ROUTER = "router"
    SWITCH = "switch"
    FIREWALL = "firewall"
    HOST = "host"
    CONTAINER = "container"
    VM = "vm"


@dataclass
class PluginMetadata:
    """Metadata about a vendor plugin."""
    name: str
    version: str
    description: str = ""
    author: str = ""
    url: str = ""


@dataclass
class VendorConfig:
    """Configuration for a vendor device type.

    This dataclass contains all the information needed to support
    a specific network device type in Archetype.
    """
    # Required fields
    kind: str  # Unique identifier (e.g., "ceos", "nokia_srlinux")
    vendor: str  # Vendor name (e.g., "Arista", "Nokia")
    device_type: DeviceType

    # Display fields
    label: str | None = None  # Display name (defaults to vendor)
    icon: str = "fa-box"  # FontAwesome icon class

    # Runtime fields
    image_prefix: str | None = None  # Docker image prefix
    versions: list[str] = field(default_factory=lambda: ["latest"])
    is_active: bool = True

    # Interface configuration
    port_naming: str = "eth"  # Interface naming pattern
    port_start_index: int = 0  # First interface number
    max_ports: int = 32  # Maximum interfaces

    # Resource requirements
    memory: int = 1024  # Memory in MB
    cpu: int = 1  # CPU cores

    # Image configuration
    requires_image: bool = True  # User must provide image
    supported_image_kinds: list[str] = field(default_factory=lambda: ["docker"])
    license_required: bool = False

    # Boot readiness
    console_shell: str | None = None  # Shell for console access
    readiness_probe: str | None = None  # Command to check boot readiness
    readiness_pattern: str | None = None  # Log pattern indicating boot complete
    readiness_timeout: int = 300  # Boot timeout in seconds

    # Documentation
    documentation_url: str | None = None
    notes: str | None = None
    tags: list[str] = field(default_factory=list)

    # Vendor-specific options
    vendor_options: dict[str, Any] = field(default_factory=dict)


class VendorPlugin(ABC):
    """Abstract base class for vendor plugins.

    Implement this class to add support for a new vendor's devices.
    The plugin system will discover and load your implementation.
    """

    @property
    @abstractmethod
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata."""
        pass

    @property
    @abstractmethod
    def vendor_configs(self) -> list[VendorConfig]:
        """Return list of vendor configurations this plugin provides."""
        pass

    # --- Optional Lifecycle Hooks ---

    def on_container_create(self, container_name: str, config: dict) -> dict:
        """Hook called before container creation.

        Can modify the container configuration (e.g., add env vars, mounts).

        Args:
            container_name: Name of the container being created
            config: Container configuration dict

        Returns:
            Modified container configuration
        """
        return config

    def on_container_start(self, container_name: str) -> None:
        """Hook called after container starts.

        Can perform post-start actions (e.g., apply configs, wait for boot).

        Args:
            container_name: Name of the started container
        """
        pass

    def on_boot_ready(self, container_name: str) -> None:
        """Hook called when container is detected as boot-ready.

        Can perform actions after boot completes (e.g., apply baseline config).

        Args:
            container_name: Name of the ready container
        """
        pass

    def on_container_stop(self, container_name: str) -> None:
        """Hook called before container stops.

        Can perform cleanup actions (e.g., save running config).

        Args:
            container_name: Name of the container being stopped
        """
        pass

    def on_container_remove(self, container_name: str) -> None:
        """Hook called before container removal.

        Can perform cleanup actions (e.g., cleanup external resources).

        Args:
            container_name: Name of the container being removed
        """
        pass

    # --- Optional Customization Methods ---

    def get_interface_name(self, index: int, config: VendorConfig) -> str:
        """Generate interface name for the given index.

        Override this method for custom interface naming logic.

        Args:
            index: Interface index (1-based)
            config: Vendor configuration

        Returns:
            Interface name (e.g., "Ethernet1", "e1-1")
        """
        return f"{config.port_naming}{config.port_start_index + index - 1}"

    def is_boot_ready(self, container_name: str, logs: str) -> bool:
        """Check if container has completed boot.

        Override for custom boot detection logic.

        Args:
            container_name: Name of the container
            logs: Recent container logs

        Returns:
            True if boot is complete
        """
        config = self._get_config_for_container(container_name)
        if config and config.readiness_pattern:
            import re
            return bool(re.search(config.readiness_pattern, logs))
        return False

    def _get_config_for_container(self, container_name: str) -> VendorConfig | None:
        """Get vendor config for a container. Override if needed."""
        return None


# Registry of loaded plugins
_plugin_registry: dict[str, VendorPlugin] = {}


def register_plugin(plugin: VendorPlugin) -> None:
    """Register a vendor plugin.

    Args:
        plugin: Plugin instance to register
    """
    _plugin_registry[plugin.metadata.name] = plugin


def get_plugin(name: str) -> VendorPlugin | None:
    """Get a registered plugin by name.

    Args:
        name: Plugin name

    Returns:
        Plugin instance or None if not found
    """
    return _plugin_registry.get(name)


def get_all_plugins() -> list[VendorPlugin]:
    """Get all registered plugins.

    Returns:
        List of all registered plugin instances
    """
    return list(_plugin_registry.values())


def get_all_vendor_configs() -> list[VendorConfig]:
    """Get all vendor configurations from all plugins.

    Returns:
        Combined list of vendor configs from all plugins
    """
    configs = []
    for plugin in _plugin_registry.values():
        configs.extend(plugin.vendor_configs)
    return configs
