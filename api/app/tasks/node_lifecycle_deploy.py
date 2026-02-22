"""Deployment mixin for NodeLifecycleManager.

Handles deploying, starting, and stopping nodes:
- Topology-based deployment (full deploy via agent)
- Per-node deployment (create + start individual containers/VMs)
- Node start/stop operations with retry logic
- Config extraction before stop
- Same-host link connection
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
from app.tasks.node_lifecycle import (
    _is_ceos_kind,
    _get_container_name,
    CEOS_STAGGER_SECONDS,
    DEPLOY_RETRY_ATTEMPTS,
    DEPLOY_RETRY_BACKOFF_SECONDS,
)


class DeploymentMixin:
    """Mixin providing deploy/start/stop methods for NodeLifecycleManager."""

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
