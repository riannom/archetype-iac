"""Tests for api/app/routers/lab_tests.py — verification test endpoints.

Covers:
- POST /labs/{id}/tests/run — runs tests (or returns 400 if no specs), auth check
- GET /labs/{id}/tests — returns test specs from topology, auth check
- GET /labs/{id}/tests/results/{job_id} — returns test results, auth check
"""
from __future__ import annotations


from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models


# ---------------------------------------------------------------------------
# POST /labs/{id}/tests/run
# ---------------------------------------------------------------------------


class TestRunLabTests:

    def test_run_tests_no_specs_returns_400(
        self, test_client: TestClient, sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """POST /tests/run returns 400 when no test specs provided and no topology YAML."""
        resp = test_client.post(
            f"/labs/{sample_lab.id}/tests/run",
            json={"specs": []},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "No test specs" in resp.json()["detail"]

    def test_run_tests_requires_auth(self, test_client: TestClient, sample_lab: models.Lab):
        resp = test_client.post(
            f"/labs/{sample_lab.id}/tests/run",
            json={"specs": []},
        )
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# GET /labs/{id}/tests
# ---------------------------------------------------------------------------


class TestGetLabTests:

    def test_get_tests_returns_empty_when_no_topology(
        self, test_client: TestClient, sample_lab: models.Lab, auth_headers: dict,
    ):
        """GET /tests returns empty list when no topology YAML exists."""
        resp = test_client.get(
            f"/labs/{sample_lab.id}/tests",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["tests"] == []

    def test_get_tests_requires_auth(self, test_client: TestClient, sample_lab: models.Lab):
        resp = test_client.get(f"/labs/{sample_lab.id}/tests")
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# GET /labs/{id}/tests/results/{job_id}
# ---------------------------------------------------------------------------


class TestGetTestResults:

    def test_get_results_returns_status_for_running_job(
        self, test_client: TestClient, test_db: Session,
        sample_lab: models.Lab, test_user: models.User,
        auth_headers: dict,
    ):
        """GET /tests/results/{job_id} returns status message for a running job."""
        job = models.Job(
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="test",
            status="running",
            log_path="/tmp/fake.json",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        resp = test_client.get(
            f"/labs/{sample_lab.id}/tests/results/{job.id}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "running"

    def test_get_results_requires_auth(
        self, test_client: TestClient, sample_lab: models.Lab,
    ):
        resp = test_client.get(
            f"/labs/{sample_lab.id}/tests/results/some-job-id",
        )
        assert resp.status_code in (401, 403)
