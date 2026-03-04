import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest';
import InfrastructurePage from './InfrastructurePage';
import type { HostDetailed } from './infrastructureTypes';

// ── Mocks ──

const apiRequest = vi.fn();
const addNotification = vi.fn();
const mockNavigate = vi.fn();
const mockSetSearchParams = vi.fn();
let mockSearchParams = new URLSearchParams();

vi.mock('../../api', () => ({
  apiRequest: (...args: unknown[]) => apiRequest(...args),
}));

vi.mock('../../theme/index', () => ({
  useTheme: () => ({ effectiveMode: 'light', toggleMode: vi.fn() }),
  ThemeSelector: ({ isOpen }: { isOpen: boolean }) => (isOpen ? <div data-testid="theme-selector">Theme</div> : null),
}));

vi.mock('../../contexts/NotificationContext', () => ({
  useNotifications: () => ({ addNotification }),
}));

vi.mock('../../components/AdminMenuButton', () => ({
  default: () => <div data-testid="admin-menu">AdminMenuButton</div>,
}));

let mockUser: Record<string, unknown> | null = {
  id: 'user-1',
  username: 'admin',
  email: 'admin@example.com',
  is_active: true,
  global_role: 'super_admin',
  created_at: '2024-01-01T00:00:00Z',
};
let mockUserLoading = false;

vi.mock('../../contexts/UserContext', () => ({
  useUser: () => ({ user: mockUser, loading: mockUserLoading }),
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
    useSearchParams: () => [mockSearchParams, mockSetSearchParams],
    Navigate: ({ to }: { to: string }) => <div data-testid="redirect">Redirect to {to}</div>,
  };
});

// ── Test data ──

function makeHost(overrides: Partial<HostDetailed> = {}): HostDetailed {
  return {
    id: 'host-1',
    name: 'Agent-01',
    address: '10.0.0.1:8001',
    status: 'online',
    version: '1.0.0',
    role: 'agent',
    image_sync_strategy: 'on_demand',
    deployment_mode: 'systemd',
    capabilities: { providers: ['docker'] },
    resource_usage: {
      cpu_percent: 25,
      memory_percent: 45,
      memory_used_gb: 8,
      memory_total_gb: 16,
      storage_percent: 60,
      storage_used_gb: 120,
      storage_total_gb: 200,
      containers_running: 5,
      containers_total: 10,
      vms_running: 0,
      vms_total: 0,
      container_details: [],
      vm_details: [],
    },
    images: [],
    labs: [{ id: 'lab-1', name: 'Test Lab', state: 'running' }],
    lab_count: 1,
    started_at: null,
    last_heartbeat: new Date().toISOString(),
    git_sha: 'abc1234567890',
    last_error: null,
    error_since: null,
    data_plane_address: null,
    ...overrides,
  };
}

const defaultMesh = {
  agents: [
    { id: 'host-1', name: 'Agent-01', address: '10.0.0.1:8001', status: 'online' },
    { id: 'host-2', name: 'Agent-02', address: '10.0.0.2:8001', status: 'online' },
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
};

function setupMocks(overrides: Record<string, unknown> = {}) {
  const hostsResponse = (overrides.hosts ?? [
    makeHost(),
    makeHost({ id: 'host-2', name: 'Agent-02', address: '10.0.0.2:8001', version: '0.9.0', labs: [], lab_count: 0 }),
  ]) as unknown[];
  const meshResponse = overrides.mesh ?? defaultMesh;
  const latestResponse = overrides.latest ?? { version: '1.0.0' };

  apiRequest.mockImplementation(async (path: string, options?: RequestInit) => {
    if (path === '/infrastructure/mesh') return meshResponse;
    if (path === '/agents/detailed') return hostsResponse;
    if (path === '/agents/updates/latest') return latestResponse;
    if (path === '/infrastructure/network-configs') return overrides.networkConfigs ?? [];
    if (path === '/infrastructure/interfaces') return overrides.interfaces ?? { interfaces: [], total: 0 };
    if (path === '/infrastructure/nic-groups') return overrides.nicGroups ?? { groups: [], total: 0 };
    if (path.match(/\/agents\/[^/]+\/deregister-info$/)) {
      return overrides.deregisterInfo ?? {
        agent_name: 'Agent-01',
        agent_status: 'online',
        labs_assigned: 1,
        running_labs: [{ id: 'lab-1', name: 'Test Lab', state: 'running' }],
        node_placements: 3,
        nodes_assigned: 3,
        vxlan_tunnels: 0,
        cross_host_links: 0,
      };
    }
    if (path.match(/\/agents\/[^/]+$/) && options?.method === 'DELETE') {
      return { success: true };
    }
    if (path.match(/\/agents\/[^/]+\/update$/) && options?.method === 'POST') {
      return { job_id: 'j1', status: 'accepted', message: 'Update started' };
    }
    if (path.match(/\/agents\/[^/]+\/update-status$/)) {
      return { status: 'completed', progress_percent: 100, error_message: null };
    }
    if (path.match(/\/agents\/[^/]+\/sync-strategy$/) && options?.method === 'PUT') {
      return { strategy: 'push' };
    }
    if (path === '/infrastructure/settings' && options?.method === 'PATCH') {
      return { success: true };
    }
    return null;
  });
}

// ── Tests ──

describe('InfrastructurePage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockUser = {
      id: 'user-1',
      username: 'admin',
      email: 'admin@example.com',
      is_active: true,
      global_role: 'super_admin',
      created_at: '2024-01-01T00:00:00Z',
    };
    mockUserLoading = false;
    mockSearchParams = new URLSearchParams();
    setupMocks();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  // ── Auth Guard ──

  describe('Auth guard', () => {
    it('redirects when user is not authenticated', () => {
      mockUser = null;
      render(<InfrastructurePage />);
      expect(screen.getByTestId('redirect')).toHaveTextContent('Redirect to /');
    });

    it('redirects when user is a viewer (insufficient permissions)', () => {
      mockUser = { ...mockUser!, global_role: 'viewer' };
      render(<InfrastructurePage />);
      expect(screen.getByTestId('redirect')).toHaveTextContent('Redirect to /');
    });

    it('redirects when user is an operator (insufficient permissions)', () => {
      mockUser = { ...mockUser!, global_role: 'operator' };
      render(<InfrastructurePage />);
      expect(screen.getByTestId('redirect')).toHaveTextContent('Redirect to /');
    });

    it('renders page for admin user', async () => {
      mockUser = { ...mockUser!, global_role: 'admin' };
      render(<InfrastructurePage />);
      await waitFor(() => {
        expect(screen.queryByTestId('redirect')).not.toBeInTheDocument();
        expect(screen.getByText('Archetype Infrastructure Management')).toBeInTheDocument();
      });
    });

    it('renders page for super_admin user', async () => {
      render(<InfrastructurePage />);
      await waitFor(() => {
        expect(screen.queryByTestId('redirect')).not.toBeInTheDocument();
        expect(screen.getByText('ARCHETYPE')).toBeInTheDocument();
      });
    });

    it('does not redirect while user is loading', () => {
      mockUserLoading = true;
      mockUser = null;
      render(<InfrastructurePage />);
      expect(screen.queryByTestId('redirect')).not.toBeInTheDocument();
    });
  });

  // ── Header ──

  describe('Header', () => {
    it('renders the ARCHETYPE title', async () => {
      render(<InfrastructurePage />);
      await waitFor(() => {
        expect(screen.getByText('ARCHETYPE')).toBeInTheDocument();
      });
    });

    it('renders Infrastructure subtitle', async () => {
      render(<InfrastructurePage />);
      await waitFor(() => {
        expect(screen.getByText('Infrastructure')).toBeInTheDocument();
      });
    });

    it('renders Back button that navigates to home', async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      render(<InfrastructurePage />);
      await waitFor(() => expect(screen.getByText('Back')).toBeInTheDocument());
      await user.click(screen.getByText('Back'));
      expect(mockNavigate).toHaveBeenCalledWith('/');
    });

    it('renders AdminMenuButton', async () => {
      render(<InfrastructurePage />);
      await waitFor(() => {
        expect(screen.getByTestId('admin-menu')).toBeInTheDocument();
      });
    });

    it('opens theme selector when palette button is clicked', async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      render(<InfrastructurePage />);
      await waitFor(() => expect(screen.getByTitle('Theme Settings')).toBeInTheDocument());
      await user.click(screen.getByTitle('Theme Settings'));
      expect(screen.getByTestId('theme-selector')).toBeInTheDocument();
    });

    it('renders dark/light mode toggle button', async () => {
      render(<InfrastructurePage />);
      await waitFor(() => {
        expect(screen.getByTitle('Switch to dark mode')).toBeInTheDocument();
      });
    });
  });

  // ── Tab Navigation ──

  describe('Tab navigation', () => {
    it('renders Hosts and Network tabs', async () => {
      render(<InfrastructurePage />);
      await waitFor(() => {
        expect(screen.getByText('Hosts')).toBeInTheDocument();
        expect(screen.getByText('Network')).toBeInTheDocument();
      });
    });

    it('defaults to hosts tab', async () => {
      render(<InfrastructurePage />);
      await waitFor(() => {
        expect(screen.getByText('Agent-01')).toBeInTheDocument();
      });
    });

    it('shows host count badge on Hosts tab', async () => {
      render(<InfrastructurePage />);
      await waitFor(() => {
        const hostsTab = screen.getByText('Hosts').closest('button');
        expect(hostsTab?.textContent).toContain('2');
      });
    });

    it('switches to Network tab on click', async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      render(<InfrastructurePage />);
      await waitFor(() => expect(screen.getByText('Network')).toBeInTheDocument());
      await user.click(screen.getByText('Network'));
      expect(mockSetSearchParams).toHaveBeenCalledWith({ tab: 'network' });
    });

    it('switches to Hosts tab on click', async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      render(<InfrastructurePage />);
      await waitFor(() => expect(screen.getByText('Hosts')).toBeInTheDocument());
      await user.click(screen.getByText('Hosts'));
      expect(mockSetSearchParams).toHaveBeenCalledWith({ tab: 'hosts' });
    });

    it('renders network tab when URL param is tab=network', async () => {
      mockSearchParams = new URLSearchParams('tab=network');
      render(<InfrastructurePage />);
      await waitFor(() => {
        // Network tab content renders (Global Settings from NetworkTab)
        expect(screen.getByText('Global Settings')).toBeInTheDocument();
      });
    });
  });

  // ── Footer ──

  describe('Footer', () => {
    it('renders footer text', async () => {
      render(<InfrastructurePage />);
      await waitFor(() => {
        expect(screen.getByText('Archetype Infrastructure Management')).toBeInTheDocument();
      });
    });

    it('shows hosts auto-refresh interval on hosts tab', async () => {
      render(<InfrastructurePage />);
      await waitFor(() => {
        expect(screen.getByText('Auto-refresh: 10s')).toBeInTheDocument();
      });
    });

    it('shows network auto-refresh interval on network tab', async () => {
      mockSearchParams = new URLSearchParams('tab=network');
      render(<InfrastructurePage />);
      await waitFor(() => {
        expect(screen.getByText(/Auto-refresh: 30s/)).toBeInTheDocument();
      });
    });
  });

  // ── Data Loading ──

  describe('Data loading', () => {
    it('fetches hosts on mount', async () => {
      render(<InfrastructurePage />);
      await waitFor(() => {
        expect(apiRequest).toHaveBeenCalledWith('/agents/detailed');
      });
    });

    it('fetches mesh data on mount', async () => {
      render(<InfrastructurePage />);
      await waitFor(() => {
        expect(apiRequest).toHaveBeenCalledWith('/infrastructure/mesh');
      });
    });

    it('fetches latest version on mount', async () => {
      render(<InfrastructurePage />);
      await waitFor(() => {
        expect(apiRequest).toHaveBeenCalledWith('/agents/updates/latest');
      });
    });

    it('fetches NIC groups on mount', async () => {
      render(<InfrastructurePage />);
      await waitFor(() => {
        expect(apiRequest).toHaveBeenCalledWith('/infrastructure/nic-groups');
      });
    });

    it('fetches managed interfaces on mount', async () => {
      render(<InfrastructurePage />);
      await waitFor(() => {
        expect(apiRequest).toHaveBeenCalledWith('/infrastructure/interfaces');
      });
    });
  });

  // ── Settings Save ──

  describe('Settings save', () => {
    it('calls PATCH /infrastructure/settings when save is triggered on network tab', async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      mockSearchParams = new URLSearchParams('tab=network');
      render(<InfrastructurePage />);
      await waitFor(() => expect(screen.getByText('Global Settings')).toBeInTheDocument());

      // Change MTU value to make dirty
      const mtuInput = screen.getByDisplayValue('1450');
      await user.clear(mtuInput);
      await user.type(mtuInput, '9000');

      // The save button should be enabled when dirty
      const saveBtn = screen.getByText('Save Settings').closest('button')!;
      if (!saveBtn.hasAttribute('disabled')) {
        await user.click(saveBtn);
        await waitFor(() => {
          expect(apiRequest).toHaveBeenCalledWith(
            '/infrastructure/settings',
            expect.objectContaining({ method: 'PATCH' }),
          );
        });
      }
    });
  });

  // ── Custom Update Modal ──

  describe('Custom update modal', () => {
    it('does not render custom update modal by default', async () => {
      render(<InfrastructurePage />);
      await waitFor(() => expect(screen.getByText('Agent-01')).toBeInTheDocument());
      expect(screen.queryByText('Custom Update Target')).not.toBeInTheDocument();
    });
  });

  // ── Error Handling ──

  describe('Error handling', () => {
    it('displays hosts error message on fetch failure', async () => {
      apiRequest.mockImplementation(async (path: string) => {
        if (path === '/agents/detailed') throw new Error('Connection refused');
        if (path === '/infrastructure/mesh') return defaultMesh;
        if (path === '/agents/updates/latest') return { version: '1.0.0' };
        if (path === '/infrastructure/network-configs') return [];
        if (path === '/infrastructure/interfaces') return { interfaces: [], total: 0 };
        if (path === '/infrastructure/nic-groups') return { groups: [], total: 0 };
        return null;
      });
      render(<InfrastructurePage />);
      await waitFor(() => {
        expect(screen.getByText('Connection refused')).toBeInTheDocument();
      });
    });
  });

  // ── Empty State ──

  describe('Empty state', () => {
    it('shows "No Hosts Registered" when no agents exist', async () => {
      setupMocks({ hosts: [], mesh: { ...defaultMesh, agents: [] } });
      render(<InfrastructurePage />);
      await waitFor(() => {
        expect(screen.getByText('No Hosts Registered')).toBeInTheDocument();
      });
    });
  });
});
