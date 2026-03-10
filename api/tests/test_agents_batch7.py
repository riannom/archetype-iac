"""Batch 7: Agents router gap-fill tests.

Covers all 18 endpoints in api/app/routers/agents.py:
- register_agent, heartbeat, list_agents, list_agents_detailed, get_agent
- get_deregister_info, unregister_agent, update_sync_strategy
- list_agent_images, reconcile_agent_images_endpoint
- list_agent_interfaces, list_agent_bridges
- get_latest_version, trigger_agent_update, trigger_bulk_update
- get_update_status, list_update_jobs, rebuild_docker_agent
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models
from app.state import HostStatus, JobStatus, LabState
from tests.factories import make_host


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------



def _make_update_job(
    db: Session,
    host_id: str,
    *,
    status: str = "completed",
    from_version: str = "0.3.0",
    to_version: str = "0.4.0",
    created_at: datetime | None = None,
) -> models.AgentUpdateJob:
    job = models.AgentUpdateJob(
        id=str(uuid4()),
        host_id=host_id,
        from_version=from_version,
        to_version=to_version,
        status=status,
        created_at=created_at or datetime.now(timezone.utc),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


# ---------------------------------------------------------------------------
# POST /agents/register
# ---------------------------------------------------------------------------


class TestRegisterAgent:
    """Tests for POST /agents/register."""

    def test_register_new_agent(
        self, test_client: TestClient, agent_auth_headers: dict, test_db: Session,
    ):
        agent_id = str(uuid4())[:8]
        resp = test_client.post(
            "/agents/register",
            json={
                "agent": {
                    "agent_id": agent_id,
                    "name": "new-agent",
                    "address": "10.0.0.99:8001",
                    "capabilities": {"providers": ["docker"], "features": []},
                    "version": "0.4.0",
                    "deployment_mode": "systemd",
                },
            },
            headers=agent_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["assigned_id"] == agent_id
        assert "registered" in data["message"].lower()

    def test_register_existing_agent_updates(
        self, test_client: TestClient, agent_auth_headers: dict, test_db: Session,
    ):
        host = make_host(test_db, name="existing", version="0.3.0")
        resp = test_client.post(
            "/agents/register",
            json={
                "agent": {
                    "agent_id": host.id,
                    "name": "existing-updated",
                    "address": host.address,
                    "capabilities": {"providers": ["docker"], "features": []},
                    "version": "0.4.0",
                    "deployment_mode": "systemd",
                },
            },
            headers=agent_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "re-registered" in data["message"].lower()

    def test_register_requires_agent_auth(self, test_client: TestClient):
        resp = test_client.post(
            "/agents/register",
            json={
                "agent": {
                    "agent_id": "x",
                    "name": "x",
                    "address": "x",
                    "capabilities": {},
                },
            },
        )
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# POST /agents/{agent_id}/heartbeat
# ---------------------------------------------------------------------------


class TestHeartbeat:
    """Tests for POST /agents/{agent_id}/heartbeat."""

    def test_heartbeat_success(
        self, test_client: TestClient, agent_auth_headers: dict, test_db: Session,
    ):
        host = make_host(test_db)
        resp = test_client.post(
            f"/agents/{host.id}/heartbeat",
            json={
                "agent_id": host.id,
                "status": "online",
                "active_jobs": 0,
                "resource_usage": {"cpu_percent": 25},
            },
            headers=agent_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["acknowledged"] is True

    def test_heartbeat_updates_data_plane_ip(
        self, test_client: TestClient, agent_auth_headers: dict, test_db: Session,
    ):
        host = make_host(test_db)
        test_client.post(
            f"/agents/{host.id}/heartbeat",
            json={
                "agent_id": host.id,
                "status": "online",
                "resource_usage": {},
                "data_plane_ip": "192.168.1.100",
            },
            headers=agent_auth_headers,
        )
        test_db.refresh(host)
        assert host.data_plane_address == "192.168.1.100"

    def test_heartbeat_agent_not_found(
        self, test_client: TestClient, agent_auth_headers: dict,
    ):
        resp = test_client.post(
            "/agents/nonexistent/heartbeat",
            json={"agent_id": "nonexistent", "status": "online", "resource_usage": {}},
            headers=agent_auth_headers,
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /agents
# ---------------------------------------------------------------------------


class TestListAgents:
    """Tests for GET /agents."""

    def test_list_agents_empty(
        self, test_client: TestClient, auth_headers: dict,
    ):
        resp = test_client.get("/agents", headers=auth_headers)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_list_agents_returns_hosts(
        self, test_client: TestClient, auth_headers: dict, test_db: Session,
    ):
        make_host(test_db, name="alpha")
        make_host(test_db, name="bravo", address="10.0.0.2:8001")
        resp = test_client.get("/agents", headers=auth_headers)
        assert resp.status_code == 200
        names = [a["name"] for a in resp.json()]
        assert "alpha" in names
        assert "bravo" in names

    def test_list_agents_requires_auth(self, test_client: TestClient):
        resp = test_client.get("/agents")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /agents/detailed
# ---------------------------------------------------------------------------


class TestListAgentsDetailed:
    """Tests for GET /agents/detailed."""

    def test_detailed_returns_role_agent(
        self, test_client: TestClient, auth_headers: dict, test_db: Session,
    ):
        make_host(test_db, capabilities={"providers": ["docker"], "features": []})
        resp = test_client.get("/agents/detailed", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        agent = data[0]
        assert agent["role"] == "agent"
        assert "resource_usage" in agent

    def test_detailed_controller_role(
        self, test_client: TestClient, auth_headers: dict, test_db: Session,
    ):
        make_host(
            test_db, name="ctrl", address="10.0.0.5:8001",
            capabilities={"providers": [], "features": []},
        )
        resp = test_client.get("/agents/detailed", headers=auth_headers)
        data = resp.json()
        ctrl = [a for a in data if a["name"] == "ctrl"]
        assert len(ctrl) == 1
        assert ctrl[0]["role"] == "controller"

    def test_detailed_agent_controller_role(
        self, test_client: TestClient, auth_headers: dict, test_db: Session,
    ):
        make_host(
            test_db, name="local", address="10.0.0.6:8001",
            capabilities={"providers": ["docker"], "features": []},
            is_local=True,
        )
        resp = test_client.get("/agents/detailed", headers=auth_headers)
        data = resp.json()
        local = [a for a in data if a["name"] == "local"]
        assert len(local) == 1
        assert local[0]["role"] == "agent+controller"


# ---------------------------------------------------------------------------
# GET /agents/{agent_id}
# ---------------------------------------------------------------------------


class TestGetAgent:
    """Tests for GET /agents/{agent_id}."""

    def test_get_agent_success(
        self, test_client: TestClient, auth_headers: dict, test_db: Session,
    ):
        host = make_host(test_db, name="lookup-me")
        resp = test_client.get(f"/agents/{host.id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["name"] == "lookup-me"

    def test_get_agent_not_found(
        self, test_client: TestClient, auth_headers: dict,
    ):
        resp = test_client.get("/agents/nonexistent", headers=auth_headers)
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /agents/{agent_id}/deregister-info
# ---------------------------------------------------------------------------


class TestDeregisterInfo:
    """Tests for GET /agents/{agent_id}/deregister-info."""

    def test_deregister_info_empty_agent(
        self, test_client: TestClient, admin_auth_headers: dict, test_db: Session,
    ):
        host = make_host(test_db)
        resp = test_client.get(
            f"/agents/{host.id}/deregister-info", headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["agent_id"] == host.id
        assert data["labs_assigned"] == 0
        assert data["node_placements"] == 0
        assert data["vxlan_tunnels"] == 0

    def test_deregister_info_with_resources(
        self, test_client: TestClient, admin_auth_headers: dict, test_db: Session,
        test_user: models.User,
    ):
        host = make_host(test_db)
        lab = models.Lab(
            name="test-lab", owner_id=test_user.id, provider="docker",
            agent_id=host.id, state=LabState.RUNNING,
        )
        test_db.add(lab)
        test_db.commit()

        resp = test_client.get(
            f"/agents/{host.id}/deregister-info", headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["labs_assigned"] == 1
        assert len(data["running_labs"]) == 1

    def test_deregister_info_not_found(
        self, test_client: TestClient, admin_auth_headers: dict,
    ):
        resp = test_client.get(
            "/agents/nonexistent/deregister-info", headers=admin_auth_headers,
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /agents/{agent_id}
# ---------------------------------------------------------------------------


class TestUnregisterAgent:
    """Tests for DELETE /agents/{agent_id}."""

    def test_unregister_agent_success(
        self, test_client: TestClient, admin_auth_headers: dict, test_db: Session,
    ):
        host = make_host(test_db, name="to-delete")
        resp = test_client.delete(
            f"/agents/{host.id}", headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "deleted"
        assert data["agent_name"] == "to-delete"
        assert "cleanup" in data

    def test_unregister_agent_cleans_lab_refs(
        self, test_client: TestClient, admin_auth_headers: dict, test_db: Session,
        test_user: models.User,
    ):
        host = make_host(test_db)
        lab = models.Lab(
            name="orphan-lab", owner_id=test_user.id, provider="docker",
            agent_id=host.id,
        )
        test_db.add(lab)
        test_db.commit()

        resp = test_client.delete(
            f"/agents/{host.id}", headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["cleanup"]["labs_unassigned"] >= 1

        test_db.refresh(lab)
        assert lab.agent_id is None

    def test_unregister_not_found(
        self, test_client: TestClient, admin_auth_headers: dict,
    ):
        resp = test_client.delete(
            "/agents/nonexistent", headers=admin_auth_headers,
        )
        assert resp.status_code == 404

    def test_unregister_requires_admin(
        self, test_client: TestClient, auth_headers: dict, test_db: Session,
    ):
        host = make_host(test_db)
        resp = test_client.delete(
            f"/agents/{host.id}", headers=auth_headers,
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# PUT /agents/{agent_id}/sync-strategy
# ---------------------------------------------------------------------------


class TestUpdateSyncStrategy:
    """Tests for PUT /agents/{agent_id}/sync-strategy."""

    @pytest.mark.parametrize("strategy", ["push", "pull", "on_demand", "disabled"])
    def test_valid_strategies(
        self, test_client: TestClient, admin_auth_headers: dict, test_db: Session,
        strategy: str,
    ):
        host = make_host(test_db)
        resp = test_client.put(
            f"/agents/{host.id}/sync-strategy",
            json={"strategy": strategy},
            headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["strategy"] == strategy

    def test_invalid_strategy(
        self, test_client: TestClient, admin_auth_headers: dict, test_db: Session,
    ):
        host = make_host(test_db)
        resp = test_client.put(
            f"/agents/{host.id}/sync-strategy",
            json={"strategy": "invalid"},
            headers=admin_auth_headers,
        )
        assert resp.status_code == 400

    def test_sync_strategy_not_found(
        self, test_client: TestClient, admin_auth_headers: dict,
    ):
        resp = test_client.put(
            "/agents/nonexistent/sync-strategy",
            json={"strategy": "push"},
            headers=admin_auth_headers,
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /agents/{agent_id}/images
# ---------------------------------------------------------------------------


class TestListAgentImages:
    """Tests for GET /agents/{agent_id}/images."""

    def test_list_images_empty(
        self, test_client: TestClient, auth_headers: dict, test_db: Session,
    ):
        host = make_host(test_db)
        resp = test_client.get(
            f"/agents/{host.id}/images", headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["agent_id"] == host.id
        assert data["images"] == []

    def test_list_images_with_records(
        self, test_client: TestClient, auth_headers: dict, test_db: Session,
    ):
        host = make_host(test_db)
        ih = models.ImageHost(
            image_id="docker:ceos:4.28", host_id=host.id,
            reference="ceos:4.28.0F", status="synced",
        )
        test_db.add(ih)
        test_db.commit()

        resp = test_client.get(
            f"/agents/{host.id}/images", headers=auth_headers,
        )
        assert resp.status_code == 200
        images = resp.json()["images"]
        assert len(images) == 1
        assert images[0]["status"] == "synced"

    def test_list_images_not_found(
        self, test_client: TestClient, auth_headers: dict,
    ):
        resp = test_client.get("/agents/nonexistent/images", headers=auth_headers)
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /agents/{agent_id}/images/reconcile
# ---------------------------------------------------------------------------


class TestReconcileAgentImages:
    """Tests for POST /agents/{agent_id}/images/reconcile."""

    def test_reconcile_not_found(self, test_client: TestClient, test_db: Session):
        resp = test_client.post("/agents/nonexistent/images/reconcile")
        assert resp.status_code == 404

    def test_reconcile_offline_agent(self, test_client: TestClient, test_db: Session):
        host = make_host(test_db, status=HostStatus.OFFLINE)
        resp = test_client.post(f"/agents/{host.id}/images/reconcile")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /agents/{agent_id}/interfaces
# ---------------------------------------------------------------------------


class TestListAgentInterfaces:
    """Tests for GET /agents/{agent_id}/interfaces (proxy)."""

    def test_interfaces_agent_not_found(
        self, test_client: TestClient, auth_headers: dict,
    ):
        resp = test_client.get("/agents/nonexistent/interfaces", headers=auth_headers)
        assert resp.status_code == 404

    def test_interfaces_offline_agent(
        self, test_client: TestClient, auth_headers: dict, test_db: Session,
    ):
        host = make_host(test_db, status=HostStatus.OFFLINE)
        resp = test_client.get(
            f"/agents/{host.id}/interfaces", headers=auth_headers,
        )
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /agents/{agent_id}/bridges
# ---------------------------------------------------------------------------


class TestListAgentBridges:
    """Tests for GET /agents/{agent_id}/bridges (proxy)."""

    def test_bridges_agent_not_found(
        self, test_client: TestClient, auth_headers: dict,
    ):
        resp = test_client.get("/agents/nonexistent/bridges", headers=auth_headers)
        assert resp.status_code == 404

    def test_bridges_offline_agent(
        self, test_client: TestClient, auth_headers: dict, test_db: Session,
    ):
        host = make_host(test_db, status=HostStatus.OFFLINE)
        resp = test_client.get(
            f"/agents/{host.id}/bridges", headers=auth_headers,
        )
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /agents/updates/latest
# ---------------------------------------------------------------------------


class TestGetLatestVersion:
    """Tests for GET /agents/updates/latest."""

    @patch("app.routers.agents.get_latest_agent_version", return_value="0.5.0")
    def test_get_latest_version(
        self, mock_ver, test_client: TestClient, auth_headers: dict,
    ):
        resp = test_client.get("/agents/updates/latest", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["version"] == "0.5.0"

    def test_latest_version_requires_auth(self, test_client: TestClient):
        resp = test_client.get("/agents/updates/latest")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /agents/{agent_id}/update
# ---------------------------------------------------------------------------


class TestTriggerAgentUpdate:
    """Tests for POST /agents/{agent_id}/update."""

    def test_update_not_found(
        self, test_client: TestClient, admin_auth_headers: dict,
    ):
        resp = test_client.post(
            "/agents/nonexistent/update",
            json={"target_version": "0.5.0"},
            headers=admin_auth_headers,
        )
        assert resp.status_code == 404

    def test_update_offline_agent(
        self, test_client: TestClient, admin_auth_headers: dict, test_db: Session,
    ):
        host = make_host(test_db, status=HostStatus.OFFLINE)
        resp = test_client.post(
            f"/agents/{host.id}/update",
            json={"target_version": "0.5.0"},
            headers=admin_auth_headers,
        )
        assert resp.status_code == 503

    def test_update_docker_agent_rejected(
        self, test_client: TestClient, admin_auth_headers: dict, test_db: Session,
    ):
        host = make_host(test_db, deployment_mode="docker")
        resp = test_client.post(
            f"/agents/{host.id}/update",
            json={"target_version": "0.5.0"},
            headers=admin_auth_headers,
        )
        assert resp.status_code == 400
        assert "rebuild" in resp.json()["detail"].lower()

    def test_update_already_at_version(
        self, test_client: TestClient, admin_auth_headers: dict, test_db: Session,
    ):
        host = make_host(test_db, version="0.5.0")
        resp = test_client.post(
            f"/agents/{host.id}/update",
            json={"target_version": "0.5.0"},
            headers=admin_auth_headers,
        )
        assert resp.status_code == 400
        assert "already" in resp.json()["detail"].lower()

    def test_update_concurrent_conflict(
        self, test_client: TestClient, admin_auth_headers: dict, test_db: Session,
    ):
        host = make_host(test_db, version="0.3.0")
        _make_update_job(
            test_db, host.id, status="downloading",
            created_at=datetime.now(timezone.utc),
        )
        resp = test_client.post(
            f"/agents/{host.id}/update",
            json={"target_version": "0.5.0"},
            headers=admin_auth_headers,
        )
        assert resp.status_code == 409
        assert "already in progress" in resp.json()["detail"].lower()

    def test_update_auto_expires_stale_job(
        self, test_client: TestClient, admin_auth_headers: dict, test_db: Session,
    ):
        host = make_host(test_db, version="0.3.0")
        stale_job = _make_update_job(
            test_db, host.id, status="downloading",
            created_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
        # Patch the HTTP call to avoid real network
        with patch("app.routers.agents.httpx") as mock_httpx:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"accepted": True}
            mock_resp.raise_for_status = MagicMock()
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_httpx.AsyncClient.return_value = mock_client

            with patch("app.routers.agents.get_commit", return_value="abc1234"):
                with patch("app.routers.agents._get_agent_auth_headers", return_value={}):
                    test_client.post(
                        f"/agents/{host.id}/update",
                        json={"target_version": "0.5.0"},
                        headers=admin_auth_headers,
                    )

        # Stale job should have been expired, new job should be created
        test_db.refresh(stale_job)
        assert stale_job.status == JobStatus.FAILED


# ---------------------------------------------------------------------------
# GET /agents/{agent_id}/update-status
# ---------------------------------------------------------------------------


class TestGetUpdateStatus:
    """Tests for GET /agents/{agent_id}/update-status."""

    def test_update_status_with_job(
        self, test_client: TestClient, auth_headers: dict, test_db: Session,
    ):
        host = make_host(test_db)
        job = _make_update_job(test_db, host.id)
        resp = test_client.get(
            f"/agents/{host.id}/update-status", headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["job_id"] == job.id
        assert data["status"] == "completed"

    def test_update_status_no_jobs(
        self, test_client: TestClient, auth_headers: dict, test_db: Session,
    ):
        host = make_host(test_db)
        resp = test_client.get(
            f"/agents/{host.id}/update-status", headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json() is None

    def test_update_status_not_found(
        self, test_client: TestClient, auth_headers: dict,
    ):
        resp = test_client.get("/agents/nonexistent/update-status", headers=auth_headers)
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /agents/{agent_id}/update-jobs
# ---------------------------------------------------------------------------


class TestListUpdateJobs:
    """Tests for GET /agents/{agent_id}/update-jobs."""

    def test_list_update_jobs(
        self, test_client: TestClient, auth_headers: dict, test_db: Session,
    ):
        host = make_host(test_db)
        _make_update_job(test_db, host.id, status="completed")
        _make_update_job(
            test_db, host.id, status="failed",
            created_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        resp = test_client.get(
            f"/agents/{host.id}/update-jobs", headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    def test_list_update_jobs_respects_limit(
        self, test_client: TestClient, auth_headers: dict, test_db: Session,
    ):
        host = make_host(test_db)
        for i in range(5):
            _make_update_job(
                test_db, host.id,
                created_at=datetime.now(timezone.utc) - timedelta(hours=i),
            )
        resp = test_client.get(
            f"/agents/{host.id}/update-jobs?limit=2", headers=auth_headers,
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_list_update_jobs_not_found(
        self, test_client: TestClient, auth_headers: dict,
    ):
        resp = test_client.get("/agents/nonexistent/update-jobs", headers=auth_headers)
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /agents/updates/bulk
# ---------------------------------------------------------------------------


class TestBulkUpdate:
    """Tests for POST /agents/updates/bulk."""

    def test_bulk_update_validation_failures(
        self, test_client: TestClient, admin_auth_headers: dict, test_db: Session,
    ):
        offline = make_host(test_db, name="off", address="10.0.0.10:8001", status=HostStatus.OFFLINE)
        docker_host = make_host(test_db, name="dock", address="10.0.0.11:8001", deployment_mode="docker")
        same_ver = make_host(test_db, name="same", address="10.0.0.12:8001", version="0.5.0")

        resp = test_client.post(
            "/agents/updates/bulk",
            json={
                "agent_ids": ["nonexistent", offline.id, docker_host.id, same_ver.id],
                "target_version": "0.5.0",
            },
            headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success_count"] == 0
        assert data["failure_count"] == 4

    def test_bulk_update_requires_admin(
        self, test_client: TestClient, auth_headers: dict,
    ):
        resp = test_client.post(
            "/agents/updates/bulk",
            json={"agent_ids": [], "target_version": "0.5.0"},
            headers=auth_headers,
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /agents/{agent_id}/rebuild
# ---------------------------------------------------------------------------


class TestRebuildDockerAgent:
    """Tests for POST /agents/{agent_id}/rebuild."""

    def test_rebuild_not_found(
        self, test_client: TestClient, admin_auth_headers: dict,
    ):
        resp = test_client.post(
            "/agents/nonexistent/rebuild", headers=admin_auth_headers,
        )
        assert resp.status_code == 404

    def test_rebuild_non_docker_rejected(
        self, test_client: TestClient, admin_auth_headers: dict, test_db: Session,
    ):
        host = make_host(test_db, deployment_mode="systemd")
        resp = test_client.post(
            f"/agents/{host.id}/rebuild", headers=admin_auth_headers,
        )
        assert resp.status_code == 400
        assert "not Docker-deployed" in resp.json()["detail"]

    def test_rebuild_remote_docker_rejected(
        self, test_client: TestClient, admin_auth_headers: dict, test_db: Session,
    ):
        host = make_host(test_db, deployment_mode="docker", is_local=False)
        resp = test_client.post(
            f"/agents/{host.id}/rebuild", headers=admin_auth_headers,
        )
        assert resp.status_code == 400
        assert "local" in resp.json()["detail"].lower()

    def test_rebuild_requires_admin(
        self, test_client: TestClient, auth_headers: dict, test_db: Session,
    ):
        host = make_host(test_db, deployment_mode="docker", is_local=True)
        resp = test_client.post(
            f"/agents/{host.id}/rebuild", headers=auth_headers,
        )
        assert resp.status_code == 403