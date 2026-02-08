import React, { useState, useEffect } from 'react';
import { ExternalNetworkNode } from '../types';
import { apiRequest } from '../../api';

interface ExternalNetworkConfigProps {
  node: ExternalNetworkNode;
  onUpdate: (id: string, updates: Partial<ExternalNetworkNode>) => void;
  onDelete: (id: string) => void;
  agents?: { id: string; name: string }[];
}

interface ManagedInterface {
  id: string;
  host_id: string;
  host_name: string | null;
  name: string;
  interface_type: string;
  parent_interface: string | null;
  vlan_id: number | null;
  ip_address: string | null;
  desired_mtu: number;
  current_mtu: number | null;
  is_up: boolean;
  sync_status: string;
  sync_error: string | null;
}

const ExternalNetworkConfig: React.FC<ExternalNetworkConfigProps> = ({
  node,
  onUpdate,
  onDelete,
}) => {
  const [interfaces, setInterfaces] = useState<ManagedInterface[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Load external managed interfaces from infrastructure
  useEffect(() => {
    const loadInterfaces = async () => {
      setLoading(true);
      setError(null);
      try {
        const res = await apiRequest<{ interfaces: ManagedInterface[] }>(
          '/infrastructure/interfaces?interface_type=external'
        );
        setInterfaces(res.interfaces || []);
      } catch (err) {
        setError('Failed to load infrastructure interfaces');
        console.error('Error loading interfaces:', err);
      } finally {
        setLoading(false);
      }
    };
    loadInterfaces();
  }, []);

  const handleInterfaceSelect = (interfaceId: string) => {
    if (!interfaceId) {
      onUpdate(node.id, {
        managedInterfaceId: undefined,
        managedInterfaceName: undefined,
        managedInterfaceHostId: undefined,
        managedInterfaceHostName: undefined,
        host: undefined,
      });
      return;
    }
    const iface = interfaces.find((i) => i.id === interfaceId);
    if (iface) {
      onUpdate(node.id, {
        managedInterfaceId: iface.id,
        managedInterfaceName: iface.name,
        managedInterfaceHostId: iface.host_id,
        managedInterfaceHostName: iface.host_name || undefined,
        host: iface.host_id,
        // Clear legacy fields
        connectionType: undefined,
        parentInterface: undefined,
        vlanId: undefined,
        bridgeName: undefined,
      });
    }
  };

  // Group interfaces by host
  const groupedInterfaces: Record<string, ManagedInterface[]> = {};
  for (const iface of interfaces) {
    const hostLabel = iface.host_name || iface.host_id;
    if (!groupedInterfaces[hostLabel]) {
      groupedInterfaces[hostLabel] = [];
    }
    groupedInterfaces[hostLabel].push(iface);
  }

  // Find the currently selected interface
  const selectedInterface = node.managedInterfaceId
    ? interfaces.find((i) => i.id === node.managedInterfaceId)
    : null;

  // Check if this is a legacy node (has old fields but no managed interface)
  const isLegacy = !node.managedInterfaceId && (node.connectionType || node.parentInterface || node.vlanId || node.bridgeName);

  const syncStatusColor = (status: string) => {
    switch (status) {
      case 'synced': return 'text-green-600 dark:text-green-400';
      case 'mismatch': return 'text-yellow-600 dark:text-yellow-400';
      case 'error': return 'text-red-600 dark:text-red-400';
      default: return 'text-stone-400';
    }
  };

  const syncStatusIcon = (status: string) => {
    switch (status) {
      case 'synced': return 'fa-check-circle';
      case 'mismatch': return 'fa-exclamation-circle';
      case 'error': return 'fa-times-circle';
      default: return 'fa-question-circle';
    }
  };

  return (
    <div className="w-80 bg-white dark:bg-stone-900 border-l border-stone-200 dark:border-stone-700 overflow-y-auto flex flex-col">
      {/* Header */}
      <div className="p-4 border-b border-stone-200 dark:border-stone-700 flex justify-between items-center bg-gradient-to-r from-blue-50 to-purple-50 dark:from-blue-950/30 dark:to-purple-950/30">
        <div>
          <h2 className="text-xs font-black uppercase tracking-widest text-blue-700 dark:text-blue-300">External Network</h2>
          <div className="text-[9px] font-bold text-purple-600 dark:text-purple-400 tracking-tighter uppercase">
            {selectedInterface ? selectedInterface.name : 'Unconfigured'}
          </div>
        </div>
        <button
          onClick={() => onDelete(node.id)}
          className="p-1.5 text-stone-500 hover:text-red-500 hover:bg-red-100 dark:hover:bg-red-950/30 rounded transition-all"
        >
          <i className="fa-solid fa-trash-can text-sm"></i>
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-5 space-y-6 custom-scrollbar">
        {/* Name */}
        <div className="space-y-2">
          <label className="text-[10px] font-bold text-stone-500 uppercase tracking-widest">Display Name</label>
          <input
            type="text"
            value={node.name}
            onChange={(e) => onUpdate(node.id, { name: e.target.value })}
            className="w-full bg-stone-100 dark:bg-stone-800 border border-stone-300 dark:border-stone-700 rounded-lg px-3 py-2 text-sm text-stone-900 dark:text-stone-100 focus:outline-none focus:border-blue-500"
            placeholder="e.g., Production Network"
          />
        </div>

        {/* Legacy Warning */}
        {isLegacy && (
          <div className="p-3 bg-yellow-50 dark:bg-yellow-950/30 border border-yellow-200 dark:border-yellow-800 rounded-lg">
            <div className="flex items-start gap-2">
              <i className="fa-solid fa-exclamation-triangle text-yellow-500 mt-0.5"></i>
              <div className="text-[9px] text-yellow-700 dark:text-yellow-400 leading-relaxed">
                <span className="font-bold">Legacy Configuration</span><br />
                This external network uses the old configuration format.
                Select a managed interface below to upgrade it.
                {node.connectionType === 'vlan' && node.parentInterface && node.vlanId && (
                  <span className="block mt-1 font-mono text-[8px]">
                    Current: {node.parentInterface}.{node.vlanId}
                  </span>
                )}
              </div>
            </div>
          </div>
        )}

        {/* Managed Interface Selection */}
        <div className="space-y-2">
          <label className="text-[10px] font-bold text-stone-500 uppercase tracking-widest">Infrastructure Interface</label>

          {loading ? (
            <div className="p-3 bg-stone-100 dark:bg-stone-800 rounded-lg text-center">
              <i className="fa-solid fa-spinner fa-spin text-stone-400 mr-2"></i>
              <span className="text-[10px] text-stone-500">Loading interfaces...</span>
            </div>
          ) : error ? (
            <div className="p-3 bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-800 rounded-lg text-center">
              <i className="fa-solid fa-exclamation-triangle text-red-500 mr-2"></i>
              <span className="text-[10px] text-red-600 dark:text-red-400">{error}</span>
            </div>
          ) : interfaces.length === 0 ? (
            <div className="p-3 bg-stone-50 dark:bg-stone-800 border border-stone-200 dark:border-stone-700 rounded-lg">
              <div className="flex items-start gap-2">
                <i className="fa-solid fa-info-circle text-stone-400 mt-0.5"></i>
                <div className="text-[9px] text-stone-500 dark:text-stone-400 leading-relaxed">
                  No external interfaces configured. Create one in
                  <span className="font-bold"> Infrastructure</span> first.
                </div>
              </div>
            </div>
          ) : (
            <select
              value={node.managedInterfaceId || ''}
              onChange={(e) => handleInterfaceSelect(e.target.value)}
              className="w-full bg-stone-100 dark:bg-stone-800 border border-stone-300 dark:border-stone-700 rounded-lg px-3 py-2 text-sm text-stone-900 dark:text-stone-100 focus:outline-none focus:border-blue-500 appearance-none"
            >
              <option value="">Select interface...</option>
              {Object.entries(groupedInterfaces).map(([hostLabel, ifaces]) => (
                <optgroup key={hostLabel} label={hostLabel}>
                  {ifaces.map((iface) => (
                    <option key={iface.id} value={iface.id}>
                      {iface.name}
                      {iface.vlan_id ? ` (VLAN ${iface.vlan_id})` : ''}
                      {iface.sync_status === 'synced' ? ' \u2713' : ''}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
          )}
          <p className="text-[9px] text-stone-400 dark:text-stone-500">
            Managed interfaces are created in the Infrastructure page
          </p>
        </div>

        {/* Selected Interface Details */}
        {selectedInterface && (
          <div className="space-y-3 p-4 bg-blue-50/50 dark:bg-blue-950/20 border border-blue-200 dark:border-blue-800/50 rounded-xl">
            <div className="text-[10px] font-bold text-blue-700 dark:text-blue-300 uppercase tracking-widest">Interface Details</div>

            <div className="grid grid-cols-2 gap-2 text-[10px]">
              <div>
                <span className="text-stone-400">Name</span>
                <div className="font-mono font-bold text-stone-700 dark:text-stone-200">{selectedInterface.name}</div>
              </div>
              <div>
                <span className="text-stone-400">Host</span>
                <div className="font-bold text-stone-700 dark:text-stone-200">{selectedInterface.host_name || selectedInterface.host_id.slice(0, 8)}</div>
              </div>
              {selectedInterface.parent_interface && (
                <div>
                  <span className="text-stone-400">Parent</span>
                  <div className="font-mono font-bold text-stone-700 dark:text-stone-200">{selectedInterface.parent_interface}</div>
                </div>
              )}
              {selectedInterface.vlan_id && (
                <div>
                  <span className="text-stone-400">VLAN</span>
                  <div className="font-bold text-stone-700 dark:text-stone-200">{selectedInterface.vlan_id}</div>
                </div>
              )}
              <div>
                <span className="text-stone-400">MTU</span>
                <div className="font-bold text-stone-700 dark:text-stone-200">{selectedInterface.current_mtu || selectedInterface.desired_mtu}</div>
              </div>
              <div>
                <span className="text-stone-400">Status</span>
                <div className={`font-bold ${syncStatusColor(selectedInterface.sync_status)}`}>
                  <i className={`fa-solid ${syncStatusIcon(selectedInterface.sync_status)} mr-1`}></i>
                  {selectedInterface.sync_status}
                </div>
              </div>
            </div>

            {selectedInterface.ip_address && (
              <div className="text-[10px]">
                <span className="text-stone-400">IP Address</span>
                <div className="font-mono font-bold text-stone-700 dark:text-stone-200">{selectedInterface.ip_address}</div>
              </div>
            )}

            {selectedInterface.sync_error && (
              <div className="p-2 bg-red-50 dark:bg-red-950/30 rounded text-[9px] text-red-600 dark:text-red-400">
                {selectedInterface.sync_error}
              </div>
            )}
          </div>
        )}

        {/* Info Box */}
        <div className="p-3 bg-stone-50 dark:bg-stone-950/50 border border-stone-200 dark:border-stone-800 rounded-lg">
          <div className="flex items-start gap-2">
            <i className="fa-solid fa-info-circle text-stone-400 mt-0.5"></i>
            <div className="text-[9px] text-stone-500 dark:text-stone-400 leading-relaxed">
              External networks connect lab devices to physical networks via infrastructure-managed interfaces.
              Multiple devices can share the same external network for L2 broadcast domain connectivity.
              Cross-host connections are handled automatically via VXLAN tunnels.
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default ExternalNetworkConfig;
