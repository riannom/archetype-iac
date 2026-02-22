"""Per-node create, start, stop, and destroy schemas."""

from pydantic import BaseModel, Field


class CreateNodeRequest(BaseModel):
    """Controller -> Agent: Create a single node container."""
    node_name: str
    display_name: str | None = None
    kind: str = "linux"
    image: str | None = None
    interface_count: int | None = None
    binds: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    startup_config: str | None = None
    # Hardware spec overrides (API-resolved, take priority over VENDOR_CONFIGS)
    memory: int | None = Field(None, gt=0, description="RAM in MB")
    cpu: int | None = Field(None, gt=0, description="vCPU count")
    cpu_limit: int | None = Field(None, ge=1, le=100, description="CPU limit percentage")
    disk_driver: str | None = Field(None, description="Disk bus: virtio, ide, sata")
    nic_driver: str | None = Field(None, description="NIC model: virtio, e1000, rtl8139")
    machine_type: str | None = Field(None, description="QEMU machine type")
    libvirt_driver: str | None = Field(None, description="Libvirt domain driver: kvm or qemu")
    readiness_probe: str | None = Field(None, description="Readiness probe type override")
    readiness_pattern: str | None = Field(None, description="Readiness regex override")
    readiness_timeout: int | None = Field(None, gt=0, description="Boot readiness timeout in seconds")
    efi_boot: bool | None = Field(None, description="Enable EFI firmware boot")
    efi_vars: str | None = Field(None, description="EFI vars mode (e.g., stateless)")
    data_volume_gb: int | None = Field(None, ge=0, description="Data volume size in GB (0 = none)")
    image_sha256: str | None = Field(None, description="Expected SHA256 of backing image for integrity verification")


class CreateNodeResponse(BaseModel):
    """Agent -> Controller: Node creation result."""
    success: bool
    container_name: str | None = None
    container_id: str | None = None
    status: str = "unknown"
    details: str | None = None
    error: str | None = None
    duration_ms: int | None = None


class StartNodeRequest(BaseModel):
    """Controller -> Agent: Start a node with optional veth repair."""
    repair_endpoints: bool = True
    fix_interfaces: bool = True


class StartNodeResponse(BaseModel):
    """Agent -> Controller: Node start result."""
    success: bool
    status: str = "unknown"
    endpoints_repaired: int = 0
    interfaces_fixed: int = 0
    error: str | None = None
    duration_ms: int | None = None


class StopNodeResponse(BaseModel):
    """Agent -> Controller: Node stop result."""
    success: bool
    status: str = "unknown"
    error: str | None = None
    duration_ms: int | None = None


class DestroyNodeResponse(BaseModel):
    """Agent -> Controller: Node destroy result."""
    success: bool
    container_removed: bool = False
    error: str | None = None
    duration_ms: int | None = None
