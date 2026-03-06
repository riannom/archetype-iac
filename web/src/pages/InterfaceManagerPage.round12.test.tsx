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
const mockedApi = vi.mocked(apiRequest);

// ─── Factories ─────────────────────────────────────────────────────

function makeAgent(overrides: Partial<any> = {}) {
  return {
    id: 'agent-1',
    name: 'Agent Alpha',
    status: 'online',
    address: '10.0.0.1:8001',
    ...overrides,
  };
}

function makeIface(overrides: Partial<any> = {}) {
  return {
    id: 'iface-1',
    host_id: 'agent-1',
    host_name: 'Agent Alpha',
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
    last_sync_at: '2026-03-01T00:00:00Z',
    created_at: '2026-03-01T00:00:00Z',
    updated_at: '2026-03-01T00:00:00Z',
    ...overrides,
  };
}

/** Sets up the two initial API calls: /agents and /infrastructure/interfaces */
function setupMocks(interfaces: any[] = [], agents: any[] = [makeAgent()]) {
  mockedApi
    .mockResolvedValueOnce(agents)                 // GET /agents
    .mockResolvedValueOnce({ interfaces });         // GET /infrastructure/interfaces
}

// ─── Tests ─────────────────────────────────────────────────────────

describe('InterfaceManagerPage — round 12', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockCanView = true;
  });

  // ─── Permission Guard ─────────────────────────────────────────

  it('redirects to /infrastructure when user lacks permission', () => {
    mockCanView = false;
    mockedApi.mockResolvedValue([]);
    render(<InterfaceManagerPage />);
    expect(screen.getByTestId('redirect')).toHaveTextContent('/infrastructure');
  });

  // ─── Loading & Empty States ───────────────────────────────────

  it('shows spinner while loading', () => {
    // Never resolve — keep loading forever
    mockedApi.mockReturnValue(new Promise(() => {}));
    render(<InterfaceManagerPage />);
    expect(document.querySelector('.fa-spinner')).toBeInTheDocument();
  });

  it('shows empty state message when no interfaces returned', async () => {
    setupMocks([], [makeAgent()]);
    render(<InterfaceManagerPage />);
    expect(await screen.findByText('No managed interfaces')).toBeInTheDocument();
    expect(screen.getByText(/Create a transport or external/)).toBeInTheDocument();
  });

  // ─── Interface List Rendering ─────────────────────────────────

  it('displays interface name, IP, VLAN, parent, and type badge', async () => {
    setupMocks([makeIface()]);
    render(<InterfaceManagerPage />);

    expect(await screen.findByText('eth0.100')).toBeInTheDocument();
    expect(screen.getByText('10.100.0.1/24')).toBeInTheDocument();
    expect(screen.getByText('100')).toBeInTheDocument();
    expect(screen.getByText('eth0')).toBeInTheDocument();
    const badges = await screen.findAllByText('Transport');
    expect(badges.length).toBeGreaterThanOrEqual(1);
  });

  it('groups interfaces by host and shows host header with count', async () => {
    setupMocks([
      makeIface({ id: 'i1', name: 'eth0.100' }),
      makeIface({ id: 'i2', name: 'eth0.200' }),
    ]);
    render(<InterfaceManagerPage />);
    await screen.findByText('eth0.100');
    expect(screen.getByText('(2 interfaces)')).toBeInTheDocument();
  });

  it('shows singular "interface" when only one exists on a host', async () => {
    setupMocks([makeIface()]);
    render(<InterfaceManagerPage />);
    await screen.findByText('eth0.100');
    expect(screen.getByText('(1 interface)')).toBeInTheDocument();
  });

  it('renders multiple host groups separately', async () => {
    setupMocks([
      makeIface({ id: 'i1', host_id: 'agent-1', host_name: 'Host A', name: 'eth0.10' }),
      makeIface({ id: 'i2', host_id: 'agent-2', host_name: 'Host B', name: 'eth0.20' }),
    ], [makeAgent({ id: 'agent-1' }), makeAgent({ id: 'agent-2', name: 'Agent Beta' })]);
    render(<InterfaceManagerPage />);
    expect(await screen.findByText('Host A')).toBeInTheDocument();
    expect(screen.getByText('Host B')).toBeInTheDocument();
  });

  // ─── Sync Status Badges ───────────────────────────────────────

  it.each([
    ['synced', 'Synced'],
    ['mismatch', 'Mismatch'],
    ['error', 'Error'],
    ['unconfigured', 'Pending'],
    ['unknown-status', 'unknown-status'],
  ])('renders sync badge for status "%s" as "%s"', async (status, label) => {
    setupMocks([makeIface({ sync_status: status })]);
    render(<InterfaceManagerPage />);
    expect(await screen.findByText(label)).toBeInTheDocument();
  });

  it('shows sync_error text next to error badge', async () => {
    setupMocks([makeIface({ sync_status: 'error', sync_error: 'OVS timeout' })]);
    render(<InterfaceManagerPage />);
    expect(await screen.findByText('OVS timeout')).toBeInTheDocument();
  });

  // ─── Type Badges ──────────────────────────────────────────────

  it.each([
    ['transport', 'Transport'],
    ['external', 'External'],
    ['custom', 'Custom'],
  ])('renders type badge for "%s" as "%s"', async (type, label) => {
    setupMocks([makeIface({ interface_type: type })]);
    render(<InterfaceManagerPage />);
    expect(await screen.findByText(label)).toBeInTheDocument();
  });

  // ─── Up/Down Indicator ────────────────────────────────────────

  it('shows green dot for is_up=true and grey dot for is_up=false', async () => {
    setupMocks([
      makeIface({ id: 'i-up', name: 'up-iface', is_up: true }),
      makeIface({ id: 'i-down', name: 'down-iface', is_up: false }),
    ]);
    render(<InterfaceManagerPage />);
    await screen.findByText('up-iface');

    const upRow = screen.getByText('up-iface').closest('tr')!;
    expect(upRow.querySelector('.bg-green-500')).toBeInTheDocument();

    const downRow = screen.getByText('down-iface').closest('tr')!;
    expect(downRow.querySelector('.bg-stone-400')).toBeInTheDocument();
  });

  // ─── Null/Missing Field Rendering ─────────────────────────────

  it('renders dashes for null parent, vlan, and ip', async () => {
    setupMocks([makeIface({
      parent_interface: null,
      vlan_id: null,
      ip_address: null,
    })]);
    render(<InterfaceManagerPage />);
    await screen.findByText('eth0.100');
    // Each null field renders as '-'
    const dashes = screen.getAllByText('-');
    expect(dashes.length).toBeGreaterThanOrEqual(3);
  });

  // ─── Search / Filter ──────────────────────────────────────────

  it('filters by name search query', async () => {
    setupMocks([
      makeIface({ id: 'i1', name: 'eth0.100' }),
      makeIface({ id: 'i2', name: 'vlan200' }),
    ]);
    render(<InterfaceManagerPage />);
    await screen.findByText('eth0.100');

    fireEvent.change(screen.getByPlaceholderText('Search interfaces...'), {
      target: { value: 'vlan' },
    });

    expect(screen.getByText('vlan200')).toBeInTheDocument();
    expect(screen.queryByText('eth0.100')).not.toBeInTheDocument();
  });

  it('filters by host_name in search', async () => {
    setupMocks([
      makeIface({ id: 'i1', name: 'a', host_name: 'HostAlpha' }),
      makeIface({ id: 'i2', name: 'b', host_name: 'HostBeta', host_id: 'agent-2' }),
    ]);
    render(<InterfaceManagerPage />);
    await screen.findByText('a');

    fireEvent.change(screen.getByPlaceholderText('Search interfaces...'), {
      target: { value: 'Beta' },
    });

    expect(screen.getByText('b')).toBeInTheDocument();
    expect(screen.queryByText('a')).not.toBeInTheDocument();
  });

  it('filters by ip_address in search', async () => {
    setupMocks([
      makeIface({ id: 'i1', name: 'x', ip_address: '10.1.1.1/24' }),
      makeIface({ id: 'i2', name: 'y', ip_address: '192.168.0.1/24' }),
    ]);
    render(<InterfaceManagerPage />);
    await screen.findByText('x');

    fireEvent.change(screen.getByPlaceholderText('Search interfaces...'), {
      target: { value: '192.168' },
    });

    expect(screen.getByText('y')).toBeInTheDocument();
    expect(screen.queryByText('x')).not.toBeInTheDocument();
  });

  it('shows empty state when search matches nothing', async () => {
    setupMocks([makeIface()]);
    render(<InterfaceManagerPage />);
    await screen.findByText('eth0.100');

    fireEvent.change(screen.getByPlaceholderText('Search interfaces...'), {
      target: { value: 'zzz-no-match' },
    });

    expect(screen.getByText('No managed interfaces')).toBeInTheDocument();
  });

  it('host filter triggers API re-fetch with host_id param', async () => {
    setupMocks([], [makeAgent({ id: 'a1', name: 'Host1' })]);
    render(<InterfaceManagerPage />);
    await waitFor(() => expect(mockedApi).toHaveBeenCalledWith('/agents'));

    // The next call will be for the filtered interfaces
    mockedApi.mockResolvedValueOnce({ interfaces: [] });

    fireEvent.change(screen.getByLabelText('Host filter'), {
      target: { value: 'a1' },
    });

    await waitFor(() => {
      expect(mockedApi).toHaveBeenCalledWith('/infrastructure/interfaces?host_id=a1');
    });
  });

  it('type filter triggers API re-fetch with interface_type param', async () => {
    setupMocks([]);
    render(<InterfaceManagerPage />);
    await waitFor(() => expect(mockedApi).toHaveBeenCalledWith('/infrastructure/interfaces'));

    mockedApi.mockResolvedValueOnce({ interfaces: [] });

    fireEvent.change(screen.getByLabelText('Type filter'), {
      target: { value: 'external' },
    });

    await waitFor(() => {
      expect(mockedApi).toHaveBeenCalledWith('/infrastructure/interfaces?interface_type=external');
    });
  });

  // ─── Delete Flow ──────────────────────────────────────────────

  it('shows confirm/cancel buttons on delete click', async () => {
    setupMocks([makeIface()]);
    render(<InterfaceManagerPage />);
    await screen.findByText('eth0.100');

    fireEvent.click(screen.getByTitle('Delete interface'));

    expect(screen.getByText('Delete')).toBeInTheDocument();
    expect(screen.getByText('Cancel')).toBeInTheDocument();
  });

  it('cancels delete and hides confirm UI', async () => {
    setupMocks([makeIface()]);
    render(<InterfaceManagerPage />);
    await screen.findByText('eth0.100');

    fireEvent.click(screen.getByTitle('Delete interface'));
    fireEvent.click(screen.getByText('Cancel'));

    // Confirm UI gone — edit/delete buttons should be back
    expect(screen.getByTitle('Delete interface')).toBeInTheDocument();
    expect(screen.getByTitle('Edit interface')).toBeInTheDocument();
  });

  it('calls DELETE API and reloads interfaces on confirm', async () => {
    setupMocks([makeIface()]);
    // delete response + reload responses
    mockedApi
      .mockResolvedValueOnce({})                         // DELETE
      .mockResolvedValueOnce({ interfaces: [] });        // reload

    render(<InterfaceManagerPage />);
    await screen.findByText('eth0.100');

    fireEvent.click(screen.getByTitle('Delete interface'));
    fireEvent.click(screen.getByText('Delete'));

    await waitFor(() => {
      expect(mockedApi).toHaveBeenCalledWith(
        '/infrastructure/interfaces/iface-1',
        { method: 'DELETE' },
      );
    });
  });

  it('handles delete API failure gracefully', async () => {
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    setupMocks([makeIface()]);
    mockedApi.mockRejectedValueOnce(new Error('Server error'));

    render(<InterfaceManagerPage />);
    await screen.findByText('eth0.100');

    fireEvent.click(screen.getByTitle('Delete interface'));
    fireEvent.click(screen.getByText('Delete'));

    await waitFor(() => {
      expect(consoleSpy).toHaveBeenCalledWith('Failed to delete interface:', expect.any(Error));
    });
    consoleSpy.mockRestore();
  });

  // ─── Edit Modal ───────────────────────────────────────────────

  it('opens edit modal with pre-filled values', async () => {
    setupMocks([makeIface({ ip_address: '10.0.0.5/24', desired_mtu: 1500 })]);
    render(<InterfaceManagerPage />);
    await screen.findByText('eth0.100');

    fireEvent.click(screen.getByTitle('Edit interface'));

    expect(screen.getByText('Edit Interface')).toBeInTheDocument();

    // The modal IP input should be pre-filled
    const modal = document.querySelector('.fixed')!;
    const ipInput = modal.querySelector('input[type="text"]') as HTMLInputElement;
    expect(ipInput.value).toBe('10.0.0.5/24');
  });

  it('shows CIDR validation warning in edit modal', async () => {
    setupMocks([makeIface()]);
    render(<InterfaceManagerPage />);
    await screen.findByText('eth0.100');

    fireEvent.click(screen.getByTitle('Edit interface'));

    const ipInput = screen.getByPlaceholderText('e.g. 10.100.0.1/24');
    fireEvent.change(ipInput, { target: { value: 'bad-cidr' } });

    expect(screen.getByText(/Must be valid CIDR notation/)).toBeInTheDocument();
  });

  it('disables Save button when CIDR is invalid in edit modal', async () => {
    setupMocks([makeIface()]);
    render(<InterfaceManagerPage />);
    await screen.findByText('eth0.100');

    fireEvent.click(screen.getByTitle('Edit interface'));

    const ipInput = screen.getByPlaceholderText('e.g. 10.100.0.1/24');
    fireEvent.change(ipInput, { target: { value: '999.0.0.1/99' } });

    const saveBtn = screen.getByText('Save').closest('button')!;
    expect(saveBtn).toBeDisabled();
  });

  it('calls PATCH API on save and closes modal', async () => {
    setupMocks([makeIface({ id: 'iface-42', desired_mtu: 1500, ip_address: '10.0.0.1/24' })]);
    // PATCH response + reload
    mockedApi
      .mockResolvedValueOnce({})
      .mockResolvedValueOnce({ interfaces: [] });

    render(<InterfaceManagerPage />);
    await screen.findByText('eth0.100');

    fireEvent.click(screen.getByTitle('Edit interface'));

    const mtuInput = screen.getByDisplayValue('1500') as HTMLInputElement;
    fireEvent.change(mtuInput, { target: { value: '9000' } });

    fireEvent.click(screen.getByText('Save').closest('button')!);

    await waitFor(() => {
      expect(mockedApi).toHaveBeenCalledWith(
        '/infrastructure/interfaces/iface-42',
        {
          method: 'PATCH',
          body: JSON.stringify({ desired_mtu: 9000, ip_address: '10.0.0.1/24' }),
        },
      );
    });

    // Modal should close
    await waitFor(() => {
      expect(screen.queryByText('Edit Interface')).not.toBeInTheDocument();
    });
  });

  it('shows sync error in edit modal when present', async () => {
    setupMocks([makeIface({ sync_error: 'OVS bridge unreachable' })]);
    render(<InterfaceManagerPage />);
    await screen.findByText('eth0.100');

    fireEvent.click(screen.getByTitle('Edit interface'));

    expect(screen.getByText('Last sync error')).toBeInTheDocument();
    // Error text appears in both table row and modal — use getAllByText
    const errorTexts = screen.getAllByText('OVS bridge unreachable');
    expect(errorTexts.length).toBeGreaterThanOrEqual(2); // table + modal
  });

  it('closes edit modal via Cancel button', async () => {
    setupMocks([makeIface()]);
    render(<InterfaceManagerPage />);
    await screen.findByText('eth0.100');

    fireEvent.click(screen.getByTitle('Edit interface'));
    expect(screen.getByText('Edit Interface')).toBeInTheDocument();

    // Find Cancel within the modal overlay
    const modal = document.querySelector('.fixed')!;
    const cancelBtn = Array.from(modal.querySelectorAll('button'))
      .find(b => b.textContent === 'Cancel')!;
    fireEvent.click(cancelBtn);

    expect(screen.queryByText('Edit Interface')).not.toBeInTheDocument();
  });

  // ─── Inline MTU Edit ──────────────────────────────────────────

  it('shows inline MTU input on click and saves on Enter', async () => {
    setupMocks([makeIface({ id: 'i1', desired_mtu: 9000, current_mtu: 1500 })]);
    // PATCH + reload
    mockedApi
      .mockResolvedValueOnce({})
      .mockResolvedValueOnce({ interfaces: [] });

    render(<InterfaceManagerPage />);
    await screen.findByText('eth0.100');

    // Click the desired MTU value button (shows "9000" clickable)
    const mtuBtn = screen.getByTitle('Click to edit desired MTU');
    fireEvent.click(mtuBtn);

    // Should show number input
    const input = screen.getByDisplayValue('9000') as HTMLInputElement;
    expect(input.type).toBe('number');

    fireEvent.change(input, { target: { value: '1500' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() => {
      expect(mockedApi).toHaveBeenCalledWith(
        '/infrastructure/interfaces/i1',
        { method: 'PATCH', body: JSON.stringify({ desired_mtu: 1500 }) },
      );
    });
  });

  it('cancels inline MTU edit on Escape', async () => {
    setupMocks([makeIface({ desired_mtu: 9000 })]);
    render(<InterfaceManagerPage />);
    await screen.findByText('eth0.100');

    fireEvent.click(screen.getByTitle('Click to edit desired MTU'));
    const input = screen.getByDisplayValue('9000');
    fireEvent.keyDown(input, { key: 'Escape' });

    // Input should disappear, button should be back
    expect(screen.getByTitle('Click to edit desired MTU')).toBeInTheDocument();
  });

  it('does not save inline MTU when value is out of range', async () => {
    setupMocks([makeIface({ desired_mtu: 9000 })]);
    render(<InterfaceManagerPage />);
    await screen.findByText('eth0.100');

    fireEvent.click(screen.getByTitle('Click to edit desired MTU'));
    const input = screen.getByDisplayValue('9000');

    // Set an out-of-range value (> 9216)
    fireEvent.change(input, { target: { value: '99999' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    // PATCH should NOT have been called (only the initial 2 load calls)
    await waitFor(() => {
      expect(mockedApi).toHaveBeenCalledTimes(2);
    });
  });

  // ─── Create Modal ─────────────────────────────────────────────

  it('opens create modal and shows all form fields', async () => {
    setupMocks([], [makeAgent()]);
    render(<InterfaceManagerPage />);

    fireEvent.click(await screen.findByText('Create Interface'));

    expect(screen.getByText('Create Managed Interface')).toBeInTheDocument();
    expect(screen.getByLabelText('Host')).toBeInTheDocument();
    expect(screen.getByLabelText('Type')).toBeInTheDocument();
    expect(screen.getByLabelText('Parent Interface')).toBeInTheDocument();
    expect(screen.getByText('VLAN ID')).toBeInTheDocument();
    expect(screen.getByText('MTU')).toBeInTheDocument();
  });

  it('only shows online agents in create modal host dropdown', async () => {
    setupMocks([], [
      makeAgent({ id: 'a1', name: 'Online Agent', status: 'online' }),
      makeAgent({ id: 'a2', name: 'Offline Agent', status: 'offline' }),
    ]);
    render(<InterfaceManagerPage />);
    fireEvent.click(await screen.findByText('Create Interface'));

    const hostSelect = screen.getByLabelText('Host');
    const options = hostSelect.querySelectorAll('option');
    const optionTexts = Array.from(options).map(o => o.textContent);

    expect(optionTexts).toContain('Online Agent');
    expect(optionTexts).not.toContain('Offline Agent');
  });

  it('loads agent physical interfaces when host is selected', async () => {
    setupMocks([], [makeAgent({ id: 'a1', name: 'Host1' })]);
    mockedApi.mockResolvedValueOnce({
      interfaces: [
        { name: 'ens192', mtu: 1500, is_physical: true, state: 'up' },
        { name: 'lo', mtu: 65536, is_physical: false, state: 'unknown' },
      ],
    });

    render(<InterfaceManagerPage />);
    fireEvent.click(await screen.findByText('Create Interface'));

    fireEvent.change(screen.getByLabelText('Host'), { target: { value: 'a1' } });

    await waitFor(() => {
      expect(mockedApi).toHaveBeenCalledWith('/infrastructure/agents/a1/interfaces');
    });

    // Physical interface should appear, non-physical filtered out
    const parentSelect = screen.getByLabelText('Parent Interface');
    await waitFor(() => {
      const opts = parentSelect.querySelectorAll('option');
      const texts = Array.from(opts).map(o => o.textContent);
      expect(texts.some(t => t?.includes('ens192'))).toBe(true);
      expect(texts.some(t => t?.includes('lo'))).toBe(false);
    });
  });

  it('shows type description for each interface type', async () => {
    setupMocks([], [makeAgent()]);
    render(<InterfaceManagerPage />);
    fireEvent.click(await screen.findByText('Create Interface'));

    // Default is "custom" per initial form state — but HTML select defaults to first option "transport"
    const typeSelect = screen.getByLabelText('Type');

    fireEvent.change(typeSelect, { target: { value: 'transport' } });
    expect(screen.getByText(/Routed subinterface for VXLAN/)).toBeInTheDocument();

    fireEvent.change(typeSelect, { target: { value: 'external' } });
    expect(screen.getByText(/L2 pass-through/)).toBeInTheDocument();

    fireEvent.change(typeSelect, { target: { value: 'custom' } });
    expect(screen.getByText(/General-purpose managed/)).toBeInTheDocument();
  });

  it('hides IP field when type is external', async () => {
    setupMocks([], [makeAgent()]);
    render(<InterfaceManagerPage />);
    fireEvent.click(await screen.findByText('Create Interface'));

    // Initially type is 'custom', IP field should be visible
    expect(screen.getByText('IP Address (CIDR)')).toBeInTheDocument();

    const typeSelect = screen.getByLabelText('Type');
    fireEvent.change(typeSelect, { target: { value: 'external' } });

    // IP field hidden for external type
    // Use queryByLabelText to avoid error — the label is inside the modal
    const labels = Array.from(document.querySelectorAll('label'));
    const ipLabel = labels.find(l => l.textContent === 'IP Address (CIDR)');
    expect(ipLabel).toBeUndefined();
  });

  it('disables create button when no host is selected', async () => {
    setupMocks([], [makeAgent()]);
    render(<InterfaceManagerPage />);
    fireEvent.click(await screen.findByText('Create Interface'));

    const modal = document.querySelector('.fixed')!;
    const createBtn = Array.from(modal.querySelectorAll('button'))
      .find(b => b.textContent?.includes('Create'))!;
    expect(createBtn).toBeDisabled();
  });

  it('calls create API and closes modal on success', async () => {
    setupMocks([], [makeAgent({ id: 'a1' })]);
    // agent interfaces load + create + reload
    mockedApi
      .mockResolvedValueOnce({ interfaces: [{ name: 'eth0', mtu: 1500, is_physical: true, state: 'up' }] })
      .mockResolvedValueOnce({})
      .mockResolvedValueOnce({ interfaces: [makeIface()] });

    render(<InterfaceManagerPage />);
    fireEvent.click(await screen.findByText('Create Interface'));

    fireEvent.change(screen.getByLabelText('Host'), { target: { value: 'a1' } });
    await waitFor(() => expect(mockedApi).toHaveBeenCalledWith('/infrastructure/agents/a1/interfaces'));

    const modal = document.querySelector('.fixed')!;
    const createBtn = Array.from(modal.querySelectorAll('button'))
      .find(b => b.textContent?.includes('Create'))!;
    fireEvent.click(createBtn);

    await waitFor(() => {
      expect(mockedApi).toHaveBeenCalledWith(
        '/infrastructure/agents/a1/managed-interfaces',
        expect.objectContaining({ method: 'POST' }),
      );
    });

    // Modal closes
    await waitFor(() => {
      expect(screen.queryByText('Create Managed Interface')).not.toBeInTheDocument();
    });
  });

  it('disables create when CIDR is invalid for non-external type', async () => {
    setupMocks([], [makeAgent({ id: 'a1' })]);
    mockedApi.mockResolvedValueOnce({ interfaces: [] }); // agent interfaces

    render(<InterfaceManagerPage />);
    fireEvent.click(await screen.findByText('Create Interface'));

    // Select a host so that button is not disabled due to missing host
    fireEvent.change(screen.getByLabelText('Host'), { target: { value: 'a1' } });
    await waitFor(() => expect(mockedApi).toHaveBeenCalledWith('/infrastructure/agents/a1/interfaces'));

    // Type defaults to custom, enter invalid IP
    const ipInput = screen.getByPlaceholderText('e.g. 10.100.0.1/24');
    fireEvent.change(ipInput, { target: { value: 'not-valid' } });

    const modal = document.querySelector('.fixed')!;
    const createBtn = Array.from(modal.querySelectorAll('button'))
      .find(b => b.textContent?.includes('Create'))!;
    expect(createBtn).toBeDisabled();
  });

  it('closes create modal via Cancel', async () => {
    setupMocks([], [makeAgent()]);
    render(<InterfaceManagerPage />);
    fireEvent.click(await screen.findByText('Create Interface'));
    expect(screen.getByText('Create Managed Interface')).toBeInTheDocument();

    const modal = document.querySelector('.fixed')!;
    const cancelBtn = Array.from(modal.querySelectorAll('button'))
      .find(b => b.textContent === 'Cancel')!;
    fireEvent.click(cancelBtn);

    expect(screen.queryByText('Create Managed Interface')).not.toBeInTheDocument();
  });

  // ─── API Error Handling ───────────────────────────────────────

  it('handles agents API error gracefully', async () => {
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    mockedApi
      .mockRejectedValueOnce(new Error('Network error'))  // /agents fails
      .mockResolvedValueOnce({ interfaces: [] });          // /infrastructure/interfaces

    render(<InterfaceManagerPage />);

    await waitFor(() => {
      expect(consoleSpy).toHaveBeenCalledWith('Failed to load agents:', expect.any(Error));
    });
    consoleSpy.mockRestore();
  });

  it('handles interfaces API error gracefully', async () => {
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    mockedApi
      .mockResolvedValueOnce([makeAgent()])                // /agents
      .mockRejectedValueOnce(new Error('DB down'));        // /infrastructure/interfaces fails

    render(<InterfaceManagerPage />);

    await waitFor(() => {
      expect(consoleSpy).toHaveBeenCalledWith('Failed to load interfaces:', expect.any(Error));
    });
    consoleSpy.mockRestore();
  });

  it('handles agents response as { agents: [...] } wrapper', async () => {
    mockedApi
      .mockResolvedValueOnce({ agents: [makeAgent({ id: 'wrapped', name: 'Wrapped Agent' })] })
      .mockResolvedValueOnce({ interfaces: [] });

    render(<InterfaceManagerPage />);

    // Open create modal to verify agents loaded into dropdown
    fireEvent.click(await screen.findByText('Create Interface'));

    const hostSelect = screen.getByLabelText('Host');
    const opts = Array.from(hostSelect.querySelectorAll('option'));
    expect(opts.some(o => o.textContent === 'Wrapped Agent')).toBe(true);
  });

  // ─── Navigation ───────────────────────────────────────────────

  it('navigates back to infrastructure network tab', async () => {
    setupMocks([]);
    render(<InterfaceManagerPage />);
    await screen.findByText('No managed interfaces');

    const backBtn = screen.getByText('Back').closest('button')!;
    fireEvent.click(backBtn);
    expect(mockNavigate).toHaveBeenCalledWith('/infrastructure?tab=network');
  });

  // ─── Current/Desired MTU Display ──────────────────────────────

  it('shows current_mtu / desired_mtu in the table', async () => {
    setupMocks([makeIface({ current_mtu: 1500, desired_mtu: 9000 })]);
    render(<InterfaceManagerPage />);
    await screen.findByText('eth0.100');

    // current_mtu is text, desired_mtu is clickable button
    expect(screen.getByText('1500')).toBeInTheDocument();
    expect(screen.getByTitle('Click to edit desired MTU')).toHaveTextContent('9000');
  });

  it('shows dash for null current_mtu', async () => {
    setupMocks([makeIface({ current_mtu: null, desired_mtu: 9000 })]);
    render(<InterfaceManagerPage />);
    await screen.findByText('eth0.100');

    // The '-' for null current_mtu should be present
    const row = screen.getByText('eth0.100').closest('tr')!;
    expect(row.textContent).toContain('-');
  });

  // ─── Parent interface selector disabled without host ──────────

  it('parent interface selector is disabled when no host selected', async () => {
    setupMocks([], [makeAgent()]);
    render(<InterfaceManagerPage />);
    fireEvent.click(await screen.findByText('Create Interface'));

    const parentSelect = screen.getByLabelText('Parent Interface') as HTMLSelectElement;
    expect(parentSelect).toBeDisabled();
  });
});
