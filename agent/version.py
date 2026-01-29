"""Dynamic version management for the agent.

This module provides version information that can be read from:
1. The VERSION file (primary source)
2. Git tags as fallback
"""

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
