import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { BrowserRouter } from 'react-router-dom';
import NetworkTab from './NetworkTab';
import type {
  AgentMeshResponse,
  AgentNetworkConfig,
  HostDetailed,
  ManagedInterface,
  NicGroup,
} from './infrastructureTypes';

// Mock AgentMeshSection since it is a complex child component
vi.mock('./AgentMeshSection', () => ({
  default: () => <div data-testid="agent-mesh-section">AgentMeshSection</div>,
}));

function makeHost(overrides: Partial<HostDetailed> = {}): HostDetailed {
  return {
    id: 'host-1',
    name: 'Agent-01',
    address: '10.0.0.1:8001',
    status: 'online',
    version: '1.0.0',
    role: 'agent',
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
    labs: [],
    lab_count: 0,
    started_at: null,
    last_heartbeat: new Date().toISOString(),
    git_sha: null,
    last_error: null,
    error_since: null,
    data_plane_address: null,
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
      updated_at: '2026-01-01T00:00:00Z',
      updated_by_id: null,
    },
    ...overrides,
  };
}

function makeNetworkConfig(overrides: Partial<AgentNetworkConfig> = {}): AgentNetworkConfig {
  return {
    id: 'nc-1',
    host_id: 'host-1',
    host_name: 'Agent-01',
    data_plane_interface: 'eth0',
    desired_mtu: 9000,
    current_mtu: 1500,
    last_sync_at: null,
    sync_status: 'synced',
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
    host_id: 'host-1',
    host_name: 'Agent-01',
    name: 'ens192.100',
    interface_type: 'transport',
    parent_interface: 'ens192',
    vlan_id: 100,
    ip_address: '10.10.10.1',
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

function makeNicGroup(overrides: Partial<NicGroup> = {}): NicGroup {
  return {
    id: 'ng-1',
    host_id: 'host-1',
    host_name: 'Agent-01',
    name: 'Data Plane',
    description: 'Data plane interfaces',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    members: [],
    ...overrides,
  };
}

const defaultProps = () => ({
  mesh: makeMesh(),
  meshLoading: false,
  meshError: null,
  hosts: [makeHost()],
  networkConfigs: [makeNetworkConfig()],
  networkConfigsLoading: false,
  managedInterfaces: [] as ManagedInterface[],
  showManagedInterfaces: false,
  setShowManagedInterfaces: vi.fn(),
  nicGroups: [] as NicGroup[],
  nicGroupsLoading: false,
  mtuValue: 1450,
  settingsDirty: false,
  savingSettings: false,
  mtuRecommendation: null,
  configuringMtu: null,
  testingAll: false,
  testingLink: null,
  onHandleMtuChange: vi.fn(),
  onSaveSettings: vi.fn(),
  onLoadNetworkConfigs: vi.fn(),
  onOpenMtuConfigModal: vi.fn(),
  onTestAllLinks: vi.fn(),
  onTestLink: vi.fn(),
  onOpenNicGroupModal: vi.fn(),
  onOpenNicGroupMemberModal: vi.fn(),
});

function renderWithRouter(ui: React.ReactElement) {
  return render(<BrowserRouter>{ui}</BrowserRouter>);
}

describe('NetworkTab', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // ── Loading & Error ──

  it('shows loading spinner when meshLoading is true and mesh is null', () => {
    const props = defaultProps();
    renderWithRouter(<NetworkTab {...props} meshLoading={true} mesh={null} />);
    expect(screen.getByText('Loading infrastructure data...')).toBeInTheDocument();
  });

  it('shows error message when meshError is set', () => {
    const props = defaultProps();
    renderWithRouter(<NetworkTab {...props} meshError="Failed to load mesh data" />);
    expect(screen.getByText('Failed to load mesh data')).toBeInTheDocument();
  });

  // ── Global Settings Panel ──

  it('renders the Global Settings heading', () => {
    const props = defaultProps();
    renderWithRouter(<NetworkTab {...props} />);
    expect(screen.getByText('Global Settings')).toBeInTheDocument();
  });

  it('renders overlay MTU input with correct value', () => {
    const props = defaultProps();
    renderWithRouter(<NetworkTab {...props} mtuValue={1450} />);
    const input = screen.getByDisplayValue('1450');
    expect(input).toBeInTheDocument();
    expect(input).toHaveAttribute('type', 'number');
  });

  it('calls onHandleMtuChange when MTU input value changes', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    renderWithRouter(<NetworkTab {...props} />);
    const input = screen.getByDisplayValue('1450');
    await user.clear(input);
    await user.type(input, '9000');
    expect(props.onHandleMtuChange).toHaveBeenCalled();
  });

  it('shows MTU recommendation when provided', () => {
    const props = defaultProps();
    renderWithRouter(<NetworkTab {...props} mtuRecommendation={8950} />);
    expect(screen.getByText(/Recommended: 8950/)).toBeInTheDocument();
    expect(screen.getByText('Apply')).toBeInTheDocument();
  });

  it('clicking Apply on recommendation calls onHandleMtuChange', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    renderWithRouter(<NetworkTab {...props} mtuRecommendation={8950} />);
    await user.click(screen.getByText('Apply'));
    expect(props.onHandleMtuChange).toHaveBeenCalledWith(8950);
  });

  it('disables Save Settings button when settingsDirty is false', () => {
    const props = defaultProps();
    renderWithRouter(<NetworkTab {...props} settingsDirty={false} />);
    const saveBtn = screen.getByText('Save Settings').closest('button')!;
    expect(saveBtn).toBeDisabled();
  });

  it('enables Save Settings button when settingsDirty is true', () => {
    const props = defaultProps();
    renderWithRouter(<NetworkTab {...props} settingsDirty={true} />);
    const saveBtn = screen.getByText('Save Settings').closest('button')!;
    expect(saveBtn).not.toBeDisabled();
  });

  it('calls onSaveSettings when Save Settings is clicked', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    renderWithRouter(<NetworkTab {...props} settingsDirty={true} />);
    await user.click(screen.getByText('Save Settings').closest('button')!);
    expect(props.onSaveSettings).toHaveBeenCalledTimes(1);
  });

  it('shows unsaved changes indicator when settingsDirty', () => {
    const props = defaultProps();
    renderWithRouter(<NetworkTab {...props} settingsDirty={true} />);
    expect(screen.getByText('Unsaved changes')).toBeInTheDocument();
  });

  // ── Host Network Table ──

  it('shows empty state when no hosts are registered', () => {
    const props = defaultProps();
    renderWithRouter(<NetworkTab {...props} hosts={[]} />);
    expect(screen.getByText('No agents registered')).toBeInTheDocument();
  });

  it('renders host rows with name and transport mode', () => {
    const props = defaultProps();
    renderWithRouter(<NetworkTab {...props} />);
    expect(screen.getByText('Agent-01')).toBeInTheDocument();
    expect(screen.getByText('Management')).toBeInTheDocument();
  });

  it('shows Configure button and calls onOpenMtuConfigModal', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    renderWithRouter(<NetworkTab {...props} />);
    const configureBtn = screen.getByText('Configure').closest('button')!;
    expect(configureBtn).not.toBeDisabled();
    await user.click(configureBtn);
    expect(props.onOpenMtuConfigModal).toHaveBeenCalledWith('host-1');
  });

  it('disables Configure button for offline host', () => {
    const props = defaultProps();
    props.hosts = [makeHost({ status: 'offline' })];
    renderWithRouter(<NetworkTab {...props} />);
    const configureBtn = screen.getByText('Configure').closest('button')!;
    expect(configureBtn).toBeDisabled();
  });

  it('shows attention banner when configs have mismatch status', () => {
    const props = defaultProps();
    props.networkConfigs = [makeNetworkConfig({ sync_status: 'mismatch' })];
    renderWithRouter(<NetworkTab {...props} />);
    expect(screen.getByText(/Some agents have MTU configurations that need attention/)).toBeInTheDocument();
  });

  // ── Managed Interfaces ──

  it('renders managed interfaces section when interfaces exist', () => {
    const props = defaultProps();
    props.managedInterfaces = [makeManagedInterface()];
    renderWithRouter(<NetworkTab {...props} />);
    expect(screen.getByText('Managed Interfaces')).toBeInTheDocument();
    expect(screen.getByText('(1)')).toBeInTheDocument();
  });

  it('toggles managed interfaces visibility on Show/Hide button click', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    props.managedInterfaces = [makeManagedInterface()];
    renderWithRouter(<NetworkTab {...props} />);
    const toggleBtn = screen.getByText('Show').closest('button')!;
    await user.click(toggleBtn);
    expect(props.setShowManagedInterfaces).toHaveBeenCalledTimes(1);
  });

  // ── NIC Groups ──

  it('renders NIC groups section and Create Group button', () => {
    const props = defaultProps();
    renderWithRouter(<NetworkTab {...props} />);
    expect(screen.getByText('NIC Groups (Preview)')).toBeInTheDocument();
    expect(screen.getByText('Create Group')).toBeInTheDocument();
  });

  it('calls onOpenNicGroupModal when Create Group is clicked', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    renderWithRouter(<NetworkTab {...props} />);
    await user.click(screen.getByText('Create Group').closest('button')!);
    expect(props.onOpenNicGroupModal).toHaveBeenCalledTimes(1);
  });

  it('renders NIC groups table when groups exist', () => {
    const props = defaultProps();
    props.nicGroups = [makeNicGroup()];
    renderWithRouter(<NetworkTab {...props} />);
    expect(screen.getByText('Data Plane')).toBeInTheDocument();
    expect(screen.getByText('No members')).toBeInTheDocument();
  });

  it('shows NIC group loading state', () => {
    const props = defaultProps();
    renderWithRouter(<NetworkTab {...props} nicGroupsLoading={true} />);
    expect(screen.getByText('Loading NIC groups...')).toBeInTheDocument();
  });

  it('shows empty NIC groups message', () => {
    const props = defaultProps();
    renderWithRouter(<NetworkTab {...props} nicGroups={[]} nicGroupsLoading={false} />);
    expect(screen.getByText('No NIC groups yet.')).toBeInTheDocument();
  });

  // ── Agent Mesh Section ──

  it('renders AgentMeshSection when mesh is available', () => {
    const props = defaultProps();
    renderWithRouter(<NetworkTab {...props} />);
    expect(screen.getByTestId('agent-mesh-section')).toBeInTheDocument();
  });
});
