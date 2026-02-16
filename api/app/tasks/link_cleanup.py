"""Link cleanup operations for orphaned records.

Extracted from link_reconciliation.py. Provides:
- _cleanup_deleted_links: Remove LinkState records marked as deleted
- cleanup_orphaned_link_states: Clean up orphaned LinkStates (null definition_id)
- cleanup_orphaned_tunnels: Clean up orphaned VxlanTunnel records
- detect_duplicate_tunnels: Find and resolve duplicate tunnel records
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app import agent_client, models
from app.config import settings
from app.services.interface_naming import normalize_interface

logger = logging.getLogger(__name__)


async def _detach_overlay_endpoint(
    agent: models.Host,
    lab_id: str,
    node: str,
    iface: str | None,
    link_id: str,
) -> tuple[bool, str | None]:
    """Detach one overlay endpoint and return (success, error)."""
    try:
        result = await agent_client.detach_overlay_interface_on_agent(
            agent,
            lab_id=lab_id,
            container_name=node,
            interface_name=normalize_interface(iface) if iface else "",
            link_id=link_id,
        )
        if isinstance(result, dict) and not result.get("success", False):
            return False, result.get("error") or "detach failed"
        return True, None
    except Exception as e:
        return False, str(e)


async def _cleanup_deleted_links(
    session: Session,
    host_to_agent: dict[str, models.Host],
    lab_id: str | None = None,
) -> int:
    """Remove LinkState records marked as deleted and tear down their tunnels.

    This prevents stale VXLAN overlays from persisting after interface renames
    (e.g., Ethernet -> eth) or topology updates.
    """
    from app.tasks.live_links import teardown_link

    query = session.query(models.LinkState).filter(
        models.LinkState.desired_state == "deleted"
    )
    if lab_id:
        query = query.filter(models.LinkState.lab_id == lab_id)
    deleted_links = query.all()

    if not deleted_links:
        return 0

    removed = 0
    changed = False
    for link_state in deleted_links:
        link_info = {
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

        required_agents: list[str] = []
        if link_state.is_cross_host:
            if link_state.source_host_id:
                required_agents.append(link_state.source_host_id)
            if link_state.target_host_id:
                required_agents.append(link_state.target_host_id)
        else:
            host_id = link_state.source_host_id or link_state.target_host_id
            if host_id:
                required_agents.append(host_id)

        offline_agents = [aid for aid in required_agents if aid not in host_to_agent]
        if offline_agents:
            msg = (
                "Teardown deferred: required agent(s) offline: "
                + ", ".join(offline_agents)
            )
            logger.info(
                f"Deferring deleted link cleanup for {link_state.link_name}: {msg}"
            )
            session.query(models.VxlanTunnel).filter(
                models.VxlanTunnel.link_state_id == link_state.id
            ).update(
                {
                    "status": "cleanup",
                    "error_message": msg,
                },
                synchronize_session=False,
            )
            changed = True
            continue

        teardown_ok = False
        try:
            teardown_ok = await teardown_link(
                session,
                link_state.lab_id,
                link_info,
                host_to_agent,
            )
        except Exception as e:
            logger.warning(
                f"Failed to teardown deleted link {link_state.link_name}: {e}"
            )
            session.query(models.VxlanTunnel).filter(
                models.VxlanTunnel.link_state_id == link_state.id
            ).update(
                {
                    "status": "cleanup",
                    "error_message": f"Teardown failed: {e}",
                },
                synchronize_session=False,
            )
            changed = True
            continue

        if not teardown_ok:
            logger.info(
                f"Deferring deleted link cleanup for {link_state.link_name}: "
                "teardown incomplete"
            )
            session.query(models.VxlanTunnel).filter(
                models.VxlanTunnel.link_state_id == link_state.id
            ).update(
                {
                    "status": "cleanup",
                    "error_message": "Teardown failed or incomplete; will retry",
                },
                synchronize_session=False,
            )
            changed = True
            continue

        # Always delete VXLAN tunnel records tied to this LinkState
        session.query(models.VxlanTunnel).filter(
            models.VxlanTunnel.link_state_id == link_state.id
        ).delete(synchronize_session=False)

        session.delete(link_state)
        removed += 1
        changed = True

    if changed:
        session.commit()
    return removed


async def cleanup_orphaned_link_states(session: Session) -> int:
    """Clean up orphaned LinkState records and their VXLAN tunnels.

    Orphaned LinkStates have link_definition_id IS NULL, meaning the Link
    definition they referenced was deleted (e.g., interface rename from
    eth to Ethernet). Their VXLAN ports persist on OVS until explicitly
    torn down.

    Only deletes non-"up" orphans — actively working links that just lost
    their definition are left alone to avoid disruption.

    Args:
        session: Database session

    Returns:
        Number of orphaned LinkStates deleted
    """
    orphaned = (
        session.query(models.LinkState)
        .filter(
            models.LinkState.link_definition_id.is_(None),
            models.LinkState.actual_state != "up",
        )
        .all()
    )

    if not orphaned:
        return 0

    # Build host_to_agent map for teardown calls
    agents = (
        session.query(models.Host)
        .filter(models.Host.status == "online")
        .all()
    )
    host_to_agent = {a.id: a for a in agents}

    count = 0
    changed = False
    for ls in orphaned:
        # Check for associated VxlanTunnel and tear down on agents
        tunnel = (
            session.query(models.VxlanTunnel)
            .filter(models.VxlanTunnel.link_state_id == ls.id)
            .first()
        )
        if tunnel:
            deferred_reasons: list[str] = []
            # Tear down VXLAN ports on both agents
            for agent_id, node, iface in [
                (tunnel.agent_a_id, ls.source_node, ls.source_interface),
                (tunnel.agent_b_id, ls.target_node, ls.target_interface),
            ]:
                agent = host_to_agent.get(agent_id)
                if agent:
                    ok, error = await _detach_overlay_endpoint(
                        agent,
                        ls.lab_id,
                        node,
                        iface,
                        ls.link_name,
                    )
                    if ok:
                        logger.info(
                            f"Torn down VXLAN port for orphaned link {ls.link_name} "
                            f"on agent {agent.name}"
                        )
                    else:
                        logger.warning(
                            f"Failed to tear down VXLAN port for orphaned link "
                            f"{ls.link_name} on agent {agent_id}: {error}"
                        )
                        deferred_reasons.append(
                            f"{agent_id}: {error or 'detach failed'}"
                        )
                else:
                    logger.debug(
                        f"Agent {agent_id} offline, skipping VXLAN teardown for "
                        f"orphaned link {ls.link_name}"
                    )
                    deferred_reasons.append(f"{agent_id}: offline")

            if deferred_reasons:
                tunnel.status = "cleanup"
                tunnel.error_message = (
                    "Cleanup deferred; waiting for agents: "
                    + "; ".join(deferred_reasons)
                )
                changed = True
                continue

        logger.info(
            f"Deleting orphaned LinkState: {ls.link_name} "
            f"(actual_state={ls.actual_state}, definition_id={ls.link_definition_id})"
        )
        session.delete(ls)
        count += 1
        changed = True

    if changed:
        session.commit()

    return count


async def cleanup_orphaned_tunnels(session: Session) -> int:
    """Clean up orphaned VxlanTunnel records.

    Orphaned tunnels are those where:
    - link_state_id is NULL (LinkState was deleted but tunnel remained)
    - status is "cleanup" for more than 5 minutes (teardown stalled)

    Args:
        session: Database session

    Returns:
        Number of orphaned tunnels deleted
    """
    cutoff_time = datetime.now(timezone.utc) - timedelta(seconds=settings.orphaned_tunnel_cleanup_timeout)

    orphaned = (
        session.query(models.VxlanTunnel)
        .filter(
            or_(
                models.VxlanTunnel.link_state_id.is_(None),
                and_(
                    models.VxlanTunnel.status == "cleanup",
                    models.VxlanTunnel.updated_at < cutoff_time,
                ),
            )
        )
        .all()
    )

    # Build host_to_agent map for teardown calls
    agents = (
        session.query(models.Host)
        .filter(models.Host.status == "online")
        .all()
    )
    host_to_agent = {a.id: a for a in agents}

    count = 0
    changed = False
    for tunnel in orphaned:
        link_state = None
        if tunnel.link_state_id is not None:
            link_state = (
                session.query(models.LinkState)
                .filter(models.LinkState.id == tunnel.link_state_id)
                .first()
            )

        if link_state:
            deferred_reasons: list[str] = []
            for agent_id, node, iface in [
                (tunnel.agent_a_id, link_state.source_node, link_state.source_interface),
                (tunnel.agent_b_id, link_state.target_node, link_state.target_interface),
            ]:
                agent = host_to_agent.get(agent_id)
                if agent:
                    ok, error = await _detach_overlay_endpoint(
                        agent,
                        link_state.lab_id,
                        node,
                        iface,
                        link_state.link_name,
                    )
                    if ok:
                        logger.info(
                            f"Torn down VXLAN port for orphaned tunnel on agent {agent.name} "
                            f"(link {link_state.link_name})"
                        )
                    else:
                        logger.warning(
                            f"Failed to detach VXLAN port for orphaned tunnel on agent "
                            f"{agent_id}: {error}"
                        )
                        deferred_reasons.append(
                            f"{agent_id}: {error or 'detach failed'}"
                        )
                else:
                    logger.debug(
                        f"Agent {agent_id} offline, skipping VXLAN teardown "
                        f"for orphaned tunnel (link_state_id={tunnel.link_state_id})"
                    )
                    deferred_reasons.append(f"{agent_id}: offline")

            if deferred_reasons:
                tunnel.status = "cleanup"
                tunnel.error_message = (
                    "Cleanup deferred; waiting for agents: "
                    + "; ".join(deferred_reasons)
                )
                changed = True
                continue

        logger.debug(
            f"Deleting orphaned tunnel: vni={tunnel.vni}, "
            f"link_state_id={tunnel.link_state_id}, status={tunnel.status}"
        )
        session.delete(tunnel)
        count += 1
        changed = True

    if changed:
        session.commit()

    return count


async def detect_duplicate_tunnels(
    session: Session,
    host_to_agent: dict[str, models.Host],
) -> int:
    """Detect and resolve duplicate VxlanTunnel records for the same segment.

    Duplicates arise when agent recovery rebuilds _link_tunnels with
    placeholder link_ids, allowing new tunnel records for the same
    (agent_pair, vni) to coexist with old ones.

    Groups by canonical key (min(agent_a, agent_b), max(agent_a, agent_b), vni)
    and keeps the one whose link_state is active, preferring newest by created_at.

    Returns number of duplicate tunnels removed.
    """
    from collections import defaultdict

    all_tunnels = (
        session.query(models.VxlanTunnel)
        .filter(models.VxlanTunnel.status != "cleanup")
        .all()
    )

    # Group by canonical key: (sorted agent pair, vni)
    groups: dict[tuple[str, str, int], list[models.VxlanTunnel]] = defaultdict(list)
    for t in all_tunnels:
        key = (min(t.agent_a_id, t.agent_b_id), max(t.agent_a_id, t.agent_b_id), t.vni)
        groups[key].append(t)

    removed = 0
    teardown_tasks = []

    for key, tunnels in groups.items():
        if len(tunnels) <= 1:
            continue

        # Identify keeper: active LinkState, newest created_at
        active_tunnels = []
        inactive_tunnels = []
        for t in tunnels:
            if t.link_state_id:
                ls = (
                    session.query(models.LinkState)
                    .filter(models.LinkState.id == t.link_state_id)
                    .first()
                )
                if ls and ls.actual_state != "deleted":
                    active_tunnels.append(t)
                    continue
            inactive_tunnels.append(t)

        # Pick keeper from active tunnels (newest), or from all if none active
        if active_tunnels:
            active_tunnels.sort(key=lambda t: t.created_at, reverse=True)
            keeper = active_tunnels[0]
            duplicates = active_tunnels[1:] + inactive_tunnels
        else:
            tunnels.sort(key=lambda t: t.created_at, reverse=True)
            keeper = tunnels[0]
            duplicates = tunnels[1:]

        for dup in duplicates:
            logger.warning(
                f"Removing duplicate tunnel {dup.id} for VNI {dup.vni} "
                f"between {dup.agent_a_id}/{dup.agent_b_id} "
                f"(keeping {keeper.id})"
            )

            # Attempt teardown on both agents
            for agent_id in (dup.agent_a_id, dup.agent_b_id):
                agent = host_to_agent.get(agent_id)
                if not agent:
                    continue
                link_state = None
                if dup.link_state_id:
                    link_state = (
                        session.query(models.LinkState)
                        .filter(models.LinkState.id == dup.link_state_id)
                        .first()
                    )

                async def _teardown(a=agent, ls=link_state, d=dup):
                    try:
                        await agent_client.detach_overlay_interface_on_agent(
                            a,
                            lab_id=d.lab_id,
                            container_name=ls.source_node if ls else "",
                            interface_name="",
                            link_id=ls.link_name if ls else f"dup-{d.id}",
                        )
                    except Exception as e:
                        logger.warning(f"Failed to tear down duplicate tunnel on {a.name}: {e}")

                teardown_tasks.append(_teardown())

            session.delete(dup)
            removed += 1

    if teardown_tasks:
        await asyncio.gather(*teardown_tasks, return_exceptions=True)

    if removed > 0:
        session.commit()
        logger.info(f"Removed {removed} duplicate VxlanTunnel record(s)")

    return removed
