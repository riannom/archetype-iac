from __future__ import annotations

from datetime import datetime
from pathlib import Path
import json
import re
from typing import Optional

from app.config import settings


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


def image_store_root() -> Path:
    if settings.qcow2_store:
        return Path(settings.qcow2_store)
    return Path(settings.netlab_workspace) / "images"


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
    return json.loads(path.read_text(encoding="utf-8"))


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


def get_device_image_count(device_id: str) -> int:
    """Count how many images are assigned to a device type.

    Checks both 'device_id' field and 'compatible_devices' list.
    """
    manifest = load_manifest()
    count = 0
    device_lower = device_id.lower()

    for image in manifest.get("images", []):
        # Check primary device assignment
        if (image.get("device_id") or "").lower() == device_lower:
            count += 1
            continue

        # Check compatible_devices list
        compatible = [d.lower() for d in image.get("compatible_devices", [])]
        if device_lower in compatible:
            count += 1

    return count


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
        "ceos": "eos",
        "eos": "eos",
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
    vendor = get_vendor_for_device(device_id) if device_id else None

    return {
        "id": image_id,
        "kind": kind,
        "reference": reference,
        "filename": filename,
        "device_id": device_id,
        "version": version,
        # New fields
        "vendor": vendor,
        "uploaded_at": datetime.utcnow().isoformat() + "Z",
        "size_bytes": size_bytes,
        "is_default": False,
        "notes": notes,
        "compatible_devices": compatible_devices or ([device_id] if device_id else []),
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
                updates["vendor"] = get_vendor_for_device(updates["device_id"])

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
    """Look up the actual Docker image reference for a device type and version.

    Args:
        device_id: Device type (e.g., 'eos', 'ceos', 'iosv')
        version: Optional version string (e.g., '4.35.1F')

    Returns:
        Docker image reference (e.g., 'ceos64-lab-4.35.1f:imported') or None if not found
    """
    manifest = load_manifest()
    images = manifest.get("images", [])

    # Normalize device_id for matching (eos and ceos are equivalent)
    normalized_device = device_id.lower()
    if normalized_device in ("ceos", "arista_ceos", "arista_eos"):
        normalized_device = "eos"

    # First try exact version match
    if version:
        version_lower = version.lower()
        for img in images:
            if img.get("kind") != "docker":
                continue
            img_device = (img.get("device_id") or "").lower()
            if img_device in ("ceos", "arista_ceos", "arista_eos"):
                img_device = "eos"
            img_version = (img.get("version") or "").lower()
            if img_device == normalized_device and img_version == version_lower:
                return img.get("reference")

    # Fall back to any image for this device type
    for img in images:
        if img.get("kind") != "docker":
            continue
        img_device = (img.get("device_id") or "").lower()
        if img_device in ("ceos", "arista_ceos", "arista_eos"):
            img_device = "eos"
        if img_device == normalized_device:
            return img.get("reference")

    return None
