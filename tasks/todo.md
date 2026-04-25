# Test Coverage Push (target: 100%)

## Baseline (2026-04-24)
- Frontend (vitest, 197 files / 3690 tests): **lines 92.46%, branches 82.64%, functions 78.24%, statements 92.46%**
- API/agent: pytest needs `python3.14` or `archetype-iac-api-1` container running; deferred until tooling is up.

## Batches

### Batch 1 — scenario stepFields (DONE — merged in #124)
- [x] All 5 stepFields files → 100% lines/branches/fn
- [x] +5 test files / +29 tests; full suite 202/3719 passing; `tsc --noEmit` clean
- [x] PR #124 merged (`dc588913`); also fixed flaky agent test `test_console_websocket_docker_none_read_and_write_timeout`

## Post-Batch-1 Baseline (2026-04-25)
- Frontend: **lines 92.59%, branches 82.69%, functions 78.91%, statements 92.59%** (functions +0.67% from batch 1)

### Batch 2 — small utils + hooks (DONE — merged in #125)
- [x] `web/src/components/ui/index.ts` → 100%
- [x] `web/src/utils/resourceUsage.ts` → 100%
- [x] `web/src/studio/hooks/useStudioModals.ts` → 100%
- [x] PR #125 merged (`7ec5136e`); +3 files / +20 tests; full suite 205/3739

### Batch 3 — studio hooks (DONE — merged in #126)
- [x] `web/src/studio/hooks/useStudioAuth.ts` → 100%/94.44%/100%/100% (one branch is implicit `} finally {` cleanup)
- [x] `web/src/studio/hooks/useCanvasInteraction.ts` → 100%
- [x] PR #126 merged (`1463f1b1`); +2 files / +25 tests; full suite 207/3764

## Post-Batch-3 Baseline (2026-04-25)
- Frontend: **lines 92.76%, branches 82.88%, functions 79.06%, statements 92.76%**

### Batch 4 — context provider gap tests (DONE — merged in #127)
- [x] `web/src/contexts/UserContext.tsx` → 100%
- [x] `web/src/contexts/ImageLibraryContext.tsx` → 100%
- [x] PR #127 merged (`6166485a`); +15 tests; full suite 207/3779

## Post-Batch-4 Baseline (2026-04-25)
- Frontend: **lines 92.82%, branches 83.00%, functions 79.23%, statements 92.82%**

### Batch 5 — AnnotationProperties + scenarioTypes (DONE — merged in #128)
- [x] `web/src/studio/components/properties/AnnotationProperties.tsx` → 100%
- [x] `web/src/studio/components/scenario/scenarioTypes.ts` → 100%
- [x] PR #128 merged (`5240282d`); +2 files / +28 tests; full suite 209/3807

## Post-Batch-5 Baseline (2026-04-25)
- Frontend: **lines 92.85%, branches 83.18%, functions 80.42%, statements 92.85%**

### Batch 6 — small UI files (in progress)
- [ ] `web/src/components/ui/ToastContainer.tsx` (36 lines, 100/100/60 → 100): disabled prefs, missing prefs, position class, unknown-position fallback
- [ ] `web/src/components/ui/EmptyState.tsx` (60 lines, 100/100/80 → 100): compact=true, action.icon
- [ ] `web/src/studio/components/ViewTabBar.tsx` (45 lines, 97.8/100/83.3 → 100): no prior test; showInfraTab toggle, active class, click handlers
- [ ] `web/src/studio/components/properties/LinkProperties.tsx` (66 lines, 100/100/75 → 100): no prior test; source/target select updates, missing-node tolerance
- [ ] Commit, push, open PR, merge, clean up worktree

# Observability Follow-ups

- [ ] Trigger `.github/workflows/observability-maintenance.yml` via `workflow_dispatch` with `mode=all` on branch `observability-maintenance-ci` once GitHub CLI auth is fixed.
- [ ] Verify the uploaded `observability-maintenance-*` artifact includes:
  - `support-bundle-triage.log`
  - `support-bundle-triage.zip`
  - `canary.log`
  - `db-report-30d.log`
  - `link-reservation-drift.log`

# Move Docker Management Interfaces to OVS

## Phase 1: Remove Dead Docker Bridge Management Code
- [x] 1.1 `agent/providers/docker.py`: Remove management network creation from `deploy()` and `create_node()`
- [x] 1.2 `agent/providers/docker.py`: Remove management network deletion from `destroy()` and single-node destroy
- [x] 1.3 `agent/network/docker_plugin.py`: Remove `ManagementNetwork`, `create_management_network()`, `attach_to_management()`, `delete_management_network()`, serialization
- [x] 1.4 `agent/network/local.py`: Remove `ManagedNetwork`, management tracking, create/delete methods

## Phase 2: OVS-Backed Management + Reserved NICs
- [x] 2.1 `agent/providers/docker.py` `_create_lab_networks()`: Start loop at 0 to create eth0 network on OVS
- [x] 2.2 `agent/providers/docker.py` `_create_containers()`: Per-device primary network + reserved NIC support
- [x] 2.3 `agent/providers/docker.py` `create_node()`: Same per-device logic for single-node creation
- [ ] 2.4 `agent/providers/docker.py` `_calculate_required_interfaces()`: Account for management + reserved in total (deferred — current buffer handles this)

## Phase 3: Per-Device Data Port Offset in Interface Naming
- [x] 3.1 `api/app/services/interface_naming.py`: Add `get_data_port_start()` helper
- [x] 3.2 `api/app/services/interface_naming.py`: Update `normalize_interface()` for management + per-device offset
- [x] 3.3 `api/app/services/interface_naming.py`: Update `denormalize_interface()` for management + reserved NICs

## Phase 4: Frontend — Make Management Interfaces Wireable
- [x] 4.1 `web/src/studio/utils/interfaceRegistry.ts`: Add management to `getAvailableInterfaces()` (after data ports)
- [x] 4.2 `web/src/studio/utils/interfaceRegistry.ts`: Accept management in `isValidInterface()`

## Phase 5: Deprecate Management Config Settings
- [x] 5.1 `agent/config.py`: Mark `mgmt_network_subnet_base` and `mgmt_network_enable_nat` as deprecated

## Phase 6: Verification
- [x] 6.1 Syntax check all modified Python files (12/12 pass)
- [x] 6.2 TypeScript type check (`npx tsc --noEmit` — clean)
- [x] 6.3 Run frontend tests (1714 passed, 6 skipped)

## Additional Cleanup
- [x] Remove `ManagementNetwork`/`ManagedNetwork` from `agent/network/__init__.py` exports
- [x] Remove 3 management endpoints from `agent/routers/ovs_plugin.py`
- [x] Remove 4 management schemas from `agent/schemas/plugin.py`
- [x] Remove unused `ipaddress` import from `docker_plugin.py`
- [x] Remove/update 3 obsolete tests in agent/tests/
- [x] Zero-fill `management_networks_count` in health/status responses

## Files Modified
| File | Change Type |
|------|-------------|
| `agent/providers/docker.py` | Remove mgmt bridge, add eth0 to OVS, per-device primary network + reserved NICs |
| `agent/network/docker_plugin.py` | Remove ManagementNetwork, create/attach/delete methods, serialization, unused import |
| `agent/network/local.py` | Remove ManagedNetwork, create/delete methods, tracking, subnet conflict check |
| `agent/network/__init__.py` | Remove ManagedNetwork export |
| `agent/routers/ovs_plugin.py` | Remove 3 management endpoints |
| `agent/schemas/plugin.py` | Remove 4 management schemas |
| `agent/schemas/__init__.py` | Remove management schema imports |
| `api/app/services/interface_naming.py` | Add `get_data_port_start()`, management normalize/denormalize, per-device offset |
| `web/src/studio/utils/interfaceRegistry.ts` | Include management in available/valid interfaces |
| `agent/config.py` | Deprecate mgmt_network_* settings |
| `agent/tests/test_network_local_unit.py` | Remove 2 obsolete management tests |
| `agent/tests/test_network_migrations.py` | Remove mgmt recreation test, clean imports |
| `agent/tests/test_docker_provider_ops_corner_cases.py` | Remove mgmt deploy/destroy test, clean imports |
