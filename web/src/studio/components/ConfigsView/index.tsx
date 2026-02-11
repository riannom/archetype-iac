import React, { useState } from 'react';
import { isDeviceNode } from '../../types';
import { useConfigManager } from '../../hooks/useConfigManager';
import { NodeList } from './NodeList';
import SnapshotList from './SnapshotList';
import ConfigViewer from './ConfigViewer';
import { ConfigMapping } from './ConfigMapping';
import { ConfigActions } from './ConfigActions';
import type { ConfigsViewProps } from './types';

type Tab = 'snapshots' | 'mapping';

const ConfigsView: React.FC<ConfigsViewProps> = ({
  labId,
  nodes,
  runtimeStates,
  studioRequest,
  onExtractConfigs,
}) => {
  const [activeTab, setActiveTab] = useState<Tab>('snapshots');

  const mgr = useConfigManager({ labId, nodes, studioRequest });

  const handleExtract = async () => {
    mgr.setExtracting(true);
    try {
      await onExtractConfigs();
      await mgr.loadSnapshots();
    } finally {
      mgr.setExtracting(false);
    }
  };

  const handleBulkDeleteOrphaned = async () => {
    try {
      await mgr.bulkDeleteSnapshots({ orphanedOnly: true, force: true });
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Failed to delete orphaned configs');
    }
  };

  const handleDownloadAll = async () => {
    try {
      await mgr.downloadConfigs({ all: true });
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Download failed');
    }
  };

  const handleDownloadNode = async (nodeName: string) => {
    try {
      await mgr.downloadConfigs({ nodeNames: [nodeName] });
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Download failed');
    }
  };

  const handleDeleteSnapshot = async (snapshotId: string) => {
    try {
      await mgr.deleteSnapshot(snapshotId);
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Failed to delete snapshot');
    }
  };

  return (
    <div className="flex-1 bg-transparent flex flex-col overflow-hidden animate-in fade-in duration-300">
      {/* Header */}
      <header className="px-6 py-4 border-b border-stone-200 dark:border-stone-800 glass-surface">
        <div className="flex flex-wrap justify-between items-end gap-4">
          <div>
            <h1 className="text-2xl font-black text-stone-900 dark:text-white tracking-tight">
              Configuration Management
            </h1>
            <p className="text-stone-500 dark:text-stone-400 text-xs mt-1">
              View, compare, map, and manage configuration snapshots across your devices.
            </p>
          </div>
          <div className="flex items-center gap-4">
            {/* Tab navigation */}
            <div className="flex gap-1 glass-control rounded-lg p-0.5 border">
              <button
                onClick={() => setActiveTab('snapshots')}
                className={`px-3 py-1.5 text-xs font-bold rounded-md transition-colors ${
                  activeTab === 'snapshots'
                    ? 'glass-surface text-stone-900 dark:text-white shadow-sm'
                    : 'text-stone-500 dark:text-stone-400 hover:text-stone-700 dark:hover:text-stone-300'
                }`}
              >
                <i className="fa-solid fa-clock-rotate-left mr-1.5" />
                Snapshots
              </button>
              <button
                onClick={() => setActiveTab('mapping')}
                className={`px-3 py-1.5 text-xs font-bold rounded-md transition-colors ${
                  activeTab === 'mapping'
                    ? 'glass-surface text-stone-900 dark:text-white shadow-sm'
                    : 'text-stone-500 dark:text-stone-400 hover:text-stone-700 dark:hover:text-stone-300'
                }`}
              >
                <i className="fa-solid fa-link mr-1.5" />
                Mapping
                {mgr.orphanedSnapshots.length > 0 && (
                  <span className="ml-1.5 px-1.5 py-0.5 text-[9px] bg-amber-500 text-white rounded-full">
                    {mgr.orphanedNodeNames.length}
                  </span>
                )}
              </button>
            </div>
            {/* Action buttons */}
            <ConfigActions
              extracting={mgr.extracting}
              orphanedCount={mgr.orphanedSnapshots.length}
              onExtract={handleExtract}
              onDownloadAll={handleDownloadAll}
              onDeleteAllOrphaned={handleBulkDeleteOrphaned}
            />
          </div>
        </div>
      </header>

      {/* Main content */}
      {activeTab === 'snapshots' ? (
        <div className="flex-1 flex overflow-hidden">
          <NodeList
            nodeNames={mgr.nodeNames}
            orphanedNodeNames={mgr.orphanedNodeNames}
            selectedNodeName={mgr.selectedNodeName}
            nodeNamesWithSnapshots={mgr.nodeNamesWithSnapshots}
            snapshots={mgr.snapshots}
            activeNodeNames={mgr.activeNodeNames}
            runtimeStates={runtimeStates}
            deviceNodes={mgr.deviceNodes}
            getDisplayName={mgr.getDisplayName}
            onSelectNode={mgr.selectNode}
            onBulkDeleteOrphaned={handleBulkDeleteOrphaned}
          />
          <SnapshotList
            selectedNodeName={mgr.selectedNodeName}
            nodeSnapshots={mgr.nodeSnapshots}
            selectedSnapshotIds={mgr.selectedSnapshotIds}
            viewMode={mgr.viewMode}
            loading={mgr.loading}
            getDisplayName={mgr.getDisplayName}
            onSnapshotClick={mgr.handleSnapshotClick}
            onDeleteSnapshot={handleDeleteSnapshot}
            onSetActiveConfig={mgr.setActiveConfig}
            onSetViewMode={mgr.setViewMode}
            onDownloadNode={handleDownloadNode}
          />
          <ConfigViewer
            selectedSnapshot={mgr.selectedSnapshot}
            comparisonSnapshots={mgr.comparisonSnapshots}
            viewMode={mgr.viewMode}
            error={mgr.error}
            labId={labId}
            studioRequest={studioRequest}
          />
        </div>
      ) : (
        <div className="flex-1 overflow-auto">
          <ConfigMapping
            labId={labId}
            orphanedByDeviceKind={mgr.orphanedByDeviceKind}
            deviceNodes={mgr.deviceNodes}
            getDisplayName={mgr.getDisplayName}
            onMapConfig={mgr.mapConfig}
            onSetActiveConfig={mgr.setActiveConfig}
          />
        </div>
      )}
    </div>
  );
};

export default ConfigsView;
