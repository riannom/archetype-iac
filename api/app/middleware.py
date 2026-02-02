from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.auth import get_current_user_optional
from app.db import SessionLocal

logger = logging.getLogger(__name__)


class CurrentUserMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        database = SessionLocal()
        try:
            request.state.user = get_current_user_optional(request, database)
        finally:
            database.close()
        return await call_next(request)


class DeprecationMiddleware(BaseHTTPMiddleware):
    """Middleware to add deprecation headers to legacy API endpoints.

    This middleware adds Deprecation and Sunset headers to responses for
    endpoints that don't use the versioned API path (/api/v1/).

    Headers added:
    - Deprecation: true
    - Sunset: Date when the endpoint will be removed
    - Link: URL to the new versioned endpoint

    Usage:
        app.add_middleware(DeprecationMiddleware, sunset_date="2026-06-01")
    """

    # Endpoints that are exempt from deprecation warnings
    EXEMPT_PATHS = {
        "/health",
        "/metrics",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/ws/",  # WebSocket endpoints
    }

    def __init__(self, app, sunset_date: str = "2026-12-01"):
        super().__init__(app)
        self.sunset_date = sunset_date

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        path = request.url.path

        # Skip if already using versioned API
        if path.startswith("/api/v"):
            return response

        # Skip exempt paths
        for exempt in self.EXEMPT_PATHS:
            if path.startswith(exempt):
                return response

        # Skip non-API paths (static files, etc.)
        if not self._is_api_path(path):
            return response

        # Add deprecation headers
        response.headers["Deprecation"] = "true"
        response.headers["Sunset"] = self.sunset_date

        # Suggest versioned endpoint
        versioned_path = f"/api/v1{path}"
        response.headers["Link"] = f'<{versioned_path}>; rel="successor-version"'

        return response

    def _is_api_path(self, path: str) -> bool:
        """Check if a path is an API endpoint that should be deprecated."""
        # List of API path prefixes
        api_prefixes = [
            "/labs",
            "/images",
            "/agents",
            "/vendors",
            "/devices",
            "/jobs",
            "/users",
            "/permissions",
            "/dashboard",
        ]
        return any(path.startswith(prefix) for prefix in api_prefixes)
