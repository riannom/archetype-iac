from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import re
from typing import Optional

from app.config import settings
from app.services.device_constraints import validate_minimum_hardware


# =============================================================================
# QCOW2 DEVICE DETECTION FOR VRNETLAB BUILDS
# =============================================================================

# Mapping of filename patterns to (device_id, vrnetlab_subdir)
# Used to detect device type from qcow2 filename and determine vrnetlab build path
QCOW2_DEVICE_PATTERNS: dict[str, tuple[str, str]] = {
    # Cisco IOS-XE / Catalyst
    r"c8000v[_-]?[\d\.]+.*\.qcow2": ("c8000v", "cisco/c8000v"),
    r"cat9kv[_-]?[\d\.]+.*\.qcow2": ("cat9kv", "cisco/cat9kv"),
    r"cat8000v[_-]?[\d\.]+.*\.qcow2": ("c8000v", "cisco/c8000v"),
    r"csr1000v[_-]?[\d\.]+.*\.qcow2": ("csr1000v", "cisco/csr"),
    # Cisco Firewall / FTD
    r"ftdv[_-]?[\d\.]+.*\.qcow2": ("ftdv", "cisco/ftdv"),
    r"cisco[_-]?secure[_-]?firewall[_-]?threat[_-]?defense.*\.qcow2": ("ftdv", "cisco/ftdv"),
    r"asav[_-]?[\d\.]+.*\.qcow2": ("asav", "cisco/asav"),
    # Cisco IOS-XR
    r"xrv9k[_-]?[\d\.]+.*\.qcow2": ("xrv9k", "cisco/xrv9k"),
    r"iosxrv9000[_-]?[\d\.]+.*\.qcow2": ("xrv9k", "cisco/xrv9k"),
    r"xrd[_-]?[\d\.]+.*\.qcow2": ("xrd", "cisco/xrd"),
    # Cisco NX-OS
    r"n9kv[_-]?[\d\.]+.*\.qcow2": ("n9kv", "cisco/n9kv"),
    r"nexus9[_-]?[\d\.]+.*\.qcow2": ("n9kv", "cisco/n9kv"),
    r"nxosv[_-]?[\d\.]+.*\.qcow2": ("n9kv", "cisco/n9kv"),
    # Cisco IOSv / IOS
    r"vios[_-]?[\d\.]+.*\.qcow2": ("iosv", "cisco/iosv"),
    r"iosv[_-]?[\d\.]+.*\.qcow2": ("iosv", "cisco/iosv"),
    r"iosvl2[_-]?[\d\.]+.*\.qcow2": ("iosvl2", "cisco/iosvl2"),
    # Cisco SD-WAN components
    r"viptela[_-]?smart.*\.qcow2": ("cat-sdwan-controller", "cisco/sdwan"),
    r"viptela[_-]?vmanage.*\.qcow2": ("cat-sdwan-manager", "cisco/sdwan"),
    r"viptela[_-]?bond.*\.qcow2": ("cat-sdwan-validator", "cisco/sdwan"),
    r"viptela[_-]?edge.*\.qcow2": ("cat-sdwan-vedge", "cisco/sdwan"),
    r"vedge[_-]?[\d\.]+.*\.qcow2": ("cat-sdwan-vedge", "cisco/sdwan"),
    r"c8000v[_-]?sdwan.*\.qcow2": ("cat-sdwan-cedge", "cisco/sdwan"),
    # Juniper
    r"vsrx[_-]?[\d\.]+.*\.qcow2": ("vsrx", "juniper/vsrx"),
    r"vjunos[_-]?[\d\.]+.*\.qcow2": ("vjunos-switch", "juniper/vjunos-switch"),
    r"vmx[_-]?[\d\.]+.*\.qcow2": ("vmx", "juniper/vmx"),
    r"vqfx[_-]?[\d\.]+.*\.qcow2": ("vqfx", "juniper/vqfx"),
    # Arista
    r"veos[_-]?[\d\.]+.*\.qcow2": ("veos", "arista/veos"),
    # Nokia
    r"sros[_-]?[\d\.]+.*\.qcow2": ("sros", "nokia/sros"),
    # Palo Alto
    r"pa[_-]?vm[_-]?[\d\.]+.*\.qcow2": ("panos", "paloalto/panos"),
    # Generic / Catch-all for common formats
    r".*\.qcow2": (None, None),  # Unknown device
}


def detect_qcow2_device_type(filename: str) -> tuple[str | None, str | None]:
    """Detect device type and vrnetlab path from qcow2 filename.

    Args:
        filename: The qcow2 filename (e.g., "c8000v-17.16.01a.qcow2")

    Returns:
        Tuple of (device_id, vrnetlab_path) or (None, None) if unknown.
        device_id: The device type identifier (e.g., "c8000v")
        vrnetlab_path: The vrnetlab subdirectory to use (e.g., "cisco/c8000v")
    """
    filename_lower = filename.lower()
    for pattern, (device_id, vrnetlab_path) in QCOW2_DEVICE_PATTERNS.items():
        if device_id is None:  # Skip the catch-all pattern
            continue
        if re.search(pattern, filename_lower, re.IGNORECASE):
            return device_id, vrnetlab_path
    return None, None


# Vendor mapping for detected devices
DEVICE_VENDOR_MAP = {
    "eos": "Arista",
    "ceos": "Arista",
    "arista_ceos": "Arista",
    "arista_eos": "Arista",
    "iosv": "Cisco",
    "iosxr": "Cisco",
    "csr": "Cisco",
    "nxos": "Cisco",
    "iosvl2": "Cisco",
    "xrd": "Cisco",
    "vsrx": "Juniper",
    "crpd": "Juniper",
    "vjunos": "Juniper",
    "vqfx": "Juniper",
    "srlinux": "Nokia",
    "cumulus": "NVIDIA",
    "sonic": "SONiC",
    "vyos": "VyOS",
    "frr": "Open Source",
    "linux": "Open Source",
    "alpine": "Open Source",
}

# Legacy/simplified IDs seen in filenames/manifests that should map to canonical
# vendor IDs returned by /vendors and used by the UI catalog.
DEVICE_ID_ALIASES = {
    "iosv": "cisco_iosv",
    "ceos": "eos",
}


def canonicalize_device_id(device_id: str | None) -> str | None:
    """Normalize a device ID to the canonical vendor/device key."""
    if not device_id:
        return None

    normalized = device_id.strip().lower()
    normalized = DEVICE_ID_ALIASES.get(normalized, normalized)

    # Also let vendor alias resolution handle known aliases.
    try:
        from agent.vendors import get_kind_for_device
        return get_kind_for_device(normalized)
    except Exception:
        return normalized


def canonicalize_device_ids(device_ids: list[str] | None) -> list[str]:
    """Normalize and deduplicate a list of device IDs."""
    if not device_ids:
        return []

    result: list[str] = []
    seen: set[str] = set()
    for device_id in device_ids:
        canonical = canonicalize_device_id(device_id)
        if not canonical or canonical in seen:
            continue
        seen.add(canonical)
        result.append(canonical)
    return result


def image_store_root() -> Path:
    if settings.qcow2_store:
        return Path(settings.qcow2_store)
    return Path(settings.workspace) / "images"


def ensure_image_store() -> Path:
    path = image_store_root()
    path.mkdir(parents=True, exist_ok=True)
    return path


def qcow2_path(filename: str) -> Path:
    return ensure_image_store() / filename


def manifest_path() -> Path:
    return ensure_image_store() / "manifest.json"


def load_manifest() -> dict:
    path = manifest_path()
    if not path.exists():
        return {"images": []}
    manifest = json.loads(path.read_text(encoding="utf-8"))

    # Normalize legacy device IDs in-memory so callers always see canonical IDs.
    for image in manifest.get("images", []):
        if not isinstance(image, dict):
            continue

        canonical_device_id = canonicalize_device_id(image.get("device_id"))
        compatible_devices = canonicalize_device_ids(image.get("compatible_devices") or [])
        if canonical_device_id and canonical_device_id not in compatible_devices:
            compatible_devices.append(canonical_device_id)

        image["device_id"] = canonical_device_id
        image["compatible_devices"] = compatible_devices
        if canonical_device_id:
            image["vendor"] = get_vendor_for_device(canonical_device_id)

    return manifest


def save_manifest(data: dict) -> None:
    path = manifest_path()
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def rules_path() -> Path:
    return ensure_image_store() / "rules.json"


def load_rules() -> list[dict[str, str]]:
    path = rules_path()
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("rules", [])


# =============================================================================
# CUSTOM DEVICE TYPES
# =============================================================================

def custom_devices_path() -> Path:
    """Path to the custom device types JSON file."""
    return ensure_image_store() / "custom_devices.json"


def hidden_devices_path() -> Path:
    """Path to the hidden devices JSON file."""
    return ensure_image_store() / "hidden_devices.json"


def load_custom_devices() -> list[dict]:
    """Load custom device types from storage."""
    path = custom_devices_path()
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("devices", [])


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


def find_custom_device(device_id: str) -> Optional[dict]:
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


def update_custom_device(device_id: str, updates: dict) -> Optional[dict]:
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


def delete_custom_device(device_id: str) -> Optional[dict]:
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


def ensure_custom_device_exists(device_id: str) -> Optional[dict]:
    """Ensure a custom device entry exists for a device_id.

    If the device_id doesn't exist as a vendor config or custom device,
    create a custom device entry based on the canonical vendor config
    (resolved via alias).

    This is called during image import to ensure that device_ids like "eos"
    get proper custom device entries with portNaming, etc.

    Args:
        device_id: Device ID to ensure exists (e.g., "eos")

    Returns:
        The existing or newly created custom device, or None if no
        canonical vendor config exists
    """
    from agent.vendors import VENDOR_CONFIGS, get_kind_for_device

    if not device_id:
        return None

    # Check if it already exists as a vendor config
    if device_id in VENDOR_CONFIGS:
        return None  # Built-in, no custom device needed

    # Check if it already exists as a custom device
    existing = find_custom_device(device_id)
    if existing:
        return existing

    # Resolve alias to canonical vendor ID (e.g., "eos" -> "ceos")
    canonical_id = get_kind_for_device(device_id)

    # If canonical is same as device_id and not in VENDOR_CONFIGS, no base config
    if canonical_id not in VENDOR_CONFIGS:
        return None

    # Get the canonical vendor config
    config = VENDOR_CONFIGS[canonical_id]

    # Create custom device entry with properties from the vendor config
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


def image_matches_device(image: dict, device_id: str) -> bool:
    """Check if an image matches a device via device_id or compatible_devices.

    Device IDs are normalized to canonical IDs before comparison.
    """
    target = canonicalize_device_id(device_id)
    if not target:
        return False

    if canonicalize_device_id(image.get("device_id") or "") == target:
        return True
    for cd in image.get("compatible_devices") or []:
        if canonicalize_device_id(cd) == target:
            return True
    return False


def get_device_image_count(device_id: str) -> int:
    """Count how many images are assigned to a device type.

    Checks both 'device_id' field and 'compatible_devices' list.
    """
    manifest = load_manifest()
    return sum(1 for img in manifest.get("images", [])
               if image_matches_device(img, device_id))


def detect_device_from_filename(filename: str) -> tuple[str | None, str | None]:
    name = filename.lower()
    for rule in load_rules():
        pattern = rule.get("pattern")
        device_id = rule.get("device_id")
        if not pattern or not device_id:
            continue
        if re.search(pattern, name):
            return device_id, _extract_version(filename)
    keyword_map = {
        "ceos": "ceos",
        "eos": "ceos",
        "iosv": "iosv",
        "csr": "csr",
        "nxos": "nxos",
        "viosl2": "iosvl2",
        "iosvl2": "iosvl2",
        "iosxr": "iosxr",
    }
    for keyword, device_id in keyword_map.items():
        if keyword in name:
            return device_id, _extract_version(filename)
    return None, _extract_version(filename)


def _extract_version(filename: str) -> str | None:
    match = re.search(r"(\d+(?:\.\d+){1,3}[A-Za-z0-9]*)", filename)
    return match.group(1) if match else None


def get_vendor_for_device(device_id: str) -> Optional[str]:
    """Get the vendor name for a device ID."""
    if not device_id:
        return None
    device_lower = device_id.lower()
    return DEVICE_VENDOR_MAP.get(device_lower)


def create_image_entry(
    image_id: str,
    kind: str,
    reference: str,
    filename: str,
    device_id: Optional[str] = None,
    version: Optional[str] = None,
    size_bytes: Optional[int] = None,
    notes: str = "",
    compatible_devices: Optional[list[str]] = None,
    source: Optional[str] = None,
) -> dict:
    """Create a new image library entry with all metadata fields.

    Args:
        image_id: Unique identifier (e.g., "docker:ceos:4.28.0F")
        kind: Image type ("docker" or "qcow2")
        reference: Docker image reference or file path
        filename: Original filename
        device_id: Assigned device type (e.g., "eos")
        version: Version string (e.g., "4.28.0F")
        size_bytes: File size in bytes
        notes: User notes about the image
        compatible_devices: List of device IDs this image works with

    Returns:
        Dictionary with all image metadata fields
    """
    canonical_device_id = canonicalize_device_id(device_id)
    normalized_compatible_devices = canonicalize_device_ids(compatible_devices)
    if canonical_device_id and canonical_device_id not in normalized_compatible_devices:
        normalized_compatible_devices.append(canonical_device_id)

    vendor = get_vendor_for_device(canonical_device_id) if canonical_device_id else None

    # Ensure custom device entry exists for this device_id
    # This creates entries like "eos" with proper portNaming from the canonical "ceos" config
    if canonical_device_id:
        ensure_custom_device_exists(canonical_device_id)

    return {
        "id": image_id,
        "kind": kind,
        "reference": reference,
        "filename": filename,
        "device_id": canonical_device_id,
        "version": version,
        # New fields
        "vendor": vendor,
        "uploaded_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "size_bytes": size_bytes,
        "is_default": False,
        "notes": notes,
        "compatible_devices": normalized_compatible_devices,
        "source": source,
    }


def update_image_entry(
    manifest: dict,
    image_id: str,
    updates: dict,
) -> Optional[dict]:
    """Update an existing image entry with new values.

    Args:
        manifest: The manifest dictionary
        image_id: ID of the image to update
        updates: Dictionary of fields to update

    Returns:
        Updated image entry or None if not found
    """
    for item in manifest.get("images", []):
        if item.get("id") == image_id:
            # Update vendor if device_id is being changed
            if "device_id" in updates:
                updates["device_id"] = canonicalize_device_id(updates["device_id"])
                updates["vendor"] = get_vendor_for_device(updates["device_id"])

            if "compatible_devices" in updates:
                updates["compatible_devices"] = canonicalize_device_ids(updates["compatible_devices"])

            # Ensure device_id is included in compatible_devices when assigned.
            if updates.get("device_id"):
                compatible = updates.get("compatible_devices")
                if compatible is None:
                    compatible = canonicalize_device_ids(item.get("compatible_devices") or [])
                if updates["device_id"] not in compatible:
                    compatible.append(updates["device_id"])
                updates["compatible_devices"] = compatible

            # Handle is_default - if setting as default, unset other defaults for same device
            if updates.get("is_default") and updates.get("device_id"):
                device_id = updates.get("device_id") or item.get("device_id")
                for other in manifest.get("images", []):
                    if other.get("device_id") == device_id and other.get("id") != image_id:
                        other["is_default"] = False

            item.update(updates)
            return item
    return None


def find_image_by_id(manifest: dict, image_id: str) -> Optional[dict]:
    """Find an image entry by its ID."""
    for item in manifest.get("images", []):
        if item.get("id") == image_id:
            return item
    return None


def find_image_by_reference(manifest: dict, reference: str) -> Optional[dict]:
    """Find an image entry by its Docker reference or file path."""
    for item in manifest.get("images", []):
        if item.get("reference") == reference:
            return item
    return None


def delete_image_entry(manifest: dict, image_id: str) -> Optional[dict]:
    """Delete an image entry from the manifest by its ID.

    Args:
        manifest: The manifest dictionary
        image_id: ID of the image to delete

    Returns:
        The deleted image entry or None if not found
    """
    images = manifest.get("images", [])
    for i, item in enumerate(images):
        if item.get("id") == image_id:
            return images.pop(i)
    return None


# =============================================================================
# DEVICE CONFIGURATION OVERRIDES
# =============================================================================

def device_overrides_path() -> Path:
    """Path to the device configuration overrides JSON file."""
    return ensure_image_store() / "device_overrides.json"


def load_device_overrides() -> dict[str, dict]:
    """Load all device configuration overrides from storage."""
    path = device_overrides_path()
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("overrides", {})


def save_device_overrides(overrides: dict[str, dict]) -> None:
    """Save device configuration overrides to storage."""
    path = device_overrides_path()
    path.write_text(json.dumps({"overrides": overrides}, indent=2), encoding="utf-8")


def get_device_override(device_id: str) -> Optional[dict]:
    """Get configuration override for a specific device.

    Returns:
        Override dictionary or None if no override exists
    """
    overrides = load_device_overrides()
    return overrides.get(device_id)


def set_device_override(device_id: str, override: dict) -> dict:
    """Update configuration override for a device.

    Args:
        device_id: ID of the device to update
        override: Dictionary of override values

    Returns:
        The updated override entry
    """
    overrides = load_device_overrides()
    if device_id in overrides:
        overrides[device_id].update(override)
    else:
        overrides[device_id] = override
    save_device_overrides(overrides)
    return overrides[device_id]


def delete_device_override(device_id: str) -> bool:
    """Remove configuration override for a device (reset to defaults).

    Returns:
        True if override was removed, False if not found
    """
    overrides = load_device_overrides()
    if device_id not in overrides:
        return False
    del overrides[device_id]
    save_device_overrides(overrides)
    return True


def find_image_reference(device_id: str, version: str | None = None) -> str | None:
    """Look up the image reference for a device type and version.

    Supports Docker, qcow2, and IOL images.

    Args:
        device_id: Device type (e.g., 'eos', 'ceos', 'iosv', 'cisco_iosv')
        version: Optional version string (e.g., '4.35.1F')

    Returns:
        Image reference (Docker tag or file path for qcow2/IOL) or None if not found
    """
    manifest = load_manifest()
    images = manifest.get("images", [])

    # Supported image kinds
    supported_kinds = ("docker", "qcow2", "iol")

    # First try exact version match
    if version:
        version_lower = version.lower()
        for img in images:
            if img.get("kind") not in supported_kinds:
                continue
            img_version = (img.get("version") or "").lower()
            if image_matches_device(img, device_id) and img_version == version_lower:
                return img.get("reference")

    # Fall back to default image for this device type
    for img in images:
        if img.get("kind") not in supported_kinds:
            continue
        if image_matches_device(img, device_id) and img.get("is_default"):
            return img.get("reference")

    # Fall back to any image for this device type
    for img in images:
        if img.get("kind") not in supported_kinds:
            continue
        if image_matches_device(img, device_id):
            return img.get("reference")

    return None


def get_image_provider(image_reference: str | None) -> str:
    """Determine the provider type for an image based on its reference.

    Args:
        image_reference: Image reference (Docker tag or file path)

    Returns:
        Provider name: "libvirt" for qcow2/img files, "docker" otherwise
    """
    if not image_reference:
        return "docker"

    # File-based images that need libvirt/QEMU
    if image_reference.endswith((".qcow2", ".img")):
        return "libvirt"

    # IOL images run in a Docker container wrapper
    if image_reference.endswith(".iol"):
        return "docker"

    # Default to docker for Docker image tags
    return "docker"
