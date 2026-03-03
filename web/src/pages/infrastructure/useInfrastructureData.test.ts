/**
 * Tests for useInfrastructureData hook.
 *
 * These tests verify:
 * 1. Initial data loading (mesh, hosts, version, network configs, interfaces, NIC groups)
 * 2. Loading state transitions
 * 3. Error handling for API failures
 * 4. Empty data handling
 * 5. Periodic refresh via intervals
 * 6. Data state management (mtu, settings dirty flag)
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { useInfrastructureData } from './useInfrastructureData';
import type {
  AgentMeshResponse,
  HostDetailed,
  AgentNetworkConfig,
  ManagedInterfacesResponse,
  NicGroupsResponse,
} from './infrastructureTypes';

// Mock the apiRequest function
vi.mock('../../api', () => ({
  apiRequest: vi.fn(),
}));

import { apiRequest } from '../../api';

const mockApiRequest = apiRequest as ReturnType<typeof vi.fn>;

// ============================================================================
// Fixtures
// ============================================================================

function makeMeshResponse(overrides: Partial<AgentMeshResponse> = {}): AgentMeshResponse {
  return {
    agents: [
      { id: 'agent-1', name: 'Controller', address: '10.0.0.1:8001', status: 'online' },
    ],
    links: [],
    settings: {
      overlay_mtu: 1450,
      mtu_verification_enabled: true,
      overlay_preserve_container_mtu: false,
      overlay_clamp_host_mtu: false,
      updated_at: null,
      updated_by_id: null,
    },
    ...overrides,
  };
}

function makeHostDetailed(overrides: Partial<HostDetailed> = {}): HostDetailed {
  return {
    id: 'agent-1',
    name: 'Controller',
    address: '10.0.0.1:8001',
    status: 'online',
    version: '1.0.0',
    role: 'controller',
    capabilities: { providers: ['docker'], features: [], max_concurrent_jobs: 4 },
    resource_usage: {
      cpu_percent: 25,
      memory_percent: 50,
      memory_used_gb: 8,
      memory_total_gb: 16,
      storage_percent: 30,
      storage_used_gb: 100,
      storage_total_gb: 300,
      containers_running: 3,
      containers_total: 5,
      vms_running: 0,
      vms_total: 0,
      container_details: [],
      vm_details: [],
    },
    images: [],
    labs: [],
    lab_count: 2,
    started_at: '2024-01-01T00:00:00Z',
    last_heartbeat: '2024-01-01T01:00:00Z',
    git_sha: 'abc123',
    last_error: null,
    error_since: null,
    data_plane_address: null,
    ...overrides,
  };
}

// ============================================================================
// Test Setup Helpers
// ============================================================================

/**
 * Sets up mock responses for all initial API calls.
 * The useInfrastructureData hook loads 6 endpoints on mount.
 */
function setupDefaultMocks(overrides: {
  mesh?: AgentMeshResponse | Error;
  hosts?: HostDetailed[] | Error;
  latestVersion?: { version: string } | Error;
  networkConfigs?: AgentNetworkConfig[] | Error;
  managedInterfaces?: ManagedInterfacesResponse | Error;
  nicGroups?: NicGroupsResponse | Error;
} = {}) {
  const mesh = overrides.mesh ?? makeMeshResponse();
  const hosts = overrides.hosts ?? [makeHostDetailed()];
  const latestVersion = overrides.latestVersion ?? { version: 'v2.0.0' };
  const networkConfigs = overrides.networkConfigs ?? [];
  const managedInterfaces = overrides.managedInterfaces ?? { interfaces: [], total: 0 };
  const nicGroups = overrides.nicGroups ?? { groups: [], total: 0 };

  mockApiRequest.mockImplementation(async (path: string) => {
    if (path === '/infrastructure/mesh') {
      if (mesh instanceof Error) throw mesh;
      return mesh;
    }
    if (path === '/agents/detailed') {
      if (hosts instanceof Error) throw hosts;
      return hosts;
    }
    if (path === '/agents/updates/latest') {
      if (latestVersion instanceof Error) throw latestVersion;
      return latestVersion;
    }
    if (path === '/infrastructure/network-configs') {
      if (networkConfigs instanceof Error) throw networkConfigs;
      return networkConfigs;
    }
    if (path === '/infrastructure/interfaces') {
      if (managedInterfaces instanceof Error) throw managedInterfaces;
      return managedInterfaces;
    }
    if (path === '/infrastructure/nic-groups') {
      if (nicGroups instanceof Error) throw nicGroups;
      return nicGroups;
    }
    throw new Error(`Unmocked API path: ${path}`);
  });
}

describe('useInfrastructureData', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Use fake timers but let promises resolve naturally
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  // ============================================================================
  // Initial Data Loading
  // ============================================================================

  describe('initial data loading', () => {
    it('loads mesh data on mount', async () => {
      const meshData = makeMeshResponse();
      setupDefaultMocks({ mesh: meshData });

      const { result } = renderHook(() => useInfrastructureData());

      await waitFor(() => {
        expect(result.current.meshLoading).toBe(false);
      });

      expect(result.current.mesh).toEqual(meshData);
      expect(result.current.meshError).toBeNull();
    });

    it('loads hosts on mount', async () => {
      const hostsData = [makeHostDetailed(), makeHostDetailed({ id: 'agent-2', name: 'Agent 2' })];
      setupDefaultMocks({ hosts: hostsData });

      const { result } = renderHook(() => useInfrastructureData());

      await waitFor(() => {
        expect(result.current.hostsLoading).toBe(false);
      });

      expect(result.current.hosts).toHaveLength(2);
      expect(result.current.hostsError).toBeNull();
    });

    it('loads latest version on mount', async () => {
      setupDefaultMocks({ latestVersion: { version: 'v3.1.0' } });

      const { result } = renderHook(() => useInfrastructureData());

      await waitFor(() => {
        expect(result.current.latestVersion).toBe('v3.1.0');
      });
    });

    it('loads network configs on mount', async () => {
      const configs: AgentNetworkConfig[] = [{
        id: 'nc-1',
        host_id: 'agent-1',
        host_name: 'Controller',
        data_plane_interface: 'eth0',
        desired_mtu: 1500,
        current_mtu: 1500,
        last_sync_at: null,
        sync_status: 'synced',
        sync_error: null,
        transport_mode: 'management',
        parent_interface: null,
        vlan_id: null,
        transport_ip: '10.0.0.1',
        transport_subnet: '10.0.0.0/24',
      }];
      setupDefaultMocks({ networkConfigs: configs });

      const { result } = renderHook(() => useInfrastructureData());

      await waitFor(() => {
        expect(result.current.networkConfigsLoading).toBe(false);
      });

      expect(result.current.networkConfigs).toHaveLength(1);
      expect(result.current.networkConfigs[0].data_plane_interface).toBe('eth0');
    });

    it('loads NIC groups on mount', async () => {
      const groups: NicGroupsResponse = {
        groups: [{
          id: 'ng-1',
          host_id: 'agent-1',
          host_name: 'Controller',
          name: 'data-plane',
          description: 'Data plane NICs',
          created_at: '2024-01-01T00:00:00Z',
          updated_at: '2024-01-01T00:00:00Z',
          members: [],
        }],
        total: 1,
      };
      setupDefaultMocks({ nicGroups: groups });

      const { result } = renderHook(() => useInfrastructureData());

      await waitFor(() => {
        expect(result.current.nicGroupsLoading).toBe(false);
      });

      expect(result.current.nicGroups).toHaveLength(1);
    });
  });

  // ============================================================================
  // Loading State Transitions
  // ============================================================================

  describe('loading state transitions', () => {
    it('starts with loading=true for mesh and hosts', () => {
      setupDefaultMocks();

      const { result } = renderHook(() => useInfrastructureData());

      // Check synchronously before any async resolution
      expect(result.current.meshLoading).toBe(true);
      expect(result.current.hostsLoading).toBe(true);
    });

    it('transitions meshLoading to false after load completes', async () => {
      setupDefaultMocks();

      const { result } = renderHook(() => useInfrastructureData());

      expect(result.current.meshLoading).toBe(true);

      await waitFor(() => {
        expect(result.current.meshLoading).toBe(false);
      });
    });

    it('transitions hostsLoading to false after load completes', async () => {
      setupDefaultMocks();

      const { result } = renderHook(() => useInfrastructureData());

      expect(result.current.hostsLoading).toBe(true);

      await waitFor(() => {
        expect(result.current.hostsLoading).toBe(false);
      });
    });

    it('transitions meshLoading to false even on error', async () => {
      setupDefaultMocks({ mesh: new Error('Network error') });

      const { result } = renderHook(() => useInfrastructureData());

      await waitFor(() => {
        expect(result.current.meshLoading).toBe(false);
      });

      expect(result.current.meshError).toBe('Network error');
    });
  });

  // ============================================================================
  // Error States
  // ============================================================================

  describe('error states', () => {
    it('sets meshError on mesh API failure', async () => {
      setupDefaultMocks({ mesh: new Error('Service unavailable') });

      const { result } = renderHook(() => useInfrastructureData());

      await waitFor(() => {
        expect(result.current.meshLoading).toBe(false);
      });

      expect(result.current.meshError).toBe('Service unavailable');
      expect(result.current.mesh).toBeNull();
    });

    it('sets hostsError on hosts API failure', async () => {
      setupDefaultMocks({ hosts: new Error('Forbidden') });

      const { result } = renderHook(() => useInfrastructureData());

      await waitFor(() => {
        expect(result.current.hostsLoading).toBe(false);
      });

      expect(result.current.hostsError).toBe('Forbidden');
      expect(result.current.hosts).toHaveLength(0);
    });

    it('uses fallback message for non-Error exceptions on mesh', async () => {
      mockApiRequest.mockImplementation(async (path: string) => {
        if (path === '/infrastructure/mesh') throw 'some string error';
        if (path === '/agents/detailed') return [];
        if (path === '/agents/updates/latest') return { version: '' };
        if (path === '/infrastructure/network-configs') return [];
        if (path === '/infrastructure/interfaces') return { interfaces: [], total: 0 };
        if (path === '/infrastructure/nic-groups') return { groups: [], total: 0 };
      });

      const { result } = renderHook(() => useInfrastructureData());

      await waitFor(() => {
        expect(result.current.meshLoading).toBe(false);
      });

      expect(result.current.meshError).toBe('Failed to load infrastructure data');
    });

    it('handles network config failure gracefully', async () => {
      const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
      setupDefaultMocks({ networkConfigs: new Error('timeout') });

      const { result } = renderHook(() => useInfrastructureData());

      await waitFor(() => {
        expect(result.current.networkConfigsLoading).toBe(false);
      });

      // Network configs stay empty but no crash
      expect(result.current.networkConfigs).toHaveLength(0);
      consoleSpy.mockRestore();
    });

    it('handles latest version failure gracefully', async () => {
      const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
      setupDefaultMocks({ latestVersion: new Error('Not found') });

      const { result } = renderHook(() => useInfrastructureData());

      await waitFor(() => {
        expect(result.current.meshLoading).toBe(false);
      });

      // latestVersion remains empty string
      expect(result.current.latestVersion).toBe('');
      consoleSpy.mockRestore();
    });
  });

  // ============================================================================
  // Empty Data Handling
  // ============================================================================

  describe('empty data handling', () => {
    it('handles empty mesh agents and links', async () => {
      setupDefaultMocks({
        mesh: makeMeshResponse({ agents: [], links: [] }),
      });

      const { result } = renderHook(() => useInfrastructureData());

      await waitFor(() => {
        expect(result.current.meshLoading).toBe(false);
      });

      expect(result.current.mesh?.agents).toHaveLength(0);
      expect(result.current.mesh?.links).toHaveLength(0);
    });

    it('handles empty hosts list', async () => {
      setupDefaultMocks({ hosts: [] });

      const { result } = renderHook(() => useInfrastructureData());

      await waitFor(() => {
        expect(result.current.hostsLoading).toBe(false);
      });

      expect(result.current.hosts).toHaveLength(0);
    });

    it('handles empty managed interfaces', async () => {
      setupDefaultMocks({ managedInterfaces: { interfaces: [], total: 0 } });

      const { result } = renderHook(() => useInfrastructureData());

      await waitFor(() => {
        expect(result.current.meshLoading).toBe(false);
      });

      expect(result.current.managedInterfaces).toHaveLength(0);
    });
  });

  // ============================================================================
  // MTU and Settings Form
  // ============================================================================

  describe('MTU and settings form state', () => {
    it('sets mtuValue from mesh settings on load', async () => {
      setupDefaultMocks({
        mesh: makeMeshResponse({
          settings: {
            overlay_mtu: 9000,
            mtu_verification_enabled: true,
            overlay_preserve_container_mtu: false,
            overlay_clamp_host_mtu: false,
            updated_at: null,
            updated_by_id: null,
          },
        }),
      });

      const { result } = renderHook(() => useInfrastructureData());

      await waitFor(() => {
        expect(result.current.meshLoading).toBe(false);
      });

      expect(result.current.mtuValue).toBe(9000);
    });

    it('resets settingsDirty to false on mesh load', async () => {
      setupDefaultMocks();

      const { result } = renderHook(() => useInfrastructureData());

      await waitFor(() => {
        expect(result.current.meshLoading).toBe(false);
      });

      // Mark as dirty
      act(() => {
        result.current.setSettingsDirty(true);
      });

      expect(result.current.settingsDirty).toBe(true);

      // Reload mesh -- should reset dirty flag
      await act(async () => {
        await result.current.loadMesh();
      });

      expect(result.current.settingsDirty).toBe(false);
    });

    it('clears meshError on successful reload', async () => {
      // First load fails
      setupDefaultMocks({ mesh: new Error('Service unavailable') });

      const { result } = renderHook(() => useInfrastructureData());

      await waitFor(() => {
        expect(result.current.meshError).toBe('Service unavailable');
      });

      // Now fix the mock for reload
      mockApiRequest.mockImplementation(async (path: string) => {
        if (path === '/infrastructure/mesh') return makeMeshResponse();
        if (path === '/agents/detailed') return [];
        if (path === '/agents/updates/latest') return { version: '' };
        if (path === '/infrastructure/network-configs') return [];
        if (path === '/infrastructure/interfaces') return { interfaces: [], total: 0 };
        if (path === '/infrastructure/nic-groups') return { groups: [], total: 0 };
      });

      await act(async () => {
        await result.current.loadMesh();
      });

      expect(result.current.meshError).toBeNull();
      expect(result.current.mesh).not.toBeNull();
    });
  });

  // ============================================================================
  // Periodic Refresh
  // ============================================================================

  describe('periodic refresh', () => {
    it('sets up intervals on mount', async () => {
      setupDefaultMocks();

      renderHook(() => useInfrastructureData());

      // Wait for initial load
      await waitFor(() => {
        expect(mockApiRequest).toHaveBeenCalled();
      });

      const callCountAfterInit = mockApiRequest.mock.calls.length;

      // Advance time by 10 seconds -- hosts refresh interval
      await act(async () => {
        vi.advanceTimersByTime(10000);
      });

      // Should have at least one more call (hosts refreshes at 10s)
      expect(mockApiRequest.mock.calls.length).toBeGreaterThan(callCountAfterInit);
    });

    it('clears intervals on unmount', async () => {
      setupDefaultMocks();

      const { unmount } = renderHook(() => useInfrastructureData());

      await waitFor(() => {
        expect(mockApiRequest).toHaveBeenCalled();
      });

      unmount();

      const callCountAfterUnmount = mockApiRequest.mock.calls.length;

      // Advance timers -- should not trigger more API calls
      vi.advanceTimersByTime(60000);

      expect(mockApiRequest.mock.calls.length).toBe(callCountAfterUnmount);
    });
  });

  // ============================================================================
  // Manual Reload
  // ============================================================================

  describe('manual reload', () => {
    it('loadMesh can be called manually to refresh mesh data', async () => {
      setupDefaultMocks();

      const { result } = renderHook(() => useInfrastructureData());

      await waitFor(() => {
        expect(result.current.meshLoading).toBe(false);
      });

      const meshCalls = mockApiRequest.mock.calls.filter(
        (c: string[]) => c[0] === '/infrastructure/mesh'
      ).length;

      await act(async () => {
        await result.current.loadMesh();
      });

      const meshCallsAfter = mockApiRequest.mock.calls.filter(
        (c: string[]) => c[0] === '/infrastructure/mesh'
      ).length;

      expect(meshCallsAfter).toBe(meshCalls + 1);
    });

    it('loadHosts can be called manually to refresh hosts', async () => {
      setupDefaultMocks();

      const { result } = renderHook(() => useInfrastructureData());

      await waitFor(() => {
        expect(result.current.hostsLoading).toBe(false);
      });

      const updatedHosts = [
        makeHostDetailed({ id: 'agent-1', name: 'Controller Updated' }),
        makeHostDetailed({ id: 'agent-2', name: 'New Agent' }),
      ];

      mockApiRequest.mockImplementation(async (path: string) => {
        if (path === '/agents/detailed') return updatedHosts;
        if (path === '/infrastructure/mesh') return makeMeshResponse();
        if (path === '/agents/updates/latest') return { version: '' };
        if (path === '/infrastructure/network-configs') return [];
        if (path === '/infrastructure/interfaces') return { interfaces: [], total: 0 };
        if (path === '/infrastructure/nic-groups') return { groups: [], total: 0 };
      });

      await act(async () => {
        await result.current.loadHosts();
      });

      expect(result.current.hosts).toHaveLength(2);
      expect(result.current.hosts[1].name).toBe('New Agent');
    });

    it('loadNicGroups can be called manually', async () => {
      setupDefaultMocks();

      const { result } = renderHook(() => useInfrastructureData());

      await waitFor(() => {
        expect(result.current.nicGroupsLoading).toBe(false);
      });

      mockApiRequest.mockImplementation(async (path: string) => {
        if (path === '/infrastructure/nic-groups') return {
          groups: [{
            id: 'ng-1',
            host_id: 'agent-1',
            host_name: 'Controller',
            name: 'lab-nics',
            description: null,
            created_at: '2024-01-01T00:00:00Z',
            updated_at: '2024-01-01T00:00:00Z',
            members: [],
          }],
          total: 1,
        };
        if (path === '/infrastructure/mesh') return makeMeshResponse();
        if (path === '/agents/detailed') return [];
        if (path === '/agents/updates/latest') return { version: '' };
        if (path === '/infrastructure/network-configs') return [];
        if (path === '/infrastructure/interfaces') return { interfaces: [], total: 0 };
      });

      await act(async () => {
        await result.current.loadNicGroups();
      });

      expect(result.current.nicGroups).toHaveLength(1);
      expect(result.current.nicGroups[0].name).toBe('lab-nics');
    });
  });
});
