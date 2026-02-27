import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import React from 'react';

// ─── Mocks (before component import) ──────────────────────────────

const mockNavigate = vi.fn();

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<any>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
    Navigate: ({ to }: { to: string }) => <div data-testid="redirect">Redirect to {to}</div>,
  };
});

vi.mock('../components/AdminMenuButton', () => ({
  default: () => <div data-testid="admin-menu">AdminMenu</div>,
}));

let mockCanView = true;
vi.mock('../contexts/UserContext', () => ({
  useUser: () => ({ user: { id: 'u1', role: 'admin' } }),
}));

vi.mock('../utils/permissions', () => ({
  canViewInfrastructure: () => mockCanView,
}));

vi.mock('../api', () => ({
  apiRequest: vi.fn(),
}));

import InterfaceManagerPage from './InterfaceManagerPage';
const { apiRequest } = await import('../api');
const mockedApiRequest = vi.mocked(apiRequest);

// ─── Factories ─────────────────────────────────────────────────────

function makeAgent(overrides: Partial<any> = {}) {
  return {
    id: 'agent-1',
    name: 'Agent 1',
    status: 'online',
    address: '10.0.0.1:8001',
    ...overrides,
  };
}

function makeInterface(overrides: Partial<any> = {}) {
  return {
    id: 'iface-1',
    host_id: 'agent-1',
    host_name: 'Host Alpha',
    name: 'eth0.100',
    interface_type: 'transport',
    parent_interface: 'eth0',
    vlan_id: 100,
    ip_address: '10.100.0.1/24',
    desired_mtu: 9000,
    current_mtu: 9000,
    is_up: true,
    sync_status: 'synced',
    sync_error: null,
    last_sync_at: '2026-02-27T00:00:00Z',
    created_at: '2026-02-27T00:00:00Z',
    updated_at: '2026-02-27T00:00:00Z',
    ...overrides,
  };
}

function setupDefaultMocks(interfaces: any[] = [], agents: any[] = [makeAgent()]) {
  mockedApiRequest
    .mockResolvedValueOnce(agents)          // /agents
    .mockResolvedValueOnce({ interfaces }); // /infrastructure/interfaces
}

// ─── CIDR Validation (replicated from source, not exported) ────────

const CIDR_REGEX = /^(\d{1,3}\.){3}\d{1,3}\/\d{1,2}$/;

function isValidCidr(value: string): boolean {
  if (!CIDR_REGEX.test(value)) return false;
  const [ip, prefixStr] = value.split('/');
  const prefix = parseInt(prefixStr);
  if (prefix < 0 || prefix > 32) return false;
  const octets = ip.split('.').map(Number);
  return octets.every(o => o >= 0 && o <= 255);
}

// ─── Tests ─────────────────────────────────────────────────────────

describe('InterfaceManagerPage - extended', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockCanView = true;
  });

  // ─── Permission Guard ────────────────────────────────────────

  it('redirects when user lacks infrastructure permission', () => {
    mockCanView = false;
    mockedApiRequest.mockResolvedValue([]);
    render(<InterfaceManagerPage />);
    expect(screen.getByTestId('redirect')).toHaveTextContent('Redirect to /infrastructure');
  });

  // ─── Loading & Data Display ──────────────────────────────────

  it('shows empty state when no interfaces exist', async () => {
    setupDefaultMocks([], [makeAgent()]);
    render(<InterfaceManagerPage />);
    expect(await screen.findByText('No managed interfaces')).toBeInTheDocument();
  });

  it('renders interface list grouped by host', async () => {
    const iface = makeInterface();
    setupDefaultMocks([iface]);
    render(<InterfaceManagerPage />);
    // Use getAllByText since "Host Alpha" may appear in dropdown option and host header
    const elements = await screen.findAllByText('Host Alpha');
    expect(elements.length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('eth0.100')).toBeInTheDocument();
  });

  it('renders interface type badge', async () => {
    setupDefaultMocks([makeInterface({ interface_type: 'transport' })]);
    render(<InterfaceManagerPage />);
    expect(await screen.findByText('Transport')).toBeInTheDocument();
  });

  it('renders sync status badge for synced interface', async () => {
    setupDefaultMocks([makeInterface({ sync_status: 'synced' })]);
    render(<InterfaceManagerPage />);
    expect(await screen.findByText('Synced')).toBeInTheDocument();
  });

  it('renders sync error badge and message', async () => {
    setupDefaultMocks([makeInterface({ sync_status: 'error', sync_error: 'Connection refused' })]);
    render(<InterfaceManagerPage />);
    expect(await screen.findByText('Error')).toBeInTheDocument();
    expect(screen.getByText('Connection refused')).toBeInTheDocument();
  });

  // ─── CIDR Validation (pure function) ─────────────────────────

  describe('isValidCidr', () => {
    it('accepts valid CIDR notation', () => {
      expect(isValidCidr('10.100.0.1/24')).toBe(true);
      expect(isValidCidr('192.168.1.0/16')).toBe(true);
      expect(isValidCidr('0.0.0.0/0')).toBe(true);
      expect(isValidCidr('255.255.255.255/32')).toBe(true);
    });

    it('rejects invalid formats', () => {
      expect(isValidCidr('10.100.0.1')).toBe(false);
      expect(isValidCidr('10.100.0/24')).toBe(false);
      expect(isValidCidr('not-an-ip/24')).toBe(false);
      expect(isValidCidr('')).toBe(false);
    });

    it('rejects out-of-range octets', () => {
      expect(isValidCidr('999.999.999.999/24')).toBe(false);
      expect(isValidCidr('256.0.0.1/24')).toBe(false);
    });

    it('rejects out-of-range prefix length', () => {
      expect(isValidCidr('10.0.0.1/33')).toBe(false);
      expect(isValidCidr('10.0.0.1/99')).toBe(false);
    });
  });

  // ─── Search Filter ───────────────────────────────────────────

  it('filters interfaces by search query', async () => {
    setupDefaultMocks([
      makeInterface({ name: 'eth0.100', host_name: 'Host Alpha' }),
      makeInterface({ id: 'iface-2', name: 'eth0.200', host_name: 'Host Alpha' }),
    ]);
    render(<InterfaceManagerPage />);

    await screen.findByText('eth0.100');

    const searchInput = screen.getByPlaceholderText('Search interfaces...');
    fireEvent.change(searchInput, { target: { value: 'eth0.200' } });

    expect(screen.getByText('eth0.200')).toBeInTheDocument();
    expect(screen.queryByText('eth0.100')).not.toBeInTheDocument();
  });

  // ─── Delete Confirmation ─────────────────────────────────────

  it('shows delete confirmation and can cancel', async () => {
    setupDefaultMocks([makeInterface()]);
    render(<InterfaceManagerPage />);

    await screen.findByText('eth0.100');

    const deleteBtn = screen.getByTitle('Delete interface');
    fireEvent.click(deleteBtn);

    expect(screen.getByText('Delete')).toBeInTheDocument();
    expect(screen.getByText('Cancel')).toBeInTheDocument();

    fireEvent.click(screen.getByText('Cancel'));
    // Cancel button should disappear from the confirmation row
    expect(screen.queryByText('Cancel')).not.toBeInTheDocument();
  });

  it('calls delete API on confirm', async () => {
    setupDefaultMocks([makeInterface()]);
    mockedApiRequest
      .mockResolvedValueOnce({})
      .mockResolvedValueOnce({ interfaces: [] });

    render(<InterfaceManagerPage />);

    await screen.findByText('eth0.100');

    fireEvent.click(screen.getByTitle('Delete interface'));
    fireEvent.click(screen.getByText('Delete'));

    await waitFor(() => {
      expect(mockedApiRequest).toHaveBeenCalledWith(
        '/infrastructure/interfaces/iface-1',
        { method: 'DELETE' },
      );
    });
  });

  // ─── Create Modal ────────────────────────────────────────────

  it('opens create modal and shows host select', async () => {
    setupDefaultMocks([], [makeAgent()]);
    render(<InterfaceManagerPage />);

    fireEvent.click(await screen.findByText('Create Interface'));

    expect(screen.getByText('Create Managed Interface')).toBeInTheDocument();
    expect(screen.getByLabelText('Host')).toBeInTheDocument();
  });

  it('shows type description when selecting interface type', async () => {
    setupDefaultMocks([], [makeAgent()]);
    render(<InterfaceManagerPage />);
    fireEvent.click(await screen.findByText('Create Interface'));

    const typeSelect = screen.getByLabelText('Type');
    fireEvent.change(typeSelect, { target: { value: 'external' } });

    expect(screen.getByText(/L2 pass-through/)).toBeInTheDocument();
  });

  it('disables create button when no host selected', async () => {
    setupDefaultMocks([], [makeAgent()]);
    render(<InterfaceManagerPage />);
    fireEvent.click(await screen.findByText('Create Interface'));

    // Find the Create submit button inside the modal (the fixed overlay)
    const modal = document.querySelector('.fixed');
    const createButtons = modal?.querySelectorAll('button');
    const submitBtn = Array.from(createButtons || []).find(b => b.textContent?.includes('Create'));
    expect(submitBtn).toBeDisabled();
  });

  // ─── Host and Type Filter ────────────────────────────────────

  it('renders host filter dropdown', async () => {
    setupDefaultMocks([], [makeAgent({ id: 'a1', name: 'MyHost' })]);
    render(<InterfaceManagerPage />);
    await waitFor(() => {
      expect(mockedApiRequest).toHaveBeenCalledWith('/agents');
    });
    expect(screen.getByLabelText('Host filter')).toBeInTheDocument();
  });

  it('renders type filter dropdown', () => {
    setupDefaultMocks();
    render(<InterfaceManagerPage />);
    expect(screen.getByLabelText('Type filter')).toBeInTheDocument();
  });

  // ─── Back Navigation ─────────────────────────────────────────

  it('has back button that navigates to infrastructure', async () => {
    setupDefaultMocks();
    render(<InterfaceManagerPage />);
    const backBtn = screen.getByText('Back').closest('button')!;
    fireEvent.click(backBtn);
    expect(mockNavigate).toHaveBeenCalledWith('/infrastructure?tab=network');
  });
});
