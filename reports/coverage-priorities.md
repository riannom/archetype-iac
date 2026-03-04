# Coverage Priorities Summary

Generated: 2026-03-04

## Inputs

- Static import-map coverage: `python3 scripts/coverage_map.py`
- Runtime probes:
  - Agent: `tests/test_plugin_state.py`, `tests/test_plugin_vlan.py`, `tests/test_plugin_handlers_extended.py`
  - API: `tests/test_tasks_node_lifecycle_stop.py`
- CI failure review: GitHub Actions run `22655838213` (`Tests` workflow)

## Current Snapshot

### Static import-map coverage

- API: 173 source, 139 covered, 34 uncovered.
- Agent: 98 source, 95 covered, 3 uncovered.
- Web: 235 source, 221 covered, 14 uncovered.

### Runtime probe coverage (sampled)

- `agent/network/plugin_handlers.py`: 100%
- `agent/network/plugin_state.py`: 53%
- `agent/network/plugin_vlan.py`: 55%
- `api/app/tasks/node_lifecycle_stop.py`: 98%

## Key Findings

1. Static import-map output is directional, not authoritative runtime coverage.
2. Some static "uncovered" modules are exercised indirectly at runtime (confirmed for agent plugin modules and API node lifecycle stop).
3. Highest-confidence real gaps are in currently uncovered Web components/hooks and deeper error/recovery branches for agent plugin state/VLAN code.

## CI Reliability Findings

Latest failing `Tests` run (`22655838213`) failed on:

- `Lint`: Ruff violations in agent tests.
- `Backend Tests`: timeout at 30 minutes while running API coverage step.

### Fixes applied

- Cleaned Ruff violations in:
  - `agent/tests/test_docker_networks_extended.py`
  - `agent/tests/test_docker_setup_extended.py`
  - `agent/tests/test_libvirt_config_extended.py`
  - `agent/tests/test_overlay_state_extended.py`
  - `agent/tests/test_overlay_vxlan_extended.py`
  - `agent/tests/test_plugin_handlers_extended.py`
- Updated `.github/workflows/test.yml`:
  - `backend-tests.timeout-minutes`: `30` -> `90`
  - API/Agent coverage command verbosity: `-v` -> `-q`

## Next Priorities

1. Fill P1 Web uncovered modules (`isoImport`, `infrastructure`, `deviceManager`, `useLabDataLoading`).
2. Raise branch depth for `agent/network/plugin_state.py` and `agent/network/plugin_vlan.py` via recovery/error-path tests.
3. Validate API split-module runtime coverage (`agent_client`, split routers/models/schemas) before writing new tests for static-only gaps.
4. After stabilization, ratchet thresholds:
   - API: 55 -> 60 (then 65)
   - Agent: 50 -> 55 (then 60)
   - Web lines/statements: 50 -> 60

Detailed implementation plan: `tasks/test-coverage-round7-plan.md`
