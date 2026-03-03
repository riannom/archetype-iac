import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { useMtuConfig } from './useMtuConfig';
import type {
  HostDetailed,
  AgentNetworkConfig,
  ManagedInterface,
  AgentMeshResponse,
  InterfaceDetailsResponse,
} from './infrastructureTypes';

// Mock api module
vi.mock('../../api', () => ({
  apiRequest: vi.fn(),
}));

// Mock NotificationContext
const mockAddNotification = vi.fn();
vi.mock('../../contexts/NotificationContext', () => ({
  useNotifications: () => ({
    addNotification: mockAddNotification,
  }),
}));

import { apiRequest } from '../../api';

const mockApiRequest = apiRequest as ReturnType<typeof vi.fn>;

// ============================================================================
// Helpers
// ============================================================================

function makeHost(overrides: Partial<HostDetailed> = {}): HostDetailed {
  return {
    id: 'agent-1',
    name: 'Agent One',
    address: '10.0.0.1:8001',
    status: 'online',
    version: 'abc1234',
    role: 'agent',
    capabilities: {},
    resource_usage: {
      cpu_percent: 10,
      memory_percent: 20,
      memory_used_gb: 4,
      memory_total_gb: 16,
      storage_percent: 30,
      storage_used_gb: 100,
      storage_total_gb: 500,
      containers_running: 5,
      containers_total: 10,
      vms_running: 0,
      vms_total: 0,
      container_details: [],
      vm_details: [],
    },
    images: [],
    labs: [],
    lab_count: 0,
    started_at: null,
    last_heartbeat: null,
    git_sha: null,
    last_error: null,
    error_since: null,
    data_plane_address: null,
    ...overrides,
  };
}

function makeNetworkConfig(overrides: Partial<AgentNetworkConfig> = {}): AgentNetworkConfig {
  return {
    id: 'nc-1',
    host_id: 'agent-1',
    host_name: 'Agent One',
    data_plane_interface: 'eth0',
    desired_mtu: 9000,
    current_mtu: 1500,
    last_sync_at: null,
    sync_status: 'pending',
    sync_error: null,
    transport_mode: 'management',
    parent_interface: null,
    vlan_id: null,
    transport_ip: null,
    transport_subnet: null,
    ...overrides,
  };
}

function makeManagedInterface(overrides: Partial<ManagedInterface> = {}): ManagedInterface {
  return {
    id: 'mi-1',
    host_id: 'agent-1',
    host_name: 'Agent One',
    name: 'eth0',
    interface_type: 'data_plane',
    parent_interface: null,
    vlan_id: null,
    ip_address: '10.0.0.1/24',
    desired_mtu: 9000,
    current_mtu: 1500,
    is_up: true,
    sync_status: 'synced',
    sync_error: null,
    last_sync_at: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

function makeMesh(overrides: Partial<AgentMeshResponse> = {}): AgentMeshResponse {
  return {
    agents: [{ id: 'agent-1', name: 'Agent One', address: '10.0.0.1', status: 'online' }],
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

function makeInterfacesResponse(overrides: Partial<InterfaceDetailsResponse> = {}): InterfaceDetailsResponse {
  return {
    interfaces: [
      { name: 'eth0', mtu: 1500, is_physical: true, is_default_route: true, mac: '00:11:22:33:44:55', ipv4_addresses: ['10.0.0.1'], state: 'up' },
      { name: 'eth1', mtu: 1500, is_physical: true, is_default_route: false, mac: '00:11:22:33:44:66', ipv4_addresses: [], state: 'up' },
      { name: 'lo', mtu: 65536, is_physical: false, is_default_route: false, mac: null, ipv4_addresses: ['127.0.0.1'], state: 'up' },
    ],
    default_route_interface: 'eth0',
    network_manager: null,
    ...overrides,
  };
}

function defaultHookArgs() {
  return {
    hosts: [makeHost()] as HostDetailed[],
    networkConfigs: [] as AgentNetworkConfig[],
    managedInterfaces: [] as ManagedInterface[],
    mesh: null as AgentMeshResponse | null,
    mtuValue: 1450,
    loadMesh: vi.fn().mockResolvedValue(undefined),
    loadNetworkConfigs: vi.fn().mockResolvedValue(undefined),
  };
}

// ============================================================================
// Tests
// ============================================================================

describe('useMtuConfig', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // ── Initial State ──

  it('returns initial state', () => {
    const args = defaultHookArgs();
    const { result } = renderHook(() =>
      useMtuConfig(args.hosts, args.networkConfigs, args.managedInterfaces, args.mesh, args.mtuValue, args.loadMesh, args.loadNetworkConfigs)
    );

    expect(result.current.testingAll).toBe(false);
    expect(result.current.testingLink).toBeNull();
    expect(result.current.configuringMtu).toBeNull();
    expect(result.current.configModalData).toBeNull();
    expect(result.current.selectedInterface).toBe('');
    expect(result.current.desiredMtu).toBe(9000);
    expect(result.current.savingMtuConfig).toBe(false);
    expect(result.current.mtuRecommendation).toBeNull();
  });

  // ── testAllLinks ──

  it('runs all MTU tests and reloads mesh', async () => {
    const args = defaultHookArgs();
    mockApiRequest.mockResolvedValueOnce({ total_pairs: 2, successful: 2, failed: 0, results: [] });

    const { result } = renderHook(() =>
      useMtuConfig(args.hosts, args.networkConfigs, args.managedInterfaces, args.mesh, args.mtuValue, args.loadMesh, args.loadNetworkConfigs)
    );

    await act(async () => {
      await result.current.testAllLinks();
    });

    expect(mockApiRequest).toHaveBeenCalledWith('/infrastructure/mesh/test-all', { method: 'POST' });
    expect(args.loadMesh).toHaveBeenCalled();
    expect(result.current.testingAll).toBe(false);
  });

  it('shows warning when some tests fail', async () => {
    const args = defaultHookArgs();
    mockApiRequest.mockResolvedValueOnce({ total_pairs: 2, successful: 1, failed: 1, results: [] });

    const { result } = renderHook(() =>
      useMtuConfig(args.hosts, args.networkConfigs, args.managedInterfaces, args.mesh, args.mtuValue, args.loadMesh, args.loadNetworkConfigs)
    );

    await act(async () => {
      await result.current.testAllLinks();
    });

    expect(mockAddNotification).toHaveBeenCalledWith(
      'warning',
      'MTU tests completed with failures',
      expect.stringContaining('1 passed')
    );
  });

  it('handles testAllLinks error', async () => {
    const args = defaultHookArgs();
    mockApiRequest.mockRejectedValueOnce(new Error('Test endpoint down'));

    const { result } = renderHook(() =>
      useMtuConfig(args.hosts, args.networkConfigs, args.managedInterfaces, args.mesh, args.mtuValue, args.loadMesh, args.loadNetworkConfigs)
    );

    await act(async () => {
      await result.current.testAllLinks();
    });

    expect(mockAddNotification).toHaveBeenCalledWith('error', 'Failed to run MTU tests', 'Test endpoint down');
    expect(result.current.testingAll).toBe(false);
  });

  // ── testLink ──

  it('tests individual link', async () => {
    const args = defaultHookArgs();
    mockApiRequest.mockResolvedValueOnce({ success: true });

    const { result } = renderHook(() =>
      useMtuConfig(args.hosts, args.networkConfigs, args.managedInterfaces, args.mesh, args.mtuValue, args.loadMesh, args.loadNetworkConfigs)
    );

    await act(async () => {
      await result.current.testLink('agent-1', 'agent-2', 'data_plane');
    });

    expect(mockApiRequest).toHaveBeenCalledWith('/infrastructure/mesh/test-mtu', {
      method: 'POST',
      body: JSON.stringify({
        source_agent_id: 'agent-1',
        target_agent_id: 'agent-2',
        test_path: 'data_plane',
      }),
    });
    expect(args.loadMesh).toHaveBeenCalled();
    expect(result.current.testingLink).toBeNull();
  });

  it('handles testLink error', async () => {
    const args = defaultHookArgs();
    mockApiRequest.mockRejectedValueOnce(new Error('Link unreachable'));

    const { result } = renderHook(() =>
      useMtuConfig(args.hosts, args.networkConfigs, args.managedInterfaces, args.mesh, args.mtuValue, args.loadMesh, args.loadNetworkConfigs)
    );

    await act(async () => {
      await result.current.testLink('agent-1', 'agent-2', 'management');
    });

    expect(mockAddNotification).toHaveBeenCalledWith('error', 'Failed to test link', 'Link unreachable');
  });

  // ── openMtuConfigModal ──

  it('warns when agent is offline', async () => {
    const args = defaultHookArgs();
    args.hosts = [makeHost({ id: 'agent-1', status: 'offline' })];

    const { result } = renderHook(() =>
      useMtuConfig(args.hosts, args.networkConfigs, args.managedInterfaces, args.mesh, args.mtuValue, args.loadMesh, args.loadNetworkConfigs)
    );

    await act(async () => {
      await result.current.openMtuConfigModal('agent-1');
    });

    expect(mockAddNotification).toHaveBeenCalledWith('warning', 'Agent is offline');
    expect(mockApiRequest).not.toHaveBeenCalled();
  });

  it('loads interface details and opens modal', async () => {
    const args = defaultHookArgs();
    mockApiRequest.mockResolvedValueOnce(makeInterfacesResponse());

    const { result } = renderHook(() =>
      useMtuConfig(args.hosts, args.networkConfigs, args.managedInterfaces, args.mesh, args.mtuValue, args.loadMesh, args.loadNetworkConfigs)
    );

    await act(async () => {
      await result.current.openMtuConfigModal('agent-1');
    });

    expect(result.current.configModalData).not.toBeNull();
    expect(result.current.configModalData!.agentId).toBe('agent-1');
    expect(result.current.configModalData!.agentName).toBe('Agent One');
    expect(result.current.configModalData!.interfaces.length).toBe(2); // only physical
    expect(result.current.configModalData!.defaultInterface).toBe('eth0');
    expect(result.current.selectedInterface).toBe('eth0');
    expect(result.current.desiredMtu).toBe(9000);
  });

  it('pre-fills form from existing config', async () => {
    const args = defaultHookArgs();
    args.networkConfigs = [makeNetworkConfig({ data_plane_interface: 'eth1', desired_mtu: 8000 })];
    mockApiRequest.mockResolvedValueOnce(makeInterfacesResponse());

    const { result } = renderHook(() =>
      useMtuConfig(args.hosts, args.networkConfigs, args.managedInterfaces, args.mesh, args.mtuValue, args.loadMesh, args.loadNetworkConfigs)
    );

    await act(async () => {
      await result.current.openMtuConfigModal('agent-1');
    });

    expect(result.current.selectedInterface).toBe('eth1');
    expect(result.current.desiredMtu).toBe(8000);
  });

  it('handles interface fetch error', async () => {
    const args = defaultHookArgs();
    mockApiRequest.mockRejectedValueOnce(new Error('Agent unreachable'));

    const { result } = renderHook(() =>
      useMtuConfig(args.hosts, args.networkConfigs, args.managedInterfaces, args.mesh, args.mtuValue, args.loadMesh, args.loadNetworkConfigs)
    );

    await act(async () => {
      await result.current.openMtuConfigModal('agent-1');
    });

    expect(mockAddNotification).toHaveBeenCalledWith('error', 'Failed to load interface details', 'Agent unreachable');
    expect(result.current.configuringMtu).toBeNull();
  });

  // ── closeMtuConfigModal ──

  it('resets modal state on close', async () => {
    const args = defaultHookArgs();
    mockApiRequest.mockResolvedValueOnce(makeInterfacesResponse());

    const { result } = renderHook(() =>
      useMtuConfig(args.hosts, args.networkConfigs, args.managedInterfaces, args.mesh, args.mtuValue, args.loadMesh, args.loadNetworkConfigs)
    );

    await act(async () => {
      await result.current.openMtuConfigModal('agent-1');
    });

    expect(result.current.configModalData).not.toBeNull();

    act(() => {
      result.current.closeMtuConfigModal();
    });

    expect(result.current.configModalData).toBeNull();
    expect(result.current.selectedInterface).toBe('');
    expect(result.current.desiredMtu).toBe(9000);
    expect(result.current.selectedTransportMode).toBe('management');
  });

  // ── saveMtuConfig ──

  it('saves configuration and closes modal', async () => {
    const args = defaultHookArgs();
    // Open modal
    mockApiRequest.mockResolvedValueOnce(makeInterfacesResponse());

    const { result } = renderHook(() =>
      useMtuConfig(args.hosts, args.networkConfigs, args.managedInterfaces, args.mesh, args.mtuValue, args.loadMesh, args.loadNetworkConfigs)
    );

    await act(async () => {
      await result.current.openMtuConfigModal('agent-1');
    });

    // Save config
    mockApiRequest.mockResolvedValueOnce({});

    await act(async () => {
      await result.current.saveMtuConfig();
    });

    expect(mockApiRequest).toHaveBeenCalledWith(
      '/infrastructure/agents/agent-1/network-config',
      expect.objectContaining({ method: 'PATCH' })
    );
    expect(args.loadNetworkConfigs).toHaveBeenCalled();
    expect(result.current.configModalData).toBeNull();
    expect(result.current.savingMtuConfig).toBe(false);
  });

  it('does nothing when configModalData is null', async () => {
    const args = defaultHookArgs();
    const { result } = renderHook(() =>
      useMtuConfig(args.hosts, args.networkConfigs, args.managedInterfaces, args.mesh, args.mtuValue, args.loadMesh, args.loadNetworkConfigs)
    );

    await act(async () => {
      await result.current.saveMtuConfig();
    });

    expect(mockApiRequest).not.toHaveBeenCalled();
  });

  it('handles save error', async () => {
    const args = defaultHookArgs();
    mockApiRequest.mockResolvedValueOnce(makeInterfacesResponse());

    const { result } = renderHook(() =>
      useMtuConfig(args.hosts, args.networkConfigs, args.managedInterfaces, args.mesh, args.mtuValue, args.loadMesh, args.loadNetworkConfigs)
    );

    await act(async () => {
      await result.current.openMtuConfigModal('agent-1');
    });

    mockApiRequest.mockRejectedValueOnce(new Error('Permission denied'));

    await act(async () => {
      await result.current.saveMtuConfig();
    });

    expect(mockAddNotification).toHaveBeenCalledWith('error', 'Failed to save MTU configuration', 'Permission denied');
    expect(result.current.savingMtuConfig).toBe(false);
  });

  // ── MTU Recommendation ──

  it('returns null recommendation when no mesh data', () => {
    const args = defaultHookArgs();
    const { result } = renderHook(() =>
      useMtuConfig(args.hosts, args.networkConfigs, args.managedInterfaces, args.mesh, args.mtuValue, args.loadMesh, args.loadNetworkConfigs)
    );

    expect(result.current.mtuRecommendation).toBeNull();
  });

  it('computes MTU recommendation from data plane tests', () => {
    const args = defaultHookArgs();
    args.mesh = makeMesh({
      links: [
        {
          id: 'link-1',
          source_agent_id: 'agent-1',
          source_agent_name: 'A1',
          target_agent_id: 'agent-2',
          target_agent_name: 'A2',
          link_type: 'vxlan',
          configured_mtu: 1450,
          tested_mtu: 9000,
          last_test_at: '2026-01-01T00:00:00Z',
          test_status: 'success',
          test_error: null,
          latency_ms: 1,
          test_path: 'data_plane',
        },
      ],
    });
    args.mtuValue = 1450;

    const { result } = renderHook(() =>
      useMtuConfig(args.hosts, args.networkConfigs, args.managedInterfaces, args.mesh, args.mtuValue, args.loadMesh, args.loadNetworkConfigs)
    );

    // 9000 - 50 = 8950 which is > 1450, so recommendation should be 8950
    expect(result.current.mtuRecommendation).toBe(8950);
  });

  it('returns null when all data plane tests failed', () => {
    const args = defaultHookArgs();
    args.mesh = makeMesh({
      links: [
        {
          id: 'link-1',
          source_agent_id: 'agent-1',
          source_agent_name: 'A1',
          target_agent_id: 'agent-2',
          target_agent_name: 'A2',
          link_type: 'vxlan',
          configured_mtu: 1450,
          tested_mtu: null,
          last_test_at: null,
          test_status: 'failed',
          test_error: 'timeout',
          latency_ms: null,
          test_path: 'data_plane',
        },
      ],
    });

    const { result } = renderHook(() =>
      useMtuConfig(args.hosts, args.networkConfigs, args.managedInterfaces, args.mesh, args.mtuValue, args.loadMesh, args.loadNetworkConfigs)
    );

    expect(result.current.mtuRecommendation).toBeNull();
  });

  it('returns null when recommended is not greater than current mtu', () => {
    const args = defaultHookArgs();
    args.mtuValue = 9000;
    args.mesh = makeMesh({
      links: [
        {
          id: 'link-1',
          source_agent_id: 'agent-1',
          source_agent_name: 'A1',
          target_agent_id: 'agent-2',
          target_agent_name: 'A2',
          link_type: 'vxlan',
          configured_mtu: 9000,
          tested_mtu: 9000,
          last_test_at: '2026-01-01T00:00:00Z',
          test_status: 'success',
          test_error: null,
          latency_ms: 1,
          test_path: 'data_plane',
        },
      ],
    });

    const { result } = renderHook(() =>
      useMtuConfig(args.hosts, args.networkConfigs, args.managedInterfaces, args.mesh, args.mtuValue, args.loadMesh, args.loadNetworkConfigs)
    );

    // 9000 - 50 = 8950 which is < 9000, so null
    expect(result.current.mtuRecommendation).toBeNull();
  });

  // ── State setters ──

  it('can update desiredMtu', async () => {
    const args = defaultHookArgs();
    mockApiRequest.mockResolvedValueOnce(makeInterfacesResponse());

    const { result } = renderHook(() =>
      useMtuConfig(args.hosts, args.networkConfigs, args.managedInterfaces, args.mesh, args.mtuValue, args.loadMesh, args.loadNetworkConfigs)
    );

    await act(async () => {
      await result.current.openMtuConfigModal('agent-1');
    });

    act(() => {
      result.current.setDesiredMtu(4000);
    });

    expect(result.current.desiredMtu).toBe(4000);
  });

  it('can update selectedTransportMode', () => {
    const args = defaultHookArgs();
    const { result } = renderHook(() =>
      useMtuConfig(args.hosts, args.networkConfigs, args.managedInterfaces, args.mesh, args.mtuValue, args.loadMesh, args.loadNetworkConfigs)
    );

    act(() => {
      result.current.setSelectedTransportMode('subinterface');
    });

    expect(result.current.selectedTransportMode).toBe('subinterface');
  });
});
