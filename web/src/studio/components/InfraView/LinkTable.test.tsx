import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';
import type { HostGroup, HostStats, NodeWithState } from './types';
import type { LinkStateData } from '../../hooks/useLabStateWS';

vi.mock('../../hooks/usePersistedState', () => ({
  usePersistedState: (_key: string, initial: any) => [initial, vi.fn()],
}));

vi.mock('../../../utils/agentColors', () => ({
  getAgentColor: (id: string) => '#22c55e',
}));

import LinkTable from './LinkTable';

// ─── Pure functions replicated from source (not exported) ──────────

type Endpoint = { node: string; iface: string };

function parseEndpoint(part: string): Endpoint {
  const colonIdx = part.indexOf(':');
  if (colonIdx < 0) return { node: part, iface: '' };
  return { node: part.slice(0, colonIdx), iface: part.slice(colonIdx + 1) };
}

function formatIface(containerName: string, linuxIface: string, vendorLookup: Map<string, string>): string {
  const vendor = vendorLookup.get(`${containerName}:${linuxIface}`);
  if (vendor && vendor !== linuxIface) return `${vendor} (${linuxIface})`;
  return linuxIface;
}

const STATE_ORDER: Record<string, number> = {
  error: 0,
  pending: 1,
  unknown: 2,
  down: 3,
  up: 4,
};

function sortLinks(links: LinkStateData[]): LinkStateData[] {
  return [...links].sort((a, b) => {
    const ao = STATE_ORDER[a.actual_state] ?? 5;
    const bo = STATE_ORDER[b.actual_state] ?? 5;
    if (ao !== bo) return ao - bo;
    return a.link_name.localeCompare(b.link_name);
  });
}

function parseLinkEndpoints(linkName: string): [Endpoint, Endpoint] {
  const dashIdx = linkName.indexOf('-');
  const srcPart = dashIdx >= 0 ? linkName.slice(0, dashIdx) : linkName;
  const tgtPart = dashIdx >= 0 ? linkName.slice(dashIdx + 1) : '';
  return [parseEndpoint(srcPart), parseEndpoint(tgtPart)];
}

// ─── Factories ─────────────────────────────────────────────────────

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

function makeHostStats(overrides: Partial<HostStats> = {}): HostStats {
  return { nodeCount: 0, runningCount: 0, linkCount: 0, vlanTags: new Set(), ...overrides };
}

function makeHostGroup(overrides: Partial<HostGroup> = {}): HostGroup {
  return {
    hostId: 'h1',
    hostName: 'Host-1',
    agentId: 'a1',
    nodes: [],
    localLinks: [],
    stats: makeHostStats(),
    ...overrides,
  };
}

// ─── Pure Function Tests ───────────────────────────────────────────

describe('parseEndpoint', () => {
  it('parses "R1:eth1" into node and iface', () => {
    expect(parseEndpoint('R1:eth1')).toEqual({ node: 'R1', iface: 'eth1' });
  });

  it('handles no colon — whole string is node, iface empty', () => {
    expect(parseEndpoint('R1')).toEqual({ node: 'R1', iface: '' });
  });

  it('handles multiple colons — only splits on first', () => {
    expect(parseEndpoint('R1:eth1:extra')).toEqual({ node: 'R1', iface: 'eth1:extra' });
  });

  it('handles empty string', () => {
    expect(parseEndpoint('')).toEqual({ node: '', iface: '' });
  });
});

describe('formatIface', () => {
  it('returns vendor name with linux name in parens when different', () => {
    const lookup = new Map([['R1:eth1', 'Ethernet1']]);
    expect(formatIface('R1', 'eth1', lookup)).toBe('Ethernet1 (eth1)');
  });

  it('returns linux name when vendor name is the same', () => {
    const lookup = new Map([['R1:eth1', 'eth1']]);
    expect(formatIface('R1', 'eth1', lookup)).toBe('eth1');
  });

  it('returns linux name when no vendor mapping exists', () => {
    expect(formatIface('R1', 'eth1', new Map())).toBe('eth1');
  });
});

describe('sortLinks', () => {
  it('sorts by STATE_ORDER: error < pending < unknown < down < up', () => {
    const links = [
      makeLinkState({ link_name: 'up-link', actual_state: 'up' }),
      makeLinkState({ link_name: 'error-link', actual_state: 'error' }),
      makeLinkState({ link_name: 'pending-link', actual_state: 'pending' }),
      makeLinkState({ link_name: 'down-link', actual_state: 'down' }),
      makeLinkState({ link_name: 'unknown-link', actual_state: 'unknown' }),
    ];
    const sorted = sortLinks(links);
    expect(sorted.map(l => l.actual_state)).toEqual(['error', 'pending', 'unknown', 'down', 'up']);
  });

  it('sorts alphabetically by link_name within same state', () => {
    const links = [
      makeLinkState({ link_name: 'B:eth1-C:eth1', actual_state: 'up' }),
      makeLinkState({ link_name: 'A:eth1-D:eth1', actual_state: 'up' }),
    ];
    const sorted = sortLinks(links);
    expect(sorted.map(l => l.link_name)).toEqual(['A:eth1-D:eth1', 'B:eth1-C:eth1']);
  });

  it('does not mutate original array', () => {
    const links = [
      makeLinkState({ link_name: 'B', actual_state: 'up' }),
      makeLinkState({ link_name: 'A', actual_state: 'error' }),
    ];
    const original = [...links];
    sortLinks(links);
    expect(links).toEqual(original);
  });
});

describe('parseLinkEndpoints', () => {
  it('parses full link name into src and tgt endpoints', () => {
    const [src, tgt] = parseLinkEndpoints('R1:eth1-R2:eth2');
    expect(src).toEqual({ node: 'R1', iface: 'eth1' });
    expect(tgt).toEqual({ node: 'R2', iface: 'eth2' });
  });

  it('handles link name with no dash', () => {
    const [src, tgt] = parseLinkEndpoints('R1:eth1');
    expect(src).toEqual({ node: 'R1', iface: 'eth1' });
    expect(tgt).toEqual({ node: '', iface: '' });
  });
});

// ─── Component Rendering Tests ─────────────────────────────────────

describe('LinkTable component', () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it('shows "No links to display" when no links exist', () => {
    render(
      <LinkTable
        hostGroups={[makeHostGroup()]}
        crossHostLinks={[]}
        vendorLookup={new Map()}
        selectedLinkName={null}
        onSelectLink={vi.fn()}
      />
    );
    expect(screen.getByText('No links to display')).toBeInTheDocument();
  });

  it('renders local links section with count', () => {
    const link = makeLinkState({ actual_state: 'up' });
    const group = makeHostGroup({ localLinks: [link], stats: makeHostStats({ linkCount: 1 }) });
    render(
      <LinkTable
        hostGroups={[group]}
        crossHostLinks={[]}
        vendorLookup={new Map()}
        selectedLinkName={null}
        onSelectLink={vi.fn()}
      />
    );
    expect(screen.getByText(/Local Links/)).toBeInTheDocument();
  });

  it('renders cross-host links section', () => {
    const crossLink = makeLinkState({
      is_cross_host: true,
      source_host_id: 'a1',
      target_host_id: 'a2',
      source_vlan_tag: 100,
      target_vlan_tag: 200,
      vni: 5001,
    });
    render(
      <LinkTable
        hostGroups={[makeHostGroup()]}
        crossHostLinks={[crossLink]}
        vendorLookup={new Map()}
        selectedLinkName={null}
        onSelectLink={vi.fn()}
      />
    );
    expect(screen.getByText(/Cross-Host Links/)).toBeInTheDocument();
  });

  it('calls onSelectLink when a link row is clicked', () => {
    const onSelectLink = vi.fn();
    const link = makeLinkState({ link_name: 'R1:eth1-R2:eth1', actual_state: 'up' });
    const group = makeHostGroup({ localLinks: [link] });
    render(
      <LinkTable
        hostGroups={[group]}
        crossHostLinks={[]}
        vendorLookup={new Map()}
        selectedLinkName={null}
        onSelectLink={onSelectLink}
      />
    );
    // Find the row by the source node name
    const row = screen.getByText('R1').closest('tr')!;
    fireEvent.click(row);
    expect(onSelectLink).toHaveBeenCalledWith('R1:eth1-R2:eth1');
  });

  it('renders VLAN badges with numbers for cross-host links', () => {
    const crossLink = makeLinkState({
      is_cross_host: true,
      source_vlan_tag: 100,
      target_vlan_tag: 200,
    });
    render(
      <LinkTable
        hostGroups={[makeHostGroup()]}
        crossHostLinks={[crossLink]}
        vendorLookup={new Map()}
        selectedLinkName={null}
        onSelectLink={vi.fn()}
      />
    );
    expect(screen.getByText('100')).toBeInTheDocument();
    expect(screen.getByText('200')).toBeInTheDocument();
  });

  it('renders VNI badge for cross-host links', () => {
    const crossLink = makeLinkState({
      is_cross_host: true,
      vni: 7777,
    });
    render(
      <LinkTable
        hostGroups={[makeHostGroup()]}
        crossHostLinks={[crossLink]}
        vendorLookup={new Map()}
        selectedLinkName={null}
        onSelectLink={vi.fn()}
      />
    );
    expect(screen.getByText('7777')).toBeInTheDocument();
  });

  it('renders "-" when VLAN/VNI is null', () => {
    const crossLink = makeLinkState({
      is_cross_host: true,
      source_vlan_tag: null,
      target_vlan_tag: null,
      vni: null,
    });
    render(
      <LinkTable
        hostGroups={[makeHostGroup()]}
        crossHostLinks={[crossLink]}
        vendorLookup={new Map()}
        selectedLinkName={null}
        onSelectLink={vi.fn()}
      />
    );
    // Should render multiple "-" dashes for null VLAN/VNI values
    const dashes = screen.getAllByText('-');
    expect(dashes.length).toBeGreaterThanOrEqual(3);
  });

  it('renders "All" filter button', () => {
    render(
      <LinkTable
        hostGroups={[makeHostGroup()]}
        crossHostLinks={[]}
        vendorLookup={new Map()}
        selectedLinkName={null}
        onSelectLink={vi.fn()}
      />
    );
    expect(screen.getByText('All')).toBeInTheDocument();
  });
});
