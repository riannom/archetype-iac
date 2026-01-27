import React, { useCallback, useEffect, useState } from 'react';
import { Navigate, useNavigate } from 'react-router-dom';
import { useTheme, ThemeSelector } from '../theme/index';
import { useUser } from '../contexts/UserContext';
import { apiRequest } from '../api';
import { ArchetypeIcon } from '../components/icons';

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
  capabilities: {
    providers?: string[];
    features?: string[];
    max_concurrent_jobs?: number;
  };
  resource_usage: {
    cpu_percent: number;
    memory_percent: number;
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

const HostsPage: React.FC = () => {
  const { effectiveMode, toggleMode } = useTheme();
  const { user, loading: userLoading } = useUser();
  const navigate = useNavigate();
  const [hosts, setHosts] = useState<HostDetailed[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showThemeSelector, setShowThemeSelector] = useState(false);
  const [expandedLabs, setExpandedLabs] = useState<Set<string>>(new Set());

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
    const interval = setInterval(loadHosts, 10000);
    return () => clearInterval(interval);
  }, [loadHosts]);

  // Redirect non-admins
  if (!userLoading && user && !user.is_admin) {
    return <Navigate to="/" replace />;
  }

  if (!userLoading && !user) {
    return <Navigate to="/" replace />;
  }

  const getStatusColor = (status: string) => {
    if (status === 'online') return 'bg-green-500';
    if (status === 'degraded') return 'bg-amber-500';
    return 'bg-red-500';
  };

  const getStatusText = (status: string) => {
    if (status === 'online') return 'Online';
    if (status === 'degraded') return 'Degraded';
    return 'Offline';
  };

  const getRoleBadgeColor = (role: string) => {
    if (role === 'controller') return 'bg-purple-100 dark:bg-purple-900/30 text-purple-600 dark:text-purple-400 border-purple-200 dark:border-purple-700';
    if (role === 'agent+controller') return 'bg-blue-100 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 border-blue-200 dark:border-blue-700';
    return 'bg-sage-100 dark:bg-sage-900/30 text-sage-600 dark:text-sage-400 border-sage-200 dark:border-sage-700';
  };

  const getRoleLabel = (role: string) => {
    if (role === 'controller') return 'Controller';
    if (role === 'agent+controller') return 'Agent + Controller';
    return 'Agent';
  };

  const getCpuColor = (percent: number) => {
    if (percent >= 80) return 'bg-red-500';
    if (percent >= 60) return 'bg-amber-500';
    return 'bg-sage-500';
  };

  const getMemoryColor = (percent: number) => {
    if (percent >= 85) return 'bg-red-500';
    if (percent >= 70) return 'bg-amber-500';
    return 'bg-blue-500';
  };

  const getStorageColor = (percent: number) => {
    if (percent >= 90) return 'bg-red-500';
    if (percent >= 75) return 'bg-amber-500';
    return 'bg-violet-500';
  };

  const formatSize = (gb: number) => {
    if (gb >= 1000) {
      return `${(gb / 1000).toFixed(1)}TB`;
    }
    return `${gb.toFixed(1)}GB`;
  };

  const formatTimestamp = (ts: string | null) => {
    if (!ts) return 'Never';
    const date = new Date(ts);
    const now = new Date();
    const diff = now.getTime() - date.getTime();
    const seconds = Math.floor(diff / 1000);
    if (seconds < 60) return `${seconds}s ago`;
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes}m ago`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours}h ago`;
    return date.toLocaleDateString();
  };

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
                </p>
              </div>
              <div className="flex items-center gap-4 text-sm text-stone-600 dark:text-stone-400">
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
                          <div className={`w-3 h-3 rounded-full ${getStatusColor(host.status)} ${host.status === 'online' ? 'animate-pulse' : ''}`}></div>
                          <div>
                            <h3 className="font-bold text-stone-900 dark:text-white">{host.name}</h3>
                            <p className="text-xs text-stone-500">{host.address}</p>
                          </div>
                        </div>
                        <span className={`px-2 py-0.5 rounded-md text-[10px] font-bold uppercase border ${getRoleBadgeColor(host.role)}`}>
                          {getRoleLabel(host.role)}
                        </span>
                      </div>

                      {/* Status & Version */}
                      <div className="flex items-center gap-4 text-xs text-stone-500 dark:text-stone-400 mb-4">
                        <span className="flex items-center gap-1">
                          <i className="fa-solid fa-circle text-[8px]" style={{ color: host.status === 'online' ? '#22c55e' : '#ef4444' }}></i>
                          {getStatusText(host.status)}
                        </span>
                        <span>v{host.version}</span>
                        <span className="text-stone-400">
                          <i className="fa-regular fa-clock mr-1"></i>
                          {formatTimestamp(host.last_heartbeat)}
                        </span>
                      </div>

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
                            <span className="font-medium text-stone-700 dark:text-stone-300">{host.resource_usage.memory_percent.toFixed(0)}%</span>
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
                              {formatSize(host.resource_usage.storage_used_gb)} / {formatSize(host.resource_usage.storage_total_gb)}
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
