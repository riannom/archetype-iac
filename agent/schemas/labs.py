"""Lab status, reconciliation, discovery, cleanup, and config schemas."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from agent.schemas.enums import NodeStatus


class LabStatusRequest(BaseModel):
    """Controller -> Agent: Get status of a lab."""
    lab_id: str


class NodeInfo(BaseModel):
    """Status of a single node."""
    name: str
    status: NodeStatus
    container_id: str | None = None
    image: str | None = None
    ip_addresses: list[str] = Field(default_factory=list)
    error: str | None = None


class LabStatusResponse(BaseModel):
    """Agent -> Controller: Lab status."""
    lab_id: str
    nodes: list[NodeInfo] = Field(default_factory=list)
    error: str | None = None


# --- Node Reconciliation ---


class NodeReconcileTarget(BaseModel):
    """A node to reconcile with its desired state."""
    container_name: str
    desired_state: Literal["running", "stopped"]


class NodeReconcileRequest(BaseModel):
    """Controller -> Agent: Reconcile nodes to desired states."""
    nodes: list[NodeReconcileTarget]


class NodeReconcileResult(BaseModel):
    """Result of reconciling a single node."""
    container_name: str
    action: Literal["started", "stopped", "removed", "already_running", "already_stopped", "error"]
    success: bool
    error: str | None = None


class NodeReconcileResponse(BaseModel):
    """Agent -> Controller: Reconcile results."""
    lab_id: str
    results: list[NodeReconcileResult] = Field(default_factory=list)
    error: str | None = None


# --- Discovery ---


class DiscoveredLab(BaseModel):
    """A lab discovered via container inspection."""
    lab_id: str
    nodes: list[NodeInfo] = Field(default_factory=list)


class DiscoverLabsResponse(BaseModel):
    """Response from lab discovery endpoint."""
    labs: list[DiscoveredLab] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# --- Cleanup ---


class CleanupOrphansRequest(BaseModel):
    """Request to clean up orphan containers."""
    valid_lab_ids: list[str] = Field(default_factory=list)


class CleanupOrphansResponse(BaseModel):
    """Response from orphan cleanup endpoint."""
    removed_containers: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class CleanupLabOrphansRequest(BaseModel):
    """Request to clean up orphan containers for a specific lab.

    Used when nodes are migrated between agents - removes containers
    for nodes that are no longer assigned to this agent.
    """
    lab_id: str
    keep_node_names: list[str] = Field(default_factory=list)
    """Node names that should be kept on this agent. All other containers for this lab will be removed."""


class CleanupLabOrphansResponse(BaseModel):
    """Response from lab orphan cleanup endpoint."""
    removed_containers: list[str] = Field(default_factory=list)
    kept_containers: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


# --- Config Extraction ---


class ExtractConfigsRequest(BaseModel):
    """Controller -> Agent: Extract configs from running cEOS nodes."""
    lab_id: str


class ExtractedConfig(BaseModel):
    """A single extracted node configuration."""
    node_name: str
    content: str


class ExtractConfigsResponse(BaseModel):
    """Agent -> Controller: Config extraction result."""
    success: bool
    extracted_count: int = 0
    configs: list[ExtractedConfig] = Field(default_factory=list)
    error: str | None = None


class ExtractNodeConfigResponse(BaseModel):
    """Agent -> Controller: Single-node config extraction result."""
    success: bool
    node_name: str
    content: str | None = None
    error: str | None = None


class UpdateConfigRequest(BaseModel):
    """Controller -> Agent: Push a startup config for a node."""
    content: str


class UpdateConfigResponse(BaseModel):
    """Agent -> Controller: Config update result."""
    success: bool
    error: str | None = None
