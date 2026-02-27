import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';
import type { AgentGraphNode, NodeWithState, HostStats } from './types';
import type { LinkStateData } from '../../hooks/useLabStateWS';

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

// ─── Pure functions replicated for unit tests (not exported from source) ───

function lightenColor(hex: string, amount: number): string {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  const lr = Math.min(255, Math.round(r + (255 - r) * amount));
  const lg = Math.min(255, Math.round(g + (255 - g) * amount));
  const lb = Math.min(255, Math.round(b + (255 - b) * amount));
  return `#${lr.toString(16).padStart(2, '0')}${lg.toString(16).padStart(2, '0')}${lb.toString(16).padStart(2, '0')}`;
}

const WORLD_W = 800;
const WORLD_H = 600;
const SAT_RADIUS = 55;
const MAX_VISIBLE_SATELLITES = 8;
const MIN_ZOOM = 0.3;
const MAX_ZOOM = 3;

interface Position { x: number; y: number }

function computeInitialPositions(agents: AgentGraphNode[]): Map<string, Position> {
  const positions = new Map<string, Position>();
  const cx = WORLD_W / 2;
  const cy = WORLD_H / 2;
  if (agents.length === 0) return positions;
  if (agents.length === 1) {
    positions.set(agents[0].agentId, { x: cx, y: cy });
    return positions;
  }
  if (agents.length === 2) {
    positions.set(agents[0].agentId, { x: cx - 150, y: cy });
    positions.set(agents[1].agentId, { x: cx + 150, y: cy });
    return positions;
  }
  const radius = Math.min(WORLD_W, WORLD_H) * 0.3;
  const startAngle = -Math.PI / 2;
  agents.forEach((agent, i) => {
    const angle = startAngle + (2 * Math.PI * i) / agents.length;
    positions.set(agent.agentId, {
      x: cx + radius * Math.cos(angle),
      y: cy + radius * Math.sin(angle),
    });
  });
  return positions;
}

function computeSatellitePositions(
  agentPos: Position,
  nodes: NodeWithState[],
  crossHostNames: Set<string>,
): Map<string, Position> {
  const positions = new Map<string, Position>();
  const sorted = [...nodes].sort((a, b) => {
    const aCross = crossHostNames.has(a.containerName) ? 0 : 1;
    const bCross = crossHostNames.has(b.containerName) ? 0 : 1;
    if (aCross !== bCross) return aCross - bCross;
    return a.node.name.localeCompare(b.node.name);
  });
  const crossHostCount = sorted.filter(n => crossHostNames.has(n.containerName)).length;
  const visibleCount = Math.max(crossHostCount, Math.min(sorted.length, MAX_VISIBLE_SATELLITES));
  const visible = sorted.slice(0, visibleCount);
  if (visible.length === 0) return positions;
  if (visible.length <= 4) {
    const startAngle = -Math.PI * 5 / 6;
    const endAngle = -Math.PI / 6;
    const step = visible.length === 1 ? 0 : (endAngle - startAngle) / (visible.length - 1);
    visible.forEach((nws, i) => {
      const angle = visible.length === 1 ? -Math.PI / 2 : startAngle + step * i;
      positions.set(`sat:${nws.containerName}`, {
        x: agentPos.x + SAT_RADIUS * Math.cos(angle),
        y: agentPos.y + SAT_RADIUS * Math.sin(angle),
      });
    });
  } else {
    const startAngle = -Math.PI / 2;
    visible.forEach((nws, i) => {
      const angle = startAngle + (2 * Math.PI * i) / visible.length;
      positions.set(`sat:${nws.containerName}`, {
        x: agentPos.x + SAT_RADIUS * Math.cos(angle),
        y: agentPos.y + SAT_RADIUS * Math.sin(angle),
      });
    });
  }
  return positions;
}

function computeFitView(
  positions: Map<string, Position>,
  dims: { width: number; height: number },
): { zoom: number; pan: Position } {
  if (positions.size === 0) return { zoom: 1, pan: { x: 0, y: 0 } };
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  positions.forEach(({ x, y }) => {
    minX = Math.min(minX, x); minY = Math.min(minY, y);
    maxX = Math.max(maxX, x); maxY = Math.max(maxY, y);
  });
  const pad = 100;
  minX -= pad; minY -= pad; maxX += pad; maxY += pad;
  const contentW = maxX - minX;
  const contentH = maxY - minY;
  const contentCx = (minX + maxX) / 2;
  const contentCy = (minY + maxY) / 2;
  const fitZoom = Math.min(dims.width / contentW, dims.height / contentH, MAX_ZOOM);
  return {
    zoom: Math.max(MIN_ZOOM, fitZoom),
    pan: { x: WORLD_W / 2 - contentCx, y: WORLD_H / 2 - contentCy },
  };
}

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
