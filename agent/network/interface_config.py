"""Interface configuration utilities for MTU management.

This module provides network manager detection and persistent MTU configuration
for physical host interfaces. Supports NetworkManager, netplan, and systemd-networkd.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

NetworkManager = Literal["networkmanager", "netplan", "systemd-networkd", "unknown"]


def detect_network_manager() -> NetworkManager:
    """Detect which network manager is in use on this system.

    Returns:
        One of: "networkmanager", "netplan", "systemd-networkd", "unknown"
    """
    # Check for NetworkManager (nmcli)
    if shutil.which("nmcli"):
        try:
            result = subprocess.run(
                ["nmcli", "-t", "-f", "RUNNING", "general"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and "running" in result.stdout.lower():
                logger.debug("Detected NetworkManager as network manager")
                return "networkmanager"
        except Exception as e:
            logger.debug(f"NetworkManager check failed: {e}")

    # Check for netplan (Ubuntu)
    netplan_dir = Path("/etc/netplan")
    if netplan_dir.exists() and any(netplan_dir.glob("*.yaml")):
        if shutil.which("netplan"):
            logger.debug("Detected netplan as network manager")
            return "netplan"

    # Check for systemd-networkd
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "systemd-networkd"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and "active" in result.stdout.strip():
            logger.debug("Detected systemd-networkd as network manager")
            return "systemd-networkd"
    except Exception as e:
        logger.debug(f"systemd-networkd check failed: {e}")

    logger.debug("Could not detect network manager")
    return "unknown"


async def set_mtu_runtime(interface: str, mtu: int) -> tuple[bool, str | None]:
    """Apply runtime-only MTU via ip command.

    Args:
        interface: Network interface name
        mtu: MTU value to set

    Returns:
        Tuple of (success, error_message)
    """
    def _sync_set_mtu() -> tuple[bool, str | None]:
        try:
            result = subprocess.run(
                ["ip", "link", "set", interface, "mtu", str(mtu)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                error = result.stderr.strip() or f"ip link set failed with code {result.returncode}"
                return False, error
            return True, None
        except subprocess.TimeoutExpired:
            return False, "Command timed out"
        except Exception as e:
            return False, str(e)

    return await asyncio.to_thread(_sync_set_mtu)


async def set_mtu_persistent_networkmanager(interface: str, mtu: int) -> tuple[bool, str | None]:
    """Apply persistent MTU configuration via NetworkManager.

    Args:
        interface: Network interface name
        mtu: MTU value to set

    Returns:
        Tuple of (success, error_message)
    """
    def _sync_set_mtu() -> tuple[bool, str | None]:
        try:
            # First, find the connection name for this interface
            result = subprocess.run(
                ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return False, f"Failed to list connections: {result.stderr}"

            connection_name = None
            for line in result.stdout.strip().split("\n"):
                if ":" in line:
                    name, device = line.rsplit(":", 1)
                    if device == interface:
                        connection_name = name
                        break

            if not connection_name:
                return False, f"No NetworkManager connection found for interface {interface}"

            # Modify the connection MTU
            result = subprocess.run(
                ["nmcli", "connection", "modify", connection_name, "802-3-ethernet.mtu", str(mtu)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return False, f"Failed to modify connection: {result.stderr}"

            # Apply the changes (reactivate connection)
            result = subprocess.run(
                ["nmcli", "connection", "up", connection_name],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                # Connection might briefly go down, but MTU should still be saved
                logger.warning(f"Connection reactivation warning: {result.stderr}")

            return True, None

        except subprocess.TimeoutExpired:
            return False, "Command timed out"
        except Exception as e:
            return False, str(e)

    return await asyncio.to_thread(_sync_set_mtu)


async def set_mtu_persistent_netplan(interface: str, mtu: int) -> tuple[bool, str | None]:
    """Apply persistent MTU configuration via netplan.

    Args:
        interface: Network interface name
        mtu: MTU value to set

    Returns:
        Tuple of (success, error_message)
    """
    def _sync_set_mtu() -> tuple[bool, str | None]:
        try:
            import yaml
        except ImportError:
            return False, "PyYAML not installed, cannot modify netplan"

        netplan_dir = Path("/etc/netplan")

        # Find which netplan file contains this interface
        config_file = None
        config_data = None

        for yaml_file in netplan_dir.glob("*.yaml"):
            try:
                with open(yaml_file) as f:
                    data = yaml.safe_load(f)
                    if not data or "network" not in data:
                        continue

                    network = data["network"]
                    for section in ["ethernets", "bonds", "bridges", "vlans"]:
                        if section in network and interface in network[section]:
                            config_file = yaml_file
                            config_data = data
                            break
                    if config_file:
                        break
            except Exception as e:
                logger.debug(f"Error reading {yaml_file}: {e}")
                continue

        if not config_file or not config_data:
            # Create a new config file for this interface
            config_file = netplan_dir / f"90-archetype-{interface}.yaml"
            config_data = {
                "network": {
                    "version": 2,
                    "ethernets": {
                        interface: {
                            "mtu": mtu,
                        }
                    }
                }
            }
        else:
            # Update existing config
            network = config_data["network"]
            for section in ["ethernets", "bonds", "bridges", "vlans"]:
                if section in network and interface in network[section]:
                    network[section][interface]["mtu"] = mtu
                    break

        try:
            # Write the config file
            with open(config_file, "w") as f:
                yaml.dump(config_data, f, default_flow_style=False)

            # Apply netplan
            result = subprocess.run(
                ["netplan", "apply"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                return False, f"netplan apply failed: {result.stderr}"

            return True, None

        except Exception as e:
            return False, str(e)

    return await asyncio.to_thread(_sync_set_mtu)


async def set_mtu_persistent_systemd_networkd(interface: str, mtu: int) -> tuple[bool, str | None]:
    """Apply persistent MTU configuration via systemd-networkd.

    Args:
        interface: Network interface name
        mtu: MTU value to set

    Returns:
        Tuple of (success, error_message)
    """
    def _sync_set_mtu() -> tuple[bool, str | None]:
        networkd_dir = Path("/etc/systemd/network")
        networkd_dir.mkdir(parents=True, exist_ok=True)

        # Look for existing .network file for this interface
        config_file = None
        for network_file in networkd_dir.glob("*.network"):
            try:
                content = network_file.read_text()
                # Check if this file matches our interface
                if f"Name={interface}" in content:
                    config_file = network_file
                    break
            except Exception:
                continue

        if not config_file:
            # Create new config file
            # Use high number to ensure it's processed after others
            config_file = networkd_dir / f"90-archetype-{interface}.network"
            content = f"""[Match]
Name={interface}

[Link]
MTUBytes={mtu}
"""
        else:
            # Update existing file
            content = config_file.read_text()

            # Check if [Link] section exists
            if "[Link]" in content:
                # Update or add MTUBytes in [Link] section
                if "MTUBytes=" in content:
                    # Replace existing MTUBytes
                    content = re.sub(
                        r"MTUBytes=\d+",
                        f"MTUBytes={mtu}",
                        content
                    )
                else:
                    # Add MTUBytes to [Link] section
                    content = content.replace(
                        "[Link]",
                        f"[Link]\nMTUBytes={mtu}"
                    )
            else:
                # Add [Link] section
                content += f"\n[Link]\nMTUBytes={mtu}\n"

        try:
            config_file.write_text(content)

            # Reload systemd-networkd
            result = subprocess.run(
                ["systemctl", "reload", "systemd-networkd"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                # Try restart instead
                result = subprocess.run(
                    ["systemctl", "restart", "systemd-networkd"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if result.returncode != 0:
                    return False, f"Failed to reload systemd-networkd: {result.stderr}"

            return True, None

        except Exception as e:
            return False, str(e)

    return await asyncio.to_thread(_sync_set_mtu)


async def set_mtu_persistent(interface: str, mtu: int, network_manager: NetworkManager) -> tuple[bool, str | None]:
    """Apply persistent MTU configuration based on detected network manager.

    Args:
        interface: Network interface name
        mtu: MTU value to set
        network_manager: Detected network manager type

    Returns:
        Tuple of (success, error_message)
    """
    if network_manager == "networkmanager":
        return await set_mtu_persistent_networkmanager(interface, mtu)
    elif network_manager == "netplan":
        return await set_mtu_persistent_netplan(interface, mtu)
    elif network_manager == "systemd-networkd":
        return await set_mtu_persistent_systemd_networkd(interface, mtu)
    else:
        return False, "Unknown network manager - cannot persist MTU configuration"


def get_interface_mtu(interface: str) -> int | None:
    """Get the current MTU of an interface.

    Args:
        interface: Network interface name

    Returns:
        Current MTU value, or None if interface not found
    """
    try:
        mtu_path = Path(f"/sys/class/net/{interface}/mtu")
        if mtu_path.exists():
            return int(mtu_path.read_text().strip())
    except Exception as e:
        logger.debug(f"Failed to read MTU for {interface}: {e}")
    return None


def get_interface_max_mtu(interface: str) -> int | None:
    """Get the maximum MTU supported by an interface.

    Args:
        interface: Network interface name

    Returns:
        Maximum MTU value, or None if not available
    """
    try:
        # Try ethtool first
        result = subprocess.run(
            ["ethtool", "-i", interface],
            capture_output=True,
            text=True,
            timeout=5,
        )
        # Max MTU isn't directly available from ethtool -i,
        # but we can try ip link to see if there's a max

        result = subprocess.run(
            ["ip", "-j", "-d", "link", "show", interface],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            import json
            data = json.loads(result.stdout)
            if data and len(data) > 0:
                # Some drivers report max_mtu
                return data[0].get("max_mtu")
    except Exception as e:
        logger.debug(f"Failed to get max MTU for {interface}: {e}")
    return None


def is_physical_interface(interface: str) -> bool:
    """Check if an interface is a physical interface.

    Args:
        interface: Network interface name

    Returns:
        True if physical interface, False otherwise
    """
    # Virtual interfaces typically have specific prefixes or are in /sys/devices/virtual
    virtual_prefixes = (
        "lo", "docker", "veth", "br-", "virbr", "vxlan",
        "ovs-", "arch-", "clab", "tap", "tun", "bond", "team"
    )
    if interface.startswith(virtual_prefixes):
        return False

    # Check if it's in the virtual devices directory
    virtual_path = Path(f"/sys/devices/virtual/net/{interface}")
    if virtual_path.exists():
        return False

    # Check if it has a device symlink (physical interfaces do)
    device_path = Path(f"/sys/class/net/{interface}/device")
    return device_path.exists()


def get_default_route_interface() -> str | None:
    """Get the interface used for the default route.

    Returns:
        Interface name, or None if no default route
    """
    try:
        result = subprocess.run(
            ["ip", "-j", "route", "show", "default"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            import json
            routes = json.loads(result.stdout)
            if routes:
                return routes[0].get("dev")
    except Exception as e:
        logger.debug(f"Failed to get default route interface: {e}")
    return None
