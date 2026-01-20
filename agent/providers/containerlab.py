"""Containerlab provider for container-based network labs."""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

import docker
import yaml
from docker.errors import NotFound, APIError

from agent.providers.base import (
    DeployResult,
    DestroyResult,
    NodeActionResult,
    NodeInfo,
    NodeStatus,
    Provider,
    StatusResult,
)


logger = logging.getLogger(__name__)


class ContainerlabProvider(Provider):
    """Provider for containerlab-based network labs.

    Uses containerlab CLI for deploy/destroy and Docker SDK for
    status queries and node control (more reliable).
    """

    def __init__(self):
        self._docker: docker.DockerClient | None = None

    @property
    def name(self) -> str:
        return "containerlab"

    @property
    def docker(self) -> docker.DockerClient:
        """Lazy-initialize Docker client."""
        if self._docker is None:
            self._docker = docker.from_env()
        return self._docker

    def _lab_prefix(self, lab_id: str) -> str:
        """Get container name prefix for a lab.

        Containerlab names containers as: clab-{topology_name}-{node_name}
        We use lab_id as topology name for uniqueness.
        """
        # Sanitize lab_id for use in container names
        safe_id = re.sub(r'[^a-zA-Z0-9_-]', '', lab_id)[:20]
        return f"clab-{safe_id}"

    def _topology_path(self, workspace: Path) -> Path:
        """Get path to topology file in workspace."""
        return workspace / "topology.clab.yml"

    def _strip_aura_fields(self, topology_yaml: str) -> str:
        """Strip Aura-specific fields (like 'host') that containerlab doesn't understand.

        The 'host' field is used by Aura for multi-host placement but is not
        a valid containerlab field.

        Handles both formats:
        - GUI format: {nodes: {...}, links: [...]}
        - Containerlab format: {topology: {nodes: {...}, links: [...]}}
        """
        try:
            topo = yaml.safe_load(topology_yaml)
            if not topo:
                return topology_yaml

            # Strip 'host' field from nodes - handle both YAML formats
            # Format 1: nodes at top level (from GUI)
            nodes = topo.get("nodes", {})
            # Format 2: nodes under 'topology' key (containerlab native format)
            if not nodes:
                nodes = topo.get("topology", {}).get("nodes", {})

            if isinstance(nodes, dict):
                for node_name, node_config in nodes.items():
                    if isinstance(node_config, dict) and "host" in node_config:
                        del node_config["host"]

            return yaml.dump(topo, default_flow_style=False)
        except Exception as e:
            logger.warning(f"Failed to strip Aura fields from topology: {e}")
            return topology_yaml

    async def _run_clab(
        self,
        args: list[str],
        workspace: Path,
    ) -> tuple[int, str, str]:
        """Run containerlab command asynchronously."""
        cmd = ["clab"] + args

        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await process.communicate()
        return (
            process.returncode or 0,
            stdout.decode(errors="replace"),
            stderr.decode(errors="replace"),
        )

    def _get_container_status(self, container) -> NodeStatus:
        """Map Docker container status to NodeStatus."""
        status = container.status.lower()
        if status == "running":
            return NodeStatus.RUNNING
        elif status == "created":
            return NodeStatus.PENDING
        elif status in ("exited", "dead"):
            return NodeStatus.STOPPED
        elif status == "paused":
            return NodeStatus.STOPPED
        elif status == "restarting":
            return NodeStatus.STARTING
        else:
            return NodeStatus.UNKNOWN

    def _get_container_ips(self, container) -> list[str]:
        """Extract IP addresses from container."""
        ips = []
        try:
            networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})
            for net_name, net_info in networks.items():
                if net_info.get("IPAddress"):
                    ips.append(net_info["IPAddress"])
        except Exception:
            pass
        return ips

    def _node_from_container(self, container, prefix: str) -> NodeInfo | None:
        """Convert Docker container to NodeInfo."""
        name = container.name

        # Extract node name from container name (clab-{lab}-{node})
        if not name.startswith(prefix + "-"):
            return None

        node_name = name[len(prefix) + 1:]

        return NodeInfo(
            name=node_name,
            status=self._get_container_status(container),
            container_id=container.short_id,
            image=container.image.tags[0] if container.image.tags else str(container.image.id)[:12],
            ip_addresses=self._get_container_ips(container),
        )

    async def _cleanup_failed_deploy(self, lab_id: str, workspace: Path) -> None:
        """Clean up any containers created during a failed deployment."""
        prefix = self._lab_prefix(lab_id)
        logger.info(f"Cleaning up containers for failed deployment: {prefix}")

        try:
            containers = self.docker.containers.list(
                all=True,
                filters={"name": prefix},
            )

            for container in containers:
                try:
                    logger.info(f"Removing container: {container.name}")
                    container.remove(force=True)
                except Exception as e:
                    logger.warning(f"Failed to remove container {container.name}: {e}")

            # Also try containerlab destroy to clean up any networks/bridges
            topo_path = self._topology_path(workspace)
            if topo_path.exists():
                try:
                    await self._run_clab(
                        ["destroy", "-t", str(topo_path), "--cleanup"],
                        workspace,
                    )
                except Exception as e:
                    logger.warning(f"clab destroy during cleanup failed: {e}")

        except Exception as e:
            logger.error(f"Error during deployment cleanup: {e}")

    async def deploy(
        self,
        lab_id: str,
        topology_yaml: str,
        workspace: Path,
    ) -> DeployResult:
        """Deploy a containerlab topology.

        If deployment fails, automatically cleans up any partially created resources.
        """
        # Ensure workspace exists
        workspace.mkdir(parents=True, exist_ok=True)

        # Strip Aura-specific fields (like 'host') before writing
        clean_topology = self._strip_aura_fields(topology_yaml)

        # Write topology file
        topo_path = self._topology_path(workspace)
        topo_path.write_text(clean_topology, encoding="utf-8")

        logger.info(f"Deploying lab {lab_id} from {topo_path}")

        # Run containerlab deploy
        returncode, stdout, stderr = await self._run_clab(
            ["deploy", "-t", str(topo_path), "--reconfigure"],
            workspace,
        )

        if returncode != 0:
            logger.error(f"Deploy failed for lab {lab_id}: exit code {returncode}")

            # Clean up any partially created resources
            await self._cleanup_failed_deploy(lab_id, workspace)

            return DeployResult(
                success=False,
                stdout=stdout,
                stderr=stderr,
                error=f"containerlab deploy failed with exit code {returncode}",
            )

        # Get deployed node info
        status_result = await self.status(lab_id, workspace)

        # Verify all nodes are running
        failed_nodes = [n for n in status_result.nodes if n.status != NodeStatus.RUNNING]
        if failed_nodes:
            failed_names = ", ".join(n.name for n in failed_nodes)
            logger.warning(f"Some nodes not running after deploy: {failed_names}")
            # Don't fail the whole deploy, but include warning in output
            stdout += f"\n\nWarning: Some nodes may not be fully running: {failed_names}"

        logger.info(f"Deploy completed for lab {lab_id}: {len(status_result.nodes)} nodes")

        return DeployResult(
            success=True,
            nodes=status_result.nodes,
            stdout=stdout,
            stderr=stderr,
        )

    async def destroy(
        self,
        lab_id: str,
        workspace: Path,
    ) -> DestroyResult:
        """Destroy a containerlab topology."""
        topo_path = self._topology_path(workspace)

        if not topo_path.exists():
            # Try to destroy by prefix if topology file is missing
            prefix = self._lab_prefix(lab_id)
            try:
                containers = self.docker.containers.list(
                    all=True,
                    filters={"name": prefix},
                )
                for container in containers:
                    try:
                        container.remove(force=True)
                    except Exception:
                        pass
                return DestroyResult(
                    success=True,
                    stdout=f"Removed {len(containers)} containers by prefix",
                )
            except Exception as e:
                return DestroyResult(
                    success=False,
                    error=f"Failed to destroy by prefix: {e}",
                )

        # Run containerlab destroy
        returncode, stdout, stderr = await self._run_clab(
            ["destroy", "-t", str(topo_path), "--cleanup"],
            workspace,
        )

        if returncode != 0:
            return DestroyResult(
                success=False,
                stdout=stdout,
                stderr=stderr,
                error=f"containerlab destroy failed with exit code {returncode}",
            )

        return DestroyResult(
            success=True,
            stdout=stdout,
            stderr=stderr,
        )

    async def status(
        self,
        lab_id: str,
        workspace: Path,
    ) -> StatusResult:
        """Get status of all nodes using Docker SDK."""
        prefix = self._lab_prefix(lab_id)
        nodes: list[NodeInfo] = []

        try:
            # Find all containers with our lab prefix
            containers = self.docker.containers.list(
                all=True,
                filters={"name": prefix},
            )

            for container in containers:
                node = self._node_from_container(container, prefix)
                if node:
                    nodes.append(node)

            return StatusResult(
                lab_exists=len(nodes) > 0,
                nodes=nodes,
            )

        except Exception as e:
            return StatusResult(
                lab_exists=False,
                error=str(e),
            )

    async def start_node(
        self,
        lab_id: str,
        node_name: str,
        workspace: Path,
    ) -> NodeActionResult:
        """Start a specific node using Docker SDK."""
        prefix = self._lab_prefix(lab_id)
        container_name = f"{prefix}-{node_name}"

        try:
            container = self.docker.containers.get(container_name)
            container.start()

            # Wait briefly for container to start
            await asyncio.sleep(1)

            # Refresh container state
            container.reload()

            return NodeActionResult(
                success=True,
                node_name=node_name,
                new_status=self._get_container_status(container),
                stdout=f"Started container {container_name}",
            )

        except NotFound:
            return NodeActionResult(
                success=False,
                node_name=node_name,
                error=f"Container {container_name} not found",
            )
        except APIError as e:
            return NodeActionResult(
                success=False,
                node_name=node_name,
                error=f"Docker API error: {e}",
            )
        except Exception as e:
            return NodeActionResult(
                success=False,
                node_name=node_name,
                error=str(e),
            )

    async def stop_node(
        self,
        lab_id: str,
        node_name: str,
        workspace: Path,
    ) -> NodeActionResult:
        """Stop a specific node using Docker SDK."""
        prefix = self._lab_prefix(lab_id)
        container_name = f"{prefix}-{node_name}"

        try:
            container = self.docker.containers.get(container_name)
            container.stop(timeout=10)

            # Refresh container state
            container.reload()

            return NodeActionResult(
                success=True,
                node_name=node_name,
                new_status=self._get_container_status(container),
                stdout=f"Stopped container {container_name}",
            )

        except NotFound:
            return NodeActionResult(
                success=False,
                node_name=node_name,
                error=f"Container {container_name} not found",
            )
        except APIError as e:
            return NodeActionResult(
                success=False,
                node_name=node_name,
                error=f"Docker API error: {e}",
            )
        except Exception as e:
            return NodeActionResult(
                success=False,
                node_name=node_name,
                error=str(e),
            )

    async def get_console_command(
        self,
        lab_id: str,
        node_name: str,
        workspace: Path,
    ) -> list[str] | None:
        """Get docker exec command for console access."""
        prefix = self._lab_prefix(lab_id)
        container_name = f"{prefix}-{node_name}"

        try:
            container = self.docker.containers.get(container_name)
            if container.status != "running":
                return None

            # Return docker exec command
            # The actual PTY handling is done in the console module
            return ["docker", "exec", "-it", container_name, "/bin/sh"]

        except NotFound:
            return None
        except Exception:
            return None

    def get_container_name(self, lab_id: str, node_name: str) -> str:
        """Get the Docker container name for a node."""
        prefix = self._lab_prefix(lab_id)
        return f"{prefix}-{node_name}"

    async def discover_labs(self) -> dict[str, list[NodeInfo]]:
        """Discover all running containerlab labs and their nodes.

        Returns a dict mapping lab_id to list of NodeInfo for that lab.
        Used for reconciliation after agent/controller restart.
        """
        discovered: dict[str, list[NodeInfo]] = {}

        try:
            # Find all containerlab containers (they all start with "clab-")
            containers = self.docker.containers.list(
                all=True,
                filters={"name": "clab-"},
            )

            for container in containers:
                name = container.name
                # Parse container name: clab-{lab_id}-{node_name}
                if not name.startswith("clab-"):
                    continue

                parts = name[5:].split("-", 1)  # Remove "clab-" prefix
                if len(parts) < 2:
                    continue

                lab_id = parts[0]
                node_name = parts[1]

                node_info = NodeInfo(
                    name=node_name,
                    status=self._get_container_status(container),
                    container_id=container.short_id,
                    image=container.image.tags[0] if container.image.tags else str(container.image.id)[:12],
                    ip_addresses=self._get_container_ips(container),
                )

                if lab_id not in discovered:
                    discovered[lab_id] = []
                discovered[lab_id].append(node_info)

            logger.info(f"Discovered {len(discovered)} labs with running containers")

        except Exception as e:
            logger.error(f"Error discovering labs: {e}")

        return discovered

    async def cleanup_orphan_containers(self, valid_lab_ids: set[str]) -> list[str]:
        """Remove containers for labs that no longer exist in the database.

        Args:
            valid_lab_ids: Set of lab IDs that are valid (exist in DB)

        Returns:
            List of container names that were removed
        """
        removed: list[str] = []

        try:
            containers = self.docker.containers.list(
                all=True,
                filters={"name": "clab-"},
            )

            for container in containers:
                name = container.name
                if not name.startswith("clab-"):
                    continue

                parts = name[5:].split("-", 1)
                if len(parts) < 2:
                    continue

                lab_id = parts[0]

                if lab_id not in valid_lab_ids:
                    logger.info(f"Removing orphan container: {name}")
                    try:
                        container.remove(force=True)
                        removed.append(name)
                    except Exception as e:
                        logger.warning(f"Failed to remove orphan container {name}: {e}")

            if removed:
                logger.info(f"Cleaned up {len(removed)} orphan containers")

        except Exception as e:
            logger.error(f"Error cleaning up orphan containers: {e}")

        return removed
