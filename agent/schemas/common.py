"""Agent registration, heartbeat, and job result schemas."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from agent.version import __version__, get_commit
from agent.schemas.enums import AgentStatus, JobStatus, Provider


class AgentCapabilities(BaseModel):
    """What the agent can do."""
    providers: list[Provider] = Field(default_factory=list)
    max_concurrent_jobs: int = 4
    features: list[str] = Field(default_factory=list)  # e.g., ["vxlan", "console"]


class AgentInfo(BaseModel):
    """Agent identification and capabilities."""
    agent_id: str
    name: str
    address: str  # host:port for controller to reach agent
    capabilities: AgentCapabilities
    version: str = __version__
    commit: str = Field(default_factory=get_commit)
    started_at: datetime | None = None  # When the agent process started
    is_local: bool = False  # True if co-located with controller (enables rebuild)
    deployment_mode: str = "unknown"  # systemd, docker, unknown - for update strategy
    # Separate data plane IP for VXLAN tunnels (when transport config is active)
    data_plane_ip: str | None = None
    docker_snapshotter_mode: str | None = None


class RegistrationRequest(BaseModel):
    """Agent -> Controller: Register this agent."""
    agent: AgentInfo
    token: str | None = None  # Optional auth token


class RegistrationResponse(BaseModel):
    """Controller -> Agent: Registration result."""
    success: bool
    message: str = ""
    assigned_id: str | None = None  # Controller may assign/confirm ID


class HeartbeatRequest(BaseModel):
    """Agent -> Controller: I'm still alive."""
    agent_id: str
    status: AgentStatus = AgentStatus.ONLINE
    active_jobs: int = 0
    resource_usage: dict[str, Any] = Field(default_factory=dict)  # cpu, memory, etc.
    data_plane_ip: str | None = None
    docker_snapshotter_mode: str | None = None


class HeartbeatResponse(BaseModel):
    """Controller -> Agent: Acknowledged, here's any pending work."""
    acknowledged: bool
    pending_jobs: list[str] = Field(default_factory=list)  # Job IDs to fetch


class JobResult(BaseModel):
    """Agent -> Controller: Job completed."""
    job_id: str
    status: JobStatus
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    error_message: str | None = None
    completed_at: datetime = Field(default_factory=datetime.utcnow)
