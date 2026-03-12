import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';
import type { AgentGraphNode, NodeWithState, HostStats } from './types';
import type { LinkStateData } from '../../hooks/useLabStateWS';
import {
  type Position,
  WORLD_W,
  WORLD_H,
  SAT_RADIUS,
  MAX_VISIBLE_SATELLITES,
  MIN_ZOOM,
  MAX_ZOOM,
  lightenColor,
  computeInitialPositions,
  computeSatellitePositions,
  computeFitView,
} from './agentGraphLayout';

// Mock child components
vi.mock('./AgentNode', () => ({
  default: ({ agent, onSelect, overflowCount }: any) => (
    <g data-testid={`agent-node-${agent.agentId}`} onClick={() => onSelect(false)}>
      <text>{agent.agentName}</text>
      {overflowCount > 0 && <text data-testid={`overflow-${agent.agentId}`}>+{overflowCount}</text>}
    </g>
  ),
}));

vi.mock('./GraphLink', () => ({
  default: ({ linkState, onSelect, isSelected }: any) => (
    <g
      data-testid={`graph-link-${linkState.link_name}`}
      onClick={() => onSelect(linkState.link_name)}
      data-selected={isSelected}
    />
  ),
}));

import AgentGraph from './AgentGraph';

// ─── Factories ─────────────────────────────────────────────────────

function makeHostStats(overrides: Partial<HostStats> = {}): HostStats {
  return { nodeCount: 0, runningCount: 0, linkCount: 0, vlanTags: new Set(), ...overrides };
}

function makeNodeWithState(name: string, overrides: Partial<NodeWithState> = {}): NodeWithState {
  return {
    node: { id: name, name, x: 0, y: 0, nodeType: 'device' as const, type: 'router' as any, model: 'ceos', version: '1', container_name: name },
    state: null,
    containerName: name,
    ...overrides,
  };
}

function makeAgent(overrides: Partial<AgentGraphNode> = {}): AgentGraphNode {
  return { agentId: 'a1', agentName: 'Agent-1', color: '#22c55e', nodes: [], localLinks: [], stats: makeHostStats(), ...overrides };
}

function makeLinkState(overrides: Partial<LinkStateData> = {}): LinkStateData {
  return { link_name: 'R1:eth1-R2:eth1', desired_state: 'up', actual_state: 'up', source_node: 'R1', target_node: 'R2', ...overrides };
}

function defaultProps() {
  return {
    agentNodes: [] as AgentGraphNode[],
    crossHostLinks: [] as LinkStateData[],
    crossHostNodeNames: new Set<string>(),
    selectedIds: new Set<string>(),
    onSelectAgent: vi.fn(),
    onDeselectAll: vi.fn(),
    selectedLinkName: null as string | null,
    onSelectLink: vi.fn(),
    vendorLookup: new Map<string, string>(),
    deviceModels: [],
  };
}

// ─── Pure Function Tests ───────────────────────────────────────────

describe('lightenColor', () => {
  it('returns white when amount is 1', () => {
    expect(lightenColor('#000000', 1)).toBe('#ffffff');
  });

  it('returns the same color when amount is 0', () => {
    expect(lightenColor('#ff0000', 0)).toBe('#ff0000');
  });

  it('lightens red by 50%', () => {
    expect(lightenColor('#ff0000', 0.5)).toBe('#ff8080');
  });

  it('handles mid-grey', () => {
    expect(lightenColor('#808080', 0.5)).toBe('#c0c0c0');
  });
});

describe('computeInitialPositions', () => {
  it('returns empty map for zero agents', () => {
    expect(computeInitialPositions([]).size).toBe(0);
  });

  it('places single agent at center', () => {
    const positions = computeInitialPositions([makeAgent({ agentId: 'a1' })]);
    const pos = positions.get('a1')!;
    expect(pos.x).toBe(WORLD_W / 2);
    expect(pos.y).toBe(WORLD_H / 2);
  });

  it('places two agents horizontally spaced', () => {
    const agents = [makeAgent({ agentId: 'a1' }), makeAgent({ agentId: 'a2' })];
    const positions = computeInitialPositions(agents);
    expect(positions.get('a1')!.x).toBe(WORLD_W / 2 - 150);
    expect(positions.get('a2')!.x).toBe(WORLD_W / 2 + 150);
    expect(positions.get('a1')!.y).toBe(positions.get('a2')!.y);
  });

  it('places three agents in circular layout', () => {
    const agents = Array.from({ length: 3 }, (_, i) => makeAgent({ agentId: `a${i}` }));
    const positions = computeInitialPositions(agents);
    expect(positions.size).toBe(3);
    const p0 = positions.get('a0')!;
    expect(p0.x).toBeCloseTo(WORLD_W / 2, 0);
    expect(p0.y).toBeLessThan(WORLD_H / 2);
  });

  it('distributes many agents at equal radius', () => {
    const agents = Array.from({ length: 6 }, (_, i) => makeAgent({ agentId: `a${i}` }));
    const positions = computeInitialPositions(agents);
    const cx = WORLD_W / 2;
    const cy = WORLD_H / 2;
    const radii = new Set<number>();
    positions.forEach(({ x, y }) => radii.add(Math.round(Math.sqrt((x - cx) ** 2 + (y - cy) ** 2))));
    expect(radii.size).toBe(1);
  });
});

describe('computeSatellitePositions', () => {
  const center: Position = { x: 400, y: 300 };

  it('returns empty map for no nodes', () => {
    expect(computeSatellitePositions(center, [], new Set()).size).toBe(0);
  });

  it('places single node directly above agent center', () => {
    const positions = computeSatellitePositions(center, [makeNodeWithState('R1')], new Set());
    const pos = positions.get('sat:R1')!;
    expect(pos.x).toBeCloseTo(center.x, 0);
    expect(pos.y).toBeLessThan(center.y);
  });

  it('caps visible satellites at MAX_VISIBLE_SATELLITES', () => {
    const nodes = Array.from({ length: 12 }, (_, i) => makeNodeWithState(`R${i}`));
    const positions = computeSatellitePositions(center, nodes, new Set());
    expect(positions.size).toBe(MAX_VISIBLE_SATELLITES);
  });

  it('shows all cross-host nodes even beyond cap', () => {
    const nodes = Array.from({ length: 12 }, (_, i) => makeNodeWithState(`R${i}`));
    const crossHostNames = new Set(nodes.slice(0, 10).map(n => n.containerName));
    const positions = computeSatellitePositions(center, nodes, crossHostNames);
    expect(positions.size).toBe(10);
  });

  it('positions are all at SAT_RADIUS distance', () => {
    const nodes = Array.from({ length: 5 }, (_, i) => makeNodeWithState(`R${i}`));
    const positions = computeSatellitePositions(center, nodes, new Set());
    positions.forEach((pos) => {
      const dist = Math.sqrt((pos.x - center.x) ** 2 + (pos.y - center.y) ** 2);
      expect(dist).toBeCloseTo(SAT_RADIUS, 0);
    });
  });
});

describe('computeFitView', () => {
  it('returns default for empty positions', () => {
    const result = computeFitView(new Map(), { width: 800, height: 600 });
    expect(result.zoom).toBe(1);
    expect(result.pan).toEqual({ x: 0, y: 0 });
  });

  it('clamps zoom to MIN_ZOOM for very spread positions', () => {
    const positions = new Map([['a', { x: -10000, y: -10000 }], ['b', { x: 10000, y: 10000 }]]);
    expect(computeFitView(positions, { width: 200, height: 200 }).zoom).toBe(MIN_ZOOM);
  });

  it('clamps zoom to MAX_ZOOM for tight cluster', () => {
    const positions = new Map([['a', { x: 400, y: 300 }], ['b', { x: 401, y: 301 }]]);
    expect(computeFitView(positions, { width: 8000, height: 6000 }).zoom).toBe(MAX_ZOOM);
  });

  it('centers pan on content centroid', () => {
    const positions = new Map([['a', { x: 100, y: 100 }], ['b', { x: 200, y: 200 }]]);
    const result = computeFitView(positions, { width: 800, height: 600 });
    expect(result.pan.x).toBe(WORLD_W / 2 - 150);
    expect(result.pan.y).toBe(WORLD_H / 2 - 150);
  });
});

// ─── Component Rendering Tests ─────────────────────────────────────

describe('AgentGraph component', () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it('renders svg element with empty data', () => {
    const { container } = render(<AgentGraph {...defaultProps()} />);
    expect(container.querySelector('svg')).toBeInTheDocument();
  });

  it('renders agent nodes for each agent', () => {
    const props = defaultProps();
    props.agentNodes = [makeAgent({ agentId: 'a1', agentName: 'Agent-1' }), makeAgent({ agentId: 'a2', agentName: 'Agent-2' })];
    render(<AgentGraph {...props} />);
    expect(screen.getByTestId('agent-node-a1')).toBeInTheDocument();
    expect(screen.getByTestId('agent-node-a2')).toBeInTheDocument();
  });

  it('calls onSelectAgent on agent click', () => {
    const props = defaultProps();
    props.agentNodes = [makeAgent({ agentId: 'a1' })];
    render(<AgentGraph {...props} />);
    fireEvent.click(screen.getByTestId('agent-node-a1'));
    expect(props.onSelectAgent).toHaveBeenCalledWith('a1', false);
  });

  it('renders cross-host GraphLink elements', () => {
    const props = defaultProps();
    props.agentNodes = [
      makeAgent({ agentId: 'a1', nodes: [makeNodeWithState('R1')] }),
      makeAgent({ agentId: 'a2', nodes: [makeNodeWithState('R2')] }),
    ];
    props.crossHostLinks = [makeLinkState({ is_cross_host: true, source_host_id: 'a1', target_host_id: 'a2' })];
    props.crossHostNodeNames = new Set(['R1', 'R2']);
    render(<AgentGraph {...props} />);
    expect(screen.getByTestId('graph-link-R1:eth1-R2:eth1')).toBeInTheDocument();
  });

  it('shows hint overlay initially', () => {
    render(<AgentGraph {...defaultProps()} />);
    expect(screen.getByText(/Click agent to inspect/)).toBeInTheDocument();
  });

  it('renders zoom controls', () => {
    render(<AgentGraph {...defaultProps()} />);
    expect(screen.getByTitle('Zoom in')).toBeInTheDocument();
    expect(screen.getByTitle('Zoom out')).toBeInTheDocument();
    expect(screen.getByTitle('Center view')).toBeInTheDocument();
    expect(screen.getByTitle('Fit to screen')).toBeInTheDocument();
  });

  it('shows 100% zoom initially', () => {
    render(<AgentGraph {...defaultProps()} />);
    expect(screen.getByText('100%')).toBeInTheDocument();
  });
});
