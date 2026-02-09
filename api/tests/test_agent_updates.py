"""Tests for agent update endpoints and completion detection.

Covers:
- trigger_agent_update: success, offline rejection, already-at-version,
  Docker rejection, concurrent guard (409)
- trigger_bulk_update: mixed results, parallel execution
- get_update_status / list_update_jobs: basic CRUD
- Completion detection: re-registration with matching version/SHA -> completed
- Completion detection: version mismatch -> stays active
- git_sha stored on registration
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models
from app.config import settings


def _sqlite_safe_check_update_completion(database, agent_id, new_version, new_commit):
    """SQLite-compatible version of _check_update_completion.

    SQLite strips timezone info from DateTime columns, causing TypeError
    when comparing aware ``now`` with naive ``job.started_at``.  This
    reimplementation normalises both sides to aware UTC before the
    subtraction so the tests pass on SQLite-backed CI.
    """
    active_statuses = ("pending", "downloading", "installing", "restarting")
    active_jobs = (
        database.query(models.AgentUpdateJob)
        .filter(
            models.AgentUpdateJob.host_id == agent_id,
            models.AgentUpdateJob.status.in_(active_statuses),
        )
        .order_by(models.AgentUpdateJob.created_at.desc())
        .all()
    )

    now = datetime.now(timezone.utc)
    for job in active_jobs:
        version_match = new_version == job.to_version
        commit_match = (
            new_commit
            and new_commit != "unknown"
            and job.to_version
            and new_commit.startswith(job.to_version)
        )

        if version_match or commit_match:
            job.status = "completed"
            job.progress_percent = 100
            job.completed_at = now
        elif job.status == "restarting":
            started = job.started_at
            if started:
                # Normalise naive datetimes returned by SQLite
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
                if (now - started).total_seconds() > 600:
                    job.status = "failed"
                    job.error_message = (
                        "Agent did not re-register with expected version after update"
                    )
                    job.completed_at = now

    if active_jobs:
        database.commit()


# ---- Fixtures ----

@pytest.fixture
def online_host(test_db: Session) -> models.Host:
    """Create an online systemd agent."""
    host = models.Host(
        id="agent-update-test",
        name="Update Test Agent",
        address="10.0.0.1:8080",
        status="online",
        capabilities=json.dumps({"providers": ["docker"]}),
        version="0.3.6",
        git_sha="aabbccdd" + "0" * 32,
        deployment_mode="systemd",
        last_heartbeat=datetime.now(timezone.utc),
        resource_usage="{}",
    )
    test_db.add(host)
    test_db.commit()
    test_db.refresh(host)
    return host


@pytest.fixture
def docker_host(test_db: Session) -> models.Host:
    """Create an online Docker agent."""
    host = models.Host(
        id="agent-docker-test",
        name="Docker Agent",
        address="10.0.0.2:8080",
        status="online",
        capabilities=json.dumps({"providers": ["docker"]}),
        version="0.3.6",
        deployment_mode="docker",
        is_local=True,
        last_heartbeat=datetime.now(timezone.utc),
        resource_usage="{}",
    )
    test_db.add(host)
    test_db.commit()
    test_db.refresh(host)
    return host


@pytest.fixture
def offline_host(test_db: Session) -> models.Host:
    """Create an offline agent."""
    host = models.Host(
        id="agent-offline-test",
        name="Offline Agent",
        address="10.0.0.3:8080",
        status="offline",
        capabilities=json.dumps({}),
        version="0.3.5",
        deployment_mode="systemd",
        resource_usage="{}",
    )
    test_db.add(host)
    test_db.commit()
    test_db.refresh(host)
    return host


def _mock_agent_update_response(accepted=True, message="Update initiated", deployment_mode="systemd"):
    """Create a mock httpx response for agent update requests.

    Uses MagicMock (not AsyncMock) because httpx.Response.json() is sync.
    """
    from unittest.mock import MagicMock

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "accepted": accepted,
        "message": message,
        "deployment_mode": deployment_mode,
    }
    return mock_response


# ---- trigger_agent_update tests ----

class TestTriggerAgentUpdate:
    """Tests for POST /agents/{id}/update."""

    @patch("httpx.AsyncClient")
    def test_success(self, mock_client_cls, test_client: TestClient, test_db: Session, online_host):
        """Successful update creates job and sends request to agent."""
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=_mock_agent_update_response())
        mock_client_cls.return_value = mock_client

        response = test_client.post(
            f"/agents/{online_host.id}/update",
            json={"target_version": "0.3.7"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "downloading"
        assert data["to_version"] == "0.3.7"
        assert data["agent_id"] == online_host.id

        # Verify job in DB
        job = test_db.query(models.AgentUpdateJob).filter_by(host_id=online_host.id).first()
        assert job is not None
        assert job.status == "downloading"
        assert job.from_version == "0.3.6"
        assert job.to_version == "0.3.7"

    def test_offline_rejected(self, test_client: TestClient, offline_host):
        """Update of offline agent returns 503."""
        response = test_client.post(
            f"/agents/{offline_host.id}/update",
            json={"target_version": "0.3.7"},
        )
        assert response.status_code == 503

    def test_already_at_version(self, test_client: TestClient, online_host):
        """Update to same version returns 400."""
        response = test_client.post(
            f"/agents/{online_host.id}/update",
            json={"target_version": "0.3.6"},
        )
        assert response.status_code == 400
        assert "already at version" in response.json()["detail"].lower()

    def test_docker_agent_rejected(self, test_client: TestClient, docker_host):
        """Docker agents get 400 with guidance to use rebuild."""
        response = test_client.post(
            f"/agents/{docker_host.id}/update",
            json={"target_version": "0.3.7"},
        )
        assert response.status_code == 400
        assert "rebuild" in response.json()["detail"].lower()

    @patch("httpx.AsyncClient")
    def test_concurrent_guard(self, mock_client_cls, test_client: TestClient, test_db: Session, online_host):
        """Second update while first is in progress returns 409."""
        # Create an active update job
        active_job = models.AgentUpdateJob(
            id="existing-job-1",
            host_id=online_host.id,
            from_version="0.3.5",
            to_version="0.3.6",
            status="downloading",
        )
        test_db.add(active_job)
        test_db.commit()

        response = test_client.post(
            f"/agents/{online_host.id}/update",
            json={"target_version": "0.3.7"},
        )
        assert response.status_code == 409
        assert "already in progress" in response.json()["detail"].lower()

    def test_agent_not_found(self, test_client: TestClient):
        """Update for nonexistent agent returns 404."""
        response = test_client.post(
            "/agents/nonexistent-agent/update",
            json={"target_version": "0.3.7"},
        )
        assert response.status_code == 404


# ---- trigger_bulk_update tests ----

class TestTriggerBulkUpdate:
    """Tests for POST /agents/updates/bulk."""

    @patch("httpx.AsyncClient")
    def test_mixed_results(self, mock_client_cls, test_client: TestClient, test_db: Session, online_host, offline_host):
        """Bulk update returns success for online agents, failure for offline."""
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=_mock_agent_update_response())
        mock_client_cls.return_value = mock_client

        response = test_client.post(
            "/agents/updates/bulk",
            json={
                "agent_ids": [online_host.id, offline_host.id],
                "target_version": "0.3.7",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success_count"] == 1
        assert data["failure_count"] == 1

        # Find results by agent_id
        results_by_id = {r["agent_id"]: r for r in data["results"]}
        assert results_by_id[online_host.id]["success"] is True
        assert results_by_id[offline_host.id]["success"] is False

    @patch("httpx.AsyncClient")
    def test_docker_agent_skipped(self, mock_client_cls, test_client: TestClient, docker_host):
        """Docker agents are skipped in bulk update."""
        response = test_client.post(
            "/agents/updates/bulk",
            json={
                "agent_ids": [docker_host.id],
                "target_version": "0.3.7",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["failure_count"] == 1
        assert "rebuild" in data["results"][0]["error"].lower()


# ---- get_update_status / list_update_jobs tests ----

class TestUpdateStatusEndpoints:
    """Tests for update status and job listing."""

    def test_get_update_status(self, test_client: TestClient, test_db: Session, online_host):
        """Returns most recent update job."""
        job = models.AgentUpdateJob(
            id="status-job-1",
            host_id=online_host.id,
            from_version="0.3.5",
            to_version="0.3.6",
            status="completed",
            progress_percent=100,
        )
        test_db.add(job)
        test_db.commit()

        response = test_client.get(f"/agents/{online_host.id}/update-status")
        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == "status-job-1"
        assert data["status"] == "completed"
        assert data["progress_percent"] == 100

    def test_get_update_status_no_jobs(self, test_client: TestClient, online_host):
        """Returns null when no update jobs exist."""
        response = test_client.get(f"/agents/{online_host.id}/update-status")
        assert response.status_code == 200
        assert response.json() is None

    def test_list_update_jobs(self, test_client: TestClient, test_db: Session, online_host):
        """Lists recent update jobs in reverse chronological order."""
        for i in range(3):
            job = models.AgentUpdateJob(
                id=f"list-job-{i}",
                host_id=online_host.id,
                from_version=f"0.3.{i}",
                to_version=f"0.3.{i+1}",
                status="completed" if i < 2 else "failed",
                progress_percent=100 if i < 2 else 0,
            )
            test_db.add(job)
        test_db.commit()

        response = test_client.get(f"/agents/{online_host.id}/update-jobs?limit=10")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 3


# ---- Completion detection tests ----

class TestUpdateCompletion:
    """Tests for _check_update_completion on re-registration."""

    def test_version_match_completes_job(self, test_client: TestClient, test_db: Session):
        """Re-registration with matching version marks job completed."""
        # Create host with active update job
        host = models.Host(
            id="completion-agent-1",
            name="Completion Agent",
            address="10.0.0.10:8080",
            status="online",
            capabilities=json.dumps({}),
            version="0.3.6",
            deployment_mode="systemd",
            resource_usage="{}",
            last_heartbeat=datetime.now(timezone.utc),
        )
        test_db.add(host)
        test_db.commit()

        job = models.AgentUpdateJob(
            id="completion-job-1",
            host_id="completion-agent-1",
            from_version="0.3.6",
            to_version="0.3.7",
            status="restarting",
            started_at=datetime.now(timezone.utc),
        )
        test_db.add(job)
        test_db.commit()

        # Re-register with new version
        response = test_client.post(
            "/agents/register",
            json={
                "agent": {
                    "agent_id": "completion-agent-1",
                    "name": "Completion Agent",
                    "address": "10.0.0.10:8080",
                    "capabilities": {"providers": []},
                    "version": "0.3.7",
                    "commit": "abc1234",
                    "deployment_mode": "systemd",
                },
            },
        )
        assert response.status_code == 200

        # Job should be completed
        test_db.refresh(job)
        assert job.status == "completed"
        assert job.progress_percent == 100

    def test_commit_sha_match_completes_job(self, test_client: TestClient, test_db: Session):
        """Re-registration with commit SHA matching to_version prefix marks job completed."""
        host = models.Host(
            id="sha-completion-1",
            name="SHA Agent",
            address="10.0.0.11:8080",
            status="online",
            capabilities=json.dumps({}),
            version="0.3.6",
            deployment_mode="systemd",
            resource_usage="{}",
            last_heartbeat=datetime.now(timezone.utc),
        )
        test_db.add(host)
        test_db.commit()

        # Job targets a commit SHA
        job = models.AgentUpdateJob(
            id="sha-job-1",
            host_id="sha-completion-1",
            from_version="0.3.6",
            to_version="abc1234",
            status="restarting",
            started_at=datetime.now(timezone.utc),
        )
        test_db.add(job)
        test_db.commit()

        # Re-register: version stays same, but commit starts with target
        response = test_client.post(
            "/agents/register",
            json={
                "agent": {
                    "agent_id": "sha-completion-1",
                    "name": "SHA Agent",
                    "address": "10.0.0.11:8080",
                    "capabilities": {"providers": []},
                    "version": "0.3.6",
                    "commit": "abc12345678abcdef0123456789abcdef01234567",
                    "deployment_mode": "systemd",
                },
            },
        )
        assert response.status_code == 200

        test_db.refresh(job)
        assert job.status == "completed"

    @patch("app.routers.agents._check_update_completion", _sqlite_safe_check_update_completion)
    def test_version_mismatch_stays_active(self, test_client: TestClient, test_db: Session):
        """Re-registration with wrong version doesn't complete job (unless timed out)."""
        object.__setattr__(settings, "image_sync_enabled", False)
        try:
            host = models.Host(
                id="mismatch-agent-1",
                name="Mismatch Agent",
                address="10.0.0.12:8080",
                status="online",
                capabilities=json.dumps({}),
                version="0.3.5",
                deployment_mode="systemd",
                resource_usage="{}",
                last_heartbeat=datetime.now(timezone.utc),
            )
            test_db.add(host)
            test_db.commit()

            # Job was recently started (not timed out)
            job = models.AgentUpdateJob(
                id="mismatch-job-1",
                host_id="mismatch-agent-1",
                from_version="0.3.5",
                to_version="0.3.7",
                status="restarting",
                started_at=datetime.now(timezone.utc),
            )
            test_db.add(job)
            test_db.commit()

            # Re-register with wrong version (still 0.3.5, update didn't take)
            response = test_client.post(
                "/agents/register",
                json={
                    "agent": {
                        "agent_id": "mismatch-agent-1",
                        "name": "Mismatch Agent",
                        "address": "10.0.0.12:8080",
                        "capabilities": {"providers": []},
                        "version": "0.3.5",
                        "commit": "",
                        "deployment_mode": "systemd",
                    },
                },
            )
            assert response.status_code == 200

            # Job should still be restarting (not enough time has passed for timeout)
            test_db.refresh(job)
            assert job.status == "restarting"
        finally:
            object.__setattr__(settings, "image_sync_enabled", True)

    @patch("app.routers.agents._check_update_completion", _sqlite_safe_check_update_completion)
    def test_timeout_marks_failed(self, test_client: TestClient, test_db: Session):
        """Re-registration after timeout with wrong version marks job failed."""
        object.__setattr__(settings, "image_sync_enabled", False)
        try:
            host = models.Host(
                id="timeout-agent-1",
                name="Timeout Agent",
                address="10.0.0.13:8080",
                status="online",
                capabilities=json.dumps({}),
                version="0.3.5",
                deployment_mode="systemd",
                resource_usage="{}",
                last_heartbeat=datetime.now(timezone.utc),
            )
            test_db.add(host)
            test_db.commit()

            # Job started > 10 minutes ago
            job = models.AgentUpdateJob(
                id="timeout-job-1",
                host_id="timeout-agent-1",
                from_version="0.3.5",
                to_version="0.3.7",
                status="restarting",
                started_at=datetime.now(timezone.utc) - timedelta(minutes=15),
            )
            test_db.add(job)
            test_db.commit()

            # Re-register with wrong version
            response = test_client.post(
                "/agents/register",
                json={
                    "agent": {
                        "agent_id": "timeout-agent-1",
                        "name": "Timeout Agent",
                        "address": "10.0.0.13:8080",
                        "capabilities": {"providers": []},
                        "version": "0.3.5",
                        "commit": "",
                        "deployment_mode": "systemd",
                    },
                },
            )
            assert response.status_code == 200

            test_db.refresh(job)
            assert job.status == "failed"
            assert "expected version" in job.error_message.lower()
        finally:
            object.__setattr__(settings, "image_sync_enabled", True)


# ---- git_sha registration tests ----

class TestGitShaRegistration:
    """Tests for git_sha being stored on registration."""

    def test_new_registration_stores_git_sha(self, test_client: TestClient, test_db: Session):
        """New agent registration stores git_sha from commit field."""
        response = test_client.post(
            "/agents/register",
            json={
                "agent": {
                    "agent_id": "sha-reg-agent-1",
                    "name": "SHA Reg Agent",
                    "address": "10.0.0.20:8080",
                    "capabilities": {"providers": ["docker"]},
                    "version": "0.3.7",
                    "commit": "deadbeef12345678",
                    "deployment_mode": "systemd",
                },
            },
        )

        assert response.status_code == 200
        host = test_db.get(models.Host, "sha-reg-agent-1")
        assert host is not None
        assert host.git_sha == "deadbeef12345678"
        assert host.deployment_mode == "systemd"

    def test_reregistration_updates_git_sha(self, test_client: TestClient, test_db: Session, online_host):
        """Re-registration updates git_sha."""
        old_sha = online_host.git_sha

        response = test_client.post(
            "/agents/register",
            json={
                "agent": {
                    "agent_id": online_host.id,
                    "name": online_host.name,
                    "address": "10.0.0.1:8080",
                    "capabilities": {"providers": ["docker"]},
                    "version": "0.3.7",
                    "commit": "newsha123456789a",
                    "deployment_mode": "systemd",
                },
            },
        )

        assert response.status_code == 200
        test_db.refresh(online_host)
        assert online_host.git_sha == "newsha123456789a"
        assert online_host.git_sha != old_sha

    def test_git_sha_in_detailed_response(self, test_client: TestClient, online_host):
        """git_sha appears in detailed agent listing."""
        response = test_client.get("/agents/detailed")
        assert response.status_code == 200
        data = response.json()
        agent = next(h for h in data if h["id"] == online_host.id)
        assert agent["git_sha"] == online_host.git_sha

    def test_git_sha_in_host_out(self, test_client: TestClient, online_host):
        """git_sha appears in basic agent response."""
        response = test_client.get(f"/agents/{online_host.id}")
        assert response.status_code == 200
        data = response.json()
        assert "git_sha" in data
