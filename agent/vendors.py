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
from typing import Optional


class DeviceType(str, Enum):
    """Device type classification for UI categorization."""
    ROUTER = "router"
    SWITCH = "switch"
    FIREWALL = "firewall"
    HOST = "host"
    CONTAINER = "container"
    EXTERNAL = "external"


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
    # Note: provision_interfaces is deprecated. OVS-based networking handles
    # interface provisioning automatically for all device types.

    # Resource requirements
    memory: int = 1024  # Memory in MB
    cpu: int = 1  # CPU cores

    # Image requirements
    requires_image: bool = True
    supported_image_kinds: list[str] = field(default_factory=lambda: ["docker"])

    # Documentation and licensing
    documentation_url: Optional[str] = None
    license_required: bool = False

    # Searchable tags
    tags: list[str] = field(default_factory=list)

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


# =============================================================================
# VENDOR CONFIGURATIONS - Single Source of Truth
# =============================================================================
# Add new vendors here. They will automatically appear in:
# - Console access (agent uses console_shell)
# - Topology generation (API uses aliases and default_image)
# - UI device palette (frontend uses category, label, icon, versions)
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
        aliases=["vyos"],
        device_type=DeviceType.ROUTER,
        category="Network",
        subcategory="Routers",
        label="VyOS",
        icon="fa-arrows-to-dot",
        versions=["1.4-rolling", "1.3.3"],
        is_active=True,
        notes="VyOS uses vbash for configuration mode.",
        port_naming="eth",
        port_start_index=0,
        max_ports=8,
        requires_image=False,
        documentation_url="https://docs.vyos.io/",
        tags=["routing", "firewall", "vpn", "bgp", "ospf"],
        # Container runtime configuration
        capabilities=["NET_ADMIN", "SYS_ADMIN"],
        privileged=True,
        sysctls={
            "net.ipv4.ip_forward": "1",
            "net.ipv6.conf.all.forwarding": "1",
        },
    ),
    "cisco_iosxr": VendorConfig(
        kind="cisco_iosxr",
        vendor="Cisco",
        console_shell="/bin/bash",
        default_image="ios-xr:latest",
        aliases=["iosxr", "xr"],
        device_type=DeviceType.ROUTER,
        category="Network",
        subcategory="Routers",
        label="Cisco IOS-XR",
        icon="fa-arrows-to-dot",
        versions=["7.5.2", "7.3.2"],
        is_active=True,
        notes="IOS-XR starts in bash. Run 'xr' for XR CLI.",
        port_naming="GigabitEthernet",
        port_start_index=0,
        max_ports=8,
        memory=4096,  # 4GB recommended
        cpu=2,
        requires_image=True,
        documentation_url="https://www.cisco.com/c/en/us/td/docs/iosxr/",
        license_required=True,
        tags=["routing", "bgp", "mpls", "segment-routing", "netconf"],
    ),
    "cisco_xrd": VendorConfig(
        kind="cisco_xrd",
        vendor="Cisco",
        console_shell="/bin/bash",
        default_image="ios-xrd:latest",
        aliases=["xrd"],
        device_type=DeviceType.ROUTER,
        category="Network",
        subcategory="Routers",
        label="Cisco XRd",
        icon="fa-arrows-to-dot",
        versions=["7.8.1", "7.7.1"],
        is_active=True,
        notes="XRd container variant of IOS-XR.",
        port_naming="GigabitEthernet",
        port_start_index=0,
        max_ports=8,
        requires_image=True,
        documentation_url="https://www.cisco.com/c/en/us/td/docs/iosxr/cisco8000/xrd/",
        license_required=True,
        tags=["routing", "bgp", "mpls", "segment-routing", "container"],
    ),
    "cisco_iosv": VendorConfig(
        kind="linux",  # Uses linux kind as fallback (QEMU-based)
        vendor="Cisco",
        console_shell="/bin/sh",
        default_image=None,  # Requires user-provided image
        aliases=["iosv"],
        device_type=DeviceType.ROUTER,
        category="Network",
        subcategory="Routers",
        label="Cisco IOSv",
        icon="fa-arrows-to-dot",
        versions=["15.9(3)M4", "15.8"],
        is_active=True,
        notes="IOSv requires vrnetlab or QEMU setup. User must import image.",
        port_naming="GigabitEthernet",
        port_start_index=0,
        max_ports=8,
        requires_image=True,
        supported_image_kinds=["qcow2"],
        documentation_url="https://www.cisco.com/c/en/us/td/docs/ios/",
        license_required=True,
        tags=["routing", "bgp", "ospf", "eigrp", "legacy"],
    ),
    "cisco_csr1000v": VendorConfig(
        kind="linux",  # Uses linux kind as fallback (QEMU-based)
        vendor="Cisco",
        console_shell="/bin/sh",
        default_image=None,  # Requires user-provided image
        aliases=["csr1000v", "csr"],
        device_type=DeviceType.ROUTER,
        category="Network",
        subcategory="Routers",
        label="Cisco CSR1000v",
        icon="fa-arrows-to-dot",
        versions=["17.3.2", "17.2.1"],
        is_active=True,
        notes="CSR1000v requires vrnetlab or QEMU setup. User must import image.",
        port_naming="GigabitEthernet",
        port_start_index=1,
        max_ports=8,
        requires_image=True,
        supported_image_kinds=["qcow2"],
        documentation_url="https://www.cisco.com/c/en/us/td/docs/routers/csr1000/",
        license_required=True,
        tags=["routing", "bgp", "sd-wan", "ipsec", "cloud"],
    ),
    "juniper_crpd": VendorConfig(
        kind="juniper_crpd",
        vendor="Juniper",
        console_shell="cli",
        default_image="crpd:latest",
        aliases=["crpd"],
        device_type=DeviceType.ROUTER,
        category="Network",
        subcategory="Routers",
        label="Juniper cRPD",
        icon="fa-arrows-to-dot",
        versions=["23.2R1", "22.4R1"],
        is_active=True,
        notes="cRPD uses standard Junos CLI.",
        port_naming="eth",
        port_start_index=0,
        max_ports=8,
        requires_image=True,
        documentation_url="https://www.juniper.net/documentation/product/us/en/crpd/",
        license_required=True,
        tags=["routing", "bgp", "mpls", "container", "kubernetes"],
    ),
    "juniper_vsrx3": VendorConfig(
        kind="juniper_vsrx3",
        vendor="Juniper",
        console_shell="cli",
        default_image="vrnetlab/vr-vsrx3:latest",
        aliases=["vsrx", "vsrx3"],
        device_type=DeviceType.ROUTER,
        category="Network",
        subcategory="Routers",
        label="Juniper vSRX3",
        icon="fa-arrows-to-dot",
        versions=["23.2R1", "22.4R1"],
        is_active=True,
        notes="vSRX3 with standard Junos CLI.",
        port_naming="ge-0/0/",
        port_start_index=0,
        max_ports=8,
        requires_image=True,
        supported_image_kinds=["qcow2", "docker"],
        documentation_url="https://www.juniper.net/documentation/product/us/en/vsrx/",
        license_required=True,
        tags=["routing", "firewall", "security", "ipsec", "nat"],
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
        aliases=["eos", "arista_eos", "arista_ceos"],
        # Note: entrypoint is overridden in docker.py to use if-wait.sh wrapper
        entrypoint="/sbin/init",
        device_type=DeviceType.SWITCH,
        category="Network",
        subcategory="Switches",
        label="Arista EOS",
        icon="fa-arrows-left-right-to-line",
        versions=["4.35.1F", "4.28.0F", "4.27.1F"],
        is_active=True,
        notes="cEOS requires 'Cli' command for EOS prompt. User must import image.",
        port_naming="Ethernet",
        port_start_index=1,
        max_ports=12,
        memory=2048,  # 2GB recommended minimum
        cpu=2,
        requires_image=True,
        documentation_url="https://www.arista.com/en/support/product-documentation",
        license_required=True,
        tags=["switching", "bgp", "evpn", "vxlan", "datacenter"],
        # Boot readiness detection via log patterns
        readiness_probe="log_pattern",
        readiness_pattern=r"%SYS-5-CONFIG_I|%SYS-5-SYSTEM_INITIALIZED|%SYS-5-SYSTEM_RESTARTED|%ZTP-6-CANCEL|Startup complete|System ready",
        readiness_timeout=300,  # cEOS can take up to 5 minutes
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
    ),
    "nokia_srlinux": VendorConfig(
        kind="nokia_srlinux",
        vendor="Nokia",
        console_shell="sr_cli",
        default_image="ghcr.io/nokia/srlinux:latest",
        aliases=["srlinux", "srl"],
        device_type=DeviceType.SWITCH,
        category="Network",
        subcategory="Switches",
        label="Nokia SR Linux",
        icon="fa-arrows-left-right-to-line",
        versions=["23.10.1", "23.7.1", "latest"],
        is_active=True,
        notes="SR Linux uses sr_cli for its native CLI.",
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
    ),
    "cvx": VendorConfig(
        kind="cvx",
        vendor="NVIDIA",
        console_shell="/bin/bash",
        default_image="networkop/cx:5.4.0",
        aliases=["cumulus"],
        device_type=DeviceType.SWITCH,
        category="Network",
        subcategory="Switches",
        label="NVIDIA Cumulus",
        icon="fa-arrows-left-right-to-line",
        versions=["5.4.0", "4.4.0"],
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
        aliases=["sonic"],
        device_type=DeviceType.SWITCH,
        category="Network",
        subcategory="Switches",
        label="SONiC",
        icon="fa-arrows-left-right-to-line",
        versions=["latest", "202305"],
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
        aliases=["vjunos"],
        device_type=DeviceType.SWITCH,
        category="Network",
        subcategory="Switches",
        label="Juniper vJunos Switch",
        icon="fa-arrows-left-right-to-line",
        versions=["23.2R1", "22.4R1"],
        is_active=True,
        notes="vJunos Switch with standard Junos CLI.",
        port_naming="ge-0/0/",
        port_start_index=0,
        max_ports=12,
        requires_image=True,
        supported_image_kinds=["qcow2", "docker"],
        documentation_url="https://www.juniper.net/documentation/product/us/en/vjunos-switch/",
        license_required=True,
        tags=["switching", "evpn", "vxlan", "datacenter"],
    ),
    "juniper_vqfx": VendorConfig(
        kind="juniper_vqfx",
        vendor="Juniper",
        console_shell="cli",
        default_image="vrnetlab/vr-vqfx:latest",
        aliases=["vqfx"],
        device_type=DeviceType.SWITCH,
        category="Network",
        subcategory="Switches",
        label="Juniper vQFX",
        icon="fa-arrows-left-right-to-line",
        versions=["20.2R1", "19.4R1"],
        is_active=True,
        notes="vQFX with standard Junos CLI.",
        port_naming="xe-0/0/",
        port_start_index=0,
        max_ports=12,
        requires_image=True,
        supported_image_kinds=["qcow2", "docker"],
        documentation_url="https://www.juniper.net/documentation/product/us/en/virtual-qfx/",
        license_required=True,
        tags=["switching", "evpn", "datacenter"],
    ),
    "cisco_n9kv": VendorConfig(
        kind="cisco_n9kv",
        vendor="Cisco",
        console_shell="/bin/bash",
        default_image="vrnetlab/vr-n9kv:latest",
        aliases=["nxos", "n9kv"],
        device_type=DeviceType.SWITCH,
        category="Network",
        subcategory="Switches",
        label="Cisco NX-OSv",
        icon="fa-arrows-left-right-to-line",
        versions=["9.3.9", "9.3.8"],
        is_active=False,  # Requires specific setup
        notes="Nexus 9000v requires vrnetlab image.",
        port_naming="Ethernet1/",
        port_start_index=1,
        max_ports=12,
        requires_image=True,
        supported_image_kinds=["qcow2"],
        documentation_url="https://www.cisco.com/c/en/us/td/docs/switches/datacenter/nexus9000/",
        license_required=True,
        tags=["switching", "vxlan", "evpn", "datacenter", "aci"],
        console_method="ssh",
        console_user="admin",
        console_password="admin",
    ),

    # =========================================================================
    # NETWORK DEVICES - Load Balancers
    # =========================================================================
    "f5_bigip": VendorConfig(
        kind="linux",
        vendor="F5",
        console_shell="/bin/bash",
        default_image=None,  # Requires user-provided image
        aliases=["f5", "bigip"],
        device_type=DeviceType.SWITCH,
        category="Network",
        subcategory="Load Balancers",
        label="F5 BIG-IP VE",
        icon="fa-server",
        versions=["16.1.0", "17.0.0"],
        is_active=True,
        notes="F5 BIG-IP requires licensed image. User must import.",
        port_naming="1.",
        port_start_index=1,
        max_ports=8,
        requires_image=True,
        supported_image_kinds=["qcow2"],
        documentation_url="https://clouddocs.f5.com/",
        license_required=True,
        tags=["load-balancer", "waf", "ssl", "adc"],
    ),
    "haproxy": VendorConfig(
        kind="linux",
        vendor="Open Source",
        console_shell="/bin/sh",
        default_image="haproxy:latest",
        aliases=["haproxy"],
        device_type=DeviceType.CONTAINER,
        category="Network",
        subcategory="Load Balancers",
        label="HAProxy",
        icon="fa-box",
        versions=["2.6", "2.8", "latest"],
        is_active=True,
        notes="HAProxy load balancer container.",
        port_naming="eth",
        port_start_index=0,
        max_ports=8,
        requires_image=False,
        documentation_url="https://www.haproxy.org/#docs",
        tags=["load-balancer", "proxy", "open-source"],
    ),
    "citrix_adc": VendorConfig(
        kind="linux",
        vendor="Citrix",
        console_shell="/bin/bash",
        default_image=None,
        aliases=["citrix", "adc"],
        device_type=DeviceType.SWITCH,
        category="Network",
        subcategory="Load Balancers",
        label="Citrix ADC",
        icon="fa-server",
        versions=["13.1"],
        is_active=False,
        notes="Citrix ADC requires licensed image.",
        port_naming="0/",
        port_start_index=1,
        max_ports=8,
        requires_image=True,
        supported_image_kinds=["qcow2"],
        documentation_url="https://docs.citrix.com/en-us/citrix-adc",
        license_required=True,
        tags=["load-balancer", "adc", "ssl"],
    ),

    # =========================================================================
    # SECURITY DEVICES
    # =========================================================================
    "cisco_asav": VendorConfig(
        kind="linux",
        vendor="Cisco",
        console_shell="/bin/sh",
        default_image=None,  # Requires user-provided image
        aliases=["asa", "asav"],
        device_type=DeviceType.FIREWALL,
        category="Security",
        subcategory=None,
        label="Cisco ASAv",
        icon="fa-shield-halved",
        versions=["9.16.1", "9.15.1"],
        is_active=True,
        notes="ASAv requires vrnetlab or QEMU setup. User must import image.",
        port_naming="GigabitEthernet0/",
        port_start_index=0,
        max_ports=10,
        requires_image=True,
        supported_image_kinds=["qcow2"],
        documentation_url="https://www.cisco.com/c/en/us/td/docs/security/asa/",
        license_required=True,
        tags=["firewall", "vpn", "ipsec", "nat", "security"],
    ),
    "fortinet_fortigate": VendorConfig(
        kind="linux",
        vendor="Fortinet",
        console_shell="/bin/sh",
        default_image=None,
        aliases=["fortigate", "fortinet"],
        device_type=DeviceType.FIREWALL,
        category="Security",
        subcategory=None,
        label="FortiGate VM",
        icon="fa-user-shield",
        versions=["7.2.0", "7.0.5"],
        is_active=False,
        notes="FortiGate requires licensed image.",
        port_naming="port",
        port_start_index=1,
        max_ports=10,
        requires_image=True,
        supported_image_kinds=["qcow2"],
        documentation_url="https://docs.fortinet.com/product/fortigate/",
        license_required=True,
        tags=["firewall", "utm", "vpn", "security", "sd-wan"],
    ),
    "paloalto_vmseries": VendorConfig(
        kind="linux",
        vendor="Palo Alto",
        console_shell="/bin/sh",
        default_image=None,
        aliases=["paloalto", "pan", "vmseries"],
        device_type=DeviceType.FIREWALL,
        category="Security",
        subcategory=None,
        label="Palo Alto VM-Series",
        icon="fa-lock",
        versions=["10.1.0", "10.0.0"],
        is_active=False,
        notes="VM-Series requires licensed image.",
        port_naming="ethernet1/",
        port_start_index=1,
        max_ports=10,
        requires_image=True,
        supported_image_kinds=["qcow2"],
        documentation_url="https://docs.paloaltonetworks.com/vm-series",
        license_required=True,
        tags=["firewall", "ngfw", "security", "threat-prevention"],
    ),

    # =========================================================================
    # COMPUTE
    # =========================================================================
    "linux": VendorConfig(
        kind="linux",
        vendor="Open Source",
        console_shell="/bin/sh",
        default_image="alpine:latest",
        aliases=["alpine", "ubuntu", "debian"],
        device_type=DeviceType.HOST,
        category="Compute",
        subcategory=None,
        label="Linux Server",
        icon="fa-terminal",
        versions=["Alpine", "Ubuntu 22.04", "Debian 12"],
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
    "frr": VendorConfig(
        kind="linux",
        vendor="Open Source",
        console_shell="vtysh",
        default_image="quay.io/frrouting/frr:latest",
        aliases=["frr", "frrouting"],
        device_type=DeviceType.CONTAINER,
        category="Compute",
        subcategory=None,
        label="FRR Container",
        icon="fa-box-open",
        versions=["latest", "8.4.1", "8.3"],
        is_active=True,
        notes="FRR uses vtysh for routing configuration.",
        port_naming="eth",
        port_start_index=0,
        max_ports=8,
        requires_image=False,
        documentation_url="https://docs.frrouting.org/",
        tags=["routing", "bgp", "ospf", "open-source", "container"],
        # Container runtime configuration
        capabilities=["NET_ADMIN", "SYS_ADMIN"],
        privileged=True,
        sysctls={
            "net.ipv4.ip_forward": "1",
            "net.ipv6.conf.all.forwarding": "1",
        },
    ),
    "windows": VendorConfig(
        kind="linux",  # Placeholder - needs special handling
        vendor="Microsoft",
        console_shell="/bin/sh",
        default_image=None,
        aliases=["windows", "win"],
        device_type=DeviceType.HOST,
        category="Compute",
        subcategory=None,
        label="Windows Server",
        icon="fa-window-maximize",
        versions=["2022", "2019"],
        is_active=False,
        notes="Windows requires special QEMU/KVM setup.",
        port_naming="Ethernet",
        port_start_index=0,
        max_ports=4,
        requires_image=True,
        supported_image_kinds=["qcow2"],
        documentation_url="https://docs.microsoft.com/en-us/windows-server/",
        license_required=True,
        tags=["host", "windows", "server"],
    ),

    # =========================================================================
    # CLOUD & EXTERNAL
    # =========================================================================
    "internet": VendorConfig(
        kind="bridge",  # Special type for external connectivity
        vendor="System",
        console_shell="/bin/sh",
        default_image=None,
        aliases=["internet", "wan"],
        device_type=DeviceType.EXTERNAL,
        category="Cloud & External",
        subcategory=None,
        label="Public Internet",
        icon="fa-cloud",
        versions=["Default"],
        is_active=True,
        notes="External bridge for internet connectivity.",
        port_naming="",
        port_start_index=0,
        max_ports=1,
        requires_image=False,
        tags=["external", "bridge", "connectivity"],
    ),
    "mgmt_bridge": VendorConfig(
        kind="bridge",  # Special type for management
        vendor="System",
        console_shell="/bin/sh",
        default_image=None,
        aliases=["mgmt", "management"],
        device_type=DeviceType.EXTERNAL,
        category="Cloud & External",
        subcategory=None,
        label="Management Bridge",
        icon="fa-plug-circle-bolt",
        versions=["br0"],
        is_active=True,
        notes="Management bridge for OOB access.",
        port_naming="",
        port_start_index=0,
        max_ports=1,
        requires_image=False,
        tags=["external", "bridge", "management", "oob"],
    ),

    # =========================================================================
    # CISCO SD-WAN (VM-based, requires libvirt provider)
    # =========================================================================
    "c8000v": VendorConfig(
        kind="cisco_c8000v",
        vendor="Cisco",
        console_shell="/bin/sh",  # Fallback, not used with SSH method
        default_image=None,
        aliases=["cat-sdwan-edge", "sdwan-edge", "cedge", "c8000v"],
        device_type=DeviceType.ROUTER,
        category="Network",
        subcategory="Routers",
        label="Catalyst SD-WAN Edge",
        icon="fa-arrows-to-dot",
        versions=["17.16.01a", "17.15.01"],
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
        readiness_probe="log_pattern",
        readiness_pattern=r"Press RETURN to get started!",
        readiness_timeout=250,
        console_method="ssh",
        console_user="admin",
        console_password="admin",
    ),
    "cat-sdwan-controller": VendorConfig(
        kind="cat-sdwan-controller",
        vendor="Cisco",
        console_shell="/bin/sh",
        default_image=None,
        aliases=["viptela-smart", "smart-controller"],
        device_type=DeviceType.ROUTER,
        category="Network",
        subcategory="Routers",
        label="SD-WAN Controller",
        icon="fa-server",
        versions=["20.16.1", "20.15.1"],
        is_active=True,
        notes="Cisco SD-WAN Controller (vSmart). Requires QEMU/libvirt.",
        port_naming="eth",
        port_start_index=0,
        max_ports=4,
        memory=2048,
        cpu=2,
        requires_image=True,
        supported_image_kinds=["qcow2"],
        documentation_url="https://www.cisco.com/c/en/us/td/docs/routers/sdwan/",
        license_required=True,
        tags=["sd-wan", "controller", "vm"],
        readiness_probe="log_pattern",
        readiness_pattern=r"login:",
        readiness_timeout=180,
        console_method="ssh",
        console_user="admin",
        console_password="admin",
    ),
    "cat-sdwan-manager": VendorConfig(
        kind="cat-sdwan-manager",
        vendor="Cisco",
        console_shell="/bin/sh",
        default_image=None,
        aliases=["viptela-vmanage", "vmanage"],
        device_type=DeviceType.ROUTER,
        category="Network",
        subcategory="Routers",
        label="SD-WAN Manager",
        icon="fa-server",
        versions=["20.16.1", "20.15.1"],
        is_active=True,
        notes="Cisco SD-WAN Manager (vManage). Requires QEMU/libvirt. 256GB data volume.",
        port_naming="eth",
        port_start_index=0,
        max_ports=4,
        memory=32768,  # 32GB recommended
        cpu=16,
        requires_image=True,
        supported_image_kinds=["qcow2"],
        documentation_url="https://www.cisco.com/c/en/us/td/docs/routers/sdwan/",
        license_required=True,
        tags=["sd-wan", "manager", "nms", "vm"],
        readiness_probe="log_pattern",
        readiness_pattern=r"login:",
        readiness_timeout=600,  # vManage takes longer to boot
        console_method="ssh",
        console_user="admin",
        console_password="admin",
    ),
    "cat-sdwan-validator": VendorConfig(
        kind="cat-sdwan-validator",
        vendor="Cisco",
        console_shell="/bin/sh",
        default_image=None,
        aliases=["viptela-bond", "vbond", "validator"],
        device_type=DeviceType.ROUTER,
        category="Network",
        subcategory="Routers",
        label="SD-WAN Validator",
        icon="fa-server",
        versions=["20.16.1", "20.15.1"],
        is_active=True,
        notes="Cisco SD-WAN Validator (vBond). Requires QEMU/libvirt.",
        port_naming="eth",
        port_start_index=0,
        max_ports=4,
        memory=2048,
        cpu=2,
        requires_image=True,
        supported_image_kinds=["qcow2"],
        documentation_url="https://www.cisco.com/c/en/us/td/docs/routers/sdwan/",
        license_required=True,
        tags=["sd-wan", "validator", "vm"],
        readiness_probe="log_pattern",
        readiness_pattern=r"login:",
        readiness_timeout=180,
        console_method="ssh",
        console_user="admin",
        console_password="admin",
    ),
    "cat-sdwan-vedge": VendorConfig(
        kind="cat-sdwan-vedge",
        vendor="Cisco",
        console_shell="/bin/sh",
        default_image=None,
        aliases=["vedge", "viptela-vedge"],
        device_type=DeviceType.ROUTER,
        category="Network",
        subcategory="Routers",
        label="SD-WAN vEdge",
        icon="fa-arrows-to-dot",
        versions=["20.16.1", "20.15.1"],
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
        readiness_probe="log_pattern",
        readiness_pattern=r"login:",
        readiness_timeout=180,
        console_method="ssh",
        console_user="admin",
        console_password="admin",
    ),

    # =========================================================================
    # CISCO SECURITY (VM-based)
    # =========================================================================
    "ftdv": VendorConfig(
        kind="cisco_ftdv",
        vendor="Cisco",
        console_shell="/bin/sh",
        default_image=None,
        aliases=["firepower-threat-defense", "ftd"],
        device_type=DeviceType.FIREWALL,
        category="Security",
        subcategory=None,
        label="Firepower Threat Defense",
        icon="fa-shield-halved",
        versions=["7.7.0", "7.6.0", "7.4.0"],
        is_active=True,
        notes="Cisco Firepower Threat Defense Virtual. Requires QEMU/libvirt.",
        port_naming="GigabitEthernet0/",
        port_start_index=0,
        max_ports=10,
        memory=8192,  # 8GB required
        cpu=4,
        requires_image=True,
        supported_image_kinds=["qcow2"],
        documentation_url="https://www.cisco.com/c/en/us/td/docs/security/firepower/quick_start/kvm/ftdv-kvm-gsg.html",
        license_required=True,
        tags=["firewall", "ngfw", "security", "threat-defense", "vm"],
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
        aliases=["firepower-management-center", "fmc"],
        device_type=DeviceType.FIREWALL,
        category="Security",
        subcategory=None,
        label="Firepower Management Center",
        icon="fa-server",
        versions=["7.7.0", "7.6.0", "7.4.0"],
        is_active=True,
        notes="Cisco Firepower Management Center Virtual. Requires QEMU/libvirt. 250GB data volume.",
        port_naming="eth",
        port_start_index=0,
        max_ports=2,
        memory=32768,  # 32GB required
        cpu=8,
        requires_image=True,
        supported_image_kinds=["qcow2"],
        documentation_url="https://www.cisco.com/c/en/us/td/docs/security/firepower/quick_start/kvm/fmcv-kvm-gsg.html",
        license_required=True,
        tags=["firewall", "management", "security", "vm"],
        readiness_probe="log_pattern",
        readiness_pattern=r"login:",
        readiness_timeout=600,  # FMC takes longer to boot
        console_method="ssh",
        console_user="admin",
        console_password="admin",
    ),

    # =========================================================================
    # CISCO WIRELESS (VM-based)
    # =========================================================================
    "cat9800": VendorConfig(
        kind="cisco_cat9kv",
        vendor="Cisco",
        console_shell="/bin/sh",
        default_image=None,
        aliases=["cat9800-cl", "c9800", "wlc"],
        device_type=DeviceType.ROUTER,
        category="Network",
        subcategory="Wireless",
        label="Catalyst 9800 WLC",
        icon="fa-wifi",
        versions=["17.17.01", "17.15.01"],
        is_active=True,
        notes="Cisco Catalyst 9800-CL Wireless LAN Controller. Requires QEMU/libvirt.",
        port_naming="GigabitEthernet",
        port_start_index=1,
        max_ports=4,
        memory=8192,  # 8GB required
        cpu=4,
        requires_image=True,
        supported_image_kinds=["qcow2"],
        documentation_url="https://www.cisco.com/c/en/us/td/docs/wireless/controller/9800/",
        license_required=True,
        tags=["wireless", "wlc", "wifi", "ap", "vm"],
        readiness_probe="log_pattern",
        readiness_pattern=r"Press RETURN to get started!",
        readiness_timeout=300,
        console_method="ssh",
        console_user="admin",
        console_password="admin",
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


def _get_config_by_kind(kind: str) -> VendorConfig | None:
    """Look up VendorConfig by device kind.

    This handles the mapping from device kinds (e.g., 'cisco_c8000v')
    to VendorConfig entries (keyed by 'c8000v').
    """
    return _KIND_TO_CONFIG.get(kind)


def get_console_shell(kind: str) -> str:
    """Get the console shell command for a device kind.

    Args:
        kind: The device kind (from archetype.node_kind or clab-node-kind label)

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
        kind: The device kind (from archetype.node_kind or clab-node-kind label)

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
        kind: The device kind (from archetype.node_kind or clab-node-kind label)

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
    final_image = image or config.default_image or "alpine:latest"

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
            # Resource requirements
            "memory": config.memory,
            "cpu": config.cpu,
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
        })

    # Convert to output format
    result = []
    # Define category order
    category_order = ["Network", "Security", "Compute", "Cloud & External"]

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
