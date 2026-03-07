import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { BrowserRouter } from 'react-router-dom';
import React from 'react';
import HostsTab from './HostsTab';
import type { AgentImagesDetailResponse, HostDetailed, UpdateStatus, SyncStrategy } from './infrastructureTypes';

// Mock HostCard
vi.mock('./HostCard', () => ({
  default: ({
    host,
    isUpdateAvailable,
  }: {
    host: HostDetailed;
    isUpdateAvailable: boolean;
  }) => (
    <div data-testid={`host-card-${host.id}`}>
      <span data-testid="host-name">{host.name}</span>
      {isUpdateAvailable && <span data-testid="update-available" />}
    </div>
  ),
}));

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

function defaultProps() {
  return {
    hosts: [] as HostDetailed[],
    hostsLoading: false,
    hostsError: null as string | null,
    latestVersion: '1.1.0',
    outdatedCount: 0,
    expandedLabs: new Set<string>(),
    expandedContainers: new Set<string>(),
    expandedVMs: new Set<string>(),
    expandedImages: new Set<string>(),
    agentImageDetails: {} as Record<string, AgentImagesDetailResponse>,
    agentImagesLoading: new Set<string>(),
    agentImagesCleaning: new Set<string>(),
    updatingAgents: new Set<string>(),
    updateStatuses: new Map<string, UpdateStatus>(),
    isUpdateAvailable: vi.fn(() => false),
    onToggleLabs: vi.fn(),
    onToggleContainers: vi.fn(),
    onToggleVMs: vi.fn(),
    onToggleImages: vi.fn(),
    onCleanupStaleImages: vi.fn(),
    onUpdateSyncStrategy: vi.fn(),
    onTriggerUpdate: vi.fn(),
    onTriggerRebuild: vi.fn(),
    onTriggerBulkUpdate: vi.fn(),
    onCustomUpdate: vi.fn(),
    onDeregister: vi.fn(),
  };
}

describe('HostsTab', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // ── Title and latest version ──

  it('renders title "Compute Hosts"', () => {
    render(<HostsTab {...defaultProps()} />);
    expect(screen.getByText('Compute Hosts')).toBeInTheDocument();
  });

  it('displays latest version', () => {
    render(<HostsTab {...defaultProps()} />);
    expect(screen.getByText(/Latest: v1\.1\.0/)).toBeInTheDocument();
  });

  // ── Empty state ──

  it('shows empty state when no hosts', () => {
    render(<HostsTab {...defaultProps()} />);
    expect(screen.getByText('No Hosts Registered')).toBeInTheDocument();
    expect(screen.getByText('Start an agent to register hosts with the controller.')).toBeInTheDocument();
  });

  // ── Loading state ──

  it('shows loading spinner when hostsLoading and no hosts', () => {
    const props = defaultProps();
    props.hostsLoading = true;
    render(<HostsTab {...props} />);

    expect(screen.getByText('Loading hosts...')).toBeInTheDocument();
  });

  it('does not show loading spinner when hostsLoading but hosts already loaded', () => {
    const props = defaultProps();
    props.hostsLoading = true;
    props.hosts = [makeHost()];
    render(<HostsTab {...props} />);

    expect(screen.queryByText('Loading hosts...')).not.toBeInTheDocument();
    expect(screen.getByTestId('host-card-host-1')).toBeInTheDocument();
  });

  // ── Error state ──

  it('shows error message when hostsError is set', () => {
    const props = defaultProps();
    props.hostsError = 'Failed to fetch hosts';
    render(<HostsTab {...props} />);

    expect(screen.getByText('Failed to fetch hosts')).toBeInTheDocument();
  });

  // ── Host cards rendering ──

  it('renders a HostCard for each host', () => {
    const props = defaultProps();
    props.hosts = [
      makeHost({ id: 'h1', name: 'Agent-01' }),
      makeHost({ id: 'h2', name: 'Agent-02' }),
      makeHost({ id: 'h3', name: 'Agent-03' }),
    ];
    render(<HostsTab {...props} />);

    expect(screen.getByTestId('host-card-h1')).toBeInTheDocument();
    expect(screen.getByTestId('host-card-h2')).toBeInTheDocument();
    expect(screen.getByTestId('host-card-h3')).toBeInTheDocument();
  });

  it('passes isUpdateAvailable result to HostCard', () => {
    const props = defaultProps();
    const host = makeHost({ id: 'h-outdated' });
    props.hosts = [host];
    props.isUpdateAvailable = vi.fn(() => true);
    render(<HostsTab {...props} />);

    expect(screen.getByTestId('update-available')).toBeInTheDocument();
    expect(props.isUpdateAvailable).toHaveBeenCalledWith(host);
  });

  // ── Online/Offline counts ──

  it('shows correct online and offline counts', () => {
    const props = defaultProps();
    props.hosts = [
      makeHost({ id: 'h1', status: 'online' }),
      makeHost({ id: 'h2', status: 'online' }),
      makeHost({ id: 'h3', status: 'offline' }),
    ];
    render(<HostsTab {...props} />);

    expect(screen.getByText('2 Online')).toBeInTheDocument();
    expect(screen.getByText('1 Offline')).toBeInTheDocument();
  });

  // ── Bulk update button ──

  it('shows bulk update button when outdatedCount > 0', () => {
    const props = defaultProps();
    props.outdatedCount = 3;
    render(<HostsTab {...props} />);

    expect(screen.getByText('Update 3 Agents')).toBeInTheDocument();
  });

  it('uses singular for 1 outdated agent', () => {
    const props = defaultProps();
    props.outdatedCount = 1;
    render(<HostsTab {...props} />);

    expect(screen.getByText('Update 1 Agent')).toBeInTheDocument();
  });

  it('hides bulk update button when outdatedCount is 0', () => {
    render(<HostsTab {...defaultProps()} />);
    expect(screen.queryByText(/Update.*Agent/)).not.toBeInTheDocument();
  });

  it('calls onTriggerBulkUpdate when bulk update button is clicked', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    props.outdatedCount = 2;
    render(<HostsTab {...props} />);

    await user.click(screen.getByText('Update 2 Agents'));
    expect(props.onTriggerBulkUpdate).toHaveBeenCalledTimes(1);
  });
});
