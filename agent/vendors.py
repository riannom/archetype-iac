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

Architecture:
  vendor_schema.py   - Dataclass/enum definitions (DeviceType, VendorConfig, sub-configs)
  vendor_registry.py - VENDOR_CONFIGS dict literal (the ~80-device data blob)
  vendors.py (this)  - Builder/accessor functions, runtime configs, derived maps
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging

# Re-export schema types so existing `from agent.vendors import ...` keeps working.
from agent.vendor_schema import (  # noqa: F401
    ContainerConfig,
    ConfigExtractionConfig,
    ConfigInjectionConfig,
    ConsoleConfig,
    DeviceType,
    InterfaceConfig,
    ReadinessConfig,
    ResourceConfig,
    UIConfig,
    VendorConfig,
    VMConfig,
)

# Re-export the registry dict.
from agent.vendor_registry import VENDOR_CONFIGS  # noqa: F401

logger = logging.getLogger(__name__)


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
        if not cfg.filename_patterns:
            continue
        for pattern in cfg.filename_patterns:
            patterns[pattern] = (key, cfg.vrnetlab_subdir or "")
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


def get_default_image(kind: str) -> str | None:
    """Get the default Docker image for a device kind."""
    config = _get_config_by_kind(kind)
    if config:
        return config.default_image
    return None


def get_vendor_config(kind: str) -> VendorConfig | None:
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


def is_cjunos_kind(kind: str) -> bool:
    """Check if a device kind is cJunOS (Juniper containerized Junos Evolved).

    Args:
        kind: Device kind to check

    Returns:
        True if the kind is cJunOS or one of its aliases
    """
    canonical = get_kind_for_device(kind)
    return canonical == "juniper_cjunos"


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
    cpu_features_disable: list[str] = field(default_factory=list)  # CPU features to disable
    config_inject_method: str = "none"    # "none", "bootflash", "iso", or "config_disk"
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
        cpu_features_disable=list(config.cpu_features_disable),
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
        # Try alias lookup (e.g., "cjunos" -> "juniper_cjunos")
        canonical_kind = get_kind_for_device(kind)
        config = _get_config_by_kind(canonical_kind)
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
            # Define preferred subcategory order; any unlisted ones appended
            subcat_order = ["Routers", "Switches", "Wireless", "Load Balancers"]
            remaining = [k for k in subcats if k != "_direct" and k not in subcat_order]
            for subcat in subcat_order + remaining + ["_direct"]:
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
