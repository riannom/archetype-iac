# Test Coverage Gap Fill Round 3 — Implementation Plan

**Status:** Completed (2026-03-04)  
**Superseded by:** `tasks/test-coverage-round7-plan.md`

## Context
Round 1 added 22 files / 312 tests. Round 2 added 18 files / 488 tests (total: 40 files, 800 tests).
Round 3 analysis reveals remaining gaps across all three components. This plan targets the highest-risk untested modules.

## Current State Summary

### API Backend
- 123 source files (~60K LOC), 137 test files, ~2,553 test functions
- **25% of source files still untested** (31 modules)
- Critical gaps: `routers/labs.py` (3,118 LOC, 0 tests), `tasks/jobs.py` (1,639 LOC, 0 tests)
- Worst LOC:test ratios: `tasks/jobs.py` (126:1), `routers/images.py` (96:1), `node_lifecycle_deploy.py` (71:1), `agent_client.py` (69:1)

### Agent
- 81 source files, 90 test files, ~1,136 test functions
- **13 modules with ZERO coverage** (5,178 LOC, 127 functions)
- **10 of 11 router modules lack direct tests** (6,961 LOC, 140 functions)
- Critical: routers.overlay (1,452 LOC), routers.interfaces (833 LOC), routers.console (824 LOC), routers.nodes (819 LOC)

### Frontend
- 276 source files, 109 test files, ~1,717 tests
- **84 untested files** (30.4%) — but 62 are background animations (low business logic)
- Excluding animations: **22 untested files, ~4,700 LOC** of business logic
- Critical: InfraView/AgentGraph.tsx (763 LOC), LinkTable.tsx (617 LOC), StudioPage.tsx (189:1 ratio)

---

## Execution Strategy
Work in 3 parallel batches. Within each batch, files ordered by production risk.

---

## Batch 1: API Backend (6 files, ~160 tests)

### 1.1 `api/tests/test_routers_labs_full.py`
**Source:** `api/app/routers/labs.py` (3,118 lines, 0 tests) — THE largest untested API file
**Tests (~35):**
- `TestCreateLab` — create with name, create with topology YAML, duplicate name error, validates permissions
- `TestGetLab` — returns lab detail, 404 missing, populates node/link counts, includes state
- `TestListLabs` — returns all, filters by state, pagination, includes counts
- `TestUpdateTopology` — syncs nodes/links from canvas JSON, creates/removes nodes, creates/removes links, normalizes interfaces
- `TestDeleteLab` — success, blocks while deployed, cascades node_states/link_states
- `TestLabUp` — enqueues deploy job, checks conflicting jobs (409), validates images available
- `TestLabDown` — enqueues destroy job, checks conflicting jobs, handles already-down
- `TestLabRestart` — enqueues restart, checks conflicting jobs
- `TestExportTopology` — returns YAML, includes startup configs, handles empty lab
- `TestImportTopology` — creates from YAML, validates format, handles malformed
**Mock pattern:** `test_client` + `auth_headers` + `sample_lab`, patch `app.tasks.jobs.*`, `app.agent_client.*`, `app.utils.agents.*`

### 1.2 `api/tests/test_tasks_jobs_full.py`
**Source:** `api/app/tasks/jobs.py` (1,639 lines, 0 dedicated tests)
**Tests (~30):**
- `TestEnqueueJob` — creates job record, enqueues to RQ, handles Redis down
- `TestJobCallbackCompletion` — success updates state, failure updates state, marks error nodes
- `TestJobCallbackCleanup` — releases deploy lock, clears transitional states
- `TestDeployJob` — orchestrates full deploy, handles per-node failure, handles topology failure
- `TestDestroyJob` — orchestrates destroy, handles partial failure, cleans up link states
- `TestRestartJob` — destroy then deploy sequence, failure on destroy aborts deploy
- `TestHasConflictingJob` — active job blocks, completed job allows, stale job allows
- `TestJobTimeout` — long-running job detection, marks as failed
- `TestJobProgress` — broadcasts progress, handles missing broadcaster
**Mock pattern:** `test_db` + mock RQ queue, patch `app.tasks.node_lifecycle.*`, `app.services.broadcaster.*`

### 1.3 `api/tests/test_routers_vendors.py`
**Source:** `api/app/routers/vendors.py` (641 lines, 0 tests)
**Tests (~20):**
- `TestListVendors` — returns all vendors, includes label/vendor/kind
- `TestGetVendor` — returns vendor detail, 404 unknown
- `TestGetVendorConfig` — returns config with interfaces/vm/console, includes custom devices
- `TestListDevices` — returns all devices, respects hidden_devices filter
- `TestGetDevice` — returns device detail, 404 unknown, resolves aliases
- `TestCustomDevices` — create custom device, update, delete, validates fields
- `TestDeviceOverrides` — get overrides, set overrides, clear overrides
- `TestHiddenDevices` — hide device, unhide, list hidden
**Mock pattern:** `test_client` + `auth_headers`, `monkeypatch` for `agent.vendors.VENDOR_CONFIGS`, `app.image_store.*`

### 1.4 `api/tests/test_services_support_bundle.py`
**Source:** `api/app/services/support_bundle.py` (689 lines, 0 tests)
**Tests (~15):**
- `TestCreateBundle` — generates zip, includes lab configs, includes agent data, includes DB dump
- `TestBundleMetadata` — includes summary, timestamps, version, user
- `TestCollectLabData` — collects topology, node states, link states, job history
- `TestCollectAgentData` — queries agent health, collects logs, handles offline agent
- `TestCollectSystemData` — DB stats, Redis stats, config settings
- `TestBundleStorage` — saves to workspace, lists bundles, download by ID, cleanup expired
**Mock pattern:** `test_db` + `tmp_path` for workspace, patch `app.agent_client.*`, mock Redis

### 1.5 `api/tests/test_metrics.py`
**Source:** `api/app/metrics.py` (857 lines, 0 tests)
**Tests (~15):**
- `TestMetricsEndpoint` — returns Prometheus format, includes lab count, includes node count
- `TestLabMetrics` — by state, by error, creation rate
- `TestNodeMetrics` — by state, by provider, by device type
- `TestJobMetrics` — active/completed/failed counts, duration histogram
- `TestAgentMetrics` — online/offline count, resource usage
- `TestResourceMetrics` — CPU/memory utilization across hosts
**Mock pattern:** `test_client`, `test_db` with sample data, no auth required (metrics endpoint is public)

### 1.6 `api/tests/test_tasks_link_cleanup.py`
**Source:** `api/app/tasks/link_cleanup.py` (514 lines, 0 tests)
**Tests (~15):**
- `TestCleanupOrphanedLinkStates` — removes states for deleted links, preserves valid states
- `TestCleanupStaleLinkStates` — removes states for destroyed labs, handles transitional
- `TestReconcileLinkStateNames` — normalizes link names, deduplicates, handles device type changes
- `TestCleanupDuplicateLinkStates` — identifies duplicates, preserves preferred, hard-deletes rest
- `TestPeriodicCleanup` — runs on schedule, handles DB errors gracefully
**Mock pattern:** `test_db` with link state fixtures, mock device resolver

---

## Batch 2: Agent (6 files, ~130 tests)

### 2.1 `agent/tests/test_routers_overlay_full.py`
**Source:** `agent/routers/overlay.py` (1,452 lines, 0 tests) — largest untested agent router
**Tests (~30):**
- `TestDeclareState` — creates tunnels, updates existing, removes stale, handles offline peer
- `TestCreateTunnel` — VXLAN tunnel setup, VNI allocation, OVS port creation, duplicate handling
- `TestDeleteTunnel` — cleanup OVS port, free VNI, handle missing tunnel
- `TestGetBridgePorts` — returns all ports with tags, empty bridge, OVS disabled
- `TestSetPortVlan` — success, invalid port, OVS disabled
- `TestGetPortState` — returns state with ifindex resolution, port not found
- `TestExternalLinks` — attach external interface, detach, VLAN tagging, bridge cleanup
- `TestMtuEndpoints` — test MTU between agents, check MTU on bridge, data plane path
- `TestRecoverTunnels` — rebuilds from OVS state, matches tunnels to link IDs
**Mock pattern:** `TestClient(app)` + disable auth, patch `get_overlay_manager`, `get_ovs_manager`, `get_docker_plugin`, `settings.enable_ovs`

### 2.2 `agent/tests/test_routers_interfaces_full.py`
**Source:** `agent/routers/interfaces.py` (833 lines, 0 tests)
**Tests (~20):**
- `TestProvisionInterfaces` — allocates VLANs, sets OVS ports, handles errors
- `TestDeclarePortState` — sets VLAN tag, resolves ifindex, handles missing port
- `TestGetEndpointVlan` — returns VLAN for port, ifindex resolution, missing port
- `TestHotConnect` — matches VLANs for same-host link, rollback on failure
- `TestListEndpoints` — returns all endpoints with VLANs, empty lab
- `TestInterfaceDiscovery` — discovers container interfaces, maps to OVS ports
**Mock pattern:** `TestClient(app)` + disable auth, patch `_resolve_ovs_port`, `_ovs_set_port_vlan`, `get_docker_plugin`, `get_ovs_manager`

### 2.3 `agent/tests/test_routers_console_full.py`
**Source:** `agent/routers/console.py` (824 lines, 0 tests)
**Tests (~18):**
- `TestConsoleWebSocket` — connects SSH backend, Docker exec backend, libvirt virsh backend
- `TestConsoleRouting` — routes by console_method (ssh, docker_exec, virsh), fallback logic
- `TestConsoleAuth` — validates token, rejects invalid, handles expired
- `TestConsoleSessionLifecycle` — registers session, unregisters on close, handles disconnect
- `TestConfigExtraction` — exec command on node, returns output, handles timeout
- `TestConsoleResize` — sends resize signal, handles invalid dimensions
**Mock pattern:** `TestClient(app)` + WebSocketTestSession, mock `ConsoleSessionRegistry`, `PtyInjector`, patch subprocess/docker/virsh

### 2.4 `agent/tests/test_routers_nodes_full.py`
**Source:** `agent/routers/nodes.py` (819 lines, 0 tests)
**Tests (~20):**
- `TestCreateNode` — Docker container, libvirt VM, invalid provider, creates workspace
- `TestStartNode` — starts stopped container/VM, already running, error state
- `TestStopNode` — stops running, already stopped, extracts config before stop
- `TestDestroyNode` — removes container/VM, cleans workspace, handles not found
- `TestGetNodeStatus` — returns state/readiness, VM vs container info
- `TestExecOnNode` — runs command, returns output, handles timeout, handles not found
- `TestNodeReconcile` — syncs actual state, handles drift
**Mock pattern:** `TestClient(app)` + disable auth, patch `get_provider_for_request`, mock DockerProvider/LibvirtProvider

### 2.5 `agent/tests/test_routers_images_full.py`
**Source:** `agent/routers/images.py` (667 lines, 0 tests)
**Tests (~18):**
- `TestReceiveImage` — Docker image load, file-based image store, validates checksum
- `TestDeleteImage` — removes Docker image, removes file, handles not found
- `TestListImages` — returns local images with details, empty list
- `TestCheckImage` — image exists true/false, checks both Docker and file store
- `TestImageValidation` — validates image format, rejects invalid, size limits
- `TestImagePull` — pulls from registry, handles auth, handles network error
**Mock pattern:** `TestClient(app)` + disable auth, patch Docker client, `os.path.exists`, `shutil`

### 2.6 `agent/tests/test_virsh_console_lock.py`
**Source:** `agent/virsh_console_lock.py` (190 lines, 0 tests)
**Tests (~12):**
- `TestLockAcquisition` — acquire succeeds, blocks concurrent, releases on exit
- `TestKillOrphans` — kills stale virsh processes, handles no orphans
- `TestTryConsoleLock` — non-blocking acquire, returns False when held
- `TestLockTimeout` — times out after deadline, raises exception
- `TestConcurrentAccess` — threading safety, prevents race conditions
- `TestOrphanDetection` — identifies stale processes by age, by domain
**Mock pattern:** `monkeypatch` for `subprocess.run`, `psutil.process_iter`, `threading.Lock`

---

## Batch 3: Frontend (6 files, ~90 tests)

### 3.1 `web/src/studio/components/InfraView/AgentGraph.test.tsx`
**Source:** `web/src/studio/components/InfraView/AgentGraph.tsx` (763 lines, 0 tests)
**Tests (~20):**
- Position computation: `computeInitialPositions` with various agent counts, spacing
- Satellite positions: nodes around agent center, respects radius
- Fit view: computes correct viewport for all nodes
- Rendering: agent zones, node circles, link lines between nodes
- Interactions: click agent, click node, hover tooltip, pan/zoom
- Edge cases: single agent, many agents, no nodes
**Mock pattern:** Mock SVG refs, `vi.mock` for theme/context, mock agent/node data

### 3.2 `web/src/studio/components/InfraView/LinkTable.test.tsx`
**Source:** `web/src/studio/components/InfraView/LinkTable.tsx` (617 lines, 0 tests)
**Tests (~15):**
- `parseEndpoint`: standard format, hyphenated node, invalid format
- `formatIface`: interface name formatting
- `sortLinks`: by source, by target, by status, by VNI
- Rendering: table headers, link rows, status indicators, VNI badges
- Filtering: by status, by host, search text
- Interactions: click link row, sort column headers
**Mock pattern:** Mock link data, `vi.mock` for theme

### 3.3 `web/src/studio/components/InfraView/index.test.tsx` (extend existing)
**Source:** `web/src/studio/components/InfraView/index.tsx` (352 lines, 1 smoke test)
**Tests (~12):**
- Tab switching: between graph and table views, persists selection
- Data aggregation: passes correct data to AgentGraph, passes correct data to LinkTable
- VNI detail panel: opens on link click, shows tunnel details, closes
- Agent detail: opens on agent click, shows host info, closes
- Empty states: no agents, no links, loading state
**Mock pattern:** Mock child components, mock studioRequest, `vi.mock` for router

### 3.4 `web/src/studio/components/DeviceConfigManager.test.tsx`
**Source:** `web/src/studio/components/DeviceConfigManager.tsx` (481 lines, 1 test)
**Tests (~12):**
- Initial load: fetches configs, loading state, error
- Config editor: opens for node, shows current config, validates syntax
- Save config: saves to API, success message, error on failure
- Batch operations: extract all configs, clear all configs
- Config comparison: diff view, side-by-side, highlights changes
**Mock pattern:** Mock studioRequest, `vi.mock` for ConfigService, theme

### 3.5 `web/src/pages/InterfaceManagerPage.extended.test.tsx`
**Source:** `web/src/pages/InterfaceManagerPage.tsx` (709 lines, 3 tests)
**Tests (~15):**
- Interface list: renders managed interfaces, shows status, groups by host
- CRUD: create interface, edit details, delete with confirm
- NIC group management: create group, add/remove members, delete group
- Agent connectivity: shows agent status per interface, handles offline
- Filtering: by host, by status, search
- Validation: interface name format, duplicate detection
**Mock pattern:** Extend existing test setup, additional API mocks for interface CRUD

### 3.6 `web/src/studio/components/NotificationsPanel.test.tsx`
**Source:** `web/src/studio/components/NotificationsPanel.tsx` (126 lines, 0 tests)
**Tests (~8):**
- Rendering: shows notification list, empty state
- Notification types: info/warning/error styling, icons
- Actions: dismiss single, dismiss all, click to navigate
- Auto-dismiss: fades after timeout, persists on hover
**Mock pattern:** Mock NotificationContext, `vi.mock` for theme

---

## Verification

After all files are written:

1. **API tests:** `cd api && python -m pytest tests/test_routers_labs_full.py tests/test_tasks_jobs_full.py tests/test_routers_vendors.py tests/test_services_support_bundle.py tests/test_metrics.py tests/test_tasks_link_cleanup.py -v`
2. **Agent tests:** `cd agent && python -m pytest tests/test_routers_overlay_full.py tests/test_routers_interfaces_full.py tests/test_routers_console_full.py tests/test_routers_nodes_full.py tests/test_routers_images_full.py tests/test_virsh_console_lock.py -v`
3. **Frontend tests:** `cd web && npx vitest run src/studio/components/InfraView/AgentGraph.test.tsx src/studio/components/InfraView/LinkTable.test.tsx src/studio/components/InfraView/index.test.tsx src/studio/components/DeviceConfigManager.test.tsx src/pages/InterfaceManagerPage.extended.test.tsx src/studio/components/NotificationsPanel.test.tsx`
4. Regression check: `cd web && npx vitest run`
5. Python syntax validation for all 12 Python files

## Summary
- **18 new test files** across 3 components (6 API, 6 Agent, 6 Frontend)
- **~380 new test functions** covering highest-risk untested production paths
- Targets the 3 largest untested API files (labs.py, jobs.py, vendors.py)
- Fills the agent router gap (overlay, interfaces, console, nodes, images)
- Covers critical frontend visualization code (InfraView, DeviceConfigManager)
- All tests follow existing project conventions
- No changes to production code required
