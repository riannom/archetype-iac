import React, { useState, useEffect, useCallback } from 'react';
import ConfigRebootConfirmModal from './ConfigRebootConfirmModal';
import { RuntimeStatus } from './RuntimeControl';
import { NodeStateEntry } from '../../types/nodeState';

interface ConfigSnapshot {
  id: string;
  lab_id: string;
  node_name: string;
  content: string;
  content_hash: string;
  snapshot_type: string;
  device_kind: string | null;
  created_at: string;
  is_active: boolean;
  is_orphaned: boolean;
}

interface ConfigSnapshotSelectorProps {
  labId: string;
  nodeName: string;
  nodeId: string;
  studioRequest: <T>(path: string, options?: RequestInit) => Promise<T>;
  onOpenConfigViewer?: (nodeId: string, nodeName: string, snapshotContent?: string, snapshotLabel?: string) => void;
  onUpdateStatus: (nodeId: string, status: RuntimeStatus) => void;
  nodeState?: NodeStateEntry;
}

function relativeTime(dateStr: string): string {
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return 'just now';
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.floor(diffHr / 24);
  if (diffDay < 30) return `${diffDay}d ago`;
  return date.toLocaleDateString();
}

function typeBadgeClass(type: string): string {
  switch (type) {
    case 'manual': return 'bg-blue-500/10 text-blue-400 border-blue-500/20';
    case 'pre_stop': return 'bg-purple-500/10 text-purple-400 border-purple-500/20';
    default: return 'bg-amber-500/10 text-amber-400 border-amber-500/20';
  }
}

function typeLabel(type: string): string {
  switch (type) {
    case 'manual': return 'Manual';
    case 'pre_stop': return 'Pre-Stop';
    case 'auto_stop': return 'Auto';
    default: return type;
  }
}

const ConfigSnapshotSelector: React.FC<ConfigSnapshotSelectorProps> = ({
  labId,
  nodeName,
  nodeId,
  studioRequest,
  onOpenConfigViewer,
  onUpdateStatus,
  nodeState,
}) => {
  const [snapshots, setSnapshots] = useState<ConfigSnapshot[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pendingConfigChange, setPendingConfigChange] = useState(false);
  const [confirmModal, setConfirmModal] = useState<{
    open: boolean;
    snapshotId: string | null;
    description: string;
  }>({ open: false, snapshotId: null, description: '' });
  const [apiLoading, setApiLoading] = useState(false);
  const [selectedSnapshotId, setSelectedSnapshotId] = useState<string | null>(null);

  const activeSnapshot = snapshots.find(s => s.is_active);
  const selectedSnapshot = snapshots.find(s => s.id === selectedSnapshotId);

  const fetchSnapshots = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await studioRequest<{ snapshots: ConfigSnapshot[] }>(
        `/labs/${labId}/config-snapshots/${encodeURIComponent(nodeName)}/list`
      );
      setSnapshots(data.snapshots);
    } catch (err) {
      setError('Failed to load snapshots');
    } finally {
      setLoading(false);
    }
  }, [labId, nodeName, studioRequest]);

  useEffect(() => {
    fetchSnapshots();
  }, [fetchSnapshots]);

  // Clear pending badge when node starts booting
  useEffect(() => {
    if (nodeState?.actual_state === 'booting') {
      setPendingConfigChange(false);
    }
  }, [nodeState?.actual_state]);

  const handleSetActive = (snapshotId: string) => {
    const snap = snapshots.find(s => s.id === snapshotId);
    const desc = snap
      ? `Set "${typeLabel(snap.snapshot_type)}" snapshot from ${relativeTime(snap.created_at)} as the active startup configuration.`
      : 'Set this snapshot as the active startup configuration.';
    setConfirmModal({ open: true, snapshotId, description: desc });
  };

  const handleResetToDefault = () => {
    setConfirmModal({
      open: true,
      snapshotId: null,
      description: 'Clear the active startup configuration and reset to vendor default.',
    });
  };

  const handleConfirm = async (rebootNow: boolean) => {
    try {
      setApiLoading(true);
      await studioRequest(`/labs/${labId}/nodes/${encodeURIComponent(nodeName)}/active-config`, {
        method: 'PUT',
        body: JSON.stringify({ snapshot_id: confirmModal.snapshotId }),
      });
      await fetchSnapshots();
      if (rebootNow) {
        onUpdateStatus(nodeId, 'booting');
      } else {
        setPendingConfigChange(true);
      }
      setConfirmModal({ open: false, snapshotId: null, description: '' });
    } catch {
      setError('Failed to update active config');
    } finally {
      setApiLoading(false);
    }
  };

  const handleDelete = async (snapshotId: string) => {
    try {
      await studioRequest(`/labs/${labId}/config-snapshots/${snapshotId}`, {
        method: 'DELETE',
      });
      await fetchSnapshots();
    } catch {
      setError('Failed to delete snapshot');
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <i className="fa-solid fa-spinner fa-spin text-stone-400" />
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col gap-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <label className="text-[10px] font-bold text-stone-500 uppercase tracking-widest">
          Config Snapshots
        </label>
        <div className="flex items-center gap-1">
          {activeSnapshot && (
            <button
              onClick={handleResetToDefault}
              className="flex items-center gap-1 px-2 py-1 text-[9px] font-bold uppercase text-stone-500 dark:text-stone-400 hover:text-amber-500 dark:hover:text-amber-400 hover:bg-amber-500/10 rounded transition-colors"
              title="Reset to vendor default"
            >
              <i className="fa-solid fa-rotate-left" />
              Default
            </button>
          )}
          {onOpenConfigViewer && (
            <button
              onClick={() => {
                const snap = selectedSnapshot || activeSnapshot;
                if (snap) {
                  const label = `${typeLabel(snap.snapshot_type)} — ${relativeTime(snap.created_at)}`;
                  onOpenConfigViewer(nodeId, nodeName, snap.content, label);
                } else {
                  onOpenConfigViewer(nodeId, nodeName);
                }
              }}
              className="flex items-center gap-1.5 px-2 py-1 text-[9px] font-bold uppercase text-sage-600 dark:text-sage-400 hover:bg-sage-500/10 rounded transition-colors"
              title={selectedSnapshot ? `View selected snapshot` : 'View saved config in larger window'}
            >
              <i className="fa-solid fa-expand" />
              Expand
            </button>
          )}
        </div>
      </div>

      {/* Pending config change badge */}
      {pendingConfigChange && (
        <div className="flex items-center gap-2 px-3 py-2 bg-amber-500/10 border border-amber-500/20 rounded-lg">
          <i className="fa-solid fa-clock text-amber-500 text-[10px]" />
          <span className="text-[10px] font-bold text-amber-600 dark:text-amber-400">
            Config change pending — will apply on next boot
          </span>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="flex items-center gap-2 px-3 py-2 bg-red-500/10 border border-red-500/20 rounded-lg">
          <i className="fa-solid fa-circle-exclamation text-red-500 text-[10px]" />
          <span className="text-[10px] text-red-600 dark:text-red-400">{error}</span>
          <button onClick={() => setError(null)} className="ml-auto text-red-400 hover:text-red-300">
            <i className="fa-solid fa-xmark text-[10px]" />
          </button>
        </div>
      )}

      {/* Snapshot list */}
      {snapshots.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-12 text-stone-400 dark:text-stone-600">
          <i className="fa-solid fa-inbox text-2xl opacity-30 mb-2" />
          <p className="text-[10px] font-bold uppercase tracking-tight">No snapshots</p>
          <p className="text-[9px] mt-1">Extract configs to create snapshots</p>
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto space-y-1.5 custom-scrollbar">
          {snapshots.map(snap => (
            <div
              key={snap.id}
              onClick={() => setSelectedSnapshotId(prev => prev === snap.id ? null : snap.id)}
              className={`group relative p-2.5 rounded-lg border transition-all cursor-pointer ${
                snap.id === selectedSnapshotId
                  ? 'bg-sage-500/10 border-sage-500/40 dark:border-sage-500/30 ring-1 ring-sage-500/20'
                  : snap.is_active
                    ? 'bg-amber-500/5 border-amber-500/30 dark:border-amber-500/20'
                    : 'glass-control border-stone-200 dark:border-stone-700 hover:border-stone-300 dark:hover:border-stone-600'
              }`}
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2 min-w-0">
                  {snap.is_active && (
                    <i className="fa-solid fa-star text-amber-500 text-[9px] shrink-0" title="Active" />
                  )}
                  <span className="text-[10px] text-stone-600 dark:text-stone-400 truncate">
                    {relativeTime(snap.created_at)}
                  </span>
                  <span className={`text-[9px] px-1.5 py-0.5 rounded border shrink-0 ${typeBadgeClass(snap.snapshot_type)}`}>
                    {typeLabel(snap.snapshot_type)}
                  </span>
                </div>
                {/* Hover actions */}
                <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                  {!snap.is_active && (
                    <button
                      onClick={(e) => { e.stopPropagation(); handleSetActive(snap.id); }}
                      className="p-1 text-stone-400 hover:text-amber-500 transition-colors"
                      title="Set as active config"
                    >
                      <i className="fa-regular fa-star text-[10px]" />
                    </button>
                  )}
                  <button
                    onClick={(e) => { e.stopPropagation(); handleDelete(snap.id); }}
                    className="p-1 text-stone-400 hover:text-red-500 transition-colors"
                    title="Delete snapshot"
                  >
                    <i className="fa-solid fa-trash-can text-[10px]" />
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Config preview */}
      <div className="mt-auto">
        <div className="flex items-center justify-between mb-1.5">
          <span className="text-[9px] font-bold text-stone-500 uppercase tracking-widest">
            {activeSnapshot ? 'Active Config' : 'Startup Config'}
          </span>
          {activeSnapshot && (
            <span className="text-[8px] text-stone-400 dark:text-stone-600">
              {activeSnapshot.id.slice(0, 8)}
            </span>
          )}
        </div>
        <pre className="bg-stone-50 dark:bg-black text-sage-700 dark:text-sage-400 font-mono text-[10px] p-3 rounded-xl border border-stone-200 dark:border-stone-800 max-h-[200px] overflow-y-auto custom-scrollbar whitespace-pre-wrap break-all">
          {activeSnapshot
            ? activeSnapshot.content
            : <span className="text-stone-400 dark:text-stone-600 italic">Vendor default (no active config)</span>
          }
        </pre>
      </div>

      {/* Reboot confirmation modal */}
      <ConfigRebootConfirmModal
        isOpen={confirmModal.open}
        onClose={() => setConfirmModal({ open: false, snapshotId: null, description: '' })}
        onRebootNow={() => handleConfirm(true)}
        onApplyLater={() => handleConfirm(false)}
        loading={apiLoading}
        actionDescription={confirmModal.description}
      />
    </div>
  );
};

export default ConfigSnapshotSelector;
