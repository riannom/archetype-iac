"""Agent-Controller protocol schemas.

These Pydantic models define the data structures exchanged between
the agent and the controller via HTTP/WebSocket.

Submodules:
    enums       - AgentStatus, NodeStatus, JobStatus, Provider, LinkState
    common      - Registration, heartbeat, job result
    deploy      - Lab deployment and teardown
    labs        - Lab status, reconciliation, discovery, cleanup, config
    overlay     - VXLAN tunnels, VTEPs, overlay convergence
    network     - Links, OVS, carrier, interfaces, external, port state
    images      - Image sync and inventory
    console     - Console access and CLI verification
    plugin      - Docker OVS plugin
    admin       - Agent updates, Docker pruning, workspace cleanup
    node_lifecycle - Per-node create/start/stop/destroy
    provisioning   - Endpoint repair, interface provisioning, transport config
"""

from agent.schemas.base import (  # noqa: F401
    BaseResponse,
    HardwareSpecMixin,
)

from agent.schemas.enums import (  # noqa: F401
    AgentStatus,
    JobStatus,
    LinkState,
    NodeStatus,
    Provider,
)

from agent.schemas.common import (  # noqa: F401
    AgentCapabilities,
    AgentInfo,
    HeartbeatRequest,
    HeartbeatResponse,
    JobResult,
    RegistrationRequest,
    RegistrationResponse,
)

from agent.schemas.deploy import (  # noqa: F401
    DeployLink,
    DeployNode,
    DeployRequest,
    DeployTopology,
    DestroyRequest,
)

from agent.schemas.labs import (  # noqa: F401
    CleanupLabOrphansRequest,
    CleanupLabOrphansResponse,
    CleanupOrphansRequest,
    CleanupOrphansResponse,
    DiscoveredLab,
    DiscoverLabsResponse,
    ExtractConfigsResponse,
    ExtractedConfig,
    ExtractNodeConfigResponse,
    LabStatusResponse,
    NodeInfo,
    RuntimeIdentityAuditNode,
    RuntimeIdentityAuditProvider,
    RuntimeIdentityAuditResponse,
    RuntimeIdentityBackfillEntry,
    RuntimeIdentityBackfillNodeResult,
    RuntimeIdentityBackfillProviderResult,
    RuntimeIdentityBackfillRequest,
    RuntimeIdentityBackfillResponse,
    NodeReconcileRequest,
    NodeReconcileResponse,
    NodeReconcileResult,
    NodeReconcileTarget,
    UpdateConfigRequest,
    UpdateConfigResponse,
)

from agent.schemas.overlay import (  # noqa: F401
    AttachContainerRequest,
    AttachContainerResponse,
    AttachOverlayExternalRequest,
    AttachOverlayExternalResponse,
    AttachOverlayInterfaceRequest,
    AttachOverlayInterfaceResponse,
    CleanupAuditRequest,
    CleanupAuditResponse,
    CleanupOverlayRequest,
    CleanupOverlayResponse,
    CreateTunnelRequest,
    CreateTunnelResponse,
    DeclareOverlayStateRequest,
    DeclareOverlayStateResponse,
    DeclaredTunnel,
    DeclaredTunnelResult,
    DetachOverlayInterfaceRequest,
    DetachOverlayInterfaceResponse,
    EnsureVtepRequest,
    EnsureVtepResponse,
    MtuTestRequest,
    MtuTestResponse,
    OverlayStatusResponse,
    TunnelInfo,
    VtepInfo,
)

from agent.schemas.network import (  # noqa: F401
    BridgeDeletePatchRequest,
    BridgeDeletePatchResponse,
    BridgePatchRequest,
    BridgePatchResponse,
    CarrierStateRequest,
    CarrierStateResponse,
    DeclarePortStateRequest,
    DeclarePortStateResponse,
    DeclaredPortPairing,
    DeclaredPortResult,
    ExternalConnectRequest,
    ExternalConnectResponse,
    ExternalConnectionInfo,
    ExternalDisconnectRequest,
    ExternalDisconnectResponse,
    ExternalListResponse,
    FixInterfacesResponse,
    InterfaceDetail,
    InterfaceDetailsResponse,
    LinkCreate,
    LinkCreateResponse,
    LinkDeleteResponse,
    LinkInfo,
    LinkListResponse,
    OVSPortInfo,
    OVSStatusResponse,
    PortIsolateResponse,
    PortInfo,
    PortRestoreRequest,
    PortRestoreResponse,
    PortStateResponse,
    PortVlanResponse,
    SetMtuRequest,
    SetMtuResponse,
)

from agent.schemas.images import (  # noqa: F401
    DockerImageInfo,
    ImageExistsResponse,
    ImageInventoryResponse,
    ImagePullProgress,
    ImagePullRequest,
    ImagePullResponse,
    ImageReceiveRequest,
    ImageReceiveResponse,
)

from agent.schemas.console import (  # noqa: F401
    CliCommandOutput,
    CliVerifyRequest,
    CliVerifyResponse,
    ConsoleInfo,
    ConsoleRequest,
)

from agent.schemas.plugin import (  # noqa: F401
    PluginBridgeInfo,
    PluginExternalAttachRequest,
    PluginExternalAttachResponse,
    PluginExternalInfo,
    PluginExternalListResponse,
    PluginFlowsResponse,
    PluginHealthResponse,
    PluginLabPortsResponse,
    PluginPortInfo,
    PluginStatusResponse,
    PluginVxlanRequest,
    PluginVxlanResponse,
)

from agent.schemas.admin import (  # noqa: F401
    CleanupWorkspacesRequest,
    DockerPruneRequest,
    DockerPruneResponse,
    UpdateProgressCallback,
    UpdateRequest,
    UpdateResponse,
)

from agent.schemas.node_lifecycle import (  # noqa: F401
    CreateNodeRequest,
    CreateNodeResponse,
    DestroyNodeResponse,
    StartNodeRequest,
    StartNodeResponse,
    StopNodeResponse,
)

from agent.schemas.provisioning import (  # noqa: F401
    EndpointRepairResult,
    InterfaceProvisionRequest,
    InterfaceProvisionResponse,
    RepairEndpointsRequest,
    RepairEndpointsResponse,
    TransportConfigResponse,
)
