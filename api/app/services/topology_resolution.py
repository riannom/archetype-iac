"""Topology resolution helpers - pure functions, no DB dependency.

These functions resolve device kinds, images, and effective port counts
using the vendor config registry, image manifest, and device overrides.
They are used by TopologyService and other callers throughout the codebase.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from agent.vendors import get_default_image, get_config_by_device
from app.image_store import find_image_reference, find_custom_device, get_device_override
from app.schemas import CrossHostLink

logger = logging.getLogger(__name__)


def resolve_node_image(
    device: str | None,
    kind: str,
    explicit_image: str | None = None,
    version: str | None = None,
) -> str | None:
    """Resolve the Docker image for a node using 3-step fallback.

    This is the canonical image resolution logic used throughout the codebase.
    Priority:
    1. Explicit image if specified (node.image)
    2. Image from manifest via find_image_reference() (uploaded images)
    3. Vendor default via get_default_image()

    Args:
        device: Device type (e.g., "ceos", "nokia_srlinux") for manifest lookup
        kind: Resolved kind (e.g., "ceos") for vendor default lookup
        explicit_image: Explicitly specified image (highest priority)
        version: Optional version for manifest lookup

    Returns:
        Resolved image reference or None if no image found
    """
    if explicit_image:
        return explicit_image

    # Try to find uploaded image for this device type and version
    image = find_image_reference(device or kind, version)
    if image:
        return image

    # Fall back to vendor default image
    return get_default_image(kind)


def resolve_device_kind(device: str | None) -> str:
    """Resolve the canonical kind for a device, checking custom devices.

    Priority:
    1. If device matches a vendor config, use vendor's kind
    2. If device is a custom device, use custom device's kind field
    3. Fall back to the device ID itself (or "linux" if None)

    Args:
        device: Device type (e.g., "eos", "ceos", custom device ID)

    Returns:
        The canonical kind (e.g., "ceos" for EOS devices)
    """
    if not device:
        return "linux"

    # First check if vendor config knows this device
    config = get_config_by_device(device)
    if config:
        return config.kind

    # Check custom devices for a kind override
    custom = find_custom_device(device)
    if custom and custom.get("kind"):
        return custom["kind"]

    # Fall back to the device ID itself
    return device


def resolve_effective_max_ports(
    device_id: str | None,
    kind: str | None,
    image_reference: str | None = None,
    version: str | None = None,
) -> int:
    """Resolve effective maxPorts including catalog, custom, and override layers."""
    try:
        from app.services.device_service import get_device_service

        resolved = get_device_service().resolve_hardware_specs(
            device_id or kind or "linux",
            None,
            image_reference,
            version=version,
        )
        resolved_ports = resolved.get("max_ports")
        if resolved_ports is not None:
            return int(resolved_ports)
    except Exception:
        pass

    base_ports: int | None = None

    if device_id:
        config = get_config_by_device(device_id)
        if config:
            base_ports = config.max_ports
        else:
            custom = find_custom_device(device_id)
            if custom:
                base_ports = custom.get("maxPorts")

    if base_ports is None and kind:
        config = get_config_by_device(kind)
        if config:
            base_ports = config.max_ports

    override = None
    if device_id:
        override = get_device_override(device_id)
    if not override and kind and kind != device_id:
        override = get_device_override(kind)
    if override and "maxPorts" in override:
        base_ports = override["maxPorts"]

    return int(base_ports or 0)


@dataclass
class NodePlacementInfo:
    """Placement of a node on a specific host."""
    node_name: str
    host_id: str
    node_id: str | None = None  # DB Node.id


@dataclass
class TopologyAnalysisResult:
    """Analysis of a topology for multi-host deployment."""
    placements: dict[str, list[NodePlacementInfo]]  # host_id -> nodes
    cross_host_links: list[CrossHostLink]
    single_host: bool
