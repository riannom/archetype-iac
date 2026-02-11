# Test Coverage Gap Report

This report maps tests to directly imported source files and lists uncovered files.

## api

Source files: 101. Covered: 89. Uncovered: 12.

Uncovered files:
- `api/app/events/publisher.py`
- `api/app/routers/users.py`
- `api/app/scheduler.py`
- `api/app/utils/agents.py`
- `api/app/utils/cache.py`
- `api/app/utils/http.py`
- `api/app/utils/logs.py`
- `api/app/utils/nodes.py`
- `api/app/utils/pagination.py`
- `api/app/utils/supervisor.py`
- `api/app/utils/time_range.py`
- `api/app/utils/timeouts.py`

## agent

Source files: 47. Covered: 42. Uncovered: 5.

Uncovered files:
- `agent/console_session_registry.py`
- `agent/network/transport.py`
- `agent/plugins/loader.py`
- `agent/registry.py`
- `agent/virsh_console_lock.py`

## web

Source files: 169. Covered: 159. Uncovered: 10.

Uncovered files:
- `web/src/pages/InterfaceManagerPage.tsx`
- `web/src/pages/UserManagementPage.tsx`
- `web/src/studio/components/ConfigsView/ConfigActions.tsx`
- `web/src/studio/components/ConfigsView/ConfigMapping.tsx`
- `web/src/studio/components/ConfigsView/ConfigViewer.tsx`
- `web/src/studio/components/ConfigsView/NodeList.tsx`
- `web/src/studio/components/ConfigsView/SnapshotList.tsx`
- `web/src/studio/components/ConfigsView/types.ts`
- `web/src/studio/hooks/useConfigManager.ts`
- `web/src/studio/hooks/usePolling.ts`
