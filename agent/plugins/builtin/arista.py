"""Arista vendor plugin.

This plugin provides support for Arista cEOS (containerized EOS).
It's an example of how vendor plugins work and provides actual
Arista-specific functionality.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from agent.plugins import (
    DeviceType,
    PluginMetadata,
    VendorConfig,
    VendorPlugin,
)

logger = logging.getLogger(__name__)


class AristaPlugin(VendorPlugin):
    """Plugin for Arista Networks devices."""

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="arista",
            version="1.0.0",
            description="Support for Arista EOS devices",
            author="Archetype",
            url="https://www.arista.com/",
        )

    @property
    def vendor_configs(self) -> list[VendorConfig]:
        return [
            VendorConfig(
                kind="ceos",
                vendor="Arista",
                device_type=DeviceType.SWITCH,
                label="Arista EOS",
                icon="fa-arrows-left-right-to-line",
                versions=["4.32", "4.31", "4.30", "4.29", "4.28", "latest"],
                port_naming="Ethernet",
                port_start_index=1,
                max_ports=64,
                memory=2048,
                cpu=1,
                console_shell="/bin/Cli",
                readiness_pattern=r"System ready|Aboot: Starting EOS agent",
                readiness_timeout=180,
                documentation_url="https://www.arista.com/en/products/software-controlled-container-networking",
                license_required=False,
                tags=["arista", "eos", "ceos", "switch", "datacenter"],
                notes="Arista cEOS - containerized EOS for lab environments",
                vendor_options={
                    "zerotouchCancel": True,
                    "intfType": "eth",
                    "mgmtIntf": "eth0",
                    "ceosEnv": {
                        "CEOS": "1",
                        "EOS_PLATFORM": "ceoslab",
                        "INTFTYPE": "eth",
                        "MGMT_INTF": "eth0",
                    },
                },
            ),
        ]

    def on_container_create(self, container_name: str, config: dict) -> dict:
        """Add cEOS-specific environment variables and mounts."""
        # Add cEOS environment variables
        env = config.get("environment", {})
        for ceos_config in self.vendor_configs:
            if ceos_config.kind == "ceos":
                ceos_env = ceos_config.vendor_options.get("ceosEnv", {})
                for key, value in ceos_env.items():
                    if key not in env:
                        env[key] = value
        config["environment"] = env

        return config

    def on_container_start(self, container_name: str) -> None:
        """Perform post-start actions for cEOS."""
        # Could add iptables cleanup for the EOS_FORWARD rule here
        logger.debug(f"cEOS container started: {container_name}")

    def on_boot_ready(self, container_name: str) -> None:
        """Called when cEOS has completed boot."""
        logger.info(f"cEOS boot complete: {container_name}")

    def is_boot_ready(self, container_name: str, logs: str) -> bool:
        """Check if cEOS has completed boot."""
        # cEOS is ready when it logs "System ready" or starts EOS agent
        patterns = [
            r"System ready",
            r"Aboot: Starting EOS agent",
            r"Startup complete",
        ]
        for pattern in patterns:
            if re.search(pattern, logs, re.IGNORECASE):
                return True
        return False

    def get_interface_name(self, index: int, config: VendorConfig) -> str:
        """Generate Arista interface name.

        Args:
            index: Interface index (1-based)
            config: Vendor configuration

        Returns:
            Interface name (e.g., "Ethernet1", "Ethernet2")
        """
        # cEOS uses Ethernet<N> naming
        return f"Ethernet{index}"
