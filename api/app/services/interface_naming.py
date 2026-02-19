"""Centralized interface name translation.

Single source of truth for converting between vendor-specific interface names
(e.g., Ethernet1, GigabitEthernet0/0, ge-0/0/0) and Linux-style names (eth1, eth2).

Translation is driven by the ``portNaming`` / ``portStartIndex`` fields in the
vendor catalog (``agent/vendors.py``), with overrides from custom devices and
device overrides.

Public API:
    normalize_interface(iface, device_type)   – vendor name → eth{N}
    denormalize_interface(iface, device_type)  – eth{N} → vendor name
"""
from __future__ import annotations

import re
from typing import Optional

from agent.vendors import get_config_by_device
from app.image_store import find_custom_device, get_device_override

# Docker reserves eth0 for management (default bridge network).
# OVS plugin data networks start at eth1. This offset converts between
# vendor port indices (which may start at 0) and Docker interface numbers.
DOCKER_DATA_PORT_START = 1


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_device_kind(device: str | None) -> str:
    """Resolve the canonical kind for a device, checking custom devices.

    Lookup chain:
    1. get_config_by_device(device) → VendorConfig.kind
    2. find_custom_device(device) → kind override
    3. Fall back to device string itself
    """
    if not device:
        return "linux"

    config = get_config_by_device(device)
    if config:
        return config.kind

    custom = find_custom_device(device)
    if custom and custom.get("kind"):
        return custom["kind"]

    return device


def _resolve_port_naming(device_type: str) -> tuple[str, int]:
    """Look up (port_naming, port_start_index) for a device type.

    Lookup chain:
    1. get_config_by_device(device) → VendorConfig (handles keys, kinds, aliases)
    2. find_custom_device(device) → portNaming override
    3. get_device_override(device/kind) → portNaming override
    """
    # get_config_by_device handles keys, kind lookup, and aliases
    config = get_config_by_device(device_type)
    kind = config.kind if config else _resolve_device_kind(device_type)

    port_naming = config.port_naming if config else "eth"
    port_start_index = config.port_start_index if config else 0

    custom = find_custom_device(device_type)
    if custom:
        port_naming = custom.get("portNaming", port_naming)
        port_start_index = custom.get("portStartIndex", port_start_index)

    override = get_device_override(device_type) or get_device_override(kind)
    if override:
        port_naming = override.get("portNaming", port_naming)
        port_start_index = override.get("portStartIndex", port_start_index)

    # Device-specific overrides for known alias variants that differ from
    # their canonical kind defaults.
    device_lower = (device_type or "").lower()
    if device_lower in {
        "cat9000v-q200",
        "cat9000v_q200",
        "cat9000v-uadp",
        "cat9000v_uadp",
    }:
        port_naming = "GigabitEthernet1/0/{index}"
        port_start_index = 1

    return port_naming, port_start_index


# Pre-compiled fallback patterns for when device_type is unknown.
# Order matters: most-specific patterns first to avoid false matches.
_FALLBACK_PATTERNS: list[tuple[re.Pattern, bool]] = [
    # GigabitEthernet0/0/0/{N} — Cisco IOS-XR
    (re.compile(r"^GigabitEthernet\d+/\d+/\d+/(\d+)$", re.IGNORECASE), False),
    # GigabitEthernet1/0/{N} — Cisco Cat9000v
    (re.compile(r"^GigabitEthernet\d+/\d+/(\d+)$", re.IGNORECASE), False),
    # GigabitEthernet0/{N} — Cisco IOS
    (re.compile(r"^GigabitEthernet\d+/(\d+)$", re.IGNORECASE), False),
    # GigabitEthernet{N} — Cisco generic
    (re.compile(r"^GigabitEthernet(\d+)$", re.IGNORECASE), False),
    # Ethernet1/{N} — Cisco Nexus
    (re.compile(r"^Ethernet\d+/(\d+)$", re.IGNORECASE), False),
    # ethernet-1/{N} — Nokia SR Linux
    (re.compile(r"^ethernet-\d+/(\d+)$", re.IGNORECASE), False),
    # ge-0/0/{N}, xe-0/0/{N}, or et-0/0/{N} — Juniper (0-indexed)
    (re.compile(r"^(?:[gx]e|et)-\d+/\d+/(\d+)$", re.IGNORECASE), True),
    # ge0/{N} — VyOS
    (re.compile(r"^ge\d+/(\d+)$", re.IGNORECASE), False),
    # e1-{N} — Nokia SR OS
    (re.compile(r"^e\d+-(\d+)$", re.IGNORECASE), False),
    # Ethernet{N} — Arista / SONiC
    (re.compile(r"^Ethernet(\d+)$", re.IGNORECASE), False),
    # swp{N} — Cumulus
    (re.compile(r"^swp(\d+)$", re.IGNORECASE), False),
    # port{N} — OpenWrt / FortiGate
    (re.compile(r"^port(\d+)$", re.IGNORECASE), False),
]


def _build_normalize_regex(port_naming: str) -> Optional[re.Pattern]:
    r"""Build a regex that extracts the interface index from a vendor name.

    E.g. port_naming="Ethernet" → r"^Ethernet(\d+)$"
         port_naming="ge-0/0/" → r"^ge-0/0/(\d+)$"
         port_naming="ethernet-1/{index}" → r"^ethernet-1/(\d+)$"
    """
    if port_naming == "eth":
        return None  # No conversion needed

    if "{index}" in port_naming:
        escaped = re.escape(port_naming.replace("{index}", "__PLACEHOLDER__"))
        pattern = escaped.replace("__PLACEHOLDER__", r"(\d+)")
    else:
        pattern = re.escape(port_naming) + r"(\d+)"

    return re.compile(f"^{pattern}$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def normalize_interface(iface: str, device_type: str | None = None) -> str:
    """Convert vendor interface name to Linux eth{N} format.

    Args:
        iface: Interface name (e.g., "Ethernet1", "ge-0/0/0", "eth1")
        device_type: Device type for precise translation (e.g., "arista_ceos")

    Returns:
        Normalized name (e.g., "eth1"). Returns as-is if unrecognized.
    """
    if not iface:
        return iface

    # Already in eth{N} format
    if re.match(r"^eth\d+$", iface, re.IGNORECASE):
        return iface.lower()

    # Device-aware normalization
    if device_type:
        port_naming, port_start_index = _resolve_port_naming(device_type)
        if port_naming != "eth":
            regex = _build_normalize_regex(port_naming)
            if regex:
                m = regex.match(iface)
                if m:
                    vendor_index = int(m.group(1))
                    # Convert vendor index to Docker eth index:
                    # eth1 = first data port (eth0 is Docker management).
                    # Formula: eth{vendor_index - port_start_index + 1}
                    # - cEOS (start=1): Ethernet1 → eth{1-1+1} = eth1 ✓
                    # - Juniper (start=0): et-0/0/0 → eth{0-0+1} = eth1 ✓
                    return f"eth{vendor_index - port_start_index + DOCKER_DATA_PORT_START}"

    # Fallback: try common patterns (backward compat for device_type=None)
    for pattern, is_zero_indexed in _FALLBACK_PATTERNS:
        m = pattern.match(iface)
        if m:
            idx = int(m.group(1))
            if is_zero_indexed:
                return f"eth{idx + DOCKER_DATA_PORT_START}"
            return f"eth{idx}"

    # Unrecognized — return as-is
    return iface


def denormalize_interface(iface: str, device_type: str | None = None) -> str:
    """Convert Linux eth{N} name to vendor-specific format for UI display.

    Args:
        iface: Linux interface name (e.g., "eth1")
        device_type: Device type (e.g., "arista_ceos", "nokia_srlinux")

    Returns:
        Vendor-formatted name (e.g., "Ethernet1" for cEOS). Returns as-is
        if device_type is None or interface isn't in eth{N} format.
    """
    if not device_type or not iface:
        return iface

    port_naming, port_start_index = _resolve_port_naming(device_type)

    # If the device uses 'eth' naming, no conversion needed
    if port_naming == "eth":
        return iface

    # Extract index from eth-style interface name
    m = re.match(r"^eth(\d+)$", iface, re.IGNORECASE)
    if not m:
        return iface

    eth_index = int(m.group(1))
    # Reverse the normalize formula: vendor_index = eth_index - 1 + port_start_index
    vendor_index = eth_index - DOCKER_DATA_PORT_START + port_start_index

    # Generate the vendor-specific interface name
    if "{index}" in port_naming:
        return port_naming.replace("{index}", str(vendor_index))
    else:
        return f"{port_naming}{vendor_index}"
