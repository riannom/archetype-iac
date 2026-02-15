"""Libvirt provider for VM-based network labs.

This provider uses libvirt/QEMU to run virtual machine-based network devices
like Cisco IOS-XRv, FTDv, vManage, etc.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import socket
import subprocess
import uuid
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape as xml_escape
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.schemas import DeployTopology


from agent.config import settings
from agent.providers.naming import libvirt_domain_name as _libvirt_name
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
from agent.vendors import (
    get_config_extraction_settings,
    get_console_credentials,
    get_console_method,
    get_libvirt_config,
)
from agent.network.ovs_vlan_tags import used_vlan_tags_on_bridge_from_ovs_outputs

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


# Inline Python script for TCP telnet console bridging.
# Connects stdin/stdout to a TCP telnet serial port with IAC negotiation.
# Spawned as a subprocess with PTY like virsh console — the existing
# _console_websocket_libvirt handler works without modification.
_TCP_TELNET_CONSOLE_SCRIPT = r'''
import sys, os, socket, select, struct, tty, termios

port = int(sys.argv[1])
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.settimeout(10)
try:
    sock.connect(("127.0.0.1", port))
except Exception as e:
    sys.stderr.write(f"Failed to connect to TCP serial port {port}: {e}\n")
    sys.exit(1)
sock.setblocking(False)

fd = sys.stdin.fileno()
old_settings = termios.tcgetattr(fd)
try:
    tty.setraw(fd)
    while True:
        readable, _, _ = select.select([sock, fd], [], [], 1.0)
        for r in readable:
            if r is sock:
                try:
                    data = sock.recv(4096)
                except (BlockingIOError, ConnectionError):
                    continue
                if not data:
                    sys.exit(0)
                # Strip telnet IAC sequences
                out = bytearray()
                i = 0
                while i < len(data):
                    if data[i] == 0xFF and i + 1 < len(data):
                        cmd = data[i + 1]
                        if cmd == 0xFF:
                            out.append(0xFF)
                            i += 2
                        elif cmd in (0xFB, 0xFC, 0xFD, 0xFE) and i + 2 < len(data):
                            # WILL/WONT/DO/DONT + option: reject
                            opt = data[i + 2]
                            if cmd == 0xFD:  # DO -> WONT
                                sock.sendall(bytes([0xFF, 0xFC, opt]))
                            elif cmd == 0xFB:  # WILL -> DONT
                                sock.sendall(bytes([0xFF, 0xFE, opt]))
                            i += 3
                        elif cmd in (0xF1, 0xF2, 0xF3, 0xF4, 0xF5, 0xF6, 0xF7, 0xF8, 0xF9, 0xFA):
                            if cmd == 0xFA:
                                # Skip subnegotiation until SE (0xF0)
                                i += 2
                                while i < len(data):
                                    if data[i] == 0xFF and i + 1 < len(data) and data[i + 1] == 0xF0:
                                        i += 2
                                        break
                                    i += 1
                            else:
                                i += 2
                        else:
                            i += 2
                    else:
                        out.append(data[i])
                        i += 1
                if out:
                    os.write(sys.stdout.fileno(), bytes(out))
            elif r is fd:
                data = sys.stdin.buffer.read1(4096)
                if not data:
                    sys.exit(0)
                try:
                    sock.sendall(data)
                except ConnectionError:
                    sys.exit(0)
finally:
    termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    sock.close()
'''


class LibvirtProvider(Provider):
    """Provider for libvirt/QEMU-based virtual machine labs.

    Uses libvirt API for VM lifecycle management and QEMU for
    disk overlay creation and console access.
    """

    # VLAN range for VM interfaces (separate from Docker's 100-2999 range)
    VLAN_RANGE_START = 2000
    VLAN_RANGE_END = 2999
    ALLOWED_DOMAIN_DRIVERS = {"kvm", "qemu"}

    # Whitelisted values for domain XML generation
    VALID_MACHINE_TYPES = {
        "pc", "q35",
        "pc-i440fx-2.9", "pc-q35-2.9",
        "pc-i440fx-4.2", "pc-q35-4.2",
        "pc-i440fx-6.2", "pc-q35-6.2",
        "pc-i440fx-8.2", "pc-q35-8.2",
        "pc-q35-9.0",
        "virt",
    }
    VALID_DISK_DRIVERS = {"virtio", "ide", "scsi", "sata"}
    VALID_NIC_DRIVERS = {
        "virtio", "e1000", "rtl8139", "i82551", "i82557b",
        "i82559er", "ne2k_pci", "pcnet",
    }
    # VMware-specific drivers unsupported by QEMU - auto-substitute
    NIC_DRIVER_SUBSTITUTIONS = {
        "vmxnet3": "virtio",
        "vmxnet2": "e1000",
        "vmxnet": "e1000",
    }

    def __init__(self):
        if not LIBVIRT_AVAILABLE:
            raise ImportError("libvirt-python package is not installed")
        self._conn: libvirt.virConnect | None = None
        self._uri = getattr(settings, 'libvirt_uri', 'qemu:///system')
        # Track VLAN allocations per lab: {lab_id: {node_name: [vlan_tags]}}
        self._vlan_allocations: dict[str, dict[str, list[int]]] = {}
        # Next VLAN to allocate per lab
        self._next_vlan: dict[str, int] = {}

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
        return _libvirt_name(lab_id, node_name)

    def _lab_prefix(self, lab_id: str) -> str:
        """Get domain name prefix for a lab."""
        safe_lab_id = re.sub(r'[^a-zA-Z0-9_-]', '', lab_id)[:20]
        return f"arch-{safe_lab_id}"

    def _undefine_domain(self, domain: libvirt.virDomain, domain_name: str) -> None:
        """Undefine a domain, cleaning up NVRAM when required."""
        try:
            domain.undefine()
            return
        except libvirt.libvirtError as e:
            flags = getattr(libvirt, "VIR_DOMAIN_UNDEFINE_NVRAM", None)
            if not flags:
                raise e
            logger.info(
                "Domain undefine fallback with NVRAM cleanup for %s: %s",
                domain_name,
                e,
            )
            domain.undefineFlags(flags)

    def _disks_dir(self, workspace: Path) -> Path:
        """Get directory for disk overlays."""
        disks = workspace / "disks"
        disks.mkdir(parents=True, exist_ok=True)
        return disks

    def _vlans_dir(self, workspace: Path) -> Path:
        """Get directory for VLAN allocation files."""
        vlans = workspace / "vlans"
        vlans.mkdir(parents=True, exist_ok=True)
        return vlans

    def _save_vlan_allocations(self, lab_id: str, workspace: Path) -> None:
        """Persist VLAN allocations to file for recovery after agent restart.

        Saves the current VLAN allocations for a lab to a JSON file.
        This enables recovery of network state when the agent restarts
        or when a lab is redeployed.

        Args:
            lab_id: Lab identifier
            workspace: Lab workspace path
        """
        allocations = self._vlan_allocations.get(lab_id, {})
        next_vlan = self._next_vlan.get(lab_id, self.VLAN_RANGE_START)

        vlan_data = {
            "allocations": allocations,
            "next_vlan": next_vlan,
        }

        vlans_dir = self._vlans_dir(workspace)
        vlan_file = vlans_dir / f"{lab_id}.json"

        try:
            with open(vlan_file, "w") as f:
                json.dump(vlan_data, f, indent=2)
            logger.debug(f"Saved VLAN allocations for lab {lab_id} to {vlan_file}")
        except Exception as e:
            logger.warning(f"Failed to save VLAN allocations for lab {lab_id}: {e}")

    def _get_used_vlan_tags_on_ovs_bridge(self) -> set[int]:
        """Return VLAN tags currently in use on the OVS bridge.

        VLAN tags on OVS are global to the bridge, not per-lab. If we reuse a VLAN tag
        already assigned to another port, we accidentally connect two unrelated L2
        segments, which can create loops and MAC flapping.
        """
        try:
            ovs_bridge = getattr(settings, "ovs_bridge_name", "arch-ovs")

            ports_res = subprocess.run(
                ["ovs-vsctl", "list-ports", ovs_bridge],
                capture_output=True,
                text=True,
            )
            if ports_res.returncode != 0:
                return set()

            csv_res = subprocess.run(
                ["ovs-vsctl", "--format=csv", "--columns=name,tag", "list", "port"],
                capture_output=True,
                text=True,
            )
            if csv_res.returncode != 0:
                return set()

            return used_vlan_tags_on_bridge_from_ovs_outputs(
                bridge_list_ports_output=ports_res.stdout,
                list_port_name_tag_csv=csv_res.stdout,
            )
        except Exception:
            # Non-fatal: older environments may not have OVS CLI access.
            return set()

    @staticmethod
    def _extract_domain_vlan_tags(domain) -> list[int]:
        """Extract VLAN tag ids from a libvirt domain XML (data interfaces only)."""
        try:
            xml = domain.XMLDesc(0)
            root = ET.fromstring(xml)
            tags: list[int] = []
            # Only consider bridge interfaces (data plane). Mgmt NIC is type='network'.
            for iface in root.findall(".//devices/interface[@type='bridge']"):
                for tag in iface.findall("./vlan/tag"):
                    raw = tag.get("id")
                    if not raw:
                        continue
                    try:
                        tags.append(int(raw))
                    except ValueError:
                        continue
            return tags
        except Exception:
            return []

    def _discover_vlan_allocations_from_domains(
        self,
        lab_id: str,
    ) -> dict[str, list[int]]:
        """Discover per-node VLAN tags from currently defined libvirt domains.

        This is a safety net for cases where the agent lost its in-memory VLAN state
        (restart) or the persisted JSON is missing/incomplete.
        """
        discovered: dict[str, list[int]] = {}
        try:
            prefix = self._lab_prefix(lab_id)
            for domain in self.conn.listAllDomains(0):
                try:
                    name = domain.name()
                except Exception:
                    continue
                if not name.startswith(prefix + "-"):
                    continue
                node_name = name[len(prefix) + 1 :]
                tags = self._extract_domain_vlan_tags(domain)
                if tags:
                    discovered[node_name] = tags
        except Exception:
            return {}
        return discovered

    def _load_vlan_allocations(self, lab_id: str, workspace: Path) -> bool:
        """Load VLAN allocations from file.

        Restores VLAN allocation state from a previously saved JSON file.
        Used during stale network recovery to restore state after agent restart.

        Args:
            lab_id: Lab identifier
            workspace: Lab workspace path

        Returns:
            True if allocations were loaded, False if file doesn't exist or load failed
        """
        vlans_dir = self._vlans_dir(workspace)
        vlan_file = vlans_dir / f"{lab_id}.json"

        if not vlan_file.exists():
            return False

        try:
            with open(vlan_file) as f:
                vlan_data = json.load(f)

            allocations = vlan_data.get("allocations", {})
            next_vlan = vlan_data.get("next_vlan", self.VLAN_RANGE_START)

            self._vlan_allocations[lab_id] = allocations
            self._next_vlan[lab_id] = next_vlan

            logger.info(
                f"Loaded VLAN allocations for lab {lab_id}: "
                f"{len(allocations)} nodes, next_vlan={next_vlan}"
            )
            return True

        except Exception as e:
            logger.warning(f"Failed to load VLAN allocations for lab {lab_id}: {e}")
            return False

    def _remove_vlan_file(self, lab_id: str, workspace: Path) -> None:
        """Remove VLAN allocation file for a lab.

        Called during destroy to clean up the VLAN file when a lab is removed.

        Args:
            lab_id: Lab identifier
            workspace: Lab workspace path
        """
        vlans_dir = self._vlans_dir(workspace)
        vlan_file = vlans_dir / f"{lab_id}.json"

        if vlan_file.exists():
            try:
                vlan_file.unlink()
                logger.debug(f"Removed VLAN file for lab {lab_id}")
            except Exception as e:
                logger.warning(f"Failed to remove VLAN file for lab {lab_id}: {e}")

    def _ovs_port_exists(self, port_name: str) -> bool:
        """Check if an OVS port exists on the bridge.

        Args:
            port_name: Name of the OVS port (e.g., vnet0)

        Returns:
            True if the port exists, False otherwise
        """
        try:
            from agent.network.backends.registry import get_network_backend

            backend = get_network_backend()
            return backend.check_port_exists(port_name)
        except Exception:
            pass

        try:
            result = subprocess.run(
                ["ovs-vsctl", "port-to-br", port_name],
                capture_output=True,
                text=True,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _recover_stale_network(self, lab_id: str, workspace: Path) -> dict[str, list[int]]:
        """Recover network state for a lab being redeployed.

        This method attempts to restore VLAN allocations from a previous
        deployment. When the agent restarts or a lab is redeployed, the
        in-memory VLAN allocations are lost. This method:

        1. Loads VLAN allocations from the persisted JSON file
        2. Validates that the allocations are still usable
        3. Returns the recovered allocations for reuse

        The recovered allocations can be used to avoid reallocating VLANs
        for nodes that already have working network connectivity.

        Args:
            lab_id: Lab identifier
            workspace: Lab workspace path

        Returns:
            Dict mapping node_name -> list of VLAN tags for recovered nodes.
            Empty dict if no recovery was possible.
        """
        recovered: dict[str, list[int]] = {}

        # Load persisted allocations when available, but also discover from live domains.
        self._load_vlan_allocations(lab_id, workspace)

        allocations = self._vlan_allocations.get(lab_id, {})
        discovered = self._discover_vlan_allocations_from_domains(lab_id)
        if discovered:
            # Domain XML is source-of-truth for currently running/defined nodes.
            allocations = {**allocations, **discovered}
            self._vlan_allocations[lab_id] = allocations

        # Check which allocations have valid domains still defined
        # (The OVS ports are created by libvirt when VMs are defined/started)
        try:
            all_domains = self.conn.listAllDomains(0)
            existing_nodes = set()
            prefix = self._lab_prefix(lab_id)

            for domain in all_domains:
                name = domain.name()
                if name.startswith(prefix + "-"):
                    node_name = name[len(prefix) + 1:]
                    existing_nodes.add(node_name)

            # Keep allocations for nodes that still have domains
            for node_name, vlans in allocations.items():
                if node_name in existing_nodes:
                    recovered[node_name] = vlans
                    logger.info(
                        f"Recovered VLAN allocation for {node_name}: {vlans}"
                    )
                else:
                    logger.debug(
                        f"Discarding stale VLAN allocation for {node_name} "
                        "(domain no longer exists)"
                    )

            # Update in-memory state to only keep valid allocations
            self._vlan_allocations[lab_id] = recovered

            if recovered:
                logger.info(
                    f"Recovered network state for lab {lab_id}: "
                    f"{len(recovered)} nodes with valid VLAN allocations"
                )
                # Re-save the cleaned allocations
                self._save_vlan_allocations(lab_id, workspace)

        except Exception as e:
            logger.warning(f"Error during stale network recovery for lab {lab_id}: {e}")
            return {}

        return recovered

    def _allocate_vlans(
        self,
        lab_id: str,
        node_name: str,
        count: int,
        workspace: Path | None = None,
    ) -> list[int]:
        """Allocate VLAN tags for a VM's interfaces.

        Each interface gets a unique VLAN tag for isolation on the OVS bridge.
        This mirrors how Docker containers get isolated VLANs.

        If the node already has VLAN allocations (e.g., from stale network
        recovery), those are returned instead of allocating new VLANs.

        Args:
            lab_id: Lab identifier
            node_name: Node name
            count: Number of VLANs to allocate
            workspace: Lab workspace path for persisting allocations

        Returns:
            List of VLAN tags
        """
        if lab_id not in self._next_vlan:
            self._next_vlan[lab_id] = self.VLAN_RANGE_START
        if lab_id not in self._vlan_allocations:
            self._vlan_allocations[lab_id] = {}

        # Always avoid VLAN collisions with existing OVS ports (across all labs).
        used_on_bridge = self._get_used_vlan_tags_on_ovs_bridge()

        # Check if this node already has VLANs allocated (from recovery)
        existing_vlans = self._vlan_allocations[lab_id].get(node_name)
        if existing_vlans:
            # Verify the allocation has enough VLANs for the requested count
            if len(existing_vlans) >= count:
                logger.debug(
                    f"Using recovered VLANs for {node_name}: {existing_vlans[:count]}"
                )
                return existing_vlans[:count]
            else:
                # Need more VLANs than recovered - clear and reallocate
                logger.debug(
                    f"Recovered VLANs insufficient for {node_name} "
                    f"(have {len(existing_vlans)}, need {count}), reallocating"
                )

        vlans = []
        # Cap search to range size to avoid infinite loops if exhausted.
        range_size = (self.VLAN_RANGE_END - self.VLAN_RANGE_START) + 1
        for _ in range(count):
            attempts = 0
            vlan = self._next_vlan[lab_id]
            while attempts < range_size and (vlan in used_on_bridge or vlan in vlans):
                vlan += 1
                if vlan > self.VLAN_RANGE_END:
                    vlan = self.VLAN_RANGE_START
                attempts += 1
            if attempts >= range_size:
                raise RuntimeError("No free VLAN tags available on OVS bridge")
            vlans.append(vlan)
            used_on_bridge.add(vlan)
            vlan += 1
            if vlan > self.VLAN_RANGE_END:
                vlan = self.VLAN_RANGE_START
            self._next_vlan[lab_id] = vlan

        self._vlan_allocations[lab_id][node_name] = vlans
        logger.debug(f"Allocated VLANs for {node_name}: {vlans}")

        # Persist allocations to file for recovery
        if workspace:
            self._save_vlan_allocations(lab_id, workspace)

        return vlans

    async def _set_vm_tap_mtu(self, lab_id: str, node_name: str) -> None:
        """Set MTU on all tap devices for a running VM.

        VM tap devices inherit the OVS bridge MTU (often 1450 from VXLAN ports),
        which blocks standard 1500-byte frames. This sets them to local_mtu.
        """
        if settings.local_mtu <= 0:
            return

        vlans = self.get_node_vlans(lab_id, node_name)
        for i in range(len(vlans)):
            port_name = await self.get_vm_interface_port(lab_id, node_name, i)
            if port_name:
                try:
                    subprocess.run(
                        ["ip", "link", "set", port_name, "mtu", str(settings.local_mtu)],
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                except subprocess.CalledProcessError as e:
                    logger.warning(f"Failed to set MTU on {port_name}: {e.stderr}")

    def get_node_vlans(self, lab_id: str, node_name: str) -> list[int]:
        """Get the VLAN tags allocated to a VM's interfaces.

        Args:
            lab_id: Lab identifier
            node_name: Node name

        Returns:
            List of VLAN tags, or empty list if not found
        """
        return self._vlan_allocations.get(lab_id, {}).get(node_name, [])

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

    def _compute_file_sha256(self, file_path: str) -> str:
        """Compute SHA256 hash of a file using streaming 1MB chunks."""
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()

    def _verify_backing_image(self, image_path: str, expected_sha256: str | None) -> None:
        """Verify backing image integrity before overlay creation.

        QEMU COW operations can corrupt the host page cache of read-only
        backing images. This check detects stale page cache data by comparing
        the file's SHA256 against the expected value from the image manifest.

        On mismatch, drops page caches and re-verifies. If the second hash
        matches, the file is fine (page cache was stale). If it still
        mismatches, the file is actually corrupted.
        """
        if not expected_sha256:
            return

        actual = self._compute_file_sha256(image_path)
        if actual == expected_sha256:
            return

        logger.warning(
            "Backing image SHA256 mismatch (page cache may be stale): "
            "expected %s, got %s — dropping caches and re-verifying",
            expected_sha256[:16], actual[:16],
        )

        # Drop page caches to flush stale data
        try:
            with open("/proc/sys/vm/drop_caches", "w") as f:
                f.write("3\n")
        except OSError as e:
            logger.warning("Could not drop page caches: %s", e)

        actual = self._compute_file_sha256(image_path)
        if actual == expected_sha256:
            logger.info(
                "Backing image OK after cache drop — page cache corruption recovered"
            )
            return

        raise RuntimeError(
            f"Backing image integrity check failed for {image_path}: "
            f"expected SHA256 {expected_sha256[:16]}..., "
            f"got {actual[:16]}... (file is corrupted)"
        )

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

    def _generate_mac_address(self, domain_name: str, interface_index: int) -> str:
        """Generate a deterministic MAC address for a VM interface.

        Uses domain name and interface index to generate consistent MACs.
        Format: 52:54:00:XX:XX:XX (QEMU/KVM OUI prefix)
        """
        import hashlib
        # Create deterministic hash from domain name and interface index
        hash_input = f"{domain_name}:{interface_index}".encode()
        hash_bytes = hashlib.md5(hash_input).digest()
        # Use QEMU/KVM OUI prefix (52:54:00) + 3 bytes from hash
        mac = f"52:54:00:{hash_bytes[0]:02x}:{hash_bytes[1]:02x}:{hash_bytes[2]:02x}"
        return mac

    def _find_ovmf_code_path(self) -> str | None:
        """Find a host OVMF firmware code file for EFI boot."""
        candidates = [
            "/usr/share/OVMF/OVMF_CODE.fd",
            "/usr/share/OVMF/OVMF_CODE_4M.fd",
            "/usr/share/edk2/ovmf/OVMF_CODE.fd",
            "/usr/share/edk2-ovmf/x64/OVMF_CODE.fd",
        ]
        for path in candidates:
            if os.path.exists(path):
                return path
        return None

    def _find_ovmf_vars_template(self) -> str | None:
        """Find a host OVMF vars template file for stateful EFI boot."""
        candidates = [
            "/usr/share/OVMF/OVMF_VARS.fd",
            "/usr/share/OVMF/OVMF_VARS_4M.fd",
            "/usr/share/edk2/ovmf/OVMF_VARS.fd",
            "/usr/share/edk2-ovmf/x64/OVMF_VARS.fd",
        ]
        for path in candidates:
            if os.path.exists(path):
                return path
        return None

    def _resolve_domain_driver(self, requested: str | None, node_name: str) -> str:
        """Resolve and validate libvirt domain driver.

        Policy:
        - enforce when value is valid (kvm|qemu)
        - warn and fall back to kvm when invalid/unsupported
        """
        candidate = (requested or "kvm").strip().lower()
        if candidate in self.ALLOWED_DOMAIN_DRIVERS:
            return candidate
        logger.warning(
            "Invalid libvirt_driver '%s' for %s; falling back to 'kvm'",
            requested,
            node_name,
        )
        return "kvm"

    def _generate_domain_xml(
        self,
        name: str,
        node_config: dict,
        overlay_path: Path,
        data_volume_path: Path | None = None,
        interface_count: int = 1,
        vlan_tags: list[int] | None = None,
        kind: str | None = None,
        include_management_interface: bool = False,
        management_network: str = "default",
        config_iso_path: Path | None = None,
    ) -> str:
        """Generate libvirt domain XML for a VM.

        Args:
            name: Domain name
            node_config: Node configuration from topology
            overlay_path: Path to the overlay disk
            data_volume_path: Optional path to data volume
            interface_count: Number of network interfaces to create
            vlan_tags: VLAN tags for each interface (for OVS isolation)
            kind: Device kind for config extraction lookup

        Returns:
            Domain XML string
        """
        # Get resource requirements from node config
        memory_mb = node_config.get("memory", 2048)
        cpus = node_config.get("cpu", 1)
        cpu_limit = node_config.get("cpu_limit")

        # Get driver and machine settings (whitelist-validated)
        machine_type = node_config.get("machine_type", "pc-q35-6.2")
        if machine_type not in self.VALID_MACHINE_TYPES:
            raise ValueError(f"Invalid machine type: {machine_type}")
        disk_driver = node_config.get("disk_driver", "virtio")
        if disk_driver not in self.VALID_DISK_DRIVERS:
            raise ValueError(f"Invalid disk driver: {disk_driver}")
        nic_driver = node_config.get("nic_driver", "virtio")
        if nic_driver in self.NIC_DRIVER_SUBSTITUTIONS:
            replacement = self.NIC_DRIVER_SUBSTITUTIONS[nic_driver]
            logger.warning(
                f"NIC driver '{nic_driver}' unsupported by QEMU, "
                f"substituting '{replacement}' for node {name}"
            )
            nic_driver = replacement
        if nic_driver not in self.VALID_NIC_DRIVERS:
            raise ValueError(f"Invalid NIC driver: {nic_driver}")
        libvirt_driver = self._resolve_domain_driver(
            node_config.get("libvirt_driver"),
            name,
        )
        efi_boot = bool(node_config.get("efi_boot", False))
        efi_vars = str(node_config.get("efi_vars") or "").strip().lower()

        # Map bus type to device name prefix
        dev_prefix = {"ide": "hd", "sata": "sd", "scsi": "sd"}.get(disk_driver, "vd")

        # Generate UUID for the domain
        domain_uuid = str(uuid.uuid4())

        # Build disk elements
        # cache='none' (O_DIRECT) bypasses page cache — prevents QEMU COW ops
        # from corrupting the host page cache of read-only backing images.
        # io='native' is required for optimal O_DIRECT performance.
        # discard='unmap' passes guest TRIM to reclaim overlay disk space.
        disks_xml = f'''
    <disk type='file' device='disk'>
      <driver name='qemu' type='qcow2' cache='none' io='native' discard='unmap'/>
      <source file='{xml_escape(str(overlay_path))}'/>
      <target dev='{dev_prefix}a' bus='{disk_driver}'/>
    </disk>'''

        if data_volume_path:
            disks_xml += f'''
    <disk type='file' device='disk'>
      <driver name='qemu' type='qcow2' cache='none' io='native' discard='unmap'/>
      <source file='{xml_escape(str(data_volume_path))}'/>
      <target dev='{dev_prefix}b' bus='{disk_driver}'/>
    </disk>'''

        if config_iso_path:
            disks_xml += f'''
    <disk type='file' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <source file='{xml_escape(str(config_iso_path))}'/>
      <target dev='hdc' bus='ide'/>
      <readonly/>
    </disk>'''

        # Build network interface elements
        # VMs connect to the OVS bridge (arch-ovs) for networking
        # Each interface gets a unique VLAN tag for isolation (like Docker containers)
        ovs_bridge = getattr(settings, 'ovs_bridge_name', 'arch-ovs')
        interfaces_xml = ""

        # Ensure we have at least 1 interface
        interface_count = max(1, interface_count)
        reserved_nics = node_config.get("reserved_nics", 0)
        data_interface_mac_offset = 0

        # For SSH-console libvirt VMs (e.g., NX-OSv/Cat9k), add a dedicated
        # management NIC on libvirt's default network before data interfaces.
        if include_management_interface:
            mgmt_mac = self._generate_mac_address(name, 0)
            interfaces_xml += f'''
    <interface type='network'>
      <mac address='{mgmt_mac}'/>
      <source network='{xml_escape(management_network)}'/>
      <model type='{nic_driver}'/>
    </interface>'''
            data_interface_mac_offset = 1

        # Reserved (dummy) NICs — placeholder interfaces required by some
        # platforms (e.g., XRv9000 needs ctrl-dummy + dev-dummy between
        # management and data interfaces for Spirit bootstrap).
        # These get their own VLAN tags from the beginning of vlan_tags.
        for r in range(reserved_nics):
            mac_address = self._generate_mac_address(name, r + data_interface_mac_offset)
            interface_id = str(uuid.uuid4())
            vlan_xml = ""
            if vlan_tags and r < len(vlan_tags):
                vlan_xml = f'''
      <vlan>
        <tag id='{vlan_tags[r]}'/>
      </vlan>'''
            interfaces_xml += f'''
    <interface type='bridge'>
      <mac address='{mac_address}'/>
      <source bridge='{ovs_bridge}'/>
      <virtualport type='openvswitch'>
        <parameters interfaceid='{interface_id}'/>
      </virtualport>{vlan_xml}
      <model type='{nic_driver}'/>
    </interface>'''
        data_interface_mac_offset += reserved_nics

        for i in range(interface_count):
            mac_address = self._generate_mac_address(name, i + data_interface_mac_offset)
            interface_id = str(uuid.uuid4())

            # Add VLAN tag if provided (for OVS isolation)
            # Data interfaces use vlan_tags after the reserved_nics offset
            vlan_xml = ""
            vlan_idx = i + reserved_nics
            if vlan_tags and vlan_idx < len(vlan_tags):
                vlan_xml = f'''
      <vlan>
        <tag id='{vlan_tags[vlan_idx]}'/>
      </vlan>'''

            interfaces_xml += f'''
    <interface type='bridge'>
      <mac address='{mac_address}'/>
      <source bridge='{ovs_bridge}'/>
      <virtualport type='openvswitch'>
        <parameters interfaceid='{interface_id}'/>
      </virtualport>{vlan_xml}
      <model type='{nic_driver}'/>
    </interface>'''

        # Build metadata section with device kind for config extraction
        metadata_xml = ""
        if kind:
            readiness_probe = node_config.get("readiness_probe")
            readiness_pattern = node_config.get("readiness_pattern")
            readiness_timeout = node_config.get("readiness_timeout")
            readiness_xml = ""
            if readiness_probe:
                readiness_xml += f"\n      <archetype:readiness_probe>{xml_escape(str(readiness_probe))}</archetype:readiness_probe>"
            if readiness_pattern:
                readiness_xml += f"\n      <archetype:readiness_pattern>{xml_escape(str(readiness_pattern))}</archetype:readiness_pattern>"
            if readiness_timeout:
                try:
                    readiness_xml += f"\n      <archetype:readiness_timeout>{int(readiness_timeout)}</archetype:readiness_timeout>"
                except (TypeError, ValueError):
                    logger.debug(f"Skipping invalid readiness_timeout value in metadata: {readiness_timeout}")
            serial_type = node_config.get("serial_type", "pty")
            if serial_type and serial_type != "pty":
                readiness_xml += f"\n      <archetype:serial_type>{xml_escape(str(serial_type))}</archetype:serial_type>"
            metadata_xml = f'''
  <metadata>
    <archetype:node xmlns:archetype="http://archetype.io/libvirt/1">
      <archetype:kind>{xml_escape(kind)}</archetype:kind>{readiness_xml}
    </archetype:node>
  </metadata>'''

        # Build OS section, optionally enabling EFI firmware.
        os_type_line = f"<type arch='x86_64' machine='{xml_escape(machine_type)}'>hvm</type>"
        os_open = "<os>"
        os_extras = "\n    <boot dev='hd'/>"
        qemu_commandline_xml = ""
        if efi_boot:
            ovmf_code = self._find_ovmf_code_path()
            ovmf_vars = self._find_ovmf_vars_template()
            if efi_vars == "stateless":
                # Stateless EFI: use QEMU commandline passthrough to inject the
                # OVMF CODE as a single read-only pflash drive.  This bypasses
                # libvirt's firmware auto-selection which unconditionally adds a
                # second pflash device (NVRAM) — matching vrnetlab's approach.
                if ovmf_code:
                    qemu_commandline_xml = (
                        "\n  <qemu:commandline>"
                        f"\n    <qemu:arg value='-drive'/>"
                        f"\n    <qemu:arg value='if=pflash,format=raw,readonly=on,file={xml_escape(ovmf_code)}'/>"
                        "\n  </qemu:commandline>"
                    )
                else:
                    logger.warning(
                        "Stateless EFI boot requested for %s but no OVMF firmware found",
                        name,
                    )
            else:
                # Stateful EFI: let libvirt manage firmware via firmware='efi'
                os_open = "<os firmware='efi'>"
                if ovmf_code:
                    os_extras += f"\n    <loader readonly='yes' type='pflash'>{xml_escape(ovmf_code)}</loader>"
                    if ovmf_vars:
                        os_extras += (
                            f"\n    <nvram template='{xml_escape(ovmf_vars)}'>"
                            f"/var/lib/libvirt/qemu/nvram/{xml_escape(name)}_VARS.fd</nvram>"
                        )
                else:
                    logger.warning(
                        "EFI boot requested for %s but no OVMF firmware file was found; "
                        "relying on libvirt firmware auto-selection",
                        name,
                    )

        # Build the full domain XML
        cputune_xml = ""
        if cpu_limit is not None:
            try:
                limit_pct = max(1, min(100, int(cpu_limit)))
                period = 100000
                quota = int(period * max(1, int(cpus)) * (limit_pct / 100.0))
                if quota > 0:
                    cputune_xml = (
                        "\n  <cputune>"
                        f"\n    <period>{period}</period>"
                        f"\n    <quota>{quota}</quota>"
                        "\n  </cputune>"
                    )
            except (TypeError, ValueError):
                logger.debug("Skipping invalid cpu_limit value in domain XML: %s", cpu_limit)

        # Build serial/console XML — PTY (default) or TCP telnet
        serial_type = node_config.get("serial_type", "pty")
        serial_port_count = node_config.get("serial_port_count", 1)
        if serial_type == "tcp":
            tcp_port = self._allocate_tcp_serial_port()
            serial_xml = f"""    <serial type='tcp'>
      <source mode='bind' host='127.0.0.1' service='{tcp_port}'/>
      <protocol type='telnet'/>
      <target port='0'/>
    </serial>
    <console type='tcp'>
      <source mode='bind' host='127.0.0.1' service='{tcp_port}'/>
      <protocol type='telnet'/>
      <target type='serial' port='0'/>
    </console>"""
            # Additional serial ports as PTY (XRv9000 needs 4 total for inner VM)
            for port_idx in range(1, serial_port_count):
                serial_xml += f"""
    <serial type='pty'>
      <target port='{port_idx}'/>
    </serial>"""
        else:
            serial_xml = """    <serial type='pty'>
      <target port='0'/>
    </serial>
    <console type='pty'>
      <target type='serial' port='0'/>
    </console>"""
            # Additional serial ports (e.g., IOS-XRv 9000 needs 4 total)
            for port_idx in range(1, serial_port_count):
                serial_xml += f"""
    <serial type='pty'>
      <target port='{port_idx}'/>
    </serial>"""

        # VNC graphics + VGA video by default.
        # nographic=True omits display devices so OVMF outputs to serial.
        nographic = node_config.get("nographic", False)
        if nographic or serial_type == "tcp":
            graphics_xml = ""
        else:
            graphics_xml = """    <graphics type='vnc' port='-1' autoport='yes' listen='127.0.0.1'>
      <listen type='address' address='127.0.0.1'/>
    </graphics>
    <video>
      <model type='cirrus'/>
    </video>
"""

        # SMBIOS product identification (required by some vendors, e.g., IOS-XRv 9000)
        smbios_product = node_config.get("smbios_product", "")
        sysinfo_xml = ""
        smbios_os_xml = ""
        if smbios_product:
            sysinfo_xml = f"""
  <sysinfo type='smbios'>
    <system>
      <entry name='manufacturer'>cisco</entry>
      <entry name='product'>{xml_escape(smbios_product)}</entry>
    </system>
  </sysinfo>"""
            smbios_os_xml = "\n    <smbios mode='sysinfo'/>"

        # CPU SMP topology — some platforms (e.g., XRv9000) require cores-per-socket
        # instead of sockets-per-core for Spirit bootstrap to detect CPUs correctly.
        # migratable='off' exposes VMX/SVM for nested KVM (required by XRv9000 XR VM).
        cpu_sockets = node_config.get("cpu_sockets", 0)
        if cpu_sockets > 0:
            cores = max(1, cpus // cpu_sockets)
            cpu_xml = (
                f"<cpu mode='host-passthrough' migratable='off'>\n"
                f"    <topology sockets='{cpu_sockets}' cores='{cores}' threads='1'/>\n"
                f"  </cpu>"
            )
        else:
            cpu_xml = "<cpu mode='host-passthrough' migratable='off'/>"

        smm_xml = "\n    <smm state='off'/>" if efi_boot else ""

        qemu_ns = " xmlns:qemu='http://libvirt.org/schemas/domain/qemu/1.0'" if qemu_commandline_xml else ""

        xml = f'''<domain type='{libvirt_driver}'{qemu_ns}>{sysinfo_xml}
  <name>{xml_escape(name)}</name>
  <uuid>{domain_uuid}</uuid>{metadata_xml}
  <memory unit='MiB'>{memory_mb}</memory>
  <vcpu>{cpus}</vcpu>{cputune_xml}
  {os_open}
    {os_type_line}{os_extras}{smbios_os_xml}
  </os>
  <features>
    <acpi/>
    <apic/>{smm_xml}
  </features>
  {cpu_xml}
  <clock offset='utc'>
    <timer name='rtc' tickpolicy='catchup'/>
    <timer name='pit' tickpolicy='delay'/>
    <timer name='hpet' present='no'/>
  </clock>
  <devices>
    <emulator>/usr/bin/qemu-system-x86_64</emulator>
{disks_xml}
{interfaces_xml}
{serial_xml}
{graphics_xml}    <memballoon model='none'/>
    <rng model='virtio'>
      <backend model='random'>/dev/urandom</backend>
    </rng>
  </devices>{qemu_commandline_xml}
</domain>'''

        return xml

    @staticmethod
    def _allocate_tcp_serial_port() -> int:
        """Allocate a free TCP port for serial console.

        Uses the OS to find an available port by binding to port 0.
        """
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    @staticmethod
    def _get_tcp_serial_port(domain) -> int | None:
        """Extract TCP serial port from a running domain's XML.

        Parses the domain XML for <serial type='tcp'> and returns
        the service port number. Returns None if not a TCP serial domain.
        """
        try:
            xml_str = domain.XMLDesc(0)
            root = ET.fromstring(xml_str)
            for serial in root.findall(".//devices/serial[@type='tcp']"):
                source = serial.find("source")
                if source is not None:
                    port_str = source.get("service")
                    if port_str:
                        return int(port_str)
        except Exception:
            pass
        return None

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

        # Attempt to recover stale network state from previous deployment
        # This handles the case where the agent restarted and lost in-memory VLAN allocations
        recovered_vlans = self._recover_stale_network(lab_id, workspace)
        if recovered_vlans:
            logger.info(
                f"Recovered network state for {len(recovered_vlans)} existing nodes"
            )

        disks_dir = self._disks_dir(workspace)
        deployed_nodes: list[NodeInfo] = []
        errors: list[str] = []

        for node in libvirt_nodes:
            node_name = node.name
            display_name = node.display_name or node_name
            log_name = f"{display_name}({node_name})" if display_name != node_name else node_name

            # Look up libvirt config from vendor registry
            libvirt_config = get_libvirt_config(node.kind)
            if getattr(libvirt_config, "source", "vendor") == "fallback":
                logger.warning(
                    f"VM config fallback in use for {log_name}: "
                    f"unknown device kind '{node.kind}' resolved to generic defaults "
                    "(override with explicit memory/cpu/machine_type/disk_driver/nic_driver)"
                )

            # Build node config dict for helper methods
            # interface_count comes from topology (based on links) or defaults to 1
            interface_count = node.interface_count or 1
            resolved_memory = node.memory if node.memory is not None else libvirt_config.memory_mb
            resolved_cpu = node.cpu if node.cpu is not None else libvirt_config.cpu_count
            resolved_cpu_limit = node.cpu_limit
            resolved_machine_type = (
                node.machine_type if node.machine_type is not None else libvirt_config.machine_type
            )
            resolved_disk_driver = (
                node.disk_driver if node.disk_driver is not None else libvirt_config.disk_driver
            )
            resolved_nic_driver = (
                node.nic_driver if node.nic_driver is not None else libvirt_config.nic_driver
            )
            resolved_libvirt_driver = node.libvirt_driver if node.libvirt_driver is not None else "kvm"
            resolved_efi_boot = (
                node.efi_boot if node.efi_boot is not None else libvirt_config.efi_boot
            )
            resolved_efi_vars = node.efi_vars if node.efi_vars is not None else libvirt_config.efi_vars
            resolved_readiness_probe = (
                node.readiness_probe if node.readiness_probe is not None else libvirt_config.readiness_probe
            )
            resolved_readiness_pattern = (
                node.readiness_pattern if node.readiness_pattern is not None else libvirt_config.readiness_pattern
            )
            resolved_readiness_timeout = (
                node.readiness_timeout if node.readiness_timeout is not None else libvirt_config.readiness_timeout
            )
            node_config = {
                "image": node.image,
                "memory": resolved_memory,
                "cpu": resolved_cpu,
                "cpu_limit": resolved_cpu_limit,
                "machine_type": resolved_machine_type,
                "disk_driver": resolved_disk_driver,
                "nic_driver": resolved_nic_driver,
                "libvirt_driver": resolved_libvirt_driver,
                "efi_boot": resolved_efi_boot,
                "efi_vars": resolved_efi_vars,
                "data_volume_gb": node.data_volume_gb if node.data_volume_gb is not None else libvirt_config.data_volume_gb,
                "readiness_probe": resolved_readiness_probe,
                "readiness_pattern": resolved_readiness_pattern,
                "readiness_timeout": resolved_readiness_timeout,
                "serial_type": libvirt_config.serial_type,
                "nographic": libvirt_config.nographic,
                "serial_port_count": libvirt_config.serial_port_count,
                "smbios_product": libvirt_config.smbios_product,
                "reserved_nics": libvirt_config.reserved_nics,
                "cpu_sockets": libvirt_config.cpu_sockets,
                "interface_count": interface_count,
                "_display_name": display_name,
            }
            logger.info(
                f"VM config for {log_name}: {resolved_memory}MB RAM, "
                f"{resolved_cpu} vCPU, disk={resolved_disk_driver}, "
                f"nic={resolved_nic_driver}, machine={resolved_machine_type}, "
                f"driver={resolved_libvirt_driver}, cpu_limit={resolved_cpu_limit}, interfaces={interface_count}, "
                f"efi_boot={resolved_efi_boot}, efi_vars={resolved_efi_vars}"
            )

            try:
                node_info = await self._deploy_node(
                    lab_id,
                    node_name,
                    node_config,
                    disks_dir,
                    kind=node.kind,
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
        kind: str | None = None,
    ) -> NodeInfo:
        """Deploy a single VM node."""
        domain_name = self._domain_name(lab_id, node_name)
        # Ensure we have a complete view of existing VLAN allocations before allocating new ones.
        # This avoids accidentally reusing VLAN tags that are already in use on arch-ovs.
        try:
            self._recover_stale_network(lab_id, disks_dir.parent)
        except Exception:
            pass

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
                    # Shut-off domain may have stale XML from a previous
                    # vendor config.  Undefine it so we recreate with the
                    # latest definition instead of booting the old one.
                    logger.info(
                        "Undefining stale shut-off domain %s for fresh creation",
                        domain_name,
                    )
                    self._undefine_domain(existing, domain_name)
                    for suffix in ("", "-data"):
                        disk = disks_dir / f"{node_name}{suffix}.qcow2"
                        if disk.exists():
                            disk.unlink()
                            logger.info("Removed stale disk: %s", disk)
                    lab_allocs = self._vlan_allocations.get(lab_id, {})
                    if node_name in lab_allocs:
                        del lab_allocs[node_name]
                        self._save_vlan_allocations(lab_id, disks_dir.parent)
                    # Fall through to fresh creation below
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

        # Get interface count from node config (default to 1)
        interface_count = node_config.get("interface_count", 1)
        reserved_nics = node_config.get("reserved_nics", 0)

        # Allocate VLAN tags for each interface (for OVS isolation)
        # Include reserved (dummy) NICs that need their own VLAN tags
        # Pass workspace (parent of disks_dir) for persisting allocations
        workspace = disks_dir.parent
        vlan_tags = self._allocate_vlans(lab_id, node_name, interface_count + reserved_nics, workspace)

        include_management_interface = False
        if kind and self._node_uses_dedicated_mgmt_interface(kind):
            include_management_interface = self._ensure_libvirt_network("default")
            if not include_management_interface:
                logger.warning(
                    f"Unable to enable libvirt 'default' network for {node_name}; "
                    "management NIC omitted, SSH console may be unavailable"
                )

        # Generate domain XML with multiple interfaces
        xml = self._generate_domain_xml(
            domain_name,
            node_config,
            overlay_path,
            data_volume_path,
            interface_count=interface_count,
            vlan_tags=vlan_tags,
            kind=kind,
            include_management_interface=include_management_interface,
            management_network="default",
        )

        # Define and start the domain
        domain = self.conn.defineXML(xml)
        if not domain:
            raise RuntimeError(f"Failed to define domain {domain_name}")

        domain.create()
        logger.info(f"Started domain {domain_name}")

        # Set MTU on tap devices (they inherit bridge MTU which may be 1450)
        await self._set_vm_tap_mtu(lab_id, node_name)

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
                    self._undefine_domain(domain, name)
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

            # Clean up VLAN allocations for this lab (in-memory and on disk)
            if lab_id in self._vlan_allocations:
                del self._vlan_allocations[lab_id]
            if lab_id in self._next_vlan:
                del self._next_vlan[lab_id]
            self._remove_vlan_file(lab_id, workspace)

            # Clean up OVS networking state for this lab
            try:
                from agent.network.backends.registry import get_network_backend
                backend = get_network_backend()
                if hasattr(backend, 'ovs_manager') and backend.ovs_manager._initialized:
                    ovs_result = await backend.ovs_manager.cleanup_lab(lab_id)
                    logger.info(f"OVS cleanup for lab {lab_id}: {ovs_result}")
            except Exception as e:
                logger.warning(f"OVS cleanup during VM destroy failed: {e}")

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
            await self._set_vm_tap_mtu(lab_id, node_name)

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

    async def _remove_vm(
        self,
        lab_id: str,
        node_name: str,
        workspace: Path,
    ) -> None:
        """Remove a single VM and clean up per-node resources.

        Force-stops the domain (if running), undefines it (including NVRAM),
        deletes overlay disks, and cleans VLAN allocations. Does NOT clean
        lab-level VLAN tracking — that's only done by destroy_node.

        Raises libvirt.libvirtError if the domain doesn't exist (caller handles).
        """
        domain_name = self._domain_name(lab_id, node_name)

        domain = self.conn.lookupByName(domain_name)
        state, _ = domain.state()

        # Force stop if running or in any active state
        if state not in (libvirt.VIR_DOMAIN_SHUTOFF, libvirt.VIR_DOMAIN_CRASHED):
            try:
                domain.destroy()
            except libvirt.libvirtError:
                pass  # May fail if already stopped

        # Undefine domain (removes definition + NVRAM)
        self._undefine_domain(domain, domain_name)
        logger.info(f"Undefined domain {domain_name}")

        # Delete overlay disks and config ISO
        disks_dir = self._disks_dir(workspace)
        for suffix in ("", "-data"):
            disk_path = disks_dir / f"{node_name}{suffix}.qcow2"
            if disk_path.exists():
                try:
                    disk_path.unlink()
                    logger.info(f"Removed disk: {disk_path}")
                except Exception as e:
                    logger.warning(f"Failed to remove disk {disk_path}: {e}")

        iso_path = disks_dir / f"{node_name}-config.iso"
        if iso_path.exists():
            try:
                iso_path.unlink()
                logger.info(f"Removed config ISO: {iso_path}")
            except Exception as e:
                logger.warning(f"Failed to remove config ISO {iso_path}: {e}")

        # Clean up per-node VLAN allocations
        lab_allocs = self._vlan_allocations.get(lab_id, {})
        if node_name in lab_allocs:
            del lab_allocs[node_name]
            self._save_vlan_allocations(lab_id, workspace)

    async def stop_node(
        self,
        lab_id: str,
        node_name: str,
        workspace: Path,
        force: bool = True,
    ) -> NodeActionResult:
        """Stop a specific VM by destroying, undefining, and removing disks.

        After stop, the domain and disks are gone. Starting the node again
        will create a fresh VM from the base image with the saved startup config.

        Args:
            lab_id: Lab identifier
            node_name: Node name within the lab
            workspace: Lab workspace path
            force: Ignored (kept for API compatibility). VMs are always
                   force-stopped before removal.
        """
        domain_name = self._domain_name(lab_id, node_name)

        try:
            await self._remove_vm(lab_id, node_name, workspace)

            return NodeActionResult(
                success=True,
                node_name=node_name,
                new_status=NodeStatus.STOPPED,
                stdout=f"Stopped and removed domain {domain_name}",
            )

        except libvirt.libvirtError as e:
            if "domain not found" in str(e).lower():
                # Domain already gone — treat as success
                return NodeActionResult(
                    success=True,
                    node_name=node_name,
                    new_status=NodeStatus.STOPPED,
                    stdout="Domain already removed",
                )
            if "domain is not running" in str(e).lower():
                return NodeActionResult(
                    success=True,
                    node_name=node_name,
                    new_status=NodeStatus.STOPPED,
                    stdout="Domain already stopped",
                )
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

    async def create_node(
        self,
        lab_id: str,
        node_name: str,
        kind: str,
        workspace: Path,
        *,
        image: str | None = None,
        display_name: str | None = None,
        interface_count: int | None = None,
        binds: list[str] | None = None,
        env: dict[str, str] | None = None,
        startup_config: str | None = None,
        memory: int | None = None,
        cpu: int | None = None,
        cpu_limit: int | None = None,
        disk_driver: str | None = None,
        nic_driver: str | None = None,
        machine_type: str | None = None,
        libvirt_driver: str | None = None,
        readiness_probe: str | None = None,
        readiness_pattern: str | None = None,
        readiness_timeout: int | None = None,
        efi_boot: bool | None = None,
        efi_vars: str | None = None,
        data_volume_gb: int | None = None,
        image_sha256: str | None = None,
    ) -> NodeActionResult:
        """Create (define) a single VM without starting it."""
        domain_name = self._domain_name(lab_id, node_name)

        try:
            # Ensure allocations are recovered/discovered before handing out VLAN tags.
            try:
                self._recover_stale_network(lab_id, workspace)
            except Exception:
                pass

            # Check if domain already exists
            try:
                existing = self.conn.lookupByName(domain_name)
                if existing:
                    state = self._get_domain_status(existing)
                    if state == NodeStatus.RUNNING:
                        return NodeActionResult(
                            success=True,
                            node_name=node_name,
                            new_status=state,
                            stdout=f"Domain {domain_name} already running",
                        )
                    # Shut-off domain may have stale XML — undefine so we
                    # recreate with the latest vendor config.
                    logger.info(
                        "Undefining stale shut-off domain %s for fresh creation",
                        domain_name,
                    )
                    self._undefine_domain(existing, domain_name)
                    disks_dir = self._disks_dir(workspace)
                    for suffix in ("", "-data"):
                        disk = disks_dir / f"{node_name}{suffix}.qcow2"
                        if disk.exists():
                            disk.unlink()
                            logger.info("Removed stale disk: %s", disk)
                    lab_allocs = self._vlan_allocations.get(lab_id, {})
                    if node_name in lab_allocs:
                        del lab_allocs[node_name]
                        self._save_vlan_allocations(lab_id, workspace)
                    # Fall through to fresh creation below
            except libvirt.libvirtError:
                pass  # Domain doesn't exist, we'll create it

            # Build node_config from vendor registry, with API-resolved overrides
            libvirt_config = get_libvirt_config(kind)
            node_config: dict[str, Any] = {
                "image": image,
                "memory": memory or libvirt_config.memory_mb,
                "cpu": cpu or libvirt_config.cpu_count,
                "cpu_limit": cpu_limit,
                "machine_type": machine_type or libvirt_config.machine_type,
                "disk_driver": disk_driver or libvirt_config.disk_driver,
                "nic_driver": nic_driver or libvirt_config.nic_driver,
                "libvirt_driver": libvirt_driver or "kvm",
                "readiness_probe": readiness_probe if readiness_probe is not None else libvirt_config.readiness_probe,
                "readiness_pattern": readiness_pattern if readiness_pattern is not None else libvirt_config.readiness_pattern,
                "readiness_timeout": readiness_timeout if readiness_timeout is not None else libvirt_config.readiness_timeout,
                "efi_boot": efi_boot if efi_boot is not None else libvirt_config.efi_boot,
                "efi_vars": efi_vars if efi_vars is not None else libvirt_config.efi_vars,
                "serial_type": libvirt_config.serial_type,
                "nographic": libvirt_config.nographic,
                "serial_port_count": libvirt_config.serial_port_count,
                "smbios_product": libvirt_config.smbios_product,
                "reserved_nics": libvirt_config.reserved_nics,
                "cpu_sockets": libvirt_config.cpu_sockets,
                "data_volume_gb": data_volume_gb if data_volume_gb is not None else libvirt_config.data_volume_gb,
                "interface_count": interface_count or 1,
                "_display_name": display_name or node_name,
            }

            disks_dir = self._disks_dir(workspace)

            # Get base image
            base_image = self._get_base_image(node_config)
            if not base_image:
                return NodeActionResult(
                    success=False,
                    node_name=node_name,
                    error=f"No base image found for node {node_name} (image={image})",
                )

            # Verify backing image integrity before overlay creation.
            # QEMU COW operations can corrupt the host page cache of backing
            # images; this detects and recovers from stale cache data.
            try:
                self._verify_backing_image(base_image, image_sha256)
            except RuntimeError as e:
                return NodeActionResult(
                    success=False,
                    node_name=node_name,
                    error=str(e),
                )

            # Create overlay disk
            overlay_path = disks_dir / f"{node_name}.qcow2"
            if not await self._create_overlay_disk(base_image, overlay_path):
                return NodeActionResult(
                    success=False,
                    node_name=node_name,
                    error=f"Failed to create overlay disk for {node_name}",
                )

            # Inject startup-config into bootflash if supported
            if not startup_config:
                config_file = workspace / "configs" / node_name / "startup-config"
                if config_file.exists():
                    startup_config = config_file.read_text()

            if startup_config and libvirt_config.config_inject_method == "bootflash":
                from agent.providers.bootflash_inject import inject_startup_config
                inject_ok = await asyncio.to_thread(
                    inject_startup_config,
                    overlay_path,
                    startup_config,
                    partition=libvirt_config.config_inject_partition,
                    fs_type=libvirt_config.config_inject_fs_type,
                    config_path=libvirt_config.config_inject_path,
                )
                if inject_ok:
                    logger.info("Injected startup config for %s (%d bytes)", node_name, len(startup_config))
                else:
                    logger.warning("Config injection failed for %s; VM will boot without config", node_name)

            # Create config ISO for platforms that read config from CD-ROM (e.g., IOS-XR CVAC)
            config_iso_path: Path | None = None
            if startup_config and libvirt_config.config_inject_method == "iso":
                from agent.providers.iso_inject import create_config_iso
                config_iso_path = disks_dir / f"{node_name}-config.iso"
                iso_ok = await asyncio.to_thread(
                    create_config_iso,
                    config_iso_path,
                    startup_config,
                    volume_label=libvirt_config.config_inject_iso_volume_label or "config",
                    filename=libvirt_config.config_inject_iso_filename or "startup-config",
                )
                if iso_ok:
                    logger.info("Created config ISO for %s (%d bytes)", node_name, len(startup_config))
                else:
                    logger.warning("Config ISO creation failed for %s; VM will boot without config", node_name)
                    config_iso_path = None

            # Create data volume if needed
            data_volume_path = None
            data_volume_size = node_config.get("data_volume_gb")
            if data_volume_size:
                data_volume_path = disks_dir / f"{node_name}-data.qcow2"
                if not await self._create_data_volume(data_volume_path, data_volume_size):
                    return NodeActionResult(
                        success=False,
                        node_name=node_name,
                        error=f"Failed to create data volume for {node_name}",
                    )

            # Allocate VLAN tags for OVS isolation (include reserved dummy NICs)
            iface_count = node_config.get("interface_count", 1)
            reserved_nics = node_config.get("reserved_nics", 0)
            vlan_tags = self._allocate_vlans(lab_id, node_name, iface_count + reserved_nics, workspace)

            include_management_interface = False
            if self._node_uses_dedicated_mgmt_interface(kind):
                include_management_interface = self._ensure_libvirt_network("default")
                if not include_management_interface:
                    logger.warning(
                        f"Unable to enable libvirt 'default' network for {node_name}; "
                        "management NIC omitted, SSH console may be unavailable"
                    )

            # Generate domain XML
            xml = self._generate_domain_xml(
                domain_name,
                node_config,
                overlay_path,
                data_volume_path,
                interface_count=iface_count,
                vlan_tags=vlan_tags,
                kind=kind,
                include_management_interface=include_management_interface,
                management_network="default",
                config_iso_path=config_iso_path,
            )

            # Define but do NOT start
            domain = self.conn.defineXML(xml)
            if not domain:
                return NodeActionResult(
                    success=False,
                    node_name=node_name,
                    error=f"Failed to define domain {domain_name}",
                )

            logger.info(f"Defined domain {domain_name} (not started)")

            return NodeActionResult(
                success=True,
                node_name=node_name,
                new_status=NodeStatus.STOPPED,
                stdout=f"Defined domain {domain_name}",
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

    async def destroy_node(
        self,
        lab_id: str,
        node_name: str,
        workspace: Path,
    ) -> NodeActionResult:
        """Destroy a single VM and clean up all resources.

        Uses _remove_vm for per-node cleanup. Lab-level VLAN tracking
        is cleaned up if needed.
        """
        domain_name = self._domain_name(lab_id, node_name)

        try:
            try:
                await self._remove_vm(lab_id, node_name, workspace)
            except libvirt.libvirtError as e:
                if "domain not found" in str(e).lower():
                    logger.info(f"Domain {domain_name} not found, already removed")
                else:
                    raise

            logger.info(f"Destroyed domain {domain_name}")

            return NodeActionResult(
                success=True,
                node_name=node_name,
                new_status=NodeStatus.STOPPED,
                stdout=f"Destroyed domain {domain_name}",
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
        """Get console command for a VM.

        Supports two console methods:
        - virsh: Use virsh console (default for serial console devices)
        - ssh: Use SSH to VM's management IP address

        The console method is determined by the device's vendor config.
        """
        domain_name = self._domain_name(lab_id, node_name)

        try:
            domain = self.conn.lookupByName(domain_name)
            state, _ = domain.state()

            if state != libvirt.VIR_DOMAIN_RUNNING:
                return None

            # Get device kind from domain metadata
            kind = self._get_domain_kind(domain)
            if kind:
                console_method = get_console_method(kind)
            else:
                console_method = "docker_exec"  # Default to virsh for VMs

            if console_method == "ssh":
                # Get VM IP address and SSH credentials
                ip = await self._get_vm_management_ip(domain_name)
                if not ip:
                    logger.warning(
                        f"No IP address found for SSH console to {domain_name}, "
                        "falling back to virsh console"
                    )
                    return ["virsh", "-c", self._uri, "console", "--force", domain_name]

                user, password = get_console_credentials(kind)

                # Some appliances expose a management IP but do not accept SSH yet
                # (or credentials differ). Probe SSH quickly and fall back to
                # serial console so the UI console is still usable.
                try:
                    probe = await asyncio.to_thread(
                        subprocess.run,
                        [
                            "sshpass",
                            "-p",
                            password,
                            "ssh",
                            "-o",
                            "StrictHostKeyChecking=no",
                            "-o",
                            "UserKnownHostsFile=/dev/null",
                            "-o",
                            "LogLevel=ERROR",
                            "-o",
                            "ConnectTimeout=5",
                            "-o",
                            "PreferredAuthentications=password",
                            "-o",
                            "PubkeyAuthentication=no",
                            "-o",
                            "NumberOfPasswordPrompts=1",
                            f"{user}@{ip}",
                            "true",
                        ],
                        capture_output=True,
                        text=True,
                    )
                    if probe.returncode != 0:
                        logger.warning(
                            "SSH console probe failed for %s (%s@%s rc=%s), falling back to virsh console",
                            domain_name,
                            user,
                            ip,
                            probe.returncode,
                        )
                        return ["virsh", "-c", self._uri, "console", "--force", domain_name]
                except Exception:
                    logger.warning(
                        "SSH console probe errored for %s (%s@%s), falling back to virsh console",
                        domain_name,
                        user,
                        ip,
                        exc_info=True,
                    )
                    return ["virsh", "-c", self._uri, "console", "--force", domain_name]

                # Use sshpass for non-interactive password authentication
                return [
                    "sshpass", "-p", password,
                    "ssh",
                    "-o", "StrictHostKeyChecking=no",
                    "-o", "UserKnownHostsFile=/dev/null",
                    "-o", "LogLevel=ERROR",
                    f"{user}@{ip}",
                ]
            else:
                # Check for TCP serial port (e.g., IOS-XRv 9000)
                tcp_port = self._get_tcp_serial_port(domain)
                if tcp_port:
                    return ["python3", "-c", _TCP_TELNET_CONSOLE_SCRIPT, str(tcp_port)]

                # Default: virsh console (serial console)
                # --force takes over console even if another session is connected
                return ["virsh", "-c", self._uri, "console", "--force", domain_name]

        except libvirt.libvirtError:
            return None
        except Exception:
            return None

    async def _get_vm_management_ip(self, domain_name: str) -> str | None:
        """Get the management IP address for a VM.

        Uses virsh domifaddr to query the guest agent or DHCP leases
        for the VM's IP address.

        Args:
            domain_name: Libvirt domain name

        Returns:
            IP address string or None if not found
        """
        try:
            # Try guest agent first (most accurate)
            result = await asyncio.to_thread(
                subprocess.run,
                ["virsh", "-c", self._uri, "domifaddr", domain_name, "--source", "agent"],
                capture_output=True,
                text=True,
            )

            if result.returncode == 0 and result.stdout.strip():
                # Parse output: "Name       MAC address          Protocol     Address"
                for line in result.stdout.strip().split("\n")[2:]:  # Skip header
                    parts = line.split()
                    if len(parts) >= 4:
                        # Address is like "192.168.1.100/24"
                        ip = parts[3].split("/")[0]
                        if ip and not ip.startswith("127."):
                            return ip

            # Fall back to DHCP leases
            result = await asyncio.to_thread(
                subprocess.run,
                ["virsh", "-c", self._uri, "domifaddr", domain_name, "--source", "lease"],
                capture_output=True,
                text=True,
            )

            if result.returncode == 0 and result.stdout.strip():
                for line in result.stdout.strip().split("\n")[2:]:
                    parts = line.split()
                    if len(parts) >= 4:
                        ip = parts[3].split("/")[0]
                        if ip and not ip.startswith("127."):
                            return ip

            # Fall back to ARP (least reliable)
            result = await asyncio.to_thread(
                subprocess.run,
                ["virsh", "-c", self._uri, "domifaddr", domain_name, "--source", "arp"],
                capture_output=True,
                text=True,
            )

            if result.returncode == 0 and result.stdout.strip():
                for line in result.stdout.strip().split("\n")[2:]:
                    parts = line.split()
                    if len(parts) >= 4:
                        ip = parts[3].split("/")[0]
                        if ip and not ip.startswith("127."):
                            return ip

            return None

        except Exception as e:
            logger.debug(f"Error getting VM IP for {domain_name}: {e}")
            return None

    def _node_uses_dedicated_mgmt_interface(self, kind: str | None) -> bool:
        """Return True when VM should have a dedicated management interface.

        Needed when console_method is SSH, or when config_extract_method is SSH
        (e.g., IOS-XRv uses TCP serial for console but SSH for config extraction).
        """
        if not kind:
            return False
        try:
            if get_console_method(kind) == "ssh":
                return True
            extract_settings = get_config_extraction_settings(kind)
            return extract_settings.method == "ssh"
        except Exception:
            return False

    def _ensure_libvirt_network(self, network_name: str) -> bool:
        """Ensure a libvirt network exists, is active, and autostarted."""
        try:
            network = self.conn.networkLookupByName(network_name)
            if network is None:
                return False
            if network.isActive() != 1:
                network.create()
            try:
                network.setAutostart(True)
            except Exception:
                # Autostart failure is non-fatal if the network is active now.
                pass
            return True
        except Exception:
            return False

    def _domain_has_dedicated_mgmt_interface(self, domain) -> bool:
        """Detect whether a domain includes a default-network management NIC."""
        try:
            xml = domain.XMLDesc(0)
            root = ET.fromstring(xml)
            for iface in root.findall(".//devices/interface[@type='network']"):
                src = iface.find("source")
                if src is not None and src.get("network") == "default":
                    return True
        except Exception:
            return False
        return False

    async def get_vm_interface_port(
        self,
        lab_id: str,
        node_name: str,
        interface_index: int,
    ) -> str | None:
        """Get the OVS port name for a VM interface.

        Libvirt creates ports on OVS with names like 'vnet0', 'vnet1', etc.
        We can find the port by looking for the interface with our MAC address.

        Args:
            lab_id: Lab identifier
            node_name: Node name
            interface_index: Interface index (0-based)

        Returns:
            OVS port name, or None if not found
        """
        domain_name = self._domain_name(lab_id, node_name)
        mac_index = interface_index
        try:
            domain = self.conn.lookupByName(domain_name)
            kind = self._get_domain_kind(domain)
            if self._node_uses_dedicated_mgmt_interface(kind) and self._domain_has_dedicated_mgmt_interface(domain):
                mac_index = interface_index + 1
        except Exception:
            pass

        guest_mac = self._generate_mac_address(domain_name, mac_index)

        # Libvirt modifies the first byte of the MAC on host-side tap devices:
        # guest sees 52:54:00:XX:XX:XX but the tap device (and OVS mac_in_use)
        # reports fe:54:00:XX:XX:XX.  Match against both forms.
        tap_mac = "fe" + guest_mac[2:]   # fe:54:00:XX:XX:XX
        expected_macs = {guest_mac.lower(), tap_mac.lower()}

        try:
            # Get OVS ports and find the one with matching MAC
            result = subprocess.run(
                ["ovs-vsctl", "--format=json", "list-ports", settings.ovs_bridge_name],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                return None

            ports = result.stdout.strip().split('\n')
            for port in ports:
                if not port:
                    continue
                # Check if this port has our MAC address
                mac_result = subprocess.run(
                    ["ovs-vsctl", "get", "interface", port, "mac_in_use"],
                    capture_output=True,
                    text=True,
                )
                if mac_result.returncode == 0:
                    port_mac = mac_result.stdout.strip().strip('"')
                    if port_mac.lower() in expected_macs:
                        return port

            return None
        except Exception as e:
            logger.error(f"Error finding VM interface port: {e}")
            return None

    async def hot_connect(
        self,
        lab_id: str,
        source_node: str,
        source_interface: int,
        target_node: str,
        target_interface: int,
    ) -> bool:
        """Connect two VM interfaces by matching their VLAN tags.

        This creates a layer 2 link between two VM interfaces by setting
        them to the same VLAN on the OVS bridge.

        Args:
            lab_id: Lab identifier
            source_node: Source node name
            source_interface: Source interface index (0-based)
            target_node: Target node name
            target_interface: Target interface index (0-based)

        Returns:
            True if successful
        """
        # Get the VLAN tags for both interfaces
        source_vlans = self.get_node_vlans(lab_id, source_node)
        target_vlans = self.get_node_vlans(lab_id, target_node)

        if source_interface >= len(source_vlans):
            logger.error(f"Source interface {source_interface} not found for {source_node}")
            return False
        if target_interface >= len(target_vlans):
            logger.error(f"Target interface {target_interface} not found for {target_node}")
            return False

        # Use the source VLAN for both interfaces (they need to match)
        shared_vlan = source_vlans[source_interface]

        # Find the OVS ports for both interfaces
        source_port = await self.get_vm_interface_port(lab_id, source_node, source_interface)
        target_port = await self.get_vm_interface_port(lab_id, target_node, target_interface)

        if not source_port:
            logger.error(f"Could not find OVS port for {source_node} interface {source_interface}")
            return False
        if not target_port:
            logger.error(f"Could not find OVS port for {target_node} interface {target_interface}")
            return False

        # Set both ports to the same VLAN
        try:
            for port in [source_port, target_port]:
                result = subprocess.run(
                    ["ovs-vsctl", "set", "port", port, f"tag={shared_vlan}"],
                    capture_output=True,
                    text=True,
                )
                if result.returncode != 0:
                    logger.error(f"Failed to set VLAN on port {port}: {result.stderr}")
                    return False

            logger.info(
                f"Connected {source_node}:{source_interface} <-> {target_node}:{target_interface} "
                f"via VLAN {shared_vlan}"
            )
            return True

        except Exception as e:
            logger.error(f"Error during hot_connect: {e}")
            return False

    async def _run_post_boot_commands(
        self,
        domain_name: str,
        kind: str,
    ) -> bool:
        """Run vendor-specific post-boot commands on a VM.

        This handles workarounds like disabling paging or DNS lookups
        that need to be executed after the VM is ready.

        Args:
            domain_name: Libvirt domain name
            kind: Device kind for looking up vendor config

        Returns:
            True if commands were run (or already completed), False on error
        """
        from agent.console_extractor import run_vm_post_boot_commands, PEXPECT_AVAILABLE

        if not PEXPECT_AVAILABLE:
            logger.debug("pexpect not available, skipping post-boot commands")
            return True  # Not an error, just skip

        try:
            result = await asyncio.to_thread(
                run_vm_post_boot_commands,
                domain_name,
                kind,
                self._uri,
            )
            return result.success
        except Exception as e:
            logger.warning(f"Post-boot commands failed for {domain_name}: {e}")
            return False

    async def check_readiness(
        self,
        lab_id: str,
        node_name: str,
        kind: str,
    ) -> ReadinessResult:
        """Check if a VM has finished booting and is ready.

        This uses the serial console output to detect boot completion
        patterns defined in the vendor config. When the VM becomes ready,
        post-boot commands are automatically executed (once).

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

        # Apply per-node readiness metadata overrides when present.
        overrides = self._get_domain_readiness_overrides(domain)
        effective_probe = overrides.get("readiness_probe") or get_libvirt_config(kind).readiness_probe

        # Only use management-IP/SSH gating when readiness probe explicitly asks
        # for SSH readiness. Console method alone is not sufficient.
        if get_console_method(kind) == "ssh" and effective_probe in {"ssh", "tcp_ssh", "management_ssh"}:
            ip = await self._get_vm_management_ip(domain_name)
            if not ip:
                return ReadinessResult(
                    is_ready=False,
                    message="Waiting for management IP",
                    progress_percent=30,
                )

            ssh_ready = await asyncio.to_thread(self._check_tcp_port, ip, 22, 2.0)
            if not ssh_ready:
                return ReadinessResult(
                    is_ready=False,
                    message=f"Management IP {ip} reachable, waiting for SSH",
                    progress_percent=70,
                )

            return ReadinessResult(
                is_ready=True,
                message=f"Management SSH ready on {ip}",
                progress_percent=100,
            )

        probe = get_libvirt_probe(
            kind,
            domain_name,
            self._uri,
            readiness_probe=overrides.get("readiness_probe"),
            readiness_pattern=overrides.get("readiness_pattern"),
        )

        # Run the probe
        result = await probe.check(node_name)

        # If ready, run post-boot commands (idempotent - only runs once)
        if result.is_ready:
            await self._run_post_boot_commands(domain_name, kind)

        return result

    @staticmethod
    def _check_tcp_port(host: str, port: int, timeout: float) -> bool:
        """Return True when TCP port is connectable."""
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except Exception:
            return False

    def get_readiness_timeout(
        self,
        kind: str,
        lab_id: str | None = None,
        node_name: str | None = None,
    ) -> int:
        """Get the readiness timeout for a device type.

        Args:
            kind: Device kind

        Returns:
            Timeout in seconds
        """
        if lab_id and node_name:
            domain_name = self._domain_name(lab_id, node_name)
            try:
                domain = self.conn.lookupByName(domain_name)
                overrides = self._get_domain_readiness_overrides(domain)
                timeout = overrides.get("readiness_timeout")
                if isinstance(timeout, int) and timeout > 0:
                    return timeout
            except Exception:
                pass
        return get_readiness_timeout(kind)

    def get_node_kind(self, lab_id: str, node_name: str) -> str | None:
        """Get the device kind for a VM node from its domain metadata.

        Args:
            lab_id: Lab identifier
            node_name: Node name

        Returns:
            Device kind string, or None if not found
        """
        domain_name = self._domain_name(lab_id, node_name)
        try:
            domain = self.conn.lookupByName(domain_name)
            return self._get_domain_kind(domain)
        except Exception:
            return None

    def _get_domain_kind(self, domain) -> str | None:
        """Get the device kind for a libvirt domain.

        The kind is stored in domain metadata during deployment.

        Args:
            domain: libvirt domain object

        Returns:
            Device kind string, or None if not found
        """
        try:
            metadata = self._get_domain_metadata_values(domain)
            return metadata.get("kind")
        except Exception as e:
            logger.debug(f"Error getting domain kind: {e}")
        return None

    def _get_domain_readiness_overrides(self, domain) -> dict[str, Any]:
        """Read per-node readiness overrides from libvirt domain metadata."""
        values = self._get_domain_metadata_values(domain)
        overrides: dict[str, Any] = {}
        probe = values.get("readiness_probe")
        pattern = values.get("readiness_pattern")
        timeout_raw = values.get("readiness_timeout")
        if probe:
            overrides["readiness_probe"] = probe
        if pattern:
            overrides["readiness_pattern"] = pattern
        if timeout_raw:
            try:
                timeout = int(timeout_raw)
                if timeout > 0:
                    overrides["readiness_timeout"] = timeout
            except (TypeError, ValueError):
                logger.debug(f"Invalid readiness_timeout metadata value: {timeout_raw}")
        return overrides

    def _get_domain_metadata_values(self, domain) -> dict[str, str]:
        """Extract archetype metadata values from domain XML by local tag name."""
        import xml.etree.ElementTree as ET

        xml = domain.XMLDesc()
        root = ET.fromstring(xml)
        metadata = root.find("metadata")
        if metadata is None:
            return {}

        values: dict[str, str] = {}
        for elem in metadata.iter():
            local_tag = elem.tag.split("}")[-1] if isinstance(elem.tag, str) else ""
            if local_tag in {
                "kind",
                "readiness_probe",
                "readiness_pattern",
                "readiness_timeout",
            }:
                text = (elem.text or "").strip()
                if text:
                    values[local_tag] = text
        return values

    def get_runtime_profile(self, lab_id: str, node_name: str) -> dict[str, Any]:
        """Get runtime configuration for a VM node from live libvirt XML."""
        import xml.etree.ElementTree as ET

        domain_name = self._domain_name(lab_id, node_name)
        domain = self.conn.lookupByName(domain_name)
        state, _ = domain.state()
        state_map = {
            libvirt.VIR_DOMAIN_RUNNING: "running",
            libvirt.VIR_DOMAIN_SHUTOFF: "stopped",
            libvirt.VIR_DOMAIN_SHUTDOWN: "stopping",
            libvirt.VIR_DOMAIN_PAUSED: "paused",
            libvirt.VIR_DOMAIN_CRASHED: "crashed",
            libvirt.VIR_DOMAIN_NOSTATE: "unknown",
            libvirt.VIR_DOMAIN_BLOCKED: "blocked",
            libvirt.VIR_DOMAIN_PMSUSPENDED: "suspended",
        }
        xml = domain.XMLDesc()
        root = ET.fromstring(xml)

        def _text(path: str) -> str | None:
            el = root.find(path)
            if el is None or el.text is None:
                return None
            txt = el.text.strip()
            return txt if txt else None

        memory_mb = None
        memory_elem = root.find("memory")
        memory_text = _text("memory")
        if memory_text:
            try:
                memory_value = int(memory_text)
                unit = (memory_elem.attrib.get("unit") if memory_elem is not None else "KiB") or "KiB"
                unit_l = unit.lower()
                if unit_l in ("kib", "kb"):
                    memory_mb = int(memory_value / 1024)
                elif unit_l in ("mib", "mb"):
                    memory_mb = memory_value
                elif unit_l in ("gib", "gb"):
                    memory_mb = memory_value * 1024
                else:
                    memory_mb = int(memory_value / 1024)
            except ValueError:
                pass

        cpu = None
        vcpu_text = _text("vcpu")
        if vcpu_text:
            try:
                cpu = int(vcpu_text)
            except ValueError:
                pass

        machine_type = None
        domain_driver = None
        efi_boot = None
        efi_vars = None
        os_elem = root.find("os")
        domain_driver = root.attrib.get("type")
        os_type = root.find("os/type")
        if os_type is not None:
            machine_type = os_type.attrib.get("machine")
        if os_elem is not None:
            firmware = (os_elem.attrib.get("firmware") or "").lower()
            if firmware:
                efi_boot = firmware == "efi"
            if efi_boot:
                efi_vars = "stateful" if os_elem.find("nvram") is not None else "stateless"
        # Also detect stateless EFI via qemu:commandline pflash passthrough
        qemu_ns = "http://libvirt.org/schemas/domain/qemu/1.0"
        for qarg in root.findall(f"{{{qemu_ns}}}commandline/{{{qemu_ns}}}arg"):
            val = qarg.attrib.get("value", "")
            if "if=pflash" in val and "readonly=on" in val:
                efi_boot = True
                efi_vars = "stateless"
                break

        disk_driver = None
        disk_source = None
        for disk in root.findall("devices/disk"):
            if disk.attrib.get("device") != "disk":
                continue
            target = disk.find("target")
            if target is not None:
                disk_driver = target.attrib.get("bus")
            source = disk.find("source")
            if source is not None:
                disk_source = source.attrib.get("file")
            break

        nic_driver = None
        iface = root.find("devices/interface/model")
        if iface is not None:
            nic_driver = iface.attrib.get("type")

        metadata = self._get_domain_metadata_values(domain)

        return {
            "provider": "libvirt",
            "node_name": node_name,
            "domain_name": domain_name,
            "state": state_map.get(state, "unknown"),
            "runtime": {
                "memory": memory_mb,
                "cpu": cpu,
                "machine_type": machine_type,
                "libvirt_driver": domain_driver,
                "efi_boot": efi_boot,
                "efi_vars": efi_vars,
                "disk_driver": disk_driver,
                "nic_driver": nic_driver,
                "disk_source": disk_source,
                "kind": metadata.get("kind"),
                "readiness_probe": metadata.get("readiness_probe"),
                "readiness_pattern": metadata.get("readiness_pattern"),
                "readiness_timeout": (
                    int(metadata["readiness_timeout"])
                    if metadata.get("readiness_timeout", "").isdigit()
                    else None
                ),
            },
        }

    async def _extract_config(
        self,
        lab_id: str,
        node_name: str,
        kind: str,
    ) -> tuple[str, str] | None:
        """Extract running config from a single VM.

        Supports:
        - config_extract_method="serial": Use virsh console + pexpect
        - config_extract_method="ssh": Use SSH to VM's management IP

        Args:
            lab_id: Lab identifier
            node_name: Node name within the lab
            kind: Device kind for vendor config lookup

        Returns:
            Tuple of (node_name, config_content) or None if extraction failed
        """
        domain_name = self._domain_name(lab_id, node_name)

        # Check VM is running
        try:
            domain = self.conn.lookupByName(domain_name)
            state, _ = domain.state()
            if state != libvirt.VIR_DOMAIN_RUNNING:
                logger.warning(f"Cannot extract config from {node_name}: VM not running")
                return None
        except libvirt.libvirtError:
            logger.warning(f"Cannot extract config from {node_name}: domain not found")
            return None

        # Check extraction method
        extraction_settings = get_config_extraction_settings(kind)

        if extraction_settings.method == "ssh":
            # Extract via SSH
            config = await self._extract_config_via_ssh(domain_name, kind, node_name)
            if config:
                # Guard against obvious non-config noise while allowing
                # legitimately small configs on freshly booted devices.
                compact = config.strip()
                if len(compact) < 64 and not re.search(
                    r"(version|hostname|interface|current configuration|^!$)",
                    compact,
                    re.IGNORECASE | re.MULTILINE,
                ):
                    logger.warning(
                        f"Discarding suspiciously short extracted config for {node_name} via SSH "
                        f"({len(compact)} bytes)"
                    )
                    return None
                logger.info(f"Extracted config from {node_name} via SSH ({len(config)} bytes)")
                return (node_name, config)
            return None

        elif extraction_settings.method == "serial":
            # Extract via serial console (pexpect)
            from agent.console_extractor import extract_vm_config, PEXPECT_AVAILABLE

            if not PEXPECT_AVAILABLE:
                logger.warning("pexpect not available, skipping VM config extraction")
                return None

            # Run extraction in thread pool to avoid blocking
            result = await asyncio.to_thread(
                extract_vm_config,
                domain_name,
                kind,
                self._uri,
            )

            if result.success:
                # Extra safety net even if extractor reported success.
                compact = result.config.strip()
                if len(compact) < 64 and not re.search(
                    r"(version|hostname|interface|current configuration|^!$)",
                    compact,
                    re.IGNORECASE | re.MULTILINE,
                ):
                    logger.warning(
                        f"Discarding suspiciously short extracted config for {node_name} "
                        f"({len(compact)} bytes)"
                    )
                    return None
                logger.info(f"Extracted config from {node_name} ({len(result.config)} bytes)")
                return (node_name, result.config)
            else:
                logger.warning(f"Failed to extract config from {node_name}: {result.error}")
                return None

        else:
            logger.debug(f"No extraction method for {node_name} (method={extraction_settings.method})")
            return None

    async def _extract_config_via_ssh(
        self,
        domain_name: str,
        kind: str,
        node_name: str,
    ) -> str | None:
        """Extract config from VM via SSH.

        Args:
            domain_name: Libvirt domain name
            kind: Device kind for credential and command lookup
            node_name: Node name for logging

        Returns:
            Config content string or None on failure
        """
        try:
            # Get VM IP address
            ip = await self._get_vm_management_ip(domain_name)
            if not ip:
                logger.warning(f"No IP address found for SSH extraction from {node_name}")
                return None

            # Get extraction settings
            extraction_settings = get_config_extraction_settings(kind)
            user = extraction_settings.user or "admin"
            password = extraction_settings.password or "admin"
            cmd = extraction_settings.command

            if not cmd:
                logger.warning(f"No extraction command for {kind}, skipping {node_name}")
                return None

            # Run SSH command with sshpass
            proc = await asyncio.create_subprocess_exec(
                "sshpass", "-p", password,
                "ssh",
                "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
                "-o", "LogLevel=ERROR",
                "-o", "ConnectTimeout=10",
                f"{user}@{ip}",
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                stderr_str = stderr.decode("utf-8") if stderr else ""
                logger.warning(
                    f"SSH extraction failed for {node_name}: "
                    f"exit={proc.returncode}, stderr={stderr_str}"
                )
                return None

            return stdout.decode("utf-8") if stdout else None

        except Exception as e:
            logger.error(f"SSH extraction failed for {node_name}: {e}")
            return None

    async def _extract_all_vm_configs(
        self,
        lab_id: str,
        workspace: Path,
    ) -> list[tuple[str, str]]:
        """Extract running configs from all VMs in a lab.

        Returns list of (node_name, config_content) tuples.
        Also saves configs to workspace/configs/{node}/startup-config.

        Args:
            lab_id: Lab identifier
            workspace: Lab workspace path

        Returns:
            List of (node_name, config_content) tuples
        """
        extracted = []
        prefix = self._lab_prefix(lab_id)

        try:
            # Get all running VMs for this lab
            all_domains = self.conn.listAllDomains(libvirt.VIR_CONNECT_LIST_DOMAINS_ACTIVE)

            for domain in all_domains:
                name = domain.name()
                if not name.startswith(prefix + "-"):
                    continue

                node_name = name[len(prefix) + 1:]

                # Get device kind from domain metadata
                kind = self._get_domain_kind(domain)
                if not kind:
                    logger.warning(f"Unknown device kind for {node_name}, skipping extraction")
                    continue

                result = await self._extract_config(lab_id, node_name, kind)
                if result:
                    node_name, config = result

                    # Save to workspace
                    config_dir = workspace / "configs" / node_name
                    config_dir.mkdir(parents=True, exist_ok=True)
                    config_path = config_dir / "startup-config"
                    config_path.write_text(config)
                    logger.info(f"Saved config to {config_path}")

                    extracted.append((node_name, config))

        except Exception as e:
            logger.error(f"Error during VM config extraction for lab {lab_id}: {e}")

        return extracted

    async def discover_labs(self) -> dict[str, list[NodeInfo]]:
        """Discover all running labs managed by this provider.

        Enumerates all libvirt domains with the 'arch-' prefix and groups
        them by lab_id. Domain naming convention: arch-{lab_id}-{node_name}

        Returns:
            Dict mapping lab_id -> list of NodeInfo for each lab's VMs.
        """
        discovered: dict[str, list[NodeInfo]] = {}

        try:
            # Get all domains (running and defined/stopped)
            all_domains = self.conn.listAllDomains(0)

            for domain in all_domains:
                name = domain.name()

                # Only process Archetype-managed domains
                if not name.startswith("arch-"):
                    continue

                # Parse domain name: arch-{lab_id}-{node_name}
                # lab_id may contain hyphens, but we truncate to 20 chars in _lab_prefix
                parts = name.split("-", 2)  # Split into at most 3 parts
                if len(parts) < 3:
                    logger.debug(f"Skipping malformed domain name: {name}")
                    continue

                # parts[0] = "arch", parts[1] = lab_id, parts[2] = node_name
                lab_id = parts[1]
                node_name = parts[2]

                node = NodeInfo(
                    name=node_name,
                    status=self._get_domain_status(domain),
                    container_id=domain.UUIDString()[:12],
                )

                if lab_id not in discovered:
                    discovered[lab_id] = []
                discovered[lab_id].append(node)

            logger.info(f"Discovered {len(discovered)} labs with LibvirtProvider")

        except Exception as e:
            logger.error(f"Error discovering labs: {e}")

        return discovered

    async def cleanup_orphan_domains(
        self,
        valid_lab_ids: set[str],
        workspace_base: Path | None = None,
    ) -> dict[str, list[str]]:
        """Remove VMs for labs that no longer exist.

        Discovers all Archetype-managed domains and removes those belonging
        to labs not in the valid_lab_ids set. Also cleans up associated
        disk overlays and VLAN allocations.

        Args:
            valid_lab_ids: Set of lab IDs that are known to be valid.
            workspace_base: Base workspace path for disk cleanup.
                           If None, skips disk cleanup.

        Returns:
            Dict with keys 'domains', 'disks' listing removed items.
        """
        removed: dict[str, list[str]] = {"domains": [], "disks": []}

        try:
            # Discover all labs managed by this provider
            discovered = await self.discover_labs()

            for lab_id, nodes in discovered.items():
                # Check if this lab_id is in the valid set
                # Handle both exact matches and prefix matches (for truncated IDs)
                is_orphan = lab_id not in valid_lab_ids
                if is_orphan:
                    # Also check for prefix matches (lab IDs may be truncated to 20 chars)
                    is_orphan = not any(
                        vid.startswith(lab_id) or lab_id.startswith(vid[:20])
                        for vid in valid_lab_ids
                    )

                if not is_orphan:
                    continue

                logger.info(f"Cleaning up orphan lab: {lab_id} ({len(nodes)} VMs)")

                for node in nodes:
                    domain_name = self._domain_name(lab_id, node.name)

                    try:
                        domain = self.conn.lookupByName(domain_name)

                        # Force stop if running
                        state, _ = domain.state()
                        if state == libvirt.VIR_DOMAIN_RUNNING:
                            logger.info(f"Force stopping orphan domain: {domain_name}")
                            domain.destroy()

                        # Undefine domain (remove from libvirt)
                        self._undefine_domain(domain, domain_name)
                        removed["domains"].append(domain_name)
                        logger.info(f"Removed orphan domain: {domain_name}")

                    except libvirt.libvirtError as e:
                        logger.warning(f"Error removing orphan domain {domain_name}: {e}")

                # Clean up disk overlays if workspace provided
                if workspace_base:
                    lab_workspace = workspace_base / lab_id
                    disks_dir = lab_workspace / "disks"
                    if disks_dir.exists():
                        for disk_file in disks_dir.iterdir():
                            try:
                                disk_file.unlink()
                                removed["disks"].append(str(disk_file))
                                logger.info(f"Removed orphan disk: {disk_file}")
                            except Exception as e:
                                logger.warning(f"Failed to remove disk {disk_file}: {e}")
                        # Try to remove the disks directory
                        try:
                            disks_dir.rmdir()
                        except Exception:
                            pass  # Directory may not be empty

                # Clean up VLAN allocations (in-memory and on disk)
                if lab_id in self._vlan_allocations:
                    del self._vlan_allocations[lab_id]
                    logger.debug(f"Freed VLAN allocations for orphan lab: {lab_id}")
                if lab_id in self._next_vlan:
                    del self._next_vlan[lab_id]
                if workspace_base:
                    self._remove_vlan_file(lab_id, lab_workspace)

            if removed["domains"]:
                logger.info(
                    f"Orphan cleanup complete: removed {len(removed['domains'])} domains, "
                    f"{len(removed['disks'])} disks"
                )

        except Exception as e:
            logger.error(f"Error during orphan cleanup: {e}")

        return removed

    async def cleanup_orphan_containers(
        self,
        valid_lab_ids: set[str],
    ) -> list[str]:
        """Remove VMs for labs that no longer exist (API-compatible method).

        This is an alias for cleanup_orphan_domains that returns just the
        list of removed domain names, matching Docker provider's signature.

        Args:
            valid_lab_ids: Set of lab IDs that are known to be valid.

        Returns:
            List of removed domain names.
        """
        result = await self.cleanup_orphan_domains(valid_lab_ids)
        return result.get("domains", [])

    async def cleanup_lab_orphan_domains(
        self,
        lab_id: str,
        keep_node_names: set[str],
        workspace_base: Path | None = None,
    ) -> dict[str, list[str]]:
        """Remove VMs for nodes that no longer exist within a specific lab.

        Used when nodes are deleted from a lab's topology. Finds all VMs for
        this lab and removes those not in the keep_node_names set.

        Args:
            lab_id: Lab identifier to clean up.
            keep_node_names: Set of node names that should be kept.
            workspace_base: Base workspace path for disk cleanup.
                           If None, skips disk cleanup.

        Returns:
            Dict with keys 'domains', 'disks' listing removed items.
        """
        removed: dict[str, list[str]] = {"domains": [], "disks": []}

        try:
            # Get the lab prefix to find all domains for this lab
            lab_prefix = self._lab_prefix(lab_id)

            # Get all domains (running and defined/stopped)
            all_domains = self.conn.listAllDomains(0)

            for domain in all_domains:
                name = domain.name()

                # Only process domains for this lab
                if not name.startswith(lab_prefix + "-"):
                    continue

                # Parse node name from domain name: arch-{lab_id}-{node_name}
                parts = name.split("-", 2)
                if len(parts) < 3:
                    continue
                node_name = parts[2]

                if node_name in keep_node_names:
                    logger.debug(f"Keeping VM {name} (node {node_name} still in topology)")
                    continue

                # This VM is for a node not in topology - remove it
                try:
                    logger.info(f"Removing orphan VM {name} (node {node_name} deleted from topology)")

                    # Force stop if running
                    state, _ = domain.state()
                    if state == libvirt.VIR_DOMAIN_RUNNING:
                        logger.info(f"Force stopping orphan domain: {name}")
                        domain.destroy()

                    # Undefine domain (remove from libvirt)
                    self._undefine_domain(domain, name)
                    removed["domains"].append(name)

                    # Clean up VLAN allocation for this node
                    if lab_id in self._vlan_allocations:
                        if node_name in self._vlan_allocations[lab_id]:
                            del self._vlan_allocations[lab_id][node_name]
                            logger.debug(f"Freed VLAN allocations for orphan node: {node_name}")

                except libvirt.libvirtError as e:
                    logger.warning(f"Error removing orphan domain {name}: {e}")

            # Clean up disk overlays for removed nodes
            if workspace_base and removed["domains"]:
                lab_workspace = workspace_base / lab_id
                disks_dir = lab_workspace / "disks"
                if disks_dir.exists():
                    for domain_name in removed["domains"]:
                        # Parse node_name from domain_name
                        parts = domain_name.split("-", 2)
                        if len(parts) < 3:
                            continue
                        node_name = parts[2]

                        # Look for disk files matching this node
                        for disk_file in disks_dir.iterdir():
                            if disk_file.name.startswith(node_name):
                                try:
                                    disk_file.unlink()
                                    removed["disks"].append(str(disk_file))
                                    logger.info(f"Removed orphan disk: {disk_file}")
                                except Exception as e:
                                    logger.warning(f"Failed to remove disk {disk_file}: {e}")

            if removed["domains"]:
                logger.info(
                    f"Lab orphan cleanup complete for {lab_id}: removed {len(removed['domains'])} VMs, "
                    f"{len(removed['disks'])} disks"
                )

        except Exception as e:
            logger.error(f"Error during lab orphan cleanup for {lab_id}: {e}")

        return removed
