# Extensibility Refactor Plan

This plan addresses architectural issues identified in the extensibility audit, enabling proper support for multiple providers (Docker, Libvirt, Podman), network backends (OVS, Linux bridge), and vendors without code changes.

## Overview

| Phase | Focus | Risk | Effort |
|-------|-------|------|--------|
| 1 | Network Backend Abstraction | High | Large |
| 2 | Provider Interface Enforcement | High | Medium |
| 3 | Vendor Plugin Activation | Low | Medium |
| 4 | State Machine Centralization | Medium | Medium |
| 5 | Database Schema Hardening | Low | Small |

---

## Phase 1: Network Backend Abstraction

**Goal**: Create a unified interface for network backends so OVS, Linux bridge, or other implementations can be swapped without changing provider code.

### 1.1 Define NetworkBackend Interface

**New file**: `agent/network/backend.py`

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

@dataclass
class LinkResult:
    success: bool
    vlan_tag: Optional[int] = None
    error: Optional[str] = None

class NetworkBackend(ABC):
    """Abstract interface for network backends (OVS, Linux bridge, etc.)"""

    @property
    @abstractmethod
    def name(self) -> str:
        """Backend identifier (e.g., 'ovs', 'linux_bridge')"""
        ...

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize backend resources (create bridges, etc.)"""
        ...

    @abstractmethod
    async def cleanup(self) -> None:
        """Cleanup backend resources"""
        ...

    @abstractmethod
    async def provision_interface(
        self,
        container_name: str,
        interface_name: str,
        lab_id: str,
    ) -> int:
        """
        Provision an interface for a container before boot.
        Returns: VLAN tag assigned to the interface
        """
        ...

    @abstractmethod
    async def hot_connect(
        self,
        lab_id: str,
        container_a: str,
        interface_a: str,
        container_b: str,
        interface_b: str,
    ) -> LinkResult:
        """
        Create L2 connectivity between two interfaces.
        Returns: LinkResult with VLAN tag if successful
        """
        ...

    @abstractmethod
    async def hot_disconnect(
        self,
        lab_id: str,
        container: str,
        interface: str,
    ) -> LinkResult:
        """
        Remove interface from shared VLAN, isolating it.
        Returns: LinkResult with new isolated VLAN tag
        """
        ...

    @abstractmethod
    async def delete_interface(
        self,
        container_name: str,
        interface_name: str,
    ) -> bool:
        """Remove an interface entirely"""
        ...

    @abstractmethod
    async def get_interface_vlan(
        self,
        container_name: str,
        interface_name: str,
    ) -> Optional[int]:
        """Get current VLAN tag for an interface"""
        ...


class OverlayBackend(ABC):
    """Abstract interface for cross-host overlay networking"""

    @abstractmethod
    async def create_tunnel(
        self,
        local_ip: str,
        remote_ip: str,
        vni: int,
        vlan_tag: int,
    ) -> bool:
        """Create VXLAN tunnel to remote host"""
        ...

    @abstractmethod
    async def delete_tunnel(
        self,
        vni: int,
    ) -> bool:
        """Remove VXLAN tunnel"""
        ...

    @abstractmethod
    async def get_tunnel_status(
        self,
        vni: int,
    ) -> dict:
        """Get tunnel health/status"""
        ...
```

### 1.2 Implement OVS Backend

**New file**: `agent/network/backends/ovs.py`

Refactor existing code from:
- `agent/network/docker_plugin.py` (DockerOVSPlugin.hot_connect)
- `agent/network/ovs.py` (OVSNetworkManager)

Into a single `OVSBackend(NetworkBackend)` implementation.

**Key changes**:
- Consolidate duplicate hot_connect implementations
- Move all `ovs-vsctl` commands into this single module
- Implement both shared-bridge and dedicated-bridge modes via config

### 1.3 Create Backend Registry

**New file**: `agent/network/backend_registry.py`

```python
from agent.network.backend import NetworkBackend
from agent.config import get_settings

_backend: NetworkBackend | None = None

def get_network_backend() -> NetworkBackend:
    global _backend
    if _backend is None:
        settings = get_settings()
        if settings.network_backend == "ovs":
            from agent.network.backends.ovs import OVSBackend
            _backend = OVSBackend(settings)
        elif settings.network_backend == "linux_bridge":
            from agent.network.backends.linux_bridge import LinuxBridgeBackend
            _backend = LinuxBridgeBackend(settings)
        else:
            raise ValueError(f"Unknown network backend: {settings.network_backend}")
    return _backend
```

### 1.4 Update Provider to Use Backend

**Modify**: `agent/providers/docker.py`

Before:
```python
if self.use_ovs_plugin:
    await self._plugin_hot_connect(...)
elif self.use_ovs and self.ovs_manager._initialized:
    await self.ovs_manager.hot_connect(...)
else:
    await self.local_network.create_link(...)
```

After:
```python
from agent.network.backend_registry import get_network_backend

backend = get_network_backend()
result = await backend.hot_connect(lab_id, container_a, iface_a, container_b, iface_b)
```

### 1.5 Update API Endpoints

**Modify**: `agent/main.py`

Replace all direct OVS/plugin calls with backend abstraction:
- Lines 2509-2564: `/labs/{lab_id}/links` endpoint
- Lines 2566-2620: `/labs/{lab_id}/links` DELETE endpoint
- Lines 201-220: Remove individual manager getters, use single backend getter

### 1.6 Configuration Changes

**Modify**: `agent/config.py`

```python
# Replace multiple flags
# enable_ovs: bool = True
# enable_ovs_plugin: bool = True

# With single backend selection
network_backend: str = "ovs"  # "ovs" | "linux_bridge" | "veth"
ovs_mode: str = "shared"  # "shared" | "dedicated" (only for OVS backend)
```

### 1.7 Files to Modify

| File | Changes |
|------|---------|
| `agent/network/backend.py` | NEW - Interface definitions |
| `agent/network/backend_registry.py` | NEW - Backend factory |
| `agent/network/backends/ovs.py` | NEW - OVS implementation |
| `agent/network/backends/__init__.py` | NEW - Package init |
| `agent/network/docker_plugin.py` | Refactor to use/extend OVSBackend |
| `agent/network/ovs.py` | Deprecate, merge into OVSBackend |
| `agent/network/local.py` | Remove OVS imports, use backend |
| `agent/providers/docker.py` | Use backend instead of direct managers |
| `agent/main.py` | Use backend for all link operations |
| `agent/config.py` | Simplify network config flags |

### 1.8 Migration Strategy

1. Create new backend interface and OVS implementation
2. Create adapter that wraps existing managers (temporary)
3. Update providers to use adapter
4. Gradually migrate manager code into backend
5. Remove deprecated managers
6. Update config flags

---

## Phase 2: Provider Interface Enforcement

**Goal**: Ensure all provider-specific code goes through the Provider interface, enabling LibvirtProvider and future providers to work.

### 2.1 Add Missing Abstract Methods

**Modify**: `agent/providers/base.py`

```python
class Provider(ABC):
    # Existing methods...

    @abstractmethod
    def get_node_identifier(self, lab_id: str, node_name: str) -> str:
        """Get the runtime identifier for a node (container name, domain name, etc.)"""
        ...

    @abstractmethod
    async def list_nodes(self, lab_id: str) -> list[NodeInfo]:
        """List all nodes for a lab with their current status"""
        ...

    @abstractmethod
    async def extract_config(self, lab_id: str, node_name: str) -> str | None:
        """Extract running configuration from a node"""
        ...

    @abstractmethod
    async def extract_all_configs(self, lab_id: str, workspace: str) -> dict[str, str]:
        """Extract configs from all nodes in a lab"""
        ...

    @abstractmethod
    async def get_node_logs(self, lab_id: str, node_name: str, lines: int = 100) -> str:
        """Get recent logs from a node"""
        ...

    @abstractmethod
    async def exec_command(
        self,
        lab_id: str,
        node_name: str,
        command: list[str],
    ) -> tuple[int, str, str]:
        """Execute command in node, return (exit_code, stdout, stderr)"""
        ...
```

### 2.2 Implement in DockerProvider

**Modify**: `agent/providers/docker.py`

```python
def get_node_identifier(self, lab_id: str, node_name: str) -> str:
    return self._container_name(lab_id, node_name)

async def list_nodes(self, lab_id: str) -> list[NodeInfo]:
    containers = await asyncio.to_thread(
        self.docker.containers.list,
        all=True,
        filters={"label": f"archetype.lab_id={lab_id}"},
    )
    return [NodeInfo(name=c.labels.get("archetype.node_name"), ...) for c in containers]

async def extract_config(self, lab_id: str, node_name: str) -> str | None:
    # Move logic from _extract_all_ceos_configs here
    ...

async def extract_all_configs(self, lab_id: str, workspace: str) -> dict[str, str]:
    # Rename from _extract_all_ceos_configs, make public
    ...
```

### 2.3 Implement Stubs in LibvirtProvider

**Modify**: `agent/providers/libvirt.py`

Implement all abstract methods, even if initially returning errors:

```python
def get_node_identifier(self, lab_id: str, node_name: str) -> str:
    return self._domain_name(lab_id, node_name)

async def list_nodes(self, lab_id: str) -> list[NodeInfo]:
    domains = self.conn.listAllDomains()
    return [NodeInfo(...) for d in domains if lab_id in d.name()]

async def extract_config(self, lab_id: str, node_name: str) -> str | None:
    # VMs typically need SSH or vendor-specific extraction
    raise NotImplementedError("Config extraction not yet supported for VMs")
```

### 2.4 Fix main.py Violations

**Modify**: `agent/main.py`

| Line | Current | Fixed |
|------|---------|-------|
| 328 | `provider.docker.containers.list` | `provider.list_nodes(lab_id)` |
| 341 | `provider._fix_interface_names()` | Move to provider method or remove |
| 1479 | `provider._extract_all_ceos_configs()` | `provider.extract_all_configs()` |
| 1974 | `provider.get_container_name()` | `provider.get_node_identifier()` |
| 3635 | `docker.from_env()` | `get_provider().docker` or new method |

### 2.5 Create NodeInfo Dataclass

**New file**: `agent/providers/types.py`

```python
from dataclasses import dataclass
from typing import Optional

@dataclass
class NodeInfo:
    name: str
    identifier: str  # Container name, domain name, etc.
    status: str  # running, stopped, etc.
    lab_id: str
    kind: Optional[str] = None
    image: Optional[str] = None
    created_at: Optional[str] = None
```

### 2.6 Files to Modify

| File | Changes |
|------|---------|
| `agent/providers/base.py` | Add abstract methods |
| `agent/providers/types.py` | NEW - Shared types |
| `agent/providers/docker.py` | Implement new methods, expose privates |
| `agent/providers/libvirt.py` | Implement abstract methods |
| `agent/main.py` | Replace Docker-specific calls |

---

## Phase 3: Vendor Plugin Activation

**Goal**: Activate the existing but unused plugin system so vendors can be added via plugins instead of code changes.

### 3.1 Unify VendorConfig Definitions

**Problem**: Two different VendorConfig classes exist:
- `agent/vendors.py:31-161` (used)
- `agent/plugins/__init__.py:61-108` (unused)

**Solution**: Keep vendors.py VendorConfig as the canonical definition, update plugins to use it.

**Modify**: `agent/plugins/__init__.py`

```python
from agent.vendors import VendorConfig  # Use existing definition

class VendorPlugin(ABC):
    @abstractmethod
    def get_configs(self) -> list[VendorConfig]:
        """Return vendor configurations provided by this plugin"""
        ...

    @abstractmethod
    def get_container_hooks(self) -> dict[str, ContainerHook]:
        """Return hooks for container lifecycle events"""
        ...
```

### 3.2 Add Data-Driven Vendor Fields

**Modify**: `agent/vendors.py` VendorConfig

```python
@dataclass
class VendorConfig:
    # Existing fields...

    # NEW: Platform detection workarounds (currently hardcoded for cEOS)
    platform_detection_race: bool = False
    interface_wait_env_var: str | None = None
    interface_wait_script: str | None = None

    # NEW: Boot progress patterns (currently hardcoded in readiness.py)
    progress_patterns: dict[str, int] = field(default_factory=dict)

    # NEW: Vendor-specific options (currently hardcoded in _get_vendor_options)
    vendor_options: dict[str, Any] = field(default_factory=dict)

    # NEW: CLI probe command (currently misusing console_shell)
    readiness_cli_command: str | None = None
```

### 3.3 Update cEOS Config to Use New Fields

**Modify**: `agent/vendors.py` cEOS entry

```python
"ceos": VendorConfig(
    kind="ceos",
    # ... existing fields ...

    # NEW: Data-driven platform detection
    platform_detection_race=True,
    interface_wait_env_var="CLAB_INTFS",
    interface_wait_script="/mnt/flash/if-wait.sh ; exec /sbin/init",

    # NEW: Progress patterns
    progress_patterns={
        "Platform initialization": 10,
        "Starting ProcMgr": 30,
        "ProcMgr initialization complete": 50,
        "Patch applied": 70,
        "patch applied": 70,
        "%BOOT": 90,
    },

    # NEW: Vendor options
    vendor_options={
        "zerotouchCancel": True,
    },
),
```

### 3.4 Remove Hardcoded Vendor Checks

**Modify**: `agent/providers/docker.py`

Before (lines 434-442):
```python
if is_ceos_kind(node.kind) and interface_count > 0:
    config["environment"]["CLAB_INTFS"] = str(interface_count)
    config["entrypoint"] = ["/bin/bash", "-c"]
    config["command"] = ["/mnt/flash/if-wait.sh ; exec /sbin/init"]
```

After:
```python
vendor_config = get_vendor_config(node.kind)
if vendor_config.platform_detection_race and interface_count > 0:
    if vendor_config.interface_wait_env_var:
        config["environment"][vendor_config.interface_wait_env_var] = str(interface_count)
    if vendor_config.interface_wait_script:
        config["entrypoint"] = ["/bin/bash", "-c"]
        config["command"] = [vendor_config.interface_wait_script]
```

**Modify**: `agent/readiness.py`

Before (lines 245-246):
```python
if is_ceos_kind(kind):
    progress_patterns = CEOS_PROGRESS_PATTERNS
```

After:
```python
vendor_config = get_vendor_config(kind)
progress_patterns = vendor_config.progress_patterns or {}
```

### 3.5 Wire Up Plugin Loading

**Modify**: `agent/vendors.py`

```python
def load_vendor_configs() -> dict[str, VendorConfig]:
    """Load vendor configs from built-in registry and plugins"""
    configs = dict(VENDOR_CONFIGS)  # Start with built-ins

    # Load plugins
    from agent.plugins import discover_plugins
    for plugin in discover_plugins():
        for config in plugin.get_configs():
            if config.kind in configs:
                logger.warning(f"Plugin overriding vendor {config.kind}")
            configs[config.kind] = config

    return configs

# Replace global VENDOR_CONFIGS access with function call
_loaded_configs: dict[str, VendorConfig] | None = None

def get_vendor_configs() -> dict[str, VendorConfig]:
    global _loaded_configs
    if _loaded_configs is None:
        _loaded_configs = load_vendor_configs()
    return _loaded_configs
```

### 3.6 Files to Modify

| File | Changes |
|------|---------|
| `agent/vendors.py` | Add new fields, update cEOS config, add plugin loading |
| `agent/plugins/__init__.py` | Use vendors.py VendorConfig, update interface |
| `agent/plugins/builtin/arista.py` | Update to new plugin interface |
| `agent/providers/docker.py` | Use data-driven fields instead of is_ceos_kind() |
| `agent/readiness.py` | Use vendor_config.progress_patterns |

---

## Phase 4: State Machine Centralization

**Goal**: Centralize state transition logic to eliminate scattered hardcoded state strings.

### 4.1 Create State Enums

**New file**: `api/app/state.py`

```python
from enum import Enum

class NodeState(str, Enum):
    UNDEPLOYED = "undeployed"
    PENDING = "pending"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"

class DesiredState(str, Enum):
    STOPPED = "stopped"
    RUNNING = "running"

class LinkState(str, Enum):
    UNKNOWN = "unknown"
    PENDING = "pending"
    CREATING = "creating"
    UP = "up"
    DOWN = "down"
    ERROR = "error"

class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
```

### 4.2 Create State Machine Service

**New file**: `api/app/services/state_machine.py`

```python
from api.app.state import NodeState, DesiredState, LinkState

class NodeStateMachine:
    """Centralized state transition logic for nodes"""

    VALID_TRANSITIONS: dict[NodeState, set[NodeState]] = {
        NodeState.UNDEPLOYED: {NodeState.PENDING},
        NodeState.PENDING: {NodeState.STARTING, NodeState.ERROR},
        NodeState.STARTING: {NodeState.RUNNING, NodeState.ERROR},
        NodeState.RUNNING: {NodeState.STOPPING, NodeState.ERROR},
        NodeState.STOPPING: {NodeState.STOPPED, NodeState.ERROR},
        NodeState.STOPPED: {NodeState.PENDING, NodeState.UNDEPLOYED},
        NodeState.ERROR: {NodeState.PENDING, NodeState.STOPPED, NodeState.UNDEPLOYED},
    }

    @classmethod
    def can_transition(cls, current: NodeState, target: NodeState) -> bool:
        """Check if transition is valid"""
        return target in cls.VALID_TRANSITIONS.get(current, set())

    @classmethod
    def get_transition_for_desired(
        cls,
        current: NodeState,
        desired: DesiredState,
    ) -> NodeState | None:
        """Get the next state to move toward desired state"""
        if desired == DesiredState.RUNNING:
            if current == NodeState.STOPPED:
                return NodeState.PENDING
            if current == NodeState.PENDING:
                return NodeState.STARTING
            if current == NodeState.ERROR:
                return NodeState.PENDING
        elif desired == DesiredState.STOPPED:
            if current == NodeState.RUNNING:
                return NodeState.STOPPING
        return None

    @classmethod
    def is_terminal(cls, state: NodeState) -> bool:
        """Check if state is terminal (no automatic transitions)"""
        return state in {NodeState.RUNNING, NodeState.STOPPED, NodeState.ERROR}


class LinkStateMachine:
    """Centralized state transition logic for links"""

    VALID_TRANSITIONS: dict[LinkState, set[LinkState]] = {
        LinkState.UNKNOWN: {LinkState.PENDING, LinkState.UP, LinkState.DOWN},
        LinkState.PENDING: {LinkState.CREATING, LinkState.ERROR},
        LinkState.CREATING: {LinkState.UP, LinkState.ERROR},
        LinkState.UP: {LinkState.DOWN, LinkState.ERROR},
        LinkState.DOWN: {LinkState.PENDING, LinkState.UP},
        LinkState.ERROR: {LinkState.PENDING, LinkState.DOWN},
    }

    @classmethod
    def can_transition(cls, current: LinkState, target: LinkState) -> bool:
        return target in cls.VALID_TRANSITIONS.get(current, set())
```

### 4.3 Update Task Files to Use State Machine

**Modify**: `api/app/tasks/jobs.py`

Before (lines 1208-1216):
```python
if ns.desired_state == "stopped" and ns.actual_state == "running":
    ns.actual_state = "stopping"
elif ns.desired_state == "running" and ns.actual_state in ("stopped", "error"):
    ns.actual_state = "starting"
```

After:
```python
from api.app.services.state_machine import NodeStateMachine
from api.app.state import NodeState, DesiredState

current = NodeState(ns.actual_state)
desired = DesiredState(ns.desired_state)
next_state = NodeStateMachine.get_transition_for_desired(current, desired)
if next_state:
    ns.actual_state = next_state.value
```

### 4.4 Update Reconciliation

**Modify**: `api/app/tasks/reconciliation.py`

Replace string comparisons with enum usage:
```python
from api.app.state import NodeState

# Before
if node_state.actual_state == "pending":
    ...

# After
if node_state.actual_state == NodeState.PENDING.value:
    ...
# Or better, store enum directly in model
```

### 4.5 Files to Modify

| File | Changes |
|------|---------|
| `api/app/state.py` | NEW - State enums |
| `api/app/services/state_machine.py` | NEW - Transition logic |
| `api/app/tasks/jobs.py` | Use state machine |
| `api/app/tasks/reconciliation.py` | Use state enums |
| `api/app/tasks/state_enforcement.py` | Use state machine |
| `api/app/tasks/job_health.py` | Use state enums |
| `api/app/routers/labs.py` | Use state enums |

---

## Phase 5: Database Schema Hardening

**Goal**: Add database-level validation and missing indexes.

### 5.1 Add CHECK Constraints

**New migration**: `api/alembic/versions/017_add_state_constraints.py`

```python
def upgrade():
    # Node state constraints
    op.create_check_constraint(
        'ck_node_states_actual_state',
        'node_states',
        "actual_state IN ('undeployed', 'pending', 'starting', 'running', 'stopping', 'stopped', 'error')"
    )
    op.create_check_constraint(
        'ck_node_states_desired_state',
        'node_states',
        "desired_state IN ('stopped', 'running')"
    )

    # Link state constraints
    op.create_check_constraint(
        'ck_link_states_actual_state',
        'link_states',
        "actual_state IN ('unknown', 'pending', 'creating', 'up', 'down', 'error')"
    )

    # Job status constraints
    op.create_check_constraint(
        'ck_jobs_status',
        'jobs',
        "status IN ('pending', 'running', 'completed', 'failed')"
    )
```

### 5.2 Add Missing Indexes

**New migration**: `api/alembic/versions/018_add_performance_indexes.py`

```python
def upgrade():
    # Reconciliation query optimization
    op.create_index(
        'ix_node_states_lab_actual',
        'node_states',
        ['lab_id', 'actual_state']
    )
    op.create_index(
        'ix_node_states_lab_desired',
        'node_states',
        ['lab_id', 'desired_state']
    )

    # Link state queries
    op.create_index(
        'ix_link_states_lab_actual',
        'link_states',
        ['lab_id', 'actual_state']
    )

    # Job health checks
    op.create_index(
        'ix_jobs_status',
        'jobs',
        ['status']
    )
    op.create_index(
        'ix_jobs_lab_status',
        'jobs',
        ['lab_id', 'status']
    )

    # Host health checks
    op.create_index(
        'ix_hosts_status',
        'hosts',
        ['status']
    )

    # Image sync queries
    op.create_index(
        'ix_image_hosts_status',
        'image_hosts',
        ['image_id', 'host_id', 'status']
    )
```

### 5.3 Files to Create

| File | Purpose |
|------|---------|
| `api/alembic/versions/017_add_state_constraints.py` | State validation |
| `api/alembic/versions/018_add_performance_indexes.py` | Query optimization |

---

## Implementation Status

### Phase 1: Network Backend Abstraction - IMPLEMENTED

**Completed:**
- [x] Created `agent/network/backend.py` - NetworkBackend and OverlayBackend abstract interfaces
- [x] Created `agent/network/backends/` package with OVSBackend implementation
- [x] Created `agent/network/backend_registry.py` - Factory for backend selection
- [x] Updated `agent/config.py` - Added `network_backend` setting
- [x] Updated `agent/providers/docker.py` - Added `network_backend` property
- [x] Updated `agent/main.py` - Link endpoints now use backend abstraction
- [x] Updated `agent/network/__init__.py` - Exports new backend components

**Files Created:**
- `agent/network/backend.py` - Abstract interfaces (NetworkBackend, OverlayBackend)
- `agent/network/backend_registry.py` - get_network_backend(), get_overlay_backend()
- `agent/network/backends/__init__.py` - Package init
- `agent/network/backends/ovs.py` - OVSBackend wrapping existing OVS code

**Files Modified:**
- `agent/config.py` - Added network_backend setting
- `agent/providers/docker.py` - Added network_backend property
- `agent/main.py` - Updated create_link, delete_link endpoints
- `agent/network/__init__.py` - Added backend exports

### Phase 2: Provider Interface Enforcement - IMPLEMENTED

**Completed:**
- [x] Added missing abstract methods to Provider base class (get_node_identifier, list_nodes, extract_config, extract_all_configs, get_node_logs, exec_command)
- [x] Added ExecResult dataclass to base.py
- [x] Enhanced NodeInfo dataclass with lab_id, kind, created_at fields
- [x] Implemented all new methods in DockerProvider
- [x] Implemented stubs for all new methods in LibvirtProvider (with appropriate warnings for unsupported operations)
- [x] Updated main.py extract_configs endpoint to use provider.extract_all_configs()

**Files Modified:**
- `agent/providers/base.py` - Added abstract methods and ExecResult
- `agent/providers/docker.py` - Implemented new Provider interface methods
- `agent/providers/libvirt.py` - Added stub implementations
- `agent/main.py` - Updated extract_configs to use Provider interface

**Note:** Some direct Docker client access in main.py remains for internal operations (interface fixing, raw container access). These are intentionally left as-is since they are Docker-specific administrative functions.

### Phase 3: Vendor Plugin Activation - IMPLEMENTED

**Completed:**
- [x] Added new data-driven fields to VendorConfig (platform_detection_race, interface_wait_env_var, interface_wait_script, progress_patterns, vendor_options)
- [x] Updated cEOS config in VENDOR_CONFIGS to use new fields
- [x] Replaced hardcoded is_ceos_kind() check in docker.py with data-driven runtime_config lookup for interface wait handling
- [x] Updated readiness.py to use vendor config.progress_patterns instead of hardcoded CEOS_PROGRESS_PATTERNS
- [x] Removed is_ceos_kind import from readiness.py

**Files Modified:**
- `agent/vendors.py` - Added new VendorConfig fields and updated cEOS config
- `agent/providers/docker.py` - Replaced hardcoded cEOS check with data-driven config
- `agent/readiness.py` - Now uses vendor config for progress patterns

**Note:** Plugin loading infrastructure (discover_plugins) is existing but not yet activated. The data-driven vendor config fields provide the foundation for future plugin-based vendor definitions.

### Phase 4: State Machine Centralization - IMPLEMENTED

**Completed:**
- [x] Created `api/app/state.py` - Centralized state enums (LabState, NodeActualState, NodeDesiredState, LinkActualState, LinkDesiredState, JobStatus, HostStatus, CarrierState, ImageSyncStatus, VxlanTunnelStatus)
- [x] Created `api/app/services/state_machine.py` - NodeStateMachine, LinkStateMachine, LabStateMachine with valid transitions and helper methods
- [x] Updated `api/app/tasks/jobs.py` - Replaced hardcoded state strings with enum values, uses state machine for transitions
- [x] Updated `api/app/tasks/reconciliation.py` - Uses LabStateMachine for lab state aggregation, uses enums throughout
- [x] Updated `api/app/tasks/state_enforcement.py` - Uses NodeStateMachine.get_enforcement_action() for determining actions
- [x] Updated `api/app/tasks/job_health.py` - Uses enums for status comparisons

**Files Created:**
- `api/app/state.py` - All state enums with string values for database compatibility
- `api/app/services/state_machine.py` - State transition logic with VALID_TRANSITIONS, helper methods

**Files Modified:**
- `api/app/tasks/jobs.py` - ~50 state string replacements with enums
- `api/app/tasks/reconciliation.py` - Lab state computation now uses LabStateMachine
- `api/app/tasks/state_enforcement.py` - Enforcement action determination uses NodeStateMachine
- `api/app/tasks/job_health.py` - Status comparisons use enums

**Key Features:**
- Enums inherit from (str, Enum) for database string compatibility
- State machines provide `can_transition()`, `get_transition_for_desired()`, `matches_desired()`, `needs_enforcement()`, and `get_enforcement_action()` methods
- LabStateMachine.compute_lab_state() centralizes lab state aggregation logic
- All state comparisons now use `EnumType.VALUE.value` pattern for type safety

---

## Implementation Order

### Week 1-2: Phase 1 (Network Backend)
- Create backend interface and registry
- Implement OVS backend wrapping existing code
- Update one endpoint as proof of concept
- Test with existing OVS setup

### Week 3: Phase 1 Completion + Phase 2 Start
- Migrate remaining endpoints to backend
- Remove deprecated manager code
- Add missing Provider abstract methods
- Implement in DockerProvider

### Week 4: Phase 2 Completion + Phase 3
- Fix all main.py violations
- Implement LibvirtProvider stubs
- Add VendorConfig fields
- Update cEOS to use data-driven config

### Week 5: Phase 3 Completion + Phase 4
- Remove hardcoded vendor checks
- Wire up plugin loading
- Create state enums and machine
- Update one task file as proof of concept

### Week 6: Phase 4 Completion + Phase 5
- Migrate remaining task files to state machine
- Create database migrations
- Test migrations on staging
- Deploy

---

## Risk Mitigation

### High-Risk Changes

| Change | Risk | Mitigation |
|--------|------|------------|
| Network backend swap | Link creation breaks | Feature flag to fall back to old code |
| Provider interface changes | LibvirtProvider fails | Already non-functional; no regression |
| Vendor config changes | cEOS boot fails | Extensive testing on cEOS containers |
| State enum migration | State mismatches | Run old and new code in parallel initially |

### Testing Strategy

1. **Unit tests** for new abstractions (backend, state machine)
2. **Integration tests** for provider interface compliance
3. **End-to-end tests** for link creation with different backends
4. **Canary deployment** with feature flags enabled gradually

### Rollback Plan

Each phase has feature flags for rollback:
- `use_new_network_backend: bool = False`
- `use_new_provider_interface: bool = False`
- `use_vendor_plugins: bool = False`
- `use_state_machine: bool = False`

Set flags to False to revert to old behavior immediately.

---

## Success Criteria

### Phase 1 Complete When:
- [ ] New provider (e.g., Podman) can be added without network code changes
- [ ] Linux bridge backend can be implemented without touching providers

### Phase 2 Complete When:
- [ ] LibvirtProvider passes all abstract method checks
- [ ] main.py has zero direct Docker client access

### Phase 3 Complete When:
- [ ] New vendor added via plugin without touching vendors.py
- [ ] No `is_ceos_kind()` calls remain in providers or readiness

### Phase 4 Complete When:
- [ ] Zero hardcoded state strings outside state.py
- [ ] State transitions validated at single location

### Phase 5 Complete When:
- [ ] Database rejects invalid state values
- [ ] Reconciliation queries use indexes (EXPLAIN shows index scans)
