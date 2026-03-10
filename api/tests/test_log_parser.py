from __future__ import annotations

from datetime import datetime, timezone

import app.services.log_parser as log_parser
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
