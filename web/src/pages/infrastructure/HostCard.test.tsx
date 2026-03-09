import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { BrowserRouter } from 'react-router-dom';
import HostCard from './HostCard';
import type { AgentImagesDetailResponse, HostDetailed, UpdateStatus, SyncStrategy } from './infrastructureTypes';

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

const defaultProps = () => ({
  host: makeHost(),
  latestVersion: '1.1.0',
  isUpdateAvailable: false,
  expandedLabs: new Set<string>(),
  expandedContainers: new Set<string>(),
  expandedVMs: new Set<string>(),
  expandedImages: new Set<string>(),
  agentImageDetails: {} as Record<string, AgentImagesDetailResponse>,
  agentImagesLoading: new Set<string>(),
  agentImagesCleaning: new Set<string>(),
  updatingAgents: new Set<string>(),
  updateStatuses: new Map<string, UpdateStatus>(),
  onToggleLabs: vi.fn(),
  onToggleContainers: vi.fn(),
  onToggleVMs: vi.fn(),
  onToggleImages: vi.fn(),
  onCleanupStaleImages: vi.fn(),
  onUpdateSyncStrategy: vi.fn(),
  onTriggerUpdate: vi.fn(),
  onTriggerRebuild: vi.fn(),
  onCustomUpdate: vi.fn(),
  onDeregister: vi.fn(),
});

function renderWithRouter(ui: React.ReactElement) {
  return render(<BrowserRouter>{ui}</BrowserRouter>);
}

describe('HostCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // ── Basic Rendering ──

  it('renders host name and address', () => {
    renderWithRouter(<HostCard {...defaultProps()} />);
    expect(screen.getByText('Agent-01')).toBeInTheDocument();
    expect(screen.getByText('10.0.0.1:8001')).toBeInTheDocument();
  });

  it('renders role badge', () => {
    renderWithRouter(<HostCard {...defaultProps()} />);
    // getRoleLabel('agent') returns "Agent"
    expect(screen.getByText('Agent')).toBeInTheDocument();
  });

  it('renders version and git sha', () => {
    renderWithRouter(<HostCard {...defaultProps()} />);
    expect(screen.getByText('v1.0.0')).toBeInTheDocument();
    expect(screen.getByText('(abc1234)')).toBeInTheDocument();
  });

  // ── Status Indicators ──

  it('renders online status text', () => {
    renderWithRouter(<HostCard {...defaultProps()} />);
    // getConnectionStatusText('online') produces a status text
    expect(screen.getByText('Online')).toBeInTheDocument();
  });

  it('renders offline status text when host is offline', () => {
    const props = defaultProps();
    props.host = makeHost({ status: 'offline' });
    renderWithRouter(<HostCard {...props} />);
    expect(screen.getByText('Offline')).toBeInTheDocument();
  });

  // ── Resource Bars ──

  it('renders CPU usage percentage', () => {
    renderWithRouter(<HostCard {...defaultProps()} />);
    expect(screen.getByText('25%')).toBeInTheDocument();
  });

  it('renders memory usage in GB format', () => {
    renderWithRouter(<HostCard {...defaultProps()} />);
    expect(screen.getByText('8.0GB / 16.0GB')).toBeInTheDocument();
  });

  it('renders storage usage in GB format', () => {
    renderWithRouter(<HostCard {...defaultProps()} />);
    expect(screen.getByText('120.0GB / 200.0GB')).toBeInTheDocument();
  });

  it('shows CPU critical icon at 95% usage', () => {
    const props = defaultProps();
    props.host = makeHost({
      resource_usage: {
        cpu_percent: 96,
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
    });
    const { container } = renderWithRouter(<HostCard {...props} />);
    const criticalIcon = container.querySelector('i[title="CPU critical - deployment may fail"]');
    expect(criticalIcon).toBeInTheDocument();
  });

  it('shows memory warning icon between 80-95% usage', () => {
    const props = defaultProps();
    props.host = makeHost({
      resource_usage: {
        cpu_percent: 25,
        memory_percent: 85,
        memory_used_gb: 14,
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
    });
    const { container } = renderWithRouter(<HostCard {...props} />);
    const warningIcon = container.querySelector('i[title="Memory high - consider distributing nodes across agents"]');
    expect(warningIcon).toBeInTheDocument();
  });

  it('uses memory totals as the display source of truth when percent is stale', () => {
    const props = defaultProps();
    props.host = makeHost({
      resource_usage: {
        cpu_percent: 25,
        memory_percent: 94,
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
    });
    const { container } = renderWithRouter(<HostCard {...props} />);
    const warningIcon = container.querySelector('i[title="Memory high - consider distributing nodes across agents"]');
    const criticalIcon = container.querySelector('i[title="Memory critical - deployment will likely fail"]');
    expect(screen.getByText('8.0GB / 16.0GB')).toBeInTheDocument();
    expect(warningIcon).not.toBeInTheDocument();
    expect(criticalIcon).not.toBeInTheDocument();
  });

  // ── Error Alert ──

  it('shows error alert when host has last_error', () => {
    const props = defaultProps();
    props.host = makeHost({
      last_error: 'Connection refused',
      error_since: '2026-01-01T00:00:00Z',
    });
    renderWithRouter(<HostCard {...props} />);
    expect(screen.getByText('Agent Error')).toBeInTheDocument();
    expect(screen.getByText('Connection refused')).toBeInTheDocument();
  });

  it('does not show error alert when last_error is null', () => {
    renderWithRouter(<HostCard {...defaultProps()} />);
    expect(screen.queryByText('Agent Error')).not.toBeInTheDocument();
  });

  // ── Container Section ──

  it('renders container count summary', () => {
    renderWithRouter(<HostCard {...defaultProps()} />);
    expect(screen.getByText(/\/10 containers/)).toBeInTheDocument();
  });

  it('toggles containers on click', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    renderWithRouter(<HostCard {...props} />);
    const containerBtn = screen.getByText(/containers/).closest('button')!;
    await user.click(containerBtn);
    expect(props.onToggleContainers).toHaveBeenCalledWith('host-1');
  });

  // ── Labs Section ──

  it('renders lab list when host has labs', () => {
    const props = defaultProps();
    props.host = makeHost({
      labs: [
        { id: 'lab-1', name: 'My Lab', state: 'running' },
        { id: 'lab-2', name: 'Test Lab', state: 'stopped' },
      ],
      lab_count: 2,
    });
    renderWithRouter(<HostCard {...props} />);
    expect(screen.getByText('My Lab')).toBeInTheDocument();
    expect(screen.getByText('Test Lab')).toBeInTheDocument();
  });

  it('shows "Show all" button when more than 3 labs', () => {
    const labs = Array.from({ length: 5 }, (_, i) => ({
      id: `lab-${i}`,
      name: `Lab ${i}`,
      state: 'running',
    }));
    const props = defaultProps();
    props.host = makeHost({ labs, lab_count: 5 });
    renderWithRouter(<HostCard {...props} />);
    expect(screen.getByText('Show all 5')).toBeInTheDocument();
  });

  it('calls onToggleLabs when "Show all" is clicked', async () => {
    const user = userEvent.setup();
    const labs = Array.from({ length: 5 }, (_, i) => ({
      id: `lab-${i}`,
      name: `Lab ${i}`,
      state: 'running',
    }));
    const props = defaultProps();
    props.host = makeHost({ labs, lab_count: 5 });
    renderWithRouter(<HostCard {...props} />);
    await user.click(screen.getByText('Show all 5'));
    expect(props.onToggleLabs).toHaveBeenCalledWith('host-1');
  });

  it('shows stale image details when provided for an expanded host', () => {
    const props = defaultProps();
    props.host = makeHost({ images: [] });
    props.expandedImages = new Set(['host-1']);
    props.agentImageDetails = {
      'host-1': {
        agent_id: 'host-1',
        agent_name: 'Agent-01',
        images: props.host.images,
        inventory: [],
        inventory_refreshed_at: '2026-01-01T00:00:00Z',
        stale_images: [{
          reference: '/var/lib/archetype/images/stale.qcow2',
          display_reference: '/var/lib/archetype/images/stale.qcow2',
          kind: 'qcow2',
          size_bytes: 4096,
          created: null,
          device_id: null,
          tracked_image_id: null,
          tracked_status: null,
          is_needed: false,
          is_stale: true,
          reason: 'Not referenced by catalog or active nodes',
        }],
      },
    };

    renderWithRouter(<HostCard {...props} />);
    expect(screen.getByText(/1 stale/)).toBeInTheDocument();
    expect(screen.getByText('Stale On Agent')).toBeInTheDocument();
    expect(screen.getByText('stale.qcow2')).toBeInTheDocument();
    expect(screen.getByText(/Inventory refreshed/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /clean stale/i })).toBeInTheDocument();
  });

  it('calls onCleanupStaleImages when clean stale is clicked', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    props.host = makeHost({ images: [] });
    props.expandedImages = new Set(['host-1']);
    props.agentImageDetails = {
      'host-1': {
        agent_id: 'host-1',
        agent_name: 'Agent-01',
        images: [],
        inventory: [],
        inventory_refreshed_at: '2026-01-01T00:00:00Z',
        stale_images: [{
          reference: '/var/lib/archetype/images/stale.qcow2',
          display_reference: '/var/lib/archetype/images/stale.qcow2',
          kind: 'qcow2',
          size_bytes: 4096,
          created: null,
          device_id: null,
          tracked_image_id: null,
          tracked_status: null,
          is_needed: false,
          is_stale: true,
          reason: 'Not referenced by catalog or active nodes',
        }],
      },
    };

    renderWithRouter(<HostCard {...props} />);
    await user.click(screen.getByRole('button', { name: /clean stale/i }));
    expect(props.onCleanupStaleImages).toHaveBeenCalledWith('host-1');
  });

  // ── Update Button ──

  it('shows update button for systemd agent when update available', () => {
    const props = defaultProps();
    props.isUpdateAvailable = true;
    renderWithRouter(<HostCard {...props} />);
    expect(screen.getByText(/Update to v1.1.0/)).toBeInTheDocument();
  });

  it('calls onTriggerUpdate when update button is clicked', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    props.isUpdateAvailable = true;
    renderWithRouter(<HostCard {...props} />);
    await user.click(screen.getByText(/Update to v1.1.0/).closest('button')!);
    expect(props.onTriggerUpdate).toHaveBeenCalledWith('host-1');
  });

  it('shows rebuild button for docker agent when update available', () => {
    const props = defaultProps();
    props.isUpdateAvailable = true;
    props.host = makeHost({ deployment_mode: 'docker' });
    renderWithRouter(<HostCard {...props} />);
    expect(screen.getByText('Rebuild Container')).toBeInTheDocument();
  });

  it('calls onTriggerRebuild when rebuild button clicked', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    props.isUpdateAvailable = true;
    props.host = makeHost({ deployment_mode: 'docker' });
    renderWithRouter(<HostCard {...props} />);
    await user.click(screen.getByText('Rebuild Container').closest('button')!);
    expect(props.onTriggerRebuild).toHaveBeenCalledWith('host-1');
  });

  it('shows update progress when agent is updating', () => {
    const props = defaultProps();
    props.updatingAgents = new Set(['host-1']);
    props.updateStatuses = new Map([
      ['host-1', {
        job_id: 'j-1',
        agent_id: 'host-1',
        from_version: '1.0.0',
        to_version: '1.1.0',
        status: 'downloading',
        progress_percent: 45,
        error_message: null,
      }],
    ]);
    renderWithRouter(<HostCard {...props} />);
    expect(screen.getByText('Downloading...')).toBeInTheDocument();
    expect(screen.getByText('45%')).toBeInTheDocument();
  });

  // ── Image Sync Strategy ──

  it('renders image sync strategy dropdown', () => {
    renderWithRouter(<HostCard {...defaultProps()} />);
    const select = screen.getByDisplayValue('On Demand');
    expect(select).toBeInTheDocument();
  });

  it('calls onUpdateSyncStrategy when strategy is changed', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    renderWithRouter(<HostCard {...props} />);
    const select = screen.getByDisplayValue('On Demand');
    await user.selectOptions(select, 'push');
    expect(props.onUpdateSyncStrategy).toHaveBeenCalledWith('host-1', 'push');
  });

  // ── Deregister ──

  it('renders Deregister Host button', () => {
    renderWithRouter(<HostCard {...defaultProps()} />);
    expect(screen.getByText('Deregister Host')).toBeInTheDocument();
  });

  it('calls onDeregister when Deregister Host is clicked', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    renderWithRouter(<HostCard {...props} />);
    await user.click(screen.getByText('Deregister Host').closest('button')!);
    expect(props.onDeregister).toHaveBeenCalledWith(props.host);
  });

  // ── Opacity for offline ──

  it('applies opacity class for offline host', () => {
    const props = defaultProps();
    props.host = makeHost({ status: 'offline' });
    const { container } = renderWithRouter(<HostCard {...props} />);
    const card = container.firstChild as HTMLElement;
    expect(card.className).toContain('opacity-50');
  });

  it('does not apply opacity class for online host', () => {
    const { container } = renderWithRouter(<HostCard {...defaultProps()} />);
    const card = container.firstChild as HTMLElement;
    expect(card.className).not.toContain('opacity-50');
  });
});
