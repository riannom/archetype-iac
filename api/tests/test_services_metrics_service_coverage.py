"""Tests for app.services.metrics_service — MetricsService coverage."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from app import models
from app.services.metrics_service import MetricsService, get_metrics_service


class TestMetricsServiceDashboard:
    """Tests for MetricsService.get_dashboard_metrics()."""

    def test_no_hosts_no_labs(self, test_db: Session):
        service = MetricsService(test_db)
        metrics = service.get_dashboard_metrics()

        assert metrics["agents"]["total"] == 0
        assert metrics["agents"]["online"] == 0
        assert metrics["containers"]["running"] == 0
        assert metrics["resources"]["avg_cpu_percent"] == 0
        assert metrics["labs"]["total"] == 0
        assert "timestamp" in metrics

    def test_with_online_host(self, test_db: Session, sample_host: models.Host):
        """An online host contributes to agent counts and resource metrics."""
        service = MetricsService(test_db)

        with patch("app.agent_client.is_agent_online", return_value=True):
            metrics = service.get_dashboard_metrics()

        assert metrics["agents"]["total"] == 1
        assert metrics["agents"]["online"] == 1
        assert metrics["agents"]["offline"] == 0
        assert metrics["resources"]["avg_cpu_percent"] == 25.5
        assert metrics["containers"]["running"] == 5

    def test_with_offline_host(self, test_db: Session, sample_host: models.Host):
        """An offline host is counted but doesn't contribute to resource averages."""
        service = MetricsService(test_db)

        with patch("app.agent_client.is_agent_online", return_value=False):
            metrics = service.get_dashboard_metrics()

        assert metrics["agents"]["total"] == 1
        assert metrics["agents"]["offline"] == 1
        assert metrics["resources"]["avg_cpu_percent"] == 0

    def test_with_degraded_host(self, test_db: Session):
        """A degraded host is counted separately."""
        host = models.Host(
            id="degraded-1",
            name="Degraded Agent",
            address="localhost:9090",
            status="degraded",
            capabilities=json.dumps({}),
            version="1.0.0",
            resource_usage=json.dumps({}),
        )
        test_db.add(host)
        test_db.commit()

        service = MetricsService(test_db)
        with patch("app.agent_client.is_agent_online", return_value=False):
            metrics = service.get_dashboard_metrics()

        assert metrics["agents"]["degraded"] == 1

    def test_multiple_hosts_average(self, test_db: Session, multiple_hosts: list[models.Host]):
        """Averages are computed correctly across multiple online hosts."""
        service = MetricsService(test_db)

        def mock_online(host):
            return host.status == "online"

        with patch("app.agent_client.is_agent_online", side_effect=mock_online):
            metrics = service.get_dashboard_metrics()

        # 2 online hosts: agent-1 (30% cpu) + agent-2 (20% cpu) = avg 25%
        assert metrics["agents"]["online"] == 2
        assert metrics["agents"]["offline"] == 1
        assert metrics["resources"]["avg_cpu_percent"] == 25.0

    def test_lab_state_counts(self, test_db: Session, test_user: models.User):
        """Lab metrics count by state."""
        for state in ["running", "running", "stopped", "error"]:
            lab = models.Lab(
                name=f"Lab-{state}",
                owner_id=test_user.id,
                provider="docker",
                state=state,
            )
            test_db.add(lab)
        test_db.commit()

        service = MetricsService(test_db)
        with patch("app.agent_client.is_agent_online", return_value=False):
            metrics = service.get_dashboard_metrics()

        assert metrics["labs"]["total"] == 4
        assert metrics["labs"]["running"] == 2
        assert metrics["labs"]["stopped"] == 1
        assert metrics["labs"]["error"] == 1

    def test_containers_by_lab(self, test_db: Session, sample_host: models.Host):
        """Container details aggregate by lab prefix."""
        service = MetricsService(test_db)
        with patch("app.agent_client.is_agent_online", return_value=True):
            metrics = service.get_dashboard_metrics()

        # sample_host has 2 non-system containers with lab_prefix="test"
        assert metrics["containers"]["by_lab"].get("test") == 2


class TestCountLabsByState:
    def test_empty(self, test_db: Session):
        service = MetricsService(test_db)
        assert service._count_labs_by_state([]) == {}

    def test_none_state_becomes_unknown(self, test_db: Session):
        lab = MagicMock()
        lab.state = None
        service = MetricsService(test_db)
        counts = service._count_labs_by_state([lab])
        assert counts.get("unknown") == 1


class TestContainersBreakdown:
    def test_empty_hosts(self, test_db: Session):
        service = MetricsService(test_db)
        result = service.get_containers_breakdown()
        assert result["hosts"] == []
        assert result["totals"]["running"] == 0

    def test_with_host(self, test_db: Session, sample_host: models.Host):
        service = MetricsService(test_db)
        with patch("app.agent_client.is_agent_online", return_value=True):
            result = service.get_containers_breakdown()

        assert len(result["hosts"]) == 1
        assert result["hosts"][0]["id"] == sample_host.id
        assert result["totals"]["running"] == 5


class TestResourceDistribution:
    def test_empty(self, test_db: Session):
        service = MetricsService(test_db)
        result = service.get_resource_distribution()
        assert result["agents"] == []
        assert result["averages"]["cpu_percent"] == 0.0

    def test_with_online_host(self, test_db: Session, sample_host: models.Host):
        service = MetricsService(test_db)
        with patch("app.agent_client.is_agent_online", return_value=True):
            result = service.get_resource_distribution()

        assert len(result["agents"]) == 1
        assert result["agents"][0]["cpu_percent"] == 25.5
        assert result["averages"]["cpu_percent"] == 25.5


class TestJobStatistics:
    def test_no_jobs(self, test_db: Session):
        service = MetricsService(test_db)
        result = service.get_job_statistics()
        assert result["total"] == 0
        assert result["period_hours"] == 24

    def test_with_recent_jobs(self, test_db: Session, sample_lab: models.Lab, test_user: models.User):
        for status, action in [("completed", "up"), ("failed", "down"), ("completed", "up:node")]:
            job = models.Job(
                lab_id=sample_lab.id,
                user_id=test_user.id,
                action=action,
                status=status,
            )
            test_db.add(job)
        test_db.commit()

        service = MetricsService(test_db)
        result = service.get_job_statistics()
        assert result["total"] == 3
        assert result["by_status"]["completed"] == 2
        assert result["by_status"]["failed"] == 1
        assert result["by_action"]["up"] == 2


class TestNodeStateSummary:
    def test_no_states(self, test_db: Session):
        service = MetricsService(test_db)
        result = service.get_node_state_summary()
        assert result["total"] == 0
        assert result["ready"] == 0

    def test_with_node_states(self, test_db: Session, sample_lab_with_nodes):
        lab, nodes = sample_lab_with_nodes
        nodes[0].actual_state = "running"
        nodes[0].is_ready = True
        nodes[1].actual_state = "stopped"
        nodes[1].is_ready = False
        test_db.commit()

        service = MetricsService(test_db)
        result = service.get_node_state_summary()
        assert result["total"] == 2
        assert result["ready"] == 1
        assert result["by_state"]["running"] == 1
        assert result["by_state"]["stopped"] == 1


class TestGetMetricsService:
    def test_factory_returns_instance(self, test_db: Session):
        service = get_metrics_service(test_db)
        assert isinstance(service, MetricsService)
