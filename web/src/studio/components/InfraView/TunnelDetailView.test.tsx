import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import React from 'react';
import TunnelDetailView from './TunnelDetailView';
import type { AgentGraphNode, CrossHostBundle } from './types';
import type { LinkStateData } from '../../hooks/useLabStateWS';

// ============================================================================
// Helpers
// ============================================================================

function makeLink(overrides: Partial<LinkStateData> = {}): LinkStateData {
  return {
    link_name: 'router1:eth1-switch1:eth1',
    desired_state: 'up',
    actual_state: 'up',
    source_node: 'router1',
    target_node: 'switch1',
    source_vlan_tag: 100,
    target_vlan_tag: 200,
    vni: 5001,
    is_cross_host: true,
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
      nodeCount: 2,
      runningCount: 1,
      linkCount: 0,
      vlanTags: new Set<number>(),
    },
    ...overrides,
  };
}

function makeBundle(overrides: Partial<CrossHostBundle> = {}): CrossHostBundle {
  return {
    agentA: 'agent-1',
    agentB: 'agent-2',
    links: [makeLink()],
    hasError: false,
    allUp: true,
    ...overrides,
  };
}

// ============================================================================
// Tests
// ============================================================================

describe('TunnelDetailView', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // ── Empty state ──

  it('shows empty message when no cross-host tunnels exist', () => {
    render(
      <TunnelDetailView
        selectedAgentNodes={[makeAgent()]}
        relevantBundles={[]}
      />
    );
    expect(
      screen.getByText('No cross-host tunnels between selected agents')
    ).toBeInTheDocument();
  });

  it('shows empty message when bundles have no links', () => {
    render(
      <TunnelDetailView
        selectedAgentNodes={[makeAgent()]}
        relevantBundles={[makeBundle({ links: [] })]}
      />
    );
    expect(
      screen.getByText('No cross-host tunnels between selected agents')
    ).toBeInTheDocument();
  });

  // ── Header / Agent chips ──

  it('renders "Tunnels between" header text', () => {
    render(
      <TunnelDetailView
        selectedAgentNodes={[
          makeAgent({ agentId: 'a1', agentName: 'Agent One' }),
          makeAgent({ agentId: 'a2', agentName: 'Agent Two', color: '#3b82f6' }),
        ]}
        relevantBundles={[makeBundle()]}
      />
    );
    expect(screen.getByText('Tunnels between')).toBeInTheDocument();
  });

  it('renders agent name chips for selected agents', () => {
    render(
      <TunnelDetailView
        selectedAgentNodes={[
          makeAgent({ agentId: 'a1', agentName: 'Agent One' }),
          makeAgent({ agentId: 'a2', agentName: 'Agent Two', color: '#3b82f6' }),
        ]}
        relevantBundles={[makeBundle()]}
      />
    );
    expect(screen.getByText('Agent One')).toBeInTheDocument();
    expect(screen.getByText('Agent Two')).toBeInTheDocument();
  });

  it('renders agent color dots in chips', () => {
    const { container } = render(
      <TunnelDetailView
        selectedAgentNodes={[
          makeAgent({ agentId: 'a1', agentName: 'Agent One', color: '#22c55e' }),
          makeAgent({ agentId: 'a2', agentName: 'Agent Two', color: '#3b82f6' }),
        ]}
        relevantBundles={[makeBundle()]}
      />
    );
    const dots = container.querySelectorAll('.w-2.h-2.rounded-full');
    expect(dots.length).toBe(2);
    expect((dots[0] as HTMLElement).style.backgroundColor).toBe('rgb(34, 197, 94)');
    expect((dots[1] as HTMLElement).style.backgroundColor).toBe('rgb(59, 130, 246)');
  });

  // ── Table headers ──

  it('renders all table column headers', () => {
    render(
      <TunnelDetailView
        selectedAgentNodes={[makeAgent()]}
        relevantBundles={[makeBundle()]}
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

  // ── Link data rendering ──

  it('renders link source and target from link_name', () => {
    render(
      <TunnelDetailView
        selectedAgentNodes={[makeAgent()]}
        relevantBundles={[
          makeBundle({
            links: [makeLink({ link_name: 'r1:eth1-sw1:eth2' })],
          }),
        ]}
      />
    );
    expect(screen.getByText('r1:eth1')).toBeInTheDocument();
    expect(screen.getByText('sw1:eth2')).toBeInTheDocument();
  });

  it('renders VLAN tags when present', () => {
    render(
      <TunnelDetailView
        selectedAgentNodes={[makeAgent()]}
        relevantBundles={[
          makeBundle({
            links: [makeLink({ source_vlan_tag: 150, target_vlan_tag: 250 })],
          }),
        ]}
      />
    );
    expect(screen.getByText('150')).toBeInTheDocument();
    expect(screen.getByText('250')).toBeInTheDocument();
  });

  it('renders dash when VLAN tags are null', () => {
    render(
      <TunnelDetailView
        selectedAgentNodes={[makeAgent()]}
        relevantBundles={[
          makeBundle({
            links: [makeLink({ source_vlan_tag: null, target_vlan_tag: null, vni: null })],
          }),
        ]}
      />
    );
    // Three dashes: source vlan, vni, target vlan
    const dashes = screen.getAllByText('-');
    expect(dashes.length).toBeGreaterThanOrEqual(3);
  });

  it('renders VNI badge when present', () => {
    render(
      <TunnelDetailView
        selectedAgentNodes={[makeAgent()]}
        relevantBundles={[
          makeBundle({
            links: [makeLink({ vni: 5001 })],
          }),
        ]}
      />
    );
    expect(screen.getByText('5001')).toBeInTheDocument();
  });

  it('renders state text for each link', () => {
    render(
      <TunnelDetailView
        selectedAgentNodes={[makeAgent()]}
        relevantBundles={[
          makeBundle({
            links: [makeLink({ actual_state: 'up' })],
          }),
        ]}
      />
    );
    expect(screen.getByText('up')).toBeInTheDocument();
  });

  it('renders error message when present', () => {
    render(
      <TunnelDetailView
        selectedAgentNodes={[makeAgent()]}
        relevantBundles={[
          makeBundle({
            links: [
              makeLink({
                actual_state: 'error',
                error_message: 'VXLAN tunnel creation failed',
              }),
            ],
          }),
        ]}
      />
    );
    expect(screen.getByText('VXLAN tunnel creation failed')).toBeInTheDocument();
  });

  it('renders dash for error column when no error message', () => {
    render(
      <TunnelDetailView
        selectedAgentNodes={[makeAgent()]}
        relevantBundles={[
          makeBundle({
            links: [makeLink({ error_message: null })],
          }),
        ]}
      />
    );
    // Error column shows dash
    const tbody = document.querySelector('tbody');
    const errorCell = tbody?.querySelector('td:last-child');
    expect(errorCell?.textContent).toBe('-');
  });

  // ── State-based styling ──

  it('renders green dot for up state', () => {
    const { container } = render(
      <TunnelDetailView
        selectedAgentNodes={[makeAgent()]}
        relevantBundles={[
          makeBundle({ links: [makeLink({ actual_state: 'up' })] }),
        ]}
      />
    );
    const dots = container.querySelectorAll('tbody .w-1\\.5.h-1\\.5');
    expect(dots.length).toBe(1);
    expect(dots[0]).toHaveClass('bg-green-500');
  });

  it('renders red dot for error state', () => {
    const { container } = render(
      <TunnelDetailView
        selectedAgentNodes={[makeAgent()]}
        relevantBundles={[
          makeBundle({ links: [makeLink({ actual_state: 'error' })] }),
        ]}
      />
    );
    const dots = container.querySelectorAll('tbody .w-1\\.5.h-1\\.5');
    expect(dots[0]).toHaveClass('bg-red-500');
  });

  it('renders amber dot for pending state', () => {
    const { container } = render(
      <TunnelDetailView
        selectedAgentNodes={[makeAgent()]}
        relevantBundles={[
          makeBundle({ links: [makeLink({ actual_state: 'pending' })] }),
        ]}
      />
    );
    const dots = container.querySelectorAll('tbody .w-1\\.5.h-1\\.5');
    expect(dots[0]).toHaveClass('bg-amber-500');
  });

  it('applies green text color for up state text', () => {
    const { container } = render(
      <TunnelDetailView
        selectedAgentNodes={[makeAgent()]}
        relevantBundles={[
          makeBundle({ links: [makeLink({ actual_state: 'up' })] }),
        ]}
      />
    );
    const stateText = container.querySelector('.text-green-400');
    expect(stateText).toBeInTheDocument();
    expect(stateText?.textContent).toBe('up');
  });

  it('applies red text color for error state text', () => {
    const { container } = render(
      <TunnelDetailView
        selectedAgentNodes={[makeAgent()]}
        relevantBundles={[
          makeBundle({ links: [makeLink({ actual_state: 'error' })] }),
        ]}
      />
    );
    const stateText = container.querySelector('.text-red-400');
    expect(stateText).toBeInTheDocument();
    expect(stateText?.textContent).toBe('error');
  });

  // ── Row tints ──

  it('applies green row tint for up links', () => {
    const { container } = render(
      <TunnelDetailView
        selectedAgentNodes={[makeAgent()]}
        relevantBundles={[
          makeBundle({ links: [makeLink({ actual_state: 'up' })] }),
        ]}
      />
    );
    const row = container.querySelector('tbody tr');
    expect(row?.className).toContain('bg-green-950/20');
  });

  it('applies red row tint for error links', () => {
    const { container } = render(
      <TunnelDetailView
        selectedAgentNodes={[makeAgent()]}
        relevantBundles={[
          makeBundle({ links: [makeLink({ actual_state: 'error' })] }),
        ]}
      />
    );
    const row = container.querySelector('tbody tr');
    expect(row?.className).toContain('bg-red-950/20');
  });

  // ── Sorting ──

  it('sorts links by state priority: error first, then pending, then up', () => {
    const { container } = render(
      <TunnelDetailView
        selectedAgentNodes={[makeAgent()]}
        relevantBundles={[
          makeBundle({
            links: [
              makeLink({ link_name: 'a:e1-b:e1', actual_state: 'up' }),
              makeLink({ link_name: 'c:e1-d:e1', actual_state: 'error' }),
              makeLink({ link_name: 'e:e1-f:e1', actual_state: 'pending' }),
            ],
          }),
        ]}
      />
    );
    const rows = container.querySelectorAll('tbody tr');
    expect(rows.length).toBe(3);

    // First row should be error (c:e1)
    const firstSource = rows[0].querySelector('td')?.textContent;
    expect(firstSource).toBe('c:e1');

    // Second row should be pending (e:e1)
    const secondSource = rows[1].querySelector('td')?.textContent;
    expect(secondSource).toBe('e:e1');

    // Third row should be up (a:e1)
    const thirdSource = rows[2].querySelector('td')?.textContent;
    expect(thirdSource).toBe('a:e1');
  });

  it('sorts links alphabetically by name within same state', () => {
    const { container } = render(
      <TunnelDetailView
        selectedAgentNodes={[makeAgent()]}
        relevantBundles={[
          makeBundle({
            links: [
              makeLink({ link_name: 'z:e1-a:e1', actual_state: 'up' }),
              makeLink({ link_name: 'a:e1-b:e1', actual_state: 'up' }),
              makeLink({ link_name: 'm:e1-n:e1', actual_state: 'up' }),
            ],
          }),
        ]}
      />
    );
    const rows = container.querySelectorAll('tbody tr');
    const sources = Array.from(rows).map(
      (r) => r.querySelector('td')?.textContent
    );
    expect(sources).toEqual(['a:e1', 'm:e1', 'z:e1']);
  });

  // ── Multiple bundles ──

  it('flattens links from multiple bundles', () => {
    render(
      <TunnelDetailView
        selectedAgentNodes={[makeAgent()]}
        relevantBundles={[
          makeBundle({
            links: [makeLink({ link_name: 'r1:e1-s1:e1' })],
          }),
          makeBundle({
            agentA: 'agent-1',
            agentB: 'agent-3',
            links: [makeLink({ link_name: 'r2:e1-s2:e1' })],
          }),
        ]}
      />
    );
    expect(screen.getByText('r1:e1')).toBeInTheDocument();
    expect(screen.getByText('r2:e1')).toBeInTheDocument();
  });

  // ── Link name with hyphens in target ──

  it('correctly parses link names with hyphens in the target portion', () => {
    render(
      <TunnelDetailView
        selectedAgentNodes={[makeAgent()]}
        relevantBundles={[
          makeBundle({
            links: [makeLink({ link_name: 'src:e1-tgt-core-1:e2' })],
          }),
        ]}
      />
    );
    expect(screen.getByText('src:e1')).toBeInTheDocument();
    expect(screen.getByText('tgt-core-1:e2')).toBeInTheDocument();
  });

  // ── Error message tooltip ──

  it('sets error message as title attribute for tooltip', () => {
    render(
      <TunnelDetailView
        selectedAgentNodes={[makeAgent()]}
        relevantBundles={[
          makeBundle({
            links: [
              makeLink({
                actual_state: 'error',
                error_message: 'Connection refused on VXLAN port',
              }),
            ],
          }),
        ]}
      />
    );
    const errorEl = screen.getByText('Connection refused on VXLAN port');
    expect(errorEl).toHaveAttribute('title', 'Connection refused on VXLAN port');
  });
});
