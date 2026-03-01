# Coverage Priorities Summary

Generated: 2026-03-01
Worktree: `worktree/test-coverage-gap-plan-20260301`

## Scope and Inputs

- Regenerated import-map coverage via `python3 scripts/coverage_map.py`.
- Updated `scripts/coverage_map.py` to parse Python imports with AST so `from x import y` module imports are counted.
- Reviewed CI workflow gates in `.github/workflows/test.yml`.
- Reviewed support-bundle and observability regression tests:
  - `api/tests/test_support_bundles.py`
  - `api/tests/test_services_support_bundle.py`
  - `tests/scripts/test_support_bundle_triage_drill.py`
  - `tests/test_prometheus_alert_rules.py`
  - `tests/test_grafana_dashboards.py`

Note: this import-map is still a static signal. It does not prove line-level runtime coverage for framework-dispatched code paths.

## Current Snapshot (Import-Map)

- API: 123 source, 119 covered, 5 uncovered.
- Agent: 82 source, 57 covered, 25 uncovered.
- Web: 186 source, 181 covered, 5 uncovered.

### API uncovered (5)

- `api/app/agent_auth.py`
- `api/app/routers/dashboard.py`
- `api/app/routers/labs_configs.py`
- `api/app/routers/labs_node_states.py`
- `api/app/utils/time.py`

### Agent uncovered (25)

- `agent/http_client.py`
- `agent/image_cleanup.py`
- `agent/n9kv_poap.py`
- `agent/network/cmd.py`
- `agent/providers/naming.py`
- `agent/routers/__init__.py`
- `agent/routers/admin.py`
- `agent/routers/console.py`
- `agent/routers/health.py`
- `agent/routers/interfaces.py`
- `agent/routers/nodes.py`
- `agent/routers/overlay.py`
- `agent/routers/ovs_plugin.py`
- `agent/schemas/admin.py`
- `agent/schemas/base.py`
- `agent/schemas/console.py`
- `agent/schemas/deploy.py`
- `agent/schemas/enums.py`
- `agent/schemas/images.py`
- `agent/schemas/labs.py`
- `agent/schemas/network.py`
- `agent/schemas/node_lifecycle.py`
- `agent/schemas/overlay.py`
- `agent/schemas/plugin.py`
- `agent/schemas/provisioning.py`

### Web uncovered (5)

- `web/src/components/AdminMenuButton.tsx`
- `web/src/studio/components/ConfigRebootConfirmModal.tsx`
- `web/src/studio/components/InfraView/AgentNode.tsx`
- `web/src/studio/components/InfraView/DetailPanel.tsx`
- `web/src/studio/components/InfraView/GraphLink.tsx`

## Phase 1 Progress (Implemented In This Branch)

- Added API tests:
  - `api/tests/test_worker_entrypoint.py`
  - `api/tests/test_routers_scenarios.py`
  - `api/tests/test_device_constraints.py`
- Added web tests:
  - `web/src/studio/components/TaskLogEntryModal.test.tsx`
  - `web/src/studio/components/InfraView/InfraHeader.test.tsx`
- Regenerated coverage reports and reduced import-map uncovered counts:
  - API: 8 -> 5
  - Web: 7 -> 5

## Support-Bundle Triage Readiness

Current strengths:

- API support-bundle service has broad contract tests, including degraded-observability behavior and completeness warnings (`api/tests/test_services_support_bundle.py`).
- Support-bundle router endpoints are directly exercised (`api/tests/test_support_bundles.py`).
- CI observability guardrails run support-bundle triage drill and observability canary end-to-end (`.github/workflows/test.yml`).

Primary remaining risks:

- API auth/triage-adjacent surfaces still uncovered by import-map: `agent_auth.py`, dashboard, and lab config/state routes.
- Agent uncovered set is likely inflated by static analysis limitations for app-wired route tests, so measurement confidence is weaker than desired.
- Frontend InfraView still has uncovered rendering paths (`AgentNode`, `DetailPanel`, `GraphLink`).

## Plan

### Phase 0: Measurement Hardening (1-2 days)

- Keep AST-based Python import scanning (done in this branch).
- Extend coverage map to flag low-confidence modules (e.g., FastAPI route modules only reached through app wiring).
- Publish backend coverage XML as CI artifacts and generate per-module low-coverage summary.
- Add a freshness check so `reports/test-coverage-map.json` and `reports/test-coverage-gaps.md` are regenerated in CI, not left stale.

### Phase 1: Support-Bundle Critical Gaps (completed in this branch)

- Add `api/tests/test_worker_entrypoint.py` for:
  - metrics server start behavior
  - `WORKER_EXECUTION_MODE` selection
  - Redis/RQ boot path mocking
- Add `api/tests/test_routers_scenarios.py` for:
  - YAML validation errors
  - execute path job creation and async dispatch
  - file CRUD edge cases
- Add `api/tests/test_device_constraints.py` for cat9k minimum hardware validation boundaries.
- Add web tests for `TaskLogEntryModal` and `InfraHeader` triage behavior.

### Phase 2: Broader API/Agent Gaps (3-5 days)

- Add direct unit tests for `agent/http_client.py`, `agent/providers/naming.py`, and `agent/image_cleanup.py`.
- Add schema validation tests for high-use agent schemas with parameterized fixtures.
- Add focused auth/error-path tests for agent admin/health surfaces.

### Phase 3: Gate Tightening (after Phase 1 passes)

- Introduce frontend coverage threshold in CI (currently no fail-under gate in workflow).
- Raise backend fail-under gradually:
  - API: 55 -> 65 -> 70
  - Agent: 50 -> 60 -> 65
- Add critical-module minimums for support-bundle triage path (`support_bundle`, worker metrics path, scenario execution path).

## Exit Criteria

- Import-map uncovered counts target:
  - API: <= 4
  - Agent: <= 15
  - Web: <= 3
- CI enforces frontend coverage fail-under and tighter backend thresholds.
- Support-bundle triage drill validates at least one seeded failure in each class:
  - Prometheus query failure
  - Loki service log gap
  - worker/scheduler control-plane probe issue
  and confirms manifest/completeness signals remain actionable.
