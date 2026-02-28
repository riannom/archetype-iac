import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import NodeListPanel from './NodeListPanel';
import { DeviceType } from '../types';
import type { Node, DeviceNode, ExternalNetworkNode, DeviceModel } from '../types';
import type { RuntimeStatus } from './RuntimeControl';

// ── Test data ──────────────────────────────────────────────────

function makeDevice(overrides: Partial<DeviceNode> & { id: string; name: string }): DeviceNode {
  return {
    nodeType: 'device',
    type: DeviceType.ROUTER,
    model: 'ceos',
    version: '4.28.0F',
    x: 0,
    y: 0,
    ...overrides,
  };
}

const router1 = makeDevice({ id: 'r1', name: 'Router-A' });
const router2 = makeDevice({ id: 'r2', name: 'Router-B' });
const switch1 = makeDevice({ id: 's1', name: 'Switch-C', type: DeviceType.SWITCH });
const vm1 = makeDevice({ id: 'vm1', name: 'VM-Router', model: 'iosv' });

const externalNode: ExternalNetworkNode = {
  id: 'ext1',
  name: 'External Net',
  nodeType: 'external',
  x: 0,
  y: 0,
};

const defaultNodes: Node[] = [router1, router2, switch1, vm1, externalNode];

const defaultModels: DeviceModel[] = [
  {
    id: 'ceos',
    type: DeviceType.ROUTER,
    name: 'Arista cEOS',
    icon: 'router',
    versions: ['4.28.0F'],
    isActive: true,
    vendor: 'arista',
    supportedImageKinds: ['docker'],
  },
  {
    id: 'iosv',
    type: DeviceType.ROUTER,
    name: 'Cisco IOSv',
    icon: 'router',
    versions: ['15.9'],
    isActive: true,
    vendor: 'cisco',
    supportedImageKinds: ['qcow2'],
  },
];

const defaultStates: Record<string, RuntimeStatus> = {
  r1: 'running',
  r2: 'booting',
  s1: 'stopped',
  vm1: 'error',
};

const defaultProps = {
  nodes: defaultNodes,
  runtimeStates: defaultStates,
  deviceModels: defaultModels,
  selectedId: null,
  onFocusNode: vi.fn(),
  onOpenConsole: vi.fn(),
  onSelectNode: vi.fn(),
};

describe('NodeListPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // ── Rendering basics ──

  describe('Rendering', () => {
    it('renders search input', () => {
      render(<NodeListPanel {...defaultProps} />);
      expect(screen.getByPlaceholderText('Search nodes...')).toBeInTheDocument();
    });

    it('renders four filter chips (Running, Booting, Stopped, Error)', () => {
      render(<NodeListPanel {...defaultProps} />);
      expect(screen.getByText('Running')).toBeInTheDocument();
      expect(screen.getByText('Booting')).toBeInTheDocument();
      expect(screen.getByText('Stopped')).toBeInTheDocument();
      expect(screen.getByText('Error')).toBeInTheDocument();
    });

    it('excludes external network nodes from the list', () => {
      render(<NodeListPanel {...defaultProps} />);
      expect(screen.queryByText('External Net')).not.toBeInTheDocument();
    });

    it('displays device nodes sorted alphabetically by name', () => {
      render(<NodeListPanel {...defaultProps} />);
      const names = screen.getAllByText(/^(Router-A|Router-B|Switch-C|VM-Router)$/).map(el => el.textContent);
      expect(names).toEqual(['Router-A', 'Router-B', 'Switch-C', 'VM-Router']);
    });

    it('shows state counts on filter chips', () => {
      render(<NodeListPanel {...defaultProps} />);
      // Running: 1 (r1), Booting: 1 (r2), Stopped: 1 (s1), Error: 1 (vm1)
      // Each filter chip displays a count — we verify via the chip button text
      const runningChip = screen.getByText('Running').closest('button');
      expect(runningChip?.textContent).toContain('1');
    });
  });

  // ── Search ──

  describe('Search', () => {
    it('filters nodes by case-insensitive name match', async () => {
      const user = userEvent.setup();
      render(<NodeListPanel {...defaultProps} />);

      await user.type(screen.getByPlaceholderText('Search nodes...'), 'router');

      expect(screen.getByText('Router-A')).toBeInTheDocument();
      expect(screen.getByText('Router-B')).toBeInTheDocument();
      expect(screen.queryByText('Switch-C')).not.toBeInTheDocument();
      expect(screen.queryByText('VM-Router')).toBeInTheDocument();
    });

    it('shows "No nodes match filter" when search has no matches', async () => {
      const user = userEvent.setup();
      render(<NodeListPanel {...defaultProps} />);

      await user.type(screen.getByPlaceholderText('Search nodes...'), 'zzzzz');

      expect(screen.getByText('No nodes match filter')).toBeInTheDocument();
    });
  });

  // ── Filtering ──

  describe('Filtering', () => {
    it('toggles filter chip on click', async () => {
      const user = userEvent.setup();
      render(<NodeListPanel {...defaultProps} />);

      await user.click(screen.getByText('Running'));

      // Only running nodes should be visible
      expect(screen.getByText('Router-A')).toBeInTheDocument();
      expect(screen.queryByText('Router-B')).not.toBeInTheDocument();
      expect(screen.queryByText('Switch-C')).not.toBeInTheDocument();
      expect(screen.queryByText('VM-Router')).not.toBeInTheDocument();
    });

    it('deactivates filter when clicked again', async () => {
      const user = userEvent.setup();
      render(<NodeListPanel {...defaultProps} />);

      await user.click(screen.getByText('Running'));
      // Only Router-A visible
      expect(screen.queryByText('Switch-C')).not.toBeInTheDocument();

      await user.click(screen.getByText('Running'));
      // All visible again
      expect(screen.getByText('Switch-C')).toBeInTheDocument();
    });

    it('supports multiple active filters (OR logic)', async () => {
      const user = userEvent.setup();
      render(<NodeListPanel {...defaultProps} />);

      await user.click(screen.getByText('Running'));
      await user.click(screen.getByText('Error'));

      expect(screen.getByText('Router-A')).toBeInTheDocument();
      expect(screen.getByText('VM-Router')).toBeInTheDocument();
      expect(screen.queryByText('Router-B')).not.toBeInTheDocument();
      expect(screen.queryByText('Switch-C')).not.toBeInTheDocument();
    });

    it('treats undeployed nodes (no state) as stopped', async () => {
      const user = userEvent.setup();
      const statesWithoutSwitch: Record<string, RuntimeStatus> = {
        r1: 'running',
        r2: 'booting',
        vm1: 'error',
        // s1 has no state entry → should count as "stopped"
      };
      render(<NodeListPanel {...defaultProps} runtimeStates={statesWithoutSwitch} />);

      await user.click(screen.getByText('Stopped'));

      expect(screen.getByText('Switch-C')).toBeInTheDocument();
    });

    it('shows "Clear filters" button when filter produces no results', async () => {
      const user = userEvent.setup();
      // All nodes are running, none are stopped
      const allRunning: Record<string, RuntimeStatus> = {
        r1: 'running',
        r2: 'running',
        s1: 'running',
        vm1: 'running',
      };
      render(<NodeListPanel {...defaultProps} runtimeStates={allRunning} />);

      await user.click(screen.getByText('Error'));

      expect(screen.getByText('No nodes match filter')).toBeInTheDocument();
      expect(screen.getByText('Clear filters')).toBeInTheDocument();
    });

    it('Clear filters button resets search and active filters', async () => {
      const user = userEvent.setup();
      const allRunning: Record<string, RuntimeStatus> = {
        r1: 'running',
        r2: 'running',
        s1: 'running',
        vm1: 'running',
      };
      render(<NodeListPanel {...defaultProps} runtimeStates={allRunning} />);

      // Type search + activate filter to get zero results
      await user.type(screen.getByPlaceholderText('Search nodes...'), 'zzz');
      await user.click(screen.getByText('Error'));

      expect(screen.getByText('No nodes match filter')).toBeInTheDocument();

      await user.click(screen.getByText('Clear filters'));

      // All nodes visible again
      expect(screen.getByText('Router-A')).toBeInTheDocument();
      expect(screen.getByText('VM-Router')).toBeInTheDocument();
    });
  });

  // ── Empty states ──

  describe('Empty states', () => {
    it('shows "No devices in topology" when there are no device nodes', () => {
      render(<NodeListPanel {...defaultProps} nodes={[externalNode]} runtimeStates={{}} />);
      expect(screen.getByText('No devices in topology')).toBeInTheDocument();
      expect(screen.getByText('Use the Library tab to add devices')).toBeInTheDocument();
    });

    it('shows "No devices in topology" when nodes array is empty', () => {
      render(<NodeListPanel {...defaultProps} nodes={[]} runtimeStates={{}} />);
      expect(screen.getByText('No devices in topology')).toBeInTheDocument();
    });
  });

  // ── Interactions ──

  describe('Node interactions', () => {
    it('calls onSelectNode and onFocusNode when clicking a node row', async () => {
      const user = userEvent.setup();
      const onSelectNode = vi.fn();
      const onFocusNode = vi.fn();
      render(<NodeListPanel {...defaultProps} onSelectNode={onSelectNode} onFocusNode={onFocusNode} />);

      await user.click(screen.getByText('Router-A'));

      expect(onSelectNode).toHaveBeenCalledWith('r1');
      expect(onFocusNode).toHaveBeenCalledWith('r1');
    });

    it('highlights selected node with active styling', () => {
      const { container } = render(<NodeListPanel {...defaultProps} selectedId="r1" />);
      const selectedRow = screen.getByText('Router-A').closest('button');
      expect(selectedRow?.className).toContain('bg-sage-600/15');
      expect(selectedRow?.className).toContain('border-sage-500');
    });

    it('does not highlight non-selected nodes', () => {
      render(<NodeListPanel {...defaultProps} selectedId="r1" />);
      const otherRow = screen.getByText('Router-B').closest('button');
      expect(otherRow?.className).not.toContain('bg-sage-600/15');
    });
  });

  // ── Console button ──

  describe('Console button', () => {
    it('renders console button for nodes that have a runtime state', () => {
      const { container } = render(<NodeListPanel {...defaultProps} />);
      // r1 has state 'running' → console button should exist
      const consoleButtons = container.querySelectorAll('button[title="Open console"]');
      // r1, r2, s1, vm1 all have states
      expect(consoleButtons.length).toBe(4);
    });

    it('does not render console button for nodes without runtime state', () => {
      const statesPartial: Record<string, RuntimeStatus> = {
        r1: 'running',
        // r2, s1, vm1 have no states
      };
      const { container } = render(<NodeListPanel {...defaultProps} runtimeStates={statesPartial} />);
      const consoleButtons = container.querySelectorAll('button[title="Open console"]');
      expect(consoleButtons.length).toBe(1);
    });

    it('calls onOpenConsole and stops propagation on console button click', async () => {
      const user = userEvent.setup();
      const onOpenConsole = vi.fn();
      const onSelectNode = vi.fn();
      const { container } = render(
        <NodeListPanel {...defaultProps} onOpenConsole={onOpenConsole} onSelectNode={onSelectNode} />
      );

      const consoleBtn = container.querySelector('button[title="Open console"]');
      if (consoleBtn) {
        await user.click(consoleBtn);
      }

      expect(onOpenConsole).toHaveBeenCalledWith('r1');
      // onSelectNode should NOT be called because stopPropagation prevents it
      expect(onSelectNode).not.toHaveBeenCalled();
    });
  });

  // ── Icons ──

  describe('Icons', () => {
    it('shows VM icon (fa-hard-drive) for qcow2 device models', () => {
      const { container } = render(<NodeListPanel {...defaultProps} />);
      // vm1 has model 'iosv' which supports qcow2
      const vmRow = screen.getByText('VM-Router').closest('button');
      const vmIcon = vmRow?.querySelector('.fa-hard-drive');
      expect(vmIcon).toBeTruthy();
    });

    it('shows container icon (fa-cube) for non-VM device models', () => {
      const { container } = render(<NodeListPanel {...defaultProps} />);
      const routerRow = screen.getByText('Router-A').closest('button');
      const containerIcon = routerRow?.querySelector('.fa-cube');
      expect(containerIcon).toBeTruthy();
    });

    it('defaults to container icon when model is not in deviceModels', () => {
      const nodeWithUnknownModel = makeDevice({ id: 'x1', name: 'Unknown', model: 'nonexistent' });
      render(<NodeListPanel {...defaultProps} nodes={[nodeWithUnknownModel]} runtimeStates={{}} />);
      const row = screen.getByText('Unknown').closest('button');
      expect(row?.querySelector('.fa-cube')).toBeTruthy();
    });
  });

  // ── State counts ──

  describe('State counts', () => {
    it('counts undeployed nodes as stopped in state counts', () => {
      // Only provide state for r1, rest are undeployed → stopped
      const partialStates: Record<string, RuntimeStatus> = { r1: 'running' };
      render(<NodeListPanel {...defaultProps} runtimeStates={partialStates} />);

      // Running=1, Stopped=3 (r2, s1, vm1 are undeployed = stopped)
      // The chip shows the count next to the label
      const stoppedChip = screen.getByText('Stopped').closest('button');
      expect(stoppedChip?.textContent).toContain('3');
    });

    it('counts all states correctly', () => {
      render(<NodeListPanel {...defaultProps} />);
      const runningChip = screen.getByText('Running').closest('button');
      const bootingChip = screen.getByText('Booting').closest('button');
      const stoppedChip = screen.getByText('Stopped').closest('button');
      const errorChip = screen.getByText('Error').closest('button');

      expect(runningChip?.textContent).toContain('1');
      expect(bootingChip?.textContent).toContain('1');
      expect(stoppedChip?.textContent).toContain('1');
      expect(errorChip?.textContent).toContain('1');
    });
  });
});
