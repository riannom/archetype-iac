"""Network backend abstraction for lab connectivity."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class NetworkBackend(ABC):
    """Abstract network backend interface.

    Concrete backends (e.g., OVS) should provide thin wrappers around
    existing networking managers without altering behavior.
    """

    name: str = ""

    @abstractmethod
    async def initialize(self) -> dict[str, Any]:
        """Initialize backend and recover state.

        Returns a dict of recovery/initialization info for logging.
        """

    @abstractmethod
    async def shutdown(self) -> None:
        """Shutdown backend and release resources."""

    @property
    @abstractmethod
    def overlay_manager(self) -> Any:
        """Expose overlay manager for legacy access."""

    @property
    @abstractmethod
    def ovs_manager(self) -> Any:
        """Expose OVS manager for legacy access."""

    @property
    @abstractmethod
    def plugin_running(self) -> bool:
        """Whether a backend-managed plugin is running."""

    @abstractmethod
    def ovs_initialized(self) -> bool:
        """Return True if OVS manager is initialized."""

    @abstractmethod
    async def ensure_ovs_initialized(self) -> None:
        """Ensure OVS manager is initialized."""

    @abstractmethod
    def get_ovs_status(self) -> dict[str, Any]:
        """Return OVS status data."""

    @abstractmethod
    def get_links_for_lab(self, lab_id: str) -> list[Any]:
        """Return OVS links for a lab."""

    @abstractmethod
    async def handle_container_restart(self, container_name: str, lab_id: str) -> dict[str, Any]:
        """Handle container restart for networking reprovisioning."""

    @abstractmethod
    async def connect_to_external(
        self,
        container_name: str,
        interface_name: str,
        external_interface: str,
        vlan_tag: int | None = None,
    ) -> int | None:
        """Connect a container interface to an external interface."""

    @abstractmethod
    async def create_patch_to_bridge(self, target_bridge: str, vlan_tag: int | None = None) -> str | None:
        """Create a patch port to another bridge."""

    @abstractmethod
    async def delete_patch_to_bridge(self, target_bridge: str) -> bool:
        """Delete a patch connection to another bridge."""

    @abstractmethod
    async def detach_external_interface(self, external_interface: str) -> bool:
        """Detach an external interface from the backend bridge."""

    @abstractmethod
    async def list_external_connections(self) -> list[dict[str, Any]]:
        """List external connections managed by the backend."""

    # Overlay wrappers
    @abstractmethod
    async def overlay_create_tunnel(
        self,
        lab_id: str,
        link_id: str,
        local_ip: str,
        remote_ip: str,
        vni: int | None = None,
    ) -> Any:
        """Create an overlay tunnel."""

    @abstractmethod
    async def overlay_create_bridge(self, tunnel: Any) -> None:
        """Create an overlay bridge for a tunnel."""

    @abstractmethod
    async def overlay_get_bridges_for_lab(self, lab_id: str) -> list[Any]:
        """Return overlay bridges for a lab."""

    @abstractmethod
    async def overlay_attach_container(
        self,
        bridge: Any,
        container_name: str,
        interface_name: str,
        ip_address: str | None = None,
    ) -> bool:
        """Attach a container interface to the overlay."""

    @abstractmethod
    async def overlay_cleanup_lab(self, lab_id: str) -> dict[str, Any]:
        """Cleanup overlay resources for a lab."""

    @abstractmethod
    def overlay_status(self) -> dict[str, Any]:
        """Return overlay status."""

    @abstractmethod
    def overlay_get_vtep(self, remote_ip: str) -> Any | None:
        """Get existing VTEP if present."""

    @abstractmethod
    async def overlay_ensure_vtep(
        self,
        local_ip: str,
        remote_ip: str,
        remote_host_id: str | None = None,
    ) -> Any:
        """Ensure a VTEP exists."""

    @abstractmethod
    async def overlay_attach_interface(
        self,
        lab_id: str,
        container_name: str,
        interface_name: str,
        vlan_tag: int,
        tenant_mtu: int | None,
        link_id: str,
        remote_ip: str,
    ) -> bool:
        """Attach an interface to overlay with VLAN tag."""

    @abstractmethod
    async def overlay_detach_interface(
        self,
        link_id: str,
        remote_ip: str,
        delete_vtep_if_unused: bool = True,
    ) -> dict[str, Any]:
        """Detach a link from overlay and manage VTEP state (legacy)."""

    @abstractmethod
    async def overlay_create_link_tunnel(
        self,
        lab_id: str,
        link_id: str,
        vni: int,
        local_ip: str,
        remote_ip: str,
        local_vlan: int,
        tenant_mtu: int = 0,
    ) -> Any:
        """Create a per-link access-mode VXLAN tunnel port."""

    @abstractmethod
    async def overlay_delete_link_tunnel(self, link_id: str, lab_id: str | None = None) -> bool:
        """Delete a per-link VXLAN tunnel port."""

    @abstractmethod
    def check_port_exists(self, port_name: str) -> bool:
        """Check if an OVS port exists on the bridge."""
