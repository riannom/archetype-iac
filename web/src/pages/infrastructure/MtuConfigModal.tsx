import React from 'react';
import type {
  AgentMeshResponse,
  AgentNetworkConfig,
  InterfaceDetail,
  ManagedInterface,
} from './infrastructureTypes';
import { Select } from '../../components/ui/Select';

interface MtuConfigModalProps {
  configModalData: {
    agentId: string;
    agentName: string;
    interfaces: InterfaceDetail[];
    defaultInterface: string | null;
    networkManager: string | null;
    currentConfig: AgentNetworkConfig | null;
  };
  managedInterfaces: ManagedInterface[];
  mesh: AgentMeshResponse | null;
  mtuValue: number;
  selectedInterface: string;
  setSelectedInterface: (v: string) => void;
  desiredMtu: number;
  setDesiredMtu: (v: number) => void;
  savingMtuConfig: boolean;
  selectedTransportMode: string;
  setSelectedTransportMode: (v: string) => void;
  selectedTransportInterface: string;
  setSelectedTransportInterface: (v: string) => void;
  useTransportInterface: boolean;
  setUseTransportInterface: (v: boolean) => void;
  onSave: () => void;
  onClose: () => void;
}

const MtuConfigModal: React.FC<MtuConfigModalProps> = ({
  configModalData,
  managedInterfaces,
  mesh,
  mtuValue,
  selectedInterface,
  setSelectedInterface,
  desiredMtu,
  setDesiredMtu,
  savingMtuConfig,
  selectedTransportMode,
  setSelectedTransportMode,
  selectedTransportInterface,
  setSelectedTransportInterface,
  useTransportInterface,
  setUseTransportInterface,
  onSave,
  onClose,
}) => {
  const transportIfaces = managedInterfaces.filter(
    i => i.host_id === configModalData.agentId && i.interface_type === 'transport'
  );
  const subifaces = transportIfaces.filter(i => i.parent_interface && i.vlan_id !== null);
  const dedicatedIfaces = transportIfaces.filter(i => !i.vlan_id);
  const canSubinterface = subifaces.length > 0;
  const canDedicated = dedicatedIfaces.length > 0;
  const requiredMtu = mesh?.settings?.overlay_mtu && mesh.settings.overlay_mtu > 0
    ? mesh.settings.overlay_mtu
    : 1500;
  const hasDataPlaneMtuTest = !!mesh?.links?.some(link =>
    link.test_path === 'data_plane'
    && link.test_status === 'success'
    && link.tested_mtu !== null
    && link.tested_mtu >= requiredMtu
    && (link.source_agent_id === configModalData.agentId || link.target_agent_id === configModalData.agentId)
  );

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white dark:bg-stone-900 rounded-2xl shadow-2xl w-full max-w-lg mx-4 max-h-[90vh] overflow-y-auto">
        <div className="p-6 border-b border-stone-200 dark:border-stone-800">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-bold text-stone-900 dark:text-white flex items-center gap-2">
              <i className="fa-solid fa-ethernet text-sage-600 dark:text-sage-400"></i>
              Configure MTU - {configModalData.agentName}
            </h2>
            <button
              onClick={onClose}
              className="text-stone-400 hover:text-stone-600 dark:hover:text-stone-300 transition-colors"
            >
              <i className="fa-solid fa-times text-lg"></i>
            </button>
          </div>
        </div>

        <div className="p-6 space-y-6">
          {/* Network Manager Info */}
          {configModalData.networkManager && (
            <div className="flex items-center gap-2 text-xs text-stone-500 dark:text-stone-400 bg-stone-100 dark:bg-stone-800 px-3 py-2 rounded-lg">
              <i className="fa-solid fa-info-circle"></i>
              <span>Network Manager: <strong className="text-stone-700 dark:text-stone-300">{configModalData.networkManager}</strong></span>
              {configModalData.networkManager !== 'unknown' && (
                <span className="text-green-600 dark:text-green-400">(will persist on reboot)</span>
              )}
              {configModalData.networkManager === 'unknown' && (
                <span className="text-amber-600 dark:text-amber-400">(runtime only, may not persist)</span>
              )}
            </div>
          )}

          {/* Transport Mode */}
          <div>
            <label className="block text-sm font-medium text-stone-700 dark:text-stone-300 mb-2">
              Transport Mode
            </label>
            <Select
              value={selectedTransportMode}
              onChange={(e) => {
                const next = e.target.value;
                setSelectedTransportMode(next);
                setUseTransportInterface(next !== 'management');
                if (next === 'subinterface' && subifaces.length > 0) {
                  setSelectedTransportInterface(subifaces[0].name);
                  setSelectedInterface(subifaces[0].name);
                }
                if (next === 'dedicated' && dedicatedIfaces.length > 0) {
                  setSelectedTransportInterface(dedicatedIfaces[0].name);
                  setSelectedInterface(dedicatedIfaces[0].name);
                }
              }}
            >
              <option value="management">Management</option>
              <option value="subinterface" disabled={!canSubinterface}>
                Subinterface
              </option>
              <option value="dedicated" disabled={!canDedicated}>
                Dedicated
              </option>
            </Select>
            {!canSubinterface && (
              <p className="text-xs text-stone-400 mt-2">
                Subinterface disabled: create a transport subinterface in Managed Interfaces.
              </p>
            )}
            {!canDedicated && (
              <p className="text-xs text-stone-400 mt-2">
                Dedicated disabled: create a transport interface in Managed Interfaces.
              </p>
            )}
            {selectedTransportMode !== 'management' && !hasDataPlaneMtuTest && (
              <p className="text-xs text-amber-600 dark:text-amber-400 mt-2">
                Transport selected but MTU not verified. VXLAN will stay on management until tests pass.
              </p>
            )}

            {selectedTransportMode === 'subinterface' && canSubinterface && (
              <div className="mt-3">
                <label className="block text-xs font-medium text-stone-600 dark:text-stone-400 mb-1">
                  Transport Subinterface
                </label>
                <Select
                  value={selectedTransportInterface}
                  onChange={(e) => {
                    setSelectedTransportInterface(e.target.value);
                    if (useTransportInterface) {
                      setSelectedInterface(e.target.value);
                    }
                  }}
                >
                  {Object.entries(
                    subifaces.reduce((groups, iface) => {
                      const key = iface.parent_interface || 'unknown';
                      (groups[key] ||= []).push(iface);
                      return groups;
                    }, {} as Record<string, ManagedInterface[]>)
                  ).map(([parent, ifaces]) => (
                    <optgroup key={parent} label={`Parent: ${parent}`}>
                      {ifaces.map((iface) => (
                        <option key={iface.id} value={iface.name}>
                          {iface.parent_interface}.{iface.vlan_id} ({iface.ip_address || 'no IP'})
                        </option>
                      ))}
                    </optgroup>
                  ))}
                </Select>
              </div>
            )}

            {selectedTransportMode === 'dedicated' && canDedicated && (
              <div className="mt-3">
                <label className="block text-xs font-medium text-stone-600 dark:text-stone-400 mb-1">
                  Transport Interface
                </label>
                <Select
                  value={selectedTransportInterface}
                  onChange={(e) => {
                    setSelectedTransportInterface(e.target.value);
                    if (useTransportInterface) {
                      setSelectedInterface(e.target.value);
                    }
                  }}
                >
                  {Object.entries(
                    dedicatedIfaces.reduce((groups, iface) => {
                      const key = iface.parent_interface || 'physical';
                      (groups[key] ||= []).push(iface);
                      return groups;
                    }, {} as Record<string, ManagedInterface[]>)
                  ).map(([parent, ifaces]) => (
                    <optgroup key={parent} label={`NIC: ${parent}`}>
                      {ifaces.map((iface) => (
                        <option key={iface.id} value={iface.name}>
                          {iface.name} ({iface.ip_address || 'no IP'})
                        </option>
                      ))}
                    </optgroup>
                  ))}
                </Select>
              </div>
            )}
          </div>

          {/* Interface Selection */}
          <div>
            <label className="block text-sm font-medium text-stone-700 dark:text-stone-300 mb-2">
              Physical Interface
            </label>
            {selectedTransportMode !== 'management' && (
              <label className="flex items-center gap-2 text-xs text-stone-500 dark:text-stone-400 mb-2">
                <input
                  type="checkbox"
                  checked={useTransportInterface}
                  onChange={(e) => {
                    const checked = e.target.checked;
                    setUseTransportInterface(checked);
                    if (checked && selectedTransportInterface) {
                      setSelectedInterface(selectedTransportInterface);
                    }
                  }}
                />
                Use transport interface for MTU (recommended)
              </label>
            )}
            {configModalData.interfaces.length === 0 ? (
              <p className="text-sm text-stone-500">No physical interfaces found on this agent.</p>
            ) : (
              <Select
                value={selectedInterface}
                onChange={(e) => setSelectedInterface(e.target.value)}
                disabled={selectedTransportMode !== 'management' && useTransportInterface}
              >
                <option value="">Select an interface...</option>
                {configModalData.interfaces.map((iface) => (
                  <option key={iface.name} value={iface.name}>
                    {iface.name}
                    {iface.is_default_route ? ' (Recommended - Default Route)' : ''}
                    {' - '}MTU: {iface.mtu}
                    {iface.ipv4_addresses.length > 0 ? ` - ${iface.ipv4_addresses[0]}` : ''}
                  </option>
                ))}
              </Select>
            )}

            {/* Selected Interface Details */}
            {selectedInterface && (
              <div className="mt-3 p-3 bg-stone-50 dark:bg-stone-800/50 rounded-lg">
                {(() => {
                  const iface = configModalData.interfaces.find(i => i.name === selectedInterface);
                  if (!iface) return null;
                  return (
                    <div className="grid grid-cols-2 gap-2 text-xs">
                      <div>
                        <span className="text-stone-500">Current MTU:</span>
                        <span className="ml-2 font-mono text-stone-700 dark:text-stone-300">{iface.mtu}</span>
                      </div>
                      <div>
                        <span className="text-stone-500">State:</span>
                        <span className={`ml-2 ${iface.state === 'UP' || iface.state === 'up' ? 'text-green-600 dark:text-green-400' : 'text-stone-500'}`}>
                          {iface.state}
                        </span>
                      </div>
                      <div>
                        <span className="text-stone-500">MAC:</span>
                        <span className="ml-2 font-mono text-stone-700 dark:text-stone-300">{iface.mac || 'N/A'}</span>
                      </div>
                      <div>
                        <span className="text-stone-500">IP:</span>
                        <span className="ml-2 font-mono text-stone-700 dark:text-stone-300">
                          {iface.ipv4_addresses.length > 0 ? iface.ipv4_addresses.join(', ') : 'N/A'}
                        </span>
                      </div>
                    </div>
                  );
                })()}
              </div>
            )}
          </div>

          {/* MTU Input */}
          <div>
            <label className="block text-sm font-medium text-stone-700 dark:text-stone-300 mb-2">
              Desired MTU
            </label>
            <div className="flex items-center gap-3">
              <input
                type="number"
                min={68}
                max={9216}
                value={desiredMtu}
                onChange={(e) => setDesiredMtu(parseInt(e.target.value) || 1500)}
                className="w-32 px-3 py-2 bg-stone-100 dark:bg-stone-800 border border-stone-300 dark:border-stone-700 rounded-lg text-stone-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-sage-500 font-mono"
              />
              <span className="text-xs text-stone-500">bytes (68-9216)</span>
            </div>
            <div className="flex gap-2 mt-2">
              <button
                onClick={() => setDesiredMtu(1500)}
                className={`px-2 py-1 text-xs rounded transition-colors ${desiredMtu === 1500 ? 'bg-sage-600 text-white' : 'bg-stone-100 dark:bg-stone-800 text-stone-600 dark:text-stone-400 hover:bg-stone-200 dark:hover:bg-stone-700'}`}
              >
                1500 (Standard)
              </button>
              <button
                onClick={() => setDesiredMtu(9000)}
                className={`px-2 py-1 text-xs rounded transition-colors ${desiredMtu === 9000 ? 'bg-sage-600 text-white' : 'bg-stone-100 dark:bg-stone-800 text-stone-600 dark:text-stone-400 hover:bg-stone-200 dark:hover:bg-stone-700'}`}
              >
                9000 (Jumbo)
              </button>
            </div>
            <p className="text-xs text-stone-400 mt-2">
              For VXLAN overlay (50 byte overhead), set underlay to at least {mtuValue + 50} for {mtuValue} byte overlay MTU.
            </p>
          </div>

          {/* Warning for MTU decrease */}
          {selectedInterface && (() => {
            const iface = configModalData.interfaces.find(i => i.name === selectedInterface);
            if (iface && desiredMtu < iface.mtu) {
              return (
                <div className="p-3 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-lg">
                  <div className="flex items-start gap-2">
                    <i className="fa-solid fa-triangle-exclamation text-amber-500 mt-0.5"></i>
                    <div className="text-xs text-amber-700 dark:text-amber-300">
                      You are decreasing MTU from {iface.mtu} to {desiredMtu}. This may briefly interrupt network traffic.
                    </div>
                  </div>
                </div>
              );
            }
            return null;
          })()}
        </div>

        <div className="p-6 border-t border-stone-200 dark:border-stone-800 flex justify-end gap-3">
          <button
            onClick={onClose}
            className="px-4 py-2 glass-control text-stone-600 dark:text-stone-400 rounded-lg transition-all text-sm font-medium"
          >
            Cancel
          </button>
          <button
            onClick={onSave}
            disabled={!selectedInterface || savingMtuConfig}
            className={`px-4 py-2 rounded-lg transition-all text-sm font-medium ${
              selectedInterface && !savingMtuConfig
                ? 'bg-sage-600 hover:bg-sage-700 text-white'
                : 'bg-stone-200 dark:bg-stone-800 text-stone-400 cursor-not-allowed'
            }`}
          >
            {savingMtuConfig ? (
              <>
                <i className="fa-solid fa-spinner fa-spin mr-2"></i>
                Applying...
              </>
            ) : (
              <>
                <i className="fa-solid fa-check mr-2"></i>
                Apply MTU
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
};

export default MtuConfigModal;
