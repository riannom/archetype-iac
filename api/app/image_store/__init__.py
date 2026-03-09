"""Image store package -- re-exports all public names for backward compatibility.

All existing ``from app.image_store import X`` paths continue to work.
"""
from __future__ import annotations

# -- paths --
from .paths import (
    image_store_root,
    ensure_image_store,
    docker_archive_root,
    ensure_docker_archive_root,
    docker_archive_path,
    qcow2_path,
    iol_path,
    manifest_path,
    rules_path,
    custom_devices_path,
    hidden_devices_path,
    device_overrides_path,
)

# -- detection --
from .detection import (
    QCOW2_DEVICE_PATTERNS,
    detect_iol_device_type,
    detect_qcow2_device_type,
    detect_device_from_filename,
    classify_image_file,
    _build_qcow2_device_patterns,
    _get_filename_keyword_map,
    _extract_version,
)

# -- aliases --
from .aliases import (
    DEVICE_VENDOR_MAP,
    DEVICE_ID_ALIASES,
    IMAGE_COMPAT_ALIASES,
    PLATFORM_SIBLINGS,
    RUNNABLE_IMAGE_KINDS,
    get_image_compatibility_aliases,
    normalize_default_device_scope_id,
    normalize_default_device_scope_ids,
    canonicalize_device_id,
    canonicalize_device_ids,
    image_matches_device,
    get_image_default_device_scopes,
    is_image_default_for_device,
    get_vendor_for_device,
    get_image_provider,
    _build_device_vendor_map,
    _build_device_id_aliases,
    _build_platform_siblings,
    _maybe_backfill_specific_linux_device,
    _maybe_backfill_vjunos_evolved_device,
    _maybe_correct_device_via_filename,
    _device_compatibility_tokens,
)

# -- manifest --
from .manifest import (
    load_manifest,
    save_manifest,
    find_image_by_id,
    find_image_by_reference,
    get_device_image_count,
    find_image_reference,
    _normalize_manifest_images,
    _backfill_single_image_defaults,
)

# -- custom_devices --
from .custom_devices import (
    load_rules,
    load_custom_devices,
    load_hidden_devices,
    save_hidden_devices,
    hide_device,
    unhide_device,
    is_device_hidden,
    save_custom_devices,
    find_custom_device,
    add_custom_device,
    update_custom_device,
    delete_custom_device,
    ensure_custom_device_exists,
    cleanup_orphaned_custom_devices,
    _infer_dynamic_custom_device_metadata,
    _display_name_from_device_id,
)

# -- overrides --
from .overrides import (
    load_device_overrides,
    save_device_overrides,
    get_device_override,
    set_device_override,
    delete_device_override,
)

# -- metadata --
from .metadata import (
    ImageMetadata,
    create_image_entry,
    update_image_entry,
    delete_image_entry,
)

__all__ = [
    # paths
    "image_store_root",
    "ensure_image_store",
    "docker_archive_root",
    "ensure_docker_archive_root",
    "docker_archive_path",
    "qcow2_path",
    "iol_path",
    "manifest_path",
    "rules_path",
    "custom_devices_path",
    "hidden_devices_path",
    "device_overrides_path",
    # detection
    "QCOW2_DEVICE_PATTERNS",
    "detect_iol_device_type",
    "detect_qcow2_device_type",
    "detect_device_from_filename",
    "classify_image_file",
    "_build_qcow2_device_patterns",
    "_get_filename_keyword_map",
    "_extract_version",
    # aliases
    "DEVICE_VENDOR_MAP",
    "DEVICE_ID_ALIASES",
    "IMAGE_COMPAT_ALIASES",
    "PLATFORM_SIBLINGS",
    "RUNNABLE_IMAGE_KINDS",
    "get_image_compatibility_aliases",
    "normalize_default_device_scope_id",
    "normalize_default_device_scope_ids",
    "canonicalize_device_id",
    "canonicalize_device_ids",
    "image_matches_device",
    "get_image_default_device_scopes",
    "is_image_default_for_device",
    "get_vendor_for_device",
    "get_image_provider",
    "_build_device_vendor_map",
    "_build_device_id_aliases",
    "_build_platform_siblings",
    "_maybe_backfill_specific_linux_device",
    "_maybe_backfill_vjunos_evolved_device",
    "_maybe_correct_device_via_filename",
    "_device_compatibility_tokens",
    # manifest
    "load_manifest",
    "save_manifest",
    "find_image_by_id",
    "find_image_by_reference",
    "get_device_image_count",
    "find_image_reference",
    "_normalize_manifest_images",
    "_backfill_single_image_defaults",
    # custom_devices
    "load_rules",
    "load_custom_devices",
    "load_hidden_devices",
    "save_hidden_devices",
    "hide_device",
    "unhide_device",
    "is_device_hidden",
    "save_custom_devices",
    "find_custom_device",
    "add_custom_device",
    "update_custom_device",
    "delete_custom_device",
    "ensure_custom_device_exists",
    "cleanup_orphaned_custom_devices",
    "_infer_dynamic_custom_device_metadata",
    "_display_name_from_device_id",
    # overrides
    "load_device_overrides",
    "save_device_overrides",
    "get_device_override",
    "set_device_override",
    "delete_device_override",
    # metadata
    "ImageMetadata",
    "create_image_entry",
    "update_image_entry",
    "delete_image_entry",
]
