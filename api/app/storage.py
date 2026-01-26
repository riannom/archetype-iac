from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from app.config import settings

if TYPE_CHECKING:
    from app.schemas import LabLayout


def workspace_root() -> Path:
    return Path(settings.netlab_workspace)


def lab_workspace(lab_id: str) -> Path:
    return workspace_root() / lab_id


def topology_path(lab_id: str) -> Path:
    return lab_workspace(lab_id) / "topology.yml"


def layout_path(lab_id: str) -> Path:
    """Get the path to a lab's layout.json file."""
    return lab_workspace(lab_id) / "layout.json"


def ensure_topology_file(lab_id: str) -> Path:
    path = topology_path(lab_id)
    if not path.exists():
        path.write_text(
            "defaults:\\n  device: iosv\\nnodes: {}\\nlinks: []\\n",
            encoding="utf-8",
        )
    return path


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
