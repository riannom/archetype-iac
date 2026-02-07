"""OVS-backed network backend adapter."""

from __future__ import annotations

import logging
from typing import Any

from agent.config import settings
from agent.network.backends.base import NetworkBackend
from agent.network.docker_plugin import get_docker_ovs_plugin
from agent.network.overlay import OverlayManager
from agent.network.ovs import OVSNetworkManager
import subprocess


logger = logging.getLogger(__name__)


class OVSBackend(NetworkBackend):
    """Network backend that wraps existing OVS-based managers."""

    name = "ovs"

    def __init__(self) -> None:
        self._overlay = OverlayManager()
        self._ovs = OVSNetworkManager()
        self._plugin = get_docker_ovs_plugin()
        self._plugin_runner = None

    @property
    def overlay_manager(self) -> Any:
        return self._overlay

    @property
    def ovs_manager(self) -> Any:
        return self._ovs

    @property
    def plugin_running(self) -> bool:
        return self._plugin_runner is not None

    async def initialize(self) -> dict[str, Any]:
        info: dict[str, Any] = {
            "vnis_recovered": 0,
            "vlans_recovered": 0,
            "ovs_initialized": False,
            "ovs_plugin_started": False,
        }

        if settings.enable_vxlan:
            info["vnis_recovered"] = await self._overlay.recover_allocations()

        if settings.enable_ovs:
            await self._ovs.initialize()
            info["ovs_initialized"] = True
            info["vlans_recovered"] = await self._ovs.recover_allocations()

        if settings.enable_docker and settings.enable_ovs_plugin:
            self._plugin_runner = await self._plugin.start()
            info["ovs_plugin_started"] = True

        return info

    async def shutdown(self) -> None:
        if self._plugin_runner:
            try:
                await self._plugin.shutdown()
                await self._plugin_runner.cleanup()
                logger.info("Docker OVS network plugin stopped")
            finally:
                self._plugin_runner = None

    def ovs_initialized(self) -> bool:
        return bool(self._ovs._initialized)

    async def ensure_ovs_initialized(self) -> None:
        if not self._ovs._initialized:
            await self._ovs.initialize()

    def get_ovs_status(self) -> dict[str, Any]:
        return self._ovs.get_status()

    def get_links_for_lab(self, lab_id: str) -> list[Any]:
        return self._ovs.get_links_for_lab(lab_id)

    async def handle_container_restart(self, container_name: str, lab_id: str) -> dict[str, Any]:
        if not self._ovs._initialized:
            return {"reprovisioned_ports": 0, "reconnected_links": 0, "errors": []}
        return await self._ovs.handle_container_restart(container_name, lab_id)

    async def connect_to_external(
        self,
        container_name: str,
        interface_name: str,
        external_interface: str,
        vlan_tag: int | None = None,
    ) -> int | None:
        return await self._ovs.connect_to_external(
            container_name=container_name,
            interface_name=interface_name,
            external_interface=external_interface,
            vlan_tag=vlan_tag,
        )

    async def create_patch_to_bridge(self, target_bridge: str, vlan_tag: int | None = None) -> str | None:
        return await self._ovs.create_patch_to_bridge(
            target_bridge=target_bridge,
            vlan_tag=vlan_tag,
        )

    async def delete_patch_to_bridge(self, target_bridge: str) -> bool:
        return await self._ovs.delete_patch_to_bridge(target_bridge)

    async def detach_external_interface(self, external_interface: str) -> bool:
        return await self._ovs.detach_external_interface(external_interface)

    async def list_external_connections(self) -> list[dict[str, Any]]:
        return await self._ovs.list_external_connections()

    async def overlay_create_tunnel(
        self,
        lab_id: str,
        link_id: str,
        local_ip: str,
        remote_ip: str,
        vni: int | None = None,
    ) -> Any:
        return await self._overlay.create_tunnel(
            lab_id=lab_id,
            link_id=link_id,
            local_ip=local_ip,
            remote_ip=remote_ip,
            vni=vni,
        )

    async def overlay_create_bridge(self, tunnel: Any) -> None:
        await self._overlay.create_bridge(tunnel)

    async def overlay_get_bridges_for_lab(self, lab_id: str) -> list[Any]:
        return await self._overlay.get_bridges_for_lab(lab_id)

    async def overlay_attach_container(
        self,
        bridge: Any,
        container_name: str,
        interface_name: str,
        ip_address: str | None = None,
    ) -> bool:
        return await self._overlay.attach_container(
            bridge=bridge,
            container_name=container_name,
            interface_name=interface_name,
            ip_address=ip_address,
        )

    async def overlay_cleanup_lab(self, lab_id: str) -> dict[str, Any]:
        return await self._overlay.cleanup_lab(lab_id)

    def overlay_status(self) -> dict[str, Any]:
        return self._overlay.get_tunnel_status()

    def overlay_get_vtep(self, remote_ip: str) -> Any | None:
        return self._overlay.get_vtep(remote_ip)

    async def overlay_ensure_vtep(
        self,
        local_ip: str,
        remote_ip: str,
        remote_host_id: str | None = None,
    ) -> Any:
        return await self._overlay.ensure_vtep(
            local_ip=local_ip,
            remote_ip=remote_ip,
            remote_host_id=remote_host_id,
        )

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
        return await self._overlay.attach_overlay_interface(
            lab_id=lab_id,
            container_name=container_name,
            interface_name=interface_name,
            vlan_tag=vlan_tag,
            tenant_mtu=tenant_mtu,
            link_id=link_id,
            remote_ip=remote_ip,
        )

    async def overlay_detach_interface(
        self,
        link_id: str,
        remote_ip: str,
        delete_vtep_if_unused: bool = True,
    ) -> dict[str, Any]:
        return await self._overlay.detach_overlay_interface(
            link_id=link_id,
            remote_ip=remote_ip,
            delete_vtep_if_unused=delete_vtep_if_unused,
        )

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
        return await self._overlay.create_link_tunnel(
            lab_id=lab_id,
            link_id=link_id,
            vni=vni,
            local_ip=local_ip,
            remote_ip=remote_ip,
            local_vlan=local_vlan,
            tenant_mtu=tenant_mtu,
        )

    async def overlay_delete_link_tunnel(self, link_id: str) -> bool:
        return await self._overlay.delete_link_tunnel(link_id=link_id)

    def check_port_exists(self, port_name: str) -> bool:
        try:
            result = subprocess.run(
                ["ovs-vsctl", "port-to-br", port_name],
                capture_output=True,
                text=True,
            )
            return result.returncode == 0
        except Exception:
            return False
