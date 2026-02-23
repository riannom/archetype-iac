"""Image synchronization and inventory schemas."""

from datetime import datetime

from pydantic import BaseModel, Field

from agent.schemas.base import BaseResponse


class DockerImageInfo(BaseModel):
    """Information about a Docker image on an agent."""
    id: str  # Docker image ID (sha256:...)
    tags: list[str] = Field(default_factory=list)  # Image tags (e.g., ["ceos:4.28.0F"])
    size_bytes: int = 0
    created: str | None = None  # ISO timestamp


class ImageInventoryResponse(BaseModel):
    """Agent -> Controller: List of Docker images on agent."""
    images: list[DockerImageInfo] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class ImageExistsResponse(BaseModel):
    """Agent -> Controller: Whether an image exists."""
    exists: bool
    image: DockerImageInfo | None = None
    sha256: str | None = None


class ImageReceiveRequest(BaseModel):
    """Controller -> Agent: Metadata for incoming image stream."""
    image_id: str  # Library image ID (e.g., "docker:ceos:4.28.0F")
    reference: str  # Docker reference (e.g., "ceos:4.28.0F")
    total_bytes: int  # Expected size for progress tracking
    job_id: str | None = None  # Sync job ID for progress reporting


class ImageReceiveResponse(BaseResponse):
    """Agent -> Controller: Result of receiving an image."""
    loaded_images: list[str] = Field(default_factory=list)  # Tags of loaded images


class ImagePullRequest(BaseModel):
    """Agent -> Controller: Request to pull an image from controller."""
    image_id: str  # Library image ID
    reference: str  # Docker reference


class ImagePullResponse(BaseModel):
    """Controller -> Agent: Pull job created."""
    job_id: str
    status: str = "pending"


class ImagePullProgress(BaseModel):
    """Progress of an image pull operation."""
    job_id: str
    status: str  # pending, transferring, loading, completed, failed
    progress_percent: int = 0
    bytes_transferred: int = 0
    total_bytes: int = 0
    error: str | None = None
    started_at: float | None = None
