"""Vendor device catalog endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app import models
from app.auth import get_current_user


router = APIRouter(prefix="/vendors", tags=["vendors"])


@router.get("")
def list_vendors() -> list[dict]:
    """Return vendor configurations for frontend device catalog.

    This endpoint provides a unified view of all supported network devices,
    including their categories, icons, versions, and availability status.
    Data is sourced from the centralized vendor registry in agent/vendors.py,
    merged with any custom device types defined per installation.
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

    # Load custom devices and merge them (custom devices aren't hidden)
    custom_devices = load_custom_devices()
    if custom_devices:
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
                # Add to existing category
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


@router.post("")
def add_custom_device(
    payload: dict,
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """Add a custom device type.

    Required fields:
    - id: Unique device identifier
    - name: Display name

    Optional fields:
    - type: Device type (router, switch, firewall, host, container)
    - category: UI category (Network, Security, Compute, Cloud & External)
    - vendor: Vendor name (default: "Custom")
    - icon: FontAwesome icon class (default: "fa-box")
    - versions: List of versions (default: ["latest"])
    - memory: Memory requirement in MB (default: 1024)
    - cpu: CPU cores required (default: 1)
    - maxPorts: Maximum interfaces (default: 8)
    - portNaming: Interface naming pattern (default: "eth")
    - portStartIndex: Starting port number (default: 0)
    - requiresImage: Whether user must provide image (default: true)
    - supportedImageKinds: List of image types (default: ["docker"])
    - licenseRequired: Whether license is required (default: false)
    - documentationUrl: Link to documentation
    - tags: Searchable tags
    """
    from app.image_store import add_custom_device as store_add_device, find_custom_device
    from agent.vendors import VENDOR_CONFIGS

    device_id = payload.get("id")
    if not device_id:
        raise HTTPException(status_code=400, detail="Device ID is required")
    if not payload.get("name"):
        raise HTTPException(status_code=400, detail="Device name is required")

    # Check if device ID conflicts with vendor registry
    if device_id in VENDOR_CONFIGS:
        raise HTTPException(
            status_code=409,
            detail=f"Device ID '{device_id}' conflicts with built-in vendor registry"
        )

    # Check if already exists as custom device
    if find_custom_device(device_id):
        raise HTTPException(
            status_code=409,
            detail=f"Custom device '{device_id}' already exists"
        )

    try:
        device = store_add_device(payload)
        return {"device": device}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.delete("/{device_id}")
def delete_device(
    device_id: str,
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """Delete or hide a device type.

    - Custom devices: Permanently deleted (only if no images assigned)
    - Built-in devices: Hidden from the UI (can be restored later)

    Both require no images to be assigned to the device.
    Accepts device IDs or aliases (e.g., 'eos' or 'ceos' both work for Arista EOS).
    """
    from app.image_store import (
        find_custom_device,
        delete_custom_device as store_delete_device,
        get_device_image_count,
        hide_device,
        is_device_hidden,
    )
    from agent.vendors import VENDOR_CONFIGS, get_kind_for_device

    # Resolve alias to canonical device ID (for vendor registry lookup)
    canonical_id = get_kind_for_device(device_id)

    # Check if any images are assigned to this device (check both original and canonical)
    image_count = get_device_image_count(device_id)
    if canonical_id != device_id:
        image_count += get_device_image_count(canonical_id)
    if image_count > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete device with {image_count} assigned image(s). Unassign images first."
        )

    # Check if it's a built-in vendor device (use canonical ID)
    if canonical_id in VENDOR_CONFIGS:
        # Check if already hidden (we hide by the device_id used in UI, which is the alias)
        if is_device_hidden(device_id):
            raise HTTPException(
                status_code=400,
                detail=f"Device '{device_id}' is already hidden"
            )
        # Hide instead of delete - use the device_id as passed (the alias shown in UI)
        hide_device(device_id)
        return {"message": f"Built-in device '{device_id}' hidden successfully"}

    # Check if custom device exists
    device = find_custom_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    deleted = store_delete_device(device_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Device not found")

    return {"message": f"Custom device '{device_id}' deleted successfully"}


@router.post("/{device_id}/restore")
def restore_hidden_device(
    device_id: str,
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """Restore a hidden built-in device type.

    Only built-in devices that have been hidden can be restored.
    Accepts device IDs or aliases.
    """
    from app.image_store import unhide_device, is_device_hidden
    from agent.vendors import VENDOR_CONFIGS, get_kind_for_device

    # Resolve alias to canonical device ID
    canonical_id = get_kind_for_device(device_id)

    # Check if it's a built-in vendor device
    if canonical_id not in VENDOR_CONFIGS:
        raise HTTPException(
            status_code=400,
            detail="Only built-in devices can be restored"
        )

    # Check if it's actually hidden
    if not is_device_hidden(device_id):
        raise HTTPException(
            status_code=400,
            detail=f"Device '{device_id}' is not hidden"
        )

    unhide_device(device_id)
    return {"message": f"Device '{device_id}' restored successfully"}


@router.get("/hidden")
def list_hidden_devices(
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """List all hidden built-in devices."""
    from app.image_store import load_hidden_devices
    return {"hidden": load_hidden_devices()}


@router.put("/{device_id}")
def update_custom_device_endpoint(
    device_id: str,
    payload: dict,
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """Update a custom device type's properties.

    Body can include any of:
    - name: Display name
    - category: UI category
    - vendor: Vendor name
    - icon: FontAwesome icon class
    - versions: List of versions
    - memory: Memory requirement in MB
    - cpu: CPU cores required
    - maxPorts: Maximum interfaces
    - portNaming: Interface naming pattern
    - requiresImage: Whether user must provide image
    - supportedImageKinds: List of image types
    - licenseRequired: Whether license is required
    - documentationUrl: Link to docs
    - tags: Searchable tags
    - isActive: Whether device is available in UI
    """
    from app.image_store import find_custom_device, update_custom_device
    from agent.vendors import VENDOR_CONFIGS

    # Check if it's a built-in vendor device
    if device_id in VENDOR_CONFIGS:
        raise HTTPException(
            status_code=400,
            detail="Cannot modify built-in vendor devices"
        )

    # Check if custom device exists
    if not find_custom_device(device_id):
        raise HTTPException(status_code=404, detail="Custom device not found")

    updated = update_custom_device(device_id, payload)
    if not updated:
        raise HTTPException(status_code=404, detail="Device not found")

    return {"device": updated}


@router.get("/{device_id}/config")
def get_device_config(
    device_id: str,
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """Get full device configuration including base config, overrides, and effective values.

    Returns:
        - base: The base configuration from vendor registry or custom device
        - overrides: User-defined configuration overrides
        - effective: Merged configuration (base + overrides)
    """
    from app.image_store import (
        find_custom_device,
        get_device_override,
    )
    from agent.vendors import _get_vendor_options, _get_config_by_kind

    base_config = {}

    # Check if it's a built-in vendor device
    config = _get_config_by_kind(device_id)
    if config:
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
            raise HTTPException(status_code=404, detail="Device not found")
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


@router.put("/{device_id}/config")
def update_device_config(
    device_id: str,
    payload: dict,
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """Update device configuration overrides.

    Allowed override fields:
    - memory: Memory requirement in MB
    - cpu: CPU cores required
    - maxPorts: Maximum interfaces
    - portNaming: Interface naming pattern
    - portStartIndex: Starting port number
    - readinessTimeout: Boot readiness timeout in seconds
    - vendorOptions: Vendor-specific options (e.g., zerotouchCancel)

    Returns the updated configuration.
    """
    from app.image_store import (
        find_custom_device,
        set_device_override,
    )
    from agent.vendors import VENDOR_CONFIGS, get_kind_for_device

    # Allowed override fields
    ALLOWED_OVERRIDE_FIELDS = {
        "memory", "cpu", "maxPorts", "portNaming", "portStartIndex",
        "readinessTimeout", "vendorOptions"
    }

    # Filter payload to only allowed fields
    filtered_payload = {k: v for k, v in payload.items() if k in ALLOWED_OVERRIDE_FIELDS}

    if not filtered_payload:
        raise HTTPException(
            status_code=400,
            detail=f"No valid override fields provided. Allowed: {', '.join(ALLOWED_OVERRIDE_FIELDS)}"
        )

    # Resolve alias to canonical device ID
    canonical_id = get_kind_for_device(device_id)

    # Verify device exists
    is_built_in = canonical_id in VENDOR_CONFIGS
    if not is_built_in:
        custom = find_custom_device(device_id)
        if not custom:
            raise HTTPException(status_code=404, detail="Device not found")

    # Set override
    set_device_override(device_id, filtered_payload)

    # Return updated config
    return get_device_config(device_id, current_user)


@router.delete("/{device_id}/config")
def reset_device_config(
    device_id: str,
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """Reset device configuration to defaults by removing all overrides.

    Returns:
        Success message
    """
    from app.image_store import delete_device_override
    from agent.vendors import VENDOR_CONFIGS, get_kind_for_device

    # Resolve alias to canonical device ID
    canonical_id = get_kind_for_device(device_id)

    # Verify device exists
    is_built_in = canonical_id in VENDOR_CONFIGS
    if not is_built_in:
        from app.image_store import find_custom_device
        custom = find_custom_device(device_id)
        if not custom:
            raise HTTPException(status_code=404, detail="Device not found")

    # Delete override
    deleted = delete_device_override(device_id)
    if not deleted:
        return {"message": f"Device '{device_id}' has no overrides to reset"}

    return {"message": f"Device '{device_id}' reset to defaults"}
