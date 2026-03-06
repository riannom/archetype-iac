# Test Coverage Round 11 — Gap Analysis & Improvement Plan

## Executive Summary

After 10 rounds of coverage work, the project has **5,296 API tests**, **3,547 agent tests**, and **3,336 web tests** (~12,179 total). Coverage is broad but depth is uneven — many modules have only happy-path tests while critical business logic paths remain untested.

This plan identifies the **highest-impact gaps** and organizes them into prioritized phases.

---

## Tier-by-Tier Gap Summary

### API — Key Gaps

| Priority | Module | Est. Coverage | Gap Description |
|----------|--------|--------------|-----------------|
| **P0** | `routers/lab_tests.py` | ~10% | All 3 endpoints broken by arg-order bug; no valid-path tests |
| **P0** | `services/topology.py` (1,270 lines) | ~30% | Only 12 tests; `migrate_from_yaml_file`, `update_from_graph` edge cases, `normalize_links_for_lab` untested |
| **P1** | `routers/iso.py` (1,181 lines) | ~25% | SSE import pipeline, `complete_upload` success, `_execute_import` untested |
| **P1** | `tasks/jobs.py` (1,135 lines) | ~55% | `_auto_extract_configs_before_destroy`, `_run_job_preflight_checks` failure branches |
| **P1** | `tasks/link_orchestration.py` (1,144 lines) | ~45% | Cross-host VXLAN setup, external network links, multi-agent teardown |
| **P1** | `services/support_bundle.py` (1,246 lines) | ~45% | Full async collection pipeline, Prometheus/Loki error paths |
| **P2** | `tasks/link_reconciliation.py` (1,211 lines) | ~55% | Cross-host convergence, same-host convergence full cycle |
| **P2** | `routers/agents.py` (1,574 lines) | ~50% | `trigger_bulk_update` concurrency, `rebuild_docker_agent` success |
| **P2** | `routers/labs/operations.py` (1,134 lines) | ~60% | `poll_nodes_ready` timeout loop, `generate_config_diff` details |
| **P2** | `routers/labs/link_states.py` (1,181 lines) | ~55% | Dedup key computation edge cases, `refresh_link_states` |
| **P2** | `tasks/image_sync.py` (1,220 lines) | ~50% | Async coordination/timeout/retry, `check_and_start_image_sync` branches |
| **P2** | `routers/labs/crud.py` | ~65% | `_upsert_node_states` dedup with `reused_old_ids` |
| **P3** | `models/catalog.py`, `models/topology.py`, `models/infra.py` | ~10% | No behavioral model tests |

### Agent — Key Gaps

| Priority | Module | Est. Coverage | Gap Description |
|----------|--------|--------------|-----------------|
| **P1** | `routers/console.py` (823 lines) | ~45% | WebSocket streaming (`_console_websocket_ssh/docker/libvirt`) untested |
| **P1** | `network/ovs_vlan_tags.py` (229 lines) | ~40% | Only 2-4 test functions for complex VLAN batch logic |
| **P1** | `network/plugin_handlers.py` (239 lines) | ~40% | Handler dispatch functions lack branch coverage |
| **P2** | `network/ovs_provision.py` (700 lines) | ~55% | `handle_container_restart()` crash recovery untested |
| **P2** | `routers/overlay.py` (1,451 lines) | ~55% | `attach_overlay_external`, `reconcile_overlay_ports` logic |
| **P2** | `providers/libvirt_n9kv.py` | ~45% | `resolve_management_network()` untested |
| **P2** | `routers/images.py` (755 lines) | ~60% | `_execute_pull_from_controller()` streaming |
| **P2** | `providers/docker_config_extract.py` (278 lines) | ~50% | Multi-node extraction fallback chains |
| **P3** | `network/overlay_state.py` (462 lines) | ~60% | `recover_link_tunnels()` edge cases |
| **P3** | `network/carrier_monitor.py` (303 lines) | ~60% | Event-driven carrier state changes |
| **P3** | `main.py` lifespan with `enable_ovs=True` | ~75% | Carrier monitor, VM refresh, Docker event listener startup |

### Web — Key Gaps

| Priority | Module | Est. Coverage | Gap Description |
|----------|--------|--------------|-----------------|
| **P1** | `canvas/Canvas.tsx` (757 lines) | ~0% direct | X-Ray overlay, elapsed timer, drag-drop, link port labels, agent zones |
| **P1** | `StudioPage.tsx` (1,295 lines) | ~45% | WS reconnect, YAML import, carrier toggle, scenario execution |
| **P2** | `TaskLogPanel.tsx` (610 lines) | ~35% | Log filtering, real-time streaming, clear-log, expandable detail |
| **P3** | `AgentAlertBanner.tsx` (159 lines) | ~15% | Multiple alerts, dismiss-all, navigation |
| **P3** | `usePolling.ts` | ~50% | Interval reset on prop change, enable toggle |
| **P3** | Animations (58 files) | ~5% | Only import smoke test |

---

## Implementation Phases

### Phase 1: Critical Gaps (P0) — ~60 tests
**Focus: Broken/missing coverage for core business logic**

- [ ] **`api/app/routers/lab_tests.py`** — Fix arg-order bug, add tests for all 3 endpoints (run, get tests, get results) with valid inputs, topology YAML fallback, error paths (~15 tests)
- [ ] **`api/app/services/topology.py`** — `update_from_graph` (add/update/remove nodes/links), `migrate_from_yaml_file`, `normalize_links_for_lab`, `build_deploy_topology` multi-host, `analyze_placements`, `get_reserved_interfaces_for_host` (~45 tests)

### Phase 2: High-Impact Gaps (P1) — ~180 tests
**Focus: Large modules with shallow coverage**

**API (~80 tests):**
- [ ] `routers/iso.py` — `complete_upload` success, `_execute_import` pipeline, `stream_import_progress` SSE, chunk edge cases (~25 tests)
- [ ] `tasks/jobs.py` — `_auto_extract_configs_before_destroy` partial failures, `_run_job_preflight_checks` image-missing/CPU branches, `_dispatch_webhook` errors (~20 tests)
- [ ] `tasks/link_orchestration.py` — `create_external_network_links` macvlan/bridge, `create_cross_host_link` VXLAN branches, `teardown_deployment_links` multi-agent errors (~20 tests)
- [ ] `services/support_bundle.py` — `build_support_bundle` collection pipeline, Prometheus/Loki query error handling, `_build_completeness_warnings` (~15 tests)

**Agent (~50 tests):**
- [ ] `routers/console.py` — WebSocket dispatch routing for SSH/Docker/libvirt, connection setup errors, auth failures (~15 tests)
- [ ] `network/ovs_vlan_tags.py` — Batch VLAN tag reading, edge cases (empty bridge, missing ports, stale tags) (~15 tests)
- [ ] `network/plugin_handlers.py` — Handler dispatch for create/delete/join/leave, error propagation (~10 tests)
- [ ] `network/ovs_provision.py` — `handle_container_restart()` recovery, `discover_existing_state()` errors (~10 tests)

**Web (~50 tests):**
- [ ] `canvas/Canvas.tsx` — X-Ray overlay rendering, transitional node timer, drag-drop handlers, link port labels, agent color zones (~25 tests)
- [ ] `StudioPage.tsx` — WS reconnect recovery, YAML import flow, carrier state toggle, scenario highlight propagation (~25 tests)

### Phase 3: Moderate Gaps (P2) — ~150 tests
**Focus: Deepening existing shallow coverage**

**API (~80 tests):**
- [ ] `tasks/link_reconciliation.py` — Cross-host port convergence, same-host convergence cycle, VLAN mismatch multi-agent (~20 tests)
- [ ] `routers/agents.py` — `trigger_bulk_update` concurrency/partial-failure, `rebuild_docker_agent` success path (~15 tests)
- [ ] `routers/labs/operations.py` — `poll_nodes_ready` timeout behavior, `generate_config_diff` detail assertions (~10 tests)
- [ ] `routers/labs/link_states.py` — Dedup key edge cases, `refresh_link_states`, `_sync_link_oper_state` (~10 tests)
- [ ] `tasks/image_sync.py` — Async coordination/timeout/retry, `check_and_start_image_sync` branches (~15 tests)
- [ ] `routers/labs/crud.py` — `_upsert_node_states` reused_old_ids dedup (~10 tests)

**Agent (~40 tests):**
- [ ] `routers/overlay.py` — `attach_overlay_external`, `disconnect_from_external`, `reconcile_overlay_ports`, `cleanup_audit` (~15 tests)
- [ ] `providers/libvirt_n9kv.py` — `resolve_management_network()`, N9Kv-specific node creation paths (~10 tests)
- [ ] `routers/images.py` — `_execute_pull_from_controller()` streaming, transfer state persistence (~10 tests)
- [ ] `providers/docker_config_extract.py` — Multi-node extraction, method fallback chains (~5 tests)

**Web (~30 tests):**
- [ ] `TaskLogPanel.tsx` — Log filtering, real-time updates, clear-log, expandable detail (~15 tests)
- [ ] `AgentAlertBanner.tsx` — Multiple alerts, dismiss-all, navigation (~8 tests)
- [ ] `usePolling.ts` — Interval change, enable toggle recovery (~7 tests)

### Phase 4: Polish (P3) — ~60 tests
**Focus: Model tests and remaining shallow areas**

- [ ] `models/catalog.py`, `models/topology.py`, `models/infra.py` — Constraint, relationship, cascade behavior (~20 tests)
- [ ] Agent `network/overlay_state.py` — `recover_link_tunnels()` edge cases (~10 tests)
- [ ] Agent `network/carrier_monitor.py` — Event-driven carrier state changes (~10 tests)
- [ ] Agent `main.py` lifespan with `enable_ovs=True` — Carrier monitor, VM refresh startup (~10 tests)
- [ ] Web animation modules — Per-animation init + tick smoke tests (~10 tests)

---

## Estimated Total: ~450 new tests across ~30 files

| Phase | Tests | Files | Tier Split |
|-------|-------|-------|------------|
| Phase 1 | ~60 | 3-4 | API only |
| Phase 2 | ~180 | 10-12 | API 80 / Agent 50 / Web 50 |
| Phase 3 | ~150 | 10-12 | API 80 / Agent 40 / Web 30 |
| Phase 4 | ~60 | 8-10 | API 20 / Agent 30 / Web 10 |

---

## Implementation Notes

1. **Test style**: Follow existing patterns — pytest fixtures with mocked DB sessions (API), mocked subprocess/Docker/libvirt (Agent), vitest + React Testing Library (Web)
2. **Naming**: Use `test_{module}_coverage.py` / `{Module}.coverage.test.tsx` to distinguish from existing tests
3. **No production changes** except the lab_tests.py arg-order bug fix (Phase 1)
4. **Verification**: Run each tier's test suite after adding tests to ensure no regressions
