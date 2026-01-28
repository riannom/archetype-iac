import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Node, isDeviceNode, DeviceNode } from '../types';
import { RuntimeStatus } from './RuntimeControl';
import ConfigDiffViewer from './ConfigDiffViewer';

interface ConfigSnapshot {
  id: string;
  lab_id: string;
  node_name: string;
  content: string;
  content_hash: string;
  snapshot_type: string;
  created_at: string;
}

interface ConfigsViewProps {
  labId: string;
  nodes: Node[];
  runtimeStates: Record<string, RuntimeStatus>;
  studioRequest: <T>(path: string, options?: RequestInit) => Promise<T>;
  onExtractConfigs: () => Promise<void>;
}

const ConfigsView: React.FC<ConfigsViewProps> = ({
  labId,
  nodes,
  runtimeStates,
  studioRequest,
  onExtractConfigs,
}) => {
  const [snapshots, setSnapshots] = useState<ConfigSnapshot[]>([]);
  const [selectedNodeName, setSelectedNodeName] = useState<string | null>(null);
  const [selectedSnapshotIds, setSelectedSnapshotIds] = useState<Set<string>>(new Set());
  const [viewMode, setViewMode] = useState<'view' | 'compare'>('view');
  const [loading, setLoading] = useState(false);
  const [extracting, setExtracting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  // Filter to device nodes only (external networks don't have configs)
  const deviceNodes = useMemo(() => nodes.filter(isDeviceNode), [nodes]);

  // Map container_name -> display name for UI display
  const nodeDisplayNames = useMemo(() => {
    const map = new Map<string, string>();
    deviceNodes.forEach((n) => {
      const containerName = n.container_name || n.name;
      // Prefer display name, but use container_name as fallback
      map.set(containerName, n.name);
    });
    return map;
  }, [deviceNodes]);

  // Get unique node names from snapshots (these are container names)
  const nodeNamesWithSnapshots = useMemo(() => {
    const names = new Set<string>();
    snapshots.forEach((s) => names.add(s.node_name));
    return Array.from(names).sort();
  }, [snapshots]);

  // Get all node container names (from topology) - use container_name for matching
  const allNodeContainerNames = useMemo(() => {
    return deviceNodes.map((n) => n.container_name || n.name).sort();
  }, [deviceNodes]);

  // Merge node names from both sources (using container names as canonical key)
  const nodeNames = useMemo(() => {
    const names = new Set<string>();
    allNodeContainerNames.forEach((n) => names.add(n));
    nodeNamesWithSnapshots.forEach((n) => names.add(n));
    return Array.from(names).sort();
  }, [allNodeContainerNames, nodeNamesWithSnapshots]);

  // Get display name for a container name
  const getDisplayName = (containerName: string) => {
    return nodeDisplayNames.get(containerName) || containerName;
  };

  // Get snapshots for selected node
  const nodeSnapshots = useMemo(() => {
    if (!selectedNodeName) return [];
    return snapshots
      .filter((s) => s.node_name === selectedNodeName)
      .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
  }, [snapshots, selectedNodeName]);

  // Get selected snapshot for viewing
  const selectedSnapshot = useMemo(() => {
    const ids = Array.from(selectedSnapshotIds);
    if (ids.length === 1) {
      return snapshots.find((s) => s.id === ids[0]) || null;
    }
    return null;
  }, [snapshots, selectedSnapshotIds]);

  // Get snapshots for comparison
  const comparisonSnapshots = useMemo(() => {
    const ids = Array.from(selectedSnapshotIds);
    if (ids.length === 2) {
      const a = snapshots.find((s) => s.id === ids[0]);
      const b = snapshots.find((s) => s.id === ids[1]);
      if (a && b) {
        // Sort by creation time so older is first
        return new Date(a.created_at) < new Date(b.created_at) ? [a, b] : [b, a];
      }
    }
    return null;
  }, [snapshots, selectedSnapshotIds]);

  // Load snapshots
  const loadSnapshots = useCallback(async () => {
    if (!labId) return;
    setLoading(true);
    setError(null);
    try {
      const data = await studioRequest<{ snapshots: ConfigSnapshot[] }>(
        `/labs/${labId}/config-snapshots`
      );
      setSnapshots(data.snapshots || []);

      // Auto-select first node if none selected
      if (!selectedNodeName && data.snapshots && data.snapshots.length > 0) {
        const firstNode = data.snapshots[0].node_name;
        setSelectedNodeName(firstNode);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load snapshots');
    } finally {
      setLoading(false);
    }
  }, [labId, selectedNodeName, studioRequest]);

  useEffect(() => {
    loadSnapshots();
  }, [loadSnapshots]);

  // Handle extract configs
  const handleExtract = async () => {
    setExtracting(true);
    try {
      await onExtractConfigs();
      await loadSnapshots();
    } finally {
      setExtracting(false);
    }
  };

  // Handle snapshot selection
  const handleSnapshotClick = (snapshotId: string) => {
    setSelectedSnapshotIds((prev) => {
      const next = new Set(prev);
      if (viewMode === 'compare') {
        // In compare mode, allow selecting up to 2
        if (next.has(snapshotId)) {
          next.delete(snapshotId);
        } else if (next.size < 2) {
          next.add(snapshotId);
        } else {
          // Replace oldest selection
          const oldest = Array.from(next)[0];
          next.delete(oldest);
          next.add(snapshotId);
        }
      } else {
        // In view mode, single selection
        next.clear();
        next.add(snapshotId);
      }
      return next;
    });
  };

  // Handle delete snapshot
  const handleDeleteSnapshot = async (snapshotId: string) => {
    try {
      await studioRequest(`/labs/${labId}/config-snapshots/${snapshotId}`, {
        method: 'DELETE',
      });
      setSelectedSnapshotIds((prev) => {
        const next = new Set(prev);
        next.delete(snapshotId);
        return next;
      });
      await loadSnapshots();
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Failed to delete snapshot');
    }
  };

  // Handle copy config
  const handleCopy = async () => {
    if (selectedSnapshot) {
      await navigator.clipboard.writeText(selectedSnapshot.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  // Format timestamp
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

    return date.toLocaleDateString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  // Get node status indicator color (nodeName is container_name)
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

  return (
    <div className="flex-1 bg-stone-50 dark:bg-stone-950 flex flex-col overflow-hidden animate-in fade-in duration-300">
      {/* Header */}
      <header className="px-6 py-4 border-b border-stone-200 dark:border-stone-800 bg-white/50 dark:bg-stone-900/50 backdrop-blur-sm">
        <div className="flex flex-wrap justify-between items-end gap-4">
          <div>
            <h1 className="text-2xl font-black text-stone-900 dark:text-white tracking-tight">
              Configuration Snapshots
            </h1>
            <p className="text-stone-500 dark:text-stone-400 text-xs mt-1">
              View, compare, and track configuration changes across your devices.
            </p>
          </div>
          <div className="flex gap-3">
            <button
              onClick={handleExtract}
              disabled={extracting}
              className="px-4 py-2 bg-sage-600 hover:bg-sage-500 disabled:opacity-50 text-white rounded-lg text-xs font-bold transition-all shadow-sm flex items-center gap-2"
            >
              {extracting ? (
                <>
                  <i className="fa-solid fa-spinner fa-spin" />
                  Extracting...
                </>
              ) : (
                <>
                  <i className="fa-solid fa-download" />
                  Extract Configs
                </>
              )}
            </button>
            <button
              onClick={loadSnapshots}
              className="px-3 py-2 bg-stone-200 dark:bg-stone-800 hover:bg-stone-300 dark:hover:bg-stone-700 text-stone-700 dark:text-white rounded-lg text-xs font-bold transition-all"
            >
              <i className="fa-solid fa-rotate" />
            </button>
          </div>
        </div>
      </header>

      {/* Main content */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left panel - Node list */}
        <div className="w-56 border-r border-stone-200 dark:border-stone-800 flex flex-col overflow-hidden bg-white/30 dark:bg-stone-900/30">
          <div className="p-3 border-b border-stone-200 dark:border-stone-800">
            <div className="text-[10px] font-bold text-stone-500 uppercase tracking-widest">
              Nodes
            </div>
          </div>
          <div className="flex-1 overflow-y-auto custom-scrollbar">
            {nodeNames.length === 0 ? (
              <div className="p-4 text-center text-xs text-stone-500">
                No nodes in topology
              </div>
            ) : (
              nodeNames.map((containerName) => {
                const hasSnapshots = nodeNamesWithSnapshots.includes(containerName);
                const snapshotCount = snapshots.filter((s) => s.node_name === containerName).length;
                const isSelected = selectedNodeName === containerName;
                const displayName = getDisplayName(containerName);

                return (
                  <button
                    key={containerName}
                    onClick={() => {
                      setSelectedNodeName(containerName);
                      setSelectedSnapshotIds(new Set());
                      setViewMode('view');
                    }}
                    className={`w-full px-3 py-2.5 flex items-center gap-3 text-left transition-colors outline-none focus:outline-none focus:ring-0 active:outline-none ${
                      isSelected
                        ? 'bg-sage-600/20 border-r-2 border-sage-500'
                        : 'bg-transparent hover:bg-stone-100 dark:hover:bg-stone-800 focus:bg-transparent dark:focus:bg-transparent active:bg-stone-100 dark:active:bg-stone-800'
                    }`}
                  >
                    <div className={`w-2 h-2 rounded-full ${getNodeStatusColor(containerName)}`} />
                    <div className="flex-1 min-w-0">
                      <div
                        className={`text-xs font-medium truncate ${
                          isSelected
                            ? 'text-sage-700 dark:text-sage-300'
                            : 'text-stone-700 dark:text-stone-300'
                        }`}
                      >
                        {displayName}
                      </div>
                      {hasSnapshots && (
                        <div className="text-[10px] text-stone-500">
                          {snapshotCount} snapshot{snapshotCount !== 1 ? 's' : ''}
                        </div>
                      )}
                    </div>
                    {!hasSnapshots && (
                      <span className="text-[10px] text-stone-400 italic">No configs</span>
                    )}
                  </button>
                );
              })
            )}
          </div>
        </div>

        {/* Middle panel - Snapshot list */}
        <div className="w-72 border-r border-stone-200 dark:border-stone-800 flex flex-col overflow-hidden">
          <div className="p-3 border-b border-stone-200 dark:border-stone-800 flex items-center justify-between">
            <div className="text-[10px] font-bold text-stone-500 uppercase tracking-widest">
              Snapshots{selectedNodeName ? `: ${getDisplayName(selectedNodeName)}` : ''}
            </div>
            {nodeSnapshots.length > 1 && (
              <div className="flex gap-1">
                <button
                  onClick={() => {
                    setViewMode('view');
                    setSelectedSnapshotIds(new Set());
                  }}
                  className={`px-2 py-1 text-[10px] font-bold rounded transition-colors ${
                    viewMode === 'view'
                      ? 'bg-sage-600 text-white'
                      : 'bg-stone-200 dark:bg-stone-700 text-stone-600 dark:text-stone-300'
                  }`}
                >
                  View
                </button>
                <button
                  onClick={() => {
                    setViewMode('compare');
                    setSelectedSnapshotIds(new Set());
                  }}
                  className={`px-2 py-1 text-[10px] font-bold rounded transition-colors ${
                    viewMode === 'compare'
                      ? 'bg-sage-600 text-white'
                      : 'bg-stone-200 dark:bg-stone-700 text-stone-600 dark:text-stone-300'
                  }`}
                >
                  Compare
                </button>
              </div>
            )}
          </div>

          <div className="flex-1 overflow-y-auto custom-scrollbar p-2 space-y-2">
            {loading && (
              <div className="flex items-center justify-center py-8">
                <i className="fa-solid fa-spinner fa-spin text-stone-400" />
              </div>
            )}

            {!loading && nodeSnapshots.length === 0 && selectedNodeName && (
              <div className="text-center py-8">
                <i className="fa-solid fa-file-circle-xmark text-2xl text-stone-300 dark:text-stone-700 mb-3" />
                <p className="text-xs text-stone-500">No snapshots for this node</p>
                <p className="text-[10px] text-stone-400 mt-1">
                  Click "Extract Configs" to create one
                </p>
              </div>
            )}

            {!loading && !selectedNodeName && (
              <div className="text-center py-8">
                <i className="fa-solid fa-hand-pointer text-2xl text-stone-300 dark:text-stone-700 mb-3" />
                <p className="text-xs text-stone-500">Select a node to view snapshots</p>
              </div>
            )}

            {viewMode === 'compare' && nodeSnapshots.length > 0 && (
              <div className="bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-lg p-2 text-[10px] text-amber-700 dark:text-amber-300">
                <i className="fa-solid fa-info-circle mr-1" />
                Select 2 snapshots to compare
                {selectedSnapshotIds.size > 0 && (
                  <span className="ml-1">({selectedSnapshotIds.size}/2 selected)</span>
                )}
              </div>
            )}

            {nodeSnapshots.map((snapshot) => {
              const isSelected = selectedSnapshotIds.has(snapshot.id);
              return (
                <div
                  key={snapshot.id}
                  onClick={() => handleSnapshotClick(snapshot.id)}
                  className={`p-3 rounded-lg border cursor-pointer transition-all group outline-none ${
                    isSelected
                      ? 'bg-sage-600/20 border-sage-500'
                      : 'bg-stone-100 dark:bg-stone-800 border-stone-200 dark:border-stone-700 hover:border-stone-300 dark:hover:border-stone-600'
                  }`}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        {viewMode === 'compare' && (
                          <div
                            className={`w-4 h-4 rounded border-2 flex items-center justify-center ${
                              isSelected
                                ? 'bg-sage-500 border-sage-500 text-white'
                                : 'border-stone-300 dark:border-stone-600'
                            }`}
                          >
                            {isSelected && <i className="fa-solid fa-check text-[8px]" />}
                          </div>
                        )}
                        <div className="text-xs font-medium text-stone-700 dark:text-stone-300">
                          {formatTimestamp(snapshot.created_at)}
                        </div>
                      </div>
                      <div className="flex items-center gap-2 mt-1">
                        <span
                          className={`px-1.5 py-0.5 text-[9px] font-bold uppercase rounded ${
                            snapshot.snapshot_type === 'manual'
                              ? 'bg-blue-100 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400'
                              : 'bg-amber-100 dark:bg-amber-900/30 text-amber-600 dark:text-amber-400'
                          }`}
                        >
                          {snapshot.snapshot_type === 'manual' ? 'Manual' : 'Auto'}
                        </span>
                        <span className="text-[10px] text-stone-400 font-mono truncate">
                          {snapshot.content_hash.slice(0, 8)}
                        </span>
                      </div>
                    </div>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        if (confirm('Delete this snapshot?')) {
                          handleDeleteSnapshot(snapshot.id);
                        }
                      }}
                      className="opacity-0 group-hover:opacity-100 p-1 text-stone-400 hover:text-red-500 transition-all"
                    >
                      <i className="fa-solid fa-trash-can text-xs" />
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* Right panel - Config viewer */}
        <div className="flex-1 flex flex-col overflow-hidden">
          {/* Config header */}
          {(selectedSnapshot || comparisonSnapshots) && (
            <div className="p-3 border-b border-stone-200 dark:border-stone-800 flex items-center justify-between bg-white/30 dark:bg-stone-900/30">
              <div className="text-xs text-stone-500 dark:text-stone-400">
                {viewMode === 'compare' && comparisonSnapshots ? (
                  <>
                    <i className="fa-solid fa-code-compare mr-2" />
                    Comparing {comparisonSnapshots.length} snapshots
                  </>
                ) : selectedSnapshot ? (
                  <>
                    <i className="fa-solid fa-clock mr-1" />
                    {new Date(selectedSnapshot.created_at).toLocaleString()}
                  </>
                ) : null}
              </div>
              {selectedSnapshot && viewMode === 'view' && (
                <button
                  onClick={handleCopy}
                  className="flex items-center gap-2 px-3 py-1.5 text-xs font-medium bg-stone-100 dark:bg-stone-800 hover:bg-stone-200 dark:hover:bg-stone-700 text-stone-700 dark:text-stone-300 rounded-lg transition-colors"
                >
                  <i className={`fa-solid ${copied ? 'fa-check' : 'fa-copy'}`} />
                  {copied ? 'Copied!' : 'Copy'}
                </button>
              )}
            </div>
          )}

          {/* Config content */}
          <div className="flex-1 overflow-auto bg-stone-950">
            {error && (
              <div className="p-8 text-center">
                <i className="fa-solid fa-exclamation-circle text-2xl text-red-500 mb-2" />
                <p className="text-sm text-stone-400">{error}</p>
              </div>
            )}

            {!error && !selectedSnapshot && !comparisonSnapshots && (
              <div className="flex items-center justify-center h-full">
                <div className="text-center">
                  <i className="fa-solid fa-file-code text-4xl text-stone-700 mb-4" />
                  <p className="text-sm text-stone-500">
                    {viewMode === 'compare'
                      ? 'Select 2 snapshots to compare'
                      : 'Select a snapshot to view its content'}
                  </p>
                </div>
              </div>
            )}

            {viewMode === 'view' && selectedSnapshot && (
              <pre className="p-4 text-xs font-mono text-sage-400 whitespace-pre overflow-x-auto">
                {selectedSnapshot.content}
              </pre>
            )}

            {viewMode === 'compare' && comparisonSnapshots && (
              <ConfigDiffViewer
                snapshotA={comparisonSnapshots[0]}
                snapshotB={comparisonSnapshots[1]}
                studioRequest={studioRequest}
                labId={labId}
              />
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default ConfigsView;
