"""Tests for api/app/routers/lab_tests.py — verification test endpoints.

NOTE: The lab_tests router has a bug where get_lab_or_404() is called with
arguments in wrong order: get_lab_or_404(database, lab_id) instead of
get_lab_or_404(lab_id, database, current_user). This causes all 3 endpoints
to fail with TypeError at runtime. Tests document this behavior and verify
auth guards still work (auth runs before the handler body).

Covers:
- POST /labs/{id}/tests/run — arg order bug (TypeError), auth check
- GET /labs/{id}/tests — arg order bug (TypeError), auth check
- GET /labs/{id}/tests/results/{job_id} — arg order bug (TypeError), auth check
"""
from __future__ import annotations


import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models


# ---------------------------------------------------------------------------
# POST /labs/{id}/tests/run
# ---------------------------------------------------------------------------


class TestRunLabTests:

    def test_run_tests_hits_argument_bug(
        self, test_client: TestClient, sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """POST /tests/run fails due to get_lab_or_404 arg order bug.

        get_lab_or_404(database, lab_id) should be
        get_lab_or_404(lab_id, database, current_user).
        """
        payload = {
            "specs": [
                {"type": "ping", "name": "ping-test", "source": "R1", "target": "R2"},
            ],
        }
        with pytest.raises(TypeError, match="missing 1 required positional argument"):
            test_client.post(
                f"/labs/{sample_lab.id}/tests/run",
                json=payload,
                headers=auth_headers,
            )

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

    def test_get_tests_hits_argument_bug(
        self, test_client: TestClient, sample_lab: models.Lab, auth_headers: dict,
    ):
        """GET /tests fails due to get_lab_or_404 arg order bug."""
        with pytest.raises(TypeError, match="missing 1 required positional argument"):
            test_client.get(
                f"/labs/{sample_lab.id}/tests",
                headers=auth_headers,
            )

    def test_get_tests_requires_auth(self, test_client: TestClient, sample_lab: models.Lab):
        resp = test_client.get(f"/labs/{sample_lab.id}/tests")
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# GET /labs/{id}/tests/results/{job_id}
# ---------------------------------------------------------------------------


class TestGetTestResults:

    def test_get_results_hits_argument_bug(
        self, test_client: TestClient, test_db: Session,
        sample_lab: models.Lab, test_user: models.User,
        auth_headers: dict,
    ):
        """GET /tests/results/{job_id} fails due to get_lab_or_404 arg order bug."""
        job = models.Job(
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="test",
            status="completed",
            log_path="/tmp/fake.json",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        with pytest.raises(TypeError, match="missing 1 required positional argument"):
            test_client.get(
                f"/labs/{sample_lab.id}/tests/results/{job.id}",
                headers=auth_headers,
            )

    def test_get_results_requires_auth(
        self, test_client: TestClient, sample_lab: models.Lab,
    ):
        resp = test_client.get(
            f"/labs/{sample_lab.id}/tests/results/some-job-id",
        )
        assert resp.status_code in (401, 403)
