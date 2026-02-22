"""Agent update, Docker pruning, and workspace cleanup schemas."""

from pydantic import BaseModel, Field


class UpdateRequest(BaseModel):
    """Controller -> Agent: Update to a new version."""
    job_id: str
    target_version: str
    callback_url: str


class UpdateProgressCallback(BaseModel):
    """Agent -> Controller: Update progress report."""
    job_id: str
    agent_id: str
    status: str  # downloading, installing, restarting, completed, failed
    progress_percent: int = 0
    error_message: str | None = None


class UpdateResponse(BaseModel):
    """Agent -> Controller: Immediate response to update request."""
    accepted: bool
    message: str = ""
    deployment_mode: str = "unknown"  # systemd, docker, unknown


# --- Docker Pruning ---


class DockerPruneRequest(BaseModel):
    """Controller -> Agent: Request to prune Docker resources."""
    valid_lab_ids: list[str] = Field(default_factory=list)
    prune_dangling_images: bool = True
    prune_build_cache: bool = True
    prune_unused_volumes: bool = False
    prune_stopped_containers: bool = False
    prune_unused_networks: bool = False


class DockerPruneResponse(BaseModel):
    """Agent -> Controller: Result of Docker prune operation."""
    success: bool = True
    images_removed: int = 0
    build_cache_removed: int = 0
    volumes_removed: int = 0
    containers_removed: int = 0
    networks_removed: int = 0
    space_reclaimed: int = 0
    errors: list[str] = Field(default_factory=list)


# --- Workspace Cleanup ---


class CleanupWorkspacesRequest(BaseModel):
    """Controller -> Agent: Request to remove orphaned workspace directories."""
    valid_lab_ids: list[str] = Field(default_factory=list)
