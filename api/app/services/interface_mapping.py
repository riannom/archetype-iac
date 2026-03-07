"""Interface mapping service for tracking OVS/Linux/vendor interface names.

This service manages the interface_mappings table which maps between:
- OVS ports (vh614ed63ed40)
- Linux interfaces (eth1)
- Vendor interfaces (Ethernet1, ge-0/0/0)

Key operations:
- populate_from_agent: Fetch port info from agent and upsert mappings
- get_mapping: Look up mapping for a specific interface
- translate_interface: Convert between naming conventions
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

from app import agent_client, models
from app.services.interface_naming import normalize_interface, denormalize_interface

logger = logging.getLogger(__name__)


def _merge_agent_ports(
    docker_ports: list[dict] | None,
    port_state: list[dict] | None,
    result: dict[str, int],
    *,
    allowed_node_names: set[str] | None = None,
) -> dict[tuple[str, str], dict]:
    """Merge Docker inventory and live port-state into a per-interface view."""
    merged_ports: dict[tuple[str, str], dict] = {}

    # Deduplicate ports by (container, interface) — agent may return
    # duplicate entries for the same interface with different OVS ports
    for port in docker_ports or []:
        container_name = port.get("container")
        linux_interface = port.get("interface")
        if not container_name or not linux_interface:
            result["skipped"] += 1
            continue
        runtime_name = container_name
        match = re.match(r'archetype-[a-f0-9-]+-(.+)$', runtime_name)
        node_name = match.group(1) if match else runtime_name
        if allowed_node_names is not None and node_name not in allowed_node_names:
            continue
        merged_ports[(node_name, linux_interface)] = {
            "node_name": node_name,
            "runtime_name": runtime_name,
            "linux_interface": linux_interface,
            "ovs_port": port.get("port_name"),
            "ovs_bridge": port.get("bridge_name"),
            "vlan_tag": port.get("vlan_tag"),
        }

    # Port-state uses topology node names and includes libvirt VM ports.
    # Prefer it over Docker plugin inventory whenever both exist.
    for port in port_state or []:
        node_name = port.get("node_name")
        linux_interface = port.get("interface_name")
        if not node_name or not linux_interface:
            result["skipped"] += 1
            continue
        if allowed_node_names is not None and node_name not in allowed_node_names:
            continue
        merged_ports[(node_name, linux_interface)] = {
            "node_name": node_name,
            "runtime_name": node_name,
            "linux_interface": linux_interface,
            "ovs_port": port.get("ovs_port_name"),
            "ovs_bridge": "arch-ovs",
            "vlan_tag": port.get("vlan_tag"),
        }

    return merged_ports


def _upsert_interface_mappings(
    database: Session,
    lab_id: str,
    nodes: list[models.Node],
    merged_ports: dict[tuple[str, str], dict],
    result: dict[str, int],
) -> None:
    """Upsert InterfaceMapping rows from merged live agent port data."""
    node_by_container = {n.container_name: n for n in nodes}
    node_by_display = {n.display_name: n for n in nodes}
    now = datetime.now(timezone.utc)

    for (_key_name, linux_interface), port in merged_ports.items():
        runtime_name = port.get("runtime_name") or port.get("node_name") or ""
        node_name = port.get("node_name") or runtime_name

        node = (
            node_by_container.get(node_name)
            or node_by_display.get(node_name)
            or node_by_container.get(runtime_name)
        )
        if not node:
            logger.debug(f"No node found for runtime {runtime_name} (tried: {node_name})")
            result["skipped"] += 1
            continue

        device_type = node.device
        vendor_interface = linux_to_vendor_interface(linux_interface, device_type)

        existing = (
            database.query(models.InterfaceMapping)
            .filter(
                models.InterfaceMapping.lab_id == lab_id,
                models.InterfaceMapping.node_id == node.id,
                models.InterfaceMapping.linux_interface == linux_interface,
            )
            .first()
        )

        if existing:
            existing.ovs_port = port.get("ovs_port")
            existing.ovs_bridge = port.get("ovs_bridge")
            existing.vlan_tag = port.get("vlan_tag")
            existing.vendor_interface = vendor_interface
            existing.device_type = device_type
            existing.last_verified_at = now
            result["updated"] += 1
        else:
            mapping = models.InterfaceMapping(
                id=str(uuid4()),
                lab_id=lab_id,
                node_id=node.id,
                ovs_port=port.get("ovs_port"),
                ovs_bridge=port.get("ovs_bridge"),
                vlan_tag=port.get("vlan_tag"),
                linux_interface=linux_interface,
                vendor_interface=vendor_interface,
                device_type=device_type,
                last_verified_at=now,
            )
            database.add(mapping)
            result["created"] += 1


def linux_to_vendor_interface(linux_if: str, device_type: str | None) -> str | None:
    """Convert Linux interface name to vendor-specific name.

    Delegates to the centralized denormalize_interface().

    Args:
        linux_if: Linux interface name (e.g., "eth1")
        device_type: Device type (e.g., "arista_ceos")

    Returns:
        Vendor interface name or None if cannot convert
    """
    if not device_type:
        return None

    result = denormalize_interface(linux_if, device_type)
    # Return None if no conversion was possible (input returned unchanged
    # and it wasn't already a vendor name)
    if result == linux_if:
        # Check if the input was actually an eth-style name that just uses eth naming
        if re.match(r"^eth\d+$", linux_if, re.IGNORECASE):
            return result  # eth naming device — eth1 IS the vendor name
        return None
    return result


def vendor_to_linux_interface(vendor_if: str, device_type: str | None) -> str | None:
    """Convert vendor interface name to Linux interface name.

    Delegates to the centralized normalize_interface().

    Args:
        vendor_if: Vendor interface name (e.g., "Ethernet1")
        device_type: Device type (e.g., "arista_ceos")

    Returns:
        Linux interface name or None if cannot convert
    """
    result = normalize_interface(vendor_if, device_type)
    if result == vendor_if and not re.match(r"^eth\d+$", vendor_if, re.IGNORECASE):
        return None
    return result


async def populate_from_agent(
    database: Session,
    lab_id: str,
    agent: models.Host,
    *,
    target_node: models.Node | None = None,
) -> dict:
    """Fetch port info from agent and populate interface_mappings.

    Args:
        database: Database session
        lab_id: Lab identifier
        agent: Agent to query

    Returns:
        Dict with counts: created, updated, errors
    """
    result = {"created": 0, "updated": 0, "errors": 0, "skipped": 0}

    nodes_q = database.query(models.Node).filter(models.Node.lab_id == lab_id)
    if target_node is not None:
        nodes_q = nodes_q.filter(models.Node.id == target_node.id)
    nodes = nodes_q.all()
    if not nodes:
        logger.debug(f"No nodes found for interface mapping sync in lab {lab_id}")
        return result

    allowed_node_names: set[str] | None = None
    if target_node is not None:
        allowed_node_names = {
            value for value in (target_node.display_name, target_node.container_name)
            if value
        }

    # Merge provider-specific and provider-agnostic feeds.
    # Docker OVS plugin inventory is useful for bridge/container naming, but
    # agent port-state is the authoritative source for live VM endpoint data.
    docker_ports = await agent_client.get_lab_ports_from_agent(agent, lab_id) or []
    port_state = await agent_client.get_lab_port_state(agent, lab_id) or []

    if not docker_ports and not port_state:
        logger.debug(f"No ports returned from agent {agent.name} for lab {lab_id}")
        return result

    merged_ports = _merge_agent_ports(
        docker_ports,
        port_state,
        result,
        allowed_node_names=allowed_node_names,
    )
    _upsert_interface_mappings(database, lab_id, nodes, merged_ports, result)

    database.commit()
    logger.info(
        f"Interface mapping sync for lab {lab_id}: "
        f"created={result['created']}, updated={result['updated']}, "
        f"skipped={result['skipped']}"
    )
    return result


async def populate_node_from_agent(
    database: Session,
    lab_id: str,
    node: models.Node,
    agent: models.Host,
) -> dict:
    """Refresh interface mappings for a single node from live agent data."""
    return await populate_from_agent(
        database,
        lab_id,
        agent,
        target_node=node,
    )


async def populate_all_agents(
    database: Session,
    lab_id: str,
) -> dict:
    """Populate interface mappings from all agents that have nodes for this lab.

    Args:
        database: Database session
        lab_id: Lab identifier

    Returns:
        Dict with counts: created, updated, errors, agents_queried
    """
    result = {"created": 0, "updated": 0, "errors": 0, "agents_queried": 0}

    # Get all placements for this lab to find which agents to query
    placements = (
        database.query(models.NodePlacement)
        .filter(models.NodePlacement.lab_id == lab_id)
        .all()
    )

    # Get unique host IDs
    host_ids = set(p.host_id for p in placements)

    # Get online agents
    agents = (
        database.query(models.Host)
        .filter(
            models.Host.id.in_(host_ids),
            models.Host.status == "online",
        )
        .all()
    )

    for agent in agents:
        try:
            agent_result = await populate_from_agent(database, lab_id, agent)
            result["created"] += agent_result["created"]
            result["updated"] += agent_result["updated"]
            result["errors"] += agent_result["errors"]
            result["agents_queried"] += 1
        except Exception as e:
            database.rollback()
            logger.error(f"Failed to populate mappings from agent {agent.name}: {e}")
            result["errors"] += 1

    return result


def get_mapping(
    database: Session,
    lab_id: str,
    node_id: str,
    linux_interface: str,
) -> models.InterfaceMapping | None:
    """Get interface mapping for a specific interface.

    Args:
        database: Database session
        lab_id: Lab identifier
        node_id: Node ID (database ID, not GUI ID)
        linux_interface: Linux interface name (e.g., "eth1")

    Returns:
        InterfaceMapping or None
    """
    return (
        database.query(models.InterfaceMapping)
        .filter(
            models.InterfaceMapping.lab_id == lab_id,
            models.InterfaceMapping.node_id == node_id,
            models.InterfaceMapping.linux_interface == linux_interface,
        )
        .first()
    )


def get_mapping_by_ovs_port(
    database: Session,
    ovs_port: str,
) -> models.InterfaceMapping | None:
    """Get interface mapping by OVS port name.

    Args:
        database: Database session
        ovs_port: OVS port name (e.g., "vh614ed63ed40")

    Returns:
        InterfaceMapping or None
    """
    return (
        database.query(models.InterfaceMapping)
        .filter(models.InterfaceMapping.ovs_port == ovs_port)
        .first()
    )


def update_vlan_tag(
    database: Session,
    lab_id: str,
    node_id: str,
    linux_interface: str,
    vlan_tag: int,
) -> bool:
    """Update the VLAN tag for an interface mapping.

    Args:
        database: Database session
        lab_id: Lab identifier
        node_id: Node ID (database ID)
        linux_interface: Linux interface name
        vlan_tag: New VLAN tag

    Returns:
        True if mapping was updated, False if not found
    """
    mapping = get_mapping(database, lab_id, node_id, linux_interface)
    if not mapping:
        return False

    mapping.vlan_tag = vlan_tag
    database.commit()
    return True


def delete_lab_mappings(database: Session, lab_id: str) -> int:
    """Delete all interface mappings for a lab.

    Args:
        database: Database session
        lab_id: Lab identifier

    Returns:
        Number of mappings deleted
    """
    count = (
        database.query(models.InterfaceMapping)
        .filter(models.InterfaceMapping.lab_id == lab_id)
        .delete()
    )
    database.commit()
    return count
