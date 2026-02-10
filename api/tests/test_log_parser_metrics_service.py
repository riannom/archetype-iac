from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import app.services.log_parser as log_parser
import app.services.metrics_service as metrics_service
from app import models
from app.schemas import LabLogEntry


def test_log_parser_extracts_hosts_and_levels() -> None:
    log = """
=== Host: agent-a (host-1) ===
2024-01-01T10:00:00 INFO started
Agent: a0b1c2d3-e4f5-0000-0000-000000000002 (agent-b)
2024-01-01 10:00:01 ERROR failed
""".strip()

    parsed = log_parser.parse_job_log(log, job_id="job-1")
    assert len(parsed.entries) == 3
    assert "agent-a" in parsed.hosts
    assert "agent-b" in parsed.hosts
    assert parsed.entries[1].level == "info"
    assert parsed.entries[2].level == "error"


def test_log_parser_filtering() -> None:
    entries = [
        LabLogEntry(
            timestamp=datetime.now(timezone.utc),
            level="info",
            message="hello",
            host_id="h1",
            host_name="host-1",
            job_id="job",
            source="job",
        ),
        LabLogEntry(
            timestamp=datetime.now(timezone.utc),
            level="error",
            message="boom",
            host_id="h2",
            host_name="host-2",
            job_id="job",
            source="job",
        ),
    ]

    filtered = log_parser.filter_entries(entries, level="warning")
    assert len(filtered) == 1
    assert filtered[0].level == "error"


def test_metrics_service_dashboard_metrics(test_db) -> None:
    host = models.Host(
        id="h1",
        name="Host",
        address="localhost:1",
        status="online",
        capabilities="{}",
        version="1.0.0",
        last_heartbeat=datetime.now(timezone.utc),
        resource_usage="{\"cpu_percent\": 10, \"memory_percent\": 20, \"disk_used_gb\": 5, \"disk_total_gb\": 10, \"containers_running\": 1, \"containers_total\": 2}",
    )
    lab = models.Lab(
        name="Lab",
        owner_id="user",
        provider="docker",
        state="running",
        workspace_path="/tmp/lab",
    )
    test_db.add_all([host, lab])
    test_db.commit()

    service = metrics_service.MetricsService(test_db)
    # Mock is_agent_online because SQLite strips timezone from last_heartbeat,
    # causing naive vs aware datetime comparison in the real function
    with patch("app.agent_client.is_agent_online", return_value=True):
        metrics = service.get_dashboard_metrics()
    assert metrics["agents"]["online"] == 1
    assert metrics["labs"]["running"] == 1


def test_metrics_service_job_stats(test_db) -> None:
    job = models.Job(
        lab_id="lab",
        user_id=None,
        action="up",
        status="completed",
    )
    test_db.add(job)
    test_db.commit()

    service = metrics_service.MetricsService(test_db)
    stats = service.get_job_statistics(hours=1)
    assert stats["total"] == 1
    assert stats["by_status"]["completed"] == 1
