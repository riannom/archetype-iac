"""System information endpoints including version and updates."""
from __future__ import annotations

import logging
import time
from pathlib import Path

import httpx
from fastapi import APIRouter

from app.config import settings
from app.schemas import UpdateInfo, VersionInfo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/system", tags=["system"])

# Cache for update check results
_update_cache: dict = {
    "data": None,
    "timestamp": 0,
}


def get_version() -> str:
    """Read version from VERSION file at repository root."""
    # Try multiple locations for VERSION file
    possible_paths = [
        Path(__file__).parent.parent.parent.parent / "VERSION",  # /api/../VERSION
        Path("/app/VERSION"),  # Docker container path
        Path("VERSION"),  # Current working directory
    ]

    for version_path in possible_paths:
        if version_path.exists():
            return version_path.read_text().strip()

    return "0.0.0"  # Fallback if VERSION file not found


@router.get("/version", response_model=VersionInfo)
def get_version_info() -> VersionInfo:
    """Get current application version.

    Returns the version string read from the VERSION file at the repository root.
    """
    return VersionInfo(version=get_version())


@router.get("/updates", response_model=UpdateInfo)
async def check_for_updates() -> UpdateInfo:
    """Check GitHub for available updates.

    Queries the GitHub releases API to check if a newer version is available.
    Results are cached for 1 hour to avoid rate limiting.

    Returns:
        UpdateInfo with current version, latest version, and release details
    """
    current_version = get_version()
    now = time.time()

    # Check cache
    if (
        _update_cache["data"] is not None
        and (now - _update_cache["timestamp"]) < settings.version_check_cache_ttl
    ):
        cached = _update_cache["data"]
        # Update current_version in case it changed (unlikely but possible)
        cached["current_version"] = current_version
        return UpdateInfo(**cached)

    # Fetch from GitHub
    github_url = f"https://api.github.com/repos/{settings.github_repo}/releases/latest"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                github_url,
                headers={
                    "Accept": "application/vnd.github.v3+json",
                    "User-Agent": f"Archetype/{current_version}",
                },
            )

            if response.status_code == 404:
                # No releases yet
                result = {
                    "current_version": current_version,
                    "latest_version": None,
                    "update_available": False,
                    "release_url": None,
                    "release_notes": None,
                    "published_at": None,
                    "error": None,
                }
                _update_cache["data"] = result
                _update_cache["timestamp"] = now
                return UpdateInfo(**result)

            if response.status_code != 200:
                logger.warning(
                    f"GitHub API returned {response.status_code}: {response.text}"
                )
                return UpdateInfo(
                    current_version=current_version,
                    error=f"GitHub API error: {response.status_code}",
                )

            data = response.json()

    except httpx.ConnectError:
        logger.warning("Cannot connect to GitHub API")
        return UpdateInfo(
            current_version=current_version,
            error="Cannot connect to GitHub",
        )
    except httpx.TimeoutException:
        logger.warning("GitHub API request timed out")
        return UpdateInfo(
            current_version=current_version,
            error="GitHub API timeout",
        )
    except Exception as e:
        logger.error(f"Error checking for updates: {e}")
        return UpdateInfo(
            current_version=current_version,
            error=str(e),
        )

    # Parse release info
    tag_name = data.get("tag_name", "")
    # Strip 'v' prefix if present
    latest_version = tag_name.lstrip("v")

    # Compare versions (simple string comparison works for semver)
    update_available = _compare_versions(latest_version, current_version) > 0

    result = {
        "current_version": current_version,
        "latest_version": latest_version,
        "update_available": update_available,
        "release_url": data.get("html_url"),
        "release_notes": data.get("body"),
        "published_at": data.get("published_at"),
        "error": None,
    }

    # Cache the result
    _update_cache["data"] = result
    _update_cache["timestamp"] = now

    return UpdateInfo(**result)


def _compare_versions(v1: str, v2: str) -> int:
    """Compare two semver version strings.

    Returns:
        1 if v1 > v2, -1 if v1 < v2, 0 if equal
    """
    if not v1 or not v2:
        return 0

    def parse_version(v: str) -> tuple[int, ...]:
        """Parse version string into tuple of integers."""
        # Handle pre-release suffixes like -alpha, -beta, -rc1
        base = v.split("-")[0]
        parts = []
        for part in base.split("."):
            try:
                parts.append(int(part))
            except ValueError:
                parts.append(0)
        # Pad to at least 3 parts
        while len(parts) < 3:
            parts.append(0)
        return tuple(parts)

    try:
        v1_tuple = parse_version(v1)
        v2_tuple = parse_version(v2)

        if v1_tuple > v2_tuple:
            return 1
        elif v1_tuple < v2_tuple:
            return -1
        return 0
    except Exception:
        # Fallback to string comparison
        if v1 > v2:
            return 1
        elif v1 < v2:
            return -1
        return 0
