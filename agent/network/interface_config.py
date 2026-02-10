"""Interface configuration utilities for MTU management.

This module provides network manager detection and persistent MTU configuration
for physical host interfaces. Supports NetworkManager, netplan, and systemd-networkd.

When running in a container with pid:host, uses nsenter to access the host's
network management tools and configuration files.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

NetworkManager = Literal["networkmanager", "netplan", "systemd-networkd", "unknown"]


def _is_in_container() -> bool:
    """Detect if we're running inside a container.

    Returns:
        True if running in a container, False otherwise
    """
    # Check for /.dockerenv file (Docker)
    if Path("/.dockerenv").exists():
        return True

    # Check cgroup for container indicators
    try:
        cgroup_path = Path("/proc/1/cgroup")
        if cgroup_path.exists():
            content = cgroup_path.read_text()
            if "docker" in content or "kubepods" in content or "containerd" in content:
                return True
    except Exception:
        pass

    # Check for container environment variable
    if os.environ.get("container"):
        return True

    return False


def _run_on_host(cmd: list[str], timeout: int = 10) -> subprocess.CompletedProcess:
    """Run a command, using nsenter if in a container with pid:host.

    When running in a container with pid:host, this uses nsenter to execute
    the command in the host's mount namespace, allowing access to host tools
    like nmcli, netplan, and systemctl.

    Args:
        cmd: Command and arguments to run
        timeout: Command timeout in seconds

    Returns:
        CompletedProcess result
    """
    if _is_in_container():
        # Use nsenter to run in host's mount namespace
        # -t 1: target PID 1 (init process, which is the host's init due to pid:host)
        # -m: enter mount namespace (access host's filesystem)
        # --: end of nsenter options
        nsenter_cmd = ["nsenter", "-t", "1", "-m", "--"] + cmd
        logger.debug(f"Running via nsenter: {' '.join(nsenter_cmd)}")
        return subprocess.run(
            nsenter_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    else:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )


def _host_path_exists(path: str) -> bool:
    """Check if a path exists on the host filesystem.

    Args:
        path: Path to check

    Returns:
        True if path exists on host
    """
    if _is_in_container():
        result = _run_on_host(["test", "-e", path], timeout=5)
        return result.returncode == 0
    else:
        return Path(path).exists()


def _host_glob(directory: str, pattern: str) -> list[str]:
    """Glob files in a directory on the host filesystem.

    Args:
        directory: Directory to search
        pattern: Glob pattern

    Returns:
        List of matching file paths
    """
    if _is_in_container():
        # Use find command via nsenter
        result = _run_on_host(
            ["find", directory, "-maxdepth", "1", "-name", pattern, "-type", "f"],
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().split("\n")
        return []
    else:
        return [str(p) for p in Path(directory).glob(pattern)]


def _host_read_file(path: str) -> str | None:
    """Read a file from the host filesystem.

    Args:
        path: Path to read

    Returns:
        File contents or None if not readable
    """
    if _is_in_container():
        result = _run_on_host(["cat", path], timeout=5)
        if result.returncode == 0:
            return result.stdout
        return None
    else:
        try:
            return Path(path).read_text()
        except Exception:
            return None


def _host_write_file(path: str, content: str) -> tuple[bool, str | None]:
    """Write content to a file on the host filesystem.

    Args:
        path: Path to write
        content: Content to write

    Returns:
        Tuple of (success, error_message)
    """
    if _is_in_container():
        # Use tee to write via nsenter
        # We pass content via stdin
        try:
            result = subprocess.run(
                ["nsenter", "-t", "1", "-m", "--", "tee", path],
                input=content,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return False, result.stderr
            return True, None
        except Exception as e:
            return False, str(e)
    else:
        try:
            Path(path).write_text(content)
            return True, None
        except Exception as e:
            return False, str(e)


def _host_mkdir(path: str) -> tuple[bool, str | None]:
    """Create a directory on the host filesystem.

    Args:
        path: Directory path to create

    Returns:
        Tuple of (success, error_message)
    """
    if _is_in_container():
        result = _run_on_host(["mkdir", "-p", path], timeout=5)
        if result.returncode != 0:
            return False, result.stderr
        return True, None
    else:
        try:
            Path(path).mkdir(parents=True, exist_ok=True)
            return True, None
        except Exception as e:
            return False, str(e)


def detect_network_manager() -> NetworkManager:
    """Detect which network manager is in use on this system.

    When running in a container with pid:host, uses nsenter to detect
    the host's network manager.

    Returns:
        One of: "networkmanager", "netplan", "systemd-networkd", "unknown"
    """
    in_container = _is_in_container()
    if in_container:
        logger.debug("Running in container, using nsenter for network manager detection")

    # Check for NetworkManager (nmcli)
    try:
        result = _run_on_host(["nmcli", "-t", "-f", "RUNNING", "general"], timeout=5)
        if result.returncode == 0 and "running" in result.stdout.lower():
            logger.debug("Detected NetworkManager as network manager")
            return "networkmanager"
    except Exception as e:
        logger.debug(f"NetworkManager check failed: {e}")

    # Check for netplan (Ubuntu)
    netplan_files = _host_glob("/etc/netplan", "*.yaml")
    if netplan_files:
        # Check if netplan command is available
        try:
            result = _run_on_host(["which", "netplan"], timeout=5)
            if result.returncode == 0:
                logger.debug("Detected netplan as network manager")
                return "netplan"
        except Exception as e:
            logger.debug(f"netplan check failed: {e}")

    # Check for systemd-networkd
    try:
        result = _run_on_host(["systemctl", "is-active", "systemd-networkd"], timeout=5)
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

    When running in a container, uses nsenter to run nmcli on the host.

    Args:
        interface: Network interface name
        mtu: MTU value to set

    Returns:
        Tuple of (success, error_message)
    """
    def _sync_set_mtu() -> tuple[bool, str | None]:
        try:
            # First, find the connection name for this interface
            result = _run_on_host(
                ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show"],
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
            result = _run_on_host(
                ["nmcli", "connection", "modify", connection_name, "802-3-ethernet.mtu", str(mtu)],
                timeout=10,
            )
            if result.returncode != 0:
                return False, f"Failed to modify connection: {result.stderr}"

            # Apply the changes (reactivate connection)
            result = _run_on_host(
                ["nmcli", "connection", "up", connection_name],
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

    When running in a container, uses nsenter to access host's netplan config.

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

        netplan_dir = "/etc/netplan"

        # Find which netplan file contains this interface
        config_file = None
        config_data = None

        yaml_files = _host_glob(netplan_dir, "*.yaml")
        for yaml_file in yaml_files:
            try:
                content = _host_read_file(yaml_file)
                if not content:
                    continue

                data = yaml.safe_load(content)
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
            config_file = f"{netplan_dir}/90-archetype-{interface}.yaml"
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
            yaml_content = yaml.dump(config_data, default_flow_style=False)
            success, error = _host_write_file(config_file, yaml_content)
            if not success:
                return False, f"Failed to write netplan config: {error}"

            # Apply netplan
            result = _run_on_host(["netplan", "apply"], timeout=30)
            if result.returncode != 0:
                return False, f"netplan apply failed: {result.stderr}"

            return True, None

        except Exception as e:
            return False, str(e)

    return await asyncio.to_thread(_sync_set_mtu)


async def set_mtu_persistent_systemd_networkd(interface: str, mtu: int) -> tuple[bool, str | None]:
    """Apply persistent MTU configuration via systemd-networkd.

    When running in a container, uses nsenter to access host's systemd-networkd config.

    Args:
        interface: Network interface name
        mtu: MTU value to set

    Returns:
        Tuple of (success, error_message)
    """
    def _sync_set_mtu() -> tuple[bool, str | None]:
        networkd_dir = "/etc/systemd/network"

        # Ensure directory exists
        success, error = _host_mkdir(networkd_dir)
        if not success:
            return False, f"Failed to create networkd directory: {error}"

        # Look for existing .network file for this interface
        config_file = None
        network_files = _host_glob(networkd_dir, "*.network")
        for network_file in network_files:
            try:
                content = _host_read_file(network_file)
                if not content:
                    continue
                # Check if this file matches our interface
                if f"Name={interface}" in content:
                    config_file = network_file
                    break
            except Exception:
                continue

        if not config_file:
            # Create new config file
            # Use high number to ensure it's processed after others
            config_file = f"{networkd_dir}/90-archetype-{interface}.network"
            content = f"""[Match]
Name={interface}

[Link]
MTUBytes={mtu}
"""
        else:
            # Update existing file
            content = _host_read_file(config_file)
            if not content:
                return False, f"Failed to read existing config: {config_file}"

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
            success, error = _host_write_file(config_file, content)
            if not success:
                return False, f"Failed to write config: {error}"

            # Reload systemd-networkd
            result = _run_on_host(["systemctl", "reload", "systemd-networkd"], timeout=30)
            if result.returncode != 0:
                # Try restart instead
                result = _run_on_host(["systemctl", "restart", "systemd-networkd"], timeout=30)
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
