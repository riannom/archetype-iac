import React from 'react';
import { Modal } from '../../../components/ui/Modal';
import { IolBuildRow } from './deviceManagerTypes';
import { formatBuildTimestamp } from './deviceManagerUtils';
import type { useIolBuildManager } from './useIolBuildManager';

type IolBuildManagerReturn = ReturnType<typeof useIolBuildManager>;

interface BuildJobsViewProps {
  uploadStatus: string | null;
  iolBuildRows: IolBuildRow[];
  hasActiveIolBuilds: boolean;
  activeIolBuildCount: number;
  currentIolBuildRows: IolBuildRow[];
  historicalIolBuildRows: IolBuildRow[];
  refreshingIolBuilds: boolean;
  retryingIolImageId: string | null;
  ignoringIolImageId: string | null;
  autoRefreshIolBuilds: boolean;
  setAutoRefreshIolBuilds: (value: boolean) => void;
  refreshIolBuildStatuses: IolBuildManagerReturn['refreshIolBuildStatuses'];
  retryIolBuild: IolBuildManagerReturn['retryIolBuild'];
  ignoreIolBuildFailure: IolBuildManagerReturn['ignoreIolBuildFailure'];
  openIolDiagnostics: IolBuildManagerReturn['openIolDiagnostics'];
  showIolDiagnostics: boolean;
  setShowIolDiagnostics: (show: boolean) => void;
  iolDiagnostics: IolBuildManagerReturn['iolDiagnostics'];
  iolDiagnosticsLoading: boolean;
  iolDiagnosticsError: string | null;
}

const BuildJobsView: React.FC<BuildJobsViewProps> = ({
  uploadStatus,
  iolBuildRows,
  hasActiveIolBuilds,
  activeIolBuildCount,
  currentIolBuildRows,
  historicalIolBuildRows,
  refreshingIolBuilds,
  retryingIolImageId,
  ignoringIolImageId,
  autoRefreshIolBuilds,
  setAutoRefreshIolBuilds,
  refreshIolBuildStatuses,
  retryIolBuild,
  ignoreIolBuildFailure,
  openIolDiagnostics,
  showIolDiagnostics,
  setShowIolDiagnostics,
  iolDiagnostics,
  iolDiagnosticsLoading,
  iolDiagnosticsError,
}) => {
  return (
    <div className="h-full overflow-auto p-6">
      <div className="max-w-5xl mx-auto space-y-4">
        <div>
          <h2 className="text-lg font-bold text-stone-900 dark:text-white">Build Jobs</h2>
          <p className="text-xs text-stone-500 dark:text-stone-400 mt-1">
            Track and manage background IOL Docker image builds
          </p>
          {hasActiveIolBuilds && (
            <div className="mt-2 inline-flex items-center gap-2 rounded-full border border-blue-200 dark:border-blue-800 bg-blue-50 dark:bg-blue-900/20 px-3 py-1 text-[11px] font-semibold text-blue-700 dark:text-blue-300">
              <i className="fa-solid fa-circle-notch fa-spin" />
              {activeIolBuildCount} build{activeIolBuildCount === 1 ? '' : 's'} in progress
            </div>
          )}
        </div>

        {uploadStatus && (
          <p className="text-xs text-stone-500 dark:text-stone-400">{uploadStatus}</p>
        )}

        {iolBuildRows.length === 0 ? (
          <div className="rounded-xl border border-dashed border-stone-300 dark:border-stone-700 bg-white/50 dark:bg-stone-900/40 p-8 text-center">
            <i className="fa-solid fa-compact-disc text-3xl text-stone-300 dark:text-stone-600 mb-3" />
            <h3 className="text-sm font-bold text-stone-600 dark:text-stone-300">No IOL Build Jobs</h3>
            <p className="text-xs text-stone-500 dark:text-stone-400 mt-1">
              Import an ISO or upload an IOL binary in Image Management to create build jobs.
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="rounded-lg border border-stone-200 dark:border-stone-800 bg-stone-50/70 dark:bg-stone-900/40">
              <div className="px-3 py-2 border-b border-stone-200 dark:border-stone-800 flex items-center justify-between">
                <div>
                  <h3 className="text-[11px] font-bold text-stone-700 dark:text-stone-300 uppercase tracking-wide">
                    Current Jobs
                  </h3>
                  <p className="text-[11px] text-stone-500 dark:text-stone-400 mt-0.5">
                    {hasActiveIolBuilds ? 'Live updates active' : 'No active builds'}
                  </p>
                </div>
                <div className="flex items-center gap-3">
                  <label className="flex items-center gap-1 text-[11px] text-stone-500 dark:text-stone-400">
                    <input
                      type="checkbox"
                      checked={autoRefreshIolBuilds}
                      onChange={(e) => setAutoRefreshIolBuilds(e.target.checked)}
                      className="w-3 h-3 rounded border-stone-300 dark:border-stone-600 text-sage-600 focus:ring-sage-500"
                    />
                    Auto
                  </label>
                  <button
                    onClick={refreshIolBuildStatuses}
                    disabled={refreshingIolBuilds}
                    className="text-[11px] font-bold text-sage-600 hover:text-sage-500 disabled:text-stone-400 transition-colors"
                  >
                    <i className={`fa-solid fa-rotate mr-1 ${refreshingIolBuilds ? 'fa-spin' : ''}`} />
                    Refresh
                  </button>
                </div>
              </div>
              <div className="p-3 space-y-2 max-h-[50vh] overflow-y-auto custom-scrollbar">
                {currentIolBuildRows.length === 0 ? (
                  <div className="rounded-md border border-dashed border-stone-300 dark:border-stone-700 bg-white/60 dark:bg-stone-900/30 px-3 py-2 text-xs text-stone-500 dark:text-stone-400">
                    No pending or failed jobs. Completed builds are listed in History below.
                  </div>
                ) : (
                  currentIolBuildRows.map((row) => {
                    const statusTone =
                      row.status === 'failed'
                        ? 'text-red-600 dark:text-red-400'
                        : row.status === 'ignored'
                        ? 'text-stone-500 dark:text-stone-300'
                        : row.status === 'building' || row.status === 'queued'
                        ? 'text-blue-600 dark:text-blue-400'
                        : 'text-amber-600 dark:text-amber-400';
                    const statusLabel =
                      row.status === 'failed'
                        ? 'Failed'
                        : row.status === 'ignored'
                        ? 'Ignored'
                        : row.status === 'building'
                        ? 'Building'
                        : row.status === 'queued'
                        ? 'Queued'
                        : 'Not Started';
                    const isRetrying = retryingIolImageId === row.image.id;
                    const isIgnoring = ignoringIolImageId === row.image.id;

                    return (
                      <div
                        key={row.image.id}
                        className="rounded-md border border-stone-200 dark:border-stone-800 bg-white/70 dark:bg-stone-800/30 p-2.5"
                      >
                        <div className="flex items-start justify-between gap-2">
                          <div className="min-w-0">
                            <div className="text-xs font-semibold text-stone-800 dark:text-stone-200 truncate">
                              {row.image.filename || row.image.reference}
                            </div>
                            <div className={`text-[11px] font-bold uppercase ${statusTone}`}>
                              {row.status === 'building' && <i className="fa-solid fa-spinner fa-spin mr-1" />}
                              {statusLabel}
                            </div>
                            {row.dockerReference && (
                              <div className="text-[11px] text-stone-500 dark:text-stone-400 mt-0.5 truncate">
                                Docker: {row.dockerReference}
                              </div>
                            )}
                            {row.buildJobId && (
                              <div className="text-[11px] text-stone-400 dark:text-stone-500 truncate">
                                Job: {row.buildJobId}
                              </div>
                            )}
                            {row.status === 'ignored' && (
                              <div className="text-[11px] text-stone-400 dark:text-stone-500 mt-0.5 truncate">
                                Ignored by {row.buildIgnoredBy || 'user'} at {formatBuildTimestamp(row.buildIgnoredAt)}
                              </div>
                            )}
                            {row.buildError && (
                              <div className="text-[11px] text-red-500 mt-1 whitespace-pre-wrap break-words">
                                {row.buildError}
                              </div>
                            )}
                          </div>
                          <div className="flex flex-wrap items-center justify-end gap-1.5 shrink-0 max-w-[320px]">
                            <button
                              onClick={() => openIolDiagnostics(row.image.id)}
                              className="px-2 py-1 rounded text-[11px] font-bold glass-control text-stone-700 dark:text-stone-300 transition-colors"
                            >
                              Details
                            </button>
                            <button
                              onClick={() => retryIolBuild(row.image.id, false)}
                              disabled={isRetrying || isIgnoring || row.status === 'queued' || row.status === 'building'}
                              className="px-2 py-1 rounded text-[11px] font-bold bg-sage-600 hover:bg-sage-500 disabled:bg-stone-300 dark:disabled:bg-stone-700 text-white transition-colors"
                            >
                              {isRetrying ? 'Retrying...' : 'Retry'}
                            </button>
                            <button
                              onClick={() => retryIolBuild(row.image.id, true)}
                              disabled={isRetrying || isIgnoring || row.status === 'queued' || row.status === 'building'}
                              className="px-2 py-1 rounded text-[11px] font-bold glass-control text-stone-700 dark:text-stone-300 disabled:text-stone-400 transition-colors"
                            >
                              Force
                            </button>
                            <button
                              onClick={() => ignoreIolBuildFailure(row.image.id)}
                              disabled={isRetrying || isIgnoring || row.status !== 'failed'}
                              className="px-2 py-1 rounded text-[11px] font-bold glass-control text-stone-700 dark:text-stone-300 disabled:text-stone-400 transition-colors"
                            >
                              {isIgnoring ? 'Ignoring...' : 'Ignore'}
                            </button>
                          </div>
                        </div>
                      </div>
                    );
                  })
                )}
              </div>
            </div>

            {historicalIolBuildRows.length > 0 && (
              <div className="rounded-lg border border-stone-200 dark:border-stone-800 bg-stone-50/50 dark:bg-stone-900/25">
                <div className="px-3 py-2 border-b border-stone-200 dark:border-stone-800 flex items-center justify-between">
                  <h3 className="text-[11px] font-bold text-stone-700 dark:text-stone-300 uppercase tracking-wide">
                    Build History
                  </h3>
                  <span className="text-[11px] text-stone-500 dark:text-stone-400">
                    {historicalIolBuildRows.length} completed
                  </span>
                </div>
                <div className="p-3 space-y-2 max-h-[50vh] overflow-y-auto custom-scrollbar">
                  {historicalIolBuildRows.map((row) => (
                    <div
                      key={`history-${row.image.id}`}
                      className="rounded-md border border-stone-200 dark:border-stone-800 bg-white/70 dark:bg-stone-800/30 p-2.5"
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0">
                          <div className="text-xs font-semibold text-stone-800 dark:text-stone-200 truncate">
                            {row.image.filename || row.image.reference}
                          </div>
                          <div className="text-[11px] font-bold uppercase text-emerald-600 dark:text-emerald-400">
                            Ready
                          </div>
                          {row.dockerReference && (
                            <div className="text-[11px] text-stone-500 dark:text-stone-400 mt-0.5 truncate">
                              Docker: {row.dockerReference}
                            </div>
                          )}
                          {row.buildJobId && (
                            <div className="text-[11px] text-stone-400 dark:text-stone-500 truncate">
                              Job: {row.buildJobId}
                            </div>
                          )}
                        </div>
                        <span className="text-[11px] text-stone-400 dark:text-stone-500 whitespace-nowrap">
                          Completed
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      <Modal
        isOpen={showIolDiagnostics}
        onClose={() => setShowIolDiagnostics(false)}
        title="IOL Build Diagnostics"
        size="lg"
      >
        {iolDiagnosticsLoading && (
          <div className="py-8 text-center">
            <i className="fa-solid fa-spinner fa-spin text-xl text-stone-400" />
          </div>
        )}

        {!iolDiagnosticsLoading && iolDiagnosticsError && (
          <div className="p-3 rounded bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300 text-sm">
            {iolDiagnosticsError}
          </div>
        )}

        {!iolDiagnosticsLoading && !iolDiagnosticsError && iolDiagnostics && (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3 text-xs">
              <div className="text-stone-500 dark:text-stone-400">
                File
                <div className="font-semibold text-stone-800 dark:text-stone-100 break-all">
                  {iolDiagnostics.filename || iolDiagnostics.reference || iolDiagnostics.image_id}
                </div>
              </div>
              <div className="text-stone-500 dark:text-stone-400">
                Status
                <div className="font-semibold text-stone-800 dark:text-stone-100 uppercase">
                  {iolDiagnostics.status || 'unknown'}
                </div>
              </div>
              <div className="text-stone-500 dark:text-stone-400">
                Job ID
                <div className="font-mono text-stone-700 dark:text-stone-200 break-all">
                  {iolDiagnostics.queue_job?.id || iolDiagnostics.build_job_id || '-'}
                </div>
              </div>
              <div className="text-stone-500 dark:text-stone-400">
                Queue Status
                <div className="font-semibold text-stone-800 dark:text-stone-100">
                  {iolDiagnostics.queue_job?.status || iolDiagnostics.rq_status || '-'}
                </div>
              </div>
              <div className="text-stone-500 dark:text-stone-400">
                Started
                <div className="text-stone-700 dark:text-stone-200">
                  {formatBuildTimestamp(iolDiagnostics.queue_job?.started_at)}
                </div>
              </div>
              <div className="text-stone-500 dark:text-stone-400">
                Ended
                <div className="text-stone-700 dark:text-stone-200">
                  {formatBuildTimestamp(iolDiagnostics.queue_job?.ended_at)}
                </div>
              </div>
            </div>

            {iolDiagnostics.recommended_action && (
              <div className="p-3 rounded border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-900/20 text-amber-800 dark:text-amber-200 text-xs">
                {iolDiagnostics.recommended_action}
              </div>
            )}

            {iolDiagnostics.build_error && (
              <div>
                <div className="text-xs font-semibold text-stone-600 dark:text-stone-300 mb-1">Build Error</div>
                <pre className="p-3 rounded bg-stone-900 text-stone-200 text-[11px] whitespace-pre-wrap break-words max-h-40 overflow-auto">
                  {iolDiagnostics.build_error}
                </pre>
              </div>
            )}

            {iolDiagnostics.queue_job?.error_log && (
              <div>
                <div className="text-xs font-semibold text-stone-600 dark:text-stone-300 mb-1">Worker Traceback</div>
                <pre className="p-3 rounded bg-stone-950 text-stone-200 text-[11px] whitespace-pre-wrap break-words max-h-56 overflow-auto">
                  {iolDiagnostics.queue_job.error_log}
                </pre>
              </div>
            )}
          </div>
        )}
      </Modal>
    </div>
  );
};

export default BuildJobsView;
