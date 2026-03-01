# Test Coverage Gap Report

This report maps tests to directly imported source files and lists uncovered files.

## api

Source files: 165. Covered: 125. Uncovered: 40.

Uncovered files:
- `api/app/agent_client/http.py`
- `api/app/agent_client/links.py`
- `api/app/agent_client/maintenance.py`
- `api/app/agent_client/node_ops.py`
- `api/app/agent_client/overlay.py`
- `api/app/agent_client/selection.py`
- `api/app/image_store/aliases.py`
- `api/app/image_store/custom_devices.py`
- `api/app/image_store/detection.py`
- `api/app/image_store/manifest.py`
- `api/app/image_store/metadata.py`
- `api/app/image_store/overrides.py`
- `api/app/image_store/paths.py`
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
- `api/app/schemas/lab.py`
- `api/app/schemas/system.py`
- `api/app/schemas/users.py`
- `api/app/schemas/webhooks.py`
- `api/app/services/catalog_identity.py`
- `api/app/services/catalog_query.py`
- `api/app/services/topology_deploy.py`
- `api/app/services/topology_resolution.py`
- `api/app/tasks/jobs_multihost.py`
- `api/app/tasks/jobs_node_reconcile.py`
- `api/app/tasks/reconciliation_db.py`
- `api/app/tasks/reconciliation_refresh.py`
- `api/app/tasks/stuck_agents.py`
- `api/app/tasks/stuck_nodes.py`

## agent

Source files: 96. Covered: 82. Uncovered: 14.

Uncovered files:
- `agent/network/overlay_state.py`
- `agent/network/overlay_vxlan.py`
- `agent/network/ovs_provision.py`
- `agent/network/plugin_handlers.py`
- `agent/network/plugin_state.py`
- `agent/network/plugin_vlan.py`
- `agent/providers/docker_config_extract.py`
- `agent/providers/docker_setup.py`
- `agent/providers/libvirt_config.py`
- `agent/providers/libvirt_n9kv.py`
- `agent/providers/libvirt_readiness.py`
- `agent/providers/libvirt_xml.py`
- `agent/vendor_registry.py`
- `agent/vendor_schema.py`

## web

Source files: 235. Covered: 186. Uncovered: 49.

Uncovered files:
- `web/src/components/isoImport/ISOImportModal.tsx`
- `web/src/components/isoImport/ISOImportProgress.tsx`
- `web/src/components/isoImport/ISOInputStep.tsx`
- `web/src/components/isoImport/ISOReviewStep.tsx`
- `web/src/components/isoImport/index.ts`
- `web/src/components/isoImport/types.ts`
- `web/src/components/isoImport/useISOUpload.ts`
- `web/src/pages/infrastructure/AgentMeshSection.tsx`
- `web/src/pages/infrastructure/DeregisterModal.tsx`
- `web/src/pages/infrastructure/HostCard.tsx`
- `web/src/pages/infrastructure/HostsTab.tsx`
- `web/src/pages/infrastructure/InfrastructurePage.tsx`
- `web/src/pages/infrastructure/MtuConfigModal.tsx`
- `web/src/pages/infrastructure/NetworkTab.tsx`
- `web/src/pages/infrastructure/NicGroupModal.tsx`
- `web/src/pages/infrastructure/badgeHelpers.ts`
- `web/src/pages/infrastructure/index.ts`
- `web/src/pages/infrastructure/infrastructureTypes.ts`
- `web/src/pages/infrastructure/useAgentUpdates.ts`
- `web/src/pages/infrastructure/useInfrastructureData.ts`
- `web/src/pages/infrastructure/useMtuConfig.ts`
- `web/src/pages/infrastructure/useNicGroups.ts`
- `web/src/studio/components/canvas/Canvas.tsx`
- `web/src/studio/components/canvas/CanvasControls.tsx`
- `web/src/studio/components/canvas/ContextMenuOverlay.tsx`
- `web/src/studio/components/canvas/index.ts`
- `web/src/studio/components/canvas/types.ts`
- `web/src/studio/components/canvas/useCanvasInteraction.ts`
- `web/src/studio/components/canvas/useCanvasViewport.ts`
- `web/src/studio/components/deviceManager/BuildJobsView.tsx`
- `web/src/studio/components/deviceManager/DeviceCatalogView.tsx`
- `web/src/studio/components/deviceManager/DeviceManager.tsx`
- `web/src/studio/components/deviceManager/ImageLibraryView.tsx`
- `web/src/studio/components/deviceManager/UploadControls.tsx`
- `web/src/studio/components/deviceManager/UploadLogsModal.tsx`
- `web/src/studio/components/deviceManager/deviceManagerTypes.ts`
- `web/src/studio/components/deviceManager/deviceManagerUtils.ts`
- `web/src/studio/components/deviceManager/index.ts`
- `web/src/studio/components/deviceManager/useDeviceFilters.ts`
- `web/src/studio/components/deviceManager/useImageFilters.ts`
- `web/src/studio/components/deviceManager/useImageManagementLog.ts`
- `web/src/studio/components/deviceManager/useImageUpload.ts`
- `web/src/studio/components/deviceManager/useIolBuildManager.ts`
- `web/src/studio/hooks/useConsoleManager.ts`
- `web/src/studio/hooks/useJobTracking.ts`
- `web/src/studio/hooks/useLabDataLoading.ts`
- `web/src/studio/hooks/useLabTopology.ts`
- `web/src/studio/hooks/useNodeStates.ts`
- `web/src/studio/studioUtils.ts`
