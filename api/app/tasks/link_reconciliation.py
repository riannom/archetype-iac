"""Link reconciliation background task.

This module periodically verifies that link states match actual OVS
configuration and attempts repair if discrepancies are found.

Key operations:
1. Query all links marked as "up" or in "error" state needing recovery
2. Verify VLAN tags match on both endpoints
3. Attempt repair if mismatch detected (supports partial re-attachment)
4. Mark as error if repair fails

Repair/cleanup operations are in separate modules:
- link_repair: partial recovery, VLAN repair, full link repair
- link_cleanup: orphaned link/tunnel cleanup, duplicate detection
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any

from sqlalchemy.orm import Session, joinedload

from app import agent_client, models
from app.db import get_session
from app.services.link_operational_state import recompute_link_oper_state
from app.services.link_validator import verify_link_connected, is_vlan_mismatch
from app.services.link_validator import ensure_link_interface_mappings
from app.services.link_reservations import reconcile_link_endpoint_reservations
from app.services.link_reservations import get_link_endpoint_reservation_drift_counts
from app.metrics import (
    record_db_transaction_issue,
    set_link_endpoint_reservation_metrics,
)
from app.utils.link import links_needing_reconciliation_filter
from app.tasks.live_links import create_link_if_ready, teardown_link
from app.agent_client import (
    compute_vxlan_port_name,
    declare_overlay_state_on_agent,
    declare_port_state_on_agent,
)
from app.tasks.jobs import _release_db_transaction_for_io as _release_db_tx_for_io

# Re-export from extracted modules for backwards compatibility
from app.tasks.link_repair import (
    attempt_partial_recovery,
    attempt_vlan_repair,
    attempt_link_repair,
)
from app.tasks.link_cleanup import (
    _cleanup_deleted_links,
    cleanup_orphaned_link_states,
    cleanup_orphaned_tunnels,
    detect_duplicate_tunnels,
)

logger = logging.getLogger(__name__)

# Configuration
RECONCILIATION_INTERVAL_SECONDS = 60
RECONCILIATION_ENABLED = True
RESERVATION_RECONCILE_INTERVAL_CYCLES = 5


def _release_db_transaction_for_io(
    session: Session,
    *,
    context: str,
    table: str = "link_states",
    lab_id: str | None = None,
) -> None:
    """Close any open transaction before awaited agent I/O.

    Reconciliation helpers must not hold DB transactions across awaited RPCs.
    Read state first, release the transaction, perform agent I/O, then reopen a
    short write transaction for any DB updates.
    """
    _release_db_tx_for_io(
        session,
        context=context,
        table=table,
        lab_id=lab_id,
    )


def _sync_oper_state(session: Session, link_state: models.LinkState) -> None:
    recompute_link_oper_state(session, link_state)


def _resolve_node_by_endpoint_name(
    session: Session,
    lab_id: str,
    endpoint_name: str,
) -> models.Node | None:
    """Resolve endpoint node by container_name first, then display_name."""
    if not endpoint_name:
        return None

    node = (
        session.query(models.Node)
        .filter(
            models.Node.lab_id == lab_id,
            models.Node.container_name == endpoint_name,
        )
        .first()
    )
    if node:
        return node

    return (
        session.query(models.Node)
        .filter(
            models.Node.lab_id == lab_id,
            models.Node.display_name == endpoint_name,
        )
        .first()
    )


def _waiting_on_intentionally_absent_endpoint(
    session: Session,
    link: models.LinkState,
) -> bool:
    """Return True when link endpoints are intentionally absent or still starting.

    Links targeting nodes that the operator intentionally stopped should stay
    quietly pending/down instead of entering repeated repair/error churn.
    The same applies while nodes are still legitimately pending/starting.
    """
    endpoint_states = (
        session.query(models.NodeState)
        .filter(
            models.NodeState.lab_id == link.lab_id,
            models.NodeState.node_name.in_([link.source_node, link.target_node]),
        )
        .all()
    )
    for node_state in endpoint_states:
        if (
            node_state.desired_state == "stopped"
            and node_state.actual_state in {"undeployed", "stopped", "stopping"}
        ):
            return True
        if (
            node_state.desired_state == "running"
            and node_state.actual_state in {"pending", "starting"}
        ):
            return True
    return False


async def reconcile_link_states(session: Session) -> dict:
    """Reconcile link_states with actual OVS configuration.

    Processes links based on desired vs actual state:
    1. Links "down"/"pending" with desired "up" - create if both nodes running
    2. Links "up" with desired "down" - tear down
    3. Links "up" with desired "up" - verify VLAN tags match
    4. Cross-host links in "error" with desired "up" - attempt recovery

    Args:
        session: Database session

    Returns:
        Dict with reconciliation results
    """
    results = {
        "checked": 0,
        "valid": 0,
        "repaired": 0,
        "recovered": 0,
        "created": 0,
        "torn_down": 0,
        "errors": 0,
        "skipped": 0,
    }

    # Build host_to_agent map for all online agents
    agents = (
        session.query(models.Host)
        .filter(models.Host.status == "online")
        .all()
    )
    host_to_agent = {a.id: a for a in agents}

    # Remove deleted links first to prevent stale overlays
    deleted_removed = await _cleanup_deleted_links(session, host_to_agent)
    if deleted_removed > 0:
        logger.info(f"Cleaned up {deleted_removed} deleted LinkState record(s)")

    # Get links that need attention:
    # - Links marked as "up" (verification)
    # - Cross-host links in "error" with partial attachment (recovery)
    links_to_check = (
        session.query(models.LinkState)
        .filter(links_needing_reconciliation_filter())
        .all()
    )

    if not links_to_check:
        return results

    for link in links_to_check:
        results["checked"] += 1

        # Skip if required agents are offline
        if link.source_host_id and link.source_host_id not in host_to_agent:
            results["skipped"] += 1
            continue
        if link.target_host_id and link.target_host_id not in host_to_agent:
            results["skipped"] += 1
            continue

        try:
            # Enforce desired state: create links that should be up
            if link.actual_state in ("down", "pending") and link.desired_state == "up":
                _release_db_transaction_for_io(
                    session,
                    context=f"link creation enforcement for {link.link_name}",
                    table="link_states",
                    lab_id=link.lab_id,
                )
                created = await create_link_if_ready(
                    session, link.lab_id, link, host_to_agent,
                    skip_locked=True,
                )
                if created:
                    results["created"] += 1
                    logger.info(f"Link {link.link_name} created via enforcement")
                elif link.actual_state == "pending":
                    results["skipped"] += 1
                else:
                    results["errors"] += 1
                continue

            # Enforce desired state: tear down links that should be down
            if link.actual_state == "up" and link.desired_state == "down":
                link_info = {
                    "link_state_id": link.id,
                    "link_name": link.link_name,
                    "is_cross_host": link.is_cross_host,
                    "actual_state": link.actual_state,
                    "source_host_id": link.source_host_id,
                    "target_host_id": link.target_host_id,
                    "source_node": link.source_node,
                    "target_node": link.target_node,
                    "source_interface": link.source_interface,
                    "target_interface": link.target_interface,
                }
                _release_db_transaction_for_io(
                    session,
                    context=f"link teardown enforcement for {link.link_name}",
                    table="link_states",
                    lab_id=link.lab_id,
                )
                torn_down = await teardown_link(
                    session, link.lab_id, link_info, host_to_agent,
                )
                if torn_down:
                    results["torn_down"] += 1
                    logger.info(f"Link {link.link_name} torn down via enforcement")
                else:
                    results["errors"] += 1
                continue

            # Handle error links needing recovery
            if link.actual_state == "error" and link.is_cross_host:
                logger.info(
                    f"Attempting recovery for link {link.link_name} "
                    f"(source_attached={link.source_vxlan_attached}, "
                    f"target_attached={link.target_vxlan_attached})"
                )
                _release_db_transaction_for_io(
                    session,
                    context=f"cross-host partial recovery for {link.link_name}",
                    table="link_states",
                    lab_id=link.lab_id,
                )
                recovered = await attempt_partial_recovery(session, link, host_to_agent)
                if recovered:
                    results["recovered"] += 1
                    logger.info(f"Link {link.link_name} recovered successfully")
                else:
                    results["errors"] += 1
                    logger.error(f"Link {link.link_name} recovery failed")
                continue

            # For "up" links (desired="up"), verify connectivity
            _release_db_transaction_for_io(
                session,
                context=f"link verification for {link.link_name}",
                table="link_states",
                lab_id=link.lab_id,
            )
            is_valid, error = await verify_link_connected(session, link, host_to_agent)

            if is_valid:
                results["valid"] += 1
            else:
                logger.warning(f"Link {link.link_name} verification failed: {error}")

                if error and error.startswith("Overlay status unavailable"):
                    results["skipped"] += 1
                    continue

                # Try lightweight VLAN repair first if it's a VLAN mismatch
                if is_vlan_mismatch(error):
                    _release_db_transaction_for_io(
                        session,
                        context=f"vlan repair for {link.link_name}",
                        table="link_states",
                        lab_id=link.lab_id,
                    )
                    vlan_repaired = await attempt_vlan_repair(session, link, host_to_agent)
                    if vlan_repaired:
                        # Trust the repair — skip immediate re-verification.
                        # The overlay manager's in-memory local_vlan may be stale
                        # after set_port_vlan pushes DB tags directly to OVS,
                        # causing false VLAN_MISMATCH on re-verify. The next
                        # reconciliation cycle will verify the fix.
                        results["repaired"] += 1
                        logger.info(f"Link {link.link_name} VLAN repair succeeded")
                        continue

                # Fall through to full link repair
                _release_db_transaction_for_io(
                    session,
                    context=f"full link repair for {link.link_name}",
                    table="link_states",
                    lab_id=link.lab_id,
                )
                repaired = await attempt_link_repair(session, link, host_to_agent)
                if repaired:
                    results["repaired"] += 1
                    logger.info(f"Link {link.link_name} repaired successfully")
                else:
                    link.actual_state = "error"
                    link.error_message = f"Reconciliation failed: {error}"
                    _sync_oper_state(session, link)
                    results["errors"] += 1
                    logger.error(f"Link {link.link_name} repair failed")

        except Exception as e:
            session.rollback()
            logger.error(f"Error reconciling link {link.link_name}: {e}")
            results["errors"] += 1

    session.commit()
    return results


async def run_overlay_convergence(
    session: Session,
    host_to_agent: dict[str, models.Host],
    *,
    lab_id: str | None = None,
) -> dict[str, Any]:
    """Declare full desired overlay state to each online agent.

    Builds the desired tunnel set from VxlanTunnel + LinkState records,
    groups by agent, and sends declare-state to each. The agent converges
    to match: creates missing, updates drifted, removes orphans.

    Args:
        session: Database session
        host_to_agent: Map of agent_id to Host model
        lab_id: If provided, only converge tunnels for this lab

    Returns:
        Dict with per-agent results
    """
    # Read overlay MTU from DB (source of truth)
    from app.routers.infrastructure import get_or_create_settings
    infra = get_or_create_settings(session)
    overlay_mtu = infra.overlay_mtu or 0

    # Single joined query: active tunnels with desired_state="up"
    tunnel_q = (
        session.query(models.VxlanTunnel)
        .join(models.LinkState, models.VxlanTunnel.link_state_id == models.LinkState.id)
        .filter(
            models.VxlanTunnel.status == "active",
            models.LinkState.desired_state == "up",
        )
    )
    if lab_id is not None:
        tunnel_q = tunnel_q.filter(models.VxlanTunnel.lab_id == lab_id)
    tunnels = tunnel_q.options(joinedload(models.VxlanTunnel.link_state)).all()

    # Group by agent, building entries for both sides
    agent_tunnels: dict[str, list[dict]] = defaultdict(list)
    agent_declared_labs: dict[str, set[str]] = defaultdict(set)
    for tunnel in tunnels:
        ls = tunnel.link_state
        if not ls:
            continue

        tun_lab_id = str(tunnel.lab_id)
        port_name = tunnel.port_name or compute_vxlan_port_name(tun_lab_id, ls.link_name)

        # Side A (source)
        agent_tunnels[tunnel.agent_a_id].append({
            "link_id": ls.link_name,
            "lab_id": tun_lab_id,
            "vni": tunnel.vni,
            "local_ip": tunnel.agent_a_ip,
            "remote_ip": tunnel.agent_b_ip,
            "expected_vlan": ls.source_vlan_tag or 0,
            "port_name": port_name,
            "mtu": overlay_mtu,
        })
        agent_declared_labs[tunnel.agent_a_id].add(tun_lab_id)

        # Side B (target)
        agent_tunnels[tunnel.agent_b_id].append({
            "link_id": ls.link_name,
            "lab_id": tun_lab_id,
            "vni": tunnel.vni,
            "local_ip": tunnel.agent_b_ip,
            "remote_ip": tunnel.agent_a_ip,
            "expected_vlan": ls.target_vlan_tag or 0,
            "port_name": port_name,
            "mtu": overlay_mtu,
        })
        agent_declared_labs[tunnel.agent_b_id].add(tun_lab_id)

    # Also protect in-progress cross-host links (creating/connecting)
    ip_q = session.query(models.LinkState).filter(
        models.LinkState.is_cross_host.is_(True),
        models.LinkState.actual_state.in_(["creating", "connecting"]),
    )
    if lab_id is not None:
        ip_q = ip_q.filter(models.LinkState.lab_id == lab_id)
    in_progress_links = ip_q.all()
    for ls in in_progress_links:
        port_name = compute_vxlan_port_name(ls.lab_id, ls.link_name)
        ls_lab_id = str(ls.lab_id)
        # Add placeholder entries so declare-state won't orphan-clean them
        for host_id in [ls.source_host_id, ls.target_host_id]:
            if host_id:
                existing = agent_tunnels.get(host_id, [])
                if not any(t["port_name"] == port_name for t in existing):
                    agent_tunnels[host_id].append({
                        "link_id": ls.link_name,
                        "lab_id": ls_lab_id,
                        "vni": ls.vni or 0,
                        "local_ip": "",
                        "remote_ip": "",
                        "expected_vlan": 0,
                        "port_name": port_name,
                        "mtu": overlay_mtu,
                    })
                agent_declared_labs[host_id].add(ls_lab_id)

    # Include lab scope from placements so agents can safely converge empty
    # declarations when they no longer host active cross-host tunnels.
    online_agent_ids = list(host_to_agent.keys())
    if online_agent_ids:
        placement_q = session.query(
            models.NodePlacement.host_id, models.NodePlacement.lab_id,
        ).filter(models.NodePlacement.host_id.in_(online_agent_ids))
        if lab_id is not None:
            placement_q = placement_q.filter(models.NodePlacement.lab_id == lab_id)
        placement_scopes = placement_q.distinct().all()
        for p_host_id, p_lab_id in placement_scopes:
            if p_host_id and p_lab_id:
                agent_declared_labs[p_host_id].add(str(p_lab_id))

    # Call each online agent in parallel
    all_results: dict[str, Any] = {}

    async def _declare_on_agent(agent_id: str, declared_tunnels: list[dict]):
        agent = host_to_agent.get(agent_id)
        if not agent:
            return
        try:
            declared_labs = sorted(agent_declared_labs.get(agent_id, set()))
            result = await declare_overlay_state_on_agent(
                agent,
                declared_tunnels,
                declared_labs=declared_labs,
            )
            results_list = result.get("results", [])
            orphans = result.get("orphans_removed", [])

            # Update attachment flags based on results
            for r in results_list:
                if r.get("status") in ("converged", "created", "updated"):
                    link_id = r.get("link_id")
                    if not link_id:
                        continue
                    # Find matching LinkState
                    for tunnel in tunnels:
                        ls = tunnel.link_state
                        if not ls or ls.link_name != link_id:
                            continue
                        if tunnel.agent_a_id == agent_id:
                            ls.source_vxlan_attached = True
                        elif tunnel.agent_b_id == agent_id:
                            ls.target_vxlan_attached = True
                        break

            created = sum(1 for r in results_list if r.get("status") == "created")
            updated = sum(1 for r in results_list if r.get("status") == "updated")
            errors = sum(1 for r in results_list if r.get("status") == "error")

            if created or updated or orphans or errors:
                logger.info(
                    f"Overlay convergence on {agent.name}: "
                    f"created={created}, updated={updated}, "
                    f"orphans={len(orphans)}, errors={errors}"
                )

            all_results[agent_id] = {
                "created": created,
                "updated": updated,
                "orphans_removed": orphans,
                "errors": errors,
            }
        except Exception as e:
            logger.error(f"Overlay convergence failed on agent {agent_id}: {e}")
            all_results[agent_id] = {"error": str(e)}

    # Include all online agents, even those with empty declarations.
    agents_to_converge = set(host_to_agent.keys())
    tasks = [
        _declare_on_agent(aid, agent_tunnels.get(aid, []))
        for aid in agents_to_converge
    ]

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

    # Persist attachment-flag updates made from declare-state results.
    session.commit()

    return all_results


async def refresh_interface_mappings(
    session: Session,
    host_to_agent: dict[str, models.Host],
    *,
    lab_id: str | None = None,
) -> dict[str, int]:
    """Bulk refresh InterfaceMapping records from agent port state.

    Queries each online agent for port state per lab, then upserts
    InterfaceMapping records with fresh VLAN tags and last_verified_at.

    Args:
        session: Database session
        host_to_agent: Map of agent_id to Host model
        lab_id: If provided, only refresh mappings for this lab

    Returns:
        Dict with 'updated' and 'created' counts
    """
    from datetime import datetime, timezone

    result = {"updated": 0, "created": 0}

    # Find all desired-up links, not only healthy ones.
    # Broken links still need fresh InterfaceMapping data so VM OVS port
    # changes can be detected and repaired after restart/recovery.
    links_q = session.query(models.LinkState).filter(
        models.LinkState.desired_state == "up",
    )
    if lab_id is not None:
        links_q = links_q.filter(models.LinkState.lab_id == lab_id)
    active_links = links_q.all()

    if not active_links:
        return result

    # Group by (host_id, lab_id) to minimize agent calls.
    # For cross-host links, add entries for BOTH source and target hosts.
    host_lab_pairs: dict[tuple[str, str], list[models.LinkState]] = defaultdict(list)
    for ls in active_links:
        if ls.is_cross_host:
            if ls.source_host_id and ls.source_host_id in host_to_agent:
                host_lab_pairs[(ls.source_host_id, ls.lab_id)].append(ls)
            if ls.target_host_id and ls.target_host_id in host_to_agent:
                host_lab_pairs[(ls.target_host_id, ls.lab_id)].append(ls)
        else:
            host_id = ls.source_host_id or ls.target_host_id
            if host_id and host_id in host_to_agent:
                host_lab_pairs[(host_id, ls.lab_id)].append(ls)

    now = datetime.now(timezone.utc)

    for (host_id, lab_id), links in host_lab_pairs.items():
        agent = host_to_agent.get(host_id)
        if not agent:
            continue

        try:
            _release_db_transaction_for_io(
                session,
                context=f"interface mapping refresh port-state fetch for agent {host_id} lab {lab_id}",
                table="interface_mappings",
                lab_id=lab_id,
            )
            ports = await agent_client.get_lab_port_state(agent, lab_id)
            if not ports:
                continue

            # Build lookup: (node_name, interface) -> port_info
            port_lookup: dict[tuple[str, str], dict] = {}
            for p in ports:
                key = (p.get("node_name", ""), p.get("interface_name", ""))
                port_lookup[key] = p

            # Get node definitions for this lab
            nodes = (
                session.query(models.Node)
                .filter(models.Node.lab_id == lab_id)
                .all()
            )
            node_by_name: dict[str, models.Node] = {}
            for n in nodes:
                node_by_name[n.display_name] = n
                if n.container_name:
                    node_by_name[n.container_name] = n

            # Update InterfaceMapping records
            # Use port_lookup.values() (deduplicated by node+interface)
            # rather than raw ports list which may contain duplicates
            for p in port_lookup.values():
                node_name = p.get("node_name", "")
                iface = p.get("interface_name", "")
                ovs_port = p.get("ovs_port_name", "")
                vlan_tag = p.get("vlan_tag", 0)

                node = node_by_name.get(node_name)
                if not node:
                    continue

                existing = (
                    session.query(models.InterfaceMapping)
                    .filter(
                        models.InterfaceMapping.lab_id == lab_id,
                        models.InterfaceMapping.node_id == node.id,
                        models.InterfaceMapping.linux_interface == iface,
                    )
                    .first()
                )

                if existing:
                    existing.ovs_port = ovs_port
                    existing.vlan_tag = vlan_tag
                    existing.last_verified_at = now
                    result["updated"] += 1
                else:
                    from uuid import uuid4
                    mapping = models.InterfaceMapping(
                        id=str(uuid4()),
                        lab_id=lab_id,
                        node_id=node.id,
                        ovs_port=ovs_port,
                        ovs_bridge="arch-ovs",
                        vlan_tag=vlan_tag,
                        linux_interface=iface,
                        last_verified_at=now,
                    )
                    session.add(mapping)
                    result["created"] += 1

        except Exception as e:
            session.rollback()
            logger.error(f"InterfaceMapping refresh failed for agent {host_id}, lab {lab_id}: {e}")

    # Ensure newly added mappings are visible to subsequent queries in the same
    # SQLAlchemy session even when autoflush is disabled (as in tests).
    if result["created"] or result["updated"]:
        session.flush()

    return result


async def run_same_host_convergence(
    session: Session,
    host_to_agent: dict[str, models.Host],
) -> dict[str, Any]:
    """Declare same-host port state to each online agent.

    Builds port pairings from same-host LinkState records where
    desired_state="up". Uses InterfaceMapping to find OVS port names.

    Args:
        session: Database session
        host_to_agent: Map of agent_id to Host model

    Returns:
        Dict with per-agent results
    """
    # Query same-host links that should be up
    same_host_links = (
        session.query(models.LinkState)
        .filter(
            models.LinkState.is_cross_host.is_(False),
            models.LinkState.desired_state == "up",
            models.LinkState.actual_state == "up",
        )
        .all()
    )

    if not same_host_links:
        return {}

    def _build_pairings() -> tuple[dict[str, list[dict]], int, set[str]]:
        pairings: dict[str, list[dict]] = defaultdict(list)
        missing_mappings = 0
        links_missing_mapping: set[str] = set()

        for ls in same_host_links:
            host_id = ls.source_host_id or ls.target_host_id
            if not host_id or host_id not in host_to_agent:
                continue

            # Get node definitions to find node IDs
            source_node = _resolve_node_by_endpoint_name(session, ls.lab_id, ls.source_node)
            target_node = _resolve_node_by_endpoint_name(session, ls.lab_id, ls.target_node)

            if not source_node or not target_node:
                missing_mappings += 1
                links_missing_mapping.add(ls.id)
                continue

            # Look up OVS port names from InterfaceMapping
            source_mapping = (
                session.query(models.InterfaceMapping)
                .filter(
                    models.InterfaceMapping.lab_id == ls.lab_id,
                    models.InterfaceMapping.node_id == source_node.id,
                    models.InterfaceMapping.linux_interface == ls.source_interface,
                )
                .first()
            )
            target_mapping = (
                session.query(models.InterfaceMapping)
                .filter(
                    models.InterfaceMapping.lab_id == ls.lab_id,
                    models.InterfaceMapping.node_id == target_node.id,
                    models.InterfaceMapping.linux_interface == ls.target_interface,
                )
                .first()
            )

            if not source_mapping or not target_mapping:
                missing_mappings += 1
                links_missing_mapping.add(ls.id)
                continue
            if not source_mapping.ovs_port or not target_mapping.ovs_port:
                missing_mappings += 1
                links_missing_mapping.add(ls.id)
                continue

            # Use the shared vlan_tag from LinkState (or source mapping's tag)
            shared_vlan = ls.vlan_tag or source_mapping.vlan_tag or 0
            if shared_vlan == 0:
                continue

            pairings[host_id].append({
                "link_name": ls.link_name,
                "lab_id": str(ls.lab_id),
                "port_a": source_mapping.ovs_port,
                "port_b": target_mapping.ovs_port,
                "vlan_tag": shared_vlan,
            })

        return pairings, missing_mappings, links_missing_mapping

    # First pass, then one refresh/retry pass if mappings are missing.
    agent_pairings, missing_mappings, missing_link_ids = _build_pairings()
    if missing_mappings:
        hydrated = 0
        for link_id in missing_link_ids:
            link = session.get(models.LinkState, link_id)
            if not link:
                continue
            hydrated += await ensure_link_interface_mappings(session, link, host_to_agent)
        if hydrated:
            session.flush()
            agent_pairings, missing_mappings, missing_link_ids = _build_pairings()

    if missing_mappings:
        logger.debug(
            "Same-host convergence missing InterfaceMapping data for %s link(s); "
            "refreshing mappings and retrying once",
            missing_mappings,
        )
        await refresh_interface_mappings(session, host_to_agent)
        agent_pairings, still_missing, still_missing_link_ids = _build_pairings()
        if still_missing:
            repaired = 0
            failed = 0
            for link_id in still_missing_link_ids:
                link = session.get(models.LinkState, link_id)
                if not link:
                    continue
                if await attempt_link_repair(session, link, host_to_agent):
                    repaired += 1
                else:
                    failed += 1
            if repaired or failed:
                session.commit()
                logger.info(
                    "Same-host fallback repair after mapping miss: repaired=%s failed=%s",
                    repaired,
                    failed,
                )
            if failed:
                logger.warning(
                    "Same-host convergence still missing InterfaceMapping data for %s link(s) "
                    "after refresh; repair failed for %s link(s)",
                    still_missing,
                    failed,
                )

    if not agent_pairings:
        return {}

    release_lab_id = same_host_links[0].lab_id if len({ls.lab_id for ls in same_host_links}) == 1 else None

    # Call each agent in parallel
    all_results: dict[str, Any] = {}

    async def _declare_on_agent(agent_id: str, pairings: list[dict]):
        agent = host_to_agent.get(agent_id)
        if not agent:
            return
        try:
            result = await declare_port_state_on_agent(agent, pairings)
            results_list = result.get("results", [])

            updated = sum(1 for r in results_list if r.get("status") == "updated")
            errors = sum(1 for r in results_list if r.get("status") == "error")

            if updated or errors:
                logger.info(
                    f"Same-host convergence on {agent.name}: "
                    f"updated={updated}, errors={errors}"
                )

            all_results[agent_id] = {
                "updated": updated,
                "errors": errors,
                "converged": sum(1 for r in results_list if r.get("status") == "converged"),
            }
        except Exception as e:
            logger.error(f"Same-host convergence failed on agent {agent_id}: {e}")
            all_results[agent_id] = {"error": str(e)}

    agents_to_converge = set(agent_pairings.keys()) & set(host_to_agent.keys())
    tasks = [
        _declare_on_agent(aid, agent_pairings.get(aid, []))
        for aid in agents_to_converge
    ]

    if tasks:
        _release_db_transaction_for_io(
            session,
            context="same-host convergence port-state declaration",
            table="link_states",
            lab_id=release_lab_id,
        )
        await asyncio.gather(*tasks, return_exceptions=True)

    return all_results


async def run_cross_host_port_convergence(
    session: Session,
    host_to_agent: dict[str, models.Host],
    *,
    lab_id: str | None = None,
) -> dict[str, Any]:
    """Push DB-stored VLAN tags to container ports on cross-host links.

    After a container restart, the Docker OVS plugin assigns a new random
    VLAN tag to the container port. Overlay convergence already pushes DB
    tags to VXLAN tunnel ports, but nobody pushes DB tags to container ports.
    This function closes that gap.

    Missing InterfaceMapping records are treated as a non-fatal condition.
    Cross-host links can include VM endpoints that are not returned by the
    agent /labs/{lab_id}/port-state endpoint, so we must not trigger full
    link repair solely due to mapping absence.

    Uses InterfaceMapping to look up OVS port names, then calls
    set_port_vlan_on_agent for any container port whose current VLAN
    doesn't match the DB-stored tag.

    Args:
        session: Database session
        host_to_agent: Map of agent_id to Host model
        lab_id: If provided, only converge ports for this lab

    Returns:
        Dict with 'updated' and 'errors' counts
    """
    result: dict[str, int] = {"updated": 0, "errors": 0}

    # Query cross-host links that should be up
    xh_q = session.query(models.LinkState).filter(
        models.LinkState.is_cross_host.is_(True),
        models.LinkState.desired_state == "up",
        models.LinkState.actual_state == "up",
    )
    if lab_id is not None:
        xh_q = xh_q.filter(models.LinkState.lab_id == lab_id)
    cross_host_links = xh_q.all()

    if not cross_host_links:
        return result

    def _collect_agent_corrections() -> tuple[dict[str, list[tuple[str, int]]], int, set[str]]:
        corrections: dict[str, list[tuple[str, int]]] = defaultdict(list)
        missing_mappings = 0
        links_missing_mapping: set[str] = set()

        for ls in cross_host_links:
            # Process both endpoints
            for side in ("source", "target"):
                if side == "source":
                    host_id = ls.source_host_id
                    node_name = ls.source_node
                    iface = ls.source_interface
                    db_vlan = ls.source_vlan_tag
                else:
                    host_id = ls.target_host_id
                    node_name = ls.target_node
                    iface = ls.target_interface
                    db_vlan = ls.target_vlan_tag

                if not host_id or host_id not in host_to_agent or not db_vlan:
                    continue

                # Find node ID for InterfaceMapping lookup
                node = _resolve_node_by_endpoint_name(session, ls.lab_id, node_name)
                if not node:
                    missing_mappings += 1
                    links_missing_mapping.add(ls.id)
                    continue

                mapping = (
                    session.query(models.InterfaceMapping)
                    .filter(
                        models.InterfaceMapping.lab_id == ls.lab_id,
                        models.InterfaceMapping.node_id == node.id,
                        models.InterfaceMapping.linux_interface == iface,
                    )
                    .first()
                )
                if not mapping or not mapping.ovs_port:
                    missing_mappings += 1
                    links_missing_mapping.add(ls.id)
                    continue

                # Compare current tag vs DB truth
                if mapping.vlan_tag != db_vlan:
                    corrections[host_id].append((mapping.ovs_port, db_vlan))

        return corrections, missing_mappings, links_missing_mapping

    agent_corrections, missing_mappings, _ = _collect_agent_corrections()
    if missing_mappings:
        logger.warning(
            "Cross-host port convergence skipped %s endpoint(s) due to missing InterfaceMapping data; "
            "refreshing mappings and retrying once",
            missing_mappings,
        )
        await refresh_interface_mappings(session, host_to_agent)
        agent_corrections, still_missing, _ = _collect_agent_corrections()
        if still_missing:
            logger.warning(
                "Cross-host port convergence still missing InterfaceMapping data for %s endpoint(s) "
                "after refresh; skipping endpoint corrections for those sides",
                still_missing,
            )

    if not agent_corrections:
        return result

    # Apply corrections in parallel across agents
    async def _apply_corrections(agent_id: str, corrections: list[tuple[str, int]]):
        agent = host_to_agent.get(agent_id)
        if not agent:
            return
        for port_name, vlan_tag in corrections:
            try:
                _release_db_transaction_for_io(
                    session,
                    context=f"cross-host port convergence vlan update for agent {agent_id}",
                    table="link_states",
                    lab_id=lab_id,
                )
                ok = await agent_client.set_port_vlan_on_agent(agent, port_name, vlan_tag)
                if ok:
                    result["updated"] += 1
                else:
                    result["errors"] += 1
            except Exception as e:
                logger.error(
                    f"Cross-host port convergence failed for {port_name} "
                    f"on agent {agent_id}: {e}"
                )
                result["errors"] += 1

    tasks = [
        _apply_corrections(aid, corrections)
        for aid, corrections in agent_corrections.items()
        if aid in host_to_agent
    ]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

    return result


async def link_reconciliation_monitor():
    """Background task that periodically reconciles link states.

    This runs as a long-lived task, checking link states at regular intervals.
    """
    logger.info("Link reconciliation monitor started")
    cycle_count = 0

    while True:
        try:
            await asyncio.sleep(RECONCILIATION_INTERVAL_SECONDS)

            if not RECONCILIATION_ENABLED:
                continue

            cycle_count += 1

            with get_session() as session:
                try:
                    # Build host_to_agent map once for the full cycle
                    agents = (
                        session.query(models.Host)
                        .filter(models.Host.status == "online")
                        .all()
                    )
                    host_to_agent = {a.id: a for a in agents}

                    # Phase 2: Detect and remove duplicate tunnels first
                    dups_removed = await detect_duplicate_tunnels(session, host_to_agent)
                    if dups_removed > 0:
                        logger.info(f"Removed {dups_removed} duplicate VxlanTunnel(s)")

                    results = await reconcile_link_states(session)

                    if results["checked"] > 0:
                        logger.info(
                            f"Link reconciliation: checked={results['checked']}, "
                            f"valid={results['valid']}, repaired={results['repaired']}, "
                            f"recovered={results['recovered']}, "
                            f"created={results['created']}, "
                            f"torn_down={results['torn_down']}, "
                            f"errors={results['errors']}, skipped={results['skipped']}"
                        )

                    # Clean up orphaned LinkState records (and their VXLAN ports)
                    ls_deleted = await cleanup_orphaned_link_states(session)
                    if ls_deleted > 0:
                        logger.info(f"Cleaned up {ls_deleted} orphaned LinkState records")

                    # Clean up orphaned VxlanTunnel records
                    orphans_deleted = await cleanup_orphaned_tunnels(session)
                    if orphans_deleted > 0:
                        logger.info(f"Cleaned up {orphans_deleted} orphaned VxlanTunnel records")

                    # Update reservation health gauges every cycle (lightweight query).
                    drift_counts = get_link_endpoint_reservation_drift_counts(session)
                    set_link_endpoint_reservation_metrics(
                        total=drift_counts["total"],
                        missing=drift_counts["missing"],
                        orphaned=drift_counts["orphaned"],
                        conflicts=drift_counts["conflicts"],
                    )

                    # Reservation self-heal is periodic (heavier write path).
                    if cycle_count % RESERVATION_RECONCILE_INTERVAL_CYCLES == 0:
                        reservation_result = reconcile_link_endpoint_reservations(session)
                        drift_counts_after = get_link_endpoint_reservation_drift_counts(session)
                        set_link_endpoint_reservation_metrics(
                            total=drift_counts_after["total"],
                            missing=drift_counts_after["missing"],
                            orphaned=drift_counts_after["orphaned"],
                            conflicts=drift_counts_after["conflicts"],
                        )
                        if (
                            reservation_result["orphans_removed"]
                            or reservation_result["released"]
                            or reservation_result["conflicts"]
                            or drift_counts_after["missing"]
                            or drift_counts_after["orphaned"]
                            or drift_counts_after["conflicts"]
                        ):
                            logger.info(
                                "Endpoint reservation reconcile: "
                                f"checked={reservation_result['checked']}, "
                                f"claimed={reservation_result['claimed']}, "
                                f"released={reservation_result['released']}, "
                                f"orphans_removed={reservation_result['orphans_removed']}, "
                                f"conflicts={reservation_result['conflicts']}, "
                                f"drift_missing={drift_counts_after['missing']}, "
                                f"drift_orphaned={drift_counts_after['orphaned']}, "
                                f"drift_conflicts={drift_counts_after['conflicts']}, "
                                f"total={drift_counts_after['total']}"
                            )

                    # Overlay (cross-host) convergence
                    convergence_results = await run_overlay_convergence(
                        session, host_to_agent
                    )
                    if convergence_results:
                        total_created = sum(
                            r.get("created", 0) for r in convergence_results.values()
                            if isinstance(r, dict)
                        )
                        total_orphans = sum(
                            len(r.get("orphans_removed", []))
                            for r in convergence_results.values()
                            if isinstance(r, dict)
                        )
                        if total_created or total_orphans:
                            logger.info(
                                f"Overlay convergence: created={total_created}, "
                                f"orphans_removed={total_orphans} "
                                f"across {len(convergence_results)} agent(s)"
                            )

                    # Refresh InterfaceMapping from agent port state
                    mapping_result = await refresh_interface_mappings(
                        session, host_to_agent
                    )
                    if mapping_result["updated"] or mapping_result["created"]:
                        logger.info(
                            f"InterfaceMapping refresh: "
                            f"updated={mapping_result['updated']}, "
                            f"created={mapping_result['created']}"
                        )

                    # Cross-host container port convergence
                    # (push DB tags to container ports after restart)
                    xhost_result = await run_cross_host_port_convergence(
                        session, host_to_agent
                    )
                    if xhost_result["updated"] or xhost_result["errors"]:
                        logger.info(
                            f"Cross-host port convergence: "
                            f"updated={xhost_result['updated']}, "
                            f"errors={xhost_result['errors']}"
                        )

                    # Same-host port convergence
                    port_results = await run_same_host_convergence(
                        session, host_to_agent
                    )
                    if port_results:
                        total_updated = sum(
                            r.get("updated", 0) for r in port_results.values()
                            if isinstance(r, dict)
                        )
                        if total_updated:
                            logger.info(
                                f"Same-host convergence: updated={total_updated} "
                                f"across {len(port_results)} agent(s)"
                            )

                except Exception as e:
                    logger.error(f"Link reconciliation error: {e}")

        except asyncio.CancelledError:
            logger.info("Link reconciliation monitor cancelled")
            break
        except Exception as e:
            logger.error(f"Link reconciliation monitor error: {e}")
            # Continue running despite errors
            await asyncio.sleep(10)


async def reconcile_lab_links(session: Session, lab_id: str) -> dict:
    """Reconcile links for a specific lab.

    This can be called on-demand (e.g., after deployment) to verify
    all links in a lab are properly connected and enforce desired state.

    Args:
        session: Database session
        lab_id: Lab identifier

    Returns:
        Dict with reconciliation results
    """
    results = {
        "checked": 0,
        "valid": 0,
        "repaired": 0,
        "recovered": 0,
        "created": 0,
        "torn_down": 0,
        "errors": 0,
        "skipped": 0,
    }

    # Build host_to_agent map
    agents = (
        session.query(models.Host)
        .filter(models.Host.status == "online")
        .all()
    )
    host_to_agent = {a.id: a for a in agents}

    # Remove deleted links first to prevent stale overlays
    deleted_removed = await _cleanup_deleted_links(session, host_to_agent, lab_id=lab_id)
    if deleted_removed > 0:
        logger.info(f"Cleaned up {deleted_removed} deleted LinkState record(s) for lab {lab_id}")

    # Get links that need attention for this lab
    links_to_check = (
        session.query(models.LinkState)
        .filter(
            models.LinkState.lab_id == lab_id,
            links_needing_reconciliation_filter(),
        )
        .all()
    )

    if not links_to_check:
        return results

    for link in links_to_check:
        results["checked"] += 1

        # Skip if required agents are offline
        if link.source_host_id and link.source_host_id not in host_to_agent:
            results["skipped"] += 1
            continue
        if link.target_host_id and link.target_host_id not in host_to_agent:
            results["skipped"] += 1
            continue

        try:
            if link.desired_state == "up" and _waiting_on_intentionally_absent_endpoint(session, link):
                link.actual_state = "pending"
                link.error_message = None
                _sync_oper_state(session, link)
                results["skipped"] += 1
                continue

            # Enforce desired state: create links that should be up
            if link.actual_state in ("down", "pending") and link.desired_state == "up":
                _release_db_transaction_for_io(
                    session,
                    context=f"link creation enforcement for {link.link_name}",
                    table="link_states",
                    lab_id=lab_id,
                )
                created = await create_link_if_ready(
                    session, lab_id, link, host_to_agent,
                    skip_locked=True,
                )
                if created:
                    results["created"] += 1
                    logger.info(f"Link {link.link_name} created via enforcement")
                elif link.actual_state == "pending":
                    results["skipped"] += 1
                else:
                    results["errors"] += 1
                continue

            # Enforce desired state: tear down links that should be down
            if link.actual_state == "up" and link.desired_state == "down":
                link_info = {
                    "link_state_id": link.id,
                    "link_name": link.link_name,
                    "is_cross_host": link.is_cross_host,
                    "actual_state": link.actual_state,
                    "source_host_id": link.source_host_id,
                    "target_host_id": link.target_host_id,
                    "source_node": link.source_node,
                    "target_node": link.target_node,
                    "source_interface": link.source_interface,
                    "target_interface": link.target_interface,
                }
                _release_db_transaction_for_io(
                    session,
                    context=f"link teardown enforcement for {link.link_name}",
                    table="link_states",
                    lab_id=lab_id,
                )
                torn_down = await teardown_link(
                    session, lab_id, link_info, host_to_agent,
                )
                if torn_down:
                    results["torn_down"] += 1
                    logger.info(f"Link {link.link_name} torn down via enforcement")
                else:
                    results["errors"] += 1
                continue

            # Handle error links needing recovery
            if link.actual_state == "error" and link.is_cross_host:
                _release_db_transaction_for_io(
                    session,
                    context=f"cross-host partial recovery for {link.link_name}",
                    table="link_states",
                    lab_id=lab_id,
                )
                recovered = await attempt_partial_recovery(session, link, host_to_agent)
                if recovered:
                    results["recovered"] += 1
                else:
                    results["errors"] += 1
                continue

            # For "up" links (desired="up"), verify connectivity
            _release_db_transaction_for_io(
                session,
                context=f"link verification for {link.link_name}",
                table="link_states",
                lab_id=lab_id,
            )
            is_valid, error = await verify_link_connected(session, link, host_to_agent)

            if is_valid:
                results["valid"] += 1
            else:
                logger.warning(f"Link {link.link_name} verification failed: {error}")

                # Try lightweight VLAN repair first if it's a VLAN mismatch
                if is_vlan_mismatch(error):
                    _release_db_transaction_for_io(
                        session,
                        context=f"vlan repair for {link.link_name}",
                        table="link_states",
                        lab_id=lab_id,
                    )
                    vlan_repaired = await attempt_vlan_repair(session, link, host_to_agent)
                    if vlan_repaired:
                        _release_db_transaction_for_io(
                            session,
                            context=f"post-vlan-repair verify for {link.link_name}",
                            table="link_states",
                            lab_id=lab_id,
                        )
                        is_valid2, _ = await verify_link_connected(session, link, host_to_agent)
                        if is_valid2:
                            results["repaired"] += 1
                            continue

                # Fall through to full link repair
                _release_db_transaction_for_io(
                    session,
                    context=f"full link repair for {link.link_name}",
                    table="link_states",
                    lab_id=lab_id,
                )
                repaired = await attempt_link_repair(session, link, host_to_agent)
                if repaired:
                    results["repaired"] += 1
                else:
                    link.actual_state = "error"
                    link.error_message = f"Reconciliation failed: {error}"
                    _sync_oper_state(session, link)
                    results["errors"] += 1

        except Exception as e:
            logger.error(
                "Error reconciling link %s: %s",
                link.link_name,
                e,
                extra={
                    "event": "db_transaction_issue" if "statement timeout" in str(e).lower() else "link_reconciliation_error",
                    "issue": "statement_timeout" if "statement timeout" in str(e).lower() else "runtime_error",
                    "phase": "link_reconciliation",
                    "table": "link_states",
                    "lab_id": link.lab_id,
                    "link_name": link.link_name,
                },
            )
            if "statement timeout" in str(e).lower():
                record_db_transaction_issue(
                    issue="statement_timeout",
                    phase="link_reconciliation",
                    table="link_states",
                )
            try:
                session.rollback()
            except Exception as rollback_error:
                record_db_transaction_issue(
                    issue="rollback_failed",
                    phase="link_reconciliation",
                    table="link_states",
                )
                logger.warning(
                    "Failed to rollback session after link %s reconciliation error: %s",
                    link.link_name,
                    rollback_error,
                    extra={
                        "event": "db_transaction_issue",
                        "issue": "rollback_failed",
                        "phase": "link_reconciliation",
                        "table": "link_states",
                        "lab_id": link.lab_id,
                        "link_name": link.link_name,
                    },
                )
            results["errors"] += 1

    session.commit()
    return results
