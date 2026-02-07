# Config Management Feature — Implementation Plan

## Overview
Add config version management with mapping, active config selection, deletion, download, and node reload capabilities. Configs that survive node deletion ("stranded configs") can be reassigned to new nodes of similar device type via a visual mapping interface.

## Architecture Decisions (from review)
- **#1** `mapped_to_node_id` FK on ConfigSnapshot for config reassignment
- **#2** Query-time orphan detection + `device_kind` field for type matching
- **#3** Bulk delete + workspace cleanup + active config guard
- **#4** Flexible multi-node zip download (50MB cap)
- **#5** `active_config_snapshot_id` FK on Node + set-as-startup + reload (phased)
- **#6** Consolidate UI onto snapshots data source
- **#7** Sub-components + `useConfigManager` hook
- **#8** Extract `resolve_startup_config()` function
- **#9** `ConfigService` class for all config mutations
- **#10-13** Comprehensive test strategy (existing CRUD, service, resolver, frontend)
- **#14** In-memory zip with 50MB cap
- **#15** Single LEFT JOIN for orphan detection
- **#16** Server-side `is_active` flag (N+1 prevention)
- **#17** Do nothing on content dedup (insertion dedup sufficient)

---

## Phase 1: Database & Backend Foundation

### 1.1 Alembic Migration
- [ ] Add `device_kind` (String(100), nullable) to `config_snapshots`
- [ ] Add `mapped_to_node_id` (String(36), FK to nodes.id, nullable, SET NULL on delete) to `config_snapshots`
- [ ] Add `active_config_snapshot_id` (String(36), FK to config_snapshots.id, nullable, SET NULL on delete) to `nodes`
- [ ] Add index on `config_snapshots.device_kind`
- [ ] Add index on `config_snapshots.mapped_to_node_id`

### 1.2 Model Updates
- [ ] Update `ConfigSnapshot` model with `device_kind`, `mapped_to_node_id` fields + relationship
- [ ] Update `Node` model with `active_config_snapshot_id` field + relationship
- [ ] Update `ConfigSnapshotOut` schema with new fields + `is_active` computed field
- [ ] Add `ConfigSnapshotMapping` schema (for mapping requests)
- [ ] Add `ConfigDownloadRequest` schema

### 1.3 ConfigService Class
**File:** `api/app/services/config_service.py` (new)
- [ ] `save_extracted_config(lab_id, node_name, content, snapshot_type, device_kind)` — triple-write (config_json + snapshot + workspace) + set active
- [ ] `set_active_config(node_id, snapshot_id)` — update FK + sync to config_json + push to agent
- [ ] `map_config(snapshot_id, target_node_id)` — set mapped_to_node_id + optionally set as active
- [ ] `delete_configs(lab_id, node_name=None, orphaned_only=False, snapshot_ids=None)` — bulk delete with active config guard + workspace cleanup
- [ ] `build_download_zip(lab_id, node_names, include_orphaned)` — in-memory zip with 50MB cap + metadata.json
- [ ] `list_configs_with_orphan_status(lab_id, node_name=None)` — LEFT JOIN query with is_active flag
- [ ] `get_orphaned_configs(lab_id)` — stranded configs grouped by device_kind

### 1.4 resolve_startup_config()
**File:** `api/app/services/config_service.py` or `api/app/services/topology.py`
- [ ] Extract from inline logic in topology.py:1282-1329
- [ ] Priority chain: active_config_snapshot_id → config_json["startup-config"] → latest snapshot → None
- [ ] Log which source was used
- [ ] Handle deleted active snapshot gracefully (fallback)

### 1.5 Update Extraction Flow
- [ ] Populate `device_kind` on ConfigSnapshot during extraction (look up from Node.kind)
- [ ] Auto-set `active_config_snapshot_id` when new snapshot created via extraction
- [ ] Wire ConfigService into existing extract-configs endpoint

---

## Phase 2: New API Endpoints

### 2.1 Enhanced Listing
- [ ] Update `GET /labs/{lab_id}/config-snapshots` to use LEFT JOIN orphan detection + is_active flag
- [ ] Add `orphaned_only` query param filter
- [ ] Add `device_kind` query param filter
- [ ] Return `is_active`, `is_orphaned`, `device_kind` on each snapshot

### 2.2 Config Mapping
- [ ] `POST /labs/{lab_id}/config-snapshots/{snapshot_id}/map` — map snapshot to target node
  - Body: `{ target_node_id }`
  - Validates device_kind compatibility (warn if mismatch, don't block)
  - Returns updated snapshot

### 2.3 Set Active Config
- [ ] `PUT /labs/{lab_id}/nodes/{node_name}/active-config` — set which snapshot is the startup-config
  - Body: `{ snapshot_id }`
  - Updates Node.active_config_snapshot_id + config_json + pushes to agent
  - Returns node state

### 2.4 Bulk Delete
- [ ] `DELETE /labs/{lab_id}/config-snapshots` with query params:
  - `node_name` — delete all for a node
  - `orphaned_only=true` — delete all stranded
  - `snapshot_ids` — delete specific list
- [ ] Active config guard: return warning if any snapshot is active, require `force=true`
- [ ] Workspace file cleanup alongside DB deletion

### 2.5 Zip Download
- [ ] `GET /labs/{lab_id}/config-snapshots/download` with query params:
  - `node_name` (repeatable) — specific nodes
  - `include_orphaned=true` — include stranded configs
  - `all=true` — everything
- [ ] Zip structure: `{node_name}/{timestamp}_{type}_startup-config`
- [ ] Include `metadata.json` per node
- [ ] 50MB cap with 413 response if exceeded

### 2.6 Node Reload (Phase 2 of #5)
- [ ] `POST /labs/{lab_id}/nodes/{node_name}/reload`
  - Destroys and redeploys single node
  - Preserves links (re-creates after deploy)
  - Uses active config for the fresh deploy
  - Returns job ID

---

## Phase 3: Frontend — Component Refactor

### 3.1 Break ConfigsView into Sub-Components
**Directory:** `web/src/studio/components/ConfigsView/`
- [ ] `index.tsx` — layout orchestrator + tab navigation (Snapshots | Mapping)
- [ ] `NodeList.tsx` — left panel: active nodes + stranded config groups
- [ ] `SnapshotList.tsx` — middle panel: snapshots for selected node with active indicator
- [ ] `ConfigViewer.tsx` — right panel: config content + diff viewer
- [ ] `ConfigMapping.tsx` — drag-and-drop mapping interface
- [ ] `ConfigActions.tsx` — delete, download, set-as-startup action bar

### 3.2 useConfigManager Hook
**File:** `web/src/studio/hooks/useConfigManager.ts` (new)
- [ ] Shared state: selected node, selected snapshot, compare mode
- [ ] API calls: list, delete, map, set-active, download
- [ ] Orphan grouping logic
- [ ] Active config tracking

### 3.3 Consolidate Data Source
- [ ] ConfigViewerModal reads from snapshots API instead of files API
- [ ] Or deprecate ConfigViewerModal, use inline viewer in ConfigsView

### 3.4 Restructure Tests
- [ ] Move relevant assertions from ConfigsView.test.tsx to sub-component test files
- [ ] Keep slim integration test in ConfigsView/index.test.tsx

---

## Phase 4: Frontend — New Features

### 4.1 Stranded Config UI
- [ ] NodeList shows "Orphaned Configs" section with device_kind grouping
- [ ] Visual distinction (different color/icon) for orphaned vs active nodes
- [ ] Count badges showing number of stranded configs per device type

### 4.2 Config Mapping Interface
- [ ] Drag source: orphaned config cards (showing node_name, device_kind, latest timestamp)
- [ ] Drop target: compatible nodes (same device_kind highlighted, others dimmed)
- [ ] Visual connection lines showing existing mappings
- [ ] Confirmation dialog: "Map config from [old-node] to [new-node]? Device type: cEOS"
- [ ] Option to "Set as startup-config" immediately after mapping

### 4.3 Active Config Indicator + Selection
- [ ] Star/badge icon on the active snapshot in SnapshotList
- [ ] "Set as Startup Config" button on any snapshot
- [ ] Prompt: "Apply now? This will reload [node-name]." with Yes/No/Just Set options
- [ ] Visual feedback when active config changes

### 4.4 Bulk Delete UI
- [ ] Multi-select checkboxes on snapshots
- [ ] "Delete Selected" button with count
- [ ] "Delete All Orphaned" button in orphaned configs section
- [ ] Warning dialog when deleting active config: "This is the startup-config for [node]. Delete anyway?"

### 4.5 Download UI
- [ ] "Download" button on individual nodes (zip of that node's history)
- [ ] "Download All" button with options (selected nodes, all, include orphaned)
- [ ] Progress indicator for large downloads

### 4.6 New Component Tests
- [ ] `ConfigMapping.test.tsx` — drag-and-drop, device_kind matching, confirmation
- [ ] `ConfigActions.test.tsx` — delete guard, download trigger, set-active
- [ ] `useConfigManager.test.tsx` — state management, API calls, orphan grouping
- [ ] `NodeList.test.tsx` — node display, orphan section, selection
- [ ] `SnapshotList.test.tsx` — active indicator, multi-select, compare mode

---

## Phase 5: Backend Tests

### 5.1 Existing CRUD Tests
**File:** `api/tests/test_config_snapshots.py` (new)
- [ ] Test GET /config-snapshots (list, filter by node_name)
- [ ] Test GET /config-snapshots/{node_name}/list
- [ ] Test DELETE /config-snapshots/{snapshot_id}
- [ ] Test POST /config-diff

### 5.2 ConfigService Tests
**File:** `api/tests/test_config_service.py` (new)
- [ ] save_extracted_config: happy path, dedup, device_kind population
- [ ] set_active_config: happy path, invalid snapshot_id, snapshot from different lab
- [ ] map_config: same device_kind, different device_kind (warning), invalid target
- [ ] delete_configs: by node, orphaned only, active config guard, workspace cleanup
- [ ] build_download_zip: empty, single node, multi-node, 50MB cap
- [ ] list_configs_with_orphan_status: mixed active/orphaned, is_active flag
- [ ] Edge cases: concurrent set-active, deleted active snapshot, mapping to deleted node

### 5.3 resolve_startup_config Tests
- [ ] Branch 1: active_config_snapshot_id set and valid
- [ ] Branch 2: active_config_snapshot_id set but snapshot deleted → fallback
- [ ] Branch 3: config_json["startup-config"] exists
- [ ] Branch 4: latest ConfigSnapshot exists
- [ ] Branch 5: no config anywhere → None
- [ ] Branch 6: multiple snapshots, latest used

---

## Implementation Order
1. Phase 1 (foundation) — all backend work depends on this
2. Phase 5.1 (existing CRUD tests) — safety net before modifying
3. Phase 2 (new endpoints) — backend features
4. Phase 5.2-5.3 (new backend tests) — validate new code
5. Phase 3 (frontend refactor) — restructure before adding features
6. Phase 4 (frontend features) — new UI on clean component structure

## Review
_To be filled after implementation._
