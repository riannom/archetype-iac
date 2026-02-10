import React, { useState, useEffect, useCallback } from 'react';
import { Modal } from '../../components/ui/Modal';
import { getSystemLogs, LogEntry, LogQueryParams } from '../../api';
import { usePolling } from '../hooks/usePolling';

interface SystemLogsModalProps {
  isOpen: boolean;
  onClose: () => void;
}

const LOG_LEVELS = ['All', 'INFO', 'WARNING', 'ERROR'] as const;
const SERVICES = ['All', 'api', 'worker', 'agent'] as const;
const TIME_RANGES = [
  { value: '15m', label: 'Last 15 min' },
  { value: '1h', label: 'Last hour' },
  { value: '24h', label: 'Last 24h' },
] as const;

const levelColors: Record<string, string> = {
  INFO: 'text-green-600 dark:text-green-400 bg-green-100 dark:bg-green-900/30',
  WARNING: 'text-amber-600 dark:text-amber-400 bg-amber-100 dark:bg-amber-900/30',
  WARN: 'text-amber-600 dark:text-amber-400 bg-amber-100 dark:bg-amber-900/30',
  ERROR: 'text-red-600 dark:text-red-400 bg-red-100 dark:bg-red-900/30',
  DEBUG: 'text-stone-500 dark:text-stone-400 bg-stone-100 dark:bg-stone-800',
};

const SystemLogsModal: React.FC<SystemLogsModalProps> = ({ isOpen, onClose }) => {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [totalCount, setTotalCount] = useState(0);

  // Filter state
  const [service, setService] = useState<string>('All');
  const [level, setLevel] = useState<string>('All');
  const [timeRange, setTimeRange] = useState<string>('1h');
  const [search, setSearch] = useState<string>('');
  const [autoRefresh, setAutoRefresh] = useState(true);

  const fetchLogs = useCallback(async () => {
    setLoading(true);
    setError(null);

    try {
      const params: LogQueryParams = {
        since: timeRange,
        limit: 200,
      };

      if (service !== 'All') params.service = service;
      if (level !== 'All') params.level = level;
      if (search.trim()) params.search = search.trim();

      const response = await getSystemLogs(params);
      setLogs(response.entries);
      setTotalCount(response.total_count);
    } catch (err) {
      console.error('Failed to fetch logs:', err);
      setError(err instanceof Error ? err.message : 'Failed to fetch logs');
    } finally {
      setLoading(false);
    }
  }, [service, level, timeRange, search]);

  // Initial fetch
  useEffect(() => {
    if (!isOpen) return;
    fetchLogs();
  }, [isOpen, fetchLogs]);

  // Auto-refresh
  usePolling(fetchLogs, 5000, isOpen && autoRefresh);

  // Format timestamp for display
  const formatTime = (timestamp: string): string => {
    try {
      const date = new Date(timestamp);
      return date.toLocaleTimeString('en-US', {
        hour12: false,
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      });
    } catch {
      return timestamp;
    }
  };

  const formatDate = (timestamp: string): string => {
    try {
      const date = new Date(timestamp);
      return date.toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric',
      });
    } catch {
      return '';
    }
  };

  // Get Grafana explore URL
  const getGrafanaUrl = (): string => {
    const baseUrl = `${window.location.protocol}//${window.location.hostname}:3000`;
    return `${baseUrl}/explore?orgId=1&left=%5B%22now-${timeRange}%22,%22now%22,%22Loki%22,%7B%22expr%22:%22%7Bservice%3D~%5C%22api%7Cworker%7Cagent%5C%22%7D%22%7D%5D`;
  };

  return (
    <Modal isOpen={isOpen} onClose={onClose} title="System Logs" size="xl">
      <div className="flex flex-col h-[70vh]">
        {/* Filters */}
        <div className="flex flex-wrap items-center gap-3 pb-4 border-b border-stone-200 dark:border-stone-700">
          {/* Service filter */}
          <div className="flex items-center gap-2">
            <label className="text-xs font-medium text-stone-500 dark:text-stone-400">Service:</label>
            <select
              value={service}
              onChange={(e) => setService(e.target.value)}
              className="px-2 py-1 text-sm bg-stone-100 dark:bg-stone-800 border border-stone-300 dark:border-stone-600 rounded-md text-stone-700 dark:text-stone-200"
            >
              {SERVICES.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </div>

          {/* Level filter */}
          <div className="flex items-center gap-2">
            <label className="text-xs font-medium text-stone-500 dark:text-stone-400">Level:</label>
            <select
              value={level}
              onChange={(e) => setLevel(e.target.value)}
              className="px-2 py-1 text-sm bg-stone-100 dark:bg-stone-800 border border-stone-300 dark:border-stone-600 rounded-md text-stone-700 dark:text-stone-200"
            >
              {LOG_LEVELS.map((l) => (
                <option key={l} value={l}>{l}</option>
              ))}
            </select>
          </div>

          {/* Time range filter */}
          <div className="flex items-center gap-2">
            <label className="text-xs font-medium text-stone-500 dark:text-stone-400">Time:</label>
            <select
              value={timeRange}
              onChange={(e) => setTimeRange(e.target.value)}
              className="px-2 py-1 text-sm bg-stone-100 dark:bg-stone-800 border border-stone-300 dark:border-stone-600 rounded-md text-stone-700 dark:text-stone-200"
            >
              {TIME_RANGES.map((t) => (
                <option key={t.value} value={t.value}>{t.label}</option>
              ))}
            </select>
          </div>

          {/* Search */}
          <div className="flex items-center gap-2 flex-1 min-w-[200px]">
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && fetchLogs()}
              placeholder="Search logs..."
              className="flex-1 px-3 py-1 text-sm bg-stone-100 dark:bg-stone-800 border border-stone-300 dark:border-stone-600 rounded-md text-stone-700 dark:text-stone-200 placeholder-stone-400"
            />
          </div>

          {/* Auto-refresh toggle */}
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
              className="w-4 h-4 rounded border-stone-300 dark:border-stone-600 text-sage-600 focus:ring-sage-500"
            />
            <span className="text-xs text-stone-500 dark:text-stone-400">Auto-refresh</span>
          </label>

          {/* Manual refresh */}
          <button
            onClick={fetchLogs}
            disabled={loading}
            className="px-3 py-1 text-sm bg-sage-600 hover:bg-sage-500 disabled:bg-stone-400 text-white rounded-md transition-colors"
          >
            <i className={`fa-solid fa-rotate ${loading ? 'animate-spin' : ''}`}></i>
          </button>
        </div>

        {/* Error display */}
        {error && (
          <div className="mt-3 p-3 bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300 rounded-md text-sm">
            {error}
          </div>
        )}

        {/* Logs table */}
        <div className="flex-1 overflow-auto mt-4">
          {logs.length === 0 && !loading ? (
            <div className="flex flex-col items-center justify-center h-full text-stone-400 dark:text-stone-500">
              <i className="fa-solid fa-file-lines text-4xl mb-3 opacity-30"></i>
              <p className="text-sm">No logs found</p>
              <p className="text-xs mt-1">
                {service !== 'All' || level !== 'All' || search
                  ? 'Try adjusting your filters'
                  : 'Logs will appear here when available'}
              </p>
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-stone-100 dark:bg-stone-800">
                <tr>
                  <th className="px-3 py-2 text-left text-xs font-medium text-stone-500 dark:text-stone-400 uppercase tracking-wider w-20">Time</th>
                  <th className="px-3 py-2 text-left text-xs font-medium text-stone-500 dark:text-stone-400 uppercase tracking-wider w-20">Level</th>
                  <th className="px-3 py-2 text-left text-xs font-medium text-stone-500 dark:text-stone-400 uppercase tracking-wider w-20">Service</th>
                  <th className="px-3 py-2 text-left text-xs font-medium text-stone-500 dark:text-stone-400 uppercase tracking-wider">Message</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-stone-200 dark:divide-stone-700">
                {logs.map((log, index) => (
                  <tr
                    key={`${log.timestamp}-${index}`}
                    className="hover:bg-stone-50 dark:hover:bg-stone-800/50"
                  >
                    <td className="px-3 py-2 text-stone-500 dark:text-stone-400 whitespace-nowrap font-mono text-xs">
                      <div>{formatTime(log.timestamp)}</div>
                      <div className="text-[10px] text-stone-400 dark:text-stone-500">{formatDate(log.timestamp)}</div>
                    </td>
                    <td className="px-3 py-2">
                      <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${levelColors[log.level] || 'text-stone-600 dark:text-stone-400 bg-stone-100 dark:bg-stone-800'}`}>
                        {log.level}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-stone-600 dark:text-stone-300 text-xs">
                      {log.service}
                    </td>
                    <td className="px-3 py-2 text-stone-700 dark:text-stone-200 font-mono text-xs break-all">
                      <div>{log.message}</div>
                      {log.correlation_id && (
                        <div className="text-[10px] text-stone-400 dark:text-stone-500 mt-1">
                          <i className="fa-solid fa-link mr-1"></i>
                          {log.correlation_id.slice(0, 8)}
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between pt-4 mt-4 border-t border-stone-200 dark:border-stone-700">
          <span className="text-xs text-stone-500 dark:text-stone-400">
            Showing {logs.length} of {totalCount} entries
          </span>
          <a
            href={getGrafanaUrl()}
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-sage-600 dark:text-sage-400 hover:underline"
          >
            <i className="fa-solid fa-external-link mr-1"></i>
            Open in Grafana
          </a>
        </div>
      </div>
    </Modal>
  );
};

export default SystemLogsModal;
