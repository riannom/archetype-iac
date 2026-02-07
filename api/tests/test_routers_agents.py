"""Tests for agent management endpoints (routers/agents.py).

This module tests:
- Agent registration
- Heartbeat handling
- Agent listing and details
- Sync strategy management
- Agent updates
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models


class TestAgentRegistration:
    """Tests for agent registration endpoint."""

    def test_register_new_agent(self, test_client: TestClient, test_db: Session):
        """New agent registration creates host record."""
        response = test_client.post(
            "/agents/register",
            json={
                "agent": {
                    "agent_id": "new-agent-123",
                    "name": "Test Agent",
                    "address": "192.168.1.10:8080",
                    "capabilities": {
                        "providers": ["docker"],
                        "max_concurrent_jobs": 4,
                        "features": ["vxlan"]
                    },
                    "version": "1.0.0"
                }
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["assigned_id"] == "new-agent-123"

        # Verify in database
        host = test_db.get(models.Host, "new-agent-123")
        assert host is not None
        assert host.name == "Test Agent"
        assert host.status == "online"

    def test_reregister_existing_agent(self, test_client: TestClient, test_db: Session, sample_host: models.Host):
        """Re-registering existing agent updates record."""
        response = test_client.post(
            "/agents/register",
            json={
                "agent": {
                    "agent_id": sample_host.id,
                    "name": "Updated Agent Name",
                    "address": sample_host.address,
                    "capabilities": {
                        "providers": ["docker", "libvirt"],
                    },
                    "version": "2.0.0"
                }
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "re-registered" in data["message"].lower()

        test_db.refresh(sample_host)
        assert sample_host.name == "Updated Agent Name"
        assert sample_host.version == "2.0.0"

    def test_register_agent_with_same_name(self, test_client: TestClient, test_db: Session, sample_host: models.Host):
        """Registering with same name updates existing record (new ID)."""
        response = test_client.post(
            "/agents/register",
            json={
                "agent": {
                    "agent_id": "different-id-123",
                    "name": sample_host.name,  # Same name
                    "address": "192.168.1.99:8080",
                    "capabilities": {"providers": ["docker"]},
                    "version": "1.0.0"
                }
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        # Should return the existing agent's ID
        assert data["assigned_id"] == sample_host.id


class TestAgentHeartbeat:
    """Tests for agent heartbeat endpoint."""

    def test_heartbeat_updates_status(self, test_client: TestClient, test_db: Session, sample_host: models.Host):
        """Heartbeat updates host status and resource usage."""
        response = test_client.post(
            f"/agents/{sample_host.id}/heartbeat",
            json={
                "agent_id": sample_host.id,
                "status": "online",
                "active_jobs": 2,
                "resource_usage": {
                    "cpu_percent": 45.5,
                    "memory_percent": 60.2,
                    "disk_percent": 30.0
                }
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert data["acknowledged"] is True

        test_db.refresh(sample_host)
        assert sample_host.status == "online"
        usage = json.loads(sample_host.resource_usage)
        assert usage["cpu_percent"] == 45.5

    def test_heartbeat_unregistered_agent(self, test_client: TestClient):
        """Heartbeat from unregistered agent returns 404."""
        response = test_client.post(
            "/agents/nonexistent-agent/heartbeat",
            json={
                "agent_id": "nonexistent-agent",
                "status": "online",
            }
        )

        assert response.status_code == 404


class TestListAgents:
    """Tests for agent listing endpoints."""

    def test_list_agents_empty(self, test_client: TestClient, test_db: Session):
        """List agents returns empty list when none registered."""
        response = test_client.get("/agents")
        assert response.status_code == 200
        data = response.json()
        assert data == []

    def test_list_agents(self, test_client: TestClient, test_db: Session, sample_host: models.Host):
        """List agents returns registered agents."""
        response = test_client.get("/agents")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["id"] == sample_host.id
        assert data[0]["name"] == sample_host.name

    def test_list_agents_detailed(self, test_client: TestClient, test_db: Session, sample_host: models.Host):
        """Detailed list includes resource usage and labs."""
        response = test_client.get("/agents/detailed")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        agent = data[0]
        assert "resource_usage" in agent
        assert "labs" in agent
        assert "role" in agent

    def test_get_single_agent(self, test_client: TestClient, test_db: Session, sample_host: models.Host):
        """Get single agent by ID."""
        response = test_client.get(f"/agents/{sample_host.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == sample_host.id
        assert data["name"] == sample_host.name

    def test_get_nonexistent_agent(self, test_client: TestClient):
        """Get nonexistent agent returns 404."""
        response = test_client.get("/agents/nonexistent")
        assert response.status_code == 404


class TestUnregisterAgent:
    """Tests for agent unregistration."""

    def test_unregister_agent(self, test_client: TestClient, test_db: Session, sample_host: models.Host):
        """Unregister agent removes from database."""
        response = test_client.delete(f"/agents/{sample_host.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "deleted"

        # Verify removed
        host = test_db.get(models.Host, sample_host.id)
        assert host is None

    def test_unregister_nonexistent_agent(self, test_client: TestClient):
        """Unregister nonexistent agent returns 404."""
        response = test_client.delete("/agents/nonexistent")
        assert response.status_code == 404


class TestSyncStrategy:
    """Tests for sync strategy management."""

    def test_update_sync_strategy(self, test_client: TestClient, test_db: Session, sample_host: models.Host):
        """Update agent's sync strategy."""
        response = test_client.put(
            f"/agents/{sample_host.id}/sync-strategy",
            json={"strategy": "pull"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["strategy"] == "pull"

        test_db.refresh(sample_host)
        assert sample_host.image_sync_strategy == "pull"

    def test_update_sync_strategy_invalid(self, test_client: TestClient, test_db: Session, sample_host: models.Host):
        """Invalid sync strategy returns error."""
        response = test_client.put(
            f"/agents/{sample_host.id}/sync-strategy",
            json={"strategy": "invalid_strategy"}
        )
        assert response.status_code == 400

    def test_update_sync_strategy_all_valid(self, test_client: TestClient, test_db: Session, sample_host: models.Host):
        """All valid strategies are accepted."""
        valid_strategies = ["push", "pull", "on_demand", "disabled"]
        for strategy in valid_strategies:
            response = test_client.put(
                f"/agents/{sample_host.id}/sync-strategy",
                json={"strategy": strategy}
            )
            assert response.status_code == 200


class TestAgentImages:
    """Tests for agent image management."""

    def test_list_agent_images_empty(self, test_client: TestClient, test_db: Session, sample_host: models.Host):
        """List images returns empty when none synced."""
        response = test_client.get(f"/agents/{sample_host.id}/images")
        assert response.status_code == 200
        data = response.json()
        assert data["agent_id"] == sample_host.id
        assert data["images"] == []

    def test_list_agent_images(self, test_client: TestClient, test_db: Session, sample_host: models.Host):
        """List images returns synced images."""
        # Add image host record
        image_host = models.ImageHost(
            image_id="docker:ceos:4.28.0F",
            host_id=sample_host.id,
            reference="ceos:4.28.0F",
            status="synced",
            size_bytes=1024000,
        )
        test_db.add(image_host)
        test_db.commit()

        response = test_client.get(f"/agents/{sample_host.id}/images")
        assert response.status_code == 200
        data = response.json()
        assert len(data["images"]) == 1
        assert data["images"][0]["image_id"] == "docker:ceos:4.28.0F"


class TestAgentUpdates:
    """Tests for agent software updates."""

    def test_get_latest_version(self, test_client: TestClient):
        """Get latest agent version."""
        with patch("app.routers.agents.get_latest_agent_version", return_value="1.2.3"):
            response = test_client.get("/agents/updates/latest")
            assert response.status_code == 200
            data = response.json()
            assert data["version"] == "1.2.3"

    def test_trigger_update_offline_agent(self, test_client: TestClient, test_db: Session, sample_host: models.Host):
        """Trigger update on offline agent returns error."""
        sample_host.status = "offline"
        test_db.commit()

        response = test_client.post(f"/agents/{sample_host.id}/update")
        assert response.status_code == 503

    def test_trigger_update_already_current(self, test_client: TestClient, test_db: Session, sample_host: models.Host):
        """Trigger update when already at target version."""
        with patch("app.routers.agents.get_latest_agent_version", return_value=sample_host.version):
            response = test_client.post(f"/agents/{sample_host.id}/update")
            assert response.status_code == 400
            assert "already at version" in response.json()["detail"].lower()

    def test_get_update_status_no_jobs(self, test_client: TestClient, test_db: Session, sample_host: models.Host):
        """Get update status when no jobs exist."""
        response = test_client.get(f"/agents/{sample_host.id}/update-status")
        assert response.status_code == 200
        assert response.json() is None

    def test_list_update_jobs_empty(self, test_client: TestClient, test_db: Session, sample_host: models.Host):
        """List update jobs returns empty list."""
        response = test_client.get(f"/agents/{sample_host.id}/update-jobs")
        assert response.status_code == 200
        assert response.json() == []


class TestAgentRoleDetermination:
    """Tests for agent role determination logic."""

    def test_agent_role_local(self, test_client: TestClient, test_db: Session):
        """Local agent gets agent+controller role."""
        host = models.Host(
            id="local-agent",
            name="Local Agent",
            address="localhost:8080",
            status="online",
            capabilities=json.dumps({"providers": ["docker"]}),
            is_local=True,
        )
        test_db.add(host)
        test_db.commit()

        response = test_client.get("/agents/detailed")
        assert response.status_code == 200
        data = response.json()
        local = [a for a in data if a["id"] == "local-agent"][0]
        assert local["role"] == "agent+controller"

    def test_agent_role_remote(self, test_client: TestClient, test_db: Session):
        """Remote agent gets agent role."""
        host = models.Host(
            id="remote-agent",
            name="Remote Agent",
            address="192.168.1.100:8080",
            status="online",
            capabilities=json.dumps({"providers": ["docker"]}),
        )
        test_db.add(host)
        test_db.commit()

        response = test_client.get("/agents/detailed")
        assert response.status_code == 200
        data = response.json()
        remote = [a for a in data if a["id"] == "remote-agent"][0]
        assert remote["role"] == "agent"

    def test_agent_role_no_providers(self, test_client: TestClient, test_db: Session):
        """Agent without providers gets controller role."""
        host = models.Host(
            id="controller-only",
            name="Controller Only",
            address="192.168.1.100:8080",
            status="online",
            capabilities=json.dumps({"providers": []}),
        )
        test_db.add(host)
        test_db.commit()

        response = test_client.get("/agents/detailed")
        assert response.status_code == 200
        data = response.json()
        controller = [a for a in data if a["id"] == "controller-only"][0]
        assert controller["role"] == "controller"
