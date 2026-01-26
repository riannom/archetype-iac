from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class LabCreate(BaseModel):
    name: str
    provider: str = "containerlab"


class LabOut(BaseModel):
    id: str
    name: str
    owner_id: str | None
    workspace_path: str
    provider: str = "containerlab"
    state: str = "stopped"
    agent_id: str | None = None
    state_updated_at: datetime | None = None
    state_error: str | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class LabYamlIn(BaseModel):
    content: str


class LabYamlOut(BaseModel):
    content: str


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)


class UserOut(BaseModel):
    id: str
    email: EmailStr
    is_active: bool
    is_admin: bool
    created_at: datetime

    class Config:
        from_attributes = True


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class GraphEndpoint(BaseModel):
    node: str
    ifname: str | None = None
    # External connection type: "node" (default), "bridge", "macvlan", "host"
    # When type is not "node", the node field contains the bridge/interface name
    type: str = "node"
    # IP address for this interface (CIDR format, e.g., "10.0.0.1/24")
    ipv4: str | None = None
    ipv6: str | None = None


class GraphLink(BaseModel):
    endpoints: list[GraphEndpoint]
    type: str | None = None
    name: str | None = None
    pool: str | None = None
    prefix: str | None = None
    bridge: str | None = None
    mtu: int | None = None
    bandwidth: int | None = None


class GraphNode(BaseModel):
    id: str
    name: str
    device: str | None = None
    image: str | None = None
    version: str | None = None
    role: str | None = None
    mgmt: dict | None = None
    vars: dict | None = None
    host: str | None = None  # Agent ID for multi-host placement
    network_mode: str | None = None  # Container network mode (e.g., "bridge", "host", "none")


class TopologyGraph(BaseModel):
    nodes: list[GraphNode]
    links: list[GraphLink]
    defaults: dict | None = None


class NodePlacement(BaseModel):
    """Placement of a node on a specific host."""

    node_name: str
    host_id: str  # Agent ID


class CrossHostLink(BaseModel):
    """A link that spans two different hosts."""

    link_id: str  # Unique identifier for the link
    node_a: str  # Node name on host A
    interface_a: str  # Interface name on node A
    host_a: str  # Agent ID for host A
    ip_a: str | None = None  # IP address for node A's interface (CIDR format)
    node_b: str  # Node name on host B
    interface_b: str  # Interface name on node B
    host_b: str  # Agent ID for host B
    ip_b: str | None = None  # IP address for node B's interface (CIDR format)


class TopologyAnalysis(BaseModel):
    """Analysis of a topology for multi-host deployment."""

    placements: dict[str, list[NodePlacement]]  # host_id -> nodes
    cross_host_links: list[CrossHostLink]  # Links spanning hosts
    single_host: bool  # True if all nodes on one host


class JobOut(BaseModel):
    id: str
    lab_id: str | None
    user_id: str | None
    action: str
    status: str
    agent_id: str | None = None
    log_path: str | None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    retry_count: int = 0
    created_at: datetime

    class Config:
        from_attributes = True


class PermissionCreate(BaseModel):
    user_email: EmailStr
    role: str = "viewer"


class PermissionOut(BaseModel):
    id: str
    lab_id: str
    user_id: str
    role: str
    created_at: datetime
    user_email: EmailStr | None = None

    class Config:
        from_attributes = True


# Layout persistence schemas
class NodeLayout(BaseModel):
    """Visual position and styling for a node."""

    x: float
    y: float
    label: str | None = None
    color: str | None = None
    metadata: dict | None = None  # Extensible


class AnnotationLayout(BaseModel):
    """Layout data for an annotation (text, rect, circle, arrow, caption)."""

    id: str
    type: str  # text, rect, circle, arrow, caption
    x: float
    y: float
    width: float | None = None
    height: float | None = None
    text: str | None = None
    color: str | None = None
    fontSize: int | None = None
    targetX: float | None = None  # For arrows
    targetY: float | None = None  # For arrows
    metadata: dict | None = None  # Extensible


class LinkLayout(BaseModel):
    """Visual styling for a link."""

    color: str | None = None
    strokeWidth: int | None = None
    style: str | None = None  # solid, dashed, dotted
    metadata: dict | None = None  # Extensible


class CanvasState(BaseModel):
    """Canvas viewport state."""

    zoom: float | None = None
    offsetX: float | None = None
    offsetY: float | None = None


class LabLayout(BaseModel):
    """Complete visual layout for a lab workspace."""

    version: int = 1  # Schema versioning for migrations
    canvas: CanvasState | None = None
    nodes: dict[str, NodeLayout] = {}  # node_id -> position
    annotations: list[AnnotationLayout] = []
    links: dict[str, LinkLayout] | None = None  # link_id -> styling
    custom: dict | None = None  # Extensible user metadata
