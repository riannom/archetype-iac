import React, { useState } from 'react';
import ConfigDiffViewer from '../ConfigDiffViewer';
import type { ConfigSnapshot } from './types';

interface ConfigViewerProps {
  selectedSnapshot: ConfigSnapshot | null;
  comparisonSnapshots: [ConfigSnapshot, ConfigSnapshot] | null;
  viewMode: 'view' | 'compare';
  error: string | null;
  labId: string;
  studioRequest: <T>(path: string, options?: RequestInit) => Promise<T>;
}

const ConfigViewer: React.FC<ConfigViewerProps> = ({
  selectedSnapshot,
  comparisonSnapshots,
  viewMode,
  error,
  labId,
  studioRequest,
}) => {
  const [copyFeedback, setCopyFeedback] = useState(false);

  const handleCopy = async () => {
    if (!selectedSnapshot?.content) return;

    try {
      await navigator.clipboard.writeText(selectedSnapshot.content);
      setCopyFeedback(true);
      setTimeout(() => setCopyFeedback(false), 2000);
    } catch (err) {
      console.error('Failed to copy to clipboard:', err);
    }
  };

  // Error state
  if (error) {
    return (
      <div className="h-full flex items-center justify-center bg-stone-950">
        <div className="flex items-center gap-3 text-red-400">
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          <span>{error}</span>
        </div>
      </div>
    );
  }

  // Compare mode
  if (viewMode === 'compare') {
    if (!comparisonSnapshots || comparisonSnapshots.length !== 2) {
      return (
        <div className="h-full flex items-center justify-center bg-stone-950">
          <div className="text-stone-500 text-sm">
            Select 2 snapshots to compare
          </div>
        </div>
      );
    }

    return (
      <div className="h-full flex flex-col bg-stone-950">
        <div className="flex items-center justify-between px-4 py-3 border-b border-stone-800">
          <div className="text-sm text-stone-400">
            Comparing 2 snapshots
          </div>
        </div>
        <div className="flex-1 overflow-hidden">
          <ConfigDiffViewer
            snapshotA={comparisonSnapshots[0]}
            snapshotB={comparisonSnapshots[1]}
            studioRequest={studioRequest}
            labId={labId}
          />
        </div>
      </div>
    );
  }

  // View mode - empty state
  if (!selectedSnapshot) {
    return (
      <div className="h-full flex items-center justify-center bg-stone-950">
        <div className="text-stone-500 text-sm">
          Select a snapshot to view
        </div>
      </div>
    );
  }

  // View mode - with content
  return (
    <div className="h-full flex flex-col bg-stone-950">
      <div className="flex items-center justify-between px-4 py-3 border-b border-stone-800">
        <div className="text-sm text-stone-400">
          {new Date(selectedSnapshot.created_at).toLocaleString()}
        </div>
        <button
          onClick={handleCopy}
          className="flex items-center gap-2 px-3 py-1.5 text-sm text-stone-300 hover:text-white bg-stone-800 hover:bg-stone-700 rounded transition-colors"
        >
          {copyFeedback ? (
            <>
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
              Copied!
            </>
          ) : (
            <>
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
              </svg>
              Copy
            </>
          )}
        </button>
      </div>
      <div className="flex-1 overflow-auto">
        <pre className="p-4 text-sm font-mono text-sage-400 bg-stone-950 whitespace-pre-wrap break-words">
          {selectedSnapshot.content}
        </pre>
      </div>
    </div>
  );
};

export default ConfigViewer;
