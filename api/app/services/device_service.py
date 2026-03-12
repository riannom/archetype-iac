"""Device service for managing vendor configurations and custom devices.

This service encapsulates all device/vendor-related business logic,
extracted from main.py to improve maintainability and testability.

Includes:
- Hardware safety constraints (minimum requirements for memory-intensive devices)
- Unified device identity resolution (DeviceResolver)
- Vendor configuration management (DeviceService)

Usage:
    from app.services.device_service import DeviceService

    service = DeviceService()
    vendors = service.list_vendors()
    device = service.add_custom_device(payload)
"""
from __future__ import annotations

import functools
import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Device hardware constraints
# ---------------------------------------------------------------------------

CAT9K_MIN_MEMORY_MB = 18432
CAT9K_MIN_CPU = 4


def _normalize_device_id(device_id: str | None) -> str:
    return (device_id or "").strip().lower().replace("_", "-")


def is_cat9k_memory_intensive(device_id: str | None) -> bool:
    """Return True for Cat9k variants known to require high memory."""
    normalized = _normalize_device_id(device_id)
    if not normalized:
        return False
    return bool(
        re.search(r"(cat9000v|cat9kv)", normalized)
        and re.search(r"(uadp|q200|cat9kv)", normalized)
    )


def minimum_hardware_for_device(device_id: str | None) -> dict[str, int] | None:
    """Return minimum hardware requirements for a device, if constrained."""
    if is_cat9k_memory_intensive(device_id):
        return {"memory": CAT9K_MIN_MEMORY_MB, "cpu": CAT9K_MIN_CPU}
    return None


def validate_minimum_hardware(device_id: str | None, memory: int | None, cpu: int | None) -> None:
    """Raise ValueError when provided hardware is below required minimums."""
    minimums = minimum_hardware_for_device(device_id)
    if not minimums:
        return

    violations: list[str] = []
    if memory is not None and memory < minimums["memory"]:
        violations.append(f"memory={memory}MB < required {minimums['memory']}MB")
    if cpu is not None and cpu < minimums["cpu"]:
        violations.append(f"cpu={cpu} < required {minimums['cpu']}")

    if violations:
        raise ValueError(
            f"Device '{device_id}' is memory intensive and cannot run below minimums: "
            + ", ".join(violations)
        )


# ---------------------------------------------------------------------------
# Device identity resolver
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ResolvedDevice:
    """Result of device identity resolution."""

    # The canonical VENDOR_CONFIGS key (e.g., "ceos", "cisco_n9kv").
    # For custom/unknown devices, this is the normalized input.
    canonical_id: str
    # The VENDOR_CONFIGS key if the device is a built-in vendor device, else None.
    vendor_config_key: str | None
    # Runtime kind (from VendorConfig.kind). Same as vendor_config_key for most entries.
    kind: str | None
    # Vendor display name (e.g., "Arista", "Cisco").
    vendor: str | None
    # True if this is a custom (user-created) device, not in VENDOR_CONFIGS.
    is_custom: bool


class DeviceResolver:
    """Unified device identity resolver.

    Resolution order (explicit, documented):
      1. Exact VENDOR_CONFIGS key match
      2. Derived alias chain resolution (kinds + aliases)
      3. Custom device lookup
      4. Return unresolved (canonical_id = normalized input)
    """

    def __init__(self) -> None:
        self._vendor_configs: dict | None = None
        self._alias_map: dict[str, str] | None = None
        self._vendor_map: dict[str, str] | None = None

    def _ensure_loaded(self) -> None:
        """Lazy-load VENDOR_CONFIGS and derived maps."""
        if self._vendor_configs is not None:
            return
        try:
            from agent.vendors import (
                VENDOR_CONFIGS,
                _DERIVED_DEVICE_ID_ALIASES,
                _DERIVED_DEVICE_VENDOR_MAP,
            )
            self._vendor_configs = VENDOR_CONFIGS
            self._alias_map = _DERIVED_DEVICE_ID_ALIASES
            self._vendor_map = _DERIVED_DEVICE_VENDOR_MAP
        except ImportError:
            self._vendor_configs = {}
            self._alias_map = {}
            self._vendor_map = {}

    @functools.lru_cache(maxsize=512)
    def resolve(self, device_id: str | None) -> ResolvedDevice:
        """Resolve a device identifier to its canonical form.

        Args:
            device_id: Any known device identifier (key, kind, alias, custom ID).

        Returns:
            ResolvedDevice with resolved metadata.
        """
        if not device_id:
            return ResolvedDevice(
                canonical_id="",
                vendor_config_key=None,
                kind=None,
                vendor=None,
                is_custom=False,
            )

        self._ensure_loaded()
        assert self._vendor_configs is not None
        assert self._alias_map is not None
        assert self._vendor_map is not None

        normalized = device_id.strip().lower()

        # 1. Check derived alias map (covers keys, kinds, and aliases).
        resolved_key = self._alias_map.get(normalized)
        if resolved_key and resolved_key in self._vendor_configs:
            cfg = self._vendor_configs[resolved_key]
            return ResolvedDevice(
                canonical_id=resolved_key,
                vendor_config_key=resolved_key,
                kind=cfg.kind,
                vendor=cfg.vendor,
                is_custom=False,
            )

        # 2. Direct VENDOR_CONFIGS key match (shouldn't miss if alias map is complete).
        if normalized in self._vendor_configs:
            cfg = self._vendor_configs[normalized]
            return ResolvedDevice(
                canonical_id=normalized,
                vendor_config_key=normalized,
                kind=cfg.kind,
                vendor=cfg.vendor,
                is_custom=False,
            )

        # 3. Custom device lookup.
        try:
            from app.image_store import find_custom_device
            custom = find_custom_device(device_id)
            if custom:
                return ResolvedDevice(
                    canonical_id=device_id,
                    vendor_config_key=None,
                    kind=custom.get("kind"),
                    vendor=custom.get("vendor"),
                    is_custom=True,
                )
        except Exception:
            pass

        # 4. Unresolved — return normalized input.
        vendor = self._vendor_map.get(normalized)
        return ResolvedDevice(
            canonical_id=normalized,
            vendor_config_key=None,
            kind=None,
            vendor=vendor,
            is_custom=False,
        )

    def resolve_config(self, device_id: str | None):
        """Resolve a device identifier to its VendorConfig.

        Args:
            device_id: Any known device identifier.

        Returns:
            VendorConfig if found, None otherwise.
        """
        if not device_id:
            return None
        self._ensure_loaded()
        assert self._vendor_configs is not None

        resolved = self.resolve(device_id)
        if resolved.vendor_config_key:
            return self._vendor_configs.get(resolved.vendor_config_key)
        return None


# Module-level singleton for convenience.
_resolver: DeviceResolver | None = None


def get_resolver() -> DeviceResolver:
    """Get the module-level DeviceResolver singleton."""
    global _resolver
    if _resolver is None:
        _resolver = DeviceResolver()
    return _resolver


# ---------------------------------------------------------------------------
# Device service
# ---------------------------------------------------------------------------


def _get_config_by_kind(device_id: str):
    """Module-level helper kept patchable for tests."""
    from agent.vendors import _get_config_by_kind as vendor_get_config_by_kind

    return vendor_get_config_by_kind(device_id)


def get_kind_for_device(device_id: str) -> str:
    """Module-level helper kept patchable for tests."""
    from agent.vendors import get_kind_for_device as vendor_get_kind_for_device

    return vendor_get_kind_for_device(device_id)


def get_config_by_device(device_id: str):
    """Module-level helper kept patchable for tests.

    Uses legacy patchable helpers first so existing tests can override behavior
    without patching agent.vendors directly.
    """
    config = _get_config_by_kind(device_id)
    if config:
        return config

    canonical = get_kind_for_device(device_id)
    if canonical and canonical != device_id:
        config = _get_config_by_kind(canonical)
        if config:
            return config

    return None


def find_custom_device(device_id: str):
    """Module-level helper kept patchable for tests."""
    from app.image_store import find_custom_device as image_store_find_custom_device

    return image_store_find_custom_device(device_id)


def get_device_override(device_id: str):
    """Module-level helper kept patchable for tests."""
    from app.image_store import get_device_override as image_store_get_device_override

    return image_store_get_device_override(device_id)


def get_image_runtime_metadata(image_reference: str | None) -> dict:
    """Get runtime metadata hints for an image reference from manifest."""
    if not image_reference:
        return {}
    try:
        from app.image_store import find_image_by_id, find_image_by_reference, load_manifest

        manifest = load_manifest()
        image = find_image_by_reference(manifest, image_reference)
        if not image:
            image = find_image_by_id(manifest, image_reference)

        # Accept ID-like or basename-only values and map them back to manifest refs.
        # This keeps metadata resolution stable even when node.image stores an image ID.
        if not image:
            candidate_keys = {image_reference, Path(image_reference).name}
            if ":" in image_reference and "/" not in image_reference:
                candidate_keys.add(image_reference.split(":", 1)[1])
            for item in manifest.get("images", []):
                item_id = str(item.get("id") or "")
                item_ref = str(item.get("reference") or "")
                item_ref_name = Path(item_ref).name if item_ref else ""
                if (
                    item_id in candidate_keys
                    or item_ref in candidate_keys
                    or item_ref_name in candidate_keys
                ):
                    image = item
                    break
        image = image or {}
    except Exception:
        return {}

    return {
        "memory": image.get("memory_mb"),
        "cpu": image.get("cpu_count"),
        "cpu_limit": image.get("cpu_limit"),
        "max_ports": image.get("max_ports"),
        "port_naming": image.get("port_naming"),
        "disk_driver": image.get("disk_driver"),
        "nic_driver": image.get("nic_driver"),
        "machine_type": image.get("machine_type"),
        "libvirt_driver": image.get("libvirt_driver"),
        "readiness_timeout": image.get("boot_timeout"),
        "readiness_probe": image.get("readiness_probe"),
        "readiness_pattern": image.get("readiness_pattern"),
        "efi_boot": image.get("efi_boot"),
        "efi_vars": image.get("efi_vars"),
    }


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
        from agent.vendors import VENDOR_CONFIGS, get_vendors_for_ui
        from app.image_store import (
            get_image_compatibility_aliases,
            load_custom_devices,
            load_hidden_devices,
            normalize_default_device_scope_id,
        )

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

        self._attach_compatibility_aliases(
            result,
            VENDOR_CONFIGS,
            get_image_compatibility_aliases(),
            normalize_default_device_scope_id,
        )
        return result

    def _attach_compatibility_aliases(
        self,
        categories: list[dict],
        vendor_configs: dict,
        compatibility_aliases: dict[str, list[str]],
        normalize_scope,
    ) -> None:
        """Attach server-driven compatibility aliases to each device model."""

        def enrich_model(model: dict) -> dict:
            device_id = normalize_scope(model.get("id"))
            aliases: set[str] = set()
            if device_id:
                aliases.update(compatibility_aliases.get(device_id, []))
                config = vendor_configs.get(device_id)
                if config:
                    kind = normalize_scope(config.kind)
                    if kind and kind != device_id:
                        aliases.add(kind)
                    for alias in config.aliases or []:
                        normalized = normalize_scope(alias)
                        if normalized:
                            aliases.add(normalized)
            model["compatibilityAliases"] = sorted(aliases)
            return model

        for category in categories:
            if "subCategories" in category:
                for subcategory in category["subCategories"]:
                    subcategory["models"] = [enrich_model(model) for model in subcategory.get("models", [])]
            elif "models" in category:
                category["models"] = [enrich_model(model) for model in category.get("models", [])]

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

        device_id = payload.get("id")
        if not device_id:
            raise DeviceValidationError("Device ID is required")
        if not payload.get("name"):
            raise DeviceValidationError("Device name is required")

        # Check if device ID conflicts with vendor registry
        if get_config_by_device(device_id) is not None:
            raise DeviceConflictError(
                f"Device ID '{device_id}' conflicts with built-in vendor registry"
            )

        # Check if already exists as custom device
        if find_custom_device(device_id):
            raise DeviceConflictError(f"Custom device '{device_id}' already exists")

        try:
            return store_add_device(payload)
        except ValueError as e:
            message = str(e)
            if "already exists" in message.lower():
                raise DeviceConflictError(message)
            raise DeviceValidationError(message)

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

        # Check if it's a built-in vendor device
        if get_config_by_device(device_id) is not None:
            raise DeviceValidationError("Cannot modify built-in vendor devices")

        # Check if custom device exists
        if not find_custom_device(device_id):
            raise DeviceNotFoundError(f"Custom device '{device_id}' not found")

        try:
            updated = update_custom_device(device_id, payload)
            if not updated:
                raise DeviceNotFoundError(f"Device '{device_id}' not found")
        except ValueError as e:
            raise DeviceValidationError(str(e))

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
            canonicalize_device_id,
            find_custom_device,
            delete_custom_device as store_delete_device,
            get_device_image_count,
            hide_device,
            is_device_hidden,
        )
        canonical_id = canonicalize_device_id(device_id) or device_id
        config = get_config_by_device(device_id)

        # Check if any images are assigned
        image_count = get_device_image_count(canonical_id)
        if image_count > 0:
            raise DeviceHasImagesError(
                f"Cannot delete device with {image_count} assigned image(s)"
            )

        # Check if it's a built-in vendor device
        if config is not None:
            if is_device_hidden(canonical_id):
                raise DeviceValidationError(f"Device '{canonical_id}' is already hidden")
            hide_device(canonical_id)
            return {"message": f"Built-in device '{canonical_id}' hidden successfully"}

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
        from app.image_store import canonicalize_device_id, find_custom_device, get_device_override
        from agent.vendors import _get_vendor_options

        base_config = {}

        # Check if it's a built-in vendor device
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
                "memory": config.memory,
                "cpu": config.cpu,
                "diskDriver": config.disk_driver,
                "nicDriver": config.nic_driver,
                "machineType": config.machine_type,
                "libvirtDriver": "kvm" if "qcow2" in (getattr(config, "supported_image_kinds", []) or []) else None,
                "efiBoot": None,
                "efiVars": None,
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

        # Built-in overrides are keyed by canonical device ID.
        overrides = get_device_override(resolved_device_id) or {}

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
        from app.image_store import canonicalize_device_id, find_custom_device, set_device_override

        # Allowed override fields
        ALLOWED_OVERRIDE_FIELDS = {
            "memory", "cpu", "maxPorts", "portNaming", "portStartIndex",
            "readinessTimeout", "vendorOptions",
            "cpuLimit",
            "diskDriver", "nicDriver", "machineType",
            "libvirtDriver",
            "efiBoot", "efiVars",
        }

        # Filter payload to only allowed fields
        filtered_payload = {
            k: v for k, v in payload.items() if k in ALLOWED_OVERRIDE_FIELDS
        }

        if not filtered_payload:
            raise DeviceValidationError(
                f"No valid override fields provided. Allowed: {', '.join(ALLOWED_OVERRIDE_FIELDS)}"
            )

        # Verify device exists
        is_built_in = get_config_by_device(device_id) is not None
        if not is_built_in:
            custom = find_custom_device(device_id)
            if not custom:
                raise DeviceNotFoundError(f"Device '{device_id}' not found")
        override_device_id = (
            (canonicalize_device_id(device_id) or device_id) if is_built_in else device_id
        )

        # Set override
        try:
            validate_minimum_hardware(
                override_device_id,
                filtered_payload.get("memory"),
                filtered_payload.get("cpu"),
            )
        except ValueError as e:
            raise DeviceValidationError(str(e))
        set_device_override(override_device_id, filtered_payload)

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
        from app.image_store import canonicalize_device_id, delete_device_override, find_custom_device

        # Verify device exists
        is_built_in = get_config_by_device(device_id) is not None
        if not is_built_in:
            custom = find_custom_device(device_id)
            if not custom:
                raise DeviceNotFoundError(f"Device '{device_id}' not found")
        override_device_id = (
            (canonicalize_device_id(device_id) or device_id) if is_built_in else device_id
        )

        # Delete override
        deleted = delete_device_override(override_device_id)
        if not deleted:
            return {"message": f"Device '{override_device_id}' has no overrides to reset"}

        return {"message": f"Device '{override_device_id}' reset to defaults"}

    def resolve_hardware_specs(
        self,
        device_id: str,
        node_config_json: dict | None = None,
        image_reference: str | None = None,
        version: str | None = None,
    ) -> dict:
        """Resolve hardware specs for a device. Per-node overrides > device definition > defaults.

        Args:
            device_id: Device type identifier (e.g., "cat9000v-uadp", "ceos")
            node_config_json: Per-node config_json dict with optional hardware overrides

        Returns:
            Dict with resolved memory, cpu, cpu_limit, max_ports, port_naming, disk_driver,
            nic_driver, machine_type, libvirt_driver, efi_boot, efi_vars
            (keys present only when values are non-default / explicitly set)
        """
        from app.image_store import canonicalize_device_id, find_image_reference

        specs: dict = {}

        # Layer 1: Built-in vendor config
        config = get_config_by_device(device_id)

        if config:
            specs["memory"] = config.memory
            specs["cpu"] = config.cpu
            specs["max_ports"] = config.max_ports
            specs["port_naming"] = config.port_naming
            specs["disk_driver"] = config.disk_driver
            specs["nic_driver"] = config.nic_driver
            specs["machine_type"] = config.machine_type
            if "qcow2" in (getattr(config, "supported_image_kinds", []) or []):
                specs["libvirt_driver"] = "kvm"
            specs["readiness_probe"] = config.readiness_probe
            specs["readiness_pattern"] = config.readiness_pattern
            specs["readiness_timeout"] = config.readiness_timeout
            specs["efi_boot"] = config.efi_boot
            specs["efi_vars"] = config.efi_vars
            if config.data_volume_gb:
                specs["data_volume_gb"] = config.data_volume_gb
        else:
            # Layer 1b: Custom device definition
            custom = find_custom_device(device_id)
            if custom:
                for field in ("memory", "cpu", "maxPorts", "portNaming", "diskDriver", "nicDriver", "machineType", "libvirtDriver", "efiBoot", "efiVars", "dataVolumeGb"):
                    val = custom.get(field)
                    if val is not None:
                        # Normalize camelCase to snake_case for API consistency
                        key = {
                            "maxPorts": "max_ports",
                            "portNaming": "port_naming",
                            "diskDriver": "disk_driver",
                            "nicDriver": "nic_driver",
                            "machineType": "machine_type",
                            "libvirtDriver": "libvirt_driver",
                            "efiBoot": "efi_boot",
                            "efiVars": "efi_vars",
                            "dataVolumeGb": "data_volume_gb",
                        }.get(field, field)
                        specs[key] = val

        # Layer 1c: Image metadata (for example VIRL2 node-definition defaults)
        # should override internal vendor defaults when present.
        metadata_image_reference = image_reference
        if not metadata_image_reference and version:
            candidates = [device_id]
            canonical = canonicalize_device_id(device_id)
            if canonical and canonical not in candidates:
                candidates.append(canonical)
            for candidate in candidates:
                if not candidate:
                    continue
                metadata_image_reference = find_image_reference(candidate, version)
                if metadata_image_reference:
                    break

        image_meta = get_image_runtime_metadata(metadata_image_reference)
        vendor_probe_none = config and getattr(config, "readiness_probe", None) == "none"
        for key in ("memory", "cpu", "cpu_limit", "max_ports", "port_naming", "disk_driver", "nic_driver", "machine_type", "libvirt_driver", "readiness_timeout", "readiness_probe", "readiness_pattern", "efi_boot", "efi_vars", "data_volume_gb"):
            if vendor_probe_none and key in ("readiness_probe", "readiness_pattern"):
                continue
            val = image_meta.get(key)
            if val is not None:
                specs[key] = val

        # Layer 2: Device overrides (device_overrides.json)
        overrides = get_device_override(device_id) or {}
        for field, key in [("memory", "memory"), ("cpu", "cpu"),
                           ("cpuLimit", "cpu_limit"),
                           ("maxPorts", "max_ports"), ("portNaming", "port_naming"),
                           ("diskDriver", "disk_driver"), ("nicDriver", "nic_driver"),
                           ("machineType", "machine_type"), ("libvirtDriver", "libvirt_driver"),
                           ("readinessProbe", "readiness_probe"), ("readinessPattern", "readiness_pattern"),
                           ("readinessTimeout", "readiness_timeout"),
                           ("efiBoot", "efi_boot"),
                           ("efiVars", "efi_vars"),
                           ("dataVolumeGb", "data_volume_gb")]:
            val = overrides.get(field)
            if val is not None:
                specs[key] = val

        # Layer 3: Per-node config_json overrides (highest priority)
        if node_config_json:
            for key in ("memory", "cpu", "cpu_limit", "max_ports", "port_naming", "disk_driver", "nic_driver", "machine_type", "libvirt_driver", "readiness_probe", "readiness_pattern", "readiness_timeout", "efi_boot", "efi_vars", "data_volume_gb"):
                val = node_config_json.get(key)
                if val is not None:
                    specs[key] = val

        try:
            validate_minimum_hardware(device_id, specs.get("memory"), specs.get("cpu"))
        except ValueError as e:
            raise DeviceValidationError(str(e))

        return specs

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
        from app.image_store import canonicalize_device_id, hide_device, is_device_hidden

        canonical_id = canonicalize_device_id(device_id) or device_id
        if get_config_by_device(device_id) is None:
            raise DeviceValidationError("Only built-in devices can be hidden")

        if is_device_hidden(canonical_id):
            raise DeviceValidationError(f"Device '{canonical_id}' is already hidden")

        hide_device(canonical_id)
        return {"message": f"Device '{canonical_id}' hidden successfully"}


# Singleton instance
_device_service: DeviceService | None = None


def get_device_service() -> DeviceService:
    """Get the device service singleton."""
    global _device_service
    if _device_service is None:
        _device_service = DeviceService()
    return _device_service
