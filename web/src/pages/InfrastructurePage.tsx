import React, { useCallback, useEffect, useState } from 'react';
import { Navigate, useNavigate, useSearchParams } from 'react-router-dom';
import { useTheme, ThemeSelector } from '../theme/index';
import { useUser } from '../contexts/UserContext';
import { apiRequest } from '../api';
import { ArchetypeIcon } from '../components/icons';
import { formatStorageSize, formatTimestamp, formatUptimeFromBoot } from '../utils/format';
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

// ============================================================================
// Types
// ============================================================================

interface InfraSettings {
  overlay_mtu: number;
  mtu_verification_enabled: boolean;
  updated_at: string | null;
  updated_by_id: string | null;
}

interface AgentMeshNode {
  id: string;
  name: string;
  address: string;
  status: string;
}

interface AgentLinkOut {
  id: string;
  source_agent_id: string;
  source_agent_name: string | null;
  target_agent_id: string;
  target_agent_name: string | null;
  link_type: string;
  configured_mtu: number;
  tested_mtu: number | null;
  last_test_at: string | null;
  test_status: string;
  test_error: string | null;
  latency_ms: number | null;
}

interface AgentMeshResponse {
  agents: AgentMeshNode[];
  links: AgentLinkOut[];
  settings: InfraSettings;
}

interface MtuTestResponse {
  success: boolean;
  source_agent_id: string;
  target_agent_id: string;
  configured_mtu: number;
  tested_mtu: number | null;
  link_type: string | null;
  latency_ms: number | null;
  error: string | null;
}

interface MtuTestAllResponse {
  total_pairs: number;
  successful: number;
  failed: number;
  results: MtuTestResponse[];
}

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
  started_at: string | null;
  last_heartbeat: string | null;
  last_error: string | null;
  error_since: string | null;
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
type TabType = 'hosts' | 'settings';

const SYNC_STRATEGY_OPTIONS: { value: SyncStrategy; label: string; description: string }[] = [
  { value: 'on_demand', label: 'On Demand', description: 'Sync when deployment needs image' },
  { value: 'push', label: 'Push', description: 'Receive images on upload' },
  { value: 'pull', label: 'Pull', description: 'Pull images when online' },
  { value: 'disabled', label: 'Disabled', description: 'Manual sync only' },
];

// ============================================================================
// Component
// ============================================================================

const InfrastructurePage: React.FC = () => {
  const { effectiveMode, toggleMode } = useTheme();
  const { user, loading: userLoading } = useUser();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  // Tab state from URL
  const activeTab = (searchParams.get('tab') as TabType) || 'hosts';
  const setActiveTab = (tab: TabType) => {
    setSearchParams({ tab });
  };

  // Settings/Mesh state
  const [mesh, setMesh] = useState<AgentMeshResponse | null>(null);
  const [meshLoading, setMeshLoading] = useState(true);
  const [meshError, setMeshError] = useState<string | null>(null);
  const [showThemeSelector, setShowThemeSelector] = useState(false);

  // Settings form state
  const [mtuValue, setMtuValue] = useState<number>(1450);
  const [verificationEnabled, setVerificationEnabled] = useState<boolean>(true);
  const [savingSettings, setSavingSettings] = useState(false);
  const [settingsDirty, setSettingsDirty] = useState(false);

  // Testing state
  const [testingAll, setTestingAll] = useState(false);
  const [testingLink, setTestingLink] = useState<string | null>(null);

  // Hosts state
  const [hosts, setHosts] = useState<HostDetailed[]>([]);
  const [hostsLoading, setHostsLoading] = useState(true);
  const [hostsError, setHostsError] = useState<string | null>(null);
  const [expandedLabs, setExpandedLabs] = useState<Set<string>>(new Set());
  const [latestVersion, setLatestVersion] = useState<string>('');
  const [updatingAgents, setUpdatingAgents] = useState<Set<string>>(new Set());
  const [updateStatuses, setUpdateStatuses] = useState<Map<string, UpdateStatus>>(new Map());

  // ============================================================================
  // Data Loading
  // ============================================================================

  const loadMesh = useCallback(async () => {
    try {
      const data = await apiRequest<AgentMeshResponse>('/infrastructure/mesh');
      setMesh(data);
      setMtuValue(data.settings.overlay_mtu);
      setVerificationEnabled(data.settings.mtu_verification_enabled);
      setSettingsDirty(false);
      setMeshError(null);
    } catch (err) {
      setMeshError(err instanceof Error ? err.message : 'Failed to load infrastructure data');
    } finally {
      setMeshLoading(false);
    }
  }, []);

  const loadHosts = useCallback(async () => {
    try {
      const data = await apiRequest<HostDetailed[]>('/agents/detailed');
      setHosts(data);
      setHostsError(null);
    } catch (err) {
      setHostsError(err instanceof Error ? err.message : 'Failed to load hosts');
    } finally {
      setHostsLoading(false);
    }
  }, []);

  const loadLatestVersion = useCallback(async () => {
    try {
      const data = await apiRequest<{ version: string }>('/agents/updates/latest');
      setLatestVersion(data.version);
    } catch (err) {
      console.error('Failed to load latest version:', err);
    }
  }, []);

  useEffect(() => {
    loadMesh();
    loadHosts();
    loadLatestVersion();
    const meshInterval = setInterval(loadMesh, 30000);
    const hostsInterval = setInterval(loadHosts, 10000);
    return () => {
      clearInterval(meshInterval);
      clearInterval(hostsInterval);
    };
  }, [loadMesh, loadHosts, loadLatestVersion]);

  // Poll update status for agents being updated
  useEffect(() => {
    if (updatingAgents.size === 0) return;

    const pollInterval = setInterval(async () => {
      for (const agentId of updatingAgents) {
        try {
          const status = await apiRequest<UpdateStatus | null>(`/agents/${agentId}/update-status`);
          if (status) {
            if (status.status === 'restarting') {
              const host = hosts.find(h => h.id === agentId);
              if (host && host.status === 'online' && host.version === status.to_version) {
                setUpdateStatuses(prev => new Map(prev).set(agentId, {
                  ...status,
                  status: 'completed',
                  progress_percent: 100
                }));
                setUpdatingAgents(prev => {
                  const next = new Set(prev);
                  next.delete(agentId);
                  return next;
                });
                loadHosts();
                continue;
              }
            }

            setUpdateStatuses(prev => new Map(prev).set(agentId, status));

            if (status.status === 'completed' || status.status === 'failed') {
              setUpdatingAgents(prev => {
                const next = new Set(prev);
                next.delete(agentId);
                return next;
              });
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
  }, [updatingAgents, loadHosts, hosts]);

  // ============================================================================
  // Auth Check
  // ============================================================================

  if (!userLoading && user && !user.is_admin) {
    return <Navigate to="/" replace />;
  }

  if (!userLoading && !user) {
    return <Navigate to="/" replace />;
  }

  // ============================================================================
  // Settings Handlers
  // ============================================================================

  const handleMtuChange = (value: number) => {
    setMtuValue(value);
    setSettingsDirty(value !== mesh?.settings.overlay_mtu || verificationEnabled !== mesh?.settings.mtu_verification_enabled);
  };

  const handleVerificationChange = (value: boolean) => {
    setVerificationEnabled(value);
    setSettingsDirty(mtuValue !== mesh?.settings.overlay_mtu || value !== mesh?.settings.mtu_verification_enabled);
  };

  const saveSettings = async () => {
    setSavingSettings(true);
    try {
      await apiRequest('/infrastructure/settings', {
        method: 'PATCH',
        body: JSON.stringify({
          overlay_mtu: mtuValue,
          mtu_verification_enabled: verificationEnabled,
        }),
      });
      setSettingsDirty(false);
      await loadMesh();
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Failed to save settings');
    } finally {
      setSavingSettings(false);
    }
  };

  const testAllLinks = async () => {
    setTestingAll(true);
    try {
      const result = await apiRequest<MtuTestAllResponse>('/infrastructure/mesh/test-all', {
        method: 'POST',
      });
      await loadMesh();
      if (result.failed > 0) {
        alert(`MTU tests completed: ${result.successful} passed, ${result.failed} failed`);
      }
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Failed to run MTU tests');
    } finally {
      setTestingAll(false);
    }
  };

  const testLink = async (sourceId: string, targetId: string) => {
    const linkKey = `${sourceId}-${targetId}`;
    setTestingLink(linkKey);
    try {
      await apiRequest<MtuTestResponse>('/infrastructure/mesh/test-mtu', {
        method: 'POST',
        body: JSON.stringify({
          source_agent_id: sourceId,
          target_agent_id: targetId,
        }),
      });
      await loadMesh();
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Failed to test link');
    } finally {
      setTestingLink(null);
    }
  };

  // ============================================================================
  // Host Handlers
  // ============================================================================

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

  const triggerRebuild = async (hostId: string) => {
    if (!confirm('Rebuild the agent container? This will restart the agent with the latest code.')) {
      return;
    }

    try {
      setUpdatingAgents(prev => new Set(prev).add(hostId));
      const response = await apiRequest<{ success: boolean; message: string; output?: string }>(
        `/agents/${hostId}/rebuild`,
        { method: 'POST' }
      );

      if (response.success) {
        setTimeout(() => {
          setUpdatingAgents(prev => {
            const next = new Set(prev);
            next.delete(hostId);
            return next;
          });
          loadHosts();
        }, 5000);
      } else {
        alert(response.message || 'Rebuild failed');
        setUpdatingAgents(prev => {
          const next = new Set(prev);
          next.delete(hostId);
          return next;
        });
      }
    } catch (err) {
      console.error('Failed to trigger rebuild:', err);
      alert(err instanceof Error ? err.message : 'Failed to trigger rebuild');
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

  // ============================================================================
  // Helpers
  // ============================================================================

  const getStatusBadgeStyle = (status: string): string => {
    switch (status) {
      case 'success':
        return 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400 border-green-300 dark:border-green-700';
      case 'failed':
        return 'bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-400 border-red-300 dark:border-red-700';
      case 'pending':
        return 'bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400 border-amber-300 dark:border-amber-700';
      default:
        return 'bg-stone-100 dark:bg-stone-800 text-stone-600 dark:text-stone-400 border-stone-300 dark:border-stone-700';
    }
  };

  const getLinkTypeBadge = (linkType: string): string => {
    switch (linkType) {
      case 'direct':
        return 'bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400';
      case 'routed':
        return 'bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-400';
      default:
        return 'bg-stone-100 dark:bg-stone-800 text-stone-500 dark:text-stone-400';
    }
  };

  const outdatedCount = hosts.filter(
    h => h.status === 'online' && isUpdateAvailable(h)
  ).length;

  const refreshAll = () => {
    loadMesh();
    loadHosts();
  };

  // ============================================================================
  // Render
  // ============================================================================

  return (
    <>
      <div className="min-h-screen bg-stone-50 dark:bg-stone-900 flex flex-col overflow-hidden">
        <header className="h-20 border-b border-stone-200 dark:border-stone-800 bg-white/30 dark:bg-stone-900/30 flex items-center justify-between px-10">
          <div className="flex items-center gap-4">
            <ArchetypeIcon size={40} className="text-sage-600 dark:text-sage-400" />
            <div>
              <h1 className="text-xl font-black text-stone-900 dark:text-white tracking-tight">ARCHETYPE</h1>
              <p className="text-[10px] text-sage-600 dark:text-sage-500 font-bold uppercase tracking-widest">Infrastructure</p>
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
              onClick={refreshAll}
              className="flex items-center gap-2 px-3 py-2 bg-stone-100 dark:bg-stone-800 hover:bg-stone-200 dark:hover:bg-stone-700 text-stone-600 dark:text-stone-300 border border-stone-300 dark:border-stone-700 rounded-lg transition-all"
            >
              <i className="fa-solid fa-rotate text-xs"></i>
              <span className="text-[10px] font-bold uppercase">Refresh</span>
            </button>
          </div>
        </header>

        {/* Tab Bar */}
        <div className="border-b border-stone-200 dark:border-stone-800 bg-white dark:bg-stone-900 px-10">
          <div className="flex gap-1">
            <button
              onClick={() => setActiveTab('hosts')}
              className={`px-4 py-3 text-sm font-medium border-b-2 transition-all ${
                activeTab === 'hosts'
                  ? 'text-sage-600 dark:text-sage-400 border-sage-600 dark:border-sage-400'
                  : 'text-stone-500 dark:text-stone-400 border-transparent hover:text-stone-700 dark:hover:text-stone-300'
              }`}
            >
              <i className="fa-solid fa-server mr-2"></i>
              Hosts
              <span className="ml-2 px-1.5 py-0.5 text-xs rounded-full bg-stone-200 dark:bg-stone-700">
                {hosts.length}
              </span>
            </button>
            <button
              onClick={() => setActiveTab('settings')}
              className={`px-4 py-3 text-sm font-medium border-b-2 transition-all ${
                activeTab === 'settings'
                  ? 'text-sage-600 dark:text-sage-400 border-sage-600 dark:border-sage-400'
                  : 'text-stone-500 dark:text-stone-400 border-transparent hover:text-stone-700 dark:hover:text-stone-300'
              }`}
            >
              <i className="fa-solid fa-sliders mr-2"></i>
              Settings & Mesh
            </button>
          </div>
        </div>

        <main className="flex-1 overflow-y-auto p-10 custom-scrollbar">
          {activeTab === 'hosts' ? (
            // ================================================================
            // HOSTS TAB
            // ================================================================
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

              {hostsLoading && hosts.length === 0 ? (
                <div className="flex items-center justify-center py-20">
                  <i className="fa-solid fa-spinner fa-spin text-stone-400 text-2xl"></i>
                  <span className="ml-3 text-stone-500">Loading hosts...</span>
                </div>
              ) : hostsError ? (
                <div className="text-center py-20 text-red-500">
                  <i className="fa-solid fa-exclamation-circle text-3xl mb-3"></i>
                  <p>{hostsError}</p>
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
                          {host.started_at && (
                            <span className="text-stone-400 font-mono" title="System uptime">
                              <i className="fa-solid fa-clock mr-1"></i>
                              {formatUptimeFromBoot(host.started_at)}
                            </span>
                          )}
                          <span className="text-stone-400">
                            <i className="fa-regular fa-clock mr-1"></i>
                            {formatTimestamp(host.last_heartbeat)}
                          </span>
                        </div>

                        {/* Error Alert */}
                        {host.last_error && (
                          <div className="mb-4 p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg">
                            <div className="flex items-start gap-2">
                              <i className="fa-solid fa-triangle-exclamation text-red-500 dark:text-red-400 mt-0.5 flex-shrink-0"></i>
                              <div className="flex-1 min-w-0">
                                <div className="text-xs font-semibold text-red-700 dark:text-red-300 mb-1">
                                  Agent Error
                                  {host.error_since && (
                                    <span className="font-normal text-red-500 dark:text-red-400 ml-1">
                                      (since {formatTimestamp(host.error_since)})
                                    </span>
                                  )}
                                </div>
                                <div className="text-xs text-red-600 dark:text-red-400 break-words">
                                  {host.last_error}
                                </div>
                              </div>
                            </div>
                          </div>
                        )}

                        {/* Update Progress or Button */}
                        {updatingAgents.has(host.id) ? (
                          <div className="mb-4 p-2 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg">
                            <div className="flex items-center justify-between text-xs mb-1">
                              <span className="text-blue-700 dark:text-blue-300 font-medium">
                                <i className="fa-solid fa-spinner fa-spin mr-1.5"></i>
                                {host.deployment_mode === 'docker' ? 'Rebuilding...' :
                                  updateStatuses.get(host.id)?.status === 'downloading' ? 'Downloading...' :
                                  updateStatuses.get(host.id)?.status === 'installing' ? 'Installing...' :
                                  updateStatuses.get(host.id)?.status === 'restarting' ? 'Restarting...' :
                                  'Starting update...'}
                              </span>
                              {host.deployment_mode !== 'docker' && (
                                <span className="text-blue-600 dark:text-blue-400">
                                  {updateStatuses.get(host.id)?.progress_percent || 0}%
                                </span>
                              )}
                            </div>
                            {host.deployment_mode !== 'docker' && (
                              <div className="h-1.5 bg-blue-200 dark:bg-blue-800 rounded-full overflow-hidden">
                                <div
                                  className="h-full bg-blue-500 transition-all"
                                  style={{ width: `${updateStatuses.get(host.id)?.progress_percent || 0}%` }}
                                ></div>
                              </div>
                            )}
                          </div>
                        ) : isUpdateAvailable(host) && host.status === 'online' ? (
                          <div className="mb-4">
                            {host.deployment_mode === 'docker' ? (
                              <button
                                onClick={() => triggerRebuild(host.id)}
                                className="w-full flex items-center justify-center gap-2 px-3 py-2 bg-purple-100 dark:bg-purple-900/30 hover:bg-purple-200 dark:hover:bg-purple-900/50 text-purple-700 dark:text-purple-400 border border-purple-300 dark:border-purple-700 rounded-lg transition-all text-xs font-medium"
                              >
                                <i className="fa-solid fa-rotate"></i>
                                Rebuild Container
                              </button>
                            ) : (
                              <button
                                onClick={() => triggerUpdate(host.id)}
                                className="w-full flex items-center justify-center gap-2 px-3 py-2 bg-amber-100 dark:bg-amber-900/30 hover:bg-amber-200 dark:hover:bg-amber-900/50 text-amber-700 dark:text-amber-400 border border-amber-300 dark:border-amber-700 rounded-lg transition-all text-xs font-medium"
                              >
                                <i className="fa-solid fa-download"></i>
                                Update to v{latestVersion}
                              </button>
                            )}
                          </div>
                        ) : null}

                        {/* Resource Bars */}
                        <div className="space-y-3 mb-4">
                          <div>
                            <div className="flex justify-between text-xs mb-1">
                              <span className="text-stone-500 dark:text-stone-400">CPU</span>
                              <span className="font-medium text-stone-700 dark:text-stone-300">{host.resource_usage.cpu_percent.toFixed(0)}%</span>
                            </div>
                            <div className="h-2 bg-stone-200 dark:bg-stone-700 rounded-full overflow-hidden">
                              <div className={`h-full ${getCpuColor(host.resource_usage.cpu_percent)} transition-all`} style={{ width: `${Math.min(host.resource_usage.cpu_percent, 100)}%` }}></div>
                            </div>
                          </div>

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
          ) : (
            // ================================================================
            // SETTINGS TAB
            // ================================================================
            <div className="max-w-6xl mx-auto">
              {meshLoading && !mesh ? (
                <div className="flex items-center justify-center py-20">
                  <i className="fa-solid fa-spinner fa-spin text-stone-400 text-2xl"></i>
                  <span className="ml-3 text-stone-500">Loading infrastructure data...</span>
                </div>
              ) : meshError ? (
                <div className="text-center py-20 text-red-500">
                  <i className="fa-solid fa-exclamation-circle text-3xl mb-3"></i>
                  <p>{meshError}</p>
                </div>
              ) : (
                <div className="space-y-8">
                  {/* Settings Panel */}
                  <div className="bg-white dark:bg-stone-900 border border-stone-200 dark:border-stone-800 rounded-2xl p-6">
                    <h2 className="text-lg font-bold text-stone-900 dark:text-white mb-4 flex items-center gap-2">
                      <i className="fa-solid fa-sliders text-sage-600 dark:text-sage-400"></i>
                      Global Settings
                    </h2>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                      <div>
                        <label className="block text-sm font-medium text-stone-700 dark:text-stone-300 mb-2">
                          Overlay MTU
                          <span className="text-stone-400 font-normal ml-2">(VXLAN tunnel MTU)</span>
                        </label>
                        <div className="flex items-center gap-3">
                          <input
                            type="number"
                            min={68}
                            max={9000}
                            value={mtuValue}
                            onChange={(e) => handleMtuChange(parseInt(e.target.value) || 1450)}
                            className="w-32 px-3 py-2 bg-stone-100 dark:bg-stone-800 border border-stone-300 dark:border-stone-700 rounded-lg text-stone-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-sage-500"
                          />
                          <span className="text-xs text-stone-500">bytes (68-9000)</span>
                        </div>
                        <p className="text-xs text-stone-400 mt-1">
                          Default is 1450 to account for ~50 byte VXLAN overhead on 1500 MTU networks.
                          Use 8950 for jumbo frame networks.
                        </p>
                      </div>

                      <div>
                        <label className="block text-sm font-medium text-stone-700 dark:text-stone-300 mb-2">
                          MTU Verification
                        </label>
                        <label className="flex items-center gap-3 cursor-pointer">
                          <input
                            type="checkbox"
                            checked={verificationEnabled}
                            onChange={(e) => handleVerificationChange(e.target.checked)}
                            className="w-5 h-5 rounded border-stone-300 dark:border-stone-600 text-sage-600 focus:ring-sage-500"
                          />
                          <span className="text-sm text-stone-600 dark:text-stone-400">
                            Enable automatic MTU verification between agents
                          </span>
                        </label>
                      </div>
                    </div>

                    <div className="mt-6 flex items-center gap-4">
                      <button
                        onClick={saveSettings}
                        disabled={!settingsDirty || savingSettings}
                        className={`flex items-center gap-2 px-4 py-2 rounded-lg transition-all text-sm font-medium ${
                          settingsDirty && !savingSettings
                            ? 'bg-sage-600 hover:bg-sage-700 text-white'
                            : 'bg-stone-200 dark:bg-stone-800 text-stone-400 cursor-not-allowed'
                        }`}
                      >
                        {savingSettings ? (
                          <i className="fa-solid fa-spinner fa-spin"></i>
                        ) : (
                          <i className="fa-solid fa-save"></i>
                        )}
                        Save Settings
                      </button>
                      {settingsDirty && (
                        <span className="text-xs text-amber-600 dark:text-amber-400">
                          <i className="fa-solid fa-circle-exclamation mr-1"></i>
                          Unsaved changes
                        </span>
                      )}
                      {mesh?.settings.updated_at && (
                        <span className="text-xs text-stone-400">
                          Last updated: {formatTimestamp(mesh.settings.updated_at)}
                        </span>
                      )}
                    </div>
                  </div>

                  {/* Agent Mesh */}
                  <div className="bg-white dark:bg-stone-900 border border-stone-200 dark:border-stone-800 rounded-2xl p-6">
                    <div className="flex items-center justify-between mb-6">
                      <h2 className="text-lg font-bold text-stone-900 dark:text-white flex items-center gap-2">
                        <i className="fa-solid fa-diagram-project text-sage-600 dark:text-sage-400"></i>
                        Agent Mesh
                      </h2>
                      <div className="flex items-center gap-3">
                        <div className="flex items-center gap-4 text-xs text-stone-500">
                          <div className="flex items-center gap-1.5">
                            <div className="w-3 h-3 rounded-full bg-green-500"></div>
                            <span>{mesh?.agents.filter(a => a.status === 'online').length} Online</span>
                          </div>
                          <div className="flex items-center gap-1.5">
                            <div className="w-3 h-3 rounded-full bg-red-500"></div>
                            <span>{mesh?.agents.filter(a => a.status !== 'online').length} Offline</span>
                          </div>
                        </div>
                        <button
                          onClick={testAllLinks}
                          disabled={testingAll || (mesh?.agents.filter(a => a.status === 'online').length ?? 0) < 2}
                          className={`flex items-center gap-2 px-3 py-1.5 rounded-lg transition-all text-xs font-medium ${
                            !testingAll && (mesh?.agents.filter(a => a.status === 'online').length ?? 0) >= 2
                              ? 'bg-blue-100 dark:bg-blue-900/30 hover:bg-blue-200 dark:hover:bg-blue-900/50 text-blue-700 dark:text-blue-400'
                              : 'bg-stone-200 dark:bg-stone-800 text-stone-400 cursor-not-allowed'
                          }`}
                        >
                          {testingAll ? (
                            <>
                              <i className="fa-solid fa-spinner fa-spin"></i>
                              Testing...
                            </>
                          ) : (
                            <>
                              <i className="fa-solid fa-network-wired"></i>
                              Test All
                            </>
                          )}
                        </button>
                      </div>
                    </div>

                    {mesh?.agents.length === 0 ? (
                      <div className="text-center py-12 text-stone-500">
                        <i className="fa-solid fa-server text-4xl mb-3 opacity-30"></i>
                        <p>No agents registered</p>
                      </div>
                    ) : mesh?.agents.length === 1 ? (
                      <div className="text-center py-12 text-stone-500">
                        <i className="fa-solid fa-link-slash text-4xl mb-3 opacity-30"></i>
                        <p>Only one agent registered. Add more agents to see mesh connectivity.</p>
                      </div>
                    ) : (
                      <>
                        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4 mb-6">
                          {mesh?.agents.map((agent) => (
                            <div
                              key={agent.id}
                              className="p-4 bg-stone-50 dark:bg-stone-800/50 border border-stone-200 dark:border-stone-700 rounded-xl"
                            >
                              <div className="flex items-center gap-2 mb-2">
                                <div className={`w-3 h-3 rounded-full ${agent.status === 'online' ? 'bg-green-500 animate-pulse' : 'bg-red-500'}`}></div>
                                <span className="font-medium text-stone-900 dark:text-white truncate">{agent.name}</span>
                              </div>
                              <p className="text-xs text-stone-500 truncate">{agent.address}</p>
                            </div>
                          ))}
                        </div>

                        <h3 className="text-sm font-semibold text-stone-700 dark:text-stone-300 mb-3">Link Details</h3>
                        <div className="overflow-x-auto">
                          <table className="w-full text-sm">
                            <thead>
                              <tr className="border-b border-stone-200 dark:border-stone-700">
                                <th className="text-left py-2 px-3 font-medium text-stone-500 dark:text-stone-400">Path</th>
                                <th className="text-left py-2 px-3 font-medium text-stone-500 dark:text-stone-400">Type</th>
                                <th className="text-left py-2 px-3 font-medium text-stone-500 dark:text-stone-400">MTU</th>
                                <th className="text-left py-2 px-3 font-medium text-stone-500 dark:text-stone-400">Latency</th>
                                <th className="text-left py-2 px-3 font-medium text-stone-500 dark:text-stone-400">Status</th>
                                <th className="text-left py-2 px-3 font-medium text-stone-500 dark:text-stone-400">Last Test</th>
                                <th className="text-right py-2 px-3 font-medium text-stone-500 dark:text-stone-400">Action</th>
                              </tr>
                            </thead>
                            <tbody>
                              {mesh?.links.map((link) => {
                                const linkKey = `${link.source_agent_id}-${link.target_agent_id}`;
                                const isTesting = testingLink === linkKey;

                                return (
                                  <tr key={link.id} className="border-b border-stone-100 dark:border-stone-800 hover:bg-stone-50 dark:hover:bg-stone-800/30">
                                    <td className="py-2 px-3">
                                      <div className="flex items-center gap-2">
                                        <span className="font-medium text-stone-700 dark:text-stone-300">{link.source_agent_name}</span>
                                        <i className="fa-solid fa-arrow-right text-stone-400 text-xs"></i>
                                        <span className="font-medium text-stone-700 dark:text-stone-300">{link.target_agent_name}</span>
                                      </div>
                                    </td>
                                    <td className="py-2 px-3">
                                      <span className={`px-2 py-0.5 rounded text-xs font-medium ${getLinkTypeBadge(link.link_type)}`}>
                                        {link.link_type}
                                      </span>
                                    </td>
                                    <td className="py-2 px-3 text-stone-600 dark:text-stone-400">
                                      {link.tested_mtu ? (
                                        <span className={link.tested_mtu >= link.configured_mtu ? 'text-green-600 dark:text-green-400' : 'text-amber-600 dark:text-amber-400'}>
                                          {link.tested_mtu}
                                        </span>
                                      ) : (
                                        <span className="text-stone-400">-</span>
                                      )}
                                      <span className="text-stone-400 text-xs ml-1">/ {link.configured_mtu}</span>
                                    </td>
                                    <td className="py-2 px-3 text-stone-600 dark:text-stone-400">
                                      {link.latency_ms !== null ? (
                                        <span>{link.latency_ms.toFixed(2)} ms</span>
                                      ) : (
                                        <span className="text-stone-400">-</span>
                                      )}
                                    </td>
                                    <td className="py-2 px-3">
                                      <span className={`px-2 py-0.5 rounded text-xs font-medium border ${getStatusBadgeStyle(link.test_status)}`}>
                                        {link.test_status}
                                      </span>
                                      {link.test_error && (
                                        <span className="ml-2 text-xs text-red-500" title={link.test_error}>
                                          <i className="fa-solid fa-circle-exclamation"></i>
                                        </span>
                                      )}
                                    </td>
                                    <td className="py-2 px-3 text-xs text-stone-400">
                                      {link.last_test_at ? formatTimestamp(link.last_test_at) : '-'}
                                    </td>
                                    <td className="py-2 px-3 text-right">
                                      <button
                                        onClick={() => testLink(link.source_agent_id, link.target_agent_id)}
                                        disabled={isTesting || testingAll}
                                        className={`px-2 py-1 rounded text-xs font-medium transition-all ${
                                          !isTesting && !testingAll
                                            ? 'bg-stone-100 dark:bg-stone-800 hover:bg-stone-200 dark:hover:bg-stone-700 text-stone-600 dark:text-stone-400'
                                            : 'bg-stone-100 dark:bg-stone-800 text-stone-400 cursor-not-allowed'
                                        }`}
                                      >
                                        {isTesting ? (
                                          <i className="fa-solid fa-spinner fa-spin"></i>
                                        ) : (
                                          <i className="fa-solid fa-play"></i>
                                        )}
                                      </button>
                                    </td>
                                  </tr>
                                );
                              })}
                            </tbody>
                          </table>
                        </div>

                        {mesh?.links.length === 0 && (
                          <div className="text-center py-8 text-stone-500">
                            <p className="text-sm">No links to display. Links are created automatically when agents are registered.</p>
                          </div>
                        )}
                      </>
                    )}
                  </div>
                </div>
              )}
            </div>
          )}
        </main>

        <footer className="h-10 border-t border-stone-200 dark:border-stone-900 bg-stone-100 dark:bg-stone-950 flex items-center px-10 justify-between text-[10px] text-stone-500 dark:text-stone-600 font-medium">
          <span>Archetype Infrastructure Management</span>
          <span>Auto-refresh: {activeTab === 'hosts' ? '10s' : '30s'}</span>
        </footer>
      </div>

      <ThemeSelector
        isOpen={showThemeSelector}
        onClose={() => setShowThemeSelector(false)}
      />
    </>
  );
};

export default InfrastructurePage;
