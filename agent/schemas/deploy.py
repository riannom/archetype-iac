"""Lab deployment and teardown schemas."""

from pydantic import BaseModel, Field

from agent.schemas.enums import Provider


class DeployNode(BaseModel):
    """Node definition for JSON deploy request."""
    name: str                         # Container name (internal ID)
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
    # Hardware spec overrides (controller-resolved, highest priority at runtime)
    memory: int | None = Field(None, gt=0, description="RAM in MB")
    cpu: int | None = Field(None, gt=0, description="vCPU count")
    cpu_limit: int | None = Field(None, ge=1, le=100, description="CPU limit percentage")
    disk_driver: str | None = Field(None, description="Disk bus: virtio, ide, sata")
    nic_driver: str | None = Field(None, description="NIC model: virtio, e1000, rtl8139")
    machine_type: str | None = Field(None, description="QEMU machine type")
    libvirt_driver: str | None = Field(None, description="Libvirt domain driver: kvm or qemu")
    readiness_probe: str | None = Field(None, description="Readiness probe type override")
    readiness_pattern: str | None = Field(None, description="Readiness regex override")
    readiness_timeout: int | None = Field(None, gt=0, description="Readiness timeout override")
    efi_boot: bool | None = Field(None, description="Enable EFI firmware boot")
    efi_vars: str | None = Field(None, description="EFI vars mode (e.g., stateless)")
    data_volume_gb: int | None = Field(None, ge=0, description="Data volume size in GB (0 = none)")
    # Readiness overrides (controller-resolved, used for custom/imported kinds)
    readiness_probe: str | None = None
    readiness_pattern: str | None = None
    readiness_timeout: int | None = Field(None, gt=0, description="Boot readiness timeout in seconds")


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
