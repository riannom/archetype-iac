import React, { useState, useEffect } from 'react';
import { ExternalNetworkNode, ExternalConnectionType } from '../types';
import { apiRequest } from '../../api';

interface ExternalNetworkConfigProps {
  node: ExternalNetworkNode;
  onUpdate: (id: string, updates: Partial<ExternalNetworkNode>) => void;
  onDelete: (id: string) => void;
  agents?: { id: string; name: string }[];
}

interface NetworkInterface {
  name: string;
  state: string;
  type: string;
  ipv4_addresses: string[];
  mac?: string;
  is_vlan: boolean;
}

interface Bridge {
  name: string;
  state?: string;
  interfaces?: string[];
}

const ExternalNetworkConfig: React.FC<ExternalNetworkConfigProps> = ({
  node,
  onUpdate,
  onDelete,
  agents = [],
}) => {
  const [interfaces, setInterfaces] = useState<NetworkInterface[]>([]);
  const [bridges, setBridges] = useState<Bridge[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Load interfaces and bridges from the selected agent
  useEffect(() => {
    const loadNetworkInfo = async () => {
      if (!node.host) {
        setInterfaces([]);
        setBridges([]);
        return;
      }

      setLoading(true);
      setError(null);

      try {
        // Fetch interfaces and bridges in parallel
        const [interfacesRes, bridgesRes] = await Promise.all([
          apiRequest<{ interfaces: NetworkInterface[] }>(`/agents/${node.host}/interfaces`).catch(() => ({ interfaces: [] })),
          apiRequest<{ bridges: Bridge[] }>(`/agents/${node.host}/bridges`).catch(() => ({ bridges: [] })),
        ]);

        setInterfaces(interfacesRes.interfaces || []);
        setBridges(bridgesRes.bridges || []);
      } catch (err) {
        setError('Failed to load network information');
        console.error('Error loading network info:', err);
      } finally {
        setLoading(false);
      }
    };

    loadNetworkInfo();
  }, [node.host]);

  const handleConnectionTypeChange = (type: ExternalConnectionType) => {
    onUpdate(node.id, {
      connectionType: type,
      // Clear the other type's fields
      ...(type === 'vlan' ? { bridgeName: undefined } : { parentInterface: undefined, vlanId: undefined }),
    });
  };

  // Filter interfaces for VLAN parent selection (exclude existing VLANs and virtual interfaces)
  const availableParentInterfaces = interfaces.filter(
    (iface) => !iface.is_vlan && iface.type !== 'bridge' && !iface.name.startsWith('veth')
  );

  return (
    <div className="w-80 bg-white dark:bg-stone-900 border-l border-stone-200 dark:border-stone-700 overflow-y-auto flex flex-col">
      {/* Header */}
      <div className="p-4 border-b border-stone-200 dark:border-stone-700 flex justify-between items-center bg-gradient-to-r from-blue-50 to-purple-50 dark:from-blue-950/30 dark:to-purple-950/30">
        <div>
          <h2 className="text-xs font-black uppercase tracking-widest text-blue-700 dark:text-blue-300">External Network</h2>
          <div className="text-[9px] font-bold text-purple-600 dark:text-purple-400 tracking-tighter uppercase">
            {node.connectionType === 'vlan' ? 'VLAN Connection' : 'Bridge Connection'}
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

        {/* Host Selection */}
        <div className="space-y-2">
          <label className="text-[10px] font-bold text-stone-500 uppercase tracking-widest">Host Agent</label>
          <select
            value={node.host || ''}
            onChange={(e) => onUpdate(node.id, { host: e.target.value || undefined })}
            className="w-full bg-stone-100 dark:bg-stone-800 border border-stone-300 dark:border-stone-700 rounded-lg px-3 py-2 text-sm text-stone-900 dark:text-stone-100 focus:outline-none focus:border-blue-500 appearance-none"
          >
            <option value="">Select host...</option>
            {agents.map((agent) => (
              <option key={agent.id} value={agent.id}>
                {agent.name}
              </option>
            ))}
          </select>
          <p className="text-[9px] text-stone-400 dark:text-stone-500">
            The host where this external network is available
          </p>
        </div>

        {/* Connection Type Toggle */}
        <div className="space-y-2">
          <label className="text-[10px] font-bold text-stone-500 uppercase tracking-widest">Connection Type</label>
          <div className="grid grid-cols-2 gap-2">
            <button
              onClick={() => handleConnectionTypeChange('vlan')}
              className={`flex flex-col items-center justify-center p-3 rounded-lg border-2 transition-all ${
                node.connectionType === 'vlan'
                  ? 'border-blue-500 bg-blue-50 dark:bg-blue-950/30'
                  : 'border-stone-200 dark:border-stone-700 hover:border-stone-300 dark:hover:border-stone-600'
              }`}
            >
              <i className={`fa-solid fa-layer-group text-lg mb-1 ${node.connectionType === 'vlan' ? 'text-blue-500' : 'text-stone-400'}`}></i>
              <span className={`text-[10px] font-bold ${node.connectionType === 'vlan' ? 'text-blue-700 dark:text-blue-300' : 'text-stone-500'}`}>VLAN</span>
            </button>
            <button
              onClick={() => handleConnectionTypeChange('bridge')}
              className={`flex flex-col items-center justify-center p-3 rounded-lg border-2 transition-all ${
                node.connectionType === 'bridge'
                  ? 'border-purple-500 bg-purple-50 dark:bg-purple-950/30'
                  : 'border-stone-200 dark:border-stone-700 hover:border-stone-300 dark:hover:border-stone-600'
              }`}
            >
              <i className={`fa-solid fa-network-wired text-lg mb-1 ${node.connectionType === 'bridge' ? 'text-purple-500' : 'text-stone-400'}`}></i>
              <span className={`text-[10px] font-bold ${node.connectionType === 'bridge' ? 'text-purple-700 dark:text-purple-300' : 'text-stone-500'}`}>Bridge</span>
            </button>
          </div>
        </div>

        {/* Loading/Error State */}
        {loading && (
          <div className="p-3 bg-stone-100 dark:bg-stone-800 rounded-lg text-center">
            <i className="fa-solid fa-spinner fa-spin text-stone-400 mr-2"></i>
            <span className="text-[10px] text-stone-500">Loading network info...</span>
          </div>
        )}

        {error && (
          <div className="p-3 bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-800 rounded-lg text-center">
            <i className="fa-solid fa-exclamation-triangle text-red-500 mr-2"></i>
            <span className="text-[10px] text-red-600 dark:text-red-400">{error}</span>
          </div>
        )}

        {/* VLAN Configuration */}
        {node.connectionType === 'vlan' && (
          <div className="space-y-4 p-4 bg-blue-50/50 dark:bg-blue-950/20 border border-blue-200 dark:border-blue-800/50 rounded-xl">
            <div className="space-y-2">
              <label className="text-[10px] font-bold text-blue-700 dark:text-blue-300 uppercase tracking-widest">Parent Interface</label>
              <select
                value={node.parentInterface || ''}
                onChange={(e) => onUpdate(node.id, { parentInterface: e.target.value || undefined })}
                className="w-full bg-white dark:bg-stone-800 border border-blue-300 dark:border-blue-700 rounded-lg px-3 py-2 text-sm text-stone-900 dark:text-stone-100 focus:outline-none focus:border-blue-500 appearance-none"
                disabled={!node.host}
              >
                <option value="">{node.host ? 'Select interface...' : 'Select host first'}</option>
                {availableParentInterfaces.map((iface) => (
                  <option key={iface.name} value={iface.name}>
                    {iface.name} ({iface.state})
                  </option>
                ))}
              </select>
              <p className="text-[9px] text-blue-600/60 dark:text-blue-400/60">
                e.g., ens192, eth0
              </p>
            </div>

            <div className="space-y-2">
              <label className="text-[10px] font-bold text-blue-700 dark:text-blue-300 uppercase tracking-widest">VLAN ID</label>
              <input
                type="number"
                min="1"
                max="4094"
                value={node.vlanId || ''}
                onChange={(e) => onUpdate(node.id, { vlanId: e.target.value ? parseInt(e.target.value, 10) : undefined })}
                className="w-full bg-white dark:bg-stone-800 border border-blue-300 dark:border-blue-700 rounded-lg px-3 py-2 text-sm text-stone-900 dark:text-stone-100 focus:outline-none focus:border-blue-500"
                placeholder="100"
              />
              <p className="text-[9px] text-blue-600/60 dark:text-blue-400/60">
                1-4094
              </p>
            </div>

            {/* Preview */}
            {node.parentInterface && node.vlanId && (
              <div className="p-2 bg-blue-100 dark:bg-blue-900/30 rounded-lg">
                <div className="text-[9px] font-bold text-blue-700 dark:text-blue-300 uppercase mb-1">Interface Preview</div>
                <code className="text-xs font-mono text-blue-800 dark:text-blue-200">
                  {node.parentInterface}.{node.vlanId}
                </code>
              </div>
            )}
          </div>
        )}

        {/* Bridge Configuration */}
        {node.connectionType === 'bridge' && (
          <div className="space-y-4 p-4 bg-purple-50/50 dark:bg-purple-950/20 border border-purple-200 dark:border-purple-800/50 rounded-xl">
            <div className="space-y-2">
              <label className="text-[10px] font-bold text-purple-700 dark:text-purple-300 uppercase tracking-widest">Bridge Name</label>
              <select
                value={node.bridgeName || ''}
                onChange={(e) => onUpdate(node.id, { bridgeName: e.target.value || undefined })}
                className="w-full bg-white dark:bg-stone-800 border border-purple-300 dark:border-purple-700 rounded-lg px-3 py-2 text-sm text-stone-900 dark:text-stone-100 focus:outline-none focus:border-purple-500 appearance-none"
                disabled={!node.host}
              >
                <option value="">{node.host ? 'Select bridge...' : 'Select host first'}</option>
                {bridges.map((bridge) => (
                  <option key={bridge.name} value={bridge.name}>
                    {bridge.name}
                  </option>
                ))}
              </select>
              <p className="text-[9px] text-purple-600/60 dark:text-purple-400/60">
                e.g., br0, br-prod
              </p>
            </div>

            {/* Or enter manually */}
            <div className="text-center text-[9px] text-stone-400 dark:text-stone-500">- or enter manually -</div>

            <input
              type="text"
              value={node.bridgeName || ''}
              onChange={(e) => onUpdate(node.id, { bridgeName: e.target.value || undefined })}
              className="w-full bg-white dark:bg-stone-800 border border-purple-300 dark:border-purple-700 rounded-lg px-3 py-2 text-sm text-stone-900 dark:text-stone-100 focus:outline-none focus:border-purple-500"
              placeholder="br-prod"
            />
          </div>
        )}

        {/* Info Box */}
        <div className="p-3 bg-stone-50 dark:bg-stone-950/50 border border-stone-200 dark:border-stone-800 rounded-lg">
          <div className="flex items-start gap-2">
            <i className="fa-solid fa-info-circle text-stone-400 mt-0.5"></i>
            <div className="text-[9px] text-stone-500 dark:text-stone-400 leading-relaxed">
              {node.connectionType === 'vlan' ? (
                <>
                  VLAN sub-interfaces are automatically created when the lab deploys and removed when destroyed.
                  The parent interface must be a physical network interface on the selected host.
                </>
              ) : (
                <>
                  Bridge connections use an existing Linux bridge on the host.
                  Make sure the bridge is already created and configured.
                </>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default ExternalNetworkConfig;
