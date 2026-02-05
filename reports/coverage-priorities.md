# Coverage Priorities Summary

Generated: 2026-02-05

## Snapshot (import-based coverage map)

- API: 75 source files, 39 covered, 36 uncovered.
- Agent: 41 source files, 15 covered, 26 uncovered.
- Web: 156 source files, 67 covered, 89 uncovered.

Note: This report is based on direct imports in test files and does not represent runtime line coverage.

## Priority Focus Areas

### API
- State and enforcement logic: `api/app/services/state_machine.py`, `api/app/tasks/state_enforcement.py`.
- Link validation and reconciliation: `api/app/services/link_validator.py`, `api/app/tasks/link_reconciliation.py`, `api/app/tasks/live_links.py`.
- Interface mapping and device catalog logic: `api/app/services/interface_mapping.py`, `api/app/services/device_service.py`.

### Agent
- VXLAN/VNI allocation and overlay bookkeeping: `agent/network/overlay.py`.
- Provider discovery and registry behavior: `agent/providers/registry.py`.

### Web
- Core data contexts and persistence hooks: `web/src/contexts/DeviceCatalogContext.tsx`, `web/src/contexts/ImageLibraryContext.tsx`, `web/src/studio/hooks/usePersistedState.ts`.
- Canvas state management: `web/src/studio/store/canvasStore.ts`.
- Infrastructure page shell: `web/src/pages/InfrastructurePage.tsx`.

## Deprioritized in this iteration

- Animated backgrounds and visual-only components under `web/src/components/backgrounds/animations`.
