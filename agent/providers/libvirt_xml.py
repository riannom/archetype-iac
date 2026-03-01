"""Domain XML generation, disk creation, and related helpers for LibvirtProvider.

Extracted from libvirt.py to reduce file size.  All functions are standalone
and accept explicit parameters instead of ``self``.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import socket
import subprocess
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from agent.config import settings
from agent.vendors import get_vendor_config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# MAC helpers
# ---------------------------------------------------------------------------

def generate_mac_address(domain_name: str, interface_index: int) -> str:
    """Generate a deterministic MAC address for a VM interface.

    Uses domain name and interface index to generate consistent MACs.
    Format: 52:54:00:XX:XX:XX (QEMU/KVM OUI prefix)
    """
    # Create deterministic hash from domain name and interface index
    hash_input = f"{domain_name}:{interface_index}".encode()
    hash_bytes = hashlib.md5(hash_input).digest()
    # Use QEMU/KVM OUI prefix (52:54:00) + 3 bytes from hash
    mac = f"52:54:00:{hash_bytes[0]:02x}:{hash_bytes[1]:02x}:{hash_bytes[2]:02x}"
    return mac


# ---------------------------------------------------------------------------
# OVMF / EFI helpers
# ---------------------------------------------------------------------------

def find_ovmf_code_path() -> str | None:
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


def find_ovmf_vars_template() -> str | None:
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


# ---------------------------------------------------------------------------
# Domain driver resolution
# ---------------------------------------------------------------------------

def resolve_domain_driver(
    requested: str | None,
    node_name: str,
    allowed_drivers: set[str],
) -> str:
    """Resolve and validate libvirt domain driver.

    Policy:
    - enforce when value is valid (kvm|qemu)
    - warn and fall back to kvm when invalid/unsupported
    """
    candidate = (requested or "kvm").strip().lower()
    if candidate in allowed_drivers:
        return candidate
    logger.warning(
        "Invalid libvirt_driver '%s' for %s; falling back to 'kvm'",
        requested,
        node_name,
    )
    return "kvm"


# ---------------------------------------------------------------------------
# Disk creation helpers
# ---------------------------------------------------------------------------

def translate_container_path_to_host(path: str) -> str:
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


def create_overlay_disk_sync(
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
    host_base_image = translate_container_path_to_host(base_image)
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


def create_data_volume_sync(
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


# ---------------------------------------------------------------------------
# vJunos SVM compatibility patch
# ---------------------------------------------------------------------------

def patch_vjunos_svm_compat(overlay_path: Path) -> bool:
    """Patch vJunos overlay disk to support AMD SVM for nested virtualization.

    vJunos images check /proc/cpuinfo for 'vmx' (Intel) only. On AMD hosts,
    the CPU flag is 'svm'. This patches start-junos.sh to accept both flags.
    Uses qemu-nbd to mount the overlay, patch in-place, and unmount.

    Returns True if patched (or already patched), False on error.
    """
    import tempfile

    nbd_dev = None
    mount_dir = None
    try:
        # Find a free nbd device
        subprocess.run(
            ["modprobe", "nbd", "max_part=8"],
            capture_output=True, timeout=10,
        )
        for i in range(16):
            dev = f"/dev/nbd{i}"
            # Check if this nbd device has a connected disk (size > 0)
            size_path = Path(f"/sys/block/nbd{i}/size")
            if size_path.exists():
                try:
                    size = int(size_path.read_text().strip())
                    if size == 0:
                        nbd_dev = dev
                        break
                except (ValueError, OSError):
                    continue
        if not nbd_dev:
            logger.warning("No free nbd device found for vJunos SVM patch")
            return False

        # Connect the overlay disk
        result = subprocess.run(
            ["qemu-nbd", "--connect", nbd_dev, str(overlay_path)],
            capture_output=True, timeout=30,
        )
        if result.returncode != 0:
            logger.warning("qemu-nbd connect failed: %s", result.stderr.decode())
            nbd_dev = None
            return False

        # Wait for partitions to appear
        subprocess.run(["partprobe", nbd_dev], capture_output=True, timeout=10)
        time.sleep(1)

        # Mount partition 2 (Linux root)
        part2 = f"{nbd_dev}p2"
        mount_dir = tempfile.mkdtemp(prefix="vjunos-patch-")
        result = subprocess.run(
            ["mount", part2, mount_dir],
            capture_output=True, timeout=30,
        )
        if result.returncode != 0:
            logger.warning("Failed to mount %s: %s", part2, result.stderr.decode())
            return False

        # Find and patch start-junos.sh
        script_path = Path(mount_dir) / "home" / "pfe" / "junos" / "start-junos.sh"
        if not script_path.exists():
            logger.info("start-junos.sh not found at %s; skipping SVM patch", script_path)
            return True  # Not a vJunos image, nothing to patch

        content = script_path.read_text()
        old_check = "grep -ci vmx"
        new_check = 'grep -ciE "vmx|svm"'
        if "svm" in content:
            logger.info("start-junos.sh already patched for SVM support")
            return True
        if old_check not in content:
            logger.warning("start-junos.sh has unexpected vmx check; skipping patch")
            return True

        content = content.replace(old_check, new_check)
        script_path.write_text(content)
        logger.info("Patched start-junos.sh for AMD SVM compatibility")
        return True

    except Exception as e:
        logger.warning("vJunos SVM patch failed: %s", e)
        return False
    finally:
        # Clean up: unmount and disconnect
        if mount_dir:
            subprocess.run(["umount", mount_dir], capture_output=True, timeout=10)
            try:
                Path(mount_dir).rmdir()
            except OSError:
                pass
        if nbd_dev:
            subprocess.run(
                ["qemu-nbd", "--disconnect", nbd_dev],
                capture_output=True, timeout=10,
            )


# ---------------------------------------------------------------------------
# TCP serial port helpers
# ---------------------------------------------------------------------------

def allocate_tcp_serial_port() -> int:
    """Allocate a free TCP port for serial console.

    Uses the OS to find an available port by binding to port 0.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get_tcp_serial_port(domain) -> int | None:
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


# ---------------------------------------------------------------------------
# Domain XML generation
# ---------------------------------------------------------------------------

def generate_domain_xml(
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
    *,
    valid_machine_types: set[str],
    valid_disk_drivers: set[str],
    valid_nic_drivers: set[str],
    nic_driver_substitutions: dict[str, str],
    allowed_domain_drivers: set[str],
    mac_generator=generate_mac_address,
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
        valid_machine_types: Set of whitelisted machine types
        valid_disk_drivers: Set of whitelisted disk drivers
        valid_nic_drivers: Set of whitelisted NIC drivers
        nic_driver_substitutions: Map of unsupported -> replacement NIC drivers
        allowed_domain_drivers: Set of allowed libvirt domain drivers
        mac_generator: Callable to generate MAC addresses

    Returns:
        Domain XML string
    """
    # Get resource requirements from node config
    memory_mb = node_config.get("memory", 2048)
    cpus = node_config.get("cpu", 1)
    cpu_limit = node_config.get("cpu_limit")

    # Get driver and machine settings (whitelist-validated)
    machine_type = node_config.get("machine_type", "pc-q35-6.2")
    if machine_type not in valid_machine_types:
        raise ValueError(f"Invalid machine type: {machine_type}")
    disk_driver = node_config.get("disk_driver", "virtio")
    if disk_driver not in valid_disk_drivers:
        raise ValueError(f"Invalid disk driver: {disk_driver}")
    nic_driver = node_config.get("nic_driver", "virtio")
    if nic_driver in nic_driver_substitutions:
        replacement = nic_driver_substitutions[nic_driver]
        logger.warning(
            f"NIC driver '{nic_driver}' unsupported by QEMU, "
            f"substituting '{replacement}' for node {name}"
        )
        nic_driver = replacement
    if nic_driver not in valid_nic_drivers:
        raise ValueError(f"Invalid NIC driver: {nic_driver}")
    libvirt_driver = resolve_domain_driver(
        node_config.get("libvirt_driver"),
        name,
        allowed_domain_drivers,
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

    if config_disk_path:
        disks_xml += f'''
    <controller type='usb' model='qemu-xhci'/>
    <disk type='file' device='disk'>
      <driver name='qemu' type='raw'/>
      <source file='{xml_escape(str(config_disk_path))}'/>
      <target dev='sda' bus='usb'/>
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
        mgmt_mac = mac_generator(name, 0)
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
        mac_address = mac_generator(name, r + data_interface_mac_offset)
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
        mac_address = mac_generator(name, i + data_interface_mac_offset)
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

        # Only store readiness overrides in domain XML that differ from
        # vendor defaults.  This prevents stale vendor defaults from being
        # locked into domain XML across vendor config updates.
        vendor_cfg = get_vendor_config(kind)
        if vendor_cfg:
            if readiness_probe == vendor_cfg.readiness_probe:
                readiness_probe = None
            if readiness_pattern == vendor_cfg.readiness_pattern:
                readiness_pattern = None
            if readiness_timeout is not None:
                try:
                    if int(readiness_timeout) == vendor_cfg.readiness_timeout:
                        readiness_timeout = None
                except (TypeError, ValueError):
                    pass

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
        ovmf_code = find_ovmf_code_path()
        ovmf_vars = find_ovmf_vars_template()
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
    # Libvirt <log> element tees serial output to a file for lock-free observation
    log_xml = ""
    if serial_log_path:
        log_xml = f"\n      <log file='{xml_escape(str(serial_log_path))}' append='off'/>"
    if serial_type == "tcp":
        tcp_port = allocate_tcp_serial_port()
        serial_xml = f"""    <serial type='tcp'>
      <source mode='bind' host='127.0.0.1' service='{tcp_port}'/>
      <protocol type='telnet'/>{log_xml}
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
        serial_xml = f"""    <serial type='pty'>{log_xml}
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
    cpu_features_disable = node_config.get("cpu_features_disable", [])
    cpu_children: list[str] = []
    if cpu_sockets > 0:
        cores = max(1, cpus // cpu_sockets)
        cpu_children.append(
            f"    <topology sockets='{cpu_sockets}' cores='{cores}' threads='1'/>"
        )
    for feat in cpu_features_disable:
        cpu_children.append(f"    <feature policy='disable' name='{feat}'/>")
    if cpu_children:
        cpu_xml = (
            "<cpu mode='host-passthrough' migratable='off'>\n"
            + "\n".join(cpu_children) + "\n"
            "  </cpu>"
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
