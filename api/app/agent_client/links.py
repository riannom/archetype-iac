"""Link management: hot-connect, hot-disconnect, port state, external connections."""

from __future__ import annotations

import logging

from app import models
from app.agent_client.http import (
    _agent_request,
    VTEP_OPERATION_TIMEOUT,
)
from app.agent_client.selection import get_agent_url


logger = logging.getLogger(__name__)


async def create_link_on_agent(
    agent: models.Host,
    lab_id: str,
    source_node: str,
    source_interface: str,
    target_node: str,
    target_interface: str,
) -> dict:
    """Hot-connect two interfaces on an agent.

    This creates a link between two container interfaces by assigning
    them the same VLAN tag on the OVS bridge.

    Args:
        agent: The agent managing the lab
        lab_id: Lab identifier
        source_node: Source node name
        source_interface: Source interface name (e.g., "eth1")
        target_node: Target node name
        target_interface: Target interface name

    Returns:
        Dict with 'success', 'link', and optionally 'error' keys
    """
    url = f"{get_agent_url(agent)}/labs/{lab_id}/links"
    logger.info(
        f"Hot-connect on agent {agent.id}: "
        f"{source_node}:{source_interface} <-> {target_node}:{target_interface}"
    )

    try:
        result = await _agent_request(
            "POST",
            url,
            json_body={
                "source_node": source_node,
                "source_interface": source_interface,
                "target_node": target_node,
                "target_interface": target_interface,
            },
            timeout=30.0,
            max_retries=0,
        )
        if result.get("success"):
            logger.info(f"Hot-connect succeeded: {result.get('link', {}).get('link_id')}")
        else:
            logger.warning(f"Hot-connect failed: {result.get('error')}")
        return result
    except Exception as e:
        logger.error(f"Hot-connect failed on agent {agent.id}: {e}")
        return {"success": False, "error": str(e)}


async def delete_link_on_agent(
    agent: models.Host,
    lab_id: str,
    link_id: str,
) -> dict:
    """Hot-disconnect a link on an agent.

    This breaks a link between two container interfaces by assigning
    them separate VLAN tags.

    Args:
        agent: The agent managing the lab
        lab_id: Lab identifier
        link_id: Link identifier (format: "node1:iface1-node2:iface2")

    Returns:
        Dict with 'success' and optionally 'error' keys
    """
    url = f"{get_agent_url(agent)}/labs/{lab_id}/links/{link_id}"
    logger.info(f"Hot-disconnect on agent {agent.id}: {link_id}")

    try:
        result = await _agent_request(
            "DELETE",
            url,
            timeout=30.0,
            max_retries=0,
        )
        if result.get("success"):
            logger.info(f"Hot-disconnect succeeded: {link_id}")
        else:
            logger.warning(f"Hot-disconnect failed: {result.get('error')}")
        return result
    except Exception as e:
        logger.error(f"Hot-disconnect failed on agent {agent.id}: {e}")
        return {"success": False, "error": str(e)}


async def list_links_on_agent(
    agent: models.Host,
    lab_id: str,
) -> dict:
    """List all links for a lab on an agent.

    Args:
        agent: The agent managing the lab
        lab_id: Lab identifier

    Returns:
        Dict with 'links' list
    """
    url = f"{get_agent_url(agent)}/labs/{lab_id}/links"

    try:
        return await _agent_request(
            "GET",
            url,
            timeout=30.0,
            max_retries=0,
        )
    except Exception as e:
        logger.error(f"List links failed on agent {agent.id}: {e}")
        return {"links": []}


async def get_lab_port_state(
    agent: models.Host,
    lab_id: str,
) -> list[dict]:
    """Get OVS port state for a lab from an agent.

    Returns lightweight port info (port name, VLAN tag, carrier)
    for bulk InterfaceMapping refresh.

    Args:
        agent: The agent to query
        lab_id: Lab identifier

    Returns:
        List of port info dicts
    """
    url = f"{get_agent_url(agent)}/labs/{lab_id}/port-state"
    try:
        data = await _agent_request(
            "GET",
            url,
            timeout=10.0,
            max_retries=0,
        )
        return data.get("ports", [])
    except Exception as e:
        logger.warning(f"Get lab port state failed on {agent.name}: {e}")
        return []


async def declare_port_state_on_agent(
    agent: models.Host,
    pairings: list[dict],
) -> dict:
    """Declare same-host port state on an agent.

    The agent converges port VLAN tags to match declared pairings.

    Args:
        agent: The agent to converge
        pairings: List of port pairing dicts with keys:
            link_name, lab_id, port_a, port_b, vlan_tag

    Returns:
        Dict with 'results' list
    """
    url = f"{get_agent_url(agent)}/ports/declare-state"
    try:
        result = await _agent_request(
            "POST",
            url,
            json_body={"pairings": pairings},
            timeout=VTEP_OPERATION_TIMEOUT,
            max_retries=0,
        )
        return result
    except Exception as e:
        logger.error(f"Failed to declare port state on agent {agent.id}: {e}")
        return {"results": [], "error": str(e)}


async def get_lab_ports_from_agent(
    agent: models.Host,
    lab_id: str,
) -> list[dict]:
    """Get OVS port information for a lab from an agent.

    Returns list of port info dicts with:
    - port_name: OVS port name
    - bridge_name: OVS bridge name
    - container: Container name (if known)
    - interface: Linux interface name (e.g., "eth1")
    - vlan_tag: Current VLAN tag

    Args:
        agent: The agent to query
        lab_id: Lab identifier

    Returns:
        List of port info dicts
    """
    url = f"{get_agent_url(agent)}/ovs-plugin/labs/{lab_id}/ports"

    try:
        data = await _agent_request(
            "GET",
            url,
            timeout=10.0,
            max_retries=0,
        )
        return data.get("ports", [])
    except Exception as e:
        logger.warning(f"Get lab ports failed: {e}")
        return []


async def get_interface_vlan_from_agent(
    agent: models.Host,
    lab_id: str,
    node_name: str,
    interface: str,
    read_from_ovs: bool = False,
) -> int | None:
    """Get the current VLAN tag for a specific interface from an agent.

    Args:
        agent: The agent managing the node
        lab_id: Lab identifier
        node_name: Container name or node name
        interface: Interface name (e.g., "eth1")
        read_from_ovs: If True, read directly from OVS instead of in-memory state.
                       Use this for verification to get ground truth.

    Returns:
        VLAN tag or None if not found
    """
    url = f"{get_agent_url(agent)}/labs/{lab_id}/interfaces/{node_name}/{interface}/vlan"
    if read_from_ovs:
        url += "?read_from_ovs=true"
    try:
        data = await _agent_request(
            "GET",
            url,
            timeout=10.0,
            max_retries=0,
        )
        return data.get("vlan_tag")
    except Exception as e:
        logger.warning(f"Get interface VLAN failed: {e}")
        return None


async def get_interface_port_details_from_agent(
    agent: models.Host,
    lab_id: str,
    node_name: str,
    interface: str,
    read_from_ovs: bool = False,
) -> dict:
    """Get provider-agnostic OVS port details for a specific interface."""
    url = f"{get_agent_url(agent)}/labs/{lab_id}/interfaces/{node_name}/{interface}/vlan"
    if read_from_ovs:
        url += "?read_from_ovs=true"
    try:
        data = await _agent_request(
            "GET",
            url,
            timeout=10.0,
            max_retries=0,
        )
        return {
            "vlan_tag": data.get("vlan_tag"),
            "ovs_port_name": data.get("ovs_port_name"),
            "error": data.get("error"),
        }
    except Exception as e:
        logger.warning(f"Get interface port details failed: {e}")
        return {"vlan_tag": None, "ovs_port_name": None, "error": str(e)}


async def set_port_vlan_on_agent(
    agent: models.Host,
    port_name: str,
    vlan_tag: int,
    link_id: str | None = None,
) -> bool:
    """Set the VLAN tag on an OVS port via the agent.

    Args:
        agent: The agent managing the port
        port_name: OVS port name (e.g., VXLAN port or container veth)
        vlan_tag: VLAN tag to set
        link_id: Optional link identifier for agent in-memory tracking sync

    Returns:
        True if successful, False otherwise
    """
    url = f"{get_agent_url(agent)}/overlay/ports/{port_name}/vlan"
    body: dict = {"vlan_tag": vlan_tag}
    if link_id:
        body["link_id"] = link_id
    try:
        result = await _agent_request(
            "PUT",
            url,
            json_body=body,
            timeout=10.0,
            max_retries=0,
        )
        return result.get("success", False)
    except Exception as e:
        logger.warning(f"Set port VLAN failed on {agent.name}: {e}")
        return False


async def repair_endpoints_on_agent(
    agent: models.Host,
    lab_id: str,
    nodes: list[str] | None = None,
) -> dict:
    """Repair missing veth pairs and OVS ports on an agent.

    After agent/container restarts, endpoints may have stale in-memory
    state where the physical veth pairs no longer exist. This triggers
    recreation of the veth pairs, OVS attachment, and namespace moves.

    Args:
        agent: The agent to repair endpoints on
        lab_id: Lab identifier
        nodes: Optional list of node names to repair (all if None)

    Returns:
        Dict with 'success', 'nodes_repaired', 'total_endpoints_repaired',
        'results', and optionally 'error' keys.
    """
    url = f"{get_agent_url(agent)}/labs/{lab_id}/repair-endpoints"
    logger.info(
        f"Repairing endpoints on agent {agent.id} for lab {lab_id}"
        + (f" nodes={nodes}" if nodes else " (all nodes)")
    )

    try:
        result = await _agent_request(
            "POST",
            url,
            json_body={"nodes": nodes or []},
            timeout=60.0,
            max_retries=0,
        )
        repaired = result.get("total_endpoints_repaired", 0)
        if repaired > 0:
            logger.info(
                f"Repaired {repaired} endpoint(s) on agent {agent.id} for lab {lab_id}"
            )
        return result
    except Exception as e:
        logger.error(f"Endpoint repair failed on agent {agent.id}: {e}")
        return {"success": False, "error": str(e)}


async def connect_external_on_agent(
    agent: models.Host,
    lab_id: str,
    node_name: str,
    interface_name: str,
    external_interface: str,
    vlan_tag: int | None = None,
) -> dict:
    """Connect a container interface to an external network.

    Args:
        agent: The agent managing the lab
        lab_id: Lab identifier
        node_name: Node name
        interface_name: Interface name inside container
        external_interface: External host interface to connect to
        vlan_tag: Optional VLAN for isolation

    Returns:
        Dict with 'success', 'vlan_tag', and optionally 'error' keys
    """
    url = f"{get_agent_url(agent)}/labs/{lab_id}/external/connect"
    logger.info(
        f"External connect on agent {agent.id}: "
        f"{node_name}:{interface_name} -> {external_interface}"
    )

    try:
        result = await _agent_request(
            "POST",
            url,
            json_body={
                "node_name": node_name,
                "interface_name": interface_name,
                "external_interface": external_interface,
                "vlan_tag": vlan_tag,
            },
            timeout=30.0,
            max_retries=0,
        )
        if result.get("success"):
            logger.info(f"External connect succeeded (VLAN {result.get('vlan_tag')})")
        else:
            logger.warning(f"External connect failed: {result.get('error')}")
        return result
    except Exception as e:
        logger.error(f"External connect failed on agent {agent.id}: {e}")
        return {"success": False, "error": str(e)}


async def detach_external_on_agent(
    agent: models.Host,
    external_interface: str,
) -> dict:
    """Detach an external interface from the OVS bridge.

    Called during teardown when no more labs reference this external interface.

    Args:
        agent: The agent where the external interface is connected
        external_interface: External host interface name to detach

    Returns:
        Dict with 'success' and optionally 'error' keys
    """
    url = f"{get_agent_url(agent)}/ovs-plugin/labs/_global/external/{external_interface}"
    logger.info(f"Detaching external interface {external_interface} on agent {agent.id}")

    try:
        result = await _agent_request(
            "DELETE",
            url,
            timeout=30.0,
            max_retries=0,
        )
        if result.get("success"):
            logger.info(f"External detach succeeded for {external_interface}")
        else:
            logger.warning(f"External detach failed: {result.get('error')}")
        return result
    except Exception as e:
        logger.error(f"External detach failed on agent {agent.id}: {e}")
        return {"success": False, "error": str(e)}
