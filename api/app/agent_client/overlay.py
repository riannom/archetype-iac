"""VXLAN/overlay tunnel management and cross-host link setup."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.orm import Session

from app import models
from app.agent_client.http import (
    _agent_request,
    _safe_agent_request,
    AgentError,
    VTEP_OPERATION_TIMEOUT,
)
from app.agent_client.selection import (
    get_agent_url,
    resolve_agent_ip,
    resolve_data_plane_ip,
    _data_plane_mtu_ok,
)


logger = logging.getLogger(__name__)


def compute_vxlan_port_name(lab_id: str, link_name: str) -> str:
    """Compute the deterministic OVS port name for a per-link VXLAN tunnel.

    Must match agent/network/overlay.py:_link_tunnel_interface_name().
    """
    import hashlib

    combined = f"{lab_id}:{link_name}"
    link_hash = hashlib.md5(combined.encode()).hexdigest()[:8]
    return f"vxlan-{link_hash}"


async def reconcile_vxlan_ports_on_agent(
    agent: models.Host,
    valid_port_names: list[str],
    force: bool = False,
    confirm: bool = False,
    allow_empty: bool = False,
) -> dict:
    """Tell agent which VXLAN ports should exist; agent removes the rest.

    Args:
        agent: The agent to reconcile
        valid_port_names: List of VXLAN port names that should be kept

    Returns dict with 'removed_ports' key listing what was cleaned up.
    """
    url = f"{get_agent_url(agent)}/overlay/reconcile-ports"

    try:
        return await _agent_request(
            "POST",
            url,
            json_body={
                "valid_port_names": valid_port_names,
                "force": force,
                "confirm": confirm,
                "allow_empty": allow_empty,
            },
            timeout=60.0,
            max_retries=0,
        )
    except Exception as e:
        logger.error(f"Failed to reconcile VXLAN ports on agent {agent.id}: {e}")
        return {"removed_ports": [], "errors": [str(e)]}


async def declare_overlay_state_on_agent(
    agent: models.Host,
    tunnels: list[dict],
    declared_labs: list[str] | None = None,
) -> dict:
    """Declare full desired overlay state on an agent.

    The agent converges to match: creates missing, updates drifted,
    removes orphans. This is a superset of reconcile_vxlan_ports_on_agent.

    Args:
        agent: The agent to converge
        tunnels: List of declared tunnel dicts with keys:
            link_id, lab_id, vni, local_ip, remote_ip,
            expected_vlan, port_name, mtu
        declared_labs: Optional explicit lab scope for orphan cleanup.
            If empty/None, agent infers scope from declared tunnels.

    Returns:
        Dict with 'results' list and 'orphans_removed' list.
        Falls back to whitelist reconciliation if agent returns 404.
    """
    url = f"{get_agent_url(agent)}/overlay/declare-state"

    try:
        result = await _agent_request(
            "POST",
            url,
            json_body={
                "tunnels": tunnels,
                "declared_labs": declared_labs,
            },
            timeout=VTEP_OPERATION_TIMEOUT,
            max_retries=0,
        )
        return result
    except Exception as e:
        error_msg = str(e)
        # 404 means agent is old version — fall back to whitelist approach
        if "404" in error_msg or "Not Found" in error_msg:
            logger.warning(
                f"Agent {agent.name} does not support declare-state (404), "
                f"falling back to whitelist reconciliation"
            )
            valid_ports = [t["port_name"] for t in tunnels]
            return await reconcile_vxlan_ports_on_agent(
                agent,
                valid_port_names=valid_ports,
                confirm=True,
            )
        logger.error(f"Failed to declare overlay state on agent {agent.id}: {e}")
        return {"results": [], "orphans_removed": [], "error": error_msg}


async def get_overlay_status_from_agent(agent: models.Host) -> dict:
    """Get overlay status from an agent."""
    return await _safe_agent_request(
        agent, "GET", "/overlay/status",
        fallback={"tunnels": [], "bridges": []},
        timeout=10.0, description="Overlay status", log_level="error",
    )


async def attach_container_on_agent(
    agent: models.Host,
    lab_id: str,
    link_id: str,
    container_name: str,
    interface_name: str,
    ip_address: str | None = None,
) -> dict:
    """Attach a container to an overlay bridge on an agent.

    Args:
        agent: The agent where the container is running
        lab_id: Lab identifier
        link_id: Link identifier (matches the tunnel/bridge)
        container_name: Docker container name
        interface_name: Interface name inside container (e.g., eth1)
        ip_address: Optional IP address in CIDR format (e.g., "10.0.0.1/24")

    Returns:
        Dict with 'success' and optionally 'error' keys
    """
    url = f"{get_agent_url(agent)}/overlay/attach"

    payload = {
        "lab_id": lab_id,
        "link_id": link_id,
        "container_name": container_name,
        "interface_name": interface_name,
    }
    if ip_address:
        payload["ip_address"] = ip_address

    try:
        result = await _agent_request(
            "POST",
            url,
            json_body=payload,
            timeout=30.0,
            max_retries=0,
        )
        if result.get("success"):
            ip_info = f" with IP {ip_address}" if ip_address else ""
            logger.info(f"Attached {container_name} to overlay on {agent.id}{ip_info}")
        else:
            logger.warning(f"Container attachment failed on {agent.id}: {result.get('error')}")
        return result
    except Exception as e:
        logger.error(f"Failed to attach container on agent {agent.id}: {e}")
        return {"success": False, "error": str(e)}


async def cleanup_overlay_on_agent(agent: models.Host, lab_id: str) -> dict:
    """Clean up all overlay networking for a lab on an agent.

    Args:
        agent: The agent to clean up
        lab_id: Lab identifier

    Returns:
        Dict with 'tunnels_deleted', 'bridges_deleted', and 'errors' keys
    """
    url = f"{get_agent_url(agent)}/overlay/cleanup"

    try:
        result = await _agent_request(
            "POST",
            url,
            json_body={"lab_id": lab_id},
            timeout=60.0,
            max_retries=0,
        )
        logger.info(
            f"Overlay cleanup on {agent.id}: "
            f"{result.get('tunnels_deleted', 0)} tunnels, "
            f"{result.get('bridges_deleted', 0)} bridges"
        )
        return result
    except Exception as e:
        logger.error(f"Failed to cleanup overlay on agent {agent.id}: {e}")
        return {"tunnels_deleted": 0, "bridges_deleted": 0, "errors": [str(e)]}


async def get_cleanup_audit_from_agent(agent: models.Host, include_ovs: bool = False) -> dict:
    """Get a dry-run cleanup audit from an agent (no deletions)."""
    url = f"{get_agent_url(agent)}/cleanup/audit"

    try:
        return await _agent_request(
            "POST",
            url,
            json_body={"include_ovs": include_ovs},
            timeout=30.0,
            max_retries=0,
        )
    except Exception as e:
        logger.error(f"Cleanup audit failed on agent {agent.id}: {e}")
        return {"network": {}, "ovs": None, "errors": [str(e)]}


async def attach_overlay_interface_on_agent(
    agent: models.Host,
    lab_id: str,
    container_name: str,
    interface_name: str,
    vni: int,
    local_ip: str,
    remote_ip: str,
    link_id: str,
    tenant_mtu: int = 0,
) -> dict:
    """Create a per-link VXLAN tunnel and attach a container interface.

    The agent discovers the container's local VLAN and creates an access-mode
    VXLAN port with tag=<local_vlan> and options:key=<vni>.

    Args:
        agent: The agent where the container is running
        lab_id: Lab identifier
        container_name: Docker container name
        interface_name: Interface name inside container (e.g., eth1)
        vni: VXLAN Network Identifier (shared between both sides)
        local_ip: Agent's own IP for VXLAN endpoint
        remote_ip: Remote agent's IP for VXLAN endpoint
        link_id: Link identifier for tracking
        tenant_mtu: Optional MTU (0 = auto-discover)

    Returns:
        Dict with 'success', 'local_vlan', 'vni', and optionally 'error' keys
    """
    url = f"{get_agent_url(agent)}/overlay/attach-link"

    payload = {
        "lab_id": lab_id,
        "container_name": container_name,
        "interface_name": interface_name,
        "vni": vni,
        "local_ip": local_ip,
        "remote_ip": remote_ip,
        "link_id": link_id,
        "tenant_mtu": tenant_mtu,
    }

    try:
        result = await _agent_request(
            "POST",
            url,
            json_body=payload,
            timeout=VTEP_OPERATION_TIMEOUT,
            max_retries=0,
        )
        if result.get("success"):
            logger.info(
                f"Attached {container_name}:{interface_name} with VNI {vni} "
                f"(local VLAN {result.get('local_vlan')}) on {agent.id}"
            )
        else:
            logger.warning(f"Overlay attach failed on {agent.id}: {result.get('error')}")
        return result
    except Exception as e:
        logger.error(f"Failed to attach overlay interface on agent {agent.id}: {e}")
        return {"success": False, "error": str(e)}


async def detach_overlay_interface_on_agent(
    agent: models.Host,
    lab_id: str,
    container_name: str,
    interface_name: str,
    link_id: str,
) -> dict:
    """Detach a link from the overlay on an agent.

    This performs a complete detach:
    1. Restores the container interface to an isolated VLAN (unique tag)
    2. Deletes the per-link VXLAN tunnel port

    Args:
        agent: The agent to detach on
        lab_id: Lab identifier
        container_name: Container name (short form, e.g., "eos_1")
        interface_name: Interface name inside container (e.g., eth1)
        link_id: Link identifier for tunnel lookup

    Returns:
        Dict with 'success', 'interface_isolated', 'new_vlan',
        'tunnel_deleted' keys
    """
    url = f"{get_agent_url(agent)}/overlay/detach-link"

    payload = {
        "lab_id": lab_id,
        "container_name": container_name,
        "interface_name": interface_name,
        "link_id": link_id,
    }

    try:
        result = await _agent_request(
            "POST",
            url,
            json_body=payload,
            timeout=VTEP_OPERATION_TIMEOUT,
            max_retries=0,
        )
        if result.get("success"):
            isolated_msg = f" (interface isolated to VLAN {result.get('new_vlan')})" if result.get("interface_isolated") else ""
            tunnel_msg = " (tunnel deleted)" if result.get("tunnel_deleted") else ""
            logger.info(
                f"Detached {container_name}:{interface_name} link {link_id} "
                f"on {agent.id}{isolated_msg}{tunnel_msg}"
            )
        else:
            logger.warning(f"Overlay detach failed on {agent.id}: {result.get('error')}")
        return result
    except Exception as e:
        logger.error(f"Failed to detach overlay interface on agent {agent.id}: {e}")
        return {"success": False, "error": str(e)}


async def setup_cross_host_link_v2(
    database: Session,
    lab_id: str,
    link_id: str,
    agent_a: models.Host,
    agent_b: models.Host,
    node_a: str,
    interface_a: str,
    node_b: str,
    interface_b: str,
) -> dict:
    """Set up a cross-host link using the per-link VNI model.

    Each cross-host link gets its own VXLAN port on each agent in access mode.
    The agent discovers the container's local VLAN and creates the VXLAN port
    with tag=<local_vlan> and options:key=<vni>. No VLAN coordination needed.

    Args:
        database: Database session (used for transport interface IP lookup)
        lab_id: Lab identifier
        link_id: Link identifier
        agent_a: First agent
        agent_b: Second agent
        node_a: Container name on agent_a
        interface_a: Interface name in node_a
        node_b: Container name on agent_b
        interface_b: Interface name in node_b

    Returns:
        Dict with 'success' and status information
    """
    from app.services.link_manager import allocate_vni
    from app.routers.infrastructure import get_or_create_settings

    # Read overlay MTU from infrastructure settings
    infra = get_or_create_settings(database)
    overlay_mtu = infra.overlay_mtu or 0

    # Prefer data plane addresses for VXLAN tunnels, but only if MTU tests validate it.
    required_mtu = overlay_mtu if overlay_mtu and overlay_mtu > 0 else 1500
    if _data_plane_mtu_ok(database, agent_a.id, agent_b.id, required_mtu):
        agent_ip_a = await resolve_data_plane_ip(database, agent_a)
        agent_ip_b = await resolve_data_plane_ip(database, agent_b)
    else:
        logger.warning(
            "Data-plane MTU test missing/insufficient between agents "
            f"{agent_a.id} and {agent_b.id} (required_mtu={required_mtu}). "
            "Using management IPs for VXLAN; run MTU tests to enable transport."
        )
        agent_ip_a = await resolve_agent_ip(agent_a.address)
        agent_ip_b = await resolve_agent_ip(agent_b.address)

    # Allocate deterministic per-link VNI
    vni = allocate_vni(lab_id, link_id)

    logger.info(
        f"Setting up cross-host link {link_id} (VNI {vni}): "
        f"{agent_a.id}({agent_ip_a}) <-> {agent_b.id}({agent_ip_b})"
    )

    # Retry logic for container attachments - containers may still be starting
    max_retries = 3
    retry_delay = 2.0  # seconds

    async def attach_with_retry(agent, node, interface, local_ip, remote_ip) -> dict:
        """Attempt attachment with retries for timing issues."""
        last_error = None
        for attempt in range(max_retries):
            result = await attach_overlay_interface_on_agent(
                agent,
                lab_id=lab_id,
                container_name=node,
                interface_name=interface,
                vni=vni,
                local_ip=local_ip,
                remote_ip=remote_ip,
                link_id=link_id,
                tenant_mtu=overlay_mtu,
            )
            if result.get("success"):
                return result
            last_error = result.get("error", "unknown error")
            if "not running" in str(last_error).lower() and attempt < max_retries - 1:
                logger.info(
                    f"Container not running, retrying in {retry_delay}s "
                    f"(attempt {attempt + 1}/{max_retries})"
                )
                await asyncio.sleep(retry_delay)
            else:
                break
        return {"success": False, "error": last_error}

    # Create per-link VXLAN ports on both agents in parallel
    attach_a_result, attach_b_result = await asyncio.gather(
        attach_with_retry(agent_a, node_a, interface_a, agent_ip_a, agent_ip_b),
        attach_with_retry(agent_b, node_b, interface_b, agent_ip_b, agent_ip_a),
    )

    # Check if either attachment failed
    attach_errors = []
    if not attach_a_result.get("success"):
        attach_errors.append(
            f"{agent_a.name}:{node_a}:{interface_a}: {attach_a_result.get('error')}"
        )
    if not attach_b_result.get("success"):
        attach_errors.append(
            f"{agent_b.name}:{node_b}:{interface_b}: {attach_b_result.get('error')}"
        )

    if attach_errors:
        error_msg = "; ".join(attach_errors)
        logger.error(
            f"Per-link tunnel creation failed for {link_id}: {error_msg}"
        )

        # Best-effort rollback: detach the side that succeeded
        rollback_tasks = []
        if attach_a_result.get("success"):
            rollback_tasks.append(
                detach_overlay_interface_on_agent(
                    agent_a,
                    lab_id=lab_id,
                    container_name=node_a,
                    interface_name=interface_a,
                    link_id=link_id,
                )
            )
        if attach_b_result.get("success"):
            rollback_tasks.append(
                detach_overlay_interface_on_agent(
                    agent_b,
                    lab_id=lab_id,
                    container_name=node_b,
                    interface_name=interface_b,
                    link_id=link_id,
                )
            )
        if rollback_tasks:
            try:
                await asyncio.gather(*rollback_tasks)
                logger.info(f"Rolled back partial attachments for {link_id}")
            except Exception as e:
                logger.warning(f"Rollback failed for {link_id}: {e}")
                # Track which agents still have partial state for reconciliation
                agents_with_state = []
                if attach_a_result.get("success"):
                    agents_with_state.append(agent_a.id)
                if attach_b_result.get("success"):
                    agents_with_state.append(agent_b.id)
                return {
                    "success": False,
                    "error": f"Per-link tunnel creation failed: {error_msg}",
                    "vni": vni,
                    "partial_state": True,
                    "agents_with_state": agents_with_state,
                }

        return {
            "success": False,
            "error": f"Per-link tunnel creation failed: {error_msg}",
            "vni": vni,
        }

    return {
        "success": True,
        "vni": vni,
        "agent_a": agent_a.id,
        "agent_b": agent_b.id,
        "local_vlans": {
            "a": attach_a_result.get("local_vlan"),
            "b": attach_b_result.get("local_vlan"),
        },
    }
