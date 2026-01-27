
import React, { useCallback } from 'react';
import { DeviceModel, Node } from '../types';

export type RuntimeStatus = 'stopped' | 'booting' | 'running' | 'error';

interface RuntimeControlProps {
  labId: string;
  nodes: Node[];
  runtimeStates: Record<string, RuntimeStatus>;
  deviceModels: DeviceModel[];
  onUpdateStatus: (nodeId: string, status: RuntimeStatus) => void;
  onRefreshStates: () => void;
  studioRequest: <T>(path: string, options?: RequestInit) => Promise<T>;
}

const RuntimeControl: React.FC<RuntimeControlProps> = ({ labId, nodes, runtimeStates, deviceModels, onUpdateStatus, onRefreshStates, studioRequest }) => {
  const getStatusColor = (status: RuntimeStatus) => {
    switch (status) {
      case 'running': return 'text-green-500 bg-green-500/10 border-green-500/20';
      case 'booting': return 'text-yellow-500 bg-yellow-500/10 border-yellow-500/20';
      case 'error': return 'text-red-500 bg-red-500/10 border-red-500/20';
      default: return 'text-stone-500 bg-stone-500/10 border-stone-500/20';
    }
  };

  const handleBulkAction = useCallback(async (action: 'running' | 'stopped') => {
    if (!labId || nodes.length === 0) return;

    try {
      // Set all nodes' desired state
      await studioRequest(`/labs/${labId}/nodes/desired-state`, {
        method: 'PUT',
        body: JSON.stringify({ state: action === 'running' ? 'running' : 'stopped' }),
      });

      // Optimistically update UI
      nodes.forEach(node => {
        onUpdateStatus(node.id, action === 'running' ? 'booting' : 'stopped');
      });

      // Trigger sync for all nodes
      await studioRequest(`/labs/${labId}/sync`, { method: 'POST' });

      // Refresh states after a short delay
      setTimeout(() => onRefreshStates(), 1000);
    } catch (error) {
      console.error('Bulk action failed:', error);
    }
  }, [labId, nodes, studioRequest, onUpdateStatus, onRefreshStates]);

  return (
    <div className="flex-1 bg-stone-50 dark:bg-stone-950 flex flex-col overflow-hidden animate-in fade-in duration-300">
      <div className="p-8 max-w-7xl mx-auto w-full flex-1 flex flex-col overflow-hidden">
        <header className="mb-8 flex justify-between items-end">
          <div>
            <h1 className="text-3xl font-black text-stone-900 dark:text-white tracking-tight">Runtime Control</h1>
            <p className="text-stone-500 dark:text-stone-400 text-sm mt-1">Live operational state and lifecycle management for your topology.</p>
          </div>
          <div className="flex gap-3">
            <button
              onClick={() => handleBulkAction('running')}
              className="px-4 py-2 bg-green-600 hover:bg-green-500 text-white rounded-lg text-xs font-bold transition-all shadow-lg shadow-green-900/20"
            >
              <i className="fa-solid fa-play mr-2"></i> Start All
            </button>
            <button
              onClick={() => handleBulkAction('stopped')}
              className="px-4 py-2 bg-stone-200 dark:bg-stone-800 hover:bg-stone-300 dark:hover:bg-stone-700 text-stone-700 dark:text-white rounded-lg border border-stone-300 dark:border-stone-700 text-xs font-bold transition-all"
            >
              <i className="fa-solid fa-stop mr-2"></i> Stop All
            </button>
          </div>
        </header>

        <div className="bg-white/50 dark:bg-stone-900/50 border border-stone-200 dark:border-stone-800 rounded-2xl flex flex-col overflow-hidden">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="bg-stone-100 dark:bg-stone-900 border-b border-stone-200 dark:border-stone-800">
                <th className="px-6 py-4 text-[10px] font-bold text-stone-500 uppercase tracking-widest">Device Name</th>
                <th className="px-6 py-4 text-[10px] font-bold text-stone-500 uppercase tracking-widest">Model / Version</th>
                <th className="px-6 py-4 text-[10px] font-bold text-stone-500 uppercase tracking-widest">Status</th>
                <th className="px-6 py-4 text-[10px] font-bold text-stone-500 uppercase tracking-widest">Utilization</th>
                <th className="px-6 py-4 text-[10px] font-bold text-stone-500 uppercase tracking-widest text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-stone-200/50 dark:divide-stone-800/50">
              {nodes.map(node => {
                const status = runtimeStates[node.id] || 'stopped';
                const model = deviceModels.find(m => m.id === node.model);
                return (
                  <tr key={node.id} className="hover:bg-stone-100/50 dark:hover:bg-stone-800/30 transition-colors group">
                    <td className="px-6 py-4">
                      <div className="flex items-center gap-3">
                        <div className="w-8 h-8 rounded bg-stone-200 dark:bg-stone-800 flex items-center justify-center text-stone-500 dark:text-stone-400">
                          <i className={`fa-solid ${model?.icon || 'fa-microchip'}`}></i>
                        </div>
                        <div>
                          <div className="text-sm font-bold text-stone-900 dark:text-white">{node.name}</div>
                          <div className="text-[10px] text-stone-500 font-mono uppercase tracking-tighter">{node.id}</div>
                        </div>
                      </div>
                    </td>
                    <td className="px-6 py-4">
                      <div className="text-xs text-stone-700 dark:text-stone-300 font-medium">{model?.name}</div>
                      <div className="text-[10px] text-stone-500 italic">{node.version}</div>
                    </td>
                    <td className="px-6 py-4">
                      <span className={`px-2.5 py-1 rounded-full text-[10px] font-black uppercase tracking-wider border transition-all ${getStatusColor(status)}`}>
                        {status}
                      </span>
                    </td>
                    <td className="px-6 py-4">
                      <span className="text-stone-400 dark:text-stone-700 text-[10px] font-bold italic">
                        {status === 'running' ? 'Metrics unavailable' : 'Offline'}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-right">
                      <div className="flex justify-end gap-1 opacity-60 group-hover:opacity-100 transition-opacity">
                        <>
                            {status === 'stopped' ? (
                              <button
                                onClick={() => onUpdateStatus(node.id, 'booting')}
                                className="p-2 text-green-500 hover:bg-green-500/10 rounded-lg transition-all"
                                title="Power On"
                              >
                                <i className="fa-solid fa-play"></i>
                              </button>
                            ) : (
                              <button
                                onClick={() => onUpdateStatus(node.id, 'stopped')}
                                className="p-2 text-red-500 hover:bg-red-500/10 rounded-lg transition-all"
                                title="Power Off"
                              >
                                <i className="fa-solid fa-power-off"></i>
                              </button>
                            )}
                            <button
                              onClick={() => onUpdateStatus(node.id, 'booting')}
                              className="p-2 text-stone-400 hover:bg-stone-400/10 rounded-lg transition-all"
                              title="Reload"
                            >
                              <i className="fa-solid fa-rotate"></i>
                            </button>
                        </>
                      </div>
                    </td>
                  </tr>
                );
              })}
              {nodes.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-6 py-20 text-center text-stone-500 dark:text-stone-600 italic text-sm">
                    No devices in current topology. Return to Designer to add nodes.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};

export default RuntimeControl;
