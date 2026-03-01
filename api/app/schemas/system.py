from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


# =============================================================================
# Version and Update Schemas
# =============================================================================


class VersionInfo(BaseModel):
    """Current version information."""
    version: str
    build_time: str | None = None
    commit: str | None = None


class UpdateInfo(BaseModel):
    """Update check results."""
    current_version: str
    latest_version: str | None = None
    update_available: bool = False
    release_url: str | None = None
    release_notes: str | None = None
    published_at: str | None = None
    error: str | None = None


# =============================================================================
# Lab Logs Schemas
# =============================================================================


class LabLogEntry(BaseModel):
    """A single log entry for a lab."""

    timestamp: datetime
    level: str  # "info", "success", "warning", "error"
    message: str
    host_id: str | None = None
    host_name: str | None = None
    job_id: str | None = None
    source: str = "job"  # "job", "system", "realtime"


class LabLogJob(BaseModel):
    """Summary of a job for log filtering."""

    id: str
    action: str
    status: str
    created_at: datetime
    completed_at: datetime | None = None


class LabLogsResponse(BaseModel):
    """Response schema for lab logs endpoint."""

    entries: list[LabLogEntry]
    jobs: list[LabLogJob]  # Jobs available for filtering
    hosts: list[str]  # Hosts found in logs
    total_count: int
    error_count: int
    has_more: bool = False


# =============================================================================
# System Logs Schemas
# =============================================================================


class SystemLogEntry(BaseModel):
    """A single log entry from centralized logging."""

    timestamp: str
    level: str
    service: str
    message: str
    correlation_id: str | None = None
    logger: str | None = None
    extra: dict[str, Any] | None = None


class SystemLogQueryResponse(BaseModel):
    """Response from system log query endpoint."""

    entries: list[SystemLogEntry]
    total_count: int
    has_more: bool


# =============================================================================
# Config Snapshot Schemas
# =============================================================================


class ConfigSnapshotOut(BaseModel):
    """Output schema for a single config snapshot."""

    id: str
    lab_id: str
    node_name: str
    content: str
    content_hash: str
    snapshot_type: str  # "manual" or "auto_stop"
    device_kind: str | None = None
    mapped_to_node_id: str | None = None
    created_at: datetime
    # Computed fields added by API
    is_active: bool = False
    is_orphaned: bool = False

    model_config = ConfigDict(from_attributes=True)


class ConfigSnapshotsResponse(BaseModel):
    """Response schema for listing config snapshots."""

    snapshots: list[ConfigSnapshotOut]


class ConfigSnapshotCreate(BaseModel):
    """Input schema for creating a config snapshot."""

    node_name: str | None = None  # If None, snapshot all nodes


class ConfigMappingRequest(BaseModel):
    """Input schema for mapping a config snapshot to a target node."""

    target_node_id: str


class SetActiveConfigRequest(BaseModel):
    """Input schema for setting a node's active startup-config."""

    snapshot_id: str | None = None


class ConfigDiffRequest(BaseModel):
    """Input schema for generating a diff between two snapshots."""

    snapshot_id_a: str
    snapshot_id_b: str


class ConfigDiffLine(BaseModel):
    """A single line in a unified diff."""

    line_number_a: int | None = None  # Line number in version A (None for additions)
    line_number_b: int | None = None  # Line number in version B (None for deletions)
    content: str
    type: str  # "unchanged", "added", "removed", "header"


class ConfigDiffResponse(BaseModel):
    """Response schema for a config diff."""

    snapshot_a: ConfigSnapshotOut
    snapshot_b: ConfigSnapshotOut
    diff_lines: list[ConfigDiffLine]
    additions: int = 0
    deletions: int = 0


# ── Lab verification framework ──


class TestSpec(BaseModel):
    """A single test specification for lab verification."""
    type: Literal["ping", "command", "link_state", "node_state"]
    name: str | None = None
    # ping fields
    source: str | None = None
    target: str | None = None
    count: int = 3
    # command fields
    node: str | None = None
    cmd: str | None = None
    expect: str | None = None
    # state check fields
    link_name: str | None = None
    node_name: str | None = None
    expected_state: str | None = None


class RunTestsRequest(BaseModel):
    """Request to run lab verification tests."""
    specs: list[TestSpec] | None = None


class TestResultItem(BaseModel):
    """Result of a single test."""
    spec_index: int
    spec_name: str
    status: Literal["passed", "failed", "error", "skipped"]
    duration_ms: float
    output: str | None = None
    error: str | None = None


class TestRunResponse(BaseModel):
    """Response from starting a test run."""
    job_id: str
    message: str


# --- Scenario schemas ---

class ScenarioSummary(BaseModel):
    """Summary of a scenario file for listing."""
    filename: str
    name: str
    description: str = ""
    step_count: int = 0


class ScenarioDetail(BaseModel):
    """Full scenario definition with raw YAML."""
    filename: str
    name: str
    description: str = ""
    steps: list[dict]
    raw_yaml: str


class ScenarioSave(BaseModel):
    """Request body for creating/updating a scenario."""
    content: str


class ScenarioExecuteResponse(BaseModel):
    """Response from starting a scenario execution."""
    job_id: str
    scenario_name: str
    step_count: int
