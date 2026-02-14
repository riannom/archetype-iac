import { useCallback, useEffect, useMemo, useState } from 'react';
import { Node, isDeviceNode } from '../types';
import type { ConfigSnapshot } from '../components/ConfigsView/types';
import { downloadBlob } from '../../utils/download';

interface UseConfigManagerOptions {
  labId: string;
  nodes: Node[];
  studioRequest: <T>(path: string, options?: RequestInit) => Promise<T>;
}

export function useConfigManager({ labId, nodes, studioRequest }: UseConfigManagerOptions) {
  const [snapshots, setSnapshots] = useState<ConfigSnapshot[]>([]);
  const [selectedNodeName, setSelectedNodeName] = useState<string | null>(null);
  const [selectedSnapshotIds, setSelectedSnapshotIds] = useState<Set<string>>(new Set());
  const [viewMode, setViewMode] = useState<'view' | 'compare'>('view');
  const [loading, setLoading] = useState(false);
  const [extracting, setExtracting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Device nodes only
  const deviceNodes = useMemo(() => nodes.filter(isDeviceNode), [nodes]);

  // Container name -> display name mapping
  const nodeDisplayNames = useMemo(() => {
    const map = new Map<string, string>();
    deviceNodes.forEach((n) => {
      const containerName = n.container_name || n.name;
      map.set(containerName, n.name);
    });
    return map;
  }, [deviceNodes]);

  const getDisplayName = useCallback(
    (containerName: string) => nodeDisplayNames.get(containerName) || containerName,
    [nodeDisplayNames]
  );

  // Node names with snapshots
  const nodeNamesWithSnapshots = useMemo(() => {
    const names = new Set<string>();
    snapshots.forEach((s) => names.add(s.node_name));
    return Array.from(names).sort();
  }, [snapshots]);

  // All node container names from topology
  const allNodeContainerNames = useMemo(
    () => deviceNodes.map((n) => n.container_name || n.name).sort(),
    [deviceNodes]
  );

  // Active nodes (exist in topology)
  const activeNodeNames = useMemo(() => {
    const names = new Set<string>();
    allNodeContainerNames.forEach((n) => names.add(n));
    return names;
  }, [allNodeContainerNames]);

  // Merged node names (topology + snapshot-only)
  const nodeNames = useMemo(() => {
    const names = new Set<string>();
    allNodeContainerNames.forEach((n) => names.add(n));
    nodeNamesWithSnapshots.forEach((n) => names.add(n));
    return Array.from(names).sort();
  }, [allNodeContainerNames, nodeNamesWithSnapshots]);

  // Orphaned node names (have snapshots but no longer in topology)
  const orphanedNodeNames = useMemo(
    () => nodeNamesWithSnapshots.filter((n) => !activeNodeNames.has(n)),
    [nodeNamesWithSnapshots, activeNodeNames]
  );

  // Snapshots grouped by orphan status
  const orphanedSnapshots = useMemo(
    () => snapshots.filter((s) => s.is_orphaned),
    [snapshots]
  );

  // Orphaned configs grouped by device_kind
  const orphanedByDeviceKind = useMemo(() => {
    const grouped = new Map<string, ConfigSnapshot[]>();
    orphanedSnapshots.forEach((s) => {
      const kind = s.device_kind || 'unknown';
      const list = grouped.get(kind) || [];
      list.push(s);
      grouped.set(kind, list);
    });
    return grouped;
  }, [orphanedSnapshots]);

  // Snapshots for selected node
  const nodeSnapshots = useMemo(() => {
    if (!selectedNodeName) return [];
    return snapshots
      .filter((s) => s.node_name === selectedNodeName)
      .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
  }, [snapshots, selectedNodeName]);

  // Single selected snapshot
  const selectedSnapshot = useMemo(() => {
    const ids = Array.from(selectedSnapshotIds);
    if (ids.length === 1) {
      return snapshots.find((s) => s.id === ids[0]) || null;
    }
    return null;
  }, [snapshots, selectedSnapshotIds]);

  // Comparison snapshots (sorted older first)
  const comparisonSnapshots = useMemo((): [ConfigSnapshot, ConfigSnapshot] | null => {
    const ids = Array.from(selectedSnapshotIds);
    if (ids.length === 2) {
      const a = snapshots.find((s) => s.id === ids[0]);
      const b = snapshots.find((s) => s.id === ids[1]);
      if (a && b) {
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
      if (!selectedNodeName && data.snapshots && data.snapshots.length > 0) {
        setSelectedNodeName(data.snapshots[0].node_name);
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

  // Select node
  const selectNode = useCallback((containerName: string) => {
    setSelectedNodeName(containerName);
    setSelectedSnapshotIds(new Set());
    setViewMode('view');
  }, []);

  // Handle snapshot click
  const handleSnapshotClick = useCallback(
    (snapshotId: string) => {
      setSelectedSnapshotIds((prev) => {
        const next = new Set(prev);
        if (viewMode === 'compare') {
          if (next.has(snapshotId)) {
            next.delete(snapshotId);
          } else if (next.size < 2) {
            next.add(snapshotId);
          } else {
            const oldest = Array.from(next)[0];
            next.delete(oldest);
            next.add(snapshotId);
          }
        } else {
          next.clear();
          next.add(snapshotId);
        }
        return next;
      });
    },
    [viewMode]
  );

  // Delete single snapshot
  const deleteSnapshot = useCallback(
    async (snapshotId: string) => {
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
        throw err instanceof Error ? err : new Error('Failed to delete snapshot');
      }
    },
    [labId, studioRequest, loadSnapshots]
  );

  // Bulk delete
  const bulkDeleteSnapshots = useCallback(
    async (opts: { nodeName?: string; orphanedOnly?: boolean; force?: boolean }) => {
      const params = new URLSearchParams();
      if (opts.nodeName) params.set('node_name', opts.nodeName);
      if (opts.orphanedOnly) params.set('orphaned_only', 'true');
      if (opts.force) params.set('force', 'true');

      const result = await studioRequest<{ deleted_count: number }>(
        `/labs/${labId}/config-snapshots?${params.toString()}`,
        { method: 'DELETE' }
      );
      await loadSnapshots();
      return result;
    },
    [labId, studioRequest, loadSnapshots]
  );

  // Map config to node
  const mapConfig = useCallback(
    async (snapshotId: string, targetNodeId: string) => {
      const result = await studioRequest<ConfigSnapshot>(
        `/labs/${labId}/config-snapshots/${snapshotId}/map`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ target_node_id: targetNodeId }),
        }
      );
      await loadSnapshots();
      return result;
    },
    [labId, studioRequest, loadSnapshots]
  );

  // Set active config
  const setActiveConfig = useCallback(
    async (nodeName: string, snapshotId: string) => {
      const result = await studioRequest<{ success: boolean; message: string }>(
        `/labs/${labId}/nodes/${nodeName}/active-config`,
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ snapshot_id: snapshotId }),
        }
      );
      await loadSnapshots();
      return result;
    },
    [labId, studioRequest, loadSnapshots]
  );

  // Download configs
  const downloadConfigs = useCallback(
    async (opts?: { nodeNames?: string[]; includeOrphaned?: boolean; all?: boolean }) => {
      const params = new URLSearchParams();
      if (opts?.nodeNames) {
        opts.nodeNames.forEach((n) => params.append('node_name', n));
      }
      if (opts?.includeOrphaned) params.set('include_orphaned', 'true');
      if (opts?.all) params.set('all', 'true');

      // Use fetch directly for binary download (apiRequest parses JSON, not suitable for blobs)
      const token = localStorage.getItem('token');
      const response = await fetch(
        `/api/v1/labs/${labId}/config-snapshots/download?${params.toString()}`,
        {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        }
      );
      if (!response.ok) {
        const err = await response.json().catch(() => ({ detail: 'Download failed' }));
        throw new Error(err.detail || 'Download failed');
      }

      const blob = await response.blob();
      const filename =
        response.headers.get('Content-Disposition')?.split('filename=')[1] || 'configs.zip';
      downloadBlob(blob, filename);
    },
    [labId]
  );

  return {
    // State
    snapshots,
    selectedNodeName,
    selectedSnapshotIds,
    viewMode,
    loading,
    extracting,
    error,
    // Computed
    deviceNodes,
    nodeNames,
    orphanedNodeNames,
    orphanedSnapshots,
    orphanedByDeviceKind,
    nodeSnapshots,
    selectedSnapshot,
    comparisonSnapshots,
    activeNodeNames,
    nodeNamesWithSnapshots,
    // Helpers
    getDisplayName,
    // Actions
    setViewMode,
    setExtracting,
    selectNode,
    handleSnapshotClick,
    loadSnapshots,
    deleteSnapshot,
    bulkDeleteSnapshots,
    mapConfig,
    setActiveConfig,
    downloadConfigs,
  };
}
