# Test Coverage Round 9 — Gap Analysis & Improvement Plan

## Current State Summary

After 8 rounds of systematic coverage improvements, the codebase has:

| Tier | Source Files | Test Files | Test Functions | Est. Coverage |
|------|-------------|------------|----------------|---------------|
| API | 159 modules | ~200 files | ~3,472 | 55-65% |
| Agent | 97 modules | 172 files | ~3,269 | 60-70% |
| Web | 235 files | 183 files | ~3,224 | 70-80% |
| **Total** | **491** | **555** | **~9,965** | |

---

## Critical Gaps (P0 — Must Fix)

These gaps represent foundational infrastructure where a single bug affects the entire system.

### 1. `api/app/agent_client/http.py` — Core Retry/HTTP Layer (336 lines, ~10% covered)
**Risk**: Every agent operation flows through `with_retry()` and `_safe_agent_request()`. Retry logic, exponential backoff, error classification (`AgentUnavailableError` vs `AgentJobError`), and timeout handling are completely untested.
**Impact**: A bug here breaks deploy, destroy, reconciliation, and all agent-driven operations simultaneously.
**Tests needed**: ~25 (retry success/failure, backoff timing, error classification, timeout, transport errors)

### 2. `api/app/errors.py` — Error Classification (221 lines, ~5% covered)
**Risk**: `categorize_httpx_error()` classifies exceptions into `ErrorCategory` enums used across the agent client layer. `StructuredError` dataclass wraps errors for logging. Zero tests.
**Impact**: Silent misclassification could cause incorrect retry behavior and alerting.
**Tests needed**: ~15 (each error category, edge cases, StructuredError construction)

### 3. `api/app/main.py` — Startup Lifespan (~35% covered)
**Risk**: JWT secret validation, Alembic migration fallback, catalog identity sync, admin user seeding — all untested startup paths.
**Impact**: Startup failures or silent misconfiguration in production.
**Tests needed**: ~15 (JWT validation, weak secret warning, migration fallback, admin seed variants)

### 4. `agent/routers/images.py` — Image Pull Flow (~55% covered)
**Risk**: `pull_image` endpoint and `_execute_pull_from_controller` (streaming download from controller, progress tracking, Docker load, checksum verification) have zero tests.
**Impact**: Silent breakage of image distribution to agents.
**Tests needed**: ~20 (pull flow, progress tracking, streaming errors, checksum verification)

---

## High Priority Gaps (P1 — Should Fix)

### 5. `api/app/webhooks.py` — Delivery & Error Paths (~45% covered)
- `log_delivery()` — persists webhook delivery records, zero tests
- `deliver_webhook()` error paths (Timeout, ConnectError) — untested
- `dispatch_webhook_event` multi-webhook retry with `asyncio.gather` — stub only
- **Tests needed**: ~15

### 6. `api/app/agent_client/` submodules — `links.py`, `node_ops.py`, `overlay.py` (~30% covered)
- `links.py` (425 lines): `get_lab_port_state`, `declare_port_state_on_agent`, `connect_external_on_agent` untested
- `node_ops.py` (631 lines): `deploy_to_agent`, `reconcile_nodes_on_agent`, `check_node_readiness` untested
- `overlay.py` (528 lines): `attach_overlay_interface_on_agent` partial coverage only
- **Tests needed**: ~35

### 7. `agent/network/backends/ovs_backend.py` — Delegation Methods (~35% covered)
- ~25 thin wrapper methods delegating to OVS/overlay managers are never tested
- A typo in method name or parameter mismatch only discovered at runtime
- **Tests needed**: ~15

### 8. `agent/console_extractor.py` — Top-Level Entry Points (~60% covered)
- `extract_vm_config()`, `run_vm_post_boot_commands()`, `run_vm_cli_commands()` — orchestration logic (retry loops, fallback paths, lock management) has no dedicated tests
- Underlying primitives are tested, but integration flow is not
- **Tests needed**: ~15

### 9. `api/app/routers/images/upload_docker.py` — Chunk Upload (~40% covered)
- `complete_chunk_upload()` (130 lines): archive reassembly, type detection, temp cleanup
- `_run_docker_with_progress()`: multi-stream Docker pull with progress callback
- **Tests needed**: ~15

### 10. `api/app/routers/infrastructure_nic_groups.py` — All 5 Endpoints (0% covered)
- NIC group CRUD (list, create, add member, delete member, delete group)
- Gates which physical interfaces are used for overlay networking
- **Tests needed**: ~15

---

## Medium Priority Gaps (P2 — Nice to Have)

### 11. `api/app/tasks/cleanup_base.py` — CleanupRunner (~0% covered)
- Base class for all cleanup tasks; `get_valid_lab_ids/host_ids/user_ids` determine what to keep vs delete
- **Tests needed**: ~10

### 12. `agent/providers/docker.py` — Deep Branch Coverage (~65% covered)
- `_create_lab_networks` / `_delete_lab_networks` internal logic
- `_extract_config_via_ssh` / `_extract_config_via_nvram` method bodies
- `_rename_container_interface` and `_find_interface_by_ifindex`
- **Tests needed**: ~20

### 13. `agent/providers/libvirt.py` — Node Creation Path (~70% covered)
- `_create_node_pre_sync` and `_define_domain_sync` — disk setup, domain definition
- **Tests needed**: ~10

### 14. `agent/main.py` — Docker/VXLAN Lifespan Paths (~70% covered)
- All tests force `enable_docker=False` and `enable_vxlan=False`
- Docker event listener, carrier monitor, OVS plugin startup never exercised
- **Tests needed**: ~10

### 15. `agent/routers/overlay.py` — Missing Endpoints (~70% covered)
- `overlay_port_ifindex` (sysfs reading), `test_mtu` (ICMP probe), `backfill_metadata`
- **Tests needed**: ~10

### 16. Web: `StudioPage.tsx` — Interactive Handlers (~15% covered)
- Only 20 tests out of 30+ handler functions
- `handleCreateLab`, `handleConnect`, `handleExport`, `handleLogin` all untested
- **Tests needed**: ~25

### 17. Web: `ISOImportProgress.tsx` — No Coverage
- Multi-progress-bar display for ISO import steps
- `formatBytes` utility untested
- **Tests needed**: ~8

### 18. `api/app/main.py` — Inline Routes
- `/health`, `/disk-usage`, `/metrics` endpoints have zero tests
- `CorrelationIdMiddleware` untested
- **Tests needed**: ~10

### 19. `agent/schemas/*.py` — Schema Validation (~20% covered)
- Deploy, node_lifecycle, provisioning, overlay, plugin schemas — only import-tested
- **Tests needed**: ~10

### 20. `api/app/routers/labs/operations.py` — Config Diff (~55% covered)
- `generate_config_diff()` multi-node scenarios, partial agent failures
- `cleanup_lab_orphans` actual logic (not just mock)
- **Tests needed**: ~10

---

## Implementation Plan

### Phase 9a — Critical Infrastructure (P0) — ~75 tests
| # | Module | Tests | Priority |
|---|--------|-------|----------|
| 1 | `api/app/agent_client/http.py` | 25 | P0 |
| 2 | `api/app/errors.py` | 15 | P0 |
| 3 | `api/app/main.py` (lifespan) | 15 | P0 |
| 4 | `agent/routers/images.py` (pull flow) | 20 | P0 |

### Phase 9b — High Priority Gaps (P1) — ~110 tests
| # | Module | Tests | Priority |
|---|--------|-------|----------|
| 5 | `api/app/webhooks.py` | 15 | P1 |
| 6 | `api/app/agent_client/{links,node_ops,overlay}.py` | 35 | P1 |
| 7 | `agent/network/backends/ovs_backend.py` | 15 | P1 |
| 8 | `agent/console_extractor.py` (entry points) | 15 | P1 |
| 9 | `api/app/routers/images/upload_docker.py` | 15 | P1 |
| 10 | `api/app/routers/infrastructure_nic_groups.py` | 15 | P1 |

### Phase 9c — Medium Priority (P2) — ~123 tests
| # | Module | Tests | Priority |
|---|--------|-------|----------|
| 11 | `api/app/tasks/cleanup_base.py` | 10 | P2 |
| 12 | `agent/providers/docker.py` (deep branches) | 20 | P2 |
| 13 | `agent/providers/libvirt.py` (node creation) | 10 | P2 |
| 14 | `agent/main.py` (docker/vxlan lifespan) | 10 | P2 |
| 15 | `agent/routers/overlay.py` (missing endpoints) | 10 | P2 |
| 16 | `web/StudioPage.tsx` (handlers) | 25 | P2 |
| 17 | `web/ISOImportProgress.tsx` | 8 | P2 |
| 18 | `api/app/main.py` (inline routes) | 10 | P2 |
| 19 | `agent/schemas/*.py` | 10 | P2 |
| 20 | `api/app/routers/labs/operations.py` (config diff) | 10 | P2 |

### Total: ~308 new tests across ~20 modules

---

## CI Hardening Recommendations

Beyond test content, these CI improvements would increase confidence:

1. **Make backend tests blocking** — currently `continue-on-error: true` means test failures don't block merges
2. **Add coverage thresholds per-module** — enforce minimum coverage on critical modules (agent_client, errors, webhooks)
3. **Fix pre-existing failures** — 50 failures in `test_vxlan_df_default.py` should be triaged and either fixed or marked as known-issues
4. **Coverage trend tracking** — upload coverage reports as CI artifacts (already done) but add diff-based threshold: no PR should decrease coverage

---

## Execution Strategy

1. Each phase should be a separate branch and PR
2. Run full test suite after each phase to verify no regressions
3. P0 items should be prioritized — they represent the highest systemic risk
4. Tests should follow existing patterns (class-based organization, mock/patch dependencies, FastAPI TestClient with dependency overrides)
5. Syntax-validate all new test files before committing
