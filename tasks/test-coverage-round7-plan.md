# Test Coverage Analysis & Improvement Plan — Round 7

## Status

- Date: 2026-03-04
- Scope: API, Agent, Web test coverage gaps + CI stability blockers
- Previous plans (`round3`, `round5`, `round6`) are now marked complete and superseded by this plan.

## Evidence Used

1. Fresh static import-map coverage from `python3 scripts/coverage_map.py`.
2. Targeted runtime coverage probes to validate static-map blind spots:
   - Agent: `tests/test_plugin_state.py`, `tests/test_plugin_vlan.py`, `tests/test_plugin_handlers_extended.py`
   - API: `tests/test_tasks_node_lifecycle_stop.py`
3. Latest failed Actions run analysis:
   - Run `22655838213` (`Tests` workflow) failed in:
     - `Backend Tests` (job timeout at 30m)
     - `Lint` (`ruff check agent/`)

## Current Snapshot

### Static import-map signal (2026-03-04)

| Component | Source Files | Covered | Uncovered |
|-----------|--------------|---------|-----------|
| API       | 173          | 139     | 34        |
| Agent     | 98           | 95      | 3         |
| Web       | 235          | 221     | 14        |

### Runtime probe signal (sampled)

- Agent `plugin_handlers.py`: 100% (112/112 statements) in targeted suite.
- Agent `plugin_state.py`: 53% (547 stmts, 257 miss) in targeted suite.
- Agent `plugin_vlan.py`: 55% (396 stmts, 180 miss) in targeted suite.
- API `node_lifecycle_stop.py`: 98% (191 stmts, 4 miss) in targeted suite.

### Interpretation

- The static map is useful for discovery, but it under-reports modules exercised indirectly through composed modules and router aggregation.
- Confirmed: some files reported as “uncovered” are exercised at runtime.
- Confirmed high-confidence gap area: Web uncovered files and branch-depth in Agent plugin state/VLAN logic.

## CI Failures Addressed In This Round

1. `Lint` failure fixed:
   - Resolved Ruff violations in Agent tests (unused imports/variables).
   - Files updated:
     - `agent/tests/test_docker_networks_extended.py`
     - `agent/tests/test_docker_setup_extended.py`
     - `agent/tests/test_libvirt_config_extended.py`
     - `agent/tests/test_overlay_state_extended.py`
     - `agent/tests/test_overlay_vxlan_extended.py`
     - `agent/tests/test_plugin_handlers_extended.py`
2. `Backend Tests` timeout mitigation:
   - Increased `backend-tests` timeout from `30` to `90` minutes.
   - Reduced pytest verbosity (`-v` -> `-q`) for API/Agent backend coverage runs.
   - File updated: `.github/workflows/test.yml`

## Priority Gaps To Close Next

### P1: Web files with no direct test import coverage

Target these first (highest confidence true gaps):

- `web/src/components/isoImport/ISOImportProgress.tsx`
- `web/src/pages/infrastructure/DeregisterModal.tsx`
- `web/src/pages/infrastructure/HostsTab.tsx`
- `web/src/studio/components/canvas/CanvasControls.tsx`
- `web/src/studio/components/deviceManager/ImageLibraryView.tsx`
- `web/src/studio/components/deviceManager/UploadLogsModal.tsx`
- `web/src/studio/components/deviceManager/deviceManagerUtils.ts`
- `web/src/studio/components/deviceManager/useImageFilters.ts`
- `web/src/studio/components/deviceManager/useImageManagementLog.ts`
- `web/src/studio/hooks/useLabDataLoading.ts`

### P2: Agent branch-depth hardening

Even with substantial test coverage, core logic still has branch gaps:

- `agent/network/plugin_state.py`
- `agent/network/plugin_vlan.py`

Focus on recovery/error branches:
- Corrupt state files, stale endpoint GC, VLAN collision fallback, partial reconciliation rollback.

### P3: API split-module runtime verification

Static-map “uncovered” modules in these areas need runtime coverage validation and targeted tests where needed:

- `api/app/agent_client/*`
- `api/app/routers/images/*`
- `api/app/routers/labs/*`
- `api/app/routers/infrastructure_*`
- `api/app/schemas/*`
- `api/app/models/*`

Add focused coverage runs per area and only add tests where runtime coverage confirms real gaps.

## Execution Plan

### Phase 1 (1-2 days): Coverage Signal Hardening

1. Add `make` targets for targeted runtime coverage probes by module family (API routers, Agent plugin core, Web critical hooks/components).
2. Persist probe artifacts under `reports/coverage-probes/`.
3. Update `scripts/coverage_map.py` output docs to clearly label static import-map limitations.

### Phase 2 (2-3 days): Web High-Confidence Gap Fill

1. Add test files for all P1 Web modules.
2. Focus on behavior tests (user interactions, edge/error state rendering), not import-smoke tests.
3. Run `make test-web-container` and `npx vitest run --coverage` for touched areas.

### Phase 3 (2 days): Agent Plugin Branch Gap Fill

1. Add recovery/error-path tests for `plugin_state.py` and `plugin_vlan.py`.
2. Target runtime branch increases before threshold changes.
3. Validate with focused `pytest --cov` module probes.

### Phase 4 (2-3 days): API Runtime Gap Validation + Fill

1. Run targeted module coverage for split API routers/services/models flagged by static map.
2. Add missing behavior tests only for modules below agreed runtime targets.
3. Prioritize `agent_client` and infra/labs/images split routers.

### Phase 5 (1 day): Threshold Ratcheting

After Phase 2-4 passes:

1. Raise API coverage gate from `55` to `60` (next ratchet `65`).
2. Raise Agent coverage gate from `50` to `55` (next ratchet `60`).
3. Raise Web lines/statements thresholds from `50` to `60` if observed runtime supports it.

## Exit Criteria

1. `Tests` workflow passes without timeout and lint failures.
2. All P1 Web modules have direct behavior tests.
3. Agent plugin core branch coverage measurably increases versus current probes.
4. API split-module gap list is reduced based on runtime coverage evidence (not static-import artifacts only).
