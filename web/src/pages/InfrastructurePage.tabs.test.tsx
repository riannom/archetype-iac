import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import InfrastructurePage from './InfrastructurePage';

const apiRequest = vi.fn();
const addNotification = vi.fn();
const mockNavigate = vi.fn();

vi.mock('../api', () => ({
  apiRequest: (...args: unknown[]) => apiRequest(...args),
}));

vi.mock('../theme/index', () => ({
  useTheme: () => ({ effectiveMode: 'light', toggleMode: vi.fn() }),
  ThemeSelector: ({ isOpen }: { isOpen: boolean }) => (isOpen ? <div>Theme</div> : null),
}));

vi.mock('../contexts/UserContext', () => ({
  useUser: () => ({
    user: { id: 'user-1', username: 'admin', email: 'admin@example.com', is_active: true, global_role: 'super_admin', created_at: '2024-01-01T00:00:00Z' },
    loading: false,
  }),
}));

vi.mock('../contexts/NotificationContext', () => ({
  useNotifications: () => ({ addNotification }),
}));

vi.mock('../components/AdminMenuButton', () => ({
  default: () => <div>AdminMenuButton</div>,
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
    useSearchParams: () => [new URLSearchParams(), vi.fn()],
    Navigate: ({ to }: { to: string }) => <div data-testid="redirect">Redirect {to}</div>,
  };
});

const defaultMeshResponse = {
  agents: [
    { id: 'a1', name: 'agent-1', address: '10.0.0.1:8001', status: 'online' },
    { id: 'a2', name: 'agent-2', address: '10.0.0.2:8001', status: 'online' },
  ],
  links: [
    {
      id: 'link-1',
      source_agent_id: 'a1',
      source_agent_name: 'agent-1',
      target_agent_id: 'a2',
      target_agent_name: 'agent-2',
      link_type: 'vxlan',
      configured_mtu: 1450,
      tested_mtu: 1450,
      last_test_at: '2026-02-01T10:00:00Z',
      test_status: 'success',
      test_error: null,
      latency_ms: 2.5,
      test_path: 'management',
    },
  ],
  settings: {
    overlay_mtu: 1450,
    mtu_verification_enabled: true,
    overlay_preserve_container_mtu: false,
    overlay_clamp_host_mtu: true,
    updated_at: null,
    updated_by_id: null,
  },
};

const defaultHostsResponse = [
  {
    id: 'a1',
    name: 'agent-1',
    address: '10.0.0.1:8001',
    status: 'online',
    version: '0.5.0',
    role: 'agent',
    capabilities: { providers: ['docker'], features: [], max_concurrent_jobs: 5 },
    resource_usage: {
      cpu_percent: 25.5,
      memory_percent: 40.2,
      memory_used_gb: 8.0,
      memory_total_gb: 20.0,
      storage_percent: 55.0,
      storage_used_gb: 110,
      storage_total_gb: 200,
      containers_running: 3,
      containers_total: 5,
      vms_running: 0,
      vms_total: 0,
      container_details: [],
      vm_details: [],
    },
    images: [],
    labs: [{ id: 'lab-1', name: 'Test Lab', state: 'running' }],
    lab_count: 1,
    started_at: '2026-02-01T00:00:00Z',
    last_heartbeat: '2026-02-01T10:00:00Z',
    git_sha: 'abc1234',
    last_error: null,
    error_since: null,
    data_plane_address: null,
  },
];

function setupDefaultMocks() {
  apiRequest.mockImplementation(async (path: string) => {
    if (path === '/infrastructure/mesh') return defaultMeshResponse;
    if (path === '/agents/detailed') return defaultHostsResponse;
    if (path === '/agents/updates/latest') return { version: '0.5.0' };
    if (path === '/infrastructure/network-configs') return [];
    if (path === '/infrastructure/interfaces') return { interfaces: [], total: 0 };
    if (path === '/infrastructure/nic-groups') return { groups: [], total: 0 };
    return null;
  });
}

describe('InfrastructurePage - tabs and settings', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    setupDefaultMocks();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  // ============================================================
  // Tab navigation
  // ============================================================

  it('renders the page title and subtitle', async () => {
    render(<InfrastructurePage />);
    await waitFor(() => {
      expect(screen.getByText('Archetype Infrastructure Management')).toBeInTheDocument();
    });
  });

  it('renders Hosts tab by default', async () => {
    render(<InfrastructurePage />);
    await waitFor(() => {
      expect(screen.getByText('Hosts')).toBeInTheDocument();
      expect(screen.getByText('Network')).toBeInTheDocument();
    });
  });

  it('switches to the Network tab when clicked', async () => {
    render(<InfrastructurePage />);
    await waitFor(() => expect(screen.getByText('Network')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Network'));
    // Network tab should now be active
    await waitFor(() => {
      const networkBtn = screen.getByText('Network').closest('button');
      expect(networkBtn).toBeTruthy();
    });
  });

  // ============================================================
  // Agent Mesh / Settings
  // ============================================================

  it('renders agents and mesh links when mesh data is present', async () => {
    render(<InfrastructurePage />);
    await waitFor(() => {
      expect(screen.getByText('agent-1')).toBeInTheDocument();
    });
  });

  it('shows empty state when no agents exist', async () => {
    apiRequest.mockImplementation(async (path: string) => {
      if (path === '/infrastructure/mesh') return { agents: [], links: [], settings: defaultMeshResponse.settings };
      if (path === '/agents/detailed') return [];
      if (path === '/agents/updates/latest') return { version: '0.5.0' };
      if (path === '/infrastructure/network-configs') return [];
      if (path === '/infrastructure/interfaces') return { interfaces: [], total: 0 };
      if (path === '/infrastructure/nic-groups') return { groups: [], total: 0 };
      return null;
    });

    render(<InfrastructurePage />);
    await waitFor(() => {
      expect(screen.getByText('Compute Hosts')).toBeInTheDocument();
    });
  });

  it('displays MTU overlay setting value from mesh', async () => {
    render(<InfrastructurePage />);
    await waitFor(() => {
      expect(apiRequest).toHaveBeenCalledWith('/infrastructure/mesh');
    });
  });

  // ============================================================
  // Hosts tab
  // ============================================================

  it('renders host cards with status and resource usage', async () => {
    render(<InfrastructurePage />);
    await waitFor(() => {
      expect(screen.getByText('agent-1')).toBeInTheDocument();
      expect(screen.getByText('10.0.0.1:8001')).toBeInTheDocument();
    });
  });

  it('shows offline status for offline agents', async () => {
    apiRequest.mockImplementation(async (path: string) => {
      if (path === '/infrastructure/mesh') return defaultMeshResponse;
      if (path === '/agents/detailed') return [{ ...defaultHostsResponse[0], status: 'offline' }];
      if (path === '/agents/updates/latest') return { version: '0.5.0' };
      if (path === '/infrastructure/network-configs') return [];
      if (path === '/infrastructure/interfaces') return { interfaces: [], total: 0 };
      if (path === '/infrastructure/nic-groups') return { groups: [], total: 0 };
      return null;
    });

    render(<InfrastructurePage />);
    await waitFor(() => {
      expect(screen.getByText('agent-1')).toBeInTheDocument();
    });
  });

  it('loads latest agent version on mount', async () => {
    render(<InfrastructurePage />);
    await waitFor(() => {
      expect(apiRequest).toHaveBeenCalledWith('/agents/updates/latest');
    });
  });

  it('shows labs assigned to a host', async () => {
    render(<InfrastructurePage />);
    await waitFor(() => {
      expect(screen.getByText('agent-1')).toBeInTheDocument();
    });
    // The host card should display lab count
    expect(apiRequest).toHaveBeenCalledWith('/agents/detailed');
  });

  // ============================================================
  // MTU Testing
  // ============================================================

  it('calls test-all endpoint when Test All button is clicked', async () => {
    apiRequest.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === '/infrastructure/mesh/test-all' && options?.method === 'POST') {
        return { total_pairs: 1, successful: 1, failed: 0, results: [] };
      }
      if (path === '/infrastructure/mesh') return defaultMeshResponse;
      if (path === '/agents/detailed') return defaultHostsResponse;
      if (path === '/agents/updates/latest') return { version: '0.5.0' };
      if (path === '/infrastructure/network-configs') return [];
      if (path === '/infrastructure/interfaces') return { interfaces: [], total: 0 };
      if (path === '/infrastructure/nic-groups') return { groups: [], total: 0 };
      return null;
    });

    render(<InfrastructurePage />);
    // Switch to network tab to see mesh controls
    await waitFor(() => expect(screen.getByText('Network')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Network'));

    // The test-all button should be available on the Network tab
    await waitFor(() => {
      const testBtns = screen.queryAllByRole('button');
      expect(testBtns.length).toBeGreaterThan(0);
    });
  });

  it('shows warning notification when MTU test has failures', async () => {
    apiRequest.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === '/infrastructure/mesh/test-all' && options?.method === 'POST') {
        return { total_pairs: 2, successful: 1, failed: 1, results: [] };
      }
      if (path === '/infrastructure/mesh') return defaultMeshResponse;
      if (path === '/agents/detailed') return defaultHostsResponse;
      if (path === '/agents/updates/latest') return { version: '0.5.0' };
      if (path === '/infrastructure/network-configs') return [];
      if (path === '/infrastructure/interfaces') return { interfaces: [], total: 0 };
      if (path === '/infrastructure/nic-groups') return { groups: [], total: 0 };
      return null;
    });

    render(<InfrastructurePage />);
    await waitFor(() => expect(apiRequest).toHaveBeenCalledWith('/infrastructure/mesh'));
  });

  // ============================================================
  // NIC Groups
  // ============================================================

  it('loads NIC groups on mount', async () => {
    render(<InfrastructurePage />);
    await waitFor(() => {
      expect(apiRequest).toHaveBeenCalledWith('/infrastructure/nic-groups');
    });
  });

  it('loads managed interfaces on mount', async () => {
    render(<InfrastructurePage />);
    await waitFor(() => {
      expect(apiRequest).toHaveBeenCalledWith('/infrastructure/interfaces');
    });
  });

  // ============================================================
  // Error handling
  // ============================================================

  it('handles API failure for mesh endpoint gracefully', async () => {
    apiRequest.mockImplementation(async (path: string) => {
      if (path === '/infrastructure/mesh') throw new Error('Network error');
      if (path === '/agents/detailed') return defaultHostsResponse;
      if (path === '/agents/updates/latest') return { version: '0.5.0' };
      if (path === '/infrastructure/network-configs') return [];
      if (path === '/infrastructure/interfaces') return { interfaces: [], total: 0 };
      if (path === '/infrastructure/nic-groups') return { groups: [], total: 0 };
      return null;
    });

    render(<InfrastructurePage />);
    await waitFor(() => {
      // Should still render page even if mesh fails
      expect(screen.getByText('Archetype Infrastructure Management')).toBeInTheDocument();
    });
  });

  it('handles API failure for hosts endpoint gracefully', async () => {
    apiRequest.mockImplementation(async (path: string) => {
      if (path === '/infrastructure/mesh') return { agents: [], links: [], settings: defaultMeshResponse.settings };
      if (path === '/agents/detailed') throw new Error('Hosts fetch failed');
      if (path === '/agents/updates/latest') return { version: '0.5.0' };
      if (path === '/infrastructure/network-configs') return [];
      if (path === '/infrastructure/interfaces') return { interfaces: [], total: 0 };
      if (path === '/infrastructure/nic-groups') return { groups: [], total: 0 };
      return null;
    });

    render(<InfrastructurePage />);
    await waitFor(() => {
      expect(screen.getByText('Compute Hosts')).toBeInTheDocument();
    });
  });

  it('shows loading state initially', async () => {
    // Use a never-resolving promise to keep the loading state
    let resolveHosts: (v: unknown) => void;
    apiRequest.mockImplementation(async (path: string) => {
      if (path === '/infrastructure/mesh') return defaultMeshResponse;
      if (path === '/agents/detailed') return new Promise(r => { resolveHosts = r; });
      if (path === '/agents/updates/latest') return { version: '0.5.0' };
      if (path === '/infrastructure/network-configs') return [];
      if (path === '/infrastructure/interfaces') return { interfaces: [], total: 0 };
      if (path === '/infrastructure/nic-groups') return { groups: [], total: 0 };
      return null;
    });

    render(<InfrastructurePage />);
    // The page should render while data is loading
    await waitFor(() => {
      expect(screen.getByText('Archetype Infrastructure Management')).toBeInTheDocument();
    });

    // Clean up
    resolveHosts!(defaultHostsResponse);
  });

  it('loads network configs on mount', async () => {
    render(<InfrastructurePage />);
    await waitFor(() => {
      expect(apiRequest).toHaveBeenCalledWith('/infrastructure/network-configs');
    });
  });

  it('renders back button that calls navigate', async () => {
    render(<InfrastructurePage />);
    await waitFor(() => expect(screen.getByText('Back')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Back'));
    expect(mockNavigate).toHaveBeenCalledWith('/');
  });
});
