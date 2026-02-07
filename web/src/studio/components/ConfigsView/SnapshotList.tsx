import React, { useState } from 'react';
import type { ConfigSnapshot } from './types';

interface SnapshotListProps {
  selectedNodeName: string | null;
  nodeSnapshots: ConfigSnapshot[];
  selectedSnapshotIds: Set<string>;
  viewMode: 'view' | 'compare';
  loading: boolean;
  getDisplayName: (containerName: string) => string;
  onSnapshotClick: (snapshotId: string) => void;
  onDeleteSnapshot: (snapshotId: string) => void;
  onSetActiveConfig: (nodeName: string, snapshotId: string) => Promise<any>;
  onSetViewMode: (mode: 'view' | 'compare') => void;
  onDownloadNode: (nodeName: string) => void;
}

const formatTimestamp = (timestamp: string) => {
  const date = new Date(timestamp);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMins / 60);
  const diffDays = Math.floor(diffHours / 24);
  if (diffMins < 1) return 'Just now';
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays < 7) return `${diffDays}d ago`;
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
};

const SnapshotList: React.FC<SnapshotListProps> = ({
  selectedNodeName,
  nodeSnapshots,
  selectedSnapshotIds,
  viewMode,
  loading,
  getDisplayName,
  onSnapshotClick,
  onDeleteSnapshot,
  onSetActiveConfig,
  onSetViewMode,
  onDownloadNode,
}) => {
  const [hoveredSnapshot, setHoveredSnapshot] = useState<string | null>(null);
  const [settingActive, setSettingActive] = useState<string | null>(null);

  const handleSetActive = async (snapshotId: string) => {
    if (!selectedNodeName) return;

    const confirmed = window.confirm(
      'Set this as the startup config? The node will use this config on next reload.'
    );

    if (!confirmed) return;

    try {
      setSettingActive(snapshotId);
      await onSetActiveConfig(selectedNodeName, snapshotId);
    } finally {
      setSettingActive(null);
    }
  };

  const handleDelete = (snapshotId: string) => {
    const confirmed = window.confirm('Delete this snapshot? This action cannot be undone.');
    if (confirmed) {
      onDeleteSnapshot(snapshotId);
    }
  };

  const getTypeBadgeClass = (type: string) => {
    switch (type) {
      case 'manual':
        return 'bg-blue-500/10 text-blue-400 border-blue-500/20';
      case 'pre_stop':
        return 'bg-purple-500/10 text-purple-400 border-purple-500/20';
      case 'auto':
        return 'bg-amber-500/10 text-amber-400 border-amber-500/20';
      default:
        return 'bg-stone-500/10 text-stone-400 border-stone-500/20';
    }
  };

  const getTypeLabel = (type: string) => {
    switch (type) {
      case 'manual':
        return 'Manual';
      case 'pre_stop':
        return 'Pre-Stop';
      case 'auto':
        return 'Auto';
      default:
        return type;
    }
  };

  // Loading state (before node selection)
  if (loading && !selectedNodeName) {
    return (
      <div className="flex-1 flex flex-col bg-stone-900 border-l border-r border-stone-800">
        <div className="flex-1 flex items-center justify-center p-8">
          <div className="text-stone-500">
            <i className="fas fa-spinner fa-spin mr-2"></i>
            Loading snapshots...
          </div>
        </div>
      </div>
    );
  }

  // Empty state: no node selected
  if (!selectedNodeName) {
    return (
      <div className="flex-1 flex flex-col bg-stone-900 border-l border-r border-stone-800">
        <div className="flex-1 flex items-center justify-center p-8">
          <div className="text-center text-stone-500">
            <i className="fas fa-arrow-left text-4xl mb-4 opacity-50"></i>
            <p className="text-lg">Select a node to view snapshots</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col bg-stone-900 border-l border-r border-stone-800">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-stone-800 bg-stone-900/50">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-medium text-stone-300">
            {getDisplayName(selectedNodeName)}
          </h3>
          <span className="text-xs text-stone-500">
            ({nodeSnapshots.length} snapshot{nodeSnapshots.length !== 1 ? 's' : ''})
          </span>
        </div>

        <div className="flex items-center gap-2">
          {/* Download Button */}
          <button
            onClick={() => onDownloadNode(selectedNodeName)}
            className="px-2 py-1 text-xs text-stone-400 hover:text-stone-300 hover:bg-stone-800 rounded transition-colors"
            title="Download all snapshots"
          >
            <i className="fas fa-download"></i>
          </button>

          {/* View/Compare Mode Toggle */}
          {nodeSnapshots.length >= 2 && (
            <div className="flex gap-1 bg-stone-800 rounded p-0.5">
              <button
                onClick={() => onSetViewMode('view')}
                className={`px-2 py-1 text-xs rounded transition-colors ${
                  viewMode === 'view'
                    ? 'bg-sage-600 text-white'
                    : 'text-stone-400 hover:text-stone-300'
                }`}
              >
                View
              </button>
              <button
                onClick={() => onSetViewMode('compare')}
                className={`px-2 py-1 text-xs rounded transition-colors ${
                  viewMode === 'compare'
                    ? 'bg-sage-600 text-white'
                    : 'text-stone-400 hover:text-stone-300'
                }`}
              >
                Compare
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Compare Mode Info Bar */}
      {viewMode === 'compare' && (
        <div className="px-4 py-2 bg-sage-500/10 border-b border-sage-500/20">
          <p className="text-xs text-sage-400">
            Select 2 snapshots to compare ({selectedSnapshotIds.size}/2 selected)
          </p>
        </div>
      )}

      {/* Snapshot List */}
      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <div className="flex items-center justify-center p-8">
            <div className="text-stone-500">
              <i className="fas fa-spinner fa-spin mr-2"></i>
              Loading snapshots...
            </div>
          </div>
        ) : nodeSnapshots.length === 0 ? (
          <div className="flex items-center justify-center p-8">
            <div className="text-center text-stone-500">
              <i className="fas fa-inbox text-4xl mb-4 opacity-50"></i>
              <p className="text-lg mb-2">No snapshots</p>
              <p className="text-sm">Extract configs to create snapshots</p>
            </div>
          </div>
        ) : (
          <div className="p-4 space-y-2">
            {nodeSnapshots.map((snapshot) => {
              const isSelected = selectedSnapshotIds.has(snapshot.id);
              const isHovered = hoveredSnapshot === snapshot.id;
              const isSettingActive = settingActive === snapshot.id;

              return (
                <div
                  key={snapshot.id}
                  onMouseEnter={() => setHoveredSnapshot(snapshot.id)}
                  onMouseLeave={() => setHoveredSnapshot(null)}
                  onClick={() => onSnapshotClick(snapshot.id)}
                  className={`
                    relative p-3 rounded-lg border transition-all cursor-pointer
                    ${
                      isSelected
                        ? 'bg-sage-500/10 border-sage-500/30'
                        : 'bg-stone-800/50 border-stone-700 hover:border-stone-600'
                    }
                  `}
                >
                  {/* Active Config Indicator */}
                  {snapshot.is_active && (
                    <div className="absolute top-2 right-2 flex items-center gap-1.5">
                      <i className="fas fa-star text-amber-400 text-xs"></i>
                      <span className="text-xs font-medium text-amber-400 bg-amber-500/10 px-2 py-0.5 rounded border border-amber-500/20">
                        Active
                      </span>
                    </div>
                  )}

                  {/* Snapshot Info */}
                  <div className="space-y-2">
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex-1">
                        <div className="text-sm text-stone-300 font-medium">
                          {formatTimestamp(snapshot.created_at)}
                        </div>
                        <div className="text-xs text-stone-500 font-mono mt-0.5">
                          {snapshot.content_hash?.slice(0, 8) || 'N/A'}
                        </div>
                      </div>
                    </div>

                    <div className="flex items-center gap-2">
                      {/* Type Badge */}
                      <span
                        className={`text-xs px-2 py-0.5 rounded border ${getTypeBadgeClass(
                          snapshot.snapshot_type
                        )}`}
                      >
                        {getTypeLabel(snapshot.snapshot_type)}
                      </span>

                      {/* Action Buttons (on hover) */}
                      {isHovered && !snapshot.is_active && (
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            handleSetActive(snapshot.id);
                          }}
                          disabled={isSettingActive}
                          className="text-xs text-stone-400 hover:text-amber-400 transition-colors"
                          title="Set as startup config"
                        >
                          {isSettingActive ? (
                            <i className="fas fa-spinner fa-spin"></i>
                          ) : (
                            <i className="far fa-star"></i>
                          )}
                        </button>
                      )}

                      {isHovered && (
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            handleDelete(snapshot.id);
                          }}
                          className="text-xs text-stone-400 hover:text-red-400 transition-colors ml-auto"
                          title="Delete snapshot"
                        >
                          <i className="fas fa-trash"></i>
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
};

export default SnapshotList;
