import React from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import { NicGroupCreateModal, NicGroupMemberModal } from './NicGroupModal';
import type { HostDetailed, ManagedInterface, NicGroup } from './infrastructureTypes';

// ── Factories ──

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
    name: 'uplink-a',
    description: 'Primary uplink group',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    members: [],
    ...overrides,
  };
}

// ============================================================================
// NicGroupCreateModal
// ============================================================================

describe('NicGroupCreateModal', () => {
  const defaultCreateProps = () => ({
    hosts: [
      makeHost({ id: 'host-1', name: 'Agent-01' }),
      makeHost({ id: 'host-2', name: 'Agent-02' }),
    ],
    newNicGroupHostId: '',
    setNewNicGroupHostId: vi.fn(),
    newNicGroupName: '',
    setNewNicGroupName: vi.fn(),
    newNicGroupDescription: '',
    setNewNicGroupDescription: vi.fn(),
    creatingNicGroup: false,
    onCreate: vi.fn(),
    onClose: vi.fn(),
  });

  beforeEach(() => {
    vi.clearAllMocks();
  });

  // ── Rendering ──

  it('renders the Create NIC Group heading', () => {
    render(<NicGroupCreateModal {...defaultCreateProps()} />);
    expect(screen.getByText('Create NIC Group')).toBeInTheDocument();
  });

  it('renders Host label and select', () => {
    render(<NicGroupCreateModal {...defaultCreateProps()} />);
    expect(screen.getByText('Host')).toBeInTheDocument();
    expect(screen.getByText('Select a host...')).toBeInTheDocument();
  });

  it('renders host options in the select dropdown', () => {
    render(<NicGroupCreateModal {...defaultCreateProps()} />);
    expect(screen.getByText('Agent-01')).toBeInTheDocument();
    expect(screen.getByText('Agent-02')).toBeInTheDocument();
  });

  it('renders Group Name input with placeholder', () => {
    render(<NicGroupCreateModal {...defaultCreateProps()} />);
    expect(screen.getByText('Group Name')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('e.g. uplink-a')).toBeInTheDocument();
  });

  it('renders Description input with placeholder', () => {
    render(<NicGroupCreateModal {...defaultCreateProps()} />);
    expect(screen.getByText('Description')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('Optional')).toBeInTheDocument();
  });

  it('renders Cancel and Create buttons', () => {
    render(<NicGroupCreateModal {...defaultCreateProps()} />);
    expect(screen.getByText('Cancel')).toBeInTheDocument();
    expect(screen.getByText('Create')).toBeInTheDocument();
  });

  // ── Input Handling ──

  it('calls setNewNicGroupHostId when host is selected', async () => {
    const user = userEvent.setup();
    const props = defaultCreateProps();
    render(<NicGroupCreateModal {...props} />);
    const select = screen.getByRole('combobox');
    await user.selectOptions(select, 'host-1');
    expect(props.setNewNicGroupHostId).toHaveBeenCalledWith('host-1');
  });

  it('calls setNewNicGroupName when name is typed', async () => {
    const user = userEvent.setup();
    const props = defaultCreateProps();
    render(<NicGroupCreateModal {...props} />);
    const input = screen.getByPlaceholderText('e.g. uplink-a');
    await user.type(input, 'my-group');
    expect(props.setNewNicGroupName).toHaveBeenCalled();
  });

  it('calls setNewNicGroupDescription when description is typed', async () => {
    const user = userEvent.setup();
    const props = defaultCreateProps();
    render(<NicGroupCreateModal {...props} />);
    const input = screen.getByPlaceholderText('Optional');
    await user.type(input, 'A description');
    expect(props.setNewNicGroupDescription).toHaveBeenCalled();
  });

  // ── Validation ──

  it('disables Create button when host is not selected', () => {
    const props = defaultCreateProps();
    props.newNicGroupHostId = '';
    props.newNicGroupName = 'my-group';
    render(<NicGroupCreateModal {...props} />);
    const createBtn = screen.getByText('Create').closest('button')!;
    expect(createBtn).toBeDisabled();
  });

  it('disables Create button when name is empty', () => {
    const props = defaultCreateProps();
    props.newNicGroupHostId = 'host-1';
    props.newNicGroupName = '';
    render(<NicGroupCreateModal {...props} />);
    const createBtn = screen.getByText('Create').closest('button')!;
    expect(createBtn).toBeDisabled();
  });

  it('disables Create button when name is only whitespace', () => {
    const props = defaultCreateProps();
    props.newNicGroupHostId = 'host-1';
    props.newNicGroupName = '   ';
    render(<NicGroupCreateModal {...props} />);
    const createBtn = screen.getByText('Create').closest('button')!;
    expect(createBtn).toBeDisabled();
  });

  it('disables Create button when both host and name are empty', () => {
    const props = defaultCreateProps();
    render(<NicGroupCreateModal {...props} />);
    const createBtn = screen.getByText('Create').closest('button')!;
    expect(createBtn).toBeDisabled();
  });

  it('enables Create button when host and name are provided', () => {
    const props = defaultCreateProps();
    props.newNicGroupHostId = 'host-1';
    props.newNicGroupName = 'uplink-a';
    render(<NicGroupCreateModal {...props} />);
    const createBtn = screen.getByText('Create').closest('button')!;
    expect(createBtn).not.toBeDisabled();
  });

  it('enables Create button even without description', () => {
    const props = defaultCreateProps();
    props.newNicGroupHostId = 'host-1';
    props.newNicGroupName = 'uplink-a';
    props.newNicGroupDescription = '';
    render(<NicGroupCreateModal {...props} />);
    const createBtn = screen.getByText('Create').closest('button')!;
    expect(createBtn).not.toBeDisabled();
  });

  // ── Actions ──

  it('calls onCreate when Create button is clicked', async () => {
    const user = userEvent.setup();
    const props = defaultCreateProps();
    props.newNicGroupHostId = 'host-1';
    props.newNicGroupName = 'uplink-a';
    render(<NicGroupCreateModal {...props} />);
    await user.click(screen.getByText('Create').closest('button')!);
    expect(props.onCreate).toHaveBeenCalledTimes(1);
  });

  it('calls onClose when Cancel button is clicked', async () => {
    const user = userEvent.setup();
    const props = defaultCreateProps();
    render(<NicGroupCreateModal {...props} />);
    await user.click(screen.getByText('Cancel'));
    expect(props.onClose).toHaveBeenCalledTimes(1);
  });

  it('calls onClose when X button is clicked', async () => {
    const user = userEvent.setup();
    const props = defaultCreateProps();
    const { container } = render(<NicGroupCreateModal {...props} />);
    const closeIcon = container.querySelector('i.fa-times')!;
    await user.click(closeIcon.closest('button')!);
    expect(props.onClose).toHaveBeenCalledTimes(1);
  });

  // ── Loading State ──

  it('disables Create button when creatingNicGroup is true', () => {
    const props = defaultCreateProps();
    props.newNicGroupHostId = 'host-1';
    props.newNicGroupName = 'uplink-a';
    props.creatingNicGroup = true;
    render(<NicGroupCreateModal {...props} />);
    const createBtn = screen.getByText('Creating...').closest('button')!;
    expect(createBtn).toBeDisabled();
  });

  it('shows "Creating..." text when creatingNicGroup is true', () => {
    const props = defaultCreateProps();
    props.creatingNicGroup = true;
    render(<NicGroupCreateModal {...props} />);
    expect(screen.getByText('Creating...')).toBeInTheDocument();
  });

  it('shows "Create" text when creatingNicGroup is false', () => {
    render(<NicGroupCreateModal {...defaultCreateProps()} />);
    expect(screen.getByText('Create')).toBeInTheDocument();
    expect(screen.queryByText('Creating...')).not.toBeInTheDocument();
  });
});

// ============================================================================
// NicGroupMemberModal
// ============================================================================

describe('NicGroupMemberModal', () => {
  const defaultMemberProps = () => ({
    memberGroup: makeNicGroup(),
    managedInterfaces: [
      makeManagedInterface({ id: 'mi-1', host_id: 'host-1', name: 'ens192.100', ip_address: '10.10.10.1' }),
      makeManagedInterface({ id: 'mi-2', host_id: 'host-1', name: 'ens224', interface_type: 'external', ip_address: null }),
      makeManagedInterface({ id: 'mi-3', host_id: 'host-2', name: 'ens256', interface_type: 'transport', ip_address: '10.20.20.1' }),
    ],
    memberInterfaceId: '',
    setMemberInterfaceId: vi.fn(),
    memberRole: 'transport',
    setMemberRole: vi.fn(),
    addingNicGroupMember: false,
    onAdd: vi.fn(),
    onClose: vi.fn(),
  });

  beforeEach(() => {
    vi.clearAllMocks();
  });

  // ── Rendering ──

  it('renders the Add NIC Group Member heading', () => {
    render(<NicGroupMemberModal {...defaultMemberProps()} />);
    expect(screen.getByText('Add NIC Group Member')).toBeInTheDocument();
  });

  it('displays the group name', () => {
    render(<NicGroupMemberModal {...defaultMemberProps()} />);
    expect(screen.getByText('uplink-a')).toBeInTheDocument();
  });

  it('renders Managed Interface select', () => {
    render(<NicGroupMemberModal {...defaultMemberProps()} />);
    expect(screen.getByText('Managed Interface')).toBeInTheDocument();
    expect(screen.getByText('Select an interface...')).toBeInTheDocument();
  });

  it('filters interfaces to show only those matching the group host_id', () => {
    render(<NicGroupMemberModal {...defaultMemberProps()} />);
    // host-1 interfaces: ens192.100 and ens224
    expect(screen.getByText(/ens192\.100/)).toBeInTheDocument();
    expect(screen.getByText(/ens224/)).toBeInTheDocument();
    // host-2 interface should NOT be shown
    expect(screen.queryByText(/ens256/)).not.toBeInTheDocument();
  });

  it('renders interface details in option text (type and IP)', () => {
    render(<NicGroupMemberModal {...defaultMemberProps()} />);
    // ens192.100 has type=transport, ip=10.10.10.1
    expect(screen.getByText('ens192.100 (transport, 10.10.10.1)')).toBeInTheDocument();
    // ens224 has type=external, no IP
    expect(screen.getByText('ens224 (external)')).toBeInTheDocument();
  });

  it('renders Role select with default options', () => {
    render(<NicGroupMemberModal {...defaultMemberProps()} />);
    expect(screen.getByText('Role')).toBeInTheDocument();
    expect(screen.getByText('transport')).toBeInTheDocument();
  });

  it('renders all role options', () => {
    const { container } = render(<NicGroupMemberModal {...defaultMemberProps()} />);
    const roleSelect = container.querySelectorAll('select')[1]; // second select
    const options = roleSelect.querySelectorAll('option');
    const roleValues = Array.from(options).map(o => o.value);
    expect(roleValues).toContain('transport');
    expect(roleValues).toContain('external');
    expect(roleValues).toContain('custom');
    expect(roleValues).toContain('other');
  });

  it('renders Cancel and Add Member buttons', () => {
    render(<NicGroupMemberModal {...defaultMemberProps()} />);
    expect(screen.getByText('Cancel')).toBeInTheDocument();
    expect(screen.getByText('Add Member')).toBeInTheDocument();
  });

  // ── Input Handling ──

  it('calls setMemberInterfaceId when interface is selected', async () => {
    const user = userEvent.setup();
    const props = defaultMemberProps();
    render(<NicGroupMemberModal {...props} />);
    const selects = screen.getAllByRole('combobox');
    // First select is for interfaces
    await user.selectOptions(selects[0], 'mi-1');
    expect(props.setMemberInterfaceId).toHaveBeenCalledWith('mi-1');
  });

  it('calls setMemberRole when role is changed', async () => {
    const user = userEvent.setup();
    const props = defaultMemberProps();
    render(<NicGroupMemberModal {...props} />);
    const selects = screen.getAllByRole('combobox');
    // Second select is for role
    await user.selectOptions(selects[1], 'external');
    expect(props.setMemberRole).toHaveBeenCalledWith('external');
  });

  // ── Validation ──

  it('disables Add Member button when no interface is selected', () => {
    const props = defaultMemberProps();
    props.memberInterfaceId = '';
    render(<NicGroupMemberModal {...props} />);
    const addBtn = screen.getByText('Add Member').closest('button')!;
    expect(addBtn).toBeDisabled();
  });

  it('enables Add Member button when interface is selected', () => {
    const props = defaultMemberProps();
    props.memberInterfaceId = 'mi-1';
    render(<NicGroupMemberModal {...props} />);
    const addBtn = screen.getByText('Add Member').closest('button')!;
    expect(addBtn).not.toBeDisabled();
  });

  // ── Actions ──

  it('calls onAdd when Add Member button is clicked', async () => {
    const user = userEvent.setup();
    const props = defaultMemberProps();
    props.memberInterfaceId = 'mi-1';
    render(<NicGroupMemberModal {...props} />);
    await user.click(screen.getByText('Add Member').closest('button')!);
    expect(props.onAdd).toHaveBeenCalledTimes(1);
  });

  it('calls onClose when Cancel button is clicked', async () => {
    const user = userEvent.setup();
    const props = defaultMemberProps();
    render(<NicGroupMemberModal {...props} />);
    await user.click(screen.getByText('Cancel'));
    expect(props.onClose).toHaveBeenCalledTimes(1);
  });

  it('calls onClose when X close button is clicked', async () => {
    const user = userEvent.setup();
    const props = defaultMemberProps();
    const { container } = render(<NicGroupMemberModal {...props} />);
    const closeIcon = container.querySelector('i.fa-times')!;
    await user.click(closeIcon.closest('button')!);
    expect(props.onClose).toHaveBeenCalledTimes(1);
  });

  // ── Loading State ──

  it('disables Add Member button when addingNicGroupMember is true', () => {
    const props = defaultMemberProps();
    props.memberInterfaceId = 'mi-1';
    props.addingNicGroupMember = true;
    render(<NicGroupMemberModal {...props} />);
    const addBtn = screen.getByText('Adding...').closest('button')!;
    expect(addBtn).toBeDisabled();
  });

  it('shows "Adding..." text when addingNicGroupMember is true', () => {
    const props = defaultMemberProps();
    props.addingNicGroupMember = true;
    render(<NicGroupMemberModal {...props} />);
    expect(screen.getByText('Adding...')).toBeInTheDocument();
  });

  it('shows "Add Member" text when addingNicGroupMember is false', () => {
    render(<NicGroupMemberModal {...defaultMemberProps()} />);
    expect(screen.getByText('Add Member')).toBeInTheDocument();
    expect(screen.queryByText('Adding...')).not.toBeInTheDocument();
  });

  // ── Edge Cases ──

  it('shows no interface options when no managed interfaces match host', () => {
    const props = defaultMemberProps();
    props.managedInterfaces = [
      makeManagedInterface({ id: 'mi-3', host_id: 'host-99', name: 'eth0' }),
    ];
    render(<NicGroupMemberModal {...props} />);
    // Only the placeholder option should exist
    const selects = screen.getAllByRole('combobox');
    const interfaceSelect = selects[0];
    const options = interfaceSelect.querySelectorAll('option');
    // 1 = placeholder "Select an interface..."
    expect(options.length).toBe(1);
  });

  it('renders correctly with empty managed interfaces array', () => {
    const props = defaultMemberProps();
    props.managedInterfaces = [];
    render(<NicGroupMemberModal {...props} />);
    expect(screen.getByText('Add NIC Group Member')).toBeInTheDocument();
    const selects = screen.getAllByRole('combobox');
    const interfaceSelect = selects[0];
    const options = interfaceSelect.querySelectorAll('option');
    expect(options.length).toBe(1);
  });
});
