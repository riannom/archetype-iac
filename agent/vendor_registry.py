"""Vendor configuration registry — the device data blob.

This module contains the VENDOR_CONFIGS dictionary, the single source of truth
for all vendor/device configurations. Schema types are imported from vendor_schema.py.

When adding a new vendor:
1. Add entry to VENDOR_CONFIGS with all fields
2. Test console access with a running container
3. Rebuild containers: docker compose -f docker-compose.gui.yml up -d --build
4. New device will appear in API (/vendors) and UI automatically

KEY NAMING CONVENTION:
  - Industry shorthand for well-known: ceos, vyos, frr, linux, alpine, tcl
  - vendor_device for vendor-specific: cisco_iosv, cisco_n9kv, juniper_vjunosrouter
  - Hyphenated for sub-models: cat9000v-q200, cat9000v-uadp, iol-xe, iol-l2
  - All keys are lowercase; underscores separate vendor from device
"""

from __future__ import annotations

from agent.vendor_schema import DeviceType, VendorConfig


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
        # Config extraction via TCP serial console (immune to user SSH/password changes)
        config_extract_method="serial",
        config_extract_command="show running-config",
        config_extract_user="admin",
        config_extract_password="cisco",
        config_extract_timeout=60,
        config_extract_prompt_pattern=r"RP/\d+/RP\d+/CPU\d+:[\w\-]+#",
        config_extract_paging_disable="terminal length 0",
        console_user="admin",
        console_password="cisco",
        documentation_url="https://www.cisco.com/c/en/us/td/docs/iosxr/",
        license_required=True,
        tags=["routing", "bgp", "mpls", "segment-routing", "netconf"],
        filename_patterns=[r"xrv9k[_-]?[\d\.]+.*\.qcow2", r"iosxrv9000[_-]?[\d\.]+.*\.qcow2"],
        vrnetlab_subdir="cisco/xrv9k",
        # Config injection: IOS-XR CVAC reads config from CD-ROM with label "config"
        config_inject_method="iso",
        config_inject_iso_volume_label="config",
        config_inject_iso_filename="iosxr_config.txt",
        # Default config prevents the Administrative User Dialog on first boot
        default_startup_config=(
            "hostname {hostname}\n"
            "username admin\n"
            " group root-lr\n"
            " group cisco-support\n"
            " secret cisco\n"
            "!\n"
            "username cisco\n"
            " group root-lr\n"
            " group cisco-support\n"
            " secret cisco\n"
            "!\n"
            "username lab\n"
            " group root-lr\n"
            " group cisco-support\n"
            " secret cisco\n"
            "!\n"
            "end\n"
        ),
        default_credentials="admin / cisco",
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
        filename_keywords=["ios-xrd", "xrd"],
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
        # Config injection via ISO (IOS-XE CVAC)
        config_inject_method="iso",
        config_inject_iso_filename="iosxe_config.txt",
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
        filename_keywords=["crpd"],
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
        config_extract_method="serial",
        config_extract_command="show configuration",
        config_extract_user="admin",
        config_extract_password="admin@123",
        config_extract_timeout=60,
        config_extract_prompt_pattern=r"[\w@.\-]+[>%#]\s*$",
        config_extract_paging_disable="set cli screen-length 0",
        config_inject_method="config_disk",
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
        config_extract_method="serial",
        config_extract_command="show configuration",
        config_extract_user="admin",
        config_extract_password="admin@123",
        config_extract_timeout=60,
        config_extract_prompt_pattern=r"[\w@.\-]+[>%#]\s*$",
        config_extract_paging_disable="set cli screen-length 0",
        config_inject_method="config_disk",
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
        filename_keywords=["cjunosevolved", "cjunos"],
        readiness_probe="log_pattern",
        readiness_pattern=r"EVO FLAVORS|login:",
        readiness_timeout=600,
        config_extract_method="docker",
        # The outer container's `cli` script SSHes into the inner QEMU VM but
        # doesn't pass -c args through, so we SSH directly to extract config.
        config_extract_command="ssh -o StrictHostKeyChecking=no -i /root/.ssh/sshkey root@176.1.1.1 \"cli -c 'show configuration'\"",
        config_extract_timeout=30,
        environment={
            "CPTX_COSIM": "BT|BX",
            # Required by entrypoint.sh to read /config/startup-config.cfg
            "CLAB_LABEL_CLAB_NODE_KIND": "juniper_cjunos",
        },
        capabilities=["NET_ADMIN", "SYS_ADMIN", "NET_RAW"],
        privileged=True,
        binds=[
            "/dev/kvm:/dev/kvm",
            "/dev/net/tun:/dev/net/tun",
            "{workspace}/configs/{node}/config:/config",
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
        default_image=None,
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
        supported_image_kinds=["qcow2"],
        default_credentials="admin / YourPaSsWoRd",
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
        config_extract_method="serial",
        config_extract_command="show configuration",
        config_extract_user="admin",
        config_extract_password="admin@123",
        config_extract_timeout=60,
        config_extract_prompt_pattern=r"[\w@.\-]+[>%#]\s*$",
        config_extract_paging_disable="set cli screen-length 0",
        config_inject_method="config_disk",
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
        efi_vars="stateless",  # CML reference: stateless EFI. Boot var stored on bootflash via EEM applet
        serial_port_count=2,  # CML spec: console + aux; N9Kv sysconf expects 2 serial ports
        cpu_features_disable=["smep", "smap", "pku", "umip"],  # NX-OS guest kernel lacks SMAP support → ksm_scan_thread GPF
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
        readiness_pattern=r"login:|User Access Verification|switch[^\s]*[#>]",
        readiness_timeout=600,  # N9Kv takes a long time to boot
        # Serial (virsh) console for NX-OS CLI access.
        # SSH hits the Wind Linux underlay (bash), not NX-OS.
        console_method="virsh",
        console_user="admin",
        console_password="cisco",
        # Config extraction also via serial console
        config_extract_method="serial",
        config_extract_command="show running-config",
        config_extract_user="admin",
        config_extract_password="cisco",
        config_extract_timeout=60,
        config_extract_paging_disable="terminal length 0",
        # No post-boot commands needed: ISO config handles credentials and POAP skip,
        # EEM applet handles boot variable, config save happens at extraction time.
        # Config injection: CML-style ISO (volume=disk, file=nxos_config.txt).
        # Preamble echo commands create set_boot.py on bootflash at boot time.
        config_inject_method="iso",
        config_inject_iso_volume_label="disk",
        config_inject_iso_filename="nxos_config.txt",
        default_credentials="admin / cisco",
        # Minimal default config ensures ISO is always created (preamble adds credentials + EEM)
        default_startup_config="hostname {hostname}\n",
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
        supported_image_kinds=["docker", "qcow2"],
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
        # Config injection via ISO (IOS-XE CVAC)
        config_inject_method="iso",
        config_inject_iso_filename="iosxe_config.txt",
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
        aliases=["cisco_cat9800"],
        platform="",
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
        filename_patterns=[r"C9800[_-]?CL.*\.qcow2", r"cat9800[_-]?[\d\.]+.*\.qcow2"],
        readiness_probe="log_pattern",
        readiness_pattern=r"Press RETURN to get started!",
        readiness_timeout=300,
        # Config injection via ISO (IOS-XE CVAC)
        config_inject_method="iso",
        config_inject_iso_filename="iosxe_config.txt",
    ),
    "cat9000v-q200": VendorConfig(
        kind="cisco_cat9000v_q200",
        vendor="Cisco",
        console_shell="/bin/sh",
        default_image=None,
        aliases=["cat9000v_q200", "cat9kv", "cisco_cat9kv"],
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
        filename_patterns=[r"cat9kv[_-]?prd.*\.qcow2", r"cat9kv[_-]?[\d\.]+.*\.qcow2"],
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
        # Config injection via ISO (IOS-XE CVAC)
        config_inject_method="iso",
        config_inject_iso_filename="iosxe_config.txt",
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
        # Config injection via ISO (IOS-XE CVAC)
        config_inject_method="iso",
        config_inject_iso_filename="iosxe_config.txt",
    ),
}
