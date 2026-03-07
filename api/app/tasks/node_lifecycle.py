"""Node lifecycle management.

Orchestrates per-node lifecycle operations: deploy, start, stop, destroy.
Extracted from run_node_reconcile() in jobs.py for testability and maintainability.

Usage:
    manager = NodeLifecycleManager(session, lab, job, node_ids)
    result = await manager.execute()
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from app import agent_client, models
from app.metrics import (
    nlm_phase_duration,
    record_job_completed,
    record_job_failed,
    record_job_started,
)
from app.timing import AsyncTimedOperation
from app.config import settings
from app.services.broadcaster import broadcast_node_state_change
from app.services.broadcaster import get_broadcaster as _get_broadcaster
from app.services.state_machine import NodeStateMachine
from app.image_store import get_image_provider, load_manifest
from app.services.topology import TopologyService, resolve_node_image
from app.state import (
    HostStatus,
    JobStatus,
    NodeActualState,
    NodeDesiredState,
)
from app.utils.async_tasks import safe_create_task
from app.utils.job import broadcast_job_progress
from app.utils.lab import recompute_lab_state
from app.utils.time import utcnow

logger = logging.getLogger(__name__)
# Backward-compatible symbol for tests/patching call-sites that import directly.
get_broadcaster = _get_broadcaster


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
    from app.utils.naming import docker_container_name

    return docker_container_name(lab_id, node_name)


from app.tasks.node_lifecycle_agents import AgentResolutionMixin  # noqa: E402
from app.tasks.node_lifecycle_deploy import DeploymentMixin  # noqa: E402
from app.tasks.node_lifecycle_stop import StopMixin  # noqa: E402


class NodeLifecycleManager(AgentResolutionMixin, DeploymentMixin, StopMixin):
    """Per-node lifecycle orchestrator.

    Handles the lifecycle of individual nodes: deploy, start, stop, destroy.
    Each operation is independent at the node level. Per-node lifecycle uses
    Docker SDK + OVS for container and networking operations.

    Agent resolution methods are in node_lifecycle_agents.py (AgentResolutionMixin).
    Deploy/start methods are in node_lifecycle_deploy.py (DeploymentMixin).
    Stop methods are in node_lifecycle_stop.py (StopMixin).

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
        self.agent: models.Host | None = None
        self.target_agent_id: str | None = None

        # Topology graph — loaded once in _filter_topology_for_agent, reused
        self.graph = None
        self.post_operation_cleanup_failed = False

    # Known device types for bounded Prometheus labels.
    # Loaded dynamically from agent vendor registry; hardcoded fallback for
    # environments where the agent package is not available.
    try:
        from agent.vendors import VENDOR_CONFIGS as _VC
        _known_device_types: set[str] = set()
        for _key, _config in _VC.items():
            _normalized_key = (_key or "").strip().lower()
            if _normalized_key:
                _known_device_types.add(_normalized_key)

            _kind = (getattr(_config, "kind", None) or "").strip().lower()
            if _kind:
                _known_device_types.add(_kind)

            for _alias in (getattr(_config, "aliases", None) or []):
                _normalized_alias = (_alias or "").strip().lower()
                if _normalized_alias:
                    _known_device_types.add(_normalized_alias)

        # Keep legacy/common aliases recognized even when vendor keys shift to
        # canonical IDs (for example "nokia_srlinux" instead of "srlinux").
        _known_device_types.update({
            "ceos", "srlinux", "iosv", "iosvl2", "csr1000v", "cat8000v",
            "cat9000v", "xrv9k", "asav", "nxosv", "linux", "frr",
        })
        _KNOWN_DEVICE_TYPES = frozenset(_known_device_types)
        del _VC, _known_device_types
    except ImportError:
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

    def _release_db_transaction_for_io(self, reason: str) -> None:
        """Close any open transaction before long external I/O awaits.

        Sync jobs perform many DB reads/writes and then wait on agent/network calls.
        Closing the transaction boundary before waits prevents idle-in-transaction
        timeouts and reduces session invalidation cascades.
        """
        has_pending_writes = bool(
            self.session.new or self.session.dirty or self.session.deleted
        )
        try:
            if has_pending_writes:
                self.session.commit()
            else:
                self.session.rollback()
        except Exception as exc:
            logger.warning(
                "Sync job %s failed to release DB transaction before %s: %s",
                self.job.id,
                reason,
                exc,
            )
            try:
                self.session.rollback()
            except Exception:
                pass
            raise

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
        self._release_db_transaction_for_io("agent resolution")
        if not await self._resolve_agents():
            if self.job.status == JobStatus.FAILED.value:
                record_job_failed(self.job.action, failure_message=self.job.log_path)
            return LifecycleResult(success=False, log=self.log_parts)

        # Mark job running
        self.job.status = JobStatus.RUNNING.value
        self.job.agent_id = self.agent.id
        self.job.started_at = utcnow()
        self.session.commit()
        queue_wait = (
            (self.job.started_at - self.job.created_at).total_seconds()
            if self.job.started_at and self.job.created_at else None
        )
        record_job_started(self.job.action, queue_wait_seconds=queue_wait)

        await broadcast_job_progress(
            self.lab.id, self.job.id, self.job.action,
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
            self._release_db_transaction_for_io("migration detection")
            await self._handle_migration(nodes_to_start_or_deploy)

        # Phase: Image sync check
        _timing_extras = {"lab_id": str(self.lab.id), "job_id": str(self.job.id)}
        if nodes_to_start_or_deploy:
            self._release_db_transaction_for_io("image sync check")
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

        # Phase: Pre-deploy cleanup (remove stale network records)
        if nodes_need_deploy or nodes_need_start:
            self._release_db_transaction_for_io("pre-deploy cleanup")
            await self._pre_deploy_cleanup(nodes_need_deploy + nodes_need_start)

        # Phase: Deploy undeployed nodes
        if nodes_need_deploy:
            for device_type, grouped_nodes in self._group_nodes_by_device_type(nodes_need_deploy):
                self._release_db_transaction_for_io("deploy phase")
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
                self._release_db_transaction_for_io("start phase")
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
                self._release_db_transaction_for_io("stop phase")
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
            self._release_db_transaction_for_io("readiness polling")
            await self._wait_for_readiness(deployed_and_started)

        # Phase: Post-operation cleanup (cross-host links)
        self._release_db_transaction_for_io("post-operation cleanup")
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
            self.job.completed_at = utcnow()
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
            self.job.completed_at = utcnow()
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
                        ns.stopping_started_at = utcnow()
                    elif next_state == NodeActualState.STARTING:
                        ns.starting_started_at = utcnow()
            except ValueError:
                # Handle legacy state values
                if (
                    ns.desired_state == NodeDesiredState.STOPPED.value
                    and ns.actual_state == NodeActualState.RUNNING.value
                ):
                    ns.actual_state = NodeActualState.STOPPING.value
                    ns.stopping_started_at = utcnow()
                    ns.error_message = None
                elif ns.desired_state == NodeDesiredState.RUNNING.value and ns.actual_state in (
                    NodeActualState.STOPPED.value,
                    NodeActualState.ERROR.value,
                ):
                    ns.actual_state = NodeActualState.STARTING.value
                    ns.starting_started_at = utcnow()
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

    # --- Agent resolution methods are in node_lifecycle_agents.py ---

    async def _check_resources(self) -> bool:
        """Pre-deploy resource validation (safety net).

        MUST run BEFORE _handle_migration (Phase 2.2).
        Now acts as a safety net — the bin-packer already validated
        placement, so this only hard-fails on catastrophic mismatches
        (available < 50% of required) that indicate stale data.
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
            # Check if this is a catastrophic mismatch (< 50% of needed)
            # or just stale data from between placement and deploy
            is_catastrophic = (
                cap_result.required_memory_mb > 0
                and cap_result.available_memory_mb
                < cap_result.required_memory_mb * 0.5
            )
            if is_catastrophic:
                error_msg = format_capacity_error({self.agent.id: cap_result})
                logger.warning(
                    f"Job {self.job.id}: Resource check failed "
                    f"(catastrophic): {error_msg}"
                )
                self.job.status = JobStatus.FAILED.value
                self.job.completed_at = utcnow()
                self.job.log_path = f"ERROR: {error_msg}"
                for ns in deploy_candidates:
                    ns.actual_state = NodeActualState.ERROR.value
                    ns.error_message = (
                        "Insufficient resources on target agent"
                    )
                    self._broadcast_state(ns, name_suffix="resource_error")
                self.session.commit()
                return False
            else:
                # Stale data — log warning but proceed (bin-packer validated)
                warning_msg = format_capacity_error({self.agent.id: cap_result})
                logger.warning(
                    f"Job {self.job.id}: Resource check soft-fail "
                    f"(stale data likely, bin-packer approved): "
                    f"{warning_msg}"
                )
                self.log_parts.append(
                    "WARNING: Stale capacity data detected — "
                    "proceeding with bin-packer-approved placement"
                )

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
        from app.tasks.migration_cleanup import enqueue_node_migration_cleanup

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

            def _provider_for_node(node_name: str) -> str:
                node_provider = self.provider
                db_node = self.db_nodes_map.get(node_name)
                if db_node:
                    kind = db_node.device or "linux"
                    image = resolve_node_image(
                        db_node.device,
                        kind,
                        db_node.image,
                        db_node.version,
                    )
                    if image:
                        node_provider = get_image_provider(image)
                    else:
                        logger.warning(
                            f"Migration cleanup: cannot resolve image for {node_name} "
                            f"(device={db_node.device}), using lab provider '{self.provider}'"
                        )
                return node_provider

            for old_agent_id, node_names in old_agent_nodes.items():
                old_agent = self.session.get(models.Host, old_agent_id)
                if not old_agent:
                    self.log_parts.append(
                        f"  Old agent {old_agent_id} not found, removing stale placements"
                    )
                elif not agent_client.is_agent_online(old_agent):
                    self.log_parts.append(
                        f"  Old agent {old_agent.name} is offline, "
                        f"queued cleanup for {len(node_names)} node(s)"
                    )
                    for node_name in node_names:
                        enqueue_node_migration_cleanup(
                            self.session,
                            self.lab.id,
                            node_name,
                            old_agent.id,
                            provider=_provider_for_node(node_name),
                            reason="Old agent offline during migration",
                        )
                        self.log_parts.append(
                            f"    {node_name}: cleanup queued until {old_agent.name} is online"
                        )
                else:
                    self.log_parts.append(
                        f"  Destroying {len(node_names)} node(s) on "
                        f"{old_agent.name}..."
                    )

                    for node_name in node_names:
                        try:
                            self._release_db_transaction_for_io(
                                f"migration destroy {node_name} on {old_agent.name}"
                            )
                            result = await agent_client.destroy_node_on_agent(
                                old_agent,
                                self.lab.id,
                                node_name,
                                provider=_provider_for_node(node_name),
                            )
                            if result.get("success"):
                                self.log_parts.append(
                                    f"    {node_name}: destroyed on {old_agent.name}"
                                )
                            else:
                                error = result.get("error", "unknown")
                                enqueue_node_migration_cleanup(
                                    self.session,
                                    self.lab.id,
                                    node_name,
                                    old_agent.id,
                                    provider=_provider_for_node(node_name),
                                    reason=f"Initial cleanup failed: {error}",
                                )
                                self.log_parts.append(
                                    f"    {node_name}: cleanup queued after failure ({error})"
                                )
                        except Exception as e:
                            enqueue_node_migration_cleanup(
                                self.session,
                                self.lab.id,
                                node_name,
                                old_agent.id,
                                provider=_provider_for_node(node_name),
                                reason=f"Initial cleanup exception: {e}",
                            )
                            self.log_parts.append(
                                f"    {node_name}: cleanup queued after exception ({e})"
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
                            self._release_db_transaction_for_io(
                                f"migration stop probe {node_name} on {other_agent.name}"
                            )
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
        await broadcast_job_progress(
            self.lab.id, self.job.id, self.job.action,
            "running",
            progress_message=f"Checking images on {self.agent.name}...",
        )

        self._release_db_transaction_for_io("image sync coordination")
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
                ns.starting_started_at = utcnow()
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
            self.job.completed_at = utcnow()
            self.job.log_path = "\n".join(self.log_parts)
            self.session.commit()
            return None

        return nodes_need_deploy, nodes_need_start

    # --- Deploy/start/stop methods are in node_lifecycle_deploy.py ---

    async def _pre_deploy_cleanup(
        self, nodes_to_deploy_or_start: list[models.NodeState],
    ) -> None:
        """Remove stale network infrastructure before deploying new containers.

        New containers get fresh VLAN tags, but leftover VxlanTunnel and
        LinkState records (from a prior run that wasn't cleaned up on destroy)
        still reference the old tags.  Clearing them here prevents VLAN
        mismatches and guarantees _post_operation_cleanup() starts from a
        clean slate.
        """
        node_names = {ns.node_name for ns in nodes_to_deploy_or_start}
        lab_id = self.lab.id

        # 1. Find stale VxlanTunnel records for links involving these nodes
        stale_tunnels = (
            self.session.query(models.VxlanTunnel)
            .join(
                models.LinkState,
                models.VxlanTunnel.link_state_id == models.LinkState.id,
            )
            .filter(
                models.VxlanTunnel.lab_id == lab_id,
                models.LinkState.source_node.in_(node_names)
                | models.LinkState.target_node.in_(node_names),
            )
            .all()
        )

        if stale_tunnels:
            # Ask agents to clean OVS VXLAN ports for this lab
            agent_ids: set[str] = set()
            for t in stale_tunnels:
                agent_ids.add(t.agent_a_id)
                agent_ids.add(t.agent_b_id)

            agents = (
                self.session.query(models.Host)
                .filter(
                    models.Host.id.in_(agent_ids),
                    models.Host.status == "online",
                )
                .all()
            )
            for a in agents:
                try:
                    await agent_client.cleanup_overlay_on_agent(a, lab_id)
                except Exception as e:
                    logger.warning(
                        "Pre-deploy overlay cleanup on agent %s failed: %s",
                        a.name,
                        e,
                    )

            for t in stale_tunnels:
                self.session.delete(t)
            logger.info(
                "Pre-deploy cleanup: removed %d stale VxlanTunnel record(s) "
                "for lab %s",
                len(stale_tunnels),
                lab_id,
            )

        # 2. Delete stale LinkState records for links involving these nodes
        stale_link_states = (
            self.session.query(models.LinkState)
            .filter(
                models.LinkState.lab_id == lab_id,
                models.LinkState.source_node.in_(node_names)
                | models.LinkState.target_node.in_(node_names),
            )
            .all()
        )
        if stale_link_states:
            for ls in stale_link_states:
                self.session.delete(ls)
            logger.info(
                "Pre-deploy cleanup: removed %d stale LinkState record(s) "
                "for lab %s",
                len(stale_link_states),
                lab_id,
            )

        # 3. Delete stale InterfaceMapping records for nodes being redeployed
        #    (their OVS ports will be freshly assigned)
        deleted_mappings = 0
        node_def_ids = [
            n.id
            for n in self.session.query(models.Node)
            .filter(
                models.Node.lab_id == lab_id,
                models.Node.display_name.in_(node_names),
            )
            .all()
        ]
        if node_def_ids:
            deleted_mappings = (
                self.session.query(models.InterfaceMapping)
                .filter(
                    models.InterfaceMapping.lab_id == lab_id,
                    models.InterfaceMapping.node_id.in_(node_def_ids),
                )
                .delete(synchronize_session="fetch")
            )
            if deleted_mappings:
                logger.info(
                    "Pre-deploy cleanup: removed %d stale InterfaceMapping "
                    "record(s) for lab %s",
                    deleted_mappings,
                    lab_id,
                )

        if stale_tunnels or stale_link_states or deleted_mappings:
            self.session.commit()

    async def _post_operation_cleanup(self):
        """Post-operation cleanup: create cross-host VXLAN links and converge."""
        from app.tasks.jobs import _create_cross_host_links_if_ready
        from app.tasks.link_reconciliation import (
            reconcile_lab_links,
            refresh_interface_mappings,
            run_cross_host_port_convergence,
            run_overlay_convergence,
        )

        self._release_db_transaction_for_io("cross-host link creation")
        await _create_cross_host_links_if_ready(
            self.session, self.lab.id, self.log_parts,
        )
        try:
            self._release_db_transaction_for_io("post-op link reconciliation")
            reconcile_result = await reconcile_lab_links(self.session, self.lab.id)
            if reconcile_result["checked"] or reconcile_result["errors"]:
                self.log_parts.append(
                    "Post-op link reconciliation: "
                    f"checked={reconcile_result['checked']}, "
                    f"created={reconcile_result['created']}, "
                    f"repaired={reconcile_result['repaired']}, "
                    f"errors={reconcile_result['errors']}, "
                    f"skipped={reconcile_result['skipped']}"
                )
        except Exception as e:
            # Reconciliation can fail after a DB error (e.g. statement timeout).
            # Reset session state so finalize() can still persist job outcome.
            try:
                self.session.rollback()
            except Exception:
                pass
            self.post_operation_cleanup_failed = True
            logger.warning(f"Post-op link reconciliation failed for lab {self.lab.id}: {e}")
            self.log_parts.append(f"WARNING: Post-op link reconciliation failed: {e}")

        # Immediate per-lab convergence: push VLAN tags to overlay + container
        # ports so cross-host links work right away (no 60s wait).
        try:
            agents = (
                self.session.query(models.Host)
                .filter(models.Host.status == "online")
                .all()
            )
            host_to_agent = {a.id: a for a in agents}
            if host_to_agent:
                self._release_db_transaction_for_io("post-op convergence")
                await run_overlay_convergence(
                    self.session, host_to_agent, lab_id=self.lab.id,
                )
                await refresh_interface_mappings(
                    self.session, host_to_agent, lab_id=self.lab.id,
                )
                await run_cross_host_port_convergence(
                    self.session, host_to_agent, lab_id=self.lab.id,
                )
        except Exception as e:
            self.post_operation_cleanup_failed = True
            logger.warning(
                "Post-op convergence failed for lab %s: %s", self.lab.id, e,
            )
            self.log_parts.append(f"WARNING: Post-op convergence failed: {e}")

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
        if self.post_operation_cleanup_failed:
            error_count += 1

        if error_count > 0:
            self.job.status = JobStatus.FAILED.value
            self.log_parts.append(f"\nCompleted with {error_count} error(s)")
            if self.post_operation_cleanup_failed:
                self.log_parts.append(
                    "Post-operation state settlement failed; node/link state may be stale"
                )
            await broadcast_job_progress(
                self.lab.id, self.job.id, self.job.action,
                "failed",
                error_message=(
                    "Post-operation state settlement failed"
                    if self.post_operation_cleanup_failed and error_count == 1
                    else f"Node sync failed: {error_count} error(s)"
                ),
            )
        else:
            self.job.status = JobStatus.COMPLETED.value
            self.log_parts.append("\nAll nodes synced successfully")
            await broadcast_job_progress(
                self.lab.id, self.job.id, self.job.action,
                "completed",
                progress_message="Node sync completed successfully",
            )
            # Clear enforcement counters for non-error nodes on success
            # so stale circuit breakers don't block future operations
            for ns in self.node_states:
                if ns.actual_state != NodeActualState.ERROR.value:
                    ns.reset_enforcement()

        self.job.completed_at = utcnow()
        self.job.log_path = "\n".join(self.log_parts)
        recompute_lab_state(self.session, self.lab.id, commit=False)
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
    # Fallback timeout used when an agent readiness response does not include
    # a per-node timeout override.
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
        start_time = asyncio.get_running_loop().time()
        timeout_by_node: dict[str, int] = {
            ns.node_name: self.READINESS_POLL_MAX_DURATION for ns in unready_nodes
        }
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

        def _coerce_timeout(timeout_value: object) -> int | None:
            """Parse positive timeout values from agent responses."""
            if timeout_value is None:
                return None
            try:
                parsed = int(timeout_value)
            except (TypeError, ValueError):
                return None
            return parsed if parsed > 0 else None

        while unready_nodes:
            await asyncio.sleep(self.READINESS_POLL_INTERVAL)
            elapsed = asyncio.get_running_loop().time() - start_time

            still_unready = []
            timed_out_by_timeout: dict[int, list[str]] = {}
            for ns in unready_nodes:
                current_timeout = timeout_by_node.get(
                    ns.node_name,
                    self.READINESS_POLL_MAX_DURATION,
                )
                try:
                    # Resolve device kind and provider for readiness check
                    db_node = self.db_nodes_map.get(ns.node_name)
                    kind = (db_node.device if db_node else None)
                    image = resolve_node_image(
                        db_node.device, kind, db_node.image, db_node.version
                    ) if db_node else None
                    provider_type = get_image_provider(image) if image else None

                    self._release_db_transaction_for_io(
                        f"readiness probe for {ns.node_name}"
                    )
                    result = await agent_client.check_node_readiness(
                        self.agent, self.lab.id, ns.node_name,
                        kind=kind, provider_type=provider_type,
                    )
                    timeout_override = _coerce_timeout(result.get("timeout"))
                    if timeout_override is not None:
                        current_timeout = timeout_override
                        timeout_by_node[ns.node_name] = timeout_override

                    if result.get("is_ready"):
                        ns.is_ready = True
                        self._broadcast_state(ns, name_suffix="ready")
                        self.log_parts.append(
                            f"  {ns.node_name}: ready ({int(elapsed)}s)"
                        )
                        timeout_by_node.pop(ns.node_name, None)
                        last_status_by_node.pop(ns.node_name, None)
                        last_status_log_elapsed.pop(ns.node_name, None)
                    else:
                        _log_waiting_status(
                            ns.node_name,
                            elapsed,
                            str(result.get("message", "not ready")),
                            result.get("progress_percent"),
                            result.get("details"),
                        )
                        if elapsed >= current_timeout:
                            timed_out_by_timeout.setdefault(current_timeout, []).append(ns.node_name)
                            timeout_by_node.pop(ns.node_name, None)
                            last_status_by_node.pop(ns.node_name, None)
                            last_status_log_elapsed.pop(ns.node_name, None)
                        else:
                            still_unready.append(ns)
                except Exception as e:
                    logger.debug(f"Readiness check failed for {ns.node_name}: {e}")
                    _log_waiting_status(
                        ns.node_name,
                        elapsed,
                        f"Readiness check failed: {e}",
                        None,
                        None,
                    )
                    if elapsed >= current_timeout:
                        timed_out_by_timeout.setdefault(current_timeout, []).append(ns.node_name)
                        timeout_by_node.pop(ns.node_name, None)
                        last_status_by_node.pop(ns.node_name, None)
                        last_status_log_elapsed.pop(ns.node_name, None)
                    else:
                        still_unready.append(ns)

            for timeout_seconds, node_names in sorted(timed_out_by_timeout.items()):
                self.log_parts.append(
                    f"  Readiness timeout ({timeout_seconds}s): "
                    f"{len(node_names)} node(s) still not ready: {', '.join(node_names)}"
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

    def _refresh_placements(self):
        """Re-query placements after modifications."""
        all_placements = (
            self.session.query(models.NodePlacement)
            .filter(models.NodePlacement.lab_id == self.lab.id)
            .all()
        )
        self.placements_map = {p.node_name: p for p in all_placements}
