import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import VniLinkDetailPanel from './VniLinkDetailPanel';
import type { LinkStateData } from '../../hooks/useLabStateWS';
import type { LinkPathDetail, LinkEndpointDetail } from '../../../api';

// ── Mock API ──

const mockGetLinkDetail = vi.fn();
vi.mock('../../../api', () => ({
  getLinkDetail: (...args: unknown[]) => mockGetLinkDetail(...args),
}));

// ── Test data factories ──

function makeEndpoint(overrides?: Partial<LinkEndpointDetail>): LinkEndpointDetail {
  return {
    node_name: 'router-1',
    interface: 'eth1',
    vendor_interface: 'Ethernet1',
    ovs_port: 'ovs-port-1',
    ovs_bridge: 'arch-ovs',
    vlan_tag: 100,
    host_id: 'host-1',
    host_name: 'agent-1',
    oper_state: 'up',
    oper_reason: null,
    carrier_state: 'on',
    vxlan_attached: null,
    ...overrides,
  };
}

function makeLinkDetail(overrides?: Partial<LinkPathDetail>): LinkPathDetail {
  return {
    link_name: 'router-1:eth1--router-2:eth1',
    actual_state: 'up',
    desired_state: 'up',
    error_message: null,
    is_cross_host: false,
    source: makeEndpoint({ node_name: 'router-1' }),
    target: makeEndpoint({ node_name: 'router-2', interface: 'eth2', vendor_interface: 'Ethernet2' }),
    tunnel: null,
    ...overrides,
  };
}

function makeLinkState(overrides?: Partial<LinkStateData>): LinkStateData {
  return {
    link_name: 'router-1:eth1--router-2:eth1',
    desired_state: 'up',
    actual_state: 'up',
    source_node: 'router-1',
    target_node: 'router-2',
    ...overrides,
  };
}

const defaultProps = {
  labId: 'lab-1',
  linkState: makeLinkState(),
  onClose: vi.fn(),
};

describe('VniLinkDetailPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetLinkDetail.mockResolvedValue(makeLinkDetail());
  });

  // ── Loading state ──

  describe('Loading', () => {
    it('shows loading spinner and text while fetching', () => {
      mockGetLinkDetail.mockReturnValue(new Promise(() => {})); // never resolves
      render(<VniLinkDetailPanel {...defaultProps} />);
      expect(screen.getByText('Loading link detail...')).toBeInTheDocument();
    });

    it('calls getLinkDetail with labId and link_name', () => {
      render(<VniLinkDetailPanel {...defaultProps} />);
      expect(mockGetLinkDetail).toHaveBeenCalledWith('lab-1', 'router-1:eth1--router-2:eth1');
    });
  });

  // ── Error state ──

  describe('Error', () => {
    it('displays error message when API call fails', async () => {
      mockGetLinkDetail.mockRejectedValue(new Error('Network timeout'));
      render(<VniLinkDetailPanel {...defaultProps} />);
      await waitFor(() => {
        expect(screen.getByText('Network timeout')).toBeInTheDocument();
      });
    });

    it('displays generic error for non-Error throws', async () => {
      mockGetLinkDetail.mockRejectedValue('something broke');
      render(<VniLinkDetailPanel {...defaultProps} />);
      await waitFor(() => {
        expect(screen.getByText('Failed to load')).toBeInTheDocument();
      });
    });
  });

  // ── Header ──

  describe('Header', () => {
    it('shows "Link Detail" for same-host links', async () => {
      render(<VniLinkDetailPanel {...defaultProps} />);
      await waitFor(() => {
        expect(screen.getByText('Link Detail')).toBeInTheDocument();
      });
    });

    it('shows "Cross-Host Link Detail" for cross-host links', async () => {
      const crossHostState = makeLinkState({ is_cross_host: true });
      render(<VniLinkDetailPanel {...defaultProps} linkState={crossHostState} />);
      await waitFor(() => {
        expect(screen.getByText('Cross-Host Link Detail')).toBeInTheDocument();
      });
    });

    it('displays actual_state in the header', async () => {
      render(<VniLinkDetailPanel {...defaultProps} />);
      await waitFor(() => {
        // The header shows the actual_state text
        expect(screen.getByText('up')).toBeInTheDocument();
      });
    });

    it('shows VNI badge when vni is set', async () => {
      const withVni = makeLinkState({ vni: 10042 });
      render(<VniLinkDetailPanel {...defaultProps} linkState={withVni} />);
      await waitFor(() => {
        expect(screen.getByText('VNI 10042')).toBeInTheDocument();
      });
    });

    it('does not show VNI badge when vni is null', async () => {
      const noVni = makeLinkState({ vni: null });
      render(<VniLinkDetailPanel {...defaultProps} linkState={noVni} />);
      await waitFor(() => {
        expect(screen.queryByText(/VNI/)).not.toBeInTheDocument();
      });
    });

    it('calls onClose when close button is clicked', async () => {
      const user = userEvent.setup();
      const onClose = vi.fn();
      render(<VniLinkDetailPanel {...defaultProps} onClose={onClose} />);
      await waitFor(() => screen.getByText('Link Detail'));

      const closeBtn = screen.getByTitle('Close');
      await user.click(closeBtn);

      expect(onClose).toHaveBeenCalledOnce();
    });
  });

  // ── Endpoint cards ──

  describe('Endpoint cards', () => {
    it('renders Source and Target labels', async () => {
      render(<VniLinkDetailPanel {...defaultProps} />);
      await waitFor(() => {
        expect(screen.getByText('Source')).toBeInTheDocument();
        expect(screen.getByText('Target')).toBeInTheDocument();
      });
    });

    it('displays node names in endpoint cards', async () => {
      render(<VniLinkDetailPanel {...defaultProps} />);
      await waitFor(() => {
        expect(screen.getByText('router-1')).toBeInTheDocument();
        expect(screen.getByText('router-2')).toBeInTheDocument();
      });
    });

    it('displays host name in parentheses when set', async () => {
      render(<VniLinkDetailPanel {...defaultProps} />);
      await waitFor(() => {
        expect(screen.getAllByText('(agent-1)')).toHaveLength(2);
      });
    });

    it('does not show host name when null', async () => {
      mockGetLinkDetail.mockResolvedValue(
        makeLinkDetail({
          source: makeEndpoint({ host_name: null }),
          target: makeEndpoint({ host_name: null }),
        }),
      );
      render(<VniLinkDetailPanel {...defaultProps} />);
      await waitFor(() => {
        expect(screen.queryByText(/\(agent/)).not.toBeInTheDocument();
      });
    });

    it('shows vendor_interface as primary, raw interface in parens when different', async () => {
      render(<VniLinkDetailPanel {...defaultProps} />);
      await waitFor(() => {
        expect(screen.getByText('Ethernet1')).toBeInTheDocument();
        expect(screen.getByText('(eth1)')).toBeInTheDocument();
      });
    });

    it('falls back to raw interface when vendor_interface is null', async () => {
      mockGetLinkDetail.mockResolvedValue(
        makeLinkDetail({
          source: makeEndpoint({ vendor_interface: null }),
        }),
      );
      render(<VniLinkDetailPanel {...defaultProps} />);
      await waitFor(() => {
        expect(screen.getByText('eth1')).toBeInTheDocument();
      });
    });

    it('does not show raw interface in parens when same as vendor_interface', async () => {
      mockGetLinkDetail.mockResolvedValue(
        makeLinkDetail({
          source: makeEndpoint({ vendor_interface: 'eth1', interface: 'eth1' }),
        }),
      );
      render(<VniLinkDetailPanel {...defaultProps} />);
      await waitFor(() => {
        // Should only have one instance of eth1, not a second in parens
        const ethElements = screen.getAllByText('eth1');
        expect(ethElements).toHaveLength(1);
      });
    });

    it('displays OVS port when set', async () => {
      render(<VniLinkDetailPanel {...defaultProps} />);
      await waitFor(() => {
        expect(screen.getAllByText('ovs-port-1')).toHaveLength(2);
      });
    });

    it('hides OVS port when null', async () => {
      mockGetLinkDetail.mockResolvedValue(
        makeLinkDetail({
          source: makeEndpoint({ ovs_port: null }),
          target: makeEndpoint({ ovs_port: null }),
        }),
      );
      render(<VniLinkDetailPanel {...defaultProps} />);
      await waitFor(() => {
        expect(screen.queryByText('OVS port')).not.toBeInTheDocument();
      });
    });

    it('displays VLAN tag when set', async () => {
      render(<VniLinkDetailPanel {...defaultProps} />);
      await waitFor(() => {
        expect(screen.getAllByText('100')).toHaveLength(2);
      });
    });

    it('hides VLAN row when vlan_tag is null', async () => {
      mockGetLinkDetail.mockResolvedValue(
        makeLinkDetail({
          source: makeEndpoint({ vlan_tag: null }),
          target: makeEndpoint({ vlan_tag: null }),
        }),
      );
      render(<VniLinkDetailPanel {...defaultProps} />);
      await waitFor(() => {
        expect(screen.queryByText('VLAN')).not.toBeInTheDocument();
      });
    });

    it('displays carrier state with color dot', async () => {
      render(<VniLinkDetailPanel {...defaultProps} />);
      await waitFor(() => {
        // carrier_state is 'on'
        expect(screen.getAllByText('on')).toHaveLength(2);
      });
    });

    it('shows "-" for carrier state when null', async () => {
      mockGetLinkDetail.mockResolvedValue(
        makeLinkDetail({
          source: makeEndpoint({ carrier_state: null }),
        }),
      );
      render(<VniLinkDetailPanel {...defaultProps} />);
      await waitFor(() => {
        expect(screen.getByText('-')).toBeInTheDocument();
      });
    });

    it('displays oper_reason when present', async () => {
      mockGetLinkDetail.mockResolvedValue(
        makeLinkDetail({
          source: makeEndpoint({ oper_state: 'down', oper_reason: 'errdisable' }),
        }),
      );
      render(<VniLinkDetailPanel {...defaultProps} />);
      await waitFor(() => {
        expect(screen.getByText('(errdisable)')).toBeInTheDocument();
      });
    });

    it('shows VXLAN "attached" when vxlan_attached is true', async () => {
      mockGetLinkDetail.mockResolvedValue(
        makeLinkDetail({
          source: makeEndpoint({ vxlan_attached: true }),
        }),
      );
      render(<VniLinkDetailPanel {...defaultProps} />);
      await waitFor(() => {
        expect(screen.getByText('attached')).toBeInTheDocument();
      });
    });

    it('shows VXLAN "detached" when vxlan_attached is false', async () => {
      mockGetLinkDetail.mockResolvedValue(
        makeLinkDetail({
          source: makeEndpoint({ vxlan_attached: false }),
        }),
      );
      render(<VniLinkDetailPanel {...defaultProps} />);
      await waitFor(() => {
        expect(screen.getByText('detached')).toBeInTheDocument();
      });
    });

    it('hides VXLAN row when vxlan_attached is null', async () => {
      render(<VniLinkDetailPanel {...defaultProps} />);
      await waitFor(() => {
        expect(screen.queryByText('attached')).not.toBeInTheDocument();
        expect(screen.queryByText('detached')).not.toBeInTheDocument();
      });
    });
  });

  // ── Tunnel section ──

  describe('Tunnel section', () => {
    const tunnelDetail = makeLinkDetail({
      is_cross_host: true,
      tunnel: {
        vni: 10042,
        vlan_tag: 3001,
        agent_a_ip: '10.0.0.1',
        agent_b_ip: '10.0.0.2',
        port_name: 'vxlan-10042',
        status: 'active',
        error_message: null,
      },
    });

    it('renders VXLAN Tunnel section when tunnel is present', async () => {
      mockGetLinkDetail.mockResolvedValue(tunnelDetail);
      render(<VniLinkDetailPanel {...defaultProps} />);
      await waitFor(() => {
        expect(screen.getByText('VXLAN Tunnel')).toBeInTheDocument();
      });
    });

    it('does not render tunnel section when tunnel is null', async () => {
      render(<VniLinkDetailPanel {...defaultProps} />);
      await waitFor(() => {
        expect(screen.queryByText('VXLAN Tunnel')).not.toBeInTheDocument();
      });
    });

    it('displays tunnel VNI', async () => {
      mockGetLinkDetail.mockResolvedValue(tunnelDetail);
      render(<VniLinkDetailPanel {...defaultProps} />);
      await waitFor(() => {
        expect(screen.getByText('10042')).toBeInTheDocument();
      });
    });

    it('displays tunnel status with color', async () => {
      mockGetLinkDetail.mockResolvedValue(tunnelDetail);
      render(<VniLinkDetailPanel {...defaultProps} />);
      await waitFor(() => {
        expect(screen.getByText('active')).toBeInTheDocument();
      });
    });

    it('displays tunnel VLAN tag', async () => {
      mockGetLinkDetail.mockResolvedValue(tunnelDetail);
      render(<VniLinkDetailPanel {...defaultProps} />);
      await waitFor(() => {
        expect(screen.getByText('3001')).toBeInTheDocument();
      });
    });

    it('displays tunnel port name when set', async () => {
      mockGetLinkDetail.mockResolvedValue(tunnelDetail);
      render(<VniLinkDetailPanel {...defaultProps} />);
      await waitFor(() => {
        expect(screen.getByText('vxlan-10042')).toBeInTheDocument();
      });
    });

    it('hides port name when null', async () => {
      const noPort = makeLinkDetail({
        tunnel: { ...tunnelDetail.tunnel!, port_name: null },
      });
      mockGetLinkDetail.mockResolvedValue(noPort);
      render(<VniLinkDetailPanel {...defaultProps} />);
      await waitFor(() => {
        expect(screen.getByText('VXLAN Tunnel')).toBeInTheDocument();
        // "Port" label should not appear
        const portLabels = screen.queryAllByText('Port');
        expect(portLabels).toHaveLength(0);
      });
    });

    it('displays tunnel endpoints', async () => {
      mockGetLinkDetail.mockResolvedValue(tunnelDetail);
      render(<VniLinkDetailPanel {...defaultProps} />);
      await waitFor(() => {
        // Both IPs are in the same span, so match by content containing them
        const endpointEl = screen.getByText('Endpoints').closest('div');
        expect(endpointEl?.textContent).toContain('10.0.0.1');
        expect(endpointEl?.textContent).toContain('10.0.0.2');
      });
    });

    it('displays tunnel error message when present', async () => {
      const withError = makeLinkDetail({
        tunnel: { ...tunnelDetail.tunnel!, error_message: 'VXLAN port creation failed' },
      });
      mockGetLinkDetail.mockResolvedValue(withError);
      render(<VniLinkDetailPanel {...defaultProps} />);
      await waitFor(() => {
        expect(screen.getByText('VXLAN port creation failed')).toBeInTheDocument();
      });
    });

    it('applies correct color for failed tunnel status', async () => {
      const failed = makeLinkDetail({
        tunnel: { ...tunnelDetail.tunnel!, status: 'failed' },
      });
      mockGetLinkDetail.mockResolvedValue(failed);
      render(<VniLinkDetailPanel {...defaultProps} />);
      await waitFor(() => {
        const statusEl = screen.getByText('failed');
        expect(statusEl.className).toContain('text-red-400');
      });
    });

    it('applies correct color for pending tunnel status', async () => {
      const pending = makeLinkDetail({
        tunnel: { ...tunnelDetail.tunnel!, status: 'pending' },
      });
      mockGetLinkDetail.mockResolvedValue(pending);
      render(<VniLinkDetailPanel {...defaultProps} />);
      await waitFor(() => {
        const statusEl = screen.getByText('pending');
        expect(statusEl.className).toContain('text-amber-400');
      });
    });
  });

  // ── Link error message ──

  describe('Link error', () => {
    it('displays link-level error message when present', async () => {
      mockGetLinkDetail.mockResolvedValue(
        makeLinkDetail({ error_message: 'Link creation timed out' }),
      );
      render(<VniLinkDetailPanel {...defaultProps} />);
      await waitFor(() => {
        expect(screen.getByText('Link creation timed out')).toBeInTheDocument();
      });
    });

    it('does not show error banner when error_message is null', async () => {
      render(<VniLinkDetailPanel {...defaultProps} />);
      await waitFor(() => {
        expect(screen.queryByText('Link creation timed out')).not.toBeInTheDocument();
      });
    });
  });

  // ── Refetch behavior ──

  describe('Refetch', () => {
    it('refetches when linkState.link_name changes', async () => {
      const { rerender } = render(<VniLinkDetailPanel {...defaultProps} />);
      await waitFor(() => expect(mockGetLinkDetail).toHaveBeenCalledTimes(1));

      const newLinkState = makeLinkState({ link_name: 'router-3:eth1--router-4:eth1' });
      rerender(<VniLinkDetailPanel {...defaultProps} linkState={newLinkState} />);

      await waitFor(() => {
        expect(mockGetLinkDetail).toHaveBeenCalledWith('lab-1', 'router-3:eth1--router-4:eth1');
      });
    });

    it('refetches when labId changes', async () => {
      const { rerender } = render(<VniLinkDetailPanel {...defaultProps} />);
      await waitFor(() => expect(mockGetLinkDetail).toHaveBeenCalledTimes(1));

      rerender(<VniLinkDetailPanel {...defaultProps} labId="lab-2" />);

      await waitFor(() => {
        expect(mockGetLinkDetail).toHaveBeenCalledWith('lab-2', 'router-1:eth1--router-2:eth1');
      });
    });
  });

  // ── State colors ──

  describe('State colors', () => {
    it.each([
      ['up', 'text-green-400'],
      ['down', 'text-stone-500'],
      ['pending', 'text-amber-400'],
      ['error', 'text-red-400'],
    ] as const)('applies %s state text color correctly', async (state, expectedClass) => {
      const ls = makeLinkState({ actual_state: state as LinkStateData['actual_state'] });
      render(<VniLinkDetailPanel {...defaultProps} linkState={ls} />);
      await waitFor(() => {
        const stateEl = screen.getByText(state);
        expect(stateEl.className).toContain(expectedClass);
      });
    });

    it('uses fallback color for unknown state', async () => {
      const ls = makeLinkState({ actual_state: 'unknown' as LinkStateData['actual_state'] });
      render(<VniLinkDetailPanel {...defaultProps} linkState={ls} />);
      await waitFor(() => {
        const stateEl = screen.getByText('unknown');
        expect(stateEl.className).toContain('text-stone-500');
      });
    });
  });
});
