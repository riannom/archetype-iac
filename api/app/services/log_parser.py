"""Log parsing service for multi-host job logs."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel

from app.schemas import LabLogEntry


class ParsedLogs(BaseModel):
    """Result of parsing job logs."""

    entries: list[LabLogEntry]
    hosts: list[str]  # List of host names found in logs


# Regex patterns for parsing log content
HOST_SECTION_PATTERN = re.compile(r"^=== Host: ([^(]+)\s*(?:\(([^)]+)\))?\s*===\s*$")
# Pattern for "Agent: id (name)" format used in job logs
AGENT_LINE_PATTERN = re.compile(r"^Agent:\s*([a-f0-9-]+)\s*\(([^)]+)\)\s*$")
TIMESTAMP_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})")
LEVEL_PATTERNS = [
    (re.compile(r"\bERROR\b", re.IGNORECASE), "error"),
    (re.compile(r"\bFAILED\b", re.IGNORECASE), "error"),
    (re.compile(r"\bWARN(?:ING)?\b", re.IGNORECASE), "warning"),
    (re.compile(r"\bSUCCESS\b", re.IGNORECASE), "success"),
    (re.compile(r"level=error", re.IGNORECASE), "error"),
    (re.compile(r"level=warn", re.IGNORECASE), "warning"),
    (re.compile(r"level=info", re.IGNORECASE), "info"),
]


def extract_level(line: str) -> Literal["info", "success", "warning", "error"]:
    """Extract log level from a line of text."""
    for pattern, level in LEVEL_PATTERNS:
        if pattern.search(line):
            return level
    return "info"


def extract_timestamp(line: str) -> datetime | None:
    """Try to extract a timestamp from a log line."""
    match = TIMESTAMP_PATTERN.match(line)
    if match:
        ts_str = match.group(1)
        # Try parsing common formats
        for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"]:
            try:
                return datetime.strptime(ts_str, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


def parse_job_log(
    log_content: str,
    job_id: str | None = None,
    job_created_at: datetime | None = None,
) -> ParsedLogs:
    """Parse job log content into structured entries.

    Job logs may contain host sections in the format:
        === Host: agent-name (agent-id) ===

    Each section's lines are associated with that host until the next section.

    Args:
        log_content: Raw log text content
        job_id: Optional job ID to include in entries
        job_created_at: Fallback timestamp if lines don't have timestamps

    Returns:
        ParsedLogs with entries and list of hosts found
    """
    if not log_content:
        return ParsedLogs(entries=[], hosts=[])

    entries: list[LabLogEntry] = []
    hosts_found: set[str] = set()
    current_host_name: str | None = None
    current_host_id: str | None = None
    base_timestamp = job_created_at or datetime.now(timezone.utc)
    line_index = 0

    lines = log_content.split("\n")

    for line in lines:
        line = line.rstrip()
        if not line:
            continue

        # Check for host section header (=== Host: name (id) ===)
        host_match = HOST_SECTION_PATTERN.match(line)
        if host_match:
            current_host_name = host_match.group(1).strip()
            current_host_id = host_match.group(2) if host_match.group(2) else None
            hosts_found.add(current_host_name)
            continue

        # Check for agent line (Agent: id (name))
        agent_match = AGENT_LINE_PATTERN.match(line)
        if agent_match:
            current_host_id = agent_match.group(1).strip()
            current_host_name = agent_match.group(2).strip()
            hosts_found.add(current_host_name)
            # Don't continue - still add as log entry

        # Parse as log entry
        timestamp = extract_timestamp(line)
        if not timestamp:
            # Use base timestamp with offset for ordering
            timestamp = base_timestamp.replace(
                microsecond=min(line_index * 1000, 999999)
            )

        level = extract_level(line)

        # Clean up the message (remove timestamp prefix if present)
        message = line
        ts_match = TIMESTAMP_PATTERN.match(line)
        if ts_match:
            message = line[len(ts_match.group(0)) :].lstrip(" -:")

        entries.append(
            LabLogEntry(
                timestamp=timestamp,
                level=level,
                message=message,
                host_id=current_host_id,
                host_name=current_host_name,
                job_id=job_id,
                source="job",
            )
        )
        line_index += 1

    return ParsedLogs(entries=entries, hosts=list(hosts_found))


def filter_entries(
    entries: list[LabLogEntry],
    host_id: str | None = None,
    level: str | None = None,
    search: str | None = None,
    since: datetime | None = None,
) -> list[LabLogEntry]:
    """Filter log entries by various criteria.

    Args:
        entries: List of log entries to filter
        host_id: Filter by host ID
        level: Filter by log level (or higher severity)
        search: Text search in message
        since: Only entries after this time

    Returns:
        Filtered list of entries
    """
    result = entries

    if host_id:
        result = [e for e in result if e.host_id == host_id or e.host_name == host_id]

    if level:
        level_priority = {"info": 0, "success": 1, "warning": 2, "error": 3}
        min_priority = level_priority.get(level.lower(), 0)
        result = [e for e in result if level_priority.get(e.level, 0) >= min_priority]

    if search:
        search_lower = search.lower()
        result = [e for e in result if search_lower in e.message.lower()]

    if since:
        result = [e for e in result if e.timestamp >= since]

    return result
