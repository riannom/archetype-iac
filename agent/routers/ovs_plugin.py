"""Docker OVS plugin endpoints."""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter

from agent.config import settings
from agent.helpers import get_workspace, _get_docker_ovs_plugin
from agent.providers import get_provider
from agent.schemas import (
    OVSStatusResponse,
    LinkInfo,
    LinkState,
    PluginHealthResponse,
    PluginBridgeInfo,
    PluginStatusResponse,
    PluginPortInfo,
    PluginLabPortsResponse,
    PluginFlowsResponse,
    PluginVxlanRequest,
    PluginVxlanResponse,
    PluginExternalAttachRequest,
    PluginExternalAttachResponse,
    PluginExternalInfo,
    PluginExternalListResponse,
    PluginMgmtNetworkInfo,
    PluginMgmtNetworkResponse,
    PluginMgmtAttachRequest,
    PluginMgmtAttachResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ovs-plugin"])


# --- OVS Status Endpoint ---

@router.get("/ovs/status")
async def ovs_status() -> OVSStatusResponse:
    """Get status of OVS networking on this agent.

    Returns information about the OVS bridge, provisioned ports,
    and active links.
    """
    if not settings.enable_ovs:
        return OVSStatusResponse(
            bridge_name="",
            initialized=False,
        )

    try:
        from agent.network.backends.registry import get_network_backend
        backend = get_network_backend()
        status = backend.get_ovs_status()

        from agent.schemas import OVSPortInfo
        ports = [
            OVSPortInfo(
                port_name=p["port_name"],
                container_name=p["container"],
                interface_name=p["interface"],
                vlan_tag=p["vlan_tag"],
                lab_id=p["lab_id"],
            )
            for p in status["ports"]
        ]

        links = [
            LinkInfo(
                link_id=lnk["link_id"],
                lab_id=lnk["lab_id"],
                source_node=lnk["port_a"].rsplit(":", 1)[0].split("-")[-1],
                source_interface=lnk["port_a"].rsplit(":", 1)[1] if ":" in lnk["port_a"] else "",
                target_node=lnk["port_b"].rsplit(":", 1)[0].split("-")[-1],
                target_interface=lnk["port_b"].rsplit(":", 1)[1] if ":" in lnk["port_b"] else "",
                state=LinkState.CONNECTED,
                vlan_tag=lnk["vlan_tag"],
            )
            for lnk in status["links"]
        ]

        return OVSStatusResponse(
            bridge_name=status["bridge"],
            initialized=status["initialized"],
            ports=ports,
            links=links,
            vlan_allocations=status["vlan_allocations"],
        )

    except Exception as e:
        logger.error(f"OVS status failed: {e}")
        return OVSStatusResponse(
            bridge_name="",
            initialized=False,
        )


@router.get("/labs/{lab_id}/boot-logs")
async def lab_boot_logs(lab_id: str):
    """Get recent boot logs for all nodes in a lab.

    Returns last 200 lines of container logs for each node,
    useful for diagnosing boot failures.
    """
    from agent.routers.console import _get_container_boot_logs

    result: dict[str, str | None] = {}

    docker_provider = get_provider("docker")
    if docker_provider:
        try:
            workspace = get_workspace(lab_id)
            status = await docker_provider.status(lab_id=lab_id, workspace=workspace)
            for node in status.nodes:
                logs = await _get_container_boot_logs(node.name, tail_lines=200)
                result[node.name] = logs
        except Exception as e:
            logger.warning(f"Failed to get Docker boot logs for lab {lab_id}: {e}")

    libvirt_provider = get_provider("libvirt")
    if libvirt_provider:
        try:
            workspace = get_workspace(lab_id)
            status = await libvirt_provider.status(lab_id=lab_id, workspace=workspace)
            for node in status.nodes:
                if node.name not in result:
                    result[node.name] = None
        except Exception as e:
            logger.warning(f"Failed to get libvirt boot logs for lab {lab_id}: {e}")

    return {"lab_id": lab_id, "boot_logs": result}


@router.get("/ovs/flows")
async def ovs_flows():
    """Get OVS flow table dump for diagnostics.

    Returns the output of ovs-ofctl dump-flows for the OVS bridge.
    """
    if not settings.enable_ovs:
        return {"bridge": "", "flows": "", "error": "OVS not enabled"}

    try:
        bridge = settings.ovs_bridge_name
        result = await asyncio.to_thread(
            lambda: __import__("subprocess").run(
                ["ovs-ofctl", "dump-flows", bridge],
                capture_output=True, text=True, timeout=10,
            )
        )
        return {
            "bridge": bridge,
            "flows": result.stdout,
            "error": result.stderr if result.returncode != 0 else None,
        }
    except Exception as e:
        logger.error(f"Failed to get OVS flows: {e}")
        return {"bridge": settings.ovs_bridge_name, "flows": "", "error": str(e)}


# --- Docker OVS Plugin Endpoints ---

@router.get("/ovs-plugin/health")
async def ovs_plugin_health() -> PluginHealthResponse:
    """Check Docker OVS plugin health."""
    if not settings.enable_ovs_plugin:
        return PluginHealthResponse(healthy=False)

    try:
        plugin = _get_docker_ovs_plugin()
        health = await plugin.health_check()

        return PluginHealthResponse(
            healthy=health["healthy"],
            checks=health["checks"],
            uptime_seconds=health["uptime_seconds"],
            started_at=health.get("started_at"),
        )

    except Exception as e:
        logger.error(f"OVS plugin health check failed: {e}")
        return PluginHealthResponse(healthy=False)


@router.get("/ovs-plugin/status")
async def ovs_plugin_status() -> PluginStatusResponse:
    """Get comprehensive Docker OVS plugin status."""
    if not settings.enable_ovs_plugin:
        return PluginStatusResponse(healthy=False)

    try:
        plugin = _get_docker_ovs_plugin()
        status = await plugin.get_plugin_status()

        bridges = [
            PluginBridgeInfo(
                lab_id=b["lab_id"],
                bridge_name=b["bridge_name"],
                port_count=b["port_count"],
                vlan_range_used=tuple(b["vlan_range_used"]),
                vxlan_tunnels=b["vxlan_tunnels"],
                external_interfaces=b["external_interfaces"],
                last_activity=b["last_activity"],
            )
            for b in status["bridges"]
        ]

        return PluginStatusResponse(
            healthy=status["healthy"],
            labs_count=status["labs_count"],
            endpoints_count=status["endpoints_count"],
            networks_count=status["networks_count"],
            management_networks_count=status["management_networks_count"],
            bridges=bridges,
            uptime_seconds=status["uptime_seconds"],
        )

    except Exception as e:
        logger.error(f"OVS plugin status failed: {e}")
        return PluginStatusResponse(healthy=False)


@router.get("/ovs-plugin/labs/{lab_id}")
async def ovs_plugin_lab_status(lab_id: str) -> PluginBridgeInfo | dict:
    """Get status of a specific lab's OVS bridge."""
    if not settings.enable_ovs_plugin:
        return {"error": "OVS plugin not enabled"}

    try:
        plugin = _get_docker_ovs_plugin()
        status = plugin.get_lab_status(lab_id)

        if not status:
            return {"error": f"Lab {lab_id} not found"}

        lab_bridge = plugin.lab_bridges.get(lab_id)
        if not lab_bridge:
            return {"error": f"Lab bridge not found for {lab_id}"}

        vlan_range_used = plugin.get_lab_vlan_range(lab_id)
        return PluginBridgeInfo(
            lab_id=lab_id,
            bridge_name=lab_bridge.bridge_name,
            port_count=len(status.get("endpoints", [])),
            vlan_range_used=vlan_range_used,
            vxlan_tunnels=len(lab_bridge.vxlan_tunnels),
            external_interfaces=list(lab_bridge.external_ports.keys()),
            last_activity=lab_bridge.last_activity.isoformat(),
        )

    except Exception as e:
        logger.error(f"OVS plugin lab status failed: {e}")
        return {"error": str(e)}


@router.get("/ovs-plugin/labs/{lab_id}/ports")
async def ovs_plugin_lab_ports(lab_id: str) -> PluginLabPortsResponse:
    """Get detailed port information for a lab."""
    if not settings.enable_ovs_plugin:
        return PluginLabPortsResponse(lab_id=lab_id, ports=[])

    try:
        plugin = _get_docker_ovs_plugin()
        ports_data = await plugin.get_lab_ports(lab_id)

        ports = [
            PluginPortInfo(
                port_name=p["port_name"],
                bridge_name=p.get("bridge_name"),
                container=p.get("container"),
                interface=p["interface"],
                vlan_tag=p["vlan_tag"],
                rx_bytes=p.get("rx_bytes", 0),
                tx_bytes=p.get("tx_bytes", 0),
            )
            for p in ports_data
        ]

        return PluginLabPortsResponse(lab_id=lab_id, ports=ports)

    except Exception as e:
        logger.error(f"OVS plugin lab ports failed: {e}")
        return PluginLabPortsResponse(lab_id=lab_id, ports=[])


@router.get("/ovs-plugin/labs/{lab_id}/flows")
async def ovs_plugin_lab_flows(lab_id: str) -> PluginFlowsResponse:
    """Get OVS flow information for a lab."""
    if not settings.enable_ovs_plugin:
        return PluginFlowsResponse(error="OVS plugin not enabled")

    try:
        plugin = _get_docker_ovs_plugin()
        flows_data = await plugin.get_lab_flows(lab_id)

        if "error" in flows_data:
            return PluginFlowsResponse(error=flows_data["error"])

        return PluginFlowsResponse(
            bridge=flows_data.get("bridge"),
            flow_count=flows_data.get("flow_count", 0),
            flows=flows_data.get("flows", []),
        )

    except Exception as e:
        logger.error(f"OVS plugin lab flows failed: {e}")
        return PluginFlowsResponse(error=str(e))


@router.post("/ovs-plugin/labs/{lab_id}/vxlan")
async def create_plugin_vxlan(lab_id: str, request: PluginVxlanRequest) -> PluginVxlanResponse:
    """Create VXLAN tunnel on a lab's OVS bridge."""
    if not settings.enable_ovs_plugin:
        return PluginVxlanResponse(success=False, error="OVS plugin not enabled")

    logger.info(
        f"Creating plugin VXLAN: lab={lab_id}, vni={request.vni}, "
        f"remote={request.remote_ip}"
    )

    try:
        plugin = _get_docker_ovs_plugin()
        port_name = await plugin.create_vxlan_tunnel(
            lab_id=lab_id,
            link_id=request.link_id,
            local_ip=request.local_ip,
            remote_ip=request.remote_ip,
            vni=request.vni,
            vlan_tag=request.vlan_tag,
        )

        return PluginVxlanResponse(success=True, port_name=port_name)

    except Exception as e:
        logger.error(f"Plugin VXLAN creation failed: {e}")
        return PluginVxlanResponse(success=False, error=str(e))


@router.delete("/ovs-plugin/labs/{lab_id}/vxlan/{vni}")
async def delete_plugin_vxlan(lab_id: str, vni: int) -> PluginVxlanResponse:
    """Delete VXLAN tunnel from a lab's OVS bridge."""
    if not settings.enable_ovs_plugin:
        return PluginVxlanResponse(success=False, error="OVS plugin not enabled")

    logger.info(f"Deleting plugin VXLAN: lab={lab_id}, vni={vni}")

    try:
        plugin = _get_docker_ovs_plugin()
        success = await plugin.delete_vxlan_tunnel(lab_id, vni)

        if success:
            return PluginVxlanResponse(success=True)
        else:
            return PluginVxlanResponse(success=False, error="Tunnel not found")

    except Exception as e:
        logger.error(f"Plugin VXLAN deletion failed: {e}")
        return PluginVxlanResponse(success=False, error=str(e))


@router.post("/ovs-plugin/labs/{lab_id}/external")
async def attach_plugin_external(
    lab_id: str, request: PluginExternalAttachRequest
) -> PluginExternalAttachResponse:
    """Attach external host interface to lab's OVS bridge."""
    if not settings.enable_ovs_plugin:
        return PluginExternalAttachResponse(success=False, error="OVS plugin not enabled")

    logger.info(
        f"Attaching external interface: lab={lab_id}, "
        f"interface={request.external_interface}"
    )

    try:
        plugin = _get_docker_ovs_plugin()
        vlan_tag = await plugin.attach_external_interface(
            lab_id=lab_id,
            external_interface=request.external_interface,
            vlan_tag=request.vlan_tag,
        )

        return PluginExternalAttachResponse(success=True, vlan_tag=vlan_tag)

    except Exception as e:
        logger.error(f"External interface attachment failed: {e}")
        return PluginExternalAttachResponse(success=False, error=str(e))


@router.delete("/ovs-plugin/labs/{lab_id}/external/{interface}")
async def detach_plugin_external(lab_id: str, interface: str) -> PluginExternalAttachResponse:
    """Detach external interface from lab's OVS bridge."""
    if not settings.enable_ovs_plugin:
        return PluginExternalAttachResponse(success=False, error="OVS plugin not enabled")

    logger.info(f"Detaching external interface: lab={lab_id}, interface={interface}")

    try:
        plugin = _get_docker_ovs_plugin()
        success = await plugin.detach_external_interface(lab_id, interface)

        if success:
            return PluginExternalAttachResponse(success=True)
        else:
            return PluginExternalAttachResponse(success=False, error="Interface not found")

    except Exception as e:
        logger.error(f"External interface detachment failed: {e}")
        return PluginExternalAttachResponse(success=False, error=str(e))


@router.get("/ovs-plugin/labs/{lab_id}/external")
async def list_plugin_external(lab_id: str) -> PluginExternalListResponse:
    """List external interfaces attached to a lab's OVS bridge."""
    if not settings.enable_ovs_plugin:
        return PluginExternalListResponse(lab_id=lab_id, interfaces=[])

    try:
        plugin = _get_docker_ovs_plugin()
        external_ports = plugin.list_external_interfaces(lab_id)

        interfaces = [
            PluginExternalInfo(interface=iface, vlan_tag=vlan)
            for iface, vlan in external_ports.items()
        ]

        return PluginExternalListResponse(lab_id=lab_id, interfaces=interfaces)

    except Exception as e:
        logger.error(f"List external interfaces failed: {e}")
        return PluginExternalListResponse(lab_id=lab_id, interfaces=[])


@router.post("/ovs-plugin/labs/{lab_id}/mgmt")
async def create_plugin_mgmt_network(lab_id: str) -> PluginMgmtNetworkResponse:
    """Create management network for a lab."""
    if not settings.enable_ovs_plugin:
        return PluginMgmtNetworkResponse(success=False, error="OVS plugin not enabled")

    logger.info(f"Creating management network for lab {lab_id}")

    try:
        plugin = _get_docker_ovs_plugin()
        mgmt_net = await plugin.create_management_network(lab_id)

        return PluginMgmtNetworkResponse(
            success=True,
            network=PluginMgmtNetworkInfo(
                lab_id=mgmt_net.lab_id,
                network_id=mgmt_net.network_id,
                network_name=mgmt_net.network_name,
                subnet=mgmt_net.subnet,
                gateway=mgmt_net.gateway,
            ),
        )

    except Exception as e:
        logger.error(f"Management network creation failed: {e}")
        return PluginMgmtNetworkResponse(success=False, error=str(e))


@router.post("/ovs-plugin/labs/{lab_id}/mgmt/attach")
async def attach_to_plugin_mgmt(
    lab_id: str, request: PluginMgmtAttachRequest
) -> PluginMgmtAttachResponse:
    """Attach container to management network."""
    if not settings.enable_ovs_plugin:
        return PluginMgmtAttachResponse(success=False, error="OVS plugin not enabled")

    logger.info(f"Attaching {request.container_id} to management network for lab {lab_id}")

    try:
        plugin = _get_docker_ovs_plugin()
        ip_address = await plugin.attach_to_management(request.container_id, lab_id)

        if ip_address:
            return PluginMgmtAttachResponse(success=True, ip_address=ip_address)
        else:
            return PluginMgmtAttachResponse(success=False, error="Failed to get IP address")

    except Exception as e:
        logger.error(f"Management network attachment failed: {e}")
        return PluginMgmtAttachResponse(success=False, error=str(e))


@router.delete("/ovs-plugin/labs/{lab_id}/mgmt")
async def delete_plugin_mgmt_network(lab_id: str) -> PluginMgmtNetworkResponse:
    """Delete management network for a lab."""
    if not settings.enable_ovs_plugin:
        return PluginMgmtNetworkResponse(success=False, error="OVS plugin not enabled")

    logger.info(f"Deleting management network for lab {lab_id}")

    try:
        plugin = _get_docker_ovs_plugin()
        success = await plugin.delete_management_network(lab_id)

        if success:
            return PluginMgmtNetworkResponse(success=True)
        else:
            return PluginMgmtNetworkResponse(success=False, error="Network not found")

    except Exception as e:
        logger.error(f"Management network deletion failed: {e}")
        return PluginMgmtNetworkResponse(success=False, error=str(e))
