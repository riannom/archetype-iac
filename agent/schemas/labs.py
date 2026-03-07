"""Lab status, reconciliation, discovery, cleanup, and config schemas."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from agent.schemas.base import BaseResponse
from agent.schemas.enums import NodeStatus


class NodeInfo(BaseModel):
    """Status of a single node."""
    name: str
    status: NodeStatus
    container_id: str | None = None
    runtime_id: str | None = None
    node_definition_id: str | None = None
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


class NodeReconcileResult(BaseResponse):
    """Result of reconciling a single node."""
    container_name: str
    action: Literal["started", "stopped", "removed", "already_running", "already_stopped", "error"]


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


class RuntimeIdentityAuditNode(BaseModel):
    """Provider-reported runtime identity coverage for one runtime object."""
    provider: str
    runtime_name: str
    lab_id: str | None = None
    node_name: str | None = None
    node_definition_id: str | None = None
    runtime_id: str | None = None
    resolved_by_metadata: bool = False
    name_only: bool = False
    missing_node_definition_id: bool = False
    missing_runtime_id: bool = False
    inconsistent_metadata: bool = False


class RuntimeIdentityAuditProvider(BaseModel):
    """Runtime identity audit summary for one provider on an agent."""
    provider: str
    managed_runtimes: int = 0
    resolved_by_metadata: int = 0
    name_only: int = 0
    missing_node_definition_id: int = 0
    missing_runtime_id: int = 0
    inconsistent_metadata: int = 0
    error: str | None = None
    nodes: list[RuntimeIdentityAuditNode] = Field(default_factory=list)


class RuntimeIdentityAuditResponse(BaseModel):
    """Agent -> Controller: runtime identity audit across providers."""
    providers: list[RuntimeIdentityAuditProvider] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class RuntimeIdentityBackfillEntry(BaseModel):
    """Authoritative runtime identity mapping for an existing runtime."""
    lab_id: str
    node_name: str
    node_definition_id: str
    provider: str


class RuntimeIdentityBackfillRequest(BaseModel):
    """Controller -> Agent: request runtime identity backfill."""
    entries: list[RuntimeIdentityBackfillEntry] = Field(default_factory=list)
    dry_run: bool = True


class RuntimeIdentityBackfillNodeResult(BaseModel):
    """Per-runtime backfill result."""
    lab_id: str
    node_name: str
    node_definition_id: str
    runtime_name: str
    outcome: str
    dry_run: bool | None = None


class RuntimeIdentityBackfillProviderResult(BaseModel):
    """Backfill result for one provider on one agent."""
    provider: str
    updated: int = 0
    recreate_required: int = 0
    missing: int = 0
    skipped: int = 0
    nodes: list[RuntimeIdentityBackfillNodeResult] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class RuntimeIdentityBackfillResponse(BaseModel):
    """Agent -> Controller: runtime identity backfill results."""
    providers: list[RuntimeIdentityBackfillProviderResult] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
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


class ExtractedConfig(BaseModel):
    """A single extracted node configuration."""
    node_name: str
    content: str


class ExtractConfigsResponse(BaseResponse):
    """Agent -> Controller: Config extraction result."""
    extracted_count: int = 0
    configs: list[ExtractedConfig] = Field(default_factory=list)


class ExtractNodeConfigResponse(BaseResponse):
    """Agent -> Controller: Single-node config extraction result."""
    node_name: str
    content: str | None = None


class UpdateConfigRequest(BaseModel):
    """Controller -> Agent: Push a startup config for a node."""
    content: str


class UpdateConfigResponse(BaseResponse):
    """Agent -> Controller: Config update result."""
