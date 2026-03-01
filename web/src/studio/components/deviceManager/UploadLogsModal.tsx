import React from 'react';
import { Modal } from '../../../components/ui/Modal';
import {
  ImageManagementLogEntry,
  ImageManagementLogFilter,
  IMAGE_LOG_LEVEL_COLORS,
  IMAGE_LOG_CATEGORY_COLORS,
} from './deviceManagerTypes';
import { formatImageLogTime, formatImageLogDate } from './deviceManagerUtils';

interface UploadLogsModalProps {
  isOpen: boolean;
  onClose: () => void;
  imageManagementLogs: ImageManagementLogEntry[];
  filteredImageManagementLogs: ImageManagementLogEntry[];
  imageLogFilter: ImageManagementLogFilter;
  setImageLogFilter: (filter: ImageManagementLogFilter) => void;
  imageLogSearch: string;
  setImageLogSearch: (search: string) => void;
  imageLogCounts: {
    all: number;
    errors: number;
    iso: number;
    docker: number;
    qcow2: number;
  };
  uploadErrorCount: number;
  copiedUploadLogId: string | null;
  clearImageManagementLogs: () => void;
  copyUploadLogEntry: (entry: ImageManagementLogEntry) => void;
}

const UploadLogsModal: React.FC<UploadLogsModalProps> = ({
  isOpen,
  onClose,
  imageManagementLogs,
  filteredImageManagementLogs,
  imageLogFilter,
  setImageLogFilter,
  imageLogSearch,
  setImageLogSearch,
  imageLogCounts,
  uploadErrorCount,
  copiedUploadLogId,
  clearImageManagementLogs,
  copyUploadLogEntry,
}) => {
  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Image Upload Logs"
      size="xl"
    >
      <div className="flex flex-col h-[70vh]">
        <div className="flex flex-wrap items-center gap-3 pb-4 border-b border-stone-200 dark:border-stone-700">
          <div className="flex items-center gap-2">
            <label className="text-xs font-medium text-stone-500 dark:text-stone-400">Filter:</label>
            <select
              aria-label="Image log filter"
              value={imageLogFilter}
              onChange={(e) => setImageLogFilter(e.target.value as ImageManagementLogFilter)}
              className="px-2 py-1 text-sm bg-stone-100 dark:bg-stone-800 border border-stone-300 dark:border-stone-600 rounded-md text-stone-700 dark:text-stone-200"
            >
              <option value="all">All ({imageLogCounts.all})</option>
              <option value="errors">Errors ({imageLogCounts.errors})</option>
              <option value="iso">ISO ({imageLogCounts.iso})</option>
              <option value="docker">Docker ({imageLogCounts.docker})</option>
              <option value="qcow2">QCOW2 ({imageLogCounts.qcow2})</option>
            </select>
          </div>

          <div className="flex-1 min-w-[220px]">
            <input
              aria-label="Search image logs"
              type="text"
              value={imageLogSearch}
              onChange={(e) => setImageLogSearch(e.target.value)}
              placeholder="Search logs..."
              className="w-full px-3 py-1 text-sm bg-stone-100 dark:bg-stone-800 border border-stone-300 dark:border-stone-600 rounded-md text-stone-700 dark:text-stone-200 placeholder-stone-400"
            />
          </div>

          <button
            onClick={clearImageManagementLogs}
            disabled={imageManagementLogs.length === 0}
            className="px-3 py-1.5 rounded-md text-xs font-semibold bg-stone-100 dark:bg-stone-800 border border-stone-300 dark:border-stone-600 text-stone-700 dark:text-stone-300 disabled:opacity-50 transition-colors"
          >
            Clear History
          </button>
        </div>

        <div className="flex items-center justify-between pt-3 text-xs text-stone-500 dark:text-stone-400">
          <span>
            Showing {filteredImageManagementLogs.length} of {imageManagementLogs.length} entries
          </span>
          {uploadErrorCount > 0 && (
            <span className="text-red-600 dark:text-red-400 font-semibold">
              {uploadErrorCount} errors
            </span>
          )}
        </div>

        <div className="flex-1 overflow-auto mt-3">
          {imageManagementLogs.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-stone-400 dark:text-stone-500">
              <i className="fa-solid fa-file-lines text-4xl mb-3 opacity-30"></i>
              <p className="text-sm">No logs found</p>
              <p className="text-xs mt-1">No upload or processing events recorded yet.</p>
            </div>
          ) : filteredImageManagementLogs.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-stone-400 dark:text-stone-500">
              <i className="fa-solid fa-filter-circle-xmark text-3xl mb-3 opacity-40"></i>
              <p className="text-sm">No matching logs</p>
              <p className="text-xs mt-1">No log entries match the current filter.</p>
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-stone-100 dark:bg-stone-800 z-10">
                <tr>
                  <th className="px-3 py-2 text-left text-xs font-medium text-stone-500 dark:text-stone-400 uppercase tracking-wider w-20">Time</th>
                  <th className="px-3 py-2 text-left text-xs font-medium text-stone-500 dark:text-stone-400 uppercase tracking-wider w-20">Level</th>
                  <th className="px-3 py-2 text-left text-xs font-medium text-stone-500 dark:text-stone-400 uppercase tracking-wider w-24">Category</th>
                  <th className="px-3 py-2 text-left text-xs font-medium text-stone-500 dark:text-stone-400 uppercase tracking-wider w-32">Phase</th>
                  <th className="px-3 py-2 text-left text-xs font-medium text-stone-500 dark:text-stone-400 uppercase tracking-wider">Message</th>
                  <th className="px-3 py-2 text-left text-xs font-medium text-stone-500 dark:text-stone-400 uppercase tracking-wider w-24">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-stone-200 dark:divide-stone-700">
                {filteredImageManagementLogs.map((entry) => (
                  <tr
                    key={entry.id}
                    className="hover:bg-stone-50 dark:hover:bg-stone-800/50 align-top"
                  >
                    <td className="px-3 py-2 text-stone-500 dark:text-stone-400 whitespace-nowrap font-mono text-xs">
                      <div>{formatImageLogTime(entry.timestamp)}</div>
                      <div className="text-[10px] text-stone-400 dark:text-stone-500">{formatImageLogDate(entry.timestamp)}</div>
                    </td>
                    <td className="px-3 py-2">
                      <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium uppercase ${IMAGE_LOG_LEVEL_COLORS[entry.level]}`}>
                        {entry.level}
                      </span>
                    </td>
                    <td className="px-3 py-2">
                      <span className={`inline-block px-2 py-0.5 rounded text-[10px] font-semibold uppercase ${IMAGE_LOG_CATEGORY_COLORS[entry.category] || 'text-stone-700 dark:text-stone-300 bg-stone-200 dark:bg-stone-700'}`}>
                        {entry.category}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-[11px] text-stone-600 dark:text-stone-300 font-mono">
                      {entry.phase}
                    </td>
                    <td className="px-3 py-2 text-stone-700 dark:text-stone-200 font-mono text-xs">
                      <div>{entry.message}</div>
                      {entry.filename && (
                        <div className="text-[10px] text-stone-500 dark:text-stone-400 mt-1">
                          file: {entry.filename}
                        </div>
                      )}
                      {entry.details && (
                        <pre className="mt-1 p-2 bg-stone-100 dark:bg-stone-900 rounded text-[10px] text-stone-600 dark:text-stone-300 whitespace-pre-wrap break-words max-h-20 overflow-auto custom-scrollbar">
                          {entry.details}
                        </pre>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      <button
                        onClick={() => copyUploadLogEntry(entry)}
                        className="px-2 py-1 rounded text-[10px] font-bold glass-control text-stone-700 dark:text-stone-300 transition-colors whitespace-nowrap"
                      >
                        <i className={`fa-solid ${copiedUploadLogId === entry.id ? 'fa-check' : 'fa-copy'} mr-1`} />
                        {copiedUploadLogId === entry.id ? 'Copied' : 'Copy'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </Modal>
  );
};

export default UploadLogsModal;
