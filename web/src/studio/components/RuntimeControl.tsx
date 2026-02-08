
import React, { useCallback, useMemo, useState } from 'react';
import { DeviceModel, Node, isDeviceNode, DeviceNode } from '../types';
import { getAgentColor } from '../../utils/agentColors';
import { NodeRuntimeStatus, NodeStateEntry } from '../../types/nodeState';

/** @deprecated Use NodeRuntimeStatus from types/nodeState instead */
export type RuntimeStatus = NodeRuntimeStatus;

// Track pending operations to prevent race conditions from rapid clicks
type PendingOp = 'bulk' | string; // 'bulk' for bulk actions, node ID for per-node actions

interface RuntimeControlProps {
  labId: string;
  nodes: Node[];
  runtimeStates: Record<string, RuntimeStatus>;
  nodeStates: Record<string, NodeStateEntry>;
  deviceModels: DeviceModel[];
  onUpdateStatus: (nodeId: string, status: RuntimeStatus) => Promise<void>;
  onSetRuntimeStatus: (nodeId: string, status: RuntimeStatus) => void;
  onRefreshStates: () => void;
  studioRequest: <T>(path: string, options?: RequestInit) => Promise<T>;
  agents?: { id: string; name: string }[];
  onUpdateNode?: (id: string, updates: Partial<Node>) => void;
  pendingNodeOps?: Set<string>;
  onFlushTopologySave?: () => Promise<void>;
}

const RuntimeControl: React.FC<RuntimeControlProps> = ({ labId, nodes, runtimeStates, nodeStates, deviceModels, onUpdateStatus, onSetRuntimeStatus, onRefreshStates, studioRequest, agents = [], onUpdateNode, pendingNodeOps = new Set(), onFlushTopologySave }) => {
  const [isExtracting, setIsExtracting] = useState(false);
  const [pendingOps, setPendingOps] = useState<Set<PendingOp>>(new Set());

  // Helper to check if an operation is pending
  // Checks both local bulk ops and per-node ops passed from parent
  const isOperationPending = useCallback((nodeId?: string): boolean => {
    if (pendingOps.has('bulk')) return true; // Bulk op blocks everything
    if (!nodeId) return pendingOps.size > 0 || pendingNodeOps.size > 0; // Any operation pending
    return pendingOps.has(nodeId) || pendingNodeOps.has(nodeId);
  }, [pendingOps, pendingNodeOps]);

  // Filter to device nodes only (external networks don't have runtime status)
  const deviceNodes = useMemo(() => nodes.filter(isDeviceNode), [nodes]);

  const getStatusColor = (status: RuntimeStatus) => {
    switch (status) {
      case 'running': return 'text-green-500 bg-green-500/10 border-green-500/20';
      case 'booting': return 'text-yellow-500 bg-yellow-500/10 border-yellow-500/20';
      case 'stopping': return 'text-orange-500 bg-orange-500/10 border-orange-500/20';
      case 'error': return 'text-red-500 bg-red-500/10 border-red-500/20';
      default: return 'text-stone-500 bg-stone-500/10 border-stone-500/20';
    }
  };

  // Check if any nodes are currently running, booting, or stopping
  const hasRunningNodes = deviceNodes.some(node => {
    const status = runtimeStates[node.id];
    return status === 'running' || status === 'booting' || status === 'stopping';
  });

  // Check if all nodes are currently running
  const allNodesRunning = deviceNodes.length > 0 && deviceNodes.every(node => {
    const status = runtimeStates[node.id];
    return status === 'running';
  });

  // Check if lab is deployed (any node has ever been started)
  const isLabDeployed = hasRunningNodes;

  const handleBulkAction = useCallback(async (action: 'running' | 'stopped') => {
    if (!labId || nodes.length === 0) return;
    if (isOperationPending()) return; // Block if any op pending

    // Optimistic update + setPendingOps in same synchronous block
    // so React batches them into a single render (no gap where fallback 'stopped' can flash)
    setPendingOps(prev => new Set(prev).add('bulk'));
    nodes.forEach(node => {
      onSetRuntimeStatus(node.id, action === 'running' ? 'booting' : 'stopping');
    });

    try {
      // Flush any pending topology saves before deploying
      // This ensures host assignments are committed to DB before the deploy job reads them
      if (onFlushTopologySave) {
        await onFlushTopologySave();
      }

      // Set all nodes' desired state - this now auto-triggers sync
      await studioRequest(`/labs/${labId}/nodes/desired-state`, {
        method: 'PUT',
        body: JSON.stringify({ state: action === 'running' ? 'running' : 'stopped' }),
      });

      // Refresh states after a short delay (WebSocket will also push updates)
      setTimeout(() => onRefreshStates(), 1000);
    } catch (error) {
      console.error('Bulk action failed:', error);
    } finally {
      setPendingOps(prev => {
        const next = new Set(prev);
        next.delete('bulk');
        return next;
      });
    }
  }, [labId, nodes, studioRequest, onSetRuntimeStatus, onRefreshStates, isOperationPending, onFlushTopologySave]);

  const handleExtractConfigs = useCallback(async () => {
    const confirmed = window.confirm(
      "Before extracting, make sure you've run 'copy running start' on each device to save your running config to startup config.\n\nExtract configs from all running nodes?"
    );
    if (!confirmed) return;

    setIsExtracting(true);
    try {
      await studioRequest(`/labs/${labId}/extract-configs`, { method: 'POST' });
      alert('Configs extracted successfully!');
    } catch (error) {
      console.error('Extract configs failed:', error);
      alert('Failed to extract configs. Check console for details.');
    } finally {
      setIsExtracting(false);
    }
  }, [labId, studioRequest]);

  const handleStopAll = useCallback(async () => {
    const confirmed = window.confirm(
      "Stopping all nodes will first extract configs from running cEOS devices. This may take a moment.\n\nContinue with Stop All?"
    );
    if (!confirmed) return;

    await handleBulkAction('stopped');
  }, [handleBulkAction]);

  return (
    <div className="flex-1 bg-stone-50 dark:bg-stone-950 flex flex-col overflow-hidden animate-in fade-in duration-300">
      <div className="p-8 max-w-7xl mx-auto w-full flex-1 flex flex-col overflow-hidden">
        <header className="mb-8 flex justify-between items-end">
          <div>
            <h1 className="text-3xl font-black text-stone-900 dark:text-white tracking-tight">Runtime Control</h1>
            <p className="text-stone-500 dark:text-stone-400 text-sm mt-1">Live operational state and lifecycle management for your topology.</p>
          </div>
          <div className="flex gap-3">
            {!isLabDeployed ? (
              <button
                onClick={() => handleBulkAction('running')}
                disabled={isOperationPending()}
                className="px-4 py-2 bg-green-600 hover:bg-green-500 disabled:bg-green-600/50 disabled:cursor-not-allowed text-white rounded-lg text-xs font-bold transition-all shadow-lg shadow-green-900/20"
                title="Deploy all nodes in the topology"
              >
                <i className={`fa-solid ${pendingOps.has('bulk') ? 'fa-spinner fa-spin' : 'fa-rocket'} mr-2`}></i>
                {pendingOps.has('bulk') ? 'Deploying...' : 'Deploy Lab'}
              </button>
            ) : (
              <>
                {!allNodesRunning && (
                  <button
                    onClick={() => handleBulkAction('running')}
                    disabled={isOperationPending()}
                    className="px-4 py-2 bg-green-600 hover:bg-green-500 disabled:bg-green-600/50 disabled:cursor-not-allowed text-white rounded-lg text-xs font-bold transition-all shadow-lg shadow-green-900/20"
                    title="Start all stopped nodes"
                  >
                    <i className={`fa-solid ${pendingOps.has('bulk') ? 'fa-spinner fa-spin' : 'fa-play'} mr-2`}></i>
                    {pendingOps.has('bulk') ? 'Starting...' : 'Start All'}
                  </button>
                )}
                <button
                  onClick={handleStopAll}
                  disabled={isOperationPending()}
                  className="px-4 py-2 bg-stone-200 dark:bg-stone-800 hover:bg-stone-300 dark:hover:bg-stone-700 disabled:opacity-50 disabled:cursor-not-allowed text-stone-700 dark:text-white rounded-lg border border-stone-300 dark:border-stone-700 text-xs font-bold transition-all"
                  title="Stop all running nodes"
                >
                  <i className={`fa-solid ${pendingOps.has('bulk') ? 'fa-spinner fa-spin' : 'fa-stop'} mr-2`}></i>
                  {pendingOps.has('bulk') ? 'Stopping...' : 'Stop All'}
                </button>
                <button
                  onClick={handleExtractConfigs}
                  disabled={isExtracting || !hasRunningNodes}
                  className="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:bg-blue-600/50 disabled:cursor-not-allowed text-white rounded-lg text-xs font-bold transition-all shadow-lg shadow-blue-900/20"
                  title="Extract configs from all running nodes"
                >
                  <i className={`fa-solid ${isExtracting ? 'fa-spinner fa-spin' : 'fa-download'} mr-2`}></i>
                  {isExtracting ? 'Extracting...' : 'Extract Configs'}
                </button>
              </>
            )}
          </div>
        </header>

        <div className="bg-white/50 dark:bg-stone-900/50 border border-stone-200 dark:border-stone-800 rounded-2xl flex flex-col overflow-hidden">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="bg-stone-100 dark:bg-stone-900 border-b border-stone-200 dark:border-stone-800">
                <th className="px-6 py-4 text-[10px] font-bold text-stone-500 uppercase tracking-widest">Device Name</th>
                <th className="px-6 py-4 text-[10px] font-bold text-stone-500 uppercase tracking-widest">Model</th>
                <th className="px-6 py-4 text-[10px] font-bold text-stone-500 uppercase tracking-widest">Version</th>
                <th className="px-6 py-4 text-[10px] font-bold text-stone-500 uppercase tracking-widest">Status</th>
                <th className="px-6 py-4 text-[10px] font-bold text-stone-500 uppercase tracking-widest">Assigned</th>
                <th className="px-6 py-4 text-[10px] font-bold text-stone-500 uppercase tracking-widest">Running On</th>
                <th className="px-6 py-4 text-[10px] font-bold text-stone-500 uppercase tracking-widest text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-stone-200/50 dark:divide-stone-800/50">
              {deviceNodes.map(node => {
                const status = runtimeStates[node.id] || 'stopped';
                const model = deviceModels.find(m => m.id === node.model);
                const nodeState = nodeStates[node.id];
                const hostName = nodeState?.host_name;
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
                      <div className="text-xs text-stone-700 dark:text-stone-300 font-medium">{model?.name || '—'}</div>
                    </td>
                    <td className="px-6 py-4">
                      <div className="text-xs text-stone-500 dark:text-stone-400 font-mono">{node.version || '—'}</div>
                    </td>
                    <td className="px-6 py-4">
                      <div className="flex items-center gap-2">
                        <span className={`px-2.5 py-1 rounded-full text-[10px] font-black uppercase tracking-wider border transition-all ${getStatusColor(status)}`}>
                          {status}
                        </span>
                        {/* Enforcement failure warning indicator */}
                        {status === 'error' && nodeState?.error_message?.includes('enforcement') && (
                          <div className="relative group/tooltip">
                            <i className="fa-solid fa-triangle-exclamation text-amber-500 text-sm animate-pulse cursor-help"></i>
                            <div className="absolute left-full ml-2 top-1/2 -translate-y-1/2 hidden group-hover/tooltip:block z-50">
                              <div className="bg-stone-900 text-white text-xs px-3 py-2 rounded-lg shadow-lg max-w-xs whitespace-nowrap">
                                <div className="font-bold text-amber-400 mb-1">Restart Attempts Exhausted</div>
                                <div className="text-stone-300">{nodeState.error_message}</div>
                              </div>
                            </div>
                          </div>
                        )}
                      </div>
                    </td>
                    <td className="px-6 py-4">
                      {agents.length > 1 ? (
                        <select
                          value={node.host || ''}
                          onChange={(e) => onUpdateNode?.(node.id, { host: e.target.value || undefined })}
                          disabled={status === 'running' || status === 'booting' || status === 'stopping'}
                          className="bg-white dark:bg-stone-800 border border-stone-300 dark:border-stone-700 rounded px-2 py-1 text-xs text-stone-700 dark:text-stone-300 focus:outline-none focus:border-sage-500 disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                          <option value="">Auto</option>
                          {agents.map((agent) => (
                            <option key={agent.id} value={agent.id}>
                              {agent.name}
                            </option>
                          ))}
                        </select>
                      ) : (
                        <span className="text-stone-400 dark:text-stone-600 text-xs">Auto</span>
                      )}
                    </td>
                    <td className="px-6 py-4">
                      {(status === 'running' || status === 'booting' || status === 'stopping') && hostName ? (
                        <div className="flex items-center gap-1.5">
                          {agents.length > 1 && (
                            <div
                              className="w-2 h-2 rounded-full flex-shrink-0"
                              style={{ backgroundColor: getAgentColor(nodeState?.host_id || '') }}
                            />
                          )}
                          <span className="text-xs text-stone-600 dark:text-stone-400">{hostName}</span>
                        </div>
                      ) : (
                        <span className="text-stone-400 dark:text-stone-600 text-xs">—</span>
                      )}
                    </td>
                    <td className="px-6 py-4 text-right">
                      <div className="flex justify-end gap-1 opacity-60 group-hover:opacity-100 transition-opacity">
                        <>
                            {status === 'stopping' ? (
                              <button
                                disabled
                                className="p-2 text-orange-500 rounded-lg"
                                title="Stopping..."
                              >
                                <i className="fa-solid fa-spinner fa-spin"></i>
                              </button>
                            ) : status === 'stopped' ? (
                              <button
                                onClick={() => onUpdateStatus(node.id, 'booting')}
                                disabled={isOperationPending(node.id)}
                                className="p-2 text-green-500 hover:bg-green-500/10 disabled:opacity-50 disabled:cursor-not-allowed rounded-lg transition-all"
                                title={isLabDeployed ? "Start this node" : "Deploy lab (starts all nodes)"}
                              >
                                <i className={`fa-solid ${(pendingOps.has(node.id) || pendingNodeOps.has(node.id)) ? 'fa-spinner fa-spin' : (isLabDeployed ? 'fa-play' : 'fa-rocket')}`}></i>
                              </button>
                            ) : (
                              <>
                                <button
                                  onClick={() => onUpdateStatus(node.id, 'stopped')}
                                  disabled={isOperationPending(node.id)}
                                  className="p-2 text-red-500 hover:bg-red-500/10 disabled:opacity-50 disabled:cursor-not-allowed rounded-lg transition-all"
                                  title="Stop this node"
                                >
                                  <i className={`fa-solid ${(pendingOps.has(node.id) || pendingNodeOps.has(node.id)) ? 'fa-spinner fa-spin' : 'fa-power-off'}`}></i>
                                </button>
                                <button
                                  onClick={() => onUpdateStatus(node.id, 'booting')}
                                  disabled={isOperationPending(node.id)}
                                  className="p-2 text-stone-400 hover:bg-stone-400/10 disabled:opacity-50 disabled:cursor-not-allowed rounded-lg transition-all"
                                  title="Restart this node"
                                >
                                  <i className={`fa-solid ${(pendingOps.has(node.id) || pendingNodeOps.has(node.id)) ? 'fa-spinner fa-spin' : 'fa-rotate'}`}></i>
                                </button>
                              </>
                            )}
                        </>
                      </div>
                    </td>
                  </tr>
                );
              })}
              {nodes.length === 0 && (
                <tr>
                  <td colSpan={7} className="px-6 py-20 text-center text-stone-500 dark:text-stone-600 italic text-sm">
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
