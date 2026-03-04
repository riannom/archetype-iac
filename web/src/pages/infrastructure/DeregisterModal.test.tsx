import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import React from 'react';
import DeregisterModal from './DeregisterModal';
import type { HostDetailed } from './infrastructureTypes';

// ============================================================================
// Helpers
// ============================================================================

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
    labs: [],
    lab_count: 0,
    started_at: null,
    last_heartbeat: new Date().toISOString(),
    git_sha: 'abc1234567890',
    last_error: null,
    error_since: null,
    data_plane_address: null,
    ...overrides,
  };
}

function makeDeregisterInfo(overrides: Partial<NonNullable<Parameters<typeof DeregisterModal>[0]['deregisterInfo']>> = {}) {
  return {
    agent_name: 'Agent-01',
    agent_status: 'online',
    labs_assigned: 0,
    running_labs: [] as { id: string; name: string; state: string }[],
    node_placements: 0,
    nodes_assigned: 0,
    vxlan_tunnels: 0,
    cross_host_links: 0,
    ...overrides,
  };
}

function defaultProps() {
  return {
    deregisterHost: makeHost(),
    deregisterInfo: makeDeregisterInfo(),
    deregisterLoading: false,
    deregistering: false,
    onConfirm: vi.fn(),
    onCancel: vi.fn(),
  };
}

describe('DeregisterModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // ── Title and structure ──

  it('renders the deregister host title', () => {
    render(<DeregisterModal {...defaultProps()} />);
    expect(screen.getByText('Deregister Host')).toBeInTheDocument();
  });

  it('displays the agent name in confirmation text', () => {
    render(<DeregisterModal {...defaultProps()} />);
    expect(screen.getByText('Agent-01')).toBeInTheDocument();
  });

  // ── Loading state ──

  it('shows loading spinner when deregisterLoading is true', () => {
    const props = defaultProps();
    props.deregisterLoading = true;
    render(<DeregisterModal {...props} />);

    expect(screen.getByText('Loading impact summary...')).toBeInTheDocument();
  });

  it('disables confirm button when loading', () => {
    const props = defaultProps();
    props.deregisterLoading = true;
    render(<DeregisterModal {...props} />);

    const confirmBtn = screen.getByText('Deregister');
    expect(confirmBtn).toBeDisabled();
  });

  // ── Deregistering state ──

  it('shows spinner text when deregistering', () => {
    const props = defaultProps();
    props.deregistering = true;
    render(<DeregisterModal {...props} />);

    expect(screen.getByText('Deregistering...')).toBeInTheDocument();
  });

  it('disables confirm button when deregistering', () => {
    const props = defaultProps();
    props.deregistering = true;
    render(<DeregisterModal {...props} />);

    const btn = screen.getByText('Deregistering...');
    expect(btn.closest('button')).toBeDisabled();
  });

  // ── Impact summary: labs assigned ──

  it('shows labs assigned cleanup item', () => {
    const props = defaultProps();
    props.deregisterInfo = makeDeregisterInfo({ labs_assigned: 3 });
    render(<DeregisterModal {...props} />);

    expect(screen.getByText(/Unassign 3 labs from this host/)).toBeInTheDocument();
  });

  it('uses singular when labs_assigned is 1', () => {
    const props = defaultProps();
    props.deregisterInfo = makeDeregisterInfo({ labs_assigned: 1 });
    render(<DeregisterModal {...props} />);

    expect(screen.getByText(/Unassign 1 lab from this host/)).toBeInTheDocument();
  });

  // ── Impact summary: node placements ──

  it('shows node placements cleanup item', () => {
    const props = defaultProps();
    props.deregisterInfo = makeDeregisterInfo({ node_placements: 5 });
    render(<DeregisterModal {...props} />);

    expect(screen.getByText(/Remove 5 node placements/)).toBeInTheDocument();
  });

  // ── Impact summary: nodes assigned ──

  it('shows nodes assigned cleanup item', () => {
    const props = defaultProps();
    props.deregisterInfo = makeDeregisterInfo({ nodes_assigned: 2 });
    render(<DeregisterModal {...props} />);

    expect(screen.getByText(/Unassign 2 nodes from this host/)).toBeInTheDocument();
  });

  // ── Impact summary: cross-host links ──

  it('shows cross-host links cleanup item', () => {
    const props = defaultProps();
    props.deregisterInfo = makeDeregisterInfo({ cross_host_links: 4 });
    render(<DeregisterModal {...props} />);

    expect(screen.getByText(/Clear 4 cross-host link references/)).toBeInTheDocument();
  });

  // ── Impact summary: VXLAN tunnels ──

  it('shows VXLAN tunnels cleanup item', () => {
    const props = defaultProps();
    props.deregisterInfo = makeDeregisterInfo({ vxlan_tunnels: 2 });
    render(<DeregisterModal {...props} />);

    expect(screen.getByText(/Delete 2 VXLAN tunnels/)).toBeInTheDocument();
  });

  it('uses singular for 1 tunnel', () => {
    const props = defaultProps();
    props.deregisterInfo = makeDeregisterInfo({ vxlan_tunnels: 1 });
    render(<DeregisterModal {...props} />);

    expect(screen.getByText(/Delete 1 VXLAN tunnel$/)).toBeInTheDocument();
  });

  // ── No dependent resources ──

  it('shows "No dependent resources found" when all counts are zero', () => {
    render(<DeregisterModal {...defaultProps()} />);
    expect(screen.getByText('No dependent resources found')).toBeInTheDocument();
  });

  // ── Running labs warning ──

  it('shows warning when running labs exist', () => {
    const props = defaultProps();
    props.deregisterInfo = makeDeregisterInfo({
      running_labs: [
        { id: 'lab-1', name: 'Lab Alpha', state: 'running' },
        { id: 'lab-2', name: 'Lab Beta', state: 'running' },
      ],
    });
    render(<DeregisterModal {...props} />);

    expect(screen.getByText(/This host has 2 running labs/)).toBeInTheDocument();
    expect(screen.getByText('Lab Alpha')).toBeInTheDocument();
    expect(screen.getByText('Lab Beta')).toBeInTheDocument();
  });

  it('does not show warning when no running labs', () => {
    render(<DeregisterModal {...defaultProps()} />);
    expect(screen.queryByText(/running lab/)).not.toBeInTheDocument();
  });

  // ── Preservation notice ──

  it('displays data preservation notice', () => {
    render(<DeregisterModal {...defaultProps()} />);
    expect(
      screen.getByText(/Topology data .* is preserved\. The agent can re-register later\./)
    ).toBeInTheDocument();
  });

  // ── Button actions ──

  it('calls onConfirm when Deregister button is clicked', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    render(<DeregisterModal {...props} />);

    await user.click(screen.getByText('Deregister'));
    expect(props.onConfirm).toHaveBeenCalledTimes(1);
  });

  it('calls onCancel when Cancel button is clicked', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    render(<DeregisterModal {...props} />);

    await user.click(screen.getByText('Cancel'));
    expect(props.onCancel).toHaveBeenCalledTimes(1);
  });

  it('calls onCancel when X close button is clicked', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    const { container } = render(<DeregisterModal {...props} />);

    // The X button is the first button in the header
    const headerBtns = container.querySelectorAll('.p-6.border-b button');
    expect(headerBtns.length).toBe(1);
    await user.click(headerBtns[0]);
    expect(props.onCancel).toHaveBeenCalledTimes(1);
  });

  // ── Null deregisterInfo ──

  it('renders nothing in body when deregisterInfo is null and not loading', () => {
    const props = defaultProps();
    props.deregisterInfo = null;
    render(<DeregisterModal {...props} />);

    expect(screen.getByText('Deregister Host')).toBeInTheDocument();
    expect(screen.queryByText('No dependent resources found')).not.toBeInTheDocument();
    expect(screen.queryByText('Loading impact summary...')).not.toBeInTheDocument();
  });
});
