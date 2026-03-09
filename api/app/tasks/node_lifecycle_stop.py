"""Stop mixin for NodeLifecycleManager.

Handles stopping running containers/VMs:
- Auto-extract configs before stop (autosave snapshots)
- Batch stop via per-agent reconcile requests
- Fallback to default agent when container not found on expected host
- Normalize error→stopped for nodes with desired_state=stopped
"""
from __future__ import annotations

import asyncio
import logging

from app import agent_client, models
from app.agent_client import AgentUnavailableError
from app.config import settings
from app.state import (
    NodeActualState,
    NodeDesiredState,
)
from app.tasks.node_lifecycle import _get_container_name

logger = logging.getLogger(__name__)


class _ExtractionTimedOut(Exception):
    """Per-agent config extraction timeout sentinel."""


class StopMixin:
    """Mixin providing stop-related methods for NodeLifecycleManager."""

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

            # Call agents concurrently with per-agent timeout so partial
            # success is preserved when one agent is slow.
            extraction_timeout = max(
                0.1,
                float(getattr(settings, "auto_extract_on_stop_timeout_seconds", 30.0)),
            )

            async def _extract_for_agent(extract_agent: models.Host) -> dict | Exception:
                try:
                    return await asyncio.wait_for(
                        agent_client.extract_configs_on_agent(extract_agent, self.lab.id),
                        timeout=extraction_timeout,
                    )
                except asyncio.TimeoutError:
                    return _ExtractionTimedOut(
                        f"Config extraction timed out after {extraction_timeout}s "
                        f"for agent {extract_agent.id}"
                    )
                except Exception as exc:
                    return exc

            self._release_db_transaction_for_io(
                f"auto-extract before stop for lab {self.lab.id}"
            )
            results = await asyncio.gather(
                *[_extract_for_agent(a) for a, _ in agent_node_map.values()],
                return_exceptions=False,
            )

            # Collect configs (filter to only nodes being stopped)
            stop_node_names = {ns.node_name for ns in running_nodes}
            configs = []
            for (a, _node_names), result in zip(agent_node_map.values(), results):
                if isinstance(result, _ExtractionTimedOut):
                    logger.warning(str(result))
                    self.log_parts.append(
                        f"    Auto-extract timed out on {a.name or a.id} "
                        f"after {extraction_timeout}s (continuing)"
                    )
                    continue
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
        agent_groups: dict[str, tuple[models.Host, list[tuple[models.NodeState, str]]]] = {}
        for ns in nodes_need_stop:
            container_name = _get_container_name(self.lab.id, ns.node_name)
            self.log_parts.append(
                f"Stopping {ns.node_name} ({container_name})..."
            )

            # Use actual container location from placements
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
        async def _stop_on_agent(
            stop_agent: models.Host,
            node_list: list[tuple[models.NodeState, str]],
        ) -> tuple[models.Host, list[tuple[models.NodeState, str]], dict | Exception]:
            batch = [
                {"container_name": cn, "desired_state": "stopped"}
                for _, cn in node_list
            ]
            try:
                self._release_db_transaction_for_io(
                    f"stop batch on {stop_agent.name or stop_agent.id}"
                )
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
                self._release_db_transaction_for_io(
                    f"stop fallback batch on {self.agent.name or self.agent.id}"
                )
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
            # Keep stopping_started_at set — the agent acknowledged the stop
            # but VMs may still be in graceful shutdown (ACPI poweroff).
            # Reconciliation will clear it when it confirms the VM is stopped.
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
