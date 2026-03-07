"""State reconciliation – DB maintenance and per-lab reconciliation.

Extracted from reconciliation.py.  Functions here handle link-state
initialization, placement backfill, orphan cleanup, and the main per-lab
reconciliation loop that compares agent reality to database records.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone

from app import agent_client, models
from app.config import settings
from app.metrics import (
    nlm_phase_duration,
    record_node_state_transition,
    record_runtime_identity_event,
)

from app.services.broadcaster import broadcast_node_state_change, broadcast_link_state_change
from app.services.link_reservations import release_link_endpoint_reservations
from app.utils.link import canonicalize_link_endpoints, link_state_endpoint_key
from app.services.topology import TopologyService
from app.utils.job import is_job_within_timeout
from app.state import (
    JobStatus,
    LabState,
    LinkActualState,
    LinkDesiredState,
    NodeActualState,
    NodeDesiredState,
)
from app.services.state_machine import LabStateMachine
from app.utils.time import utcnow

logger = logging.getLogger(__name__)


def _record_runtime_identity_observation(
    event: str,
    *,
    lab_id: str,
    agent_id: str | None = None,
    node_name: str | None = None,
    node_definition_id: str | None = None,
    expected_node_definition_id: str | None = None,
    expected_runtime_id: str | None = None,
    observed_runtime_id: str | None = None,
    level: int = logging.INFO,
) -> None:
    """Emit a bounded metric plus structured log for identity decisions."""
    record_runtime_identity_event(event)
    logger.log(
        level,
        "Runtime identity observation: %s",
        event,
        extra={
            "event": f"runtime_identity_{event}",
            "lab_id": lab_id,
            "agent_id": agent_id,
            "node_name": node_name,
            "node_definition_id": node_definition_id,
            "expected_node_definition_id": expected_node_definition_id,
            "expected_runtime_id": expected_runtime_id,
            "observed_runtime_id": observed_runtime_id,
        },
    )


def _apply_runtime_identity_decision(
    placement: models.NodePlacement,
    *,
    lab_id: str,
    agent_id: str,
    node_name: str,
    node_definition_id: str,
    observed_runtime_id: str | None,
    replacement_expected: bool = False,
) -> bool:
    """Apply the runtime identity decision table to a placement.

    Decision table for the hot reconciliation path:
    - metadata/topology match + no stored runtime_id + observed runtime_id:
      adopt the observed runtime_id.
    - metadata/topology match + stored runtime_id == observed runtime_id:
      keep the placement and clear prior drift flags.
    - metadata/topology match + stored runtime_id != observed runtime_id +
      placement.status == "starting":
      treat as an explicit replacement, update runtime_id, mark deployed.
    - metadata/topology match + stored runtime_id != observed runtime_id +
      placement.status != "starting":
      flag placement drift, keep the stored runtime_id, and require operator
      review instead of silently re-associating.
    - observed runtime_id missing:
      keep the current placement identity and surface the missing identity.

    Returns True when the placement is drifted and should keep its drift flag.
    """
    if not observed_runtime_id:
        if placement.runtime_id is None:
            _record_runtime_identity_observation(
                "placement_runtime_id_missing",
                lab_id=lab_id,
                agent_id=agent_id,
                node_name=node_name,
                node_definition_id=node_definition_id,
            )
        return placement.status == "drifted"

    if placement.runtime_id is None:
        placement.runtime_id = observed_runtime_id
        if placement.status == "drifted":
            placement.status = "deployed"
        return False

    if placement.runtime_id == observed_runtime_id:
        if placement.status == "drifted":
            placement.status = "deployed"
        return False

    if placement.status == "starting" or replacement_expected:
        _record_runtime_identity_observation(
            "runtime_replaced",
            lab_id=lab_id,
            agent_id=agent_id,
            node_name=node_name,
            node_definition_id=node_definition_id,
            expected_runtime_id=placement.runtime_id,
            observed_runtime_id=observed_runtime_id,
        )
        placement.runtime_id = observed_runtime_id
        placement.status = "deployed"
        return False

    _record_runtime_identity_observation(
        "runtime_id_mismatch",
        lab_id=lab_id,
        agent_id=agent_id,
        node_name=node_name,
        node_definition_id=node_definition_id,
        expected_runtime_id=placement.runtime_id,
        observed_runtime_id=observed_runtime_id,
        level=logging.WARNING,
    )
    placement.status = "drifted"
    return True


def _ensure_link_states_for_lab(session, lab_id: str) -> int:
    """Ensure LinkState records exist for all links in a lab's topology.

    This is called during reconciliation to create missing link state records
    for labs that may have been deployed before link state tracking was added.

    Uses database as source of truth. Performs canonical deduplication to
    prevent duplicate link states with different interface naming forms
    (e.g. eth1 vs Ethernet1).

    Returns the number of link states created.
    """
    service = TopologyService(session)
    db_links = service.get_links(lab_id)

    if not db_links:
        return 0

    # Build node_device_map for canonical normalization
    nodes = session.query(models.Node).filter(models.Node.lab_id == lab_id).all()
    node_device_map: dict[str, str | None] = {
        n.container_name: n.device for n in nodes
    }
    placements_by_node_definition_id = {
        placement.node_definition_id: placement
        for placement in (
            session.query(models.NodePlacement)
            .filter(
                models.NodePlacement.lab_id == lab_id,
                models.NodePlacement.node_definition_id.is_not(None),
            )
            .all()
        )
        if placement.node_definition_id
    }

    # Get existing link states
    existing = list(
        session.query(models.LinkState)
        .filter(models.LinkState.lab_id == lab_id)
        .all()
    )

    # --- Dedup pass: consolidate existing duplicates by canonical key ---
    key_groups: dict[tuple, list[models.LinkState]] = defaultdict(list)
    for ls in existing:
        key = link_state_endpoint_key(ls, node_device_map)
        key_groups[key].append(ls)

    dedup_deleted = 0
    for key, group in key_groups.items():
        if len(group) <= 1:
            continue
        # Keep the preferred row (canonical naming, most recent)
        preferred = sorted(
            group,
            key=lambda s: (
                s.desired_state != "deleted",
                s.updated_at or datetime.min.replace(tzinfo=timezone.utc),
            ),
        )[-1]
        for dup in group:
            if dup.id != preferred.id:
                logger.warning(
                    "Dedup: deleting duplicate LinkState %s (%s) in favour of %s (%s) for lab %s",
                    dup.id, dup.link_name, preferred.id, preferred.link_name, lab_id,
                )
                release_link_endpoint_reservations(session, dup.id)
                session.delete(dup)
                existing.remove(dup)
                dedup_deleted += 1
    if dedup_deleted:
        session.flush()

    # --- Build canonical key set from surviving rows ---
    existing_keys = {link_state_endpoint_key(ls, node_device_map) for ls in existing}

    created_count = 0
    for link in db_links:
        # Get node container names for the link state record
        source_node = session.get(models.Node, link.source_node_id)
        target_node = session.get(models.Node, link.target_node_id)
        if not source_node or not target_node:
            continue

        src_dev = node_device_map.get(source_node.container_name)
        tgt_dev = node_device_map.get(target_node.container_name)
        canonical_key = canonicalize_link_endpoints(
            source_node.container_name, link.source_interface,
            target_node.container_name, link.target_interface,
            src_dev, tgt_dev,
        )

        if canonical_key in existing_keys:
            continue  # Already exists (possibly under different naming)

        # Determine host placement from nodes or node_placements
        source_host_id = source_node.host_id
        target_host_id = target_node.host_id

        # Resolve host placement by deterministic Node definition FK.
        if not source_host_id:
            placement = placements_by_node_definition_id.get(source_node.id)
            if placement:
                source_host_id = placement.host_id

        if not target_host_id:
            placement = placements_by_node_definition_id.get(target_node.id)
            if placement:
                target_host_id = placement.host_id

        # Determine if cross-host
        is_cross_host = (
            source_host_id is not None
            and target_host_id is not None
            and source_host_id != target_host_id
        )

        # Use canonical (normalized) interface names for the new state
        src_n_canon, src_i_canon, tgt_n_canon, tgt_i_canon = canonical_key

        # If canonical ordering swapped source/target, swap host IDs too
        if src_n_canon != source_node.container_name:
            source_host_id, target_host_id = target_host_id, source_host_id

        new_state = models.LinkState(
            lab_id=lab_id,
            link_name=f"{src_n_canon}:{src_i_canon}-{tgt_n_canon}:{tgt_i_canon}",
            link_definition_id=link.id,
            source_node=src_n_canon,
            source_interface=src_i_canon,
            target_node=tgt_n_canon,
            target_interface=tgt_i_canon,
            source_host_id=source_host_id,
            target_host_id=target_host_id,
            is_cross_host=is_cross_host,
            desired_state="up",
            actual_state="unknown",
        )
        session.add(new_state)
        existing_keys.add(canonical_key)
        created_count += 1

    return created_count


def _backfill_placement_node_ids(session, lab_id: str) -> int:
    """Legacy compatibility helper kept for import stability.

    Deterministic identifier migrations now require `node_definition_id` to be
    populated at write-time, so reconciliation no longer performs name-based
    placement backfills.
    """
    missing_count = (
        session.query(models.NodePlacement.id)
        .filter(
            models.NodePlacement.lab_id == lab_id,
            models.NodePlacement.node_definition_id.is_(None),
        )
        .count()
    )
    if missing_count:
        logger.warning(
            "Lab %s has %s placement row(s) missing node_definition_id; "
            "manual cleanup/backfill is required",
            lab_id,
            missing_count,
        )
    return 0


def cleanup_orphaned_node_states(session, lab_id: str) -> int:
    """Delete orphaned NodeState rows for a lab.

    Orphaned NodeStates have node_definition_id IS NULL, meaning the Node
    definition they referenced was deleted (e.g., GUI ID change without
    name-match reuse). Only deletes rows in safe actual_states to avoid
    disrupting active containers.

    Returns:
        Number of orphaned NodeStates deleted
    """
    safe_states = (
        NodeActualState.UNDEPLOYED,
        NodeActualState.STOPPED,
        NodeActualState.ERROR,
    )
    orphaned = (
        session.query(models.NodeState)
        .filter(
            models.NodeState.lab_id == lab_id,
            models.NodeState.node_definition_id.is_(None),
            models.NodeState.actual_state.in_(safe_states),
        )
        .all()
    )

    if not orphaned:
        return 0

    count = 0
    for ns in orphaned:
        logger.info(
            f"Removing orphaned NodeState: lab={lab_id} node_id={ns.node_id} "
            f"node_name={ns.node_name} actual_state={ns.actual_state}"
        )
        session.delete(ns)
        count += 1

    if count:
        session.commit()
    return count


_lab_orphan_check_counter = 0
_LAB_ORPHAN_CHECK_INTERVAL = settings.lab_orphan_check_multiplier


async def _maybe_cleanup_labless_containers(session):
    """Periodically remove containers belonging to deleted labs.

    When a lab is deleted from the database, its containers on agents become
    invisible to per-lab reconciliation (which iterates DB labs). This function
    tells each agent the full list of valid lab IDs so it can remove any
    containers belonging to labs no longer in the database.

    Runs every _LAB_ORPHAN_CHECK_INTERVAL cycles to avoid excessive overhead.
    """
    global _lab_orphan_check_counter
    _lab_orphan_check_counter += 1
    if _lab_orphan_check_counter < _LAB_ORPHAN_CHECK_INTERVAL:
        return
    _lab_orphan_check_counter = 0

    online_agents: list[models.Host] = []

    try:
        from app.tasks.cleanup_base import get_valid_lab_ids
        valid_lab_ids = list(get_valid_lab_ids(session))
        all_agents = session.query(models.Host).all()

        async def _cleanup_agent(agent):
            try:
                result = await agent_client.cleanup_orphans_on_agent(agent, valid_lab_ids)
                removed = result.get("removed_containers", [])
                if removed:
                    logger.info(
                        f"Removed {len(removed)} lab-less container(s) on {agent.name}: {removed}"
                    )
            except Exception as e:
                logger.warning(f"Failed global orphan check on {agent.name}: {e}")

        online_agents = [a for a in all_agents if agent_client.is_agent_online(a)]
        await asyncio.gather(*[_cleanup_agent(a) for a in online_agents])
    except Exception as e:
        logger.warning(f"Failed global lab-less container cleanup: {e}")

    # VXLAN port reconciliation: make sure each agent only keeps declared ports.
    try:
        active_tunnels = (
            session.query(models.VxlanTunnel)
            .filter(models.VxlanTunnel.status == "active")
            .all()
        )

        link_ids = {t.link_state_id for t in active_tunnels if t.link_state_id}
        link_name_by_id = {}
        if link_ids:
            link_name_by_id = {
                ls.id: ls.link_name
                for ls in session.query(models.LinkState)
                .filter(models.LinkState.id.in_(link_ids))
                .all()
            }

        valid_ports_by_agent: dict[str, set[str]] = {}
        for tunnel in active_tunnels:
            link_name = link_name_by_id.get(tunnel.link_state_id)
            if not link_name:
                continue
            port_name = tunnel.port_name or agent_client.compute_vxlan_port_name(
                str(tunnel.lab_id), link_name
            )
            if tunnel.agent_a_id:
                valid_ports_by_agent.setdefault(tunnel.agent_a_id, set()).add(port_name)
            if tunnel.agent_b_id:
                valid_ports_by_agent.setdefault(tunnel.agent_b_id, set()).add(port_name)

        async def _reconcile_agent_ports(agent: models.Host):
            valid_ports = sorted(valid_ports_by_agent.get(agent.id, set()))
            try:
                await agent_client.reconcile_vxlan_ports_on_agent(agent, valid_ports)
            except Exception as e:
                logger.warning(
                    f"VXLAN port reconciliation failed on {agent.name}: {e}"
                )

        if online_agents:
            await asyncio.gather(*[_reconcile_agent_ports(a) for a in online_agents])
    except Exception as e:
        logger.warning(f"Failed VXLAN port reconciliation: {e}")

    # Overlay convergence: declare desired state so agents converge (create/update/delete)
    try:
        from app.tasks.link_reconciliation import run_overlay_convergence
        host_to_agent = {a.id: a for a in online_agents}
        await run_overlay_convergence(session, host_to_agent)
    except Exception as e:
        logger.warning(f"Failed overlay convergence: {e}")


async def _reconcile_single_lab(session, lab_id: str) -> int:
    """Reconcile a single lab's state with actual container status.

    Returns:
        Number of node state changes detected.
    """
    from app.tasks.reconciliation import reconciliation_lock

    lab = session.get(models.Lab, lab_id)
    if not lab:
        return 0

    # Acquire distributed lock to prevent concurrent reconciliation
    with reconciliation_lock(lab_id) as lock_acquired:
        if not lock_acquired:
            logger.debug(f"Lab {lab_id} reconciliation skipped - another process holds lock")
            return 0

        # Check if there's an active bulk deploy/destroy job for this lab.
        # Only "up" and "down" jobs should block reconciliation — per-node
        # sync/enforcement jobs should NOT prevent lab state updates, otherwise
        # lab.state gets stuck while enforcement is recovering nodes.
        active_job = (
            session.query(models.Job)
            .filter(
                models.Job.lab_id == lab_id,
                models.Job.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value]),
                models.Job.action.in_(["up", "down"]),
            )
            .first()
        )

        if active_job:
            # Check if job is still within its expected timeout window
            if is_job_within_timeout(
                active_job.action,
                active_job.status,
                active_job.started_at,
                active_job.created_at,
            ):
                logger.debug(f"Lab {lab_id} has active job {active_job.id}, skipping reconciliation")
                return 0
            else:
                # Job is stuck - log warning but proceed with reconciliation
                # The job_health_monitor will handle the stuck job separately
                logger.warning(
                    f"Lab {lab_id} has stuck job {active_job.id} "
                    f"(action={active_job.action}, status={active_job.status}), "
                    f"proceeding with state reconciliation"
                )

        # Call the actual reconciliation logic (extracted to allow locking)
        return await _do_reconcile_lab(session, lab, lab_id)
    return 0


async def _do_reconcile_lab(session, lab, lab_id: str) -> int:
    """Perform the actual reconciliation logic for a lab.

    This is called by _reconcile_single_lab after acquiring the lock.

    Returns:
        Number of node state changes detected.
    """
    from app.tasks.reconciliation import (
        _clear_agent_error,
        _set_agent_error,
        link_ops_lock,
        _last_endpoint_repair,
        ENDPOINT_REPAIR_COOLDOWN,
    )
    from app.utils.lab import get_lab_provider

    _reconcile_state_changes = 0

    # Ensure link states exist for this lab using database (source of truth)
    try:
        links_created = _ensure_link_states_for_lab(session, lab_id)
        session.commit()
        if links_created > 0:
            logger.info(f"Created {links_created} link state(s) for lab {lab_id}")
    except Exception as e:
        session.rollback()
        logger.warning(f"Failed to ensure link states for lab {lab_id}: {e}")

    # Normalize link interface names for existing labs
    try:
        topo_service = TopologyService(session)
        normalized = topo_service.normalize_links_for_lab(lab_id)
        if normalized > 0:
            logger.info(f"Normalized {normalized} link record(s) for lab {lab_id}")
    except Exception as e:
        session.rollback()
        logger.warning(f"Failed to normalize link interfaces for lab {lab_id}: {e}")

    # Clean up orphaned NodeState records (node_definition_id IS NULL)
    try:
        ns_deleted = cleanup_orphaned_node_states(session, lab_id)
        if ns_deleted > 0:
            logger.info(f"Cleaned up {ns_deleted} orphaned NodeState record(s) for lab {lab_id}")
    except Exception as e:
        session.rollback()
        logger.warning(f"Failed to clean up orphaned node states for lab {lab_id}: {e}")

    # Get ALL agents that have nodes for this lab (multi-host support)
    try:
        lab_provider = get_lab_provider(lab)

        # Look up node definitions for this lab.
        db_nodes = (
            session.query(models.Node)
            .filter(models.Node.lab_id == lab_id)
            .all()
        )

        # D.2: Build indexed dicts from bulk-loaded data to avoid per-container queries
        nodes_by_id = {n.id: n for n in db_nodes}
        nodes_by_container_name = {n.container_name: n for n in db_nodes}
        node_devices_by_id: dict[str, str | None] = {n.id: n.device for n in db_nodes}
        node_images_by_id: dict[str, str | None] = {n.id: n.image for n in db_nodes}
        node_runtime_name_by_id: dict[str, str] = {n.id: n.container_name for n in db_nodes}
        valid_node_ids = {n.id for n in db_nodes}

        # Find unique agents from placement records keyed by deterministic FK.
        placements = (
            session.query(models.NodePlacement)
            .filter(
                models.NodePlacement.lab_id == lab_id,
                models.NodePlacement.node_definition_id.is_not(None),
            )
            .all()
        )
        agent_ids = {p.host_id for p in placements if p.host_id}
        placements_by_node_definition_id = {
            p.node_definition_id: p
            for p in placements
            if p.node_definition_id
        }
        node_expected_agent_by_node_definition_id: dict[str, str] = {
            p.node_definition_id: p.host_id
            for p in placements
            if p.node_definition_id and p.host_id
        }
        node_expected_agent_by_name: dict[str, str] = {
            node_runtime_name_by_id[node_id]: host_id
            for node_id, host_id in node_expected_agent_by_node_definition_id.items()
            if node_id in node_runtime_name_by_id
        }

        # Also include the lab's default agent if set
        if lab.agent_id:
            agent_ids.add(lab.agent_id)

        # Include cross-host link endpoint agents so link validation can
        # evaluate both sides even when placements are temporarily missing.
        link_host_rows = (
            session.query(models.LinkState.source_host_id, models.LinkState.target_host_id)
            .filter(
                models.LinkState.lab_id == lab_id,
                models.LinkState.is_cross_host.is_(True),
            )
            .all()
        )
        for source_host_id, target_host_id in link_host_rows:
            if source_host_id:
                agent_ids.add(source_host_id)
            if target_host_id:
                agent_ids.add(target_host_id)

        # If no placements and no default, find any healthy agent
        if not agent_ids:
            fallback_agent = await agent_client.get_agent_for_lab(
                session, lab, required_provider=lab_provider
            )
            if fallback_agent:
                agent_ids.add(fallback_agent.id)

        if not agent_ids:
            logger.warning(f"No agent available to reconcile lab {lab_id}")
            return 0

        # Query actual container status from ALL agents (in parallel)
        # Track both status and which agent has each container
        container_status_map: dict[str, str] = {}
        container_agent_map: dict[str, str] = {}  # node_name -> agent_id
        container_runtime_id_map: dict[str, str] = {}  # node_name -> runtime_id
        container_node_definition_id_map: dict[str, str] = {}  # node_name -> node_definition_id
        agents_successfully_queried: set[str] = set()  # Track which agents responded
        host_to_agent: dict[str, models.Host] = {}  # For live link creation

        # Build list of online agents to query
        agents_to_query: list[tuple[str, models.Host]] = []
        for agent_id in agent_ids:
            agent = session.get(models.Host, agent_id)
            if not agent or not agent_client.is_agent_online(agent):
                logger.debug(f"Agent {agent_id} is offline, skipping in reconciliation")
                continue
            host_to_agent[agent_id] = agent
            agents_to_query.append((agent_id, agent))

        async def _query_agent(aid: str, ag: models.Host):
            """Query a single agent for lab status."""
            try:
                result = await agent_client.get_lab_status_from_agent(ag, lab_id)
                return aid, ag, result, None
            except Exception as e:
                return aid, ag, None, e

        # Query all agents in parallel
        if agents_to_query:
            query_results = await asyncio.gather(
                *[_query_agent(aid, ag) for aid, ag in agents_to_query],
                return_exceptions=True,
            )

            for item in query_results:
                if isinstance(item, Exception):
                    logger.warning(f"Agent query failed with exception: {item}")
                    continue

                aid, ag, result, error = item
                if error:
                    logger.warning(f"Failed to query agent {ag.name} for lab {lab_id}: {error}")
                    _set_agent_error(ag, f"Query failed: {error}")
                    continue

                nodes = result.get("nodes", [])
                agent_error = result.get("error")

                if not agent_error:
                    agents_successfully_queried.add(aid)
                    _clear_agent_error(ag)
                else:
                    logger.warning(
                        f"Agent {ag.name} returned error for lab {lab_id}: {agent_error}"
                    )
                    _set_agent_error(ag, agent_error)

                for n in nodes:
                    reported_node_name = n.get("name", "")
                    if not reported_node_name:
                        continue

                    node_definition_id = n.get("node_definition_id")
                    matched_node = (
                        nodes_by_id.get(node_definition_id)
                        if node_definition_id
                        else None
                    )
                    canonical_node_name = (
                        matched_node.container_name
                        if matched_node
                        else reported_node_name
                    )

                    if node_definition_id and not matched_node:
                        _record_runtime_identity_observation(
                            "unknown_node_definition_id",
                            lab_id=lab_id,
                            agent_id=aid,
                            node_name=reported_node_name,
                            node_definition_id=node_definition_id,
                            level=logging.WARNING,
                        )
                    elif matched_node and reported_node_name != canonical_node_name:
                        _record_runtime_identity_observation(
                            "metadata_name_mismatch",
                            lab_id=lab_id,
                            agent_id=aid,
                            node_name=reported_node_name,
                            node_definition_id=node_definition_id,
                            expected_node_definition_id=matched_node.id,
                            level=logging.WARNING,
                        )

                    container_status_map[canonical_node_name] = n.get("status", "unknown")
                    container_agent_map[canonical_node_name] = aid

                    runtime_id = n.get("runtime_id")
                    if runtime_id:
                        container_runtime_id_map[canonical_node_name] = runtime_id
                    else:
                        _record_runtime_identity_observation(
                            "missing_runtime_id",
                            lab_id=lab_id,
                            agent_id=aid,
                            node_name=canonical_node_name,
                            node_definition_id=node_definition_id,
                        )

                    if node_definition_id and matched_node:
                        container_node_definition_id_map[canonical_node_name] = node_definition_id
                    elif not node_definition_id:
                        _record_runtime_identity_observation(
                            "missing_node_definition_id",
                            lab_id=lab_id,
                            agent_id=aid,
                            node_name=canonical_node_name,
                        )

        logger.debug(f"Lab {lab_id} container status: {container_status_map}")

        # === ORPHAN CLEANUP: Remove containers/VMs not in topology ===
        # Build set of valid node names from database definitions
        valid_node_names = {n.container_name for n in db_nodes}

        # Only run cleanup if no active job (don't interfere with deploy/destroy)
        check_active_job = (
            session.query(models.Job)
            .filter(
                models.Job.lab_id == lab_id,
                models.Job.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value]),
            )
            .first()
        )
        if check_active_job:
            logger.debug(
                f"Skipping orphan cleanup for lab {lab_id} - active job {check_active_job.id} in progress"
            )
        else:
            # Find orphan containers: exist on agents but not in database
            orphan_node_names = set(container_status_map.keys()) - valid_node_names
            if orphan_node_names:
                logger.warning(
                    f"Lab {lab_id} has {len(orphan_node_names)} orphan container(s)/VM(s) "
                    f"not in topology: {list(orphan_node_names)}"
                )

                # Clean up orphan containers on each agent
                for agent_id in agents_successfully_queried:
                    agent = host_to_agent.get(agent_id)
                    if not agent:
                        continue

                    try:
                        cleanup_result = await agent_client.cleanup_lab_orphans(
                            agent, lab_id, list(valid_node_names)
                        )
                        removed = cleanup_result.get("removed_containers", [])
                        if removed:
                            logger.info(
                                f"Cleaned up {len(removed)} orphan(s) on agent {agent.name}: {removed}"
                            )
                            # Remove cleaned up containers from status map
                            for name in removed:
                                container_status_map.pop(name, None)
                                container_agent_map.pop(name, None)
                    except Exception as e:
                        logger.warning(f"Failed to cleanup orphans on agent {agent.name}: {e}")

            # Cleanup orphan NodePlacement records in database
            # This runs even if containers are already gone (e.g. manually removed)
            orphan_placements_to_delete = [
                placement
                for placement in (
                    session.query(models.NodePlacement)
                    .filter(models.NodePlacement.lab_id == lab_id)
                    .all()
                )
                if (
                    not placement.node_definition_id
                    or placement.node_definition_id not in valid_node_ids
                )
            ]
            for placement in orphan_placements_to_delete:
                logger.info(f"Removing orphan placement for node {placement.node_name} in lab {lab_id}")
                session.delete(placement)

        # Update NodeState records based on actual container status
        node_states = (
            session.query(models.NodeState)
            .filter(models.NodeState.lab_id == lab_id)
            .all()
        )
        starting_node_names = {
            node_runtime_name_by_id.get(ns.node_definition_id, ns.node_name)
            for ns in node_states
            if ns.actual_state == NodeActualState.STARTING.value
        }

        running_count = 0
        stopped_count = 0
        error_count = 0
        undeployed_count = 0

        # Check for active jobs that might be handling "stopping" nodes
        active_job = (
            session.query(models.Job)
            .filter(
                models.Job.lab_id == lab_id,
                models.Job.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value]),
            )
            .first()
        )

        # If there's an active job, refresh node_states from DB to pick up any
        # transitional state changes (stopping_started_at, starting_started_at)
        # that the job may have committed after we initially loaded them.
        # This prevents race conditions where reconciliation overwrites "stopping"
        # with "running" because it read stale state.
        if active_job:
            for ns in node_states:
                session.refresh(ns)

        for ns in node_states:
            runtime_node_name = node_runtime_name_by_id.get(
                ns.node_definition_id,
                ns.node_name,
            )
            if ns.node_definition_id and ns.node_definition_id not in nodes_by_id:
                logger.warning(
                    "NodeState %s in lab %s references missing Node definition %s",
                    ns.id,
                    lab_id,
                    ns.node_definition_id,
                )

            # Skip nodes where enforcement has permanently failed.
            # Enforcement owns their state — reconciliation must not overwrite
            # the error state, which would cause an infinite retry oscillation.
            if ns.enforcement_failed_at is not None:
                logger.debug(
                    f"Skipping reconciliation for {ns.node_name}: "
                    f"enforcement_failed_at set"
                )
                error_count += 1
                continue

            # Skip nodes with active image sync — the async sync callback will
            # re-trigger deployment when sync completes
            if ns.image_sync_status in ("syncing", "checking"):
                logger.debug(
                    f"Skipping reconciliation for {ns.node_name}: "
                    f"image sync in progress ({ns.image_sync_status})"
                )
                running_count += 1
                continue

            # Skip nodes with active transitional operations
            # The job will handle state updates - reconciliation should not interfere
            #
            # Check stopping_started_at/starting_started_at FIRST, regardless of actual_state.
            # This handles race conditions where the job hasn't updated actual_state yet
            # but has already started the operation.

            # Check for active stop operation
            if ns.stopping_started_at:
                stopping_duration = utcnow() - ns.stopping_started_at
                if stopping_duration.total_seconds() < settings.stale_stopping_threshold:
                    # Stop operation in progress - let the job handle it
                    stopped_count += 1
                    continue
                # Timeout exceeded - fall through to normal reconciliation
                logger.warning(
                    f"Node {ns.node_name} in lab {lab_id} stuck in stopping operation for "
                    f"{stopping_duration.total_seconds():.0f}s, recovering via reconciliation"
                )
                # Clear the stale timestamp
                ns.stopping_started_at = None

            # Check for active start operation
            if ns.starting_started_at:
                starting_duration = utcnow() - ns.starting_started_at
                if starting_duration.total_seconds() < settings.stale_node_starting_threshold:
                    # Start operation in progress - let the job handle it
                    running_count += 1
                    continue
                # Timeout exceeded - fall through to normal reconciliation
                logger.warning(
                    f"Node {ns.node_name} in lab {lab_id} stuck in starting operation for "
                    f"{starting_duration.total_seconds():.0f}s, recovering via reconciliation"
                )
                # Clear the stale timestamp
                ns.starting_started_at = None

            # Additional check: skip "stopping" or "starting" states even without timestamp
            # if there's an active job (the job will manage state)
            if ns.actual_state == NodeActualState.STOPPING.value:
                if active_job:
                    stopped_count += 1
                    continue
                # No timestamp and no job - something is wrong, recover
                logger.warning(
                    f"Node {ns.node_name} in lab {lab_id} in 'stopping' state without "
                    f"timestamp or active job, recovering via reconciliation"
                )

            if ns.actual_state == NodeActualState.STARTING.value:
                if active_job:
                    running_count += 1
                    continue
                # No timestamp and no job - something is wrong, recover
                logger.warning(
                    f"Node {ns.node_name} in lab {lab_id} in 'starting' state without "
                    f"timestamp or active job, recovering via reconciliation"
                )

            container_status = container_status_map.get(runtime_node_name)
            old_state = ns.actual_state
            old_is_ready = ns.is_ready

            if container_status:
                if container_status == "running":
                    ns.actual_state = NodeActualState.RUNNING.value
                    ns.stopping_started_at = None  # Clear if recovering from stuck stopping
                    ns.starting_started_at = None  # Clear if recovering from stuck starting
                    ns.error_message = None
                    running_count += 1

                    # Set boot_started_at if not already set (backfill for existing nodes)
                    if not ns.boot_started_at:
                        ns.boot_started_at = utcnow()

                    # Check boot readiness for nodes that are running but not yet ready
                    if not ns.is_ready:
                        # Poll agent for readiness status
                        try:
                            readiness_agent_id = (
                                container_agent_map.get(runtime_node_name)
                                or node_expected_agent_by_node_definition_id.get(ns.node_definition_id)
                                or node_expected_agent_by_name.get(runtime_node_name)
                                or lab.agent_id
                            )
                            readiness_agent = (
                                host_to_agent.get(readiness_agent_id)
                                if readiness_agent_id
                                else None
                            )
                            if not readiness_agent:
                                logger.debug(
                                    f"No reachable agent for readiness check of "
                                    f"{ns.node_name} in lab {lab_id}"
                                )
                                continue

                            # Get device kind and determine provider type
                            device_kind = node_devices_by_id.get(ns.node_definition_id)
                            node_image = node_images_by_id.get(ns.node_definition_id)
                            provider_type = None
                            if node_image:
                                # Determine provider from image extension
                                if node_image.endswith((".qcow2", ".img")):
                                    provider_type = "libvirt"
                                else:
                                    provider_type = "docker"

                            readiness = await agent_client.check_node_readiness(
                                readiness_agent,
                                lab_id,
                                runtime_node_name,
                                kind=device_kind,
                                provider_type=provider_type,
                            )
                            if readiness.get("is_ready", False):
                                ns.is_ready = True
                                # Record boot-wait duration metric
                                if ns.boot_started_at:
                                    boot_secs = (utcnow() - ns.boot_started_at).total_seconds()
                                    _boot_device = (device_kind or "linux").lower()
                                    nlm_phase_duration.labels(
                                        phase="boot_wait", device_type=_boot_device, status="success",
                                    ).observe(boot_secs)
                                logger.info(
                                    f"Node {ns.node_name} in lab {lab_id} is now ready"
                                )
                        except Exception as e:
                            logger.debug(f"Readiness check failed for {ns.node_name}: {e}")

                elif container_status in ("stopped", "exited"):
                    ns.actual_state = NodeActualState.STOPPED.value
                    ns.stopping_started_at = None  # Clear if recovering from stuck stopping
                    ns.starting_started_at = None  # Clear if recovering from stuck starting
                    ns.error_message = None
                    ns.is_ready = False
                    ns.boot_started_at = None
                    stopped_count += 1
                elif container_status in ("error", "dead"):
                    ns.actual_state = NodeActualState.ERROR.value
                    ns.stopping_started_at = None  # Clear if recovering from stuck stopping
                    ns.starting_started_at = None  # Clear if recovering from stuck starting
                    ns.error_message = f"Container status: {container_status}"
                    ns.is_ready = False
                    ns.boot_started_at = None
                    error_count += 1
                else:
                    # Unknown container status
                    stopped_count += 1
            else:
                # Container not found in status response
                # Only mark as undeployed if we successfully queried the agent that should have it
                # This prevents falsely marking nodes as undeployed when agent is temporarily unreachable
                expected_agent = (
                    node_expected_agent_by_node_definition_id.get(ns.node_definition_id)
                    or node_expected_agent_by_name.get(runtime_node_name)
                )
                agent_was_queried = (
                    expected_agent in agents_successfully_queried
                    if expected_agent
                    else len(agents_successfully_queried) > 0
                )

                if agent_was_queried:
                    # Agent responded but container/domain not found — it truly doesn't exist.
                    # Both Docker (ps -a) and libvirt (virsh list --all) include stopped
                    # instances in status, so absence means the node is gone.
                    if ns.actual_state != "undeployed":
                        ns.actual_state = NodeActualState.UNDEPLOYED.value
                        ns.error_message = None
                    ns.is_ready = False
                    ns.boot_started_at = None
                    undeployed_count += 1
                else:
                    # Agent didn't respond - preserve existing state to avoid false negatives
                    logger.debug(
                        f"Preserving state for {ns.node_name} - expected agent "
                        f"{expected_agent or 'unknown'} was not successfully queried"
                    )

            if ns.actual_state != old_state or (ns.is_ready != old_is_ready and ns.is_ready):
                _reconcile_state_changes += 1
                # Classify the transition for flap detection
                if ns.actual_state == NodeActualState.ERROR.value:
                    record_node_state_transition("error")
                elif ns.actual_state == NodeActualState.RUNNING.value:
                    record_node_state_transition("start")
                elif ns.actual_state in (NodeActualState.STOPPED.value, NodeActualState.UNDEPLOYED.value):
                    record_node_state_transition("stop")
                elif old_state == NodeActualState.ERROR.value:
                    record_node_state_transition("recover")
                logger.info(
                    "Node state transition",
                    extra={
                        "event": "node_state_transition",
                        "lab_id": lab_id,
                        "node_id": ns.node_id,
                        "node_name": ns.node_name,
                        "old_state": old_state,
                        "new_state": ns.actual_state,
                        "is_ready": ns.is_ready,
                        "trigger": "reconciliation",
                    },
                )
                # Broadcast state change to WebSocket clients
                # Look up host info for this node
                node_host_id = container_agent_map.get(runtime_node_name)
                node_host_name = host_to_agent.get(node_host_id).name if node_host_id and node_host_id in host_to_agent else None
                asyncio.create_task(
                    broadcast_node_state_change(
                        lab_id=lab_id,
                        node_id=ns.node_id,
                        node_name=ns.node_name,
                        desired_state=ns.desired_state,
                        actual_state=ns.actual_state,
                        is_ready=ns.is_ready,
                        error_message=ns.error_message,
                        host_id=node_host_id,
                        host_name=node_host_name,
                    )
                )

        # Ensure NodePlacement records exist for containers found on agents
        # This handles cases where deploy jobs failed after containers were created
        # IMPORTANT: Don't blindly trust where containers are found - check node_def.host_id
        misplaced_containers: dict[str, str] = {}  # node_name -> wrong_agent_id
        for node_name, agent_id in container_agent_map.items():
            # Prefer exact runtime-provided node definition identity when available.
            runtime_node_definition_id = container_node_definition_id_map.get(node_name)
            node_def = (
                nodes_by_id.get(runtime_node_definition_id)
                if runtime_node_definition_id
                else nodes_by_container_name.get(node_name)
            )
            if not runtime_node_definition_id:
                _record_runtime_identity_observation(
                    "name_fallback_used",
                    lab_id=lab_id,
                    agent_id=agent_id,
                    node_name=node_name,
                    expected_node_definition_id=node_def.id if node_def else None,
                )

            # Check if container is on the WRONG agent according to node definition
            if node_def and node_def.host_id and node_def.host_id != agent_id:
                misplaced_containers[node_name] = agent_id
                logger.warning(
                    f"MISPLACED CONTAINER: {node_name} in lab {lab_id} found on agent {agent_id} "
                    f"but should be on {node_def.host_id}. Queued for removal."
                )
                # Don't update placement for misplaced containers - this would perpetuate the bug
                continue

            existing_placement = (
                placements_by_node_definition_id.get(node_def.id)
                if node_def
                else None
            )
            if existing_placement:
                placement_is_drifted = False
                observed_runtime_id = container_runtime_id_map.get(node_name)
                # Update if container moved to a different agent (and move is valid per node_def)
                if existing_placement.host_id != agent_id:
                    logger.info(
                        f"Updating placement for {node_name} in lab {lab_id}: "
                        f"{existing_placement.host_id} -> {agent_id}"
                    )
                    existing_placement.host_id = agent_id
                existing_placement.node_name = node_def.container_name
                placement_is_drifted = _apply_runtime_identity_decision(
                    existing_placement,
                    lab_id=lab_id,
                    agent_id=agent_id,
                    node_name=node_name,
                    node_definition_id=node_def.id,
                    observed_runtime_id=observed_runtime_id,
                    replacement_expected=node_name in starting_node_names,
                )
                if (
                    not placement_is_drifted
                    and existing_placement.status == "starting"
                    and (observed_runtime_id or existing_placement.runtime_id)
                ):
                    existing_placement.status = "deployed"
                elif (
                    not placement_is_drifted
                    and existing_placement.status != "failed"
                    and observed_runtime_id
                ):
                    existing_placement.status = "deployed"
                node_expected_agent_by_node_definition_id[node_def.id] = agent_id
                node_expected_agent_by_name[node_def.container_name] = agent_id
            else:
                if not node_def:
                    logger.warning(
                        "Skipping placement creation for %s/%s during reconciliation: "
                        "node definition not found",
                        lab_id,
                        node_name,
                    )
                    continue
                # Create new placement record
                logger.info(
                    f"Creating placement for {node_name} in lab {lab_id} on agent {agent_id}"
                )
                new_placement = models.NodePlacement(
                    lab_id=lab_id,
                    node_name=node_def.container_name,
                    node_definition_id=node_def.id,
                    host_id=agent_id,
                    runtime_id=container_runtime_id_map.get(node_name),
                    status="deployed",
                )
                session.add(new_placement)
                placements_by_node_definition_id[node_def.id] = new_placement
                node_expected_agent_by_node_definition_id[node_def.id] = agent_id
                node_expected_agent_by_name[node_def.container_name] = agent_id
                if not new_placement.runtime_id:
                    _record_runtime_identity_observation(
                        "placement_runtime_id_missing",
                        lab_id=lab_id,
                        agent_id=agent_id,
                        node_name=node_name,
                        node_definition_id=node_def.id,
                    )

        # Remove misplaced containers from wrong agents
        # Only do this when no active job to avoid interfering with deployments
        if misplaced_containers and not check_active_job:
            for node_name, wrong_agent_id in misplaced_containers.items():
                agent = host_to_agent.get(wrong_agent_id)
                if agent:
                    try:
                        await agent_client.destroy_container_on_agent(agent, lab_id, node_name)
                        logger.info(f"Removed misplaced container {node_name} from agent {agent.name}")
                    except Exception as e:
                        logger.warning(f"Failed to remove misplaced {node_name} from {agent.name}: {e}")

        # Update lab state based on aggregated node states using state machine
        old_lab_state = lab.state
        new_lab_state = LabStateMachine.compute_lab_state(
            running_count=running_count,
            stopped_count=stopped_count,
            undeployed_count=undeployed_count,
            error_count=error_count,
        )
        lab.state = new_lab_state.value
        if new_lab_state == LabState.ERROR:
            lab.state_error = f"{error_count} node(s) in error state"
        else:
            lab.state_error = None

        lab.state_updated_at = utcnow()

        if lab.state != old_lab_state:
            logger.info(f"Reconciled lab {lab_id} state: {old_lab_state} -> {lab.state}")

        # Reconcile link states based on node states and L2 connectivity
        # Build a map of node name -> actual state for quick lookup
        node_actual_states: dict[str, str] = {}
        for ns in node_states:
            runtime_node_name = node_runtime_name_by_id.get(
                ns.node_definition_id,
                ns.node_name,
            )
            node_actual_states[runtime_node_name] = ns.actual_state

        # Update link states
        link_states = (
            session.query(models.LinkState)
            .filter(models.LinkState.lab_id == lab_id)
            .all()
        )

        # D.4: Batch-load active VXLAN tunnels to avoid per-link queries
        active_tunnels = (
            session.query(models.VxlanTunnel)
            .filter(
                models.VxlanTunnel.lab_id == lab_id,
                models.VxlanTunnel.status == "active",
            )
            .all()
        )
        tunnels_by_link_state_id = {t.link_state_id: t for t in active_tunnels}

        for ls in link_states:
            old_actual = ls.actual_state
            source_state = node_actual_states.get(ls.source_node, "unknown")
            target_state = node_actual_states.get(ls.target_node, "unknown")

            # Determine link actual state based on endpoint node states
            if source_state == NodeActualState.RUNNING.value and target_state == NodeActualState.RUNNING.value:
                # Both nodes running - check L2 connectivity
                # If carrier states are off, link is administratively down
                if ls.source_carrier_state == "off" or ls.target_carrier_state == "off":
                    ls.actual_state = LinkActualState.DOWN.value
                    ls.error_message = "Carrier disabled on one or more endpoints"
                elif ls.is_cross_host:
                    # For cross-host links, rely on active tunnel records as the
                    # source of truth during reconciliation.
                    tunnel = tunnels_by_link_state_id.get(ls.id)
                    if not tunnel:
                        # No active tunnel - link is broken
                        ls.actual_state = LinkActualState.ERROR.value
                        ls.error_message = "VXLAN tunnel not active"
                    else:
                        ls.actual_state = LinkActualState.UP.value
                        ls.error_message = None
                else:
                    # Same-host links must not be marked UP speculatively.
                    # Keep explicit DOWN state, preserve existing UP/ERROR truth,
                    # and otherwise move to PENDING so convergence/repair can verify.
                    if ls.desired_state == LinkDesiredState.DOWN.value:
                        ls.actual_state = LinkActualState.DOWN.value
                        ls.error_message = None
                    elif old_actual == LinkActualState.UP.value:
                        ls.actual_state = LinkActualState.UP.value
                        ls.error_message = None
                    elif old_actual == LinkActualState.ERROR.value:
                        ls.actual_state = LinkActualState.ERROR.value
                        if not ls.error_message:
                            ls.error_message = "Awaiting same-host convergence repair"
                    else:
                        ls.actual_state = LinkActualState.PENDING.value
                        ls.error_message = None
            elif source_state == NodeActualState.ERROR.value or target_state == NodeActualState.ERROR.value:
                # At least one node is in error state
                ls.actual_state = LinkActualState.ERROR.value
                ls.error_message = "One or more endpoint nodes in error state"
            elif source_state in (NodeActualState.STOPPED.value, NodeActualState.UNDEPLOYED.value) or target_state in (NodeActualState.STOPPED.value, NodeActualState.UNDEPLOYED.value):
                # At least one node is stopped/undeployed
                ls.actual_state = LinkActualState.DOWN.value
                ls.error_message = None
            else:
                # Unknown or transitional states
                ls.actual_state = LinkActualState.UNKNOWN.value
                ls.error_message = None

            if ls.actual_state != old_actual:
                logger.info(
                    "Link state transition",
                    extra={
                        "event": "link_state_transition",
                        "lab_id": lab_id,
                        "link_name": ls.link_name,
                        "old_state": old_actual,
                        "new_state": ls.actual_state,
                        "source_node": ls.source_node,
                        "target_node": ls.target_node,
                        "is_cross_host": ls.is_cross_host,
                        "trigger": "reconciliation",
                    },
                )
                # Broadcast link state change to WebSocket clients
                asyncio.create_task(
                    broadcast_link_state_change(
                        lab_id=lab_id,
                        link_name=ls.link_name,
                        desired_state=ls.desired_state,
                        actual_state=ls.actual_state,
                        source_node=ls.source_node,
                        target_node=ls.target_node,
                        error_message=ls.error_message,
                        source_oper_state=ls.source_oper_state,
                        target_oper_state=ls.target_oper_state,
                        source_oper_reason=ls.source_oper_reason,
                        target_oper_reason=ls.target_oper_reason,
                        oper_epoch=ls.oper_epoch,
                        is_cross_host=ls.is_cross_host,
                        vni=ls.vni,
                        source_host_id=ls.source_host_id,
                        target_host_id=ls.target_host_id,
                        source_vlan_tag=ls.source_vlan_tag,
                        target_vlan_tag=ls.target_vlan_tag,
                    )
                )

        # Auto-connect pending links when both nodes become running
        # This handles links that were added while nodes were not yet deployed
        # Also handles cross-host links where VXLAN tunnel is missing
        from app.tasks.live_links import create_link_if_ready

        # Collect links that need auto-connect
        links_to_connect = []
        for ls in link_states:
            # Check if link should be connected but isn't
            # Retry ALL error links, not just specific error types - the link setup
            # functions are idempotent and will reapply correct VLAN tags, recreate
            # VXLAN tunnels, etc. This handles recovery from agent restarts, VLAN
            # mismatches, transient failures, and other error conditions.
            should_auto_connect = (
                ls.desired_state == LinkDesiredState.UP.value
                and node_actual_states.get(ls.source_node) == NodeActualState.RUNNING.value
                and node_actual_states.get(ls.target_node) == NodeActualState.RUNNING.value
                and ls.source_carrier_state != "off"
                and ls.target_carrier_state != "off"
                and ls.actual_state in (LinkActualState.UNKNOWN.value, LinkActualState.PENDING.value, LinkActualState.DOWN.value, LinkActualState.ERROR.value)
            )
            if should_auto_connect:
                links_to_connect.append(ls)

        if links_to_connect:
            logger.info(
                f"Auto-connecting {len(links_to_connect)} link(s) in lab {lab_id}: "
                f"{[ls.link_name for ls in links_to_connect]}"
            )

        # Process auto-connect with lock to prevent conflicts with live_links
        if links_to_connect:
            with link_ops_lock(lab_id) as lock_acquired:
                if not lock_acquired:
                    logger.debug(
                        f"Could not acquire link ops lock for lab {lab_id}, "
                        f"skipping auto-connect (will retry next cycle)"
                    )
                else:
                    # Repair stale endpoints before attempting link creation.
                    # Rate-limited to avoid spamming repair on every cycle.
                    error_links = [
                        ls for ls in links_to_connect
                        if ls.actual_state == LinkActualState.ERROR.value
                    ]
                    if error_links:
                        now = utcnow()
                        last_repair = _last_endpoint_repair.get(lab_id)
                        if last_repair is None or (now - last_repair) >= ENDPOINT_REPAIR_COOLDOWN:
                            _last_endpoint_repair[lab_id] = now
                            # Collect unique agents that need repair
                            repair_agents: dict[str, list[str]] = {}
                            for ls in error_links:
                                for node_name in (ls.source_node, ls.target_node):
                                    host_id = node_expected_agent_by_name.get(node_name)
                                    if host_id and host_id in host_to_agent:
                                        repair_agents.setdefault(host_id, []).append(node_name)
                            for host_id, nodes in repair_agents.items():
                                agent = host_to_agent[host_id]
                                unique_nodes = list(set(nodes))
                                try:
                                    result = await agent_client.repair_endpoints_on_agent(
                                        agent, lab_id, unique_nodes,
                                    )
                                    repaired = result.get("total_endpoints_repaired", 0)
                                    if repaired > 0:
                                        logger.info(
                                            f"Repaired {repaired} endpoint(s) on {agent.name} "
                                            f"before auto-connect"
                                        )
                                except Exception as e:
                                    logger.warning(f"Endpoint repair on {agent.name} failed: {e}")

                    for ls in links_to_connect:
                        logger.info(f"Auto-connecting pending link {ls.link_name}")
                        try:
                            await create_link_if_ready(session, lab_id, ls, host_to_agent, skip_locked=True)
                        except Exception as e:
                            logger.error(f"Failed to auto-connect link {ls.link_name}: {e}")
                            ls.actual_state = LinkActualState.ERROR.value
                            ls.error_message = str(e)

        # Clean up links marked for deletion (desired_state="deleted")
        for ls in link_states:
            if ls.desired_state == "deleted":
                session.delete(ls)

        # === OBSERVATION ONLY: Log nodes where actual != desired ===
        # Enforcement is handled solely by state_enforcement_monitor() —
        # reconciliation is read-only and does not create jobs.
        out_of_sync_count = 0
        for ns in node_states:
            if ns.actual_state in (NodeActualState.STOPPING.value, NodeActualState.STARTING.value, NodeActualState.PENDING.value):
                continue
            if ns.stopping_started_at or ns.starting_started_at:
                continue
            needs_start = (
                ns.desired_state == NodeDesiredState.RUNNING.value
                and ns.actual_state in (NodeActualState.STOPPED.value, NodeActualState.UNDEPLOYED.value, NodeActualState.ERROR.value)
            )
            needs_stop = (
                ns.desired_state == NodeDesiredState.STOPPED.value
                and ns.actual_state == NodeActualState.RUNNING.value
            )
            if needs_start or needs_stop:
                out_of_sync_count += 1

        if out_of_sync_count:
            logger.debug(
                f"Reconciliation: {out_of_sync_count} node(s) out of sync in lab {lab_id} "
                f"(enforcement deferred to state_enforcement_monitor)"
            )

        session.commit()

    except Exception as e:
        logger.error(f"Failed to reconcile lab {lab_id}: {e}")
        # Rollback any uncommitted changes to prevent idle-in-transaction
        session.rollback()

    return _reconcile_state_changes
