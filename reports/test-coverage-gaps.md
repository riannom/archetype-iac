# Test Coverage Gap Report

This report maps tests to directly imported source files and lists uncovered files.

## api

Source files: 168. Covered: 144. Uncovered: 24.

Uncovered files:
- `api/app/agent_client/selection.py`
- `api/app/models/auth.py`
- `api/app/models/base.py`
- `api/app/models/catalog.py`
- `api/app/models/infra.py`
- `api/app/models/job.py`
- `api/app/models/lab.py`
- `api/app/models/state.py`
- `api/app/models/topology.py`
- `api/app/models/webhook.py`
- `api/app/routers/images/library.py`
- `api/app/routers/images/upload_vm.py`
- `api/app/routers/infrastructure_interfaces.py`
- `api/app/routers/infrastructure_nic_groups.py`
- `api/app/routers/labs/_shared.py`
- `api/app/routers/labs/crud.py`
- `api/app/routers/labs/topology.py`
- `api/app/schemas/agents.py`
- `api/app/schemas/system.py`
- `api/app/schemas/users.py`
- `api/app/schemas/webhooks.py`
- `api/app/tasks/node_lifecycle_deploy.py`
- `api/app/tasks/node_lifecycle_stop.py`
- `api/app/utils/db.py`

## agent

Source files: 98. Covered: 96. Uncovered: 2.

Uncovered files:
- `agent/labels.py`
- `agent/network/plugin_vlan.py`

## web

Source files: 274. Covered: 256. Uncovered: 18.

Uncovered files:
- `web/src/components/backgrounds/animations/useCanvasAnimation.ts`
- `web/src/components/isoImport/index.ts`
- `web/src/hooks/useModalState.ts`
- `web/src/pages/UserCreateModal.tsx`
- `web/src/pages/UserEditModal.tsx`
- `web/src/pages/UserPasswordModal.tsx`
- `web/src/pages/infrastructure/index.ts`
- `web/src/studio/components/LogEntryDetail.tsx`
- `web/src/studio/components/Sidebar.tsx`
- `web/src/studio/components/SidebarFilters.tsx`
- `web/src/studio/components/canvas/index.ts`
- `web/src/studio/components/canvas/useCanvasTouchGestures.ts`
- `web/src/studio/components/deviceManager/index.ts`
- `web/src/studio/components/properties/DeviceNodeProperties.tsx`
- `web/src/studio/constants.tsx`
- `web/src/studio/hooks/useLabLifecycle.ts`
- `web/src/studio/hooks/useTopologyHandlers.ts`
- `web/src/types/js-yaml.d.ts`
