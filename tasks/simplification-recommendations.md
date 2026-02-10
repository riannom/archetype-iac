# Codebase Simplification Recommendations

Generated: 2026-02-09

## Executive Summary

Analysis of **API** (18 findings), **Agent** (15 findings), and **Frontend** (18 findings) codebases. Cross-cutting themes:

| Theme | Occurrences | Impact |
|-------|------------|--------|
| **Monolithic files** | `agent/main.py` (6,348 lines), `labs.py` (3,609 lines), `InfrastructurePage` (2,447 lines), `StudioPage` (1,890 lines) | High |
| **Duplicated utilities** | Container naming (4 places), `_run_cmd` (5 places), `docker.from_env()` (30+ calls), `resolve_device_kind` (3 copies) | High |
| **Dead code** | Unused Zustand store (440 lines), legacy netlab modules, unused schemas, dead functions in StudioPage | Medium |
| **Inconsistent patterns** | Raw `fetch` vs `apiRequest` (13 instances), `alert()` vs notification system (25 instances), inline vs top-level imports | Medium |

---

## HIGH IMPACT

### 1. Split `agent/main.py` (6,348 lines) into routers
- [ ] Container control endpoints (start/stop/remove) -> `agent/routers/containers.py`
- [ ] Per-node lifecycle (create/start/stop/destroy) -> `agent/routers/nodes.py`
- [ ] Image sync endpoints -> `agent/routers/images.py`
- [ ] Console WebSocket handlers -> `agent/routers/console.py`
- [ ] Network/overlay endpoints -> `agent/routers/network.py`
- [ ] Debug/diagnostic endpoints -> `agent/routers/debug.py`

### 2. Split `api/app/routers/labs.py` (3,609 lines)
- [ ] Node/link state management -> `api/app/routers/lab_states.py`
- [ ] Config extraction/snapshots -> `api/app/routers/lab_configs.py`
- [ ] Interface mappings -> `api/app/routers/lab_interfaces.py`
- [ ] Move 7 inline Pydantic models to `schemas.py`

### 3. Split `InfrastructurePage.tsx` (2,447 lines)
- [ ] Extract 16 inline types to shared types file
- [ ] Extract `HostCard` component
- [ ] Extract `NetworkTab` and `HostsTab` components
- [ ] Extract 3 modals (`DeregisterModal`, `MtuConfigModal`, `CustomUpdateModal`)
- [ ] Move badge helpers to `utils/status.ts`
- [ ] Consolidate 4 identical `toggle*Expanded` functions

### 4. Centralize Docker client creation (30+ scattered `docker.from_env()` calls)
- [ ] Create shared `get_docker_client()` singleton utility
- [ ] Replace all 14 calls in `agent/main.py`
- [ ] Replace 7 calls in `agent/network/docker_plugin.py`
- [ ] Replace 3 calls in `agent/readiness.py`
- [ ] Replace remaining scattered calls

### 5. Consolidate container name building (4 implementations)
- [ ] Create `agent/utils/naming.py` with single `get_container_name()`
- [ ] Replace `_get_container_name` in `api/app/tasks/jobs.py:1365`
- [ ] Replace `_get_container_name` in `api/app/tasks/node_lifecycle.py:64`
- [ ] Replace `_container_name` in `agent/providers/docker.py:314`
- [ ] Replace `_build_container_name` in `agent/network/overlay.py:46`
- [ ] Replace inline regex in `agent/main.py:1647-1659`

### 6. Consolidate `_run_cmd` async subprocess helper (5 copies)
- [ ] Create `agent/network/utils.py` with shared `run_cmd()`
- [ ] Replace in `overlay.py:220`, `ovs.py:396`, `cleanup.py:126`, `local.py:130`, `docker_plugin.py:169`

### 7. Merge duplicate topology modules
- [ ] Consolidate `api/app/topology.py` (891 lines) into `api/app/services/topology.py` (1,429 lines)
- [ ] Deduplicate `resolve_device_kind` (3 copies across `interface_naming.py`, `services/topology.py`, `topology.py`)
- [ ] Deduplicate `_effective_max_ports` / `_get_effective_max_ports` (2 copies in same file)

---

## MEDIUM IMPACT

### 8. Remove dead code in `StudioPage.tsx` (~100 lines)
- [ ] Remove `buildStatusMap` (lines 132-183) - never called
- [ ] Remove `resolveNodeStatus` (lines 132-183) - only called by buildStatusMap
- [ ] Remove `guessDeviceType` (lines 58-65) - never called
- [ ] Remove `DEFAULT_ICON` (line 42) - never referenced
- [ ] Remove `RuntimeStatus` type alias (line 40) - backward-compat alias not needed

### 9. Remove or adopt unused `canvasStore.ts` (440 lines)
- [ ] Decision: migrate StudioPage to use it OR delete it
- [ ] If deleting: also remove test file and `zustand`/`immer` deps if unused elsewhere

### 10. Replace `alert()` with notification system (25 instances)
- [ ] `InfrastructurePage.tsx` - 16 occurrences
- [ ] `ConfigsView/index.tsx` - 4 occurrences
- [ ] `RuntimeControl.tsx` - 2 occurrences
- [ ] `ImageCard.tsx` - 1 occurrence
- [ ] `DeviceManager.tsx` - 1 occurrence
- [ ] `ConfigsView/ConfigMapping.tsx` - 1 occurrence

### 11. Standardize API calls (13 raw `fetch` instances)
- [ ] `ISOImportModal.tsx` - 6 occurrences (may need `rawApiRequest` variant for non-JSON)
- [ ] `NotificationContext.tsx` - 3 occurrences
- [ ] `UserContext.tsx` - 1 occurrence
- [ ] `DeviceManager.tsx` - 2 occurrences
- [ ] `StudioConsolePage.tsx` - 1 occurrence

### 12. Remove legacy netlab code
- [ ] Audit usage of `api/app/netlab.py` (16 lines)
- [ ] Audit usage of `api/app/providers.py` (58 lines)
- [ ] Audit usage of `api/app/catalog.py` (46 lines)
- [ ] Remove `enqueue_job` and `execute_netlab_action` from `api/app/jobs.py` (never imported externally)

### 13. Deduplicate deploy/destroy callback functions
- [ ] Merge `_execute_deploy_with_callback` (lines 1242-1348) and `_execute_destroy_with_callback` (lines 1429-1513)
- [ ] Adapt existing unused `execute_with_callback()` from `callbacks.py`

### 14. Eliminate duplicate type definitions (frontend)
- [ ] Shared `ManagedInterface` and `InterfaceDetail` -> `types/infrastructure.ts`
- [ ] Shared badge functions -> `utils/infrastructure.ts`
- [ ] Shared `LinkStateData`/`LabStateData` -> `types/nodeState.ts`

### 15. Extract StudioPage sub-hooks
- [ ] `useConsoleManager` hook (windows, docked consoles, merge/split/reorder)
- [ ] `useJobTracker` hook (jobs, prevJobsRef, status change tracking)
- [ ] `useTopologyPersistence` hook (save/load layout, dirty tracking, debouncing)

### 16. Consolidate VLAN persistence (Docker/Libvirt providers)
- [ ] Move `_save_vlan_allocations`, `_load_vlan_allocations`, `_remove_vlan_file` to shared mixin in `agent/providers/base.py`

### 17. Remove redundant inline imports in `labs.py`
- [ ] Remove 8 redundant `from app.utils.lab import get_lab_provider` (already at top-level line 29)
- [ ] Remove redundant `asyncio`, `agent_client` re-imports

---

## LOW IMPACT

### 18. Move inline Pydantic models to `schemas.py`
- [ ] `CheckResourcesRequest`, `PerHostCapacity`, `CheckResourcesResponse`
- [ ] `TopologyGraphWithLayout`, `CleanupOrphansResponse`
- [ ] `InterfaceMappingSyncResponse`, `LinkReconciliationResponse`

### 19. Remove deprecated `Vtep` class in `overlay.py`
- [ ] Remove class (lines 84-113) and associated methods if legacy trunk model no longer in use

### 20. Remove unused agent schemas
- [ ] Remove `LabStatusRequest` (never used)
- [ ] Remove `ExtractConfigsRequest` import or schema (unused as endpoint parameter)

### 21. Add `management_ips` property to NodeState model
- [ ] Replace 2 instances of `json.loads(state.management_ips_json)` with model property

### 22. Simplify `get_lab_provider` utility
- [ ] Make it a `Lab` model property since the column already defaults to `"docker"`

### 23. Deduplicate `formatBytes` function
- [ ] `ISOImportModal.tsx` and `ImageSyncProgress.tsx` both define local `formatBytes`
- [ ] Use shared `formatSize` from `utils/format.ts`

### 24. Trivial `refreshImageLibrary` wrapper
- [ ] Expose `fetchImageLibrary` directly instead of wrapping in identity callback
