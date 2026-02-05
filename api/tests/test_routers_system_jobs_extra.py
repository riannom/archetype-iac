from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import app.routers.jobs as jobs_router  # noqa: F401
import app.routers.system as system_router  # noqa: F401
import pytest

from app import models


def test_list_jobs_and_get_job_log(test_client, test_db, test_user, auth_headers) -> None:
    lab = models.Lab(
        name="Lab",
        owner_id=test_user.id,
        provider="docker",
        state="stopped",
        workspace_path="/tmp/lab",
    )
    test_db.add(lab)
    test_db.commit()

    job = models.Job(
        lab_id=lab.id,
        user_id=test_user.id,
        action="up",
        status="failed",
        log_path="error: failed to start",
    )
    test_db.add(job)
    test_db.commit()

    resp = test_client.get(f"/labs/{lab.id}/jobs", headers=auth_headers)
    assert resp.status_code == 200
    jobs = resp.json()["jobs"]
    assert jobs[0]["action"] == "up"

    job_resp = test_client.get(f"/labs/{lab.id}/jobs/{job.id}", headers=auth_headers)
    assert job_resp.status_code == 200

    log_resp = test_client.get(f"/labs/{lab.id}/jobs/{job.id}/log", headers=auth_headers)
    assert log_resp.status_code == 200
    assert "error" in log_resp.json()["log"]


def test_get_job_log_from_file_path(test_client, test_db, test_user, auth_headers, tmp_path) -> None:
    lab = models.Lab(
        name="Lab",
        owner_id=test_user.id,
        provider="docker",
        state="stopped",
        workspace_path="/tmp/lab",
    )
    test_db.add(lab)
    test_db.commit()

    log_file = tmp_path / "job.log"
    log_file.write_text("hello\nworld")

    job = models.Job(
        lab_id=lab.id,
        user_id=test_user.id,
        action="up",
        status="failed",
        log_path=str(log_file),
    )
    test_db.add(job)
    test_db.commit()

    log_resp = test_client.get(f"/labs/{lab.id}/jobs/{job.id}/log", headers=auth_headers)
    assert log_resp.status_code == 200
    assert log_resp.json()["log"] == "hello\nworld"


def test_cancel_job_updates_lab(test_client, test_db, test_user, auth_headers) -> None:
    lab = models.Lab(
        name="Lab",
        owner_id=test_user.id,
        provider="docker",
        state="running",
        workspace_path="/tmp/lab",
    )
    test_db.add(lab)
    test_db.commit()

    job = models.Job(
        lab_id=lab.id,
        user_id=test_user.id,
        action="up",
        status="running",
        log_path=None,
    )
    test_db.add(job)
    test_db.commit()

    resp = test_client.post(f"/labs/{lab.id}/jobs/{job.id}/cancel", headers=auth_headers)
    assert resp.status_code == 200
    test_db.refresh(job)
    test_db.refresh(lab)
    assert job.status == "cancelled"
    assert lab.state == "unknown"


def test_system_version_info(test_client) -> None:
    resp = test_client.get("/system/version")
    assert resp.status_code == 200
    body = resp.json()
    assert "version" in body
    assert "commit" in body


def test_system_alerts(test_client, test_db) -> None:
    host = models.Host(
        id="h1",
        name="Host",
        address="localhost:1",
        status="online",
        last_error="boom",
        error_since=datetime.now(timezone.utc),
        version="1.0.0",
    )
    test_db.add(host)
    test_db.commit()

    resp = test_client.get("/system/alerts")
    assert resp.status_code == 200
    body = resp.json()
    assert body["agent_error_count"] == 1
    assert body["alerts"][0]["error_message"] == "boom"


def test_system_compare_versions() -> None:
    assert system_router._compare_versions("1.2.0", "1.1.9") == 1
    assert system_router._compare_versions("1.0.0", "1.0.0") == 0
    assert system_router._compare_versions("0.9.1", "1.0.0") == -1
