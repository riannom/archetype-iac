"""Shared log helpers."""

from __future__ import annotations

from pathlib import Path


def _is_likely_file_path(value: str) -> bool:
    """Check if a string looks like a file path (not inline content)."""
    if "\n" in value:
        return False
    if len(value) > 4096:
        return False
    return value.startswith("/") or not value.startswith("=")


def get_log_content(log_path_or_content: str | None) -> str | None:
    """Get log content from either a file path or inline content."""
    if not log_path_or_content:
        return None

    if _is_likely_file_path(log_path_or_content):
        try:
            log_path = Path(log_path_or_content)
            if log_path.exists() and log_path.is_file():
                return log_path.read_text(encoding="utf-8")
        except OSError:
            pass

    return log_path_or_content
