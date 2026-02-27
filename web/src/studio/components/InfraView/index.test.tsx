import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import React from 'react';
import type { LinkStateData } from '../../hooks/useLabStateWS';
import type { DeviceModel } from '../../types';
import { DeviceType } from '../../types';

// Mock API
vi.mock('../../../api', () => ({
  getLabInterfaceMappings: vi.fn().mockResolvedValue({ mappings: [], total: 0 }),
  getLabInfraNotifications: vi.fn().mockResolvedValue({ notifications: [] }),
}));

// Mock child components
vi.mock('./InfraHeader', () => ({
  default: (props: any) => (
    <div data-testid="infra-header">
      <span data-testid="total-nodes">{props.totalNodes}</span>
      <span data-testid="total-running">{props.totalRunning}</span>
    </div>
  ),
}));

vi.mock('./AgentGraph', () => ({
  default: (props: any) => (
    <div data-testid="agent-graph">
      {props.agentNodes.map((a: any) => (
        <button
          key={a.agentId}
          data-testid={`select-agent-${a.agentId}`}
          onClick={() => props.onSelectAgent(a.agentId, false)}
        >
          {a.agentName}
        </button>
      ))}
      {props.crossHostLinks.map((l: any) => (
        <button
          key={l.link_name}
          data-testid={`select-link-${l.link_name}`}
          onClick={() => props.onSelectLink(l.link_name)}
        >
          {l.link_name}
        </button>
      ))}
    </div>
  ),
}));

vi.mock('./DetailPanel', () => ({
  default: (props: any) => (
    <div data-testid="detail-panel">
      <span data-testid="selected-count">{props.selectedIds.size}</span>
      <button data-testid="close-detail" onClick={props.onClose}>Close</button>
    </div>
  ),
}));

vi.mock('./VniLinkDetailPanel', () => ({
  default: (props: any) => (
    <div data-testid="vni-link-detail">
      <span>{props.linkState.link_name}</span>
      <button data-testid="close-vni-detail" onClick={props.onClose}>Close</button>
    </div>
  ),
}));

vi.mock('./LinkTable', () => ({
  default: (props: any) => (
    <div data-testid="link-table">
      {props.hostGroups.map((g: any) => (
        <div key={g.hostId} data-testid={`host-group-${g.hostId}`}>{g.hostName}</div>
      ))}
    </div>
  ),
}));

vi.mock('./NotificationsPanel', () => ({
  default: (props: any) => (
    <div data-testid="notifications-panel">notifications</div>
  ),
}));

vi.mock('../../../utils/agentColors', () => ({
  getAgentColor: () => '#22c55e',
}));

import InfraView from './index';
import { getLabInterfaceMappings } from '../../../api';

// ─── Factories ─────────────────────────────────────────────────────

function makeNode(name: string, hostId?: string) {
  return {
    id: name,
    name,
    x: 0,
    y: 0,
    nodeType: 'device' as const,
    type: DeviceType.ROUTER,
    model: 'ceos',
    version: '1',
    container_name: name,
  };
}

function makeNodeState(name: string, hostId?: string, hostName?: string) {
  return {
    id: `ns-${name}`,
    lab_id: 'lab1',
    node_id: name,
    node_name: name,
    desired_state: 'running' as const,
    actual_state: 'running' as const,
    host_id: hostId,
    host_name: hostName,
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

function defaultProps() {
  return {
    labId: 'lab-1',
    nodes: [] as any[],
    nodeStates: {} as Record<string, any>,
    linkStates: new Map<string, LinkStateData>(),
    agents: [] as { id: string; name: string }[],
    deviceModels: [] as DeviceModel[],
  };
}

// ─── Tests ─────────────────────────────────────────────────────────

describe('InfraView', () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it('renders child components', () => {
    render(<InfraView {...defaultProps()} />);
    expect(screen.getByTestId('infra-header')).toBeInTheDocument();
    expect(screen.getByTestId('agent-graph')).toBeInTheDocument();
  });

  it('calls getLabInterfaceMappings on mount', async () => {
    render(<InfraView {...defaultProps()} />);
    await waitFor(() => {
      expect(getLabInterfaceMappings).toHaveBeenCalledWith('lab-1');
    });
  });

  it('groups nodes by host_id into host groups', () => {
    const props = defaultProps();
    props.nodes = [makeNode('R1'), makeNode('R2'), makeNode('R3')];
    props.nodeStates = {
      R1: makeNodeState('R1', 'agent-1', 'Agent 1'),
      R2: makeNodeState('R2', 'agent-1', 'Agent 1'),
      R3: makeNodeState('R3', 'agent-2', 'Agent 2'),
    };
    props.agents = [
      { id: 'agent-1', name: 'Agent 1' },
      { id: 'agent-2', name: 'Agent 2' },
    ];

    render(<InfraView {...props} />);

    expect(screen.getByTestId('host-group-agent-1')).toHaveTextContent('Agent 1');
    expect(screen.getByTestId('host-group-agent-2')).toHaveTextContent('Agent 2');
  });

  it('puts nodes with no host_id into unassigned group', () => {
    const props = defaultProps();
    props.nodes = [makeNode('R1')];
    props.nodeStates = {}; // no state => no host_id

    render(<InfraView {...props} />);
    expect(screen.getByTestId('host-group-__unassigned__')).toHaveTextContent('Unassigned');
  });

  it('extracts cross-host links', () => {
    const props = defaultProps();
    const crossLink = makeLinkState({
      link_name: 'R1:eth1-R2:eth1',
      is_cross_host: true,
      source_host_id: 'a1',
      target_host_id: 'a2',
    });
    props.linkStates = new Map([['R1:eth1-R2:eth1', crossLink]]);

    render(<InfraView {...props} />);
    // The cross-host link should be passed to AgentGraph and show a button
    expect(screen.getByTestId('select-link-R1:eth1-R2:eth1')).toBeInTheDocument();
  });

  it('shows detail panel after agent selection', async () => {
    const props = defaultProps();
    props.nodes = [makeNode('R1')];
    props.nodeStates = { R1: makeNodeState('R1', 'a1', 'Agent 1') };
    props.agents = [{ id: 'a1', name: 'Agent 1' }];

    render(<InfraView {...props} />);

    // Click to select agent
    fireEvent.click(screen.getByTestId('select-agent-a1'));

    await waitFor(() => {
      expect(screen.getByTestId('detail-panel')).toBeInTheDocument();
    });
  });

  it('shows VNI link detail panel when a link is selected', async () => {
    const props = defaultProps();
    const crossLink = makeLinkState({
      link_name: 'R1:eth1-R2:eth1',
      is_cross_host: true,
    });
    props.linkStates = new Map([['R1:eth1-R2:eth1', crossLink]]);

    render(<InfraView {...props} />);

    fireEvent.click(screen.getByTestId('select-link-R1:eth1-R2:eth1'));

    await waitFor(() => {
      expect(screen.getByTestId('vni-link-detail')).toBeInTheDocument();
    });
  });

  it('shows link table tab and notifications tab', () => {
    render(<InfraView {...defaultProps()} />);
    expect(screen.getByText('Links')).toBeInTheDocument();
    expect(screen.getByText('Notifications')).toBeInTheDocument();
  });

  it('switches to notifications tab', async () => {
    render(<InfraView {...defaultProps()} />);
    fireEvent.click(screen.getByText('Notifications'));
    await waitFor(() => {
      expect(screen.getByTestId('notifications-panel')).toBeInTheDocument();
    });
  });

  it('passes correct totalNodes and totalRunning to header', () => {
    const props = defaultProps();
    props.nodes = [makeNode('R1'), makeNode('R2')];
    props.nodeStates = {
      R1: makeNodeState('R1', 'a1', 'Agent 1'),
      R2: makeNodeState('R2', 'a1', 'Agent 1'),
    };
    props.agents = [{ id: 'a1', name: 'Agent 1' }];

    render(<InfraView {...props} />);
    expect(screen.getByTestId('total-nodes')).toHaveTextContent('2');
    expect(screen.getByTestId('total-running')).toHaveTextContent('2');
  });

  it('handles empty agents gracefully', () => {
    const props = defaultProps();
    props.nodes = [];
    props.agents = [];

    render(<InfraView {...props} />);
    expect(screen.getByTestId('infra-header')).toBeInTheDocument();
    expect(screen.getByTestId('total-nodes')).toHaveTextContent('0');
  });
});
