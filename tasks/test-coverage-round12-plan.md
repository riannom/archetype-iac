# Test Coverage Round 12 — Gap Analysis & Improvement Plan

## Executive Summary

After 11 rounds of coverage work (~12,300+ total tests), the project has broad coverage but **depth remains uneven**. This round targets the highest-risk gaps: complex internal functions with thin coverage, untested monitor/scheduler loops, streaming paths, and frontend pages with low test density.

**Key finding:** No source files are completely untested. The problem is uniformly about **depth, not breadth** — private helper functions doing the real work are under-tested while entry-point functions have happy-path coverage.

---

## Cross-Cutting Themes

1. **Monitor/scheduler loops untested**: `state_enforcement_monitor`, `disk_cleanup_monitor`, `link_reconciliation_monitor` — always-running background tasks with zero direct tests
2. **Streaming paths thin**: `stream_image`, `stream_import_progress`, `_load_image_streaming`, agent `receive_image` chunked upload — hard to test but high-risk
3. **Private convergence functions**: `run_same_host_convergence` (583 lines), `run_cross_host_port_convergence` (753 lines), `_is_enforceable` (126 lines of conditionals) — core networking logic with surface-only tests
4. **Frontend page-level gaps**: `UserManagementPage` (852 lines, 18 tests), `InterfaceManagerPage` (709 lines, 22 tests), `LogsView` (726 lines, 16 tests) — complex stateful UIs

---

## Phase 1: Critical Depth Gaps — ~80 tests

### API (50 tests)

| Module | Lines | Current Tests | Target | New Tests |
|--------|-------|---------------|--------|-----------|
| `tasks/link_reconciliation.py` | 1,211 | 47 | Convergence functions | ~15 |
| `services/topology.py` (TopologyService) | 1,270 | ~25 | `export_to_graph`, `analyze_placements`, `to_topology_yaml_for_host`, `migrate_from_yaml_file` | ~15 |
| `tasks/state_enforcement.py` | 1,066 | 126 | `_is_enforceable` deep branches, `state_enforcement_monitor` loop | ~10 |
| `routers/labs/link_states.py` | 1,181 | 35 | `_upsert_link_states`, `_get_or_create_link_definition`, `set_all_links_desired_state` | ~10 |

**Rationale:** These are the core networking convergence engine, topology mutation layer, and state enforcement decision tree. Bugs here cause silent failures in production.

### Agent (15 tests)

| Module | Lines | Current Tests | Target | New Tests |
|--------|-------|---------------|--------|-----------|
| `network/local.py` | 694 | ~15 | `attach_to_bridge`, `create_link_ovs`, `delete_link_ovs`, `get_status`, `cleanup_lab` | ~15 |

**Rationale:** Core OVS same-host networking paths used by every multi-node lab. ~350 lines of untested OVS logic.

### Web (15 tests)

| Module | Lines | Current Tests | Target | New Tests |
|--------|-------|---------------|--------|-----------|
| `pages/UserManagementPage.tsx` | 852 | 18 | Password modal, OIDC toggle, permission grant/revoke, delete confirm, filter/search | ~15 |

**Rationale:** Largest page file handling critical auth/admin flows, lowest test density.

---

## Phase 2: High-Impact Depth — ~120 tests

### API (60 tests)

| Module | Target | New Tests |
|--------|--------|-----------|
| `routers/iso.py` (1,181 lines) | `_import_single_image` full path, `stream_import_progress` SSE | ~15 |
| `tasks/image_sync.py` (1,220 lines) | `_wait_for_sync_and_callback`, `_run_sync_and_callback`, timeout/retry | ~12 |
| `routers/labs_node_states.py` (694 lines) | `reconcile_lab`, `refresh_node_states` polling | ~10 |
| `routers/labs_configs.py` (863 lines) | `extract_configs` full async path, `set_active_config` null-clear | ~10 |
| `routers/agents.py` (1,574 lines) | `rebuild_docker_agent`, `list_agent_interfaces`, `_mark_links_for_recovery_sync` | ~13 |

### Agent (30 tests)

| Module | Target | New Tests |
|--------|--------|-----------|
| `network/cleanup.py` (622 lines) | `_is_veth_orphaned` logic, `cleanup_ovs_orphans`, real-deletion paths | ~15 |
| `providers/docker.py` (2,904 lines) | `_create_containers` error branches, `_capture_container_vlans`, `_recover_stale_networks` | ~15 |

### Web (30 tests)

| Module | Target | New Tests |
|--------|--------|-----------|
| `studio/components/LogsView.tsx` (726 lines) | Auto-scroll, job log modal, entry expand, filter pipeline, auto-refresh | ~15 |
| `pages/InterfaceManagerPage.tsx` (709 lines) | Reservation CRUD, link repair, node selection, bulk ops | ~15 |

---

## Phase 3: Moderate Depth + Monitors — ~100 tests

### API (50 tests)

| Module | Target | New Tests |
|--------|--------|-----------|
| `tasks/jobs.py` (1,135 lines) | `_auto_extract_configs_before_destroy`, `_capture_node_ips`, agent-offline recovery | ~12 |
| `tasks/disk_cleanup.py` (668 lines) | `disk_cleanup_monitor`, aggressive mode, agent-offline | ~10 |
| `routers/labs/operations.py` (1,134 lines) | `generate_config_diff` edge cases, `cleanup_lab_orphans` agent-online path | ~10 |
| `routers/images/upload_docker.py` (1,235 lines) | Archive decompression error, chunk cancel, progress tracking | ~8 |
| `routers/images/sync.py` (652 lines) | `_execute_sync_job` streaming, `stream_image` success path | ~10 |

### Agent (20 tests)

| Module | Target | New Tests |
|--------|--------|-----------|
| `providers/libvirt.py` (3,047 lines) | `_recover_stale_network`, injection helpers, readiness intervention edges | ~10 |
| `routers/images.py` (755 lines) | `receive_image` streaming internals, checksum mismatch | ~10 |

### Web (30 tests)

| Module | Target | New Tests |
|--------|--------|-----------|
| `studio/components/DeviceConfigManager.tsx` (481 lines) | Add modal, delete confirm, filter persistence, recently-added highlight | ~10 |
| `studio/components/InfraView/LinkTable.tsx` (617 lines) | Sort columns, cross-host filter, VLAN tag display, state colors | ~10 |
| `studio/components/ScenarioPanel.tsx` (371 lines) | YAML dirty detection, save/discard, error paths, step status badges | ~10 |

---

## Phase 4: Polish & Edge Cases — ~50 tests

### API (20 tests)

| Module | Target | New Tests |
|--------|--------|-----------|
| `tasks/scenario_executor.py` (337 lines) | `_step_exec`, wait timeout/cancel, `_step_verify` integration | ~10 |
| `tasks/jobs_multihost.py` (634 lines) | Mid-deploy agent failure, partial host recovery | ~10 |

### Agent (15 tests)

| Module | Target | New Tests |
|--------|--------|-----------|
| `network/overlay_state.py` (462 lines) | `recover_link_tunnels()` edge cases, cache corruption | ~8 |
| `network/carrier_monitor.py` (303 lines) | Event-driven carrier state changes, debounce | ~7 |

### Web (15 tests)

| Module | Target | New Tests |
|--------|--------|-----------|
| `studio/components/Dashboard.tsx` (571 lines) | Rename inline edit, pending-delete timeout, theme selector | ~8 |
| `pages/AdminSettingsPage.tsx` (233 lines) | OIDC provider list, non-admin redirect | ~7 |

---

## Summary

| Phase | Tests | Files | Tier Split | Focus |
|-------|-------|-------|------------|-------|
| Phase 1 | ~80 | 6 | API 50 / Agent 15 / Web 15 | Critical convergence, topology, enforcement, OVS |
| Phase 2 | ~120 | 9 | API 60 / Agent 30 / Web 30 | Streaming, cleanup, page depth |
| Phase 3 | ~100 | 10 | API 50 / Agent 20 / Web 30 | Monitors, error recovery, components |
| Phase 4 | ~50 | 6 | API 20 / Agent 15 / Web 15 | Edge cases, polish |
| **Total** | **~350** | **~31** | **API 180 / Agent 80 / Web 90** | |

---

## Implementation Notes

1. **Test style**: Follow existing patterns — pytest fixtures with mocked DB (API), mocked subprocess/Docker/OVS (Agent), vitest + RTL (Web)
2. **Naming**: `test_{module}_round12.py` / `{Module}.round12.test.tsx`
3. **No production changes** — pure test additions
4. **Verification**: Run each tier's test suite after adding tests to confirm no regressions
5. **Priority on private functions**: This round explicitly targets the internal helper functions that prior rounds skipped (convergence engines, dedup logic, enforcement conditionals)
