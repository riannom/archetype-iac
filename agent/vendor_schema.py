"""Vendor configuration schema definitions.

Dataclasses and enums that define the structure of vendor device configurations.
These are the building blocks used by vendor_registry.py (data) and vendors.py (logic).

SINGLE SOURCE OF TRUTH: This module defines the canonical schema for all vendor
device configuration. Do not duplicate these definitions elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import functools


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
    management_interface: str | None


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
    cpu_features_disable: tuple[str, ...]


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
    readiness_pattern: str | None
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
    entrypoint: str | None
    cmd: list | None
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
    documentation_url: str | None
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
    default_image: str | None
    notes: str = ""

    # Alias resolution (used by topology.py)
    aliases: list[str] = field(default_factory=list)

    # Platform grouping (e.g., "cisco_cat9kv" for Cat9800/Cat9000v variants)
    # Used to group related devices that share image artifacts.
    platform: str = ""

    # UI metadata (used by frontend)
    device_type: DeviceType = DeviceType.CONTAINER
    category: str = "Compute"
    subcategory: str | None = None
    label: str = ""
    icon: str = "fa-box"
    versions: list[str] = field(default_factory=lambda: ["latest"])
    is_active: bool = True

    # Interface/port configuration
    port_naming: str = "eth"
    port_start_index: int = 0
    max_ports: int = 8
    management_interface: str | None = None  # NOS-level management interface name (e.g., "mgmt0")
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
    cpu_features_disable: list[str] = field(default_factory=list)  # CPU features to disable (e.g., smep, smap)

    # Image requirements
    requires_image: bool = True
    supported_image_kinds: list[str] = field(default_factory=lambda: ["docker"])

    # Documentation and licensing
    documentation_url: str | None = None
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
    readiness_pattern: str | None = None  # Regex pattern for log/cli detection
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

    # Injection method: "none", "bootflash" (qemu-nbd mount+write), "iso" (CD-ROM), or "config_disk" (VFAT USB)
    config_inject_method: str = "none"
    # Partition number to mount (0 = auto-detect via blkid) — bootflash only
    config_inject_partition: int = 0
    # Expected filesystem type of the target partition — bootflash only
    config_inject_fs_type: str = "ext2"
    # Path within the mounted partition where startup-config is written — bootflash only
    config_inject_path: str = "/startup-config"
    # ISO 9660 volume label for config CD-ROM — iso only (e.g., "config" for IOS-XR CVAC)
    config_inject_iso_volume_label: str = ""
    # Filename inside the ISO — iso only (e.g., "iosxr_config.txt" for IOS-XR CVAC)
    config_inject_iso_filename: str = ""

    # Default startup config template applied when no user config exists.
    # Use {hostname} placeholder for the node name.
    default_startup_config: str = ""

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
    entrypoint: str | None = None

    # Override the default command
    cmd: list[str] | None = None

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

    @functools.cached_property
    def interfaces(self) -> InterfaceConfig:
        return InterfaceConfig(
            port_naming=self.port_naming,
            port_start_index=self.port_start_index,
            max_ports=self.max_ports,
            management_interface=self.management_interface,
        )

    @functools.cached_property
    def resources(self) -> ResourceConfig:
        return ResourceConfig(memory=self.memory, cpu=self.cpu)

    @functools.cached_property
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
            cpu_features_disable=tuple(self.cpu_features_disable),
        )

    @functools.cached_property
    def console(self) -> ConsoleConfig:
        return ConsoleConfig(
            console_method=self.console_method,
            console_shell=self.console_shell,
            console_user=self.console_user,
            console_password=self.console_password,
            default_credentials=self.default_credentials,
        )

    @functools.cached_property
    def readiness(self) -> ReadinessConfig:
        return ReadinessConfig(
            readiness_probe=self.readiness_probe,
            readiness_pattern=self.readiness_pattern,
            readiness_timeout=self.readiness_timeout,
        )

    @functools.cached_property
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

    @functools.cached_property
    def config_injection(self) -> ConfigInjectionConfig:
        return ConfigInjectionConfig(
            config_inject_method=self.config_inject_method,
            config_inject_partition=self.config_inject_partition,
            config_inject_fs_type=self.config_inject_fs_type,
            config_inject_path=self.config_inject_path,
            config_inject_iso_volume_label=self.config_inject_iso_volume_label,
            config_inject_iso_filename=self.config_inject_iso_filename,
        )

    @functools.cached_property
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

    @functools.cached_property
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
