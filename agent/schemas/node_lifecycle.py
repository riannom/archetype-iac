"""Per-node create, start, stop, and destroy schemas."""

from pydantic import BaseModel, Field

from agent.schemas.base import BaseResponse, HardwareSpecMixin


class CreateNodeRequest(HardwareSpecMixin):
    """Controller -> Agent: Create a single node container."""
    node_name: str
    display_name: str | None = None
    kind: str = "linux"
    image: str | None = None
    interface_count: int | None = None
    binds: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    startup_config: str | None = None
    image_sha256: str | None = Field(None, description="Expected SHA256 of backing image for integrity verification")


class CreateNodeResponse(BaseResponse):
    """Agent -> Controller: Node creation result."""
    container_name: str | None = None
    container_id: str | None = None
    status: str = "unknown"
    details: str | None = None
    duration_ms: int | None = None


class StartNodeRequest(BaseModel):
    """Controller -> Agent: Start a node with optional veth repair."""
    repair_endpoints: bool = True
    fix_interfaces: bool = True


class StartNodeResponse(BaseResponse):
    """Agent -> Controller: Node start result."""
    status: str = "unknown"
    endpoints_repaired: int = 0
    interfaces_fixed: int = 0
    duration_ms: int | None = None


class StopNodeResponse(BaseResponse):
    """Agent -> Controller: Node stop result."""
    status: str = "unknown"
    duration_ms: int | None = None


class DestroyNodeResponse(BaseResponse):
    """Agent -> Controller: Node destroy result."""
    container_removed: bool = False
    duration_ms: int | None = None
