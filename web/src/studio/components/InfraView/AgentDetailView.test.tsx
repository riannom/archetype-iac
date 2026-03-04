import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import React from 'react';
import AgentDetailView from './AgentDetailView';
import type { AgentGraphNode, NodeWithState } from './types';
import type { LinkStateData } from '../../hooks/useLabStateWS';

// ============================================================================
// Helpers
// ============================================================================

function makeNodeWithState(overrides: Partial<NodeWithState> = {}): NodeWithState {
  return {
    node: {
      id: 'node-1',
      name: 'router1',
      x: 0,
      y: 0,
      nodeType: 'device' as const,
      type: 'router' as any,
      model: 'ceos',
      version: '4.28.0F',
    },
    state: {
      id: 'ns-1',
      lab_id: 'lab-1',
      node_id: 'node-1',
      node_name: 'router1',
      desired_state: 'running' as any,
      actual_state: 'running',
    },
    containerName: 'archetype-lab1-router1',
    ...overrides,
  };
}

function makeLink(overrides: Partial<LinkStateData> = {}): LinkStateData {
  return {
    link_name: 'router1:eth1-switch1:eth1',
    desired_state: 'up',
    actual_state: 'up',
    source_node: 'router1',
    target_node: 'switch1',
    source_vlan_tag: 100,
    target_vlan_tag: 200,
    ...overrides,
  };
}

function makeAgent(overrides: Partial<AgentGraphNode> = {}): AgentGraphNode {
  return {
    agentId: 'agent-1',
    agentName: 'Agent One',
    color: '#22c55e',
    nodes: [],
    localLinks: [],
    stats: {
      nodeCount: 0,
      runningCount: 0,
      linkCount: 0,
      vlanTags: new Set<number>(),
    },
    ...overrides,
  };
}

// ============================================================================
// Tests
// ============================================================================

describe('AgentDetailView', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // ── Empty states ──

  it('shows "No nodes deployed" when agent has no nodes', () => {
    render(<AgentDetailView agent={makeAgent({ nodes: [] })} />);
    expect(screen.getByText('No nodes deployed')).toBeInTheDocument();
  });

  it('shows "No local links" when agent has no links', () => {
    render(<AgentDetailView agent={makeAgent({ localLinks: [] })} />);
    expect(screen.getByText('No local links')).toBeInTheDocument();
  });

  // ── Column headers ──

  it('renders Nodes and Local Links column headers', () => {
    render(<AgentDetailView agent={makeAgent()} />);
    expect(screen.getByText('Nodes')).toBeInTheDocument();
    expect(screen.getByText('Local Links')).toBeInTheDocument();
  });

  // ── Node rendering ──

  it('renders node names', () => {
    const agent = makeAgent({
      nodes: [
        makeNodeWithState({ node: { id: 'n1', name: 'router1', x: 0, y: 0, nodeType: 'device' as const, type: 'router' as any, model: 'ceos', version: '4.28.0F' } }),
        makeNodeWithState({ node: { id: 'n2', name: 'switch1', x: 0, y: 0, nodeType: 'device' as const, type: 'switch' as any, model: 'ceos', version: '4.28.0F' } }),
      ],
    });
    render(<AgentDetailView agent={agent} />);
    expect(screen.getByText('router1')).toBeInTheDocument();
    expect(screen.getByText('switch1')).toBeInTheDocument();
  });

  it('displays actual_state text for each node', () => {
    const agent = makeAgent({
      nodes: [
        makeNodeWithState({
          node: { id: 'n1', name: 'r1', x: 0, y: 0, nodeType: 'device' as const, type: 'router' as any, model: 'ceos', version: '4.28.0F' },
          state: { id: 'ns1', lab_id: 'l1', node_id: 'n1', node_name: 'r1', desired_state: 'running' as any, actual_state: 'running' },
        }),
      ],
    });
    render(<AgentDetailView agent={agent} />);
    expect(screen.getByText('running')).toBeInTheDocument();
  });

  it('shows "undeployed" when node state is null', () => {
    const agent = makeAgent({
      nodes: [
        makeNodeWithState({
          state: null,
        }),
      ],
    });
    render(<AgentDetailView agent={agent} />);
    expect(screen.getByText('undeployed')).toBeInTheDocument();
  });

  it('renders correct state dot color for running nodes', () => {
    const agent = makeAgent({
      nodes: [makeNodeWithState()],
    });
    const { container } = render(<AgentDetailView agent={agent} />);
    const dot = container.querySelector('.bg-green-500');
    expect(dot).toBeInTheDocument();
  });

  it('renders correct state dot color for error nodes', () => {
    const agent = makeAgent({
      nodes: [
        makeNodeWithState({
          state: { id: 'ns1', lab_id: 'l1', node_id: 'n1', node_name: 'r1', desired_state: 'running' as any, actual_state: 'error' },
        }),
      ],
    });
    const { container } = render(<AgentDetailView agent={agent} />);
    const dot = container.querySelector('.bg-red-500');
    expect(dot).toBeInTheDocument();
  });

  it('renders correct state dot color for stopped nodes', () => {
    const agent = makeAgent({
      nodes: [
        makeNodeWithState({
          state: { id: 'ns1', lab_id: 'l1', node_id: 'n1', node_name: 'r1', desired_state: 'stopped' as any, actual_state: 'stopped' },
        }),
      ],
    });
    const { container } = render(<AgentDetailView agent={agent} />);
    const dot = container.querySelector('.bg-stone-500');
    expect(dot).toBeInTheDocument();
  });

  it('renders correct state dot color for starting nodes', () => {
    const agent = makeAgent({
      nodes: [
        makeNodeWithState({
          state: { id: 'ns1', lab_id: 'l1', node_id: 'n1', node_name: 'r1', desired_state: 'running' as any, actual_state: 'starting' },
        }),
      ],
    });
    const { container } = render(<AgentDetailView agent={agent} />);
    const dot = container.querySelector('.bg-amber-500');
    expect(dot).toBeInTheDocument();
  });

  // ── Node row tints ──

  it('applies row tint for running nodes', () => {
    const agent = makeAgent({
      nodes: [makeNodeWithState()],
    });
    const { container } = render(<AgentDetailView agent={agent} />);
    expect(container.querySelector('.bg-green-950\\/20')).toBeInTheDocument();
  });

  it('applies row tint for error nodes', () => {
    const agent = makeAgent({
      nodes: [
        makeNodeWithState({
          state: { id: 'ns1', lab_id: 'l1', node_id: 'n1', node_name: 'r1', desired_state: 'running' as any, actual_state: 'error' },
        }),
      ],
    });
    const { container } = render(<AgentDetailView agent={agent} />);
    expect(container.querySelector('.bg-red-950\\/20')).toBeInTheDocument();
  });

  // ── Link rendering ──

  it('renders link endpoint names from link_name', () => {
    const agent = makeAgent({
      localLinks: [makeLink({ link_name: 'router1:eth1-switch1:eth1' })],
    });
    render(<AgentDetailView agent={agent} />);
    expect(screen.getByText('router1:eth1')).toBeInTheDocument();
    expect(screen.getByText('switch1:eth1')).toBeInTheDocument();
  });

  it('renders VLAN badges when tags are present', () => {
    const agent = makeAgent({
      localLinks: [makeLink({ source_vlan_tag: 100, target_vlan_tag: 200 })],
    });
    render(<AgentDetailView agent={agent} />);
    expect(screen.getByText('v100')).toBeInTheDocument();
    expect(screen.getByText('v200')).toBeInTheDocument();
  });

  it('does not render VLAN badges when tags are null', () => {
    const agent = makeAgent({
      localLinks: [makeLink({ source_vlan_tag: null, target_vlan_tag: null })],
    });
    render(<AgentDetailView agent={agent} />);
    expect(screen.queryByText(/^v\d+$/)).not.toBeInTheDocument();
  });

  it('renders link state dot with correct color for up links', () => {
    const agent = makeAgent({
      localLinks: [makeLink({ actual_state: 'up' })],
    });
    const { container } = render(<AgentDetailView agent={agent} />);
    // The link dot should be green for "up" state
    const linkSection = container.querySelectorAll('.w-1\\.5.h-1\\.5');
    expect(linkSection.length).toBeGreaterThan(0);
    expect(linkSection[0]).toHaveClass('bg-green-500');
  });

  it('renders link state dot with correct color for error links', () => {
    const agent = makeAgent({
      localLinks: [makeLink({ actual_state: 'error' })],
    });
    const { container } = render(<AgentDetailView agent={agent} />);
    const linkDots = container.querySelectorAll('.w-1\\.5.h-1\\.5');
    const errorDot = Array.from(linkDots).find((el) => el.classList.contains('bg-red-500'));
    expect(errorDot).toBeTruthy();
  });

  it('renders link state dot with correct color for pending links', () => {
    const agent = makeAgent({
      localLinks: [makeLink({ actual_state: 'pending' })],
    });
    const { container } = render(<AgentDetailView agent={agent} />);
    const linkDots = container.querySelectorAll('.w-1\\.5.h-1\\.5');
    const pendingDot = Array.from(linkDots).find((el) => el.classList.contains('bg-amber-500'));
    expect(pendingDot).toBeTruthy();
  });

  // ── Link row tints ──

  it('applies row tint for up links', () => {
    const agent = makeAgent({
      localLinks: [makeLink({ actual_state: 'up' })],
    });
    const { container } = render(<AgentDetailView agent={agent} />);
    expect(container.querySelector('.bg-green-950\\/20')).toBeInTheDocument();
  });

  // ── Multiple nodes and links ──

  it('renders multiple nodes and links together', () => {
    const agent = makeAgent({
      nodes: [
        makeNodeWithState({ node: { id: 'n1', name: 'r1', x: 0, y: 0, nodeType: 'device' as const, type: 'router' as any, model: 'ceos', version: '4.28.0F' } }),
        makeNodeWithState({ node: { id: 'n2', name: 'r2', x: 0, y: 0, nodeType: 'device' as const, type: 'router' as any, model: 'ceos', version: '4.28.0F' } }),
        makeNodeWithState({ node: { id: 'n3', name: 'sw1', x: 0, y: 0, nodeType: 'device' as const, type: 'switch' as any, model: 'ceos', version: '4.28.0F' } }),
      ],
      localLinks: [
        makeLink({ link_name: 'r1:eth1-r2:eth1' }),
        makeLink({ link_name: 'r2:eth2-sw1:eth1', actual_state: 'pending' }),
      ],
    });
    render(<AgentDetailView agent={agent} />);
    expect(screen.getByText('r1')).toBeInTheDocument();
    expect(screen.getByText('r2')).toBeInTheDocument();
    expect(screen.getByText('sw1')).toBeInTheDocument();
    expect(screen.getByText('r1:eth1')).toBeInTheDocument();
    expect(screen.getByText('sw1:eth1')).toBeInTheDocument();
  });

  // ── Link name parsing with hyphens ──

  it('handles link names containing hyphens in target part', () => {
    const agent = makeAgent({
      localLinks: [makeLink({ link_name: 'r1:eth1-sw-core-1:eth2' })],
    });
    render(<AgentDetailView agent={agent} />);
    expect(screen.getByText('r1:eth1')).toBeInTheDocument();
    // Target joins remaining parts with '-'
    expect(screen.getByText('sw-core-1:eth2')).toBeInTheDocument();
  });
});
