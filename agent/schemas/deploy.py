"""Lab deployment and teardown schemas."""

from pydantic import BaseModel, Field

from agent.schemas.base import HardwareSpecMixin
from agent.schemas.enums import Provider


class DeployNode(HardwareSpecMixin):
    """Node definition for JSON deploy request."""
    name: str                         # Container name (internal ID)
    node_definition_id: str | None = None
    display_name: str | None = None   # Human-readable name for logs
    kind: str = "linux"               # Device kind (ceos, srl, linux, etc.)
    image: str | None = None          # Docker image (uses vendor default if not specified)
    # Max interface index needed for this node (e.g., eth3 => 3).
    # Sourced from UI maxPorts (vendor defaults/overrides) and raised if any
    # link references a higher interface. Used to pre-provision interfaces
    # before boot (critical for devices like cEOS).
    interface_count: int | None = None
    binds: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    ports: list[str] = Field(default_factory=list)
    startup_config: str | None = None
    exec_cmds: list[str] = Field(default_factory=list)


class DeployLink(BaseModel):
    """Link definition for JSON deploy request."""
    source_node: str
    source_interface: str
    target_node: str
    target_interface: str


class DeployTopology(BaseModel):
    """Topology for JSON deploy request.

    This is the structured JSON format that replaces YAML for multi-host deployments.
    Each agent receives only the nodes assigned to it, with node host assignments
    determined by the controller using database `nodes.host_id`.
    """
    nodes: list[DeployNode]
    links: list[DeployLink] = Field(default_factory=list)


class DeployRequest(BaseModel):
    """Controller -> Agent: Deploy a lab topology.

    Uses structured JSON format only.
    """
    job_id: str
    lab_id: str
    topology: DeployTopology | None = None  # New JSON format (preferred)
    provider: Provider = Provider.DOCKER
    # Optional callback URL for async execution
    # If provided, agent returns 202 Accepted immediately and POSTs result to this URL
    callback_url: str | None = None


class DestroyRequest(BaseModel):
    """Controller -> Agent: Tear down a lab."""
    job_id: str
    lab_id: str
    provider: Provider = Provider.DOCKER
    # Optional callback URL for async execution
    callback_url: str | None = None
