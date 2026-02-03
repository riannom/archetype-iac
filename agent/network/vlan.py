"""VLAN interface management for external network connectivity.

This module handles creating and deleting VLAN sub-interfaces (802.1Q)
for connecting lab devices to external networks.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class VlanInterface:
    """Represents a VLAN sub-interface."""

    parent: str  # Parent interface (e.g., "eth0", "ens192")
    vlan_id: int  # VLAN ID (1-4094)
    lab_id: str  # Lab that owns this interface

    @property
    def name(self) -> str:
        """Get the interface name (e.g., 'eth0.100')."""
        return f"{self.parent}.{self.vlan_id}"


@dataclass
class VlanManager:
    """Manages VLAN sub-interfaces for external network connectivity.

    Tracks created interfaces per lab for proper cleanup on lab destruction.
    """

    # Track interfaces created per lab: lab_id -> set of interface names
    _interfaces_by_lab: dict[str, set[str]] = field(default_factory=dict)

    def _run_ip_command(self, args: list[str]) -> tuple[int, str, str]:
        """Run an ip command and return (returncode, stdout, stderr).

        This is the synchronous version, used internally.
        """
        cmd = ["ip"] + args
        logger.debug(f"Running: {' '.join(cmd)}")
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            logger.error(f"Command timed out: {' '.join(cmd)}")
            return 1, "", "Command timed out"
        except Exception as e:
            logger.error(f"Command failed: {' '.join(cmd)}: {e}")
            return 1, "", str(e)

    async def _run_ip_command_async(self, args: list[str]) -> tuple[int, str, str]:
        """Run an ip command asynchronously.

        Wraps _run_ip_command in asyncio.to_thread to avoid blocking.
        """
        return await asyncio.to_thread(self._run_ip_command, args)

    def interface_exists(self, name: str) -> bool:
        """Check if an interface exists."""
        returncode, _, _ = self._run_ip_command(["link", "show", name])
        return returncode == 0

    def create_vlan_interface(
        self,
        parent: str,
        vlan_id: int,
        lab_id: str,
    ) -> str | None:
        """Create a VLAN sub-interface.

        Args:
            parent: Parent interface name (e.g., "eth0", "ens192")
            vlan_id: VLAN ID (1-4094)
            lab_id: Lab ID for tracking ownership

        Returns:
            Interface name if created successfully, None otherwise
        """
        if not 1 <= vlan_id <= 4094:
            logger.error(f"Invalid VLAN ID: {vlan_id}")
            return None

        iface_name = f"{parent}.{vlan_id}"

        # Check if interface already exists
        if self.interface_exists(iface_name):
            logger.info(f"VLAN interface {iface_name} already exists")
            # Track it for this lab
            if lab_id not in self._interfaces_by_lab:
                self._interfaces_by_lab[lab_id] = set()
            self._interfaces_by_lab[lab_id].add(iface_name)
            return iface_name

        # Check if parent interface exists
        if not self.interface_exists(parent):
            logger.error(f"Parent interface {parent} does not exist")
            return None

        # Create the VLAN sub-interface
        # ip link add link eth0 name eth0.100 type vlan id 100
        returncode, stdout, stderr = self._run_ip_command([
            "link", "add", "link", parent,
            "name", iface_name,
            "type", "vlan", "id", str(vlan_id)
        ])

        if returncode != 0:
            logger.error(f"Failed to create VLAN interface {iface_name}: {stderr}")
            return None

        # Bring the interface up
        returncode, stdout, stderr = self._run_ip_command([
            "link", "set", iface_name, "up"
        ])

        if returncode != 0:
            logger.warning(f"Failed to bring up VLAN interface {iface_name}: {stderr}")
            # Interface was created but not up - try to clean up
            self._run_ip_command(["link", "delete", iface_name])
            return None

        logger.info(f"Created VLAN interface {iface_name} for lab {lab_id}")

        # Track the interface
        if lab_id not in self._interfaces_by_lab:
            self._interfaces_by_lab[lab_id] = set()
        self._interfaces_by_lab[lab_id].add(iface_name)

        return iface_name

    def delete_vlan_interface(self, name: str) -> bool:
        """Delete a VLAN sub-interface.

        Args:
            name: Interface name (e.g., "eth0.100")

        Returns:
            True if deleted successfully or didn't exist, False on error
        """
        if not self.interface_exists(name):
            logger.debug(f"VLAN interface {name} does not exist")
            return True

        # ip link delete eth0.100
        returncode, stdout, stderr = self._run_ip_command([
            "link", "delete", name
        ])

        if returncode != 0:
            logger.error(f"Failed to delete VLAN interface {name}: {stderr}")
            return False

        logger.info(f"Deleted VLAN interface {name}")

        # Remove from tracking
        for lab_interfaces in self._interfaces_by_lab.values():
            lab_interfaces.discard(name)

        return True

    def cleanup_lab(self, lab_id: str) -> list[str]:
        """Clean up all VLAN interfaces created for a lab.

        Args:
            lab_id: Lab ID to clean up

        Returns:
            List of interface names that were deleted
        """
        deleted = []
        interfaces = self._interfaces_by_lab.pop(lab_id, set())

        for iface_name in interfaces:
            if self.delete_vlan_interface(iface_name):
                deleted.append(iface_name)

        if deleted:
            logger.info(f"Cleaned up {len(deleted)} VLAN interfaces for lab {lab_id}")

        return deleted

    def get_lab_interfaces(self, lab_id: str) -> set[str]:
        """Get all VLAN interfaces tracked for a lab."""
        return self._interfaces_by_lab.get(lab_id, set()).copy()

    def list_all_interfaces(self) -> dict[str, set[str]]:
        """Get all tracked VLAN interfaces by lab."""
        return {lab: ifaces.copy() for lab, ifaces in self._interfaces_by_lab.items()}


# Global instance for use by the agent
_vlan_manager: VlanManager | None = None


def get_vlan_manager() -> VlanManager:
    """Get the global VLAN manager instance."""
    global _vlan_manager
    if _vlan_manager is None:
        _vlan_manager = VlanManager()
    return _vlan_manager



async def cleanup_external_networks(lab_id: str) -> list[str]:
    """Clean up external network interfaces for a lab.

    Args:
        lab_id: Lab ID

    Returns:
        List of deleted interface names
    """
    def _sync_cleanup() -> list[str]:
        manager = get_vlan_manager()
        return manager.cleanup_lab(lab_id)

    return await asyncio.to_thread(_sync_cleanup)
