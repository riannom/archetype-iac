"""Image upload and management endpoints."""
from __future__ import annotations

import asyncio  # noqa: F401 - re-exported for test monkeypatching
import subprocess  # noqa: F401 - re-exported for test monkeypatching

from fastapi import APIRouter

from .upload_docker import router as upload_docker_router
from .upload_vm import router as upload_vm_router
from .library import router as library_router
from .sync import router as sync_router

# Re-export symbols that external code imports directly from this module.
# Tests access: _chunk_upload_sessions, _chunk_upload_lock, _upload_progress,
#   _upload_lock, threading, _update_progress, _clear_progress
# image_sync.py imports: _execute_sync_job
from ._shared import (  # noqa: F401
    _chunk_upload_lock,
    _chunk_upload_sessions,
    _clear_progress,
    _update_progress,
    _upload_progress,
    _upload_lock,
    threading,
)
from .sync import _execute_sync_job  # noqa: F401

# Assemble a single router from all sub-modules, preserving the original
# prefix so that `app.include_router(images.router)` keeps working.
router = APIRouter(prefix="/images", tags=["images"])
router.include_router(upload_docker_router)
router.include_router(upload_vm_router)
router.include_router(library_router)
router.include_router(sync_router)
