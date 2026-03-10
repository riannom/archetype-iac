"""Agent resolution mixin for NodeLifecycleManager.

Handles determining which agent(s) should handle each node:
- Explicit host assignments (Node.host_id)
- Sticky placements (NodePlacement affinity)
- Bin-packing across available agents
- Sub-job spawning for multi-agent deployments
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from app import agent_client, models
from app.agent_client import AgentUnavailableError
from app.config import settings
from app.utils.db import release_db_transaction_for_io as _release_db_tx_for_io
from app.state import (
    JobStatus,
    NodeActualState,
    NodeDesiredState,
)
from app.utils.async_tasks import safe_create_task
from app.utils.time import utcnow

logger = logging.getLogger(__name__)


class AgentResolutionMixin:
    """Mixin providing agent resolution methods for NodeLifecycleManager."""

    def _release_db_transaction_for_io(self, reason: str) -> None:
        """Close open DB transactions before long agent I/O waits.

        NodeLifecycleManager provides a richer override, but tests and some
        isolated call sites instantiate this mixin directly.
        """
        session = getattr(self, "session", None)
        if session is None:
            return
        _release_db_tx_for_io(
            session,
            context=reason,
            table="node_states",
            lab_id=getattr(getattr(self, "lab", None), "id", None),
            job_id=getattr(getattr(self, "job", None), "id", None),
        )

    async def _get_candidate_agents(self) -> list[models.Host]:
        """Return online agents that support the required provider."""
        from app.agent_client import get_agent_providers

        cutoff = utcnow() - timedelta(
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
        all_node_agents: dict[str, str] = {}  # node_name -> agent_id

        if not await self._resolve_explicit_placements(all_node_agents):
            return False

        if not await self._resolve_auto_placements(all_node_agents):
            return False

        nodes_without_agent = self._group_and_dispatch(all_node_agents)
        self._handle_unassigned_nodes(nodes_without_agent)

        return await self._resolve_final_agent()

    async def _resolve_explicit_placements(
        self, all_node_agents: dict[str, str],
    ) -> bool:
        """Resolve explicit host assignments (Node.host_id). Fail fast on errors."""
        explicit_placement_failures = []
        for ns in self.node_states:
            db_node = self._resolve_db_node(ns)
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
                    try:
                        self._release_db_transaction_for_io(
                            f"explicit placement ping for {ns.node_name}"
                        )
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

        if explicit_placement_failures:
            error_msg = (
                "Cannot deploy - explicit host assignments failed:\n"
                + "\n".join(explicit_placement_failures)
            )
            self.job.status = JobStatus.FAILED.value
            self.job.completed_at = utcnow()
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

        return True

    async def _resolve_auto_placements(
        self, all_node_agents: dict[str, str],
    ) -> bool:
        """Resolve sticky placements and bin-pack new nodes.

        Returns False if bin-packing fails (marks job FAILED).
        """
        auto_placed_nodes = [
            ns for ns in self.node_states if ns.node_name not in all_node_agents
        ]
        if not auto_placed_nodes:
            return True

        # Separate sticky (have placement) from truly new
        sticky_nodes = []
        new_nodes = []
        sticky_host_checks: dict[str, tuple[bool, str | None]] = {}
        invalidated_sticky = 0
        for ns in auto_placed_nodes:
            placement = self.placements_map.get(ns.node_name)
            if placement:
                if placement.status == "failed":
                    new_nodes.append(ns)
                    logger.info(
                        f"Skipping failed placement for {ns.node_name} "
                            f"on agent {placement.host_id}"
                    )
                else:
                    host_check = sticky_host_checks.get(placement.host_id)
                    if host_check is None:
                        host_agent = self.session.get(models.Host, placement.host_id)
                        if not host_agent:
                            host_check = (
                                False,
                                f"assigned host {placement.host_id} not found",
                            )
                        elif not agent_client.is_agent_online(host_agent):
                            host_check = (
                                False,
                                f"assigned host {host_agent.name} is offline",
                            )
                        else:
                            try:
                                self._release_db_transaction_for_io(
                                    f"sticky placement ping for {ns.node_name}"
                                )
                                await agent_client.ping_agent(host_agent)
                                host_check = (True, None)
                            except AgentUnavailableError:
                                host_check = (
                                    False,
                                    f"assigned host {host_agent.name} is unreachable",
                                )
                        sticky_host_checks[placement.host_id] = host_check

                    if host_check[0]:
                        all_node_agents[ns.node_name] = placement.host_id
                        sticky_nodes.append(ns.node_name)
                    else:
                        # Fail open for this node by evicting stale sticky placement
                        # so normal placement/fallback logic can re-home it.
                        placement.status = "failed"
                        invalidated_sticky += 1
                        new_nodes.append(ns)
                        logger.warning(
                            "Evicting sticky placement for %s on %s: %s",
                            ns.node_name,
                            placement.host_id,
                            host_check[1],
                        )
            else:
                new_nodes.append(ns)

        if invalidated_sticky:
            self.session.commit()
            logger.info(
                "Invalidated %s stale sticky placement(s) in lab %s",
                invalidated_sticky,
                self.lab.id,
            )

        if sticky_nodes:
            logger.debug(
                f"Job {self.job.id}: {len(sticky_nodes)} node(s) use "
                f"sticky placement: {sticky_nodes}"
            )

        # Distribute truly new nodes via bin-packing
        if new_nodes and settings.placement_scoring_enabled:
            result = await self._run_bin_pack_placement(
                all_node_agents, new_nodes, sticky_nodes,
            )
            if result is False:
                return False

        # Fallback for unassigned nodes (scoring disabled or no candidates)
        fallback_nodes = [
            ns for ns in new_nodes
            if ns.node_name not in all_node_agents
        ]
        if fallback_nodes:
            for ns in fallback_nodes:
                selected = await agent_client.get_agent_for_node(
                    self.session,
                    self.lab.id,
                    ns.node_name,
                    required_provider=self.provider,
                )
                if selected:
                    all_node_agents[ns.node_name] = selected.id

        return True

    async def _run_bin_pack_placement(
        self,
        all_node_agents: dict[str, str],
        new_nodes: list,
        sticky_nodes: list[str],
    ) -> bool | None:
        """Run bin-packing placement for new nodes. Returns False on failure."""
        from app.services.resource_capacity import (
            build_node_requirements,
            plan_placement,
            AgentBucket,
        )

        candidates = await self._get_candidate_agents()

        # Parallel-ping all candidate agents
        ping_tasks = [
            agent_client.ping_agent(cand) for cand in candidates
        ]
        self._release_db_transaction_for_io("bin-pack candidate ping")
        ping_results = await asyncio.gather(
            *ping_tasks, return_exceptions=True
        )
        reachable = [
            c for c, r in zip(candidates, ping_results)
            if not isinstance(r, Exception)
        ]
        for c, r in zip(candidates, ping_results):
            if isinstance(r, Exception):
                logger.warning(
                    f"Agent {c.name} heartbeat fresh but "
                    f"unreachable, skipping for placement"
                )

        # Query real-time capacity from each agent (parallel)
        cap_tasks = [
            agent_client.query_agent_capacity(c) for c in reachable
        ]
        self._release_db_transaction_for_io("bin-pack capacity query")
        cap_results = await asyncio.gather(
            *cap_tasks, return_exceptions=True
        )

        # Build agent buckets from real-time data
        agent_buckets: list[AgentBucket] = []
        for agent, cap in zip(reachable, cap_results):
            if (
                isinstance(cap, Exception)
                or not cap
                or "error" in cap
                or not cap.get("memory_total_gb")
            ):
                logger.warning(
                    f"Capacity query failed for {agent.name}: {cap}"
                )
                continue
            mem_total = cap.get("memory_total_gb", 0) * 1024
            allocated_mem = cap.get("allocated_memory_mb", 0)
            cpu_total = cap.get("cpu_count", 0)
            allocated_cpu = cap.get("allocated_vcpus", 0)
            bucket = AgentBucket(
                agent_id=agent.id,
                agent_name=agent.name or agent.id,
                memory_available_mb=max(0, mem_total - allocated_mem),
                cpu_available_cores=max(0, cpu_total - allocated_cpu),
                memory_total_mb=mem_total,
                cpu_total_cores=cpu_total,
            )
            agent_buckets.append(bucket)
            logger.debug(
                f"Job {self.job.id}: Agent {agent.id} ({agent.name}) "
                f"capacity: {bucket.memory_available_mb:.0f}MB / "
                f"{bucket.cpu_available_cores:.0f} vCPUs available"
            )

        if not agent_buckets:
            return None  # No capacity data — fall through to fallback

        # Build node requirements
        _DUMMY_NODE = type("_D", (), {"device": "linux"})()
        node_reqs = build_node_requirements([
            (
                ns.node_name,
                (
                    self.db_nodes_map.get(ns.node_name)
                    or _DUMMY_NODE
                ).device or "linux",
            )
            for ns in new_nodes
        ])

        # Pre-subtract sticky node requirements from buckets
        if sticky_nodes:
            sticky_reqs = build_node_requirements([
                (
                    name,
                    (
                        self.db_nodes_map.get(name)
                        or _DUMMY_NODE
                    ).device or "linux",
                )
                for name in sticky_nodes
            ])
            bucket_map = {
                b.agent_id: b for b in agent_buckets
            }
            overflow_names = []
            for sreq in sticky_reqs:
                agent_id = all_node_agents.get(sreq.node_name)
                bucket = bucket_map.get(agent_id) if agent_id else None
                if bucket and (
                    bucket.memory_available_mb >= sreq.memory_mb
                    and bucket.cpu_available_cores >= sreq.cpu_cores
                ):
                    bucket.memory_available_mb -= sreq.memory_mb
                    bucket.cpu_available_cores -= sreq.cpu_cores
                else:
                    overflow_names.append(sreq.node_name)
                    node_reqs.append(sreq)
            if overflow_names:
                for name in overflow_names:
                    all_node_agents.pop(name, None)
                logger.info(
                    f"Job {self.job.id}: {len(overflow_names)} "
                    f"sticky node(s) overflowed to bin-packer: "
                    f"{overflow_names}"
                )

        # Determine local agent for controller reserve
        local_id = None
        for b in agent_buckets:
            agent_obj = next(
                (a for a in reachable if a.id == b.agent_id),
                None,
            )
            if agent_obj and agent_obj.is_local:
                local_id = b.agent_id
                break

        # Run bin-packer
        placement = plan_placement(
            node_reqs,
            agent_buckets,
            controller_reserve_mb=settings.placement_controller_reserve_mb,
            local_agent_id=local_id,
        )

        if placement.unplaceable:
            error_msg = "\n".join(placement.errors)
            logger.error(
                f"Job {self.job.id}: Bin-packing failed: "
                f"{error_msg}"
            )
            self.job.status = JobStatus.FAILED.value
            self.job.completed_at = utcnow()
            self.job.log_path = f"ERROR: {error_msg}"
            for ns in self.node_states:
                if ns.node_name in placement.unplaceable:
                    ns.actual_state = NodeActualState.ERROR.value
                    ns.error_message = (
                        "Insufficient cluster resources"
                    )
                    self._broadcast_state(
                        ns, name_suffix="placement_error"
                    )
            self.session.commit()
            return False

        for node_name, agent_id in placement.assignments.items():
            all_node_agents[node_name] = agent_id

        for w in placement.warnings:
            logger.warning(
                f"Job {self.job.id}: Placement warning: {w}"
            )
            self.log_parts.append(f"WARNING: {w}")

        # Log spread summary
        counts: dict[str, int] = {}
        for aid in placement.assignments.values():
            counts[aid] = counts.get(aid, 0) + 1
        logger.info(
            f"Job {self.job.id}: Bin-pack placement for "
            f"{len(new_nodes)} new node(s): {counts}"
        )
        return None

    def _group_and_dispatch(
        self, all_node_agents: dict[str, str],
    ) -> list:
        """Group nodes by agent, set primary, spawn sub-jobs for others.

        Returns list of nodes without agent assignment.
        Mutates: self.target_agent_id, self.node_states.
        """
        nodes_by_agent: dict[str, list] = {}
        nodes_without_agent = []
        for ns in self.node_states:
            agent_id = all_node_agents.get(ns.node_name)
            if agent_id:
                nodes_by_agent.setdefault(agent_id, []).append(ns)
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

        return nodes_without_agent

    def _handle_unassigned_nodes(
        self, nodes_without_agent: list,
    ) -> None:
        """Handle nodes with no agent assignment."""
        if not nodes_without_agent:
            return

        if not self.node_states:
            # No other nodes with agents, try fallback logic
            self.node_states = nodes_without_agent
            return

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

    async def _resolve_final_agent(self) -> bool:
        """Resolve self.agent with multi-tier fallback.

        Returns True if agent found, False if none available (marks job FAILED).
        """
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
                if p and p.status != "failed":
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
            self.job.completed_at = utcnow()
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
