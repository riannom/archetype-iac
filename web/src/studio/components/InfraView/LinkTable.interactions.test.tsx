import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import React from 'react';
import type { HostGroup, HostStats } from './types';
import type { LinkStateData } from '../../hooks/useLabStateWS';

// ─── Mocks ────────────────────────────────────────────────────────

let mockPersistedStates: Record<string, { value: any; setter: ReturnType<typeof vi.fn> }> = {};

vi.mock('../../hooks/usePersistedState', () => ({
  usePersistedState: (key: string, initial: any) => {
    if (!mockPersistedStates[key]) {
      mockPersistedStates[key] = { value: initial, setter: vi.fn() };
    }
    return [mockPersistedStates[key].value, mockPersistedStates[key].setter];
  },
}));

vi.mock('../../../utils/agentColors', () => ({
  getAgentColor: (id: string) => {
    const colors: Record<string, string> = {
      agent1: '#22c55e',
      agent2: '#3b82f6',
      agent3: '#ef4444',
    };
    return colors[id] || '#888888';
  },
}));

import LinkTable from './LinkTable';

// ─── Factories ──────────────────────────────────────────────────────

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
    agentId: 'agent1',
    nodes: [],
    localLinks: [],
    stats: makeHostStats(),
    ...overrides,
  };
}

function renderLinkTable(props: Partial<React.ComponentProps<typeof LinkTable>> = {}) {
  const defaultProps: React.ComponentProps<typeof LinkTable> = {
    hostGroups: [],
    crossHostLinks: [],
    vendorLookup: new Map(),
    selectedLinkName: null,
    onSelectLink: vi.fn(),
  };
  return render(<LinkTable {...defaultProps} {...props} />);
}

// ─── Tests ──────────────────────────────────────────────────────────

describe('LinkTable - interactions', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockPersistedStates = {};
  });

  // ── Empty State ─────────────────────────────────────────────────

  describe('empty state', () => {
    it('shows empty message when hostGroups have no links', () => {
      renderLinkTable({ hostGroups: [makeHostGroup()] });
      expect(screen.getByText('No links to display')).toBeInTheDocument();
    });

    it('shows empty message when both local and cross-host are empty arrays', () => {
      renderLinkTable({ hostGroups: [], crossHostLinks: [] });
      expect(screen.getByText('No links to display')).toBeInTheDocument();
    });

    it('does not render Local Links or Cross-Host Links headers in empty state', () => {
      renderLinkTable({ hostGroups: [makeHostGroup()] });
      expect(screen.queryByText(/Local Links/)).not.toBeInTheDocument();
      expect(screen.queryByText(/Cross-Host Links/)).not.toBeInTheDocument();
    });

    it('still renders the filter bar in empty state', () => {
      renderLinkTable({ hostGroups: [makeHostGroup()] });
      expect(screen.getByText('All')).toBeInTheDocument();
      expect(screen.getByText(/Filter/i)).toBeInTheDocument();
    });
  });

  // ── State Badge Rendering ───────────────────────────────────────

  describe('state badges', () => {
    it.each([
      ['up', 'bg-green-500'],
      ['down', 'bg-stone-500'],
      ['pending', 'bg-amber-500'],
      ['error', 'bg-red-500'],
      ['unknown', 'bg-stone-600'],
    ] as const)('renders correct dot color for %s state', (state, expectedDotClass) => {
      const link = makeLinkState({ actual_state: state as LinkStateData['actual_state'] });
      const group = makeHostGroup({ localLinks: [link] });
      renderLinkTable({ hostGroups: [group] });

      const stateText = screen.getByText(state);
      expect(stateText).toBeInTheDocument();

      // The dot is a sibling div within the same flex container
      const container = stateText.closest('.flex');
      expect(container).not.toBeNull();
      const dot = container!.querySelector('div[class*="rounded-full"]');
      expect(dot).not.toBeNull();
      expect(dot!.className).toContain(expectedDotClass);
    });

    it('renders state text with correct color classes', () => {
      const link = makeLinkState({ actual_state: 'error' });
      const group = makeHostGroup({ localLinks: [link] });
      renderLinkTable({ hostGroups: [group] });

      const stateText = screen.getByText('error');
      expect(stateText.className).toContain('text-red-400');
    });
  });

  // ── Sorting ─────────────────────────────────────────────────────

  describe('sorting', () => {
    it('displays links sorted by state priority: error first, up last', () => {
      const links = [
        makeLinkState({ link_name: 'A:eth1-B:eth1', actual_state: 'up', source_node: 'A', target_node: 'B' }),
        makeLinkState({ link_name: 'C:eth1-D:eth1', actual_state: 'error', source_node: 'C', target_node: 'D' }),
        makeLinkState({ link_name: 'E:eth1-F:eth1', actual_state: 'pending', source_node: 'E', target_node: 'F' }),
      ];
      const group = makeHostGroup({ localLinks: links });
      renderLinkTable({ hostGroups: [group] });

      const rows = screen.getAllByRole('row').filter(r => r.querySelector('td'));
      // error (C) should come first, then pending (E), then up (A)
      expect(within(rows[0]).getByText('C')).toBeInTheDocument();
      expect(within(rows[1]).getByText('E')).toBeInTheDocument();
      expect(within(rows[2]).getByText('A')).toBeInTheDocument();
    });

    it('sorts alphabetically by link_name within same state', () => {
      const links = [
        makeLinkState({ link_name: 'Z:eth1-W:eth1', actual_state: 'up', source_node: 'Z', target_node: 'W' }),
        makeLinkState({ link_name: 'A:eth1-B:eth1', actual_state: 'up', source_node: 'A', target_node: 'B' }),
      ];
      const group = makeHostGroup({ localLinks: links });
      renderLinkTable({ hostGroups: [group] });

      const rows = screen.getAllByRole('row').filter(r => r.querySelector('td'));
      // A:eth1-B:eth1 sorts before Z:eth1-W:eth1
      const firstRowSrc = within(rows[0]).getByText('A');
      const secondRowSrc = within(rows[1]).getByText('Z');
      expect(firstRowSrc).toBeInTheDocument();
      expect(secondRowSrc).toBeInTheDocument();
    });

    it('sorts cross-host links by state priority', () => {
      const crossLinks = [
        makeLinkState({ link_name: 'X:eth1-Y:eth1', actual_state: 'up', source_node: 'X', target_node: 'Y', is_cross_host: true }),
        makeLinkState({ link_name: 'M:eth1-N:eth1', actual_state: 'error', source_node: 'M', target_node: 'N', is_cross_host: true }),
      ];
      renderLinkTable({ hostGroups: [makeHostGroup()], crossHostLinks: crossLinks });

      // Find the cross-host table (only table rendered since no local links)
      const tables = screen.getAllByRole('table');
      const crossTable = tables[tables.length - 1]; // last table is cross-host
      const rows = within(crossTable).getAllByRole('row').filter(r => r.querySelector('td'));
      expect(within(rows[0]).getByText('M')).toBeInTheDocument();
      expect(within(rows[1]).getByText('X')).toBeInTheDocument();
    });
  });

  // ── Host Filter ─────────────────────────────────────────────────

  describe('host filter', () => {
    function setupMultiHost() {
      const group1 = makeHostGroup({
        hostId: 'h1',
        hostName: 'Host-1',
        agentId: 'agent1',
        localLinks: [
          makeLinkState({ link_name: 'R1:eth1-R2:eth1', source_node: 'R1', target_node: 'R2' }),
        ],
      });
      const group2 = makeHostGroup({
        hostId: 'h2',
        hostName: 'Host-2',
        agentId: 'agent2',
        localLinks: [
          makeLinkState({ link_name: 'R3:eth1-R4:eth1', source_node: 'R3', target_node: 'R4' }),
        ],
      });
      // Use unique node names for cross-host to avoid collision with local links
      const crossLinks = [
        makeLinkState({
          link_name: 'XH1:eth2-XH2:eth2',
          source_node: 'XH1',
          target_node: 'XH2',
          is_cross_host: true,
          source_host_id: 'agent1',
          target_host_id: 'agent2',
        }),
      ];
      return { groups: [group1, group2], crossLinks };
    }

    it('renders filter buttons for each host group with a non-empty agentId', () => {
      const { groups, crossLinks } = setupMultiHost();
      renderLinkTable({ hostGroups: groups, crossHostLinks: crossLinks });

      expect(screen.getByText('All')).toBeInTheDocument();
      // Host names appear in both filter bar and sub-headers, so use getAllByText
      expect(screen.getAllByText('Host-1').length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText('Host-2').length).toBeGreaterThanOrEqual(1);
    });

    it('does not render filter button for host with empty agentId', () => {
      const group = makeHostGroup({ agentId: '', hostName: 'No-Agent' });
      renderLinkTable({ hostGroups: [group] });
      expect(screen.queryByText('No-Agent')).not.toBeInTheDocument();
    });

    it('filters local links when a host button is clicked', () => {
      const { groups, crossLinks } = setupMultiHost();
      renderLinkTable({ hostGroups: groups, crossHostLinks: crossLinks });

      // Initially both hosts' links are visible
      expect(screen.getAllByText('R1').length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText('R3').length).toBeGreaterThanOrEqual(1);

      // Click Host-1 filter button
      const filterBar = screen.getByText('All').parentElement!;
      fireEvent.click(within(filterBar).getByText('Host-1'));

      // R1 still visible (local link on Host-1)
      expect(screen.getAllByText('R1').length).toBeGreaterThanOrEqual(1);
      // Host-2 local link R3:eth1-R4:eth1 should be filtered out
      expect(screen.queryByText('R4')).not.toBeInTheDocument();
    });

    it('filters cross-host links to those involving the selected host', () => {
      const group1 = makeHostGroup({ hostId: 'h1', agentId: 'agent1', hostName: 'Host-1' });
      const group2 = makeHostGroup({ hostId: 'h2', agentId: 'agent2', hostName: 'Host-2' });
      const group3 = makeHostGroup({ hostId: 'h3', agentId: 'agent3', hostName: 'Host-3' });

      const crossLinks = [
        makeLinkState({
          link_name: 'X:eth1-Y:eth1', source_node: 'X', target_node: 'Y',
          is_cross_host: true, source_host_id: 'agent1', target_host_id: 'agent2',
        }),
        makeLinkState({
          link_name: 'P:eth1-Q:eth1', source_node: 'P', target_node: 'Q',
          is_cross_host: true, source_host_id: 'agent2', target_host_id: 'agent3',
        }),
      ];

      renderLinkTable({
        hostGroups: [group1, group2, group3],
        crossHostLinks: crossLinks,
      });

      // Click Host-1 filter button
      const filterBar = screen.getByText('All').parentElement!;
      fireEvent.click(within(filterBar).getByText('Host-1'));
      expect(screen.getByText('X')).toBeInTheDocument();
      expect(screen.queryByText('P')).not.toBeInTheDocument();
    });

    it('toggles filter off when the same host is clicked again', () => {
      const { groups, crossLinks } = setupMultiHost();
      renderLinkTable({ hostGroups: groups, crossHostLinks: crossLinks });

      const filterBar = screen.getByText('All').parentElement!;
      // Activate filter
      fireEvent.click(within(filterBar).getByText('Host-1'));
      expect(screen.queryByText('R4')).not.toBeInTheDocument();

      // Click same host again to deactivate
      fireEvent.click(within(filterBar).getByText('Host-1'));
      expect(screen.getAllByText('R3').length).toBeGreaterThanOrEqual(1);
    });

    it('clicking "All" button resets filter', () => {
      const { groups, crossLinks } = setupMultiHost();
      renderLinkTable({ hostGroups: groups, crossHostLinks: crossLinks });

      const filterBar = screen.getByText('All').parentElement!;
      fireEvent.click(within(filterBar).getByText('Host-1'));
      fireEvent.click(screen.getByText('All'));
      expect(screen.getAllByText('R3').length).toBeGreaterThanOrEqual(1);
    });
  });

  // ── VLAN Display ────────────────────────────────────────────────

  describe('VLAN and VNI display', () => {
    it('renders source and target VLAN tags as badges', () => {
      const crossLink = makeLinkState({
        is_cross_host: true,
        source_vlan_tag: 150,
        target_vlan_tag: 250,
        vni: 9001,
      });
      renderLinkTable({ hostGroups: [makeHostGroup()], crossHostLinks: [crossLink] });

      expect(screen.getByText('150')).toBeInTheDocument();
      expect(screen.getByText('250')).toBeInTheDocument();
    });

    it('renders VNI badge with violet-themed styling', () => {
      const crossLink = makeLinkState({
        is_cross_host: true,
        vni: 42000,
      });
      renderLinkTable({ hostGroups: [makeHostGroup()], crossHostLinks: [crossLink] });

      const vniBadge = screen.getByText('42000');
      expect(vniBadge.className).toContain('bg-violet-950');
      expect(vniBadge.className).toContain('text-violet-400');
    });

    it('renders VLAN badges with stone-themed styling', () => {
      const crossLink = makeLinkState({
        is_cross_host: true,
        source_vlan_tag: 333,
        target_vlan_tag: 444,
      });
      renderLinkTable({ hostGroups: [makeHostGroup()], crossHostLinks: [crossLink] });

      const srcBadge = screen.getByText('333');
      expect(srcBadge.className).toContain('bg-stone-800');
      expect(srcBadge.className).toContain('font-mono');
    });

    it('renders dash placeholders when VLAN/VNI are null', () => {
      const crossLink = makeLinkState({
        is_cross_host: true,
        source_vlan_tag: null,
        target_vlan_tag: null,
        vni: null,
      });
      renderLinkTable({ hostGroups: [makeHostGroup()], crossHostLinks: [crossLink] });

      // 3 dashes for src VLAN, VNI, tgt VLAN, plus possibly error column
      const dashes = screen.getAllByText('-');
      expect(dashes.length).toBeGreaterThanOrEqual(3);
    });

    it('renders dash placeholders when VLAN/VNI are undefined', () => {
      const crossLink = makeLinkState({ is_cross_host: true });
      // source_vlan_tag, target_vlan_tag, vni are all undefined
      renderLinkTable({ hostGroups: [makeHostGroup()], crossHostLinks: [crossLink] });

      const dashes = screen.getAllByText('-');
      expect(dashes.length).toBeGreaterThanOrEqual(3);
    });

    it('local links do not show VLAN or VNI columns', () => {
      const link = makeLinkState({ actual_state: 'up' });
      const group = makeHostGroup({ localLinks: [link] });
      renderLinkTable({ hostGroups: [group] });

      expect(screen.queryByText('Src VLAN')).not.toBeInTheDocument();
      expect(screen.queryByText('Tgt VLAN')).not.toBeInTheDocument();
      expect(screen.queryByText('VNI')).not.toBeInTheDocument();
    });

    it('cross-host links include VLAN and VNI column headers', () => {
      const crossLink = makeLinkState({ is_cross_host: true });
      renderLinkTable({ hostGroups: [makeHostGroup()], crossHostLinks: [crossLink] });

      expect(screen.getByText('Src VLAN')).toBeInTheDocument();
      expect(screen.getByText('Tgt VLAN')).toBeInTheDocument();
      expect(screen.getByText('VNI')).toBeInTheDocument();
    });
  });

  // ── Cross-Host Indicators ───────────────────────────────────────

  describe('cross-host section', () => {
    it('shows Cross-Host Links section with count', () => {
      const crossLinks = [
        makeLinkState({ link_name: 'A:eth1-B:eth1', is_cross_host: true, source_node: 'A', target_node: 'B' }),
        makeLinkState({ link_name: 'C:eth1-D:eth1', is_cross_host: true, source_node: 'C', target_node: 'D' }),
      ];
      renderLinkTable({ hostGroups: [makeHostGroup()], crossHostLinks: crossLinks });

      expect(screen.getByText(/Cross-Host Links \(2\)/)).toBeInTheDocument();
    });

    it('renders extra columns for cross-host that local lacks', () => {
      // Render both sections
      const localLink = makeLinkState({ link_name: 'L1:eth1-L2:eth1', source_node: 'L1', target_node: 'L2' });
      const crossLink = makeLinkState({ link_name: 'C1:eth1-C2:eth1', is_cross_host: true, source_node: 'C1', target_node: 'C2' });
      const group = makeHostGroup({ localLinks: [localLink] });
      renderLinkTable({ hostGroups: [group], crossHostLinks: [crossLink] });

      // Cross-host table has Src VLAN, VNI, Tgt VLAN
      const tables = screen.getAllByRole('table');
      expect(tables.length).toBe(2);

      // Local table headers
      const localHeaders = within(tables[0]).getAllByRole('columnheader');
      const localHeaderTexts = localHeaders.map(h => h.textContent?.replace('⠿', '').trim());
      expect(localHeaderTexts).not.toContain('Src VLAN');
      expect(localHeaderTexts).not.toContain('VNI');

      // Cross-host table headers
      const crossHeaders = within(tables[1]).getAllByRole('columnheader');
      const crossHeaderTexts = crossHeaders.map(h => h.textContent?.replace('⠿', '').trim());
      expect(crossHeaderTexts).toContain('Src VLAN');
      expect(crossHeaderTexts).toContain('VNI');
      expect(crossHeaderTexts).toContain('Tgt VLAN');
    });
  });

  // ── Row Selection ───────────────────────────────────────────────

  describe('row selection', () => {
    it('calls onSelectLink with link_name when local link row is clicked', () => {
      const onSelectLink = vi.fn();
      const link = makeLinkState({ link_name: 'SW1:eth1-SW2:eth1', source_node: 'SW1', target_node: 'SW2' });
      const group = makeHostGroup({ localLinks: [link] });
      renderLinkTable({ hostGroups: [group], onSelectLink });

      fireEvent.click(screen.getByText('SW1').closest('tr')!);
      expect(onSelectLink).toHaveBeenCalledWith('SW1:eth1-SW2:eth1');
    });

    it('calls onSelectLink with link_name when cross-host link row is clicked', () => {
      const onSelectLink = vi.fn();
      const crossLink = makeLinkState({
        link_name: 'R5:eth3-R6:eth3',
        source_node: 'R5',
        target_node: 'R6',
        is_cross_host: true,
      });
      renderLinkTable({ hostGroups: [makeHostGroup()], crossHostLinks: [crossLink], onSelectLink });

      fireEvent.click(screen.getByText('R5').closest('tr')!);
      expect(onSelectLink).toHaveBeenCalledWith('R5:eth3-R6:eth3');
    });

    it('applies ring styling to the selected link row', () => {
      const link = makeLinkState({ link_name: 'N1:eth1-N2:eth1', source_node: 'N1', target_node: 'N2' });
      const group = makeHostGroup({ localLinks: [link] });
      renderLinkTable({ hostGroups: [group], selectedLinkName: 'N1:eth1-N2:eth1' });

      const row = screen.getByText('N1').closest('tr')!;
      expect(row.className).toContain('ring-1');
      expect(row.className).toContain('ring-stone-500');
    });

    it('does not apply ring styling to unselected rows', () => {
      const link = makeLinkState({ link_name: 'N1:eth1-N2:eth1', source_node: 'N1', target_node: 'N2' });
      const group = makeHostGroup({ localLinks: [link] });
      renderLinkTable({ hostGroups: [group], selectedLinkName: 'other-link' });

      const row = screen.getByText('N1').closest('tr')!;
      expect(row.className).not.toContain('ring-1');
    });
  });

  // ── Row Tinting ─────────────────────────────────────────────────

  describe('row tinting by state', () => {
    it('applies green tint to up-state rows', () => {
      const link = makeLinkState({ link_name: 'G1:eth1-G2:eth1', actual_state: 'up', source_node: 'G1', target_node: 'G2' });
      const group = makeHostGroup({ localLinks: [link] });
      renderLinkTable({ hostGroups: [group] });

      const row = screen.getByText('G1').closest('tr')!;
      expect(row.className).toContain('bg-green-950/20');
    });

    it('applies red tint to error-state rows', () => {
      const link = makeLinkState({ link_name: 'E1:eth1-E2:eth1', actual_state: 'error', source_node: 'E1', target_node: 'E2' });
      const group = makeHostGroup({ localLinks: [link] });
      renderLinkTable({ hostGroups: [group] });

      const row = screen.getByText('E1').closest('tr')!;
      expect(row.className).toContain('bg-red-950/20');
    });

    it('applies amber tint to pending-state rows', () => {
      const link = makeLinkState({ link_name: 'P1:eth1-P2:eth1', actual_state: 'pending', source_node: 'P1', target_node: 'P2' });
      const group = makeHostGroup({ localLinks: [link] });
      renderLinkTable({ hostGroups: [group] });

      const row = screen.getByText('P1').closest('tr')!;
      expect(row.className).toContain('bg-amber-950/15');
    });

    it('applies no tint for down-state rows', () => {
      const link = makeLinkState({ link_name: 'D1:eth1-D2:eth1', actual_state: 'down', source_node: 'D1', target_node: 'D2' });
      const group = makeHostGroup({ localLinks: [link] });
      renderLinkTable({ hostGroups: [group] });

      const row = screen.getByText('D1').closest('tr')!;
      expect(row.className).not.toContain('bg-green-950');
      expect(row.className).not.toContain('bg-red-950');
      expect(row.className).not.toContain('bg-amber-950');
    });
  });

  // ── Error Column ────────────────────────────────────────────────

  describe('error column', () => {
    it('renders error message text when present', () => {
      const link = makeLinkState({
        actual_state: 'error',
        error_message: 'VXLAN tunnel failed',
        source_node: 'Err1',
        target_node: 'Err2',
      });
      const group = makeHostGroup({ localLinks: [link] });
      renderLinkTable({ hostGroups: [group] });

      expect(screen.getByText('VXLAN tunnel failed')).toBeInTheDocument();
    });

    it('renders dash when no error message', () => {
      const link = makeLinkState({ actual_state: 'up', error_message: undefined });
      const group = makeHostGroup({ localLinks: [link] });
      renderLinkTable({ hostGroups: [group] });

      // One dash for the empty error column
      const dashes = screen.getAllByText('-');
      expect(dashes.length).toBeGreaterThanOrEqual(1);
    });

    it('sets title attribute on error message for tooltip', () => {
      const link = makeLinkState({
        actual_state: 'error',
        error_message: 'Link creation timeout after 30s',
      });
      const group = makeHostGroup({ localLinks: [link] });
      renderLinkTable({ hostGroups: [group] });

      const errorSpan = screen.getByText('Link creation timeout after 30s');
      expect(errorSpan.getAttribute('title')).toBe('Link creation timeout after 30s');
    });
  });

  // ── Vendor Interface Formatting ─────────────────────────────────

  describe('vendor interface formatting', () => {
    it('displays vendor name with linux name in parens', () => {
      const vendorLookup = new Map([
        ['R1:eth1', 'Ethernet1'],
        ['R2:eth1', 'Ethernet1'],
      ]);
      const link = makeLinkState({ source_node: 'R1', target_node: 'R2' });
      const group = makeHostGroup({ localLinks: [link] });
      renderLinkTable({ hostGroups: [group], vendorLookup });

      const ifaceTexts = screen.getAllByText('Ethernet1 (eth1)');
      expect(ifaceTexts.length).toBe(2);
    });

    it('displays raw linux name when no vendor mapping', () => {
      const link = makeLinkState({
        link_name: 'SW1:eth3-SW2:eth4',
        source_node: 'SW1',
        target_node: 'SW2',
      });
      const group = makeHostGroup({ localLinks: [link] });
      renderLinkTable({ hostGroups: [group], vendorLookup: new Map() });

      expect(screen.getByText('eth3')).toBeInTheDocument();
      expect(screen.getByText('eth4')).toBeInTheDocument();
    });

    it('displays raw name when vendor name matches linux name', () => {
      const vendorLookup = new Map([['R1:eth1', 'eth1']]);
      const link = makeLinkState({ source_node: 'R1', target_node: 'R2' });
      const group = makeHostGroup({ localLinks: [link] });
      renderLinkTable({ hostGroups: [group], vendorLookup });

      // Should show "eth1" not "eth1 (eth1)"
      expect(screen.queryByText('eth1 (eth1)')).not.toBeInTheDocument();
      expect(screen.getAllByText('eth1').length).toBeGreaterThanOrEqual(1);
    });
  });

  // ── Column Headers ──────────────────────────────────────────────

  describe('column headers', () => {
    it('renders all local link column headers', () => {
      const link = makeLinkState();
      const group = makeHostGroup({ localLinks: [link] });
      renderLinkTable({ hostGroups: [group] });

      // Strip the drag handle character
      const headers = screen.getAllByRole('columnheader');
      const headerTexts = headers.map(h => h.textContent?.replace('⠿', '').trim());
      expect(headerTexts).toContain('State');
      expect(headerTexts).toContain('Source');
      expect(headerTexts).toContain('Src Interface');
      expect(headerTexts).toContain('Target');
      expect(headerTexts).toContain('Tgt Interface');
      expect(headerTexts).toContain('Error');
    });

    it('renders all cross-host link column headers', () => {
      const crossLink = makeLinkState({ is_cross_host: true });
      renderLinkTable({ hostGroups: [makeHostGroup()], crossHostLinks: [crossLink] });

      const headers = screen.getAllByRole('columnheader');
      const headerTexts = headers.map(h => h.textContent?.replace('⠿', '').trim());
      expect(headerTexts).toContain('Src VLAN');
      expect(headerTexts).toContain('VNI');
      expect(headerTexts).toContain('Tgt VLAN');
    });

    it('column headers are draggable', () => {
      const link = makeLinkState();
      const group = makeHostGroup({ localLinks: [link] });
      renderLinkTable({ hostGroups: [group] });

      const headers = screen.getAllByRole('columnheader');
      headers.forEach(header => {
        expect(header.getAttribute('draggable')).toBe('true');
      });
    });
  });

  // ── Host Sub-headers ────────────────────────────────────────────

  describe('host sub-headers', () => {
    it('shows host sub-headers when multiple hosts have local links and no filter is active', () => {
      const group1 = makeHostGroup({
        hostId: 'h1', hostName: 'Host-1', agentId: 'agent1',
        localLinks: [makeLinkState({ link_name: 'A:eth1-B:eth1', source_node: 'A', target_node: 'B' })],
      });
      const group2 = makeHostGroup({
        hostId: 'h2', hostName: 'Host-2', agentId: 'agent2',
        localLinks: [makeLinkState({ link_name: 'C:eth1-D:eth1', source_node: 'C', target_node: 'D' })],
      });

      renderLinkTable({ hostGroups: [group1, group2] });

      // Host names appear in both filter bar AND sub-headers (2 each)
      expect(screen.getAllByText('Host-1').length).toBe(2);
      expect(screen.getAllByText('Host-2').length).toBe(2);
    });

    it('does not show host sub-headers when only one host has local links', () => {
      const group1 = makeHostGroup({
        hostId: 'h1', hostName: 'Host-1', agentId: 'agent1',
        localLinks: [makeLinkState()],
      });
      const group2 = makeHostGroup({
        hostId: 'h2', hostName: 'Host-2', agentId: 'agent2',
        localLinks: [], // No links
      });

      renderLinkTable({ hostGroups: [group1, group2] });

      // Host-1 should appear in filter bar but not as sub-header
      // since only one group has local links, sub-headers are suppressed
      const allHost1Text = screen.getAllByText('Host-1');
      // Only the filter button, not a sub-header row
      expect(allHost1Text.length).toBe(1);
    });
  });

  // ── Local Links Count ───────────────────────────────────────────

  describe('section counts', () => {
    it('shows correct count in Local Links header', () => {
      const links = [
        makeLinkState({ link_name: 'A:eth1-B:eth1', source_node: 'A', target_node: 'B' }),
        makeLinkState({ link_name: 'C:eth1-D:eth1', source_node: 'C', target_node: 'D' }),
        makeLinkState({ link_name: 'E:eth1-F:eth1', source_node: 'E', target_node: 'F' }),
      ];
      const group = makeHostGroup({ localLinks: links });
      renderLinkTable({ hostGroups: [group] });

      expect(screen.getByText(/Local Links \(3\)/)).toBeInTheDocument();
    });

    it('shows combined count across multiple host groups', () => {
      const group1 = makeHostGroup({
        hostId: 'h1', agentId: 'agent1', hostName: 'Host-1',
        localLinks: [
          makeLinkState({ link_name: 'A:eth1-B:eth1', source_node: 'A', target_node: 'B' }),
        ],
      });
      const group2 = makeHostGroup({
        hostId: 'h2', agentId: 'agent2', hostName: 'Host-2',
        localLinks: [
          makeLinkState({ link_name: 'C:eth1-D:eth1', source_node: 'C', target_node: 'D' }),
          makeLinkState({ link_name: 'E:eth1-F:eth1', source_node: 'E', target_node: 'F' }),
        ],
      });
      renderLinkTable({ hostGroups: [group1, group2] });

      expect(screen.getByText(/Local Links \(3\)/)).toBeInTheDocument();
    });
  });

  // ── Endpoint Parsing Edge Cases ─────────────────────────────────

  describe('endpoint parsing in rendered output', () => {
    it('handles link names with colons in interface part', () => {
      const link = makeLinkState({
        link_name: 'R1:GigabitEthernet0/0-R2:GigabitEthernet0/1',
        source_node: 'R1',
        target_node: 'R2',
      });
      const group = makeHostGroup({ localLinks: [link] });
      renderLinkTable({ hostGroups: [group] });

      expect(screen.getByText('R1')).toBeInTheDocument();
      expect(screen.getByText('R2')).toBeInTheDocument();
    });
  });

  // ── Draggable Header Interaction ────────────────────────────────

  describe('drag-and-drop column reorder', () => {
    it('fires dragStart event on column header', () => {
      const link = makeLinkState();
      const group = makeHostGroup({ localLinks: [link] });
      renderLinkTable({ hostGroups: [group] });

      const headers = screen.getAllByRole('columnheader');
      const stateHeader = headers[0]; // First column is State

      // Should not throw when dragging
      fireEvent.dragStart(stateHeader, {
        dataTransfer: { effectAllowed: '', setData: vi.fn() },
      });
    });

    it('fires dragOver and drop events without error', () => {
      const link = makeLinkState();
      const group = makeHostGroup({ localLinks: [link] });
      renderLinkTable({ hostGroups: [group] });

      const headers = screen.getAllByRole('columnheader');
      fireEvent.dragStart(headers[0], {
        dataTransfer: { effectAllowed: '', setData: vi.fn() },
      });
      fireEvent.dragOver(headers[1]);
      fireEvent.drop(headers[1]);
      fireEvent.dragEnd(headers[0]);
    });
  });

  // ── Mixed Sections ──────────────────────────────────────────────

  describe('mixed local and cross-host', () => {
    it('renders both sections simultaneously', () => {
      const localLink = makeLinkState({ link_name: 'L1:eth1-L2:eth1', source_node: 'L1', target_node: 'L2' });
      const crossLink = makeLinkState({
        link_name: 'C1:eth1-C2:eth1',
        source_node: 'C1',
        target_node: 'C2',
        is_cross_host: true,
      });
      const group = makeHostGroup({ localLinks: [localLink] });
      renderLinkTable({ hostGroups: [group], crossHostLinks: [crossLink] });

      expect(screen.getByText(/Local Links/)).toBeInTheDocument();
      expect(screen.getByText(/Cross-Host Links/)).toBeInTheDocument();
      expect(screen.getByText('L1')).toBeInTheDocument();
      expect(screen.getByText('C1')).toBeInTheDocument();
    });

    it('hides empty message when at least one section has links', () => {
      const link = makeLinkState();
      const group = makeHostGroup({ localLinks: [link] });
      renderLinkTable({ hostGroups: [group] });

      expect(screen.queryByText('No links to display')).not.toBeInTheDocument();
    });
  });
});
