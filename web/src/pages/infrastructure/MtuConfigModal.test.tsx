import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import MtuConfigModal from './MtuConfigModal';
import type {
  AgentMeshResponse,
  AgentNetworkConfig,
  InterfaceDetail,
  ManagedInterface,
} from './infrastructureTypes';

function makeInterface(overrides: Partial<InterfaceDetail> = {}): InterfaceDetail {
  return {
    name: 'eth0',
    mtu: 1500,
    is_physical: true,
    is_default_route: true,
    mac: 'aa:bb:cc:dd:ee:ff',
    ipv4_addresses: ['10.0.0.1/24'],
    state: 'UP',
    ...overrides,
  };
}

function makeMesh(overrides: Partial<AgentMeshResponse> = {}): AgentMeshResponse {
  return {
    agents: [],
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

function makeManagedInterface(overrides: Partial<ManagedInterface> = {}): ManagedInterface {
  return {
    id: 'mi-1',
    host_id: 'agent-1',
    host_name: 'Agent-01',
    name: 'ens192.100',
    interface_type: 'transport',
    parent_interface: 'ens192',
    vlan_id: 100,
    ip_address: '10.10.10.1/24',
    desired_mtu: 9000,
    current_mtu: 9000,
    is_up: true,
    sync_status: 'synced',
    sync_error: null,
    last_sync_at: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

const defaultProps = () => ({
  configModalData: {
    agentId: 'agent-1',
    agentName: 'Agent-01',
    interfaces: [makeInterface(), makeInterface({ name: 'eth1', mtu: 9000, is_default_route: false, ipv4_addresses: ['192.168.1.1/24'] })],
    defaultInterface: 'eth0',
    networkManager: 'networkd',
    currentConfig: null as AgentNetworkConfig | null,
  },
  managedInterfaces: [] as ManagedInterface[],
  mesh: makeMesh(),
  mtuValue: 1450,
  selectedInterface: '',
  setSelectedInterface: vi.fn(),
  desiredMtu: 9000,
  setDesiredMtu: vi.fn(),
  savingMtuConfig: false,
  selectedTransportMode: 'management',
  setSelectedTransportMode: vi.fn(),
  selectedTransportInterface: '',
  setSelectedTransportInterface: vi.fn(),
  useTransportInterface: false,
  setUseTransportInterface: vi.fn(),
  onSave: vi.fn(),
  onClose: vi.fn(),
});

describe('MtuConfigModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // ── Rendering ──

  it('renders the modal title with agent name', () => {
    render(<MtuConfigModal {...defaultProps()} />);
    expect(screen.getByText('Configure MTU - Agent-01')).toBeInTheDocument();
  });

  it('renders close button (X) and Cancel button', () => {
    render(<MtuConfigModal {...defaultProps()} />);
    expect(screen.getByText('Cancel')).toBeInTheDocument();
  });

  it('calls onClose when Cancel is clicked', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    render(<MtuConfigModal {...props} />);
    await user.click(screen.getByText('Cancel'));
    expect(props.onClose).toHaveBeenCalledTimes(1);
  });

  it('calls onClose when X button is clicked', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    const { container } = render(<MtuConfigModal {...props} />);
    // The X button contains fa-times icon
    const closeIcon = container.querySelector('i.fa-times');
    expect(closeIcon).toBeInTheDocument();
    await user.click(closeIcon!.closest('button')!);
    expect(props.onClose).toHaveBeenCalledTimes(1);
  });

  // ── Network Manager Info ──

  it('shows network manager info when available', () => {
    render(<MtuConfigModal {...defaultProps()} />);
    expect(screen.getByText(/networkd/)).toBeInTheDocument();
    expect(screen.getByText('(will persist on reboot)')).toBeInTheDocument();
  });

  it('shows runtime-only warning for unknown network manager', () => {
    const props = defaultProps();
    props.configModalData.networkManager = 'unknown';
    render(<MtuConfigModal {...props} />);
    expect(screen.getByText('(runtime only, may not persist)')).toBeInTheDocument();
  });

  // ── Transport Mode ──

  it('renders transport mode select with management default', () => {
    render(<MtuConfigModal {...defaultProps()} />);
    expect(screen.getByText('Transport Mode')).toBeInTheDocument();
    expect(screen.getByDisplayValue('Management')).toBeInTheDocument();
  });

  it('disables subinterface option when no transport subinterfaces exist', () => {
    render(<MtuConfigModal {...defaultProps()} />);
    const select = screen.getByDisplayValue('Management') as HTMLSelectElement;
    const subinterfaceOption = Array.from(select.options).find(o => o.value === 'subinterface');
    expect(subinterfaceOption).toBeDefined();
    expect(subinterfaceOption!.disabled).toBe(true);
  });

  it('shows hint when subinterface is disabled', () => {
    render(<MtuConfigModal {...defaultProps()} />);
    expect(screen.getByText(/Subinterface disabled/)).toBeInTheDocument();
  });

  // ── Interface Selection ──

  it('renders interface dropdown with available interfaces', () => {
    const props = defaultProps();
    render(<MtuConfigModal {...props} />);
    expect(screen.getByText('Physical Interface')).toBeInTheDocument();
    expect(screen.getByText('Select an interface...')).toBeInTheDocument();
  });

  it('shows interface details when an interface is selected', () => {
    const props = defaultProps();
    props.selectedInterface = 'eth0';
    render(<MtuConfigModal {...props} />);
    expect(screen.getByText('Current MTU:')).toBeInTheDocument();
    expect(screen.getByText('1500')).toBeInTheDocument();
    expect(screen.getByText('aa:bb:cc:dd:ee:ff')).toBeInTheDocument();
  });

  it('shows no interfaces message when interface list is empty', () => {
    const props = defaultProps();
    props.configModalData.interfaces = [];
    render(<MtuConfigModal {...props} />);
    expect(screen.getByText('No physical interfaces found on this agent.')).toBeInTheDocument();
  });

  // ── MTU Input ──

  it('renders desired MTU input with current value', () => {
    const props = defaultProps();
    render(<MtuConfigModal {...props} />);
    expect(screen.getByDisplayValue('9000')).toBeInTheDocument();
  });

  it('renders quick MTU preset buttons', () => {
    render(<MtuConfigModal {...defaultProps()} />);
    expect(screen.getByText('1500 (Standard)')).toBeInTheDocument();
    expect(screen.getByText('9000 (Jumbo)')).toBeInTheDocument();
  });

  it('calls setDesiredMtu when preset button is clicked', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    render(<MtuConfigModal {...props} />);
    await user.click(screen.getByText('1500 (Standard)'));
    expect(props.setDesiredMtu).toHaveBeenCalledWith(1500);
  });

  it('shows VXLAN overhead guidance text', () => {
    const props = defaultProps();
    render(<MtuConfigModal {...props} />);
    expect(screen.getByText(/For VXLAN overlay/)).toBeInTheDocument();
    // With mtuValue=1450, should say at least 1500
    expect(screen.getByText(/at least 1500/)).toBeInTheDocument();
  });

  // ── MTU Decrease Warning ──

  it('shows warning when decreasing MTU', () => {
    const props = defaultProps();
    props.selectedInterface = 'eth1'; // eth1 has mtu 9000
    props.desiredMtu = 1500;
    render(<MtuConfigModal {...props} />);
    expect(screen.getByText(/You are decreasing MTU from 9000 to 1500/)).toBeInTheDocument();
  });

  it('does not show warning when increasing MTU', () => {
    const props = defaultProps();
    props.selectedInterface = 'eth0'; // eth0 has mtu 1500
    props.desiredMtu = 9000;
    render(<MtuConfigModal {...props} />);
    expect(screen.queryByText(/You are decreasing/)).not.toBeInTheDocument();
  });

  // ── Save Button ──

  it('disables Apply MTU button when no interface selected', () => {
    const props = defaultProps();
    props.selectedInterface = '';
    render(<MtuConfigModal {...props} />);
    const applyBtn = screen.getByText('Apply MTU').closest('button')!;
    expect(applyBtn).toBeDisabled();
  });

  it('enables Apply MTU button when interface is selected', () => {
    const props = defaultProps();
    props.selectedInterface = 'eth0';
    render(<MtuConfigModal {...props} />);
    const applyBtn = screen.getByText('Apply MTU').closest('button')!;
    expect(applyBtn).not.toBeDisabled();
  });

  it('calls onSave when Apply MTU is clicked', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    props.selectedInterface = 'eth0';
    render(<MtuConfigModal {...props} />);
    await user.click(screen.getByText('Apply MTU').closest('button')!);
    expect(props.onSave).toHaveBeenCalledTimes(1);
  });

  it('shows loading state when saving', () => {
    const props = defaultProps();
    props.savingMtuConfig = true;
    props.selectedInterface = 'eth0';
    render(<MtuConfigModal {...props} />);
    expect(screen.getByText('Applying...')).toBeInTheDocument();
  });

  it('disables Apply MTU button when saving', () => {
    const props = defaultProps();
    props.savingMtuConfig = true;
    props.selectedInterface = 'eth0';
    render(<MtuConfigModal {...props} />);
    const applyBtn = screen.getByText('Applying...').closest('button')!;
    expect(applyBtn).toBeDisabled();
  });
});
