from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

from app.config import settings

if TYPE_CHECKING:
    from app.schemas import LabLayout

_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")


def workspace_root() -> Path:
    return Path(settings.workspace)


def lab_workspace(lab_id: str) -> Path:
    if not _SAFE_ID_RE.match(lab_id):
        raise ValueError(f"Invalid lab_id: {lab_id}")
    workspace = workspace_root() / lab_id
    resolved = workspace.resolve()
    if not resolved.is_relative_to(workspace_root().resolve()):
        raise ValueError(f"Path traversal detected in lab_id: {lab_id}")
    return workspace


def layout_path(lab_id: str) -> Path:
    """Get the path to a lab's layout.json file."""
    return lab_workspace(lab_id) / "layout.json"


def read_layout(lab_id: str) -> "LabLayout | None":
    """Read layout data from disk, returning None if not found."""
    from app.schemas import LabLayout

    path = layout_path(lab_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return LabLayout.model_validate(data)
    except (json.JSONDecodeError, ValueError):
        return None


def write_layout(lab_id: str, layout: "LabLayout") -> None:
    """Write layout data to disk."""
    path = layout_path(lab_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(layout.model_dump_json(indent=2), encoding="utf-8")


def delete_layout(lab_id: str) -> bool:
    """Delete layout file if it exists. Returns True if deleted."""
    path = layout_path(lab_id)
    if path.exists():
        path.unlink()
        return True
    return False
