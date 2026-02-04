"""Libvirt provider for VM-based network labs.

This provider uses libvirt/QEMU to run virtual machine-based network devices
like Cisco IOS-XRv, FTDv, vManage, etc.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from textwrap import dedent
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.schemas import DeployTopology


from agent.config import settings
from agent.providers.base import (
    DeployResult,
    DestroyResult,
    NodeActionResult,
    NodeInfo,
    NodeStatus,
    Provider,
    StatusResult,
)
from agent.readiness import ReadinessResult, get_libvirt_probe, get_readiness_timeout
from agent.vendors import get_libvirt_config

logger = logging.getLogger(__name__)


def _log_name(node_name: str, node_config: dict) -> str:
    """Format node name for logging: 'DisplayName(id)' or just 'id'."""
    display_name = node_config.get("_display_name") if isinstance(node_config, dict) else None
    if display_name and display_name != node_name:
        return f"{display_name}({node_name})"
    return node_name


# Try to import libvirt - it's optional
try:
    import libvirt
    LIBVIRT_AVAILABLE = True
except ImportError:
    libvirt = None
    LIBVIRT_AVAILABLE = False


class LibvirtProvider(Provider):
    """Provider for libvirt/QEMU-based virtual machine labs.

    Uses libvirt API for VM lifecycle management and QEMU for
    disk overlay creation and console access.
    """

    def __init__(self):
        if not LIBVIRT_AVAILABLE:
            raise ImportError("libvirt-python package is not installed")
        self._conn: libvirt.virConnect | None = None
        self._uri = getattr(settings, 'libvirt_uri', 'qemu:///system')

    @property
    def name(self) -> str:
        return "libvirt"

    @property
    def display_name(self) -> str:
        return "Libvirt/QEMU"

    @property
    def capabilities(self) -> list[str]:
        return ["deploy", "destroy", "status", "node_actions", "console", "vm"]

    @property
    def conn(self) -> libvirt.virConnect:
        """Lazy-initialize libvirt connection."""
        if self._conn is None or not self._conn.isAlive():
            self._conn = libvirt.open(self._uri)
            if self._conn is None:
                raise RuntimeError(f"Failed to connect to libvirt at {self._uri}")
        return self._conn

    def _domain_name(self, lab_id: str, node_name: str) -> str:
        """Generate libvirt domain name for a node."""
        # Sanitize for valid domain name
        safe_lab_id = re.sub(r'[^a-zA-Z0-9_-]', '', lab_id)[:20]
        safe_node = re.sub(r'[^a-zA-Z0-9_-]', '', node_name)[:30]
        return f"arch-{safe_lab_id}-{safe_node}"

    def _lab_prefix(self, lab_id: str) -> str:
        """Get domain name prefix for a lab."""
        safe_lab_id = re.sub(r'[^a-zA-Z0-9_-]', '', lab_id)[:20]
        return f"arch-{safe_lab_id}"

    def _disks_dir(self, workspace: Path) -> Path:
        """Get directory for disk overlays."""
        disks = workspace / "disks"
        disks.mkdir(parents=True, exist_ok=True)
        return disks

    def _get_base_image(self, node_config: dict) -> str | None:
        """Get the base image path for a node.

        Looks up the image in the qcow2 store based on the node's image field.
        """
        image_ref = node_config.get("image")
        if not image_ref:
            return None

        # Check if it's an absolute path
        if image_ref.startswith("/"):
            if os.path.exists(image_ref):
                return image_ref
            return None

        # Look in qcow2 store
        qcow2_store = getattr(settings, 'qcow2_store_path', None)
        if not qcow2_store:
            # Fall back to workspace/images
            qcow2_store = Path(settings.workspace_path) / "images"

        # Try exact filename match
        image_path = Path(qcow2_store) / image_ref
        if image_path.exists():
            return str(image_path)

        # Try with .qcow2 extension
        if not image_ref.endswith(('.qcow2', '.qcow')):
            image_path = Path(qcow2_store) / f"{image_ref}.qcow2"
            if image_path.exists():
                return str(image_path)

        # Search for partial match
        store_path = Path(qcow2_store)
        if store_path.exists():
            for f in store_path.iterdir():
                if f.suffix in ('.qcow2', '.qcow') and image_ref.lower() in f.name.lower():
                    return str(f)

        return None

    def _translate_container_path_to_host(self, path: str) -> str:
        """Translate container path to host-accessible path for libvirt.

        When running in Docker, container mounts like /var/lib/archetype
        may not exist on the host. Libvirt runs on the host and needs
        the actual host path (typically the Docker volume mountpoint).

        Args:
            path: Path as seen from the container

        Returns:
            Path as accessible from the host
        """
        # Check if ARCHETYPE_HOST_IMAGE_PATH is set (explicit host path)
        host_image_path = os.environ.get("ARCHETYPE_HOST_IMAGE_PATH")
        if host_image_path:
            # Replace /var/lib/archetype/images with the host path
            if path.startswith("/var/lib/archetype/images/"):
                return path.replace("/var/lib/archetype/images", host_image_path)
            return path

        # Try to detect Docker volume mount point
        # Docker volumes are typically at /var/lib/docker/volumes/<name>/_data
        if path.startswith("/var/lib/archetype/"):
            # Try common Docker volume patterns
            volume_bases = [
                "/var/lib/docker/volumes/archetype-iac_archetype_workspaces/_data",
                "/var/lib/docker/volumes/archetype_workspaces/_data",
            ]
            for volume_base in volume_bases:
                test_path = path.replace("/var/lib/archetype", volume_base)
                if os.path.exists(test_path):
                    logger.debug(f"Translated path {path} -> {test_path}")
                    return test_path

        # Fallback: return original path
        return path

    def _create_overlay_disk_sync(
        self,
        base_image: str,
        overlay_path: Path,
    ) -> bool:
        """Create a qcow2 overlay disk backed by a base image (sync version).

        Args:
            base_image: Path to the base qcow2 image
            overlay_path: Path for the overlay disk

        Returns:
            True if successful
        """
        if overlay_path.exists():
            logger.info(f"Overlay disk already exists: {overlay_path}")
            return True

        # Translate the base image path to host-accessible path
        host_base_image = self._translate_container_path_to_host(base_image)
        if host_base_image != base_image:
            logger.info(f"Translated base image path: {base_image} -> {host_base_image}")

        cmd = [
            "qemu-img", "create",
            "-F", "qcow2",
            "-f", "qcow2",
            "-b", host_base_image,
            str(overlay_path),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.error(f"Failed to create overlay disk: {result.stderr}")
            return False

        logger.info(f"Created overlay disk: {overlay_path}")
        return True

    async def _create_overlay_disk(
        self,
        base_image: str,
        overlay_path: Path,
    ) -> bool:
        """Create a qcow2 overlay disk backed by a base image (async version).

        Wraps the sync version in asyncio.to_thread to avoid blocking.
        """
        return await asyncio.to_thread(
            self._create_overlay_disk_sync, base_image, overlay_path
        )

    def _create_data_volume_sync(
        self,
        path: Path,
        size_gb: int,
    ) -> bool:
        """Create an empty qcow2 data volume (sync version).

        Args:
            path: Path for the data volume
            size_gb: Size in gigabytes

        Returns:
            True if successful
        """
        if path.exists():
            logger.info(f"Data volume already exists: {path}")
            return True

        cmd = [
            "qemu-img", "create",
            "-f", "qcow2",
            str(path),
            f"{size_gb}G",
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.error(f"Failed to create data volume: {result.stderr}")
            return False

        logger.info(f"Created data volume: {path} ({size_gb}GB)")
        return True

    async def _create_data_volume(
        self,
        path: Path,
        size_gb: int,
    ) -> bool:
        """Create an empty qcow2 data volume (async version).

        Wraps the sync version in asyncio.to_thread to avoid blocking.
        """
        return await asyncio.to_thread(self._create_data_volume_sync, path, size_gb)

    def _generate_domain_xml(
        self,
        name: str,
        node_config: dict,
        overlay_path: Path,
        data_volume_path: Path | None = None,
        bridge_interfaces: list[str] | None = None,
    ) -> str:
        """Generate libvirt domain XML for a VM.

        Args:
            name: Domain name
            node_config: Node configuration from topology
            overlay_path: Path to the overlay disk
            data_volume_path: Optional path to data volume
            bridge_interfaces: List of bridge names for network interfaces

        Returns:
            Domain XML string
        """
        # Get resource requirements from node config
        memory_mb = node_config.get("memory", 2048)
        cpus = node_config.get("cpu", 1)

        # Get driver settings
        disk_driver = node_config.get("disk_driver", "virtio")
        nic_driver = node_config.get("nic_driver", "virtio")

        # Generate UUID for the domain
        domain_uuid = str(uuid.uuid4())

        # Build disk elements
        disks_xml = f'''
    <disk type='file' device='disk'>
      <driver name='qemu' type='qcow2'/>
      <source file='{overlay_path}'/>
      <target dev='vda' bus='{disk_driver}'/>
    </disk>'''

        if data_volume_path:
            disks_xml += f'''
    <disk type='file' device='disk'>
      <driver name='qemu' type='qcow2'/>
      <source file='{data_volume_path}'/>
      <target dev='vdb' bus='{disk_driver}'/>
    </disk>'''

        # Build network interface elements
        # VMs connect to the OVS bridge (arch-ovs) for networking
        # This integrates with the same networking as Docker containers
        ovs_bridge = getattr(settings, 'ovs_bridge_name', 'arch-ovs')
        interfaces_xml = ""
        if bridge_interfaces:
            for i, bridge in enumerate(bridge_interfaces):
                interfaces_xml += f'''
    <interface type='bridge'>
      <source bridge='{bridge}'/>
      <virtualport type='openvswitch'/>
      <model type='{nic_driver}'/>
    </interface>'''
        else:
            # Default: single interface connected to OVS bridge
            interfaces_xml = f'''
    <interface type='bridge'>
      <source bridge='{ovs_bridge}'/>
      <virtualport type='openvswitch'/>
      <model type='{nic_driver}'/>
    </interface>'''

        # Build the full domain XML
        xml = f'''<domain type='kvm'>
  <name>{name}</name>
  <uuid>{domain_uuid}</uuid>
  <memory unit='MiB'>{memory_mb}</memory>
  <vcpu>{cpus}</vcpu>
  <os>
    <type arch='x86_64' machine='pc-q35-6.2'>hvm</type>
    <boot dev='hd'/>
  </os>
  <features>
    <acpi/>
    <apic/>
  </features>
  <cpu mode='host-passthrough'/>
  <clock offset='utc'>
    <timer name='rtc' tickpolicy='catchup'/>
    <timer name='pit' tickpolicy='delay'/>
    <timer name='hpet' present='no'/>
  </clock>
  <devices>
    <emulator>/usr/bin/qemu-system-x86_64</emulator>
{disks_xml}
{interfaces_xml}
    <serial type='pty'>
      <target port='0'/>
    </serial>
    <console type='pty'>
      <target type='serial' port='0'/>
    </console>
    <graphics type='vnc' port='-1' autoport='yes' listen='127.0.0.1'>
      <listen type='address' address='127.0.0.1'/>
    </graphics>
    <video>
      <model type='cirrus'/>
    </video>
  </devices>
</domain>'''

        return xml

    def _get_domain_status(self, domain) -> NodeStatus:
        """Map libvirt domain state to NodeStatus."""
        state, _ = domain.state()
        state_map = {
            libvirt.VIR_DOMAIN_NOSTATE: NodeStatus.UNKNOWN,
            libvirt.VIR_DOMAIN_RUNNING: NodeStatus.RUNNING,
            libvirt.VIR_DOMAIN_BLOCKED: NodeStatus.RUNNING,
            libvirt.VIR_DOMAIN_PAUSED: NodeStatus.STOPPED,
            libvirt.VIR_DOMAIN_SHUTDOWN: NodeStatus.STOPPING,
            libvirt.VIR_DOMAIN_SHUTOFF: NodeStatus.STOPPED,
            libvirt.VIR_DOMAIN_CRASHED: NodeStatus.ERROR,
            libvirt.VIR_DOMAIN_PMSUSPENDED: NodeStatus.STOPPED,
        }
        return state_map.get(state, NodeStatus.UNKNOWN)

    def _node_from_domain(self, domain, prefix: str) -> NodeInfo | None:
        """Convert libvirt domain to NodeInfo."""
        name = domain.name()

        # Check if this domain belongs to our lab
        if not name.startswith(prefix + "-"):
            return None

        node_name = name[len(prefix) + 1:]

        return NodeInfo(
            name=node_name,
            status=self._get_domain_status(domain),
            container_id=domain.UUIDString()[:12],
        )

    async def deploy(
        self,
        lab_id: str,
        topology: "DeployTopology | None",
        workspace: Path,
    ) -> DeployResult:
        """Deploy a libvirt topology.

        Steps:
        1. Parse topology (JSON)
        2. Validate images exist
        3. Create overlay disks for each node
        4. Define and start VMs
        """
        workspace.mkdir(parents=True, exist_ok=True)
        if topology is None:
            return DeployResult(
                success=False,
                error="No topology provided (JSON required)",
            )

        if not topology.nodes:
            return DeployResult(
                success=False,
                error="No nodes found in topology",
            )

        # Filter to only nodes with libvirt-compatible images (qcow2, img)
        libvirt_nodes = []
        skipped_nodes = []
        for node in topology.nodes:
            image = node.image
            if image and (image.endswith(".qcow2") or image.endswith(".img")):
                libvirt_nodes.append(node)
            else:
                skipped_nodes.append(node.name)

        if skipped_nodes:
            logger.info(f"Skipping {len(skipped_nodes)} non-libvirt nodes: {skipped_nodes}")

        if not libvirt_nodes:
            logger.info(f"No libvirt-compatible nodes to deploy in lab {lab_id}")
            return DeployResult(
                success=True,
                stdout="No libvirt-compatible nodes to deploy",
            )

        logger.info(f"Deploying lab {lab_id} with {len(libvirt_nodes)} VMs via libvirt")

        disks_dir = self._disks_dir(workspace)
        deployed_nodes: list[NodeInfo] = []
        errors: list[str] = []

        for node in libvirt_nodes:
            node_name = node.name
            display_name = node.display_name or node_name
            log_name = f"{display_name}({node_name})" if display_name != node_name else node_name

            # Look up libvirt config from vendor registry
            libvirt_config = get_libvirt_config(node.kind)

            # Build node config dict for helper methods
            node_config = {
                "image": node.image,
                "memory": libvirt_config.memory_mb,
                "cpu": libvirt_config.cpu_count,
                "disk_driver": libvirt_config.disk_driver,
                "nic_driver": libvirt_config.nic_driver,
                "data_volume_gb": libvirt_config.data_volume_gb,
                "readiness_probe": libvirt_config.readiness_probe,
                "readiness_pattern": libvirt_config.readiness_pattern,
                "readiness_timeout": libvirt_config.readiness_timeout,
                "_display_name": display_name,
            }
            logger.info(
                f"VM config for {log_name}: {libvirt_config.memory_mb}MB RAM, "
                f"{libvirt_config.cpu_count} vCPU, disk={libvirt_config.disk_driver}, "
                f"nic={libvirt_config.nic_driver}"
            )

            try:
                node_info = await self._deploy_node(
                    lab_id,
                    node_name,
                    node_config,
                    disks_dir,
                )
                deployed_nodes.append(node_info)
                logger.info(f"Deployed VM {log_name}")
            except Exception as e:
                error_msg = f"Failed to deploy {log_name}: {e}"
                logger.error(error_msg)
                errors.append(error_msg)

        if not deployed_nodes and errors:
            return DeployResult(
                success=False,
                error=f"Failed to deploy any nodes: {errors[0]}",
                stderr="\n".join(errors),
            )

        stdout_lines = [f"Deployed {len(deployed_nodes)} VMs"]
        if errors:
            stdout_lines.append(f"Errors: {len(errors)}")

        return DeployResult(
            success=True,
            stdout="\n".join(stdout_lines),
            stderr="\n".join(errors) if errors else "",
            nodes=deployed_nodes,
        )

    async def _deploy_node(
        self,
        lab_id: str,
        node_name: str,
        node_config: dict,
        disks_dir: Path,
    ) -> NodeInfo:
        """Deploy a single VM node."""
        domain_name = self._domain_name(lab_id, node_name)

        # Check if domain already exists
        try:
            existing = self.conn.lookupByName(domain_name)
            if existing:
                state = self._get_domain_status(existing)
                if state == NodeStatus.RUNNING:
                    logger.info(f"Domain {domain_name} already running")
                    return NodeInfo(
                        name=node_name,
                        status=state,
                        container_id=existing.UUIDString()[:12],
                    )
                else:
                    # Start the existing domain
                    existing.create()
                    return NodeInfo(
                        name=node_name,
                        status=NodeStatus.RUNNING,
                        container_id=existing.UUIDString()[:12],
                    )
        except libvirt.libvirtError:
            pass  # Domain doesn't exist, we'll create it

        # Get base image
        base_image = self._get_base_image(node_config)
        if not base_image:
            raise ValueError(f"No base image found for node {node_name}")

        # Create overlay disk
        overlay_path = disks_dir / f"{node_name}.qcow2"
        if not await self._create_overlay_disk(base_image, overlay_path):
            raise RuntimeError(f"Failed to create overlay disk for {node_name}")

        # Check if data volume is needed
        data_volume_path = None
        data_volume_size = node_config.get("data_volume_gb")
        if data_volume_size:
            data_volume_path = disks_dir / f"{node_name}-data.qcow2"
            if not await self._create_data_volume(data_volume_path, data_volume_size):
                raise RuntimeError(f"Failed to create data volume for {node_name}")

        # Generate domain XML
        # TODO: Handle bridge interfaces from topology links
        xml = self._generate_domain_xml(
            domain_name,
            node_config,
            overlay_path,
            data_volume_path,
            bridge_interfaces=None,  # Will be populated from links
        )

        # Define and start the domain
        domain = self.conn.defineXML(xml)
        if not domain:
            raise RuntimeError(f"Failed to define domain {domain_name}")

        domain.create()
        logger.info(f"Started domain {domain_name}")

        return NodeInfo(
            name=node_name,
            status=NodeStatus.RUNNING,
            container_id=domain.UUIDString()[:12],
        )

    async def destroy(
        self,
        lab_id: str,
        workspace: Path,
    ) -> DestroyResult:
        """Destroy a libvirt topology."""
        prefix = self._lab_prefix(lab_id)
        destroyed_count = 0
        errors: list[str] = []

        try:
            # Get all domains (running and defined)
            running_domains = self.conn.listAllDomains(libvirt.VIR_CONNECT_LIST_DOMAINS_ACTIVE)
            defined_domains = self.conn.listAllDomains(libvirt.VIR_CONNECT_LIST_DOMAINS_INACTIVE)

            all_domains = running_domains + defined_domains

            for domain in all_domains:
                name = domain.name()
                if not name.startswith(prefix + "-"):
                    continue

                try:
                    # Stop if running
                    state, _ = domain.state()
                    if state == libvirt.VIR_DOMAIN_RUNNING:
                        domain.destroy()

                    # Undefine (remove from libvirt)
                    domain.undefine()
                    destroyed_count += 1
                    logger.info(f"Destroyed domain {name}")

                except libvirt.libvirtError as e:
                    logger.warning(f"Error destroying domain {name}: {e}")
                    errors.append(f"{name}: {e}")

            # Clean up disk overlays
            disks_dir = self._disks_dir(workspace)
            if disks_dir.exists():
                for disk_file in disks_dir.iterdir():
                    try:
                        disk_file.unlink()
                        logger.info(f"Removed disk: {disk_file}")
                    except Exception as e:
                        logger.warning(f"Failed to remove disk {disk_file}: {e}")

            if errors and destroyed_count == 0:
                return DestroyResult(
                    success=False,
                    error=f"Failed to destroy domains: {'; '.join(errors)}",
                )

            return DestroyResult(
                success=True,
                stdout=f"Destroyed {destroyed_count} VM domains",
                stderr="; ".join(errors) if errors else "",
            )

        except Exception as e:
            logger.exception(f"Destroy failed for lab {lab_id}: {e}")
            return DestroyResult(
                success=False,
                error=str(e),
            )

    async def status(
        self,
        lab_id: str,
        workspace: Path,
    ) -> StatusResult:
        """Get status of all VMs in a lab."""
        prefix = self._lab_prefix(lab_id)
        nodes: list[NodeInfo] = []

        try:
            # Get all domains
            all_domains = self.conn.listAllDomains(0)

            for domain in all_domains:
                node = self._node_from_domain(domain, prefix)
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
        """Start a specific VM."""
        domain_name = self._domain_name(lab_id, node_name)

        try:
            domain = self.conn.lookupByName(domain_name)
            state, _ = domain.state()

            if state == libvirt.VIR_DOMAIN_RUNNING:
                return NodeActionResult(
                    success=True,
                    node_name=node_name,
                    new_status=NodeStatus.RUNNING,
                    stdout="Domain already running",
                )

            domain.create()

            return NodeActionResult(
                success=True,
                node_name=node_name,
                new_status=NodeStatus.RUNNING,
                stdout=f"Started domain {domain_name}",
            )

        except libvirt.libvirtError as e:
            return NodeActionResult(
                success=False,
                node_name=node_name,
                error=f"Libvirt error: {e}",
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
        """Stop a specific VM."""
        domain_name = self._domain_name(lab_id, node_name)

        try:
            domain = self.conn.lookupByName(domain_name)
            state, _ = domain.state()

            if state != libvirt.VIR_DOMAIN_RUNNING:
                return NodeActionResult(
                    success=True,
                    node_name=node_name,
                    new_status=NodeStatus.STOPPED,
                    stdout="Domain already stopped",
                )

            # Graceful shutdown first
            domain.shutdown()

            # Wait for shutdown (up to 30 seconds)
            for _ in range(30):
                await asyncio.sleep(1)
                state, _ = domain.state()
                if state != libvirt.VIR_DOMAIN_RUNNING:
                    break
            else:
                # Force stop if graceful shutdown didn't work
                domain.destroy()

            return NodeActionResult(
                success=True,
                node_name=node_name,
                new_status=NodeStatus.STOPPED,
                stdout=f"Stopped domain {domain_name}",
            )

        except libvirt.libvirtError as e:
            return NodeActionResult(
                success=False,
                node_name=node_name,
                error=f"Libvirt error: {e}",
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
        """Get virsh console command for console access."""
        domain_name = self._domain_name(lab_id, node_name)

        try:
            domain = self.conn.lookupByName(domain_name)
            state, _ = domain.state()

            if state != libvirt.VIR_DOMAIN_RUNNING:
                return None

            # Return virsh console command
            # --force takes over console even if another session is connected
            return ["virsh", "-c", self._uri, "console", "--force", domain_name]

        except libvirt.libvirtError:
            return None
        except Exception:
            return None

    async def check_readiness(
        self,
        lab_id: str,
        node_name: str,
        kind: str,
    ) -> ReadinessResult:
        """Check if a VM has finished booting and is ready.

        This uses the serial console output to detect boot completion
        patterns defined in the vendor config.

        Args:
            lab_id: Lab identifier
            node_name: Node name within the lab
            kind: Device kind for vendor config lookup

        Returns:
            ReadinessResult with ready status and progress
        """
        domain_name = self._domain_name(lab_id, node_name)

        try:
            domain = self.conn.lookupByName(domain_name)
            state, _ = domain.state()

            if state != libvirt.VIR_DOMAIN_RUNNING:
                return ReadinessResult(
                    is_ready=False,
                    message=f"VM not running (state={state})",
                    progress_percent=0,
                )
        except libvirt.libvirtError:
            return ReadinessResult(
                is_ready=False,
                message="VM domain not found",
                progress_percent=0,
            )

        # Get the appropriate probe for this device type
        probe = get_libvirt_probe(kind, domain_name, self._uri)

        # Run the probe
        result = await probe.check(node_name)
        return result

    def get_readiness_timeout(self, kind: str) -> int:
        """Get the readiness timeout for a device type.

        Args:
            kind: Device kind

        Returns:
            Timeout in seconds
        """
        return get_readiness_timeout(kind)
