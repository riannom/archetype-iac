"""Device type mapping for ISO imports.

Maps parsed node definitions to existing vendor registry entries
or creates new custom device types.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from app.iso.models import ParsedNodeDefinition, ParsedImage

logger = logging.getLogger(__name__)

# Mapping from VIRL2 node definition IDs to existing vendor registry device IDs
VIRL2_TO_VENDOR_MAP = {
    # SD-WAN
    "cat-sdwan-edge": "c8000v",
    "cat-sdwan-controller": "cat-sdwan-controller",
    "cat-sdwan-manager": "cat-sdwan-manager",
    "cat-sdwan-validator": "cat-sdwan-validator",
    "cat-sdwan-vedge": "cat-sdwan-vedge",
    # Security
    "ftdv": "ftdv",
    "fmcv": "fmcv",
    "asav": "cisco_asav",
    # Wireless
    "cat9800": "cat9800",
    # Routers
    "iosv": "cisco_iosv",
    "iosvl2": "iosvl2",
    "csr1000v": "cisco_csr1000v",
    "iosxrv9000": "cisco_iosxr",
    "nxos": "cisco_n9kv",
    # Linux/Containers
    "alpine": "linux",
    "ubuntu": "linux",
    "server": "linux",
}

# Icon mapping based on device nature
NATURE_TO_ICON = {
    "router": "fa-arrows-to-dot",
    "switch": "fa-arrows-left-right-to-line",
    "firewall": "fa-shield-halved",
    "server": "fa-server",
    "wireless": "fa-wifi",
    "container": "fa-box",
}

# Category mapping based on device nature
NATURE_TO_CATEGORY = {
    "router": ("Network", "Routers"),
    "switch": ("Network", "Switches"),
    "firewall": ("Security", None),
    "server": ("Compute", None),
    "wireless": ("Network", "Wireless"),
    "container": ("Compute", None),
}


def map_node_definition_to_device(node_def: ParsedNodeDefinition) -> str | None:
    """Map a parsed node definition to an existing device ID.

    Args:
        node_def: Parsed node definition from ISO

    Returns:
        Device ID from vendor registry, or None if no match
    """
    # Try direct mapping first
    if node_def.id in VIRL2_TO_VENDOR_MAP:
        return VIRL2_TO_VENDOR_MAP[node_def.id]

    # Try normalized ID (lowercase, hyphen-separated)
    normalized = node_def.id.lower().replace("_", "-")
    if normalized in VIRL2_TO_VENDOR_MAP:
        return VIRL2_TO_VENDOR_MAP[normalized]

    # Try vendor registry lookup
    try:
        from agent.vendors import VENDOR_CONFIGS, _ALIAS_TO_KIND

        # Check if ID matches a vendor config key or alias
        if node_def.id in VENDOR_CONFIGS:
            return node_def.id
        if node_def.id.lower() in _ALIAS_TO_KIND:
            return _ALIAS_TO_KIND[node_def.id.lower()]
    except ImportError:
        pass

    return None


def create_device_config_from_node_def(node_def: ParsedNodeDefinition) -> dict:
    """Create a custom device configuration from a node definition.

    Args:
        node_def: Parsed node definition

    Returns:
        Device configuration dict suitable for add_custom_device()
    """
    # Determine category and subcategory
    category, subcategory = NATURE_TO_CATEGORY.get(
        node_def.nature, ("Compute", None)
    )

    # Determine icon
    icon = NATURE_TO_ICON.get(node_def.nature, "fa-box")

    # Extract port naming pattern and start index
    port_naming = node_def.interface_naming_pattern
    port_start_index = node_def.port_start_index

    # Determine supported image kinds
    supported_kinds = ["qcow2"]  # Default for VMs

    # Build the device config
    config = {
        "id": node_def.id,
        "name": node_def.label,
        "type": node_def.nature,
        "vendor": node_def.vendor or "Cisco",
        "category": category,
        "icon": icon,
        "versions": [],  # Will be populated from images
        "isActive": True,
        # Resource properties
        "memory": node_def.ram_mb,
        "cpu": node_def.cpus,
        "maxPorts": len(node_def.interfaces) or node_def.interface_count_default,
        "portNaming": port_naming,
        "portStartIndex": port_start_index,
        # Image properties
        "requiresImage": True,
        "supportedImageKinds": supported_kinds,
        "licenseRequired": True,  # Most vendor images require license
        "documentationUrl": None,
        "tags": _generate_tags(node_def),
        # Boot properties
        "readinessProbe": "log_pattern" if node_def.boot_completed_patterns else "none",
        "readinessPattern": "|".join(re.escape(p) for p in node_def.boot_completed_patterns) if node_def.boot_completed_patterns else None,
        "readinessTimeout": node_def.boot_timeout,
        # VM-specific
        "libvirtDriver": node_def.libvirt_driver,
        "diskDriver": node_def.disk_driver,
        "nicDriver": node_def.nic_driver,
        # Mark as imported from ISO
        "importedFromISO": True,
        "isoNodeDefinitionId": node_def.id,
    }

    return config


def _generate_tags(node_def: ParsedNodeDefinition) -> list[str]:
    """Generate searchable tags from node definition."""
    tags = []

    # Nature-based tags
    tags.append(node_def.nature)

    # Vendor-based tags
    if node_def.vendor:
        tags.append(node_def.vendor.lower())

    # Feature-based tags from description
    description_lower = node_def.description.lower()
    feature_keywords = [
        "sd-wan", "sdwan", "vpn", "firewall", "security",
        "routing", "switching", "wireless", "controller",
        "manager", "validator", "edge",
    ]
    for keyword in feature_keywords:
        if keyword in description_lower or keyword in node_def.id.lower():
            tags.append(keyword.replace("-", ""))

    return list(set(tags))


def get_image_device_mapping(
    image: ParsedImage,
    node_definitions: list[ParsedNodeDefinition],
) -> tuple[str, dict | None]:
    """Get device mapping for an image.

    Args:
        image: Parsed image
        node_definitions: List of node definitions from the ISO

    Returns:
        Tuple of (device_id, new_device_config or None)
        - device_id: Existing or new device ID
        - new_device_config: Config dict if new device needs to be created, None if using existing
    """
    # Find the node definition for this image
    node_def = next(
        (n for n in node_definitions if n.id == image.node_definition_id),
        None
    )

    if not node_def:
        # No node definition found, use generic mapping
        return image.node_definition_id, None

    # Try to map to existing device
    existing_device = map_node_definition_to_device(node_def)
    if existing_device:
        return existing_device, None

    # Need to create new device
    new_config = create_device_config_from_node_def(node_def)
    return node_def.id, new_config
