"""Image upload and management endpoints.

This package was split into focused submodules. To preserve compatibility with
existing tests and older import paths, we re-export legacy symbols from here.
"""
from __future__ import annotations

import asyncio  # noqa: F401 - re-exported for test monkeypatching
import lzma  # noqa: F401 - re-exported for test monkeypatching
import shutil  # noqa: F401 - re-exported for test monkeypatching
import subprocess  # noqa: F401 - re-exported for test monkeypatching
import sys
from types import ModuleType

from fastapi import APIRouter

from app.image_store import (  # noqa: F401
    create_image_entry,
    delete_image_entry,
    detect_device_from_filename,
    detect_iol_device_type,
    detect_qcow2_device_type,
    ensure_image_store,
    find_image_by_id,
    iol_path,
    load_manifest,
    qcow2_path,
    save_manifest,
    update_image_entry,
)
from app.jobs import get_queue  # noqa: F401
from app.services.catalog_service import (  # noqa: F401
    CatalogAliasConflictError,
    CatalogImageNotFoundError,
    apply_manifest_style_image_update,
    catalog_is_seeded,
    delete_catalog_image,
    get_catalog_library_image,
    resolve_catalog_device_id,
)
from app.services.resource_monitor import ResourceMonitor  # noqa: F401
from app.utils.image_integrity import compute_sha256, validate_qcow2  # noqa: F401

from . import _shared as _shared_module
from . import library as _library_module
from . import sync as _sync_module
from . import upload_docker as _upload_docker_module
from . import upload_vm as _upload_vm_module
from ._shared import (  # noqa: F401
    _CHUNK_UPLOAD_DIR,
    ImageChunkUploadInitRequest,
    _chunk_upload_destination,
    _chunk_upload_lock,
    _chunk_upload_sessions,
    _cleanup_chunk_upload_session_files,
    _cleanup_expired_chunk_upload_sessions,
    _clear_progress,
    _format_size,
    _get_progress,
    _is_docker_image_tar,
    _sanitize_upload_filename,
    _update_progress,
    _upload_progress,
    _upload_lock,
    threading,
)
from .library import (  # noqa: F401
    assign_image_to_device,
    delete_image,
    router as library_router,
    unassign_image_from_device,
    update_image_library,
)
from .sync import _execute_sync_job, router as sync_router  # noqa: F401
from .upload_docker import (  # noqa: F401
    cancel_chunk_upload,
    complete_chunk_upload,
    get_chunk_upload_status,
    init_chunk_upload,
    _load_image_background,
    _load_image_background_from_archive,
    _load_image_streaming,
    _load_image_sync,
    router as upload_docker_router,
    upload_chunk,
)
from .upload_vm import (  # noqa: F401
    Qcow2ConfirmRequest,
    _enqueue_iol_build_job,
    confirm_qcow2_upload,
    _detect_qcow2,
    _parse_sidecar_metadata,
    _finalize_qcow2_upload,
    _register_qcow2,
    router as upload_vm_router,
)

# Assemble a single router from all sub-modules, preserving the original
# prefix so that `app.include_router(images.router)` keeps working.
router = APIRouter(prefix="/images", tags=["images"])
router.include_router(upload_docker_router)
router.include_router(upload_vm_router)
router.include_router(library_router)
router.include_router(sync_router)


_PATCH_TARGET_MODULES = (
    _shared_module,
    _library_module,
    _upload_docker_module,
    _upload_vm_module,
    _sync_module,
)


class _ImagesCompatModule(ModuleType):
    """Propagate monkeypatched attributes to split image-router submodules."""

    def __setattr__(self, name: str, value):
        super().__setattr__(name, value)
        if name.startswith("__"):
            return
        for module in _PATCH_TARGET_MODULES:
            if hasattr(module, name):
                setattr(module, name, value)


sys.modules[__name__].__class__ = _ImagesCompatModule
