import React, { useCallback, useEffect, useState } from 'react';
import { Navigate, useNavigate } from 'react-router-dom';
import { useTheme, ThemeSelector } from '../theme/index';
import { useUser } from '../contexts/UserContext';
import { apiRequest } from '../api';
import { ArchetypeIcon } from '../components/icons';
import { formatStorageSize, formatTimestamp } from '../utils/format';
import {
  getCpuColor,
  getMemoryColor,
  getStorageColor,
  getConnectionStatusColor,
  getConnectionStatusText,
  getRoleBadgeColor,
  getRoleLabel,
  type ConnectionStatus,
  type RoleBadgeType,
} from '../utils/status';

interface LabInfo {
  id: string;
  name: string;
  state: string;
}

interface HostDetailed {
  id: string;
  name: string;
  address: string;
  status: string;
  version: string;
  role: 'agent' | 'controller' | 'agent+controller';
  image_sync_strategy?: string;
  deployment_mode?: 'systemd' | 'docker' | 'unknown';
  capabilities: {
    providers?: string[];
    features?: string[];
    max_concurrent_jobs?: number;
  };
  resource_usage: {
    cpu_percent: number;
    memory_percent: number;
    memory_used_gb: number;
    memory_total_gb: number;
    storage_percent: number;
    storage_used_gb: number;
    storage_total_gb: number;
    containers_running: number;
    containers_total: number;
  };
  labs: LabInfo[];
  lab_count: number;
  last_heartbeat: string | null;
}

interface UpdateStatus {
  job_id: string;
  agent_id: string;
  from_version: string;
  to_version: string;
  status: string;
  progress_percent: number;
  error_message: string | null;
}

type SyncStrategy = 'push' | 'pull' | 'on_demand' | 'disabled';

const SYNC_STRATEGY_OPTIONS: { value: SyncStrategy; label: string; description: string }[] = [
  { value: 'on_demand', label: 'On Demand', description: 'Sync when deployment needs image' },
  { value: 'push', label: 'Push', description: 'Receive images on upload' },
  { value: 'pull', label: 'Pull', description: 'Pull images when online' },
  { value: 'disabled', label: 'Disabled', description: 'Manual sync only' },
];

const HostsPage: React.FC = () => {
  const { effectiveMode, toggleMode } = useTheme();
  const { user, loading: userLoading } = useUser();
  const navigate = useNavigate();
  const [hosts, setHosts] = useState<HostDetailed[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showThemeSelector, setShowThemeSelector] = useState(false);
  const [expandedLabs, setExpandedLabs] = useState<Set<string>>(new Set());
  const [latestVersion, setLatestVersion] = useState<string>('');
  const [updatingAgents, setUpdatingAgents] = useState<Set<string>>(new Set());
  const [updateStatuses, setUpdateStatuses] = useState<Map<string, UpdateStatus>>(new Map());

  const loadLatestVersion = useCallback(async () => {
    try {
      const data = await apiRequest<{ version: string }>('/agents/updates/latest');
      setLatestVersion(data.version);
    } catch (err) {
      console.error('Failed to load latest version:', err);
    }
  }, []);

  const loadHosts = useCallback(async () => {
    try {
      const data = await apiRequest<HostDetailed[]>('/agents/detailed');
      setHosts(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load hosts');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadHosts();
    loadLatestVersion();
    const interval = setInterval(loadHosts, 10000);
    return () => clearInterval(interval);
  }, [loadHosts, loadLatestVersion]);

  // Poll update status for agents being updated
  useEffect(() => {
    if (updatingAgents.size === 0) return;

    const pollInterval = setInterval(async () => {
      for (const agentId of updatingAgents) {
        try {
          const status = await apiRequest<UpdateStatus | null>(`/agents/${agentId}/update-status`);
          if (status) {
            setUpdateStatuses(prev => new Map(prev).set(agentId, status));

            // If completed or failed, remove from updating set
            if (status.status === 'completed' || status.status === 'failed') {
              setUpdatingAgents(prev => {
                const next = new Set(prev);
                next.delete(agentId);
                return next;
              });

              // Refresh hosts to get updated version
              if (status.status === 'completed') {
                loadHosts();
              }
            }
          }
        } catch (err) {
          console.error(`Failed to poll update status for ${agentId}:`, err);
        }
      }
    }, 2000);

    return () => clearInterval(pollInterval);
  }, [updatingAgents, loadHosts]);

  // Redirect non-admins
  if (!userLoading && user && !user.is_admin) {
    return <Navigate to="/" replace />;
  }

  if (!userLoading && !user) {
    return <Navigate to="/" replace />;
  }

  const toggleLabsExpanded = (hostId: string) => {
    setExpandedLabs(prev => {
      const next = new Set(prev);
      if (next.has(hostId)) {
        next.delete(hostId);
      } else {
        next.add(hostId);
      }
      return next;
    });
  };

  const updateSyncStrategy = async (hostId: string, strategy: SyncStrategy) => {
    try {
      await apiRequest(`/agents/${hostId}/sync-strategy`, {
        method: 'PUT',
        body: JSON.stringify({ strategy }),
      });
      // Update local state
      setHosts(prev => prev.map(h =>
        h.id === hostId ? { ...h, image_sync_strategy: strategy } : h
      ));
    } catch (err) {
      console.error('Failed to update sync strategy:', err);
      alert(err instanceof Error ? err.message : 'Failed to update sync strategy');
    }
  };

  const triggerUpdate = async (hostId: string) => {
    try {
      setUpdatingAgents(prev => new Set(prev).add(hostId));
      const response = await apiRequest<{ job_id: string; status: string; message: string }>(
        `/agents/${hostId}/update`,
        { method: 'POST' }
      );

      if (response.status === 'failed') {
        alert(response.message || 'Update failed to start');
        setUpdatingAgents(prev => {
          const next = new Set(prev);
          next.delete(hostId);
          return next;
        });
      }
    } catch (err) {
      console.error('Failed to trigger update:', err);
      alert(err instanceof Error ? err.message : 'Failed to trigger update');
      setUpdatingAgents(prev => {
        const next = new Set(prev);
        next.delete(hostId);
        return next;
      });
    }
  };

  const triggerBulkUpdate = async () => {
    const outdatedAgents = hosts.filter(
      h => h.status === 'online' && h.version && h.version !== latestVersion
    );

    if (outdatedAgents.length === 0) {
      alert('All agents are already up to date');
      return;
    }

    if (!confirm(`Update ${outdatedAgents.length} agent(s) to version ${latestVersion}?`)) {
      return;
    }

    try {
      const agentIds = outdatedAgents.map(h => h.id);
      setUpdatingAgents(prev => {
        const next = new Set(prev);
        agentIds.forEach(id => next.add(id));
        return next;
      });

      const response = await apiRequest<{
        success_count: number;
        failure_count: number;
        results: Array<{ agent_id: string; success: boolean; error?: string }>;
      }>('/agents/updates/bulk', {
        method: 'POST',
        body: JSON.stringify({ agent_ids: agentIds }),
      });

      if (response.failure_count > 0) {
        const failures = response.results
          .filter(r => !r.success)
          .map(r => `${r.agent_id}: ${r.error}`)
          .join('\n');
        alert(`${response.success_count} updates started, ${response.failure_count} failed:\n${failures}`);
      }

      // Remove failed agents from updating set
      response.results.filter(r => !r.success).forEach(r => {
        setUpdatingAgents(prev => {
          const next = new Set(prev);
          next.delete(r.agent_id);
          return next;
        });
      });
    } catch (err) {
      console.error('Failed to trigger bulk update:', err);
      alert(err instanceof Error ? err.message : 'Failed to trigger bulk update');
      setUpdatingAgents(new Set());
    }
  };

  const isUpdateAvailable = (host: HostDetailed): boolean => {
    if (!latestVersion || !host.version) return false;
    return host.version !== latestVersion;
  };

  const outdatedCount = hosts.filter(
    h => h.status === 'online' && isUpdateAvailable(h)
  ).length;

  return (
    <>
      <div className="min-h-screen bg-stone-50 dark:bg-stone-900 flex flex-col overflow-hidden">
        <header className="h-20 border-b border-stone-200 dark:border-stone-800 bg-white/30 dark:bg-stone-900/30 flex items-center justify-between px-10">
          <div className="flex items-center gap-4">
            <ArchetypeIcon size={40} className="text-sage-600 dark:text-sage-400" />
            <div>
              <h1 className="text-xl font-black text-stone-900 dark:text-white tracking-tight">ARCHETYPE</h1>
              <p className="text-[10px] text-sage-600 dark:text-sage-500 font-bold uppercase tracking-widest">Host Management</p>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <button
              onClick={() => navigate('/')}
              className="flex items-center gap-2 px-3 py-2 bg-stone-100 dark:bg-stone-800 hover:bg-stone-200 dark:hover:bg-stone-700 text-stone-600 dark:text-stone-300 border border-stone-300 dark:border-stone-700 rounded-lg transition-all"
            >
              <i className="fa-solid fa-arrow-left text-xs"></i>
              <span className="text-[10px] font-bold uppercase">Back</span>
            </button>

            <button
              onClick={() => setShowThemeSelector(true)}
              className="w-9 h-9 flex items-center justify-center bg-stone-100 dark:bg-stone-800 text-stone-600 dark:text-stone-400 hover:text-sage-600 dark:hover:text-sage-400 rounded-lg transition-all border border-stone-300 dark:border-stone-700"
              title="Theme Settings"
            >
              <i className="fa-solid fa-palette text-sm"></i>
            </button>

            <button
              onClick={toggleMode}
              className="w-9 h-9 flex items-center justify-center bg-stone-100 dark:bg-stone-800 text-stone-600 dark:text-stone-400 hover:text-sage-600 dark:hover:text-sage-400 rounded-lg transition-all border border-stone-300 dark:border-stone-700"
              title={`Switch to ${effectiveMode === 'dark' ? 'light' : 'dark'} mode`}
            >
              <i className={`fa-solid ${effectiveMode === 'dark' ? 'fa-sun' : 'fa-moon'} text-sm`}></i>
            </button>

            <button
              onClick={loadHosts}
              className="flex items-center gap-2 px-3 py-2 bg-stone-100 dark:bg-stone-800 hover:bg-stone-200 dark:hover:bg-stone-700 text-stone-600 dark:text-stone-300 border border-stone-300 dark:border-stone-700 rounded-lg transition-all"
            >
              <i className="fa-solid fa-rotate text-xs"></i>
              <span className="text-[10px] font-bold uppercase">Refresh</span>
            </button>
          </div>
        </header>

        <main className="flex-1 overflow-y-auto p-10 custom-scrollbar">
          <div className="max-w-7xl mx-auto">
            <div className="flex justify-between items-center mb-8">
              <div>
                <h2 className="text-2xl font-bold text-stone-900 dark:text-white">Compute Hosts</h2>
                <p className="text-stone-500 text-sm mt-1">
                  Monitor and manage infrastructure agents across your environment.
                  {latestVersion && <span className="ml-2 text-sage-600 dark:text-sage-400">Latest: v{latestVersion}</span>}
                </p>
              </div>
              <div className="flex items-center gap-4 text-sm text-stone-600 dark:text-stone-400">
                {outdatedCount > 0 && (
                  <button
                    onClick={triggerBulkUpdate}
                    className="flex items-center gap-2 px-3 py-1.5 bg-amber-100 dark:bg-amber-900/30 hover:bg-amber-200 dark:hover:bg-amber-900/50 text-amber-700 dark:text-amber-400 rounded-lg transition-all text-xs font-medium"
                  >
                    <i className="fa-solid fa-download"></i>
                    Update {outdatedCount} Agent{outdatedCount !== 1 ? 's' : ''}
                  </button>
                )}
                <div className="flex items-center gap-2">
                  <div className="w-3 h-3 rounded-full bg-green-500"></div>
                  <span>{hosts.filter(h => h.status === 'online').length} Online</span>
                </div>
                <div className="flex items-center gap-2">
                  <div className="w-3 h-3 rounded-full bg-red-500"></div>
                  <span>{hosts.filter(h => h.status !== 'online').length} Offline</span>
                </div>
              </div>
            </div>

            {loading && hosts.length === 0 ? (
              <div className="flex items-center justify-center py-20">
                <i className="fa-solid fa-spinner fa-spin text-stone-400 text-2xl"></i>
                <span className="ml-3 text-stone-500">Loading hosts...</span>
              </div>
            ) : error ? (
              <div className="text-center py-20 text-red-500">
                <i className="fa-solid fa-exclamation-circle text-3xl mb-3"></i>
                <p>{error}</p>
              </div>
            ) : hosts.length === 0 ? (
              <div className="col-span-full py-20 bg-stone-100/50 dark:bg-stone-900/30 border-2 border-dashed border-stone-300 dark:border-stone-800 rounded-3xl flex flex-col items-center justify-center text-stone-500 dark:text-stone-600">
                <i className="fa-solid fa-server text-5xl mb-4 opacity-10"></i>
                <h3 className="text-lg font-bold text-stone-500 dark:text-stone-400">No Hosts Registered</h3>
                <p className="text-sm max-w-xs text-center mt-1">Start an agent to register hosts with the controller.</p>
              </div>
            ) : (
              <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-6">
                {hosts.map((host) => {
                  const isExpanded = expandedLabs.has(host.id);
                  const hasMultipleLabs = host.labs.length > 3;

                  return (
                    <div
                      key={host.id}
                      className="bg-white dark:bg-stone-900 border border-stone-200 dark:border-stone-800 rounded-2xl p-6 hover:border-sage-500/30 hover:shadow-xl transition-all"
                    >
                      {/* Header */}
                      <div className="flex items-start justify-between mb-4">
                        <div className="flex items-center gap-3">
                          <div className={`w-3 h-3 rounded-full ${getConnectionStatusColor(host.status as ConnectionStatus)} ${host.status === 'online' ? 'animate-pulse' : ''}`}></div>
                          <div>
                            <h3 className="font-bold text-stone-900 dark:text-white">{host.name}</h3>
                            <p className="text-xs text-stone-500">{host.address}</p>
                          </div>
                        </div>
                        <span className={`px-2 py-0.5 rounded-md text-[10px] font-bold uppercase border ${getRoleBadgeColor(host.role as RoleBadgeType)}`}>
                          {getRoleLabel(host.role as RoleBadgeType)}
                        </span>
                      </div>

                      {/* Status & Version */}
                      <div className="flex items-center gap-4 text-xs text-stone-500 dark:text-stone-400 mb-4">
                        <span className="flex items-center gap-1">
                          <i className="fa-solid fa-circle text-[8px]" style={{ color: host.status === 'online' ? '#22c55e' : '#ef4444' }}></i>
                          {getConnectionStatusText(host.status as ConnectionStatus)}
                        </span>
                        <span className={isUpdateAvailable(host) ? 'text-amber-600 dark:text-amber-400' : ''}>
                          v{host.version}
                          {isUpdateAvailable(host) && (
                            <i className="fa-solid fa-arrow-up ml-1 text-[10px]" title={`Update available: v${latestVersion}`}></i>
                          )}
                        </span>
                        <span className="text-stone-400">
                          <i className="fa-regular fa-clock mr-1"></i>
                          {formatTimestamp(host.last_heartbeat)}
                        </span>
                      </div>

                      {/* Update Progress or Button */}
                      {updatingAgents.has(host.id) ? (
                        <div className="mb-4 p-2 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg">
                          <div className="flex items-center justify-between text-xs mb-1">
                            <span className="text-blue-700 dark:text-blue-300 font-medium">
                              <i className="fa-solid fa-spinner fa-spin mr-1.5"></i>
                              {updateStatuses.get(host.id)?.status === 'downloading' && 'Downloading...'}
                              {updateStatuses.get(host.id)?.status === 'installing' && 'Installing...'}
                              {updateStatuses.get(host.id)?.status === 'restarting' && 'Restarting...'}
                              {!updateStatuses.get(host.id) && 'Starting update...'}
                            </span>
                            <span className="text-blue-600 dark:text-blue-400">
                              {updateStatuses.get(host.id)?.progress_percent || 0}%
                            </span>
                          </div>
                          <div className="h-1.5 bg-blue-200 dark:bg-blue-800 rounded-full overflow-hidden">
                            <div
                              className="h-full bg-blue-500 transition-all"
                              style={{ width: `${updateStatuses.get(host.id)?.progress_percent || 0}%` }}
                            ></div>
                          </div>
                        </div>
                      ) : isUpdateAvailable(host) && host.status === 'online' ? (
                        <div className="mb-4">
                          <button
                            onClick={() => triggerUpdate(host.id)}
                            className="w-full flex items-center justify-center gap-2 px-3 py-2 bg-amber-100 dark:bg-amber-900/30 hover:bg-amber-200 dark:hover:bg-amber-900/50 text-amber-700 dark:text-amber-400 border border-amber-300 dark:border-amber-700 rounded-lg transition-all text-xs font-medium"
                          >
                            <i className="fa-solid fa-download"></i>
                            Update to v{latestVersion}
                          </button>
                        </div>
                      ) : null}

                      {/* Resource Bars */}
                      <div className="space-y-3 mb-4">
                        {/* CPU */}
                        <div>
                          <div className="flex justify-between text-xs mb-1">
                            <span className="text-stone-500 dark:text-stone-400">CPU</span>
                            <span className="font-medium text-stone-700 dark:text-stone-300">{host.resource_usage.cpu_percent.toFixed(0)}%</span>
                          </div>
                          <div className="h-2 bg-stone-200 dark:bg-stone-700 rounded-full overflow-hidden">
                            <div className={`h-full ${getCpuColor(host.resource_usage.cpu_percent)} transition-all`} style={{ width: `${Math.min(host.resource_usage.cpu_percent, 100)}%` }}></div>
                          </div>
                        </div>

                        {/* Memory */}
                        <div>
                          <div className="flex justify-between text-xs mb-1">
                            <span className="text-stone-500 dark:text-stone-400">Memory</span>
                            <span className="font-medium text-stone-700 dark:text-stone-300">
                              {host.resource_usage.memory_total_gb > 0
                                ? `${formatStorageSize(host.resource_usage.memory_used_gb)} / ${formatStorageSize(host.resource_usage.memory_total_gb)}`
                                : `${host.resource_usage.memory_percent.toFixed(0)}%`
                              }
                            </span>
                          </div>
                          <div className="h-2 bg-stone-200 dark:bg-stone-700 rounded-full overflow-hidden">
                            <div className={`h-full ${getMemoryColor(host.resource_usage.memory_percent)} transition-all`} style={{ width: `${Math.min(host.resource_usage.memory_percent, 100)}%` }}></div>
                          </div>
                        </div>

                        {/* Storage */}
                        <div>
                          <div className="flex justify-between text-xs mb-1">
                            <span className="text-stone-500 dark:text-stone-400">Storage</span>
                            <span className="font-medium text-stone-700 dark:text-stone-300">
                              {formatStorageSize(host.resource_usage.storage_used_gb)} / {formatStorageSize(host.resource_usage.storage_total_gb)}
                            </span>
                          </div>
                          <div className="h-2 bg-stone-200 dark:bg-stone-700 rounded-full overflow-hidden">
                            <div className={`h-full ${getStorageColor(host.resource_usage.storage_percent)} transition-all`} style={{ width: `${Math.min(host.resource_usage.storage_percent, 100)}%` }}></div>
                          </div>
                        </div>
                      </div>

                      {/* Containers */}
                      <div className="flex items-center gap-4 text-xs text-stone-600 dark:text-stone-400 mb-4 py-2 border-t border-stone-100 dark:border-stone-800">
                        <span className="flex items-center gap-1.5">
                          <i className="fa-solid fa-cube text-stone-400"></i>
                          <strong>{host.resource_usage.containers_running}</strong>/{host.resource_usage.containers_total} containers
                        </span>
                        {host.capabilities.providers && host.capabilities.providers.length > 0 && (
                          <span className="flex items-center gap-1 text-[10px] px-1.5 py-0.5 bg-stone-100 dark:bg-stone-800 rounded">
                            {host.capabilities.providers.join(', ')}
                          </span>
                        )}
                      </div>

                      {/* Image Sync Strategy */}
                      <div className="py-2 border-t border-stone-100 dark:border-stone-800">
                        <div className="flex items-center justify-between">
                          <span className="text-xs text-stone-500 dark:text-stone-400">
                            <i className="fa-solid fa-sync mr-1.5"></i>
                            Image Sync
                          </span>
                          <select
                            value={host.image_sync_strategy || 'on_demand'}
                            onChange={(e) => updateSyncStrategy(host.id, e.target.value as SyncStrategy)}
                            disabled={host.status !== 'online'}
                            className="text-xs bg-stone-100 dark:bg-stone-800 border border-stone-200 dark:border-stone-700 rounded px-2 py-1 text-stone-700 dark:text-stone-300 disabled:opacity-50 disabled:cursor-not-allowed focus:outline-none focus:ring-1 focus:ring-sage-500"
                          >
                            {SYNC_STRATEGY_OPTIONS.map(opt => (
                              <option key={opt.value} value={opt.value}>
                                {opt.label}
                              </option>
                            ))}
                          </select>
                        </div>
                        <p className="text-[10px] text-stone-400 mt-1">
                          {SYNC_STRATEGY_OPTIONS.find(o => o.value === (host.image_sync_strategy || 'on_demand'))?.description}
                        </p>
                      </div>

                      {/* Labs */}
                      {host.labs.length > 0 && (
                        <div className="pt-2 border-t border-stone-100 dark:border-stone-800">
                          <div className="flex items-center justify-between mb-2">
                            <span className="text-xs font-medium text-stone-500 dark:text-stone-400">
                              <i className="fa-solid fa-diagram-project mr-1.5"></i>
                              {host.lab_count} Lab{host.lab_count !== 1 ? 's' : ''}
                            </span>
                            {hasMultipleLabs && (
                              <button
                                onClick={() => toggleLabsExpanded(host.id)}
                                className="text-[10px] text-sage-600 dark:text-sage-400 hover:underline"
                              >
                                {isExpanded ? 'Show less' : `Show all ${host.labs.length}`}
                              </button>
                            )}
                          </div>
                          <div className="space-y-1">
                            {(isExpanded ? host.labs : host.labs.slice(0, 3)).map((lab) => (
                              <div
                                key={lab.id}
                                className="flex items-center justify-between text-xs py-1 px-2 bg-stone-50 dark:bg-stone-800/50 rounded"
                              >
                                <span className="text-stone-700 dark:text-stone-300 truncate max-w-[150px]">{lab.name}</span>
                                <span className={`px-1.5 py-0.5 rounded text-[9px] font-medium uppercase ${
                                  lab.state === 'running' ? 'bg-green-100 dark:bg-green-900/30 text-green-600 dark:text-green-400' :
                                  lab.state === 'starting' ? 'bg-amber-100 dark:bg-amber-900/30 text-amber-600 dark:text-amber-400' :
                                  'bg-stone-200 dark:bg-stone-700 text-stone-500 dark:text-stone-400'
                                }`}>
                                  {lab.state}
                                </span>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </main>

        <footer className="h-10 border-t border-stone-200 dark:border-stone-900 bg-stone-100 dark:bg-stone-950 flex items-center px-10 justify-between text-[10px] text-stone-500 dark:text-stone-600 font-medium">
          <span>Archetype Infrastructure Management</span>
          <span>Auto-refresh: 10s</span>
        </footer>
      </div>

      <ThemeSelector
        isOpen={showThemeSelector}
        onClose={() => setShowThemeSelector(false)}
      />
    </>
  );
};

export default HostsPage;
