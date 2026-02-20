from __future__ import annotations

import functools
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import logging
import re
from typing import Optional

from app.config import settings
from app.services.device_constraints import validate_minimum_hardware

logger = logging.getLogger(__name__)

# =============================================================================
# QCOW2 DEVICE DETECTION FOR VRNETLAB BUILDS
# =============================================================================

# Derived from VENDOR_CONFIGS — single source of truth.
# Additional legacy patterns for devices not yet in VENDOR_CONFIGS are kept below.
def _build_qcow2_device_patterns() -> dict[str, tuple[str, str]]:
    """Build qcow2 detection patterns from VENDOR_CONFIGS + legacy entries."""
    try:
        from agent.vendors import _DERIVED_QCOW2_DEVICE_PATTERNS
        patterns = dict(_DERIVED_QCOW2_DEVICE_PATTERNS)
    except ImportError:
        patterns = {}

    # Legacy patterns for devices without VENDOR_CONFIGS entries.
    # These will be removed once corresponding entries are added.
    _LEGACY_QCOW2_PATTERNS: dict[str, tuple[str, str]] = {
        r"cat9kv[_-]?[\d\.]+.*\.qcow2": ("cat9kv", "cisco/cat9kv"),
        r"c8000v[_-]?sdwan.*\.qcow2": ("cat-sdwan-cedge", "cisco/sdwan"),
        r"vmx.*\.qcow2": ("vmx", "juniper/vmx"),
        r"vqfx.*\.qcow2": ("juniper_vqfx", "juniper/vqfx"),
        r"veos[_-]?[\d\.]+.*\.qcow2": ("veos", "arista/veos"),
        r"sros[_-]?[\d\.]+.*\.qcow2": ("sros", "nokia/sros"),
    }
    for pattern, value in _LEGACY_QCOW2_PATTERNS.items():
        if pattern not in patterns:
            patterns[pattern] = value

    return patterns


QCOW2_DEVICE_PATTERNS: dict[str, tuple[str, str]] = _build_qcow2_device_patterns()


def detect_iol_device_type(filename: str) -> str | None:
    """Detect IOL device type from filename.

    Args:
        filename: The IOL filename (e.g., "i86bi-linux-l3-adventerprisek9-15.6.1T.bin")

    Returns:
        Device ID ("iol-xe" or "iol-l2") or None if not recognized as IOL.
    """
    name = filename.lower()
    if "l2-" in name or "ioll2" in name or "iol_l2" in name:
        return "iol-l2"
    if "l3-" in name or "iol" in name:
        return "iol-xe"
    return None


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


# Vendor mapping derived from VENDOR_CONFIGS. Legacy fallbacks included for
# identifiers that VENDOR_CONFIGS doesn't cover yet.
def _build_device_vendor_map() -> dict[str, str]:
    """Build device-to-vendor mapping from VENDOR_CONFIGS + legacy entries."""
    try:
        from agent.vendors import _DERIVED_DEVICE_VENDOR_MAP
        vendor_map = dict(_DERIVED_DEVICE_VENDOR_MAP)
    except ImportError:
        vendor_map = {}

    _LEGACY_VENDOR_MAP = {
        "crpd": "Juniper",
        "vqfx": "Juniper",
        "juniper_vqfx": "Juniper",
        "srlinux": "Nokia",
        "cumulus": "NVIDIA",
        "sonic": "SONiC",
    }
    for k, v in _LEGACY_VENDOR_MAP.items():
        vendor_map.setdefault(k, v)

    return vendor_map


DEVICE_VENDOR_MAP: dict[str, str] = _build_device_vendor_map()

# Device ID aliases derived from VENDOR_CONFIGS (keys, kinds, and explicit aliases).
# Maps any known identifier to its canonical VENDOR_CONFIGS key.
def _build_device_id_aliases() -> dict[str, str]:
    """Build device-id alias map from VENDOR_CONFIGS."""
    try:
        from agent.vendors import _DERIVED_DEVICE_ID_ALIASES
        return dict(_DERIVED_DEVICE_ID_ALIASES)
    except ImportError:
        return {}


DEVICE_ID_ALIASES: dict[str, str] = _build_device_id_aliases()

# Explicit compatibility aliases for device types that intentionally share
# image artifacts while remaining separate draggable device IDs.
IMAGE_COMPAT_ALIASES: dict[str, list[str]] = {
    "cat9000v-uadp": ["cisco_cat9kv"],
    "cat9000v-q200": ["cisco_cat9kv"],
    "cat9000v_uadp": ["cisco_cat9kv"],
    "cat9000v_q200": ["cisco_cat9kv"],
}


def normalize_default_device_scope_id(device_id: str | None) -> str | None:
    """Normalize per-device default scope key.

    Unlike canonicalize_device_id, this keeps distinct UI device IDs separate
    (for example cat9800 vs cat9000v-uadp) so defaults can be independent.
    """
    if not device_id:
        return None
    normalized = device_id.strip().lower()
    return normalized or None


def normalize_default_device_scope_ids(device_ids: list[str] | None) -> list[str]:
    """Normalize and deduplicate per-device default scope keys."""
    if not device_ids:
        return []

    result: list[str] = []
    seen: set[str] = set()
    for device_id in device_ids:
        normalized = normalize_default_device_scope_id(device_id)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


@functools.lru_cache(maxsize=256)
def canonicalize_device_id(device_id: str | None) -> str | None:
    """Normalize an image device ID to a canonical draggable device key.

    Image assignment should align with UI device IDs (same IDs used in the
    draggable device catalog), not broad runtime kinds.

    Results are cached — the function is pure for a given module load.
    """
    if not device_id:
        return None

    normalized = device_id.strip().lower()

    # Fast path: the derived alias map covers keys, kinds, and aliases.
    resolved = DEVICE_ID_ALIASES.get(normalized)
    if resolved:
        return resolved

    # Fallback: try VENDOR_CONFIGS directly for any edge cases.
    try:
        from agent.vendors import VENDOR_CONFIGS

        if normalized in VENDOR_CONFIGS:
            return normalized

        alias_matches = [
            key
            for key, config in VENDOR_CONFIGS.items()
            if normalized in {alias.lower() for alias in (config.aliases or [])}
        ]
        if len(alias_matches) == 1:
            return alias_matches[0]

        kind_matches = [
            key
            for key, config in VENDOR_CONFIGS.items()
            if (config.kind or "").lower() == normalized
        ]
        if len(kind_matches) == 1:
            return kind_matches[0]
    except ImportError:
        pass
    except Exception:
        logger.warning(
            "Unexpected error during device ID canonicalization for '%s'",
            device_id,
            exc_info=True,
        )

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


def _maybe_backfill_specific_linux_device(image: dict) -> str | None:
    """Migrate legacy generic-linux assignments for known container images.

    Historical manifests collapsed several Linux-family device types into
    device_id='linux'. When image metadata clearly identifies a distinct
    draggable type, backfill it during normalization.
    """
    current = normalize_default_device_scope_id(image.get("device_id"))
    if current != "linux":
        return image.get("device_id")

    haystack = " ".join(
        [
            str(image.get("id") or ""),
            str(image.get("reference") or ""),
            str(image.get("filename") or ""),
        ]
    ).lower()
    if "frr" in haystack:
        return "frr"
    if "haproxy" in haystack:
        return "haproxy"
    if "tcl" in haystack or "tiny core" in haystack:
        return "tcl"
    if "alpine" in haystack:
        return "alpine"
    return image.get("device_id")


def _maybe_backfill_vjunos_evolved_device(image: dict, device_id: str | None) -> str | None:
    """Migrate legacy vJunos Evolved assignments from router ID to evolved ID."""
    current = normalize_default_device_scope_id(device_id)
    if current not in {"juniper_vjunosrouter", "vjunos-router", "vjunosrouter"}:
        return device_id

    haystack = " ".join(
        [
            str(image.get("id") or ""),
            str(image.get("reference") or ""),
            str(image.get("filename") or ""),
        ]
    ).lower()
    if "vjunos-evolved" in haystack or "vjunos_evolved" in haystack or "vjunosevolved" in haystack:
        return "juniper_vjunosevolved"
    return device_id


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


def iol_path(filename: str) -> Path:
    return ensure_image_store() / filename


def manifest_path() -> Path:
    return ensure_image_store() / "manifest.json"


RUNNABLE_IMAGE_KINDS = {"docker", "qcow2"}


def _normalize_manifest_images(manifest: dict) -> None:
    """Canonicalize image metadata and backfill sensible default flags."""
    images = manifest.get("images", [])
    if not isinstance(images, list):
        return

    for image in images:
        if not isinstance(image, dict):
            continue

        raw_device_id = _maybe_backfill_specific_linux_device(image)
        raw_device_id = _maybe_backfill_vjunos_evolved_device(image, raw_device_id)
        raw_compatible_devices = list(image.get("compatible_devices") or [])

        # If a legacy Linux assignment is clearly a distinct device type and
        # compatibility only listed linux, shift compatibility to the inferred type.
        inferred_scope = normalize_default_device_scope_id(raw_device_id)
        compat_scopes = {
            normalize_default_device_scope_id(dev) for dev in raw_compatible_devices if dev
        }
        if inferred_scope in {"frr", "haproxy", "alpine", "tcl"} and compat_scopes <= {"linux"}:
            raw_compatible_devices = [raw_device_id]

        canonical_device_id = canonicalize_device_id(raw_device_id)
        compatible_devices = canonicalize_device_ids(raw_compatible_devices)
        if canonical_device_id and canonical_device_id not in compatible_devices:
            compatible_devices.append(canonical_device_id)

        image["device_id"] = canonical_device_id
        image["compatible_devices"] = compatible_devices
        if canonical_device_id:
            image["vendor"] = get_vendor_for_device(canonical_device_id)
        default_for_devices = normalize_default_device_scope_ids(image.get("default_for_devices") or [])
        if not default_for_devices and image.get("is_default") and canonical_device_id:
            # Legacy manifest entries only tracked a single boolean default.
            scope = normalize_default_device_scope_id(canonical_device_id)
            default_for_devices = [scope] if scope else []
        image["default_for_devices"] = default_for_devices
        image["is_default"] = bool(default_for_devices)

    _backfill_single_image_defaults(manifest)


def _backfill_single_image_defaults(manifest: dict) -> None:
    """Mark a runnable image default when it is the only match for a device."""
    images = [img for img in manifest.get("images", []) if isinstance(img, dict)]
    if not images:
        return

    candidate_devices: set[str] = set()
    for image in images:
        kind = str(image.get("kind") or "").lower()
        if kind not in RUNNABLE_IMAGE_KINDS:
            continue
        for device_id in image.get("compatible_devices") or []:
            canonical = canonicalize_device_id(device_id)
            if canonical:
                candidate_devices.add(canonical)

    for device_id in candidate_devices:
        matches = [
            image
            for image in images
            if str(image.get("kind") or "").lower() in RUNNABLE_IMAGE_KINDS
            and image_matches_device(image, device_id)
        ]
        if len(matches) == 1:
            scopes = normalize_default_device_scope_ids(matches[0].get("default_for_devices") or [])
            scope = normalize_default_device_scope_id(device_id)
            if scope and scope not in scopes:
                scopes.append(scope)
                matches[0]["default_for_devices"] = scopes
            matches[0]["is_default"] = bool(matches[0].get("default_for_devices"))


def load_manifest() -> dict:
    path = manifest_path()
    if not path.exists():
        return {"images": []}
    manifest = json.loads(path.read_text(encoding="utf-8"))
    _normalize_manifest_images(manifest)
    return manifest


def save_manifest(data: dict) -> None:
    _normalize_manifest_images(data)
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
) -> Optional[dict]:
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


def _device_compatibility_tokens(device_id: str | None) -> set[str]:
    """Return normalized matching tokens for a device ID."""
    canonical = canonicalize_device_id(device_id)
    if not canonical:
        return set()

    tokens = {canonical}

    # Direct aliases: target -> legacy/shared IDs.
    for alias in IMAGE_COMPAT_ALIASES.get(canonical, []):
        normalized = canonicalize_device_id(alias)
        if normalized:
            tokens.add(normalized)

    # Reverse aliases: legacy/shared ID -> one or more target device IDs.
    for target, aliases in IMAGE_COMPAT_ALIASES.items():
        if canonical in aliases:
            normalized_target = canonicalize_device_id(target)
            if normalized_target:
                tokens.add(normalized_target)

    return tokens


def image_matches_device(image: dict, device_id: str) -> bool:
    """Check if an image matches a device via device_id or compatible_devices.

    Device IDs are normalized to draggable device IDs. Matching also applies
    explicit compatibility aliases for known shared-image families.
    """
    target_tokens = _device_compatibility_tokens(device_id)
    if not target_tokens:
        return False

    image_tokens: set[str] = set()
    image_tokens.update(_device_compatibility_tokens(image.get("device_id")))
    for cd in image.get("compatible_devices") or []:
        image_tokens.update(_device_compatibility_tokens(cd))

    return bool(image_tokens.intersection(target_tokens))


def get_image_default_device_scopes(image: dict) -> list[str]:
    """Return normalized per-device default scopes for an image entry."""
    scopes = normalize_default_device_scope_ids(image.get("default_for_devices") or [])
    if scopes:
        return scopes

    # Legacy fallback: boolean default scoped to primary device_id.
    if image.get("is_default"):
        scope = normalize_default_device_scope_id(image.get("device_id"))
        if scope:
            return [scope]
    return []


def is_image_default_for_device(image: dict, device_id: str) -> bool:
    """Check whether image is default for the specific device type."""
    scope = normalize_default_device_scope_id(device_id)
    if not scope:
        return False
    return scope in get_image_default_device_scopes(image)


def get_device_image_count(device_id: str) -> int:
    """Count how many images are assigned to a device type.

    Checks both 'device_id' field and 'compatible_devices' list.
    """
    manifest = load_manifest()
    return sum(1 for img in manifest.get("images", [])
               if image_matches_device(img, device_id))


def _get_filename_keyword_map() -> dict[str, str]:
    """Get the filename keyword map, derived from VENDOR_CONFIGS."""
    try:
        from agent.vendors import _DERIVED_FILENAME_KEYWORD_MAP
        return _DERIVED_FILENAME_KEYWORD_MAP
    except ImportError:
        return {}


def detect_device_from_filename(filename: str) -> tuple[str | None, str | None]:
    name = filename.lower()
    for rule in load_rules():
        pattern = rule.get("pattern")
        device_id = rule.get("device_id")
        if not pattern or not device_id:
            continue
        if re.search(pattern, name):
            return device_id, _extract_version(filename)
    keyword_map = _get_filename_keyword_map()
    for keyword, device_id in keyword_map.items():
        if keyword in name:
            return device_id, _extract_version(filename)
    return None, _extract_version(filename)


def _extract_version(filename: str) -> str | None:
    match = re.search(r"(\d+(?:\.\d+){1,3}[A-Za-z0-9]*)", filename)
    return match.group(1) if match else None


def get_vendor_for_device(device_id: str) -> Optional[str]:
    """Get the vendor name for a device ID.

    Deprecated: prefer DeviceResolver.resolve(device_id).vendor
    """
    if not device_id:
        return None
    device_lower = device_id.lower()
    result = DEVICE_VENDOR_MAP.get(device_lower)
    if result:
        return result
    # Fallback to DeviceResolver for full alias chain resolution.
    try:
        from app.services.device_resolver import get_resolver
        resolved = get_resolver().resolve(device_id)
        return resolved.vendor
    except Exception:
        return None


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
    device_id: Optional[str] = None
    version: Optional[str] = None
    size_bytes: Optional[int] = None
    sha256: Optional[str] = None
    notes: str = ""
    compatible_devices: Optional[list[str]] = None
    source: Optional[str] = None

    # VM runtime hints (from VIRL2 node-definitions or user overrides)
    memory_mb: Optional[int] = None
    cpu_count: Optional[int] = None
    disk_driver: Optional[str] = None
    nic_driver: Optional[str] = None
    machine_type: Optional[str] = None
    libvirt_driver: Optional[str] = None
    boot_timeout: Optional[int] = None
    readiness_probe: Optional[str] = None
    readiness_pattern: Optional[str] = None
    efi_boot: Optional[bool] = None
    efi_vars: Optional[str] = None
    max_ports: Optional[int] = None
    port_naming: Optional[str] = None
    cpu_limit: Optional[int] = None
    has_loopback: Optional[bool] = None
    provisioning_driver: Optional[str] = None
    provisioning_media_type: Optional[str] = None

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
    device_id: Optional[str] = None,
    version: Optional[str] = None,
    size_bytes: Optional[int] = None,
    notes: str = "",
    compatible_devices: Optional[list[str]] = None,
    source: Optional[str] = None,
    memory_mb: Optional[int] = None,
    cpu_count: Optional[int] = None,
    disk_driver: Optional[str] = None,
    nic_driver: Optional[str] = None,
    machine_type: Optional[str] = None,
    libvirt_driver: Optional[str] = None,
    boot_timeout: Optional[int] = None,
    readiness_probe: Optional[str] = None,
    readiness_pattern: Optional[str] = None,
    efi_boot: Optional[bool] = None,
    efi_vars: Optional[str] = None,
    max_ports: Optional[int] = None,
    port_naming: Optional[str] = None,
    cpu_limit: Optional[int] = None,
    has_loopback: Optional[bool] = None,
    provisioning_driver: Optional[str] = None,
    provisioning_media_type: Optional[str] = None,
    sha256: Optional[str] = None,
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
        ensure_custom_device_exists(canonical_device_id, preferred_image_kind=kind)

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
) -> Optional[dict]:
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


def cleanup_orphaned_custom_devices() -> list[str]:
    """Remove custom devices that no image references.

    Scans custom devices marked isCustom=True and removes any that have no
    matching images in the manifest. Returns list of removed device IDs.
    """
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


def find_image_reference(device_id: str, version: str | None = None) -> str | None:
    """Look up the image reference for a device type and version.

    Supports Docker and qcow2 images. Raw IOL binaries (kind="iol") are
    excluded — they must be built into Docker images first.

    Args:
        device_id: Device type (e.g., 'eos', 'ceos', 'iosv', 'cisco_iosv')
        version: Optional version string (e.g., '4.35.1F')

    Returns:
        Image reference (Docker tag or file path for qcow2/IOL) or None if not found
    """
    manifest = load_manifest()
    images = manifest.get("images", [])

    # Supported image kinds
    supported_kinds = ("docker", "qcow2")

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
        if image_matches_device(img, device_id) and is_image_default_for_device(img, device_id):
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

    # Default to docker for Docker image tags
    return "docker"
