"""Tests for job management endpoints (routers/jobs.py).

This module tests:
- Lab up/down/restart operations
- Node start/stop operations
- Job listing and status
- Job cancellation
- Log retrieval
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models


class TestLabUp:
    """Tests for lab up endpoint."""

    def test_lab_up_no_agent(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Lab up fails when no agent available."""
        with patch("app.routers.jobs.agent_client.get_agent_for_lab", new_callable=AsyncMock) as mock:
            mock.return_value = None
            response = test_client.post(
                f"/labs/{sample_lab.id}/up",
                headers=auth_headers
            )
            assert response.status_code == 503
            assert "No healthy agent" in response.json()["detail"]

    def test_lab_up_creates_job(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
        tmp_path,
    ):
        """Lab up creates queued job and starts background task."""
        # Create topology file
        from app.config import settings
        with patch.object(settings, "netlab_workspace", str(tmp_path)):
            topo_dir = tmp_path / sample_lab.id
            topo_dir.mkdir(parents=True, exist_ok=True)
            topo_file = topo_dir / "topology.yml"
            topo_file.write_text("name: test\ntopology:\n  nodes:\n    r1:\n      kind: linux\n")

            with patch("app.routers.jobs.agent_client.get_agent_for_lab", new_callable=AsyncMock) as mock_agent:
                mock_agent.return_value = sample_host
                with patch("app.routers.jobs.topology_path") as mock_topo:
                    mock_topo.return_value = topo_file
                    with patch("app.routers.jobs.run_agent_job", new_callable=AsyncMock):
                        response = test_client.post(
                            f"/labs/{sample_lab.id}/up",
                            headers=auth_headers
                        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "queued"
        assert data["action"] == "up"
        assert data["lab_id"] == sample_lab.id


class TestLabDown:
    """Tests for lab down endpoint."""

    def test_lab_down_creates_job(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
    ):
        """Lab down creates queued job."""
        with patch("app.routers.jobs.agent_client.get_agent_for_lab", new_callable=AsyncMock) as mock_agent:
            mock_agent.return_value = sample_host
            with patch("app.routers.jobs.run_agent_job", new_callable=AsyncMock):
                response = test_client.post(
                    f"/labs/{sample_lab.id}/down",
                    headers=auth_headers
                )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "queued"
        assert data["action"] == "down"


class TestLabRestart:
    """Tests for lab restart endpoint."""

    def test_lab_restart_creates_jobs(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
        tmp_path,
    ):
        """Lab restart creates down then up jobs."""
        from app.config import settings
        with patch.object(settings, "netlab_workspace", str(tmp_path)):
            topo_dir = tmp_path / sample_lab.id
            topo_dir.mkdir(parents=True, exist_ok=True)
            topo_file = topo_dir / "topology.yml"
            topo_file.write_text("name: test\ntopology:\n  nodes:\n    r1:\n      kind: linux\n")

            with patch("app.routers.jobs.agent_client.get_agent_for_lab", new_callable=AsyncMock) as mock_agent:
                mock_agent.return_value = sample_host
                with patch("app.routers.jobs.topology_path") as mock_topo:
                    mock_topo.return_value = topo_file
                    with patch("app.routers.jobs.run_agent_job", new_callable=AsyncMock):
                        response = test_client.post(
                            f"/labs/{sample_lab.id}/restart",
                            headers=auth_headers
                        )

        assert response.status_code == 200
        # Returns the down job first
        data = response.json()
        assert data["action"] == "down"

    def test_lab_restart_no_topology(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
        tmp_path,
    ):
        """Lab restart fails if no topology file."""
        from app.config import settings
        with patch.object(settings, "netlab_workspace", str(tmp_path)):
            with patch("app.routers.jobs.agent_client.get_agent_for_lab", new_callable=AsyncMock) as mock_agent:
                mock_agent.return_value = sample_host
                with patch("app.routers.jobs.topology_path") as mock_topo:
                    mock_topo.return_value = tmp_path / "nonexistent" / "topology.yml"
                    response = test_client.post(
                        f"/labs/{sample_lab.id}/restart",
                        headers=auth_headers
                    )

        assert response.status_code == 400
        assert "No topology file" in response.json()["detail"]


class TestNodeAction:
    """Tests for node start/stop endpoints."""

    def test_node_start(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
    ):
        """Node start creates queued job."""
        with patch("app.routers.jobs.agent_client.get_agent_for_lab", new_callable=AsyncMock) as mock_agent:
            mock_agent.return_value = sample_host
            with patch("app.routers.jobs.run_agent_job", new_callable=AsyncMock):
                response = test_client.post(
                    f"/labs/{sample_lab.id}/nodes/router1/start",
                    headers=auth_headers
                )

        assert response.status_code == 200
        data = response.json()
        assert data["action"] == "node:start:router1"

    def test_node_stop(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
    ):
        """Node stop creates queued job."""
        with patch("app.routers.jobs.agent_client.get_agent_for_lab", new_callable=AsyncMock) as mock_agent:
            mock_agent.return_value = sample_host
            with patch("app.routers.jobs.run_agent_job", new_callable=AsyncMock):
                response = test_client.post(
                    f"/labs/{sample_lab.id}/nodes/router1/stop",
                    headers=auth_headers
                )

        assert response.status_code == 200
        data = response.json()
        assert data["action"] == "node:stop:router1"

    def test_node_invalid_action(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Invalid node action returns 400."""
        response = test_client.post(
            f"/labs/{sample_lab.id}/nodes/router1/restart",
            headers=auth_headers
        )
        assert response.status_code == 400
        assert "Unsupported" in response.json()["detail"]


class TestListJobs:
    """Tests for job listing endpoints."""

    def test_list_jobs_empty(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """List jobs returns empty when no jobs."""
        response = test_client.get(
            f"/labs/{sample_lab.id}/jobs",
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["jobs"] == []

    def test_list_jobs(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        test_user: models.User,
        auth_headers: dict,
    ):
        """List jobs returns lab's jobs."""
        # Create some jobs
        jobs = [
            models.Job(lab_id=sample_lab.id, user_id=test_user.id, action="up", status="completed"),
            models.Job(lab_id=sample_lab.id, user_id=test_user.id, action="down", status="queued"),
        ]
        for job in jobs:
            test_db.add(job)
        test_db.commit()

        response = test_client.get(
            f"/labs/{sample_lab.id}/jobs",
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["jobs"]) == 2

    def test_get_single_job(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        test_user: models.User,
        auth_headers: dict,
    ):
        """Get single job by ID."""
        job = models.Job(
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="completed"
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        response = test_client.get(
            f"/labs/{sample_lab.id}/jobs/{job.id}",
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == job.id
        assert data["action"] == "up"

    def test_get_job_not_found(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Get nonexistent job returns 404."""
        response = test_client.get(
            f"/labs/{sample_lab.id}/jobs/nonexistent-job",
            headers=auth_headers
        )
        assert response.status_code == 404


class TestJobLog:
    """Tests for job log retrieval."""

    def test_get_job_log_from_content(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        test_user: models.User,
        auth_headers: dict,
    ):
        """Get job log from inline content."""
        job = models.Job(
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="completed",
            log_path="Job completed successfully.\n\nSTDOUT: Lab deployed"
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        response = test_client.get(
            f"/labs/{sample_lab.id}/jobs/{job.id}/log",
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert "Lab deployed" in data["log"]

    def test_get_job_log_tail(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        test_user: models.User,
        auth_headers: dict,
    ):
        """Get job log with tail parameter."""
        log_content = "\n".join([f"Line {i}" for i in range(100)])
        job = models.Job(
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="completed",
            log_path=log_content
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        response = test_client.get(
            f"/labs/{sample_lab.id}/jobs/{job.id}/log?tail=5",
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        lines = data["log"].split("\n")
        assert len(lines) == 5
        assert "Line 99" in data["log"]

    def test_get_job_log_not_found(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        test_user: models.User,
        auth_headers: dict,
    ):
        """Get log for job without log returns 404."""
        job = models.Job(
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="queued",
            log_path=None
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        response = test_client.get(
            f"/labs/{sample_lab.id}/jobs/{job.id}/log",
            headers=auth_headers
        )
        assert response.status_code == 404


class TestCancelJob:
    """Tests for job cancellation."""

    def test_cancel_queued_job(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        test_user: models.User,
        auth_headers: dict,
    ):
        """Cancel a queued job."""
        job = models.Job(
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="queued",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        response = test_client.post(
            f"/labs/{sample_lab.id}/jobs/{job.id}/cancel",
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "cancelled"

        test_db.refresh(job)
        assert job.status == "cancelled"
        assert job.completed_at is not None

    def test_cancel_running_job(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        test_user: models.User,
        auth_headers: dict,
    ):
        """Cancel a running job."""
        job = models.Job(
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="running",
            started_at=datetime.now(timezone.utc),
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        response = test_client.post(
            f"/labs/{sample_lab.id}/jobs/{job.id}/cancel",
            headers=auth_headers
        )
        assert response.status_code == 200

        test_db.refresh(job)
        assert job.status == "cancelled"

    def test_cancel_completed_job_fails(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        test_user: models.User,
        auth_headers: dict,
    ):
        """Cannot cancel completed job."""
        job = models.Job(
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="completed",
            completed_at=datetime.now(timezone.utc),
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        response = test_client.post(
            f"/labs/{sample_lab.id}/jobs/{job.id}/cancel",
            headers=auth_headers
        )
        assert response.status_code == 400
        assert "Cannot cancel" in response.json()["detail"]

    def test_cancel_failed_job_fails(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        test_user: models.User,
        auth_headers: dict,
    ):
        """Cannot cancel failed job."""
        job = models.Job(
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="failed",
            completed_at=datetime.now(timezone.utc),
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        response = test_client.post(
            f"/labs/{sample_lab.id}/jobs/{job.id}/cancel",
            headers=auth_headers
        )
        assert response.status_code == 400


class TestJobEnrichment:
    """Tests for job output enrichment."""

    def test_error_summary_extraction(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        test_user: models.User,
        auth_headers: dict,
    ):
        """Failed job includes error_summary field."""
        job = models.Job(
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="failed",
            log_path="ERROR: Job execution failed\n\nDetails: Image not found\n\n=== STDERR ===\nError: image ceos:4.28.0F not found",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        response = test_client.get(
            f"/labs/{sample_lab.id}/jobs/{job.id}",
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert "error_summary" in data
        assert data["error_summary"] is not None

    def test_completed_job_no_error_summary(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        test_user: models.User,
        auth_headers: dict,
    ):
        """Completed job has no error_summary."""
        job = models.Job(
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="completed",
            log_path="Job completed successfully",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        response = test_client.get(
            f"/labs/{sample_lab.id}/jobs/{job.id}",
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data.get("error_summary") is None


class TestLabStatus:
    """Tests for lab status endpoint."""

    def test_lab_status_from_agent(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        auth_headers: dict,
    ):
        """Lab status fetched from agent."""
        with patch("app.routers.jobs.agent_client.get_agent_for_lab", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = sample_host
            with patch("app.routers.jobs.agent_client.get_lab_status_from_agent", new_callable=AsyncMock) as mock_status:
                mock_status.return_value = {
                    "nodes": [
                        {"name": "r1", "status": "running"},
                        {"name": "r2", "status": "running"},
                    ]
                }
                response = test_client.get(
                    f"/labs/{sample_lab.id}/status",
                    headers=auth_headers
                )

        assert response.status_code == 200
        data = response.json()
        assert len(data["nodes"]) == 2
        assert data["agent_id"] == sample_host.id

    def test_lab_status_no_agent(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Lab status falls back to netlab when no agent."""
        with patch("app.routers.jobs.agent_client.get_agent_for_lab", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = None
            with patch("app.routers.jobs.run_netlab_command") as mock_netlab:
                mock_netlab.return_value = (0, "Status output", "")
                response = test_client.get(
                    f"/labs/{sample_lab.id}/status",
                    headers=auth_headers
                )

        assert response.status_code == 200
        data = response.json()
        assert "raw" in data
