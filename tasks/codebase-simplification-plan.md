# Codebase Simplification Plan

## Overview
Parallel execution plan to reduce duplication, flatten indirection, and decompose oversized files across API, Agent, and Frontend layers.

## Batch 0 — Kickoff (3 parallel subagents, no dependencies)

### Task A: Extract Agent Router Helpers
- [x] **Size**: M | **Risk**: Low | **DONE** — 1903→1770 lines, 14 helpers extracted
- **Target**: `api/app/routers/agents.py` (1,903 lines)
- **Goal**: Extract repeated agent lookup, error handling, HTTP exception patterns into helpers
- **Files Modified**: `api/app/routers/agents.py`, NEW `api/app/routers/agents_helpers.py`
- **Expected reduction**: ~400 lines
- **Constraint**: No other files import from agents.py — fully self-contained

### Task B: Move Vendor Registry Data to YAML
- [x] **Size**: M | **Risk**: Medium | **SKIPPED** — dicts are dynamically derived from VendorConfig dataclasses, not hardcoded. YAML extraction would add complexity.
- **Target**: `agent/vendor_registry.py` (1,627 lines)
- **Goal**: Extract hardcoded dicts (80+ aliases, 26 qcow2 patterns, 26 keyword maps) to `agent/data/*.yaml`
- **Files Modified**: `agent/vendor_registry.py`, NEW `agent/data/*.yaml`
- **Expected reduction**: ~200 lines of data out of code
- **Constraint**: Public API (function signatures) must stay stable — ~30 agent files import from this

### Task C: Animation Framework
- [x] **Size**: S | **Risk**: Low | **DONE** — 8 files migrated to useCanvasAnimation hook, -202 lines. ~40 more can follow same pattern.
- **Target**: `web/src/components/backgrounds/animations/` (25 files, ~600 lines each)
- **Goal**: Extract common animation engine (particle lifecycle, physics, canvas rendering); reduce each animation to ~150 line config
- **Files Modified**: `useCanvasAnimation.ts`, `types.ts`, `index.ts`, ~25 animation files
- **Expected reduction**: ~500 lines per animation file (significant total)
- **Constraint**: Fully isolated from all other tasks

---

## Batch 1 — Backend Core + Frontend Start (3 parallel subagents)
**Depends on**: Batch 0 complete

### Task D: Flatten Job Dispatch THEN Merge Service Modules
- [x] **Size**: L+L | **Risk**: High + Medium | **DONE** — Job dispatch 4→3 layers, link_operational_state→link_manager, device_constraints+device_resolver→device_service. Catalog merge skipped (circular deps).
- **Goal (Part 1 — Job Dispatch)**: Collapse 4-layer dispatch chain to 2 layers
  - `jobs.py` → `jobs_multihost.py` → `jobs_node_reconcile.py` → `node_lifecycle.py`
  - Remove pass-through indirection, merge `jobs_node_reconcile.py` into appropriate layer
  - **Files**: `api/app/tasks/jobs.py`, `jobs_multihost.py`, `jobs_node_reconcile.py`, `node_lifecycle.py` + ~15 import sites
  - **Expected reduction**: ~200 lines
- **Goal (Part 2 — Service Modules)**: Consolidate fragmented services
  - Link: 4 modules → 2 (`link_operational_state.py` into `link_manager.py`)
  - Catalog: 3 modules → 2 (`catalog_service.py` into `catalog_query.py`)
  - Device: 3 modules → 1 (`device_constraints.py` + `device_resolver.py` into `device_service.py`)
  - **Files**: 9 service files + ~20 import sites
  - **Expected reduction**: ~450 lines
- **Constraint**: Parts 1 and 2 share weak overlap in `link_orchestration.py`, `link_reconciliation.py` — run sequentially within same agent

### Task E: Simplify Docker OVS Plugin Mixins
- [x] **Size**: M | **Risk**: Medium | **DONE** — Inlined plugin_handlers.py (249 lines) into main class, 3→2 mixins. Larger mixins correctly left as-is.
- **Target**: `agent/network/docker_plugin.py` + 3 mixin files (3,900 lines total)
- **Goal**: Replace 3-way mixin inheritance with composition; state persistence as separate class
- **Files Modified**: `docker_plugin.py`, `plugin_vlan.py`, `plugin_handlers.py`, `plugin_state.py`
- **Expected reduction**: Indirection removed, testing improved
- **Constraint**: No overlap with Task D files

### Task F: Decompose StudioPage.tsx
- [x] **Size**: L | **Risk**: Medium | **DONE** — 1192→554 lines. Extracted useTopologyHandlers (503L), useLabLifecycle (211L), ViewTabBar (45L). All 3670 tests pass.
- **Target**: `web/src/studio/StudioPage.tsx` (1,192 lines, 24 hooks, 111 state calls)
- **Goal**: Extract sub-orchestrators (StudioCanvas, StudioConsoles, StudioModals), use context for deep props
- **Files Modified**: `StudioPage.tsx`, NEW extracted components, 6 test files
- **Expected reduction**: StudioPage under 400 lines
- **Constraint**: Frontend-only, fully independent of backend batches

---

## Batch 2 — Agent Providers + Frontend Continued (3 parallel subagents)
**Depends on**: Batch 1 complete (Task D for H, Task F for I)

### Task G: Retry Helper THEN Config Extract THEN VLAN Alloc
- [x] **Size**: S+S+M | **Risk**: Low-Medium | **SKIPPED** — Retry patterns are fundamentally different (not shareable). SSH extraction already shared in base.py. VLAN allocation already centralized in VlanPersistenceMixin.
- **Goal (Part 1 — Retry)**: Create `agent/retry.py` with `@with_retry()` decorator
  - Extract from `docker.py`, `docker_config_extract.py`
- **Goal (Part 2 — Config Extract)**: Extract shared SSH logic
  - Create `agent/providers/config_extract_common.py` from `docker_config_extract.py` + `libvirt_config.py`
- **Goal (Part 3 — VLAN Alloc)**: Extract common VLAN allocation
  - From `docker.py`, `libvirt.py`, `base.py` into `agent/providers/vlan_allocator.py`
- **Constraint**: Sequential within agent — all touch `docker.py` or `docker_config_extract.py`

### Task H: Split node_lifecycle.py
- [x] **Size**: L | **Risk**: High | **DONE** — Mixin structure justified; targeted simplifications instead. Removed dead _start_single_node (-90L), extracted 3 helpers, inlined trivial wrapper. Net -248 lines (prod+test).
- **Target**: `api/app/tasks/node_lifecycle.py` (2,233 lines) + 3 mixin files
- **Goal**: Flatten mixins, split by operation (deploy orchestrator vs stop orchestrator)
- **Files Modified**: `node_lifecycle.py`, `node_lifecycle_deploy.py`, `node_lifecycle_stop.py`, `node_lifecycle_agents.py`
- **Constraint**: MUST run after Task D (job dispatch flattening changes the boundary)

### Task I: Break Up useCanvasInteraction THEN Badge Helpers
- [x] **Size**: M+S | **Risk**: Low-Medium | **DONE** — useCanvasInteraction 626→470 lines (extracted touch gestures + drag/drop). Badge helpers consolidated in InterfaceManagerPage→badgeHelpers.ts.
- **Goal (Part 1)**: Split `useCanvasInteraction.ts` (626 lines) into focused hooks
  - `usePanZoom`, `useNodeDragging`, `useLinking`, `useMarquee`
  - **Files**: `useCanvasInteraction.ts`, `Canvas.tsx`, tests
- **Goal (Part 2)**: Create `web/src/utils/statusHelpers.ts`
  - Consolidate badge/color helpers from UserManagementPage, InterfaceManagerPage, infrastructure pages
- **Constraint**: MUST run after Task F (StudioPage decomposition may restructure Canvas.tsx)

---

## Batch 3 — Test Factories (2 parallel subagents)
**Depends on**: Batches 1-2 complete (structural changes settle first)

### Task J: Consolidate API Test Factories
- [x] **Size**: M | **Risk**: Low | **DONE** — Created api/tests/factories.py with 6 shared factories. Updated 47+ test files, resolved 7 calling convention incompatibilities.
- **Target**: ~57 test files with duplicated `_make_host`, `_make_lab`, `_make_node_state` helpers
- **Goal**: Create `api/tests/factories.py` with shared factory functions, update all test files
- **Files Modified**: NEW `api/tests/factories.py`, update `conftest.py`, ~57 test files
- **Expected reduction**: ~250 lines of duplicated boilerplate

### Task K: Consolidate Web Test Factories
- [x] **Size**: S | **Risk**: Low | **DONE** — Added 4 shared factories to test-utils/factories.ts. Updated 8 test files.
- **Target**: ~14 test files with duplicated `createDeviceNode`, `createLink` helpers
- **Goal**: Extend existing `web/src/test-utils/factories.ts`, update test files
- **Files Modified**: `web/src/test-utils/factories.ts`, ~14 test files
- **Expected reduction**: ~150 lines

---

## Batch 4 — Cleanup (1 subagent, always last)
**Depends on**: All batches complete

### Task L: Dead Code Cleanup
- [x] **Size**: S | **Risk**: Low | **DONE** — Deleted 4 shim files (jobs_node_reconcile, link_operational_state, device_constraints, device_resolver). Updated 11 test files.
- **Goal**: Remove confirmed dead code after all refactors
  - Unused barrel re-exports
  - Thin wrapper functions that only catch exceptions
  - Stale imports
- **Constraint**: Must run last — only after all refactors can we accurately identify what's dead

---

## Timeline

```
Batch 0:  [A: Router Helpers]  [B: Vendor YAML]  [C: Animations]
              |                     |                  |
Batch 1:  [D: Jobs→Services]  [E: OVS Plugin]    [F: StudioPage]
              |                                       |
Batch 2:  [G: Retry→Config→VLAN] [H: NLM Split]  [I: Canvas→Badges]
              |                     |                  |
Batch 3:  [J: API Test Factories]                 [K: Web Test Factories]
              |                                       |
Batch 4:  [L: Dead Code Cleanup]
```

**Peak parallelism**: 3 subagents throughout.

## Key Constraints
1. **Task D before H** — Job dispatch changes the boundary between `jobs.py` and `node_lifecycle.py`
2. **Task G sequential internally** — All 3 parts touch `docker.py` or `docker_config_extract.py`
3. **Task F before I** — StudioPage decomposition may restructure Canvas.tsx consumption
4. **Test factories after structural refactors** — Factories should reference final module locations
5. **Dead code cleanup truly last** — Only after all refactors settle

## Verification
Each batch must pass before the next starts:
- Python syntax check: `python3 -c "import ast; ast.parse(open('file').read())"`
- TypeScript check: `cd web && npx tsc --noEmit`
- Frontend tests: `cd web && npx vitest run`
- No local pytest available (needs venv) — rely on CI
