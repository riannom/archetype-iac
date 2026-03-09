"""Libvirt provider for VM-based network labs.

This provider uses libvirt/QEMU to run virtual machine-based network devices
like Cisco IOS-XRv, FTDv, vManage, etc.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
from functools import partial
import hashlib
import logging
import os
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.schemas import DeployTopology


from agent.config import settings
from agent.providers.naming import libvirt_domain_name as _libvirt_name, sanitize_id
from agent.providers.base import (
    DeployResult,
    DestroyResult,
    NodeActionResult,
    NodeInfo,
    NodeStatus,
    Provider,
    RuntimeConflictProbeResult,
    StatusResult,
    VlanPersistenceMixin,
)
from agent.readiness import ReadinessResult, get_readiness_timeout
from agent.vendors import (
    get_kind_for_device,
    get_console_credentials,
    get_console_method,
    get_libvirt_config,
    get_vendor_config,
)
from agent.network.ovs_vlan_tags import used_vlan_tags_on_bridge_from_ovs_outputs
from agent.metrics import runtime_identity_skips

# Companion modules — extracted from this file to reduce size.
from agent.providers.libvirt_xml import (
    generate_mac_address as _generate_mac_address,
    generate_ovs_interface_id as _generate_ovs_interface_id,
    find_ovmf_code_path as _find_ovmf_code_path,
    find_ovmf_vars_template as _find_ovmf_vars_template,
    resolve_domain_driver as _resolve_domain_driver,
    translate_container_path_to_host as _translate_container_path_to_host,
    create_overlay_disk_sync as _create_overlay_disk_sync,
    create_data_volume_sync as _create_data_volume_sync,
    patch_vjunos_svm_compat as _patch_vjunos_svm_compat,
    allocate_tcp_serial_port as _allocate_tcp_serial_port,
    get_tcp_serial_port as _get_tcp_serial_port,
    generate_domain_xml as _generate_domain_xml,
)
from agent.providers.libvirt_n9kv import (
    _N9KV_CONFIG_PREAMBLE,
    n9kv_poap_network_name as _n9kv_poap_network_name,
    n9kv_poap_bridge_name as _n9kv_poap_bridge_name,
    n9kv_poap_subnet as _n9kv_poap_subnet,
    n9kv_poap_config_url as _n9kv_poap_config_url,
    n9kv_poap_tftp_root as _n9kv_poap_tftp_root,
    n9kv_poap_bootfile_name as _n9kv_poap_bootfile_name,
    stage_n9kv_poap_tftp_script as _stage_n9kv_poap_tftp_script,
    ensure_n9kv_poap_network as _ensure_n9kv_poap_network,
    teardown_n9kv_poap_network as _teardown_n9kv_poap_network,
    ensure_libvirt_network as _ensure_libvirt_network,
    node_uses_dedicated_mgmt_interface as _node_uses_dedicated_mgmt_interface,
    resolve_management_network as _resolve_management_network,
)
from agent.providers.libvirt_readiness import (
    extract_probe_markers as _extract_probe_markers,
    classify_console_result as _classify_console_result,
    check_tcp_port as _check_tcp_port,
    run_post_boot_commands as _run_post_boot_commands,
    run_n9kv_loader_recovery as _run_n9kv_loader_recovery,
    run_n9kv_panic_recovery as _run_n9kv_panic_recovery,
    run_n9kv_poap_skip as _run_n9kv_poap_skip,
    run_n9kv_admin_password_setup as _run_n9kv_admin_password_setup,
    check_readiness as _check_readiness,
)
from agent.providers.libvirt_config import (
    get_vm_management_ip as _get_vm_management_ip,
    extract_config as _extract_config,
    extract_config_via_ssh as _extract_config_via_ssh,
    prepare_startup_config_for_injection as _prepare_startup_config_for_injection,
    format_injection_diagnostics as _format_injection_diagnostics,
)

logger = logging.getLogger(__name__)
ARCHETYPE_LIBVIRT_NS = "http://archetype.io/libvirt/1"


def _log_name(node_name: str, node_config: dict) -> str:
    """Format node name for logging: 'DisplayName(id)' or just 'id'."""
    display_name = node_config.get("_display_name") if isinstance(node_config, dict) else None
    if display_name and display_name != node_name:
        return f"{display_name}({node_name})"
    return node_name


def _update_domain_identity_metadata_xml(
    xml_text: str,
    *,
    lab_id: str,
    node_name: str,
    node_definition_id: str,
    provider: str,
) -> str:
    """Upsert runtime identity fields in a libvirt domain XML document."""
    ET.register_namespace("archetype", ARCHETYPE_LIBVIRT_NS)
    root = ET.fromstring(xml_text)
    metadata = root.find("metadata")
    if metadata is None:
        metadata = ET.SubElement(root, "metadata")

    node_elem = None
    for child in metadata:
        local_tag = child.tag.split("}")[-1] if isinstance(child.tag, str) else ""
        if local_tag == "node":
            node_elem = child
            break
    if node_elem is None:
        node_elem = ET.SubElement(metadata, f"{{{ARCHETYPE_LIBVIRT_NS}}}node")

    def _upsert(local_tag: str, value: str) -> None:
        elem = None
        for child in node_elem:
            child_local = child.tag.split("}")[-1] if isinstance(child.tag, str) else ""
            if child_local == local_tag:
                elem = child
                break
        if elem is None:
            elem = ET.SubElement(node_elem, f"{{{ARCHETYPE_LIBVIRT_NS}}}{local_tag}")
        elem.text = value

    _upsert("lab_id", lab_id)
    _upsert("node_name", node_name)
    _upsert("node_definition_id", node_definition_id)
    _upsert("provider", provider)

    return ET.tostring(root, encoding="unicode")


def _build_domain_identity_metadata_fragment(
    *,
    lab_id: str,
    node_name: str,
    node_definition_id: str,
    provider: str,
) -> str:
    """Build libvirt metadata XML fragment for runtime identity fields."""
    ET.register_namespace("archetype", ARCHETYPE_LIBVIRT_NS)
    node_elem = ET.Element(f"{{{ARCHETYPE_LIBVIRT_NS}}}node")

    for local_tag, value in (
        ("lab_id", lab_id),
        ("node_name", node_name),
        ("node_definition_id", node_definition_id),
        ("provider", provider),
    ):
        elem = ET.SubElement(node_elem, f"{{{ARCHETYPE_LIBVIRT_NS}}}{local_tag}")
        elem.text = value

    return ET.tostring(node_elem, encoding="unicode")


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
import sys, os, socket, select, struct, tty, termios, time

port = int(sys.argv[1])
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.settimeout(10)
# Retry connection — chardev reset may briefly take the port offline
for attempt in range(6):
    try:
        sock.connect(("127.0.0.1", port))
        break
    except ConnectionRefusedError:
        if attempt < 5:
            time.sleep(0.5)
            sock.close()
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10)
        else:
            sys.stderr.write(f"Failed to connect to TCP serial port {port} after retries\n")
            sys.exit(1)
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


# _N9KV_CONFIG_PREAMBLE is imported from libvirt_n9kv and re-exported
# so that external callers (e.g., tests) can still do:
#   from agent.providers.libvirt import _N9KV_CONFIG_PREAMBLE


def _coalesce(node_val, default):
    """Return node_val if not None, else default."""
    return node_val if node_val is not None else default


class LibvirtProvider(Provider, VlanPersistenceMixin):
    """Provider for libvirt/QEMU-based virtual machine labs.

    Uses libvirt API for VM lifecycle management and QEMU for
    disk overlay creation and console access.
    """

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
    # Loader recovery constants.
    _N9KV_LOADER_RECOVERY_MAX_ATTEMPTS: int = 5
    _N9KV_LOADER_RECOVERY_COOLDOWN: float = 30.0
    # Kernel panic recovery constants.
    _N9KV_PANIC_RECOVERY_MAX_ATTEMPTS: int = 3
    _N9KV_PANIC_RECOVERY_COOLDOWN: float = 60.0

    def __init__(self):
        if not LIBVIRT_AVAILABLE:
            raise ImportError("libvirt-python package is not installed")
        self._conn: libvirt.virConnect | None = None
        self._uri = getattr(settings, 'libvirt_uri', 'qemu:///system')
        self.__init_vlan_state__()
        # Single-thread executor for all libvirt calls.
        # Libvirt Python bindings are NOT thread-safe — serializing all
        # conn.* calls to one dedicated thread avoids races without locks
        # while keeping the asyncio event loop free for /healthz etc.
        self._libvirt_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="libvirt",
        )
        # N9Kv per-VM lifecycle state (mutable, per-instance).
        self._n9kv_loader_recovery_attempts: dict[str, int] = {}
        self._n9kv_loader_recovery_last_at: dict[str, float] = {}
        self._n9kv_poap_skip_attempted: set[str] = set()
        self._n9kv_admin_password_completed: set[str] = set()
        self._n9kv_panic_recovery_attempts: dict[str, int] = {}
        self._n9kv_panic_recovery_last_at: dict[str, float] = {}
        self._n9kv_panic_last_log_size: dict[str, int] = {}
        # Carrier monitor: cached VM port -> MonitoredPort mapping.
        self._vm_port_cache: dict = {}

    async def _run_libvirt(self, func, *args, **kwargs):
        """Run a blocking function on the dedicated libvirt thread."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._libvirt_executor, partial(func, *args, **kwargs),
        )

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

    def get_vm_stats_sync(self) -> list[dict]:
        """Collect VM stats for resource reporting (must run on libvirt thread).

        Returns a list of dicts with name, status, lab_prefix, node_name
        for all Archetype-managed domains.
        """
        results = []
        try:
            all_domains = self.conn.listAllDomains(0)
            for domain in all_domains:
                name = domain.name()
                metadata = self._get_domain_metadata_values(domain)
                lab_id = metadata.get("lab_id")
                node_name = metadata.get("node_name")
                if not lab_id or not node_name:
                    continue
                state, _ = domain.state()
                is_running = state == libvirt.VIR_DOMAIN_RUNNING
                # domain.info() -> [state, maxMem_kb, mem_kb, nrVirtCpu, cpuTime]
                info = domain.info()
                results.append({
                    "name": name,
                    "status": "running" if is_running else "stopped",
                    "lab_prefix": sanitize_id(lab_id, max_len=20),
                    "node_name": node_name,
                    "is_vm": True,
                    "vcpus": info[3],
                    "memory_mb": info[1] // 1024,  # maxMem (allocated ceiling)
                })
        except Exception as e:
            logger.warning(f"get_vm_stats_sync failed: {type(e).__name__}: {e}")
        return results

    def _domain_name(self, lab_id: str, node_name: str) -> str:
        """Generate libvirt domain name for a node."""
        return _libvirt_name(lab_id, node_name)


    @staticmethod
    def _canonical_kind(kind: str | None) -> str:
        """Resolve aliases and normalize kind identifiers."""
        if not kind:
            return ""
        return (get_kind_for_device(kind) or kind).strip().lower()

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

        This is intentionally a recovery-only path and relies on
        metadata-backed identity only.
        """
        discovered: dict[str, list[int]] = {}
        try:
            for domain in self.conn.listAllDomains(0):
                metadata = self._get_domain_metadata_values(domain)
                node_name = metadata.get("node_name")
                if metadata.get("lab_id") != lab_id or not node_name:
                    continue
                tags = self._extract_domain_vlan_tags(domain)
                if tags:
                    discovered[node_name] = tags
        except Exception:
            return {}
        return discovered

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
            skipped_metadata = 0
            for domain in all_domains:
                metadata = self._get_domain_metadata_values(domain)
                node_name = metadata.get("node_name")
                if metadata.get("lab_id") == lab_id and node_name:
                    existing_nodes.add(node_name)
                elif not metadata.get("lab_id") or not node_name:
                    skipped_metadata += 1

            if skipped_metadata:
                runtime_identity_skips.labels(
                    resource_type="libvirt_domain",
                    operation="recover_stale_network",
                    reason="missing_runtime_metadata",
                ).inc(skipped_metadata)
                logger.info(
                    "Skipped %d libvirt domain(s) without metadata while recovering lab %s",
                    skipped_metadata,
                    lab_id,
                )

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
                    proc = await asyncio.create_subprocess_exec(
                        "ip", "link", "set", port_name, "mtu", str(settings.local_mtu),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    _, stderr_bytes = await proc.communicate()
                    if proc.returncode != 0:
                        logger.warning(f"Failed to set MTU on {port_name}: {stderr_bytes.decode()}")
                except Exception as e:
                    logger.warning(f"Failed to set MTU on {port_name}: {e}")

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

    # -- Disk & image helpers (delegated to libvirt_xml) --

    def _translate_container_path_to_host(self, path: str) -> str:
        return _translate_container_path_to_host(path)

    def _create_overlay_disk_sync(self, base_image: str, overlay_path: Path) -> bool:
        return _create_overlay_disk_sync(base_image, overlay_path)

    async def _create_overlay_disk(self, base_image: str, overlay_path: Path) -> bool:
        return await asyncio.to_thread(_create_overlay_disk_sync, base_image, overlay_path)

    def _create_data_volume_sync(self, path: Path, size_gb: int) -> bool:
        return _create_data_volume_sync(path, size_gb)

    async def _create_data_volume(self, path: Path, size_gb: int) -> bool:
        return await asyncio.to_thread(_create_data_volume_sync, path, size_gb)

    def _generate_mac_address(self, domain_name: str, interface_index: int) -> str:
        return _generate_mac_address(domain_name, interface_index)

    def _generate_ovs_interface_id(
        self,
        domain_name: str,
        interface_role: str,
        interface_index: int,
    ) -> str:
        return _generate_ovs_interface_id(domain_name, interface_role, interface_index)

    def _find_ovmf_code_path(self) -> str | None:
        return _find_ovmf_code_path()

    def _find_ovmf_vars_template(self) -> str | None:
        return _find_ovmf_vars_template()

    def _resolve_domain_driver(self, requested: str | None, node_name: str) -> str:
        return _resolve_domain_driver(requested, node_name, self.ALLOWED_DOMAIN_DRIVERS)

    @staticmethod
    def _patch_vjunos_svm_compat(overlay_path: Path) -> bool:
        return _patch_vjunos_svm_compat(overlay_path)

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
        config_disk_path: Path | None = None,
        serial_log_path: Path | None = None,
    ) -> str:
        """Generate libvirt domain XML for a VM — delegates to libvirt_xml."""
        return _generate_domain_xml(
            name,
            node_config,
            overlay_path,
            data_volume_path=data_volume_path,
            interface_count=interface_count,
            vlan_tags=vlan_tags,
            kind=kind,
            include_management_interface=include_management_interface,
            management_network=management_network,
            config_iso_path=config_iso_path,
            config_disk_path=config_disk_path,
            serial_log_path=serial_log_path,
            valid_machine_types=self.VALID_MACHINE_TYPES,
            valid_disk_drivers=self.VALID_DISK_DRIVERS,
            valid_nic_drivers=self.VALID_NIC_DRIVERS,
            nic_driver_substitutions=self.NIC_DRIVER_SUBSTITUTIONS,
            allowed_domain_drivers=self.ALLOWED_DOMAIN_DRIVERS,
            mac_generator=_generate_mac_address,
        )

    @staticmethod
    def _allocate_tcp_serial_port() -> int:
        return _allocate_tcp_serial_port()

    @staticmethod
    def _get_tcp_serial_port(domain) -> int | None:
        return _get_tcp_serial_port(domain)

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

    def _node_from_domain(self, domain, lab_id: str) -> NodeInfo | None:
        """Convert libvirt domain to NodeInfo."""
        name = domain.name()
        metadata = self._get_domain_metadata_values(domain)

        if metadata.get("lab_id") != lab_id:
            return None

        node_name = metadata.get("node_name")
        if not node_name or not metadata.get("node_definition_id"):
            logger.debug(
                "Skipping metadata-incomplete domain during status: %s",
                name,
            )
            return None

        return NodeInfo(
            name=node_name,
            status=self._get_domain_status(domain),
            container_id=domain.UUIDString()[:12],
            runtime_id=domain.UUIDString(),
            node_definition_id=metadata.get("node_definition_id"),
        )

    def _running_domain_identity_visible(
        self,
        domain_name: str,
        lab_id: str,
        node_name: str,
        node_definition_id: str | None = None,
    ) -> bool:
        """Verify a running domain is visible through metadata-backed status."""
        try:
            domain = self.conn.lookupByName(domain_name)
        except libvirt.libvirtError:
            return False

        node = self._node_from_domain(domain, lab_id)
        if node is None or node.name != node_name:
            return False
        if node_definition_id and node.node_definition_id != node_definition_id:
            return False
        return True

    def _classify_existing_domain_identity(
        self,
        domain,
        *,
        lab_id: str,
        node_name: str,
        node_definition_id: str | None,
    ) -> str:
        """Classify an existing libvirt domain before create."""
        metadata = self._get_domain_metadata_values(domain)
        if not metadata:
            return "foreign"

        provider_name = metadata.get("provider")
        if provider_name not in (None, "", self.name):
            return "foreign"

        existing_lab_id = metadata.get("lab_id")
        existing_node_name = metadata.get("node_name")
        existing_node_definition_id = metadata.get("node_definition_id")

        if not existing_lab_id or not existing_node_name or not existing_node_definition_id:
            return "foreign"
        if existing_lab_id != lab_id or existing_node_name != node_name:
            return "stale_managed"
        if node_definition_id and existing_node_definition_id != node_definition_id:
            return "stale_managed"
        return "expected"

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
        recovered_vlans = await self._run_libvirt(self._recover_stale_network, lab_id, workspace)
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
            c = _coalesce  # short alias for config resolution
            node_config = {
                "image": node.image,
                "lab_id": lab_id,
                "node_name": node_name,
                "node_definition_id": getattr(node, "node_definition_id", None),
                "provider": self.name,
                "memory": c(node.memory, libvirt_config.memory_mb),
                "cpu": c(node.cpu, libvirt_config.cpu_count),
                "cpu_limit": node.cpu_limit,
                "machine_type": c(node.machine_type, libvirt_config.machine_type),
                "disk_driver": c(node.disk_driver, libvirt_config.disk_driver),
                "nic_driver": c(node.nic_driver, libvirt_config.nic_driver),
                "libvirt_driver": c(node.libvirt_driver, "kvm"),
                "efi_boot": c(node.efi_boot, libvirt_config.efi_boot),
                "efi_vars": c(node.efi_vars, libvirt_config.efi_vars),
                "data_volume_gb": c(node.data_volume_gb, libvirt_config.data_volume_gb),
                "readiness_probe": c(node.readiness_probe, libvirt_config.readiness_probe),
                "readiness_pattern": c(node.readiness_pattern, libvirt_config.readiness_pattern),
                "readiness_timeout": c(node.readiness_timeout, libvirt_config.readiness_timeout),
                "serial_type": libvirt_config.serial_type,
                "nographic": libvirt_config.nographic,
                "serial_port_count": libvirt_config.serial_port_count,
                "smbios_product": libvirt_config.smbios_product,
                "reserved_nics": libvirt_config.reserved_nics,
                "cpu_sockets": libvirt_config.cpu_sockets,
                "needs_nested_vmx": libvirt_config.needs_nested_vmx,
                "interface_count": interface_count,
                "_display_name": display_name,
            }
            logger.info(
                f"VM config for {log_name}: {node_config['memory']}MB RAM, "
                f"{node_config['cpu']} vCPU, disk={node_config['disk_driver']}, "
                f"nic={node_config['nic_driver']}, machine={node_config['machine_type']}, "
                f"driver={node_config['libvirt_driver']}, cpu_limit={node_config['cpu_limit']}, interfaces={interface_count}, "
                f"efi_boot={node_config['efi_boot']}, efi_vars={node_config['efi_vars']}"
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

    def _node_precheck_sync(
        self,
        lab_id: str,
        node_name: str,
        domain_name: str,
        workspace: Path,
        disks_dir: Path,
        node_definition_id: str | None = None,
    ) -> tuple[bool, str | None, str | None, str | None]:
        """Shared pre-deployment cleanup — libvirt thread.

        Recovers network state and classifies an existing domain, if present.

        Returns ``(already_running, uuid_short, status, identity)``.
        """
        try:
            self._recover_stale_network(lab_id, workspace)
        except Exception:
            pass

        try:
            existing = self.conn.lookupByName(domain_name)
            if existing:
                state = self._get_domain_status(existing)
                identity = self._classify_existing_domain_identity(
                    existing,
                    lab_id=lab_id,
                    node_name=node_name,
                    node_definition_id=node_definition_id,
                )
                if state == NodeStatus.RUNNING:
                    logger.info(f"Domain {domain_name} already running")
                    return True, existing.UUIDString()[:12], state, identity
                return False, existing.UUIDString()[:12], state, identity
        except libvirt.libvirtError:
            pass
        return False, None, None, None

    def _deploy_node_pre_sync(
        self,
        lab_id: str,
        node_name: str,
        domain_name: str,
        disks_dir: Path,
    ) -> NodeInfo | None:
        """Pre-deploy: recover network state, check existing domain — libvirt thread.

        Returns NodeInfo if domain already running, None to proceed with creation.
        """
        already_running, uuid_short, state, _identity = self._node_precheck_sync(
            lab_id, node_name, domain_name, disks_dir.parent, disks_dir,
        )
        if already_running:
            return NodeInfo(
                name=node_name,
                status=state,
                container_id=uuid_short,
            )
        return None

    def _deploy_node_define_start_sync(
        self,
        domain_name: str,
        xml: str,
        kind: str | None,
    ) -> str:
        """Define and start domain — libvirt thread. Returns domain UUID."""
        domain = self.conn.defineXML(xml)
        if not domain:
            raise RuntimeError(f"Failed to define domain {domain_name}")
        domain.create()
        logger.info(f"Started domain {domain_name}")
        self._clear_vm_post_boot_commands_cache(domain_name)
        self._mark_post_boot_console_ownership_pending(domain_name, kind)
        return domain.UUIDString()[:12]

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

        # Phase 1: check existing domain (libvirt thread)
        existing = await self._run_libvirt(
            self._deploy_node_pre_sync, lab_id, node_name, domain_name, disks_dir,
        )
        if existing is not None:
            return existing

        # Phase 2: disk preparation (async subprocess)
        base_image = self._get_base_image(node_config)
        if not base_image:
            raise ValueError(f"No base image found for node {node_name}")

        overlay_path = disks_dir / f"{node_name}.qcow2"
        if not await self._create_overlay_disk(base_image, overlay_path):
            raise RuntimeError(f"Failed to create overlay disk for {node_name}")

        if node_config.get("needs_nested_vmx", False):
            await asyncio.to_thread(self._patch_vjunos_svm_compat, overlay_path)

        data_volume_path = None
        data_volume_size = node_config.get("data_volume_gb")
        if data_volume_size:
            data_volume_path = disks_dir / f"{node_name}-data.qcow2"
            if not await self._create_data_volume(data_volume_path, data_volume_size):
                raise RuntimeError(f"Failed to create data volume for {node_name}")

        # Phase 3: VLAN allocation + XML generation (libvirt thread for mgmt network)
        interface_count = node_config.get("interface_count", 1)
        reserved_nics = node_config.get("reserved_nics", 0)
        workspace = disks_dir.parent
        vlan_tags = self._allocate_vlans(lab_id, node_name, interface_count + reserved_nics, workspace)

        include_management_interface, management_network = await self._run_libvirt(
            self._resolve_management_network, lab_id, node_name, kind,
        )

        # Serial log for lock-free readiness observation
        serial_log_dir = workspace / "serial-logs"
        serial_log_dir.mkdir(parents=True, exist_ok=True)
        try:
            serial_log_dir.chmod(0o777)
        except OSError:
            pass
        serial_log_path = serial_log_dir / f"{domain_name}.log"
        try:
            serial_log_path.touch(exist_ok=True)
            serial_log_path.chmod(0o666)
        except OSError:
            pass

        xml = self._generate_domain_xml(
            domain_name,
            node_config,
            overlay_path,
            data_volume_path,
            interface_count=interface_count,
            vlan_tags=vlan_tags,
            kind=kind,
            include_management_interface=include_management_interface,
            management_network=management_network,
            serial_log_path=serial_log_path,
        )

        # Phase 4: define + start domain (libvirt thread)
        uuid_str = await self._run_libvirt(
            self._deploy_node_define_start_sync, domain_name, xml, kind,
        )

        # Phase 5: post-start MTU fix (async subprocess)
        await self._set_vm_tap_mtu(lab_id, node_name)

        return NodeInfo(
            name=node_name,
            status=NodeStatus.RUNNING,
            container_id=uuid_str,
        )

    def _destroy_sync(
        self,
        lab_id: str,
        workspace: Path,
    ) -> tuple[int, list[str], str | None]:
        """Destroy all lab domains — runs on the libvirt thread.

        Returns (destroyed_count, errors_list, fatal_error_or_none).
        """
        destroyed_count = 0
        errors: list[str] = []

        try:
            running_domains = self.conn.listAllDomains(libvirt.VIR_CONNECT_LIST_DOMAINS_ACTIVE)
            defined_domains = self.conn.listAllDomains(libvirt.VIR_CONNECT_LIST_DOMAINS_INACTIVE)
            all_domains = running_domains + defined_domains
            skipped_metadata = 0

            for domain in all_domains:
                name = domain.name()
                metadata = self._get_domain_metadata_values(domain)
                node_name = metadata.get("node_name")
                if metadata.get("lab_id") != lab_id:
                    continue
                if not node_name:
                    skipped_metadata += 1
                    continue
                try:
                    state, _ = domain.state()
                    if state == libvirt.VIR_DOMAIN_RUNNING:
                        domain.destroy()
                    self._undefine_domain(domain, name)
                    self._clear_vm_post_boot_commands_cache(name)
                    self._teardown_n9kv_poap_network(lab_id, node_name)
                    destroyed_count += 1
                    logger.info(f"Destroyed domain {name}")
                except libvirt.libvirtError as e:
                    logger.warning(f"Error destroying domain {name}: {e}")
                    errors.append(f"{name}: {e}")

            if skipped_metadata:
                runtime_identity_skips.labels(
                    resource_type="libvirt_domain",
                    operation="destroy",
                    reason="missing_node_metadata",
                ).inc(skipped_metadata)
                logger.info(
                    "Skipped %d libvirt domain(s) without node metadata during destroy for lab %s",
                    skipped_metadata,
                    lab_id,
                )

            # Clean up disk overlays
            disks_dir = self._disks_dir(workspace)
            if disks_dir.exists():
                for disk_file in disks_dir.iterdir():
                    try:
                        disk_file.unlink()
                        logger.info(f"Removed disk: {disk_file}")
                    except Exception as e:
                        logger.warning(f"Failed to remove disk {disk_file}: {e}")

            # Clean up serial logs
            serial_log_dir = workspace / "serial-logs"
            if serial_log_dir.exists():
                import shutil
                shutil.rmtree(serial_log_dir, ignore_errors=True)
                logger.info(f"Removed serial log directory: {serial_log_dir}")

            # Clean up VLAN allocations
            if lab_id in self._vlan_allocations:
                del self._vlan_allocations[lab_id]
            if lab_id in self._next_vlan:
                del self._next_vlan[lab_id]
            self._remove_vlan_file(lab_id, workspace)

            return (destroyed_count, errors, None)
        except Exception as e:
            logger.exception(f"Destroy failed for lab {lab_id}: {e}")
            return (0, errors, str(e))

    async def destroy(
        self,
        lab_id: str,
        workspace: Path,
    ) -> DestroyResult:
        """Destroy a libvirt topology."""
        destroyed_count, errors, fatal = await self._run_libvirt(
            self._destroy_sync, lab_id, workspace,
        )

        if fatal:
            return DestroyResult(success=False, error=fatal)

        # OVS cleanup is async — run outside the libvirt executor
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

    def _status_sync(self, lab_id: str) -> StatusResult:
        """Synchronous status check — runs on the libvirt thread.

        Managed-runtime identity here is metadata-only. Domain names remain a
        convenience handle for direct node actions and recovery operations, not
        a reconciliation identity source.
        """
        nodes: list[NodeInfo] = []
        try:
            all_domains = self.conn.listAllDomains(0)
            for domain in all_domains:
                node = self._node_from_domain(domain, lab_id)
                if node:
                    nodes.append(node)
            return StatusResult(lab_exists=len(nodes) > 0, nodes=nodes)
        except Exception as e:
            return StatusResult(lab_exists=False, error=str(e))

    async def status(
        self,
        lab_id: str,
        workspace: Path,
    ) -> StatusResult:
        """Get status of all VMs in a lab."""
        return await self._run_libvirt(self._status_sync, lab_id)

    def _start_node_sync(self, domain_name: str) -> tuple[str, str | None, str | None]:
        """Lookup, start domain — runs on libvirt thread.

        Returns:
            ("already_running", None, None) | ("started", kind, None) | ("error", None, error_msg)
        """
        try:
            domain = self.conn.lookupByName(domain_name)
            state, _ = domain.state()
            if state == libvirt.VIR_DOMAIN_RUNNING:
                return ("already_running", None, None)
            kind = self._get_domain_kind(domain)
            self._clear_vm_post_boot_commands_cache(domain_name)
            self._mark_post_boot_console_ownership_pending(domain_name, kind)
            domain.create()
            return ("started", kind, None)
        except libvirt.libvirtError as e:
            self._clear_vm_console_control_state(domain_name)
            return ("error", None, f"Libvirt error: {e}")
        except Exception as e:
            self._clear_vm_console_control_state(domain_name)
            return ("error", None, str(e))

    async def start_node(
        self,
        lab_id: str,
        node_name: str,
        workspace: Path,
    ) -> NodeActionResult:
        """Start a specific VM."""
        domain_name = self._domain_name(lab_id, node_name)

        status, _kind, error = await self._run_libvirt(self._start_node_sync, domain_name)

        if status == "already_running":
            visible = await self._run_libvirt(
                self._running_domain_identity_visible,
                domain_name,
                lab_id,
                node_name,
                None,
            )
            if not visible:
                return NodeActionResult(
                    success=False,
                    node_name=node_name,
                    error=(
                        f"Domain {domain_name} is running but not visible through "
                        "metadata-backed status"
                    ),
                )
            return NodeActionResult(
                success=True,
                node_name=node_name,
                new_status=NodeStatus.RUNNING,
                stdout="Domain already running",
            )
        if status == "error":
            return NodeActionResult(
                success=False,
                node_name=node_name,
                error=error,
            )

        await self._set_vm_tap_mtu(lab_id, node_name)
        return NodeActionResult(
            success=True,
            node_name=node_name,
            new_status=NodeStatus.RUNNING,
            stdout=f"Started domain {domain_name}",
        )

    def _remove_vm_sync(
        self,
        lab_id: str,
        node_name: str,
        workspace: Path,
    ) -> None:
        """Remove a single VM — runs on the libvirt thread.

        Force-stops the domain (if running), undefines it (including NVRAM),
        deletes overlay disks, and cleans VLAN allocations.

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
        self._clear_vm_post_boot_commands_cache(domain_name)
        self._teardown_n9kv_poap_network(lab_id, node_name)
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

        config_disk = disks_dir / f"{node_name}-config.img"
        if config_disk.exists():
            try:
                config_disk.unlink()
                logger.info(f"Removed config disk: {config_disk}")
            except Exception as e:
                logger.warning(f"Failed to remove config disk {config_disk}: {e}")

        # Clean up per-node serial log
        serial_log = workspace / "serial-logs" / f"{domain_name}.log"
        if serial_log.exists():
            try:
                serial_log.unlink()
                logger.info(f"Removed serial log: {serial_log}")
            except Exception as e:
                logger.warning(f"Failed to remove serial log {serial_log}: {e}")

        # Clean up per-node VLAN allocations
        lab_allocs = self._vlan_allocations.get(lab_id, {})
        if node_name in lab_allocs:
            del lab_allocs[node_name]
            self._save_vlan_allocations(lab_id, workspace)

    async def _remove_vm(
        self,
        lab_id: str,
        node_name: str,
        workspace: Path,
    ) -> None:
        """Remove a single VM and clean up per-node resources."""
        await self._run_libvirt(self._remove_vm_sync, lab_id, node_name, workspace)

    def _clear_vm_post_boot_commands_cache(self, domain_name: str) -> None:
        """Clear serial post-boot command cache for a specific VM."""
        try:
            from agent.console_extractor import clear_vm_post_boot_cache

            clear_vm_post_boot_cache(domain_name)
        except Exception:
            logger.debug(
                "Unable to clear VM post-boot cache for %s",
                domain_name,
                exc_info=True,
            )
        self._n9kv_loader_recovery_attempts.pop(domain_name, None)
        self._n9kv_loader_recovery_last_at.pop(domain_name, None)
        self._n9kv_poap_skip_attempted.discard(domain_name)
        self._n9kv_admin_password_completed.discard(domain_name)
        self._n9kv_panic_recovery_attempts.pop(domain_name, None)
        self._n9kv_panic_recovery_last_at.pop(domain_name, None)
        self._n9kv_panic_last_log_size.pop(domain_name, None)
        LibvirtProvider._clear_vm_console_control_state(domain_name)

    @staticmethod
    def _set_vm_console_control_state(
        domain_name: str,
        *,
        state: str,
        message: str,
    ) -> None:
        """Publish console ownership state for a VM domain."""
        try:
            from agent.console_session_registry import set_console_control_state

            set_console_control_state(
                domain_name,
                state=state,
                message=message,
            )
        except Exception:
            logger.debug(
                "Unable to set console control state for %s",
                domain_name,
                exc_info=True,
            )

    @staticmethod
    def _clear_vm_console_control_state(domain_name: str) -> None:
        """Clear persisted console ownership/read-only state for a VM."""
        LibvirtProvider._set_vm_console_control_state(
            domain_name,
            state="interactive",
            message="Configuration state cleared. Interactive control restored.",
        )

    def _mark_post_boot_console_ownership_pending(
        self,
        domain_name: str,
        kind: str | None,
    ) -> None:
        """Set console read-only when start-time post-boot automation is pending."""
        if not kind:
            return

        canonical_kind = self._canonical_kind(kind)
        if canonical_kind == "cisco_n9kv" and not settings.n9kv_boot_modifications_enabled:
            self._clear_vm_console_control_state(domain_name)
            return

        config = get_vendor_config(canonical_kind)
        if config is None or not config.post_boot_commands:
            self._clear_vm_console_control_state(domain_name)
            return

        self._set_vm_console_control_state(
            domain_name,
            state="read_only",
            message=(
                "Configuration in progress. Console is view-only until "
                "post-boot setup completes."
            ),
        )

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

    def _create_node_pre_sync(
        self,
        lab_id: str,
        node_name: str,
        domain_name: str,
        workspace: Path,
        node_definition_id: str | None = None,
    ) -> NodeActionResult | None:
        """Pre-create: recover state, check existing domain — libvirt thread.

        Returns NodeActionResult if domain already running, None to proceed.
        """
        disks_dir = self._disks_dir(workspace)
        already_running, _, state, identity = self._node_precheck_sync(
            lab_id, node_name, domain_name, workspace, disks_dir,
            node_definition_id,
        )
        if already_running:
            if identity == "foreign":
                return NodeActionResult(
                    success=False,
                    node_name=node_name,
                    error=(
                        f"Runtime namespace conflict: domain {domain_name} exists "
                        "but is not managed by Archetype"
                    ),
                )
            if identity == "stale_managed":
                return NodeActionResult(
                    success=False,
                    node_name=node_name,
                    error=(
                        f"Runtime namespace conflict: managed domain {domain_name} "
                        "belongs to a different node identity"
                    ),
                )
            if not self._running_domain_identity_visible(
                domain_name,
                lab_id,
                node_name,
                node_definition_id,
            ):
                return NodeActionResult(
                    success=False,
                    node_name=node_name,
                    error=(
                        f"Domain {domain_name} is running but not visible through "
                        "metadata-backed status"
                    ),
                )
            return NodeActionResult(
                success=True,
                node_name=node_name,
                new_status=state,
                stdout=f"Domain {domain_name} already running",
            )
        if state is not None:
            if identity == "foreign":
                return NodeActionResult(
                    success=False,
                    node_name=node_name,
                    error=(
                        f"Runtime namespace conflict: domain {domain_name} exists "
                        "but is not managed by Archetype"
                    ),
                )
            if identity == "stale_managed":
                return NodeActionResult(
                    success=False,
                    node_name=node_name,
                    error=(
                        f"Runtime namespace conflict: managed domain {domain_name} "
                        "belongs to a different node identity"
                    ),
                )
            return NodeActionResult(
                success=True,
                node_name=node_name,
                new_status=state,
                stdout=f"Domain {domain_name} already exists",
            )
        return None

    def _define_domain_sync(self, domain_name: str, xml: str) -> bool:
        """Define (but don't start) a domain — libvirt thread. Returns success."""
        domain = self.conn.defineXML(xml)
        if not domain:
            return False
        logger.info(f"Defined domain {domain_name} (not started)")
        return True

    async def create_node(
        self,
        lab_id: str,
        node_name: str,
        kind: str,
        workspace: Path,
        *,
        node_definition_id: str | None = None,
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
            # Phase 1: recovery + existing check (libvirt thread)
            early = await self._run_libvirt(
                self._create_node_pre_sync,
                lab_id,
                node_name,
                domain_name,
                workspace,
                node_definition_id,
            )
            if early is not None:
                return early

            # Build node_config from vendor registry, with API-resolved overrides
            libvirt_config = get_libvirt_config(kind)
            node_config: dict[str, Any] = {
                "lab_id": lab_id,
                "node_name": node_name,
                "node_definition_id": node_definition_id,
                "provider": self.name,
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
                "needs_nested_vmx": libvirt_config.needs_nested_vmx,
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

            # Verify backing image integrity
            try:
                self._verify_backing_image(base_image, image_sha256)
            except RuntimeError as e:
                return NodeActionResult(
                    success=False,
                    node_name=node_name,
                    error=str(e),
                )

            # Phase 2: disk preparation (async subprocess)
            overlay_path = disks_dir / f"{node_name}.qcow2"
            if not await self._create_overlay_disk(base_image, overlay_path):
                return NodeActionResult(
                    success=False,
                    node_name=node_name,
                    error=f"Failed to create overlay disk for {node_name}",
                )

            if libvirt_config.needs_nested_vmx:
                await asyncio.to_thread(self._patch_vjunos_svm_compat, overlay_path)

            canonical_kind = self._canonical_kind(kind)

            # Inject startup-config
            inject_summary = ""
            if not startup_config:
                config_file = workspace / "configs" / node_name / "startup-config"
                if config_file.exists():
                    startup_config = config_file.read_text()
            if not startup_config:
                vendor = get_vendor_config(kind)
                if vendor and vendor.default_startup_config:
                    startup_config = vendor.default_startup_config.replace("{hostname}", node_name)
                    logger.info("Using default startup config for %s (%s)", node_name, kind)
            if startup_config and (
                canonical_kind != "cisco_n9kv" or settings.n9kv_boot_modifications_enabled
            ):
                startup_config = self._prepare_startup_config_for_injection(
                    kind, startup_config, node_name=node_name
                )

            if startup_config and canonical_kind == "cisco_n9kv" and not settings.n9kv_boot_modifications_enabled:
                inject_summary = "skipped=n9kv_boot_modifications_disabled"
                logger.info(
                    "Skipping N9Kv startup-config injection for %s (boot mutations disabled)",
                    node_name,
                )
            elif startup_config and libvirt_config.config_inject_method == "bootflash":
                from agent.providers.bootflash_inject import inject_startup_config

                inject_diag: dict[str, Any] = {}
                inject_ok = await asyncio.to_thread(
                    inject_startup_config,
                    overlay_path,
                    startup_config,
                    partition=libvirt_config.config_inject_partition,
                    fs_type=libvirt_config.config_inject_fs_type,
                    config_path=libvirt_config.config_inject_path,
                    diagnostics=inject_diag,
                )
                inject_summary = self._format_injection_diagnostics(inject_ok, inject_diag)
                if inject_summary:
                    logger.info("Config injection details for %s: %s", node_name, inject_summary)
                if inject_ok:
                    logger.info("Injected startup config for %s (%d bytes)", node_name, len(startup_config))
                else:
                    logger.warning("Config injection failed for %s; VM will boot without config", node_name)

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

            config_disk_path: Path | None = None
            if startup_config and libvirt_config.config_inject_method == "config_disk":
                from agent.providers.config_disk_inject import create_config_disk
                config_disk_path = disks_dir / f"{node_name}-config.img"
                disk_ok = await asyncio.to_thread(
                    create_config_disk, config_disk_path, startup_config,
                )
                if disk_ok:
                    logger.info("Created config disk for %s (%d bytes)", node_name, len(startup_config))
                else:
                    logger.warning("Config disk creation failed for %s; VM will boot without config", node_name)
                    config_disk_path = None

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

            # Phase 3: VLAN + management network (libvirt thread for mgmt)
            iface_count = node_config.get("interface_count", 1)
            reserved_nics = node_config.get("reserved_nics", 0)
            vlan_tags = self._allocate_vlans(lab_id, node_name, iface_count + reserved_nics, workspace)

            include_management_interface, management_network = await self._run_libvirt(
                self._resolve_management_network, lab_id, node_name, kind,
            )

            # Serial log for lock-free readiness observation
            serial_log_dir = workspace / "serial-logs"
            serial_log_dir.mkdir(parents=True, exist_ok=True)
            try:
                serial_log_dir.chmod(0o777)
            except OSError:
                pass
            serial_log_path = serial_log_dir / f"{domain_name}.log"
            try:
                serial_log_path.touch(exist_ok=True)
                serial_log_path.chmod(0o666)
            except OSError:
                pass

            xml = self._generate_domain_xml(
                domain_name,
                node_config,
                overlay_path,
                data_volume_path,
                interface_count=iface_count,
                vlan_tags=vlan_tags,
                kind=kind,
                include_management_interface=include_management_interface,
                management_network=management_network,
                config_iso_path=config_iso_path,
                config_disk_path=config_disk_path,
                serial_log_path=serial_log_path,
            )

            # Phase 4: define domain (libvirt thread)
            ok = await self._run_libvirt(self._define_domain_sync, domain_name, xml)
            if not ok:
                return NodeActionResult(
                    success=False,
                    node_name=node_name,
                    error=f"Failed to define domain {domain_name}",
                )

            details = f"Defined domain {domain_name}"
            if inject_summary:
                details = f"{details}\nConfig injection: {inject_summary}"

            return NodeActionResult(
                success=True,
                node_name=node_name,
                new_status=NodeStatus.STOPPED,
                stdout=details,
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

    async def probe_runtime_conflict(
        self,
        lab_id: str,
        node_name: str,
        *,
        node_definition_id: str | None = None,
    ) -> RuntimeConflictProbeResult:
        """Inspect the target libvirt domain namespace before create."""
        domain_name = self._domain_name(lab_id, node_name)
        try:
            _already_running, runtime_id_short, status, identity = await self._run_libvirt(
                self._node_precheck_sync,
                lab_id,
                node_name,
                domain_name,
                Path("/tmp"),
                Path("/tmp"),
                node_definition_id,
            )
        except Exception as e:
            return RuntimeConflictProbeResult(
                available=False,
                classification="error",
                runtime_name=domain_name,
                error=f"Libvirt conflict probe failed: {e}",
            )

        classification = identity or "absent"
        if classification == "foreign":
            error = f"Domain {domain_name} is not managed by Archetype"
        elif classification == "stale_managed":
            error = f"Domain {domain_name} belongs to a different managed node identity"
        else:
            error = None

        return RuntimeConflictProbeResult(
            available=classification in {"absent", "expected"},
            classification=classification,
            runtime_name=domain_name,
            status=status.value if hasattr(status, "value") else status,
            runtime_id=runtime_id_short,
            error=error,
        )

    # -- Config extraction & injection helpers (delegated to libvirt_config) --

    def _prepare_startup_config_for_injection(
        self,
        kind: str,
        startup_config: str,
        *,
        node_name: str = "",
    ) -> str:
        """Normalize startup-config content before disk/ISO injection."""
        return _prepare_startup_config_for_injection(
            kind, startup_config,
            node_name=node_name,
            n9kv_config_preamble=_N9KV_CONFIG_PREAMBLE,
        )

    def _format_injection_diagnostics(self, inject_ok: bool, diag: dict[str, Any]) -> str:
        """Render compact bootflash injection diagnostics for callback logs."""
        return _format_injection_diagnostics(inject_ok, diag)

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

    def _get_console_info_sync(self, domain_name: str) -> tuple[str, str | None, int | None] | None:
        """Lookup domain state, kind, and TCP serial port — libvirt thread.

        Returns (console_method, kind, tcp_port) or None if domain not running.
        """
        try:
            domain = self.conn.lookupByName(domain_name)
            state, _ = domain.state()
            if state != libvirt.VIR_DOMAIN_RUNNING:
                return None
            kind = self._get_domain_kind(domain)
            console_method = get_console_method(kind) if kind else "docker_exec"
            tcp_port = self._get_tcp_serial_port(domain) if console_method != "ssh" else None
            return (console_method, kind, tcp_port)
        except Exception:
            return None

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

        info = await self._run_libvirt(self._get_console_info_sync, domain_name)
        if info is None:
            return None
        console_method, kind, tcp_port = info

        try:
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
                if tcp_port:
                    return ["python3", "-c", _TCP_TELNET_CONSOLE_SCRIPT, str(tcp_port)]

                # Default: virsh console (serial console)
                # --force takes over console even if another session is connected
                return ["virsh", "-c", self._uri, "console", "--force", domain_name]

        except Exception:
            return None

    async def _get_vm_management_ip(self, domain_name: str) -> str | None:
        """Get the management IP address for a VM."""
        return await _get_vm_management_ip(domain_name, self._uri)

    # -- N9Kv POAP & management network helpers (delegated to libvirt_n9kv) --

    def _node_uses_dedicated_mgmt_interface(self, kind: str | None) -> bool:
        return _node_uses_dedicated_mgmt_interface(kind)

    @staticmethod
    def _n9kv_poap_network_name(lab_id: str, node_name: str) -> str:
        return _n9kv_poap_network_name(lab_id, node_name)

    @staticmethod
    def _n9kv_poap_bridge_name(lab_id: str, node_name: str) -> str:
        return _n9kv_poap_bridge_name(lab_id, node_name)

    @staticmethod
    def _n9kv_poap_subnet(lab_id: str, node_name: str) -> tuple[str, str, str]:
        return _n9kv_poap_subnet(lab_id, node_name)

    def _n9kv_poap_config_url(self, lab_id: str, node_name: str, gateway_ip: str) -> str:
        return _n9kv_poap_config_url(lab_id, node_name, gateway_ip)

    def _n9kv_poap_tftp_root(self, lab_id: str, node_name: str) -> Path:
        return _n9kv_poap_tftp_root(lab_id, node_name)

    @staticmethod
    def _n9kv_poap_bootfile_name() -> str:
        return _n9kv_poap_bootfile_name()

    def _stage_n9kv_poap_tftp_script(self, lab_id: str, node_name: str, gateway_ip: str) -> tuple[Path, str] | None:
        return _stage_n9kv_poap_tftp_script(lab_id, node_name, gateway_ip)

    def _ensure_n9kv_poap_network(self, lab_id: str, node_name: str) -> str | None:
        return _ensure_n9kv_poap_network(self.conn, lab_id, node_name)

    def _teardown_n9kv_poap_network(self, lab_id: str, node_name: str) -> None:
        _teardown_n9kv_poap_network(self.conn, lab_id, node_name)

    def _resolve_management_network(
        self,
        lab_id: str,
        node_name: str,
        kind: str | None,
    ) -> tuple[bool, str]:
        return _resolve_management_network(
            self.conn, lab_id, node_name, kind,
            canonical_kind_fn=self._canonical_kind,
        )

    def _ensure_libvirt_network(self, network_name: str) -> bool:
        return _ensure_libvirt_network(self.conn, network_name)

    def _domain_has_dedicated_mgmt_interface(self, domain) -> bool:
        """Detect whether a domain includes a libvirt-managed network NIC."""
        try:
            xml = domain.XMLDesc(0)
            root = ET.fromstring(xml)
            for iface in root.findall(".//devices/interface[@type='network']"):
                src = iface.find("source")
                if src is not None and src.get("network"):
                    return True
        except Exception:
            return False
        return False

    def _resolve_data_interface_mac_sync(
        self,
        lab_id: str,
        node_name: str,
        interface_index: int,
    ) -> str:
        """Compute the guest MAC for a data interface — runs on libvirt thread.

        Accounts for dedicated management NIC offset (+1) and reserved NICs
        (e.g. XRv9k ``reserved_nics=2``).

        Returns the deterministic guest MAC (52:54:00:XX:XX:XX).
        """
        domain_name = self._domain_name(lab_id, node_name)
        mac_index = interface_index
        try:
            domain = self.conn.lookupByName(domain_name)
            kind = self._get_domain_kind(domain)
            if self._node_uses_dedicated_mgmt_interface(kind) and self._domain_has_dedicated_mgmt_interface(domain):
                mac_index = interface_index + 1
            # Account for reserved (dummy) NICs between management and data
            # interfaces (e.g., XRv9k has reserved_nics=2 for ctrl-dummy + dev-dummy).
            if kind:
                config = get_vendor_config(kind)
                if config:
                    mac_index += config.reserved_nics
        except Exception:
            pass

        return self._generate_mac_address(domain_name, mac_index)

    def _get_vm_interface_port_sync(
        self,
        lab_id: str,
        node_name: str,
        interface_index: int,
    ) -> str | None:
        """Find OVS port for a VM interface — runs on libvirt thread.

        Uses libvirt for MAC offset detection and OVS CLI for port lookup.
        """
        guest_mac = self._resolve_data_interface_mac_sync(
            lab_id, node_name, interface_index,
        )
        tap_mac = "fe" + guest_mac[2:]
        expected_macs = {guest_mac.lower(), tap_mac.lower()}
        domain_name = self._domain_name(lab_id, node_name)
        expected_iface_id = self._generate_ovs_interface_id(
            domain_name,
            "data",
            interface_index,
        )

        try:
            iface_result = subprocess.run(
                [
                    "ovs-vsctl",
                    "--data=bare",
                    "--no-heading",
                    "--columns=name",
                    "find",
                    "Interface",
                    f"external_ids:iface-id={expected_iface_id}",
                ],
                capture_output=True,
                text=True,
            )
            if iface_result.returncode == 0:
                exact_port = iface_result.stdout.strip().splitlines()
                if exact_port:
                    port_name = exact_port[0].strip()
                    if port_name:
                        return port_name

            try:
                domain = self.conn.lookupByName(domain_name)
                root = ET.fromstring(domain.XMLDesc(0))
                for interface in root.findall(".//devices/interface"):
                    mac_elem = interface.find("mac")
                    target_elem = interface.find("target")
                    if mac_elem is None or target_elem is None:
                        continue
                    xml_mac = (mac_elem.get("address") or "").lower()
                    port_name = target_elem.get("dev")
                    if xml_mac in expected_macs and port_name and self._ovs_port_exists(port_name):
                        return port_name
            except Exception:
                logger.debug(
                    "Domain XML target lookup failed for %s:%s",
                    node_name,
                    interface_index + 1,
                    exc_info=True,
                )

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
                mac_result = subprocess.run(
                    ["ovs-vsctl", "get", "interface", port, "mac_in_use"],
                    capture_output=True,
                    text=True,
                )
                if mac_result.returncode == 0:
                    port_mac = mac_result.stdout.strip().strip('"')
                    if port_mac.lower() in expected_macs:
                        return port
                ext_result = subprocess.run(
                    ["ovs-vsctl", "get", "interface", port, "external_ids:attached-mac"],
                    capture_output=True,
                    text=True,
                )
                if ext_result.returncode == 0:
                    attached_mac = ext_result.stdout.strip().strip('"')
                    if attached_mac.lower() in expected_macs:
                        return port
            return None
        except Exception as e:
            logger.error(f"Error finding VM interface port: {e}")
            return None

    async def get_vm_interface_port(
        self,
        lab_id: str,
        node_name: str,
        interface_index: int,
    ) -> str | None:
        """Get the OVS port name for a VM interface."""
        return await self._run_libvirt(
            self._get_vm_interface_port_sync, lab_id, node_name, interface_index,
        )

    async def set_vm_link_state(
        self,
        lab_id: str,
        node_name: str,
        interface_index: int,
        link_state: str,
    ) -> tuple[bool, str | None]:
        """Set VM interface link state via ``virsh domif-setlink``.

        Args:
            lab_id: Lab identifier.
            node_name: VM node name.
            interface_index: 0-based data interface index.
            link_state: ``"up"`` or ``"down"``.

        Returns:
            ``(success, error_message)`` tuple.
        """
        domain_name = self._domain_name(lab_id, node_name)
        try:
            guest_mac = await self._run_libvirt(
                self._resolve_data_interface_mac_sync,
                lab_id, node_name, interface_index,
            )
        except Exception as e:
            return False, f"MAC resolution failed: {e}"

        proc = await asyncio.create_subprocess_exec(
            "virsh", "-c", self._uri,
            "domif-setlink", domain_name, guest_mac, link_state,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await proc.communicate()
        if proc.returncode != 0:
            err = stderr_bytes.decode(errors="replace").strip()
            return False, f"virsh domif-setlink failed: {err}"

        logger.info(
            "Set VM link %s on %s mac=%s (%s:eth%d)",
            link_state, domain_name, guest_mac, node_name, interface_index + 1,
        )
        return True, None

    # ------------------------------------------------------------------
    # Carrier monitor: VM tap port discovery
    # ------------------------------------------------------------------

    def _build_vm_monitored_ports_sync(self) -> dict:
        """Build ``{port_name: MonitoredPort}`` for all deployed VM interfaces.

        Runs on the libvirt thread (accesses ``self.conn`` for MAC offsets).
        Uses a single batch OVS query to resolve port MACs.
        """
        from agent.network.carrier_monitor import MonitoredPort

        # Batch OVS query: get all interface names and their MACs.
        try:
            result = subprocess.run(
                [
                    "ovs-vsctl", "--format=json",
                    "--columns=name,mac_in_use", "list", "Interface",
                ],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                return {}
            ovs_data = json.loads(result.stdout)
        except Exception:
            logger.debug("Failed to batch-query OVS interfaces for VM port cache")
            return {}

        # Build MAC -> port_name lookup from OVS data.
        mac_to_port: dict[str, str] = {}
        for row in ovs_data.get("data", []):
            port_name = row[0]
            mac = row[1]
            if isinstance(port_name, str) and isinstance(mac, str) and mac:
                mac_to_port[mac.lower().strip('"')] = port_name

        ports: dict = {}
        for lab_id, lab_allocs in self._vlan_allocations.items():
            for node_name, vlans in lab_allocs.items():
                domain_name = self._domain_name(lab_id, node_name)
                for iface_idx in range(len(vlans)):
                    guest_mac = self._resolve_data_interface_mac_sync(
                        lab_id, node_name, iface_idx,
                    )
                    tap_mac = "fe" + guest_mac[2:]
                    # Match either guest or tap MAC in the batch results.
                    port_name = (
                        mac_to_port.get(guest_mac.lower())
                        or mac_to_port.get(tap_mac.lower())
                    )
                    if port_name:
                        ports[port_name] = MonitoredPort(
                            port_name=port_name,
                            container_name=domain_name,
                            interface_name=f"eth{iface_idx + 1}",
                            lab_id=lab_id,
                            node_name=node_name,
                        )
        return ports

    def get_vm_monitored_ports(self) -> dict:
        """Return cached VM port map (sync, non-blocking)."""
        return self._vm_port_cache

    async def refresh_vm_monitored_ports(self) -> None:
        """Rebuild the VM port cache on the libvirt thread."""
        self._vm_port_cache = await self._run_libvirt(
            self._build_vm_monitored_ports_sync,
        )
        logger.debug(
            "Refreshed VM monitored ports cache: %d ports", len(self._vm_port_cache),
        )

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
                proc = await asyncio.create_subprocess_exec(
                    "ovs-vsctl", "set", "port", port, f"tag={shared_vlan}",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, stderr_bytes = await proc.communicate()
                if proc.returncode != 0:
                    logger.error(f"Failed to set VLAN on port {port}: {stderr_bytes.decode()}")
                    return False

            logger.info(
                f"Connected {source_node}:{source_interface} <-> {target_node}:{target_interface} "
                f"via VLAN {shared_vlan}"
            )
            return True

        except Exception as e:
            logger.error(f"Error during hot_connect: {e}")
            return False

    # -- Readiness & boot intervention (delegated to libvirt_readiness) --

    async def _run_post_boot_commands(self, domain_name: str, kind: str) -> bool:
        return await _run_post_boot_commands(
            domain_name, kind, self._uri,
            canonical_kind_fn=self._canonical_kind,
            clear_console_state_fn=self._clear_vm_console_control_state,
        )

    @staticmethod
    def _extract_probe_markers(details: str | None) -> set[str]:
        return _extract_probe_markers(details)

    @staticmethod
    def _classify_console_result(result) -> str:
        return _classify_console_result(result)

    async def _run_n9kv_loader_recovery(self, domain_name: str, kind: str) -> str:
        return await _run_n9kv_loader_recovery(
            domain_name, kind, self._uri,
            recovery_attempts=self._n9kv_loader_recovery_attempts,
            recovery_last_at=self._n9kv_loader_recovery_last_at,
            max_attempts=self._N9KV_LOADER_RECOVERY_MAX_ATTEMPTS,
            cooldown=self._N9KV_LOADER_RECOVERY_COOLDOWN,
        )

    async def _run_n9kv_panic_recovery(self, domain_name: str, kind: str, serial_log_path: str) -> str:
        return await _run_n9kv_panic_recovery(
            domain_name, kind, serial_log_path,
            run_libvirt_fn=self._run_libvirt,
            conn=self.conn,
            panic_attempts=self._n9kv_panic_recovery_attempts,
            panic_last_at=self._n9kv_panic_recovery_last_at,
            panic_last_log_size=self._n9kv_panic_last_log_size,
            max_attempts=self._N9KV_PANIC_RECOVERY_MAX_ATTEMPTS,
            cooldown=self._N9KV_PANIC_RECOVERY_COOLDOWN,
        )

    async def _run_n9kv_poap_skip(self, domain_name: str, kind: str) -> str:
        return await _run_n9kv_poap_skip(
            domain_name, kind, self._uri,
            poap_skip_attempted=self._n9kv_poap_skip_attempted,
        )

    async def _run_n9kv_admin_password_setup(self, domain_name: str, kind: str) -> str:
        return await _run_n9kv_admin_password_setup(
            domain_name, kind, self._uri,
            admin_password_completed=self._n9kv_admin_password_completed,
        )

    def _check_readiness_domain_sync(self, domain_name: str) -> tuple[int, dict] | None:
        """Lookup domain state and readiness overrides — runs on libvirt thread."""
        try:
            domain = self.conn.lookupByName(domain_name)
            state, _ = domain.state()
            overrides = self._get_domain_readiness_overrides(domain)
            return (state, overrides)
        except libvirt.libvirtError:
            return None

    async def check_readiness(
        self,
        lab_id: str,
        node_name: str,
        kind: str,
    ) -> ReadinessResult:
        """Check if a VM has finished booting and is ready."""
        domain_name = self._domain_name(lab_id, node_name)
        result = await _check_readiness(
            lab_id, node_name, kind,
            domain_name=domain_name,
            uri=self._uri,
            run_libvirt_fn=self._run_libvirt,
            check_readiness_domain_sync_fn=self._check_readiness_domain_sync,
            get_vm_management_ip_fn=self._get_vm_management_ip,
            canonical_kind_fn=self._canonical_kind,
            clear_console_state_fn=self._clear_vm_console_control_state,
            loader_recovery_attempts=self._n9kv_loader_recovery_attempts,
            loader_recovery_last_at=self._n9kv_loader_recovery_last_at,
            loader_max_attempts=self._N9KV_LOADER_RECOVERY_MAX_ATTEMPTS,
            loader_cooldown=self._N9KV_LOADER_RECOVERY_COOLDOWN,
            panic_recovery_attempts=self._n9kv_panic_recovery_attempts,
            panic_recovery_last_at=self._n9kv_panic_recovery_last_at,
            panic_last_log_size=self._n9kv_panic_last_log_size,
            panic_max_attempts=self._N9KV_PANIC_RECOVERY_MAX_ATTEMPTS,
            panic_cooldown=self._N9KV_PANIC_RECOVERY_COOLDOWN,
            poap_skip_attempted=self._n9kv_poap_skip_attempted,
            admin_password_completed=self._n9kv_admin_password_completed,
            conn=self.conn,
        )
        if result.is_ready:
            node_vlans = self.get_node_vlans(lab_id, node_name)
            if node_vlans:
                ovs_port = await self.get_vm_interface_port(lab_id, node_name, 0)
                if not ovs_port:
                    return ReadinessResult(
                        is_ready=False,
                        message="Waiting for data interface attachment",
                        progress_percent=95,
                        details="eth1 OVS port not yet resolvable",
                    )
        return result

    @staticmethod
    def _check_tcp_port(host: str, port: int, timeout: float) -> bool:
        return _check_tcp_port(host, port, timeout)

    def _get_readiness_timeout_sync(
        self,
        kind: str,
        lab_id: str | None,
        node_name: str | None,
    ) -> int:
        """Get readiness timeout with domain overrides — libvirt thread."""
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

    def get_readiness_timeout(
        self,
        kind: str,
        lab_id: str | None = None,
        node_name: str | None = None,
    ) -> int:
        """Sync accessor — use get_readiness_timeout_async from async contexts."""
        return self._get_readiness_timeout_sync(kind, lab_id, node_name)

    async def get_readiness_timeout_async(
        self,
        kind: str,
        lab_id: str | None = None,
        node_name: str | None = None,
    ) -> int:
        """Get the readiness timeout for a device type (async)."""
        return await self._run_libvirt(
            self._get_readiness_timeout_sync, kind, lab_id, node_name,
        )

    def _get_node_kind_sync(self, lab_id: str, node_name: str) -> str | None:
        """Get device kind from domain metadata — libvirt thread."""
        try:
            for domain in self.conn.listAllDomains(0):
                metadata = self._get_domain_metadata_values(domain)
                if metadata.get("lab_id") != lab_id:
                    continue
                if metadata.get("node_name") != node_name:
                    continue
                return self._get_domain_kind(domain)
        except Exception:
            return None
        return None

    def get_node_kind(self, lab_id: str, node_name: str) -> str | None:
        """Sync accessor — use get_node_kind_async from async contexts."""
        return self._get_node_kind_sync(lab_id, node_name)

    async def get_node_kind_async(self, lab_id: str, node_name: str) -> str | None:
        """Get the device kind for a VM node (async)."""
        return await self._run_libvirt(self._get_node_kind_sync, lab_id, node_name)

    def _resolve_node_name_for_action_sync(
        self,
        lab_id: str,
        identifier: str,
    ) -> str | None:
        """Resolve a libvirt action target to a node name using metadata first.

        Accepts a runtime UUID, exact runtime name, or explicit node name.
        This keeps direct-action/recovery paths anchored on runtime metadata
        rather than reverse-parsing generated domain names.
        """
        if not identifier:
            return None

        candidates = []
        seen_names: set[str] = set()

        def _add_candidate(domain) -> None:
            try:
                name = domain.name()
            except Exception:
                return
            if name in seen_names:
                return
            seen_names.add(name)
            candidates.append(domain)

        lookup_by_uuid = getattr(self.conn, "lookupByUUIDString", None)
        if callable(lookup_by_uuid):
            try:
                _add_candidate(lookup_by_uuid(identifier))
            except Exception:
                pass

        try:
            _add_candidate(self.conn.lookupByName(identifier))
        except Exception:
            pass

        if "-" not in identifier:
            try:
                _add_candidate(self.conn.lookupByName(self._domain_name(lab_id, identifier)))
            except Exception:
                pass

        for domain in candidates:
            metadata = self._get_domain_metadata_values(domain)
            if metadata.get("lab_id") != lab_id:
                continue
            node_name = metadata.get("node_name")
            if node_name:
                return node_name

        return identifier if "-" not in identifier else None

    async def resolve_node_name_for_action(self, lab_id: str, identifier: str) -> str | None:
        """Resolve a libvirt direct-action target to a node name."""
        return await self._run_libvirt(
            self._resolve_node_name_for_action_sync,
            lab_id,
            identifier,
        )

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
                "lab_id",
                "node_definition_id",
                "node_name",
                "provider",
                "readiness_probe",
                "readiness_pattern",
                "readiness_timeout",
                "serial_type",
            }:
                text = (elem.text or "").strip()
                if text:
                    values[local_tag] = text
        return values

    def _get_runtime_profile_sync(self, lab_id: str, node_name: str) -> dict[str, Any]:
        """Get runtime configuration from live domain XML — libvirt thread."""
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

    def get_runtime_profile(self, lab_id: str, node_name: str) -> dict[str, Any]:
        """Sync accessor — use get_runtime_profile_async from async contexts."""
        return self._get_runtime_profile_sync(lab_id, node_name)

    async def get_runtime_profile_async(self, lab_id: str, node_name: str) -> dict[str, Any]:
        """Get runtime configuration for a VM node (async)."""
        return await self._run_libvirt(self._get_runtime_profile_sync, lab_id, node_name)

    def _check_domain_running_sync(self, domain_name: str) -> bool | None:
        """Check if a domain exists and is running — libvirt thread.

        Returns True if running, False if exists but not running, None if not found.
        """
        try:
            domain = self.conn.lookupByName(domain_name)
            state, _ = domain.state()
            return state == libvirt.VIR_DOMAIN_RUNNING
        except libvirt.libvirtError:
            return None

    async def _extract_config(
        self,
        lab_id: str,
        node_name: str,
        kind: str,
    ) -> tuple[str, str] | None:
        """Extract running config from a single VM."""
        domain_name = self._domain_name(lab_id, node_name)
        return await _extract_config(
            lab_id, node_name, kind,
            domain_name=domain_name,
            uri=self._uri,
            run_libvirt_fn=self._run_libvirt,
            check_domain_running_sync_fn=self._check_domain_running_sync,
            run_ssh_command_fn=self._run_ssh_command,
        )

    async def _extract_config_via_ssh(
        self,
        domain_name: str,
        kind: str,
        node_name: str,
    ) -> str | None:
        """Extract config from VM via SSH."""
        return await _extract_config_via_ssh(
            domain_name, kind, node_name,
            uri=self._uri,
            run_ssh_command_fn=self._run_ssh_command,
        )

    def _list_lab_vm_kinds_sync(self, lab_id: str) -> list[tuple[str, str]]:
        """List running VMs for a lab with their kinds — libvirt thread.

        Returns list of (node_name, kind) tuples.
        """
        results: list[tuple[str, str]] = []
        try:
            all_domains = self.conn.listAllDomains(libvirt.VIR_CONNECT_LIST_DOMAINS_ACTIVE)
            for domain in all_domains:
                metadata = self._get_domain_metadata_values(domain)
                if metadata.get("lab_id") != lab_id:
                    continue
                node_name = metadata.get("node_name")
                if not node_name:
                    continue
                kind = self._get_domain_kind(domain)
                if kind:
                    results.append((node_name, kind))
                else:
                    logger.warning(f"Unknown device kind for {node_name}, skipping extraction")
        except Exception as e:
            logger.error(f"Error listing VM kinds for lab {lab_id}: {e}")
        return results

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

        try:
            vm_kinds = await self._run_libvirt(self._list_lab_vm_kinds_sync, lab_id)

            for node_name, kind in vm_kinds:
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

    def _discover_labs_sync(self) -> dict[str, list[NodeInfo]]:
        """Enumerate all archetype-managed libvirt domains — libvirt thread."""
        discovered: dict[str, list[NodeInfo]] = {}
        try:
            all_domains = self.conn.listAllDomains(0)
            for domain in all_domains:
                name = domain.name()
                if not name.startswith("arch-"):
                    continue
                metadata = self._get_domain_metadata_values(domain)
                lab_id = metadata.get("lab_id")
                node_name = metadata.get("node_name")
                node_definition_id = metadata.get("node_definition_id")
                if not lab_id or not node_name or not node_definition_id:
                    logger.debug(
                        "Skipping metadata-incomplete domain during discovery: %s",
                        name,
                    )
                    continue
                node = NodeInfo(
                    name=node_name,
                    status=self._get_domain_status(domain),
                    container_id=domain.UUIDString()[:12],
                    runtime_id=domain.UUIDString(),
                    node_definition_id=node_definition_id,
                )
                if lab_id not in discovered:
                    discovered[lab_id] = []
                discovered[lab_id].append(node)
            logger.info(f"Discovered {len(discovered)} labs with LibvirtProvider")
        except Exception as e:
            logger.error(f"Error discovering labs: {e}")
        return discovered

    async def discover_labs(self) -> dict[str, list[NodeInfo]]:
        """Discover all running labs managed by this provider."""
        return await self._run_libvirt(self._discover_labs_sync)

    def _audit_runtime_identity_sync(self) -> dict[str, Any]:
        """Audit libvirt metadata coverage for all Archetype-managed domains."""
        audit: dict[str, Any] = {
            "provider": self.name,
            "managed_runtimes": 0,
            "resolved_by_metadata": 0,
            "name_only": 0,
            "missing_node_definition_id": 0,
            "missing_runtime_id": 0,
            "inconsistent_metadata": 0,
            "nodes": [],
        }
        try:
            all_domains = self.conn.listAllDomains(0)
            for domain in all_domains:
                runtime_name = domain.name()
                if not runtime_name.startswith("arch-"):
                    continue

                metadata = self._get_domain_metadata_values(domain)
                parts = runtime_name.split("-", 2)
                parsed_lab_id = parts[1] if len(parts) >= 3 else None
                parsed_node_name = parts[2] if len(parts) >= 3 else None
                lab_id = metadata.get("lab_id") or parsed_lab_id
                node_name = metadata.get("node_name") or parsed_node_name
                node_definition_id = metadata.get("node_definition_id")
                runtime_id = domain.UUIDString()
                provider_metadata = metadata.get("provider")
                inconsistent_metadata = bool(
                    provider_metadata not in (None, "", self.name)
                    or not metadata.get("lab_id")
                    or not metadata.get("node_name")
                )
                name_only = bool(node_name and not node_definition_id)
                resolved_by_metadata = bool(
                    metadata.get("lab_id")
                    and metadata.get("node_name")
                    and node_definition_id
                    and runtime_id
                    and provider_metadata == self.name
                )

                audit["managed_runtimes"] += 1
                audit["resolved_by_metadata"] += int(resolved_by_metadata)
                audit["name_only"] += int(name_only)
                audit["missing_node_definition_id"] += int(not bool(node_definition_id))
                audit["missing_runtime_id"] += int(not bool(runtime_id))
                audit["inconsistent_metadata"] += int(inconsistent_metadata)
                audit["nodes"].append({
                    "provider": self.name,
                    "runtime_name": runtime_name,
                    "lab_id": lab_id,
                    "node_name": node_name,
                    "node_definition_id": node_definition_id,
                    "runtime_id": runtime_id,
                    "resolved_by_metadata": resolved_by_metadata,
                    "name_only": name_only,
                    "missing_node_definition_id": not bool(node_definition_id),
                    "missing_runtime_id": not bool(runtime_id),
                    "inconsistent_metadata": inconsistent_metadata,
                })
        except Exception as e:
            logger.error(f"Error auditing libvirt runtime identity: {e}")
            audit["error"] = str(e)
        return audit

    async def audit_runtime_identity(self) -> dict[str, Any]:
        """Audit libvirt runtime metadata coverage across all managed domains."""
        return await self._run_libvirt(self._audit_runtime_identity_sync)

    def _backfill_runtime_identity_sync(
        self,
        entries: list[dict[str, str]],
        *,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Backfill libvirt domain identity metadata in place."""
        result: dict[str, Any] = {
            "provider": self.name,
            "updated": 0,
            "recreate_required": 0,
            "missing": 0,
            "skipped": 0,
            "nodes": [],
            "errors": [],
        }
        for entry in entries:
            lab_id = entry.get("lab_id", "")
            node_name = entry.get("node_name", "")
            node_definition_id = entry.get("node_definition_id", "")
            provider_name = entry.get("provider", self.name) or self.name
            runtime_name = self._domain_name(lab_id, node_name)
            try:
                domain = self.conn.lookupByName(runtime_name)
            except Exception:
                result["missing"] += 1
                result["nodes"].append({
                    "lab_id": lab_id,
                    "node_name": node_name,
                    "node_definition_id": node_definition_id,
                    "runtime_name": runtime_name,
                    "outcome": "missing",
                })
                continue

            metadata = self._get_domain_metadata_values(domain)
            if (
                metadata.get("lab_id") == lab_id
                and metadata.get("node_name") == node_name
                and metadata.get("node_definition_id") == node_definition_id
                and metadata.get("provider", self.name) == provider_name
            ):
                result["skipped"] += 1
                result["nodes"].append({
                    "lab_id": lab_id,
                    "node_name": node_name,
                    "node_definition_id": node_definition_id,
                    "runtime_name": runtime_name,
                    "outcome": "already_present",
                })
                continue

            try:
                if not dry_run:
                    metadata_xml = _build_domain_identity_metadata_fragment(
                        lab_id=lab_id,
                        node_name=node_name,
                        node_definition_id=node_definition_id,
                        provider=provider_name,
                    )
                    metadata_written = False

                    set_metadata = getattr(domain, "setMetadata", None)
                    metadata_type = getattr(libvirt, "VIR_DOMAIN_METADATA_ELEMENT", None)
                    affect_live = getattr(libvirt, "VIR_DOMAIN_AFFECT_LIVE", 0)
                    affect_config = getattr(libvirt, "VIR_DOMAIN_AFFECT_CONFIG", 0)
                    if callable(set_metadata) and metadata_type is not None:
                        set_metadata(
                            metadata_type,
                            metadata_xml,
                            "node",
                            ARCHETYPE_LIBVIRT_NS,
                            affect_live | affect_config,
                        )
                        metadata_written = True

                    if not metadata_written:
                        xml = domain.XMLDesc(0)
                        updated_xml = _update_domain_identity_metadata_xml(
                            xml,
                            lab_id=lab_id,
                            node_name=node_name,
                            node_definition_id=node_definition_id,
                            provider=provider_name,
                        )
                        updated = self.conn.defineXML(updated_xml)
                        if not updated:
                            raise RuntimeError(f"Failed to redefine domain {runtime_name}")
                result["updated"] += 1
                result["nodes"].append({
                    "lab_id": lab_id,
                    "node_name": node_name,
                    "node_definition_id": node_definition_id,
                    "runtime_name": runtime_name,
                    "outcome": "updated" if not dry_run else "would_update",
                    "dry_run": dry_run,
                })
            except Exception as e:
                result["errors"].append(f"{runtime_name}: {e}")

        return result

    async def backfill_runtime_identity(
        self,
        entries: list[dict[str, str]],
        *,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Backfill libvirt runtime metadata for existing domains."""
        return await self._run_libvirt(
            self._backfill_runtime_identity_sync,
            entries,
            dry_run=dry_run,
        )

    def _cleanup_orphan_domains_sync(
        self,
        valid_lab_ids: set[str],
        workspace_base: Path | None,
    ) -> dict[str, list[str]]:
        """Discover and remove orphan domains — libvirt thread."""
        removed: dict[str, list[str]] = {"domains": [], "disks": []}
        try:
            discovered = self._discover_labs_sync()
            for lab_id, nodes in discovered.items():
                if not self._is_orphan_lab(lab_id, valid_lab_ids):
                    continue

                logger.info(f"Cleaning up orphan lab: {lab_id} ({len(nodes)} VMs)")
                for node in nodes:
                    domain_name = self._domain_name(lab_id, node.name)
                    try:
                        # Recovery-only direct lookup by generated domain name.
                        # Managed discovery/status no longer uses domain names as
                        # identity, but cleanup must still remove legacy domains.
                        domain = self.conn.lookupByName(domain_name)
                        state, _ = domain.state()
                        if state == libvirt.VIR_DOMAIN_RUNNING:
                            logger.info(f"Force stopping orphan domain: {domain_name}")
                            domain.destroy()
                        self._undefine_domain(domain, domain_name)
                        self._clear_vm_post_boot_commands_cache(domain_name)
                        self._teardown_n9kv_poap_network(lab_id, node.name)
                        removed["domains"].append(domain_name)
                        logger.info(f"Removed orphan domain: {domain_name}")
                    except libvirt.libvirtError as e:
                        logger.warning(f"Error removing orphan domain {domain_name}: {e}")

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
                        try:
                            disks_dir.rmdir()
                        except Exception:
                            pass

                self._cleanup_orphan_vlans(
                    lab_id,
                    (workspace_base / lab_id) if workspace_base else None,
                )

            if removed["domains"]:
                logger.info(
                    f"Orphan cleanup complete: removed {len(removed['domains'])} domains, "
                    f"{len(removed['disks'])} disks"
                )
        except Exception as e:
            logger.error(f"Error during orphan cleanup: {e}")
        return removed

    async def cleanup_orphan_domains(
        self,
        valid_lab_ids: set[str],
        workspace_base: Path | None = None,
    ) -> dict[str, list[str]]:
        """Remove VMs for labs that no longer exist."""
        return await self._run_libvirt(
            self._cleanup_orphan_domains_sync, valid_lab_ids, workspace_base,
        )

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

    def _cleanup_lab_orphan_domains_sync(
        self,
        lab_id: str,
        keep_node_names: set[str],
        workspace_base: Path | None,
    ) -> dict[str, list[str]]:
        """Remove orphan domains within a lab — libvirt thread."""
        removed: dict[str, list[str]] = {"domains": [], "disks": []}
        # Track node_name per removed domain for disk cleanup
        removed_node_names: list[str] = []
        skipped_metadata = 0
        try:
            all_domains = self.conn.listAllDomains(0)

            for domain in all_domains:
                name = domain.name()
                metadata = self._get_domain_metadata_values(domain)
                if metadata.get("lab_id") != lab_id:
                    continue
                node_name = metadata.get("node_name")
                if not node_name:
                    skipped_metadata += 1
                    continue
                if node_name in keep_node_names:
                    logger.debug(f"Keeping VM {name} (node {node_name} still in topology)")
                    continue
                try:
                    logger.info(f"Removing orphan VM {name} (node {node_name} deleted from topology)")
                    state, _ = domain.state()
                    if state == libvirt.VIR_DOMAIN_RUNNING:
                        logger.info(f"Force stopping orphan domain: {name}")
                        domain.destroy()
                    self._undefine_domain(domain, name)
                    self._clear_vm_post_boot_commands_cache(name)
                    self._teardown_n9kv_poap_network(lab_id, node_name)
                    removed["domains"].append(name)
                    removed_node_names.append(node_name)
                    if lab_id in self._vlan_allocations:
                        if node_name in self._vlan_allocations[lab_id]:
                            del self._vlan_allocations[lab_id][node_name]
                            logger.debug(f"Freed VLAN allocations for orphan node: {node_name}")
                except libvirt.libvirtError as e:
                    logger.warning(f"Error removing orphan domain {name}: {e}")

            if skipped_metadata:
                runtime_identity_skips.labels(
                    resource_type="libvirt_domain",
                    operation="orphan_cleanup",
                    reason="missing_node_metadata",
                ).inc(skipped_metadata)
                logger.info(
                    "Skipped %d libvirt domain(s) without node metadata during orphan cleanup for lab %s",
                    skipped_metadata,
                    lab_id,
                )

            if workspace_base and removed_node_names:
                lab_workspace = workspace_base / lab_id
                disks_dir = lab_workspace / "disks"
                if disks_dir.exists():
                    for nname in removed_node_names:
                        for disk_file in disks_dir.iterdir():
                            if disk_file.name.startswith(nname):
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

    async def cleanup_lab_orphan_domains(
        self,
        lab_id: str,
        keep_node_names: set[str],
        workspace_base: Path | None = None,
    ) -> dict[str, list[str]]:
        """Remove VMs for nodes that no longer exist within a specific lab."""
        return await self._run_libvirt(
            self._cleanup_lab_orphan_domains_sync, lab_id, keep_node_names, workspace_base,
        )
