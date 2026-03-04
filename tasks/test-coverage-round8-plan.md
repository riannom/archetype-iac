# Test Coverage Round 8 — Gap Analysis & Improvement Plan

_Generated 2026-03-04 from full codebase analysis_

## Executive Summary

After 7 rounds of test coverage expansion (991+ tests added in round 6 alone, 1250+ in rounds 7-10), the codebase has strong overall coverage. This analysis identifies the **remaining gaps** organized by priority and effort.

**Current state**: ~195 API test files, ~60 agent test files, ~173 web test files
**Remaining gaps**: Concentrated in router endpoints, agent startup/console, and web data-loading hooks

---

## Phase 1: API — Untested Endpoints & Security-Sensitive Code

**Est. ~15 test files, ~400 tests**

### P1.1 — Router Endpoints with Zero Coverage
| Module | Lines | Gap | Tests Needed |
|--------|-------|-----|-------------|
| `routers/lab_tests.py` | 132 | 3 endpoints (run/list/get results) completely untested | 12-15 |
| `routers/labs_configs.py` | 863 | 5 endpoints untested: `create_config_snapshot`, `list_node_config_snapshots`, `map_config_snapshot`, `download_config_snapshots`, `list_orphaned_configs` (~250 lines) | 20-25 |
| `routers/auth.py` (OIDC) | ~60 | `oidc_login` + `oidc_callback` — security-sensitive, zero tests | 8-10 |
| `routers/scenarios.py` | 180 | DELETE endpoint missing, auth checks, 404 paths, execute failures | 10-12 |
| `routers/jobs.py` | 919 | `lab_status` and `audit_log` endpoints — no direct HTTP tests | 6-8 |
| `routers/webhooks.py` | 300 | `list_webhook_deliveries` + `test_webhook` error paths | 8-10 |

### P1.2 — Business Logic with No Dedicated Tests
| Module | Lines | Gap | Tests Needed |
|--------|-------|-----|-------------|
| `tasks/stuck_agents.py` | 76 | Entire module untested | 6-8 |
| `services/permissions.py` | 102 | Only 2/5 methods unit-tested; RBAC priority chain, `require_lab_role`, `is_admin_or_above` | 10-12 |
| `events/publisher.py` | 117 | 7 `emit_*` functions only mocked, never directly tested for event construction | 8-10 |
| `routers/callbacks.py` helpers | ~200 | `_auto_connect_pending_links`, `_auto_reattach_overlay_endpoints` — complex, only incidental coverage | 10-12 |

### P1.3 — Shallow Coverage Deepening
| Module | Lines | Gap | Tests Needed |
|--------|-------|-----|-------------|
| `services/audit.py` | 49 | Exception-swallowing path untested | 2-3 |
| `services/log_parser.py` | 179 | Timestamp edge cases, malformed input, limit behavior | 6-8 |
| `services/metrics_service.py` | 337 | Dashboard metrics, per-container breakdowns barely touched | 10-12 |
| `tasks/live_links.py` | 638 | Cross-host creation, multi-host teardown, `_sync_oper_state` | 8-10 |
| `tasks/live_nodes.py` | 433 | Error paths in deploy/destroy, `_cleanup_node_records` | 6-8 |
| `agent_client/maintenance.py` | 390 | 11 wrapper functions only patched, never directly unit-tested | 12-15 |
| `routers/console.py` | 230 | WebSocket auth failure, disconnection handling | 4-6 |
| `routers/images/sync.py` | 652 | `stream_image` actual file streaming (chunked, headers) | 4-6 |

---

## Phase 2: Agent — Startup, Console, Provider Gaps

**Est. ~10 test files, ~200 tests**

### P2.1 — High Priority (Complex, Zero Coverage)
| Module | Lines | Gap | Tests Needed |
|--------|-------|-----|-------------|
| `main.py` lifespan | ~200 | Entire startup/shutdown orchestration untested (Redis lock init, crash recovery, Docker plugin start, background tasks, teardown) | 15-20 |
| `providers/base.py` `_run_ssh_command` | ~60 | SSH exec with retry/timeout/output parsing — zero direct tests | 8-10 |
| `console/docker_exec.py` | 253 | `DockerConsole.start()` error branches, `read_blocking/nonblocking`, `console_session()` coroutine | 12-15 |
| `console/ssh_console.py` | 179 | SSH auth failure, OSError, read timeout, partial close | 8-10 |

### P2.2 — Medium Priority (Shallow Existing Coverage)
| Module | Lines | Gap | Tests Needed |
|--------|-------|-----|-------------|
| `providers/config_disk_inject.py` | 215 | `_mcopy_into_disk` / `_mount_copy_into_disk` / fallback path | 8-10 |
| `providers/docker_networks.py` | 554 | `recover_stale_networks`, `prune_legacy_lab_networks` — real impl never called | 8-10 |
| `network/interface_config.py` | 644 | Host helper functions, 4 `_sync_set_mtu()` variants, `detect_network_manager()` edge cases | 12-15 |
| `providers/base.py` VLAN I/O | ~100 | `_save_vlan_allocations`, `_load_vlan_allocations`, `_remove_vlan_file`, `_cleanup_orphan_vlans`, `cleanup_orphan_resources` | 10-12 |

### P2.3 — Low Priority (Small, Easily Testable)
| Module | Lines | Gap | Tests Needed |
|--------|-------|-----|-------------|
| `version.py` | ~40 | VERSION file read, GIT_SHA file read, fallback to "0.0.0" | 4-5 |
| `network/transport.py` | 80 | `get_vxlan_local_ip()` fallback chain, `_detect_local_ip()` socket failure | 4-5 |
| `network/vlan.py` | 229 | `VlanManager` failure paths, `cleanup_external_networks` | 6-8 |
| `network/ovs_vlan_tags.py` | 68 | `_parse_tag_field` edge cases | 4-5 |
| `logging_config.py` | ~80 | Exception-info serialization, non-JSON extra fields, text format | 4-5 |
| `metrics.py` | 74 | `DummyMetric` chaining, Prometheus-enabled branch | 3-4 |
| `http_client.py` | 41 | Client re-creation when closed, close when None | 3-4 |

---

## Phase 3: Web Frontend — Data Loading & Device Manager Gaps

**Est. ~8 test files, ~150 tests**

### P3.1 — Critical (Business Logic Hooks, Zero Coverage)
| Module | Lines | Gap | Tests Needed |
|--------|-------|-----|-------------|
| `hooks/useLabDataLoading.ts` | 137 | Drives entire dashboard: lab loading, agent polling, filtering | 15-20 |
| `deviceManager/useImageFilters.ts` | 150 | Filter pipeline: vendor, kind, assignment, sort with edge cases | 12-15 |
| `deviceManager/useImageManagementLog.ts` | 140 | localStorage persistence, clipboard copy fallback, log filtering | 10-12 |
| `deviceManager/deviceManagerUtils.ts` | 77 | Pure functions: formatImageLogTime/Date, normalizeBuildStatus, parseErrorMessage | 10-12 |

### P3.2 — High Priority (Component Gaps)
| Module | Lines | Gap | Tests Needed |
|--------|-------|-----|-------------|
| `deviceManager/ImageLibraryView.tsx` | ~120 | Image library tab rendering, filtering, sorting | 8-10 |
| `deviceManager/UploadLogsModal.tsx` | ~100 | Upload log display, filter tabs, search, copy, clear | 8-10 |
| `infrastructure/DeregisterModal.tsx` | ~80 | Destructive operation confirmation (running labs, tunnels check) | 6-8 |
| `infrastructure/HostsTab.tsx` | ~80 | Tab composition wrapping HostCard list, bulk-update buttons | 6-8 |
| `canvas/CanvasControls.tsx` | 44 | Zoom in/out, center, fit-to-screen, agent toggle interactions | 6-8 |

### P3.3 — Deepening Existing Coverage
| Module | Gap | Tests Needed |
|--------|-----|-------------|
| `StudioPage.tsx` | View switching (tabs), YAML import/export, topology save debounce, multi-select, lab rename | 15-20 |
| `Canvas.tsx` | Annotation editing, arrow drawing preview, elapsed timer, error indicator overlay, retry tooltip | 10-12 |
| `Dashboard.tsx` | Pagination, filter/sort via URL params, lab rename inline, SystemLogsModal | 8-10 |
| `Sidebar.tsx` | Preference loading, debounced persistence, external network add, node tab actions | 6-8 |

---

## Implementation Order

| Round | Phase | Focus | Est. Tests | Est. Files |
|-------|-------|-------|-----------|-----------|
| 8a | 1.1 | API untested endpoints | ~80 | 6 |
| 8b | 1.2 | API business logic gaps | ~40 | 4 |
| 8c | 2.1 | Agent startup + console | ~45 | 4 |
| 8d | 3.1 | Web data-loading hooks | ~50 | 4 |
| 8e | 1.3 | API shallow coverage deepening | ~55 | 8 |
| 8f | 2.2 | Agent provider/network gaps | ~40 | 4 |
| 8g | 3.2 | Web component gaps | ~35 | 5 |
| 8h | 2.3 + 3.3 | Low-priority agent + web deepening | ~60 | 8 |
| **Total** | | | **~405** | **~43** |

---

## Key Observations

1. **API routers** have the most critical gaps — 5+ endpoints with zero HTTP tests, including security-sensitive OIDC auth
2. **Agent `main.py` lifespan** is the single largest untested complex function (~200 lines, 7+ error branches) — startup failures would be silent
3. **Console subsystem** (both docker_exec and ssh_console) has almost no failure-path testing despite being user-facing
4. **Web frontend** is strongest overall (173 test files) but the device manager refactoring left several hooks/utils without coverage
5. **Previous rounds** successfully covered the core state machine, reconciliation, and lifecycle code — remaining gaps are at the edges (router layer, startup/shutdown, utilities)
6. **Re-export pattern** in web frontend creates confusing test paths but doesn't cause false coverage — tests do reach real implementations
