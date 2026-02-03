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

        cmd = [
            "qemu-img", "create",
            "-F", "qcow2",
            "-f", "qcow2",
            "-b", base_image,
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
        interfaces_xml = ""
        if bridge_interfaces:
            for i, bridge in enumerate(bridge_interfaces):
                interfaces_xml += f'''
    <interface type='bridge'>
      <source bridge='{bridge}'/>
      <model type='{nic_driver}'/>
    </interface>'''
        else:
            # Default management network
            interfaces_xml = f'''
    <interface type='network'>
      <source network='default'/>
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

        Note: LibvirtProvider JSON topology support is not yet implemented.
        """
        workspace.mkdir(parents=True, exist_ok=True)
        if topology is None:
            return DeployResult(
                success=False,
                error="No topology provided (JSON required)",
            )

        return DeployResult(
            success=False,
            error="LibvirtProvider does not support JSON topology yet",
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
            return ["virsh", "-c", self._uri, "console", domain_name]

        except libvirt.libvirtError:
            return None
        except Exception:
            return None
