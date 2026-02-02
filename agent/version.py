"""Dynamic version management for the agent.

This module provides version information that can be read from:
1. The VERSION file (primary source)
2. Git tags as fallback
"""

import os
import subprocess
from pathlib import Path


def get_version() -> str:
    """Get the agent version.

    Reads from VERSION file first, falls back to git tag.

    Returns:
        Version string (e.g., "0.2.0")
    """
    # Try reading from VERSION file first
    version_file = Path(__file__).parent / "VERSION"
    if version_file.exists():
        try:
            version = version_file.read_text().strip()
            if version:
                return version
        except Exception:
            pass

    # Fall back to git tag
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=Path(__file__).parent,
        )
        if result.returncode == 0:
            tag = result.stdout.strip()
            # Remove 'v' prefix if present
            if tag.startswith("v"):
                tag = tag[1:]
            return tag
    except Exception:
        pass

    # Default fallback
    return "0.0.0"


# Cache the version at import time
__version__ = get_version()


def get_commit() -> str:
    """Get the agent commit SHA.

    Reads from ARCHETYPE_GIT_SHA env var first, then GIT_SHA file,
    falls back to git rev-parse if available.
    """
    env_sha = os.getenv("ARCHETYPE_GIT_SHA", "").strip()
    if env_sha:
        return env_sha

    commit_file = Path(__file__).parent / "GIT_SHA"
    if commit_file.exists():
        try:
            commit = commit_file.read_text().strip()
            if commit:
                return commit
        except Exception:
            pass

    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=Path(__file__).parent,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass

    return "unknown"
