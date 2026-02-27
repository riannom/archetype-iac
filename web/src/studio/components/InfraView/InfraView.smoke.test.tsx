import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import React from 'react';
import type { AgentGraphNode, CrossHostBundle, NodeWithState, HostStats } from './types';
import type { LinkStateData } from '../../hooks/useLabStateWS';
import AgentDetailView from './AgentDetailView';
import TunnelDetailView from './TunnelDetailView';
import NotificationsPanel from './NotificationsPanel';

// Mock the api module so NotificationsPanel doesn't make real fetch calls
vi.mock('../../../api', () => ({
  getLabInfraNotifications: vi.fn(),
}));
import { getLabInfraNotifications } from '../../../api';

// --- Factory helpers ---

function makeHostStats(overrides: Partial<HostStats> = {}): HostStats {
  return {
    nodeCount: 0,
    runningCount: 0,
    linkCount: 0,
    vlanTags: new Set(),
    ...overrides,
  };
}

function makeNodeWithState(overrides: Partial<NodeWithState> & { name?: string } = {}): NodeWithState {
  const name = overrides.name || 'node1';
  return {
    node: {
      id: name,
      name,
      x: 0,
      y: 0,
      nodeType: 'device' as const,
      type: 'router' as any,
      model: 'ceos',
      version: '1',
      container_name: name,
    },
    state: null,
    containerName: name,
    ...overrides,
  };
}

function makeAgentGraphNode(overrides: Partial<AgentGraphNode> = {}): AgentGraphNode {
  return {
    agentId: 'agent-1',
    agentName: 'Agent 1',
    color: '#22c55e',
    nodes: [],
    localLinks: [],
    stats: makeHostStats(),
    ...overrides,
  };
}

function makeLinkState(overrides: Partial<LinkStateData> = {}): LinkStateData {
  return {
    link_name: 'R1:eth1-R2:eth1',
    desired_state: 'up',
    actual_state: 'up',
    source_node: 'R1',
    target_node: 'R2',
    ...overrides,
  };
}

// --- AgentDetailView ---

describe('AgentDetailView', () => {
  it('renders Nodes and Local Links headers', () => {
    const agent = makeAgentGraphNode();
    render(<AgentDetailView agent={agent} />);

    expect(screen.getByText('Nodes')).toBeInTheDocument();
    expect(screen.getByText('Local Links')).toBeInTheDocument();
  });

  it('shows empty state for no nodes', () => {
    const agent = makeAgentGraphNode({ nodes: [] });
    render(<AgentDetailView agent={agent} />);

    expect(screen.getByText('No nodes deployed')).toBeInTheDocument();
  });

  it('shows empty state for no local links', () => {
    const agent = makeAgentGraphNode({ localLinks: [] });
    render(<AgentDetailView agent={agent} />);

    expect(screen.getByText('No local links')).toBeInTheDocument();
  });

  it('renders node names and states', () => {
    const nws = makeNodeWithState({
      name: 'R1',
      state: {
        id: 'ns1',
        lab_id: 'lab1',
        node_id: 'R1',
        node_name: 'R1',
        desired_state: 'running',
        actual_state: 'running',
      },
    });
    const agent = makeAgentGraphNode({
      nodes: [nws],
      stats: makeHostStats({ nodeCount: 1, runningCount: 1 }),
    });

    render(<AgentDetailView agent={agent} />);

    expect(screen.getByText('R1')).toBeInTheDocument();
    expect(screen.getByText('running')).toBeInTheDocument();
  });

  it('renders VLAN badges for local links with tags', () => {
    const link = makeLinkState({
      link_name: 'R1:eth1-R2:eth1',
      source_vlan_tag: 100,
      target_vlan_tag: 200,
    });
    const agent = makeAgentGraphNode({
      localLinks: [link],
      stats: makeHostStats({ linkCount: 1 }),
    });

    render(<AgentDetailView agent={agent} />);

    expect(screen.getByText('v100')).toBeInTheDocument();
    expect(screen.getByText('v200')).toBeInTheDocument();
  });
});

// --- TunnelDetailView ---

describe('TunnelDetailView', () => {
  it('shows empty state when no tunnels exist', () => {
    render(
      <TunnelDetailView
        selectedAgentNodes={[makeAgentGraphNode()]}
        relevantBundles={[]}
      />
    );

    expect(
      screen.getByText('No cross-host tunnels between selected agents')
    ).toBeInTheDocument();
  });

  it('renders table headers', () => {
    const link = makeLinkState({
      is_cross_host: true,
      source_host_id: 'a1',
      target_host_id: 'a2',
      vni: 5001,
    });
    const bundle: CrossHostBundle = {
      agentA: 'a1',
      agentB: 'a2',
      links: [link],
      hasError: false,
      allUp: true,
    };

    render(
      <TunnelDetailView
        selectedAgentNodes={[
          makeAgentGraphNode({ agentId: 'a1', agentName: 'Agent A' }),
          makeAgentGraphNode({ agentId: 'a2', agentName: 'Agent B' }),
        ]}
        relevantBundles={[bundle]}
      />
    );

    expect(screen.getByText('Source')).toBeInTheDocument();
    expect(screen.getByText('Src VLAN')).toBeInTheDocument();
    expect(screen.getByText('State')).toBeInTheDocument();
    expect(screen.getByText('VNI')).toBeInTheDocument();
    expect(screen.getByText('Tgt VLAN')).toBeInTheDocument();
    expect(screen.getByText('Target')).toBeInTheDocument();
    expect(screen.getByText('Error')).toBeInTheDocument();
  });

  it('renders link names split into source and target parts', () => {
    const link = makeLinkState({
      link_name: 'R1:eth1-R2:eth1',
      is_cross_host: true,
      vni: 5001,
    });
    const bundle: CrossHostBundle = {
      agentA: 'a1',
      agentB: 'a2',
      links: [link],
      hasError: false,
      allUp: true,
    };

    render(
      <TunnelDetailView
        selectedAgentNodes={[makeAgentGraphNode({ agentId: 'a1' }), makeAgentGraphNode({ agentId: 'a2' })]}
        relevantBundles={[bundle]}
      />
    );

    expect(screen.getByText('R1:eth1')).toBeInTheDocument();
    expect(screen.getByText('R2:eth1')).toBeInTheDocument();
  });

  it('renders VNI badge', () => {
    const link = makeLinkState({ vni: 7777, is_cross_host: true });
    const bundle: CrossHostBundle = {
      agentA: 'a1',
      agentB: 'a2',
      links: [link],
      hasError: false,
      allUp: true,
    };

    render(
      <TunnelDetailView
        selectedAgentNodes={[makeAgentGraphNode({ agentId: 'a1' }), makeAgentGraphNode({ agentId: 'a2' })]}
        relevantBundles={[bundle]}
      />
    );

    expect(screen.getByText('7777')).toBeInTheDocument();
  });

  it('renders error_message when present', () => {
    const link = makeLinkState({
      actual_state: 'error',
      error_message: 'VXLAN tunnel timeout',
      is_cross_host: true,
    });
    const bundle: CrossHostBundle = {
      agentA: 'a1',
      agentB: 'a2',
      links: [link],
      hasError: true,
      allUp: false,
    };

    render(
      <TunnelDetailView
        selectedAgentNodes={[makeAgentGraphNode({ agentId: 'a1' }), makeAgentGraphNode({ agentId: 'a2' })]}
        relevantBundles={[bundle]}
      />
    );

    expect(screen.getByText('VXLAN tunnel timeout')).toBeInTheDocument();
  });
});

// --- NotificationsPanel ---

describe('NotificationsPanel', () => {
  const mockedGetNotifications = vi.mocked(getLabInfraNotifications);

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows loading state initially', () => {
    // Return a promise that never resolves
    mockedGetNotifications.mockReturnValue(new Promise(() => {}));

    render(<NotificationsPanel labId="lab-1" refreshKey={0} />);

    expect(screen.getByText('Loading notifications...')).toBeInTheDocument();
  });

  it('shows empty state when no notifications', async () => {
    mockedGetNotifications.mockResolvedValue({ notifications: [] });

    render(<NotificationsPanel labId="lab-1" refreshKey={0} />);

    expect(
      await screen.findByText('No infrastructure issues detected')
    ).toBeInTheDocument();
  });

  it('renders notification title and category', async () => {
    mockedGetNotifications.mockResolvedValue({
      notifications: [
        {
          id: 'n1',
          severity: 'error',
          category: 'tunnel_failed',
          title: 'VXLAN tunnel creation failed',
          detail: 'Timeout connecting to remote agent',
          entity_type: null,
          entity_name: null,
          timestamp: null,
        },
      ],
    });

    render(<NotificationsPanel labId="lab-1" refreshKey={0} />);

    expect(
      await screen.findByText('VXLAN tunnel creation failed')
    ).toBeInTheDocument();
    expect(screen.getByText('Tunnel Failed')).toBeInTheDocument();
  });
});
