import React from 'react';
import { DeviceNode } from '../../types';
import { RuntimeStatus } from '../RuntimeControl';
import type { ConfigSnapshot } from './types';

interface NodeListProps {
  nodeNames: string[];
  orphanedNodeNames: string[];
  selectedNodeName: string | null;
  nodeNamesWithSnapshots: string[];
  snapshots: ConfigSnapshot[];
  activeNodeNames: Set<string>;
  runtimeStates: Record<string, RuntimeStatus>;
  deviceNodes: DeviceNode[];
  getDisplayName: (containerName: string) => string;
  onSelectNode: (containerName: string) => void;
  onBulkDeleteOrphaned: () => void;
}

export const NodeList: React.FC<NodeListProps> = ({
  nodeNames,
  orphanedNodeNames,
  selectedNodeName,
  nodeNamesWithSnapshots,
  snapshots,
  activeNodeNames,
  runtimeStates,
  deviceNodes,
  getDisplayName,
  onSelectNode,
  onBulkDeleteOrphaned,
}) => {
  const getNodeStatusColor = (containerName: string) => {
    const node = deviceNodes.find((n) => (n.container_name || n.name) === containerName);
    if (!node) return 'bg-stone-400';
    const status = runtimeStates[node.id];
    switch (status) {
      case 'running':
        return 'bg-emerald-500';
      case 'booting':
        return 'bg-amber-500 animate-pulse';
      case 'stopped':
        return 'bg-stone-400';
      case 'error':
        return 'bg-red-500';
      default:
        return 'bg-stone-400';
    }
  };

  const getSnapshotCount = (containerName: string) => {
    return snapshots.filter((s) => s.node_name === containerName).length;
  };

  const getOrphanedDeviceKind = (containerName: string) => {
    const snapshot = snapshots.find((s) => s.node_name === containerName);
    return snapshot?.device_kind || null;
  };

  return (
    <div className="w-64 border-r border-stone-700 bg-stone-800/50 flex flex-col">
      {/* Active Nodes Section */}
      <div className="flex-1 overflow-y-auto">
        <div className="p-3 border-b border-stone-700 bg-stone-800/70">
          <h3 className="text-sm font-semibold text-stone-200">
            Active Nodes ({nodeNames.length})
          </h3>
        </div>
        <div className="divide-y divide-stone-700/50">
          {nodeNames.map((containerName) => {
            const hasSnapshots = nodeNamesWithSnapshots.includes(containerName);
            const snapshotCount = getSnapshotCount(containerName);
            const isSelected = selectedNodeName === containerName;
            const isActive = activeNodeNames.has(containerName);

            return (
              <button
                key={containerName}
                onClick={() => onSelectNode(containerName)}
                className={`w-full text-left px-4 py-3 hover:bg-sage-600/10 transition-colors ${
                  isSelected ? 'bg-sage-600/20 border-r-2 border-sage-500' : ''
                }`}
              >
                <div className="flex items-center gap-3">
                  {/* Status Indicator */}
                  <div
                    className={`w-2 h-2 rounded-full flex-shrink-0 ${getNodeStatusColor(
                      containerName
                    )}`}
                  />

                  {/* Node Info */}
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium text-stone-200 truncate">
                      {getDisplayName(containerName)}
                    </div>
                    {hasSnapshots && (
                      <div className="text-xs text-stone-400 mt-0.5">
                        {snapshotCount} snapshot{snapshotCount !== 1 ? 's' : ''}
                      </div>
                    )}
                    {!isActive && (
                      <div className="text-xs text-amber-400 mt-0.5">Inactive</div>
                    )}
                  </div>
                </div>
              </button>
            );
          })}
        </div>
      </div>

      {/* Orphaned Configs Section */}
      {orphanedNodeNames.length > 0 && (
        <div className="border-t border-stone-700">
          <div className="p-3 border-b border-stone-700 bg-stone-800/70 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-semibold text-amber-400">Orphaned Configs</h3>
              <span className="px-2 py-0.5 text-xs font-medium rounded-full bg-amber-500/20 text-amber-300">
                {orphanedNodeNames.length}
              </span>
            </div>
            <button
              onClick={onBulkDeleteOrphaned}
              className="px-2 py-1 text-xs font-medium text-red-400 hover:text-red-300 hover:bg-red-500/10 rounded transition-colors"
              title="Delete all orphaned configs"
            >
              Delete All
            </button>
          </div>
          <div className="divide-y divide-stone-700/50 max-h-64 overflow-y-auto">
            {orphanedNodeNames.map((containerName) => {
              const snapshotCount = getSnapshotCount(containerName);
              const isSelected = selectedNodeName === containerName;
              const deviceKind = getOrphanedDeviceKind(containerName);

              return (
                <button
                  key={containerName}
                  onClick={() => onSelectNode(containerName)}
                  className={`w-full text-left px-4 py-3 hover:bg-amber-500/5 transition-colors ${
                    isSelected ? 'bg-sage-600/20 border-r-2 border-sage-500' : ''
                  }`}
                >
                  <div className="flex items-center gap-3">
                    {/* Warning Indicator for Orphaned */}
                    <div className="w-2 h-2 rounded-full flex-shrink-0 bg-amber-500" />

                    {/* Node Info */}
                    <div className="flex-1 min-w-0">
                      <div className="text-sm font-medium text-stone-300 truncate">
                        {getDisplayName(containerName)}
                      </div>
                      <div className="flex items-center gap-2 mt-0.5">
                        {deviceKind && (
                          <span className="text-xs px-1.5 py-0.5 rounded bg-stone-700/50 text-stone-400">
                            {deviceKind}
                          </span>
                        )}
                        <span className="text-xs text-stone-400">
                          {snapshotCount} snapshot{snapshotCount !== 1 ? 's' : ''}
                        </span>
                      </div>
                    </div>
                  </div>
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
};
