import React, { useCallback, useEffect, useMemo, useState, useRef } from 'react';
import { TaskLogEntry } from './TaskLogPanel';
import { LabLogEntry, LabLogJob, LabLogsResponse, LabLogsQueryParams } from '../../api';

interface LogsViewProps {
  labId: string;
  studioRequest: <T>(path: string, options?: RequestInit) => Promise<T>;
  realtimeEntries?: TaskLogEntry[];
}

const LogsView: React.FC<LogsViewProps> = ({
  labId,
  studioRequest,
  realtimeEntries = [],
}) => {
  const [logs, setLogs] = useState<LabLogsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [copied, setCopied] = useState(false);

  // Filters
  const [selectedJobId, setSelectedJobId] = useState<string>('all');
  const [selectedHostId, setSelectedHostId] = useState<string>('all');
  const [selectedLevel, setSelectedLevel] = useState<string>('all');
  const [selectedSince, setSelectedSince] = useState<string>('all');
  const [searchQuery, setSearchQuery] = useState<string>('');

  // Host sidebar collapsed state
  const [hostSidebarCollapsed, setHostSidebarCollapsed] = useState(false);

  const autoRefreshIntervalRef = useRef<number | null>(null);

  // Build query params from filter state
  const queryParams = useMemo((): LabLogsQueryParams => {
    const params: LabLogsQueryParams = {};
    if (selectedJobId !== 'all') params.job_id = selectedJobId;
    if (selectedHostId !== 'all') params.host_id = selectedHostId;
    if (selectedLevel !== 'all') params.level = selectedLevel;
    if (selectedSince !== 'all') params.since = selectedSince;
    if (searchQuery.trim()) params.search = searchQuery.trim();
    params.limit = 500;
    return params;
  }, [selectedJobId, selectedHostId, selectedLevel, selectedSince, searchQuery]);

  // Load logs
  const loadLogs = useCallback(async () => {
    if (!labId) return;
    setLoading(true);
    setError(null);
    try {
      const queryString = new URLSearchParams();
      if (queryParams.job_id) queryString.set('job_id', queryParams.job_id);
      if (queryParams.host_id) queryString.set('host_id', queryParams.host_id);
      if (queryParams.level) queryString.set('level', queryParams.level);
      if (queryParams.since) queryString.set('since', queryParams.since);
      if (queryParams.search) queryString.set('search', queryParams.search);
      if (queryParams.limit) queryString.set('limit', queryParams.limit.toString());

      const qs = queryString.toString();
      const path = `/labs/${labId}/logs${qs ? `?${qs}` : ''}`;
      const data = await studioRequest<LabLogsResponse>(path);
      setLogs(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load logs');
    } finally {
      setLoading(false);
    }
  }, [labId, queryParams, studioRequest]);

  useEffect(() => {
    loadLogs();
  }, [loadLogs]);

  // Auto-refresh
  useEffect(() => {
    if (autoRefresh) {
      autoRefreshIntervalRef.current = window.setInterval(loadLogs, 5000);
    } else if (autoRefreshIntervalRef.current) {
      window.clearInterval(autoRefreshIntervalRef.current);
      autoRefreshIntervalRef.current = null;
    }
    return () => {
      if (autoRefreshIntervalRef.current) {
        window.clearInterval(autoRefreshIntervalRef.current);
      }
    };
  }, [autoRefresh, loadLogs]);

  // Merge job logs with realtime entries
  const allEntries = useMemo(() => {
    const entries: Array<LabLogEntry & { isRealtime?: boolean }> = [];

    // Add job log entries
    if (logs?.entries) {
      entries.push(...logs.entries);
    }

    // Add realtime entries (convert TaskLogEntry to LabLogEntry format)
    realtimeEntries.forEach((entry) => {
      entries.push({
        timestamp: entry.timestamp.toISOString(),
        level: entry.level,
        message: entry.message,
        job_id: entry.jobId || null,
        host_id: null,
        host_name: null,
        source: 'realtime',
        isRealtime: true,
      });
    });

    // Sort by timestamp
    entries.sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime());

    return entries;
  }, [logs?.entries, realtimeEntries]);

  // Get all hosts (from logs + filter for sidebar)
  const allHosts = useMemo(() => {
    const hosts = new Set<string>();
    if (logs?.hosts) {
      logs.hosts.forEach((h) => hosts.add(h));
    }
    return Array.from(hosts).sort();
  }, [logs?.hosts]);

  // Level colors (matching TaskLogPanel)
  const levelColors: Record<string, string> = {
    info: 'text-cyan-700 dark:text-cyan-400',
    success: 'text-green-700 dark:text-green-400',
    warning: 'text-amber-700 dark:text-yellow-400',
    error: 'text-red-700 dark:text-red-400',
  };

  const levelBorders: Record<string, string> = {
    info: 'border-l-cyan-500',
    success: 'border-l-green-500',
    warning: 'border-l-amber-500 dark:border-l-yellow-500',
    error: 'border-l-red-500 bg-red-100/50 dark:bg-red-900/20',
  };

  // Format timestamp
  const formatTimestamp = (timestamp: string) => {
    const date = new Date(timestamp);
    return date.toLocaleTimeString('en-US', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    });
  };

  // Format job action for display
  const formatJobAction = (action: string) => {
    if (action.startsWith('sync:')) {
      return action.replace('sync:', 'Sync ');
    }
    if (action.startsWith('node:')) {
      const parts = action.split(':');
      return `Node ${parts[1]} (${parts[2] || ''})`;
    }
    return action.toUpperCase();
  };

  // Copy all visible logs
  const handleCopyAll = async () => {
    const text = allEntries
      .map((e) => {
        const ts = formatTimestamp(e.timestamp);
        const host = e.host_name ? `[${e.host_name}]` : '';
        return `${ts} ${e.level.toUpperCase().padEnd(7)} ${host} ${e.message}`;
      })
      .join('\n');

    await navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  // Copy single entry
  const handleCopyEntry = async (entry: LabLogEntry) => {
    const ts = formatTimestamp(entry.timestamp);
    const host = entry.host_name ? `[${entry.host_name}]` : '';
    const text = `${ts} ${entry.level.toUpperCase()} ${host} ${entry.message}`;
    await navigator.clipboard.writeText(text);
  };

  // Export as text file
  const handleExport = () => {
    const text = allEntries
      .map((e) => {
        const ts = new Date(e.timestamp).toISOString();
        const host = e.host_name || '-';
        return `${ts}\t${e.level.toUpperCase()}\t${host}\t${e.message}`;
      })
      .join('\n');

    const blob = new Blob([text], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `lab-logs-${labId}-${new Date().toISOString().slice(0, 10)}.txt`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  return (
    <div className="flex-1 bg-stone-50 dark:bg-stone-950 flex flex-col overflow-hidden animate-in fade-in duration-300">
      {/* Header */}
      <header className="px-6 py-4 border-b border-stone-200 dark:border-stone-800 bg-white/50 dark:bg-stone-900/50 backdrop-blur-sm">
        <div className="flex flex-wrap justify-between items-end gap-4">
          <div>
            <h1 className="text-2xl font-black text-stone-900 dark:text-white tracking-tight">
              Logs
            </h1>
            <p className="text-stone-500 dark:text-stone-400 text-xs mt-1">
              View logs from jobs and operations affecting this lab.
            </p>
          </div>
          <div className="flex gap-2 items-center">
            <label className="flex items-center gap-2 text-xs text-stone-600 dark:text-stone-400 cursor-pointer">
              <input
                type="checkbox"
                checked={autoRefresh}
                onChange={(e) => setAutoRefresh(e.target.checked)}
                className="rounded border-stone-300 dark:border-stone-600 text-sage-600 focus:ring-sage-500"
              />
              Auto-refresh
            </label>
            <button
              onClick={handleCopyAll}
              disabled={allEntries.length === 0}
              className="px-3 py-2 bg-stone-200 dark:bg-stone-800 hover:bg-stone-300 dark:hover:bg-stone-700 disabled:opacity-50 text-stone-700 dark:text-white rounded-lg text-xs font-bold transition-all flex items-center gap-2"
            >
              <i className={`fa-solid ${copied ? 'fa-check' : 'fa-copy'}`} />
              {copied ? 'Copied!' : 'Copy All'}
            </button>
            <button
              onClick={loadLogs}
              disabled={loading}
              className="px-3 py-2 bg-stone-200 dark:bg-stone-800 hover:bg-stone-300 dark:hover:bg-stone-700 text-stone-700 dark:text-white rounded-lg text-xs font-bold transition-all"
            >
              <i className={`fa-solid ${loading ? 'fa-spinner fa-spin' : 'fa-rotate'}`} />
            </button>
          </div>
        </div>
      </header>

      {/* Filter bar */}
      <div className="px-6 py-3 border-b border-stone-200 dark:border-stone-800 bg-white/30 dark:bg-stone-900/30 flex flex-wrap items-center gap-3">
        {/* Job filter */}
        <div className="flex items-center gap-2">
          <label className="text-[10px] font-bold text-stone-500 uppercase">Job</label>
          <select
            value={selectedJobId}
            onChange={(e) => setSelectedJobId(e.target.value)}
            className="px-2 py-1.5 text-xs bg-stone-100 dark:bg-stone-800 border border-stone-200 dark:border-stone-700 rounded-lg text-stone-700 dark:text-stone-300 focus:outline-none focus:ring-1 focus:ring-sage-500"
          >
            <option value="all">All Jobs</option>
            {logs?.jobs.map((job) => (
              <option key={job.id} value={job.id}>
                {formatJobAction(job.action)} ({job.status})
              </option>
            ))}
          </select>
        </div>

        {/* Host filter */}
        <div className="flex items-center gap-2">
          <label className="text-[10px] font-bold text-stone-500 uppercase">Host</label>
          <select
            value={selectedHostId}
            onChange={(e) => setSelectedHostId(e.target.value)}
            className="px-2 py-1.5 text-xs bg-stone-100 dark:bg-stone-800 border border-stone-200 dark:border-stone-700 rounded-lg text-stone-700 dark:text-stone-300 focus:outline-none focus:ring-1 focus:ring-sage-500"
          >
            <option value="all">All Hosts</option>
            {allHosts.map((host) => (
              <option key={host} value={host}>
                {host}
              </option>
            ))}
          </select>
        </div>

        {/* Level filter */}
        <div className="flex items-center gap-2">
          <label className="text-[10px] font-bold text-stone-500 uppercase">Level</label>
          <select
            value={selectedLevel}
            onChange={(e) => setSelectedLevel(e.target.value)}
            className="px-2 py-1.5 text-xs bg-stone-100 dark:bg-stone-800 border border-stone-200 dark:border-stone-700 rounded-lg text-stone-700 dark:text-stone-300 focus:outline-none focus:ring-1 focus:ring-sage-500"
          >
            <option value="all">All Levels</option>
            <option value="info">Info+</option>
            <option value="warning">Warning+</option>
            <option value="error">Errors Only</option>
          </select>
        </div>

        {/* Time filter */}
        <div className="flex items-center gap-2">
          <label className="text-[10px] font-bold text-stone-500 uppercase">Time</label>
          <select
            value={selectedSince}
            onChange={(e) => setSelectedSince(e.target.value)}
            className="px-2 py-1.5 text-xs bg-stone-100 dark:bg-stone-800 border border-stone-200 dark:border-stone-700 rounded-lg text-stone-700 dark:text-stone-300 focus:outline-none focus:ring-1 focus:ring-sage-500"
          >
            <option value="all">All Time</option>
            <option value="15m">Last 15 min</option>
            <option value="1h">Last 1 hour</option>
            <option value="24h">Last 24 hours</option>
          </select>
        </div>

        {/* Search */}
        <div className="flex-1 min-w-[200px]">
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search logs..."
            className="w-full px-3 py-1.5 text-xs bg-stone-100 dark:bg-stone-800 border border-stone-200 dark:border-stone-700 rounded-lg text-stone-700 dark:text-stone-300 placeholder-stone-400 focus:outline-none focus:ring-1 focus:ring-sage-500"
          />
        </div>
      </div>

      {/* Main content */}
      <div className="flex-1 flex overflow-hidden">
        {/* Host sidebar */}
        <div
          className={`border-r border-stone-200 dark:border-stone-800 flex flex-col overflow-hidden bg-white/30 dark:bg-stone-900/30 transition-all ${
            hostSidebarCollapsed ? 'w-10' : 'w-40'
          }`}
        >
          <div className="p-2 border-b border-stone-200 dark:border-stone-800 flex items-center justify-between">
            {!hostSidebarCollapsed && (
              <span className="text-[10px] font-bold text-stone-500 uppercase tracking-widest">
                Hosts
              </span>
            )}
            <button
              onClick={() => setHostSidebarCollapsed(!hostSidebarCollapsed)}
              className="p-1 text-stone-400 hover:text-stone-600 dark:hover:text-stone-300"
            >
              <i className={`fa-solid fa-chevron-${hostSidebarCollapsed ? 'right' : 'left'} text-xs`} />
            </button>
          </div>
          {!hostSidebarCollapsed && (
            <div className="flex-1 overflow-y-auto custom-scrollbar">
              <button
                onClick={() => setSelectedHostId('all')}
                className={`w-full px-3 py-2 flex items-center gap-2 text-left text-xs transition-colors ${
                  selectedHostId === 'all'
                    ? 'bg-sage-600/20 text-sage-700 dark:text-sage-300 border-r-2 border-sage-500'
                    : 'text-stone-600 dark:text-stone-400 hover:bg-stone-100 dark:hover:bg-stone-800'
                }`}
              >
                <span className="w-2 h-2 rounded-full bg-sage-500" />
                All
              </button>
              {allHosts.map((host, idx) => {
                const colors = ['bg-blue-500', 'bg-purple-500', 'bg-orange-500', 'bg-pink-500', 'bg-teal-500'];
                const color = colors[idx % colors.length];
                return (
                  <button
                    key={host}
                    onClick={() => setSelectedHostId(host)}
                    className={`w-full px-3 py-2 flex items-center gap-2 text-left text-xs transition-colors ${
                      selectedHostId === host
                        ? 'bg-sage-600/20 text-sage-700 dark:text-sage-300 border-r-2 border-sage-500'
                        : 'text-stone-600 dark:text-stone-400 hover:bg-stone-100 dark:hover:bg-stone-800'
                    }`}
                  >
                    <span className={`w-2 h-2 rounded-full ${color}`} />
                    <span className="truncate">{host}</span>
                  </button>
                );
              })}
            </div>
          )}
        </div>

        {/* Log entries */}
        <div className="flex-1 flex flex-col overflow-hidden">
          {error && (
            <div className="p-4 bg-red-50 dark:bg-red-900/20 border-b border-red-200 dark:border-red-800 text-red-700 dark:text-red-400 text-xs">
              <i className="fa-solid fa-exclamation-circle mr-2" />
              {error}
            </div>
          )}

          {loading && allEntries.length === 0 && (
            <div className="flex-1 flex items-center justify-center">
              <div className="text-center">
                <i className="fa-solid fa-spinner fa-spin text-2xl text-stone-400 mb-3" />
                <p className="text-xs text-stone-500">Loading logs...</p>
              </div>
            </div>
          )}

          {!loading && allEntries.length === 0 && (
            <div className="flex-1 flex items-center justify-center">
              <div className="text-center">
                <i className="fa-solid fa-file-lines text-4xl text-stone-300 dark:text-stone-700 mb-4" />
                <p className="text-sm text-stone-500 dark:text-stone-400">No log entries found</p>
                <p className="text-xs text-stone-400 dark:text-stone-600 mt-1">
                  {searchQuery || selectedLevel !== 'all' || selectedJobId !== 'all'
                    ? 'Try adjusting your filters'
                    : 'Logs will appear here after lab operations'}
                </p>
              </div>
            </div>
          )}

          {allEntries.length > 0 && (
            <div className="flex-1 overflow-y-auto font-mono text-[11px] custom-scrollbar">
              {allEntries.map((entry, idx) => {
                const isRealtime = (entry as any).isRealtime;
                return (
                  <div
                    key={`${entry.timestamp}-${idx}`}
                    onClick={() => handleCopyEntry(entry)}
                    className={`flex gap-3 px-4 py-1.5 border-l-2 cursor-pointer hover:bg-stone-100 dark:hover:bg-stone-800/50 ${
                      levelBorders[entry.level] || 'border-l-stone-300'
                    } ${isRealtime ? 'bg-blue-50/30 dark:bg-blue-900/10' : ''}`}
                    title="Click to copy"
                  >
                    <span className="text-stone-400 dark:text-stone-600 min-w-[70px] shrink-0">
                      {formatTimestamp(entry.timestamp)}
                    </span>
                    <span
                      className={`min-w-[50px] shrink-0 font-bold uppercase ${
                        levelColors[entry.level] || 'text-stone-500'
                      }`}
                    >
                      {entry.level}
                    </span>
                    {entry.host_name && (
                      <span className="px-1.5 py-0.5 bg-stone-200 dark:bg-stone-700 text-stone-600 dark:text-stone-400 rounded text-[9px] font-medium shrink-0">
                        {entry.host_name}
                      </span>
                    )}
                    <span className="text-stone-700 dark:text-stone-300 flex-1 break-words">
                      {entry.message}
                    </span>
                    {isRealtime && (
                      <span className="text-blue-500 dark:text-blue-400 text-[9px] shrink-0">
                        LIVE
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* Footer */}
      <footer className="px-6 py-2 border-t border-stone-200 dark:border-stone-800 bg-white/30 dark:bg-stone-900/30 flex items-center justify-between text-xs text-stone-500 dark:text-stone-400">
        <div className="flex items-center gap-4">
          <span>
            {logs?.total_count || 0} entries
            {(logs?.error_count || 0) > 0 && (
              <span className="ml-1 text-red-600 dark:text-red-400">
                ({logs?.error_count} errors)
              </span>
            )}
          </span>
          {logs?.has_more && (
            <span className="text-amber-600 dark:text-amber-400">
              <i className="fa-solid fa-warning mr-1" />
              Results limited
            </span>
          )}
        </div>
        <button
          onClick={handleExport}
          disabled={allEntries.length === 0}
          className="px-3 py-1 bg-stone-200 dark:bg-stone-800 hover:bg-stone-300 dark:hover:bg-stone-700 disabled:opacity-50 rounded text-xs font-medium transition-colors flex items-center gap-2"
        >
          <i className="fa-solid fa-download" />
          Export as Text
        </button>
      </footer>
    </div>
  );
};

export default LogsView;
