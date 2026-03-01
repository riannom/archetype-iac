# Test Coverage Gap Report

This report maps tests to directly imported source files and lists uncovered files.

## api

Source files: 123. Covered: 119. Uncovered: 5.

Uncovered files:
- `api/app/agent_auth.py`
- `api/app/routers/dashboard.py`
- `api/app/routers/labs_configs.py`
- `api/app/routers/labs_node_states.py`
- `api/app/utils/time.py`

## agent

Source files: 82. Covered: 57. Uncovered: 25.

Uncovered files:
- `agent/http_client.py`
- `agent/image_cleanup.py`
- `agent/n9kv_poap.py`
- `agent/network/cmd.py`
- `agent/providers/naming.py`
- `agent/routers/__init__.py`
- `agent/routers/admin.py`
- `agent/routers/console.py`
- `agent/routers/health.py`
- `agent/routers/interfaces.py`
- `agent/routers/nodes.py`
- `agent/routers/overlay.py`
- `agent/routers/ovs_plugin.py`
- `agent/schemas/admin.py`
- `agent/schemas/base.py`
- `agent/schemas/console.py`
- `agent/schemas/deploy.py`
- `agent/schemas/enums.py`
- `agent/schemas/images.py`
- `agent/schemas/labs.py`
- `agent/schemas/network.py`
- `agent/schemas/node_lifecycle.py`
- `agent/schemas/overlay.py`
- `agent/schemas/plugin.py`
- `agent/schemas/provisioning.py`

## web

Source files: 186. Covered: 181. Uncovered: 5.

Uncovered files:
- `web/src/components/AdminMenuButton.tsx`
- `web/src/studio/components/ConfigRebootConfirmModal.tsx`
- `web/src/studio/components/InfraView/AgentNode.tsx`
- `web/src/studio/components/InfraView/DetailPanel.tsx`
- `web/src/studio/components/InfraView/GraphLink.tsx`
