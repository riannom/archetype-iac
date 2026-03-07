import { useCallback, useEffect, useState } from 'react';
import { apiRequest } from '../../api';
import type {
  AgentImagesDetailResponse,
  AgentStaleCleanupResponse,
  AgentMeshResponse,
  HostDetailed,
  AgentNetworkConfig,
  ManagedInterface,
  ManagedInterfacesResponse,
  NicGroup,
  NicGroupsResponse,
} from './infrastructureTypes';

export function useInfrastructureData() {
  // Settings/Mesh state
  const [mesh, setMesh] = useState<AgentMeshResponse | null>(null);
  const [meshLoading, setMeshLoading] = useState(true);
  const [meshError, setMeshError] = useState<string | null>(null);

  // Settings form state
  const [mtuValue, setMtuValue] = useState<number>(1450);
  const [settingsDirty, setSettingsDirty] = useState(false);

  // Hosts state
  const [hosts, setHosts] = useState<HostDetailed[]>([]);
  const [hostsLoading, setHostsLoading] = useState(true);
  const [hostsError, setHostsError] = useState<string | null>(null);
  const [latestVersion, setLatestVersion] = useState<string>('');
  const [agentImageDetails, setAgentImageDetails] = useState<Record<string, AgentImagesDetailResponse>>({});
  const [agentImagesLoading, setAgentImagesLoading] = useState<Set<string>>(new Set());
  const [agentImagesCleaning, setAgentImagesCleaning] = useState<Set<string>>(new Set());

  // Host network config state
  const [networkConfigs, setNetworkConfigs] = useState<AgentNetworkConfig[]>([]);
  const [networkConfigsLoading, setNetworkConfigsLoading] = useState(false);

  // Managed interfaces state
  const [managedInterfaces, setManagedInterfaces] = useState<ManagedInterface[]>([]);

  // NIC groups state (future interface affinity)
  const [nicGroups, setNicGroups] = useState<NicGroup[]>([]);
  const [nicGroupsLoading, setNicGroupsLoading] = useState(false);

  // ============================================================================
  // Data Loading
  // ============================================================================

  const loadMesh = useCallback(async () => {
    try {
      const data = await apiRequest<AgentMeshResponse>('/infrastructure/mesh');
      setMesh(data);
      setMtuValue(data.settings.overlay_mtu);
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

  const loadAgentImageDetails = useCallback(async (hostId: string, force = false) => {
    if (!force && agentImageDetails[hostId]) {
      return agentImageDetails[hostId];
    }
    setAgentImagesLoading(prev => {
      const next = new Set(prev);
      next.add(hostId);
      return next;
    });
    try {
      const data = await apiRequest<AgentImagesDetailResponse>(`/agents/${hostId}/images`);
      setAgentImageDetails(prev => ({ ...prev, [hostId]: data }));
      return data;
    } finally {
      setAgentImagesLoading(prev => {
        const next = new Set(prev);
        next.delete(hostId);
        return next;
      });
    }
  }, [agentImageDetails]);

  const cleanupStaleAgentImages = useCallback(async (hostId: string) => {
    setAgentImagesCleaning(prev => {
      const next = new Set(prev);
      next.add(hostId);
      return next;
    });
    try {
      const response = await apiRequest<AgentStaleCleanupResponse>(`/agents/${hostId}/images/cleanup-stale`, {
        method: 'POST',
      });
      await loadAgentImageDetails(hostId, true);
      return response;
    } finally {
      setAgentImagesCleaning(prev => {
        const next = new Set(prev);
        next.delete(hostId);
        return next;
      });
    }
  }, [loadAgentImageDetails]);

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

  const loadManagedInterfaces = useCallback(async () => {
    try {
      const data = await apiRequest<ManagedInterfacesResponse>('/infrastructure/interfaces');
      setManagedInterfaces(data.interfaces);
    } catch (err) {
      console.error('Failed to load managed interfaces:', err);
    }
  }, []);

  const loadNicGroups = useCallback(async () => {
    setNicGroupsLoading(true);
    try {
      const data = await apiRequest<NicGroupsResponse>('/infrastructure/nic-groups');
      setNicGroups(data.groups);
    } catch (err) {
      console.error('Failed to load NIC groups:', err);
    } finally {
      setNicGroupsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadMesh();
    loadHosts();
    loadLatestVersion();
    loadNetworkConfigs();
    loadManagedInterfaces();
    loadNicGroups();
    const meshInterval = setInterval(loadMesh, 30000);
    const hostsInterval = setInterval(loadHosts, 10000);
    const networkConfigsInterval = setInterval(loadNetworkConfigs, 30000);
    const managedIfacesInterval = setInterval(loadManagedInterfaces, 30000);
    const nicGroupsInterval = setInterval(loadNicGroups, 30000);
    return () => {
      clearInterval(meshInterval);
      clearInterval(hostsInterval);
      clearInterval(networkConfigsInterval);
      clearInterval(managedIfacesInterval);
      clearInterval(nicGroupsInterval);
    };
  }, [loadMesh, loadHosts, loadLatestVersion, loadNetworkConfigs, loadManagedInterfaces, loadNicGroups]);

  return {
    // Mesh
    mesh,
    meshLoading,
    meshError,
    loadMesh,
    // Settings form
    mtuValue,
    setMtuValue,
    settingsDirty,
    setSettingsDirty,
    // Hosts
    hosts,
    setHosts,
    hostsLoading,
    hostsError,
    latestVersion,
    agentImageDetails,
    agentImagesLoading,
    agentImagesCleaning,
    loadAgentImageDetails,
    cleanupStaleAgentImages,
    loadHosts,
    // Network configs
    networkConfigs,
    networkConfigsLoading,
    loadNetworkConfigs,
    // Managed interfaces
    managedInterfaces,
    // NIC groups
    nicGroups,
    nicGroupsLoading,
    loadNicGroups,
    // Managed interfaces loader
    loadManagedInterfaces,
  };
}
