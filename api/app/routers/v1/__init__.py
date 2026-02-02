"""API v1 routers.

This module provides versioned API endpoints under /api/v1/.
All new features should be added to versioned endpoints.

Usage:
    # In main.py:
    from app.routers.v1 import router as v1_router
    app.include_router(v1_router, prefix="/api/v1")

Migration Path:
1. New endpoints go directly into v1
2. Legacy endpoints (without /api/v1) remain for backward compatibility
3. Deprecation headers are added to legacy endpoints
4. After deprecation period, legacy endpoints are removed

Example:
    /api/v1/labs - versioned endpoint (preferred)
    /labs - legacy endpoint (deprecated)
"""
from __future__ import annotations

from fastapi import APIRouter

# Create main v1 router
router = APIRouter(tags=["v1"])


# Import sub-routers when they exist
# from .labs import router as labs_router
# from .images import router as images_router
# from .agents import router as agents_router

# router.include_router(labs_router, prefix="/labs", tags=["labs"])
# router.include_router(images_router, prefix="/images", tags=["images"])
# router.include_router(agents_router, prefix="/agents", tags=["agents"])


@router.get("/version")
def get_api_version() -> dict:
    """Get the current API version."""
    return {
        "version": "1",
        "status": "current",
        "deprecated": False,
    }
