# State Management Overhaul — Implementation Plan

**Created**: 2026-02-07
**Status**: IN PROGRESS — Phase 0 + Phase 1 + Phase 2 + Phase 3 Complete
**Scope**: States, runtime control, transitions, placement enforcement, parallelization, logging, UI accuracy

## Design Principles

1. **`actual_state` reflects reality** — always truthful about what the container is doing
2. **`desired_state` is user intent** — only `stopped` or `running`
3. **Error is an end state** — not part of normal transitions; UI suppresses transient errors
4. **Explicit placement is inviolable** — if a host is specified, the node runs there or errors
5. **Single enforcement path** — one system detects and corrects mismatches
6. **No containerlab dependency** — we own the full container lifecycle (Docker SDK + OVS)
7. **Realtime canvas is primary** — individual node operations are first-class; bulk ops are convenience wrappers
8. **Parallel where safe** — cross-agent operations run concurrently
9. **Structured logging** — every state transition produces a machine-parseable log entry

---

## Decisions Summary

| # | Issue | Decision |
|---|-------|----------|
| 1 | Dual enforcement paths (race conditions) | Unify: reconciliation = read-only, enforcement = sole corrective path |
| 2 | Two API paths for start/stop | Deprecate old endpoint, redirect to new with same guards |
| 3 | Bulk start/stop blocks on any transitional | Start/stop what's ready, skip transitional, return counts |
| 4 | Placement migration gaps | Resource-check-first: verify new host before touching old container |
| 5 | 6+ duplicate NodeStateEntry types | Single types file + Pick<> |
| 6 | sync_node_lifecycle is 1,400 lines | Full NodeLifecycleManager class decomposition |
| 7 | Two parallel state systems (nodeStates + runtimeStates) | Derive runtimeStates via useMemo from nodeStates |
| 8 | Error messages overwritten by enforcement | Preserve original error, append enforcement context |
| 9 | State machine only 10% tested | Exhaustive parametrized tests for all transitions |
| 10 | Zero concurrency tests | Full concurrency test suite |
| 11 | No bulk operation backend tests | TDD: write tests first, then implement |
| 12 | No end-to-end enforcement test | Full integration test for enforce_node_state |
| 13 | Full topology redeploy for single node restart | Per-node container lifecycle (Docker SDK + OVS veth recreation); no containerlab |
| 14 | Sequential operations | Parallelize agent queries and cross-agent deploys |
| 15 | Logging gaps | Structured logging with extra={} for all state transitions |
| 16 | UI error flash on transient failures | Backend-informed grace period (will_retry flag) |

---

## Naming Conventions — Aligning with New Mental Model

Since this is greenfield, we rename functions/classes to match the per-node, realtime canvas approach. No legacy constructs preserved.

| Current Name | New Name | Rationale |
|---|---|---|
| `run_node_reconcile()` | `sync_node_lifecycle()` | Not "reconciling" — enforcing desired state per-node |
| `NodeReconciler` class | `NodeLifecycleManager` | Manages create/start/stop/destroy lifecycle, not reconciliation |
| `run_agent_job()` | `run_lab_deploy()` | Clarifies this is the bulk lab-level deploy (convenience wrapper) |
| `run_multihost_deploy()` | `run_lab_deploy_multihost()` | Consistent with above |
| `run_multihost_destroy()` | `run_lab_destroy_multihost()` | Consistent |
| `deploy_to_agent()` | `create_node_on_agent()` / `start_node_on_agent()` | Per-node operations, not topology deploys |
| `deploy_node_immediately()` | `request_node_start()` | User-intent language, not implementation |
| `destroy_node_immediately()` | `request_node_destroy()` | Consistent |
| `container_action()` | `node_action_on_agent()` | Works for both Docker and libvirt nodes |
| `refresh_states_from_agents()` | `poll_actual_states()` | Clearer — polls reality from agents |
| `_do_reconcile_lab()` | `sync_lab_state()` | Now read-only: syncs DB state with agent reality |
| `_reconcile_single_lab()` | `sync_single_lab()` | Consistent |
| `state_enforcement_monitor()` | `state_enforcement_loop()` | Clearer that it's the main loop |
| `enforce_lab_states()` | `enforce_all_labs()` | Clearer scope |
| Job action `sync:node:{id}` | `lifecycle:node:{id}` | Matches class name |
| Job action `sync:lab:{ids}` | `lifecycle:bulk:{ids}` | Matches |
| Job action `sync:agent:{agent}:{ids}` | `lifecycle:agent:{agent}:{ids}` | Matches |
| `node_action` endpoint | Remove entirely | Replaced by `set_node_desired_state` |

**Frontend renames:**
| Current Name | New Name | Rationale |
|---|---|---|
| `runtimeStates` (eliminated) | Derived via useMemo | No longer a separate state object |
| `handleWSNodeStateChange` | `handleNodeStateUpdate` | Not WS-specific — handles any state update |
| `handleUpdateStatus` | `handleNodeAction` | Matches user intent (start/stop action) |
| `loadNodeStates` | `fetchNodeStates` | Standard verb for API calls |
| `RuntimeStatus` (utils/status.ts) | `LabStatus` | Rename to clarify this is lab-level, not node-level |
| `RuntimeStatus` (RuntimeControl.tsx) | Keep as `RuntimeStatus` | This IS node-level display status |
| `isOperationPending` | `isNodeBusy` | Simpler, matches UX concept |

**File renames:**
| Current | New | Rationale |
|---|---|---|
| `api/app/tasks/jobs.py` | Split: `api/app/tasks/lab_lifecycle.py` + `api/app/tasks/node_lifecycle.py` | Separate lab-level and node-level concerns |
| `api/app/tasks/live_nodes.py` | Merge into `node_lifecycle.py` | Consolidate node lifecycle logic |
| `api/app/tasks/state_enforcement.py` | Keep (name is good) | Already matches the new model |
| `api/app/tasks/reconciliation.py` | `api/app/tasks/state_sync.py` | Now read-only — syncs state, doesn't reconcile |

---

## Architectural Direction: Realtime Canvas + No Containerlab

### Current State
- Deploy flow: API builds full JSON topology → sends to agent → agent delegates to containerlab → containerlab creates ALL containers + networking
- Starting a single node requires redeploying the entire topology on that agent
- `lab_up` / `lab_down` are the primary lifecycle endpoints
- Containerlab manages container creation, network interface provisioning, and startup configs

### Target State
- **Per-node lifecycle**: API tells agent to create/start/stop/destroy ONE container at a time
- **Agent owns Docker directly**: Docker SDK for container create/start/stop/remove
- **Agent owns OVS networking directly**: DockerOVSPlugin for veth pair creation, VLAN assignment, hot_connect
- **No topology-level deploy**: Agent never receives or processes a full topology
- **Individual node operations are atomic**: Create container → attach OVS interfaces → apply startup config → start container
- **Bulk operations are convenience**: "Start All" iterates over individual node starts; "Deploy Lab" iterates over individual node deploys
- **Realtime canvas drives everything**: User drags node onto canvas → node is created in DB → if desired_state=running, enforcement starts it

### Migration Strategy
This is a large change. We phase it:
1. **Phase 0-2**: Refactor state management, enforcement, and NodeLifecycleManager WITHOUT changing the deploy mechanism. NodeLifecycleManager still calls deploy_to_agent() but is internally clean.
2. **Phase 3**: Replace deploy_to_agent() with per-node Docker SDK operations. This is where containerlab is removed from the deploy path.
3. The existing containerlab-based deploy remains as a fallback until per-node lifecycle is proven stable.

---

## Implementation Phases

### Phase 0: Foundation — Tests Before Refactoring
> Write comprehensive tests FIRST to create a safety net for all subsequent changes.

- [x] **0.1** Exhaustive state machine tests (Issue #9)
  - File: `api/tests/test_state_machine.py`
  - Parametrized tests for ALL valid transitions in NodeStateMachine
  - Parametrized tests for key invalid transitions (ensure they return False)
  - Tests for EXITED state (currently zero coverage)
  - Tests for LabStateMachine.compute_lab_state() with all combinations:
    - All running → running
    - All stopped → stopped
    - Any error → error
    - Any transitional → appropriate transitional state
    - Mixed running/stopped → running (or whatever the current behavior is)
  - Tests for is_terminal, CONTAINER_EXISTS_STATES, STOPPED_EQUIVALENT_STATES
  - Tests for get_transition_for_desired() with all state/desired combinations
  - Tests for get_enforcement_action() with all state/desired combinations
  - Tests for needs_enforcement() during transitional states (should return False)

- [x] **0.2** Concurrency test suite (Issue #10)
  - File: `api/tests/test_concurrency.py` (new)
  - Deploy lock tests: acquire, contend (second acquire fails), expire (TTL), release
  - API guard tests:
    - HTTP 409 when starting a node that is currently stopping
    - HTTP 409 when stopping a node that is currently starting
    - HTTP 409 when starting a node that is already starting (no repeated commands)
    - HTTP 409 when stopping a node that is already stopping (no repeated commands)
  - Simulated dual-enforcement: two enforcement cycles on same node — only one should create a job
  - Concurrent bulk + individual: bulk start while individual start in progress on one node

- [x] **0.3** End-to-end enforcement test (Issue #12)
  - File: `api/tests/test_state_enforcement.py` (extend)
  - Test enforce_node_state() full flow:
    - Create node with desired=running, actual=stopped
    - Call enforce_node_state()
    - Verify: job created with action matching sync:node:{id}
    - Verify: enforcement_attempts incremented
    - Verify: Redis cooldown set
  - Test enforcement skipped during backoff period
  - Test enforcement stops after max retries:
    - Verify: actual_state set to "error"
    - Verify: error_message preserves original error (Issue #8)
    - Verify: enforcement_failed_at is set
  - Test enforcement with explicit placement:
    - Node.host_id set to offline agent → error state, not fallback to another agent
  - Test enforcement resets on manual intervention:
    - User changes desired_state → enforcement_attempts reset to 0

- [x] **0.4** Bulk operation tests — TDD (Issue #11)
  - File: `api/tests/test_bulk_operations.py` (new)
  - These tests define the NEW behavior from Issue #3. They will FAIL initially.
  - Test: start-all with all stopped → all nodes get desired_state=running
  - Test: start-all with mixed states:
    - stopped nodes → desired_state=running
    - running nodes → unchanged (already in desired state)
    - starting nodes → skipped (transitional)
    - stopping nodes → skipped (transitional)
    - error nodes → desired_state=running (enforcement will retry)
    - undeployed nodes → desired_state=running
  - Test: stop-all with mixed states:
    - running nodes → desired_state=stopped
    - stopped nodes → unchanged
    - starting nodes → skipped (transitional)
    - stopping nodes → skipped (transitional)
  - Test: response includes counts:
    ```json
    { "affected": 15, "skipped_transitional": 2, "already_in_state": 3 }
    ```
  - Test: no-op when all already in desired state → affected=0
  - Test: individual sync jobs created per affected node (or batched appropriately)

### Phase 1: Backend Architecture — Single Enforcement Path
> Establish clean separation: reconciliation reads, enforcement writes.

- [x] **1.1** Remove enforcement from reconciliation (Issue #1)
  - File: `api/app/tasks/reconciliation.py`
  - Remove lines ~1299-1364 (enforcement section in `_do_reconcile_lab`)
  - Reconciliation becomes purely read-only:
    - Updates actual_state from agent container status ✓
    - Updates is_ready from boot checks ✓
    - Updates lab state aggregation ✓
    - Updates link states ✓
    - Cleans up orphan containers ✓
    - Does NOT create enforcement jobs ✗ (removed)
    - Does NOT call sync_node_lifecycle ✗ (removed)
  - State enforcement monitor (`state_enforcement.py`) becomes the sole corrective path
  - Verify: enforcement still detects mismatches within its cycle (max 60s detection delay)
  - Verify: reconciliation still correctly updates actual_state so enforcement sees current reality

- [x] **1.2** Deprecate old node action endpoint (Issue #2)
  - File: `api/app/routers/jobs.py`
  - `POST /labs/{lab_id}/nodes/{node}/{action}` becomes thin adapter:
    ```python
    @router.post("/labs/{lab_id}/nodes/{node}/{action}")
    async def node_action(lab_id, node, action, ...):
        # Translate action to desired_state
        state = "running" if action == "start" else "stopped"
        # Delegate to the guarded endpoint logic
        return await set_node_desired_state(lab_id, node, state, ...)
    ```
  - Add `Deprecation` header to response
  - All guards now apply uniformly:
    - Transitional state rejection (no start during stopping, no stop during starting)
    - Conflicting job check
    - Error retry handling (reset enforcement_attempts, clear error_message)
    - Per-node provider detection (docker vs libvirt)

- [x] **1.3** Update bulk start/stop behavior (Issue #3)
  - File: `api/app/routers/labs.py`
  - `PUT /labs/{lab_id}/nodes/desired-state` changes from all-or-nothing to selective:
  - For each node in the lab:
    - If node is in target state → skip (already_in_state++)
    - If node is in transitional state (starting/stopping/pending) → skip (skipped_transitional++)
    - Otherwise → set desired_state, create sync job (affected++)
  - Return response: `{ affected, skipped_transitional, already_in_state }`
  - This makes Phase 0.4 TDD tests pass

- [x] **1.4** Preserve error messages on enforcement failure (Issue #8)
  - File: `api/app/tasks/state_enforcement.py`
  - When max retries exhausted (line ~299), change message to:
    ```python
    error_message = (
        f"State enforcement failed after {attempts} attempts. "
        f"Last error: {node_state.error_message or 'unknown'}"
    )
    ```
  - When enforcement creates a new job, do NOT clear error_message prematurely
  - Error_message should only be cleared when the actual operation succeeds

### Phase 2: NodeLifecycleManager Decomposition
> Break the 1,400-line function into a testable, maintainable class.

- [x] **2.1** Design and implement NodeLifecycleManager class (Issue #6)
  - File: `api/app/tasks/node_lifecycle.py` (new)
  - Class structure:
    ```python
    class NodeLifecycleManager:
        """Per-node lifecycle orchestrator.

        Handles the lifecycle of individual nodes: deploy, start, stop, destroy.
        Each operation is independent — no full topology deploys.
        """

        def __init__(self, session, lab, job, node_ids):
            self.session = session
            self.lab = lab
            self.job = job
            self.node_ids = node_ids

        async def execute(self) -> LifecycleResult:
            """Main orchestrator — calls phases in order."""
            nodes = await self._load_and_validate()
            if not nodes:
                return LifecycleResult.noop()

            await self._set_transitional_states(nodes)

            agent_groups = await self._resolve_agents(nodes)

            for agent, agent_nodes in agent_groups.items():
                nodes_to_deploy = [n for n in agent_nodes if self._needs_deploy(n)]
                nodes_to_start = [n for n in agent_nodes if self._needs_start(n)]
                nodes_to_stop = [n for n in agent_nodes if self._needs_stop(n)]

                if nodes_to_deploy or nodes_to_start:
                    await self._check_resources(agent, nodes_to_deploy + nodes_to_start)
                    await self._handle_migration(agent, nodes_to_deploy + nodes_to_start)
                    await self._check_images(agent, nodes_to_deploy + nodes_to_start)

                if nodes_to_deploy:
                    await self._deploy_nodes(agent, nodes_to_deploy)

                if nodes_to_start:
                    await self._start_nodes(agent, nodes_to_start)

                if nodes_to_stop:
                    await self._stop_nodes(agent, nodes_to_stop)

            await self._post_operation_cleanup()

        async def _load_and_validate(self) -> list[NodeState]:
            """Load node states, fix placeholders, early-exit if all in desired state."""

        async def _set_transitional_states(self, nodes: list[NodeState]):
            """Set starting/stopping/pending BEFORE agent lookup. Broadcast via WS."""

        async def _resolve_agents(self, nodes) -> dict[Host, list[NodeState]]:
            """Determine target agent per node. Priority: Node.host_id > NodePlacement > lab.agent_id.
            Spawn sub-jobs for nodes on other agents (cross-agent parallelism).
            Fail fast if explicit host is offline."""

        async def _check_resources(self, agent, nodes):
            """Pre-deploy resource validation.
            MUST run BEFORE _handle_migration.
            If insufficient: set error state with clear message, do NOT touch old container."""

        async def _handle_migration(self, agent, nodes):
            """Detect misplaced containers. Stop/remove old container on wrong host.
            Only runs AFTER _check_resources confirms new host can accept."""

        async def _check_images(self, agent, nodes):
            """Verify images available on target agent."""

        async def _deploy_nodes(self, agent, nodes):
            """Deploy undeployed/pending nodes. Per-node container creation via Docker SDK."""

        async def _start_nodes(self, agent, nodes):
            """Start stopped containers. docker start + OVS veth recreation.
            Does NOT redeploy the full topology — operates on individual containers."""

        async def _stop_nodes(self, agent, nodes):
            """Stop running containers via docker stop."""

        async def _post_operation_cleanup(self):
            """Update placements, capture management IPs, orchestrate links."""
    ```
  - `sync_node_lifecycle()` in jobs.py becomes:
    ```python
    async def sync_node_lifecycle(job_id, lab_id, node_ids, ...):
        manager = NodeLifecycleManager(session, lab, job, node_ids)
        await manager.execute()
    ```

- [x] **2.2** Resource-check-first migration (Issue #4)
  - Implement within `NodeLifecycleManager._check_resources()` and `_handle_migration()`
  - Order: _check_resources() is called BEFORE _handle_migration() (see execute() above)
  - If resources insufficient on target host:
    - Set actual_state = "error" for affected nodes
    - Set error_message = "Insufficient resources on {agent_name}: requires {needed}MB RAM, {available}MB available"
    - Do NOT touch the old container (leave it in place on old host)
    - Broadcast error state to UI
    - Remove affected nodes from further processing
  - If resources sufficient:
    - _handle_migration() proceeds: stop old container, remove from old host
    - Then _deploy_nodes() or _start_nodes() creates on new host

- [x] **2.3** Batch-load queries to fix N+1 patterns (Issue #14 partial)
  - Within NodeLifecycleManager._load_and_validate() and _resolve_agents():
    ```python
    # Batch load all Node definitions for this lab
    all_nodes = {n.id: n for n in session.query(Node).filter(Node.lab_id == lab_id).all()}

    # Batch load all NodePlacements for this lab
    all_placements = {p.node_name: p for p in
        session.query(NodePlacement).filter(NodePlacement.lab_id == lab_id).all()}
    ```
  - Pass these maps to phase methods instead of querying per-node
  - Estimated reduction: from ~3N+2 queries to ~5 batch queries

- [x] **2.4** Write tests for NodeLifecycleManager (extends Issue #12)
  - File: `api/tests/test_node_lifecycle.py` (new)
  - Test each phase method independently with mocked dependencies:
    - _load_and_validate: returns empty when all in desired state
    - _set_transitional_states: stopped→starting, running→stopping, undeployed→pending
    - _resolve_agents: explicit host_id honored, offline host → error
    - _check_resources: insufficient → error state, old container untouched
    - _handle_migration: removes container from old host after resource check
    - _start_nodes: calls docker start + veth recreation (no full redeploy)
    - _stop_nodes: calls docker stop per node
  - Test the full execute() orchestration:
    - Mixed node states: some need deploy, some need start, some need stop
    - Multi-agent: spawns sub-jobs for other agents
    - Error in one phase doesn't block other phases
    - Placement updated after successful operations

### Phase 3: Agent Per-Node Lifecycle (No Containerlab)
> Replace topology-level deploy with per-node Docker SDK operations.

- [x] **3.1** Implement per-node container create/start/stop/destroy on agent ✅
  - File: `agent/providers/docker.py`, `agent/providers/base.py`
  - `create_node()`: Builds TopologyNode, validates image, sets up cEOS dirs, creates/attaches Docker networks, creates container (not started)
  - Enhanced `start_node()`: Added `repair_endpoints` + `fix_interfaces` kwargs for veth pair recreation after docker start
  - `destroy_node()`: Removes container, cleans VLAN allocations, deletes lab networks if last container

- [x] **3.2** Agent schemas for per-node lifecycle ✅
  - File: `agent/schemas.py`
  - CreateNodeRequest/Response, StartNodeRequest/Response, StopNodeResponse, DestroyNodeResponse

- [x] **3.3** New agent endpoints for per-node lifecycle ✅
  - File: `agent/main.py`
  - `POST /labs/{lab_id}/nodes/{node_name}/create` — create container (no start)
  - `POST /labs/{lab_id}/nodes/{node_name}/start` — start + veth repair + interface fix
  - `POST /labs/{lab_id}/nodes/{node_name}/stop` — stop container
  - `DELETE /labs/{lab_id}/nodes/{node_name}` — destroy container + cleanup

- [x] **3.4** API client methods + NodeLifecycleManager per-node paths ✅
  - File: `api/app/agent_client.py` — 4 new client functions
  - File: `api/app/tasks/node_lifecycle.py` — `_deploy_nodes()` and `_start_nodes()` dispatch to per-node or topology paths based on `per_node_lifecycle_enabled` feature flag
  - File: `api/app/config.py` — `per_node_lifecycle_enabled: bool = True`

- [x] **3.5** Parallelize agent status queries ✅
  - File: `api/app/tasks/reconciliation.py`
  - Replaced sequential agent loop with `asyncio.gather()` for parallel queries
  - Handles partial failures gracefully

- [x] **3.6** Tests ✅
  - File: `agent/tests/test_node_lifecycle.py` (new) — 11 agent-side tests
  - File: `api/tests/test_node_lifecycle.py` (extended) — 12 new per-node tests (62 total pass)
  - All existing tests updated with `per_node_lifecycle_enabled=False` for topology path testing

### Phase 4: Frontend State Management
> Clean up types, derive display state, suppress error flashing.

- [ ] **4.1** Create unified type definitions (Issue #5)
  - File: `web/src/types/nodeState.ts` (new)
  - Define canonical `NodeStateEntry` interface with ALL fields:
    ```typescript
    export interface NodeStateEntry {
      id: string;
      lab_id: string;
      node_id: string;
      node_name: string;
      node_definition_id?: string | null;
      desired_state: 'running' | 'stopped';
      actual_state: NodeActualState;
      error_message?: string | null;
      is_ready: boolean;
      boot_started_at?: string | null;
      stopping_started_at?: string | null;
      starting_started_at?: string | null;
      host_id?: string | null;
      host_name?: string | null;
      image_sync_status?: string | null;
      image_sync_message?: string | null;
      management_ip?: string | null;
      management_ips_json?: string | null;
      will_retry?: boolean;  // Issue #16: enforcement will retry
      enforcement_attempts?: number;
      created_at?: string;
      updated_at?: string;
    }

    export type NodeActualState =
      | 'undeployed' | 'pending' | 'starting' | 'running'
      | 'stopping' | 'stopped' | 'exited' | 'error';

    export type RuntimeStatus =
      | 'stopped' | 'booting' | 'running' | 'stopping' | 'error';
    ```
  - Export Pick<> type aliases for component-specific subsets:
    ```typescript
    export type CanvasNodeState = Pick<NodeStateEntry, 'id' | 'node_id' | 'node_name' | 'host_id' | 'host_name' | 'actual_state' | 'error_message'>;
    export type ConsoleNodeState = Pick<NodeStateEntry, 'id' | 'node_id' | 'actual_state' | 'is_ready'>;
    ```
  - Define `mapActualToRuntime()` pure function here (single source of truth for mapping)
  - Update all 6+ component files to import from this central file
  - Remove duplicate NodeStateData from canvasStore.ts
  - Remove duplicate RuntimeStatus from utils/status.ts (or align it for lab-level use)

- [ ] **4.2** Derive runtimeStates via useMemo (Issue #7)
  - File: `web/src/studio/StudioPage.tsx`
  - Remove `runtimeStates` as independent useState
  - Add useMemo:
    ```typescript
    const runtimeStates = useMemo(() => {
      const map: Record<string, RuntimeStatus> = {};
      for (const [id, state] of Object.entries(nodeStates)) {
        map[id] = mapActualToRuntime(state.actual_state, state.desired_state, state.will_retry);
      }
      return map;
    }, [nodeStates]);
    ```
  - For optimistic updates: write to nodeStates directly:
    ```typescript
    // Instead of: setRuntimeStates(prev => ({...prev, [id]: 'booting'}))
    // Do: setNodeStates(prev => ({...prev, [id]: {...prev[id], actual_state: 'starting'}}))
    ```
  - Remove duplicate mapping logic from handleWSNodeStateChange and loadNodeStates
  - The useMemo automatically produces the correct RuntimeStatus from the updated nodeStates

- [ ] **4.3** Implement error flash suppression (Issue #16)
  - Backend changes:
    - File: `api/app/services/broadcaster.py`
    - Add `will_retry` field to node state WS messages:
      ```python
      will_retry = (
          node_state.actual_state == "error"
          and node_state.enforcement_attempts < settings.state_enforcement_max_retries
          and node_state.enforcement_failed_at is None
      )
      ```
    - File: `api/app/schemas.py`
    - Add `will_retry: bool = False` to NodeStateOut
  - Frontend changes:
    - `mapActualToRuntime()` in `types/nodeState.ts`:
      ```typescript
      export function mapActualToRuntime(
        actual: NodeActualState,
        desired: 'running' | 'stopped',
        willRetry?: boolean
      ): RuntimeStatus {
        if (actual === 'error' && willRetry) return 'booting'; // Suppress transient error
        if (actual === 'error') return 'error';                // Enforcement gave up
        if (actual === 'running') return 'running';
        if (actual === 'stopping') return 'stopping';
        if (actual === 'starting' || actual === 'pending') {
          return desired === 'running' ? 'booting' : 'stopped';
        }
        if (actual === 'exited' || actual === 'stopped') return 'stopped';
        return 'stopped'; // undeployed
      }
      ```
    - Error toast: only show when `will_retry === false` and `actual_state === 'error'`
    - File: `web/src/studio/hooks/useLabStateWS.ts`
    - Pass through `will_retry` field from WS messages

- [ ] **4.4** Frontend tests for new behavior
  - Test mapActualToRuntime():
    - error + will_retry=true → 'booting' (not 'error')
    - error + will_retry=false → 'error'
    - All other state mappings unchanged
  - Test error toast suppression:
    - will_retry=true → no toast
    - will_retry transitions false → toast shown
  - Test optimistic updates write to nodeStates
  - Test runtimeStates is correctly derived via useMemo

### Phase 5: Structured Logging
> Comprehensive, machine-parseable logging for all state transitions.

- [ ] **5.1** Add structured state transition logging (Issue #15)
  - File: `api/app/tasks/node_lifecycle.py`
  - Log at EVERY state transition point with structured extra:
    ```python
    logger.info(
        "Node state transition",
        extra={
            "event": "node_state_transition",
            "lab_id": lab_id,
            "node_id": node_id,
            "node_name": node_name,
            "old_state": old_state,
            "new_state": new_state,
            "trigger": "user_action",  # or "enforcement", "reconciliation", "agent_response"
            "agent_id": agent_id,
            "job_id": job_id,
        }
    )
    ```
  - Key logging points (each produces a structured log entry):
    1. API endpoint: user sets desired_state (trigger="user_action", include user_id)
    2. Transitional state set in NodeLifecycleManager (trigger="reconciler")
    3. Agent operation result — start/stop/deploy success or failure (trigger="agent_response")
    4. Enforcement action triggered (trigger="enforcement", include attempt count)
    5. Reconciliation state update from agent reality (trigger="reconciliation")
    6. Error state set (trigger="error", include error_message in extra)
    7. Placement decision (trigger="placement", include decision_chain showing which source was used)

- [ ] **5.2** Add structured logging to agent communications
  - File: `api/app/agent_client.py`
  - Log request metadata (NOT full payloads — too large):
    ```python
    logger.info(
        "Agent request",
        extra={
            "event": "agent_request",
            "method": "start_node",  # or "stop_node", "create_node", "deploy", etc.
            "agent_id": agent.id,
            "agent_name": agent.name,
            "lab_id": lab_id,
            "node_name": node_name,
        }
    )
    ```
  - Log response with duration:
    ```python
    logger.info(
        "Agent response",
        extra={
            "event": "agent_response",
            "method": "start_node",
            "agent_id": agent.id,
            "lab_id": lab_id,
            "node_name": node_name,
            "status": "success",  # or "error"
            "duration_ms": elapsed_ms,
            "error": error_msg if failed else None,
        }
    )
    ```

- [ ] **5.3** Promote link state changes to info level
  - File: `api/app/tasks/reconciliation.py`
  - Change `logger.debug` (line ~1194) to `logger.info` for link state changes
  - Add structured extra:
    ```python
    logger.info(
        "Link state transition",
        extra={
            "event": "link_state_transition",
            "lab_id": lab_id,
            "link_id": link_id,
            "old_state": old_state,
            "new_state": new_state,
            "source_node": source_node_name,
            "target_node": target_node_name,
        }
    )
    ```

- [ ] **5.4** Add API endpoint entry logging
  - File: `api/app/routers/labs.py`
  - Log when user calls state-changing endpoints:
    ```python
    logger.info(
        "User state change request",
        extra={
            "event": "user_state_request",
            "user_id": current_user.id,
            "user_email": current_user.email,
            "lab_id": lab_id,
            "node_id": node_id,
            "requested_state": state,
            "endpoint": "set_node_desired_state",
        }
    )
    ```
  - Also log bulk operations with node count

- [ ] **5.5** Add agent-side structured logging
  - File: `agent/main.py`
  - Log container lifecycle events on the agent:
    ```python
    logger.info(
        "Container operation",
        extra={
            "event": "container_operation",
            "operation": "start",  # create, start, stop, destroy
            "lab_id": lab_id,
            "node_name": node_name,
            "container_id": container_id,
            "result": "success",
            "duration_ms": elapsed_ms,
        }
    )
    ```

---

## Execution Order and Dependencies

```
Phase 0 (Tests) ──────────────────────────────────────────────┐
  0.1 State machine tests                                     │
  0.2 Concurrency tests                                       │
  0.3 Enforcement e2e test                                    │
  0.4 Bulk operation tests (TDD — these fail initially)       │
                                                               │
Phase 1 (Backend Architecture) ───────────────────────────────┤
  1.1 Remove enforcement from reconciliation                  │
  1.2 Deprecate old endpoint                                  │  Phase 4 (Frontend) ──────────
  1.3 Bulk start/stop (makes 0.4 tests pass)                  │    4.1 Unified types
  1.4 Preserve error messages                                 │    4.2 Derived runtimeStates
                                                               │    4.3 Error flash suppression
Phase 2 (NodeLifecycleManager) ─────────────────────────────────────┤      (depends on 5.1 for
  2.1 Design + implement NodeLifecycleManager class                 │       will_retry field)
  2.2 Resource-check-first migration                          │    4.4 Frontend tests
  2.3 Batch-load N+1 fix                                      │
  2.4 NodeLifecycleManager tests                                    │  Phase 5 (Logging) ───────────
                                                               │    5.1 State transition logging
Phase 3 (Agent Per-Node Lifecycle) ───────────────────────────┘    5.2 Agent communication logging
  3.1 Per-node container create (Docker SDK)                       5.3 Link state log level
  3.2 Veth recreation (DockerOVSPlugin)                            5.4 API entry logging
  3.3 New agent endpoints                                          5.5 Agent-side logging
  3.4 Update NodeLifecycleManager to use per-node endpoints
  3.5 Parallelize agent queries
  3.6 Agent-side tests
```

**Dependency rules:**
- Phase 0 MUST complete before any code changes (safety net)
- Phase 1 can start immediately after Phase 0
- Phase 2 depends on Phase 1.1 (enforcement removed from reconciliation)
- Phase 3 depends on Phase 2.1 (NodeLifecycleManager exists to update)
- Phase 4 (frontend) can run in PARALLEL with Phases 1-3 (backend)
- Phase 4.3 (error flash) needs Phase 5.1 (will_retry field in WS messages) — implement 5.1 first
- Phase 5 (logging) can run in parallel with other phases, except 5.1 before 4.3

---

## Risk Assessment

| Phase | Risk | Mitigation |
|-------|------|------------|
| 0 (Tests) | Low — pure additions | Run existing tests to verify no regressions |
| 1.1 (Single enforcement) | Medium — changes enforcement timing | Monitor after deploy; max 60s delay for mismatch detection |
| 1.3 (Bulk selective) | Low — more permissive than current | TDD tests define exact behavior |
| 2.1 (NodeLifecycleManager) | High — refactoring core lifecycle | Phase 0 tests as safety net; incremental extraction |
| 3.1-3.2 (Per-node lifecycle) | Highest — new container management | Prototype veth recreation first; keep containerlab as fallback |
| 4.2 (Derived state) | Low — pure frontend refactor | TypeScript catches type errors |
| 5 (Logging) | Very low — additive only | No behavior changes |

---

## Success Criteria

1. **No error flash on normal start/stop** — starting a node shows stopped → starting → running with no error blip
2. **Explicit placement enforced** — a node assigned to host-A never starts on host-B; resource failures produce clear error messages without destroying the old container
3. **Start All starts what's ready** — transitional nodes are skipped, response shows counts
4. **No repeated commands** — clicking start on a starting node is rejected (409); clicking stop on a stopping node is rejected (409)
5. **Single-node restart is isolated** — starting one stopped node does NOT redeploy or disrupt other running nodes
6. **No containerlab in the deploy path** — agent creates/starts/stops containers directly via Docker SDK
7. **Every state transition is logged** — structured JSON logs with node_id, old/new state, trigger, agent_id
8. **All state transitions are tested** — 100% valid transition coverage, concurrency guards, bulk operations, enforcement end-to-end
9. **Single enforcement path** — only state_enforcement.py triggers corrective actions; reconciliation is read-only
10. **Realtime canvas operations are per-node** — no topology-level deploy required for individual nodes
