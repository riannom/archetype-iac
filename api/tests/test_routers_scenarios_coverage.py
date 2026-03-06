"""Additional tests for api/app/routers/scenarios.py — uncovered endpoints.

Covers:
- DELETE /labs/{id}/scenarios/{filename} — success, not found, lab not found, auth
- GET /labs/{id}/scenarios/{filename} — 404 when file doesn't exist
- POST execute — scenario not found, lab not found
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app import models
from app.config import settings


def _scenario_path(lab_id: str, filename: str) -> Path:
    sdir = Path(settings.workspace) / lab_id / "scenarios"
    sdir.mkdir(parents=True, exist_ok=True)
    return sdir / filename


# ===========================================================================
# DELETE /labs/{id}/scenarios/{filename}
# ===========================================================================


class TestDeleteScenario:

    def test_delete_scenario_success(
        self, test_client: TestClient, sample_lab: models.Lab, auth_headers: dict,
    ):
        """Deleting an existing scenario file succeeds."""
        path = _scenario_path(sample_lab.id, "to-delete.yml")
        path.write_text(
            "name: Deletable\nsteps:\n  - type: wait\n    seconds: 1\n",
            encoding="utf-8",
        )

        resp = test_client.delete(
            f"/labs/{sample_lab.id}/scenarios/to-delete.yml",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert not path.exists()

    def test_delete_scenario_not_found(
        self, test_client: TestClient, sample_lab: models.Lab, auth_headers: dict,
    ):
        """Deleting a non-existent scenario returns 404."""
        resp = test_client.delete(
            f"/labs/{sample_lab.id}/scenarios/nonexistent.yml",
            headers=auth_headers,
        )
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_delete_scenario_invalid_filename(
        self, test_client: TestClient, sample_lab: models.Lab, auth_headers: dict,
    ):
        """Deleting with an invalid filename returns 400."""
        resp = test_client.delete(
            f"/labs/{sample_lab.id}/scenarios/bad@name.yml",
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "Invalid filename" in resp.json()["detail"]

    def test_delete_scenario_lab_not_found(
        self, test_client: TestClient, auth_headers: dict,
    ):
        resp = test_client.delete(
            "/labs/nonexistent-lab/scenarios/test.yml",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_delete_scenario_requires_auth(
        self, test_client: TestClient, sample_lab: models.Lab,
    ):
        resp = test_client.delete(
            f"/labs/{sample_lab.id}/scenarios/test.yml",
        )
        assert resp.status_code in (401, 403)


# ===========================================================================
# GET /labs/{id}/scenarios/{filename} — 404 path
# ===========================================================================


class TestGetScenarioNotFound:

    def test_get_scenario_file_not_found(
        self, test_client: TestClient, sample_lab: models.Lab, auth_headers: dict,
    ):
        """Getting a non-existent scenario file returns 404."""
        resp = test_client.get(
            f"/labs/{sample_lab.id}/scenarios/nonexistent.yml",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_get_scenario_invalid_filename(
        self, test_client: TestClient, sample_lab: models.Lab, auth_headers: dict,
    ):
        """Getting a scenario with invalid filename returns 400."""
        resp = test_client.get(
            f"/labs/{sample_lab.id}/scenarios/bad@name.yml",
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_get_scenario_lab_not_found(
        self, test_client: TestClient, auth_headers: dict,
    ):
        resp = test_client.get(
            "/labs/nonexistent-lab/scenarios/test.yml",
            headers=auth_headers,
        )
        assert resp.status_code == 404


# ===========================================================================
# POST execute — error paths
# ===========================================================================


class TestExecuteScenarioErrors:

    def test_execute_scenario_not_found(
        self, test_client: TestClient, sample_lab: models.Lab, auth_headers: dict,
    ):
        """Executing a non-existent scenario returns 404."""
        resp = test_client.post(
            f"/labs/{sample_lab.id}/scenarios/nonexistent.yml/execute",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_execute_scenario_lab_not_found(
        self, test_client: TestClient, auth_headers: dict,
    ):
        resp = test_client.post(
            "/labs/nonexistent-lab/scenarios/test.yml/execute",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_execute_scenario_invalid_filename(
        self, test_client: TestClient, sample_lab: models.Lab, auth_headers: dict,
    ):
        """Executing with an invalid filename returns 400."""
        resp = test_client.post(
            f"/labs/{sample_lab.id}/scenarios/bad@name.yml/execute",
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_execute_scenario_requires_auth(
        self, test_client: TestClient, sample_lab: models.Lab,
    ):
        resp = test_client.post(
            f"/labs/{sample_lab.id}/scenarios/test.yml/execute",
        )
        assert resp.status_code in (401, 403)
