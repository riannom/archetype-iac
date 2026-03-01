# Codebase Modularization Plan

> Generated 2026-03-01. Files ranked by size, grouped into parallelizable work items.

## Executive Summary

31 source files exceed 1,000 lines. The top 12 files (all >1,500 lines) account for the bulk of cognitive load. This plan splits them into focused modules using **re-export `__init__.py`/index patterns** so call-site changes are zero or minimal.

**Total files targeted**: 19 source files across API (12), Agent (5), Frontend (5)
**Estimated net new files**: ~55 modules extracted
**Risk mitigation**: Every package split uses `__init__.py` re-exports to preserve existing import paths

---

## Priority Tiers

| Tier | Criteria | Files | Parallel Batches |
|------|----------|-------|-----------------|
| **P0** | >2,500 lines | 5 files | Batch A (3 API) + Batch B (2 Agent) |
| **P1** | 1,500–2,500 lines | 8 files | Batch C (5 API) + Batch D (3 mixed) |
| **P2** | 1,000–1,500 lines | 6 files | Batch E (deferred / optional) |

---

## Batch A: API Mega-Files (P0, parallelizable)

### A1. `api/app/routers/images.py` → `api/app/routers/images/` package (3,289 lines)

Split into 5 files:

| New File | Content | ~Lines |
|----------|---------|--------|
| `__init__.py` | Re-export sub-routers, import shared middleware | 30 |
| `upload_docker.py` | Docker tar upload (single-shot + chunked + streaming SSE), `load_image`, chunk upload endpoints, progress dict + helpers | 900 |
| `upload_vm.py` | qcow2 upload/detect/register/confirm, IOL upload/build/retry/diagnostics | 600 |
| `library.py` | `list_image_library`, `update_image_library`, `assign/unassign_image`, `delete_image`, `get_images_for_device`, `list_qcow2`, `backfill_checksums` | 450 |
| `sync.py` | `get_image_hosts`, `push_image_to_hosts`, `stream_image`, sync job CRUD + inline Pydantic models | 600 |

**Approach**: Create `images/` directory, move file, split by section comments already in the code. Each sub-file gets its own `router = APIRouter()` with appropriate prefix/tags. `__init__.py` includes all sub-routers.

### A2. `api/app/routers/labs.py` → `api/app/routers/labs/` package (3,130 lines)

Split into 5 files:

| New File | Content | ~Lines |
|----------|---------|--------|
| `__init__.py` | Re-export sub-routers + shared utilities (`has_conflicting_job`, `get_config_by_device`) | 50 |
| `crud.py` | Lab CRUD + node state helpers (`list/create/get/update/delete/clone_lab`, `_populate_lab_counts`, `_upsert_node_states`, `_ensure_node_states_exist`) | 600 |
| `topology.py` | Topology/YAML/Graph/Layout/Bundle endpoints + resource checking | 700 |
| `link_states.py` | All link state management: upsert helpers, CRUD endpoints, hot-connect/disconnect, live links, external network, reconcile links | 700 |
| `operations.py` | IaC workflow: `check_nodes_ready`, `poll_nodes_ready`, `export_inventory`, `generate_config_diff`, `get_lab_logs`, `get_infra_notifications`, `cleanup_lab_orphans`, interface mapping endpoints | 600 |

**Note**: `topology.py` here is the router; distinct from `services/topology.py`.

### A3. `api/app/agent_client.py` → `api/app/agent_client/` package (2,769 lines)

Split into 7 files:

| New File | Content | ~Lines |
|----------|---------|--------|
| `__init__.py` | Re-exports all public symbols (preserves `agent_client.foo()` pattern across 30+ import sites) | 60 |
| `http.py` | Core HTTP: `get_http_client`, `_agent_request`, `_safe_agent_request`, `_timed_node_operation`, `with_retry`, exception classes | 340 |
| `selection.py` | Agent discovery/health: `get_healthy_agent`, `get_agent_for_lab/node`, `get_all_agents`, `check_agent_health`, `ping_agent`, `query_agent_capacity` | 450 |
| `overlay.py` | VXLAN/overlay: `compute_vxlan_port_name`, `reconcile_vxlan_ports`, `declare_overlay_state`, `setup_cross_host_link_v2`, tunnel management | 440 |
| `node_ops.py` | Per-node + lab-level: `deploy/destroy_to_agent`, `create/start/stop/destroy_node_on_agent`, `container_action`, readiness checks | 400 |
| `links.py` | Link management: `create/delete_link_on_agent`, `get_lab_ports`, `set_port_vlan`, `repair_endpoints`, external connect/detach | 280 |
| `maintenance.py` | MTU, interfaces, cleanup, configs, exec: `test_mtu`, `cleanup_*`, `extract_configs`, `exec_node`, OVS status | 400 |

**Critical**: `__init__.py` must re-export every public name since this module is imported as `from app import agent_client` and called as `agent_client.foo()` across 30+ files.

---

## Batch B: Agent Mega-Files (P0, parallelizable with Batch A)

### B1. `agent/providers/libvirt.py` → extract 4 companion files (4,592 lines)

| New File | Content | ~Lines |
|----------|---------|--------|
| `libvirt_xml.py` | `_generate_domain_xml` + disk creation (`_create_overlay_disk_sync`, `_create_data_volume_sync`) + MAC/OVMF helpers + `_patch_vjunos_svm_compat` | 700 |
| `libvirt_n9kv.py` | N9Kv POAP: `_n9kv_poap_*` helpers, `_ensure_n9kv_poap_network`, `_teardown_n9kv_poap_network`, `_resolve_management_network` | 250 |
| `libvirt_readiness.py` | `check_readiness`, boot intervention handlers (`_run_n9kv_loader_recovery`, `_run_n9kv_poap_skip`, `_run_n9kv_admin_password_setup`, `_run_n9kv_kernel_panic_recovery`), probe markers | 550 |
| `libvirt_config.py` | `_extract_config`, `_extract_config_via_ssh`, `_get_vm_management_ip`, config injection helpers | 350 |

**Remaining in `libvirt.py`**: ~2,200 lines — core lifecycle (deploy/destroy/start/stop/create/status) + VLAN/OVS state + `_run_libvirt` executor.

**Approach**: Extract as plain functions/classes that accept `conn` or other params. The main `LibvirtProvider` calls them via `_run_libvirt`. N9Kv and XML groups have the least coupling — extract first.

### B2. `agent/network/docker_plugin.py` → mixin extraction (3,602 lines)

The file has 15 explicit `# ===` section dividers. Extract into mixin classes:

| New File | Content | ~Lines |
|----------|---------|--------|
| `plugin_state.py` | `PluginStateMixin`: State Persistence + Stale GC + State Reconciliation + State Recovery | 990 |
| `plugin_handlers.py` | `PluginHandlersMixin`: 12 `handle_*` methods for Docker network plugin protocol | 215 |
| `plugin_vlan.py` | `PluginVlanMixin`: VLAN Management API + Carrier State Management (`hot_connect`, `hot_disconnect`, `set_carrier_state`, carrier monitor) | 845 |

**Remaining in `docker_plugin.py`**: ~1,550 lines — `DockerOVSPlugin(PluginStateMixin, PluginHandlersMixin, PluginVlanMixin)` with OVS Operations, Health, TTL, VXLAN, External, Status, Endpoint Repair, HTTP Server.

---

## Batch C: API Large Files (P1, parallelizable)

### C1. `api/app/tasks/reconciliation.py` → split into 3 files (1,863 lines)

| New File | Content | ~Lines |
|----------|---------|--------|
| `reconciliation.py` (keep) | Background monitor loop, locking helpers, `_set/clear_agent_error` | 500 |
| `reconciliation_refresh.py` | `refresh_states_from_agents`, `_check_readiness_for_nodes` | 530 |
| `reconciliation_db.py` | DB maintenance: `_ensure_link_states_for_lab`, `_backfill_placement_node_ids`, `cleanup_orphaned_node_states`, `_reconcile_single_lab` | 500 |

### C2. `api/app/tasks/jobs.py` → split into 3 files (1,789 lines)

| New File | Content | ~Lines |
|----------|---------|--------|
| `jobs.py` (keep) | Job utilities, deploy lock, preflight, webhooks, `run_agent_job` (single-host core) | 450 |
| `jobs_multihost.py` | `run_multihost_deploy`, `run_multihost_destroy` | 595 |
| `jobs_node_reconcile.py` | `run_node_reconcile`, `_create_cross_host_links_if_ready` | 195 |

### C3. `api/app/routers/infrastructure.py` → split into 3 files (1,546 lines)

| New File | Content | ~Lines |
|----------|---------|--------|
| `infrastructure.py` (keep) | Settings, agent mesh, MTU testing, transport config | 380 |
| `infrastructure_interfaces.py` | Interface config, network config, managed interfaces, transport IP helper | 640 |
| `infrastructure_nic_groups.py` | NIC group CRUD | 260 |

### C4. `api/app/schemas.py` → `api/app/schemas/` package (1,533 lines)

**Lowest risk split** — Pydantic classes have zero import cycles.

| New File | Content | ~Lines |
|----------|---------|--------|
| `__init__.py` | Re-exports everything (zero call-site changes) | 40 |
| `lab.py` | Lab, topology, canvas/layout, node/link state, hot-connect, readiness, inventory, job schemas | 500 |
| `users.py` | User, auth, permissions, audit logs, user preferences, support bundles | 235 |
| `agents.py` | Agent mesh, MTU, interfaces, network config, managed interfaces, NIC groups, infra settings | 275 |
| `webhooks.py` | Webhook CRUD + delivery schemas | 80 |
| `system.py` | Version, logs, scenarios, tests, config snapshots | 210 |

### C5. `api/app/services/topology.py` → extract helpers (1,544 lines)

| New File | Content | ~Lines |
|----------|---------|--------|
| `topology.py` (keep) | `TopologyService` class with node/link queries, import/export, normalization | 900 |
| `topology_deploy.py` | `build_deploy_topology`, `_node_to_deploy_dict`, `_link_to_deploy_dict`, `graph_to_deploy_topology` (pure data transforms, no DB) | 330 |
| `topology_resolution.py` | `resolve_node_image`, `resolve_device_kind`, `resolve_effective_max_ports`, `NodePlacementInfo`, `TopologyAnalysisResult` | 155 |

---

## Batch D: Agent + Frontend Large Files (P1, parallelizable)

### D1. `agent/vendors.py` → split into 3 files (2,738 lines)

| New File | Content | ~Lines |
|----------|---------|--------|
| `vendor_schema.py` | All dataclasses: `InterfaceConfig`, `ResourceConfig`, `VMConfig`, `ConsoleConfig`, `ReadinessConfig`, `VendorConfig`, etc. | 470 |
| `vendor_registry.py` | `VENDOR_CONFIGS` dict literal (the ~80-device data blob) | 1,650 |
| `vendors.py` (keep) | All builder/accessor functions, `ContainerRuntimeConfig`, `LibvirtRuntimeConfig`, `ConfigExtractionSettings`, `get_vendors_for_ui` | 650 |

**Note**: `api/app/services/device_resolver.py` imports from `vendors.py` with a fallback. Preserve the `vendors.py` public API surface.

### D2. `agent/network/ovs.py` → extract VlanAllocator + provisioning (2,106 lines)

| New File | Content | ~Lines |
|----------|---------|--------|
| `vlan.py` (expand existing) | Move `VlanAllocator` class here (already has companion file) | +266 |
| `ovs_provision.py` | Port provisioning: PID lookup, veth creation, `ensure_port`, `remove_port`, container namespace operations | 750 |
| `ovs.py` (keep) | `OVSNetworkManager` core: init, port state, link management, external interface, reconciliation | 1,100 |

### D3. `web/src/pages/InfrastructurePage.tsx` → directory structure (3,147 lines)

Extract into `web/src/pages/infrastructure/`:

| New File | Purpose | ~Lines |
|----------|---------|--------|
| `InfrastructurePage.tsx` | Composition shell | 100 |
| `infrastructureTypes.ts` | 15+ interfaces (`InfraSettings`, `AgentMeshNode`, etc.) | 120 |
| `useInfrastructureData.ts` | `loadMesh`, `loadHosts`, `loadLatestVersion` + 10s polling | 150 |
| `useNetworkConfig.ts` | `loadNetworkConfigs`, `loadManagedInterfaces`, `loadNicGroups` + 30s polling | 120 |
| `useAgentUpdates.ts` | Update statuses, triggers, 2s in-progress polling | 150 |
| `useMtuConfig.ts` | MTU modal state + handlers | 100 |
| `useNicGroups.ts` | NIC group modal state + handlers | 80 |
| `HostCard.tsx` | Single host card with resource bars, containers, VMs, images | 300 |
| `HostsTab.tsx` | Grid of `HostCard` components | 100 |
| `NetworkTab.tsx` | Global settings, network config, managed interfaces, NIC groups | 250 |
| `AgentMeshSection.tsx` | Mesh visualization + link test table | 150 |
| `MtuConfigModal.tsx` | MTU configuration modal | 80 |
| `DeregisterModal.tsx` | Agent deregister confirmation | 60 |
| `NicGroupModal.tsx` | NIC group create/member modals | 80 |

### D4. `web/src/studio/StudioPage.tsx` → extract hooks (2,268 lines)

| New File | Purpose | ~Lines |
|----------|---------|--------|
| `studio/studioUtils.ts` | `generateContainerName`, `buildGraphNodes`, `buildGraphLinks` (pure functions) | 120 |
| `studio/hooks/useLabTopology.ts` | nodes/links/annotations + refs, save/load layout & topology | 250 |
| `studio/hooks/useNodeStates.ts` | `nodeStates`, `runtimeStates` (derived), optimistic guard, WS handler | 200 |
| `studio/hooks/useConsoleManager.ts` | `consoleWindows`, `dockedConsoles`, 14 console handlers | 300 |
| `studio/hooks/useJobTracking.ts` | `jobs`, `taskLog`, polling, WS job progress handler | 200 |
| `studio/hooks/useLabDataLoading.ts` | Labs, agents, system metrics loading + polling effects | 150 |

**Result**: `StudioPage.tsx` → ~300-line composition shell.

### D5. `web/src/studio/components/DeviceManager.tsx` → directory structure (2,130 lines)

Extract into `web/src/studio/components/deviceManager/`:

| New File | Purpose | ~Lines |
|----------|---------|--------|
| `DeviceManager.tsx` | Composition shell | 150 |
| `deviceManagerTypes.ts` | 10+ interfaces, constants | 80 |
| `deviceManagerUtils.ts` | Pure formatters | 40 |
| `useImageUpload.ts` | Upload state + chunked upload + qcow2 confirm flow | 300 |
| `useIolBuildManager.ts` | IOL build statuses, polling, retry/ignore actions | 200 |
| `useDeviceFilters.ts` | 5 persisted device filter states + `filteredDevices` | 80 |
| `useImageFilters.ts` | 5 persisted image filter states + `filteredImages` | 80 |
| `useImageManagementLog.ts` | Upload logs modal state + filtering | 100 |
| `BuildJobsView.tsx` | IOL build jobs tab (current + historical tables) | 200 |
| `DeviceCatalogView.tsx` | Left panel device list with filters | 250 |
| `ImageLibraryView.tsx` | Right panel image list with drag-drop | 250 |
| `UploadControls.tsx` | Upload buttons, progress bars, qcow2 pending dialog | 150 |
| `UploadLogsModal.tsx` | Upload/management logs modal | 100 |

### D6. Cross-cutting: `web/src/utils/chunkedUpload.ts` (shared utility)

Both `ISOImportModal.tsx` and `DeviceManager.tsx` implement near-identical chunked upload logic. Extract a shared `chunkedUpload({ file, endpoint, chunkSize, onProgress, signal })` async function (~80 lines).

---

## Batch E: Optional / Deferred (P2)

These files are 1,000–1,500 lines and internally cohesive. Split only if they grow further or become pain points.

| File | Lines | Recommended Action |
|------|-------|--------------------|
| `api/app/image_store.py` | 1,489 | Optional: `image_store/` package (paths, detection, manifest, custom_devices) |
| `api/app/tasks/node_lifecycle_deploy.py` | 1,462 | Optional: extract `StopMixin` (~340 lines) |
| `api/app/services/catalog_service.py` | 1,297 | Optional: split read path vs write path |
| `api/app/models.py` | 1,230 | Optional: `models/` package (requires Alembic `env.py` update) |
| `api/app/tasks/image_sync.py` | 1,220 | Leave as-is |
| `api/app/tasks/link_reconciliation.py` | 1,202 | Leave as-is |
| `api/app/routers/iso.py` | 1,181 | Leave as-is |
| `api/app/tasks/job_health.py` | 1,101 | Optional: split job vs node health |
| `api/app/tasks/state_enforcement.py` | 1,063 | Leave as-is |
| `agent/providers/docker.py` | 3,674 | Extract `docker_setup.py` (~500 lines), expand existing `docker_networks.py` (+400 lines) |
| `agent/network/overlay.py` | 1,951 | Optional: extract per-link tunnel to `link_tunnel.py` |
| `agent/console_extractor.py` | 1,596 | Leave as-is |
| `web/src/studio/components/Canvas.tsx` | 1,399 | Optional: extract `useCanvasViewport`, `useCanvasInteraction`, annotation/link sub-components |
| `web/src/components/ISOImportModal.tsx` | 1,180 | Optional: extract step components + hooks (benefits from shared chunkedUpload) |

---

## Execution Strategy

### Parallelism Map

```
Batch A (API routers + agent_client)     Batch B (Agent providers + plugin)
  A1: images.py    ──────────┐             B1: libvirt.py  ──────────┐
  A2: labs.py      ──────────┤ parallel    B2: docker_plugin.py ─────┤ parallel
  A3: agent_client.py ───────┘             ───────────────────────────┘
          │                                          │
          └────────────── gate: tests pass ──────────┘
                              │
Batch C (API tasks/schemas)              Batch D (Agent vendors + Frontend)
  C1: reconciliation.py ─────┐             D1: vendors.py ──────────┐
  C2: jobs.py ────────────────┤             D2: ovs.py ─────────────┤
  C3: infrastructure.py ─────┤ parallel    D3: InfrastructurePage ──┤ parallel
  C4: schemas.py ─────────────┤             D4: StudioPage ──────────┤
  C5: topology.py ────────────┘             D5: DeviceManager ───────┤
                                            D6: chunkedUpload ───────┘
```

### Per-Item Checklist

For each split:
1. Create the new directory/package structure
2. Move code with `__init__.py` re-exports (zero call-site changes)
3. Run `python3 -c "import ast; ast.parse(open('file').read())"` for each new Python file
4. Run `npx tsc --noEmit` for each new TypeScript file
5. Run existing tests (`npx vitest run` for frontend)
6. Verify no circular imports (`python3 -c "from app.MODULE import *"`)
7. Commit each batch independently

### Risk Assessment

| Risk | Mitigation |
|------|------------|
| Circular imports (Python) | `__init__.py` re-exports break cycles; test with import check |
| Alembic migration breaks | Only `models.py` split (P2) carries this risk; deferred |
| TypeScript import path changes | Co-located barrel exports (`index.ts`) preserve paths |
| Shared state in mixin splits | Mixins access `self.*` naturally; type hints via Protocol |
| Call-site churn | `__init__.py` / barrel re-exports make splits transparent |
