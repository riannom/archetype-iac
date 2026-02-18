"""Node lifecycle management.

Orchestrates per-node lifecycle operations: deploy, start, stop, destroy.
Extracted from run_node_reconcile() in jobs.py for testability and maintainability.

Usage:
    manager = NodeLifecycleManager(session, lab, job, node_ids)
    result = await manager.execute()
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from app import agent_client, models
from app.agent_client import AgentUnavailableError
from app.metrics import (
    nlm_phase_duration,
    record_job_completed,
    record_job_failed,
    record_job_started,
)
from app.timing import AsyncTimedOperation
from app.config import settings
from app.services.broadcaster import broadcast_node_state_change, get_broadcaster
from app.services.state_machine import NodeStateMachine
from app.image_store import find_image_by_reference, get_image_provider, load_manifest
from app.services.topology import TopologyService, graph_to_deploy_topology, resolve_node_image
from app.storage import lab_workspace
from app.state import (
    HostStatus,
    JobStatus,
    NodeActualState,
    NodeDesiredState,
)
from app.utils.async_tasks import safe_create_task

logger = logging.getLogger(__name__)


# Seconds between cEOS container starts to avoid boot race conditions.
# cEOS CPU-bound init takes ~0.5-1s; 0.5s spacing prevents kernel socket
# contention while keeping total stagger time minimal.
CEOS_STAGGER_SECONDS = 0.5

# In-job retry constants for transient agent failures.
DEPLOY_RETRY_ATTEMPTS = 2
DEPLOY_RETRY_BACKOFF_SECONDS = 5


def _is_ceos_kind(kind: str) -> bool:
    """Check if a device kind is cEOS (needs staggered starts)."""
    if not kind:
        return False
    k = kind.lower()
    return "ceos" in k or k in ("arista_ceos", "ceos")


@dataclass
class LifecycleResult:
    """Result of a lifecycle operation."""

    success: bool
    error_count: int = 0
    log: list[str] = field(default_factory=list)

    @classmethod
    def noop(cls) -> LifecycleResult:
        return cls(success=True, log=["No action needed"])


def _get_container_name(lab_id: str, node_name: str) -> str:
    """Get the container name for a node."""
    safe_lab_id = re.sub(r"[^a-zA-Z0-9_-]", "", lab_id)[:20]
    safe_node = re.sub(r"[^a-zA-Z0-9_-]", "", node_name)
    return f"archetype-{safe_lab_id}-{safe_node}"


class NodeLifecycleManager:
    """Per-node lifecycle orchestrator.

    Handles the lifecycle of individual nodes: deploy, start, stop, destroy.
    Each operation is independent at the node level. Per-node lifecycle uses
    Docker SDK + OVS for container and networking operations.

    Phases (called in order by execute()):
        1. _load_and_validate     — Load state, batch-load maps, early exit if nothing to do
        2. _set_transitional_states — Set starting/stopping BEFORE agent lookup
        3. _resolve_agents        — Determine agent per node, spawn sub-jobs for other agents
        4. _check_resources       — Pre-deploy resource validation (BEFORE migration)
        5. _categorize_nodes      — Classify into deploy/start/stop groups
        6. _handle_migration      — Detect and clean up misplaced containers
        7. _check_images          — Pre-deploy image sync check
        8. _deploy_nodes          — Deploy undeployed nodes via full topology
        9. _start_nodes           — Start stopped nodes via full redeploy
       10. _stop_nodes            — Stop running containers
       11. _post_operation_cleanup — Cross-host VXLAN links
       12. _finalize              — Set job status, broadcast result
    """

    def __init__(
        self,
        session,
        lab: models.Lab,
        job: models.Job,
        node_ids: list[str],
        provider: str = "docker",
    ):
        self.session = session
        self.lab = lab
        self.job = job
        self.node_ids = node_ids
        self.provider = provider

        self.log_parts: list[str] = []
        self.topo_service = TopologyService(session)

        # Populated during _load_and_validate
        self.node_states: list[models.NodeState] = []
        self.old_agent_ids: set[str] = set()

        # Batch-loaded maps (Phase 2.3 — eliminates N+1 queries)
        self.db_nodes_map: dict[str, models.Node] = {}  # container_name -> Node
        self.db_nodes_by_gui_id: dict[str, models.Node] = {}  # gui_id -> Node
        self.placements_map: dict[str, models.NodePlacement] = {}  # node_name -> NodePlacement
        self.all_lab_states: dict[str, models.NodeState] = {}  # node_name -> NodeState

        # Populated during _resolve_agents
        self.agent: Optional[models.Host] = None
        self.target_agent_id: Optional[str] = None

        # Topology graph — loaded once in _filter_topology_for_agent, reused
        self.graph = None

    # Known device types for bounded Prometheus labels
    _KNOWN_DEVICE_TYPES = frozenset({
        "ceos", "srlinux", "iosv", "iosvl2", "csr1000v", "cat8000v",
        "cat9000v", "xrv9k", "asav", "nxosv", "linux", "frr",
    })

    def _dominant_device_type(self, node_states: list | None = None) -> str:
        """Return the most common device type among target nodes, bounded to known set."""
        states = node_states or self.node_states
        types: list[str] = []
        for ns in states:
            db_node = self.db_nodes_map.get(ns.node_name)
            device = (db_node.device if db_node else "linux") or "linux"
            types.append(device.lower())
        if not types:
            return "other"
        # Most common type
        from collections import Counter
        most_common = Counter(types).most_common(1)[0][0]
        return most_common if most_common in self._KNOWN_DEVICE_TYPES else "other"

    def _group_nodes_by_device_type(
        self, node_states: list[models.NodeState],
    ) -> list[tuple[str, list[models.NodeState]]]:
        groups: dict[str, list[models.NodeState]] = {}
        for ns in node_states:
            db_node = self.db_nodes_map.get(ns.node_name)
            raw_device = (db_node.device if db_node else "linux") or "linux"
            device_type = raw_device.lower()
            if device_type not in self._KNOWN_DEVICE_TYPES:
                device_type = "other"
            groups.setdefault(device_type, []).append(ns)
        return [(k, groups[k]) for k in sorted(groups.keys())]

    async def execute(self) -> LifecycleResult:
        """Main orchestrator — calls phases in order."""
        # Phase: Load and validate
        if not await self._load_and_validate():
            if self.job.status == JobStatus.COMPLETED.value:
                record_job_completed(self.job.action, duration_seconds=0.0)
            return LifecycleResult(
                success=True, log=self.log_parts or ["No action needed"]
            )

        # Phase: Set transitional states (BEFORE agent lookup)
        await self._set_transitional_states()

        # Phase: Resolve agents (may spawn sub-jobs for other agents)
        if not await self._resolve_agents():
            if self.job.status == JobStatus.FAILED.value:
                record_job_failed(self.job.action, failure_message=self.job.log_path)
            return LifecycleResult(success=False, log=self.log_parts)

        # Mark job running
        self.job.status = JobStatus.RUNNING.value
        self.job.agent_id = self.agent.id
        self.job.started_at = datetime.now(timezone.utc)
        self.session.commit()
        queue_wait = (
            (self.job.started_at - self.job.created_at).total_seconds()
            if self.job.started_at and self.job.created_at else None
        )
        record_job_started(self.job.action, queue_wait_seconds=queue_wait)

        await self._broadcast_job_progress(
            "running",
            progress_message=(
                f"Syncing {len(self.node_states)} node(s) on "
                f"{self.agent.name or self.agent.id}"
            ),
        )

        self.log_parts.append("=== Node Sync Job ===")
        self.log_parts.append(f"Lab: {self.lab.id}")
        self.log_parts.append(f"Agent: {self.agent.id} ({self.agent.name})")
        self.log_parts.append(f"Nodes: {', '.join(self.node_ids)}")
        self.log_parts.append("")

        # Phase: Resource check (BEFORE migration — Phase 2.2)
        if not await self._check_resources():
            if self.job.status == JobStatus.FAILED.value:
                duration = (
                    (self.job.completed_at - self.job.started_at).total_seconds()
                    if self.job.completed_at and self.job.started_at else None
                )
                record_job_failed(
                    self.job.action,
                    duration_seconds=duration,
                    failure_message=self.job.log_path,
                )
            return LifecycleResult(success=False, log=self.log_parts)

        # Categorize nodes by action
        nodes_need_deploy, nodes_need_start, nodes_need_stop = self._categorize_nodes()

        # Phase: Migration detection (AFTER resource check — Phase 2.2)
        nodes_to_start_or_deploy = nodes_need_deploy + nodes_need_start
        if nodes_to_start_or_deploy:
            await self._handle_migration(nodes_to_start_or_deploy)

        # Phase: Image sync check
        _timing_extras = {"lab_id": str(self.lab.id), "job_id": str(self.job.id)}
        if nodes_to_start_or_deploy:
            async with AsyncTimedOperation(
                histogram=nlm_phase_duration,
                labels={
                    "phase": "image_sync",
                    "device_type": self._dominant_device_type(nodes_to_start_or_deploy),
                    "status": "auto",
                },
                log_event="nlm_phase",
                log_extras={**_timing_extras, "phase": "image_sync"},
            ):
                result = await self._check_images(
                    nodes_need_deploy,
                    nodes_need_start,
                    nodes_to_start_or_deploy,
                    nodes_need_stop,
                )
            if result is None:
                # All nodes syncing/failed, nothing left to do this pass
                if self.job.status == JobStatus.COMPLETED.value:
                    duration = (
                        (self.job.completed_at - self.job.started_at).total_seconds()
                        if self.job.completed_at and self.job.started_at else 0.0
                    )
                    record_job_completed(self.job.action, duration_seconds=duration)
                elif self.job.status == JobStatus.FAILED.value:
                    duration = (
                        (self.job.completed_at - self.job.started_at).total_seconds()
                        if self.job.completed_at and self.job.started_at else None
                    )
                    record_job_failed(
                        self.job.action,
                        duration_seconds=duration,
                        failure_message=self.job.log_path,
                    )
                return LifecycleResult(success=True, log=self.log_parts)
            nodes_need_deploy, nodes_need_start = result

        # Phase: Deploy undeployed nodes
        if nodes_need_deploy:
            for device_type, grouped_nodes in self._group_nodes_by_device_type(nodes_need_deploy):
                async with AsyncTimedOperation(
                    histogram=nlm_phase_duration,
                    labels={
                        "phase": "container_deploy",
                        "device_type": device_type,
                        "status": "auto",
                    },
                    log_event="nlm_phase",
                    log_extras={
                        **_timing_extras,
                        "phase": "container_deploy",
                        "device_type": device_type,
                    },
                ):
                    await self._deploy_nodes(grouped_nodes)

        # Phase: Start stopped nodes (via redeploy)
        if nodes_need_start:
            for device_type, grouped_nodes in self._group_nodes_by_device_type(nodes_need_start):
                async with AsyncTimedOperation(
                    histogram=nlm_phase_duration,
                    labels={
                        "phase": "container_start",
                        "device_type": device_type,
                        "status": "auto",
                    },
                    log_event="nlm_phase",
                    log_extras={
                        **_timing_extras,
                        "phase": "container_start",
                        "device_type": device_type,
                    },
                ):
                    await self._start_nodes(grouped_nodes)

        # Phase: Stop running nodes
        if nodes_need_stop:
            for device_type, grouped_nodes in self._group_nodes_by_device_type(nodes_need_stop):
                async with AsyncTimedOperation(
                    histogram=nlm_phase_duration,
                    labels={
                        "phase": "container_stop",
                        "device_type": device_type,
                        "status": "auto",
                    },
                    log_event="nlm_phase",
                    log_extras={
                        **_timing_extras,
                        "phase": "container_stop",
                        "device_type": device_type,
                    },
                ):
                    await self._stop_nodes(grouped_nodes)

        # Phase: Active readiness polling for newly deployed/started nodes
        deployed_and_started = [
            ns.node_name for ns in self.node_states
            if ns.actual_state == NodeActualState.RUNNING.value and not ns.is_ready
        ]
        if deployed_and_started:
            await self._wait_for_readiness(deployed_and_started)

        # Phase: Post-operation cleanup (cross-host links)
        async with AsyncTimedOperation(
            histogram=nlm_phase_duration,
            labels={
                "phase": "post_cleanup",
                "device_type": self._dominant_device_type(),
                "status": "auto",
            },
            log_event="nlm_phase",
            log_extras={**_timing_extras, "phase": "post_cleanup"},
        ):
            await self._post_operation_cleanup()

        # Finalize job
        return await self._finalize()

    # ------------------------------------------------------------------ #
    #  Phase methods                                                       #
    # ------------------------------------------------------------------ #

    async def _load_and_validate(self) -> bool:
        """Load node states, fix placeholders, early-exit if all in desired state.

        Populates self.node_states, batch-loaded maps, and self.old_agent_ids.
        Returns True if there are nodes needing action, False otherwise.
        """
        # Load node states for requested node_ids
        self.node_states = (
            self.session.query(models.NodeState)
            .filter(
                models.NodeState.lab_id == self.lab.id,
                models.NodeState.node_id.in_(self.node_ids),
            )
            .all()
        )

        if not self.node_states:
            self.job.status = JobStatus.COMPLETED.value
            self.job.completed_at = datetime.now(timezone.utc)
            self.job.log_path = "No nodes to sync"
            self.session.commit()
            return False

        # Batch-load all Node definitions for this lab (Phase 2.3)
        all_db_nodes = self.topo_service.get_nodes(self.lab.id)
        self.db_nodes_map = {n.container_name: n for n in all_db_nodes}
        self.db_nodes_by_gui_id = {n.gui_id: n for n in all_db_nodes}

        # Batch-load all NodePlacements for this lab (Phase 2.3)
        self._refresh_placements()
        self.old_agent_ids = {p.host_id for p in self.placements_map.values()}

        # Batch-load all NodeStates for this lab (needed for topology filtering)
        all_states = (
            self.session.query(models.NodeState)
            .filter(models.NodeState.lab_id == self.lab.id)
            .all()
        )
        self.all_lab_states = {ns.node_name: ns for ns in all_states}

        # Batch-load latest ConfigSnapshots per node (Phase 1.2)
        from sqlalchemy import func, and_

        subq = (
            self.session.query(
                models.ConfigSnapshot.node_name,
                func.max(models.ConfigSnapshot.created_at).label("latest_at"),
            )
            .filter(models.ConfigSnapshot.lab_id == self.lab.id)
            .group_by(models.ConfigSnapshot.node_name)
            .subquery()
        )
        latest_snapshots = (
            self.session.query(models.ConfigSnapshot)
            .join(subq, and_(
                models.ConfigSnapshot.node_name == subq.c.node_name,
                models.ConfigSnapshot.created_at == subq.c.latest_at,
            ))
            .filter(models.ConfigSnapshot.lab_id == self.lab.id)
            .all()
        )
        self.latest_snapshots_map = {s.node_name: s for s in latest_snapshots}

        # Also batch-load explicit snapshots referenced by nodes
        explicit_snapshot_ids = {
            n.active_config_snapshot_id for n in all_db_nodes
            if n.active_config_snapshot_id
        }
        if explicit_snapshot_ids:
            explicit = (
                self.session.query(models.ConfigSnapshot)
                .filter(models.ConfigSnapshot.id.in_(explicit_snapshot_ids))
                .all()
            )
            self.explicit_snapshots_map = {s.id: s for s in explicit}
        else:
            self.explicit_snapshots_map = {}

        # Cache image manifest for SHA256 lookups (Phase 1.4)
        try:
            self._manifest = load_manifest()
        except Exception:
            self._manifest = None

        # Fix node_name placeholders from lazy initialization
        for ns in self.node_states:
            if ns.node_name == ns.node_id and ns.node_id in self.db_nodes_by_gui_id:
                db_node = self.db_nodes_by_gui_id[ns.node_id]
                if db_node.container_name != ns.node_name:
                    logger.info(
                        f"Fixing placeholder node_name: {ns.node_name} -> "
                        f"{db_node.container_name}"
                    )
                    ns.node_name = db_node.container_name
                    self.session.commit()

        # Early exit: Check if all nodes are already in their desired state
        nodes_needing_action = []
        for ns in self.node_states:
            if ns.desired_state == NodeDesiredState.RUNNING.value:
                if ns.actual_state not in (NodeActualState.RUNNING.value,):
                    nodes_needing_action.append(ns)
            elif ns.desired_state == NodeDesiredState.STOPPED.value:
                if ns.actual_state not in (
                    NodeActualState.STOPPED.value,
                    NodeActualState.UNDEPLOYED.value,
                    NodeActualState.EXITED.value,
                ):
                    nodes_needing_action.append(ns)

        if not nodes_needing_action:
            self.job.status = JobStatus.COMPLETED.value
            self.job.completed_at = datetime.now(timezone.utc)
            self.job.log_path = "All nodes already in desired state"
            self.session.commit()
            logger.info(
                f"Job {self.job.id} completed: all nodes already in desired state"
            )
            return False

        return True

    async def _set_transitional_states(self):
        """Set starting/stopping/pending states BEFORE agent lookup.

        This ensures the UI shows transitional states before any agent errors.
        """
        for ns in self.node_states:
            old_state = ns.actual_state
            try:
                current_actual = NodeActualState(ns.actual_state)
                desired = NodeDesiredState(ns.desired_state)
                next_state = NodeStateMachine.get_transition_for_desired(
                    current_actual, desired
                )
                if next_state:
                    ns.actual_state = next_state.value
                    ns.error_message = None
                    if next_state == NodeActualState.STOPPING:
                        ns.stopping_started_at = datetime.now(timezone.utc)
                    elif next_state == NodeActualState.STARTING:
                        ns.starting_started_at = datetime.now(timezone.utc)
            except ValueError:
                # Handle legacy state values
                if (
                    ns.desired_state == NodeDesiredState.STOPPED.value
                    and ns.actual_state == NodeActualState.RUNNING.value
                ):
                    ns.actual_state = NodeActualState.STOPPING.value
                    ns.stopping_started_at = datetime.now(timezone.utc)
                    ns.error_message = None
                elif ns.desired_state == NodeDesiredState.RUNNING.value and ns.actual_state in (
                    NodeActualState.STOPPED.value,
                    NodeActualState.ERROR.value,
                ):
                    ns.actual_state = NodeActualState.STARTING.value
                    ns.starting_started_at = datetime.now(timezone.utc)
                    ns.error_message = None
                elif ns.desired_state == NodeDesiredState.RUNNING.value and ns.actual_state in (
                    NodeActualState.UNDEPLOYED.value,
                    NodeActualState.PENDING.value,
                ):
                    ns.actual_state = NodeActualState.PENDING.value
                    ns.error_message = None

            if ns.actual_state != old_state:
                logger.info(
                    "Node state transition",
                    extra={
                        "event": "node_state_transition",
                        "lab_id": self.lab.id,
                        "node_id": ns.node_id,
                        "node_name": ns.node_name,
                        "old_state": old_state,
                        "new_state": ns.actual_state,
                        "trigger": "lifecycle_manager",
                        "job_id": self.job.id,
                    },
                )
                self._broadcast_state(ns)

        self.session.commit()

    async def _get_candidate_agents(self) -> list[models.Host]:
        """Return online agents that support the required provider."""
        from app.agent_client import get_agent_providers

        cutoff = datetime.now(timezone.utc) - timedelta(
            seconds=settings.agent_stale_timeout
        )
        agents = (
            self.session.query(models.Host)
            .filter(
                models.Host.status == "online",
                models.Host.last_heartbeat >= cutoff,
            )
            .all()
        )
        if self.provider:
            agents = [
                a for a in agents
                if self.provider in get_agent_providers(a)
            ]
        return agents

    async def _resolve_agents(self) -> bool:
        """Determine target agent per node. Spawn sub-jobs for other agents.

        Priority: Node.host_id (explicit) > NodePlacement (affinity) >
                  lab.agent_id > any healthy agent.
        Returns True if an agent was found, False otherwise.
        """
        # Build node -> agent mapping
        all_node_agents: dict[str, str] = {}  # node_name -> agent_id

        # Priority 1: Explicit placement (Node.host_id)
        explicit_placement_failures = []
        for ns in self.node_states:
            # Prefer exact container-name lookup, but fall back to gui_id-based
            # lookup so explicit host assignment is still enforced when
            # NodeState.node_name is stale.
            db_node = self.db_nodes_map.get(ns.node_name) or self.db_nodes_by_gui_id.get(ns.node_id)
            if db_node and db_node.host_id:
                host_agent = self.session.get(models.Host, db_node.host_id)
                if not host_agent:
                    explicit_placement_failures.append(
                        f"{ns.node_name}: assigned host "
                        f"{db_node.host_id} not found"
                    )
                elif not agent_client.is_agent_online(host_agent):
                    explicit_placement_failures.append(
                        f"{ns.node_name}: assigned host "
                        f"{host_agent.name} is offline"
                    )
                else:
                    # Verify agent is actually reachable (not just heartbeat-fresh)
                    try:
                        await agent_client.ping_agent(host_agent)
                    except AgentUnavailableError:
                        explicit_placement_failures.append(
                            f"{ns.node_name}: assigned host "
                            f"{host_agent.name} is unreachable"
                        )
                        continue
                    all_node_agents[ns.node_name] = db_node.host_id
                    logger.info(
                        "Placement decision",
                        extra={
                            "event": "placement_decision",
                            "lab_id": self.lab.id,
                            "node_name": ns.node_name,
                            "agent_id": db_node.host_id,
                            "agent_name": host_agent.name,
                            "decision_source": "explicit_host_id",
                            "job_id": self.job.id,
                        },
                    )

        # Fail fast if any explicit placements can't be honored
        if explicit_placement_failures:
            error_msg = (
                "Cannot deploy - explicit host assignments failed:\n"
                + "\n".join(explicit_placement_failures)
            )
            self.job.status = JobStatus.FAILED.value
            self.job.completed_at = datetime.now(timezone.utc)
            self.job.log_path = error_msg
            for ns in self.node_states:
                if ns.node_name in [
                    f.split(":")[0] for f in explicit_placement_failures
                ]:
                    ns.actual_state = NodeActualState.ERROR.value
                    ns.error_message = "Assigned host unavailable"
            self.session.commit()
            logger.error(f"Sync job {self.job.id} failed: {error_msg}")
            return False

        # Priority 2: Sticky placements (NodePlacement affinity)
        # Priority 3: Auto-placed nodes (resource-aware spread or legacy fallback)
        auto_placed_nodes = [
            ns for ns in self.node_states if ns.node_name not in all_node_agents
        ]
        if auto_placed_nodes:
            # Separate sticky (have placement) from truly new
            sticky_nodes = []
            new_nodes = []
            for ns in auto_placed_nodes:
                placement = self.placements_map.get(ns.node_name)
                if placement:
                    if placement.status == "failed":
                        # Skip failed placement, treat as new for re-scoring
                        new_nodes.append(ns)
                        logger.info(
                            f"Skipping failed placement for {ns.node_name} "
                            f"on agent {placement.host_id}"
                        )
                    else:
                        all_node_agents[ns.node_name] = placement.host_id
                        sticky_nodes.append(ns.node_name)
                else:
                    new_nodes.append(ns)

            if sticky_nodes:
                logger.debug(
                    f"Job {self.job.id}: {len(sticky_nodes)} node(s) use "
                    f"sticky placement: {sticky_nodes}"
                )

            # Distribute truly new nodes
            if new_nodes and settings.placement_scoring_enabled:
                from app.services.resource_capacity import (
                    distribute_nodes_by_score,
                    score_agent,
                )

                # Find all online agents with the required provider
                candidates = await self._get_candidate_agents()
                # Verify candidate agents are actually reachable
                reachable_candidates = []
                for cand in candidates:
                    try:
                        await agent_client.ping_agent(cand)
                        reachable_candidates.append(cand)
                    except AgentUnavailableError:
                        logger.warning(
                            f"Agent {cand.name} heartbeat fresh but unreachable, "
                            f"skipping for placement"
                        )
                candidates = reachable_candidates
                if candidates:
                    agent_scores = {}
                    for agent in candidates:
                        agent_scores[agent.id] = score_agent(agent)
                        logger.debug(
                            f"Job {self.job.id}: Agent {agent.id} ({agent.name}) "
                            f"score={agent_scores[agent.id].score:.3f} "
                            f"({agent_scores[agent.id].reason})"
                        )

                    spread = distribute_nodes_by_score(
                        [ns.node_name for ns in new_nodes], agent_scores
                    )
                    for node_name, agent_id in spread.items():
                        all_node_agents[node_name] = agent_id

                    # Log spread summary
                    counts: dict[str, int] = {}
                    for aid in spread.values():
                        counts[aid] = counts.get(aid, 0) + 1
                    logger.info(
                        f"Job {self.job.id}: Resource-aware spread for "
                        f"{len(new_nodes)} new node(s): {counts}"
                    )

            # Fallback for unassigned nodes (scoring disabled or no candidates)
            fallback_nodes = [
                ns for ns in new_nodes
                if ns.node_name not in all_node_agents
            ]
            if fallback_nodes:
                default_agent_id = None
                if self.lab.agent_id:
                    default_agent = self.session.get(
                        models.Host, self.lab.agent_id
                    )
                    if default_agent and agent_client.is_agent_online(
                        default_agent
                    ):
                        default_agent_id = self.lab.agent_id
                if not default_agent_id:
                    healthy_agent = await agent_client.get_healthy_agent(
                        self.session, required_provider=self.provider
                    )
                    if healthy_agent:
                        default_agent_id = healthy_agent.id

                if default_agent_id:
                    for ns in fallback_nodes:
                        all_node_agents[ns.node_name] = default_agent_id

        # Group nodes by target agent
        nodes_by_agent: dict[str, list] = {}
        nodes_without_agent = []
        for ns in self.node_states:
            agent_id = all_node_agents.get(ns.node_name)
            if agent_id:
                if agent_id not in nodes_by_agent:
                    nodes_by_agent[agent_id] = []
                nodes_by_agent[agent_id].append(ns)
            else:
                nodes_without_agent.append(ns)

        logger.debug(
            f"Job {self.job.id}: all_node_agents mapping: {all_node_agents}"
        )
        for agent_id, nodes in nodes_by_agent.items():
            logger.debug(
                f"Job {self.job.id}: Agent {agent_id} will handle nodes: "
                f"{[ns.node_name for ns in nodes]}"
            )

        if nodes_by_agent:
            agent_ids = list(nodes_by_agent.keys())
            self.target_agent_id = agent_ids[0]
            original_node_count = len(self.node_states)
            self.node_states = nodes_by_agent[self.target_agent_id]
            logger.info(
                f"Processing {len(self.node_states)} node(s) on agent "
                f"{self.target_agent_id}"
            )
            logger.debug(
                f"Job {self.job.id}: Filtered node_states from "
                f"{original_node_count} to {len(self.node_states)} nodes. "
                f"Remaining nodes: {[ns.node_name for ns in self.node_states]}"
            )

            # Spawn sub-jobs for other agents
            for other_agent_id in agent_ids[1:]:
                other_nodes = nodes_by_agent[other_agent_id]
                other_node_ids = [ns.node_id for ns in other_nodes]
                logger.info(
                    f"Spawning sync job for {len(other_node_ids)} node(s) on "
                    f"agent {other_agent_id}"
                )
                other_job = models.Job(
                    lab_id=self.lab.id,
                    user_id=self.job.user_id,
                    action=(
                        f"sync:agent:{other_agent_id}:"
                        f"{','.join(other_node_ids)}"
                    ),
                    status=JobStatus.QUEUED.value,
                    parent_job_id=self.job.id,
                )
                self.session.add(other_job)
                self.session.commit()
                self.session.refresh(other_job)
                # Local import to avoid circular dependency
                from app.tasks.jobs import run_node_reconcile

                safe_create_task(
                    run_node_reconcile(
                        other_job.id,
                        self.lab.id,
                        other_node_ids,
                        provider=self.provider,
                    ),
                    name=f"sync:agent:{other_job.id}",
                )

        # Handle nodes without agents
        if nodes_without_agent:
            if not self.node_states:
                # No other nodes with agents, try fallback logic
                self.node_states = nodes_without_agent
            else:
                # Mark unassigned nodes needing action as error
                nodes_needing_action = []
                for ns in nodes_without_agent:
                    needs_action = False
                    if ns.desired_state == NodeDesiredState.RUNNING.value:
                        if ns.actual_state not in (NodeActualState.RUNNING.value,):
                            needs_action = True
                    elif ns.desired_state == NodeDesiredState.STOPPED.value:
                        if ns.actual_state not in (
                            NodeActualState.STOPPED.value,
                            NodeActualState.UNDEPLOYED.value,
                            NodeActualState.EXITED.value,
                        ):
                            needs_action = True
                    if needs_action:
                        nodes_needing_action.append(ns)

                if nodes_needing_action:
                    logger.warning(
                        f"Cannot assign agent for {len(nodes_needing_action)} "
                        f"node(s), marking as error"
                    )
                    for ns in nodes_needing_action:
                        ns.actual_state = NodeActualState.ERROR.value
                        ns.error_message = (
                            "No agent available for explicit host placement"
                        )
                    self.session.commit()

        # Find the agent object
        if self.target_agent_id:
            self.agent = self.session.get(models.Host, self.target_agent_id)
            if self.agent and not agent_client.is_agent_online(self.agent):
                logger.warning(
                    f"Target agent {self.target_agent_id} is offline or "
                    f"unresponsive"
                )
                self.agent = None
        else:
            self.agent = None
            # Check existing placements for this set of nodes
            placement_agents = set()
            for ns in self.node_states:
                p = self.placements_map.get(ns.node_name)
                if p:
                    placement_agents.add(p.host_id)

            if len(placement_agents) == 1:
                placement_agent_id = list(placement_agents)[0]
                self.agent = self.session.get(models.Host, placement_agent_id)
                if self.agent and agent_client.is_agent_online(self.agent):
                    logger.info(
                        f"Using existing placement agent: {self.agent.name}"
                    )
                else:
                    self.agent = None

            if not self.agent:
                if self.lab.agent_id:
                    self.agent = self.session.get(
                        models.Host, self.lab.agent_id
                    )
                    if self.agent and not agent_client.is_agent_online(
                        self.agent
                    ):
                        self.agent = None

                if not self.agent:
                    self.agent = await agent_client.get_healthy_agent(
                        self.session,
                        required_provider=self.provider,
                    )

        if not self.agent:
            self.job.status = JobStatus.FAILED.value
            self.job.completed_at = datetime.now(timezone.utc)
            if self.target_agent_id:
                self.job.log_path = (
                    f"ERROR: Target agent {self.target_agent_id} is offline "
                    f"or unresponsive"
                )
                error_msg = "Target agent offline"
            else:
                self.job.log_path = (
                    f"ERROR: No healthy agent available with "
                    f"{self.provider} support"
                )
                error_msg = "No agent available"
            for ns in self.node_states:
                ns.actual_state = NodeActualState.ERROR.value
                ns.error_message = error_msg
            self.session.commit()
            logger.warning(
                f"Job {self.job.id} failed: no healthy agent available"
            )
            return False

        return True

    async def _check_resources(self) -> bool:
        """Pre-deploy resource validation.

        MUST run BEFORE _handle_migration (Phase 2.2).
        If insufficient: set error state, do NOT touch old container.
        Returns True if resources are OK, False if job should abort.
        """
        if not settings.resource_validation_enabled:
            return True

        from app.services.resource_capacity import (
            check_capacity,
            format_capacity_error,
        )

        deploy_candidates = [
            ns
            for ns in self.node_states
            if ns.desired_state == NodeDesiredState.RUNNING.value
            and ns.actual_state in ("undeployed", "pending")
        ]
        if not deploy_candidates:
            return True

        # Use batch-loaded node map for device types (Phase 2.3)
        device_types = []
        for ns in deploy_candidates:
            db_node = self.db_nodes_map.get(ns.node_name)
            device_types.append(db_node.device if db_node else "linux")

        cap_result = check_capacity(self.agent, device_types)
        if not cap_result.fits:
            error_msg = format_capacity_error({self.agent.id: cap_result})
            logger.warning(
                f"Job {self.job.id}: Resource check failed: {error_msg}"
            )
            self.job.status = JobStatus.FAILED.value
            self.job.completed_at = datetime.now(timezone.utc)
            self.job.log_path = f"ERROR: {error_msg}"
            for ns in deploy_candidates:
                ns.actual_state = NodeActualState.ERROR.value
                ns.error_message = "Insufficient resources on target agent"
                self._broadcast_state(ns, name_suffix="resource_error")
            self.session.commit()
            return False

        if cap_result.has_warnings:
            for w in cap_result.warnings:
                logger.warning(f"Job {self.job.id}: Resource warning: {w}")
                self.log_parts.append(f"WARNING: {w}")

        return True

    def _categorize_nodes(self) -> tuple[list, list, list]:
        """Classify nodes into deploy, start, and stop groups.

        Returns (nodes_need_deploy, nodes_need_start, nodes_need_stop).
        """
        nodes_need_deploy = []
        nodes_need_start = []
        nodes_need_stop = []

        for ns in self.node_states:
            if ns.desired_state == NodeDesiredState.RUNNING.value:
                if ns.actual_state in ("undeployed", "pending"):
                    nodes_need_deploy.append(ns)
                elif ns.actual_state in ("stopped", "error", "starting"):
                    nodes_need_start.append(ns)
            elif ns.desired_state == NodeDesiredState.STOPPED.value:
                if ns.actual_state in ("running", "stopping", "starting"):
                    nodes_need_stop.append(ns)

        logger.info(
            f"Sync job {self.job.id}: deploy={len(nodes_need_deploy)}, "
            f"start={len(nodes_need_start)}, stop={len(nodes_need_stop)}"
        )
        if nodes_need_start:
            self.log_parts.append(f"Starting {len(nodes_need_start)} node(s)...")

        return nodes_need_deploy, nodes_need_start, nodes_need_stop

    async def _handle_migration(
        self, nodes_to_start_or_deploy: list[models.NodeState]
    ):
        """Detect and handle nodes that exist on different agents.

        Stops containers on old agents before deploying to new agent.
        Only runs AFTER _check_resources confirms new host can accept (Phase 2.2).
        """
        from app.tasks.jobs import _update_node_placements

        node_names_to_check = [
            ns.node_name for ns in nodes_to_start_or_deploy
        ]

        # Use batch-loaded placements for migration detection (Phase 2.3)
        migrations_needed = []
        for node_name in node_names_to_check:
            placement = self.placements_map.get(node_name)
            if placement and placement.host_id != self.agent.id:
                migrations_needed.append(placement)

        if migrations_needed:
            self.log_parts.append(
                "=== Migration: Cleaning up containers on old agents ==="
            )
            logger.info(
                f"Migration needed for {len(migrations_needed)} nodes in "
                f"lab {self.lab.id}"
            )

            # Group by old agent for efficiency
            old_agent_nodes: dict[str, list[str]] = {}
            for placement in migrations_needed:
                if placement.host_id not in old_agent_nodes:
                    old_agent_nodes[placement.host_id] = []
                old_agent_nodes[placement.host_id].append(placement.node_name)

            for old_agent_id, node_names in old_agent_nodes.items():
                old_agent = self.session.get(models.Host, old_agent_id)
                if not old_agent:
                    self.log_parts.append(
                        f"  Old agent {old_agent_id} not found, skipping cleanup"
                    )
                    continue

                if not agent_client.is_agent_online(old_agent):
                    self.log_parts.append(
                        f"  Old agent {old_agent.name} is offline, skipping cleanup"
                    )
                    continue

                self.log_parts.append(
                    f"  Destroying {len(node_names)} container(s) on "
                    f"{old_agent.name}..."
                )

                for node_name in node_names:
                    try:
                        result = await agent_client.destroy_node_on_agent(
                            old_agent,
                            self.lab.id,
                            node_name,
                            provider=self.provider,
                        )
                        if result.get("success"):
                            self.log_parts.append(
                                f"    {node_name}: destroyed on {old_agent.name}"
                            )
                        else:
                            error = result.get("error", "unknown")
                            self.log_parts.append(
                                f"    {node_name}: {error}"
                            )
                    except Exception as e:
                        self.log_parts.append(
                            f"    {node_name}: cleanup failed - {e}"
                        )

                # Delete old placement records
                for node_name in node_names:
                    self.session.query(models.NodePlacement).filter(
                        models.NodePlacement.lab_id == self.lab.id,
                        models.NodePlacement.node_name == node_name,
                        models.NodePlacement.host_id == old_agent_id,
                    ).delete()

            self.session.commit()
            self.log_parts.append("")
            self._refresh_placements()

        # Fallback: check for untracked containers on other agents
        placed_node_names = set(self.placements_map.keys())
        node_actual_states = {
            ns.node_name: ns.actual_state for ns in nodes_to_start_or_deploy
        }

        # Use batch-loaded node map for explicit host check (Phase 2.3)
        nodes_with_explicit_host = set()
        for ns in nodes_to_start_or_deploy:
            db_node = self.db_nodes_map.get(ns.node_name)
            if db_node and db_node.host_id:
                nodes_with_explicit_host.add(ns.node_name)

        untracked_nodes = [
            n
            for n in node_names_to_check
            if n not in placed_node_names
            and node_actual_states.get(n) not in ("undeployed", None)
            and n not in nodes_with_explicit_host
        ]

        if untracked_nodes:
            all_agents = (
                self.session.query(models.Host)
                .filter(
                    models.Host.id != self.agent.id,
                    models.Host.status == HostStatus.ONLINE.value,
                )
                .all()
            )
            other_agents = [
                a for a in all_agents if agent_client.is_agent_online(a)
            ]

            if other_agents:
                self.log_parts.append(
                    "=== Migration: Checking other agents for untracked "
                    "containers ==="
                )
                logger.info(
                    f"Checking {len(other_agents)} other agents for "
                    f"{len(untracked_nodes)} untracked nodes in lab "
                    f"{self.lab.id}"
                )

                for other_agent in other_agents:
                    containers_found = []
                    for node_name in untracked_nodes:
                        container_name = _get_container_name(
                            self.lab.id, node_name
                        )
                        try:
                            result = await agent_client.container_action(
                                other_agent,
                                container_name,
                                "stop",
                                lab_id=self.lab.id,
                            )
                            if result.get("success"):
                                containers_found.append(node_name)
                                self.log_parts.append(
                                    f"  {node_name}: found and stopped on "
                                    f"{other_agent.name}"
                                )
                        except Exception as e:
                            logger.debug(
                                f"Container check failed on "
                                f"{other_agent.name}: {e}"
                            )

                    if containers_found:
                        logger.info(
                            f"Stopped {len(containers_found)} containers on "
                            f"{other_agent.name} during migration for lab "
                            f"{self.lab.id}"
                        )

                self.log_parts.append("")

        # Update placements EARLY with "starting" status
        node_names_for_placement = [
            ns.node_name for ns in nodes_to_start_or_deploy
        ]
        await _update_node_placements(
            self.session,
            self.lab.id,
            self.agent.id,
            node_names_for_placement,
            status="starting",
        )
        self._refresh_placements()

    async def _check_images(
        self,
        nodes_need_deploy: list,
        nodes_need_start: list,
        nodes_to_start_or_deploy: list,
        nodes_need_stop: list,
    ) -> tuple[list, list] | None:
        """Pre-deploy image sync check.

        Returns updated (nodes_need_deploy, nodes_need_start) or None if all
        nodes are syncing/failed and there's nothing left to do this pass.
        """
        if not (
            settings.image_sync_enabled
            and settings.image_sync_pre_deploy_check
        ):
            return nodes_need_deploy, nodes_need_start

        from app.tasks.image_sync import check_and_start_image_sync

        deploy_node_names = {
            ns.node_name for ns in nodes_to_start_or_deploy
        }
        full_image_map = self.topo_service.get_image_to_nodes_map(self.lab.id)
        image_to_nodes: dict[str, list[str]] = {}
        image_refs: list[str] = []
        for img_ref, node_names in full_image_map.items():
            filtered = [n for n in node_names if n in deploy_node_names]
            if filtered:
                image_to_nodes[img_ref] = filtered
                if img_ref not in image_refs:
                    image_refs.append(img_ref)

        if not image_refs:
            return nodes_need_deploy, nodes_need_start

        self.log_parts.append("=== Image Sync Check ===")
        self.log_parts.append(
            f"Checking {len(image_refs)} image(s) on {self.agent.name}..."
        )
        await self._broadcast_job_progress(
            "running",
            progress_message=f"Checking images on {self.agent.name}...",
        )

        syncing_nodes, failed_nodes, sync_log = (
            await check_and_start_image_sync(
                host_id=self.agent.id,
                image_references=image_refs,
                database=self.session,
                lab_id=self.lab.id,
                job_id=self.job.id,
                node_ids=self.node_ids,
                image_to_nodes=image_to_nodes,
                provider=self.provider,
            )
        )
        self.log_parts.extend(sync_log)
        self.log_parts.append("")

        excluded = syncing_nodes | failed_nodes
        if not excluded:
            return nodes_need_deploy, nodes_need_start

        nodes_need_deploy = [
            ns
            for ns in nodes_need_deploy
            if ns.node_name not in excluded
        ]
        nodes_need_start = [
            ns
            for ns in nodes_need_start
            if ns.node_name not in excluded
        ]

        # Mark syncing nodes
        for ns in nodes_to_start_or_deploy:
            if ns.node_name in syncing_nodes:
                ns.actual_state = NodeActualState.STARTING.value
                ns.starting_started_at = datetime.now(timezone.utc)
                ns.error_message = None
                self._broadcast_state(
                    ns,
                    name_suffix="starting",
                    image_sync_status="syncing",
                    image_sync_message=ns.image_sync_message,
                )

        # Mark failed nodes
        for ns in nodes_to_start_or_deploy:
            if ns.node_name in failed_nodes:
                ns.actual_state = NodeActualState.ERROR.value
                ns.error_message = "Required image not available on agent"
                self._broadcast_state(
                    ns,
                    name_suffix="imgsync",
                    image_sync_status="failed",
                    image_sync_message="Image not available",
                )

        self.session.commit()

        if (
            not nodes_need_deploy
            and not nodes_need_start
            and not nodes_need_stop
        ):
            if syncing_nodes:
                self.job.status = JobStatus.COMPLETED.value
                self.log_parts.append(
                    f"Waiting for image sync to complete for "
                    f"{len(syncing_nodes)} node(s)"
                )
            else:
                self.job.status = JobStatus.FAILED.value
            self.job.completed_at = datetime.now(timezone.utc)
            self.job.log_path = "\n".join(self.log_parts)
            self.session.commit()
            return None

        return nodes_need_deploy, nodes_need_start

    async def _deploy_nodes(self, nodes_need_deploy: list[models.NodeState]):
        """Deploy nodes — dispatches to per-node or topology path."""
        if settings.per_node_lifecycle_enabled:
            await self._deploy_nodes_per_node(nodes_need_deploy)
        else:
            await self._deploy_nodes_topology(nodes_need_deploy)

    async def _deploy_nodes_topology(self, nodes_need_deploy: list[models.NodeState]):
        """Deploy undeployed/pending nodes via full topology deploy.

        Containerlab requires full topology context, so we include all nodes
        on this agent to prevent it from destroying existing containers.
        """
        from app.tasks.jobs import (
            acquire_deploy_lock,
            release_deploy_lock,
            _update_node_placements,
            _capture_node_ips,
            _cleanup_orphan_containers,
        )

        self.log_parts.append("=== Phase 1: Deploy Topology ===")

        if not self.topo_service.has_nodes(self.lab.id):
            error_msg = "No topology defined in database"
            self.job.status = JobStatus.FAILED.value
            self.job.completed_at = datetime.now(timezone.utc)
            self.job.log_path = f"ERROR: {error_msg}"
            for ns in nodes_need_deploy:
                ns.actual_state = NodeActualState.ERROR.value
                ns.error_message = error_msg
            self.session.commit()
            return

        nodes_to_deploy_names = {ns.node_name for ns in nodes_need_deploy}
        filtered_graph, deployed_node_names = (
            self._filter_topology_for_agent(nodes_to_deploy_names)
        )

        if not deployed_node_names:
            self.log_parts.append(
                f"No nodes to deploy on {self.agent.name}"
            )
            for ns in nodes_need_deploy:
                ns.actual_state = NodeActualState.ERROR.value
                ns.error_message = "No nodes to deploy"
            self.session.commit()
            return

        # Validation: verify topology placement
        misplaced = self._validate_topology_placement(filtered_graph)
        if misplaced:
            error_msg = (
                f"DEPLOY ABORTED: Nodes assigned to different agent "
                f"detected in topology: {', '.join(misplaced)}. "
                f"This agent: {self.agent.id}"
            )
            logger.error(f"Job {self.job.id}: {error_msg}")
            self.log_parts.append(error_msg)
            for ns in nodes_need_deploy:
                ns.actual_state = NodeActualState.ERROR.value
                ns.error_message = "Deploy validation failed - wrong agent"
            self.session.commit()
            self.job.status = JobStatus.FAILED.value
            self.job.completed_at = datetime.now(timezone.utc)
            self.job.log_path = "\n".join(self.log_parts)
            self.session.commit()
            return

        topology_json = graph_to_deploy_topology(filtered_graph)
        self.log_parts.append(
            f"Deploying {len(filtered_graph.nodes)} node(s) on "
            f"{self.agent.name}: {', '.join(deployed_node_names)}"
        )

        all_topology_nodes = [
            n.container_name or n.name for n in filtered_graph.nodes
        ]
        lock_acquired, failed_nodes = acquire_deploy_lock(
            self.lab.id, all_topology_nodes, self.agent.id
        )
        if not lock_acquired:
            error_msg = (
                f"DEPLOY ABORTED: Could not acquire lock for nodes: "
                f"{', '.join(failed_nodes)}. Another deploy may be in progress."
            )
            logger.error(f"Job {self.job.id}: {error_msg}")
            self.log_parts.append(error_msg)
            for ns in nodes_need_deploy:
                ns.actual_state = NodeActualState.ERROR.value
                ns.error_message = "Deploy lock conflict"
            self.session.commit()
            self.job.status = JobStatus.FAILED.value
            self.job.completed_at = datetime.now(timezone.utc)
            self.job.log_path = "\n".join(self.log_parts)
            self.session.commit()
            return

        try:
            result = await agent_client.deploy_to_agent(
                self.agent,
                self.job.id,
                self.lab.id,
                topology=topology_json,
                provider=self.provider,
            )

            if result.get("status") == "completed":
                self.log_parts.append("Deploy completed successfully")
                await _capture_node_ips(
                    self.session, self.lab.id, self.agent
                )

                # Re-query all states (deploy may have changed things)
                all_states = (
                    self.session.query(models.NodeState)
                    .filter(models.NodeState.lab_id == self.lab.id)
                    .all()
                )

                await _update_node_placements(
                    self.session,
                    self.lab.id,
                    self.agent.id,
                    list(deployed_node_names),
                )

                if (
                    self.old_agent_ids
                    and self.agent.id not in self.old_agent_ids
                ):
                    self.log_parts.append("")
                    self.log_parts.append("=== Orphan Cleanup ===")
                    await _cleanup_orphan_containers(
                        self.session,
                        self.lab.id,
                        self.agent.id,
                        self.old_agent_ids,
                        self.log_parts,
                    )

                # Identify nodes that should be stopped after deploy
                nodes_to_stop_after = [
                    ns
                    for ns in all_states
                    if ns.desired_state == NodeDesiredState.STOPPED.value
                    and ns.node_name in deployed_node_names
                ]

                # Mark deployed nodes as running
                for ns in all_states:
                    if ns.node_name in deployed_node_names:
                        ns.actual_state = NodeActualState.RUNNING.value
                        ns.error_message = None
                        if not ns.boot_started_at:
                            ns.boot_started_at = datetime.now(timezone.utc)
                self.session.commit()

                # Stop nodes that should be stopped
                if nodes_to_stop_after:
                    self.log_parts.append("")
                    self.log_parts.append(
                        f"Stopping {len(nodes_to_stop_after)} nodes with "
                        f"desired_state=stopped..."
                    )
                    for ns in nodes_to_stop_after:
                        container_name = _get_container_name(
                            self.lab.id, ns.node_name
                        )
                        stop_result = await agent_client.container_action(
                            self.agent,
                            container_name,
                            "stop",
                            lab_id=self.lab.id,
                        )
                        if stop_result.get("success"):
                            ns.actual_state = NodeActualState.STOPPED.value
                            ns.stopping_started_at = None
                            ns.boot_started_at = None
                            self.log_parts.append(
                                f"  {ns.node_name}: stopped"
                            )
                        else:
                            ns.actual_state = NodeActualState.ERROR.value
                            ns.stopping_started_at = None
                            ns.error_message = (
                                stop_result.get("error") or "Stop failed"
                            )
                            ns.boot_started_at = None
                            self.log_parts.append(
                                f"  {ns.node_name}: FAILED - "
                                f"{ns.error_message}"
                            )
                    self.session.commit()

            else:
                error_msg = result.get("error_message", "Deploy failed")
                self.log_parts.append(f"Deploy FAILED: {error_msg}")
                for ns in nodes_need_deploy:
                    ns.actual_state = NodeActualState.ERROR.value
                    ns.error_message = error_msg
                self.session.commit()

            if result.get("stdout"):
                self.log_parts.append(
                    f"\nDeploy STDOUT:\n{result['stdout']}"
                )
            if result.get("stderr"):
                self.log_parts.append(
                    f"\nDeploy STDERR:\n{result['stderr']}"
                )

        except AgentUnavailableError as e:
            error_msg = f"Agent unreachable (transient): {e.message}"
            self.log_parts.append(f"Deploy FAILED (transient): {error_msg}")
            self.log_parts.append(
                "  Note: This may be a temporary network issue. "
                "Nodes will be retried by reconciliation."
            )
            for ns in nodes_need_deploy:
                if ns.actual_state not in ("running", "stopped"):
                    self._handle_transient_failure(ns, error_msg)
            self.session.commit()
            logger.warning(
                f"Deploy in sync job {self.job.id} failed due to agent "
                f"unavailability: {e}"
            )
        except Exception as e:
            error_msg = str(e)
            self.log_parts.append(f"Deploy FAILED: {error_msg}")
            for ns in nodes_need_deploy:
                ns.actual_state = NodeActualState.ERROR.value
                ns.error_message = error_msg
            self.session.commit()
            logger.exception(
                f"Deploy failed in sync job {self.job.id}: {e}"
            )
        finally:
            release_deploy_lock(self.lab.id, all_topology_nodes)

    async def _start_nodes(self, nodes_need_start: list[models.NodeState]):
        """Start nodes — dispatches to per-node or topology path."""
        if settings.per_node_lifecycle_enabled:
            await self._start_nodes_per_node(nodes_need_start)
        else:
            await self._start_nodes_topology(nodes_need_start)

    async def _start_nodes_topology(self, nodes_need_start: list[models.NodeState]):
        """Start stopped nodes via full redeploy.

        Docker start alone doesn't recreate network interfaces.
        Containerlab --reconfigure destroys and recreates all veth pairs.
        """
        from app.tasks.jobs import (
            acquire_deploy_lock,
            release_deploy_lock,
            _update_node_placements,
            _capture_node_ips,
            _cleanup_orphan_containers,
        )

        self.log_parts.append("")
        self.log_parts.append("=== Phase 2: Start Nodes (via redeploy) ===")
        self.log_parts.append(
            "Note: Full redeploy required to recreate network interfaces"
        )

        # Re-read desired_state to catch changes since job was queued
        for ns in nodes_need_start:
            self.session.refresh(ns)
        nodes_need_start = [
            ns for ns in nodes_need_start
            if ns.desired_state == NodeDesiredState.RUNNING.value
        ]
        if not nodes_need_start:
            self.log_parts.append("  All nodes' desired_state changed, nothing to start")
            return

        if not self.topo_service.has_nodes(self.lab.id):
            error_msg = "No topology defined in database"
            for ns in nodes_need_start:
                ns.actual_state = NodeActualState.ERROR.value
                ns.starting_started_at = None
                ns.error_message = error_msg
            self.session.commit()
            self.log_parts.append(f"Redeploy FAILED: {error_msg}")
            return

        nodes_to_start_names = {ns.node_name for ns in nodes_need_start}
        filtered_graph, deployed_node_names = (
            self._filter_topology_for_agent(nodes_to_start_names)
        )

        if not deployed_node_names:
            self.log_parts.append(
                f"No nodes to redeploy on {self.agent.name}"
            )
            for ns in nodes_need_start:
                ns.actual_state = NodeActualState.ERROR.value
                ns.starting_started_at = None
                ns.error_message = "No nodes to deploy"
            self.session.commit()
            return

        # Validation: verify topology placement
        misplaced = self._validate_topology_placement(filtered_graph)
        if misplaced:
            error_msg = (
                f"REDEPLOY ABORTED: Nodes assigned to different agent "
                f"detected: {', '.join(misplaced)}. "
                f"This agent: {self.agent.id}"
            )
            logger.error(f"Job {self.job.id}: {error_msg}")
            self.log_parts.append(error_msg)
            for ns in nodes_need_start:
                ns.actual_state = NodeActualState.ERROR.value
                ns.starting_started_at = None
                ns.error_message = "Deploy validation failed - wrong agent"
            self.session.commit()
            self.job.status = JobStatus.FAILED.value
            self.job.completed_at = datetime.now(timezone.utc)
            self.job.log_path = "\n".join(self.log_parts)
            self.session.commit()
            return

        topology_json = graph_to_deploy_topology(filtered_graph)
        self.log_parts.append(
            f"Redeploying {len(filtered_graph.nodes)} node(s) on "
            f"{self.agent.name}: {', '.join(deployed_node_names)}"
        )

        all_topology_nodes = [
            n.container_name or n.name for n in filtered_graph.nodes
        ]
        lock_acquired, failed_nodes = acquire_deploy_lock(
            self.lab.id, all_topology_nodes, self.agent.id
        )
        if not lock_acquired:
            error_msg = (
                f"REDEPLOY ABORTED: Could not acquire lock for nodes: "
                f"{', '.join(failed_nodes)}. Another deploy may be in progress."
            )
            logger.error(f"Job {self.job.id}: {error_msg}")
            self.log_parts.append(error_msg)
            for ns in nodes_need_start:
                ns.actual_state = NodeActualState.ERROR.value
                ns.starting_started_at = None
                ns.error_message = "Deploy lock conflict"
            self.session.commit()
            self.job.status = JobStatus.FAILED.value
            self.job.completed_at = datetime.now(timezone.utc)
            self.job.log_path = "\n".join(self.log_parts)
            self.session.commit()
            return

        try:
            result = await agent_client.deploy_to_agent(
                self.agent,
                self.job.id,
                self.lab.id,
                topology=topology_json,
                provider=self.provider,
            )

            if result.get("status") == "completed":
                self.log_parts.append("Redeploy completed successfully")
                await _capture_node_ips(
                    self.session, self.lab.id, self.agent
                )

                all_states = (
                    self.session.query(models.NodeState)
                    .filter(models.NodeState.lab_id == self.lab.id)
                    .all()
                )

                await _update_node_placements(
                    self.session,
                    self.lab.id,
                    self.agent.id,
                    list(deployed_node_names),
                )

                if (
                    self.old_agent_ids
                    and self.agent.id not in self.old_agent_ids
                ):
                    self.log_parts.append("")
                    self.log_parts.append("=== Orphan Cleanup ===")
                    await _cleanup_orphan_containers(
                        self.session,
                        self.lab.id,
                        self.agent.id,
                        self.old_agent_ids,
                        self.log_parts,
                    )

                # Mark started nodes as running
                for ns in all_states:
                    if ns.node_name in deployed_node_names:
                        ns.actual_state = NodeActualState.RUNNING.value
                        ns.starting_started_at = None
                        ns.error_message = None
                        if not ns.boot_started_at:
                            ns.boot_started_at = datetime.now(timezone.utc)
                        self.log_parts.append(
                            f"  Node {ns.node_name}: started"
                        )

                # Stop nodes that should be stopped after redeploy
                nodes_to_stop_after = [
                    ns
                    for ns in all_states
                    if ns.desired_state == NodeDesiredState.STOPPED.value
                    and ns.node_name in deployed_node_names
                ]
                if nodes_to_stop_after:
                    self.log_parts.append("")
                    self.log_parts.append(
                        f"Stopping {len(nodes_to_stop_after)} nodes with "
                        f"desired_state=stopped..."
                    )
                    for ns in nodes_to_stop_after:
                        container_name = _get_container_name(
                            self.lab.id, ns.node_name
                        )
                        stop_result = await agent_client.container_action(
                            self.agent,
                            container_name,
                            "stop",
                            lab_id=self.lab.id,
                        )
                        if stop_result.get("success"):
                            ns.actual_state = NodeActualState.STOPPED.value
                            ns.stopping_started_at = None
                            ns.boot_started_at = None
                            self.log_parts.append(
                                f"  {ns.node_name}: stopped"
                            )
                        else:
                            ns.actual_state = NodeActualState.ERROR.value
                            ns.stopping_started_at = None
                            ns.error_message = (
                                stop_result.get("error") or "Stop failed"
                            )
                            ns.boot_started_at = None
                            self.log_parts.append(
                                f"  {ns.node_name}: FAILED - "
                                f"{ns.error_message}"
                            )
            else:
                error_msg = result.get("error_message", "Redeploy failed")
                self.log_parts.append(f"Redeploy FAILED: {error_msg}")
                for ns in nodes_need_start:
                    ns.actual_state = NodeActualState.ERROR.value
                    ns.starting_started_at = None
                    ns.error_message = error_msg

            if result.get("stdout"):
                self.log_parts.append(
                    f"\nDeploy STDOUT:\n{result['stdout']}"
                )
            if result.get("stderr"):
                self.log_parts.append(
                    f"\nDeploy STDERR:\n{result['stderr']}"
                )

        except AgentUnavailableError as e:
            error_msg = f"Agent unreachable (transient): {e.message}"
            self.log_parts.append(
                f"Redeploy FAILED (transient): {error_msg}"
            )
            self.log_parts.append(
                "  Note: This may be a temporary network issue. "
                "Nodes will be retried by reconciliation."
            )
            for ns in nodes_need_start:
                self._handle_transient_failure(ns, error_msg)
            logger.warning(
                f"Redeploy in sync job {self.job.id} failed due to agent "
                f"unavailability: {e}"
            )
        except Exception as e:
            error_msg = str(e)
            self.log_parts.append(f"Redeploy FAILED: {error_msg}")
            for ns in nodes_need_start:
                ns.actual_state = NodeActualState.ERROR.value
                ns.starting_started_at = None
                ns.error_message = error_msg
            logger.exception(
                f"Redeploy failed in sync job {self.job.id}: {e}"
            )
        finally:
            release_deploy_lock(self.lab.id, all_topology_nodes)

        self.session.commit()

    async def _deploy_single_node(self, ns: models.NodeState) -> str | None:
        """Create and start a single node container/VM.

        Returns the node_name on success, None on failure.
        Used by _deploy_nodes_per_node for parallel/sequential deploys.
        """
        db_node = self.db_nodes_map.get(ns.node_name)
        if not db_node:
            ns.actual_state = NodeActualState.ERROR.value
            ns.error_message = "Node definition not found"
            self.log_parts.append(f"  {ns.node_name}: ERROR - no node definition")
            return None

        kind = db_node.device or "linux"
        iface_count = self._get_interface_count(ns.node_name)
        startup_config = self._get_startup_config(ns.node_name, db_node)

        # Resolve image: explicit → manifest → vendor default
        image = resolve_node_image(db_node.device, kind, db_node.image, db_node.version)
        if not image:
            ns.actual_state = NodeActualState.ERROR.value
            ns.error_message = f"No image found for device '{db_node.device}'. Import one first."
            self.log_parts.append(f"  {ns.node_name}: ERROR - no image available")
            return None

        # Determine provider from image type (qcow2 → libvirt, else docker)
        node_provider = get_image_provider(image)

        # Resolve hardware specs: per-node config > device overrides > vendor defaults
        from app.services.device_service import get_device_service

        try:
            node_config = json.loads(db_node.config_json) if db_node.config_json else None
            hw_specs = get_device_service().resolve_hardware_specs(
                kind,
                node_config,
                image,
                version=db_node.version,
            )

            # Look up image SHA256 from cached manifest for integrity verification
            image_sha256 = None
            if node_provider == "libvirt" and self._manifest:
                img_entry = find_image_by_reference(self._manifest, image)
                if img_entry:
                    image_sha256 = img_entry.get("sha256")

            # Create container/VM
            create_result = await agent_client.create_node_on_agent(
                self.agent,
                self.lab.id,
                ns.node_name,
                kind,
                image=image,
                display_name=db_node.display_name,
                interface_count=iface_count,
                startup_config=startup_config,
                provider=node_provider,
                memory=hw_specs.get("memory"),
                cpu=hw_specs.get("cpu"),
                cpu_limit=hw_specs.get("cpu_limit"),
                disk_driver=hw_specs.get("disk_driver"),
                nic_driver=hw_specs.get("nic_driver"),
                machine_type=hw_specs.get("machine_type"),
                libvirt_driver=hw_specs.get("libvirt_driver"),
                readiness_probe=hw_specs.get("readiness_probe"),
                readiness_pattern=hw_specs.get("readiness_pattern"),
                readiness_timeout=hw_specs.get("readiness_timeout"),
                efi_boot=hw_specs.get("efi_boot"),
                efi_vars=hw_specs.get("efi_vars"),
                data_volume_gb=hw_specs.get("data_volume_gb"),
                image_sha256=image_sha256,
            )

            if not create_result.get("success"):
                error_msg = create_result.get("error", "Container creation failed")
                ns.actual_state = NodeActualState.ERROR.value
                ns.error_message = error_msg
                self.log_parts.append(f"  {ns.node_name}: CREATE FAILED - {error_msg}")
                return None

            create_details = (create_result.get("details") or "").strip()
            if create_details and node_provider == "libvirt":
                for line in create_details.splitlines():
                    self.log_parts.append(f"    {ns.node_name} create: {line}")

            # Start container/VM
            start_result = await agent_client.start_node_on_agent(
                self.agent,
                self.lab.id,
                ns.node_name,
                provider=node_provider,
            )

            if start_result.get("success"):
                ns.actual_state = NodeActualState.RUNNING.value
                ns.error_message = None
                ns.boot_started_at = datetime.now(timezone.utc)
                self.log_parts.append(f"  {ns.node_name}: deployed and started")
                self._broadcast_state(ns, name_suffix="started")
                logger.info(
                    "Node state transition",
                    extra={
                        "event": "node_state_transition",
                        "lab_id": self.lab.id,
                        "node_id": ns.node_id,
                        "node_name": ns.node_name,
                        "old_state": "pending",
                        "new_state": "running",
                        "trigger": "agent_response",
                        "agent_id": self.agent.id,
                        "job_id": self.job.id,
                    },
                )
                return ns.node_name
            else:
                error_msg = start_result.get("error", "Container start failed")
                ns.actual_state = NodeActualState.ERROR.value
                ns.error_message = error_msg
                self.log_parts.append(f"  {ns.node_name}: START FAILED - {error_msg}")
                logger.info(
                    "Node state transition",
                    extra={
                        "event": "node_state_transition",
                        "lab_id": self.lab.id,
                        "node_id": ns.node_id,
                        "node_name": ns.node_name,
                        "old_state": "pending",
                        "new_state": "error",
                        "trigger": "agent_response",
                        "agent_id": self.agent.id,
                        "job_id": self.job.id,
                        "error_message": error_msg,
                    },
                )
                return None

        except AgentUnavailableError as e:
            self._handle_transient_failure(ns, f"Agent unreachable: {e.message}")
            return None
        except Exception as e:
            ns.actual_state = NodeActualState.ERROR.value
            ns.error_message = str(e)
            self.log_parts.append(f"  {ns.node_name}: FAILED - {e}")
            logger.exception(f"Deploy node {ns.node_name} failed: {e}")
            return None

    async def _create_and_start_nodes(
        self,
        nodes: list[models.NodeState],
        phase_label: str,
    ) -> list[str]:
        """Deploy or start nodes via per-node create+start.

        Non-cEOS nodes deploy in parallel; cEOS nodes deploy
        sequentially with stagger delay. Returns list of deployed node names.
        """
        from app.tasks.jobs import (
            _update_node_placements,
            _capture_node_ips,
        )

        self.log_parts.append(phase_label)

        # Re-read desired_state to catch changes since job was queued
        for ns in nodes:
            self.session.refresh(ns)
        nodes = [
            ns for ns in nodes
            if ns.desired_state == NodeDesiredState.RUNNING.value
        ]
        if not nodes:
            self.log_parts.append("  All nodes' desired_state changed, nothing to deploy")
            return []

        deployed_names: list[str] = []

        # Split into cEOS (needs stagger) and non-cEOS (can deploy in parallel)
        ceos_nodes = []
        non_ceos_nodes = []
        for ns in nodes:
            db_node = self.db_nodes_map.get(ns.node_name)
            kind = (db_node.device or "linux") if db_node else "linux"
            if _is_ceos_kind(kind):
                ceos_nodes.append(ns)
            else:
                non_ceos_nodes.append(ns)

        # Deploy all non-cEOS nodes in parallel (with retry)
        if non_ceos_nodes:
            self.log_parts.append(f"  Deploying {len(non_ceos_nodes)} non-cEOS node(s) in parallel")
            tasks = [self._deploy_single_node_with_retry(ns) for ns in non_ceos_nodes]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for ns, result in zip(non_ceos_nodes, results):
                if isinstance(result, Exception):
                    logger.exception(f"Parallel deploy of {ns.node_name} raised: {result}")
                elif result:
                    deployed_names.append(result)

            # Connect links between already-deployed nodes (don't wait for cEOS)
            if deployed_names:
                await self._connect_same_host_links(set(deployed_names))

        # Deploy cEOS nodes sequentially with stagger (with retry)
        ceos_started = False
        for ns in ceos_nodes:
            if ceos_started:
                # Use stagger time to connect any new links
                await asyncio.sleep(CEOS_STAGGER_SECONDS)

            name = await self._deploy_single_node_with_retry(ns)
            if name:
                deployed_names.append(name)
                ceos_started = True
                # Connect any newly-eligible links after each cEOS node
                await self._connect_same_host_links(set(deployed_names))

        self.session.commit()

        if deployed_names:
            # Update placements
            await _update_node_placements(
                self.session, self.lab.id, self.agent.id, deployed_names
            )
            # Capture IPs
            await _capture_node_ips(self.session, self.lab.id, self.agent)
            # Connect same-host links
            await self._connect_same_host_links(set(deployed_names))

        return deployed_names

    async def _deploy_single_node_with_retry(self, ns: models.NodeState) -> str | None:
        """Deploy a single node with retry on transient failures."""
        for attempt in range(1, DEPLOY_RETRY_ATTEMPTS + 1):
            result = await self._deploy_single_node(ns)
            if result is not None:
                return result

            # Check if failure was transient (pending state = transient)
            if ns.actual_state != NodeActualState.PENDING.value:
                # Non-transient failure (error state) — don't retry
                return None

            if attempt < DEPLOY_RETRY_ATTEMPTS:
                logger.info(
                    f"Retrying deploy of {ns.node_name} "
                    f"(attempt {attempt + 1}/{DEPLOY_RETRY_ATTEMPTS}) "
                    f"after {DEPLOY_RETRY_BACKOFF_SECONDS}s backoff"
                )
                self.log_parts.append(
                    f"  {ns.node_name}: retrying in {DEPLOY_RETRY_BACKOFF_SECONDS}s "
                    f"(attempt {attempt + 1}/{DEPLOY_RETRY_ATTEMPTS})"
                )
                await asyncio.sleep(DEPLOY_RETRY_BACKOFF_SECONDS)

        # All retries exhausted
        if ns.actual_state == NodeActualState.PENDING.value:
            self._handle_transient_failure(
                ns, ns.error_message or "Agent unreachable after retries"
            )
        return None

    async def _deploy_nodes_per_node(self, nodes_need_deploy: list[models.NodeState]):
        """Deploy nodes via per-node container creation and start."""
        await self._create_and_start_nodes(
            nodes_need_deploy, "=== Phase 1: Deploy (per-node) ==="
        )

    async def _start_single_node(self, ns: models.NodeState) -> str | None:
        """Start a single node and update its state.

        DEPRECATED: Unified lifecycle always uses _deploy_single_node() for
        fresh container creation. Kept temporarily for reference.
        """
        db_node = self.db_nodes_map.get(ns.node_name)
        kind = db_node.device if db_node else "linux"

        # Determine provider from image type
        image = resolve_node_image(
            db_node.device, kind, db_node.image, db_node.version
        ) if db_node else None
        node_provider = get_image_provider(image)

        try:
            result = await agent_client.start_node_on_agent(
                self.agent,
                self.lab.id,
                ns.node_name,
                provider=node_provider,
            )

            if result.get("success"):
                old_state = ns.actual_state
                ns.actual_state = NodeActualState.RUNNING.value
                ns.starting_started_at = None
                ns.error_message = None
                if not ns.boot_started_at:
                    ns.boot_started_at = datetime.now(timezone.utc)
                self.log_parts.append(f"  {ns.node_name}: started")
                self._broadcast_state(ns, name_suffix="started")
                logger.info(
                    "Node state transition",
                    extra={
                        "event": "node_state_transition",
                        "lab_id": self.lab.id,
                        "node_id": ns.node_id,
                        "node_name": ns.node_name,
                        "old_state": old_state,
                        "new_state": "running",
                        "trigger": "agent_response",
                        "agent_id": self.agent.id,
                        "job_id": self.job.id,
                    },
                )
                return ns.node_name
            else:
                error_msg = result.get("error", "Start failed")
                error_lc = error_msg.lower()
                # Self-heal drift: start requested but runtime object is missing
                # (e.g., libvirt domain deleted). Fall back to full deploy.
                if "not found" in error_lc:
                    self.log_parts.append(
                        f"  {ns.node_name}: start target missing, attempting redeploy..."
                    )
                    return await self._deploy_single_node(ns)

                old_state = ns.actual_state
                ns.actual_state = NodeActualState.ERROR.value
                ns.starting_started_at = None
                ns.error_message = error_msg
                self.log_parts.append(f"  {ns.node_name}: FAILED - {error_msg}")
                logger.info(
                    "Node state transition",
                    extra={
                        "event": "node_state_transition",
                        "lab_id": self.lab.id,
                        "node_id": ns.node_id,
                        "node_name": ns.node_name,
                        "old_state": old_state,
                        "new_state": "error",
                        "trigger": "agent_response",
                        "agent_id": self.agent.id,
                        "job_id": self.job.id,
                        "error_message": error_msg,
                    },
                )
                return None

        except AgentUnavailableError as e:
            self._handle_transient_failure(ns, f"Agent unreachable: {e.message}")
            return None
        except Exception as e:
            ns.actual_state = NodeActualState.ERROR.value
            ns.starting_started_at = None
            ns.error_message = str(e)
            self.log_parts.append(f"  {ns.node_name}: FAILED - {e}")
            logger.exception(f"Start node {ns.node_name} failed: {e}")
            return None

    async def _start_nodes_per_node(self, nodes_need_start: list[models.NodeState]):
        """Start stopped nodes via fresh container/VM creation."""
        self.log_parts.append("")
        await self._create_and_start_nodes(
            nodes_need_start,
            "=== Phase 2: Start Nodes (per-node, fresh create) ===",
        )

    def _get_interface_count(self, node_name: str) -> int:
        """Get interface count for a node from topology service."""
        iface_map = self.topo_service.get_interface_count_map(self.lab.id)
        count = iface_map.get(node_name, 0)
        return max(count, 4)  # Minimum 4 interfaces for flexibility

    def _get_startup_config(self, node_name: str, db_node: models.Node) -> str | None:
        """Resolve startup config content for create/start operations.

        Priority:
        1. N9Kv: saved workspace config (explicitly prefer persisted file)
        2. active snapshot
        3. config_json["startup-config"]
        4. latest snapshot
        5. saved workspace config (fallback for non-N9Kv)
        """

        def _read_saved_workspace_config() -> str | None:
            try:
                config_file = (
                    lab_workspace(self.lab.id)
                    / "configs"
                    / node_name
                    / "startup-config"
                )
                if not config_file.exists():
                    return None
                content = config_file.read_text(encoding="utf-8")
                return content if content.strip() else None
            except Exception:
                return None

        saved_workspace = _read_saved_workspace_config()
        kind = (db_node.device or "").strip().lower()

        # N9Kv startup-config staging is sensitive; prefer the saved workspace file.
        if kind == "cisco_n9kv" and saved_workspace:
            return saved_workspace

        try:
            # Use active config snapshot if set
            if db_node.active_config_snapshot_id:
                snapshot = self.explicit_snapshots_map.get(
                    db_node.active_config_snapshot_id
                )
                if snapshot and snapshot.content:
                    return snapshot.content

            # Fall back to config_json startup-config
            if db_node.config_json:
                config = json.loads(db_node.config_json)
                startup = config.get("startup-config")
                if isinstance(startup, str) and startup.strip():
                    return startup

            # Fall back to latest snapshot
            snapshot = self.latest_snapshots_map.get(node_name)
            if snapshot and snapshot.content:
                return snapshot.content
        except Exception:
            pass

        return saved_workspace

    async def _connect_same_host_links(self, node_names: set[str]):
        """Connect same-host links for the given nodes.

        Collects eligible links then connects them all in parallel via
        asyncio.gather (each link is independent — unique VLAN tags).
        """
        if not self.graph:
            self.graph = self.topo_service.export_to_graph(self.lab.id)

        from app.services.interface_naming import normalize_interface

        # Collect eligible links
        link_tasks = []
        for link in self.graph.links:
            if len(link.endpoints) != 2:
                continue

            ep_a, ep_b = link.endpoints
            node_a = ep_a.node
            node_b = ep_b.node

            # Resolve node names (endpoints may use gui_id)
            for n in self.graph.nodes:
                if n.id == node_a:
                    node_a = n.container_name or n.name
                if n.id == node_b:
                    node_b = n.container_name or n.name

            # Only connect if at least one endpoint is in our deployed/started set
            if node_a not in node_names and node_b not in node_names:
                continue

            # Both endpoints must be on this agent
            placement_a = self.placements_map.get(node_a)
            placement_b = self.placements_map.get(node_b)
            if not placement_a or not placement_b:
                continue
            if placement_a.host_id != self.agent.id or placement_b.host_id != self.agent.id:
                continue

            # Both nodes should be running
            state_a = self.all_lab_states.get(node_a)
            state_b = self.all_lab_states.get(node_b)
            if not state_a or not state_b:
                continue
            if state_a.actual_state != NodeActualState.RUNNING.value:
                continue
            if state_b.actual_state != NodeActualState.RUNNING.value:
                continue

            ifname_a = normalize_interface(ep_a.ifname)
            ifname_b = normalize_interface(ep_b.ifname)
            link_tasks.append((node_a, ifname_a, node_b, ifname_b))

        if not link_tasks:
            return

        # Connect all links in parallel
        async def _connect_one(na, ifa, nb, ifb):
            result = await agent_client.create_link_on_agent(
                self.agent, self.lab.id, na, ifa, nb, ifb,
            )
            if result.get("success"):
                return True
            logger.warning(
                f"Failed to connect link {na}:{ifa} <-> {nb}:{ifb}: {result.get('error')}"
            )
            return False

        results = await asyncio.gather(
            *[_connect_one(*args) for args in link_tasks],
            return_exceptions=True,
        )

        links_connected = 0
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                na, ifa, nb, ifb = link_tasks[i]
                logger.warning(f"Failed to connect link {na}:{ifa} <-> {nb}:{ifb}: {result}")
            elif result:
                links_connected += 1

        if links_connected:
            self.log_parts.append(f"  Connected {links_connected} same-host link(s)")

    async def _auto_extract_before_stop(
        self, nodes_need_stop: list[models.NodeState]
    ) -> None:
        """Auto-extract configs from running nodes before removing them.

        Creates autosave snapshots and sets them as the active config so the
        next start uses the most recent running config. Failure-tolerant:
        extraction errors are logged but don't block the stop operation.
        """
        if not settings.feature_auto_extract_on_stop:
            return

        # Extract from nodes that are running or in stopping transition
        # (transitional states are set before this method is called)
        extractable_states = {
            NodeActualState.RUNNING.value,
            NodeActualState.STOPPING.value,
        }
        running_nodes = [
            ns for ns in nodes_need_stop
            if ns.actual_state in extractable_states
        ]
        if not running_nodes:
            return

        try:
            self.log_parts.append("  Auto-extracting configs before stop...")

            # Group nodes by agent
            agent_node_map: dict[str, tuple[models.Host, list[str]]] = {}
            for ns in running_nodes:
                placement = self.placements_map.get(ns.node_name)
                if placement and placement.host_id != self.agent.id:
                    extract_agent = self.session.get(
                        models.Host, placement.host_id
                    )
                    if not (extract_agent and agent_client.is_agent_online(extract_agent)):
                        extract_agent = self.agent
                else:
                    extract_agent = self.agent

                entry = agent_node_map.setdefault(
                    extract_agent.id, (extract_agent, [])
                )
                entry[1].append(ns.node_name)

            # Call agents concurrently with timeout
            EXTRACTION_TIMEOUT = 15  # seconds
            tasks = [
                agent_client.extract_configs_on_agent(a, self.lab.id)
                for a, _ in agent_node_map.values()
            ]
            try:
                results = await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=EXTRACTION_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    f"Config extraction timed out after {EXTRACTION_TIMEOUT}s "
                    f"for lab {self.lab.id}"
                )
                self.log_parts.append(
                    f"    Auto-extract timed out after {EXTRACTION_TIMEOUT}s "
                    f"(proceeding with stop)"
                )
                return

            # Collect configs (filter to only nodes being stopped)
            stop_node_names = {ns.node_name for ns in running_nodes}
            configs = []
            for (a, _node_names), result in zip(agent_node_map.values(), results):
                if isinstance(result, Exception):
                    logger.warning(f"Auto-extract failed on agent {a.id}: {result}")
                    continue
                if not result.get("success"):
                    logger.warning(
                        f"Auto-extract failed on agent {a.id}: "
                        f"{result.get('error', 'Unknown')}"
                    )
                    continue
                for cfg in result.get("configs", []):
                    if cfg.get("node_name") in stop_node_names:
                        configs.append(cfg)

            if not configs:
                self.log_parts.append("    No configs extracted")
                return

            # Save as autosave snapshots with set_as_active=True
            from app.services.config_service import ConfigService
            config_svc = ConfigService(self.session)

            node_device_map = {
                n.container_name: n.device
                for n in self.session.query(models.Node)
                .filter(models.Node.lab_id == self.lab.id)
                .all()
            }

            snapshots_created = 0
            for cfg in configs:
                node_name = cfg.get("node_name")
                content = cfg.get("content")
                if not node_name or not content:
                    continue
                snapshot = config_svc.save_extracted_config(
                    lab_id=self.lab.id,
                    node_name=node_name,
                    content=content,
                    snapshot_type="autosave",
                    device_kind=node_device_map.get(node_name),
                    set_as_active=True,
                )
                if snapshot:
                    snapshots_created += 1

            self.session.commit()
            self.log_parts.append(
                f"    Extracted {len(configs)} config(s), "
                f"created {snapshots_created} autosave snapshot(s)"
            )
            logger.info(
                f"Auto-extracted {len(configs)} configs before stop for lab {self.lab.id}"
            )

        except Exception as e:
            logger.warning(f"Error during auto-extract before stop: {e}")
            self.log_parts.append(f"    Auto-extract failed: {e} (continuing with stop)")

    async def _stop_nodes(self, nodes_need_stop: list[models.NodeState]):
        """Stop running containers using batch reconcile per agent.

        Groups nodes by target agent (using placement data) and sends one
        batch reconcile request per agent. Nodes not found on a non-default
        agent are retried on the default agent in a single fallback batch.
        """
        self.log_parts.append("")
        self.log_parts.append("=== Phase 3: Stop Nodes ===")

        # Re-read desired_state to catch changes since job was queued
        # (e.g. user clicked Start All while stop was in progress)
        for ns in nodes_need_stop:
            self.session.refresh(ns)
        nodes_need_stop = [
            ns for ns in nodes_need_stop
            if ns.desired_state == NodeDesiredState.STOPPED.value
        ]
        if not nodes_need_stop:
            self.log_parts.append("  All nodes' desired_state changed, nothing to stop")
            return

        # Auto-extract configs before removing containers
        await self._auto_extract_before_stop(nodes_need_stop)

        # Group nodes by target agent
        # agent_id -> (agent, [(ns, container_name)])
        agent_groups: dict[str, tuple[models.Host, list[tuple[models.NodeState, str]]]] = {}
        for ns in nodes_need_stop:
            container_name = _get_container_name(self.lab.id, ns.node_name)
            self.log_parts.append(
                f"Stopping {ns.node_name} ({container_name})..."
            )

            # Use actual container location from placements (Phase 2.3)
            placement = self.placements_map.get(ns.node_name)
            if placement and placement.host_id != self.agent.id:
                actual_agent = self.session.get(
                    models.Host, placement.host_id
                )
                if actual_agent and agent_client.is_agent_online(
                    actual_agent
                ):
                    stop_agent = actual_agent
                    self.log_parts.append(
                        f"    (container on {actual_agent.name}, not "
                        f"{self.agent.name})"
                    )
                else:
                    stop_agent = self.agent
            else:
                stop_agent = self.agent

            agent_groups.setdefault(
                stop_agent.id, (stop_agent, [])
            )[1].append((ns, container_name))

        # Send batch reconcile to all agents in parallel
        # Each coroutine returns (stop_agent, node_list, response_or_error)
        async def _stop_on_agent(
            stop_agent: models.Host,
            node_list: list[tuple[models.NodeState, str]],
        ) -> tuple[models.Host, list[tuple[models.NodeState, str]], dict | Exception]:
            batch = [
                {"container_name": cn, "desired_state": "stopped"}
                for _, cn in node_list
            ]
            try:
                response = await agent_client.reconcile_nodes_on_agent(
                    stop_agent, self.lab.id, batch
                )
                return (stop_agent, node_list, response)
            except Exception as e:
                return (stop_agent, node_list, e)

        agent_results = await asyncio.gather(*[
            _stop_on_agent(stop_agent, node_list)
            for stop_agent, node_list in agent_groups.values()
        ])

        # Process results and collect fallback nodes
        fallback_nodes: list[tuple[models.NodeState, str]] = []

        for stop_agent, node_list, response_or_error in agent_results:
            if isinstance(response_or_error, AgentUnavailableError):
                error_msg = f"Agent unreachable (transient): {response_or_error.message}"
                for ns, container_name in node_list:
                    ns.error_message = error_msg
                    self.log_parts.append(
                        f"  {ns.node_name}: FAILED (transient) - {error_msg}"
                    )
                    logger.warning(
                        f"Stop {ns.node_name} in job {self.job.id} failed due "
                        f"to agent unavailability"
                    )
            elif isinstance(response_or_error, Exception):
                for ns, container_name in node_list:
                    ns.actual_state = NodeActualState.ERROR.value
                    ns.stopping_started_at = None
                    ns.error_message = str(response_or_error)
                    ns.boot_started_at = None
                    ns.is_ready = False
                    self.log_parts.append(f"  {ns.node_name}: FAILED - {response_or_error}")
                    self._broadcast_state(ns, name_suffix="error")
            else:
                results = response_or_error.get("results", [])
                results_by_name = {
                    r.get("container_name"): r for r in results
                }
                for ns, container_name in node_list:
                    result = results_by_name.get(container_name, {})
                    # Check for "not found" on non-default agent -> queue fallback
                    if (
                        not result.get("success")
                        and "not found" in result.get("error", "").lower()
                        and stop_agent.id != self.agent.id
                    ):
                        self.log_parts.append(
                            f"    Container not on {stop_agent.name}, "
                            f"will retry on {self.agent.name}..."
                        )
                        fallback_nodes.append((ns, container_name))
                        continue

                    self._apply_stop_result(ns, result, stop_agent)

        # Fallback: retry not-found nodes on default agent in one batch
        if fallback_nodes:
            fallback_batch = [
                {"container_name": cn, "desired_state": "stopped"}
                for _, cn in fallback_nodes
            ]
            try:
                response = await agent_client.reconcile_nodes_on_agent(
                    self.agent, self.lab.id, fallback_batch
                )
                results = response.get("results", [])
                results_by_name = {
                    r.get("container_name"): r for r in results
                }
                for ns, container_name in fallback_nodes:
                    result = results_by_name.get(container_name, {})
                    self._apply_stop_result(ns, result, self.agent)

            except AgentUnavailableError as e:
                error_msg = f"Agent unreachable (transient): {e.message}"
                for ns, container_name in fallback_nodes:
                    ns.error_message = error_msg
                    self.log_parts.append(
                        f"  {ns.node_name}: FAILED (transient) - {error_msg}"
                    )
                    logger.warning(
                        f"Stop {ns.node_name} in job {self.job.id} failed due "
                        f"to agent unavailability (fallback)"
                    )
            except Exception as e:
                for ns, container_name in fallback_nodes:
                    ns.actual_state = NodeActualState.ERROR.value
                    ns.stopping_started_at = None
                    ns.error_message = str(e)
                    ns.boot_started_at = None
                    ns.is_ready = False
                    self.log_parts.append(f"  {ns.node_name}: FAILED - {e}")
                    self._broadcast_state(ns, name_suffix="error")

        self.session.commit()

    def _apply_stop_result(
        self,
        ns: models.NodeState,
        result: dict,
        stop_agent: models.Host,
    ):
        """Apply a single stop result to a node state, broadcast, and log."""
        if result.get("success"):
            old_state = ns.actual_state
            ns.actual_state = NodeActualState.STOPPED.value
            ns.stopping_started_at = None
            ns.error_message = None
            ns.boot_started_at = None
            ns.is_ready = False
            self.log_parts.append(f"  {ns.node_name}: stopped")
            self._broadcast_state(ns, name_suffix="stopped")
            logger.info(
                "Node state transition",
                extra={
                    "event": "node_state_transition",
                    "lab_id": self.lab.id,
                    "node_id": ns.node_id,
                    "node_name": ns.node_name,
                    "old_state": old_state,
                    "new_state": "stopped",
                    "trigger": "agent_response",
                    "agent_id": stop_agent.id,
                    "job_id": self.job.id,
                },
            )
        else:
            old_state = ns.actual_state
            ns.actual_state = NodeActualState.ERROR.value
            ns.stopping_started_at = None
            ns.error_message = (
                result.get("error") or "Stop failed"
            )
            ns.boot_started_at = None
            ns.is_ready = False
            self.log_parts.append(
                f"  {ns.node_name}: FAILED - {ns.error_message}"
            )
            self._broadcast_state(ns, name_suffix="error")
            logger.info(
                "Node state transition",
                extra={
                    "event": "node_state_transition",
                    "lab_id": self.lab.id,
                    "node_id": ns.node_id,
                    "node_name": ns.node_name,
                    "old_state": old_state,
                    "new_state": "error",
                    "trigger": "agent_response",
                    "agent_id": stop_agent.id,
                    "job_id": self.job.id,
                    "error_message": ns.error_message,
                },
            )

    def _converge_stopped_desired_error_states(self) -> int:
        """Normalize desired=stopped nodes stuck in error to stopped.

        This is a safety guard: once the user intent is "stopped", stale error
        state should not block stop controls or keep retry/error UI flags.
        """
        normalized = 0
        for ns in self.node_states:
            if (
                ns.desired_state == NodeDesiredState.STOPPED.value
                and ns.actual_state == NodeActualState.ERROR.value
            ):
                ns.actual_state = NodeActualState.STOPPED.value
                ns.error_message = None
                ns.image_sync_status = None
                ns.image_sync_message = None
                ns.stopping_started_at = None
                ns.starting_started_at = None
                ns.boot_started_at = None
                ns.is_ready = False
                ns.reset_enforcement()
                normalized += 1
                self._broadcast_state(ns, name_suffix="stopped")
        return normalized

    async def _post_operation_cleanup(self):
        """Post-operation cleanup: create cross-host VXLAN links."""
        from app.tasks.jobs import _create_cross_host_links_if_ready

        await _create_cross_host_links_if_ready(
            self.session, self.lab.id, self.log_parts,
        )

    async def _reconcile_node_placement_statuses(self) -> None:
        """Align node_placements.status with final per-node outcomes.

        Prevent stale "starting" placement rows when deploy/start paths fail
        before a runtime object is actually created.
        """
        from app.tasks.jobs import _update_node_placements

        deployed_names: list[str] = []
        failed_names: list[str] = []

        for ns in self.node_states:
            if ns.actual_state == NodeActualState.RUNNING.value:
                deployed_names.append(ns.node_name)
                continue

            if ns.desired_state == NodeDesiredState.RUNNING.value and ns.actual_state in (
                NodeActualState.ERROR.value,
                NodeActualState.UNDEPLOYED.value,
                NodeActualState.STOPPED.value,
                NodeActualState.EXITED.value,
            ):
                failed_names.append(ns.node_name)

        if deployed_names:
            await _update_node_placements(
                self.session,
                self.lab.id,
                self.agent.id,
                deployed_names,
                status="deployed",
            )

        if failed_names:
            await _update_node_placements(
                self.session,
                self.lab.id,
                self.agent.id,
                failed_names,
                status="failed",
            )

    async def _finalize(self) -> LifecycleResult:
        """Finalize job status and broadcast result."""
        self._converge_stopped_desired_error_states()
        await self._reconcile_node_placement_statuses()
        error_count = sum(
            1
            for ns in self.node_states
            if ns.actual_state == NodeActualState.ERROR.value
        )

        if error_count > 0:
            self.job.status = JobStatus.FAILED.value
            self.log_parts.append(f"\nCompleted with {error_count} error(s)")
            await self._broadcast_job_progress(
                "failed",
                error_message=f"Node sync failed: {error_count} error(s)",
            )
        else:
            self.job.status = JobStatus.COMPLETED.value
            self.log_parts.append("\nAll nodes synced successfully")
            await self._broadcast_job_progress(
                "completed",
                progress_message="Node sync completed successfully",
            )
            # Clear enforcement counters for non-error nodes on success
            # so stale circuit breakers don't block future operations
            for ns in self.node_states:
                if ns.actual_state != NodeActualState.ERROR.value:
                    ns.reset_enforcement()

        self.job.completed_at = datetime.now(timezone.utc)
        self.job.log_path = "\n".join(self.log_parts)
        self.session.commit()
        duration = (
            (self.job.completed_at - self.job.started_at).total_seconds()
            if self.job.completed_at and self.job.started_at else None
        )
        if self.job.status == JobStatus.COMPLETED.value:
            record_job_completed(self.job.action, duration_seconds=duration or 0.0)
        elif self.job.status == JobStatus.FAILED.value:
            record_job_failed(
                self.job.action,
                duration_seconds=duration,
                failure_message=self.job.log_path,
            )

        logger.info(
            f"Job {self.job.id} completed with status: {self.job.status}"
        )
        return LifecycleResult(
            success=error_count == 0,
            error_count=error_count,
            log=self.log_parts,
        )

    # Readiness polling constants
    READINESS_POLL_INTERVAL = 5  # seconds
    READINESS_POLL_MAX_DURATION = 120  # seconds

    async def _wait_for_readiness(self, deployed_names: list[str]) -> None:
        """Actively poll readiness for newly deployed nodes.

        Polls every READINESS_POLL_INTERVAL seconds until all nodes report
        ready or READINESS_POLL_MAX_DURATION is reached. Remaining unready
        nodes are left for reconciliation to handle.
        """
        if not deployed_names:
            return

        # Get nodes that need readiness checking
        unready_nodes = [
            ns for ns in self.node_states
            if ns.node_name in deployed_names
            and ns.actual_state == NodeActualState.RUNNING.value
            and not ns.is_ready
        ]

        if not unready_nodes:
            return

        self.log_parts.append(f"  Waiting for {len(unready_nodes)} node(s) to become ready...")
        start_time = asyncio.get_event_loop().time()
        last_status_by_node: dict[str, tuple[str, str | None, str]] = {}
        last_status_log_elapsed: dict[str, float] = {}

        def _log_waiting_status(
            node_name: str,
            elapsed_seconds: float,
            message: str,
            progress_percent: int | None,
            details: str | None,
        ) -> None:
            """Log readiness wait status on change or at a low periodic rate."""
            normalized_message = (message or "not ready").strip()
            normalized_details = (details or "").strip()
            normalized_progress = (
                str(progress_percent) if progress_percent is not None else None
            )
            status_key = (
                normalized_message,
                normalized_progress,
                normalized_details,
            )
            previous = last_status_by_node.get(node_name)
            previous_elapsed = last_status_log_elapsed.get(node_name, -1e9)

            # Log immediately on state change; otherwise at most every 20s.
            if previous == status_key and (elapsed_seconds - previous_elapsed) < 20:
                return

            line = f"  {node_name}: waiting ({int(elapsed_seconds)}s) - {normalized_message}"
            if progress_percent is not None:
                line += f" [progress={progress_percent}%]"
            if normalized_details:
                line += f" | {normalized_details}"
            self.log_parts.append(line)
            last_status_by_node[node_name] = status_key
            last_status_log_elapsed[node_name] = elapsed_seconds

        while unready_nodes:
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed >= self.READINESS_POLL_MAX_DURATION:
                remaining = [ns.node_name for ns in unready_nodes]
                self.log_parts.append(
                    f"  Readiness timeout ({self.READINESS_POLL_MAX_DURATION}s): "
                    f"{len(remaining)} node(s) still not ready: {', '.join(remaining)}"
                )
                break

            await asyncio.sleep(self.READINESS_POLL_INTERVAL)

            still_unready = []
            for ns in unready_nodes:
                try:
                    # Resolve device kind and provider for readiness check
                    db_node = self.db_nodes_map.get(ns.node_name)
                    kind = (db_node.device if db_node else None)
                    image = resolve_node_image(
                        db_node.device, kind, db_node.image, db_node.version
                    ) if db_node else None
                    provider_type = get_image_provider(image) if image else None

                    result = await agent_client.check_node_readiness(
                        self.agent, self.lab.id, ns.node_name,
                        kind=kind, provider_type=provider_type,
                    )
                    if result.get("is_ready"):
                        ns.is_ready = True
                        self._broadcast_state(ns, name_suffix="ready")
                        self.log_parts.append(
                            f"  {ns.node_name}: ready ({int(elapsed)}s)"
                        )
                        last_status_by_node.pop(ns.node_name, None)
                        last_status_log_elapsed.pop(ns.node_name, None)
                    else:
                        still_unready.append(ns)
                        _log_waiting_status(
                            ns.node_name,
                            elapsed,
                            str(result.get("message", "not ready")),
                            result.get("progress_percent"),
                            result.get("details"),
                        )
                except Exception as e:
                    logger.debug(f"Readiness check failed for {ns.node_name}: {e}")
                    still_unready.append(ns)
                    _log_waiting_status(
                        ns.node_name,
                        elapsed,
                        f"Readiness check failed: {e}",
                        None,
                        None,
                    )

            unready_nodes = still_unready

        self.session.commit()

    def _handle_transient_failure(self, ns: models.NodeState, error: str) -> None:
        """Consistently handle transient agent failures (AgentUnavailableError).

        Sets node to pending state, clears transitional timestamps,
        records error message.
        """
        ns.actual_state = NodeActualState.PENDING.value
        ns.starting_started_at = None
        ns.stopping_started_at = None
        ns.error_message = error
        self.log_parts.append(f"  {ns.node_name}: FAILED (transient) - {error}")

    # ------------------------------------------------------------------ #
    #  Shared helpers                                                       #
    # ------------------------------------------------------------------ #

    def _filter_topology_for_agent(
        self, target_node_names: set[str]
    ) -> tuple:
        """Filter topology to include all nodes belonging to this agent.

        Shared logic used by both deploy and start phases. Uses batch-loaded
        maps to avoid N+1 queries (Phase 2.3).

        Returns (filtered_graph, deployed_node_names).
        """
        from app.topology import TopologyGraph

        if not self.graph:
            self.graph = self.topo_service.export_to_graph(self.lab.id)

        # Determine all nodes that should be on this agent
        all_agent_node_names = set()
        for n in self.graph.nodes:
            node_key = n.container_name or n.name
            db_node = self.db_nodes_map.get(node_key)
            if db_node and db_node.host_id:
                # Explicit host — include only if it matches this agent
                if db_node.host_id == self.agent.id:
                    all_agent_node_names.add(node_key)
                    logger.debug(
                        f"Job {self.job.id}: Added {node_key} "
                        f"(explicit host match: {db_node.host_id})"
                    )
                else:
                    logger.debug(
                        f"Job {self.job.id}: Skipped {node_key} "
                        f"(explicit host {db_node.host_id} != agent "
                        f"{self.agent.id})"
                    )
            else:
                # Auto-placed — include if in target set or has placement here
                if node_key in target_node_names:
                    all_agent_node_names.add(node_key)
                    logger.debug(
                        f"Job {self.job.id}: Added {node_key} "
                        f"(auto-placed, in target set)"
                    )
                else:
                    placement = self.placements_map.get(node_key)
                    if placement and placement.host_id == self.agent.id:
                        all_agent_node_names.add(node_key)
                        logger.debug(
                            f"Job {self.job.id}: Added {node_key} "
                            f"(existing placement on this agent)"
                        )

        # Include existing running/stopped nodes on this agent
        for node_name, ns in self.all_lab_states.items():
            if ns.actual_state in ("running", "stopped"):
                placement = self.placements_map.get(node_name)
                if placement:
                    if placement.host_id == self.agent.id:
                        all_agent_node_names.add(node_name)
                        logger.debug(
                            f"Job {self.job.id}: Added existing {node_name} "
                            f"(placement on this agent)"
                        )
                else:
                    if (
                        self.lab.agent_id == self.agent.id
                        or self.lab.agent_id is None
                    ):
                        all_agent_node_names.add(node_name)
                        logger.debug(
                            f"Job {self.job.id}: Added existing {node_name} "
                            f"(no placement, lab default agent match: "
                            f"lab.agent_id={self.lab.agent_id}, "
                            f"agent.id={self.agent.id})"
                        )

        # Filter graph nodes
        filtered_nodes = [
            n
            for n in self.graph.nodes
            if (n.container_name or n.name) in all_agent_node_names
        ]

        # Debug logging
        for n in self.graph.nodes:
            node_key = n.container_name or n.name
            is_included = node_key in all_agent_node_names
            logger.debug(
                f"Node filtering for {node_key}: included={is_included}, "
                f"agent={self.agent.id}, "
                f"all_agent_nodes={list(all_agent_node_names)}"
            )

        # Filter links (both endpoints must be included)
        filtered_node_names = {
            n.container_name or n.name for n in filtered_nodes
        }
        filtered_node_ids = {n.id for n in filtered_nodes}
        filtered_node_identifiers = filtered_node_names | filtered_node_ids
        filtered_links = [
            link
            for link in self.graph.links
            if all(
                ep.node in filtered_node_identifiers
                for ep in link.endpoints
            )
        ]

        # Set interface counts
        interface_count_map = self.topo_service.get_interface_count_map(
            self.lab.id
        )
        for n in filtered_nodes:
            node_key = n.container_name or n.name
            iface_count = interface_count_map.get(node_key, 0)
            if iface_count > 0:
                vars_dict = dict(n.vars or {})
                vars_dict["interface_count"] = iface_count
                n.vars = vars_dict

        filtered_graph = TopologyGraph(
            nodes=filtered_nodes,
            links=filtered_links,
            defaults=self.graph.defaults,
        )

        deployed_node_names = target_node_names & filtered_node_names
        return filtered_graph, deployed_node_names

    def _validate_topology_placement(self, filtered_graph) -> list[str]:
        """Verify all nodes in topology belong to this agent.

        Uses batch-loaded node map (Phase 2.3).
        Returns list of misplaced node descriptions, empty if all OK.
        """
        misplaced = []
        for n in filtered_graph.nodes:
            node_key = n.container_name or n.name
            db_node = self.db_nodes_map.get(node_key)
            if (
                db_node
                and db_node.host_id
                and db_node.host_id != self.agent.id
            ):
                misplaced.append(
                    f"{node_key} (assigned to {db_node.host_id})"
                )
        return misplaced

    def _broadcast_state(self, ns, name_suffix="state", **extra):
        """Fire-and-forget WebSocket broadcast of node state change."""
        # Include starting_started_at for frontend elapsed timer
        started_at = getattr(ns, "starting_started_at", None)
        if started_at and "starting_started_at" not in extra:
            extra["starting_started_at"] = started_at.isoformat()
        safe_create_task(
            broadcast_node_state_change(
                lab_id=self.lab.id,
                node_id=ns.node_id,
                node_name=ns.node_name,
                desired_state=ns.desired_state,
                actual_state=ns.actual_state,
                is_ready=ns.is_ready,
                error_message=ns.error_message,
                **extra,
            ),
            name=f"broadcast:{name_suffix}:{self.lab.id}:{ns.node_id}",
        )

    async def _broadcast_job_progress(
        self, status, progress_message=None, error_message=None
    ):
        """Fire-and-forget broadcast of job progress."""
        try:
            broadcaster = get_broadcaster()
            await broadcaster.publish_job_progress(
                lab_id=self.lab.id,
                job_id=self.job.id,
                action=self.job.action,
                status=status,
                progress_message=progress_message,
                error_message=error_message,
            )
        except Exception as e:
            logger.debug(f"Failed to broadcast job progress: {e}")

    def _refresh_placements(self):
        """Re-query placements after modifications."""
        all_placements = (
            self.session.query(models.NodePlacement)
            .filter(models.NodePlacement.lab_id == self.lab.id)
            .all()
        )
        self.placements_map = {p.node_name: p for p in all_placements}
