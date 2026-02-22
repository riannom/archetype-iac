# Move Docker Management Interfaces to OVS

## Phase 1: Remove Dead Docker Bridge Management Code
- [ ] 1.1 `agent/providers/docker.py`: Remove management network creation from `deploy()` and `create_node()`
- [ ] 1.2 `agent/providers/docker.py`: Remove management network deletion from `destroy()` and single-node destroy
- [ ] 1.3 `agent/network/docker_plugin.py`: Remove `ManagementNetwork`, `create_management_network()`, `attach_to_management()`, `delete_management_network()`, serialization
- [ ] 1.4 `agent/network/local.py`: Remove `ManagedNetwork`, management tracking, create/delete methods

## Phase 2: OVS-Backed Management + Reserved NICs
- [ ] 2.1 `agent/providers/docker.py` `_create_lab_networks()`: Start loop at 0 to create eth0 network on OVS
- [ ] 2.2 `agent/providers/docker.py` `_create_containers()`: Per-device primary network + reserved NIC support
- [ ] 2.3 `agent/providers/docker.py` `create_node()`: Same per-device logic for single-node creation
- [ ] 2.4 `agent/providers/docker.py` `_calculate_required_interfaces()`: Account for management + reserved in total

## Phase 3: Per-Device Data Port Offset in Interface Naming
- [ ] 3.1 `api/app/services/interface_naming.py`: Add `get_data_port_start()` helper
- [ ] 3.2 `api/app/services/interface_naming.py`: Update `normalize_interface()` for management + per-device offset
- [ ] 3.3 `api/app/services/interface_naming.py`: Update `denormalize_interface()` for management + reserved NICs

## Phase 4: Frontend — Make Management Interfaces Wireable
- [ ] 4.1 `web/src/studio/utils/interfaceRegistry.ts`: Add management to `getAvailableInterfaces()` (after data ports)
- [ ] 4.2 `web/src/studio/utils/interfaceRegistry.ts`: Accept management in `isValidInterface()`

## Phase 5: Deprecate Management Config Settings
- [ ] 5.1 `agent/config.py`: Mark `mgmt_network_subnet_base` and `mgmt_network_enable_nat` as deprecated

## Phase 6: Verification
- [ ] 6.1 Syntax check all modified Python files
- [ ] 6.2 TypeScript type check (`npx tsc --noEmit`)
- [ ] 6.3 Run frontend tests (`npx vitest run`)

## Files Modified
| File | Change Type |
|------|-------------|
| `agent/providers/docker.py` | Remove mgmt bridge, add eth0 to OVS, per-device primary network + reserved NICs |
| `agent/network/docker_plugin.py` | Remove ManagementNetwork, create/attach/delete methods, serialization |
| `agent/network/local.py` | Remove ManagedNetwork, create/delete methods, tracking |
| `api/app/services/interface_naming.py` | Add `get_data_port_start()`, management normalize/denormalize, per-device offset |
| `web/src/studio/utils/interfaceRegistry.ts` | Include management in available/valid interfaces |
| `agent/config.py` | Deprecate mgmt_network_* settings |
