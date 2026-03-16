import React from 'react';
import { LabLogEntry, LabLogJob } from '../../api';

interface LogEntryDetailProps {
  entry: LabLogEntry & { isRealtime?: boolean };
  job: LabLogJob | null | undefined;
  expandedJobLog: string | null;
  loadingJobLog: boolean;
  copiedEntryIdx: number | null;
  idx: number;
  levelColors: Record<string, string>;
  onFilterToJob: (jobId: string) => void;
  onCopyEntry: (entry: LabLogEntry, idx: number) => void;
  onClose: () => void;
}

const LogEntryDetail: React.FC<LogEntryDetailProps> = ({
  entry,
  job,
  expandedJobLog,
  loadingJobLog,
  copiedEntryIdx,
  idx,
  levelColors,
  onFilterToJob,
  onCopyEntry,
  onClose,
}) => {
  const isRealtime = (entry as any).isRealtime;

  return (
    <div className="px-4 py-3 bg-stone-100 dark:bg-stone-800/70 border-l-2 border-l-stone-300 dark:border-l-stone-600 ml-0 relative">
      {/* Close button */}
      <button
        onClick={(e) => {
          e.stopPropagation();
          onClose();
        }}
        className="absolute top-2 right-2 p-1.5 text-stone-400 hover:text-stone-600 dark:hover:text-stone-300 hover:bg-stone-200 dark:hover:bg-stone-700 rounded transition-all"
        title="Collapse"
      >
        <i className="fa-solid fa-times" />
      </button>
      <div className="flex flex-col gap-2 pr-8">
        {/* Full message */}
        <div>
          <span className="text-[11px] font-bold text-stone-500 uppercase">Message</span>
          <p className="text-stone-700 dark:text-stone-300 whitespace-pre-wrap mt-1">{entry.message}</p>
        </div>
        {/* Metadata grid */}
        <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-[11px]">
          <div>
            <span className="text-stone-500">Timestamp:</span>{' '}
            <span className="text-stone-600 dark:text-stone-400">{new Date(entry.timestamp).toLocaleString()}</span>
          </div>
          <div>
            <span className="text-stone-500">Level:</span>{' '}
            <span className={`font-bold uppercase ${levelColors[entry.level] || 'text-stone-500'}`}>{entry.level}</span>
          </div>
          {entry.host_name && (
            <div>
              <span className="text-stone-500">Host:</span>{' '}
              <span className="text-stone-600 dark:text-stone-400">{entry.host_name}</span>
            </div>
          )}
          <div>
            <span className="text-stone-500">Source:</span>{' '}
            <span className="text-stone-600 dark:text-stone-400">{entry.source || (isRealtime ? 'realtime' : 'job')}</span>
          </div>
        </div>
        {/* Job details */}
        {job && (
          <div className="mt-2 p-2 bg-stone-200/50 dark:bg-stone-700/50 rounded">
            <span className="text-[11px] font-bold text-stone-500 uppercase">Job Details</span>
            <div className="mt-1 grid grid-cols-2 gap-x-4 gap-y-1 text-[11px]">
              <div>
                <span className="text-stone-500">Action:</span>{' '}
                <span className="text-stone-700 dark:text-stone-300">{formatJobAction(job.action)}</span>
              </div>
              <div>
                <span className="text-stone-500">Status:</span>{' '}
                <span className={job.status === 'failed' ? 'text-red-600 dark:text-red-400 font-bold' : job.status === 'completed' ? 'text-green-600 dark:text-green-400' : 'text-stone-700 dark:text-stone-300'}>
                  {job.status}
                </span>
              </div>
              <div>
                <span className="text-stone-500">ID:</span>{' '}
                <span className="text-stone-600 dark:text-stone-400 font-mono">{job.id.slice(0, 8)}...</span>
              </div>
              <div>
                <span className="text-stone-500">Created:</span>{' '}
                <span className="text-stone-600 dark:text-stone-400">{new Date(job.created_at).toLocaleTimeString()}</span>
              </div>
            </div>
            {/* Filter to this job button */}
            <button
              onClick={(e) => {
                e.stopPropagation();
                onFilterToJob(job.id);
              }}
              className="mt-2 px-2 py-1 text-[11px] bg-sage-600 hover:bg-sage-500 text-white rounded transition-all"
            >
              <i className="fa-solid fa-filter mr-1" />
              Filter to this job
            </button>

            {/* Full job log */}
            <div className="mt-3 pt-3 border-t border-stone-300 dark:border-stone-600">
              <span className="text-[11px] font-bold text-stone-500 uppercase">Full Job Log</span>
              {loadingJobLog ? (
                <div className="mt-2 text-stone-500 dark:text-stone-400">
                  <i className="fa-solid fa-spinner fa-spin mr-2" />
                  Loading job log...
                </div>
              ) : expandedJobLog ? (
                <pre className="mt-2 p-2 bg-stone-900 dark:bg-black text-stone-100 text-[11px] rounded overflow-x-auto whitespace-pre-wrap max-h-60 overflow-y-auto custom-scrollbar">
                  {expandedJobLog}
                </pre>
              ) : null}
            </div>
          </div>
        )}
        {/* Copy button */}
        <button
          onClick={(e) => {
            e.stopPropagation();
            onCopyEntry(entry, idx);
          }}
          className={`self-start px-2 py-1 text-[11px] rounded transition-all ${
            copiedEntryIdx === idx
              ? 'bg-green-200 dark:bg-green-800 text-green-700 dark:text-green-300'
              : 'bg-stone-200 dark:bg-stone-700 hover:bg-stone-300 dark:hover:bg-stone-600 text-stone-700 dark:text-stone-300'
          }`}
        >
          <i className={`fa-solid ${copiedEntryIdx === idx ? 'fa-check' : 'fa-copy'} mr-1`} />
          {copiedEntryIdx === idx ? 'Copied!' : 'Copy entry'}
        </button>
      </div>
    </div>
  );
};

// Format job action for display (moved here since it's used only in the detail view)
function formatJobAction(action: string): string {
  if (action.startsWith('sync:')) {
    return action.replace('sync:', 'Sync ');
  }
  if (action.startsWith('node:')) {
    const parts = action.split(':');
    return `Node ${parts[1]} (${parts[2] || ''})`;
  }
  return action.toUpperCase();
}

export default LogEntryDetail;
