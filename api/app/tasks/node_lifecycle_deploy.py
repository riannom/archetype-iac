"""Deployment mixin for NodeLifecycleManager.

Handles deploying and starting nodes:
- Topology-based deployment (full deploy via agent)
- Per-node deployment (create + start individual containers/VMs)
- Node start operations with retry logic
- Same-host link connection

Stop methods are in node_lifecycle_stop.py (StopMixin).
"""
from __future__ import annotations

import asyncio
import json
import logging

from app import agent_client, models
from app.agent_client import AgentUnavailableError
from app.config import settings
from app.image_store import find_image_by_reference, get_image_provider
from app.services.topology import graph_to_deploy_topology, resolve_node_image
from app.state import (
    NodeActualState,
    NodeDesiredState,
)
from app.storage import lab_workspace
from app.utils.time import utcnow

logger = logging.getLogger(__name__)

# Re-import module-level constants from main module to avoid duplication
from app.tasks.node_lifecycle import (  # noqa: E402
    _is_ceos_kind,
    _get_container_name,
    CEOS_STAGGER_SECONDS,
    DEPLOY_RETRY_ATTEMPTS,
    DEPLOY_RETRY_BACKOFF_SECONDS,
)


def acquire_deploy_lock(lab_id: str, node_names: list[str], agent_id: str, timeout: int = 300):
    from app.tasks.jobs import acquire_deploy_lock as _impl

    return _impl(lab_id, node_names, agent_id, timeout=timeout)


def release_deploy_lock(lab_id: str, node_names: list[str]) -> None:
    from app.tasks.jobs import release_deploy_lock as _impl

    _impl(lab_id, node_names)


async def _update_node_placements(session, lab_id: str, agent_id: str, node_names: list[str], status: str = "deployed"):
    from app.tasks.jobs import _update_node_placements as _impl

    await _impl(session, lab_id, agent_id, node_names, status=status)


async def _capture_node_ips(session, lab_id: str, agent):
    from app.tasks.jobs import _capture_node_ips as _impl

    await _impl(session, lab_id, agent)


async def _cleanup_orphan_containers(session, lab_id: str, new_agent_id: str, old_agent_ids: set[str], log_parts: list[str]):
    from app.tasks.jobs import _cleanup_orphan_containers as _impl

    await _impl(session, lab_id, new_agent_id, old_agent_ids, log_parts)


def get_device_service():
    from app.services.device_service import get_device_service as _impl

    return _impl()


class DeploymentMixin:
    """Mixin providing deploy/start/stop methods for NodeLifecycleManager."""

    async def _verify_runtime_identity_status(
        self,
        node_names: list[str] | set[str],
    ) -> dict[str, str]:
        """Verify newly started nodes are visible via agent status with identity fields."""
        requested_names = list(node_names)
        if not requested_names:
            return {}

        try:
            status_result = await agent_client.get_lab_status_from_agent(self.agent, self.lab.id)
        except Exception as exc:
            return {
                node_name: f"Status verification failed after start: {exc}"
                for node_name in requested_names
            }

        status_nodes = status_result.get("nodes", []) or []
        status_by_name = {
            node.get("name"): node
            for node in status_nodes
            if node.get("name")
        }

        failures: dict[str, str] = {}
        for node_name in requested_names:
            db_node = self.db_nodes_map.get(node_name)
            expected_runtime_name = (
                db_node.container_name if db_node and db_node.container_name else node_name
            )
            status_node = status_by_name.get(expected_runtime_name) or status_by_name.get(node_name)
            if not status_node:
                failures[node_name] = "Agent status missing node after start"
                continue
            if db_node and status_node.get("node_definition_id") != db_node.id:
                failures[node_name] = "Agent status missing expected node_definition_id after start"
                continue
            if not status_node.get("runtime_id"):
                failures[node_name] = "Agent status missing runtime_id after start"
        return failures

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
        self.log_parts.append("=== Phase 1: Deploy Topology ===")

        if not self.topo_service.has_nodes(self.lab.id):
            error_msg = "No topology defined in database"
            self.job.status = "failed"
            self.job.completed_at = utcnow()
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
            self.job.status = "failed"
            self.job.completed_at = utcnow()
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
            self.job.status = "failed"
            self.job.completed_at = utcnow()
            self.job.log_path = "\n".join(self.log_parts)
            self.session.commit()
            return

        try:
            self._release_db_transaction_for_io("topology deploy request")
            result = await agent_client.deploy_to_agent(
                self.agent,
                self.job.id,
                self.lab.id,
                topology=topology_json,
                provider=self.provider,
            )

            if result.get("status") == "completed":
                self.log_parts.append("Deploy completed successfully")
                identity_failures = await self._verify_runtime_identity_status(
                    list(deployed_node_names)
                )
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
                    and ns.node_name not in identity_failures
                ]

                # Mark deployed nodes as running
                for ns in all_states:
                    if ns.node_name in identity_failures:
                        ns.actual_state = NodeActualState.ERROR.value
                        ns.error_message = identity_failures[ns.node_name]
                        self.log_parts.append(
                            f"  {ns.node_name}: STATUS VERIFY FAILED - {ns.error_message}"
                        )
                    elif ns.node_name in deployed_node_names:
                        ns.actual_state = NodeActualState.RUNNING.value
                        ns.error_message = None
                        if not ns.boot_started_at:
                            ns.boot_started_at = utcnow()
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
            self.job.status = "failed"
            self.job.completed_at = utcnow()
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
            self.job.status = "failed"
            self.job.completed_at = utcnow()
            self.job.log_path = "\n".join(self.log_parts)
            self.session.commit()
            return

        try:
            self._release_db_transaction_for_io("topology redeploy request")
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
                            ns.boot_started_at = utcnow()
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
            self._release_db_transaction_for_io(
                f"create node {ns.node_name} on agent {self.agent.id}"
            )
            create_result = await agent_client.create_node_on_agent(
                self.agent,
                self.lab.id,
                ns.node_name,
                kind,
                node_definition_id=db_node.id,
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
            self._release_db_transaction_for_io(
                f"start node {ns.node_name} on agent {self.agent.id}"
            )
            start_result = await agent_client.start_node_on_agent(
                self.agent,
                self.lab.id,
                ns.node_name,
                provider=node_provider,
            )

            if start_result.get("success"):
                identity_failures = await self._verify_runtime_identity_status([ns.node_name])
                if identity_failures:
                    error_msg = identity_failures[ns.node_name]
                    ns.actual_state = NodeActualState.ERROR.value
                    ns.error_message = error_msg
                    self.log_parts.append(f"  {ns.node_name}: STATUS VERIFY FAILED - {error_msg}")
                    return None
                ns.actual_state = NodeActualState.RUNNING.value
                ns.error_message = None
                ns.boot_started_at = utcnow()
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
            self._release_db_transaction_for_io("parallel non-cEOS deployment")
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
                    ns.boot_started_at = utcnow()
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
