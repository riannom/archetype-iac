# Test Coverage Gap Report

This report maps tests to directly imported source files and lists uncovered files.

## api

Source files: 173. Covered: 139. Uncovered: 34.

Uncovered files:
- `api/app/agent_client/http.py`
- `api/app/agent_client/links.py`
- `api/app/agent_client/maintenance.py`
- `api/app/agent_client/node_ops.py`
- `api/app/agent_client/overlay.py`
- `api/app/agent_client/selection.py`
- `api/app/image_store/paths.py`
- `api/app/models/auth.py`
- `api/app/models/base.py`
- `api/app/models/catalog.py`
- `api/app/models/infra.py`
- `api/app/models/job.py`
- `api/app/models/lab.py`
- `api/app/models/state.py`
- `api/app/models/topology.py`
- `api/app/models/webhook.py`
- `api/app/routers/images/_shared.py`
- `api/app/routers/images/library.py`
- `api/app/routers/images/sync.py`
- `api/app/routers/images/upload_docker.py`
- `api/app/routers/images/upload_vm.py`
- `api/app/routers/infrastructure_interfaces.py`
- `api/app/routers/infrastructure_nic_groups.py`
- `api/app/routers/labs/_shared.py`
- `api/app/routers/labs/crud.py`
- `api/app/routers/labs/link_states.py`
- `api/app/routers/labs/operations.py`
- `api/app/routers/labs/topology.py`
- `api/app/schemas/agents.py`
- `api/app/schemas/system.py`
- `api/app/schemas/users.py`
- `api/app/schemas/webhooks.py`
- `api/app/tasks/node_lifecycle_stop.py`
- `api/app/tasks/stuck_agents.py`

## agent

Source files: 98. Covered: 95. Uncovered: 3.

Uncovered files:
- `agent/network/plugin_handlers.py`
- `agent/network/plugin_state.py`
- `agent/network/plugin_vlan.py`

## web

Source files: 235. Covered: 221. Uncovered: 14.

Uncovered files:
- `web/src/components/isoImport/ISOImportProgress.tsx`
- `web/src/components/isoImport/index.ts`
- `web/src/pages/infrastructure/DeregisterModal.tsx`
- `web/src/pages/infrastructure/HostsTab.tsx`
- `web/src/pages/infrastructure/index.ts`
- `web/src/studio/components/canvas/CanvasControls.tsx`
- `web/src/studio/components/canvas/index.ts`
- `web/src/studio/components/deviceManager/ImageLibraryView.tsx`
- `web/src/studio/components/deviceManager/UploadLogsModal.tsx`
- `web/src/studio/components/deviceManager/deviceManagerUtils.ts`
- `web/src/studio/components/deviceManager/index.ts`
- `web/src/studio/components/deviceManager/useImageFilters.ts`
- `web/src/studio/components/deviceManager/useImageManagementLog.ts`
- `web/src/studio/hooks/useLabDataLoading.ts`
