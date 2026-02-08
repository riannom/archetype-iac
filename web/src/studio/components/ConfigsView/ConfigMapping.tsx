import React, { useState } from 'react';
import { DeviceNode } from '../../types';
import type { ConfigSnapshot } from './types';

interface ConfigMappingProps {
  labId: string;
  orphanedByDeviceKind: Map<string, ConfigSnapshot[]>;
  deviceNodes: DeviceNode[];
  getDisplayName: (containerName: string) => string;
  onMapConfig: (snapshotId: string, targetNodeId: string) => Promise<any>;
  onSetActiveConfig: (nodeName: string, snapshotId: string) => Promise<any>;
}

export const ConfigMapping: React.FC<ConfigMappingProps> = ({
  labId,
  orphanedByDeviceKind,
  deviceNodes,
  getDisplayName,
  onMapConfig,
  onSetActiveConfig,
}) => {
  const [selectedOrphan, setSelectedOrphan] = useState<{
    deviceKind: string;
    nodeName: string;
    latestSnapshot: ConfigSnapshot;
    snapshotCount: number;
  } | null>(null);
  const [showConfirmDialog, setShowConfirmDialog] = useState(false);
  const [selectedTargetNode, setSelectedTargetNode] = useState<DeviceNode | null>(null);
  const [isMapping, setIsMapping] = useState(false);

  // Convert Map to array for rendering
  const orphanedGroups = Array.from(orphanedByDeviceKind.entries()).map(
    ([deviceKind, snapshots]) => {
      // Group by node_name
      const byNode = new Map<string, ConfigSnapshot[]>();
      snapshots.forEach((snap) => {
        const existing = byNode.get(snap.node_name) || [];
        byNode.set(snap.node_name, [...existing, snap]);
      });

      return {
        deviceKind,
        nodes: Array.from(byNode.entries()).map(([nodeName, nodeSnapshots]) => {
          // Sort by created_at descending to get latest
          const sorted = [...nodeSnapshots].sort(
            (a, b) =>
              new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
          );
          return {
            nodeName,
            snapshots: sorted,
            latestSnapshot: sorted[0],
            count: sorted.length,
          };
        }),
      };
    }
  );

  const handleOrphanClick = (
    deviceKind: string,
    nodeName: string,
    latestSnapshot: ConfigSnapshot,
    snapshotCount: number
  ) => {
    setSelectedOrphan({ deviceKind, nodeName, latestSnapshot, snapshotCount });
  };

  const handleTargetNodeClick = (node: DeviceNode) => {
    if (!selectedOrphan) return;
    if (node.model !== selectedOrphan.deviceKind) return;

    setSelectedTargetNode(node);
    setShowConfirmDialog(true);
  };

  const handleConfirmMap = async () => {
    if (!selectedOrphan || !selectedTargetNode) return;

    setIsMapping(true);
    try {
      // Map the config to the target node
      await onMapConfig(selectedOrphan.latestSnapshot.id, selectedTargetNode.id);

      // Set it as the active startup config
      await onSetActiveConfig(
        (selectedTargetNode.container_name || selectedTargetNode.name),
        selectedOrphan.latestSnapshot.id
      );

      // Clear selection
      setSelectedOrphan(null);
      setSelectedTargetNode(null);
      setShowConfirmDialog(false);
    } catch (error) {
      console.error('Failed to map config:', error);
      alert('Failed to map config. See console for details.');
    } finally {
      setIsMapping(false);
    }
  };

  const handleCancelMap = () => {
    setSelectedTargetNode(null);
    setShowConfirmDialog(false);
  };

  // If no orphaned configs, show success message
  if (orphanedGroups.length === 0 || orphanedGroups.every((g) => g.nodes.length === 0)) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-center">
          <i className="fas fa-check-circle text-green-600 text-4xl mb-3"></i>
          <p className="text-lg font-medium text-stone-700 dark:text-stone-300">
            All configs are matched to active nodes
          </p>
          <p className="text-sm text-stone-500 dark:text-stone-400 mt-1">
            No orphaned configurations found
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-6">
        {/* Left Column: Orphaned Configs */}
        <div>
          <h3 className="text-sm font-semibold text-stone-700 dark:text-stone-300 mb-3 flex items-center">
            <i className="fas fa-link-slash text-amber-600 mr-2"></i>
            Orphaned Configs
          </h3>
          <div className="space-y-2">
            {orphanedGroups.map((group) =>
              group.nodes.map((node) => (
                <button
                  key={`${group.deviceKind}-${node.nodeName}`}
                  onClick={() =>
                    handleOrphanClick(
                      group.deviceKind,
                      node.nodeName,
                      node.latestSnapshot,
                      node.count
                    )
                  }
                  className={`w-full text-left p-4 rounded-lg border-2 transition-all ${
                    selectedOrphan?.nodeName === node.nodeName &&
                    selectedOrphan?.deviceKind === group.deviceKind
                      ? 'border-amber-500 bg-amber-50 dark:bg-amber-950 shadow-md'
                      : 'border-amber-200 dark:border-amber-800 bg-white dark:bg-stone-800 hover:border-amber-300 dark:hover:border-amber-600 hover:shadow-sm'
                  }`}
                >
                  <div className="flex items-start justify-between">
                    <div className="flex-1">
                      <div className="font-medium text-stone-900 dark:text-stone-100">
                        {node.nodeName}
                      </div>
                      <div className="text-xs text-stone-500 dark:text-stone-400 mt-1">
                        {group.deviceKind}
                      </div>
                      <div className="text-xs text-stone-400 dark:text-stone-500 mt-1">
                        {node.count} snapshot{node.count !== 1 ? 's' : ''}
                      </div>
                    </div>
                    <div className="text-right">
                      <div className="text-xs text-stone-500 dark:text-stone-400">Latest</div>
                      <div className="text-xs text-stone-400 dark:text-stone-500 mt-1">
                        {new Date(node.latestSnapshot.created_at).toLocaleDateString()}
                      </div>
                      <div className="text-xs text-stone-400 dark:text-stone-500">
                        {new Date(node.latestSnapshot.created_at).toLocaleTimeString()}
                      </div>
                    </div>
                  </div>
                </button>
              ))
            )}
          </div>
        </div>

        {/* Right Column: Available Nodes */}
        <div>
          <h3 className="text-sm font-semibold text-stone-700 dark:text-stone-300 mb-3 flex items-center">
            <i className="fas fa-link text-sage-600 mr-2"></i>
            Available Nodes
          </h3>
          {!selectedOrphan ? (
            <div className="text-center py-12 text-stone-400 dark:text-stone-500 text-sm">
              Select an orphaned config to see compatible nodes
            </div>
          ) : (
            <div className="space-y-2">
              {deviceNodes.map((node) => {
                const isCompatible = node.model === selectedOrphan.deviceKind;
                return (
                  <button
                    key={node.id}
                    onClick={() => handleTargetNodeClick(node)}
                    disabled={!isCompatible}
                    className={`w-full text-left p-4 rounded-lg border-2 transition-all ${
                      isCompatible
                        ? 'border-sage-200 dark:border-sage-800 bg-white dark:bg-stone-800 hover:border-sage-400 dark:hover:border-sage-600 hover:shadow-sm cursor-pointer'
                        : 'border-stone-200 dark:border-stone-700 bg-stone-50 dark:bg-stone-900 opacity-50 cursor-not-allowed'
                    }`}
                  >
                    <div className="flex items-start justify-between">
                      <div className="flex-1">
                        <div className="font-medium text-stone-900 dark:text-stone-100">
                          {getDisplayName((node.container_name || node.name))}
                        </div>
                        <div className="text-xs text-stone-500 dark:text-stone-400 mt-1">
                          {node.model}
                        </div>
                        {isCompatible && (
                          <div className="text-xs text-sage-600 mt-2 flex items-center">
                            <i className="fas fa-check-circle mr-1"></i>
                            Compatible
                          </div>
                        )}
                      </div>
                      {isCompatible && (
                        <i className="fas fa-arrow-right text-sage-400"></i>
                      )}
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* Confirmation Dialog */}
      {showConfirmDialog && selectedOrphan && selectedTargetNode && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-white dark:bg-stone-900 rounded-lg shadow-xl max-w-md w-full mx-4 p-6">
            <h3 className="text-lg font-semibold text-stone-900 dark:text-stone-100 mb-4">
              Confirm Config Mapping
            </h3>
            <div className="space-y-3 text-sm text-stone-700 dark:text-stone-300">
              <p>
                Map configs from{' '}
                <span className="font-medium text-amber-700">
                  {selectedOrphan.nodeName}
                </span>{' '}
                to{' '}
                <span className="font-medium text-sage-700">
                  {getDisplayName((selectedTargetNode.container_name || selectedTargetNode.name))}
                </span>
                ?
              </p>
              <div className="bg-stone-50 dark:bg-stone-800 p-3 rounded border border-stone-200 dark:border-stone-700">
                <div className="text-xs text-stone-500 dark:text-stone-400 mb-1">Device Type</div>
                <div className="font-medium">{selectedOrphan.deviceKind}</div>
              </div>
              <div className="bg-stone-50 dark:bg-stone-800 p-3 rounded border border-stone-200 dark:border-stone-700">
                <div className="text-xs text-stone-500 dark:text-stone-400 mb-1">Action</div>
                <div>
                  This will map {selectedOrphan.snapshotCount} snapshot
                  {selectedOrphan.snapshotCount !== 1 ? 's' : ''} and set the latest
                  config as the startup config.
                </div>
              </div>
            </div>
            <div className="flex justify-end gap-3 mt-6">
              <button
                onClick={handleCancelMap}
                disabled={isMapping}
                className="px-4 py-2 text-sm font-medium text-stone-700 dark:text-stone-300 bg-white dark:bg-stone-800 border border-stone-300 dark:border-stone-600 rounded-md hover:bg-stone-50 dark:hover:bg-stone-700 disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={handleConfirmMap}
                disabled={isMapping}
                className="px-4 py-2 text-sm font-medium text-white bg-sage-600 rounded-md hover:bg-sage-700 disabled:opacity-50 flex items-center gap-2"
              >
                {isMapping && (
                  <i className="fas fa-spinner fa-spin"></i>
                )}
                {isMapping ? 'Mapping...' : 'Confirm'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
