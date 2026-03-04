# Test Coverage Analysis & Improvement Plan — Round 6

**Status:** Completed (2026-03-04)  
**Superseded by:** `tasks/test-coverage-round7-plan.md`

## Current State (March 3, 2026)

### Overall Numbers

| Component | Source LOC | Test LOC | Test Files | Test Functions | Test:Source |
|-----------|-----------|----------|------------|----------------|-------------|
| API       | 64,081    | 79,009   | 176        | 3,297          | 1.23x       |
| Agent     | 42,277    | 45,284   | 136        | 1,953          | 1.07x       |
| Frontend  | ~56,000   | ~38,000  | 142        | 2,191          | ~0.68x      |
| **Total** | **~162k** | **~162k**| **454**    | **7,441**      | **~1.0x**   |

Growth since Round 5 (Feb 27): +72 test files, +1,193 test functions, +~29k test LOC.

### Test Infrastructure Strengths

- API conftest.py is production-quality (799 lines, 40+ fixtures, global Redis mock)
- Agent conftest.py has clean CI separation (Docker/aiohttp/asyncssh stubs, integration gating)
- Frontend has vitest + RTL + factories, but provider coverage in `renderWithProviders` is incomplete
- CI has 7-job parallel pipeline with confidence gate, secrets scan, BATS shell tests
- Both Python suites use `asyncio_mode = auto` for clean async testing

---

## CI Pipeline Weaknesses

These systemic issues undermine the value of all test coverage:

### 1. Backend test failures don't block merges (CRITICAL)

Both `backend-tests` jobs run with `continue-on-error: true`. Only narrow smoke checks (4-5 tests each) are hard-required. A regression that breaks 100 unit tests **will not block a PR merge**.

**Fix:** Remove `continue-on-error: true` from the `backend-tests` job, or add the full test suites to the confidence gate's required checks.

### 2. No frontend coverage enforcement

Vitest config declares `@vitest/coverage-v8` but the CI `frontend-tests` job doesn't pass `--coverage`. No thresholds are set. Frontend coverage could silently drop to 0%.

**Fix:** Add `--coverage` flag and set thresholds (start at 60%, ratchet up).

### 3. Coverage thresholds are low

API: 55%, Agent: 50%. These are permissive enough that entire subsystems can be deleted and coverage still passes.

**Fix:** Ratchet to API: 65%, Agent: 60% after Round 6 work.

### 4. Coverage reports go nowhere

XML reports are written locally but not uploaded as artifacts or sent to Codecov/Coveralls. No trend tracking exists.

**Fix:** Upload coverage artifacts + add Codecov integration.

### 5. No database migration test in CI

No job runs `alembic upgrade head` against a real Postgres instance. SQLite in tests bypasses Alembic entirely.

**Fix:** Add a migration smoke job: spin up Postgres in CI, run `alembic upgrade head`, verify schema matches expectations.

### 6. Lint is non-blocking

`ruff` and `tsc --noEmit` run under `continue-on-error: true`. Type errors and unused imports don't block merges.

**Fix:** Make lint blocking (at minimum `ruff --select F401,F841` for unused code and `tsc --noEmit`).

---

## Component Analysis: Gaps by Severity

### API — Critical Gaps (zero or near-zero coverage)

| # | Module | Lines | Why Critical |
|---|--------|-------|-------------|
| 1 | `tasks/reconciliation_db.py` | 1,275 | Core DB reconciliation — link state init, placement backfill, orphan cleanup, main per-lab reconcile loop |
| 2 | `routers/infrastructure_interfaces.py` | 843 | Largest untested router — NIC management, data plane separation |
| 3 | `services/catalog_identity.py` | 641 | Catalog identity sync, alias resolution, deterministic identity backfill |
| 4 | `tasks/jobs_multihost.py` | 634 | Multi-host deploy/destroy orchestration, rollback logic |
| 5 | `tasks/node_lifecycle_stop.py` | 413 | Node stop/destroy lifecycle — complementary to well-tested deploy path |
| 6 | `tasks/reconciliation_refresh.py` | 410 | Reconciliation sweep triggers, transitional state age checks |
| 7 | `tasks/stuck_nodes.py` | 170 | Detects nodes stuck in transitional states — production reliability |
| 8 | `tasks/jobs_node_reconcile.py` | 219 | Per-node reconcile job outside full deploy |
| 9 | `routers/webhooks.py` | ~150 | Webhook CRUD endpoints (only delivery tested elsewhere) |

### API — Thin Coverage (tests exist but shallow)

| Module | Tests | Gap |
|--------|-------|-----|
| `tasks/node_lifecycle_deploy.py` (1,078 lines) | 20 | Only orchestration, not per-device branches |
| `tasks/node_lifecycle_agents.py` (659 lines) | 10 | Agent dispatch tested, config push/placement sync thin |
| `tasks/link_repair.py` | 9 | Most repair branches uncovered |
| `routers/state_ws.py` | ~18 | Reconnect/auth timeout paths thin |
| `routers/console.py` | 9 | SSH proxy and VM serial console untested |
| `routers/scenarios.py` | 4 | Scenario execution path uncovered |
| `routers/callbacks.py` | 17 | Error paths and idempotency uncovered |
| `services/link_manager.py` | 26 | Cross-host hot-connect edge cases thin |
| `routers/auth.py` | ~34 | OIDC flow not meaningfully tested |
| `image_store/manifest.py` | 0 | Core load/save/update (indirect only) |
| `image_store/metadata.py` | 0 | Image metadata enrichment (indirect only) |

### Agent — Critical Gaps

| # | Module | Lines | Why Critical |
|---|--------|-------|-------------|
| 1 | `network/plugin_state.py` | 1,037 | State persistence, GC, reconciliation for DockerOVSPlugin — only indirect coverage |
| 2 | `network/plugin_vlan.py` | 877 | VLAN management and carrier state mixin — only indirect coverage |
| 3 | `network/ovs_provision.py` | 700 | OVS port provisioning orchestration |
| 4 | `routers/ovs_plugin.py` | 431 | OVS status/link/bridge-ports endpoints |
| 5 | `providers/libvirt_n9kv.py` | 346 | N9Kv boot intervention — POAP, loader recovery, kernel panic |
| 6 | `providers/docker_config_extract.py` | 278 | Config extraction from containers |
| 7 | `routers/admin.py` | 187 | POAP config serving, agent update endpoints |
| 8 | `image_metadata.py` | 165 | File-locking concurrent JSON store with `fcntl.flock` |
| 9 | `vendor_registry.py` | 1,614 | VENDOR_CONFIGS data blob — no validation tests |

### Agent — Thin Coverage

| Module | Tests | Gap |
|--------|-------|-----|
| `network/ovs.py` (1,638 lines) | ~55 | Complex async batching paths under-exercised |
| `network/docker_plugin.py` (1,541 lines) | ~170 | Spread across 11 files; carrier/VLAN collision/repair thin |
| `network/overlay.py` recovery | 2 | `test_overlay_recovery.py` has only 2 tests |
| `network/vlan.py` | 2 | Very thin for VLAN allocation module |
| `providers/libvirt_xml.py` (769 lines) | indirect | Never directly imported in tests |
| `providers/libvirt.py` N9Kv paths | ~205 total | 3,047 lines; boot intervention paths thin |

### Frontend — Critical Gaps

| # | Module | Lines | Why Critical |
|---|--------|-------|-------------|
| 1 | `studio/components/canvas/Canvas.tsx` (sub) | 757 | ReactFlow rendering engine — zero tests |
| 2 | `studio/components/canvas/useCanvasInteraction.ts` | 457 | Core drag-drop, selection, link creation |
| 3 | `studio/components/deviceManager/useImageUpload.ts` | 486 | Two-phase upload with progress |
| 4 | `studio/hooks/useLabTopology.ts` | 311 | Topology CRUD — add/delete nodes/links |
| 5 | `pages/infrastructure/NetworkTab.tsx` | 592 | Complex network config tab |
| 6 | `pages/infrastructure/HostCard.tsx` | 501 | Agent card with resource meters |
| 7 | `pages/infrastructure/MtuConfigModal.tsx` | 386 | MTU configuration form |
| 8 | `studio/hooks/useConsoleManager.ts` | 245 | Console WebSocket lifecycle |
| 9 | `studio/hooks/useJobTracking.ts` | 224 | Job polling + WS aggregation |
| 10 | `studio/studioUtils.ts` | 84 | Pure functions — `buildGraphNodes`, `buildGraphLinks` |

### Frontend — Thin Coverage

| Module | Tests | Gap |
|--------|-------|-----|
| `StudioPage.tsx` | 20 | Only smoke tests — no topology/deploy/WS flows |
| `useConfigManager.ts` | 11 | No snapshot create/delete |
| `usePersistedState.ts` | 2 | Trivially thin for stateful hook |
| `ConfigsView/*` | 1-2 each | Single export checks |

---

## Improvement Plan

### Phase 0: CI Hardening (no new tests — infrastructure fixes)

**Priority: Do first. Multiplies value of all subsequent test work.**

| Task | Effort | Impact |
|------|--------|--------|
| Remove `continue-on-error: true` from `backend-tests` | 5 min | Prevents regressions from shipping silently |
| Make lint job blocking | 5 min | Catches unused code and type errors |
| Add `--coverage` + thresholds to frontend CI job | 15 min | Prevents frontend coverage regression |
| Ratchet coverage: API 55→65%, Agent 50→60% | After Phase 1 | Enforces new coverage |
| Add Alembic migration smoke job | 30 min | Catches broken migrations before deploy |
| Upload coverage XML as CI artifacts | 10 min | Enables trend tracking |

### Phase 1: API Background Tasks (target: ~120 tests)

**Rationale:** Background tasks are the hardest to debug in production and have the most critical gaps. These run autonomously without user visibility.

| File | Target Tests | Focus |
|------|-------------|-------|
| `tasks/reconciliation_db.py` (1,275 LOC) | 35 | `_ensure_link_states_for_lab`, `_backfill_placement_node_ids`, `cleanup_orphaned_node_states`, main per-lab reconcile loop, multi-agent scenarios |
| `tasks/jobs_multihost.py` (634 LOC) | 25 | Multi-host dispatch, capacity checks, partial-failure rollback, single-host fallback |
| `tasks/node_lifecycle_stop.py` (413 LOC) | 20 | Stop sequencing, cleanup state transitions, partial-stop recovery |
| `tasks/reconciliation_refresh.py` (410 LOC) | 15 | Sweep trigger logic, transitional state age guards, per-lab triggers |
| `tasks/stuck_nodes.py` (170 LOC) | 10 | Detection thresholds, transition timeout logic, state correction |
| `tasks/jobs_node_reconcile.py` (219 LOC) | 15 | Per-node sync outside deploy, agent communication failures |

**Testing approach:** Use existing conftest fixtures (`sample_lab`, `running_lab`, `deployed_node_state`, `mock_broadcaster`). Mock `agent_client` calls. Test each reconciliation branch independently with explicit DB state setup.

### Phase 2: Agent Networking Core (target: ~100 tests)

**Rationale:** Plugin state and VLAN management are the heart of container networking. Currently covered only indirectly through the composed `DockerOVSPlugin` class.

| File | Target Tests | Focus |
|------|-------------|-------|
| `network/plugin_state.py` (1,037 LOC) | 30 | State persistence to disk, GC of stale state, reconciliation with OVS, recovery from corrupt state files |
| `network/plugin_vlan.py` (877 LOC) | 25 | VLAN allocation/release, carrier state propagation, collision detection, hot-connect VLAN matching |
| `network/ovs_provision.py` (700 LOC) | 20 | Port provisioning orchestration, batch operations, failure recovery |
| `routers/ovs_plugin.py` (431 LOC) | 15 | Status, link state, bridge-ports endpoints |
| `image_metadata.py` (165 LOC) | 10 | `fcntl.flock` concurrent access, atomic writes, corruption recovery |

**Testing approach:** Mock subprocess (`ovs-vsctl`, `ip link`) calls. Use `AsyncMock` for async methods. Test state transitions and recovery scenarios. For `image_metadata.py`, test concurrent access with threading.

### Phase 3: API Routers & Services (target: ~90 tests)

| File | Target Tests | Focus |
|------|-------------|-------|
| `routers/infrastructure_interfaces.py` (843 LOC) | 25 | NIC assignment, data plane separation, interface enumeration |
| `services/catalog_identity.py` (641 LOC) | 20 | Identity sync, alias resolution, deterministic backfill |
| `tasks/link_repair.py` (existing tests: 9) | 15 | All repair branches — VLAN mismatch, carrier, OVS port swap |
| `routers/webhooks.py` (~150 LOC) | 10 | Webhook CRUD — create, list, delete, update, delivery trigger |
| `routers/scenarios.py` (existing: 4) | 10 | Scenario execution path, error handling |
| `image_store/manifest.py` + `metadata.py` | 10 | Direct unit tests isolating load/save/enrich logic |

### Phase 4: Agent Provider & Boot Paths (target: ~70 tests)

| File | Target Tests | Focus |
|------|-------------|-------|
| `providers/libvirt_n9kv.py` (346 LOC) | 15 | POAP abort, loader recovery retries, admin password setup, kernel panic restart |
| `providers/docker_config_extract.py` (278 LOC) | 12 | Config extraction for cEOS, cJunOS, Linux; error handling |
| `providers/libvirt_xml.py` (769 LOC) | 15 | Direct import tests — domain XML generation for all device types |
| `routers/admin.py` (187 LOC) | 10 | POAP config serving, agent update trigger |
| `vendor_registry.py` (1,614 LOC) | 18 | Validation tests — all entries have required fields, port naming patterns valid, alias resolution |

### Phase 5: Frontend Hooks & Utilities (target: ~80 tests)

**Rationale:** Hooks are pure logic, easiest to test, highest ROI.

| File | Target Tests | Focus |
|------|-------------|-------|
| `studio/studioUtils.ts` (84 LOC) | 15 | Pure functions: `buildGraphNodes`, `buildGraphLinks`, `generateContainerName` |
| `studio/hooks/useLabTopology.ts` (311 LOC) | 20 | Add/delete node, create link, update-topology calls |
| `studio/hooks/useConsoleManager.ts` (245 LOC) | 15 | Session lifecycle, WebSocket management, tab state |
| `studio/hooks/useJobTracking.ts` (224 LOC) | 12 | Job polling, status transitions, log aggregation |
| `studio/hooks/useNodeStates.ts` (197 LOC) | 8 | Node state derivation, update tracking |
| `pages/infrastructure/useInfrastructureData.ts` (164 LOC) | 10 | Data loading, error states, refresh logic |

**Testing approach:** `renderHook` from RTL. Mock `fetch` calls. Test state transitions and error handling.

### Phase 6: Frontend Components (target: ~60 tests)

| File | Target Tests | Focus |
|------|-------------|-------|
| `pages/infrastructure/NetworkTab.tsx` (592 LOC) | 15 | Rendering with mock data, modal triggers, form submissions |
| `pages/infrastructure/HostCard.tsx` (501 LOC) | 15 | Resource meters, lab list, action buttons |
| `studio/components/deviceManager/UploadControls.tsx` (265 LOC) | 10 | Upload flow UI, progress, error display |
| `studio/components/canvas/ContextMenuOverlay.tsx` (68 LOC) | 8 | Menu items, action dispatch |
| `pages/infrastructure/MtuConfigModal.tsx` (386 LOC) | 12 | Form validation, submit, error states |

### Phase 7: Test Infrastructure Improvements

| Task | Impact |
|------|--------|
| Add MSW (Mock Service Worker) to frontend test setup | Replaces brittle `vi.fn()` fetch mocks across 142 test files |
| Extend `renderWithProviders` with `UserContext`, `NotificationContext`, `DeviceCatalogContext` | Eliminates per-test provider boilerplate |
| Add shared factories for nodes, links, node states, topology graphs in `test-utils/factories.ts` | Currently only Host/Lab/Device/Image/User factories exist |
| Consolidate agent "batch" test files by module (e.g., merge `test_docker_plugin_batch{1-11}.py` into organized files) | 136 test files across sprawling batch naming; hard to find tests for a module |
| Add `image_store/` direct unit tests (not via `__init__` re-exports) | Isolates submodule logic; clearer failure traces |

---

## Priority Ordering

```
Phase 0 (CI)  →  Phase 1 (API tasks)  →  Phase 2 (Agent networking)
     ↓                                          ↓
Phase 3 (API routers)  →  Phase 4 (Agent providers)
     ↓                          ↓
Phase 5 (FE hooks)  →  Phase 6 (FE components)  →  Phase 7 (Infrastructure)
```

**Phase 0** should be done immediately — it's pure config changes that multiply the value of everything else.

**Phases 1-2** are the highest-value test work — background tasks and networking core are the hardest to debug in production.

**Phases 3-4** fill important router/provider gaps.

**Phases 5-6** bring the frontend to parity.

**Phase 7** is ongoing infrastructure improvement.

### Expected Outcome

| Metric | Current | After Round 6 |
|--------|---------|---------------|
| Total test functions | 7,441 | ~8,061 (+620) |
| API coverage | ~55% | ~68% |
| Agent coverage | ~50% | ~62% |
| Frontend coverage | unmeasured | ~65% (with thresholds) |
| Backend tests block merges | No | Yes |
| Lint blocks merges | No | Yes |
| Coverage trend tracking | None | CI artifacts |

### Known Risks

1. **Test sprawl**: Agent already has 136 test files with "batch" naming. New tests should use organized names, not more batches.
2. **SQLite vs Postgres**: All API tests use SQLite. Some reconciliation logic may behave differently on Postgres (JSON operations, locking). The Alembic migration smoke job partially mitigates this.
3. **Frontend ReactFlow**: The canvas sub-module (`Canvas.tsx`, `useCanvasInteraction.ts`) is extremely hard to unit-test due to deep ReactFlow integration. Consider a separate E2E strategy (Playwright) for canvas interaction testing rather than unit tests.
4. **No E2E browser tests**: There is no Playwright/Cypress setup. The most user-visible bugs (canvas interaction, console connection, deploy flow) can only be caught by E2E tests. This is a strategic gap beyond the scope of unit test improvement.
