"""Vendor device catalog endpoints."""
from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app import models
from app.auth import get_current_user, get_current_user_optional
from app.db import get_db
from app.services.device_constraints import validate_minimum_hardware


router = APIRouter(prefix="/vendors", tags=["vendors"])


def load_hidden_devices() -> list[str]:
    """Compatibility wrapper for tests monkeypatching app.routers.vendors."""
    from app.image_store import load_hidden_devices as _load_hidden_devices

    return _load_hidden_devices()


def get_config_by_device(device_id: str):
    """Compatibility wrapper for tests monkeypatching app.routers.vendors."""
    from agent.vendors import get_config_by_device as _get_config_by_device

    return _get_config_by_device(device_id)


def canonicalize_device_id(device_id: str) -> str | None:
    """Compatibility wrapper for tests monkeypatching app.routers.vendors."""
    from app.image_store import canonicalize_device_id as _canonicalize_device_id

    return _canonicalize_device_id(device_id)


def find_custom_device(device_id: str) -> dict | None:
    """Compatibility wrapper for tests monkeypatching app.routers.vendors."""
    from app.image_store import find_custom_device as _find_custom_device

    return _find_custom_device(device_id)


def store_add_device(payload: dict) -> dict:
    """Compatibility wrapper for tests monkeypatching app.routers.vendors."""
    from app.image_store import add_custom_device as _store_add_device

    return _store_add_device(payload)


def store_delete_device(device_id: str) -> bool:
    """Compatibility wrapper for tests monkeypatching app.routers.vendors."""
    from app.image_store import delete_custom_device as _store_delete_device

    return _store_delete_device(device_id)


def update_custom_device(device_id: str, payload: dict) -> dict | None:
    """Compatibility wrapper for tests monkeypatching app.routers.vendors."""
    from app.image_store import update_custom_device as _update_custom_device

    return _update_custom_device(device_id, payload)


def get_device_image_count(device_id: str) -> int:
    """Compatibility wrapper for tests monkeypatching app.routers.vendors."""
    from app.image_store import get_device_image_count as _get_device_image_count

    return _get_device_image_count(device_id)


def hide_device(device_id: str) -> None:
    """Compatibility wrapper for tests monkeypatching app.routers.vendors."""
    from app.image_store import hide_device as _hide_device

    _hide_device(device_id)


def is_device_hidden(device_id: str) -> bool:
    """Compatibility wrapper for tests monkeypatching app.routers.vendors."""
    from app.image_store import is_device_hidden as _is_device_hidden

    return _is_device_hidden(device_id)


def unhide_device(device_id: str) -> None:
    """Compatibility wrapper for tests monkeypatching app.routers.vendors."""
    from app.image_store import unhide_device as _unhide_device

    _unhide_device(device_id)


def get_device_override(device_id: str) -> dict | None:
    """Compatibility wrapper for tests monkeypatching app.routers.vendors."""
    from app.image_store import get_device_override as _get_device_override

    return _get_device_override(device_id)


def set_device_override(device_id: str, payload: dict) -> None:
    """Compatibility wrapper for tests monkeypatching app.routers.vendors."""
    from app.image_store import set_device_override as _set_device_override

    _set_device_override(device_id, payload)


def delete_device_override(device_id: str) -> bool:
    """Compatibility wrapper for tests monkeypatching app.routers.vendors."""
    from app.image_store import delete_device_override as _delete_device_override

    return _delete_device_override(device_id)


def _get_vendor_options(config) -> dict:
    """Compatibility wrapper for tests monkeypatching app.routers.vendors."""
    from agent.vendors import _get_vendor_options as _vendor_options

    return _vendor_options(config)


def catalog_is_seeded(database: Session) -> bool:
    """Compatibility wrapper for tests monkeypatching app.routers.vendors."""
    from app.services.catalog_service import catalog_is_seeded as _catalog_is_seeded

    return _catalog_is_seeded(database)


def count_catalog_images_for_device(database: Session, device_id: str) -> int:
    """Compatibility wrapper for tests monkeypatching app.routers.vendors."""
    from app.services.catalog_service import (
        count_catalog_images_for_device as _count_catalog_images_for_device,
    )

    return _count_catalog_images_for_device(database, device_id)


def _normalize_scope_token(value: object | None) -> str | None:
    from app.image_store import normalize_default_device_scope_id

    if value is None:
        return None
    return normalize_default_device_scope_id(str(value))


def _build_identity_map_from_registry() -> dict:
    """Build identity metadata from vendor registry + manifest compatibility aliases."""
    from agent.vendors import VENDOR_CONFIGS
    from app.image_store import get_image_compatibility_aliases, load_custom_devices

    canonical_to_runtime_kind: dict[str, str | None] = {}
    canonical_to_aliases: dict[str, set[str]] = defaultdict(set)
    alias_to_canonicals: dict[str, set[str]] = defaultdict(set)

    def register_alias(canonical: str, alias: object | None) -> None:
        normalized = _normalize_scope_token(alias)
        if not normalized or normalized == canonical:
            return
        canonical_to_aliases[canonical].add(normalized)
        alias_to_canonicals[normalized].add(canonical)

    for key, config in VENDOR_CONFIGS.items():
        canonical = _normalize_scope_token(key)
        if not canonical:
            continue
        runtime_kind = _normalize_scope_token(getattr(config, "kind", None))
        canonical_to_runtime_kind.setdefault(canonical, runtime_kind)
        register_alias(canonical, runtime_kind)
        for alias in getattr(config, "aliases", None) or []:
            register_alias(canonical, alias)

    compatibility_aliases = get_image_compatibility_aliases()
    for raw_canonical, aliases in compatibility_aliases.items():
        canonical = _normalize_scope_token(raw_canonical)
        if not canonical:
            continue
        canonical_to_runtime_kind.setdefault(canonical, canonical)
        for alias in aliases:
            register_alias(canonical, alias)

    for custom in load_custom_devices() or []:
        canonical = _normalize_scope_token(custom.get("id"))
        if not canonical:
            continue
        runtime_kind = _normalize_scope_token(custom.get("kind")) or canonical
        canonical_to_runtime_kind.setdefault(canonical, runtime_kind)
        register_alias(canonical, runtime_kind)

    canonical_to_aliases_sorted = {
        canonical: sorted(canonical_to_aliases.get(canonical, set()))
        for canonical in sorted(canonical_to_runtime_kind.keys())
    }
    alias_to_canonicals_sorted = {
        alias: sorted(canonicals)
        for alias, canonicals in sorted(alias_to_canonicals.items())
        if canonicals
    }

    interface_aliases: dict[str, str] = {
        canonical: canonical for canonical in canonical_to_runtime_kind.keys()
    }
    for alias, canonicals in alias_to_canonicals_sorted.items():
        if len(canonicals) == 1:
            interface_aliases[alias] = canonicals[0]

    return {
        "canonical_to_runtime_kind": canonical_to_runtime_kind,
        "canonical_to_aliases": canonical_to_aliases_sorted,
        "alias_to_canonicals": alias_to_canonicals_sorted,
        "interface_aliases": interface_aliases,
    }


def _get_identity_map(database: Session) -> dict:
    from app.services.catalog_service import (
        catalog_is_seeded,
        ensure_catalog_identity_synced,
        get_catalog_identity_map,
    )

    try:
        ensure_catalog_identity_synced(database, source="runtime_identity_sync")
        if catalog_is_seeded(database):
            return get_catalog_identity_map(database)
    except Exception:
        try:
            database.rollback()
        except Exception:
            pass
    return _build_identity_map_from_registry()


@router.get("")
def list_vendors(
    request: Request,
    database: Session = Depends(get_db),
) -> list[dict]:
    """Return vendor configurations for frontend device catalog.

    This endpoint provides a unified view of all supported network devices,
    including their categories, icons, versions, and availability status.
    Data is sourced from the centralized vendor registry in agent/vendors.py,
    merged with any custom device types defined per installation.
    Hidden devices are filtered out.
    """
    current_user = get_current_user_optional(request, database)
    if current_user is None and not catalog_is_seeded(database):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    from agent.vendors import get_vendors_for_ui
    from app.image_store import load_custom_devices

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
    custom_by_category: dict[str, list[dict]] = {}
    custom_devices = load_custom_devices()
    if custom_devices:
        # Group custom devices by category
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

    identity_map = _get_identity_map(database)
    canonical_to_aliases = identity_map.get("canonical_to_aliases", {})
    alias_to_canonicals = identity_map.get("alias_to_canonicals", {})
    canonical_to_runtime_kind = identity_map.get("canonical_to_runtime_kind", {})

    def enrich_model(model: dict) -> dict:
        device_id = _normalize_scope_token(model.get("id"))
        canonical_id = device_id
        if device_id and device_id not in canonical_to_aliases:
            matches = alias_to_canonicals.get(device_id, [])
            if len(matches) == 1:
                canonical_id = matches[0]

        aliases = set(canonical_to_aliases.get(canonical_id or "", []))
        if device_id:
            aliases.discard(device_id)
        model["compatibilityAliases"] = sorted(aliases)
        model["runtimeKind"] = canonical_to_runtime_kind.get(canonical_id or "")
        return model

    # Attach server-driven compatibility aliases to each model.
    for category in result:
        if "subCategories" in category:
            for subcategory in category["subCategories"]:
                subcategory["models"] = [enrich_model(model) for model in subcategory.get("models", [])]
        elif "models" in category:
            category["models"] = [enrich_model(model) for model in category.get("models", [])]

    return result


@router.get("/identity-map")
def get_identity_map(
    database: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """Return canonical/alias/runtime identity metadata for frontend resolution."""
    return _get_identity_map(database)


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
    device_id = payload.get("id")
    if not device_id:
        raise HTTPException(status_code=400, detail="Device ID is required")
    if not payload.get("name"):
        raise HTTPException(status_code=400, detail="Device name is required")

    # Check if device ID conflicts with vendor registry (including aliases and
    # cases where config key differs from runtime kind like c8000v).
    if get_config_by_device(device_id) is not None:
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
        validate_minimum_hardware(
            device_id,
            payload.get("memory"),
            payload.get("cpu"),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        device = store_add_device(payload)
        return {"device": device}
    except ValueError as e:
        msg = str(e)
        status = 409 if "already exists" in msg.lower() else 400
        raise HTTPException(status_code=status, detail=msg)


@router.delete("/{device_id}")
def delete_device(
    device_id: str,
    database: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """Delete or hide a device type.

    - Custom devices: Permanently deleted (only if no images assigned)
    - Built-in devices: Hidden from the UI (can be restored later)

    Both require no images to be assigned to the device.
    Accepts device IDs or aliases (e.g., 'eos' or 'ceos' both work for Arista EOS).
    """
    config = get_config_by_device(device_id)
    canonical_id = canonicalize_device_id(device_id) or device_id

    # Check if any images are assigned to this device.
    if catalog_is_seeded(database):
        image_count = count_catalog_images_for_device(database, canonical_id)
    else:
        image_count = get_device_image_count(canonical_id)
    if image_count > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete device with {image_count} assigned image(s). Unassign images first."
        )

    # Check if it's a built-in vendor device.
    if config is not None:
        if is_device_hidden(canonical_id):
            raise HTTPException(
                status_code=400,
                detail=f"Device '{canonical_id}' is already hidden"
            )
        hide_device(canonical_id)
        return {"message": f"Built-in device '{canonical_id}' hidden successfully"}

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
    config = get_config_by_device(device_id)
    canonical_id = canonicalize_device_id(device_id) or device_id

    # Check if it's a built-in vendor device.
    if config is None:
        raise HTTPException(
            status_code=400,
            detail="Only built-in devices can be restored"
        )

    # Check if it's actually hidden.
    if not is_device_hidden(canonical_id):
        raise HTTPException(
            status_code=400,
            detail=f"Device '{canonical_id}' is not hidden"
        )

    unhide_device(canonical_id)
    return {"message": f"Device '{canonical_id}' restored successfully"}


@router.get("/hidden")
def list_hidden_devices(
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """List all hidden built-in devices."""
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
    # Check if it's a built-in vendor device (including aliases).
    if get_config_by_device(device_id) is not None:
        raise HTTPException(
            status_code=400,
            detail="Cannot modify built-in vendor devices"
        )

    # Check if custom device exists
    if not find_custom_device(device_id):
        raise HTTPException(status_code=404, detail="Custom device not found")

    try:
        validate_minimum_hardware(
            device_id,
            payload.get("memory"),
            payload.get("cpu"),
        )
        updated = update_custom_device(device_id, payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
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
    base_config = {}

    # Check if it's a built-in vendor device.
    config = get_config_by_device(device_id)
    is_built_in = config is not None
    resolved_device_id = (
        (canonicalize_device_id(device_id) or device_id) if is_built_in else device_id
    )

    if config:
        base_config = {
            "id": resolved_device_id,
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
            "managementInterface": getattr(config, "management_interface", None),
            "memory": config.memory,
            "cpu": config.cpu,
            "diskDriver": getattr(config, "disk_driver", None),
            "nicDriver": getattr(config, "nic_driver", None),
            "machineType": getattr(config, "machine_type", None),
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

    # Built-in overrides are keyed by canonical device ID.
    overrides = get_device_override(resolved_device_id) or {}

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
    # Allowed override fields
    ALLOWED_OVERRIDE_FIELDS = {
        "memory", "cpu", "maxPorts", "portNaming", "portStartIndex",
        "readinessTimeout", "vendorOptions",
        "diskDriver", "nicDriver", "machineType",
    }

    # Filter payload to only allowed fields
    filtered_payload = {k: v for k, v in payload.items() if k in ALLOWED_OVERRIDE_FIELDS}

    if not filtered_payload:
        raise HTTPException(
            status_code=400,
            detail=f"No valid override fields provided. Allowed: {', '.join(ALLOWED_OVERRIDE_FIELDS)}"
        )

    # Verify device exists.
    is_built_in = get_config_by_device(device_id) is not None
    if not is_built_in:
        custom = find_custom_device(device_id)
        if not custom:
            raise HTTPException(status_code=404, detail="Device not found")
    override_device_id = (
        (canonicalize_device_id(device_id) or device_id) if is_built_in else device_id
    )

    # Set override
    try:
        validate_minimum_hardware(
            device_id,
            filtered_payload.get("memory"),
            filtered_payload.get("cpu"),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    set_device_override(override_device_id, filtered_payload)

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
    # Verify device exists.
    is_built_in = get_config_by_device(device_id) is not None
    if not is_built_in:
        custom = find_custom_device(device_id)
        if not custom:
            raise HTTPException(status_code=404, detail="Device not found")
    override_device_id = (
        (canonicalize_device_id(device_id) or device_id) if is_built_in else device_id
    )

    # Delete override.
    deleted = delete_device_override(override_device_id)
    if not deleted:
        return {"message": f"Device '{override_device_id}' has no overrides to reset"}

    return {"message": f"Device '{override_device_id}' reset to defaults"}
