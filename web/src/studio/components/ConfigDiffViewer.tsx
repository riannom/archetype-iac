import React, { useEffect, useState } from 'react';

interface ConfigSnapshot {
  id: string;
  lab_id: string;
  node_name: string;
  content: string;
  content_hash: string;
  snapshot_type: string;
  created_at: string;
}

interface DiffLine {
  line_number_a: number | null;
  line_number_b: number | null;
  content: string;
  type: 'unchanged' | 'added' | 'removed' | 'header';
}

interface DiffResponse {
  snapshot_a: ConfigSnapshot;
  snapshot_b: ConfigSnapshot;
  diff_lines: DiffLine[];
  additions: number;
  deletions: number;
}

interface ConfigDiffViewerProps {
  snapshotA: ConfigSnapshot;
  snapshotB: ConfigSnapshot;
  studioRequest: <T>(path: string, options?: RequestInit) => Promise<T>;
  labId: string;
}

const ConfigDiffViewer: React.FC<ConfigDiffViewerProps> = ({
  snapshotA,
  snapshotB,
  studioRequest,
  labId,
}) => {
  const [diff, setDiff] = useState<DiffResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const loadDiff = async () => {
      setLoading(true);
      setError(null);
      try {
        const data = await studioRequest<DiffResponse>(`/labs/${labId}/config-diff`, {
          method: 'POST',
          body: JSON.stringify({
            snapshot_id_a: snapshotA.id,
            snapshot_id_b: snapshotB.id,
          }),
        });
        setDiff(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load diff');
      } finally {
        setLoading(false);
      }
    };

    loadDiff();
  }, [snapshotA.id, snapshotB.id, studioRequest, labId]);

  const formatTimestamp = (timestamp: string) => {
    return new Date(timestamp).toLocaleString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <i className="fa-solid fa-spinner fa-spin text-2xl text-stone-400" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-8 text-center">
        <i className="fa-solid fa-exclamation-circle text-2xl text-red-500 mb-2" />
        <p className="text-sm text-stone-400">{error}</p>
      </div>
    );
  }

  if (!diff) {
    return null;
  }

  const hasChanges = diff.additions > 0 || diff.deletions > 0;

  return (
    <div className="flex flex-col h-full">
      {/* Diff header */}
      <div className="px-4 py-3 glass-control border-b border-stone-800 flex items-center justify-between">
        <div className="flex items-center gap-6">
          <div className="flex items-center gap-2">
            <div className="w-3 h-3 rounded bg-red-500/30 border border-red-500" />
            <span className="text-xs text-stone-400">
              <span className="font-mono text-red-400">-{diff.deletions}</span> removed
            </span>
          </div>
          <div className="flex items-center gap-2">
            <div className="w-3 h-3 rounded bg-emerald-500/30 border border-emerald-500" />
            <span className="text-xs text-stone-400">
              <span className="font-mono text-emerald-400">+{diff.additions}</span> added
            </span>
          </div>
        </div>
        {!hasChanges && (
          <span className="text-xs text-stone-500 italic">No differences</span>
        )}
      </div>

      {/* Version labels */}
      <div className="grid grid-cols-2 text-[10px] font-medium border-b border-stone-800">
        <div className="px-4 py-2 bg-red-500/10 text-red-400 border-r border-stone-800">
          <i className="fa-solid fa-clock mr-1" />
          {formatTimestamp(snapshotA.created_at)}
          <span className="ml-2 text-stone-500">({snapshotA.snapshot_type})</span>
        </div>
        <div className="px-4 py-2 bg-emerald-500/10 text-emerald-400">
          <i className="fa-solid fa-clock mr-1" />
          {formatTimestamp(snapshotB.created_at)}
          <span className="ml-2 text-stone-500">({snapshotB.snapshot_type})</span>
        </div>
      </div>

      {/* Diff content */}
      <div className="flex-1 overflow-auto">
        <table className="w-full text-xs font-mono">
          <tbody>
            {diff.diff_lines.map((line, index) => {
              if (line.type === 'header') {
                return (
                  <tr key={index} className="bg-blue-500/10">
                    <td colSpan={4} className="px-4 py-1 text-blue-400 font-bold">
                      {line.content}
                    </td>
                  </tr>
                );
              }

              const bgColor =
                line.type === 'added'
                  ? 'bg-emerald-500/10'
                  : line.type === 'removed'
                  ? 'bg-red-500/10'
                  : '';

              const textColor =
                line.type === 'added'
                  ? 'text-emerald-400'
                  : line.type === 'removed'
                  ? 'text-red-400'
                  : 'text-stone-400';

              const lineNumColor =
                line.type === 'added'
                  ? 'text-emerald-600'
                  : line.type === 'removed'
                  ? 'text-red-600'
                  : 'text-stone-600';

              const prefixChar =
                line.type === 'added' ? '+' : line.type === 'removed' ? '-' : ' ';

              return (
                <tr key={index} className={`${bgColor} hover:bg-white/5`}>
                  <td
                    className={`w-12 px-2 py-0.5 text-right select-none border-r border-stone-800 ${lineNumColor}`}
                  >
                    {line.line_number_a ?? ''}
                  </td>
                  <td
                    className={`w-12 px-2 py-0.5 text-right select-none border-r border-stone-800 ${lineNumColor}`}
                  >
                    {line.line_number_b ?? ''}
                  </td>
                  <td className={`w-6 px-1 py-0.5 text-center select-none ${textColor}`}>
                    {prefixChar}
                  </td>
                  <td className={`px-2 py-0.5 whitespace-pre ${textColor}`}>
                    {line.content}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>

        {diff.diff_lines.length === 0 && (
          <div className="flex items-center justify-center py-12">
            <div className="text-center">
              <i className="fa-solid fa-equals text-3xl text-stone-700 mb-3" />
              <p className="text-sm text-stone-500">Configurations are identical</p>
              <p className="text-[10px] text-stone-600 mt-1">
                Hash: {snapshotA.content_hash.slice(0, 12)}...
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default ConfigDiffViewer;
