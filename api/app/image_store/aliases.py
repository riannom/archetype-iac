"""Device ID canonicalization, normalization, and compatibility."""
from __future__ import annotations

import functools
import logging

logger = logging.getLogger(__name__)


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
    "c8000v": ["cisco_c8000v"],
    "ftdv": ["cisco_ftdv"],
}

# Platform siblings: devices sharing a platform field use the same image.
# Maps each device_id to the set of sibling device_ids (including itself).
def _build_platform_siblings() -> dict[str, list[str]]:
    """Build device_id -> platform sibling device_ids from VENDOR_CONFIGS."""
    try:
        from agent.vendors import VENDOR_CONFIGS
    except ImportError:
        return {}
    platform_groups: dict[str, list[str]] = {}
    for key, cfg in VENDOR_CONFIGS.items():
        if cfg.platform:
            platform_groups.setdefault(cfg.platform, []).append(key)
    siblings: dict[str, list[str]] = {}
    for members in platform_groups.values():
        if len(members) < 2:
            continue
        for member in members:
            siblings[member] = members
    return siblings


PLATFORM_SIBLINGS: dict[str, list[str]] = _build_platform_siblings()


RUNNABLE_IMAGE_KINDS = {"docker", "qcow2"}


def get_image_compatibility_aliases() -> dict[str, list[str]]:
    """Return normalized image compatibility aliases for UI/API consumers."""
    aliases: dict[str, list[str]] = {}
    for device_id, values in IMAGE_COMPAT_ALIASES.items():
        normalized_device = normalize_default_device_scope_id(device_id)
        if not normalized_device:
            continue
        normalized_values = normalize_default_device_scope_ids(values)
        if normalized_values:
            aliases[normalized_device] = normalized_values
    return aliases


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

    Results are cached -- the function is pure for a given module load.
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


def _maybe_correct_device_via_filename(image: dict, device_id: str | None) -> str | None:
    """Use filename detection to correct misassigned qcow2 images.

    When the stored device_id is a platform alias shared by multiple device
    types (e.g. cisco_cat9kv), filename detection provides a more precise
    device assignment.  Also resets stale compatible_devices when the
    detected device differs from the stored one.
    """
    from .detection import detect_qcow2_device_type

    kind = str(image.get("kind") or "").lower()
    if kind != "qcow2":
        return device_id
    filename = str(image.get("filename") or "")
    if not filename:
        return device_id
    detected, _ = detect_qcow2_device_type(filename)
    if not detected:
        return device_id
    # If filename detection gives a different device than stored,
    # clear stale compatible_devices that were based on the old ID.
    stored_canonical = canonicalize_device_id(device_id)
    detected_canonical = canonicalize_device_id(detected)
    if detected_canonical and detected_canonical != stored_canonical:
        image["compatible_devices"] = [detected]
    return detected


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


def get_vendor_for_device(device_id: str) -> str | None:
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
