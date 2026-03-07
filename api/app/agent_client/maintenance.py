"""MTU testing, interface configuration, cleanup, config extraction, exec, OVS status."""

from __future__ import annotations

import logging

from app import models
from app.agent_client.http import (
    _agent_request,
    _safe_agent_request,
)
from app.agent_client.selection import get_agent_url


logger = logging.getLogger(__name__)


# --- Config Extraction Functions ---


async def extract_configs_on_agent(
    agent: models.Host,
    lab_id: str,
) -> dict:
    """Extract running configs from all nodes in a lab."""
    logger.info(f"Extracting configs for lab {lab_id} via agent {agent.id}")
    result = await _safe_agent_request(
        agent, "POST", f"/labs/{lab_id}/extract-configs",
        fallback={"success": False, "extracted_count": 0},
        timeout=120.0, metric_operation="extract_configs",
        description=f"Extract configs for lab {lab_id}", log_level="error",
    )
    if result.get("success"):
        logger.info(f"Extracted {result.get('extracted_count', 0)} configs for lab {lab_id}")
    return result


async def extract_node_config_on_agent(
    agent: models.Host,
    lab_id: str,
    node_name: str,
) -> dict:
    """Extract running config from one node on an agent."""
    logger.info(f"Extracting config for node {node_name} in lab {lab_id} via agent {agent.id}")
    result = await _safe_agent_request(
        agent, "POST", f"/labs/{lab_id}/nodes/{node_name}/extract-config",
        fallback={"success": False, "node_name": node_name},
        timeout=120.0, metric_operation="extract_configs",
        description=f"Extract config for {node_name} in lab {lab_id}", log_level="error",
    )
    if result.get("success"):
        logger.info(f"Extracted config for {node_name} in lab {lab_id}")
    return result


async def update_config_on_agent(
    agent: models.Host,
    lab_id: str,
    node_name: str,
    content: str,
) -> dict:
    """Push a startup config to an agent for a specific node."""
    logger.debug(f"Pushing config for {node_name} to agent {agent.id}")
    result = await _safe_agent_request(
        agent, "PUT", f"/labs/{lab_id}/nodes/{node_name}/config",
        json_body={"content": content},
        fallback={"success": False}, timeout=30.0,
        metric_operation="update_config",
        description=f"Push config for {node_name}", log_level="error",
    )
    if result.get("success"):
        logger.debug(f"Pushed config for {node_name} to agent {agent.id}")
    return result


# --- Docker Cleanup Functions ---


async def prune_docker_on_agent(
    agent: models.Host,
    valid_lab_ids: list[str],
    prune_dangling_images: bool = True,
    prune_build_cache: bool = True,
    prune_unused_volumes: bool = False,
    prune_stopped_containers: bool = False,
    prune_unused_networks: bool = False,
) -> dict:
    """Request an agent to prune Docker resources."""
    return await _safe_agent_request(
        agent, "POST", "/prune-docker",
        json_body={
            "valid_lab_ids": valid_lab_ids,
            "prune_dangling_images": prune_dangling_images,
            "prune_build_cache": prune_build_cache,
            "prune_unused_volumes": prune_unused_volumes,
            "prune_stopped_containers": prune_stopped_containers,
            "prune_unused_networks": prune_unused_networks,
        },
        fallback={
            "success": False, "images_removed": 0, "build_cache_removed": 0,
            "volumes_removed": 0, "containers_removed": 0, "networks_removed": 0,
            "space_reclaimed": 0, "errors": [],
        },
        timeout=120.0, description="Prune Docker", log_level="error",
    )


# --- Workspace Cleanup Functions ---


async def cleanup_agent_workspace(agent: models.Host, lab_id: str) -> dict:
    """Tell an agent to remove workspace for a specific lab."""
    return await _safe_agent_request(
        agent, "DELETE", f"/labs/{lab_id}/workspace",
        fallback={"success": False}, timeout=30.0,
        description=f"Cleanup workspace for lab {lab_id}",
    )


async def cleanup_workspaces_on_agent(agent: models.Host, valid_lab_ids: list[str]) -> dict:
    """Tell an agent to remove orphaned workspace directories."""
    return await _safe_agent_request(
        agent, "POST", "/cleanup-workspaces",
        json_body={"valid_lab_ids": valid_lab_ids},
        fallback={"success": False, "removed": [], "errors": []},
        timeout=60.0, description="Cleanup workspaces",
    )


async def delete_image_on_agent(agent: models.Host, reference: str) -> dict:
    """Tell an agent to remove a specific image artifact."""
    from urllib.parse import quote

    encoded_reference = quote(reference, safe="")
    return await _safe_agent_request(
        agent,
        "DELETE",
        f"/images/{encoded_reference}",
        fallback={"success": False, "deleted": False, "error": "agent unavailable"},
        timeout=60.0,
        description=f"Delete image {reference}",
        log_level="error",
    )


# --- MTU Testing Functions ---


async def test_mtu_on_agent(
    agent: models.Host,
    target_ip: str,
    mtu: int,
    source_ip: str | None = None,
) -> dict:
    """Test MTU to a target IP from an agent.

    Runs ping with DF (Don't Fragment) bit set to verify path MTU.
    Also detects link type (direct/routed) via TTL analysis.

    Args:
        agent: The agent to run the test from
        target_ip: Target IP address to test connectivity to
        mtu: MTU size to test
        source_ip: Optional source IP for bind address (data plane testing)

    Returns:
        Dict with 'success', 'tested_mtu', 'link_type', 'latency_ms', 'error' keys
    """
    url = f"{get_agent_url(agent)}/network/test-mtu"

    try:
        payload: dict = {
            "target_ip": target_ip,
            "mtu": mtu,
        }
        if source_ip:
            payload["source_ip"] = source_ip
        return await _agent_request(
            "POST",
            url,
            json_body=payload,
            timeout=30.0,
            max_retries=0,
        )
    except Exception as e:
        logger.error(f"MTU test failed on agent {agent.id}: {e}")
        return {
            "success": False,
            "error": str(e),
        }


# --- Interface Configuration Functions ---


async def get_agent_interface_details(agent: models.Host) -> dict:
    """Get detailed interface information from an agent.

    Returns all interfaces with their MTU, identifies the default route
    interface, and detects which network manager is in use.

    Args:
        agent: The agent to query

    Returns:
        Dict with 'interfaces', 'default_route_interface', 'network_manager' keys
    """
    url = f"{get_agent_url(agent)}/interfaces/details"

    try:
        return await _agent_request(
            "GET",
            url,
            timeout=30.0,
            max_retries=0,
            metric_operation="get_interface_details",
            metric_host_id=agent.id,
        )
    except Exception as e:
        logger.error(f"Failed to get interface details from agent {agent.id}: {e}")
        raise


async def set_agent_interface_mtu(
    agent: models.Host,
    interface_name: str,
    mtu: int,
    persist: bool = True,
) -> dict:
    """Set MTU on an agent's interface.

    Applies the MTU change and optionally persists it across reboots.

    Args:
        agent: The agent to configure
        interface_name: Name of the interface
        mtu: MTU value to set
        persist: Whether to persist the change across reboots

    Returns:
        Dict with 'success', 'interface', 'previous_mtu', 'new_mtu',
        'persisted', 'network_manager', 'error' keys
    """
    url = f"{get_agent_url(agent)}/interfaces/{interface_name}/mtu"

    try:
        return await _agent_request(
            "POST",
            url,
            json_body={"mtu": mtu, "persist": persist},
            timeout=60.0,  # Longer timeout for persistence operations
            max_retries=0,
        )
    except Exception as e:
        logger.error(f"Failed to set MTU on agent {agent.id} interface {interface_name}: {e}")
        return {
            "success": False,
            "interface": interface_name,
            "previous_mtu": 0,
            "new_mtu": mtu,
            "persisted": False,
            "error": str(e),
        }


async def provision_interface_on_agent(
    agent: models.Host,
    action: str,
    name: str | None = None,
    parent_interface: str | None = None,
    vlan_id: int | None = None,
    ip_cidr: str | None = None,
    mtu: int | None = None,
    attach_to_ovs: bool = False,
    ovs_vlan_tag: int | None = None,
) -> dict:
    """Provision, configure, or delete an interface on an agent host.

    Args:
        agent: The agent to configure
        action: "create_subinterface", "configure", or "delete"
        name: Interface name (auto-generated for subinterfaces)
        parent_interface: Parent for subinterface creation
        vlan_id: VLAN ID for subinterface
        ip_cidr: IP/CIDR to assign
        mtu: Desired MTU
        attach_to_ovs: Whether to also add to OVS bridge
        ovs_vlan_tag: VLAN tag for OVS attachment

    Returns:
        Dict with 'success', 'interface_name', 'mtu', 'ip_address', 'error' keys
    """
    url = f"{get_agent_url(agent)}/interfaces/provision"
    payload = {
        "action": action,
        "name": name,
        "parent_interface": parent_interface,
        "vlan_id": vlan_id,
        "ip_cidr": ip_cidr,
        "mtu": mtu,
        "attach_to_ovs": attach_to_ovs,
        "ovs_vlan_tag": ovs_vlan_tag,
    }
    # Remove None values
    payload = {k: v for k, v in payload.items() if v is not None}

    try:
        return await _agent_request(
            "POST",
            url,
            json_body=payload,
            timeout=60.0,
            max_retries=0,
        )
    except Exception as e:
        logger.error(f"Failed to provision interface on agent {agent.id}: {e}")
        return {"success": False, "error": str(e)}


# --- OVS Status Functions ---


async def get_ovs_status_from_agent(agent: models.Host) -> dict:
    """Get OVS networking status from an agent.

    Returns:
        Dict with 'bridge_name', 'initialized', 'ports', 'links'
    """
    url = f"{get_agent_url(agent)}/ovs/status"

    try:
        return await _agent_request(
            "GET",
            url,
            timeout=10.0,
            max_retries=0,
        )
    except Exception as e:
        logger.error(f"OVS status failed on agent {agent.id}: {e}")
        return {"bridge_name": "", "initialized": False, "ports": [], "links": []}


async def get_agent_boot_logs(agent: models.Host, lab_id: str | None = None) -> dict:
    """Get boot logs from an agent for a specific lab.

    Returns:
        Dict with 'lab_id' and 'boot_logs' mapping node names to log text
    """
    if not lab_id:
        return {"boot_logs": {}, "error": "lab_id required"}

    url = f"{get_agent_url(agent)}/labs/{lab_id}/boot-logs"

    try:
        return await _agent_request(
            "GET",
            url,
            timeout=15.0,
            max_retries=0,
        )
    except Exception as e:
        logger.error(f"Failed to get boot logs from agent {agent.id}: {e}")
        return {"boot_logs": {}, "error": str(e)}


async def get_agent_ovs_flows(agent: models.Host) -> dict:
    """Get OVS flow table from an agent.

    Returns:
        Dict with 'bridge', 'flows', and optionally 'error'
    """
    url = f"{get_agent_url(agent)}/ovs/flows"

    try:
        return await _agent_request(
            "GET",
            url,
            timeout=10.0,
            max_retries=0,
        )
    except Exception as e:
        logger.error(f"Failed to get OVS flows from agent {agent.id}: {e}")
        return {"bridge": "", "flows": "", "error": str(e)}


# --- Exec Functions ---


async def exec_node_on_agent(
    agent: models.Host,
    lab_id: str,
    node_name: str,
    cmd: str,
    timeout: float = 30.0,
) -> dict:
    """Execute a command inside a node on an agent.

    Uses the existing POST /labs/{lab_id}/nodes/{node_name}/exec endpoint.

    Returns:
        dict with keys: exit_code (int), output (str)
    """
    url = f"{get_agent_url(agent)}/labs/{lab_id}/nodes/{node_name}/exec"
    return await _agent_request(
        "POST", url, json_body={"cmd": cmd}, timeout=timeout
    )
