"""Vendor-specific configurations for network devices.

This module provides a centralized registry of vendor configurations
including console shell commands, default images, device aliases, and UI metadata.

SINGLE SOURCE OF TRUTH: This is the authoritative source for all vendor/device
configuration. The API and frontend consume this registry.

When adding a new vendor:
1. Add entry to VENDOR_CONFIGS with all fields
2. Test console access with a running container
3. Rebuild containers: docker compose -f docker-compose.gui.yml up -d --build
4. New device will appear in API (/vendors) and UI automatically
"""

from dataclasses import dataclass, field
from enum import Enum
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class DeviceType(str, Enum):
    """Device type classification for UI categorization."""
    ROUTER = "router"
    SWITCH = "switch"
    FIREWALL = "firewall"
    HOST = "host"
    CONTAINER = "container"
    EXTERNAL = "external"


# =============================================================================
# COMPOSED SUB-CONFIGURATIONS
# =============================================================================
# These dataclasses group related VendorConfig fields into logical units.
# Access via VendorConfig properties: config.interfaces, config.vm, etc.
# =============================================================================

@dataclass(frozen=True)
class InterfaceConfig:
    """Interface/port configuration for a device."""
    port_naming: str
    port_start_index: int
    max_ports: int
    management_interface: Optional[str]


@dataclass(frozen=True)
class ResourceConfig:
    """Resource requirements."""
    memory: int  # MB
    cpu: int  # cores


@dataclass(frozen=True)
class VMConfig:
    """Libvirt/QEMU VM settings."""
    disk_driver: str
    nic_driver: str
    machine_type: str
    data_volume_gb: int
    efi_boot: bool
    efi_vars: str
    serial_type: str
    nographic: bool
    serial_port_count: int
    smbios_product: str
    force_stop: bool
    reserved_nics: int
    cpu_sockets: int
    needs_nested_vmx: bool


@dataclass(frozen=True)
class ConsoleConfig:
    """Console access configuration."""
    console_method: str
    console_shell: str
    console_user: str
    console_password: str
    default_credentials: str


@dataclass(frozen=True)
class ReadinessConfig:
    """Boot readiness detection configuration."""
    readiness_probe: str
    readiness_pattern: Optional[str]
    readiness_timeout: int


@dataclass(frozen=True)
class ConfigExtractionConfig:
    """Configuration extraction settings."""
    config_extract_method: str
    config_extract_command: str
    config_extract_user: str
    config_extract_password: str
    config_extract_enable_password: str
    config_extract_timeout: int
    config_extract_prompt_pattern: str
    config_extract_paging_disable: str


@dataclass(frozen=True)
class ConfigInjectionConfig:
    """Configuration injection settings."""
    config_inject_method: str
    config_inject_partition: int
    config_inject_fs_type: str
    config_inject_path: str
    config_inject_iso_volume_label: str
    config_inject_iso_filename: str


@dataclass(frozen=True)
class ContainerConfig:
    """Container runtime configuration."""
    environment: dict
    capabilities: list
    privileged: bool
    binds: list
    entrypoint: Optional[str]
    cmd: Optional[list]
    network_mode: str
    sysctls: dict
    runtime: str
    hostname_template: str
    post_boot_commands: list


@dataclass(frozen=True)
class UIConfig:
    """Frontend UI display configuration."""
    icon: str
    versions: list
    is_active: bool
    requires_image: bool
    supported_image_kinds: list
    documentation_url: Optional[str]
    license_required: bool
    tags: list


@dataclass
class VendorConfig:
    """Configuration for a vendor's network device kind.

    Fields:
        kind: Device kind identifier (e.g., "ceos") - used in topology YAML
        vendor: Vendor name for display (e.g., "Arista")
        console_shell: Shell command for console access
        default_image: Default Docker image when none specified
        notes: Usage notes and documentation
        aliases: Alternative device names that resolve to this kind
        device_type: Classification for UI categorization
        category: Top-level UI category (Network, Security, Compute, Cloud & External)
        subcategory: Optional subcategory (Routers, Switches, Load Balancers)
        label: Display name for UI (e.g., "Arista EOS")
        icon: FontAwesome icon class
        versions: Available version options
        is_active: Whether device is available in UI
        port_naming: Interface naming pattern (eth, Ethernet, GigabitEthernet)
        port_start_index: Starting port number (0 or 1)
        max_ports: Maximum number of interfaces
        requires_image: Whether user must provide/import an image
        supported_image_kinds: List of supported image types (docker, qcow2)
        documentation_url: Link to vendor documentation
        license_required: Whether device requires commercial license
        tags: Searchable tags for filtering (e.g., ["bgp", "mpls"])
    """

    # Core fields (used by agent for console access)
    kind: str
    vendor: str
    console_shell: str
    default_image: Optional[str]
    notes: str = ""

    # Alias resolution (used by topology.py)
    aliases: list[str] = field(default_factory=list)

    # Platform grouping (e.g., "cisco_cat9kv" for Cat9800/Cat9000v variants)
    # Used to group related devices that share image artifacts.
    platform: str = ""

    # UI metadata (used by frontend)
    device_type: DeviceType = DeviceType.CONTAINER
    category: str = "Compute"
    subcategory: Optional[str] = None
    label: str = ""
    icon: str = "fa-box"
    versions: list[str] = field(default_factory=lambda: ["latest"])
    is_active: bool = True

    # Interface/port configuration
    port_naming: str = "eth"
    port_start_index: int = 0
    max_ports: int = 8
    management_interface: Optional[str] = None  # NOS-level management interface name (e.g., "mgmt0")
    # Note: provision_interfaces is deprecated. OVS-based networking handles
    # interface provisioning automatically for all device types.

    # Resource requirements
    memory: int = 1024  # Memory in MB
    cpu: int = 1  # CPU cores

    # Libvirt/QEMU VM settings (for qcow2-based devices)
    disk_driver: str = "virtio"  # Disk bus type: virtio, ide, sata
    nic_driver: str = "virtio"   # NIC model: virtio, e1000, rtl8139
    machine_type: str = "pc-q35-6.2"  # QEMU machine type: pc-q35-* (modern), pc-i440fx-* (legacy IDE)
    data_volume_gb: int = 0      # Size of additional data volume (0 = none)
    efi_boot: bool = False       # Boot with UEFI firmware (OVMF) instead of legacy BIOS
    efi_vars: str = ""           # EFI NVRAM mode: "" (stateful, default), "stateless" (no persistent NVRAM)
    serial_type: str = "pty"     # Serial port type: "pty" (default virsh console), "tcp" (TCP telnet)
    nographic: bool = False      # Remove VGA/VNC display; forces UEFI output to serial console
    serial_port_count: int = 1   # Number of serial ports (IOS-XRv 9000 needs 4)
    smbios_product: str = ""     # SMBIOS type=1 product string (e.g., "Cisco IOS XRv 9000")
    force_stop: bool = True      # Skip ACPI graceful shutdown (most network VMs don't support it)
    reserved_nics: int = 0        # Dummy NICs inserted after management, before data (XRv9k needs 2)
    cpu_sockets: int = 0          # If >0, explicit SMP topology: sockets=N, cores=cpu/N, threads=1
    needs_nested_vmx: bool = False  # Force VMX CPU flag (vJunos checks /proc/cpuinfo for vmx even on AMD)

    # Image requirements
    requires_image: bool = True
    supported_image_kinds: list[str] = field(default_factory=lambda: ["docker"])

    # Documentation and licensing
    documentation_url: Optional[str] = None
    license_required: bool = False

    # Searchable tags
    tags: list[str] = field(default_factory=list)

    # Image detection fields (used to derive detection maps from VENDOR_CONFIGS)
    # filename_patterns: regex patterns for qcow2 filename detection
    filename_patterns: list[str] = field(default_factory=list)
    # filename_keywords: substring keywords for Docker tar filename detection
    filename_keywords: list[str] = field(default_factory=list)
    # vrnetlab_subdir: vrnetlab build subdirectory (e.g., "cisco/c8000v")
    vrnetlab_subdir: str = ""

    # Boot readiness detection
    # - "none": No probe, always considered ready when container is running
    # - "log_pattern": Check container logs for boot completion pattern
    # - "cli_probe": Execute CLI command and check for expected output
    readiness_probe: str = "none"
    readiness_pattern: Optional[str] = None  # Regex pattern for log/cli detection
    readiness_timeout: int = 120  # Max seconds to wait for ready state

    # Console access method
    # - "docker_exec": Use docker exec with console_shell (default for native containers)
    # - "ssh": Use SSH to container IP (for vrnetlab/VM-based devices)
    console_method: str = "docker_exec"
    console_user: str = "admin"  # Username for SSH console access
    console_password: str = "admin"  # Password for SSH console access

    # Display-only hint for UI, e.g. "admin / admin"
    default_credentials: str = ""

    # ==========================================================================
    # Configuration extraction settings (used by console_extractor.py)
    # These settings control how running configs are extracted from devices
    # ==========================================================================

    # Method for extracting config: "serial" (virsh console), "docker" (docker exec), "ssh", "none"
    config_extract_method: str = "none"
    # Command to run to extract config (e.g., "show running-config")
    config_extract_command: str = "show running-config"
    # Login username (empty = no login required, device boots to CLI prompt)
    config_extract_user: str = ""
    # Login password
    config_extract_password: str = ""
    # Enable mode password (empty = no enable needed or enable has no password)
    config_extract_enable_password: str = ""
    # Timeout in seconds for extraction process
    config_extract_timeout: int = 30
    # Regex pattern to detect CLI prompt (used to know when command output is complete)
    config_extract_prompt_pattern: str = r"[\w\-]+[>#]\s*$"
    # Command to disable paging (empty = use default for device type)
    config_extract_paging_disable: str = ""

    # ==========================================================================
    # Configuration injection settings (used by LibvirtProvider)
    # These settings control how startup configs are written into VM disks
    # ==========================================================================

    # Injection method: "none", "bootflash" (qemu-nbd mount+write), or "iso" (CD-ROM)
    config_inject_method: str = "none"
    # Partition number to mount (0 = auto-detect via blkid) — bootflash only
    config_inject_partition: int = 0
    # Expected filesystem type of the target partition — bootflash only
    config_inject_fs_type: str = "ext2"
    # Path within the mounted partition where startup-config is written — bootflash only
    config_inject_path: str = "/startup-config"
    # ISO 9660 volume label for config CD-ROM — iso only (e.g., "config-1" for IOS-XR CVAC)
    config_inject_iso_volume_label: str = ""
    # Filename inside the ISO — iso only (e.g., "iosxr_config.txt" for IOS-XR CVAC)
    config_inject_iso_filename: str = ""

    # ==========================================================================
    # Container runtime configuration (used by DockerProvider)
    # These settings control how containers are created and configured
    # ==========================================================================

    # Environment variables to set in the container
    # Keys are variable names, values are the values to set
    environment: dict[str, str] = field(default_factory=dict)

    # Linux capabilities to add to the container
    # Common: NET_ADMIN (required for networking), SYS_ADMIN (for some vendor devices)
    capabilities: list[str] = field(default_factory=lambda: ["NET_ADMIN"])

    # Whether to run the container in privileged mode
    # Required for some vendors (cEOS, SR Linux) that need full system access
    privileged: bool = False

    # Volume mounts in "host:container" format
    # Use {workspace} placeholder for lab workspace directory
    # Example: ["{workspace}/configs/{node}/flash:/mnt/flash"]
    binds: list[str] = field(default_factory=list)

    # Override the default entrypoint
    entrypoint: Optional[str] = None

    # Override the default command
    cmd: Optional[list[str]] = None

    # Network mode for container
    # "none": No networking (links added manually)
    # "bridge": Use default bridge (for management)
    network_mode: str = "none"

    # Sysctls to set in the container
    sysctls: dict[str, str] = field(default_factory=dict)

    # Runtime type (e.g., "runsc" for gVisor, empty for default)
    runtime: str = ""

    # Hostname template - use {node} for node name
    hostname_template: str = "{node}"

    # Post-boot commands to run after container is ready
    # These commands are executed inside the container once after boot completion
    # Use for vendor-specific workarounds (e.g., removing iptables rules)
    post_boot_commands: list[str] = field(default_factory=list)

    # -------------------------------------------------------------------------
    # Composed sub-configuration property accessors
    # These return frozen dataclass views grouping related fields.
    # -------------------------------------------------------------------------

    @property
    def interfaces(self) -> InterfaceConfig:
        return InterfaceConfig(
            port_naming=self.port_naming,
            port_start_index=self.port_start_index,
            max_ports=self.max_ports,
            management_interface=self.management_interface,
        )

    @property
    def resources(self) -> ResourceConfig:
        return ResourceConfig(memory=self.memory, cpu=self.cpu)

    @property
    def vm(self) -> VMConfig:
        return VMConfig(
            disk_driver=self.disk_driver,
            nic_driver=self.nic_driver,
            machine_type=self.machine_type,
            data_volume_gb=self.data_volume_gb,
            efi_boot=self.efi_boot,
            efi_vars=self.efi_vars,
            serial_type=self.serial_type,
            nographic=self.nographic,
            serial_port_count=self.serial_port_count,
            smbios_product=self.smbios_product,
            force_stop=self.force_stop,
            reserved_nics=self.reserved_nics,
            cpu_sockets=self.cpu_sockets,
            needs_nested_vmx=self.needs_nested_vmx,
        )

    @property
    def console(self) -> ConsoleConfig:
        return ConsoleConfig(
            console_method=self.console_method,
            console_shell=self.console_shell,
            console_user=self.console_user,
            console_password=self.console_password,
            default_credentials=self.default_credentials,
        )

    @property
    def readiness(self) -> ReadinessConfig:
        return ReadinessConfig(
            readiness_probe=self.readiness_probe,
            readiness_pattern=self.readiness_pattern,
            readiness_timeout=self.readiness_timeout,
        )

    @property
    def config_extraction(self) -> ConfigExtractionConfig:
        return ConfigExtractionConfig(
            config_extract_method=self.config_extract_method,
            config_extract_command=self.config_extract_command,
            config_extract_user=self.config_extract_user,
            config_extract_password=self.config_extract_password,
            config_extract_enable_password=self.config_extract_enable_password,
            config_extract_timeout=self.config_extract_timeout,
            config_extract_prompt_pattern=self.config_extract_prompt_pattern,
            config_extract_paging_disable=self.config_extract_paging_disable,
        )

    @property
    def config_injection(self) -> ConfigInjectionConfig:
        return ConfigInjectionConfig(
            config_inject_method=self.config_inject_method,
            config_inject_partition=self.config_inject_partition,
            config_inject_fs_type=self.config_inject_fs_type,
            config_inject_path=self.config_inject_path,
            config_inject_iso_volume_label=self.config_inject_iso_volume_label,
            config_inject_iso_filename=self.config_inject_iso_filename,
        )

    @property
    def container(self) -> ContainerConfig:
        return ContainerConfig(
            environment=self.environment,
            capabilities=self.capabilities,
            privileged=self.privileged,
            binds=self.binds,
            entrypoint=self.entrypoint,
            cmd=self.cmd,
            network_mode=self.network_mode,
            sysctls=self.sysctls,
            runtime=self.runtime,
            hostname_template=self.hostname_template,
            post_boot_commands=self.post_boot_commands,
        )

    @property
    def ui(self) -> UIConfig:
        return UIConfig(
            icon=self.icon,
            versions=self.versions,
            is_active=self.is_active,
            requires_image=self.requires_image,
            supported_image_kinds=self.supported_image_kinds,
            documentation_url=self.documentation_url,
            license_required=self.license_required,
            tags=self.tags,
        )


# =============================================================================
# VENDOR CONFIGURATIONS - Single Source of Truth
# =============================================================================
# Add new vendors here. They will automatically appear in:
# - Console access (agent uses console_shell)
# - Topology generation (API uses aliases and default_image)
# - UI device palette (frontend uses category, label, icon, versions)
#
# KEY NAMING CONVENTION:
#   - Industry shorthand for well-known: ceos, vyos, frr, linux, alpine, tcl
#   - vendor_device for vendor-specific: cisco_iosv, cisco_n9kv, juniper_vjunosrouter
#   - Hyphenated for sub-models: cat9000v-q200, cat9000v-uadp, iol-xe, iol-l2
#   - All keys are lowercase; underscores separate vendor from device
# =============================================================================

VENDOR_CONFIGS: dict[str, VendorConfig] = {
    # =========================================================================
    # NETWORK DEVICES - Routers
    # =========================================================================
    "vyos": VendorConfig(
        kind="vyos",
        vendor="VyOS",
        console_shell="/bin/vbash",
        default_image="vyos/vyos:1.4-rolling",
        aliases=[],
        device_type=DeviceType.ROUTER,
        category="Network",
        subcategory="Routers",
        label="VyOS",
        icon="fa-arrows-to-dot",
        versions=[],
        is_active=True,
        notes="VyOS uses vbash for configuration mode.",
        port_naming="eth",
        port_start_index=0,
        max_ports=8,
        requires_image=False,
        documentation_url="https://docs.vyos.io/",
        tags=["routing", "firewall", "vpn", "bgp", "ospf"],
        # Config extraction via docker exec
        config_extract_method="docker",
        config_extract_command="/opt/vyatta/sbin/vyatta-cfg-cmd-wrapper show configuration commands",
        config_extract_timeout=15,
        # Container runtime configuration
        capabilities=["NET_ADMIN", "SYS_ADMIN"],
        privileged=True,
        sysctls={
            "net.ipv4.ip_forward": "1",
            "net.ipv6.conf.all.forwarding": "1",
        },
        default_credentials="vyos / vyos",
    ),
    "cisco_iosxr": VendorConfig(
        kind="cisco_iosxr",
        vendor="Cisco",
        console_shell="/bin/bash",
        default_image="ios-xr:latest",
        aliases=["iosxrv9000", "iosxr", "xrv9k"],
        device_type=DeviceType.ROUTER,
        category="Network",
        subcategory="Routers",
        label="Cisco IOS-XR",
        icon="fa-arrows-to-dot",
        versions=[],
        is_active=True,
        notes="IOS-XRv 9000 VM. First 3 interfaces are MgmtEth0/RP0/CPU0/0 + 2x donotuse; data starts at 4th.",
        # Interfaces: MgmtEth0/RP0/CPU0/0, donotuse0, donotuse1, Gi0/0/0/0, Gi0/0/0/1, ...
        port_naming="GigabitEthernet0/0/0/{index}",
        port_start_index=0,
        max_ports=8,
        management_interface="MgmtEth0/RP0/CPU0/0",
        # VM settings per CML reference platform (iosxrv9000 node definition)
        memory=20480,  # 20GB — CML minimum 10GB, recommended 20GB
        cpu=4,
        machine_type="pc",  # i440fx — XRv9000 expects i440fx disk paths (q35 breaks Spirit/grub.cfg)
        disk_driver="virtio",
        nic_driver="virtio",
        efi_boot=True,
        efi_vars="stateless",  # vrnetlab uses single pflash (CODE only); extra NVRAM pflash breaks Spirit grub.cfg
        nographic=True,  # Remove VGA so OVMF outputs to serial (virsh console)
        serial_port_count=4,  # CML refplat: XR console, aux, calvados console, calvados aux
        serial_type="tcp",  # XR CLI lives on TCP telnet serial, not PTY
        smbios_product="Cisco IOS XRv 9000",  # Required for platform identification
        reserved_nics=2,  # ctrl-dummy + dev-dummy between mgmt and data NICs
        cpu_sockets=1,    # SMP: 1 socket × N cores (Spirit bootstrap requires cores, not sockets)
        requires_image=True,
        supported_image_kinds=["qcow2"],
        # TCP serial is single-connection (QEMU allows one client), so readiness probe can't
        # read console output without blocking the user's session. Skip the wait.
        readiness_probe="none",
        # Config extraction via SSH (console is TCP serial, not docker exec)
        config_extract_method="ssh",
        config_extract_command="show running-config",
        config_extract_user="cisco",
        config_extract_password="cisco",
        config_extract_timeout=30,
        config_extract_prompt_pattern=r"RP/\d+/RP\d+/CPU\d+:[\w\-]+#",
        console_user="cisco",
        console_password="cisco",
        documentation_url="https://www.cisco.com/c/en/us/td/docs/iosxr/",
        license_required=True,
        tags=["routing", "bgp", "mpls", "segment-routing", "netconf"],
        filename_patterns=[r"xrv9k[_-]?[\d\.]+.*\.qcow2", r"iosxrv9000[_-]?[\d\.]+.*\.qcow2"],
        vrnetlab_subdir="cisco/xrv9k",
        # Config injection: IOS-XR CVAC reads config from CD-ROM with label "config-1"
        config_inject_method="iso",
        config_inject_iso_volume_label="config-1",
        config_inject_iso_filename="iosxr_config.txt",
        default_credentials="cisco / cisco",
    ),
    "cisco_xrd": VendorConfig(
        kind="cisco_xrd",
        vendor="Cisco",
        console_shell="/bin/bash",
        default_image="ios-xrd:latest",
        aliases=[],
        device_type=DeviceType.ROUTER,
        category="Network",
        subcategory="Routers",
        label="Cisco XRd",
        icon="fa-arrows-to-dot",
        versions=[],
        is_active=True,
        notes="XRd container variant of IOS-XR.",
        port_naming="GigabitEthernet0/0/0/{index}",
        port_start_index=0,
        max_ports=8,
        management_interface="MgmtEth0/RP0/CPU0/0",
        requires_image=True,
        documentation_url="https://www.cisco.com/c/en/us/td/docs/iosxr/cisco8000/xrd/",
        license_required=True,
        tags=["routing", "bgp", "mpls", "segment-routing", "container"],
        filename_patterns=[r"xrd[_-]?[\d\.]+.*\.qcow2"],
        vrnetlab_subdir="cisco/xrd",
        default_credentials="cisco / cisco",
    ),
    "cisco_iosv": VendorConfig(
        kind="cisco_iosv",
        vendor="Cisco",
        console_shell="/bin/sh",
        default_image=None,  # Requires user-provided image
        aliases=["iosv"],
        device_type=DeviceType.ROUTER,
        category="Network",
        subcategory="Routers",
        label="Cisco IOSv",
        icon="fa-arrows-to-dot",
        versions=[],
        is_active=True,
        notes="IOSv requires QEMU/libvirt. User must import image.",
        port_naming="GigabitEthernet0/{index}",
        port_start_index=0,
        max_ports=8,
        memory=2048,  # 2GB recommended
        cpu=1,
        machine_type="pc-i440fx-6.2",  # IOSv GRUB requires IDE; only i440fx has IDE controller
        disk_driver="ide",  # IOSv GRUB needs IDE for flash/NVRAM writes
        nic_driver="e1000",  # IOSv requires e1000 NICs
        requires_image=True,
        supported_image_kinds=["qcow2"],
        documentation_url="https://www.cisco.com/c/en/us/td/docs/ios/",
        license_required=True,
        tags=["routing", "bgp", "ospf", "eigrp", "legacy", "vm"],
        filename_patterns=[r"vios[_-]?[\d\.]+.*\.qcow2", r"iosv[_-]?[\d\.]+.*\.qcow2"],
        filename_keywords=["iosv"],
        vrnetlab_subdir="cisco/iosv",
        readiness_probe="log_pattern",
        readiness_pattern=r"Press RETURN to get started|[\w.-]+[>#]",
        readiness_timeout=180,
        # Config extraction via serial console
        config_extract_method="serial",
        config_extract_command="show running-config",
        config_extract_timeout=30,
        config_extract_prompt_pattern=r"[\w\-]+[>#]\s*$",
        config_extract_paging_disable="terminal length 0",
        # Post-boot commands to run after VM is ready
        post_boot_commands=[
            "terminal length 0",  # Disable paging for CLI sessions
            "no ip domain-lookup",  # Disable DNS lookups that slow down CLI
            # Ensure data interfaces are enabled even when imported startup-configs
            # omit explicit "no shutdown" lines.
            "configure terminal",
            "interface GigabitEthernet0/0",
            "no shutdown",
            "interface GigabitEthernet0/1",
            "no shutdown",
            "interface GigabitEthernet0/2",
            "no shutdown",
            "interface GigabitEthernet0/3",
            "no shutdown",
            "interface GigabitEthernet0/4",
            "no shutdown",
            "interface GigabitEthernet0/5",
            "no shutdown",
            "interface GigabitEthernet0/6",
            "no shutdown",
            "interface GigabitEthernet0/7",
            "no shutdown",
            "end",
        ],
    ),
    "iosvl2": VendorConfig(
        kind="iosvl2",
        vendor="Cisco",
        console_shell="/bin/sh",
        default_image=None,
        aliases=["iosv-l2"],
        device_type=DeviceType.SWITCH,
        category="Network",
        subcategory="Switches",
        label="Cisco IOSv-L2",
        icon="fa-arrows-left-right-to-line",
        versions=[],
        is_active=True,
        notes="Cisco IOSv-L2 virtual switch. Requires QEMU/libvirt.",
        port_naming="GigabitEthernet0/{index}",
        port_start_index=0,
        max_ports=16,
        memory=1024,
        cpu=1,
        machine_type="pc-i440fx-6.2",
        disk_driver="ide",
        nic_driver="e1000",
        requires_image=True,
        supported_image_kinds=["qcow2"],
        documentation_url="https://developer.cisco.com/docs/modeling-labs/#!iosvl2",
        license_required=True,
        tags=["switch", "ios", "l2", "stp", "vm"],
        filename_patterns=[r"iosvl2[_-]?[\d\.]+.*\.qcow2"],
        filename_keywords=["viosl2", "iosvl2"],
        vrnetlab_subdir="cisco/iosvl2",
        readiness_probe="log_pattern",
        readiness_pattern=r"Would you like to enter the initial configuration dialog?",
        readiness_timeout=300,
    ),
    "iol-xe": VendorConfig(
        kind="iol-xe",
        vendor="Cisco",
        console_shell="screen -x iol",
        default_image=None,
        aliases=["iol", "iol-xe-serial-4eth"],
        device_type=DeviceType.ROUTER,
        category="Network",
        subcategory="Routers",
        label="Cisco IOL XE",
        icon="fa-arrows-to-dot",
        versions=[],
        is_active=True,
        notes="Cisco IOS on Linux (IOL XE) router image. "
              "Upload .bin/.iol binary to auto-build Docker image.",
        port_naming="Ethernet0/{index}",
        port_start_index=0,
        max_ports=32,
        memory=1024,
        cpu=1,
        requires_image=True,
        supported_image_kinds=["iol", "docker"],
        documentation_url="https://developer.cisco.com/docs/modeling-labs/#!iol",
        license_required=True,
        tags=["router", "ios", "iol", "container"],
        filename_keywords=["l3-adventerprise", "iol"],
        readiness_probe="log_pattern",
        readiness_pattern=r"Press RETURN to get started!",
        readiness_timeout=120,
        # Container runtime
        environment={"IOL_PID": "1"},
        capabilities=["NET_ADMIN"],
        binds=[
            "{workspace}/configs/{node}/iol-data:/iol/data",
            "{workspace}/configs/{node}:/iol/configs:ro",
        ],
        # Config extraction via NVRAM binary
        config_extract_method="nvram",
        config_extract_timeout=5,
    ),
    "iol-l2": VendorConfig(
        kind="iol-l2",
        vendor="Cisco",
        console_shell="screen -x iol",
        default_image=None,
        aliases=["ioll2-xe"],
        device_type=DeviceType.SWITCH,
        category="Network",
        subcategory="Switches",
        label="Cisco IOL-L2",
        icon="fa-arrows-left-right-to-line",
        versions=[],
        is_active=True,
        notes="Cisco IOS on Linux (IOL-L2) switch image. "
              "Upload .bin/.iol binary to auto-build Docker image.",
        port_naming="Ethernet0/{index}",
        port_start_index=0,
        max_ports=32,
        memory=1024,
        cpu=1,
        requires_image=True,
        supported_image_kinds=["iol", "docker"],
        documentation_url="https://developer.cisco.com/docs/modeling-labs/#!iol",
        license_required=True,
        tags=["switch", "ios", "iol", "l2", "container"],
        filename_keywords=["ioll2", "iol_l2", "l2-adventerprise", "l2-ipbasek9"],
        readiness_probe="log_pattern",
        readiness_pattern=r"Press RETURN to get started!",
        readiness_timeout=120,
        # Container runtime
        environment={"IOL_PID": "1"},
        capabilities=["NET_ADMIN"],
        binds=[
            "{workspace}/configs/{node}/iol-data:/iol/data",
            "{workspace}/configs/{node}:/iol/configs:ro",
        ],
        # Config extraction via NVRAM binary
        config_extract_method="nvram",
        config_extract_timeout=5,
    ),
    "cisco_csr1000v": VendorConfig(
        kind="cisco_csr1000v",
        vendor="Cisco",
        console_shell="/bin/sh",  # Fallback, not used with SSH method
        default_image=None,  # Requires user-provided image
        aliases=["csr", "csr1000v"],
        device_type=DeviceType.ROUTER,
        category="Network",
        subcategory="Routers",
        label="Cisco CSR1000v",
        icon="fa-arrows-to-dot",
        versions=[],
        is_active=True,
        notes="CSR1000v requires QEMU/libvirt. User must import image.",
        port_naming="GigabitEthernet",
        port_start_index=1,
        max_ports=8,
        memory=4096,  # 4GB required
        cpu=2,
        requires_image=True,
        supported_image_kinds=["qcow2"],
        documentation_url="https://www.cisco.com/c/en/us/td/docs/routers/csr1000/",
        license_required=True,
        tags=["routing", "bgp", "sd-wan", "ipsec", "cloud", "vm"],
        filename_patterns=[r"csr1000v[_-]?[\d\.]+.*\.qcow2"],
        filename_keywords=["csr"],
        vrnetlab_subdir="cisco/csr",
        readiness_probe="log_pattern",
        readiness_pattern=r"Press RETURN to get started|[\w.-]+[>#]",
        readiness_timeout=300,
        # Config extraction via serial console
        config_extract_method="serial",
        config_extract_command="show running-config",
        config_extract_timeout=60,  # CSR can be slower
        config_extract_prompt_pattern=r"[\w\-]+[>#]\s*$",
        config_extract_paging_disable="terminal length 0",
        # Post-boot commands to run after VM is ready
        post_boot_commands=[
            "terminal length 0",  # Disable paging for CLI sessions
            "no ip domain-lookup",  # Disable DNS lookups that slow down CLI
        ],
    ),
    "juniper_crpd": VendorConfig(
        kind="juniper_crpd",
        vendor="Juniper",
        console_shell="cli",
        default_image="crpd:latest",
        aliases=[],
        device_type=DeviceType.ROUTER,
        category="Network",
        subcategory="Routers",
        label="Juniper cRPD",
        icon="fa-arrows-to-dot",
        versions=[],
        is_active=True,
        notes="cRPD uses standard Junos CLI.",
        port_naming="eth",
        port_start_index=0,
        max_ports=8,
        requires_image=True,
        documentation_url="https://www.juniper.net/documentation/product/us/en/crpd/",
        license_required=True,
        tags=["routing", "bgp", "mpls", "container", "kubernetes"],
        default_credentials="root / (no password)",
    ),
    "juniper_vsrx3": VendorConfig(
        kind="juniper_vsrx3",
        vendor="Juniper",
        console_shell="cli",
        default_image="vrnetlab/vr-vsrx3:latest",
        aliases=[],
        device_type=DeviceType.ROUTER,
        category="Network",
        subcategory="Routers",
        label="Juniper vSRX3",
        icon="fa-arrows-to-dot",
        versions=[],
        is_active=True,
        notes="vSRX3 with standard Junos CLI.",
        port_naming="ge-0/0/",
        port_start_index=0,
        max_ports=8,
        memory=4096,  # 4GB required
        cpu=2,
        requires_image=True,
        supported_image_kinds=["qcow2", "docker"],
        documentation_url="https://www.juniper.net/documentation/product/us/en/vsrx/",
        license_required=True,
        tags=["routing", "firewall", "security", "ipsec", "nat", "vm"],
        filename_patterns=[r"vsrx.*\.qcow2"],
        vrnetlab_subdir="juniper/vsrx",
        readiness_probe="log_pattern",
        readiness_pattern=r"login:",
        readiness_timeout=300,
        default_credentials="root / (no password)",
    ),
    "juniper_vjunosrouter": VendorConfig(
        kind="juniper_vjunosrouter",
        vendor="Juniper",
        console_shell="cli",
        default_image="vrnetlab/vr-vjunosrouter:latest",
        aliases=["vjunos-router", "vjunosrouter", "vjunos_router"],
        device_type=DeviceType.ROUTER,
        category="Network",
        subcategory="Routers",
        label="Juniper vJunos Router",
        icon="fa-arrows-to-dot",
        versions=[],
        is_active=True,
        notes="vJunos Router with standard Junos CLI.",
        port_naming="ge-0/0/",
        port_start_index=0,
        max_ports=12,
        memory=5120,  # 5GB per Juniper's reference XML
        cpu=4,        # 4 vCPU per Juniper's reference XML
        cpu_sockets=0,
        needs_nested_vmx=True,  # vJunos runs nested VM requiring VMX CPU emulation
        requires_image=True,
        supported_image_kinds=["qcow2", "docker"],
        documentation_url="https://www.juniper.net/documentation/product/us/en/vjunos-router/",
        license_required=True,
        tags=["routing", "bgp", "mpls", "evpn", "vm"],
        filename_patterns=[r"vjunos[_-]?router.*\.qcow2"],
        filename_keywords=["vjunos-router", "vjunos_router", "vjunosrouter"],
        vrnetlab_subdir="juniper/vjunos-router",
        readiness_probe="log_pattern",
        readiness_pattern=r"login:",
        readiness_timeout=300,
        default_credentials="root / (no password)",
    ),
    "juniper_vjunosevolved": VendorConfig(
        kind="juniper_vjunosevolved",
        vendor="Juniper",
        console_shell="cli",
        # Uses the official Juniper vJunos Router vrnetlab profile today.
        default_image="vrnetlab/vr-vjunosrouter:latest",
        aliases=["vjunos-evolved", "vjunos_evolved", "vjunosevolved"],
        device_type=DeviceType.ROUTER,
        category="Network",
        subcategory="Routers",
        label="Juniper vJunos Evolved",
        icon="fa-arrows-to-dot",
        versions=[],
        is_active=True,
        notes="vJunos Evolved router with Junos Evolved CLI.",
        port_naming="ge-0/0/",
        port_start_index=0,
        max_ports=12,
        memory=8192,  # 8GB per Juniper's vJunosEvolved reference XML
        cpu=4,        # 4 vCPU per Juniper's reference XML
        cpu_sockets=0,
        needs_nested_vmx=True,  # vJunos runs nested VM requiring VMX CPU emulation
        requires_image=True,
        supported_image_kinds=["qcow2", "docker"],
        documentation_url="https://www.juniper.net/documentation/product/us/en/vjunos-evolved/",
        license_required=True,
        tags=["routing", "bgp", "mpls", "evpn", "vm", "evolved"],
        filename_patterns=[r"vjunos[_-]?evolved.*\.qcow2"],
        filename_keywords=["vjunos-evolved", "vjunos_evolved", "vjunosevolved"],
        vrnetlab_subdir="juniper/vjunos-router",
        readiness_probe="log_pattern",
        readiness_pattern=r"login:",
        readiness_timeout=300,
        default_credentials="root / (no password)",
    ),
    "juniper_cjunos": VendorConfig(
        kind="juniper_cjunos",
        vendor="Juniper",
        console_shell="cli",
        default_image="cjunosevolved:latest",
        aliases=["cjunos", "cjunosevolved", "cjunos-evolved", "cjunos_evolved", "cjunosevolved"],
        device_type=DeviceType.ROUTER,
        category="Network",
        subcategory="Routers",
        label="Juniper cJunos",
        icon="fa-arrows-to-dot",
        versions=[],
        is_active=True,
        notes="Containerized Junos Evolved — runs KVM VM inside Docker. Requires /dev/kvm.",
        port_naming="et-0/0/",
        port_start_index=0,
        max_ports=12,
        memory=8192,
        cpu=4,
        requires_image=True,
        supported_image_kinds=["docker"],
        documentation_url="https://www.juniper.net/documentation/product/us/en/cjunos-evolved/",
        license_required=True,
        tags=["routing", "bgp", "mpls", "evpn", "evolved", "container"],
        readiness_probe="log_pattern",
        readiness_pattern=r"EVO FLAVORS|login:",
        readiness_timeout=600,
        config_extract_method="docker",
        config_extract_command="cli -c 'show configuration | display set'",
        config_extract_timeout=30,
        environment={
            "CPTX_COSIM": "BT|BX",
        },
        capabilities=["NET_ADMIN", "SYS_ADMIN", "NET_RAW"],
        privileged=True,
        binds=[
            "/dev/kvm:/dev/kvm",
            "/dev/net/tun:/dev/net/tun",
        ],
        sysctls={
            "net.ipv4.ip_forward": "1",
            "net.ipv6.conf.all.disable_ipv6": "0",
        },
        default_credentials="root / (no password)",
    ),

    # =========================================================================
    # NETWORK DEVICES - Switches
    # =========================================================================
    # =========================================================================
    # Arista cEOS (containerized EOS)
    # =========================================================================
    # Key behaviors and requirements:
    #
    # 1. PLATFORM DETECTION RACE CONDITION
    #    cEOS uses Ark.getPlatform() during early boot to detect if running as
    #    a container. If this returns None (instead of "ceoslab"), boot fails:
    #    - VEosLabInit skips container-specific init
    #    - EosInitStage tries to load kernel modules (modprobe rbfd) which fails
    #    - Result: Partial boot, no CLI access
    #
    #    Solution: Network interfaces must exist BEFORE /sbin/init runs. We use
    #    an if-wait.sh script (from containerlab) that blocks until interfaces
    #    appear in /sys/class/net/. The CLAB_INTFS env var tells it how many
    #    interfaces to wait for. See docker.py IF_WAIT_SCRIPT and
    #    _create_container_config() for implementation.
    #
    # 2. ENVIRONMENT VARIABLES (all required for proper boot)
    #    - CEOS=1: Identifies as cEOS container
    #    - EOS_PLATFORM=ceoslab: Platform type for Ark.getPlatform()
    #    - INTFTYPE=eth: Linux interface naming (eth1 -> Ethernet1)
    #    - MGMT_INTF=eth0: Management interface name
    #    - SKIP_ZEROTOUCH_BARRIER_IN_SYSDBINIT=1: Skip ZTP barrier
    #    - CEOS_NOZEROTOUCH=1: Disable Zero Touch Provisioning
    #    - CLAB_INTFS=N: Number of interfaces to wait for (set by docker.py)
    #
    # 3. FLASH DIRECTORY (/mnt/flash)
    #    Must be mounted and contain:
    #    - startup-config: Initial configuration (hostname, users, etc.)
    #    - zerotouch-config: File presence disables ZTP
    #    - if-wait.sh: Interface wait script (created by docker.py)
    #
    # 4. KERNEL MODULES
    #    /lib/modules must be mounted read-only so cEOS can load modules.
    #    Without this, modprobe calls fail even when platform is correct.
    #
    # 5. INTERFACE MAPPING
    #    Linux eth1,eth2,... map to EOS Ethernet1,Ethernet2,...
    #    eth0 is reserved for management (Ma0 in EOS).
    #
    # 6. BOOT TIME
    #    cEOS takes 60-120+ seconds to fully boot. Readiness is detected via
    #    log patterns like %SYS-5-CONFIG_I or "System ready".
    # =========================================================================
    "ceos": VendorConfig(
        kind="ceos",
        vendor="Arista",
        console_shell="FastCli",  # FastCli is always available; Cli symlink may not exist
        default_image="ceos:latest",
        # Common IDs seen in imports/manifests/UI; keep these aliases so EOS
        # does not get treated as a separate "custom" device in the UI.
        aliases=["eos", "arista_eos", "arista_ceos"],
        # Note: entrypoint is overridden in docker.py to use if-wait.sh wrapper
        entrypoint="/sbin/init",
        device_type=DeviceType.SWITCH,
        category="Network",
        subcategory="Switches",
        label="Arista cEOS",
        icon="fa-arrows-left-right-to-line",
        versions=[],
        is_active=True,
        notes="cEOS requires 'Cli' command for EOS prompt. User must import image.",
        management_interface="Management0",
        port_naming="Ethernet",
        port_start_index=1,
        max_ports=12,
        memory=2048,  # 2GB recommended minimum
        cpu=2,
        requires_image=True,
        documentation_url="https://www.arista.com/en/support/product-documentation",
        license_required=True,
        tags=["switching", "bgp", "evpn", "vxlan", "datacenter"],
        filename_keywords=["ceos", "eos"],
        # Boot readiness detection via log patterns
        readiness_probe="log_pattern",
        readiness_pattern=r"%SYS-5-CONFIG_I|%SYS-5-SYSTEM_INITIALIZED|%SYS-5-SYSTEM_RESTARTED|%ZTP-6-CANCEL|Startup complete|System ready",
        readiness_timeout=300,  # cEOS can take up to 5 minutes
        # Config extraction via docker exec (FastCli with privilege level 15)
        config_extract_method="docker",
        config_extract_command="FastCli -p 15 -c 'show running-config'",
        config_extract_timeout=30,
        # Container runtime configuration
        environment={
            "CEOS": "1",                                  # Identify as cEOS
            "EOS_PLATFORM": "ceoslab",                    # Platform for Ark.getPlatform()
            "container": "docker",                        # Container runtime type
            "ETBA": "1",                                  # Enable test board agent
            "SKIP_ZEROTOUCH_BARRIER_IN_SYSDBINIT": "1",   # Skip ZTP barrier
            "INTFTYPE": "eth",                            # Linux interface prefix
            "MGMT_INTF": "eth0",                          # Management interface
            "CEOS_NOZEROTOUCH": "1",                      # Disable ZTP
            # Note: CLAB_INTFS is set dynamically in docker.py based on topology
        },
        capabilities=["NET_ADMIN", "SYS_ADMIN", "NET_RAW"],
        privileged=True,
        binds=[
            "{workspace}/configs/{node}/flash:/mnt/flash",              # EOS flash storage
            "{workspace}/configs/{node}/systemd:/etc/systemd/system.conf.d:ro",  # systemd env
            "/lib/modules:/lib/modules:ro",                              # Kernel modules for modprobe
        ],
        sysctls={
            "net.ipv4.ip_forward": "1",
            "net.ipv6.conf.all.disable_ipv6": "0",
            "net.ipv6.conf.all.accept_dad": "0",
            "net.ipv6.conf.default.accept_dad": "0",
            "net.ipv6.conf.all.autoconf": "0",
        },
        # Post-boot commands to fix cEOS iptables rules and errdisable issues
        post_boot_commands=[
            # Remove iptables DROP rules for data interfaces (eth1+)
            # EOS adds these in the EOS_FORWARD chain which block forwarding
            "for i in $(seq 1 12); do iptables -D EOS_FORWARD -i eth$i -j DROP 2>/dev/null || true; done",
            # Disable link-flap errdisable detection.  The carrier propagation
            # mechanism uses `ip link set carrier off/on` which cEOS interprets
            # as link flaps.  When errdisabled, EOS clears IFF_UP which breaks
            # the carrier loop-prevention (host-side veth carrier drops, monitor
            # sees a new transition, propagates back → both sides errdisabled).
            # Note: cEOS 4.35+ uses `no errdisable flap-setting cause link-flap`
            # instead of the older `no errdisable detect cause link-flap`.
            "FastCli -p 15 -c 'configure\nno errdisable flap-setting cause link-flap\nerrdisable recovery cause link-flap\nerrdisable recovery interval 30\nend'",
        ],
        default_credentials="admin / (no password)",
    ),
    "nokia_srlinux": VendorConfig(
        kind="nokia_srlinux",
        vendor="Nokia",
        console_shell="sr_cli",
        default_image="ghcr.io/nokia/srlinux:latest",
        aliases=[],
        device_type=DeviceType.SWITCH,
        category="Network",
        subcategory="Switches",
        label="Nokia SR Linux",
        icon="fa-arrows-left-right-to-line",
        versions=[],
        is_active=True,
        notes="SR Linux uses sr_cli for its native CLI.",
        management_interface="mgmt0",
        port_naming="e1-",
        port_start_index=1,
        max_ports=12,
        memory=4096,  # 4GB recommended
        cpu=2,
        requires_image=False,
        documentation_url="https://documentation.nokia.com/srlinux/",
        tags=["switching", "bgp", "evpn", "datacenter", "gnmi"],
        # Boot readiness: SR Linux typically boots faster than cEOS
        readiness_probe="log_pattern",
        readiness_pattern=r"System is ready|SR Linux.*started|mgmt0.*up",
        readiness_timeout=120,
        # Config extraction via docker exec
        config_extract_method="docker",
        config_extract_command="sr_cli -d 'info flat'",
        config_extract_timeout=30,
        # Container runtime configuration
        environment={
            "SRLINUX": "1",
        },
        capabilities=["NET_ADMIN", "SYS_ADMIN", "NET_RAW"],
        privileged=True,
        sysctls={
            "net.ipv4.ip_forward": "1",
            "net.ipv6.conf.all.disable_ipv6": "0",
            "net.ipv6.conf.all.accept_dad": "0",
            "net.ipv6.conf.default.accept_dad": "0",
        },
        default_credentials="admin / NokiaSrl1!",
    ),
    "cvx": VendorConfig(
        kind="cvx",
        vendor="NVIDIA",
        console_shell="/bin/bash",
        default_image="networkop/cx:5.4.0",
        aliases=[],
        device_type=DeviceType.SWITCH,
        category="Network",
        subcategory="Switches",
        label="NVIDIA Cumulus",
        icon="fa-arrows-left-right-to-line",
        versions=[],
        is_active=True,
        notes="Cumulus VX uses standard Linux bash with NCLU/NVUE.",
        port_naming="swp",
        port_start_index=1,
        max_ports=12,
        requires_image=False,
        documentation_url="https://docs.nvidia.com/networking-ethernet-software/cumulus-linux/",
        tags=["switching", "linux", "bgp", "evpn", "datacenter"],
    ),
    "sonic-vs": VendorConfig(
        kind="sonic-vs",
        vendor="SONiC",
        console_shell="vtysh",
        default_image="docker-sonic-vs:latest",
        aliases=[],
        device_type=DeviceType.SWITCH,
        category="Network",
        subcategory="Switches",
        label="SONiC",
        icon="fa-arrows-left-right-to-line",
        versions=[],
        is_active=True,
        notes="SONiC uses FRR's vtysh for routing configuration.",
        port_naming="Ethernet",
        port_start_index=0,
        max_ports=12,
        requires_image=True,
        documentation_url="https://github.com/sonic-net/SONiC/wiki",
        tags=["switching", "linux", "bgp", "datacenter", "open-source"],
    ),
    "juniper_vjunosswitch": VendorConfig(
        kind="juniper_vjunosswitch",
        vendor="Juniper",
        console_shell="cli",
        default_image="vrnetlab/vr-vjunosswitch:latest",
        aliases=["vjunos-switch", "vjunosswitch", "vjunos"],
        device_type=DeviceType.SWITCH,
        category="Network",
        subcategory="Switches",
        label="Juniper vJunos Switch",
        icon="fa-arrows-left-right-to-line",
        versions=[],
        is_active=True,
        notes="vJunos Switch with standard Junos CLI.",
        port_naming="ge-0/0/",
        port_start_index=0,
        max_ports=12,
        memory=5120,  # 5GB per Juniper's reference XML
        cpu=4,        # 4 vCPU per Juniper's reference XML
        cpu_sockets=0,
        needs_nested_vmx=True,  # vJunos runs nested VM requiring VMX CPU emulation
        requires_image=True,
        supported_image_kinds=["qcow2", "docker"],
        documentation_url="https://www.juniper.net/documentation/product/us/en/vjunos-switch/",
        license_required=True,
        tags=["switching", "evpn", "vxlan", "datacenter", "vm"],
        filename_patterns=[r"vjunos[_-]?switch.*\.qcow2", r"vjunos.*\.qcow2"],
        filename_keywords=["vjunos-switch", "vjunos_switch", "vjunosswitch"],
        vrnetlab_subdir="juniper/vjunos-switch",
        readiness_probe="log_pattern",
        readiness_pattern=r"login:",
        readiness_timeout=300,
        default_credentials="root / (no password)",
    ),
    "cisco_n9kv": VendorConfig(
        kind="cisco_n9kv",
        vendor="Cisco",
        console_shell="/bin/bash",
        default_image="vrnetlab/vr-n9kv:latest",
        aliases=["nxos", "nxosv9000", "n9kv"],
        device_type=DeviceType.SWITCH,
        category="Network",
        subcategory="Switches",
        label="Cisco NX-OSv",
        icon="fa-arrows-left-right-to-line",
        versions=[],
        is_active=False,  # Requires specific setup
        notes="Nexus 9000v requires QEMU/libvirt. High resource requirements.",
        port_naming="Ethernet1/",
        port_start_index=1,
        max_ports=12,
        management_interface="mgmt0",
        memory=12288,  # 12GB required (CML minimum 10GB)
        cpu=2,
        nic_driver="e1000",  # NX-OS lacks virtio drivers; e1000 required
        disk_driver="sata",  # NX-OS requires AHCI/SATA to detect bootflash; IDE boots kernel but no bootflash
        machine_type="pc-i440fx-6.2",  # e1000 TX hangs on Q35; i440fx works with explicit AHCI controller
        efi_boot=True,  # N9Kv image uses UEFI; legacy BIOS drops to boot manager
        efi_vars="stateless",  # N9Kv uses stateless NVRAM per CML spec
        serial_port_count=2,  # CML spec: console + aux; N9Kv sysconf expects 2 serial ports
        requires_image=True,
        supported_image_kinds=["qcow2"],
        documentation_url="https://www.cisco.com/c/en/us/td/docs/switches/datacenter/nexus9000/",
        license_required=True,
        tags=["switching", "vxlan", "evpn", "datacenter", "aci", "vm"],
        filename_patterns=[r"n9kv[_-]?[\d\.]+.*\.qcow2", r"nexus9[_-]?[\d\.]+.*\.qcow2", r"nxosv[_-]?[\d\.]+.*\.qcow2"],
        filename_keywords=["nxos"],
        vrnetlab_subdir="cisco/n9kv",
        # Detect login prompt via serial console log to defer post-boot commands
        # until NX-OS is actually ready (boot takes 5-10 min).
        readiness_probe="log_pattern",
        readiness_pattern=r"login:|User Access Verification",
        readiness_timeout=600,  # N9Kv takes a long time to boot
        # Serial (virsh) console for NX-OS CLI access.
        # SSH hits the Wind Linux underlay (bash), not NX-OS.
        console_method="virsh",
        console_user="admin",
        console_password="admin",
        # Config extraction also via serial console
        config_extract_method="serial",
        config_extract_command="show running-config",
        config_extract_user="admin",
        config_extract_password="admin",
        config_extract_timeout=60,
        config_extract_paging_disable="terminal length 0",
        # Post-boot recovery for this environment:
        # import staged bootflash config into running, persist to startup,
        # and ensure POAP stays disabled on this VM state.
        post_boot_commands=[
            "configure terminal ; system no poap ; end",
            "copy bootflash:startup-config running-config",
            "copy running-config startup-config",
            # Compatibility fallback path for images that do not accept the
            # direct bootflash->running import in one command.
            "copy bootflash:startup-config startup-config",
            "copy startup-config running-config",
            "copy running-config startup-config",
        ],
        # Config injection: write startup-config to bootflash partition before boot
        config_inject_method="bootflash",
        config_inject_partition=0,  # auto-detect via blkid
        config_inject_fs_type="ext2",
        config_inject_path="/startup-config",
        default_credentials="admin / (no password)",
    ),

    # =========================================================================
    # NETWORK DEVICES - Load Balancers
    # =========================================================================
    "f5_bigip": VendorConfig(
        kind="f5_bigip",
        vendor="F5",
        console_shell="/bin/bash",
        default_image=None,  # Requires user-provided image
        aliases=[],
        device_type=DeviceType.SWITCH,
        category="Network",
        subcategory="Load Balancers",
        label="F5 BIG-IP VE",
        icon="fa-server",
        versions=[],
        is_active=True,
        notes="F5 BIG-IP requires licensed image. User must import.",
        port_naming="1.",
        port_start_index=1,
        max_ports=8,
        memory=4096,  # 4GB minimum
        cpu=2,
        requires_image=True,
        supported_image_kinds=["qcow2"],
        documentation_url="https://clouddocs.f5.com/",
        license_required=True,
        tags=["load-balancer", "waf", "ssl", "adc", "vm"],
        readiness_probe="log_pattern",
        readiness_pattern=r"login:",
        readiness_timeout=300,
    ),
    "haproxy": VendorConfig(
        kind="linux",
        vendor="Open Source",
        console_shell="/bin/sh",
        default_image="haproxy:latest",
        aliases=[],
        device_type=DeviceType.CONTAINER,
        category="Network",
        subcategory="Load Balancers",
        label="HAProxy",
        icon="fa-box",
        versions=[],
        is_active=True,
        notes="HAProxy load balancer container.",
        port_naming="eth",
        port_start_index=0,
        max_ports=8,
        requires_image=False,
        documentation_url="https://www.haproxy.org/#docs",
        tags=["load-balancer", "proxy", "open-source"],
        filename_keywords=["haproxy"],
    ),
    "citrix_adc": VendorConfig(
        kind="citrix_adc",
        vendor="Citrix",
        console_shell="/bin/bash",
        default_image=None,
        aliases=[],
        device_type=DeviceType.SWITCH,
        category="Network",
        subcategory="Load Balancers",
        label="Citrix ADC",
        icon="fa-server",
        versions=[],
        is_active=False,
        notes="Citrix ADC requires licensed image.",
        port_naming="0/",
        port_start_index=1,
        max_ports=8,
        memory=2048,  # 2GB minimum
        cpu=2,
        requires_image=True,
        supported_image_kinds=["qcow2"],
        documentation_url="https://docs.citrix.com/en-us/citrix-adc",
        license_required=True,
        tags=["load-balancer", "adc", "ssl", "vm"],
        readiness_probe="log_pattern",
        readiness_pattern=r"Done",
        readiness_timeout=180,
    ),

    # =========================================================================
    # SECURITY DEVICES
    # =========================================================================
    "cisco_asav": VendorConfig(
        kind="cisco_asav",
        vendor="Cisco",
        console_shell="/bin/sh",
        default_image=None,  # Requires user-provided image
        aliases=[],
        device_type=DeviceType.FIREWALL,
        category="Security",
        subcategory=None,
        label="Cisco ASAv",
        icon="fa-shield-halved",
        versions=[],
        is_active=True,
        notes="ASAv requires QEMU/libvirt. User must import image.",
        port_naming="GigabitEthernet0/",
        port_start_index=0,
        max_ports=10,
        memory=2048,  # 2GB required
        cpu=1,
        nic_driver="e1000",  # ASAv works better with e1000
        machine_type="pc-i440fx-6.2",  # e1000 TX hangs on Q35; i440fx is reliable
        requires_image=True,
        supported_image_kinds=["qcow2"],
        documentation_url="https://www.cisco.com/c/en/us/td/docs/security/asa/",
        license_required=True,
        tags=["firewall", "vpn", "ipsec", "nat", "security", "vm"],
        filename_patterns=[r"asav[_-]?[\d\.]+.*\.qcow2"],
        vrnetlab_subdir="cisco/asav",
        readiness_probe="log_pattern",
        readiness_pattern=r"ciscoasa>|ciscoasa#",
        readiness_timeout=180,
        # Config extraction via serial console
        config_extract_method="serial",
        config_extract_command="show running-config",
        config_extract_timeout=30,
        config_extract_prompt_pattern=r"ciscoasa[>#]\s*$",
        config_extract_paging_disable="terminal pager 0",
        # Post-boot commands to run after VM is ready
        post_boot_commands=[
            "terminal pager 0",  # Disable paging for CLI sessions
        ],
    ),
    "fortinet_fortigate": VendorConfig(
        kind="fortinet_fortigate",
        vendor="Fortinet",
        console_shell="/bin/sh",
        default_image=None,
        aliases=[],
        device_type=DeviceType.FIREWALL,
        category="Security",
        subcategory=None,
        label="FortiGate VM",
        icon="fa-user-shield",
        versions=[],
        is_active=False,
        notes="FortiGate requires licensed image.",
        port_naming="port",
        port_start_index=1,
        max_ports=10,
        memory=2048,  # 2GB minimum
        cpu=1,
        requires_image=True,
        supported_image_kinds=["qcow2"],
        documentation_url="https://docs.fortinet.com/product/fortigate/",
        license_required=True,
        tags=["firewall", "utm", "vpn", "security", "sd-wan", "vm"],
        readiness_probe="log_pattern",
        readiness_pattern=r"login:",
        readiness_timeout=180,
    ),
    "paloalto_vmseries": VendorConfig(
        kind="paloalto_vmseries",
        vendor="Palo Alto",
        console_shell="/bin/sh",
        default_image=None,
        aliases=[],
        device_type=DeviceType.FIREWALL,
        category="Security",
        subcategory=None,
        label="Palo Alto VM-Series",
        icon="fa-lock",
        versions=[],
        is_active=False,
        notes="VM-Series requires licensed image.",
        port_naming="ethernet1/",
        port_start_index=1,
        max_ports=10,
        memory=6144,  # 6GB minimum
        cpu=2,
        requires_image=True,
        supported_image_kinds=["qcow2"],
        documentation_url="https://docs.paloaltonetworks.com/vm-series",
        license_required=True,
        tags=["firewall", "ngfw", "security", "threat-prevention", "vm"],
        filename_patterns=[r"pa[_-]?vm[_-]?[\d\.]+.*\.qcow2"],
        vrnetlab_subdir="paloalto/panos",
        readiness_probe="log_pattern",
        readiness_pattern=r"login:",
        readiness_timeout=600,  # PA VMs take a long time to boot
    ),

    # =========================================================================
    # COMPUTE
    # =========================================================================
    "linux": VendorConfig(
        kind="linux",
        vendor="Open Source",
        console_shell="/bin/sh",
        default_image="alpine:latest",
        aliases=[],
        device_type=DeviceType.HOST,
        category="Compute",
        subcategory=None,
        label="Linux Server",
        icon="fa-terminal",
        versions=[],
        is_active=True,
        notes="Generic Linux container. Uses /bin/sh for broad compatibility.",
        port_naming="eth",
        port_start_index=0,
        max_ports=8,
        requires_image=False,
        documentation_url="https://docs.docker.com/",
        tags=["host", "linux", "container", "testing"],
        # Container runtime configuration
        capabilities=["NET_ADMIN"],
        privileged=False,
        cmd=["sleep", "infinity"],  # Keep container running
    ),
    "alpine": VendorConfig(
        kind="alpine",
        vendor="Open Source",
        console_shell="/bin/sh",
        default_image=None,
        aliases=[],
        device_type=DeviceType.HOST,
        category="Compute",
        subcategory=None,
        label="Alpine Linux",
        icon="fa-leaf",
        versions=[],
        is_active=True,
        notes="Dedicated Alpine Linux node type. Assign Docker or qcow2 images explicitly.",
        port_naming="eth",
        port_start_index=0,
        max_ports=8,
        memory=512,
        cpu=1,
        requires_image=True,
        supported_image_kinds=["docker", "qcow2"],
        documentation_url="https://www.alpinelinux.org/",
        tags=["host", "linux", "alpine", "lightweight", "open-source"],
        filename_keywords=["alpine"],
        readiness_probe="log_pattern",
        readiness_pattern=r"Welcome to Alpine Linux",
        readiness_timeout=120,
    ),
    "tcl": VendorConfig(
        kind="tcl",
        vendor="Open Source",
        console_shell="/bin/sh",
        default_image=None,
        aliases=[],
        device_type=DeviceType.HOST,
        category="Compute",
        subcategory=None,
        label="Tiny Core Linux",
        icon="fa-cube",
        versions=[],
        is_active=True,
        notes="Tiny Core Linux (TCL) VM node type for lightweight service hosts.",
        port_naming="eth",
        port_start_index=0,
        max_ports=4,
        memory=256,
        cpu=1,
        requires_image=True,
        supported_image_kinds=["qcow2"],
        documentation_url="http://www.tinycorelinux.net/",
        tags=["host", "linux", "tinycore", "tcl", "lightweight", "open-source"],
        filename_keywords=["tcl"],
        readiness_probe="log_pattern",
        readiness_pattern=r"###### BOOT CONFIG DONE ######",
        readiness_timeout=60,
    ),
    "frr": VendorConfig(
        kind="linux",
        vendor="Open Source",
        console_shell="vtysh",
        default_image="quay.io/frrouting/frr:latest",
        aliases=[],
        device_type=DeviceType.CONTAINER,
        category="Compute",
        subcategory=None,
        label="FRR Container",
        icon="fa-box-open",
        versions=[],
        is_active=True,
        notes="FRR uses vtysh for routing configuration.",
        port_naming="eth",
        port_start_index=0,
        max_ports=8,
        requires_image=False,
        documentation_url="https://docs.frrouting.org/",
        tags=["routing", "bgp", "ospf", "open-source", "container"],
        filename_keywords=["frr"],
        # Config extraction via docker exec
        config_extract_method="docker",
        config_extract_command="vtysh -c 'show running-config'",
        config_extract_timeout=15,
        # Container runtime configuration
        capabilities=["NET_ADMIN", "SYS_ADMIN"],
        privileged=True,
        sysctls={
            "net.ipv4.ip_forward": "1",
            "net.ipv6.conf.all.forwarding": "1",
        },
    ),
    "windows": VendorConfig(
        kind="windows",
        vendor="Microsoft",
        console_shell="/bin/sh",
        default_image=None,
        aliases=[],
        device_type=DeviceType.HOST,
        category="Compute",
        subcategory=None,
        label="Windows Server",
        icon="fa-window-maximize",
        versions=[],
        is_active=False,
        notes="Windows requires special QEMU/KVM setup.",
        port_naming="Ethernet",
        port_start_index=0,
        max_ports=4,
        memory=4096,  # 4GB minimum
        cpu=2,
        requires_image=True,
        supported_image_kinds=["qcow2"],
        documentation_url="https://docs.microsoft.com/en-us/windows-server/",
        license_required=True,
        tags=["host", "windows", "server", "vm"],
        readiness_probe="none",  # Windows doesn't have serial console readiness
        readiness_timeout=600,
    ),

    # =========================================================================
    # CISCO SD-WAN (VM-based, requires libvirt provider)
    # =========================================================================
    "c8000v": VendorConfig(
        kind="cisco_c8000v",
        vendor="Cisco",
        console_shell="/bin/sh",  # Fallback, not used with SSH method
        default_image=None,
        aliases=["cat8000v", "cat-sdwan-edge"],
        device_type=DeviceType.ROUTER,
        category="Network",
        subcategory="Routers",
        label="Catalyst SD-WAN Edge",
        icon="fa-arrows-to-dot",
        versions=[],
        is_active=True,
        notes="Cisco Catalyst 8000v SD-WAN Edge. Requires QEMU/libvirt.",
        port_naming="GigabitEthernet",
        port_start_index=1,
        max_ports=12,
        memory=5120,  # 5GB required
        cpu=2,
        requires_image=True,
        supported_image_kinds=["qcow2"],
        documentation_url="https://www.cisco.com/c/en/us/td/docs/routers/sdwan/",
        license_required=True,
        tags=["sd-wan", "routing", "vpn", "ipsec", "vm"],
        filename_patterns=[r"c8000v[_-]?[\d\.]+.*\.qcow2", r"cat8000v[_-]?[\d\.]+.*\.qcow2"],
        vrnetlab_subdir="cisco/c8000v",
        readiness_probe="log_pattern",
        readiness_pattern=r"Press RETURN to get started!",
        readiness_timeout=250,
        # Config extraction via serial console
        config_extract_method="serial",
        config_extract_command="show running-config",
        config_extract_timeout=60,
        config_extract_prompt_pattern=r"[\w\-]+[>#]\s*$",
        config_extract_paging_disable="terminal length 0",
        # Post-boot commands to run after VM is ready
        post_boot_commands=[
            "terminal length 0",  # Disable paging for CLI sessions
            "no ip domain-lookup",  # Disable DNS lookups that slow down CLI
        ],
    ),
    "cat-sdwan-controller": VendorConfig(
        kind="cat-sdwan-controller",
        vendor="Cisco",
        console_shell="/bin/sh",
        default_image=None,
        aliases=[],
        device_type=DeviceType.ROUTER,
        category="Network",
        subcategory="Routers",
        label="SD-WAN Controller",
        icon="fa-server",
        versions=[],
        is_active=True,
        notes="Cisco SD-WAN Controller (vSmart). Requires QEMU/libvirt.",
        port_naming="eth",
        port_start_index=0,
        max_ports=4,
        memory=4096,  # CML refplat spec: 4GB minimum
        cpu=2,
        requires_image=True,
        supported_image_kinds=["qcow2"],
        documentation_url="https://www.cisco.com/c/en/us/td/docs/routers/sdwan/",
        license_required=True,
        tags=["sd-wan", "controller", "vm"],
        filename_patterns=[r"viptela[_-]?smart.*\.qcow2"],
        vrnetlab_subdir="cisco/sdwan",
        readiness_probe="log_pattern",
        readiness_pattern=r"login:",
        readiness_timeout=180,
    ),
    "cat-sdwan-manager": VendorConfig(
        kind="cat-sdwan-manager",
        vendor="Cisco",
        console_shell="/bin/sh",
        default_image=None,
        aliases=[],
        device_type=DeviceType.ROUTER,
        category="Network",
        subcategory="Routers",
        label="SD-WAN Manager",
        icon="fa-server",
        versions=[],
        is_active=True,
        notes="Cisco SD-WAN Manager (vManage). Requires QEMU/libvirt. 256GB data volume.",
        port_naming="eth",
        port_start_index=0,
        max_ports=4,
        memory=32768,  # 32GB recommended
        cpu=8,  # CML refplat spec: 8 vCPUs
        data_volume_gb=256,  # CML refplat spec: 256GB data volume
        requires_image=True,
        supported_image_kinds=["qcow2"],
        documentation_url="https://www.cisco.com/c/en/us/td/docs/routers/sdwan/",
        license_required=True,
        tags=["sd-wan", "manager", "nms", "vm"],
        filename_patterns=[r"viptela[_-]?vmanage.*\.qcow2"],
        vrnetlab_subdir="cisco/sdwan",
        readiness_probe="log_pattern",
        readiness_pattern=r"login:",
        readiness_timeout=600,  # vManage takes longer to boot
    ),
    "cat-sdwan-validator": VendorConfig(
        kind="cat-sdwan-validator",
        vendor="Cisco",
        console_shell="/bin/sh",
        default_image=None,
        aliases=[],
        device_type=DeviceType.ROUTER,
        category="Network",
        subcategory="Routers",
        label="SD-WAN Validator",
        icon="fa-server",
        versions=[],
        is_active=True,
        notes="Cisco SD-WAN Validator (vBond). Requires QEMU/libvirt.",
        port_naming="eth",
        port_start_index=0,
        max_ports=4,
        memory=4096,  # CML refplat spec: 4GB minimum
        cpu=2,
        requires_image=True,
        supported_image_kinds=["qcow2"],
        documentation_url="https://www.cisco.com/c/en/us/td/docs/routers/sdwan/",
        license_required=True,
        tags=["sd-wan", "validator", "vm"],
        filename_patterns=[r"viptela[_-]?bond.*\.qcow2"],
        vrnetlab_subdir="cisco/sdwan",
        readiness_probe="log_pattern",
        readiness_pattern=r"login:",
        readiness_timeout=180,
    ),
    "cat-sdwan-vedge": VendorConfig(
        kind="cat-sdwan-vedge",
        vendor="Cisco",
        console_shell="/bin/sh",
        default_image=None,
        aliases=[],
        device_type=DeviceType.ROUTER,
        category="Network",
        subcategory="Routers",
        label="SD-WAN vEdge",
        icon="fa-arrows-to-dot",
        versions=[],
        is_active=True,
        notes="Cisco SD-WAN vEdge (legacy). Requires QEMU/libvirt.",
        port_naming="ge0/",
        port_start_index=0,
        max_ports=8,
        memory=2048,
        cpu=2,
        requires_image=True,
        supported_image_kinds=["qcow2"],
        documentation_url="https://www.cisco.com/c/en/us/td/docs/routers/sdwan/",
        license_required=True,
        tags=["sd-wan", "vedge", "vm"],
        filename_patterns=[r"viptela[_-]?edge.*\.qcow2", r"vedge[_-]?[\d\.]+.*\.qcow2"],
        vrnetlab_subdir="cisco/sdwan",
        readiness_probe="log_pattern",
        readiness_pattern=r"login:",
        readiness_timeout=180,
    ),

    # =========================================================================
    # CISCO SECURITY (VM-based)
    # =========================================================================
    "ftdv": VendorConfig(
        kind="cisco_ftdv",
        vendor="Cisco",
        console_shell="/bin/sh",
        default_image=None,
        aliases=[],
        device_type=DeviceType.FIREWALL,
        category="Security",
        subcategory=None,
        label="Firepower Threat Defense",
        icon="fa-shield-halved",
        versions=[],
        is_active=True,
        notes="Cisco Firepower Threat Defense Virtual. Requires QEMU/libvirt.",
        port_naming="GigabitEthernet0/",
        port_start_index=0,
        max_ports=10,
        management_interface="Management0/0",
        memory=8192,  # 8GB required
        cpu=4,
        requires_image=True,
        supported_image_kinds=["qcow2"],
        documentation_url="https://www.cisco.com/c/en/us/td/docs/security/firepower/quick_start/kvm/ftdv-kvm-gsg.html",
        license_required=True,
        tags=["firewall", "ngfw", "security", "threat-defense", "vm"],
        filename_patterns=[r"ftdv[_-]?[\d\.]+.*\.qcow2", r"cisco[_-]?secure[_-]?firewall[_-]?threat[_-]?defense.*\.qcow2"],
        vrnetlab_subdir="cisco/ftdv",
        readiness_probe="log_pattern",
        readiness_pattern=r"login:",
        readiness_timeout=300,
        console_method="ssh",
        console_user="admin",
        console_password="admin",
    ),
    "fmcv": VendorConfig(
        kind="fmcv",
        vendor="Cisco",
        console_shell="/bin/sh",
        default_image=None,
        aliases=[],
        device_type=DeviceType.FIREWALL,
        category="Security",
        subcategory=None,
        label="Firepower Management Center",
        icon="fa-server",
        versions=[],
        is_active=True,
        notes="Cisco Firepower Management Center Virtual. Requires QEMU/libvirt. 250GB data volume.",
        port_naming="eth",
        port_start_index=0,
        max_ports=2,
        memory=32768,  # 32GB required
        cpu=4,  # CML refplat spec: 4 vCPUs
        data_volume_gb=256,  # CML refplat spec: 256GB data volume
        requires_image=True,
        supported_image_kinds=["qcow2"],
        documentation_url="https://www.cisco.com/c/en/us/td/docs/security/firepower/quick_start/kvm/fmcv-kvm-gsg.html",
        license_required=True,
        tags=["firewall", "management", "security", "vm"],
        readiness_probe="log_pattern",
        readiness_pattern=r"login:",
        readiness_timeout=600,  # FMC takes longer to boot
    ),

    # =========================================================================
    # CISCO WIRELESS (VM-based)
    # =========================================================================
    "cat9800": VendorConfig(
        kind="cisco_cat9800",
        vendor="Cisco",
        console_shell="/bin/sh",
        default_image=None,
        aliases=["cat9kv", "cisco_cat9kv"],
        platform="cisco_cat9kv",
        device_type=DeviceType.ROUTER,
        category="Network",
        subcategory="Wireless",
        label="Catalyst 9800 WLC",
        icon="fa-wifi",
        versions=[],
        is_active=True,
        notes="Cisco Catalyst 9800-CL Wireless LAN Controller. Requires QEMU/libvirt.",
        port_naming="GigabitEthernet",
        port_start_index=1,
        max_ports=4,
        # Cat9kv images (including cat9000v q200/uadp variants) are sensitive
        # to VM chipset and bus/NIC model; i440fx/IDE/e1000 is more reliable.
        memory=18432,  # 18GB for stable boot on newer Cat9kv images
        cpu=4,
        machine_type="pc-i440fx-6.2",
        disk_driver="ide",
        nic_driver="e1000",
        requires_image=True,
        supported_image_kinds=["qcow2"],
        documentation_url="https://www.cisco.com/c/en/us/td/docs/wireless/controller/9800/",
        license_required=True,
        tags=["wireless", "wlc", "wifi", "ap", "vm"],
        readiness_probe="log_pattern",
        readiness_pattern=r"Press RETURN to get started!",
        readiness_timeout=300,
    ),
    "cat9000v-q200": VendorConfig(
        kind="cisco_cat9000v_q200",
        vendor="Cisco",
        console_shell="/bin/sh",
        default_image=None,
        aliases=["cat9000v_q200"],
        platform="cisco_cat9kv",
        device_type=DeviceType.SWITCH,
        category="Network",
        subcategory="Switches",
        label="BETA CAT9000v Q200",
        icon="fa-arrows-left-right-to-line",
        versions=[],
        is_active=True,
        notes="Catalyst 9000v Q200 data plane virtual switch. Requires QEMU/libvirt.",
        port_naming="GigabitEthernet1/0/{index}",
        port_start_index=1,
        max_ports=24,
        management_interface="GigabitEthernet0/0",
        memory=18432,
        cpu=4,
        machine_type="pc-i440fx-6.2",
        disk_driver="ide",
        nic_driver="e1000",
        requires_image=True,
        supported_image_kinds=["qcow2"],
        documentation_url="https://www.cisco.com/c/en/us/support/switches/catalyst-9300-series-switches/series.html",
        license_required=True,
        tags=["switch", "cat9k", "cat9000v", "q200", "vm"],
        readiness_probe="log_pattern",
        readiness_pattern=r"Press RETURN to get started!",
        readiness_timeout=600,
        console_method="ssh",
        console_user="admin",
        console_password="admin",
        # Config extraction via serial console (IOS-XE)
        config_extract_method="serial",
        config_extract_command="show running-config",
        config_extract_user="admin",
        config_extract_password="admin",
        config_extract_timeout=60,
        config_extract_paging_disable="terminal length 0",
    ),
    "cat9000v-uadp": VendorConfig(
        kind="cisco_cat9000v_uadp",
        vendor="Cisco",
        console_shell="/bin/sh",
        default_image=None,
        aliases=["cat9000v_uadp"],
        platform="cisco_cat9kv",
        device_type=DeviceType.SWITCH,
        category="Network",
        subcategory="Switches",
        label="BETA CAT9000v UADP",
        icon="fa-arrows-left-right-to-line",
        versions=[],
        is_active=True,
        notes="Catalyst 9000v UADP data plane virtual switch. Requires QEMU/libvirt.",
        port_naming="GigabitEthernet1/0/{index}",
        port_start_index=1,
        max_ports=24,
        management_interface="GigabitEthernet0/0",
        memory=18432,
        cpu=4,
        machine_type="pc-i440fx-6.2",
        disk_driver="ide",
        nic_driver="e1000",
        requires_image=True,
        supported_image_kinds=["qcow2"],
        documentation_url="https://www.cisco.com/c/en/us/support/switches/catalyst-9300-series-switches/series.html",
        license_required=True,
        tags=["switch", "cat9k", "cat9000v", "uadp", "vm"],
        readiness_probe="log_pattern",
        readiness_pattern=r"Press RETURN to get started!",
        readiness_timeout=600,
        console_method="ssh",
        console_user="admin",
        console_password="admin",
        # Config extraction via serial console (IOS-XE)
        config_extract_method="serial",
        config_extract_command="show running-config",
        config_extract_user="admin",
        config_extract_password="admin",
        config_extract_timeout=60,
        config_extract_paging_disable="terminal length 0",
    ),
}


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

# Build alias lookup table at module load time
_ALIAS_TO_KIND: dict[str, str] = {}
for _key, config in VENDOR_CONFIGS.items():
    # The vendor config key maps to kind (e.g., "cisco_iosv" -> "linux")
    _ALIAS_TO_KIND[_key.lower()] = config.kind
    # The kind itself is a valid lookup
    _ALIAS_TO_KIND[config.kind] = config.kind
    # All aliases map to this kind
    for alias in config.aliases:
        _ALIAS_TO_KIND[alias.lower()] = config.kind

# Build kind-to-config lookup table (maps device kind -> VendorConfig)
_KIND_TO_CONFIG: dict[str, VendorConfig] = {}
for _key, config in VENDOR_CONFIGS.items():
    # Map the device kind to this config
    _KIND_TO_CONFIG[config.kind] = config
    # Also map the config key itself
    _KIND_TO_CONFIG[_key] = config


# =============================================================================
# DERIVED MAPS (built from VENDOR_CONFIGS — single source of truth)
# =============================================================================

def build_device_id_aliases() -> dict[str, str]:
    """Build a mapping from all known identifiers to their canonical VENDOR_CONFIGS key.

    Merges keys, kinds, and aliases into a single lookup table.
    Priority: VENDOR_CONFIGS keys > explicit aliases > kind mappings.
    """
    aliases: dict[str, str] = {}

    # Pass 1: kind mappings (lowest priority — can be overwritten).
    for key, cfg in VENDOR_CONFIGS.items():
        if cfg.kind:
            aliases[cfg.kind.lower()] = key

    # Pass 2: explicit aliases (medium priority).
    for key, cfg in VENDOR_CONFIGS.items():
        for alias in (cfg.aliases or []):
            aliases[alias.lower()] = key

    # Pass 3: VENDOR_CONFIGS keys (highest priority — never overwritten).
    for key in VENDOR_CONFIGS:
        aliases[key.lower()] = key

    return aliases


def build_device_vendor_map() -> dict[str, str]:
    """Build a mapping from device identifiers to vendor names.

    Maps keys, kinds, and aliases to their vendor string.
    """
    vendor_map: dict[str, str] = {}
    for key, cfg in VENDOR_CONFIGS.items():
        vendor_map[key.lower()] = cfg.vendor
        if cfg.kind:
            vendor_map[cfg.kind.lower()] = cfg.vendor
        for alias in (cfg.aliases or []):
            vendor_map[alias.lower()] = cfg.vendor
    return vendor_map


def build_filename_keyword_map() -> dict[str, str]:
    """Build a mapping from filename keywords to VENDOR_CONFIGS keys.

    Used for Docker tar filename detection. Keywords are ordered with
    longer/more-specific first to avoid ambiguous matches.
    """
    keyword_map: dict[str, str] = {}
    pairs: list[tuple[str, str]] = []
    for key, cfg in VENDOR_CONFIGS.items():
        for kw in (cfg.filename_keywords or []):
            pairs.append((kw.lower(), key))
    # Sort by keyword length descending so specific matches win over substrings.
    pairs.sort(key=lambda p: len(p[0]), reverse=True)
    for kw, key in pairs:
        keyword_map[kw] = key
    return keyword_map


def build_qcow2_device_patterns() -> dict[str, tuple[str, str]]:
    """Build a mapping from filename regex patterns to (device_key, vrnetlab_subdir).

    Used for qcow2 filename detection and vrnetlab build path resolution.
    """
    patterns: dict[str, tuple[str, str]] = {}
    for key, cfg in VENDOR_CONFIGS.items():
        if not cfg.filename_patterns or not cfg.vrnetlab_subdir:
            continue
        for pattern in cfg.filename_patterns:
            patterns[pattern] = (key, cfg.vrnetlab_subdir)
    return patterns


# Pre-built derived maps (module-level for performance)
_DERIVED_DEVICE_ID_ALIASES = build_device_id_aliases()
_DERIVED_DEVICE_VENDOR_MAP = build_device_vendor_map()
_DERIVED_FILENAME_KEYWORD_MAP = build_filename_keyword_map()
_DERIVED_QCOW2_DEVICE_PATTERNS = build_qcow2_device_patterns()


def _get_config_by_kind(kind: str) -> VendorConfig | None:
    """Look up VendorConfig by device kind.

    This handles the mapping from device kinds (e.g., 'cisco_c8000v')
    to VendorConfig entries (keyed by 'c8000v').
    """
    return _KIND_TO_CONFIG.get(kind)


def get_console_shell(kind: str) -> str:
    """Get the console shell command for a device kind.

    Args:
        kind: The device kind (from archetype.node_kind label)

    Returns:
        Shell command to use for console access
    """
    config = _get_config_by_kind(kind)
    if not config:
        # Try alias lookup (e.g., "eos" -> "ceos")
        canonical_kind = get_kind_for_device(kind)
        config = _get_config_by_kind(canonical_kind)
    if config:
        return config.console_shell
    return "/bin/sh"  # Safe default


def get_console_method(kind: str) -> str:
    """Get the console access method for a device kind.

    Args:
        kind: The device kind (from archetype.node_kind label)

    Returns:
        Console method: "docker_exec" or "ssh"
    """
    config = _get_config_by_kind(kind)
    if not config:
        # Try alias lookup (e.g., "eos" -> "ceos")
        canonical_kind = get_kind_for_device(kind)
        config = _get_config_by_kind(canonical_kind)
    if config:
        return config.console_method
    return "docker_exec"  # Default


def get_console_credentials(kind: str) -> tuple[str, str]:
    """Get the console credentials for SSH-based console access.

    Args:
        kind: The device kind (from archetype.node_kind label)

    Returns:
        Tuple of (username, password)
    """
    config = _get_config_by_kind(kind)
    if config:
        return (config.console_user, config.console_password)
    return ("admin", "admin")  # Default


def get_default_image(kind: str) -> Optional[str]:
    """Get the default Docker image for a device kind."""
    config = _get_config_by_kind(kind)
    if config:
        return config.default_image
    return None


def get_vendor_config(kind: str) -> Optional[VendorConfig]:
    """Get the full vendor configuration for a device kind."""
    return VENDOR_CONFIGS.get(kind)


def list_supported_kinds() -> list[str]:
    """List all supported device kinds."""
    return list(VENDOR_CONFIGS.keys())


def get_kind_for_device(device: str) -> str:
    """Resolve a device alias to its canonical kind.

    Args:
        device: Device name or alias (e.g., "eos", "arista_eos", "ceos")

    Returns:
        The canonical device kind (e.g., "ceos")
    """
    device_lower = device.lower()
    return _ALIAS_TO_KIND.get(device_lower, device_lower)


def is_ceos_kind(kind: str) -> bool:
    """Check if a device kind is cEOS (Arista containerized EOS).

    This is a convenience function that handles all cEOS aliases consistently.
    Use this instead of hard-coded checks like `kind == "ceos"` or
    `kind in ("ceos", "eos")`.

    Args:
        kind: Device kind to check

    Returns:
        True if the kind is cEOS or one of its aliases
    """
    # Normalize to canonical kind first
    canonical = get_kind_for_device(kind)
    return canonical == "ceos"


def get_all_vendors() -> list[VendorConfig]:
    """Return all vendor configurations."""
    return list(VENDOR_CONFIGS.values())


@dataclass
class ContainerRuntimeConfig:
    """Container runtime configuration for DockerProvider.

    This is a simplified view of VendorConfig focused on container creation.
    """
    image: str
    environment: dict[str, str]
    capabilities: list[str]
    privileged: bool
    binds: list[str]
    entrypoint: str | None
    cmd: list[str] | None
    network_mode: str
    sysctls: dict[str, str]
    hostname: str
    memory_mb: int
    cpu_count: int


def get_container_config(
    device: str,
    node_name: str,
    image: str | None = None,
    workspace: str = "",
) -> ContainerRuntimeConfig:
    """Get container runtime configuration for a device type.

    Args:
        device: Device type/kind (e.g., "ceos", "linux", "nokia_srlinux")
        node_name: Node name for hostname and path substitution
        image: Override image (uses default if not specified)
        workspace: Lab workspace path for bind mount substitution

    Returns:
        ContainerRuntimeConfig for container creation
    """
    # Look up by device key first, then by kind, then by alias
    config = VENDOR_CONFIGS.get(device)
    if not config:
        config = _get_config_by_kind(device)
    if not config:
        # Try alias lookup (e.g., "eos" -> "ceos")
        kind = get_kind_for_device(device)
        config = _get_config_by_kind(kind)
    if not config:
        # Fallback to linux defaults
        config = VENDOR_CONFIGS.get("linux")

    # Use provided image or default
    final_image = image or config.default_image
    if not final_image:
        if config.requires_image:
            raise ValueError(
                f"Device '{device}' requires an image but none was provided. "
                f"Import a compatible image ({', '.join(config.supported_image_kinds)}) first."
            )
        final_image = "alpine:latest"

    # Process bind mounts - substitute {workspace} and {node}
    processed_binds = []
    for bind in config.binds:
        processed = bind.replace("{workspace}", workspace).replace("{node}", node_name)
        processed_binds.append(processed)

    # Process hostname template
    hostname = config.hostname_template.replace("{node}", node_name)

    return ContainerRuntimeConfig(
        image=final_image,
        environment=dict(config.environment),
        capabilities=list(config.capabilities),
        privileged=config.privileged,
        binds=processed_binds,
        entrypoint=config.entrypoint,
        cmd=list(config.cmd) if config.cmd else None,
        network_mode=config.network_mode,
        sysctls=dict(config.sysctls),
        hostname=hostname,
        memory_mb=config.memory,
        cpu_count=config.cpu,
    )


@dataclass
class LibvirtRuntimeConfig:
    """Libvirt/QEMU runtime configuration for LibvirtProvider.

    This is a simplified view of VendorConfig focused on VM creation.
    """
    memory_mb: int
    cpu_count: int
    machine_type: str
    disk_driver: str
    nic_driver: str
    data_volume_gb: int
    efi_boot: bool
    efi_vars: str
    readiness_probe: str
    readiness_pattern: str | None
    readiness_timeout: int
    serial_type: str = "pty"  # "pty" (default virsh console) or "tcp" (TCP telnet)
    nographic: bool = False  # Remove VGA/VNC; forces UEFI output to serial
    serial_port_count: int = 1  # Number of serial ports (IOS-XRv 9000 needs 4)
    smbios_product: str = ""  # SMBIOS type=1 product (e.g., "Cisco IOS XRv 9000")
    force_stop: bool = True  # Skip ACPI shutdown (most network VMs don't support it)
    reserved_nics: int = 0  # Dummy NICs after management, before data interfaces
    cpu_sockets: int = 0    # If >0, explicit SMP topology: sockets=N, cores=cpu/N
    needs_nested_vmx: bool = False  # Force VMX CPU flag for AMD hosts (vJunos compat)
    config_inject_method: str = "none"    # "none", "bootflash", or "iso"
    config_inject_partition: int = 0      # 0 = auto-detect via blkid (bootflash only)
    config_inject_fs_type: str = "ext2"   # Expected filesystem of bootflash
    config_inject_path: str = "/startup-config"  # Path within mounted partition (bootflash only)
    config_inject_iso_volume_label: str = ""  # ISO volume label (iso only)
    config_inject_iso_filename: str = ""      # Filename inside ISO (iso only)
    source: str = "vendor"  # "vendor" for matched profile, "fallback" for generic defaults


def get_libvirt_config(device: str) -> LibvirtRuntimeConfig:
    """Get libvirt runtime configuration for a device type.

    Args:
        device: Device type/kind (e.g., "cisco_iosv", "cisco_csr1000v")

    Returns:
        LibvirtRuntimeConfig for VM creation
    """
    # Look up by device key first, then by kind, then by alias
    config = VENDOR_CONFIGS.get(device)
    if not config:
        config = _get_config_by_kind(device)
    if not config:
        # Try alias lookup
        kind = get_kind_for_device(device)
        config = _get_config_by_kind(kind)
    if not config:
        # Memory-intensive VM families should never silently run with generic
        # low defaults because failures are expensive and hard to diagnose.
        memory_intensive_markers = (
            "cat9000",
            "cat9kv",
            "uadp",
            "q200",
            "cat9800",
            "fmcv",
            "ftdv",
        )
        normalized = str(device).lower()
        if any(marker in normalized for marker in memory_intensive_markers):
            raise ValueError(
                f"No vendor/libvirt profile found for memory-intensive device '{device}'. "
                "Refusing fallback defaults. Define explicit device config (memory/cpu/"
                "machine_type/disk_driver/nic_driver)."
            )

        logger.warning(
            "No libvirt profile found for device '%s'; using fallback defaults "
            "(memory=2048MB, cpu=1, machine_type=pc-q35-6.2, disk=virtio, nic=virtio)",
            device,
        )
        # Fallback to defaults
        return LibvirtRuntimeConfig(
            memory_mb=2048,
            cpu_count=1,
            machine_type="pc-q35-6.2",
            disk_driver="virtio",
            nic_driver="virtio",
            data_volume_gb=0,
            efi_boot=False,
            efi_vars="",
            readiness_probe="none",
            readiness_pattern=None,
            readiness_timeout=120,
            serial_type="pty",
            nographic=False,
            serial_port_count=1,
            smbios_product="",
            force_stop=True,
            reserved_nics=0,
            cpu_sockets=0,
            config_inject_method="none",
            config_inject_partition=0,
            config_inject_fs_type="ext2",
            config_inject_path="/startup-config",
            config_inject_iso_volume_label="",
            config_inject_iso_filename="",
            source="fallback",
        )

    return LibvirtRuntimeConfig(
        memory_mb=config.memory,
        cpu_count=config.cpu,
        machine_type=config.machine_type,
        disk_driver=config.disk_driver,
        nic_driver=config.nic_driver,
        data_volume_gb=config.data_volume_gb,
        efi_boot=config.efi_boot,
        efi_vars=config.efi_vars,
        readiness_probe=config.readiness_probe,
        readiness_pattern=config.readiness_pattern,
        readiness_timeout=config.readiness_timeout,
        serial_type=config.serial_type,
        nographic=config.nographic,
        serial_port_count=config.serial_port_count,
        smbios_product=config.smbios_product,
        force_stop=config.force_stop,
        reserved_nics=config.reserved_nics,
        cpu_sockets=config.cpu_sockets,
        needs_nested_vmx=config.needs_nested_vmx,
        config_inject_method=config.config_inject_method,
        config_inject_partition=config.config_inject_partition,
        config_inject_fs_type=config.config_inject_fs_type,
        config_inject_path=config.config_inject_path,
        config_inject_iso_volume_label=config.config_inject_iso_volume_label,
        config_inject_iso_filename=config.config_inject_iso_filename,
        source="vendor",
    )


@dataclass
class ConfigExtractionSettings:
    """Settings for config extraction from a device."""
    method: str  # "serial", "docker", "none"
    command: str
    user: str
    password: str
    enable_password: str
    timeout: int
    prompt_pattern: str
    paging_disable: str


def get_config_extraction_settings(kind: str) -> ConfigExtractionSettings:
    """Get config extraction settings for a device kind.

    Args:
        kind: Device kind (e.g., "cisco_iosv")

    Returns:
        ConfigExtractionSettings for this device type
    """
    config = _get_config_by_kind(kind)
    if not config:
        # Try direct key lookup
        config = VENDOR_CONFIGS.get(kind)

    if not config:
        return ConfigExtractionSettings(
            method="none",
            command="",
            user="",
            password="",
            enable_password="",
            timeout=30,
            prompt_pattern="",
            paging_disable="",
        )

    return ConfigExtractionSettings(
        method=config.config_extract_method,
        command=config.config_extract_command,
        user=config.config_extract_user,
        password=config.config_extract_password,
        enable_password=config.config_extract_enable_password,
        timeout=config.config_extract_timeout,
        prompt_pattern=config.config_extract_prompt_pattern,
        paging_disable=config.config_extract_paging_disable,
    )


def get_config_by_device(device: str) -> VendorConfig | None:
    """Get VendorConfig by device key or alias.

    Args:
        device: Device key, kind, or alias

    Returns:
        VendorConfig if found, None otherwise
    """
    # Try direct key lookup
    if device in VENDOR_CONFIGS:
        return VENDOR_CONFIGS[device]

    # Try kind lookup
    config = _get_config_by_kind(device)
    if config:
        return config

    # Try alias lookup
    kind = get_kind_for_device(device)
    if kind in VENDOR_CONFIGS:
        return VENDOR_CONFIGS[kind]

    return _get_config_by_kind(kind)


def _get_vendor_options(config: VendorConfig) -> dict:
    """Extract vendor-specific options for a device configuration.

    Returns a dictionary of vendor-specific settings that can be customized.
    """
    options = {}

    # Arista cEOS: Zero Touch Provisioning cancel
    if config.kind == "ceos":
        options["zerotouchCancel"] = True

    # Nokia SR Linux: gNMI interface
    if config.kind == "nokia_srlinux":
        options["gnmiEnabled"] = True

    return options


def get_vendors_for_ui() -> list[dict]:
    """Return vendors grouped by category/subcategory for frontend.

    Returns data in the format expected by web/src/studio/constants.tsx:
    [
        {
            "name": "Network",
            "subCategories": [
                {
                    "name": "Switches",
                    "models": [{"id": "ceos", "type": "switch", ...}]
                }
            ]
        },
        {
            "name": "Compute",
            "models": [{"id": "linux", ...}]
        }
    ]
    """
    # Group vendors by category -> subcategory
    categories: dict[str, dict[str, list[dict]]] = {}

    for key, config in VENDOR_CONFIGS.items():
        cat = config.category
        subcat = config.subcategory or "_direct"  # Use _direct for no subcategory

        if cat not in categories:
            categories[cat] = {}
        if subcat not in categories[cat]:
            categories[cat][subcat] = []

        # Use the vendor config key as ID (matches ISO import mapping)
        device_id = key

        categories[cat][subcat].append({
            "id": device_id,
            "type": config.device_type.value,
            "vendor": config.vendor,
            "name": config.label or config.vendor,
            "icon": config.icon,
            "versions": config.versions,
            "isActive": config.is_active,
            # Port/interface configuration
            "portNaming": config.port_naming,
            "portStartIndex": config.port_start_index,
            "maxPorts": config.max_ports,
            "managementInterface": config.management_interface,
            # Resource requirements
            "memory": config.memory,
            "cpu": config.cpu,
            # Libvirt/QEMU VM settings
            "diskDriver": config.disk_driver,
            "nicDriver": config.nic_driver,
            "machineType": config.machine_type,
            "efiBoot": config.efi_boot,
            # Image configuration
            "requiresImage": config.requires_image,
            "supportedImageKinds": config.supported_image_kinds,
            # Documentation and licensing
            "documentationUrl": config.documentation_url,
            "licenseRequired": config.license_required,
            "tags": config.tags,
            # Boot readiness configuration
            "readinessProbe": config.readiness_probe,
            "readinessPattern": config.readiness_pattern,
            "readinessTimeout": config.readiness_timeout,
            # Additional metadata
            "kind": config.kind,
            "consoleShell": config.console_shell,
            "notes": config.notes,
            # Vendor-specific options
            "vendorOptions": _get_vendor_options(config),
            # Default credentials hint
            "defaultCredentials": config.default_credentials,
        })

    # Convert to output format
    result = []
    # Define category order
    category_order = ["Network", "Security", "Compute"]

    for cat in category_order:
        if cat not in categories:
            continue

        subcats = categories[cat]
        cat_data: dict = {"name": cat}

        # Check if category has subcategories (other than _direct)
        has_subcategories = any(k != "_direct" for k in subcats.keys())

        if has_subcategories:
            cat_data["subCategories"] = []
            # Define subcategory order for Network
            subcat_order = ["Routers", "Switches", "Load Balancers", "_direct"]
            for subcat in subcat_order:
                if subcat not in subcats:
                    continue
                if subcat == "_direct":
                    # Direct models without subcategory
                    if subcats[subcat]:
                        cat_data["subCategories"].append({
                            "name": "Other",
                            "models": subcats[subcat]
                        })
                else:
                    cat_data["subCategories"].append({
                        "name": subcat,
                        "models": subcats[subcat]
                    })
        else:
            # No subcategories, models directly on category
            cat_data["models"] = subcats.get("_direct", [])

        result.append(cat_data)

    return result
