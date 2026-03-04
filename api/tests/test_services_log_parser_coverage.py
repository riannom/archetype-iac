"""Tests for app.services.log_parser — extract_level, extract_timestamp, parse_job_log, filter_entries."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from app.services.log_parser import (
    extract_level,
    extract_timestamp,
    filter_entries,
    parse_job_log,
)
from app.schemas import LabLogEntry


# ---------------------------------------------------------------------------
# extract_level
# ---------------------------------------------------------------------------

class TestExtractLevel:
    def test_info_default(self):
        assert extract_level("Starting container") == "info"

    def test_error_keyword(self):
        assert extract_level("ERROR: something broke") == "error"

    def test_failed_keyword(self):
        assert extract_level("Container startup FAILED") == "error"

    def test_warning_keyword(self):
        assert extract_level("WARNING: low memory") == "warning"

    def test_warn_keyword(self):
        assert extract_level("WARN disk nearly full") == "warning"

    def test_success_keyword(self):
        assert extract_level("Node deployed SUCCESS") == "success"

    def test_level_equals_error(self):
        assert extract_level("level=error msg=oops") == "error"

    def test_level_equals_warn(self):
        assert extract_level("level=warn msg=hmm") == "warning"

    def test_level_equals_info(self):
        assert extract_level("level=info msg=ok") == "info"

    def test_empty_string(self):
        assert extract_level("") == "info"

    def test_mixed_case(self):
        assert extract_level("error found") == "error"

    def test_no_match_returns_info(self):
        assert extract_level("everything is fine") == "info"


# ---------------------------------------------------------------------------
# extract_timestamp
# ---------------------------------------------------------------------------

class TestExtractTimestamp:
    def test_iso_format(self):
        result = extract_timestamp("2024-01-15T10:30:45 some message")
        assert result is not None
        assert result.year == 2024
        assert result.month == 1
        assert result.hour == 10
        assert result.tzinfo == timezone.utc

    def test_space_separated_format(self):
        result = extract_timestamp("2024-06-20 14:05:30 INFO starting")
        assert result is not None
        assert result.day == 20
        assert result.minute == 5

    def test_no_timestamp(self):
        assert extract_timestamp("just a plain message") is None

    def test_empty_string(self):
        assert extract_timestamp("") is None

    def test_malformed_timestamp(self):
        assert extract_timestamp("2024-99-99T99:99:99 bad date") is None

    def test_timestamp_at_start_only(self):
        # Timestamp must be at the start of the line
        assert extract_timestamp("message 2024-01-15T10:30:45") is None


# ---------------------------------------------------------------------------
# parse_job_log
# ---------------------------------------------------------------------------

class TestParseJobLog:
    def test_empty_content(self):
        result = parse_job_log("")
        assert result.entries == []
        assert result.hosts == []

    def test_simple_lines(self):
        content = "Starting deploy\nContainer created\nDone"
        result = parse_job_log(content)
        assert len(result.entries) == 3
        assert result.entries[0].message == "Starting deploy"

    def test_host_section_header(self):
        content = "=== Host: agent-1 (abc-123) ===\nDeploying node R1"
        result = parse_job_log(content)
        assert "agent-1" in result.hosts
        assert len(result.entries) == 1
        assert result.entries[0].host_name == "agent-1"
        assert result.entries[0].host_id == "abc-123"

    def test_host_section_without_id(self):
        content = "=== Host: my-agent ===\nRunning"
        result = parse_job_log(content)
        assert "my-agent" in result.hosts
        assert result.entries[0].host_id is None

    def test_agent_line_format(self):
        content = "Agent: abc-123 (my-agent)\nDeploying"
        result = parse_job_log(content)
        assert "my-agent" in result.hosts
        # Agent line also produces an entry
        assert len(result.entries) == 2

    def test_timestamp_extraction_in_log(self):
        content = "2024-01-15T10:30:45 INFO Deploy complete"
        result = parse_job_log(content)
        assert len(result.entries) == 1
        assert result.entries[0].timestamp.year == 2024
        # Message should have timestamp prefix stripped
        assert "2024-01-15" not in result.entries[0].message

    def test_fallback_timestamp(self):
        base = datetime(2024, 6, 1, tzinfo=timezone.utc)
        content = "line one\nline two"
        result = parse_job_log(content, job_created_at=base)
        assert result.entries[0].timestamp.year == 2024
        assert result.entries[0].timestamp.month == 6

    def test_job_id_passed_through(self):
        content = "Test line"
        result = parse_job_log(content, job_id="job-42")
        assert result.entries[0].job_id == "job-42"

    def test_blank_lines_skipped(self):
        content = "line1\n\n\nline2"
        result = parse_job_log(content)
        assert len(result.entries) == 2

    def test_multiple_host_sections(self):
        content = (
            "=== Host: agent-1 (id1) ===\n"
            "Running on agent-1\n"
            "=== Host: agent-2 (id2) ===\n"
            "Running on agent-2\n"
        )
        result = parse_job_log(content)
        assert set(result.hosts) == {"agent-1", "agent-2"}
        assert result.entries[0].host_name == "agent-1"
        assert result.entries[1].host_name == "agent-2"


# ---------------------------------------------------------------------------
# filter_entries
# ---------------------------------------------------------------------------

class TestFilterEntries:
    @pytest.fixture
    def sample_entries(self) -> list[LabLogEntry]:
        base = datetime(2024, 1, 1, tzinfo=timezone.utc)
        return [
            LabLogEntry(
                timestamp=base,
                level="info",
                message="Starting deployment",
                host_id="h1",
                host_name="agent-1",
            ),
            LabLogEntry(
                timestamp=base + timedelta(minutes=1),
                level="warning",
                message="Slow startup detected",
                host_id="h2",
                host_name="agent-2",
            ),
            LabLogEntry(
                timestamp=base + timedelta(minutes=2),
                level="error",
                message="Container FAILED to start",
                host_id="h1",
                host_name="agent-1",
            ),
            LabLogEntry(
                timestamp=base + timedelta(minutes=3),
                level="success",
                message="Deployment complete",
                host_id="h2",
                host_name="agent-2",
            ),
        ]

    def test_no_filters(self, sample_entries):
        result = filter_entries(sample_entries)
        assert len(result) == 4

    def test_filter_by_host_id(self, sample_entries):
        result = filter_entries(sample_entries, host_id="h1")
        assert len(result) == 2
        assert all(e.host_id == "h1" for e in result)

    def test_filter_by_host_name(self, sample_entries):
        result = filter_entries(sample_entries, host_id="agent-2")
        assert len(result) == 2

    def test_filter_by_level_warning(self, sample_entries):
        """Level filter returns entries at or above the given severity."""
        result = filter_entries(sample_entries, level="warning")
        assert len(result) == 2
        assert all(e.level in ("warning", "error") for e in result)

    def test_filter_by_level_error(self, sample_entries):
        result = filter_entries(sample_entries, level="error")
        assert len(result) == 1

    def test_filter_by_search(self, sample_entries):
        result = filter_entries(sample_entries, search="deployment")
        assert len(result) == 2

    def test_filter_by_search_case_insensitive(self, sample_entries):
        result = filter_entries(sample_entries, search="FAILED")
        assert len(result) == 1

    def test_filter_by_since(self, sample_entries):
        cutoff = datetime(2024, 1, 1, 0, 1, 30, tzinfo=timezone.utc)
        result = filter_entries(sample_entries, since=cutoff)
        assert len(result) == 2

    def test_combined_filters(self, sample_entries):
        result = filter_entries(sample_entries, host_id="h1", level="error")
        assert len(result) == 1
        assert result[0].level == "error"

    def test_empty_entries(self):
        result = filter_entries([])
        assert result == []
