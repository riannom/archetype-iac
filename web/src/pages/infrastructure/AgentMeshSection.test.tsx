import React from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import AgentMeshSection from './AgentMeshSection';
import type {
  AgentMeshResponse,
  AgentNetworkConfig,
  HostDetailed,
  ManagedInterface,
} from './infrastructureTypes';

// ── Factories ──

function makeAgent(overrides: Partial<AgentMeshResponse['agents'][0]> = {}) {
  return {
    id: 'agent-1',
    name: 'Agent-01',
    address: '10.0.0.1:8001',
    status: 'online',
    ...overrides,
  };
}

function makeLink(overrides: Partial<AgentMeshResponse['links'][0]> = {}) {
  return {
    id: 'link-1',
    source_agent_id: 'agent-1',
    source_agent_name: 'Agent-01',
    target_agent_id: 'agent-2',
    target_agent_name: 'Agent-02',
    link_type: 'vxlan',
    configured_mtu: 1450,
    tested_mtu: null,
    last_test_at: null,
    test_status: 'pending',
    test_error: null,
    latency_ms: null,
    test_path: 'management',
    ...overrides,
  };
}

function makeMesh(overrides: Partial<AgentMeshResponse> = {}): AgentMeshResponse {
  return {
    agents: [
      makeAgent({ id: 'agent-1', name: 'Agent-01' }),
      makeAgent({ id: 'agent-2', name: 'Agent-02', address: '10.0.0.2:8001' }),
    ],
    links: [makeLink()],
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

function makeHost(overrides: Partial<HostDetailed> = {}): HostDetailed {
  return {
    id: 'agent-1',
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

function makeNetworkConfig(overrides: Partial<AgentNetworkConfig> = {}): AgentNetworkConfig {
  return {
    id: 'nc-1',
    host_id: 'agent-1',
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
    host_id: 'agent-1',
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

const defaultProps = () => ({
  mesh: makeMesh(),
  hosts: [
    makeHost({ id: 'agent-1', name: 'Agent-01' }),
    makeHost({ id: 'agent-2', name: 'Agent-02', address: '10.0.0.2:8001' }),
  ],
  networkConfigs: [] as AgentNetworkConfig[],
  managedInterfaces: [] as ManagedInterface[],
  testingAll: false,
  testingLink: null as string | null,
  onTestAllLinks: vi.fn(),
  onTestLink: vi.fn(),
});

// ── Tests ──

describe('AgentMeshSection', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // ── Basic Rendering ──

  describe('Basic rendering', () => {
    it('renders the Agent Mesh heading', () => {
      render(<AgentMeshSection {...defaultProps()} />);
      expect(screen.getByText('Agent Mesh')).toBeInTheDocument();
    });

    it('renders online and offline agent counts', () => {
      render(<AgentMeshSection {...defaultProps()} />);
      expect(screen.getByText('2 Online')).toBeInTheDocument();
      expect(screen.getByText('0 Offline')).toBeInTheDocument();
    });

    it('renders agent cards for each agent', () => {
      render(<AgentMeshSection {...defaultProps()} />);
      // Agent names appear in both the agent card grid and the link table
      expect(screen.getAllByText('Agent-01').length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText('Agent-02').length).toBeGreaterThanOrEqual(1);
    });

    it('renders management address on agent cards', () => {
      render(<AgentMeshSection {...defaultProps()} />);
      expect(screen.getByText('10.0.0.1:8001')).toBeInTheDocument();
      expect(screen.getByText('10.0.0.2:8001')).toBeInTheDocument();
    });
  });

  // ── Empty States ──

  describe('Empty states', () => {
    it('shows no agents message when agents list is empty', () => {
      const props = defaultProps();
      props.mesh = makeMesh({ agents: [], links: [] });
      render(<AgentMeshSection {...props} />);
      expect(screen.getByText('No agents registered')).toBeInTheDocument();
    });

    it('shows single agent message when only one agent exists', () => {
      const props = defaultProps();
      props.mesh = makeMesh({
        agents: [makeAgent()],
        links: [],
      });
      render(<AgentMeshSection {...props} />);
      expect(screen.getByText(/Only one agent registered/)).toBeInTheDocument();
    });

    it('shows no links message when agents exist but no links', () => {
      const props = defaultProps();
      props.mesh = makeMesh({ links: [] });
      render(<AgentMeshSection {...props} />);
      expect(screen.getByText(/No links to display/)).toBeInTheDocument();
    });
  });

  // ── Online/Offline Counts ──

  describe('Agent status counts', () => {
    it('shows correct counts with mixed online/offline agents', () => {
      const props = defaultProps();
      props.mesh = makeMesh({
        agents: [
          makeAgent({ id: 'a1', status: 'online' }),
          makeAgent({ id: 'a2', status: 'offline' }),
          makeAgent({ id: 'a3', status: 'online' }),
        ],
      });
      render(<AgentMeshSection {...props} />);
      expect(screen.getByText('2 Online')).toBeInTheDocument();
      expect(screen.getByText('1 Offline')).toBeInTheDocument();
    });

    it('shows all offline when no agents are online', () => {
      const props = defaultProps();
      props.mesh = makeMesh({
        agents: [
          makeAgent({ id: 'a1', status: 'offline' }),
          makeAgent({ id: 'a2', status: 'offline' }),
        ],
      });
      render(<AgentMeshSection {...props} />);
      expect(screen.getByText('0 Online')).toBeInTheDocument();
      expect(screen.getByText('2 Offline')).toBeInTheDocument();
    });
  });

  // ── Test All Links Button ──

  describe('Test All Links button', () => {
    it('renders Test All button when 2+ agents are online', () => {
      render(<AgentMeshSection {...defaultProps()} />);
      expect(screen.getByText('Test All')).toBeInTheDocument();
    });

    it('calls onTestAllLinks when Test All is clicked', async () => {
      const user = userEvent.setup();
      const props = defaultProps();
      render(<AgentMeshSection {...props} />);
      await user.click(screen.getByText('Test All').closest('button')!);
      expect(props.onTestAllLinks).toHaveBeenCalledTimes(1);
    });

    it('disables Test All button when fewer than 2 agents are online', () => {
      const props = defaultProps();
      props.mesh = makeMesh({
        agents: [
          makeAgent({ id: 'a1', status: 'online' }),
          makeAgent({ id: 'a2', status: 'offline' }),
        ],
      });
      render(<AgentMeshSection {...props} />);
      const btn = screen.getByText('Test All').closest('button')!;
      expect(btn).toBeDisabled();
    });

    it('disables Test All button when testingAll is true', () => {
      const props = defaultProps();
      props.testingAll = true;
      render(<AgentMeshSection {...props} />);
      const btn = screen.getByText('Testing...').closest('button')!;
      expect(btn).toBeDisabled();
    });

    it('shows "Testing..." text when testingAll is true', () => {
      const props = defaultProps();
      props.testingAll = true;
      render(<AgentMeshSection {...props} />);
      expect(screen.getByText('Testing...')).toBeInTheDocument();
    });
  });

  // ── Link Details Table ──

  describe('Link details table', () => {
    it('renders link table headers', () => {
      render(<AgentMeshSection {...defaultProps()} />);
      expect(screen.getByText('Link Details')).toBeInTheDocument();
      expect(screen.getByText('Path')).toBeInTheDocument();
      expect(screen.getByText('Type')).toBeInTheDocument();
      expect(screen.getByText('MTU')).toBeInTheDocument();
      expect(screen.getByText('Latency')).toBeInTheDocument();
      expect(screen.getByText('Status')).toBeInTheDocument();
      expect(screen.getByText('Last Test')).toBeInTheDocument();
      expect(screen.getByText('Action')).toBeInTheDocument();
    });

    it('renders link source and target names in table rows', () => {
      render(<AgentMeshSection {...defaultProps()} />);
      // Agent names appear in both agent cards and link rows
      const agent01Els = screen.getAllByText('Agent-01');
      const agent02Els = screen.getAllByText('Agent-02');
      // At least 2 occurrences: one in agent card, one in link table
      expect(agent01Els.length).toBeGreaterThanOrEqual(2);
      expect(agent02Els.length).toBeGreaterThanOrEqual(2);
    });

    it('renders configured MTU for link', () => {
      render(<AgentMeshSection {...defaultProps()} />);
      expect(screen.getByText('/ 1450')).toBeInTheDocument();
    });

    it('renders tested MTU when available', () => {
      const props = defaultProps();
      props.mesh = makeMesh({
        links: [makeLink({ tested_mtu: 1450, configured_mtu: 1450 })],
      });
      render(<AgentMeshSection {...props} />);
      expect(screen.getByText('1450')).toBeInTheDocument();
    });

    it('renders dash when tested MTU is null', () => {
      const props = defaultProps();
      props.mesh = makeMesh({
        links: [makeLink({ tested_mtu: null })],
      });
      render(<AgentMeshSection {...props} />);
      // Dash for null tested_mtu
      const dashElements = screen.getAllByText('-');
      expect(dashElements.length).toBeGreaterThan(0);
    });

    it('renders latency when available', () => {
      const props = defaultProps();
      props.mesh = makeMesh({
        links: [makeLink({ latency_ms: 1.25 })],
      });
      render(<AgentMeshSection {...props} />);
      expect(screen.getByText('1.25 ms')).toBeInTheDocument();
    });

    it('renders dash when latency is null', () => {
      const props = defaultProps();
      props.mesh = makeMesh({
        links: [makeLink({ latency_ms: null })],
      });
      render(<AgentMeshSection {...props} />);
      // Multiple dashes for null values
      const dashes = screen.getAllByText('-');
      expect(dashes.length).toBeGreaterThanOrEqual(1);
    });

    it('renders link test status badge', () => {
      const props = defaultProps();
      props.mesh = makeMesh({
        links: [makeLink({ test_status: 'success' })],
      });
      render(<AgentMeshSection {...props} />);
      expect(screen.getByText('success')).toBeInTheDocument();
    });

    it('renders error icon when test_error is present', () => {
      const props = defaultProps();
      props.mesh = makeMesh({
        links: [makeLink({ test_error: 'Connection timeout' })],
      });
      const { container } = render(<AgentMeshSection {...props} />);
      const errorIcon = container.querySelector('i.fa-circle-exclamation');
      expect(errorIcon).toBeInTheDocument();
    });

    it('renders Management badge for management test path', () => {
      render(<AgentMeshSection {...defaultProps()} />);
      expect(screen.getByText('Management')).toBeInTheDocument();
    });

    it('renders Transport badge for data_plane test path', () => {
      const props = defaultProps();
      props.mesh = makeMesh({
        links: [makeLink({ test_path: 'data_plane' })],
      });
      render(<AgentMeshSection {...props} />);
      expect(screen.getByText('Transport')).toBeInTheDocument();
    });
  });

  // ── Per-Link Test Button ──

  describe('Per-link test button', () => {
    it('calls onTestLink with correct args when link test button is clicked', async () => {
      const user = userEvent.setup();
      const props = defaultProps();
      render(<AgentMeshSection {...props} />);
      // Find the play button (test button) in the link row
      const { container } = render(<AgentMeshSection {...props} />);
      const playButtons = container.querySelectorAll('i.fa-play');
      if (playButtons.length > 0) {
        await user.click(playButtons[0].closest('button')!);
        expect(props.onTestLink).toHaveBeenCalledWith('agent-1', 'agent-2', 'management');
      }
    });

    it('disables per-link test button when testingAll is true', () => {
      const props = defaultProps();
      props.testingAll = true;
      const { container } = render(<AgentMeshSection {...props} />);
      const actionButtons = container.querySelectorAll('td:last-child button');
      actionButtons.forEach(btn => {
        expect(btn).toBeDisabled();
      });
    });

    it('disables specific link test button when that link is testing', () => {
      const props = defaultProps();
      props.testingLink = 'agent-1-agent-2-management';
      const { container } = render(<AgentMeshSection {...props} />);
      const actionButtons = container.querySelectorAll('td:last-child button');
      if (actionButtons.length > 0) {
        expect(actionButtons[0]).toBeDisabled();
      }
    });

    it('shows spinner icon when link is being tested', () => {
      const props = defaultProps();
      props.testingLink = 'agent-1-agent-2-management';
      const { container } = render(<AgentMeshSection {...props} />);
      const spinnerIcon = container.querySelector('i.fa-spinner');
      expect(spinnerIcon).toBeInTheDocument();
    });
  });

  // ── Transport Display ──

  describe('Transport display on agent cards', () => {
    it('shows data plane address when host has one', () => {
      const props = defaultProps();
      props.hosts = [
        makeHost({ id: 'agent-1', data_plane_address: '192.168.1.1' }),
        makeHost({ id: 'agent-2' }),
      ];
      render(<AgentMeshSection {...props} />);
      expect(screen.getByText('192.168.1.1')).toBeInTheDocument();
    });

    it('shows VLAN badge when transport mode is subinterface', () => {
      const props = defaultProps();
      props.networkConfigs = [
        makeNetworkConfig({
          host_id: 'agent-1',
          transport_mode: 'subinterface',
          data_plane_interface: 'ens192.100',
        }),
      ];
      render(<AgentMeshSection {...props} />);
      expect(screen.getByText('VLAN')).toBeInTheDocument();
    });

    it('shows NIC badge when transport mode is dedicated', () => {
      const props = defaultProps();
      props.networkConfigs = [
        makeNetworkConfig({
          host_id: 'agent-1',
          transport_mode: 'dedicated',
          data_plane_interface: 'ens224',
        }),
      ];
      render(<AgentMeshSection {...props} />);
      expect(screen.getByText('NIC')).toBeInTheDocument();
    });

    it('shows transport interface name from managed interfaces', () => {
      const props = defaultProps();
      props.networkConfigs = [
        makeNetworkConfig({
          host_id: 'agent-1',
          transport_mode: 'subinterface',
        }),
      ];
      props.managedInterfaces = [
        makeManagedInterface({ host_id: 'agent-1', name: 'ens192.100' }),
      ];
      render(<AgentMeshSection {...props} />);
      expect(screen.getByText('ens192.100')).toBeInTheDocument();
    });

    it('shows MTU current/desired on transport agent cards', () => {
      const props = defaultProps();
      props.networkConfigs = [
        makeNetworkConfig({
          host_id: 'agent-1',
          transport_mode: 'dedicated',
          current_mtu: 9000,
          desired_mtu: 9000,
        }),
      ];
      render(<AgentMeshSection {...props} />);
      expect(screen.getByText('9000/9000')).toBeInTheDocument();
    });
  });

  // ── Transport Readiness Summary ──

  describe('Transport readiness summary', () => {
    it('shows transport readiness banner when transport agents exist', () => {
      const props = defaultProps();
      props.networkConfigs = [
        makeNetworkConfig({ host_id: 'agent-1', transport_mode: 'dedicated' }),
      ];
      render(<AgentMeshSection {...props} />);
      expect(screen.getByText(/agents have data plane transport/)).toBeInTheDocument();
    });

    it('shows correct transport agent count', () => {
      const props = defaultProps();
      props.networkConfigs = [
        makeNetworkConfig({ host_id: 'agent-1', transport_mode: 'dedicated' }),
        makeNetworkConfig({ host_id: 'agent-2', transport_mode: 'subinterface' }),
      ];
      render(<AgentMeshSection {...props} />);
      expect(screen.getByText(/2\/2/)).toBeInTheDocument();
    });

    it('does not show transport readiness banner when no transport agents', () => {
      const props = defaultProps();
      props.networkConfigs = [
        makeNetworkConfig({ host_id: 'agent-1', transport_mode: 'management' }),
      ];
      render(<AgentMeshSection {...props} />);
      expect(screen.queryByText(/agents have data plane transport/)).not.toBeInTheDocument();
    });

    it('shows data plane link verification count', () => {
      const props = defaultProps();
      props.networkConfigs = [
        makeNetworkConfig({ host_id: 'agent-1', transport_mode: 'dedicated' }),
      ];
      props.mesh = makeMesh({
        links: [
          makeLink({ test_path: 'data_plane', test_status: 'success' }),
          makeLink({ id: 'link-2', test_path: 'data_plane', test_status: 'failed' }),
        ],
      });
      render(<AgentMeshSection {...props} />);
      expect(screen.getByText(/data plane links verified/)).toBeInTheDocument();
    });
  });
});
