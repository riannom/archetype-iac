"""Device service for managing vendor configurations and custom devices.

This service encapsulates all device/vendor-related business logic,
extracted from main.py to improve maintainability and testability.

Usage:
    from app.services.device_service import DeviceService

    service = DeviceService()
    vendors = service.list_vendors()
    device = service.add_custom_device(payload)
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class DeviceNotFoundError(Exception):
    """Raised when a device is not found."""
    pass


class DeviceConflictError(Exception):
    """Raised when a device ID conflicts with existing device."""
    pass


class DeviceValidationError(Exception):
    """Raised when device data is invalid."""
    pass


class DeviceHasImagesError(Exception):
    """Raised when trying to delete device with assigned images."""
    pass


class DeviceService:
    """Service for managing vendor configurations and custom devices.

    This service provides methods for:
    - Listing vendors with filtering and merging
    - Managing custom devices (CRUD)
    - Managing device visibility (hide/restore)
    - Managing device configuration overrides
    """

    def list_vendors(self) -> list[dict]:
        """Return vendor configurations for frontend device catalog.

        Returns a unified view of all supported network devices,
        including their categories, icons, versions, and availability status.
        Hidden devices are filtered out.
        """
        from agent.vendors import get_vendors_for_ui
        from app.image_store import load_custom_devices, load_hidden_devices

        # Get base vendor configs
        result = get_vendors_for_ui()

        # Load hidden device IDs
        hidden_ids = set(load_hidden_devices())

        # Filter out hidden devices from vendor registry
        def filter_models(models: list[dict]) -> list[dict]:
            return [m for m in models if m.get("id") not in hidden_ids]

        for cat_data in result:
            if "subCategories" in cat_data:
                for subcat in cat_data["subCategories"]:
                    subcat["models"] = filter_models(subcat.get("models", []))
                # Remove empty subcategories
                cat_data["subCategories"] = [
                    s for s in cat_data["subCategories"] if s.get("models")
                ]
            elif "models" in cat_data:
                cat_data["models"] = filter_models(cat_data.get("models", []))

        # Remove empty categories
        result = [c for c in result if c.get("models") or c.get("subCategories")]

        # Load custom devices and merge them
        custom_devices = load_custom_devices()
        if custom_devices:
            result = self._merge_custom_devices(result, custom_devices)

        return result

    def _merge_custom_devices(
        self, result: list[dict], custom_devices: list[dict]
    ) -> list[dict]:
        """Merge custom devices into the vendor catalog structure."""
        # Group custom devices by category
        custom_by_category: dict[str, list[dict]] = {}
        for device in custom_devices:
            cat = device.get("category", "Compute")
            if cat not in custom_by_category:
                custom_by_category[cat] = []
            custom_by_category[cat].append(device)

        # Merge into existing categories or create new ones
        for cat_data in result:
            cat_name = cat_data.get("name")
            if cat_name in custom_by_category:
                if "subCategories" in cat_data:
                    # Find "Custom" subcategory or create one
                    other_subcat = None
                    for subcat in cat_data["subCategories"]:
                        if subcat.get("name") == "Custom":
                            other_subcat = subcat
                            break
                    if other_subcat:
                        other_subcat["models"].extend(custom_by_category[cat_name])
                    else:
                        cat_data["subCategories"].append({
                            "name": "Custom",
                            "models": custom_by_category[cat_name]
                        })
                elif "models" in cat_data:
                    cat_data["models"].extend(custom_by_category[cat_name])
                del custom_by_category[cat_name]

        # Add remaining categories that don't exist
        for cat_name, devices in custom_by_category.items():
            result.append({
                "name": cat_name,
                "models": devices
            })

        return result

    def add_custom_device(self, payload: dict) -> dict:
        """Add a custom device type.

        Args:
            payload: Device configuration with required 'id' and 'name' fields

        Returns:
            The created device configuration

        Raises:
            DeviceValidationError: If required fields are missing
            DeviceConflictError: If device ID conflicts with existing device
        """
        from app.image_store import add_custom_device as store_add_device, find_custom_device
        from agent.vendors import VENDOR_CONFIGS

        device_id = payload.get("id")
        if not device_id:
            raise DeviceValidationError("Device ID is required")
        if not payload.get("name"):
            raise DeviceValidationError("Device name is required")

        # Check if device ID conflicts with vendor registry
        if device_id in VENDOR_CONFIGS:
            raise DeviceConflictError(
                f"Device ID '{device_id}' conflicts with built-in vendor registry"
            )

        # Check if already exists as custom device
        if find_custom_device(device_id):
            raise DeviceConflictError(f"Custom device '{device_id}' already exists")

        try:
            return store_add_device(payload)
        except ValueError as e:
            raise DeviceConflictError(str(e))

    def update_custom_device(self, device_id: str, payload: dict) -> dict:
        """Update a custom device type's properties.

        Args:
            device_id: Device identifier
            payload: Fields to update

        Returns:
            The updated device configuration

        Raises:
            DeviceNotFoundError: If device not found
            DeviceValidationError: If trying to modify built-in device
        """
        from app.image_store import find_custom_device, update_custom_device
        from agent.vendors import VENDOR_CONFIGS

        # Check if it's a built-in vendor device
        if device_id in VENDOR_CONFIGS:
            raise DeviceValidationError("Cannot modify built-in vendor devices")

        # Check if custom device exists
        if not find_custom_device(device_id):
            raise DeviceNotFoundError(f"Custom device '{device_id}' not found")

        updated = update_custom_device(device_id, payload)
        if not updated:
            raise DeviceNotFoundError(f"Device '{device_id}' not found")

        return updated

    def delete_device(self, device_id: str) -> dict:
        """Delete or hide a device type.

        Custom devices are permanently deleted.
        Built-in devices are hidden from the UI.

        Args:
            device_id: Device identifier

        Returns:
            Success message dict

        Raises:
            DeviceNotFoundError: If device not found
            DeviceHasImagesError: If device has assigned images
        """
        from app.image_store import (
            find_custom_device,
            delete_custom_device as store_delete_device,
            get_device_image_count,
            hide_device,
            is_device_hidden,
        )
        from agent.vendors import VENDOR_CONFIGS, get_kind_for_device

        # Resolve alias to canonical device ID
        canonical_id = get_kind_for_device(device_id)

        # Check if any images are assigned
        image_count = get_device_image_count(device_id)
        if canonical_id != device_id:
            image_count += get_device_image_count(canonical_id)
        if image_count > 0:
            raise DeviceHasImagesError(
                f"Cannot delete device with {image_count} assigned image(s)"
            )

        # Check if it's a built-in vendor device
        if canonical_id in VENDOR_CONFIGS:
            if is_device_hidden(device_id):
                raise DeviceValidationError(f"Device '{device_id}' is already hidden")
            hide_device(device_id)
            return {"message": f"Built-in device '{device_id}' hidden successfully"}

        # Check if custom device exists
        device = find_custom_device(device_id)
        if not device:
            raise DeviceNotFoundError(f"Device '{device_id}' not found")

        deleted = store_delete_device(device_id)
        if not deleted:
            raise DeviceNotFoundError(f"Device '{device_id}' not found")

        return {"message": f"Custom device '{device_id}' deleted successfully"}

    def get_device_config(self, device_id: str) -> dict:
        """Get full device configuration including base, overrides, and effective.

        Args:
            device_id: Device identifier

        Returns:
            Dict with 'base', 'overrides', and 'effective' configurations

        Raises:
            DeviceNotFoundError: If device not found
        """
        from app.image_store import find_custom_device, get_device_override
        from agent.vendors import VENDOR_CONFIGS, get_kind_for_device, _get_vendor_options

        # Resolve alias to canonical device ID
        canonical_id = get_kind_for_device(device_id)

        base_config = {}

        # Check if it's a built-in vendor device
        if canonical_id in VENDOR_CONFIGS:
            config = VENDOR_CONFIGS[canonical_id]
            base_config = {
                "id": device_id,
                "kind": config.kind,
                "vendor": config.vendor,
                "name": config.label or config.vendor,
                "type": config.device_type.value,
                "icon": config.icon,
                "versions": config.versions,
                "isActive": config.is_active,
                "portNaming": config.port_naming,
                "portStartIndex": config.port_start_index,
                "maxPorts": config.max_ports,
                "memory": config.memory,
                "cpu": config.cpu,
                "requiresImage": config.requires_image,
                "supportedImageKinds": config.supported_image_kinds,
                "documentationUrl": config.documentation_url,
                "licenseRequired": config.license_required,
                "tags": config.tags,
                "notes": config.notes,
                "consoleShell": config.console_shell,
                "readinessProbe": config.readiness_probe,
                "readinessPattern": config.readiness_pattern,
                "readinessTimeout": config.readiness_timeout,
                "vendorOptions": _get_vendor_options(config),
                "isBuiltIn": True,
            }
        else:
            # Check if it's a custom device
            custom = find_custom_device(device_id)
            if not custom:
                raise DeviceNotFoundError(f"Device '{device_id}' not found")
            base_config = {**custom, "isBuiltIn": False}

        # Get overrides
        overrides = get_device_override(device_id) or {}

        # Compute effective configuration
        effective = {**base_config, **overrides}

        return {
            "base": base_config,
            "overrides": overrides,
            "effective": effective,
        }

    def update_device_config(self, device_id: str, payload: dict) -> dict:
        """Update device configuration overrides.

        Args:
            device_id: Device identifier
            payload: Override fields to set

        Returns:
            Updated configuration dict

        Raises:
            DeviceNotFoundError: If device not found
            DeviceValidationError: If no valid override fields provided
        """
        from app.image_store import find_custom_device, set_device_override
        from agent.vendors import VENDOR_CONFIGS, get_kind_for_device

        # Allowed override fields
        ALLOWED_OVERRIDE_FIELDS = {
            "memory", "cpu", "maxPorts", "portNaming", "portStartIndex",
            "readinessTimeout", "vendorOptions"
        }

        # Filter payload to only allowed fields
        filtered_payload = {
            k: v for k, v in payload.items() if k in ALLOWED_OVERRIDE_FIELDS
        }

        if not filtered_payload:
            raise DeviceValidationError(
                f"No valid override fields provided. Allowed: {', '.join(ALLOWED_OVERRIDE_FIELDS)}"
            )

        # Resolve alias to canonical device ID
        canonical_id = get_kind_for_device(device_id)

        # Verify device exists
        is_built_in = canonical_id in VENDOR_CONFIGS
        if not is_built_in:
            custom = find_custom_device(device_id)
            if not custom:
                raise DeviceNotFoundError(f"Device '{device_id}' not found")

        # Set override
        set_device_override(device_id, filtered_payload)

        # Return updated config
        return self.get_device_config(device_id)

    def reset_device_config(self, device_id: str) -> dict:
        """Reset device configuration to defaults by removing all overrides.

        Args:
            device_id: Device identifier

        Returns:
            Success message dict

        Raises:
            DeviceNotFoundError: If device not found
        """
        from app.image_store import delete_device_override, find_custom_device
        from agent.vendors import VENDOR_CONFIGS, get_kind_for_device

        # Resolve alias to canonical device ID
        canonical_id = get_kind_for_device(device_id)

        # Verify device exists
        is_built_in = canonical_id in VENDOR_CONFIGS
        if not is_built_in:
            custom = find_custom_device(device_id)
            if not custom:
                raise DeviceNotFoundError(f"Device '{device_id}' not found")

        # Delete override
        deleted = delete_device_override(device_id)
        if not deleted:
            return {"message": f"Device '{device_id}' has no overrides to reset"}

        return {"message": f"Device '{device_id}' reset to defaults"}

    def list_hidden_devices(self) -> list[str]:
        """List all hidden built-in device IDs."""
        from app.image_store import load_hidden_devices
        return load_hidden_devices()

    def hide_device(self, device_id: str) -> dict:
        """Hide a built-in device from the UI.

        Args:
            device_id: Device identifier

        Returns:
            Success message dict

        Raises:
            DeviceValidationError: If device is not built-in or already hidden
        """
        from app.image_store import hide_device, is_device_hidden
        from agent.vendors import VENDOR_CONFIGS, get_kind_for_device

        canonical_id = get_kind_for_device(device_id)

        if canonical_id not in VENDOR_CONFIGS:
            raise DeviceValidationError("Only built-in devices can be hidden")

        if is_device_hidden(device_id):
            raise DeviceValidationError(f"Device '{device_id}' is already hidden")

        hide_device(device_id)
        return {"message": f"Device '{device_id}' hidden successfully"}

    def restore_device(self, device_id: str) -> dict:
        """Restore a hidden built-in device.

        Args:
            device_id: Device identifier

        Returns:
            Success message dict

        Raises:
            DeviceValidationError: If device is not built-in or not hidden
        """
        from app.image_store import unhide_device, is_device_hidden
        from agent.vendors import VENDOR_CONFIGS, get_kind_for_device

        canonical_id = get_kind_for_device(device_id)

        if canonical_id not in VENDOR_CONFIGS:
            raise DeviceValidationError("Only built-in devices can be restored")

        if not is_device_hidden(device_id):
            raise DeviceValidationError(f"Device '{device_id}' is not hidden")

        unhide_device(device_id)
        return {"message": f"Device '{device_id}' restored successfully"}


# Singleton instance
_device_service: DeviceService | None = None


def get_device_service() -> DeviceService:
    """Get the device service singleton."""
    global _device_service
    if _device_service is None:
        _device_service = DeviceService()
    return _device_service
