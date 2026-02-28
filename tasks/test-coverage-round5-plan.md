# Test Coverage Improvement Plan — Round 5+

## Current State (Feb 27, 2026)

### Overall Numbers

| Component | Source LOC | Test LOC | Test Files | Test Functions | Test:Source |
|-----------|-----------|----------|------------|----------------|-------------|
| API       | 59,971    | 68,514   | 154        | 2,885          | 1.14x       |
| Agent     | 40,325    | 29,240   | 99         | 1,327          | 0.73x       |
| Frontend  | 56,134    | 35,348   | 129        | 2,036          | 0.63x       |
| **Total** | **156,430** | **133,102** | **382** | **6,248**    | **0.85x**   |

### What Rounds 1-4 Delivered (1,542 tests across 63 files)

| Round | Tests | Files | Focus |
|-------|-------|-------|-------|
| 1     | 312   | 22    | Core workflows — deploy pipelines, link reconciliation, device resolver, console extractor |
| 2     | 488   | 18    | Services layer — catalog, broadcaster, config service, vendors, infrastructure router |
| 3     | 449   | 18    | Large routers — overlay, labs, interfaces, console, nodes, jobs |
| 4     | 173   | 5     | Advanced flows — multihost deploy/destroy, image upload/build, Docker plugin helpers |

### Key Wins from Rounds 1-4

- `routers/labs.py` — 0 → 152 tests (4 test files: full, configs, node_states, links_infra)
- `tasks/jobs.py` — 0 → 114 tests (3 test files: full, deploy, advanced)
- `routers/infrastructure.py` — 0 → 43 tests
- `services/catalog_service.py` — 0 → 48 tests
- `tasks/node_lifecycle_deploy.py` — 0 → 30 tests
- `routers/images.py` — 0 → 48 tests (upload/build flow)
- All 6 agent routers now have `_full.py` test files
- `vendors.py` expanded with 71 comprehensive tests
- Console infrastructure — 63 tests (extractor pipeline + session registry)
- Frontend: InfrastructurePage extended, AgentGraph, LinkTable, NotificationsPanel, ScenarioPanel, VerificationPanel

---

## Remaining Gaps (Ranked by Impact)

### Top 10 Gaps

| # | Component | File | LOC | Current Tests | Gap Score |
|---|-----------|------|-----|---------------|-----------|
| 1 | Agent | `network/docker_plugin.py` | 3,591 | 45 (~1.3%) | 3,541 |
| 2 | Agent | `providers/docker.py` | 3,674 | ~15 | 3,450 |
| 3 | API | `routers/images.py` | 3,287 | 48 (upload only) | 2,500 |
| 4 | Agent | `network/ovs.py` | 2,097 | 32 | 1,750 |
| 5 | Agent | `network/overlay.py` | 1,936 | 20 | 1,700 |
| 6 | API | `tasks/reconciliation.py` | 1,863 | ~1 | 1,850 |
| 7 | API | `routers/agents.py` | 1,565 | 0 | 1,565 |
| 8 | API | `schemas.py` | 1,530 | 0 | 1,530 |
| 9 | API | `image_store.py` | 1,479 | 0 | 1,479 |
| 10 | API | `tasks/node_lifecycle.py` | 1,495 | 0 | 1,495 |

### Additional Gaps

| Component | File | LOC | Current Tests | Notes |
|-----------|------|-----|---------------|-------|
| API | `routers/labs.py` | 3,118 | 152 | Good but 36 endpoints — bundle/layout/inventory still gaps |
| API | `tasks/node_lifecycle_deploy.py` | 1,431 | 30 | Needs depth expansion |
| API | `services/catalog_service.py` | 1,297 | 48 | Already started, could use more edge cases |
| API | `tasks/link_reconciliation.py` | 1,194 | ~20 | Needs depth |
| Agent | `routers/overlay.py` | 1,451 | 40 (full file) | Has tests but overlay.py source is huge |
| Agent | `routers/labs.py` | 853 | 0 | Agent-side lab endpoints untested |
| Agent | `providers/base.py` | 508 | 0 | Abstract base class — 14 methods |
| Agent | `network/local.py` | 694 | 5 | Same-host veth management |
| Agent | `network/cleanup.py` | 587 | 5 | Orphan cleanup |
| Agent | `routers/ovs_plugin.py` | 431 | 0 | OVS debug/status endpoints |
| Agent | `events/docker_events.py` | 286 | 0 | Docker event listener |
| Agent | `console/docker_exec.py` | 253 | 0 | Docker exec console |
| Agent | `providers/docker_networks.py` | 238 | 0 | Docker network CRUD |
| Frontend | `InfrastructurePage.tsx` | 3,147 | ~1 | Most complex page |
| Frontend | `ConsoleManager.tsx` | 789 | 0 | Terminal session management |
| Frontend | `studio/types.ts` | 345 | 0 | Core type definitions |
| Frontend | `VniLinkDetailPanel.tsx` | 273 | 0 | VXLAN detail view |
| Frontend | `NodeListPanel.tsx` | 204 | 0 | Node listing UI |

---

## Batch Plans

### Batch 5 — Agent Networking Depth (~80 tests)

**Target:** The 3 networking files that are the heart of the agent system.

**Files:**
1. `agent/network/docker_plugin.py` (3,591 LOC, 45 tests → target 90+)
   - `hot_connect()` — VLAN tag matching to create L2 links
   - `_discover_endpoint()` — veth-to-interface mapping (ifindex-based)
   - `_provision_lab_bridge()` — OVS bridge setup for lab
   - `_migrate_container_network()` — network migration on restart
   - `get_lab_status()` — lab network state reporting
   - `LabBridge` / `EndpointState` / `NetworkState` dataclass state machines
   - VLAN allocation and release logic

2. `agent/network/overlay.py` (1,936 LOC, 20 tests → target 50+)
   - `create_tunnel()` — VXLAN tunnel creation
   - `declare_state()` — convergence loop
   - `recover_link_tunnels()` — rebuild from OVS on startup
   - `cleanup_ovs_vxlan_orphans()` — orphan detection/deletion
   - `hot_connect()` / `hot_disconnect()` — overlay link management
   - `VxlanTunnel`, `Vtep`, `LinkTunnel`, `OverlayBridge` classes

3. `agent/network/ovs.py` (2,097 LOC, 32 tests → target 60+)
   - `VlanAllocator` — allocation, release, range management
   - `OVSNetworkManager` — bridge/port operations
   - `create_link()` / `teardown_link()` — L2 link lifecycle
   - `hot_connect()` / `hot_disconnect()` — live link operations
   - `get_ports_for_lab()` — port enumeration

**Testing approach:** Unit tests with mocked subprocess calls (ovs-vsctl, ip link). Use `unittest.mock.AsyncMock` for async methods. Test state transitions, error paths, recovery scenarios.

### Batch 6 — API Data & Background Tasks (~100 tests)

**Target:** Data layer and autonomous background processes.

**Files:**
1. `api/app/schemas.py` (1,530 LOC, 0 tests → target 30+)
   - Pydantic validation for all request/response models
   - Edge cases: optional fields, enum values, nested models
   - Serialization round-trip tests

2. `api/app/image_store.py` (1,479 LOC, 0 tests → target 25+)
   - `manifest.json` read/write operations
   - Device detection from image filenames
   - Path resolution and validation
   - Image metadata enrichment

3. `api/app/tasks/reconciliation.py` (1,863 LOC, ~1 test → target 30+)
   - State sync branches (running, stopped, error labs)
   - Stale node detection and cleanup
   - Transitional state threshold logic
   - Agent communication failure handling

4. `api/app/tasks/node_lifecycle.py` (1,495 LOC, 0 tests → target 20+)
   - 12-phase orchestration flow
   - Phase transition logic
   - Error handling and rollback
   - Broadcast point verification

**Testing approach:** Use existing conftest.py fixtures. Mock DB sessions, agent_client calls. Test each reconciliation branch independently.

### Batch 7 — API Router Completion (~80 tests)

**Target:** Fill remaining endpoint gaps in router layer.

**Files:**
1. `api/app/routers/agents.py` (1,565 LOC, 0 tests → target 30+)
   - `register_agent()` — agent registration
   - `heartbeat()` — agent heartbeat processing
   - `list_agents()` — agent enumeration
   - `trigger_agent_update()` — remote update orchestration
   - `unregister_agent()` — agent removal

2. `api/app/routers/images.py` (3,287 LOC, 48 tests → target 80+)
   - Expand beyond upload/build to cover:
   - `list_images()`, `get_image()`, `delete_image()`
   - Registry sync, push to agents
   - Device detection and metadata endpoints

3. `api/app/routers/labs.py` (3,118 LOC, 152 tests → target 200+)
   - Bundle import/export
   - Layout save/get
   - Inventory operations
   - Deeper error path testing

**Testing approach:** FastAPI TestClient with dependency overrides from conftest.py. Mock agent_client for agent communication. Test auth/permission scenarios.

### Batch 8 — Frontend Complex Components (~60 tests)

**Target:** Complex UI pages and components with zero coverage.

**Files:**
1. `web/src/pages/InfrastructurePage.tsx` (3,147 LOC, ~1 test → target 40+)
   - Agent mesh visualization
   - MTU testing flow
   - Interface management
   - Tunnel status display

2. `web/src/studio/components/ConsoleManager.tsx` (789 LOC, 0 tests → target 15+)
   - Terminal session creation/destruction
   - Tab management
   - Resize handling
   - WebSocket connection lifecycle

3. `web/src/studio/components/VniLinkDetailPanel.tsx` (273 LOC, 0 tests → target 10+)
4. `web/src/studio/components/NodeListPanel.tsx` (204 LOC, 0 tests → target 8+)
5. `web/src/studio/types.ts` (345 LOC, 0 tests → target 15+)

**Testing approach:** Vitest + React Testing Library. Mock API calls, WebSocket connections, React Flow context. Test user interactions, state changes, error displays.

### Batch 9 — Agent Remaining Zero-Coverage Files (~60 tests)

**Target:** Agent files with zero test coverage.

**Files:**
1. `agent/routers/labs.py` (853 LOC, 0 tests → target 15+)
2. `agent/providers/base.py` (508 LOC, 0 tests → target 10+)
3. `agent/routers/ovs_plugin.py` (431 LOC, 0 tests → target 12+)
4. `agent/events/docker_events.py` (286 LOC, 0 tests → target 8+)
5. `agent/console/docker_exec.py` (253 LOC, 0 tests → target 8+)
6. `agent/providers/docker_networks.py` (238 LOC, 0 tests → target 8+)

**Testing approach:** AsyncMock for Docker SDK and subprocess calls. Test error handling and edge cases. Use `_make_provider()` pattern from existing tests.

---

## Execution Notes

### Testing Patterns to Follow
- **API tests**: Use conftest.py fixtures (`db_session`, `test_user`, `admin_auth`, `sample_lab`, etc.)
- **Agent tests**: Use `unittest.mock.AsyncMock`, `_make_provider()` helper, mock subprocess calls
- **Frontend tests**: Use Vitest + RTL, mock API via `vi.mock('../api')`, mock React Flow context
- **Class-based organization**: Group related tests in classes (e.g., `class TestHotConnect:`)
- **Naming**: `test_{module}_{feature}.py` or `test_{module}_full.py` for comprehensive files

### Validation
- API/Agent: `python3 -c "import ast; ast.parse(open('file').read())"` for syntax check
- Frontend: `npx vitest run --reporter=verbose` from `web/`
- TypeScript: `npx tsc --noEmit` from `web/`

### Priority Order
1. Batch 5 (Agent networking) — highest risk, hardest to debug without tests
2. Batch 6 (API data/tasks) — autonomous processes need coverage
3. Batch 7 (API routers) — user-facing endpoints
4. Batch 8 (Frontend) — UI stability
5. Batch 9 (Agent remaining) — completeness
