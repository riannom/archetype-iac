"""Tests for untested endpoints in api/app/routers/jobs.py.

Covers:
- GET /labs/{id}/status (lab_status) — success, lab not found, no agents
- GET /labs/{id}/audit (audit_log) — success, empty, lab not found
"""
from __future__ import annotations


from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models


# ===========================================================================
# GET /labs/{id}/status
# ===========================================================================


class TestLabStatus:

    def test_lab_status_success_with_agent(
        self, test_client: TestClient, test_db: Session,
        sample_lab: models.Lab, sample_host: models.Host,
        auth_headers: dict, monkeypatch,
    ):
        """Lab status with a placed agent returns node information."""
        # Assign agent to lab
        sample_lab.agent_id = sample_host.id
        test_db.commit()

        # Mock agent_client calls
        import app.agent_client as ac_mod

        async def _fake_get_lab_status(agent, lab_id):
            return {
                "nodes": [
                    {"name": "R1", "status": "running"},
                    {"name": "R2", "status": "running"},
                ],
                "error": None,
            }

        async def _fake_get_healthy_agent(db, required_provider=None):
            return sample_host

        monkeypatch.setattr(ac_mod, "get_lab_status_from_agent", _fake_get_lab_status)
        monkeypatch.setattr(ac_mod, "get_healthy_agent", _fake_get_healthy_agent)

        resp = test_client.get(
            f"/labs/{sample_lab.id}/status",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "nodes" in data
        assert "agents" in data
        assert data["is_multi_host"] is False

    def test_lab_status_lab_not_found(
        self, test_client: TestClient, auth_headers: dict,
    ):
        resp = test_client.get(
            "/labs/nonexistent-lab/status",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_lab_status_no_agents_returns_503(
        self, test_client: TestClient, test_db: Session,
        sample_lab: models.Lab, auth_headers: dict, monkeypatch,
    ):
        """Lab with no agents returns 503."""
        import app.agent_client as ac_mod

        async def _fake_get_healthy_agent(db, required_provider=None):
            return None

        monkeypatch.setattr(ac_mod, "get_healthy_agent", _fake_get_healthy_agent)

        resp = test_client.get(
            f"/labs/{sample_lab.id}/status",
            headers=auth_headers,
        )
        assert resp.status_code == 503
        assert "No agents" in resp.json()["detail"]

    def test_lab_status_requires_auth(
        self, test_client: TestClient, sample_lab: models.Lab,
    ):
        resp = test_client.get(f"/labs/{sample_lab.id}/status")
        assert resp.status_code in (401, 403)


# ===========================================================================
# GET /labs/{id}/audit
# ===========================================================================


class TestAuditLog:

    def test_audit_log_success_with_jobs(
        self, test_client: TestClient, test_db: Session,
        sample_lab: models.Lab, test_user: models.User, auth_headers: dict,
    ):
        """Audit log returns job entries for the lab."""
        job1 = models.Job(
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="completed",
        )
        job2 = models.Job(
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="down",
            status="completed",
        )
        test_db.add_all([job1, job2])
        test_db.commit()

        resp = test_client.get(
            f"/labs/{sample_lab.id}/audit",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "jobs" in data
        assert len(data["jobs"]) == 2

    def test_audit_log_empty(
        self, test_client: TestClient, sample_lab: models.Lab, auth_headers: dict,
    ):
        """Audit log with no jobs returns empty list."""
        resp = test_client.get(
            f"/labs/{sample_lab.id}/audit",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["jobs"] == []

    def test_audit_log_lab_not_found(
        self, test_client: TestClient, auth_headers: dict,
    ):
        resp = test_client.get(
            "/labs/nonexistent-lab/audit",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_audit_log_requires_auth(
        self, test_client: TestClient, sample_lab: models.Lab,
    ):
        resp = test_client.get(f"/labs/{sample_lab.id}/audit")
        assert resp.status_code in (401, 403)
