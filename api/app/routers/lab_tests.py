"""Lab verification test endpoints."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.auth import get_current_user
from app.config import settings
from app.db import get_db
from app.state import JobStatus
from app.utils.lab import get_lab_or_404
from app.utils.async_tasks import safe_create_task

logger = logging.getLogger(__name__)

router = APIRouter(tags=["lab_tests"])


@router.post("/labs/{lab_id}/tests/run", response_model=schemas.TestRunResponse)
def run_lab_tests(
    lab_id: str,
    request: schemas.RunTestsRequest | None = None,
    database: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Run verification tests against a deployed lab.

    If request.specs is provided, run those tests.
    Otherwise, load tests from the lab's topology YAML.
    """
    lab = get_lab_or_404(lab_id, database, current_user)

    specs = None
    if request and request.specs:
        specs = [s.model_dump() for s in request.specs]

    if not specs:
        # Try loading from topology YAML
        from app.topology import yaml_to_graph
        workspace = Path(settings.workspace) / lab.id
        topo_file = workspace / "topology.yml"
        if topo_file.exists():
            try:
                graph = yaml_to_graph(topo_file.read_text())
                if graph.tests:
                    specs = graph.tests
            except Exception:
                pass

    if not specs:
        raise HTTPException(status_code=400, detail="No test specs provided and no tests found in topology YAML")

    # Create job record
    log_dir = Path(settings.workspace) / lab.id / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    job = models.Job(
        lab_id=lab.id,
        user_id=current_user.id,
        action="test",
        status=JobStatus.QUEUED,
        log_path=str(log_dir / "test_results.json"),
    )
    database.add(job)
    database.commit()
    database.refresh(job)

    # Launch async test runner
    from app.tasks.test_runner import run_verification_tests
    safe_create_task(
        run_verification_tests(job.id, lab.id, specs),
        name=f"test:{job.id}",
    )

    return schemas.TestRunResponse(job_id=job.id, message=f"Running {len(specs)} tests")


@router.get("/labs/{lab_id}/tests")
def get_lab_tests(
    lab_id: str,
    database: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Get test specs from the lab's topology YAML."""
    lab = get_lab_or_404(lab_id, database, current_user)

    from app.topology import yaml_to_graph
    workspace = Path(settings.workspace) / lab.id
    topo_file = workspace / "topology.yml"

    if not topo_file.exists():
        return {"tests": []}

    try:
        graph = yaml_to_graph(topo_file.read_text())
        return {"tests": graph.tests or []}
    except Exception:
        return {"tests": []}


@router.get("/labs/{lab_id}/tests/results/{job_id}")
def get_test_results(
    lab_id: str,
    job_id: str,
    database: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Get structured test results for a completed test run."""
    lab = get_lab_or_404(lab_id, database, current_user)
    job = database.get(models.Job, job_id)

    if not job or job.lab_id != lab.id:
        raise HTTPException(status_code=404, detail="Test job not found")

    if job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
        return {"status": job.status, "message": "Tests still running"}

    if not job.log_path:
        raise HTTPException(status_code=404, detail="No test results available")

    try:
        results = json.loads(Path(job.log_path).read_text())
        return results
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Test results file not found")
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Test results file is corrupted")
