"""Lab scenario endpoints — CRUD + execute for YAML scenario files."""
from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.auth import get_current_user
from app.config import settings
from app.db import get_db
from app.state import JobStatus
from app.utils.async_tasks import safe_create_task
from app.utils.lab import get_lab_or_404

logger = logging.getLogger(__name__)

router = APIRouter(tags=["scenarios"])

_SAFE_FILENAME_RE = re.compile(r"^[a-zA-Z0-9_\-]+\.ya?ml$")


def _scenario_dir(lab_id: str) -> Path:
    return Path(settings.workspace) / lab_id / "scenarios"


def _validate_filename(filename: str) -> None:
    if not _SAFE_FILENAME_RE.match(filename):
        raise HTTPException(status_code=400, detail="Invalid filename — use alphanumeric/underscore/hyphen with .yml extension")


def _parse_scenario(raw: str) -> dict:
    """Parse and validate a scenario YAML string."""
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {e}")
    if not isinstance(data, dict) or "steps" not in data:
        raise HTTPException(status_code=400, detail="Scenario YAML must contain a 'steps' key")
    if not isinstance(data["steps"], list) or len(data["steps"]) == 0:
        raise HTTPException(status_code=400, detail="Scenario must have at least one step")
    return data


@router.get("/labs/{lab_id}/scenarios", response_model=list[schemas.ScenarioSummary])
def list_scenarios(
    lab_id: str,
    database: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """List all scenarios for a lab."""
    get_lab_or_404(lab_id, database, current_user)
    sdir = _scenario_dir(lab_id)
    if not sdir.exists():
        return []
    results = []
    for f in sorted(sdir.iterdir()):
        if not f.suffix in (".yml", ".yaml"):
            continue
        try:
            data = yaml.safe_load(f.read_text())
            results.append(schemas.ScenarioSummary(
                filename=f.name,
                name=data.get("name", f.stem),
                description=data.get("description", ""),
                step_count=len(data.get("steps", [])),
            ))
        except Exception:
            results.append(schemas.ScenarioSummary(
                filename=f.name, name=f.stem, description="(parse error)", step_count=0,
            ))
    return results


@router.get("/labs/{lab_id}/scenarios/{filename}", response_model=schemas.ScenarioDetail)
def get_scenario(
    lab_id: str,
    filename: str,
    database: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Get a single scenario definition."""
    get_lab_or_404(lab_id, database, current_user)
    _validate_filename(filename)
    path = _scenario_dir(lab_id) / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Scenario not found")
    raw = path.read_text()
    data = _parse_scenario(raw)
    return schemas.ScenarioDetail(
        filename=filename,
        name=data.get("name", path.stem),
        description=data.get("description", ""),
        steps=data["steps"],
        raw_yaml=raw,
    )


@router.put("/labs/{lab_id}/scenarios/{filename}")
def save_scenario(
    lab_id: str,
    filename: str,
    body: schemas.ScenarioSave,
    database: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Create or update a scenario file."""
    get_lab_or_404(lab_id, database, current_user)
    _validate_filename(filename)
    _parse_scenario(body.content)  # validate
    sdir = _scenario_dir(lab_id)
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / filename).write_text(body.content)
    return {"ok": True, "filename": filename}


@router.delete("/labs/{lab_id}/scenarios/{filename}")
def delete_scenario(
    lab_id: str,
    filename: str,
    database: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Delete a scenario file."""
    get_lab_or_404(lab_id, database, current_user)
    _validate_filename(filename)
    path = _scenario_dir(lab_id) / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Scenario not found")
    path.unlink()
    return {"ok": True}


@router.post("/labs/{lab_id}/scenarios/{filename}/execute", response_model=schemas.ScenarioExecuteResponse)
def execute_scenario(
    lab_id: str,
    filename: str,
    database: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Start executing a scenario, returns a job_id for tracking."""
    lab = get_lab_or_404(lab_id, database, current_user)
    _validate_filename(filename)
    path = _scenario_dir(lab_id) / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Scenario not found")
    raw = path.read_text()
    data = _parse_scenario(raw)
    steps = data["steps"]
    scenario_name = data.get("name", path.stem)

    log_dir = Path(settings.workspace) / lab.id / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    job = models.Job(
        lab_id=lab.id,
        user_id=current_user.id,
        action="scenario",
        status=JobStatus.QUEUED,
        log_path=str(log_dir / f"scenario_{path.stem}.json"),
    )
    database.add(job)
    database.commit()
    database.refresh(job)

    from app.tasks.scenario_executor import run_scenario
    safe_create_task(
        run_scenario(job.id, lab.id, steps, scenario_name),
        name=f"scenario:{job.id}",
    )

    return schemas.ScenarioExecuteResponse(
        job_id=job.id,
        scenario_name=scenario_name,
        step_count=len(steps),
    )
