"""Plugin loader for discovering and loading vendor plugins.

This module handles automatic discovery of vendor plugins from:
1. Built-in plugins (agent/plugins/builtin/)
2. Entry points (agent.plugins namespace)
3. Custom plugin directories

Usage:
    from agent.plugins.loader import load_all_plugins, load_builtin_plugins

    # Load only built-in plugins
    load_builtin_plugins()

    # Load all plugins including entry points
    load_all_plugins()
"""
from __future__ import annotations

import importlib
import logging
import pkgutil
from pathlib import Path
from typing import Iterator

from agent.plugins import VendorPlugin, register_plugin, _plugin_registry

logger = logging.getLogger(__name__)


def _discover_builtin_plugins() -> Iterator[type[VendorPlugin]]:
    """Discover built-in plugin classes from agent/plugins/builtin/.

    Yields:
        Plugin classes found in the builtin directory
    """
    try:
        import agent.plugins.builtin as builtin_package

        # Get the package path
        package_path = Path(builtin_package.__file__).parent

        # Iterate through all modules in the builtin package
        for module_info in pkgutil.iter_modules([str(package_path)]):
            if module_info.name.startswith("_"):
                continue

            try:
                module = importlib.import_module(
                    f"agent.plugins.builtin.{module_info.name}"
                )

                # Find VendorPlugin subclasses in the module
                for attr_name in dir(module):
                    if attr_name.startswith("_"):
                        continue

                    attr = getattr(module, attr_name)
                    if (
                        isinstance(attr, type)
                        and issubclass(attr, VendorPlugin)
                        and attr is not VendorPlugin
                    ):
                        yield attr

            except Exception as e:
                logger.warning(f"Failed to load builtin plugin module {module_info.name}: {e}")

    except ImportError as e:
        logger.debug(f"No builtin plugins package: {e}")


def _discover_entrypoint_plugins() -> Iterator[type[VendorPlugin]]:
    """Discover plugins from entry points.

    Entry points should be defined in setup.py or pyproject.toml:
        [project.entry-points."agent.plugins"]
        myvendor = "mypackage.plugins:MyVendorPlugin"

    Yields:
        Plugin classes found in entry points
    """
    try:
        # Python 3.10+ has importlib.metadata
        from importlib.metadata import entry_points

        # Get entry points for our namespace
        eps = entry_points()
        if hasattr(eps, "select"):
            # Python 3.10+
            plugin_eps = eps.select(group="agent.plugins")
        else:
            # Python 3.9
            plugin_eps = eps.get("agent.plugins", [])

        for ep in plugin_eps:
            try:
                plugin_class = ep.load()
                if issubclass(plugin_class, VendorPlugin):
                    yield plugin_class
            except Exception as e:
                logger.warning(f"Failed to load entry point plugin {ep.name}: {e}")

    except ImportError:
        # importlib.metadata not available
        pass


def load_builtin_plugins() -> list[VendorPlugin]:
    """Load only built-in plugins.

    Returns:
        List of loaded plugin instances
    """
    loaded = []

    for plugin_class in _discover_builtin_plugins():
        try:
            plugin = plugin_class()
            register_plugin(plugin)
            loaded.append(plugin)
            logger.info(f"Loaded builtin plugin: {plugin.metadata.name}")
        except Exception as e:
            logger.error(f"Failed to instantiate plugin {plugin_class}: {e}")

    return loaded


def load_entrypoint_plugins() -> list[VendorPlugin]:
    """Load plugins from entry points.

    Returns:
        List of loaded plugin instances
    """
    loaded = []

    for plugin_class in _discover_entrypoint_plugins():
        try:
            plugin = plugin_class()

            # Skip if already registered (e.g., if also in builtins)
            if plugin.metadata.name in _plugin_registry:
                logger.debug(f"Skipping duplicate plugin: {plugin.metadata.name}")
                continue

            register_plugin(plugin)
            loaded.append(plugin)
            logger.info(f"Loaded entrypoint plugin: {plugin.metadata.name}")
        except Exception as e:
            logger.error(f"Failed to instantiate plugin {plugin_class}: {e}")

    return loaded


def load_all_plugins() -> list[VendorPlugin]:
    """Load all available plugins.

    This loads plugins from:
    1. Built-in plugins directory
    2. Entry points

    Returns:
        List of all loaded plugin instances
    """
    loaded = []

    # Load builtin plugins first
    loaded.extend(load_builtin_plugins())

    # Then load entry point plugins
    loaded.extend(load_entrypoint_plugins())

    logger.info(f"Loaded {len(loaded)} vendor plugins")
    return loaded


def get_plugin_for_kind(kind: str) -> VendorPlugin | None:
    """Find the plugin that provides a specific device kind.

    Args:
        kind: Device kind (e.g., "ceos", "nokia_srlinux")

    Returns:
        Plugin that provides this kind, or None
    """
    for plugin in _plugin_registry.values():
        for config in plugin.vendor_configs:
            if config.kind == kind:
                return plugin
    return None
