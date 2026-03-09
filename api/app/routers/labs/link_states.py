"""Link state management endpoints for labs.

NOTE: Several symbols (agent_client, TopologyService, get_online_agent_for_lab,
reconcile_lab_links, sync_link_endpoint_reservations, recompute_link_oper_state)
are resolved through the parent package (``app.routers.labs``) so that test
monkeypatching on that path continues to work.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import db, models, schemas
from app.auth import get_current_user
from app.services.interface_naming import normalize_interface
from app.services.link_reservations import get_conflicting_link_details
from app.state import (
    LabState,
    LinkActualState,
    LinkDesiredState,
)
from app.utils.http import raise_not_found, raise_unavailable
from app.utils.lab import get_lab_or_404, get_lab_provider, require_lab_editor
from app.utils.link import canonicalize_link_endpoints, generate_link_name, link_state_endpoint_key


def _pkg():
    """Resolve the parent package for monkeypatch-safe attribute access."""
    return sys.modules["app.routers.labs"]

logger = logging.getLogger(__name__)

router = APIRouter(tags=["labs"])


def _choose_preferred_link_state(
    states: list[models.LinkState],
    node_device_map: dict[str, str | None] | None = None,
) -> models.LinkState:
    """Choose one row to keep when duplicate endpoint records exist."""
    def _is_canonical_row(state: models.LinkState) -> bool:
        src_n, src_i, tgt_n, tgt_i = link_state_endpoint_key(state, node_device_map)
        canonical_name = generate_link_name(src_n, src_i, tgt_n, tgt_i)
        src_dev = (node_device_map or {}).get(state.source_node)
        tgt_dev = (node_device_map or {}).get(state.target_node)
        stored_src_i = normalize_interface(state.source_interface, src_dev)
        stored_tgt_i = normalize_interface(state.target_interface, tgt_dev)
        return (
            state.link_name == canonical_name
            and state.source_node == src_n
            and stored_src_i == src_i
            and state.target_node == tgt_n
            and stored_tgt_i == tgt_i
        )

    return sorted(
        states,
        key=lambda s: (
            _is_canonical_row(s),
            s.desired_state != "deleted",
            s.updated_at or datetime.min.replace(tzinfo=timezone.utc),
        ),
    )[-1]


def _find_matching_link_state(
    states: list[models.LinkState],
    link_definition_id: str,
    node_device_map: dict[str, str | None] | None = None,
) -> tuple[models.LinkState | None, list[models.LinkState]]:
    """Find matching LinkState rows by deterministic Link definition id."""
    matches = [s for s in states if s.link_definition_id == link_definition_id]
    if not matches:
        return None, []
    preferred = _choose_preferred_link_state(matches, node_device_map)
    return preferred, [s for s in matches if s.id != preferred.id]


def _find_matching_link_state_by_endpoints(
    states: list[models.LinkState],
    src_n: str,
    src_i: str,
    tgt_n: str,
    tgt_i: str,
    node_device_map: dict[str, str | None] | None = None,
) -> tuple[models.LinkState | None, list[models.LinkState]]:
    """Compatibility matcher for legacy rows missing link_definition_id."""
    key = (src_n, src_i, tgt_n, tgt_i)
    matches = [s for s in states if link_state_endpoint_key(s, node_device_map) == key]
    if not matches:
        return None, []
    preferred = _choose_preferred_link_state(matches, node_device_map)
    return preferred, [s for s in matches if s.id != preferred.id]


def _parse_link_id_endpoints(link_id: str) -> tuple[str, str, str, str] | None:
    """Best-effort parse for link id format: nodeA:ifaceA-nodeB:ifaceB."""
    if "-" not in link_id or ":" not in link_id:
        return None
    left, right = link_id.split("-", 1)
    if ":" not in left or ":" not in right:
        return None
    src_n, src_i = left.rsplit(":", 1)
    tgt_n, tgt_i = right.rsplit(":", 1)
    if not src_n or not src_i or not tgt_n or not tgt_i:
        return None
    return canonicalize_link_endpoints(src_n, src_i, tgt_n, tgt_i)


def _sync_link_oper_state(database: Session, link_state: models.LinkState) -> None:
    _pkg().recompute_link_oper_state(database, link_state)


def _apply_link_state_canonical_fields(
    state: models.LinkState,
    *,
    link_def_id: str,
    link_name: str,
    src_n: str,
    src_i: str,
    tgt_n: str,
    tgt_i: str,
) -> bool:
    """Align a LinkState row with the canonical topology representation."""
    changed = (
        state.link_name != link_name
        or state.source_node != src_n
        or state.source_interface != src_i
        or state.target_node != tgt_n
        or state.target_interface != tgt_i
        or state.link_definition_id != link_def_id
    )
    state.link_name = link_name
    state.source_node = src_n
    state.source_interface = src_i
    state.target_node = tgt_n
    state.target_interface = tgt_i
    state.link_definition_id = link_def_id
    if state.desired_state == "deleted":
        state.desired_state = LinkDesiredState.UP
        changed = True
    return changed


def _get_or_create_link_definition(
    database: Session,
    lab_id: str,
    link_name: str,
    src_n: str,
    src_i: str,
    tgt_n: str,
    tgt_i: str,
    *,
    node_by_name: dict[str, models.Node] | None = None,
    strict: bool = False,
) -> models.Link | None:
    """Resolve Link definition by canonical name, creating it if missing."""
    link_def = (
        database.query(models.Link)
        .filter(
            models.Link.lab_id == lab_id,
            models.Link.link_name == link_name,
        )
        .first()
    )
    if link_def:
        return link_def

    node_by_name = node_by_name or {}
    src_node = node_by_name.get(src_n) or (
        database.query(models.Node)
        .filter(models.Node.lab_id == lab_id, models.Node.container_name == src_n)
        .first()
    )
    tgt_node = node_by_name.get(tgt_n) or (
        database.query(models.Node)
        .filter(models.Node.lab_id == lab_id, models.Node.container_name == tgt_n)
        .first()
    )
    if not src_node or not tgt_node:
        detail = (
            f"Cannot resolve link endpoints for '{link_name}' "
            f"(source='{src_n}', target='{tgt_n}')"
        )
        if strict:
            raise_not_found(detail)
        logger.warning("Skipping Link definition creation in lab %s: %s", lab_id, detail)
        return None

    link_def = models.Link(
        lab_id=lab_id,
        link_name=link_name,
        source_node_id=src_node.id,
        source_interface=src_i,
        target_node_id=tgt_node.id,
        target_interface=tgt_i,
    )
    savepoint = database.begin_nested()
    try:
        database.add(link_def)
        database.flush()
        savepoint.commit()
        return link_def
    except IntegrityError:
        savepoint.rollback()
        # Concurrent request likely inserted the same logical link.
        existing = (
            database.query(models.Link)
            .filter(
                models.Link.lab_id == lab_id,
                models.Link.link_name == link_name,
            )
            .first()
        )
        if existing:
            return existing
        raise


def _link_endpoint_payload(state: models.LinkState) -> list[dict[str, str]]:
    return [
        {
            "node_name": state.source_node,
            "interface_name": normalize_interface(state.source_interface or ""),
        },
        {
            "node_name": state.target_node,
            "interface_name": normalize_interface(state.target_interface or ""),
        },
    ]


def _raise_link_endpoint_conflict(
    database: Session,
    state: models.LinkState,
    conflicts: list[str],
) -> None:
    endpoints = _link_endpoint_payload(state)
    conflict_details = get_conflicting_link_details(
        database,
        state.lab_id,
        state.id,
        [(endpoint["node_name"], endpoint["interface_name"]) for endpoint in endpoints],
    )
    all_conflicting_links = sorted(
        {
            link
            for detail in conflict_details
            for link in detail.get("conflicting_links", [])
            if isinstance(link, str)
        }
    )
    if not all_conflicting_links:
        all_conflicting_links = sorted(conflicts)

    raise HTTPException(
        status_code=409,
        detail={
            "code": "link_endpoint_reserved",
            "message": "Endpoint already reserved by desired-up link(s).",
            "link": {
                "lab_id": state.lab_id,
                "link_name": state.link_name,
                "desired_state": state.desired_state,
                "endpoints": endpoints,
            },
            "conflicting_links": all_conflicting_links,
            "conflicting_endpoints": conflict_details,
        },
    )


def _upsert_link_states(
    database: Session,
    lab_id: str,
    graph: schemas.TopologyGraph,
) -> tuple[int, int, list[str], list[dict]]:
    """Create or update LinkState records for all links in a topology graph.

    New links are initialized with desired_state='up', actual_state='unknown'.
    Existing links retain their desired_state (user preference persists).
    Links removed from topology are marked for deletion (caller handles teardown).

    Returns:
        Tuple of (created_count, updated_count, added_link_names, removed_link_info)
        - added_link_names: List of newly created link names
        - removed_link_info: List of dicts with info about removed links for teardown
    """
    # Get existing link states for this lab
    existing_states = (
        database.query(models.LinkState)
        .filter(models.LinkState.lab_id == lab_id)
        .all()
    )

    # Build node ID to name mapping for resolving link endpoints
    # Node endpoints in links reference node IDs, not names
    node_id_to_name: dict[str, str] = {}
    for node in graph.nodes:
        # Use container_name (YAML key) for consistency
        node_id_to_name[node.id] = node.container_name or node.name

    # Build node name to host_id mapping from database
    # This is used to populate source_host_id/target_host_id on new LinkState records
    db_nodes = (
        database.query(models.Node)
        .filter(models.Node.lab_id == lab_id)
        .all()
    )
    node_name_to_def: dict[str, models.Node] = {n.container_name: n for n in db_nodes}
    node_name_to_host: dict[str, str | None] = {
        n.container_name: n.host_id for n in db_nodes
    }
    node_name_to_device: dict[str, str | None] = {
        n.container_name: n.device for n in db_nodes
    }
    link_def_by_name: dict[str, models.Link] = {
        lnk.link_name: lnk
        for lnk in (
            database.query(models.Link)
            .filter(models.Link.lab_id == lab_id)
            .all()
        )
    }

    # Also check NodePlacement for nodes without host_id set
    placements = (
        database.query(models.NodePlacement)
        .filter(models.NodePlacement.lab_id == lab_id)
        .all()
    )
    for p in placements:
        if p.node_name not in node_name_to_host or not node_name_to_host.get(p.node_name):
            node_name_to_host[p.node_name] = p.host_id

    # Track which links are in the current topology
    current_link_names: set[str] = set()
    created_count = 0
    updated_count = 0
    added_link_names: list[str] = []
    removed_link_info: list[dict] = []
    mutated_states: list[models.LinkState] = []

    for link in graph.links:
        if len(link.endpoints) != 2:
            continue  # Skip non-point-to-point links

        ep_a, ep_b = link.endpoints

        # Skip external endpoints (bridge, macvlan, host)
        if ep_a.type != "node" or ep_b.type != "node":
            continue

        # Resolve node IDs to names and canonicalize endpoints
        source_node = node_id_to_name.get(ep_a.node, ep_a.node)
        target_node = node_id_to_name.get(ep_b.node, ep_b.node)
        src_n, src_i, tgt_n, tgt_i = canonicalize_link_endpoints(
            source_node,
            ep_a.ifname or "eth0",
            target_node,
            ep_b.ifname or "eth0",
            source_device=node_name_to_device.get(source_node),
            target_device=node_name_to_device.get(target_node),
        )
        link_name = generate_link_name(src_n, src_i, tgt_n, tgt_i)
        current_link_names.add(link_name)
        link_def = link_def_by_name.get(link_name)
        if not link_def:
            link_def = _get_or_create_link_definition(
                database,
                lab_id,
                link_name,
                src_n,
                src_i,
                tgt_n,
                tgt_i,
                node_by_name=node_name_to_def,
            )
            if not link_def:
                continue
            link_def_by_name[link_name] = link_def

        existing, duplicates = _find_matching_link_state(
            existing_states,
            link_def.id,
            node_name_to_device,
        )
        if not existing:
            legacy_existing, legacy_duplicates = _find_matching_link_state_by_endpoints(
                [state for state in existing_states if state.link_definition_id is None],
                src_n,
                src_i,
                tgt_n,
                tgt_i,
                node_name_to_device,
            )
            if legacy_existing:
                existing = legacy_existing
                duplicates = duplicates + legacy_duplicates
        # Old naming variants can collide to the same canonical endpoints.
        # Delete duplicates immediately so the preferred row's link_name
        # rename doesn't hit the unique constraint (uq_link_state_lab_link).
        for duplicate in duplicates:
            existing_states.remove(duplicate)
            database.delete(duplicate)
        if duplicates:
            database.flush()

        if existing:
            # Update existing link state to canonical storage
            existing_changed = _apply_link_state_canonical_fields(
                existing,
                link_def_id=link_def.id,
                link_name=link_name,
                src_n=src_n,
                src_i=src_i,
                tgt_n=tgt_n,
                tgt_i=tgt_i,
            )
            if existing_changed:
                mutated_states.append(existing)
            updated_count += 1
        else:
            # Create new link state
            # Look up host_ids for the endpoints
            src_host_id = node_name_to_host.get(src_n)
            tgt_host_id = node_name_to_host.get(tgt_n)
            is_cross_host = (
                src_host_id is not None
                and tgt_host_id is not None
                and src_host_id != tgt_host_id
            )

            new_state = models.LinkState(
                lab_id=lab_id,
                link_definition_id=link_def.id,
                link_name=link_name,
                source_node=src_n,
                source_interface=src_i,
                target_node=tgt_n,
                target_interface=tgt_i,
                source_host_id=src_host_id,
                target_host_id=tgt_host_id,
                is_cross_host=is_cross_host,
                desired_state=LinkDesiredState.UP,
                actual_state=LinkActualState.UNKNOWN,
            )
            savepoint = database.begin_nested()
            try:
                database.add(new_state)
                database.flush()
                savepoint.commit()
                existing_states.append(new_state)
                mutated_states.append(new_state)
                added_link_names.append(link_name)
                created_count += 1
            except IntegrityError:
                savepoint.rollback()
                existing = (
                    database.query(models.LinkState)
                    .filter(
                        models.LinkState.lab_id == lab_id,
                        models.LinkState.link_name == link_name,
                    )
                    .first()
                )
                if not existing:
                    existing, _ = _find_matching_link_state(
                        (
                            database.query(models.LinkState)
                            .filter(models.LinkState.lab_id == lab_id)
                            .all()
                        ),
                        link_def.id,
                        node_name_to_device,
                    )
                if not existing:
                    raise
                if all(state.id != existing.id for state in existing_states):
                    existing_states.append(existing)
                existing_changed = _apply_link_state_canonical_fields(
                    existing,
                    link_def_id=link_def.id,
                    link_name=link_name,
                    src_n=src_n,
                    src_i=src_i,
                    tgt_n=tgt_n,
                    tgt_i=tgt_i,
                )
                if existing_changed:
                    mutated_states.append(existing)
                updated_count += 1

    # Collect info about links to remove (for teardown) before deleting
    for existing_state in existing_states:
        if existing_state.link_name not in current_link_names:
            # Store info needed for teardown before deletion
            removed_link_info.append({
                "link_name": existing_state.link_name,
                "source_node": existing_state.source_node,
                "source_interface": existing_state.source_interface,
                "target_node": existing_state.target_node,
                "target_interface": existing_state.target_interface,
                "is_cross_host": existing_state.is_cross_host,
                "actual_state": existing_state.actual_state,
                "source_host_id": existing_state.source_host_id,
                "target_host_id": existing_state.target_host_id,
                "vni": existing_state.vni,
            })
            # Don't delete here - let the live_links task handle teardown first
            # The task will delete after successful teardown
            # For now, mark as pending deletion but keep the record
            existing_state.desired_state = "deleted"
            mutated_states.append(existing_state)

    for state in mutated_states:
        ok, conflicts = _pkg().sync_link_endpoint_reservations(database, state)
        if not ok:
            _raise_link_endpoint_conflict(database, state, conflicts)
        _sync_link_oper_state(database, state)

    return created_count, updated_count, added_link_names, removed_link_info


def _ensure_link_states_exist(
    database: Session,
    lab_id: str,
) -> None:
    """Ensure LinkState records exist for all links in the topology.

    Uses database as source of truth.
    Safe to call multiple times - idempotent operation.
    """
    service = _pkg().TopologyService(database)
    if service.has_nodes(lab_id):
        graph = service.export_to_graph(lab_id)
        # Ignore the added/removed info - this is just for ensuring records exist
        _pkg()._upsert_link_states(database, lab_id, graph)
        database.commit()


@router.get("/labs/{lab_id}/links/states")
def list_link_states(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.LinkStatesResponse:
    """Get all link states for a lab.

    Returns the desired and actual state for each link in the topology.
    Auto-creates missing LinkState records for labs with existing topologies.
    """
    lab = get_lab_or_404(lab_id, database, current_user)

    # Sync LinkState records from database topology
    service = _pkg().TopologyService(database)
    if service.has_nodes(lab.id):
        graph = service.export_to_graph(lab.id)
        _pkg()._upsert_link_states(database, lab.id, graph)
        database.commit()

    states = (
        database.query(models.LinkState)
        .filter(models.LinkState.lab_id == lab_id)
        .order_by(models.LinkState.link_name)
        .all()
    )

    return schemas.LinkStatesResponse(
        links=[schemas.LinkStateOut.model_validate(s) for s in states]
    )


@router.get("/labs/{lab_id}/links/{link_name}/state")
def get_link_state(
    lab_id: str,
    link_name: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.LinkStateOut:
    """Get the state for a specific link."""
    lab = get_lab_or_404(lab_id, database, current_user)
    _ensure_link_states_exist(database, lab.id)

    state = (
        database.query(models.LinkState)
        .filter(
            models.LinkState.lab_id == lab_id,
            models.LinkState.link_name == link_name,
        )
        .first()
    )
    if not state:
        raise_not_found(f"Link '{link_name}' not found")

    return schemas.LinkStateOut.model_validate(state)


@router.get("/labs/{lab_id}/links/{link_name}/detail")
def get_link_detail(
    lab_id: str,
    link_name: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.LinkPathDetail:
    """Get full logical path detail for a link including tunnel and interface mappings."""
    lab = get_lab_or_404(lab_id, database, current_user)
    _ensure_link_states_exist(database, lab.id)

    link_state = (
        database.query(models.LinkState)
        .filter(
            models.LinkState.lab_id == lab_id,
            models.LinkState.link_name == link_name,
        )
        .first()
    )
    if not link_state:
        raise_not_found(f"Link '{link_name}' not found")

    # Look up tunnel if cross-host
    tunnel_detail = None
    if link_state.is_cross_host:
        tunnel = (
            database.query(models.VxlanTunnel)
            .filter(models.VxlanTunnel.link_state_id == link_state.id)
            .first()
        )
        if tunnel:
            tunnel_detail = schemas.VxlanTunnelDetail(
                vni=tunnel.vni,
                vlan_tag=tunnel.vlan_tag,
                agent_a_ip=tunnel.agent_a_ip,
                agent_b_ip=tunnel.agent_b_ip,
                port_name=tunnel.port_name,
                status=tunnel.status,
                error_message=tunnel.error_message,
            )

    # Look up host names
    host_names: dict[str, str] = {}
    host_ids = [h for h in [link_state.source_host_id, link_state.target_host_id] if h]
    if host_ids:
        hosts = database.query(models.Host).filter(models.Host.id.in_(host_ids)).all()
        host_names = {h.id: h.name for h in hosts}

    # Look up interface mappings via Node table (link_state stores node names, InterfaceMapping uses node_id)
    def _get_interface_mapping(node_name: str, interface: str):
        node = (
            database.query(models.Node)
            .filter(
                models.Node.lab_id == lab_id,
                models.Node.container_name == node_name,
            )
            .first()
        )
        if not node:
            return None
        return (
            database.query(models.InterfaceMapping)
            .filter(
                models.InterfaceMapping.lab_id == lab_id,
                models.InterfaceMapping.node_id == node.id,
                models.InterfaceMapping.linux_interface == interface,
            )
            .first()
        )

    src_mapping = _get_interface_mapping(link_state.source_node, link_state.source_interface)
    tgt_mapping = _get_interface_mapping(link_state.target_node, link_state.target_interface)

    source = schemas.LinkEndpointDetail(
        node_name=link_state.source_node,
        interface=link_state.source_interface,
        vendor_interface=src_mapping.vendor_interface if src_mapping else None,
        ovs_port=src_mapping.ovs_port if src_mapping else None,
        ovs_bridge=src_mapping.ovs_bridge if src_mapping else None,
        vlan_tag=link_state.source_vlan_tag,
        host_id=link_state.source_host_id,
        host_name=host_names.get(link_state.source_host_id) if link_state.source_host_id else None,
        oper_state=link_state.source_oper_state,
        oper_reason=link_state.source_oper_reason,
        carrier_state=link_state.source_carrier_state,
        vxlan_attached=link_state.source_vxlan_attached if link_state.is_cross_host else None,
    )

    target = schemas.LinkEndpointDetail(
        node_name=link_state.target_node,
        interface=link_state.target_interface,
        vendor_interface=tgt_mapping.vendor_interface if tgt_mapping else None,
        ovs_port=tgt_mapping.ovs_port if tgt_mapping else None,
        ovs_bridge=tgt_mapping.ovs_bridge if tgt_mapping else None,
        vlan_tag=link_state.target_vlan_tag,
        host_id=link_state.target_host_id,
        host_name=host_names.get(link_state.target_host_id) if link_state.target_host_id else None,
        oper_state=link_state.target_oper_state,
        oper_reason=link_state.target_oper_reason,
        carrier_state=link_state.target_carrier_state,
        vxlan_attached=link_state.target_vxlan_attached if link_state.is_cross_host else None,
    )

    return schemas.LinkPathDetail(
        link_name=link_state.link_name,
        actual_state=link_state.actual_state,
        desired_state=link_state.desired_state,
        error_message=link_state.error_message,
        is_cross_host=link_state.is_cross_host,
        source=source,
        target=target,
        tunnel=tunnel_detail,
    )


@router.put("/labs/{lab_id}/links/{link_name}/state")
def set_link_state(
    lab_id: str,
    link_name: str,
    payload: schemas.LinkStateUpdate,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.LinkStateOut:
    """Set the desired state for a link (up or down).

    This updates the desired state in the database. The actual state
    will be reconciled by the reconciliation system or can be triggered
    by a manual sync operation.
    """
    lab = require_lab_editor(lab_id, database, current_user)
    _ensure_link_states_exist(database, lab.id)

    state = (
        database.query(models.LinkState)
        .filter(
            models.LinkState.lab_id == lab_id,
            models.LinkState.link_name == link_name,
        )
        .first()
    )
    if not state:
        raise_not_found(f"Link '{link_name}' not found")

    state.desired_state = payload.state
    ok, conflicts = _pkg().sync_link_endpoint_reservations(database, state)
    if not ok:
        database.rollback()
        _raise_link_endpoint_conflict(database, state, conflicts)
    _sync_link_oper_state(database, state)
    database.commit()
    database.refresh(state)

    return schemas.LinkStateOut.model_validate(state)


@router.put("/labs/{lab_id}/links/desired-state")
def set_all_links_desired_state(
    lab_id: str,
    payload: schemas.LinkStateUpdate,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.LinkStatesResponse:
    """Set the desired state for all links in a lab.

    Useful for "Enable All Links" or "Disable All Links" operations.
    """
    lab = require_lab_editor(lab_id, database, current_user)
    _ensure_link_states_exist(database, lab.id)

    states = (
        database.query(models.LinkState)
        .filter(models.LinkState.lab_id == lab_id)
        .all()
    )
    for state in states:
        state.desired_state = payload.state
        ok, conflicts = _pkg().sync_link_endpoint_reservations(database, state)
        if not ok:
            database.rollback()
            _raise_link_endpoint_conflict(database, state, conflicts)
        _sync_link_oper_state(database, state)
    database.commit()

    # Refresh and return all states
    states = (
        database.query(models.LinkState)
        .filter(models.LinkState.lab_id == lab_id)
        .order_by(models.LinkState.link_name)
        .all()
    )
    return schemas.LinkStatesResponse(
        links=[schemas.LinkStateOut.model_validate(s) for s in states]
    )


@router.post("/labs/{lab_id}/links/refresh")
def refresh_link_states(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.LinkStateRefreshResponse:
    """Refresh link states from the current topology.

    This updates LinkState records to match the current topology.
    New links are created, removed links are deleted.
    Uses database topology as source of truth.
    """
    lab = get_lab_or_404(lab_id, database, current_user)

    service = _pkg().TopologyService(database)
    if not service.has_nodes(lab.id):
        raise_not_found("Topology not found")
    graph = service.export_to_graph(lab.id)

    created, updated, _, _ = _pkg()._upsert_link_states(database, lab.id, graph)
    database.commit()

    return schemas.LinkStateRefreshResponse(
        message="Link states refreshed",
        links_created=created,
        links_updated=updated,
    )


# ============================================================================
# Hot-Connect Link Management Endpoints
# ============================================================================


@router.post("/labs/{lab_id}/hot-connect")
async def hot_connect_link(
    lab_id: str,
    request: schemas.HotConnectRequest,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.HotConnectResponse:
    """Hot-connect two interfaces in a running lab.

    This creates a Layer 2 link between two container interfaces without
    restarting any nodes. The link is established by assigning both interfaces
    the same VLAN tag on the OVS bridge.

    Requirements:
    - Lab must be deployed (running state)
    - Both nodes must be running
    - Interfaces must be pre-provisioned via OVS
    """
    lab = require_lab_editor(lab_id, database, current_user)

    # Verify lab is running
    if lab.state not in (LabState.RUNNING, LabState.STARTING):
        raise HTTPException(
            status_code=400,
            detail=f"Lab must be running for hot-connect (current state: {lab.state})"
        )

    # Look up device types for accurate interface normalization
    _src_db_node = (
        database.query(models.Node)
        .filter(models.Node.lab_id == lab_id, models.Node.container_name == request.source_node)
        .first()
    )
    _tgt_db_node = (
        database.query(models.Node)
        .filter(models.Node.lab_id == lab_id, models.Node.container_name == request.target_node)
        .first()
    )
    src_n, src_i, tgt_n, tgt_i = canonicalize_link_endpoints(
        request.source_node,
        request.source_interface,
        request.target_node,
        request.target_interface,
        source_device=_src_db_node.device if _src_db_node else None,
        target_device=_tgt_db_node.device if _tgt_db_node else None,
    )
    link_name = generate_link_name(src_n, src_i, tgt_n, tgt_i)
    _hot_node_by_name: dict[str, models.Node] = {}
    if _src_db_node:
        _hot_node_by_name[_src_db_node.container_name] = _src_db_node
    if _tgt_db_node:
        _hot_node_by_name[_tgt_db_node.container_name] = _tgt_db_node
    link_def = _get_or_create_link_definition(
        database,
        lab_id,
        link_name,
        src_n,
        src_i,
        tgt_n,
        tgt_i,
        node_by_name=_hot_node_by_name,
        strict=True,
    )
    if link_def is None:
        raise_not_found(f"Link definition '{link_name}' could not be resolved")
    existing_states = (
        database.query(models.LinkState)
        .filter(
            models.LinkState.lab_id == lab_id,
            models.LinkState.link_definition_id == link_def.id,
        )
        .all()
    )
    _device_map: dict[str, str | None] = {}
    if _src_db_node:
        _device_map[_src_db_node.container_name] = _src_db_node.device
    if _tgt_db_node:
        _device_map[_tgt_db_node.container_name] = _tgt_db_node.device
    link_state, duplicate_states = _find_matching_link_state(
        existing_states,
        link_def.id,
        _device_map,
    )
    for duplicate in duplicate_states:
        duplicate.desired_state = "deleted"  # not in LinkDesiredState enum - soft-delete marker
    if not link_state:
        legacy_states = (
            database.query(models.LinkState)
            .filter(
                models.LinkState.lab_id == lab_id,
                models.LinkState.link_definition_id.is_(None),
            )
            .all()
        )
        link_state, legacy_duplicates = _find_matching_link_state_by_endpoints(
            legacy_states,
            src_n,
            src_i,
            tgt_n,
            tgt_i,
            _device_map,
        )
        for duplicate in legacy_duplicates:
            duplicate.desired_state = "deleted"  # not in LinkDesiredState enum - soft-delete marker

    if not link_state:

        # Look up host_ids for the endpoints
        src_node = (
            database.query(models.Node)
            .filter(models.Node.lab_id == lab_id, models.Node.container_name == src_n)
            .first()
        )
        tgt_node = (
            database.query(models.Node)
            .filter(models.Node.lab_id == lab_id, models.Node.container_name == tgt_n)
            .first()
        )
        src_host_id = src_node.host_id if src_node else None
        tgt_host_id = tgt_node.host_id if tgt_node else None

        # Fall back to NodePlacement if host_id not set on node
        if not src_host_id:
            placement = (
                database.query(models.NodePlacement)
                .filter(models.NodePlacement.lab_id == lab_id, models.NodePlacement.node_name == src_n)
                .first()
            )
            if placement:
                src_host_id = placement.host_id
        if not tgt_host_id:
            placement = (
                database.query(models.NodePlacement)
                .filter(models.NodePlacement.lab_id == lab_id, models.NodePlacement.node_name == tgt_n)
                .first()
            )
            if placement:
                tgt_host_id = placement.host_id

        is_cross_host = (
            src_host_id is not None
            and tgt_host_id is not None
            and src_host_id != tgt_host_id
        )

        link_state = models.LinkState(
            lab_id=lab_id,
            link_definition_id=link_def.id,
            link_name=link_name,
            source_node=src_n,
            source_interface=src_i,
            target_node=tgt_n,
            target_interface=tgt_i,
            source_host_id=src_host_id,
            target_host_id=tgt_host_id,
            is_cross_host=is_cross_host,
            desired_state=LinkDesiredState.UP,
            actual_state=LinkActualState.UNKNOWN,
        )
        savepoint = database.begin_nested()
        try:
            database.add(link_state)
            database.flush()
            savepoint.commit()
        except IntegrityError:
            savepoint.rollback()
            existing = (
                database.query(models.LinkState)
                .filter(
                    models.LinkState.lab_id == lab_id,
                    models.LinkState.link_name == link_name,
                )
                .first()
            )
            if not existing:
                raise
            link_state = existing
        _sync_link_oper_state(database, link_state)
    else:
        # Ensure canonical storage and reactivate stale records.
        link_state.link_name = link_name
        link_state.link_definition_id = link_def.id
        link_state.source_node = src_n
        link_state.source_interface = src_i
        link_state.target_node = tgt_n
        link_state.target_interface = tgt_i
        if link_state.desired_state == "deleted":
            link_state.desired_state = LinkDesiredState.UP
        _sync_link_oper_state(database, link_state)

    host_to_agent = await _pkg()._build_host_to_agent_map(database, lab_id)
    if not host_to_agent:
        raise_unavailable("No healthy agent available")

    success = await _pkg().create_link_if_ready(database, lab_id, link_state, host_to_agent)
    database.commit()

    if success:
        return schemas.HotConnectResponse(
            success=True,
            link_id=link_state.link_name,
            vlan_tag=link_state.vlan_tag,
        )

    if link_state.actual_state == LinkActualState.PENDING:
        return schemas.HotConnectResponse(
            success=False,
            error="Link pending - waiting for nodes to be running",
        )

    return schemas.HotConnectResponse(
        success=False,
        error=link_state.error_message or "Link creation failed",
    )


@router.delete("/labs/{lab_id}/hot-disconnect/{link_id:path}")
async def hot_disconnect_link(
    lab_id: str,
    link_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.HotConnectResponse:
    """Hot-disconnect a link in a running lab.

    This breaks a Layer 2 link between two container interfaces without
    restarting any nodes. The link is broken by assigning each interface
    a separate VLAN tag.

    Args:
        lab_id: Lab identifier
        link_id: Link identifier (format: "node1:iface1-node2:iface2")
    """
    require_lab_editor(lab_id, database, current_user)

    link_states = (
        database.query(models.LinkState)
        .filter(models.LinkState.lab_id == lab_id)
        .all()
    )
    _del_device_map: dict[str, str | None] = {
        n.container_name: n.device
        for n in database.query(models.Node).filter(models.Node.lab_id == lab_id).all()
    }
    parsed = _parse_link_id_endpoints(link_id)
    canonical_link_name: str | None = None
    link_state = None
    if parsed:
        src_n, src_i, tgt_n, tgt_i = parsed
        canonical_link_name = generate_link_name(src_n, src_i, tgt_n, tgt_i)
        link_def = (
            database.query(models.Link)
            .filter(
                models.Link.lab_id == lab_id,
                models.Link.link_name == canonical_link_name,
            )
            .first()
        )
        if link_def:
            candidate_states = [
                ls for ls in link_states if ls.link_definition_id == link_def.id
            ]
            link_state, _ = _find_matching_link_state(
                candidate_states,
                link_def.id,
                _del_device_map,
            )

    if link_state is None:
        # Backward compatibility for historical ids in older clients/tests.
        candidate_link_names = {link_id}
        if canonical_link_name:
            candidate_link_names.add(canonical_link_name)
        link_state = (
            database.query(models.LinkState)
            .filter(
                models.LinkState.lab_id == lab_id,
                models.LinkState.link_name.in_(list(candidate_link_names)),
            )
            .order_by(models.LinkState.updated_at.desc())
            .first()
        )

    if not link_state:
        return schemas.HotConnectResponse(success=False, error=f"Link '{link_id}' not found")

    host_to_agent = await _pkg()._build_host_to_agent_map(database, lab_id)
    if not host_to_agent:
        raise_unavailable("No healthy agent available")

    link_info = {
        "link_state_id": link_state.id,
        "link_name": link_state.link_name,
        "source_node": link_state.source_node,
        "source_interface": link_state.source_interface,
        "target_node": link_state.target_node,
        "target_interface": link_state.target_interface,
        "is_cross_host": link_state.is_cross_host,
        "actual_state": link_state.actual_state,
        "source_host_id": link_state.source_host_id,
        "target_host_id": link_state.target_host_id,
        "vni": link_state.vni,
    }
    success = await _pkg().teardown_link(database, lab_id, link_info, host_to_agent)
    database.commit()

    if success:
        return schemas.HotConnectResponse(success=True, link_id=link_id)

    return schemas.HotConnectResponse(
        success=False,
        error="Failed to disconnect link",
    )


@router.get("/labs/{lab_id}/live-links")
async def list_live_links(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """List all active OVS links for a running lab.

    This queries the agent for the current state of all OVS-managed links,
    including their VLAN tags and connection state.
    """
    lab = get_lab_or_404(lab_id, database, current_user)

    # Get agent for this lab
    lab_provider = get_lab_provider(lab)
    agent = await _pkg().get_online_agent_for_lab(database, lab, required_provider=lab_provider)
    if not agent:
        return {"links": [], "error": "No healthy agent available"}

    # Forward to agent
    result = await _pkg().agent_client.list_links_on_agent(agent, lab.id)
    return result


@router.post("/labs/{lab_id}/external/connect")
async def connect_to_external_network(
    lab_id: str,
    request: schemas.ExternalConnectRequest,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.ExternalConnectResponse:
    """Connect a node interface to an external network.

    This establishes connectivity between a container interface and an
    external host interface (e.g., for internet access, management network,
    or physical lab equipment).

    Requirements:
    - Lab must be deployed (running state)
    - Node must be running
    - External interface must exist on the host
    """
    lab = require_lab_editor(lab_id, database, current_user)

    # Verify lab is running
    if lab.state not in (LabState.RUNNING, LabState.STARTING):
        raise HTTPException(
            status_code=400,
            detail=f"Lab must be running for external connect (current state: {lab.state})"
        )

    # Get agent for this lab
    lab_provider = get_lab_provider(lab)
    agent = await _pkg().get_online_agent_for_lab(database, lab, required_provider=lab_provider)
    if not agent:
        raise_unavailable("No healthy agent available")

    # Forward to agent
    result = await _pkg().agent_client.connect_external_on_agent(
        agent=agent,
        lab_id=lab.id,
        node_name=request.node_name,
        interface_name=request.interface_name,
        external_interface=request.external_interface,
        vlan_tag=request.vlan_tag,
    )

    return schemas.ExternalConnectResponse(
        success=result.get("success", False),
        vlan_tag=result.get("vlan_tag"),
        error=result.get("error"),
    )


@router.post("/labs/{lab_id}/links/reconcile")
async def reconcile_links(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.LinkReconciliationResponse:
    """Reconcile link states for a lab.

    Verifies all links marked as "up" have matching VLAN tags on both
    endpoints. Attempts to repair any mismatched links.
    """
    require_lab_editor(lab_id, database, current_user)

    result = await _pkg().reconcile_lab_links(database, lab_id)

    return schemas.LinkReconciliationResponse(
        checked=result["checked"],
        valid=result["valid"],
        repaired=result["repaired"],
        errors=result["errors"],
        skipped=result["skipped"],
    )
