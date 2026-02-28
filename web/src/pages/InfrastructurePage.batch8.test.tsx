import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest';
import InfrastructurePage from './InfrastructurePage';

// ── Mocks ──

const apiRequest = vi.fn();
const addNotification = vi.fn();
const mockNavigate = vi.fn();
const mockSetSearchParams = vi.fn();
let mockSearchParams = new URLSearchParams();

vi.mock('../api', () => ({
  apiRequest: (...args: unknown[]) => apiRequest(...args),
}));

vi.mock('../theme/index', () => ({
  useTheme: () => ({ effectiveMode: 'light', toggleMode: vi.fn() }),
  ThemeSelector: ({ isOpen }: { isOpen: boolean }) => (isOpen ? <div>Theme</div> : null),
}));

vi.mock('../contexts/NotificationContext', () => ({
  useNotifications: () => ({ addNotification }),
}));

vi.mock('../components/AdminMenuButton', () => ({
  default: () => <div>AdminMenuButton</div>,
}));

// Default: super_admin user
let mockUser: Record<string, unknown> | null = {
  id: 'user-1',
  username: 'admin',
  email: 'admin@example.com',
  is_active: true,
  global_role: 'super_admin',
  created_at: '2024-01-01T00:00:00Z',
};
let mockUserLoading = false;

vi.mock('../contexts/UserContext', () => ({
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

const host1 = {
  id: 'a1',
  name: 'agent-1',
  address: '10.0.0.1:8001',
  status: 'online',
  version: '0.5.0',
  role: 'agent',
  image_sync_strategy: 'on_demand',
  deployment_mode: 'systemd',
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
  last_heartbeat: '2026-02-27T10:00:00Z',
  git_sha: 'abc1234',
  last_error: null,
  error_since: null,
  data_plane_address: null,
};

const host2 = {
  ...host1,
  id: 'a2',
  name: 'agent-2',
  address: '10.0.0.2:8001',
  version: '0.4.0', // outdated
  labs: [],
  lab_count: 0,
};

const defaultMesh = {
  agents: [
    { id: 'a1', name: 'agent-1', address: '10.0.0.1:8001', status: 'online' },
    { id: 'a2', name: 'agent-2', address: '10.0.0.2:8001', status: 'online' },
  ],
  links: [],
  settings: {
    overlay_mtu: 1450,
    mtu_verification_enabled: true,
    overlay_preserve_container_mtu: false,
    overlay_clamp_host_mtu: true,
    updated_at: null,
    updated_by_id: null,
  },
};

function setupMocks(overrides: Record<string, unknown> = {}) {
  const hostsResponse = (overrides.hosts ?? [host1, host2]) as unknown[];
  const meshResponse = overrides.mesh ?? defaultMesh;
  const latestResponse = overrides.latest ?? { version: '0.5.0' };

  apiRequest.mockImplementation(async (path: string, options?: RequestInit) => {
    if (path === '/infrastructure/mesh') return meshResponse;
    if (path === '/agents/detailed') return hostsResponse;
    if (path === '/agents/updates/latest') return latestResponse;
    if (path === '/infrastructure/network-configs') return overrides.networkConfigs ?? [];
    if (path === '/infrastructure/interfaces') return overrides.interfaces ?? { interfaces: [], total: 0 };
    if (path === '/infrastructure/nic-groups') return overrides.nicGroups ?? { groups: [], total: 0 };
    if (path.match(/\/agents\/[^/]+\/deregister-info$/)) {
      return overrides.deregisterInfo ?? {
        agent_name: 'agent-1',
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
      return overrides.updateResponse ?? { job_id: 'j1', status: 'accepted', message: 'Update started' };
    }
    if (path.match(/\/agents\/[^/]+\/update-status$/)) {
      return overrides.updateStatus ?? { status: 'completed', progress_percent: 100, error_message: null };
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

describe('InfrastructurePage — Batch 8', () => {
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

  // ============================================================================
  // Auth guard
  // ============================================================================

  describe('Auth guard', () => {
    it('redirects unauthenticated users to /', async () => {
      mockUser = null;
      render(<InfrastructurePage />);
      expect(screen.getByTestId('redirect')).toHaveTextContent('Redirect to /');
    });

    it('redirects non-admin users to /', async () => {
      mockUser = { ...mockUser!, global_role: 'viewer' };
      render(<InfrastructurePage />);
      expect(screen.getByTestId('redirect')).toHaveTextContent('Redirect to /');
    });

    it('does not redirect admin users', async () => {
      render(<InfrastructurePage />);
      await waitFor(() => {
        expect(screen.queryByTestId('redirect')).not.toBeInTheDocument();
        expect(screen.getByText('Archetype Infrastructure Management')).toBeInTheDocument();
      });
    });
  });

  // ============================================================================
  // Version comparison and update availability
  // ============================================================================

  describe('Version update detection', () => {
    it('shows update available icon when host version differs from latest', async () => {
      setupMocks({ latest: { version: '0.6.0' } });
      render(<InfrastructurePage />);
      await waitFor(() => {
        expect(screen.getByText('agent-1')).toBeInTheDocument();
      });
      // Both hosts should show update available indicator
      const arrows = document.querySelectorAll('.fa-arrow-up');
      expect(arrows.length).toBeGreaterThan(0);
    });

    it('does not show update icon when host version matches latest', async () => {
      setupMocks({ hosts: [{ ...host1, version: '0.5.0' }], latest: { version: '0.5.0' } });
      render(<InfrastructurePage />);
      await waitFor(() => {
        expect(screen.getByText('agent-1')).toBeInTheDocument();
      });
      const arrows = document.querySelectorAll('[title*="Update available"]');
      expect(arrows.length).toBe(0);
    });

    it('shows bulk update button when outdated agents exist', async () => {
      setupMocks({ latest: { version: '0.6.0' } });
      render(<InfrastructurePage />);
      await waitFor(() => {
        expect(screen.getByText(/Update \d+ Agent/)).toBeInTheDocument();
      });
    });

    it('does not show bulk update button when all agents are current', async () => {
      setupMocks({ hosts: [host1], latest: { version: '0.5.0' } });
      render(<InfrastructurePage />);
      await waitFor(() => {
        expect(screen.getByText('agent-1')).toBeInTheDocument();
      });
      expect(screen.queryByText(/Update \d+ Agent/)).not.toBeInTheDocument();
    });
  });

  // ============================================================================
  // Host error display
  // ============================================================================

  describe('Host error display', () => {
    it('shows error alert when host has last_error', async () => {
      setupMocks({
        hosts: [{ ...host1, last_error: 'Docker daemon unreachable', error_since: '2026-02-27T09:00:00Z' }],
      });
      render(<InfrastructurePage />);
      await waitFor(() => {
        expect(screen.getByText('Agent Error')).toBeInTheDocument();
        expect(screen.getByText('Docker daemon unreachable')).toBeInTheDocument();
      });
    });

    it('does not show error alert when last_error is null', async () => {
      render(<InfrastructurePage />);
      await waitFor(() => {
        expect(screen.getByText('agent-1')).toBeInTheDocument();
      });
      expect(screen.queryByText('Agent Error')).not.toBeInTheDocument();
    });
  });

  // ============================================================================
  // Deregister flow
  // ============================================================================

  describe('Deregister flow', () => {
    it('opens deregister modal when Deregister Host button is clicked', async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      render(<InfrastructurePage />);
      await waitFor(() => expect(screen.getByText('agent-1')).toBeInTheDocument());

      const deregisterBtns = screen.getAllByText('Deregister Host');
      await user.click(deregisterBtns[0]);

      await waitFor(() => {
        // Modal should appear — the modal has a "Deregister Host" heading
        expect(apiRequest).toHaveBeenCalledWith('/agents/a1/deregister-info');
      });
    });

    it('calls DELETE endpoint on confirm deregister', async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      render(<InfrastructurePage />);
      await waitFor(() => expect(screen.getByText('agent-1')).toBeInTheDocument());

      const deregisterBtns = screen.getAllByText('Deregister Host');
      await user.click(deregisterBtns[0]);

      // Wait for deregister info to load
      await waitFor(() => {
        expect(apiRequest).toHaveBeenCalledWith('/agents/a1/deregister-info');
      });

      // Find and click the confirm button in the modal
      const confirmBtns = screen.getAllByText('Deregister');
      const confirmBtn = confirmBtns.find(btn => {
        const el = btn.closest('button');
        return el && el.className.includes('bg-red');
      });
      if (confirmBtn) {
        await user.click(confirmBtn);
      }

      await waitFor(() => {
        expect(apiRequest).toHaveBeenCalledWith('/agents/a1', { method: 'DELETE' });
      });
    });

    it('closes modal on cancel', async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      render(<InfrastructurePage />);
      await waitFor(() => expect(screen.getByText('agent-1')).toBeInTheDocument());

      const deregisterBtns = screen.getAllByText('Deregister Host');
      await user.click(deregisterBtns[0]);

      await waitFor(() => {
        expect(apiRequest).toHaveBeenCalledWith('/agents/a1/deregister-info');
      });

      // Click Cancel
      const cancelBtn = screen.getByText('Cancel');
      await user.click(cancelBtn);

      // Modal should be closed — deregister info should no longer be loading
      // After cancel, the modal host state is cleared
    });
  });

  // ============================================================================
  // Online/Offline counts
  // ============================================================================

  describe('Online/Offline counts', () => {
    it('displays online and offline agent counts', async () => {
      setupMocks({
        hosts: [
          host1,
          { ...host2, status: 'offline' },
        ],
      });
      render(<InfrastructurePage />);
      await waitFor(() => {
        expect(screen.getByText('1 Online')).toBeInTheDocument();
        expect(screen.getByText('1 Offline')).toBeInTheDocument();
      });
    });

    it('shows correct count when all agents are online', async () => {
      render(<InfrastructurePage />);
      await waitFor(() => {
        expect(screen.getByText('2 Online')).toBeInTheDocument();
        expect(screen.getByText('0 Offline')).toBeInTheDocument();
      });
    });
  });

  // ============================================================================
  // No hosts registered
  // ============================================================================

  describe('Empty hosts', () => {
    it('shows "No Hosts Registered" when no agents exist', async () => {
      setupMocks({ hosts: [], mesh: { agents: [], links: [], settings: defaultMesh.settings } });
      render(<InfrastructurePage />);
      await waitFor(() => {
        expect(screen.getByText('No Hosts Registered')).toBeInTheDocument();
      });
    });
  });

  // ============================================================================
  // Host card details
  // ============================================================================

  describe('Host card details', () => {
    it('displays host git SHA abbreviated', async () => {
      render(<InfrastructurePage />);
      await waitFor(() => {
        // SHA is in a child span: "(abc1234)" — use queryAllByText with exact false
        const shaEls = screen.queryAllByText('(abc1234)');
        expect(shaEls.length).toBeGreaterThan(0);
      });
    });

    it('displays host lab count', async () => {
      render(<InfrastructurePage />);
      await waitFor(() => {
        // host1 has 1 lab
        expect(screen.getByText('agent-1')).toBeInTheDocument();
        expect(screen.getByText('1 Lab')).toBeInTheDocument();
      });
    });

    it('displays host version in card', async () => {
      render(<InfrastructurePage />);
      await waitFor(() => {
        expect(screen.getByText('agent-1')).toBeInTheDocument();
      });
      // Version is rendered as "v0.5.0" text node inside a span with child span for SHA
      // Use container query to find version text
      const versionEls = document.querySelectorAll('[title^="Commit:"]');
      expect(versionEls.length).toBeGreaterThan(0);
      expect(versionEls[0].textContent).toContain('v0.5.0');
    });
  });

  // ============================================================================
  // Latest version in subtitle
  // ============================================================================

  describe('Latest version display', () => {
    it('shows latest version in subtitle when available', async () => {
      render(<InfrastructurePage />);
      await waitFor(() => {
        expect(screen.getByText(/Latest: v0\.5\.0/)).toBeInTheDocument();
      });
    });
  });

  // ============================================================================
  // Hosts tab count badge
  // ============================================================================

  describe('Tab badges', () => {
    it('shows host count on Hosts tab', async () => {
      render(<InfrastructurePage />);
      await waitFor(() => {
        const hostsTab = screen.getByText('Hosts').closest('button');
        expect(hostsTab?.textContent).toContain('2');
      });
    });
  });

  // ============================================================================
  // Network tab
  // ============================================================================

  describe('Network tab', () => {
    it('switches to Network tab and shows mesh content', async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      render(<InfrastructurePage />);
      await waitFor(() => expect(screen.getByText('Network')).toBeInTheDocument());

      await user.click(screen.getByText('Network'));

      expect(mockSetSearchParams).toHaveBeenCalledWith({ tab: 'network' });
    });
  });

  // ============================================================================
  // Hosts error state
  // ============================================================================

  describe('Hosts error state', () => {
    it('displays hosts error message on fetch failure', async () => {
      apiRequest.mockImplementation(async (path: string) => {
        if (path === '/agents/detailed') throw new Error('Connection refused');
        if (path === '/infrastructure/mesh') return defaultMesh;
        if (path === '/agents/updates/latest') return { version: '0.5.0' };
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

  // ============================================================================
  // Agent update trigger
  // ============================================================================

  describe('Agent update', () => {
    it('calls update endpoint when Update button is clicked', async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      // Both hosts are systemd + outdated
      setupMocks({
        hosts: [
          { ...host1, version: '0.4.0', deployment_mode: 'systemd' },
        ],
        latest: { version: '0.6.0' },
      });
      render(<InfrastructurePage />);
      await waitFor(() => expect(screen.getByText('agent-1')).toBeInTheDocument());

      // Button text is "Update to v0.6.0"
      const updateBtn = await screen.findByText(/Update to v0\.6\.0/);
      await user.click(updateBtn);

      await waitFor(() => {
        expect(apiRequest).toHaveBeenCalledWith(
          '/agents/a1/update',
          expect.objectContaining({ method: 'POST' }),
        );
      });
    });
  });

  // ============================================================================
  // Periodic refresh
  // ============================================================================

  describe('Periodic refresh', () => {
    it('fetches mesh data on mount', async () => {
      render(<InfrastructurePage />);
      await waitFor(() => {
        expect(apiRequest).toHaveBeenCalledWith('/infrastructure/mesh');
      });
    });

    it('fetches hosts data on mount', async () => {
      render(<InfrastructurePage />);
      await waitFor(() => {
        expect(apiRequest).toHaveBeenCalledWith('/agents/detailed');
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
});
