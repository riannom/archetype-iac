"""Tests for callbacks router endpoints."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models


class TestJobCompletionCallback:
    """Tests for POST /callbacks/job/{job_id}."""

    def test_job_callback_success(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        test_user: models.User,
    ):
        """Test successful job completion callback."""
        # Create a job
        job = models.Job(
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="running",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        # Send callback
        response = test_client.post(
            f"/callbacks/job/{job.id}",
            json={
                "job_id": job.id,
                "agent_id": "test-agent",
                "status": "completed",
                "stdout": "Lab deployed successfully",
                "stderr": "",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

        # Verify job was updated
        test_db.refresh(job)
        assert job.status == "completed"
        assert job.completed_at is not None
        assert "successfully" in job.log_path

    def test_job_callback_failure(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        test_user: models.User,
    ):
        """Test failed job callback."""
        job = models.Job(
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="running",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        response = test_client.post(
            f"/callbacks/job/{job.id}",
            json={
                "job_id": job.id,
                "agent_id": "test-agent",
                "status": "failed",
                "error_message": "Container failed to start",
                "stderr": "Error: port already in use",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

        test_db.refresh(job)
        assert job.status == "failed"
        assert "Container failed to start" in job.log_path

    def test_job_callback_job_id_mismatch(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        test_user: models.User,
    ):
        """Test callback with mismatched job IDs returns error."""
        job = models.Job(
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="running",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        response = test_client.post(
            f"/callbacks/job/{job.id}",
            json={
                "job_id": "different-job-id",
                "agent_id": "test-agent",
                "status": "completed",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "mismatch" in data["message"].lower()

    def test_job_callback_job_not_found(
        self, test_client: TestClient
    ):
        """Test callback for non-existent job."""
        response = test_client.post(
            "/callbacks/job/nonexistent-job-id",
            json={
                "job_id": "nonexistent-job-id",
                "agent_id": "test-agent",
                "status": "completed",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "not found" in data["message"].lower()

    def test_job_callback_idempotent(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        test_user: models.User,
    ):
        """Test that callback is idempotent for completed jobs."""
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
            f"/callbacks/job/{job.id}",
            json={
                "job_id": job.id,
                "agent_id": "test-agent",
                "status": "completed",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "already completed" in data["message"].lower()

    def test_job_callback_updates_lab_state_on_deploy(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        test_user: models.User,
        sample_host: models.Host,
    ):
        """Test that successful deploy callback updates lab state to running."""
        job = models.Job(
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="running",
            agent_id=sample_host.id,
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        response = test_client.post(
            f"/callbacks/job/{job.id}",
            json={
                "job_id": job.id,
                "agent_id": sample_host.id,
                "status": "completed",
            },
        )
        assert response.status_code == 200

        test_db.refresh(sample_lab)
        assert sample_lab.state == "running"

    def test_job_callback_updates_lab_state_on_down(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        test_user: models.User,
    ):
        """Test that successful down callback updates lab state to stopped."""
        sample_lab.state = "running"
        test_db.commit()

        job = models.Job(
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="down",
            status="running",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        response = test_client.post(
            f"/callbacks/job/{job.id}",
            json={
                "job_id": job.id,
                "agent_id": "test-agent",
                "status": "completed",
            },
        )
        assert response.status_code == 200

        test_db.refresh(sample_lab)
        assert sample_lab.state == "stopped"

    def test_job_callback_updates_lab_state_on_failure(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        test_user: models.User,
    ):
        """Test that failed job callback updates lab state to error."""
        job = models.Job(
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="running",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        response = test_client.post(
            f"/callbacks/job/{job.id}",
            json={
                "job_id": job.id,
                "agent_id": "test-agent",
                "status": "failed",
                "error_message": "Deployment failed",
            },
        )
        assert response.status_code == 200

        test_db.refresh(sample_lab)
        assert sample_lab.state == "error"

    def test_job_callback_updates_node_states(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab_with_nodes: tuple[models.Lab, list[models.NodeState]],
        test_user: models.User,
    ):
        """Test that callback with node_states updates NodeState records."""
        lab, nodes = sample_lab_with_nodes

        job = models.Job(
            lab_id=lab.id,
            user_id=test_user.id,
            action="up",
            status="running",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        response = test_client.post(
            f"/callbacks/job/{job.id}",
            json={
                "job_id": job.id,
                "agent_id": "test-agent",
                "status": "completed",
                "node_states": {
                    "R1": "running",
                    "R2": "running",
                },
            },
        )
        assert response.status_code == 200

        # Verify node states were updated
        for node in nodes:
            test_db.refresh(node)
            assert node.actual_state == "running"

    def test_job_callback_with_timestamps(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        test_user: models.User,
    ):
        """Test that callback respects provided timestamps."""
        job = models.Job(
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="running",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        started_at = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        completed_at = datetime(2024, 1, 15, 10, 5, 0, tzinfo=timezone.utc)

        response = test_client.post(
            f"/callbacks/job/{job.id}",
            json={
                "job_id": job.id,
                "agent_id": "test-agent",
                "status": "completed",
                "started_at": started_at.isoformat(),
                "completed_at": completed_at.isoformat(),
            },
        )
        assert response.status_code == 200

        test_db.refresh(job)
        # SQLite may strip timezone info; compare the date/time values
        assert job.started_at.replace(tzinfo=None) == started_at.replace(tzinfo=None)
        assert job.completed_at.replace(tzinfo=None) == completed_at.replace(tzinfo=None)


class TestDeadLetterCallback:
    """Tests for POST /callbacks/dead-letter/{job_id}."""

    def test_dead_letter_callback(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        test_user: models.User,
    ):
        """Test dead letter callback marks job as failed."""
        job = models.Job(
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="running",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        response = test_client.post(
            f"/callbacks/dead-letter/{job.id}",
            json={
                "job_id": job.id,
                "agent_id": "test-agent",
                "status": "completed",  # Original status
                "error_message": "Callback delivery failed",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

        test_db.refresh(job)
        assert job.status == "failed"
        assert "callback delivery failed" in job.log_path.lower()

    def test_dead_letter_callback_unknown_job(
        self, test_client: TestClient
    ):
        """Test dead letter callback for unknown job is logged."""
        response = test_client.post(
            "/callbacks/dead-letter/unknown-job",
            json={
                "job_id": "unknown-job",
                "agent_id": "test-agent",
                "status": "completed",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "not found" in data["message"].lower()

    def test_dead_letter_updates_lab_state_to_unknown(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        test_user: models.User,
    ):
        """Test dead letter callback sets lab state to unknown."""
        job = models.Job(
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="running",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        response = test_client.post(
            f"/callbacks/dead-letter/{job.id}",
            json={
                "job_id": job.id,
                "agent_id": "test-agent",
                "status": "completed",
            },
        )
        assert response.status_code == 200

        test_db.refresh(sample_lab)
        assert sample_lab.state == "unknown"


class TestUpdateProgressCallback:
    """Tests for POST /callbacks/update/{job_id}."""

    def test_update_progress_callback(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_host: models.Host,
    ):
        """Test update progress callback."""
        # Create an AgentUpdateJob
        update_job = models.AgentUpdateJob(
            id="update-job-1",
            host_id=sample_host.id,
            from_version="1.0.0",
            to_version="2.0.0",
            status="pending",
        )
        test_db.add(update_job)
        test_db.commit()

        response = test_client.post(
            f"/callbacks/update/{update_job.id}",
            json={
                "job_id": update_job.id,
                "agent_id": sample_host.id,
                "status": "downloading",
                "progress_percent": 50,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

        test_db.refresh(update_job)
        assert update_job.status == "downloading"
        assert update_job.progress_percent == 50
        assert update_job.started_at is not None

    def test_update_progress_callback_completed(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_host: models.Host,
    ):
        """Test update progress callback for completion."""
        update_job = models.AgentUpdateJob(
            id="update-job-2",
            host_id=sample_host.id,
            from_version="1.0.0",
            to_version="2.0.0",
            status="installing",
        )
        test_db.add(update_job)
        test_db.commit()

        response = test_client.post(
            f"/callbacks/update/{update_job.id}",
            json={
                "job_id": update_job.id,
                "agent_id": sample_host.id,
                "status": "completed",
                "progress_percent": 100,
            },
        )
        assert response.status_code == 200

        test_db.refresh(update_job)
        assert update_job.status == "completed"
        assert update_job.completed_at is not None

    def test_update_progress_callback_failed(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_host: models.Host,
    ):
        """Test update progress callback for failure."""
        update_job = models.AgentUpdateJob(
            id="update-job-3",
            host_id=sample_host.id,
            from_version="1.0.0",
            to_version="2.0.0",
            status="downloading",
        )
        test_db.add(update_job)
        test_db.commit()

        response = test_client.post(
            f"/callbacks/update/{update_job.id}",
            json={
                "job_id": update_job.id,
                "agent_id": sample_host.id,
                "status": "failed",
                "progress_percent": 30,
                "error_message": "Download interrupted",
            },
        )
        assert response.status_code == 200

        test_db.refresh(update_job)
        assert update_job.status == "failed"
        assert update_job.error_message == "Download interrupted"

    def test_update_progress_callback_unknown_job(
        self, test_client: TestClient
    ):
        """Test update callback for unknown job."""
        response = test_client.post(
            "/callbacks/update/unknown-job",
            json={
                "job_id": "unknown-job",
                "agent_id": "test-agent",
                "status": "downloading",
                "progress_percent": 50,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
