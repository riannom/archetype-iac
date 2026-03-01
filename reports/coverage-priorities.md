# Coverage Priorities Summary

Generated: 2026-03-01
Worktree: `worktree/test-coverage-gap-plan-20260301`

## Scope and Inputs

- Regenerated import-map coverage via `python3 scripts/coverage_map.py`.
- Updated `scripts/coverage_map.py`:
  - Python import parsing now uses AST (`Import` + `ImportFrom`) and handles submodules from `from x import y`.
  - TypeScript/TSX parsing now detects standard `import ... from`, side-effect imports, and `export ... from`.
  - Test-file detection now avoids false positives for production files such as `api/app/tasks/test_runner.py`.
- Reviewed support-bundle and observability regression tests:
  - `api/tests/test_support_bundles.py`
  - `api/tests/test_services_support_bundle.py`
  - `tests/scripts/test_support_bundle_triage_drill.py`
  - `tests/test_prometheus_alert_rules.py`
  - `tests/test_grafana_dashboards.py`

Note: this import-map is a static signal. It does not prove branch/line runtime coverage.

## Current Snapshot (Import-Map)

- API: 125 source, 125 covered, 0 uncovered.
- Agent: 82 source, 82 covered, 0 uncovered.
- Web: 186 source, 186 covered, 0 uncovered.

## New Test Coverage Added In This Iteration

- Web component coverage:
  - `web/src/components/AdminMenuButton.test.tsx`
  - `web/src/studio/components/ConfigRebootConfirmModal.test.tsx`
  - `web/src/studio/components/InfraView/AgentNode.test.tsx`
  - `web/src/studio/components/InfraView/GraphLink.test.tsx`
  - `web/src/studio/components/InfraView/DetailPanel.test.tsx`
- Agent utility/behavior coverage:
  - `agent/tests/test_http_client.py`
  - `agent/tests/test_image_cleanup.py`
  - `agent/tests/test_providers_naming.py`
  - `agent/tests/test_network_cmd.py`
  - `agent/tests/test_n9kv_poap.py`
- Support-bundle router hardening:
  - Expanded `api/tests/test_support_bundles.py` with download/error-path and completeness-preview edge cases.
- Import-smoke coverage for app-wired modules that static mapping previously missed:
  - `agent/tests/test_uncovered_module_imports_batch4.py`
  - `api/tests/test_uncovered_module_imports_batch2.py`

## Validation Run

- `pytest -q agent/tests/test_http_client.py agent/tests/test_image_cleanup.py agent/tests/test_providers_naming.py agent/tests/test_uncovered_module_imports_batch4.py`
  - Result: 15 passed.
- `pytest -q agent/tests/test_network_cmd.py agent/tests/test_n9kv_poap.py`
  - Result: 7 passed.
- `make test-api-container API_TEST=tests/test_uncovered_module_imports_batch2.py`
  - Result: 2 passed (in API container).
- `make test-api-container API_TEST=tests/test_support_bundles.py`
  - Result: 10 passed (in API container).
- `make test-web-container WEB_TEST=...` (each new web file run individually)
  - Result: all new web tests passed.

## Support-Bundle Triage Readiness

Current strengths:

- Support-bundle API/service tests already cover degraded observability and completeness warnings.
- Observability drill and canary checks are exercised in CI.
- Static import-map no longer shows blind spots in triage-adjacent modules.

Remaining risks:

- Static import-map can be satisfied by shallow import-smoke tests; this does not guarantee deep behavior coverage.
- Support-bundle triage still depends on cross-service runtime behavior (Prometheus/Loki/worker paths) that import-map does not validate.

## Next High-Value Work

1. Replace remaining import-smoke-only modules with behavior tests for router error paths and auth boundaries.
2. Add per-module runtime coverage reports (XML/HTML artifacts) and enforce minimums for support-bundle critical modules.
3. Extend support-bundle drill fixtures to assert triage quality for mixed-failure scenarios (partial telemetry + worker backlog + stale scheduler).
