"""ImageMetadata dataclass and image CRUD operations."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from .aliases import (
    PLATFORM_SIBLINGS,
    canonicalize_device_id,
    canonicalize_device_ids,
    get_image_default_device_scopes,
    get_vendor_for_device,
    normalize_default_device_scope_id,
    normalize_default_device_scope_ids,
)
from .custom_devices import ensure_custom_device_exists

logger = logging.getLogger(__name__)


@dataclass
class ImageMetadata:
    """Structured metadata for an image library entry.

    Replaces the 23+ parameter signature of create_image_entry with a
    single typed dataclass.
    """
    image_id: str
    kind: str  # "docker", "qcow2", "iol"
    reference: str  # Docker tag or file path
    filename: str

    # Core metadata
    device_id: str | None = None
    version: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None
    notes: str = ""
    compatible_devices: list[str] | None = None
    source: str | None = None

    # VM runtime hints (from VIRL2 node-definitions or user overrides)
    memory_mb: int | None = None
    cpu_count: int | None = None
    disk_driver: str | None = None
    nic_driver: str | None = None
    machine_type: str | None = None
    libvirt_driver: str | None = None
    boot_timeout: int | None = None
    readiness_probe: str | None = None
    readiness_pattern: str | None = None
    efi_boot: bool | None = None
    efi_vars: str | None = None
    max_ports: int | None = None
    port_naming: str | None = None
    cpu_limit: int | None = None
    has_loopback: bool | None = None
    provisioning_driver: str | None = None
    provisioning_media_type: str | None = None

    def to_entry(self) -> dict:
        """Convert to a manifest image entry dict.

        Handles canonicalization, vendor resolution, and custom device creation.
        """
        return create_image_entry(
            image_id=self.image_id,
            kind=self.kind,
            reference=self.reference,
            filename=self.filename,
            device_id=self.device_id,
            version=self.version,
            size_bytes=self.size_bytes,
            notes=self.notes,
            compatible_devices=self.compatible_devices,
            source=self.source,
            memory_mb=self.memory_mb,
            cpu_count=self.cpu_count,
            disk_driver=self.disk_driver,
            nic_driver=self.nic_driver,
            machine_type=self.machine_type,
            libvirt_driver=self.libvirt_driver,
            boot_timeout=self.boot_timeout,
            readiness_probe=self.readiness_probe,
            readiness_pattern=self.readiness_pattern,
            efi_boot=self.efi_boot,
            efi_vars=self.efi_vars,
            max_ports=self.max_ports,
            port_naming=self.port_naming,
            cpu_limit=self.cpu_limit,
            has_loopback=self.has_loopback,
            provisioning_driver=self.provisioning_driver,
            provisioning_media_type=self.provisioning_media_type,
            sha256=self.sha256,
        )


def create_image_entry(
    image_id: str,
    kind: str,
    reference: str,
    filename: str,
    device_id: str | None = None,
    version: str | None = None,
    size_bytes: int | None = None,
    notes: str = "",
    compatible_devices: list[str] | None = None,
    source: str | None = None,
    memory_mb: int | None = None,
    cpu_count: int | None = None,
    disk_driver: str | None = None,
    nic_driver: str | None = None,
    machine_type: str | None = None,
    libvirt_driver: str | None = None,
    boot_timeout: int | None = None,
    readiness_probe: str | None = None,
    readiness_pattern: str | None = None,
    efi_boot: bool | None = None,
    efi_vars: str | None = None,
    max_ports: int | None = None,
    port_naming: str | None = None,
    cpu_limit: int | None = None,
    has_loopback: bool | None = None,
    provisioning_driver: str | None = None,
    provisioning_media_type: str | None = None,
    sha256: str | None = None,
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
    canonical_device = canonicalize_device_id(device_id)
    normalized_compatible_devices = canonicalize_device_ids(compatible_devices)
    if canonical_device and canonical_device not in normalized_compatible_devices:
        normalized_compatible_devices.append(canonical_device)

    # Expand platform siblings for new entries too.
    expanded = set(normalized_compatible_devices)
    for dev_id in list(expanded):
        for sibling in PLATFORM_SIBLINGS.get(dev_id, []):
            expanded.add(sibling)
    normalized_compatible_devices = list(expanded)

    vendor = get_vendor_for_device(canonical_device) if canonical_device else None

    # Ensure custom device entry exists for this device_id
    # This creates entries like "eos" with proper portNaming from the canonical "ceos" config
    if canonical_device:
        ensure_custom_device_exists(canonical_device, preferred_image_kind=kind)

    return {
        "id": image_id,
        "kind": kind,
        "reference": reference,
        "filename": filename,
        "device_id": canonical_device,
        "version": version,
        # New fields
        "vendor": vendor,
        "uploaded_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "size_bytes": size_bytes,
        "sha256": sha256,
        "is_default": False,
        "default_for_devices": [],
        "notes": notes,
        "compatible_devices": normalized_compatible_devices,
        "source": source,
        # Optional runtime hints sourced from vendor image metadata (for example VIRL2 node-definitions).
        "memory_mb": memory_mb,
        "cpu_count": cpu_count,
        "disk_driver": disk_driver,
        "nic_driver": nic_driver,
        "machine_type": machine_type,
        "libvirt_driver": libvirt_driver,
        "boot_timeout": boot_timeout,
        "readiness_probe": readiness_probe,
        "readiness_pattern": readiness_pattern,
        "efi_boot": efi_boot,
        "efi_vars": efi_vars,
        "max_ports": max_ports,
        "port_naming": port_naming,
        "cpu_limit": cpu_limit,
        "has_loopback": has_loopback,
        "provisioning_driver": provisioning_driver,
        "provisioning_media_type": provisioning_media_type,
    }


def update_image_entry(
    manifest: dict,
    image_id: str,
    updates: dict,
) -> dict | None:
    """Update an existing image entry with new values.

    Args:
        manifest: The manifest dictionary
        image_id: ID of the image to update
        updates: Dictionary of fields to update

    Returns:
        Updated image entry or None if not found
    """
    default_for_device = normalize_default_device_scope_id(updates.pop("default_for_device", None))

    for item in manifest.get("images", []):
        if item.get("id") == image_id:
            # Update vendor if device_id is being changed
            if "device_id" in updates:
                updates["device_id"] = canonicalize_device_id(updates["device_id"])
                updates["vendor"] = get_vendor_for_device(updates["device_id"])

            if "compatible_devices" in updates:
                updates["compatible_devices"] = canonicalize_device_ids(updates["compatible_devices"])

            if "default_for_devices" in updates:
                updates["default_for_devices"] = normalize_default_device_scope_ids(updates["default_for_devices"])

            # Ensure device_id is included in compatible_devices when assigned.
            if updates.get("device_id"):
                compatible = updates.get("compatible_devices")
                if compatible is None:
                    compatible = canonicalize_device_ids(item.get("compatible_devices") or [])
                if updates["device_id"] not in compatible:
                    compatible.append(updates["device_id"])
                updates["compatible_devices"] = compatible

            if "is_default" in updates:
                requested_default = bool(updates["is_default"])
                default_scope = default_for_device or normalize_default_device_scope_id(
                    updates.get("device_id") or item.get("device_id")
                )
                current_scopes = updates.get("default_for_devices")
                if current_scopes is None:
                    current_scopes = get_image_default_device_scopes(item)

                if requested_default:
                    if default_scope:
                        # Only one default image per device scope.
                        for other in manifest.get("images", []):
                            if other.get("id") == image_id:
                                continue
                            other_scopes = get_image_default_device_scopes(other)
                            if default_scope in other_scopes:
                                other_scopes = [scope for scope in other_scopes if scope != default_scope]
                                other["default_for_devices"] = other_scopes
                                other["is_default"] = bool(other_scopes)
                        if default_scope not in current_scopes:
                            current_scopes.append(default_scope)
                        updates["default_for_devices"] = normalize_default_device_scope_ids(current_scopes)
                        updates["is_default"] = True
                    else:
                        # No scope available; leave existing default scopes unchanged.
                        updates.pop("is_default", None)
                else:
                    if default_scope:
                        current_scopes = [scope for scope in current_scopes if scope != default_scope]
                        updates["default_for_devices"] = normalize_default_device_scope_ids(current_scopes)
                        updates["is_default"] = bool(updates["default_for_devices"])
                    else:
                        updates["default_for_devices"] = []
                        updates["is_default"] = False
            elif "default_for_devices" in updates:
                updates["is_default"] = bool(updates["default_for_devices"])

            # Invariant: device defaults must always be compatible.
            final_default_scopes = normalize_default_device_scope_ids(
                updates.get("default_for_devices") or item.get("default_for_devices") or []
            )
            if final_default_scopes:
                compatible = updates.get("compatible_devices")
                if compatible is None:
                    compatible = canonicalize_device_ids(item.get("compatible_devices") or [])
                else:
                    compatible = canonicalize_device_ids(compatible)

                for scope in final_default_scopes:
                    canonical_scope = canonicalize_device_id(scope)
                    if canonical_scope and canonical_scope not in compatible:
                        compatible.append(canonical_scope)
                updates["compatible_devices"] = compatible

            item.update(updates)
            return item
    return None


def delete_image_entry(manifest: dict, image_id: str) -> dict | None:
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
