# Codebase Simplification — Next Steps

Follow-up items from the simplification effort (59 files, -1,447 net lines).

## Immediate Follow-up

### 1. CI Validation
- [ ] Push branch and verify all CI checks pass (API tests, frontend tests, lint)
- [ ] The 47+ modified API test files and shared `factories.py` need pytest validation
- [ ] Frontend vitest + tsc already verified locally, but CI is authoritative

### 2. Migrate Remaining ~40 Animation Files
- [ ] Task C migrated 8 animation files to `useCanvasAnimation` hook (-202 lines)
- [ ] The same mechanical pattern applies to the rest in `web/src/components/backgrounds/animations/`
- [ ] Each migration removes ~15-20 lines of boilerplate (canvas setup, resize handler, RAF loop, cleanup)
- [ ] Estimated total savings: ~600-800 lines

## Medium-term Improvements

### 3. Large Frontend Components
These components are still oversized and would benefit from the same decomposition applied to StudioPage:

| Component | Lines | Suggested Action |
|-----------|-------|-----------------|
| `UserManagementPage.tsx` | 845 | Extract UserCreateModal, UserEditModal, UserPasswordModal |
| `ConsoleManager.tsx` | 789 | Extract ConsoleGestures, ConsoleTabManager |
| `Canvas.tsx` | 764 | Extract LinkDrawing, SelectionRendering sub-components |
| `AgentGraph.tsx` | 763 | Extract layout computation into a hook |
| `LogsView.tsx` | 726 | Extract filter state into useLogFilters hook |
| `ThemeProvider.tsx` | 651 | Separate theme loading, preference persistence, context |

### 4. Catalog Service Consolidation
- Task D correctly skipped merging `catalog_service.py`/`catalog_query.py`/`catalog_identity.py` due to circular dependencies
- A deeper restructuring could break the cycle: extract shared types to a `catalog_types.py`, then merge the logic modules
- Low priority — the current structure works, it's just 3 files instead of 2

### 5. Large Backend Files
These remain the biggest files in the codebase:

| File | Lines | Notes |
|------|-------|-------|
| `agent/providers/libvirt.py` | 3,723 | N9Kv-specific logic (~500 lines) could be extracted |
| `agent/providers/docker.py` | 3,125 | Network setup logic could be a sub-module |
| `api/app/tasks/node_lifecycle.py` | 2,201 | Mixin structure justified; already simplified |
| `api/app/routers/agents.py` | 1,770 | Already simplified; further reduction diminishing returns |
| `agent/network/docker_plugin.py` | 1,660 | 2 remaining mixins justified by total size |
| `api/app/tasks/reconciliation_db.py` | 1,593 | Mixed concerns (placement, orphan cleanup, reconciliation) |
| `api/app/services/topology.py` | 1,455 | Graph conversion + persistence + analysis mixed |

### 6. API Test Factory Coverage Extension
- `api/tests/factories.py` currently has: `make_host`, `make_lab`, `make_node_state`, `make_link_state`, `make_placement`, `make_job`
- Could add: `make_node_def`, `make_image_host`, `make_permission` if duplication is found in remaining test files
- Also: move `_fake_get_session` context manager pattern from individual tests to `conftest.py`

## Completed (Reference)

| Task | Result |
|------|--------|
| A: Agent Router Helpers | 1,903→1,770 lines, 14 helpers |
| B: Vendor Registry YAML | Skipped — derived from typed dataclasses |
| C: Animation Framework | 8/48 files migrated, -202 lines |
| D: Job Dispatch + Services | 4→3 layers; 3 service merges (1 skipped) |
| E: OVS Plugin Mixins | 3→2 mixins, plugin_handlers inlined |
| F: Decompose StudioPage | 1,192→554 lines, 3 extractions |
| G: Retry/Config/VLAN | All skipped — already well-factored |
| H: Node Lifecycle | Targeted cleanup, -248 lines |
| I: Canvas Hook + Badges | 626→470 lines, badge helpers consolidated |
| J: API Test Factories | Created factories.py, 47+ files updated |
| K: Web Test Factories | 4 shared factories, 8 files updated |
| L: Dead Code Cleanup | 4 shim files deleted, 11 tests updated |
