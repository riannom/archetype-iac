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
  overlay_preserve_container_mtu: boolean;
  overlay_clamp_host_mtu: boolean;
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

interface InterfaceDetail {
  name: string;
  mtu: number;
  is_physical: boolean;
  is_default_route: boolean;
  mac: string | null;
  ipv4_addresses: string[];
  state: string;
}

interface InterfaceDetailsResponse {
  interfaces: InterfaceDetail[];
  default_route_interface: string | null;
  network_manager: string | null;
}

interface AgentNetworkConfig {
  id: string;
  host_id: string;
  host_name: string | null;
  data_plane_interface: string | null;
  desired_mtu: number;
  current_mtu: number | null;
  last_sync_at: string | null;
  sync_status: string;
  sync_error: string | null;
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
  const [preserveContainerMtu, setPreserveContainerMtu] = useState<boolean>(false);
  const [clampHostMtu, setClampHostMtu] = useState<boolean>(true);
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

  // Deregister state
  const [deregisterHost, setDeregisterHost] = useState<HostDetailed | null>(null);
  const [deregisterInfo, setDeregisterInfo] = useState<{
    agent_name: string;
    agent_status: string;
    labs_assigned: number;
    running_labs: { id: string; name: string; state: string }[];
    node_placements: number;
    nodes_assigned: number;
    vxlan_tunnels: number;
    cross_host_links: number;
  } | null>(null);
  const [deregisterLoading, setDeregisterLoading] = useState(false);
  const [deregistering, setDeregistering] = useState(false);

  // Host network config state
  const [networkConfigs, setNetworkConfigs] = useState<AgentNetworkConfig[]>([]);
  const [networkConfigsLoading, setNetworkConfigsLoading] = useState(false);
  const [configuringMtu, setConfiguringMtu] = useState<string | null>(null);  // agent ID being configured
  const [configModalData, setConfigModalData] = useState<{
    agentId: string;
    agentName: string;
    interfaces: InterfaceDetail[];
    defaultInterface: string | null;
    networkManager: string | null;
    currentConfig: AgentNetworkConfig | null;
  } | null>(null);
  const [selectedInterface, setSelectedInterface] = useState<string>('');
  const [desiredMtu, setDesiredMtu] = useState<number>(9000);
  const [savingMtuConfig, setSavingMtuConfig] = useState(false);

  // ============================================================================
  // Data Loading
  // ============================================================================

  const loadMesh = useCallback(async () => {
    try {
      const data = await apiRequest<AgentMeshResponse>('/infrastructure/mesh');
      setMesh(data);
      setMtuValue(data.settings.overlay_mtu);
      setVerificationEnabled(data.settings.mtu_verification_enabled);
      setPreserveContainerMtu(data.settings.overlay_preserve_container_mtu);
      setClampHostMtu(data.settings.overlay_clamp_host_mtu);
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

  const loadNetworkConfigs = useCallback(async () => {
    setNetworkConfigsLoading(true);
    try {
      const data = await apiRequest<AgentNetworkConfig[]>('/infrastructure/network-configs');
      setNetworkConfigs(data);
    } catch (err) {
      console.error('Failed to load network configs:', err);
    } finally {
      setNetworkConfigsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadMesh();
    loadHosts();
    loadLatestVersion();
    loadNetworkConfigs();
    const meshInterval = setInterval(loadMesh, 30000);
    const hostsInterval = setInterval(loadHosts, 10000);
    const networkConfigsInterval = setInterval(loadNetworkConfigs, 30000);
    return () => {
      clearInterval(meshInterval);
      clearInterval(hostsInterval);
      clearInterval(networkConfigsInterval);
    };
  }, [loadMesh, loadHosts, loadLatestVersion, loadNetworkConfigs]);

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
    setSettingsDirty(
      value !== mesh?.settings.overlay_mtu ||
      verificationEnabled !== mesh?.settings.mtu_verification_enabled ||
      preserveContainerMtu !== mesh?.settings.overlay_preserve_container_mtu ||
      clampHostMtu !== mesh?.settings.overlay_clamp_host_mtu
    );
  };

  const handleVerificationChange = (value: boolean) => {
    setVerificationEnabled(value);
    setSettingsDirty(
      mtuValue !== mesh?.settings.overlay_mtu ||
      value !== mesh?.settings.mtu_verification_enabled ||
      preserveContainerMtu !== mesh?.settings.overlay_preserve_container_mtu ||
      clampHostMtu !== mesh?.settings.overlay_clamp_host_mtu
    );
  };

  const handlePreserveContainerMtuChange = (value: boolean) => {
    setPreserveContainerMtu(value);
    setSettingsDirty(
      mtuValue !== mesh?.settings.overlay_mtu ||
      verificationEnabled !== mesh?.settings.mtu_verification_enabled ||
      value !== mesh?.settings.overlay_preserve_container_mtu ||
      clampHostMtu !== mesh?.settings.overlay_clamp_host_mtu
    );
  };

  const handleClampHostMtuChange = (value: boolean) => {
    setClampHostMtu(value);
    setSettingsDirty(
      mtuValue !== mesh?.settings.overlay_mtu ||
      verificationEnabled !== mesh?.settings.mtu_verification_enabled ||
      preserveContainerMtu !== mesh?.settings.overlay_preserve_container_mtu ||
      value !== mesh?.settings.overlay_clamp_host_mtu
    );
  };

  const saveSettings = async () => {
    setSavingSettings(true);
    try {
      await apiRequest('/infrastructure/settings', {
        method: 'PATCH',
        body: JSON.stringify({
          overlay_mtu: mtuValue,
          mtu_verification_enabled: verificationEnabled,
          overlay_preserve_container_mtu: preserveContainerMtu,
          overlay_clamp_host_mtu: clampHostMtu,
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
  // Deregister Handlers
  // ============================================================================

  const handleDeregisterClick = async (host: HostDetailed) => {
    setDeregisterHost(host);
    setDeregisterInfo(null);
    setDeregisterLoading(true);
    try {
      const info = await apiRequest<typeof deregisterInfo>(`/agents/${host.id}/deregister-info`);
      setDeregisterInfo(info);
    } catch (err) {
      console.error('Failed to load deregister info:', err);
      setDeregisterInfo({
        agent_name: host.name,
        agent_status: host.status,
        labs_assigned: host.labs.length,
        running_labs: host.labs.filter(l => l.state === 'running' || l.state === 'starting'),
        node_placements: 0,
        nodes_assigned: 0,
        vxlan_tunnels: 0,
        cross_host_links: 0,
      });
    } finally {
      setDeregisterLoading(false);
    }
  };

  const handleConfirmDeregister = async () => {
    if (!deregisterHost) return;
    setDeregistering(true);
    try {
      await apiRequest(`/agents/${deregisterHost.id}`, { method: 'DELETE' });
      setDeregisterHost(null);
      setDeregisterInfo(null);
      await loadHosts();
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Failed to deregister host');
    } finally {
      setDeregistering(false);
    }
  };

  const handleCancelDeregister = () => {
    setDeregisterHost(null);
    setDeregisterInfo(null);
  };

  // ============================================================================
  // MTU Configuration Handlers
  // ============================================================================

  const openMtuConfigModal = async (agentId: string) => {
    const host = hosts.find(h => h.id === agentId);
    if (!host || host.status !== 'online') {
      alert('Agent is offline');
      return;
    }

    setConfiguringMtu(agentId);
    try {
      const interfacesData = await apiRequest<InterfaceDetailsResponse>(
        `/infrastructure/agents/${agentId}/interfaces`
      );

      const existingConfig = networkConfigs.find(c => c.host_id === agentId);

      setConfigModalData({
        agentId,
        agentName: host.name,
        interfaces: interfacesData.interfaces.filter(i => i.is_physical),
        defaultInterface: interfacesData.default_route_interface,
        networkManager: interfacesData.network_manager,
        currentConfig: existingConfig || null,
      });

      // Pre-fill form with existing config or defaults
      if (existingConfig?.data_plane_interface) {
        setSelectedInterface(existingConfig.data_plane_interface);
        setDesiredMtu(existingConfig.desired_mtu);
      } else if (interfacesData.default_route_interface) {
        setSelectedInterface(interfacesData.default_route_interface);
        setDesiredMtu(9000);
      } else if (interfacesData.interfaces.length > 0) {
        const firstPhysical = interfacesData.interfaces.find(i => i.is_physical);
        setSelectedInterface(firstPhysical?.name || '');
        setDesiredMtu(9000);
      }
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Failed to load interface details');
    } finally {
      setConfiguringMtu(null);
    }
  };

  const closeMtuConfigModal = () => {
    setConfigModalData(null);
    setSelectedInterface('');
    setDesiredMtu(9000);
  };

  const saveMtuConfig = async () => {
    if (!configModalData || !selectedInterface) return;

    setSavingMtuConfig(true);
    try {
      await apiRequest(`/infrastructure/agents/${configModalData.agentId}/network-config`, {
        method: 'PATCH',
        body: JSON.stringify({
          data_plane_interface: selectedInterface,
          desired_mtu: desiredMtu,
        }),
      });
      await loadNetworkConfigs();
      closeMtuConfigModal();
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Failed to save MTU configuration');
    } finally {
      setSavingMtuConfig(false);
    }
  };

  const getMtuSyncStatusBadge = (status: string): { color: string; icon: string; text: string } => {
    switch (status) {
      case 'synced':
        return { color: 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400', icon: 'fa-check', text: 'Synced' };
      case 'mismatch':
        return { color: 'bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400', icon: 'fa-triangle-exclamation', text: 'Mismatch' };
      case 'error':
        return { color: 'bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-400', icon: 'fa-times-circle', text: 'Error' };
      case 'unconfigured':
        return { color: 'bg-stone-100 dark:bg-stone-800 text-stone-500 dark:text-stone-400', icon: 'fa-minus', text: 'Not Configured' };
      default:
        return { color: 'bg-stone-100 dark:bg-stone-800 text-stone-500 dark:text-stone-400', icon: 'fa-question', text: 'Unknown' };
    }
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
      <div className="min-h-screen bg-stone-50/72 dark:bg-stone-900/72 backdrop-blur-[1px] flex flex-col overflow-hidden">
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
                              <span className="text-stone-500 dark:text-stone-400 flex items-center gap-1">
                                CPU
                                {host.resource_usage.cpu_percent >= 95 && (
                                  <i className="fa-solid fa-circle-exclamation text-red-500" title="CPU critical - deployment may fail"></i>
                                )}
                                {host.resource_usage.cpu_percent >= 80 && host.resource_usage.cpu_percent < 95 && (
                                  <i className="fa-solid fa-triangle-exclamation text-amber-500" title="CPU high - consider distributing nodes across agents"></i>
                                )}
                              </span>
                              <span className="font-medium text-stone-700 dark:text-stone-300">{host.resource_usage.cpu_percent.toFixed(0)}%</span>
                            </div>
                            <div className="h-2 bg-stone-200 dark:bg-stone-700 rounded-full overflow-hidden">
                              <div className={`h-full ${getCpuColor(host.resource_usage.cpu_percent)} transition-all`} style={{ width: `${Math.min(host.resource_usage.cpu_percent, 100)}%` }}></div>
                            </div>
                          </div>

                          <div>
                            <div className="flex justify-between text-xs mb-1">
                              <span className="text-stone-500 dark:text-stone-400 flex items-center gap-1">
                                Memory
                                {host.resource_usage.memory_percent >= 95 && (
                                  <i className="fa-solid fa-circle-exclamation text-red-500" title="Memory critical - deployment will likely fail"></i>
                                )}
                                {host.resource_usage.memory_percent >= 80 && host.resource_usage.memory_percent < 95 && (
                                  <i className="fa-solid fa-triangle-exclamation text-amber-500" title="Memory high - consider distributing nodes across agents"></i>
                                )}
                              </span>
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
                              <span className="text-stone-500 dark:text-stone-400 flex items-center gap-1">
                                Storage
                                {host.resource_usage.storage_percent >= 95 && (
                                  <i className="fa-solid fa-circle-exclamation text-red-500" title="Storage critical - deployment will likely fail"></i>
                                )}
                                {host.resource_usage.storage_percent >= 85 && host.resource_usage.storage_percent < 95 && (
                                  <i className="fa-solid fa-triangle-exclamation text-amber-500" title="Storage high - consider freeing space or using another agent"></i>
                                )}
                              </span>
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

                        {/* Deregister */}
                        <div className="pt-3 mt-3 border-t border-stone-100 dark:border-stone-800">
                          <button
                            onClick={() => handleDeregisterClick(host)}
                            className="w-full flex items-center justify-center gap-2 px-3 py-1.5 text-xs font-medium text-stone-400 dark:text-stone-500 hover:text-red-600 dark:hover:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20 border border-transparent hover:border-red-200 dark:hover:border-red-800 rounded-lg transition-all"
                          >
                            <i className="fa-solid fa-link-slash"></i>
                            Deregister Host
                          </button>
                        </div>
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

                      <div>
                        <label className="block text-sm font-medium text-stone-700 dark:text-stone-300 mb-2">
                          Preserve Container MTU
                        </label>
                        <label className="flex items-center gap-3 cursor-pointer">
                          <input
                            type="checkbox"
                            checked={preserveContainerMtu}
                            onChange={(e) => handlePreserveContainerMtuChange(e.target.checked)}
                            className="w-5 h-5 rounded border-stone-300 dark:border-stone-600 text-sage-600 focus:ring-sage-500"
                          />
                          <span className="text-sm text-stone-600 dark:text-stone-400">
                            Keep container MTU unchanged on overlay links
                          </span>
                        </label>
                        <p className="text-xs text-stone-400 mt-1">
                          When enabled, containers keep their configured MTU and oversized packets may fragment.
                        </p>
                      </div>

                      <div>
                        <label className="block text-sm font-medium text-stone-700 dark:text-stone-300 mb-2">
                          Clamp Host MTU
                        </label>
                        <label className="flex items-center gap-3 cursor-pointer">
                          <input
                            type="checkbox"
                            checked={clampHostMtu}
                            onChange={(e) => handleClampHostMtuChange(e.target.checked)}
                            className="w-5 h-5 rounded border-stone-300 dark:border-stone-600 text-sage-600 focus:ring-sage-500"
                          />
                          <span className="text-sm text-stone-600 dark:text-stone-400">
                            Clamp host-side veth MTU to overlay MTU
                          </span>
                        </label>
                        <p className="text-xs text-stone-400 mt-1">
                          Recommended when preserving container MTU to avoid oversize frames on the host bridge.
                        </p>
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

                  {/* Host Network Configuration */}
                  <div className="bg-white dark:bg-stone-900 border border-stone-200 dark:border-stone-800 rounded-2xl p-6">
                    <div className="flex items-center justify-between mb-4">
                      <h2 className="text-lg font-bold text-stone-900 dark:text-white flex items-center gap-2">
                        <i className="fa-solid fa-ethernet text-sage-600 dark:text-sage-400"></i>
                        Host Network
                      </h2>
                      <button
                        onClick={loadNetworkConfigs}
                        disabled={networkConfigsLoading}
                        className="flex items-center gap-2 px-2 py-1 text-xs text-stone-500 hover:text-stone-700 dark:hover:text-stone-300 transition-colors"
                      >
                        <i className={`fa-solid fa-sync ${networkConfigsLoading ? 'fa-spin' : ''}`}></i>
                        Refresh
                      </button>
                    </div>

                    <p className="text-sm text-stone-500 dark:text-stone-400 mb-4">
                      Configure physical interface MTU on each agent host for optimal VXLAN overlay performance.
                      Jumbo frames (MTU 9000) are recommended for production deployments.
                    </p>

                    {hosts.length === 0 ? (
                      <div className="text-center py-8 text-stone-500">
                        <i className="fa-solid fa-server text-3xl mb-3 opacity-30"></i>
                        <p>No agents registered</p>
                      </div>
                    ) : (
                      <div className="overflow-x-auto">
                        <table className="w-full text-sm">
                          <thead>
                            <tr className="border-b border-stone-200 dark:border-stone-700">
                              <th className="text-left py-2 px-3 font-medium text-stone-500 dark:text-stone-400">Agent</th>
                              <th className="text-left py-2 px-3 font-medium text-stone-500 dark:text-stone-400">Interface</th>
                              <th className="text-left py-2 px-3 font-medium text-stone-500 dark:text-stone-400">Current MTU</th>
                              <th className="text-left py-2 px-3 font-medium text-stone-500 dark:text-stone-400">Desired MTU</th>
                              <th className="text-left py-2 px-3 font-medium text-stone-500 dark:text-stone-400">Status</th>
                              <th className="text-right py-2 px-3 font-medium text-stone-500 dark:text-stone-400">Action</th>
                            </tr>
                          </thead>
                          <tbody>
                            {hosts.map((host) => {
                              const config = networkConfigs.find(c => c.host_id === host.id);
                              const statusBadge = getMtuSyncStatusBadge(config?.sync_status || 'unconfigured');
                              const isLoading = configuringMtu === host.id;
                              const needsAttention = config?.sync_status === 'mismatch' || config?.sync_status === 'error';

                              return (
                                <tr
                                  key={host.id}
                                  className={`border-b border-stone-100 dark:border-stone-800 hover:bg-stone-50 dark:hover:bg-stone-800/30 ${
                                    needsAttention ? 'bg-amber-50/50 dark:bg-amber-900/10' : ''
                                  }`}
                                >
                                  <td className="py-2 px-3">
                                    <div className="flex items-center gap-2">
                                      <div className={`w-2 h-2 rounded-full ${host.status === 'online' ? 'bg-green-500' : 'bg-red-500'}`}></div>
                                      <span className="font-medium text-stone-700 dark:text-stone-300">{host.name}</span>
                                    </div>
                                  </td>
                                  <td className="py-2 px-3 text-stone-600 dark:text-stone-400 font-mono text-xs">
                                    {config?.data_plane_interface || '-'}
                                  </td>
                                  <td className="py-2 px-3">
                                    {config?.current_mtu ? (
                                      <span className={`font-mono ${
                                        config.current_mtu >= (config.desired_mtu || 9000)
                                          ? 'text-green-600 dark:text-green-400'
                                          : 'text-amber-600 dark:text-amber-400'
                                      }`}>
                                        {config.current_mtu}
                                        {config.current_mtu < (config.desired_mtu || 9000) && (
                                          <i className="fa-solid fa-triangle-exclamation ml-1.5 text-[10px]" title="Below desired MTU"></i>
                                        )}
                                      </span>
                                    ) : (
                                      <span className="text-stone-400">-</span>
                                    )}
                                  </td>
                                  <td className="py-2 px-3 text-stone-600 dark:text-stone-400 font-mono">
                                    {config?.desired_mtu || 9000}
                                  </td>
                                  <td className="py-2 px-3">
                                    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium ${statusBadge.color}`}>
                                      <i className={`fa-solid ${statusBadge.icon} text-[10px]`}></i>
                                      {statusBadge.text}
                                    </span>
                                    {config?.sync_error && (
                                      <span className="ml-2 text-xs text-red-500" title={config.sync_error}>
                                        <i className="fa-solid fa-circle-exclamation"></i>
                                      </span>
                                    )}
                                  </td>
                                  <td className="py-2 px-3 text-right">
                                    <button
                                      onClick={() => openMtuConfigModal(host.id)}
                                      disabled={host.status !== 'online' || isLoading}
                                      className={`px-2 py-1 rounded text-xs font-medium transition-all ${
                                        host.status === 'online' && !isLoading
                                          ? 'bg-stone-100 dark:bg-stone-800 hover:bg-stone-200 dark:hover:bg-stone-700 text-stone-600 dark:text-stone-400'
                                          : 'bg-stone-100 dark:bg-stone-800 text-stone-400 cursor-not-allowed'
                                      }`}
                                    >
                                      {isLoading ? (
                                        <i className="fa-solid fa-spinner fa-spin"></i>
                                      ) : (
                                        <>
                                          <i className="fa-solid fa-cog mr-1"></i>
                                          Configure
                                        </>
                                      )}
                                    </button>
                                  </td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    )}

                    {networkConfigs.some(c => c.sync_status === 'mismatch' || c.sync_status === 'error') && (
                      <div className="mt-4 p-3 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-lg">
                        <div className="flex items-start gap-2">
                          <i className="fa-solid fa-triangle-exclamation text-amber-500 mt-0.5"></i>
                          <div className="text-xs text-amber-700 dark:text-amber-300">
                            <strong>Attention:</strong> Some agents have MTU configurations that need attention.
                            Click Configure to update the interface MTU.
                          </div>
                        </div>
                      </div>
                    )}
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

      {/* Deregister Confirmation Modal */}
      {deregisterHost && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white dark:bg-stone-900 rounded-2xl shadow-2xl w-full max-w-md mx-4">
            <div className="p-6 border-b border-stone-200 dark:border-stone-800">
              <div className="flex items-center justify-between">
                <h2 className="text-lg font-bold text-stone-900 dark:text-white flex items-center gap-2">
                  <i className="fa-solid fa-link-slash text-red-500"></i>
                  Deregister Host
                </h2>
                <button
                  onClick={handleCancelDeregister}
                  className="text-stone-400 hover:text-stone-600 dark:hover:text-stone-300 transition-colors"
                >
                  <i className="fa-solid fa-times text-lg"></i>
                </button>
              </div>
            </div>

            <div className="p-6 space-y-4">
              {deregisterLoading ? (
                <div className="flex items-center justify-center py-8">
                  <i className="fa-solid fa-spinner fa-spin text-stone-400 text-xl"></i>
                  <span className="ml-3 text-stone-500">Loading impact summary...</span>
                </div>
              ) : deregisterInfo ? (
                <>
                  <p className="text-sm text-stone-600 dark:text-stone-400">
                    Remove <strong className="text-red-600 dark:text-red-400">{deregisterInfo.agent_name}</strong> from the controller?
                  </p>

                  <div className="text-xs text-stone-500 dark:text-stone-400 space-y-1.5">
                    <p className="font-medium text-stone-600 dark:text-stone-300">This will clean up:</p>
                    <ul className="list-disc list-inside space-y-1 pl-1">
                      {deregisterInfo.labs_assigned > 0 && (
                        <li>Unassign {deregisterInfo.labs_assigned} lab{deregisterInfo.labs_assigned !== 1 ? 's' : ''} from this host</li>
                      )}
                      {deregisterInfo.node_placements > 0 && (
                        <li>Remove {deregisterInfo.node_placements} node placement{deregisterInfo.node_placements !== 1 ? 's' : ''}</li>
                      )}
                      {deregisterInfo.nodes_assigned > 0 && (
                        <li>Unassign {deregisterInfo.nodes_assigned} node{deregisterInfo.nodes_assigned !== 1 ? 's' : ''} from this host</li>
                      )}
                      {deregisterInfo.cross_host_links > 0 && (
                        <li>Clear {deregisterInfo.cross_host_links} cross-host link reference{deregisterInfo.cross_host_links !== 1 ? 's' : ''}</li>
                      )}
                      {deregisterInfo.vxlan_tunnels > 0 && (
                        <li>Delete {deregisterInfo.vxlan_tunnels} VXLAN tunnel{deregisterInfo.vxlan_tunnels !== 1 ? 's' : ''}</li>
                      )}
                      {deregisterInfo.labs_assigned === 0 && deregisterInfo.node_placements === 0 && deregisterInfo.nodes_assigned === 0 && deregisterInfo.cross_host_links === 0 && deregisterInfo.vxlan_tunnels === 0 && (
                        <li>No dependent resources found</li>
                      )}
                    </ul>
                  </div>

                  {deregisterInfo.running_labs.length > 0 && (
                    <div className="p-3 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-lg">
                      <div className="flex items-start gap-2">
                        <i className="fa-solid fa-triangle-exclamation text-amber-500 mt-0.5 flex-shrink-0"></i>
                        <div className="text-xs text-amber-700 dark:text-amber-300">
                          <strong>Warning:</strong> This host has {deregisterInfo.running_labs.length} running lab{deregisterInfo.running_labs.length !== 1 ? 's' : ''}.
                          Containers will NOT be stopped automatically.
                          <ul className="mt-1 list-disc list-inside">
                            {deregisterInfo.running_labs.map(lab => (
                              <li key={lab.id}>{lab.name}</li>
                            ))}
                          </ul>
                        </div>
                      </div>
                    </div>
                  )}

                  <p className="text-xs text-stone-400 dark:text-stone-500">
                    Topology data (nodes, links, configs) is preserved. The agent can re-register later.
                  </p>
                </>
              ) : null}
            </div>

            <div className="p-6 border-t border-stone-200 dark:border-stone-800 flex justify-end gap-3">
              <button
                onClick={handleCancelDeregister}
                className="px-4 py-2 bg-stone-100 dark:bg-stone-800 hover:bg-stone-200 dark:hover:bg-stone-700 text-stone-600 dark:text-stone-400 rounded-lg transition-all text-sm font-medium"
              >
                Cancel
              </button>
              <button
                onClick={handleConfirmDeregister}
                disabled={deregisterLoading || deregistering}
                className={`px-4 py-2 rounded-lg transition-all text-sm font-medium ${
                  !deregisterLoading && !deregistering
                    ? 'bg-red-600 hover:bg-red-700 text-white'
                    : 'bg-stone-200 dark:bg-stone-800 text-stone-400 cursor-not-allowed'
                }`}
              >
                {deregistering ? (
                  <>
                    <i className="fa-solid fa-spinner fa-spin mr-2"></i>
                    Deregistering...
                  </>
                ) : (
                  <>
                    <i className="fa-solid fa-link-slash mr-2"></i>
                    Deregister
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* MTU Configuration Modal */}
      {configModalData && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white dark:bg-stone-900 rounded-2xl shadow-2xl w-full max-w-lg mx-4 max-h-[90vh] overflow-y-auto">
            <div className="p-6 border-b border-stone-200 dark:border-stone-800">
              <div className="flex items-center justify-between">
                <h2 className="text-lg font-bold text-stone-900 dark:text-white flex items-center gap-2">
                  <i className="fa-solid fa-ethernet text-sage-600 dark:text-sage-400"></i>
                  Configure MTU - {configModalData.agentName}
                </h2>
                <button
                  onClick={closeMtuConfigModal}
                  className="text-stone-400 hover:text-stone-600 dark:hover:text-stone-300 transition-colors"
                >
                  <i className="fa-solid fa-times text-lg"></i>
                </button>
              </div>
            </div>

            <div className="p-6 space-y-6">
              {/* Network Manager Info */}
              {configModalData.networkManager && (
                <div className="flex items-center gap-2 text-xs text-stone-500 dark:text-stone-400 bg-stone-100 dark:bg-stone-800 px-3 py-2 rounded-lg">
                  <i className="fa-solid fa-info-circle"></i>
                  <span>Network Manager: <strong className="text-stone-700 dark:text-stone-300">{configModalData.networkManager}</strong></span>
                  {configModalData.networkManager !== 'unknown' && (
                    <span className="text-green-600 dark:text-green-400">(will persist on reboot)</span>
                  )}
                  {configModalData.networkManager === 'unknown' && (
                    <span className="text-amber-600 dark:text-amber-400">(runtime only, may not persist)</span>
                  )}
                </div>
              )}

              {/* Interface Selection */}
              <div>
                <label className="block text-sm font-medium text-stone-700 dark:text-stone-300 mb-2">
                  Physical Interface
                </label>
                {configModalData.interfaces.length === 0 ? (
                  <p className="text-sm text-stone-500">No physical interfaces found on this agent.</p>
                ) : (
                  <select
                    value={selectedInterface}
                    onChange={(e) => setSelectedInterface(e.target.value)}
                    className="w-full px-3 py-2 bg-stone-100 dark:bg-stone-800 border border-stone-300 dark:border-stone-700 rounded-lg text-stone-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-sage-500"
                  >
                    <option value="">Select an interface...</option>
                    {configModalData.interfaces.map((iface) => (
                      <option key={iface.name} value={iface.name}>
                        {iface.name}
                        {iface.is_default_route ? ' (Recommended - Default Route)' : ''}
                        {' - '}MTU: {iface.mtu}
                        {iface.ipv4_addresses.length > 0 ? ` - ${iface.ipv4_addresses[0]}` : ''}
                      </option>
                    ))}
                  </select>
                )}

                {/* Selected Interface Details */}
                {selectedInterface && (
                  <div className="mt-3 p-3 bg-stone-50 dark:bg-stone-800/50 rounded-lg">
                    {(() => {
                      const iface = configModalData.interfaces.find(i => i.name === selectedInterface);
                      if (!iface) return null;
                      return (
                        <div className="grid grid-cols-2 gap-2 text-xs">
                          <div>
                            <span className="text-stone-500">Current MTU:</span>
                            <span className="ml-2 font-mono text-stone-700 dark:text-stone-300">{iface.mtu}</span>
                          </div>
                          <div>
                            <span className="text-stone-500">State:</span>
                            <span className={`ml-2 ${iface.state === 'UP' || iface.state === 'up' ? 'text-green-600 dark:text-green-400' : 'text-stone-500'}`}>
                              {iface.state}
                            </span>
                          </div>
                          <div>
                            <span className="text-stone-500">MAC:</span>
                            <span className="ml-2 font-mono text-stone-700 dark:text-stone-300">{iface.mac || 'N/A'}</span>
                          </div>
                          <div>
                            <span className="text-stone-500">IP:</span>
                            <span className="ml-2 font-mono text-stone-700 dark:text-stone-300">
                              {iface.ipv4_addresses.length > 0 ? iface.ipv4_addresses.join(', ') : 'N/A'}
                            </span>
                          </div>
                        </div>
                      );
                    })()}
                  </div>
                )}
              </div>

              {/* MTU Input */}
              <div>
                <label className="block text-sm font-medium text-stone-700 dark:text-stone-300 mb-2">
                  Desired MTU
                </label>
                <div className="flex items-center gap-3">
                  <input
                    type="number"
                    min={68}
                    max={9216}
                    value={desiredMtu}
                    onChange={(e) => setDesiredMtu(parseInt(e.target.value) || 1500)}
                    className="w-32 px-3 py-2 bg-stone-100 dark:bg-stone-800 border border-stone-300 dark:border-stone-700 rounded-lg text-stone-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-sage-500 font-mono"
                  />
                  <span className="text-xs text-stone-500">bytes (68-9216)</span>
                </div>
                <div className="flex gap-2 mt-2">
                  <button
                    onClick={() => setDesiredMtu(1500)}
                    className={`px-2 py-1 text-xs rounded transition-colors ${desiredMtu === 1500 ? 'bg-sage-600 text-white' : 'bg-stone-100 dark:bg-stone-800 text-stone-600 dark:text-stone-400 hover:bg-stone-200 dark:hover:bg-stone-700'}`}
                  >
                    1500 (Standard)
                  </button>
                  <button
                    onClick={() => setDesiredMtu(9000)}
                    className={`px-2 py-1 text-xs rounded transition-colors ${desiredMtu === 9000 ? 'bg-sage-600 text-white' : 'bg-stone-100 dark:bg-stone-800 text-stone-600 dark:text-stone-400 hover:bg-stone-200 dark:hover:bg-stone-700'}`}
                  >
                    9000 (Jumbo)
                  </button>
                </div>
                <p className="text-xs text-stone-400 mt-2">
                  For VXLAN overlay (50 byte overhead), set underlay to at least {mtuValue + 50} for {mtuValue} byte overlay MTU.
                </p>
              </div>

              {/* Warning for MTU decrease */}
              {selectedInterface && (() => {
                const iface = configModalData.interfaces.find(i => i.name === selectedInterface);
                if (iface && desiredMtu < iface.mtu) {
                  return (
                    <div className="p-3 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-lg">
                      <div className="flex items-start gap-2">
                        <i className="fa-solid fa-triangle-exclamation text-amber-500 mt-0.5"></i>
                        <div className="text-xs text-amber-700 dark:text-amber-300">
                          You are decreasing MTU from {iface.mtu} to {desiredMtu}. This may briefly interrupt network traffic.
                        </div>
                      </div>
                    </div>
                  );
                }
                return null;
              })()}
            </div>

            <div className="p-6 border-t border-stone-200 dark:border-stone-800 flex justify-end gap-3">
              <button
                onClick={closeMtuConfigModal}
                className="px-4 py-2 bg-stone-100 dark:bg-stone-800 hover:bg-stone-200 dark:hover:bg-stone-700 text-stone-600 dark:text-stone-400 rounded-lg transition-all text-sm font-medium"
              >
                Cancel
              </button>
              <button
                onClick={saveMtuConfig}
                disabled={!selectedInterface || savingMtuConfig}
                className={`px-4 py-2 rounded-lg transition-all text-sm font-medium ${
                  selectedInterface && !savingMtuConfig
                    ? 'bg-sage-600 hover:bg-sage-700 text-white'
                    : 'bg-stone-200 dark:bg-stone-800 text-stone-400 cursor-not-allowed'
                }`}
              >
                {savingMtuConfig ? (
                  <>
                    <i className="fa-solid fa-spinner fa-spin mr-2"></i>
                    Applying...
                  </>
                ) : (
                  <>
                    <i className="fa-solid fa-check mr-2"></i>
                    Apply MTU
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
};

export default InfrastructurePage;
