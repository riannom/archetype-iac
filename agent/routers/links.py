"""Link hot-connect and hot-disconnect endpoints."""
from __future__ import annotations

import logging

from fastapi import APIRouter

from agent.config import settings
from agent.helpers import (
    get_provider_for_request,
    _get_docker_ovs_plugin,
    _resolve_ovs_port,
    _ovs_set_port_vlan,
    _ovs_allocate_link_vlan,
    _ovs_list_used_vlans,
    _pick_isolation_vlan,
    OVSPortInfo,
)
from agent.network.backends.registry import get_network_backend
from agent.schemas import (
    LinkCreate,
    LinkCreateResponse,
    LinkDeleteResponse,
    LinkInfo,
    LinkListResponse,
    LinkState,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["links"])


def _split_link_id_candidates(link_id: str) -> list[tuple[str, str, str, str]]:
    """Parse link_id into endpoint candidates.

    Supports node names containing "-" by trying each separator position.
    """
    candidates: list[tuple[str, str, str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()

    for idx, char in enumerate(link_id):
        if char != "-":
            continue

        left = link_id[:idx]
        right = link_id[idx + 1:]
        if ":" not in left or ":" not in right:
            continue

        node_a, iface_a = left.split(":", 1)
        node_b, iface_b = right.split(":", 1)
        if not node_a or not iface_a or not node_b or not iface_b:
            continue

        candidate = (node_a, iface_a, node_b, iface_b)
        if candidate in seen:
            continue
        seen.add(candidate)
        candidates.append(candidate)

    return candidates


async def _resolve_link_id_endpoints(
    lab_id: str,
    link_id: str,
) -> tuple[tuple[str, str, str, str, OVSPortInfo | None, OVSPortInfo | None] | None, str | None]:
    """Resolve link_id to endpoints and OVS ports.

    Returns:
        ((node_a, iface_a, node_b, iface_b, port_a, port_b), None) on success
        (None, error_message) for invalid or ambiguous IDs
    """
    candidates = _split_link_id_candidates(link_id)
    if not candidates:
        return None, f"Invalid link_id format: {link_id}"

    resolved_port_cache: dict[tuple[str, str], OVSPortInfo | None] = {}
    successful: list[tuple[str, str, str, str, OVSPortInfo, OVSPortInfo]] = []

    for node_a, iface_a, node_b, iface_b in candidates:
        key_a = (node_a, iface_a)
        key_b = (node_b, iface_b)

        if key_a not in resolved_port_cache:
            resolved_port_cache[key_a] = await _resolve_ovs_port(lab_id, node_a, iface_a)
        if key_b not in resolved_port_cache:
            resolved_port_cache[key_b] = await _resolve_ovs_port(lab_id, node_b, iface_b)

        port_a = resolved_port_cache[key_a]
        port_b = resolved_port_cache[key_b]
        if port_a and port_b:
            successful.append((node_a, iface_a, node_b, iface_b, port_a, port_b))

    if len(successful) > 1:
        return None, f"Ambiguous link_id format: {link_id}"

    if len(successful) == 1:
        node_a, iface_a, node_b, iface_b, port_a, port_b = successful[0]
        return (node_a, iface_a, node_b, iface_b, port_a, port_b), None

    # Fail closed if link_id cannot be resolved to both endpoints.
    return None, f"Unresolvable link_id endpoints: {link_id}"


@router.post("/labs/{lab_id}/links")
async def create_link(lab_id: str, link: LinkCreate) -> LinkCreateResponse:
    """Hot-connect two interfaces in a running lab.

    Creates a Layer 2 link between two node interfaces by assigning them
    the same VLAN tag on the shared OVS bridge. Works across providers:
    Docker↔Docker, Libvirt↔Libvirt, and Docker↔Libvirt.

    Args:
        lab_id: Lab identifier
        link: Link creation request with source/target nodes and interfaces

    Returns:
        LinkCreateResponse with link details or error
    """
    if not settings.enable_ovs:
        return LinkCreateResponse(
            success=False,
            error="OVS networking not enabled on this agent",
        )

    logger.info(
        f"Hot-connect request: lab={lab_id}, "
        f"{link.source_node}:{link.source_interface} <-> "
        f"{link.target_node}:{link.target_interface}"
    )

    try:
        # Resolve OVS ports for both endpoints (provider-agnostic)
        port_a = await _resolve_ovs_port(lab_id, link.source_node, link.source_interface)
        port_b = await _resolve_ovs_port(lab_id, link.target_node, link.target_interface)

        if not port_a:
            return LinkCreateResponse(
                success=False,
                error=f"Cannot find OVS port for {link.source_node}:{link.source_interface}",
            )
        if not port_b:
            return LinkCreateResponse(
                success=False,
                error=f"Cannot find OVS port for {link.target_node}:{link.target_interface}",
            )

        bridge = settings.ovs_bridge_name or "arch-ovs"
        shared_vlan = await _ovs_allocate_link_vlan(bridge)
        if shared_vlan is None:
            return LinkCreateResponse(
                success=False,
                error="No free VLAN available for link creation",
            )

        old_vlan_a = port_a.vlan_tag
        old_vlan_b = port_b.vlan_tag

        if not await _ovs_set_port_vlan(port_a.port_name, shared_vlan):
            return LinkCreateResponse(
                success=False,
                error=f"Failed to set VLAN on {port_a.port_name}",
            )

        if not await _ovs_set_port_vlan(port_b.port_name, shared_vlan):
            # Roll back source side on partial failure.
            await _ovs_set_port_vlan(port_a.port_name, old_vlan_a)
            return LinkCreateResponse(
                success=False,
                error=f"Failed to set VLAN on {port_b.port_name}",
            )

        # Update Docker plugin tracking if either endpoint is Docker.
        # Also release old tracked tags from either pool to avoid stale allocator state.
        plugin = _get_docker_ovs_plugin() if settings.enable_ovs_plugin else None
        if plugin:
            plugin_updated = False
            for resolved_port, old_vlan in ((port_a, old_vlan_a), (port_b, old_vlan_b)):
                if resolved_port.provider != "docker":
                    continue
                for ep in plugin.endpoints.values():
                    if ep.host_veth == resolved_port.port_name:
                        plugin._release_vlan(old_vlan)
                        plugin._release_linked_vlan(old_vlan)
                        ep.vlan_tag = shared_vlan
                        plugin_updated = True
                        break
            if plugin_updated:
                await plugin._mark_dirty_and_save()

        link_id = f"{link.source_node}:{link.source_interface}-{link.target_node}:{link.target_interface}"

        logger.info(
            f"Connected {link.source_node}:{link.source_interface} ({port_a.provider}:{port_a.port_name}) "
            f"<-> {link.target_node}:{link.target_interface} ({port_b.provider}:{port_b.port_name}) "
            f"via VLAN {shared_vlan}"
        )

        return LinkCreateResponse(
            success=True,
            link=LinkInfo(
                link_id=link_id,
                lab_id=lab_id,
                source_node=link.source_node,
                source_interface=link.source_interface,
                target_node=link.target_node,
                target_interface=link.target_interface,
                state=LinkState.CONNECTED,
                vlan_tag=shared_vlan,
                source_ovs_port=port_a.port_name,
                target_ovs_port=port_b.port_name,
            ),
        )

    except Exception as e:
        logger.error(f"Hot-connect failed: {e}")
        return LinkCreateResponse(
            success=False,
            error=str(e),
        )


@router.delete("/labs/{lab_id}/links/{link_id}")
async def delete_link(lab_id: str, link_id: str) -> LinkDeleteResponse:
    """Hot-disconnect a link in a running lab.

    Breaks a Layer 2 link by assigning each endpoint a unique VLAN tag.
    Works across providers: Docker↔Docker, Libvirt↔Libvirt, Docker↔Libvirt.

    Args:
        lab_id: Lab identifier
        link_id: Link identifier (format: "node1:iface1-node2:iface2")

    Returns:
        LinkDeleteResponse with success status
    """
    if not settings.enable_ovs:
        return LinkDeleteResponse(
            success=False,
            error="OVS networking not enabled on this agent",
        )

    logger.info(f"Hot-disconnect request: lab={lab_id}, link={link_id}")

    try:
        # Parse link_id and resolve endpoints/ports. Supports hyphenated node names.
        resolved, parse_error = await _resolve_link_id_endpoints(lab_id, link_id)
        if parse_error:
            return LinkDeleteResponse(success=False, error=parse_error)
        assert resolved is not None  # parse_error is None
        node_a, iface_a, node_b, iface_b, port_a, port_b = resolved
        if not port_a or not port_b:
            return LinkDeleteResponse(
                success=False,
                error=f"Unresolvable link endpoints for {link_id}",
            )

        bridge = settings.ovs_bridge_name or "arch-ovs"
        used = await _ovs_list_used_vlans(bridge)
        endpoint_plans: list[tuple[OVSPortInfo, str, str, int, int | None]] = []
        errors: list[str] = []

        # Plan both endpoint VLAN moves first to keep disconnect transactional.
        for port, node, iface in ((port_a, node_a, iface_a), (port_b, node_b, iface_b)):
            new_vlan = _pick_isolation_vlan(used, bridge, port.port_name)
            if new_vlan is None:
                errors.append(f"Failed to allocate VLAN for {node}:{iface}")
                continue

            used.add(new_vlan)
            endpoint_plans.append((port, node, iface, new_vlan, port.vlan_tag))

        if errors:
            return LinkDeleteResponse(success=False, error="; ".join(errors))

        # Apply VLAN changes; roll back all changed ports if any endpoint fails.
        applied: list[tuple[str, int | None]] = []
        for port, node, iface, new_vlan, old_vlan in endpoint_plans:
            if not await _ovs_set_port_vlan(port.port_name, new_vlan):
                rollback_errors: list[str] = []
                for applied_port, previous_vlan in reversed(applied):
                    if previous_vlan is None:
                        continue
                    if not await _ovs_set_port_vlan(applied_port, previous_vlan):
                        rollback_errors.append(applied_port)
                rollback_detail = ""
                if rollback_errors:
                    rollback_detail = (
                        f" (rollback failed for ports: {', '.join(rollback_errors)})"
                    )
                return LinkDeleteResponse(
                    success=False,
                    error=f"Failed to disconnect {node}:{iface}{rollback_detail}",
                )
            applied.append((port.port_name, old_vlan))

        # Update Docker plugin state for endpoints touched via direct OVS.
        plugin = _get_docker_ovs_plugin() if settings.enable_ovs_plugin else None
        if plugin:
            plugin_updated = False
            for port, _, _, new_vlan, old_vlan in endpoint_plans:
                if port.provider != "docker":
                    continue
                for ep in plugin.endpoints.values():
                    if ep.host_veth != port.port_name:
                        continue
                    plugin._release_vlan(old_vlan)
                    plugin._release_linked_vlan(old_vlan)
                    if 100 <= new_vlan <= 2049:
                        plugin._allocated_vlans.add(new_vlan)
                    elif 2050 <= new_vlan <= 4000:
                        plugin._allocated_linked_vlans.add(new_vlan)
                    ep.vlan_tag = new_vlan
                    plugin_updated = True
                    break
            if plugin_updated:
                await plugin._mark_dirty_and_save()

        return LinkDeleteResponse(success=True)

    except Exception as e:
        logger.error(f"Hot-disconnect failed: {e}")
        return LinkDeleteResponse(
            success=False,
            error=str(e),
        )


@router.get("/labs/{lab_id}/links")
async def list_links(lab_id: str) -> LinkListResponse:
    """List all links and their connection states for a lab.

    Returns all OVS-managed links for the specified lab, including
    their VLAN tags and connection state.

    Args:
        lab_id: Lab identifier

    Returns:
        LinkListResponse with list of links
    """
    if not settings.enable_ovs:
        return LinkListResponse(links=[])

    try:
        backend = get_network_backend()
        if not backend.ovs_initialized():
            return LinkListResponse(links=[])

        # Get provider for container name resolution
        get_provider_for_request()

        links = []
        for ovs_link in backend.get_links_for_lab(lab_id):
            # Parse port keys to get node/interface names
            # Format: "container_name:interface_name"
            port_a_parts = ovs_link.port_a.rsplit(":", 1)
            port_b_parts = ovs_link.port_b.rsplit(":", 1)

            # Extract node names from container names
            # Container format: "archetype-{lab_id}-{node_name}"
            source_node = port_a_parts[0].split("-")[-1] if port_a_parts else ""
            target_node = port_b_parts[0].split("-")[-1] if port_b_parts else ""
            source_interface = port_a_parts[1] if len(port_a_parts) > 1 else ""
            target_interface = port_b_parts[1] if len(port_b_parts) > 1 else ""

            links.append(LinkInfo(
                link_id=ovs_link.link_id,
                lab_id=ovs_link.lab_id,
                source_node=source_node,
                source_interface=source_interface,
                target_node=target_node,
                target_interface=target_interface,
                state=LinkState.CONNECTED,
                vlan_tag=ovs_link.vlan_tag,
            ))

        return LinkListResponse(links=links)

    except Exception as e:
        logger.error(f"List links failed: {e}")
        return LinkListResponse(links=[])
