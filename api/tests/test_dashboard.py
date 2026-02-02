"""Tests for dashboard endpoints."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models


class TestDashboardMetrics:
    """Tests for /dashboard/metrics endpoint."""

    def test_metrics_empty_state(
        self,
        test_client: TestClient,
        test_db: Session,
    ):
        """Test dashboard metrics with no agents or labs."""
        response = test_client.get("/dashboard/metrics")
        assert response.status_code == 200
        data = response.json()

        # Verify structure
        assert "agents" in data
        assert "containers" in data
        assert "cpu_percent" in data
        assert "memory_percent" in data
        assert "storage" in data
        assert "labs_running" in data
        assert "labs_total" in data
        assert "is_multi_host" in data

        # Verify empty state values
        assert data["agents"]["online"] == 0
        assert data["agents"]["total"] == 0
        assert data["containers"]["running"] == 0
        assert data["labs_running"] == 0
        assert data["labs_total"] == 0
        assert data["is_multi_host"] is False

    def test_metrics_single_agent(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_host: models.Host,
    ):
        """Test dashboard metrics with a single online agent."""
        response = test_client.get("/dashboard/metrics")
        assert response.status_code == 200
        data = response.json()

        assert data["agents"]["online"] == 1
        assert data["agents"]["total"] == 1
        assert data["containers"]["running"] == 5
        assert data["cpu_percent"] == 25.5
        assert data["memory_percent"] == 45.2
        assert data["is_multi_host"] is False

        # Verify per_host data
        assert len(data["per_host"]) == 1
        host_data = data["per_host"][0]
        assert host_data["id"] == sample_host.id
        assert host_data["name"] == sample_host.name
        assert host_data["cpu_percent"] == 25.5

    def test_metrics_multi_agent_aggregation(
        self,
        test_client: TestClient,
        test_db: Session,
        multiple_hosts: list[models.Host],
    ):
        """Test dashboard metrics aggregates across multiple agents."""
        response = test_client.get("/dashboard/metrics")
        assert response.status_code == 200
        data = response.json()

        # 2 online out of 3 total (agent-3 is offline)
        assert data["agents"]["online"] == 2
        assert data["agents"]["total"] == 3

        # Containers: 3 + 2 = 5 running
        assert data["containers"]["running"] == 5

        # CPU: average of 30.0 and 20.0 = 25.0
        assert data["cpu_percent"] == 25.0

        # Memory: average of 50.0 and 40.0 = 45.0
        assert data["memory_percent"] == 45.0

        # Storage: aggregated totals
        # Used: 80 + 60 = 140 GB
        # Total: 200 + 200 = 400 GB
        assert data["storage"]["used_gb"] == 140.0
        assert data["storage"]["total_gb"] == 400.0
        assert data["storage"]["percent"] == 35.0  # 140/400 * 100

        assert data["is_multi_host"] is True

        # Per-host data should only include online agents
        assert len(data["per_host"]) == 2

    def test_metrics_labs_running_count(
        self,
        test_client: TestClient,
        test_db: Session,
    ):
        """Test that labs_running is based on actual container presence."""
        # Create a host with container details that reference a lab
        lab = models.Lab(name="Running Lab", provider="docker")
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        # Create host with containers for this lab
        host = models.Host(
            id="test-agent",
            name="Test Agent",
            address="localhost:8080",
            status="online",
            capabilities=json.dumps({"providers": ["docker"]}),
            version="1.0.0",
            resource_usage=json.dumps({
                "cpu_percent": 10.0,
                "memory_percent": 20.0,
                "containers_running": 2,
                "containers_total": 2,
                "container_details": [
                    {
                        "name": f"archetype-{lab.id}-r1",
                        "status": "running",
                        "lab_prefix": lab.id,
                        "is_system": False,
                    },
                ],
            }),
        )
        test_db.add(host)
        test_db.commit()

        response = test_client.get("/dashboard/metrics")
        assert response.status_code == 200
        data = response.json()

        assert data["labs_running"] == 1
        assert data["labs_total"] == 1

    def test_metrics_offline_agents_excluded(
        self,
        test_client: TestClient,
        test_db: Session,
    ):
        """Test that offline agents don't contribute to resource totals."""
        # Create only offline agent
        host = models.Host(
            id="offline-agent",
            name="Offline Agent",
            address="localhost:8080",
            status="offline",
            capabilities=json.dumps({"providers": ["docker"]}),
            version="1.0.0",
            resource_usage=json.dumps({
                "cpu_percent": 99.0,
                "memory_percent": 99.0,
                "containers_running": 100,
            }),
        )
        test_db.add(host)
        test_db.commit()

        response = test_client.get("/dashboard/metrics")
        assert response.status_code == 200
        data = response.json()

        # Offline agent should not contribute
        assert data["agents"]["online"] == 0
        assert data["agents"]["total"] == 1
        assert data["containers"]["running"] == 0
        assert data["cpu_percent"] == 0
        assert data["memory_percent"] == 0


class TestContainersBreakdown:
    """Tests for /dashboard/metrics/containers endpoint."""

    def test_containers_empty_state(
        self,
        test_client: TestClient,
        test_db: Session,
    ):
        """Test containers breakdown with no agents."""
        response = test_client.get("/dashboard/metrics/containers")
        assert response.status_code == 200
        data = response.json()

        assert data["by_lab"] == {}
        assert data["system_containers"] == []
        assert data["total_running"] == 0
        assert data["total_stopped"] == 0

    def test_containers_by_lab(
        self,
        test_client: TestClient,
        test_db: Session,
    ):
        """Test containers are grouped by lab."""
        # Create lab
        lab = models.Lab(name="Test Lab", provider="docker")
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        # Create host with containers for this lab
        host = models.Host(
            id="test-agent",
            name="Test Agent",
            address="localhost:8080",
            status="online",
            capabilities=json.dumps({"providers": ["docker"]}),
            version="1.0.0",
            resource_usage=json.dumps({
                "container_details": [
                    {
                        "name": f"archetype-{lab.id}-r1",
                        "status": "running",
                        "lab_prefix": lab.id,
                        "is_system": False,
                    },
                    {
                        "name": f"archetype-{lab.id}-r2",
                        "status": "running",
                        "lab_prefix": lab.id,
                        "is_system": False,
                    },
                ],
            }),
        )
        test_db.add(host)
        test_db.commit()

        response = test_client.get("/dashboard/metrics/containers")
        assert response.status_code == 200
        data = response.json()

        assert lab.id in data["by_lab"]
        assert len(data["by_lab"][lab.id]["containers"]) == 2
        assert data["by_lab"][lab.id]["name"] == "Test Lab"
        assert data["total_running"] == 2

    def test_system_containers(
        self,
        test_client: TestClient,
        test_db: Session,
    ):
        """Test system containers are reported separately."""
        host = models.Host(
            id="test-agent",
            name="Test Agent",
            address="localhost:8080",
            status="online",
            capabilities=json.dumps({"providers": ["docker"]}),
            version="1.0.0",
            resource_usage=json.dumps({
                "container_details": [
                    {
                        "name": "archetype-system-container",
                        "status": "running",
                        "lab_prefix": "",
                        "is_system": True,
                    },
                ],
            }),
        )
        test_db.add(host)
        test_db.commit()

        response = test_client.get("/dashboard/metrics/containers")
        assert response.status_code == 200
        data = response.json()

        assert len(data["system_containers"]) == 1
        assert data["system_containers"][0]["is_system"] is True

    def test_orphan_containers(
        self,
        test_client: TestClient,
        test_db: Session,
    ):
        """Test orphan containers (lab deleted) are treated as system containers."""
        host = models.Host(
            id="test-agent",
            name="Test Agent",
            address="localhost:8080",
            status="online",
            capabilities=json.dumps({"providers": ["docker"]}),
            version="1.0.0",
            resource_usage=json.dumps({
                "container_details": [
                    {
                        "name": "archetype-deleted-lab-r1",
                        "status": "running",
                        "lab_prefix": "nonexistent-lab-id",
                        "is_system": False,
                    },
                ],
            }),
        )
        test_db.add(host)
        test_db.commit()

        response = test_client.get("/dashboard/metrics/containers")
        assert response.status_code == 200
        data = response.json()

        # Orphan should be in system_containers
        assert len(data["system_containers"]) == 1
        assert data["by_lab"] == {}


class TestResourceDistribution:
    """Tests for /dashboard/metrics/resources endpoint."""

    def test_resources_empty_state(
        self,
        test_client: TestClient,
        test_db: Session,
    ):
        """Test resource distribution with no agents."""
        response = test_client.get("/dashboard/metrics/resources")
        assert response.status_code == 200
        data = response.json()

        assert "by_agent" in data
        assert "by_lab" in data
        assert data["by_agent"] == []
        assert data["by_lab"] == []

    def test_resources_by_agent(
        self,
        test_client: TestClient,
        test_db: Session,
        multiple_hosts: list[models.Host],
    ):
        """Test resource distribution by agent."""
        response = test_client.get("/dashboard/metrics/resources")
        assert response.status_code == 200
        data = response.json()

        # Only online agents should be included
        assert len(data["by_agent"]) == 2

        # Verify agent data
        agent_ids = {a["id"] for a in data["by_agent"]}
        assert "agent-1" in agent_ids
        assert "agent-2" in agent_ids
        assert "agent-3" not in agent_ids  # Offline

    def test_resources_by_lab(
        self,
        test_client: TestClient,
        test_db: Session,
    ):
        """Test resource distribution by lab."""
        # Create lab
        lab = models.Lab(name="Test Lab", provider="docker")
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        # Create host with containers for this lab
        host = models.Host(
            id="test-agent",
            name="Test Agent",
            address="localhost:8080",
            status="online",
            capabilities=json.dumps({"providers": ["docker"]}),
            version="1.0.0",
            resource_usage=json.dumps({
                "cpu_percent": 50.0,
                "memory_percent": 60.0,
                "container_details": [
                    {
                        "name": f"archetype-{lab.id}-r1",
                        "status": "running",
                        "lab_prefix": lab.id,
                        "is_system": False,
                    },
                    {
                        "name": f"archetype-{lab.id}-r2",
                        "status": "running",
                        "lab_prefix": lab.id,
                        "is_system": False,
                    },
                ],
            }),
        )
        test_db.add(host)
        test_db.commit()

        response = test_client.get("/dashboard/metrics/resources")
        assert response.status_code == 200
        data = response.json()

        # Lab should have container count (by_lab is a list of objects)
        assert len(data["by_lab"]) == 1
        lab_entry = data["by_lab"][0]
        assert lab_entry["id"] == lab.id
        assert lab_entry["container_count"] == 2
        assert lab_entry["name"] == "Test Lab"


class TestLabPrefixMatching:
    """Tests for lab ID prefix matching in dashboard metrics."""

    def test_truncated_lab_prefix_match(
        self,
        test_client: TestClient,
        test_db: Session,
    ):
        """Test that truncated lab IDs are matched."""
        # Create lab with long ID (UUID format)
        lab = models.Lab(name="Test Lab", provider="docker")
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        # Containerlab truncates to ~20 chars
        truncated_prefix = lab.id[:20]

        host = models.Host(
            id="test-agent",
            name="Test Agent",
            address="localhost:8080",
            status="online",
            capabilities=json.dumps({"providers": ["docker"]}),
            version="1.0.0",
            resource_usage=json.dumps({
                "cpu_percent": 10.0,
                "memory_percent": 20.0,
                "containers_running": 1,
                "container_details": [
                    {
                        "name": f"archetype-{truncated_prefix}-r1",
                        "status": "running",
                        "lab_prefix": truncated_prefix,
                        "is_system": False,
                    },
                ],
            }),
        )
        test_db.add(host)
        test_db.commit()

        response = test_client.get("/dashboard/metrics")
        assert response.status_code == 200
        data = response.json()

        # Lab should be counted as running
        assert data["labs_running"] == 1

    def test_partial_prefix_match(
        self,
        test_client: TestClient,
        test_db: Session,
    ):
        """Test that partial prefix matching works for short prefixes."""
        lab = models.Lab(name="Test Lab", provider="docker")
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        # Use just the beginning of the lab ID
        partial_prefix = lab.id[:8]

        host = models.Host(
            id="test-agent",
            name="Test Agent",
            address="localhost:8080",
            status="online",
            capabilities=json.dumps({"providers": ["docker"]}),
            version="1.0.0",
            resource_usage=json.dumps({
                "cpu_percent": 10.0,
                "memory_percent": 20.0,
                "containers_running": 1,
                "container_details": [
                    {
                        "name": f"archetype-{partial_prefix}-r1",
                        "status": "running",
                        "lab_prefix": partial_prefix,
                        "is_system": False,
                    },
                ],
            }),
        )
        test_db.add(host)
        test_db.commit()

        response = test_client.get("/dashboard/metrics")
        assert response.status_code == 200
        data = response.json()

        # Lab should still be matched and counted
        assert data["labs_running"] == 1
