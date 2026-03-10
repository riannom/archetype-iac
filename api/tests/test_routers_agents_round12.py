"""Tests for under-tested paths in routers/agents.py — round 12.

Covers:
- Docker agent rebuild endpoint (deployment_mode checks, non-local, subprocess)
- Agent introspection (deregister-info, detailed enrichment)
- Link recovery on agent restart (_mark_links_for_recovery_sync)
- Bridge proxy endpoint
- Update completion detection (_check_update_completion)
- Bulk update edge cases
- Docker mode rejection on update
- Concurrent update expiry
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models


# ---------------------------------------------------------------------------
# Rebuild endpoint
# ---------------------------------------------------------------------------


class TestRebuildDockerAgent:
    """Tests for POST /agents/{agent_id}/rebuild."""

    def test_rebuild_non_docker_agent_rejected(
        self, test_client: TestClient, test_db: Session, admin_auth_headers: dict
    ):
        """Rebuild is rejected for non-docker deployment modes."""
        host = models.Host(
            id="systemd-agent",
            name="Systemd Agent",
            address="10.0.0.1:8001",
            status="online",
            capabilities=json.dumps({"providers": ["docker"]}),
            version="1.0.0",
            deployment_mode="systemd",
            is_local=True,
        )
        test_db.add(host)
        test_db.commit()

        resp = test_client.post("/agents/systemd-agent/rebuild", headers=admin_auth_headers)
        assert resp.status_code == 400
        assert "not Docker-deployed" in resp.json()["detail"]

    def test_rebuild_remote_docker_agent_rejected(
        self, test_client: TestClient, test_db: Session, admin_auth_headers: dict
    ):
        """Rebuild is rejected for remote (non-local) Docker agents."""
        host = models.Host(
            id="remote-docker",
            name="Remote Docker",
            address="10.0.0.2:8001",
            status="online",
            capabilities=json.dumps({"providers": ["docker"]}),
            version="1.0.0",
            deployment_mode="docker",
            is_local=False,
        )
        test_db.add(host)
        test_db.commit()

        resp = test_client.post("/agents/remote-docker/rebuild", headers=admin_auth_headers)
        assert resp.status_code == 400
        assert "local Docker agents" in resp.json()["detail"]

    def test_rebuild_agent_not_found(
        self, test_client: TestClient, admin_auth_headers: dict
    ):
        """Rebuild returns 404 for unknown agent."""
        resp = test_client.post("/agents/ghost/rebuild", headers=admin_auth_headers)
        assert resp.status_code == 404

    def test_rebuild_compose_file_missing(
        self, test_client: TestClient, test_db: Session, admin_auth_headers: dict
    ):
        """Rebuild returns failure when docker-compose file not found."""
        host = models.Host(
            id="local-docker",
            name="Local Docker",
            address="localhost:8001",
            status="online",
            capabilities=json.dumps({"providers": ["docker"]}),
            version="1.0.0",
            deployment_mode="docker",
            is_local=True,
        )
        test_db.add(host)
        test_db.commit()

        # All compose file paths will fail to exist in test env
        resp = test_client.post("/agents/local-docker/rebuild", headers=admin_auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "not found" in data["message"]


# ---------------------------------------------------------------------------
# Deregister info (introspection)
# ---------------------------------------------------------------------------


class TestDeregisterInfo:
    """Tests for GET /agents/{agent_id}/deregister-info."""

    def test_deregister_info_empty(
        self, test_client: TestClient, test_db: Session,
        sample_host: models.Host, admin_auth_headers: dict,
    ):
        """Returns zeroed counts when agent has no associated resources."""
        resp = test_client.get(
            f"/agents/{sample_host.id}/deregister-info",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["agent_id"] == sample_host.id
        assert data["agent_name"] == sample_host.name
        assert data["labs_assigned"] == 0
        assert data["running_labs"] == []
        assert data["node_placements"] == 0
        assert data["vxlan_tunnels"] == 0
        assert data["cross_host_links"] == 0
        assert data["nodes_assigned"] == 0

    def test_deregister_info_with_resources(
        self, test_client: TestClient, test_db: Session,
        sample_host: models.Host, test_user: models.User,
        admin_auth_headers: dict,
    ):
        """Returns correct counts when agent has associated resources."""
        lab = models.Lab(
            name="Agent Lab",
            owner_id=test_user.id,
            provider="docker",
            state="running",
            workspace_path="/tmp/lab",
            agent_id=sample_host.id,
        )
        test_db.add(lab)
        test_db.flush()

        node = models.Node(
            id="nd-1",
            lab_id=lab.id,
            gui_id="n1",
            display_name="R1",
            container_name="archetype-test-r1",
            device="linux",
            host_id=sample_host.id,
        )
        test_db.add(node)

        placement = models.NodePlacement(
            lab_id=lab.id,
            node_name="R1",
            host_id=sample_host.id,
        )
        test_db.add(placement)
        test_db.commit()

        resp = test_client.get(
            f"/agents/{sample_host.id}/deregister-info",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["labs_assigned"] == 1
        assert len(data["running_labs"]) == 1
        assert data["running_labs"][0]["name"] == "Agent Lab"
        assert data["node_placements"] == 1
        assert data["nodes_assigned"] == 1

    def test_deregister_info_not_found(
        self, test_client: TestClient, admin_auth_headers: dict,
    ):
        """Returns 404 for unknown agent."""
        resp = test_client.get(
            "/agents/nonexistent/deregister-info",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Link recovery on agent restart
# ---------------------------------------------------------------------------


class TestLinkRecoveryOnRestart:
    """Tests for cross-host link recovery when agent re-registers after restart."""

    def test_restart_marks_cross_host_links_error(
        self, test_client: TestClient, test_db: Session,
        agent_auth_headers: dict, monkeypatch,
    ):
        """Agent restart marks cross-host links as error for recovery."""
        monkeypatch.setattr("app.config.settings.image_sync_enabled", False)
        # Prevent background convergence task from racing with second register call
        monkeypatch.setattr("app.routers.agents.asyncio.create_task", lambda coro: coro.close())

        old_ts = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=5)).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
        # Register agent initially
        payload = {
            "agent": {
                "agent_id": "link-agent",
                "name": "Link Agent",
                "address": "localhost:9001",
                "capabilities": {"providers": ["docker"]},
                "version": "1.0.0",
                "started_at": old_ts,
                "is_local": False,
            }
        }
        resp = test_client.post("/agents/register", json=payload, headers=agent_auth_headers)
        assert resp.status_code == 200

        # Create a cross-host link referencing this agent
        lab = models.Lab(
            name="XH Lab",
            owner_id="user",
            provider="docker",
            state="running",
            workspace_path="/tmp/xh",
        )
        test_db.add(lab)
        test_db.flush()

        link = models.LinkState(
            lab_id=lab.id,
            link_name="A:eth1-B:eth1",
            source_node="A",
            source_interface="eth1",
            target_node="B",
            target_interface="eth1",
            desired_state="up",
            actual_state="up",
            is_cross_host=True,
            source_host_id="link-agent",
            target_host_id="other-agent",
            source_vxlan_attached=True,
            target_vxlan_attached=True,
        )
        test_db.add(link)
        test_db.commit()

        # Re-register with new started_at (simulate restart)
        new_ts = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S")
        payload["agent"]["started_at"] = new_ts
        resp = test_client.post("/agents/register", json=payload, headers=agent_auth_headers)
        assert resp.status_code == 200

        # Verify link was marked for recovery
        test_db.refresh(link)
        assert link.actual_state == "error"
        assert "recovery" in link.error_message.lower()
        assert link.source_vxlan_attached is False
        # target side should remain unchanged (different agent)
        assert link.target_vxlan_attached is True

    def test_restart_no_links_to_recover(
        self, test_client: TestClient, test_db: Session,
        agent_auth_headers: dict, monkeypatch,
    ):
        """Agent restart with no cross-host links completes without error."""
        monkeypatch.setattr("app.config.settings.image_sync_enabled", False)
        # Prevent background convergence task from racing with second register call
        monkeypatch.setattr("app.routers.agents.asyncio.create_task", lambda coro: coro.close())

        old_ts = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=5)).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
        payload = {
            "agent": {
                "agent_id": "no-link-agent",
                "name": "No Link Agent",
                "address": "localhost:9002",
                "capabilities": {"providers": ["docker"]},
                "version": "1.0.0",
                "started_at": old_ts,
                "is_local": False,
            }
        }
        resp = test_client.post("/agents/register", json=payload, headers=agent_auth_headers)
        assert resp.status_code == 200

        new_ts = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S")
        payload["agent"]["started_at"] = new_ts
        resp = test_client.post("/agents/register", json=payload, headers=agent_auth_headers)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Bridge proxy endpoint
# ---------------------------------------------------------------------------


class TestBridgeProxy:
    """Tests for GET /agents/{agent_id}/bridges."""

    def test_bridges_offline_agent(
        self, test_client: TestClient, test_db: Session, auth_headers: dict,
    ):
        """Bridges proxy returns 503 for offline agent."""
        host = models.Host(
            id="offline-br",
            name="Offline BR",
            address="localhost:9999",
            status="offline",
            capabilities="{}",
            version="1.0.0",
        )
        test_db.add(host)
        test_db.commit()

        resp = test_client.get("/agents/offline-br/bridges", headers=auth_headers)
        assert resp.status_code == 503

    def test_bridges_not_found(
        self, test_client: TestClient, auth_headers: dict,
    ):
        """Bridges proxy returns 404 for unknown agent."""
        resp = test_client.get("/agents/nonexistent/bridges", headers=auth_headers)
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Update completion detection
# ---------------------------------------------------------------------------


class TestUpdateCompletionDetection:
    """Tests for _check_update_completion logic triggered during re-registration."""

    def test_reregister_completes_update_job_by_version(
        self, test_client: TestClient, test_db: Session,
        agent_auth_headers: dict, monkeypatch,
    ):
        """Re-registration with matching version completes update job."""
        monkeypatch.setattr("app.config.settings.image_sync_enabled", False)
        # Prevent background convergence task from racing with test session
        monkeypatch.setattr("app.routers.agents.asyncio.create_task", lambda coro: coro.close())

        ts = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S")
        payload = {
            "agent": {
                "agent_id": "upd-agent",
                "name": "Upd Agent",
                "address": "localhost:9003",
                "capabilities": {"providers": ["docker"]},
                "version": "1.0.0",
                "started_at": ts,
                "is_local": False,
            }
        }
        test_client.post("/agents/register", json=payload, headers=agent_auth_headers)

        # Create an active update job targeting version 2.0.0
        update_job = models.AgentUpdateJob(
            id="upd-job-1",
            host_id="upd-agent",
            from_version="1.0.0",
            to_version="2.0.0",
            status="restarting",
        )
        test_db.add(update_job)
        test_db.commit()

        # Re-register with the target version
        payload["agent"]["version"] = "2.0.0"
        resp = test_client.post("/agents/register", json=payload, headers=agent_auth_headers)
        assert resp.status_code == 200

        test_db.refresh(update_job)
        assert update_job.status == "completed"
        assert update_job.progress_percent == 100

    def test_reregister_expires_stale_update_job(
        self, test_client: TestClient, test_db: Session,
        agent_auth_headers: dict, monkeypatch,
    ):
        """Re-registration with wrong version expires stale update job."""
        monkeypatch.setattr("app.config.settings.image_sync_enabled", False)
        # Prevent background convergence task from racing with test session
        monkeypatch.setattr("app.routers.agents.asyncio.create_task", lambda coro: coro.close())

        ts = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S")
        payload = {
            "agent": {
                "agent_id": "stale-upd-agent",
                "name": "Stale Agent",
                "address": "localhost:9004",
                "capabilities": {"providers": ["docker"]},
                "version": "1.0.0",
                "started_at": ts,
                "is_local": False,
            }
        }
        test_client.post("/agents/register", json=payload, headers=agent_auth_headers)

        # Create an update job that started over 5 minutes ago, targeting 3.0.0
        update_job = models.AgentUpdateJob(
            id="stale-job-1",
            host_id="stale-upd-agent",
            from_version="1.0.0",
            to_version="3.0.0",
            status="installing",
            started_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
        test_db.add(update_job)
        test_db.commit()

        # Re-register with a different version (not matching job target)
        payload["agent"]["version"] = "1.5.0"
        resp = test_client.post("/agents/register", json=payload, headers=agent_auth_headers)
        assert resp.status_code == 200

        test_db.refresh(update_job)
        assert update_job.status == "failed"
        assert "Expired" in update_job.error_message


# ---------------------------------------------------------------------------
# Docker mode rejection on update trigger
# ---------------------------------------------------------------------------


class TestUpdateDockerModeRejection:
    """Tests for POST /agents/{agent_id}/update when agent is Docker-deployed."""

    def test_update_docker_agent_rejected(
        self, test_client: TestClient, test_db: Session, admin_auth_headers: dict,
    ):
        """Update endpoint rejects Docker-deployed agents with helpful message."""
        host = models.Host(
            id="docker-upd-agent",
            name="Docker Agent",
            address="localhost:9005",
            status="online",
            capabilities=json.dumps({"providers": ["docker"]}),
            version="1.0.0",
            deployment_mode="docker",
            last_heartbeat=datetime.now(timezone.utc),
        )
        test_db.add(host)
        test_db.commit()

        with patch("app.routers.agents.get_latest_agent_version", return_value="2.0.0"):
            resp = test_client.post(
                "/agents/docker-upd-agent/update",
                headers=admin_auth_headers,
            )
        assert resp.status_code == 400
        assert "rebuild" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Concurrent update expiry
# ---------------------------------------------------------------------------


class TestConcurrentUpdateExpiry:
    """Tests for stale update job auto-expiry in trigger_agent_update."""

    def test_stale_concurrent_update_auto_expired(
        self, test_client: TestClient, test_db: Session, admin_auth_headers: dict,
    ):
        """Stale in-progress update job is auto-expired, allowing new update."""
        host = models.Host(
            id="concurrent-agent",
            name="Concurrent Agent",
            address="localhost:9006",
            status="online",
            capabilities=json.dumps({"providers": ["docker"]}),
            version="1.0.0",
            deployment_mode="systemd",
            last_heartbeat=datetime.now(timezone.utc),
        )
        test_db.add(host)
        test_db.flush()

        # Old stale update job (started 10 min ago)
        old_job = models.AgentUpdateJob(
            id="old-concurrent-job",
            host_id="concurrent-agent",
            from_version="0.9.0",
            to_version="1.0.0",
            status="downloading",
            started_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
        test_db.add(old_job)
        test_db.commit()

        # Trigger a new update — should auto-expire the stale job then attempt new
        with patch("app.routers.agents.get_latest_agent_version", return_value="2.0.0"), \
             patch("app.routers.agents.get_commit", return_value="abc1234"), \
             patch("app.routers.agents._get_agent_auth_headers", return_value={}):

            mock_response = MagicMock()
            mock_response.json.return_value = {"accepted": True}
            mock_response.raise_for_status = MagicMock()

            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)

            with patch("httpx.AsyncClient", return_value=mock_client):
                resp = test_client.post(
                    "/agents/concurrent-agent/update",
                    headers=admin_auth_headers,
                )

        # Old job should now be failed (expired)
        test_db.refresh(old_job)
        assert old_job.status == "failed"
        assert "Expired" in old_job.error_message

        # New update should have been initiated
        assert resp.status_code == 200
        assert resp.json()["status"] == "downloading"

    def test_fresh_concurrent_update_blocked(
        self, test_client: TestClient, test_db: Session, admin_auth_headers: dict,
    ):
        """Recent in-progress update blocks new update with 409."""
        host = models.Host(
            id="blocked-agent",
            name="Blocked Agent",
            address="localhost:9007",
            status="online",
            capabilities=json.dumps({"providers": ["docker"]}),
            version="1.0.0",
            deployment_mode="systemd",
            last_heartbeat=datetime.now(timezone.utc),
        )
        test_db.add(host)
        test_db.flush()

        active_job = models.AgentUpdateJob(
            id="active-job-block",
            host_id="blocked-agent",
            from_version="1.0.0",
            to_version="2.0.0",
            status="downloading",
            started_at=datetime.now(timezone.utc) - timedelta(seconds=30),
        )
        test_db.add(active_job)
        test_db.commit()

        with patch("app.routers.agents.get_latest_agent_version", return_value="2.0.0"):
            resp = test_client.post(
                "/agents/blocked-agent/update",
                headers=admin_auth_headers,
            )
        assert resp.status_code == 409
        assert "already in progress" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Unregister cleanup counts
# ---------------------------------------------------------------------------


class TestUnregisterCleanup:
    """Tests for DELETE /agents/{agent_id} cleanup counts."""

    def test_unregister_returns_cleanup_counts(
        self, test_client: TestClient, test_db: Session,
        test_user: models.User, admin_auth_headers: dict,
    ):
        """Unregister cleans up associated resources and reports counts."""
        host = models.Host(
            id="cleanup-agent",
            name="Cleanup Agent",
            address="localhost:9008",
            status="online",
            capabilities=json.dumps({"providers": ["docker"]}),
            version="1.0.0",
        )
        test_db.add(host)
        test_db.flush()

        lab = models.Lab(
            name="Cleanup Lab",
            owner_id=test_user.id,
            provider="docker",
            state="stopped",
            workspace_path="/tmp/cleanup",
            agent_id="cleanup-agent",
        )
        test_db.add(lab)
        test_db.flush()

        node = models.Node(
            id="cleanup-nd",
            lab_id=lab.id,
            gui_id="n1",
            display_name="R1",
            container_name="archetype-cleanup-r1",
            device="linux",
            host_id="cleanup-agent",
        )
        test_db.add(node)

        placement = models.NodePlacement(
            lab_id=lab.id,
            node_name="R1",
            host_id="cleanup-agent",
        )
        test_db.add(placement)
        test_db.commit()

        resp = test_client.delete("/agents/cleanup-agent", headers=admin_auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "deleted"
        assert data["agent_name"] == "Cleanup Agent"
        assert data["cleanup"]["labs_unassigned"] >= 1
        assert data["cleanup"]["nodes_unassigned"] >= 1
        assert data["cleanup"]["node_placements_deleted"] >= 1

        # Verify host is gone
        assert test_db.get(models.Host, "cleanup-agent") is None
        # Verify lab still exists but agent_id is NULL
        test_db.refresh(lab)
        assert lab.agent_id is None
