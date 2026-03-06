"""Tests for api/app/routers/lab_tests.py — verification test endpoints (round 11).

Covers run_lab_tests, get_lab_tests, get_test_results with patched
get_lab_or_404 to bypass the known arg-order bug.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models
from app.config import settings
from app.state import JobStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _patch_get_lab(lab: models.Lab):
    """Patch get_lab_or_404 to return the given lab regardless of arg order."""
    return patch(
        "app.routers.lab_tests.get_lab_or_404",
        return_value=lab,
    )


# ---------------------------------------------------------------------------
# POST /labs/{id}/tests/run
# ---------------------------------------------------------------------------


class TestRunLabTestsRound11:

    def test_run_with_explicit_specs(
        self, test_client: TestClient, sample_lab: models.Lab,
        auth_headers: dict, monkeypatch,
    ):
        """Happy path — explicit specs provided."""
        with _patch_get_lab(sample_lab), \
             patch("app.routers.lab_tests.safe_create_task") as mock_task:
            resp = test_client.post(
                f"/labs/{sample_lab.id}/tests/run",
                json={
                    "specs": [
                        {"type": "ping", "name": "p1", "source": "R1", "target": "R2"},
                    ],
                },
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "job_id" in data
        assert "1 tests" in data["message"] or "1 test" in data["message"]
        mock_task.assert_called_once()

    def test_run_with_multiple_specs(
        self, test_client: TestClient, sample_lab: models.Lab,
        auth_headers: dict,
    ):
        specs = [
            {"type": "ping", "name": "p1", "source": "R1", "target": "R2"},
            {"type": "command", "name": "c1", "source": "R1", "command": "show version", "expected": "ok"},
        ]
        with _patch_get_lab(sample_lab), \
             patch("app.routers.lab_tests.safe_create_task"):
            resp = test_client.post(
                f"/labs/{sample_lab.id}/tests/run",
                json={"specs": specs},
                headers=auth_headers,
            )
        assert resp.status_code == 200
        assert "2 tests" in resp.json()["message"]

    def test_run_yaml_fallback(
        self, test_client: TestClient, sample_lab: models.Lab,
        auth_headers: dict, tmp_path, monkeypatch,
    ):
        """When no specs provided, load from topology YAML."""
        ws = tmp_path / "workspace" / sample_lab.id
        ws.mkdir(parents=True)
        (ws / "topology.yml").write_text("name: lab\n")

        mock_graph = MagicMock()
        mock_graph.tests = [{"type": "ping", "name": "t1", "source": "R1", "target": "R2"}]
        with _patch_get_lab(sample_lab), \
             patch("app.routers.lab_tests.safe_create_task"), \
             patch("app.topology.yaml_to_graph", return_value=mock_graph):
            resp = test_client.post(
                f"/labs/{sample_lab.id}/tests/run",
                json=None,
                headers=auth_headers,
            )
        assert resp.status_code == 200

    def test_run_no_specs_returns_400(
        self, test_client: TestClient, sample_lab: models.Lab,
        auth_headers: dict, tmp_path,
    ):
        """No specs provided and no YAML tests → 400."""
        ws = tmp_path / "workspace" / sample_lab.id
        ws.mkdir(parents=True)
        # No topology.yml
        with _patch_get_lab(sample_lab):
            resp = test_client.post(
                f"/labs/{sample_lab.id}/tests/run",
                json={"specs": []},
                headers=auth_headers,
            )
        assert resp.status_code == 400
        assert "No test specs" in resp.json()["detail"]

    def test_run_empty_specs_no_yaml_400(
        self, test_client: TestClient, sample_lab: models.Lab,
        auth_headers: dict,
    ):
        with _patch_get_lab(sample_lab):
            resp = test_client.post(
                f"/labs/{sample_lab.id}/tests/run",
                json={},
                headers=auth_headers,
            )
        assert resp.status_code == 400

    def test_run_job_created_with_correct_fields(
        self, test_client: TestClient, test_db: Session,
        sample_lab: models.Lab, auth_headers: dict,
    ):
        with _patch_get_lab(sample_lab), \
             patch("app.routers.lab_tests.safe_create_task"):
            resp = test_client.post(
                f"/labs/{sample_lab.id}/tests/run",
                json={"specs": [{"type": "ping", "name": "p", "source": "A", "target": "B"}]},
                headers=auth_headers,
            )
        job_id = resp.json()["job_id"]
        job = test_db.get(models.Job, job_id)
        assert job is not None
        assert job.action == "test"
        assert job.status == JobStatus.QUEUED
        assert job.lab_id == sample_lab.id

    def test_run_yaml_parse_error_falls_through(
        self, test_client: TestClient, sample_lab: models.Lab,
        auth_headers: dict, tmp_path,
    ):
        """YAML parse error → fallthrough → 400."""
        ws = tmp_path / "workspace" / sample_lab.id
        ws.mkdir(parents=True)
        (ws / "topology.yml").write_text("name: lab\n")

        with _patch_get_lab(sample_lab), \
             patch("app.topology.yaml_to_graph", side_effect=Exception("bad yaml")):
            resp = test_client.post(
                f"/labs/{sample_lab.id}/tests/run",
                json={},
                headers=auth_headers,
            )
        assert resp.status_code == 400

    def test_run_requires_auth(
        self, test_client: TestClient, sample_lab: models.Lab,
    ):
        resp = test_client.post(f"/labs/{sample_lab.id}/tests/run", json={})
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# GET /labs/{id}/tests
# ---------------------------------------------------------------------------


class TestGetLabTestsRound11:

    def test_returns_yaml_tests(
        self, test_client: TestClient, sample_lab: models.Lab,
        auth_headers: dict, tmp_path,
    ):
        ws = tmp_path / "workspace" / sample_lab.id
        ws.mkdir(parents=True)
        (ws / "topology.yml").write_text("name: lab\n")

        mock_graph = MagicMock()
        mock_graph.tests = [{"type": "ping", "name": "t1"}]
        with _patch_get_lab(sample_lab), \
             patch("app.topology.yaml_to_graph", return_value=mock_graph):
            resp = test_client.get(
                f"/labs/{sample_lab.id}/tests",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        assert resp.json()["tests"] == [{"type": "ping", "name": "t1"}]

    def test_empty_on_no_topology_file(
        self, test_client: TestClient, sample_lab: models.Lab,
        auth_headers: dict, tmp_path,
    ):
        # workspace dir exists but no topology.yml
        ws = tmp_path / "workspace" / sample_lab.id
        ws.mkdir(parents=True)
        with _patch_get_lab(sample_lab):
            resp = test_client.get(
                f"/labs/{sample_lab.id}/tests",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        assert resp.json()["tests"] == []

    def test_empty_on_parse_error(
        self, test_client: TestClient, sample_lab: models.Lab,
        auth_headers: dict, tmp_path,
    ):
        ws = tmp_path / "workspace" / sample_lab.id
        ws.mkdir(parents=True)
        (ws / "topology.yml").write_text("name: lab\n")
        with _patch_get_lab(sample_lab), \
             patch("app.topology.yaml_to_graph", side_effect=Exception("bad")):
            resp = test_client.get(
                f"/labs/{sample_lab.id}/tests",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        assert resp.json()["tests"] == []

    def test_empty_when_graph_has_no_tests(
        self, test_client: TestClient, sample_lab: models.Lab,
        auth_headers: dict, tmp_path,
    ):
        ws = tmp_path / "workspace" / sample_lab.id
        ws.mkdir(parents=True)
        (ws / "topology.yml").write_text("name: lab\n")
        mock_graph = MagicMock()
        mock_graph.tests = None
        with _patch_get_lab(sample_lab), \
             patch("app.topology.yaml_to_graph", return_value=mock_graph):
            resp = test_client.get(f"/labs/{sample_lab.id}/tests", headers=auth_headers)
        assert resp.json()["tests"] == []


# ---------------------------------------------------------------------------
# GET /labs/{id}/tests/results/{job_id}
# ---------------------------------------------------------------------------


class TestGetTestResultsRound11:

    def test_returns_json_results(
        self, test_client: TestClient, test_db: Session,
        sample_lab: models.Lab, test_user: models.User,
        auth_headers: dict, tmp_path,
    ):
        results_file = tmp_path / "results.json"
        results_file.write_text(json.dumps({"tests": [{"name": "t1", "passed": True}]}))
        job = models.Job(
            lab_id=sample_lab.id, user_id=test_user.id,
            action="test", status=JobStatus.COMPLETED,
            log_path=str(results_file),
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)
        with _patch_get_lab(sample_lab):
            resp = test_client.get(
                f"/labs/{sample_lab.id}/tests/results/{job.id}",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        assert resp.json()["tests"][0]["passed"] is True

    def test_404_on_missing_job(
        self, test_client: TestClient, sample_lab: models.Lab,
        auth_headers: dict,
    ):
        with _patch_get_lab(sample_lab):
            resp = test_client.get(
                f"/labs/{sample_lab.id}/tests/results/nonexistent",
                headers=auth_headers,
            )
        assert resp.status_code == 404

    def test_404_on_wrong_lab(
        self, test_client: TestClient, test_db: Session,
        sample_lab: models.Lab, test_user: models.User,
        auth_headers: dict,
    ):
        """Job exists but belongs to different lab."""
        job = models.Job(
            lab_id="other-lab", user_id=test_user.id,
            action="test", status=JobStatus.COMPLETED,
            log_path="/tmp/fake.json",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)
        with _patch_get_lab(sample_lab):
            resp = test_client.get(
                f"/labs/{sample_lab.id}/tests/results/{job.id}",
                headers=auth_headers,
            )
        assert resp.status_code == 404

    def test_status_message_for_running_job(
        self, test_client: TestClient, test_db: Session,
        sample_lab: models.Lab, test_user: models.User,
        auth_headers: dict,
    ):
        job = models.Job(
            lab_id=sample_lab.id, user_id=test_user.id,
            action="test", status=JobStatus.RUNNING,
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)
        with _patch_get_lab(sample_lab):
            resp = test_client.get(
                f"/labs/{sample_lab.id}/tests/results/{job.id}",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        assert "still running" in resp.json()["message"]

    def test_status_message_for_queued_job(
        self, test_client: TestClient, test_db: Session,
        sample_lab: models.Lab, test_user: models.User,
        auth_headers: dict,
    ):
        job = models.Job(
            lab_id=sample_lab.id, user_id=test_user.id,
            action="test", status=JobStatus.QUEUED,
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)
        with _patch_get_lab(sample_lab):
            resp = test_client.get(
                f"/labs/{sample_lab.id}/tests/results/{job.id}",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        assert "still running" in resp.json()["message"]

    def test_404_on_missing_results_file(
        self, test_client: TestClient, test_db: Session,
        sample_lab: models.Lab, test_user: models.User,
        auth_headers: dict,
    ):
        job = models.Job(
            lab_id=sample_lab.id, user_id=test_user.id,
            action="test", status=JobStatus.COMPLETED,
            log_path="/tmp/nonexistent_results.json",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)
        with _patch_get_lab(sample_lab):
            resp = test_client.get(
                f"/labs/{sample_lab.id}/tests/results/{job.id}",
                headers=auth_headers,
            )
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"]

    def test_500_on_corrupt_json(
        self, test_client: TestClient, test_db: Session,
        sample_lab: models.Lab, test_user: models.User,
        auth_headers: dict, tmp_path,
    ):
        bad_file = tmp_path / "corrupt.json"
        bad_file.write_text("{not valid json!!")
        job = models.Job(
            lab_id=sample_lab.id, user_id=test_user.id,
            action="test", status=JobStatus.COMPLETED,
            log_path=str(bad_file),
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)
        with _patch_get_lab(sample_lab):
            resp = test_client.get(
                f"/labs/{sample_lab.id}/tests/results/{job.id}",
                headers=auth_headers,
            )
        assert resp.status_code == 500
        assert "corrupted" in resp.json()["detail"]

    def test_404_on_no_log_path(
        self, test_client: TestClient, test_db: Session,
        sample_lab: models.Lab, test_user: models.User,
        auth_headers: dict,
    ):
        job = models.Job(
            lab_id=sample_lab.id, user_id=test_user.id,
            action="test", status=JobStatus.COMPLETED,
            log_path=None,
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)
        with _patch_get_lab(sample_lab):
            resp = test_client.get(
                f"/labs/{sample_lab.id}/tests/results/{job.id}",
                headers=auth_headers,
            )
        assert resp.status_code == 404
