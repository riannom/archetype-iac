"""Custom device CRUD, hidden devices, and detection rules."""
from __future__ import annotations

import json
import logging
import re

from app.services.device_service import validate_minimum_hardware

from .aliases import (
    canonicalize_device_id,
    get_vendor_for_device,
)
from .paths import custom_devices_path, hidden_devices_path, rules_path

logger = logging.getLogger(__name__)


def load_rules() -> list[dict[str, str]]:
    path = rules_path()
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("rules", [])


def load_custom_devices() -> list[dict]:
    """Load custom device types from storage."""
    path = custom_devices_path()
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    devices = data.get("devices", [])

    # Prevent shadowing first-class vendor models with stale custom entries.
    try:
        from agent.vendors import VENDOR_CONFIGS, get_kind_for_device, _get_config_by_kind

        vendor_ids = {key.lower() for key in VENDOR_CONFIGS.keys()}
        def _shadows_vendor(device_id: str) -> bool:
            did = (device_id or "").lower()
            if did in vendor_ids:
                return True
            kind = get_kind_for_device(did)
            return _get_config_by_kind(kind) is not None

        filtered = [d for d in devices if not _shadows_vendor(d.get("id") or "")]
        shadowed = len(devices) - len(filtered)
        if shadowed:
            logger.warning("Ignoring %s custom device(s) shadowed by vendor registry", shadowed)
        return filtered
    except Exception:
        return devices


def load_hidden_devices() -> list[str]:
    """Load list of hidden device IDs."""
    path = hidden_devices_path()
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("hidden", [])


def save_hidden_devices(hidden: list[str]) -> None:
    """Save list of hidden device IDs."""
    path = hidden_devices_path()
    path.write_text(json.dumps({"hidden": hidden}, indent=2), encoding="utf-8")


def hide_device(device_id: str) -> bool:
    """Hide a device by adding it to the hidden list.

    Returns True if device was added, False if already hidden.
    """
    hidden = load_hidden_devices()
    if device_id in hidden:
        return False
    hidden.append(device_id)
    save_hidden_devices(hidden)
    return True


def unhide_device(device_id: str) -> bool:
    """Unhide a device by removing it from the hidden list.

    Returns True if device was removed, False if not in list.
    """
    hidden = load_hidden_devices()
    if device_id not in hidden:
        return False
    hidden.remove(device_id)
    save_hidden_devices(hidden)
    return True


def is_device_hidden(device_id: str) -> bool:
    """Check if a device is hidden."""
    return device_id in load_hidden_devices()


def save_custom_devices(devices: list[dict]) -> None:
    """Save custom device types to storage."""
    path = custom_devices_path()
    path.write_text(json.dumps({"devices": devices}, indent=2), encoding="utf-8")


def find_custom_device(device_id: str) -> dict | None:
    """Find a custom device type by its ID."""
    devices = load_custom_devices()
    for device in devices:
        if device.get("id") == device_id:
            return device
    return None


def add_custom_device(device: dict) -> dict:
    """Add a new custom device type.

    Args:
        device: Device configuration dict with at least 'id' and 'name' fields

    Supported fields:
        - id: Unique device identifier (required)
        - name: Display name (required)
        - type: Device type (router, switch, firewall, host, container)
        - vendor: Vendor name
        - category: UI category (Network, Security, Compute, Cloud & External)
        - icon: FontAwesome icon class
        - versions: List of version strings

        Resource properties:
        - memory: Memory requirement in MB (e.g., 2048)
        - cpu: CPU cores required (e.g., 2)
        - maxPorts: Maximum number of network interfaces
        - portNaming: Interface naming pattern (eth, Ethernet, etc.)
        - portStartIndex: Starting port number (0 or 1)

        Other properties:
        - requiresImage: Whether user must provide an image
        - supportedImageKinds: List of supported image types (docker, qcow2)
        - licenseRequired: Whether device requires commercial license
        - documentationUrl: Link to documentation
        - tags: Searchable tags

    Returns:
        The added device entry
    """
    devices = load_custom_devices()

    # Don't allow custom devices to shadow built-in vendor IDs.
    try:
        from agent.vendors import VENDOR_CONFIGS, get_kind_for_device, _get_config_by_kind

        did = (device.get("id") or "").lower()
        if did in {k.lower() for k in VENDOR_CONFIGS.keys()}:
            raise ValueError(f"Device '{device.get('id')}' already exists as a built-in vendor device")

        kind = get_kind_for_device(did)
        if _get_config_by_kind(kind) is not None:
            raise ValueError(f"Device '{device.get('id')}' already exists as a built-in vendor device")
    except ImportError:
        pass

    # Check for duplicate
    for existing in devices:
        if existing.get("id") == device.get("id"):
            raise ValueError(f"Device '{device.get('id')}' already exists")

    # Add default fields if not present - UI metadata
    device.setdefault("type", "container")
    device.setdefault("vendor", "Custom")
    device.setdefault("icon", "fa-box")
    device.setdefault("versions", ["latest"])
    device.setdefault("isActive", True)
    device.setdefault("category", "Compute")
    device.setdefault("isCustom", True)  # Mark as custom device

    # Resource properties defaults
    device.setdefault("memory", 1024)  # 1GB default
    device.setdefault("cpu", 1)  # 1 CPU core default
    device.setdefault("maxPorts", 8)  # 8 interfaces default
    device.setdefault("portNaming", "eth")
    device.setdefault("portStartIndex", 0)

    # Other property defaults
    device.setdefault("requiresImage", True)
    device.setdefault("supportedImageKinds", ["docker"])
    device.setdefault("licenseRequired", False)
    device.setdefault("documentationUrl", None)
    device.setdefault("tags", [])

    validate_minimum_hardware(
        device.get("id"),
        device.get("memory"),
        device.get("cpu"),
    )

    devices.append(device)
    save_custom_devices(devices)
    return device


def update_custom_device(device_id: str, updates: dict) -> dict | None:
    """Update an existing custom device type.

    Args:
        device_id: ID of the device to update
        updates: Dictionary of fields to update

    Returns:
        Updated device entry or None if not found
    """
    devices = load_custom_devices()
    for device in devices:
        if device.get("id") == device_id:
            merged = {**device, **updates}
            validate_minimum_hardware(
                device_id,
                merged.get("memory"),
                merged.get("cpu"),
            )
            # Don't allow changing the ID or isCustom flag
            updates.pop("id", None)
            updates.pop("isCustom", None)
            device.update(updates)
            save_custom_devices(devices)
            return device
    return None


def delete_custom_device(device_id: str) -> dict | None:
    """Delete a custom device type by its ID.

    Returns:
        The deleted device or None if not found
    """
    devices = load_custom_devices()
    for i, device in enumerate(devices):
        if device.get("id") == device_id:
            deleted = devices.pop(i)
            save_custom_devices(devices)
            return deleted
    return None


def _infer_dynamic_custom_device_metadata(device_id: str) -> tuple[str, str, str, str | None]:
    """Infer basic UI metadata for dynamically created custom devices."""
    normalized = (device_id or "").strip().lower()
    if not normalized:
        return "container", "fa-box", "Compute", None

    if any(token in normalized for token in ("firewall", "ftd", "asav", "panos", "forti")):
        return "firewall", "fa-shield-halved", "Security", None
    if any(token in normalized for token in ("switch", "qfx", "nxos", "n9k", "cat9k")):
        return "switch", "fa-arrows-left-right-to-line", "Network", "Switches"
    if any(token in normalized for token in ("router", "csr", "ios", "xrv", "xrd", "junos", "sdwan", "vedge")):
        return "router", "fa-arrows-to-dot", "Network", "Routers"
    if any(token in normalized for token in ("host", "server", "windows")):
        return "host", "fa-server", "Compute", None
    return "container", "fa-box", "Compute", None


def _display_name_from_device_id(device_id: str) -> str:
    """Generate a readable label from a machine-style device ID."""
    pretty = re.sub(r"[_\-]+", " ", (device_id or "").strip())
    pretty = re.sub(r"\s+", " ", pretty).strip()
    return pretty.title() if pretty else "Custom Device"


def ensure_custom_device_exists(
    device_id: str,
    preferred_image_kind: str | None = None,
) -> dict | None:
    """Ensure a custom device entry exists for a device_id.

    If the device_id doesn't exist as a vendor config or custom device,
    create a custom device entry based on either:
    1) canonical vendor config (resolved via alias), or
    2) inferred generic metadata for unknown device families.

    This is called during image import to ensure that device_ids like "eos"
    get proper custom device entries with portNaming, etc.

    Args:
        device_id: Device ID to ensure exists (e.g., "eos")
        preferred_image_kind: Image kind that triggered creation (docker/qcow2/iol)

    Returns:
        The existing or newly created custom device.
    """
    from agent.vendors import VENDOR_CONFIGS, get_kind_for_device

    if not device_id:
        return None

    # Resolve the full alias chain to a canonical VENDOR_CONFIGS key.
    canonical_key = canonicalize_device_id(device_id)

    # If the canonical ID maps to a built-in vendor config, no custom device needed.
    if canonical_key and canonical_key in VENDOR_CONFIGS:
        return None

    # Check if it already exists as a vendor config by key
    if device_id in VENDOR_CONFIGS:
        return None  # Built-in, no custom device needed

    # Check if it already exists as a custom device
    existing = find_custom_device(device_id)
    if existing:
        return existing

    # Resolve alias to canonical vendor ID (e.g., "eos" -> "ceos")
    canonical_id = get_kind_for_device(device_id)

    if canonical_id in VENDOR_CONFIGS:
        # Get the canonical vendor config.
        config = VENDOR_CONFIGS[canonical_id]

        # Create custom device entry with properties from the vendor config.
        custom_device = {
            "id": device_id,
            "name": config.label or f"{config.vendor} ({device_id})",
            "type": config.device_type.value,
            "vendor": config.vendor,
            "icon": config.icon,
            "versions": config.versions.copy() if config.versions else ["latest"],
            "isActive": config.is_active,
            "category": config.category,
            "subcategory": config.subcategory,
            "portNaming": config.port_naming,
            "portStartIndex": config.port_start_index,
            "maxPorts": config.max_ports,
            "memory": config.memory,
            "cpu": config.cpu,
            "requiresImage": config.requires_image,
            "supportedImageKinds": config.supported_image_kinds.copy() if config.supported_image_kinds else ["docker"],
            "documentationUrl": config.documentation_url,
            "licenseRequired": config.license_required,
            "tags": config.tags.copy() if config.tags else [],
            "kind": canonical_id,  # Reference to the canonical vendor for runtime config
            "consoleShell": config.console_shell,
            "isCustom": True,
        }
        return add_custom_device(custom_device)

    # Unknown device family: create an inferred custom profile so uploads remain usable.
    dev_type, icon, category, subcategory = _infer_dynamic_custom_device_metadata(device_id)
    image_kinds = ["docker"]
    if preferred_image_kind and preferred_image_kind not in image_kinds:
        image_kinds = [preferred_image_kind]

    default_memory = 2048 if dev_type in {"router", "switch", "firewall", "host"} else 1024
    default_cpu = 2 if dev_type in {"router", "switch", "firewall", "host"} else 1

    dynamic_device = {
        "id": device_id,
        "name": _display_name_from_device_id(device_id),
        "type": dev_type,
        "vendor": get_vendor_for_device(device_id) or "Custom",
        "icon": icon,
        "versions": ["latest"],
        "isActive": True,
        "category": category,
        "subcategory": subcategory,
        "portNaming": "eth",
        "portStartIndex": 0,
        "maxPorts": 12 if dev_type in {"router", "switch", "firewall"} else 8,
        "memory": default_memory,
        "cpu": default_cpu,
        "requiresImage": True,
        "supportedImageKinds": image_kinds,
        "documentationUrl": None,
        "licenseRequired": False,
        "tags": [dev_type, "auto-generated"],
        "isCustom": True,
    }
    return add_custom_device(dynamic_device)


def cleanup_orphaned_custom_devices() -> list[str]:
    """Remove custom devices that no image references.

    Scans custom devices marked isCustom=True and removes any that have no
    matching images in the manifest. Returns list of removed device IDs.
    """
    from .manifest import load_manifest
    from .aliases import image_matches_device

    manifest = load_manifest()
    devices = load_custom_devices()
    removed: list[str] = []

    for device in devices:
        device_id = device.get("id")
        if not device.get("isCustom"):
            continue
        if not device_id:
            continue
        # Check if any image references this device.
        has_image = any(
            image_matches_device(img, device_id)
            for img in manifest.get("images", [])
            if isinstance(img, dict)
        )
        if not has_image:
            deleted = delete_custom_device(device_id)
            if deleted:
                removed.append(device_id)
                logger.info("Removed orphaned custom device: %s", device_id)

    return removed
